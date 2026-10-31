# API contracts — 1.0.0

Local base URL: http://127.0.0.1:8000. Vite dev proxies `/api`, `/health`, `/docs`,
`/openapi.json` and `/redoc` from http://127.0.0.1:5173. REST responses use
Cache-Control: no-store; internal caching is explicit in response metadata. REST
schemas are in /docs and /openapi.json. The WebSocket protocol is documented here.

## Application authentication and browser origins

`/health` stays public because it reports process liveness only. When
`MCA_API_KEY` is configured, every `/api` request and candle WebSocket requires it.
Without a configured key, POST/PUT mutations (settings, watchlist, scan start,
analysis persistence, planning and backtest submission) require loopback. The key
can be supplied by:

- `X-API-Key: <MCA_API_KEY>` for HTTP/native WebSocket clients, or
- WebSocket subprotocol `mca-auth.<base64url-utf8-key>` for browser WebSockets.

A missing/wrong key returns 401 with `WWW-Authenticate: ApiKey`; repeated failures
from one client return 429. A non-loopback mutation without a configured key returns
403. Browser `Origin` values must match
`MCA_WEBSOCKET_ORIGINS`; CORS preflight allows `Content-Type` and `X-API-Key` for
GET/POST/PUT/OPTIONS. No browser cookies or credentialed CORS sessions are used. Exchange credentials are never accepted.

## GET /health

Returns status=ok, version=1.0.0, mode=analysis_only, exchange_connectivity=not_checked,
`trading_enabled=false`, `execution_endpoints=none`, `application_key_required` and
an ISO UTC timestamp. No provider call.
Trading cannot be enabled by configuration or any API. Startup requires
a writable/compatible SQLite archive (schema version 4 with candle history).

## GET /api/v1/markets

Returns provider, fetched_at (original local UTC reception), timestamp_basis,
count, contracts, eligibility_notice, cache_hit and cache_age_seconds.
Discovery TTL defaults to 60 seconds.

Live discovery has a separate total deadline of 15 seconds by default
(`MCA_DISCOVERY_TIMEOUT_SECONDS`). It includes the adapter queue, pacing,
HTTP attempts and backoff. Expiry returns 504 `upstream_timeout`. After a
timeout or connection failure, discovery pauses for 30 seconds by default
(`MCA_DISCOVERY_FAILURE_COOLDOWN_SECONDS`). The first failure includes
`Retry-After`; requests during the pause return 503 `discovery_cooldown` with
the remaining retry period. Markets, candles and analysis share this state
and the same in-flight discovery. Expired metadata is never promoted to a
current contract universe. Provider access and rate-limit blocks still apply.

Contracts contain symbol, base_asset, quote_asset, margin_asset, contract_type and
status and available public contract `constraints`, sorted alphabetically. Every match has TRADING/PERPETUAL/USDT quote/USDT
margin. No liquidity filtering. This is metadata eligibility, not trading eligibility.

## GET /api/v1/candles

| Query | Validation |
| --- | --- |
| symbol | Required; 1–40 alphanumeric/underscore characters; normalized uppercase; discovered contract |
| timeframe | 1m, 5m, 15m, 30m, 1h (default), 4h, 1d |
| limit | 1–1500, default 500 |
| start_time | Optional inclusive candle-open lower bound, UTC milliseconds |
| end_time | Optional inclusive candle-open upper bound; >= start_time |

Unknown fields/invalid values return 422. Timestamps are bounded through
4102444800000 (2100-01-01 UTC). Each request is one page. Start forward pagination
with an explicit start_time, advance with next_start_time, and retain end_time.
The cursor is only a hint; it does not guarantee another page. A null cursor
suggests no continuation; recent provisional candles suppress it.

Response: provider, symbol, timeframe, fetched_at, server_time, time_basis,
candles, quality, next_start_time, cache_hit, cache_age_seconds.
server_time is exchange time extrapolated with monotonic elapsed time, explicitly
labeled exchange_clock_extrapolated; it is not the last trade's timestamp.
Cached reception timestamps are preserved.

Candle: open_time/close_time (UTC milliseconds); open/high/low/close/volume/
quote_volume/taker_buy_volume/taker_buy_quote_volume (decimal strings);
trades (integer); is_closed (boolean). Boundary-uncertain candles stay provisional.

Quality: status (usable/incomplete/stale/empty/historical), missing_intervals and
warnings. Explicit old end_time requests are historical rather than live-stale.
Usable means structural validity and recent period coverage, not indicator warm-up,
profitability, or proof of recent trades. Short batches and open candles carry
warnings; gaps are not filled. Candle cache TTL defaults to five seconds.

## GET /api/v1/history/candles

Same query fields; reads only the local archive without provider access. Formerly
discovered/delisted symbols remain inspectable. Returns symbol, timeframe,
source=local_archive, live_data=false, candles and quality.

Only confirmed REST pages previously fetched by the service are stored. Without
start_time, returns latest matching page; with start_time, earliest matching page.
Both are sorted ascending. This is an incomplete historical archive, never live data.

