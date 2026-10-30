from datetime import UTC, datetime

from fastapi import APIRouter, Request

from app.schemas.markets import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """Process liveness only: this endpoint never calls the exchange."""
    return HealthResponse(timestamp=datetime.now(UTC),application_key_required=request.app.state.settings.api_key is not None)
