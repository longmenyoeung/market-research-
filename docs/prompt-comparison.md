# Men Crypto Signal Pro comparison

Audit date: 2026-10-31, Asia/Bangkok.

This compares the user-supplied TradingView Pine Script v6 specification with
the current Python/FastAPI and React application. It describes implemented
behavior and gaps; it does not establish trading performance or full completion.

## Current assessment

The project provides a broader research workspace: dynamic contract discovery,
full-universe scanning, market structure drawings, multiple timeframes,
derivatives context when available, saved reports, a hypothetical planner and
historical research tools. This breadth does not demonstrate better signals.
Reliable live Binance connectivity remains unresolved, and the last inspected
BTC archive has no 15m candles. Existing 30m, 1h and 4h studies are historical.

## Requirement comparison

| Requested capability | Current implementation | Assessment |
| --- | --- | --- |
| Separate visual indicator and backtest strategy | `analysis/indicator_study.py` and `backtesting/` are separate Python components | Architecture exists; no Pine files yet |
| Pine Script v6 indicator and strategy deliverables | Current application uses Python/React and Lightweight Charts | Not delivered |
| 15m main view and 1h confirmation | Quick 15m/30m/1h/4h controls; live 15m studies request 1h and 4h context | Implemented in app; actual BTC 15m history missing |
| EMA50/200, RSI14, MACD12/26/9, VWAP, ATR14, volume SMA20, ADX14 | Calculated, documented and available on charts | Implemented |
| Exact requested LONG/SHORT conjunction | Past markers use EMA20/50/200, RSI/MACD, ADX and relative volume | Different rule; not an exact match |
| VWAP mandatory for directional signals | VWAP is plotted, but `build_signals()` does not require price above/below it | Gap |
| Higher trend required by the historical arrow rule | Historical arrows are single-timeframe; archived higher context is shown separately | Gap relative to requested arrow rule |
| Current candidates with higher context | Complete report engine applies context and freshness rules; alignment follows preferences | Implemented, live operation not verified successfully |
| Clear BUY/SELL labels and automatic drawings | Past arrows, EMA/Bollinger/VWAP, swing/structure/zone/trendline drawings | Implemented and visually checked with real archived data |
| ATR stop and 1:2 reward/risk | Past references draw 1R/2R/3R; simulator default reward multiple is 2 | Implemented as hypothetical references/simulation |
| Equity and stop-distance position sizing | Shared risk manager accounts for stop distance and modeled costs | Implemented |
| Default risk 0.5% | Default is 0.25%, with a hard maximum of 0.5% | Intentional difference; no claim of superior performance |
| Maximum exposure setting | Simulator uses a fixed 1x notional constraint; manual planner accepts leverage assumptions | Independent configurable exposure cap is missing |
| One open strategy position | Simulator tracks a single virtual position | Implemented in simulation; visual marker spacing is not position tracking |
| Optional break-even stop | Not present in simulator | Gap |
| Optional trailing stop | Not present in simulator | Gap |
| Dashboard trend, momentum and reference prices | Available through scanner, reports, study page and planner | Implemented across pages rather than one TradingView table |
| Current position status | Backtests track virtual positions; application does not read exchange account positions | Real position status is outside the read-only public-data design |
| BUY/SELL and condition alerts | Dashboard stores classification, invalidation, liquidity, volatility, funding and stale-data changes | Partial; not dedicated closed-bar study alert conditions |
| SL-hit/TP-hit alerts | No tracker for a manually recorded hypothetical position | Gap; a drawn level does not prove a real fill |
| Same rules for indicator and strategy | Backtester supports EMA crossover and full evidence-engine replay; new chart markers use a third rule | Important parity gap |
| Fees, slippage and funding | Explicit simulation assumptions and historical funding option; public data required | Implemented; reliable live data loading remains blocked |
| Trades, win rate, profit factor, net result, drawdown | Reported by simulator, with sampling limits | Implemented |
| Average winning and losing trade | Expected net P&L per trade exists; separate average win/loss fields do not | Partial |
| Closed bars, causal indicators, higher-timeframe timing | Regression tests cover calculation causality, confirmation timing and context as-of joins | Implemented software checks; not a universal guarantee against data revisions |
| Missing volume | Unavailable warm-ups or inadequate volume prevent the relevant rule checks | Implemented |
| Out-of-sample and walk-forward checks | Separate development/held-out partitions, training-only parameter selection, walk-forward windows | Broader than the prompt; credible market results still required |
| Binance alert-to-order automation | Exchange requests are public-data-only and guarded; no execution endpoints | Excluded by the original manual-only requirement |

## The two-component target

1. **Indicator:** visualization, evidence dashboard, confirmed signal events,
   hypothetical level drawings and user-facing notifications. It must not
   submit exchange transactions or describe a historical arrow as a current
   candidate when required data is stale.
2. **Strategy simulator:** consumes the exact same signal function and
   parameters, then models a later entry, position sizing, stops, targets,
   exposure, fees, slippage, funding, optional stop adjustments and results.
   Its positions and fills are virtual. A visual signal need not become a
   simulated trade when a position is already open or risk checks reject it.

The reference to "actual entries/exits" in a TradingView strategy means
instructions to its broker emulator for backtesting, not authorization for
Binance order execution. TradingView documents that distinction in its
[strategy guide](https://www.tradingview.com/pine-script-docs/concepts/strategies/#broker-emulator).
If Pine deliverables are selected, create a separate indicator and strategy
file with matching rule blocks and explicit simulation labeling. Compilation
and chart checks in TradingView must be reported separately from Python tests.
No exchange webhook or order-automation integration is part of this project.

## Work required to claim a specification match

1. Resolve whether deliverables are the Python app alone or also two Pine v6
   files. Preserve separate visualization and simulation components either way.
2. Define one versioned signal profile for the requested EMA50/200, RSI/MACD,
   VWAP, ADX, volume and confirmed higher trend conjunction. Specify RSI bands,
   ADX/volume thresholds, UTC VWAP anchoring and missing-data behavior explicitly.
   OHLCV volume measures participation, not proven buying/selling intent.
3. Reuse that profile in visualization and simulation; test signal parity and
   prefix causality, including every confirmed higher-timeframe join. Preserve
   the existing experimental study as a separately named rule rather than
   silently changing the meaning of its previous arrows.
4. Add simulated configurable exposure, optional break-even/trailing stops,
   explicit intrabar ambiguity assumptions and separate average win/loss metrics.
5. Implement deduplicated confirmed-candle signal events and hypothetical
   stop/target tracking notifications. Do not infer exchange positions or fills
   from candle touches. Keep application notifications inside the dashboard.
6. Obtain permitted, reliable real 15m and higher-timeframe data and demonstrate
   a complete live chart/report. Then run development, held-out and walk-forward
   evaluations with costs; publish sample sizes and limitations before making
   any performance comparison.

The last full verification record reports 238 backend tests, 16 frontend unit
tests and 18 browser tests. Those passing tests are evidence about software
behavior, not evidence of profitable or superior trading performance.
