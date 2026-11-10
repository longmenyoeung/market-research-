"""Service for scanning crypto/gold news feeds and combining with technical analysis for XAUTUSDT, BTCUSDT, and ETHUSDT."""

import asyncio
from datetime import UTC, datetime
import logging
from typing import TYPE_CHECKING

import httpx

from app.adapters.base import MarketDataError
from app.analysis.news import (
    aggregate_asset_news,
    analyze_news_item,
    combine_news_and_technicals,
    parse_rss_xml,
)
from app.analysis.scoring import assess, summarize
from app.indicators.engine import finite
from app.schemas.candles import CandleQuery
from app.schemas.markets import FOCUSED_SYMBOLS
from app.schemas.news import NewsScanRequest

if TYPE_CHECKING:
    from app.services.platform import PlatformService

logger = logging.getLogger(__name__)

PUBLIC_RSS_FEEDS: tuple[tuple[str, str], ...] = (
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("CoinTelegraph", "https://cointelegraph.com/rss"),
    ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
)

ALLOWED_RSS_URLS: frozenset[str] = frozenset(url for _, url in PUBLIC_RSS_FEEDS)


async def _enforce_rss_read_only(request: httpx.Request) -> None:
    forbidden = {"authorization", "proxy-authorization", "cookie", "x-mbx-apikey"}
    url = str(request.url)
    if request.method != "GET" or url not in ALLOWED_RSS_URLS or request.content or forbidden.intersection(request.headers):
        raise RuntimeError("Only allowlisted public RSS GET requests without credentials are permitted.")


async def fetch_public_rss_items(timeout_seconds: float = 4.0) -> tuple[list[dict], list[str]]:
    """Fetch headlines from allowlisted public RSS feeds; fail gracefully when offline."""
    items: list[dict] = []
    warnings: list[str] = []
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_seconds),
        follow_redirects=False,
        trust_env=False,
        event_hooks={"request": [_enforce_rss_read_only]},
    ) as client:
        tasks = [client.get(url) for _, url in PUBLIC_RSS_FEEDS]
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        for (source_name, _url), resp in zip(PUBLIC_RSS_FEEDS, responses):
            if isinstance(resp, Exception):
                warnings.append(f"{source_name} RSS unreachable (offline or blocked).")
                continue
            if resp.status_code != 200:
                warnings.append(f"{source_name} RSS returned HTTP {resp.status_code}.")
                continue
            parsed = parse_rss_xml(resp.text, source_name)
            items.extend(parsed)
    return items, warnings


async def _load_technical_summary(platform: "PlatformService", symbol: str, timeframe: str) -> dict:
    """Compute technical indicators and directional scores from live candles or local archive fallback."""
    preferences = platform.preferences.model_copy(deep=True)
    config = preferences.indicators
    candles = []
    data_source = "unavailable"
    used_tf = timeframe

    # Try local archive first if platform is already in cached/offline state to avoid waiting on timeouts
    prefer_archive = platform.scan.get("status") in {"cached", "error"} or platform.scan.get("cached_rows") is True
    if not prefer_archive:
        try:
            page = await platform.market.analysis_candles(CandleQuery(symbol=symbol, timeframe=timeframe, limit=400))
            closed = [c for c in page.candles if c.is_closed]
            if len(closed) >= 50:
                candles = closed
                data_source = "live"
        except MarketDataError:
            candles = []

    if not candles:
        for candidate_tf in dict.fromkeys([timeframe, "1h", "4h", "1d", "30m"]):
            try:
                archived = await platform.market.archive(CandleQuery(symbol=symbol, timeframe=candidate_tf, limit=400))
                closed = [c for c in archived.candles if c.is_closed]
                if len(closed) >= 50:
                    candles = closed
                    data_source = "local_archive"
                    used_tf = candidate_tf
                    break
            except MarketDataError:
                continue

    if not candles:
        return {
            "available": False,
            "data_source": "unavailable",
            "timeframe": timeframe,
            "last_price": None,
            "trend": "unknown",
            "momentum": "unknown",
        }

    df, _catalog, shape = await platform._calculations(symbol, used_tf, candles, config)
    summary = summarize(df, shape, preferences)
    last = df.iloc[-1]
    last_price = float(last.close)
    atr_val = finite(last.get("atr"))
    vwap_val = finite(last.get("vwap"))

    now_ms = int(candles[-1].close_time)
    synthetic_metric = {
        "last_price": last_price,
        "bid": last_price * 0.9999,
        "ask": last_price * 1.0001,
        "spread_bps": 2.0,
        "volume": max(preferences.minimum_volume, 10_000_000.0),
        "change_percent": float((last.close - df.close.iloc[max(0, len(df) - 24)]) / df.close.iloc[max(0, len(df) - 24)] * 100)
        if len(df) > 1
        else 0.0,
        "ticker_time": now_ms,
        "book_time": now_ms,
    }
    assessment = assess(
        df,
        shape,
        preferences,
        synthetic_metric,
        [],
        [],
        True,
        used_tf,
        as_of_ms=now_ms,
    )

    return {
        "available": True,
        "data_source": data_source,
        "timeframe": used_tf,
        "as_of": int(candles[-1].close_time),
        "last_price": round(last_price, 4 if last_price < 10 else 2),
        "trend": summary.get("trend", "unknown"),
        "momentum": summary.get("momentum", "unknown"),
        "structure_trend": shape.get("trend", "unknown"),
        "rsi": summary.get("rsi"),
        "macd": summary.get("macd"),
        "adx": summary.get("adx"),
        "atr": atr_val,
        "vwap": vwap_val,
        "support": shape.get("support"),
        "resistance": shape.get("resistance"),
        "long_score": assessment.get("long_score", 0.0),
        "short_score": assessment.get("short_score", 0.0),
    }


