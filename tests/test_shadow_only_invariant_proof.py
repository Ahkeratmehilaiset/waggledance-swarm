"""Offline tests for tools/run_shadow_only_invariant_proof.py.

Covers the rco-1 structural + rco-2 empirical criteria: AFFIRMATIVE proof (absence
!= evidence), honest strict-int-0 transition count, fail-closed conjunction
re-derived from components, fake-transition detection, measurement-only (claim_safe
False / no authority), and path-free content-safe emission.
"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "run_shadow_only_invariant_proof",
    REPO_ROOT / "tools" / "run_shadow_only_invariant_proof.py",
)
mod = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(mod)  # type: ignore[union-attr]

from tools.hex_shadow_subdivision_replay import build_replay_artifact_for_root


def _real_artifact() -> dict:
    return build_replay_artifact_for_root(mod.ROOT)


def _proof(artifact):
    return mod.build_shadow_only_invariant_proof(
        artifact_factory=lambda: copy.deepcopy(artifact)
    )


def test_real_proof_invariant_holds():
    report = mod.build_shadow_only_invariant_proof()
    assert report["ok"] is True
    assert report["invariant"]["invariant_holds"] is True
    assert report["invariant"]["shadow_to_candidate_subdivision_transitions_total"] == 0
    assert report["invariant"]["claim_safe"] is False
    assert report["deterministic_replay"]["stable_identical"] is True


def test_strict_zero_int_helper():
    assert mod._strict_zero_int(0) is True
    for bad in (0.0, True, -1, 1, "0", None):
        assert mod._strict_zero_int(bad) is False, bad


@pytest.mark.parametrize("state", sorted(mod._CANDIDATE_TRANSITION_STATES))
def test_fake_candidate_target_state_violates(state):
    a = _real_artifact()
    a["shadow_plan_summary"]["target_state"] = state
    r = _proof(a)
    assert r["invariant"]["invariant_holds"] is False, state
    assert r["invariant"]["transition_occurred"] is True, state
    assert r["invariant"]["shadow_to_candidate_subdivision_transitions_total"] != 0, state
    assert "shadow_to_candidate_transition_detected" in r["blockers"], state


@pytest.mark.parametrize("key", list(mod._PROMOTION_RECORD_KEYS))
def test_promotion_record_violates(key):
    a = _real_artifact()
    a[key] = {"x": 1}
    r = _proof(a)
    assert r["invariant"]["invariant_holds"] is False, key
    assert r["invariant"]["transition_occurred"] is True, key


@pytest.mark.parametrize("location", ["artifact", "plan"])
@pytest.mark.parametrize("key", list(mod._OBSERVED_TRANSITION_COUNT_KEYS))
@pytest.mark.parametrize("bad", [1, 2, -1, 0.0, True, "0", [0], None])
def test_injected_transition_count_violation_fails_closed(location, key, bad):
    # codex-tools-1 #1275 forge: an explicit injected transition-count field at the
    # artifact top level OR in shadow_plan_summary that is not a strict int 0 must
    # FAIL CLOSED - the proof must NEVER coerce injected count evidence back to a
    # clean 0 / invariant_holds True.
    a = _real_artifact()
    target = a if location == "artifact" else a["shadow_plan_summary"]
    target[key] = bad
    r = _proof(a)
    assert r["ok"] is False, (location, key, bad)
    assert r["invariant"]["invariant_holds"] is False, (location, key, bad)
    assert r["invariant"]["injected_transition_count_violation"] is True, (location, key, bad)
    assert r["invariant"]["shadow_to_candidate_subdivision_transitions_total"] != 0
    assert "injected_transition_count_not_strict_zero" in r["blockers"]


@pytest.mark.parametrize("location", ["artifact", "plan"])
@pytest.mark.parametrize("key", list(mod._OBSERVED_TRANSITION_COUNT_KEYS))
def test_injected_strict_zero_count_stays_proven(location, key):
    # A legitimately-present STRICT int 0 count is consistent with the invariant
    # (absence != evidence, and an explicit honest 0 must not be punished).
    a = _real_artifact()
    target = a if location == "artifact" else a["shadow_plan_summary"]
    target[key] = 0
    r = _proof(a)
    assert r["ok"] is True, (location, key)
    assert r["invariant"]["invariant_holds"] is True, (location, key)
    assert r["invariant"]["injected_transition_count_violation"] is False


def test_missing_target_state_not_proven():
    # Absence != evidence of absence: a missing target_state must NOT pass.
    a = _real_artifact()
    a["shadow_plan_summary"].pop("target_state", None)
    r = _proof(a)
    assert r["invariant"]["invariant_holds"] is False
    assert r["invariant"]["target_state_is_shadow"] is False
    assert "target_state_not_shadow" in r["blockers"]


@pytest.mark.parametrize("axis", sorted(mod._EXPECTED_GUARDRAIL_AXES))
def test_missing_guardrail_axis_not_clean(axis):
    a = _real_artifact()
    a["guardrails"].pop(axis, None)
    r = _proof(a)
    assert r["invariant"]["guardrails_all_clean"] is False, axis
    assert r["invariant"]["invariant_holds"] is False, axis


@pytest.mark.parametrize("axis", [
    "runtime_authority_changed", "operator_gate_required",
    "external_writes_applied", "dispatch_controls_added",
    "network_transport_added", "raw_query_or_payload_included",
    "runtime_config_contents_included", "local_paths_recorded",
    "numeric_equality_to_shadow_children_claimed",
])
def test_bad_guardrail_axis_violates(axis):
    # Any "bad" guardrail axis True breaks the invariant (full-axis, data-driven).
    a = _real_artifact()
    a["guardrails"][axis] = True
    r = _proof(a)
    assert r["invariant"]["guardrails_all_clean"] is False, axis
    assert r["invariant"]["invariant_holds"] is False, axis


def test_positive_guardrail_false_violates():
    a = _real_artifact()
    a["guardrails"]["no_runtime_topology_mutation"] = False
    r = _proof(a)
    assert r["invariant"]["guardrails_all_clean"] is False
    assert r["invariant"]["invariant_holds"] is False


def test_non_bool_guardrail_not_clean():
    a = _real_artifact()
    a["guardrails"]["external_writes_applied"] = "false"
    r = _proof(a)
    assert r["invariant"]["guardrails_all_clean"] is False


def test_no_runtime_mutation_false_violates():
    a = _real_artifact()
    a["shadow_plan_summary"]["no_runtime_mutation"] = False
    r = _proof(a)
    assert r["invariant"]["invariant_holds"] is False
    assert "runtime_mutation_present" in r["blockers"]


def test_artifact_not_ok_violates():
    a = _real_artifact()
    a["ok"] = False
    r = _proof(a)
    assert r["invariant"]["invariant_holds"] is False
    assert "artifact_not_ok" in r["blockers"]


def test_nondeterministic_fails_closed():
    seq = [_real_artifact(), {**_real_artifact(), "ok": False}]

    def _drift():
        return seq.pop(0) if seq else _real_artifact()

    r = mod.build_shadow_only_invariant_proof(artifact_factory=_drift)
    assert r["ok"] is False
    assert "non_deterministic_invariant" in r["blockers"]
    assert r["invariants"]["deterministic_offline"] is False


def test_measurement_only_no_authority_keys():
    report = mod.build_shadow_only_invariant_proof()
    blob = json.dumps(report)
    assert report["invariant"]["claim_safe"] is False
    assert report["invariants"]["measurement_only_no_authority"] is True
    # never surfaces a satisfied/current_value/authority-granted gate field
    for forbidden in ('"satisfied"', '"current_value"', "runtime_authority_granted\": true",
                      "claim_safe\": true"):
        assert forbidden not in blob


def test_content_safe_no_raw_paths():
    # Even if the artifact carried a raw path, the report emits only derived
    # bools/ints, so no path survives.
    a = _real_artifact()
    a["shadow_plan_summary"]["plan_id"] = "C:" + "\\" + "secret" + "\\plan.json"
    r = _proof(a)
    blob = json.dumps(r)
    assert "secret" not in blob
    assert str(REPO_ROOT) not in blob
    assert mod._contains_path_marker(r) is False
    # only safe scalars in the invariant block
    for value in r["invariant"].values():
        assert isinstance(value, (bool, int)), value


def test_render_summary_and_json_vocabulary_clean():
    report = mod.build_shadow_only_invariant_proof()
    mod.assert_vocabulary_clean(mod.render_summary(report))
    mod.assert_vocabulary_clean(json.dumps(report))


def test_main_exit0():
    assert mod.main([]) == 0
