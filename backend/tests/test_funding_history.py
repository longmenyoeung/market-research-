import asyncio
from datetime import UTC, datetime

import httpx
import pytest
from types import SimpleNamespace
from fastapi.testclient import TestClient

from app.adapters.base import MarketDataError
from app.adapters.binance_usdm import BinanceUSDMAdapter
from app.core.safety import ReadOnlyViolation
from app.main import create_app
from app.backtesting.service import BacktestService
from app.schemas.derivatives import FundingQuery
from test_research import ResearchProvider

QUERY=FundingQuery(symbol='TESTUSDT',start_time=1_700_000_000_000,end_time=1_700_100_000_000)


async def test_public_funding_history_validates_range_and_settlement_price(settings):
    def handle(request):
        assert request.method=='GET'
        assert dict(request.url.params)=={'symbol':'TESTUSDT','startTime':str(QUERY.start_time),'endTime':str(QUERY.end_time),'limit':'1000'}
        return httpx.Response(200,json=[{'symbol':'TESTUSDT','fundingTime':QUERY.start_time,
            'fundingRate':'0.0001','markPrice':'123.45'}])
    adapter=BinanceUSDMAdapter(settings,transport=httpx.MockTransport(handle))
    try:
        result=await adapter.fetch_funding_history(QUERY)
        assert result==[dict(time=QUERY.start_time,rate=.0001,mark_price=123.45,rate_type='Regular')]
    finally:
        await adapter.aclose()


@pytest.mark.parametrize('override',[
    {'fundingTime':QUERY.end_time+1},{'markPrice':'NaN'},{'markPrice':None},
    {'symbol':'OTHERUSDT'},{'fundingRate':'Infinity'},
])
async def test_invalid_funding_never_gets_substituted(settings,override):
    point={'symbol':'TESTUSDT','fundingTime':QUERY.start_time,'fundingRate':'0.001','markPrice':'100',**override}
    adapter=BinanceUSDMAdapter(settings,transport=httpx.MockTransport(lambda _:httpx.Response(200,json=[point])))
    try:
        with pytest.raises(MarketDataError): await adapter.fetch_funding_history(QUERY)
    finally:
        await adapter.aclose()


@pytest.mark.parametrize('params',[
    {'symbol':'TESTUSDT','startTime':'1','endTime':'2','limit':'1001'},
    {'symbol':'TESTUSDT','startTime':'3','endTime':'2','limit':'1000'},
    {'symbol':'TESTUSDT','startTime':'1','endTime':'2','limit':'1000','signature':'x'},
])
async def test_funding_parameter_guard_rejects_before_network(settings,params):
    def forbidden(_): raise AssertionError('Unsafe query reached transport')
    adapter=BinanceUSDMAdapter(settings,transport=httpx.MockTransport(forbidden))
    try:
        with pytest.raises(ReadOnlyViolation):
            await adapter._client.get('https://fapi.binance.com/fapi/v1/fundingRate',params=params)
    finally:
        await adapter.aclose()


def test_local_backtest_cancellation_is_persisted(settings):
    class BlockingProvider(ResearchProvider):
        async def fetch_candles(self,query):
            await asyncio.Event().wait()
    with TestClient(create_app(settings,provider=BlockingProvider())) as client:
        response=client.post('/api/v1/backtests',json=dict(symbol='TESTUSDT',start_time=QUERY.start_time,
                             end_time=QUERY.start_time+600*3600000-1))
        assert response.status_code==202,response.text
        run_id=response.json()['id']
        cancelled=client.post(f'/api/v1/backtests/{run_id}/cancel')
        assert cancelled.status_code==200 and cancelled.json()['status']=='cancelled'
        assert client.get('/api/v1/backtests').json()[0]['data']['status']=='cancelled'


async def test_funding_pagination_preserves_second_rate_type_at_inclusive_boundary():
    boundary=QUERY.start_time+999
    regular=lambda time: dict(time=time,rate=.0001,mark_price=100,rate_type='Regular')
    calls=[]
    class Provider:
        async def fetch_funding_history(self,query):
            calls.append(query.start_time)
            if len(calls)==1:
                return [regular(QUERY.start_time+i) for i in range(1000)]
            return [regular(boundary),dict(time=boundary,rate=.0002,mark_price=101,rate_type='Special')]
    service=BacktestService(SimpleNamespace(provider=Provider()),None)
    points=await service._load_funding('TESTUSDT',QUERY.start_time,QUERY.end_time)
    assert calls==[QUERY.start_time,boundary]
    assert len(points)==1001
    assert points[-1]['rate_type']=='Special'
