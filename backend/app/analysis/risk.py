from decimal import Decimal, ROUND_DOWN
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator, field_validator
from app.core.risk_limits import (
    DEFAULT_RISK_PERCENT,
    MAX_DAILY_LOSS_PERCENT,
    MAX_RISK_PERCENT,
    MIN_NET_REWARD_TO_RISK,
)
from app.schemas.platform import StrictModel
from app.schemas.candles import normalize_symbol

PlanDecimal = Annotated[Decimal, Field(max_digits=36, decimal_places=18)]


class RiskManager:
    """Independent sizing authority for planning, reports and backtests.

    No caller can raise the per-trade or per-day limit by supplying a larger
    preference; out-of-range values raise instead of being clamped silently.
    """

    default_risk_percent = DEFAULT_RISK_PERCENT
    max_risk_percent = MAX_RISK_PERCENT
    max_daily_loss_percent = MAX_DAILY_LOSS_PERCENT
    minimum_net_reward_to_risk = MIN_NET_REWARD_TO_RISK

    @classmethod
    def validate_risk_percent(cls, value: Decimal) -> Decimal:
        if not Decimal(0) < value <= cls.max_risk_percent:
            raise ValueError(f"risk_percent must be > 0 and <= {cls.max_risk_percent}.")
        return value

    @classmethod
    def risk_budget(cls, equity: Decimal, risk_percent: Decimal) -> Decimal:
        return equity * cls.validate_risk_percent(risk_percent) / 100

    @classmethod
    def daily_loss_limit(cls, equity: Decimal) -> Decimal:
        return equity * cls.max_daily_loss_percent / 100

    @classmethod
    def size(cls, *, equity, risk_percent, cost_per_unit, entry, fee_bps=0, leverage=1) -> Decimal:
        """Size from the risk budget, then apply a 1x-notional margin constraint."""
        equity, risk_percent, cost_per_unit, entry = (Decimal(str(value)) for value in
                                                      (equity, risk_percent, cost_per_unit, entry))
        fee_bps, leverage = Decimal(str(fee_bps)), Decimal(str(leverage))
        cls.validate_risk_percent(risk_percent)
        if equity <= 0 or entry <= 0 or cost_per_unit <= 0 or leverage < 1:
            return Decimal(0)
        by_risk = equity * risk_percent / 100 / cost_per_unit
        by_margin = equity / (entry / leverage + entry * fee_bps / 10000)
        return min(by_risk, by_margin)

    @classmethod
    def within_daily_limit(cls, realized_pnl: Decimal, day_start_equity: Decimal) -> bool:
        """Entries remain allowed while the realized loss is inside the rail."""
        return realized_pnl > -cls.daily_loss_limit(day_start_equity)


class TradePlan(StrictModel):
    symbol: str | None = None
    side: Literal["LONG", "SHORT"]
    equity: PlanDecimal = Field(gt=0, le=1_000_000_000)
    risk_percent: PlanDecimal = Field(default=DEFAULT_RISK_PERCENT, gt=0, le=MAX_RISK_PERCENT)
    entry: PlanDecimal = Field(gt=0, le=1_000_000_000)
    stop: PlanDecimal = Field(gt=0, le=1_000_000_000)
    target: PlanDecimal = Field(gt=0, le=1_000_000_000)
    leverage: PlanDecimal = Field(default=1, ge=1, le=125)
    fee_bps: PlanDecimal = Field(default=5, ge=0, le=100)
    slippage_bps: PlanDecimal = Field(default=5, ge=0, le=1000)
    funding_rate: PlanDecimal = Field(default=0, ge=0, le=Decimal("0.1"))
    funding_periods: int = Field(default=0, ge=0, le=1000)
    quantity_step: PlanDecimal | None = Field(default=None, gt=0)
    minimum_quantity: PlanDecimal | None = Field(default=None, gt=0)
    maximum_quantity: PlanDecimal | None = Field(default=None, gt=0)
    minimum_notional: PlanDecimal | None = Field(default=None, gt=0)
    tick_size: PlanDecimal | None = Field(default=None, gt=0)

    @field_validator("symbol")
    @classmethod
    def valid_symbol(cls, value: str | None) -> str | None:
        return normalize_symbol(value) if value is not None else None

    @model_validator(mode="after")
    def correct_side(self) -> Self:
        if not (self.stop < self.entry < self.target if self.side == "LONG" else self.target < self.entry < self.stop):
            raise ValueError("LONG requires stop < entry < target; SHORT requires target < entry < stop.")
        if self.tick_size is not None and any(value % self.tick_size for value in (self.entry, self.stop, self.target)):
            raise ValueError("Prices must align to the supplied tick size.")
        return self


