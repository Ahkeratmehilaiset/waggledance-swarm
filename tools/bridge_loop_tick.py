#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Autonomous-loop aggregator for one agent tick.

Chains the existing bridge primitives into a single ordered worklist plus a
recommended self-pacing wakeup, so a Claude/Codex session can drain its bridge
inbox and complete already-approved merges WITHOUT a human poke -- while every
mutation still flows through the existing gated tools.

By default this tool is read-only: it has no ``--apply`` and never runs
``gh pr merge``. It only REPORTS:
  * the next-action recommendation (drain peer RCO requests / handoffs to me),
  * my own rco_pass'd PRs that are now CI-green + mergeable + preflight-clear +
    head-matched -- i.e. ready for me to complete the merge in this same tick
    via the existing ``gh pr merge --squash --match-head-commit`` flow,
  * open operator decision packs (surface-only; never auto-resolved),
  * stale heartbeat-only peer sessions that need an explicit bridge activation,
  * a recommended ScheduleWakeup interval derived from bridge state.

With ``--emit-peer-activation`` it may write exactly one validated
``type=handoff status=scout_requested`` event when the peer is heartbeat-only.
That opt-in mutation is intentionally limited to keeping the peer active; it
does not resolve operator packs, merge PRs, or claim work.

Merge-readiness here encodes the CLAUDE.md Rule 9 peer-RCO criteria (head-match
+ CI green + mergeable clean + no standing peer block), which is the flow used
for direct peer-RCO PRs. Idle-consensus-protocol PRs keep using
``idle_consensus_auto_merge.evaluate_auto_merge_gate`` (consensus + MAGMA
receipt) and are out of scope for this aggregator.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
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
from waggledance.core.bridge_event_schema import validate_event  # noqa: E402

# Adaptive wakeup bands (seconds). Sub-300 only when there is actionable
# merge/RCO work, to respect the ~5-minute prompt-cache TTL.
WAKEUP_ACT_NOW = 90  # merge-ready or open peer RCO addressed to me
WAKEUP_IN_FLIGHT = 240  # CI pending on a candidate / active own claim
WAKEUP_QUIET = 1800  # nothing pending; operator's ball or idle

SnapshotFn = Callable[[int], Mapping[str, Any]]
PEER_AGENT = {"claude": "codex", "codex": "claude"}
SUBSTANTIVE_IDLE_MINUTES = 30.0
PEER_ACTIVATION_RECENT_MINUTES = 30.0
NON_SUBSTANTIVE_TYPES = {"heartbeat", "liveness"}

# How recently a peer's substantive event must have happened to count as
# an "active PR-producing claim" for wakeup-anticipation purposes. Tracks
# the observed Codex self-merge timeout window (~5-10 min after CI green)
# documented in docs/runs/magma_100h_sprint_2026_05_23/baseline.json::
# claude_activation_contract.rco_timeout_minutes_after_ci_green.
PEER_ACTIVE_CLAIM_MAX_AGE_MINUTES = 15.0

# Event (type, status) pairs that indicate a peer is mid-task on work that
# typically produces a PR. Used to keep Claude's wakeup tight even before
# the peer's PR is open on GitHub. See feedback memory
# heartbeat-short-while-peer-pr-awaits-rco for the lesson.
PEER_PR_PRODUCING_SIGNALS = frozenset(
    {
        ("claim", "active"),
        ("claim", "started"),
        ("status", "active"),
        ("handoff", "active_requested"),
    }
)

# Bridge rco_pass payloads commonly carry a short head (e.g. "862d34bd") while
# gh pr view returns the full 40-char head_sha. Treat the approved head as an
# unambiguous prefix of the full sha, with a sane minimum length.
MIN_HEAD_PREFIX = 7


def head_matches(approved_head: str, full_head_sha: str) -> bool:
    """True when the rco_pass approved head identifies the snapshot head.

    Accepts an exact match or an unambiguous case-insensitive prefix of length
    >= MIN_HEAD_PREFIX. The full snapshot sha is what the merge command should
    pin (``--match-head-commit``); this only validates they refer to the same
    commit.
    """
    approved = approved_head.strip().lower()
    full = full_head_sha.strip().lower()
    if not approved or not full:
        return False
    if len(approved) < MIN_HEAD_PREFIX:
        return False
    return full == approved or full.startswith(approved)


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


