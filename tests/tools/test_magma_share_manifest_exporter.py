# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.run_runtime_receipt_emission_proof import (
    build_runtime_receipt_emission_proof,
)
from tools.verify_magma_receipt import verify_manifest
from waggledance.core.magma.share_manifest import (
    EXPORT_REPORT_VERSION,
    MANIFEST_VERSION,
    build_magma_share_manifest,
    validate_magma_share_manifest,
    write_magma_share_manifest_export,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "export_magma_share_manifest.py"
FIXED_NOW = datetime(2026, 5, 28, 8, 0, tzinfo=timezone.utc)
PRIVATE_MARKERS = ("private runtime query", "context secret", "DO_NOT_LEAK")


def _source_manifest(tmp_path: Path) -> Path:
    report = build_runtime_receipt_emission_proof(
        out_dir=tmp_path / "source-proof",
        now_utc=FIXED_NOW,
    )
    return Path(report["receipt_manifest"])


def _all_json_text(root: Path) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(root.rglob("*.json"))
    )


def _export_kwargs(tmp_path: Path, source_manifest: Path) -> dict:
    return {
        "source_manifest_path": source_manifest,
        "out_dir": tmp_path / "share-export",
        "operator_approval_id": "operator:approval:magma-share-fixture",
        "verify_source_manifest": verify_manifest,
        "share_id": "magma:share:fixture:001",
        "producer_agent_id": "codex-lead-1",
        "producer_role": "lead",
        "bridge_event_ref": "bridge:wd-image1-magma-share-export",
        "purpose": "cross_instance_replay",
        "now_utc": FIXED_NOW,
    }


def test_operator_gated_export_writes_payload_free_valid_share_manifest(
    tmp_path: Path,
) -> None:
    source_manifest = _source_manifest(tmp_path)

    report = write_magma_share_manifest_export(
        **_export_kwargs(tmp_path, source_manifest)
    )

    share_manifest_path = Path(report["share_manifest"])
    share_manifest = json.loads(share_manifest_path.read_text(encoding="utf-8"))
    assert report["report_version"] == EXPORT_REPORT_VERSION
    assert report["ok"] is True
    assert report["operator_gate_required"] is True
    assert report["operator_gate_satisfied"] is True
    assert report["operator_approval_id_recorded"] is False
    assert report["runtime_export_enabled"] is False
    assert report["default_runtime_receipt_emission_changed"] is False
    assert report["payload_files_exported"] == 0
    assert share_manifest["manifest_version"] == MANIFEST_VERSION
    assert share_manifest["created_at_utc"] == "2026-05-28T08:00:00Z"
    assert share_manifest["runtime_export_enabled"] is False
    assert share_manifest["export_policy"]["payload_visibility"] == "no_payload"
    assert share_manifest["artifact_counts"] == {
        "entries": 1,
        "evaluation_results": 1,
        "payload_files": 0,
        "receipts": 1,
    }
    assert set(share_manifest["forbidden_material_absent"]) == {
        "raw_payload",
        "replacement_map",
        "raw_context",
        "raw_solver_output",
        "raw_query_digest",
    }
    entry = share_manifest["entries"][0]
    assert entry["receipt_digest"].startswith("sha256:")
    assert entry["evaluation_result_digest"].startswith("sha256:")
    assert "canonical_payload_digest" not in entry
    assert "raw_payload" not in entry
    assert "raw_context" not in entry

    validate_magma_share_manifest(share_manifest)
    exported_text = _all_json_text(Path(report["share_manifest"]).parent)
    assert not any(marker in exported_text for marker in PRIVATE_MARKERS)


def test_export_refuses_missing_operator_approval_without_writing(
    tmp_path: Path,
) -> None:
    source_manifest = _source_manifest(tmp_path)
    kwargs = _export_kwargs(tmp_path, source_manifest)
    kwargs["operator_approval_id"] = ""

    with pytest.raises(ValueError, match="operator_approval_id"):
        write_magma_share_manifest_export(**kwargs)

    assert not kwargs["out_dir"].exists()


def test_export_fail_closes_when_source_receipt_bundle_is_tampered(
    tmp_path: Path,
) -> None:
    source_manifest = _source_manifest(tmp_path)
    manifest = json.loads(source_manifest.read_text(encoding="utf-8"))
    evaluation_path = source_manifest.parent / manifest["entries"][0][
        "evaluation_result"
    ]
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    evaluation["verdict"] = "review"
    evaluation_path.write_text(
        json.dumps(evaluation, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    kwargs = _export_kwargs(tmp_path, source_manifest)

    with pytest.raises(ValueError, match="source receipt manifest verification failed"):
        write_magma_share_manifest_export(**kwargs)

    assert not kwargs["out_dir"].exists()


def test_share_manifest_validation_rejects_bad_date_and_count_drift(
    tmp_path: Path,
) -> None:
    source_manifest = _source_manifest(tmp_path)
    manifest = build_magma_share_manifest(
        source_manifest_path=source_manifest,
        verify_source_manifest=verify_manifest,
        share_id="magma:share:fixture:002",
        producer_agent_id="codex-lead-1",
        producer_role="lead",
        bridge_event_ref="bridge:wd-image1-magma-share-export",
        purpose="peer_review",
        now_utc=FIXED_NOW,
    )

    bad_date = dict(manifest)
    bad_date["created_at_utc"] = "not-a-date"
    with pytest.raises(ValueError, match="created_at_utc"):
        validate_magma_share_manifest(bad_date)

    bad_count = json.loads(json.dumps(manifest))
    bad_count["artifact_counts"]["entries"] = 2
    with pytest.raises(ValueError, match="artifact_counts.entries"):
        validate_magma_share_manifest(bad_count)


def test_cli_json_export_is_operator_gated_and_redacts_payload_markers(
    tmp_path: Path,
) -> None:
    source_manifest = _source_manifest(tmp_path)
    out_dir = tmp_path / "cli-share-export"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--json",
            "--source-manifest",
            str(source_manifest),
            "--out-dir",
            str(out_dir),
            "--operator-approval-id",
            "operator:approval:magma-share-cli",
            "--share-id",
            "magma:share:fixture:cli",
            "--producer-agent",
            "codex-lead-1",
            "--producer-role",
            "lead",
            "--bridge-event-ref",
            "bridge:wd-image1-magma-share-export",
            "--purpose",
            "cross_instance_replay",
            "--now",
            "2026-05-28T08:00:00Z",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["operator_gate_satisfied"] is True
    assert payload["runtime_export_enabled"] is False
    assert Path(payload["share_manifest"]).exists()
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)
    assert not any(marker in _all_json_text(out_dir) for marker in PRIVATE_MARKERS)
