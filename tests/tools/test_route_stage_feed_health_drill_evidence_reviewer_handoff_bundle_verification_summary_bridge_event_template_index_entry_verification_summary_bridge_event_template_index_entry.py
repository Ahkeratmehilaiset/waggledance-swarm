# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry import (
    EVENT_STATUS,
    INDEX_ENTRY_VERSION,
    SUMMARY_ARTIFACT_ID,
    SUMMARY_VERSION,
    TEMPLATE_ARTIFACT_ID,
    TEMPLATE_VERSION,
    VERIFICATION_KEY,
    TemplateIndexEntryError,
    build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry,
)
from tools.verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry import (
    INDEX_ENTRY_VERSION as SOURCE_INDEX_ENTRY_VERSION,
    SUMMARY_ARTIFACT_ID as SOURCE_SUMMARY_ARTIFACT_ID,
    TEMPLATE_ARTIFACT_ID as SOURCE_TEMPLATE_ARTIFACT_ID,
    VERIFICATION_VERSION as SOURCE_VERIFICATION_VERSION,
    verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry,
)
from waggledance.core.bridge_event_schema import validate_event


ROOT = Path(__file__).resolve().parents[2]
HELPER_DIR = ROOT / "tests" / "tools"
if str(HELPER_DIR) not in sys.path:
    sys.path.insert(0, str(HELPER_DIR))

from test_verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry import (  # noqa: E402
    _artifact_bytes as _source_artifact_bytes,
    _artifact_set as _source_artifact_set,
    _index_entry as _source_index_entry,
)


SCRIPT = (
    ROOT
    / "tools"
    / (
        "build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_"
        "verification_summary_bridge_event_template_index_entry_"
        "verification_summary_bridge_event_template_index_entry.py"
    )
)
FIXED_NOW = datetime(2026, 6, 20, 0, 5, tzinfo=timezone.utc)
PRIVATE_MARKERS = ("C:/private", "PRIVATE_", "http://", "https://")


def test_route_stage_reviewer_handoff_bundle_verifier_summary_bridge_template_index_entry_ties_digests_without_authority() -> None:
    artifacts = _artifact_set()
    raw = _artifact_bytes(artifacts)

    entry = build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        index_entry_verification_summary=artifacts["summary"],
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=raw["summary"],
        summary_bridge_event_template_bytes=raw["template"],
        now_utc=FIXED_NOW,
    )

    assert entry["ok"] is True
    assert entry["index_entry_version"] == INDEX_ENTRY_VERSION
    assert entry["created_at_utc"] == "2026-06-20T00:05:00Z"
    assert entry["summary_version"] == SUMMARY_VERSION
    assert entry["template_version"] == TEMPLATE_VERSION
    assert entry["artifact_count"] == 2
    by_id = {item["artifact_id"]: item for item in entry["artifacts"]}
    assert by_id[SUMMARY_ARTIFACT_ID]["sha256"] == _sha256_hex(raw["summary"])
    assert by_id[TEMPLATE_ARTIFACT_ID]["sha256"] == _sha256_hex(raw["template"])
    assert by_id[SUMMARY_ARTIFACT_ID]["json_schema_version"] == SUMMARY_VERSION
    assert by_id[TEMPLATE_ARTIFACT_ID]["json_schema_version"] == TEMPLATE_VERSION
    assert all(item["payload_included"] is False for item in entry["artifacts"])
    assert all(item["local_path_recorded"] is False for item in entry["artifacts"])
    template_entry = entry["template_index_entry"]
    assert template_entry["template_only"] is True
    assert template_entry["bridge_event_schema_validated"] is True
    assert template_entry["source_summary_sha256"] == _sha256_hex(raw["summary"])
    assert template_entry["template_sha256"] == _sha256_hex(raw["template"])
    assert template_entry["source_contract_check"] == "match"
    assert template_entry["rebuilt_template_check"] == "match"
    assert template_entry["event_status"] == EVENT_STATUS
    assert template_entry["approval_granted"] is False
    assert template_entry["release_decision_made"] is False
    assert template_entry["runtime_authority_granted"] is False
    assert entry["operator_boundary"]["approval_granted"] is False
    assert entry["operator_boundary"]["release_decision_made"] is False
    assert entry["operator_boundary"]["runtime_authority_granted"] is False
    assert entry["direct_bridge_write_performed"] is False
    assert entry["transport_added"] is False
    assert entry["external_fetch_performed"] is False
    assert entry["runtime_controls_added"] is False
    assert entry["controls_present"] is False
    assert entry["runtime_authority_granted"] is False
    assert entry["external_writes_applied"] is False
    assert entry["network_access_performed"] is False
    assert entry["artifact_payloads_included"] is False
    assert entry["local_paths_recorded"] is False
    assert not any(marker in json.dumps(entry, sort_keys=True) for marker in PRIVATE_MARKERS)


