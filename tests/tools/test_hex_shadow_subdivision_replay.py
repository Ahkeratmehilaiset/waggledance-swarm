# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from tools.hex_shadow_subdivision_replay import (
    build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry,
    build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template,
    build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary,
    build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry,
    build_shadow_subdivision_replay_verifier_summary_bridge_event_template,
    build_shadow_subdivision_replay_verifier_summary,
    build_shadow_subdivision_replay_artifact,
    build_source_snapshot,
    verify_shadow_subdivision_replay_artifact,
    verify_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry,
    verify_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry,
)
from tools.wd_image1_capability_manifest import (
    build_hexagonal_upgrade_proof,
    build_hexagonal_upgrade_runtime_smoke,
)
from waggledance.core.bridge_event_schema import validate_event

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "hex_shadow_subdivision_replay.py"


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _refresh_artifact_digest(artifact: dict) -> None:
    artifact["artifact_digest"] = _canonical_digest(
        {key: value for key, value in artifact.items() if key != "artifact_digest"}
    )


def _valid_replay_artifact() -> dict:
    return build_shadow_subdivision_replay_artifact(
        upgrade_proof=build_hexagonal_upgrade_proof(ROOT),
        runtime_boundary_smoke=build_hexagonal_upgrade_runtime_smoke(ROOT),
        source_snapshot=build_source_snapshot(
            ROOT,
            now_utc=datetime(2026, 5, 31, tzinfo=timezone.utc),
        ),
    )


def _valid_verifier_report() -> dict:
    artifact = _valid_replay_artifact()
    return verify_shadow_subdivision_replay_artifact(
        artifact,
        expected_git_commit=artifact["source_snapshot"]["git_commit"],
    )


def _valid_verifier_summary() -> dict:
    return build_shadow_subdivision_replay_verifier_summary(
        _valid_verifier_report(),
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:hex-shadow-replay-verifier",
        now_utc=datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc),
    )


def _valid_verifier_summary_bridge_event_template() -> dict:
    return build_shadow_subdivision_replay_verifier_summary_bridge_event_template(
        _valid_verifier_summary(),
        agent_id="codex-lead-1",
        task_id="wd-image1-hex-shadow-replay-template",
        to="operator,claude-rco-1,codex-tools-1",
        role="lead-impl",
        run_id="codex-lead-1-20260531T120000Z",
        session_id="codex-lead-1-20260531T120000Z",
        now_utc=datetime(2026, 5, 31, 12, 30, tzinfo=timezone.utc),
    )


def _valid_verifier_summary_bridge_event_template_index_entry_verification() -> dict:
    template = _valid_verifier_summary_bridge_event_template()
    raw = _json_bytes(template)
    entry = build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry(
        template,
        template_report_bytes=raw,
        now_utc=datetime(2026, 5, 31, 12, 45, tzinfo=timezone.utc),
    )
    return verify_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry(
        entry,
        template,
        template_report_bytes=raw,
    )


def _valid_verifier_summary_bridge_event_template_index_entry_verification_summary() -> (
    dict
):
    return build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary(
        _valid_verifier_summary_bridge_event_template_index_entry_verification(),
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:hex-index-entry-verification",
        now_utc=datetime(2026, 5, 31, 13, 0, tzinfo=timezone.utc),
    )


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True).encode("utf-8")


def _raw_sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def test_hex_shadow_replay_verifier_accepts_current_artifact() -> None:
    artifact = _valid_replay_artifact()

    report = verify_shadow_subdivision_replay_artifact(
        artifact,
        expected_git_commit=artifact["source_snapshot"]["git_commit"],
    )

    assert report["ok"] is True
    assert report["proof_id"] == "hex_shadow_subdivision_replay_verifier_v1"
    assert report["verified_proof_id"] == "hex_shadow_subdivision_replay_v1"
    assert report["artifact_declared_ok"] is True
    assert report["recomputed_contract_ok"] is True
    assert report["blockers"] == []
    assert report["checks"]["artifact_digest_match"] is True
    assert report["checks"]["source_snapshot_git_commit_matches_expected"] is True
    assert report["checks"]["required_metric_names_present"] is True
    assert report["checks"]["required_metric_lines_present"] is True
    assert report["guardrails"]["runtime_authority_changed"] is False
    assert str(ROOT) not in json.dumps(report, sort_keys=True)


def test_hex_shadow_replay_verifier_summary_renders_path_free_context_without_authority() -> (
    None
):
    report = _valid_verifier_report()

    summary = build_shadow_subdivision_replay_verifier_summary(
        report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:hex-shadow-replay-verifier",
        now_utc=datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc),
    )

    assert summary["ok"] is True
    assert summary["proof_id"] == "hex_shadow_subdivision_replay_verifier_summary_v1"
    assert summary["created_at_utc"] == "2026-05-31T12:00:00Z"
    assert summary["reviewer_ownership"] == {
        "reviewer_agent_id": "claude-rco-1",
        "handoff_ref": "bridge:hex-shadow-replay-verifier",
        "manual_review_required": True,
        "approval_granted": False,
        "runtime_subdivision_authority_granted": False,
    }
    verification = summary["shadow_subdivision_replay_verification"]
    assert verification["verification_ok"] is True
    assert (
        verification["verifier_proof_id"] == "hex_shadow_subdivision_replay_verifier_v1"
    )
    assert verification["verified_proof_id"] == "hex_shadow_subdivision_replay_v1"
    assert verification["artifact_declared_ok"] is True
    assert verification["recomputed_contract_ok"] is True
    assert set(verification["digest_checks"].values()) == {"match"}
    assert set(verification["contract_checks"].values()) == {"match"}
    assert verification["guardrails"]["runtime_authority_changed"] is False
    assert verification["blockers"] == []
    assert summary["operator_boundary"]["verification_report_boundary_ok"] is True
    assert summary["manual_review_required"] is True
    assert summary["approval_granted"] is False
    assert summary["release_decision_made"] is False
    assert summary["automatic_release_decision"] is False
    assert summary["direct_bridge_write_performed"] is False
    assert summary["transport_added"] is False
    assert summary["external_fetch_performed"] is False
    assert summary["runtime_controls_added"] is False
    assert summary["runtime_subdivision_authority_granted"] is False
    assert summary["artifact_payloads_included"] is False
    assert summary["local_paths_recorded"] is False
    assert str(ROOT) not in json.dumps(summary, sort_keys=True)


def test_hex_shadow_replay_verifier_summary_propagates_blockers_without_authority() -> (
    None
):
    artifact = _valid_replay_artifact()
    artifact["guardrails"]["runtime_authority_changed"] = True
    _refresh_artifact_digest(artifact)
    report = verify_shadow_subdivision_replay_artifact(artifact)

    summary = build_shadow_subdivision_replay_verifier_summary(
        report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:hex-shadow-replay-verifier",
        now_utc=datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc),
    )

    assert summary["ok"] is False
    assert "guardrail_runtime_authority_changed" in summary["blockers"]
    assert (
        "verification_report_guardrail_not_false:runtime_authority_changed"
        in summary["blockers"]
    )
    assert (
        "verification_report_guardrail_not_false:runtime_authority_changed"
        in summary["operator_boundary"]["boundary_blockers"]
    )
    assert summary["approval_granted"] is False
    assert summary["direct_bridge_write_performed"] is False
    assert summary["runtime_subdivision_authority_granted"] is False


