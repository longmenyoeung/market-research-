"""News sentiment scanner and combined News + Technical BUY/SELL decision engine for Gold (XAUTUSDT), Bitcoin (BTCUSDT), and Ethereum (ETHUSDT)."""

from datetime import UTC, datetime
import re
from xml.etree import ElementTree

from app.schemas.markets import ASSET_METADATA, FOCUSED_SYMBOLS

ASSET_KEYWORDS: dict[str, tuple[str, ...]] = {
    "XAUTUSDT": (
        "gold", "xau", "xaut", "paxg", "bullion", "precious metal", "spot gold",
        "comex", "central bank gold", "មាស", "ហាងឆេងមាស", "តម្លៃមាស",
    ),
    "BTCUSDT": (
        "bitcoin", "btc", "satoshi", "ibit", "fbtc", "microstrategy",
        "spot bitcoin", "btc etf", "bitcoin etf", "halving", "ប៊ីតខوين",
    ),
    "ETHUSDT": (
        "ethereum", "eth", "ether", "vitalik", "pectra", "dencun",
        "layer-2", "layer 2", "eth etf", "ethereum etf", "spot eth", "staking", "defi", "អ៊ីធើរៀម",
    ),
}

MACRO_KEYWORDS: tuple[str, ...] = (
    "fed", "federal reserve", "powell", "fomc", "rate cut", "rate hike",
    "interest rate", "cpi", "inflation", "pce", "nfp", "payroll", "unemployment",
    "dxy", "us dollar", "dollar index", "treasury yield", "tariff", "geopolitical",
    "safe haven", "safe-haven", "recession", "liquidity", "crypto market",
)

GOLD_SAFE_HAVEN_KEYWORDS: tuple[str, ...] = (
    "safe haven", "safe-haven", "geopolitical", "war", "conflict", "central bank buying",
    "inflation hedge", "tariff", "uncertainty",
)

BULLISH_TERMS: tuple[tuple[str, int], ...] = (
    ("rate cut", 35),
    ("rate cuts", 35),
    ("etf inflow", 35),
    ("etf inflows", 35),
    ("record inflow", 40),
    ("approved", 30),
    ("approval", 30),
    ("all-time high", 35),
    ("record high", 35),
    ("breakout", 30),
    ("breaks above", 30),
    ("massive buying", 35),
    ("central bank buying", 35),
    ("institutional adoption", 30),
    ("institutional demand", 30),
    ("short squeeze", 30),
    ("ឡើងថ្លៃខ្លាំង", 35),
    ("ទិញចូល", 30),
    ("surge", 22),
    ("surges", 22),
    ("soar", 25),
    ("soars", 25),
    ("rally", 24),
    ("rallies", 24),
    ("bullish", 26),
    ("gain", 16),
    ("gains", 16),
    ("jump", 20),
    ("jumps", 20),
    ("climb", 16),
    ("climbs", 16),
    ("rebound", 20),
    ("recovery", 18),
    ("accumulation", 24),
    ("accumulate", 22),
    ("inflow", 22),
    ("inflows", 22),
    ("dovish", 26),
    ("weak dollar", 24),
    ("dollar drops", 24),
    ("dollar falls", 24),
    ("inflation hedge", 24),
    ("safe-haven demand", 28),
    ("safe haven demand", 28),
    ("upgrade", 20),
    ("partnership", 18),
    ("support holds", 20),
    ("buy signal", 28),
    ("strong demand", 24),
    ("ឡើងថ្លៃ", 25),
    ("វិជ្ជមាន", 22),
    ("ទិញ", 20),
)

BEARISH_TERMS: tuple[tuple[str, int], ...] = (
    ("rate hike", 35),
    ("rate hikes", 35),
    ("etf outflow", 35),
    ("etf outflows", 35),
    ("record outflow", 40),
    ("hack", 38),
    ("hacked", 38),
    ("exploit", 35),
    ("ban", 35),
    ("banned", 35),
    ("lawsuit", 30),
    ("sec charges", 35),
    ("crash", 38),
    ("crashes", 38),
    ("plunge", 34),
    ("plunges", 34),
    ("liquidation", 30),
    ("liquidations", 30),
    ("bankruptcy", 40),
    ("insolvency", 40),
    ("ចុះថ្លៃខ្លាំង", 35),
    ("លក់ចេញ", 30),
    ("drop", 20),
    ("drops", 20),
    ("fall", 18),
    ("falls", 18),
    ("slump", 24),
    ("slumps", 24),
    ("dump", 28),
    ("dumps", 28),
    ("bearish", 26),
    ("outflow", 22),
    ("outflows", 22),
    ("hawkish", 26),
    ("strong dollar", 24),
    ("dollar surges", 24),
    ("yields surge", 24),
    ("sell-off", 28),
    ("selloff", 28),
    ("selling pressure", 26),
    ("rejection", 22),
    ("breaks below", 28),
    ("resistance holds", 20),
    ("delay", 18),
    ("delayed", 18),
    ("investigation", 22),
    ("sell signal", 28),
    ("weak demand", 22),
    ("ចុះថ្លៃ", 25),
    ("អវិជ្ជមាន", 22),
    ("លក់", 20),
)


