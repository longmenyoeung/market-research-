import asyncio
import json
from collections.abc import AsyncIterator, Callable
from urllib.parse import quote

from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus, WebSocketException

from app.adapters.base import MarketDataError
from app.core.config import Settings
from app.market_data.validation import parse_update
from app.schemas.candles import CandleQuery, CandleUpdate


class NoRedirectConnect(connect):
    def process_redirect(self, exc: Exception) -> Exception:
        # websockets follows redirects by default; never follow one here.
        return exc


def stream_url(symbol: str, timeframe: str) -> str:
    query = CandleQuery(symbol=symbol, timeframe=timeframe)
    # USDⓈ-M routed market endpoint; legacy /ws kline routes were retired.
    # Subscription stays in the URL. No application messages are sent.
    return f"wss://fstream.binance.com/market/ws/{quote(query.symbol.lower(), safe='')}@kline_{query.timeframe}"


async def read_stream(
    symbol: str, timeframe: str, settings: Settings,
    check_status: Callable[[int, str | None], None],
) -> AsyncIterator[CandleUpdate]:
    try:
        async with NoRedirectConnect(
            stream_url(symbol, timeframe), proxy=None, open_timeout=settings.request_timeout_seconds,
            close_timeout=2, ping_interval=20, ping_timeout=20, max_size=64 * 1024, max_queue=16,
        ) as connection:
            # Subscription is encoded in the URL. No application messages are sent
            # to Binance; protocol ping/pong handles connection health only.
            async for raw in connection:
                try:
                    payload = json.loads(raw)
                except (ValueError, UnicodeError) as exc:
                    raise MarketDataError("invalid_upstream_data", "Stream returned invalid JSON.") from exc
                yield parse_update(payload, symbol, timeframe, settings.stream_stale_seconds)
    except InvalidStatus as exc:
        check_status(exc.response.status_code, exc.response.headers.get("Retry-After"))
        raise MarketDataError("stream_rejected", "Provider rejected the public stream handshake.", 502) from exc
    except (OSError, asyncio.TimeoutError, WebSocketException) as exc:
        raise MarketDataError("stream_disconnected", "Public candle stream disconnected.", 502) from exc
