import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools.build_magma_alert_feed_reviewer_bridge_event_template import (
    build_magma_alert_feed_reviewer_bridge_event_template,
)
from tools.build_magma_alert_feed_reviewer_handoff_summary import (
    build_magma_alert_feed_reviewer_handoff_summary,
)
from tools.package_magma_alert_feed_release_evidence import (
    build_magma_alert_feed_release_evidence_package,
)
from tools.validate_magma_alert_feed_release_evidence import (
    validate_magma_alert_feed_release_evidence_package,
)
from waggledance.core.bridge_event_schema import validate_event


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "build_magma_alert_feed_reviewer_bridge_event_template.py"
COMMIT_SHA = "4" * 40
FIXED_NOW = datetime(2026, 5, 28, 19, 10, tzinfo=timezone.utc)
PRIVATE_MARKERS = ("C:/private", "PRIVATE_", "http://", "https://")


def _ops_payload() -> dict:
    return {
        "magma_share_import_handoff": {
            "provider_health": {
                "metrics_alert_state": {
                    "source": "prometheus_alertmanager_snapshot",
                    "status": "nominal",
                    "severity": "none",
                    "prometheus_alertmanager_feed": True,
                    "active_count": 0,
                    "active": [],
                    "feed_health": {
                        "configured": True,
                        "available": True,
                        "cache_enabled": True,
                        "cache_present": True,
                        "cache_stale": False,
                        "backoff_active": False,
                        "controls_present": False,
                        "runtime_authority_granted": False,
                        "external_writes_applied": False,
                        "status": "nominal",
                        "failure_reason": "none",
                    },
                    "slo_panels": [],
                    "drill_evidence": {
                        "required_artifacts": [
                            {"id": "metrics_scrape"},
                            {"id": "ops_snapshot"},
                            {"id": "runtime_window_logs"},
                        ],
                        "controls_present": False,
                    },
                }
            }
        }
    }


def _metrics_text() -> str:
    return "\n".join(
        [
            "waggledance_magma_handoff_alert_feed_available 1",
            "waggledance_magma_handoff_alert_feed_fetch_failures_total 0",
            "waggledance_magma_handoff_alert_feed_backoff_active 0",
            "waggledance_magma_handoff_alert_feed_cache_stale 0",
            "waggledance_magma_handoff_runtime_authority_granted 0",
            "waggledance_magma_handoff_payload_files_imported 0",
            "waggledance_magma_handoff_local_paths_recorded 0",
            "waggledance_magma_handoff_controls_present 0",
            "waggledance_magma_handoff_alert_feed_controls_present 0",
            "waggledance_magma_handoff_alert_feed_runtime_authority_granted 0",
            "waggledance_magma_handoff_alert_feed_external_writes_applied 0",
            "",
        ]
    )


def _summary() -> dict:
    ops_bytes = json.dumps(_ops_payload(), sort_keys=True).encode("utf-8")
    metrics_bytes = _metrics_text().encode("utf-8")
    package = build_magma_alert_feed_release_evidence_package(
        ops_payload=_ops_payload(),
        metrics_text=_metrics_text(),
        release_ref="pr:756",
        commit_sha=COMMIT_SHA,
        operator_agent_id="operator:wd-image1",
        bridge_event_ref="bridge:wd-image1-magma-alert-feed-release",
        ci_run_ref="gh:run:bridge-template",
        now_utc=FIXED_NOW,
        ops_sha256=_sha256_hex(ops_bytes),
        ops_size_bytes=len(ops_bytes),
        metrics_sha256=_sha256_hex(metrics_bytes),
        metrics_size_bytes=len(metrics_bytes),
    )
    validation = validate_magma_alert_feed_release_evidence_package(
        package,
        ops_bytes=ops_bytes,
        metrics_bytes=metrics_bytes,
    )
    return build_magma_alert_feed_reviewer_handoff_summary(
        package=package,
        validation_report=validation,
        reviewer_agent_id="reviewer:wd-image1",
        bridge_event_ref="bridge:wd-image1-reviewer-handoff",
        now_utc=FIXED_NOW,
    )


