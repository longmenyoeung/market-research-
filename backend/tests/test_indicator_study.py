import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.adapters.base import MarketDataError
from app.analysis.indicator_study import build_signals
from app.main import create_app
from app.schemas.candles import CandleQuery, INTERVAL_MS
from app.schemas.indicator_study import StudyRules, higher_context
from app.schemas.platform import IndicatorSettings
from test_research import ResearchProvider


def signal_frame():
    rows = []
    for index, side in enumerate(["BUY", "BUY", "neutral", "SELL", "SELL", "neutral", "BUY"]):
        bullish = side == "BUY"
        rows.append({"time": 1_700_006_400_000+index*3600000, "close": 100.,
                     "ema_20": 99. if bullish else 101., "ema_50": 98. if bullish else 102.,
                     "ema_200": 97. if bullish else 103., "rsi_14": 55. if bullish else 45.,
                     "macd_hist": 1. if bullish else -1., "adx": 25., "atr": 2.,
                     "relative_volume": 1.2 if side != "neutral" else 0.5})
    return pd.DataFrame(rows)


def test_buy_sell_markers_are_transitions_with_causal_positive_levels():
    rules = StudyRules(signal_spacing_bars=1)
    signals = build_signals(signal_frame(), "1h", IndicatorSettings(), rules)["signals"]
    assert [signal["side"] for signal in signals] == ["BUY", "SELL", "BUY"]
    for signal in signals:
        assert signal["known_at"] == signal["time"]+3599999
        assert all(signal["evidence"].values())
        assert abs(signal["stop"]-signal["reference"]) == 3
        if signal["side"] == "BUY":
            assert signal["stop"] < signal["reference"] < signal["targets"][0] < signal["targets"][1] < signal["targets"][2]
        else:
            assert signal["stop"] > signal["reference"] > signal["targets"][0] > signal["targets"][1] > signal["targets"][2]


def test_future_candles_do_not_change_earlier_markers_and_spacing_is_respected():
    data = signal_frame()
    rules = StudyRules(signal_spacing_bars=1)
    full = build_signals(data, "1h", IndicatorSettings(), rules)["signals"]
    prefix = build_signals(data.iloc[:5], "1h", IndicatorSettings(), rules)["signals"]
    assert prefix == [signal for signal in full if signal["time"] <= data.time.iloc[4]]
    assert len(build_signals(data, "1h", IndicatorSettings(), StudyRules(signal_spacing_bars=5))["signals"]) == 2


@pytest.mark.parametrize("field,value", [("ema_200", np.nan), ("rsi_14", 80.), ("adx", 10.), ("relative_volume", 0.)])
def test_missing_warmup_or_failed_confirmation_never_creates_marker(field, value):
    data = signal_frame().iloc[:2].copy()
    data[field] = value
    assert build_signals(data, "1h", IndicatorSettings(), StudyRules())["signals"] == []


def test_gapped_study_is_rejected():
    with pytest.raises(MarketDataError, match="continuous"):
        build_signals(signal_frame().drop(index=2), "1h", IndicatorSettings(), StudyRules())


def test_archive_study_has_indicators_drawings_and_no_current_direction(settings):
    class NoNetwork(ResearchProvider):
        async def discover_contracts(self):
            raise AssertionError("Archive study must not discover live contracts")
        async def fetch_candles(self, query):
            raise AssertionError("Archive study must not request live candles")

    app = create_app(settings, provider=NoNetwork())
    with TestClient(app) as client:
        page = client.portal.call(ResearchProvider().fetch_candles, CandleQuery(symbol="TESTUSDT", timeframe="1h", limit=400))
        client.portal.call(app.state.platform.store.save, "TESTUSDT", "1h", page.candles)
        response = client.post("/api/v1/indicator-study", json={"symbol": "TESTUSDT", "source": "archive"})
        assert response.status_code == 200, response.text
        study = response.json()
        assert study["indicator_study"]["historical"] is True
        assert study["indicator_study"]["current_signal"] == "NO TRADE"
        assert study["assessment"]["classification"] == "NO TRADE"
        assert study["market_overview"]["quality"]["status"] == "historical"
        assert len(study["indicators"]) >= 50
        assert study["series"]["ema_200"] and study["structure"]["annotations"]
        assert study["assessment"]["scenarios"] == {}
        assert client.post("/api/v1/indicator-study", json={"symbol": "EMPTYUSDT"}).status_code == 422


