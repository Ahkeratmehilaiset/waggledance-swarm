#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Read-only autonomous-loop aggregator for one agent tick.

Chains the existing bridge primitives into a single ordered worklist plus a
recommended self-pacing wakeup, so a Claude/Codex session can drain its bridge
inbox and complete already-approved merges WITHOUT a human poke -- while every
mutation still flows through the existing gated tools.

This tool is strictly read-only: it has no ``--apply`` and never runs
``gh pr merge`` or writes the bridge. It only REPORTS:
  * the next-action recommendation (drain peer RCO requests / handoffs to me),
  * my own rco_pass'd PRs that are now CI-green + mergeable + preflight-clear +
    head-matched -- i.e. ready for me to complete the merge in this same tick
    via the existing ``gh pr merge --squash --match-head-commit`` flow,
  * open operator decision packs (surface-only; never auto-resolved),
  * a recommended ScheduleWakeup interval derived from bridge state.

Merge-readiness here encodes the CLAUDE.md Rule 9 peer-RCO criteria (head-match
+ CI green + mergeable clean + no standing peer block), which is the flow used
for direct peer-RCO PRs. Idle-consensus-protocol PRs keep using
``idle_consensus_auto_merge.evaluate_auto_merge_gate`` (consensus + MAGMA
receipt) and are out of scope for this aggregator.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.bridge_next_action import (  # noqa: E402
    DEFAULT_BRIDGE_ROOT,
    list_claims,
    read_events,
    recommend_next_action,
)
from tools.check_bridge_changes_requested import (  # noqa: E402
    check_bridge_clear_to_merge,
)
from tools.idle_consensus_auto_merge import (  # noqa: E402
    MERGEABLE_STATES,
    _check_passed,
)
from tools.operator_decision_pack import scan_inbox  # noqa: E402

# Adaptive wakeup bands (seconds). Sub-300 only when there is actionable
# merge/RCO work, to respect the ~5-minute prompt-cache TTL.
WAKEUP_ACT_NOW = 90  # merge-ready or open peer RCO addressed to me
WAKEUP_IN_FLIGHT = 240  # CI pending on a candidate / active own claim
WAKEUP_QUIET = 1800  # nothing pending; operator's ball or idle

SnapshotFn = Callable[[int], Mapping[str, Any]]


def _event_agent(event: Mapping[str, Any]) -> str:
    return str(event.get("agent", ""))


def _status(event: Mapping[str, Any]) -> str:
    return str(event.get("status", "")).lower()


def _task_id(event: Mapping[str, Any]) -> str:
    return str(event.get("task_id", ""))


def _payload_pr(event: Mapping[str, Any]) -> int | None:
    payload = event.get("payload")
    if isinstance(payload, Mapping):
        pr = payload.get("pr")
        if isinstance(pr, int):
            return pr
        if isinstance(pr, str) and pr.strip().isdigit():
            return int(pr.strip())
    return None


def _payload_head(event: Mapping[str, Any]) -> str:
    payload = event.get("payload")
    if isinstance(payload, Mapping):
        return str(payload.get("head", "") or "")
    return ""


def _is_my_rco_pass(event: Mapping[str, Any], agent: str) -> bool:
    return (
        _event_agent(event) == agent
        and str(event.get("type", "")).lower() == "decision"
        and "pass" in _status(event)
    )


def _is_merged_done(event: Mapping[str, Any]) -> bool:
    return (
        str(event.get("type", "")).lower() == "done"
        and "merged" in _status(event)
    )


# Only recent rco_passes are merge-completion candidates. Older approvals
# whose PRs are already merged/closed (but never got a matching done/merged
# bridge event in earlier flows) must not be re-surfaced or trigger gh calls.
DEFAULT_CANDIDATE_MAX_AGE_HOURS = 48.0


