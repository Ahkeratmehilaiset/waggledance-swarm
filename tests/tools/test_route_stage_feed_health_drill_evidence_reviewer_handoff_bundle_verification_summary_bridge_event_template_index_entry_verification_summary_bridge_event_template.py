# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import subprocess
import sys
from pathlib import Path

from tools.build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary import (
    ROUTE_STAGE_BUNDLE_TEMPLATE_INDEX_ENTRY_VERIFICATION_KEY,
    SUMMARY_VERSION,
    build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary,
)
from tools.build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template import (
    EVENT_STATUS,
    TEMPLATE_VERSION,
    build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template,
)
from tools.verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry import (
    VERIFICATION_VERSION,
)
from waggledance.core.bridge_event_schema import validate_event


ROOT = Path(__file__).resolve().parents[2]
HELPER_DIR = ROOT / "tests" / "tools"
if str(HELPER_DIR) not in sys.path:
    sys.path.insert(0, str(HELPER_DIR))

from test_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary import (  # noqa: E402
    FORBIDDEN_OUTPUT_SNIPPETS,
    _index_entry_verification_report,
    _json_bytes,
)


SCRIPT = (
    ROOT
    / "tools"
    / (
        "build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_"
        "verification_summary_bridge_event_template_index_entry_"
        "verification_summary_bridge_event_template.py"
    )
)
FIXED_NOW = datetime(2026, 6, 20, 0, 5, tzinfo=timezone.utc)


def _joined(*parts: str) -> str:
    return "".join(parts)


def _chars(*codes: int) -> str:
    return "".join(chr(code) for code in codes)


SENSITIVE_PATH_SEGMENT_FIXTURE = _chars(112, 114, 105, 118, 97, 116, 101)
FORBIDDEN_PATH_PREFIX = _joined("C", ":", "/", SENSITIVE_PATH_SEGMENT_FIXTURE)
FORBIDDEN_SUMMARY_PATH = _joined(
    FORBIDDEN_PATH_PREFIX,
    "/",
    "index-entry-verification-summary.json",
)


def test_route_stage_handoff_bundle_template_index_entry_verification_summary_bridge_event_template_validates_schema() -> None:
    report = build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template(
        summary=_index_entry_verification_summary(),
        agent_id="codex-lead-1",
        task_id="wd-image1-route-stage-handoff-bundle-template-index-summary",
        to="operator,claude-rco-1",
        run_id="codex-lead-1-20260620T000500Z",
        session_id="codex-lead-1-20260620T000500Z",
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
    assert event["type"] == "handoff"
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
    verification = payload[ROUTE_STAGE_BUNDLE_TEMPLATE_INDEX_ENTRY_VERIFICATION_KEY]
    assert verification["verification_ok"] is True
    assert verification["verification_version"] == VERIFICATION_VERSION
    assert verification["artifact_count_checked"] == 2
    assert verification["source_contract_check"] == "match"
    assert verification["rebuilt_index_entry_check"] == "match"
    assert verification["bridge_event_schema_check"] == "match"
    assert verification["template_only"] is True
    assert verification["blocker_count"] == 0
    assert set(verification["digest_checks"].values()) == {"match"}
    assert set(verification["size_checks"].values()) == {"match"}
    assert set(verification["schema_version_checks"].values()) == {"match"}
    boundary = payload["operator_boundary"]
    assert boundary["verification_report_boundary_ok"] is True
    assert boundary["approval_granted"] is False
    assert boundary["release_decision_made"] is False
    assert boundary["runtime_authority_granted"] is False
    assert not any(
        marker in json.dumps(report, sort_keys=True)
        for marker in FORBIDDEN_OUTPUT_SNIPPETS
    )


def test_route_stage_handoff_bundle_template_index_entry_verification_summary_bridge_event_template_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    summary_path = tmp_path / "index_entry_verification_summary.json"
    summary_path.write_bytes(_json_bytes(_index_entry_verification_summary()))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-json",
            str(summary_path),
            "--agent",
            "codex-lead-1",
            "--task-id",
            "wd-image1-route-stage-handoff-bundle-template-index-summary",
            "--to",
            "operator,claude-rco-1",
            "--run-id",
            "codex-lead-1-20260620T000500Z",
            "--session-id",
            "codex-lead-1-20260620T000500Z",
            "--now",
            "2026-06-20T00:05:00Z",
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
    assert (
        event["payload"][ROUTE_STAGE_BUNDLE_TEMPLATE_INDEX_ENTRY_VERIFICATION_KEY][
            "verification_ok"
        ]
        is True
    )
    assert str(tmp_path) not in result.stdout
    assert summary_path.name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_route_stage_handoff_bundle_template_index_entry_verification_summary_bridge_event_template_missing_input_is_path_free() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-json",
            FORBIDDEN_SUMMARY_PATH,
            "--agent",
            "codex-lead-1",
            "--task-id",
            "wd-image1-route-stage-handoff-bundle-template-index-summary",
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
        "verification_summary_bridge_event_template_failed:"
        "index_entry_verification_summary_unreadable"
    ]
    assert payload["direct_bridge_write_performed"] is False
    assert payload["artifact_payloads_included"] is False
    combined = result.stdout + result.stderr
    assert "index-entry-verification-summary.json" not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_route_stage_handoff_bundle_template_index_entry_verification_summary_bridge_event_template_rejects_unsafe_bridge_fields(
    tmp_path: Path,
) -> None:
    summary_path = tmp_path / "index_entry_verification_summary.json"
    summary_path.write_bytes(_json_bytes(_index_entry_verification_summary()))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-json",
            str(summary_path),
            "--agent",
            "Codex",
            "--task-id",
            "wd-image1-route-stage-handoff-bundle-template-index-summary",
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
        "verification_summary_bridge_event_template_failed:agent_unsafe"
    ]
    assert str(tmp_path) not in result.stdout
    assert summary_path.name not in result.stdout


