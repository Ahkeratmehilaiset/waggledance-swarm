# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.run_autogrowth_multi_intent_receipt_smoke import (
    AXIS_ID,
    CHAIN_ID,
    CLAIM_LABEL,
    DEFAULT_INTENT_COUNT,
    REPORT_VERSION,
    build_autogrowth_multi_intent_receipt_smoke,
)
from tools.verify_magma_receipt import verify_manifest
from waggledance.core.magma.canonical import sha256_digest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "run_autogrowth_multi_intent_receipt_smoke.py"
FIXED_NOW = datetime(2026, 5, 23, 14, 0, tzinfo=timezone.utc)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _entry_path(manifest_path: Path, index: int, field: str) -> Path:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return manifest_path.parent / manifest["entries"][index][field]


def _all_json_text(root: Path) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(root.rglob("*.json"))
    )


def test_smoke_emits_chained_receipts_for_six_family_intents(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "multi-intent-smoke"

    report = build_autogrowth_multi_intent_receipt_smoke(
        out_dir=out_dir,
        now_utc=FIXED_NOW,
    )

    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["report_version"] == REPORT_VERSION
    assert report["generated_at_utc"] == "2026-05-23T14:00:00Z"
    assert report["axis_id"] == AXIS_ID
    assert report["claim_label"] == CLAIM_LABEL
    assert report["chain_id"] == CHAIN_ID
    assert report["runtime_path"] == (
        "AutogrowthScheduler.run_until_idle -> LowRiskGrower.grow_from_gap -> "
        "AutoPromotionEngine.evaluate_candidate"
    )
    assert "not long-running production" in report["evidence_scope"]
    assert report["risk_class"] == "local_artifact"
    assert report["intent_count"] == DEFAULT_INTENT_COUNT
    assert report["families_covered"] == [
        "bounded_interpolation",
        "interval_bucket_classifier",
        "linear_arithmetic",
        "lookup_table",
        "scalar_unit_conversion",
        "threshold_rule",
    ]
    assert report["transitions"] == ["auto_promoted"] * DEFAULT_INTENT_COUNT
    assert report["receipt_count"] == DEFAULT_INTENT_COUNT
    assert report["verifier_ok"] is True
    assert report["raw_payload_leak_check"] is True
    assert report["sink_none_preserved"] is True
    assert report["external_effect_authority_change"] is False
    assert report["operator_gate_required"] is False
    assert report["external_writes_applied"] is False
    assert report["local_artifacts_written"] is True
    assert report["receipt_emission_mode"] == "opt_in_disk_bundle_sink"
    assert report["default_sink_required"] is False
    assert report["scheduler_smoke"]["drained_count"] == DEFAULT_INTENT_COUNT
    assert report["scheduler_smoke"]["auto_promoted"] == DEFAULT_INTENT_COUNT
    assert report["scheduler_smoke"]["rejected"] == 0
    assert report["scheduler_smoke"]["errored"] == 0
    assert report["no_sink_scheduler_smoke"]["auto_promoted"] == (
        DEFAULT_INTENT_COUNT
    )
    assert report["no_overclaim_guardrails"] == {
        "not_a_competitor_benchmark": True,
        "no_consensus_grade_promotion": True,
        "no_release_boundary_change": True,
        "claim_label_remains_partial": True,
        "not_production_authority": True,
    }

    manifest_path = Path(report["receipt_manifest"])
    assert manifest_path.exists()
    verifier = verify_manifest(manifest_path)
    assert verifier["ok"] is True
    assert verifier["receipt_count"] == DEFAULT_INTENT_COUNT


def test_smoke_receipts_are_chain_linked(tmp_path: Path) -> None:
    report = build_autogrowth_multi_intent_receipt_smoke(
        out_dir=tmp_path / "multi-intent-smoke",
        now_utc=FIXED_NOW,
    )
    receipt_dir = Path(report["receipt_out_dir"])
    receipts = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(receipt_dir.glob("receipt-*.json"))
    ]

    assert len(receipts) == DEFAULT_INTENT_COUNT
    assert receipts[0]["prev_receipt_hash"] is None
    for previous, current in zip(receipts, receipts[1:]):
        assert current["prev_receipt_hash"] == sha256_digest(previous)


def test_smoke_refuses_existing_out_dir(tmp_path: Path) -> None:
    out_dir = tmp_path / "multi-intent-smoke"
    out_dir.mkdir()

    with pytest.raises(ValueError, match="out_dir must not exist"):
        build_autogrowth_multi_intent_receipt_smoke(
            out_dir=out_dir,
            now_utc=FIXED_NOW,
        )


def test_smoke_rejects_non_positive_intent_count(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="intent_count must be >= 1"):
        build_autogrowth_multi_intent_receipt_smoke(
            out_dir=tmp_path / "multi-intent-smoke",
            intent_count=0,
            now_utc=FIXED_NOW,
        )


def test_smoke_detects_tampered_payload(tmp_path: Path) -> None:
    report = build_autogrowth_multi_intent_receipt_smoke(
        out_dir=tmp_path / "tampered-payload",
        now_utc=FIXED_NOW,
    )
    manifest_path = Path(report["receipt_manifest"])
    payload_path = _entry_path(manifest_path, 0, "payload")
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["decision"] = "tampered"
    payload_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    verification = verify_manifest(manifest_path)

    assert verification["ok"] is False
    assert "entry 1: canonical_payload_digest mismatch" in verification["errors"]


def test_smoke_does_not_leak_private_markers(tmp_path: Path) -> None:
    out_dir = tmp_path / "multi-intent-smoke"

    build_autogrowth_multi_intent_receipt_smoke(
        out_dir=out_dir,
        now_utc=FIXED_NOW,
    )

    emitted_text = _all_json_text(out_dir)
    assert "private autogrowth" not in emitted_text
    assert "DO_NOT_LEAK" not in emitted_text


def test_cli_json_reports_multi_intent_smoke(tmp_path: Path) -> None:
    out_dir = tmp_path / "multi-intent-cli-smoke"

    result = _run(
        "--json",
        "--out-dir",
        str(out_dir),
        "--now",
        "2026-05-23T14:00:00Z",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["axis_id"] == AXIS_ID
    assert payload["claim_label"] == CLAIM_LABEL
    assert payload["transitions"] == ["auto_promoted"] * DEFAULT_INTENT_COUNT
    assert payload["receipt_count"] == DEFAULT_INTENT_COUNT
    assert payload["sink_none_preserved"] is True
    assert "DO_NOT_LEAK" not in result.stdout


def test_cli_refuses_existing_out_dir(tmp_path: Path) -> None:
    out_dir = tmp_path / "multi-intent-cli-existing"
    out_dir.mkdir()

    result = _run("--out-dir", str(out_dir))

    assert result.returncode == 1
    assert "out_dir must not exist" in result.stderr
