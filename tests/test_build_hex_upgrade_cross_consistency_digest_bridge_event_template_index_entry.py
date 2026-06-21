"""Offline tests for the hex cross-consistency bridge-template index entry.

Covers the local index-entry contract: path-free digest binding only, no raw
event/message payloads, every authority axis strictly False, and fail-closed
behavior for malformed, self-approving, path-tainted, or authority-granting
source templates.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import json

import pytest

import tools.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry as mod
from tools.build_hex_upgrade_cross_consistency_digest_bridge_event_template import (
    build_hex_upgrade_cross_consistency_digest_bridge_event_template as _build_template,
)

_FIXED = datetime(2026, 6, 21, 4, 30, 0, tzinfo=timezone.utc)


def _good_digest() -> dict:
    return {
        "report_version": "wd.hex_upgrade_cross_consistency_digest.v1",
        "reviewer_summary_present": True,
        "shadow_only_invariant_present": True,
        "chain_final_summary_present": True,
        "all_views_present": True,
        "reviewer_clean": True,
        "shadow_only_clean": True,
        "chain_summary_clean": True,
        "cross_consistent": True,
        "path_free_verified": True,
        "claim_safe": False,
    }


def _good_template_report() -> dict:
    return _build_template(
        digest=_good_digest(),
        agent_id="fable-5",
        task_id="demo-task",
        now_utc=_FIXED,
    )


def _index_entry(report):
    return mod.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
        bridge_event_template_report=report,
        bridge_event_template_bytes=json.dumps(report, sort_keys=True).encode(),
        now_utc=_FIXED,
    )


def test_real_template_index_entry_ok() -> None:
    entry = _index_entry(_good_template_report())

    assert entry["ok"] is True
    assert entry["index_entry_version"] == mod.INDEX_ENTRY_VERSION
    tie = entry["template_index_entry"]
    assert tie["artifact_id"] == mod.TEMPLATE_ARTIFACT_ID
    assert tie["bridge_event_schema_validated"] is True
    assert tie["digest_schema_version"] == mod.SOURCE_DIGEST_REPORT_VERSION
    assert tie["cross_consistent"] is True
    assert tie["cross_consistency_digest_ref"].startswith("sha256:")
    assert entry["template_only"] is True
    assert entry["manual_review_required"] is True


def test_validate_passes_for_good_entry() -> None:
    entry = _index_entry(_good_template_report())
    assert (
        mod.validate_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
            entry
        )
        == []
    )


def test_index_entry_authority_axes_all_false() -> None:
    entry = _index_entry(_good_template_report())
    for field in mod.AUTHORITY_FALSE_FIELDS:
        assert entry[field] is False, field
        assert entry["template_index_entry"][field] is False, field
    assert entry["runtime_subdivision_authority_granted"] is False
    assert entry["claim_safe"] is False


def test_index_entry_content_safe_no_raw_event() -> None:
    entry = _index_entry(_good_template_report())
    blob = json.dumps(entry)
    for raw in ("template ready", "fable-5", "\"ts_utc\""):
        assert raw not in blob, raw
    tie = entry["template_index_entry"]
    for field in mod._DIGEST_VERDICT_FIELDS:
        assert isinstance(tie[field], bool), field


def test_digest_ref_recorded_sha256() -> None:
    entry = _index_entry(_good_template_report())
    ref = entry["template_index_entry"]["cross_consistency_digest_ref"]
    assert ref.startswith("sha256:")
    assert len(ref) == len("sha256:") + 64


def test_main_exit0(tmp_path, capsys) -> None:
    report = _good_template_report()
    path = tmp_path / "template.json"
    path.write_text(json.dumps(report), encoding="utf-8")

    assert (
        mod.main(
            [
                "--bridge-event-template-json",
                str(path),
                "--now",
                "2026-06-21T04:30:00Z",
                "--json",
            ]
        )
        == 0
    )


def test_semantically_same_template_bytes_are_accepted() -> None:
    report = _good_template_report()
    entry = (
        mod.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
            bridge_event_template_report=report,
            bridge_event_template_bytes=json.dumps(report, indent=2).encode(),
            now_utc=_FIXED,
        )
    )

    assert entry["ok"] is True


def test_template_bytes_mismatch_fails_closed() -> None:
    entry = (
        mod.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
            bridge_event_template_report=_good_template_report(),
            bridge_event_template_bytes=b'{"different":true}',
            now_utc=_FIXED,
        )
    )

    assert entry["ok"] is False
    assert "bridge_event_template_bytes_mismatch" in entry["blockers"][0]


@pytest.mark.parametrize("proof", [None, "nope", 7, [], {}])
def test_non_mapping_template_fails(proof) -> None:
    if proof is None:
        entry = mod.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
            bridge_event_template_report=None,
            bridge_event_template_bytes=b"{}",
            now_utc=_FIXED,
        )
    else:
        entry = _index_entry(proof)
    assert entry["ok"] is False


def test_template_not_ok_fails() -> None:
    report = _good_template_report()
    report["ok"] = False
    assert _index_entry(report)["ok"] is False


def test_template_version_mismatch_fails() -> None:
    report = _good_template_report()
    report["template_version"] = "wd.something_else.v1"
    assert _index_entry(report)["ok"] is False


def test_template_self_claim_safe_fails() -> None:
    report = _good_template_report()
    report["claim_safe"] = True
    assert _index_entry(report)["ok"] is False


@pytest.mark.parametrize("field", list(mod._TEMPLATE_REPORT_FALSE_FIELDS))
def test_template_report_authority_true_fails(field: str) -> None:
    report = _good_template_report()
    report[field] = True
    assert _index_entry(report)["ok"] is False, field


@pytest.mark.parametrize("field", list(mod._BOUNDARY_FALSE_FIELDS))
def test_template_boundary_authority_true_fails(field: str) -> None:
    report = _good_template_report()
    report["bridge_event_template"]["payload"]["authority_boundary"][field] = True
    assert _index_entry(report)["ok"] is False, field


@pytest.mark.parametrize("field", list(mod._PAYLOAD_FALSE_FIELDS))
def test_template_payload_authority_true_fails(field: str) -> None:
    report = _good_template_report()
    report["bridge_event_template"]["payload"][field] = True
    assert _index_entry(report)["ok"] is False, field


def test_template_missing_cross_consistency_fails() -> None:
    report = _good_template_report()
    del report["bridge_event_template"]["payload"]["cross_consistency"]
    assert _index_entry(report)["ok"] is False


@pytest.mark.parametrize("field", list(mod._DIGEST_VERDICT_FIELDS))
def test_template_cross_verdict_non_bool_fails(field: str) -> None:
    report = _good_template_report()
    report["bridge_event_template"]["payload"]["cross_consistency"][field] = 1
    assert _index_entry(report)["ok"] is False, field


def test_template_bad_digest_ref_fails() -> None:
    report = _good_template_report()
    report["bridge_event_template"]["payload"]["cross_consistency"][
        "digest_ref"
    ] = "not-a-ref"
    assert _index_entry(report)["ok"] is False


@pytest.mark.parametrize(
    "digest_schema_version",
    [
        {"version": "wd.hex_upgrade_cross_consistency_digest.v1"},
        1,
        "wd.other.v1",
        None,
    ],
)
def test_template_digest_schema_version_mismatch_fails(digest_schema_version) -> None:
    report = _good_template_report()
    report["bridge_event_template"]["payload"]["cross_consistency"][
        "digest_schema_version"
    ] = digest_schema_version
    entry = _index_entry(report)
    assert entry["ok"] is False
    assert "digest_schema_version_mismatch" in entry["blockers"][0]


def test_template_blockers_present_fails() -> None:
    report = _good_template_report()
    report["blockers"] = ["something"]
    assert _index_entry(report)["ok"] is False


def test_template_schema_invalid_fails() -> None:
    report = _good_template_report()
    report["bridge_event_template"]["type"] = "not-handoff"
    assert _index_entry(report)["ok"] is False


def test_path_tainted_template_fails() -> None:
    report = _good_template_report()
    report["bridge_event_template"]["payload"]["injected"] = "C:" + "\\" + "secret"
    assert _index_entry(report)["ok"] is False


@pytest.mark.parametrize(
    "warning",
    [
        "lowercase authorization bearer marker should fail closed",
        "UPPERCASE SECRET marker should fail closed",
    ],
)
def test_forbidden_marker_case_variants_fail_closed(warning: str) -> None:
    report = _good_template_report()
    report["warnings"] = [warning]
    entry = _index_entry(report)
    assert entry["ok"] is False
    assert "warnings" not in entry or warning not in entry["warnings"]


def test_validate_rejects_authority_true() -> None:
    entry = _index_entry(_good_template_report())
    bad = copy.deepcopy(entry)
    bad["runtime_subdivision_authority_granted"] = True

    errors = (
        mod.validate_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
            bad
        )
    )

    assert any("runtime_subdivision_authority_granted" in item for item in errors)


def test_failure_report_is_safe() -> None:
    report = mod._failure_report("some_reason")
    assert report["ok"] is False
    assert report["claim_safe"] is False
    assert report["runtime_subdivision_authority_granted"] is False
    assert "template_index_entry" not in report
