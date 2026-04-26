"""Targeted tests for Phase 9 §U3 Autonomous Solver Synthesis +
Cold/Shadow Throttling."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.solver_synthesis import (
    U1_HIGH_CONFIDENCE_THRESHOLD,
    U1_LOW_CONFIDENCE_THRESHOLD,
    gap_to_solver_spec as gts,
    solver_candidate_store as scs,
    solver_quarantine as sq,
    validators as sv,
)


# ═══════════════════ schema enums ══════════════════════════════════

def test_candidate_states_match_schema():
    schema = json.loads((ROOT / "schemas" / "solver_candidate.schema.json")
                          .read_text(encoding="utf-8"))
    assert tuple(schema["properties"]["state"]["enum"]) == \
        scs.CANDIDATE_STATES


def test_validation_verdicts_match_schema():
    schema = json.loads((ROOT / "schemas"
                            / "solver_validation_report.schema.json")
                          .read_text(encoding="utf-8"))
    assert tuple(schema["properties"]["verdict"]["enum"]) == sv.VERDICTS


# ═══════════════════ gap_to_solver_spec routing ═══════════════════

def test_gap_routing_no_hint_goes_to_u3():
    decision = gts.route_gap(gap_ref="ten_001")
    assert decision.chosen_path == "U3_residual"
    assert decision.best_match is None


def test_gap_routing_high_confidence_picks_u1():
    table = {"intervals": [
        {"min": 0, "max": 10, "label": "low"},
        {"min": 10, "max": 20, "label": "high"},
    ]}
    decision = gts.route_gap(gap_ref="ten_002", table_hint=table)
    assert decision.chosen_path == "U1_compile"


def test_gap_routing_low_confidence_picks_u3():
    table = {"unknown_key": 42}
    decision = gts.route_gap(gap_ref="ten_003", table_hint=table)
    assert decision.chosen_path == "U3_residual"


# ═══════════════════ solver_candidate_store ═══════════════════════

def test_make_candidate_starts_in_raw_candidate():
    c = scs.make_candidate(
        solver_name="u3_x", cell_id="general",
        spec_or_code={"k": "v"}, source_gap_ref="ten_001",
    )
    assert c.state == "raw_candidate"
    assert c.no_runtime_mutation is True


def test_candidate_id_deterministic():
    a = scs.make_candidate(
        solver_name="u3_x", cell_id="general",
        spec_or_code={"k": "1"}, source_gap_ref="ten_001",
    )
    b = scs.make_candidate(
        solver_name="u3_x", cell_id="general",
        spec_or_code={"k": "different"},   # excluded from id
        source_gap_ref="ten_001",
    )
    assert a.candidate_id == b.candidate_id


def test_candidate_rejects_unknown_state():
    c = scs.make_candidate(
        solver_name="u3_x", cell_id="general",
        spec_or_code={}, source_gap_ref="ten_001",
    )
    with pytest.raises(ValueError):
        scs.with_state(c, "bogus_state")


def test_state_transitions_pure():
    c = scs.make_candidate(
        solver_name="u3_x", cell_id="general",
        spec_or_code={}, source_gap_ref="ten_001",
    )
    c2 = scs.with_state(c, "schema_valid")
    assert c.state == "raw_candidate"   # original unchanged
    assert c2.state == "schema_valid"


def test_can_exit_cold_blocks_low_use_count():
    c = scs.SolverCandidate(
        schema_version=1, candidate_id="abc123def456",
        state="cold_solver", solver_name="u3_x", cell_id="general",
        spec_or_code={}, source_gap_ref="ten_001",
        no_runtime_mutation=True, produced_by="t",
        branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
        use_count=10,   # < min 50
        shadow_observation_seconds=4000,
        critical_regressions=0,
    )
    ok, reason = scs.can_exit_cold(c)
    assert not ok
    assert "use_count" in reason


def test_can_exit_cold_passes_all_gates():
    c = scs.SolverCandidate(
        schema_version=1, candidate_id="abc123def456",
        state="cold_solver", solver_name="u3_x", cell_id="general",
        spec_or_code={}, source_gap_ref="g",
        no_runtime_mutation=True,
        produced_by="t", branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
        use_count=100,
        shadow_observation_seconds=4000,
        critical_regressions=0,
    )
    ok, reason = scs.can_exit_cold(c)
    assert ok


def test_store_save_and_round_trip(tmp_path):
    c = scs.make_candidate(
        solver_name="u3_x", cell_id="general",
        spec_or_code={"k": "v"}, source_gap_ref="ten_001",
    )
    store = scs.SolverCandidateStore().add(c)
    p = tmp_path / "store.json"
    scs.save_store(store, p)
    assert p.exists()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert c.candidate_id in data["candidates"]


def test_store_save_atomic_tmp_replace(tmp_path, monkeypatch):
    c = scs.make_candidate(
        solver_name="u3_x", cell_id="general",
        spec_or_code={}, source_gap_ref="g",
    )
    store = scs.SolverCandidateStore().add(c)
    real = scs.os.replace
    captured = []

    def cap(src, dst):
        captured.append((Path(src).name, Path(dst).name))
        return real(src, dst)

    monkeypatch.setattr(scs.os, "replace", cap)
    scs.save_store(store, tmp_path / "store.json")
    assert any(dst == "store.json" for _, dst in captured)
    assert any(src.startswith(".cands.") for src, _ in captured)


# ═══════════════════ solver_quarantine quotas ═════════════════════

def test_quota_state_remaining():
    s = sq.QuotaState()
    assert s.remaining("max_new_shadow_solvers_per_day") == 200


def test_quota_consume_and_reset():
    s = sq.QuotaState()
    s, ok = s.consume("max_new_shadow_solvers_per_day", 50)
    assert ok
    assert s.remaining("max_new_shadow_solvers_per_day") == 150
    s.reset_daily()
    assert s.remaining("max_new_shadow_solvers_per_day") == 200


def test_quota_consume_over_cap_blocks():
    s = sq.QuotaState()
    s, ok = s.consume("max_final_approvals_per_day", 21)
    assert ok is False


def test_admit_to_shadow_requires_test_valid_state():
    s = sq.QuotaState()
    c = scs.make_candidate(
        solver_name="u3_x", cell_id="general",
        spec_or_code={}, source_gap_ref="g",
    )
    # state="raw_candidate" → admission refused
    s, decision = sq.admit_to_shadow(s, c)
    assert decision.admitted is False
    assert "test_valid" in decision.rationale


def test_admit_to_shadow_succeeds_within_quota():
    s = sq.QuotaState()
    c = scs.make_candidate(
        solver_name="u3_x", cell_id="general",
        spec_or_code={}, source_gap_ref="g",
    )
    c = scs.with_state(c, "test_valid")
    s, decision = sq.admit_to_shadow(s, c)
    assert decision.admitted is True
    assert decision.target_state == "shadow_only"


def test_admit_to_shadow_blocked_by_quota():
    s = sq.QuotaState()
    s.consumed_today["max_new_shadow_solvers_per_day"] = 200   # exhausted
    c = scs.make_candidate(
        solver_name="u3_x", cell_id="general",
        spec_or_code={}, source_gap_ref="g",
    )
    c = scs.with_state(c, "test_valid")
    s, decision = sq.admit_to_shadow(s, c)
    assert decision.admitted is False
    assert "quota exhausted" in decision.rationale


def test_admit_to_review_ready_requires_cold_solver():
    s = sq.QuotaState()
    c = scs.make_candidate(
        solver_name="u3_x", cell_id="general",
        spec_or_code={}, source_gap_ref="g",
    )
    c = scs.with_state(c, "shadow_only")
    s, decision = sq.admit_to_review_ready(s, c)
    assert decision.admitted is False


def test_final_approval_requires_human_approval_id():
    """CRITICAL: constitution.no_foundational_auto_promotion."""
    s = sq.QuotaState()
    c = scs.make_candidate(
        solver_name="u3_x", cell_id="general",
        spec_or_code={}, source_gap_ref="g",
    )
    c = scs.with_state(c, "review_ready")
    s, decision = sq.admit_to_approved(s, c, human_approval_id=None)
    assert decision.admitted is False
    assert "human_approval_id" in decision.rationale


def test_final_approval_with_human_id_succeeds():
    s = sq.QuotaState()
    c = scs.make_candidate(
        solver_name="u3_x", cell_id="general",
        spec_or_code={}, source_gap_ref="g",
    )
    c = scs.with_state(c, "review_ready")
    s, decision = sq.admit_to_approved(
        s, c, human_approval_id="human:reviewer:2026-04-26",
    )
    assert decision.admitted is True
    assert decision.target_state == "approved"


# ═══════════════════ validators ════════════════════════════════════

def test_syntactic_validate_rejects_empty_solver_name():
    c = scs.SolverCandidate(
        schema_version=1, candidate_id="abc123def456",
        state="raw_candidate", solver_name="",   # empty
        cell_id="general", spec_or_code={"k": "v"},
        source_gap_ref="g", no_runtime_mutation=True,
        produced_by="t", branch_name="b", base_commit_hash="",
        pinned_input_manifest_sha256="",
    )
    r = sv.syntactic_validate(c)
    assert not r.passed


def test_semantic_validate_rejects_invalid_invariant():
    c = scs.make_candidate(
        solver_name="u3_x", cell_id="general",
        spec_or_code={"invariants": [123]},   # not str
        source_gap_ref="g",
    )
    r = sv.semantic_validate(c)
    assert not r.passed


def test_property_tests_zero_total_is_passing():
    c = scs.make_candidate(
        solver_name="u3_x", cell_id="general",
        spec_or_code={}, source_gap_ref="g",
    )
    r = sv.run_property_tests(c, [])
    assert r.passed == r.total == 0


def test_decide_verdict_cascade():
    syn = sv.GateResult(passed=True)
    sem = sv.GateResult(passed=True)
    prop = sv.CountedGateResult(passed=10, total=10)
    regr_pass = sv.CountedGateResult(passed=5, total=5)
    regr_fail = sv.CountedGateResult(passed=4, total=5)
    shadow_low = sv.ShadowEvalResult(observations=10, concordance_ratio=0.95)
    shadow_high = sv.ShadowEvalResult(observations=100,
                                          concordance_ratio=0.95)
    shadow_low_concord = sv.ShadowEvalResult(observations=100,
                                                  concordance_ratio=0.5)
    assert sv.decide_verdict(
        syntactic=sv.GateResult(passed=False),
        semantic=sem, property_tests=prop, regression=regr_pass,
        shadow=shadow_high,
    ) == "syntactic_invalid"
    assert sv.decide_verdict(
        syntactic=syn,
        semantic=sv.GateResult(passed=False),
        property_tests=prop, regression=regr_pass,
        shadow=shadow_high,
    ) == "semantic_invalid"
    assert sv.decide_verdict(
        syntactic=syn, semantic=sem, property_tests=prop,
        regression=regr_fail, shadow=shadow_high,
    ) == "regression_detected"
    assert sv.decide_verdict(
        syntactic=syn, semantic=sem, property_tests=prop,
        regression=regr_pass, shadow=shadow_low,
    ) == "needs_more_shadow"
    assert sv.decide_verdict(
        syntactic=syn, semantic=sem, property_tests=prop,
        regression=regr_pass, shadow=shadow_low_concord,
    ) == "rejected_low_value"
    assert sv.decide_verdict(
        syntactic=syn, semantic=sem, property_tests=prop,
        regression=regr_pass, shadow=shadow_high,
    ) == "pass_all_gates"


def test_validate_candidate_full_pipeline():
    c = scs.make_candidate(
        solver_name="u3_x", cell_id="general",
        spec_or_code={"invariants": ["x >= 0"]},
        source_gap_ref="g",
    )
    report = sv.validate_candidate(
        c,
        property_tests_input=[{"name": "p1", "passed": True}],
        regression_input=[{"name": "r1", "passed": True}],
        shadow_observations=100, shadow_concordance=0.95,
    )
    assert report.verdict == "pass_all_gates"
    assert report.execution_backend == "cpu"


def test_validation_report_to_dict_byte_stable():
    c = scs.make_candidate(
        solver_name="u3_x", cell_id="general",
        spec_or_code={}, source_gap_ref="g",
    )
    r1 = sv.validate_candidate(c, produced_at_iso="t")
    r2 = sv.validate_candidate(c, produced_at_iso="t")
    assert json.dumps(r1.to_dict(), sort_keys=True) == \
        json.dumps(r2.to_dict(), sort_keys=True)


# ═══════════════════ no auto-promotion / no runtime mutation ═════-

def test_u3_no_runtime_promotion_in_source():
    pkg = ROOT / "waggledance" / "core" / "solver_synthesis"
    forbidden = ("axiom_write(", "promote_to_runtime(",
                  "register_solver_in_runtime(", "live_register_solver(",
                  "auto_approve_candidate(", "git push origin main",
                  "merge_to_main(")
    for p in pkg.glob("*.py"):
        text = p.read_text(encoding="utf-8")
        for pat in forbidden:
            assert pat not in text, f"{p.name}: {pat}"


def test_no_runtime_mutation_const_in_source():
    pkg = ROOT / "waggledance" / "core" / "solver_synthesis"
    for p in pkg.glob("*.py"):
        text = p.read_text(encoding="utf-8")
        assert "no_runtime_mutation=False" not in text


# ═══════════════════ CLI ═══════════════════════════════════════════

def test_cli_help():
    r = subprocess.run(
        [sys.executable,
         str(ROOT / "tools" / "wd_synthesize_solver.py"), "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
    for flag in ("--gap-ref", "--solver-name", "--cell-id",
                  "--table-hint", "--apply", "--json"):
        assert flag in r.stdout


def test_cli_dry_run_routes_to_u3():
    r = subprocess.run(
        [sys.executable,
         str(ROOT / "tools" / "wd_synthesize_solver.py"),
         "--gap-ref", "ten_001", "--json"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["chosen_path"] == "U3_residual"


def test_cli_dry_run_routes_to_u1_with_table_hint(tmp_path):
    table_path = tmp_path / "hint.json"
    table_path.write_text(json.dumps({
        "intervals": [
            {"min": 0, "max": 10, "label": "low"},
            {"min": 10, "max": 20, "label": "high"},
        ],
    }), encoding="utf-8")
    r = subprocess.run(
        [sys.executable,
         str(ROOT / "tools" / "wd_synthesize_solver.py"),
         "--gap-ref", "ten_002",
         "--table-hint", str(table_path), "--json"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["chosen_path"] == "U1_compile"
