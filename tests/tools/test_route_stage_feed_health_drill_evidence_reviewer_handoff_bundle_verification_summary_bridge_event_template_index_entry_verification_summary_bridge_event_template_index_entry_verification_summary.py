# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import subprocess
import sys
from pathlib import Path

from tools.build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry import (
    INDEX_ENTRY_VERSION,
)
from tools.build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary import (
    ROUTE_STAGE_BUNDLE_TEMPLATE_INDEX_ENTRY_VERIFICATION_KEY,
    SUMMARY_VERSION,
    build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary,
    render_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary_markdown,
)
from tools.verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry import (
    VERIFICATION_VERSION,
    verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry,
)


ROOT = Path(__file__).resolve().parents[2]
HELPER_DIR = ROOT / "tests" / "tools"
if str(HELPER_DIR) not in sys.path:
    sys.path.insert(0, str(HELPER_DIR))

from test_verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry import (  # noqa: E402
    FORBIDDEN_OUTPUT_SNIPPETS,
    _artifact_bytes,
    _artifact_set,
    _index_entry,
    _json_bytes,
)


SCRIPT = (
    ROOT
    / "tools"
    / (
        "build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_"
        "verification_summary_bridge_event_template_index_entry_"
        "verification_summary_bridge_event_template_index_entry_"
        "verification_summary.py"
    )
)
FIXED_NOW = datetime(2026, 6, 19, 7, 20, tzinfo=timezone.utc)


def _joined(*parts: str) -> str:
    return "".join(parts)


def _chars(*codes: int) -> str:
    return "".join(chr(code) for code in codes)


SENSITIVE_PATH_SEGMENT_FIXTURE = _chars(112, 114, 105, 118, 97, 116, 101)
SENSITIVE_TOKEN_PREFIX_FIXTURE = _chars(80, 82, 73, 86, 65, 84, 69, 95)
FORBIDDEN_PATH_PREFIX = _joined("C", ":", "/", SENSITIVE_PATH_SEGMENT_FIXTURE)
FORBIDDEN_VERIFICATION_PATH = _joined(
    FORBIDDEN_PATH_PREFIX,
    "/",
    "index-entry-verification.json",
)


def test_route_stage_handoff_bundle_template_index_entry_verification_summary_renders_without_authority() -> None:
    report = _index_entry_verification_report()

    summary = build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:route-stage-handoff-bundle-template-index-verification",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is True
    assert summary["summary_version"] == SUMMARY_VERSION
    assert summary["created_at_utc"] == "2026-06-19T07:20:00Z"
    verification = summary[ROUTE_STAGE_BUNDLE_TEMPLATE_INDEX_ENTRY_VERIFICATION_KEY]
    assert verification["verification_ok"] is True
    assert verification["verification_version"] == VERIFICATION_VERSION
    assert verification["index_entry_version"] == INDEX_ENTRY_VERSION
    assert verification["artifact_count_checked"] == 2
    assert set(verification["digest_checks"].values()) == {"match"}
    assert set(verification["size_checks"].values()) == {"match"}
    assert set(verification["schema_version_checks"].values()) == {"match"}
    assert verification["source_contract_check"] == "match"
    assert verification["rebuilt_index_entry_check"] == "match"
    assert verification["bridge_event_schema_check"] == "match"
    assert verification["template_only"] is True
    boundary = summary["operator_boundary"]
    assert boundary["verification_report_boundary_ok"] is True
    assert boundary["boundary_blockers"] == []
    assert summary["manual_review_required"] is True
    assert summary["approval_granted"] is False
    assert summary["release_decision_made"] is False
    assert summary["automatic_release_decision"] is False
    assert summary["direct_bridge_write_performed"] is False
    assert summary["transport_added"] is False
    assert summary["external_fetch_performed"] is False
    assert summary["runtime_controls_added"] is False
    assert summary["controls_present"] is False
    assert summary["runtime_authority_granted"] is False
    assert summary["external_writes_applied"] is False
    assert summary["network_access_performed"] is False
    assert summary["artifact_payloads_included"] is False
    assert summary["local_paths_recorded"] is False
    assert not any(
        marker in json.dumps(summary, sort_keys=True)
        for marker in FORBIDDEN_OUTPUT_SNIPPETS
    )