def test_route_stage_reviewer_handoff_bundle_verifier_summary_bridge_template_index_entry_blocks_filename_warning_tokens() -> None:
    artifacts = _artifact_set()
    artifacts["summary"] = copy.deepcopy(artifacts["summary"])
    artifacts["template"] = copy.deepcopy(artifacts["template"])
    artifacts["summary"]["warnings"] = ["evidence.json", "safe_summary_warning"]
    artifacts["template"]["warnings"] = ["route_stage.py", "safe_template_warning"]
    raw = _artifact_bytes(artifacts)

    entry = build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        index_entry_verification_summary=artifacts["summary"],
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=raw["summary"],
        summary_bridge_event_template_bytes=raw["template"],
        now_utc=FIXED_NOW,
    )

    serialized = json.dumps(entry, sort_keys=True)
    assert "evidence.json" not in serialized
    assert "route_stage.py" not in serialized
    assert entry["verification_summary"]["warning_count"] == 1
    assert entry["warnings"] == ["safe_summary_warning", "safe_template_warning"]


def test_route_stage_reviewer_handoff_bundle_verifier_summary_bridge_template_index_entry_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    paths = _write_artifacts(tmp_path, _artifact_set())

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-json",
            str(paths["summary"]),
            "--template-json",
            str(paths["template"]),
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
    assert payload["ok"] is True
    assert payload["template_index_entry"]["rebuilt_template_check"] == "match"
    assert payload["direct_bridge_write_performed"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert str(tmp_path) not in result.stdout
    for path in paths.values():
        assert path.name not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)


def test_route_stage_reviewer_handoff_bundle_verifier_summary_bridge_template_index_entry_rejects_template_drift() -> None:
    artifacts = _artifact_set()
    artifacts["template"] = copy.deepcopy(artifacts["template"])
    artifacts["template"]["bridge_event_template"]["payload"][VERIFICATION_KEY][
        "source_contract_check"
    ] = "mismatch"
    raw = _artifact_bytes(artifacts)

    with pytest.raises(TemplateIndexEntryError) as exc_info:
        build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
            index_entry_verification_summary=artifacts["summary"],
            summary_bridge_event_template_report=artifacts["template"],
            index_entry_verification_summary_bytes=raw["summary"],
            summary_bridge_event_template_bytes=raw["template"],
            now_utc=FIXED_NOW,
        )

    assert (
        exc_info.value.code
        == "summary_bridge_event_template_verification_source_contract_check_mismatch"
    )


def test_route_stage_reviewer_handoff_bundle_verifier_summary_bridge_template_index_entry_rejects_summary_contract_forgery() -> None:
    artifacts = _artifact_set()
    artifacts["summary"] = copy.deepcopy(artifacts["summary"])
    artifacts["summary"][VERIFICATION_KEY]["blocker_count"] = "1"
    raw = _artifact_bytes(artifacts)

    with pytest.raises(TemplateIndexEntryError) as exc_info:
        build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
            index_entry_verification_summary=artifacts["summary"],
            summary_bridge_event_template_report=artifacts["template"],
            index_entry_verification_summary_bytes=raw["summary"],
            summary_bridge_event_template_bytes=raw["template"],
            now_utc=FIXED_NOW,
        )

    assert (
        exc_info.value.code
        == "summary_bridge_event_template_source_contract_failed:"
        "index_entry_verification_blocker_count_nonzero"
    )