def test_hex_shadow_replay_verifier_summary_blocks_pathy_report_without_leaking_path() -> (
    None
):
    report = _valid_verifier_report()
    report["safe_conclusion"] = (
        "review scratch at C:/Python/project2-master/private.json"
    )

    summary = build_shadow_subdivision_replay_verifier_summary(
        report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:hex-shadow-replay-verifier",
        now_utc=datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc),
    )
    serialized = json.dumps(summary, sort_keys=True)

    assert summary["ok"] is False
    assert "verification_report_path_free" in summary["blockers"]
    assert "C:/Python/project2-master/private.json" not in serialized
    assert "project2-master" not in serialized
    assert summary["local_paths_recorded"] is False


def test_hex_shadow_replay_verifier_summary_blocks_malformed_report_blockers_before_template() -> (
    None
):
    report = _valid_verifier_report()
    report["blockers"] = "hidden_blocker"

    summary = build_shadow_subdivision_replay_verifier_summary(
        report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:hex-shadow-replay-verifier",
        now_utc=datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc),
    )
    template = build_shadow_subdivision_replay_verifier_summary_bridge_event_template(
        summary,
        agent_id="codex-lead-1",
        task_id="wd-image1-hex-shadow-replay-template",
    )

    assert summary["ok"] is False
    assert "verification_report_blockers_malformed" in summary["blockers"]
    assert (
        "verification_report_blockers_malformed"
        in summary["operator_boundary"]["boundary_blockers"]
    )
    assert template["ok"] is False
    assert "bridge_event_template" not in template
    assert any(
        "operator_boundary_blockers_present" in item for item in template["blockers"]
    )


def test_hex_shadow_replay_verifier_summary_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    report = _valid_verifier_report()
    report_path = tmp_path / "verifier-report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-verification-json",
            str(report_path),
            "--reviewer-agent",
            "codex-tools-1",
            "--handoff-ref",
            "bridge:hex-shadow-replay-verifier",
            "--strict",
        ],
        check=False,
        capture_output=True,
        encoding="utf-8",
    )

    assert result.returncode == 0
    summary = json.loads(result.stdout)
    assert summary["ok"] is True
    assert summary["proof_id"] == "hex_shadow_subdivision_replay_verifier_summary_v1"
    assert str(tmp_path) not in result.stdout
    assert str(report_path) not in result.stdout
    assert str(tmp_path) not in result.stderr


def test_hex_shadow_replay_verifier_summary_bridge_event_template_is_valid_without_writing() -> (
    None
):
    summary = _valid_verifier_summary()

    report = build_shadow_subdivision_replay_verifier_summary_bridge_event_template(
        summary,
        agent_id="codex-lead-1",
        task_id="wd-image1-hex-shadow-replay-template",
        to="operator,claude-rco-1,codex-tools-1",
        role="lead-impl",
        run_id="codex-lead-1-20260531T120000Z",
        session_id="codex-lead-1-20260531T120000Z",
        now_utc=datetime(2026, 5, 31, 12, 30, tzinfo=timezone.utc),
    )

    assert report["ok"] is True
    assert (
        report["proof_id"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_v1"
    )
    assert report["template_only"] is True
    assert report["direct_bridge_write_performed"] is False
    assert report["runtime_subdivision_authority_granted"] is False
    event = report["bridge_event_template"]
    validate_event(event)
    assert event["ts_utc"] == "2026-05-31T12:30:00Z"
    assert event["cwd"] == "template_not_emitted"
    assert event["paths"] == []
    assert event["write_scope"] == []
    payload = event["payload"]
    assert (
        payload["schema_version"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template.v1"
    )
    assert payload["template_only"] is True
    assert payload["manual_review_required"] is True
    assert payload["approval_granted"] is False
    assert payload["release_decision_made"] is False
    assert payload["runtime_controls_added"] is False
    assert payload["runtime_subdivision_authority_granted"] is False
    assert payload["local_paths_recorded"] is False
    assert set(
        payload["shadow_subdivision_replay_verification"]["digest_checks"].values()
    ) == {"match"}
    assert str(ROOT) not in json.dumps(report, sort_keys=True)


def test_hex_shadow_replay_verifier_summary_bridge_event_template_blocks_authority() -> (
    None
):
    summary = _valid_verifier_summary()
    summary["runtime_subdivision_authority_granted"] = True

    report = build_shadow_subdivision_replay_verifier_summary_bridge_event_template(
        summary,
        agent_id="codex-lead-1",
        task_id="wd-image1-hex-shadow-replay-template",
    )

    assert report["ok"] is False
    assert "bridge_event_template" not in report
    assert any(
        "verifier_summary_runtime_subdivision_authority_granted_not_false" in blocker
        for blocker in report["blockers"]
    )
    assert report["direct_bridge_write_performed"] is False
    assert report["runtime_subdivision_authority_granted"] is False


def test_hex_shadow_replay_verifier_summary_bridge_event_template_blocks_malformed_summary_blockers() -> (
    None
):
    summary = _valid_verifier_summary()
    summary["blockers"] = "hidden_blocker"

    report = build_shadow_subdivision_replay_verifier_summary_bridge_event_template(
        summary,
        agent_id="codex-lead-1",
        task_id="wd-image1-hex-shadow-replay-template",
    )

    assert report["ok"] is False
    assert "bridge_event_template" not in report
    assert any(
        "verifier_summary_blockers_malformed" in item for item in report["blockers"]
    )
    assert report["direct_bridge_write_performed"] is False
    assert report["runtime_subdivision_authority_granted"] is False


def test_hex_shadow_replay_verifier_summary_bridge_event_template_blocks_malformed_verification_blockers() -> (
    None
):
    summary = _valid_verifier_summary()
    summary["shadow_subdivision_replay_verification"]["blockers"] = "hidden_blocker"

    report = build_shadow_subdivision_replay_verifier_summary_bridge_event_template(
        summary,
        agent_id="codex-lead-1",
        task_id="wd-image1-hex-shadow-replay-template",
    )

    assert report["ok"] is False
    assert "bridge_event_template" not in report
    assert any(
        "verifier_summary_verification_blockers_malformed" in item
        for item in report["blockers"]
    )
    assert report["direct_bridge_write_performed"] is False
    assert report["runtime_subdivision_authority_granted"] is False


def test_hex_shadow_replay_verifier_summary_bridge_event_template_blocks_malformed_boundary_blockers() -> (
    None
):
    summary = _valid_verifier_summary()
    summary["operator_boundary"]["boundary_blockers"] = "hidden_blocker"

    report = build_shadow_subdivision_replay_verifier_summary_bridge_event_template(
        summary,
        agent_id="codex-lead-1",
        task_id="wd-image1-hex-shadow-replay-template",
    )

    assert report["ok"] is False
    assert "bridge_event_template" not in report
    assert any(
        "operator_boundary_blockers_malformed" in item for item in report["blockers"]
    )
    assert report["direct_bridge_write_performed"] is False
    assert report["runtime_subdivision_authority_granted"] is False


def test_hex_shadow_replay_verifier_summary_bridge_event_template_blocks_path_leak() -> (
    None
):
    summary = _valid_verifier_summary()
    summary["safe_conclusion"] = (
        "scratch verifier at C:/Python/project2-master/out.json"
    )

    report = build_shadow_subdivision_replay_verifier_summary_bridge_event_template(
        summary,
        agent_id="codex-lead-1",
        task_id="wd-image1-hex-shadow-replay-template",
    )
    serialized = json.dumps(report, sort_keys=True)

    assert report["ok"] is False
    assert any("verifier_summary_path_free" in item for item in report["blockers"])
    assert "C:/Python/project2-master/out.json" not in serialized
    assert "project2-master" not in serialized


def test_hex_shadow_replay_verifier_summary_bridge_event_template_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    summary = _valid_verifier_summary()
    summary_path = tmp_path / "verifier-summary.json"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-bridge-event-template-json",
            str(summary_path),
            "--agent",
            "codex-lead-1",
            "--task-id",
            "wd-image1-hex-shadow-replay-template",
            "--to",
            "operator,claude-rco-1,codex-tools-1",
            "--now",
            "2026-05-31T12:30:00Z",
            "--strict",
            "--json",
        ],
        check=False,
        capture_output=True,
        encoding="utf-8",
    )

    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["ok"] is True
    validate_event(report["bridge_event_template"])
    assert str(tmp_path) not in result.stdout
    assert str(summary_path) not in result.stdout
    assert str(tmp_path) not in result.stderr


