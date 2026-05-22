# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json

from tools.run_release_ci_status_evidence import build_report, evaluate_report, main


COMMIT = "dc76e81cd8c804608bfaedf951220e46ff1baffa"


def _run(
    workflow: str,
    jobs: list[str],
    *,
    commit: str = COMMIT,
    event: str = "push",
    status: str = "completed",
    conclusion: str = "success",
) -> dict:
    return {
        "workflow_name": workflow,
        "run_id": 1000 + len(jobs),
        "head_sha": commit,
        "event": event,
        "status": status,
        "conclusion": conclusion,
        "created_at_utc": "2026-05-22T13:21:32Z",
        "updated_at_utc": "2026-05-22T13:30:00Z",
        "url": f"https://github.example/runs/{workflow}",
        "jobs": [
            {
                "name": job,
                "status": status,
                "conclusion": conclusion,
                "started_at_utc": "2026-05-22T13:21:34Z",
                "completed_at_utc": "2026-05-22T13:30:00Z",
                "url": f"https://github.example/jobs/{job}",
            }
            for job in jobs
        ],
    }


def _complete_runs() -> list[dict]:
    return [
        _run(
            "WaggleDance CI",
            ["test (3.11)", "test (3.12)", "test (3.13)", "security-scan"],
        ),
        _run("Tests", ["unified"]),
    ]


def test_build_report_passes_complete_required_github_actions_jobs() -> None:
    report = build_report(_complete_runs(), commit=COMMIT)

    assert report["ci_status"] == "pass"
    assert report["blockers"] == []
    assert evaluate_report(report, expected_commit=COMMIT) == []


def test_build_report_blocks_pending_required_job() -> None:
    runs = [
        _run(
            "WaggleDance CI",
            ["test (3.11)", "test (3.12)", "test (3.13)", "security-scan"],
            status="in_progress",
            conclusion="",
        ),
        _run("Tests", ["unified"]),
    ]

    report = build_report(runs, commit=COMMIT)

    assert report["ci_status"] == "blocked"
    assert any(
        blocker.startswith("job_not_completed:WaggleDance CI:test (3.12)")
        for blocker in report["blockers"]
    )


def test_build_report_blocks_missing_required_workflow() -> None:
    report = build_report([_run("Tests", ["unified"])], commit=COMMIT)

    assert report["ci_status"] == "blocked"
    assert "workflow_missing:WaggleDance CI" in report["blockers"]


def test_build_report_blocks_pull_request_runs_for_release_evidence() -> None:
    report = build_report(
        [
            _run(
                "WaggleDance CI",
                ["test (3.11)", "test (3.12)", "test (3.13)", "security-scan"],
                event="pull_request",
            ),
            _run("Tests", ["unified"], event="pull_request"),
        ],
        commit=COMMIT,
    )

    assert report["ci_status"] == "blocked"
    assert "workflow_missing:WaggleDance CI" in report["blockers"]
    assert "workflow_missing:Tests" in report["blockers"]


def test_build_report_blocks_head_sha_mismatch() -> None:
    report = build_report(
        _complete_runs(),
        commit="1748c3104a61e2e14f65c38fa7c95c42237e04f9",
    )

    assert report["ci_status"] == "blocked"
    assert "workflow_missing:WaggleDance CI" in report["blockers"]


def test_main_writes_blocked_report_from_captured_runs(tmp_path) -> None:
    runs_path = tmp_path / "runs.json"
    output = tmp_path / "ci_status.json"
    runs_path.write_text(json.dumps([_run("Tests", ["unified"])]), encoding="utf-8")

    rc = main([
        "--commit",
        COMMIT,
        "--runs-json",
        str(runs_path),
        "--output",
        str(output),
    ])

    assert rc == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["ci_status"] == "blocked"
    assert "workflow_missing:WaggleDance CI" in report["blockers"]
