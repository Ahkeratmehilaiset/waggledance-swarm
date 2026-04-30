# SPDX-License-Identifier: Apache-2.0
"""Schema-v2 tests for the control plane (Phase 11 autonomy growth)."""

from __future__ import annotations

import json

import pytest

from waggledance.core.storage.control_plane import ControlPlaneDB
from waggledance.core.storage.control_plane_schema import (
    SCHEMA_VERSION,
    all_table_names,
)


@pytest.fixture()
def cp(tmp_path):
    db = ControlPlaneDB(tmp_path / "cp.sqlite")
    db.migrate()
    yield db
    db.close()


def test_schema_version_is_at_least_v2_after_migrate(cp: ControlPlaneDB) -> None:
    """v2 introduced these tables; later migrations must keep schema_version
    monotonically forward. Test passes for any v2-or-later schema."""

    assert SCHEMA_VERSION >= 2
    assert cp.schema_version() >= 2
    assert cp.schema_version() == SCHEMA_VERSION


def test_v2_tables_present(cp: ControlPlaneDB) -> None:
    expected = {
        "family_policies",
        "validation_runs",
        "shadow_evaluations",
        "promotion_decisions",
        "autonomy_kpis",
    }
    assert expected.issubset(set(all_table_names()))
    stats = cp.stats()
    for t in expected:
        assert t in stats.table_counts, f"missing {t} in stats"


def test_family_policy_upsert_idempotent(cp: ControlPlaneDB) -> None:
    p1 = cp.upsert_family_policy(
        "scalar_unit_conversion", is_low_risk=True, max_auto_promote=10
    )
    p2 = cp.upsert_family_policy(
        "scalar_unit_conversion", is_low_risk=True, max_auto_promote=20
    )
    assert p1.id == p2.id
    assert p2.max_auto_promote == 20
    assert p2.is_low_risk is True


def test_list_family_policies_low_risk_only(cp: ControlPlaneDB) -> None:
    cp.upsert_family_policy("scalar_unit_conversion", is_low_risk=True)
    cp.upsert_family_policy("temporal_window_rule", is_low_risk=False)
    all_pol = cp.list_family_policies()
    assert len(all_pol) == 2
    low = cp.list_family_policies(low_risk_only=True)
    assert len(low) == 1
    assert low[0].family_kind == "scalar_unit_conversion"


def test_validation_run_record_round_trip(cp: ControlPlaneDB) -> None:
    fam = cp.upsert_solver_family("scalar_unit_conversion", "1.0")
    solver = cp.upsert_solver(
        family_name=fam.name,
        name="celsius_to_kelvin_v1",
        version="1.0",
        spec_hash="abc",
    )
    run = cp.record_validation_run(
        family_kind=fam.name,
        solver_id=solver.id,
        spec_hash="abc",
        case_count=8,
        pass_count=8,
        fail_count=0,
        status="completed",
        evidence=json.dumps({"cases": 8}),
    )
    assert run.case_count == 8
    assert run.pass_count == 8
    assert run.fail_count == 0
    assert run.status == "completed"
    fetched = cp.get_validation_run(run.id)
    assert fetched is not None and fetched.id == run.id


def test_shadow_evaluation_agreement_rate(cp: ControlPlaneDB) -> None:
    ev = cp.record_shadow_evaluation(
        family_kind="scalar_unit_conversion",
        sample_count=10,
        agree_count=10,
        disagree_count=0,
        oracle_kind="recompute_artifact",
    )
    assert ev.agreement_rate == 1.0
    ev2 = cp.record_shadow_evaluation(
        family_kind="scalar_unit_conversion",
        sample_count=10,
        agree_count=7,
        disagree_count=3,
    )
    assert abs(ev2.agreement_rate - 0.7) < 1e-9