def test_hex_shadow_replay_verifier_summary_bridge_event_template_index_entry_ties_digest_without_authority() -> (
    None
):
    template = _valid_verifier_summary_bridge_event_template()
    raw = _json_bytes(template)

    entry = build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry(
        template,
        template_report_bytes=raw,
        now_utc=datetime(2026, 5, 31, 12, 45, tzinfo=timezone.utc),
    )

    assert entry["ok"] is True
    assert (
        entry["proof_id"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_v1"
    )
    assert (
        entry["index_entry_version"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry.v1"
    )
    assert entry["created_at_utc"] == "2026-05-31T12:45:00Z"
    assert entry["artifact_count"] == 1
    artifact = entry["artifacts"][0]
    assert (
        artifact["artifact_id"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template"
    )
    assert artifact["sha256"] == _raw_sha256(raw)
    assert artifact["json_schema_version"] == (
        "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template.v1"
    )
    assert artifact["payload_included"] is False
    assert artifact["local_path_recorded"] is False
    index = entry["template_index_entry"]
    assert index["template_only"] is True
    assert index["bridge_event_schema_validated"] is True
    assert index["source_contract_check"] == "match"
    assert index["event_status"] == (
        "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_ready"
    )
    assert index["template_sha256"] == _raw_sha256(raw)
    assert entry["operator_boundary"]["approval_granted"] is False
    assert entry["direct_bridge_write_performed"] is False
    assert entry["runtime_subdivision_authority_granted"] is False
    assert entry["artifact_payloads_included"] is False
    assert entry["local_paths_recorded"] is False
    serialized = json.dumps(entry, sort_keys=True)
    assert '"bridge_event_template":' not in serialized
    assert (
        "Hex shadow subdivision replay verifier summary bridge-event template ready"
        not in serialized
    )
    assert str(ROOT) not in serialized


def test_hex_shadow_replay_verifier_summary_bridge_event_template_index_entry_blocks_authority() -> (
    None
):
    template = _valid_verifier_summary_bridge_event_template()
    template["runtime_subdivision_authority_granted"] = True

    entry = build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry(
        template,
        template_report_bytes=_json_bytes(template),
    )

    assert entry["ok"] is False
    assert "template_index_entry" not in entry
    assert any(
        "summary_bridge_event_template_runtime_subdivision_authority_granted_not_false"
        in item
        for item in entry["blockers"]
    )
    assert entry["direct_bridge_write_performed"] is False
    assert entry["runtime_subdivision_authority_granted"] is False


def test_hex_shadow_replay_verifier_summary_bridge_event_template_index_entry_blocks_malformed_blockers() -> (
    None
):
    template = _valid_verifier_summary_bridge_event_template()
    template["blockers"] = "hidden_blocker"

    entry = build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry(
        template,
        template_report_bytes=_json_bytes(template),
    )

    assert entry["ok"] is False
    assert "template_index_entry" not in entry
    assert any(
        "summary_bridge_event_template_blockers_malformed" in item
        for item in entry["blockers"]
    )
    assert entry["direct_bridge_write_performed"] is False
    assert entry["runtime_subdivision_authority_granted"] is False


def test_hex_shadow_replay_verifier_summary_bridge_event_template_index_entry_rejects_raw_bytes_mismatch() -> (
    None
):
    template = _valid_verifier_summary_bridge_event_template()

    entry = build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry(
        template,
        template_report_bytes=b'{"forged":true}',
    )

    assert entry["ok"] is False
    assert "template_index_entry" not in entry
    assert any(
        "summary_bridge_event_template_bytes_mismatch" in item
        for item in entry["blockers"]
    )
    assert entry["artifact_payloads_included"] is False
    assert entry["local_paths_recorded"] is False


def test_hex_shadow_replay_verifier_summary_bridge_event_template_index_entry_rejects_duplicate_raw_key_hidden_path() -> (
    None
):
    template = _valid_verifier_summary_bridge_event_template()
    hidden_prefix = (
        json.dumps(
            {
                "bridge_event_template": {
                    "message": "C:/Python/project2-master/private.json"
                }
            },
            sort_keys=True,
        )[:-1]
        + ","
    )
    raw = (hidden_prefix + json.dumps(template, sort_keys=True)[1:]).encode("utf-8")

    entry = build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry(
        template,
        template_report_bytes=raw,
    )
    serialized = json.dumps(entry, sort_keys=True)

    assert entry["ok"] is False
    assert "template_index_entry" not in entry
    assert any(
        "summary_bridge_event_template_duplicate_key" in item
        for item in entry["blockers"]
    )
    assert entry["artifact_payloads_included"] is False
    assert entry["local_paths_recorded"] is False
    assert "project2-master" not in serialized
    assert "private.json" not in serialized


def test_hex_shadow_replay_verifier_summary_bridge_event_template_index_entry_blocks_path_leak() -> (
    None
):
    template = _valid_verifier_summary_bridge_event_template()
    template["bridge_event_template"][
        "message"
    ] = "operator scratch artifact at C:/Python/project2-master/private.json"

    entry = build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry(
        template,
        template_report_bytes=_json_bytes(template),
    )
    serialized = json.dumps(entry, sort_keys=True)

    assert entry["ok"] is False
    assert any(
        "summary_bridge_event_template_path_free" in item for item in entry["blockers"]
    )
    assert "C:/Python/project2-master/private.json" not in serialized
    assert "project2-master" not in serialized


def test_hex_shadow_replay_verifier_summary_bridge_event_template_index_entry_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    template = _valid_verifier_summary_bridge_event_template()
    template_path = tmp_path / "verifier-summary-bridge-event-template.json"
    template_path.write_bytes(_json_bytes(template))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-bridge-event-template-index-entry-json",
            str(template_path),
            "--now",
            "2026-05-31T12:45:00Z",
            "--strict",
            "--json",
        ],
        check=False,
        capture_output=True,
        encoding="utf-8",
    )

    assert result.returncode == 0
    entry = json.loads(result.stdout)
    assert entry["ok"] is True
    assert entry["created_at_utc"] == "2026-05-31T12:45:00Z"
    assert entry["template_index_entry"]["bridge_event_schema_validated"] is True
    assert entry["direct_bridge_write_performed"] is False
    assert entry["runtime_subdivision_authority_granted"] is False
    assert str(tmp_path) not in result.stdout
    assert str(template_path) not in result.stdout
    assert str(tmp_path) not in result.stderr


def test_hex_shadow_replay_verifier_summary_bridge_event_template_index_entry_verifier_recomputes_digest_without_authority() -> (
    None
):
    template = _valid_verifier_summary_bridge_event_template()
    raw = _json_bytes(template)
    entry = build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry(
        template,
        template_report_bytes=raw,
        now_utc=datetime(2026, 5, 31, 12, 45, tzinfo=timezone.utc),
    )

    report = verify_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry(
        entry,
        template,
        template_report_bytes=raw,
    )
    serialized = json.dumps(report, sort_keys=True)

    assert report["ok"] is True
    assert (
        report["proof_id"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_v1"
    )
    assert (
        report["verification_version"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification.v1"
    )
    assert (
        report["verified_proof_id"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_v1"
    )
    artifact_id = "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template"
    assert report["digest_checks"][artifact_id] == "match"
    assert report["size_checks"][artifact_id] == "match"
    assert report["schema_version_checks"][artifact_id] == "match"
    assert report["source_contract_check"] == "match"
    assert report["rebuilt_index_entry_check"] == "match"
    assert report["bridge_event_schema_check"] == "match"
    assert report["direct_bridge_write_performed"] is False
    assert report["runtime_subdivision_authority_granted"] is False
    assert report["artifact_payloads_included"] is False
    assert report["local_paths_recorded"] is False
    assert '"bridge_event_template":' not in serialized
    assert str(ROOT) not in serialized


def test_hex_shadow_replay_verifier_summary_bridge_event_template_index_entry_verifier_rejects_digest_mismatch_path_free() -> (
    None
):
    template = _valid_verifier_summary_bridge_event_template()
    raw = _json_bytes(template)
    entry = build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry(
        template,
        template_report_bytes=raw,
    )
    tampered_template = json.loads(json.dumps(template))
    tampered_template["warnings"] = ["reviewer_context_changed"]
    tampered_raw = _json_bytes(tampered_template)

    report = verify_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry(
        entry,
        tampered_template,
        template_report_bytes=tampered_raw,
    )
    serialized = json.dumps(report, sort_keys=True)

    assert report["ok"] is False
    assert (
        "digest_mismatch:hex_shadow_subdivision_replay_verifier_summary_bridge_event_template"
        in report["blockers"]
    )
    assert "rebuilt_index_entry_mismatch" in report["blockers"]
    assert "project2-master" not in serialized
    assert "private.json" not in serialized


def test_hex_shadow_replay_verifier_summary_bridge_event_template_index_entry_verifier_rejects_missing_record_and_nested_authority() -> (
    None
):
    template = _valid_verifier_summary_bridge_event_template()
    raw = _json_bytes(template)
    entry = build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry(
        template,
        template_report_bytes=raw,
    )
    entry["artifacts"] = []
    entry["template_index_entry"]["runtime_subdivision_authority_granted"] = True

    report = verify_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry(
        entry,
        template,
        template_report_bytes=raw,
    )

    assert report["ok"] is False
    assert (
        "missing_artifact_record:hex_shadow_subdivision_replay_verifier_summary_bridge_event_template"
        in report["blockers"]
    )
    assert (
        "template_index_entry_runtime_subdivision_authority_granted_not_false"
        in report["blockers"]
    )
    assert report["runtime_subdivision_authority_granted"] is False


def test_hex_shadow_replay_verifier_summary_bridge_event_template_index_entry_verifier_rejects_deterministic_entry_drift() -> (
    None
):
    template = _valid_verifier_summary_bridge_event_template()
    raw = _json_bytes(template)
    entry = build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry(
        template,
        template_report_bytes=raw,
    )
    entry["reviewer_next_actions"] = ["review_changed"]

    report = verify_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry(
        entry,
        template,
        template_report_bytes=raw,
    )

    assert report["ok"] is False
    assert "rebuilt_index_entry_mismatch" in report["blockers"]
    assert report["rebuilt_index_entry_check"] == "mismatch"


def test_hex_shadow_replay_verifier_summary_bridge_event_template_index_entry_verifier_rejects_source_contract_forgery() -> (
    None
):
    template = _valid_verifier_summary_bridge_event_template()
    template["blockers"] = "hidden_blocker"

    entry = build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry(
        _valid_verifier_summary_bridge_event_template(),
        template_report_bytes=_json_bytes(
            _valid_verifier_summary_bridge_event_template()
        ),
    )
    report = verify_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry(
        entry,
        template,
        template_report_bytes=_json_bytes(template),
    )

    assert report["ok"] is False
    assert report["source_contract_check"] == "failed"
    assert any(
        item.startswith("source_contract_failed:") for item in report["blockers"]
    )


def test_hex_shadow_replay_verifier_summary_bridge_event_template_index_entry_verifier_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    template = _valid_verifier_summary_bridge_event_template()
    raw = _json_bytes(template)
    entry = build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry(
        template,
        template_report_bytes=raw,
    )
    template_path = tmp_path / "verifier-summary-bridge-event-template.json"
    index_path = tmp_path / "verifier-summary-bridge-event-template-index.json"
    template_path.write_bytes(raw)
    index_path.write_bytes(_json_bytes(entry))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verify-summary-bridge-event-template-index-entry-json",
            str(index_path),
            "--summary-bridge-event-template-source-json",
            str(template_path),
            "--strict",
            "--json",
        ],
        check=False,
        capture_output=True,
        encoding="utf-8",
    )

    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert report["rebuilt_index_entry_check"] == "match"
    assert report["direct_bridge_write_performed"] is False
    assert report["runtime_subdivision_authority_granted"] is False
    assert str(tmp_path) not in result.stdout
    assert str(index_path) not in result.stdout
    assert str(template_path) not in result.stdout
    assert str(tmp_path) not in result.stderr


def test_hex_shadow_replay_verifier_summary_bridge_event_template_index_entry_verifier_cli_rejects_duplicate_key_hidden_path_free(
    tmp_path: Path,
) -> None:
    template = _valid_verifier_summary_bridge_event_template()
    raw = _json_bytes(template)
    entry = build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry(
        template,
        template_report_bytes=raw,
    )
    hidden_prefix = (
        json.dumps(
            {
                "template_index_entry": {
                    "note": "C:/Python/project2-master/private.json"
                }
            },
            sort_keys=True,
        )[:-1]
        + ","
    )
    index_raw = (hidden_prefix + json.dumps(entry, sort_keys=True)[1:]).encode("utf-8")
    template_path = tmp_path / "verifier-summary-bridge-event-template.json"
    index_path = tmp_path / "verifier-summary-bridge-event-template-index.json"
    template_path.write_bytes(raw)
    index_path.write_bytes(index_raw)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verify-summary-bridge-event-template-index-entry-json",
            str(index_path),
            "--summary-bridge-event-template-source-json",
            str(template_path),
            "--strict",
            "--json",
        ],
        check=False,
        capture_output=True,
        encoding="utf-8",
    )

    assert result.returncode == 2
    report = json.loads(result.stdout)
    serialized = json.dumps(report, sort_keys=True)
    assert report["ok"] is False
    assert any(
        "summary_bridge_event_template_index_entry_duplicate_key" in item
        for item in report["blockers"]
    )
    assert "project2-master" not in serialized
    assert "private.json" not in serialized
    assert str(tmp_path) not in result.stdout
    assert str(tmp_path) not in result.stderr


