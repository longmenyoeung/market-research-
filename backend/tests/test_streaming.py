import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from websockets.datastructures import Headers
from websockets.exceptions import InvalidStatus
from websockets.http11 import Response

from app.adapters import binance_stream
from app.adapters.base import MarketDataError
from app.adapters.binance_stream import NoRedirectConnect, stream_url
from app.main import create_app
from app.market_data.service import MarketDataService
from app.market_data.streaming import Channel, StreamHub, Subscriber
from app.market_data.validation import parse_update
from app.schemas.markets import Contract, DiscoveryResponse
from app.storage.candles import SQLiteCandleRepository


def event(start=None, event_time=None, closed=False):
    now = int(datetime.now(UTC).timestamp() * 1000)
    start = now // 60000 * 60000 if start is None else start
    return {"e": "kline", "E": now if event_time is None else event_time, "s": "TESTUSDT", "k": {
        "s": "TESTUSDT", "i": "1m", "t": start, "T": start + 59999, "x": closed,
        "o": "100", "h": "110", "l": "90", "c": "105", "v": "10", "q": "1000", "n": 5,
        "V": "4", "Q": "400"}}


class FakeProvider:
    def __init__(self):
        self.opened = 0
        self.closed = 0
        self.stopped = False
        self.failure = None

    async def discover_contracts(self):
        return DiscoveryResponse(fetched_at=datetime.now(UTC), count=1,
                                 contracts=[Contract(symbol="TESTUSDT", base_asset="TEST")])

    async def stream_candles(self, symbol, timeframe):
        self.opened += 1
        try:
            if self.failure is not None:
                failure, self.failure = self.failure, None
                raise failure
            yield parse_update(event(), symbol, timeframe, 15)
            await asyncio.Event().wait()
        finally:
            self.closed += 1

    async def aclose(self):
        self.stopped = True


async def next_type(subscriber, message_type):
    async with asyncio.timeout(2):
        while True:
            message = await subscriber.queue.get()
            if message["type"] == message_type:
                return message


@asynccontextmanager
async def hub_context(settings, provider=None):
    provider = provider or FakeProvider()
    repository = SQLiteCandleRepository(settings.database_path)
    await repository.initialize()
    service = MarketDataService(provider, repository, settings)
    hub = StreamHub(service, settings)
    try:
        yield hub, provider
    finally:
        await hub.aclose()
        await service.aclose()


async def test_subscribers_share_one_upstream_and_cleanup(settings):
    async with hub_context(settings) as (hub, provider):
        async with hub.subscribe("TESTUSDT", "1m") as first:
            async with hub.subscribe("TESTUSDT", "1m") as second:
                a, b = await asyncio.gather(next_type(first, "candle"), next_type(second, "candle"))
                assert a["candle"] == b["candle"]
                assert provider.opened == 1
                assert a["price_basis"] == "candle_last_trade"
            assert provider.closed == 0
        assert provider.closed == 1 and not hub.channels


async def test_disconnect_reconnect_reports_resync(settings):
    provider = FakeProvider()
    provider.failure = MarketDataError("stream_disconnected", "mock network failure")
    async with hub_context(settings, provider) as (hub, _):
        async with hub.subscribe("TESTUSDT", "1m") as subscriber:
            candle = await next_type(subscriber, "candle")
            assert provider.opened == 2
            assert candle["resync_generation"] >= 2


async def test_access_block_does_not_reconnect(settings):
    provider = FakeProvider()
    provider.failure = MarketDataError("access_restricted", "blocked", 503)
    async with hub_context(settings, provider) as (hub, _):
        async with hub.subscribe("TESTUSDT", "1m") as subscriber:
            while True:
                message = await asyncio.wait_for(subscriber.queue.get(), 1)
                if message.get("terminal"):
                    break
            assert message["code"] == "access_restricted"
            assert provider.opened == 1
            await hub.channels[("TESTUSDT", "1m")].task
            assert provider.opened == 1


async def test_silent_stream_is_marked_stale(settings):
    settings = settings.model_copy(update={"stream_stale_seconds": 0.05})
    async with hub_context(settings) as (hub, _):
        async with hub.subscribe("TESTUSDT", "1m") as subscriber:
            await next_type(subscriber, "candle")
            message = await next_type(subscriber, "status")
            assert message["status"] == "stale" and message["code"] == "stream_stale"


async def test_duplicate_updates_do_not_hide_staleness(settings):
    settings = settings.model_copy(update={"stream_stale_seconds": 0.05})
    class DuplicateProvider(FakeProvider):
        async def stream_candles(self, symbol, timeframe):
            update = parse_update(event(), symbol, timeframe, 15)
            while True:
                yield update
                await asyncio.sleep(0.005)
    async with hub_context(settings, DuplicateProvider()) as (hub, _):
        async with hub.subscribe("TESTUSDT", "1m") as subscriber:
            await next_type(subscriber, "candle")
            message = await next_type(subscriber, "status")
            assert message["code"] == "stream_stale"


