import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from app.adapters.base import MarketDataError, MarketDataProvider
from app.adapters.binance_usdm import BinanceUSDMAdapter
from app.api.routes import health, markets, candles, platform as platform_routes
from app.api import websocket
from app.core.config import Settings
from app.core.logging import configure_logging
from app.core.http_security import AuthenticationFailures, RequestBodyLimitMiddleware, valid_application_key
from app.schemas.markets import ErrorDetail, ErrorResponse
from app.market_data.service import MarketDataService
from app.market_data.streaming import StreamHub
from app.storage.candles import SQLiteCandleRepository
from app.storage.platform import PlatformStore
from app.services.platform import PlatformService
from app.backtesting.service import BacktestService
from fastapi.staticfiles import StaticFiles
from pathlib import Path

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, *, provider: MarketDataProvider | None = None) -> FastAPI:
    config = settings if settings is not None else Settings()
    authentication_failures=AuthenticationFailures()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configure_logging(config.log_level)
        adapter = provider if provider is not None else BinanceUSDMAdapter(config)
        application.state.market_data_provider = adapter
        repository = PlatformStore(config.database_path)
        service = MarketDataService(adapter, repository, config)
        hub = StreamHub(service, config)
        application.state.market_data_service = service
        application.state.stream_hub = hub
        application.state.settings = config
        platform = PlatformService(service, repository, config)
        backtests = BacktestService(service, repository)
        application.state.platform = platform
        application.state.backtests = backtests
        try:
            await repository.initialize()
            await platform.initialize()
            yield
        finally:
            await backtests.aclose()
            await platform.aclose()
            await hub.aclose()
            await service.aclose()
            await repository.aclose()
            await adapter.aclose()

    application = FastAPI(
        title="Manual Crypto Analysis", version="1.0.0",
        description="Manual-only crypto market research, reports and hypothetical planning. No exchange execution.",
        lifespan=lifespan,
    )

    @application.exception_handler(MarketDataError)
    async def market_data_error(_request: Request, exc: MarketDataError) -> JSONResponse:
        logger.warning("Market data failed: code=%s", exc.code)
        headers = {"Cache-Control": "no-store"}
        if exc.retry_after_seconds is not None:
            headers["Retry-After"] = str(exc.retry_after_seconds)
        body = ErrorResponse(error=ErrorDetail(
            code=exc.code, message=exc.message, retry_after_seconds=exc.retry_after_seconds,
        ))
        return JSONResponse(status_code=exc.status_code, content=body.model_dump(mode="json"), headers=headers)

    @application.middleware("http")
    async def no_store(request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path.startswith('/api/') and request.method!='OPTIONS' and config.api_key is not None:
            if not valid_application_key(config.api_key,request.headers.get('x-api-key')):
                status=authentication_failures.reject_status(request.client.host if request.client else 'unknown')
                return JSONResponse({'detail':'A valid application X-API-Key is required.'},status_code=status,
                                    headers={'Cache-Control':'no-store','WWW-Authenticate':'ApiKey','Retry-After':'60'} if status==429 else {'Cache-Control':'no-store','WWW-Authenticate':'ApiKey'})
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin=request.headers.get("origin")
            if origin is not None and origin not in config.websocket_origins:
                return JSONResponse({"error":{"code":"origin_denied","message":"Browser origin is not allowed."}},status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.url.path not in {'/docs','/redoc','/docs/oauth2-redirect'}:
            response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        return response

    application.include_router(health.router)
    application.include_router(markets.router)
    application.include_router(candles.router)
    application.include_router(websocket.router)
    application.include_router(platform_routes.router)
    # Vite dev (127.0.0.1:5173) and the bundled UI share the same allowlist.
    # Credentials are not used, so wildcard origins would still not be acceptable.
    application.add_middleware(CORSMiddleware, allow_origins=config.websocket_origins,
                               allow_credentials=False, allow_methods=["GET", "POST", "PUT", "OPTIONS"],
                               allow_headers=["Content-Type", "X-API-Key"], max_age=600)
    application.add_middleware(RequestBodyLimitMiddleware,max_bytes=config.max_request_body_bytes)
    application.add_middleware(TrustedHostMiddleware,allowed_hosts=config.allowed_hosts)
    frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if frontend.is_dir():
        application.mount("/", StaticFiles(directory=frontend,html=True),name="dashboard")
    return application


app = create_app()
