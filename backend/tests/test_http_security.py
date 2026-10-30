import asyncio
import base64
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect

from app.core.config import Settings
from app.main import create_app
from app.market_data.validation import parse_update
from app.schemas.markets import Contract, DiscoveryResponse


APPLICATION_KEY = "local-research-key-123456"


class SecurityProvider:
    def __init__(self):
        self.opened = 0

    async def discover_contracts(self):
        return DiscoveryResponse(
            fetched_at=datetime.now(UTC), count=1,
            contracts=[Contract(symbol="TESTUSDT", base_asset="TEST")],
        )

    async def stream_candles(self, symbol, timeframe):
        self.opened += 1
        now = int(datetime.now(UTC).timestamp() * 1000)
        payload = {"e": "kline", "E": now, "s": symbol, "k": {
            "s": symbol, "i": timeframe, "t": now // 60000 * 60000,
            "T": now // 60000 * 60000 + 59999, "x": False,
            "o": "100", "h": "101", "l": "99", "c": "100", "v": "1",
            "q": "100", "n": 1, "V": "0.5", "Q": "50",
        }}
        yield parse_update(payload, symbol, timeframe, 15)
        await asyncio.Event().wait()

    async def aclose(self):
        return None


def keyed(settings):
    return settings.model_copy(update={"api_key": APPLICATION_KEY})


def encoded_protocol(value):
    token = base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")
    return f"mca-auth.{token}"


def receive_candle(websocket):
    for _ in range(4):
        message = websocket.receive_json()
        if message["type"] == "candle":
            return message
    raise AssertionError("No candle was received")


def test_application_key_protects_all_api_routes_but_not_health(settings):
    provider = SecurityProvider()
    with TestClient(create_app(keyed(settings), provider=provider)) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["application_key_required"] is True
        denied = client.get("/api/v1/markets")
        assert denied.status_code == 401
        assert APPLICATION_KEY not in denied.text
        allowed = client.get("/api/v1/markets", headers={"X-API-Key": APPLICATION_KEY})
        assert allowed.status_code == 200
        assert allowed.json()["contracts"][0]["symbol"] == "TESTUSDT"


def test_authentication_failures_are_bounded_and_good_key_bypasses_throttle(settings):
    with TestClient(create_app(keyed(settings), provider=SecurityProvider())) as client:
        responses = [client.get("/api/v1/settings", headers={"X-API-Key": "wrong-key"}) for _ in range(21)]
        assert all(response.status_code == 401 for response in responses[:20])
        assert responses[-1].status_code == 429
        assert responses[-1].headers["retry-after"] == "60"
        assert client.get("/api/v1/settings", headers={"X-API-Key": APPLICATION_KEY}).status_code == 200


def test_websocket_key_is_required_before_upstream_and_two_safe_transports_work(settings):
    provider = SecurityProvider()
    with TestClient(create_app(keyed(settings), provider=provider)) as client:
        with pytest.raises(WebSocketDisconnect) as caught:
            with client.websocket_connect("/api/v1/ws/candles/TESTUSDT?timeframe=1m"):
                pass
        assert caught.value.code == 1008 and provider.opened == 0

        with client.websocket_connect(
            "/api/v1/ws/candles/TESTUSDT?timeframe=1m",
            headers={"X-API-Key": APPLICATION_KEY},
        ) as websocket:
            assert receive_candle(websocket)["type"] == "candle"
            websocket.send_text("client commands are forbidden")
            with pytest.raises(WebSocketDisconnect) as closed:
                websocket.receive_json()
            assert closed.value.code == 1008

        with client.websocket_connect(
            "/api/v1/ws/candles/TESTUSDT?timeframe=1m",
            subprotocols=[encoded_protocol(APPLICATION_KEY)],
        ) as websocket:
            assert receive_candle(websocket)["type"] == "candle"
            websocket.send_text("client commands are forbidden")
            with pytest.raises(WebSocketDisconnect) as closed:
                websocket.receive_json()
            assert closed.value.code == 1008
        assert provider.opened == 2


def test_trusted_host_body_limit_and_security_headers(settings):
    limited = keyed(settings).model_copy(update={"max_request_body_bytes": 4096})
    with TestClient(create_app(limited, provider=SecurityProvider())) as client:
        assert client.get("/health", headers={"Host": "evil.example"}).status_code == 400
        oversized = client.post(
            "/api/v1/planner", content=b"x" * 4097,
            headers={"X-API-Key": APPLICATION_KEY, "Content-Type": "application/json"},
        )
        assert oversized.status_code == 413
        assert "configured limit" in oversized.json()["detail"]
        response = client.get("/health")
        assert response.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert response.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"


def test_application_key_validation_rejects_header_unsafe_characters(tmp_path):
    with pytest.raises(ValidationError, match="visible ASCII"):
        Settings(_env_file=None, database_path=tmp_path / "db.sqlite", api_key="unsafe-key-with-newline\n")
