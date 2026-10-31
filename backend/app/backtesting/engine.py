"""Historical simulation only: causal close signals, next-bar-open virtual fills."""
import math
from bisect import bisect_left
from typing import Literal, Self

import numpy as np
import pandas as pd
from pydantic import Field, field_validator, model_validator

from app.core.risk_limits import MAX_DAILY_LOSS_PERCENT, MAX_RISK_PERCENT
from app.analysis.risk import RiskManager
from app.indicators.engine import wilder
from app.schemas.candles import Timeframe, INTERVAL_MS, normalize_symbol
from app.schemas.platform import Finite, Preferences
from app.schemas.simulation import SimulationOptions
from app.schemas.indicator_study import StudyRules


class SimulationConfig(SimulationOptions):
    symbol: str
    timeframe: Timeframe = "1h"
    strategy: Literal["ema", "evidence", "indicator"] = "ema"
    signal_profile: Literal["experimental", "men_crypto"] = "men_crypto"
    study_rules: StudyRules = Field(default_factory=StudyRules)
    analysis_settings: Preferences = Field(default_factory=Preferences)
    higher_timeframes: list[Timeframe] = Field(default_factory=list, max_length=3)
    lower_timeframe: Timeframe | None = None
    confirmation: Literal["candidate", "breakout_or_retest"] = "breakout_or_retest"
    signal_delay_bars: int = Field(default=1, ge=1, le=5)
    funding_mode: Literal["scenario", "historical"] = "scenario"
    fast: int = Field(default=20, ge=2, le=100)
    slow: int = Field(default=50, ge=3, le=300)
    atr_period: int = Field(default=14, ge=2, le=100)
    atr_stop: Finite = Field(default=1.5, gt=0, le=10)
    # Half of this scenario spread is charged on entry and half on exit.
    development_fraction: Finite = Field(default=0.6, ge=0.3, le=0.8)
    walk_forward_folds: int = Field(default=3, ge=2, le=6)
    _symbol = field_validator("symbol")(normalize_symbol)

    @model_validator(mode="after")
    def roles_valid(self) -> Self:
        if self.fast >= self.slow:
            raise ValueError("Require fast < slow.")
        if any(INTERVAL_MS[t] <= INTERVAL_MS[self.timeframe] for t in self.higher_timeframes):
            raise ValueError("Higher context must be above the primary timeframe.")
        if self.lower_timeframe and INTERVAL_MS[self.lower_timeframe] >= INTERVAL_MS[self.timeframe]:
            raise ValueError("Lower context must be below the primary timeframe.")
        return self


class BacktestRequest(SimulationConfig):
    start_time: int = Field(ge=0, le=4_102_444_800_000)
    end_time: int = Field(ge=0, le=4_102_444_800_000)
    data_source: Literal["live", "archive"] = "live"

    @model_validator(mode="after")
    def range_valid(self) -> Self:
        if self.end_time <= self.start_time:
            raise ValueError("Require start < end.")
        count = (self.end_time-self.start_time)//INTERVAL_MS[self.timeframe]+1
        if count > 20000 or count < max(400, self.slow*3):
            raise ValueError("Select at least max(400,3*slow) and at most 20,000 bars.")
        if self.data_source == "archive" and self.funding_mode == "historical":
            raise ValueError("Archive-only runs require explicit scenario funding; no local historical funding archive exists.")
        return self


def prepare(df: pd.DataFrame, config: BacktestRequest, contexts: dict[str, pd.DataFrame] | None = None,
            funding_events: list[dict] | None = None, cancel=None) -> pd.DataFrame:
    if config.strategy == "indicator":
        from app.backtesting.indicator import prepare_indicator
        return prepare_indicator(df, config, contexts)
    if config.strategy == "evidence":
        from app.backtesting.evidence import prepare_evidence
        return prepare_evidence(df, config, contexts or {}, funding_events or [], cancel)
    result = df.copy().reset_index(drop=True)
    fast = df.close.ewm(span=config.fast, adjust=False, min_periods=config.fast).mean()
    slow = df.close.ewm(span=config.slow, adjust=False, min_periods=config.slow).mean()
    result["signal"] = np.where((fast>slow)&(fast.shift()<=slow.shift()), 1,
                                np.where((fast<slow)&(fast.shift()>=slow.shift()), -1, 0))
    tr = pd.concat([df.high-df.low, (df.high-df.close.shift()).abs(), (df.low-df.close.shift()).abs()], axis=1).max(axis=1)
    result["atr"] = wilder(tr, config.atr_period)
    return result