async def test_stream_does_not_reopen_closed_candle_and_marks_gap(settings):
    now = int(datetime.now(UTC).timestamp() * 1000)
    start = now // 60000 * 60000 - 180000
    class OrderedProvider(FakeProvider):
        async def stream_candles(self, symbol, timeframe):
            for payload in [event(start, now - 100, True), event(start, now - 90, False),
                            event(start + 120000, now - 80, True)]:
                yield parse_update(payload, symbol, timeframe, 15)
            await asyncio.Event().wait()
    async with hub_context(settings, OrderedProvider()) as (hub, _):
        async with hub.subscribe("TESTUSDT", "1m") as subscriber:
            first = await next_type(subscriber, "candle")
            second = await next_type(subscriber, "candle")
            assert first["candle"]["is_closed"] and second["candle"]["is_closed"]
            assert second["candle"]["open_time"] == start + 120000
            assert second["resync_generation"] > first["resync_generation"]


def test_delayed_stream_update_is_explicitly_stale():
    now = int(datetime.now(UTC).timestamp() * 1000)
    start = (now - 120000) // 60000 * 60000
    update = parse_update(event(start, now - 60000, False), "TESTUSDT", "1m", 15)
    assert update.stale


async def test_backpressure_and_subscription_limits(settings):
    settings = settings.model_copy(update={"stream_max_subscribers": 1})
    async with hub_context(settings) as (hub, _):
        subscriber = Subscriber(asyncio.Queue(maxsize=1))
        channel = Channel(subscribers={subscriber})
        hub._publish(channel, {"type": "status", "status": "first"})
        hub._publish(channel, {"type": "status", "status": "second"})
        message = subscriber.queue.get_nowait()
        assert message["dropped_updates"] == 1 and message["status"] == "second"
        async with hub.subscribe("TESTUSDT", "1m"):
            with pytest.raises(MarketDataError, match="capacity"):
                async with hub.subscribe("TESTUSDT", "1m"):
                    pass


def test_websocket_route_streams_and_closes_provider(settings):
    provider = FakeProvider()
    with TestClient(create_app(settings, provider=provider)) as client:
        with client.websocket_connect("/api/v1/ws/candles/testusdt?timeframe=1m") as websocket:
            for _ in range(4):
                message = websocket.receive_json()
                if message["type"] == "candle":
                    break
            assert message["symbol"] == "TESTUSDT" and message["candle"]["close"] == "105"
        assert provider.opened == 1
    assert provider.closed == 1 and provider.stopped


def test_websocket_rejects_origin_and_client_commands(settings):
    provider = FakeProvider()
    with TestClient(create_app(settings, provider=provider)) as client:
        with pytest.raises(WebSocketDisconnect) as caught:
            with client.websocket_connect("/api/v1/ws/candles/TESTUSDT", headers={"origin": "https://untrusted.example"}):
                pass
        assert caught.value.code == 1008
        assert provider.opened == 0
        with client.websocket_connect("/api/v1/ws/candles/TESTUSDT?timeframe=1m") as websocket:
            websocket.send_json({"method": "order.place"})
            with pytest.raises(WebSocketDisconnect) as caught:
                while True:
                    websocket.receive_json()
            assert caught.value.code == 1008


@pytest.mark.parametrize("suffix", ["?timeframe=2m", "?signature=test", "?timeframe=1m&timeframe=1h"])
def test_invalid_websocket_query(settings, suffix):
    provider = FakeProvider()
    with TestClient(create_app(settings, provider=provider)) as client:
        with client.websocket_connect("/api/v1/ws/candles/TESTUSDT" + suffix) as websocket:
            assert websocket.receive_json()["code"] == "invalid_subscription"
        assert provider.opened == 0


def test_websocket_url_and_redirect_boundary():
    for timeframe in ("1m", "5m", "15m", "30m", "1h", "4h", "1d"):
        assert stream_url("btcusdt", timeframe) == f"wss://fstream.binance.com/market/ws/btcusdt@kline_{timeframe}"
    with pytest.raises(ValueError):
        stream_url("../order", "1m")
    failure = RuntimeError("redirect")
    assert NoRedirectConnect.process_redirect(None, failure) is failure


async def test_connector_uses_only_receive_and_no_proxy(settings, monkeypatch):
    class ReadOnlyConnection:
        def __aiter__(self):
            return self
        async def __anext__(self):
            import json
            return json.dumps(event())
    @asynccontextmanager
    async def connector(url, **kwargs):
        assert url == stream_url("TESTUSDT", "1m")
        assert kwargs["proxy"] is None and kwargs["max_size"] == 65536
        yield ReadOnlyConnection()  # No send() method exists.
    monkeypatch.setattr(binance_stream, "NoRedirectConnect", connector)
    source = binance_stream.read_stream("TESTUSDT", "1m", settings, lambda *args: None)
    assert (await anext(source)).symbol == "TESTUSDT"
    await source.aclose()


async def test_handshake_access_error_is_reported_to_shared_guard(settings, monkeypatch):
    @asynccontextmanager
    async def connector(*args, **kwargs):
        raise InvalidStatus(Response(429, "limited", Headers({"Retry-After": "120"})))
        yield  # Marks an async context manager generator.
    monkeypatch.setattr(binance_stream, "NoRedirectConnect", connector)
    def check(status, retry):
        assert status == 429 and retry == "120"
        raise MarketDataError("rate_limited", "cooldown", 503, 120)
    source = binance_stream.read_stream("TESTUSDT", "1m", settings, check)
    with pytest.raises(MarketDataError) as caught:
        await anext(source)
    assert caught.value.code == "rate_limited"
