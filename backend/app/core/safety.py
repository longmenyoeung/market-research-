import httpx
from pydantic import ValidationError

from app.schemas.candles import CandleQuery
from app.adapters.metrics import BASE, PUBLIC_BULK_PATHS, PUBLIC_SYMBOL_PATHS
from app.schemas.candles import normalize_symbol
from app.schemas.derivatives import FundingQuery

EXCHANGE_INFO_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"
SERVER_TIME_URL = "https://fapi.binance.com/fapi/v1/time"
KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"


class ReadOnlyViolation(RuntimeError):
    """A request fell outside the public-data allowlist."""


async def enforce_public_request(request: httpx.Request) -> None:
    """Fail closed immediately before transport, including on redirected requests."""
    forbidden_headers = {"authorization", "proxy-authorization", "x-mbx-apikey", "cookie"}
    url = str(request.url.copy_with(query=None))
    valid = url in {EXCHANGE_INFO_URL, SERVER_TIME_URL} and not request.url.query
    if url in {BASE + p for p in PUBLIC_BULK_PATHS}:
        valid = not request.url.query
    if url in {BASE + p for p in PUBLIC_SYMBOL_PATHS}:
        params = dict(request.url.params.multi_items())
        try:
            valid = len(params) == len(request.url.params.multi_items()) and normalize_symbol(params["symbol"]) == params["symbol"]
            rules = {
                BASE + "/fapi/v1/depth": {"symbol": params["symbol"], "limit": "20"},
                BASE + "/fapi/v1/openInterest": {"symbol": params["symbol"]},
                BASE + "/futures/data/openInterestHist": {"symbol": params["symbol"], "period": "1h", "limit": "30"},
                BASE + "/fapi/v1/fundingRate": {"symbol": params["symbol"], "limit": "100"},
            }
            valid = valid and params == rules[url]
            if url == BASE+"/fapi/v1/fundingRate" and set(params)=={"symbol","startTime","endTime","limit"}:
                query = FundingQuery(symbol=params["symbol"],start_time=params["startTime"],
                                     end_time=params["endTime"],limit=params["limit"])
                valid = len(params)==len(request.url.params.multi_items()) and query.symbol==params["symbol"]
        except (ValueError, KeyError):
            valid = False
    if url == KLINES_URL:
        pairs = request.url.params.multi_items()
        params = dict(pairs)
        mapping = {"symbol": "symbol", "interval": "timeframe", "limit": "limit",
                "startTime": "start_time", "endTime": "end_time"}
        try:
            valid = (len(pairs) == len(params) and {"symbol", "interval", "limit"} <= params.keys()
                    and params.keys() <= mapping.keys())
            if valid:
                query = CandleQuery(**{mapping[key]: value for key, value in params.items()})
                valid = query.symbol == params["symbol"]
        except (ValidationError, ValueError):
            valid = False
    if (
        request.method != "GET"
        or not valid
        or request.content
        or forbidden_headers.intersection(request.headers)
    ):
        raise ReadOnlyViolation("Only allowlisted, credential-free public market-data requests are allowed.")