def test_reviewer_bridge_event_template_validates_bridge_schema() -> None:
    report = build_magma_alert_feed_reviewer_bridge_event_template(
        summary=_summary(),
        agent_id="codex-lead-1",
        task_id="wd-image1-reviewer-handoff-template",
        to="operator,claude-rco-1",
        run_id="codex-lead-1-20260528T191000Z",
        session_id="codex-lead-1-20260528T191000Z",
        operator_decision_ref="bridge:operator-decision:hold-20260528",
        now_utc=FIXED_NOW,
    )

    event = report["bridge_event_template"]
    validate_event(event)
    json.dumps(event, allow_nan=False)
    assert report["ok"] is True
    assert event["type"] == "handoff"
    assert event["status"] == "reviewer_handoff_summary_ready"
    assert event["paths"] == []
    assert event["write_scope"] == []
    assert event["cwd"] == "template_not_emitted"
    assert event["payload"]["template_only"] is True
    assert event["payload"]["direct_bridge_write_performed"] is False
    assert event["payload"]["operator_decision"]["approval_granted"] is False
    assert event["payload"]["operator_decision"][
        "decision_reference"
    ] == "bridge:operator-decision:hold-20260528"
    assert event["payload"]["operator_decision"][
        "decision_reference_present"
    ] is True
    assert event["payload"]["operator_decision"][
        "decision_reference_is_approval"
    ] is False
    assert event["payload"]["operator_decision"][
        "decision_reference_is_release_decision"
    ] is False
    assert event["payload"]["operator_decision"]["release_decision_made"] is False
    assert event["payload"]["operator_decision"][
        "automatic_release_decision"
    ] is False
    assert event["payload"]["transport_added"] is False
    assert event["payload"]["external_fetch_performed"] is False
    assert event["payload"]["runtime_controls_added"] is False


def test_reviewer_bridge_event_template_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    summary_path = tmp_path / "reviewer-summary.json"
    summary_path.write_text(json.dumps(_summary()), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-json",
            str(summary_path),
            "--agent",
            "codex-lead-1",
            "--task-id",
            "wd-image1-reviewer-handoff-template",
            "--to",
            "operator,claude-rco-1",
            "--run-id",
            "codex-lead-1-20260528T191000Z",
            "--session-id",
            "codex-lead-1-20260528T191000Z",
            "--operator-decision-ref",
            "bridge:operator-decision:hold-20260528",
            "--now",
            "2026-05-28T19:10:00Z",
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
    assert event["payload"]["operator_decision"][
        "decision_reference"
    ] == "bridge:operator-decision:hold-20260528"
    assert event["payload"]["operator_decision"][
        "decision_reference_is_approval"
    ] is False
    assert event["payload"]["operator_decision"][
        "decision_reference_is_release_decision"
    ] is False
    assert str(tmp_path) not in result.stdout
    assert "reviewer-summary.json" not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)


def test_reviewer_bridge_event_template_missing_summary_is_path_free() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-json",
            "C:/private/reviewer-summary.json",
            "--agent",
            "codex-lead-1",
            "--task-id",
            "wd-image1-reviewer-handoff-template",
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
        "bridge_event_template_failed:summary_json_unreadable"
    ]
    assert payload["direct_bridge_write_performed"] is False
    assert "reviewer-summary" not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)


def test_reviewer_bridge_event_template_rejects_unsafe_decision_reference(
    tmp_path: Path,
) -> None:
    summary_path = tmp_path / "reviewer-summary.json"
    summary_path.write_text(json.dumps(_summary()), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-json",
            str(summary_path),
            "--agent",
            "codex-lead-1",
            "--task-id",
            "wd-image1-reviewer-handoff-template",
            "--operator-decision-ref",
            "C:/private/operator-approval.json",
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
        "bridge_event_template_failed:operator_decision_ref_unsafe"
    ]
    assert payload["approval_granted"] is False
    assert payload["release_decision_made"] is False
    assert str(tmp_path) not in result.stdout
    assert "operator-approval" not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)


def test_reviewer_bridge_event_template_rejects_non_string_decision_reference() -> None:
    with pytest.raises(ValueError, match="operator_decision_ref_unsafe"):
        build_magma_alert_feed_reviewer_bridge_event_template(
            summary=_summary(),
            agent_id="codex-lead-1",
            task_id="wd-image1-reviewer-handoff-template",
            to="operator,claude-rco-1",
            operator_decision_ref=42,  # type: ignore[arg-type]
            now_utc=FIXED_NOW,
        )


def test_reviewer_bridge_event_template_rejects_unsafe_bridge_fields(
    tmp_path: Path,
) -> None:
    summary_path = tmp_path / "reviewer-summary.json"
    summary_path.write_text(json.dumps(_summary()), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-json",
            str(summary_path),
            "--agent",
            "Codex",
            "--task-id",
            "wd-image1-reviewer-handoff-template",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["blockers"] == ["bridge_event_template_failed:agent_unsafe"]
    assert str(tmp_path) not in result.stdout
    assert "reviewer-summary" not in result.stdout


def _sha256_hex(data: bytes) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(data).hexdigest()
