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
