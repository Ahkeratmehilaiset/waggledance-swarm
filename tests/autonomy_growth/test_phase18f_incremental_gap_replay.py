# SPDX-License-Identifier: BUSL-1.1
"""Phase 18F - incremental runtime gap replay tests.

Covers cursor state, no-op replay, post-cursor incremental replay,
strict loader, RuntimeGapDetector bridge, concurrency lock, and
honesty invariants.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.autonomy_growth.gap_candidate import (  # noqa: E402
    GapVerdict,
)
from waggledance.core.autonomy_growth.gap_intake import GapSignal  # noqa: E402
from waggledance.core.autonomy_growth.gap_mining import (  # noqa: E402
    ALLOWED_FAMILIES,
)
from waggledance.core.autonomy_growth.incremental_gap_replay import (  # noqa: E402
    REPLAY_CURSOR_KEY,
    REPLAY_LOCK_KEY,
    BridgeRejectionError,
    bridge_detector_signal_to_phase18e_event,
    load_runtime_gap_events_after_id,
    persist_detector_gap_signals_as_replay_events,
    read_replay_cursor,
    run_incremental_gap_replay_once,
    write_replay_cursor,
)
from waggledance.core.autonomy_growth.runtime_gap_replay import (  # noqa: E402
    PHASE18E_RUNTIME_GAP_EVENT_KIND,
    SCHEMA_VERSION,
    persist_runtime_gap_events,
)
from waggledance.core.autonomy_growth.solver_dispatcher import (  # noqa: E402
    LowRiskSolverDispatcher,
)
from waggledance.core.storage.control_plane import (  # noqa: E402
    ControlPlaneDB,
)
from waggledance.core.storage.control_plane_schema import (  # noqa: E402
    SCHEMA_VERSION as CONTROL_PLANE_SCHEMA_VERSION,
)


ORIGINAL_FAMILY_FEATURES: dict[str, dict[str, Any]] = {
    "scalar_unit_conversion": {
        "input_unit": "km", "output_unit": "miles",
        "rule": "1 km = 0.621371 miles",
    },
    "lookup_table": {
        "table_name": "chemical_symbols", "example_key": "tin",
    },
    "threshold_rule": {
        "threshold": 30, "example_value": 37, "rule": "above_or_below",
    },
    "interval_bucket_classifier": {
        "buckets": "[0,10),[10,20),[20,30)", "example_value": 17,
    },
    "linear_arithmetic": {
        "operator": "add", "example_inputs": {"a": 14, "b": 9},
    },
    "bounded_interpolation": {
        "endpoints": "(0,0)->(10,100)", "example_x": 3,
    },
}


PHASE18F_FAMILY_FEATURES: dict[str, dict[str, Any]] = {
    "scalar_unit_conversion": {
        "input_unit": "m", "output_unit": "ft",
        "rule": "1 m = 3.28084 ft",
    },
    "lookup_table": {
        "table_name": "country_codes", "example_key": "fi",
    },
    "threshold_rule": {
        "threshold": 100, "example_value": 150, "rule": "alert_or_quiet",
    },
    "interval_bucket_classifier": {
        "buckets": "[0,33),[33,66),[66,100]", "example_value": 50,
    },
    "linear_arithmetic": {
        "operator": "subtract", "example_inputs": {"a": 20, "b": 5},
    },
    "bounded_interpolation": {
        "endpoints": "(0,0)->(100,1)", "example_x": 50,
    },
}


def _ev(*, family_kind: str, feature_dict: dict[str, Any],
         signal_idx: int, confidence: float = 0.9,
         risk: str = "low_risk", cluster_window: str = "",
         evidence_ref: str | None = None,
         miss_reason: str = "capability_lookup_miss",
         source: str = "phase18f_test") -> dict[str, Any]:
    if evidence_ref is None:
        evidence_ref = f"audit:phase18f:test:{signal_idx:04d}"
    return {
        "schema_version": SCHEMA_VERSION,
        "occurred_at_utc": "2026-05-06T00:00:00Z",
        "source": source,
        "family_kind": family_kind,
        "feature_dict": feature_dict,
        "raw_query": "missed runtime query",
        "miss_reason": miss_reason,
        "confidence_hint": confidence,
        "risk_label": risk,
        "evidence_ref": evidence_ref,
        "cluster_window": cluster_window,
        "signal_id": f"phase18f_signal_{signal_idx:04d}",
    }


def _strong_batch(features: dict[str, dict[str, Any]],
                    *, base_idx: int = 0) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    idx = base_idx
    for fam in ALLOWED_FAMILIES:
        for sub in range(2):
            idx += 1
            out.append(_ev(
                family_kind=fam, feature_dict=features[fam],
                signal_idx=idx, confidence=0.9,
            ))
    return out


def _stringify(features: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in features.items():
        if isinstance(v, (str, int, float, bool)):
            out[str(k)] = str(v)
        elif v is None:
            out[str(k)] = ""
        else:
            out[str(k)] = json.dumps(v, sort_keys=True,
                                       separators=(",", ":"),
                                       default=str, ensure_ascii=True)
    return out


def _runtime_table_counts(cp: ControlPlaneDB) -> dict[str, int]:
    with cp._lock:  # noqa: SLF001
        return {
            "solvers": int(cp._conn.execute(
                "SELECT COUNT(*) AS c FROM solvers",
            ).fetchone()["c"]),
            "solver_capability_features": int(cp._conn.execute(
                "SELECT COUNT(*) AS c FROM solver_capability_features",
            ).fetchone()["c"]),
            "solver_artifacts": int(cp._conn.execute(
                "SELECT COUNT(*) AS c FROM solver_artifacts",
            ).fetchone()["c"]),
        }


@pytest.fixture
def temp_cp(tmp_path: Path) -> ControlPlaneDB:
    cp = ControlPlaneDB(tmp_path / "p18f_test.db")
    yield cp
    cp.close()


# ---------------------------------------------------------------------------
# 1. Cursor + lock state
# ---------------------------------------------------------------------------

def test_initial_cursor_is_zero(temp_cp):
    c = read_replay_cursor(temp_cp)
    assert c.last_processed_id == 0
    assert c.advanced_at_utc == ""


def test_write_replay_cursor_persists(temp_cp):
    write_replay_cursor(temp_cp, last_processed_id=42)
    c = read_replay_cursor(temp_cp)
    assert c.last_processed_id == 42
    assert c.advanced_at_utc != ""


def test_replay_state_uses_schema_meta_no_new_table(temp_cp):
    write_replay_cursor(temp_cp, last_processed_id=7)
    raw = temp_cp.get_meta(REPLAY_CURSOR_KEY)
    assert raw is not None
    payload = json.loads(raw)
    assert payload["last_processed_id"] == 7


def test_cursor_survives_db_close_reopen(tmp_path: Path):
    cp_path = tmp_path / "p18f_persist.db"
    cp = ControlPlaneDB(cp_path)
    persist_runtime_gap_events(cp, _strong_batch(ORIGINAL_FAMILY_FEATURES))
    run_incremental_gap_replay_once(cp)
    cursor_after_first = read_replay_cursor(cp).last_processed_id
    assert cursor_after_first > 0
    cp.close()

    cp2 = ControlPlaneDB(cp_path)
    cursor_reopened = read_replay_cursor(cp2).last_processed_id
    assert cursor_reopened == cursor_after_first
    cp2.close()


# ---------------------------------------------------------------------------
# 2. First incremental replay
# ---------------------------------------------------------------------------

def test_first_replay_processes_seed_rows(temp_cp):
    persist_runtime_gap_events(temp_cp, _strong_batch(ORIGINAL_FAMILY_FEATURES))
    res = run_incremental_gap_replay_once(temp_cp)
    assert res.status == "OK"
    assert res.loaded_event_count == 12  # 6 families × 2 signals
    assert res.cursor_advanced is True
    assert res.registered_solver_count == 6
    assert res.families_covered == 6


def test_first_replay_advances_cursor(temp_cp):
    persist_runtime_gap_events(temp_cp, _strong_batch(ORIGINAL_FAMILY_FEATURES))
    before = read_replay_cursor(temp_cp).last_processed_id
    res = run_incremental_gap_replay_once(temp_cp)
    after = read_replay_cursor(temp_cp).last_processed_id
    assert before == 0
    assert after > 0
    assert res.cursor_after == after


def test_first_replay_dispatch_hits(temp_cp):
    persist_runtime_gap_events(temp_cp, _strong_batch(ORIGINAL_FAMILY_FEATURES))
    run_incremental_gap_replay_once(temp_cp)
    dispatcher = LowRiskSolverDispatcher(temp_cp)
    cases = [
        ("scalar_unit_conversion", {"x": 10.0}, 6.21371),
        ("lookup_table", {"key": "tin"}, "Sn"),
        ("threshold_rule", {"x": 37}, "above"),
        ("interval_bucket_classifier", {"x": 17}, "[10,20)"),
        ("linear_arithmetic", {"a": 5.0, "b": 7.0}, 12.0),
        ("bounded_interpolation", {"x": 3}, 30.0),
    ]
    for fam, inputs, expected in cases:
        r = dispatcher.dispatch_by_features(
            family_kind=fam,
            features=_stringify(ORIGINAL_FAMILY_FEATURES[fam]),
            inputs=inputs,
        )
        assert r.matched is True
        assert r.reason in ("hit", "hit_by_features")
        if isinstance(expected, float):
            assert math.isclose(float(r.output), expected,
                                  rel_tol=1e-9, abs_tol=1e-9)
        else:
            assert r.output == expected


# ---------------------------------------------------------------------------
# 3. No-op replay
# ---------------------------------------------------------------------------

def test_no_op_replay_processes_zero_rows(temp_cp):
    persist_runtime_gap_events(temp_cp, _strong_batch(ORIGINAL_FAMILY_FEATURES))
    run_incremental_gap_replay_once(temp_cp)
    counts1 = _runtime_table_counts(temp_cp)
    res = run_incremental_gap_replay_once(temp_cp)
    counts2 = _runtime_table_counts(temp_cp)
    assert res.status == "OK"
    assert res.loaded_event_count == 0
    assert res.cursor_advanced is False
    assert counts1 == counts2


def test_no_op_replay_immediately_after_init(temp_cp):
    res = run_incremental_gap_replay_once(temp_cp)
    assert res.status == "OK"
    assert res.loaded_event_count == 0
    assert res.cursor_advanced is False
    counts = _runtime_table_counts(temp_cp)
    assert counts["solvers"] == 0


# ---------------------------------------------------------------------------
# 4. Post-cursor incremental replay
# ---------------------------------------------------------------------------

def test_post_cursor_replay_processes_only_new_rows(temp_cp):
    persist_runtime_gap_events(temp_cp, _strong_batch(ORIGINAL_FAMILY_FEATURES))
    first = run_incremental_gap_replay_once(temp_cp)
    cursor_after_first = read_replay_cursor(temp_cp).last_processed_id

    # Append phase18f-extended events.
    persist_runtime_gap_events(
        temp_cp,
        _strong_batch(PHASE18F_FAMILY_FEATURES, base_idx=1000),
    )
    second = run_incremental_gap_replay_once(temp_cp)
    cursor_after_second = read_replay_cursor(temp_cp).last_processed_id

    assert first.cursor_after == cursor_after_first
    assert second.cursor_before == cursor_after_first
    assert second.cursor_after > cursor_after_first
    assert second.loaded_event_count == 12  # 6 families × 2 signals
    assert second.registered_solver_count == 6
    assert second.families_covered == 6


def test_post_cursor_dispatch_hits_new_solvers(temp_cp):
    persist_runtime_gap_events(temp_cp, _strong_batch(ORIGINAL_FAMILY_FEATURES))
    run_incremental_gap_replay_once(temp_cp)
    persist_runtime_gap_events(
        temp_cp,
        _strong_batch(PHASE18F_FAMILY_FEATURES, base_idx=1000),
    )
    run_incremental_gap_replay_once(temp_cp)
    dispatcher = LowRiskSolverDispatcher(temp_cp)
    cases = [
        ("scalar_unit_conversion", {"x": 1.0}, 3.28084),
        ("lookup_table", {"key": "fi"}, "Finland"),
        ("threshold_rule", {"x": 150}, "alert"),
        ("interval_bucket_classifier", {"x": 10}, "low"),
        ("linear_arithmetic", {"a": 20.0, "b": 5.0}, 15.0),
        ("bounded_interpolation", {"x": 50}, 0.5),
    ]
    for fam, inputs, expected in cases:
        r = dispatcher.dispatch_by_features(
            family_kind=fam,
            features=_stringify(PHASE18F_FAMILY_FEATURES[fam]),
            inputs=inputs,
        )
        assert r.matched is True
        assert r.reason in ("hit", "hit_by_features")
        if isinstance(expected, float):
            assert math.isclose(float(r.output), expected,
                                  rel_tol=1e-9, abs_tol=1e-9)
        else:
            assert r.output == expected


def test_total_registered_solver_count_after_two_replays(temp_cp):
    persist_runtime_gap_events(temp_cp, _strong_batch(ORIGINAL_FAMILY_FEATURES))
    run_incremental_gap_replay_once(temp_cp)
    persist_runtime_gap_events(
        temp_cp,
        _strong_batch(PHASE18F_FAMILY_FEATURES, base_idx=1000),
    )
    run_incremental_gap_replay_once(temp_cp)
    counts = _runtime_table_counts(temp_cp)
    assert counts["solvers"] == 12  # 6 original + 6 phase18f-new


# ---------------------------------------------------------------------------
# 5. Strict load (malformed / type-confused / forbidden)
# ---------------------------------------------------------------------------

def _insert_raw_phase18e(cp: ControlPlaneDB, payload_text: str) -> int:
    rec = cp.record_runtime_gap_signal(
        kind=PHASE18E_RUNTIME_GAP_EVENT_KIND,
        family_kind="scalar_unit_conversion",
        cell_coord=None,
        signal_payload=payload_text,
        weight=0.0,
        observed_at="2026-05-06T00:00:00Z",
    )
    return int(rec.id)


def test_load_rejects_malformed_json(temp_cp):
    _insert_raw_phase18e(temp_cp, "{not valid json")
    res = load_runtime_gap_events_after_id(temp_cp, after_id=0)
    assert res.events == ()
    assert res.malformed_event_rejection_count == 1
    assert res.type_confusion_rejection_count == 0


def test_load_rejects_json_array(temp_cp):
    _insert_raw_phase18e(temp_cp, '[1,2,3]')
    res = load_runtime_gap_events_after_id(temp_cp, after_id=0)
    assert res.events == ()
    assert res.type_confusion_rejection_count == 1


def test_load_rejects_json_string(temp_cp):
    _insert_raw_phase18e(temp_cp, '"a string"')
    res = load_runtime_gap_events_after_id(temp_cp, after_id=0)
    assert res.events == ()
    assert res.type_confusion_rejection_count == 1


def test_load_rejects_json_null(temp_cp):
    _insert_raw_phase18e(temp_cp, "null")
    res = load_runtime_gap_events_after_id(temp_cp, after_id=0)
    assert res.events == ()
    assert res.type_confusion_rejection_count == 1


def test_load_rejects_json_number(temp_cp):
    _insert_raw_phase18e(temp_cp, "42")
    res = load_runtime_gap_events_after_id(temp_cp, after_id=0)
    assert res.events == ()
    assert res.type_confusion_rejection_count == 1


def test_load_rejects_empty_payload(temp_cp):
    cp = temp_cp
    cp.record_runtime_gap_signal(
        kind=PHASE18E_RUNTIME_GAP_EVENT_KIND,
        family_kind="scalar_unit_conversion",
        cell_coord=None,
        signal_payload=None,
        weight=0.0,
        observed_at="2026-05-06T00:00:00Z",
    )
    res = load_runtime_gap_events_after_id(cp, after_id=0)
    assert res.events == ()
    assert res.malformed_event_rejection_count == 1


def test_load_rejects_missing_required_field(temp_cp):
    payload = json.dumps({
        "schema_version": SCHEMA_VERSION,
        "occurred_at_utc": "2026-05-06T00:00:00Z",
        "source": "test",
        "family_kind": "scalar_unit_conversion",
        # feature_dict missing
        "raw_query": "x", "miss_reason": "x",
        "confidence_hint": 0.9, "risk_label": "low_risk",
        "evidence_ref": "x", "cluster_window": "",
    }, sort_keys=True)
    _insert_raw_phase18e(temp_cp, payload)
    res = load_runtime_gap_events_after_id(temp_cp, after_id=0)
    assert res.events == ()
    assert res.malformed_event_rejection_count == 1


def test_load_rejects_forbidden_field(temp_cp):
    payload = json.dumps({
        "schema_version": SCHEMA_VERSION,
        "occurred_at_utc": "2026-05-06T00:00:00Z",
        "source": "test",
        "family_kind": "scalar_unit_conversion",
        "feature_dict": ORIGINAL_FAMILY_FEATURES["scalar_unit_conversion"],
        "raw_query": "x", "miss_reason": "x",
        "confidence_hint": 0.9, "risk_label": "low_risk",
        "evidence_ref": "x", "cluster_window": "",
        "github_token": "PLACEHOLDER",
    }, sort_keys=True)
    _insert_raw_phase18e(temp_cp, payload)
    res = load_runtime_gap_events_after_id(temp_cp, after_id=0)
    assert res.events == ()
    assert res.forbidden_field_rejections == 1


def test_load_rejects_secret_value(temp_cp):
    feat = dict(ORIGINAL_FAMILY_FEATURES["scalar_unit_conversion"])
    feat["leak"] = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz1234"
    payload = json.dumps({
        "schema_version": SCHEMA_VERSION,
        "occurred_at_utc": "2026-05-06T00:00:00Z",
        "source": "test", "family_kind": "scalar_unit_conversion",
        "feature_dict": feat,
        "raw_query": "x", "miss_reason": "x",
        "confidence_hint": 0.9, "risk_label": "low_risk",
        "evidence_ref": "x", "cluster_window": "",
    }, sort_keys=True)
    _insert_raw_phase18e(temp_cp, payload)
    res = load_runtime_gap_events_after_id(temp_cp, after_id=0)
    assert res.events == ()
    assert res.forbidden_field_rejections == 1


def test_load_passes_well_formed(temp_cp):
    persist_runtime_gap_events(temp_cp, [
        _ev(family_kind="scalar_unit_conversion",
             feature_dict=ORIGINAL_FAMILY_FEATURES["scalar_unit_conversion"],
             signal_idx=1),
    ])
    res = load_runtime_gap_events_after_id(temp_cp, after_id=0)
    assert len(res.events) == 1
    assert res.malformed_event_rejection_count == 0
    assert res.type_confusion_rejection_count == 0


# ---------------------------------------------------------------------------
# 6. Verdict invariants (high-risk, out-of-family, builder-handoff, duplicate)
# ---------------------------------------------------------------------------

def test_high_risk_event_does_not_register(temp_cp):
    persist_runtime_gap_events(temp_cp, [
        _ev(family_kind="scalar_unit_conversion",
             feature_dict={"input_unit": "credits", "output_unit": "purchases",
                            "rule": "1 credit = 1 purchase"},
             signal_idx=1, risk="high_risk"),
        _ev(family_kind="scalar_unit_conversion",
             feature_dict={"input_unit": "credits", "output_unit": "purchases",
                            "rule": "1 credit = 1 purchase"},
             signal_idx=2, risk="high_risk"),
    ])
    res = run_incremental_gap_replay_once(temp_cp)
    assert res.registered_solver_count == 0
    counts = _runtime_table_counts(temp_cp)
    assert counts["solvers"] == 0


def test_out_of_family_event_does_not_register(temp_cp):
    persist_runtime_gap_events(temp_cp, [
        _ev(family_kind="ml_classifier",
             feature_dict={"model": "resnet50"},
             signal_idx=1),
        _ev(family_kind="ml_classifier",
             feature_dict={"model": "resnet50"},
             signal_idx=2),
    ])
    res = run_incremental_gap_replay_once(temp_cp)
    assert res.registered_solver_count == 0


def test_builder_handoff_event_quarantined(temp_cp):
    persist_runtime_gap_events(temp_cp, [
        _ev(family_kind="builder_handoff",
             feature_dict={"capability_request": "synth"},
             signal_idx=1),
        _ev(family_kind="builder_handoff",
             feature_dict={"capability_request": "synth"},
             signal_idx=2),
    ])
    res = run_incremental_gap_replay_once(temp_cp)
    assert res.registered_solver_count == 0


def test_duplicate_within_run_suppressed(temp_cp):
    # Two clusters with identical features but different cluster_window
    # share candidate_id; the second is DUPLICATE_SUPPRESSED.
    persist_runtime_gap_events(temp_cp, [
        _ev(family_kind="scalar_unit_conversion",
             feature_dict=ORIGINAL_FAMILY_FEATURES["scalar_unit_conversion"],
             signal_idx=1, cluster_window="A"),
        _ev(family_kind="scalar_unit_conversion",
             feature_dict=ORIGINAL_FAMILY_FEATURES["scalar_unit_conversion"],
             signal_idx=2, cluster_window="A"),
        _ev(family_kind="scalar_unit_conversion",
             feature_dict=ORIGINAL_FAMILY_FEATURES["scalar_unit_conversion"],
             signal_idx=3, cluster_window="B"),
        _ev(family_kind="scalar_unit_conversion",
             feature_dict=ORIGINAL_FAMILY_FEATURES["scalar_unit_conversion"],
             signal_idx=4, cluster_window="B"),
    ])
    res = run_incremental_gap_replay_once(temp_cp)
    assert res.registered_solver_count == 1


# ---------------------------------------------------------------------------
# 7. RuntimeGapDetector bridge
# ---------------------------------------------------------------------------

def test_detector_bridge_accepts_compatible_signal():
    s = GapSignal(
        kind="miss", family_kind="scalar_unit_conversion",
        cell_coord=None, payload={
            "feature_dict": ORIGINAL_FAMILY_FEATURES["scalar_unit_conversion"],
        },
    )
    ev = bridge_detector_signal_to_phase18e_event(
        s, raw_query="q", miss_reason="m", confidence_hint=0.9,
        risk_label="low_risk", evidence_ref="audit:test:1",
    )
    assert ev["schema_version"] == SCHEMA_VERSION
    assert ev["family_kind"] == "scalar_unit_conversion"
    assert ev["feature_dict"] == ORIGINAL_FAMILY_FEATURES["scalar_unit_conversion"]


def test_detector_bridge_rejects_no_payload():
    s = GapSignal(
        kind="miss", family_kind="scalar_unit_conversion",
        cell_coord=None, payload=None,
    )
    with pytest.raises(BridgeRejectionError, match="payload"):
        bridge_detector_signal_to_phase18e_event(
            s, raw_query="q", miss_reason="m", confidence_hint=0.9,
            risk_label="low_risk", evidence_ref="audit:test:2",
        )


def test_detector_bridge_rejects_non_mapping_payload():
    s = GapSignal(
        kind="miss", family_kind="scalar_unit_conversion",
        cell_coord=None, payload="not a mapping",  # type: ignore[arg-type]
    )
    with pytest.raises(BridgeRejectionError):
        bridge_detector_signal_to_phase18e_event(
            s, raw_query="q", miss_reason="m", confidence_hint=0.9,
            risk_label="low_risk", evidence_ref="audit:test:3",
        )


def test_detector_bridge_rejects_missing_feature_dict():
    s = GapSignal(
        kind="miss", family_kind="scalar_unit_conversion",
        cell_coord=None, payload={"some_other_field": 42},
    )
    with pytest.raises(BridgeRejectionError, match="feature_dict"):
        bridge_detector_signal_to_phase18e_event(
            s, raw_query="q", miss_reason="m", confidence_hint=0.9,
            risk_label="low_risk", evidence_ref="audit:test:4",
        )


def test_detector_bridge_rejects_empty_family_kind():
    s = GapSignal(
        kind="miss", family_kind="",
        cell_coord=None, payload={"feature_dict": {"a": 1}},
    )
    with pytest.raises(BridgeRejectionError):
        bridge_detector_signal_to_phase18e_event(
            s, raw_query="q", miss_reason="m", confidence_hint=0.9,
            risk_label="low_risk", evidence_ref="audit:test:5",
        )


def test_detector_bridge_rejects_out_of_range_confidence():
    s = GapSignal(
        kind="miss", family_kind="scalar_unit_conversion",
        cell_coord=None, payload={
            "feature_dict": ORIGINAL_FAMILY_FEATURES["scalar_unit_conversion"],
        },
    )
    with pytest.raises(BridgeRejectionError, match="confidence"):
        bridge_detector_signal_to_phase18e_event(
            s, raw_query="q", miss_reason="m", confidence_hint=2.5,
            risk_label="low_risk", evidence_ref="audit:test:6",
        )


def test_persist_detector_signals_round_trips_valid_and_rejects_malformed(
    temp_cp,
):
    valid = GapSignal(
        kind="miss", family_kind="scalar_unit_conversion",
        cell_coord=None, payload={
            "feature_dict": PHASE18F_FAMILY_FEATURES["scalar_unit_conversion"],
        },
    )
    bad = GapSignal(
        kind="miss", family_kind="scalar_unit_conversion",
        cell_coord=None, payload=None,
    )
    kw = dict(raw_query="q", miss_reason="m", confidence_hint=0.9,
                risk_label="low_risk",
                evidence_ref="audit:test:bridge_round_trip")
    res = persist_detector_gap_signals_as_replay_events(
        temp_cp, [(valid, kw), (bad, kw)],
    )
    assert len(res.persisted_event_ids) == 1
    assert res.bridge_rejected_count == 1


# ---------------------------------------------------------------------------
# 8. Concurrency lock
# ---------------------------------------------------------------------------

def test_held_lock_returns_locked_not_run(temp_cp):
    persist_runtime_gap_events(temp_cp, _strong_batch(ORIGINAL_FAMILY_FEATURES))
    # Hold the lock manually.
    temp_cp.set_meta(REPLAY_LOCK_KEY, json.dumps({
        "acquired_at_utc": "2099-01-01T00:00:00Z",  # far future
        "owner": "test:held", "ttl_seconds": 30,
    }))
    res = run_incremental_gap_replay_once(temp_cp)
    assert res.status == "LOCKED_NOT_RUN"
    assert res.cursor_advanced is False
    counts = _runtime_table_counts(temp_cp)
    assert counts["solvers"] == 0  # nothing registered
    temp_cp.delete_meta(REPLAY_LOCK_KEY)


def test_lock_released_after_successful_replay(temp_cp):
    persist_runtime_gap_events(temp_cp, _strong_batch(ORIGINAL_FAMILY_FEATURES))
    res = run_incremental_gap_replay_once(temp_cp)
    assert res.status == "OK"
    # Lock was released; second call should NOT see LOCKED_NOT_RUN.
    res2 = run_incremental_gap_replay_once(temp_cp)
    assert res2.status == "OK"
    assert res2.loaded_event_count == 0


def test_skip_lock_path_works(temp_cp):
    persist_runtime_gap_events(temp_cp, _strong_batch(ORIGINAL_FAMILY_FEATURES))
    res = run_incremental_gap_replay_once(temp_cp, skip_lock=True)
    assert res.status == "OK"
    assert res.cursor_advanced is True


# ---------------------------------------------------------------------------
# 9. Allowlist + delta + storage invariants
# ---------------------------------------------------------------------------

def test_allowlist_unchanged():
    expected = (
        "scalar_unit_conversion", "lookup_table", "threshold_rule",
        "interval_bucket_classifier", "linear_arithmetic",
        "bounded_interpolation",
    )
    assert ALLOWED_FAMILIES == expected


def test_provider_and_builder_delta_zero(temp_cp):
    persist_runtime_gap_events(temp_cp, _strong_batch(ORIGINAL_FAMILY_FEATURES))
    run_incremental_gap_replay_once(temp_cp)
    with temp_cp._lock:  # noqa: SLF001
        provider_total = int(temp_cp._conn.execute(
            "SELECT COUNT(*) AS c FROM provider_jobs",
        ).fetchone()["c"])
        builder_total = int(temp_cp._conn.execute(
            "SELECT COUNT(*) AS c FROM builder_jobs",
        ).fetchone()["c"])
    assert provider_total == 0
    assert builder_total == 0


def test_no_parallel_event_table_runtime_gap_signals_only(temp_cp):
    persist_runtime_gap_events(temp_cp, _strong_batch(ORIGINAL_FAMILY_FEATURES))
    # All Phase 18F events live under kind = phase18e.runtime_gap_event.v1
    # in the existing runtime_gap_signals table.
    cnt = temp_cp.count_runtime_gap_signals(
        kind=PHASE18E_RUNTIME_GAP_EVENT_KIND,
    )
    assert cnt == 12
    # Phase 18F does not apply a private migration: the instance remains at
    # the repository's canonical control-plane schema version.
    assert temp_cp.schema_version() == CONTROL_PLANE_SCHEMA_VERSION


def test_schema_meta_holds_cursor_only(temp_cp):
    write_replay_cursor(temp_cp, last_processed_id=99)
    raw = temp_cp.get_meta(REPLAY_CURSOR_KEY)
    assert raw is not None
    payload = json.loads(raw)
    # cursor payload is small + minimal; no event data leaks here.
    assert set(payload.keys()) == {"last_processed_id", "advanced_at_utc"}


# ---------------------------------------------------------------------------
# 10. Carry-forward import smokes
# ---------------------------------------------------------------------------

def test_phase18a_validator_module_importable():
    import importlib
    mod = importlib.import_module(
        "tools.validate_phase18a_benchmark_bundle",
    )
    assert hasattr(mod, "main") or hasattr(mod, "validate_bundle_dir")


def test_phase18b_proof_module_importable():
    import importlib
    mod = importlib.import_module(
        "tools.run_phase18b_gap_miner_feedback_proof",
    )
    assert hasattr(mod, "build_synthetic_fixture")


def test_phase18c_proof_module_importable():
    import importlib
    mod = importlib.import_module(
        "tools.run_phase18c_mined_solver_runtime_dispatch_proof",
    )
    assert hasattr(mod, "build_proof")


def test_phase18e_proof_module_importable():
    import importlib
    mod = importlib.import_module(
        "tools.run_phase18e_runtime_gap_replay_proof",
    )
    assert hasattr(mod, "build_proof")


# ---------------------------------------------------------------------------
# 11. End-to-end harness gate
# ---------------------------------------------------------------------------

def test_proof_harness_release_gate_pass(tmp_path: Path):
    import importlib
    proof_mod = importlib.import_module(
        "tools.run_phase18f_incremental_gap_replay_proof",
    )
    proof = proof_mod.build_proof(out_dir=tmp_path)
    assert proof["release_gate_pass"] is True
    assert proof["forbidden_claims_absent"] is True
    assert proof["first_replay_registered_solver_count"] >= 6
    assert proof["first_replay_families_covered"] == 6
    assert proof["third_replay_registered_solver_count"] >= 6
    assert proof["third_replay_families_covered"] == 6
    assert proof["total_registered_solver_count"] >= 12
    assert proof["no_op_idempotency_pass"] is True
    assert proof["lock_result"] == "LOCKED_NOT_RUN"
    assert proof["concurrent_replay_safety_pass"] is True
    assert proof["builder_handoff_executable_count"] == 0
    assert proof["high_risk_executable_count"] == 0
    assert proof["event_table_reused"] == "runtime_gap_signals"
    assert proof["no_parallel_event_table"] is True


# ---------------------------------------------------------------------------
# 12. Hygiene
# ---------------------------------------------------------------------------

def test_no_db_file_under_autonomy_growth_source():
    src = ROOT / "waggledance" / "core" / "autonomy_growth"
    bad = (
        list(src.rglob("*.db"))
        + list(src.rglob("*.sqlite"))
        + list(src.rglob("*.sqlite3"))
        + list(src.rglob("*.wal"))
        + list(src.rglob("*.shm"))
    )
    assert bad == [], f"Unexpected DB-shaped files: {bad}"
