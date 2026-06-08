# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.run_v12_memory_palace_shortcut_promotion_candidates import (
    build_memory_palace_shortcut_promotion_candidate_report,
)
from tools.run_v12_memory_palace_shortcut_runtime_promotion_design import (
    CLAIM_LABEL,
    REPORT_VERSION,
    build_memory_palace_shortcut_runtime_promotion_design,
    render_markdown,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "tools"
    / "run_v12_memory_palace_shortcut_runtime_promotion_design.py"
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_build_design_report_requires_operator_gate_without_action() -> None:
    report = build_memory_palace_shortcut_runtime_promotion_design(
        now_utc=_fixed_now(),
    )

    assert report["ok"] is True
    assert report["report_version"] == REPORT_VERSION
    assert report["claim_label"] == CLAIM_LABEL
    assert report["source_verification_ok"] is True
    assert report["design_summary"] == {
        "source_candidate_count": 2,
        "designable_source_candidate_count": 2,
        "runtime_promotion_design_count": 2,
        "top_design_target": "room.research.pathology",
    }
    designs = report["runtime_promotion_designs"]
    assert [design["target_node_id"] for design in designs] == [
        "room.research.pathology",
        "room.system.statistics",
    ]
    for design in designs:
        assert design["operator_authorization_required"] is True
        assert design["manual_review_required"] is True
        assert "operator_authorization" in design["required_operator_controls"]
        assert "no_gate_skip" in design["required_preflight_checks"]
        assert design["design_status"] == (
            "operator_authorization_required_before_runtime_promotion"
        )
        assert design["promotion_action_allowed"] is False
        assert design["runtime_route_changed"] is False
        assert design["gate_skip_performed"] is False
        assert design["approval_granted"] is False


def test_authority_boundary_remains_design_only() -> None:
    report = build_memory_palace_shortcut_runtime_promotion_design(
        now_utc=_fixed_now(),
    )
    boundary = report["authority_boundary"]

    for key in (
        "source_candidate_report_ok",
        "source_candidate_verification_ok",
        "design_only",
        "manual_review_required",
        "operator_authorization_required_for_runtime_promotion",
        "all_design_rows_action_free",
    ):
        assert boundary[key] is True
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
        "approval_granted",
        "release_decision_made",
        "automatic_release_decision",
    ):
        assert boundary[key] is False


def test_bad_source_report_blocks_without_echoing_design_rows() -> None:
    source = build_memory_palace_shortcut_promotion_candidate_report(
        now_utc=_fixed_now(),
    )
    source["promotion_candidates"][0]["promotion_action_allowed"] = True
    source["promotion_candidates"][0]["target_node_id"] = (
        r"C:\operator\private\palace.json"
    )

    report = build_memory_palace_shortcut_runtime_promotion_design(
        now_utc=_fixed_now(),
        source_report=source,
    )
    encoded = json.dumps(report, sort_keys=True)

    assert report["ok"] is False
    assert "source_candidate_verification_not_ok" in report["blockers"]
    assert report["runtime_promotion_designs"] == []
    assert r"C:\operator\private\palace.json" not in encoded
    assert "palace.json" not in encoded


def test_explicit_empty_source_report_fails_closed() -> None:
    report = build_memory_palace_shortcut_runtime_promotion_design(
        now_utc=_fixed_now(),
        source_report={},
    )

    assert report["ok"] is False
    assert report["source_verification_ok"] is False
    assert report["source_report_version"] == ""
    assert report["runtime_promotion_designs"] == []
    assert "source_candidate_report_not_ok" in report["blockers"]
    assert "source_candidate_report_not_verified" in report["blockers"]


def test_explicit_empty_source_verification_fails_closed() -> None:
    report = build_memory_palace_shortcut_runtime_promotion_design(
        now_utc=_fixed_now(),
        source_verification={},
    )

    assert report["ok"] is False
    assert report["source_verification_ok"] is False
    assert report["source_verification_version"] == ""
    assert report["runtime_promotion_designs"] == []
    assert "source_candidate_verification_not_ok" in report["blockers"]
    assert "source_candidate_report_not_verified" in report["blockers"]


def test_strict_threshold_blocks_design_without_granting_action() -> None:
    report = build_memory_palace_shortcut_runtime_promotion_design(
        now_utc=_fixed_now(),
        min_rank_score=0.9,
    )

    assert report["ok"] is False
    assert report["design_summary"]["runtime_promotion_design_count"] == 0
    assert "no_operator_gated_runtime_design_candidates" in report["blockers"]
    assert report["authority_boundary"]["promotion_action_allowed"] is False


def test_render_markdown_reports_design_status_and_caveats() -> None:
    report = build_memory_palace_shortcut_runtime_promotion_design(
        now_utc=_fixed_now(),
    )

    markdown = render_markdown(report)

    assert "V12 Memory Palace Shortcut Runtime-Promotion Design" in markdown
    assert "ok: `true`" in markdown
    assert "runtime_promotion_design_count: `2`" in markdown
    assert "`room.research.pathology`" in markdown
    assert "operator-gated runtime-promotion design rows" in markdown
    assert "This does not promote a route" in markdown


def test_cli_json_reports_design_rows() -> None:
    result = _run("--json", "--now", "2026-06-08T07:30:00Z")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["generated_at_utc"] == "2026-06-08T07:30:00Z"
    assert payload["design_summary"]["runtime_promotion_design_count"] == 2
    assert payload["runtime_promotion_designs"][0]["target_node_id"] == (
        "room.research.pathology"
    )
    assert (
        payload["runtime_promotion_designs"][0]["promotion_action_allowed"]
        is False
    )


def test_cli_default_outputs_markdown() -> None:
    result = _run("--now", "2026-06-08T07:30:00Z")

    assert result.returncode == 0, result.stderr
    assert "# V12 Memory Palace Shortcut Runtime-Promotion Design" in result.stdout
    assert "claim_label: `DESIGN_ONLY_OPERATOR_GATED_RUNTIME_PROMOTION`" in (
        result.stdout
    )
    assert "This does not promote a route" in result.stdout


def test_cli_rejects_invalid_threshold() -> None:
    result = _run("--json", "--min-rank-score", "1.1")

    assert result.returncode == 1
    assert "--min-rank-score must be between 0 and 1" in result.stderr


def _fixed_now() -> datetime:
    return datetime(2026, 6, 8, 7, 30, tzinfo=timezone.utc)
