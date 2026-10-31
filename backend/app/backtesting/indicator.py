"""The chart's exact confirmed signal rule, reused by historical simulation."""

import numpy as np
import pandas as pd
from typing import TYPE_CHECKING

from app.analysis.indicator_study import build_signals
from app.analysis.signal_profiles import attach_profile_context
from app.indicators.engine import calculate
from app.schemas.indicator_study import higher_context

if TYPE_CHECKING:
    from app.backtesting.engine import SimulationConfig


def prepare_indicator(data: pd.DataFrame, config: "SimulationConfig",
                      contexts: dict[str, pd.DataFrame] | None = None) -> pd.DataFrame:
    settings = config.analysis_settings.indicators
    df, _ = calculate(data, settings, config.timeframe)
    higher = config.higher_timeframes or higher_context(config.timeframe)
    if config.signal_profile == "men_crypto":
        if any(tf not in (contexts or {}) or contexts[tf].empty for tf in higher):
            raise ValueError("Men Crypto simulation requires historical candles for every higher confirmation timeframe.")
        df = attach_profile_context(df, config.timeframe, settings, contexts or {}, higher)
    study = build_signals(df, config.timeframe, settings, config.study_rules, config.signal_profile)
    markers = {signal["time"]: 1 if signal["side"] == "BUY" else -1 for signal in study["signals"]}
    df["signal"] = [markers.get(int(time), 0) for time in df.time]
    if "context_known_at" not in df:
        df["context_known_at"] = np.zeros(len(df), dtype=np.int64)
    return df
