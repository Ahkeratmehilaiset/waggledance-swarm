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
    "complete",
    "completed",
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


# A compact strategic fallback pool derived from
# docs/architecture/DREAM_MODE_AGENDA.md. These are deliberately framed as
# read-only discovery seeds so the idle/dream loop can keep moving after the
# daily smoke rotation is exhausted without silently inventing write authority.
DREAM_MODE_CANDIDATES: tuple[dict[str, str], ...] = (
    {
        "category": "competitor-tracking",
        "slug": "evidence-matrix-staleness-audit",
        "target": "docs/benchmarks/COMPETITIVE_EVIDENCE_MATRIX_2026.md",
        "rationale": "flag PROVEN/MEASURED competitor evidence older than the dream-mode freshness cadence",
    },
    {
        "category": "security",
        "slug": "bridge-event-schema-sweep",
        "target": ".agent-bridge/shared/events.jsonl",
        "rationale": "scan bridge events for malformed payloads or unexpected agent IDs that landed despite schema guards",
    },
    {
        "category": "wd-sub-area",
        "slug": "idle-deferred-item-lift",
        "target": "docs/architecture/IDLE_PROTOCOL_V1.md",
        "rationale": "identify the closest deferred idle-protocol item that can be promoted safely",
    },
    {
        "category": "roadmap",
        "slug": "multi-instance-replay-sanitization-contract",
        "target": "docs/architecture/DREAM_MODE_AGENDA.md",
        "rationale": "advance the five-ingredient roadmap by drafting the next sanitization-contract surface",
    },
    {
        "category": "roadmap",
        "slug": "counterfactual-eval-extension-inventory",
        "target": "tools/idle_consensus_artifact.py",
        "rationale": "find the smallest replay extension needed for stored consensus versus candidate diff evaluation",
    },
    {
        "category": "wd-sub-area",
        "slug": "contracts-regression-gap-audit",
        "target": "tests/contracts/",
        "rationale": "identify one missing contract test that would have caught a recent substrate regression",
    },
    {
        "category": "security",
        "slug": "autonomous-merge-denylist-diff-audit",
        "target": "docs/architecture/IDLE_AUTONOMY_CHARTER.md",
        "rationale": "compare autonomous-merge denylist intent against recent merged substrate diffs",
    },
    {
        "category": "competitor-tracking",
        "slug": "local-runtime-baseline-refresh",
        "target": "docs/benchmarks/COMPETITIVE_EVIDENCE_MATRIX_2026.md",
        "rationale": "check whether a local-runtime baseline should be added or retired from the WD comparison matrix",
    },
)


