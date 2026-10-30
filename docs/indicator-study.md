# BUY / SELL indicator study

Open **BUY / SELL indicator** in the dashboard. Select a contract, timeframe,
source and rule thresholds, then select **Build indicator chart**. The settings
are experimental and have not demonstrated profitability.

## Sources and current eligibility

- **Saved candles - historical replay** uses real archived closed candles only.
  It makes no exchange request, shows historical markers and always labels
  current eligibility **NO TRADE**. Missing archives or too few candles return
  an explicit error. Gapped windows are rejected.
- **Live report - current data required** first runs the complete analysis engine.
  Current BUY/SELL candidate labels come exclusively from its LONG/SHORT
  classification, with freshness, quote quality, liquidity, cost and higher
  context checks. Timeouts remain explicit failures. There is no silent archive
  substitution. The view is a snapshot that must be refreshed manually; its
  current direction expires with the report's `valid_until_ms`.

The historical marker rule is a single-timeframe study, separate from full
current candidate eligibility. Past markers do not include contemporaneous
spread, fees, funding, or higher-timeframe confirmation. Their counts are not
trade counts, a backtest, a win rate or a probability.

## Deterministic marker rule

The indicator page has quick chart controls for 15m, 30m, 1h and 4h. Its live
report uses the following explicit higher-context plan:

| Chart timeframe | Higher trend context |
| --- | --- |
| 15m | 1h and 4h |
| 30m | 1h and 4h |
| 1h | 4h |
| 4h | 1d for broader context |

The timeframe change clears the previous study before rebuilding. The global
indicator periods and thresholds remain configurable experimental rules; the
timeframe controls do not imply profitable short-term parameters.

The higher-context cards show trend, momentum, missing data and directional
conflicts with the chart trend. Archived context is calculated only from bars
that closed at or before the selected window's last confirmed candle. It stays
historical (`usable=false`) even when available. Historical arrow conditions
still use only the chart timeframe. Live higher alignment follows the existing
`require_higher_alignment` preference and the full report's data-quality gates.
An unavailable 15m archive is never manufactured from 30m or 1h bars.
The page checks whether the selected archive contains any candles before
enabling its build action. If empty, it explains the required download without
submitting a calculation. The API separately validates sufficient warm-up and
continuous history when building a study.

At a confirmed candle's close, require every condition below. Periods use the
dashboard's indicator settings; defaults are shown here.

| Condition | Past BUY | Past SELL |
| --- | --- | --- |
| Trend | Close > EMA20 > EMA50 > EMA200 | Close < EMA20 < EMA50 < EMA200 |
| Momentum | 50 < RSI14 < 70 and MACD histogram > 0 | 30 < RSI14 < 50 and histogram < 0 |
| Strength | ADX14 >= configured minimum, default 20 | Same |
| Volume | Volume / 20-bar mean volume >= configured minimum, default 1 | Same |

EMA values form one trend group. RSI and MACD form one momentum group. ADX is
directionless strength context and can overlap trend evidence; the interface
does not add these checks into a numerical confidence score.

A marker appears only when all conditions become true after a previously
unaligned candle (or a change of direction), subject to the minimum marker
spacing, default eight bars. The spacing suppresses labels and does not track
positions or exits. Missing warm-up values prevent a marker. With default
settings the API requires at least 201 confirmed candles. This is enough for
the selected rule inputs; some other indicator series can still be warming up.

The existing indicator catalog documents formulas, parameters, inputs, warm-ups
and interpretations and is also available in the page's formula details.
EMA uses alpha=2/(n+1), RSI/ATR/ADX use the existing Wilder implementation,
MACD histogram is EMA12-EMA26 minus its EMA9 signal, and relative volume divides
volume by its configured rolling mean. See [indicators.md](indicators.md).

## Drawings and signal timing

- Arrows plot at the candle's open-time coordinate, with the explicit label
  `Past BUY` or `Past SELL`. They become known only at that candle's close,
  recorded in `known_at`; the source timeframe and rule accompany each marker.
- The observed signal close is a reference, not a modeled fill price. A manual
  decision can only follow confirmation and may obtain a different price.
- Stop reference is 1.5 ATR from that close by default, below for BUY and above
  for SELL. Gross targets are 1R, 2R and 3R from that reference. Nonpositive or
  misordered levels are omitted. No trade execution or guaranteed stop fill is
  implied. Fees, slippage, funding and liquidation can change realized results.
- Select a past signal in the list to change its price lines and risk/reward
  regions. The region spans up to twenty subsequent *observed* chart bars; that
  width is visual and is not a forecast or holding-period assumption.
- Support/resistance zones, confirmed swings, BOS/CHoCH, trendlines and optional
  FVG/Fibonacci/channel drawings reuse the documented structure engine. They
  describe the latest closed study window. Selecting an older marker changes
  its reference levels; it does not rewind the other structure drawings.
  Pivot confirmation times and provisional observations remain explicit.

## API

`POST /api/v1/indicator-study` accepts:

```json
{
  "symbol": "BTCUSDT",
  "timeframe": "1h",
  "source": "archive",
  "rules": {
    "minimum_adx": 20,
    "minimum_relative_volume": 1,
    "atr_stop_multiple": 1.5,
    "signal_spacing_bars": 8
  }
}
```

Inputs are validated with Pydantic. The response contains closed candles,
indicator series/catalog, structure annotations and `indicator_study` with past
signals, rules, latest study-bar condition checks, source, as-of time,
limitations and current eligibility. It does not persist simulated trades or
transmit any data to exchange order endpoints. The same application access and
Origin controls as other local analysis computations apply.
