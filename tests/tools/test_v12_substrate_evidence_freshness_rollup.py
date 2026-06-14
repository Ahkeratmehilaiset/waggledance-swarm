# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import copy
import json
from pathlib import Path
import subprocess
import sys

from tools.build_v12_ingredient_coverage_rollup import REPORT_VERSION as ROLLUP_VERSION
from tools.build_v12_substrate_evidence_freshness_rollup import (
    REPORT_VERSION,
    build_v12_substrate_evidence_freshness_rollup,
    render_markdown,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "build_v12_substrate_evidence_freshness_rollup.py"
FIXED_NOW = datetime(2026, 6, 14, 4, 45, tzinfo=timezone.utc)


def _matrix(
    *,
    snapshot_date: str = "2026-05-06",
    audit_date: str = "2026-06-14",
    fresh_for_planning: str = "false",
    status: str = "historical_stale",
    historical: str = "true",
) -> str:
    return f"""# Competitive Evidence Matrix - fixture

**Evidence snapshot date:** {snapshot_date}
**Freshness audit:** {audit_date} read-only audit found stale evidence.
**Freshness metadata:** `snapshot_date={snapshot_date}`; `freshness_audit_date={audit_date}`; `max_age_days=14`; `status={status}`; `fresh_for_planning={fresh_for_planning}`; `priority_rows=G,J,L`; `historical_labels_until_refreshed={historical}`.

## Axes

### G. 10,000-solver capability scale

* **Label:** **MEASURED this session.** **PROVEN** that the data path works.

### J. LLM / MoE fallback as a hybrid

* **Label:** **INFERRED** for architecture; **MEASURED-LOCAL-OLLAMA-PANEL** locally.

### L. Edge resource use

* **Label:** **MEASURED** image size; **INFERRED** edge fitness.
"""


def test_rollup_reports_fresh_substrate_and_historical_matrix_staleness() -> None:
    report = build_v12_substrate_evidence_freshness_rollup(
        now_utc=FIXED_NOW,
        matrix_text=_matrix(),
        ingredient_rollup=_rollup(),
    )

    assert report["report_version"] == REPORT_VERSION
    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["freshness"]["substrate_fresh_for_planning"] is True
    assert report["freshness"]["competitive_matrix_fresh_for_planning"] is False
    assert report["competitive_matrix"]["snapshot_age_days"] == 39
    assert report["competitive_matrix"]["historical_stale_allowed"] is True
    assert [row["id"] for row in report["substrate_ingredients"]] == [
        "counterfactual_eval",
        "solver_growth_family",
        "adversarial_corpus",
    ]
    assert all(row["fresh_for_planning"] is True for row in report["substrate_ingredients"])
    assert report["freshness"]["stale_windows"] == [
        {
            "id": "competitive_evidence_matrix",
            "kind": "planning_matrix",
            "age_days": 39,
            "reason": "historical_stale_allowed",
        }
    ]
    assert report["next_substrate_slices"][0] == (
        "refresh_competitive_matrix_priority_rows_from_current_v12_proofs:G,J,L"
    )
    assert report["authority_boundary"]["runtime_authority"] is False
    assert report["authority_boundary"]["bridge_write_authority"] is False


def test_require_fresh_matrix_fails_closed_on_historical_stale_matrix() -> None:
    report = build_v12_substrate_evidence_freshness_rollup(
        now_utc=FIXED_NOW,
        matrix_text=_matrix(),
        ingredient_rollup=_rollup(),
        require_fresh_matrix=True,
    )

    assert report["ok"] is False
    assert "competitive_matrix:snapshot_age_exceeds_max_age" in report["blockers"]
    assert report["next_substrate_slices"] == [
        "fix_v12_substrate_freshness_rollup_blockers_before_claiming_planning_fresh"
    ]


def test_fails_closed_when_ingredient_rollup_is_stale() -> None:
    rollup = _rollup()
    rollup["generated_at_utc"] = "2026-05-01T00:00:00Z"

    report = build_v12_substrate_evidence_freshness_rollup(
        now_utc=FIXED_NOW,
        matrix_text=_matrix(),
        ingredient_rollup=rollup,
    )

    assert report["ok"] is False
    assert "ingredient_rollup_age_exceeds_max_age" in report["blockers"]
    assert report["freshness"]["ingredient_rollup_age_days"] == 44
    assert all(row["fresh_for_planning"] is False for row in report["substrate_ingredients"])


def test_fails_closed_when_substrate_authority_boundary_regresses() -> None:
    rollup = _rollup()
    rollup["ingredients"][0] = copy.deepcopy(rollup["ingredients"][0])
    rollup["ingredients"][0]["authority_boundary_ok"] = False
    rollup["ingredients"][0]["blocker_count"] = 1

    report = build_v12_substrate_evidence_freshness_rollup(
        now_utc=FIXED_NOW,
        matrix_text=_matrix(),
        ingredient_rollup=rollup,
    )

    assert report["ok"] is True
    row = report["substrate_ingredients"][0]
    assert row["id"] == "counterfactual_eval"
    assert row["authority_boundary_ok"] is False
    assert row["fresh_for_planning"] is False
    assert report["freshness"]["substrate_fresh_for_planning"] is False


def test_markdown_is_path_free_and_carries_authority_boundary() -> None:
    report = build_v12_substrate_evidence_freshness_rollup(
        now_utc=FIXED_NOW,
        matrix_text=_matrix(),
        ingredient_rollup=_rollup(),
    )

    markdown = render_markdown(report)
    encoded = json.dumps(report, sort_keys=True) + markdown

    assert "# V12 Substrate Evidence Freshness Rollup" in markdown
    assert "competitive matrix snapshot age: `39` days" in markdown
    assert "runtime authority: `false`" in markdown
    assert "bridge write authority: `false`" in markdown
    assert "tools/" not in encoded
    assert "waggledance/" not in encoded
    assert str(ROOT) not in encoded


def test_cli_json_smoke_on_repo_matrix() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--now",
            "2026-06-14T04:45:00Z",
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
    assert payload["competitive_matrix"]["snapshot_age_days"] == 39
    assert payload["authority_boundary"]["promotion_authority"] is False


def test_cli_rejects_invalid_max_next_slices() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--max-next-slices", "0", "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "--max-next-slices must be >= 1" in completed.stderr


def test_cli_rejects_missing_matrix_without_path_leak(tmp_path: Path) -> None:
    missing = tmp_path / "private" / "missing-matrix.md"

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


def _rollup() -> dict[str, object]:
    return {
        "report_version": ROLLUP_VERSION,
        "generated_at_utc": "2026-06-14T04:45:00Z",
        "ok": True,
        "blockers": [],
        "ingredients": [
            _ingredient(
                "counterfactual_eval",
                "add a gate-delta variant so every variant changes actual_gate",
            ),
            _ingredient(
                "solver_growth_family",
                "solver_growth_family_coverage_balanced_no_lowest_family",
            ),
            _ingredient(
                "adversarial_corpus",
                "maintain_adversarial_corpus_maturity_floor",
            ),
            _ingredient(
                "memory_palace_shortcut_runtime_design",
                "operator_authorized_shadow_replay_design_fixture_only",
            ),
        ],
    }


def _ingredient(ingredient_id: str, next_slice: str) -> dict[str, object]:
    return {
        "id": ingredient_id,
        "ok": True,
        "authority_boundary_ok": True,
        "blocker_count": 0,
        "recommended_next_slice": next_slice,
    }
