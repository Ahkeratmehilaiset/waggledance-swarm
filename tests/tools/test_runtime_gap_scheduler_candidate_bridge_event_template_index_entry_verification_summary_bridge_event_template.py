from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry import (
    ARTIFACT_ID,
    INDEX_ENTRY_VERSION,
    TEMPLATE_ARTIFACT_ID,
)
import tools.build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary as runtime_summary
from tools.build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary import (
    SUMMARY_VERSION,
    build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary,
)
from tools.build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary_bridge_event_template import (
    EVENT_STATUS,
    RUNTIME_GAP_VERIFICATION_KEY,
    TEMPLATE_VERSION,
    build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary_bridge_event_template,
)
from tools.verify_runtime_gap_scheduler_candidate_bridge_event_template_index_entry import (
    VERIFICATION_VERSION,
)
from waggledance.core.bridge_event_schema import validate_event


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "tools"
    / (
        "build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_"
        "verification_summary_bridge_event_template.py"
    )
)
FIXED_NOW = datetime(2026, 6, 7, 15, 0, tzinfo=timezone.utc)
FORBIDDEN_OUTPUT_SNIPPETS = (
    "C:/private",
    "PRIVATE_",
    "http://",
    "https://",
)


def test_runtime_gap_index_entry_verification_summary_bridge_event_template_validates_bridge_schema() -> None:
    report = build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary_bridge_event_template(
        summary=_index_entry_verification_summary(),
        agent_id="codex-lead-1",
        task_id="wd-runtime-gap-summary-template",
        to="operator,claude-rco-1",
        run_id="codex-lead-1-20260607T150000Z",
        session_id="codex-lead-1-20260607T150000Z",
        now_utc=FIXED_NOW,
    )

    event = report["bridge_event_template"]
    validate_event(event)
    json.dumps(report, allow_nan=False)
    assert report["ok"] is True
    assert report["template_version"] == TEMPLATE_VERSION
    assert report["direct_bridge_write_performed"] is False
    assert report["scheduler_enqueue_allowed"] is False
    assert report["scheduler_tick_allowed"] is False
    assert report["fast_track_priority"] is False
    assert report["gate_skip_allowed"] is False
    assert report["bridge_event_written"] is False
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
    assert payload["approval_granted"] is False
    assert payload["release_decision_made"] is False
    assert payload["automatic_release_decision"] is False
    assert payload["direct_bridge_write_performed"] is False
    assert payload["transport_added"] is False
    assert payload["external_fetch_performed"] is False
    assert payload["runtime_controls_added"] is False
    assert payload["runtime_authority_granted"] is False
    assert payload["scheduler_enqueue_allowed"] is False
    assert payload["scheduler_tick_allowed"] is False
    assert payload["scheduler_tick_executed"] is False
    assert payload["queue_writes_applied"] is False
    assert payload["control_plane_writes_applied"] is False
    assert payload["bridge_event_written"] is False
    assert payload["fast_track_priority"] is False
    assert payload["gate_skip_allowed"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    verification = payload[RUNTIME_GAP_VERIFICATION_KEY]
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
    boundary = payload["operator_boundary"]
    assert boundary["verification_report_boundary_ok"] is True
    assert boundary["runtime_authority_granted"] is False
    assert not any(
        marker in json.dumps(report, sort_keys=True)
        for marker in FORBIDDEN_OUTPUT_SNIPPETS
    )


def test_runtime_gap_index_entry_verification_summary_bridge_event_template_cli_json_is_path_free(
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
            "wd-runtime-gap-summary-template",
            "--to",
            "operator,claude-rco-1",
            "--run-id",
            "codex-lead-1-20260607T150000Z",
            "--session-id",
            "codex-lead-1-20260607T150000Z",
            "--now",
            "2026-06-07T15:00:00Z",
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
    assert payload["bridge_event_written"] is False
    assert payload["scheduler_enqueue_allowed"] is False
    assert payload["scheduler_tick_allowed"] is False
    assert payload["fast_track_priority"] is False
    assert payload["gate_skip_allowed"] is False
    assert event["payload"][RUNTIME_GAP_VERIFICATION_KEY]["verification_ok"] is True
    assert str(tmp_path) not in result.stdout
    assert summary_path.name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_runtime_gap_index_entry_verification_summary_bridge_event_template_missing_input_is_path_free() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-json",
            "C:/private/index_entry_verification_summary.json",
            "--agent",
            "codex-lead-1",
            "--task-id",
            "wd-runtime-gap-summary-template",
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
        "runtime_gap_scheduler_candidate_bridge_event_template_index_entry_"
        "verification_summary_bridge_event_template_failed:"
        "index_entry_verification_summary_unreadable"
    ]
    assert payload["direct_bridge_write_performed"] is False
    assert payload["scheduler_enqueue_allowed"] is False
    assert payload["bridge_event_written"] is False
    combined = result.stdout + result.stderr
    assert "index_entry_verification_summary.json" not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_runtime_gap_index_entry_verification_summary_bridge_event_template_rejects_unsafe_bridge_fields(
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
            "wd-runtime-gap-summary-template",
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
        "runtime_gap_scheduler_candidate_bridge_event_template_index_entry_"
        "verification_summary_bridge_event_template_failed:agent_unsafe"
    ]
    assert str(tmp_path) not in result.stdout
    assert summary_path.name not in result.stdout


def test_runtime_gap_index_entry_verification_summary_bridge_event_template_blocks_unsafe_summary_contract(
    tmp_path: Path,
) -> None:
    cases = (
        (
            "summary_not_ok",
            lambda summary: summary.__setitem__("ok", False),
            "index_entry_verification_summary_not_ok",
        ),
        (
            "scheduler_enqueue_allowed",
            lambda summary: summary.__setitem__("scheduler_enqueue_allowed", True),
            "index_entry_verification_summary_scheduler_enqueue_allowed_not_false",
        ),
        (
            "source_contract_mismatch",
            lambda summary: summary[RUNTIME_GAP_VERIFICATION_KEY].__setitem__(
                "source_contract_check",
                "mismatch",
            ),
            "index_entry_verification_source_contract_not_match",
        ),
        (
            "verification_version_mismatch",
            lambda summary: summary[RUNTIME_GAP_VERIFICATION_KEY].__setitem__(
                "verification_version",
                "unknown",
            ),
            "index_entry_verification_version_mismatch",
        ),
        (
            "blocker_count_nonzero",
            lambda summary: summary[RUNTIME_GAP_VERIFICATION_KEY].__setitem__(
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
            "fast_track_priority",
            lambda summary: summary[RUNTIME_GAP_VERIFICATION_KEY].__setitem__(
                "fast_track_priority",
                True,
            ),
            "index_entry_verification_fast_track_priority_not_false",
        ),
    )

    for label, mutate, expected_reason in cases:
        summary = _index_entry_verification_summary()
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
                "wd-runtime-gap-summary-template",
                "--to",
                "operator,claude-rco-1",
                "--now",
                "2026-06-07T15:00:00Z",
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
            "runtime_gap_scheduler_candidate_bridge_event_template_index_entry_"
            f"verification_summary_bridge_event_template_failed:{expected_reason}"
        ], label
        assert report["direct_bridge_write_performed"] is False
        assert report["scheduler_enqueue_allowed"] is False
        assert report["bridge_event_written"] is False
        assert str(tmp_path) not in result.stdout
        assert summary_path.name not in result.stdout


def test_runtime_gap_index_entry_verification_summary_bridge_event_template_rejects_path_markers_without_leak(
    tmp_path: Path,
) -> None:
    summary = _index_entry_verification_summary()
    summary["warnings"] = ["C:/private/report.json"]
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
            "wd-runtime-gap-summary-template",
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
        "runtime_gap_scheduler_candidate_bridge_event_template_index_entry_"
        "verification_summary_bridge_event_template_failed:"
        "index_entry_verification_summary_forbidden_marker"
    ]
    combined = result.stdout + result.stderr
    assert str(tmp_path) not in combined
    assert summary_path.name not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_runtime_gap_index_entry_verification_summary_bridge_event_template_non_finite_json_is_path_free(
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
            "wd-runtime-gap-summary-template",
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
        "runtime_gap_scheduler_candidate_bridge_event_template_index_entry_"
        "verification_summary_bridge_event_template_failed:"
        "index_entry_verification_summary_json_error"
    ]
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined
    assert str(tmp_path) not in combined
    assert summary_path.name not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def _index_entry_verification_summary() -> dict:
    return build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary(
        verification_report=_verification_report(),
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:runtime-gap-template-index-verifier",
        now_utc=FIXED_NOW,
    )


def _verification_report() -> dict:
    report = {
        "ok": True,
        "verification_version": VERIFICATION_VERSION,
        "index_entry_version": INDEX_ENTRY_VERSION,
        "artifact_count_checked": 2,
        "digest_checks": _checks("match"),
        "size_checks": _checks("match"),
        "schema_version_checks": _checks("match"),
        "source_contract_check": "match",
        "rebuilt_index_entry_check": "match",
        "bridge_event_schema_check": "match",
        "template_only": True,
        "manual_review_required": True,
        "blockers": [],
        "warnings": [],
    }
    report.update(
        {field: False for field in runtime_summary._VERIFICATION_FALSE_FIELDS}
    )
    return report


def _checks(status: str) -> dict[str, str]:
    return {ARTIFACT_ID: status, TEMPLATE_ARTIFACT_ID: status}


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, allow_nan=False).encode("utf-8")
