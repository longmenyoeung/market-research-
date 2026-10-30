# Architecture

```text
REST routes -> MarketDataService -> bounded TTL cache -> MarketDataProvider
                      |                               -> Binance REST + allowlist
                      v
               CandleRepository -> SQLite/WAL

WebSocket route -> StreamHub -> provider stream -> validated CandleUpdate
                       |
                 bounded queues
                       |
             closed-event cache invalidation
```

```text
React dashboard -> /api/v1 platform routes -> PlatformService / BacktestService
                                              |             |
                                      indicators + structure |
                                      evidence scoring      -> RiskManager rails
                                              |
                                       PlatformStore (SQLite/WAL, schema 4)
```

The app lifespan initializes SQLite and owns provider, caches, stream tasks. `MarketDataProvider` is a runtime-checkable Protocol covering discovery,
candles, metrics, derivatives, streams and cleanup; adapters must implement the full
read-only surface, and optional upstream payloads degrade to explicit limitations.
Shutdown cancels streams, cache work, backtest tasks, closes the shared
SQLite connection and the HTTPX client. `PlatformStore` owns versioned JSON records for
settings, watchlists, reports, snapshots, notifications and backtest runs. SQLite uses
one lock-serialized WAL connection (`check_same_thread=False`, busy timeout) so
concurrent route work cannot interleave statements. Candle history is keyed by
symbol/timeframe/open_time with UPSERT and no unused close_time index. Only confirmed
REST candles are archived.

## Safety and access controls

`app/core/risk_limits.py` is the single source of truth: 0.25% default risk, 0.5% hard
per-trade cap, 1% daily loss cap and 1.5 minimum net reward/risk. `RiskManager` sizes
hypothetical plans and simulated quantities; out-of-range preferences are rejected by schema validation
rather than silently reduced. `app/core/safety.py` enforces the public-data request
allowlist. There is no execution gate or arming endpoint. Trading is permanently disabled;
legacy `MCA_TRADING_ENABLED=true` configuration is rejected.

Health remains public process liveness. When `MCA_API_KEY` is configured, all other
API and candle-WebSocket access requires it; otherwise POST/PUT routes require a
loopback client. Browser Origins are allowlisted and CORS permits
only GET/POST/PUT/OPTIONS with `Content-Type`/`X-API-Key`. No credentials or cookies
are accepted. Redirects and environment proxies remain disabled.


## Candle correctness

Every uncached page checks contract eligibility and fetches exchange time before
klines. A candle is confirmed only if it closed before that initial sample. Crossing
a boundary during HTTP transport cannot turn a provisional snapshot into a final
candle. Exchange time is extrapolated with monotonic elapsed time for coverage
checks; response time_basis explicitly describes this estimate. Clock disagreement
over 30 seconds fails closed. Network latency introduces uncertainty; conservative
finality resolves on subsequent refreshes.

Validation rejects malformed batches, nonfinite/nonpositive prices, negative or
inconsistent volumes, invalid OHLC, wrong integer types, timestamp misalignment,
duplicates, disorder and out-of-range records. Missing intervals are counted, never
filled. A short response carries warnings; listing age/range may limit coverage.
An empty array is valid; an error object is not an empty universe or candle batch.
Recent period coverage does not prove the last trade was recent.

## Cache and access controls

Bounded LRU caches default to 60-second discovery and five-second candles. Identical
concurrent requests share one loader; distinct pending candle loads default to 32.
Canceling one waiter leaves other waiters intact. Failed loads are not cached,
expired values never serve as fallback, and invalidation prevents an in-flight
old value from repopulating the cache. Existing waiters may still receive the
original snapshot, carrying its original timestamp.

REST operations share one serialized HTTPX client and a rolling request-weight
budget. Known endpoint weights are reserved before requests, the provider's used-
weight response is observed, and discovery can lower the one-minute ceiling. A
monotonic server-clock snapshot is shared for up to 30 seconds rather than fetching
time for every symbol. Transient network/timeout/408/5xx failures have bounded retries.

