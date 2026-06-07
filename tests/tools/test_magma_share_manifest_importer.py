# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.run_runtime_receipt_emission_proof import (
    build_runtime_receipt_emission_proof,
)
from tools.verify_magma_receipt import verify_manifest
from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.magma.share_manifest import (
    IMPORT_ADMISSION_STATUS_VERSION,
    IMPORT_ADMISSION_CONTRACT_VERSION,
    IMPORT_HANDOFF_HISTORY_LIMIT,
    IMPORT_HANDOFF_STATUS_VERSION,
    IMPORT_HANDOFF_VERSION,
    IMPORT_REPORT_VERSION,
    build_magma_share_import_admission_status_summary,
    build_magma_share_import_handoff_status_summary,
    build_magma_share_import_peer_review_handoff,
    build_magma_share_manifest_import_report,
    write_magma_share_import_peer_review_handoff,
    write_magma_share_manifest_export,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "import_magma_share_manifest.py"
FIXED_NOW = datetime(2026, 5, 28, 8, 0, tzinfo=timezone.utc)
PRIVATE_MARKERS = ("private runtime query", "context secret", "DO_NOT_LEAK")


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _source_manifest(tmp_path: Path) -> Path:
    report = build_runtime_receipt_emission_proof(
        out_dir=tmp_path / "source-proof",
        now_utc=FIXED_NOW,
    )
    return Path(report["receipt_manifest"])


def _share_export(tmp_path: Path) -> tuple[Path, Path]:
    source_manifest = _source_manifest(tmp_path)
    report = write_magma_share_manifest_export(
        source_manifest_path=source_manifest,
        out_dir=tmp_path / "share-export",
        operator_approval_id="operator:approval:magma-share-import-fixture",
        verify_source_manifest=verify_manifest,
        share_id="magma:share:import:001",
        producer_agent_id="codex-lead-1",
        producer_role="lead",
        bridge_event_ref="bridge:wd-image1-magma-share-import",
        purpose="cross_instance_replay",
        now_utc=FIXED_NOW,
    )
    return Path(report["share_manifest"]), source_manifest


def _run_importer_json(
    share_manifest: Path,
    source_manifest: Path,
    *,
    now: str = "2026-05-28T09:00:00Z",
    max_age_hours: int = 24,
    expected_share_id: str | None = "magma:share:import:001",
    expected_purpose: str | None = "cross_instance_replay",
    admission_status_json: bool = False,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(SCRIPT),
        "--admission-status-json" if admission_status_json else "--json",
        "--share-manifest",
        str(share_manifest),
        "--source-manifest",
        str(source_manifest),
        "--max-age-hours",
        str(max_age_hours),
        "--now",
        now,
    ]
    if expected_share_id is not None:
        command.extend(["--expected-share-id", expected_share_id])
    if expected_purpose is not None:
        command.extend(["--expected-purpose", expected_purpose])
    return subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _assert_failed_admission_status(
    result: subprocess.CompletedProcess[str],
    *,
    blocker_class: str,
    tmp_path: Path,
) -> dict[str, object]:
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["summary_version"] == IMPORT_ADMISSION_STATUS_VERSION
    assert payload["source"] == "magma_share_manifest_import_failure"
    assert payload["status"] == "rejected"
    assert payload["severity"] == "warning"
    assert payload["ok"] is False
    assert payload["blocker_class"] == blocker_class
    assert payload["blockers"] == [blocker_class]
    admission_contract = payload["admission_contract"]
    assert payload["admission_contract_digest"] == sha256_digest(
        admission_contract
    )
    rejection_codes = {
        item["reason_code"] for item in admission_contract["rejection_modes"]
    }
    assert blocker_class in rejection_codes
    assert payload["replay_metadata_only"] is True
    assert payload["no_authority_import"] is True
    assert payload["transport_enabled"] is False
    assert payload["runtime_export_enabled"] is False
    assert payload["runtime_authority_granted"] is False
    assert payload["runtime_authority_changed"] is False
    assert payload["payload_files_imported"] == 0
    assert payload["payload_digest_imported"] is False
    assert payload["raw_material_imported"] is False
    assert payload["replacement_map_imported"] is False
    assert payload["local_paths_recorded"] is False
    serialized = json.dumps(payload, sort_keys=True)
    assert str(tmp_path) not in serialized
    assert "private runtime query" not in serialized
    assert "context secret" not in serialized
    assert ("DO" + "_NOT" + "_LEAK") not in serialized
    return payload


def _all_json_text(root: Path) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(root.rglob("*.json"))
    )


