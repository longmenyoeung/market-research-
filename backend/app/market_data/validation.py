from datetime import UTC, datetime

from pydantic import ValidationError

from app.adapters.base import MarketDataError
from app.schemas.candles import Candle, CandleQuery, CandleUpdate, DataQuality, INTERVAL_MS


def invalid_data() -> MarketDataError:
    return MarketDataError("invalid_upstream_data", "Candle data is incomplete, inconsistent or invalid.")


def require_int(value: object) -> int:
    if type(value) is not int or not 0 <= value <= 4_102_444_800_000:
        raise ValueError("Invalid integer")
    return value


def parse_row(row: object, timeframe: str, server_time: int, closed: bool | None = None) -> Candle:
    if not isinstance(row, list) or len(row) != 12:
        raise ValueError("Expected 12 fields")
    start, end, trades = require_int(row[0]), require_int(row[6]), require_int(row[8])
    duration = INTERVAL_MS[timeframe]
    if start % duration or end != start + duration - 1 or start > server_time:
        raise ValueError("Misaligned or future candle")
    if any(not isinstance(row[index], str) or len(row[index]) > 80 for index in (1, 2, 3, 4, 5, 7, 9, 10)):
        raise ValueError("Prices and volumes must be decimal strings")
    if closed is True and end >= server_time:
        raise ValueError("Future candle cannot be closed")
    return Candle(
        open_time=start, close_time=end, open=row[1], high=row[2], low=row[3], close=row[4],
        volume=row[5], quote_volume=row[7], trades=trades, taker_buy_volume=row[9],
        taker_buy_quote_volume=row[10], is_closed=end < server_time if closed is None else closed,
    )


def parse_candles(payload: object, query: CandleQuery, server_time: int) -> list[Candle]:
    try:
        if not isinstance(payload, list) or len(payload) > query.limit:
            raise ValueError("Invalid batch")
        candles = [parse_row(row, query.timeframe, server_time) for row in payload]
        previous = -1
        for candle in candles:
            if candle.open_time <= previous:
                raise ValueError("Duplicate or unordered candles")
            if query.start_time is not None and candle.open_time < query.start_time:
                raise ValueError("Candle precedes query")
            if query.end_time is not None and candle.open_time > query.end_time:
                raise ValueError("Candle exceeds query")
            previous = candle.open_time
        return candles
    except (ValueError, TypeError, KeyError, ValidationError) as exc:
        raise invalid_data() from exc


def assess_quality(candles: list[Candle], query: CandleQuery, server_time: int) -> DataQuality:
    if not candles:
        return DataQuality(status="empty", warnings=["No candles returned; no data was synthesized."])
    duration = INTERVAL_MS[query.timeframe]
    gaps = sum((b.open_time - a.open_time) // duration - 1 for a, b in zip(candles, candles[1:]))
    warnings = []
    if gaps:
        warnings.append("Missing intervals are not filled or interpolated.")
    if len(candles) < query.limit:
        warnings.append("Fewer candles than requested; listing age, range or availability may limit history.")
    if any(not candle.is_closed for candle in candles):
        warnings.append("The open candle is provisional and must not be used as a confirmed signal.")
    historical = query.end_time is not None and query.end_time < server_time - duration
    # A recent open time proves period coverage, not the age of the last actual trade.
    stale = not historical and candles[-1].open_time + duration < server_time - 5000
    status = "stale" if stale else "incomplete" if gaps else "historical" if historical else "usable"
    if stale:
        warnings.append("Latest candle does not cover the current period.")
    if not historical:
        warnings.append("REST candle data does not provide the last trade's event timestamp.")
    return DataQuality(status=status, missing_intervals=gaps, warnings=warnings)


def parse_update(payload: object, symbol: str, timeframe: str, stale_seconds: float) -> CandleUpdate:
    try:
        if not isinstance(payload, dict) or payload.get("e") != "kline" or payload.get("s") != symbol:
            raise ValueError("Unexpected event")
        kline = payload["k"]
        if not isinstance(kline, dict) or kline.get("s") != symbol or kline.get("i") != timeframe:
            raise ValueError("Unexpected candle identity")
        if type(kline.get("x")) is not bool:
            raise ValueError("Invalid finality flag")
        event_time = require_int(payload["E"])
        now = datetime.now(UTC)
        lag = now.timestamp() * 1000 - event_time
        if lag < -5000:
            raise ValueError("Future event or local clock skew")
        row = [kline[key] for key in ("t", "o", "h", "l", "c", "v", "T", "q", "n", "V", "Q")] + ["0"]
        candle = parse_row(row, timeframe, event_time, kline["x"])
        return CandleUpdate(symbol=symbol, timeframe=timeframe, event_time=event_time,
                            received_at=now, candle=candle, stale=lag > stale_seconds * 1000)
    except (ValueError, TypeError, KeyError, ValidationError) as exc:
        raise invalid_data() from exc
