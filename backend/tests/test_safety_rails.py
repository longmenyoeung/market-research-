from datetime import UTC, datetime
from decimal import Decimal
import threading

import httpx
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.adapters.base import MarketDataError
from app.adapters.binance_usdm import BinanceUSDMAdapter, retry_after_seconds
from app.adapters.metrics import fetch_bulk, fetch_derivatives
from app.analysis.risk import RiskManager, TradePlan
from app.analysis.structure import analyze_structure
from app.backtesting.engine import BacktestRequest, prepare, simulate
from app.main import create_app
from app.market_data.service import MarketDataService
from app.market_data.streaming import StreamHub
from app.market_data.validation import parse_candles
from app.schemas.candles import CandleQuery, CandleResponse, DataQuality
from app.schemas.markets import Contract, DiscoveryResponse
from app.storage.candles import SQLiteCandleRepository
from test_candles import row


class RailProvider:
    def __init__(self):
        now = int(datetime.now(UTC).timestamp() * 1000)
        start = (now - 120000) // 60000 * 60000
        self.query = CandleQuery(symbol="TESTUSDT", timeframe="1m", limit=1)
        self.candles = parse_candles([row(start)], self.query, now)

    async def discover_contracts(self):
        return DiscoveryResponse(fetched_at=datetime.now(UTC), count=1,
                                 contracts=[Contract(symbol="TESTUSDT", base_asset="TEST")])

    async def fetch_candles(self, query):
        now = int(datetime.now(UTC).timestamp() * 1000)
        return CandleResponse(symbol=query.symbol, timeframe=query.timeframe, fetched_at=datetime.now(UTC),
                              server_time=now, candles=self.candles, quality=DataQuality(status="usable"))

    async def stream_candles(self, symbol, timeframe):
        if False:
            yield None

    async def aclose(self):
        pass


class SlowProvider(RailProvider):
    def __init__(self):
        super().__init__()
        self.release = threading.Event()

    async def fetch_candles(self, query):
        import asyncio
        await asyncio.to_thread(self.release.wait, 5)
        return await super().fetch_candles(query)


class FailingRepository:
    async def initialize(self):
        pass

    async def save(self, symbol, timeframe, candles):
        raise MarketDataError("storage_unavailable", "archive is read-only", 503)

    async def read(self, query):
        return []

    async def aclose(self):
        pass


def backtest_config(**updates):
    values = dict(symbol="TESTUSDT", timeframe="1h", start_time=1_700_006_400_000,
                  end_time=1_700_006_400_000 + 600 * 3600000 - 1,
                  risk_percent=0.25, fee_bps=5, slippage_bps=5)
    values.update(updates)
    return BacktestRequest(**values)


def market_frame(count=600):
    x = np.arange(count); close = 100 + x * 0.02 + 8 * np.sin(x / 9)
    return pd.DataFrame({"time": 1_700_006_400_000 + x * 3600000, "open": close - .1, "high": close + 1,
                         "low": close - 1, "close": close, "volume": 100 + 20 * np.cos(x / 11),
                         "quote_volume": (100 + 20 * np.cos(x / 11)) * close})


def test_risk_rails_default_and_hard_cap():
    assert TradePlan.model_fields["risk_percent"].default == Decimal("0.25")
    assert Decimal("0.25") == RiskManager.default_risk_percent
    with pytest.raises(ValueError):
        RiskManager.validate_risk_percent(Decimal("0.51"))
    assert RiskManager.within_daily_limit(Decimal("-9.99"), Decimal("1000"))
    assert not RiskManager.within_daily_limit(Decimal("-10"), Decimal("1000"))


