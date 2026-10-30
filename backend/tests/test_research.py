import asyncio
from datetime import UTC, datetime
from decimal import Decimal
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.adapters.base import MarketDataError
from app.adapters.metrics import parse_bulk
from app.analysis.risk import TradePlan, plan_trade
from app.analysis.scoring import assess
from app.analysis.structure import analyze_structure
from app.backtesting.engine import BacktestRequest, prepare, simulate, run_validation
from app.indicators.engine import calculate, wilder
from app.main import create_app
from app.schemas.candles import Candle, CandleResponse, DataQuality, INTERVAL_MS
from app.schemas.markets import Contract, DiscoveryResponse
from app.schemas.platform import IndicatorSettings, Preferences


def market_frame(count=600,interval=3600000):
    x=np.arange(count); close=100+x*.02+8*np.sin(x/9)
    return pd.DataFrame({"time":1_700_006_400_000+x*interval,"open":close-.1,"high":close+1,"low":close-1,
                         "close":close,"volume":100+20*np.cos(x/11),"quote_volume":(100+20*np.cos(x/11))*close})


def test_wilder_known_seed_and_recursion():
    values=wilder(pd.Series([1.,2.,3.,6.]),3)
    assert values.iloc[:2].isna().all()
    assert values.iloc[2]==2 and values.iloc[3]==pytest.approx(10/3)


def test_indicators_are_causal_and_warmups_unavailable():
    data=market_frame(); settings=IndicatorSettings()
    full,catalog=calculate(data,settings,"1h")
    prefix,_=calculate(data.iloc[:350],settings,"1h")
    assert len(catalog)>=50
    for name,metadata in catalog.items():
        assert full[name].iloc[:metadata["warmup"]-1].isna().all(),name
        np.testing.assert_allclose(full[name].iloc[:350],prefix[name],equal_nan=True,err_msg=name)
    assert full.ema_200.iloc[-1] == pytest.approx(data.close.ewm(span=200,adjust=False).mean().iloc[-1])
    assert (full.high>=full.low).all()


def test_flat_prices_rsi_and_zero_volume_do_not_invent_values():
    data=market_frame();data[["open","high","low","close"]]=100.;data[["volume","quote_volume"]]=0.
    result,_=calculate(data,IndicatorSettings(),"1h")
    assert result.rsi_14.iloc[-1]==50
    assert np.isnan(result.vwap.iloc[-1]) and np.isnan(result.relative_volume.iloc[-1])
    assert result.atr.iloc[-1]==0


def test_structure_confirmation_never_precedes_right_bars():
    calculated,_=calculate(market_frame(),IndicatorSettings(),"1h")
    result=analyze_structure(calculated,"1h",3)
    assert result["swings"]
    for swing in result["swings"]:
        assert swing["confirmed_index"]==swing["index"]+3
        assert swing["confirmed_at"]==swing["time"]+4*3600000-1
    assert all(a["points"] and a["rule"] and a["timeframe"] for a in result["annotations"])
    assert all(a["confirmed"] is False for a in result["annotations"] if a["kind"] in {"sweep","divergence"})


@pytest.mark.parametrize("side,stop,target",[("LONG",95,110),("SHORT",105,90)])
def test_risk_budget_includes_costs_and_rounds_down(side,stop,target):
    request=TradePlan(side=side,equity=1000,entry=100,stop=stop,target=target,risk_percent="0.25",quantity_step="0.01",fee_bps=5,slippage_bps=10)
    result=plan_trade(request)
    assert result["potential_loss"]<=Decimal("2.5")
    assert result["risk_budget"]==Decimal("2.5")
    assert result["quantity"]%Decimal("0.01")==0
    assert result["net_reward_to_risk"]<result["gross_reward_to_risk"]
    assert result["costs_at_stop"]>0
    high_leverage=plan_trade(request.model_copy(update={"leverage":Decimal(10)}))
    assert high_leverage["potential_loss"]<=result["risk_budget"]


@pytest.mark.parametrize("override",[{"stop":100},{"target":90},{"equity":0},{"risk_percent":100},{"entry":"NaN"},{"tick_size":"0.3"}])
def test_invalid_trade_plans_rejected(override):
    with pytest.raises(ValidationError):
        TradePlan(**{"side":"LONG","equity":1000,"entry":100,"stop":95,"target":110,**override})


def test_no_recommendation_with_bad_data_or_quotes():
    preferences=Preferences();df,_=calculate(market_frame(),preferences.indicators,"1h");shape=analyze_structure(df,"1h")
    for usable in (True,False):
        result=assess(df,shape,preferences,{},[],[],usable,"1h")
        assert result["classification"]=="NO TRADE"
        assert result["scenarios"]["LONG"]["entry_region"] is None
        assert 0<=result["long_score"]<=100 and 0<=result["short_score"]<=100


