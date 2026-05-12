#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Build a compact fresh-agent flight plan from bridge events.

The builder is deterministic and offline. It reads the live bridge JSONL,
projects events through the EIG2 bridge projection shim when available,
classifies blocker text with the deterministic classifier, and emits the
small artifact a fresh Codex or Claude session should read before acting.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "agent-flight-plan-v1"
STATUS_CONTRACT_PATH = PROJECT_ROOT / "docs" / "eig2" / "contracts" / "agent_flight_plan_statuses.json"
DEFAULT_EVENTS_PATH = PROJECT_ROOT / ".agent-bridge" / "shared" / "events.jsonl"

FORMAL_STATUSES = {
    "rco_requested": "rco_requested",
    "rco_done": "rco_done",
    "consensus_proposal": "consensus_proposal",
    "consensus_accepted": "consensus_accepted",
    "claim_required": "claim_required",
    "missing_claim": "missing_claim",
}

REQUEST_TYPES = {
    "message",
    "finding",
    "handoff",
    "peer_review_request",
    "simulation_open",
    "sandbox_drop",
    "decision",
}
ANSWER_TYPES = {"message", "done", "decision", "blocked", "finding", "test", "release"}
ANSWER_STATUS_FRAGMENTS = (
    "accepted",
    "ack",
    "answered",
    "closed",
    "done",
    "merged",
    "pass",
    "resolved",
    "superseded",
)
OPEN_STATUS_FRAGMENTS = (
    "open",
    "proposal",
    "request",
    "ready",
    "pushed",
    "active",
    "blocked",
)
CLAIM_CLOSING_TYPES = {"release", "done"}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _load_module(path: Path, module_name: str) -> Any | None:
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _projection_fn(root: Path) -> Callable[[dict[str, Any]], dict[str, Any]]:
    module = _load_module(root / ".orchestrator" / "eig2_bridge_projection.py", "_eig2_bridge_projection")
    fn = getattr(module, "project_live_event", None) if module is not None else None
    return fn if callable(fn) else (lambda event: event)


def _classifier_fn(root: Path) -> Callable[[str | dict[str, Any]], str]:
    module = _load_module(root / ".orchestrator" / "bridge_classify.py", "_bridge_classify")
    fn = getattr(module, "classify", None) if module is not None else None
    if not callable(fn):
        return lambda _payload: "unknown_root_cause_needed"

    def classify(payload: str | dict[str, Any]) -> str:
        result = fn(payload)
        return str(getattr(result, "value", result))

    return classify


def _load_statuses(path: Path = STATUS_CONTRACT_PATH) -> dict[str, str]:
    if not path.exists():
        return dict(FORMAL_STATUSES)
    data = json.loads(path.read_text(encoding="utf-8"))
    statuses = data.get("statuses", {})
    return {key: str(statuses.get(key, value)) for key, value in FORMAL_STATUSES.items()}


def load_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _infer_claims_dir(events_path: Path) -> Path | None:
    if events_path.name == "events.jsonl" and events_path.parent.name == "shared":
        return events_path.parent.parent / "work_queue" / "claims"
    return None