def _parse_ts(value: str) -> datetime | None:
    if not value:
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def my_unmerged_rco_passes(
    events: Sequence[Mapping[str, Any]],
    *,
    agent: str,
    now_utc: datetime | None = None,
    max_age_hours: float | None = DEFAULT_CANDIDATE_MAX_AGE_HOURS,
) -> list[dict[str, Any]]:
    """Tasks where ``agent`` posted an rco_pass that has not yet been followed
    by a merged/done event -- i.e. PRs this agent approved and may now complete.

    Returns the latest rco_pass per task (with its pr + approved head), only
    when no later merged/done event closed it AND the pass is within
    ``max_age_hours`` (older approvals are almost certainly already resolved and
    must not be re-surfaced). Fail-closed: a pass with no extractable PR number
    is skipped (cannot be auto-completed safely).
    """
    latest_pass: dict[str, Mapping[str, Any]] = {}
    merged_tasks: set[str] = set()
    for event in events:
        task = _task_id(event)
        if not task:
            continue
        if _is_my_rco_pass(event, agent):
            latest_pass[task] = event
        elif _is_merged_done(event):
            merged_tasks.add(task)
            # A merge after a pass clears it; drop any recorded pass.
            latest_pass.pop(task, None)
    effective_now = now_utc or datetime.now(timezone.utc)
    candidates: list[dict[str, Any]] = []
    for task, event in latest_pass.items():
        if task in merged_tasks:
            continue
        pr = _payload_pr(event)
        if pr is None:
            continue
        if max_age_hours is not None:
            passed_at = _parse_ts(str(event.get("ts_utc", "")))
            if passed_at is None:
                continue
            age_hours = (effective_now - passed_at).total_seconds() / 3600.0
            if age_hours > max_age_hours:
                continue
        candidates.append(
            {
                "task_id": task,
                "pr": pr,
                "approved_head": _payload_head(event),
                "passed_at_utc": str(event.get("ts_utc", "")),
            }
        )
    candidates.sort(key=lambda item: item["pr"])
    return candidates


def _checks_green(snapshot: Mapping[str, Any]) -> bool:
    checks = snapshot.get("checks")
    if not isinstance(checks, list) or not checks:
        return False
    return all(_check_passed(check) for check in checks if isinstance(check, Mapping))


def evaluate_merge_ready(
    candidate: Mapping[str, Any],
    *,
    events: Sequence[Mapping[str, Any]],
    agent: str,
    snapshot_fn: SnapshotFn | None,
) -> dict[str, Any]:
    """Decide whether one rco_pass'd candidate is ready for me to merge now.

    Read-only: queries PR status (gh pr view via snapshot_fn) and the bridge
    peer-block preflight; emits the exact head-matched merge command but never
    runs it.
    """
    task = candidate["task_id"]
    pr = candidate["pr"]
    approved_head = str(candidate.get("approved_head", ""))
    result: dict[str, Any] = {
        "task_id": task,
        "pr": pr,
        "approved_head": approved_head,
        "ready": False,
        "blockers": [],
    }

    preflight = check_bridge_clear_to_merge(
        events=events, task_id=task, merging_agent=agent
    )
    result["preflight_clear"] = bool(preflight.get("clear_to_merge"))
    if not result["preflight_clear"]:
        result["blockers"].append("peer_block_or_changes_requested")

    if snapshot_fn is None:
        result["blockers"].append("pr_status_unchecked")
        return result
    try:
        snapshot = snapshot_fn(pr)
    except Exception as exc:  # noqa: BLE001 - report, never crash the tick
        result["blockers"].append(f"pr_status_error:{type(exc).__name__}")
        return result

    head_sha = str(snapshot.get("head_sha", ""))
    mergeable = str(snapshot.get("mergeable", ""))
    result["snapshot_head"] = head_sha
    result["mergeable"] = mergeable
    result["checks_green"] = _checks_green(snapshot)

    if approved_head and head_sha and head_sha != approved_head:
        result["blockers"].append("head_moved_since_rco_pass")
    if not approved_head:
        result["blockers"].append("rco_pass_missing_head")
    if mergeable not in MERGEABLE_STATES:
        result["blockers"].append(f"mergeable_not_clean:{mergeable}")
    if not result["checks_green"]:
        result["blockers"].append("checks_not_green")

    result["ready"] = not result["blockers"]
    if result["ready"]:
        result["merge_command"] = (
            f"gh pr merge {pr} --squash --match-head-commit={approved_head}"
        )
    return result


