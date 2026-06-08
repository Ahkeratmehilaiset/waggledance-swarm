# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import copy
import json
from pathlib import Path
import subprocess
import sys

from tools.build_v12_ingredient_coverage_rollup import (
    MEMORY_PALACE_VERIFICATION_VERSION,
    REPORT_VERSION,
    build_v12_ingredient_coverage_rollup,
    render_markdown,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "build_v12_ingredient_coverage_rollup.py"
FIXED_NOW = datetime(2026, 6, 8, 4, 20, tzinfo=timezone.utc)


def _run_rollup(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_rollup_reports_current_v12_ingredients_without_authority() -> None:
    report = build_v12_ingredient_coverage_rollup(now_utc=FIXED_NOW)

    assert report["report_version"] == REPORT_VERSION
    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["coverage"]["ingredient_count"] == 4
    assert report["coverage"]["ok_ingredient_count"] == 4
    assert report["coverage"]["authority_clean_ingredient_count"] == 4
    assert {row["id"] for row in report["ingredients"]} == {
        "solver_growth_family",
        "counterfactual_eval",
        "adversarial_corpus",
        "memory_palace_shortcut_candidates",
    }
    authority = report["authority_boundary"]
    assert authority["runtime_authority"] is False
    assert authority["promotion_authority"] is False
    assert authority["bridge_write_authority"] is False
    assert authority["receipt_artifact_generation_executed"] is False
    assert authority["external_writes_applied"] is False


def test_rollup_fails_closed_on_authority_boundary_regression() -> None:
    sources = _valid_injected_sources()
    sources["counterfactual_eval"] = copy.deepcopy(sources["counterfactual_eval"])
    sources["counterfactual_eval"]["authority_boundary"]["runtime_authority"] = True

    report = build_v12_ingredient_coverage_rollup(
        now_utc=FIXED_NOW,
        source_reports=sources,
    )

    assert report["ok"] is False
    assert (
        "ingredient_blocked:counterfactual_eval:authority_boundary_not_ok"
        in report["blockers"]
    )
    row = _row(report, "counterfactual_eval")
    assert row["authority_boundary_ok"] is False
    assert row["authority_false_fields_ok"] is False


def test_rollup_fails_closed_on_memory_verification_regression() -> None:
    sources = _valid_injected_sources()
    sources["memory_palace_shortcut_candidates_verification"] = copy.deepcopy(
        sources["memory_palace_shortcut_candidates_verification"]
    )
    sources["memory_palace_shortcut_candidates_verification"]["ok"] = False
    sources["memory_palace_shortcut_candidates_verification"]["blockers"] = [
        "candidate_action_boundary_mismatch",
    ]

    report = build_v12_ingredient_coverage_rollup(
        now_utc=FIXED_NOW,
        source_reports=sources,
    )

    assert report["ok"] is False
    assert (
        "ingredient_blocked:memory_palace_shortcut_candidates:verification_not_ok"
        in report["blockers"]
    )
    assert (
        "ingredient_blocked:memory_palace_shortcut_candidates:"
        "verification_blockers_present"
    ) in report["blockers"]


def test_markdown_is_path_free_and_mentions_authority_boundary() -> None:
    report = build_v12_ingredient_coverage_rollup(now_utc=FIXED_NOW)

    markdown = render_markdown(report)

    assert "# V12 Ingredient Coverage Rollup" in markdown
    assert "ingredients ok: `4/4`" in markdown
    assert "runtime authority: `false`" in markdown
    assert "bridge write authority: `false`" in markdown
    assert str(ROOT) not in markdown


def test_cli_json_smoke() -> None:
    completed = _run_rollup("--now", "2026-06-08T04:20:00Z", "--json")

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["coverage"]["ingredient_count"] == 4
    assert payload["authority_boundary"]["scheduler_authority"] is False


def test_cli_rejects_invalid_min_ingredients() -> None:
    completed = _run_rollup("--min-ingredients", "0", "--json")

    assert completed.returncode == 1
    assert "--min-ingredients must be >= 1" in completed.stderr


def _row(report: dict[str, object], ingredient_id: str) -> dict[str, object]:
    for row in report["ingredients"]:
        if row["id"] == ingredient_id:
            return row
    raise AssertionError(f"missing row {ingredient_id}")


def _valid_injected_sources() -> dict[str, dict[str, object]]:
    common_boundary = {
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
    solver_boundary = dict(common_boundary)
    solver_boundary.pop("external_writes_applied")
    solver_boundary["operator_gate_required_for_runtime_promotion"] = True
    memory_verification = {
        "ok": True,
        "verification_version": MEMORY_PALACE_VERIFICATION_VERSION,
        "blockers": [],
        "read_side_report_only": True,
        "manual_review_required": True,
        "operator_gate_required_for_runtime_promotion": True,
        "runtime_route_changed": False,
        "storage_write_performed": False,
        "bridge_append_performed": False,
        "solver_call_performed": False,
        "scheduler_enqueue_performed": False,
        "promotion_performed": False,
        "promotion_action_allowed": False,
        "gate_skip_performed": False,
        "network_access_performed": False,
    }
    return {
        "solver_growth_family": {
            "report_version": "wd.v12.solver_growth_family_coverage_summary.v0",
            "ok": True,
            "blockers": [],
            "authority_boundary": solver_boundary,
            "recommended_next_slice": "expand_solver_growth_cases",
        },
        "counterfactual_eval": {
            "report_version": "wd.v12.counterfactual_eval_coverage_summary.v0",
            "ok": True,
            "blockers": [],
            "authority_boundary": dict(common_boundary),
            "next_eval_targets": ["add second runtime-condition sample family"],
        },
        "adversarial_corpus": {
            "report_version": "wd.v12.adversarial_corpus_maturity_summary.v0",
            "ok": True,
            "blockers": [],
            "authority_boundary": dict(common_boundary),
            "maturation_targets": [
                {"kind": "defect_type", "name": "path_escape", "count": 2}
            ],
        },
        "memory_palace_shortcut_candidates": {
            "report_version": (
                "wd.v12.memory_palace_shortcut_promotion_candidates.v0"
            ),
            "ok": True,
            "blockers": [],
            "authority_boundary": {},
        },
        "memory_palace_shortcut_candidates_verification": memory_verification,
    }
