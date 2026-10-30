from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

from app.schemas.candles import CandleQuery, CandleResponse, CandleUpdate
from app.schemas.markets import DiscoveryResponse
from app.schemas.derivatives import FundingQuery


class MarketDataError(Exception):
    """Sanitized provider failure, safe to expose through the API."""

    def __init__(
        self, code: str, message: str, status_code: int = 502,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


@runtime_checkable
class MarketDataProvider(Protocol):
    """Structural contract for a read-only public market-data provider.

    ``fetch_metrics`` and ``fetch_derivatives`` are optional on the wire, but a
    concrete provider must expose them so the analysis platform can degrade to
    explicit limitations instead of failing with ``AttributeError``.
    """

    async def discover_contracts(self) -> DiscoveryResponse: ...

    async def fetch_candles(self, query: CandleQuery) -> CandleResponse: ...

    async def fetch_metrics(self) -> dict[str, Any]: ...

    async def fetch_derivatives(self, symbol: str) -> dict[str, Any]: ...

    async def fetch_funding_history(self, query: FundingQuery) -> list[dict]: ...

    def stream_candles(self, symbol: str, timeframe: str) -> AsyncIterator[CandleUpdate]: ...

    async def aclose(self) -> None: ...
