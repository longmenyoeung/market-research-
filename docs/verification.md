# Verification record

## Dashboard startup without redundant live discovery - 2026-10-31 (Asia/Bangkok)

The previous deadline change still left a failing live `/markets` request on
every page load. Startup now initializes its contract list from scanner state.
Scanner responses include `universe_symbols` before filters, so the picker keeps
the complete universe even when a filter hides all table rows. An idle scanner
still starts an initial background scan; a cached scan retains its historical
warning and can be refreshed through the existing Retry public data action.
The strict `/markets` endpoint retains its live-only behavior.

| Check | Observed result |
| --- | --- |
| Scanner backend regression suite | 6 passed, 1 non-failing Starlette TestClient warning |
| Frontend unit suite | 14 passed |
| Browser suite | 15 passed, including no startup discovery request and filter-independent contract picker |
| Production build | Passed |
| Real built dashboard on port 8000 | 522 saved symbols and picker entries; BTC archived candles rendered |
| Real browser after 16 seconds of polling | Zero markets requests, zero HTTP error responses, zero JavaScript page errors |

The updated backend and frontend are running locally. Historical charts remain
explicitly labeled and have no current entry or target levels. This fixes the
redundant startup failure; it does not establish live provider connectivity.

## Backend discovery deadline and recovery - 2026-10-31 (Asia/Bangkok)

The original running backend returned `/api/v1/markets` 504 after 31.52 seconds.
Code inspection traced the response to the public adapter's `upstream_timeout`
mapping, with three HTTP attempts, rather than a database or Uvicorn timeout.
Discovery now shares one bounded load across its callers, with a configurable
15-second total deadline and a 30-second pause after connection failures.
Requests during the pause return 503 `discovery_cooldown` and `Retry-After`.
Provider access blocks, rate limits and metadata freshness rules still apply.

| Check | Observed result |
| --- | --- |
| Full backend suite | 222 passed, 1 non-failing Starlette TestClient warning |
| Real markets request after backend restart | 504 in 15.156 seconds, Retry-After 30 |
| Immediate repeated markets request | 503 discovery_cooldown in 0.016 seconds |
| Immediate analysis request | 503 discovery_cooldown in under 0.001 seconds |
| Health | 200 in 0.063 seconds |
| Real saved BTCUSDT 1h archive | 200 in 0.031 seconds, 399 historical candles |

Mock tests verify cancellation of stalled work, concurrent request sharing,
cooldown expiry, recovery to fresh discovery and cache reuse, and API retry
headers. No fake data was used in the real endpoint checks. Public Binance
connectivity remains unavailable in this observation; a shorter timeout does
not repair that external connection. The updated backend is running on port 8000.

## Analysis timeout and archived chart - 2026-10-31 (Asia/Bangkok)

The local backend returned health and the cached scanner successfully, but
live contract discovery timed out. The scanner had 522 saved contracts and no
usable current analyses. A direct archive request returned 399 actual BTCUSDT
1h candles with `source=local_archive`, `live_data=false`, and historical quality.
The chart now opens those saved candles without a live analysis request when
the scanner is cached. If a live analysis request times out, it displays the
saved chart when available and preserves the timeout message. Archived charts
contain candles and volume only; current analysis, entry, stop and target
drawings remain unavailable.

| Check | Observed result |
| --- | --- |
| Backend suite | 217 passed, 1 non-failing Starlette TestClient warning |
| Frontend unit suite | 14 passed |
| Browser suite | 14 passed, including cached-universe selection and simulated analysis 504 |
| Production build and local static bundle | Passed; bundle served over port 8000 |

The provider timeout still prevents live analysis at this moment. Archive
availability varies by contract and timeframe, and saved candles are not a
current market quote.

## Browser offline error diagnosis — 2026-10-31 (Asia/Bangkok)

Both local services responded during diagnosis: `127.0.0.1:8000/health`,
`127.0.0.1:5173/health` and the scanner through Vite all returned HTTP 200.
A fresh Chromium session loaded the development dashboard without failed
requests or JavaScript errors. Simulating browser Offline mode reproduced the
exact `net::ERR_INTERNET_DISCONNECTED` failure on a loopback request.

