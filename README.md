# Manual Crypto Analysis

A local Python/FastAPI + React platform for researching USDT perpetual futures before a manual trading decision. It discovers contracts dynamically, scans the complete eligible universe, computes 53 indicator series, draws deterministic chart annotations, compares timeframes, generates independent LONG/SHORT scenarios, stores reports, calculates hypothetical risk and runs historical research simulations.

**The application is permanently analysis-only. It cannot place, modify or cancel orders or open or close positions. Trading cannot be enabled.** All exchange access uses public market-data endpoints and requires no exchange keys.

## Quick start (PowerShell)

From the project root, use Python 3.11 and Node.js 22.12+ (Node 24 also works):

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.lock.txt
npm --prefix frontend ci
.\.venv\Scripts\python.exe scripts\run_local.py --build
```

Open **http://127.0.0.1:8000** for the dashboard or **http://127.0.0.1:8000/docs** for the API. Stop with Ctrl+C. On subsequent starts, omit `--build` unless the frontend changed. One process serves the built dashboard and the API. The launcher binds only to loopback and uses one backend worker.

If the official Binance public-data connection fails, the dashboard shows the
last saved market scan when available. Those rows are explicitly stale and all
candidate classifications are disabled until fresh data succeeds. Select
**Retry public data** after the connection recovers. Follow the
[network check in deployment](docs/deployment.md#provider-failures-and-capacity).

The existing workspace already has its Python and Node dependencies installed. A fresh installation must still use the commands above. Optional configuration:

```powershell
Copy-Item backend\.env.example backend\.env
```

Do not overwrite an existing `.env`; merge settings instead. Defaults work without it. SQLite initializes at `backend/data/market_data.db`, independent of the current directory. No exchange secrets are needed. `MCA_API_KEY`, if configured, is solely a local-application access key for API clients; the bundled browser does not store it. Leave it unset for normal loopback dashboard use. Legacy `MCA_TRADING_ENABLED=false` is accepted for compatibility; `true` is rejected at startup.

For development, run these in separate terminals:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload
npm --prefix frontend run dev
```

The development dashboard opens at http://127.0.0.1:5173 and proxies API/WebSocket requests to port 8000.

## Use the platform

1. Open Overview. Discovery populates the full eligible universe and an initial scan runs in the background. Provider failures appear explicitly; no demonstration data replaces live data.
2. Open Market scanner. All eligible markets remain visible by default, including low-volume, wide-spread and volatile pairs. Set filters explicitly and inspect freshness before selecting a market.
3. Open a contract in Chart workspace and generate analysis. Select the primary timeframe, higher context and lower momentum; empty context choices use automatic defaults. Toggle indicator overlays and annotation groups. The **Both / LONG / SHORT / Hide** control draws hypothetical entry regions, stops, 1R/2R/3R take-profit levels and risk/reward shading when fresh source data supports a scenario. The drawings disappear when the report becomes stale or is classified NO TRADE. When the public WebSocket is unavailable, the selected chart uses labeled REST candle snapshots every 15 seconds; open candles remain provisional and REST does not provide a last-trade event timestamp.
4. Read Analysis report for both scenarios, evidence conflicts, confirmation/invalidation conditions, indicator formulas, derivatives context and data limitations. Scores are experimental alignment measures, not win probabilities.
5. Use Trade planner with hypothetical equity and your reference prices. It draws an offline LONG or SHORT price map from the validated entry, stop and take-profit inputs. Optional discovered symbols load current contract filters; blank symbols keep the calculator usable offline. Risk defaults to 0.25%, with a 0.5% per-plan cap. Stops are not guaranteed fills.
6. Save a Watchlist and inspect Report history. Historical classifications are labeled as snapshots. Notifications stay inside the dashboard.
7. Use Validation lab for a causal replay of the same grouped LONG/SHORT evidence assessor or for the independent EMA-crossover baseline. Both use development/held-out data, walk-forward evaluation and explicit cost assumptions; neither proves profitability.

The seven timeframes are 1m, 5m, 15m, 30m, 1h, 4h and 1d. Timestamps are stored in UTC and displayed in browser local time. Only confirmed candles enter indicators and scoring; current candles remain provisional on charts.

## Verify locally

```powershell
.\.venv\Scripts\python.exe -m pytest -q backend\tests -c backend\pytest.ini
.\.venv\Scripts\python.exe -m pip check
npm --prefix frontend test
npm --prefix frontend run build
.\.venv\Scripts\python.exe backend\scripts\smoke_local.py
```

Browser smoke tests (from `frontend`):

```powershell
npx playwright install chromium
npm run test:e2e
```

Tests use explicitly synthetic fixtures and mock transports. They cover discovery, provider errors, public-request safety, candles/finality/gaps, caches, SQLite, reconnects, source-data expiry, indicator causality, structure confirmation timing, scenario gating, cost-aware risk, funding-history validation, causal evidence replay, authentication, notifications and browser flows. The [verification record](docs/verification.md) distinguishes completed local checks from unverified live access.

Optional public-data diagnostic, run once without bypasses:

```powershell
.\.venv\Scripts\python.exe backend\scripts\live_market_data.py --stream
```

