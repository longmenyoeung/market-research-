import asyncio
import copy
import logging
import uuid
import hashlib
import pandas as pd
from datetime import UTC, datetime

from app.adapters.base import MarketDataError
from app.adapters.metrics import BLOCKING_CODES
from app.analysis.scoring import assess, summarize
from app.analysis.structure import analyze_structure
from app.core.config import Settings
from app.indicators.engine import calculate, finite, frame, series_payload, snapshot
from app.market_data.cache import AsyncTTLCache
from app.market_data.service import MarketDataService
from app.schemas.candles import CandleQuery, INTERVAL_MS
from app.schemas.platform import AnalysisRequest, Preferences, ScanFilter, Watchlist
from app.storage.platform import PlatformRepository

logger = logging.getLogger(__name__)
TIMEFRAMES = list(INTERVAL_MS)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class PlatformService:
    def __init__(self, market: MarketDataService, store: PlatformRepository, config: Settings) -> None:
        self.market, self.store, self.config = market, store, config
        self.preferences = Preferences()
        self.watchlist = Watchlist()
        self.metrics_cache = AsyncTTLCache[dict](10, 1, 1)
        self.derivatives_cache = AsyncTTLCache[dict](30, 50, 8)
        self.report_cache = AsyncTTLCache[dict](20, 100, 8)
        self.calculation_cache = AsyncTTLCache(300,config.calculation_cache_entries,config.max_pending_requests)
        self.scan_quote_cache = AsyncTTLCache(5,1,1)
        self.scan_inputs: dict[str,dict] = {}
        self.market.closed_candle_listeners.append(self._invalidate_reports)
        self.analysis_slots = asyncio.Semaphore(3)
        self.maintenance_task: asyncio.Task | None = None
        self.quote_task: asyncio.Task | None = None
        self.scan_task: asyncio.Task | None = None
        self.scan = {"status": "idle", "timeframe": "1h", "rows": [], "processed": 0, "total": 0, "error": None}
        self.previous_rows: dict[tuple[str,str], dict] = {}
        self.revision = 0

    async def initialize(self) -> None:
        settings = await self.store.records("scan_settings", limit=1)
        watches = await self.store.records("watchlists", limit=1)
        if settings: self.preferences = Preferences.model_validate(settings[0]["data"])
        if watches: self.watchlist = Watchlist.model_validate(watches[0]["data"])
        # Restore only actual archived provider discovery and scan rows. No saved
        # quote or assessment is allowed to become a current candidate.
        universes = await self.store.records("market_symbols", limit=1)
        if universes:
            universe = universes[0]["data"]
            contracts = universe.get("contracts", [])
            if isinstance(contracts, list) and contracts:
                snapshots = await self.store.records("market_snapshots", limit=1000)
                timeframe = snapshots[0]["data"].get("timeframe", "1h") if snapshots else "1h"
                latest = {}
                for saved in snapshots:
                    row = saved["data"]
                    if row.get("timeframe") == timeframe and row.get("symbol") not in latest:
                        latest[row["symbol"]] = row
                rows = []
                for contract in contracts:
                    symbol = contract.get("symbol")
                    if not symbol:
                        continue
                    row = copy.deepcopy(latest.get(symbol, {"symbol": symbol, "timeframe": timeframe}))
                    row.update(quality="stale", classification="NO TRADE", long_score=None, short_score=None)
                    row["warnings"] = ["Saved market snapshot only; live discovery and quotes have not been verified since restart."]
                    rows.append(row)
                if rows:
                    self.scan = {"status": "cached", "timeframe": timeframe, "rows": rows,
                                 "processed": len(latest), "total": len(rows), "error": None,
                                 "cached_rows": True,
                                 "message": f"Last saved market discovery: {universe.get('fetched_at', universes[0]['saved_at'])}. All saved rows are stale until refreshed."}

    async def save_preferences(self, preferences: Preferences) -> dict:
        self.preferences = preferences; self.revision += 1
        if self.quote_task and not self.quote_task.done():
            self.quote_task.cancel()
            await asyncio.gather(self.quote_task,return_exceptions=True)
        self.report_cache.invalidate(lambda key: True)
        self.calculation_cache.invalidate(lambda key: True)
        self.scan_quote_cache.invalidate(lambda key: True)
        self.scan_inputs.clear()
        await self.store.put("scan_settings", "default", preferences.model_dump())
        if self.maintenance_task and not self.maintenance_task.done():
            self.maintenance_task.cancel()
            await asyncio.gather(self.maintenance_task,return_exceptions=True)
        # Avoid mixing different parameter revisions in one progressive scan.
        if self.scan_task and not self.scan_task.done():
            self.scan_task.cancel(); await asyncio.gather(self.scan_task, return_exceptions=True)
        self.scan = {"status": "idle", "timeframe": self.scan["timeframe"], "rows": [], "processed": 0, "total": 0, "error": None}
        return preferences.model_dump()

    async def save_watchlist(self, watchlist: Watchlist) -> dict:
        self.watchlist = watchlist
        await self.store.put("watchlists", "default", watchlist.model_dump())
        return watchlist.model_dump()

    async def metrics(self) -> dict:
        return (await self.metrics_cache.get("bulk", self.market.provider.fetch_metrics)).value

    async def derivatives(self, symbol: str) -> dict:
        await self.market.require_symbol(symbol)
        return (await self.derivatives_cache.get(symbol, lambda: self.market.provider.fetch_derivatives(symbol))).value

    async def report(self, request: AnalysisRequest) -> dict:
        key = (self.revision, request.symbol, request.model_dump_json())
        result = (await self.report_cache.get(key, lambda: self._report(request))).value
        # Cache age alone cannot establish freshness: a quote may have been near
        # its deadline when the report was first generated.
        if int(datetime.now(UTC).timestamp()*1000) > result["valid_until_ms"]:
            reason = "Required source data expired; regenerate this analysis before use."
            result["assessment"].update(classification="NO TRADE", final_assessment=f"NO TRADE: {reason}")
            result["assessment"]["reasons_to_avoid"].append(reason)
            for scenario in result["assessment"]["scenarios"].values():
                scenario.update(entry=None, entry_region=None, stop=None, invalidation=None,
                                targets=[], gross_rr=None, net_rr=None, risk=None)
            result["structure"]["annotations"] = [a for a in result["structure"]["annotations"] if a["kind"] != "plan"]
        return result

    def _invalidate_reports(self, symbol: str, _timeframe: str) -> None:
        # A selected market's primary or context candle can affect its report.
        self.report_cache.invalidate(lambda key: key[1] == symbol)

    async def _calculations(self, symbol, timeframe, candles, config):
        data=frame(candles)
        fingerprint=hashlib.blake2b(pd.util.hash_pandas_object(data,index=False).values.tobytes(),digest_size=16).hexdigest()
        key=(symbol,timeframe,config.model_dump_json(),fingerprint)
        async def load():
            df,catalog=await asyncio.to_thread(calculate,data,config,timeframe)
            shape=await asyncio.to_thread(analyze_structure,df,timeframe,config.swing_window,momentum_period=config.momentum_period)
            return df,catalog,shape
        return (await self.calculation_cache.get(key,load)).value

    async def _report(self, request: AnalysisRequest, *, scanner: bool = False) -> dict:
        async with self.analysis_slots:
            preferences = self.preferences.model_copy(deep=True)
            c = preferences.indicators
            limit = min(1200, max(400, max(c.ema_periods+c.sma_periods+[c.trend_anchor, c.volatility_period+c.percentile_period,
                         c.ichimoku_span+c.ichimoku_base, c.macd_slow+c.macd_signal, c.adx_period*2,
                         c.momentum_period*2, c.momentum_period+c.stochastic_smooth, c.awesome_slow,
                         c.atr_period, c.band_period, c.channel_period, c.volume_period, c.regression_period,
                         *c.rsi_periods])+100))
            primary = await self.market.analysis_candles(CandleQuery(symbol=request.symbol, timeframe=request.timeframe, limit=limit))
            df,catalog,primary_shape = await self._calculations(request.symbol,request.timeframe,primary.candles,c)
            higher = request.higher_timeframes or [tf for tf in TIMEFRAMES if INTERVAL_MS[tf] > INTERVAL_MS[request.timeframe]][:2]
            if scanner: higher = higher[:1]
            higher = list(dict.fromkeys(tf for tf in higher if INTERVAL_MS[tf] > INTERVAL_MS[request.timeframe]))
            lower = request.lower_timeframe
            if lower is None and not scanner:
                lower = next((tf for tf in reversed(TIMEFRAMES) if INTERVAL_MS[tf] < INTERVAL_MS[request.timeframe]), None)
            contexts = []; limitations = list(primary.quality.warnings); daily = df if request.timeframe == "1d" else None
            roles = [(tf,"higher") for tf in higher]
            if lower and INTERVAL_MS[lower] < INTERVAL_MS[request.timeframe] and lower not in higher: roles.append((lower,"lower"))
            if not scanner and request.timeframe != "1d" and "1d" not in [tf for tf,_ in roles]: roles.append(("1d","reference"))
            for tf, role in roles:
                try:
                    page = await self.market.analysis_candles(CandleQuery(symbol=request.symbol, timeframe=tf, limit=limit))
                    other,_,shape = await self._calculations(request.symbol,tf,page.candles,c)
                    if tf == "1d": daily = other
                    usable = self._usable(page, other, preferences)
                    contexts.append({"timeframe": tf, "role": role, "usable": usable, "quality": page.quality.model_dump(),
                                     "valid_until_ms": int(other.time.iloc[-1])+2*INTERVAL_MS[tf]+5000 if not other.empty else 0,
                                     "summary": summarize(other, shape, preferences), "fetched_at": page.fetched_at.isoformat()})
                except MarketDataError as exc:
                    if exc.code in BLOCKING_CODES:
                        raise
                    contexts.append({"timeframe": tf, "role": role, "usable": False, "error": exc.code, "summary": {"trend": "unknown"}})
                    limitations.append(f"{tf} {role} context unavailable: {exc.code}")
            shape = primary_shape if scanner else await asyncio.to_thread(analyze_structure, df, request.timeframe, c.swing_window, daily,
                                            momentum_period=c.momentum_period)
            derivative_data = await self.derivatives(request.symbol) if request.include_derivatives else {"limitations": ["Optional derivatives detail not requested."]}
            bulk = await self.metrics(); metric = bulk["rows"].get(request.symbol, {})
            limitations.extend(bulk["limitations"])
            required_valid = self._usable(primary, df, preferences)
            # Earlier fetched data may age during multi-timeframe requests; recheck
            # clock coverage at the final classification time.
            now_ms = int(datetime.now(UTC).timestamp()*1000)
            for context in contexts:
                if context.get("usable") and now_ms > context["valid_until_ms"]:
                    context.update(usable=False, error="context_expired")
                    limitations.append(f"{context['timeframe']} context expired during analysis.")
            if df.empty or now_ms-(int(df.time.iloc[-1])+INTERVAL_MS[request.timeframe]) > INTERVAL_MS[request.timeframe]+5000:
                required_valid = False
            assessment = assess(df, shape, preferences, metric, contexts, limitations, required_valid, request.timeframe,
                                derivative_data)
            annotations = shape["annotations"]
            if not scanner and not df.empty:
                for side, scenario in assessment["scenarios"].items():
                    if scenario.get("entry"):
                        zone_start = int(df.time.iloc[max(0, len(df)-30)])
                        zone_end = int(df.time.iloc[-1])
                        annotations.append({"id": f"{side}-Entry-region", "kind": "plan",
                            "label": f"{side} Entry region", "timeframe": request.timeframe, "region": "entry",
                            "points": [{"time": zone_start, "price": scenario["entry_region"][0]},
                                       {"time": zone_end, "price": scenario["entry_region"][1]}],
                            "confirmed": False, "confirmed_at": now_ms,
                            "rule": "Hypothetical entry region: ask for LONG or bid for SHORT, plus/minus 0.1 ATR. Horizontal width shows 30 observed bars, not a timing forecast. No execution."})
                        for label, price in [("Entry", scenario["entry"]), ("Stop", scenario["stop"]), *[(f"Target {i+1}",t) for i,t in enumerate(scenario["targets"])]]:
                            annotations.append({"id": f"{side}-{label}", "kind": "plan", "label": f"{side} {label}", "timeframe": request.timeframe,
                                "points": [{"time": int(df.time.iloc[-1]), "price": price}], "confirmed": False,
                                "confirmed_at": now_ms, "rule": "Hypothetical quote-based entry; stop beyond structure/1.5 ATR; targets at 1R/2R/3R. No execution."})
                        for label, end_price in [("Risk", scenario["stop"]), ("Reward 2R", scenario["targets"][1])]:
                            annotations.append({"id": f"{side}-{label}-area", "kind": "plan", "label": f"{side} {label}",
                                "timeframe": request.timeframe, "region": "risk" if label == "Risk" else "reward",
                                "points": [{"time": int(df.time.iloc[max(0,len(df)-30)]), "price": scenario["entry"]},
                                           {"time": int(df.time.iloc[-1]), "price": end_price}],
                                "confirmed": False, "confirmed_at": now_ms,
                                "rule": "Visual entry-to-stop or entry-to-2R price area over the last 30 observed bars; horizontal width has no timing implication."})
            report_id = str(uuid.uuid4())
            deadlines = [(metric.get("book_time") or 0)+60000, (metric.get("ticker_time") or 0)+120000,
                         int(df.time.iloc[-1])+2*INTERVAL_MS[request.timeframe]+5000 if not df.empty else 0]
            if preferences.require_higher_alignment:
                deadlines += [context.get("valid_until_ms", 0) for context in contexts if context["role"] == "higher"]
            if scanner:
                offset=max(0,len(df)-30)
                compact_shape={k:shape[k] for k in ("trend","support","resistance")}
                compact_shape["events"]=[{**e,"index":e["index"]-offset} for e in shape["events"]]
                compact_shape["annotations"]=[a for a in shape["annotations"] if a["kind"]=="divergence"]
                self.scan_inputs[request.symbol]={"df":df.tail(30).reset_index(drop=True),"shape":compact_shape,
                    "contexts":copy.deepcopy(contexts),"usable":required_valid,"timeframe":request.timeframe,
                    "candle_valid_until_ms":int(df.time.iloc[-1])+2*INTERVAL_MS[request.timeframe]+5000 if not df.empty else 0}
            result = {"id": report_id, "generated_at": utc_now(), "valid_until_ms": min(deadlines),
                "symbol": request.symbol, "timeframe": request.timeframe,
                "market_overview": {"contract_type": "USDT-margined perpetual", **metric, "quality": primary.quality.model_dump(),
                                    "candles_fetched_at": primary.fetched_at.isoformat(), "metrics_fetched_at": bulk["fetched_at"], "regime": assessment["summary"].get("regime")},
                "indicators": {} if scanner else snapshot(df,catalog), "structure": shape, "multi_timeframe": contexts,
                "assessment": assessment, "derivatives": derivative_data,
                "volume_analysis": {"relative_volume": finite(df.relative_volume.iloc[-1]) if not df.empty else None,
                    "spike": bool(not df.empty and df.relative_volume.iloc[-1] >= 2),
                    "price_volume_divergence": bool(len(df)>20 and (df.close.iloc[-1]-df.close.iloc[-20])*(df.obv.iloc[-1]-df.obv.iloc[-20]) < 0),
                    "volume_profile": {"available": False, "reason": "OHLCV does not reveal true volume at price; trade-level data is not collected."}},
                "candles": [] if scanner else [c.model_dump(mode="json") for c in primary.candles],
                "series": {} if scanner else series_payload(df,catalog), "parameters": preferences.model_dump(),
                "data_limitations": limitations+shape["limitations"]+derivative_data.get("limitations", []),
                "manual_only": True}
            # Keep lightweight historical records; chart series are reproducible from candles.
            stored = {k:v for k,v in result.items() if k not in {"candles", "series"}}
            if not scanner:
                await self.store.put("analysis_reports", report_id, stored, request.symbol, request.timeframe)
                await self.store.put("indicator_snapshots", report_id, result["indicators"], request.symbol, request.timeframe)
                await self.store.put("chart_annotations", report_id, {"annotations": annotations}, request.symbol, request.timeframe)
            return result

    @staticmethod
    def _usable(page, df, preferences: Preferences) -> bool:
        if df.empty or page.quality.status not in {"usable"} or page.quality.missing_intervals:
            return False
        required = [f"ema_{preferences.indicators.trend_anchor}", "adx", "atr", "macd_hist", f"rsi_{preferences.indicators.momentum_period}"]
        return all(name in df and finite(df[name].iloc[-1]) is not None for name in required)

    async def start_scan(self, timeframe: str) -> dict:
        if self.maintenance_task and not self.maintenance_task.done():
            self.maintenance_task.cancel()
            await asyncio.gather(self.maintenance_task,return_exceptions=True)
        if self.quote_task and not self.quote_task.done():
            self.quote_task.cancel()
            await asyncio.gather(self.quote_task,return_exceptions=True)
        if self.scan_task and not self.scan_task.done():
            if self.scan["timeframe"] == timeframe:
                return {"status": self.scan["status"], "timeframe": self.scan["timeframe"], "message": "A scan is already in progress."}
            self.scan_task.cancel()
            await asyncio.gather(self.scan_task, return_exceptions=True)
        fallback = copy.deepcopy(self.scan) if self.scan["rows"] else None
        if fallback:
            fallback.update(status="cached", error=None, cached_rows=True,
                            message="Showing the previous market scan while public discovery is retried. All rows are stale.")
            for row in fallback["rows"]:
                row.update(quality="stale", classification="NO TRADE", long_score=None, short_score=None)
                row["warnings"] = ["Previous market snapshot only; live discovery and quotes are pending."]
        self.scan = {"status": "discovering", "timeframe": timeframe,
                     "rows": fallback["rows"] if fallback else [],
                     "processed": fallback["processed"] if fallback else 0,
                     "total": fallback["total"] if fallback else 0,
                     "error": None, "cached_rows": bool(fallback),
                     "message": fallback["message"] if fallback else None,
                     "started_at": utc_now()}
        self.scan_inputs.clear()
        self.scan_quote_cache.invalidate(lambda key: True)
        self.scan_task = asyncio.create_task(self._scan(timeframe, fallback))
        return {"status": "discovering", "timeframe": timeframe}

    def _apply_scan_report(self,row,report):
        assessment = report["assessment"]; summary = assessment["summary"]
        metric = report["market_overview"]
        row.update({k:v for k,v in metric.items() if k not in {"quality"}})
        row.update(summary, quality="usable" if metric["quality"]["status"] == "usable" and summary.get("trend") != "unknown" else "unavailable",
                   classification=assessment["classification"], long_score=assessment["long_score"], short_score=assessment["short_score"],
                   analyzed_at=report["generated_at"], valid_until_ms=report["valid_until_ms"],
                   warnings=assessment["reasons_to_avoid"])
        row["liquidity"] = "warning" if row.get("volume",0) < self.preferences.minimum_volume or row.get("spread_bps") is None or row["spread_bps"] > self.preferences.maximum_spread_bps else "normal"
        row["volatility_status"] = "high" if row.get("volatility") is not None and row["volatility"] >= self.preferences.high_volatility_atr_percent else "normal" if row.get("volatility") is not None else "unknown"
        row["candles_analyzed_at"]=report["generated_at"]

    async def _scan(self, timeframe: str, fallback: dict | None = None) -> None:
        try:
            universe = await self.market.discover_contracts()
            self.scan.update(status="loading metrics", cached_rows=False, message=None, total=universe.count,
                             rows=[{"symbol": c.symbol, "timeframe": timeframe,
                                    "quality": "pending", "classification": "NO TRADE", "long_score": None, "short_score": None,
                                    "liquidity": "unknown", "volatility_status": "unknown", "warnings": ["Analysis pending; no candidate classification."]}
                                   for c in universe.contracts])
            bulk = await self.metrics()
            self.scan.update(status="analyzing", limitations=bulk["limitations"])
            for row in self.scan["rows"]:
                row.update(bulk["rows"].get(row["symbol"],{}))
            await self.store.put("market_symbols", "universe", universe.model_dump(mode="json"))
            queue = asyncio.Queue()
            for row in self.scan["rows"]: queue.put_nowait(row)
            async def worker() -> None:
                while not queue.empty():
                    row = queue.get_nowait()
                    try:
                        request = AnalysisRequest(symbol=row["symbol"], timeframe=timeframe, include_derivatives=False)
                        report = await self._report(request, scanner=True)
                        self._apply_scan_report(row,report)
                        await self._notify_changes(row)
                        await self.store.put("market_snapshots", str(uuid.uuid4()), row, row["symbol"], timeframe)
                    except MarketDataError as exc:
                        if exc.code in BLOCKING_CODES:
                            raise
                        row.update(quality="unavailable", classification="NO TRADE", error=exc.code, warnings=[exc.message], analyzed_at=utc_now())
                        await self._notify_changes(row)
                    finally:
                        self.scan["processed"] += 1
                        queue.task_done()
            workers = [asyncio.create_task(worker()) for _ in range(self.preferences.scan_concurrency)]
            try:
                await asyncio.gather(*workers)
            finally:
                for task in workers: task.cancel()
                await asyncio.gather(*workers, return_exceptions=True)
            self.scan.update(status="complete", finished_at=utc_now())
        except asyncio.CancelledError:
            self.scan["status"] = "cancelled"
            raise
        except MarketDataError as exc:
            if fallback:
                self.scan = {**fallback, "error": exc.code,
                             "message": f"{exc.message} Showing stale saved market snapshots."}
            else:
                self.scan.update(status="error", error=exc.code, message=exc.message)
        except Exception:
            logger.exception("Scanner failed")
            if fallback:
                self.scan = {**fallback, "error": "scanner_failed",
                             "message": "Scanner could not refresh. Showing stale saved market snapshots."}
            else:
                self.scan.update(status="error", error="scanner_failed", message="Scanner could not finish; review backend logs.")

    async def _notify_changes(self, row: dict) -> None:
        key = (row["symbol"], row["timeframe"]); previous = self.previous_rows.get(key)
        changes = []
        if previous:
            if previous["classification"] != row["classification"]:
                changes.append(f"Classification: {previous['classification']} → {row['classification']}")
                if "candidate" in previous["classification"] and "candidate" not in row["classification"]: changes.append("Previous candidate invalidated or no longer meets its confirmation/data requirements.")
            if previous.get("liquidity") != "warning" and row.get("liquidity") == "warning": changes.append("Liquidity deteriorated.")
            if previous.get("volatility_status") != "high" and row.get("volatility_status") == "high": changes.append("Volatility threshold crossed.")
            if abs(previous.get("funding_rate") or 0) < self.preferences.funding_warning <= abs(row.get("funding_rate") or 0): changes.append("Funding threshold crossed.")
            if "retest" in row.get("setups",[]) and "retest" not in previous.get("setups",[]): changes.append("An observed retest confirmation is present; manual review required.")
        for message in changes:
            await self.store.put("dashboard_notifications", str(uuid.uuid4()), {"symbol": row["symbol"], "timeframe": row["timeframe"], "message": message, "created_at": utc_now(), "dashboard_only": True}, row["symbol"], row["timeframe"])
        self.previous_rows[key] = copy.deepcopy(row)

    async def _maintain_scan(self):
        scan=self.scan
        revision=self.revision
        try:
            universe=await self.market.discover_contracts()
            if scan is not self.scan or revision!=self.revision: return
            eligible={contract.symbol for contract in universe.contracts}
            scan["rows"]=[row for row in scan["rows"] if row["symbol"] in eligible]
            self.scan_inputs={symbol:state for symbol,state in self.scan_inputs.items() if symbol in eligible}
            present={row["symbol"] for row in scan["rows"]}
            for symbol in sorted(eligible-present):
                scan["rows"].append(dict(symbol=symbol,timeframe=scan["timeframe"],quality="pending",classification="NO TRADE",
                    long_score=None,short_score=None,liquidity="unknown",volatility_status="unknown",warnings=["Newly discovered contract; analysis pending."]))
            scan["total"]=len(eligible)
            now=int(datetime.now(UTC).timestamp()*1000)
            due=[]
            for row in scan["rows"]:
                state=self.scan_inputs.get(row["symbol"])
                if (state is None or now>state["candle_valid_until_ms"]-4000
                        or any(now>context.get("valid_until_ms",0)-4000
                               for context in state["contexts"] if context["role"]=="higher")):
                    due.append(row)
            scan["refreshing_count"]=len(due)
            queue=asyncio.Queue()
            for row in due: queue.put_nowait(row)
            async def worker():
                while not queue.empty():
                    row=queue.get_nowait()
                    try:
                        report=await self._report(AnalysisRequest(symbol=row["symbol"],timeframe=scan["timeframe"],include_derivatives=False),scanner=True)
                        if scan is not self.scan or revision!=self.revision: return
                        self._apply_scan_report(row,report)
                        await self._notify_changes(row)
                    except MarketDataError as exc:
                        if exc.code in BLOCKING_CODES: raise
                        row.update(quality="unavailable",classification="NO TRADE",warnings=[exc.message],error=exc.code)
                    finally:
                        scan["refreshing_count"]=max(0,scan["refreshing_count"]-1)
                        queue.task_done()
            workers=[asyncio.create_task(worker()) for _ in range(self.preferences.scan_concurrency)]
            try:
                await asyncio.gather(*workers)
            finally:
                for task in workers: task.cancel()
                await asyncio.gather(*workers,return_exceptions=True)
            scan["processed"]=len(scan["rows"])
            scan["last_maintenance_at"]=utc_now()
        except MarketDataError as exc:
            scan.update(status="error",error=exc.code,message=exc.message)
            for row in scan["rows"]:
                row.update(classification="NO TRADE",quality="unavailable",warnings=[exc.message])
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Scanner maintenance failed")
            scan.update(status="error",error="maintenance_failed",message="Scanner maintenance failed; inspect local logs.")

    async def _refresh_scan_quotes(self):
        scan=self.scan
        revision=self.revision
        try:
            bulk=await self.metrics()
        except MarketDataError as exc:
            for row in scan["rows"]:
                if row.get("quality") not in {"pending","unavailable"}:
                    row.update(classification="NO TRADE",quality="unavailable",warnings=[f"Current quote refresh failed: {exc.code}"])
            return
        now=int(datetime.now(UTC).timestamp()*1000)
        preferences=self.preferences.model_copy(deep=True)
        inputs=list(self.scan_inputs.items())
        def evaluate():
            results=[]
            for symbol,state in inputs:
                contexts=copy.deepcopy(state["contexts"])
                for context in contexts:
                    if now>context.get("valid_until_ms",0): context["usable"]=False
                valid=state["usable"] and now<=state["candle_valid_until_ms"]
                metric=bulk["rows"].get(symbol,{})
                assessment=assess(state["df"],state["shape"],preferences,metric,contexts,list(bulk["limitations"]),valid,state["timeframe"])
                deadlines=[state["candle_valid_until_ms"],(metric.get("book_time") or 0)+60000,(metric.get("ticker_time") or 0)+120000]
                if preferences.require_higher_alignment:
                    deadlines += [c.get("valid_until_ms",0) for c in contexts if c["role"]=="higher"]
                results.append((symbol,metric,assessment,min(deadlines),valid))
            return results
        results=await asyncio.to_thread(evaluate)
        if scan is not self.scan or revision!=self.revision: return
        by_symbol={row["symbol"]:row for row in scan["rows"]}
        for row in scan["rows"]:
            row.update(bulk["rows"].get(row["symbol"],{}))
        for symbol,metric,assessment,deadline,valid in results:
            row=by_symbol.get(symbol)
            if row is None: continue
            row.setdefault("candles_analyzed_at",row.get("analyzed_at"))
            row.update(assessment["summary"],classification=assessment["classification"],
                       long_score=assessment["long_score"],short_score=assessment["short_score"],
                       valid_until_ms=deadline,analyzed_at=utc_now(),quotes_updated_at=bulk["fetched_at"],
                       quality="usable" if valid and now<=deadline else "stale",warnings=assessment["reasons_to_avoid"])
            row["liquidity"]="warning" if metric.get("volume",0)<preferences.minimum_volume or metric.get("spread_bps") is None or metric["spread_bps"]>preferences.maximum_spread_bps else "normal"
            await self._notify_changes(row)

    async def scanner(self, filters: ScanFilter) -> dict:
        if self.scan_inputs and (self.quote_task is None or self.quote_task.done()):
            async def refresh_quotes() -> None:
                try:
                    await self.scan_quote_cache.get((self.revision,id(self.scan)),self._refresh_scan_quotes)
                except Exception:
                    logger.exception("Background scanner quote refresh failed")
            self.quote_task = asyncio.create_task(refresh_quotes())
        if self.scan_inputs and self.scan["status"]=="complete" and (self.maintenance_task is None or self.maintenance_task.done()):
            self.maintenance_task=asyncio.create_task(self._maintain_scan())
        state = copy.deepcopy(self.scan); rows = state.pop("rows")
        now = datetime.now(UTC)
        for row in rows:
            if row.get("analyzed_at"):
                age = (now-datetime.fromisoformat(row["analyzed_at"])).total_seconds()
                row["age_seconds"] = max(0,age)
                if (age > 120 or now.timestamp()*1000 > row.get("valid_until_ms", float("inf"))) and row["quality"] not in {"unavailable", "pending"}:
                    row.update(quality="stale", classification="NO TRADE")
                    row["warnings"].append("Required source data expired or analysis is older than 120 seconds; refresh before use.")
                    previous = self.previous_rows.get((row["symbol"], row["timeframe"]))
                    if previous and not previous.get("stale_notified"):
                        previous["stale_notified"] = True
                        await self.store.put("dashboard_notifications", str(uuid.uuid4()), {"symbol": row["symbol"], "timeframe": row["timeframe"], "message": "Analysis data became stale.", "created_at": utc_now(), "dashboard_only": True}, row["symbol"], row["timeframe"])
        overview = {"eligible": state["total"], "usable": sum(r["quality"] == "usable" for r in rows),
                    "liquidity_warnings": sum(r.get("liquidity") == "warning" for r in rows),
                    "long_candidates": sum(r["classification"] == "LONG candidate" for r in rows),
                    "short_candidates": sum(r["classification"] == "SHORT candidate" for r in rows),
                    "btc": next((r for r in rows if r["symbol"] == "BTCUSDT"), None),
                    "mean_atr_percent": sum(r["volatility"] for r in rows if r.get("volatility") is not None)/max(1,sum(r.get("volatility") is not None for r in rows)) if any(r.get("volatility") is not None for r in rows) else None}
        def passes(r: dict) -> bool:
            def minimum(key, bound): return bound is None or bound == 0 or r.get(key) is not None and r[key] >= bound
            def maximum(key, bound): return bound is None or r.get(key) is not None and r[key] <= bound
            return (minimum("volume",filters.minimum_volume) and maximum("spread_bps",filters.maximum_spread)
                and minimum("volatility",filters.minimum_volatility) and maximum("volatility",filters.maximum_volatility)
                and minimum("long_score",filters.minimum_long) and minimum("short_score",filters.minimum_short)
                and minimum("rsi",filters.rsi_min) and maximum("rsi",filters.rsi_max) and minimum("adx",filters.adx_min)
                and (filters.trend == "all" or r.get("trend") == filters.trend)
                and (filters.setup == "all" or filters.setup in r.get("setups",[]))
                and (filters.quality == "all" or r["quality"] == filters.quality)
                and (filters.macd == "all" or r.get("macd") == filters.macd)
                and (not filters.watchlist_only or r["symbol"] in self.watchlist.symbols))
        selected = [r for r in rows if passes(r)]
        known = [r for r in selected if r.get(filters.sort) is not None]; missing = [r for r in selected if r.get(filters.sort) is None]
        known.sort(key=lambda r:r[filters.sort], reverse=filters.descending)
        return {**state, "rows": known+missing, "displayed": len(selected), "overview": overview,
                "notice": "All eligible contracts appear by default. Experimental scores are not probabilities. Older scan rows are downgraded to NO TRADE."}

    async def aclose(self) -> None:
        self.market.closed_candle_listeners.remove(self._invalidate_reports)
        if self.quote_task:
            self.quote_task.cancel()
            await asyncio.gather(self.quote_task,return_exceptions=True)
        if self.maintenance_task:
            self.maintenance_task.cancel()
            await asyncio.gather(self.maintenance_task,return_exceptions=True)
        if self.scan_task:
            self.scan_task.cancel(); await asyncio.gather(self.scan_task, return_exceptions=True)
        await self.report_cache.aclose(); await self.derivatives_cache.aclose(); await self.metrics_cache.aclose()
        await self.calculation_cache.aclose(); await self.scan_quote_cache.aclose()
        self.scan_inputs.clear()
