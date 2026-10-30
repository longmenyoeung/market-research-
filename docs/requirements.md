# Requirements and delivery status

The original project roadmap is implemented as a local research application. This
matrix distinguishes implemented software from acceptance checks that require
external services or future production infrastructure.

| Phase | Included implementation | Verification and limits |
| --- | --- | --- |
| 1. Foundation/discovery | Typed configuration, FastAPI health, public Binance adapter, dynamic active USDT perpetual filtering, request guard | Offline filtering/error/safety tests; actual local process smoke check. Real discovery times out here. |
| 2. Market data | Seven candle timeframes, historical pagination, validation, gap/finality/freshness metadata, caches, SQLite archive, shared candle streams/reconnects | Mock transport and stream regressions; local WebSocket handshake check. Real provider REST/stream acceptance is unverified. |
| 3. Scanner | Full eligible universe, quote/volume/spread/ATR/trend/momentum/structure/evidence/regime/quality columns, filters/sorting/watchlist, warnings, progressive work, rolling request-weight budget and incremental candle/calculation reuse | A synthetic 500-symbol benchmark retained every market and downloaded no candles on its warm pass; cold 214.196 s, warm 20.309 s, quote refresh 0.304 s. Zero network latency means this is not real-provider scale certification. |
| 4. Indicators/MTF | 53 configurable series, formula catalog, warm-up/null handling, primary/higher/lower roles, conflict disclosure | Prefix-causality and warm-up tests, nested maximum-period coverage, invalid timeframe validation. Methods are documented in indicators.md. |
| 5. Drawings/structure | Confirmed pivots/HH/HL/LH/LL, equal pivots, BOS/CHoCH, zones, retests, sweeps, FVG, channels, Fibonacci, prior periods, patterns and divergence | Deterministic rules and confirmation timestamps. Sweeps/divergence/setup implications remain provisional. True volume profile is unavailable without trade-at-price inputs. |
| 6. Evidence/reports | Independent LONG/SHORT scenarios, five capped evidence groups, experimental weights, WAIT/NO TRADE gates, costs/risk illustrations, complete reports, JSON export | Expired/invalid quotes suppress current entry references and classification. Optional OI context carries no score weight and rejects stale/malformed/short/sparse data. No profitability claim. |
| 7. Manual planner | Decimal cost-aware quantity sizing, risk/notional/margin/gross/net reward-risk, funding assumptions, optional contract filters | LONG/SHORT validation, cost budget and quantity-step tests; actual local HTTP check. No liquidation or maintenance-tier model. |
| 8. Dashboard/state | React overview/scanner/chart/report/planner/watchlist/history/settings/validation, dashboard-only notifications, SQLite preferences/history | Desktop/mobile Chromium flows and React rendering tests. Saved reports remain historical. Bundled browser is intended for loopback use. |
| 9. Historical validation | Causal replay of the shared LONG/SHORT assessor plus separate EMA baseline; prior-close/later-open timing, confirmed historical structure, aligned context closes, fees/spread/slippage, historical or scenario funding, development/holdout, sensitivity and expanding walk-forward | Prefix/future-mutation causality, funding-settlement, separation, cost, gap, empty-sample, cancellation and persistence tests. These validate software behavior, not evidence profitability. |
| 10. Safety/documentation/deployment | Permanent analysis-only boundary, optional all-API/WebSocket application-key gate, Host/Origin/body/auth throttles, safety regressions, locked dependencies, docs, launcher and GitHub Actions | Local tests/build/process/browser/security checks recorded in verification.md. No remote CI, public deployment, production identity, distributed load test or live-provider certification. |

## Mandatory invariants

- Never place, modify or cancel exchange orders or open/close actual positions.
- Never expose execution/arming routes, private account operations, order signing,
  exchange trading credentials or transaction-triggering notifications.
- Legacy MCA_TRADING_ENABLED=true must fail startup validation. Health always says
  analysis_only, trading_enabled=false and execution_endpoints=none.
- Use public allowlisted data only. Surface access restrictions and cooldowns;
  never bypass geographic restrictions, denied hosts, provider limits or controls.
- Keep every eligible market visible unless the user applies filters. Low liquidity
  and high volatility cause warnings rather than silent universe exclusions.
- Never invent missing prices, candles, order books, funding, OI or volume profile.
  Synthetic data belongs exclusively to tests and explicit developer benchmarks.
- Source timestamps govern current classifications. Expired/incomplete/invalid
  required data yields NO TRADE; cached reception timestamps are never refreshed
  to disguise old observations.
- Keep contradictory timeframes visible and correlated indicators in capped groups.
  Scores are neither win probabilities nor guarantees of profitability.
- Risk plans remain hypothetical. Current limits default to 0.25% and cap at 0.5%;
  the research backtester additionally blocks new entries after a 1% daily realized
  loss. These conventions do not prevent gaps or guarantee realized losses.
- Keep notifications inside the dashboard. Every trading decision/action is manual.

## Explicit acceptance items outside this environment

Cambodia futures legal/account eligibility requires independent verification. The
workstation's official Binance endpoint continues to time out; public-data access,
when available, still will not establish eligibility. Do not introduce alternate
hosts, proxies or DNS workarounds to make acceptance pass.

No remote has been configured and no code has been pushed. GitHub Actions is
configured but not remotely verified. Docker, Redis and PostgreSQL were optional
future choices in the specification; they are not included or claimed operational.
Production hosting, identity, multi-worker quotas and provider throughput require
separate implementation/verification before a public deployment.
