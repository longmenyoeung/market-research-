"""Experimental, causal chart studies. Past labels are not current trade advice."""

import math

import numpy as np
import pandas as pd

from app.adapters.base import MarketDataError
from app.schemas.candles import INTERVAL_MS
from app.schemas.indicator_study import StudyRules
from app.schemas.platform import IndicatorSettings


def build_signals(df: pd.DataFrame, timeframe: str, config: IndicatorSettings,
                  rules: StudyRules, profile: str = "experimental") -> dict:
    """Evaluate each closed bar using only indicator values available at that bar.

    Signal spacing is a display filter, not a modeled holding period. No fills,
    exits, strategy returns, or probabilities are inferred from these markers.
    """
    duration = INTERVAL_MS[timeframe]
    if len(df) > 1 and not np.all(np.diff(df.time.to_numpy()) == duration):
        raise MarketDataError("invalid_study_data", "Indicator study requires continuous, ordered candles.", 422)
    names = [f"ema_{period}" for period in (config.trend_fast, config.trend_slow, config.trend_anchor)]
    required = [*names, f"rsi_{config.momentum_period}", "macd_hist", "adx", "atr", "relative_volume"]
    if profile == "men_crypto":
        required.append("vwap")
    signals = []
    prior_side = None
    last_signal_index = -rules.signal_spacing_bars
    evaluation = {"BUY": {}, "SELL": {}}
    unavailable = []
    states = []
    for i, row in df.iterrows():
        unavailable = [name for name in required if not math.isfinite(float(row.get(name, math.nan)))]
        if unavailable:
            prior_side = None
            evaluation = {"BUY": {}, "SELL": {}}
            states.append({"time":int(row.time),"known_at":int(row.time)+duration-1,"side":"WAIT","ready":False,"trend_bias":0,"weak":True})
            continue
        fast, slow, anchor = [float(row[name]) for name in names]
        rsi, hist = float(row[f"rsi_{config.momentum_period}"]), float(row.macd_hist)
        evaluation = {
            "BUY": {"trend": row.close > fast > slow > anchor,
                    "momentum": rules.buy_rsi_min < rsi < rules.buy_rsi_max and hist > 0,
                    "strength": row.adx >= rules.minimum_adx,
                    "volume": row.relative_volume >= rules.minimum_relative_volume},
            "SELL": {"trend": row.close < fast < slow < anchor,
                     "momentum": rules.sell_rsi_min < rsi < rules.sell_rsi_max and hist < 0,
                     "strength": row.adx >= rules.minimum_adx,
                     "volume": row.relative_volume >= rules.minimum_relative_volume},
        }
        if profile == "men_crypto":
            complete_vwap = bool(row.get("vwap_complete_session", False))
            evaluation["BUY"]["trend"] = bool(row.close > slow > anchor and complete_vwap and row.close > row.vwap)
            evaluation["SELL"]["trend"] = bool(row.close < slow < anchor and complete_vwap and row.close < row.vwap)
            evaluation["BUY"]["higher_context"] = bool(row.get("higher_buy", False))
            evaluation["SELL"]["higher_context"] = bool(row.get("higher_sell", False))
        side = next((side for side, groups in evaluation.items() if all(groups.values())), None)
        states.append({"time":int(row.time),"known_at":int(row.time)+duration-1,"side":side or "WAIT","ready":True,
                       "trend_bias":1 if slow>anchor else -1 if slow<anchor else 0,
                       "weak":bool(row.adx<rules.minimum_adx or row.relative_volume<rules.minimum_relative_volume)})
        if side and side != prior_side and i-last_signal_index >= rules.signal_spacing_bars and row.atr > 0:
            entry = float(row.close)
            risk = float(row.atr)*rules.atr_stop_multiple
            direction = 1 if side == "BUY" else -1
            stop = entry-direction*risk
            targets = [entry+direction*risk*r for r in (1, 2, 3)]
            # Logically valid OHLCV can still imply impossible positive-price levels.
            if min(stop, *targets) > 0:
                timestamp = int(row.time)
                signals.append({
                    "id": f"{side}-{timestamp}", "side": side, "time": timestamp,
                    "known_at": timestamp+duration-1, "timeframe": timeframe,
                    "reference": entry, "stop": stop, "targets": targets,
                    "atr": float(row.atr), "rsi": rsi, "adx": float(row.adx),
                    "relative_volume": float(row.relative_volume),
                    "evidence": {key: bool(value) for key, value in evaluation[side].items()},
                    "rule": "Closed-bar transition into aligned EMA trend, RSI/MACD momentum, ADX strength, and relative-volume conditions.",
                    "level_rule": f"Observed signal close reference; stop={rules.atr_stop_multiple} ATR away; hypothetical targets at 1R/2R/3R before costs. No fill is assumed.",
                })
                signals[-1]["profile"] = profile
                if profile == "men_crypto":
                    signals[-1]["rule"] = "Men Crypto: confirmed close beyond EMA slow/anchor and complete-session UTC VWAP; RSI/MACD, ADX, relative volume and all confirmed higher trends agree."
                last_signal_index = i
        prior_side = side
    groups = {
        "trend": f"Close and EMA {config.trend_fast}/{config.trend_slow}/{config.trend_anchor} are ordered in one direction.",
        "momentum": f"RSI {config.momentum_period} in ({rules.buy_rsi_min},{rules.buy_rsi_max}) with positive MACD histogram for BUY; ({rules.sell_rsi_min},{rules.sell_rsi_max}) with negative histogram for SELL. One combined group.",
        "strength": f"ADX >= {rules.minimum_adx}; ADX measures strength and has no direction by itself.",
        "volume": f"Candle volume / {config.volume_period}-bar volume mean >= {rules.minimum_relative_volume}.",
    }
    if profile == "men_crypto":
        groups["trend"] = f"Close > EMA {config.trend_slow} > EMA {config.trend_anchor} and above complete-session UTC VWAP for BUY; inverse for SELL. One price/trend group."
        groups["higher_context"] = "Every configured higher timeframe has a confirmed, nonstale close and aligned EMA slow/anchor trend. No present-day summary is copied backward."
    return {
        "profile": profile,
        "signals": signals,
        "states": states,
        "evaluation": {side: {key: bool(value) for key, value in groups.items()} for side, groups in evaluation.items()},
        "unavailable": unavailable,
        "rules": rules.model_dump(),
        "evidence_groups": groups,
        "limitations": [
            "Experimental rules have not demonstrated profitability. Evidence is not a win probability.",
            "Markers are known only at candle close; their reference price is not a guaranteed executable entry.",
            "Signal markers exclude historical spread, funding and fees; target lines are gross hypothetical references.",
            "Spacing suppresses repeated labels; it does not simulate positions or exits.",
            "Structure pivots require right-hand confirmation; drawings disclose their confirmation time.",
        ],
    }
