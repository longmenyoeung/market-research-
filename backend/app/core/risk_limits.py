"""Platform-wide risk rails.

These constants are the single source of truth for per-trade and per-day risk.
Strategy, API and backtest code must import them instead of inventing limits.
Changing a rail is a reviewed code change, not a user preference.
"""
from decimal import Decimal

DEFAULT_RISK_PERCENT = Decimal("0.25")
# Hard ceiling. Requests above this are rejected rather than clamped silently.
MAX_RISK_PERCENT = Decimal("0.5")
# A trading day starts at 00:00 UTC. New entries stop after this realized loss.
MAX_DAILY_LOSS_PERCENT = Decimal("1")
# A candidate must still clear this net reward/risk after modelled costs.
MIN_NET_REWARD_TO_RISK = Decimal("1.5")
