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
    IMPORT_ADMISSION_CONTRACT_VERSION,
    IMPORT_ADMISSION_STATUS_VERSION,
    build_magma_share_import_admission_status_summary,
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
        "admission_contract_version": IMPORT_ADMISSION_CONTRACT_VERSION,
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
    assert event["payload"]["admission_contract_version"] == (
        IMPORT_ADMISSION_CONTRACT_VERSION
    )
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


def test_empty_and_blocked_production_statuses_render_finding_templates() -> None:
    statuses = (
        build_magma_share_import_admission_status_summary(None),
        build_magma_share_import_admission_status_summary({"not": "a report"}),
    )
    for status in statuses:
        report = build_magma_share_import_admission_status_bridge_event_template(
            admission_status=status,
            agent_id="codex-tools-1",
            task_id="magma-share-admission-status",
            to="codex-lead-1",
        )
        assert report["ok"] is True
        event = report["bridge_event_template"]
        validate_event(event)
        assert event["type"] == "finding"
        assert event["payload"]["ok"] is False
        assert event["payload"]["replay_metadata_only"] is True
        assert event["payload"]["no_authority_import"] is True


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


def test_rejects_wrong_admission_contract_version() -> None:
    status = _ready_status()
    status["admission_contract_version"] = (
        "magma.share_manifest_replay_admission_contract.v0"
    )

    report = build_magma_share_import_admission_status_bridge_event_template(
        admission_status=status,
        agent_id="codex-tools-1",
        task_id="magma-share-admission-status",
        to="codex-lead-1",
    )

    assert report["ok"] is False
    assert report["blockers"] == [
        "admission_status_bridge_event_template_failed:"
        "admission_contract_version_mismatch"
    ]


def _assert_ready_status_rejected(
    status: dict[str, object],
    blocker_substr: str,
) -> None:
    report = build_magma_share_import_admission_status_bridge_event_template(
        admission_status=status,
        agent_id="codex-tools-1",
        task_id="magma-share-admission-status",
        to="codex-lead-1",
    )
    assert report["ok"] is False
    assert any(blocker_substr in blocker for blocker in report["blockers"])
    assert "bridge_event_template" not in report


def test_ready_evidence_digests_are_required() -> None:
    for key in (
        "report_digest",
        "admission_contract_digest",
        "share_manifest_digest",
        "source_manifest_digest",
    ):
        status = _ready_status()
        status[key] = ""
        _assert_ready_status_rejected(status, f"{key}_unsafe")


def test_ready_counts_are_strict_non_bool_unsigned_integers() -> None:
    for key, value in (
        ("entry_count", "3"),
        ("age_seconds", 1.5),
        ("max_age_hours", True),
    ):
        status = _ready_status()
        status[key] = value
        _assert_ready_status_rejected(status, f"{key}_unsafe")


def test_ready_evidence_booleans_are_strict() -> None:
    cases = (
        ("context_verified", False, "context_verified_not_true"),
        (
            "context_drift_detected",
            True,
            "context_drift_detected_not_false",
        ),
        ("controls_present", True, "controls_present_not_false"),
        (
            "operator_handoff_required_for_peer_review",
            False,
            "operator_handoff_required_for_peer_review_not_true",
        ),
    )
    for key, value, blocker in cases:
        status = _ready_status()
        status[key] = value
        _assert_ready_status_rejected(status, blocker)


def test_ready_categorical_evidence_must_be_canonical() -> None:
    for key, value in (
        ("source", "magma_share_manifest_import_failure"),
        ("status", "rejected"),
        ("severity", "warning"),
        ("blocker_class", "expected_share_id_mismatch"),
    ):
        status = _ready_status()
        status[key] = value
        _assert_ready_status_rejected(status, f"{key}_ready_mismatch")

    status = _ready_status()
    status["blockers"] = ["expected_share_id_mismatch"]
    _assert_ready_status_rejected(status, "ready_blockers_present")


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
