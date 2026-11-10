import pytest

from app.core.config import Settings


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, max_retries=0, retry_base_seconds=0,
                    min_request_interval_seconds=0.1, database_path=tmp_path / "test.db",
                    stream_reconnect_seconds=0.1,allowed_hosts=['testserver','127.0.0.1','localhost'],
                    allowed_symbols=[])


@pytest.fixture
def symbol() -> dict:
    # Synthetic test metadata, never used as application fallback data.
    return {"symbol": "TESTUSDT", "baseAsset": "TEST", "quoteAsset": "USDT",
            "marginAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"}