def test_importer_builds_no_authority_replay_plan_from_fresh_share_manifest(
    tmp_path: Path,
) -> None:
    share_manifest, source_manifest = _share_export(tmp_path)

    report = build_magma_share_manifest_import_report(
        share_manifest_path=share_manifest,
        source_manifest_path=source_manifest,
        verify_source_manifest=verify_manifest,
        now_utc=FIXED_NOW + timedelta(hours=1),
        max_age_hours=24,
        expected_share_id="magma:share:import:001",
        expected_purpose="cross_instance_replay",
    )

    assert report["report_version"] == IMPORT_REPORT_VERSION
    assert report["ok"] is True
    assert report["blockers"] == []
    admission_contract = report["admission_contract"]
    assert admission_contract["contract_version"] == (IMPORT_ADMISSION_CONTRACT_VERSION)
    assert report["admission_contract_digest"] == sha256_digest(admission_contract)
    assert admission_contract["scope"] == "no_authority_metadata_replay"
    assert admission_contract["max_age_hours"] == 24
    assert admission_contract["expected_share_id"] == "magma:share:import:001"
    assert admission_contract["expected_purpose"] == "cross_instance_replay"
    assert admission_contract["transport_enabled"] is False
    assert admission_contract["runtime_authority_granted"] is False
    assert admission_contract["payload_files_imported"] == 0
    assert admission_contract["operator_handoff_required_for_peer_review"] is True
    check_names = {item["name"] for item in admission_contract["required_checks"]}
    assert {
        "share_manifest_schema_valid",
        "freshness_window_satisfied",
        "source_receipt_manifest_verified",
        "sanitized_source_manifest_digest_matches",
        "entry_receipt_digests_match",
        "forbidden_material_absence_preserved",
    }.issubset(check_names)
    rejection_codes = {
        item["reason_code"] for item in admission_contract["rejection_modes"]
    }
    assert {
        "schema_error",
        "stale_or_future_manifest",
        "source_receipt_manifest_verification_failed",
        "receipt_digest_context_drift",
        "raw_material_export_or_policy_relaxation",
    }.issubset(rejection_codes)
    assert admission_contract["report_invariants"] == {
        "ok_requires_blockers_empty": True,
        "ok_requires_context_verified": True,
        "ok_requires_context_drift_detected_false": True,
        "ok_requires_replay_metadata_only": True,
        "ok_requires_no_authority_import": True,
        "ok_requires_runtime_authority_granted_false": True,
        "ok_requires_payload_files_imported_zero": True,
        "ok_requires_raw_material_imported_false": True,
    }
    assert report["context_verified"] is True
    assert report["context_drift_detected"] is False
    assert report["replay_metadata_only"] is True
    assert report["no_authority_import"] is True
    assert report["runtime_export_enabled"] is False
    assert report["runtime_authority_granted"] is False
    assert report["runtime_authority_changed"] is False
    assert report["payload_files_imported"] == 0
    assert report["payload_digest_imported"] is False
    assert report["raw_material_imported"] is False
    assert report["replacement_map_imported"] is False
    assert report["replay_plan"]["mode"] == "no_authority_metadata_replay"
    assert report["replay_plan"]["entry_count"] == 1
    assert set(report["replay_plan"]["entries"][0]) == {
        "entry_id",
        "receipt_digest",
        "evaluation_result_digest",
        "subject_type",
        "risk_class",
        "expected_gate",
        "actual_gate",
        "verdict",
    }
    assert not any(marker in json.dumps(report) for marker in PRIVATE_MARKERS)


def test_import_admission_status_summary_is_path_free_and_no_authority(
    tmp_path: Path,
) -> None:
    share_manifest, source_manifest = _share_export(tmp_path)
    report = build_magma_share_manifest_import_report(
        share_manifest_path=share_manifest,
        source_manifest_path=source_manifest,
        verify_source_manifest=verify_manifest,
        now_utc=FIXED_NOW + timedelta(hours=1),
        max_age_hours=24,
        expected_share_id="magma:share:import:001",
        expected_purpose="cross_instance_replay",
    )

    summary = build_magma_share_import_admission_status_summary(report)

    assert summary["summary_version"] == IMPORT_ADMISSION_STATUS_VERSION
    assert summary["source"] == "magma_share_manifest_import_report"
    assert summary["status"] == "ready_for_peer_review_handoff"
    assert summary["severity"] == "none"
    assert summary["ok"] is True
    assert summary["blocker_class"] == "none"
    assert summary["blockers"] == []
    assert summary["admission_contract_digest"] == report["admission_contract_digest"]
    assert summary["entry_count"] == 1
    assert summary["context_verified"] is True
    assert summary["context_drift_detected"] is False
    assert summary["replay_metadata_only"] is True
    assert summary["no_authority_import"] is True
    assert summary["controls_present"] is False
    assert summary["transport_enabled"] is False
    assert summary["runtime_export_enabled"] is False
    assert summary["runtime_authority_granted"] is False
    assert summary["runtime_authority_changed"] is False
    assert summary["payload_files_imported"] == 0
    assert summary["payload_digest_imported"] is False
    assert summary["raw_material_imported"] is False
    assert summary["replacement_map_imported"] is False
    assert summary["local_paths_recorded"] is False
    serialized = json.dumps(summary)
    assert str(tmp_path) not in serialized
    assert "operator:decision" not in serialized
    assert "entry_id" not in serialized
    assert "receipt_digest" not in serialized
    assert not any(marker in serialized for marker in PRIVATE_MARKERS)

    empty = build_magma_share_import_admission_status_summary(None)
    assert empty["source"] == "not_configured"
    assert empty["status"] == "not_configured"
    assert empty["runtime_authority_granted"] is False
    assert empty["payload_files_imported"] == 0


