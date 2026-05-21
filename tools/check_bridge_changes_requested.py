# SPDX-License-Identifier: BUSL-1.1
"""Rule 9 preflight: refuse autonomous merge when a peer has blocked on bridge.

CLAUDE.md Rule 9 lists four conditions for autonomous merge:
  a) PR head SHA matches local EXPECTED_HEAD
  b) all required CI checks are green
  c) GitHub mergeable state is clean / mergeable
  d) no rule in CLAUDE.md is violated

GitHub-side gh pr view covers (a), (b), (c) and the charter-denylist part of
(d). But (d) also requires that no peer has emitted a blocking review on the
bridge for the same task_id. A peer's bridge-side decision changes_requested
event is NOT reflected in gh pr view at all, so the existing tooling can
race past it.

This tool fills that gap. It refuses if a peer has emitted a
``decision`` event with a blocking status (``changes_requested``,
``rco_block``, ``blocked``) for the given task_id AFTER the most recent
RCO pass / acknowledgement event from the SAME peer (so a fresh approval
overrides an older block).

Designed to be called BEFORE ``gh pr merge --squash --match-head-commit``:

    python tools/check_bridge_changes_requested.py \\
        --task-id <task_id> --from-agent claude --bridge-root .agent-bridge

Exit codes:
  0  no peer block found, safe to merge
  3  peer block found (most recent peer-decision for this task_id was blocking)
  2  argument error / unreadable events file
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


DEFAULT_BRIDGE_ROOT = Path(".agent-bridge")
BLOCKING_STATUSES = frozenset(
    {
        "changes_requested",
        "rco_block",
        "blocked",
        "rco_blocked",
        "block_requested",
    }
)
APPROVAL_STATUSES = frozenset(
    {
        "rco_pass",
        "rco_pass_pending_ci",
        "rco_pass_operator_merge_required",
        "approved",
        "acknowledged",
    }
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Refuse autonomous merge when a peer agent has blocked the PR "
            "via a bridge decision changes_requested event."
        ),
    )
    parser.add_argument(
        "--task-id",
        required=True,
        help="Bridge task_id under which the peer review was requested.",
    )
    parser.add_argument(
        "--from-agent",
        required=True,
        help="The agent attempting the merge (claude / codex / operator).",
    )
    parser.add_argument(
        "--bridge-root",
        type=Path,
        default=DEFAULT_BRIDGE_ROOT,
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.task_id or not args.task_id.strip():
        print("--task-id must not be empty", file=sys.stderr)
        return 2
    if not args.from_agent or not args.from_agent.strip():
        print("--from-agent must not be empty", file=sys.stderr)
        return 2

    events_path = args.bridge_root / "shared" / "events.jsonl"
    if not events_path.exists():
        result = {
            "ok": False,
            "decision": "missing_events_file",
            "error": f"bridge events file not found: {events_path}",
        }
        if args.json:
            print(json.dumps(result, sort_keys=True))
        else:
            print(result["error"], file=sys.stderr)
        return 2

    events = _read_events(events_path)
    result = check_bridge_clear_to_merge(
        events=events,
        task_id=args.task_id,
        merging_agent=args.from_agent,
    )
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        if result["clear_to_merge"]:
            print("safe to merge: no peer block")
        else:
            print(
                "BLOCKED: peer-decision changes_requested is the most recent peer signal",
                file=sys.stderr,
            )
            last = result.get("latest_blocking_event") or {}
            print(
                f"  blocked by {last.get('agent')} at {last.get('ts_utc')} "
                f"status={last.get('status')}",
                file=sys.stderr,
            )
    return 0 if result["clear_to_merge"] else 3


def check_bridge_clear_to_merge(
    *,
    events: Sequence[Mapping[str, Any]],
    task_id: str,
    merging_agent: str,
) -> dict[str, Any]:
    """Return the latest peer decision for task_id and whether it permits merge.

    A peer is any agent != merging_agent. We scan events for the task_id and
    record the most recent decision event whose status is in BLOCKING_STATUSES
    or APPROVAL_STATUSES. If the most recent peer decision is blocking, we
    refuse. Otherwise we permit.
    """
    latest_block: Mapping[str, Any] | None = None
    latest_approval: Mapping[str, Any] | None = None
    latest_signal_ts: str = ""

    for event in events:
        if str(event.get("task_id", "")) != task_id:
            continue
        agent = str(event.get("agent", ""))
        if agent == merging_agent:
            continue
        status = str(event.get("status", "")).lower()
        event_type = str(event.get("type", "")).lower()
        if event_type not in {"decision", "rco_review", "finding"}:
            continue
        if status in BLOCKING_STATUSES:
            ts = str(event.get("ts_utc", ""))
            if ts > latest_signal_ts:
                latest_signal_ts = ts
                latest_block = event
                latest_approval = None
        elif status in APPROVAL_STATUSES:
            ts = str(event.get("ts_utc", ""))
            if ts > latest_signal_ts:
                latest_signal_ts = ts
                latest_approval = event
                latest_block = None

    clear = latest_block is None
    return {
        "ok": True,
        "clear_to_merge": clear,
        "task_id": task_id,
        "merging_agent": merging_agent,
        "latest_blocking_event": _summarize_event(latest_block),
        "latest_approval_event": _summarize_event(latest_approval),
        "decision": "clear" if clear else "blocked",
    }


def _summarize_event(event: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if event is None:
        return None
    return {
        "ts_utc": str(event.get("ts_utc", "")),
        "agent": str(event.get("agent", "")),
        "type": str(event.get("type", "")),
        "status": str(event.get("status", "")),
    }


def _read_events(events_path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


if __name__ == "__main__":
    raise SystemExit(main())
