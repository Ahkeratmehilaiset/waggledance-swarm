# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import subprocess
import sys
from pathlib import Path

from tools.build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index import (
    build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index,
)
from tools.build_route_stage_feed_health_drill_evidence_reviewer_handoff_summary import (
    build_route_stage_feed_health_drill_evidence_reviewer_handoff_summary,
)
from tools.verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index import (
    verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index,
)
from tools.verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry import (
    EVENT_STATUS,
    INDEX_ENTRY_VERSION,
    SUMMARY_ARTIFACT_ID,
    SUMMARY_VERSION,
    TEMPLATE_ARTIFACT_ID,
    TEMPLATE_VERSION,
    VERIFICATION_VERSION,
    build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry,
    verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry,
)


ROOT = Path(__file__).resolve().parents[2]
HELPER_DIR = ROOT / "tests" / "tools"
if str(HELPER_DIR) not in sys.path:
    sys.path.insert(0, str(HELPER_DIR))

from test_route_stage_feed_health_drill_evidence_reviewer_handoff_summary import (  # noqa: E402
    _final_verification_report,
)


SCRIPT = (
    ROOT
    / "tools"
    / (
        "verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_"
        "verification_summary_bridge_event_template_index_entry.py"
    )
)
FIXED_NOW = datetime(2026, 6, 19, 7, 0, tzinfo=timezone.utc)
PRIVATE_MARKERS = ("C:/private", "PRIVATE_", "http://", "https://")


def test_route_stage_reviewer_handoff_bundle_verification_summary_template_index_entry_verifier_recomputes_digests_without_authority() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    raw = _artifact_bytes(artifacts)

    report = verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry(
        index_entry=index_entry,
        bundle_verification_summary=artifacts["summary"],
        bridge_event_template_report=artifacts["template"],
        bundle_verification_summary_bytes=raw["summary"],
        bridge_event_template_bytes=raw["template"],
    )

    assert report["ok"] is True
    assert report["verification_version"] == VERIFICATION_VERSION
    assert report["index_entry_version"] == INDEX_ENTRY_VERSION
    assert report["artifact_count_checked"] == 2
    assert set(report["digest_checks"].values()) == {"match"}
    assert set(report["size_checks"].values()) == {"match"}
    assert set(report["schema_version_checks"].values()) == {"match"}
    assert report["source_contract_check"] == "match"
    assert report["rebuilt_index_entry_check"] == "match"
    assert report["bridge_event_schema_check"] == "match"
    assert report["template_only"] is True
    assert report["manual_review_required"] is True
    assert report["approval_granted"] is False
    assert report["release_decision_made"] is False
    assert report["direct_bridge_write_performed"] is False
    assert report["transport_added"] is False
    assert report["external_fetch_performed"] is False
    assert report["runtime_controls_added"] is False
    assert report["runtime_authority_granted"] is False
    assert report["artifact_payloads_included"] is False
    assert report["local_paths_recorded"] is False


def test_route_stage_reviewer_handoff_bundle_verification_summary_template_index_entry_verifier_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    paths = _write_bundle(tmp_path, index_entry, artifacts)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--index-entry-json",
            str(paths["index_entry"]),
            "--bundle-verification-summary-json",
            str(paths["summary"]),
            "--bridge-event-template-json",
            str(paths["template"]),
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
    assert payload["rebuilt_index_entry_check"] == "match"
    assert payload["bridge_event_schema_check"] == "match"
    assert payload["direct_bridge_write_performed"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert str(tmp_path) not in result.stdout
    for path in paths.values():
        assert path.name not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)