## Platform routes

All platform state is process-local SQLite (schema version 4). Mutations require the
auth described above.

### GET /api/v1/settings · PUT /api/v1/settings

Returns the validated `Preferences` document; PUT replaces it and resets the scanner
and report cache. Unknown fields are rejected. Fields:

- `indicators`: EMA/SMA/RSI/ATR/ADX/MACD/bands/Ichimoku periods plus `trend_fast`,
  `trend_slow`, `trend_anchor`, `momentum_period`, `volatility_period`,
  `percentile_period`, `swing_window`.
- `weights`: `trend`, `structure`, `momentum`, `volume`, `context` (at least one > 0).
- `candidate_score` 50–100; `minimum_volume`; `maximum_spread_bps` 1–1000;
  `high_volatility_atr_percent`; `funding_warning`; `fee_bps`; `slippage_bps`.
- `risk_percent` default **0.25**, hard maximum **0.5**; values above the rail are
  rejected, never clamped silently.
- `require_higher_alignment`; `scan_concurrency` 1–8.

### GET /api/v1/watchlist · PUT /api/v1/watchlist

Symbol syntax is validated and normalized uppercase; a timeframe is stored. A saved symbol may subsequently delist; generating live analysis still requires current discovery.
Watchlists are research shortlists, not recommendations.

### POST /api/v1/scanner/refresh · GET /api/v1/scanner

POST accepts `{timeframe}` and returns 202 with scan state. One scan at a time; a
second request for the same timeframe returns the existing state, while a different timeframe cancels and replaces the current scan. GET accepts filters: `minimum_volume`,
`maximum_spread`, `minimum_long`, `minimum_short`, `trend`, `setup`, `macd`,
`quality`, `rsi_min`, `rsi_max`, `adx_min`, `minimum_volatility`,
`maximum_volatility`, `watchlist_only`, `sort`, `descending`. Every discovered market
remains visible by default; filters are applied only on request.

The response includes `universe_symbols`, the complete symbol list for this scan
before user filters. The dashboard uses it for contract pickers and initializes
from the scanner rather than making a separate live `/markets` request on load.
When scan status is `cached`, these are saved observations and current contract
eligibility remains unverified. The strict `/markets` API still performs live
discovery and can report provider failures to clients that explicitly request it.

Rows disclose price, quote volume, spread, liquidity warning, ATR%, volatility
status, timeframe, EMA trend, RSI, MACD, ADX, structure, LONG/SHORT evidence scores,
regime, data freshness, quality and classification. None of these is a win
probability or order instruction.

### POST /api/v1/analysis

The companion `POST /api/v1/indicator-study` builds confirmed-candle marker
studies and chart drawings. See [indicator-study.md](indicator-study.md) for its
validated source/rule inputs, historical timing and current-data gates.
Inputs additionally support `profile` (`experimental` or `men_crypto`), explicit
strictly higher `higher_timeframes`, `simulation` options and `dashboard_alerts`.
The response includes a hypothetical `paper_tracker`; it is not account state.
Backtest requests now support `strategy=indicator`, `signal_profile`,
`study_rules`, `data_source` (`live` or `archive`), `max_exposure_percent`,
`break_even`, `break_even_at_r`, `trailing_stop`, `trailing_atr` and
`trailing_activation_r`. Archive backtests require scenario funding explicitly.

Body: `symbol`, `timeframe`, up to three `higher_timeframes`, optional
`lower_timeframe`, `include_derivatives`. Returns the report and persists an
immutable history record when a fresh calculation is made. Identical requests reuse the report cache for up to 20 seconds.
`valid_until_ms` is the earliest required quote/candle/context deadline. Every cached read
rechecks this deadline and downgrades expired results to NO TRADE, removing proposed
price references from the current response. Historical records retain their original assessment.
A confirmed candle invalidates that symbol's cached reports. Higher timeframes must exceed
the primary; lower must be below it. An empty higher list selects automatic context. Classification
is `NO TRADE` unless data gates, context, liquidity, cost and minimum net reward/risk
(1.5 after modelled costs) pass.

### GET /api/v1/history/reports · GET /api/v1/notifications

History supports `symbol`, `timeframe`, `limit` 1–200. Notifications are dashboard-only
change records (classification, liquidity, volatility, funding, retest); no external
delivery exists.

### POST /api/v1/planner

Body is a hypothetical `TradePlan` (side LONG/SHORT, equity, entry, stop, target,
risk_percent ≤ 0.5, leverage ≤ 125, fee/slippage bps, adverse funding, optional
contract constraints). `risk_percent` defaults to 0.25. The response always includes
`hypothetical_only=true`, risk budget, quantity, notional, margin estimate, costs,
net reward/risk, `meets_minimum_net_reward_to_risk`, `risk_rails` and constraint
violations. No order is placed or staged.

Planner decimals support up to 18 fractional digits within 36 total digits;
unrepresentable exponent/precision inputs return 422 before calculation. Inputs
also retain their individual numeric bounds and side/price/tick validation.

