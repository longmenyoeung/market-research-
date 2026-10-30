from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.adapters.base import MarketDataError
from app.adapters.binance_usdm import BinanceUSDMAdapter
from app.core.safety import KLINES_URL, SERVER_TIME_URL, ReadOnlyViolation
from app.main import create_app
from app.market_data.validation import assess_quality, parse_candles, parse_update
from app.schemas.candles import CandleQuery


def row(start=1_800_000_000_000, **overrides):
    value = [start, "100", "110", "90", "105", "10", start + 59999, "1000", 5, "4", "400", "0"]
    for index, item in overrides.items():
        value[int(index)] = item
    return value


def test_closed_provisional_and_gap_reporting():
    start = row()[0]
    query = CandleQuery(symbol="TESTUSDT", timeframe="1m", limit=3)
    candles = parse_candles([row(), row(start + 120000)], query, start + 130000)
    assert candles[0].is_closed and not candles[1].is_closed
    assert str(candles[0].close) == "105"
    quality = assess_quality(candles, query, start + 130000)
    assert quality.status == "incomplete" and quality.missing_intervals == 1


@pytest.mark.parametrize("payload", [
    {}, [row(**{"1": "NaN"})], [row(**{"2": "95"})], [row(**{"5": "-1"})],
    [row(**{"4": 105.0})], [row(**{"8": True})], [row(**{"8": "5"})],
    [row(**{"9": "11"})], [row(**{"6": 1})], [row(1_800_000_000_001)],
    [row(), row()], [row(1_800_000_060_000), row()], [row()[:-1]],
])
def test_rejects_invalid_candles(payload):
    with pytest.raises(MarketDataError):
        parse_candles(payload, CandleQuery(symbol="TESTUSDT", timeframe="1m"), 1_800_000_200_000)


def test_rejects_future_and_out_of_range():
    with pytest.raises(MarketDataError):
        parse_candles([row()], CandleQuery(symbol="TESTUSDT", timeframe="1m"), row()[0] - 1)
    with pytest.raises(MarketDataError):
        parse_candles([row()], CandleQuery(symbol="TESTUSDT", timeframe="1m", start_time=row()[0] + 1), row()[0] + 90000)


def test_historical_data_is_not_mislabeled_live_stale():
    query = CandleQuery(symbol="TESTUSDT", timeframe="1m", end_time=row()[0] + 60000)
    candles = parse_candles([row()], query, row()[0] + 600000)
    assert assess_quality(candles, query, row()[0] + 600000).status == "historical"
    latest = CandleQuery(symbol="TESTUSDT", timeframe="1m")
    assert assess_quality(candles, latest, row()[0] + 600000).status == "stale"


def test_stream_payload_matches_identity_and_finality():
    now = int(datetime.now(UTC).timestamp() * 1000)
    start = now // 60000 * 60000
    payload = {"e": "kline", "E": now, "s": "TESTUSDT", "k": {
        "s": "TESTUSDT", "i": "1m", "t": start, "T": start + 59999, "x": False,
        "o": "100", "h": "110", "l": "90", "c": "105", "v": "10", "q": "1000", "n": 5,
        "V": "4", "Q": "400"}}
    update = parse_update(payload, "TESTUSDT", "1m", 15)
    assert not update.stale and not update.candle.is_closed
    with pytest.raises(MarketDataError):
        parse_update(payload, "OTHERUSDT", "1m", 15)
    payload["k"]["x"] = True
    with pytest.raises(MarketDataError):
        parse_update(payload, "TESTUSDT", "1m", 15)


def market_transport(symbol, calls):
    now = int(datetime.now(UTC).timestamp() * 1000)
    start = now // 60000 * 60000 - 60000

    def handle(request):
        calls.append(request)
        if request.url.path.endswith("exchangeInfo"):
            return httpx.Response(200, json={"symbols": [symbol]})
        if request.url.path.endswith("time"):
            return httpx.Response(200, json={"serverTime": now})
        assert request.url.path.endswith("klines")
        return httpx.Response(200, json=[row(start)])
    return httpx.MockTransport(handle)


