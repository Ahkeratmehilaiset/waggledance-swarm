# SPDX-License-Identifier: BUSL-1.1
"""Dormant supplied-page rebuild coordinator; never a reader, writer or gate.

Coverage and identity are caller declarations, not authentication or independent
completeness measurements. The existing core owns envelope validation/folding.
No current snapshot leaves this coordinator before an accepted page reaches one
frozen EOF. Refused pages preserve the last accepted reducer/cursor and previous
good caller state. A caller may supply a corrected page at that same cursor;
this never skips a rejected row or resets a rotated source.

Pure API: begin(...), accept_page(state, {envelopes, source, frozen_eof}),
receipt(state). source is the core's six-field declared page interval. All time
is the explicit frozen as_of_utc. No CLI, file access, subprocess, provider,
bridge emit, Monitor, wall clock or runtime wiring is included.
"""
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from tools import wd_bridge_mind as core

MAX_PAGE_EVENTS = 100000
MAX_PAGE_BYTES = 64 * 1024 * 1024
MAX_CURSOR = 2**63 - 1
_SAFE_CORE_REASONS = core.BLOCKED_REASONS | frozenset({
    'canonical_json_invalid', 'explicit_utc_time_required', 'as_of_precedes_event',
    'projection_policy_invalid', 'resumable_state_required',
})


class RebuildError(ValueError):
    """Fixed diagnostic codes only; never rejected content."""


@dataclass
class RebuildState:
    core_state: core.MindState
    frozen_eof: int
    as_of_utc: str
    previous_good: core.MindState | None
    max_page_events: int
    max_page_bytes: int
    accepted_pages: int = 0
    blocked_reason: str | None = None


def _cursor(value: Any) -> bool:
    return type(value) is int and 0 <= value <= MAX_CURSOR


def begin(*, log_identity: str, generation: str | None, frozen_eof: int, as_of_utc: str,
          previous_good: core.MindState | None = None,
          max_page_events: int = MAX_PAGE_EVENTS, max_page_bytes: int = MAX_PAGE_BYTES) -> RebuildState:
    if (not _cursor(frozen_eof) or type(max_page_events) is not int or not 1 <= max_page_events <= MAX_PAGE_EVENTS
            or type(max_page_bytes) is not int or not 1 <= max_page_bytes <= MAX_PAGE_BYTES
            or log_identity == 'UNKNOWN' or generation == 'UNKNOWN'
            or (previous_good is not None and not isinstance(previous_good, core.MindState))):
        raise RebuildError('rebuild_policy_invalid')
    source = dict(log_identity=log_identity, generation=generation, cursor_start=0, cursor_end=0,
                  event_count=0, coverage='complete')
    try:
        state = core.full_rebuild([], source=source, as_of_utc=as_of_utc)
        if previous_good is not None:
            core.state_bytes(previous_good)  # Guard before retaining, never publish its arbitrary internals.
    except core.ProjectionError:
        raise RebuildError('rebuild_policy_invalid') from None
    return RebuildState(state, frozen_eof, as_of_utc, deepcopy(previous_good), max_page_events, max_page_bytes)


def accept_page(state: RebuildState, page: Any) -> RebuildState:
    if not isinstance(state, RebuildState):
        raise RebuildError('rebuild_state_required')
    result = deepcopy(state)  # Refusal or a successful fold never mutates the caller.
    result.blocked_reason = None
    try:
        if type(page) is not dict or set(page) != {'envelopes', 'source', 'frozen_eof'}:
            raise RebuildError('page_schema_invalid')
        rows, source = page['envelopes'], page['source']
        if type(rows) is not list or type(source) is not dict:
            raise RebuildError('page_schema_invalid')
        if len(rows) > result.max_page_events:
            raise RebuildError('page_event_bound_exceeded')
        if len(core.canonical_bytes(page)) > result.max_page_bytes:
            raise RebuildError('page_byte_bound_exceeded')
        if not _cursor(page['frozen_eof']) or page['frozen_eof'] != result.frozen_eof:
            raise RebuildError('frozen_eof_changed')
        start, end = source.get('cursor_start'), source.get('cursor_end')
        if not _cursor(start) or not _cursor(end) or end > result.frozen_eof:
            raise RebuildError('page_cursor_out_of_bounds')
        if not rows and end != result.frozen_eof:
            raise RebuildError('empty_page_before_eof')
        if result.accepted_pages and result.core_state.source['cursor_end'] == result.frozen_eof and rows:
            raise RebuildError('rebuild_already_complete')
        # The core validates identity/generation, contiguous start, coverage/count,
        # original complete-event refs and every decoded row. Invalid last rows do
        # not leave a partially accepted page or advanced cursor behind.
        result.core_state = core.apply_incremental(result.core_state, rows, source=source, as_of_utc=result.as_of_utc)
        result.accepted_pages += 1
    except RebuildError as exc:
        result.blocked_reason = str(exc)
    except core.ProjectionError as exc:
        result.blocked_reason = str(exc) if str(exc) in _SAFE_CORE_REASONS else 'page_refused'
    return result


def receipt(state: RebuildState) -> dict:
    if not isinstance(state, RebuildState):
        raise RebuildError('rebuild_state_required')
    cursor = state.core_state.source['cursor_end']
    complete = bool(state.accepted_pages and cursor == state.frozen_eof and state.blocked_reason is None)
    value = dict(schema='wd.bridge-mind-rebuild-receipt.v1', authority='none', complete=complete, current=complete,
                 snapshot=core.snapshot(state.core_state, as_of_utc=state.as_of_utc) if complete else None,
                 retained_previous_good=state.previous_good is not None,
                 source=dict(log_identity=state.core_state.source['log_identity'], generation=state.core_state.source['generation'],
                             frozen_eof=state.frozen_eof, accepted_cursor=cursor,
                             accepted_events=state.core_state.source['event_count'], as_of_utc=state.as_of_utc,
                             coverage='declared_complete_frozen_prefix' if complete else 'incomplete_or_blocked'),
                 blocked=None if state.blocked_reason is None else
                     dict(active=True, offset=cursor, reason=state.blocked_reason))
    core.canonical_bytes(value)
    return value