def test_derivative_context_annotates_scenarios_without_changing_scores():
    preferences=Preferences();df,_=calculate(market_frame(),preferences.indicators,"1h");shape=analyze_structure(df,"1h")
    last=int(datetime.now(UTC).timestamp()*1000)//3600000*3600000;history=[{"time":last-(24-index)*3600000,"value":100+index} for index in range(25)]
    base=assess(df,shape,preferences,{"change_percent":2},[],[],True,"1h")
    contextual=assess(df,shape,preferences,{"change_percent":2},[],[],True,"1h",{"open_interest_history":history})
    assert contextual["long_score"]==base["long_score"] and contextual["short_score"]==base["short_score"]
    assert contextual["derivative_context"]["available"] is True
    assert any("Derivatives context (no score weight)" in item for item in contextual["scenarios"]["LONG"]["supporting"])
    assert any("Derivatives context (no score weight)" in item for item in contextual["scenarios"]["SHORT"]["conflicting"])


def test_metrics_invalid_and_crossed_book_never_produce_spread():
    result=parse_bulk([{"symbol":"TESTUSDT","bidPrice":"101","askPrice":"100","time":1}],"book")
    assert result["TESTUSDT"]["spread_bps"] is None
    with pytest.raises(MarketDataError): parse_bulk([{"symbol":"TESTUSDT","lastPrice":"NaN"}],"ticker")


def backtest_config():
    return BacktestRequest(symbol="TESTUSDT",timeframe="1h",start_time=1_700_006_400_000,end_time=1_700_006_400_000+600*3600000-1)


def test_backtest_causality_costs_and_holdout_separation():
    data=market_frame();config=backtest_config();prepared=prepare(data,config)
    prefix=prepare(data.iloc[:350],config)
    np.testing.assert_allclose(prepared.signal.iloc[:350],prefix.signal)
    result=run_validation(data,config)
    trades=result["out_of_sample"]["trades"]
    assert trades
    for trade in trades:
        assert trade["entry_time"]>trade["signal_time"]>=result["split_time"]
        assert trade["net_pnl"]==pytest.approx(trade["gross_pnl"]-trade["fees"]-trade["funding"])
    assert all(f["train_end_time"]<f["test_start_time"] for f in result["walk_forward"])
    changed=data.copy();changed.loc[400:,"close"]*=2
    result2=run_validation(changed,config)
    assert result["sensitivity"]==result2["sensitivity"]


def test_same_bar_stop_target_uses_adverse_stop():
    data=market_frame(4);data.loc[:,["open","close"]]=100.;data.loc[:,"high"]=110.;data.loc[:,"low"]=90.
    data["signal"]=[0,1,0,0];data["atr"]=1.
    result=simulate(data,backtest_config(),0,4)
    assert result["trades"][0]["reason"].startswith("stop")
    assert result["trades"][0]["net_pnl"]<0


def test_backtest_rejects_missing_history():
    with pytest.raises(ValueError,match="gaps"):
        run_validation(market_frame().drop(index=250).reset_index(drop=True),backtest_config())


