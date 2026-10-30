# Manual-only safety policy

Never place, modify or cancel orders; never open or close positions. Never add
exchange execution endpoints, private account access, trading keys, signing logic,
transaction-triggering notifications, martingale or averaging-down automation.
Any future request requiring execution must be redirected to a read-only analysis
or hypothetical-planning feature. User trading actions remain outside this system.

REST outbound access permits only GET to the official public
exchangeInfo, time, klines, 24h ticker, book ticker, premium index, depth,
open interest, open-interest history and funding-history paths. Klines accepts only
validated symbol, interval, limit, startTime and endTime; other symbol paths accept
only the exact fixed parameters in the guard. Duplicate/unknown parameters are denied.
Bodies, cookies and credentials are prohibited. The stream connector constructs only
a validated public candle URL (`wss://fstream.binance.com/market/ws/...`) and sends no
application messages; protocol ping/pong is allowed.

No alternate host, redirect following or environment proxy is supported. Health is
public process liveness and never checks the exchange. If `MCA_API_KEY` is set, all
other API routes and receive-only candle WebSockets require that application key;
browser WebSockets use a URL-safe subprotocol so the key is not placed in a URL.
Without a key, local mutations remain loopback-only. Browser Origins, Host headers,
request bodies and repeated authentication failures are bounded. Exchange order,
account and position routes do not exist.

Trading is permanently disabled. No runtime arming route, execution gate or future
execution prerequisite is implemented. Legacy MCA_TRADING_ENABLED=true prevents startup.
Risk defaults to 0.25% per trade with a hard 0.5% ceiling,
a 1% daily realized-loss cap in backtests, and a 1.5 minimum net reward/risk for
candidates. These are code rails, not user preferences. Tests enumerate routes, attempt
prohibited HTTP requests before transport, verify stream URL/redirect boundaries,
exercise the connector with no send() method, and verify execution cannot be enabled and cover auth/CORS,
risk caps, daily loss stop, spread charging, archive-failure isolation and provider
access-block propagation.

These are regression controls within this codebase, not an operating-system
sandbox against arbitrary future Python code. New provider operations require
review, explicit public-data allowlisting and tests. Keep tests aligned with the
manual-only requirements rather than relaxing them to introduce private APIs.

No exchange secrets are required. An optional local application API key may be supplied
via environment. `.env`, virtual environments, database files and coverage output are
Git ignored. Raw provider error bodies and headers are not sent to clients or normal
logs. Mock fixtures are test-only; the application has no fake live-data fallback.

Cambodia futures eligibility remains **unverified**. Public metadata visibility is
not proof of entitlement. Confirm current jurisdiction-specific and account-specific
requirements independently using [Binance terms](https://www.binance.com/en/terms),
[official support](https://www.binance.com/en/chat), and applicable Cambodian rules
before any manual futures use. This implementation makes no legal determination.
403/451 failures stop requests; no alternate region, proxy or host is attempted.
