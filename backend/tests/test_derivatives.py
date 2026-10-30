from app.analysis.scoring import derivative_context
from datetime import UTC, datetime

HOUR = 3600 * 1000


def history(start_value, end_value, points=25):
    latest = int(datetime.now(UTC).timestamp()*1000)//HOUR*HOUR
    return [{"time": latest - (points-1-index) * HOUR,
             "value": start_value + (end_value - start_value) * index / (points - 1)}
            for index in range(points)]


def test_price_and_open_interest_combinations_are_context_not_votes():
    rising = derivative_context({"change_percent": 4}, {"open_interest_history": history(100, 130)})
    assert rising["available"] is True
    assert rising["open_interest_change_percent"] == 30
    assert rising["scenario_effects"] == {"LONG": "supporting", "SHORT": "conflicting"}
    assert "no score weight" not in rising["interpretation"]

    falling_price = derivative_context({"change_percent": -4}, {"open_interest_history": history(100, 130)})
    assert falling_price["scenario_effects"] == {"LONG": "conflicting", "SHORT": "supporting"}

    short_covering = derivative_context({"change_percent": 4}, {"open_interest_history": history(130, 100)})
    assert short_covering["scenario_effects"] == {"LONG": "conflicting", "SHORT": "conflicting"}
    assert "short covering" in short_covering["interpretation"]


def test_derivative_context_requires_and_reports_enough_history():
    nearly_a_day = derivative_context({"change_percent": 4}, {"open_interest_history": history(100, 130, points=21)})
    assert nearly_a_day["available"] is True
    assert nearly_a_day["span_hours"] == 20
    too_short = derivative_context({"change_percent": 4}, {"open_interest_history": history(100, 130, points=20)})
    assert too_short["available"] is False and too_short["span_hours"] is None


def test_derivative_context_does_not_interpret_flat_changes():
    flat_price = derivative_context({"change_percent": 0.05}, {"open_interest_history": history(100, 130)})
    assert flat_price["available"] is True and flat_price["scenario_effects"] == {}
    assert "approximately flat" in flat_price["interpretation"]
    flat_open_interest = derivative_context({"change_percent": 4}, {"open_interest_history": history(100, 100.2)})
    assert flat_open_interest["scenario_effects"] == {}


def test_derivative_context_rejects_sparse_history():
    sparse = [{"time": 1_700_000_000_000, "value": 100}, {"time": 1_700_000_000_000 + 24 * HOUR, "value": 130}]
    result = derivative_context({"change_percent": 4}, {"open_interest_history": sparse})
    assert result["available"] is False and "too short" in result["interpretation"]


def test_derivative_context_rejects_stale_future_and_invalid_interior_samples():
    for change in ('stale', 'future', 'negative', 'nonfinite', 'bool_time'):
        points = history(100, 130)
        if change == 'stale':
            for point in points: point['time'] -= 3*HOUR
        elif change == 'future': points[-1]['time'] += 2*HOUR
        elif change == 'negative': points[10]['value'] = -1
        elif change == 'nonfinite': points[10]['value'] = float('nan')
        else: points[0]['time'] = True
        assert derivative_context({'change_percent': 4}, {'open_interest_history': points})['available'] is False


def test_derivative_context_fails_closed_without_reliable_history():
    assert derivative_context({}, None)["available"] is False
    assert derivative_context(None, [])["available"] is False
    assert derivative_context({"change_percent": 2}, {"open_interest_history": []})["available"] is False
    short = derivative_context({"change_percent": 2}, {"open_interest_history": history(100, 130, points=3)})
    assert short["available"] is False and "too short" in short["interpretation"]
    malformed = derivative_context({"change_percent": 2}, {"open_interest_history": [{"time": 1}, {"time": 2, "value": 3}]})
    assert malformed["available"] is False and malformed["scenario_effects"] == {}
    unordered = derivative_context({"change_percent": 2}, {"open_interest_history": list(reversed(history(100, 130)))})
    assert unordered["available"] is False
