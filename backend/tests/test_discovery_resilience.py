import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.adapters.base import MarketDataError, MarketDataProvider
from app.adapters.binance_usdm import BinanceUSDMAdapter
from app.main import create_app
from app.market_data.discovery import DiscoveryLoader
from app.market_data.service import MarketDataService
from app.schemas.markets import DiscoveryResponse
from app.storage.candles import CandleRepository


async def test_discovery_deadline_cancels_shared_work_and_cools_down(settings):
    provider = AsyncMock(spec=MarketDataProvider)
    cancelled = asyncio.Event()

    async def stalled():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    provider.discover_contracts.side_effect = stalled
    service = MarketDataService(provider, AsyncMock(spec=CandleRepository),
                                settings.model_copy(update={"discovery_timeout_seconds": 0.1}))
    try:
        results = await asyncio.wait_for(asyncio.gather(
            *(service.discover_contracts() for _ in range(4)), return_exceptions=True,
        ), timeout=2)
        assert cancelled.is_set()
        assert provider.discover_contracts.await_count == 1
        assert all(isinstance(result, MarketDataError) and result.code == "upstream_timeout"
                   and result.status_code == 504 and result.retry_after_seconds == 30 for result in results)
        with pytest.raises(MarketDataError) as caught:
            await service.discover_contracts()
        assert caught.value.code == "discovery_cooldown"
        assert caught.value.status_code == 503
        assert provider.discover_contracts.await_count == 1
    finally:
        await service.aclose()


@pytest.mark.parametrize("code,status", [("upstream_timeout", 504), ("upstream_unavailable", 502)])
async def test_connection_failure_cooldown_expires_and_allows_recovery(code, status):
    now = [100.0]
    provider = AsyncMock(spec=MarketDataProvider)
    provider.discover_contracts.side_effect = MarketDataError(code, "Synthetic connection failure", status)
    loader = DiscoveryLoader(provider, 1, 30, clock=lambda: now[0])
    with pytest.raises(MarketDataError) as first:
        await loader.load()
    assert first.value.code == code and first.value.retry_after_seconds == 30
    now[0] = 110.5
    with pytest.raises(MarketDataError) as second:
        await loader.load()
    assert second.value.code == "discovery_cooldown" and second.value.retry_after_seconds == 20
    assert provider.discover_contracts.await_count == 1

    now[0] = 130.0
    provider.discover_contracts.side_effect = None
    provider.discover_contracts.return_value = DiscoveryResponse(
        fetched_at=datetime.now(UTC), count=0, contracts=[],
    )
    assert (await loader.load()).count == 0
    assert provider.discover_contracts.await_count == 2


def test_markets_and_analysis_share_outage_and_recover_without_archive_promotion(settings, symbol):
    calls = []
    available = False

    def transport(request):
        calls.append(request.url.path)
        assert request.method == "GET" and request.url.path == "/fapi/v1/exchangeInfo"
        if not available:
            raise httpx.ConnectError("Synthetic connection failure", request=request)
        return httpx.Response(200, json={"symbols": [symbol]})

    adapter = BinanceUSDMAdapter(settings, transport=httpx.MockTransport(transport))
    app = create_app(settings, provider=adapter)
    with TestClient(app) as client:
        now = [100.0]
        app.state.market_data_service.discovery_loader.clock = lambda: now[0]
        first = client.get("/api/v1/markets")
        assert first.status_code == 502 and first.headers["retry-after"] == "30"
        second = client.post("/api/v1/analysis", json={"symbol": "TESTUSDT", "timeframe": "1h"})
        assert second.status_code == 503 and second.headers["retry-after"] == "30"
        assert second.json()["error"]["code"] == "discovery_cooldown"
        assert client.get("/api/v1/markets").status_code == 503
        assert client.get("/health").status_code == 200
        assert len(calls) == 1

        available = True
        now[0] = 130.0
        recovered = client.get("/api/v1/markets")
        assert recovered.status_code == 200
        assert recovered.json()["contracts"][0]["symbol"] == "TESTUSDT"
        assert client.get("/api/v1/markets").json()["cache_hit"] is True
        assert len(calls) == 2


def test_discovery_deadline_is_reported_with_retry_header(settings):
    async def transport(request):
        await asyncio.Event().wait()

    limited = settings.model_copy(update={"discovery_timeout_seconds": 0.1})
    adapter = BinanceUSDMAdapter(limited, transport=httpx.MockTransport(transport))
    with TestClient(create_app(limited, provider=adapter)) as client:
        response = client.get("/api/v1/markets")
        assert response.status_code == 504
        assert response.headers["retry-after"] == "30"
        assert response.json()["error"]["code"] == "upstream_timeout"
        assert "time budget" in response.json()["error"]["message"]
        assert client.get("/api/v1/markets").status_code == 503