def test_hex_shadow_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_renders_path_free_context_without_authority() -> (
    None
):
    report = _valid_verifier_summary_bridge_event_template_index_entry_verification()

    summary = build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary(
        report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:hex-index-entry-verification",
        now_utc=datetime(2026, 5, 31, 13, 0, tzinfo=timezone.utc),
    )
    nested = summary[
        "shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification"
    ]
    artifact_id = "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template"
    serialized = json.dumps(summary, sort_keys=True)

    assert summary["ok"] is True
    assert (
        summary["proof_id"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_v1"
    )
    assert (
        summary["summary_version"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary.v1"
    )
    assert summary["created_at_utc"] == "2026-05-31T13:00:00Z"
    assert nested["verification_ok"] is True
    assert nested["digest_checks"][artifact_id] == "match"
    assert nested["size_checks"][artifact_id] == "match"
    assert nested["schema_version_checks"][artifact_id] == "match"
    assert nested["source_contract_check"] == "match"
    assert nested["rebuilt_index_entry_check"] == "match"
    assert nested["bridge_event_schema_check"] == "match"
    assert summary["operator_boundary"]["index_entry_verification_report_boundary_ok"]
    assert summary["direct_bridge_write_performed"] is False
    assert summary["runtime_subdivision_authority_granted"] is False
    assert summary["artifact_payloads_included"] is False
    assert summary["local_paths_recorded"] is False
    assert '"template_index_entry":' not in serialized
    assert '"bridge_event_template":' not in serialized
    assert str(ROOT) not in serialized


def test_hex_shadow_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_rejects_authority() -> (
    None
):
    report = _valid_verifier_summary_bridge_event_template_index_entry_verification()
    report["runtime_subdivision_authority_granted"] = True

    summary = build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary(
        report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:hex-index-entry-verification",
    )

    assert summary["ok"] is False
    assert (
        "index_entry_verification_runtime_subdivision_authority_granted_not_false"
        in summary["blockers"]
    )
    assert summary["runtime_subdivision_authority_granted"] is False


def test_hex_shadow_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_rejects_path_leak() -> (
    None
):
    report = _valid_verifier_summary_bridge_event_template_index_entry_verification()
    report["warnings"] = ["C:/Python/project2-master/private.json"]

    summary = build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary(
        report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:hex-index-entry-verification",
    )
    serialized = json.dumps(summary, sort_keys=True)

    assert summary["ok"] is False
    assert "index_entry_verification_report_path_free" in summary["blockers"]
    assert "project2-master" not in serialized
    assert "private.json" not in serialized


def test_hex_shadow_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    report = _valid_verifier_summary_bridge_event_template_index_entry_verification()
    report_path = (
        tmp_path / "verifier-summary-bridge-event-template-index-verification.json"
    )
    report_path.write_bytes(_json_bytes(report))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-bridge-event-template-index-entry-verification-json",
            str(report_path),
            "--reviewer-agent",
            "claude-rco-1",
            "--handoff-ref",
            "bridge:hex-index-entry-verification",
            "--now",
            "2026-05-31T13:15:00Z",
            "--strict",
            "--json",
        ],
        check=False,
        capture_output=True,
        encoding="utf-8",
    )

    assert result.returncode == 0
    summary = json.loads(result.stdout)
    assert summary["ok"] is True
    assert summary["created_at_utc"] == "2026-05-31T13:15:00Z"
    assert summary["direct_bridge_write_performed"] is False
    assert summary["runtime_subdivision_authority_granted"] is False
    assert str(tmp_path) not in result.stdout
    assert str(report_path) not in result.stdout
    assert str(tmp_path) not in result.stderr


