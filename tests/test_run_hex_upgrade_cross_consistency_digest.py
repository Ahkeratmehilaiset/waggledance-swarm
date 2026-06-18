"""Offline tests for tools/run_hex_upgrade_cross_consistency_digest.py.

Covers the RCO dual-lens criteria for the cross-consistency digest: STRUCTURAL
(strict cross_consistent = all three views present AND each re-derived clean from
components, safe-scalar allowlist emission, path-free/content-safe) and
EMPIRICAL/FORGE (a connected adversarial view the digest actually consumes, incl.
the #1274 inconsistent-aggregate forge, asserted to fail CLOSED).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import tools.run_hex_upgrade_cross_consistency_digest as mod

REPO_ROOT = Path(__file__).resolve().parent.parent


def _good_reviewer() -> dict:
    return {
        "report_version": "wd.hex_subdivision_reviewer_summary.v1",
        "verdict_ok": True,
        "blocker_count": 0,
        "warning_count": 0,
        "source_contract_match": True,
        "rebuilt_index_entry_match": True,
        "digest_all_match": True,
        "size_all_match": True,
        "schema_version_all_match": True,
        "all_checks_match": True,
        "review_clean": True,
        "path_free_verified": True,
    }


def _good_shadow() -> dict:
    return {
        "report_version": "wd.shadow_only_invariant_proof.v1",
        "ok": True,
        "blockers": [],
        "measurement_basis": "v1_shadow_only_invariant",
        "deterministic_replay": {"runs": 2, "stable_identical": True},
        "invariant": {
            "invariant_holds": True,
            "shadow_to_candidate_subdivision_transitions_total": 0,
            "target_state_is_shadow": True,
            "transition_occurred": False,
            "no_runtime_mutation": True,
            "guardrails_all_clean": True,
            "artifact_ok": True,
            "injected_transition_count_violation": False,
            "claim_safe": False,
        },
        "invariants": {},
    }


def _good_chain() -> dict:
    return {
        "report_version": "wd.shadow_subdivision_verifier_chain_final_summary.v1",
        "chain_levels_total": 10,
        "chain_levels_present": 10,
        "chain_levels_ok": 10,
        "chain_levels_all_ok": True,
        "chain_levels_shape_ok": True,
        "artifact_verify_clean": True,
        "index_entry_verify_clean": True,
        "deepest_verify_clean": True,
        "total_blocker_count": 0,
        "total_warning_count": 0,
        "chain_clean": True,
        "path_free_verified": True,
    }


def _good_proof() -> dict:
    return {
        "reviewer_summary": _good_reviewer(),
        "shadow_only_invariant": _good_shadow(),
        "chain_final_summary": _good_chain(),
    }


def _digest(proof):
    return mod.build_cross_consistency_digest(proof)


# --------------------------------------------------------------------------- real

def test_real_proof_cross_consistent():
    from tools.wd_image1_capability_manifest import build_manifest

    proof = next(
        c["proof"] for c in build_manifest(REPO_ROOT)["capabilities"]
        if c["capability_id"] == "hexagonal_upgrades"
    )
    d = _digest(proof)
    assert d["cross_consistent"] is True
    assert d["all_views_present"] is True
    assert d["reviewer_clean"] is True
    assert d["shadow_only_clean"] is True
    assert d["chain_summary_clean"] is True
    assert d["path_free_verified"] is True
    assert d["claim_safe"] is False


def test_good_synthetic_cross_consistent():
    d = _digest(_good_proof())
    assert d["cross_consistent"] is True
    assert all(
        d[k] is True
        for k in ("reviewer_clean", "shadow_only_clean", "chain_summary_clean")
    )


def test_main_exit0():
    assert mod.main([]) == 0
    assert mod.main(["--json"]) == 0


def test_allowlist_and_scalar():
    d = _digest(_good_proof())
    assert set(d) == mod._DIGEST_SAFE_KEYS
    for key, value in d.items():
        if key == "report_version":
            assert isinstance(value, str)
        else:
            assert isinstance(value, bool), (key, value)


def test_vocabulary_and_json_clean():
    d = _digest(_good_proof())
    mod.assert_vocabulary_clean(mod.render_summary(d))
    mod.assert_vocabulary_clean(json.dumps(d))


# ----------------------------------------------------------------- absent / shape

@pytest.mark.parametrize("key,flag", [
    ("reviewer_summary", "reviewer_clean"),
    ("shadow_only_invariant", "shadow_only_clean"),
    ("chain_final_summary", "chain_summary_clean"),
])
def test_absent_view_fails_closed(key, flag):
    p = _good_proof()
    del p[key]
    d = _digest(p)
    assert d["all_views_present"] is False
    assert d[flag] is False
    assert d["cross_consistent"] is False


@pytest.mark.parametrize("key", [
    "reviewer_summary", "shadow_only_invariant", "chain_final_summary",
])
def test_non_mapping_view_fails_closed(key):
    p = _good_proof()
    p[key] = "not-a-mapping"
    d = _digest(p)
    assert d["cross_consistent"] is False


# ------------------------------------------------------ reviewer re-derivation

@pytest.mark.parametrize("field", list(mod._REVIEWER_TRUE_FIELDS))
def test_reviewer_component_false_fails(field):
    p = _good_proof()
    p["reviewer_summary"][field] = False
    d = _digest(p)
    assert d["reviewer_clean"] is False, field
    assert d["cross_consistent"] is False


@pytest.mark.parametrize("bad", [1, -1, True, "x", None, 0.0])
def test_reviewer_blocker_count_strict_zero(bad):
    p = _good_proof()
    p["reviewer_summary"]["blocker_count"] = bad
    assert _digest(p)["reviewer_clean"] is False, bad


@pytest.mark.parametrize("field", list(mod._REVIEWER_TRUE_FIELDS))
def test_reviewer_inconsistent_aggregate_fails(field):
    # #1274: a component False but the view's own review_clean/all_checks_match True
    # must still fail (the digest never trusts the aggregate composite).
    p = _good_proof()
    p["reviewer_summary"][field] = False
    p["reviewer_summary"]["review_clean"] = True
    p["reviewer_summary"]["all_checks_match"] = True
    d = _digest(p)
    assert d["reviewer_clean"] is False, field
    assert d["cross_consistent"] is False


# ------------------------------------------------------- shadow re-derivation

@pytest.mark.parametrize("field", list(mod._SHADOW_INV_TRUE_FIELDS))
def test_shadow_component_false_fails(field):
    p = _good_proof()
    p["shadow_only_invariant"]["invariant"][field] = False
    d = _digest(p)
    assert d["shadow_only_clean"] is False, field
    assert d["cross_consistent"] is False


@pytest.mark.parametrize("mutate", [
    lambda s: s.__setitem__("ok", False),
    lambda s: s["deterministic_replay"].__setitem__("stable_identical", False),
    lambda s: s["invariant"].__setitem__("transition_occurred", True),
    lambda s: s["invariant"].__setitem__("injected_transition_count_violation", True),
    lambda s: s["invariant"].__setitem__("claim_safe", True),
])
def test_shadow_other_violations_fail(mutate):
    p = _good_proof()
    mutate(p["shadow_only_invariant"])
    assert _digest(p)["shadow_only_clean"] is False


@pytest.mark.parametrize("bad", [1, -1, True, "0", None, 0.0])
def test_shadow_transition_count_strict_zero(bad):
    p = _good_proof()
    p["shadow_only_invariant"]["invariant"][
        "shadow_to_candidate_subdivision_transitions_total"
    ] = bad
    assert _digest(p)["shadow_only_clean"] is False, bad


@pytest.mark.parametrize("field", list(mod._SHADOW_INV_TRUE_FIELDS))
def test_shadow_inconsistent_aggregate_fails(field):
    # invariant_holds True (lying aggregate) but a component False -> not clean.
    p = _good_proof()
    p["shadow_only_invariant"]["invariant"][field] = False
    p["shadow_only_invariant"]["invariant"]["invariant_holds"] = True
    d = _digest(p)
    assert d["shadow_only_clean"] is False, field
    assert d["cross_consistent"] is False


# -------------------------------------------------------- chain re-derivation

@pytest.mark.parametrize("field", list(mod._CHAIN_TRUE_FIELDS))
def test_chain_component_false_fails(field):
    p = _good_proof()
    p["chain_final_summary"][field] = False
    d = _digest(p)
    assert d["chain_summary_clean"] is False, field
    assert d["cross_consistent"] is False


@pytest.mark.parametrize("total,present", [(10, 9), (0, 0), (10, 11)])
def test_chain_levels_incomplete_fails(total, present):
    p = _good_proof()
    p["chain_final_summary"]["chain_levels_total"] = total
    p["chain_final_summary"]["chain_levels_present"] = present
    assert _digest(p)["chain_summary_clean"] is False, (total, present)


@pytest.mark.parametrize("bad", [True, 10.0, "10", None])
def test_chain_levels_total_malformed_fails(bad):
    p = _good_proof()
    p["chain_final_summary"]["chain_levels_total"] = bad
    p["chain_final_summary"]["chain_levels_present"] = bad
    assert _digest(p)["chain_summary_clean"] is False, bad


@pytest.mark.parametrize("bad", [1, -1, True, "x", None, 0.0])
def test_chain_blocker_count_strict_zero(bad):
    p = _good_proof()
    p["chain_final_summary"]["total_blocker_count"] = bad
    assert _digest(p)["chain_summary_clean"] is False, bad


@pytest.mark.parametrize("field", list(mod._CHAIN_TRUE_FIELDS))
def test_chain_inconsistent_aggregate_fails(field):
    p = _good_proof()
    p["chain_final_summary"][field] = False
    p["chain_final_summary"]["chain_clean"] = True
    d = _digest(p)
    assert d["chain_summary_clean"] is False, field
    assert d["cross_consistent"] is False


# ------------------------------------------------------- coherence / contradiction

@pytest.mark.parametrize("mutate", [
    lambda p: p["reviewer_summary"].__setitem__("verdict_ok", False),
    lambda p: p["shadow_only_invariant"]["invariant"].__setitem__(
        "no_runtime_mutation", False
    ),
    lambda p: p["chain_final_summary"].__setitem__("chain_levels_all_ok", False),
])
def test_single_view_degraded_breaks_consistency(mutate):
    # A single degraded view (others clean) is a contradiction -> not consistent.
    p = _good_proof()
    mutate(p)
    assert _digest(p)["cross_consistent"] is False


# ------------------------------------------------------------------ content-safe

@pytest.mark.parametrize("key", [
    "reviewer_summary", "shadow_only_invariant", "chain_final_summary",
])
def test_injected_raw_content_does_not_leak(key):
    p = _good_proof()
    secret = "C:" + "\\" + "secret" + "\\plan.json"
    p[key]["report_version"] = secret
    p[key]["injected_raw_field"] = secret
    d = _digest(p)
    blob = json.dumps(d)
    assert "secret" not in blob
    assert str(REPO_ROOT) not in blob
    assert d["path_free_verified"] is True
    assert mod._contains_path_marker(d) is False
    for k, v in d.items():
        assert k == "report_version" or isinstance(v, bool), (k, v)


# ---------------------------------------------------------------- malformed proof

@pytest.mark.parametrize("proof", [{}, "nope", None, 7, []])
def test_malformed_proof_fails_closed(proof):
    d = _digest(proof)
    assert d["cross_consistent"] is False
    assert d["all_views_present"] is False
    assert d["reviewer_clean"] is False
    assert d["shadow_only_clean"] is False
    assert d["chain_summary_clean"] is False


# ------------------------------------------------------------------- unit helper

def test_strict_zero_int():
    assert mod._strict_zero_int(0) is True
    for bad in (0.0, True, -1, 1, "0", None):
        assert mod._strict_zero_int(bad) is False, bad


# ------------------------------------------------- chain wrong-depth (lead #1278)

def test_chain_wrong_depth_self_consistent_fails():
    # lead #1278 forge: a self-consistent but WRONG-DEPTH chain (3/3, all flags True)
    # must fail - clean requires the FULL expected depth, not merely present==total.
    p = _good_proof()
    for k in ("chain_levels_total", "chain_levels_present", "chain_levels_ok"):
        p["chain_final_summary"][k] = 3
    d = _digest(p)
    assert d["chain_summary_clean"] is False
    assert d["cross_consistent"] is False


@pytest.mark.parametrize("field", ["chain_levels_total", "chain_levels_present"])
@pytest.mark.parametrize("bad", [10.0, True, "10", None, 9, 11, 0])
def test_chain_level_count_must_equal_expected_strict_int(field, bad):
    # Degrade ONE count (the other stays the good 10); a non-strict-int or off-by-depth
    # value (incl present=10.0) must fail closed.
    p = _good_proof()
    p["chain_final_summary"][field] = bad
    assert _digest(p)["chain_summary_clean"] is False, (field, bad)


def test_expected_chain_depth_matches_renderer():
    from tools.render_shadow_subdivision_verifier_chain_final_summary import (
        _CHAIN_LEVEL_KEYS,
    )

    assert mod._EXPECTED_CHAIN_LEVEL_COUNT == len(_CHAIN_LEVEL_KEYS) == 10
