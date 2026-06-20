# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry import (
    INDEX_ENTRY_VERSION,
    SUMMARY_ARTIFACT_ID,
    TEMPLATE_ARTIFACT_ID,
)
from tools.build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary import (
    SUMMARY_VERSION,
    VERIFICATION_KEY,
    VERIFICATION_VERSION,
    build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary,
)
from tools.build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template import (
    EVENT_STATUS,
    TEMPLATE_VERSION,
    build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template,
)
from waggledance.core.bridge_event_schema import validate_event


ROOT = Path(__file__).resolve().parents[2]
HELPER_DIR = ROOT / "tests" / "tools"
if str(HELPER_DIR) not in sys.path:
    sys.path.insert(0, str(HELPER_DIR))

from test_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary import (  # noqa: E402
    _index_entry_verification_report,
)


SCRIPT = (
    ROOT
    / "tools"
    / (
        "build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_"
        "verification_summary_bridge_event_template_index_entry_"
        "verification_summary_bridge_event_template_index_entry_"
        "verification_summary_bridge_event_template.py"
    )
)
FIXED_NOW = datetime(2026, 6, 20, 0, 45, tzinfo=timezone.utc)
PRIVATE_MARKERS = ("C:/private", "PRIVATE_", "http://", "https://")
FORBIDDEN_SUMMARY_PATH = "C:/private/verifier-summary.json"
EXPECTED_FAILURE_PREFIX = (
    "route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_"
    "verification_summary_bridge_event_template_index_entry_"
    "verification_summary_bridge_event_template_index_entry_"
    "verification_summary_bridge_event_template_failed:"
)


def test_route_stage_reviewer_handoff_bundle_verifier_summary_verification_summary_bridge_event_template_validates_schema() -> None:
    report = build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template(
        summary=_verifier_summary(),
        agent_id="codex-lead-1",
        task_id="wd-image1-route-stage-reviewer-handoff-verifier-summary-verification-template",
        to="operator,claude-rco-1",
        run_id="codex-lead-1-20260620T004500Z",
        session_id="codex-lead-1-20260620T004500Z",
        now_utc=FIXED_NOW,
    )

    event = report["bridge_event_template"]
    validate_event(event)
    json.dumps(report, allow_nan=False)
    assert report["ok"] is True
    assert report["template_version"] == TEMPLATE_VERSION
    assert report["direct_bridge_write_performed"] is False
    assert report["artifact_payloads_included"] is False
    assert report["local_paths_recorded"] is False
    assert event["status"] == EVENT_STATUS
    assert event["paths"] == []
    assert event["write_scope"] == []
    assert event["cwd"] == "template_not_emitted"
    assert event["pid"] == 0
    payload = event["payload"]
    assert payload["schema_version"] == TEMPLATE_VERSION
    assert payload["summary_version"] == SUMMARY_VERSION
    assert payload["template_only"] is True
    assert payload["manual_review_required"] is True
    assert payload["direct_bridge_write_performed"] is False
    assert payload["transport_added"] is False
    assert payload["external_fetch_performed"] is False
    assert payload["runtime_controls_added"] is False
    assert payload["controls_present"] is False
    assert payload["runtime_authority_granted"] is False
    assert payload["external_writes_applied"] is False
    assert payload["network_access_performed"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert payload["approval_granted"] is False
    assert payload["release_decision_made"] is False
    assert payload["automatic_release_decision"] is False
    verification = payload[VERIFICATION_KEY]
    assert verification["verification_ok"] is True
    assert verification["verification_version"] == VERIFICATION_VERSION
    assert verification["index_entry_version"] == INDEX_ENTRY_VERSION
    assert verification["artifact_count_checked"] == 2
    assert verification["source_contract_check"] == "match"
    assert verification["rebuilt_index_entry_check"] == "match"
    assert verification["bridge_event_schema_check"] == "match"
    assert verification["template_only"] is True
    assert verification["blocker_count"] == 0
    assert set(verification["digest_checks"].values()) == {"match"}
    assert set(verification["size_checks"].values()) == {"match"}
    assert set(verification["schema_version_checks"].values()) == {"match"}
    assert sorted(verification["digest_checks"]) == sorted(
        (SUMMARY_ARTIFACT_ID, TEMPLATE_ARTIFACT_ID)
    )
    boundary = payload["operator_boundary"]
    assert boundary["verification_report_boundary_ok"] is True
    assert boundary["boundary_blockers"] == []
    assert boundary["approval_granted"] is False
    assert boundary["release_decision_made"] is False
    assert boundary["runtime_authority_granted"] is False
    assert not any(marker in json.dumps(report, sort_keys=True) for marker in PRIVATE_MARKERS)


def test_route_stage_reviewer_handoff_bundle_verifier_summary_verification_summary_bridge_event_template_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    summary_path = tmp_path / "index_entry_verification_summary.json"
    summary_path.write_bytes(_json_bytes(_verifier_summary()))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-json",
            str(summary_path),
            "--agent",
            "codex-lead-1",
            "--task-id",
            "wd-image1-route-stage-reviewer-handoff-verifier-summary-verification-template",
            "--to",
            "operator,claude-rco-1",
            "--run-id",
            "codex-lead-1-20260620T004500Z",
            "--session-id",
            "codex-lead-1-20260620T004500Z",
            "--now",
            "2026-06-20T00:45:00Z",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    event = payload["bridge_event_template"]
    validate_event(event)
    assert payload["direct_bridge_write_performed"] is False
    assert payload["approval_granted"] is False
    assert payload["release_decision_made"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert event["payload"][VERIFICATION_KEY]["verification_ok"] is True
    assert str(tmp_path) not in result.stdout
    assert summary_path.name not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)


def test_route_stage_reviewer_handoff_bundle_verifier_summary_verification_summary_bridge_event_template_missing_input_is_path_free() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-json",
            FORBIDDEN_SUMMARY_PATH,
            "--agent",
            "codex-lead-1",
            "--task-id",
            "wd-image1-route-stage-reviewer-handoff-verifier-summary-verification-template",
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
        EXPECTED_FAILURE_PREFIX + "index_entry_verification_summary_unreadable"
    ]
    assert payload["direct_bridge_write_performed"] is False
    assert payload["artifact_payloads_included"] is False
    combined = result.stdout + result.stderr
    assert "verifier-summary.json" not in combined
    assert not any(marker in combined for marker in PRIVATE_MARKERS)


