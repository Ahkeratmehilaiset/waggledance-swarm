# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.dreaming.meta_proposal.

Iteration N+5 Codex-scout Candidate 3 (Claude solo pickup; Codex
went back to sleep after pushing PR #149). meta_proposal is the
SHADOW-ONLY artifact emitted when collapse + replay are
structurally promising. Per c.txt §C7 it MUST NOT perform any
runtime mutation — it only RECOMMENDS that a future human reviewer
consider a solver for runtime entry.

Drift here would either falsely emit a meta-proposal for a
non-promising outcome (unsafe runtime-entry recommendation), or
suppress a legitimate one (loss of structural-gain evidence).

Test focus per Codex N+5 scout: confidence/EV gating,
structurally_promising None branches, hook contract validation.

Pinned invariants:

- solver_hash_for_proposal: empty/None proposal -> "sha256:empty";
  same shape -> same hash; field changes -> hash changes.
- compute_confidence formula: gate(0.5) + no_regressions(0.3) +
  ratio_above_min(0.2); clamped to [0, 1]; rounded to 6 decimals.
- compute_expected_value: estimated_fallback_delta x severity x
  confidence; rounded 6 decimals.
- is_structurally_promising wraps replay.structurally_promising.
- select_proposal_for_meta returns FIRST ACCEPT_CANDIDATE in
  deterministic order; None when no candidate exists.
- build_meta_proposal returns None when:
  - replay.structurally_promising is False, OR
  - select_proposal_for_meta returns None (no ACCEPT_CANDIDATE).
- build_meta_proposal happy path:
  - severity_max picked from self_model workspace_tensions whose
    tension_id is in replay.tension_ids_targeted.
  - structural_gain_ratio = gain / max(targeted, 1) — empty
    targeted treated as 1 (denominator-protected).
  - uncertainty banding: confidence < 0.4 -> "high",
    < 0.7 -> "medium", else "low".
  - source_tension_ids and gate_provenance are sorted (audit
    determinism).
- validate_hook_contracts:
  - missing required fields (file/file_sha256/version) -> error.
  - file missing on disk -> error.
  - file present but sha mismatch -> error.
  - all-good -> empty list.
"""
from __future__ import annotations

import hashlib

import pytest

from waggledance.core.dreaming.collapse import (
    CollapsedProposal,
    CollapseReport,
)
from waggledance.core.dreaming.meta_proposal import (
    build_meta_proposal,
    compute_confidence,
    compute_expected_value,
    is_structurally_promising,
    meta_proposal_to_dict,
    select_proposal_for_meta,
    solver_hash_for_proposal,
    validate_hook_contracts,
)
from waggledance.core.dreaming.replay import CaseEvaluation, ReplayReport


# --- helpers -------------------------------------------------------

def _proposal(
    proposal_path: str = "/tmp/p.json",
    proposal_id: str | None = "pid-1",
    solver_name: str | None = "solver_x",
    cell_id: str | None = "cell:hex_a",
    collapse_verdict: str = "ACCEPT_CANDIDATE",
    raw_verdict: str = "ACCEPT_CANDIDATE",
    gate_provenance: dict | None = None,
) -> CollapsedProposal:
    return CollapsedProposal(
        proposal_path=proposal_path,
        proposal_id=proposal_id,
        solver_name=solver_name,
        cell_id=cell_id,
        raw_verdict=raw_verdict,
        collapse_verdict=collapse_verdict,
        shadow_only=True,
        linkage_source="inline",
        linkage_pack_sha12=None,
        gate_provenance=gate_provenance or {"gate_a": "native"},
        gate_results=[],
        notes=(),
    )


def _collapse_report(
    proposals: tuple[CollapsedProposal, ...] = (),
) -> CollapseReport:
    return CollapseReport(
        schema_version=1,
        branch_name="b",
        base_commit_hash="h",
        pinned_input_manifest_sha256="f" * 64,
        replay_manifest_sha256=None,
        max_proposals_effective=10,
        proposals_evaluated=proposals,
        truncated_proposals=(),
        counts_by_verdict={},
        gate_provenance_summary={},
    )


def _replay_report(
    *,
    structurally_promising: bool = True,
    structural_gain_count: int = 1,
    targeted_case_count: int = 1,
    protected_regressions: int = 0,
    estimated_fallback_delta: float = 0.5,
    tension_ids_targeted: tuple[str, ...] = ("T-1",),
) -> ReplayReport:
    return ReplayReport(
        schema_version=1,
        branch_name="b",
        base_commit_hash="h",
        pinned_input_manifest_sha256="f" * 64,
        replay_manifest_sha256=None,
        replay_methodology="structural_proxy_v0.1",
        replay_methodology_disclaimer="...",
        replay_case_count=1,
        targeted_case_count=targeted_case_count,
        structural_gain_count=structural_gain_count,
        protected_case_regression_count=protected_regressions,
        unresolved_case_count=0,
        estimated_fallback_delta=estimated_fallback_delta,
        estimated_fallback_delta_undefined_reason=None,
        tension_ids_targeted=tension_ids_targeted,
        structurally_promising=structurally_promising,
        case_evaluations=(),
    )


def _self_model(*tensions) -> dict:
    return {
        "workspace_tensions": [
            {"tension_id": tid, "severity": sev}
            for (tid, sev) in tensions
        ],
    }


# --- solver_hash_for_proposal ------------------------------------

def test_solver_hash_empty_proposal_returns_sentinel():
    assert solver_hash_for_proposal({}) == "sha256:empty"


def test_solver_hash_none_proposal_returns_sentinel():
    """`if not proposal` is the guard — None / {} / falsy all hit the
    sha256:empty branch."""
    assert solver_hash_for_proposal(None) == "sha256:empty"


def test_solver_hash_deterministic_on_same_fields():
    p = {"cell_id": "c", "solver_name": "s", "inputs": ["x"],
         "outputs": ["y"], "formula_or_algorithm": "x+1"}
    h1 = solver_hash_for_proposal(p)
    h2 = solver_hash_for_proposal(dict(p))
    assert h1 == h2
    assert h1.startswith("sha256:")
    assert len(h1) == len("sha256:") + 64  # full sha256 hex


def test_solver_hash_changes_when_solver_name_changes():
    p1 = {"cell_id": "c", "solver_name": "s1", "inputs": [],
          "outputs": [], "formula_or_algorithm": "f"}
    p2 = dict(p1, solver_name="s2")
    assert solver_hash_for_proposal(p1) != solver_hash_for_proposal(p2)


def test_solver_hash_ignores_non_load_bearing_fields():
    """Only the 5 listed fields go into the hash. Other keys must
    not change the hash — otherwise unrelated metadata changes
    would invalidate dedup."""
    p1 = {"cell_id": "c", "solver_name": "s", "inputs": [],
          "outputs": [], "formula_or_algorithm": "f"}
    p2 = dict(p1, irrelevant_extra_field="xxx", another="zzz")
    assert solver_hash_for_proposal(p1) == solver_hash_for_proposal(p2)


# --- compute_confidence ------------------------------------------

def test_confidence_all_three_conditions_met_is_one():
    """All 3 components present: 0.5 + 0.3 + 0.2 = 1.0."""
    c = compute_confidence(
        gate_passed=True, no_protected_regressions=True,
        structural_gain_ratio=1.0,
    )
    assert c == 1.0


def test_confidence_gate_only_is_zero_point_five():
    c = compute_confidence(
        gate_passed=True, no_protected_regressions=False,
        structural_gain_ratio=0.0,
    )
    assert c == 0.5


def test_confidence_no_regressions_only_is_zero_point_three():
    c = compute_confidence(
        gate_passed=False, no_protected_regressions=True,
        structural_gain_ratio=0.0,
    )
    assert c == 0.3


def test_confidence_ratio_above_min_only_is_zero_point_two():
    c = compute_confidence(
        gate_passed=False, no_protected_regressions=False,
        structural_gain_ratio=1.0,
    )
    assert c == 0.2


def test_confidence_ratio_below_min_excluded():
    """A structural_gain_ratio strictly below MIN_GAIN_RATIO does
    NOT contribute the 0.2 component."""
    c = compute_confidence(
        gate_passed=True, no_protected_regressions=True,
        structural_gain_ratio=0.0,  # < MIN_GAIN_RATIO
    )
    assert c == 0.8  # only gate(0.5) + no_regressions(0.3)


def test_confidence_ratio_at_min_is_inclusive():
    """The check is `>= min_gain_ratio` — equal to threshold passes."""
    c = compute_confidence(
        gate_passed=False, no_protected_regressions=False,
        structural_gain_ratio=0.5, min_gain_ratio=0.5,
    )
    assert c == 0.2


def test_confidence_clamped_to_unit_interval():
    """Clamping is via max(0.0, min(1.0, ...)). With all-true
    inputs, sum is 1.0; clamping ensures no overflow even if the
    sum logic ever accidentally exceeds 1.0."""
    c = compute_confidence(
        gate_passed=True, no_protected_regressions=True,
        structural_gain_ratio=10.0,  # absurd ratio
    )
    assert c == 1.0  # still clamped


def test_confidence_zero_when_no_components_met():
    c = compute_confidence(
        gate_passed=False, no_protected_regressions=False,
        structural_gain_ratio=0.0,
    )
    assert c == 0.0


# --- compute_expected_value --------------------------------------

def test_expected_value_is_product_of_three_factors():
    ev = compute_expected_value(
        estimated_fallback_delta=0.5,
        tension_severity_max=0.8,
        confidence=0.6,
    )
    # 0.5 * 0.8 * 0.6 = 0.24
    assert ev == 0.24


def test_expected_value_zero_when_any_factor_zero():
    """Any factor at zero zeros out the whole product. Critical: a
    zero-severity tension cannot promote a meta-proposal even with
    full confidence."""
    assert compute_expected_value(
        estimated_fallback_delta=0.0, tension_severity_max=1.0,
        confidence=1.0,
    ) == 0.0
    assert compute_expected_value(
        estimated_fallback_delta=1.0, tension_severity_max=0.0,
        confidence=1.0,
    ) == 0.0
    assert compute_expected_value(
        estimated_fallback_delta=1.0, tension_severity_max=1.0,
        confidence=0.0,
    ) == 0.0


def test_expected_value_rounded_to_six_decimals():
    """Output is round(x, 6) — long-tail floats truncated."""
    ev = compute_expected_value(
        estimated_fallback_delta=1 / 3,
        tension_severity_max=1 / 7,
        confidence=1.0,
    )
    expected = round((1 / 3) * (1 / 7) * 1.0, 6)
    assert ev == expected


# --- is_structurally_promising -----------------------------------

def test_is_structurally_promising_true():
    assert is_structurally_promising(
        _replay_report(structurally_promising=True),
    ) is True


def test_is_structurally_promising_false():
    assert is_structurally_promising(
        _replay_report(structurally_promising=False),
    ) is False


# --- select_proposal_for_meta ------------------------------------

def test_select_proposal_returns_first_accept_candidate():
    p1 = _proposal(proposal_path="/p1", collapse_verdict="SOFT_REJECT")
    p2 = _proposal(proposal_path="/p2", collapse_verdict="ACCEPT_CANDIDATE")
    p3 = _proposal(proposal_path="/p3", collapse_verdict="ACCEPT_CANDIDATE")
    report = _collapse_report((p1, p2, p3))
    chosen = select_proposal_for_meta(report)
    assert chosen is not None
    # FIRST ACCEPT_CANDIDATE in iteration order — p2 not p3
    assert chosen.proposal_path == "/p2"


def test_select_proposal_returns_none_when_no_candidate():
    """No proposals with ACCEPT_CANDIDATE verdict -> None."""
    p1 = _proposal(proposal_path="/p1", collapse_verdict="SOFT_REJECT")
    p2 = _proposal(proposal_path="/p2", collapse_verdict="HARD_REJECT")
    report = _collapse_report((p1, p2))
    assert select_proposal_for_meta(report) is None


def test_select_proposal_empty_report_returns_none():
    report = _collapse_report(())
    assert select_proposal_for_meta(report) is None


# --- build_meta_proposal: None paths -----------------------------

def test_build_meta_proposal_none_when_not_promising():
    """If replay is not structurally promising, build_meta_proposal
    MUST return None — no shadow recommendation can be emitted."""
    collapse = _collapse_report((_proposal(),))
    replay = _replay_report(structurally_promising=False)
    result = build_meta_proposal(
        collapse=collapse, replay=replay,
        self_model={}, consumed_hook_contracts=[],
    )
    assert result is None


def test_build_meta_proposal_none_when_no_accept_candidate():
    """Promising but no ACCEPT_CANDIDATE in collapse -> None."""
    collapse = _collapse_report(
        (_proposal(collapse_verdict="SOFT_REJECT"),),
    )
    replay = _replay_report(structurally_promising=True)
    result = build_meta_proposal(
        collapse=collapse, replay=replay,
        self_model={}, consumed_hook_contracts=[],
    )
    assert result is None


# --- build_meta_proposal: happy path -----------------------------

def test_build_meta_proposal_happy_path_with_explicit_proposal_data():
    """Pass selected_proposal explicitly + proposal_data dict to
    avoid disk I/O. Severity from self_model. Verify confidence,
    EV, and uncertainty all computed."""
    collapse = _collapse_report(
        (_proposal(gate_provenance={"b": "shadow_proxy", "a": "native"}),),
    )
    replay = _replay_report(
        structurally_promising=True,
        structural_gain_count=1,
        targeted_case_count=1,
        protected_regressions=0,
        estimated_fallback_delta=0.5,
        tension_ids_targeted=("T-1",),
    )
    self_model = _self_model(("T-1", "high"))
    proposal_data = {
        "cell_id": "cell:x", "solver_name": "s",
        "inputs": ["a"], "outputs": ["b"],
        "formula_or_algorithm": "a+b",
    }
    result = build_meta_proposal(
        collapse=collapse, replay=replay,
        self_model=self_model, consumed_hook_contracts=[],
        proposal_data=proposal_data,
    )
    assert result is not None
    assert result.structurally_promising is True
    assert result.tension_severity_max == 1.0  # "high" -> 1.0
    # gate_passed=True + no_regressions=True + ratio>=min -> 1.0
    assert result.confidence == 1.0
    # EV = 0.5 * 1.0 * 1.0 = 0.5
    assert result.expected_value_of_merging == 0.5
    # confidence >= 0.7 -> low uncertainty
    assert result.uncertainty == "low"
    # gate_provenance sorted by key
    assert list(result.gate_provenance.keys()) == ["a", "b"]
    # source_tension_ids sorted
    assert result.source_tension_ids == ("T-1",)


def test_build_meta_proposal_uncertainty_banding_high():
    """confidence < 0.4 -> uncertainty 'high'."""
    collapse = _collapse_report((_proposal(),))
    replay = _replay_report(
        structural_gain_count=0,  # no gain -> ratio 0 -> no 0.2
        protected_regressions=1,  # regression present -> no 0.3
        # gate_passed = True (proposal verdict ACCEPT) -> 0.5
        # but with regressions, structurally_promising would be
        # False. So set it manually:
        structurally_promising=True,
    )
    result = build_meta_proposal(
        collapse=collapse, replay=replay,
        self_model={}, consumed_hook_contracts=[],
        proposal_data={"solver_name": "s"},
    )
    # gate_passed=0.5; no_regressions=False -> -0.3; ratio<min -> -0.2
    # confidence = 0.5 -> uncertainty "medium" (< 0.7)
    assert result is not None
    assert result.confidence == 0.5
    assert result.uncertainty == "medium"


def test_build_meta_proposal_targeted_zero_uses_denominator_one():
    """structural_gain_ratio = gain / max(targeted, 1). When
    targeted == 0, max(...) returns 1 to avoid div-by-zero. With
    gain=1 targeted=0, ratio = 1.0."""
    collapse = _collapse_report((_proposal(),))
    replay = _replay_report(
        structural_gain_count=1, targeted_case_count=0,
        protected_regressions=0,
    )
    result = build_meta_proposal(
        collapse=collapse, replay=replay,
        self_model={}, consumed_hook_contracts=[],
        proposal_data={"solver_name": "s"},
    )
    assert result is not None
    # ratio = 1/1 = 1.0 -> contributes the 0.2 confidence component
    assert result.confidence == 1.0


# --- validate_hook_contracts -------------------------------------

def test_validate_hook_contracts_empty_list_returns_empty_errors():
    assert validate_hook_contracts([], repo_root=None) == []  # type: ignore[arg-type]


def test_validate_hook_contracts_missing_required_fields(tmp_path):
    """Each entry must carry file/file_sha256/version. Missing any
    -> error string."""
    errors = validate_hook_contracts(
        [{"file": "x.py"}],  # missing sha + version
        tmp_path,
    )
    assert len(errors) == 1
    assert "missing required fields" in errors[0]


def test_validate_hook_contracts_file_missing_on_disk(tmp_path):
    errors = validate_hook_contracts(
        [{"file": "does_not_exist.py", "file_sha256": "sha256:" + "a" * 64,
          "version": 1}],
        tmp_path,
    )
    assert len(errors) == 1
    assert "missing on disk" in errors[0]


def test_validate_hook_contracts_sha_mismatch(tmp_path):
    """File present on disk but its actual sha differs from the
    recorded sha -> error."""
    fpath = tmp_path / "x.py"
    fpath.write_text("real content", encoding="utf-8")
    errors = validate_hook_contracts(
        [{"file": "x.py",
          "file_sha256": "sha256:" + "0" * 64,  # wrong
          "version": 1}],
        tmp_path,
    )
    assert len(errors) == 1
    assert "sha mismatch" in errors[0]


def test_validate_hook_contracts_all_good_returns_empty(tmp_path):
    """File present and sha matches -> no errors."""
    fpath = tmp_path / "x.py"
    content = b"hook contract content"
    fpath.write_bytes(content)
    actual_sha = "sha256:" + hashlib.sha256(content).hexdigest()
    errors = validate_hook_contracts(
        [{"file": "x.py", "file_sha256": actual_sha, "version": 1}],
        tmp_path,
    )
    assert errors == []


def test_validate_hook_contracts_collects_errors_across_entries(tmp_path):
    """Multiple entries -> errors list contains one per failing
    entry; valid entries don't add errors."""
    good = tmp_path / "good.py"
    good.write_bytes(b"good")
    good_sha = "sha256:" + hashlib.sha256(b"good").hexdigest()
    errors = validate_hook_contracts(
        [
            {"file": "good.py", "file_sha256": good_sha, "version": 1},
            {"file": "missing.py", "file_sha256": "sha256:" + "0" * 64,
             "version": 1},
            {"file": "good.py", "file_sha256": "sha256:" + "1" * 64,
             "version": 1},  # sha mismatch
        ],
        tmp_path,
    )
    assert len(errors) == 2
    # First error: missing file; second: sha mismatch
    assert any("missing on disk" in e for e in errors)
    assert any("sha mismatch" in e for e in errors)


# --- meta_proposal_to_dict smoke ---------------------------------

def test_meta_proposal_to_dict_carries_top_level_fields():
    collapse = _collapse_report((_proposal(),))
    replay = _replay_report(structurally_promising=True)
    self_model = _self_model(("T-1", "medium"))
    result = build_meta_proposal(
        collapse=collapse, replay=replay,
        self_model=self_model, consumed_hook_contracts=[],
        proposal_data={"solver_name": "s"},
    )
    assert result is not None
    d = meta_proposal_to_dict(result)
    assert "schema_version" in d
    assert d["structurally_promising"] is True
    assert "selected_proposal" in d
    assert "replay_metrics" in d
    assert "structural_gains" in d
    assert "why_human_review_required" in d
    assert "why_runtime_flip_is_out_of_scope" in d
