#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Read-only drift report for the repo copy used by bridge loop tools.

Bridge automation can run from a long-lived runtime worktree while implementation
PRs land in separate clean worktrees. If the runtime worktree is dirty or does
not contain the expected reference commit, local scheduler recommendations can
disagree with the current source tree. This tool makes that drift explicit.

The report is advisory only. It never fetches, checks out, resets, writes files,
opens PRs, restarts processes, or changes bridge state.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
from typing import Sequence


DEFAULT_REFERENCE = "origin/main"


@dataclass(frozen=True)
class GitResult:
    returncode: int
    stdout: str
    stderr: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Report whether a bridge runtime repo has source drift.",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help="Repository/worktree to inspect. Defaults to the current directory.",
    )
    parser.add_argument(
        "--reference",
        default=DEFAULT_REFERENCE,
        help="Reference that the runtime repo should contain. Defaults to origin/main.",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--fail-on-drift", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = report_runtime_source_drift(
            repo=args.repo,
            reference=args.reference,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        report = {
            "ok": False,
            "decision": "runtime_source_drift_error",
            "exit_code": 2,
            "reason": str(exc),
            "authority_boundary": authority_boundary(),
        }

    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(report["decision"])
        if "relationship" in report:
            print(f"relationship: {report['relationship']}")
        if "dirty_count" in report:
            print(f"dirty_count: {report['dirty_count']}")
        if report.get("safe_next_action"):
            print(f"safe_next_action: {report['safe_next_action']}")

    if report.get("exit_code"):
        return int(report["exit_code"])
    if args.fail_on_drift and report.get("drift"):
        return 3
    return 0


def authority_boundary() -> dict[str, bool]:
    return {
        "read_only": True,
        "git_fetch_allowed": False,
        "git_checkout_allowed": False,
        "git_reset_allowed": False,
        "file_write_allowed": False,
        "bridge_append_allowed": False,
        "process_restart_allowed": False,
        "merge_allowed": False,
        "gate_skip_allowed": False,
    }


def report_runtime_source_drift(*, repo: Path, reference: str) -> dict[str, object]:
    if not reference.strip():
        raise ValueError("reference must not be empty")

    git_root = _git(repo, "rev-parse", "--show-toplevel").stdout.strip()
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    reference_result = _git(repo, "rev-parse", "--verify", f"{reference}^{{commit}}", check=False)

    status_lines = [
        line
        for line in _git(repo, "status", "--porcelain=v1").stdout.splitlines()
        if line.strip()
    ]
    dirty_summary = _dirty_summary(status_lines)

    reference_oid = reference_result.stdout.strip() if reference_result.returncode == 0 else ""
    if reference_result.returncode != 0:
        relationship = "reference_missing"
        contains_reference = False
        head_is_ancestor_of_reference = False
    else:
        contains_reference = _is_ancestor(repo, reference_oid, head)
        head_is_ancestor_of_reference = _is_ancestor(repo, head, reference_oid)
        if head == reference_oid:
            relationship = "at_reference"
        elif contains_reference:
            relationship = "contains_reference"
        elif head_is_ancestor_of_reference:
            relationship = "behind_reference"
        else:
            relationship = "diverged_from_reference"

    dirty = dirty_summary["dirty_count"] > 0
    source_drift = relationship not in {"at_reference", "contains_reference"}
    drift = dirty or source_drift
    decision = _decision_for(relationship=relationship, dirty=dirty)
    safe_next_action = _safe_next_action(relationship=relationship, dirty=dirty)

    return {
        "ok": True,
        "decision": decision,
        "drift": drift,
        "source_drift": source_drift,
        "dirty": dirty,
        "relationship": relationship,
        "contains_reference": contains_reference,
        "head_is_ancestor_of_reference": head_is_ancestor_of_reference,
        "repo": str(repo),
        "git_root": git_root,
        "branch": branch,
        "head": head,
        "reference": reference,
        "reference_oid": reference_oid,
        "status_sample": status_lines[:20],
        "safe_next_action": safe_next_action,
        "authority_boundary": authority_boundary(),
        "report_version": "wd.bridge_runtime_source_drift_report.v0",
        **dirty_summary,
    }


def _decision_for(*, relationship: str, dirty: bool) -> str:
    if relationship == "reference_missing":
        return "runtime_source_reference_missing"
    if dirty and relationship in {"at_reference", "contains_reference"}:
        return "runtime_source_dirty"
    if relationship == "behind_reference":
        return "runtime_source_behind_reference"
    if relationship == "diverged_from_reference":
        return "runtime_source_diverged_from_reference"
    if dirty:
        return "runtime_source_dirty"
    return "runtime_source_clean_current"


def _safe_next_action(*, relationship: str, dirty: bool) -> str:
    if relationship == "reference_missing":
        return "fetch_or_verify_reference_outside_this_read_only_tool"
    if dirty:
        return "checkpoint_or_isolate_runtime_worktree_before_updating_source"
    if relationship == "behind_reference":
        return "fast_forward_or_recreate_runtime_worktree_from_reference"
    if relationship == "diverged_from_reference":
        return "inspect_branch_divergence_before_using_runtime_scheduler_output"
    return ""


def _dirty_summary(lines: Sequence[str]) -> dict[str, int]:
    staged = 0
    unstaged = 0
    untracked = 0
    for line in lines:
        if line.startswith("??"):
            untracked += 1
            continue
        index = line[0] if line else " "
        worktree = line[1] if len(line) > 1 else " "
        if index != " ":
            staged += 1
        if worktree != " ":
            unstaged += 1
    return {
        "dirty_count": len(lines),
        "staged_count": staged,
        "unstaged_count": unstaged,
        "untracked_count": untracked,
    }


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    if not ancestor or not descendant:
        return False
    return _git(repo, "merge-base", "--is-ancestor", ancestor, descendant, check=False).returncode == 0


def _git(repo: Path, *args: str, check: bool = True) -> GitResult:
    command = ["git", "-C", str(repo), *args]
    completed = subprocess.run(
        command,
        check=False,
        text=True,
        capture_output=True,
    )
    result = GitResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed with exit {result.returncode}: "
            f"{result.stderr.strip()}"
        )
    return result


if __name__ == "__main__":
    raise SystemExit(main())
