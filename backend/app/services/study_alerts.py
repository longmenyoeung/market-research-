"""Deduplicated dashboard records for fresh, confirmed study observations."""

import hashlib
import json
from datetime import UTC, datetime
from app.schemas.indicator_study import IndicatorStudyRequest
from app.storage.platform import PlatformRepository


async def record_study_alerts(store: PlatformRepository, request: IndicatorStudyRequest,
                             study: dict, tracker: dict, data_valid: bool) -> list[str]:
    if request.source != "live" or not request.dashboard_alerts or not data_valid:
        return []
    asof = study["as_of"]
    events = []
    for signal in study["signals"]:
        if signal["known_at"] == asof and study["current_signal"] == f"{signal['side']} candidate":
            events.append((signal["side"].lower(), f"Confirmed {signal['side']} study candidate; manual review required."))
    for event in tracker["events"]:
        if event["known_at"] == asof and event["kind"] in {"virtual_tp", "virtual_sl"}:
            events.append((event["kind"], f"Hypothetical {event['side']} {'target' if event['kind']=='virtual_tp' else 'stop'} touched in the closed-bar simulation. No real fill is claimed."))
    states = study.get("states", [])
    if len(states) >= 2 and states[-1]["ready"] and states[-2]["ready"]:
        prior, latest = states[-2:]
        if prior["trend_bias"]*latest["trend_bias"] < 0:
            events.append(("trend_reversal", "EMA slow/anchor trend reversed on a confirmed candle."))
        if latest["weak"] and not prior["weak"]:
            events.append(("weak_conditions", "Confirmed-candle ADX or relative volume fell below the study threshold."))
    if not events:
        return []
    signal_fingerprint = hashlib.sha256(json.dumps({"profile":request.profile,"rules":request.rules.model_dump(),
        "higher":sorted(study["timeframe_plan"]["higher_timeframes"])},sort_keys=True).encode()).hexdigest()[:20]
    tracker_fingerprint = hashlib.sha256(json.dumps({"signals":signal_fingerprint,
        "simulation":request.simulation.model_dump()},sort_keys=True).encode()).hexdigest()[:20]
    existing = {record["id"] for record in await store.records("dashboard_notifications",symbol=request.symbol,timeframe=request.timeframe,limit=1000)}
    emitted = []
    for kind, message in events:
        fingerprint = tracker_fingerprint if kind.startswith("virtual_") else signal_fingerprint
        key = f"study:{request.symbol}:{request.timeframe}:{fingerprint}:{asof}:{kind}"
        if key in existing:
            continue
        await store.put("dashboard_notifications", key, {"symbol":request.symbol,"timeframe":request.timeframe,
            "created_at":datetime.now(UTC).isoformat(),"event_time":asof,"message":message,
            "dashboard_only":True,"hypothetical_only":True,"signal_profile":request.profile},request.symbol,request.timeframe)
        emitted.append(key)
    return emitted
