#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Write v3.12 CI status evidence from GitHub Actions run provenance."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "waggledance.release_ci_status.v1"
DEFAULT_OUTPUT = (
    Path("docs")
    / "runs"
    / "release_soak_evidence"
    / "v3.12.0_ci_status.json"
)
DEFAULT_REPO = "Ahkeratmehilaiset/waggledance-swarm"
DEFAULT_TARGET_VERSION = "v3.12.0"
REQUIRED_WORKFLOW_JOBS = {
    "WaggleDance CI": (
        "test (3.11)",
        "test (3.12)",
        "test (3.13)",
        "security-scan",
    ),
    "Tests": ("unified",),
}


def _format_utc(value: dt.datetime) -> str:
    normalized = value.astimezone(dt.UTC).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


def _parse_utc(value: object) -> dt.datetime:
    if not isinstance(value, str) or not value.strip():
        return dt.datetime.min.replace(tzinfo=dt.UTC)
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(normalized)
    except ValueError:
        return dt.datetime.min.replace(tzinfo=dt.UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def _current_commit() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return completed.stdout.strip()


def _run_json(command: list[str]) -> Any:
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _normalize_job(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": job.get("name", ""),
        "status": job.get("status", ""),
        "conclusion": job.get("conclusion", ""),
        "started_at_utc": job.get("startedAt", ""),
        "completed_at_utc": job.get("completedAt", ""),
        "url": job.get("url", ""),
        "database_id": job.get("databaseId"),
    }


def _normalize_run(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "workflow_name": run.get("workflowName") or run.get("name", ""),
        "run_id": run.get("databaseId"),
        "head_sha": run.get("headSha", ""),
        "event": run.get("event", ""),
        "status": run.get("status", ""),
        "conclusion": run.get("conclusion", ""),
        "created_at_utc": run.get("createdAt", ""),
        "updated_at_utc": run.get("updatedAt", ""),
        "url": run.get("url", ""),
        "jobs": [
            _normalize_job(job)
            for job in run.get("jobs", [])
            if isinstance(job, dict)
        ],
    }


def collect_github_actions_runs(*, repo: str, commit: str) -> list[dict[str, Any]]:
    """Collect relevant GitHub Actions runs for an exact commit with gh CLI."""

    run_rows = _run_json([
        "gh",
        "run",
        "list",
        "--repo",
        repo,
        "--commit",
        commit,
        "--event",
        "push",
        "--limit",
        "50",
        "--json",
        "databaseId,headSha,workflowName,name,status,conclusion,url,createdAt,updatedAt,event",
    ])
    if not isinstance(run_rows, list):
        return []

    runs: list[dict[str, Any]] = []
    for row in run_rows:
        if not isinstance(row, dict):
            continue
        if row.get("headSha") != commit:
            continue
        if (row.get("workflowName") or row.get("name")) not in REQUIRED_WORKFLOW_JOBS:
            continue
        run_id = row.get("databaseId")
        if run_id is None:
            continue
        detailed = _run_json([
            "gh",
            "run",
            "view",
            str(run_id),
            "--repo",
            repo,
            "--json",
            "databaseId,headSha,workflowName,status,conclusion,url,createdAt,updatedAt,event,jobs",
        ])
        if isinstance(detailed, dict):
            runs.append(_normalize_run(detailed))
    return runs


def _latest_run_by_workflow(runs: list[dict[str, Any]], commit: str) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for run in runs:
        workflow = run.get("workflow_name")
        if workflow not in REQUIRED_WORKFLOW_JOBS:
            continue
        if run.get("head_sha") != commit:
            continue
        if run.get("event") != "push":
            continue
        current = selected.get(workflow)
        if current is None or _parse_utc(run.get("updated_at_utc")) >= _parse_utc(
            current.get("updated_at_utc")
        ):
            selected[workflow] = run
    return selected


def evaluate_report(
    report: dict[str, Any],
    *,
    expected_commit: str | None = None,
    target_version: str = DEFAULT_TARGET_VERSION,
) -> list[str]:
    """Return fail-closed blockers for a CI evidence report."""

    blockers: list[str] = []
    if report.get("schema_version") != SCHEMA_VERSION:
        blockers.append("schema_version_invalid")
    if report.get("target_version") != target_version:
        blockers.append("target_version_mismatch")
    commit = report.get("commit")
    if not isinstance(commit, str) or not commit.strip():
        blockers.append("commit_missing")
        commit = ""
    elif expected_commit is not None and commit != expected_commit:
        blockers.append("commit_mismatch")

    source = report.get("source")
    if not isinstance(source, dict) or source.get("type") != "github_actions":
        blockers.append("source_invalid")

    required_jobs = report.get("required_jobs")
    expected_required = {
        (workflow, job)
        for workflow, jobs in REQUIRED_WORKFLOW_JOBS.items()
        for job in jobs
    }
    seen_required = set()
    if not isinstance(required_jobs, list):
        blockers.append("required_jobs_missing")
    else:
        for row in required_jobs:
            if not isinstance(row, dict):
                continue
            seen_required.add((row.get("workflow"), row.get("job")))
        if seen_required != expected_required:
            blockers.append("required_jobs_mismatch")

    runs = report.get("runs")
    if not isinstance(runs, list):
        blockers.append("runs_missing")
        runs = []
    typed_runs = [run for run in runs if isinstance(run, dict)]
    selected = _latest_run_by_workflow(typed_runs, str(commit))

    for workflow, required in REQUIRED_WORKFLOW_JOBS.items():
        run = selected.get(workflow)
        if run is None:
            blockers.append(f"workflow_missing:{workflow}")
            continue
        if run.get("event") != "push":
            blockers.append(f"workflow_event_not_push:{workflow}:{run.get('event')}")
        if run.get("status") != "completed":
            blockers.append(f"workflow_not_completed:{workflow}:{run.get('status')}")
        if run.get("conclusion") != "success":
            blockers.append(
                f"workflow_not_success:{workflow}:{run.get('conclusion')}"
            )
        if not run.get("url"):
            blockers.append(f"workflow_url_missing:{workflow}")

        jobs = run.get("jobs")
        if not isinstance(jobs, list):
            blockers.append(f"jobs_missing:{workflow}")
            jobs = []
        jobs_by_name = {
            job.get("name"): job for job in jobs if isinstance(job, dict)
        }
        for job_name in required:
            job = jobs_by_name.get(job_name)
            if job is None:
                blockers.append(f"job_missing:{workflow}:{job_name}")
                continue
            if job.get("status") != "completed":
                blockers.append(
                    f"job_not_completed:{workflow}:{job_name}:{job.get('status')}"
                )
            if job.get("conclusion") != "success":
                blockers.append(
                    f"job_not_success:{workflow}:{job_name}:{job.get('conclusion')}"
                )
            if not job.get("url"):
                blockers.append(f"job_url_missing:{workflow}:{job_name}")

    return blockers


def build_report(
    runs: list[dict[str, Any]],
    *,
    repo: str = DEFAULT_REPO,
    commit: str,
    target_version: str = DEFAULT_TARGET_VERSION,
    generated_at_utc: dt.datetime | None = None,
) -> dict[str, Any]:
    generated_at_utc = generated_at_utc or dt.datetime.now(dt.UTC)
    report = {
        "schema_version": SCHEMA_VERSION,
        "target_version": target_version,
        "commit": commit,
        "source": {
            "type": "github_actions",
            "repo": repo,
            "collector": "gh run list + gh run view",
        },
        "generated_at_utc": _format_utc(generated_at_utc),
        "required_jobs": [
            {"workflow": workflow, "job": job}
            for workflow, jobs in REQUIRED_WORKFLOW_JOBS.items()
            for job in jobs
        ],
        "runs": runs,
    }
    blockers = evaluate_report(report, expected_commit=commit, target_version=target_version)
    report["blockers"] = blockers
    report["ci_status"] = "pass" if not blockers else "blocked"
    return report


def _read_runs(path: Path) -> list[dict[str, Any]]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(loaded, dict):
        loaded = loaded.get("runs", [])
    if not isinstance(loaded, list):
        raise ValueError("runs JSON must be a list or an object with a runs list")
    return [_normalize_run(row) if "workflowName" in row else row for row in loaded]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--commit", default="")
    parser.add_argument("--target-version", default=DEFAULT_TARGET_VERSION)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--runs-json",
        type=Path,
        help="Read captured GitHub Actions run JSON instead of invoking gh.",
    )
    args = parser.parse_args(argv)

    commit = args.commit or _current_commit()
    if args.runs_json is None:
        runs = collect_github_actions_runs(repo=args.repo, commit=commit)
    else:
        runs = _read_runs(args.runs_json)

    report = build_report(
        runs,
        repo=args.repo,
        commit=commit,
        target_version=args.target_version,
    )
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["ci_status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
