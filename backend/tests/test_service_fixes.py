from fastapi.testclient import TestClient

from app.adapters.base import MarketDataError
from app.main import create_app
from app.schemas.candles import INTERVAL_MS
from test_research import ResearchProvider


class CurrentProvider(ResearchProvider):
    """Last bar is provisional and covers the current period, so quality is usable."""
    def __init__(self, caps=None):
        super().__init__(); self.caps = caps or {}
    async def fetch_candles(self, query):
        page = await super().fetch_candles(query)
        duration = INTERVAL_MS[query.timeframe]
        candles = [c.model_copy(update={"open_time": c.open_time+duration, "close_time": c.close_time+duration,
                                        "is_closed": i < len(page.candles)-1}) for i, c in enumerate(page.candles)]
        cap = self.caps.get(query.timeframe)
        return page.model_copy(update={"candles": candles[-cap:] if cap else candles})


def test_quote_refresh_failure_keeps_existing_warnings_without_duplicates(settings):
    app = create_app(settings, provider=ResearchProvider())
    with TestClient(app) as client:
        platform = app.state.platform
        async def fail(): raise MarketDataError("upstream_timeout", "Synthetic timeout", 504)
        platform.metrics = fail
        platform.scan = {"status": "complete", "timeframe": "1h", "rows": [
            {"symbol": "TESTUSDT", "quality": "usable", "classification": "LONG candidate", "warnings": ["Low liquidity"]},
            {"symbol": "LOWUSDT", "quality": "pending", "warnings": ["Analysis pending; no candidate classification."]}]}
        client.portal.call(platform._refresh_scan_quotes)
        row = platform.scan["rows"][0]
        assert row["classification"] == "NO TRADE" and row["quality"] == "unavailable"
        assert row["warnings"] == ["Low liquidity", "Current quote refresh failed: upstream_timeout"]
        row["quality"] = "stale"
        client.portal.call(platform._refresh_scan_quotes)
        assert row["warnings"] == ["Low liquidity", "Current quote refresh failed: upstream_timeout"]
        assert platform.scan["rows"][1]["warnings"] == ["Analysis pending; no candidate classification."]


def test_live_men_crypto_short_higher_sample_is_explained(settings):
    app = create_app(settings, provider=CurrentProvider({"4h": 150}))
    with TestClient(app) as client:
        response = client.post("/api/v1/indicator-study", json={"symbol": "TESTUSDT", "source": "live",
                                                                "profile": "men_crypto", "higher_timeframes": ["4h"]})
        assert response.status_code == 200, response.text
        result = response.json()
        context = result["multi_timeframe"][0]
        assert context["usable"] is False
        assert context["error"].startswith("Insufficient 4h context sample:")
        assert context["error"].endswith(f"{result['parameters']['indicators']['trend_anchor']} are required.")
        study = result["indicator_study"]
        assert study["current_signal"] == "NO TRADE"
        assert "Men Crypto confirmed higher-timeframe context is not ready" in study["current_reason"]
        assert "Insufficient 4h context sample" in study["current_reason"]
        assert all(s["entry"] is None for s in result["assessment"]["scenarios"].values())


def test_live_men_crypto_higher_context_gap_is_explained(settings):
    class GapProvider(CurrentProvider):
        async def fetch_candles(self, query):
            page = await super().fetch_candles(query)
            if query.timeframe != "4h":
                return page
            candles = list(page.candles); del candles[len(candles)//2]
            return page.model_copy(update={"candles": candles})

    gap_reason = "4h context candles contain gaps at this study time; a continuous window is required."
    app = create_app(settings, provider=GapProvider())
    with TestClient(app) as client:
        response = client.post("/api/v1/indicator-study", json={"symbol": "TESTUSDT", "source": "live",
                                                                "profile": "men_crypto", "higher_timeframes": ["4h"]})
        assert response.status_code == 200, response.text
        result = response.json()
        context = result["multi_timeframe"][0]
        assert context["usable"] is False
        assert context["error"] == gap_reason
        study = result["indicator_study"]
        assert study["current_signal"] == "NO TRADE"
        assert result["assessment"]["classification"] == "NO TRADE"
        assert gap_reason in study["current_reason"]
        for scenario in result["assessment"]["scenarios"].values():
            assert scenario["entry"] is None and scenario["entry_region"] is None and scenario["stop"] is None
            assert scenario["targets"] == []
        assert not any(a["kind"] == "plan" for a in result["structure"]["annotations"])
