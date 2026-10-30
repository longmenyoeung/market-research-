from typing import Annotated

from fastapi import APIRouter, Depends

from app.market_data.service import MarketDataService
from app.api.dependencies import get_market_data
from app.schemas.markets import DiscoveryResponse, ErrorResponse

router = APIRouter(prefix="/api/v1/markets", tags=["markets"])


@router.get(
    "", response_model=DiscoveryResponse,
    responses={code: {"model": ErrorResponse} for code in (502, 503, 504)},
)
async def markets(provider: Annotated[MarketDataService, Depends(get_market_data)]) -> DiscoveryResponse:
    """Discover all active USDT-quoted, USDT-margined perpetual contracts."""
    return await provider.discover_contracts()
