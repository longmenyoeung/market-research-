# Historical research methodology

The simulator supports three explicit research strategies: shared indicator
profiles (Men Crypto Signal Pro or the original experimental study), a causal replay of the
same grouped LONG/SHORT assessor used by reports, and an independent EMA-crossover
baseline. Reusing the assessor tests its historical behavior; it does not validate
the scoring weights or establish profitability. All entries, exits, balances and
positions below are historical simulation state; nothing is sent to an exchange.
See [the shared profile methodology](men-crypto-profile.md) for exact chart/signal
parity, exposure controls, optional break-even/trailing assumptions and archive
mode. The older evidence and EMA modes remain separate research strategies.

## Data and timing

The job fetches historical candles through the read-only provider with forward
pagination. It accepts 400 to 20,000 requested primary bars and loads bounded
warm-up/context history. It refuses missing range edges, non-progressing pages and
discontinuous primary history. Only closed bars are included. Current contract
discovery is required, so survivorship bias from excluding delisted contracts
remains possible.

For evidence replay, indicators are precomputed causally, pivots become available
only after their right-side confirmation bars, and each context timeframe is joined
by its close timestamp at or before the primary historical clock. The actual shared
assessor receives that historical clock. Required higher context that is absent
fails closed. Signals also record their known-at time and cannot enter until the
configured later open. The breakout/retest confirmation mode is the default.

For the EMA baseline, a fast/slow crossover is observed at a closed bar and can
enter only at the following open, with a stop distance based on the prior bar's
ATR. Neither strategy uses same-close entry. Warm-up candles supply calculations,
never earlier test trades. A held-out window starts flat and cannot import a
development signal into its first bar.

The model permits one position at a time, with risk-based continuous quantity and
a maximum 1x notional margin assumption. No averaging down or martingale exists.
The shared RiskManager defaults to 0.25% risk and rejects values above 0.5%.
New entries stop for the remaining UTC day after realized net losses reach 1% of
the day's baseline. The baseline is the lower of cash and the prior close mark,
that is, the previous bar's close-marked equity. The two differ only when a
position is carried over UTC midnight. This is a simulation convention;
unmodeled gaps can exceed it.

## Costs and exits

- Entry and exit receive adverse slippage plus half of the configured spread.
  Fees apply to each filled notional. Spread/slippage are already in fill prices;
  their separate totals are explanatory and are not subtracted a second time.
- Stop gaps use the adverse opening price. If both stop and target touch in one
  bar, the stop is assumed first. Target gaps conservatively use the target
  reference before adverse costs. Holding limits and window ends use the close.
- Historical funding mode requires validated public events, their actual event
  timestamps, rates, rate type and settlement mark prices. It never substitutes a
  candle close or falls back to a scenario. Scenario mode is an explicit user rate
  in basis points per eight hours with UTC 00/08/16 events and candle close as a
  modeled mark proxy. Positive funding charges LONG and credits SHORT; negative
  funding reverses those effects. Scenario zero excludes funding and does not assert
  that historical exchange funding was zero.
- Exit bars incur scheduled funding even when an intrabar stop might have
  preceded it. This conservative timing assumption is disclosed in results.
- Maintenance-margin tiers, liquidation, true intrabar ordering, changing exchange
  quantity constraints, borrowing and exact trade-level fills are not modeled.
  Today's contract filters are not retroactively invented for historical dates.

## Development, held-out data and walk-forward validation

The development fraction defaults to 60%. EMA research compares three ATR-stop
variants (0.75x, 1x, 1.25x the requested multiplier, minimum 0.25). Evidence replay
compares candidate-score thresholds around the requested analysis setting. Variants
are compared only on development data. Selection uses net P&L divided by
max(maximum drawdown percent, 0.25) and requires five training trades. Scores are
null for insufficient samples.

The best eligible development variant is evaluated once on the held-out partition.
If none qualifies, the requested strategy parameter is retained and
`selection_status` states insufficient evidence; it is not represented as optimized.

Expanding walk-forward training windows choose among the same variants using
data strictly preceding each test window. Every test window starts flat. Earlier
test periods can become training data for a later fold. This is disclosed rather
than presented as independent repeated holdouts. Repeated user-led parameter
search can still overfit; the software cannot make repeated experiments independent.

## Results and uncertainty

Reports include trades, win rate, Wilson win-rate interval, profit factor,
expected net P&L per trade, net P&L/return, maximum bar-close drawdown, downside
deviation, average holding bars, separate LONG/SHORT counts and net results,
modeled cost totals, daily-loss blocked-entry counts, parameter sensitivity and
walk-forward windows. Zero-trade results retain undefined ratios as null and remain
valid, serializable results.

Equity is marked at bar closes. Intrabar drawdown can be worse. Wilson intervals
describe sample win-rate uncertainty only; trade dependence and parameter search
limit their interpretation. Small samples are flagged. Synthetic regression tests
prove software properties, not market profitability. No future result is guaranteed.
