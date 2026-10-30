"""Valid no-signal histories must remain serializable and persistable."""
import json

from app.backtesting.engine import run_validation
from app.storage.platform import PlatformStore
from test_research import market_frame, backtest_config


async def test_no_trade_validation_retains_requested_parameter_and_persists(settings):
    data = market_frame()
    data[['open', 'close']] = 100.0
    data['high'], data['low'] = 101.0, 99.0
    config = backtest_config()
    result = run_validation(data, config)
    json.dumps(result, allow_nan=False)
    assert result['out_of_sample']['metrics']['trades'] == 0
    assert result['out_of_sample']['metrics']['win_rate'] is None
    assert result['holdout_parameters']['atr_stop'] == config.atr_stop
    assert 'insufficient' in result['selection_status']
    assert all(item['selection_score'] is None for item in result['sensitivity'])
    assert all('insufficient' in fold['selection_status'] for fold in result['walk_forward'])
    store = PlatformStore(settings.database_path)
    await store.initialize()
    try:
        await store.put('backtest_results', 'zero-trades', result, config.symbol, config.timeframe)
        assert (await store.records('backtest_results'))[0]['data']['out_of_sample']['metrics']['trades'] == 0
    finally:
        await store.aclose()