def test_backtest_charges_spread_on_both_sides_once():
    data = market_frame(); config = backtest_config(spread_bps=0)
    prepared = prepare(data, config)
    free = simulate(prepared, config, 60, 600)
    spread = simulate(prepared, config.model_copy(update={"spread_bps": 40}), 60, 600)
    assert free["trades"] and spread["trades"]
    assert free["metrics"]["total_spread_cost"] == 0
    assert spread["metrics"]["total_spread_cost"] > 0
    first = spread["trades"][0]
    entry_index = int(prepared.index[prepared.time == first["entry_time"]][0])
    raw_open = float(prepared.open.iloc[entry_index])
    expected_adverse = (config.slippage_bps + 20) / 10000
    assert first["entry"] == pytest.approx(raw_open * (1 + expected_adverse))
    assert first["spread_cost"] > 0 and first["slippage_cost"] > 0
    assert spread["risk_rails"]["daily_loss_cap_percent"] == 1
    assert spread["metrics"]["blocked_by_daily_loss_cap"] >= 0


def test_live_candles_survive_archive_failure(settings):
    import asyncio
    service = MarketDataService(RailProvider(), FailingRepository(), settings)
    result = asyncio.run(service.get_candles(CandleQuery(symbol="TESTUSDT", timeframe="1m", limit=1)))
    assert len(result.candles) == 1 and result.candles[0].is_closed
    asyncio.run(service.aclose())


def test_access_block_propagates_from_optional_metrics():
    class Blocked:
        async def _read(self, *args, **kwargs):
            raise MarketDataError("access_restricted", "denied", 503)

    import asyncio
    with pytest.raises(MarketDataError, match="denied"):
        asyncio.run(fetch_bulk(Blocked()))
    with pytest.raises(MarketDataError, match="denied"):
        asyncio.run(fetch_derivatives(Blocked(), "TESTUSDT"))


def test_retry_after_is_clamped_and_reported(settings):
    assert retry_after_seconds("999999999", 60, 3600) == 3600
    assert retry_after_seconds(None, 999999, 3600) == 3600
    adapter = BinanceUSDMAdapter(settings, transport=httpx.MockTransport(
        lambda request: httpx.Response(429, headers={"Retry-After": "999999999"})))
    with TestClient(create_app(settings, provider=adapter)) as client:
        response = client.get("/api/v1/markets")
        assert response.status_code == 503
        assert response.headers["retry-after"] == str(settings.retry_after_max_seconds)


def test_health_is_permanently_manual_only(settings):
    with TestClient(create_app(settings, provider=RailProvider())) as client:
        health = client.get("/health").json()
        assert health["trading_enabled"] is False
        assert health["execution_endpoints"] == "none"
        assert "/api/v1/kill-switch" not in client.get("/openapi.json").json()["paths"]
        assert client.post("/api/v1/kill-switch", json={"enabled": True}).status_code in {404, 405}


