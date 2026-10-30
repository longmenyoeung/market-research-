from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Timeframe = Literal["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
INTERVAL_MS: dict[str, int] = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
}
Timestamp = Annotated[int, Field(ge=0, le=4_102_444_800_000)]
Price = Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]
Volume = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]


def normalize_symbol(value: str) -> str:
    # Unicode alphanumeric symbols are allowed; URL delimiters and whitespace aren't.
    if not 1 <= len(value) <= 40 or not all(c.isalnum() or c == "_" for c in value):
        raise ValueError("Symbol must contain 1–40 letters, digits or underscores.")
    return value.upper()


class CandleQuery(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    symbol: str
    timeframe: Timeframe = "1h"
    limit: int = Field(default=500, ge=1, le=1500)
    start_time: Timestamp | None = None
    end_time: Timestamp | None = None

    _symbol = field_validator("symbol")(normalize_symbol)

    @model_validator(mode="after")
    def ordered_range(self) -> Self:
        if self.start_time is not None and self.end_time is not None and self.start_time > self.end_time:
            raise ValueError("start_time must not exceed end_time.")
        return self


class Candle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    open_time: Timestamp
    close_time: Timestamp
    open: Price
    high: Price
    low: Price
    close: Price
    volume: Volume
    quote_volume: Volume
    trades: int = Field(ge=0)
    taker_buy_volume: Volume
    taker_buy_quote_volume: Volume
    is_closed: bool

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise ValueError("OHLC prices are inconsistent.")
        if self.close_time <= self.open_time:
            raise ValueError("Invalid candle timestamps.")
        if self.taker_buy_volume > self.volume or self.taker_buy_quote_volume > self.quote_volume:
            raise ValueError("Taker volume exceeds total volume.")
        return self


class DataQuality(BaseModel):
    status: Literal["usable", "incomplete", "stale", "empty", "historical"]
    missing_intervals: int = 0
    warnings: list[str] = Field(default_factory=list)


class CandleResponse(BaseModel):
    provider: Literal["binance_usdm"] = "binance_usdm"
    symbol: str
    timeframe: Timeframe
    fetched_at: datetime
    server_time: Timestamp
    time_basis: Literal["exchange_clock_extrapolated"] = "exchange_clock_extrapolated"
    candles: list[Candle]
    quality: DataQuality
    next_start_time: Timestamp | None = None
    cache_hit: bool = False
    cache_age_seconds: float = 0


class CandleUpdate(BaseModel):
    type: Literal["candle"] = "candle"
    symbol: str
    timeframe: Timeframe
    event_time: Timestamp
    received_at: datetime
    candle: Candle
    stale: bool
    # Price is the last trade reflected in the candle, not mark or bid/ask price.
    price_basis: Literal["candle_last_trade"] = "candle_last_trade"


class StoredCandles(BaseModel):
    symbol: str
    timeframe: Timeframe
    source: Literal["local_archive"] = "local_archive"
    live_data: Literal[False] = False
    candles: list[Candle]
    quality: DataQuality
