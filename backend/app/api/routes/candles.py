from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_market_data
from app.market_data.service import MarketDataService
from app.schemas.candles import CandleQuery, CandleResponse, StoredCandles
from app.schemas.markets import ErrorResponse

router = APIRouter(prefix="/api/v1", tags=["candles"])
errors = {code: {"model": ErrorResponse} for code in (404, 502, 503, 504)}


@router.get("/candles", response_model=CandleResponse, responses=errors)
async def candles(query: Annotated[CandleQuery, Query()],
                  service: Annotated[MarketDataService, Depends(get_market_data)]) -> CandleResponse:
    return await service.get_candles(query)


@router.get("/history/candles", response_model=StoredCandles, responses=errors)
async def history(query: Annotated[CandleQuery, Query()],
                  service: Annotated[MarketDataService, Depends(get_market_data)]) -> StoredCandles:
    return await service.archive(query)