def _is_substantive_agent_event(event: Mapping[str, Any], agent: str) -> bool:
    if _event_agent(event) != agent:
        return False
    event_type = str(event.get("type", "")).lower()
    return event_type not in NON_SUBSTANTIVE_TYPES


def _latest_agent_event(
    events: Sequence[Mapping[str, Any]],
    *,
    agent: str,
    substantive_only: bool,
) -> Mapping[str, Any] | None:
    for event in reversed(events):
        if _event_agent(event) != agent:
            continue
        if substantive_only and not _is_substantive_agent_event(event, agent):
            continue
        if _parse_ts(str(event.get("ts_utc", ""))) is None:
            continue
        return event
    return None


def _age_minutes(event: Mapping[str, Any] | None, now_utc: datetime) -> float | None:
    if event is None:
        return None
    ts = _parse_ts(str(event.get("ts_utc", "")))
    if ts is None:
        return None
    return round((now_utc - ts).total_seconds() / 60.0, 3)


def _event_targets(event: Mapping[str, Any]) -> set[str]:
    return {
        item.strip()
        for item in str(event.get("to", "")).split(",")
        if item.strip()
    }


def _latest_peer_activation_sent(
    events: Sequence[Mapping[str, Any]],
    *,
    agent: str,
    peer: str,
) -> Mapping[str, Any] | None:
    prefix = f"peer-activation-{peer}-"
    for event in reversed(events):
        if _event_agent(event) != agent:
            continue
        if str(event.get("type", "")).lower() != "handoff":
            continue
        if str(event.get("status", "")).lower() != "scout_requested":
            continue
        if peer not in _event_targets(event):
            continue
        if not str(event.get("task_id", "")).startswith(prefix):
            continue
        if _parse_ts(str(event.get("ts_utc", ""))) is None:
            continue
        return event
    return None


def peer_has_active_pr_producing_claim(
    events: Sequence[Mapping[str, Any]],
    *,
    agent: str,
    now_utc: datetime,
    max_age_minutes: float = PEER_ACTIVE_CLAIM_MAX_AGE_MINUTES,
) -> dict[str, Any]:
    """Read-only check: is the peer mid-task on work that typically produces a PR?

    Returns a structured dict (never raises). ``active=True`` when the peer's
    most recent substantive event matches one of ``PEER_PR_PRODUCING_SIGNALS``,
    the task_id has not since been closed by a peer ``done`` event, and the
    event is within ``max_age_minutes`` of ``now_utc``. The intent is to keep
    Claude's wakeup tight (``WAKEUP_IN_FLIGHT``) so the imminent peer PR
    catches a fresh RCO within the peer's self-merge timeout window
    (~5-10 min after CI green; see ``PEER_ACTIVE_CLAIM_MAX_AGE_MINUTES``).
    """

    base: dict[str, Any] = {
        "active": False,
        "peer": PEER_AGENT.get(agent, ""),
        "task_id": None,
        "event_type": None,
        "event_status": None,
        "age_minutes": None,
        "reason": "no_substantive_peer_event",
    }
    peer = base["peer"]
    if not peer:
        base["reason"] = "no_peer_for_agent"
        return base

    latest = _latest_agent_event(events, agent=peer, substantive_only=True)
    if latest is None:
        return base

    event_type = str(latest.get("type", "")).lower()
    event_status = str(latest.get("status", "")).lower()
    task_id = str(latest.get("task_id", "")) or None
    age = _age_minutes(latest, now_utc)
    base["event_type"] = event_type
    base["event_status"] = event_status
    base["task_id"] = task_id
    base["age_minutes"] = age

    if (event_type, event_status) not in PEER_PR_PRODUCING_SIGNALS:
        # A later ``done`` (or any other non-PR-producing substantive
        # event) supersedes an earlier claim-active because
        # ``_latest_agent_event(substantive_only=True)`` already returns
        # the most recent substantive event, so closure is detected here.
        base["reason"] = "latest_peer_event_not_pr_producing"
        return base

    if age is None or age > max_age_minutes:
        base["reason"] = "peer_claim_event_too_old"
        return base

    base["active"] = True
    base["reason"] = "peer_has_active_pr_producing_claim"
    return base