def summarize_trades(trades: list[dict], curve: list[dict], initial: float,
                     blocked_by_daily_cap: int = 0) -> dict:
    results = np.array([t["net_pnl"] for t in trades])
    wins = int(np.sum(results>0)); count = len(results)
    gains, losses = float(results[results>0].sum()), float(-results[results<0].sum())
    equity = np.array([initial]+[point["equity"] for point in curve]); peaks = np.maximum.accumulate(equity)
    drawdown = (peaks-equity)/np.maximum(peaks, 1e-12)
    downside = np.minimum(np.diff(equity)/np.maximum(equity[:-1],1e-12),0)
    # Wilson interval describes win-rate sampling uncertainty, not expected future win rate.
    proportion = wins/count if count else 0; z=1.96
    center = (proportion+z*z/(2*count))/(1+z*z/count) if count else None
    half = z*math.sqrt(proportion*(1-proportion)/count+z*z/(4*count*count))/(1+z*z/count) if count else None
    return {"trades": count, "win_rate": wins/count if count else None, "profit_factor": gains/losses if losses else None,
            "average_winning_trade": float(results[results>0].mean()) if wins else None,
            "average_losing_trade": float(results[results<0].mean()) if np.any(results<0) else None,
            "average_realized_r": float(np.mean([t["realized_r"] for t in trades])) if trades and all("realized_r" in t for t in trades) else None,
            "profit_factor_note": "Undefined when no losing trades exist." if not losses else None,
            "expected_net_pnl_per_trade": float(results.mean()) if count else None,
            "net_pnl": float(results.sum()), "net_return_percent": float(results.sum())/initial*100,
            "max_drawdown_percent": float(drawdown.max())*100, "downside_deviation_percent": float(np.sqrt(np.mean(downside**2)))*100 if len(downside) else 0,
            "average_holding_bars": float(np.mean([t["holding_bars"] for t in trades])) if count else None,
            "win_rate_95_interval": [max(0,center-half),min(1,center+half)] if count else None,
            "total_fees": float(sum(t["fees"] for t in trades)), "total_funding": float(sum(t["funding"] for t in trades)),
            "total_spread_cost": float(sum(t.get("spread_cost", 0) for t in trades)),
            "total_slippage_cost": float(sum(t.get("slippage_cost", 0) for t in trades)),
            "risk_percent_used": None,
            "blocked_by_daily_loss_cap": blocked_by_daily_cap,
            "daily_loss_cap_percent": float(MAX_DAILY_LOSS_PERCENT),
            "sample_warning": "Small sample; estimates are unstable." if count < 100 else "Dependent observations limit statistical confidence.",
            "long": {"trades": sum(t["side"]=="LONG" for t in trades), "net_pnl": sum(t["net_pnl"] for t in trades if t["side"]=="LONG")},
            "short": {"trades": sum(t["side"]=="SHORT" for t in trades), "net_pnl": sum(t["net_pnl"] for t in trades if t["side"]=="SHORT")}}


