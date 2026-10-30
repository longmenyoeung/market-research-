from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
import pytest

from app.core.config import Settings
from app.schemas.platform import ScanFilter
from app.services.platform import PlatformService


class RecordingStore:
    def __init__(self) -> None:
        self.writes: list[dict] = []

    async def put(self, table, record_id, data, symbol=None, timeframe=None) -> None:
        self.writes.append({"table": table, "id": record_id, "data": deepcopy(data),
                            "symbol": symbol, "timeframe": timeframe})


def service_for(store: RecordingStore, tmp_path) -> PlatformService:
    settings = Settings(_env_file=None, database_path=tmp_path / "notifications.db")
    # These tests isolate notification transitions; only the event-registration
    # surface of the market service is needed.
    return PlatformService(SimpleNamespace(closed_candle_listeners=[]), store, settings)


async def test_dashboard_notifications_fire_only_on_state_transitions(tmp_path):
    store = RecordingStore()
    service = service_for(store, tmp_path)
    key = ("TESTUSDT", "1h")
    previous = {"symbol": "TESTUSDT", "timeframe": "1h", "classification": "NO TRADE",
                "quality": "usable", "liquidity": "normal", "volatility_status": "normal",
                "funding_rate": 0.0, "setups": []}
    row = {**previous, "classification": "LONG candidate", "liquidity": "warning",
           "volatility_status": "high", "funding_rate": 0.002, "setups": ["retest"]}
    service.previous_rows[key] = previous

    await service._notify_changes(row)
    await service._notify_changes(row)

    messages = [write["data"]["message"] for write in store.writes]
    assert store.writes and all(write["table"] == "dashboard_notifications" for write in store.writes)
    assert sum("Classification:" in message for message in messages) == 1
    assert "Liquidity deteriorated." in messages
    assert "Volatility threshold crossed." in messages
    assert "Funding threshold crossed." in messages
    assert "An observed retest confirmation is present; manual review required." in messages
    assert sum(message.startswith("Classification:") for message in messages) == 1


async def test_candidate_invalidation_is_disclosed(tmp_path):
    store = RecordingStore()
    service = service_for(store, tmp_path)
    previous = {"symbol": "TESTUSDT", "timeframe": "1h", "classification": "SHORT candidate",
                "quality": "usable", "liquidity": "normal", "volatility_status": "normal",
                "funding_rate": 0.0, "setups": []}
    service.previous_rows[("TESTUSDT", "1h")] = previous

    await service._notify_changes({**previous, "classification": "NO TRADE"})

    messages = [write["data"]["message"] for write in store.writes]
    assert "Previous candidate invalidated or no longer meets its confirmation/data requirements." in messages


async def test_provider_failure_downgrades_candidate_without_crashing_notifications(tmp_path):
    store = RecordingStore()
    service = service_for(store, tmp_path)
    service.previous_rows[("TESTUSDT", "1h")] = {
        "symbol": "TESTUSDT", "timeframe": "1h", "classification": "LONG candidate", "quality": "usable",
        "liquidity": "normal", "volatility_status": "normal", "funding_rate": 0.0, "setups": []}

    await service._notify_changes({
        "symbol": "TESTUSDT", "timeframe": "1h", "classification": "NO TRADE", "quality": "unavailable",
        "warnings": ["Optional metric failed."], "analyzed_at": datetime.now(UTC).isoformat()})

    messages = [write["data"]["message"] for write in store.writes]
    assert any(message.startswith("Classification: LONG candidate") for message in messages)
    assert any("invalidated" in message for message in messages)
    assert service.previous_rows[("TESTUSDT", "1h")]["quality"] == "unavailable"


@pytest.mark.parametrize('source_expired', [False, True])
async def test_stale_scan_is_downgraded_and_notified_once(tmp_path, source_expired):
    store = RecordingStore()
    service = service_for(store, tmp_path)
    row = {"symbol": "TESTUSDT", "timeframe": "1h", "analyzed_at": datetime(2020, 1, 1, tzinfo=UTC).isoformat(),
           "classification": "LONG candidate", "quality": "usable", "liquidity": "normal",
           "volatility_status": "normal", "funding_rate": 0.0, "setups": [], "warnings": []}
    if source_expired:
        row.update(analyzed_at=datetime.now(UTC).isoformat(), valid_until_ms=int(datetime.now(UTC).timestamp()*1000)-1)
    service.scan = {"status": "complete", "timeframe": "1h", "rows": [deepcopy(row)],
                    "processed": 1, "total": 1, "error": None}
    service.previous_rows[("TESTUSDT", "1h")] = deepcopy(row)

    first = await service.scanner(ScanFilter())
    second = await service.scanner(ScanFilter())

    for result in (first, second):
        assert result["rows"][0]["quality"] == "stale"
        assert result["rows"][0]["classification"] == "NO TRADE"
        assert result["rows"][0]["age_seconds"] >= 0 if source_expired else result["rows"][0]["age_seconds"] > 120
    stale_writes = [write for write in store.writes if write["data"]["message"] == "Analysis data became stale."]
    assert len(stale_writes) == 1