def test_route_stage_handoff_bundle_template_index_entry_verification_summary_markdown_is_path_free() -> None:
    summary = build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=_index_entry_verification_report(),
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:route-stage-handoff-bundle-template-index-verification",
        now_utc=FIXED_NOW,
    )

    markdown = render_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary_markdown(
        summary
    )

    assert "Route-Stage Handoff Bundle Template Index-Entry Verifier Verification Summary" in markdown
    assert "Approval granted: `false`" in markdown
    assert "Artifact payloads included: `false`" in markdown
    assert "Local paths recorded: `false`" in markdown
    assert not any(marker in markdown for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_route_stage_handoff_bundle_template_index_entry_verification_summary_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "verification.json"
    report_path.write_bytes(_json_bytes(_index_entry_verification_report()))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verification-json",
            str(report_path),
            "--reviewer-agent",
            "claude-rco-1",
            "--handoff-ref",
            "bridge:handoff:route-stage-handoff-bundle-template-index-verification",
            "--now",
            "2026-06-19T07:20:00Z",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["direct_bridge_write_performed"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert str(tmp_path) not in result.stdout
    assert report_path.name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_route_stage_handoff_bundle_template_index_entry_verification_summary_rejects_verifier_drift() -> None:
    report = _index_entry_verification_report()
    report["bridge_event_schema_check"] = "failed"

    summary = build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:route-stage-handoff-bundle-template-index-verification",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert "verification_report_bridge_event_schema_check_not_match" in summary[
        "blockers"
    ]
    assert summary["operator_boundary"]["verification_report_boundary_ok"] is False
    assert summary["approval_granted"] is False
    assert summary["release_decision_made"] is False


def test_route_stage_handoff_bundle_template_index_entry_verification_summary_rejects_non_list_blockers() -> None:
    report = _index_entry_verification_report()
    report["blockers"] = "not-a-list"

    summary = build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:route-stage-handoff-bundle-template-index-verification",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert "verification_report_blockers_not_list" in summary["blockers"]
    assert summary["operator_boundary"]["verification_report_boundary_ok"] is False
    assert summary["approval_granted"] is False


def test_route_stage_handoff_bundle_template_index_entry_verification_summary_rejects_non_string_blocker_items() -> None:
    report = _index_entry_verification_report()
    report["blockers"] = [123]

    summary = build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:route-stage-handoff-bundle-template-index-verification",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert "verification_report_blockers_item_not_string" in summary["blockers"]
    assert summary["operator_boundary"]["verification_report_boundary_ok"] is False
    assert summary["release_decision_made"] is False


def test_route_stage_handoff_bundle_template_index_entry_verification_summary_rejects_malformed_warnings() -> None:
    report = _index_entry_verification_report()
    report["warnings"] = [123]

    summary = build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:route-stage-handoff-bundle-template-index-verification",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert "verification_report_warnings_item_not_string" in summary["blockers"]
    assert summary["operator_boundary"]["verification_report_boundary_ok"] is False
    assert summary["direct_bridge_write_performed"] is False


def test_route_stage_handoff_bundle_template_index_entry_verification_summary_rejects_warning_filename_without_leak() -> None:
    report = _index_entry_verification_report()
    report["warnings"] = ["evidence.json"]

    summary = build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:route-stage-handoff-bundle-template-index-verification",
        now_utc=FIXED_NOW,
    )
    markdown = render_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary_markdown(
        summary
    )

    assert summary["ok"] is False
    assert "verification_report_warnings_item_unsafe" in summary["blockers"]
    assert summary["warnings"] == ["invalid_warning_token"]
    verification = summary[ROUTE_STAGE_BUNDLE_TEMPLATE_INDEX_ENTRY_VERIFICATION_KEY]
    assert verification["warnings"] == ["invalid_warning_token"]
    combined = json.dumps(summary, sort_keys=True) + markdown
    assert "evidence.json" not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_route_stage_handoff_bundle_template_index_entry_verification_summary_rejects_authority_escalation() -> None:
    report = _index_entry_verification_report()
    report["approval_granted"] = True

    summary = build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:route-stage-handoff-bundle-template-index-verification",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert "verification_report_approval_granted_not_false" in summary["blockers"]
    assert summary["approval_granted"] is False
    assert summary["direct_bridge_write_performed"] is False


