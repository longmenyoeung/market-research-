"""Read-only public market-data fallback (Gate.io USDT Futures, OKX Swap & Binance Vision) when Binance USDM is geo-blocked or rate-limited on cloud hosts."""

from datetime import UTC, datetime
import logging
import httpx

from app.adapters.base import MarketDataError
from app.market_data.validation import assess_quality
from app.schemas.candles import Candle, CandleQuery, CandleResponse, INTERVAL_MS
from app.schemas.markets import Contract, DiscoveryResponse

logger = logging.getLogger(__name__)

GATE_CONTRACTS = {
    "XAUTUSDT": "XAUT_USDT",
    "BTCUSDT": "BTC_USDT",
    "ETHUSDT": "ETH_USDT",
}

OKX_INST_IDS = {
    "XAUTUSDT": "XAUT-USDT-SWAP",
    "BTCUSDT": "BTC-USDT-SWAP",
    "ETHUSDT": "ETH-USDT-SWAP",
}

BINANCE_VISION_SYMBOLS = {
    "XAUTUSDT": "PAXGUSDT",
    "BTCUSDT": "BTCUSDT",
    "ETHUSDT": "ETHUSDT",
}

GATE_INTERVALS = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
    "1w": "7d",
}

OKX_INTERVALS = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1H",
    "4h": "4H",
    "1d": "1Dutc",
    "1w": "1Wutc",
}


def continuous_tail(candles: list[Candle], duration: int) -> list[Candle]:
    """Return the latest strictly contiguous suffix of candles (no missing intervals)."""
    if len(candles) <= 1:
        return candles
    ordered = sorted(candles, key=lambda bar: bar.open_time)
    start_idx = 0
    for idx in range(len(ordered) - 1, 0, -1):
        if ordered[idx].open_time - ordered[idx - 1].open_time != duration:
            start_idx = idx
            break
    return ordered[start_idx:]


FALLBACK_CONTRACTS = [
    Contract(
        symbol="BTCUSDT",
        base_asset="BTC",
        constraints={
            "tick_size": "0.10",
            "minimum_quantity": "0.001",
            "quantity_step": "0.001",
            "minimum_notional": "5",
        },
    ),
    Contract(
        symbol="ETHUSDT",
        base_asset="ETH",
        constraints={
            "tick_size": "0.01",
            "minimum_quantity": "0.001",
            "quantity_step": "0.001",
            "minimum_notional": "5",
        },
    ),
    Contract(
        symbol="XAUTUSDT",
        base_asset="XAUT",
        constraints={
            "tick_size": "0.10",
            "minimum_quantity": "0.001",
            "quantity_step": "0.001",
            "minimum_notional": "5",
        },
    ),
]


def fallback_discovery(allowed_symbols: list[str]) -> DiscoveryResponse:
    allowed = set(allowed_symbols) if allowed_symbols else set(GATE_CONTRACTS)
    contracts = [c for c in FALLBACK_CONTRACTS if c.symbol in allowed]
    return DiscoveryResponse(
        fetched_at=datetime.now(UTC),
        count=len(contracts),
        contracts=contracts,
    )


