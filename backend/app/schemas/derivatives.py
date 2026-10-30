from typing import Self

from pydantic import Field, field_validator, model_validator

from app.schemas.candles import normalize_symbol
from app.schemas.platform import StrictModel


class FundingQuery(StrictModel):
    symbol: str
    start_time: int = Field(ge=0, le=4_102_444_800_000)
    end_time: int = Field(ge=0, le=4_102_444_800_000)
    limit: int = Field(default=1000, ge=1, le=1000)

    _symbol = field_validator('symbol')(normalize_symbol)

    @model_validator(mode='after')
    def ordered(self) -> Self:
        if self.start_time > self.end_time:
            raise ValueError('Require start_time <= end_time.')
        return self
