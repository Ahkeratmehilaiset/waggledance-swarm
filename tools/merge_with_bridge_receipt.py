# SPDX-License-Identifier: BUSL-1.1
"""Fail-closed wrapper for bridge-consensus merges.

The wrapper exists to keep the receipt preflight and the actual ``gh pr merge``
in one checked control flow. It never merges unless the PR status snapshot and
MAGMA bridge-consensus receipt both succeed for the exact head/base pair. The
default mode is dry-run; ``--apply`` is required before the final ``gh`` merge
command is executed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.idle_check import DEFAULT_EVENTS_PATH  # noqa: E402
from tools.pr_status_snapshot import (  # noqa: E402
    PrStatusSnapshotError,
    build_pr_status_snapshot,
)
from tools.write_bridge_consensus_merge_receipt import (  # noqa: E402
    BridgeConsensusMergeReceiptError,
    write_bridge_consensus_merge_receipt,
)


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
Runner = Callable[[Sequence[str]], Any]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pr_number", type=int)
    parser.add_argument("--repo", default="")
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS_PATH)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--expected-base-sha", required=True)
    parser.add_argument("--consensus-proposal-id", required=True)
    parser.add_argument("--from-agent", default="")
    parser.add_argument("--bridge-task-id", default="")
    parser.add_argument(
        "--method",
        choices=("squash", "merge", "rebase"),
        default="squash",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually run gh pr merge after the receipt gate passes.",
    )
    parser.add_argument("--now", default="")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        now_utc = _parse_utc(args.now) if args.now else None
        report = merge_with_bridge_receipt(
            pr_number=args.pr_number,
            repo=args.repo,
            events_path=args.events,
            out_dir=args.out_dir,
            expected_head=args.expected_head,
            expected_base_sha=args.expected_base_sha,
            consensus_proposal_id=args.consensus_proposal_id,
            from_agent=args.from_agent,
            bridge_task_id=args.bridge_task_id,
            method=args.method,
            apply=args.apply,
            now_utc=now_utc,
        )
    except ValueError as exc:
        report = {
            "ok": False,
            "decision": "invalid_input",
            "errors": [str(exc)],
            "merge_executed": False,
            "gh_merge_attempted": False,
            "exit_code": 2,
        }
    except OSError as exc:
        report = {
            "ok": False,
            "decision": "io_error",
            "errors": [exc.__class__.__name__],
            "merge_executed": False,
            "gh_merge_attempted": False,
            "exit_code": 1,
        }

    exit_code = int(report.get("exit_code", 0 if report.get("ok") else 1))
    if args.json:
        print(json.dumps(report, sort_keys=True))
    elif report.get("ok"):
        print(report["decision"])
    else:
        print("merge with bridge receipt FAILED", file=sys.stderr)
        for error in report.get("errors", []):
            print(f"- {error}", file=sys.stderr)
    return exit_code


def merge_with_bridge_receipt(
    *,
    pr_number: int,
    repo: str,
    events_path: Path,
    out_dir: Path,
    expected_head: str,
    expected_base_sha: str,
    consensus_proposal_id: str,
    from_agent: str = "",
    bridge_task_id: str = "",
    method: str = "squash",
    apply: bool = False,
    now_utc: datetime | None = None,
    runner: Runner | None = None,
) -> dict[str, Any]:
    """Run snapshot + receipt preflight, then optionally merge exact head."""
    if pr_number < 1:
        raise ValueError("pr_number must be positive")
    expected_head = _validate_sha(expected_head, "expected_head")
    expected_base_sha = _validate_sha(expected_base_sha, "expected_base_sha")
    if method not in {"squash", "merge", "rebase"}:
        raise ValueError("method must be squash, merge, or rebase")

    run = runner or _run_command
    snapshot_path = out_dir / "pr-status.json"
    receipt_dir = out_dir / "receipt"

    try:
        snapshot = build_pr_status_snapshot(
            pr_number=pr_number,
            repo=repo,
            receipt_verified=True,
            expected_base_sha=expected_base_sha,
            runner=run,
        )
    except PrStatusSnapshotError as exc:
        return _blocked(
            decision="pr_status_snapshot_failed",
            errors=list(exc.report.get("errors", [])) or ["PR status snapshot failed"],
            stage="snapshot",
            pr_number=pr_number,
            extra={"snapshot_report": exc.report},
        )

    if snapshot.get("head_sha") != expected_head:
        return _blocked(
            decision="unexpected_head",
            errors=[
                (
                    f"PR head {snapshot.get('head_sha')} does not match expected "
                    f"head {expected_head}"
                )
            ],
            stage="snapshot",
            pr_number=pr_number,
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(json.dumps(snapshot, sort_keys=True), encoding="utf-8")

    try:
        receipt_report = write_bridge_consensus_merge_receipt(
            pr_status=snapshot,
            events_path=events_path,
            out_dir=receipt_dir,
            expected_head=expected_head,
            expected_base_sha=expected_base_sha,
            consensus_proposal_id=consensus_proposal_id,
            repo=repo,
            from_agent=from_agent,
            bridge_task_id=bridge_task_id,
            now_utc=now_utc,
        )
    except BridgeConsensusMergeReceiptError as exc:
        return _blocked(
            decision="receipt_preflight_failed",
            errors=list(exc.report.get("errors", [])) or ["receipt preflight failed"],
            stage="receipt",
            pr_number=pr_number,
            extra={"receipt_report": exc.report, "snapshot_path": str(snapshot_path)},
        )

    command = _merge_command(
        pr_number=pr_number,
        repo=repo,
        expected_head=expected_head,
        method=method,
    )
    ready_report = {
        "ok": True,
        "decision": "merge_receipt_ready",
        "pr_number": pr_number,
        "expected_head": expected_head,
        "expected_base_sha": expected_base_sha,
        "snapshot_path": str(snapshot_path),
        "receipt_bundle_path": receipt_report["receipt_bundle_path"],
        "gh_command": command,
        "gh_merge_attempted": False,
        "merge_executed": False,
        "apply": bool(apply),
        "exit_code": 0,
    }
    if not apply:
        return ready_report

    merge_result = run(command)
    return_code = int(getattr(merge_result, "returncode", 0))
    if return_code != 0:
        return {
            **ready_report,
            "ok": False,
            "decision": "gh_merge_failed",
            "stage": "merge",
            "errors": [f"gh pr merge failed with exit code {return_code}"],
            "gh_merge_attempted": True,
            "merge_executed": False,
            "exit_code": 1,
        }

    return {
        **ready_report,
        "decision": "merge_executed_after_receipt",
        "gh_merge_attempted": True,
        "merge_executed": True,
    }


def _merge_command(
    *,
    pr_number: int,
    repo: str,
    expected_head: str,
    method: str,
) -> list[str]:
    command = ["gh", "pr", "merge", str(pr_number)]
    if repo:
        command.extend(["--repo", repo])
    command.extend([f"--match-head-commit={expected_head}", f"--{method}"])
    return command


def _blocked(
    *,
    decision: str,
    errors: Sequence[str],
    stage: str,
    pr_number: int,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {}
    if extra:
        report.update(dict(extra))
    report.update(
        {
        "ok": False,
        "decision": decision,
        "stage": stage,
        "pr_number": pr_number,
        "errors": list(errors),
        "gh_merge_attempted": False,
        "merge_executed": False,
        "exit_code": 1,
        }
    )
    return report


def _validate_sha(value: str, field: str) -> str:
    normalized = value.strip().lower()
    if not SHA_RE.fullmatch(normalized):
        raise ValueError(f"{field} must be a 40-char lowercase sha")
    return normalized


def _parse_utc(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
