import httpx
from fastapi.testclient import TestClient

from app.adapters.binance_usdm import BinanceUSDMAdapter
from app.main import create_app


def test_health_does_not_contact_exchange(settings):
    def forbidden(request):
        raise AssertionError("Health must not contact an exchange")

    adapter = BinanceUSDMAdapter(settings, transport=httpx.MockTransport(forbidden))
    with TestClient(create_app(settings, provider=adapter)) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["mode"] == "analysis_only"
        assert response.json()["exchange_connectivity"] == "not_checked"
        assert response.headers["cache-control"] == "no-store"
    assert adapter._client.is_closed


def test_markets_contract(settings, symbol):
    adapter = BinanceUSDMAdapter(settings, transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"symbols": [symbol]})))
    with TestClient(create_app(settings, provider=adapter)) as client:
        response = client.get("/api/v1/markets")
        assert response.status_code == 200
        result = response.json()
        assert result["count"] == len(result["contracts"]) == 1
        assert result["contracts"][0]["symbol"] == "TESTUSDT"
        assert "has not been verified" in result["eligibility_notice"]
        assert result["fetched_at"].endswith("Z")


def test_rate_limit_has_safe_error_and_retry_header(settings):
    adapter = BinanceUSDMAdapter(settings, transport=httpx.MockTransport(
        lambda r: httpx.Response(429, text="SECRET", headers={"Retry-After": "120"})))
    with TestClient(create_app(settings, provider=adapter)) as client:
        response = client.get("/api/v1/markets")
        assert response.status_code == 503
        assert response.headers["retry-after"] == "120"
        assert response.json()["error"]["code"] == "rate_limited"
        assert "SECRET" not in response.text


def test_invalid_metadata_fails_api(settings):
    adapter = BinanceUSDMAdapter(settings, transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"symbols": [{}]})))
    with TestClient(create_app(settings, provider=adapter)) as client:
        response = client.get("/api/v1/markets")
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "invalid_upstream_data"
