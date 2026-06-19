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
    build_shadow_subdivision_replay_artifact,
    build_shadow_subdivision_replay_verifier_summary,
    build_shadow_subdivision_replay_verifier_summary_bridge_event_template,
    build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry,
    build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary,
    build_source_snapshot,
    verify_shadow_subdivision_replay_artifact,
    verify_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry,
)
from tools.wd_image1_capability_manifest import (
    build_hexagonal_upgrade_proof,
    build_hexagonal_upgrade_runtime_smoke,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / (
    "build_hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_"
    "index_entry_verification_summary.py"
)


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True).encode("utf-8")


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


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


def _valid_index_entry_verification() -> dict:
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


def test_build_hex_shadow_index_entry_verification_summary_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "hex-index-entry-verification.json"
    report_path.write_bytes(_json_bytes(_valid_index_entry_verification()))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verification-json",
            str(report_path),
            "--reviewer-agent",
            "claude-rco-1",
            "--handoff-ref",
            "bridge:hex-index-entry-verification",
            "--now",
            "2026-05-31T13:15:00Z",
            "--json",
        ],
        check=False,
        capture_output=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    nested = summary[
        "shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification"
    ]
    artifact_id = "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template"
    assert summary["ok"] is True
    assert summary["created_at_utc"] == "2026-05-31T13:15:00Z"
    assert nested["digest_checks"][artifact_id] == "match"
    assert nested["source_contract_check"] == "match"
    assert summary["direct_bridge_write_performed"] is False
    assert summary["runtime_subdivision_authority_granted"] is False
    assert summary["artifact_payloads_included"] is False
    assert summary["local_paths_recorded"] is False
    assert str(tmp_path) not in result.stdout
    assert report_path.name not in result.stdout
    assert str(tmp_path) not in result.stderr


def test_build_hex_shadow_index_entry_verification_summary_cli_rejects_duplicate_key_path_free(
    tmp_path: Path,
) -> None:
    report = _valid_index_entry_verification()
    hidden_prefix = (
        json.dumps(
            {"warnings": ["C:/Python/project2-master/private.json"]},
            sort_keys=True,
        )[:-1]
        + ","
    )
    report_path = tmp_path / "hex-index-entry-verification.json"
    report_path.write_bytes(
        (hidden_prefix + json.dumps(report, sort_keys=True)[1:]).encode("utf-8")
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verification-json",
            str(report_path),
            "--json",
        ],
        check=False,
        capture_output=True,
        encoding="utf-8",
    )

    assert result.returncode == 1
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
    assert report_path.name not in result.stdout
    assert str(tmp_path) not in result.stderr


def test_build_hex_shadow_index_entry_verification_summary_cli_rejects_now_invalid(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "hex-index-entry-verification.json"
    report_path.write_bytes(_json_bytes(_valid_index_entry_verification()))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verification-json",
            str(report_path),
            "--now",
            "not-a-date",
            "--json",
        ],
        check=False,
        capture_output=True,
        encoding="utf-8",
    )

    assert result.returncode == 1
    summary = json.loads(result.stdout)
    assert summary["ok"] is False
    assert summary["blockers"] == [
        "shadow_subdivision_replay_verifier_summary_bridge_event_template_"
        "index_entry_verification_summary_failed:now_utc_invalid"
    ]
    assert str(tmp_path) not in result.stdout


def test_build_hex_shadow_index_entry_verification_summary_matches_core_builder() -> (
    None
):
    verification = _valid_index_entry_verification()

    summary = build_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary(
        verification,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:hex-index-entry-verification",
        now_utc=datetime(2026, 5, 31, 13, 15, tzinfo=timezone.utc),
    )

    assert summary["ok"] is True
    assert summary["proof_id"] == (
        "hex_shadow_subdivision_replay_verifier_summary_bridge_event_template_"
        "index_entry_verification_summary_v1"
    )
    assert _canonical_digest(summary).startswith("sha256:")