def _activation_task(
    *,
    agent: str,
    peer: str,
    open_packs: Sequence[Mapping[str, Any]],
    now_utc: datetime,
) -> dict[str, str]:
    stamp = now_utc.strftime("%Y-%m-%d-%H-%M")
    if open_packs:
        pack_ids = ", ".join(str(pack.get("decision_id", "")) for pack in open_packs)
        return {
            "task_id": f"peer-activation-{peer}-unblocked-scout-{stamp}",
            "summary": "Scout unblocked WD work while operator-gated packs wait",
            "message": (
                f"{peer}: heartbeat-only detected. Take a read-only scout now while "
                f"{agent} continues implementation/merge work. Open operator packs "
                f"({pack_ids}) must stay fail-closed; do not resolve them. Find the "
                "highest-value unblocked WD slice: bridge reliability, release "
                "evidence, MAGMA/evaluation proof, competitor gap, or test-gap "
                "simulation. Return exact files, risks, tests, no-go criteria, and "
                "one recommended next PR scope."
            ),
        }
    return {
        "task_id": f"peer-activation-{peer}-wd-advantage-scout-{stamp}",
        "summary": "Scout the next WD advantage slice",
        "message": (
            f"{peer}: heartbeat-only detected. Take a read-only scout now while "
            f"{agent} handles its current loop. Compare WD against current rival "
            "control-plane/security/replay patterns, run local-only reasoning or "
            "tests where possible, and report the next highest-value PR candidate "
            "with concrete acceptance tests and no-go criteria."
        ),
    }


def peer_activation_recommendation(
    *,
    agent: str,
    events: Sequence[Mapping[str, Any]],
    claims: Sequence[Any],
    open_packs: Sequence[Mapping[str, Any]],
    now_utc: datetime,
) -> dict[str, Any]:
    """Return a read-only recommendation for activating a heartbeat-only peer."""

    peer = PEER_AGENT.get(agent, "")
    base = {
        "needed": False,
        "peer": peer,
        "reason": "",
        "last_seen_at_utc": "",
        "last_substantive_at_utc": "",
        "substantive_age_minutes": None,
        "bridge_event": None,
    }
    if not peer:
        return base

    for claim in claims:
        claim_agent = getattr(claim, "agent", None)
        if claim_agent is None and isinstance(claim, Mapping):
            claim_agent = claim.get("agent")
        if str(claim_agent or "") == peer:
            base["reason"] = "peer_has_active_claim"
            return base

    latest = _latest_agent_event(events, agent=peer, substantive_only=False)
    substantive = _latest_agent_event(events, agent=peer, substantive_only=True)
    latest_age = _age_minutes(latest, now_utc)
    substantive_age = _age_minutes(substantive, now_utc)
    if latest is not None:
        base["last_seen_at_utc"] = str(latest.get("ts_utc", ""))
    if substantive is not None:
        base["last_substantive_at_utc"] = str(substantive.get("ts_utc", ""))
    base["substantive_age_minutes"] = substantive_age

    sent_activation = _latest_peer_activation_sent(events, agent=agent, peer=peer)
    sent_activation_age = _age_minutes(sent_activation, now_utc)
    if (
        sent_activation_age is not None
        and sent_activation_age < PEER_ACTIVATION_RECENT_MINUTES
    ):
        base["reason"] = "peer_activation_recently_sent"
        base["last_activation_sent_at_utc"] = str(sent_activation.get("ts_utc", ""))
        base["last_activation_age_minutes"] = sent_activation_age
        return base

    latest_type = str(latest.get("type", "")).lower() if latest is not None else ""
    heartbeat_only = latest_type in NON_SUBSTANTIVE_TYPES
    stale_substantive = (
        substantive_age is None or substantive_age >= SUBSTANTIVE_IDLE_MINUTES
    )
    if not heartbeat_only or not stale_substantive:
        base["reason"] = "peer_recently_substantive"
        return base

    task = _activation_task(
        agent=agent,
        peer=peer,
        open_packs=open_packs,
        now_utc=now_utc,
    )
    return {
        **base,
        "needed": True,
        "reason": "peer_heartbeat_only_without_recent_substantive_work",
        "last_seen_age_minutes": latest_age,
        "bridge_event": {
            "to": peer,
            "type": "handoff",
            "status": "scout_requested",
            "severity": "major",
            "task_id": task["task_id"],
            "summary": task["summary"],
            "message": task["message"],
        },
    }