def test_route_stage_reviewer_handoff_bundle_verifier_summary_verification_summary_bridge_event_template_rejects_unsafe_bridge_fields(
    tmp_path: Path,
) -> None:
    summary_path = tmp_path / "index_entry_verification_summary.json"
    summary_path.write_bytes(_json_bytes(_verifier_summary()))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-json",
            str(summary_path),
            "--agent",
            "Codex",
            "--task-id",
            "wd-image1-route-stage-reviewer-handoff-verifier-summary-verification-template",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["blockers"] == [EXPECTED_FAILURE_PREFIX + "agent_unsafe"]
    assert str(tmp_path) not in result.stdout
    assert summary_path.name not in result.stdout


def test_route_stage_reviewer_handoff_bundle_verifier_summary_verification_summary_bridge_event_template_blocks_unsafe_summary_contract(
    tmp_path: Path,
) -> None:
    cases = (
        (
            "summary_not_ok",
            lambda summary: summary.__setitem__("ok", False),
            "index_entry_verification_summary_not_ok",
        ),
        (
            "approval_granted",
            lambda summary: summary.__setitem__("approval_granted", True),
            "index_entry_verification_summary_approval_granted_not_false",
        ),
        (
            "source_contract_mismatch",
            lambda summary: summary[VERIFICATION_KEY].__setitem__(
                "source_contract_check",
                "mismatch",
            ),
            "index_entry_verification_source_contract_not_match",
        ),
        (
            "verification_version_mismatch",
            lambda summary: summary[VERIFICATION_KEY].__setitem__(
                "verification_version",
                "unknown",
            ),
            "index_entry_verification_version_mismatch",
        ),
        (
            "blocker_count_nonzero",
            lambda summary: summary[VERIFICATION_KEY].__setitem__(
                "blocker_count",
                1,
            ),
            "index_entry_verification_blocker_count_nonzero",
        ),
        (
            "boundary_not_ok",
            lambda summary: summary["operator_boundary"].__setitem__(
                "verification_report_boundary_ok",
                False,
            ),
            "operator_boundary_verification_report_not_ok",
        ),
        (
            "network_access",
            lambda summary: summary.__setitem__("network_access_performed", True),
            "index_entry_verification_summary_network_access_performed_not_false",
        ),
    )

    for label, mutate, expected_reason in cases:
        summary = _verifier_summary()
        mutate(summary)
        summary_path = tmp_path / f"{label}.json"
        summary_path.write_bytes(_json_bytes(summary))

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--summary-json",
                str(summary_path),
                "--agent",
                "codex-lead-1",
                "--task-id",
                "wd-image1-route-stage-reviewer-handoff-verifier-summary-verification-template",
                "--to",
                "operator,claude-rco-1",
                "--now",
                "2026-06-20T00:45:00Z",
                "--json",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        assert result.returncode == 1, label
        report = json.loads(result.stdout)
        assert report["ok"] is False, label
        assert report["blockers"] == [
            EXPECTED_FAILURE_PREFIX + expected_reason
        ], label
        assert report["approval_granted"] is False
        assert report["release_decision_made"] is False
        assert report["direct_bridge_write_performed"] is False
        assert str(tmp_path) not in result.stdout
        assert summary_path.name not in result.stdout