def test_hex_shadow_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_cli_rejects_duplicate_key_hidden_path_free(
    tmp_path: Path,
) -> None:
    report = _valid_verifier_summary_bridge_event_template_index_entry_verification()
    hidden_prefix = (
        json.dumps(
            {
                "warnings": [
                    "C:/Python/project2-master/private.json",
                ]
            },
            sort_keys=True,
        )[:-1]
        + ","
    )
    report_raw = (hidden_prefix + json.dumps(report, sort_keys=True)[1:]).encode(
        "utf-8"
    )
    report_path = (
        tmp_path / "verifier-summary-bridge-event-template-index-verification.json"
    )
    report_path.write_bytes(report_raw)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-bridge-event-template-index-entry-verification-json",
            str(report_path),
            "--strict",
            "--json",
        ],
        check=False,
        capture_output=True,
        encoding="utf-8",
    )

    assert result.returncode == 2
    summary = json.loads(result.stdout)
    serialized = json.dumps(summary, sort_keys=True)
    assert summary["ok"] is False
    assert any(
        "summary_bridge_event_template_index_entry_verification_duplicate_key" in item
        for item in summary["blockers"]
    )
    assert "project2-master" not in serialized
    assert "private.json" not in serialized
    assert str(tmp_path) not in result.stdout
    assert str(tmp_path) not in result.stderr