def test_mutations_require_api_key_and_cors_preflight(settings):
    keyed = settings.model_copy(update={"api_key": "a" * 32})
    with TestClient(create_app(keyed, provider=RailProvider())) as client:
        preferences = client.get("/api/v1/settings",headers={"X-API-Key":"a"*32}).json()
        assert client.put("/api/v1/settings", json=preferences).status_code == 401
        assert client.put("/api/v1/settings", json=preferences, headers={"X-API-Key": "a" * 32}).status_code == 200
        preflight = client.options("/api/v1/settings", headers={
            "Origin": "http://127.0.0.1:5173", "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "content-type"})
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
        assert "x-api-key" in preflight.headers["access-control-allow-headers"].lower()


def test_fvg_fill_status_is_as_of_latest_bar_not_signal_time():
    count = 40; times = 1_700_000_000_000 + np.arange(count) * 3600000
    open_ = np.full(count, 100.0); high = np.full(count, 101.0)
    low = np.full(count, 99.0); close = np.full(count, 100.0)
    open_[11], high[11], low[11], close[11] = 106.0, 110.0, 105.0, 108.0
    open_[12], high[12], low[12], close[12] = 107.0, 110.0, 106.0, 109.0
    low[20], high[20], close[20] = 100.0, 104.0, 102.0
    df = pd.DataFrame({"time": times, "open": open_, "high": high, "low": low, "close": close,
                       "rsi_14": np.full(count, 50.0)})
    shape = analyze_structure(df, "1h", 3)
    fvg = next(a for a in shape["annotations"] if a["kind"] == "fvg" and a["created_index"] == 12)
    assert fvg["filled"] is False
    assert fvg["filled_as_of_latest_bar"] is True
    assert fvg["filled_at"] is not None and "as of latest bar" in fvg["label"]


def test_divergence_uses_configured_momentum_period():
    count = 30; times = 1_700_000_000_000 + np.arange(count) * 3600000
    open_ = np.full(count, 100.0); high = np.full(count, 101.0)
    low = np.full(count, 98.0); close = np.full(count, 100.0)
    low[5], low[15] = 90.0, 85.0
    rsi7 = np.full(count, 50.0); rsi14 = np.full(count, 50.0)
    rsi7[5], rsi7[15] = 40.0, 60.0
    rsi14[5], rsi14[15] = 40.0, 30.0
    df = pd.DataFrame({"time": times, "open": open_, "high": high, "low": low, "close": close,
                       "rsi_7": rsi7, "rsi_14": rsi14})
    fast = analyze_structure(df, "1h", 3, momentum_period=7)
    slow = analyze_structure(df, "1h", 3, momentum_period=14)
    assert any(a["kind"] == "divergence" for a in fast["annotations"])
    assert not any(a["kind"] == "divergence" for a in slow["annotations"])


def test_daily_loss_cap_blocks_new_entries():
    count = 13; start = 1_700_006_400_000
    signal = np.zeros(count, dtype=int); signal[[2, 5, 8, 11]] = 1
    open_ = np.full(count, 100.0); high = np.full(count, 101.0)
    low = np.full(count, 99.0); close = np.full(count, 100.0)
    for index in (4, 7, 10):
        low[index], close[index] = 98.0, 98.2
    prepared = pd.DataFrame({"time": start + np.arange(count) * 3600000, "open": open_, "high": high,
                             "low": low, "close": close, "signal": signal, "atr": np.ones(count)})
    result = simulate(prepared, backtest_config(risk_percent=0.5, fee_bps=0, slippage_bps=0,
                                                spread_bps=0, funding_bps_per_8h=0), 1, count)
    assert len(result["trades"]) == 3
    assert result["metrics"]["blocked_by_daily_loss_cap"] >= 1
    assert result["risk_rails"]["blocked_by_daily_loss_cap"] >= 1


async def test_stream_seeds_dedup_from_archive(settings):
    provider = RailProvider()
    repository = SQLiteCandleRepository(settings.database_path)
    await repository.initialize()
    await repository.save("TESTUSDT", "1m", provider.candles)
    service = MarketDataService(provider, repository, settings)
    hub = StreamHub(service, settings)
    try:
        previous = await hub._seed_previous("TESTUSDT", "1m")
        assert previous is not None and previous.stale
        assert previous.candle.open_time == provider.candles[0].open_time
    finally:
        await hub.aclose(); await service.aclose(); await repository.aclose()


def test_backtest_http_error_contracts(settings):
    now = int(datetime.now(UTC).timestamp() * 1000)
    with TestClient(create_app(settings, provider=RailProvider())) as client:
        missing = client.get("/api/v1/backtests/does-not-exist")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "not_found"
        invalid = client.post("/api/v1/backtests", json={
            "symbol": "TESTUSDT", "timeframe": "1h",
            "start_time": now - 600 * 3600000, "end_time": now + 3600000})
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "invalid_range"


def test_backtest_busy_contract(settings):
    provider = SlowProvider()
    now = int(datetime.now(UTC).timestamp() * 1000)
    body = {"symbol": "TESTUSDT", "timeframe": "1h",
            "start_time": now - 600 * 3600000, "end_time": now - 3600000}
    with TestClient(create_app(settings, provider=provider)) as client:
        assert client.post("/api/v1/backtests", json=body).status_code == 202
        busy = client.post("/api/v1/backtests", json=body)
        assert busy.status_code == 503
        assert busy.json()["error"]["code"] == "busy"
        provider.release.set()