def test_import_admission_status_summary_blocks_malformed_report_without_leak(
    tmp_path: Path,
) -> None:
    share_manifest, source_manifest = _share_export(tmp_path)
    report = build_magma_share_manifest_import_report(
        share_manifest_path=share_manifest,
        source_manifest_path=source_manifest,
        verify_source_manifest=verify_manifest,
        now_utc=FIXED_NOW + timedelta(hours=1),
        max_age_hours=24,
        expected_share_id="magma:share:import:001",
        expected_purpose="cross_instance_replay",
    )
    tampered = dict(report)
    tampered["runtime_authority_granted"] = True
    tampered["local_path"] = str(tmp_path / "DO_NOT_LEAK-admission.json")

    summary = build_magma_share_import_admission_status_summary(tampered)

    assert summary["summary_version"] == IMPORT_ADMISSION_STATUS_VERSION
    assert summary["source"] == "magma_share_manifest_import_report"
    assert summary["status"] == "blocked"
    assert summary["severity"] == "warning"
    assert summary["ok"] is False
    assert summary["blocker_class"] == "authority_or_privacy_boundary"
    assert summary["blockers"] == ["authority_or_privacy_boundary"]
    assert summary["controls_present"] is False
    assert summary["transport_enabled"] is False
    assert summary["runtime_authority_granted"] is False
    assert summary["payload_files_imported"] == 0
    assert summary["local_paths_recorded"] is False
    serialized = json.dumps(summary)
    assert str(tmp_path) not in serialized
    assert "DO_NOT_LEAK-admission" not in serialized
    assert not any(marker in serialized for marker in PRIVATE_MARKERS)


def test_importer_builds_operator_owned_peer_review_handoff_without_authority(
    tmp_path: Path,
) -> None:
    share_manifest, source_manifest = _share_export(tmp_path)
    report = build_magma_share_manifest_import_report(
        share_manifest_path=share_manifest,
        source_manifest_path=source_manifest,
        verify_source_manifest=verify_manifest,
        now_utc=FIXED_NOW + timedelta(hours=1),
        max_age_hours=24,
    )

    handoff = build_magma_share_import_peer_review_handoff(
        import_report=report,
        operator_decision_id="operator:decision:magma-share-import:001",
        operator_agent_id="operator:wd-image1",
        bridge_event_ref="bridge:wd-image1-magma-share-peer-review",
        import_decision="accepted_for_peer_review",
        decision_reason_ref="reason:cross_instance_replay_review",
        now_utc=FIXED_NOW + timedelta(hours=1),
    )

    assert handoff["handoff_version"] == IMPORT_HANDOFF_VERSION
    assert handoff["ok"] is True
    assert handoff["share_id"] == report["share_id"]
    assert handoff["purpose"] == report["purpose"]
    assert handoff["import_report_digest"].startswith("sha256:")
    assert handoff["share_manifest_digest"] == report["share_manifest_digest"]
    assert handoff["source_manifest_digest"] == report["source_manifest_digest"]
    assert handoff["operator_ownership"] == {
        "operator_owned": True,
        "operator_agent_id": "operator:wd-image1",
        "operator_decision_ref": "<redacted>",
        "operator_decision_id_recorded": False,
        "bridge_event_ref": "bridge:wd-image1-magma-share-peer-review",
        "import_decision": "accepted_for_peer_review",
        "import_decision_recorded": True,
        "decision_reason_ref": "reason:cross_instance_replay_review",
    }
    assert handoff["authority"] == {
        "operator_gate_required": True,
        "operator_gate_satisfied": True,
        "handoff_scope": "peer_review_only",
        "runtime_export_enabled": False,
        "runtime_authority_granted": False,
        "runtime_authority_changed": False,
        "runtime_traffic_mutation_applied": False,
        "runtime_receipt_emission_changed": False,
    }
    assert handoff["privacy"] == {
        "replay_metadata_only": True,
        "no_authority_import": True,
        "local_paths_recorded": False,
        "payload_files_imported": 0,
        "payload_digest_imported": False,
        "raw_material_imported": False,
        "replacement_map_imported": False,
    }
    assert handoff["replay_plan"]["entry_count"] == 1
    assert set(handoff["replay_plan"]["entries"][0]) == {
        "entry_id",
        "receipt_digest",
        "evaluation_result_digest",
        "subject_type",
        "risk_class",
        "expected_gate",
        "actual_gate",
        "verdict",
    }
    serialized = json.dumps(handoff)
    assert "operator:decision:magma-share-import:001" not in serialized
    assert str(tmp_path) not in serialized
    assert not any(marker in serialized for marker in PRIVATE_MARKERS)