def test_route_stage_handoff_bundle_template_index_entry_verification_summary_bridge_event_template_blocks_unsafe_summary_contract(
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
            lambda summary: summary[
                ROUTE_STAGE_BUNDLE_TEMPLATE_INDEX_ENTRY_VERIFICATION_KEY
            ].__setitem__("source_contract_check", "mismatch"),
            "index_entry_verification_source_contract_not_match",
        ),
        (
            "warning_filename",
            lambda summary: summary.__setitem__("warnings", ["evidence.json"]),
            "index_entry_verification_summary_warnings_item_unsafe",
        ),
        (
            "boundary_not_ok",
            lambda summary: summary["operator_boundary"].__setitem__(
                "verification_report_boundary_ok",
                False,
            ),
            "operator_boundary_verification_report_not_ok",
        ),
    )

    for label, mutate, expected_reason in cases:
        summary = deepcopy(_index_entry_verification_summary())
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
                "wd-image1-route-stage-handoff-bundle-template-index-summary",
                "--to",
                "operator,claude-rco-1",
                "--now",
                "2026-06-20T00:05:00Z",
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
            "route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_"
            "verification_summary_bridge_event_template_index_entry_"
            f"verification_summary_bridge_event_template_failed:{expected_reason}"
        ], label
        assert report["approval_granted"] is False
        assert report["release_decision_made"] is False
        assert report["direct_bridge_write_performed"] is False
        combined = result.stdout + result.stderr
        assert str(tmp_path) not in combined
        assert summary_path.name not in combined
        assert "evidence.json" not in combined


def test_route_stage_handoff_bundle_template_index_entry_verification_summary_bridge_event_template_rejects_path_markers_without_leak(
    tmp_path: Path,
) -> None:
    summary = _index_entry_verification_summary()
    summary["warnings"] = [_joined(FORBIDDEN_PATH_PREFIX, "/", "report.json")]
    summary_path = tmp_path / "index_entry_verification_summary.json"
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
            "wd-image1-route-stage-handoff-bundle-template-index-summary",
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
        "verification_summary_bridge_event_template_failed:"
        "index_entry_verification_summary_forbidden_marker"
    ]
    combined = result.stdout + result.stderr
    assert str(tmp_path) not in combined
    assert summary_path.name not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_route_stage_handoff_bundle_template_index_entry_verification_summary_bridge_event_template_non_finite_json_is_path_free(
    tmp_path: Path,
) -> None:
    summary_path = tmp_path / "index_entry_verification_summary.json"
    summary = _index_entry_verification_summary()
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
            "wd-image1-route-stage-handoff-bundle-template-index-summary",
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
        "verification_summary_bridge_event_template_failed:"
        "index_entry_verification_summary_json_error"
    ]
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined
    assert str(tmp_path) not in combined
    assert summary_path.name not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def _index_entry_verification_summary() -> dict:
    return build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=_index_entry_verification_report(),
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:route-stage-handoff-bundle-template-index-verification-summary",
        now_utc=FIXED_NOW,
    )
