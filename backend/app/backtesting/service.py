import asyncio
import uuid
import threading
from datetime import UTC, datetime

from app.adapters.base import MarketDataError
from app.backtesting.engine import BacktestRequest, run_validation
from app.indicators.engine import frame
from app.schemas.candles import CandleQuery, INTERVAL_MS
from app.market_data.service import MarketDataService
from app.storage.platform import PlatformRepository
from app.schemas.derivatives import FundingQuery
from app.backtesting.evidence import context_timeframes


class BacktestService:
    def __init__(self, market: MarketDataService, store: PlatformRepository) -> None:
        self.market, self.store = market, store
        self.tasks: dict[str, asyncio.Task] = {}
        self.jobs: dict[str, dict] = {}
        self.cancel_events: dict[str, threading.Event] = {}

    async def submit(self, request: BacktestRequest) -> dict:
        if any(not task.done() for task in self.tasks.values()):
            raise MarketDataError("busy", "One historical validation run is already active.", 503, 5)
        if request.end_time >= datetime.now(UTC).timestamp()*1000:
            raise MarketDataError("invalid_range", "Backtest end_time must be in the past.", 422)
        key = str(uuid.uuid4()); job = {"id": key, "status": "loading", "bars_loaded": 0, "parameters": request.model_dump()}
        self.tasks = {k:v for k,v in self.tasks.items() if not v.done()}
        self.jobs = {key: job}
        self.cancel_events = {key: threading.Event()}
        await self.store.put("backtest_runs", key, job, request.symbol, request.timeframe)
        self.tasks[key] = asyncio.create_task(self._run(key,request))
        return job.copy()

    async def _run(self, key: str, request: BacktestRequest) -> None:
        job=self.jobs[key]
        try:
            candles = await self._load_candles(request.symbol,request.timeframe,request.start_time,request.end_time,job)
            if not candles: raise ValueError("No historical closed candles available.")
            interval=INTERVAL_MS[request.timeframe]
            aligned_start=((request.start_time+interval-1)//interval)*interval
            expected_end=((request.end_time+1)//interval)*interval-1
            if candles[0].open_time != aligned_start or candles[-1].close_time != expected_end:
                raise ValueError("Provider history does not cover the requested range; shorten the range explicitly.")
            job["status"]="calculating"
            contexts = {}
            for timeframe, role in context_timeframes(request):
                duration = INTERVAL_MS[timeframe]
                # Context starts earlier so its own indicator warm-up cannot use
                # candles from the future of the primary validation interval.
                history_start=max(0,(request.start_time//duration-700)*duration)
                job["context_loading"] = {"timeframe":timeframe,"role":role}
                context_candles=await self._load_candles(request.symbol,timeframe,history_start,request.end_time)
                contexts[timeframe]=frame(context_candles)
            funding = None
            if request.funding_mode == "historical":
                job["status"]="loading funding"
                funding=await self._load_funding(request.symbol,request.start_time,request.end_time)
                job["funding_events_loaded"]=len(funding)
            job["status"]="calculating"
            result=await asyncio.to_thread(run_validation,frame(candles),request,contexts,funding,self.cancel_events[key])
            result["data_note"]="Current eligible contracts only: survivorship bias is possible. Historical quotes are modeled assumptions; funding source is explicitly disclosed."
            result["context_sources"]={tf:{"bars":len(data),"first_time":int(data.time.iloc[0]) if len(data) else None,"last_time":int(data.time.iloc[-1]) if len(data) else None} for tf,data in contexts.items()}
            result["funding_source"]={"mode":request.funding_mode,"event_count":len(funding) if funding is not None else None,
                "start_time":request.start_time,"end_time":request.end_time,"provider":"binance_usdm" if funding is not None else None}
            await self.store.put("backtest_results",key,result,request.symbol,request.timeframe)
            job.update(status="complete",result=result)
        except (MarketDataError,ValueError) as exc:
            job.update(status="error",error=exc.code if isinstance(exc,MarketDataError) else "invalid_history",message=str(exc))
        except asyncio.CancelledError:
            job.update(status="cancelled"); raise
        except Exception:
            job.update(status="error",error="backtest_failed",message="Historical validation failed; inspect server logs.")
            import logging
            logging.getLogger(__name__).exception("Backtest failed")
        finally:
            await self.store.put("backtest_runs",key,{k:v for k,v in job.items() if k!="result"},request.symbol,request.timeframe)

    async def _load_candles(self,symbol,timeframe,start_time,end_time,job=None):
        duration=INTERVAL_MS[timeframe]
        if (end_time-start_time)//duration>20000:
            raise ValueError(f"{timeframe} context exceeds 20,000 bars; shorten the range or choose a closer context timeframe.")
        candles=[]; cursor=start_time
        while cursor<=end_time:
            page=await self.market.get_candles(CandleQuery(symbol=symbol,timeframe=timeframe,start_time=cursor,end_time=end_time,limit=1500))
            batch=[c for c in page.candles if c.is_closed and c.close_time<=end_time]
            if not batch: break
            if batch[0].open_time<cursor: raise ValueError("Historical page moved backwards.")
            candles.extend(batch)
            if len(candles)>20000: raise ValueError("Maximum 20,000 bars exceeded.")
            if job is not None: job["bars_loaded"]=len(candles)
            next_time=batch[-1].open_time+duration
            if next_time<=cursor: raise ValueError("Non-progressing provider pagination.")
            cursor=next_time
            if len(page.candles)<1500: break
        return candles

    async def _load_funding(self,symbol,start_time,end_time):
        """Page inclusively so a Regular/Special event sharing the boundary is not lost."""
        funding=[]; seen=set(); cursor=start_time
        while cursor<=end_time:
            points=await self.market.provider.fetch_funding_history(FundingQuery(
                symbol=symbol,start_time=cursor,end_time=end_time,limit=1000))
            if not points: break
            if points[0]["time"]<cursor:
                raise ValueError("Historical funding page moved backwards.")
            previous_count=len(funding)
            for point in points:
                identity=(point["time"],point["rate_type"])
                if identity not in seen:
                    seen.add(identity);funding.append(point)
            if len(funding)>20000:
                raise ValueError("Maximum historical funding events exceeded.")
            if len(points)<1000: break
            next_cursor=points[-1]["time"]
            if next_cursor<cursor or (next_cursor==cursor and len(funding)==previous_count):
                raise ValueError("Non-progressing historical funding pagination.")
            # The endpoint range is inclusive. Repeating the boundary and de-duping
            # identities preserves a second rateType with the same fundingTime.
            cursor=next_cursor
        return funding

    async def cancel(self, key: str) -> dict:
        if key not in self.tasks or self.tasks[key].done():
            return await self.get(key)
        self.cancel_events[key].set()
        self.tasks[key].cancel()
        await asyncio.gather(self.tasks[key],return_exceptions=True)
        if self.jobs[key]["status"] != "cancelled":
            self.jobs[key]["status"] = "cancelled"
            parameters = self.jobs[key]["parameters"]
            await self.store.put("backtest_runs",key,{k:v for k,v in self.jobs[key].items() if k!="result"},
                                 parameters["symbol"],parameters["timeframe"])
        return await self.get(key)

    async def get(self, key: str) -> dict:
        if key in self.jobs: return self.jobs[key]
        runs=await self.store.records("backtest_runs",limit=1000)
        job=next((r["data"] for r in runs if r["id"]==key),None)
        if job is None: raise MarketDataError("not_found","Historical run not found.",404)
        if job["status"]=="complete":
            results=await self.store.records("backtest_results",limit=1000)
            job["result"]=next((r["data"] for r in results if r["id"]==key),None)
        elif job["status"] in {"loading","loading funding","calculating"}: job.update(status="interrupted",message="Server restarted during this run; submit a new run.")
        return job

    async def aclose(self) -> None:
        tasks=list(self.tasks.values())
        for event in self.cancel_events.values(): event.set()
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
