from typing import Annotated
from fastapi import APIRouter, Depends, Query, Request, HTTPException
from pydantic import ValidationError
from app.adapters.base import MarketDataError
from app.analysis.risk import TradePlan, plan_trade
from app.api.dependencies import require_mutation_access
from app.backtesting.engine import BacktestRequest
from app.schemas.platform import AnalysisRequest, Preferences, ScanRequest, ScanFilter, Watchlist
from app.schemas.candles import Timeframe
from app.services.platform import PlatformService

router=APIRouter(prefix="/api/v1",tags=["analysis platform"])

def service(request: Request) -> PlatformService:
    return request.app.state.platform

@router.get("/settings")
async def settings(platform: Annotated[PlatformService,Depends(service)]) -> dict:
    return platform.preferences.model_dump()

@router.put("/settings")
async def update_settings(body: Preferences, platform: Annotated[PlatformService,Depends(service)],
                          _: Annotated[None,Depends(require_mutation_access)]) -> dict:
    return await platform.save_preferences(body)

@router.get("/watchlist")
async def watchlist(platform: Annotated[PlatformService,Depends(service)]) -> dict:
    return platform.watchlist.model_dump()

@router.put("/watchlist")
async def update_watchlist(body: Watchlist, platform: Annotated[PlatformService,Depends(service)],
                           _: Annotated[None,Depends(require_mutation_access)]) -> dict:
    return await platform.save_watchlist(body)

@router.post("/scanner/refresh",status_code=202)
async def scan(body: ScanRequest, platform: Annotated[PlatformService,Depends(service)],
               _: Annotated[None,Depends(require_mutation_access)]) -> dict:
    return await platform.start_scan(body.timeframe)

@router.get("/scanner")
async def scanner(filters: Annotated[ScanFilter,Query()], platform: Annotated[PlatformService,Depends(service)]) -> dict:
    return await platform.scanner(filters)

@router.post("/analysis")
async def analysis(body: AnalysisRequest, platform: Annotated[PlatformService,Depends(service)],
                   _: Annotated[None,Depends(require_mutation_access)]) -> dict:
    return await platform.report(body)

@router.get("/history/reports")
async def history(platform: Annotated[PlatformService,Depends(service)], symbol: str | None=None,
                  timeframe: Timeframe | None=None, limit: int=Query(50,ge=1,le=200)) -> list[dict]:
    return await platform.store.records("analysis_reports",symbol=symbol,timeframe=timeframe,limit=limit)

@router.get("/notifications")
async def notifications(platform: Annotated[PlatformService,Depends(service)]) -> list[dict]:
    return await platform.store.records("dashboard_notifications",limit=100)

@router.post("/planner")
async def planner(body: TradePlan, platform: Annotated[PlatformService,Depends(service)],
                  _: Annotated[None,Depends(require_mutation_access)]) -> dict:
    source="user-supplied constraints only"
    if body.symbol:
        universe=await platform.market.discover_contracts()
        contract=next((c for c in universe.contracts if c.symbol==body.symbol),None)
        if contract is None: raise MarketDataError("unknown_contract","Select a discovered contract.",404)
        try:
            body=TradePlan.model_validate({**body.model_dump(), **contract.constraints})
        except ValidationError as exc:
            raise HTTPException(422,detail=str(exc)) from exc
        source="public contract metadata; absent fields remain user-supplied or unavailable"
    return {**plan_trade(body),"inputs":body.model_dump(mode="json"),"constraint_source":source}

@router.post("/backtests",status_code=202)
async def backtest(body: BacktestRequest, request: Request,
                   _: Annotated[None,Depends(require_mutation_access)]) -> dict:
    return await request.app.state.backtests.submit(body)

@router.get("/backtests/{run_id}")
async def backtest_status(run_id: str, request: Request) -> dict:
    return await request.app.state.backtests.get(run_id)

@router.post("/backtests/{run_id}/cancel")
async def cancel_backtest(run_id: str, request: Request,
                          _: Annotated[None,Depends(require_mutation_access)]) -> dict:
    """Cancel only a local historical computation, never an exchange operation."""
    return await request.app.state.backtests.cancel(run_id)

@router.get("/backtests")
async def backtest_history(platform: Annotated[PlatformService,Depends(service)]) -> list[dict]:
    return await platform.store.records("backtest_runs",limit=50)
