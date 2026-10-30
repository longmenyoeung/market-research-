import asyncio
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from app.adapters.base import MarketDataError, MarketDataProvider
from app.adapters.binance_usdm import BinanceUSDMAdapter, retry_after_seconds
from app.core.safety import EXCHANGE_INFO_URL


async def test_adapter_conforms_to_market_data_provider_protocol(settings):
    """A concrete adapter must expose the whole read-only analysis surface."""
    adapter = BinanceUSDMAdapter(settings)
    try:
        assert isinstance(adapter, MarketDataProvider)
        assert not hasattr(adapter, "create_order")
        assert not hasattr(adapter, "cancel_order")
    finally:
        await adapter.aclose()


async def test_success_uses_only_public_request(settings, symbol):
    def handle(request):
        assert str(request.url) == EXCHANGE_INFO_URL
        assert request.method == "GET"
        assert not request.url.query
        assert not request.content
        assert "x-mbx-apikey" not in request.headers
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"symbols": [symbol], "serverTime": 1})

    adapter = BinanceUSDMAdapter(settings, transport=httpx.MockTransport(handle))
    try:
        result = await adapter.discover_contracts()
        assert result.count == 1
        assert result.fetched_at.tzinfo == UTC
        assert result.fetched_at.year > 2020  # exchangeInfo.serverTime is deliberately ignored.
    finally:
        await adapter.aclose()
    assert adapter._client.is_closed


@pytest.mark.parametrize("status,code,http_status", [
    (400, "upstream_rejected", 502), (401, "upstream_rejected", 502),
    (403, "access_restricted", 503), (451, "access_restricted", 503),
    (418, "provider_banned", 503), (429, "rate_limited", 503),
    (500, "upstream_unavailable", 502), (503, "upstream_unavailable", 502),
    (408, "upstream_unavailable", 502), (302, "upstream_rejected", 502),
])
async def test_http_errors(settings, status, code, http_status):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, text="PRIVATE UPSTREAM DETAIL", headers={"Location": "https://example.com"})

    adapter = BinanceUSDMAdapter(settings, transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(MarketDataError) as caught:
            await adapter.discover_contracts()
        assert caught.value.code == code
        assert caught.value.status_code == http_status
        assert "PRIVATE" not in str(caught.value)
        assert len(calls) == 1
        if status in (403, 451, 418, 429):
            with pytest.raises(MarketDataError) as again:
                await adapter.discover_contracts()
            assert again.value.code == code
            assert len(calls) == 1  # No retry or second network request during block.
    finally:
        await adapter.aclose()


@pytest.mark.parametrize("exception,code,status", [
    (httpx.ReadTimeout, "upstream_timeout", 504),
    (httpx.ConnectError, "upstream_unavailable", 502),
])
async def test_network_failures(settings, exception, code, status):
    def handle(request):
        raise exception("private network information", request=request)

    adapter = BinanceUSDMAdapter(settings, transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(MarketDataError) as caught:
            await adapter.discover_contracts()
        assert (caught.value.code, caught.value.status_code) == (code, status)
    finally:
        await adapter.aclose()


async def test_retry_is_bounded_and_recovers(settings, symbol):
    attempts = 0

    def handle(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(503) if attempts < 3 else httpx.Response(200, json={"symbols": [symbol]})

    adapter = BinanceUSDMAdapter(settings.model_copy(update={"max_retries": 2}), transport=httpx.MockTransport(handle))
    try:
        assert (await adapter.discover_contracts()).count == 1
        assert attempts == 3
    finally:
        await adapter.aclose()


async def test_exhausted_retries(settings):
    attempts = 0

    def handle(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(500)

    adapter = BinanceUSDMAdapter(settings.model_copy(update={"max_retries": 2}), transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(MarketDataError):
            await adapter.discover_contracts()
        assert attempts == 3
    finally:
        await adapter.aclose()


async def test_invalid_json(settings):
    adapter = BinanceUSDMAdapter(settings, transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>")))
    try:
        with pytest.raises(MarketDataError) as caught:
            await adapter.discover_contracts()
        assert caught.value.code == "invalid_upstream_data"
    finally:
        await adapter.aclose()


@pytest.mark.parametrize("value,expected", [(None, 60), ("garbage", 60), ("NaN", 60),
                                           ("inf", 60), ("-1", 60), ("1", 60), ("120", 120)])
def test_retry_after(value, expected):
    assert retry_after_seconds(value, 60) == expected


def test_retry_after_http_date():
    value = format_datetime(datetime.now(UTC) + timedelta(seconds=120))
    assert 119 <= retry_after_seconds(value, 60) <= 120


async def test_concurrent_callers_respect_cooldown(settings):
    attempts = 0

    def handle(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(429, headers={"Retry-After": "120"})

    adapter = BinanceUSDMAdapter(settings, transport=httpx.MockTransport(handle))
    try:
        results = await asyncio.gather(*(adapter.discover_contracts() for _ in range(5)), return_exceptions=True)
        assert attempts == 1
        assert all(isinstance(result, MarketDataError) for result in results)
        assert all(119 <= result.retry_after_seconds <= 120 for result in results)
    finally:
        await adapter.aclose()
