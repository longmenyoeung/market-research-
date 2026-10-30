"""Causal replay of the live evidence assessor; no exchange transactions."""
from bisect import bisect_right
from collections import deque

import numpy as np
import pandas as pd

from app.analysis.scoring import assess, summarize
from app.indicators.engine import calculate, finite
from app.schemas.candles import INTERVAL_MS


class StructureReplay:
    """Incremental implementation of the documented closed-pivot/break rules.

    Prefix-parity tests compare this with analyze_structure. No right-hand candle
    is read until it closes, and a new pivot becomes an active break level only
    on the following bar, matching chart/report structure detection.
    """

    def __init__(self, data: pd.DataFrame, timeframe: str, window: int):
        self.data, self.window, self.duration = data, window, INTERVAL_MS[timeframe]
        self.swings = deque(maxlen=60)
        self.highs, self.lows = deque(maxlen=2), deque(maxlen=2)
        self.events = deque(maxlen=20)
        self.active, self.broken, self.pending = {}, set(), []
        self.bias = 0
        self.activate_next = []
        self.index = -1

    def advance(self, index: int) -> dict:
        if index != self.index+1:
            raise ValueError("Structure replay must advance one closed bar at a time.")
        self.index = index
        row = self.data.iloc[index]
        for swing in self.activate_next:
            self.active[swing['kind']] = swing
        self.activate_next = []
        center = index-self.window
        if center >= self.window:
            for kind, column, extreme, pivots in [('high','high',np.max,self.highs), ('low','low',np.min,self.lows)]:
                values = self.data[column].iloc[center-self.window:index+1].to_numpy()
                value = float(self.data[column].iloc[center])
                if value != extreme(values) or np.sum(values == value) != 1:
                    continue
                swing = dict(kind=kind, index=center, price=value, time=int(self.data.time.iloc[center]),
                             confirmed_index=index, confirmed_at=int(row.time)+self.duration-1)
                self.swings.append(swing)
                pivots.append(swing)
                self.activate_next.append(swing)
        for kind, sign in [('high',1), ('low',-1)]:
            swing = self.active.get(kind)
            if swing is None or (kind,swing['index']) in self.broken:
                continue
            if (row.close-swing['price'])*sign > 0:
                label = 'CHoCH' if self.bias and self.bias != sign else 'BOS'
                event = dict(kind=label, direction='bullish' if sign==1 else 'bearish',
                             time=int(row.time), level=swing['price'], index=index, confirmed=True)
                self.events.append(event)
                self.pending.append(event.copy())
                self.broken.add((kind,swing['index']))
                self.bias = sign
        for event in self.pending[:]:
            if index <= event['index']:
                continue
            if index-event['index'] > 10:
                self.pending.remove(event)
                continue
            atr = finite(row.get('atr'))
            tolerance = (atr if atr is not None else row.high-row.low)*.25
            holds = row.close > event['level'] if event['direction']=='bullish' else row.close < event['level']
            if row.low <= event['level']+tolerance and row.high >= event['level']-tolerance and holds:
                self.events.append({**event,'kind':'retest','time':int(row.time),'index':index})
                self.pending.remove(event)
        trend = 'neutral' if index >= 2*self.window else 'unknown'
        if len(self.highs)==len(self.lows)==2:
            dh = self.highs[-1]['price']-self.highs[-2]['price']
            dl = self.lows[-1]['price']-self.lows[-2]['price']
            trend = 'bullish' if dh>0 and dl>0 else 'bearish' if dh<0 and dl<0 else 'neutral'
        levels = [s['price'] for s in list(self.swings)[-40:]]
        return dict(trend=trend, events=list(self.events), annotations=[],
                    support=max((v for v in levels if v<row.close),default=None),
                    resistance=min((v for v in levels if v>row.close),default=None))


def context_timeframes(config) -> list[tuple[str,str]]:
    if config.strategy != 'evidence':
        return []
    higher = config.higher_timeframes or [tf for tf in INTERVAL_MS if INTERVAL_MS[tf]>INTERVAL_MS[config.timeframe]][:2]
    roles = [(tf,'higher') for tf in dict.fromkeys(higher)]
    if config.lower_timeframe:
        roles.append((config.lower_timeframe,'lower'))
    return roles


