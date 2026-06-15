# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.build_competitive_priority_row_refresh_plan import (
    REPORT_VERSION,
    build_priority_row_refresh_plan,
    render_markdown,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "build_competitive_priority_row_refresh_plan.py"
FIXED_NOW = datetime(2026, 6, 15, 9, 25, tzinfo=timezone.utc)


def _matrix(
    *,
    snapshot_date: str = "2026-05-06",
    audit_date: str = "2026-06-15",
    priority_rows: str = "G,J,L",
    priority_snapshot_date: str = "2026-05-06",
    priority_fresh_for_planning: str = "false",
) -> str:
    return f"""# Competitive Evidence Matrix - fixture

**Evidence snapshot date:** {snapshot_date}
**Freshness audit:** {audit_date} read-only audit found stale evidence.
**Freshness metadata:** `snapshot_date={snapshot_date}`; `freshness_audit_date={audit_date}`; `max_age_days=14`; `status=historical_stale`; `fresh_for_planning=false`; `priority_rows={priority_rows}`; `priority_rows_snapshot_date={priority_snapshot_date}`; `priority_rows_freshness_audit_date={audit_date}`; `priority_rows_fresh_for_planning={priority_fresh_for_planning}`; `historical_labels_until_refreshed=true`.

## Axes

### G. 10,000-solver capability scale

* **Label:** **MEASURED this session.** **PROVEN** that the data path works.

### J. LLM / MoE fallback as a hybrid

* **Label:** **INFERRED** for the architecture; **MEASURED-LOCAL-OLLAMA-PANEL** locally.

### L. Edge resource use

* **Label:** **MEASURED** image size; **INFERRED** edge fitness.
"""


def test_current_priority_rows_require_fresh_evidence_before_upgrade() -> None:
    report = build_priority_row_refresh_plan(
        matrix_text=_matrix(),
        now_utc=FIXED_NOW,
    )

    assert report["report_version"] == REPORT_VERSION
    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["freshness"]["priority_rows"] == ["G", "J", "L"]
    assert report["freshness"]["priority_rows_snapshot_age_days"] == 40
    assert report["freshness"]["priority_rows_fresh_for_planning"] is False
    assert report["freshness"]["ready_to_update_priority_metadata"] is False
    assert [row["row"] for row in report["priority_row_refresh_plan"]] == [
        "G",
        "J",
        "L",
    ]
    assert {
        row["admission_state"] for row in report["priority_row_refresh_plan"]
    } == {"refresh_required"}
    assert all(
        "current_priority_row_evidence_required" in row["blockers"]
        for row in report["priority_row_refresh_plan"]
    )
    assert report["authority_boundary"]["benchmark_execution_authority"] is False
    assert report["authority_boundary"]["matrix_label_upgrade_authority"] is False
    assert report["next_step"] == "run_or_claim_refresh_evidence_for_priority_rows_G_J_L"


def test_require_priority_fresh_fails_closed_on_stale_priority_rows() -> None:
    report = build_priority_row_refresh_plan(
        matrix_text=_matrix(),
        now_utc=FIXED_NOW,
        require_priority_fresh=True,
    )

    assert report["ok"] is False
    assert "priority_rows_not_fresh_for_planning" in report["blockers"]
    assert report["next_step"] == "fix_priority_refresh_plan_blockers_before_execution"


def test_fresh_priority_metadata_is_admitted_but_does_not_grant_authority() -> None:
    report = build_priority_row_refresh_plan(
        matrix_text=_matrix(
            priority_snapshot_date="2026-06-10",
            priority_fresh_for_planning="true",
        ),
        now_utc=FIXED_NOW,
    )

    assert report["ok"] is True
    assert report["freshness"]["priority_rows_snapshot_age_days"] == 5
    assert report["freshness"]["priority_rows_fresh_for_planning"] is True
    assert report["freshness"]["ready_to_update_priority_metadata"] is True
    assert {
        row["admission_state"] for row in report["priority_row_refresh_plan"]
    } == {"fresh_metadata_admitted"}
    assert report["authority_boundary"]["priority_freshness_upgrade_authority"] is False
    assert report["next_step"] == (
        "prepare_matrix_priority_metadata_update_from_fresh_artifacts"
    )


def test_unknown_priority_row_gets_fail_closed_blocker() -> None:
    report = build_priority_row_refresh_plan(
        matrix_text=_matrix(priority_rows="G,Z"),
        now_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert "priority_rows_without_refresh_recipe:Z" in report["blockers"]
    assert "competitive_matrix:priority_rows_not_evidence_bearing:Z" in report[
        "blockers"
    ]


def test_markdown_is_repo_relative_and_carries_agent_split() -> None:
    report = build_priority_row_refresh_plan(
        matrix_text=_matrix(),
        now_utc=FIXED_NOW,
    )
    markdown = render_markdown(report)
    encoded = json.dumps(report, sort_keys=True) + markdown

    assert "# Competitive Priority Row Refresh Plan" in markdown
    assert "G. 10,000-solver capability scale" in markdown
    assert "priority rows fresh for planning: `false`" in markdown
    assert "benchmark execution authority: `false`" in markdown
    assert "tools/run_solver_scale_proof.py" in encoded
    assert str(ROOT) not in encoded
    assert report["agent_split"]["implementation"] == {
        "codex-lead-1": ["G", "L"],
        "codex-tools-1": ["J"],
    }


def test_cli_json_smoke_on_repo_matrix() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--now",
            "2026-06-15T09:25:00Z",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["freshness"]["priority_rows"] == ["G", "J", "L"]
    assert payload["freshness"]["priority_rows_snapshot_age_days"] == 40
    assert payload["freshness"]["ready_to_update_priority_metadata"] is False


def test_cli_require_priority_fresh_exits_nonzero() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--now",
            "2026-06-15T09:25:00Z",
            "--require-priority-fresh",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert "priority_rows_not_fresh_for_planning" in payload["blockers"]


def test_cli_rejects_missing_matrix_without_path_leak(tmp_path: Path) -> None:
    missing = tmp_path / "private" / "missing.md"

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--matrix", str(missing), "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert "matrix file could not be read" in completed.stderr
    assert str(missing) not in completed.stderr
    assert str(tmp_path) not in completed.stderr
