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
overrides an older block). Build-consensus pass events also clear an older
block from the same peer; this keeps the peer-veto gate aligned with the
promotion-consensus vocabulary.

Designed to be called BEFORE ``gh pr merge --squash --match-head-commit`` and
ANDed with ``tools/check_rco_pass_present.py``. Absence of a peer block is not
an approval signal by itself.

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
import re
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.bridge_identity_registry import (  # noqa: E402
    bridge_identity_binding_status,
    load_bridge_identity_registry,
)


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
BLOCKING_CLEAR_TOKENS = frozenset({"clear", "cleared"})
BLOCKING_WORD_TOKENS = frozenset({"block", "blocked", "blocks", "blocking"})
NO_CHANGES_REQUESTED_CLEAR_STATUSES = frozenset(
    {
        "no_changes_requested",
        "no_changes_requested_approved",
    }
)
NO_BLOCK_CLEAR_STATUSES = frozenset(
    {
        "lead_no_blocker_rco_pending",
        "producer_no_block_reemit_required",
    }
)
APPROVAL_STATUSES = frozenset(
    {
        "rco_pass",
        "rco_pass_pending_ci",
        "rco_pass_operator_merge_required",
        "build_consensus_pass",
        "approved",
        "approved_ci_green",
        "acknowledged",
    }
)
DONE_APPROVAL_STATUSES = frozenset({"approved_ci_green"})


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
        "--pr-number",
        type=int,
        default=None,
        help=(
            "Optional pull request number. When provided, peer blocks whose "
            "payload or task_id identifies this PR also block even if their "
            "bridge task_id differs from the implementation task."
        ),
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

    try:
        events = _read_events(events_path)
    except ValueError as exc:
        result = {
            "ok": False,
            "decision": "invalid_events_file",
            "error": str(exc),
        }
        if args.json:
            print(json.dumps(result, sort_keys=True))
        else:
            print(result["error"], file=sys.stderr)
        return 2
    result = check_bridge_clear_to_merge(
        events=events,
        task_id=args.task_id,
        merging_agent=args.from_agent,
        pr_number=args.pr_number,
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
    pr_number: int | None = None,
    identity_registry: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return the latest peer decision for task_id and whether it permits merge.

    A peer is any agent != merging_agent. We scan events for the task_id and
    optional PR number, then record the most recent decision event whose status
    is in BLOCKING_STATUSES or APPROVAL_STATUSES. If the most recent peer
    decision is blocking, we refuse. Otherwise we permit.
    """
    registry = (
        load_bridge_identity_registry()
        if identity_registry is None
        else dict(identity_registry)
    )
    peer_signals: dict[str, tuple[int, str, Mapping[str, Any]]] = {}
    ignored_identity_mismatch_events: list[dict[str, Any]] = []

    for index, event in enumerate(events):
        if not _event_matches_scope(event, task_id=task_id, pr_number=pr_number):
            continue
        agent = str(event.get("agent", ""))
        if agent == merging_agent:
            continue
        binding_status = bridge_identity_binding_status(
            event,
            registry=registry,
        )
        if binding_status in {"missing_uuid", "mismatch_uuid"}:
            summary = _summarize_event(event)
            if summary is not None:
                summary["identity_binding_status"] = binding_status
                ignored_identity_mismatch_events.append(summary)
            continue
        status = str(event.get("status", "")).lower()
        event_type = str(event.get("type", "")).lower()
        # Block detection is TYPE-AGNOSTIC (fail-closed): a peer veto must
        # register regardless of the event type used, so a block posted as
        # e.g. type=blocked cannot be silently dropped by the type filter and
        # let a stale approval stand. Approvals stay type-restricted.
        if _is_blocking_status(status):
            peer_signals[agent] = (index, "block", event)
            continue
        if event_type == "done" and status not in DONE_APPROVAL_STATUSES:
            continue
        if event_type not in {"decision", "rco_review", "finding", "done"}:
            continue
        if _is_approval_status(status):
            peer_signals[agent] = (index, "approval", event)

    blocking_events = [
        (index, event)
        for index, kind, event in peer_signals.values()
        if kind == "block"
    ]
    approval_events = [
        (index, event)
        for index, kind, event in peer_signals.values()
        if kind == "approval"
    ]
    latest_block = max(blocking_events, default=None, key=lambda item: item[0])
    latest_approval = max(approval_events, default=None, key=lambda item: item[0])
    clear = latest_block is None
    return {
        "ok": True,
        "clear_to_merge": clear,
        "task_id": task_id,
        "pr_number": pr_number,
        "merging_agent": merging_agent,
        "latest_blocking_event": _summarize_event(
            latest_block[1] if latest_block is not None else None
        ),
        "latest_approval_event": _summarize_event(
            latest_approval[1] if latest_approval is not None else None
        ),
        "ignored_identity_mismatch_events": ignored_identity_mismatch_events,
        "decision": "clear" if clear else "blocked",
    }


def _event_matches_scope(
    event: Mapping[str, Any], *, task_id: str, pr_number: int | None
) -> bool:
    if str(event.get("task_id", "")) == task_id:
        return True
    if pr_number is None:
        return False
    return _event_mentions_pr(event, pr_number)


def _event_mentions_pr(event: Mapping[str, Any], pr_number: int) -> bool:
    payload = event.get("payload")
    if isinstance(payload, Mapping):
        for key in ("pr", "pr_number", "pull_request", "pull_request_number"):
            value = payload.get(key)
            if value == pr_number:
                return True
            if isinstance(value, str) and value.strip() == str(pr_number):
                return True

    pattern = re.compile(rf"(?i)(?:\bpr\s*#?\s*|#){pr_number}\b")
    return pattern.search(str(event.get("task_id", ""))) is not None


def _status_tokens(status: str) -> set[str]:
    return {
        token
        for token in re.split(r"[^a-z0-9]+", status.lower())
        if token
    }


def _is_no_changes_requested_status(status: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", status.lower()).strip("_")
    return normalized in NO_CHANGES_REQUESTED_CLEAR_STATUSES


def _is_no_block_status(status: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", status.lower()).strip("_")
    return normalized in NO_BLOCK_CLEAR_STATUSES


def _is_blocking_status(status: str) -> bool:
    if status in BLOCKING_STATUSES:
        return True
    if _is_no_changes_requested_status(status) or _is_no_block_status(status):
        return False
    tokens = _status_tokens(status)
    if {"changes", "requested"}.issubset(tokens):
        return True
    if not tokens.intersection(BLOCKING_WORD_TOKENS):
        return False
    if "preflight" in tokens and tokens.intersection(BLOCKING_CLEAR_TOKENS):
        return False
    return True


def _is_approval_status(status: str) -> bool:
    if status in APPROVAL_STATUSES:
        return True
    tokens = _status_tokens(status)
    return (
        {"rco", "pass"}.issubset(tokens)
        or "approved" in tokens
        or "acknowledged" in tokens
    )


def _summarize_event(event: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if event is None:
        return None
    return {
        "ts_utc": str(event.get("ts_utc", "")),
        "agent": str(event.get("agent", "")),
        "agent_uuid": str(event.get("agent_uuid", "")),
        "type": str(event.get("type", "")),
        "status": str(event.get("status", "")),
    }


def _read_events(events_path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        events_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"invalid JSON in bridge events at line {line_number}: {exc.msg}"
            ) from exc
        if not isinstance(event, dict):
            raise ValueError(
                f"invalid bridge event at line {line_number}: event must be a JSON object"
            )
        events.append(event)
    return events


if __name__ == "__main__":
    raise SystemExit(main())
