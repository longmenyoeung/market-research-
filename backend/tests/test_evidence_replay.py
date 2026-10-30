"""Software validation on synthetic histories, never evidence of market returns."""
import json
import threading

import numpy as np
import pandas as pd
import pytest

from app.analysis.scoring import assess
from app.analysis.structure import analyze_structure
from app.backtesting.engine import BacktestRequest, prepare, run_validation, simulate
from app.backtesting.evidence import StructureReplay
from app.indicators.engine import calculate
from app.schemas.platform import Preferences
from test_research import market_frame


def config(**updates):
    data=market_frame()
    prefs=Preferences(candidate_score=50,minimum_volume=0,require_higher_alignment=False,fee_bps=0,slippage_bps=0)
    return BacktestRequest(symbol='TESTUSDT',strategy='evidence',timeframe='1h',start_time=int(data.time.iloc[0]),
        end_time=int(data.time.iloc[-1])+3599999,analysis_settings=prefs,confirmation='candidate',
        fee_bps=0,slippage_bps=0,spread_bps=0,**updates)


def test_incremental_structure_matches_report_rules_on_closed_prefixes():
    request=config()
    df,_=calculate(market_frame(),request.analysis_settings.indicators,'1h')
    replay=StructureReplay(df,'1h',3)
    for i in range(len(df)):
        snapshot=replay.advance(i)
        if i in {25,100,210,319,450,599}:
            chart=analyze_structure(df.iloc[:i+1],'1h',3)
            for key in ('trend','support','resistance','events'):
                assert snapshot[key]==chart[key],(i,key)


def test_replay_uses_the_report_assessor_with_historical_source_clock():
    request=config();raw=market_frame();prepared=prepare(raw,request)
    df,_=calculate(raw,request.analysis_settings.indicators,'1h')
    for i in (250,310,400,599):
        prefix=df.iloc[:i+1];asof=int(prefix.time.iloc[-1])+3599999;close=float(prefix.close.iloc[-1])
        metric=dict(last_price=close,bid=close,ask=close,spread_bps=0,volume=float(prefix.quote_volume.tail(24).sum()),
                    book_time=asof,ticker_time=asof,funding_rate=0)
        assessment=assess(prefix,analyze_structure(prefix,'1h'),request.analysis_settings,metric,[],[],True,'1h',as_of_ms=asof)
        assert prepared.long_score.iloc[i]==assessment['long_score']
        assert prepared.short_score.iloc[i]==assessment['short_score']
        assert prepared.evidence_classification.iloc[i]==assessment['classification']
    assert prepared.signal.abs().sum()>0


def test_primary_and_higher_replay_never_read_future_candles():
    request=config(higher_timeframes=['4h'])
    raw=market_frame();higher=market_frame(800,14400000)
    higher.time=raw.time.iloc[0]-700*14400000+np.arange(800)*14400000
    original=prepare(raw,request,{'4h':higher})
    changed=raw.copy();changed.loc[400:,['open','high','low','close']]*=2
    changed_higher=higher.copy();changed_higher.loc[changed_higher.time>raw.time.iloc[399],['open','high','low','close']]*=3
    replay=prepare(changed,request,{'4h':changed_higher})
    for field in ('signal','long_score','short_score','stop_reference'):
        np.testing.assert_allclose(original[field].iloc[:400],replay[field].iloc[:400],equal_nan=True)
    for point in original.itertuples():
        assert point.context_known_at <= point.time+3599999


def test_evidence_validation_has_holdout_threshold_sensitivity_and_replay_audit():
    result=run_validation(market_frame(),config())
    json.dumps(result,allow_nan=False)
    assert result['strategy']=='Report evidence engine historical replay'
    assert result['assessment_trace']
    assert all(s['parameter']=='candidate_score' for s in result['sensitivity'])
    assert all(f['train_end_time']<f['test_start_time'] for f in result['walk_forward'])
    assert all(t['entry_time']>t['signal_time']>=result['split_time'] for t in result['out_of_sample']['trades'])


def test_missing_required_higher_context_fails_closed():
    request=config(higher_timeframes=['4h'])
    request.analysis_settings.require_higher_alignment=True
    prepared=prepare(market_frame(),request)
    assert not prepared.signal.any()
    assert set(prepared.evidence_classification)=={'NO TRADE'}


def test_cooperative_cancellation_stops_numerical_work():
    stop=threading.Event();stop.set()
    with pytest.raises(ValueError,match='cancelled'):
        run_validation(market_frame(),config(),cancel=stop)


def test_historical_funding_uses_observed_timestamps_rates_and_settlement_marks():
    data=market_frame(4)
    data[['open','close']]=100.;data['high']=100.2;data['low']=99.8
    data['signal']=[0,1,0,0];data['atr']=1.
    request=config().model_copy(update={'strategy':'ema','funding_mode':'historical','max_holding_bars':2})
    event={'time':int(data.time.iloc[2])+12345,'rate':.01,'mark_price':150.}
    result=simulate(data,request,0,4,[event])
    trade=result['trades'][0]
    assert trade['funding']==pytest.approx(trade['quantity']*150*.01)
    assert trade['net_pnl']==pytest.approx(trade['gross_pnl']-trade['fees']-trade['funding'])
