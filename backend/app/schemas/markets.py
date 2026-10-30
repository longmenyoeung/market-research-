from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ELIGIBILITY_NOTICE = (
    "Eligible means matching contract metadata only. Cambodia futures eligibility "
    "has not been verified. Public-data access does not establish legal or account eligibility."
)


class Contract(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    base_asset: str
    quote_asset: Literal["USDT"] = "USDT"
    margin_asset: Literal["USDT"] = "USDT"
    contract_type: Literal["PERPETUAL"] = "PERPETUAL"
    status: Literal["TRADING"] = "TRADING"
    constraints: dict[str, str] = Field(default_factory=dict)


class DiscoveryResponse(BaseModel):
    provider: Literal["binance_usdm"] = "binance_usdm"
    fetched_at: datetime
    timestamp_basis: Literal["local_utc_receive_time"] = "local_utc_receive_time"
    count: int = Field(ge=0)
    contracts: list[Contract]
    eligibility_notice: str = ELIGIBILITY_NOTICE
    cache_hit: bool = False
    cache_age_seconds: float = 0


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str = "1.0.0"
    mode: Literal["analysis_only"] = "analysis_only"
    exchange_connectivity: Literal["not_checked"] = "not_checked"
    trading_enabled: Literal[False] = False
    execution_endpoints: Literal["none"] = "none"
    application_key_required: bool = False
    timestamp: datetime


class ErrorDetail(BaseModel):
    code: str
    message: str
    retry_after_seconds: int | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
