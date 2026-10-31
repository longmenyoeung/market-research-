from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from app.schemas.candles import Timeframe, normalize_symbol, INTERVAL_MS
from app.schemas.platform import StrictModel, Finite
from app.schemas.simulation import SimulationOptions

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
    buy_rsi_min: Finite = Field(default=50, ge=0, le=100)
    buy_rsi_max: Finite = Field(default=70, ge=0, le=100)
    sell_rsi_min: Finite = Field(default=30, ge=0, le=100)
    sell_rsi_max: Finite = Field(default=50, ge=0, le=100)

    @model_validator(mode="after")
    def ordered_bands(self) -> Self:
        if self.buy_rsi_min >= self.buy_rsi_max or self.sell_rsi_min >= self.sell_rsi_max:
            raise ValueError("RSI lower bounds must be below upper bounds.")
        return self


class IndicatorStudyRequest(StrictModel):
    symbol: str
    timeframe: Timeframe = "1h"
    source: Literal["archive", "live"] = "archive"
    rules: StudyRules = Field(default_factory=StudyRules)
    profile: Literal["experimental", "men_crypto"] = "experimental"
    higher_timeframes: list[Timeframe] | None = Field(default=None, min_length=1, max_length=3)
    simulation: SimulationOptions = Field(default_factory=SimulationOptions)
    dashboard_alerts: bool = True

    _symbol = field_validator("symbol")(normalize_symbol)

    @model_validator(mode="after")
    def higher_roles(self) -> Self:
        if any(INTERVAL_MS[tf] <= INTERVAL_MS[self.timeframe] for tf in self.higher_timeframes or []):
            raise ValueError("Confirmation timeframes must be strictly above the chart timeframe.")
        if len(set(self.higher_timeframes or [])) != len(self.higher_timeframes or []):
            raise ValueError("Confirmation timeframes must be distinct.")
        return self
