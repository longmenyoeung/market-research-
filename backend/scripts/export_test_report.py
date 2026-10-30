"""Generate a labeled synthetic browser-test fixture; never used by production."""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'backend'/'tests')]
from app.core.config import Settings
from app.storage.platform import PlatformStore
from app.market_data.service import MarketDataService
from app.services.platform import PlatformService
from app.schemas.platform import AnalysisRequest
from test_research import ResearchProvider


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix='mca-browser-fixture-') as temp:
        config=Settings(_env_file=None,database_path=Path(temp)/'test.db')
        store=PlatformStore(config.database_path);await store.initialize()
        market=MarketDataService(ResearchProvider(),store,config)
        service=PlatformService(market,store,config)
        try:
            report=await service.report(AnalysisRequest(symbol='TESTUSDT',timeframe='1h',higher_timeframes=['4h']))
            report['test_fixture_notice']='SYNTHETIC TEST DATA — NOT LIVE MARKET DATA'
            path=ROOT/'frontend'/'tests'/'report.fixture.json'
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps(report,default=str,allow_nan=False),encoding='utf-8')
            print(f'Wrote synthetic browser fixture: {path.name}')
        finally:
            await service.aclose();await market.aclose()

asyncio.run(main())