def test_live_study_preserves_provider_failure_instead_of_substituting_archive(settings):
    class Offline(ResearchProvider):
        async def discover_contracts(self):
            raise MarketDataError("upstream_timeout", "Synthetic provider timeout", 504)
    with TestClient(create_app(settings, provider=Offline())) as client:
        response = client.post("/api/v1/indicator-study", json={"symbol": "TESTUSDT", "source": "live"})
        assert response.status_code == 504
        assert response.json()["error"]["code"] == "upstream_timeout"


def test_live_study_with_stale_candles_has_no_current_candidate_or_trade_levels(settings):
    class Stale(ResearchProvider):
        async def fetch_candles(self, query):
            page = await super().fetch_candles(query)
            shift = 10*3600000
            return page.model_copy(update={"candles": [c.model_copy(update={
                "open_time": c.open_time-shift, "close_time": c.close_time-shift,
            }) for c in page.candles]})
    with TestClient(create_app(settings, provider=Stale())) as client:
        response = client.post("/api/v1/indicator-study", json={"symbol": "TESTUSDT", "source": "live"})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["indicator_study"]["current_signal"] == "NO TRADE"
        assert result["assessment"]["classification"] == "NO TRADE"
        assert all(scenario["entry"] is None for scenario in result["assessment"]["scenarios"].values())
        assert not any(annotation["kind"] == "plan" for annotation in result["structure"]["annotations"])


@pytest.mark.parametrize("timeframe,expected", [("15m", ["1h", "4h"]), ("30m", ["1h", "4h"]),
                                               ("1h", ["4h"]), ("4h", ["1d"])])
def test_short_term_context_is_explicit_and_strictly_higher(timeframe, expected):
    assert higher_context(timeframe) == expected
    assert all(INTERVAL_MS[context] > INTERVAL_MS[timeframe] for context in expected)


def test_live_15m_study_uses_1h_and_4h_confirmation(settings):
    with TestClient(create_app(settings, provider=ResearchProvider())) as client:
        response = client.post("/api/v1/indicator-study", json={"symbol": "TESTUSDT", "timeframe": "15m", "source": "live"})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["indicator_study"]["timeframe_plan"]["higher_timeframes"] == ["1h", "4h"]
        assert [context["timeframe"] for context in result["multi_timeframe"] if context["role"] == "higher"] == ["1h", "4h"]


def test_archive_context_does_not_use_candles_after_primary_study_time(settings):
    class NoNetwork(ResearchProvider):
        async def discover_contracts(self):
            raise AssertionError("Archive context cannot use live discovery")
        async def fetch_candles(self, query):
            raise AssertionError("Archive context cannot use live prices")
    app = create_app(settings, provider=NoNetwork())
    with TestClient(app) as client:
        fixture = ResearchProvider()
        primary = client.portal.call(fixture.fetch_candles, CandleQuery(symbol="TESTUSDT", timeframe="30m", limit=400))
        old_primary = [c.model_copy(update={"open_time": c.open_time-10*3600000, "close_time": c.close_time-10*3600000}) for c in primary.candles]
        client.portal.call(app.state.platform.store.save, "TESTUSDT", "30m", old_primary)
        for tf in ("1h", "4h"):
            later = client.portal.call(fixture.fetch_candles, CandleQuery(symbol="TESTUSDT", timeframe=tf, limit=400))
            client.portal.call(app.state.platform.store.save, "TESTUSDT", tf, later.candles)
        response = client.post("/api/v1/indicator-study", json={"symbol": "TESTUSDT", "timeframe": "30m"})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["indicator_study"]["current_signal"] == "NO TRADE"
        assert result["indicator_study"]["timeframe_plan"]["historical_markers_include_higher_context"] is False
        assert len(result["multi_timeframe"]) == 2
        for context in result["multi_timeframe"]:
            assert context["historical_available"] is True and context["usable"] is False
            assert context["as_of"] <= result["indicator_study"]["as_of"]