def test_route_stage_reviewer_handoff_bundle_verifier_summary_verification_summary_bridge_event_template_rejects_filename_warning_tokens_without_leak() -> None:
    cases = (
        (
            "summary-warning-report.json",
            lambda summary, token: summary.__setitem__("warnings", [token]),
            "index_entry_verification_summary_warnings_item_unsafe",
        ),
        (
            "verification-warning.log",
            lambda summary, token: summary[VERIFICATION_KEY].__setitem__(
                "warnings",
                [token],
            ),
            "index_entry_verification_warnings_item_unsafe",
        ),
    )

    for token, mutate, expected_reason in cases:
        summary = _verifier_summary()
        mutate(summary, token)

        report = build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template(
            summary=summary,
            agent_id="codex-lead-1",
            task_id="wd-image1-route-stage-reviewer-handoff-verifier-summary-verification-template",
            to="operator,claude-rco-1",
            run_id="codex-lead-1-20260620T004500Z",
            session_id="codex-lead-1-20260620T004500Z",
            now_utc=FIXED_NOW,
        )

        encoded = json.dumps(report, sort_keys=True)
        assert report["ok"] is False, token
        assert report["blockers"] == [EXPECTED_FAILURE_PREFIX + expected_reason]
        assert report["direct_bridge_write_performed"] is False
        assert report["artifact_payloads_included"] is False
        assert report["local_paths_recorded"] is False
        assert token not in encoded


def test_route_stage_reviewer_handoff_bundle_verifier_summary_verification_summary_bridge_event_template_non_finite_json_is_path_free(
    tmp_path: Path,
) -> None:
    summary_path = tmp_path / "index_entry_verification_summary.json"
    summary = _verifier_summary()
    summary["warnings"] = [float("nan")]
    summary_path.write_text(json.dumps(summary, sort_keys=True), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-json",
            str(summary_path),
            "--agent",
            "codex-lead-1",
            "--task-id",
            "wd-image1-route-stage-reviewer-handoff-verifier-summary-verification-template",
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
        EXPECTED_FAILURE_PREFIX + "index_entry_verification_summary_json_error"
    ]
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined
    assert str(tmp_path) not in combined
    assert summary_path.name not in combined
    assert not any(marker in combined for marker in PRIVATE_MARKERS)


def _verifier_summary() -> dict:
    return build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=_index_entry_verification_report(),
        reviewer_agent_id="claude-rco-1",
        handoff_ref=(
            "bridge:handoff:route-stage-reviewer-handoff-bundle-"
            "verifier-summary-template-index-verification"
        ),
        now_utc=FIXED_NOW,
    )


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, allow_nan=False).encode("utf-8")
