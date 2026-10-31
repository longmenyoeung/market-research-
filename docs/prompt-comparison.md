# Men Crypto Signal Pro comparison

Audit date: 2026-10-31, Asia/Bangkok.

Updated after the user asked to add the missing capabilities to the existing
Python project. See [the implementation guide](men-crypto-profile.md).

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
| Separate visual indicator and backtest strategy | `analysis/indicator_study.py` and `backtesting/` are separate Python components | Separate Python indicator and simulator now share one profile implementation |
| Pine Script v6 indicator and strategy deliverables | Current application uses Python/React and Lightweight Charts | Python equivalents selected; no Pine packaging |
| 15m main view and 1h confirmation | Quick 15m/30m/1h/4h controls; live 15m studies request 1h and 4h context | Implemented in app; actual BTC 15m history missing |
| EMA50/200, RSI14, MACD12/26/9, VWAP, ATR14, volume SMA20, ADX14 | Calculated, documented and available on charts | Implemented |
| Exact requested LONG/SHORT conjunction | New Men Crypto profile enforces EMA50/200, VWAP, RSI/MACD, ADX, relative volume and confirmed higher trend | Implemented; old experimental profile remains distinct |
| VWAP mandatory for directional signals | New Men Crypto profile requires close above/below complete-session UTC VWAP | Implemented |
| Higher trend required by the historical arrow rule | Men Crypto arrows join higher trend at each signal close; experimental arrows remain single-timeframe | Implemented per profile |
| Current candidates with higher context | Complete report engine applies context and freshness rules; alignment follows preferences | Implemented, live operation not verified successfully |
| Clear BUY/SELL labels and automatic drawings | Past arrows, EMA/Bollinger/VWAP, swing/structure/zone/trendline drawings | Implemented and visually checked with real archived data |
| ATR stop and 1:2 reward/risk | Past references draw 1R/2R/3R; simulator default reward multiple is 2 | Implemented as hypothetical references/simulation |
| Equity and stop-distance position sizing | Shared risk manager accounts for stop distance and modeled costs | Implemented |
| Default risk 0.5% | Default is 0.25%, with a hard maximum of 0.5% | Intentional difference; no claim of superior performance |
| Maximum exposure setting | Simulator exposure percentage is configurable up to 100% including entry fees | Implemented |
| One open strategy position | Simulator tracks a single virtual position | Implemented in simulation; visual marker spacing is not position tracking |
| Optional break-even stop | Configurable R trigger; adjustment active next bar | Implemented in simulation |
| Optional trailing stop | Configurable ATR distance and activation R; never loosens | Implemented in simulation |
| Dashboard trend, momentum and reference prices | Available through scanner, reports, study page and planner | Implemented across pages rather than one TradingView table |
| Current position status | Backtests track virtual positions; application does not read exchange account positions | New window-local hypothetical position tracker is explicit; real exchange position tracking remains excluded |
| BUY/SELL and condition alerts | Dashboard stores classification, invalidation, liquidity, volatility, funding and stale-data changes | New confirmed-candle study events are deduplicated; fresh live candidate gates apply |
| SL-hit/TP-hit alerts | Shared simulator tracks virtual positions and records virtual stop/target events | Implemented; no real fill is claimed |
| Same rules for indicator and strategy | New indicator strategy reuses chart profile function, rule settings and causal higher joins | Signal parity is tested on identical data |
| Fees, slippage and funding | Explicit simulation assumptions and historical funding option; public data required | Implemented; reliable live data loading remains blocked |
| Trades, win rate, profit factor, net result, drawdown | Reported by simulator, with sampling limits | Implemented |
| Average winning and losing trade | Separate average winning/losing trade and average net R now accompany existing metrics | Implemented |
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

## Remaining scope and verification limits

The shared profile, simulator controls, hypothetical tracker and closed-candle
notification paths are implemented in the Python app. The user's clarification
keeps development in this existing project; Pine v6 files are not claimed as
Python deliverables. There is no exchange execution integration.

The lower 0.25% default remains an intentional risk-policy difference; 0.5% is
selectable within the existing hard ceiling. Raw volume is participation rather
than proof of buying/selling pressure. One selected higher timeframe (1h) can
match the prompt's configuration; the default 15m view additionally offers 4h.

Reliable live provider access, real 15m history and adequate primary/context
samples remain unresolved operational requirements. The current 399-candle BTC
archive is enough for a study, but below the simulator's 400-bar validation
minimum. Historical tracker metrics are not a substitute for held-out and
walk-forward validation. Unit/browser tests verify software behavior and do
not prove profitable or superior trading performance.
