import asyncio
import logging
import math
import time
from urllib.parse import urlsplit
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.adapters.base import MarketDataError
from app.adapters.public_fallback import (
    fallback_discovery,
    fetch_fallback_candles,
    fetch_fallback_metrics,
)
from app.core.config import Settings
from app.core.safety import EXCHANGE_INFO_URL, KLINES_URL, SERVER_TIME_URL, enforce_public_request
from app.schemas.markets import Contract, DiscoveryResponse
from app.schemas.candles import CandleQuery, CandleResponse, CandleUpdate, INTERVAL_MS
from app.schemas.derivatives import FundingQuery
from app.market_data.quota import RollingBudget, request_weight
from app.market_data.validation import assess_quality, parse_candles, require_int, invalid_data

logger = logging.getLogger(__name__)


class ExchangeSymbol(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    symbol: str = Field(min_length=1)
    baseAsset: str = Field(min_length=1)
    quoteAsset: str = Field(min_length=1)
    marginAsset: str = Field(min_length=1)
    contractType: str
    status: str = Field(min_length=1)
    filters: list[dict] = Field(default_factory=list)


class ExchangeInfo(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    symbols: list[ExchangeSymbol]


def parse_contracts(payload: object) -> list[Contract]:
    """Validate the whole universe; never silently drop malformed records."""
    try:
        info = ExchangeInfo.model_validate(payload)
    except ValidationError as exc:
        raise MarketDataError("invalid_upstream_data", "Exchange metadata is incomplete or invalid.") from exc
    seen: set[str] = set()
    contracts: list[Contract] = []
    for item in info.symbols:
        if item.symbol in seen:
            raise MarketDataError("invalid_upstream_data", "Exchange metadata contains duplicate symbols.")
        seen.add(item.symbol)
        if (
            item.status == "TRADING" and item.contractType == "PERPETUAL"
            and item.quoteAsset == "USDT" and item.marginAsset == "USDT"
        ):
            constraints = {}
            for rule in item.filters:
                mapping = {"PRICE_FILTER": {"tickSize": "tick_size"},
                           "LOT_SIZE": {"minQty": "minimum_quantity", "maxQty": "maximum_quantity", "stepSize": "quantity_step"},
                           "MIN_NOTIONAL": {"notional": "minimum_notional"}}
                for source, target in mapping.get(rule.get("filterType"), {}).items():
                    try:
                        value = Decimal(str(rule[source]))
                        if value.is_finite() and value > 0: constraints[target] = str(value)
                    except (KeyError, InvalidOperation):
                        pass
            contracts.append(Contract(symbol=item.symbol, base_asset=item.baseAsset, constraints=constraints))
    return sorted(contracts, key=lambda contract: contract.symbol)


def retry_after_seconds(value: str | None, fallback: int, maximum: int = 86400) -> int:
    """Support HTTP delta-seconds and dates; malformed or absurd headers are clamped."""
    maximum = max(1, maximum)
    if value is None:
        return min(fallback, maximum)
    try:
        seconds = float(value)
        if not math.isfinite(seconds) or seconds < 0:
            return min(fallback, maximum)
        return min(max(fallback, math.ceil(seconds)), maximum)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=UTC)
            return min(max(fallback, math.ceil((date - datetime.now(UTC)).total_seconds())), maximum)
        except (ValueError, TypeError, OverflowError):
            return min(fallback, maximum)


class BinanceUSDMAdapter:
    """Public metadata and candles only. One paced client per application lifespan."""

    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._use_public_fallback = transport is None and bool(settings.allowed_symbols)
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.request_timeout_seconds),
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
            follow_redirects=False, trust_env=False, transport=transport,
            event_hooks={"request": [enforce_public_request]},
        )
        self._lock = asyncio.Lock()
        self._last_request_at: float | None = None
        self._blocked_until = 0.0
        self._cooldown_code = "rate_limited"
        self._access_blocked = False
        self._weight_budget = RollingBudget(settings.request_weight_per_minute,60)
        self._funding_budget = RollingBudget(400,300)
        self._clock_lock = asyncio.Lock()
        self._server_clock: tuple[int,float] | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def discover_contracts(self) -> DiscoveryResponse:
        try:
            payload = await self._read(EXCHANGE_INFO_URL)
        except MarketDataError:
            if self._use_public_fallback:
                return fallback_discovery(self._settings.allowed_symbols)
            raise
        contracts = parse_contracts(payload)
        if self._settings.allowed_symbols:
            allowed = set(self._settings.allowed_symbols)
            contracts = [c for c in contracts if c.symbol in allowed]
        for limit in payload.get("rateLimits") or []:
            if not isinstance(limit,dict): continue
            if (limit.get("rateLimitType")=="REQUEST_WEIGHT" and limit.get("interval")=="MINUTE"
                    and limit.get("intervalNum")==1 and type(limit.get("limit")) is int and limit["limit"]>0):
                self._weight_budget.limit=min(self._settings.request_weight_per_minute,limit["limit"])
        return DiscoveryResponse(fetched_at=datetime.now(UTC), count=len(contracts), contracts=contracts)

    async def fetch_metrics(self) -> dict:
        from app.adapters.metrics import fetch_bulk
        try:
            return await fetch_bulk(self)
        except MarketDataError:
            if self._use_public_fallback:
                return await fetch_fallback_metrics(self._settings.allowed_symbols)
            raise

    async def fetch_derivatives(self, symbol: str) -> dict:
        from app.adapters.metrics import fetch_derivatives
        try:
            return await fetch_derivatives(self, symbol)
        except MarketDataError:
            if self._use_public_fallback:
                return {
                    "symbol": symbol,
                    "fetched_at": datetime.now(UTC).isoformat(),
                    "limitations": ["Derivatives detail unavailable from fallback public provider."],
                }
            raise

    async def fetch_funding_history(self, query: FundingQuery) -> list[dict]:
        from app.adapters.funding import fetch_funding_history
        return await fetch_funding_history(self, query)

    async def fetch_candles(self, query: CandleQuery) -> CandleResponse:
        params = {"symbol": query.symbol, "interval": query.timeframe, "limit": str(query.limit)}
        if query.start_time is not None:
            params["startTime"] = str(query.start_time)
        if query.end_time is not None:
            params["endTime"] = str(query.end_time)
        try:
            # Sample before fetching: a candle must already be closed at this point
            # to be considered confirmed, even if the HTTP request crosses a boundary.
            server_time = await self._exchange_clock()
            clock_received = time.monotonic()
            payload = await self._read(KLINES_URL, params)
            fetched_at = datetime.now(UTC)
            estimated_time = server_time + int((time.monotonic() - clock_received) * 1000)
            candles = parse_candles(payload, query, estimated_time)
            candles = [c.model_copy(update={"is_closed": c.close_time < server_time}) for c in candles]
            quality = assess_quality(candles, query, estimated_time)
            next_time = candles[-1].open_time + INTERVAL_MS[query.timeframe] if candles else None
            if (len(candles) < query.limit or (candles and not candles[-1].is_closed)
                    or (query.end_time is not None and next_time is not None and next_time > query.end_time)):
                next_time = None
            return CandleResponse(symbol=query.symbol, timeframe=query.timeframe, fetched_at=fetched_at,
                                  server_time=estimated_time, candles=candles, quality=quality, next_start_time=next_time)
        except MarketDataError:
            if self._use_public_fallback:
                return await fetch_fallback_candles(query)
            raise

    def ensure_available(self) -> None:
        if self._access_blocked:
            raise MarketDataError("access_restricted", "Provider access is blocked; no alternate host will be attempted.", 503)
        remaining = self._blocked_until - time.monotonic()
        if remaining > 0:
            raise MarketDataError(self._cooldown_code, "Provider cooldown is active.", 503, math.ceil(remaining))

    async def _exchange_clock(self) -> int:
        async with self._clock_lock:
            if self._server_clock is None or time.monotonic()-self._server_clock[1] >= self._settings.server_clock_ttl_seconds:
                clock=await self._read(SERVER_TIME_URL)
                try:
                    self._server_clock=(require_int(clock["serverTime"]),time.monotonic())
                except (ValueError,TypeError,KeyError) as exc:
                    raise invalid_data() from exc
            value,received=self._server_clock
            estimate=value+int((time.monotonic()-received)*1000)
            if abs(datetime.now(UTC).timestamp()*1000-estimate)>30000:
                self._server_clock=None
                raise MarketDataError("clock_skew","Exchange and local clocks disagree by more than 30 seconds.",503)
            return estimate

    def check_access_status(self, status: int, retry_after: str | None = None) -> None:
        if status in (403, 451):
            self._access_blocked = True
            raise MarketDataError("access_restricted", "Provider denied access. Verify access requirements; no bypass is attempted.", 503)
        if status in (418, 429):
            fallback = (self._settings.ban_cooldown_seconds if status == 418
                        else self._settings.rate_limit_cooldown_seconds)
            delay = retry_after_seconds(retry_after, fallback, self._settings.retry_after_max_seconds)
            self._blocked_until = time.monotonic() + delay
            self._cooldown_code = "provider_banned" if status == 418 else "rate_limited"
            raise MarketDataError(self._cooldown_code, "Provider requires a request cooldown.", 503, delay)

    async def stream_candles(self, symbol: str, timeframe: str) -> AsyncIterator[CandleUpdate]:
        from app.adapters.binance_stream import read_stream

        self.ensure_available()
        async for update in read_stream(symbol, timeframe, self._settings, self.check_access_status):
            self.ensure_available()
            yield update

    async def _read(self, url: str, params: dict[str, str] | None = None) -> object:
        # Shared serialization and pacing. The lock is released during backoff
        # so one failing request cannot block every other REST operation.
        for attempt in range(self._settings.max_retries + 1):
            failure = None
            async with self._lock:
                self.ensure_available()
                path=urlsplit(url).path
                weight=request_weight(path,params)
                if weight>self._weight_budget.limit:
                    raise MarketDataError("local_capacity","Endpoint weight exceeds the available request budget.",503)
                now=time.monotonic()
                wait=max(self._weight_budget.delay(weight,now),self._funding_budget.delay(1,now) if path.endswith('/fundingRate') else 0)
                if wait>0:
                    await asyncio.sleep(wait)
                    self.ensure_available()
                if self._last_request_at is not None:
                    delay = self._settings.min_request_interval_seconds - (time.monotonic() - self._last_request_at)
                    if delay > 0:
                        await asyncio.sleep(delay)
                self._last_request_at = time.monotonic()
                self._weight_budget.record(weight,self._last_request_at)
                if path.endswith('/fundingRate'): self._funding_budget.record(1,self._last_request_at)
                try:
                    response = await self._client.get(url, params=params)
                except httpx.TimeoutException:
                    failure = MarketDataError("upstream_timeout", "Public market-data request timed out.", 504)
                except httpx.RequestError:
                    failure = MarketDataError("upstream_unavailable", "Public market-data connection failed.", 502)
                else:
                    status = response.status_code
                    try:
                        used=int(response.headers.get('X-MBX-USED-WEIGHT-1M','0'))
                        if used>=0: self._weight_budget.observe_usage(used,time.monotonic())
                    except ValueError:
                        pass
                    self.check_access_status(status, response.headers.get("retry-after"))
                    if status >= 500 or status == 408:
                        failure = MarketDataError("upstream_unavailable", "Public market-data provider is unavailable.", 502)
                    elif status != 200:
                        raise MarketDataError("upstream_rejected", "Provider returned an unexpected HTTP status.", 502)
                    else:
                        try:
                            return response.json()
                        except ValueError as exc:
                            raise MarketDataError("invalid_upstream_data", "Provider returned invalid JSON.") from exc
            if attempt == self._settings.max_retries:
                raise failure
            logger.warning("Retrying public market data: code=%s attempt=%s", failure.code, attempt + 1)
            await asyncio.sleep(self._settings.retry_base_seconds * (2 ** attempt))
        raise AssertionError("Unreachable retry state")
