import asyncio

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.analysis.indicator_study import build_signals
from app.analysis.signal_profiles import attach_profile_context
from app.backtesting.engine import BacktestRequest, SimulationConfig, prepare, simulate, summarize_trades, run_validation
from app.indicators.engine import calculate
from app.main import create_app
from app.schemas.candles import CandleQuery
from app.schemas.indicator_study import IndicatorStudyRequest, StudyRules
from app.schemas.platform import IndicatorSettings, Preferences
from app.services.study_alerts import record_study_alerts
from test_indicator_study import signal_frame
from test_research import ResearchProvider, market_frame


def profile_data():
    base = 1_700_006_400_000//86400000*86400000
    raw = market_frame(600, 900000); raw.time = base+np.arange(600)*900000
    context = market_frame(400, 3600000); context.time = base-300*3600000+np.arange(400)*3600000
    context.close = 100+np.arange(400)*.1
    settings = IndicatorSettings(trend_fast=3, trend_slow=5, trend_anchor=10)
    rules = StudyRules(minimum_adx=5, minimum_relative_volume=.1, buy_rsi_max=99, sell_rsi_min=1, signal_spacing_bars=1)
    return raw, context, settings, rules


def test_chart_and_strategy_use_identical_nonempty_causal_signal_events():
    raw, context, settings, rules = profile_data()
    calculated, _ = calculate(raw, settings, "15m")
    enriched = attach_profile_context(calculated, "15m", settings, {"1h":context}, ["1h"])
    study = build_signals(enriched, "15m", settings, rules, "men_crypto")
    assert len(study["signals"]) > 0
    config = BacktestRequest(symbol="TESTUSDT",timeframe="15m",strategy="indicator",signal_profile="men_crypto",
        analysis_settings=Preferences(indicators=settings),study_rules=rules,higher_timeframes=["1h"],
        start_time=int(raw.time.iloc[0]),end_time=int(raw.time.iloc[-1])+899999)
    prepared = prepare(raw, config, {"1h":context})
    assert [(int(row.time), int(row.signal)) for row in prepared.itertuples() if row.signal] == [
        (signal["time"],1 if signal["side"]=="BUY" else -1) for signal in study["signals"]]
    assert (prepared.context_known_at <= prepared.time+899999).all()
    cutoff = int(raw.time.iloc[299])+899999
    changed = context.copy(); changed.loc[changed.time+3599999>cutoff,"close"] += 10000
    prefix, _ = calculate(raw.iloc[:300], settings, "15m")
    prefix = attach_profile_context(prefix,"15m",settings,{"1h":changed},["1h"])
    earlier = build_signals(prefix,"15m",settings,rules,"men_crypto")["signals"]
    assert earlier == [signal for signal in study["signals"] if signal["known_at"]<=cutoff]


def test_shared_men_crypto_profile_runs_separate_development_and_walk_forward_windows():
    raw, context, settings, rules=profile_data()
    config=BacktestRequest(symbol="TESTUSDT",timeframe="15m",strategy="indicator",signal_profile="men_crypto",
        analysis_settings=Preferences(indicators=settings),study_rules=rules,higher_timeframes=["1h"],
        start_time=int(raw.time.iloc[0]),end_time=int(raw.time.iloc[-1])+899999)
    result=run_validation(raw,config,{"1h":context})
    assert result["development"]["metrics"]["trades"]>0
    assert len(result["walk_forward"])==3
    assert result["sensitivity"][0]["parameter"]=="reward_multiple"
    assert "average_winning_trade" in result["out_of_sample"]["metrics"]
    assert all(fold["train_end_time"]<fold["test_start_time"] for fold in result["walk_forward"])


@pytest.mark.parametrize("field,value", [("vwap",101.),("vwap_complete_session",False),("higher_buy",False)])
def test_men_crypto_requires_vwap_and_confirmed_higher_alignment(field, value):
    data = signal_frame().iloc[:2].copy()
    data["vwap"]=99.;data["vwap_complete_session"]=True;data["higher_buy"]=True;data["higher_sell"]=False
    assert build_signals(data,"1h",IndicatorSettings(),StudyRules(),"men_crypto")["signals"]
    data[field]=value
    assert not build_signals(data,"1h",IndicatorSettings(),StudyRules(),"men_crypto")["signals"]


