import asyncio
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Callable
from typing import Protocol, TypeVar

from app.adapters.base import MarketDataError
from app.schemas.candles import Candle, CandleQuery

T = TypeVar("T")


class CandleRepository(Protocol):
    async def initialize(self) -> None: ...
    async def save(self, symbol: str, timeframe: str, candles: list[Candle]) -> None: ...
    async def read(self, query: CandleQuery) -> list[Candle]: ...
    async def aclose(self) -> None: ...


class SQLiteCandleRepository:
    """Closed-candle archive on one lock-serialized WAL connection."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._db: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self._writes = 0

    async def initialize(self) -> None:
        await self._run(self._initialize)

    async def _run(self, operation: Callable[..., T], *args: object) -> T:
        try:
            return await asyncio.to_thread(self._serialized, operation, *args)
        except (sqlite3.Error, OSError, ValueError) as exc:
            raise MarketDataError("storage_unavailable", "Local candle archive is unavailable.", 503) from exc

    def _serialized(self, operation: Callable[..., T], *args: object) -> T:
        with self._lock:
            return operation(*args)

    def _connection(self) -> sqlite3.Connection:
        if self._db is None:
            raise sqlite3.ProgrammingError("database is not initialized")
        return self._db

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA busy_timeout=5000")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2, 3, 4):
                raise ValueError("Unsupported database schema version")
            db.execute("""CREATE TABLE IF NOT EXISTS candle_history (
                symbol TEXT NOT NULL, timeframe TEXT NOT NULL, open_time INTEGER NOT NULL,
                close_time INTEGER NOT NULL, candle_json TEXT NOT NULL, fetched_at TEXT NOT NULL,
                PRIMARY KEY (symbol, timeframe, open_time))""")
            # No query filters on close_time; drop the legacy write-amplifying index.
            db.execute("DROP INDEX IF EXISTS ix_candle_close_time")
            if version < 3:
                db.execute("PRAGMA user_version=3")
            db.commit()
        except Exception:
            db.close()
            raise
        self._db = db

    async def save(self, symbol: str, timeframe: str, candles: list[Candle]) -> None:
        rows = [(symbol, timeframe, c.open_time, c.close_time, c.model_dump_json(), datetime.now(UTC).isoformat())
                for c in candles if c.is_closed]
        if rows:
            await self._run(self._save, rows)

    def _save(self, rows: list[tuple[str, str, int, int, str, str]]) -> None:
        db = self._connection()
        with db:
            db.executemany("""INSERT INTO candle_history VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, timeframe, open_time) DO UPDATE SET
                close_time=excluded.close_time, candle_json=excluded.candle_json,
                fetched_at=excluded.fetched_at""", rows)

    async def read(self, query: CandleQuery) -> list[Candle]:
        return await self._run(self._read, query)

    def _read(self, query: CandleQuery) -> list[Candle]:
        # Without a lower bound return the latest page; with one, paginate forward.
        direction = "ASC" if query.start_time is not None else "DESC"
        sql = f"""SELECT candle_json FROM candle_history
            WHERE symbol=? AND timeframe=? AND open_time>=? AND open_time<=?
            ORDER BY open_time {direction} LIMIT ?"""
        values = (query.symbol, query.timeframe, query.start_time or 0,
                  query.end_time if query.end_time is not None else 4_102_444_800_000, query.limit)
        rows = self._connection().execute(sql, values).fetchall()
        return sorted((Candle.model_validate_json(row[0]) for row in rows), key=lambda c: c.open_time)

    async def aclose(self) -> None:
        await asyncio.to_thread(self._close)

    def _close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None
