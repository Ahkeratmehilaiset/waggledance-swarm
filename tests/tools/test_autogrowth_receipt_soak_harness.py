# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from tools.run_autogrowth_multi_intent_receipt_smoke import DEFAULT_INTENT_COUNT
from tools.run_autogrowth_receipt_soak_harness import (
    AXIS_ID,
    CLAIM_LABEL,
    DEFAULT_ROUNDS,
    REPORT_VERSION,
    build_autogrowth_receipt_soak_harness,
)
from tools.verify_magma_receipt import verify_manifest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "run_autogrowth_receipt_soak_harness.py"
FIXED_NOW = datetime(2026, 5, 23, 15, 0, tzinfo=timezone.utc)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _all_json_text(root: Path) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(root.rglob("*.json"))
    )


def test_harness_runs_three_rounds_and_summarizes_stability(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "receipt-soak"

    report = build_autogrowth_receipt_soak_harness(
        out_dir=out_dir,
        now_utc=FIXED_NOW,
    )

    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["report_version"] == REPORT_VERSION
    assert report["generated_at_utc"] == "2026-05-23T15:00:00Z"
    assert report["axis_id"] == AXIS_ID
    assert report["claim_label"] == CLAIM_LABEL
    assert "not release soak evidence" in report["evidence_scope"]
    assert "not long-running production" in report["evidence_scope"]
    assert report["round_count"] == DEFAULT_ROUNDS
    assert report["intent_count_per_round"] == DEFAULT_INTENT_COUNT
    assert report["expected_receipt_count"] == DEFAULT_ROUNDS * DEFAULT_INTENT_COUNT
    assert report["total_receipt_count"] == DEFAULT_ROUNDS * DEFAULT_INTENT_COUNT
    assert report["ok_rounds"] == DEFAULT_ROUNDS
    assert report["failed_rounds"] == 0
    assert report["pass_rate"] == 1.0
    assert report["aggregate_raw_payload_leak_check"] is True
    assert report["families_covered_union"] == [
        "bounded_interpolation",
        "interval_bucket_classifier",
        "linear_arithmetic",
        "lookup_table",
        "scalar_unit_conversion",
        "threshold_rule",
    ]
    assert report["no_overclaim_guardrails"] == {
        "not_release_soak_evidence": True,
        "not_a_competitor_benchmark": True,
        "no_consensus_grade_promotion": True,
        "no_release_boundary_change": True,
        "claim_label_remains_partial": True,
        "not_production_authority": True,
    }

    assert len(report["rounds"]) == DEFAULT_ROUNDS
    for round_report in report["rounds"]:
        assert round_report["ok"] is True
        assert round_report["receipt_count"] == DEFAULT_INTENT_COUNT
        assert round_report["verifier_ok"] is True
        assert round_report["sink_none_preserved"] is True
        assert round_report["raw_payload_leak_check"] is True
        assert round_report["transitions"] == ["auto_promoted"] * DEFAULT_INTENT_COUNT
        assert round_report["scheduler"]["drained_count"] == DEFAULT_INTENT_COUNT
        assert round_report["scheduler"]["auto_promoted"] == DEFAULT_INTENT_COUNT
        assert round_report["scheduler"]["rejected"] == 0
        assert round_report["scheduler"]["errored"] == 0
        assert round_report["no_sink_scheduler"]["auto_promoted"] == (
            DEFAULT_INTENT_COUNT
        )
        assert verify_manifest(Path(round_report["receipt_manifest"]))["ok"] is True

    assert report["stability_metrics"] == {
        "receipt_count_min": DEFAULT_INTENT_COUNT,
        "receipt_count_max": DEFAULT_INTENT_COUNT,
        "drained_count_min": DEFAULT_INTENT_COUNT,
        "drained_count_max": DEFAULT_INTENT_COUNT,
        "auto_promoted_min": DEFAULT_INTENT_COUNT,
        "auto_promoted_max": DEFAULT_INTENT_COUNT,
        "rejected_total": 0,
        "errored_total": 0,
        "verifier_failures": 0,
        "sink_none_failures": 0,
        "raw_payload_leak_failures": 0,
    }
    assert Path(report["report_path"]).exists()
    assert "private autogrowth" not in _all_json_text(out_dir)
    assert "DO_NOT_LEAK" not in _all_json_text(out_dir)


def test_harness_refuses_existing_out_dir(tmp_path: Path) -> None:
    out_dir = tmp_path / "receipt-soak"
    out_dir.mkdir()

    with pytest.raises(ValueError, match="out_dir must not exist"):
        build_autogrowth_receipt_soak_harness(out_dir=out_dir, now_utc=FIXED_NOW)


def test_harness_rejects_non_positive_rounds(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="rounds must be >= 1"):
        build_autogrowth_receipt_soak_harness(
            out_dir=tmp_path / "receipt-soak",
            rounds=0,
            now_utc=FIXED_NOW,
        )


def test_harness_rejects_non_positive_intent_count(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="intent_count must be >= 1"):
        build_autogrowth_receipt_soak_harness(
            out_dir=tmp_path / "receipt-soak",
            intent_count=0,
            now_utc=FIXED_NOW,
        )


def test_harness_blocks_failed_round(tmp_path: Path) -> None:
    def fake_round_builder(
        *,
        out_dir: Path,
        intent_count: int,
        now_utc: datetime,
    ) -> dict[str, Any]:
        out_dir.mkdir()
        if out_dir.name.endswith("002"):
            return {
                "ok": False,
                "generated_at_utc": now_utc.isoformat().replace("+00:00", "Z"),
                "blockers": ["synthetic_round_failure"],
                "intent_count": intent_count,
                "receipt_count": 0,
                "verifier_ok": False,
                "sink_none_preserved": True,
                "raw_payload_leak_check": True,
                "transitions": [],
                "families_covered": [],
                "receipt_manifest": None,
                "scheduler_smoke": {
                    "drained_count": 0,
                    "auto_promoted": 0,
                    "rejected": 1,
                    "errored": 0,
                },
                "no_sink_scheduler_smoke": {
                    "auto_promoted": 0,
                    "rejected": 0,
                    "errored": 0,
                },
            }
        return {
            "ok": True,
            "generated_at_utc": now_utc.isoformat().replace("+00:00", "Z"),
            "blockers": [],
            "intent_count": intent_count,
            "receipt_count": intent_count,
            "verifier_ok": True,
            "sink_none_preserved": True,
            "raw_payload_leak_check": True,
            "transitions": ["auto_promoted"] * intent_count,
            "families_covered": ["scalar_unit_conversion"],
            "receipt_manifest": str(out_dir / "manifest.json"),
            "scheduler_smoke": {
                "drained_count": intent_count,
                "auto_promoted": intent_count,
                "rejected": 0,
                "errored": 0,
            },
            "no_sink_scheduler_smoke": {
                "auto_promoted": intent_count,
                "rejected": 0,
                "errored": 0,
            },
        }

    report = build_autogrowth_receipt_soak_harness(
        out_dir=tmp_path / "receipt-soak",
        rounds=2,
        now_utc=FIXED_NOW,
        round_builder=fake_round_builder,
    )

    assert report["ok"] is False
    assert "round_failures:1" in report["blockers"]
    assert "round_002_failed:synthetic_round_failure" in report["blockers"]
    assert report["failed_rounds"] == 1
    assert report["stability_metrics"]["rejected_total"] == 1


def test_cli_json_reports_soak_harness(tmp_path: Path) -> None:
    out_dir = tmp_path / "receipt-soak-cli"

    result = _run(
        "--json",
        "--out-dir",
        str(out_dir),
        "--rounds",
        "2",
        "--now",
        "2026-05-23T15:00:00Z",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["round_count"] == 2
    assert payload["total_receipt_count"] == 2 * DEFAULT_INTENT_COUNT
    assert payload["axis_id"] == AXIS_ID
    assert payload["claim_label"] == CLAIM_LABEL
    assert payload["no_overclaim_guardrails"]["not_release_soak_evidence"] is True
    assert payload["no_overclaim_guardrails"]["not_production_authority"] is True
    assert "DO_NOT_LEAK" not in result.stdout


def test_cli_refuses_existing_out_dir(tmp_path: Path) -> None:
    out_dir = tmp_path / "receipt-soak-cli-existing"
    out_dir.mkdir()

    result = _run("--out-dir", str(out_dir))

    assert result.returncode == 1
    assert "out_dir must not exist" in result.stderr