403/451 creates a shared access block. 429/418 creates at least 60-second/24-hour
cooldown, respecting a longer Retry-After but clamping it to
`MCA_RETRY_AFTER_MAX_SECONDS` (default 24 hours) so a hostile header cannot brick the
session. Optional metric failures (ticker/book/mark/depth/OI/funding) are limitations,
but access blocks, bans and rate limits propagate instead of returning an empty 200.
No redirect, proxy, private endpoint or alternate host is attempted. State is
single-process and not durable; do not restart to evade blocks. Stream handshakes
share the same access/cooldown guard.

## Streaming

The connector uses the USDⓈ-M market stream URL `wss://fstream.binance.com/market/ws/<symbol>@kline_<interval>`.
Subscription is encoded in the URL; no application messages are sent to Binance. The
WebSocket library handles protocol ping/pong. Redirects/proxies are disabled and frame
and message buffers are bounded.

StreamHub defaults to 10 upstreams and 50 subscribers, shares connections by
symbol/timeframe, paces connection attempts and cancels unused streams. It seeds
deduplication from the latest archived closed candle, so a fresh channel or reconnect
cannot reopen a candle, reverse its open_time/event_time, or hide a gap. Silent or
disconnected streams emit stale status and reconnect with exponential backoff
capped around 30 seconds plus jitter. Access denial, bans, limits, invalid data and
rejected handshakes emit terminal status instead of reconnecting.

Provider event lag over 15 seconds is flagged stale; events over five seconds in
the future are rejected. Duplicate/out-of-order messages cannot reverse candles
or keep a stagnant stream current. Final candles cannot be reopened. Reconnects,
missing closures and skipped intervals increase resync_generation. Queue overflow
drops oldest messages and increments per-subscriber dropped_updates.

Clients must refresh REST history when either counter changes. This phase does not
promise lossless delivery or automatic complete backfill. Stream closures invalidate
the selected candle cache and that symbol's report cache; no universe-wide recalculation occurs. Long-running
streams recheck discovery at its TTL and terminate if no longer eligible.

## Sources and scope

REST/stream sources checked on 2026-10-29; market-stream routing rechecked on 2026-10-30:

- [Binance REST market data](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)
- [Binance candle streams](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/market)
- [Binance routed WebSocket migration notice](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Important-WebSocket-Change-Notice): kline uses /market/ws; legacy /ws is not used.
- [Binance HTTP errors and limits](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info)
- [WebSocket client behavior](https://websockets.readthedocs.io/en/stable/reference/asyncio/client.html)

Local single-worker implementation. Authentication is an optional all-API/WebSocket
application key plus loopback-only mutations when unset, not an identity service. There is no distributed quota
coordination, multi-worker coordination or performance certification. The scanner,
indicators, evidence scoring, drawings, reports, planner, backtester and React
dashboard are research features only. No exchange execution, private account access,
credential storage or order/position logic exists.

## Source deadlines and persistence

Each report carries `valid_until_ms`, the minimum of its required ticker (120 seconds),
best-book (60 seconds), primary closed-candle coverage and required higher-timeframe
coverage deadlines. Contexts are rechecked after slow provider work. Cached responses
are checked again on every read; expired responses become NO TRADE and lose current
planning references. Stored reports remain immutable historical observations. Scanner
rows carry the same deadlines, and the frontend expires current classifications locally.

`PlatformRepository` extends the candle repository Protocol with `put` and `records`;
services depend on this contract. SQLite is the included implementation. A PostgreSQL
implementation would require its own migrations, locking and integration verification.
Version 4 stores `candle_history` plus fixed-allowlist JSON record tables for symbols,
settings, watchlists, reports, indicator/market snapshots, chart annotations, dashboard
notifications and backtest runs/results. The document schema is deliberately local and
simple; PostgreSQL/Redis are not advertised as working backends.

Indicator and drawing formulas are documented in `indicators.md` and `drawing-rules.md`.
The backtest engine supports a causal replay of the shared evidence assessor and a
separate EMA-crossover baseline. Historical funding events, context alignment,
selection and validation assumptions are documented in `backtesting.md`.