class ResearchProvider:
    def __init__(self): self.calls=0
    async def discover_contracts(self):
        return DiscoveryResponse(fetched_at=datetime.now(UTC),count=2,contracts=[Contract(symbol="TESTUSDT",base_asset="TEST"),Contract(symbol="LOWUSDT",base_asset="LOW")])
    async def fetch_candles(self,query):
        self.calls+=1;duration=INTERVAL_MS[query.timeframe];now=int(datetime.now(UTC).timestamp()*1000)
        data=market_frame(query.limit,duration);data.time=(now//duration-query.limit+np.arange(query.limit))*duration
        candles=[Candle(open_time=int(row.time),close_time=int(row.time)+duration-1,open=str(row.open),high=str(row.high),low=str(row.low),close=str(row.close),volume=str(row.volume),quote_volume=str(row.quote_volume),trades=10,taker_buy_volume="1",taker_buy_quote_volume="100",is_closed=True) for row in data.itertuples()]
        return CandleResponse(symbol=query.symbol,timeframe=query.timeframe,fetched_at=datetime.now(UTC),server_time=now,candles=candles,quality=DataQuality(status="usable"))
    async def fetch_metrics(self):
        now=int(datetime.now(UTC).timestamp()*1000)
        return {"fetched_at":datetime.now(UTC).isoformat(),"limitations":[],"rows":{s:{"last_price":110,"bid":109.99,"ask":110.01,"spread_bps":2,"volume":volume,"change_percent":2,"ticker_time":now,"book_time":now,"mark_price":110,"index_price":110,"funding_rate":0.0001,"next_funding_time":now+3600000} for s,volume in [("TESTUSDT",10000000),("LOWUSDT",100)]}}
    async def fetch_derivatives(self,symbol): return {"symbol":symbol,"limitations":["Synthetic test provider: derivatives not supplied."]}
    async def aclose(self): pass


def test_complete_report_settings_planner_and_persistence(settings):
    provider=ResearchProvider()
    with TestClient(create_app(settings,provider=provider)) as client:
        response=client.post('/api/v1/analysis',json={"symbol":"TESTUSDT","timeframe":"1h","higher_timeframes":["4h"],"include_derivatives":True})
        assert response.status_code==200,response.text
        report=response.json()
        assert len(report["indicators"])>=50 and report["structure"]["annotations"]
        assert set(report["assessment"]["scenarios"])=={"LONG","SHORT"}
        assert report["manual_only"] and report["data_limitations"]
        calls=provider.calls
        assert client.post('/api/v1/analysis',json={"symbol":"TESTUSDT","timeframe":"1h","higher_timeframes":["4h"],"include_derivatives":True}).status_code==200
        assert provider.calls==calls
        assert len(client.get('/api/v1/history/reports').json())==1
        assert client.put('/api/v1/watchlist',json={"symbols":["testusdt"],"timeframe":"4h"}).status_code==200
        preferences=client.get('/api/v1/settings').json();preferences["candidate_score"]=70
        assert client.put('/api/v1/settings',json=preferences).status_code==200
        risk=client.post('/api/v1/planner',json={"side":"LONG","equity":1000,"entry":100,"stop":95,"target":110})
        assert risk.status_code==200 and risk.json()["hypothetical_only"]
        assert client.put('/api/v1/settings',json=preferences,headers={"Origin":"https://evil.example"}).status_code==403
    with TestClient(create_app(settings,provider=ResearchProvider())) as client:
        assert client.get('/api/v1/settings').json()["candidate_score"]==70
        assert client.get('/api/v1/watchlist').json()["symbols"]==["TESTUSDT"]


def test_fresh_report_draws_both_hypothetical_entry_regions(settings):
    class CurrentProvider(ResearchProvider):
        async def fetch_candles(self, query):
            page = await super().fetch_candles(query)
            duration = INTERVAL_MS[query.timeframe]
            candles = [candle.model_copy(update={"open_time": candle.open_time+duration,
                        "close_time": candle.close_time+duration, "is_closed": index < len(page.candles)-1})
                       for index, candle in enumerate(page.candles)]
            return page.model_copy(update={"candles": candles})

    with TestClient(create_app(settings,provider=CurrentProvider())) as client:
        response = client.post('/api/v1/analysis',json={"symbol":"TESTUSDT","timeframe":"1h",
                                                 "higher_timeframes":["4h"],"include_derivatives":False})
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["market_overview"]["quality"]["status"] == "usable"
        for side, scenario in report["assessment"]["scenarios"].items():
            assert scenario["entry"] is not None
            region = next(a for a in report["structure"]["annotations"]
                          if a["kind"] == "plan" and a["label"] == f"{side} Entry region")
            assert region["timeframe"] == report["timeframe"]
            assert [point["price"] for point in region["points"]] == scenario["entry_region"]
            assert not region["confirmed"] and region["rule"]


async def test_scanner_keeps_low_liquidity_and_filters_only_on_request(settings):
    from app.storage.platform import PlatformStore
    from app.market_data.service import MarketDataService
    from app.services.platform import PlatformService
    from app.schemas.platform import ScanFilter
    store=PlatformStore(settings.database_path);await store.initialize()
    market=MarketDataService(ResearchProvider(),store,settings);platform=PlatformService(market,store,settings)
    try:
        await platform.start_scan('1h');await asyncio.wait_for(platform.scan_task,20)
        result=await platform.scanner(ScanFilter())
        assert result["status"]=="complete" and result["total"]==2
        assert {r["symbol"] for r in result["rows"]}=={"TESTUSDT","LOWUSDT"}
        low=next(r for r in result["rows"] if r["symbol"]=="LOWUSDT")
        assert low["liquidity"]=="warning" and low["classification"]=="NO TRADE"
        filtered=await platform.scanner(ScanFilter(minimum_volume=5000000))
        assert filtered["displayed"]==1
    finally:
        await platform.aclose();await market.aclose()
