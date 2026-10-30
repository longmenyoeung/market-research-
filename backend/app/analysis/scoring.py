from datetime import UTC, datetime

import numpy as np
import pandas as pd
from pydantic import ValidationError

from app.core.risk_limits import MIN_NET_REWARD_TO_RISK
from app.indicators.engine import finite
from app.schemas.platform import Preferences


def derivative_context(metric: dict, derivatives: dict | None) -> dict:
    """Summarize aggregate futures positioning without treating it as a vote.

    Open interest is aggregate and does not reveal which side holds it. The
    price/OI combination is therefore reported as weak context with no numeric
    weight; insufficient or implausible history fails closed to unavailable.
    """
    metric = metric if isinstance(metric, dict) else {}
    derivatives = derivatives if isinstance(derivatives, dict) else {}
    funding = finite(metric.get("funding_rate")) if isinstance(metric.get("funding_rate"), (int, float)) else None
    price_change = finite(metric.get("change_percent")) if isinstance(metric.get("change_percent"), (int, float)) else None
    result = {"available": False, "funding_rate": funding, "price_change_percent": price_change,
              "open_interest_change_percent": None, "reference_time": None, "latest_time": None,
              "span_hours": None, "scenario_effects": {}, "notice": "Open interest is aggregate positioning, not a directional vote."}
    history = (derivatives or {}).get("open_interest_history")
    if not isinstance(history, list) or len(history) < 2 or price_change is None:
        result["interpretation"] = "Open-interest direction is unavailable; no positioning inference is made."
        return result
    try:
        now_ms = int(datetime.now(UTC).timestamp()*1000)
        for point in history:
            if (not isinstance(point, dict) or type(point["time"]) is not int
                    or not 0 <= point["time"] <= now_ms+5000
                    or isinstance(point["value"], bool)
                    or not np.isfinite(float(point["value"])) or float(point["value"]) < 0):
                raise ValueError()
        latest = history[-1]
        if now_ms-latest["time"] > 2*3600*1000:
            raise ValueError()
        if any(first["time"] >= second["time"] for first, second in zip(history, history[1:])):
            raise ValueError()
        cutoff = latest["time"] - 24 * 3600 * 1000
        earlier = [point for point in history if point["time"] <= cutoff]
        reference = earlier[-1] if earlier else history[0]
        latest_value, reference_value = float(latest["value"]), float(reference["value"])
        span = latest["time"] - reference["time"]
        window = [point for point in history if point["time"] >= reference["time"]]
        if not (np.isfinite(latest_value) and np.isfinite(reference_value)) or latest_value < 0 or reference_value <= 0:
            raise ValueError()
        if span < 20 * 3600 * 1000 or len(window) < 20:
            raise ValueError()
        if any(second["time"] - first["time"] > 2 * 3600 * 1000 for first, second in zip(window, window[1:])):
            raise ValueError()
    except (KeyError, TypeError, ValueError, IndexError):
        result["interpretation"] = "Open-interest history is too short, malformed, or ambiguous; no positioning inference is made."
        return result
    change = (latest_value - reference_value) / reference_value * 100
    if not np.isfinite(change):
        result["interpretation"] = "Open-interest change is not finite; no positioning inference is made."
        return result
    result["span_hours"] = round(span / 3600000, 2)
    if abs(price_change) < 0.1 or abs(change) < 0.5:
        effects = {}
        interpretation = "Price or open-interest change is approximately flat; no positioning inference is drawn."
    elif price_change > 0 and change > 0:
        effects = {"LONG": "supporting", "SHORT": "conflicting"}
        interpretation = ("Price and open interest rose together: rising participation can support upside continuation, "
                          "while crowded positioning raises squeeze/liquidation risk.")
    elif price_change < 0 and change > 0:
        effects = {"LONG": "conflicting", "SHORT": "supporting"}
        interpretation = ("Price fell while open interest rose: new short participation may be building, which can support "
                          "downside continuation and raises short-squeeze risk.")
    elif price_change > 0 and change < 0:
        effects = {"LONG": "conflicting", "SHORT": "conflicting"}
        interpretation = ("Price rose while open interest fell: the move is more consistent with short covering than new "
                          "long participation and is weak continuation evidence for either side.")
    elif price_change < 0 and change < 0:
        effects = {"LONG": "conflicting", "SHORT": "conflicting"}
        interpretation = ("Price and open interest fell together: the move is more consistent with long liquidation than new "
                          "short participation and is weak continuation evidence for either side.")
    else:
        effects = {}
        interpretation = "Price or open-interest change is approximately flat; no positioning inference is drawn."
    result.update(available=True, open_interest_change_percent=round(change, 4),
                  reference_time=reference["time"], latest_time=latest["time"],
                  scenario_effects=effects, interpretation=interpretation)
    return result