The frontend now reports a clear connection error after a failed fetch and
pauses repeated background reads for 30 seconds while the browser remains
offline. It still attempts local requests when the OS reports no internet, so
an available loopback service stays usable. This cannot change Chrome's
dinosaur error page, which appears before application JavaScript can run.

| Check | Observed result |
| --- | --- |
| Frontend unit suite | 14 passed |
| Browser suite | 12 passed, including simulated offline behavior |
| Production build | Passed |

## LONG/SHORT chart drawings and offline price map — 2026-10-30 (Asia/Bangkok)

Fresh reports now provide a Both/LONG/SHORT/Hide chart control. The selected
scenario draws its hypothetical entry region, stop reference, 1R/2R/3R
take-profit lines, and risk/reward regions. The backend supplies the price
coordinates, source timeframe and deterministic rule. Stale, incomplete,
expired or NO TRADE reports do not display planning drawings. The manual Trade
planner also draws an offline LONG or SHORT price map from validated,
user-supplied prices; it contains no market candles or exchange action.

| Check | Observed result |
| --- | --- |
| Full backend suite | 217 passed, 1 non-failing Starlette TestClient warning |
| Frontend unit suite | 11 passed |
| Browser suite | 11 passed, including both chart sides and offline planner diagrams |
| Production build | Passed |
| Visual inspection | Synthetic test charts for Both, LONG, SHORT and offline planner rendered with labeled levels |
| Actual local planner API and browser | Hypothetical LONG inputs returned `hypothetical_only=true`; the built dashboard rendered the corresponding offline price map without browser errors |

Synthetic browser fixtures are explicitly test-only. The workstation's live
Binance public-data route remains intermittent, so these tests do not prove
that a current exchange-derived scenario is available at the time of viewing.

## Live REST recovery and stale-snapshot fallback — 2026-10-30 (Asia/Bangkok)

This is the latest workstation result. Official Binance public REST access was
intermittent. A successful discovery returned **522 active USDT-margined
perpetual contracts**. The scanner processed and archived all 522. A separate
BTCUSDT 1h analysis used 400 actual candles, marked the candle input usable,
and returned `WAIT`. This observation is not a performance claim or trade advice.
Later discovery requests timed out again. Public WebSocket access failed during
the opening handshake, so exchange event streaming is still unverified here.

The selected chart now labels its 15-second REST candle snapshot fallback and
does not treat an open candle as a confirmed signal. After a backend restart,
the archived 522-contract scan loaded immediately as **cached**: 522 rows,
zero usable current analyses, every row `stale` and `NO TRADE`, and candidate
scores removed. The actual built dashboard showed all 522 scanner rows in a
headless browser with no page errors. Scanner GET no longer blocks on an
upstream quote timeout. An actual retry ended with `upstream_timeout` and
retained all 522 stale rows rather than returning an empty market view.

| Check | Observed result |
| --- | --- |
| Full backend suite | 216 passed, 1 non-failing Starlette TestClient deprecation warning |
| Frontend unit suite | 8 passed |
| Browser suite | 9 passed, including REST fallback and stale archive labeling |
| Production build | Passed |
| Local health | HTTP 200; `trading_enabled=false`; `execution_endpoints=none` |
| Local scanner after restart | HTTP 200; 522 cached rows, all non-actionable |

No alternate exchange host, DNS change, proxy, desktop-app credential access,
or geographic workaround was used. Public data access does not establish
futures eligibility in Cambodia. The older diagnosis below records an earlier
outage; its zero-record observation no longer describes the current archive.

## Local provider access diagnosis — 2026-10-30 (Asia/Bangkok)

The current workstation displays the dashboard, but its archive contains zero
market symbols, candles, reports and snapshots. A direct public Binance discovery
request times out. Independent read-only checks to Bybit and OKX public market
time endpoints also time out, while `https://example.com` returns HTTP 200.

Windows DNS currently resolves `fapi.binance.com`, `api.bybit.com` and
`www.okx.com` to the same address, `203.190.164.182`. A TCP connection to that
address on port 443 fails. The local hosts file has no entry for those names;
WinHTTP and the current user have no configured proxy. The matching address is
evidence of a DNS or network policy issue, not proof of its cause or of geographic
or account eligibility. No DNS changes, alternate exchange hosts, proxy or
restriction workaround were attempted. Live scanner acceptance still requires a
permitted network path to a supported public data source.