def test_import_handoff_status_summary_is_read_only_and_sanitized(
    tmp_path: Path,
) -> None:
    share_manifest, source_manifest = _share_export(tmp_path)
    report = build_magma_share_manifest_import_report(
        share_manifest_path=share_manifest,
        source_manifest_path=source_manifest,
        verify_source_manifest=verify_manifest,
        now_utc=FIXED_NOW + timedelta(hours=1),
        max_age_hours=24,
    )
    handoff = build_magma_share_import_peer_review_handoff(
        import_report=report,
        operator_decision_id="operator:decision:magma-share-import:summary",
        operator_agent_id="operator:wd-image1",
        bridge_event_ref="bridge:wd-image1-magma-share-peer-review",
        import_decision="accepted_for_peer_review",
        decision_reason_ref="reason:cross_instance_replay_review",
        now_utc=FIXED_NOW + timedelta(hours=1),
    )

    summary = build_magma_share_import_handoff_status_summary(handoff)

    assert summary["summary_version"] == IMPORT_HANDOFF_STATUS_VERSION
    assert summary["source"] == "magma_share_import_peer_review_handoff"
    assert summary["status"] == "ready_for_peer_review"
    assert summary["severity"] == "none"
    assert summary["controls_present"] is False
    assert summary["operator_owned"] is True
    assert summary["handoff_count"] == 1
    assert summary["active_count"] == 1
    assert summary["runtime_export_enabled"] is False
    assert summary["runtime_authority_granted"] is False
    assert summary["runtime_authority_changed"] is False
    assert summary["payload_files_imported"] == 0
    assert summary["local_paths_recorded"] is False
    latest = summary["latest"]
    assert latest["handoff_id"] == handoff["handoff_id"]
    assert latest["handoff_digest"] == handoff["handoff_digest"]
    assert latest["share_id"] == report["share_id"]
    assert latest["handoff_scope"] == "peer_review_only"
    assert latest["import_decision"] == "accepted_for_peer_review"
    assert latest["entry_count"] == 1
    assert summary["active"] == [latest]
    serialized = json.dumps(summary)
    assert "operator:decision:magma-share-import:summary" not in serialized
    assert str(tmp_path) not in serialized
    assert not any(marker in serialized for marker in PRIVATE_MARKERS)

    empty = build_magma_share_import_handoff_status_summary(None)
    assert empty["source"] == "not_configured"
    assert empty["status"] == "not_configured"
    assert empty["controls_present"] is False
    assert empty["runtime_authority_granted"] is False
    assert empty["payload_files_imported"] == 0
    assert empty["active"] == []


def test_import_handoff_status_summary_retains_bounded_history(
    tmp_path: Path,
) -> None:
    share_manifest, source_manifest = _share_export(tmp_path)
    report = build_magma_share_manifest_import_report(
        share_manifest_path=share_manifest,
        source_manifest_path=source_manifest,
        verify_source_manifest=verify_manifest,
        now_utc=FIXED_NOW + timedelta(hours=1),
        max_age_hours=24,
    )
    decisions = (
        "accepted_for_peer_review",
        "deferred_for_operator_review",
        "accepted_for_peer_review",
        "rejected_for_peer_review",
        "accepted_for_peer_review",
        "deferred_for_operator_review",
        "accepted_for_peer_review",
    )
    handoffs = [
        build_magma_share_import_peer_review_handoff(
            import_report=report,
            operator_decision_id=(
                f"operator:decision:magma-share-import:history:{index:03d}"
            ),
            operator_agent_id=f"operator:wd-image1:{index:03d}",
            bridge_event_ref=f"bridge:wd-image1-history:{index:03d}",
            import_decision=decision,
            decision_reason_ref=f"reason:cross_instance_replay:{index:03d}",
            now_utc=FIXED_NOW + timedelta(hours=index + 1),
        )
        for index, decision in enumerate(decisions)
    ]

    summary = build_magma_share_import_handoff_status_summary(
        handoffs,
        history_limit=IMPORT_HANDOFF_HISTORY_LIMIT,
    )

    expected = list(reversed(handoffs))[:IMPORT_HANDOFF_HISTORY_LIMIT]
    assert summary["source"] == "magma_share_import_peer_review_handoff"
    assert summary["handoff_count"] == len(handoffs)
    assert summary["history_retained_count"] == IMPORT_HANDOFF_HISTORY_LIMIT
    assert summary["history_dropped_count"] == (
        len(handoffs) - IMPORT_HANDOFF_HISTORY_LIMIT
    )
    assert summary["history_truncated"] is True
    assert summary["latest"]["handoff_id"] == handoffs[-1]["handoff_id"]
    assert [item["handoff_id"] for item in summary["history"]] == [
        item["handoff_id"] for item in expected
    ]
    assert summary["active_count"] == sum(
        1
        for item in expected
        if item["operator_ownership"]["import_decision"] == "accepted_for_peer_review"
    )
    assert summary["controls_present"] is False
    assert summary["runtime_authority_granted"] is False
    assert summary["payload_files_imported"] == 0
    assert summary["local_paths_recorded"] is False
    serialized = json.dumps(summary)
    assert "operator:decision:magma-share-import:history" not in serialized
    assert str(tmp_path) not in serialized
    assert not any(marker in serialized for marker in PRIVATE_MARKERS)


