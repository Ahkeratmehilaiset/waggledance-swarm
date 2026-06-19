"""Offline tests for tools/render_low_risk_repeat_window_trend_reviewer_summary.py.

Covers RCO dual-lens criteria: STRUCTURAL (safe-scalar allowlist emission, path-free,
fail-closed re-derivation never trusting the aggregate's own ok) and EMPIRICAL/FORGE
(a connected adversarial trend aggregate the renderer consumes, asserted fail CLOSED).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import tools.render_low_risk_repeat_window_trend_reviewer_summary as mod

REPO_ROOT = Path(__file__).resolve().parent.parent


def _good_trend() -> dict:
    # Mirrors the curated _safe_repeat_window_trend_aggregate the manifest stores under
    # low_risk_autonomy_proof["repeat_window_trend"].
    return {
        "all_runs_ok": True,
        "any_guardrail_tripped": False,
        "deterministic": True,
        "evidence_present": True,
        "measurement_basis": "v1_low_risk_real_loop_repeat_window",
        "ok": True,
        "promoted_solver_count_max": 1,
        "promoted_solver_count_min": 1,
        "promoted_solver_count_stable": True,
        "trend_external_writes_applied": False,
        "trend_runtime_authority_granted": False,
        "window_size": 3,
    }


def _summary(trend):
    return mod.render_repeat_window_trend_reviewer_summary(trend)


# --------------------------------------------------------------------------- real

def test_real_trend_review_clean():
    from tools.wd_image1_capability_manifest import build_manifest

    proof = next(
        c["proof"] for c in build_manifest(REPO_ROOT)["capabilities"]
        if c["capability_id"] == "low_risk_autonomy_loop"
    )
    s = _summary(proof.get("repeat_window_trend"))
    assert s["trend_present"] is True
    assert s["trend_review_clean"] is True
    assert s["path_free_verified"] is True
    assert s["claim_safe"] is False


def test_good_synthetic_clean():
    s = _summary(_good_trend())
    assert s["trend_review_clean"] is True
    assert s["claim_safe"] is False


def test_main_exit0():
    assert mod.main([]) == 0
    assert mod.main(["--json"]) == 0


def test_allowlist_and_scalar():
    s = _summary(_good_trend())
    assert set(s) == mod._SUMMARY_SAFE_KEYS
    for key, value in s.items():
        if key == "report_version":
            assert isinstance(value, str)
        elif key in ("window_size", "promoted_solver_count_min", "promoted_solver_count_max"):
            assert value is None or (isinstance(value, int) and not isinstance(value, bool))
        else:
            assert isinstance(value, bool), (key, value)


def test_vocabulary_and_json_clean():
    s = _summary(_good_trend())
    mod.assert_vocabulary_clean(mod.render_summary(s))
    mod.assert_vocabulary_clean(json.dumps(s))


# ---------------------------------------------------- consumer re-derive fail-closed

@pytest.mark.parametrize("field", [
    "all_runs_ok", "deterministic", "promoted_solver_count_stable", "evidence_present",
])
def test_positive_component_false_fails(field):
    t = _good_trend()
    t[field] = False
    s = _summary(t)
    assert s["trend_review_clean"] is False, field


@pytest.mark.parametrize("field", [
    "any_guardrail_tripped",
    "trend_runtime_authority_granted",
    "trend_external_writes_applied",
])
def test_authority_axis_true_fails(field):
    t = _good_trend()
    t[field] = True
    s = _summary(t)
    assert s["trend_review_clean"] is False, field


@pytest.mark.parametrize("field", [
    "all_runs_ok", "deterministic", "promoted_solver_count_stable", "evidence_present",
])
def test_inconsistent_aggregate_fails(field):
    # The aggregate's own ok lies True while a component is degraded -> still not clean
    # (the renderer re-derives from components, never trusts ok).
    t = _good_trend()
    t[field] = False
    t["ok"] = True
    s = _summary(t)
    assert s["trend_review_clean"] is False, field


@pytest.mark.parametrize("bad", [1, 26, 0, -1, "3", 3.0, True, None])
def test_invalid_window_size_fails(bad):
    t = _good_trend()
    t["window_size"] = bad
    s = _summary(t)
    assert s["window_size_valid"] is False, bad
    assert s["trend_review_clean"] is False, bad


@pytest.mark.parametrize("bad", [0, -1, "1", 1.0, True, None])
def test_nonpositive_or_malformed_promotion_count_fails(bad):
    t = _good_trend()
    t["promoted_solver_count_min"] = bad
    s = _summary(t)
    assert s["promotion_count_positive"] is False, bad
    assert s["trend_review_clean"] is False, bad


@pytest.mark.parametrize("bad", [0, -1, "1", 1.0, True, None])
def test_malformed_promotion_count_max_fails(bad):
    t = _good_trend()
    t["promoted_solver_count_max"] = bad
    s = _summary(t)
    assert s["promotion_count_positive"] is False, bad
    assert s["trend_review_clean"] is False, bad


def test_promotion_count_min_greater_than_max_fails():
    t = _good_trend()
    t["promoted_solver_count_min"] = 2
    t["promoted_solver_count_max"] = 1
    s = _summary(t)
    assert s["promotion_count_positive"] is False
    assert s["trend_review_clean"] is False


# ------------------------------------------------------------------ content-safe

def test_injected_raw_content_does_not_leak():
    t = _good_trend()
    secret = "C:" + "\\" + "secret" + "\\trend.json"
    t["measurement_basis"] = secret
    t["injected_raw_field"] = secret
    s = _summary(t)
    blob = json.dumps(s)
    assert "secret" not in blob
    assert str(REPO_ROOT) not in blob
    assert s["path_free_verified"] is True
    assert mod._contains_path_marker(s) is False
    for key, value in s.items():
        assert key == "report_version" or isinstance(value, (bool, int)) or value is None, (key, value)


# ---------------------------------------------------------------- malformed / absent

@pytest.mark.parametrize("trend", [{}, "nope", None, 7, []])
def test_malformed_or_absent_trend_fails_closed(trend):
    s = _summary(trend)
    assert s["trend_present"] is False
    assert s["trend_review_clean"] is False
    assert s["claim_safe"] is False


# ------------------------------------------------------------------- unit helper

def test_strict_int_helper():
    assert mod._strict_int(3) is True
    for bad in (3.0, True, "3", None):
        assert mod._strict_int(bad) is False, bad