def test_hex_shadow_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_is_valid_without_writing() -> (
    None
):
    summary = (
        _valid_verifier_summary_bridge_event_template_index_entry_verification_summary()
    )

    report = build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template(
        summary,
        agent_id="codex-lead-1",
        task_id="wd-image1-hex-index-entry-verification-summary-template",
        to="operator,claude-rco-1,codex-tools-1",
        role="lead-impl",
        run_id="codex-lead-1-20260531T131500Z",
        session_id="codex-lead-1-20260531T131500Z",
        now_utc=datetime(2026, 5, 31, 13, 15, tzinfo=timezone.utc),
    )
    event = report["bridge_event_template"]
    payload = event["payload"]
    nested = payload[
        "shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification"
    ]
    artifact_id = "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template"
    serialized = json.dumps(report, sort_keys=True)

    assert report["ok"] is True
    assert (
        report["proof_id"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_v1"
    )
    assert (
        report["template_version"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template.v1"
    )
    assert report["template_only"] is True
    assert report["direct_bridge_write_performed"] is False
    assert report["runtime_subdivision_authority_granted"] is False
    validate_event(event)
    assert event["ts_utc"] == "2026-05-31T13:15:00Z"
    assert event["cwd"] == "template_not_emitted"
    assert event["paths"] == []
    assert event["write_scope"] == []
    assert (
        event["status"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_ready"
    )
    assert (
        payload["schema_version"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template.v1"
    )
    assert (
        payload["summary_proof_id"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_v1"
    )
    assert nested["verification_ok"] is True
    assert nested["digest_checks"][artifact_id] == "match"
    assert nested["size_checks"][artifact_id] == "match"
    assert nested["schema_version_checks"][artifact_id] == "match"
    assert nested["source_contract_check"] == "match"
    assert nested["rebuilt_index_entry_check"] == "match"
    assert nested["bridge_event_schema_check"] == "match"
    assert payload["operator_boundary"]["index_entry_verification_report_boundary_ok"]
    assert payload["direct_bridge_write_performed"] is False
    assert payload["runtime_subdivision_authority_granted"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert str(ROOT) not in serialized


def test_hex_shadow_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_blocks_authority() -> (
    None
):
    summary = (
        _valid_verifier_summary_bridge_event_template_index_entry_verification_summary()
    )
    summary["runtime_subdivision_authority_granted"] = True

    report = build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template(
        summary,
        agent_id="codex-lead-1",
        task_id="wd-image1-hex-index-entry-verification-summary-template",
    )

    assert report["ok"] is False
    assert "bridge_event_template" not in report
    assert any(
        "index_entry_verification_summary_runtime_subdivision_authority_granted_not_false"
        in item
        for item in report["blockers"]
    )
    assert report["runtime_subdivision_authority_granted"] is False


def test_hex_shadow_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_blocks_malformed_boundary_blockers() -> (
    None
):
    summary = (
        _valid_verifier_summary_bridge_event_template_index_entry_verification_summary()
    )
    summary["operator_boundary"]["boundary_blockers"] = "hidden_blocker"

    report = build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template(
        summary,
        agent_id="codex-lead-1",
        task_id="wd-image1-hex-index-entry-verification-summary-template",
    )

    assert report["ok"] is False
    assert "bridge_event_template" not in report
    assert any(
        "index_entry_verification_summary_boundary_blockers_malformed" in item
        for item in report["blockers"]
    )
    assert report["direct_bridge_write_performed"] is False


def test_hex_shadow_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_blocks_path_leak() -> (
    None
):
    summary = (
        _valid_verifier_summary_bridge_event_template_index_entry_verification_summary()
    )
    summary["safe_conclusion"] = (
        "scratch verifier at C:/Python/project2-master/out.json"
    )

    report = build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template(
        summary,
        agent_id="codex-lead-1",
        task_id="wd-image1-hex-index-entry-verification-summary-template",
    )
    serialized = json.dumps(report, sort_keys=True)

    assert report["ok"] is False
    assert any(
        "index_entry_verification_summary_path_free" in item
        for item in report["blockers"]
    )
    assert "C:/Python/project2-master/out.json" not in serialized
    assert "project2-master" not in serialized


def test_hex_shadow_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    summary = (
        _valid_verifier_summary_bridge_event_template_index_entry_verification_summary()
    )
    summary_path = (
        tmp_path
        / "verifier-summary-bridge-event-template-index-verification-summary.json"
    )
    summary_path.write_bytes(_json_bytes(summary))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-bridge-event-template-index-entry-verification-summary-json",
            str(summary_path),
            "--agent",
            "codex-lead-1",
            "--task-id",
            "wd-image1-hex-index-entry-verification-summary-template",
            "--to",
            "operator,claude-rco-1,codex-tools-1",
            "--now",
            "2026-05-31T13:20:00Z",
            "--strict",
            "--json",
        ],
        check=False,
        capture_output=True,
        encoding="utf-8",
    )

    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["ok"] is True
    validate_event(report["bridge_event_template"])
    assert report["bridge_event_template"]["ts_utc"] == "2026-05-31T13:20:00Z"
    assert str(tmp_path) not in result.stdout
    assert str(summary_path) not in result.stdout
    assert str(tmp_path) not in result.stderr


def test_hex_shadow_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_cli_rejects_duplicate_key_hidden_path_free(
    tmp_path: Path,
) -> None:
    summary = (
        _valid_verifier_summary_bridge_event_template_index_entry_verification_summary()
    )
    hidden_prefix = (
        json.dumps(
            {"reviewer_ownership": {"note": "C:/Python/project2-master/private.json"}},
            sort_keys=True,
        )[:-1]
        + ","
    )
    summary_raw = (hidden_prefix + json.dumps(summary, sort_keys=True)[1:]).encode(
        "utf-8"
    )
    summary_path = (
        tmp_path
        / "verifier-summary-bridge-event-template-index-verification-summary.json"
    )
    summary_path.write_bytes(summary_raw)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-bridge-event-template-index-entry-verification-summary-json",
            str(summary_path),
            "--strict",
            "--json",
        ],
        check=False,
        capture_output=True,
        encoding="utf-8",
    )

    assert result.returncode == 2
    report = json.loads(result.stdout)
    serialized = json.dumps(report, sort_keys=True)
    assert report["ok"] is False
    assert any(
        "summary_bridge_event_template_index_entry_verification_summary_duplicate_key"
        in item
        for item in report["blockers"]
    )
    assert "project2-master" not in serialized
    assert "private.json" not in serialized
    assert str(tmp_path) not in result.stdout
    assert str(tmp_path) not in result.stderr


def _valid_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template() -> (
    dict
):
    summary = (
        _valid_verifier_summary_bridge_event_template_index_entry_verification_summary()
    )
    return build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template(
        summary,
        agent_id="codex-lead-1",
        task_id="wd-image1-hex-index-entry-verification-summary-template",
        to="operator,claude-rco-1,codex-tools-1",
        role="lead-impl",
        run_id="codex-lead-1-20260531T131500Z",
        session_id="codex-lead-1-20260531T131500Z",
        now_utc=datetime(2026, 5, 31, 13, 15, tzinfo=timezone.utc),
    )


def _valid_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry() -> (
    dict
):
    template = (
        _valid_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template()
    )
    return build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        template,
        template_report_bytes=_json_bytes(template),
        now_utc=datetime(2026, 5, 31, 13, 25, tzinfo=timezone.utc),
    )


def test_hex_shadow_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_is_path_free() -> (
    None
):
    template = (
        _valid_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template()
    )
    template_bytes = _json_bytes(template)

    report = build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        template,
        template_report_bytes=template_bytes,
        now_utc=datetime(2026, 5, 31, 13, 25, tzinfo=timezone.utc),
    )
    artifact_id = (
        "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_"
        "index_entry_verification_summary_bridge_event_template"
    )
    artifact = report["artifacts"][0]
    template_index = report["template_index_entry"]
    serialized = json.dumps(report, sort_keys=True)

    assert report["ok"] is True
    assert (
        report["proof_id"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_v1"
    )
    assert (
        report["index_entry_version"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry.v1"
    )
    assert report["created_at_utc"] == "2026-05-31T13:25:00Z"
    assert report["artifact_count"] == 1
    assert artifact["artifact_id"] == artifact_id
    assert artifact["sha256"] == "sha256:" + hashlib.sha256(template_bytes).hexdigest()
    assert artifact["size_bytes"] == len(template_bytes)
    assert (
        artifact["json_schema_version"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template.v1"
    )
    assert artifact["payload_included"] is False
    assert artifact["local_path_recorded"] is False
    assert template_index["artifact_id"] == artifact_id
    assert (
        template_index["source_summary_proof_id"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_v1"
    )
    assert (
        template_index["template_proof_id"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_v1"
    )
    assert template_index["source_contract_check"] == "match"
    assert template_index["bridge_event_schema_validated"] is True
    assert template_index["template_only"] is True
    assert report["template_only"] is True
    assert report["direct_bridge_write_performed"] is False
    assert report["transport_added"] is False
    assert report["external_fetch_performed"] is False
    assert report["runtime_subdivision_authority_granted"] is False
    assert report["artifact_payloads_included"] is False
    assert report["local_paths_recorded"] is False
    assert str(ROOT) not in serialized


def test_hex_shadow_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_blocks_authority() -> (
    None
):
    template = (
        _valid_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template()
    )
    template["runtime_subdivision_authority_granted"] = True

    report = build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        template,
        now_utc=datetime(2026, 5, 31, 13, 25, tzinfo=timezone.utc),
    )

    assert report["ok"] is False
    assert any(
        "bridge_event_template_runtime_subdivision_authority_granted_not_false" in item
        for item in report["blockers"]
    )
    assert report["runtime_subdivision_authority_granted"] is False


def test_hex_shadow_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_blocks_path_leak() -> (
    None
):
    template = (
        _valid_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template()
    )
    template["warnings"] = ["C:/Python/project2-master/private.json"]

    report = build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        template,
        now_utc=datetime(2026, 5, 31, 13, 25, tzinfo=timezone.utc),
    )
    serialized = json.dumps(report, sort_keys=True)

    assert report["ok"] is False
    assert any("bridge_event_template_path_free" in item for item in report["blockers"])
    assert "project2-master" not in serialized
    assert "private.json" not in serialized


def test_hex_shadow_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    template = (
        _valid_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template()
    )
    template_path = (
        tmp_path
        / "verifier-summary-bridge-event-template-index-verification-summary-template.json"
    )
    template_path.write_bytes(_json_bytes(template))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-bridge-event-template-index-entry-verification-summary-bridge-event-template-index-entry-json",
            str(template_path),
            "--now",
            "2026-05-31T13:25:00Z",
            "--strict",
            "--json",
        ],
        check=False,
        capture_output=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert report["created_at_utc"] == "2026-05-31T13:25:00Z"
    assert report["template_only"] is True
    assert report["direct_bridge_write_performed"] is False
    assert report["artifact_payloads_included"] is False
    assert str(tmp_path) not in result.stdout
    assert str(template_path) not in result.stdout
    assert str(tmp_path) not in result.stderr


def test_hex_shadow_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_cli_rejects_duplicate_key_hidden_path_free(
    tmp_path: Path,
) -> None:
    template = (
        _valid_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template()
    )
    hidden_prefix = (
        json.dumps({"warnings": ["C:/Python/project2-master/private.json"]})[:-1] + ","
    )
    template_raw = (hidden_prefix + json.dumps(template, sort_keys=True)[1:]).encode(
        "utf-8"
    )
    template_path = (
        tmp_path
        / "verifier-summary-bridge-event-template-index-verification-summary-template.json"
    )
    template_path.write_bytes(template_raw)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-bridge-event-template-index-entry-verification-summary-bridge-event-template-index-entry-json",
            str(template_path),
            "--strict",
            "--json",
        ],
        check=False,
        capture_output=True,
        encoding="utf-8",
    )

    assert result.returncode == 2
    report = json.loads(result.stdout)
    serialized = json.dumps(report, sort_keys=True)
    assert report["ok"] is False
    assert any(
        "bridge_event_template_duplicate_key" in item for item in report["blockers"]
    )
    assert "project2-master" not in serialized
    assert "private.json" not in serialized
    assert str(tmp_path) not in result.stdout
    assert str(tmp_path) not in result.stderr


