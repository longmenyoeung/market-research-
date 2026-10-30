from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Environment-backed settings for a permanently read-only application."""

    model_config = SettingsConfigDict(
        env_prefix="MCA_", env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8", extra="forbid", frozen=True,
    )

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    request_timeout_seconds: float = Field(default=10, ge=0.1, le=60)
    max_retries: int = Field(default=2, ge=0, le=3)
    retry_base_seconds: float = Field(default=0.5, ge=0, le=5)
    min_request_interval_seconds: float = Field(default=0.1, ge=0.1, le=60)
    request_weight_per_minute: int = Field(default=1200, ge=100, le=2400)
    server_clock_ttl_seconds: float = Field(default=30, ge=1, le=60)
    calculation_cache_entries: int = Field(default=1024, ge=1, le=4096)
    max_request_body_bytes: int = Field(default=524288, ge=4096, le=2097152)
    allowed_hosts: list[str] = ["127.0.0.1","localhost","[::1]"]
    rate_limit_cooldown_seconds: int = Field(default=60, ge=1, le=86400)
    ban_cooldown_seconds: int = Field(default=86400, ge=60, le=259200)
    retry_after_max_seconds: int = Field(default=86400, ge=60, le=604800)
    discovery_ttl_seconds: float = Field(default=60, ge=1, le=3600)
    candle_ttl_seconds: float = Field(default=5, ge=0.1, le=60)
    cache_max_entries: int = Field(default=256, ge=1, le=4096)
    max_pending_requests: int = Field(default=32, ge=1, le=256)
    # Compatibility with existing .env files; True is always rejected.
    trading_enabled: Literal[False] = False
    # Optional API key for mutation routes used by non-browser clients. When it
    # is unset, mutations are limited to loopback clients.
    api_key: str | None = Field(default=None, min_length=16, max_length=128)
    database_path: Path = BACKEND_DIR / "data" / "market_data.db"
    stream_stale_seconds: float = Field(default=15, ge=1, le=120)
    stream_max_connections: int = Field(default=10, ge=1, le=50)
    stream_max_subscribers: int = Field(default=50, ge=1, le=500)
    stream_queue_size: int = Field(default=32, ge=1, le=256)
    stream_reconnect_seconds: float = Field(default=1, ge=0.1, le=60)
    websocket_origins: list[str] = ["http://127.0.0.1:8000", "http://localhost:8000", "http://127.0.0.1:5173", "http://localhost:5173"]

    @field_validator("trading_enabled", mode="before")
    @classmethod
    def accept_legacy_disabled_setting(cls, value: object) -> object:
        return False if isinstance(value, str) and value.strip().lower() == "false" else value

    @field_validator("websocket_origins")
    @classmethod
    def no_wildcard_origins(cls, value: list[str]) -> list[str]:
        if not value or any(origin == "*" or not origin.startswith(("http://", "https://")) for origin in value):
            raise ValueError("Browser origins must be explicit http(s) URLs; wildcards are not allowed.")
        return value

    @field_validator("allowed_hosts")
    @classmethod
    def explicit_hosts(cls,value: list[str]) -> list[str]:
        if not value or any(not host or any(c in host for c in '*/\\@?# ') for host in value):
            raise ValueError("Use explicit hostnames or IPs; wildcard/URL hosts are not allowed.")
        return value

    @field_validator("api_key")
    @classmethod
    def visible_application_key(cls, value: str | None) -> str | None:
        if value is not None and any(ord(character) < 33 or ord(character) > 126 for character in value):
            raise ValueError("The application key must contain visible ASCII characters only.")
        return value
