# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.build_wd_p4_sprint_truth_dashboard import (
    build_wd_p4_sprint_truth_dashboard,
    main,
    render_markdown,
)


SCRIPT = Path("tools/build_wd_p4_sprint_truth_dashboard.py")
FIXED_NOW = datetime(2026, 6, 29, 22, 40, tzinfo=timezone.utc)
HEAD_1 = "d9acc02a399fea44c4ffc368ba2e8983e29f471f"
HEAD_2 = "89b4d4472da92778ed77d992e2627a05df4b5410"
MERGE_2 = "56119e261d7b9343edbf780d105e1675c70506ad"
HEAD_4 = "4ebabd4efdba2d383f2ae2ad71156d1acd9e7ad2"
HEAD_6 = "d24a7c023dec9ade99891e021e2175806489ea29"


def _statuses() -> list[dict]:
    return [
        {
            "seed": 1,
            "title": "RCO wake/liveness preflight",
            "owner": "codex-lead-1",
            "state": "merged",
            "pr": 1434,
            "head": HEAD_1,
            "ci": "green",
            "gate": "autonomous_merge_observed",
            "readiness_points": 2,
        },
        {
            "seed": 2,
            "title": "Standing-sign receipt replay canary",
            "owner": "codex-lead-1",
            "state": "merged",
            "pr": 1436,
            "head": HEAD_2,
            "ci": "green",
            "gate": "merged_to_main",
            "merge_commit": MERGE_2,
            "readiness_points": 2,
        },
        {
            "seed": 3,
            "title": "Rollback eligibility verifier",
            "owner": "codex-tools-1",
            "state": "complete_existing",
            "ci": "targeted_local_green",
            "gate": "existing_artifact",
            "readiness_points": 1,
        },
        {
            "seed": 4,
            "title": "P4 adversarial corpus",
            "owner": "codex-lead-1",
            "state": "consensus_pending",
            "pr": 1437,
            "head": HEAD_4,
            "ci": "green",
            "gate": "non_author_review_pending",
            "readiness_points": 2,
        },
        {
            "seed": 5,
            "title": "First standing-sign proof",
            "owner": "fable-5",
            "state": "planned",
            "ci": "not_started",
            "gate": "receipt_not_yet_rederived",
            "blockers": ["no_rederivable_standing_sign_receipt_yet"],
            "readiness_points": 3,
        },
        {
            "seed": 6,
            "title": "Hex runtime-readiness trace harness",
            "owner": "fable-5",
            "state": "consensus_pending",
            "pr": 1435,
            "head": HEAD_6,
            "ci": "green",
            "gate": "lead_build_consensus_pass_waiting_rco",
            "readiness_points": 3,
        },
        {
            "seed": 7,
            "title": "Sprint truth dashboard",
            "owner": "codex-lead-1",
            "state": "pr_open",
            "ci": "pending",
            "gate": "this_pr",
            "readiness_points": 1,
        },
    ]


def test_dashboard_counts_only_merged_or_complete_readiness() -> None:
    report = build_wd_p4_sprint_truth_dashboard(
        seed_statuses=_statuses(),
        now_utc=FIXED_NOW,
    )

    assert report["report_version"] == "wd.p4_sprint_truth_dashboard.v0"
    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["readiness"] == {
        "starting_percent": 42,
        "current_percent": 47,
        "target_percent": 52,
        "stretch_percent": 55,
        "counted_points": 5,
        "target_met": False,
        "stretch_met": False,
        "inflates_open_work": False,
    }
    assert report["summary"]["merged_or_complete_count"] == 3
    assert report["summary"]["open_count"] == 4
    assert report["summary"]["standing_sign_proven"] is False
    assert report["summary"]["finish_line_complete"] is False
    seed_2 = report["seeds"][1]
    assert seed_2["pr"] == 1436
    assert seed_2["head_short"] == HEAD_2[:12]
    assert seed_2["counts"] is True


def test_dashboard_fails_closed_on_bad_head_and_runtime_authority() -> None:
    statuses = _statuses()
    statuses[3]["head"] = "not-a-sha"
    statuses[5]["runtime_mutation_authority"] = True
    statuses[5]["production_activation_allowed"] = True

    report = build_wd_p4_sprint_truth_dashboard(
        seed_statuses=statuses,
        now_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert "invalid_head_sha:seed_4" in report["blockers"]
    assert "runtime_mutation_authority_true:seed_6" in report["blockers"]
    assert "production_activation_true:seed_6" in report["blockers"]
    assert report["authority_boundary"]["merge_allowed"] is False


def test_dashboard_requires_all_expected_seed_rows() -> None:
    report = build_wd_p4_sprint_truth_dashboard(
        seed_statuses=_statuses()[:6],
        now_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert report["blockers"] == ["missing_seed:7"]


def test_dashboard_refuses_local_path_leaks() -> None:
    statuses = _statuses()
    statuses[0]["blockers"] = ["see C:\\Python\\project2-master\\secret.txt"]

    report = build_wd_p4_sprint_truth_dashboard(
        seed_statuses=statuses,
        now_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert "local_path_leak:seed_1" in report["blockers"]


def test_markdown_renders_exact_status_without_authority() -> None:
    report = build_wd_p4_sprint_truth_dashboard(
        seed_statuses=_statuses(),
        now_utc=FIXED_NOW,
    )

    markdown = render_markdown(report)

    assert "# WD P4 Sprint Truth Dashboard" in markdown
    assert "| 6 | `consensus_pending` | #1435 | `d24a7c023dec` |" in markdown
    assert "WD readiness: `47%`" in markdown
    assert "runtime_activation_allowed: `false`" in markdown
    assert "production_activation_allowed: `false`" in markdown
    assert "C:\\Python" not in markdown


def test_cli_json_and_markdown_modes(tmp_path: Path) -> None:
    status_file = tmp_path / "statuses.json"
    status_file.write_text(json.dumps(_statuses()), encoding="utf-8")

    assert (
        main(
            [
                "--status-file",
                str(status_file),
                "--now",
                "2026-06-29T22:40:00Z",
                "--json",
            ]
        )
        == 0
    )

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--status-file",
            str(status_file),
            "--now",
            "2026-06-29T22:40:00Z",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "WD P4 Sprint Truth Dashboard" in proc.stdout
    assert "finish line complete: `false`" in proc.stdout
