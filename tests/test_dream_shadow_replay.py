"""Targeted tests for the shadow graph + structural replay harness
(Phase 8.5 Session C, commit 4). 14 cases covering c.txt §RECOMMENDED
COMMIT RHYTHM commit 4.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.dreaming import (
    DREAMING_SCHEMA_VERSION,
    MAX_REPLAY_CASES,
    MIN_GAIN_RATIO,
    replay as rep,
    shadow_graph as sg,
)


# ── Fixture builders ────────────────────────────────────────────-

def _live_solvers() -> list[dict]:
    return [
        {"solver_id": "thermal_a", "cell_id": "thermal"},
        {"solver_id": "thermal_b", "cell_id": "thermal"},
        {"solver_id": "energy_a",  "cell_id": "energy"},
    ]


def _live_edges() -> list[dict]:
    return [
        {"solver_a": "thermal_a", "solver_b": "thermal_b",
         "relation_type": "alternates_with"},
    ]


def _accepted_proposal() -> dict:
    return {
        "proposal_id": "p1", "solver_name": "thermal_dream_estimator",
        "cell_id": "thermal",
    }


def _shadow_edges() -> list[dict]:
    return [
        {"solver_a": "thermal_a", "solver_b": "thermal_dream_estimator",
         "relation_type": "composes_with"},
        {"solver_a": "thermal_dream_estimator",
         "solver_b": "thermal_b", "relation_type": "refines"},
    ]


def _replay_manifest() -> dict:
    return {"cases": [
        {"replay_case_id": "rc_001", "source_tension_id": "ten_001",
         "source_curiosity_id": "cur_thermal_1", "candidate_cell": "thermal",
         "query_hash": "h001", "tags": ["fallback"], "protect": False,
         "suspected_missing_capability": "thermal_dream_estimator"},
        {"replay_case_id": "rc_002", "source_tension_id": "ten_001",
         "source_curiosity_id": None, "candidate_cell": "thermal",
         "query_hash": "h002", "tags": [], "protect": True,
         "original_resolution_hash": "thermal_a"},
        {"replay_case_id": "rc_003", "source_tension_id": "unrelated",
         "source_curiosity_id": "cur_other", "candidate_cell": "energy",
         "query_hash": "h003", "tags": [], "protect": False},
    ]}


def _build_live_and_shadow():
    live = sg.build_live_graph(_live_solvers(), _live_edges())
    shadow = sg.add_shadow_proposals(
        live, [_accepted_proposal()], extra_edges=_shadow_edges(),
    )
    diff = sg.diff_graphs(live, shadow)
    return live, shadow, diff


# ── 1. shadow graph structural diff generation ────────────────-

def test_shadow_structural_diff_generation():
    live, shadow, diff = _build_live_and_shadow()
    assert "thermal_dream_estimator" in diff.new_nodes
    assert ("thermal_a", "thermal_dream_estimator", "composes_with") in \
        diff.new_structural_edges


# ── 2. new_bridge_candidates surfaced in shadow ───────────────-

def test_new_bridge_candidates_surfaced():
    _, _, diff = _build_live_and_shadow()
    assert any(
        e[2] == "composes_with"
        and "thermal_dream_estimator" in (e[0], e[1])
        for e in diff.new_bridge_candidates
    )


# ── 3. structural_gain_count computation ──────────────────────-

def test_structural_gain_count_computed():
    live, shadow, diff = _build_live_and_shadow()
    cases = rep.select_replay_cases(
        _replay_manifest(),
        tension_ids={"ten_001"}, curiosity_ids={"cur_thermal_1"},
    )
    report = rep.build_report(
        cases=cases, live=live, shadow=shadow, diff=diff,
        branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
        replay_manifest_sha256=None,
        tension_ids_targeted={"ten_001"}, collapse_passed=True,
    )
    # rc_001 has suspected_missing_capability = thermal_dream_estimator
    # which is now a new node → structural_gain
    assert report.structural_gain_count >= 1


# ── 4. protected_case_regression_count detection ───────────────

def test_protected_case_regression_count():
    """Shadow only ADDS nodes; protected case should not regress."""
    live, shadow, diff = _build_live_and_shadow()
    cases = rep.select_replay_cases(
        _replay_manifest(),
        tension_ids={"ten_001"}, curiosity_ids=set(),
    )
    report = rep.build_report(
        cases=cases, live=live, shadow=shadow, diff=diff,
        branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
        replay_manifest_sha256=None,
        tension_ids_targeted={"ten_001"}, collapse_passed=True,
    )
    assert report.protected_case_regression_count == 0


# ── 5. shadow_replay_report emitted deterministically ─────────-

def test_replay_report_emitted_deterministic(tmp_path):
    live, shadow, diff = _build_live_and_shadow()
    cases = rep.select_replay_cases(
        _replay_manifest(),
        tension_ids={"ten_001"}, curiosity_ids={"cur_thermal_1"},
    )
    r1 = rep.build_report(
        cases=cases, live=live, shadow=shadow, diff=diff,
        branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
        replay_manifest_sha256=None,
        tension_ids_targeted={"ten_001"}, collapse_passed=True,
    )
    out1 = tmp_path / "a"
    out2 = tmp_path / "b"
    rep.emit_report(r1, out1)
    rep.emit_report(r1, out2)
    j1 = (out1 / "shadow_replay_report.json").read_text(encoding="utf-8")
    j2 = (out2 / "shadow_replay_report.json").read_text(encoding="utf-8")
    assert j1 == j2


# ── 6. deferred_to_dream tensions are NOT marked resolved ───-

def test_deferred_to_dream_not_marked_resolved():
    """The replay layer must NEVER mutate or mark tensions resolved
    in this session — Session C is shadow-only by spec."""
    live, shadow, diff = _build_live_and_shadow()
    cases = rep.select_replay_cases(
        _replay_manifest(),
        tension_ids={"ten_001"}, curiosity_ids={"cur_thermal_1"},
    )
    report = rep.build_report(
        cases=cases, live=live, shadow=shadow, diff=diff,
        branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
        replay_manifest_sha256=None,
        tension_ids_targeted={"ten_001"}, collapse_passed=True,
    )
    d = rep.report_to_dict(report)
    # Output never carries a resolution / merge / runtime instruction
    forbidden = ["mark_resolved", "tension_resolved", "merge_now",
                 "runtime_register", "axiom_write"]
    blob = json.dumps(d)
    for pat in forbidden:
        assert pat not in blob


# ── 7. no runtime mutation ────────────────────────────────────-

def test_no_runtime_mutation_in_shadow_graph(tmp_path, monkeypatch):
    """build_live_graph + add_shadow_proposals must be pure functions
    that do not write to disk or import runtime registries."""
    writes = {"n": 0}

    def fail_write(self, *a, **kw):
        writes["n"] += 1
        raise AssertionError("file write attempted in shadow graph build")

    monkeypatch.setattr(Path, "write_text", fail_write)
    live = sg.build_live_graph(_live_solvers(), _live_edges())
    shadow = sg.add_shadow_proposals(live, [_accepted_proposal()],
                                       extra_edges=_shadow_edges())
    sg.diff_graphs(live, shadow)
    assert writes["n"] == 0


# ── 8. shadow-only invariants maintained ──────────────────────-

def test_shadow_only_invariants_maintained():
    """Live nodes remain is_live=True; shadow nodes are is_live=False
    and source='shadow_proposal'."""
    live, shadow, _ = _build_live_and_shadow()
    live_ids = {n.solver_id for n in live.nodes}
    for n in shadow.nodes:
        if n.solver_id in live_ids:
            assert n.is_live is True and n.source == "library"
        else:
            assert n.is_live is False
            assert n.source == "shadow_proposal"


# ── 9. replay on fixed proposal fixtures is deterministic ────-

def test_replay_deterministic_on_fixed_inputs():
    live, shadow, diff = _build_live_and_shadow()
    cases = rep.select_replay_cases(
        _replay_manifest(),
        tension_ids={"ten_001"}, curiosity_ids={"cur_thermal_1"},
    )
    r1 = rep.build_report(
        cases=cases, live=live, shadow=shadow, diff=diff,
        branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
        replay_manifest_sha256=None,
        tension_ids_targeted={"ten_001"}, collapse_passed=True,
    )
    r2 = rep.build_report(
        cases=cases, live=live, shadow=shadow, diff=diff,
        branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
        replay_manifest_sha256=None,
        tension_ids_targeted={"ten_001"}, collapse_passed=True,
    )
    assert rep.report_to_dict(r1) == rep.report_to_dict(r2)


# ── 10. replay_manifest_uses_tmp_path_in_tests ────────────────-

def test_replay_manifest_emit_uses_tmp_path(tmp_path):
    """emit_replay_case_manifest must write under the supplied
    directory, NEVER into the live repo."""
    cases = [
        rep.case_from_dict({"replay_case_id": "rc_001",
                              "source_tension_id": "ten_001"}),
        rep.case_from_dict({"replay_case_id": "rc_002",
                              "source_tension_id": "ten_001"}),
    ]
    out = rep.emit_replay_case_manifest(
        cases, tmp_path,
        branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
    )
    assert out.parent == tmp_path
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["case_count"] == 2
    assert [c["replay_case_id"] for c in data["cases"]] == ["rc_001", "rc_002"]


# ── 11. replay structural proxy explicitly documented ────────-

def test_replay_methodology_label_present():
    """replay_methodology must equal 'structural_proxy_v0.1' per c.txt
    §C6."""
    live, shadow, diff = _build_live_and_shadow()
    report = rep.build_report(
        cases=[], live=live, shadow=shadow, diff=diff,
        branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
        replay_manifest_sha256=None,
        tension_ids_targeted=set(), collapse_passed=False,
    )
    assert report.replay_methodology == "structural_proxy_v0.1"


# ── 12. replay_methodology_disclaimer_present ─────────────────-

def test_replay_methodology_disclaimer_present():
    live, shadow, diff = _build_live_and_shadow()
    report = rep.build_report(
        cases=[], live=live, shadow=shadow, diff=diff,
        branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
        replay_manifest_sha256=None,
        tension_ids_targeted=set(), collapse_passed=False,
    )
    md = rep.render_report_md(rep.report_to_dict(report))
    assert "Methodology Limitations" in md
    assert "structural" in md.lower()
    assert "human review" in md.lower()


# ── 13. replay_case_manifest.json byte-identical determinism ──

def test_replay_case_manifest_byte_identical(tmp_path):
    cases = [
        rep.case_from_dict({"replay_case_id": "rc_b",
                              "source_tension_id": "ten"}),
        rep.case_from_dict({"replay_case_id": "rc_a",
                              "source_tension_id": "ten"}),
    ]
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    p1 = rep.emit_replay_case_manifest(
        cases, out_a, branch_name="br", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
    )
    p2 = rep.emit_replay_case_manifest(
        cases, out_b, branch_name="br", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
    )
    assert p1.read_text(encoding="utf-8") == p2.read_text(encoding="utf-8")


# ── 14. shadow_replay_report.json byte-identical determinism ─-

def test_shadow_replay_report_byte_identical(tmp_path):
    live, shadow, diff = _build_live_and_shadow()
    cases = rep.select_replay_cases(
        _replay_manifest(),
        tension_ids={"ten_001"}, curiosity_ids={"cur_thermal_1"},
    )
    r = rep.build_report(
        cases=cases, live=live, shadow=shadow, diff=diff,
        branch_name="b", base_commit_hash="ab",
        pinned_input_manifest_sha256="sha256:t",
        replay_manifest_sha256=None,
        tension_ids_targeted={"ten_001"}, collapse_passed=True,
    )
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    rep.emit_report(r, out_a)
    rep.emit_report(r, out_b)
    j1 = (out_a / "shadow_replay_report.json").read_text(encoding="utf-8")
    j2 = (out_b / "shadow_replay_report.json").read_text(encoding="utf-8")
    assert j1 == j2
    # Replay selection bounded to MAX_REPLAY_CASES
    assert len(cases) <= MAX_REPLAY_CASES
