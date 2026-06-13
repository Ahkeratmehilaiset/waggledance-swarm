# SPDX-License-Identifier: BUSL-1.1
"""Build a read-only WD sprint status dashboard summary.

The summary is intentionally local and path-free: it reads bridge JSONL input
provided by the operator, emits JSON/Markdown, and never appends bridge events,
claims work, writes queue state, schedules work, or grants merge authority.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.work_queue import resolve_bridge_root  # noqa: E402

REPORT_VERSION = "wd.50pr_sprint_status_dashboard.v0"
DEFAULT_SPRINT_TASK_ID = "wd-50pr-sprint-plan-20260611-v2"
DEFAULT_EXPECTED_AGENTS = (
    "codex-tools-1",
    "claude-rco-1",
    "claude-rco-2",
    "operator",
)
CONSENSUS_STATUSES = {"consensus_pass", "changes_requested", "block"}
TERMINAL_TYPES = {"done", "release", "blocked"}
TERMINAL_STATUSES = {
    "done",
    "pass",
    "merged",
    "merged_observed",
    "operator_signed_merged_observed",
    "operator_signed_merge_receipt",
    "blocked",
    "released",
}
REDACTION_SENTINELS = ("PRIVATE" + "_MARKER", "_DO" + "_NOT" + "_LEAK")
SHA40_RE = re.compile(r"\b[0-9a-fA-F]{40}\b")
# Background keepalive event types: they prove the process is running, not
# that the agent is making progress, so liveness must not key on them.
SUBSTANTIVE_EXCLUDED_TYPES = frozenset({"heartbeat", "liveness"})
DEFAULT_STALLED_AFTER_MINUTES = 12.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render a path-free, read-only WD sprint status dashboard.",
    )
    parser.add_argument(
        "--events",
        type=Path,
        default=None,
        help="Path to bridge events.jsonl (default: <bridge-root>/shared/events.jsonl).",
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
    parser.add_argument("--sprint-task-id", default=DEFAULT_SPRINT_TASK_ID)
    parser.add_argument(
        "--expected-agent",
        action="append",
        dest="expected_agents",
        default=None,
        help="Expected consensus responder. Repeatable.",
    )
    parser.add_argument(
        "--expected-base-sha",
        default=None,
        help="Expected fresh origin/main SHA used for stale-base warnings.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override for deterministic output.",
    )
    parser.add_argument(
        "--max-active-claim-age-hours",
        type=float,
        default=24.0,
        help="Open claims older than this are counted as stale candidates.",
    )
    parser.add_argument(
        "--stalled-after-minutes",
        type=float,
        default=DEFAULT_STALLED_AFTER_MINUTES,
        help=(
            "An agent whose newest non-keepalive event is older than this is "
            "reported as stalled (heartbeats do not count as progress)."
        ),
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_wd_sprint_status_dashboard(
        events_path=args.events,
        bridge_root=args.bridge_root,
        sprint_task_id=args.sprint_task_id,
        expected_agents=tuple(args.expected_agents or DEFAULT_EXPECTED_AGENTS),
        expected_base_sha=args.expected_base_sha,
        max_active_claim_age_hours=args.max_active_claim_age_hours,
        stalled_after_minutes=args.stalled_after_minutes,
        now_utc=_parse_utc(args.now) if args.now else None,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report), end="")
    return 0 if report["ok"] else 1


def build_wd_sprint_status_dashboard(
    *,
    events_path: Path | None = None,
    bridge_root: Path | None = None,
    events: Sequence[Mapping[str, Any]] | None = None,
    sprint_task_id: str = DEFAULT_SPRINT_TASK_ID,
    expected_agents: Sequence[str] = DEFAULT_EXPECTED_AGENTS,
    expected_base_sha: str | None = None,
    max_active_claim_age_hours: float = 24.0,
    stalled_after_minutes: float = DEFAULT_STALLED_AFTER_MINUTES,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    generated_at_utc = generated_at.isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )
    blockers: list[str] = []
    loaded_events: list[Mapping[str, Any]] = []

    try:
        loaded_events = (
            list(events)
            if events is not None
            else read_bridge_events(_events_path(events_path, bridge_root=bridge_root))
        )
        _assert_no_redaction_sentinels(loaded_events)
    except ValueError as exc:
        blockers.append(f"events_input_refused:{exc}")

    consensus = _consensus_summary(
        loaded_events,
        sprint_task_id=sprint_task_id,
        expected_agents=expected_agents,
    )
    queue = _queue_summary(
        loaded_events,
        now_utc=generated_at,
        max_active_claim_age_hours=max_active_claim_age_hours,
    )
    agents = _agent_activity_summary(
        loaded_events,
        expected_agents=expected_agents,
        now_utc=generated_at,
        stalled_after_minutes=stalled_after_minutes,
    )
    stale_base = _stale_base_summary(
        loaded_events,
        expected_base_sha=expected_base_sha,
        sprint_task_id=sprint_task_id,
    )

    return {
        "report_version": REPORT_VERSION,
        "generated_at_utc": generated_at_utc,
        "ok": not blockers,
        "blockers": blockers,
        "sprint_task_id": sprint_task_id,
        "source": {
            "event_count": len(loaded_events),
            "events_digest": _events_digest(loaded_events),
            "source_redacted": True,
            "path_free": True,
            "messages_redacted": True,
            "payloads_redacted": True,
        },
        "consensus": consensus,
        "queue": queue,
        "agent_activity": agents,
        "stale_base": stale_base,
        "authority_boundary": _authority_boundary(),
    }


def read_bridge_events(path: Path) -> list[Mapping[str, Any]]:
    if not path.exists():
        raise ValueError("events_file_missing")
    events: list[Mapping[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line, parse_constant=_reject_json_constant)
        except json.JSONDecodeError as exc:
            raise ValueError(f"events_json_error:line_{line_no}") from exc
        if not isinstance(event, dict):
            raise ValueError(f"event_not_object:line_{line_no}")
        _assert_finite(event, path=f"line_{line_no}")
        events.append(event)
    return events


def render_markdown(report: Mapping[str, Any]) -> str:
    consensus = _mapping(report.get("consensus"))
    queue = _mapping(report.get("queue"))
    stale = _mapping(report.get("stale_base"))
    authority = _mapping(report.get("authority_boundary"))
    agents = _mapping(report.get("agent_activity"))
    lines = [
        "# WD 50-PR Sprint Status Dashboard",
        "",
        f"- report version: `{report.get('report_version')}`",
        f"- sprint task: `{report.get('sprint_task_id')}`",
        f"- input ok: `{_bool_text(report.get('ok') is True)}`",
        f"- events observed: `{_mapping(report.get('source')).get('event_count', 0)}`",
        "",
        "## Consensus",
        "",
        f"- request observed: `{_bool_text(consensus.get('request_observed') is True)}`",
        f"- responders: `{consensus.get('responded_count', 0)}/{consensus.get('expected_count', 0)}`",
        f"- complete: `{_bool_text(consensus.get('complete') is True)}`",
        f"- report grants execution: `{_bool_text(consensus.get('execution_allowed_by_report') is True)}`",
        "",
        "## Queue",
        "",
        f"- active claims: `{queue.get('active_claim_count', 0)}`",
        f"- stale open claim candidates: `{queue.get('stale_open_claim_count', 0)}`",
        f"- done events: `{queue.get('done_event_count', 0)}`",
        f"- passing tests: `{queue.get('test_pass_count', 0)}`",
        f"- findings: `{queue.get('finding_count', 0)}`",
        "",
        "## Agent Coverage",
        "",
        f"- agents seen: `{agents.get('seen_count', 0)}`",
        f"- expected agents missing activity: `{len(agents.get('missing_expected_agents', []))}`",
        (
            f"- expected agents stalled (no non-keepalive event in "
            f"{agents.get('stalled_after_minutes', DEFAULT_STALLED_AFTER_MINUTES)}min): "
            f"`{len(agents.get('stalled_expected_agents', []))}`"
        ),
        "",
        "## Freshness",
        "",
        f"- expected base provided: `{_bool_text(stale.get('expected_base_sha_present') is True)}`",
        f"- stale base warnings: `{stale.get('warning_count', 0)}`",
        "",
        "## Authority Boundary",
        "",
        f"- bridge append allowed: `{_bool_text(authority.get('bridge_append_allowed') is True)}`",
        f"- queue write allowed: `{_bool_text(authority.get('queue_write_allowed') is True)}`",
        f"- scheduler enqueue allowed: `{_bool_text(authority.get('scheduler_enqueue_allowed') is True)}`",
        f"- merge allowed: `{_bool_text(authority.get('merge_allowed') is True)}`",
    ]
    blockers = list(report.get("blockers") or [])
    if blockers:
        lines.extend(["", "## Blockers", ""])
        lines.extend(f"- `{blocker}`" for blocker in blockers)
    return "\n".join(lines) + "\n"


def _consensus_summary(
    events: Sequence[Mapping[str, Any]],
    *,
    sprint_task_id: str,
    expected_agents: Sequence[str],
) -> dict[str, Any]:
    sprint_events = [event for event in events if _string(event.get("task_id")) == sprint_task_id]
    requests = [
        event
        for event in sprint_events
        if _string(event.get("status")) == "consensus_requested"
    ]
    responses: dict[str, dict[str, str]] = {}
    for event in sprint_events:
        status = _string(event.get("status"))
        agent = _string(event.get("agent"))
        if status not in CONSENSUS_STATUSES or not agent:
            continue
        responses[agent] = {
            "status": status,
            "type": _string(event.get("type")),
            "ts_utc": _string(event.get("ts_utc")),
        }

    expected = tuple(dict.fromkeys(expected_agents))
    missing = [agent for agent in expected if agent not in responses]
    decision_counts = Counter(response["status"] for response in responses.values())
    blocked = decision_counts.get("block", 0) + decision_counts.get(
        "changes_requested", 0
    )
    complete = bool(requests) and not missing and blocked == 0
    return {
        "request_observed": bool(requests),
        "request_count": len(requests),
        "expected_agents": list(expected),
        "expected_count": len(expected),
        "responded_agents": sorted(responses),
        "responded_count": len(responses),
        "missing_agents": missing,
        "responses": {agent: responses[agent] for agent in sorted(responses)},
        "decision_counts": dict(sorted(decision_counts.items())),
        "complete": complete,
        "blocked_or_changes_requested": blocked > 0,
        "execution_allowed_by_report": False,
    }


def _queue_summary(
    events: Sequence[Mapping[str, Any]],
    *,
    now_utc: datetime,
    max_active_claim_age_hours: float,
) -> dict[str, Any]:
    latest_by_task: dict[str, Mapping[str, Any]] = {}
    claim_started: dict[str, str] = {}
    for event in events:
        task_id = _string(event.get("task_id"))
        if not task_id:
            continue
        event_type = _string(event.get("type"))
        status = _string(event.get("status"))
        if event_type == "claim" and status == "active":
            latest_by_task[task_id] = event
            claim_started[task_id] = _string(event.get("ts_utc"))
        elif event_type in TERMINAL_TYPES or status in TERMINAL_STATUSES:
            latest_by_task[task_id] = event
        elif task_id in latest_by_task:
            latest_by_task[task_id] = event

    active_claims: list[dict[str, str]] = []
    stale_open_claims: list[dict[str, str]] = []
    for task_id, event in latest_by_task.items():
        if _string(event.get("type")) == "claim" and _string(event.get("status")) == "active":
            item = {
                "task_id": task_id,
                "agent": _string(event.get("agent")),
                "started_at_utc": claim_started.get(task_id, ""),
            }
            if _is_stale_claim(
                item["started_at_utc"],
                now_utc=now_utc,
                max_age_hours=max_active_claim_age_hours,
            ):
                stale_open_claims.append(item)
            else:
                active_claims.append(item)
    active_claims.sort(key=lambda item: (item["agent"], item["task_id"]))
    stale_open_claims.sort(key=lambda item: (item["agent"], item["task_id"]))

    return {
        "max_active_claim_age_hours": max_active_claim_age_hours,
        "active_claim_count": len(active_claims),
        "active_claims": active_claims,
        "stale_open_claim_count": len(stale_open_claims),
        "stale_open_claims": stale_open_claims[:20],
        "stale_open_claims_truncated": len(stale_open_claims) > 20,
        "done_event_count": sum(1 for event in events if _string(event.get("type")) == "done"),
        "test_pass_count": sum(
            1
            for event in events
            if _string(event.get("type")) == "test" and _string(event.get("status")) == "pass"
        ),
        "finding_count": sum(1 for event in events if _string(event.get("type")) == "finding"),
        "queue_write_performed_by_report": False,
    }


def _agent_activity_summary(
    events: Sequence[Mapping[str, Any]],
    *,
    expected_agents: Sequence[str],
    now_utc: datetime,
    stalled_after_minutes: float = DEFAULT_STALLED_AFTER_MINUTES,
) -> dict[str, Any]:
    latest: dict[str, dict[str, Any]] = {}
    for event in events:
        agent = _string(event.get("agent"))
        if not agent:
            continue
        entry = latest.setdefault(
            agent,
            {
                "last_ts_utc": "",
                "last_type": "",
                "last_status": "",
                "last_substantive_ts_utc": "",
                "last_substantive_type": "",
                "last_substantive_status": "",
            },
        )
        entry["last_ts_utc"] = _string(event.get("ts_utc"))
        entry["last_type"] = _string(event.get("type"))
        entry["last_status"] = _string(event.get("status"))
        if _string(event.get("type")) not in SUBSTANTIVE_EXCLUDED_TYPES:
            entry["last_substantive_ts_utc"] = _string(event.get("ts_utc"))
            entry["last_substantive_type"] = _string(event.get("type"))
            entry["last_substantive_status"] = _string(event.get("status"))
    for entry in latest.values():
        gap = _gap_minutes(entry["last_substantive_ts_utc"], now_utc=now_utc)
        entry["substantive_gap_minutes"] = gap
        entry["stalled"] = gap is None or gap > stalled_after_minutes
    expected = tuple(dict.fromkeys(expected_agents))
    return {
        "seen_agents": sorted(latest),
        "seen_count": len(latest),
        "expected_agents": list(expected),
        "missing_expected_agents": [agent for agent in expected if agent not in latest],
        "stalled_after_minutes": stalled_after_minutes,
        "stalled_expected_agents": [
            agent
            for agent in expected
            if agent in latest and latest[agent]["stalled"] is True
        ],
        "latest_by_agent": {agent: latest[agent] for agent in sorted(latest)},
    }


def _gap_minutes(ts_utc: str, *, now_utc: datetime) -> float | None:
    """Minutes from ``ts_utc`` to ``now_utc`` (clamped at 0); None when unknown."""
    if not ts_utc:
        return None
    try:
        ts = _parse_utc(ts_utc)
    except ValueError:
        return None
    return round(max(0.0, (now_utc - ts).total_seconds() / 60.0), 1)


def _stale_base_summary(
    events: Sequence[Mapping[str, Any]],
    *,
    expected_base_sha: str | None,
    sprint_task_id: str,
) -> dict[str, Any]:
    expected = _string(expected_base_sha).lower()
    warnings: list[dict[str, str]] = []
    if not expected:
        return {
            "expected_base_sha_present": False,
            "warning_count": 0,
            "warnings": [],
        }
    scope_ids = _sprint_scope_ids(sprint_task_id)
    for event in events:
        if not _event_in_sprint_scope(event, scope_ids):
            continue
        for field, value in _base_sha_candidates(_mapping(event.get("payload"))):
            sha = value.lower()
            if sha != expected:
                warnings.append(_stale_warning(event, field=field, sha=sha))
        message = _string(event.get("message"))
        if "base" in message.lower() or "stale" in message.lower():
            for sha in SHA40_RE.findall(message):
                if sha.lower() != expected:
                    warnings.append(_stale_warning(event, field="message", sha=sha))
    return {
        "expected_base_sha_present": True,
        "expected_base_sha_prefix": expected[:12],
        "warning_count": len(warnings),
        "warnings": warnings[:20],
        "warnings_truncated": len(warnings) > 20,
    }


def _sprint_scope_ids(sprint_task_id: str) -> set[str]:
    scope = {_string(sprint_task_id)}
    if sprint_task_id.endswith("-v2"):
        scope.add(sprint_task_id[: -len("-v2")])
    return {item for item in scope if item}


def _event_in_sprint_scope(event: Mapping[str, Any], scope_ids: set[str]) -> bool:
    if _string(event.get("task_id")) in scope_ids:
        return True
    payload = _mapping(event.get("payload"))
    for key in ("sprint_id", "sprint_task_id", "source_sprint_id"):
        if _string(payload.get(key)) in scope_ids:
            return True
    return False


def _base_sha_candidates(payload: Mapping[str, Any]) -> Iterable[tuple[str, str]]:
    for key, value in payload.items():
        key_text = _string(key)
        if isinstance(value, Mapping):
            yield from (
                (f"{key_text}.{field}", sha)
                for field, sha in _base_sha_candidates(value)
            )
        elif "base" in key_text.lower() and isinstance(value, str):
            for sha in SHA40_RE.findall(value):
                yield key_text, sha


def _stale_warning(event: Mapping[str, Any], *, field: str, sha: str) -> dict[str, str]:
    return {
        "agent": _string(event.get("agent")),
        "task_id": _string(event.get("task_id")),
        "field": field,
        "sha_prefix": sha[:12],
    }


def _authority_boundary() -> dict[str, bool]:
    return {
        "read_only_report": True,
        "bridge_append_allowed": False,
        "queue_write_allowed": False,
        "scheduler_enqueue_allowed": False,
        "scheduler_tick_allowed": False,
        "runtime_activation_allowed": False,
        "merge_allowed": False,
        "network_required": False,
        "payload_export_allowed": False,
    }


def _events_path(path: Path | None, *, bridge_root: Path | None = None) -> Path:
    if path is not None:
        return path
    return resolve_bridge_root(bridge_root) / "shared" / "events.jsonl"


def _events_digest(events: Sequence[Mapping[str, Any]]) -> str:
    payload = json.dumps(events, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_utc(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_stale_claim(
    started_at_utc: str,
    *,
    now_utc: datetime,
    max_age_hours: float,
) -> bool:
    if max_age_hours < 0:
        return False
    if not started_at_utc:
        return True
    try:
        started = _parse_utc(started_at_utc)
    except ValueError:
        return True
    age_seconds = (now_utc - started).total_seconds()
    return age_seconds > max_age_hours * 3600


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non_finite_json:{value}")


def _assert_finite(value: Any, *, path: str) -> None:
    if isinstance(value, float) and not (value == value and abs(value) != float("inf")):
        raise ValueError(f"non_finite_json:{path}")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _assert_finite(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_finite(item, path=f"{path}[{index}]")


def _assert_no_redaction_sentinels(value: Any) -> None:
    if isinstance(value, str):
        for marker in REDACTION_SENTINELS:
            if marker.lower() in value.lower():
                raise ValueError("redaction_sentinel_present")
    elif isinstance(value, Mapping):
        for item in value.values():
            _assert_no_redaction_sentinels(item)
    elif isinstance(value, list | tuple):
        for item in value:
            _assert_no_redaction_sentinels(item)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


if __name__ == "__main__":
    raise SystemExit(main())
