# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.run_v12_memory_palace_shortcut_promotion_candidates import (
    CLAIM_LABEL,
    REPORT_VERSION,
    build_memory_palace_shortcut_promotion_candidate_report,
    render_markdown,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "run_v12_memory_palace_shortcut_promotion_candidates.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_build_report_marks_shortcuts_observable_without_promotion_action() -> None:
    report = build_memory_palace_shortcut_promotion_candidate_report(
        now_utc=_fixed_now(),
    )

    assert report["ok"] is True
    assert report["report_version"] == REPORT_VERSION
    assert report["claim_label"] == CLAIM_LABEL
    assert report["source_report_version"] == "wd.v12.memory_palace_shortcut_proof.v0"
    assert report["source_claim_label"] == "MEASURED_LOCAL_PROJECTION"
    assert report["memory_id"] == "memory.learning.cell_imaging.1"
    assert report["source_of_truth"] == "projection_only"
    assert report["thresholds"] == {
        "min_rank_score": 0.6,
        "min_intermediate_hops_skipped": 2,
    }
    assert report["candidate_summary"] == {
        "source_candidate_count": 2,
        "promotion_observable_count": 2,
        "blocked_count": 0,
        "top_candidate_target": "room.research.pathology",
    }
    candidates = report["promotion_candidates"]
    assert [candidate["target_node_id"] for candidate in candidates] == [
        "room.research.pathology",
        "room.system.statistics",
    ]
    for candidate in candidates:
        assert candidate["rank_score"] == 0.68
        assert candidate["hierarchy_hops"] == 3
        assert candidate["projected_shortcut_hops"] == 1
        assert candidate["intermediate_hops_skipped"] == 2
        assert candidate["promotion_observable"] is True
        assert candidate["promotion_action_allowed"] is False
        assert candidate["candidate_status"] == (
            "observable_pending_operator_design"
        )
        assert candidate["authority_flags_false"] is True
        assert candidate["runtime_route_changed"] is False
        assert candidate["solver_call_performed"] is False
        assert candidate["promotion_performed"] is False


def test_authority_boundary_remains_report_only() -> None:
    report = build_memory_palace_shortcut_promotion_candidate_report(
        now_utc=_fixed_now(),
    )
    boundary = report["authority_boundary"]

    assert boundary["source_proof_ok"] is True
    assert boundary["source_authority_boundary_ok"] is True
    assert boundary["read_side_report_only"] is True
    assert boundary["all_candidates_authority_flags_false"] is True
    for key in (
        "runtime_route_changed",
        "storage_write_performed",
        "bridge_append_performed",
        "solver_call_performed",
        "scheduler_enqueue_performed",
        "promotion_performed",
        "promotion_action_allowed",
        "gate_skip_performed",
        "network_access_performed",
    ):
        assert boundary[key] is False

    guardrails = report["no_overclaim_guardrails"]
    assert guardrails["not_promotion_action"] is True
    assert guardrails["operator_gate_required_for_runtime_promotion"] is True
    assert guardrails["not_gate_skip"] is True


def test_strict_rank_threshold_blocks_candidates_without_granting_action() -> None:
    report = build_memory_palace_shortcut_promotion_candidate_report(
        now_utc=_fixed_now(),
        min_rank_score=0.9,
    )

    assert report["ok"] is False
    assert report["candidate_summary"] == {
        "source_candidate_count": 2,
        "promotion_observable_count": 0,
        "blocked_count": 2,
        "top_candidate_target": "none",
    }
    for candidate in report["promotion_candidates"]:
        assert candidate["promotion_observable"] is False
        assert candidate["promotion_action_allowed"] is False
        assert candidate["candidate_status"] == (
            "blocked_by_threshold_or_authority_boundary"
        )
    assert report["authority_boundary"]["promotion_action_allowed"] is False


def test_render_markdown_reports_candidate_status_and_caveats() -> None:
    report = build_memory_palace_shortcut_promotion_candidate_report(
        now_utc=_fixed_now(),
    )

    markdown = render_markdown(report)

    assert "V12 Memory Palace Shortcut Promotion Candidates" in markdown
    assert "ok: `true`" in markdown
    assert "promotion_observable_count: `2`" in markdown
    assert "`room.research.pathology`" in markdown
    assert "`false`" in markdown
    assert "without loading intermediate hierarchy nodes" in markdown
    assert "This does not promote a route" in markdown
    assert "skip a gate" in markdown


def test_cli_json_reports_promotion_candidates() -> None:
    result = _run("--json", "--now", "2026-06-07T16:00:00Z")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["generated_at_utc"] == "2026-06-07T16:00:00Z"
    assert payload["candidate_summary"]["promotion_observable_count"] == 2
    assert payload["promotion_candidates"][0]["target_node_id"] == (
        "room.research.pathology"
    )
    assert (
        payload["promotion_candidates"][0]["promotion_action_allowed"]
        is False
    )
    assert payload["authority_boundary"]["promotion_action_allowed"] is False


def test_cli_default_outputs_markdown() -> None:
    result = _run("--now", "2026-06-07T16:00:00Z")

    assert result.returncode == 0, result.stderr
    assert "# V12 Memory Palace Shortcut Promotion Candidates" in result.stdout
    assert "claim_label: `MEASURED_LOCAL_PROMOTION_CANDIDATE_REPORT`" in (
        result.stdout
    )
    assert "This does not promote a route" in result.stdout


def test_cli_rejects_invalid_now() -> None:
    result = _run("--json", "--now", "not-a-date")

    assert result.returncode == 1
    assert "--now must be an ISO-8601 UTC timestamp" in result.stderr


def test_cli_rejects_invalid_threshold() -> None:
    result = _run("--json", "--min-rank-score", "1.5")

    assert result.returncode == 1
    assert "--min-rank-score must be between 0 and 1" in result.stderr


def _fixed_now() -> datetime:
    return datetime(2026, 6, 7, 16, 0, tzinfo=timezone.utc)
