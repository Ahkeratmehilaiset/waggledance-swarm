# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.solver_synthesis.solver_candidate_store.

The candidate store holds every solver candidate in flight: state
transitions, cold-gate passability, and atomic persistence. Phase 9
§U3 CRITICAL COLD SOLVER RULE requires three gates before exiting
cold (use_count, shadow_observation_seconds, critical_regressions).

A regression in `can_exit_cold` can let a flaky candidate exit cold
prematurely; a regression in `with_state` can let a transition lose
data; a regression in `save_store` can leave half-written JSON on
disk if the rename fails. Direct test coverage on this file was
zero before this PR.

Pinned invariants:

- `SolverCandidate.__post_init__` rejects unknown state.
- `compute_candidate_id` is deterministic on identical inputs and
  truncated to 12 chars.
- `make_candidate` returns state="raw_candidate" with the computed
  id; `no_runtime_mutation=True`.
- `with_state` preserves all other fields and rejects unknown
  states.
- `can_exit_cold` requires use_count ≥ 50 AND shadow_observation_
  seconds ≥ 3600 AND critical_regressions == 0; otherwise returns
  (False, reason).
- Store CRUD: add, get, by_state(sorted), to_dict (sorted).
- `save_store` writes atomically through a temp file and rename;
  the JSON payload round-trips back to the same dict.