def summarize(df: pd.DataFrame, structure: dict, preferences: Preferences) -> dict:
    if df.empty:
        return {"trend": "unknown", "momentum": "unknown", "structure": "unknown"}
    last = df.iloc[-1]; config = preferences.indicators
    fast, slow, anchor = (finite(last.get(f"ema_{p}")) for p in (config.trend_fast, config.trend_slow, config.trend_anchor))
    trend = "unknown" if any(v is None for v in (fast, slow, anchor)) else "bullish" if last.close > fast > slow > anchor else "bearish" if last.close < fast < slow < anchor else "neutral"
    rsi, hist = finite(last.get(f"rsi_{config.momentum_period}")), finite(last.get("macd_hist"))
    momentum = "unknown" if rsi is None or hist is None else "bullish" if rsi > 50 and hist > 0 else "bearish" if rsi < 50 and hist < 0 else "conflicting"
    adx = finite(last.get("adx")); percentile = finite(last.get("volatility_percentile"))
    regime = "unknown" if adx is None else "trending" if adx >= 25 else "ranging"
    if percentile is not None and percentile >= 80: regime += " / elevated volatility"
    if percentile is not None and percentile <= 20: regime += " / compression"
    recent = structure.get("events", [])
    last_event = recent[-1] if recent and recent[-1]["index"] >= len(df)-6 else None
    setups = ["trend"] if trend in ("bullish", "bearish") else []
    if last_event: setups.append("retest" if last_event["kind"] == "retest" else "breakout")
    if any(a["kind"] == "divergence" and a.get("points") and
           max(point["time"] for point in a["points"]) >= int(df.time.iloc[-1])-5*max(1, int(df.time.iloc[-1]-df.time.iloc[0])//max(1, len(df)-1))
           for a in structure.get("annotations", [])): setups.append("reversal")
    return {"trend": trend, "momentum": momentum, "structure": structure.get("trend", "unknown"),
            "rsi": rsi, "macd": momentum if hist is None else "bullish" if hist > 0 else "bearish" if hist < 0 else "neutral",
            "adx": adx, "volatility": finite(last.get("atr_percent")), "volatility_percentile": percentile,
            "regime": regime, "setups": setups, "last_event": last_event}


def assess(df: pd.DataFrame, structure: dict, preferences: Preferences, metric: dict,
           contexts: list[dict], limitations: list[str], required_valid: bool, timeframe: str,
           derivatives: dict | None = None, *, as_of_ms: int | None = None) -> dict:
    summary = summarize(df, structure, preferences)
    derivative = derivative_context(metric, derivatives)
    directions = ("bullish", "bearish")
    groups = {}
    groups["trend"] = {"direction": summary["trend"], "reason": "Close/fast EMA/slow EMA/anchor EMA ordering; other trend indicators are descriptive, not extra votes."}
    groups["structure"] = {"direction": summary["structure"], "reason": "Latest two confirmed highs and lows; mixed structure is neutral."}
    groups["momentum"] = {"direction": summary["momentum"], "reason": "RSI above/below 50 agrees with MACD histogram; disagreement earns neither direction points."}
    volume_direction = "unknown"
    if not df.empty:
        last = df.iloc[-1]; rv, cmf = finite(last.get("relative_volume")), finite(last.get("cmf"))
        if rv is not None and cmf is not None:
            volume_direction = "bullish" if rv >= 1 and cmf > 0 else "bearish" if rv >= 1 and cmf < 0 else "neutral"
    groups["volume"] = {"direction": volume_direction, "reason": "Relative volume >=1 plus Chaikin flow direction; volume indicators share one capped group."}
    higher = [c for c in contexts if c["role"] == "higher"]
    valid_higher = [c for c in higher if c.get("usable")]
    context_direction = "unknown"
    if higher and len(valid_higher) == len(higher):
        trends = {c["summary"]["trend"] for c in higher}
        context_direction = next(iter(trends)) if len(trends) == 1 else "conflicting"
    groups["context"] = {"direction": context_direction, "reason": "All selected usable higher timeframes must agree; lower momentum is reported independently."}
    weights = preferences.weights.model_dump(); total = sum(weights.values())
    scores = {direction: round(100*sum(weights[group] for group, result in groups.items() if result["direction"] == direction)/total, 1) for direction in directions}
    now_ms = as_of_ms if as_of_ms is not None else datetime.now(UTC).timestamp()*1000
    numeric_quotes = all(isinstance(metric.get(key), (int, float)) and not isinstance(metric.get(key), bool)
                         and np.isfinite(metric[key]) and metric[key] > 0 for key in ("last_price", "bid", "ask"))
    spread_valid = (isinstance(metric.get("spread_bps"), (int, float)) and not isinstance(metric.get("spread_bps"), bool)
                    and np.isfinite(metric["spread_bps"]) and metric["spread_bps"] >= 0)
    quote_valid = (numeric_quotes and metric["bid"] <= metric["ask"] and spread_valid
                   and type(metric.get("ticker_time")) is int and type(metric.get("book_time")) is int
                   and -5000 <= now_ms-metric["ticker_time"] <= 120000 and -5000 <= now_ms-metric["book_time"] <= 60000)
    reasons = list(limitations)
    if not required_valid: reasons.append("Required closed-candle data is stale, incomplete, or insufficient for warm-up.")
    if not quote_valid: reasons.append("Fresh valid ticker and best bid/ask data are required for a directional candidate.")
    if preferences.require_higher_alignment and timeframe != "1d" and (not higher or len(valid_higher) != len(higher)):
        required_valid = False; reasons.append("Required higher-timeframe data is unavailable or invalid.")
    liquidity_bad = metric.get("volume") is None or metric.get("volume", 0) < preferences.minimum_volume or metric.get("spread_bps") is None or metric.get("spread_bps", 0) > preferences.maximum_spread_bps
    if liquidity_bad: reasons.append("Liquidity/spread is outside configured planning limits; the market remains visible.")
    if abs(metric.get("funding_rate") or 0) >= preferences.funding_warning: reasons.append("Funding exceeds the configured adverse-carry warning threshold.")
    if summary.get("volatility") is not None and summary["volatility"] >= preferences.high_volatility_atr_percent: reasons.append("ATR percentage indicates elevated price movement and execution uncertainty.")
    scenarios = {}
    for side, direction, opposite in [("LONG", "bullish", "bearish"), ("SHORT", "bearish", "bullish")]:
        supporting = [f"{name}: {result['reason']}" for name, result in groups.items() if result["direction"] == direction]
        conflicting = [f"{name}: {result['direction']} — {result['reason']}" for name, result in groups.items() if result["direction"] in (opposite, "conflicting")]
        for context in contexts:
            if context.get("usable") and context["summary"].get("trend") == opposite:
                conflicting.append(f"{context['timeframe']} {context['role']} trend opposes this scenario.")
        derivative_effect = derivative["scenario_effects"].get(side)
        if derivative_effect == "supporting":
            supporting.append(f"Derivatives context (no score weight): {derivative['interpretation']}")
        elif derivative_effect == "conflicting":
            conflicting.append(f"Derivatives context (no score weight): {derivative['interpretation']}")
        scenario = {"side": side, "score": scores[direction], "thesis": f"Conditional {direction} continuation or confirmed structural reversal.",
                    "supporting": supporting, "conflicting": conflicting,
                    "confirmation": ["Wait for a closed-candle structure break/retest with aligned momentum.", "Recheck live spread, liquidity, volatility and higher timeframes before any manual decision."],
                    "cancel_conditions": ["Closed candle breaches the invalidation reference.", "Required data becomes stale or unavailable.", "Liquidity deteriorates or higher-timeframe alignment reverses."],
                    "entry_region": None, "invalidation": None, "stop": None, "targets": [], "gross_rr": None, "net_rr": None,
                    "risk": None, "limitations": list(reasons)}
        atr = finite(df.atr.iloc[-1]) if not df.empty and "atr" in df else None
        if required_valid and quote_valid and atr and atr > 0:
            entry = metric["ask"] if side == "LONG" else metric["bid"]
            level = structure.get("support" if side == "LONG" else "resistance")
            stop = min(entry-atr*1.5, level-atr*0.25) if side == "LONG" and level is not None else max(entry+atr*1.5, level+atr*0.25) if side == "SHORT" and level is not None else entry+(-1 if side == "LONG" else 1)*atr*1.5
            risk_distance = abs(entry-stop); sign = 1 if side == "LONG" else -1
            targets = [entry+sign*risk_distance*r for r in (1, 2, 3)]
            obstacle = structure.get("resistance" if side == "LONG" else "support")
            if obstacle and (obstacle-entry)*sign > 0:
                scenario["confirmation"].append(f"Opposing structure at {obstacle:.8g} can interrupt the proposed targets.")
            if stop > 0 and all(t > 0 for t in targets):
                from app.analysis.risk import TradePlan, plan_trade
                # This illustrative report plan always discloses its hypothetical equity.
                try:
                    plan = TradePlan(side=side, equity=1000, risk_percent=preferences.risk_percent,
                                     entry=f"{entry:.18f}", stop=f"{stop:.18f}", target=f"{targets[1]:.18f}",
                                     fee_bps=preferences.fee_bps, slippage_bps=preferences.slippage_bps,
                                     funding_rate=f"{abs(metric.get('funding_rate') or 0):.18f}",
                                     funding_periods=1 if metric.get("funding_rate") is not None else 0)
                except ValidationError:
                    required_valid = False
                    reason = "Scenario prices exceed the planner's supported precision or numeric bounds."
                    reasons.append(reason)
                    scenario["limitations"].append(reason)
                    scenarios[side] = scenario
                    continue
                risk = plan_trade(plan)
                net_rr = risk["net_reward_to_risk"]
                scenario.update(entry_region=[max(1e-15, entry-atr*0.1), entry+atr*0.1], entry=entry, stop=stop, invalidation=stop,
                                targets=targets, gross_rr=2.0, net_rr=float(net_rr) if net_rr is not None else None,
                                risk={"hypothetical_equity": 1000, "leverage": 1, **risk})
                scenario["limitations"].append("Targets are hypothetical 1R/2R/3R projections, not observed future prices. Report risk uses $1,000 hypothetical equity and 1x leverage.")
                if metric.get("funding_rate") is None: scenario["limitations"].append("Funding unavailable; zero in this illustration excludes unknown carry cost.")
        scenarios[side] = scenario
    classification = "WAIT"
    if not required_valid or not quote_valid or liquidity_bad:
        classification = "NO TRADE"
    else:
        for side, direction in [("LONG", "bullish"), ("SHORT", "bearish")]:
            context_ok = not preferences.require_higher_alignment or timeframe == "1d" or context_direction == direction
            scenario = scenarios[side]
            if scores[direction] >= preferences.candidate_score and scores[direction] > scores[directions[1-directions.index(direction)]] and context_ok and scenario["net_rr"] is not None and scenario["net_rr"] >= float(MIN_NET_REWARD_TO_RISK):
                classification = f"{side} candidate"
    return {"classification": classification, "long_score": scores["bullish"], "short_score": scores["bearish"],
            "summary": summary, "evidence_groups": {k: {**v, "weight": weights[k]} for k,v in groups.items()},
            "scenarios": scenarios, "reasons_to_avoid": reasons, "derivative_context": derivative,
            "required_confirmation": ["Observe a closed-candle confirmation and re-evaluate all data at the time of any manual decision."],
            "final_assessment": f"{classification}: experimental grouped evidence; unresolved conflicts, costs and data gates are retained.",
            "score_notice": "Evidence scores are experimental weighted alignment measures, not win probabilities or proof of profitability."}
