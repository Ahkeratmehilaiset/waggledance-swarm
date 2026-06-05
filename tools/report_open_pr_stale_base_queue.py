# SPDX-License-Identifier: BUSL-1.1
"""Report open PR base freshness against the current main SHA.

This CLI is intentionally read-only. It queries ``gh pr list`` with structured
JSON fields, compares each open PR's ``baseRefOid`` to a caller-supplied
current base SHA, and emits a queue report suitable for bridge handoffs.
It does not refresh branches, post bridge events, or authorize merges.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Any, Callable, Mapping, Sequence

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
GUARDED_TOKENS = ("PRIVATE_" + "MARKER", "_DO_NOT_" + "LEAK")
GH_PR_LIST_FIELDS = (
    "number,title,headRefName,headRefOid,baseRefOid,isDraft,"
    "mergeStateStatus,statusCheckRollup,reviewDecision,url"
)

Runner = Callable[[Sequence[str]], Any]


class OpenPrStaleBaseReportError(ValueError):
    """Raised when the open PR queue report cannot be produced safely."""

    def __init__(self, report: dict[str, Any]) -> None:
        super().__init__("; ".join(str(error) for error in report.get("errors", [])))
        self.report = report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--expected-base-sha",
        required=True,
        help="Current base branch SHA to compare with each PR baseRefOid.",
    )
    parser.add_argument("--repo", default="", help="Optional OWNER/NAME repository.")
    parser.add_argument("--limit", type=int, default=50, help="Open PR list limit.")
    parser.add_argument(
        "--fail-on-stale",
        action="store_true",
        help="Exit 1 when any open PR is based on a different baseRefOid.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_open_pr_stale_base_report(
            expected_base_sha=args.expected_base_sha,
            repo=args.repo,
            limit=args.limit,
        )
    except OpenPrStaleBaseReportError as exc:
        report = exc.report
        exit_code = int(report.get("exit_code", 2))
    else:
        exit_code = 1 if args.fail_on_stale and report["stale_base_count"] else 0
        report["exit_code"] = exit_code

    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        _print_human(report)
    return exit_code


def build_open_pr_stale_base_report(
    *,
    expected_base_sha: str,
    repo: str = "",
    limit: int = 50,
    runner: Runner | None = None,
) -> dict[str, Any]:
    """Build a structured report of open PRs whose base ref is stale."""
    expected_base_sha = expected_base_sha.strip().lower()
    if not SHA_RE.fullmatch(expected_base_sha):
        raise _invalid(
            "invalid_expected_base_sha",
            "expected_base_sha must be a 40-char lowercase sha",
        )
    if repo and (
        ".." in repo or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo)
    ):
        raise _invalid("invalid_repo", "repo must be OWNER/NAME")
    if limit < 1:
        raise _invalid("invalid_limit", "limit must be positive")

    command = _gh_pr_list_command(repo=repo, limit=limit)
    run = runner or _run_command
    result = run(command)
    return_code = int(getattr(result, "returncode", 0))
    if return_code != 0:
        raise OpenPrStaleBaseReportError(
            {
                "decision": "gh_pr_list_failed",
                "ok": False,
                "errors": [f"gh pr list failed with exit code {return_code}"],
                "exit_code": 1,
            }
        )

    stdout = str(getattr(result, "stdout", ""))
    _assert_no_private_markers(stdout)
    try:
        raw = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise _invalid("invalid_gh_json", exc.msg) from exc
    if not isinstance(raw, list):
        raise _invalid("invalid_gh_json", "gh pr list JSON must be a list")
    _assert_no_private_markers(raw)

    prs = [
        _normalize_pr(item, expected_base_sha=expected_base_sha, index=index)
        for index, item in enumerate(raw, 1)
    ]
    stale_prs = [item for item in prs if item["base_status"] == "stale"]
    current_prs = [item for item in prs if item["base_status"] == "current"]
    decision = "stale_base_refs_present" if stale_prs else "all_open_prs_current_base"
    report = {
        "decision": decision,
        "ok": True,
        "queue_clear": not stale_prs,
        "expected_base_sha": expected_base_sha,
        "open_pr_count": len(prs),
        "current_base_count": len(current_prs),
        "stale_base_count": len(stale_prs),
        "stale_pr_numbers": [item["number"] for item in stale_prs],
        "prs": prs,
        "stale_prs": stale_prs,
    }
    _assert_no_private_markers(report)
    return report


def _gh_pr_list_command(*, repo: str, limit: int) -> list[str]:
    command = [
        "gh",
        "pr",
        "list",
        "--state",
        "open",
        "--json",
        GH_PR_LIST_FIELDS,
        "--limit",
        str(limit),
    ]
    if repo:
        command.extend(["--repo", repo])
    return command


def _normalize_pr(
    value: object,
    *,
    expected_base_sha: str,
    index: int,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _invalid("invalid_gh_json", f"PR item {index} must be an object")
    number = _require_int(value.get("number"), f"PR item {index} number")
    if number < 1:
        raise _invalid("invalid_gh_json", f"PR item {index} number must be positive")
    head_sha = _require_sha(value.get("headRefOid"), f"PR #{number} headRefOid")
    base_sha = _require_sha(value.get("baseRefOid"), f"PR #{number} baseRefOid")
    base_status = "current" if base_sha == expected_base_sha else "stale"
    return {
        "number": number,
        "title": str(value.get("title", "")),
        "head_ref": str(value.get("headRefName", "")),
        "head_sha": head_sha,
        "base_sha": base_sha,
        "base_status": base_status,
        "is_draft": bool(value.get("isDraft", False)),
        "merge_state_status": str(value.get("mergeStateStatus", "")),
        "review_decision": str(value.get("reviewDecision", "")),
        "url": str(value.get("url", "")),
        "check_summary": _summarize_checks(value.get("statusCheckRollup", [])),
    }


def _summarize_checks(value: object) -> dict[str, int]:
    if not isinstance(value, list):
        raise _invalid("invalid_gh_json", "statusCheckRollup must be a list")
    summary = {"total": 0, "success": 0, "pending": 0, "failure": 0, "other": 0}
    for index, item in enumerate(value, 1):
        if not isinstance(item, Mapping):
            raise _invalid(
                "invalid_gh_json",
                f"statusCheckRollup item {index} must be an object",
            )
        summary["total"] += 1
        conclusion = str(item.get("conclusion", "")).upper()
        state = str(item.get("state", "")).upper()
        status = str(item.get("status", "")).upper()
        if conclusion in {"SUCCESS", "NEUTRAL", "SKIPPED"} or state == "SUCCESS":
            summary["success"] += 1
        elif conclusion in {
            "FAILURE",
            "TIMED_OUT",
            "CANCELLED",
            "ACTION_REQUIRED",
        } or state in {"FAILURE", "ERROR"}:
            summary["failure"] += 1
        elif status in {"QUEUED", "IN_PROGRESS", "PENDING"} or state == "PENDING":
            summary["pending"] += 1
        else:
            summary["other"] += 1
    return summary


def _require_int(value: object, field: str) -> int:
    if not isinstance(value, int):
        raise _invalid("invalid_gh_json", f"{field} must be an integer")
    return value


def _require_sha(value: object, field: str) -> str:
    sha = str(value or "").strip().lower()
    if not SHA_RE.fullmatch(sha):
        raise _invalid("invalid_gh_json", f"{field} must be a 40-char lowercase sha")
    return sha


def _run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
    )


def _invalid(decision: str, message: str) -> OpenPrStaleBaseReportError:
    return OpenPrStaleBaseReportError(
        {
            "decision": decision,
            "ok": False,
            "errors": [message],
            "exit_code": 2,
        }
    )


def _assert_no_private_markers(value: object) -> None:
    marker = _find_private_marker(value)
    if marker is not None:
        raise OpenPrStaleBaseReportError(
            {
                "decision": "privacy_marker_refused",
                "ok": False,
                "errors": [f"privacy marker refused: {marker}"],
                "exit_code": 2,
            }
        )


def _find_private_marker(value: object) -> str | None:
    if isinstance(value, str):
        for marker in GUARDED_TOKENS:
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


def _print_human(report: Mapping[str, Any]) -> None:
    print(f"decision: {report.get('decision', '')}")
    print(f"open_pr_count: {report.get('open_pr_count', 0)}")
    print(f"current_base_count: {report.get('current_base_count', 0)}")
    print(f"stale_base_count: {report.get('stale_base_count', 0)}")
    for item in report.get("stale_prs", []):
        if isinstance(item, Mapping):
            print(
                f"- PR #{item.get('number')}: base {item.get('base_sha')} "
                f"!= expected {report.get('expected_base_sha')}"
            )
    for error in report.get("errors", []):
        print(f"- {error}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
