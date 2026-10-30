from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.adapters.base import MarketDataError

BASE = "https://fapi.binance.com"
PUBLIC_BULK_PATHS = {"/fapi/v1/ticker/24hr", "/fapi/v1/ticker/bookTicker", "/fapi/v1/premiumIndex"}
PUBLIC_SYMBOL_PATHS = {"/fapi/v1/depth", "/fapi/v1/openInterest", "/futures/data/openInterestHist", "/fapi/v1/fundingRate"}
# These failures apply to the whole session, not just one optional metric.
BLOCKING_CODES = frozenset({"access_restricted", "provider_banned", "rate_limited"})


def number(value: Any, *, positive: bool = False, nonnegative: bool = False) -> float:
    try:
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ValueError()
        result = Decimal(str(value))
        if not result.is_finite() or abs(result) > Decimal("1e30"):
            raise ValueError()
        if positive and result <= 0 or nonnegative and result < 0:
            raise ValueError()
        return float(result)
    except (ValueError, InvalidOperation) as exc:
        raise MarketDataError("invalid_upstream_data", "Market metric contains an invalid numeric value.") from exc


def timestamp(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= 4_102_444_800_000:
        raise MarketDataError("invalid_upstream_data", "Market metric timestamp is invalid.")
    return value


def parse_bulk(payload: object, kind: str) -> dict[str, dict]:
    if not isinstance(payload, list):
        raise MarketDataError("invalid_upstream_data", "Expected a public market-metrics array.")
    result = {}
    try:
        for item in payload:
            symbol = item["symbol"]
            if not isinstance(symbol, str) or symbol in result:
                raise ValueError()
            if kind == "ticker":
                data = {"last_price": number(item["lastPrice"], positive=True),
                        "change_percent": number(item["priceChangePercent"]),
                        "volume": number(item["quoteVolume"], nonnegative=True),
                        "ticker_time": timestamp(item["closeTime"])}
            elif kind == "book":
                bid, ask = number(item["bidPrice"], nonnegative=True), number(item["askPrice"], nonnegative=True)
                data = {"bid": bid or None, "ask": ask or None,
                        "spread_bps": (ask - bid) / ((ask + bid) / 2) * 10000 if 0 < bid <= ask else None,
                        "book_time": timestamp(item["time"])}
            else:
                data = {"mark_price": number(item["markPrice"], positive=True),
                        "index_price": number(item["indexPrice"], positive=True),
                        "funding_rate": number(item["lastFundingRate"]),
                        "next_funding_time": timestamp(item["nextFundingTime"]),
                        "mark_time": timestamp(item["time"])}
            result[symbol] = data
    except (KeyError, TypeError, ValueError) as exc:
        raise MarketDataError("invalid_upstream_data", "Public market-metrics schema is invalid.") from exc
    return result


async def fetch_bulk(adapter) -> dict:
    rows: dict[str, dict] = {}
    limitations = []
    for kind, path in [("ticker", "/fapi/v1/ticker/24hr"), ("book", "/fapi/v1/ticker/bookTicker"), ("mark", "/fapi/v1/premiumIndex")]:
        try:
            data = parse_bulk(await adapter._read(BASE + path), kind)
            for symbol, values in data.items():
                rows.setdefault(symbol, {}).update(values)
        except MarketDataError as exc:
            if exc.code in BLOCKING_CODES:
                raise
            limitations.append(f"{kind}: {exc.code}")
    return {"fetched_at": datetime.now(UTC).isoformat(), "rows": rows, "limitations": limitations}


async def fetch_derivatives(adapter, symbol: str) -> dict:
    result: dict = {"symbol": symbol, "fetched_at": datetime.now(UTC).isoformat(), "limitations": []}
    specs = [("depth", "/fapi/v1/depth", {"symbol": symbol, "limit": "20"}),
             ("open_interest", "/fapi/v1/openInterest", {"symbol": symbol}),
             ("open_interest_history", "/futures/data/openInterestHist", {"symbol": symbol, "period": "1h", "limit": "30"}),
             ("funding_history", "/fapi/v1/fundingRate", {"symbol": symbol, "limit": "100"})]
    for key, path, params in specs:
        try:
            payload = await adapter._read(BASE + path, params)
            if key == "depth":
                books = {side: [[number(p, positive=True), number(q, nonnegative=True)] for p, q in payload[side]]
                         for side in ("bids", "asks")}
                if not books["bids"] or not books["asks"] or max(p for p, _ in books["bids"]) >= min(p for p, _ in books["asks"]):
                    raise ValueError("Crossed or empty book")
                result[key] = {**books, "event_time": timestamp(payload["E"]), "levels": 20}
            elif key == "open_interest":
                if payload["symbol"] != symbol:
                    raise ValueError()
                result[key] = {"value": number(payload["openInterest"], nonnegative=True), "time": timestamp(payload["time"])}
            else:
                if not isinstance(payload, list):
                    raise ValueError()
                points = []
                for item in payload:
                    if item["symbol"] != symbol:
                        raise ValueError()
                    if key == "funding_history":
                        points.append({"time": timestamp(item["fundingTime"]), "rate": number(item["fundingRate"])})
                    else:
                        points.append({"time": timestamp(item["timestamp"]), "value": number(item["sumOpenInterest"], nonnegative=True),
                                       "notional": number(item["sumOpenInterestValue"], nonnegative=True)})
                if any(a["time"] >= b["time"] for a, b in zip(points, points[1:])):
                    raise ValueError()
                result[key] = points
        except MarketDataError as exc:
            if exc.code in BLOCKING_CODES:
                raise
            result[key] = None
            result["limitations"].append(f"{key}: {exc.code}")
        except (ValueError, KeyError, TypeError):
            result[key] = None
            result["limitations"].append(f"{key}: invalid_upstream_data")
    return result
