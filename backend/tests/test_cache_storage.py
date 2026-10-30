import asyncio

import pytest

from app.adapters.base import MarketDataError
from app.market_data.cache import AsyncTTLCache
from app.market_data.validation import parse_candles
from app.schemas.candles import CandleQuery
from app.storage.candles import SQLiteCandleRepository
from test_candles import row


async def test_cache_coalesces_and_expires_without_stale_fallback():
    now = [0.0]
    cache = AsyncTTLCache(5, 2, 2, clock=lambda: now[0])
    calls = 0
    gate = asyncio.Event()

    async def load():
        nonlocal calls
        calls += 1
        await gate.wait()
        return [1]

    requests = [asyncio.create_task(cache.get("a", load)) for _ in range(5)]
    await asyncio.sleep(0)
    gate.set()
    results = await asyncio.gather(*requests)
    assert calls == 1
    results[0].value.append(2)
    assert (await cache.get("a", load)).value == [1]
    now[0] = 6

    async def fail():
        raise MarketDataError("upstream_timeout", "test")
    with pytest.raises(MarketDataError):
        await cache.get("a", fail)
    assert "a" not in cache._entries


async def test_cache_inflight_invalidation_and_capacity():
    cache = AsyncTTLCache(5, 1, 1)
    gate = asyncio.Event()

    async def load():
        await gate.wait()
        return 1
    request = asyncio.create_task(cache.get("a", load))
    await asyncio.sleep(0)
    with pytest.raises(MarketDataError, match="capacity"):
        await cache.get("b", load)
    cache.invalidate(lambda key: key == "a")
    gate.set()
    await request
    assert not cache._entries
    await cache.get("a", load)
    await cache.get("b", load)
    assert list(cache._entries) == ["b"]


async def test_cancelled_caller_does_not_cancel_shared_work():
    cache = AsyncTTLCache(5, 2, 2)
    gate = asyncio.Event()
    async def load():
        await gate.wait()
        return 42
    a = asyncio.create_task(cache.get("a", load))
    b = asyncio.create_task(cache.get("a", load))
    await asyncio.sleep(0)
    a.cancel()
    await asyncio.gather(a, return_exceptions=True)
    gate.set()
    assert (await b).value == 42
    await cache.aclose()
    assert not cache._pending


async def test_sqlite_closed_only_idempotent_persistence_and_pagination(tmp_path):
    path = tmp_path / "history.db"
    repository = SQLiteCandleRepository(path)
    await repository.initialize()
    start = row()[0]
    query = CandleQuery(symbol="TESTUSDT", timeframe="1m")
    candles = parse_candles([row(start), row(start + 60000), row(start + 120000)], query, start + 130000)
    await repository.save(query.symbol, query.timeframe, candles)
    await repository.save(query.symbol, query.timeframe, candles)
    reopened = SQLiteCandleRepository(path)
    await reopened.initialize()
    result = await reopened.read(query)
    assert len(result) == 2 and all(c.is_closed for c in result)
    latest = await reopened.read(query.model_copy(update={"limit": 1}))
    assert latest[0].open_time == start + 60000
    earliest = await reopened.read(query.model_copy(update={"start_time": start, "limit": 1}))
    assert earliest[0].open_time == start
    changed = candles[0].model_copy(update={"trades": 100})
    await reopened.save(query.symbol, query.timeframe, [changed])
    assert (await reopened.read(query))[0].trades == 100