## Permanent manual-only boundary and local delivery - 2026-10-30 (Asia/Bangkok)

This entry supersedes current-state claims in the historical records below. The
former execution arming flag/gate and kill-switch routes have been removed.
Trading is permanently disabled; legacy MCA_TRADING_ENABLED=true is rejected.
False remains accepted for existing .env compatibility. No order/execution or
private-account functionality exists.

| Check | Observed result |
| --- | --- |
| Full offline backend suite | **214 passed**, 1 non-failing Starlette TestClient deprecation warning, 47.73 s |
| Stream suite after checking current official routing docs | **16 passed**, including routed URL for all seven intervals and receive-only/redirect/proxy boundaries |
| Frontend unit tests | **8 passed** |
| Production frontend build | Successful; 1594 modules, JS 469.43 kB / gzip 148.93 kB; 4.41 s |
| Mocked Chromium desktop/mobile flows | **6 passed**, 14.7 s; includes application-key lock/unlock and evidence-run cancellation; no live data substituted |
| Real Uvicorn process smoke | Passed; built dashboard HTTP 200, permanent analysis-only health, cost-aware planner, historical empty archive, invalid local WebSocket subscription rejected |
| Smoke cleanup | Owned process tree stopped and temporary database removed; no provider request was needed |
| pip check | No broken requirements found |
| npm audit --omit=dev | 0 vulnerabilities reported for production dependencies |
| Synthetic CPU benchmark | 25 iterations, 400 bars, 53 indicators plus structure; median 157.18 ms, p95 167.31 ms, total 3.97 s |
| Synthetic 500-symbol platform benchmark | Cold 214.196 s, warm 20.309 s, quote refresh 0.304 s; all 500 visible, 50 warnings retained, zero warm candle downloads, 91.39 MiB cached DataFrames |
| Live diagnostic with --stream | Discovery returned upstream_timeout; candles/derivatives/stream not attempted after failed discovery |
| GitHub Actions | Backend, frontend, browser and real local-process smoke configured; not executed remotely |

The CPU benchmark excludes network, database and browser costs. The 500-symbol
platform benchmark includes local SQLite/scanner work but uses a zero-latency
synthetic provider and excludes total-process/browser memory. Neither is an exchange-
throughput or production capacity certification. Scanner snapshots can expire while
provider-paced full-market work is still running.

Additional regression coverage: environment/configuration cannot enable trading;
all-API/WebSocket application-key authentication occurs before provider work;
failed-auth throttling, trusted hosts, body limits and security headers are enforced;
the browser key remains memory-only; shared evidence replay is prefix-causal and
future-mutation invariant; historical funding settlement marks are required and
inclusive pagination preserves a second rate type at one timestamp; local backtest
cancellation is persisted; stale context bars lose their vote; scanner rolling
windows, calculation reuse and quote-only refresh avoid unnecessary candle calls;
cached reports expire at their required source deadlines and lose current price
references; closed candles invalidate only affected-market reports; stale rows
notify once even when their report was generated recently; invalid bid/ask/price/
spread/timestamp data creates no entry; nested maximum indicator warm-ups receive
sufficient history; incompatible timeframe roles fail validation; stale/future/OI
interior samples are rejected; equal pivots are EH/EL rather than lower pivots;
no-trade backtests preserve null selection scores, retain requested parameters and
persist successfully; extreme decimal exponents return 422 before calculation;
React suppresses expired candidate assessments and clears incompatible context
choices when the primary timeframe changes.

Current WebSocket routing was checked against the
[official market-stream catalog](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/market)
and [migration notice](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Important-WebSocket-Change-Notice).
The connector uses wss://fstream.binance.com/market/ws/<symbol>@kline_<interval>.
The earlier historical record claiming /ws was canonical is superseded. This is
a documented path correction on the same official host, not an access workaround,
and it has not been validated against a real provider connection here.

README and method/API/drawing/backtesting/deployment documents now describe the
implemented roadmap and its remaining external acceptance limits. The local
launcher is scripts/run_local.py. There is no Git remote or public deployment.
Cambodia futures eligibility is still independently unverified. Health reports
process liveness and deliberately never claims provider connectivity.

The smoke script stops only processes it creates. Any user-owned development
server is left untouched. The supplied browser fixture remains explicitly
synthetic and is never loaded by production code.