def test_route_stage_reviewer_handoff_bundle_verifier_summary_bridge_template_index_entry_rejects_raw_bytes_mismatch() -> None:
    artifacts = _artifact_set()
    raw = _artifact_bytes(artifacts)

    with pytest.raises(TemplateIndexEntryError) as exc_info:
        build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
            index_entry_verification_summary=artifacts["summary"],
            summary_bridge_event_template_report=artifacts["template"],
            index_entry_verification_summary_bytes=raw["summary"],
            summary_bridge_event_template_bytes=b'{"forged":true}',
            now_utc=FIXED_NOW,
        )

    assert exc_info.value.code == f"{TEMPLATE_ARTIFACT_ID}_bytes_mismatch"


def test_route_stage_reviewer_handoff_bundle_verifier_summary_bridge_template_index_entry_missing_input_is_path_free(
    tmp_path: Path,
) -> None:
    paths = _write_artifacts(tmp_path, _artifact_set())

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-json",
            str(paths["summary"]),
            "--template-json",
            "C:/private/verifier-summary-template.json",
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
        "verification_summary_bridge_event_template_index_entry_failed:"
        f"{TEMPLATE_ARTIFACT_ID}_unreadable"
    ]
    combined = result.stdout + result.stderr
    assert "verifier-summary-template.json" not in combined
    assert str(tmp_path) not in combined
    assert not any(marker in combined for marker in PRIVATE_MARKERS)


