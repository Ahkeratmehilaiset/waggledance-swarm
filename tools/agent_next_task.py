# SPDX-License-Identifier: BUSL-1.1
"""Deterministic continuous-loop work picker for one bridge agent.

This tool extends ``tools/bridge_next_action`` for the case where bridge
state alone does not name a concrete task. When ``bridge_next_action``
returns ``claim_unblocked_work`` or ``parallel_read_only``, an agent
still has to pick *which* unblocked work to claim. This tool answers
that question deterministically so two agents firing on independent
schedules never sit idle and never collide on the same suggestion.

Read-only contract:

* never emits bridge events;
* never claims work-queue tasks;
* never opens or merges pull requests.

The output is a recommendation report. The caller (scheduler wrapper,
LLM session, or other tool) is responsible for any subsequent claim
or external effect.

This tool is the third companion to ``tools/idle_loop_once.py`` (Slice
1) and ``docs/architecture/IDLE_LOOP_RUNBOOK.md`` (Slice 2). Together
the three pieces let the operator install one scheduled tick that
always returns something actionable for the next live agent session,
without weakening any charter gate.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.bridge_next_action import (  # noqa: E402
    BridgeNextActionError,
    read_events,
    recommend_next_action,
)
from tools.idle_check import DEFAULT_EVENTS_PATH  # noqa: E402
from waggledance.core.work_queue import (  # noqa: E402
    AGENT_ID_PATTERN,
    DEFAULT_BRIDGE_ROOT,
    WorkQueueError,
    list_claims,
)


DEFER_ACTIONS = {"continue_claim", "answer_incoming"}
PICK_ACTIONS = {"claim_unblocked_work", "parallel_read_only"}
SUCCESSFUL_COMPLETION_TYPES = {"done", "release", "test"}
SUCCESSFUL_COMPLETION_STATUSES = {
    "done",
    "merged",
    "merged_verified",
    "pass",
    "passed",
    "resolved",
    "success",
    "verified",
}


# A small, stable, charter-safe pool of always-available read-only
# verification candidates. Each entry must point at an existing path
# in the repo, must be a pytest target, and must require no external
# state (no GitHub, no live bridge, no network). The pool is small on
# purpose: the goal is a deterministic rotation, not exhaustive
# coverage.
SUBSTRATE_SMOKE_CANDIDATES: tuple[dict[str, str], ...] = (
    {
        "target": "tests/contracts/",
        "rationale": "schema contract regression check across the whole contracts suite",
    },
    {
        "target": "tests/tools/test_idle_loop_once.py",
        "rationale": "verify the idle-loop one-tick orchestrator still reports the documented decisions",
    },
    {
        "target": "tests/unit/test_idle_protocol_session.py",
        "rationale": "verify the idle-protocol session summarizer still gates convergence behind operator review",
    },
    {
        "target": "tests/unit/test_idle_protocol_validator.py",
        "rationale": "verify idle-protocol payload validation still rejects low-quality / unfalsifiable content",
    },
    {
        "target": "tests/unit/test_idle_consensus_charter.py",
        "rationale": "verify charter evaluator still enforces allowlist / denylist / code-pattern denylist",
    },
    {
        "target": "tests/unit/test_work_queue.py",
        "rationale": "verify bridge work-queue primitives still refuse overlap and honor the stale-claim lease",
    },
    {
        "target": "tests/tools/test_bridge_next_action.py",
        "rationale": "verify bridge_next_action still matches the protocol decision tree (Rule 6 reply requirements)",
    },
    {
        "target": "tests/tools/test_idle_consensus_auto_merge.py",
        "rationale": "verify idle auto-merge gate enforces the 7 parallel charter conditions (post PR #490 alignment)",
    },
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Pick a deterministic continuous-loop next task for one bridge agent."
        ),
    )
    parser.add_argument("--agent", required=True)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS_PATH)
    parser.add_argument("--bridge-root", type=Path, default=DEFAULT_BRIDGE_ROOT)
    parser.add_argument(
        "--tail",
        type=int,
        default=50000,
        help="Maximum bridge event lines to read from the end of the JSONL file.",
    )
    parser.add_argument("--now", default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    now_utc = _parse_utc(args.now) if args.now else datetime.now(timezone.utc)
    report = evaluate_agent_next_task(
        agent=args.agent,
        events_path=args.events,
        bridge_root=args.bridge_root,
        tail=args.tail,
        now_utc=now_utc,
    )
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(report["decision"])
        nxt = report.get("next_action")
        if nxt:
            print(f"next_action: {nxt}")
        candidate = report.get("candidate")
        if candidate:
            print(f"candidate: {candidate.get('kind')} target={candidate.get('target')}")
            recommended = candidate.get("recommended_command")
            if recommended:
                print(f"recommended_command: {recommended}")
        for note in report.get("notes", []):
            print(f"note: {note}")
    return int(report.get("exit_code", 0))


def evaluate_agent_next_task(
    *,
    agent: str,
    events_path: Path,
    bridge_root: Path = DEFAULT_BRIDGE_ROOT,
    tail: int = 50000,
    now_utc: datetime,
) -> dict[str, Any]:
    """Return one deterministic continuous-loop recommendation for ``agent``.

    Claims are always loaded from ``bridge_root/work_queue/claims`` via
    ``waggledance.core.work_queue.list_claims``. The caller does not pass a
    separate claims directory because the bridge protocol treats
    ``bridge_root`` as the single source of truth for active claims; honoring
    a separate ``claims_dir`` would let a wrapper silently disagree with the
    bridge runtime root and miss an active claim that should defer to
    ``bridge_next_action.continue_claim``.
    """
    if not AGENT_ID_PATTERN.fullmatch(agent):
        return {
            "decision": "agent_invalid",
            "next_action": "operator_handles",
            "exit_code": 2,
            "errors": [f"agent must match {AGENT_ID_PATTERN.pattern}"],
        }

    try:
        events = read_events(events_path, tail=tail)
        claims = list_claims(bridge_root=Path(bridge_root))
    except (BridgeNextActionError, WorkQueueError, OSError) as exc:
        return {
            "decision": "unknown",
            "next_action": "operator_handles",
            "exit_code": 2,
            "errors": [exc.__class__.__name__, str(exc)],
            "notes": [
                "bridge state could not be read; the scheduler should leave the bridge alone"
            ],
        }

    try:
        bridge_recommendation = recommend_next_action(
            agent=agent,
            events=events,
            claims=claims,
            now_utc=now_utc,
        )
    except BridgeNextActionError as exc:
        return {
            "decision": "unknown",
            "next_action": "operator_handles",
            "exit_code": 2,
            "errors": [str(exc)],
        }

    bridge_action = str(bridge_recommendation.get("action", ""))

    if bridge_action in DEFER_ACTIONS:
        return {
            "decision": "defer_to_bridge_next_action",
            "next_action": "follow_bridge_recommendation",
            "exit_code": 0,
            "agent": agent,
            "bridge_recommendation": bridge_recommendation,
            "notes": [
                (
                    f"bridge_next_action already produced a concrete recommendation "
                    f"({bridge_action}); follow it verbatim"
                )
            ],
        }

    if bridge_action in PICK_ACTIONS:
        completed_task_ids = _completed_substrate_smoke_task_ids(
            agent=agent,
            events=events,
            bridge_root=Path(bridge_root),
            now_utc=now_utc,
        )
        candidate = _pick_substrate_smoke(
            agent=agent,
            now_utc=now_utc,
            completed_task_ids=completed_task_ids,
        )
        if candidate is None:
            return {
                "decision": "substrate_smoke_pool_exhausted",
                "next_action": "claim_non_smoke_work",
                "exit_code": 0,
                "agent": agent,
                "underlying_bridge_action": bridge_action,
                "bridge_recommendation": bridge_recommendation,
                "completed_substrate_smoke_task_ids": sorted(completed_task_ids),
                "notes": [
                    (
                        "bridge_next_action left the next-work choice open, but "
                        "every substrate-smoke candidate for this agent is "
                        "already completed today"
                    ),
                    (
                        "the scheduler should claim a non-smoke WD-mission task "
                        "instead of rerunning an already-completed daily smoke"
                    ),
                ],
            }
        skip_note = []
        if candidate["rotation"]["offset"] > 0:
            skip_note = [
                (
                    "the initial daily smoke candidate was already completed, "
                    "so the picker advanced to the next pool entry"
                )
            ]
        return {
            "decision": "claim_substrate_smoke",
            "next_action": "claim_and_run",
            "exit_code": 0,
            "agent": agent,
            "underlying_bridge_action": bridge_action,
            "bridge_recommendation": bridge_recommendation,
            "candidate": candidate,
            "notes": [
                (
                    "bridge_next_action left the next-work choice open; this tool "
                    "picks a deterministic charter-safe read-only verification "
                    "candidate per the rotation rule (UTC day-of-year plus a "
                    "per-agent salt) so two agents firing on independent schedules "
                    "do not collide"
                ),
                (
                    "the candidate is a recommendation only; the caller is "
                    "responsible for claiming it before running"
                ),
                *skip_note,
            ],
        }

    return {
        "decision": "unknown_bridge_action",
        "next_action": "operator_handles",
        "exit_code": 0,
        "agent": agent,
        "bridge_recommendation": bridge_recommendation,
        "notes": [
            (
                f"bridge_next_action returned an unrecognized action "
                f"({bridge_action!r}); the scheduler should escalate"
            )
        ],
    }


def _pick_substrate_smoke(
    *,
    agent: str,
    now_utc: datetime,
    completed_task_ids: set[str] | None = None,
) -> dict[str, Any] | None:
    """Pick one substrate-smoke candidate deterministically for ``agent``."""
    pool = SUBSTRATE_SMOKE_CANDIDATES
    day_of_year = now_utc.timetuple().tm_yday
    agent_salt = sum(ord(c) for c in agent) % len(pool)
    start_index = (day_of_year + agent_salt) % len(pool)
    completed = completed_task_ids or set()
    legacy_task_id = _legacy_daily_smoke_task_id(agent=agent, now_utc=now_utc)

    index = start_index
    offset = 0
    for candidate_offset in range(len(pool)):
        candidate_index = (start_index + candidate_offset) % len(pool)
        candidate_task_id = _substrate_smoke_task_id(
            agent=agent,
            now_utc=now_utc,
            index=candidate_index,
        )
        is_legacy_completion = (
            candidate_offset == 0 and legacy_task_id in completed
        )
        if candidate_task_id not in completed and not is_legacy_completion:
            index = candidate_index
            offset = candidate_offset
            break
    else:
        return None

    chosen = pool[index]
    target = chosen["target"]
    rationale = chosen["rationale"]
    task_id = _substrate_smoke_task_id(
        agent=agent,
        now_utc=now_utc,
        index=index,
    )
    return {
        "kind": "run_substrate_smoke",
        "target": target,
        "rationale": rationale,
        "task_id_suggestion": task_id,
        "mode": "read-only",
        "write_scope": [],
        "recommended_command": (
            "C:\\Python\\project2-master\\.venv\\Scripts\\python.exe "
            f"-m pytest {target} -q"
        ),
        "rotation": {
            "agent": agent,
            "day_of_year": day_of_year,
            "agent_salt": agent_salt,
            "pool_size": len(pool),
            "start_index": start_index,
            "index": index,
            "offset": offset,
            "skipped_completed_task_ids": sorted(
                task_id
                for task_id in completed
                if task_id == legacy_task_id
                or task_id.startswith(_daily_smoke_task_prefix(agent, now_utc))
            ),
        },
    }


def _daily_smoke_task_prefix(agent: str, now_utc: datetime) -> str:
    return f"{agent}-substrate-smoke-{now_utc.strftime('%Y-%m-%d')}-"


def _legacy_daily_smoke_task_id(agent: str, now_utc: datetime) -> str:
    return f"{agent}-substrate-smoke-{now_utc.strftime('%Y-%m-%d')}"


def _substrate_smoke_task_id(
    *,
    agent: str,
    now_utc: datetime,
    index: int,
) -> str:
    return f"{_daily_smoke_task_prefix(agent, now_utc)}{index}"


def _completed_substrate_smoke_task_ids(
    *,
    agent: str,
    events: Sequence[Mapping[str, Any]],
    bridge_root: Path,
    now_utc: datetime,
) -> set[str]:
    prefix = _daily_smoke_task_prefix(agent, now_utc)
    legacy_task_id = _legacy_daily_smoke_task_id(agent=agent, now_utc=now_utc)
    completed: set[str] = set()

    for event in events:
        if str(event.get("agent", "")) != agent:
            continue
        task_id = str(event.get("task_id", ""))
        if task_id != legacy_task_id and not task_id.startswith(prefix):
            continue
        if _is_successful_completion_event(event):
            completed.add(task_id)

    done_dir = bridge_root / "work_queue" / "done"
    try:
        done_files = list(done_dir.glob("*.json")) if done_dir.exists() else []
    except OSError:
        done_files = []

    for done_file in done_files:
        try:
            payload = json.loads(done_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(payload.get("agent", "")) != agent:
            continue
        task_id = str(payload.get("task_id", ""))
        if task_id != legacy_task_id and not task_id.startswith(prefix):
            continue
        status = str(
            payload.get("release_status")
            or payload.get("status")
            or payload.get("release_message")
            or ""
        )
        if _status_is_successful(status):
            completed.add(task_id)

    return completed


def _is_successful_completion_event(event: Mapping[str, Any]) -> bool:
    event_type = str(event.get("type", "")).lower()
    if event_type not in SUCCESSFUL_COMPLETION_TYPES:
        return False
    return _status_is_successful(str(event.get("status", "")))


def _status_is_successful(status: str) -> bool:
    tokens = {
        token
        for token in status.lower().replace("-", "_").split("_")
        if token
    }
    return any(token in SUCCESSFUL_COMPLETION_STATUSES for token in tokens)


def _parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


if __name__ == "__main__":
    raise SystemExit(main())