def _load_claim_files(claims_dir: Path) -> list[dict[str, Any]]:
    if not claims_dir.exists():
        return []
    claims: list[dict[str, Any]] = []
    for path in sorted(claims_dir.glob("*.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(item, dict):
            claims.append(item)
    return claims


def _task_id(event: dict[str, Any]) -> str:
    return str(event.get("task_id") or event.get("id") or "")


def _agent(event: dict[str, Any]) -> str:
    value = str(event.get("agent") or event.get("author") or "unknown").lower()
    return value if value in {"codex", "claude", "operator", "system"} else "unknown"


def _to_agent(event: dict[str, Any]) -> str:
    value = str(event.get("to") or "").lower()
    return value if value in {"codex", "claude", "operator", "system"} else ""


def _status(event: dict[str, Any]) -> str:
    return str(event.get("status") or "").lower()


def _type(event: dict[str, Any]) -> str:
    return str(event.get("type") or event.get("message_type") or "").lower()


def _message(event: dict[str, Any], limit: int = 220) -> str:
    text = " ".join(str(event.get("message") or "").split())
    return _trim(text, limit=limit)


def _trim(text: str, limit: int = 220) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _is_request_like(event: dict[str, Any]) -> bool:
    status = _status(event)
    return _type(event) in REQUEST_TYPES and any(part in status for part in OPEN_STATUS_FRAGMENTS)


def _is_answer_like(event: dict[str, Any]) -> bool:
    status = _status(event)
    return _type(event) in ANSWER_TYPES and any(part in status for part in ANSWER_STATUS_FRAGMENTS)


def _is_consensus(event: dict[str, Any], statuses: dict[str, str]) -> bool:
    status = _status(event)
    return status in {statuses["consensus_proposal"], statuses["consensus_accepted"]} or status.startswith("consensus_")


def _has_rco(event: dict[str, Any], statuses: dict[str, str]) -> bool:
    text = f"{_status(event)} {_message(event, limit=2000)}".lower()
    return statuses["rco_requested"] in text or statuses["rco_done"] in text or " rco " in f" {text} "


def _suggest_owner(thread: dict[str, Any], target_agent: str) -> str:
    if thread["active_claim_owner"]:
        return thread["active_claim_owner"]
    if thread["last_request_index"] > thread["last_answer_index"]:
        request = thread["events"][thread["last_request_index"]]
        request_to = _to_agent(request)
        if request_to:
            return request_to
    last = thread["events"][-1]
    to_agent = _to_agent(last)
    if to_agent:
        return to_agent
    agents = [agent for agent in sorted(thread["agents"]) if agent != "system"]
    if target_agent in agents:
        return target_agent
    if len(agents) == 1:
        return agents[0]
    return "unknown"


def _thread_summary(
    task_id: str,
    thread: dict[str, Any],
    *,
    target_agent: str,
    classify: Callable[[str | dict[str, Any]], str],
) -> dict[str, Any]:
    last = thread["events"][-1]
    return {
        "task_id": task_id,
        "last_ts_utc": str(last.get("ts_utc") or last.get("timestamp") or ""),
        "agents": sorted(thread["agents"]),
        "status": _status(last),
        "suggested_owner": _suggest_owner(thread, target_agent),
        "message": _message(last),
        "classification": classify(_message(last, limit=2000)),
    }


def _claim_file_summary(
    claim: dict[str, Any],
    *,
    classify: Callable[[str | dict[str, Any]], str],
) -> dict[str, Any]:
    task_id = str(claim.get("task_id") or "")
    agent = str(claim.get("agent") or "unknown").lower()
    if agent not in {"codex", "claude", "operator", "system"}:
        agent = "unknown"
    message = str(claim.get("summary") or claim.get("message") or "")
    return {
        "task_id": task_id,
        "last_ts_utc": str(claim.get("last_heartbeat_utc") or claim.get("claimed_at_utc") or ""),
        "agents": [agent],
        "status": "active",
        "suggested_owner": agent,
        "message": _trim(message),
        "classification": classify(message),
    }


def _build_threads(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    threads: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "events": [],
            "agents": set(),
            "has_claim": False,
            "active_claim_owner": "",
            "last_request_index": -1,
            "last_answer_index": -1,
            "has_rco": False,
        }
    )
    for event in events:
        task_id = _task_id(event)
        if not task_id:
            continue
        thread = threads[task_id]
        thread["events"].append(event)
        thread["agents"].add(_agent(event))
        event_type = _type(event)
        status = _status(event)
        index = len(thread["events"]) - 1
        if event_type == "claim" and status == "active":
            thread["has_claim"] = True
            thread["active_claim_owner"] = _agent(event)
        elif event_type in CLAIM_CLOSING_TYPES:
            thread["active_claim_owner"] = ""
        if _is_request_like(event):
            thread["last_request_index"] = index
        if _is_answer_like(event):
            thread["last_answer_index"] = index
    return dict(threads)


def _next_actions(
    *,
    active_claims: list[dict[str, Any]],
    unresolved_threads: list[dict[str, Any]],
    consensus_topics: list[dict[str, Any]],
    target_agent: str,
) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    for item in active_claims:
        if item["suggested_owner"] in {target_agent, "unknown"}:
            actions.append(
                {
                    "owner": target_agent,
                    "task_id": item["task_id"],
                    "action": "continue claimed work and publish status before waiting",
                    "reason": "active bridge claim is still open",
                }
            )
    for item in unresolved_threads:
        if item["suggested_owner"] in {target_agent, "unknown"}:
            actions.append(
                {
                    "owner": target_agent,
                    "task_id": item["task_id"],
                    "action": "answer or route unresolved bridge thread",
                    "reason": "latest request-like event has no later answer-like event",
                }
            )
    for item in consensus_topics[:3]:
        if item["status"] == "consensus_accepted":
            actions.append(
                {
                    "owner": target_agent,
                    "task_id": item["task_id"],
                    "action": "execute accepted consensus lane",
                    "reason": "consensus is machine-visible and accepted",
                }
            )
    if not actions:
        actions.append(
            {
                "owner": target_agent,
                "task_id": "",
                "action": "poll bridge, respect claims, then take the highest-priority unblocked lane",
                "reason": "no active claim or unresolved incoming thread was detected",
            }
        )
    return actions[:8]


def _bootstrap_prompt(plan: dict[str, Any], target_agent: str) -> str:
    lines = [
        "Read .agent-bridge/BOOTSTRAP.md and .agent-bridge/BRIDGE_PROTOCOL.md.",
        f"Agent: {target_agent}.",
        f"Objective: {plan['objective']}",
        f"Bridge events: {plan['source']['event_count']} through {plan['source']['last_event_ts_utc'] or 'n/a'}.",
        f"Claim coverage: {plan['metrics']['claim_coverage_pct']:.1f}% "
        f"({plan['metrics']['threads_with_claim']}/{plan['metrics']['task_threads_total']}).",
        f"Multi-agent threads without claims: {plan['metrics']['multi_agent_threads_without_claim']}.",
        "Active claims:",
    ]
    if plan["active_claims"]:
        lines.extend(f"- {item['task_id']}: {item['suggested_owner']} / {item['status']}" for item in plan["active_claims"][:5])
    else:
        lines.append("- none detected")
    lines.append("Unresolved bridge threads:")
    if plan["unresolved_threads"]:
        lines.extend(f"- {item['task_id']}: {item['suggested_owner']} / {item['status']}" for item in plan["unresolved_threads"][:5])
    else:
        lines.append("- none detected")
    lines.append("Next actions:")
    for index, action in enumerate(plan["next_actions"], start=1):
        task = f" [{action['task_id']}]" if action["task_id"] else ""
        lines.append(f"{index}. {action['action']}{task}")
    lines.extend(
        [
            "Rules:",
            "- Use a dedicated worktree for write work.",
            "- Claim write scope before editing.",
            "- Do not wait silently; publish status/test/done events.",
            "- Stop only for destructive, credential, legal, or unresolved write-conflict decisions.",
        ]
    )
    return "\n".join(lines[:40])


def build_plan(
    *,
    events_path: Path = DEFAULT_EVENTS_PATH,
    claims_dir: Path | None = None,
    objective: str,
    target_agent: str = "codex",
    now_utc: str | None = None,
    root: Path = PROJECT_ROOT,
    max_items: int = 12,
) -> dict[str, Any]:
    statuses = _load_statuses(root / "docs" / "eig2" / "contracts" / "agent_flight_plan_statuses.json")
    project = _projection_fn(root)
    classify = _classifier_fn(root)
    events = [project(event) | event for event in load_events(events_path)]
    threads = _build_threads(events)

    summaries = {
        task_id: _thread_summary(task_id, thread, target_agent=target_agent, classify=classify)
        for task_id, thread in threads.items()
    }
    claims_dir = claims_dir if claims_dir is not None else _infer_claims_dir(events_path)
    claim_files = _load_claim_files(claims_dir) if claims_dir is not None and claims_dir.exists() else None
    if claim_files is None:
        active_claims = [
            summaries[task_id]
            for task_id, thread in threads.items()
            if thread["active_claim_owner"]
        ]
    else:
        active_claims = [
            _claim_file_summary(claim, classify=classify)
            for claim in claim_files
            if str(claim.get("task_id") or "")
        ]
    unresolved_threads = [
        summaries[task_id]
        for task_id, thread in threads.items()
        if thread["last_request_index"] > thread["last_answer_index"]
    ]
    consensus_topics = [
        summaries[task_id]
        for task_id, thread in threads.items()
        if any(_is_consensus(event, statuses) for event in thread["events"])
    ]
    for collection in (active_claims, unresolved_threads, consensus_topics):
        collection.sort(key=lambda item: item["last_ts_utc"], reverse=True)

    task_total = len(threads)
    with_claim = sum(1 for thread in threads.values() if thread["has_claim"])
    multi_agent = [
        thread for thread in threads.values()
        if len({agent for agent in thread["agents"] if agent != "system"}) > 1
    ]
    formal_rco_threads = sum(
        1 for thread in threads.values()
        if any(_has_rco(event, statuses) for event in thread["events"])
    )
    plan: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": now_utc or _utcnow(),
        "objective": objective,
        "source": {
            "events_path": str(events_path),
            "event_count": len(events),
            "last_event_ts_utc": str(events[-1].get("ts_utc") or "") if events else "",
        },
        "metrics": {
            "task_threads_total": task_total,
            "threads_with_claim": with_claim,
            "claim_coverage_pct": round((with_claim / task_total * 100.0), 1) if task_total else 0.0,
            "multi_agent_threads": len(multi_agent),
            "multi_agent_threads_without_claim": sum(1 for thread in multi_agent if not thread["has_claim"]),
            "formal_rco_threads": formal_rco_threads,
            "consensus_topics": len(consensus_topics),
        },
        "formal_statuses": statuses,
        "active_claims": active_claims[:max_items],
        "unresolved_threads": unresolved_threads[:max_items],
        "consensus_topics": consensus_topics[:max_items],
        "next_actions": [],
        "bootstrap_prompt": "",
    }
    plan["next_actions"] = _next_actions(
        active_claims=plan["active_claims"],
        unresolved_threads=plan["unresolved_threads"],
        consensus_topics=plan["consensus_topics"],
        target_agent=target_agent,
    )
    plan["bootstrap_prompt"] = _bootstrap_prompt(plan, target_agent)
    return plan


def write_plan(plan: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(plan, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS_PATH)
    parser.add_argument("--claims-dir", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--objective", required=True)
    parser.add_argument("--agent", choices=["codex", "claude"], default="codex")
    parser.add_argument("--max-items", type=int, default=12)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    plan = build_plan(
        events_path=args.events,
        claims_dir=args.claims_dir,
        objective=args.objective,
        target_agent=args.agent,
        max_items=args.max_items,
    )
    write_plan(plan, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
