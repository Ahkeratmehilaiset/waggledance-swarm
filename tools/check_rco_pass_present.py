# SPDX-License-Identifier: BUSL-1.1
"""Rule 9a fail-closed RCO_PASS presence gate verifier.

Enforces CLAUDE.md Rule 9a: "RCO absence = NO merge". A valid
`claude-rco-1` (or --rco-agent) `RCO_PASS` (type=decision or rco_review,
status=rco_pass) whose *message* contains the exact --head SHA string
must be present for the --task-id (canonical branch name) at the
*exact* --head. 

Fail-closed rules (per spec):
1. Scan only events authored by --rco-agent on the given --task-id.
2. A PASS counts ONLY if type in {decision, rco_review}, status in {rco_pass},
   AND message contains the exact --head (40-char SHA) string.
3. If the MOST RECENT rco-agent event on task_id is a veto
   (changes_requested / finding / blocked / rco_block* etc), REFUSE
   regardless of any earlier pass.
4. If NO qualifying RCO_PASS-at-exact-head exists, REFUSE. Silence/absence
   must REFUSE; never default-allow.
5. Exit 0 ONLY when a valid head-bound RCO_PASS is present and not
   superseded by a later veto from the rco-agent; else non-zero.

This tool is intended to be AND-ed with tools/check_bridge_changes_requested.py
in the merge step: merge only if (no peer/rco block from the changes tool)
AND (RCO_PASS present at exact head from this tool).

All claim gates are emitted false (see leak_policy CLAIM_GATES).
Offline, deterministic, no network. SHA must be exactly 40 lowercase hex chars.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence


DEFAULT_EVENTS_PATH = Path(".agent-bridge") / "shared" / "events.jsonl"
RCO_PASS_STATUSES = frozenset({"rco_pass"})
DECISION_TYPES_FOR_PASS = frozenset({"decision", "rco_review"})

# Veto detection (fail-closed, mirrors blocking logic from sibling gate)
BLOCKING_STATUSES = frozenset(
    {
        "changes_requested",
        "rco_block",
        "blocked",
        "rco_blocked",
        "block_requested",
    }
)
BLOCKING_NEGATION_TOKENS = frozenset({"no", "not", "non", "none", "without"})

# Claim gates per hard rule: all must be false in emitted artifacts.
CLAIM_GATES: tuple[str, ...] = (
    "claim_gate_satisfied",
    "claim_safe",
    "literal_future_claim_safe",
    "controls_present",
    "runtime_authority_granted",
    "external_writes_applied",
    "required_runtime_evidence_present",
)


SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fail-closed verifier for claude-rco-1 RCO_PASS presence at exact head "
            "on canonical task_id (Rule 9a: RCO absence = NO merge). "
            "Exit 0 only on valid head-bound pass with no later veto; else non-zero."
        ),
    )
    parser.add_argument(
        "--task-id",
        required=True,
        help="Canonical bridge task_id (branch name) the RCO reviewed.",
    )
    parser.add_argument(
        "--head",
        required=True,
        help="Exact 40-char lowercase SHA of the PR head the RCO_PASS must bind to.",
    )
    parser.add_argument(
        "--events",
        type=Path,
        default=DEFAULT_EVENTS_PATH,
        help="Path to bridge events.jsonl (default: .agent-bridge/shared/events.jsonl)",
    )
    parser.add_argument(
        "--rco-agent",
        default="claude-rco-1",
        help="RCO agent identity to require (default: claude-rco-1)",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON result to stdout")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    task_id = (args.task_id or "").strip()
    head = (args.head or "").strip().lower()
    rco_agent = (args.rco_agent or "").strip()

    if not task_id:
        print("--task-id must not be empty", file=sys.stderr)
        return 2
    if not SHA_RE.fullmatch(head):
        print("--head must be a 40-char lowercase hex SHA", file=sys.stderr)
        return 2
    if not rco_agent:
        print("--rco-agent must not be empty", file=sys.stderr)
        return 2

    events_path: Path = args.events
    if not events_path.exists():
        result = _make_result(
            ok=False,
            decision="no_events_file",
            task_id=task_id,
            head=head,
            rco_agent=rco_agent,
            error=f"bridge events file not found: {events_path}",
            has_qualifying_rco_pass_at_head=False,
            latest_rco_is_veto=None,
        )
        _emit(result, args.json)
        return 3

    try:
        events = _read_events(events_path)
    except ValueError as exc:
        result = _make_result(
            ok=False,
            decision="invalid_events_file",
            task_id=task_id,
            head=head,
            rco_agent=rco_agent,
            error=str(exc),
            has_qualifying_rco_pass_at_head=False,
            latest_rco_is_veto=None,
        )
        _emit(result, args.json)
        return 2

    result = check_rco_pass_present(
        events=events,
        task_id=task_id,
        head=head,
        rco_agent=rco_agent,
    )
    _emit(result, args.json)

    if result.get("ok") and result.get("has_qualifying_rco_pass_at_head"):
        return 0
    # Distinguish arg/invalid (2) vs. gate refuse (3) - here gate cases use 3
    return 3 if result.get("decision") in {"rco_pass_absent", "vetoed_after_pass", "no_qualifying_pass"} else 2


def _emit(result: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, sort_keys=True))
    else:
        if result.get("ok") and result.get("has_qualifying_rco_pass_at_head"):
            print(
                f"RCO_PASS present at exact head {result.get('head')} "
                f"for task {result.get('task_id')} (agent {result.get('rco_agent')})"
            )
        else:
            print(f"REFUSED: {result.get('decision')}", file=sys.stderr)
            if result.get("error"):
                print(result["error"], file=sys.stderr)
            latest = result.get("latest_rco_event") or {}
            if latest:
                print(
                    f"  latest rco event: agent={latest.get('agent')} "
                    f"status={latest.get('status')} type={latest.get('type')}",
                    file=sys.stderr,
                )


def _make_result(
    *,
    ok: bool,
    decision: str,
    task_id: str,
    head: str,
    rco_agent: str,
    has_qualifying_rco_pass_at_head: bool,
    latest_rco_is_veto: bool | None,
    error: str | None = None,
    rco_pass_event: Mapping[str, Any] | None = None,
    latest_rco_event: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ok": bool(ok),
        "decision": decision,
        "task_id": task_id,
        "head": head,
        "rco_agent": rco_agent,
        "has_qualifying_rco_pass_at_head": bool(has_qualifying_rco_pass_at_head),
        "latest_rco_is_veto": latest_rco_is_veto,
        "rco_pass_event": _summarize_event(rco_pass_event) if rco_pass_event is not None else None,
        "latest_rco_event": _summarize_event(latest_rco_event) if latest_rco_event is not None else None,
        "error": error,
    }
    # Hard rule: emit all claim gates as false in every artifact.
    for key in CLAIM_GATES:
        base[key] = False
    return base


def check_rco_pass_present(
    *,
    events: Sequence[Mapping[str, Any]],
    task_id: str,
    head: str,
    rco_agent: str = "claude-rco-1",
) -> dict[str, Any]:
    """Return whether a valid RCO_PASS at exact head exists for the rco-agent on task_id.

    Fail-closed: absence, wrong identity, wrong head, or later veto from rco-agent -> not ok.
    Latest is determined by append order in events list (index), not wallclock ts.
    """
    task_id = (task_id or "").strip()
    head = (head or "").strip().lower()
    rco_agent = (rco_agent or "").strip()

    base: dict[str, Any] = {
        "ok": False,
        "decision": "rco_pass_absent",
        "task_id": task_id,
        "head": head,
        "rco_agent": rco_agent,
        "has_qualifying_rco_pass_at_head": False,
        "latest_rco_is_veto": None,
        "rco_pass_event": None,
        "latest_rco_event": None,
        "error": None,
    }
    for key in CLAIM_GATES:
        base[key] = False

    if not task_id:
        base["decision"] = "invalid_task_id"
        base["error"] = "task_id must not be empty"
        return base
    if not SHA_RE.fullmatch(head):
        base["decision"] = "invalid_head"
        base["error"] = "head must be a 40-char lowercase hex SHA"
        return base
    if not rco_agent:
        base["decision"] = "invalid_rco_agent"
        base["error"] = "rco_agent must not be empty"
        return base

    # Collect all rco-agent events scoped to this task_id (exact match per spec)
    rco_events: list[tuple[int, Mapping[str, Any]]] = []
    for index, event in enumerate(events):
        if not isinstance(event, Mapping):
            continue
        if str(event.get("task_id", "")) != task_id:
            continue
        if str(event.get("agent", "")) != rco_agent:
            continue
        rco_events.append((index, event))

    if not rco_events:
        base["decision"] = "no_rco_events_for_task"
        base["latest_rco_is_veto"] = False
        return base

    # Most recent by list order (highest index)
    latest_idx, latest_ev = max(rco_events, key=lambda item: item[0])
    base["latest_rco_event"] = _summarize_event(latest_ev)
    latest_is_veto = _is_rco_veto_event(latest_ev)
    base["latest_rco_is_veto"] = latest_is_veto

    # Find qualifying head-bound passes (type-restricted, status=rco_pass, message contains exact head)
    qualifying: list[tuple[int, Mapping[str, Any]]] = []
    for idx, ev in rco_events:
        if _is_qualifying_rco_pass(ev, head, rco_agent):
            qualifying.append((idx, ev))

    if not qualifying:
        base["decision"] = "no_qualifying_pass"
        base["has_qualifying_rco_pass_at_head"] = False
        return base

    latest_pass_idx, latest_pass_ev = max(qualifying, key=lambda item: item[0])
    base["rco_pass_event"] = _summarize_event(latest_pass_ev)
    base["has_qualifying_rco_pass_at_head"] = True

    # Check for any later veto after the (latest) qualifying pass
    has_later_veto = any(
        idx > latest_pass_idx and _is_rco_veto_event(ev) for idx, ev in rco_events
    )

    if has_later_veto or latest_is_veto:
        # latest_is_veto covers the "most recent is veto" case; has_later_veto covers
        # veto strictly after the pass (even if something non-veto after veto, but
        # if most recent after veto is non-veto, still had later veto after pass)
        base["ok"] = False
        base["decision"] = "vetoed_after_pass"
        return base

    # Valid: pass at exact head present, and no veto after it
    base["ok"] = True
    base["decision"] = "rco_pass_present"
    return base


def _is_qualifying_rco_pass(event: Mapping[str, Any], head: str, rco_agent: str) -> bool:
    if str(event.get("agent", "")) != rco_agent:
        return False
    typ = str(event.get("type", "")).lower()
    if typ not in DECISION_TYPES_FOR_PASS:
        return False
    status = str(event.get("status", "")).lower()
    if status not in RCO_PASS_STATUSES:
        return False
    message = str(event.get("message", "") or "")
    # Per spec: message contains the exact --head SHA string.
    # Use substring match on the provided head (caller normalizes to lower hex).
    if head not in message:
        # Also accept if message has it case-insensitively for robustness with logs,
        # but primary is exact string containment as specified.
        if head.lower() not in message.lower():
            return False
    return True


def _is_rco_veto_event(event: Mapping[str, Any]) -> bool:
    """Fail-closed veto detection for RCO signals.

    Treats status-based blocks (changes_requested etc) and type=finding/blocked
    as vetoes per the "changes_requested/finding/blocked (a veto)" rule.
    Type-agnostic for blocks to avoid silent bypass via non-decision types.
    """
    status = str(event.get("status", "") or "").lower()
    typ = str(event.get("type", "") or "").lower()

    if _is_blocking_status(status):
        return True

    # Explicitly mentioned in spec: finding / blocked count as veto indicators
    if typ in {"finding", "blocked"}:
        # If the status is explicitly an approval, do not treat as veto (rare)
        if status in RCO_PASS_STATUSES or _is_approval_status(status):
            return False
        return True

    # Any status that lexically signals block (e.g. "rco_changes_requested_xxx")
    if _has_blocking_shape(status):
        return True

    return False


def _has_blocking_shape(status: str) -> bool:
    tokens = _status_tokens(status)
    has_shape = (
        {"changes", "requested"}.issubset(tokens)
        or any(token.startswith("block") for token in tokens)
        or "finding" in tokens  # treat "finding" status shape as potential veto signal
    )
    if not has_shape:
        return False
    return not tokens.intersection(BLOCKING_NEGATION_TOKENS)


def _is_blocking_status(status: str) -> bool:
    if status in BLOCKING_STATUSES:
        return True
    return _has_blocking_shape(status)


def _is_approval_status(status: str) -> bool:
    if status in RCO_PASS_STATUSES:
        return True
    tokens = _status_tokens(status)
    return (
        {"rco", "pass"}.issubset(tokens)
        or "approved" in tokens
        or "acknowledged" in tokens
    )


def _status_tokens(status: str) -> set[str]:
    return {
        token
        for token in re.split(r"[^a-z0-9]+", status.lower())
        if token
    }


def _summarize_event(event: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if event is None:
        return None
    return {
        "ts_utc": str(event.get("ts_utc", "")),
        "agent": str(event.get("agent", "")),
        "type": str(event.get("type", "")),
        "status": str(event.get("status", "")),
        "task_id": str(event.get("task_id", "")),
    }


def _read_events(events_path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    text = events_path.read_text(encoding="utf-8")
    for line_number, line in enumerate(text.splitlines(), start=1):
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
