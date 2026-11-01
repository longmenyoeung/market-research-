import numpy as np
import pandas as pd
import pytest

from app.backtesting.engine import BacktestRequest, SimulationConfig, prepare, run_validation, simulate
from app.schemas.platform import Preferences
from test_men_crypto import profile_data
from test_research import backtest_config as research_config, market_frame as research_frame
from test_safety_rails import backtest_config, market_frame

HOUR = 3_600_000
MIDNIGHT = 1_700_006_400_000  # UTC day boundary


def zero_cost_config():
    return SimulationConfig(symbol="TESTUSDT", timeframe="1h", risk_percent=0.5, fee_bps=0,
                            slippage_bps=0, spread_bps=0, funding_bps_per_8h=0)


def test_daily_rail_baseline_includes_open_loss_carried_over_midnight():
    # Long entered at 100 (stop 98.5) before midnight, marked at 98.6 at rollover,
    # then gaps through the stop at 97.01 on the new day.
    count = 10
    open_ = np.full(count, 100.0); high = np.full(count, 100.5)
    low = np.full(count, 98.6); close = np.full(count, 98.6)
    open_[5:], high[5:], low[5:], close[5:] = 97.01, 97.5, 96.9, 97.0
    signal = np.zeros(count, dtype=int); signal[[1, 3, 6]] = 1
    prepared = pd.DataFrame({"time": MIDNIGHT - 5 * HOUR + np.arange(count) * HOUR, "open": open_,
                             "high": high, "low": low, "close": close, "signal": signal,
                             "atr": np.ones(count)})
    result = simulate(prepared, zero_cost_config(), 1, count)
    trade = result["trades"][0]
    loss = -trade["net_pnl"]
    marked_at_rollover = 10000 + trade["quantity"] * (98.6 - 100)
    # A cash baseline would allow the day-2 signal; the marked baseline must not.
    assert 0.01 * marked_at_rollover <= loss < 0.01 * 10000
    # Bar 3's signal is skipped because a position is open (one at a time); bar 6's is blocked.
    assert len(result["trades"]) == 1
    assert result["metrics"]["blocked_by_daily_loss_cap"] == 1


def test_daily_rail_baseline_never_loosens_for_profitable_carry():
    # Long entered at 100 (stop 98.5, target 103) is marked at 102.5 at rollover, so
    # marked equity exceeds cash. It then gaps through the stop at 96.98 on day 2.
    # The loss lies strictly between 1% of cash and 1% of marked equity, so only
    # min(cash, marked) blocks the next entry; a marked-only baseline would allow it.
    count = 10
    open_ = np.full(count, 100.0); high = np.full(count, 102.9)
    low = np.full(count, 99.0); close = np.full(count, 102.5)
    open_[5:], high[5:], low[5:], close[5:] = 96.98, 97.5, 96.9, 97.0
    signal = np.zeros(count, dtype=int); signal[[1, 6]] = 1
    prepared = pd.DataFrame({"time": MIDNIGHT - 5 * HOUR + np.arange(count) * HOUR, "open": open_,
                             "high": high, "low": low, "close": close, "signal": signal,
                             "atr": np.ones(count)})
    result = simulate(prepared, zero_cost_config(), 1, count)
    trade = result["trades"][0]
    loss = -trade["net_pnl"]
    marked_at_rollover = 10000 + trade["quantity"] * (102.5 - 100)
    assert 0.01 * 10000 < loss < 0.01 * marked_at_rollover
    assert len(result["trades"]) == 1
    assert result["metrics"]["blocked_by_daily_loss_cap"] == 1


def test_walk_forward_reports_indicator_reward_multiple():
    raw, context, settings, rules = profile_data()
    config = BacktestRequest(symbol="TESTUSDT", timeframe="15m", strategy="indicator", signal_profile="men_crypto",
                             analysis_settings=Preferences(indicators=settings), study_rules=rules,
                             higher_timeframes=["1h"], start_time=int(raw.time.iloc[0]),
                             end_time=int(raw.time.iloc[-1]) + 899999)
    result = run_validation(raw, config, {"1h": context})
    values = {item["value"] for item in result["sensitivity"]}
    for fold in result["walk_forward"]:
        assert "selected_atr_stop" not in fold
        assert fold["selected_parameter"] == "reward_multiple"
        assert fold["selected_value"] in values


def test_walk_forward_reports_ema_atr_stop():
    result = run_validation(research_frame(), research_config())
    values = {item["value"] for item in result["sensitivity"]}
    for fold in result["walk_forward"]:
        assert "selected_atr_stop" not in fold
        assert fold["selected_parameter"] == "atr_stop"
        assert fold["selected_value"] in values


def test_reported_spread_and_slippage_equal_applied_costs():
    config = backtest_config(spread_bps=40, slippage_bps=7)
    result = simulate(prepare(market_frame(), config), config, 60, 600)
    half_spread, slip = config.spread_bps / 20000, config.slippage_bps / 10000
    adverse = half_spread + slip
    assert result["trades"]
    for trade in result["trades"]:
        d = 1 if trade["side"] == "LONG" else -1
        raw_entry = trade["entry"] / (1 + d * adverse)
        raw_exit = trade["exit"] / (1 - d * adverse)
        applied = d * trade["quantity"] * (raw_exit - raw_entry) - trade["gross_pnl"]
        assert trade["spread_cost"] + trade["slippage_cost"] == pytest.approx(applied, rel=1e-9)
        assert trade["spread_cost"] / trade["slippage_cost"] == pytest.approx(half_spread / slip)
    metrics = result["metrics"]
    assert metrics["total_spread_cost"] == pytest.approx(sum(t["spread_cost"] for t in result["trades"]))
