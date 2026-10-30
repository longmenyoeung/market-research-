import pytest

from app.adapters.base import MarketDataError
from app.adapters.binance_usdm import parse_contracts


def test_all_matching_contracts_sorted_without_liquidity_filter(symbol):
    payload = {"symbols": [symbol, {**symbol, "symbol": "AAAUSDT", "baseAsset": "AAA"}]}
    assert [item.symbol for item in parse_contracts(payload)] == ["AAAUSDT", "TESTUSDT"]


@pytest.mark.parametrize("field,value", [
    ("status", "PENDING_TRADING"), ("status", "BREAK"),
    ("contractType", "CURRENT_QUARTER"), ("contractType", ""),
    ("quoteAsset", "USDC"), ("marginAsset", "BTC"),
])
def test_filters_metadata_not_symbol_suffix(symbol, field, value):
    assert parse_contracts({"symbols": [{**symbol, field: value}]}) == []


def test_symbol_name_is_not_used_as_eligibility_rule(symbol):
    assert parse_contracts({"symbols": [{**symbol, "symbol": "NEWCONTRACT"}]})[0].symbol == "NEWCONTRACT"


@pytest.mark.parametrize("payload", [None, [], {}, {"symbols": None}, {"symbols": [{}]},
                                      {"code": -1000, "msg": "upstream error"}])
def test_invalid_payload_is_not_silently_returned_as_empty(payload):
    with pytest.raises(MarketDataError, match="incomplete or invalid"):
        parse_contracts(payload)


def test_one_invalid_record_fails_whole_universe(symbol):
    with pytest.raises(MarketDataError):
        parse_contracts({"symbols": [symbol, {**symbol, "symbol": 123}]})


def test_duplicate_symbols_fail(symbol):
    with pytest.raises(MarketDataError, match="duplicate"):
        parse_contracts({"symbols": [symbol, symbol]})


def test_valid_empty_universe():
    assert parse_contracts({"symbols": []}) == []
