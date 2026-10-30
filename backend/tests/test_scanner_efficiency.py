import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.adapters.binance_usdm import BinanceUSDMAdapter
from app.adapters.base import MarketDataError
from app.market_data.quota import RollingBudget, request_weight
from app.market_data.service import MarketDataService
from app.schemas.candles import CandleQuery, INTERVAL_MS
from app.storage.platform import PlatformStore
from app.services.platform import PlatformService
from app.schemas.platform import ScanFilter
from test_research import ResearchProvider, market_frame
from app.schemas.candles import Candle, CandleResponse, DataQuality


class TimedProvider(ResearchProvider):
    def __init__(self):
        super().__init__()
        self.now=datetime(2026,10,30,12,30,20,tzinfo=UTC)
        self.limits=[]

    async def fetch_candles(self,query):
        self.calls+=1;self.limits.append(query.limit)
        duration=INTERVAL_MS[query.timeframe]
        now=int(self.now.timestamp()*1000)
        data=market_frame(query.limit,duration)
        data.time=(now//duration-query.limit+1+__import__('numpy').arange(query.limit))*duration
        candles=[Candle(open_time=int(row.time),close_time=int(row.time)+duration-1,open=str(row.open),high=str(row.high),
            low=str(row.low),close=str(row.close),volume=str(row.volume),quote_volume=str(row.quote_volume),trades=10,
            taker_buy_volume='1',taker_buy_quote_volume='100',is_closed=int(row.time)+duration<now) for row in data.itertuples()]
        return CandleResponse(symbol=query.symbol,timeframe=query.timeframe,fetched_at=self.now,server_time=now,
                              candles=candles,quality=DataQuality(status='usable'))


def test_weight_budget_counts_rolling_usage_and_provider_headers():
    budget=RollingBudget(100,60)
    budget.record(40,0);budget.record(40,10)
    assert budget.delay(20,20)==0
    assert budget.delay(40,20)==40
    budget.observe_usage(99,20)
    assert budget.delay(2,20)>0
    assert budget.delay(100,80)==0
    assert [request_weight('/fapi/v1/klines',{'limit':n}) for n in (3,400,1000,1500)]==[1,2,5,10]


async def test_server_time_is_shared_between_concurrent_candle_reads(settings):
    paths=[];now=int(datetime.now(UTC).timestamp()*1000)
    def handle(request):
        paths.append(request.url.path)
        return httpx.Response(200,json={'serverTime':now} if request.url.path.endswith('/time') else [])
    adapter=BinanceUSDMAdapter(settings,transport=httpx.MockTransport(handle))
    import asyncio
    try:
        await asyncio.gather(*(adapter.fetch_candles(CandleQuery(symbol=s,timeframe='1m',limit=3)) for s in ('AAAUSDT','BBBUSDT','CCCUSDT')))
        assert paths.count('/fapi/v1/time')==1
        assert paths.count('/fapi/v1/klines')==3
    finally:
        await adapter.aclose()


async def test_analysis_window_reuses_history_then_fetches_only_recent_bars(settings,monkeypatch):
    provider=TimedProvider()
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None): return provider.now
    monkeypatch.setattr('app.market_data.service.datetime',Clock)
    store=PlatformStore(settings.database_path);await store.initialize()
    market=MarketDataService(provider,store,settings)
    try:
        query=CandleQuery(symbol='TESTUSDT',timeframe='1m',limit=400)
        first=await market.analysis_candles(query)
        again=await market.analysis_candles(query)
        assert provider.limits==[400]
        assert again.fetched_at==first.fetched_at and not again.candles[-1].is_closed
        provider.now+=timedelta(minutes=1)
        advanced=await market.analysis_candles(query)
        assert provider.limits==[400,3]
        assert len(advanced.candles)==400
        assert next(c for c in advanced.candles if c.open_time==first.candles[-1].open_time).is_closed
        assert not advanced.candles[-1].is_closed
    finally:
        await market.aclose();await store.aclose()


