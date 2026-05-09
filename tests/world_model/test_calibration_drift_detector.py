# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.world_model.calibration_drift_detector.

Iteration N+6 first scout (Claude solo; Codex offline). The drift
detector identifies SYSTEMIC shifts in calibration over time
(Phase 9 §I) — not just local errors. Drift alerts feed the
calibration_oscillation tension upstream into the dream curriculum
producer (the same surface that PR #137 fixed for source_kind
linkage). Drift here would either:
- silently miss a systemic over-confident drift (autonomy lane
  promotes solvers that consistently over-estimate), or
- generate spurious alerts on stable systems (alert fatigue).

Near-zero dedicated module-level coverage on main as of
2026-05-09: the legacy Phase 9 world-model test hit broad
detect_drift cases, but not the boundary, sorting, windowing, and
serialization invariants pinned here.

Pinned invariants:

- Insufficient history (< window records) -> no alert; never
  raises.
- Mean signed gap (prior - evidence_implied) over last `window`
  records below `threshold` in absolute value -> no alert.
- Mean signed gap >= threshold (positive direction):
  direction="drift_overconfident", magnitude=|mean|.
- Mean signed gap <= -threshold (negative direction):
  direction="drift_low_confidence", magnitude=|mean|.
- Threshold check uses STRICT-LESS-THAN (`abs < threshold` skips);
  equal to threshold IS an alert. Pinned because flipping to <=
  would silently downgrade boundary-case alerts.
- Output sorted by dimension (audit determinism — `sorted(items)`).
- Multiple dimensions evaluated independently; one drifting and
  one stable produces exactly one alert.
- Magnitude rounded to 6 decimals.
- DriftAlert is frozen, to_dict round-trips all fields.
- DEFAULT_DRIFT_THRESHOLD == 0.20 and DEFAULT_DRIFT_WINDOW == 5
  (constants pinned so a config sneak-edit is loud).