def test_route_stage_handoff_bundle_template_index_entry_verification_summary_rejects_nested_authority_structures() -> None:
    report = _index_entry_verification_report()
    report["operator_boundary"] = {
        "approval_granted": True,
        "release_decision_made": True,
    }

    summary = build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:route-stage-handoff-bundle-template-index-verification",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert "verification_report_forbidden_authority_container:operator_boundary" in (
        summary["blockers"]
    )
    assert "verification_report_nested_authority_field_not_false:approval_granted" in (
        summary["blockers"]
    )
    assert summary["operator_boundary"]["verification_report_boundary_ok"] is False
    assert summary["approval_granted"] is False
    assert summary["release_decision_made"] is False


def test_route_stage_handoff_bundle_template_index_entry_verification_summary_rejects_reviewer_ownership_container() -> None:
    report = _index_entry_verification_report()
    report["reviewer_ownership"] = {"manual_review_required": False}

    summary = build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:route-stage-handoff-bundle-template-index-verification",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert "verification_report_forbidden_authority_container:reviewer_ownership" in (
        summary["blockers"]
    )
    assert summary["operator_boundary"]["verification_report_boundary_ok"] is False
    assert summary["approval_granted"] is False
    assert summary["automatic_release_decision"] is False


def test_route_stage_handoff_bundle_template_index_entry_verification_summary_rejects_raw_payload_key() -> None:
    report = _index_entry_verification_report()
    report["raw_payload"] = {"artifact": "inline-json"}

    summary = build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:route-stage-handoff-bundle-template-index-verification",
        now_utc=FIXED_NOW,
    )

    serialized = json.dumps(summary, sort_keys=True)
    assert summary["ok"] is False
    assert "verification_report_forbidden_payload_key:raw_payload" in summary[
        "blockers"
    ]
    assert "inline-json" not in serialized
    assert summary["artifact_payloads_included"] is False
    assert summary["local_paths_recorded"] is False


def test_route_stage_handoff_bundle_template_index_entry_verification_summary_missing_input_is_path_free() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verification-json",
            FORBIDDEN_VERIFICATION_PATH,
            "--reviewer-agent",
            "claude-rco-1",
            "--handoff-ref",
            "bridge:handoff:route-stage-handoff-bundle-template-index-verification",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["blockers"] == [
        "route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_"
        "verification_summary_bridge_event_template_index_entry_"
        "verification_summary_bridge_event_template_index_entry_"
        "verification_summary_failed:verification_report_unreadable"
    ]
    assert payload["direct_bridge_write_performed"] is False
    assert payload["artifact_payloads_included"] is False
    combined = result.stdout + result.stderr
    assert "index-entry-verification.json" not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_route_stage_handoff_bundle_template_index_entry_verification_summary_non_finite_json_is_path_free(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "verification.json"
    report_path.write_text('{"ok": NaN}', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verification-json",
            str(report_path),
            "--reviewer-agent",
            "claude-rco-1",
            "--handoff-ref",
            "bridge:handoff:route-stage-handoff-bundle-template-index-verification",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["blockers"] == [
        "route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_"
        "verification_summary_bridge_event_template_index_entry_"
        "verification_summary_bridge_event_template_index_entry_"
        "verification_summary_failed:verification_report_json_error"
    ]
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined
    assert str(tmp_path) not in combined
    assert report_path.name not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def _index_entry_verification_report() -> dict:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    raw = _artifact_bytes(artifacts)
    return verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        index_entry=index_entry,
        index_entry_verification_summary=artifacts["summary"],
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=raw["summary"],
        summary_bridge_event_template_bytes=raw["template"],
    )
