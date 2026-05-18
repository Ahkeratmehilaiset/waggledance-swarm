# SPDX-License-Identifier: BUSL-1.1
"""Structured GitHub PR status snapshot for idle auto-merge input.

This helper queries ``gh pr view --json ...`` and normalizes the result into
the ``pr_status`` shape consumed by ``tools/idle_consensus_auto_merge.py``.
It never parses human-readable ``gh`` output and never performs a merge.
Default mode prints the snapshot JSON to stdout; ``--out`` writes that same
snapshot to a local file.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable, Mapping, Sequence


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
PRIVATE_MARKERS = ("PRIVATE_MARKER", "_DO_NOT_LEAK")
GH_JSON_FIELDS = (
    "number,title,headRefOid,headRefName,mergeable,"
    "statusCheckRollup,reviewDecision,isDraft,url"
)

Runner = Callable[[Sequence[str]], Any]


class PrStatusSnapshotError(ValueError):
    """Raised when a PR status snapshot cannot be produced safely."""

    def __init__(self, report: dict[str, Any]) -> None:
        super().__init__("; ".join(str(error) for error in report.get("errors", [])))
        self.report = report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write a structured PR status snapshot from gh pr view JSON.",
    )
    parser.add_argument("pr_number", type=int)
    parser.add_argument("--repo", default="")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--operator-approved",
        action="store_true",
        help="Set operator_approved=true in the snapshot.",
    )
    parser.add_argument(
        "--receipt-verified",
        action="store_true",
        help="Set receipt_verified=true in the snapshot.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        snapshot = build_pr_status_snapshot(
            pr_number=args.pr_number,
            repo=args.repo,
            operator_approved=args.operator_approved,
            receipt_verified=args.receipt_verified,
        )
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(snapshot, sort_keys=True), encoding="utf-8")
            report = {
                "decision": "written",
                "pr_number": snapshot["pr_number"],
                "out": str(args.out),
            }
        else:
            report = snapshot
    except PrStatusSnapshotError as exc:
        report = exc.report
        exit_code = int(report.get("exit_code", 2))
    else:
        exit_code = 0

    if args.json or args.out is None:
        print(json.dumps(report, sort_keys=True))
    else:
        print(report["decision"])
        if "out" in report:
            print(report["out"])
        for error in report.get("errors", []):
            print(f"- {error}", file=sys.stderr)
    return exit_code


def build_pr_status_snapshot(
    *,
    pr_number: int,
    repo: str = "",
    operator_approved: bool = False,
    receipt_verified: bool = False,
    runner: Runner | None = None,
) -> dict[str, Any]:
    """Query GitHub through ``gh`` and normalize the PR status snapshot."""
    if pr_number < 1:
        raise _invalid("invalid_pr_number", "pr_number must be positive")
    if repo and not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise _invalid("invalid_repo", "repo must be OWNER/NAME")

    command = _gh_pr_view_command(pr_number=pr_number, repo=repo)
    run = runner or _run_command
    result = run(command)
    return_code = int(getattr(result, "returncode", 0))
    if return_code != 0:
        raise PrStatusSnapshotError(
            {
                "decision": "gh_pr_view_failed",
                "ok": False,
                "errors": [f"gh pr view failed with exit code {return_code}"],
                "exit_code": 1,
            }
        )

    stdout = str(getattr(result, "stdout", ""))
    _assert_no_private_markers(stdout)
    try:
        raw = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise _invalid("invalid_gh_json", exc.msg) from exc
    if not isinstance(raw, Mapping):
        raise _invalid("invalid_gh_json", "gh pr view JSON must be an object")
    _assert_no_private_markers(raw)
    return _normalize_snapshot(
        raw,
        expected_pr_number=pr_number,
        operator_approved=operator_approved,
        receipt_verified=receipt_verified,
    )


def _gh_pr_view_command(*, pr_number: int, repo: str) -> list[str]:
    command = ["gh", "pr", "view", str(pr_number), "--json", GH_JSON_FIELDS]
    if repo:
        command.extend(["--repo", repo])
    return command


def _normalize_snapshot(
    raw: Mapping[str, Any],
    *,
    expected_pr_number: int,
    operator_approved: bool,
    receipt_verified: bool,
) -> dict[str, Any]:
    number = _require_int(raw.get("number"), "number")
    if number != expected_pr_number:
        raise _invalid("pr_number_mismatch", "gh response PR number did not match request")
    head_sha = str(raw.get("headRefOid", ""))
    if not SHA_RE.fullmatch(head_sha):
        raise _invalid("invalid_head_sha", "headRefOid must be a 40-char lowercase sha")

    checks = _normalize_checks(raw.get("statusCheckRollup", []))
    snapshot = {
        "pr_number": number,
        "title": str(raw.get("title", "")),
        "head_sha": head_sha,
        "head_ref": str(raw.get("headRefName", "")),
        "mergeable": str(raw.get("mergeable", "")),
        "is_draft": bool(raw.get("isDraft", False)),
        "url": str(raw.get("url", "")),
        "review_decision": str(raw.get("reviewDecision", "")),
        "operator_approved": bool(operator_approved),
        "receipt_verified": bool(receipt_verified),
        "checks": checks,
        "statusCheckRollup": checks,
    }
    _assert_no_private_markers(snapshot)
    return snapshot


def _normalize_checks(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise _invalid("invalid_status_check_rollup", "statusCheckRollup must be a list")
    checks: list[dict[str, str]] = []
    for index, item in enumerate(value, 1):
        if not isinstance(item, Mapping):
            raise _invalid(
                "invalid_status_check_rollup",
                f"statusCheckRollup item {index} must be an object",
            )
        name = str(item.get("name") or item.get("context") or "")
        state = str(item.get("state", ""))
        status = str(item.get("status", ""))
        conclusion = str(item.get("conclusion", ""))
        checks.append(
            {
                "name": name,
                "state": state,
                "status": status,
                "conclusion": conclusion,
            }
        )
    return checks


def _require_int(value: object, field: str) -> int:
    if not isinstance(value, int):
        raise _invalid("invalid_gh_json", f"{field} must be an integer")
    return value


def _invalid(decision: str, message: str) -> PrStatusSnapshotError:
    return PrStatusSnapshotError(
        {
            "decision": decision,
            "ok": False,
            "errors": [message],
            "exit_code": 2,
        }
    )


def _run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
    )


def _assert_no_private_markers(value: object) -> None:
    marker = _find_private_marker(value)
    if marker is not None:
        raise PrStatusSnapshotError(
            {
                "decision": "privacy_marker_refused",
                "ok": False,
                "errors": [f"privacy marker refused: {marker}"],
                "exit_code": 2,
            }
        )


def _find_private_marker(value: object) -> str | None:
    if isinstance(value, str):
        for marker in PRIVATE_MARKERS:
            if marker in value:
                return marker
        return None
    if isinstance(value, Mapping):
        for key, item in value.items():
            marker = _find_private_marker(key)
            if marker is not None:
                return marker
            marker = _find_private_marker(item)
            if marker is not None:
                return marker
        return None
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            marker = _find_private_marker(item)
            if marker is not None:
                return marker
    return None


if __name__ == "__main__":
    raise SystemExit(main())