# Final non-mutating maintenance lane for the case where the daily substrate
# and strategic dream-mode pools are already exhausted. These candidates are
# recommendations only: the tool does not call GitHub, mutate files, or claim
# work on the caller's behalf.
OPERATIONAL_SCOUT_CANDIDATES: tuple[dict[str, str], ...] = (
    {
        "slug": "open-pr-queue-scout",
        "target": "github:pull-requests",
        "rationale": "summarize open PR mergeability, CI, and review state so the next agent can unblock the queue",
        "recommended_command": (
            "gh pr list --state open --json "
            "number,title,headRefName,isDraft,mergeStateStatus,statusCheckRollup,updatedAt,url "
            "--limit 20"
        ),
    },
    {
        "slug": "bridge-stale-incoming-sweep",
        "target": ".agent-bridge/shared/events.jsonl",
        "rationale": "inspect stale incoming bridge requests and report whether any safe closeout follow-up is warranted",
        "recommended_command_template": (
            "C:\\Python\\project2-master\\.venv\\Scripts\\python.exe "
            "tools\\bridge_next_action.py --agent {agent} --json"
        ),
    },
    {
        "slug": "main-bridge-idle-health-smoke",
        "target": "tests/tools/test_agent_next_task.py tests/tools/test_bridge_next_action.py tests/tools/test_idle_loop_once.py",
        "rationale": "verify core bridge and idle selectors after mainline churn before the next claim",
        "recommended_command": (
            "C:\\Python\\project2-master\\.venv\\Scripts\\python.exe -m pytest "
            "tests\\tools\\test_agent_next_task.py "
            "tests\\tools\\test_bridge_next_action.py "
            "tests\\tools\\test_idle_loop_once.py -q"
        ),
    },
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Pick a deterministic continuous-loop next task for one bridge agent."
        ),
    )
    parser.add_argument("--agent", required=True)
    parser.add_argument(
        "--events",
        type=Path,
        default=None,
        help=(
            "Bridge events JSONL path. Defaults to "
            "<bridge-root>/shared/events.jsonl."
        ),
    )
    parser.add_argument("--bridge-root", type=Path, default=None)
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
    bridge_root = _bridge_root_for_args(args.events, args.bridge_root)
    events_path = _events_path_for_args(args.events, bridge_root)
    report = evaluate_agent_next_task(
        agent=args.agent,
        events_path=events_path,
        bridge_root=bridge_root,
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
        profile = report.get("agent_profile")
        if isinstance(profile, Mapping):
            print(f"agent_profile: {_format_metadata(profile)}")
        snapshot = report.get("claim_snapshot")
        if isinstance(snapshot, Mapping):
            own_count = len(snapshot.get("own", []))
            foreign_count = len(snapshot.get("foreign_write", []))
            print(f"claim_snapshot: own={own_count} foreign_write={foreign_count}")
        candidate = report.get("candidate")
        if candidate:
            print(
                f"candidate: {candidate.get('kind')} target={candidate.get('target')}"
            )
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
            **_bridge_context(bridge_recommendation),
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
            completed_dream_mode_task_ids = _completed_dream_mode_task_ids(
                events=events,
                bridge_root=Path(bridge_root),
                now_utc=now_utc,
            )
            active_dream_mode_task_ids = _active_dream_mode_task_ids(
                claims=claims,
                now_utc=now_utc,
            )
            dream_candidate = _pick_dream_mode_seed(
                agent=agent,
                now_utc=now_utc,
                completed_task_ids=completed_dream_mode_task_ids,
                active_task_ids=active_dream_mode_task_ids,
            )
            if dream_candidate is not None:
                return {
                    "decision": "claim_dream_mode_seed",
                    "next_action": "claim_and_run",
                    "exit_code": 0,
                    "agent": agent,
                    "underlying_bridge_action": bridge_action,
                    "bridge_recommendation": bridge_recommendation,
                    **_bridge_context(bridge_recommendation),
                    "completed_substrate_smoke_task_ids": sorted(completed_task_ids),
                    "completed_dream_mode_task_ids": sorted(
                        completed_dream_mode_task_ids
                    ),
                    "active_dream_mode_task_ids": sorted(active_dream_mode_task_ids),
                    "candidate": dream_candidate,
                    "notes": [
                        (
                            "bridge_next_action left the next-work choice open "
                            "and every substrate-smoke candidate for this agent "
                            "is already completed today"
                        ),
                        (
                            "the picker advanced to a deterministic dream-mode "
                            "seed from docs/architecture/DREAM_MODE_AGENDA.md "
                            "instead of returning an abstract non-smoke action"
                        ),
                        (
                            "the candidate is a recommendation only; the caller "
                            "is responsible for claiming it before running"
                        ),
                    ],
                }
            completed_operational_scout_task_ids = (
                _completed_operational_scout_task_ids(
                    events=events,
                    bridge_root=Path(bridge_root),
                    now_utc=now_utc,
                )
            )
            active_operational_scout_task_ids = _active_operational_scout_task_ids(
                claims=claims,
                now_utc=now_utc,
            )
            operational_candidate = _pick_operational_scout(
                agent=agent,
                now_utc=now_utc,
                completed_task_ids=completed_operational_scout_task_ids,
                active_task_ids=active_operational_scout_task_ids,
            )
            if operational_candidate is not None:
                return {
                    "decision": "claim_operational_scout",
                    "next_action": "claim_and_run",
                    "exit_code": 0,
                    "agent": agent,
                    "underlying_bridge_action": bridge_action,
                    "bridge_recommendation": bridge_recommendation,
                    **_bridge_context(bridge_recommendation),
                    "completed_substrate_smoke_task_ids": sorted(completed_task_ids),
                    "completed_dream_mode_task_ids": sorted(
                        completed_dream_mode_task_ids
                    ),
                    "active_dream_mode_task_ids": sorted(active_dream_mode_task_ids),
                    "completed_operational_scout_task_ids": sorted(
                        completed_operational_scout_task_ids
                    ),
                    "active_operational_scout_task_ids": sorted(
                        active_operational_scout_task_ids
                    ),
                    "candidate": operational_candidate,
                    "notes": [
                        (
                            "bridge_next_action left the next-work choice open, "
                            "and every substrate-smoke and dream-mode candidate "
                            "is already completed or actively claimed today"
                        ),
                        (
                            "the picker advanced to a deterministic read-only "
                            "operational scout instead of returning an operator "
                            "handoff immediately"
                        ),
                        (
                            "the candidate is a recommendation only; the caller "
                            "is responsible for claiming it before running"
                        ),
                    ],
                }
            completed_continuous_operational_scout_task_ids = (
                _completed_continuous_operational_scout_task_ids(
                    events=events,
                    bridge_root=Path(bridge_root),
                    now_utc=now_utc,
                )
            )
            active_continuous_operational_scout_task_ids = (
                _active_continuous_operational_scout_task_ids(
                    claims=claims,
                    now_utc=now_utc,
                )
            )
            continuous_candidate = _pick_continuous_operational_scout(
                agent=agent,
                now_utc=now_utc,
                completed_task_ids=completed_continuous_operational_scout_task_ids,
                active_task_ids=active_continuous_operational_scout_task_ids,
            )
            if continuous_candidate is not None:
                return {
                    "decision": "claim_continuous_operational_scout",
                    "next_action": "claim_and_run",
                    "exit_code": 0,
                    "agent": agent,
                    "underlying_bridge_action": bridge_action,
                    "bridge_recommendation": bridge_recommendation,
                    **_bridge_context(bridge_recommendation),
                    "completed_substrate_smoke_task_ids": sorted(completed_task_ids),
                    "completed_dream_mode_task_ids": sorted(
                        completed_dream_mode_task_ids
                    ),
                    "active_dream_mode_task_ids": sorted(active_dream_mode_task_ids),
                    "completed_operational_scout_task_ids": sorted(
                        completed_operational_scout_task_ids
                    ),
                    "active_operational_scout_task_ids": sorted(
                        active_operational_scout_task_ids
                    ),
                    "completed_continuous_operational_scout_task_ids": sorted(
                        completed_continuous_operational_scout_task_ids
                    ),
                    "active_continuous_operational_scout_task_ids": sorted(
                        active_continuous_operational_scout_task_ids
                    ),
                    "candidate": continuous_candidate,
                    "notes": [
                        (
                            "bridge_next_action left the next-work choice open, "
                            "and every daily substrate-smoke, dream-mode, and "
                            "operational scout candidate is already completed "
                            "or actively claimed today"
                        ),
                        (
                            "the picker advanced to a continuous read-only "
                            "operational scout with a unique sequence task id "
                            "instead of returning an operator handoff"
                        ),
                        (
                            "the candidate is a recommendation only; the caller "
                            "is responsible for claiming it before running"
                        ),
                    ],
                }
            return {
                "decision": "dream_mode_pool_exhausted",
                "next_action": "operator_handles",
                "exit_code": 0,
                "agent": agent,
                "underlying_bridge_action": bridge_action,
                "bridge_recommendation": bridge_recommendation,
                **_bridge_context(bridge_recommendation),
                "completed_substrate_smoke_task_ids": sorted(completed_task_ids),
                "completed_dream_mode_task_ids": sorted(completed_dream_mode_task_ids),
                "active_dream_mode_task_ids": sorted(active_dream_mode_task_ids),
                "completed_operational_scout_task_ids": sorted(
                    completed_operational_scout_task_ids
                ),
                "active_operational_scout_task_ids": sorted(
                    active_operational_scout_task_ids
                ),
                "completed_continuous_operational_scout_task_ids": sorted(
                    completed_continuous_operational_scout_task_ids
                ),
                "active_continuous_operational_scout_task_ids": sorted(
                    active_continuous_operational_scout_task_ids
                ),
                "notes": [
                    (
                        "bridge_next_action left the next-work choice open, but "
                        "every substrate-smoke, dream-mode, and operational "
                        "scout candidate is already completed or actively "
                        "claimed today"
                    ),
                    (
                        "the scheduler should escalate instead of rerunning an "
                        "already-completed daily candidate"
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
            **_bridge_context(bridge_recommendation),
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
        **_bridge_context(bridge_recommendation),
        "notes": [
            (
                f"bridge_next_action returned an unrecognized action "
                f"({bridge_action!r}); the scheduler should escalate"
            )
        ],
    }


def _bridge_context(bridge_recommendation: Mapping[str, Any]) -> dict[str, Any]:
    context: dict[str, Any] = {}
    for key in ("agent_profile", "claim_snapshot"):
        value = bridge_recommendation.get(key)
        if value:
            context[key] = value
    return context


def _format_metadata(metadata: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in ("role", "agent_uuid", "session_id"):
        value = str(metadata.get(key) or "")
        if value:
            parts.append(f"{key}={value}")
    capabilities = metadata.get("capabilities")
    if isinstance(capabilities, str):
        capability_items = [
            item.strip() for item in capabilities.split(",") if item.strip()
        ]
    elif isinstance(capabilities, Sequence):
        capability_items = [
            str(item).strip() for item in capabilities if str(item).strip()
        ]
    else:
        capability_items = []
    if capability_items:
        parts.append(f"capabilities={','.join(capability_items)}")
    return " ".join(parts) if parts else "none"


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
        is_legacy_completion = candidate_offset == 0 and legacy_task_id in completed
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


def _pick_dream_mode_seed(
    *,
    agent: str,
    now_utc: datetime,
    completed_task_ids: set[str] | None = None,
    active_task_ids: set[str] | None = None,
) -> dict[str, Any] | None:
    """Pick one strategic dream-mode seed deterministically for ``agent``."""
    pool = DREAM_MODE_CANDIDATES
    day_of_year = now_utc.timetuple().tm_yday
    agent_salt = sum(ord(c) for c in agent) % len(pool)
    start_index = (day_of_year + agent_salt) % len(pool)
    completed = completed_task_ids or set()
    active = active_task_ids or set()
    unavailable = completed | active

    index = start_index
    offset = 0
    for candidate_offset in range(len(pool)):
        candidate_index = (start_index + candidate_offset) % len(pool)
        candidate = pool[candidate_index]
        candidate_task_id = _dream_mode_task_id(
            now_utc=now_utc,
            category=candidate["category"],
            slug=candidate["slug"],
        )
        if candidate_task_id not in unavailable:
            index = candidate_index
            offset = candidate_offset
            break
    else:
        return None

    chosen = pool[index]
    category = chosen["category"]
    slug = chosen["slug"]
    task_id = _dream_mode_task_id(
        now_utc=now_utc,
        category=category,
        slug=slug,
    )
    return {
        "kind": "advance_dream_mode_seed",
        "category": category,
        "slug": slug,
        "target": chosen["target"],
        "rationale": chosen["rationale"],
        "task_id_suggestion": task_id,
        "mode": "read-only",
        "write_scope": [],
        "agenda": "docs/architecture/DREAM_MODE_AGENDA.md",
        "acceptance": (
            "Produce a concise finding or next-step proposal tied to the "
            "target path and route any source change through a separate "
            "write claim."
        ),
        "rotation": {
            "agent": agent,
            "day_of_year": day_of_year,
            "agent_salt": agent_salt,
            "pool_size": len(pool),
            "start_index": start_index,
            "index": index,
            "offset": offset,
            "skipped_completed_task_ids": sorted(completed),
            "skipped_active_task_ids": sorted(active),
        },
    }


def _pick_operational_scout(
    *,
    agent: str,
    now_utc: datetime,
    completed_task_ids: set[str] | None = None,
    active_task_ids: set[str] | None = None,
) -> dict[str, Any] | None:
    """Pick one final read-only operational scout deterministically."""
    pool = OPERATIONAL_SCOUT_CANDIDATES
    day_of_year = now_utc.timetuple().tm_yday
    agent_salt = sum(ord(c) for c in agent) % len(pool)
    start_index = (day_of_year + agent_salt) % len(pool)
    completed = completed_task_ids or set()
    active = active_task_ids or set()
    unavailable = completed | active

    index = start_index
    offset = 0
    for candidate_offset in range(len(pool)):
        candidate_index = (start_index + candidate_offset) % len(pool)
        candidate = pool[candidate_index]
        candidate_task_id = _operational_scout_task_id(
            now_utc=now_utc,
            slug=candidate["slug"],
        )
        if candidate_task_id not in unavailable:
            index = candidate_index
            offset = candidate_offset
            break
    else:
        return None

    chosen = pool[index]
    slug = chosen["slug"]
    task_id = _operational_scout_task_id(now_utc=now_utc, slug=slug)
    command_template = chosen.get("recommended_command_template")
    if command_template is None:
        command_template = chosen["recommended_command"]
    return {
        "kind": "operational_read_only_scout",
        "slug": slug,
        "target": chosen["target"],
        "rationale": chosen["rationale"],
        "task_id_suggestion": task_id,
        "mode": "read-only",
        "write_scope": [],
        "acceptance": (
            "Produce a concise bridge finding or handoff with the observed "
            "queue/health state; route any source change through a separate "
            "write claim."
        ),
        "recommended_command": command_template.format(agent=agent),
        "rotation": {
            "agent": agent,
            "day_of_year": day_of_year,
            "agent_salt": agent_salt,
            "pool_size": len(pool),
            "start_index": start_index,
            "index": index,
            "offset": offset,
            "skipped_completed_task_ids": sorted(completed),
            "skipped_active_task_ids": sorted(active),
        },
    }


def _pick_continuous_operational_scout(
    *,
    agent: str,
    now_utc: datetime,
    completed_task_ids: set[str] | None = None,
    active_task_ids: set[str] | None = None,
) -> dict[str, Any] | None:
    """Pick a read-only scout after all daily candidate pools are exhausted."""
    pool = OPERATIONAL_SCOUT_CANDIDATES
    day_of_year = now_utc.timetuple().tm_yday
    agent_salt = sum(ord(c) for c in agent) % len(pool)
    completed = completed_task_ids or set()
    active = active_task_ids or set()
    unavailable = completed | active

    for sequence in range(1000):
        index = (day_of_year + agent_salt + sequence) % len(pool)
        chosen = pool[index]
        slug = chosen["slug"]
        task_id = _continuous_operational_scout_task_id(
            now_utc=now_utc,
            slug=slug,
            sequence=sequence,
        )
        if task_id in unavailable:
            continue
        command_template = chosen.get("recommended_command_template")
        if command_template is None:
            command_template = chosen["recommended_command"]
        return {
            "kind": "continuous_operational_read_only_scout",
            "slug": slug,
            "target": chosen["target"],
            "rationale": chosen["rationale"],
            "task_id_suggestion": task_id,
            "mode": "read-only",
            "write_scope": [],
            "acceptance": (
                "Produce a concise bridge finding or handoff with fresh "
                "queue/health evidence; route any source change through a "
                "separate write claim."
            ),
            "recommended_command": command_template.format(agent=agent),
            "rotation": {
                "agent": agent,
                "day_of_year": day_of_year,
                "agent_salt": agent_salt,
                "pool_size": len(pool),
                "index": index,
                "sequence": sequence,
                "skipped_completed_task_ids": sorted(completed),
                "skipped_active_task_ids": sorted(active),
            },
        }
    return None


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


def _dream_mode_task_id(
    *,
    now_utc: datetime,
    category: str,
    slug: str,
) -> str:
    return f"dream-mode-{category}-{slug}-{now_utc.strftime('%Y-%m-%d')}"


def _operational_scout_task_id(
    *,
    now_utc: datetime,
    slug: str,
) -> str:
    return f"operational-scout-{slug}-{now_utc.strftime('%Y-%m-%d')}"


def _continuous_operational_scout_task_id(
    *,
    now_utc: datetime,
    slug: str,
    sequence: int,
) -> str:
    return (
        f"continuous-operational-scout-{slug}-"
        f"{now_utc.strftime('%Y-%m-%d')}-{sequence}"
    )


def _is_same_day_dream_mode_task_id(task_id: str, now_utc: datetime) -> bool:
    return _canonical_same_day_dream_mode_task_id(task_id, now_utc) is not None


def _canonical_same_day_dream_mode_task_id(
    task_id: str,
    now_utc: datetime,
) -> str | None:
    if not task_id.startswith("dream-mode-"):
        return None
    hyphenated_day = now_utc.strftime("%Y-%m-%d")
    compact_day = now_utc.strftime("%Y%m%d")
    if task_id.endswith(f"-{hyphenated_day}"):
        return task_id
    if task_id.endswith(f"-{compact_day}"):
        return task_id[: -len(compact_day)] + hyphenated_day
    return None


def _is_same_day_operational_scout_task_id(
    task_id: str,
    now_utc: datetime,
) -> bool:
    return task_id.startswith("operational-scout-") and task_id.endswith(
        f"-{now_utc.strftime('%Y-%m-%d')}"
    )


def _is_same_day_continuous_operational_scout_task_id(
    task_id: str,
    now_utc: datetime,
) -> bool:
    return (
        task_id.startswith("continuous-operational-scout-")
        and f"-{now_utc.strftime('%Y-%m-%d')}-" in task_id
    )


def _bridge_root_for_args(events_path: Path | None, bridge_root: Path | None) -> Path:
    if bridge_root is not None:
        return bridge_root
    if (
        events_path is not None
        and events_path.name == "events.jsonl"
        and events_path.parent.name == "shared"
    ):
        return events_path.parent.parent
    return DEFAULT_BRIDGE_ROOT


def _events_path_for_args(events_path: Path | None, bridge_root: Path) -> Path:
    if events_path is not None:
        return events_path
    return bridge_root / "shared" / "events.jsonl"


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


def _completed_dream_mode_task_ids(
    *,
    events: Sequence[Mapping[str, Any]],
    bridge_root: Path,
    now_utc: datetime,
) -> set[str]:
    completed: set[str] = set()

    for event in events:
        task_id = str(event.get("task_id", ""))
        canonical_task_id = _canonical_same_day_dream_mode_task_id(
            task_id,
            now_utc,
        )
        if canonical_task_id is None:
            continue
        if _is_successful_completion_event(event):
            completed.add(canonical_task_id)

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
        task_id = str(payload.get("task_id", ""))
        canonical_task_id = _canonical_same_day_dream_mode_task_id(
            task_id,
            now_utc,
        )
        if canonical_task_id is None:
            continue
        status = str(
            payload.get("release_status")
            or payload.get("status")
            or payload.get("release_message")
            or ""
        )
        if _status_is_successful(status):
            completed.add(canonical_task_id)

    return completed


def _completed_operational_scout_task_ids(
    *,
    events: Sequence[Mapping[str, Any]],
    bridge_root: Path,
    now_utc: datetime,
) -> set[str]:
    completed: set[str] = set()

    for event in events:
        task_id = str(event.get("task_id", ""))
        if not _is_same_day_operational_scout_task_id(task_id, now_utc):
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
        task_id = str(payload.get("task_id", ""))
        if not _is_same_day_operational_scout_task_id(task_id, now_utc):
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


def _completed_continuous_operational_scout_task_ids(
    *,
    events: Sequence[Mapping[str, Any]],
    bridge_root: Path,
    now_utc: datetime,
) -> set[str]:
    completed: set[str] = set()

    for event in events:
        task_id = str(event.get("task_id", ""))
        if not _is_same_day_continuous_operational_scout_task_id(task_id, now_utc):
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
        task_id = str(payload.get("task_id", ""))
        if not _is_same_day_continuous_operational_scout_task_id(task_id, now_utc):
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


def _active_dream_mode_task_ids(
    *,
    claims: Sequence[Any],
    now_utc: datetime,
) -> set[str]:
    active: set[str] = set()
    for claim in claims:
        task_id = str(getattr(claim, "task_id", ""))
        canonical_task_id = _canonical_same_day_dream_mode_task_id(
            task_id,
            now_utc,
        )
        if canonical_task_id is not None:
            active.add(canonical_task_id)
    return active


def _active_operational_scout_task_ids(
    *,
    claims: Sequence[Any],
    now_utc: datetime,
) -> set[str]:
    active: set[str] = set()
    for claim in claims:
        task_id = str(getattr(claim, "task_id", ""))
        if _is_same_day_operational_scout_task_id(task_id, now_utc):
            active.add(task_id)
    return active


def _active_continuous_operational_scout_task_ids(
    *,
    claims: Sequence[Any],
    now_utc: datetime,
) -> set[str]:
    active: set[str] = set()
    for claim in claims:
        task_id = str(getattr(claim, "task_id", ""))
        if _is_same_day_continuous_operational_scout_task_id(task_id, now_utc):
            active.add(task_id)
    return active


def _is_successful_completion_event(event: Mapping[str, Any]) -> bool:
    event_type = str(event.get("type", "")).lower()
    if event_type not in SUCCESSFUL_COMPLETION_TYPES:
        return False
    return _status_is_successful(str(event.get("status", "")))


def _status_is_successful(status: str) -> bool:
    tokens = {token for token in status.lower().replace("-", "_").split("_") if token}
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