def test_hex_shadow_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verifier_recomputes_digest_without_authority() -> (
    None
):
    template = (
        _valid_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template()
    )
    raw = _json_bytes(template)
    entry = build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        template,
        template_report_bytes=raw,
        now_utc=datetime(2026, 5, 31, 13, 25, tzinfo=timezone.utc),
    )

    report = verify_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        entry,
        template,
        template_report_bytes=raw,
    )
    serialized = json.dumps(report, sort_keys=True)
    artifact_id = (
        "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_"
        "index_entry_verification_summary_bridge_event_template"
    )

    assert report["ok"] is True
    assert (
        report["proof_id"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_v1"
    )
    assert (
        report["verification_version"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification.v1"
    )
    assert (
        report["verified_proof_id"]
        == "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_v1"
    )
    assert report["digest_checks"][artifact_id] == "match"
    assert report["size_checks"][artifact_id] == "match"
    assert report["schema_version_checks"][artifact_id] == "match"
    assert report["source_contract_check"] == "match"
    assert report["rebuilt_index_entry_check"] == "match"
    assert report["bridge_event_schema_check"] == "match"
    assert report["direct_bridge_write_performed"] is False
    assert report["runtime_subdivision_authority_granted"] is False
    assert report["artifact_payloads_included"] is False
    assert report["local_paths_recorded"] is False
    assert '"bridge_event_template":' not in serialized
    assert str(ROOT) not in serialized


def test_hex_shadow_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verifier_rejects_digest_mismatch_path_free() -> (
    None
):
    template = (
        _valid_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template()
    )
    raw = _json_bytes(template)
    entry = build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        template,
        template_report_bytes=raw,
    )
    tampered_template = json.loads(json.dumps(template))
    tampered_template["warnings"] = ["reviewer_context_changed"]
    tampered_raw = _json_bytes(tampered_template)

    report = verify_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        entry,
        tampered_template,
        template_report_bytes=tampered_raw,
    )
    serialized = json.dumps(report, sort_keys=True)

    assert report["ok"] is False
    assert (
        "digest_mismatch:hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template"
        in report["blockers"]
    )
    assert "rebuilt_index_entry_mismatch" in report["blockers"]
    assert "project2-master" not in serialized
    assert "private.json" not in serialized


def test_hex_shadow_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verifier_rejects_missing_record_and_nested_authority() -> (
    None
):
    template = (
        _valid_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template()
    )
    raw = _json_bytes(template)
    entry = build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        template,
        template_report_bytes=raw,
    )
    entry["artifacts"] = []
    entry["template_index_entry"]["runtime_subdivision_authority_granted"] = True

    report = verify_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        entry,
        template,
        template_report_bytes=raw,
    )

    assert report["ok"] is False
    assert (
        "missing_artifact_record:hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template"
        in report["blockers"]
    )
    assert (
        "template_index_entry_runtime_subdivision_authority_granted_not_false"
        in report["blockers"]
    )
    assert report["runtime_subdivision_authority_granted"] is False


def test_hex_shadow_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verifier_rejects_deterministic_entry_drift() -> (
    None
):
    template = (
        _valid_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template()
    )
    raw = _json_bytes(template)
    entry = build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        template,
        template_report_bytes=raw,
    )
    entry["reviewer_next_actions"] = ["review_changed"]

    report = verify_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        entry,
        template,
        template_report_bytes=raw,
    )

    assert report["ok"] is False
    assert "rebuilt_index_entry_mismatch" in report["blockers"]
    assert report["rebuilt_index_entry_check"] == "mismatch"


def test_hex_shadow_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verifier_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    template = (
        _valid_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template()
    )
    raw = _json_bytes(template)
    entry = build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        template,
        template_report_bytes=raw,
    )
    template_path = tmp_path / "verification-summary-template.json"
    index_path = tmp_path / "verification-summary-template-index-entry.json"
    template_path.write_bytes(raw)
    index_path.write_bytes(_json_bytes(entry))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verify-summary-bridge-event-template-index-entry-verification-summary-bridge-event-template-index-entry-json",
            str(index_path),
            "--summary-bridge-event-template-index-entry-verification-summary-bridge-event-template-source-json",
            str(template_path),
            "--strict",
            "--json",
        ],
        check=False,
        capture_output=True,
        encoding="utf-8",
    )

    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert report["rebuilt_index_entry_check"] == "match"
    assert report["direct_bridge_write_performed"] is False
    assert report["runtime_subdivision_authority_granted"] is False
    assert str(tmp_path) not in result.stdout
    assert str(index_path) not in result.stdout
    assert str(template_path) not in result.stdout
    assert str(tmp_path) not in result.stderr


