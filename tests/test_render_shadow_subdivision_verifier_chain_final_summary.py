"""Offline tests for tools/render_shadow_subdivision_verifier_chain_final_summary.py.

Covers the RCO dual-lens criteria: STRUCTURAL (verifier-chain COMPLETENESS across all
levels, fail-closed per-verify-stage re-derivation from components, safe-scalar
allowlist emission, path-free/content-safe) and EMPIRICAL/FORGE (a connected
adversarial level/verdict the renderer actually consumes, asserted to fail CLOSED).
"""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path

import pytest

import tools.render_shadow_subdivision_verifier_chain_final_summary as mod

REPO_ROOT = Path(__file__).resolve().parent.parent

_VERIFY_KEYS = (mod._STAGE_A_KEY, mod._STAGE_B_KEY, mod._STAGE_C_KEY)
_INDEX_ENTRY_KEYS = (mod._STAGE_B_KEY, mod._STAGE_C_KEY)


@lru_cache(maxsize=1)
def _real_proof_cached() -> dict:
    from tools.wd_image1_capability_manifest import build_manifest

    manifest = build_manifest(REPO_ROOT)
    for capability in manifest.get("capabilities", []):
        if capability.get("capability_id") == "hexagonal_upgrades":
            return capability["proof"]
    raise AssertionError("hexagonal_upgrades proof not found")


def _real_proof() -> dict:
    return copy.deepcopy(_real_proof_cached())


def _summary(proof):
    return mod.render_chain_final_summary(proof)


def _flag_for(stage_key: str) -> str:
    return {
        mod._STAGE_A_KEY: "artifact_verify_clean",
        mod._STAGE_B_KEY: "index_entry_verify_clean",
        mod._STAGE_C_KEY: "deepest_verify_clean",
    }[stage_key]


# --------------------------------------------------------------------------- real

def test_real_chain_summary_clean():
    s = _summary(_real_proof())
    assert s["chain_clean"] is True
    assert s["chain_levels_all_ok"] is True
    assert s["chain_levels_present"] == s["chain_levels_total"] == len(mod._CHAIN_LEVEL_KEYS)
    assert s["chain_levels_ok"] == s["chain_levels_total"]
    assert s["artifact_verify_clean"] is True
    assert s["index_entry_verify_clean"] is True
    assert s["deepest_verify_clean"] is True
    assert s["path_free_verified"] is True
    assert s["total_blocker_count"] == 0


def test_main_exit0():
    assert mod.main([]) == 0
    assert mod.main(["--json"]) == 0


def test_summary_keys_within_allowlist_and_scalar():
    s = _summary(_real_proof())
    assert set(s) == mod._SUMMARY_SAFE_KEYS
    for key, value in s.items():
        if key == "report_version":
            assert isinstance(value, str)
        else:
            assert isinstance(value, (bool, int)), (key, value)


def test_vocabulary_and_json_clean():
    s = _summary(_real_proof())
    mod.assert_vocabulary_clean(mod.render_summary(s))
    mod.assert_vocabulary_clean(json.dumps(s))


# --------------------------------------------------- breadth / completeness (all 10)

@pytest.mark.parametrize("level_key", list(mod._CHAIN_LEVEL_KEYS))
def test_missing_any_level_fails_closed(level_key):
    proof = _real_proof()
    proof.pop(level_key, None)
    s = _summary(proof)
    assert s["chain_levels_all_ok"] is False, level_key
    assert s["chain_levels_present"] == s["chain_levels_total"] - 1
    assert s["chain_clean"] is False


@pytest.mark.parametrize("level_key", list(mod._CHAIN_LEVEL_KEYS))
def test_level_ok_false_fails_closed(level_key):
    proof = _real_proof()
    proof[level_key]["ok"] = False
    s = _summary(proof)
    assert s["chain_levels_all_ok"] is False, level_key
    assert s["chain_clean"] is False


