import asyncio
import logging
import random
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.adapters.base import MarketDataError
from app.core.config import Settings
from app.market_data.service import MarketDataService
from app.schemas.candles import CandleQuery, CandleUpdate, INTERVAL_MS

logger = logging.getLogger(__name__)


@dataclass(eq=False)
class Subscriber:
    queue: asyncio.Queue[dict]
    dropped: int = 0


@dataclass
class Channel:
    subscribers: set[Subscriber] = field(default_factory=set)
    task: asyncio.Task | None = None
    sequence: int = 0
    generation: int = 1
    last: dict | None = None


class StreamHub:
    """One upstream per symbol/timeframe; bounded queues and explicit data loss."""

    def __init__(self, service: MarketDataService, settings: Settings) -> None:
        self.service, self.settings = service, settings
        self.channels: dict[tuple[str, str], Channel] = {}
        self._connect_lock = asyncio.Lock()
        self._last_connect = 0.0

    @asynccontextmanager
    async def subscribe(self, symbol: str, timeframe: str) -> AsyncIterator[Subscriber]:
        await self.service.require_symbol(symbol)
        if sum(len(c.subscribers) for c in self.channels.values()) >= self.settings.stream_max_subscribers:
            raise MarketDataError("stream_capacity", "Live subscriber capacity reached.", 503)
        key = (symbol, timeframe)
        channel = self.channels.get(key)
        if channel is None:
            if len(self.channels) >= self.settings.stream_max_connections:
                raise MarketDataError("stream_capacity", "Live stream capacity reached.", 503)
            channel = Channel()
            self.channels[key] = channel
        subscriber = Subscriber(asyncio.Queue(maxsize=self.settings.stream_queue_size))
        channel.subscribers.add(subscriber)
        if channel.last is not None:
            subscriber.queue.put_nowait(dict(channel.last))
        if channel.task is None:
            channel.task = asyncio.create_task(self._run(key, channel))
        try:
            yield subscriber
        finally:
            channel.subscribers.discard(subscriber)
            if not channel.subscribers:
                self.channels.pop(key, None)
                if channel.task is not None:
                    channel.task.cancel()
                    await asyncio.gather(channel.task, return_exceptions=True)

    def _publish(self, channel: Channel, message: dict) -> None:
        channel.sequence += 1
        base = {**message, "sequence": channel.sequence, "resync_generation": channel.generation,
                "dropped_updates": 0}
        channel.last = base
        for subscriber in channel.subscribers:
            if subscriber.queue.full():
                subscriber.queue.get_nowait()
                subscriber.dropped += 1
            subscriber.queue.put_nowait({**base, "dropped_updates": subscriber.dropped})

    async def _pace_connection(self) -> None:
        async with self._connect_lock:
            delay = self.settings.stream_reconnect_seconds - (time.monotonic() - self._last_connect)
            if delay > 0:
                await asyncio.sleep(delay)
            self._last_connect = time.monotonic()

    async def _seed_previous(self, symbol: str, timeframe: str) -> CandleUpdate | None:
        """Seed deduplication from the archive so a reconnect cannot reopen a candle."""
        try:
            stored = await self.service.repository.read(CandleQuery(symbol=symbol, timeframe=timeframe, limit=1))
        except MarketDataError as exc:
            logger.info("Stream seed unavailable: code=%s", exc.code)
            return None
        if not stored:
            return None
        candle = stored[-1]
        return CandleUpdate(symbol=symbol, timeframe=timeframe, event_time=candle.close_time,
                            received_at=datetime.now(UTC), candle=candle, stale=True)

    async def _run(self, key: tuple[str, str], channel: Channel) -> None:
        symbol, timeframe = key
        previous: CandleUpdate | None = await self._seed_previous(symbol, timeframe)
        failures = 0
        try:
            while True:
                self._publish(channel, {"type": "status", "status": "connecting", "symbol": symbol,
                                        "timeframe": timeframe, "resync_required": True})
                source = None
                try:
                    await self.service.require_symbol(symbol)
                    await self._pace_connection()
                    source = self.service.provider.stream_candles(symbol, timeframe)
                    last_progress = time.monotonic()
                    last_discovery = last_progress
                    while True:
                        remaining = self.settings.stream_stale_seconds - (time.monotonic() - last_progress)
                        update = await asyncio.wait_for(anext(source), max(0.001, remaining))
                        if time.monotonic() - last_discovery > self.settings.discovery_ttl_seconds:
                            await self.service.require_symbol(symbol)
                            last_discovery = time.monotonic()
                        if previous is not None:
                            if update.event_time <= previous.event_time or update.candle.open_time < previous.candle.open_time:
                                continue
                            if (update.candle.open_time == previous.candle.open_time
                                    and previous.candle.is_closed and not update.candle.is_closed):
                                continue
                            if update.candle.open_time > previous.candle.open_time:
                                if (update.candle.open_time > previous.candle.open_time + INTERVAL_MS[timeframe]
                                        or not previous.candle.is_closed):
                                    channel.generation += 1
                        previous = update
                        last_progress = time.monotonic()
                        await self.service.on_update(update)
                        self._publish(channel, update.model_dump(mode="json"))
                        failures = 0
                except (MarketDataError, TimeoutError, StopAsyncIteration) as exc:
                    code = exc.code if isinstance(exc, MarketDataError) else "stream_stale" if isinstance(exc, TimeoutError) else "stream_disconnected"
                    terminal = code in {"access_restricted", "provider_banned", "rate_limited", "unknown_contract",
                                        "stream_rejected", "invalid_upstream_data"}
                    channel.generation += 1
                    self._publish(channel, {"type": "status", "status": "blocked" if terminal else "stale",
                                            "code": code, "terminal": terminal, "resync_required": True})
                    if terminal:
                        return
                    failures += 1
                finally:
                    if source is not None:
                        await source.aclose()
                delay = min(30, self.settings.stream_reconnect_seconds * 2 ** min(failures, 5))
                await asyncio.sleep(delay + random.uniform(0, delay * 0.1))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Unexpected public stream failure")
            self._publish(channel, {"type": "status", "status": "error", "code": "stream_internal_error", "terminal": True})

    async def aclose(self) -> None:
        tasks = [c.task for c in self.channels.values() if c.task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.channels.clear()
