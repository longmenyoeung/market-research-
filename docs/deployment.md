# Local deployment and operations

The supported deployment is one Python process on the local machine, bound to
127.0.0.1, serving a built React dashboard. No hosting account, container runtime,
public endpoint or exchange credential is required.

## Install and launch

From the repository root in PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.lock.txt
npm --prefix frontend ci
.\.venv\Scripts\python.exe scripts\run_local.py --build
```

Open http://127.0.0.1:8000. The launcher rebuilds missing dashboard assets; `--build`
forces a rebuild after frontend edits. `--port 8765` selects another loopback port.
If using a nondefault browser port, add its exact origin to
`MCA_WEBSOCKET_ORIGINS` in the optional `.env` before launch; backend browser writes
and WebSocket subscriptions validate origins. Stop with Ctrl+C.

Use the lockfile for a reproducible Python environment and `npm ci` for Node.
Python 3.11 is the tested baseline; Node 22.12+ or 24 supports the installed Vite
version. On Linux/macOS use `.venv/bin/python`; the launcher itself is portable,
but this delivery has been exercised on Windows.

## Data and recovery

- Default database: `backend/data/market_data.db`. Use an absolute
  `MCA_DATABASE_PATH` to relocate it. Startup needs a writable compatible SQLite
  database. Existing schema versions migrate forward to version 4.
- Stop the app before copying the database for backup. SQLite WAL side files may
  exist while the app runs; copying only the main file during writes is unsafe.
- Candle history stores confirmed fetched REST candles; it is not an automatically
  complete archive. Snapshot tables have periodic retention around 1,000 records
  per table. Settings/watchlists use stable keys and are not pruned.
- Reports remain historical after restart. Incomplete background backtests are
  labeled interrupted when revisited. Scan state, caches and stream subscriptions
  are recreated. Provider cooldowns are process-local; never restart to evade them.

## Provider failures and capacity

Health reports process liveness, not exchange reachability. A healthy local server
can show provider errors while the offline planner and archive remain usable.
If the dashboard says `Public market-data request timed out`, check the network
from the same computer before retrying:

```powershell
Test-NetConnection fapi.binance.com -Port 443
.\.venv\Scripts\python.exe backend\scripts\live_market_data.py
```

The first check needs `TcpTestSucceeded: True` for the official public REST host.
The second performs one public discovery check with zero retries. If the TCP check
fails, the dashboard cannot discover contracts on that connection; a working
`/health` does not change this. Ask the network operator about ordinary access or
use a network where access is permitted. A 403/451 response requires an eligibility
check rather than a connectivity workaround. Never substitute synthetic prices in
the live scanner. The offline planner and saved historical reports remain usable.

Do not supply trading API keys. The application has no secret/signature or
alternate-exchange-host configuration. A 403/451 access block stops provider
access; 418/429 creates a cooldown. Restrictions must not be bypassed.

Use one backend worker. REST calls share a conservative rolling request-weight
budget (1,200/minute by default and reduced when provider metadata is lower),
bounded concurrency, a cached server-clock snapshot and bounded retries. A selected report can involve several timeframes,
daily references and optional derivatives. A full-universe scan can take many
minutes, and earlier rows can expire during it. The numerical CPU benchmark
excludes network/storage/browser costs and is not a provider-throughput guarantee.

## External deployment status

The repository contains GitHub Actions configuration for locked Python tests,
Node unit tests/build and mocked Chromium flows. No Git remote has been supplied;
the workflow has not been run remotely. No application has been published.

Public hosting, PostgreSQL, Redis, multi-worker coordination, Docker packaging,
production identity, TLS and distributed rate limiting are future deployment
work. Keep the supported build local. When `MCA_API_KEY` is configured, every API
and candle-WebSocket connection is authenticated; the bundled unlock screen keeps
the key only in tab memory and clears it on lock/reload. Failed keys are throttled,
requests have a bounded body size, and Host/Origin/security-header controls apply.
This remains a single-user application guard, not a production identity service or
an exchange API key.

Trading remains permanently disabled. Adding deployment infrastructure must never
introduce order or position execution.
