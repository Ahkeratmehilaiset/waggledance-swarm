"""Capacity axis: a straight burn is forecast exactly by the slope, persist lags by slope x horizon, no future leak."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import capacity_axis as c  # noqa: E402


def test_slope_is_exact_on_a_straight_burn_and_persist_lags():
    reset = 10000.0
    points = [(float(t), t // 10, reset) for t in range(0, 400, 5)]  # 0.1 percent per minute, integer readings
    result = c.evaluate(points, "seven_day", ("persist", "lsq30"))
    assert result["lsq30"]["h60"]["mae"] <= 1.0
    assert result["persist"]["h60"]["mae"] >= 5.0
    assert result["persist"]["limit_alerts"] == {"closed_window_false": 0, "open_window_unresolved": 0}
    assert result["lsq30"]["FORECAST_at_open_window_reset"]["at_reset"] == 100.0


def test_a_reset_inside_the_horizon_is_skipped():
    points = [(0.0, 50, 100.0), (5.0, 51, 100.0), (10.0, 52, 100.0), (95.0, 60, 100.0), (130.0, 1, 400.0)]
    result = c.evaluate(points, "five_hour", ("persist",))
    assert result["persist"]["h120"]["n"] == 0