def test_import_handoff_status_summary_validates_history_before_truncating(
    tmp_path: Path,
) -> None:
    share_manifest, source_manifest = _share_export(tmp_path)
    report = build_magma_share_manifest_import_report(
        share_manifest_path=share_manifest,
        source_manifest_path=source_manifest,
        verify_source_manifest=verify_manifest,
        now_utc=FIXED_NOW + timedelta(hours=1),
        max_age_hours=24,
    )
    accepted = build_magma_share_import_peer_review_handoff(
        import_report=report,
        operator_decision_id="operator:decision:magma-share-import:accepted",
        operator_agent_id="operator:wd-image1:accepted",
        bridge_event_ref="bridge:wd-image1-history:accepted",
        import_decision="accepted_for_peer_review",
        decision_reason_ref="reason:cross_instance_replay:accepted",
        now_utc=FIXED_NOW + timedelta(hours=3),
    )
    tampered = build_magma_share_import_peer_review_handoff(
        import_report=report,
        operator_decision_id="operator:decision:magma-share-import:tampered",
        operator_agent_id="operator:wd-image1:tampered",
        bridge_event_ref="bridge:wd-image1-history:tampered",
        import_decision="deferred_for_operator_review",
        decision_reason_ref="reason:cross_instance_replay:tampered",
        now_utc=FIXED_NOW + timedelta(hours=2),
    )
    tampered = json.loads(json.dumps(tampered))
    tampered["share_id"] = "C:/private/share"

    with pytest.raises(ValueError, match="share_id"):
        build_magma_share_import_handoff_status_summary(
            [accepted, tampered],
            history_limit=1,
        )


@pytest.mark.parametrize(
    ("path", "value", "match"),
    [
        (("share_id",), "C:/private/share", "share_id"),
        (("purpose",), "C:/private/purpose", "purpose"),
        (
            ("operator_ownership", "operator_agent_id"),
            "C:/private/agent",
            "operator_agent_id",
        ),
        (
            ("operator_ownership", "bridge_event_ref"),
            "C:/private/bridge",
            "bridge_event_ref",
        ),
        (
            ("operator_ownership", "decision_reason_ref"),
            "C:/private/reason",
            "decision_reason_ref",
        ),
        (("created_at_utc",), "C:/private/created-at", "created_at_utc"),
    ],
)
def test_import_handoff_status_summary_rejects_path_like_refs(
    tmp_path: Path,
    path: tuple[str, ...],
    value: str,
    match: str,
) -> None:
    share_manifest, source_manifest = _share_export(tmp_path)
    report = build_magma_share_manifest_import_report(
        share_manifest_path=share_manifest,
        source_manifest_path=source_manifest,
        verify_source_manifest=verify_manifest,
        now_utc=FIXED_NOW + timedelta(hours=1),
        max_age_hours=24,
    )
    handoff = build_magma_share_import_peer_review_handoff(
        import_report=report,
        operator_decision_id="operator:decision:magma-share-import:summary",
        operator_agent_id="operator:wd-image1",
        bridge_event_ref="bridge:wd-image1-magma-share-peer-review",
        import_decision="accepted_for_peer_review",
        decision_reason_ref="reason:cross_instance_replay_review",
        now_utc=FIXED_NOW + timedelta(hours=1),
    )
    tampered = json.loads(json.dumps(handoff))
    target = tampered
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises(ValueError, match=match):
        build_magma_share_import_handoff_status_summary(tampered)


def test_peer_review_handoff_write_refuses_failed_import_report(
    tmp_path: Path,
) -> None:
    bad_report = {
        "report_version": IMPORT_REPORT_VERSION,
        "ok": False,
        "blockers": ["context drift"],
    }

    with pytest.raises(ValueError, match="handoff-ready"):
        write_magma_share_import_peer_review_handoff(
            import_report=bad_report,
            out_path=tmp_path / "share_import_peer_review_handoff.json",
            operator_decision_id="operator:decision:magma-share-import:bad",
            operator_agent_id="operator:wd-image1",
            bridge_event_ref="bridge:wd-image1-magma-share-peer-review",
        )

    assert not (tmp_path / "share_import_peer_review_handoff.json").exists()


def test_peer_review_handoff_validates_admission_contract_when_present(
    tmp_path: Path,
) -> None:
    share_manifest, source_manifest = _share_export(tmp_path)
    report = build_magma_share_manifest_import_report(
        share_manifest_path=share_manifest,
        source_manifest_path=source_manifest,
        verify_source_manifest=verify_manifest,
        now_utc=FIXED_NOW + timedelta(hours=1),
        max_age_hours=24,
    )

    missing_contract = dict(report)
    missing_contract.pop("admission_contract")
    missing_contract.pop("admission_contract_digest")
    handoff = build_magma_share_import_peer_review_handoff(
        import_report=missing_contract,
        operator_decision_id="operator:decision:magma-share-import:missing",
        operator_agent_id="operator:wd-image1",
        bridge_event_ref="bridge:wd-image1-magma-share-peer-review",
    )
    assert handoff["handoff_version"] == IMPORT_HANDOFF_VERSION

    bad_digest = dict(report)
    bad_digest["admission_contract_digest"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="admission_contract_digest"):
        build_magma_share_import_peer_review_handoff(
            import_report=bad_digest,
            operator_decision_id="operator:decision:magma-share-import:digest",
            operator_agent_id="operator:wd-image1",
            bridge_event_ref="bridge:wd-image1-magma-share-peer-review",
        )

    relaxed_contract = json.loads(json.dumps(report))
    relaxed_contract["admission_contract"]["runtime_authority_granted"] = True
    relaxed_contract["admission_contract_digest"] = sha256_digest(
        relaxed_contract["admission_contract"]
    )
    with pytest.raises(
        ValueError,
        match="admission_contract.runtime_authority_granted",
    ):
        build_magma_share_import_peer_review_handoff(
            import_report=relaxed_contract,
            operator_decision_id="operator:decision:magma-share-import:relaxed",
            operator_agent_id="operator:wd-image1",
            bridge_event_ref="bridge:wd-image1-magma-share-peer-review",
        )


