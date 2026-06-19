"""Offline tests for build_low_risk_cross_consistency_digest_bridge_event_template.py.

Covers the RCO criteria for the bridge-event TEMPLATE slice: TEMPLATE-ONLY (no side
effects; every approval/transport/authority axis hardcoded False), PATH-FREE +
safe-scalar (derived bools/strict refs only; path_free_verified DERIVED; closed
allowlist), RE-DERIVE/NO-UPGRADE (render the digest's verdicts faithfully; refuse
self-claim_safe), SCHEMA-VALID (validate_event), and FAIL-CLOSED on missing/malformed/
non-Mapping/bool-as-int/self-claim_safe/path-tainted digest and unsafe bridge inputs.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import tools.build_low_risk_cross_consistency_digest_bridge_event_template as mod
from waggledance.core.bridge_event_schema import validate_event

REPO_ROOT = Path(__file__).resolve().parent.parent

_FIXED_NOW = "2026-06-19T06:00:00Z"


def _good_digest() -> dict:
    # mirrors run_low_risk_cross_consistency_digest output (all clean).
    return {
        "report_version": mod.DIGEST_REPORT_VERSION,
        "real_loop_present": True,
        "repeat_window_trend_present": True,
        "reviewer_summary_present": True,
        "all_views_present": True,
        "real_loop_clean": True,
        "trend_clean": True,
        "reviewer_clean": True,
        "reviewer_matches_trend": True,
        "cross_consistent": True,
        "path_free_verified": True,
        "claim_safe": False,
    }


def _build(digest=None, **kw):
    params = dict(
        digest=_good_digest() if digest is None else digest,
        agent_id="fable-5",
        task_id="demo-task",
        now_utc=mod._parse_utc(_FIXED_NOW),
    )
    params.update(kw)
    return mod.build_low_risk_cross_consistency_digest_bridge_event_template(**params)


# --------------------------------------------------------------------------- real

def test_real_digest_renders_ok():
    from tools.run_low_risk_cross_consistency_digest import (
        build_low_risk_cross_consistency_digest,
    )
    from tools.wd_image1_capability_manifest import build_manifest

    proof = next(
        c["proof"] for c in build_manifest(REPO_ROOT)["capabilities"]
        if c["capability_id"] == "low_risk_autonomy_loop"
    )
    digest = build_low_risk_cross_consistency_digest(proof)
    report = _build(digest=digest)
    assert report["ok"] is True
    assert report["bridge_event_template"]["payload"]["cross_consistency"]["cross_consistent"] is True


def test_good_synthetic_ok_and_schema_valid():
    report = _build()
    assert report["ok"] is True
    event = report["bridge_event_template"]
    validate_event(event)  # schema-valid
    assert event["status"] == mod.EVENT_STATUS
    assert event["payload"]["schema_version"] == mod.TEMPLATE_VERSION


def test_main_exit0_and_exit1(capsys):
    assert mod.main(["--agent", "fable-5", "--task-id", "demo", "--now", _FIXED_NOW, "--json"]) == 0
    # unsafe agent -> error report -> exit 1
    assert mod.main(["--agent", "Bad Agent!", "--task-id", "demo", "--now", _FIXED_NOW, "--json"]) == 1


# ------------------------------------------------------------------ template-only

def test_template_only_authority_axes_all_false():
    report = _build()
    ab = report["bridge_event_template"]["payload"]["authority_boundary"]
    for axis in (
        "approval_granted", "release_decision_made", "merge_decision_made",
        "promotion_granted", "claim_safe", "literal_future_claim_safe",
        "runtime_authority_granted", "scheduler_enqueue_allowed",
        "bridge_event_written", "gate_skip_allowed", "fast_track_priority",
    ):
        assert ab[axis] is False, axis
    assert ab["manual_review_required"] is True
    payload = report["bridge_event_template"]["payload"]
    for flag in (
        "template_only", "direct_bridge_write_performed", "transport_added",
        "external_fetch_performed", "runtime_controls_added",
        "digest_payloads_included", "local_paths_recorded",
    ):
        assert payload[flag] is (flag == "template_only"), flag
    assert report["claim_safe"] is False
    assert report["direct_bridge_write_performed"] is False


# ------------------------------------------------------------------- re-derive

@pytest.mark.parametrize("field", list(mod._DIGEST_VERDICT_FIELDS))
def test_verdicts_rendered_faithfully(field):
    # a False verdict in the digest must render False in the template (no upgrade).
    d = _good_digest()
    d[field] = False
    report = _build(digest=d)
    assert report["ok"] is True  # a not-clean digest is still a valid template to hand off
    assert report["bridge_event_template"]["payload"]["cross_consistency"][field] is False


@pytest.mark.parametrize("field", list(mod._DIGEST_VERDICT_FIELDS))
def test_verdict_non_bool_fails_closed(field):
    # bool-as-int / non-bool verdict -> refuse (the digest is malformed).
    for bad in (1, 0, "true", None):
        d = _good_digest()
        d[field] = bad
        report = _build(digest=d)
        assert report["ok"] is False, (field, bad)


def test_digest_self_claim_safe_refused():
    d = _good_digest()
    d["claim_safe"] = True
    report = _build(digest=d)
    assert report["ok"] is False
    assert "digest_self_claim_safe" in report["blockers"][0]


def test_digest_version_mismatch_fails():
    d = _good_digest()
    d["report_version"] = "wd.some_other.v1"
    assert _build(digest=d)["ok"] is False


# ------------------------------------------------------------------- fail-closed

@pytest.mark.parametrize("digest", [None, "nope", 7, [], {}])
def test_non_mapping_or_empty_digest_fails(digest):
    if digest is None:
        # explicit None is not a Mapping
        report = mod.build_low_risk_cross_consistency_digest_bridge_event_template(
            digest=None, agent_id="fable-5", task_id="demo", now_utc=mod._parse_utc(_FIXED_NOW),
        )
    else:
        report = _build(digest=digest)
    assert report["ok"] is False


@pytest.mark.parametrize("kw,bad", [
    ("agent_id", "Bad Agent"),
    ("agent_id", ""),
    ("task_id", "has space"),
    ("to", ""),
    ("to", "Bad,Targets!"),
    ("severity", "critical"),
    ("role", "Bad Role"),
    ("run_id", "bad id"),
    ("session_id", "bad session!!"),
])
def test_unsafe_bridge_inputs_fail(kw, bad):
    assert _build(**{kw: bad})["ok"] is False


# ------------------------------------------------------------------ content-safe

def test_path_free_and_allowlist():
    report = _build()
    event = report["bridge_event_template"]
    blob = json.dumps(event)
    assert str(REPO_ROOT) not in blob
    assert mod._contains_path_marker(event) is False
    assert report["path_free_verified"] is True
    cross = event["payload"]["cross_consistency"]
    assert set(cross) <= mod._TEMPLATE_SAFE_KEYS
    # only bools + the two fixed strings
    for key, value in cross.items():
        if key in ("digest_schema_version", "digest_ref"):
            assert isinstance(value, str)
        else:
            assert isinstance(value, bool), (key, value)


def test_path_tainted_digest_fails_closed():
    d = _good_digest()
    d["report_version"] = mod.DIGEST_REPORT_VERSION  # keep version valid
    d["injected"] = "C:" + "\\" + "secret" + "\\plan.json"
    report = _build(digest=d)
    assert report["ok"] is False


def test_digest_ref_is_sha256():
    cross = _build()["bridge_event_template"]["payload"]["cross_consistency"]
    ref = cross["digest_ref"]
    assert ref.startswith("sha256:") and len(ref) == len("sha256:") + 64
    assert ref.count("sha256:") == 1  # no double prefix


def test_vocabulary_clean():
    report = _build()
    mod.assert_vocabulary_clean(json.dumps(report))
    mod.assert_vocabulary_clean(report["bridge_event_template"]["message"])


def test_error_report_is_safe_and_fail_closed():
    report = mod._error_report("some_reason")
    assert report["ok"] is False
    assert report["claim_safe"] is False
    assert report["direct_bridge_write_performed"] is False
    assert "bridge_event_template" not in report
