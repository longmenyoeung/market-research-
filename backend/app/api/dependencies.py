
from fastapi import HTTPException, Request

from app.adapters.base import MarketDataProvider
from app.market_data.service import MarketDataService
from app.core.http_security import valid_application_key

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def get_provider(request: Request) -> MarketDataProvider:
    return request.app.state.market_data_provider


def get_market_data(request: Request) -> MarketDataService:
    return request.app.state.market_data_service


async def require_mutation_access(request: Request) -> None:
    """Protect state-changing routes.

    If MCA_API_KEY is configured, callers must present it. Without a key, only
    loopback clients may mutate; this covers the bundled browser UI and keeps an
    unauthenticated app off the network. Read-only GET routes remain open.
    """
    settings = request.app.state.settings
    if settings.api_key is not None:
        supplied = request.headers.get("X-API-Key", "")
        if not valid_application_key(settings.api_key,supplied):
            raise HTTPException(status_code=401, detail="A valid X-API-Key header is required.",
                                headers={"WWW-Authenticate": "ApiKey"})
        return
    host = request.client.host if request.client else ""
    if host not in LOOPBACK_HOSTS:
        raise HTTPException(status_code=403, detail="Mutations are restricted to loopback clients unless MCA_API_KEY is set.")