def _contains_term(text_lower: str, term: str) -> bool:
    if any(ord(ch) > 127 for ch in term):
        return term in text_lower
    pattern = r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])"
    return bool(re.search(pattern, text_lower))


def detect_assets(text: str) -> tuple[list[str], bool]:
    """Identify which of the 3 focused assets (XAUTUSDT, BTCUSDT, ETHUSDT) a news item affects."""
    lower = text.lower()
    matched: list[str] = []
    for symbol in FOCUSED_SYMBOLS:
        if any(_contains_term(lower, kw) for kw in ASSET_KEYWORDS[symbol]):
            matched.append(symbol)
    is_macro = any(_contains_term(lower, kw) for kw in MACRO_KEYWORDS)
    if not matched and is_macro:
        matched = list(FOCUSED_SYMBOLS)
    return matched, is_macro


def analyze_news_item(
    title: str,
    summary: str = "",
    source: str = "manual",
    published_at: str | None = None,
    url: str | None = None,
    forced_symbol: str | None = None,
) -> dict:
    """Score a single news headline/article for sentiment, catalysts, and asset impact."""
    combined_text = f"{title} {summary}".strip()
    lower = combined_text.lower()
    matched_assets, is_macro = detect_assets(combined_text)
    if forced_symbol and forced_symbol in FOCUSED_SYMBOLS and forced_symbol not in matched_assets:
        matched_assets.append(forced_symbol)
    if not matched_assets:
        matched_assets = list(FOCUSED_SYMBOLS)

    bullish_hits: list[str] = []
    bearish_hits: list[str] = []
    bull_points = 0
    bear_points = 0

    for term, weight in BULLISH_TERMS:
        if _contains_term(lower, term):
            bullish_hits.append(term)
            bull_points += weight

    for term, weight in BEARISH_TERMS:
        if _contains_term(lower, term):
            bearish_hits.append(term)
            bear_points += weight

    safe_haven_hit = any(_contains_term(lower, kw) for kw in GOLD_SAFE_HAVEN_KEYWORDS)
    if safe_haven_hit and "XAUTUSDT" in matched_assets and bull_points == 0 and bear_points == 0:
        bull_points += 24
        bullish_hits.append("safe-haven gold demand")

    raw_score = bull_points - bear_points
    score = max(-100, min(100, raw_score))
    if score >= 15:
        sentiment = "BULLISH"
    elif score <= -15:
        sentiment = "BEARISH"
    else:
        sentiment = "NEUTRAL"

    abs_score = abs(score)
    impact = "HIGH" if abs_score >= 45 else "MEDIUM" if abs_score >= 20 else "LOW"

    if sentiment == "BULLISH":
        reason = f"Bullish catalyst ({', '.join(bullish_hits[:4])})" + (
            f" outweighs bearish notes ({', '.join(bearish_hits[:2])})" if bearish_hits else ""
        )
    elif sentiment == "BEARISH":
        reason = f"Bearish catalyst ({', '.join(bearish_hits[:4])})" + (
            f" outweighs bullish notes ({', '.join(bullish_hits[:2])})" if bullish_hits else ""
        )
    else:
        reason = "Neutral or balanced news headline; no strong directional catalyst detected."

    return {
        "title": title.strip(),
        "summary": summary.strip()[:300] if summary else "",
        "source": source,
        "published_at": published_at or datetime.now(UTC).isoformat(),
        "url": url,
        "matched_assets": matched_assets,
        "is_macro": is_macro,
        "sentiment": sentiment,
        "score": score,
        "impact": impact,
        "bullish_keywords": bullish_hits,
        "bearish_keywords": bearish_hits,
        "reason": reason,
    }