@pytest.mark.parametrize("level_key", list(mod._CHAIN_LEVEL_KEYS))
@pytest.mark.parametrize("bad_ok", ["True", 1, None])
def test_level_non_strict_ok_fails_closed(level_key, bad_ok):
    proof = _real_proof()
    proof[level_key]["ok"] = bad_ok
    s = _summary(proof)
    assert s["chain_levels_all_ok"] is False, (level_key, bad_ok)
    assert s["chain_clean"] is False


@pytest.mark.parametrize("level_key", list(mod._CHAIN_LEVEL_KEYS))
def test_non_mapping_level_fails_closed(level_key):
    proof = _real_proof()
    proof[level_key] = "not-a-mapping"
    s = _summary(proof)
    assert s["chain_levels_all_ok"] is False, level_key
    assert s["chain_clean"] is False


# ------------------------------------------------------- depth: stage A (artifact)

@pytest.mark.parametrize("check_key", sorted(mod._EXPECTED_STAGE_A_CHECK_KEYS))
def test_stage_a_missing_expected_check_fails(check_key):
    proof = _real_proof()
    proof[mod._STAGE_A_KEY]["checks"].pop(check_key, None)
    s = _summary(proof)
    assert s["artifact_verify_clean"] is False, check_key
    assert s["chain_clean"] is False


def test_stage_a_check_false_fails():
    # ok stays True (breadth passes) but a component check is False -> depth catches it.
    proof = _real_proof()
    proof[mod._STAGE_A_KEY]["checks"]["artifact_path_free"] = False
    s = _summary(proof)
    assert s["artifact_verify_clean"] is False
    assert s["chain_clean"] is False


@pytest.mark.parametrize("bad_checks", [{}, "x", None, {"artifact_path_free": "true"}])
def test_stage_a_malformed_checks_fails(bad_checks):
    proof = _real_proof()
    proof[mod._STAGE_A_KEY]["checks"] = bad_checks
    s = _summary(proof)
    assert s["artifact_verify_clean"] is False, bad_checks


@pytest.mark.parametrize(
    "field,bad",
    [
        ("artifact_declared_ok", False),
        ("recomputed_contract_ok", False),
        ("blockers", ["boom"]),
        ("blockers", "not-a-list"),
    ],
)
def test_stage_a_component_violations_fail(field, bad):
    # leave ok=True so this exercises the DEEP re-derivation, not just breadth.
    proof = _real_proof()
    proof[mod._STAGE_A_KEY][field] = bad
    s = _summary(proof)
    assert s["artifact_verify_clean"] is False, (field, bad)
    assert s["chain_clean"] is False


# --------------------------------------------------- depth: stages B/C (index entry)

@pytest.mark.parametrize("stage_key", _INDEX_ENTRY_KEYS)
@pytest.mark.parametrize("field", list(mod._INDEX_ENTRY_AUTHORITY_FALSE_FIELDS))
def test_index_entry_authority_true_fails(stage_key, field):
    # ok stays True; a forged authority/boundary True must fail the DEEP gate.
    proof = _real_proof()
    proof[stage_key][field] = True
    s = _summary(proof)
    assert s[_flag_for(stage_key)] is False, (stage_key, field)
    assert s["chain_clean"] is False


@pytest.mark.parametrize("stage_key", _INDEX_ENTRY_KEYS)
@pytest.mark.parametrize("field", list(mod._INDEX_ENTRY_TRUE_FIELDS))
def test_index_entry_true_field_false_fails(stage_key, field):
    proof = _real_proof()
    proof[stage_key][field] = False
    s = _summary(proof)
    assert s[_flag_for(stage_key)] is False, (stage_key, field)


@pytest.mark.parametrize("stage_key", _INDEX_ENTRY_KEYS)
@pytest.mark.parametrize("field", list(mod._INDEX_ENTRY_MATCH_FIELDS))
def test_index_entry_match_field_mismatch_fails(stage_key, field):
    proof = _real_proof()
    proof[stage_key][field] = "mismatch"
    s = _summary(proof)
    assert s[_flag_for(stage_key)] is False, (stage_key, field)


