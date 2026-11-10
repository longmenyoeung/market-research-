import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.adapters.binance_usdm import BinanceUSDMAdapter
from app.core.config import Settings
from app.core.safety import EXCHANGE_INFO_URL, ReadOnlyViolation
from app.main import create_app


@pytest.mark.parametrize("method,url,kwargs", [
    ("POST", EXCHANGE_INFO_URL, {}), ("PUT", EXCHANGE_INFO_URL, {}),
    ("DELETE", EXCHANGE_INFO_URL, {}), ("PATCH", EXCHANGE_INFO_URL, {}),
    ("GET", "https://fapi.binance.com/fapi/v1/order", {}),
    ("POST", "https://fapi.binance.com/fapi/v1/order/test", {}),
    ("DELETE", "https://fapi.binance.com/fapi/v1/allOpenOrders", {}),
    ("GET", "https://fapi.binance.com/fapi/v2/account", {}),
    ("GET", "https://example.com/fapi/v1/exchangeInfo", {}),
    ("GET", EXCHANGE_INFO_URL + "?signature=test", {}),
    ("GET", EXCHANGE_INFO_URL, {"headers": {"X-MBX-APIKEY": "test"}}),
    ("GET", EXCHANGE_INFO_URL, {"headers": {"Authorization": "test"}}),
    ("GET", EXCHANGE_INFO_URL, {"headers": {"Cookie": "test"}}),
    ("GET", EXCHANGE_INFO_URL, {"content": b"payload"}),
])
async def test_outbound_guard_blocks_before_network(settings, method, url, kwargs):
    def forbidden(request):
        raise AssertionError("Unsafe request reached transport")

    adapter = BinanceUSDMAdapter(settings, transport=httpx.MockTransport(forbidden))
    try:
        with pytest.raises(ReadOnlyViolation):
            await adapter._client.request(method, url, **kwargs)
    finally:
        await adapter.aclose()


def test_exact_api_surface_is_read_only(settings):
    app = create_app(settings)
    paths = app.openapi()["paths"]
    expected = {"/health": {"get"}, "/api/v1/markets": {"get"}, "/api/v1/candles": {"get"},
        "/api/v1/history/candles": {"get"}, "/api/v1/settings": {"get", "put"},
        "/api/v1/watchlist": {"get", "put"}, "/api/v1/scanner": {"get"},
        "/api/v1/scanner/refresh": {"post"}, "/api/v1/analysis": {"post"},
        "/api/v1/indicator-study": {"post"},
        "/api/v1/news/scan": {"get", "post"},
        "/api/v1/history/reports": {"get"}, "/api/v1/notifications": {"get"},
        "/api/v1/planner": {"post"}, "/api/v1/backtests": {"get", "post"},
        "/api/v1/backtests/{run_id}": {"get"}, "/api/v1/backtests/{run_id}/cancel": {"post"}}
    assert {path: set(operations) for path,operations in paths.items()} == expected
    def expanded_routes(routes):
        # Newer FastAPI lazily expands included routers. Inspect their effective
        # routes too, including endpoints hidden from OpenAPI.
        for route in routes:
            if hasattr(route, "effective_candidates"):
                yield from expanded_routes(route.effective_candidates())
                yield from expanded_routes(route.effective_low_priority_routes())
            else:
                yield getattr(route, "starlette_route", None) or route

    routes = list(expanded_routes(app.routes))
    actual = {route.path for route in routes}
    assert actual - {""} == set(expected) | {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc", "/api/v1/ws/candles/{symbol}"}
    # Local settings, simulated research and planning may POST/PUT; they never
    # relay writes. Exchange transport tests continue to reject every mutation.
    assert all(not any(word in path.lower() for word in ("order", "position", "execution")) for path in actual)
    with TestClient(app) as client:
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            assert client.request(method, "/api/v1/markets").status_code == 405
        assert client.post("/api/v1/orders").status_code in {404,405}


@pytest.mark.parametrize("values", [
    {"api_key": "too-short"}, {"retry_after_max_seconds": 10},
    {"websocket_origins": ["*"]}, {"websocket_origins": []},
    {"base_url": "https://example.com"}, {"max_retries": 99},
    {"request_timeout_seconds": 0}, {"min_request_interval_seconds": 0},
])
def test_config_rejects_unsafe_or_invalid_settings(values):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


def test_execution_cannot_be_enabled(settings, monkeypatch):
    assert settings.trading_enabled is False and settings.api_key is None
    with pytest.raises(ValidationError):
        Settings(_env_file=None, trading_enabled=True)
    monkeypatch.setenv("MCA_TRADING_ENABLED", "true")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
    monkeypatch.setenv("MCA_TRADING_ENABLED", "false")
    assert Settings(_env_file=None).trading_enabled is False
