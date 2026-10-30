import json
from datetime import UTC, datetime

from app.storage.candles import CandleRepository, SQLiteCandleRepository
from typing import Protocol


class PlatformRepository(CandleRepository, Protocol):
    """Storage boundary for a future PostgreSQL implementation."""

    async def put(self, table: str, key: str, data: dict, symbol: str | None = None,
                  timeframe: str | None = None) -> None: ...

    async def records(self, table: str, *, symbol: str | None = None,
                      timeframe: str | None = None, limit: int = 100) -> list[dict]: ...

TABLES = {"scan_settings", "watchlists", "analysis_reports", "indicator_snapshots", "chart_annotations",
          "market_snapshots", "dashboard_notifications", "backtest_runs", "backtest_results", "market_symbols"}


class PlatformStore(SQLiteCandleRepository):
    """Versioned JSON records, fixed table allowlist, parameterized filters."""

    async def initialize(self) -> None:
        await super().initialize()
        await self._run(self._initialize_records)

    def _initialize_records(self) -> None:
        db = self._connection()
        with db:
            for table in TABLES:
                db.execute(f"CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, symbol TEXT, timeframe TEXT, created_at TEXT NOT NULL, payload TEXT NOT NULL)")
                db.execute(f"CREATE INDEX IF NOT EXISTS ix_{table}_lookup ON {table}(symbol,timeframe,created_at)")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version < 4:
                db.execute("PRAGMA user_version=4")

    async def put(self, table: str, key: str, data: dict, symbol: str | None = None, timeframe: str | None = None) -> None:
        if table not in TABLES: raise ValueError("Unknown record table")
        payload = json.dumps(data, allow_nan=False, default=str)
        await self._run(self._put, table, key, payload, symbol, timeframe)

    def _put(self, table: str, key: str, payload: str, symbol: str | None, timeframe: str | None) -> None:
        db = self._connection()
        with db:
            db.execute(f"INSERT INTO {table} VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET symbol=excluded.symbol,timeframe=excluded.timeframe,created_at=excluded.created_at,payload=excluded.payload",
                       (key,symbol,timeframe,datetime.now(UTC).isoformat(),payload))
            # Bounded retention for snapshots; preferences/watchlists are stable keys.
            # Pruning is periodic, not on every write, to avoid O(retention) DELETE work.
            self._writes += 1
            if table not in {"scan_settings", "watchlists", "market_symbols"} and self._writes % 25 == 0:
                db.execute(f"DELETE FROM {table} WHERE id NOT IN (SELECT id FROM {table} ORDER BY created_at DESC LIMIT 1000)")

    async def records(self, table: str, *, symbol: str | None = None, timeframe: str | None = None, limit: int = 100) -> list[dict]:
        if table not in TABLES or not 1 <= limit <= 1000: raise ValueError("Invalid record query")
        return await self._run(self._records, table, symbol, timeframe, limit)

    def _records(self, table: str, symbol: str | None, timeframe: str | None, limit: int) -> list[dict]:
        rows = self._connection().execute(f"SELECT id,created_at,payload FROM {table} WHERE (? IS NULL OR symbol=?) AND (? IS NULL OR timeframe=?) ORDER BY created_at DESC LIMIT ?", (symbol,symbol,timeframe,timeframe,limit)).fetchall()
        return [{"id": row[0], "saved_at": row[1], "data": json.loads(row[2])} for row in rows]
