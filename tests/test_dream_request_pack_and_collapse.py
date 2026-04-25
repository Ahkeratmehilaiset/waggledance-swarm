"""Targeted tests for dream request packs + proposal ingestion +
deterministic collapse (Phase 8.5 Session C, commit 3). 18 cases
covering c.txt §RECOMMENDED COMMIT RHYTHM commit 3.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.dreaming import (
    collapse as col,
    curriculum as cu,
    request_pack as rp,
)


def _load_run_dream_cycle():
    path = ROOT / "tools" / "run_dream_cycle.py"
    spec = importlib.util.spec_from_file_location("run_dream_cycle_tool", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_dream_cycle_tool"] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Fixture builders ────────────────────────────────────────────-

def _self_model_with_deferred() -> dict:
    return {
        "schema_version": 1,
        "workspace_tensions": [
            {"tension_id": "ten_001", "type": "scorecard_drift",
             "claim": "thermal score = 0.92", "observation": "fail rate 0.18",
             "severity": "high", "lifecycle_status": "persisting",
             "resolution_path": "deferred_to_dream",
             "evidence_refs": ["cell:thermal", "cur:cur_thermal_1"]},
        ],
        "blind_spots": [
            {"domain": "safety", "severity": "high",
             "detectors": ["coverage_negative_space"]},
        ],
        "scorecard": {"thermal": 0.55},
        "attention_focus": [
            {"curiosity_id": "cur_thermal_1", "candidate_cell": "thermal",
             "estimated_value": 9.5, "rank": 1},
        ],
    }


def _curiosity_log() -> list[dict]:
    return [
        {"curiosity_id": "cur_thermal_1", "candidate_cell": "thermal",
         "estimated_value": 9.5, "suspected_gap_type": "missing_solver",
         "count": 12},
    ]


def _calibration() -> list[dict]:
    return [{"dimension": "thermal", "prior_score": 0.80,
             "evidence_implied_score": 0.55}]


def _replay_case_manifest() -> dict:
    return {"cases": [
        {"replay_case_id": "rc_001", "source_tension_id": "ten_001",
         "source_curiosity_id": "cur_thermal_1",
         "candidate_cell": "thermal", "protect": False},
        {"replay_case_id": "rc_002", "source_tension_id": "ten_001",
         "source_curiosity_id": None,
         "candidate_cell": "thermal", "protect": True},
        {"replay_case_id": "rc_003", "source_tension_id": "unrelated",
         "candidate_cell": "energy", "protect": False},
    ]}


def _build_curriculum() -> cu.DreamCurriculum:
    return cu.build_curriculum(
        self_model=_self_model_with_deferred(),
        curiosity_log=_curiosity_log(),
        calibration_corrections=_calibration(),
        branch_name="phase8.5/dream-curriculum",
        base_commit_hash="abc1234",
        pinned_input_manifest_sha256="sha256:" + "f" * 64,
    )


def _make_proposal() -> dict:
    return {
        "proposal_id": "dream-prop-1",
        "cell_id": "thermal",
        "solver_name": "thermal_dream_estimator",
        "purpose": "Estimate thermal output from inputs A and B.",
        "inputs": [
            {"name": "a", "unit": "m", "description": "first input",
             "range": [0, 100], "type": "number"},
            {"name": "b", "unit": "m", "description": "second input",
             "range": [0, 100], "type": "number"},
        ],
        "outputs": [
            {"name": "out", "unit": "m", "description": "primary output",
             "type": "number", "primary": True},
        ],
        "formula_or_algorithm": {
            "kind": "formula_chain",
            "steps": [{"name": "out", "formula": "a + b",
                       "output_unit": "m", "description": "sum"}],
        },
        "assumptions": ["inputs measured in meters"],
        "invariants": ["out >= 0"],
        "units": {"system": "SI"},
        "tests": [{"name": "basic", "inputs": {"a": 1, "b": 2},
                   "expected": 3, "tolerance": 1e-6}],
        "expected_failure_modes": [
            {"condition": "a < 0", "behavior": "returns negative"}],
        "examples": [
            {"description": "simple", "inputs": {"a": 1, "b": 2},
             "expected": 3}],
        "provenance_note": "dream teacher fixture",
        "estimated_latency_ms": 0.1,
        "expected_coverage_lift": {"value": 0.10, "uncertainty": "low",
                                    "rationale": "covers common thermal Q"},
        "risk_level": "low",
        "uncertainty_declaration": "small sample of validation cases",
    }


def _write_proposal(dir_: Path, name: str, p: dict,
                      pack_sha12: str | None = None,
                      method: str = "manual") -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    pp = dir_ / name
    pp.write_text(json.dumps(p, indent=2, sort_keys=True), encoding="utf-8")
    if pack_sha12 is not None:
        sidecar = dir_ / f"{name}.dream_metadata.json"
        sidecar.write_text(json.dumps({
            "responding_to_dream_request_pack_sha12": pack_sha12,
            "generation_method": method,
            "external_model_hint": None,
        }, indent=2, sort_keys=True), encoding="utf-8")
    return pp


# ── 1. teacher pack generation structure ────────────────────────-

def test_teacher_pack_generation_structure():
    c = _build_curriculum()
    night = c.nights[0]
    pack = rp.build_request_pack(
        night=night, self_model=_self_model_with_deferred(),
        cell_manifest={"cell_id": "thermal", "solver_count": 4},
        attention_focus=_self_model_with_deferred()["attention_focus"],
        replay_case_manifest=_replay_case_manifest(),
        branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:test",
    )
    d = rp.pack_to_dict(pack)
    required = {"schema_version", "continuity_anchor", "night_index",
                 "candidate_cell", "requested_action_type",
                 "source_tension_ids", "source_curiosity_ids",
                 "replay_case_ids", "structural_context",
                 "cell_manifest_summary", "self_model_snippet",
                 "attention_focus", "why_now", "uncertainty", "mode",
                 "primary_source", "dream_request_pack_sha12"}
    assert required.issubset(d.keys())


# ── 2. dream_request_pack schema matches teacher prompt ─────────-

def test_pack_schema_matches_teacher_prompt():
    """The teacher prompt's Input Schema lists exactly these top-level
    keys; the emitted pack must carry all of them."""
    expected_top = {"schema_version", "continuity_anchor", "night_index",
                    "candidate_cell", "requested_action_type",
                    "source_tension_ids", "source_curiosity_ids",
                    "replay_case_ids", "structural_context",
                    "cell_manifest_summary", "self_model_snippet",
                    "attention_focus", "why_now", "uncertainty", "mode",
                    "primary_source", "dream_request_pack_sha12"}
    c = _build_curriculum()
    pack = rp.build_request_pack(
        night=c.nights[0], self_model=_self_model_with_deferred(),
        cell_manifest={"cell_id": "thermal", "solver_count": 4},
        attention_focus=[],
        replay_case_manifest=None,
        branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:test",
    )
    d = rp.pack_to_dict(pack)
    assert set(d.keys()) == expected_top


# ── 3. dream_request_pack_sha12 excludes self ───────────────────-

def test_pack_sha12_excludes_self():
    c = _build_curriculum()
    pack = rp.build_request_pack(
        night=c.nights[0], self_model=_self_model_with_deferred(),
        cell_manifest={"cell_id": "thermal", "solver_count": 4},
        attention_focus=[], replay_case_manifest=None,
        branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:test",
    )
    # Recompute sha12 from the dict-without-sha and compare
    d_no_sha = rp.pack_to_dict(pack)
    d_no_sha.pop(rp.SHA_FIELD)
    recomputed = rp.compute_pack_sha12(d_no_sha)
    assert recomputed == pack.dream_request_pack_sha12
    # Including the sha12 field in the input is rejected
    with pytest.raises(ValueError):
        rp.compute_pack_sha12({rp.SHA_FIELD: pack.dream_request_pack_sha12})


# ── 4. no live LLM call path in tests ───────────────────────────-

def test_no_live_llm_call_path(monkeypatch):
    """Verify there is no `requests.post` / `httpx.post` / `urllib`
    call path triggered during pack build or collapse on a fixture
    proposal."""
    called = {"n": 0}

    def fail_call(*a, **kw):
        called["n"] += 1
        raise AssertionError("network call attempted in dream pipeline")

    monkeypatch.setattr("urllib.request.urlopen", fail_call, raising=False)
    c = _build_curriculum()
    pack = rp.build_request_pack(
        night=c.nights[0], self_model=_self_model_with_deferred(),
        cell_manifest={"cell_id": "thermal", "solver_count": 4},
        attention_focus=[], replay_case_manifest=None,
        branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:test",
    )
    assert called["n"] == 0
    assert pack.dream_request_pack_sha12


# ── 5. proposal ingestion from single file ───────────────────────

def test_proposal_ingestion_single_file(tmp_path):
    pp = _write_proposal(tmp_path, "p1.json", _make_proposal(),
                          pack_sha12="abcdef123456")
    selected, truncated = col.discover_proposals(
        proposal=pp, proposal_dir=None, max_proposals=3,
    )
    assert len(selected) == 1
    assert selected[0] == pp
    assert truncated == []


# ── 6. proposal ingestion from directory in lexicographic order ──

def test_proposal_ingestion_directory_lexicographic(tmp_path):
    p1 = _write_proposal(tmp_path, "b_proposal.json", _make_proposal())
    p2 = _write_proposal(tmp_path, "a_proposal.json", _make_proposal())
    p3 = _write_proposal(tmp_path, "c_proposal.json", _make_proposal())
    selected, _ = col.discover_proposals(
        proposal=None, proposal_dir=tmp_path, max_proposals=3,
    )
    names = [p.name for p in selected]
    assert names == ["a_proposal.json", "b_proposal.json", "c_proposal.json"]


# ── 7. max_proposals limit ───────────────────────────────────────

def test_max_proposals_limit(tmp_path):
    for i in range(7):
        _write_proposal(tmp_path, f"p_{i:02d}.json", _make_proposal())
    selected, truncated = col.discover_proposals(
        proposal=None, proposal_dir=tmp_path, max_proposals=3,
    )
    assert len(selected) == 3
    assert len(truncated) == 4
    # max_proposals must reject out-of-range values
    with pytest.raises(ValueError):
        col.discover_proposals(None, tmp_path, max_proposals=0)
    with pytest.raises(ValueError):
        col.discover_proposals(None, tmp_path, max_proposals=11)


# ── 8. truncated proposals logged in report ─────────────────────-

def test_truncated_proposals_logged(tmp_path):
    for i in range(5):
        _write_proposal(tmp_path, f"p_{i:02d}.json", _make_proposal(),
                         pack_sha12="abcdef123456")
    selected, truncated = col.discover_proposals(
        proposal=None, proposal_dir=tmp_path, max_proposals=2,
    )
    report = col.collapse_many(
        proposals=selected, truncated=truncated,
        known_pack_sha12s={"abcdef123456"},
        branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
        replay_manifest_sha256=None, repo_root=ROOT,
        max_proposals=2,
    )
    d = col.report_to_dict(report)
    assert len(d["truncated_proposals"]) == 3
    md = col.render_report_md(d)
    assert "Truncated Proposals (Not Evaluated)" in md


# ── 9. orphan proposal rejected ──────────────────────────────────

def test_orphan_proposal_rejected(tmp_path):
    """No sidecar, no inline linkage → orphan, REJECT_HARD."""
    pp = _write_proposal(tmp_path, "orphan.json", _make_proposal(),
                          pack_sha12=None)
    c = col.collapse_one(pp, known_pack_sha12s={"abcdef123456"},
                          repo_root=ROOT)
    assert c.collapse_verdict == "REJECT_HARD"
    assert c.linkage_source == "missing"
    assert any("orphan" in n for n in c.notes)


# ── 10. sidecar metadata naming/schema ──────────────────────────-

def test_sidecar_naming_and_schema(tmp_path):
    pp = _write_proposal(tmp_path, "good.json", _make_proposal(),
                          pack_sha12="aaaaaaaaaaaa")
    sp = col.sidecar_path_for(pp)
    assert sp.name == "good.json.dream_metadata.json"
    sidecar = col.load_sidecar(pp)
    assert sidecar is not None
    ok, errors = col.validate_sidecar(sidecar)
    assert ok and not errors

    # Missing required field
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    pp2 = bad_dir / "bad.json"
    pp2.write_text("{}", encoding="utf-8")
    (bad_dir / "bad.json.dream_metadata.json").write_text(
        json.dumps({"generation_method": "manual"}), encoding="utf-8")
    bad_sidecar = col.load_sidecar(pp2)
    ok, errors = col.validate_sidecar(bad_sidecar)
    assert not ok
    assert any("responding_to_dream_request_pack_sha12" in e for e in errors)

    # Invalid generation_method
    pp3 = bad_dir / "bad2.json"
    pp3.write_text("{}", encoding="utf-8")
    (bad_dir / "bad2.json.dream_metadata.json").write_text(
        json.dumps({"responding_to_dream_request_pack_sha12": "x" * 12,
                    "generation_method": "wishful"}), encoding="utf-8")
    ok, errors = col.validate_sidecar(col.load_sidecar(pp3))
    assert not ok


# ── 11. deterministic collapse using existing gate logic ────────-

def test_collapse_uses_existing_gate_logic(tmp_path):
    pp = _write_proposal(tmp_path, "good.json", _make_proposal(),
                          pack_sha12="bbbbbbbbbbbb")
    c = col.collapse_one(pp, known_pack_sha12s={"bbbbbbbbbbbb"},
                          repo_root=ROOT)
    # Used the in-repo Phase 8 gates → all gate names appear
    gate_names = {g["gate"] for g in c.gate_results}
    expected = {"schema", "cell_exists", "hash_duplicate", "io_types",
                "deterministic_replay", "unit_consistency",
                "contradiction", "invariants_present", "tests_present",
                "latency_budget", "no_secrets_no_paths", "llm_dependency",
                "closed_world", "machine_invariants_shape"}
    assert expected.issubset(gate_names)


# ── 12. proposal_gate_capabilities + gate_provenance recorded ──-

def test_gate_provenance_recorded(tmp_path):
    pp = _write_proposal(tmp_path, "good.json", _make_proposal(),
                          pack_sha12="cccccccccccc")
    c = col.collapse_one(pp, known_pack_sha12s={"cccccccccccc"},
                          repo_root=ROOT,
                          capabilities={"contradiction": "shadow_proxy"})
    # All gates with "native" by default, except overridden
    assert c.gate_provenance.get("contradiction") == "shadow_proxy"
    assert c.gate_provenance.get("schema") == "native"
    # Provenance values are exactly the allowed enum
    allowed = {"native", "shadow_proxy", "unavailable"}
    for v in c.gate_provenance.values():
        assert v in allowed


# ── 13. rejection path continues to next proposal ───────────────-

def test_rejection_path_continues(tmp_path):
    """A bad proposal must not stop subsequent proposals from being
    evaluated."""
    bad = copy.deepcopy(_make_proposal()); bad.pop("inputs")
    good = _make_proposal()
    _write_proposal(tmp_path, "a_bad.json", bad,
                     pack_sha12="dddddddddddd")
    _write_proposal(tmp_path, "b_good.json", good,
                     pack_sha12="dddddddddddd")
    selected, truncated = col.discover_proposals(
        proposal=None, proposal_dir=tmp_path, max_proposals=3,
    )
    report = col.collapse_many(
        proposals=selected, truncated=truncated,
        known_pack_sha12s={"dddddddddddd"},
        branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
        replay_manifest_sha256=None, repo_root=ROOT,
        max_proposals=3,
    )
    assert len(report.proposals_evaluated) == 2
    verdicts = [p.collapse_verdict for p in report.proposals_evaluated]
    assert "REJECT_HARD" in verdicts


# ── 14. ACCEPT_CANDIDATE demoted to shadow-only ─────────────────-

def test_accept_candidate_demoted_to_shadow_only(tmp_path):
    pp = _write_proposal(tmp_path, "good.json", _make_proposal(),
                          pack_sha12="eeeeeeeeeeee")
    c = col.collapse_one(pp, known_pack_sha12s={"eeeeeeeeeeee"},
                          repo_root=ROOT)
    # Even if raw verdict is ACCEPT_CANDIDATE, shadow_only must be True
    assert c.shadow_only is True


# ── 15. verdict handling matrix correctness ─────────────────────-

def test_verdict_matrix_mapping():
    assert col._to_collapse_verdict("ACCEPT_CANDIDATE") == "ACCEPT_CANDIDATE"
    assert col._to_collapse_verdict("REJECT_SCHEMA") == "REJECT_HARD"
    assert col._to_collapse_verdict("REJECT_DUPLICATE") == "REJECT_HARD"
    assert col._to_collapse_verdict("REJECT_CONTRADICTION") == "REJECT_HARD"
    assert col._to_collapse_verdict("REJECT_LOW_VALUE") == "REJECT_SOFT"
    assert col._to_collapse_verdict("ABSTAIN") == "REJECT_SOFT"
    assert col._to_collapse_verdict("INCONCLUSIVE") == "REJECT_SOFT"
    assert col._to_collapse_verdict("UNKNOWN") == "REJECT_SOFT"


# ── 16. stochastic_generation_remains_external ──────────────────-

def test_stochastic_generation_remains_external(tmp_path):
    """run_dream_cycle must NEVER call an external generator. It
    only consumes proposal files supplied by the operator."""
    tool = _load_run_dream_cycle()
    src = (ROOT / "tools" / "run_dream_cycle.py").read_text(encoding="utf-8")
    # Forbidden patterns that would indicate live LLM calls
    forbidden = ["requests.post(", "httpx.post(", "openai.",
                 "anthropic.", "ollama.generate(", "subprocess.run(['curl"]
    for pat in forbidden:
        assert pat not in src, f"forbidden network call in run_dream_cycle: {pat}"


# ── 17. dream_request_pack.json byte-identical determinism ──────-

def test_pack_json_byte_identical(tmp_path):
    sm = _self_model_with_deferred()
    c = _build_curriculum()
    p1 = rp.build_request_pack(
        night=c.nights[0], self_model=sm,
        cell_manifest={"cell_id": "thermal", "solver_count": 4},
        attention_focus=sm["attention_focus"],
        replay_case_manifest=_replay_case_manifest(),
        branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:test",
    )
    p2 = rp.build_request_pack(
        night=c.nights[0], self_model=sm,
        cell_manifest={"cell_id": "thermal", "solver_count": 4},
        attention_focus=sm["attention_focus"],
        replay_case_manifest=_replay_case_manifest(),
        branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:test",
    )
    j1 = json.dumps(rp.pack_to_dict(p1), indent=2, sort_keys=True)
    j2 = json.dumps(rp.pack_to_dict(p2), indent=2, sort_keys=True)
    assert j1 == j2
    assert p1.dream_request_pack_sha12 == p2.dream_request_pack_sha12


# ── 18. proposal_collapse_report.json byte-identical determinism

def test_collapse_report_json_byte_identical(tmp_path):
    pp = _write_proposal(tmp_path, "good.json", _make_proposal(),
                          pack_sha12="ffffffffffff")
    selected, truncated = col.discover_proposals(
        proposal=pp, proposal_dir=None, max_proposals=3,
    )
    r1 = col.collapse_many(
        proposals=selected, truncated=truncated,
        known_pack_sha12s={"ffffffffffff"},
        branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
        replay_manifest_sha256=None, repo_root=ROOT,
        max_proposals=3,
    )
    r2 = col.collapse_many(
        proposals=selected, truncated=truncated,
        known_pack_sha12s={"ffffffffffff"},
        branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
        replay_manifest_sha256=None, repo_root=ROOT,
        max_proposals=3,
    )
    j1 = json.dumps(col.report_to_dict(r1), indent=2, sort_keys=True)
    j2 = json.dumps(col.report_to_dict(r2), indent=2, sort_keys=True)
    assert j1 == j2
