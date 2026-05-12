# SPDX-License-Identifier: BUSL-1.1
"""Phase 18E - persisted runtime gap replay tests.

Covers the full durable loop:

    persist_runtime_gap_events
      -> runtime_gap_signals (kind = phase18e.runtime_gap_event.v1)
      -> load_runtime_gap_events
      -> mine_runtime_gaps     (Phase 18B verbatim)
      -> register_mined_solver_specs   (Phase 18C verbatim)
      -> LowRiskSolverDispatcher.dispatch_by_features  (real path)

Plus all fail-closed paths (forbidden field, missing field,
unsupported schema_version, malformed JSON, non-allowlisted, high-risk,
builder-handoff, duplicate) and idempotency.
"""

from __future__ import annotations

import json
import math
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.autonomy_growth.gap_candidate import (  # noqa: E402
    GapVerdict,
)
from waggledance.core.autonomy_growth.gap_mining import (  # noqa: E402
    ALLOWED_FAMILIES,
    GapMiningConfig,
    mine_runtime_gaps,
)
from waggledance.core.autonomy_growth.runtime_gap_replay import (  # noqa: E402
    PHASE18E_RUNTIME_GAP_EVENT_KIND,
    REQUIRED_FIELDS,
    SCHEMA_VERSION,
    GapEventSchemaError,
    PersistedGapEvent,
    load_runtime_gap_events,
    normalize_runtime_gap_event,
    persist_runtime_gap_events,
    replay_persisted_gap_events,
)
from waggledance.core.autonomy_growth.solver_dispatcher import (  # noqa: E402
    LowRiskSolverDispatcher,
)
from waggledance.core.storage.control_plane import (  # noqa: E402
    ControlPlaneDB,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FAMILY_FEATURES: dict[str, dict[str, Any]] = {
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


def _ev(*, family_kind: str, feature_dict: dict[str, Any],
         signal_idx: int = 1, confidence: float = 0.9,
         risk: str = "low_risk", cluster_window: str = "",
         evidence_ref: str | None = None,
         miss_reason: str = "capability_lookup_miss",
         raw_query: str = "missed runtime query") -> dict[str, Any]:
    if evidence_ref is None:
        evidence_ref = f"audit:phase18e:test:{signal_idx:04d}"
    return {
        "schema_version": SCHEMA_VERSION,
        "occurred_at_utc": "2026-05-06T00:00:00Z",
        "source": "phase18e_test",
        "family_kind": family_kind,
        "feature_dict": feature_dict,
        "raw_query": raw_query,
        "miss_reason": miss_reason,
        "confidence_hint": confidence,
        "risk_label": risk,
        "evidence_ref": evidence_ref,
        "cluster_window": cluster_window,
        "signal_id": f"phase18e_signal_{signal_idx:04d}",
    }


@pytest.fixture
def temp_cp(tmp_path: Path) -> ControlPlaneDB:
    cp_path = tmp_path / "phase18e_test_control_plane.db"
    cp = ControlPlaneDB(cp_path)
    yield cp
    cp.close()


# ---------------------------------------------------------------------------
# 1. Normalization happy paths
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("family", ALLOWED_FAMILIES)
def test_normalize_happy_each_family(family):
    raw = _ev(family_kind=family, feature_dict=FAMILY_FEATURES[family])
    ev = normalize_runtime_gap_event(raw)
    assert isinstance(ev, PersistedGapEvent)
    assert ev.schema_version == SCHEMA_VERSION
    assert ev.family_kind == family
    assert ev.feature_dict == FAMILY_FEATURES[family]
    assert len(ev.event_id) == 16
    assert len(ev.provenance_hash) == 64


def test_normalize_event_id_deterministic_across_calls():
    raw = _ev(
        family_kind="scalar_unit_conversion",
        feature_dict=FAMILY_FEATURES["scalar_unit_conversion"],
    )
    ev1 = normalize_runtime_gap_event(raw)
    ev2 = normalize_runtime_gap_event(raw)
    assert ev1.event_id == ev2.event_id
    assert ev1.provenance_hash == ev2.provenance_hash


def test_normalize_event_id_changes_with_cluster_window():
    base = _ev(
        family_kind="scalar_unit_conversion",
        feature_dict=FAMILY_FEATURES["scalar_unit_conversion"],
    )
    other = dict(base)
    other["cluster_window"] = "window_xyz"
    ev1 = normalize_runtime_gap_event(base)
    ev2 = normalize_runtime_gap_event(other)
    assert ev1.event_id != ev2.event_id


# ---------------------------------------------------------------------------
# 2. Normalization fail-closed paths
# ---------------------------------------------------------------------------

def test_normalize_unsupported_schema_version():
    raw = _ev(
        family_kind="scalar_unit_conversion",
        feature_dict=FAMILY_FEATURES["scalar_unit_conversion"],
    )
    raw["schema_version"] = "phase17.legacy.v0"
    with pytest.raises(GapEventSchemaError, match="schema_version"):
        normalize_runtime_gap_event(raw)


@pytest.mark.parametrize("missing", REQUIRED_FIELDS)
def test_normalize_missing_required_field(missing):
    raw = _ev(
        family_kind="scalar_unit_conversion",
        feature_dict=FAMILY_FEATURES["scalar_unit_conversion"],
    )
    raw.pop(missing)
    with pytest.raises(GapEventSchemaError):
        normalize_runtime_gap_event(raw)


def test_normalize_feature_dict_must_be_mapping():
    raw = _ev(
        family_kind="scalar_unit_conversion",
        feature_dict={"input_unit": "km", "output_unit": "miles",
                       "rule": "1 km = 0.621371 miles"},
    )
    raw["feature_dict"] = ["not", "a", "mapping"]
    with pytest.raises(GapEventSchemaError, match="feature_dict"):
        normalize_runtime_gap_event(raw)


def test_normalize_confidence_out_of_range():
    raw = _ev(
        family_kind="scalar_unit_conversion",
        feature_dict=FAMILY_FEATURES["scalar_unit_conversion"],
        confidence=1.5,
    )
    with pytest.raises(GapEventSchemaError, match="confidence"):
        normalize_runtime_gap_event(raw)


def test_normalize_forbidden_key_token():
    raw = _ev(
        family_kind="scalar_unit_conversion",
        feature_dict=FAMILY_FEATURES["scalar_unit_conversion"],
    )
    raw["github_token"] = "PLACEHOLDER_NOT_A_REAL_TOKEN"
    with pytest.raises(GapEventSchemaError, match="forbidden"):
        normalize_runtime_gap_event(raw)


def test_normalize_forbidden_key_password():
    raw = _ev(
        family_kind="scalar_unit_conversion",
        feature_dict=FAMILY_FEATURES["scalar_unit_conversion"],
    )
    raw["password"] = "anything"
    with pytest.raises(GapEventSchemaError, match="forbidden"):
        normalize_runtime_gap_event(raw)


def test_normalize_raw_must_be_mapping():
    with pytest.raises(GapEventSchemaError, match="mapping"):
        normalize_runtime_gap_event("not a mapping")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 3. Persistence
# ---------------------------------------------------------------------------

def test_persist_inserts_each_event(temp_cp):
    events = [
        _ev(family_kind=fam, feature_dict=FAMILY_FEATURES[fam],
            signal_idx=i)
        for i, fam in enumerate(ALLOWED_FAMILIES, start=1)
    ]
    res = persist_runtime_gap_events(temp_cp, events)
    assert len(res.inserted_event_ids) == len(events)
    assert res.skipped_existing_event_ids == ()
    assert res.rejected_event_count == 0


def test_persist_idempotent_on_repeat(temp_cp):
    events = [_ev(family_kind="scalar_unit_conversion",
                    feature_dict=FAMILY_FEATURES["scalar_unit_conversion"],
                    signal_idx=1)]
    first = persist_runtime_gap_events(temp_cp, events)
    second = persist_runtime_gap_events(temp_cp, events)
    assert len(first.inserted_event_ids) == 1
    assert second.inserted_event_ids == ()
    assert len(second.skipped_existing_event_ids) == 1
    assert (
        first.inserted_event_ids[0]
        == second.skipped_existing_event_ids[0]
    )


def test_persist_uses_runtime_gap_signal_batch(tmp_path: Path):
    class BatchTrackingControlPlane(ControlPlaneDB):
        def __init__(self, db_path: Path) -> None:
            super().__init__(db_path)
            self.batch_sizes: list[int] = []
            self.single_calls = 0

        def record_runtime_gap_signal(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            self.single_calls += 1
            return super().record_runtime_gap_signal(*args, **kwargs)

        def record_runtime_gap_signal_many(self, signals):  # type: ignore[no-untyped-def]
            materialized = list(signals)
            self.batch_sizes.append(len(materialized))
            return super().record_runtime_gap_signal_many(materialized)

    cp = BatchTrackingControlPlane(tmp_path / "phase18e_batch_control_plane.db")
    try:
        res = persist_runtime_gap_events(
            cp,
            [
                _ev(
                    family_kind="lookup_table",
                    feature_dict=FAMILY_FEATURES["lookup_table"],
                    signal_idx=1,
                ),
                _ev(
                    family_kind="threshold_rule",
                    feature_dict=FAMILY_FEATURES["threshold_rule"],
                    signal_idx=2,
                ),
            ],
        )

        assert len(res.inserted_event_ids) == 2
        assert cp.batch_sizes == [2]
        assert cp.single_calls == 0
    finally:
        cp.close()


def test_persist_rejects_malformed(temp_cp):
    bad = [
        # missing field
        {"schema_version": SCHEMA_VERSION,
          "occurred_at_utc": "2026-05-06T00:00:00Z",
          "source": "x", "family_kind": "scalar_unit_conversion",
          # feature_dict missing
          "raw_query": "x", "miss_reason": "x",
          "confidence_hint": 0.5, "risk_label": "low_risk",
          "evidence_ref": "x", "cluster_window": ""},
        # bad schema_version
        {"schema_version": "v0",
          "occurred_at_utc": "2026-05-06T00:00:00Z",
          "source": "x", "family_kind": "scalar_unit_conversion",
          "feature_dict": {}, "raw_query": "x", "miss_reason": "x",
          "confidence_hint": 0.5, "risk_label": "low_risk",
          "evidence_ref": "x", "cluster_window": ""},
    ]
    res = persist_runtime_gap_events(temp_cp, bad)
    assert res.inserted_event_ids == ()
    assert res.malformed_event_rejection_count == 2
    assert res.forbidden_field_rejections == 0


def test_persist_rejects_forbidden_field(temp_cp):
    raw = _ev(
        family_kind="scalar_unit_conversion",
        feature_dict=FAMILY_FEATURES["scalar_unit_conversion"],
    )
    raw["api_key"] = "anything"
    res = persist_runtime_gap_events(temp_cp, [raw])
    assert res.inserted_event_ids == ()
    assert res.forbidden_field_rejections == 1
    assert res.malformed_event_rejection_count == 0


# ---------------------------------------------------------------------------
# 4. Load
# ---------------------------------------------------------------------------

def test_load_returns_only_phase18e_kind(temp_cp):
    # Insert a non-phase18e Phase 12 detector signal first.
    temp_cp.record_runtime_gap_signal(
        kind="runtime_miss",
        family_kind="scalar_unit_conversion",
        signal_payload=json.dumps({"legacy": True}),
    )
    persist_runtime_gap_events(temp_cp, [
        _ev(family_kind="scalar_unit_conversion",
            feature_dict=FAMILY_FEATURES["scalar_unit_conversion"],
            signal_idx=1),
    ])
    loaded = load_runtime_gap_events(temp_cp)
    assert len(loaded) == 1
    assert loaded[0].schema_version == SCHEMA_VERSION
    # Phase 12 row must still be present untouched.
    legacy = temp_cp.list_runtime_gap_signals(kind="runtime_miss")
    assert len(legacy) == 1


def test_load_round_trips_canonical_shape(temp_cp):
    raw = _ev(family_kind="lookup_table",
               feature_dict=FAMILY_FEATURES["lookup_table"], signal_idx=1)
    persist_runtime_gap_events(temp_cp, [raw])
    loaded = load_runtime_gap_events(temp_cp)
    assert len(loaded) == 1
    ev = loaded[0]
    assert ev.family_kind == "lookup_table"
    assert ev.feature_dict == FAMILY_FEATURES["lookup_table"]
    assert ev.confidence_hint == raw["confidence_hint"]


def test_load_filters_by_source(temp_cp):
    raw_a = _ev(family_kind="scalar_unit_conversion",
                  feature_dict=FAMILY_FEATURES["scalar_unit_conversion"],
                  signal_idx=1)
    raw_a["source"] = "source_A"
    raw_b = _ev(family_kind="lookup_table",
                  feature_dict=FAMILY_FEATURES["lookup_table"],
                  signal_idx=2)
    raw_b["source"] = "source_B"
    persist_runtime_gap_events(temp_cp, [raw_a, raw_b])
    a_only = load_runtime_gap_events(temp_cp, source="source_A")
    assert len(a_only) == 1
    assert a_only[0].source == "source_A"


# ---------------------------------------------------------------------------
# 5. Replay -> Phase 18B mining
# ---------------------------------------------------------------------------

def _build_full_fixture() -> list[dict[str, Any]]:
    """Smaller variant of the proof harness fixture: 2 strong signals
    per family + the explicit non-ALLOWLISTED categories. Yields 6
    ALLOWLISTED candidates, exercises every verdict bucket."""
    out: list[dict[str, Any]] = []
    idx = 0
    for fam in ALLOWED_FAMILIES:
        feats = FAMILY_FEATURES[fam]
        for sub in range(2):
            idx += 1
            out.append(_ev(family_kind=fam, feature_dict=feats,
                             signal_idx=idx,
                             confidence=0.85 + 0.01 * sub))
    # OUT_OF_FAMILY
    idx += 1
    out.append(_ev(family_kind="ml_classifier",
                     feature_dict={"model": "resnet50"},
                     signal_idx=idx, confidence=0.9))
    # HIGH_RISK
    idx += 1
    out.append(_ev(family_kind="scalar_unit_conversion",
                     feature_dict={"input_unit": "credits",
                                    "output_unit": "purchases",
                                    "rule": "1 credit = 1 purchase"},
                     signal_idx=idx, confidence=0.9, risk="high_risk"))
    # BUILDER_HANDOFF
    idx += 1
    out.append(_ev(family_kind="builder_handoff",
                     feature_dict={"capability_request": "synthesize"},
                     signal_idx=idx, confidence=0.9))
    # INSUFFICIENT (single signal, low confidence, distinct features)
    idx += 1
    out.append(_ev(family_kind="threshold_rule",
                     feature_dict={"threshold": 100, "example_value": 50,
                                    "rule": "alert"},
                     signal_idx=idx, confidence=0.40))
    return out


def test_replay_calls_mine_runtime_gaps_path(temp_cp, monkeypatch):
    """Smoke: replay path goes through the real Phase 18B mine_runtime_gaps."""
    persist_runtime_gap_events(temp_cp, _build_full_fixture())
    seen = {"called": False}

    real_mine = mine_runtime_gaps

    def spy(*args, **kwargs):
        seen["called"] = True
        return real_mine(*args, **kwargs)

    monkeypatch.setattr(
        "waggledance.core.autonomy_growth.runtime_gap_replay.mine_runtime_gaps",
        spy,
    )
    replay_persisted_gap_events(temp_cp)
    assert seen["called"] is True


def test_replay_six_families_register(temp_cp):
    persist_runtime_gap_events(temp_cp, _build_full_fixture())
    res = replay_persisted_gap_events(temp_cp)
    assert res.registration_summary.registered_count >= 6
    assert (
        res.mining_result.counters.get(
            GapVerdict.ALLOWLISTED_SOLVER_SPEC.value, 0,
        )
        >= 6
    )


def test_replay_out_of_family_does_not_register(temp_cp):
    persist_runtime_gap_events(temp_cp, _build_full_fixture())
    res = replay_persisted_gap_events(temp_cp)
    families_registered = {
        cid for cid in res.registration_summary.registered_candidate_ids
    }
    # Convert via candidate list to inspect family_kind for each
    # registered id.
    cand_by_id = {c.candidate_id: c for c in res.mining_result.candidates}
    registered_families = {
        cand_by_id[cid].family_kind for cid in families_registered
    }
    assert "ml_classifier" not in registered_families


def test_replay_high_risk_does_not_register(temp_cp):
    persist_runtime_gap_events(temp_cp, _build_full_fixture())
    res = replay_persisted_gap_events(temp_cp)
    high_risk_count = res.mining_result.counters.get(
        GapVerdict.HIGH_RISK_REJECTED.value, 0,
    )
    assert high_risk_count >= 1
    # high-risk candidates do not contribute to registered_candidate_ids
    cand_by_id = {c.candidate_id: c for c in res.mining_result.candidates}
    for cid in res.registration_summary.registered_candidate_ids:
        assert cand_by_id[cid].risk_label != "high_risk"


def test_replay_builder_handoff_quarantined(temp_cp):
    persist_runtime_gap_events(temp_cp, _build_full_fixture())
    res = replay_persisted_gap_events(temp_cp)
    bh_count = res.mining_result.counters.get(
        GapVerdict.BUILDER_HANDOFF_QUARANTINED.value, 0,
    )
    assert bh_count >= 1
    # builder_handoff candidates do not register
    cand_by_id = {c.candidate_id: c for c in res.mining_result.candidates}
    for cid in res.registration_summary.registered_candidate_ids:
        assert cand_by_id[cid].family_kind != "builder_handoff"


# ---------------------------------------------------------------------------
# 6. Real LowRiskSolverDispatcher dispatch via replay
# ---------------------------------------------------------------------------

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


def test_replay_then_dispatch_six_families_hit(temp_cp):
    persist_runtime_gap_events(temp_cp, _build_full_fixture())
    res = replay_persisted_gap_events(temp_cp)
    assert res.registration_summary.registered_count >= 6

    dispatcher = LowRiskSolverDispatcher(temp_cp)
    cases = [
        ("scalar_unit_conversion",
          FAMILY_FEATURES["scalar_unit_conversion"],
          {"x": 10.0}, 6.21371),
        ("lookup_table",
          FAMILY_FEATURES["lookup_table"],
          {"key": "tin"}, "Sn"),
        ("threshold_rule",
          FAMILY_FEATURES["threshold_rule"],
          {"x": 37}, "above"),
        ("interval_bucket_classifier",
          FAMILY_FEATURES["interval_bucket_classifier"],
          {"x": 17}, "[10,20)"),
        ("linear_arithmetic",
          FAMILY_FEATURES["linear_arithmetic"],
          {"a": 5.0, "b": 7.0}, 12.0),
        ("bounded_interpolation",
          FAMILY_FEATURES["bounded_interpolation"],
          {"x": 3}, 30.0),
    ]
    for fam, feats, inputs, expected in cases:
        r = dispatcher.dispatch_by_features(
            family_kind=fam, features=_stringify(feats),
            inputs=inputs,
        )
        assert r.matched is True
        assert r.reason in ("hit", "hit_by_features")
        if isinstance(expected, float):
            assert math.isclose(
                float(r.output), expected, rel_tol=1e-9, abs_tol=1e-9,
            )
        else:
            assert r.output == expected


# ---------------------------------------------------------------------------
# 7. Idempotency
# ---------------------------------------------------------------------------

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


def test_replay_idempotent_no_extra_rows(temp_cp):
    fixture = _build_full_fixture()
    persist_runtime_gap_events(temp_cp, fixture)
    replay_persisted_gap_events(temp_cp)
    counts1 = _runtime_table_counts(temp_cp)

    # Persist again -> all skipped.
    persist2 = persist_runtime_gap_events(temp_cp, fixture)
    assert persist2.inserted_event_ids == ()

    # Replay again -> upsert_* idempotent.
    replay_persisted_gap_events(temp_cp)
    counts2 = _runtime_table_counts(temp_cp)
    assert counts1 == counts2


# ---------------------------------------------------------------------------
# 8. Carry-forward smokes
# ---------------------------------------------------------------------------

def test_phase18a_validator_module_importable():
    # If the Phase 18A validator module fails to import, the carry-forward
    # gate is broken.
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


# ---------------------------------------------------------------------------
# 9. Allowlist + delta invariants
# ---------------------------------------------------------------------------

def test_allowlist_unchanged():
    expected = (
        "scalar_unit_conversion", "lookup_table", "threshold_rule",
        "interval_bucket_classifier", "linear_arithmetic",
        "bounded_interpolation",
    )
    assert ALLOWED_FAMILIES == expected


def test_provider_and_builder_delta_zero_after_replay(temp_cp):
    persist_runtime_gap_events(temp_cp, _build_full_fixture())
    replay_persisted_gap_events(temp_cp)
    # Phase 18C registration only writes solver / family / capability /
    # artifact rows. provider_jobs and builder_jobs are untouched.
    with temp_cp._lock:  # noqa: SLF001
        provider_total = int(temp_cp._conn.execute(
            "SELECT COUNT(*) AS c FROM provider_jobs",
        ).fetchone()["c"])
        builder_total = int(temp_cp._conn.execute(
            "SELECT COUNT(*) AS c FROM builder_jobs",
        ).fetchone()["c"])
    assert provider_total == 0
    assert builder_total == 0


# ---------------------------------------------------------------------------
# 10. Schema unchanged
# ---------------------------------------------------------------------------

def test_schema_v3_table_runtime_gap_signals_present(temp_cp):
    # Simply attempting to insert via the existing API succeeds, which
    # implies the v3 table is present and unchanged.
    rec = temp_cp.record_runtime_gap_signal(
        kind="probe",
        family_kind="scalar_unit_conversion",
    )
    assert rec.id > 0


def test_phase18e_kind_round_trip(temp_cp):
    raw = _ev(family_kind="lookup_table",
               feature_dict=FAMILY_FEATURES["lookup_table"], signal_idx=1)
    persist_runtime_gap_events(temp_cp, [raw])
    rows = temp_cp.list_runtime_gap_signals(
        kind=PHASE18E_RUNTIME_GAP_EVENT_KIND,
    )
    assert len(rows) == 1
    assert rows[0].kind == PHASE18E_RUNTIME_GAP_EVENT_KIND


# ---------------------------------------------------------------------------
# 11. Proof harness end-to-end
# ---------------------------------------------------------------------------

def test_proof_harness_release_gate_pass(tmp_path: Path):
    import importlib
    proof_mod = importlib.import_module(
        "tools.run_phase18e_runtime_gap_replay_proof",
    )
    proof = proof_mod.build_proof(out_dir=tmp_path)
    assert proof["release_gate_pass"] is True
    assert proof["forbidden_claims_absent"] is True
    assert proof["registered_solver_count"] >= 6
    assert proof["families_covered"] == 6
    assert proof["dispatch_case_count"] >= 18
    assert proof["dispatch_failure_count"] == 0
    assert proof["replay_idempotency_pass"] is True
    assert proof["provider_jobs_delta"] == 0
    assert proof["builder_jobs_delta"] == 0


# ---------------------------------------------------------------------------
# 12. DB hygiene
# ---------------------------------------------------------------------------

def test_no_db_file_committed_under_repo(tmp_path):
    # Sanity: the proof harness writes its DB under tempdir, not under
    # the repo. We check there are no .db / .sqlite / .wal / .shm files
    # under the autonomy_growth source tree.
    src = ROOT / "waggledance" / "core" / "autonomy_growth"
    bad = list(src.rglob("*.db")) + list(src.rglob("*.sqlite")) + \
          list(src.rglob("*.sqlite3")) + list(src.rglob("*.wal")) + \
          list(src.rglob("*.shm"))
    assert bad == [], f"Unexpected DB-shaped files: {bad}"
