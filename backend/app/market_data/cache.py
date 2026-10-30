import asyncio
import copy
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass
from typing import Generic, TypeVar

from app.adapters.base import MarketDataError

T = TypeVar("T")


@dataclass
class CacheResult(Generic[T]):
    value: T
    hit: bool
    age_seconds: float


class AsyncTTLCache(Generic[T]):
    """Bounded LRU with one in-flight loader per key and no stale fallback."""

    def __init__(self, ttl: float, capacity: int, max_pending: int,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl, self.capacity, self.max_pending, self.clock = ttl, capacity, max_pending, clock
        self._entries: OrderedDict[Hashable, tuple[float, T]] = OrderedDict()
        self._pending: dict[Hashable, asyncio.Task[T]] = {}
        self._invalidated: set[asyncio.Task[T]] = set()

    async def get(self, key: Hashable, loader: Callable[[], Awaitable[T]]) -> CacheResult[T]:
        entry = self._entries.get(key)
        if entry and self.clock() - entry[0] < self.ttl:
            self._entries.move_to_end(key)
            return CacheResult(copy.deepcopy(entry[1]), True, max(0, self.clock() - entry[0]))
        self._entries.pop(key, None)
        task = self._pending.get(key)
        if task is None:
            if len(self._pending) >= self.max_pending:
                raise MarketDataError("busy", "Market-data request capacity reached; retry later.", 503, 1)
            task = asyncio.create_task(self._load(key, loader))
            self._pending[key] = task
            # Retrieve errors even if every waiting caller disconnects.
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        value = await asyncio.shield(task)
        return CacheResult(copy.deepcopy(value), False, 0)

    async def _load(self, key: Hashable, loader: Callable[[], Awaitable[T]]) -> T:
        task = asyncio.current_task()
        try:
            value = await loader()
            if task not in self._invalidated:
                self._entries[key] = (self.clock(), copy.deepcopy(value))
                self._entries.move_to_end(key)
                while len(self._entries) > self.capacity:
                    self._entries.popitem(last=False)
            return value
        finally:
            self._pending.pop(key, None)
            self._invalidated.discard(task)

    def invalidate(self, predicate: Callable[[Hashable], bool]) -> None:
        for key in list(self._entries):
            if predicate(key):
                self._entries.pop(key)
        for key, task in self._pending.items():
            if predicate(key):
                self._invalidated.add(task)

    async def aclose(self) -> None:
        tasks = list(self._pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._entries.clear()