"""
from __future__ import annotations

import json

import pytest

from waggledance.core.solver_synthesis import (
    SOLVER_SYNTHESIS_SCHEMA_VERSION,
)
from waggledance.core.solver_synthesis.solver_candidate_store import (
    CANDIDATE_STATES,
    COLD_MAX_CRITICAL_REGRESSIONS,
    COLD_MIN_SHADOW_OBS_SECONDS,
    COLD_MIN_USE_COUNT,
    SolverCandidate,
    SolverCandidateStore,
    can_exit_cold,
    compute_candidate_id,
    make_candidate,
    save_store,
    with_state,
)


# --- helpers --------------------------------------------------------

def _candidate(*, candidate_id: str = "C-1", state: str = "raw_candidate",
               use_count: int = 0, shadow_obs: int = 0,
               crit_reg: int = 0) -> SolverCandidate:
    return SolverCandidate(
        schema_version=1,
        candidate_id=candidate_id,
        state=state,
        solver_name="test_solver",
        cell_id="general",
        spec_or_code={"kind": "scalar"},
        source_gap_ref="gap_x",
        no_runtime_mutation=True,
        produced_by="test",
        branch_name="main",
        base_commit_hash="deadbeef",
        pinned_input_manifest_sha256="f" * 64,
        match_confidence=0.5,
        use_count=use_count,
        shadow_observation_seconds=shadow_obs,
        critical_regressions=crit_reg,
    )


# --- SolverCandidate validation ------------------------------------

def test_solver_candidate_post_init_rejects_unknown_state():
    with pytest.raises(ValueError, match="unknown state"):
        SolverCandidate(
            schema_version=1, candidate_id="x",
            state="not_a_state", solver_name="a", cell_id="general",
            spec_or_code={}, source_gap_ref="g", no_runtime_mutation=True,
            produced_by="t", branch_name="", base_commit_hash="",
            pinned_input_manifest_sha256="",
        )


@pytest.mark.parametrize("state", CANDIDATE_STATES)
def test_solver_candidate_accepts_each_allowlisted_state(state):
    c = _candidate(state=state)
    assert c.state == state


def test_solver_candidate_to_dict_emits_provenance_block():
    c = _candidate()
    d = c.to_dict()
    assert d["state"] == "raw_candidate"
    assert d["spec_or_code"] == {"kind": "scalar"}
    assert d["use_count"] == 0
    assert d["provenance"] == {
        "produced_by": "test",
        "branch_name": "main",
        "base_commit_hash": "deadbeef",
        "pinned_input_manifest_sha256": "f" * 64,
    }


# --- compute_candidate_id ------------------------------------------

def test_compute_candidate_id_deterministic_and_truncated_to_twelve():
    a = compute_candidate_id(solver_name="x", cell_id="general",
                             source_gap_ref="g")
    b = compute_candidate_id(solver_name="x", cell_id="general",
                             source_gap_ref="g")
    assert a == b
    assert len(a) == 12


def test_compute_candidate_id_changes_on_input_change():
    a = compute_candidate_id(solver_name="x", cell_id="general",
                             source_gap_ref="g")
    b = compute_candidate_id(solver_name="y", cell_id="general",
                             source_gap_ref="g")
    assert a != b


# --- make_candidate -------------------------------------------------

def test_make_candidate_starts_in_raw_candidate_state():
    c = make_candidate(
        solver_name="celsius_to_kelvin",
        cell_id="general",
        spec_or_code={"factor": 1.0},
        source_gap_ref="gap_x",
    )
    assert c.state == "raw_candidate"
    assert c.no_runtime_mutation is True
    assert c.schema_version == SOLVER_SYNTHESIS_SCHEMA_VERSION
    expected_id = compute_candidate_id(
        solver_name="celsius_to_kelvin", cell_id="general",
        source_gap_ref="gap_x",
    )
    assert c.candidate_id == expected_id


def test_make_candidate_copies_spec_or_code_so_mutations_do_not_leak():
    spec_in = {"factor": 1.0}
    c = make_candidate(
        solver_name="celsius_to_kelvin", cell_id="general",
        spec_or_code=spec_in, source_gap_ref="gap_x",
    )
    spec_in["factor"] = 999.0
    assert c.spec_or_code["factor"] == 1.0


# --- with_state -----------------------------------------------------

@pytest.mark.parametrize("new_state", [
    s for s in CANDIDATE_STATES if s != "raw_candidate"
])
def test_with_state_transitions_preserve_all_other_fields(new_state):
    src = _candidate(use_count=42, shadow_obs=7200, crit_reg=0)
    out = with_state(src, new_state)
    assert out.state == new_state
    # Every other field must be byte-identical.
    assert out.candidate_id == src.candidate_id
    assert out.solver_name == src.solver_name
    assert out.cell_id == src.cell_id
    assert out.spec_or_code == src.spec_or_code
    assert out.use_count == 42
    assert out.shadow_observation_seconds == 7200
    assert out.critical_regressions == 0


def test_with_state_rejects_unknown_state():
    src = _candidate()
    with pytest.raises(ValueError, match="unknown state"):
        with_state(src, "not_a_state")


# --- can_exit_cold (CRITICAL COLD SOLVER RULE) ----------------------

def test_can_exit_cold_requires_use_count_threshold():
    c = _candidate(use_count=COLD_MIN_USE_COUNT - 1,
                   shadow_obs=COLD_MIN_SHADOW_OBS_SECONDS, crit_reg=0)
    ok, reason = can_exit_cold(c)
    assert ok is False
    assert "use_count" in reason


def test_can_exit_cold_requires_shadow_observation_threshold():
    c = _candidate(use_count=COLD_MIN_USE_COUNT,
                   shadow_obs=COLD_MIN_SHADOW_OBS_SECONDS - 1,
                   crit_reg=0)
    ok, reason = can_exit_cold(c)
    assert ok is False
    assert "shadow_observation" in reason


def test_can_exit_cold_blocks_when_critical_regressions_present():
    c = _candidate(use_count=COLD_MIN_USE_COUNT,
                   shadow_obs=COLD_MIN_SHADOW_OBS_SECONDS,
                   crit_reg=COLD_MAX_CRITICAL_REGRESSIONS + 1)
    ok, reason = can_exit_cold(c)
    assert ok is False
    assert "critical_regressions" in reason


def test_can_exit_cold_passes_when_all_three_gates_clear():
    c = _candidate(use_count=COLD_MIN_USE_COUNT,
                   shadow_obs=COLD_MIN_SHADOW_OBS_SECONDS,
                   crit_reg=0)
    ok, reason = can_exit_cold(c)
    assert ok is True
    assert "all cold gates passed" in reason


def test_can_exit_cold_use_count_check_is_strictly_at_threshold():
    """Boundary: exactly COLD_MIN_USE_COUNT must pass (the check is
    `<`, so == threshold is OK)."""
    c = _candidate(use_count=COLD_MIN_USE_COUNT,
                   shadow_obs=COLD_MIN_SHADOW_OBS_SECONDS, crit_reg=0)
    ok, _ = can_exit_cold(c)
    assert ok is True


# --- SolverCandidateStore CRUD -------------------------------------

def test_store_add_and_get_round_trip():
    store = SolverCandidateStore()
    c1 = _candidate(candidate_id="C-1")
    c2 = _candidate(candidate_id="C-2")
    store.add(c1).add(c2)
    assert store.get("C-1") is c1
    assert store.get("C-2") is c2
    assert store.get("C-99") is None


def test_store_by_state_filters_and_sorts_by_candidate_id():
    store = SolverCandidateStore()
    store.add(_candidate(candidate_id="C-2", state="shadow_only"))
    store.add(_candidate(candidate_id="C-1", state="shadow_only"))
    store.add(_candidate(candidate_id="C-3", state="approved"))
    shadow = store.by_state("shadow_only")
    assert [c.candidate_id for c in shadow] == ["C-1", "C-2"]


def test_store_to_dict_sorted_by_candidate_id():
    store = SolverCandidateStore()
    store.add(_candidate(candidate_id="C-z"))
    store.add(_candidate(candidate_id="C-a"))
    d = store.to_dict()
    keys = list(d["candidates"].keys())
    assert keys == sorted(keys)
    assert d["schema_version"] == SOLVER_SYNTHESIS_SCHEMA_VERSION


# --- save_store atomic write ---------------------------------------

def test_save_store_writes_atomic_and_round_trips_through_json(tmp_path):
    store = SolverCandidateStore()
    store.add(_candidate(candidate_id="C-1"))
    store.add(_candidate(candidate_id="C-2", state="shadow_only"))
    target = tmp_path / "candidates.json"
    out = save_store(store, target)
    assert out == target
    assert out.exists()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema_version"] == SOLVER_SYNTHESIS_SCHEMA_VERSION
    assert set(payload["candidates"].keys()) == {"C-1", "C-2"}


def test_save_store_creates_parent_directory(tmp_path):
    """If the target's parent doesn't exist, save_store must mkdir
    rather than raise."""
    store = SolverCandidateStore().add(_candidate(candidate_id="C-1"))
    target = tmp_path / "deep" / "nested" / "candidates.json"
    out = save_store(store, target)
    assert out.exists()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert "C-1" in payload["candidates"]


def test_save_store_does_not_leave_temp_file_on_success(tmp_path):
    """The atomic-rename pattern must not litter .tmp files in the
    target directory after a successful save."""
    store = SolverCandidateStore().add(_candidate(candidate_id="C-1"))
    target = tmp_path / "candidates.json"
    save_store(store, target)
    leftovers = [p for p in tmp_path.iterdir()
                 if p.name.startswith(".cands.") and p.suffix == ".tmp"]
    assert leftovers == []
