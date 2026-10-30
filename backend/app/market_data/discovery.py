"""Bound live contract discovery and pace retries after connection failures."""

import asyncio
import logging
import math
import time
from collections.abc import Callable

from app.adapters.base import MarketDataError, MarketDataProvider
from app.schemas.markets import DiscoveryResponse

logger = logging.getLogger(__name__)


class DiscoveryLoader:
    """Called by the shared discovery cache; never serves expired metadata.

    The deadline covers waiting for the adapter, request pacing, HTTP attempts,
    and retry backoff together. Provider access and rate-limit blocks remain
    under the adapter's control.
    """

    def __init__(self, provider: MarketDataProvider, timeout_seconds: float,
                 failure_cooldown_seconds: int, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.provider = provider
        self.timeout_seconds = timeout_seconds
        self.failure_cooldown_seconds = failure_cooldown_seconds
        self.clock = clock
        self._retry_at = 0.0

    async def load(self) -> DiscoveryResponse:
        remaining = self._retry_at - self.clock()
        if remaining > 0:
            raise MarketDataError(
                "discovery_cooldown",
                "Live contract discovery is temporarily unavailable after a public-data connection failure. "
                "Wait for the retry period; saved charts remain historical.",
                503, math.ceil(remaining),
            )

        started = self.clock()
        try:
            async with asyncio.timeout(self.timeout_seconds):
                result = await self.provider.discover_contracts()
        except TimeoutError:
            failure = MarketDataError(
                "upstream_timeout",
                "Live contract discovery exceeded its public-data time budget.", 504,
            )
        except MarketDataError as exc:
            failure = exc
        else:
            self._retry_at = 0.0
            return result

        logger.warning("Contract discovery failed: code=%s elapsed_seconds=%.3f",
                       failure.code, self.clock() - started)
        if failure.code in {"upstream_timeout", "upstream_unavailable"}:
            delay = max(self.failure_cooldown_seconds, failure.retry_after_seconds or 0)
            self._retry_at = self.clock() + delay
            raise MarketDataError(failure.code, failure.message, failure.status_code, delay) from failure
        raise failure