def test_peer_review_handoff_rejects_external_expected_purpose_mismatch(
    tmp_path: Path,
) -> None:
    share_manifest, source_manifest = _share_export(tmp_path)
    report = build_magma_share_manifest_import_report(
        share_manifest_path=share_manifest,
        source_manifest_path=source_manifest,
        verify_source_manifest=verify_manifest,
        now_utc=FIXED_NOW + timedelta(hours=1),
        max_age_hours=24,
    )

    with pytest.raises(ValueError, match="expected_purpose"):
        build_magma_share_import_peer_review_handoff(
            import_report=report,
            operator_decision_id="operator:decision:magma-share-import:purpose",
            operator_agent_id="operator:wd-image1",
            bridge_event_ref="bridge:wd-image1-magma-share-peer-review",
            expected_purpose="peer_review",
        )

    with pytest.raises(ValueError, match="expected_purpose is not allowed"):
        build_magma_share_import_peer_review_handoff(
            import_report=report,
            operator_decision_id="operator:decision:magma-share-import:invalid",
            operator_agent_id="operator:wd-image1",
            bridge_event_ref="bridge:wd-image1-magma-share-peer-review",
            expected_purpose="runtime_activation",
        )


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("required_checks", [], "admission_contract.canonical"),
        ("rejection_modes", [], "admission_contract.canonical"),
        ("report_invariants", {}, "admission_contract.canonical"),
        ("max_age_hours", 48, "admission_contract.canonical"),
        (
            "expected_share_id",
            "magma:share:import:forged",
            "admission_contract.expected_share_id",
        ),
        (
            "expected_purpose",
            "peer_review",
            "admission_contract.expected_purpose",
        ),
    ],
)
def test_peer_review_handoff_rejects_recomputed_admission_contract_tamper(
    tmp_path: Path,
    field: str,
    value: object,
    match: str,
) -> None:
    share_manifest, source_manifest = _share_export(tmp_path)
    report = build_magma_share_manifest_import_report(
        share_manifest_path=share_manifest,
        source_manifest_path=source_manifest,
        verify_source_manifest=verify_manifest,
        now_utc=FIXED_NOW + timedelta(hours=1),
        max_age_hours=24,
    )

    tampered = json.loads(json.dumps(report))
    tampered["admission_contract"][field] = value
    tampered["admission_contract_digest"] = sha256_digest(
        tampered["admission_contract"]
    )

    with pytest.raises(ValueError, match=match):
        build_magma_share_import_peer_review_handoff(
            import_report=tampered,
            operator_decision_id=f"operator:decision:magma-share-import:{field}",
            operator_agent_id="operator:wd-image1",
            bridge_event_ref="bridge:wd-image1-magma-share-peer-review",
        )


@pytest.mark.parametrize(
    ("label", "mutations", "match"),
    [
        (
            "max-age",
            {
                ("max_age_hours",): 999,
                ("admission_contract", "max_age_hours"): 999,
            },
            "max_age_hours",
        ),
        (
            "share-id",
            {
                ("share_id",): "magma:share:import:forged",
                (
                    "admission_contract",
                    "expected_share_id",
                ): "magma:share:import:forged",
            },
            "replay_plan entry 1 share_id",
        ),
        (
            "purpose-invalid",
            {
                ("purpose",): "runtime_activation",
                ("admission_contract", "expected_purpose"): "runtime_activation",
            },
            "purpose",
        ),
        (
            "purpose-allowed",
            {
                ("purpose",): "peer_review",
                ("admission_contract", "expected_purpose"): "peer_review",
            },
            "expected_purpose",
        ),
    ],
)
def test_peer_review_handoff_rejects_self_consistent_report_contract_tamper(
    tmp_path: Path,
    label: str,
    mutations: dict[tuple[str, ...], object],
    match: str,
) -> None:
    share_manifest, source_manifest = _share_export(tmp_path)
    report = build_magma_share_manifest_import_report(
        share_manifest_path=share_manifest,
        source_manifest_path=source_manifest,
        verify_source_manifest=verify_manifest,
        now_utc=FIXED_NOW + timedelta(hours=1),
        max_age_hours=24,
    )

    tampered = json.loads(json.dumps(report))
    for path, value in mutations.items():
        target = tampered
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
    tampered["admission_contract_digest"] = sha256_digest(
        tampered["admission_contract"]
    )

    with pytest.raises(ValueError, match=match):
        build_magma_share_import_peer_review_handoff(
            import_report=tampered,
            operator_decision_id=f"operator:decision:magma-share-import:{label}",
            operator_agent_id="operator:wd-image1",
            bridge_event_ref="bridge:wd-image1-magma-share-peer-review",
        )


