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
    build_shadow_subdivision_replay_verifier_summary,
    build_shadow_subdivision_replay_artifact,
    build_source_snapshot,
    verify_shadow_subdivision_replay_artifact,
)
from tools.wd_image1_capability_manifest import (
    build_hexagonal_upgrade_proof,
    build_hexagonal_upgrade_runtime_smoke,
)


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
        {
            key: value
            for key, value in artifact.items()
            if key != "artifact_digest"
        }
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


def test_hex_shadow_replay_verifier_summary_renders_path_free_context_without_authority() -> None:
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
    assert verification["verifier_proof_id"] == "hex_shadow_subdivision_replay_verifier_v1"
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


def test_hex_shadow_replay_verifier_summary_propagates_blockers_without_authority() -> None:
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


def test_hex_shadow_replay_verifier_summary_blocks_pathy_report_without_leaking_path() -> None:
    report = _valid_verifier_report()
    report["safe_conclusion"] = "review scratch at C:/Python/project2-master/private.json"

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


def test_hex_shadow_replay_blocked_artifact_digest_includes_blocked_reason() -> None:
    runtime_smoke = json.loads(
        json.dumps(build_hexagonal_upgrade_runtime_smoke(ROOT))
    )
    runtime_smoke["ok"] = False
    runtime_smoke["operator_metrics_smoke"]["runtime_contract"][
        "status_code"
    ] = 500
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
    artifact["shadow_plan_summary"]["new_child_cell_ids"].append(
        "thermal.hidden"
    )

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
