"""Opt-in public REST/stream check; stops on discovery failure, never substitutes data."""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters.base import MarketDataError
from app.adapters.binance_usdm import BinanceUSDMAdapter
from app.core.config import Settings
from app.schemas.candles import CandleQuery


async def main(with_stream: bool) -> int:
    adapter = BinanceUSDMAdapter(Settings(max_retries=0))
    try:
        universe = await adapter.discover_contracts()
        if not universe.contracts:
            print(json.dumps({"count": 0, "message": "No eligible contracts returned."}))
            return 0
        symbol = universe.contracts[0].symbol
        response = await adapter.fetch_candles(CandleQuery(symbol=symbol, timeframe="1m", limit=3))
        print(response.model_dump_json(indent=2))
        if with_stream:
            source = adapter.stream_candles(symbol, "1m")
            try:
                update = await asyncio.wait_for(anext(source), 20)
                print(update.model_dump_json(indent=2))
            finally:
                await source.aclose()
        return 0
    except MarketDataError as exc:
        print(json.dumps({"code": exc.code, "message": exc.message, "retry_after_seconds": exc.retry_after_seconds}))
        return 1
    except TimeoutError:
        print(json.dumps({"code": "stream_timeout", "message": "No stream event received within 20 seconds."}))
        return 1
    finally:
        await adapter.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stream", action="store_true", help="Also receive one real public candle update")
    raise SystemExit(asyncio.run(main(parser.parse_args().stream)))