def _recommended_wakeup(
    *,
    next_action: str,
    merge_ready: Sequence[Mapping[str, Any]],
    open_packs_count: int,
) -> dict[str, Any]:
    if any(item.get("ready") for item in merge_ready) or next_action == "answer_incoming":
        return {"seconds": WAKEUP_ACT_NOW, "reason": "actionable merge/RCO work pending"}
    candidate_in_flight = any(
        "checks_not_green" in item.get("blockers", [])
        or "pr_status_error" in " ".join(item.get("blockers", []))
        for item in merge_ready
    )
    if candidate_in_flight or next_action == "continue_claim":
        return {"seconds": WAKEUP_IN_FLIGHT, "reason": "CI in flight or active claim"}
    if open_packs_count:
        return {"seconds": WAKEUP_QUIET, "reason": "awaiting operator decision pack"}
    return {"seconds": WAKEUP_QUIET, "reason": "quiet; no pending bridge work"}


def build_loop_tick(
    *,
    agent: str,
    events: Sequence[Mapping[str, Any]],
    claims: Sequence[Any],
    inbox_dir: Path | str,
    now_utc: datetime | None = None,
    snapshot_fn: SnapshotFn | None = None,
) -> dict[str, Any]:
    """Aggregate one read-only loop tick for ``agent``."""
    next_action_report = recommend_next_action(
        agent=agent, events=events, claims=claims, now_utc=now_utc
    )
    next_action = str(next_action_report.get("action", ""))

    candidates = my_unmerged_rco_passes(events, agent=agent, now_utc=now_utc)
    merge_ready = [
        evaluate_merge_ready(
            candidate, events=events, agent=agent, snapshot_fn=snapshot_fn
        )
        for candidate in candidates
    ]

    packs = scan_inbox(inbox_dir)
    wakeup = _recommended_wakeup(
        next_action=next_action,
        merge_ready=merge_ready,
        open_packs_count=len(packs["open"]),
    )

    return {
        "ok": True,
        "agent": agent,
        "next_action": next_action,
        "next_action_detail": next_action_report,
        "merge_ready": merge_ready,
        "open_operator_packs": packs["open"],
        "invalid_operator_packs": packs["invalid"],
        "recommended_wakeup_seconds": wakeup["seconds"],
        "wakeup_reason": wakeup["reason"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--bridge-root", type=Path, default=DEFAULT_BRIDGE_ROOT)
    parser.add_argument("--events", type=Path, default=None)
    parser.add_argument(
        "--inbox-dir", type=Path, default=ROOT / "docs" / "operator_inbox"
    )
    parser.add_argument(
        "--repo",
        default="",
        help="OWNER/NAME for gh pr view; required to evaluate merge-readiness.",
    )
    parser.add_argument(
        "--check-prs",
        action="store_true",
        help="Query gh pr view for rco_pass'd candidates (read-only).",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    events_path = args.events or (Path(args.bridge_root) / "shared" / "events.jsonl")
    snapshot_fn: SnapshotFn | None = None
    if args.check_prs:
        from tools.pr_status_snapshot import build_pr_status_snapshot

        def snapshot_fn(pr: int) -> Mapping[str, Any]:  # type: ignore[misc]
            return build_pr_status_snapshot(pr_number=pr, repo=args.repo)

    try:
        events = read_events(events_path)
        claims = list_claims(bridge_root=Path(args.bridge_root))
        report = build_loop_tick(
            agent=args.agent,
            events=events,
            claims=claims,
            inbox_dir=args.inbox_dir,
            now_utc=datetime.now(timezone.utc),
            snapshot_fn=snapshot_fn,
        )
    except Exception as exc:  # noqa: BLE001
        report = {
            "ok": False,
            "decision": "bridge_loop_tick_error",
            "errors": [f"{type(exc).__name__}: {exc}"],
        }
        print(json.dumps(report, sort_keys=True))
        return 1

    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(f"agent={report['agent']} next_action={report['next_action']}")
        ready = [m for m in report["merge_ready"] if m.get("ready")]
        print(f"merge_ready (ready now): {len(ready)} / {len(report['merge_ready'])}")
        for m in report["merge_ready"]:
            mark = "READY" if m.get("ready") else "blocked:" + ",".join(m["blockers"])
            print(f"  PR #{m['pr']} ({m['task_id']}): {mark}")
        print(f"open operator packs: {len(report['open_operator_packs'])}")
        print(
            f"recommended wakeup: {report['recommended_wakeup_seconds']}s "
            f"({report['wakeup_reason']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