async def test_scanner_updates_quotes_without_new_candle_fetch_or_indicator_work(settings,monkeypatch):
    provider=TimedProvider();provider.now=datetime.now(UTC)
    store=PlatformStore(settings.database_path);await store.initialize()
    market=MarketDataService(provider,store,settings);service=PlatformService(market,store,settings)
    try:
        await service.start_scan('1d');await service.scan_task
        calls=provider.calls
        def unexpected(*args,**kwargs): raise AssertionError('Unchanged bars recalculated')
        monkeypatch.setattr('app.services.platform.calculate',unexpected)
        service.metrics_cache.invalidate(lambda _:True)
        service.scan_quote_cache.invalidate(lambda _:True)
        result=await service.scanner(ScanFilter())
        await service.quote_task
        result=await service.scanner(ScanFilter())
        assert result['total']==2 and provider.calls==calls
        assert all(r['quotes_updated_at'] for r in result['rows'])
        await service.start_scan('1d');await service.scan_task
        assert provider.calls==calls
    finally:
        await service.aclose();await market.aclose();await store.aclose()


async def test_saved_market_rows_restore_as_stale_and_survive_provider_timeout(settings):
    provider=ResearchProvider()
    store=PlatformStore(settings.database_path);await store.initialize()
    market=MarketDataService(provider,store,settings);service=PlatformService(market,store,settings)
    try:
        await service.start_scan('1h');await service.scan_task
        assert service.scan['status']=='complete'
        async def timed_out():
            raise MarketDataError('upstream_timeout','Public market-data request timed out.',504)
        provider.discover_contracts=timed_out
        market.discovery.invalidate(lambda _:True)
        await service.start_scan('1h')
        assert service.scan['cached_rows'] and service.scan['total']==2
        assert all(row['quality']=='stale' and row['classification']=='NO TRADE' for row in service.scan['rows'])
        await service.scan_task
        assert service.scan['status']=='cached'
    finally:
        await service.aclose();await market.aclose()

    class OfflineProvider(ResearchProvider):
        async def discover_contracts(self):
            raise MarketDataError('upstream_timeout','Public market-data request timed out.',504)

    offline=OfflineProvider();market=MarketDataService(offline,store,settings)
    restored=PlatformService(market,store,settings)
    try:
        await restored.initialize()
        snapshot=await restored.scanner(ScanFilter())
        assert snapshot['status']=='cached' and snapshot['total']==2
        expected_symbols=[row['symbol'] for row in snapshot['rows']]
        assert sorted(snapshot['universe_symbols'])==sorted(expected_symbols)
        filtered=await restored.scanner(ScanFilter(minimum_volume=10**20))
        assert filtered['rows']==[]
        assert sorted(filtered['universe_symbols'])==sorted(expected_symbols)
        assert all(row['quality']=='stale' and row['classification']=='NO TRADE' for row in snapshot['rows'])
        assert all(row['long_score'] is None and row['short_score'] is None for row in snapshot['rows'])
        await restored.start_scan('1h');await restored.scan_task
        after=await restored.scanner(ScanFilter())
        assert after['status']=='cached' and after['total']==2
        assert after['error']=='upstream_timeout'
    finally:
        await restored.aclose();await market.aclose();await store.aclose()


async def test_scanner_returns_saved_rows_without_waiting_for_quote_refresh(settings):
    provider=ResearchProvider()
    store=PlatformStore(settings.database_path);await store.initialize()
    market=MarketDataService(provider,store,settings);service=PlatformService(market,store,settings)
    try:
        await service.start_scan('1h');await service.scan_task
        gate=asyncio.Event()
        original=provider.fetch_metrics
        async def delayed_metrics():
            await gate.wait()
            return await original()
        provider.fetch_metrics=delayed_metrics
        service.metrics_cache.invalidate(lambda _:True)
        service.scan_quote_cache.invalidate(lambda _:True)
        result=await asyncio.wait_for(service.scanner(ScanFilter()),1)
        assert result['total']==2 and service.quote_task is not None
        gate.set();await service.quote_task
    finally:
        await service.aclose();await market.aclose();await store.aclose()
