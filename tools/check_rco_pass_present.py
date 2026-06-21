# SPDX-License-Identifier: BUSL-1.1
"""Rule 9a fail-closed RCO_PASS presence gate verifier.

Enforces CLAUDE.md Rule 9a: "RCO absence = NO merge". A valid
recognized RCO `RCO_PASS` (type=decision or rco_review, status=rco_pass)
whose *message* contains the exact --head SHA string, or whose structured
``payload.exact_head`` equals it, must be present for
the --task-id (canonical branch name) at the *exact* --head. The passing
RCO must not be the PR author.

Fail-closed rules (per spec):
1. Scan only events authored by the recognized --rco-agent set on the given --task-id.
   A deterministic author-agent namespace alias is accepted to avoid stalls
   when agents write `author-agent/rest` while the queue supplied
   `author-agent-rest`, or the reverse. No other task-id mismatch counts.
2. A PASS counts ONLY if type in {decision, rco_review}, status in {rco_pass},
   AND message contains the exact --head (40-char SHA) string or
   payload.exact_head equals it.
3. If a recognized RCO veto supersedes the satisfying RCO_PASS
   (changes_requested / finding / blocked / rco_block* etc), REFUSE.
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

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.bridge_identity_registry import (  # noqa: E402
    bridge_identity_binding_status,
    load_bridge_identity_registry,
)
from waggledance.core.work_queue import resolve_bridge_root  # noqa: E402
from tools.check_bridge_changes_requested import (  # noqa: E402
    _is_blocking_status as _bridge_is_blocking_status,
    _is_clear_status as _bridge_is_clear_status,
)

DEFAULT_EVENTS_PATH = Path(".agent-bridge") / "shared" / "events.jsonl"
RCO_PASS_STATUSES = frozenset({"rco_pass"})
DECISION_TYPES_FOR_PASS = frozenset({"decision", "rco_review"})
DEFAULT_RCO_AGENTS: tuple[str, ...] = ("claude-rco-1", "claude-rco-2")

BRIDGE_BLOCK_WORD_TOKENS = frozenset({"block", "blocked", "blocks", "blocking"})

# Unambiguously INFORMATIONAL finding statuses. A recognized-RCO ``type=finding``
# carrying one of these statuses is an advisory/diagnostic note, NOT a veto, so it
# must not phantom-block a prior exact-head ``rco_pass`` (the ``finding/info``
# vetoed_after_pass bug). This set is intentionally TIGHT and fail-closed: any
# finding status NOT in this set (and not an explicit approval) still counts as a
# veto, so real vetoes (``changes_requested``/``blocked``/``confirmed_*``) and
# ambiguous statuses (``operator_review_required``/``open``/empty) never silently
# bypass the gate. Expand deliberately if a specific advisory status should also
# stop vetoing.
INFORMATIONAL_FINDING_STATUSES = frozenset(
    {
        "info",
        "informational",
        "fyi",
        "note",
        "advisory",
    }
)

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
HEAD_CANDIDATE_RE = re.compile(r"\b[0-9a-fA-F]{40}\b")
AGENT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,32}$")


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
        default=None,
        help="Path to bridge events.jsonl (default: <bridge-root>/shared/events.jsonl)",
    )
    parser.add_argument(
        "--bridge-root",
        type=Path,
        default=None,
        help=(
            "Path to .agent-bridge directory (default: "
            "AGENT_BRIDGE_RUNTIME_ROOT/AGENT_BRIDGE_ROOT or repo-local)."
        ),
    )
    parser.add_argument(
        "--rco-agent",
        action="append",
        default=None,
        help=(
            "Recognized RCO agent identity. Repeat to supply a set. "
            "Defaults to claude-rco-1 and claude-rco-2."
        ),
    )
    parser.add_argument(
        "--author-agent",
        required=True,
        help=(
            "Bridge agent identity that authored the PR. A recognized RCO "
            "cannot satisfy the RCO slot for its own PR."
        ),
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit JSON result to stdout"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    task_id = (args.task_id or "").strip()
    head = (args.head or "").strip().lower()
    author_agent = (args.author_agent or "").strip()

    if not task_id:
        print("--task-id must not be empty", file=sys.stderr)
        return 2
    if not SHA_RE.fullmatch(head):
        print("--head must be a 40-char lowercase hex SHA", file=sys.stderr)
        return 2
    if not author_agent:
        print("--author-agent must not be empty", file=sys.stderr)
        return 2

    events_path = _resolve_events_path(args.events, args.bridge_root)
    if not events_path.exists():
        result = _make_result(
            ok=False,
            decision="no_events_file",
            task_id=task_id,
            head=head,
            rco_agents=args.rco_agent,
            author_agent=author_agent,
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
            rco_agents=args.rco_agent,
            author_agent=author_agent,
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
        rco_agent=args.rco_agent,
        author_agent=author_agent,
    )
    _emit(result, args.json)

    if result.get("ok") and result.get("has_qualifying_rco_pass_at_head"):
        return 0
    # Distinguish arg/invalid (2) vs. gate refuse (3) - here gate cases use 3
    gate_refuse_decisions = {
        "rco_pass_absent",
        "vetoed_after_pass",
        "no_qualifying_pass",
        "no_rco_events_for_task",
        "no_eligible_rco_agents",
    }
    return 3 if result.get("decision") in gate_refuse_decisions else 2


def _emit(result: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, sort_keys=True))
    else:
        if result.get("ok") and result.get("has_qualifying_rco_pass_at_head"):
            print(
                f"RCO_PASS present at exact head {result.get('head')} "
                f"for task {result.get('task_id')} "
                f"(agent {result.get('satisfying_rco_agent')})"
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
            for mismatch in result.get("task_id_mismatch_rco_events", []):
                print(
                    "  task-id mismatch pass candidate: "
                    f"agent={mismatch.get('agent')} "
                    f"task_id={mismatch.get('task_id')} "
                    f"status={mismatch.get('status')} type={mismatch.get('type')}",
                    file=sys.stderr,
                )
            for stale in result.get("stale_rco_pass_events", []):
                print(
                    "  stale rco_pass candidate: "
                    f"agent={stale.get('agent')} "
                    f"task_id={stale.get('task_id')} "
                    f"referenced_heads={','.join(stale.get('referenced_heads') or [])}",
                    file=sys.stderr,
                )
            guidance = result.get("rco_reemit_guidance") or {}
            if guidance:
                print(
                    "  re-emit RCO_PASS on accepted task_id: "
                    f"{guidance.get('preferred_task_id')}",
                    file=sys.stderr,
                )


def _resolve_events_path(events_path: Path | None, bridge_root: Path | None) -> Path:
    if events_path is not None:
        return events_path
    return resolve_bridge_root(bridge_root) / "shared" / "events.jsonl"


def _make_result(
    *,
    ok: bool,
    decision: str,
    task_id: str,
    head: str,
    rco_agents: Sequence[str] | None,
    author_agent: str,
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
        "rco_agent": rco_agents[0] if rco_agents else "",
        "rco_agents": list(rco_agents or []),
        "recognized_rco_agents": list(rco_agents or []),
        "eligible_rco_agents": [
            agent for agent in list(rco_agents or []) if agent != author_agent
        ],
        "author_agent": author_agent,
        "satisfying_rco_agent": None,
        "has_qualifying_rco_pass_at_head": bool(has_qualifying_rco_pass_at_head),
        "has_task_id_mismatch_rco_pass_at_head": False,
        "task_id_mismatch_rco_events": [],
        "task_id_aliases": [],
        "accepted_task_id_alias_rco_events": [],
        "has_stale_rco_pass_at_other_head": False,
        "stale_rco_pass_events": [],
        "latest_stale_rco_pass_event": None,
        "rco_reemit_guidance": None,
        "latest_rco_is_veto": latest_rco_is_veto,
        "rco_pass_event": (
            _summarize_event(rco_pass_event) if rco_pass_event is not None else None
        ),
        "latest_rco_event": (
            _summarize_event(latest_rco_event) if latest_rco_event is not None else None
        ),
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
    rco_agent: str | Sequence[str] | None = None,
    author_agent: str = "",
    identity_registry: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return whether a valid RCO_PASS at exact head exists for the RCO set on task_id.

    Fail-closed: absence, wrong identity, wrong head, missing author, self-review,
    or later veto from any recognized RCO after the satisfying pass -> not ok.
    Latest is determined by append order in events list (index), not wallclock ts.
    """
    task_id = (task_id or "").strip()
    head = (head or "").strip().lower()
    recognized_rco_agents = _normalize_rco_agents(rco_agent)
    author_agent = (author_agent or "").strip()
    eligible_rco_agents = tuple(
        agent for agent in recognized_rco_agents if agent != author_agent
    )
    registry: dict[str, str] = {}
    registry_error: str | None = None
    try:
        registry = (
            load_bridge_identity_registry()
            if identity_registry is None
            else dict(identity_registry)
        )
    except ValueError as exc:
        registry_error = str(exc)

    base: dict[str, Any] = {
        "ok": False,
        "decision": "rco_pass_absent",
        "task_id": task_id,
        "head": head,
        "rco_agent": recognized_rco_agents[0] if recognized_rco_agents else "",
        "rco_agents": list(recognized_rco_agents),
        "recognized_rco_agents": list(recognized_rco_agents),
        "eligible_rco_agents": list(eligible_rco_agents),
        "author_agent": author_agent,
        "satisfying_rco_agent": None,
        "has_qualifying_rco_pass_at_head": False,
        "has_task_id_mismatch_rco_pass_at_head": False,
        "task_id_mismatch_rco_events": [],
        "latest_rco_is_veto": None,
        "rco_pass_event": None,
        "latest_rco_event": None,
        "latest_rco_events": {},
        "blocking_rco_agents": [],
        "ignored_identity_mismatch_events": [],
        "task_id_aliases": [],
        "accepted_task_id_alias_rco_events": [],
        "has_stale_rco_pass_at_other_head": False,
        "stale_rco_pass_events": [],
        "latest_stale_rco_pass_event": None,
        "rco_reemit_guidance": None,
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
    if not recognized_rco_agents:
        base["decision"] = "invalid_rco_agents"
        base["error"] = "at least one recognized rco_agent is required"
        return base
    invalid_rco_agents = [
        agent for agent in recognized_rco_agents if not AGENT_ID_RE.fullmatch(agent)
    ]
    if invalid_rco_agents:
        base["decision"] = "invalid_rco_agents"
        base["error"] = "rco_agent entries must be bridge agent ids"
        return base
    if not author_agent:
        base["decision"] = "invalid_author_agent"
        base["error"] = "author_agent must not be empty"
        return base
    if not AGENT_ID_RE.fullmatch(author_agent):
        base["decision"] = "invalid_author_agent"
        base["error"] = "author_agent must be a bridge agent id"
        return base
    if not eligible_rco_agents:
        base["decision"] = "no_eligible_rco_agents"
        base["error"] = "all recognized RCO agents are excluded as the PR author"
        base["latest_rco_is_veto"] = False
        return base
    if registry_error:
        base["decision"] = "invalid_identity_registry"
        base["error"] = registry_error
        return base

    task_id_aliases = _author_task_id_aliases(task_id, author_agent)
    task_id_scope = frozenset((task_id, *task_id_aliases))
    base["task_id_aliases"] = list(task_id_aliases)

    task_id_mismatch_rco_events = _find_task_id_mismatch_rco_pass_events(
        events=events,
        task_id=task_id,
        task_id_aliases=task_id_aliases,
        head=head,
        eligible_rco_agents=eligible_rco_agents,
        identity_registry=registry,
    )
    base["task_id_mismatch_rco_events"] = task_id_mismatch_rco_events
    base["has_task_id_mismatch_rco_pass_at_head"] = bool(
        task_id_mismatch_rco_events
    )
    if task_id_mismatch_rco_events:
        base["rco_reemit_guidance"] = _rco_reemit_guidance(
            task_id=task_id,
            task_id_aliases=task_id_aliases,
            head=head,
            eligible_rco_agents=eligible_rco_agents,
            mismatches=task_id_mismatch_rco_events,
        )

    # Collect all recognized-RCO events scoped to this task_id or its
    # deterministic author-agent namespace alias.
    rco_events: list[tuple[int, Mapping[str, Any]]] = []
    ignored_identity_mismatch_events: list[dict[str, Any]] = []
    accepted_task_id_alias_rco_events: list[dict[str, Any]] = []
    restricted_agents = set(recognized_rco_agents)
    for index, event in enumerate(events):
        if not isinstance(event, Mapping):
            continue
        event_task_id = str(event.get("task_id", ""))
        if event_task_id not in task_id_scope:
            continue
        if str(event.get("agent", "")) not in recognized_rco_agents:
            continue
        binding_status = bridge_identity_binding_status(
            event,
            registry=registry,
            restricted_agents=restricted_agents,
        )
        if binding_status in {"missing_uuid", "mismatch_uuid"}:
            summary = _summarize_event(event)
            if summary is not None:
                summary["identity_binding_status"] = binding_status
                ignored_identity_mismatch_events.append(summary)
            continue
        if event_task_id != task_id:
            summary = _summarize_event(event)
            if summary is not None:
                accepted_task_id_alias_rco_events.append(summary)
        rco_events.append((index, event))
    base["ignored_identity_mismatch_events"] = ignored_identity_mismatch_events
    base["accepted_task_id_alias_rco_events"] = (
        accepted_task_id_alias_rco_events
    )

    if not rco_events:
        base["decision"] = "no_rco_events_for_task"
        base["latest_rco_is_veto"] = False
        return base

    # Most recent by list order (highest index)
    latest_idx, latest_ev = max(rco_events, key=lambda item: item[0])
    base["latest_rco_event"] = _summarize_event(latest_ev)
    latest_by_agent: dict[str, tuple[int, Mapping[str, Any]]] = {}
    for idx, ev in rco_events:
        latest_by_agent[str(ev.get("agent", ""))] = (idx, ev)
    base["latest_rco_events"] = {
        agent: _summarize_event(ev) for agent, (_idx, ev) in latest_by_agent.items()
    }
    latest_is_veto = _is_rco_veto_event(latest_ev)
    base["latest_rco_is_veto"] = latest_is_veto

    # Find qualifying head-bound passes (type-restricted, status=rco_pass, bound to exact head)
    qualifying: list[tuple[int, Mapping[str, Any]]] = []
    for idx, ev in rco_events:
        agent = str(ev.get("agent", ""))
        if agent not in eligible_rco_agents:
            continue
        if _is_qualifying_rco_pass(ev, head, agent):
            qualifying.append((idx, ev))

    stale_rco_pass_events = _stale_rco_pass_events(
        rco_events=rco_events,
        head=head,
        eligible_rco_agents=eligible_rco_agents,
    )
    base["stale_rco_pass_events"] = [event for _idx, event in stale_rco_pass_events]
    base["has_stale_rco_pass_at_other_head"] = bool(stale_rco_pass_events)
    if stale_rco_pass_events:
        base["latest_stale_rco_pass_event"] = stale_rco_pass_events[-1][1]

    if not qualifying:
        if stale_rco_pass_events and not base["rco_reemit_guidance"]:
            base["rco_reemit_guidance"] = _stale_rco_reemit_guidance(
                task_id=task_id,
                task_id_aliases=task_id_aliases,
                head=head,
                eligible_rco_agents=eligible_rco_agents,
                stale_events=[event for _idx, event in stale_rco_pass_events],
            )
        base["decision"] = "no_qualifying_pass"
        base["has_qualifying_rco_pass_at_head"] = False
        return base

    latest_pass_idx, latest_pass_ev = max(qualifying, key=lambda item: item[0])
    base["rco_pass_event"] = _summarize_event(latest_pass_ev)
    base["satisfying_rco_agent"] = str(latest_pass_ev.get("agent", ""))
    base["has_qualifying_rco_pass_at_head"] = True

    # Check for vetoes that supersede the satisfying pass. An older veto from a
    # different RCO belongs to the peer-veto preflight; this tool verifies the
    # exact-head RCO slot and later RCO vetoes that outrank that slot.
    blocking_agents: list[str] = []
    for agent, (latest_agent_idx, latest_agent_ev) in latest_by_agent.items():
        latest_agent_pass_idx = max(
            (idx for idx, ev in qualifying if str(ev.get("agent", "")) == agent),
            default=-1,
        )
        comparison_idx = (
            latest_agent_pass_idx if latest_agent_pass_idx >= 0 else latest_pass_idx
        )
        has_superseding_veto = any(
            idx > comparison_idx
            and str(ev.get("agent", "")) == agent
            and _is_rco_veto_event(ev)
            for idx, ev in rco_events
        )
        if has_superseding_veto or (
            latest_agent_idx > comparison_idx
            and _is_rco_veto_event(latest_agent_ev)
        ):
            blocking_agents.append(agent)
    base["blocking_rco_agents"] = sorted(set(blocking_agents))

    if base["blocking_rco_agents"]:
        base["ok"] = False
        base["decision"] = "vetoed_after_pass"
        return base

    # Valid: pass at exact head present, and no veto after it
    base["ok"] = True
    base["decision"] = "rco_pass_present"
    return base


def _find_task_id_mismatch_rco_pass_events(
    *,
    events: Sequence[Mapping[str, Any]],
    task_id: str,
    task_id_aliases: Sequence[str],
    head: str,
    eligible_rco_agents: Sequence[str],
    identity_registry: Mapping[str, str],
) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    restricted_agents = set(eligible_rco_agents)
    task_id_scope = frozenset((task_id, *task_id_aliases))
    for event in events:
        if not isinstance(event, Mapping):
            continue
        if str(event.get("task_id", "")) in task_id_scope:
            continue
        agent = str(event.get("agent", ""))
        if agent not in eligible_rco_agents:
            continue
        if bridge_identity_binding_status(
            event,
            registry=identity_registry,
            restricted_agents=restricted_agents,
        ) in {"missing_uuid", "mismatch_uuid"}:
            continue
        if _is_qualifying_rco_pass(event, head, agent):
            summary = _summarize_event(event)
            if summary is not None:
                mismatches.append(summary)
    return mismatches


def _rco_reemit_guidance(
    *,
    task_id: str,
    task_id_aliases: Sequence[str],
    head: str,
    eligible_rco_agents: Sequence[str],
    mismatches: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    rejected_task_ids = sorted(
        {str(event.get("task_id", "")) for event in mismatches if event.get("task_id")}
    )
    accepted_task_ids = [task_id, *task_id_aliases]
    return {
        "required": True,
        "reason": "task_id_mismatch_rco_pass_at_head",
        "preferred_task_id": task_id,
        "accepted_task_ids": accepted_task_ids,
        "rejected_task_ids": rejected_task_ids,
        "head": head,
        "target_rco_agents": list(eligible_rco_agents),
        # Legacy dispatchers recognize rco_requested; stricter status text can
        # be layered on the message without hiding the work from older readers.
        "legacy_request_status": "rco_requested",
    }


def _stale_rco_reemit_guidance(
    *,
    task_id: str,
    task_id_aliases: Sequence[str],
    head: str,
    eligible_rco_agents: Sequence[str],
    stale_events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    stale_heads = sorted(
        {
            str(head_value)
            for event in stale_events
            for head_value in event.get("referenced_heads", [])
        }
    )
    return {
        "required": True,
        "reason": "stale_rco_pass_head",
        "preferred_task_id": task_id,
        "accepted_task_ids": [task_id, *task_id_aliases],
        "head": head,
        "stale_heads": stale_heads,
        "target_rco_agents": list(eligible_rco_agents),
        "legacy_request_status": "rco_requested",
    }


def _author_task_id_aliases(task_id: str, author_agent: str) -> tuple[str, ...]:
    """Return deterministic author namespace slash/hyphen aliases.

    Bridge producers have historically used both `agent/rest` and
    `agent-rest` as the first namespace separator. Restrict aliases to the
    PR author prefix so a same-head RCO_PASS from an unrelated task cannot
    satisfy the gate.
    """

    aliases: list[str] = []
    slash_prefix = f"{author_agent}/"
    hyphen_prefix = f"{author_agent}-"
    if task_id.startswith(slash_prefix):
        rest = task_id[len(slash_prefix):]
        if rest:
            aliases.append(f"{author_agent}-{rest}")
    elif task_id.startswith(hyphen_prefix):
        rest = task_id[len(hyphen_prefix):]
        if rest:
            aliases.append(f"{author_agent}/{rest}")
    return tuple(aliases)


def _normalize_rco_agents(value: str | Sequence[str] | None) -> tuple[str, ...]:
    raw: Sequence[str]
    if value is None:
        raw = DEFAULT_RCO_AGENTS
    elif isinstance(value, str):
        raw = (value,)
    else:
        raw = value
    normalized: list[str] = []
    for item in raw:
        agent = str(item or "").strip()
        if not agent:
            continue
        if agent not in normalized:
            normalized.append(agent)
    return tuple(normalized)


def _is_qualifying_rco_pass(
    event: Mapping[str, Any], head: str, rco_agent: str
) -> bool:
    if str(event.get("agent", "")) != rco_agent:
        return False
    typ = str(event.get("type", "")).lower()
    if typ not in DECISION_TYPES_FOR_PASS:
        return False
    status = str(event.get("status", "")).lower()
    if status not in RCO_PASS_STATUSES:
        return False
    if _event_binds_head(event, head):
        return True
    return False


def _event_binds_head(event: Mapping[str, Any], head: str) -> bool:
    payload = event.get("payload")
    if isinstance(payload, Mapping):
        value = payload.get("exact_head")
        if isinstance(value, str) and value.strip().lower() == head:
            return True
    message = str(event.get("message", "") or "")
    # Per legacy spec: message contains the exact --head SHA string.
    # Use substring match on the provided head (caller normalizes to lower hex).
    if head in message:
        return True
    # Also accept case-insensitively for robustness with logs, but primary is
    # exact string containment as specified.
    return head.lower() in message.lower()


def _stale_rco_pass_events(
    *,
    rco_events: Sequence[tuple[int, Mapping[str, Any]]],
    head: str,
    eligible_rco_agents: Sequence[str],
) -> list[tuple[int, dict[str, Any]]]:
    stale_events: list[tuple[int, dict[str, Any]]] = []
    eligible = set(eligible_rco_agents)
    for idx, event in rco_events:
        agent = str(event.get("agent", ""))
        if agent not in eligible:
            continue
        if _is_qualifying_rco_pass(event, head, agent):
            continue
        if not _is_rco_pass_shape(event):
            continue
        head_candidates = _event_head_candidates(event)
        referenced_heads = [
            candidate for candidate in head_candidates if candidate != head
        ]
        if not referenced_heads or head in head_candidates:
            continue
        summary = _summarize_event(event)
        if summary is None:
            continue
        summary["expected_head"] = head
        summary["referenced_heads"] = referenced_heads
        stale_events.append((idx, summary))
    stale_events.sort(key=lambda item: item[0])
    return stale_events


def _is_rco_pass_shape(event: Mapping[str, Any]) -> bool:
    typ = str(event.get("type", "")).lower()
    status = str(event.get("status", "")).lower()
    return typ in DECISION_TYPES_FOR_PASS and status in RCO_PASS_STATUSES


def _event_head_candidates(event: Mapping[str, Any]) -> list[str]:
    text_parts = [str(event.get("message", "") or "")]
    payload = event.get("payload")
    if isinstance(payload, Mapping):
        for key in (
            "head",
            "head_sha",
            "reviewed_head_sha",
            "target_head_sha",
            "pr_head_sha",
        ):
            value = payload.get(key)
            if isinstance(value, str):
                text_parts.append(value)
    elif isinstance(payload, str):
        text_parts.append(payload)
    joined = "\n".join(text_parts)
    return sorted(
        {match.group(0).lower() for match in HEAD_CANDIDATE_RE.finditer(joined)}
    )


def _is_rco_veto_event(event: Mapping[str, Any]) -> bool:
    """Fail-closed veto detection for RCO signals.

    Uses the same status taxonomy as the peer gate for explicit veto names,
    while keeping unknown ``type=finding`` statuses fail-closed. Plain message
    chatter does not veto.
    """
    status = str(event.get("status", "") or "").lower()
    typ = str(event.get("type", "") or "").lower()

    if _is_blocking_status(status, event_type=typ):
        return True

    if typ == "blocked":
        return not _is_approval_status(status)

    if typ == "finding":
        if status in RCO_PASS_STATUSES or _is_approval_status(status):
            return False
        if _is_informational_finding_status(status):
            return False
        if _is_known_bridge_non_veto_status(status):
            return False
        return True

    return False


def _is_known_bridge_non_veto_status(status: str) -> bool:
    if _bridge_is_clear_status(status):
        return True
    return _has_bridge_veto_words(status) and not _bridge_is_blocking_status(
        status,
        event_type="finding",
    )


def _has_bridge_veto_words(status: str) -> bool:
    tokens = _status_tokens(status)
    return (
        {"changes", "requested"}.issubset(tokens)
        or bool(tokens.intersection(BRIDGE_BLOCK_WORD_TOKENS))
        or any(token.startswith("block") for token in tokens)
    )


def _is_blocking_status(status: str, *, event_type: str = "") -> bool:
    return _bridge_is_blocking_status(status, event_type=event_type)


def _is_approval_status(status: str) -> bool:
    if status in RCO_PASS_STATUSES:
        return True
    tokens = _status_tokens(status)
    return (
        {"rco", "pass"}.issubset(tokens)
        or "approved" in tokens
        or "acknowledged" in tokens
    )


def _is_informational_finding_status(status: str) -> bool:
    """True for unambiguously-informational finding statuses (info/fyi/advisory...).

    Normalizes punctuation to underscores then exact-matches the tight
    INFORMATIONAL_FINDING_STATUSES allowlist, so only explicitly-informational
    statuses are exempted from veto detection; unknown, ambiguous, or blocking
    statuses are never treated as informational (fail-closed).
    """
    normalized = re.sub(r"[^a-z0-9]+", "_", status.lower()).strip("_")
    return normalized in INFORMATIONAL_FINDING_STATUSES


def _status_tokens(status: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", status.lower()) if token}


def _summarize_event(event: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if event is None:
        return None
    return {
        "ts_utc": str(event.get("ts_utc", "")),
        "agent": str(event.get("agent", "")),
        "agent_uuid": str(event.get("agent_uuid", "")),
        "type": str(event.get("type", "")),
        "status": str(event.get("status", "")),
        "task_id": str(event.get("task_id", "")),
    }


def _read_events(events_path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    text = events_path.read_text(encoding="utf-8-sig")
    for line_number, line in enumerate(text.splitlines(), start=1):
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
