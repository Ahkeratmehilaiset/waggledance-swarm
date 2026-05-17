"""Pure PDAM/Logbook close-decision core.

This module intentionally has no HTTP, credential, SQLite, FAISS, or apply
logic. Production adapters must feed it already-synced entries, MES states, and
comments, then separately gate any writes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Mapping, Sequence

OK_LIKE_STATES = {"PRODUCTION", "IDLE", "NON_SCHEDULED"}
LIMITED_STATES = {"DOWNTIME", "DOWN", "PLANNED_DOWNTIME", "ENGINEERING"}
OPEN_STATUSES = {"EQ_STOP", "WIP", "ASSIGNED", "NEXT_PM"}


@dataclass(frozen=True)
class LogbookEntry:
    entry_id: int
    local_id: int
    log_code: str
    device: str
    status: str
    created_at: datetime
    issue: str = ""
    action: str = ""


@dataclass(frozen=True)
class ToolState:
    tool_id: str
    state: str
    substate: str = ""
    comment: str = ""
    updated_by: str = ""


@dataclass(frozen=True)
class MesComment:
    tool_id: str
    when: datetime
    by_user: str
    comment: str
    stage: str = ""


@dataclass(frozen=True)
class IncidentWindow:
    start: datetime
    end: datetime | None
    next_entry_id: int | None = None


@dataclass(frozen=True)
class PlannedCloseAction:
    entry_id: int
    device: str
    kind: str
    current_status: str
    target_status: str
    action_text: str
    duplicate_of: int | None = None


def build_tool_graph(
    parents: Sequence[str],
    all_tool_ids: Sequence[str],
    manual_hints: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, list[str]]:
    """Map parent tools to subtools using prefix discovery plus hints."""

    hints = manual_hints or {}
    all_ids = sorted({tool for tool in all_tool_ids if tool})
    graph: dict[str, set[str]] = {parent: set(hints.get(parent, ())) for parent in parents}
    for parent in parents:
        prefix = f"{parent.upper()}_"
        for tool_id in all_ids:
            if tool_id.upper().startswith(prefix):
                graph.setdefault(parent, set()).add(tool_id)
    return {parent: sorted(children) for parent, children in graph.items()}


def plan_close_actions(
    *,
    entries: Sequence[LogbookEntry],
    repair_timeline: Sequence[LogbookEntry],
    tool_states: Mapping[str, ToolState],
    comments: Sequence[MesComment],
    subtools: Mapping[str, Sequence[str]],
    duplicate_window_hours: int = 24,
    evidence_limit: int = 4,
    max_open_window_days: int = 7,
    now: datetime | None = None,
) -> list[PlannedCloseAction]:
    """Build deterministic close/WIP/REVIEW recommendations.

    The function is deliberately conservative: if a subtool is still limited
    and an older selected entry is not clearly a same-incident duplicate of the
    kept WIP, it returns REVIEW instead of inventing a safe closure.
    """

    clock = now or datetime.now()
    by_device: dict[str, list[LogbookEntry]] = {}
    for entry in entries:
        if entry.status.upper() in OPEN_STATUSES:
            by_device.setdefault(entry.device, []).append(entry)

    timeline_by_device: dict[str, list[LogbookEntry]] = {}
    for entry in repair_timeline:
        timeline_by_device.setdefault(entry.device, []).append(entry)

    actions: list[PlannedCloseAction] = []
    for device, device_entries in sorted(by_device.items()):
        device_entries = sorted(device_entries, key=lambda item: item.local_id, reverse=True)
        all_open = device_entries
        parent_state = tool_states.get(device)
        limited = _limited_subtools(device, tool_states, subtools)
        keep_open = _state_name(parent_state) not in OK_LIKE_STATES or bool(limited)
        keeper = _choose_keeper(all_open) if keep_open else None
        timeline = sorted(
            timeline_by_device.get(device, device_entries),
            key=lambda item: (item.created_at, item.local_id),
        )

        for entry in device_entries:
            window = _incident_window(entry, timeline, clock, max_open_window_days)
            evidence = _select_evidence(
                comments=comments,
                tool_ids=[device, *subtools.get(device, ())],
                window=window,
                limit=evidence_limit,
            )
            if keep_open and keeper and entry.entry_id == keeper.entry_id:
                actions.append(_keep_wip(entry, parent_state, limited, window, evidence))
                continue
            if keep_open and keeper:
                if _same_incident(entry, keeper, duplicate_window_hours):
                    actions.append(_close_duplicate(entry, keeper, parent_state, limited, window, evidence))
                else:
                    actions.append(_review(entry, parent_state, limited, window, evidence))
                continue
            actions.append(_close_ok(entry, parent_state, limited, window, evidence))

    return actions


def _clean(text: str, max_len: int | None = None) -> str:
    out = re.sub(r"\s+", " ", (text or "").replace("\r", " ").replace("\n", " ")).strip()
    if max_len and len(out) > max_len:
        return out[: max_len - 3].rstrip() + "..."
    return out


def _state_name(state: ToolState | None) -> str:
    return (state.state if state else "").upper()


def _state_text(state: ToolState | None) -> str:
    if not state:
        return "UNKNOWN"
    return f"{state.state or '-'}{('/' + state.substate) if state.substate else ''}"


def _is_trivial_comment(text: str) -> bool:
    value = _clean(text).lower()
    if not value or value in {".", "..", "...", "k", "ok", "x", "-", "--", "?", ",,"}:
        return True
    if "automatic tool state transition" in value:
        return True
    if re.match(r"activity [\w.]+\s+(started|completed|performed)\.?$", value, re.I):
        return True
    if re.match(r"step \d+ for activity", value, re.I):
        return True
    return False


def _limited_subtools(
    device: str,
    tool_states: Mapping[str, ToolState],
    subtools: Mapping[str, Sequence[str]],
) -> list[ToolState]:
    limited: list[ToolState] = []
    for subtool in subtools.get(device, ()):
        state = tool_states.get(subtool)
        if _state_name(state) in LIMITED_STATES:
            limited.append(state)
    return limited


def _choose_keeper(entries: Sequence[LogbookEntry]) -> LogbookEntry:
    wips = [entry for entry in entries if entry.status.upper() == "WIP"]
    candidates = wips or list(entries)
    return sorted(candidates, key=lambda item: item.local_id, reverse=True)[0]


def _same_incident(entry: LogbookEntry, keeper: LogbookEntry, duplicate_window_hours: int) -> bool:
    return abs(keeper.created_at - entry.created_at) <= timedelta(hours=duplicate_window_hours)


def _incident_window(
    entry: LogbookEntry,
    timeline: Sequence[LogbookEntry],
    now: datetime,
    max_open_window_days: int,
) -> IncidentWindow:
    later = [
        other
        for other in timeline
        if other.entry_id != entry.entry_id
        and (other.created_at, other.local_id) > (entry.created_at, entry.local_id)
    ]
    if later:
        next_entry = sorted(later, key=lambda item: (item.created_at, item.local_id))[0]
        return IncidentWindow(entry.created_at, next_entry.created_at, next_entry.entry_id)
    return IncidentWindow(entry.created_at, min(now, entry.created_at + timedelta(days=max_open_window_days)), None)


def _select_evidence(
    *,
    comments: Sequence[MesComment],
    tool_ids: Sequence[str],
    window: IncidentWindow,
    limit: int,
) -> list[MesComment]:
    ids = set(tool_ids)
    candidates = [
        comment
        for comment in comments
        if comment.tool_id in ids
        and comment.when >= window.start
        and (window.end is None or comment.when < window.end)
        and not _is_trivial_comment(comment.comment)
    ]
    return sorted(candidates, key=_evidence_sort_key)[:limit]


def _evidence_sort_key(comment: MesComment) -> tuple[int, int, datetime]:
    text = comment.comment.lower()
    repairish = any(word in text for word in ("repair", "recovered", "completed", "released", "vaihd", "korja"))
    human = bool(comment.by_user and comment.by_user.strip() and comment.by_user.upper() not in {"SYSTEM", "AUTO"})
    return (0 if human else 1, 0 if repairish else 1, comment.when)


def _format_comment(comment: MesComment, base_tool: str) -> str:
    tool = f" [{comment.tool_id}]" if comment.tool_id != base_tool else ""
    return f"{comment.when.day}.{comment.when.month}.{comment.when.year} {comment.when:%H:%M} {comment.by_user or '?'}{tool}: {_clean(comment.comment, 240)}"


def _context_lines(entry: LogbookEntry, window: IncidentWindow, evidence: Sequence[MesComment]) -> list[str]:
    if not evidence:
        return ["No time-windowed PDAM/MES evidence was selected."]
    if window.next_entry_id:
        header = (
            f"PDAM/MES evidence window {window.start:%Y-%m-%d %H:%M} - "
            f"{window.end:%Y-%m-%d %H:%M} before next fault #{window.next_entry_id}:"
        )
    else:
        header = f"PDAM/MES evidence window from {window.start:%Y-%m-%d %H:%M}:"
    return [header, *[_format_comment(comment, entry.device) for comment in evidence]]


def _limited_note(limited: Sequence[ToolState]) -> str:
    if not limited:
        return ""
    chunks = []
    for state in limited:
        comment = f": {_clean(state.comment, 160)}" if state.comment else ""
        chunks.append(f"{state.tool_id} {_state_text(state)}{comment}")
    return "Limited subtool state: " + "; ".join(chunks) + "."


def _action_text(
    entry: LogbookEntry,
    parent_state: ToolState | None,
    limited: Sequence[ToolState],
    window: IncidentWindow,
    evidence: Sequence[MesComment],
    conclusion: str,
) -> str:
    lines = _context_lines(entry, window, evidence)
    note = _limited_note(limited)
    if note:
        lines.append(note)
    lines.append(f"Parent MES state: {_state_text(parent_state)}.")
    lines.append(conclusion)
    return "\n".join(lines)


def _close_ok(
    entry: LogbookEntry,
    parent_state: ToolState | None,
    limited: Sequence[ToolState],
    window: IncidentWindow,
    evidence: Sequence[MesComment],
) -> PlannedCloseAction:
    return PlannedCloseAction(
        entry_id=entry.entry_id,
        device=entry.device,
        kind="CLOSE_OK",
        current_status=entry.status.upper(),
        target_status="OK",
        action_text=_action_text(entry, parent_state, limited, window, evidence, "Deterministic policy recommends CLOSE_OK."),
    )


def _close_duplicate(
    entry: LogbookEntry,
    keeper: LogbookEntry,
    parent_state: ToolState | None,
    limited: Sequence[ToolState],
    window: IncidentWindow,
    evidence: Sequence[MesComment],
) -> PlannedCloseAction:
    return PlannedCloseAction(
        entry_id=entry.entry_id,
        device=entry.device,
        kind="CLOSE_DUPLICATE",
        current_status=entry.status.upper(),
        target_status="OK",
        duplicate_of=keeper.entry_id,
        action_text=_action_text(
            entry,
            parent_state,
            limited,
            window,
            evidence,
            f"Same-incident duplicate of WIP #{keeper.entry_id}; deterministic policy recommends closing only this duplicate.",
        ),
    )


def _keep_wip(
    entry: LogbookEntry,
    parent_state: ToolState | None,
    limited: Sequence[ToolState],
    window: IncidentWindow,
    evidence: Sequence[MesComment],
) -> PlannedCloseAction:
    return PlannedCloseAction(
        entry_id=entry.entry_id,
        device=entry.device,
        kind="KEEP_WIP",
        current_status=entry.status.upper(),
        target_status="WIP",
        action_text=_action_text(entry, parent_state, limited, window, evidence, "Active/limited evidence remains; keep WIP open."),
    )


def _review(
    entry: LogbookEntry,
    parent_state: ToolState | None,
    limited: Sequence[ToolState],
    window: IncidentWindow,
    evidence: Sequence[MesComment],
) -> PlannedCloseAction:
    return PlannedCloseAction(
        entry_id=entry.entry_id,
        device=entry.device,
        kind="REVIEW",
        current_status=entry.status.upper(),
        target_status="REVIEW",
        action_text=_action_text(entry, parent_state, limited, window, evidence, "Ambiguous non-duplicate while a subtool is limited; require review."),
    )