def prepare_evidence(data, config, context_data, funding_events, cancel=None) -> pd.DataFrame:
    settings = config.analysis_settings.model_copy(deep=True)
    # Calculate once at the lowest supported threshold. Validation can vary the
    # threshold using these same immutable historical evidence observations.
    settings.candidate_score = 50
    settings.risk_percent = config.risk_percent
    settings.fee_bps, settings.slippage_bps = config.fee_bps, config.slippage_bps
    df, _ = calculate(data, settings.indicators, config.timeframe)
    replay = StructureReplay(df,config.timeframe,settings.indicators.swing_window)
    contexts = {}
    for timeframe, role in context_timeframes(config):
        raw = context_data.get(timeframe)
        if raw is None or raw.empty:
            contexts[timeframe] = dict(role=role, data=pd.DataFrame(), summaries=[], closes=[])
            continue
        if np.any(np.diff(raw.time)!=INTERVAL_MS[timeframe]):
            raise ValueError(f'{timeframe} historical context contains gaps.')
        calculated, _ = calculate(raw, settings.indicators,timeframe)
        state = StructureReplay(calculated,timeframe,settings.indicators.swing_window)
        summaries = []
        for index in range(len(calculated)):
            if cancel is not None and cancel.is_set():
                raise ValueError('Historical validation cancelled.')
            shape = state.advance(index)
            prefix = calculated.iloc[:index+1]
            required = [f'ema_{settings.indicators.trend_anchor}', 'adx','atr','macd_hist',f'rsi_{settings.indicators.momentum_period}']
            usable = all(finite(prefix.iloc[-1].get(k)) is not None for k in required)
            summaries.append(dict(timeframe=timeframe, role=role, usable=usable,
                                  summary=summarize(prefix,shape,settings),
                                  as_of_ms=int(prefix.time.iloc[-1])+INTERVAL_MS[timeframe]-1))
        contexts[timeframe] = dict(role=role,data=calculated,summaries=summaries,
                                   closes=(calculated.time+INTERVAL_MS[timeframe]-1).tolist())
    duration = INTERVAL_MS[config.timeframe]
    daily_bars = max(1,86_400_000//duration)
    volumes = df.quote_volume.rolling(daily_bars,min_periods=daily_bars).sum()
    funding_times = [event['time'] for event in funding_events]
    output = []
    for i in range(len(df)):
        if cancel is not None and cancel.is_set():
            raise ValueError('Historical validation cancelled.')
        shape = replay.advance(i)
        asof = int(df.time.iloc[i])+duration-1
        aligned = []
        for tf, context in contexts.items():
            j = bisect_right(context['closes'],asof)-1
            # A context bar is valid until the next one should have closed. A
            # missing trailing page must not leave an old bullish/bearish vote
            # active indefinitely.
            stale = j >= 0 and asof-context['closes'][j] >= INTERVAL_MS[tf]
            if j<0 or stale:
                aligned.append(dict(timeframe=tf,role=context['role'],usable=False,summary={'trend':'unknown'},as_of_ms=None))
            else:
                aligned.append(context['summaries'][j])
        prefix, last = df.iloc[:i+1], df.iloc[i]
        half_spread = config.spread_bps/20000
        metric = dict(last_price=float(last.close), bid=float(last.close)*(1-half_spread),
                      ask=float(last.close)*(1+half_spread), spread_bps=config.spread_bps,
                      volume=finite(volumes.iloc[i]), ticker_time=asof,book_time=asof)
        latest_funding = bisect_right(funding_times,asof)-1
        if config.funding_mode == 'historical' and latest_funding>=0:
            metric['funding_rate'] = funding_events[latest_funding]['rate']
        elif config.funding_mode == 'scenario':
            metric['funding_rate'] = config.funding_bps_per_8h/10000
        required = [f'ema_{settings.indicators.trend_anchor}', 'adx','atr','macd_hist',f'rsi_{settings.indicators.momentum_period}']
        usable = all(finite(last.get(k)) is not None for k in required)
        result = assess(prefix,shape,settings,metric,aligned,[],usable,config.timeframe,as_of_ms=asof)
        side = result['classification'].split()[0]
        sign = 1 if side=='LONG' else -1 if side=='SHORT' else 0
        direction = 'bullish' if sign==1 else 'bearish'
        confirmation = config.confirmation=='candidate' or any(e['index']==i and e['direction']==direction for e in shape['events'])
        scenario = result['scenarios'].get(side,{})
        score = result['long_score'] if sign==1 else result['short_score'] if sign==-1 else 0
        output.append(dict(signal=sign if confirmation else 0, evidence_score=score,
                           stop_reference=scenario.get('stop'), target_reference=(scenario.get('targets') or [None,None])[1],
                           long_score=result['long_score'], short_score=result['short_score'],
                           evidence_classification=result['classification'] if score>=config.analysis_settings.candidate_score else 'WAIT' if sign else result['classification'],
                           context_known_at=max((c.get('as_of_ms') or 0 for c in aligned),default=0),
                           confirmation_met=bool(sign and confirmation)))
    for key in output[0] if output else []:
        df[key] = [row[key] for row in output]
    return df
