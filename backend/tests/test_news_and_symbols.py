from app.adapters.binance_usdm import parse_contracts
from app.analysis.news import (
    aggregate_asset_news,
    analyze_news_item,
    combine_news_and_technicals,
    parse_rss_xml,
)
from app.core.config import Settings
from app.main import create_app
from app.schemas.candles import Candle
from app.schemas.markets import FOCUSED_SYMBOLS
from app.storage.platform import PlatformStore


def test_default_settings_restrict_to_three_focused_symbols():
    cfg = Settings(_env_file=None)
    assert cfg.allowed_symbols == ["XAUTUSDT", "BTCUSDT", "ETHUSDT"]
    assert list(FOCUSED_SYMBOLS) == ["XAUTUSDT", "BTCUSDT", "ETHUSDT"]


def test_discovery_filtering_keeps_only_gold_btc_eth():
    cfg = Settings(_env_file=None)
    raw_symbols = [
        {"symbol": s, "baseAsset": s.replace("USDT", ""), "quoteAsset": "USDT",
         "marginAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"}
        for s in ("DOGEUSDT", "SOLUSDT", "XAUTUSDT", "BTCUSDT", "ETHUSDT", "PEPEUSDT")
    ]
    contracts = parse_contracts({"symbols": raw_symbols})
    allowed = set(cfg.allowed_symbols)
    filtered = [c for c in contracts if c.symbol in allowed]
    assert [c.symbol for c in filtered] == ["BTCUSDT", "ETHUSDT", "XAUTUSDT"]


def test_purge_unallowed_symbols_removes_other_coins(tmp_path):
    store = PlatformStore(tmp_path / "purge.db")
    store._initialize()
    store._initialize_records()
    try:
        candle = Candle(
            open_time=1700000000000, close_time=1700003599999,
            open="100", high="105", low="95", close="102",
            volume="10", quote_volume="1000", trades=5,
            taker_buy_volume="5", taker_buy_quote_volume="500", is_closed=True,
        )
        for sym in ("BTCUSDT", "ETHUSDT", "XAUTUSDT", "DOGEUSDT", "SOLUSDT"):
            rows = [(sym, "1h", candle.open_time, candle.close_time, candle.model_dump_json(), "2026-10-30T00:00:00Z")]
            store._save(rows)
            store._put("market_snapshots", f"snap-{sym}", f'{{"symbol": "{sym}", "timeframe": "1h"}}', sym, "1h")
        store._put(
            "market_symbols",
            "universe",
            '{"count": 5, "contracts": [{"symbol": "BTCUSDT"}, {"symbol": "ETHUSDT"}, {"symbol": "XAUTUSDT"}, {"symbol": "DOGEUSDT"}, {"symbol": "SOLUSDT"}]}',
            None,
            None,
        )
        store._put(
            "watchlists",
            "default",
            '{"name": "Favorites", "symbols": ["BTCUSDT", "DOGEUSDT", "XAUTUSDT"], "timeframe": "1h"}',
            None,
            None,
        )

        store._purge_unallowed_symbols(["XAUTUSDT", "BTCUSDT", "ETHUSDT"])

        universes = store._records("market_symbols", None, None, 1)
        assert [c["symbol"] for c in universes[0]["data"]["contracts"]] == ["BTCUSDT", "ETHUSDT", "XAUTUSDT"]
        assert universes[0]["data"]["count"] == 3

        watches = store._records("watchlists", None, None, 1)
        assert watches[0]["data"]["symbols"] == ["BTCUSDT", "XAUTUSDT"]

        snapshots = store._records("market_snapshots", None, None, 10)
        assert {s["data"]["symbol"] for s in snapshots} == {"BTCUSDT", "ETHUSDT", "XAUTUSDT"}
    finally:
        store._close()


def test_news_sentiment_and_khmer_gold_detection():
    gold_bull = analyze_news_item("តម្លៃមាស ឡើងថ្លៃខ្លាំង as central bank buying and safe-haven demand surge")
    assert "XAUTUSDT" in gold_bull["matched_assets"]
    assert gold_bull["sentiment"] == "BULLISH"
    assert gold_bull["score"] > 0

    btc_bear = analyze_news_item("Bitcoin plunges after record ETF outflow and massive liquidations")
    assert "BTCUSDT" in btc_bear["matched_assets"]
    assert btc_bear["sentiment"] == "BEARISH"
    assert btc_bear["score"] < 0

    eth_bull = analyze_news_item("Ethereum rallies on spot ETH ETF inflow and layer-2 institutional adoption")
    assert "ETHUSDT" in eth_bull["matched_assets"]
    assert eth_bull["sentiment"] == "BULLISH"


def test_rss_xml_parser_extracts_items_safely():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel>
      <item><title>Bitcoin surges past resistance on ETF inflow</title><description>Strong institutional demand.</description></item>
      <item><title>Gold holds near record high as dollar drops</title><description>Safe haven buying continues.</description></item>
    </channel></rss>"""
    parsed = parse_rss_xml(xml, "TestFeed")
    assert len(parsed) == 2
    assert parsed[0]["source"] == "TestFeed"


def test_combined_news_and_technicals_buy_and_sell_plans(settings):
    headlines = [
        analyze_news_item("Gold XAUT surges to record high on central bank buying and rate cut"),
        analyze_news_item("Bitcoin rallies strongly on record ETF inflows and institutional demand"),
        analyze_news_item("Ethereum plunges after hack and heavy ETF outflows"),
    ]
    gold_news = aggregate_asset_news("XAUTUSDT", headlines)
    gold_tech = {
        "available": True, "data_source": "local_archive", "timeframe": "1h",
        "last_price": 3900.0, "trend": "bullish", "momentum": "bullish",
        "rsi": 58.0, "macd": "bullish", "adx": 28.0, "atr": 20.0,
        "support": 3860.0, "resistance": 3950.0, "long_score": 75.0, "short_score": 10.0,
    }
    gold_sig = combine_news_and_technicals("XAUTUSDT", gold_news, gold_tech)
    assert gold_sig["action"] == "BUY"
    assert "BUY ACTIVE NOW" in gold_sig["buy_plan"]["when_to_buy"]
    assert len(gold_sig["buy_plan"]["targets"]) == 3

    eth_news = aggregate_asset_news("ETHUSDT", headlines)
    eth_tech = {
        "available": True, "data_source": "local_archive", "timeframe": "1h",
        "last_price": 2200.0, "trend": "bearish", "momentum": "bearish",
        "rsi": 38.0, "macd": "bearish", "adx": 26.0, "atr": 25.0,
        "support": 2120.0, "resistance": 2260.0, "long_score": 10.0, "short_score": 75.0,
    }
    eth_sig = combine_news_and_technicals("ETHUSDT", eth_news, eth_tech)
    assert eth_sig["action"] == "SELL"
    assert "SELL ACTIVE NOW" in eth_sig["sell_plan"]["when_to_sell"]
    assert len(eth_sig["sell_plan"]["targets"]) == 3

    app = create_app(settings)
    assert "/api/v1/news/scan" in app.openapi()["paths"]
