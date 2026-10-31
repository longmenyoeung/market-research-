import asyncio
import uuid
import numpy as np
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.adapters.base import MarketDataError
from app.analysis.indicator_study import build_signals
from app.analysis.signal_profiles import attach_profile_context
from app.analysis.scoring import summarize
from app.indicators.engine import series_payload, snapshot, frame
from app.backtesting.engine import SimulationConfig, simulate
from app.schemas.candles import Candle, CandleQuery, INTERVAL_MS
from app.schemas.indicator_study import IndicatorStudyRequest, higher_context
from app.schemas.platform import AnalysisRequest, IndicatorSettings
from app.services.study_alerts import record_study_alerts

if TYPE_CHECKING:
    from app.services.platform import PlatformService


async def create_study(platform: "PlatformService", request: IndicatorStudyRequest) -> dict:
    now = datetime.now(UTC)
    now_ms = int(now.timestamp()*1000)
    preferences = platform.preferences.model_copy(deep=True)
    higher_timeframes = request.higher_timeframes or higher_context(request.timeframe)
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
        contexts = []
        context_frames = {}
        if request.source == "archive" or request.profile == "men_crypto":
            for timeframe in higher_timeframes:
                context = {"timeframe": timeframe, "role": "higher", "usable": False,
                           "historical_available": False, "summary": {"trend": "unknown"}}
                try:
                    query = CandleQuery(symbol=request.symbol, timeframe=timeframe, limit=1200 if request.source=="archive" else len(base["candles"]))
                    saved = await platform.market.archive(query) if request.source=="archive" else await platform.market.analysis_candles(query)
                    # Higher bars must have closed by the selected window's as-of
                    # time. Later archived candles cannot confirm an earlier view.
                    context_candles = [c for c in saved.candles if c.is_closed and c.close_time <= candles[-1].close_time]
                    times = [c.open_time for c in context_candles]
                    context_minimum = config.trend_anchor if request.profile=="men_crypto" else minimum
                    if len(times) < context_minimum or not np.all(np.diff(times) == INTERVAL_MS[timeframe]):
                        context["error"] = "Insufficient continuous confirmed candles at this study time."
                    else:
                        other, _, other_shape = await platform._calculations(request.symbol, timeframe, context_candles, config)
                        context_frames[timeframe] = frame(context_candles)
                        context.update(historical_available=request.source=="archive",
                                       usable=request.source=="live" and saved.quality.status=="usable", as_of=context_candles[-1].close_time,
                                       summary=summarize(other, other_shape, preferences))
                        if request.profile == "men_crypto":
                            last = other.iloc[-1]; slow = last[f"ema_{config.trend_slow}"]; anchor = last[f"ema_{config.trend_anchor}"]
                            context["summary"]["trend"] = "bullish" if last.close>slow>anchor else "bearish" if last.close<slow<anchor else "neutral"
                except MarketDataError as exc:
                    context["error"] = exc.message
                contexts.append(context)
        if request.profile == "men_crypto":
            df = await asyncio.to_thread(attach_profile_context, df, request.timeframe, config, context_frames, higher_timeframes)
        study = await asyncio.to_thread(build_signals, df, request.timeframe, config, request.rules, request.profile)
        markers = {signal["time"]:1 if signal["side"]=="BUY" else -1 for signal in study["signals"]}
        prepared = df.copy(); prepared["signal"] = [markers.get(int(timestamp),0) for timestamp in df.time]
        simulation = SimulationConfig(**request.simulation.model_dump(),symbol=request.symbol,timeframe=request.timeframe,
                                      strategy="indicator",signal_profile=request.profile,study_rules=request.rules,
                                      analysis_settings=preferences,higher_timeframes=higher_timeframes)
        tracker = await asyncio.to_thread(simulate, prepared, simulation, 0, len(prepared), close_at_end=False)
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
    if study["unavailable"]:
        usable = False
    if request.profile == "men_crypto" and not (bool(df.higher_ready.iloc[-1]) and bool(df.vwap_complete_session.iloc[-1])):
        usable = False
    classification = base["assessment"]["classification"] if usable else "NO TRADE"
    profile_blocks = False
    if classification in {"LONG candidate","SHORT candidate"}:
        side = "BUY" if classification=="LONG candidate" else "SELL"
        if not study["evaluation"].get(side) or not all(study["evaluation"][side].values()):
            classification = "WAIT"; profile_blocks = True
    if request.source == "live":
        structure = base["structure"]
        if not usable or profile_blocks:
            base["assessment"]["classification"] = classification
            for scenario in base["assessment"]["scenarios"].values():
                scenario.update(entry=None, entry_region=None, stop=None, invalidation=None,
                                targets=[], gross_rr=None, net_rr=None, risk=None)
            structure["annotations"] = [annotation for annotation in structure["annotations"] if annotation["kind"] != "plan"]
    study.update(
        source=request.source, historical=request.source == "archive",
        timeframe_plan={"primary": request.timeframe, "higher_timeframes": higher_timeframes,
                        "historical_markers_include_higher_context": request.profile=="men_crypto",
                        "live_alignment_required": base["parameters"]["require_higher_alignment"]},
        as_of=candles[-1].close_time,
        current_signal={"LONG candidate": "BUY candidate", "SHORT candidate": "SELL candidate"}.get(classification, classification),
        current_reason=("WAIT: the selected indicator profile filters are not all confirmed." if profile_blocks else base["assessment"].get("final_assessment", "Review all live report conditions before a manual decision.") if usable else
                        "Saved candles are historical. They cannot establish a current BUY or SELL opportunity." if request.source == "archive" else
                        "Required current data is stale, incomplete or invalid; no current direction is available."),
    )
    if request.profile == "men_crypto":
        base["multi_timeframe"] = contexts
    async with platform.study_alert_lock:
        study["notifications_emitted"] = await record_study_alerts(platform.store, request, study, tracker, usable)
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
        "paper_tracker": {**tracker,"settings":request.simulation.model_dump(),"assumptions":[
            "Window-local hypothetical replay starts flat and fills only at a later observed open with modeled costs.",
            "One virtual position at a time. This is not a real account position or an out-of-sample strategy validation.",
            "Break-even/trailing changes become active on the following bar; entry-price stops can still lose costs.",
            "Scenario funding is explicit; zero excludes funding and does not assert historical funding was zero."]},
    }