def test_partial_first_utc_session_cannot_confirm_vwap():
    raw, context, settings, _ = profile_data()
    data, _ = calculate(raw.iloc[1:], settings, "15m")
    enriched = attach_profile_context(data,"15m",settings,{"1h":context},["1h"])
    first_day = int(enriched.time.iloc[0])//86400000
    assert not enriched.loc[enriched.time//86400000==first_day,"vwap_complete_session"].any()
    assert enriched.loc[enriched.time//86400000>first_day,"vwap_complete_session"].all()


def test_men_crypto_long_and_short_conditions_are_symmetric():
    data=signal_frame();data["vwap"]=np.where(data.rsi_14>50,99.,101.)
    data["vwap_complete_session"]=True;data["higher_buy"]=data.rsi_14>50;data["higher_sell"]=data.rsi_14<50
    result=build_signals(data,"1h",IndicatorSettings(),StudyRules(signal_spacing_bars=1),"men_crypto")
    assert [signal["side"] for signal in result["signals"]]==["BUY","SELL","BUY"]


def exit_fixture(direction):
    rows=[]
    for index in range(5):
        rows.append({"time":1_700_006_400_000+index*3600000,"open":100.,"high":101.,"low":99.,"close":100.,"atr":2.,"signal":direction if index==1 else 0})
    if direction==1:
        rows[2].update(high=104.,low=99.,close=103.)
        rows[3].update(open=103.,high=105.,low=99.,close=104.)
    else:
        rows[2].update(high=101.,low=96.,close=97.)
        rows[3].update(open=97.,high=101.,low=95.,close=96.)
    return pd.DataFrame(rows)


@pytest.mark.parametrize("direction", [1,-1])
@pytest.mark.parametrize("trailing", [False,True])
def test_stop_adjustments_activate_next_bar_and_never_use_future_intrabar_path(direction, trailing):
    df=exit_fixture(direction)
    if trailing:
        df.loc[3,"low" if direction==1 else "high"]=101. if direction==1 else 99.
    config=SimulationConfig(symbol="TESTUSDT",strategy="indicator",study_rules=StudyRules(atr_stop_multiple=1),
        equity=1000,risk_percent=.5,fee_bps=0,slippage_bps=0,spread_bps=0,reward_multiple=10,
        break_even=not trailing,trailing_stop=trailing,trailing_atr=1,trailing_activation_r=1)
    result=simulate(df,config,0,len(df),close_at_end=False)
    trade=result["trades"][0]
    assert trade["entry_time"]==int(df.time.iloc[2])
    assert trade["exit_time"]==int(df.time.iloc[3])+3599999
    assert trade["exit"]==pytest.approx(100+direction*2 if trailing else 100)
    assert any(event["kind"]=="virtual_stop_adjusted" and event["known_at"]==int(df.time.iloc[2])+3599999 for event in result["events"])


def test_exposure_limit_and_one_virtual_position_are_enforced():
    df=exit_fixture(1);df[["open","close"]]=100.;df.high=101.;df.low=99.;df.signal=1
    config=SimulationConfig(symbol="TESTUSDT",equity=1000,risk_percent=.5,max_exposure_percent=10,
                            reward_multiple=10,fee_bps=5,slippage_bps=0,spread_bps=0)
    result=simulate(df,config,0,len(df),close_at_end=False)
    assert result["open_position"]["quantity"]*result["open_position"]["entry"]<=100
    assert sum(event["kind"]=="virtual_entry" for event in result["events"])==1
    with pytest.raises(ValidationError):
        SimulationConfig(symbol="TESTUSDT",max_exposure_percent=101)


def test_break_even_price_does_not_erase_transaction_costs():
    result=simulate(exit_fixture(1),SimulationConfig(symbol="TESTUSDT",strategy="indicator",study_rules=StudyRules(atr_stop_multiple=1),
        reward_multiple=10,break_even=True,fee_bps=5,slippage_bps=0,spread_bps=0),0,5,close_at_end=False)
    assert result["trades"][0]["net_pnl"]<0
    assert result["trades"][0]["fees"]>0


def test_average_win_loss_and_realized_r_metrics_have_explicit_empty_values():
    trades=[{"net_pnl":value,"realized_r":value/5,"fees":0,"funding":0,"holding_bars":1,"side":"LONG"} for value in (10,-5,0)]
    metrics=summarize_trades(trades,[],100)
    assert metrics["average_winning_trade"]==10
    assert metrics["average_losing_trade"]==-5
    assert metrics["average_realized_r"]==pytest.approx(1/3)
    empty=summarize_trades([],[],100)
    assert empty["average_winning_trade"] is None and empty["average_losing_trade"] is None


async def test_dashboard_alerts_are_deduplicated_and_never_emit_from_archives_or_stale_data():
    class Store:
        def __init__(self): self.saved={}
        async def records(self,*args,**kwargs): return [{"id":key} for key in self.saved]
        async def put(self,table,key,data,*args): self.saved[key]=data
    store=Store();request=IndicatorStudyRequest(symbol="TESTUSDT",source="live",profile="men_crypto")
    study={"as_of":1000,"current_signal":"BUY candidate","signals":[{"known_at":1000,"side":"BUY"}],
        "timeframe_plan":{"higher_timeframes":["4h"]},"states":[{"ready":True,"trend_bias":-1,"weak":False},{"ready":True,"trend_bias":1,"weak":True}]}
    tracker={"events":[{"known_at":1000,"kind":"virtual_tp","side":"LONG"}]}
    assert len(await record_study_alerts(store,request,study,tracker,True))==4
    assert await record_study_alerts(store,request,study,tracker,True)==[]
    changed=request.model_copy(update={"simulation":request.simulation.model_copy(update={"equity":20000})})
    assert len(await record_study_alerts(store,changed,study,tracker,True))==1
    assert all(data["dashboard_only"] and data["hypothetical_only"] for data in store.saved.values())
    assert await record_study_alerts(Store(),request,study,tracker,False)==[]
    assert await record_study_alerts(Store(),request.model_copy(update={"source":"archive"}),study,tracker,True)==[]


def test_archive_backtest_never_contacts_provider(settings):
    class NoNetwork(ResearchProvider):
        async def discover_contracts(self): raise AssertionError("Archive-only validation cannot discover live contracts")
        async def fetch_candles(self,query): raise AssertionError("Archive-only validation cannot fetch live history")
    app=create_app(settings,provider=NoNetwork())
    with TestClient(app) as client:
        page=client.portal.call(ResearchProvider().fetch_candles,CandleQuery(symbol="TESTUSDT",timeframe="1h",limit=600))
        client.portal.call(app.state.platform.store.save,"TESTUSDT","1h",page.candles)
        response=client.post("/api/v1/backtests",json={"symbol":"TESTUSDT","strategy":"indicator","signal_profile":"experimental",
            "data_source":"archive","funding_mode":"scenario","start_time":page.candles[0].open_time,"end_time":page.candles[-1].close_time})
        assert response.status_code==202,response.text
        key=response.json()["id"]
        async def wait(): await asyncio.wait_for(app.state.backtests.tasks[key],15)
        client.portal.call(wait)
        result=client.get(f"/api/v1/backtests/{key}").json()
        assert result["status"]=="complete",result
        assert result["result"]["candle_source"]=="archive"


def test_men_crypto_higher_trend_accepts_exact_ema_warmup(settings):
    app=create_app(settings,provider=ResearchProvider())
    with TestClient(app) as client:
        fixture=ResearchProvider()
        primary_asof=None
        for tf,count in [("1h",400),("4h",200)]:
            page=client.portal.call(fixture.fetch_candles,CandleQuery(symbol="TESTUSDT",timeframe=tf,limit=count))
            candles=page.candles
            if tf=="1h":
                primary_asof=candles[-1].close_time
            elif candles[-1].close_time>primary_asof:
                candles=[c.model_copy(update={"open_time":c.open_time-14400000,"close_time":c.close_time-14400000}) for c in candles]
            client.portal.call(app.state.platform.store.save,"TESTUSDT",tf,candles)
        response=client.post("/api/v1/indicator-study",json={"symbol":"TESTUSDT","profile":"men_crypto","higher_timeframes":["4h"]})
        assert response.status_code==200,response.text
        assert response.json()["multi_timeframe"][0]["historical_available"] is True