def test_importer_rejects_stale_share_manifest(tmp_path: Path) -> None:
    share_manifest, source_manifest = _share_export(tmp_path)

    with pytest.raises(ValueError, match="stale"):
        build_magma_share_manifest_import_report(
            share_manifest_path=share_manifest,
            source_manifest_path=source_manifest,
            verify_source_manifest=verify_manifest,
            now_utc=FIXED_NOW + timedelta(hours=25),
            max_age_hours=24,
        )


def test_importer_rejects_future_share_manifest(tmp_path: Path) -> None:
    share_manifest, source_manifest = _share_export(tmp_path)

    with pytest.raises(ValueError, match="future"):
        build_magma_share_manifest_import_report(
            share_manifest_path=share_manifest,
            source_manifest_path=source_manifest,
            verify_source_manifest=verify_manifest,
            now_utc=FIXED_NOW - timedelta(minutes=10),
            max_age_hours=24,
        )


def test_importer_rejects_context_drifted_share_entry_digest(
    tmp_path: Path,
) -> None:
    share_manifest, source_manifest = _share_export(tmp_path)
    manifest = json.loads(share_manifest.read_text(encoding="utf-8"))
    manifest["entries"][0]["receipt_digest"] = "sha256:" + "0" * 64
    _write_json(share_manifest, manifest)

    with pytest.raises(ValueError, match="receipt_digest context drift"):
        build_magma_share_manifest_import_report(
            share_manifest_path=share_manifest,
            source_manifest_path=source_manifest,
            verify_source_manifest=verify_manifest,
            now_utc=FIXED_NOW,
        )


def test_importer_rejects_source_manifest_digest_context_drift(
    tmp_path: Path,
) -> None:
    share_manifest, source_manifest = _share_export(tmp_path)
    source = json.loads(source_manifest.read_text(encoding="utf-8"))
    source["chain_id"] = "magma:changed:after-share-export"
    _write_json(source_manifest, source)

    with pytest.raises(ValueError, match="sanitized_source_manifest_digest"):
        build_magma_share_manifest_import_report(
            share_manifest_path=share_manifest,
            source_manifest_path=source_manifest,
            verify_source_manifest=verify_manifest,
            now_utc=FIXED_NOW,
        )


def test_importer_fail_closes_when_source_receipt_bundle_is_tampered(
    tmp_path: Path,
) -> None:
    share_manifest, source_manifest = _share_export(tmp_path)
    manifest = json.loads(source_manifest.read_text(encoding="utf-8"))
    evaluation_path = (
        source_manifest.parent / manifest["entries"][0]["evaluation_result"]
    )
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    evaluation["verdict"] = "review"
    _write_json(evaluation_path, evaluation)

    with pytest.raises(ValueError, match="source receipt manifest verification failed"):
        build_magma_share_manifest_import_report(
            share_manifest_path=share_manifest,
            source_manifest_path=source_manifest,
            verify_source_manifest=verify_manifest,
            now_utc=FIXED_NOW,
        )


@pytest.mark.parametrize(
    "now",
    [
        "2026-05-28T07:40:00Z",
        "2026-05-29T09:00:00Z",
    ],
)
def test_cli_json_failure_reports_stale_or_future_status(
    tmp_path: Path,
    now: str,
) -> None:
    share_manifest, source_manifest = _share_export(tmp_path)

    result = _run_importer_json(
        share_manifest,
        source_manifest,
        now=now,
        max_age_hours=24,
    )

    payload = _assert_failed_admission_status(
        result,
        blocker_class="stale_or_future_manifest",
        tmp_path=tmp_path,
    )
    assert payload["admission_contract"]["max_age_hours"] == 24
    assert payload["expected_share_id_configured"] is True
    assert payload["expected_purpose_configured"] is True
    assert "magma share manifest import FAILED:" in result.stderr


def test_cli_json_failure_reports_expected_share_id_mismatch(
    tmp_path: Path,
) -> None:
    share_manifest, source_manifest = _share_export(tmp_path)

    result = _run_importer_json(
        share_manifest,
        source_manifest,
        expected_share_id="magma:share:import:wrong",
    )

    _assert_failed_admission_status(
        result,
        blocker_class="expected_share_id_mismatch",
        tmp_path=tmp_path,
    )


def test_cli_json_failure_reports_source_manifest_digest_drift(
    tmp_path: Path,
) -> None:
    share_manifest, source_manifest = _share_export(tmp_path)
    source = json.loads(source_manifest.read_text(encoding="utf-8"))
    source["chain_id"] = "magma:changed:after-share-export"
    _write_json(source_manifest, source)

    _assert_failed_admission_status(
        _run_importer_json(share_manifest, source_manifest),
        blocker_class="sanitized_source_manifest_digest_context_drift",
        tmp_path=tmp_path,
    )


