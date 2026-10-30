from typing import Literal

from pydantic import Field, field_validator

from app.schemas.candles import Timeframe, normalize_symbol, INTERVAL_MS
from app.schemas.platform import StrictModel, Finite

SHORT_TERM_CONTEXT: dict[Timeframe, list[Timeframe]] = {
    "15m": ["1h", "4h"], "30m": ["1h", "4h"], "1h": ["4h"], "4h": ["1d"],
}


def higher_context(timeframe: Timeframe) -> list[Timeframe]:
    """Primary short-term views use explicit, strictly higher context windows."""
    if timeframe in SHORT_TERM_CONTEXT:
        return list(SHORT_TERM_CONTEXT[timeframe])
    return [tf for tf in INTERVAL_MS if INTERVAL_MS[tf] > INTERVAL_MS[timeframe]][:2]


class StudyRules(StrictModel):
    minimum_adx: Finite = Field(default=20, ge=5, le=60)
    minimum_relative_volume: Finite = Field(default=1, ge=0.1, le=5)
    atr_stop_multiple: Finite = Field(default=1.5, ge=0.5, le=5)
    signal_spacing_bars: int = Field(default=8, ge=1, le=100)


class IndicatorStudyRequest(StrictModel):
    symbol: str
    timeframe: Timeframe = "1h"
    source: Literal["archive", "live"] = "archive"
    rules: StudyRules = Field(default_factory=StudyRules)

    _symbol = field_validator("symbol")(normalize_symbol)