def simulate(prepared: pd.DataFrame, config: SimulationConfig, start: int, end: int,
             funding_events: list[dict] | None = None, cancel=None, *, close_at_end: bool = True) -> dict:
    balance = config.equity; position = None; trades=[]; curve=[]; events_log=[]
    fee, slip = config.fee_bps/10000, config.slippage_bps/10000
    half_spread = config.spread_bps/20000
    # Spread and slippage are charged once per side. The entry price already
    # includes the adverse entry cost, so sizing must not charge it a second time.
    adverse = slip + half_spread
    duration = INTERVAL_MS[config.timeframe]; funding_interval=8*3600000
    day = None; day_start_equity = balance; daily_realized = 0.0; blocked = 0
    funding_times = [event["time"] for event in funding_events or []]
    for i in range(max(start,1),end):
        if cancel is not None and cancel.is_set():
            raise ValueError("Historical validation cancelled.")
        signal_index = i-config.signal_delay_bars
        bar, previous = prepared.iloc[i], prepared.iloc[max(0,signal_index)]
        bar_day = int(bar.time)//86_400_000
        if bar_day != day:
            day = bar_day; day_start_equity = balance; daily_realized = 0.0
        can_enter = daily_realized > -float(MAX_DAILY_LOSS_PERCENT)*day_start_equity/100
        # Do not import a development signal into the first held-out bar.
        evidence_ok = config.strategy != "evidence" or previous.evidence_score >= config.analysis_settings.candidate_score
        if position is None and signal_index >= start and i > start and evidence_ok and balance > 0 and previous.signal and np.isfinite(previous.atr) and previous.atr > 0:
            if not can_enter:
                blocked += 1
            else:
                direction = int(previous.signal); raw_entry = float(bar.open)
                entry = raw_entry*(1+direction*adverse)
                distance = float(previous.atr)*(config.study_rules.atr_stop_multiple if config.strategy == "indicator" else config.atr_stop)
                stop, target = entry-direction*distance, entry+direction*distance*config.reward_multiple
                if config.strategy == "evidence":
                    stop, target = float(previous.stop_reference), float(previous.target_reference)
                    distance = direction*(entry-stop)
                reward = direction*(target-entry)
                costs_at_stop = stop*adverse+(entry+stop)*fee
                costs_at_target = target*adverse+(entry+target)*fee
                net_rr = (reward-costs_at_target)/(distance+costs_at_stop) if distance+costs_at_stop > 0 else -1
                entry_valid = config.strategy == "ema" or net_rr >= float(RiskManager.minimum_net_reward_to_risk)
                if stop > 0 and target > 0 and distance > 0 and reward > 0 and entry_valid:
                    cost_per_unit = distance+stop*adverse+(entry+stop)*fee
                    quantity = float(RiskManager.size(equity=balance, risk_percent=config.risk_percent,
                                                     cost_per_unit=cost_per_unit, entry=entry,
                                                     fee_bps=config.fee_bps))
                    quantity = min(quantity, balance*config.max_exposure_percent/100/(entry*(1+fee)))
                    if quantity > 0:
                        entry_fee = quantity*entry*fee; balance -= entry_fee
                        position = {"direction": direction, "entry": entry, "stop": stop, "target": target, "quantity": quantity,
                                    "entry_index": i, "entry_time": int(bar.time), "entry_fee": entry_fee, "funding": 0.0,
                                    "signal_index": signal_index,
                                    "raw_entry": raw_entry, "initial_distance": distance,
                                    "initial_risk": quantity*cost_per_unit, "favorable": entry,
                                    "initial_stop": stop}
                        events_log.append({"kind":"virtual_entry", "known_at":int(bar.time)+duration-1,
                                           "side":"LONG" if direction==1 else "SHORT", "price":entry})
        if position:
            p=position; direction=p["direction"]; exit_price=None; reason=None
            if (bar.open <= p["stop"] if direction == 1 else bar.open >= p["stop"]):
                exit_price=float(bar.open); reason="gap through stop"
            elif (bar.low <= p["stop"] if direction == 1 else bar.high >= p["stop"]):
                exit_price=p["stop"]; reason="stop (adverse-first if both touched)"
            elif (bar.high >= p["target"] if direction == 1 else bar.low <= p["target"]):
                exit_price=p["target"]; reason="target"
            elif i-p["entry_index"]+1 >= config.max_holding_bars or (close_at_end and i == end-1):
                exit_price=float(bar.close); reason="holding limit" if i-p["entry_index"]+1 >= config.max_holding_bars else "window end"
            # Scheduled funding assumption at UTC 00/08/16. Intrabar exits are
            # conservatively charged for scheduled events in their exit bar too.
            events = (int(bar.time)+duration-1)//funding_interval-(int(bar.time)-1)//funding_interval
            if config.funding_mode == "historical":
                funding = sum(direction*p["quantity"]*event["mark_price"]*event["rate"]
                              for event in (funding_events or [])[bisect_left(funding_times,int(bar.time)):bisect_left(funding_times,int(bar.time)+duration)])
            else:
                funding = events*direction*p["quantity"]*float(bar.close)*config.funding_bps_per_8h/10000
            balance -= funding; p["funding"] += funding
            if exit_price is not None:
                fill = exit_price*(1-direction*adverse)
                gross = direction*p["quantity"]*(fill-p["entry"])
                exit_fee = p["quantity"]*fill*fee
                balance += gross-exit_fee
                traded_notional = p["quantity"]*(p["raw_entry"]+exit_price)
                net_pnl = gross-p["entry_fee"]-exit_fee-p["funding"]
                daily_realized += net_pnl
                trades.append({"side": "LONG" if direction==1 else "SHORT", "entry_time":p["entry_time"], "exit_time":int(bar.time)+duration-1,
                    "signal_time": int(prepared.time.iloc[p["signal_index"]])+duration-1, "entry":p["entry"], "exit":fill,
                    "stop":p["stop"], "target":p["target"], "quantity":p["quantity"], "gross_pnl":gross,
                    "fees":p["entry_fee"]+exit_fee, "funding":p["funding"],
                    "spread_cost": traded_notional*half_spread, "slippage_cost": traded_notional*slip,
                    "net_pnl":net_pnl, "holding_bars":i-p["entry_index"]+1, "reason":reason})
                trades[-1].update(initial_stop=p["initial_stop"], initial_risk=p["initial_risk"], realized_r=net_pnl/p["initial_risk"])
                events_log.append({"kind":"virtual_tp" if reason=="target" else "virtual_sl" if "stop" in reason else "virtual_exit",
                                   "known_at":int(bar.time)+duration-1, "side":"LONG" if direction==1 else "SHORT", "price":fill})
                position=None
            else:
                # Closed-bar adjustments become active only on the next bar.
                # Today's high cannot retroactively move a stop for today's low.
                p["favorable"] = max(p["favorable"],float(bar.high)) if direction==1 else min(p["favorable"],float(bar.low))
                gain = direction*(p["favorable"]-p["entry"])
                candidates = [p["stop"]]
                if config.break_even and gain >= config.break_even_at_r*p["initial_distance"]:
                    candidates.append(p["entry"])
                if config.trailing_stop and gain >= config.trailing_activation_r*p["initial_distance"] and np.isfinite(bar.atr):
                    candidates.append(p["favorable"]-direction*float(bar.atr)*config.trailing_atr)
                adjusted = max(candidates) if direction==1 else min(candidates)
                if adjusted != p["stop"]:
                    p["stop"] = adjusted
                    events_log.append({"kind":"virtual_stop_adjusted", "known_at":int(bar.time)+duration-1,
                                       "side":"LONG" if direction==1 else "SHORT", "price":adjusted})
        marked = balance+(position["direction"]*position["quantity"]*(float(bar.close)-position["entry"]) if position else 0)
        curve.append({"time":int(bar.time), "equity":marked})
    metrics = summarize_trades(trades, curve, config.equity, blocked)
    metrics["risk_percent_used"] = config.risk_percent
    open_position = None if position is None else {**position,"side":"LONG" if position["direction"]==1 else "SHORT","hypothetical_only":True}
    return {"metrics": metrics, "trades": trades, "equity_curve": curve, "open_position":open_position,
            "events":events_log, "hypothetical_only":True,
            "risk_rails": {"risk_percent": config.risk_percent,
                           "max_risk_percent": float(MAX_RISK_PERCENT),
                           "daily_loss_cap_percent": float(MAX_DAILY_LOSS_PERCENT),
                           "blocked_by_daily_loss_cap": blocked},
            "cost_assumptions": {"fee_bps": config.fee_bps, "slippage_bps": config.slippage_bps,
                                 "spread_bps": config.spread_bps, "half_spread_per_side": config.spread_bps/2}}