Two earlier smoke attempts passed their HTTP/WebSocket checks but encountered
Windows file locks during temporary-directory cleanup. The smoke script was fixed
to stop its entire owned Windows launcher/interpreter tree before cleanup; subsequent
runs completed with exit code 0 and removed their temporary data. Automatic approval
review blocked later deletion of the two earlier leftover directories with
"blocked by policy", including an attempt limited to individually verified files.
Those directories remain outside the project at:

- C:/Users/User/AppData/Local/Temp/mca-local-smoke-ks9t8pgh
- C:/Users/User/AppData/Local/Temp/mca-local-smoke-qe5idtz3

No additional deletion was attempted. The running user-owned dashboard at
http://127.0.0.1:8000 was checked separately: dashboard HTTP 200, health
analysis_only, trading_enabled=false, execution_endpoints=none. It was left running.

## Post-Phase 3 hardening — 2026-10-30 (Asia/Bangkok)

Environment: Windows, Python 3.11.2, existing `.venv`, Node/Vite toolchain. A baseline
commit (`42dabd5`, 87 files, 7,779 insertions) was created before this pass.

| Check | Observed result |
| --- | --- |
| Full offline suite (`python -m pytest -q`) | 166 passed in 12.24 seconds |
| `pip check` | No broken requirements found |
| Frontend unit tests (`npm test`) | 6 passed (vitest 5.0.5) |
| Frontend production build (`vite build`) | 1593 modules transformed; built in 3.77 s |
| Mocked Chromium flows (`npx playwright test`) | 3 passed in 5.0 s |
| Live provider diagnostics | Blocked by local DNS/network; see below |
| GitHub Actions workflow | Added; not yet executed on GitHub |

New regression coverage in this pass: the concrete adapter satisfies the complete
runtime-checkable `MarketDataProvider` Protocol and exposes no order/cancel methods;
first-observation silence plus one-time classification, liquidity, volatility, funding
and retest notifications; candidate invalidation disclosure; stale scan downgrade
to `NO TRADE` with a single stale notification across repeated reads; provider failures
downgrading a candidate without crashing notification handling; and open-interest
context that fails closed when history covers under 20 hours, is sparse, malformed or
unordered, and carries no numeric score weight, with report rendering covered for both
new and older saved records.

Live diagnosis: `fapi.binance.com` resolved through `d2ukl3c6tymv7q.cloudfront.net` to
a private address (`10.1.110.100`/`10.1.110.101`) and timed out, while
`https://example.com` returned HTTP 200. No proxy environment variables and no Binance
hosts-file entry exist. This workstation's DNS path therefore cannot reach the official
provider endpoint. This is **not** a determination of exchange availability or of
Cambodia legal eligibility. No alternate host, proxy, DoH resolver or bypass was used,
and no synthetic data was substituted. Live discovery, candles and streams remain
unverified here.

The CI workflow installs the lockfiles, runs the backend suite, frontend unit tests,
production build and mocked Chromium smoke tests. Mocked browser flows do not prove
live provider connectivity.

## Phase 3 review fixes — 2026-10-30 (Asia/Bangkok)

Environment: Windows, Python 3.11.2, Node/Vite toolchain already installed. The
dependency lock was regenerated from the working environment and now includes numpy
2.4.6, pandas 2.3.3, ta 0.11.0, python-dateutil, pytz and tzdata in addition to the web
and test stack.

| Check | Observed result |
| --- | --- |
| Full offline suite (`python -m pytest`) | 155 passed in 22.02 seconds |
| `pip check` | No broken requirements found |
| Frontend unit tests (`npm test`) | 4 passed (vitest 5.0.5) |
| Frontend production build (`vite build`) | 1593 modules transformed; built in 3.13 s |
| Live provider diagnostics | Not re-run in this pass; prior timeout cause remains unknown |

The added regression coverage includes: the 0.25%/0.5% per-trade risk rails, 1% daily
realized-loss stop and its blocked-entry counter, half-spread plus slippage charging,
single-charge sizing, shared RiskManager sizing, kill-switch state and refusal to arm
without the environment flag, health disclosure, API-key and loopback mutation access,
CORS preflight, archive-write failure while live reads still succeed, provider access
block propagation from optional metrics, Retry-After clamping, FVG fill status labeled
as of the latest bar, divergence using the configured RSI period, and the corrected
canonical Binance stream URL.

