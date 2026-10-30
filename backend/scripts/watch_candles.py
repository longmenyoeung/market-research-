"""Print local dashboard WebSocket messages. This is a receive-only diagnostic."""
import argparse
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from websockets.asyncio.client import connect
from app.schemas.candles import CandleQuery


async def main(symbol: str, timeframe: str) -> None:
    query = CandleQuery(symbol=symbol, timeframe=timeframe)
    url = f"ws://127.0.0.1:8000/api/v1/ws/candles/{quote(query.symbol)}?timeframe={query.timeframe}"
    async with connect(url, proxy=None) as socket:
        async for message in socket:
            print(message)
            payload = json.loads(message)
            if payload.get("terminal") or payload.get("type") == "error":
                break


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol", help="A symbol returned by /api/v1/markets")
    parser.add_argument("--timeframe", default="1m")
    args = parser.parse_args()
    try:
        asyncio.run(main(args.symbol, args.timeframe))
    except KeyboardInterrupt:
        pass