def parse_rss_xml(xml_text: str, source_name: str) -> list[dict]:
    """Parse standard RSS 2.0 or Atom XML safely without external entity expansion."""
    if "<!ENTITY" in xml_text.upper() or "<!DOCTYPE" in xml_text.upper():
        xml_text = re.sub(r"<!DOCTYPE[^>]*>", "", xml_text, flags=re.IGNORECASE)
        if "<!ENTITY" in xml_text.upper():
            return []
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []

    items: list[dict] = []
    # RSS 2.0 <item> elements
    for elem in root.iter("item"):
        title = (elem.findtext("title") or "").strip()
        if not title:
            continue
        desc = (elem.findtext("description") or "").strip()
        desc = re.sub(r"<[^>]+>", " ", desc)
        desc = re.sub(r"\s+", " ", desc).strip()
        pub_date = (elem.findtext("pubDate") or "").strip() or None
        link = (elem.findtext("link") or "").strip() or None
        items.append({"title": title, "summary": desc[:280], "source": source_name, "published_at": pub_date, "url": link})
        if len(items) >= 25:
            break

    # Atom <entry> fallback
    if not items:
        for elem in root.iter():
            if elem.tag.endswith("entry"):
                title = ""
                summary = ""
                pub_date = None
                link = None
                for child in elem:
                    tag = child.tag.split("}")[-1]
                    if tag == "title" and child.text:
                        title = child.text.strip()
                    elif tag in ("summary", "content") and child.text:
                        summary = re.sub(r"<[^>]+>", " ", child.text).strip()[:280]
                    elif tag in ("published", "updated") and child.text:
                        pub_date = child.text.strip()
                    elif tag == "link":
                        link = child.attrib.get("href") or (child.text.strip() if child.text else None)
                if title:
                    items.append({"title": title, "summary": summary, "source": source_name, "published_at": pub_date, "url": link})
                if len(items) >= 25:
                    break
    return items


def aggregate_asset_news(symbol: str, analyzed_items: list[dict]) -> dict:
    """Aggregate all relevant news items for one asset (XAUTUSDT, BTCUSDT, or ETHUSDT)."""
    relevant = [item for item in analyzed_items if symbol in item.get("matched_assets", [])]
    bullish = [item for item in relevant if item["sentiment"] == "BULLISH"]
    bearish = [item for item in relevant if item["sentiment"] == "BEARISH"]
    neutral = [item for item in relevant if item["sentiment"] == "NEUTRAL"]

    if not relevant:
        return {
            "symbol": symbol,
            "sentiment": "NEUTRAL",
            "score": 0.0,
            "article_count": 0,
            "bullish_count": 0,
            "bearish_count": 0,
            "neutral_count": 0,
            "top_catalysts": [],
            "summary": "No relevant news headlines scanned yet for this asset.",
        }

    # Weight direct asset matches higher than broad macro matches
    weighted_sum = 0.0
    weight_total = 0.0
    for item in relevant:
        w = 1.5 if item["impact"] == "HIGH" else 1.15 if item["impact"] == "MEDIUM" else 0.8
        if not item.get("is_macro") or len(item.get("matched_assets", [])) == 1:
            w *= 1.25
        weighted_sum += item["score"] * w
        weight_total += w

    avg_score = round(max(-100.0, min(100.0, weighted_sum / max(1.0, weight_total))), 1)
    if avg_score >= 12:
        sentiment = "BULLISH"
    elif avg_score <= -12:
        sentiment = "BEARISH"
    else:
        sentiment = "NEUTRAL"

    sorted_catalysts = sorted(relevant, key=lambda x: abs(x["score"]), reverse=True)[:5]
    return {
        "symbol": symbol,
        "sentiment": sentiment,
        "score": avg_score,
        "article_count": len(relevant),
        "bullish_count": len(bullish),
        "bearish_count": len(bearish),
        "neutral_count": len(neutral),
        "top_catalysts": [
            {
                "title": c["title"],
                "sentiment": c["sentiment"],
                "score": c["score"],
                "impact": c["impact"],
                "source": c["source"],
                "reason": c["reason"],
            }
            for c in sorted_catalysts
        ],
        "summary": (
            f"{sentiment} news bias ({avg_score:+.1f}/100) across {len(relevant)} scanned item(s): "
            f"{len(bullish)} bullish, {len(bearish)} bearish, {len(neutral)} neutral."
        ),
    }