def _event_timestamp(now_utc: datetime) -> str:
    return now_utc.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def materialize_peer_activation_event(
    *,
    agent: str,
    event_spec: Mapping[str, Any],
    now_utc: datetime,
) -> dict[str, Any]:
    """Convert a peer-activation recommendation into a schema-valid event."""

    event = {
        "ts_utc": _event_timestamp(now_utc),
        "agent": agent,
        "type": str(event_spec.get("type", "")),
        "task_id": str(event_spec.get("task_id", "")),
        "status": str(event_spec.get("status", "")),
        "severity": str(event_spec.get("severity", "")),
        "to": str(event_spec.get("to", "")),
        "message": str(event_spec.get("message", "")),
        "paths": [],
        "write_scope": [],
        "run_id": "",
        "pid": os.getpid(),
        "cwd": str(Path.cwd()),
        "payload": {
            "summary": str(event_spec.get("summary", "")),
            "source": "bridge_loop_tick.peer_activation",
        },
    }
    validate_event(event)
    return event


def emit_peer_activation_event(
    *,
    bridge_root: Path,
    agent: str,
    event_spec: Mapping[str, Any],
    now_utc: datetime,
) -> Path:
    """Append one validated peer-activation handoff to the bridge event stream."""

    event = materialize_peer_activation_event(
        agent=agent,
        event_spec=event_spec,
        now_utc=now_utc,
    )
    shared_dir = bridge_root / "shared"
    outbox_dir = bridge_root / "outbox" / agent
    shared_dir.mkdir(parents=True, exist_ok=True)
    outbox_dir.mkdir(parents=True, exist_ok=True)

    line = json.dumps(event, separators=(",", ":"), sort_keys=False) + "\n"
    events_path = shared_dir / "events.jsonl"
    outbox_path = outbox_dir / f"{now_utc.astimezone(timezone.utc):%Y-%m-%d}.jsonl"
    last_path = shared_dir / f"last_{agent}.json"
    _append_line_with_retry(events_path, line)
    _append_line_with_retry(outbox_path, line)
    try:
        _write_json_atomic(last_path, json.dumps(event, indent=2))
    except OSError:
        # last_<agent>.json is a convenience cache; events.jsonl is canonical.
        pass
    return events_path


def _append_line_with_retry(path: Path, line: str) -> None:
    for attempt in range(40):
        try:
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)
            return
        except OSError:
            if attempt == 39:
                raise
            time.sleep(0.025 + (attempt * 0.01))


def _write_json_atomic(path: Path, payload: str) -> None:
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


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
    merged_prs: set[int] = set()
    for event in events:
        task = _task_id(event)
        if _is_merged_done(event):
            if task:
                merged_tasks.add(task)
            merged_pr = _payload_pr(event)
            if merged_pr is not None:
                merged_prs.add(merged_pr)
            # A merge after a pass clears it; drop any recorded pass.
            latest_pass.pop(task, None)
        elif task and _is_my_rco_pass(event, agent):
            latest_pass[task] = event
    effective_now = now_utc or datetime.now(timezone.utc)
    candidates: list[dict[str, Any]] = []
    for task, event in latest_pass.items():
        if task in merged_tasks:
            continue
        pr = _payload_pr(event)
        if pr is None:
            continue
        # P2: a done/merged event for the same PR number clears the candidate
        # even when its task_id differs (e.g. 20260522 vs 2026-05-22).
        if pr in merged_prs:
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

    if not approved_head:
        result["blockers"].append("rco_pass_missing_head")
    elif not head_matches(approved_head, head_sha):
        result["blockers"].append("head_moved_since_rco_pass")
    if mergeable not in MERGEABLE_STATES:
        result["blockers"].append(f"mergeable_not_clean:{mergeable}")
    if not result["checks_green"]:
        result["blockers"].append("checks_not_green")

    result["ready"] = not result["blockers"]
    if result["ready"]:
        # Pin the FULL snapshot sha, not the (possibly short) approved head.
        result["merge_command"] = (
            f"gh pr merge {pr} --squash --match-head-commit={head_sha}"
        )
    return result


