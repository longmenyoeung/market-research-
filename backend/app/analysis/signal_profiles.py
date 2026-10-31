"""Shared, causal Men Crypto profile inputs for charts and simulation."""

from bisect import bisect_right

import numpy as np
import pandas as pd

from app.schemas.candles import INTERVAL_MS
from app.schemas.platform import IndicatorSettings


def attach_profile_context(df: pd.DataFrame, timeframe: str, config: IndicatorSettings,
                           contexts: dict[str, pd.DataFrame], higher_timeframes: list[str]) -> pd.DataFrame:
    """Join only fully closed, nonstale higher EMA observations to each close.

    Caller supplies historical raw OHLCV frames. No present-day trend summary
    may be copied backward over past signals.
    """
    result = df.copy()
    bullish = np.ones(len(df), dtype=bool) if higher_timeframes else np.zeros(len(df), dtype=bool)
    bearish = bullish.copy()
    latest_known = np.zeros(len(df), dtype=np.int64)
    ready = np.full(len(df), bool(higher_timeframes), dtype=bool)
    asof = df.time.to_numpy(dtype=np.int64)+INTERVAL_MS[timeframe]-1
    for tf in higher_timeframes:
        raw = contexts.get(tf)
        if raw is None or raw.empty:
            bullish[:] = False; bearish[:] = False; ready[:] = False
            continue
        duration = INTERVAL_MS[tf]
        if duration <= INTERVAL_MS[timeframe] or np.any(np.diff(raw.time) != duration):
            raise ValueError(f"{tf} confirmation must be continuous and strictly higher than the chart.")
        close = raw.close.astype(float)
        slow = close.ewm(span=config.trend_slow, adjust=False, min_periods=config.trend_slow).mean()
        anchor = close.ewm(span=config.trend_anchor, adjust=False, min_periods=config.trend_anchor).mean()
        closes = (raw.time+duration-1).tolist()
        for i, timestamp in enumerate(asof):
            j = bisect_right(closes, int(timestamp))-1
            valid = j >= 0 and timestamp-closes[j] < duration and np.isfinite(slow.iloc[j]) and np.isfinite(anchor.iloc[j])
            ready[i] &= valid
            bullish[i] &= bool(valid and close.iloc[j] > slow.iloc[j] > anchor.iloc[j])
            bearish[i] &= bool(valid and close.iloc[j] < slow.iloc[j] < anchor.iloc[j])
            if valid:
                latest_known[i] = max(latest_known[i], closes[j])
    # A partial first loaded UTC day cannot establish a complete session VWAP.
    day = result.time//86_400_000
    first = result.time.groupby(day).transform("min")
    result["vwap_complete_session"] = first.to_numpy() == (day*86_400_000).to_numpy()
    result["higher_buy"] = bullish
    result["higher_sell"] = bearish
    result["higher_ready"] = ready
    result["context_known_at"] = latest_known
    return result
