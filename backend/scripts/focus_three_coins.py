"""Purge all contracts except XAUTUSDT (Gold), BTCUSDT (Bitcoin), and ETHUSDT (ETH) from the local database."""

import json
from pathlib import Path
import sqlite3
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import Settings
from app.schemas.markets import FOCUSED_SYMBOLS


def main() -> None:
    populate_script = BACKEND_ROOT / "scripts" / "populate_all_timeframes.py"
    if populate_script.exists():
        populate_script.unlink()

    settings = Settings()
    if not settings.database_path.exists():
        return
    allowed = list(FOCUSED_SYMBOLS)
    allowed_set = set(allowed)
    placeholders = ",".join("?" for _ in allowed)

    conn = sqlite3.connect(settings.database_path, timeout=30)
    try:
        with conn:
            conn.execute(f"DELETE FROM candle_history WHERE symbol NOT IN ({placeholders})", tuple(allowed))
            conn.execute("DELETE FROM candle_history WHERE timeframe IN ('5m', '15m', '30m', '1d')")
            for table in (
                "market_snapshots",
                "analysis_reports",
                "indicator_snapshots",
                "chart_annotations",
                "dashboard_notifications",
                "backtest_runs",
                "backtest_results",
            ):
                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
                if exists:
                    conn.execute(
                        f"DELETE FROM {table} WHERE symbol IS NOT NULL AND symbol NOT IN ({placeholders})",
                        tuple(allowed),
                    )
            exists_news = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='news_scans'"
            ).fetchone()
            if exists_news:
                conn.execute("DELETE FROM news_scans")

            for row_id, payload_str in conn.execute("SELECT id, payload FROM market_symbols").fetchall():
                data = json.loads(payload_str)
                contracts = [
                    c for c in data.get("contracts", []) if isinstance(c, dict) and c.get("symbol") in allowed_set
                ]
                data["contracts"] = contracts
                data["count"] = len(contracts)
                conn.execute("UPDATE market_symbols SET payload=? WHERE id=?", (json.dumps(data), row_id))

            for row_id, payload_str in conn.execute("SELECT id, payload FROM watchlists").fetchall():
                data = json.loads(payload_str)
                symbols = [s for s in data.get("symbols", []) if s in allowed_set]
                data["symbols"] = symbols
                conn.execute("UPDATE watchlists SET payload=? WHERE id=?", (json.dumps(data), row_id))

        remaining_candles = conn.execute(
            "SELECT symbol, timeframe, COUNT(*) FROM candle_history GROUP BY symbol, timeframe ORDER BY symbol, timeframe"
        ).fetchall()
        print("Remaining real candle symbols in DB:", remaining_candles)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
