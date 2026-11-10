from pydantic import Field, field_validator

from app.schemas.candles import Timeframe, normalize_symbol
from app.schemas.platform import StrictModel


class NewsScanRequest(StrictModel):
    timeframe: Timeframe = "1h"
    custom_headlines: list[str] = Field(default_factory=list, max_length=30)
    fetch_live_rss: bool = True
    symbol: str | None = None

    @field_validator("symbol")
    @classmethod
    def validate_optional_symbol(cls, value: str | None) -> str | None:
        return normalize_symbol(value) if value else None

    @field_validator("custom_headlines")
    @classmethod
    def clean_headlines(cls, value: list[str]) -> list[str]:
        cleaned = []
        for item in value:
            text = item.strip()
            if text:
                cleaned.append(text[:500])
        return cleaned

