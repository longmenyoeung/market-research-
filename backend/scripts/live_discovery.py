"""Optional real-network diagnostic. No synthetic fallback and no retries."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters.base import MarketDataError
from app.adapters.binance_usdm import BinanceUSDMAdapter
from app.core.config import Settings


async def main() -> int:
    adapter = BinanceUSDMAdapter(Settings(max_retries=0))
    try:
        result = await adapter.discover_contracts()
        print(json.dumps({
            "provider": result.provider,
            "fetched_at": result.fetched_at.isoformat(),
            "count": result.count,
            "first_five_symbols": [contract.symbol for contract in result.contracts[:5]],
            "eligibility_notice": result.eligibility_notice,
        }, indent=2))
        return 0
    except MarketDataError as exc:
        print(json.dumps({"code": exc.code, "message": exc.message,
                          "retry_after_seconds": exc.retry_after_seconds}, indent=2))
        return 1
    finally:
        await adapter.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