def selection_score(metrics: dict) -> float | None:
    """Risk-adjusted selection metric; tiny/empty samples cannot win."""
    if (metrics.get("trades") or 0) < 5:
        return None
    drawdown = max(metrics.get("max_drawdown_percent") or 0.0, 0.25)
    return (metrics.get("net_pnl") or 0.0)/drawdown


def run_validation(df: pd.DataFrame, config: BacktestRequest, contexts=None, funding_events=None, cancel=None) -> dict:
    if len(df) < max(400,3*config.slow): raise ValueError("Insufficient historical bars for validation.")
    if any(np.diff(df.time) != INTERVAL_MS[config.timeframe]): raise ValueError("Historical data contains gaps; backtest refused.")
    if config.funding_mode == "historical" and funding_events is None:
        raise ValueError("Historical funding requires verified funding data; no scenario fallback is allowed.")
    if funding_events is not None:
        previous = -1
        identities = set()
        for event in funding_events:
            identity = (event.get("time"),event.get("rate_type","Regular"))
            if (type(event.get("time")) is not int or event["time"] < previous or identity in identities
                    or not config.start_time <= event["time"] <= config.end_time
                    or not all(isinstance(event.get(k),(int,float)) and not isinstance(event.get(k),bool) and math.isfinite(event[k]) for k in ("rate","mark_price"))
                    or event["mark_price"]<=0 or abs(event["rate"])>1):
                raise ValueError("Historical funding events are invalid, duplicated or outside the requested range.")
            previous=event["time"]; identities.add(identity)
    split = int(len(df)*config.development_fraction)
    if split <= config.slow+10 or len(df)-split < config.walk_forward_folds*20:
        raise ValueError("Insufficient development or held-out sample.")
    prepared = prepare(df,config,contexts,funding_events,cancel)
    warmup = config.analysis_settings.indicators.trend_anchor if config.strategy in {"evidence", "indicator"} else config.slow
    if warmup >= split-10:
        raise ValueError("Development partition is too short for the selected indicator warm-up.")
    def evaluate(variant, left, right):
        return simulate(prepared,variant,left,right,funding_events,cancel)
    def variant(value):
        if config.strategy == "ema": return config.model_copy(update={"atr_stop":value})
        if config.strategy == "indicator": return config.model_copy(update={"reward_multiple":value})
        settings = config.analysis_settings.model_copy(update={"candidate_score":value})
        return config.model_copy(update={"analysis_settings":settings})
    parameter = "candidate_score" if config.strategy == "evidence" else "reward_multiple" if config.strategy == "indicator" else "atr_stop"
    requested = config.analysis_settings.candidate_score if config.strategy == "evidence" else config.reward_multiple if config.strategy == "indicator" else config.atr_stop
    variants = sorted(set([max(50,requested-10),requested,min(100,requested+10)])) if config.strategy == "evidence" else sorted(set([max(.25,requested*.75),requested,min(10,requested*1.25)]))
    development = evaluate(config,warmup,split)
    sensitivity=[]
    for value in variants:
        result=evaluate(variant(value),warmup,split)
        sensitivity.append({parameter:value,"parameter":parameter,"value":value,"development":result["metrics"],
                            "selection_score":selection_score(result["metrics"])})
    eligible = [item for item in sensitivity if item["selection_score"] is not None]
    selected = max(eligible,key=lambda item:item["selection_score"]) if eligible else next(item for item in sensitivity if item["value"] == requested)
    holdout = evaluate(variant(selected["value"]),split,len(df))
    folds=[]; boundaries=np.linspace(split,len(df),config.walk_forward_folds+1,dtype=int)
    for left,right in zip(boundaries,boundaries[1:]):
        training=[]
        for value in variants:
            metrics=evaluate(variant(value),warmup,int(left))["metrics"]
            training.append((selection_score(metrics),value))
        eligible_training = [item for item in training if item[0] is not None]
        chosen=max(eligible_training,key=lambda item:item[0])[1] if eligible_training else requested
        evaluated=evaluate(variant(chosen),int(left),int(right))
        folds.append({"train_end_time":int(df.time.iloc[left-1]),"test_start_time":int(df.time.iloc[left]),
                      "test_end_time":int(df.time.iloc[right-1]),"selected_atr_stop":variant(chosen).atr_stop,
                      "selected_parameter":parameter,"selected_value":chosen,
                      "selection_score":next(item[0] for item in training if item[1]==chosen),
                      "selection_status":"selected from training" if eligible_training else "insufficient training trades; requested parameter retained",
                      "metrics":evaluated["metrics"]})
    assumptions=[
        f"Closed-bar signals enter after {config.signal_delay_bars} bar(s) at the open, with modeled spread/slippage; no same-close fills.",
        f"One simulated position at a time; exposure capped at {config.max_exposure_percent}% of equity including entry fee, never above 1x notional.",
        "Break-even/trailing references update after a closed candle and activate on the next bar; stops never loosen. A stop at entry can still lose fees, slippage and funding.",
        "Half-spread and slippage are included once per fill; fees are charged per side.",
        "A 1% UTC daily realized-loss rail blocks new entries for the rest of that day.",
        "Stop is assumed first when both stop/target touch; stop gaps use the adverse open.",
        "Bar-close marked equity excludes intrabar drawdown and liquidation/maintenance tiers.",
        "Historical provider rates and mark prices are charged at actual observed funding timestamps." if config.funding_mode=="historical" else "Funding uses the requested per-eight-hour scenario at UTC 00/08/16 with candle close as mark proxy; zero excludes funding.",
        "Exit bars incur funding even if an intrabar exit might have preceded it; explicit conservative convention.",
        f"Only {parameter} is selected from development data; expanding walk-forward tests start flat.",
        "Today's exchange quantity filters are not assumed historically; quantities are continuous.",
        "Small samples and dependent trades limit statistical confidence. No result guarantees future performance.",
    ]
    trace=[]
    if config.strategy == "indicator":
        assumptions += [
            f"Shared indicator profile {config.signal_profile}: prepare_indicator() calls the chart's exact build_signals() function with the same settings and context data.",
            "Indicator arrows are evidence events, not fills; open positions and risk limits can suppress simulated entries.",
            "Indicator simulation entries require at least 1.5 modeled net reward/risk after spread, fees and slippage, matching the platform risk rail.",
            "Only fully closed higher candles and complete-session UTC VWAP can confirm the Men Crypto profile.",
        ]
        trace=[{"time":int(row.time),"known_at":int(row.time)+INTERVAL_MS[config.timeframe]-1,
                "signal":int(row.signal),"context_known_at":int(row.context_known_at)} for row in prepared.itertuples()]
    if config.strategy == "evidence":
        assumptions += [
            "The same assess() function, indicators, group weights and confirmed structure rules as reports are replayed with a historical clock.",
            "Historical bid/ask are explicitly modeled from candle close and requested spread; no historical order book is claimed.",
            "24-hour quote volume comes from complete trailing candle coverage; unavailable volume fails closed.",
            "Only higher/lower candles closed by the signal timestamp are available. Unknown historical open interest contributes no context vote.",
            "Evidence mode fixes stops/2R targets from the signal's report scenario; gaps that invert prices or fail net reward/risk cancel that simulated entry.",
            f"Confirmation rule: {config.confirmation}; all input parameters and optional context sources are retained.",
            "Indicator seeds use the loaded historical sample; a live report with a shorter sample can differ.",
        ]
        trace=[{"time":int(row.time),"known_at":int(row.time)+INTERVAL_MS[config.timeframe]-1,
                "classification":row.evidence_classification,"long_score":float(row.long_score),"short_score":float(row.short_score),
                "context_known_at":int(row.context_known_at),"confirmation_met":bool(row.confirmation_met)} for row in prepared.itertuples()]
    return {"strategy":"Shared indicator rules historical simulation" if config.strategy=="indicator" else "Report evidence engine historical replay" if config.strategy=="evidence" else "EMA crossover research baseline (separate from the discretionary evidence engine)",
            "parameters":config.model_dump(),"bars":len(df),"split_time":int(df.time.iloc[split]),
            "development":development,"out_of_sample":holdout,"holdout_parameters":{parameter:selected["value"]},
            "selection_status":"selected from development" if eligible else "insufficient development trades; requested parameter retained",
            "selection_metric":"net_pnl / max(max_drawdown_percent, 0.25), minimum 5 development trades",
            "sensitivity":sensitivity,"walk_forward":folds,"assessment_trace":trace,"assumptions":assumptions}
