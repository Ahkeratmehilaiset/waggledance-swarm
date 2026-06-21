# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.build_magma_share_import_replay_sanitization_bridge_event_template import (
    TEMPLATE_VERSION,
    build_magma_share_import_replay_sanitization_bridge_event_template,
)
from waggledance.core.bridge_event_schema import validate_event
from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.magma.share_manifest import (
    IMPORT_REPLAY_SANITIZATION_SUMMARY_VERSION,
    build_magma_share_import_failed_replay_sanitization_summary,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "tools"
    / "build_magma_share_import_replay_sanitization_bridge_event_template.py"
)
PRIVATE_MARKERS = ("private runtime query", "context secret", "DO_NOT_LEAK")


def _digest(seed: str) -> str:
    return "sha256:" + seed * 64


def _ready_summary() -> dict[str, object]:
    return {
        "summary_version": IMPORT_REPLAY_SANITIZATION_SUMMARY_VERSION,
        "source": "magma_share_manifest_import_report",
        "status": "ready_for_replay_sanitization_review",
        "severity": "none",
        "ok": True,
        "blocker_class": "none",
        "blockers": [],
        "controls_present": False,
        "manifest_version": "magma.share_manifest.v0",
        "admission_contract_version": (
            "magma.share_manifest_replay_admission_contract.v0"
        ),
        "sanitization_contract": "sanitization_v0",
        "scope": "no_authority_metadata_replay",
        "share_id": "magma:share:import:001",
        "purpose": "cross_instance_replay",
        "report_digest": _digest("1"),
        "admission_contract_digest": _digest("2"),
        "replay_plan_digest": _digest("3"),
        "share_manifest_digest": _digest("4"),
        "source_manifest_digest": _digest("5"),
        "entry_count": 3,
        "required_check_count": 2,
        "required_check_names": ["context_match", "purpose_match"],
        "rejection_mode_count": 2,
        "redaction_inventory": ["raw_material", "replacement_map"],
        "report_invariants": {
            "transport_enabled": False,
            "runtime_authority_granted": False,
        },
        "context_verified": True,
        "context_drift_detected": False,
        "replay_metadata_only": True,
        "no_authority_import": True,
        "full_replay_plan_exported": False,
        "entry_ids_exported": False,
        "transport_enabled": False,
        "runtime_export_enabled": False,
        "runtime_authority_granted": False,
        "runtime_authority_changed": False,
        "payload_files_exported": 0,
        "payload_files_imported": 0,
        "payload_digest_imported": False,
        "raw_material_imported": False,
        "replacement_map_imported": False,
        "local_paths_recorded": False,
    }


def test_ready_replay_sanitization_renders_valid_bridge_event_template() -> None:
    summary = _ready_summary()

    report = build_magma_share_import_replay_sanitization_bridge_event_template(
        replay_sanitization_summary=summary,
        agent_id="codex-tools-1",
        task_id="magma-share-replay-sanitization",
        to="codex-lead-1,claude-rco-1,operator",
        now_utc=datetime(2026, 6, 21, 3, 0, tzinfo=timezone.utc),
    )

    assert report["ok"] is True
    assert report["template_version"] == TEMPLATE_VERSION
    assert report["replay_sanitization_summary_digest"] == sha256_digest(summary)
    assert report["direct_bridge_write_performed"] is False
    assert report["full_replay_plan_exported"] is False
    assert report["entry_ids_exported"] is False
    event = report["bridge_event_template"]
    validate_event(event)
    assert event["type"] == "handoff"
    assert event["status"] == "magma_share_import_replay_sanitization_ready"
    assert event["cwd"] == "template_not_emitted"
    assert event["payload"]["entry_count"] == 3
    assert event["payload"]["required_check_count"] == 2
    assert event["payload"]["redaction_inventory_count"] == 2
    assert event["payload"]["report_invariant_count"] == 2
    assert event["payload"]["sanitization_contract"] == "sanitization_v0"
    assert event["payload"]["transport_enabled"] is False
    assert event["payload"]["runtime_authority_granted"] is False
    assert event["payload"]["payload_files_imported"] == 0
    assert event["payload"]["full_replay_plan_exported"] is False
    assert event["payload"]["entry_ids_exported"] is False
    assert event["payload"]["local_paths_recorded"] is False
    serialized = json.dumps(report, sort_keys=True)
    # Only the entry COUNT and the entry_ids_exported=false guardrail appear;
    # no raw entry-id list and no raw replay plan (digest only).
    assert '"entry_ids":' not in serialized
    assert '"replay_plan":' not in serialized
    assert not any(marker in serialized for marker in PRIVATE_MARKERS)


def test_rejected_replay_sanitization_renders_finding_template() -> None:
    # Use the REAL fail-closed summary builder -> proves the template builder
    # accepts genuine summary output, not just a hand-built fixture.
    summary = build_magma_share_import_failed_replay_sanitization_summary(
        reason="expected_share_id mismatch",
        max_age_hours=24,
        expected_share_id="magma:share:import:001",
        expected_purpose="cross_instance_replay",
    )

    report = build_magma_share_import_replay_sanitization_bridge_event_template(
        replay_sanitization_summary=summary,
        agent_id="codex-tools-1",
        task_id="magma-share-replay-sanitization",
        to="codex-lead-1",
        now_utc=datetime(2026, 6, 21, 3, 0, tzinfo=timezone.utc),
    )

    assert report["ok"] is True
    event = report["bridge_event_template"]
    validate_event(event)
    assert event["type"] == "finding"
    assert event["status"] == "magma_share_import_replay_sanitization_rejected"
    assert event["payload"]["ok"] is False
    assert event["payload"]["transport_enabled"] is False
    assert event["payload"]["runtime_authority_granted"] is False
    assert event["payload"]["payload_files_imported"] == 0


def test_rejects_authority_or_path_markers_without_echo() -> None:
    summary = _ready_summary()
    summary["runtime_authority_granted"] = True
    summary["local_path"] = r"C:\private\DO_NOT_LEAK\replay_import.json"

    report = build_magma_share_import_replay_sanitization_bridge_event_template(
        replay_sanitization_summary=summary,
        agent_id="codex-tools-1",
        task_id="magma-share-replay-sanitization",
        to="codex-lead-1",
    )

    assert report["ok"] is False
    assert report["runtime_authority_granted"] is False
    serialized = json.dumps(report, sort_keys=True)
    assert r"C:\private" not in serialized
    assert "DO_NOT_LEAK" not in serialized


def test_summary_version_mismatch_is_rejected() -> None:
    summary = _ready_summary()
    summary["summary_version"] = "wrong.version.v9"

    report = build_magma_share_import_replay_sanitization_bridge_event_template(
        replay_sanitization_summary=summary,
        agent_id="codex-tools-1",
        task_id="magma-share-replay-sanitization",
        to="codex-lead-1",
    )

    assert report["ok"] is False
    assert any("summary_version_mismatch" in b for b in report["blockers"])


def test_cli_json_is_path_free(tmp_path: Path) -> None:
    summary = _ready_summary()
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--replay-sanitization-summary-json",
            str(summary_path),
            "--agent",
            "codex-tools-1",
            "--task-id",
            "magma-share-replay-sanitization",
            "--to",
            "codex-lead-1,operator",
            "--now",
            "2026-06-21T03:00:00Z",
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
