"""Source deadlines and selective recalculation, using explicitly synthetic data."""
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import create_app
from app.schemas.candles import CandleQuery, CandleUpdate
from app.schemas.platform import AnalysisRequest
from test_research import ResearchProvider


def test_cached_report_expires_with_source_quotes_not_generation_time(settings, monkeypatch):
    with TestClient(create_app(settings, provider=ResearchProvider())) as client:
        payload = {"symbol": "TESTUSDT", "timeframe": "1h", "include_derivatives": False}
        first = client.post('/api/v1/analysis', json=payload).json()
        # Keep the cache clock unchanged while advancing only the source-data
        # freshness clock, so this specifically exercises a cached response.
        now = datetime.now(UTC)
        class Later(datetime):
            @classmethod
            def now(cls, tz=None):
                return now + timedelta(seconds=65)
        monkeypatch.setattr('app.services.platform.datetime', Later)
        later = client.post('/api/v1/analysis', json=payload).json()
        assert later['id'] == first['id']
        assert later['assessment']['classification'] == 'NO TRADE'
        assert 'expired' in later['assessment']['final_assessment']
        assert all(s['entry_region'] is None and s['risk'] is None for s in later['assessment']['scenarios'].values())
        assert all(a['kind'] != 'plan' for a in later['structure']['annotations'])
        # Historical records retain what was known when they were generated.
        assert 'expired' not in client.get('/api/v1/history/reports').json()[0]['data']['assessment']['final_assessment']


async def test_closed_candle_invalidates_only_that_symbols_cached_reports(settings):
    from app.market_data.service import MarketDataService
    from app.services.platform import PlatformService
    from app.storage.platform import PlatformStore

    provider = ResearchProvider()
    store = PlatformStore(settings.database_path)
    await store.initialize()
    market = MarketDataService(provider, store, settings)
    platform = PlatformService(market, store, settings)
    try:
        request = AnalysisRequest(symbol='TESTUSDT', include_derivatives=False)
        other = request.model_copy(update={'symbol': 'LOWUSDT'})
        first, untouched = await platform.report(request), await platform.report(other)
        page = await market.get_candles(CandleQuery(symbol='TESTUSDT', timeframe='1h', limit=400))
        update = CandleUpdate(symbol='TESTUSDT', timeframe='1h', event_time=page.server_time,
                              received_at=datetime.now(UTC), candle=page.candles[-1], stale=False)
        await market.on_update(update)
        assert (await platform.report(request))['id'] != first['id']
        assert (await platform.report(other))['id'] == untouched['id']
    finally:
        await platform.aclose()
        await market.aclose()
        await store.aclose()


@pytest.mark.parametrize('overrides', [
    {'timeframe': '4h', 'higher_timeframes': ['1h']},
    {'timeframe': '1h', 'higher_timeframes': ['1h']},
    {'timeframe': '1h', 'lower_timeframe': '4h'},
    {'timeframe': '1m', 'lower_timeframe': '1m'},
])
def test_timeframe_roles_reject_invalid_combinations(overrides):
    with pytest.raises(ValidationError):
        AnalysisRequest(symbol='TESTUSDT', **overrides)


def test_indicator_history_budget_covers_nested_warmups(settings):
    with TestClient(create_app(settings, provider=ResearchProvider())) as client:
        preferences = client.get('/api/v1/settings').json()
        preferences['indicators'].update(momentum_period=300, stochastic_smooth=300, awesome_slow=300)
        assert client.put('/api/v1/settings', json=preferences).status_code == 200
        response = client.post('/api/v1/analysis', json={'symbol': 'TESTUSDT', 'timeframe': '1d', 'include_derivatives': False})
        assert response.status_code == 200, response.text
        result = response.json()
        assert len(result['candles']) >= 600
        for name in ('stoch_rsi', 'stochastic_d', 'awesome', 'rsi_300'):
            assert result['indicators'][name]['value'] is not None


@pytest.mark.parametrize('override', [
    {'bid': None}, {'ask': None}, {'bid': 111}, {'ask': 0},
    {'last_price': float('nan')}, {'spread_bps': -1}, {'book_time': True},
])
def test_missing_or_invalid_quote_inputs_never_generate_entry_references(override):
    from app.analysis.scoring import assess
    from app.analysis.structure import analyze_structure
    from app.indicators.engine import calculate
    from app.schemas.platform import Preferences
    from test_research import market_frame
    preferences = Preferences(require_higher_alignment=False)
    df, _ = calculate(market_frame(), preferences.indicators, '1h')
    now = int(datetime.now(UTC).timestamp()*1000)
    metric = dict(last_price=110, bid=109.99, ask=110.01, spread_bps=2,
                  volume=10_000_000, ticker_time=now, book_time=now)
    metric.update(override)
    assessment = assess(df, analyze_structure(df, '1h'), preferences, metric, [], [], True, '1h')
    assert assessment['classification'] == 'NO TRADE'
    assert all(scenario['entry_region'] is None for scenario in assessment['scenarios'].values())


def test_equal_confirmed_pivots_are_not_labeled_lower_highs_or_lows():
    from app.analysis.structure import analyze_structure
    import pandas as pd
    import numpy as np
    prices = np.array([100, 101, 102, 103, 102, 101, 100, 101, 102, 103, 102, 101, 100, 101, 102], dtype=float)
    df = pd.DataFrame({'time': 1_700_006_400_000+np.arange(len(prices))*3600000,
                       'open': prices-.1, 'close': prices, 'high': prices+1, 'low': prices-1})
    structure = analyze_structure(df, '1h', window=1)
    highs = [s for s in structure['swings'] if s['kind'] == 'high']
    lows = [s for s in structure['swings'] if s['kind'] == 'low']
    assert highs[-1]['label'] == 'EH' and lows[-1]['label'] == 'EL'
    assert structure['trend'] == 'neutral'


@pytest.mark.parametrize('override', [
    {'entry': '1e-1000000', 'stop': '5e-1000001', 'target': '2e-1000000'},
    {'quantity_step': '1e-1000'}, {'tick_size': '1e-1000'},
])
def test_unrepresentable_planner_inputs_return_validation_errors(settings, override):
    with TestClient(create_app(settings, provider=ResearchProvider())) as client:
        response = client.post('/api/v1/planner', json={
            'side': 'LONG', 'equity': 1000, 'entry': 100, 'stop': 95, 'target': 110, **override})
        assert response.status_code == 422