"""
from __future__ import annotations

import dataclasses

import pytest

from waggledance.core.world_model import (
    DEFAULT_DRIFT_THRESHOLD,
    DEFAULT_DRIFT_WINDOW,
)
from waggledance.core.world_model.calibration_drift_detector import (
    DriftAlert,
    detect_drift,
)
from waggledance.core.world_model.prediction_calibrator import (
    CalibrationRecord,
)


# --- helpers -------------------------------------------------------

def _record(dim: str, prior: float, evidence: float,
                n_obs: int = 1) -> CalibrationRecord:
    return CalibrationRecord(
        dimension=dim,
        prior_score=prior,
        evidence_implied_score=evidence,
        abs_error=abs(prior - evidence),
        n_observations=n_obs,
    )


# --- module constants (pinned) ------------------------------------

def test_default_drift_threshold_pinned_at_0_20():
    assert DEFAULT_DRIFT_THRESHOLD == 0.20


def test_default_drift_window_pinned_at_5():
    assert DEFAULT_DRIFT_WINDOW == 5


# --- empty / insufficient history --------------------------------

def test_detect_drift_empty_history_returns_empty_alerts():
    """No dimensions present -> no alerts. Pure smoke; must not
    raise."""
    assert detect_drift({}) == []


def test_detect_drift_insufficient_records_no_alert():
    """Fewer than `window` records for a dimension -> no alert."""
    history = {
        "dim_a": [_record("dim_a", 0.9, 0.1) for _ in range(4)],
    }
    # default window = 5; only 4 records -> no alert
    assert detect_drift(history) == []


def test_detect_drift_zero_records_for_dimension_no_alert():
    history = {"dim_a": []}
    assert detect_drift(history) == []


# --- below-threshold: no alert -----------------------------------

def test_detect_drift_below_threshold_no_alert():
    """Mean signed gap |0.05| < threshold 0.20 -> no alert."""
    history = {
        "dim_a": [_record("dim_a", 0.55, 0.5) for _ in range(5)],
    }
    assert detect_drift(history) == []


def test_detect_drift_at_zero_gap_no_alert():
    history = {
        "dim_a": [_record("dim_a", 0.5, 0.5) for _ in range(10)],
    }
    assert detect_drift(history) == []


# --- threshold boundary: equal counts as alert -------------------

def test_detect_drift_exactly_at_threshold_triggers_alert():
    """The condition is `abs(mean_gap) < threshold` -> skip. Equal
    to threshold passes through as an alert. A flip to `<=` would
    silently downgrade boundary cases.

    Use exactly-representable floats (halves) to avoid the
    0.7 - 0.5 == 0.19999... float imprecision."""
    # mean gap exactly == 0.5 with halves (exactly representable):
    history = {
        "dim_a": [_record("dim_a", 1.0, 0.5) for _ in range(5)],  # gap 0.5
    }
    alerts = detect_drift(history, threshold=0.5)
    assert len(alerts) == 1
    assert alerts[0].magnitude == 0.5


# --- drift_overconfident -----------------------------------------

def test_detect_drift_overconfident_when_prior_higher_than_evidence():
    """prior > evidence (system over-estimated) for the window
    average -> drift_overconfident."""
    history = {
        "dim_a": [_record("dim_a", 0.9, 0.5) for _ in range(5)],
    }
    alerts = detect_drift(history)
    assert len(alerts) == 1
    a = alerts[0]
    assert a.dimension == "dim_a"
    assert a.direction == "drift_overconfident"
    assert a.magnitude == 0.4  # |0.9 - 0.5| = 0.4
    assert a.window_size == 5
    assert "mean(prior - evidence_implied)" in a.rationale


# --- drift_low_confidence ----------------------------------------

def test_detect_drift_low_confidence_when_prior_lower_than_evidence():
    """prior < evidence (system under-estimated) for the window
    average -> drift_low_confidence."""
    history = {
        "dim_a": [_record("dim_a", 0.3, 0.7) for _ in range(5)],
    }
    alerts = detect_drift(history)
    assert len(alerts) == 1
    a = alerts[0]
    assert a.direction == "drift_low_confidence"
    assert a.magnitude == 0.4
    assert "mean(prior - evidence_implied)" in a.rationale


# --- window slicing: last N records only -------------------------

def test_detect_drift_uses_last_window_records_only():
    """Old records outside the window MUST NOT contribute.
    Construct a series where the older records are stable and the
    last `window` show drift — alert should fire on recent data
    only."""
    stable = [_record("dim_a", 0.5, 0.5) for _ in range(10)]
    drifting = [_record("dim_a", 0.9, 0.5) for _ in range(5)]
    history = {"dim_a": stable + drifting}
    alerts = detect_drift(history)
    assert len(alerts) == 1
    assert alerts[0].direction == "drift_overconfident"
    assert alerts[0].magnitude == 0.4
    # Window size = configured (5), not full history (15)
    assert alerts[0].window_size == 5


def test_detect_drift_recent_stable_no_alert_despite_old_drift():
    """Symmetric: stable recent data + drifting old data -> no
    alert. Drift detection is RECENT-window only."""
    drifting = [_record("dim_a", 0.9, 0.5) for _ in range(10)]
    stable = [_record("dim_a", 0.5, 0.5) for _ in range(5)]
    history = {"dim_a": drifting + stable}
    assert detect_drift(history) == []


# --- multi-dimensional + sorting ---------------------------------

def test_detect_drift_multiple_dimensions_independent():
    """Each dimension is evaluated independently — one drifting
    and one stable produces exactly one alert (the drifting one)."""
    history = {
        "dim_drifting": [_record("dim_drifting", 0.9, 0.5) for _ in range(5)],
        "dim_stable":   [_record("dim_stable",   0.5, 0.5) for _ in range(5)],
    }
    alerts = detect_drift(history)
    assert len(alerts) == 1
    assert alerts[0].dimension == "dim_drifting"


def test_detect_drift_alerts_sorted_by_dimension():
    """When multiple dimensions drift, output sorted by dimension
    name for audit byte-stability."""
    history = {
        "z_dim": [_record("z_dim", 0.9, 0.5) for _ in range(5)],
        "a_dim": [_record("a_dim", 0.3, 0.7) for _ in range(5)],
        "m_dim": [_record("m_dim", 0.95, 0.4) for _ in range(5)],
    }
    alerts = detect_drift(history)
    dims = [a.dimension for a in alerts]
    assert dims == sorted(dims) == ["a_dim", "m_dim", "z_dim"]


# --- magnitude rounded to 6 decimals -----------------------------

def test_detect_drift_magnitude_rounded_to_six_decimals():
    """Long-tail floats truncated. Use 1/3 to force a many-digit
    raw value, then assert round(|mean|, 6)."""
    one_third_diff = 1 / 3
    history = {
        "dim_a": [
            _record("dim_a", 0.5 + one_third_diff, 0.5)
            for _ in range(5)
        ],
    }
    alerts = detect_drift(history, threshold=0.10)
    assert len(alerts) == 1
    assert alerts[0].magnitude == round(one_third_diff, 6)


# --- custom threshold + window ----------------------------------

def test_detect_drift_custom_threshold_widens_band():
    """A higher threshold filters out smaller drifts. Use halves
    to avoid float imprecision (0.7 - 0.5 == 0.19999...)."""
    history = {
        "dim_a": [_record("dim_a", 0.75, 0.5) for _ in range(5)],
    }
    # mean gap = 0.25 > default threshold 0.20 -> alert fires
    assert len(detect_drift(history)) == 1
    # custom threshold 0.5: 0.25 < 0.5 -> no alert
    assert detect_drift(history, threshold=0.5) == []


def test_detect_drift_custom_window_changes_required_history():
    """A larger window requires more records."""
    history = {
        "dim_a": [_record("dim_a", 0.9, 0.5) for _ in range(5)],
    }
    # default window 5: alert fires
    assert len(detect_drift(history)) == 1
    # custom window 10: insufficient records -> no alert
    assert detect_drift(history, window=10) == []


def test_detect_drift_custom_window_uses_last_n_when_extra_history():
    """With more history than window, only the last N count."""
    drifting = [_record("dim_a", 0.9, 0.5) for _ in range(20)]
    history = {"dim_a": drifting}
    alerts = detect_drift(history, window=3)
    assert len(alerts) == 1
    assert alerts[0].window_size == 3


# --- DriftAlert dataclass contract -------------------------------

def test_drift_alert_is_frozen():
    a = DriftAlert(
        dimension="x", direction="drift_overconfident",
        magnitude=0.5, window_size=5,
        rationale="test",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.magnitude = 0.0  # type: ignore[misc]


def test_drift_alert_to_dict_round_trips_fields():
    a = DriftAlert(
        dimension="dim_a", direction="drift_low_confidence",
        magnitude=0.42, window_size=5,
        rationale="custom rationale",
    )
    d = a.to_dict()
    assert d == {
        "dimension": "dim_a",
        "direction": "drift_low_confidence",
        "magnitude": 0.42,
        "window_size": 5,
        "rationale": "custom rationale",
    }


# --- mixed signed gaps cancel out --------------------------------

def test_detect_drift_alternating_signs_cancel_out():
    """If recent records oscillate around zero gap, mean cancels
    out and no alert fires — even though individual records have
    high abs error. Drift is SYSTEMIC, not noisy."""
    history = {
        "dim_a": [
            _record("dim_a", 0.9, 0.5),
            _record("dim_a", 0.1, 0.5),
            _record("dim_a", 0.9, 0.5),
            _record("dim_a", 0.1, 0.5),
            _record("dim_a", 0.5, 0.5),
        ],
    }
    # mean = (0.4 - 0.4 + 0.4 - 0.4 + 0.0) / 5 = 0.0 -> no alert
    assert detect_drift(history) == []