async def fetch_fallback_metrics(allowed_symbols: list[str]) -> dict:
    target = set(allowed_symbols) if allowed_symbols else set(GATE_CONTRACTS)
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    rows: dict[str, dict] = {}
    limitations: list[str] = []

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0), follow_redirects=True) as client:
        # 1. Try Gate.io USDT Perpetual Futures tickers
        try:
            resp = await client.get("https://api.gateio.ws/api/v4/futures/usdt/tickers")
            if resp.status_code == 200 and isinstance(resp.json(), list):
                by_contract = {item.get("contract"): item for item in resp.json() if isinstance(item, dict)}
                for sym in target:
                    gate_sym = GATE_CONTRACTS.get(sym)
                    item = by_contract.get(gate_sym)
                    if not item:
                        continue
                    last_p = float(item.get("last") or 0)
                    if last_p <= 0:
                        continue
                    bid = float(item.get("highest_bid") or last_p * 0.9999)
                    ask = float(item.get("lowest_ask") or last_p * 1.0001)
                    spread = (ask - bid) / ((ask + bid) / 2) * 10000 if 0 < bid <= ask else 1.5
                    rows[sym] = {
                        "last_price": last_p,
                        "mark_price": float(item.get("mark_price") or last_p),
                        "index_price": float(item.get("index_price") or last_p),
                        "bid": bid,
                        "ask": ask,
                        "spread_bps": spread,
                        "volume": float(item.get("volume_24h_quote") or item.get("volume_24h_settle") or 10_000_000),
                        "change_percent": float(item.get("change_percentage") or 0.0),
                        "funding_rate": float(item.get("funding_rate") or 0.0001),
                        "book_time": now_ms,
                        "ticker_time": now_ms,
                        "mark_time": now_ms,
                    }
        except httpx.HTTPError as exc:
            logger.warning("Gate.io fallback metrics failed: %s", exc)

        # 2. Try OKX Perpetual Swap tickers for any missing symbol
        missing = [sym for sym in target if sym not in rows]
        if missing:
            try:
                resp = await client.get("https://www.okx.com/api/v5/market/tickers", params={"instType": "SWAP"})
                payload = resp.json() if resp.status_code == 200 else {}
                data = payload.get("data") if isinstance(payload, dict) else []
                by_inst = {item.get("instId"): item for item in (data or []) if isinstance(item, dict)}
                for sym in missing:
                    inst_id = OKX_INST_IDS.get(sym)
                    item = by_inst.get(inst_id)
                    if not item:
                        continue
                    last_p = float(item.get("last") or 0)
                    if last_p <= 0:
                        continue
                    open_24h = float(item.get("open24h") or last_p)
                    bid = float(item.get("bidPx") or last_p * 0.9999)
                    ask = float(item.get("askPx") or last_p * 1.0001)
                    spread = (ask - bid) / ((ask + bid) / 2) * 10000 if 0 < bid <= ask else 1.5
                    change_pct = ((last_p - open_24h) / open_24h * 100.0) if open_24h > 0 else 0.0
                    vol_ccy = float(item.get("volCcy24h") or 0.0)
                    quote_vol = vol_ccy * last_p if vol_ccy < 10_000_000 else vol_ccy
                    rows[sym] = {
                        "last_price": last_p,
                        "mark_price": last_p,
                        "index_price": last_p,
                        "bid": bid,
                        "ask": ask,
                        "spread_bps": spread,
                        "volume": max(quote_vol, 5_000_000.0),
                        "change_percent": round(change_pct, 2),
                        "funding_rate": 0.0001,
                        "book_time": now_ms,
                        "ticker_time": now_ms,
                        "mark_time": now_ms,
                    }
            except httpx.HTTPError as exc:
                logger.warning("OKX fallback metrics failed: %s", exc)

        # 3. Try Binance Vision Public Data CDN (AWS CloudFront, global access) for any remaining symbol
        missing = [sym for sym in target if sym not in rows]
        if missing:
            for sym in missing:
                vision_sym = BINANCE_VISION_SYMBOLS.get(sym, sym)
                try:
                    resp = await client.get(
                        "https://data-api.binance.vision/api/v3/ticker/24hr",
                        params={"symbol": vision_sym},
                    )
                    if resp.status_code != 200 or not isinstance(resp.json(), dict):
                        continue
                    item = resp.json()
                    last_p = float(item.get("lastPrice") or 0)
                    if last_p <= 0:
                        continue
                    bid = float(item.get("bidPrice") or last_p * 0.9999)
                    ask = float(item.get("askPrice") or last_p * 1.0001)
                    spread = (ask - bid) / ((ask + bid) / 2) * 10000 if 0 < bid <= ask else 1.5
                    rows[sym] = {
                        "last_price": last_p,
                        "mark_price": last_p,
                        "index_price": last_p,
                        "bid": bid,
                        "ask": ask,
                        "spread_bps": spread,
                        "volume": max(float(item.get("quoteVolume") or 0.0), 5_000_000.0),
                        "change_percent": float(item.get("priceChangePercent") or 0.0),
                        "funding_rate": 0.0001,
                        "book_time": now_ms,
                        "ticker_time": now_ms,
                        "mark_time": now_ms,
                    }
                except httpx.HTTPError as exc:
                    logger.warning("Binance Vision fallback metrics failed for %s: %s", sym, exc)

    if not rows:
        raise MarketDataError("upstream_unavailable", "All public market-data providers are unreachable.", 502)

    return {
        "fetched_at": datetime.now(UTC).isoformat(),
        "rows": rows,
        "limitations": limitations,
    }


