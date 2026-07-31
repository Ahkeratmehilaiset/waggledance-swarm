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
        --task-id <task_id> --from-agent claude --pr-number <pr_number> \\
        --bridge-root .agent-bridge

Exit codes:
  0  no peer block found, safe to merge
  3  peer block found, or complete PR scope was not provided
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
from waggledance.core.work_queue import resolve_bridge_root  # noqa: E402

# Cause-B fix (#1387 latch-bypass): authority for a TYPE-based veto comes from the
# single-source P2/D5 taxonomy, never a hardcoded copy (a divergent copy is the
# narrower-than-real-set trap that produced #1387). BLOCK_BY_TYPE = {finding,
# blocked}; RCO_GATED_TYPES = {finding} (a finding vetoes only from a recognized
# RCO). The recognized-RCO set is single-sourced from the RCO-pass verifier.
from tools.bridge_event_taxonomy import (  # noqa: E402
    BLOCK_BY_TYPE as _TAXONOMY_BLOCK_BY_TYPE,
    RCO_GATED_TYPES as _TAXONOMY_RCO_GATED_TYPES,
)

# Recognized RCO set per CLAUDE.md Rule 9a / the bridge-consensus contract. Defined
# locally (NOT imported) because ``tools.check_rco_pass_present`` imports THIS module
# -> importing it back would be a circular import. The canonical source is
# ``check_rco_pass_present.DEFAULT_RCO_AGENTS``; a drift-guard test asserts the two
# stay equal (test-time import, no cycle).
_RECOGNIZED_RCOS = frozenset({"claude-rco-1", "claude-rco-2"})


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
BLOCKING_EVENT_TYPES = frozenset(
    {"decision", "rco_review", "finding", "blocked", "test"}
)
CLEAR_EVENT_TYPES = frozenset({"decision", "rco_review", "finding", "done", "test"})
BLOCKING_CLEAR_TOKENS = frozenset({"clear", "cleared"})
BLOCKING_RESOLUTION_TOKENS = frozenset(
    {"clear", "cleared", "resolved", "retracted", "withdrawn"}
)
BLOCKING_RESOLUTION_NEGATION_TOKENS = frozenset(
    {
        "active",
        "arent",
        "cannot",
        "cant",
        "denied",
        "failed",
        "failing",
        "fails",
        "incomplete",
        "isnt",
        "never",
        "no",
        "not",
        "open",
        "ongoing",
        "outstanding",
        "persist",
        "persistent",
        "persisting",
        "persists",
        "refused",
        "rejected",
        "still",
        "uncleared",
        "unresolved",
        "unretracted",
        "unwithdrawn",
        "wont",
        "without",
        "yet",
    }
)
BLOCKING_CLEAR_COORDINATION_TOKENS = frozenset(
    {"needed", "required", "request", "requested", "supersede", "superseded"}
)
BLOCKING_WORD_TOKENS = frozenset({"block", "blocked", "blocks", "blocking"})
NON_BLOCKING_BLOCK_PHRASES = frozenset(
    {
        "not_blocked",
        "not_blocking",
        "not_a_blocker",
    }
)
NO_CHANGES_REQUESTED_CLEAR_STATUSES = frozenset(
    {
        "no_changes_requested",
        "no_changes_requested_approved",
    }
)
CHANGES_REQUESTED_EXACT_BLOCK_PREFIXES = (
    "changes_requested",
    "rco_changes_requested",
)
CHANGES_REQUESTED_NON_BLOCKING_SUFFIXES = frozenset(
    {
        "concurrence",
        "payload_corrected",
        "addressed_exact_head_ci_pending",
        "resolved",
        "resolved_ci_green",
        "resolved_ci_pending",
        "cleared",
        "cleared_ci_green",
        "cleared_ci_pending",
        "block_clear",
        "block_cleared",
        "block_resolved",
        "retracted",
        "withdrawn",
    }
)
NON_BLOCKING_CONTEXT_STATUS_PREFIXES = (
    "ack_",
    "acknowledged_",
    "answered_",
    "received_",
)
NON_BLOCKING_CONTEXT_STATUS_SEGMENTS = (
    "_advisory_",
    "_corrected_",
    "_correction_",
    "_forwarded_",
    "_no_remaining_issues_",
    "_open_followup_",
    "_resolves_",
    "_still_monitoring_",
)
NO_BLOCK_CLEAR_STATUSES = frozenset(
    {
        "approved_waiver_block_cleared",
        "lead_no_blocker_rco_pending",
        "producer_no_block_reemit_required",
    }
)
APPROVAL_STATUSES = frozenset(
    {
        "rco_pass",
        "rco_pass_pending_ci",
        # rco_pass_operator_merge_required is RETIRED (2026-06-20): RCO status is
        # pass/block ONLY; the merge PATH is set by charter (allowlist =
        # autonomous-ok; denylist/off-allowlist = operator-sign), never by an RCO
        # status variant. RCOs post plain rco_pass and convey operator-merge in
        # the message. Dropping it here is the retirement signal; behaviour stays
        # safe either way -- _is_approval_status's generic {rco,pass} token
        # fallback still treats a stray variant as a block-clearing approval, and
        # check_rco_pass_present intentionally does NOT recognize it as a
        # qualifying pass, so a stray variant fails toward STUCK, never open.
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
        "--author-agent",
        default="",
        help=(
            "Optional PR author agent. When provided, author approval signals "
            "are ignored as self-review, author clear/retraction signals can "
            "clear that author's prior block, and author blocking or "
            "changes-requested signals remain authoritative."
        ),
    )
    parser.add_argument(
        "--pr-number",
        type=int,
        default=None,
        help=(
            "Positive pull request number required for complete merge scope. "
            "It remains syntactically optional so a missing value can return "
            "a structured fail-closed decision."
        ),
    )
    parser.add_argument(
        "--bridge-root",
        type=Path,
        default=None,
        help=(
            "Path to the runtime .agent-bridge directory. Defaults to "
            "AGENT_BRIDGE_RUNTIME_ROOT / AGENT_BRIDGE_ROOT when set, then "
            "repo-local .agent-bridge."
        ),
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
    if args.pr_number is None or args.pr_number <= 0:
        result = {
            "ok": False,
            "clear_to_merge": False,
            "decision": "scope_incomplete",
            "task_id": args.task_id,
            "pr_number": args.pr_number,
            "merging_agent": args.from_agent,
            "author_agent": (args.author_agent or "").strip(),
            "latest_blocking_event": None,
            "latest_approval_event": None,
            "error": (
                "--pr-number must be a positive integer to evaluate "
                "complete PR scope"
            ),
        }
        if args.json:
            print(json.dumps(result, sort_keys=True))
        else:
            print("BLOCKED: scope_incomplete", file=sys.stderr)
            print(result["error"], file=sys.stderr)
        return 3

    bridge_root = resolve_bridge_root(args.bridge_root)
    events_path = bridge_root / "shared" / "events.jsonl"
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
        author_agent=args.author_agent,
        pr_number=args.pr_number,
    )
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        if result["clear_to_merge"]:
            print("safe to merge: no peer block")
        else:
            if result.get("decision") == "invalid_identity_registry":
                print("BLOCKED: bridge identity registry is invalid", file=sys.stderr)
            else:
                print(
                    "BLOCKED: peer-decision changes_requested is the most recent peer signal",
                    file=sys.stderr,
                )
            if result.get("error"):
                print(result["error"], file=sys.stderr)
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
    author_agent: str = "",
    pr_number: int | None = None,
    identity_registry: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return the latest peer decision for task_id and whether it permits merge.

    We scan events for the task_id and optional PR number, then record the most
    recent authoritative signal. ``author_agent`` is asymmetric: author
    approval signals are ignored as self-review, author clear/retraction
    signals can clear that author's own prior block, and author-originated
    blocking or changes-requested signals still count. If the most recent
    authoritative signal is blocking, we refuse. Otherwise we permit.
    """
    author_agent = (author_agent or "").strip()
    try:
        registry = (
            load_bridge_identity_registry()
            if identity_registry is None
            else dict(identity_registry)
        )
    except ValueError as exc:
        return {
            "ok": False,
            "clear_to_merge": False,
            "task_id": task_id,
            "pr_number": pr_number,
            "merging_agent": merging_agent,
            "author_agent": author_agent,
            "latest_blocking_event": None,
            "latest_approval_event": None,
            "ignored_identity_mismatch_events": [],
            "unverified_rco_block_events": [],
            "ignored_author_events": [],
            "decision": "invalid_identity_registry",
            "error": str(exc),
        }
    peer_signals: dict[str, tuple[int, str, Mapping[str, Any]]] = {}
    ignored_identity_mismatch_events: list[dict[str, Any]] = []
    unverified_rco_block_events: list[dict[str, Any]] = []
    ignored_author_events: list[dict[str, Any]] = []

    for index, event in enumerate(events):
        if not _event_matches_scope(event, task_id=task_id, pr_number=pr_number):
            continue
        agent = str(event.get("agent", ""))
        if agent == merging_agent:
            continue
        author_event = bool(author_agent and agent == author_agent)
        binding_status = bridge_identity_binding_status(
            event,
            registry=registry,
        )
        if binding_status in {"missing_uuid", "mismatch_uuid"}:
            status = str(event.get("status", "")).lower()
            event_type = str(event.get("type", "")).lower()
            # Fail-closed veto asymmetry: identity binding exists to stop
            # FORGED approvals/clears, so those stay ignored when the uuid
            # does not verify. But silently dropping an unverified VETO from
            # a recognized-RCO NAME inverts the contract ("veto outranks a
            # pass"): registry drift on one RCO would disable its absolute
            # veto while the other RCO's verified pass still enables merges.
            # A block-shaped event from a recognized RCO name therefore
            # latches as a blocker even when unverified - the worst case of
            # honoring a forged veto is a spurious hold (safe), while the
            # worst case of dropping a real one is a merge past a live veto
            # (unsafe). Only a later VERIFIED signal from the same identity
            # can clear it (latest-signal-wins below never admits unverified
            # clears).
            block_shaped = (
                event_type in _TAXONOMY_BLOCK_BY_TYPE
                and (
                    event_type not in _TAXONOMY_RCO_GATED_TYPES
                    or agent in _RECOGNIZED_RCOS
                )
            ) or _is_blocking_status(status, event_type=event_type)
            if agent in _RECOGNIZED_RCOS and block_shaped:
                summary = _summarize_event(event)
                if summary is not None:
                    summary["identity_binding_status"] = binding_status
                    summary["unverified_veto_fail_closed"] = True
                    unverified_rco_block_events.append(summary)
                peer_signals[agent] = (index, "block", event)
                continue
            summary = _summarize_event(event)
            if summary is not None:
                summary["identity_binding_status"] = binding_status
                ignored_identity_mismatch_events.append(summary)
            continue
        status = str(event.get("status", "")).lower()
        event_type = str(event.get("type", "")).lower()
        # Only authoritative review/finding event types can veto. Plain
        # bridge conversation often includes diagnostic status strings with
        # "block"/"clear" words and must not become a phantom merge stop.
        # Approvals stay type-restricted.
        if _is_clear_status(status):
            if event_type in CLEAR_EVENT_TYPES:
                if author_event:
                    peer_signals[agent] = (index, "clear", event)
                    continue
                existing = peer_signals.get(agent)
                if existing is None or existing[1] != "approval":
                    peer_signals[agent] = (index, "clear", event)
            continue
        # Cause-B fix (#1387 latch-bypass): a recognized-RCO ``finding`` (and any
        # ``blocked``-type event) is a veto BY TYPE -- it latches regardless of its
        # free-text status. The #1387 vector was a recognized-RCO finding whose
        # status carried a "content_pass" token (no block-vocab), so the
        # status-string classifier below silently failed open and the PR
        # auto-merged past a live RCO veto. Authority here is the event TYPE +
        # RCO identity per the single-source P2/D5 taxonomy
        # (``BLOCK_BY_TYPE`` / ``RCO_GATED_TYPES``), never the status string. An
        # explicit clear/retraction status is handled above (so an RCO can still
        # retract via a clear); the standing veto is otherwise cleared only by a
        # later ``decision`` rco_pass from the SAME RCO (latest-signal-wins below).
        if event_type in _TAXONOMY_BLOCK_BY_TYPE and (
            event_type not in _TAXONOMY_RCO_GATED_TYPES
            or agent in _RECOGNIZED_RCOS
        ):
            peer_signals[agent] = (index, "block", event)
            continue
        if _is_blocking_status(status, event_type=event_type):
            peer_signals[agent] = (index, "block", event)
            continue
        if event_type == "done" and status not in DONE_APPROVAL_STATUSES:
            continue
        if event_type not in {"decision", "rco_review", "finding", "done"}:
            continue
        if _is_clear_status(status):
            if author_event:
                summary = _summarize_event(event)
                if summary is not None:
                    ignored_author_events.append(summary)
                continue
            peer_signals.pop(agent, None)
            continue
        if _is_approval_status(status):
            if author_event:
                summary = _summarize_event(event)
                if summary is not None:
                    ignored_author_events.append(summary)
                continue
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
        "author_agent": author_agent,
        "latest_blocking_event": _summarize_event(
            latest_block[1] if latest_block is not None else None
        ),
        "latest_approval_event": _summarize_event(
            latest_approval[1] if latest_approval is not None else None
        ),
        "ignored_identity_mismatch_events": ignored_identity_mismatch_events,
        "unverified_rco_block_events": unverified_rco_block_events,
        "ignored_author_events": ignored_author_events,
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


def _has_non_blocking_block_phrase(status: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", status.lower()).strip("_")
    if (
        normalized.startswith(("block_", "blocked_", "rco_block"))
        or "block_requested" in normalized
        or "changes_requested" in normalized
    ):
        return False
    bounded = f"_{normalized}_"
    return any(f"_{phrase}_" in bounded for phrase in NON_BLOCKING_BLOCK_PHRASES)


def _is_clear_status(status: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", status.lower()).strip("_")
    if normalized in NO_CHANGES_REQUESTED_CLEAR_STATUSES:
        return True
    if normalized in NO_BLOCK_CLEAR_STATUSES:
        return True
    for prefix in CHANGES_REQUESTED_EXACT_BLOCK_PREFIXES:
        if not normalized.startswith(prefix + "_"):
            continue
        suffix = normalized[len(prefix) + 1 :]
        return suffix in CHANGES_REQUESTED_NON_BLOCKING_SUFFIXES
    return False


def _is_blocking_status(status: str, *, event_type: str = "") -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", status.lower()).strip("_")
    normalized_event_type = re.sub(r"[^a-z0-9]+", "_", event_type.lower()).strip("_")
    if normalized_event_type and normalized_event_type not in BLOCKING_EVENT_TYPES:
        return False
    if normalized in BLOCKING_STATUSES:
        return True
    for prefix in CHANGES_REQUESTED_EXACT_BLOCK_PREFIXES:
        if normalized == prefix:
            return True
        if not normalized.startswith(prefix + "_"):
            continue
        suffix = normalized[len(prefix) + 1 :]
        if not suffix:
            return True
        if suffix in CHANGES_REQUESTED_NON_BLOCKING_SUFFIXES:
            return False
        return True
    if _is_clear_status(status):
        return False
    if _has_non_blocking_context_status(status):
        return False
    if _has_non_blocking_block_phrase(status):
        return False
    tokens = _status_tokens(status)
    if {"changes", "requested"}.issubset(tokens):
        return True
    if not tokens.intersection(BLOCKING_WORD_TOKENS):
        return False
    if "rco" in tokens:
        return True
    if tokens.intersection(BLOCKING_RESOLUTION_TOKENS):
        if tokens.intersection(BLOCKING_RESOLUTION_NEGATION_TOKENS):
            return True
        return False
    if "preflight" in tokens and tokens.intersection(BLOCKING_CLEAR_TOKENS):
        return False
    if tokens.intersection(BLOCKING_CLEAR_TOKENS) and tokens.intersection(
        BLOCKING_CLEAR_COORDINATION_TOKENS
    ):
        return False
    if (
        {"classifier", "artifact"}.issubset(tokens)
        and "veto" in tokens
        and tokens.intersection({"no", "false"})
    ):
        return False
    return True


def _has_non_blocking_context_status(status: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", status.lower()).strip("_")
    if normalized.startswith(NON_BLOCKING_CONTEXT_STATUS_PREFIXES):
        return True
    bounded = f"_{normalized}_"
    return any(segment in bounded for segment in NON_BLOCKING_CONTEXT_STATUS_SEGMENTS)


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
        events_path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"invalid JSON in bridge events at line {line_number}: {exc.msg}"
            ) from exc
        if event is None:
            continue
        if not isinstance(event, dict):
            raise ValueError(
                f"invalid bridge event at line {line_number}: event must be a JSON object"
            )
        events.append(event)
    return events


if __name__ == "__main__":
    raise SystemExit(main())
