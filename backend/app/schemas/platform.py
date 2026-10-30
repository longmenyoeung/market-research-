from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.risk_limits import DEFAULT_RISK_PERCENT, MAX_RISK_PERCENT
from app.schemas.candles import Timeframe, normalize_symbol

Finite = Annotated[float, Field(allow_inf_nan=False)]
Period = Annotated[int, Field(ge=2, le=300)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_default=True)


class IndicatorSettings(StrictModel):
    ema_periods: list[Period] = Field(default=[5, 9, 20, 21, 50, 100, 200], min_length=1, max_length=12)
    sma_periods: list[Period] = Field(default=[20, 50, 100, 200], min_length=1, max_length=12)
    rsi_periods: list[Period] = Field(default=[7, 14, 21], min_length=1, max_length=8)
    trend_fast: Period = 20
    trend_slow: Period = 50
    trend_anchor: Period = 200
    momentum_period: Period = 14
    macd_fast: Period = 12
    macd_slow: Period = 26
    macd_signal: Period = 9
    adx_period: Period = 14
    atr_period: Period = 14
    band_period: Period = 20
    band_multiplier: Finite = Field(default=2, gt=0, le=6)
    channel_period: Period = 20
    supertrend_multiplier: Finite = Field(default=3, gt=0, le=10)
    volume_period: Period = 20
    volatility_period: Period = 30
    percentile_period: Period = 100
    regression_period: Period = 20
    stochastic_smooth: Period = 3
    awesome_fast: Period = 5
    awesome_slow: Period = 34
    ichimoku_fast: Period = 9
    ichimoku_base: Period = 26
    ichimoku_span: Period = 52
    sar_step: Finite = Field(default=0.02, gt=0, le=0.1)
    sar_max: Finite = Field(default=0.2, gt=0, le=1)
    swing_window: int = Field(default=3, ge=1, le=10)

    @model_validator(mode="after")
    def order_periods(self) -> Self:
        if not self.trend_fast < self.trend_slow <= self.trend_anchor:
            raise ValueError("Trend periods must be fast < slow <= anchor.")
        if self.macd_fast >= self.macd_slow or self.sar_step > self.sar_max:
            raise ValueError("MACD fast must be below slow; SAR step must not exceed maximum.")
        if self.awesome_fast >= self.awesome_slow:
            raise ValueError("Awesome Oscillator fast period must be below slow.")
        if not self.ichimoku_fast < self.ichimoku_base < self.ichimoku_span:
            raise ValueError("Ichimoku periods must increase.")
        return self


class EvidenceWeights(StrictModel):
    trend: Finite = Field(default=25, ge=0, le=100)
    structure: Finite = Field(default=25, ge=0, le=100)
    momentum: Finite = Field(default=20, ge=0, le=100)
    volume: Finite = Field(default=10, ge=0, le=100)
    context: Finite = Field(default=20, ge=0, le=100)

    @model_validator(mode="after")
    def positive_total(self) -> Self:
        if sum(self.model_dump().values()) <= 0:
            raise ValueError("At least one evidence weight must be positive.")
        return self


class Preferences(StrictModel):
    indicators: IndicatorSettings = Field(default_factory=IndicatorSettings)
    weights: EvidenceWeights = Field(default_factory=EvidenceWeights)
    candidate_score: Finite = Field(default=65, ge=50, le=100)
    minimum_volume: Finite = Field(default=5_000_000, ge=0)
    maximum_spread_bps: Finite = Field(default=10, gt=0, le=1000)
    high_volatility_atr_percent: Finite = Field(default=3, gt=0, le=100)
    funding_warning: Finite = Field(default=0.001, gt=0, le=0.1)
    fee_bps: Finite = Field(default=5, ge=0, le=100)
    slippage_bps: Finite = Field(default=5, ge=0, le=1000)
    risk_percent: Finite = Field(default=DEFAULT_RISK_PERCENT, gt=0, le=MAX_RISK_PERCENT)
    require_higher_alignment: bool = True
    scan_concurrency: int = Field(default=3, ge=1, le=8)


class AnalysisRequest(StrictModel):
    symbol: str
    timeframe: Timeframe = "1h"
    higher_timeframes: list[Timeframe] = Field(default=[], max_length=3)
    lower_timeframe: Timeframe | None = None
    include_derivatives: bool = True

    _symbol = field_validator("symbol")(normalize_symbol)

    @model_validator(mode="after")
    def timeframe_roles(self) -> Self:
        from app.schemas.candles import INTERVAL_MS
        if any(INTERVAL_MS[tf] <= INTERVAL_MS[self.timeframe] for tf in self.higher_timeframes):
            raise ValueError("Higher-timeframe confirmations must be above the primary timeframe.")
        if self.lower_timeframe is not None and INTERVAL_MS[self.lower_timeframe] >= INTERVAL_MS[self.timeframe]:
            raise ValueError("Lower momentum timeframe must be below the primary timeframe.")
        return self


class Watchlist(StrictModel):
    name: str = Field(default="Favorites", min_length=1, max_length=60)
    symbols: list[str] = Field(default_factory=list, max_length=500)
    timeframe: Timeframe = "1h"

    @field_validator("symbols")
    @classmethod
    def symbols_valid(cls, value: list[str]) -> list[str]:
        return sorted(set(normalize_symbol(s) for s in value))


class ScanRequest(StrictModel):
    timeframe: Timeframe = "1h"


class ScanFilter(StrictModel):
    minimum_volume: Finite = Field(default=0, ge=0)
    maximum_spread: Finite | None = Field(default=None, ge=0)
    minimum_volatility: Finite | None = Field(default=None, ge=0)
    maximum_volatility: Finite | None = Field(default=None, ge=0)
    minimum_long: Finite = Field(default=0, ge=0, le=100)
    minimum_short: Finite = Field(default=0, ge=0, le=100)
    trend: Literal["all", "bullish", "bearish", "neutral"] = "all"
    setup: Literal["all", "breakout", "retest", "trend", "reversal"] = "all"
    quality: Literal["all", "usable", "stale", "unavailable", "pending"] = "all"
    watchlist_only: bool = False
    rsi_min: Finite | None = Field(default=None, ge=0, le=100)
    rsi_max: Finite | None = Field(default=None, ge=0, le=100)
    adx_min: Finite | None = Field(default=None, ge=0, le=100)
    macd: Literal["all", "bullish", "bearish"] = "all"
    sort: Literal["symbol", "volume", "spread_bps", "volatility", "long_score", "short_score", "change_percent"] = "volume"
    descending: bool = True