def test_hex_shadow_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verifier_cli_rejects_duplicate_key_hidden_path_free(
    tmp_path: Path,
) -> None:
    template = (
        _valid_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template()
    )
    raw = _json_bytes(template)
    entry = build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        template,
        template_report_bytes=raw,
    )
    hidden_prefix = (
        json.dumps(
            {
                "template_index_entry": {
                    "note": "C:/Python/project2-master/private.json"
                }
            },
            sort_keys=True,
        )[:-1]
        + ","
    )
    index_raw = (hidden_prefix + json.dumps(entry, sort_keys=True)[1:]).encode("utf-8")
    template_path = tmp_path / "verification-summary-template.json"
    index_path = tmp_path / "verification-summary-template-index-entry.json"
    template_path.write_bytes(raw)
    index_path.write_bytes(index_raw)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verify-summary-bridge-event-template-index-entry-verification-summary-bridge-event-template-index-entry-json",
            str(index_path),
            "--summary-bridge-event-template-index-entry-verification-summary-bridge-event-template-source-json",
            str(template_path),
            "--strict",
            "--json",
        ],
        check=False,
        capture_output=True,
        encoding="utf-8",
    )

    assert result.returncode == 2
    report = json.loads(result.stdout)
    serialized = json.dumps(report, sort_keys=True)
    assert report["ok"] is False
    assert any(
        "bridge_event_template_index_entry_duplicate_key" in item
        for item in report["blockers"]
    )
    assert "project2-master" not in serialized
    assert "private.json" not in serialized
    assert str(tmp_path) not in result.stdout
    assert str(tmp_path) not in result.stderr


def test_hex_shadow_replay_blocked_artifact_digest_includes_blocked_reason() -> None:
    runtime_smoke = json.loads(json.dumps(build_hexagonal_upgrade_runtime_smoke(ROOT)))
    runtime_smoke["ok"] = False
    runtime_smoke["operator_metrics_smoke"]["runtime_contract"]["status_code"] = 500
    artifact = build_shadow_subdivision_replay_artifact(
        upgrade_proof=build_hexagonal_upgrade_proof(ROOT),
        runtime_boundary_smoke=runtime_smoke,
        source_snapshot=build_source_snapshot(
            ROOT,
            now_utc=datetime(2026, 5, 31, tzinfo=timezone.utc),
        ),
    )

    report = verify_shadow_subdivision_replay_artifact(
        artifact,
        expected_git_commit=artifact["source_snapshot"]["git_commit"],
    )

    assert artifact["ok"] is False
    assert artifact["blocked_reason"] == "upstream_proof_or_metric_contract_failed"
    assert report["ok"] is False
    assert report["checks"]["artifact_digest_match"] is True
    assert "artifact_digest_match" not in report["blockers"]
    assert "metric_status_code" in report["blockers"]


def test_hex_shadow_replay_verifier_rejects_tampered_plan_digest() -> None:
    artifact = _valid_replay_artifact()
    artifact["shadow_plan_summary"]["new_child_cell_ids"].append("thermal.hidden")

    report = verify_shadow_subdivision_replay_artifact(artifact)

    assert report["ok"] is False
    assert "digest_mismatch:plan" in report["blockers"]
    assert "digest_mismatch:full_binding" in report["blockers"]
    assert "artifact_digest_match" in report["blockers"]


def test_hex_shadow_replay_verifier_rejects_forged_runtime_authority() -> None:
    artifact = _valid_replay_artifact()
    artifact["guardrails"]["runtime_authority_changed"] = True
    _refresh_artifact_digest(artifact)

    report = verify_shadow_subdivision_replay_artifact(artifact)

    assert report["ok"] is False
    assert "guardrail_runtime_authority_changed" in report["blockers"]
    assert "declared_ok_matches_recomputed_contract" in report["blockers"]
    assert "artifact_digest_match" not in report["blockers"]


def test_hex_shadow_replay_verifier_rejects_stale_source_snapshot() -> None:
    artifact = _valid_replay_artifact()

    report = verify_shadow_subdivision_replay_artifact(
        artifact,
        expected_git_commit="f" * 40,
    )

    assert report["ok"] is False
    assert "source_snapshot_git_commit_matches_expected" in report["blockers"]
    assert report["checks"]["artifact_digest_match"] is True


def test_hex_shadow_replay_verifier_rejects_workspace_path_leak() -> None:
    artifact = _valid_replay_artifact()
    artifact["safe_conclusion"] = (
        "operator scratch artifact at /workspace/waggledance-swarm/replay.json"
    )
    _refresh_artifact_digest(artifact)

    report = verify_shadow_subdivision_replay_artifact(artifact)

    assert report["ok"] is False
    assert "artifact_path_free" in report["blockers"]
    assert "artifact_digest_match" not in report["blockers"]


def test_hex_shadow_replay_verifier_rejects_forward_slash_windows_path() -> None:
    artifact = _valid_replay_artifact()
    artifact["safe_conclusion"] = (
        "operator scratch artifact at C:/Python/project2-master/secret.json"
    )
    _refresh_artifact_digest(artifact)

    report = verify_shadow_subdivision_replay_artifact(artifact)

    assert report["ok"] is False
    assert "artifact_path_free" in report["blockers"]
    assert "artifact_digest_match" not in report["blockers"]


def test_hex_shadow_replay_verifier_cli_rejects_recomputed_path_leak(
    tmp_path: Path,
) -> None:
    artifact = _valid_replay_artifact()
    artifact["safe_conclusion"] = (
        "operator scratch artifact at C:/Python/project2-master/secret.json"
    )
    _refresh_artifact_digest(artifact)
    artifact_path = tmp_path / "replay.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verify-json",
            str(artifact_path),
            "--expected-git-commit",
            artifact["source_snapshot"]["git_commit"],
            "--strict",
        ],
        check=False,
        capture_output=True,
        encoding="utf-8",
    )

    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["ok"] is False
    assert "artifact_path_free" in report["blockers"]
    assert "artifact_digest_match" not in report["blockers"]


def test_hex_shadow_replay_verifier_cli_redacts_invalid_expected_commit(
    tmp_path: Path,
) -> None:
    artifact = _valid_replay_artifact()
    artifact_path = tmp_path / "replay.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verify-json",
            str(artifact_path),
            "--expected-git-commit",
            "/workspace/secret",
            "--strict",
        ],
        check=False,
        capture_output=True,
        encoding="utf-8",
    )

    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["ok"] is False
    assert report["expected_git_commit"] is None
    assert "expected_git_commit_valid" in report["blockers"]
    assert "/workspace/secret" not in result.stdout
    assert "/workspace/secret" not in result.stderr


def test_hex_shadow_replay_verifier_cli_rejects_invalid_json_path_free(
    tmp_path: Path,
) -> None:
    artifact_path = tmp_path / "replay.json"
    artifact_path.write_text('{"ok": NaN}', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verify-json",
            str(artifact_path),
            "--strict",
        ],
        check=False,
        capture_output=True,
        encoding="utf-8",
    )

    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["ok"] is False
    assert report["blockers"] == ["artifact_json_invalid"]
    assert str(tmp_path) not in result.stdout
    assert str(artifact_path) not in result.stdout
    assert str(tmp_path) not in result.stderr
