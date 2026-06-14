# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.build_v12_counterfactual_eval_coverage_summary import (
    build_counterfactual_eval_coverage_summary,
    render_markdown,
)
from tools.run_v12_a3_counterfactual_axis_proof import (
    build_a3_counterfactual_axis_proof,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "build_v12_counterfactual_eval_coverage_summary.py"
FIXED_NOW = datetime(2026, 6, 8, 3, 10, tzinfo=timezone.utc)


def _run_summary(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_summary_reports_counterfactual_eval_coverage_without_authority() -> None:
    report = build_counterfactual_eval_coverage_summary(now_utc=FIXED_NOW)

    assert report["report_version"] == (
        "wd.v12.counterfactual_eval_coverage_summary.v0"
    )
    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["source"]["axis_id"] == "A3"
    assert report["source"]["writes_applied"] is False
    coverage = report["coverage"]
    assert coverage["variant_count"] == 4
    assert coverage["variants_with_kind_delta"] == 4
    assert coverage["variants_with_gate_delta"] == 4
    assert coverage["runtime_smoke"]["sample_count"] == 24
    assert coverage["runtime_smoke"]["same_sample_set"] is True
    assert coverage["runtime_smoke"]["deterministic"] is True
    assert coverage["runtime_smoke"]["runtime_authority_granted"] is False
    assert coverage["runtime_smoke"]["external_writes_applied"] is False
    assert coverage["runtime_smoke"]["payload_fields_exported"] is False
    assert coverage["runtime_smoke"]["raw_fields_exported"] is False
    # Cleared by the default-proof ephemeral receipt binding.
    assert coverage["receipt_chain_verified"] is True
    assert coverage["receipt_bound_stored_consensus_replay"] is True
    assert (
        "bind the summary to a verified receipt bundle"
        not in report["next_eval_targets"]
    )
    assert (
        "make stored-consensus replay receipt-bound in the default proof"
        not in report["next_eval_targets"]
    )
    assert (
        "add a gate-delta variant so every variant changes actual_gate"
        not in report["next_eval_targets"]
    )
    # Cleared by the second runtime-condition sample family.
    assert (
        "add a second runtime-condition sample family"
        not in report["next_eval_targets"]
    )
    assert coverage["runtime_sample_family_count"] == 2
    families = coverage["runtime_sample_families"]
    assert [f["sample_family"] for f in families] == [
        "scalar_unit_conversion_24_same_sample_set",
        "scalar_linear_scaling_24_candidate_oracle_match",
    ]
    assert all(f["ok"] is True for f in families)
    assert all(f["sample_count"] == 24 for f in families)
    # The second family carries a non-neutral oracle-agreement direction.
    assert families[0]["improvement_count"] == 0
    assert families[0]["neutral_divergence_count"] == 24
    assert families[1]["improvement_count"] == 24
    assert families[1]["regression_count"] == 0
    assert families[1]["neutral_divergence_count"] == 0
    assert (
        "add a sample family with a non-neutral oracle-agreement direction"
        not in report["next_eval_targets"]
    )
    authority = report["authority_boundary"]
    assert authority == {
        "read_only_summary": True,
        "runtime_authority": False,
        "promotion_authority": False,
        "scheduler_authority": False,
        "bridge_write_authority": False,
        "network_authority": False,
        "storage_write_authority": False,
        "solver_execution_authority": False,
        "external_writes_applied": False,
    }


def test_summary_fails_closed_when_source_applied_writes() -> None:
    source = build_a3_counterfactual_axis_proof(now_utc=FIXED_NOW)
    source["writes_applied"] = True

    report = build_counterfactual_eval_coverage_summary(
        now_utc=FIXED_NOW,
        a3_report=source,
    )

    assert report["ok"] is False
    assert "writes_applied_not_false" in report["blockers"]


def test_summary_fails_closed_when_a_smoke_family_is_not_ok() -> None:
    source = build_a3_counterfactual_axis_proof(now_utc=FIXED_NOW)
    source["runtime_condition_replay_smoke_families"][1]["ok"] = False

    report = build_counterfactual_eval_coverage_summary(
        now_utc=FIXED_NOW,
        a3_report=source,
    )

    assert report["ok"] is False
    assert (
        "runtime_smoke_family_not_ok:scalar_linear_scaling_24_candidate_oracle_match"
        in report["blockers"]
    )


def test_summary_backcompat_single_family_source_keeps_expansion_target() -> None:
    """An older A3 report without the families field falls back to the
    primary smoke as a single family, so the expansion target stays open."""
    source = build_a3_counterfactual_axis_proof(now_utc=FIXED_NOW)
    del source["runtime_condition_replay_smoke_families"]

    report = build_counterfactual_eval_coverage_summary(
        now_utc=FIXED_NOW,
        a3_report=source,
    )

    assert report["coverage"]["runtime_sample_family_count"] == 1
    assert (
        "add a second runtime-condition sample family"
        in report["next_eval_targets"]
    )
    # Single neutral family -> the direction-coverage target fires too.
    assert (
        "add a sample family with a non-neutral oracle-agreement direction"
        in report["next_eval_targets"]
    )


def test_summary_fails_closed_on_runtime_authority_tamper() -> None:
    source = build_a3_counterfactual_axis_proof(now_utc=FIXED_NOW)
    source["runtime_condition_replay_smoke"][
        "runtime_authority_granted"
    ] = True

    report = build_counterfactual_eval_coverage_summary(
        now_utc=FIXED_NOW,
        a3_report=source,
    )

    assert report["ok"] is False
    assert "runtime_smoke_runtime_authority_not_false" in report["blockers"]


def test_markdown_carries_next_targets_and_authority_boundary() -> None:
    report = build_counterfactual_eval_coverage_summary(now_utc=FIXED_NOW)

    markdown = render_markdown(report)

    assert "# V12 Counterfactual-Eval Coverage Summary" in markdown
    assert "variants: `4/3`" in markdown
    assert "runtime samples: `24/20`" in markdown
    assert "runtime sample families: `2`" in markdown
    assert "runtime authority: `false`" in markdown
    assert "promotion authority: `false`" in markdown
    assert "bridge write authority: `false`" in markdown
    assert (
        "add a gate-delta variant so every variant changes actual_gate"
        not in markdown
    )
    assert "add a second runtime-condition sample family" not in markdown
    assert "make stored-consensus replay receipt-bound in the default proof" not in markdown


def test_cli_json_smoke() -> None:
    completed = _run_summary("--now", "2026-06-08T03:10:00Z", "--json")

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["coverage"]["runtime_smoke"]["sample_count"] == 24
    assert payload["authority_boundary"]["network_authority"] is False
