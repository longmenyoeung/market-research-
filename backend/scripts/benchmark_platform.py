"""Synthetic complete-scanner benchmark. No exchange, prices or returns are claimed."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time
from datetime import UTC,datetime

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'backend/tests')]
from app.core.config import Settings
from app.market_data.service import MarketDataService
from app.storage.platform import PlatformStore
from app.services.platform import PlatformService
from app.schemas.markets import Contract,DiscoveryResponse
from app.schemas.platform import ScanFilter
from test_scanner_efficiency import TimedProvider


class BenchmarkProvider(TimedProvider):
    def __init__(self,count):
        super().__init__()
        self.now=datetime.now(UTC)
        self.symbols=[f'SYN{i:04d}USDT' for i in range(count)]
        self.metric_calls=0

    async def discover_contracts(self):
        return DiscoveryResponse(fetched_at=datetime.now(UTC),count=len(self.symbols),
            contracts=[Contract(symbol=s,base_asset=s[:-4]) for s in self.symbols])

    async def fetch_metrics(self):
        self.metric_calls+=1
        now=int(datetime.now(UTC).timestamp()*1000)
        return dict(fetched_at=datetime.now(UTC).isoformat(),limitations=['SYNTHETIC BENCHMARK ONLY'],
            rows={s:dict(last_price=110,bid=109.99,ask=110.01,volume=100 if i%10==0 else 20_000_000,
                change_percent=2,spread_bps=2,ticker_time=now,book_time=now) for i,s in enumerate(self.symbols)})


async def run(count):
    with tempfile.TemporaryDirectory(prefix='mca-platform-benchmark-') as temp:
        config=Settings(_env_file=None,database_path=Path(temp)/'benchmark.db')
        provider=BenchmarkProvider(count)
        store=PlatformStore(config.database_path);await store.initialize()
        market=MarketDataService(provider,store,config);service=PlatformService(market,store,config)
        try:
            start=time.perf_counter();await service.start_scan('1d');await service.scan_task
            cold=time.perf_counter()-start
            assert service.scan['status']=='complete',service.scan
            calls=provider.calls
            service.metrics_cache.invalidate(lambda _:True);service.scan_quote_cache.invalidate(lambda _:True)
            start=time.perf_counter();snapshot=await service.scanner(ScanFilter());quotes=time.perf_counter()-start
            assert snapshot['total']==count and snapshot['displayed']==count
            assert provider.calls==calls
            start=time.perf_counter();await service.start_scan('1d');await service.scan_task
            warm=time.perf_counter()-start
            assert provider.calls==calls
            frames=sum(entry[1][0].memory_usage(deep=True).sum() for entry in service.calculation_cache._entries.values())
            return dict(source='SYNTHETIC provider with zero network latency; not a live-exchange capacity certification',
                symbols=count,timeframe='1d',cold_scan_seconds=round(cold,3),warm_scan_seconds=round(warm,3),
                full_universe_quote_refresh_seconds=round(quotes,3),cold_candle_fetches=calls,
                additional_warm_candle_fetches=provider.calls-calls,all_symbols_visible=snapshot['displayed']==count,
                liquidity_warnings=snapshot['overview']['liquidity_warnings'],
                cached_dataframe_megabytes=round(int(frames)/1024**2,2),
                memory_note='DataFrame storage only; excludes Python objects, annotations, SQLite, browser and process overhead')
        finally:
            await service.aclose();await market.aclose();await store.aclose()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--symbols',type=int,default=500)
    parser.add_argument('--save',action='store_true')
    args=parser.parse_args()
    if not 1<=args.symbols<=1000: parser.error('symbols must be 1..1000')
    result=asyncio.run(run(args.symbols))
    output=json.dumps(result,indent=2)
    if args.save:
        path=ROOT/'docs/benchmarks/synthetic-platform.json';path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(output+'\n',encoding='utf-8')
    print(output)
