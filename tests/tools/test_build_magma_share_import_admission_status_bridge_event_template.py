# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.build_magma_share_import_admission_status_bridge_event_template import (
    TEMPLATE_VERSION,
    build_magma_share_import_admission_status_bridge_event_template,
)
from waggledance.core.bridge_event_schema import validate_event
from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.magma.share_manifest import (
    IMPORT_ADMISSION_STATUS_VERSION,
    build_magma_share_import_failed_admission_status_summary,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "build_magma_share_import_admission_status_bridge_event_template.py"
PRIVATE_MARKERS = ("private runtime query", "context secret", "DO_NOT_LEAK")


def _digest(seed: str) -> str:
    return "sha256:" + seed * 64


def _ready_status() -> dict[str, object]:
    return {
        "summary_version": IMPORT_ADMISSION_STATUS_VERSION,
        "source": "magma_share_manifest_import_report",
        "status": "ready_for_peer_review_handoff",
        "severity": "none",
        "ok": True,
        "blocker_class": "none",
        "blockers": [],
        "controls_present": False,
        "transport_enabled": False,
        "operator_handoff_required_for_peer_review": True,
        "share_id": "magma:share:import:001",
        "purpose": "cross_instance_replay",
        "report_digest": _digest("1"),
        "admission_contract_digest": _digest("2"),
        "share_manifest_digest": _digest("3"),
        "source_manifest_digest": _digest("4"),
        "entry_count": 3,
        "age_seconds": 3600,
        "max_age_hours": 24,
        "context_verified": True,
        "context_drift_detected": False,
        "replay_metadata_only": True,
        "no_authority_import": True,
        "runtime_export_enabled": False,
        "runtime_authority_granted": False,
        "runtime_authority_changed": False,
        "payload_files_imported": 0,
        "payload_digest_imported": False,
        "raw_material_imported": False,
        "replacement_map_imported": False,
        "local_paths_recorded": False,
    }


def test_ready_admission_status_renders_valid_bridge_event_template() -> None:
    status = _ready_status()

    report = build_magma_share_import_admission_status_bridge_event_template(
        admission_status=status,
        agent_id="codex-tools-1",
        task_id="magma-share-admission-status",
        to="codex-lead-1,claude-rco-1,operator",
        now_utc=datetime(2026, 6, 13, 3, 0, tzinfo=timezone.utc),
    )

    assert report["ok"] is True
    assert report["template_version"] == TEMPLATE_VERSION
    assert report["admission_status_digest"] == sha256_digest(status)
    assert report["direct_bridge_write_performed"] is False
    event = report["bridge_event_template"]
    validate_event(event)
    assert event["type"] == "handoff"
    assert event["status"] == "magma_share_import_admission_ready"
    assert event["cwd"] == "template_not_emitted"
    assert event["payload"]["entry_count"] == 3
    assert event["payload"]["transport_enabled"] is False
    assert event["payload"]["runtime_authority_granted"] is False
    assert event["payload"]["payload_files_imported"] == 0
    assert event["payload"]["local_paths_recorded"] is False
    serialized = json.dumps(report, sort_keys=True)
    assert "entry_id" not in serialized
    assert not any(marker in serialized for marker in PRIVATE_MARKERS)


def test_rejected_admission_status_renders_finding_template() -> None:
    status = build_magma_share_import_failed_admission_status_summary(
        reason="expected_share_id mismatch",
        max_age_hours=24,
        expected_share_id="magma:share:import:001",
        expected_purpose="cross_instance_replay",
    )

    report = build_magma_share_import_admission_status_bridge_event_template(
        admission_status=status,
        agent_id="codex-tools-1",
        task_id="magma-share-admission-status",
        to="codex-lead-1",
        now_utc=datetime(2026, 6, 13, 3, 0, tzinfo=timezone.utc),
    )

    assert report["ok"] is True
    event = report["bridge_event_template"]
    validate_event(event)
    assert event["type"] == "finding"
    assert event["status"] == "magma_share_import_admission_rejected"
    assert event["payload"]["ok"] is False
    assert event["payload"]["blocker_class"] == "expected_share_id_mismatch"
    assert event["payload"]["transport_enabled"] is False
    assert event["payload"]["runtime_authority_granted"] is False


def test_rejects_authority_or_path_markers_without_echo() -> None:
    status = _ready_status()
    status["runtime_authority_granted"] = True
    status["local_path"] = r"C:\private\DO_NOT_LEAK\share_import.json"

    report = build_magma_share_import_admission_status_bridge_event_template(
        admission_status=status,
        agent_id="codex-tools-1",
        task_id="magma-share-admission-status",
        to="codex-lead-1",
    )

    assert report["ok"] is False
    assert report["runtime_authority_granted"] is False
    serialized = json.dumps(report, sort_keys=True)
    assert r"C:\private" not in serialized
    assert "DO_NOT_LEAK" not in serialized


def test_cli_json_is_path_free(tmp_path: Path) -> None:
    status = _ready_status()
    status_path = tmp_path / "status.json"
    status_path.write_text(json.dumps(status), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--admission-status-json",
            str(status_path),
            "--agent",
            "codex-tools-1",
            "--task-id",
            "magma-share-admission-status",
            "--to",
            "codex-lead-1,operator",
            "--now",
            "2026-06-13T03:00:00Z",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    validate_event(payload["bridge_event_template"])
    assert str(tmp_path) not in completed.stdout
