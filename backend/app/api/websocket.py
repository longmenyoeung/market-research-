import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnected

from app.adapters.base import MarketDataError
from app.schemas.candles import CandleQuery
from app.core.http_security import valid_application_key, websocket_application_key

router = APIRouter()


@router.websocket("/api/v1/ws/candles/{symbol}")
async def candle_stream(websocket: WebSocket, symbol: str) -> None:
    settings = websocket.app.state.settings
    if settings.api_key is not None and not valid_application_key(settings.api_key,websocket_application_key(websocket.headers)):
        await websocket.close(code=1008)
        return
    origin = websocket.headers.get("origin")
    if origin is not None and origin not in settings.websocket_origins:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    try:
        params = dict(websocket.query_params)
        if set(params) - {"timeframe"} or len(websocket.query_params.multi_items()) != len(params):
            raise ValueError("Unexpected query parameters")
        query = CandleQuery(symbol=symbol, timeframe=params.get("timeframe", "1h"))
    except (ValueError, ValidationError):
        await websocket.send_json({"type": "error", "code": "invalid_subscription"})
        await websocket.close(code=1008)
        return
    tasks: list[asyncio.Task] = []
    try:
        async with websocket.app.state.stream_hub.subscribe(query.symbol, query.timeframe) as subscriber:
            async def send_updates() -> None:
                while True:
                    message = await subscriber.queue.get()
                    try:
                        await asyncio.wait_for(websocket.send_json(message), timeout=5)
                    except (WebSocketDisconnect, WebSocketDisconnected, OSError):
                        return
                    if message.get("terminal"):
                        try:
                            await websocket.close(code=1013)
                        except (WebSocketDisconnect, WebSocketDisconnected, OSError):
                            pass
                        return

            async def watch_disconnect() -> None:
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    # Receive-only API: never forward client payloads to an exchange.
                    try:
                        await websocket.close(code=1008)
                    finally:
                        return

            tasks = [asyncio.create_task(send_updates()), asyncio.create_task(watch_disconnect())]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            await asyncio.gather(*done, return_exceptions=True)
    except MarketDataError as exc:
        try:
            await websocket.send_json({"type": "error", "code": exc.code, "message": exc.message,
                                       "retry_after_seconds": exc.retry_after_seconds})
            await websocket.close(code=1013)
        except (WebSocketDisconnect, WebSocketDisconnected, OSError):
            pass
    except (WebSocketDisconnect, WebSocketDisconnected, TimeoutError, OSError):
        pass
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
