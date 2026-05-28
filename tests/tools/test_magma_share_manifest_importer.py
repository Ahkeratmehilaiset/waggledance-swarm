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
from waggledance.core.magma.share_manifest import (
    IMPORT_REPORT_VERSION,
    build_magma_share_manifest_import_report,
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
    evaluation_path = source_manifest.parent / manifest["entries"][0][
        "evaluation_result"
    ]
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


def test_cli_json_import_is_no_authority_and_redacts_payload_markers(
    tmp_path: Path,
) -> None:
    share_manifest, source_manifest = _share_export(tmp_path)

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
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["no_authority_import"] is True
    assert payload["runtime_authority_granted"] is False
    assert payload["payload_files_imported"] == 0
    assert str(tmp_path) not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)
    assert not any(marker in _all_json_text(tmp_path / "share-export") for marker in PRIVATE_MARKERS)
