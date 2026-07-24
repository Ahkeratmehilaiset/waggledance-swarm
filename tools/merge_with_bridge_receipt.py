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

from tools.idle_consensus_auto_merge import (  # noqa: E402
    AutoMergeGateError,
    evaluate_auto_merge_gate,
)
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
AGENT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,32}$")
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
    if type(pr_number) is not int or pr_number < 1:
        raise ValueError("pr_number must be positive")
    if type(apply) is not bool:
        raise ValueError("apply must be a boolean")
    if type(repo) is not str:
        raise ValueError("repo must be a string")
    if type(consensus_proposal_id) is not str:
        raise ValueError("consensus_proposal_id must be a string")
    if type(from_agent) is not str:
        raise ValueError("from_agent must be a string")
    if type(bridge_task_id) is not str:
        raise ValueError("bridge_task_id must be a string")
    if type(method) is not str:
        raise ValueError("method must be a string")
    if not isinstance(events_path, Path):
        raise ValueError("events_path must be a Path")
    if not isinstance(out_dir, Path):
        raise ValueError("out_dir must be a Path")
    if runner is not None and not callable(runner):
        raise ValueError("runner must be callable or null")
    if (
        not consensus_proposal_id
        or consensus_proposal_id != consensus_proposal_id.strip()
    ):
        raise ValueError(
            "consensus_proposal_id must be a non-empty exact string"
        )
    if repo != repo.strip():
        raise ValueError("repo must be an exact string")
    if repo and not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("repo must be OWNER/NAME")
    if from_agent and (
        from_agent != from_agent.strip()
        or AGENT_ID_RE.fullmatch(from_agent) is None
    ):
        raise ValueError("from_agent must be an exact agent id or empty")
    if bridge_task_id and bridge_task_id != bridge_task_id.strip():
        raise ValueError("bridge_task_id must be an exact string or empty")
    trusted_now_utc = _utc_now()
    effective_now_utc = (
        trusted_now_utc
        if now_utc is None
        else _validated_now_utc(now_utc)
    )
    if apply and effective_now_utc.date() != trusted_now_utc.date():
        raise ValueError(
            "now_utc must use the current UTC date when apply is true"
        )
    if apply:
        effective_now_utc = trusted_now_utc
    expected_head = _validate_sha(expected_head, "expected_head")
    expected_base_sha = _validate_sha(expected_base_sha, "expected_base_sha")
    if method not in {"squash", "merge", "rebase"}:
        raise ValueError("method must be squash, merge, or rebase")

    run = runner if runner is not None else _run_command
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
            now_utc=effective_now_utc,
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

    try:
        verified_snapshot = build_pr_status_snapshot(
            pr_number=pr_number,
            repo=repo,
            receipt_verified=True,
            expected_base_sha=expected_base_sha,
            runner=run,
        )
    except PrStatusSnapshotError as exc:
        return _blocked(
            decision="apply_snapshot_recheck_failed",
            errors=list(exc.report.get("errors", []))
            or ["apply snapshot recheck failed"],
            stage="apply_recheck",
            pr_number=pr_number,
            extra={
                "snapshot_report": exc.report,
                "snapshot_path": str(snapshot_path),
                "receipt_bundle_path": receipt_report["receipt_bundle_path"],
            },
        )
    drifted = _snapshot_drift_fields(snapshot, verified_snapshot)
    if drifted:
        return _blocked(
            decision="apply_snapshot_recheck_failed",
            errors=[
                "apply snapshot recheck drifted: " + ", ".join(drifted)
            ],
            stage="apply_recheck",
            pr_number=pr_number,
            extra={
                "snapshot_path": str(snapshot_path),
                "receipt_bundle_path": receipt_report["receipt_bundle_path"],
            },
        )

    try:
        fresh_gate = evaluate_auto_merge_gate(
            pr_status=verified_snapshot,
            expected_head=expected_head,
            expected_base_sha=expected_base_sha,
            consensus_proposal_id=consensus_proposal_id,
            receipt_bundle_path=receipt_report["receipt_bundle_path"],
            events_path=events_path,
            utc_date=effective_now_utc.date().isoformat(),
            repo=repo,
            from_agent=from_agent,
            bridge_task_id=bridge_task_id,
            apply=False,
            require_bridge_consensus=True,
        )
    except AutoMergeGateError as exc:
        fresh_gate = dict(exc.report)
    if fresh_gate.get("ok") is not True:
        return _blocked(
            decision="apply_gate_recheck_failed",
            errors=list(fresh_gate.get("reasons", []))
            or list(fresh_gate.get("errors", []))
            or ["fresh apply safety gate failed"],
            stage="apply_recheck",
            pr_number=pr_number,
            extra={
                "fresh_gate": fresh_gate,
                "snapshot_path": str(snapshot_path),
                "receipt_bundle_path": receipt_report["receipt_bundle_path"],
            },
        )

    merge_result = run(command)
    return_code = getattr(merge_result, "returncode", None)
    if type(return_code) is not int:
        return {
            **ready_report,
            "ok": False,
            "decision": "invalid_merge_result",
            "stage": "merge",
            "errors": ["gh pr merge returned an invalid exit code"],
            "gh_merge_attempted": True,
            "merge_executed": False,
            "exit_code": 1,
        }
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

    post_merge = _query_confirmed_merge(
        pr_number=pr_number,
        repo=repo,
        expected_head=expected_head,
        expected_head_ref=str(verified_snapshot["head_ref"]),
        expected_base_ref=str(verified_snapshot["base_ref"]),
        runner=run,
    )
    if post_merge.get("merged") is not True:
        return {
            **ready_report,
            "ok": False,
            "decision": "post_merge_state_unconfirmed",
            "stage": "post_merge",
            "errors": list(post_merge.get("errors", []))
            or ["merge command exited zero but GitHub did not confirm MERGED"],
            "fresh_gate": fresh_gate,
            "post_merge": post_merge,
            "gh_merge_attempted": True,
            "merge_executed": False,
            "exit_code": 1,
        }

    return {
        **ready_report,
        "decision": "merge_executed_after_receipt",
        "fresh_gate": fresh_gate,
        "post_merge": post_merge,
        "merge_commit_sha": post_merge["merge_commit_sha"],
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


def _snapshot_drift_fields(
    initial: Mapping[str, Any],
    verified: Mapping[str, Any],
) -> list[str]:
    fields = (
        "pr_number",
        "head_ref",
        "head_sha",
        "base_sha",
        "base_ref",
        "base_tip_sha",
        "updated_at",
        "state",
        "is_draft",
        "mergeable",
        "checks",
        "changed_paths",
        "diff_text",
        "git_identities",
        "git_identity_evidence",
    )
    return [field for field in fields if initial.get(field) != verified.get(field)]


def _query_confirmed_merge(
    *,
    pr_number: int,
    repo: str,
    expected_head: str,
    expected_head_ref: str,
    expected_base_ref: str,
    runner: Runner,
) -> dict[str, Any]:
    command = [
        "gh",
        "pr",
        "view",
        str(pr_number),
        "--json",
        "number,state,mergeCommit,headRefOid,headRefName,baseRefName",
    ]
    if repo:
        command.extend(["--repo", repo])
    result = runner(command)
    return_code = getattr(result, "returncode", None)
    if type(return_code) is not int:
        return {
            "merged": False,
            "decision": "post_merge_query_invalid",
            "errors": ["post-merge GitHub query returned an invalid exit code"],
        }
    if return_code != 0:
        return {
            "merged": False,
            "decision": "post_merge_query_failed",
            "errors": [
                f"post-merge GitHub query failed with exit code {return_code}"
            ],
        }
    try:
        raw = getattr(result, "stdout", "")
        if isinstance(raw, bytes):
            text = raw.decode("utf-8", errors="strict")
        elif isinstance(raw, str):
            text = raw.encode("utf-8", errors="strict").decode(
                "utf-8", errors="strict"
            )
        else:
            raise TypeError("stdout must be text or bytes")
        payload = json.loads(
            text,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant: {value}")
            ),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (TypeError, UnicodeError, ValueError, json.JSONDecodeError):
        return {
            "merged": False,
            "decision": "post_merge_query_invalid",
            "errors": ["post-merge GitHub response was invalid"],
        }
    if not isinstance(payload, Mapping):
        return {
            "merged": False,
            "decision": "post_merge_query_invalid",
            "errors": ["post-merge GitHub response must be an object"],
        }
    state = payload.get("state")
    number = payload.get("number")
    head = payload.get("headRefOid")
    head_ref = payload.get("headRefName")
    base_ref = payload.get("baseRefName")
    merge_commit = payload.get("mergeCommit")
    merge_oid = (
        merge_commit.get("oid")
        if isinstance(merge_commit, Mapping)
        else None
    )
    if (
        type(number) is not int
        or number != pr_number
        or state != "MERGED"
        or type(head) is not str
        or head != expected_head
        or type(head_ref) is not str
        or head_ref != expected_head_ref
        or type(base_ref) is not str
        or base_ref != expected_base_ref
        or type(merge_oid) is not str
        or not SHA_RE.fullmatch(merge_oid)
    ):
        return {
            "merged": False,
            "decision": "post_merge_not_confirmed",
            "errors": [
                "post-merge GitHub state/head/merge commit did not confirm "
                "the exact-head merge"
            ],
        }
    return {
        "merged": True,
        "decision": "post_merge_confirmed",
        "head_sha": head,
        "merge_commit_sha": merge_oid,
        "errors": [],
    }


def _reject_duplicate_json_keys(
    pairs: Sequence[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


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


def _validate_sha(value: object, field: str) -> str:
    if type(value) is not str or not SHA_RE.fullmatch(value):
        raise ValueError(f"{field} must be a 40-char lowercase sha")
    return value


def _validated_now_utc(value: object) -> datetime:
    if value is None:
        return _utc_now()
    if type(value) is not datetime:
        raise ValueError("now_utc must be a timezone-aware datetime or null")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now_utc must be timezone-aware")
    return value.astimezone(timezone.utc)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_utc(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _run_command(command: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        list(command),
        check=False,
        capture_output=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
