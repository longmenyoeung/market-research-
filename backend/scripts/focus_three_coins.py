"""Purge all contracts except XAUTUSDT (Gold), BTCUSDT (Bitcoin), and ETHUSDT (ETH) from the local database and generate an initial news + technical scan."""

from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.analysis.news import aggregate_asset_news, analyze_news_item, combine_news_and_technicals
from app.analysis.scoring import assess, summarize
from app.analysis.structure import analyze_structure
from app.core.config import Settings
from app.indicators.engine import calculate, finite, frame
from app.schemas.candles import Candle
from app.schemas.markets import FOCUSED_SYMBOLS
from app.schemas.platform import Preferences


def main() -> None:
    settings = Settings()
    allowed = list(FOCUSED_SYMBOLS)
    allowed_set = set(allowed)
    placeholders = ",".join("?" for _ in allowed)

    conn = sqlite3.connect(settings.database_path, timeout=30)
    try:
        with conn:
            conn.execute(f"DELETE FROM candle_history WHERE symbol NOT IN ({placeholders})", tuple(allowed))
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

            conn.execute(
                "CREATE TABLE IF NOT EXISTS news_scans (id TEXT PRIMARY KEY, symbol TEXT, timeframe TEXT, created_at TEXT NOT NULL, payload TEXT NOT NULL)"
            )

        headlines = [
            "តម្លៃមាស (Gold XAUT) ឡើងថ្លៃខ្លាំង on central bank buying and safe-haven demand",
            "Bitcoin (BTC) surges on steady institutional spot ETF inflows and strong market momentum",
            "Ethereum (ETH) rallies as layer-2 adoption and spot ETH ETF demand expand",
        ]
        analyzed_items = [analyze_news_item(h, source="initial_scan") for h in headlines]
        preferences = Preferences()
        signals = []
        for sym in allowed:
            rows = conn.execute(
                "SELECT candle_json FROM candle_history WHERE symbol=? AND timeframe='1h' ORDER BY open_time DESC LIMIT 400",
                (sym,),
            ).fetchall()
            candles = sorted((Candle.model_validate_json(r[0]) for r in rows), key=lambda c: c.open_time)
            if candles:
                data = frame(candles)
                df, _cat = calculate(data, preferences.indicators, "1h")
                shape = analyze_structure(df, "1h", preferences.indicators.swing_window, momentum_period=preferences.indicators.momentum_period)
                summary = summarize(df, shape, preferences)
                last = df.iloc[-1]
                last_price = float(last.close)
                now_ms = int(candles[-1].close_time)
                metric = {
                    "last_price": last_price,
                    "bid": last_price * 0.9999,
                    "ask": last_price * 1.0001,
                    "spread_bps": 2.0,
                    "volume": 10_000_000.0,
                    "change_percent": 1.0,
                    "ticker_time": now_ms,
                    "book_time": now_ms,
                }
                assessment = assess(df, shape, preferences, metric, [], [], True, "1h", as_of_ms=now_ms)
                tech = {
                    "available": True,
                    "data_source": "local_archive",
                    "timeframe": "1h",
                    "as_of": now_ms,
                    "last_price": round(last_price, 2),
                    "trend": summary.get("trend", "unknown"),
                    "momentum": summary.get("momentum", "unknown"),
                    "structure_trend": shape.get("trend", "unknown"),
                    "rsi": summary.get("rsi"),
                    "macd": summary.get("macd"),
                    "adx": summary.get("adx"),
                    "atr": finite(last.get("atr")),
                    "vwap": finite(last.get("vwap")),
                    "support": shape.get("support"),
                    "resistance": shape.get("resistance"),
                    "long_score": assessment.get("long_score", 0.0),
                    "short_score": assessment.get("short_score", 0.0),
                }
            else:
                tech = {"available": False, "data_source": "unavailable", "timeframe": "1h", "last_price": None}

            news_agg = aggregate_asset_news(sym, analyzed_items)
            signals.append(combine_news_and_technicals(sym, news_agg, tech))

        scan_payload = {
            "scanned_at": datetime.now(UTC).isoformat(),
            "timeframe": "1h",
            "focused_symbols": allowed,
            "signals": signals,
            "news_items": analyzed_items,
            "feed_warnings": [],
            "manual_only": True,
            "notice": "News sentiment and technical alignment are decision-support signals for manual review only.",
        }
        with conn:
            conn.execute(
                "INSERT INTO news_scans VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET created_at=excluded.created_at, payload=excluded.payload",
                ("latest", None, "1h", datetime.now(UTC).isoformat(), json.dumps(scan_payload)),
            )

        remaining_candles = conn.execute(
            "SELECT symbol, timeframe, COUNT(*) FROM candle_history GROUP BY symbol, timeframe"
        ).fetchall()
        print("Remaining candle symbols in DB:", remaining_candles)
        for sig in signals:
            print(
                f"{sig['symbol']} ({sig['name']}): Action={sig['action']} | Price={sig['technical'].get('last_price')} | "
                f"Buy Entry={sig['buy_plan']['entry']} SL={sig['buy_plan']['stop']} TP={sig['buy_plan']['targets']} | "
                f"Sell Entry={sig['sell_plan']['entry']} SL={sig['sell_plan']['stop']} TP={sig['sell_plan']['targets']}"
            )
    finally:
        conn.close()


if __name__ == "__main__":
    main()