def test_rest_caching_persistence_and_archive(settings, symbol):
    calls = []
    adapter = BinanceUSDMAdapter(settings, transport=market_transport(symbol, calls))
    with TestClient(create_app(settings, provider=adapter)) as client:
        params = {"symbol": "testusdt", "timeframe": "1m", "limit": 1}
        first = client.get("/api/v1/candles", params=params)
        assert first.status_code == 200, first.text
        assert first.json()["candles"][0]["is_closed"] is True
        assert first.json()["candles"][0]["close"] == "105"
        assert first.json()["cache_hit"] is False
        second = client.get("/api/v1/candles", params=params).json()
        assert second["cache_hit"] is True
        assert len(calls) == 3
        archived = client.get("/api/v1/history/candles", params=params).json()
        assert archived["source"] == "local_archive" and not archived["live_data"]
        assert archived["candles"] == first.json()["candles"]
        assert len(calls) == 3


@pytest.mark.parametrize("params", [
    {"symbol": "../order"}, {"symbol": "TESTUSDT", "timeframe": "2m"},
    {"symbol": "TESTUSDT", "limit": 1501}, {"symbol": "TESTUSDT", "limit": 0},
    {"symbol": "TESTUSDT", "start_time": 2, "end_time": 1},
    {"symbol": "TESTUSDT", "signature": "bad"}, {},
])
def test_api_input_validation_before_provider(settings, params):
    def forbidden(request):
        raise AssertionError("Invalid input reached network")
    adapter = BinanceUSDMAdapter(settings, transport=httpx.MockTransport(forbidden))
    with TestClient(create_app(settings, provider=adapter)) as client:
        assert client.get("/api/v1/candles", params=params).status_code == 422


def test_unknown_contract_is_not_queried(settings, symbol):
    calls = []
    adapter = BinanceUSDMAdapter(settings, transport=market_transport(symbol, calls))
    with TestClient(create_app(settings, provider=adapter)) as client:
        response = client.get("/api/v1/candles?symbol=UNKNOWNUSDT")
        assert response.status_code == 404
        assert len(calls) == 1


async def test_clock_skew_fails_before_candle_fetch(settings):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"serverTime": 1})
    adapter = BinanceUSDMAdapter(settings, transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(MarketDataError) as caught:
            await adapter.fetch_candles(CandleQuery(symbol="TESTUSDT", timeframe="1m"))
        assert caught.value.code == "clock_skew" and len(calls) == 1
    finally:
        await adapter.aclose()


async def test_candle_crossing_boundary_remains_provisional(settings, monkeypatch):
    from app.adapters import binance_usdm
    start = row()[0]
    monotonic = [100.0]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromtimestamp((start + 59990) / 1000, UTC)
    monkeypatch.setattr(binance_usdm, "datetime", Clock)
    monkeypatch.setattr(binance_usdm, "time", SimpleNamespace(monotonic=lambda: monotonic[0]))
    def handle(request):
        if request.url.path.endswith("time"):
            return httpx.Response(200, json={"serverTime": start + 59990})
        monotonic[0] += 0.1  # HTTP receive crosses the close boundary.
        return httpx.Response(200, json=[row(start)])
    adapter = BinanceUSDMAdapter(settings, transport=httpx.MockTransport(handle))
    # The adapter's clock is isolated from the event loop. Skip actual pacing
    # only in this deterministic boundary test.
    adapter._settings = settings.model_copy(update={"min_request_interval_seconds": 0})
    try:
        result = await adapter.fetch_candles(CandleQuery(symbol="TESTUSDT", timeframe="1m", limit=1))
        assert result.server_time > start + 59999
        assert not result.candles[0].is_closed
        assert result.next_start_time is None
    finally:
        await adapter.aclose()


@pytest.mark.parametrize("url", [
    KLINES_URL + "?symbol=TESTUSDT&interval=1m&limit=1&signature=test",
    KLINES_URL + "?symbol=TESTUSDT&interval=1m&limit=1&symbol=OTHER",
    KLINES_URL + "?symbol=TESTUSDT&interval=1m&limit=99999",
    KLINES_URL + "?symbol=TESTUSDT&interval=2m&limit=1",
    SERVER_TIME_URL + "?signature=test",
])
async def test_new_allowlist_rejects_unexpected_parameters(settings, url):
    def forbidden(request):
        raise AssertionError("Unsafe request reached network")
    adapter = BinanceUSDMAdapter(settings, transport=httpx.MockTransport(forbidden))
    try:
        with pytest.raises(ReadOnlyViolation):
            await adapter._client.get(url)
    finally:
        await adapter.aclose()