### POST /api/v1/backtests · GET /api/v1/backtests · GET /api/v1/backtests/{run_id} · POST /api/v1/backtests/{run_id}/cancel

POST returns 202 and a run id; GET returns status/results/history; cancel stops local
calculation only. `strategy=evidence` replays the shared grouped assessor using
historical clocks, confirmed structure and context bars closed by that time.
`strategy=ema` runs the separate crossover baseline. Simulation uses closed candles
only, later-open fills from prior-close signals, stop-first
intrabar convention, fees per side, and **half of `spread_bps` plus `slippage_bps`
per side**. Sizing uses the shared RiskManager. Realized losses stop new entries for
the rest of a UTC day at the 1% daily loss rail; the count is reported as
`blocked_by_daily_loss_cap`. Walk-forward and holdout selection use
`net_pnl / max(max_drawdown_percent, 0.25)` with at least five trades; the holdout
parameters are disclosed. Selection scores are null when there are fewer than five
training trades. Evidence sensitivity varies the candidate threshold; EMA sensitivity
varies the ATR stop. Historical funding requires actual event rates and settlement
marks; scenario funding is explicitly modeled. If every variant is ineligible, the
requested setting is retained and `selection_status` discloses insufficient evidence.
Results are research, not profitability evidence.

## WebSocket /api/v1/ws/candles/{symbol}?timeframe=1m

Default timeframe is 1h. Symbol must be discovered. No client messages are accepted
or forwarded. A client command closes with 1008. Invalid/duplicate query parameters
produce type=error/code=invalid_subscription then 1008. Browser Origins must match
configured origins; non-browser clients without Origin are supported.
When application authentication is enabled, the key is validated before any upstream
stream is opened.

Messages:

- type=status: connecting, stale, blocked or error; may include code, terminal and
  resync_required. Terminal status closes with 1013.
- type=candle: symbol, timeframe, event_time (provider UTC milliseconds),
  received_at (ISO UTC), candle, stale, price_basis=candle_last_trade.
- type=error: code/message/retry_after_seconds for subscription setup failures.

Status/candle messages include sequence, resync_generation and dropped_updates.
Sequence is local to the shared channel, not an exchange sequence. Initial
generation is 1; reconnects/gaps increase it. Dropped updates are cumulative per
subscriber. Counters expose known loss; they cannot guarantee detection of all
provider-side omissions. A new connection starts a fresh synchronization context.

Client synchronization:

1. Subscribe and buffer candle events.
2. Fetch REST candles for the required range; index by open_time.
3. Apply buffered updates in provider event-time order; reject older updates and
   never overwrite a final candle with a provisional one.
4. Refresh REST history after resync_generation/dropped_updates changes or
   stale/reconnecting status. Long outages may require pagination.
5. Keep data marked stale/incomplete until synchronization succeeds.

The diagnostic watch_candles.py prints messages only; it is not a chart
synchronizer. The browser refreshes selected-market REST analysis after closed candles and known stream loss.
Automatic complete bulk backfill after a long outage is not implemented.

## Errors and read-only boundary

Structured provider/storage errors:
{"error":{"code":"...","message":"...","retry_after_seconds":null}}.
When present, retry seconds also appear in HTTP Retry-After and are clamped by
`MCA_RETRY_AFTER_MAX_SECONDS` (default 86400).

| HTTP | Code | Meaning |
| --- | --- | --- |
| 401 | FastAPI detail | Missing/invalid application key on a protected API route |
| 403 | FastAPI detail / origin_denied | Non-loopback mutation without a configured key; disallowed browser Origin |
| 404 | unknown_contract | Not currently discovered |
| 404 | not_found | Unknown backtest run |
| 422 | FastAPI validation detail | Invalid request input, including risk above the rail |
| 422 | invalid_range | Backtest range invalid, gapped history or insufficient sample |
| 502 | invalid_upstream_data | Invalid/inconsistent provider response |
| 502 | upstream_rejected | Unexpected status, including redirects |
| 502 | upstream_unavailable | Transport failure or exhausted 408/5xx retries |
| 503 | access_restricted | 403/451 without bypass; optional metrics propagate it |
| 503 | rate_limited / provider_banned | 429/418 cooldown |
| 503 | discovery_cooldown | Waiting to retry failed live discovery; honor Retry-After |
| 503 | busy | Distinct pending-request capacity reached |
| 503 | clock_skew | Exchange/local clocks differ by over 30 seconds |
| 503 | storage_unavailable | Archive read unavailable; live candle reads still succeed if the write fails |
| 504 | upstream_timeout | Exhausted HTTP timeouts or total discovery deadline |

Raw provider errors are excluded. Exchange execution routes do not exist; attempting
them returns 404/405. Mutating local platform routes require the auth above. Startup
storage errors stop startup rather than serving a falsely healthy app. A failed
archive write is logged and skipped so it cannot turn a live read into an outage.
