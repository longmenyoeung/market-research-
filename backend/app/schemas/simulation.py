"""Validated settings for virtual positions only, never exchange instructions."""

from pydantic import Field

from app.core.risk_limits import DEFAULT_RISK_PERCENT, MAX_RISK_PERCENT
from app.schemas.platform import StrictModel, Finite


class SimulationOptions(StrictModel):
    equity: Finite = Field(default=10000, gt=0, le=1e9)
    risk_percent: Finite = Field(default=DEFAULT_RISK_PERCENT, gt=0, le=MAX_RISK_PERCENT)
    max_exposure_percent: Finite = Field(default=100, gt=0, le=100)
    reward_multiple: Finite = Field(default=2, gt=0, le=10)
    fee_bps: Finite = Field(default=5, ge=0, le=100)
    slippage_bps: Finite = Field(default=5, ge=0, le=1000)
    spread_bps: Finite = Field(default=2, ge=0, le=1000)
    funding_bps_per_8h: Finite = Field(default=0, ge=-100, le=100)
    max_holding_bars: int = Field(default=100, ge=1, le=1000)
    break_even: bool = False
    break_even_at_r: Finite = Field(default=1, gt=0, le=10)
    trailing_stop: bool = False
    trailing_atr: Finite = Field(default=1.5, gt=0, le=10)
    trailing_activation_r: Finite = Field(default=1.5, gt=0, le=10)
