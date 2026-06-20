"""Offline tests for build_low_risk_cross_consistency_digest_bridge_event_template_index_entry.py.

Covers the index-entry contract: a path-free, content-safe local index entry binding the
#1291 template to stable digests + safe-scalar verdicts (never the raw event/message),
schema-validated, every authority axis strictly False, claim_safe hardcoded False, and
FAIL-CLOSED on a not-ok / version-mismatch / missing-cross-consistency / authority-True /
self-claim_safe / path-tainted / non-mapping source template.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import json

import pytest

import tools.build_low_risk_cross_consistency_digest_bridge_event_template_index_entry as mod
from tools.build_low_risk_cross_consistency_digest_bridge_event_template import (
    build_low_risk_cross_consistency_digest_bridge_event_template as _build_template,
)

_FIXED = datetime(2026, 6, 19, 6, 0, 0, tzinfo=timezone.utc)


def _good_digest() -> dict:
    return {
        "report_version": "wd.low_risk_cross_consistency_digest.v1",
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


def _good_template_report() -> dict:
    return _build_template(
        digest=_good_digest(), agent_id="fable-5", task_id="demo-task", now_utc=_FIXED
    )


def _index_entry(report):
    return mod.build_low_risk_cross_consistency_digest_bridge_event_template_index_entry(
        bridge_event_template_report=report,
        bridge_event_template_bytes=json.dumps(report, sort_keys=True).encode(),
        now_utc=_FIXED,
    )


# --------------------------------------------------------------------------- good

def test_real_template_index_entry_ok():
    e = _index_entry(_good_template_report())
    assert e["ok"] is True
    assert e["index_entry_version"] == mod.INDEX_ENTRY_VERSION
    tie = e["template_index_entry"]
    assert tie["artifact_id"] == mod.TEMPLATE_ARTIFACT_ID
    assert tie["bridge_event_schema_validated"] is True
    assert tie["cross_consistent"] is True
    assert tie["cross_consistency_digest_ref"].startswith("sha256:")
    assert e["template_only"] is True
    assert e["manual_review_required"] is True


def test_validate_passes_for_good_entry():
    e = _index_entry(_good_template_report())
    assert mod.validate_low_risk_cross_consistency_digest_bridge_event_template_index_entry(e) == []


def test_index_entry_authority_axes_all_false():
    e = _index_entry(_good_template_report())
    for field in mod.AUTHORITY_FALSE_FIELDS:
        assert e[field] is False, field
        assert e["template_index_entry"][field] is False, field
    assert e["claim_safe"] is False


def test_index_entry_content_safe_no_raw_event():
    e = _index_entry(_good_template_report())
    blob = json.dumps(e)
    # no raw source bridge-event fields leaked (the entry binds digests, not the event).
    for raw in ("template ready", "codex-lead-1", "\"ts_utc\""):
        assert raw not in blob, raw
    tie = e["template_index_entry"]
    # the cross-consistency verdicts are surfaced as strict bools
    for field in mod._DIGEST_VERDICT_FIELDS:
        assert isinstance(tie[field], bool), field


def test_digest_ref_recorded_sha256():
    e = _index_entry(_good_template_report())
    ref = e["template_index_entry"]["cross_consistency_digest_ref"]
    assert ref.startswith("sha256:") and len(ref) == len("sha256:") + 64


def test_main_exit0(tmp_path, capsys):
    report = _good_template_report()
    p = tmp_path / "template.json"
    p.write_text(json.dumps(report), encoding="utf-8")
    assert mod.main([
        "--bridge-event-template-json", str(p), "--now", "2026-06-19T06:00:00Z", "--json",
    ]) == 0


# ------------------------------------------------------------------- fail-closed

@pytest.mark.parametrize("proof", [None, "nope", 7, [], {}])
def test_non_mapping_template_fails(proof):
    if proof is None:
        e = mod.build_low_risk_cross_consistency_digest_bridge_event_template_index_entry(
            bridge_event_template_report=None, bridge_event_template_bytes=b"{}", now_utc=_FIXED
        )
    else:
        e = _index_entry(proof)
    assert e["ok"] is False


def test_template_not_ok_fails():
    r = _good_template_report()
    r["ok"] = False
    assert _index_entry(r)["ok"] is False


def test_template_version_mismatch_fails():
    r = _good_template_report()
    r["template_version"] = "wd.something_else.v1"
    assert _index_entry(r)["ok"] is False


def test_template_self_claim_safe_fails():
    r = _good_template_report()
    r["claim_safe"] = True
    assert _index_entry(r)["ok"] is False


@pytest.mark.parametrize("field", list(mod._TEMPLATE_REPORT_FALSE_FIELDS))
def test_template_report_authority_true_fails(field):
    r = _good_template_report()
    r[field] = True
    assert _index_entry(r)["ok"] is False, field


@pytest.mark.parametrize("field", list(mod._BOUNDARY_FALSE_FIELDS))
def test_template_boundary_authority_true_fails(field):
    r = _good_template_report()
    r["bridge_event_template"]["payload"]["authority_boundary"][field] = True
    assert _index_entry(r)["ok"] is False, field


def test_template_missing_cross_consistency_fails():
    r = _good_template_report()
    del r["bridge_event_template"]["payload"]["cross_consistency"]
    assert _index_entry(r)["ok"] is False


@pytest.mark.parametrize("field", list(mod._DIGEST_VERDICT_FIELDS))
def test_template_cross_verdict_non_bool_fails(field):
    r = _good_template_report()
    r["bridge_event_template"]["payload"]["cross_consistency"][field] = 1
    assert _index_entry(r)["ok"] is False, field


def test_template_bad_digest_ref_fails():
    r = _good_template_report()
    r["bridge_event_template"]["payload"]["cross_consistency"]["digest_ref"] = "not-a-ref"
    assert _index_entry(r)["ok"] is False


def test_template_blockers_present_fails():
    r = _good_template_report()
    r["blockers"] = ["something"]
    assert _index_entry(r)["ok"] is False


def test_template_schema_invalid_fails():
    r = _good_template_report()
    r["bridge_event_template"]["type"] = "not-handoff"
    assert _index_entry(r)["ok"] is False


def test_path_tainted_template_fails():
    r = _good_template_report()
    r["bridge_event_template"]["payload"]["injected"] = "C:" + "\\" + "secret"
    # a forbidden output marker in the source -> fail closed
    assert _index_entry(r)["ok"] is False


@pytest.mark.parametrize(
    "warning",
    [
        "lowercase authorization bearer marker should fail closed",
        "UPPERCASE SECRET marker should fail closed",
    ],
)
def test_forbidden_marker_case_variants_fail_closed(warning):
    r = _good_template_report()
    r["warnings"] = [warning]
    e = _index_entry(r)
    assert e["ok"] is False
    assert "warnings" not in e or warning not in e["warnings"]


# ------------------------------------------------------------ validate() forge

def test_validate_rejects_authority_true():
    e = _index_entry(_good_template_report())
    bad = copy.deepcopy(e)
    bad["runtime_authority_granted"] = True
    errs = mod.validate_low_risk_cross_consistency_digest_bridge_event_template_index_entry(bad)
    assert any("runtime_authority_granted" in x for x in errs)


def test_failure_report_is_safe():
    r = mod._failure_report("some_reason")
    assert r["ok"] is False
    assert r["claim_safe"] is False
    assert "template_index_entry" not in r
