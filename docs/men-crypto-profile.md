# Men Crypto Signal Pro in the Python platform

The user requested the prompt's missing capabilities in the existing project.
This implementation has separate indicator and strategy-simulation components,
sharing one signal function. It does not include Pine files or any exchange
execution integration. TradingView's strategy broker emulator is likewise a
simulation concept; this application's simulator remains entirely local.

## Indicator

Choose **Men Crypto Signal Pro** on the BUY / SELL indicator page. The old
**Experimental EMA20/50/200** profile remains separately named and available.

The Men Crypto profile requires all of:

- BUY: close > EMA50 > EMA200 and close > complete-session UTC VWAP.
- SELL: close < EMA50 < EMA200 and close < complete-session UTC VWAP.
- RSI14 between 50 and 70 for BUY or 30 and 50 for SELL, with the matching
  positive/negative MACD(12,26,9) histogram. Bounds are configurable.
- ADX14 >= 20 and candle volume / SMA20 volume >= 1 by default.
- Every selected higher timeframe has a confirmed, nonstale close above its
  EMA50 > EMA200 for BUY, or the inverse for SELL. Choose 1h alone for the
  attachment's 15m/1h configuration, or retain the app's 1h+4h context plan.

Periods come from the global indicator settings. Thresholds, RSI bounds, marker
spacing and ATR distance are configurable on the study page. EMA/VWAP filters
form one price/trend group; RSI and MACD form one momentum group. Volume is
participation, not proof of aggressive buying or selling.

Higher series are joined at their close timestamps, never by copying today's
trend over past bars. A missing context, expired higher bar, missing volume or
unavailable warm-up cannot confirm a signal. A partially loaded first UTC day
cannot establish complete-session VWAP. Signals are known only after the
primary candle closes. Raw conditions can remain aligned across many candles;
arrows are emitted on transitions and honor configured spacing.

Archived studies always show NO TRADE for current eligibility. Live candidates
also require the complete report's current quote, liquidity, costs and context
checks. These additional safeguards can block a candidate even when a past
indicator pattern exists. Snapshot eligibility expires with its source data.

## Strategy simulator

Select **Backtest these exact rules** to transfer the profile, thresholds,
indicator settings, higher timeframes and hypothetical risk options into
**Validation lab**. The **Indicator shared rules** strategy calls the same
`build_signals()` function used by the chart. Signal parity requires identical
OHLCV windows, settings and higher data; different EMA seed histories can differ.

Signals are not fills. Virtual entries use a later candle open and modeled
spread/slippage. At most one virtual position is open. ATR stop distance uses
the prior confirmed signal's ATR; the default target is 2R. The chart's signal
close reference and a later modeled fill can have different prices and levels.

Sizing includes modeled stop costs and a configurable exposure cap, at most
100% of hypothetical equity including entry fees. Existing risk rails remain:
default 0.25%, maximum 0.5% per position, and a 1% UTC daily realized-loss rail.
This deliberately retains the app's lower default instead of the prompt's 0.5%.
These are modeling settings, not guarantees that actual losses stop there.
Indicator-strategy entries also require at least 1.5 modeled net reward/risk
after spread, fees and slippage. This can reject an arrow's simulated entry.

Optional break-even and trailing references are computed after a closed bar and
become active on the following bar. Long stops only rise; short stops only fall.
A stop at entry still incurs fees, slippage and possible funding. Stops are
assumed first if stop and target both touch; gaps can worsen the exit.

Output includes average winning/losing trade and average net R in addition to
the existing trade count, win rate, profit factor, net P&L, drawdown, cost totals,
directional performance, held-out data and walk-forward results. Net R divides
net P&L by the initial modeled risk including stop costs. The new strategy's
development sensitivity varies target reward multiple; signal inputs stay fixed.

Saved-data backtests use only the archive and require an explicit funding
scenario because historical funding is not archived locally. Live-history runs
can require actual public funding data. No fallback silently changes either
source. **Use saved candle range** uses exact stored timestamps; complete range
coverage and at least 400 continuous primary bars are still required.

## Hypothetical tracker and dashboard alerts

The indicator's tracker replays the selected window from flat using the same
simulator, with an optional still-open virtual position. Its status, quantities,
entry, stop, target and event log are hypothetical. It does not read an account,
establish real fills, or provide out-of-sample performance evidence.

On fresh live studies with alerts enabled, only the latest confirmed candle can
record BUY/SELL candidate, virtual stop/target, EMA-trend reversal or weak
ADX/volume observations. BUY/SELL alerts also require current candidate eligibility.
Stable event IDs and a per-application lock prevent refresh duplicates. Archive
replays and stale studies emit no current notifications. All notifications stay
inside the dashboard. Virtual stop/target observations never claim a real fill.

No webhook, API trading key, exchange order or position-management endpoint is
implemented. Reliable live provider access and a sufficient real 15m dataset
remain necessary before an end-to-end live demonstration or credible market
performance comparison. No strategy profitability is asserted.