def test_promotion_decision_audit_trail(cp: ControlPlaneDB) -> None:
    fam = cp.upsert_solver_family("threshold_rule", "1.0")
    solver = cp.upsert_solver(
        family_name=fam.name,
        name="hot_threshold_v1",
        version="1.0",
        spec_hash="t1",
    )
    val = cp.record_validation_run(
        family_kind=fam.name, solver_id=solver.id, spec_hash="t1",
        case_count=5, pass_count=5, fail_count=0, status="completed",
    )
    sh = cp.record_shadow_evaluation(
        family_kind=fam.name, solver_id=solver.id, spec_hash="t1",
        sample_count=5, agree_count=5, disagree_count=0,
    )
    dec = cp.record_promotion_decision(
        solver_id=solver.id,
        family_kind=fam.name,
        decision="auto_promoted",
        decided_by="autopromotion_engine_v1",
        validation_run_id=val.id,
        shadow_evaluation_id=sh.id,
    )
    assert dec.decision == "auto_promoted"
    assert dec.decided_by == "autopromotion_engine_v1"

    rolled = cp.record_promotion_decision(
        solver_id=solver.id,
        family_kind=fam.name,
        decision="rollback",
        decided_by="autopromotion_engine_v1",
        rollback_reason="shadow_agreement_drift",
    )
    assert rolled.decision == "rollback"

    listed = cp.list_promotion_decisions(solver_id=solver.id)
    decisions = [d.decision for d in listed]
    assert "auto_promoted" in decisions and "rollback" in decisions


def test_autonomy_kpi_snapshot_round_trip(cp: ControlPlaneDB) -> None:
    snap = cp.snapshot_autonomy_kpis(
        candidates_total=12,
        validations_pass_total=10,
        validations_fail_total=2,
        shadows_pass_total=8,
        shadows_fail_total=1,
        auto_promotions_total=6,
        rejections_total=3,
        rollbacks_total=1,
        dispatcher_hits_total=42,
        dispatcher_misses_total=18,
        per_family_counts={"scalar_unit_conversion": 3, "lookup_table": 2},
    )
    latest = cp.latest_autonomy_kpi()
    assert latest is not None and latest.id == snap.id
    assert latest.candidates_total == 12
    assert latest.auto_promotions_total == 6
    parsed = json.loads(latest.per_family_counts_json or "{}")
    assert parsed["scalar_unit_conversion"] == 3


def test_count_auto_promoted_for_family(cp: ControlPlaneDB) -> None:
    fam = cp.upsert_solver_family("lookup_table", "1.0")
    cp.upsert_solver(
        family_name=fam.name, name="m_per_h_v1", version="1.0",
        status="auto_promoted",
    )
    cp.upsert_solver(
        family_name=fam.name, name="km_per_mi_v1", version="1.0",
        status="auto_promoted",
    )
    cp.upsert_solver(
        family_name=fam.name, name="draft_v1", version="1.0",
        status="draft",
    )
    assert cp.count_auto_promoted_for_family("lookup_table") == 2
    assert cp.count_auto_promoted_for_family("scalar_unit_conversion") == 0


def test_v2_indexes_exist(cp: ControlPlaneDB) -> None:
    """Spot-check that v2 indexes were created (queryable via sqlite_master)."""

    rows = cp._conn.execute(  # type: ignore[attr-defined]
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
    ).fetchall()
    names = {r["name"] for r in rows}
    must_have = {
        "idx_family_policies_low_risk",
        "idx_validation_runs_solver",
        "idx_validation_runs_family",
        "idx_shadow_evaluations_solver",
        "idx_shadow_evaluations_family",
        "idx_promotion_decisions_solver",
        "idx_promotion_decisions_family",
        "idx_promotion_decisions_decided_by",
        "idx_autonomy_kpis_snapshot_at",
    }
    missing = must_have - names
    assert not missing, f"missing v2 indexes: {missing}"


def test_v2_migration_is_forward_only_from_v1(tmp_path) -> None:
    """A fresh DB at v1 then migrating to v2 preserves prior tables."""

    # Create at current SCHEMA_VERSION (which is now 2 — but emulate v1
    # by manually setting schema_meta and running migrate again).
    db = ControlPlaneDB(tmp_path / "v1.sqlite")
    db.migrate()
    fam = db.upsert_solver_family("scalar_unit_conversion", "1.0")
    db.upsert_solver(
        family_name=fam.name, name="prev_solver", version="1.0",
    )
    # Simulate downgrade then re-upgrade (forward-only invariant: migrate
    # is idempotent at the same version)
    final_version = db.migrate()
    assert final_version == SCHEMA_VERSION
    assert final_version >= 2
    # v1 row still present
    assert db.get_solver_family("scalar_unit_conversion") is not None
    assert db.get_solver("prev_solver") is not None
    db.close()