def test_cli_json_failure_reports_raw_material_policy_relaxation(
    tmp_path: Path,
) -> None:
    share_manifest, source_manifest = _share_export(tmp_path)
    manifest = json.loads(share_manifest.read_text(encoding="utf-8"))
    manifest["export_policy"]["allow_raw_payloads"] = True
    _write_json(share_manifest, manifest)

    _assert_failed_admission_status(
        _run_importer_json(share_manifest, source_manifest),
        blocker_class="raw_material_export_or_policy_relaxation",
        tmp_path=tmp_path,
    )


def test_cli_json_failure_does_not_echo_unsafe_expected_share_id(
    tmp_path: Path,
) -> None:
    share_manifest, source_manifest = _share_export(tmp_path)
    private_marker = "DO" + "_NOT" + "_LEAK"

    result = _run_importer_json(
        share_manifest,
        source_manifest,
        expected_share_id=f"C:/private/{private_marker}",
    )

    payload = _assert_failed_admission_status(
        result,
        blocker_class="expected_share_id_mismatch",
        tmp_path=tmp_path,
    )
    assert payload["expected_share_id_configured"] is False
    assert payload["admission_contract"]["expected_share_id"] is None
    assert "C:/private" not in result.stdout
    assert private_marker not in result.stdout
    assert "C:/private" not in result.stderr
    assert private_marker not in result.stderr


def test_cli_admission_status_json_reports_ready_without_full_replay_plan(
    tmp_path: Path,
) -> None:
    share_manifest, source_manifest = _share_export(tmp_path)

    result = _run_importer_json(
        share_manifest,
        source_manifest,
        admission_status_json=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["summary_version"] == IMPORT_ADMISSION_STATUS_VERSION
    assert payload["source"] == "magma_share_manifest_import_report"
    assert payload["status"] == "ready_for_peer_review_handoff"
    assert payload["severity"] == "none"
    assert payload["ok"] is True
    assert payload["blocker_class"] == "none"
    assert payload["blockers"] == []
    assert payload["share_id"] == "magma:share:import:001"
    assert payload["purpose"] == "cross_instance_replay"
    assert payload["entry_count"] == 1
    assert payload["context_verified"] is True
    assert payload["context_drift_detected"] is False
    assert payload["replay_metadata_only"] is True
    assert payload["no_authority_import"] is True
    assert payload["transport_enabled"] is False
    assert payload["runtime_export_enabled"] is False
    assert payload["runtime_authority_granted"] is False
    assert payload["runtime_authority_changed"] is False
    assert payload["payload_files_imported"] == 0
    assert payload["payload_digest_imported"] is False
    assert payload["raw_material_imported"] is False
    assert payload["replacement_map_imported"] is False
    assert payload["local_paths_recorded"] is False
    assert "replay_plan" not in payload
    assert "admission_contract" not in payload
    serialized = json.dumps(payload, sort_keys=True)
    assert str(tmp_path) not in serialized
    assert not any(marker in serialized for marker in PRIVATE_MARKERS)


def test_cli_admission_status_json_failure_reports_rejected_status(
    tmp_path: Path,
) -> None:
    share_manifest, source_manifest = _share_export(tmp_path)

    result = _run_importer_json(
        share_manifest,
        source_manifest,
        expected_share_id="magma:share:import:wrong",
        admission_status_json=True,
    )

    _assert_failed_admission_status(
        result,
        blocker_class="expected_share_id_mismatch",
        tmp_path=tmp_path,
    )


def test_cli_json_import_is_no_authority_and_redacts_payload_markers(
    tmp_path: Path,
) -> None:
    share_manifest, source_manifest = _share_export(tmp_path)
    handoff_path = tmp_path / "share_import_peer_review_handoff.json"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--json",
            "--share-manifest",
            str(share_manifest),
            "--source-manifest",
            str(source_manifest),
            "--expected-share-id",
            "magma:share:import:001",
            "--expected-purpose",
            "cross_instance_replay",
            "--max-age-hours",
            "24",
            "--now",
            "2026-05-28T09:00:00Z",
            "--peer-review-handoff-out",
            str(handoff_path),
            "--operator-decision-id",
            "operator:decision:magma-share-cli",
            "--operator-agent",
            "operator:wd-image1",
            "--bridge-event-ref",
            "bridge:wd-image1-magma-share-import",
            "--import-decision",
            "accepted_for_peer_review",
            "--decision-reason-ref",
            "reason:cross_instance_replay_review",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["admission_contract"]["contract_version"] == (
        IMPORT_ADMISSION_CONTRACT_VERSION
    )
    assert payload["no_authority_import"] is True
    assert payload["runtime_authority_granted"] is False
    assert payload["payload_files_imported"] == 0
    handoff = payload["peer_review_handoff"]
    assert handoff["handoff_version"] == IMPORT_HANDOFF_VERSION
    assert handoff["operator_ownership"]["import_decision"] == (
        "accepted_for_peer_review"
    )
    assert handoff["authority"]["runtime_authority_granted"] is False
    assert handoff_path.exists()
    written_handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    assert written_handoff == handoff
    assert str(tmp_path) not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)
    assert not any(
        marker in _all_json_text(tmp_path / "share-export")
        for marker in PRIVATE_MARKERS
    )
    assert not any(
        marker in handoff_path.read_text(encoding="utf-8") for marker in PRIVATE_MARKERS
    )