def test_route_stage_reviewer_handoff_bundle_verifier_summary_bridge_template_index_entry_non_finite_json_is_path_free(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    artifacts["template"]["warnings"] = [float("nan")]
    paths = {
        "summary": tmp_path / "index-entry-verification-summary.json",
        "template": tmp_path / "summary-bridge-event-template.json",
    }
    paths["summary"].write_bytes(_json_bytes(artifacts["summary"]))
    paths["template"].write_text(
        json.dumps(artifacts["template"], sort_keys=True),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-json",
            str(paths["summary"]),
            "--template-json",
            str(paths["template"]),
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
        "verification_summary_bridge_event_template_index_entry_failed:"
        f"{TEMPLATE_ARTIFACT_ID}_json_error"
    ]
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined
    assert str(tmp_path) not in combined
    for path in paths.values():
        assert path.name not in combined
    assert not any(marker in combined for marker in PRIVATE_MARKERS)


def _artifact_set() -> dict[str, dict]:
    summary = _summary()
    template = _template(summary)
    return {"summary": summary, "template": template}


def _summary() -> dict:
    report = _verification_report()
    return {
        "proof_id": (
            "route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_"
            "verification_summary_bridge_event_template_index_entry_"
            "verification_summary_v1"
        ),
        "ok": True,
        "summary_version": SUMMARY_VERSION,
        "created_at_utc": "2026-06-20T00:00:00Z",
        "reviewer_ownership": {
            "reviewer_agent_id": "claude-rco-1",
            "handoff_ref": (
                "bridge:handoff:route-stage-reviewer-handoff-bundle-"
                "verifier-summary"
            ),
            "manual_review_required": True,
            "approval_granted": False,
            "release_decision_made": False,
            "automatic_release_decision": False,
        },
        VERIFICATION_KEY: {
            "verification_ok": True,
            "verification_version": SOURCE_VERIFICATION_VERSION,
            "index_entry_version": SOURCE_INDEX_ENTRY_VERSION,
            "artifact_count_checked": report["artifact_count_checked"],
            "digest_checks": report["digest_checks"],
            "size_checks": report["size_checks"],
            "schema_version_checks": report["schema_version_checks"],
            "source_contract_check": "match",
            "rebuilt_index_entry_check": "match",
            "bridge_event_schema_check": "match",
            "template_only": True,
            "blocker_count": 0,
            "blockers": [],
            "warning_count": 0,
            "warnings": [],
        },
        "operator_boundary": _operator_boundary(),
        "reviewer_next_actions": [
            "review_route_stage_reviewer_handoff_bundle_verifier_summary_bridge_event_template",
            "append_bridge_event_separately_only_after_manual_review",
        ],
        "template_only": True,
        "manual_review_required": True,
        **_authority_false_fields(),
        "blockers": [],
        "warnings": [],
    }


def _template(summary: dict) -> dict:
    payload = {
        "schema_version": TEMPLATE_VERSION,
        "summary_version": SUMMARY_VERSION,
        "summary_proof_id": summary["proof_id"],
        "reviewer_ownership": summary["reviewer_ownership"],
        VERIFICATION_KEY: summary[VERIFICATION_KEY],
        "operator_boundary": summary["operator_boundary"],
        "template_only": True,
        "manual_review_required": True,
        **_authority_false_fields(),
    }
    event = {
        "ts_utc": "2026-06-20T00:02:00Z",
        "agent": "codex-lead-1",
        "type": "handoff",
        "task_id": "wd-image1-route-stage-reviewer-handoff-verifier-summary-template-index",
        "status": EVENT_STATUS,
        "severity": "medium",
        "to": "operator,claude-rco-1,codex-tools-1",
        "message": (
            "Route-stage reviewer handoff bundle verifier-summary "
            "bridge-event template ready; manual review required."
        ),
        "paths": [],
        "write_scope": [],
        "run_id": "codex-lead-1-20260620T000200Z",
        "role": "lead-impl",
        "session_id": "codex-lead-1-20260620T000200Z",
        "capabilities": ["wd_image1", "route_stage_feed", "bridge_event"],
        "pid": 0,
        "cwd": "template_not_emitted",
        "payload": payload,
    }
    validate_event(event)
    return {
        "proof_id": (
            "route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_"
            "verification_summary_bridge_event_template_index_entry_"
            "verification_summary_bridge_event_template_v1"
        ),
        "ok": True,
        "template_version": TEMPLATE_VERSION,
        "bridge_event_template": event,
        "template_only": True,
        "manual_review_required": True,
        **_authority_false_fields(),
        "blockers": [],
        "warnings": [],
    }


def _verification_report() -> dict:
    artifacts = _source_artifact_set()
    raw = _source_artifact_bytes(artifacts)
    index_entry = _source_index_entry(artifacts)
    return verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry(
        index_entry=index_entry,
        bundle_verification_summary=artifacts["summary"],
        bridge_event_template_report=artifacts["template"],
        bundle_verification_summary_bytes=raw["summary"],
        bridge_event_template_bytes=raw["template"],
    )


def _write_artifacts(tmp_path: Path, artifacts: dict[str, dict]) -> dict[str, Path]:
    paths = {
        "summary": tmp_path / "index-entry-verification-summary.json",
        "template": tmp_path / "summary-bridge-event-template.json",
    }
    for key, path in paths.items():
        path.write_bytes(_json_bytes(artifacts[key]))
    return paths


def _artifact_bytes(artifacts: dict[str, dict]) -> dict[str, bytes]:
    return {key: _json_bytes(value) for key, value in artifacts.items()}


def _json_bytes(value: dict) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _operator_boundary() -> dict[str, bool | list[str]]:
    return {
        "verification_report_boundary_ok": True,
        "boundary_blockers": [],
        "manual_review_required": True,
        **_authority_false_fields(),
    }


def _authority_false_fields() -> dict[str, bool]:
    return {
        "approval_granted": False,
        "release_decision_made": False,
        "automatic_release_decision": False,
        "direct_bridge_write_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "runtime_controls_added": False,
        "controls_present": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
        "network_access_performed": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
    }
