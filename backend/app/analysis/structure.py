from datetime import UTC, datetime, timedelta
import math

import numpy as np
import pandas as pd

from app.schemas.candles import INTERVAL_MS


def analyze_structure(df: pd.DataFrame, timeframe: str, window: int = 3, daily: pd.DataFrame | None = None,
                      momentum_period: int = 14) -> dict:
    annotations: list[dict] = []
    swings: list[dict] = []
    events: list[dict] = []
    duration = INTERVAL_MS[timeframe]
    if len(df) < 2*window+1:
        return {"trend": "unknown", "swings": [], "annotations": [], "events": [], "support": None, "resistance": None,
                "limitations": ["Insufficient closed candles for confirmed swings."]}
    df = df.reset_index(drop=True)
    atr = float(df.atr.iloc[-1]) if "atr" in df and np.isfinite(df.atr.iloc[-1]) else float((df.high-df.low).tail(14).mean())
    last_time = int(df.time.iloc[-1])

    def annotation(kind: str, label: str, points: list[dict], rule: str, confirmed: bool = True,
                   confirmed_at: int | None = None, **extra) -> dict:
        value = {"id": f"{kind}-{len(annotations)}", "kind": kind, "label": label, "timeframe": timeframe,
                 "points": points, "rule": rule, "confirmed": confirmed,
                 "confirmed_at": confirmed_at or last_time+duration-1, **extra}
        annotations.append(value)
        return value

    previous = {}
    for i in range(window, len(df)-window):
        for kind, column, comparison in [("high", "high", np.max), ("low", "low", np.min)]:
            values = df[column].iloc[i-window:i+window+1].to_numpy()
            price = float(df[column].iloc[i])
            if price != comparison(values) or np.sum(values == price) != 1:
                continue
            prior = previous.get(kind)
            if prior is None:
                label = kind.upper()
            elif price == prior["price"]:
                label = "EH" if kind == "high" else "EL"
            elif kind == "high":
                label = "HH" if price > prior["price"] else "LH"
            else:
                label = "HL" if price > prior["price"] else "LL"
            swing = {"kind": kind, "label": label, "index": i, "time": int(df.time.iloc[i]), "price": price,
                     "confirmed_index": i+window, "confirmed_at": int(df.time.iloc[i+window])+duration-1}
            swings.append(swing); previous[kind] = swing
            annotation("swing", label, [{"time": swing["time"], "price": price}],
                       f"Unique {kind} in {2*window+1} bars; known only after {window} right bars close.", confirmed_at=swing["confirmed_at"])
    highs, lows = [s for s in swings if s["kind"] == "high"], [s for s in swings if s["kind"] == "low"]
    trend = "neutral"
    if len(highs) >= 2 and len(lows) >= 2:
        trend = "bullish" if highs[-1]["price"] > highs[-2]["price"] and lows[-1]["price"] > lows[-2]["price"] else "bearish" if highs[-1]["price"] < highs[-2]["price"] and lows[-1]["price"] < lows[-2]["price"] else "neutral"
    active = {}; broken = set(); bias = 0; pending: list[dict] = []
    for i in range(len(df)):
        for s in swings:
            if s["confirmed_index"] == i-1:
                active[s["kind"]] = s
        close, high, low = float(df.close.iloc[i]), float(df.high.iloc[i]), float(df.low.iloc[i])
        time = int(df.time.iloc[i])
        for kind, direction in [("high", 1), ("low", -1)]:
            s = active.get(kind)
            if s is None or (kind, s["index"]) in broken:
                continue
            beyond = close > s["price"] if direction == 1 else close < s["price"]
            if beyond:
                label = "CHoCH" if bias and bias != direction else "BOS"
                item = {"kind": label, "direction": "bullish" if direction == 1 else "bearish", "time": time,
                        "level": s["price"], "index": i, "confirmed": True}
                events.append(item); pending.append(item.copy()); broken.add((kind, s["index"])); bias = direction
                annotation("structure", f"{label} {item['direction']}", [{"time": s["time"], "price": s["price"]}, {"time": time, "price": s["price"]}],
                           "Closed candle crosses an already confirmed swing; CHoCH reverses the preceding break direction.", confirmed_at=time+duration-1)
            elif (high > s["price"] and close < s["price"]) if direction == 1 else (low < s["price"] and close > s["price"]):
                annotation("sweep", "Potential liquidity sweep", [{"time": time, "price": s["price"]}],
                           "Wick crosses prior confirmed swing and closes back inside; liquidity intent is unverified.", False, time+duration-1)
        for item in pending[:]:
            if i <= item["index"]:
                continue
            if i-item["index"] > 10:
                pending.remove(item); continue
            # Use contemporaneous ATR, never final ATR for historical retest thresholds.
            current_atr = float(df.atr.iloc[i]) if "atr" in df and np.isfinite(df.atr.iloc[i]) else high-low
            tolerance = current_atr*0.25
            holds = close > item["level"] if item["direction"] == "bullish" else close < item["level"]
            if low <= item["level"]+tolerance and high >= item["level"]-tolerance and holds:
                events.append({**item, "kind": "retest", "time": time, "index": i})
                annotation("retest", "Observed retest", [{"time": time, "price": item["level"]}],
                           "Within 10 bars after break, range revisits level +/-0.25 contemporaneous ATR and closes on breakout side.", confirmed_at=time+duration-1)
                pending.remove(item)
        if i >= 2:
            upper, lower, direction = (low, float(df.high.iloc[i-2]), "bullish") if low > df.high.iloc[i-2] else (float(df.low.iloc[i-2]), high, "bearish")
            if upper > lower:
                # Fill status is reported "as of the latest closed bar" after the
                # loop. It must never be folded back into the event as if known then.
                annotation("fvg", f"{direction} FVG",
                           [{"time": int(df.time.iloc[i-2]), "price": lower}, {"time": time, "price": upper}],
                           "Three-bar non-overlap: low[t]>high[t-2] or high[t]<low[t-2]. Not a prediction of a fill.",
                           confirmed_at=time+duration-1, filled=False, fill_checked_until=last_time,
                           filled_as_of_latest_bar=False, filled_at=None, created_index=i,
                           zone_lower=lower, zone_upper=upper, direction=direction)
    for a in annotations:
        if a["kind"] != "fvg":
            continue
        later = df.iloc[a["created_index"]+1:]
        hit = later[later.low <= a["zone_lower"]] if a["direction"] == "bullish" else later[later.high >= a["zone_upper"]]
        a["filled_as_of_latest_bar"] = not hit.empty
        a["filled_at"] = int(hit.time.iloc[0])+duration-1 if not hit.empty else None
        a["label"] = f"{a['direction']} FVG ({'filled' if not hit.empty else 'unfilled'} as of latest bar)"
    close = float(df.close.iloc[-1])
    levels = sorted(set(s["price"] for s in swings[-40:]))
    support = max((v for v in levels if v < close), default=None)
    resistance = min((v for v in levels if v > close), default=None)
    for label, level in [("Support", support), ("Resistance", resistance)]:
        if level is not None:
            annotation("zone", label, [{"time": int(df.time.iloc[max(0, len(df)-80)]), "price": level-atr*0.25}, {"time": last_time, "price": level+atr*0.25}],
                       "Nearest confirmed swing below/above latest close; zone half-width=0.25 current ATR.")
    for kind, items in [("high", highs), ("low", lows)]:
        if len(items) >= 2:
            a, b = items[-2:]
            annotation("trendline", f"{kind.title()} trendline", [{"time": a["time"], "price": a["price"]}, {"time": b["time"], "price": b["price"]}],
                       "Line joining the two most recent confirmed swings of the same type.")
    ordered = sorted(swings, key=lambda s: s["time"])
    if len(ordered) >= 2:
        b = ordered[-1]; a = next((s for s in reversed(ordered[:-1]) if s["kind"] != b["kind"]), None)
        if a:
            for ratio in [0.236, 0.382, 0.5, 0.618, 0.786, 1.272, 1.618]:
                price = b["price"]-(b["price"]-a["price"])*ratio if ratio < 1 else a["price"]+(b["price"]-a["price"])*ratio
                if price > 0:
                    annotation("fibonacci", f"Fib {ratio}", [{"time": a["time"], "price": price}, {"time": last_time, "price": price}],
                               "Retracement from latest opposite confirmed swing leg; ratios above 1 are extensions.")
    for items, bullish in [(lows, True), (highs, False)]:
        rsi_name = (f"rsi_{momentum_period}" if f"rsi_{momentum_period}" in df
                    else next((name for name in df if name.startswith("rsi_")), None))
        if len(items) >= 2 and rsi_name:
            a, b = items[-2:]; ra, rb = df[rsi_name].iloc[a["index"]], df[rsi_name].iloc[b["index"]]
            divergent = b["price"] < a["price"] and rb > ra if bullish else b["price"] > a["price"] and rb < ra
            if np.isfinite(ra) and np.isfinite(rb) and divergent:
                annotation("divergence", f"{'Bullish' if bullish else 'Bearish'} RSI divergence",
                           [{"time": a["time"], "price": a["price"]}, {"time": b["time"], "price": b["price"]}],
                           f"Two confirmed price pivots diverge from {rsi_name}; reversal remains provisional.", False)
    if len(df) >= 20:
        recent = df.tail(20); x = np.arange(20); slope, intercept = np.polyfit(x, recent.close, 1)
        upper_offset = max(recent.high-(intercept+slope*x)); lower_offset = min(recent.low-(intercept+slope*x))
        for label, offset in [("Channel high", upper_offset), ("Channel low", lower_offset)]:
            annotation("channel", label, [{"time": int(recent.time.iloc[0]), "price": float(intercept+offset)},
                       {"time": last_time, "price": float(intercept+19*slope+offset)}], "20-bar OLS channel spanning extreme high/low residuals.")
    last, before = df.iloc[-1], df.iloc[-2]
    body, span = abs(last.close-last.open), last.high-last.low
    patterns = []
    if span > 0 and body <= span*0.1: patterns.append("doji")
    if last.close > last.open and before.close < before.open and last.open <= before.close and last.close >= before.open: patterns.append("bullish engulfing")
    if last.close < last.open and before.close > before.open and last.open >= before.close and last.close <= before.open: patterns.append("bearish engulfing")
    if body > 0 and min(last.open,last.close)-last.low >= 2*body and last.high-max(last.open,last.close) <= body: patterns.append("hammer shape")
    for pattern in patterns:
        annotation("pattern", pattern, [{"time": last_time, "price": float(last.close)}], "Doji: body <=10% range; engulfing: opposite prior body enclosed; hammer: lower shadow >=2 bodies, upper <=1.")
    limitations = ["Swing pivots require right-hand confirmation; annotations disclose when known.",
                   "FVG, sweeps, divergence and zones describe observations, not guaranteed future prices.",
                   "True volume profile unavailable: candle OHLCV does not supply volume traded at each price."]
    if daily is not None and not daily.empty:
        asof = datetime.fromtimestamp((last_time+duration)/1000, UTC)
        day_start = asof.replace(hour=0, minute=0, second=0, microsecond=0)
        prev_day = int((day_start-timedelta(days=1)).timestamp()*1000)
        prior = daily[daily.time == prev_day]
        if not prior.empty:
            d = prior.iloc[-1]; pivot = (d.high+d.low+d.close)/3
            for label, price in [("Previous day high", d.high), ("Previous day low", d.low), ("Pivot P", pivot), ("Pivot R1", 2*pivot-d.low), ("Pivot S1", 2*pivot-d.high), ("Pivot R2", pivot+d.high-d.low), ("Pivot S2", pivot-d.high+d.low)]:
                if price > 0:
                    a = annotation("previous_period", label, [{"time": last_time, "price": float(price)}], "Previous complete UTC daily OHLC; classic floor-pivot formulas.")
                    a["timeframe"] = "1d"
        week_start = day_start-timedelta(days=day_start.weekday())
        week = daily[(daily.time >= int((week_start-timedelta(days=7)).timestamp()*1000)) & (daily.time < int(week_start.timestamp()*1000))]
        if len(week) == 7:
            for label, price in [("Previous week high", week.high.max()), ("Previous week low", week.low.min())]:
                a = annotation("previous_period", label, [{"time": last_time, "price": float(price)}], "High/low of seven complete daily bars in the previous Monday–Sunday UTC week.")
                a["timeframe"] = "1d"
        else: limitations.append("Previous complete week unavailable in the daily sample.")
    else: limitations.append("Previous day/week and floor pivots unavailable without daily data.")
    return {"trend": trend, "swings": swings[-60:], "annotations": annotations, "events": events[-20:],
            "support": support, "resistance": resistance, "patterns": patterns, "limitations": limitations}
