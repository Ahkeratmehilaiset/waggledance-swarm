# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.dreaming.replay.

Iteration N+5 Codex-scout Candidate 2 (Claude takes; Codex owns
Candidate 1 #149 in parallel — disjoint write scopes per rule 7).

replay is the structural counterfactual harness: re-evaluates pinned
historical cases against the shadow graph to ask "would the new
shadow solver have realized a structural path that the live solver
did not?" (Phase 8.5 Session C, deliverable C.6). Drift here would
either falsely promote a candidate (regression hidden, structural
gain inflated) or block an otherwise-promising shadow.

Test scope per Codex N+5 scout: select_replay_cases dedupe/bounds
and structural-gain proxy. Plus structurally_promising decision and
the empty-cases estimated_fallback_delta semantics.

Pinned invariants:

- select_replay_cases:
  - None / empty manifest -> [] (no exception).
  - Filters cases whose source_tension_id or source_curiosity_id
    match the request; non-matches dropped.
  - Dedupes by query_hash; if no query_hash, dedupes by
    replay_case_id. FIRST occurrence wins (later duplicates
    silently skipped).
  - Sorted by replay_case_id (audit determinism).
  - Bounded by max_cases parameter; default MAX_REPLAY_CASES.
- _shadow_capability_present (via evaluate_case):
  - suspected_missing_capability matching diff.new_nodes -> True.
  - bridge_candidate_refs matching diff.new_bridge_candidates ->
    True.
  - No matching tag -> False.
- evaluate_case:
  - Tagged + capability present -> structural_gain=True,
    regression=False.
  - Tagged + no capability present -> structural_gain=False,
    regression=False ("no matching tag or no shadow capability").
  - Untagged case -> structural_gain=False, regression=False
    ("case has no missing-capability tag").
  - Protected case with original_resolution_hash absent from
    shadow -> regression=False with "shadow does not remove live
    solvers" rationale (current scaffold semantic).
- build_report:
  - replay_case_count==0 -> estimated_fallback_delta=0.0 with
    undefined_reason set.
  - replay_case_count>0 -> estimated_fallback_delta is gain/n
    rounded to 6 decimals; undefined_reason None.
  - structurally_promising True only if collapse_passed AND
    gain>0 AND regressions==0 AND gain/max(targeted,1) >=
    MIN_GAIN_RATIO.
  - tension_ids_targeted is sorted+deduped tuple.
- emit_replay_case_manifest writes deterministically sorted JSON.
"""
from __future__ import annotations

import json
from pathlib import Path

from waggledance.core.dreaming import MIN_GAIN_RATIO, MAX_REPLAY_CASES
from waggledance.core.dreaming.replay import (
    REPLAY_METHODOLOGY,
    CaseEvaluation,
    ReplayCase,
    build_report,
    case_from_dict,
    emit_replay_case_manifest,
    evaluate_case,
    report_to_dict,
    select_replay_cases,
)
from waggledance.core.dreaming.shadow_graph import StructuralDiff, build_live_graph


# --- helpers -------------------------------------------------------

def _case(
    replay_case_id: str = "RC-1",
    source_tension_id: str | None = None,
    source_curiosity_id: str | None = None,
    query_hash: str | None = None,
    suspected_missing_capability: str | None = None,
    bridge_candidate_refs: tuple[str, ...] = (),
    protect: bool = False,
    original_resolution_hash: str | None = None,
    candidate_cell: str | None = None,
) -> ReplayCase:
    return ReplayCase(
        replay_case_id=replay_case_id,
        source_curiosity_id=source_curiosity_id,
        source_tension_id=source_tension_id,
        source_file=None,
        query_hash=query_hash,
        candidate_cell=candidate_cell,
        tags=(),
        protect=protect,
        original_resolution_hash=original_resolution_hash,
        suspected_missing_capability=suspected_missing_capability,
        bridge_candidate_refs=bridge_candidate_refs,
    )


def _diff(
    new_nodes: tuple[str, ...] = (),
    new_structural_edges: tuple = (),
    new_bridge_candidates: tuple = (),
    new_rescale_opportunities: tuple = (),
    affected_cells: tuple[str, ...] = (),
    proposal_solver_hashes: tuple[str, ...] = (),
) -> StructuralDiff:
    return StructuralDiff(
        new_nodes=new_nodes,
        new_structural_edges=new_structural_edges,
        new_bridge_candidates=new_bridge_candidates,
        new_rescale_opportunities=new_rescale_opportunities,
        affected_cells=affected_cells,
        proposal_solver_hashes=proposal_solver_hashes,
    )


def _empty_graph():
    return build_live_graph([])


# --- case_from_dict ----------------------------------------------

def test_case_from_dict_carries_all_fields():
    raw = {
        "replay_case_id": "RC-7",
        "source_curiosity_id": "C-1",
        "source_tension_id": "T-1",
        "source_file": "/tmp/x.py",
        "query_hash": "deadbeef",
        "candidate_cell": "cell:hex_a",
        "tags": ["foo", "bar"],
        "protect": True,
        "original_resolution_hash": "abc123",
        "suspected_missing_capability": "cap-x",
        "bridge_candidate_refs": ["b1", "b2"],
    }
    c = case_from_dict(raw)
    assert c.replay_case_id == "RC-7"
    assert c.source_tension_id == "T-1"
    assert c.tags == ("foo", "bar")
    assert c.protect is True
    assert c.bridge_candidate_refs == ("b1", "b2")


def test_case_from_dict_handles_missing_optional_fields():
    c = case_from_dict({"replay_case_id": "RC-1"})
    assert c.replay_case_id == "RC-1"
    assert c.source_tension_id is None
    assert c.tags == ()
    assert c.protect is False
    assert c.bridge_candidate_refs == ()


# --- select_replay_cases: None / empty -----------------------------

def test_select_replay_cases_none_manifest_returns_empty():
    assert select_replay_cases(None, ["T-1"], ["C-1"]) == []


def test_select_replay_cases_empty_manifest_returns_empty():
    assert select_replay_cases({}, ["T-1"], ["C-1"]) == []


def test_select_replay_cases_no_cases_key_returns_empty():
    """Manifest dict without 'cases' key must not crash."""
    assert select_replay_cases({"foo": "bar"}, ["T-1"], []) == []


# --- select_replay_cases: filter by tension/curiosity id ----------

def test_select_replay_cases_filters_to_matching_ids():
    manifest = {"cases": [
        {"replay_case_id": "RC-A", "source_tension_id": "T-1",
         "query_hash": "h1"},
        {"replay_case_id": "RC-B", "source_tension_id": "T-OTHER",
         "query_hash": "h2"},
        {"replay_case_id": "RC-C", "source_curiosity_id": "C-1",
         "query_hash": "h3"},
    ]}
    cases = select_replay_cases(manifest, ["T-1"], ["C-1"])
    ids = [c.replay_case_id for c in cases]
    # RC-B is dropped (no matching id), RC-A + RC-C kept.
    assert ids == ["RC-A", "RC-C"]


def test_select_replay_cases_returns_sorted_by_replay_case_id():
    """Output must be sorted alphabetically by replay_case_id for
    deterministic audit."""
    manifest = {"cases": [
        {"replay_case_id": "RC-Z", "source_tension_id": "T",
         "query_hash": "h-z"},
        {"replay_case_id": "RC-A", "source_tension_id": "T",
         "query_hash": "h-a"},
        {"replay_case_id": "RC-M", "source_tension_id": "T",
         "query_hash": "h-m"},
    ]}
    cases = select_replay_cases(manifest, ["T"], [])
    assert [c.replay_case_id for c in cases] == ["RC-A", "RC-M", "RC-Z"]


# --- select_replay_cases: dedup ----------------------------------

def test_select_replay_cases_dedupes_by_query_hash_first_wins():
    """Two cases with the same query_hash collapse to one — FIRST
    occurrence wins (the second instance is silently skipped)."""
    manifest = {"cases": [
        {"replay_case_id": "RC-FIRST", "source_tension_id": "T",
         "query_hash": "h-shared"},
        {"replay_case_id": "RC-SECOND", "source_tension_id": "T",
         "query_hash": "h-shared"},
    ]}
    cases = select_replay_cases(manifest, ["T"], [])
    assert len(cases) == 1
    assert cases[0].replay_case_id == "RC-FIRST"


def test_select_replay_cases_dedupes_by_replay_case_id_when_no_hash():
    """No query_hash -> dedupe key is replay_case_id."""
    manifest = {"cases": [
        {"replay_case_id": "RC-1", "source_tension_id": "T"},
        {"replay_case_id": "RC-1", "source_tension_id": "T"},  # dup
        {"replay_case_id": "RC-2", "source_tension_id": "T"},
    ]}
    cases = select_replay_cases(manifest, ["T"], [])
    assert [c.replay_case_id for c in cases] == ["RC-1", "RC-2"]


def test_select_replay_cases_no_dedup_when_distinct_hashes():
    """Distinct query_hashes -> all cases preserved (after sort)."""
    manifest = {"cases": [
        {"replay_case_id": "RC-A", "source_tension_id": "T",
         "query_hash": "h1"},
        {"replay_case_id": "RC-B", "source_tension_id": "T",
         "query_hash": "h2"},
    ]}
    cases = select_replay_cases(manifest, ["T"], [])
    assert len(cases) == 2


# --- select_replay_cases: max_cases bound ------------------------

def test_select_replay_cases_bounded_by_max_cases():
    """Output truncates to max_cases parameter."""
    manifest = {"cases": [
        {"replay_case_id": f"RC-{i:02d}", "source_tension_id": "T",
         "query_hash": f"h-{i}"}
        for i in range(20)
    ]}
    cases = select_replay_cases(manifest, ["T"], [], max_cases=5)
    assert len(cases) == 5
    # truncation comes after sort, so first 5 by id
    assert [c.replay_case_id for c in cases] == [
        "RC-00", "RC-01", "RC-02", "RC-03", "RC-04",
    ]


def test_select_replay_cases_default_max_is_module_constant():
    """Default max_cases respects MAX_REPLAY_CASES (50)."""
    assert MAX_REPLAY_CASES == 50  # contract pinned to constant
    manifest = {"cases": [
        {"replay_case_id": f"RC-{i:03d}", "source_tension_id": "T",
         "query_hash": f"h-{i}"}
        for i in range(MAX_REPLAY_CASES + 10)
    ]}
    cases = select_replay_cases(manifest, ["T"], [])
    assert len(cases) == MAX_REPLAY_CASES


# --- evaluate_case: structural-gain matching ---------------------

def test_evaluate_case_structural_gain_when_capability_in_new_nodes():
    case = _case(
        replay_case_id="RC-cap",
        source_tension_id="T",
        suspected_missing_capability="cap-x",
    )
    diff = _diff(new_nodes=("cap-x",))
    ev = evaluate_case(case, _empty_graph(), _empty_graph(), diff)
    assert ev.structural_gain is True
    assert ev.regression is False
    assert "shadow capability" in ev.rationale


def test_evaluate_case_structural_gain_when_bridge_ref_in_new_bridges():
    """A case's bridge_candidate_refs matching a new bridge in the
    diff counts as gain — the shadow graph offers a bridge the case
    flagged as needed."""
    case = _case(
        replay_case_id="RC-bridge",
        source_tension_id="T",
        bridge_candidate_refs=("solver_x",),
    )
    diff = _diff(
        new_bridge_candidates=(("solver_x", "solver_y", "composes_with"),),
    )
    ev = evaluate_case(case, _empty_graph(), _empty_graph(), diff)
    assert ev.structural_gain is True


def test_evaluate_case_no_gain_when_tagged_but_capability_absent():
    case = _case(
        replay_case_id="RC-miss",
        source_tension_id="T",
        suspected_missing_capability="cap-not-in-diff",
    )
    diff = _diff(new_nodes=("some-other-cap",))
    ev = evaluate_case(case, _empty_graph(), _empty_graph(), diff)
    assert ev.structural_gain is False
    assert ev.regression is False
    assert "no matching tag or no shadow capability" in ev.rationale


def test_evaluate_case_untagged_case_returns_no_capability_tag_rationale():
    case = _case(replay_case_id="RC-untagged", source_tension_id="T")
    diff = _diff()
    ev = evaluate_case(case, _empty_graph(), _empty_graph(), diff)
    assert ev.structural_gain is False
    assert ev.regression is False
    assert "no missing-capability tag" in ev.rationale


def test_evaluate_case_protected_with_hash_does_not_regress():
    """Protected case where original_resolution_hash is absent from
    the shadow's solver hashes => no regression. Current scaffold
    semantic: 'shadow does not remove live solvers'."""
    case = _case(
        replay_case_id="RC-protected",
        source_tension_id="T",
        protect=True,
        original_resolution_hash="hash-original",
    )
    diff = _diff()
    ev = evaluate_case(case, _empty_graph(), _empty_graph(), diff)
    assert ev.regression is False
    assert "shadow does not remove live solvers" in ev.rationale


# --- build_report: empty cases path ------------------------------

def test_build_report_empty_cases_zero_delta_with_undefined_reason():
    """replay_case_count=0 -> estimated_fallback_delta=0.0 AND
    estimated_fallback_delta_undefined_reason is set
    (audit-explicit "metric is undefined")."""
    live = _empty_graph()
    shadow = _empty_graph()
    diff = _diff()
    report = build_report(
        cases=[], live=live, shadow=shadow, diff=diff,
        branch_name="b", base_commit_hash="h",
        pinned_input_manifest_sha256="f" * 64,
        replay_manifest_sha256=None,
        tension_ids_targeted=[],
        collapse_passed=True,
    )
    assert report.replay_case_count == 0
    assert report.estimated_fallback_delta == 0.0
    assert report.estimated_fallback_delta_undefined_reason is not None
    assert "undefined" in report.estimated_fallback_delta_undefined_reason
    # No gain, no targeted; structurally_promising must be False
    # because gain==0 short-circuits the rule.
    assert report.structurally_promising is False


# --- build_report: estimated_fallback_delta computation ----------

def test_build_report_estimated_fallback_delta_is_gain_over_count():
    """Non-empty case set: estimated_fallback_delta = gain/n
    rounded to 6 decimals."""
    diff = _diff(new_nodes=("cap-x",))
    cases = [
        _case("RC-1", source_tension_id="T",
                suspected_missing_capability="cap-x"),  # gain
        _case("RC-2", source_tension_id="T"),  # untagged, no gain
        _case("RC-3", source_tension_id="T"),  # untagged, no gain
    ]
    report = build_report(
        cases=cases, live=_empty_graph(),
        shadow=_empty_graph(), diff=diff,
        branch_name="b", base_commit_hash="h",
        pinned_input_manifest_sha256="f" * 64,
        replay_manifest_sha256=None,
        tension_ids_targeted=["T"],
        collapse_passed=True,
    )
    assert report.replay_case_count == 3
    assert report.structural_gain_count == 1
    assert report.estimated_fallback_delta == round(1 / 3, 6)
    assert report.estimated_fallback_delta_undefined_reason is None


# --- build_report: structurally_promising rule -------------------

def test_structurally_promising_requires_collapse_passed():
    """Even with full gain and no regressions, collapse_passed=False
    blocks structurally_promising."""
    diff = _diff(new_nodes=("cap",))
    cases = [_case("RC", source_tension_id="T",
                       suspected_missing_capability="cap")]
    report = build_report(
        cases=cases, live=_empty_graph(),
        shadow=_empty_graph(), diff=diff,
        branch_name="b", base_commit_hash="h",
        pinned_input_manifest_sha256="f" * 64,
        replay_manifest_sha256=None,
        tension_ids_targeted=["T"],
        collapse_passed=False,
    )
    assert report.structural_gain_count == 1
    assert report.structurally_promising is False


def test_structurally_promising_requires_gain_above_min_ratio():
    """gain/max(targeted,1) must be >= MIN_GAIN_RATIO. With 1 gain
    and many targeted untagged cases, the ratio MUST clear
    MIN_GAIN_RATIO. If gain ratio is too low, NOT promising."""
    diff = _diff(new_nodes=("cap",))
    # 1 case, capability matches => gain=1, targeted=1, ratio=1.0
    cases = [_case("RC-A", source_tension_id="T",
                       suspected_missing_capability="cap")]
    report = build_report(
        cases=cases, live=_empty_graph(),
        shadow=_empty_graph(), diff=diff,
        branch_name="b", base_commit_hash="h",
        pinned_input_manifest_sha256="f" * 64,
        replay_manifest_sha256=None,
        tension_ids_targeted=["T"],
        collapse_passed=True,
    )
    assert report.structurally_promising is True
    assert (report.structural_gain_count
            / max(report.targeted_case_count, 1)) >= MIN_GAIN_RATIO


# --- build_report: tension_ids_targeted dedup+sort --------------

def test_build_report_tension_ids_targeted_sorted_and_deduped():
    """tension_ids_targeted is `tuple(sorted(set(...)))` — dedup
    and sort guaranteed."""
    report = build_report(
        cases=[], live=_empty_graph(),
        shadow=_empty_graph(), diff=_diff(),
        branch_name="b", base_commit_hash="h",
        pinned_input_manifest_sha256="f" * 64,
        replay_manifest_sha256=None,
        tension_ids_targeted=["T-z", "T-a", "T-z", "T-m"],
        collapse_passed=True,
    )
    assert report.tension_ids_targeted == ("T-a", "T-m", "T-z")


# --- build_report: methodology disclaimer pinned ------------------

def test_build_report_carries_methodology_constants():
    """Methodology + disclaimer constants must propagate so audit
    consumers know the report came from the structural-proxy
    scaffold (vs a future execution-based replay)."""
    report = build_report(
        cases=[], live=_empty_graph(),
        shadow=_empty_graph(), diff=_diff(),
        branch_name="b", base_commit_hash="h",
        pinned_input_manifest_sha256="f" * 64,
        replay_manifest_sha256=None,
        tension_ids_targeted=[],
        collapse_passed=True,
    )
    assert report.replay_methodology == REPLAY_METHODOLOGY
    assert "structural" in report.replay_methodology_disclaimer.lower()


# --- emit_replay_case_manifest determinism -----------------------

def test_emit_replay_case_manifest_writes_sorted_json(tmp_path):
    """Writes JSON file sorted by replay_case_id; output is
    deterministic across runs (audit-byte-stable)."""
    cases = [
        _case("RC-Z"),
        _case("RC-A"),
        _case("RC-M"),
    ]
    path = emit_replay_case_manifest(
        cases, tmp_path,
        branch_name="b", base_commit_hash="h",
        pinned_input_manifest_sha256="f" * 64,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    ids = [c["replay_case_id"] for c in payload["cases"]]
    assert ids == ["RC-A", "RC-M", "RC-Z"]
    assert payload["case_count"] == 3
    assert payload["max_replay_cases_cap"] == MAX_REPLAY_CASES


# --- report_to_dict round-trip -----------------------------------

def test_report_to_dict_carries_all_top_level_fields():
    """Smoke: report dict must carry the audit-load-bearing fields."""
    report = build_report(
        cases=[_case("RC", source_tension_id="T",
                          suspected_missing_capability="cap")],
        live=_empty_graph(), shadow=_empty_graph(),
        diff=_diff(new_nodes=("cap",)),
        branch_name="b", base_commit_hash="h",
        pinned_input_manifest_sha256="f" * 64,
        replay_manifest_sha256=None,
        tension_ids_targeted=["T"],
        collapse_passed=True,
    )
    d = report_to_dict(report)
    assert d["replay_methodology"] == REPLAY_METHODOLOGY
    assert d["replay_case_count"] == 1
    assert d["structural_gain_count"] == 1
    assert d["structurally_promising"] is True
    assert "case_evaluations" in d