No live exchange connectivity was re-established, so this phase does not prove provider
availability, the real stream endpoint or legal eligibility. The endpoint path follows
the official USDⓈ-M WebSocket documentation and is covered by URL/redirect boundary
tests only.

## Phase 2 — 2026-10-29 (Asia/Bangkok)

Environment: Windows, Python 3.11.2. Added websockets 16.1.1; exact versions are
in backend/requirements.lock.txt.

| Check | Observed result |
| --- | --- |
| Full offline suite | 121 passed in 3.28 seconds |
| Isolated-clock boundary-test refinement | Passed in a subsequent targeted run |
| pip check | No broken requirements found |
| Actual Uvicorn process, 127.0.0.1:8765 | Started successfully, version 0.2.0 |
| HTTP health | 200; analysis_only; connectivity not_checked |
| HTTP archive with temporary empty database | 200; empty candles, historical-only source, live_data=false |
| HTTP OpenAPI | Health, markets, candles and local-history GET operations only |
| Actual local WebSocket | Handshake succeeded; invalid timeframe returned invalid_subscription |
| Live pipeline diagnostic with --stream | Discovery timed out; candles and Binance stream were not attempted |

The temporary server was stopped and its test database cleaned up. No server is
left running. No mock data was substituted during live checks. Timeout cause is
unknown; no geographic eligibility or availability conclusion is drawn.

New regression coverage: malformed OHLCV/timestamps, gaps, stale/historical data,
clock skew, conservative finality across a candle boundary, query allowlists,
unknown contracts, cache coalescing/expiry/cancellation/in-flight invalidation,
SQLite persistence/idempotency/ranges, shared streams, reconnect status, silent
and duplicate-event staleness, final-candle monotonicity, queue drops, subscription
limits, Origin checks, client-command rejection and access-denial handling.

The preexisting Starlette TestClient HTTPX deprecation warning remains non-failing
and unsuppressed. Offline mocks do not prove live exchange connectivity. Complete
backfill, multi-worker deployment and load testing remain outside this phase.

## Phase 1 (historical record)

Verified on 2026-10-29 (Asia/Bangkok), Windows, Python 3.11.2, using the local
`.venv`. Exact installed dependencies are in `backend/requirements.lock.txt`.

| Check | Observed result |
| --- | --- |
| `python -m pytest` from backend | 68 passed in 1.12 seconds |
| `python -m pip check` | No broken requirements found |
| Actual Uvicorn startup on 127.0.0.1:8765 | Successful |
| HTTP GET /health against Uvicorn | 200, analysis_only, exchange_connectivity not_checked |
| HTTP GET /openapi.json against Uvicorn | 200; only /health and /api/v1/markets application paths |
| Live public discovery diagnostic | Failed with upstream_timeout; no live contract count obtained |

The temporary smoke-test server was stopped after verification. No development
server is left running. A local Git repository was initialized; no commit or remote
publication was made.

The live diagnostic used the official public URL, a 10-second HTTPX timeout and
zero retries. A timeout does not identify its cause and does not establish whether
the provider is geographically restricted. There was no alternate-host/proxy
attempt, and no mock data was substituted. Live discovery remains unverified in
this environment. Offline adapter and route tests validate discovery against
explicitly synthetic fixtures and all listed failure paths.

One non-failing dependency warning remains: Starlette deprecates the HTTPX backend
used by its TestClient in favor of httpx2. The tested version still supports HTTPX;
the warning is not suppressed. A future test-client migration can be evaluated
separately. Production outbound calls use the requested HTTPX library.

An initial test was attempted before dependency installation finished and failed
to import Pydantic. After installation, the first full suite found a safety-test
compatibility issue with FastAPI's lazy included-router representation. The route
inspection now expands included routes, including hidden routes, while preserving
the exact read-only route allowlist. The final full suite passed as reported above.

Coverage includes all four discovery predicates, dynamic symbols, sorted full
results, empty/invalid metadata, duplicate symbols, network errors, bounded retries,
HTTP failure mapping, Retry-After formats, concurrent cooldown callers, health
isolation, API serialization, cleanup, forbidden request methods/paths/credentials,
and invalid or unsafe configuration. This is Phase 1 behavioral verification, not
a production security audit, performance test, or trading-eligibility determination.