def _recommended_wakeup(
    *,
    next_action: str,
    merge_ready: Sequence[Mapping[str, Any]],
    open_packs_count: int,
    peer_activation: Mapping[str, Any] | None = None,
    peer_active_claim: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if any(item.get("ready") for item in merge_ready) or next_action == "answer_incoming":
        return {"seconds": WAKEUP_ACT_NOW, "reason": "actionable merge/RCO work pending"}
    if peer_activation and peer_activation.get("needed"):
        return {"seconds": WAKEUP_ACT_NOW, "reason": "peer activation needed"}
    if peer_active_claim and peer_active_claim.get("active"):
        return {
            "seconds": WAKEUP_IN_FLIGHT,
            "reason": "peer has active PR-producing claim; anticipate",
        }
    if next_action == "claim_unblocked_work" and open_packs_count:
        return {
            "seconds": WAKEUP_IN_FLIGHT,
            "reason": "operator pack open; check unblocked work soon",
        }
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
    effective_now = now_utc or datetime.now(timezone.utc)
    peer_activation = peer_activation_recommendation(
        agent=agent,
        events=events,
        claims=claims,
        open_packs=packs["open"],
        now_utc=effective_now,
    )
    peer_active_claim = peer_has_active_pr_producing_claim(
        events,
        agent=agent,
        now_utc=effective_now,
    )
    wakeup = _recommended_wakeup(
        next_action=next_action,
        merge_ready=merge_ready,
        open_packs_count=len(packs["open"]),
        peer_activation=peer_activation,
        peer_active_claim=peer_active_claim,
    )

    return {
        "ok": True,
        "agent": agent,
        "next_action": next_action,
        "next_action_detail": next_action_report,
        "merge_ready": merge_ready,
        "open_operator_packs": packs["open"],
        "invalid_operator_packs": packs["invalid"],
        "peer_activation": peer_activation,
        "peer_active_claim": peer_active_claim,
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
    parser.add_argument(
        "--emit-peer-activation",
        action="store_true",
        help=(
            "Write the recommended peer activation handoff when needed. "
            "All other actions remain report-only."
        ),
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
        now_utc = datetime.now(timezone.utc)
        report = build_loop_tick(
            agent=args.agent,
            events=events,
            claims=claims,
            inbox_dir=args.inbox_dir,
            now_utc=now_utc,
            snapshot_fn=snapshot_fn,
        )
        peer_activation = report.get("peer_activation", {})
        if (
            args.emit_peer_activation
            and isinstance(peer_activation, dict)
            and peer_activation.get("needed")
            and isinstance(peer_activation.get("bridge_event"), Mapping)
        ):
            event_path = emit_peer_activation_event(
                bridge_root=Path(args.bridge_root),
                agent=args.agent,
                event_spec=peer_activation["bridge_event"],
                now_utc=now_utc,
            )
            peer_activation["emitted"] = True
            peer_activation["emitted_path"] = str(event_path)
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
        peer_activation = report.get("peer_activation", {})
        if isinstance(peer_activation, Mapping) and peer_activation.get("needed"):
            event = peer_activation.get("bridge_event", {})
            task = event.get("task_id") if isinstance(event, Mapping) else ""
            print(f"peer activation needed: {peer_activation.get('peer')} {task}")
            if peer_activation.get("emitted"):
                print(f"peer activation emitted: {peer_activation.get('emitted_path')}")
        print(f"open operator packs: {len(report['open_operator_packs'])}")
        print(
            f"recommended wakeup: {report['recommended_wakeup_seconds']}s "
            f"({report['wakeup_reason']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
