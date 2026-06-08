# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.build_v12_solver_growth_family_coverage_summary import (
    build_solver_growth_family_coverage_summary,
    render_markdown,
)
from tools.run_v12_a4_solver_growth_axis_proof import (
    build_a4_solver_growth_axis_proof,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "build_v12_solver_growth_family_coverage_summary.py"


def _fixed_now() -> datetime:
    return datetime(2026, 6, 8, 1, 58, tzinfo=timezone.utc)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _source_report() -> dict:
    return build_a4_solver_growth_axis_proof(now_utc=_fixed_now())


def test_builds_solver_growth_family_coverage_summary() -> None:
    summary = build_solver_growth_family_coverage_summary(
        now_utc=_fixed_now(),
        a4_report=_source_report(),
    )

    assert summary["ok"] is True
    assert summary["blockers"] == []
    assert summary["source"]["axis_id"] == "A4"
    assert summary["coverage"]["families_covered"] == 6
    assert summary["coverage"]["dispatch_success_count"] == 18
    assert summary["coverage"]["weakest_family_count"] == 3
    assert summary["portfolio_pressure"]["registered_solver_count"] == 6
    assert summary["portfolio_pressure"]["rejected_registration_count"] == 8
    assert summary["portfolio_pressure"]["candidate_total"] == 14
    assert summary["growth_targets"][0]["reason"] == "tie_for_lowest_dispatch_coverage"
    assert summary["authority_boundary"]["read_only_summary"] is True
    assert summary["authority_boundary"]["runtime_authority"] is False
    assert summary["authority_boundary"]["promotion_authority"] is False
    assert summary["authority_boundary"]["bridge_write_authority"] is False


def test_summary_is_path_free() -> None:
    summary = build_solver_growth_family_coverage_summary(
        now_utc=_fixed_now(),
        a4_report=_source_report(),
    )

    encoded = json.dumps(summary, sort_keys=True)
    assert "tools/" not in encoded
    assert "waggledance/" not in encoded
    assert "evidence_sources" not in encoded


def test_fail_closed_on_authority_guardrail_tamper() -> None:
    source = deepcopy(_source_report())
    source["no_overclaim_guardrails"]["does_not_touch_production_control_plane"] = False

    summary = build_solver_growth_family_coverage_summary(
        now_utc=_fixed_now(),
        a4_report=source,
    )

    assert summary["ok"] is False
    assert (
        "guardrail_does_not_touch_production_control_plane_not_true"
        in summary["blockers"]
    )
    assert summary["authority_boundary"]["runtime_authority"] is False
    assert summary["authority_boundary"]["scheduler_authority"] is False
    assert summary["recommended_next_slice"] == (
        "fix_source_a4_proof_or_guardrail_blockers_before_growth_planning"
    )


def test_fail_closed_when_family_dispatch_below_minimum() -> None:
    source = deepcopy(_source_report())
    source["dispatch"]["per_family_dispatch_counts"]["lookup_table"] = 1

    summary = build_solver_growth_family_coverage_summary(
        now_utc=_fixed_now(),
        min_dispatch_per_family=3,
        a4_report=source,
    )

    assert summary["ok"] is False
    assert "family_dispatch_below_minimum" in summary["blockers"]
    assert summary["growth_targets"][0]["family"] == "lookup_table"
    assert summary["growth_targets"][0]["reason"] == "below_minimum_dispatch_coverage"


def test_render_markdown_carries_authority_boundary() -> None:
    summary = build_solver_growth_family_coverage_summary(
        now_utc=_fixed_now(),
        a4_report=_source_report(),
    )

    markdown = render_markdown(summary)

    assert "V12 Solver-Growth Family Coverage Summary" in markdown
    assert "runtime authority: `false`" in markdown
    assert "promotion authority: `false`" in markdown
    assert "bridge write authority: `false`" in markdown
    assert "does not promote solvers" in markdown


def test_cli_json_reports_family_coverage() -> None:
    result = _run("--json", "--now", "2026-06-08T01:58:00Z")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["report_version"] == (
        "wd.v12.solver_growth_family_coverage_summary.v0"
    )
    assert payload["ok"] is True
    assert payload["coverage"]["families_covered"] == 6
    assert payload["authority_boundary"]["runtime_authority"] is False


def test_cli_rejects_invalid_min_dispatch() -> None:
    result = _run("--json", "--min-dispatch-per-family", "0")

    assert result.returncode == 1
    assert "--min-dispatch-per-family must be >= 1" in result.stderr