async def run_news_scan(platform: "PlatformService", request: NewsScanRequest) -> dict:
    """Scan news (live RSS + custom headlines) and compute BUY/SELL decisions for Gold, BTC, and ETH."""
    raw_items: list[dict] = []
    feed_warnings: list[str] = []

    for headline in request.custom_headlines:
        raw_items.append(
            {
                "title": headline,
                "summary": "",
                "source": "manual_input",
                "published_at": datetime.now(UTC).isoformat(),
                "url": None,
                "forced_symbol": request.symbol,
            }
        )

    if request.fetch_live_rss:
        rss_items, rss_warnings = await fetch_public_rss_items()
        raw_items.extend(rss_items)
        feed_warnings.extend(rss_warnings)

    # If offline and no custom headlines were supplied, restore previously saved news items if available
    if not raw_items:
        saved = await platform.store.records("news_scans", limit=1)
        if saved and isinstance(saved[0].get("data", {}).get("news_items"), list):
            for old in saved[0]["data"]["news_items"]:
                if isinstance(old, dict) and old.get("title"):
                    raw_items.append(
                        {
                            "title": old["title"],
                            "summary": old.get("summary", ""),
                            "source": old.get("source", "saved_scan"),
                            "published_at": old.get("published_at"),
                            "url": old.get("url"),
                        }
                    )

    seen_titles: set[str] = set()
    analyzed_items: list[dict] = []
    for item in raw_items:
        norm_title = item["title"].strip().lower()
        if not norm_title or norm_title in seen_titles:
            continue
        seen_titles.add(norm_title)
        analyzed = analyze_news_item(
            title=item["title"],
            summary=item.get("summary", ""),
            source=item.get("source", "rss"),
            published_at=item.get("published_at"),
            url=item.get("url"),
            forced_symbol=item.get("forced_symbol"),
        )
        analyzed_items.append(analyzed)

    target_symbols = [request.symbol] if request.symbol in FOCUSED_SYMBOLS else list(FOCUSED_SYMBOLS)
    signals: list[dict] = []
    for sym in target_symbols:
        tech = await _load_technical_summary(platform, sym, request.timeframe)
        news_agg = aggregate_asset_news(sym, analyzed_items)
        combined = combine_news_and_technicals(sym, news_agg, tech)
        signals.append(combined)

    result = {
        "scanned_at": datetime.now(UTC).isoformat(),
        "timeframe": request.timeframe,
        "focused_symbols": list(FOCUSED_SYMBOLS),
        "signals": signals,
        "news_items": analyzed_items[:40],
        "feed_warnings": feed_warnings,
        "manual_only": True,
        "notice": (
            "News sentiment and technical alignment are decision-support signals for manual review only. "
            "No exchange orders are placed automatically."
        ),
    }
    await platform.store.put("news_scans", "latest", result)
    return result


async def get_latest_news_scan(platform: "PlatformService", timeframe: str = "1h") -> dict:
    """Return the latest stored news scan or generate an initial offline-safe scan."""
    saved = await platform.store.records("news_scans", limit=1)
    if saved and isinstance(saved[0].get("data"), dict):
        data = saved[0]["data"]
        if data.get("signals"):
            return data
    return await run_news_scan(
        platform,
        NewsScanRequest(timeframe=timeframe, custom_headlines=[], fetch_live_rss=False),
    )