def plan_trade(plan: TradePlan) -> dict:
    risk_budget = RiskManager.risk_budget(plan.equity, plan.risk_percent)
    distance, reward = abs(plan.entry-plan.stop), abs(plan.target-plan.entry)
    fee_slip = (plan.fee_bps+plan.slippage_bps)/10000
    funding_per_unit = plan.entry*plan.funding_rate*plan.funding_periods
    stop_cost = (plan.entry+plan.stop)*fee_slip+funding_per_unit
    target_cost = (plan.entry+plan.target)*fee_slip+funding_per_unit
    # RiskManager owns the risk budget and the 1x-notional margin constraint.
    quantity = RiskManager.size(equity=plan.equity, risk_percent=plan.risk_percent,
                                cost_per_unit=distance+stop_cost, entry=plan.entry,
                                fee_bps=fee_slip*10000, leverage=plan.leverage)
    if plan.maximum_quantity is not None: quantity = min(quantity, plan.maximum_quantity)
    if plan.quantity_step is not None: quantity = (quantity/plan.quantity_step).to_integral_value(rounding=ROUND_DOWN)*plan.quantity_step
    violations = []
    if quantity <= 0: violations.append("Risk budget cannot accommodate one quantity step.")
    if plan.minimum_quantity is not None and quantity < plan.minimum_quantity: violations.append("Below minimum contract quantity.")
    notional = quantity*plan.entry
    if plan.minimum_notional is not None and notional < plan.minimum_notional: violations.append("Below minimum contract notional.")
    loss, profit = quantity*(distance+stop_cost), quantity*(reward-target_cost)
    net_rr = profit/loss if loss else None
    return {"hypothetical_only": True, "risk_budget": risk_budget, "quantity": quantity, "notional": notional,
            "initial_margin_estimate": notional/plan.leverage, "potential_loss": loss, "potential_profit": profit,
            "gross_reward_to_risk": reward/distance, "net_reward_to_risk": net_rr,
            "meets_minimum_net_reward_to_risk": net_rr is not None and net_rr >= RiskManager.minimum_net_reward_to_risk,
            "costs_at_stop": quantity*stop_cost, "costs_at_target": quantity*target_cost,
            "fee_at_target": quantity*(plan.entry+plan.target)*plan.fee_bps/10000,
            "slippage_at_target": quantity*(plan.entry+plan.target)*plan.slippage_bps/10000,
            "funding_cost": quantity*funding_per_unit, "constraints_satisfied": not violations, "violations": violations,
            "risk_rails": {"default_risk_percent": RiskManager.default_risk_percent,
                           "max_risk_percent": RiskManager.max_risk_percent,
                           "max_daily_loss_percent": RiskManager.max_daily_loss_percent,
                           "minimum_net_reward_to_risk": RiskManager.minimum_net_reward_to_risk},
            "assumptions": ["Linear USDT contract; fees and slippage charged on entry and each hypothetical exit.",
                "Funding is a user-specified adverse cost, not a forecast or assumed receipt.",
                "Initial margin excludes exchange maintenance tiers; liquidation price is not calculated.",
                "Stops do not guarantee exit price. Gaps, slippage, liquidation and market conditions can exceed planned loss.",
                "Contract constraints are enforced only when supplied; leverage does not change the risk budget."]}