def test_route_stage_reviewer_handoff_bundle_verification_summary_template_index_entry_verifier_rejects_digest_mismatch_path_free(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    paths = _write_bundle(tmp_path, index_entry, artifacts)
    tampered_template = copy.deepcopy(artifacts["template"])
    tampered_template["bridge_event_template"]["message"] += " changed"
    paths["template"].write_bytes(_json_bytes(tampered_template))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--template-index-entry-json",
            str(paths["index_entry"]),
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
    assert f"digest_mismatch:{TEMPLATE_ARTIFACT_ID}" in payload["blockers"]
    assert payload["approval_granted"] is False
    assert payload["local_paths_recorded"] is False
    combined = result.stdout + result.stderr
    assert str(tmp_path) not in combined
    for path in paths.values():
        assert path.name not in combined
    assert not any(marker in combined for marker in PRIVATE_MARKERS)


def test_route_stage_reviewer_handoff_bundle_verification_summary_template_index_entry_verifier_rejects_missing_record() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    index_entry["artifacts"] = [
        item
        for item in index_entry["artifacts"]
        if item["artifact_id"] != SUMMARY_ARTIFACT_ID
    ]
    raw = _artifact_bytes(artifacts)

    report = verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry(
        index_entry=index_entry,
        bundle_verification_summary=artifacts["summary"],
        bridge_event_template_report=artifacts["template"],
        bundle_verification_summary_bytes=raw["summary"],
        bridge_event_template_bytes=raw["template"],
    )

    assert report["ok"] is False
    assert f"artifact_record_missing:{SUMMARY_ARTIFACT_ID}" in report["blockers"]
    assert report["digest_checks"][SUMMARY_ARTIFACT_ID] == "missing_index_record"
    assert report["approval_granted"] is False
    assert report["release_decision_made"] is False


def test_route_stage_reviewer_handoff_bundle_verification_summary_template_index_entry_verifier_rejects_nested_authority() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    index_entry["template_index_entry"]["runtime_authority_granted"] = True
    raw = _artifact_bytes(artifacts)

    report = verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry(
        index_entry=index_entry,
        bundle_verification_summary=artifacts["summary"],
        bridge_event_template_report=artifacts["template"],
        bundle_verification_summary_bytes=raw["summary"],
        bridge_event_template_bytes=raw["template"],
    )

    assert report["ok"] is False
    assert "template_index_entry_runtime_authority_granted_not_false" in report["blockers"]
    assert report["runtime_authority_granted"] is False
    assert report["release_decision_made"] is False


def test_route_stage_reviewer_handoff_bundle_verification_summary_template_index_entry_verifier_rejects_deterministic_entry_drift() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    index_entry["reviewer_next_actions"] = ["approve_release"]
    raw = _artifact_bytes(artifacts)

    report = verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry(
        index_entry=index_entry,
        bundle_verification_summary=artifacts["summary"],
        bridge_event_template_report=artifacts["template"],
        bundle_verification_summary_bytes=raw["summary"],
        bridge_event_template_bytes=raw["template"],
    )

    assert report["ok"] is False
    assert report["rebuilt_index_entry_check"] == "mismatch"
    assert "rebuilt_index_entry_mismatch" in report["blockers"]
    assert report["approval_granted"] is False


def test_route_stage_reviewer_handoff_bundle_verification_summary_template_index_entry_verifier_rejects_source_contract_forgery() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    tampered_summary = copy.deepcopy(artifacts["summary"])
    tampered_summary["runtime_authority_granted"] = True

    report = verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry(
        index_entry=index_entry,
        bundle_verification_summary=tampered_summary,
        bridge_event_template_report=artifacts["template"],
        bundle_verification_summary_bytes=_json_bytes(tampered_summary),
        bridge_event_template_bytes=_json_bytes(artifacts["template"]),
    )

    assert report["ok"] is False
    assert "source_contract_failed:summary_runtime_authority_granted_not_false" in report["blockers"]
    assert report["source_contract_check"] == "failed"
    assert report["runtime_authority_granted"] is False


def test_route_stage_reviewer_handoff_bundle_verification_summary_template_index_entry_verifier_missing_input_is_path_free(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    paths = _write_bundle(tmp_path, index_entry, artifacts)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--index-entry-json",
            "C:/private/template-index-entry.json",
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
        "verification_summary_bridge_event_template_index_entry_verification_"
        "failed:route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_"
        "verification_summary_bridge_event_template_index_entry_unreadable"
    ]
    combined = result.stdout + result.stderr
    assert "template-index-entry.json" not in combined
    assert not any(marker in combined for marker in PRIVATE_MARKERS)


def test_route_stage_reviewer_handoff_bundle_verification_summary_template_index_entry_verifier_non_finite_json_is_path_free(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    paths = _write_bundle(tmp_path, index_entry, artifacts)
    paths["index_entry"].write_text('{"ok": NaN}', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--index-entry-json",
            str(paths["index_entry"]),
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
        "verification_summary_bridge_event_template_index_entry_verification_"
        "failed:route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_"
        "verification_summary_bridge_event_template_index_entry_json_error"
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
    report = _bundle_verification_report()
    return {
        "proof_id": (
            "route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_"
            "verification_summary_v1"
        ),
        "ok": True,
        "summary_version": SUMMARY_VERSION,
        "created_at_utc": "2026-06-19T06:50:00Z",
        "route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification": {
            "verification_ok": True,
            "verification_version": report["verification_version"],
            "bundle_index_version": report["bundle_index_version"],
            "artifact_count_checked": report["artifact_count_checked"],
            "digest_checks": report["digest_checks"],
            "size_checks": report["size_checks"],
            "schema_version_checks": report["schema_version_checks"],
            "source_contract_check": report["source_contract_check"],
            "rebuilt_bundle_index_check": report["rebuilt_bundle_index_check"],
            "reviewer_handoff_summary_check": report["reviewer_handoff_summary_check"],
            "template_only": True,
            "blocker_count": 0,
            "blockers": [],
            "warning_count": 0,
            "warnings": [],
        },
        "operator_boundary": _authority_boundary(),
        "reviewer_next_actions": [
            "review_route_stage_reviewer_handoff_bundle_verification_summary_bridge_event_template",
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
        "route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification": summary[
            "route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification"
        ],
        "operator_boundary": _authority_boundary(),
        "template_only": True,
        "manual_review_required": True,
        **_authority_false_fields(),
    }
    event = {
        "ts_utc": "2026-06-19T06:55:00Z",
        "agent": "codex-lead-1",
        "type": "handoff",
        "task_id": "wd-image1-route-stage-reviewer-handoff-bundle-verification-summary-template-index",
        "status": EVENT_STATUS,
        "severity": "medium",
        "to": "operator,claude-rco-1,codex-tools-1",
        "message": (
            "Route-stage reviewer handoff bundle verification summary "
            "bridge-event template ready; manual review required."
        ),
        "paths": [],
        "write_scope": [],
        "run_id": "codex-lead-1-20260619T065500Z",
        "role": "lead-impl",
        "session_id": "codex-lead-1-20260619T065500Z",
        "capabilities": ["wd_image1", "route_stage_feed", "bridge_event"],
        "pid": 0,
        "cwd": "template_not_emitted",
        "payload": payload,
    }
    return {
        "proof_id": (
            "route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_"
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


def _bundle_verification_report() -> dict:
    final_verification = _final_verification_report()
    summary = build_route_stage_feed_health_drill_evidence_reviewer_handoff_summary(
        verification_report=final_verification,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:route-stage-feed-health-drill-reviewer-handoff",
        now_utc=datetime(2026, 6, 19, 6, 20, tzinfo=timezone.utc),
    )
    raw = {
        "verification": _json_bytes(final_verification),
        "summary": _json_bytes(summary),
    }
    bundle_index = build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index(
        verification_report=final_verification,
        reviewer_handoff_summary=summary,
        verification_report_bytes=raw["verification"],
        reviewer_handoff_summary_bytes=raw["summary"],
        now_utc=datetime(2026, 6, 19, 6, 30, tzinfo=timezone.utc),
    )
    return verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index(
        bundle_index=bundle_index,
        verification_report=final_verification,
        reviewer_handoff_summary=summary,
        verification_report_bytes=raw["verification"],
        reviewer_handoff_summary_bytes=raw["summary"],
    )


def _index_entry(artifacts: dict[str, dict]) -> dict:
    raw = _artifact_bytes(artifacts)
    return build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry(
        bundle_verification_summary=artifacts["summary"],
        bridge_event_template_report=artifacts["template"],
        bundle_verification_summary_bytes=raw["summary"],
        bridge_event_template_bytes=raw["template"],
        now_utc=FIXED_NOW,
    )


def _write_bundle(
    tmp_path: Path,
    index_entry: dict,
    artifacts: dict[str, dict],
) -> dict[str, Path]:
    paths = {
        "index_entry": tmp_path / "template-index-entry.json",
        "summary": tmp_path / "verification-summary.json",
        "template": tmp_path / "bridge-event-template.json",
    }
    paths["index_entry"].write_bytes(_json_bytes(index_entry))
    paths["summary"].write_bytes(_json_bytes(artifacts["summary"]))
    paths["template"].write_bytes(_json_bytes(artifacts["template"]))
    return paths


def _artifact_bytes(artifacts: dict[str, dict]) -> dict[str, bytes]:
    return {artifact_id: _json_bytes(artifact) for artifact_id, artifact in artifacts.items()}


def _json_bytes(value: dict) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def _authority_boundary() -> dict[str, bool]:
    return {"manual_review_required": True, **_authority_false_fields()}


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