@pytest.mark.parametrize("stage_key", _INDEX_ENTRY_KEYS)
@pytest.mark.parametrize("field", list(mod._INDEX_ENTRY_ALL_MATCH_DICTS))
@pytest.mark.parametrize("bad", [{}, {"x": "mismatch"}, "x", None])
def test_index_entry_all_match_dict_bad_fails(stage_key, field, bad):
    proof = _real_proof()
    proof[stage_key][field] = bad
    s = _summary(proof)
    assert s[_flag_for(stage_key)] is False, (stage_key, field, bad)


@pytest.mark.parametrize("stage_key", _INDEX_ENTRY_KEYS)
@pytest.mark.parametrize(
    "field,bad",
    [
        ("blockers", ["boom"]),
        ("blockers", "not-a-list"),
        ("warnings", ["warn"]),
        ("artifact_count_checked", 2),
        ("artifact_count_checked", 1.0),
        ("artifact_count_checked", True),
    ],
)
def test_index_entry_component_violations_fail(stage_key, field, bad):
    proof = _real_proof()
    proof[stage_key][field] = bad
    s = _summary(proof)
    assert s[_flag_for(stage_key)] is False, (stage_key, field, bad)
    assert s["chain_clean"] is False


@pytest.mark.parametrize("stage_key", _INDEX_ENTRY_KEYS)
@pytest.mark.parametrize("field", sorted(mod._INDEX_ENTRY_REQUIRED_FIELDS))
def test_index_entry_missing_required_field_fails(stage_key, field):
    proof = _real_proof()
    proof[stage_key].pop(field, None)
    s = _summary(proof)
    assert s[_flag_for(stage_key)] is False, (stage_key, field)


# ------------------------------------------------------------------ content-safe

@pytest.mark.parametrize("level_key", list(mod._CHAIN_LEVEL_KEYS))
def test_injected_raw_path_does_not_leak(level_key):
    proof = _real_proof()
    secret = "C:" + "\\" + "secret" + "\\plan.json"
    proof[level_key]["proof_id"] = secret
    proof[level_key]["safe_conclusion"] = "leak " + secret
    s = _summary(proof)
    blob = json.dumps(s)
    assert "secret" not in blob
    assert str(REPO_ROOT) not in blob
    assert s["path_free_verified"] is True
    assert mod._contains_path_marker(s) is False
    for key, value in s.items():
        assert key == "report_version" or isinstance(value, (bool, int)), (key, value)


# ---------------------------------------------------------------- malformed proof

@pytest.mark.parametrize("proof", [{}, "nope", None, 7, []])
def test_non_mapping_or_empty_proof_fails_closed(proof):
    s = _summary(proof)
    assert s["chain_clean"] is False
    assert s["chain_levels_all_ok"] is False
    assert s["chain_levels_present"] == 0
    assert s["artifact_verify_clean"] is False
    assert s["index_entry_verify_clean"] is False
    assert s["deepest_verify_clean"] is False


# ------------------------------------------------------------------- unit helpers

def test_level_ok_helper():
    assert mod._level_ok({"ok": True}) is True
    for bad in ({"ok": False}, {"ok": "True"}, {"ok": 1}, {}, "x", None):
        assert mod._level_ok(bad) is False, bad


def test_strict_helpers():
    assert mod._strict_empty_list([]) is True
    for bad in (["x"], (), "", None, {}, 0):
        assert mod._strict_empty_list(bad) is False, bad
    assert mod._strict_int_one(1) is True
    for bad in (1.0, True, 0, 2, -1, "1", None):
        assert mod._strict_int_one(bad) is False, bad
    assert mod._all_match({"a": "match"}) is True
    for bad in ({}, {"a": "mismatch"}, {"a": "match", "b": "x"}, "x", None):
        assert mod._all_match(bad) is False, bad
    assert mod._all_true_nonempty({"a": True}) is True
    for bad in ({}, {"a": False}, {"a": "true"}, {"a": 1}, "x", None):
        assert mod._all_true_nonempty(bad) is False, bad
