from datetime import UTC, datetime
from collections.abc import Callable
from collections import OrderedDict
import logging

from app.adapters.base import MarketDataError, MarketDataProvider
from app.core.config import Settings
from app.market_data.cache import AsyncTTLCache
from app.market_data.discovery import DiscoveryLoader
from app.market_data.validation import assess_quality
from app.schemas.candles import CandleQuery, CandleResponse, CandleUpdate, StoredCandles, INTERVAL_MS
from app.schemas.markets import DiscoveryResponse
from app.storage.candles import CandleRepository

logger = logging.getLogger(__name__)


class MarketDataService:
    def __init__(self, provider: MarketDataProvider, repository: CandleRepository, settings: Settings) -> None:
        self.provider, self.repository = provider, repository
        self.closed_candle_listeners: list[Callable[[str, str], None]] = []
        self.analysis_windows: OrderedDict[tuple, CandleResponse] = OrderedDict()
        self.analysis_dirty: set[tuple[str,str]] = set()
        self.analysis_window_capacity = settings.calculation_cache_entries
        self.discovery = AsyncTTLCache[DiscoveryResponse](settings.discovery_ttl_seconds, 1, 1)
        self.discovery_loader = DiscoveryLoader(provider, settings.discovery_timeout_seconds,
                                                settings.discovery_failure_cooldown_seconds)
        self.candles = AsyncTTLCache[CandleResponse](settings.candle_ttl_seconds,
                                                   settings.cache_max_entries, settings.max_pending_requests)

    async def discover_contracts(self) -> DiscoveryResponse:
        result = await self.discovery.get("universe", self.discovery_loader.load)
        return result.value.model_copy(update={"cache_hit": result.hit, "cache_age_seconds": result.age_seconds})

    async def require_symbol(self, symbol: str) -> None:
        universe = await self.discover_contracts()
        if not any(contract.symbol == symbol for contract in universe.contracts):
            raise MarketDataError("unknown_contract", "Symbol is not an active USDT-margined perpetual contract.", 404)

    async def get_candles(self, query: CandleQuery) -> CandleResponse:
        await self.require_symbol(query.symbol)

        async def load() -> CandleResponse:
            result = await self.provider.fetch_candles(query)
            # The archive is an optional durable copy. A local write failure must
            # not turn a successful live read into an outage.
            try:
                await self.repository.save(query.symbol, query.timeframe, result.candles)
            except MarketDataError as exc:
                logger.warning("Candle archive write skipped: code=%s", exc.code)
            return result

        key = (query.symbol, query.timeframe, query.limit, query.start_time, query.end_time)
        cached = await self.candles.get(key, load)
        result = cached.value
        # Never promote a cached provisional candle to final by passage of time.
        age_ms = max(0, int((datetime.now(UTC) - result.fetched_at).total_seconds() * 1000))
        quality = assess_quality(result.candles, query, result.server_time + age_ms)
        return result.model_copy(update={"cache_hit": cached.hit, "cache_age_seconds": cached.age_seconds,
                                         "quality": quality})

    async def analysis_candles(self, query: CandleQuery) -> CandleResponse:
        """Reuse a closed history; fetch only recent bars at the next close.

        Reception times are retained. Old provisional candles are never promoted
        by the clock; only an explicit confirmed REST replacement can do that.
        Extended outages and gaps trigger a complete bounded reload.
        """
        if query.start_time is not None or query.end_time is not None:
            return await self.get_candles(query)
        await self.require_symbol(query.symbol)
        key=(query.symbol,query.timeframe,query.limit)
        prior=self.analysis_windows.get(key)
        duration=INTERVAL_MS[query.timeframe]
        now=int(datetime.now(UTC).timestamp()*1000)
        expected=(now//duration-1)*duration
        closed=[c for c in prior.candles if c.is_closed] if prior else []
        latest=closed[-1].open_time if closed else -1
        dirty=(query.symbol,query.timeframe) in self.analysis_dirty
        if prior and latest>=expected and not dirty and prior.quality.missing_intervals==0:
            age=max(0,(datetime.now(UTC)-prior.fetched_at).total_seconds())
            quality=assess_quality(prior.candles,query,prior.server_time+int(age*1000))
            self.analysis_windows.move_to_end(key)
            return prior.model_copy(deep=True,update={'quality':quality,'cache_hit':True,'cache_age_seconds':age})
        if prior and closed and len(prior.candles)>=query.limit-2 and expected-latest<=2*duration and prior.quality.missing_intervals==0:
            fresh=await self.get_candles(query.model_copy(update={'limit':3}))
            merged={c.open_time:c for c in prior.candles}
            for candle in fresh.candles:
                # A confirmed prior bar cannot be reopened by a later page.
                existing=merged.get(candle.open_time)
                if existing is None or not existing.is_closed or candle.is_closed:
                    merged[candle.open_time]=candle
            candles=sorted(merged.values(),key=lambda c:c.open_time)[-query.limit:]
            quality=assess_quality(candles,query,fresh.server_time)
            result=fresh.model_copy(update={'candles':candles,'quality':quality,'next_start_time':None})
            if quality.missing_intervals:
                result=await self.get_candles(query)
        else:
            result=await self.get_candles(query)
        self.analysis_dirty.discard((query.symbol,query.timeframe))
        self.analysis_windows[key]=result.model_copy(deep=True)
        self.analysis_windows.move_to_end(key)
        while len(self.analysis_windows)>self.analysis_window_capacity:
            self.analysis_windows.popitem(last=False)
        return result

    async def archive(self, query: CandleQuery) -> StoredCandles:
        candles = await self.repository.read(query)
        quality = assess_quality(candles, query, int(datetime.now(UTC).timestamp() * 1000))
        quality.status = "historical" if candles and not quality.missing_intervals else "incomplete" if candles else "empty"
        quality.warnings.append("Local archive only; coverage may be partial and this is not live data.")
        return StoredCandles(symbol=query.symbol, timeframe=query.timeframe, candles=candles, quality=quality)

    async def on_update(self, update: CandleUpdate) -> None:
        # REST final candles are authoritative archive input. Streaming updates
        # invalidate only the selected symbol/timeframe; no universe recalculation.
        if update.candle.is_closed:
            self.analysis_dirty.add((update.symbol,update.timeframe))
            self.candles.invalidate(lambda key: key[:2] == (update.symbol, update.timeframe))
            for listener in tuple(self.closed_candle_listeners):
                listener(update.symbol, update.timeframe)

    async def aclose(self) -> None:
        self.analysis_windows.clear()
        self.analysis_dirty.clear()
        await self.candles.aclose()
        await self.discovery.aclose()
