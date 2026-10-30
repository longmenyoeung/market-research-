import asyncio
import uuid
import numpy as np
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.adapters.base import MarketDataError
from app.analysis.indicator_study import build_signals
from app.analysis.scoring import summarize
from app.indicators.engine import series_payload, snapshot
from app.schemas.candles import Candle, CandleQuery, INTERVAL_MS
from app.schemas.indicator_study import IndicatorStudyRequest, higher_context
from app.schemas.platform import AnalysisRequest, IndicatorSettings

if TYPE_CHECKING:
    from app.services.platform import PlatformService


async def create_study(platform: "PlatformService", request: IndicatorStudyRequest) -> dict:
    now = datetime.now(UTC)
    now_ms = int(now.timestamp()*1000)
    preferences = platform.preferences.model_copy(deep=True)
    higher_timeframes = higher_context(request.timeframe)
    base = None
    if request.source == "live":
        # Current BUY/SELL eligibility always comes from the complete engine,
        # including fresh quotes, higher context, costs and data-quality checks.
        base = await platform.report(AnalysisRequest(symbol=request.symbol, timeframe=request.timeframe,
                                                    higher_timeframes=higher_timeframes))
        candles = [Candle.model_validate(value) for value in base["candles"]]
        quality = base["market_overview"]["quality"]
        config = IndicatorSettings.model_validate(base["parameters"]["indicators"])
    else:
        page = await platform.market.archive(CandleQuery(symbol=request.symbol, timeframe=request.timeframe, limit=1200))
        candles, quality, config = page.candles, page.quality.model_dump(), preferences.indicators
    candles = [c for c in candles if c.is_closed and c.close_time < now_ms]
    if quality.get("missing_intervals", 0):
        raise MarketDataError("invalid_study_data", "Saved candles contain gaps. A continuous candle window is required.", 422)
    minimum = max(config.trend_anchor, config.macd_slow+config.macd_signal,
                  2*config.adx_period, config.volume_period, config.momentum_period+1)+1
    if len(candles) < minimum:
        raise MarketDataError("insufficient_study_data", f"This indicator requires at least {minimum} confirmed candles; {len(candles)} are available.", 422)
    async with platform.analysis_slots:
        df, catalog, structure = await platform._calculations(request.symbol, request.timeframe, candles, config)
        study = await asyncio.to_thread(build_signals, df, request.timeframe, config, request.rules)
        contexts = []
        if request.source == "archive":
            for timeframe in higher_timeframes:
                context = {"timeframe": timeframe, "role": "higher", "usable": False,
                           "historical_available": False, "summary": {"trend": "unknown"}}
                try:
                    saved = await platform.market.archive(CandleQuery(symbol=request.symbol, timeframe=timeframe, limit=1200))
                    # Higher bars must have closed by the selected window's as-of
                    # time. Later archived candles cannot confirm an earlier view.
                    context_candles = [c for c in saved.candles if c.is_closed and c.close_time <= candles[-1].close_time]
                    times = [c.open_time for c in context_candles]
                    if len(times) < minimum or not np.all(np.diff(times) == INTERVAL_MS[timeframe]):
                        context["error"] = "Insufficient continuous confirmed candles at this study time."
                    else:
                        other, _, other_shape = await platform._calculations(request.symbol, timeframe, context_candles, config)
                        context.update(historical_available=True, as_of=context_candles[-1].close_time,
                                       summary=summarize(other, other_shape, preferences))
                except MarketDataError as exc:
                    context["error"] = exc.message
                contexts.append(context)
    if base is None:
        base = {
            "symbol": request.symbol, "timeframe": request.timeframe,
            "generated_at": now.isoformat(), "valid_until_ms": 0,
            "market_overview": {"quality": {**quality, "status": "historical"}},
            "parameters": preferences.model_dump(), "manual_only": True,
            "assessment": {"classification": "NO TRADE", "long_score": None, "short_score": None, "scenarios": {}},
            "multi_timeframe": contexts,
        }
    current_time = int(datetime.now(UTC).timestamp()*1000)
    usable = request.source == "live" and current_time <= base["valid_until_ms"] and base["market_overview"]["quality"]["status"] == "usable"
    classification = base["assessment"]["classification"] if usable else "NO TRADE"
    if request.source == "live":
        structure = base["structure"]
        if not usable:
            base["assessment"]["classification"] = "NO TRADE"
            for scenario in base["assessment"]["scenarios"].values():
                scenario.update(entry=None, entry_region=None, stop=None, invalidation=None,
                                targets=[], gross_rr=None, net_rr=None, risk=None)
            structure["annotations"] = [annotation for annotation in structure["annotations"] if annotation["kind"] != "plan"]
    study.update(
        source=request.source, historical=request.source == "archive",
        timeframe_plan={"primary": request.timeframe, "higher_timeframes": higher_timeframes,
                        "historical_markers_include_higher_context": False,
                        "live_alignment_required": base["parameters"]["require_higher_alignment"]},
        as_of=candles[-1].close_time,
        current_signal={"LONG candidate": "BUY candidate", "SHORT candidate": "SELL candidate"}.get(classification, classification),
        current_reason=(base["assessment"].get("final_assessment", "Review all live report conditions before a manual decision.") if usable else
                        "Saved candles are historical. They cannot establish a current BUY or SELL opportunity." if request.source == "archive" else
                        "Required current data is stale, incomplete or invalid; no current direction is available."),
    )
    for signal in study["signals"]:
        structure["annotations"].append({
            "id": signal["id"], "kind": "study_signal", "label": f"Past {signal['side']}",
            "timeframe": request.timeframe, "confirmed": True, "confirmed_at": signal["known_at"],
            "points": [{"time": signal["time"], "price": signal["reference"]}], "rule": signal["rule"],
        })
    return {
        **base, "id": f"study-{uuid.uuid4()}", "indicator_study": study,
        "structure": structure, "indicators": snapshot(df, catalog), "series": series_payload(df, catalog),
        "candles": [c.model_dump(mode="json") for c in candles],
    }
