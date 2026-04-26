"""Targeted tests for Phase 9 §O Proposal Compiler."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.proposal_compiler import (
    acceptance_criteria_compiler as ac,
    affected_files_analyzer as afa,
    patch_generator as pg,
    pr_draft_compiler as prc,
    rollout_planner as rp,
    test_generator as tg,
)


def _proposal(ptype="solver_family_growth", target="thermal",
                solver="thermal_estimator") -> dict:
    return {
        "meta_proposal_id": "abc123def456",
        "proposal_type": ptype,
        "scope_class": "solver_library",
        "canonical_target": target,
        "selected_proposal": {
            "solver_name": solver,
            "cell_id": target,
            "solver_hash": "sha256:abc",
        },
    }


# ═══════════════════ schema invariants ═════════════════════════════

def test_schema_const_invariants():
    schema = json.loads((ROOT / "schemas" / "proposal_compiler.schema.json")
                          .read_text(encoding="utf-8"))
    assert schema["properties"]["no_main_branch_auto_merge"]["const"] is True
    assert schema["properties"]["no_runtime_mutation"]["const"] is True


# ═══════════════════ affected_files_analyzer ══════════════════════

def test_affected_files_solver_family_growth():
    p = _proposal()
    files = afa.analyze(p)
    assert any("axioms/thermal/thermal_estimator" in f for f in files)
    assert any("test_thermal_estimator" in f for f in files)


def test_affected_files_topology_subdivision():
    p = _proposal(ptype="topology_subdivision", target="root")
    files = afa.analyze(p)
    assert any("topology" in f for f in files)


def test_affected_files_unknown_type_returns_empty():
    p = _proposal(ptype="unknown_type")
    files = afa.analyze(p)
    assert files == []


def test_affected_files_deterministic():
    p = _proposal()
    a = afa.analyze(p)
    b = afa.analyze(p)
    assert a == b


# ═══════════════════ test_generator ════════════════════════════════

def test_test_spec_for_solver_family_growth():
    spec = tg.generate_test_spec(_proposal())
    assert "deterministic_compile" in spec["categories"]
    assert "byte_identical_artifact" in spec["categories"]


def test_test_spec_for_topology_subdivision():
    spec = tg.generate_test_spec(_proposal(ptype="topology_subdivision"))
    assert "shadow_first_invariant" in spec["categories"]


def test_test_spec_must_run_in_isolated_worktree():
    spec = tg.generate_test_spec(_proposal())
    assert spec["must_run_in_isolated_worktree"] is True


# ═══════════════════ rollout_planner ═══════════════════════════════

def test_rollout_includes_human_approval_gates():
    plan = rp.plan_rollout(_proposal())
    assert "human_review" in plan["human_approval_gates"]
    assert "canary_cell" in plan["human_approval_gates"]


def test_rollout_shadow_first():
    plan = rp.plan_rollout(_proposal())
    assert plan["shadow_first"] is True


def test_rollback_has_trigger_conditions():
    plan = rp.plan_rollback(_proposal())
    assert "critical_regression_detected" in plan["trigger_conditions"]
    assert plan["shadow_first"] is True


def test_rollout_review_only_for_non_runtime_proposals():
    plan = rp.plan_rollout(_proposal(ptype="archival_cleanup"))
    assert "review_only" in plan["stages"]


# ═══════════════════ acceptance_criteria_compiler ════════════════-

def test_acceptance_includes_no_runtime_mutation():
    crit = ac.compile_acceptance(_proposal())
    assert any("no live runtime path" in c for c in crit)


def test_acceptance_includes_human_review_id():
    crit = ac.compile_acceptance(_proposal())
    assert any("human review approval id" in c for c in crit)


def test_solver_growth_acceptance_includes_byte_identical():
    crit = ac.compile_acceptance(_proposal(ptype="solver_family_growth"))
    assert any("byte-identical" in c for c in crit)


def test_review_checklist_includes_human_authentication():
    md = ac.compile_review_checklist(_proposal())
    assert "human reviewer authenticated" in md
    assert "no_runtime_mutation invariant held" in md


def test_pr_draft_carries_const_invariants():
    p = _proposal()
    rollout = rp.plan_rollout(p)
    md = ac.compile_pr_draft(p, affected_files=["a.py", "b.py"],
                                  rollout_plan=rollout)
    assert "no_runtime_mutation:** True" in md
    assert "no_main_branch_auto_merge:** True" in md


# ═══════════════════ patch_generator ═══════════════════════════════

def test_patch_skeleton_is_skeleton_not_real_diff():
    p = _proposal()
    files = afa.analyze(p)
    patch = pg.generate_patch_skeleton(p, files)
    assert "SKELETON" in patch
    assert "no live runtime mutation" in patch


# ═══════════════════ pr_draft_compiler.compile_bundle ══════════════

def test_compile_bundle_const_invariants():
    bundle = prc.compile_bundle(_proposal())
    assert bundle.no_main_branch_auto_merge is True
    assert bundle.no_runtime_mutation is True


def test_compile_bundle_deterministic():
    a = prc.compile_bundle(_proposal())
    b = prc.compile_bundle(_proposal())
    assert a.bundle_id == b.bundle_id
    assert a.to_dict() == b.to_dict()


def test_compile_bundle_to_dict_byte_stable():
    bundle = prc.compile_bundle(_proposal())
    j1 = json.dumps(bundle.to_dict(), sort_keys=True)
    j2 = json.dumps(bundle.to_dict(), sort_keys=True)
    assert j1 == j2


def test_compile_bundle_carries_source_id():
    bundle = prc.compile_bundle(_proposal())
    assert bundle.source_meta_proposal_id == "abc123def456"


def test_compile_bundle_pr_draft_includes_target():
    bundle = prc.compile_bundle(_proposal(target="energy"))
    assert "energy" in bundle.pr_draft_md


# ═══════════════════ no auto-apply / no runtime mutation ─────────-

def test_compiler_source_has_no_apply_or_runtime_patterns():
    pkg = ROOT / "waggledance" / "core" / "proposal_compiler"
    forbidden = ("git push origin main", "git merge ",
                  "git apply ", "git am ",
                  "axiom_write(", "promote_to_runtime(",
                  "subprocess.run([\"git", "subprocess.run(['git",
                  "merge_to_main(", "auto_apply_patch(")
    for p in pkg.glob("*.py"):
        text = p.read_text(encoding="utf-8")
        for pat in forbidden:
            assert pat not in text, f"{p.name}: {pat}"


def test_no_const_invariant_false_in_source():
    pkg = ROOT / "waggledance" / "core" / "proposal_compiler"
    for p in pkg.glob("*.py"):
        text = p.read_text(encoding="utf-8")
        assert "no_main_branch_auto_merge=False" not in text
        assert "no_runtime_mutation=False" not in text


# ═══════════════════ CLI ═══════════════════════════════════════════

def test_cli_help():
    r = subprocess.run(
        [sys.executable,
         str(ROOT / "tools" / "compile_meta_proposal.py"), "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
    assert "--meta-proposal-path" in r.stdout


def test_cli_dry_run(tmp_path):
    p_path = tmp_path / "p.json"
    p_path.write_text(json.dumps(_proposal()), encoding="utf-8")
    r = subprocess.run(
        [sys.executable,
         str(ROOT / "tools" / "compile_meta_proposal.py"),
         "--meta-proposal-path", str(p_path), "--json"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["dry_run"] is True
    assert out["no_runtime_mutation"] is True


def test_cli_apply_writes_bundle(tmp_path):
    p_path = tmp_path / "p.json"
    p_path.write_text(json.dumps(_proposal()), encoding="utf-8")
    out_dir = tmp_path / "out"
    r = subprocess.run(
        [sys.executable,
         str(ROOT / "tools" / "compile_meta_proposal.py"),
         "--meta-proposal-path", str(p_path),
         "--output-dir", str(out_dir),
         "--apply", "--json"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
    out = json.loads(r.stdout)
    bundle_dir = Path(out["bundle_dir"])
    assert (bundle_dir / "bundle.json").exists()
    assert (bundle_dir / "pr_draft.md").exists()
    assert (bundle_dir / "patch_skeleton.diff").exists()
    assert (bundle_dir / "review_checklist.md").exists()