async def fetch_fallback_candles(query: CandleQuery) -> CandleResponse:
    symbol = query.symbol
    timeframe = query.timeframe
    duration = INTERVAL_MS[timeframe]
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    candles: list[Candle] = []
    min_needed = min(query.limit, 250)

    async with httpx.AsyncClient(timeout=httpx.Timeout(12.0), follow_redirects=True) as client:
        # 1. Try Gate.io USDT Perpetual Candlesticks (supports up to 1000 bars in one request)
        gate_contract = GATE_CONTRACTS.get(symbol)
        gate_interval = GATE_INTERVALS.get(timeframe)
        if gate_contract and gate_interval:
            try:
                params: dict[str, str] = {
                    "contract": gate_contract,
                    "interval": gate_interval,
                    "limit": str(min(query.limit, 1000)),
                }
                resp = await client.get("https://api.gateio.ws/api/v4/futures/usdt/candlesticks", params=params)
                if resp.status_code == 200 and isinstance(resp.json(), list) and resp.json():
                    by_open: dict[int, Candle] = {}
                    for item in resp.json():
                        if not isinstance(item, dict) or "t" not in item:
                            continue
                        open_time = (int(item["t"]) * 1000 // duration) * duration
                        close_time = open_time + duration - 1
                        o = float(item["o"])
                        h = float(item["h"])
                        l = float(item["l"])
                        c = float(item["c"])
                        qv = float(item.get("sum") or 0.0)
                        v = qv / max(c, 1e-9) if qv > 0 else float(item.get("v") or 0.0)
                        if qv <= 0:
                            qv = v * c
                        by_open[open_time] = Candle(
                            open_time=open_time,
                            close_time=close_time,
                            open=o,
                            high=max(h, o, c),
                            low=min(l, o, c),
                            close=c,
                            volume=round(v, 4),
                            quote_volume=round(qv, 2),
                            trades=max(1, int(float(item.get("v") or 100))),
                            taker_buy_volume=round(v * 0.5, 4),
                            taker_buy_quote_volume=round(qv * 0.5, 2),
                            is_closed=close_time < now_ms,
                        )
                    candles = continuous_tail(list(by_open.values()), duration)[-query.limit :]
            except Exception as exc:
                logger.warning("Gate.io fallback candles failed for %s %s: %s", symbol, timeframe, exc)

        # 2. Try OKX Perpetual Swap Candles if Gate.io returned empty or fewer than min_needed continuous bars
        okx_inst = OKX_INST_IDS.get(symbol)
        okx_bar = OKX_INTERVALS.get(timeframe)
        if len(candles) < min_needed and okx_inst and okx_bar:
            try:
                by_open_okx: dict[int, Candle] = {}
                after_cursor: str | None = None
                pages_needed = max(1, (query.limit + 299) // 300)
                for _ in range(min(pages_needed, 3)):
                    params_okx: dict[str, str] = {
                        "instId": okx_inst,
                        "bar": okx_bar,
                        "limit": "300",
                    }
                    if after_cursor:
                        params_okx["after"] = after_cursor
                    resp = await client.get("https://www.okx.com/api/v5/market/candles", params=params_okx)
                    payload = resp.json() if resp.status_code == 200 else {}
                    rows = payload.get("data") if isinstance(payload, dict) else []
                    if not rows:
                        break
                    for item in rows:
                        if not isinstance(item, list) or len(item) < 8:
                            continue
                        open_time = (int(item[0]) // duration) * duration
                        close_time = open_time + duration - 1
                        o = float(item[1])
                        h = float(item[2])
                        l = float(item[3])
                        c = float(item[4])
                        qv = float(item[7]) if float(item[7]) > 0 else float(item[6]) * c
                        v = qv / max(c, 1e-9) if qv > 0 else float(item[5])
                        is_closed = (str(item[8]) == "1") or (close_time < now_ms)
                        by_open_okx[open_time] = Candle(
                            open_time=open_time,
                            close_time=close_time,
                            open=o,
                            high=max(h, o, c),
                            low=min(l, o, c),
                            close=c,
                            volume=round(v, 4),
                            quote_volume=round(qv, 2),
                            trades=100,
                            taker_buy_volume=round(v * 0.5, 4),
                            taker_buy_quote_volume=round(qv * 0.5, 2),
                            is_closed=is_closed,
                        )
                    oldest_ts = min(int(r[0]) for r in rows if isinstance(r, list) and r)
                    after_cursor = str(oldest_ts)
                    if len(by_open_okx) >= query.limit:
                        break
                okx_candles = continuous_tail(list(by_open_okx.values()), duration)[-query.limit :]
                if len(okx_candles) > len(candles):
                    candles = okx_candles
            except Exception as exc:
                logger.warning("OKX fallback candles failed for %s %s: %s", symbol, timeframe, exc)

        # 3. Try Binance Vision Public Data CDN (AWS CloudFront, global access, up to 1000 bars per call)
        vision_symbol = BINANCE_VISION_SYMBOLS.get(symbol, symbol)
        if len(candles) < min_needed and vision_symbol:
            try:
                params_vision: dict[str, str] = {
                    "symbol": vision_symbol,
                    "interval": timeframe,
                    "limit": str(min(query.limit, 1000)),
                }
                resp = await client.get("https://data-api.binance.vision/api/v3/klines", params=params_vision)
                if resp.status_code == 200 and isinstance(resp.json(), list) and resp.json():
                    by_open_vision: dict[int, Candle] = {}
                    for item in resp.json():
                        if not isinstance(item, list) or len(item) < 11:
                            continue
                        open_time = (int(item[0]) // duration) * duration
                        close_time = open_time + duration - 1
                        o = float(item[1])
                        h = float(item[2])
                        l = float(item[3])
                        c = float(item[4])
                        v = float(item[5])
                        qv = float(item[7])
                        by_open_vision[open_time] = Candle(
                            open_time=open_time,
                            close_time=close_time,
                            open=o,
                            high=max(h, o, c),
                            low=min(l, o, c),
                            close=c,
                            volume=round(v, 4),
                            quote_volume=round(qv, 2),
                            trades=max(1, int(item[8])),
                            taker_buy_volume=round(float(item[9]), 4),
                            taker_buy_quote_volume=round(float(item[10]), 2),
                            is_closed=close_time < now_ms,
                        )
                    vision_candles = continuous_tail(list(by_open_vision.values()), duration)[-query.limit :]
                    if len(vision_candles) > len(candles):
                        candles = vision_candles
            except Exception as exc:
                logger.warning("Binance Vision fallback candles failed for %s %s: %s", symbol, timeframe, exc)

    if not candles:
        raise MarketDataError("upstream_unavailable", f"Unable to load public candles for {symbol} {timeframe}.", 502)

    quality = assess_quality(candles, query, now_ms)
    return CandleResponse(
        symbol=symbol,
        timeframe=timeframe,
        fetched_at=datetime.now(UTC),
        server_time=now_ms,
        candles=candles,
        quality=quality,
        next_start_time=None,
    )