It uses zero REST retries and stops if discovery fails. On this workstation,
official public REST access was intermittent on **2026-10-30 (Asia/Bangkok)**:
one live check discovered 522 contracts and fetched BTC candles, while later
requests timed out. A real BTC report completed from 400 candles. The public
WebSocket opening handshake timed out, so live exchange streaming remains
unverified here. No alternate host, DNS workaround or proxy is used. Public-data
access never establishes Cambodian legal or account eligibility; that remains
separately unverified.

Example offline-compatible API calls:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
$body = @{side='LONG';equity=1000;risk_percent=0.25;entry=100;stop=95;target=110} | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8000/api/v1/planner -Method Post -ContentType 'application/json' -Body $body
```

The planner example contains hypothetical user-supplied prices. A successful health response reports process liveness, `analysis_only`, `trading_enabled=false`, `execution_endpoints=none` and `exchange_connectivity=not_checked`.

## Architecture and files

```text
backend/
  app/
    main.py                    lifespan, middleware, routes, built dashboard
    core/                      configuration, logging, public-request guard, risk limits
    adapters/                  read-only provider Protocol, Binance REST and streams
    market_data/               validation, bounded caches, archival service, stream hub
    schemas/                   validated contracts, candles and analysis settings
    indicators/engine.py       configurable formulas and method catalog
    analysis/                  structure, evidence scoring and hypothetical risk
    services/platform.py       progressive scanner, reports, watchlists and notifications
    storage/                   repository contracts, SQLite candles and JSON snapshots
    backtesting/               historical jobs, causal simulation and validation
    api/                       REST routes and receive-only local candle WebSocket
  tests/                       offline regression tests
  scripts/                     opt-in live checks, fixture/docs exporters and CPU benchmark
frontend/
  src/                       dashboard, scanner, chart, reports, planner and validation lab
  tests/                     explicitly synthetic browser fixtures and smoke tests
scripts/run_local.py         build and launch the loopback application
docs/                        contracts, methods, safety, deployment and verification
.github/workflows/ci.yml     locked backend, frontend and browser checks
```

Backend calculations remain independent of React. `MarketDataProvider` and the storage repository Protocols allow another read-only provider or future PostgreSQL storage implementation without changing analysis formulas. PostgreSQL, Redis and Docker are optional future deployment choices and are not installed by this application.

## Scope and practical limits

- The full local research workflow is implemented across roadmap phases 1-9. Phase 10 has local safety checks, documentation, CI configuration and local deployment instructions. External CI, production deployment and real-provider acceptance still require verification in a permitted, working environment.
- No order methods, private account APIs, signing logic, trading keys, execution gates or transaction-triggering alerts exist. The former kill-switch/arming API has been removed to honor the permanent manual-only requirement.
- Required quote and candle deadlines govern classifications, including cached reports. A book quote expires after 60 seconds and a ticker after 120 seconds. Source expiry downgrades current assessments to NO TRADE. Closed candles invalidate the affected symbol's cached reports; the full universe is not recalculated on every tick.
- Full-universe scans are progressive snapshots. A rolling request-weight budget, bounded concurrency and shared server-clock snapshot govern public REST calls. Unchanged candle windows and calculations are reused, and only due symbols refresh after a candle closes. In a zero-network synthetic 500-symbol benchmark the cold 1d scan took 214.196 s, warm rescan 20.309 s and quote-only refresh 0.304 s; all markets stayed visible and the warm pass downloaded no candles. This is not live-provider throughput certification, and rows can still expire during slow or restricted access.
- SQLite is for one local process. Cooldowns and stream coordination are process-local; use one worker. Retention periodically prunes snapshot tables to roughly 1,000 records each. Candle history contains only fetched confirmed REST pages and may have gaps. Back up the database while stopped.
- Optional missing metrics remain unavailable. Aggregate open interest provides contextual narration with no score weight; stale, sparse, malformed or insufficient history creates no inference. True volume profile is unavailable without trade-at-price data.
- The report risk illustration uses $1,000 hypothetical equity and 1x leverage. Its reference prices and projections are not exchange-ready instructions; use the planner to apply contract constraints. Targets are 1R/2R/3R projections. There is no maintenance-tier or liquidation-price model.
- Backtests model fees, spread and slippage. Historical mode fetches validated public funding events with their settlement mark prices; scenario mode uses an explicit hypothetical rate and never masquerades as observed history. The evidence strategy reuses the live assessor with historical clocks, confirmed pivots and close-time-aligned contexts. Liquidations are not modeled and profitability is never claimed. Current-contract selection introduces possible survivorship bias.
- The dashboard is intended for one local operator. Optional `MCA_API_KEY` authentication protects every API/WebSocket route and has a memory-only browser unlock screen, request throttling, body/host limits and security headers. It is not a production identity service; public hosting still needs a specified TLS/identity architecture. No Git remote is configured, so GitHub Actions has not run remotely.

Read [API contracts](docs/api-contracts.md), [architecture](docs/architecture.md), [indicator formulas](docs/indicators.md), [drawing rules](docs/drawing-rules.md), [backtesting methods](docs/backtesting.md), [safety policy](docs/safety-policy.md), [Cambodia eligibility boundary](docs/eligibility.md) and [deployment](docs/deployment.md).