def combine_news_and_technicals(symbol: str, news_summary: dict, tech: dict) -> dict:
    """Combine News Sentiment + Technical Analysis to tell when to BUY and when to SELL."""
    meta = ASSET_METADATA.get(symbol, {"name": symbol, "short": symbol, "khmer": symbol, "symbol": symbol})
    news_sentiment = news_summary["sentiment"]
    news_score = float(news_summary["score"])
    has_news = news_summary["article_count"] > 0

    tech_available = bool(tech.get("available"))
    trend = tech.get("trend", "unknown")
    momentum = tech.get("momentum", "unknown")
    long_score = float(tech.get("long_score") or 0.0)
    short_score = float(tech.get("short_score") or 0.0)
    tech_net = round(long_score - short_score, 1)

    if not tech_available:
        technical_bias = "UNAVAILABLE"
    elif trend == "bullish" and long_score >= short_score:
        technical_bias = "BULLISH"
    elif trend == "bearish" and short_score >= long_score:
        technical_bias = "BEARISH"
    elif long_score - short_score >= 20:
        technical_bias = "BULLISH"
    elif short_score - long_score >= 20:
        technical_bias = "BEARISH"
    else:
        technical_bias = "NEUTRAL"

    if has_news and tech_available:
        combined_score = round(0.55 * news_score + 0.45 * tech_net, 1)
    elif has_news:
        combined_score = round(news_score, 1)
    elif tech_available:
        combined_score = round(tech_net, 1)
    else:
        combined_score = 0.0

    # Determine BUY / SELL / WAIT decision and alignment
    conflict = (
        (news_sentiment == "BULLISH" and technical_bias == "BEARISH")
        or (news_sentiment == "BEARISH" and technical_bias == "BULLISH")
    )

    if conflict and abs(news_score) < 60:
        action = "WAIT"
        alignment = "CONFLICTING (News vs Technicals disagree)"
    elif news_sentiment == "BULLISH" and technical_bias == "BULLISH":
        action = "BUY"
        alignment = "STRONG ALIGNMENT (Bullish News + Bullish Chart)"
    elif news_sentiment == "BEARISH" and technical_bias == "BEARISH":
        action = "SELL"
        alignment = "STRONG ALIGNMENT (Bearish News + Bearish Chart)"
    elif news_sentiment == "BULLISH" and (technical_bias in ("NEUTRAL", "UNAVAILABLE") or news_score >= 60):
        action = "BUY"
        alignment = "NEWS-DRIVEN BUY (Bullish News Catalyst)"
    elif news_sentiment == "BEARISH" and (technical_bias in ("NEUTRAL", "UNAVAILABLE") or news_score <= -60):
        action = "SELL"
        alignment = "NEWS-DRIVEN SELL (Bearish News Catalyst)"
    elif not has_news and technical_bias == "BULLISH" and combined_score >= 20:
        action = "BUY"
        alignment = "TECHNICAL BUY (No opposing news)"
    elif not has_news and technical_bias == "BEARISH" and combined_score <= -20:
        action = "SELL"
        alignment = "TECHNICAL SELL (No opposing news)"
    else:
        action = "WAIT"
        alignment = "NEUTRAL / WAIT FOR CONFIRMATION"

    last_price = tech.get("last_price")
    atr = tech.get("atr")
    support = tech.get("support")
    resistance = tech.get("resistance")
    timeframe = tech.get("timeframe", "1h")
    rsi = tech.get("rsi")

    buy_plan = None
    sell_plan = None
    if isinstance(last_price, (int, float)) and last_price > 0:
        effective_atr = float(atr) if isinstance(atr, (int, float)) and atr > 0 else float(last_price) * 0.01
        risk_dist = max(effective_atr * 1.5, float(last_price) * 0.004)
        decimals = 2 if last_price >= 10 else 4

        buy_stop = min(
            float(last_price) - risk_dist,
            float(support) - effective_atr * 0.25 if isinstance(support, (int, float)) and 0 < support < last_price else float(last_price) - risk_dist,
        )
        buy_stop = max(0.0001, round(buy_stop, decimals))
        buy_r = max(float(last_price) - buy_stop, effective_atr * 0.5)
        buy_targets = [round(float(last_price) + buy_r * mult, decimals) for mult in (1, 2, 3)]
        buy_zone = [round(float(last_price) - effective_atr * 0.15, decimals), round(float(last_price) + effective_atr * 0.15, decimals)]
        buy_trigger_level = round(float(resistance) if isinstance(resistance, (int, float)) and resistance > last_price else float(last_price) + effective_atr * 0.5, decimals)

        buy_plan = {
            "side": "LONG",
            "entry": round(float(last_price), decimals),
            "entry_zone": buy_zone,
            "stop": buy_stop,
            "targets": buy_targets,
            "when_to_buy": (
                f"BUY ACTIVE NOW: News sentiment is {news_sentiment} ({news_score:+.1f}) and {timeframe} chart trend is {trend}. "
                f"Enter BUY in zone {buy_zone[0]}–{buy_zone[1]}, Stop Loss at {buy_stop}, Take Profit at TP1 {buy_targets[0]} (1R), TP2 {buy_targets[1]} (2R), TP3 {buy_targets[2]} (3R)."
                if action == "BUY"
                else f"WHEN TO BUY: Wait until news turns Bullish AND {timeframe} candle closes above {buy_trigger_level} with RSI > 50 (current RSI: {f'{rsi:.1f}' if isinstance(rsi, (int, float)) else 'N/A'}). "
                     f"Then enter near {round(float(last_price), decimals)} with Stop Loss at {buy_stop} and TP at {buy_targets[0]} / {buy_targets[1]}."
            ),
        }

        sell_stop = max(
            float(last_price) + risk_dist,
            float(resistance) + effective_atr * 0.25 if isinstance(resistance, (int, float)) and resistance > last_price else float(last_price) + risk_dist,
        )
        sell_stop = round(sell_stop, decimals)
        sell_r = max(sell_stop - float(last_price), effective_atr * 0.5)
        sell_targets = [round(max(0.0001, float(last_price) - sell_r * mult), decimals) for mult in (1, 2, 3)]
        sell_zone = [round(float(last_price) - effective_atr * 0.15, decimals), round(float(last_price) + effective_atr * 0.15, decimals)]
        sell_trigger_level = round(float(support) if isinstance(support, (int, float)) and 0 < support < last_price else float(last_price) - effective_atr * 0.5, decimals)

        sell_plan = {
            "side": "SHORT",
            "entry": round(float(last_price), decimals),
            "entry_zone": sell_zone,
            "stop": sell_stop,
            "targets": sell_targets,
            "when_to_sell": (
                f"SELL ACTIVE NOW: News sentiment is {news_sentiment} ({news_score:+.1f}) and {timeframe} chart trend is {trend}. "
                f"Enter SELL in zone {sell_zone[0]}–{sell_zone[1]}, Stop Loss at {sell_stop}, Take Profit at TP1 {sell_targets[0]} (1R), TP2 {sell_targets[1]} (2R), TP3 {sell_targets[2]} (3R)."
                if action == "SELL"
                else f"WHEN TO SELL: Wait until news turns Bearish AND {timeframe} candle closes below {sell_trigger_level} with RSI < 50 (current RSI: {f'{rsi:.1f}' if isinstance(rsi, (int, float)) else 'N/A'}). "
                     f"Then enter near {round(float(last_price), decimals)} with Stop Loss at {sell_stop} and TP at {sell_targets[0]} / {sell_targets[1]}."
            ),
        }

    if action == "BUY":
        decision_summary = (
            f"BUY ({meta['name']}): News analysis is {news_sentiment} ({news_score:+.1f}) and technical bias is {technical_bias} "
            f"(Combined score: {combined_score:+.1f}). Look for LONG entries while price holds above support."
        )
    elif action == "SELL":
        decision_summary = (
            f"SELL ({meta['name']}): News analysis is {news_sentiment} ({news_score:+.1f}) and technical bias is {technical_bias} "
            f"(Combined score: {combined_score:+.1f}). Look for SHORT/SELL entries while price stays below resistance."
        )
    else:
        decision_summary = (
            f"WAIT / HOLD ({meta['name']}): News is {news_sentiment} ({news_score:+.1f}) while technical bias is {technical_bias} "
            f"(Combined score: {combined_score:+.1f}). Do not force a trade; wait for the BUY or SELL trigger conditions below."
        )

    return {
        "symbol": symbol,
        "name": meta["name"],
        "khmer_name": meta["khmer"],
        "short_name": meta["short"],
        "action": action,
        "alignment": alignment,
        "combined_score": combined_score,
        "decision_summary": decision_summary,
        "news": news_summary,
        "technical": {
            **tech,
            "bias": technical_bias,
            "net_score": tech_net,
        },
        "buy_plan": buy_plan,
        "sell_plan": sell_plan,
    }

