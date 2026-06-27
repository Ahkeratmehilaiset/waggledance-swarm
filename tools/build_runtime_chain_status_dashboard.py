# SPDX-License-Identifier: BUSL-1.1
"""Build a read-only runtime chain status dashboard from bridge events.

The dashboard is intentionally observational: it reads bridge JSONL, summarizes
runtime-chain gate receipts and inferred next gates, and never writes bridge
events, work-queue claims, GitHub state, or runtime activation state.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.work_queue import resolve_bridge_root  # noqa: E402


REPORT_VERSION = "wd.runtime_chain_status_dashboard.v0"
DEFAULT_MATCH = "hex-subdivision"
DEFAULT_TAIL = 50000
DEFAULT_REQUIRED_BUILD_AGENTS = ("codex-lead-1", "codex-tools-1")
DEFAULT_MIN_RCO_PASSES = 1
PR_RE = re.compile(r"\bPR\s+#(?P<pr>\d+)\b", re.IGNORECASE)
PULL_PATH_RE = re.compile(r"\bpull/(?P<pr>\d+)\b", re.IGNORECASE)
SHA40_RE = re.compile(r"\b[0-9a-fA-F]{40}\b")
REDACTION_SENTINELS = ("PRIVATE" + "_MARKER", "_DO" + "_NOT" + "_LEAK")
TERMINAL_STATUSES = {"done", "merged", "merged_observed", "post_merge_main_ci_green"}
BLOCKING_STATUS_FRAGMENTS = ("changes_requested", "block", "failed", "error")
CI_GREEN_FRAGMENTS = ("ci 6/6 green", "ci green", "github ci 6/6", "6/6 success")
POST_MERGE_GREEN_FRAGMENTS = (
    "post-merge main ci green",
    "post_merge_main_ci_green",
    "post_merge_ci_green",
)
MERGE_FRAGMENTS = (" merged by ", " merged at exact head", "merged_post_merge")
NO_LIVE_RUNTIME_FRAGMENTS = (
    "no live runtime",
    "dormant",
    "observability-only",
    "no topology mutation",
    "no runtime authority",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render a read-only runtime-chain bridge status dashboard.",
    )
    parser.add_argument(
        "--events",
        type=Path,
        default=None,
        help="Path to bridge events JSONL (default: <bridge-root>/shared/events.jsonl).",
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
        "--match",
        action="append",
        default=None,
        help=(
            "Case-insensitive task/message substring to include. Repeatable; "
            f"default: {DEFAULT_MATCH!r}."
        ),
    )
    parser.add_argument(
        "--required-build-agent",
        action="append",
        dest="required_build_agents",
        default=None,
        help="Build-consensus agent required for an unmerged row. Repeatable.",
    )
    parser.add_argument(
        "--min-rco-passes",
        type=int,
        default=DEFAULT_MIN_RCO_PASSES,
        help="Minimum RCO pass count before the inferred merge gate is satisfied.",
    )
    parser.add_argument(
        "--tail",
        type=int,
        default=DEFAULT_TAIL,
        help="Maximum event lines to read from the end of the JSONL file; <=0 reads all.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override for deterministic output.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_runtime_chain_status_dashboard(
        events_path=args.events,
        bridge_root=args.bridge_root,
        match_terms=tuple(args.match or (DEFAULT_MATCH,)),
        required_build_agents=tuple(
            args.required_build_agents or DEFAULT_REQUIRED_BUILD_AGENTS
        ),
        min_rco_passes=args.min_rco_passes,
        tail=args.tail,
        now_utc=_parse_utc(args.now) if args.now else None,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report), end="")
    return 0 if report["ok"] else 1


def build_runtime_chain_status_dashboard(
    *,
    events_path: Path | None = None,
    bridge_root: Path | None = None,
    events: Sequence[Mapping[str, Any]] | None = None,
    match_terms: Sequence[str] = (DEFAULT_MATCH,),
    required_build_agents: Sequence[str] = DEFAULT_REQUIRED_BUILD_AGENTS,
    min_rco_passes: int = DEFAULT_MIN_RCO_PASSES,
    tail: int = DEFAULT_TAIL,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    blockers: list[str] = []
    loaded_events: list[Mapping[str, Any]] = []

    try:
        loaded_events = (
            list(events)
            if events is not None
            else read_bridge_events(_events_path(events_path, bridge_root), tail=tail)
        )
        _assert_no_redaction_sentinels(loaded_events)
    except ValueError as exc:
        blockers.append(f"events_input_refused:{exc}")

    normalized_terms = tuple(
        term.lower() for term in match_terms if isinstance(term, str) and term.strip()
    )
    selected = _select_matching_events(loaded_events, normalized_terms)
    stages = _summarize_stages(
        selected,
        required_build_agents=tuple(dict.fromkeys(required_build_agents)),
        min_rco_passes=max(0, min_rco_passes),
    )

    return {
        "report_version": REPORT_VERSION,
        "generated_at_utc": _format_utc(generated_at),
        "ok": not blockers,
        "blockers": blockers,
        "filters": {
            "match_terms": list(normalized_terms),
            "required_build_agents": list(dict.fromkeys(required_build_agents)),
            "min_rco_passes": max(0, min_rco_passes),
        },
        "source": {
            "event_count": len(loaded_events),
            "matched_event_count": len(selected),
            "source_redacted": True,
            "messages_redacted": True,
            "payloads_redacted": True,
            "path_free": True,
        },
        "stages": stages,
        "summary": _summary(stages),
        "authority_boundary": _authority_boundary(),
    }


def read_bridge_events(path: Path, *, tail: int = DEFAULT_TAIL) -> list[Mapping[str, Any]]:
    if not path.exists():
        raise ValueError("events_file_missing")
    lines = path.read_text(encoding="utf-8").splitlines()
    if tail > 0:
        lines = lines[-tail:]
    events: list[Mapping[str, Any]] = []
    for line_no, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line, parse_constant=_reject_json_constant)
        except json.JSONDecodeError as exc:
            raise ValueError(f"events_json_error:line_{line_no}") from exc
        if not isinstance(event, dict):
            raise ValueError(f"event_not_object:line_{line_no}")
        events.append(event)
    return events


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Runtime Chain Status",
        "",
        f"- ok: {_bool_text(bool(report.get('ok')))}",
        f"- matched_events: {report.get('source', {}).get('matched_event_count', 0)}",
        f"- active_blockers: {report.get('summary', {}).get('active_blocker_count', 0)}",
        "",
        "| PR | Task | State | Next gate | Builds | RCO | CI | Safety |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for stage in report.get("stages", []):
        if not isinstance(stage, Mapping):
            continue
        lines.append(
            "| {pr} | {task} | {state} | {gate} | {builds} | {rco} | {ci} | {safety} |".format(
                pr=stage.get("pr_number") or "",
                task=_shorten(str(stage.get("task_id") or "")),
                state=stage.get("state") or "",
                gate=stage.get("next_gate") or "",
                builds=",".join(stage.get("build_consensus_agents", [])),
                rco=",".join(stage.get("rco_pass_agents", [])),
                ci=stage.get("ci_state") or "",
                safety=stage.get("safety_state") or "",
            )
        )
    lines.append("")
    return "\n".join(lines)


def _summarize_stages(
    events: Sequence[Mapping[str, Any]],
    *,
    required_build_agents: Sequence[str],
    min_rco_passes: int,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for event in events:
        task_id = _string(event.get("task_id")) or "unknown"
        grouped[task_id].append(event)

    rows = [
        _summarize_stage(
            task_id,
            task_events,
            required_build_agents=required_build_agents,
            min_rco_passes=min_rco_passes,
        )
        for task_id, task_events in grouped.items()
    ]
    _apply_pr_terminal_evidence(rows)
    return sorted(rows, key=lambda row: (row["latest_ts_utc"], row["task_id"]))


def _select_matching_events(
    events: Sequence[Mapping[str, Any]], terms: Sequence[str]
) -> list[Mapping[str, Any]]:
    selected = [event for event in events if _event_matches(event, terms)]
    selected_prs = {
        pr_number
        for pr_number in (_extract_pr_number((event,)) for event in selected)
        if pr_number is not None
    }
    if not selected_prs:
        return selected
    expanded: list[Mapping[str, Any]] = []
    seen_event_ids: set[int] = set()
    for event in events:
        if id(event) in seen_event_ids:
            continue
        pr_number = _extract_pr_number((event,))
        if event in selected or pr_number in selected_prs:
            expanded.append(event)
            seen_event_ids.add(id(event))
    return expanded


def _apply_pr_terminal_evidence(rows: list[dict[str, Any]]) -> None:
    rows_by_pr: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        pr_number = row.get("pr_number")
        if isinstance(pr_number, int):
            rows_by_pr[pr_number].append(row)

    for pr_rows in rows_by_pr.values():
        green_rows = [
            row for row in pr_rows if row.get("state") == "post_merge_main_ci_green"
        ]
        if not green_rows:
            continue
        merge_commit_prefix = next(
            (
                str(row.get("merge_commit_prefix"))
                for row in green_rows
                if row.get("merge_commit_prefix")
            ),
            "",
        )
        for row in pr_rows:
            if row.get("state") == "merged_post_merge_ci_pending":
                row["state"] = "post_merge_main_ci_green"
                row["next_gate"] = "complete"
                row["ci_state"] = "green"
                row["blockers"] = []
                if merge_commit_prefix and not row.get("merge_commit_prefix"):
                    row["merge_commit_prefix"] = merge_commit_prefix


def _summarize_stage(
    task_id: str,
    events: Sequence[Mapping[str, Any]],
    *,
    required_build_agents: Sequence[str],
    min_rco_passes: int,
) -> dict[str, Any]:
    latest = max(events, key=lambda event: _string(event.get("ts_utc")))
    pr_number = _extract_pr_number(events)
    head_sha = _extract_latest_sha(events)
    evidence_events = _latest_head_segment(events, head_sha)
    build_agents = sorted(
        {
            _string(event.get("agent"))
            for event in evidence_events
            if _string(event.get("status")).lower() == "build_consensus_pass"
        }
        - {""}
    )
    rco_agents = sorted(
        {
            _string(event.get("agent"))
            for event in evidence_events
            if _string(event.get("status")).lower() == "rco_pass"
        }
        - {""}
    )
    missing_build_agents = [
        agent for agent in required_build_agents if agent not in build_agents
    ]
    merge_commit = _extract_merge_commit(events)
    ci_state = _ci_state(evidence_events)
    safety_state = _safety_state(evidence_events)
    blockers = _stage_blockers(
        evidence_events, missing_build_agents, rco_agents, min_rco_passes
    )
    state = _state(evidence_events, blockers=blockers)
    next_gate = _next_gate(
        state=state,
        blockers=blockers,
        missing_build_agents=missing_build_agents,
        rco_pass_count=len(rco_agents),
        min_rco_passes=min_rco_passes,
        ci_state=ci_state,
    )

    return {
        "task_id": task_id,
        "pr_number": pr_number,
        "head_sha_prefix": head_sha[:12] if head_sha else "",
        "merge_commit_prefix": merge_commit[:12] if merge_commit else "",
        "latest_ts_utc": _string(latest.get("ts_utc")),
        "latest_agent": _string(latest.get("agent")),
        "latest_type": _string(latest.get("type")),
        "latest_status": _string(latest.get("status")),
        "state": state,
        "next_gate": next_gate,
        "build_consensus_agents": build_agents,
        "missing_build_consensus_agents": missing_build_agents,
        "rco_pass_agents": rco_agents,
        "rco_pass_count": len(rco_agents),
        "ci_state": ci_state,
        "safety_state": safety_state,
        "blockers": blockers,
        "event_count": len(events),
    }


def _stage_blockers(
    events: Sequence[Mapping[str, Any]],
    missing_build_agents: Sequence[str],
    rco_agents: Sequence[str],
    min_rco_passes: int,
) -> list[str]:
    if _post_merge_green(events):
        return []
    blockers: list[str] = []
    for event in events:
        status = _string(event.get("status")).lower()
        if any(fragment in status for fragment in BLOCKING_STATUS_FRAGMENTS):
            if "pass" not in status and "green" not in status:
                blockers.append(f"blocking_status:{status}")
    if not _merged(events):
        blockers.extend(f"missing_build_consensus:{agent}" for agent in missing_build_agents)
        if len(rco_agents) < min_rco_passes:
            blockers.append(
                f"missing_rco_pass:{min_rco_passes - len(rco_agents)}"
            )
    return sorted(dict.fromkeys(blockers))


def _state(events: Sequence[Mapping[str, Any]], *, blockers: Sequence[str]) -> str:
    if _post_merge_green(events):
        return "post_merge_main_ci_green"
    if _merged(events):
        return "merged_post_merge_ci_pending"
    if blockers:
        return "blocked_or_incomplete"
    return "gate_complete_open"


def _next_gate(
    *,
    state: str,
    blockers: Sequence[str],
    missing_build_agents: Sequence[str],
    rco_pass_count: int,
    min_rco_passes: int,
    ci_state: str,
) -> str:
    if state == "post_merge_main_ci_green":
        return "complete"
    if state == "merged_post_merge_ci_pending":
        return "post_merge_main_ci"
    if any(blocker.startswith("blocking_status:") for blocker in blockers):
        return "resolve_blocker"
    if missing_build_agents:
        return "build_consensus"
    if rco_pass_count < min_rco_passes:
        return "rco_pass"
    if ci_state not in {"green", "unknown"}:
        return "ci_green"
    return "merge"


def _summary(stages: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    active_blockers = [
        blocker
        for stage in stages
        for blocker in stage.get("blockers", [])
        if isinstance(blocker, str)
    ]
    return {
        "stage_count": len(stages),
        "merge_ready_count": sum(
            1 for stage in stages if stage.get("next_gate") == "merge"
        ),
        "post_merge_pending_count": sum(
            1 for stage in stages if stage.get("next_gate") == "post_merge_main_ci"
        ),
        "complete_count": sum(
            1 for stage in stages if stage.get("next_gate") == "complete"
        ),
        "active_blocker_count": len(active_blockers),
        "active_blockers": sorted(dict.fromkeys(active_blockers)),
    }


def _event_matches(event: Mapping[str, Any], terms: Sequence[str]) -> bool:
    if not terms:
        return True
    text = " ".join(
        (
            _string(event.get("task_id")),
            _string(event.get("status")),
            _string(event.get("message")),
        )
    ).lower()
    return any(term in text for term in terms)


def _extract_pr_number(events: Sequence[Mapping[str, Any]]) -> int | None:
    for event in reversed(events):
        payload = _mapping(event.get("payload"))
        for key in ("pr_number", "pr"):
            value = payload.get(key)
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
        for path in event.get("paths") or []:
            if isinstance(path, str):
                match = PULL_PATH_RE.search(path)
                if match:
                    return int(match.group("pr"))
        match = PR_RE.search(_string(event.get("message")))
        if match:
            return int(match.group("pr"))
    return None


def _extract_latest_sha(events: Sequence[Mapping[str, Any]]) -> str:
    for event in reversed(events):
        matches = _event_sha_values(event)
        if matches:
            return matches[-1].lower()
    return ""


def _latest_head_segment(
    events: Sequence[Mapping[str, Any]], head_sha: str
) -> Sequence[Mapping[str, Any]]:
    if not head_sha:
        return events
    segment_start = 0
    for index, event in enumerate(events):
        shas = set(_event_sha_values(event))
        if shas and head_sha not in shas:
            segment_start = index + 1
    return events[segment_start:] or events[-1:]


def _event_sha_values(event: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    payload = _mapping(event.get("payload"))
    for key in ("exact_head", "head_sha", "head", "merge_commit", "base_sha"):
        value = payload.get(key)
        if isinstance(value, str) and SHA40_RE.fullmatch(value):
            values.append(value.lower())
    values.extend(
        match.lower() for match in SHA40_RE.findall(_string(event.get("message")))
    )
    return values


def _extract_merge_commit(events: Sequence[Mapping[str, Any]]) -> str:
    for event in reversed(events):
        text = _text(event)
        if "merge commit" not in text.lower() and "main head" not in text.lower():
            continue
        matches = SHA40_RE.findall(text)
        if matches:
            return matches[-1].lower()
    return ""


def _ci_state(events: Sequence[Mapping[str, Any]]) -> str:
    text = "\n".join(_text(event).lower() for event in events)
    if "ci failed" in text or "conclusion failure" in text:
        return "failed"
    if any(_has_post_merge_green_status(event) for event in events):
        return "green"
    if any(fragment in text for fragment in CI_GREEN_FRAGMENTS):
        return "green"
    if "ci" in text and ("running" in text or "in_progress" in text):
        return "running"
    return "unknown"


def _safety_state(events: Sequence[Mapping[str, Any]]) -> str:
    text = "\n".join(_text(event).lower() for event in events)
    if re.search(r"\blive[_a-z]*runtime[_a-z]*\W{0,24}true\b", text):
        return "review_required"
    if any("changes_requested" in _string(event.get("status")).lower() for event in events):
        return "changes_requested"
    if any(fragment in text for fragment in NO_LIVE_RUNTIME_FRAGMENTS):
        return "no_live_runtime_claim_seen"
    return "unknown"


def _merged(events: Sequence[Mapping[str, Any]]) -> bool:
    for event in events:
        status = _string(event.get("status")).lower()
        if status in TERMINAL_STATUSES and "merged" in status:
            return True
        text = f" {_text(event).lower()} "
        if any(fragment in text for fragment in MERGE_FRAGMENTS):
            return True
    return False


def _post_merge_green(events: Sequence[Mapping[str, Any]]) -> bool:
    return any(_has_post_merge_green_status(event) for event in events)


def _has_post_merge_green_status(event: Mapping[str, Any]) -> bool:
    status = _string(event.get("status")).lower()
    return any(fragment in status for fragment in POST_MERGE_GREEN_FRAGMENTS)


def _events_path(path: Path | None, bridge_root: Path | None) -> Path:
    if path is not None:
        return path
    return resolve_bridge_root(bridge_root) / "shared" / "events.jsonl"


def _authority_boundary() -> dict[str, bool]:
    return {
        "read_only_report": True,
        "bridge_append_allowed": False,
        "queue_write_allowed": False,
        "github_write_allowed": False,
        "runtime_activation_allowed": False,
        "merge_allowed": False,
        "network_required": False,
        "payload_export_allowed": False,
    }


def _parse_utc(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non_finite_json:{value}")


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


def _text(event: Mapping[str, Any]) -> str:
    return " ".join(
        item
        for item in (
            _string(event.get("task_id")),
            _string(event.get("type")),
            _string(event.get("status")),
            _string(event.get("message")),
        )
        if item
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _shorten(value: str, *, limit: int = 48) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "..."


if __name__ == "__main__":
    raise SystemExit(main())
