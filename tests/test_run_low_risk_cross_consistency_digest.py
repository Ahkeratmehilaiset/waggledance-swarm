"""Offline tests for tools/run_low_risk_cross_consistency_digest.py.

Covers the RCO dual-lens criteria for the low-risk cross-consistency digest:
STRUCTURAL (strict cross_consistent = all three views present AND each re-derived
clean from RAW component scalars AND the reviewer faithfully matches the trend;
safe-scalar allowlist emission; path-free/content-safe) and EMPIRICAL/FORGE (build a
TRUE baseline, mutate ONE axis, assert cross_consistent flips False FOR THAT REASON):
per-source raw window/count re-derivation (#1284/#1286), the #1274 lying-aggregate,
and cross-source DISAGREEMENT (#1278-class mutual coherence).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import tools.run_low_risk_cross_consistency_digest as mod

REPO_ROOT = Path(__file__).resolve().parent.parent


def _good_real_loop() -> dict:
    # carries the FULL authority_boundary the live dry-run emits (all axes False), so the
    # boundary all-False / forged-extra-key checks have a true baseline.
    return {
        "report_version": "wd.low_risk_autogrowth_chain_dry_run.v1",
        "ok": True,
        "local_artifacts_written": False,
        "authority_boundary": {f: False for f in mod._REAL_LOOP_BOUNDARY_FALSE_FIELDS},
    }


def _good_trend() -> dict:
    return {
        "all_runs_ok": True,
        "deterministic": True,
        "promoted_solver_count_stable": True,
        "evidence_present": True,
        "any_guardrail_tripped": False,
        "trend_runtime_authority_granted": False,
        "trend_external_writes_applied": False,
        "window_size": 3,
        "promoted_solver_count_min": 1,
        "promoted_solver_count_max": 1,
    }


def _good_reviewer() -> dict:
    # window/min/max MUST match the trend (3/1/1) so reviewer_matches_trend holds.
    return {
        "report_version": "wd.low_risk_repeat_window_trend_reviewer_summary.v1",
        "trend_present": True,
        "all_runs_ok": True,
        "deterministic": True,
        "promotion_count_stable": True,
        "evidence_present": True,
        "no_guardrail_tripped": True,
        "no_runtime_authority_granted": True,
        "no_external_writes": True,
        "window_size_valid": True,
        "promotion_count_positive": True,
        "trend_review_clean": True,
        "path_free_verified": True,
        "claim_safe": False,
        "window_size": 3,
        "promoted_solver_count_min": 1,
        "promoted_solver_count_max": 1,
    }


def _good_proof() -> dict:
    return {
        "real_loop_dry_run": _good_real_loop(),
        "repeat_window_trend": _good_trend(),
        "repeat_window_trend_reviewer_summary": _good_reviewer(),
    }


def _digest(proof):
    return mod.build_low_risk_cross_consistency_digest(proof)


# --------------------------------------------------------------------------- real

def test_real_proof_cross_consistent():
    from tools.wd_image1_capability_manifest import build_manifest

    proof = next(
        c["proof"] for c in build_manifest(REPO_ROOT)["capabilities"]
        if c["capability_id"] == "low_risk_autonomy_loop"
    )
    d = _digest(proof)
    assert d["cross_consistent"] is True
    assert d["all_views_present"] is True
    assert d["real_loop_clean"] is True
    assert d["trend_clean"] is True
    assert d["reviewer_clean"] is True
    assert d["reviewer_matches_trend"] is True
    assert d["path_free_verified"] is True
    assert d["claim_safe"] is False


def test_good_synthetic_cross_consistent():
    d = _digest(_good_proof())
    assert d["cross_consistent"] is True
    assert all(
        d[k] is True
        for k in ("real_loop_clean", "trend_clean", "reviewer_clean", "reviewer_matches_trend")
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
    ("real_loop_dry_run", "real_loop_clean"),
    ("repeat_window_trend", "trend_clean"),
    ("repeat_window_trend_reviewer_summary", "reviewer_clean"),
])
def test_absent_view_fails_closed(key, flag):
    p = _good_proof()
    del p[key]
    d = _digest(p)
    assert d["all_views_present"] is False
    assert d[flag] is False
    assert d["cross_consistent"] is False


@pytest.mark.parametrize("key", [
    "real_loop_dry_run", "repeat_window_trend", "repeat_window_trend_reviewer_summary",
])
def test_non_mapping_view_fails_closed(key):
    p = _good_proof()
    p[key] = "not-a-mapping"
    d = _digest(p)
    assert d["cross_consistent"] is False


# ------------------------------------------------------ real-loop re-derivation

@pytest.mark.parametrize("mutate", [
    lambda v: v.__setitem__("ok", False),
    lambda v: v.__setitem__("ok", "true"),  # non-strict truthy not accepted
    lambda v: v.__setitem__("local_artifacts_written", True),
    lambda v: v["authority_boundary"].__setitem__("external_writes_applied", True),
    lambda v: v["authority_boundary"].__setitem__("runtime_authority_granted", True),
    lambda v: v.__setitem__("claim_safe", True),
    lambda v: v.__setitem__("authority_boundary", "nope"),
])
def test_real_loop_violation_fails(mutate):
    p = _good_proof()
    mutate(p["real_loop_dry_run"])
    d = _digest(p)
    assert d["real_loop_clean"] is False
    assert d["cross_consistent"] is False


@pytest.mark.parametrize("field", list(mod._REAL_LOOP_BOUNDARY_FALSE_FIELDS))
def test_real_loop_boundary_axis_true_fails(field):
    # every authority_boundary axis (incl. control-plane / scheduler / provider /
    # builder / gate-skip) must re-derive False; any True -> not clean (RCO-2 pt1).
    p = _good_proof()
    p["real_loop_dry_run"]["authority_boundary"][field] = True
    d = _digest(p)
    assert d["real_loop_clean"] is False, field
    assert d["cross_consistent"] is False


@pytest.mark.parametrize("field", list(mod._REAL_LOOP_BOUNDARY_FALSE_FIELDS))
def test_real_loop_boundary_axis_missing_fails(field):
    # a required authority axis absent (get -> None, not False) fails closed.
    p = _good_proof()
    del p["real_loop_dry_run"]["authority_boundary"][field]
    assert _digest(p)["real_loop_clean"] is False, field


@pytest.mark.parametrize("bad", [True, "yes", 1, {}])
def test_real_loop_forged_extra_boundary_key_fails(bad):
    # a forged EXTRA boundary key that is not False must fail closed (#1271-style).
    p = _good_proof()
    p["real_loop_dry_run"]["authority_boundary"]["forged_secret_authority"] = bad
    assert _digest(p)["real_loop_clean"] is False, bad


# --------------------------------------------------------- trend re-derivation

@pytest.mark.parametrize("field", list(mod._TREND_TRUE_FIELDS))
def test_trend_true_component_false_fails(field):
    p = _good_proof()
    p["repeat_window_trend"][field] = False
    d = _digest(p)
    assert d["trend_clean"] is False, field
    assert d["cross_consistent"] is False


@pytest.mark.parametrize("field", list(mod._TREND_FALSE_FIELDS))
def test_trend_false_component_true_fails(field):
    p = _good_proof()
    p["repeat_window_trend"][field] = True
    d = _digest(p)
    assert d["trend_clean"] is False, field
    assert d["cross_consistent"] is False


def test_trend_claim_safe_refused():
    p = _good_proof()
    p["repeat_window_trend"]["claim_safe"] = True
    assert _digest(p)["trend_clean"] is False


@pytest.mark.parametrize("window", [1, 26, 3.0, True, "3", None])
def test_trend_window_malformed_fails(window):
    p = _good_proof()
    p["repeat_window_trend"]["window_size"] = window
    # keep the reviewer matched so the ONLY failing axis is the trend window
    p["repeat_window_trend_reviewer_summary"]["window_size"] = window
    assert _digest(p)["trend_clean"] is False, window


@pytest.mark.parametrize("cmin,cmax", [
    (0, 1), (1, 0), (2, 1), (1.0, 1), (1, 1.0), (True, 1), (1, True), (-1, 1), (None, 1),
])
def test_trend_count_malformed_fails(cmin, cmax):
    p = _good_proof()
    p["repeat_window_trend"]["promoted_solver_count_min"] = cmin
    p["repeat_window_trend"]["promoted_solver_count_max"] = cmax
    assert _digest(p)["trend_clean"] is False, (cmin, cmax)


# ------------------------------------------------------ reviewer re-derivation

@pytest.mark.parametrize("field", list(mod._REVIEWER_TRUE_FIELDS))
def test_reviewer_component_false_fails(field):
    p = _good_proof()
    p["repeat_window_trend_reviewer_summary"][field] = False
    d = _digest(p)
    assert d["reviewer_clean"] is False, field
    assert d["cross_consistent"] is False


@pytest.mark.parametrize("field", list(mod._REVIEWER_TRUE_FIELDS))
def test_reviewer_inconsistent_aggregate_fails(field):
    # #1274: a component False while the view's own trend_review_clean True must still
    # fail (the digest never trusts the aggregate composite).
    p = _good_proof()
    p["repeat_window_trend_reviewer_summary"][field] = False
    p["repeat_window_trend_reviewer_summary"]["trend_review_clean"] = True
    d = _digest(p)
    assert d["reviewer_clean"] is False, field
    assert d["cross_consistent"] is False


def test_reviewer_claim_safe_refused():
    p = _good_proof()
    p["repeat_window_trend_reviewer_summary"]["claim_safe"] = True
    d = _digest(p)
    assert d["reviewer_clean"] is False
    assert d["cross_consistent"] is False


@pytest.mark.parametrize("window", [1, 26, 3.0, True, "3", None])
def test_reviewer_window_malformed_fails(window):
    p = _good_proof()
    p["repeat_window_trend_reviewer_summary"]["window_size"] = window
    p["repeat_window_trend"]["window_size"] = window  # keep matched
    assert _digest(p)["reviewer_clean"] is False, window


# ------------------------------------------------- mutual coherence (#1278-class)

@pytest.mark.parametrize("reviewer_over,trend_over,label", [
    ({"window_size": 4}, {"window_size": 3}, "window"),
    ({"promoted_solver_count_min": 2, "promoted_solver_count_max": 2},
     {"promoted_solver_count_min": 1, "promoted_solver_count_max": 1}, "min"),
    ({"promoted_solver_count_min": 1, "promoted_solver_count_max": 3},
     {"promoted_solver_count_min": 1, "promoted_solver_count_max": 1}, "max"),
])
def test_reviewer_disagrees_with_trend_fails(reviewer_over, trend_over, label):
    # Both views individually CLEAN (each set of values is internally valid) but they
    # DISAGREE on a shared field -> reviewer is not a faithful view of the trend -> not
    # consistent. This is the digest's core job (mutual coherence), beyond per-view clean.
    p = _good_proof()
    p["repeat_window_trend_reviewer_summary"].update(reviewer_over)
    p["repeat_window_trend"].update(trend_over)
    d = _digest(p)
    assert d["trend_clean"] is True, (label, "trend should still be clean")
    assert d["reviewer_clean"] is True, (label, "reviewer should still be clean")
    assert d["reviewer_matches_trend"] is False, label
    assert d["cross_consistent"] is False, label


def test_reviewer_matches_trend_requires_strict_ints():
    # a non-strict value on either side (even if equal-ish) fails coherence.
    p = _good_proof()
    p["repeat_window_trend_reviewer_summary"]["window_size"] = 3.0
    p["repeat_window_trend"]["window_size"] = 3.0
    assert _digest(p)["reviewer_matches_trend"] is False


# ------------------------------------------------------------------ content-safe

@pytest.mark.parametrize("key", [
    "real_loop_dry_run", "repeat_window_trend", "repeat_window_trend_reviewer_summary",
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
    assert d["real_loop_clean"] is False
    assert d["trend_clean"] is False
    assert d["reviewer_clean"] is False


# --------------------------------------------------------- single-view degraded

@pytest.mark.parametrize("mutate", [
    lambda p: p["real_loop_dry_run"].__setitem__("ok", False),
    lambda p: p["repeat_window_trend"].__setitem__("all_runs_ok", False),
    lambda p: p["repeat_window_trend_reviewer_summary"].__setitem__("deterministic", False),
])
def test_single_view_degraded_breaks_consistency(mutate):
    p = _good_proof()
    mutate(p)
    assert _digest(p)["cross_consistent"] is False


# ------------------------------------------------------------------- unit helpers

def test_strict_int_helper():
    assert mod._strict_int(0) is True
    assert mod._strict_int(5) is True
    for bad in (0.0, True, False, "0", None, []):
        assert mod._strict_int(bad) is False, bad


def test_window_count_valid_helper():
    assert mod._window_count_valid(3, 1, 2) is True
    assert mod._window_count_valid(mod._MIN_WINDOW, 1, 1) is True
    assert mod._window_count_valid(mod._MAX_WINDOW, 1, 9) is True
    for bad in (
        (1, 1, 1), (mod._MAX_WINDOW + 1, 1, 1), (3.0, 1, 1), (True, 1, 1),
        (3, 0, 1), (3, 2, 1), (3, 1.0, 1), (3, 1, True), (3, None, 1),
    ):
        assert mod._window_count_valid(*bad) is False, bad


def test_window_bounds_match_renderer():
    from tools.render_low_risk_repeat_window_trend_reviewer_summary import (
        _MAX_WINDOW, _MIN_WINDOW,
    )

    assert (mod._MIN_WINDOW, mod._MAX_WINDOW) == (_MIN_WINDOW, _MAX_WINDOW) == (2, 25)
