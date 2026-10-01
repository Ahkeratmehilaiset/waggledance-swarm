#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Task-control records from an S1 dispatch result (F26 S6 source preparation, P2; pure and DORMANT).

``task_controls(dispatch_result, now, *, cancellations=None)`` reads only the S1 ``dispatches()`` result
the caller passes (parsed JSON), the caller's explicit offset-aware ``now`` and an optional explicit
cancellation-coverage statement. It opens no file and reads no clock, environment, queue, provider or
process, and nothing calls it yet. It produces closed ``wd.routing-task-control.v1`` records (the
``controls`` input of tools/wd_routing_associations) by MECHANICAL correlation: a control is a statement
of which request S1 found current for a task, never provenance, authentication, acceptance or permission,
and no output field says identity_verified.

Output ``{"controls", "withheld", "coverage"}``:

* ``controls``: exactly ``{schema, task_id, request_id, request_digest, state, observed_utc}`` per task,
  copied from that task's one live S1 dispatch (``request_id`` = dispatch_id, ``request_digest`` and
  ``observed_utc`` = dispatched_utc verbatim, never recomputed). ``state`` is ``live`` or, only when the
  caller's complete cancellation statement names exactly that request, ``cancelled``.
* ``withheld``: ``{task_id, request_id, reason}`` for every task that had a dispatch but gets no control.
* ``coverage``: ``{schema, complete, cancellation, reason}``.

Fail-closed rules:

* The whole result must be strict built-in JSON (exact dict/list/str/int/float/bool/None, exact str keys,
  finite floats, depth <= 64) with the closed S1 shapes (12-field dispatches of the dispatch authority,
  6-key rejections, duplicate entries); otherwise NOTHING is emitted (``input_malformed``).
* Copies of one dispatch_id must be identical (else every task they name is ``dispatch_conflict``); a task
  with two different live dispatches is ``dispatch_ambiguous``; a dispatch after ``now`` is ``future_dated``.
* A task that S1 both dispatched and held (``dispatch_held_newer_rejection``) is ``held_conflict``; a hold
  whose task cannot be read withholds EVERY task (``held_unattributed``, coverage incomplete); a superseded
  record of the task observed after its live dispatch, at an unknown time, or naming any winner other than
  that live dispatch is ``supersession_inconsistent``: an old revision never becomes a live control (D-F1).
* No cancellation input exists in the Bridge yet. Without an explicit statement ``{schema:
  wd.routing-cancellation-coverage.v1, complete: true, cancelled: [{task_id, request_id, request_digest}]}``
  every task is withheld ``cancellation_coverage_unknown`` and ``coverage.cancellation`` is ``unknown``:
  absence of a cancel record is never read as live authority, and no ``cancelled`` control is invented.
  A cancellation for the task that names another request is ``cancellation_mismatch``.
* A versioned v2 statement ``{schema: wd.routing-cancellation-coverage.v2, complete: true, cancelled: [...],
  unknown_tasks: [task_id, ...]}`` (unique non-empty exact str ids) adds task-level unknown coverage: a task in
  unknown_tasks is withheld ``cancellation_unknown`` before any cancel or live control, even if a known
  cancellation names it. An unknown task with no dispatch here has no effect and proves nothing live. Whole-log
  completeness stays the ``complete`` flag (false withholds every task); an unattributable unknown control
  must be reported upstream as complete false, never dropped. v1 behaviour is unchanged; a mixed or other
  version is unknown coverage. The S-A deriver output (wd.routing-cancellation-derivation.v1) is a distinct
  evidence wrapper: a future adapter must convert it explicitly, nothing here reads it.

Cancellation of the caller (KeyboardInterrupt, SystemExit, GeneratorExit) and unexpected errors propagate;
a ``now`` that is not an offset-aware datetime (or cannot be converted to UTC) is a ValueError.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

DISPATCH_SCHEMA = "wd.routing-dispatch.v1"
DISPATCH_FIELDS = ("schema", "dispatch_id", "task_id", "revision", "input_digest", "scope", "dispatch_key",
                   "worker", "requester", "request_digest", "dispatched_utc", "expected_responders")
RESULT_KEYS = ("dispatches", "rejected", "duplicates_ignored")
REJECTION_KEYS = ("request_id", "reason", "index", "superseded_by", "task_id", "observed_utc")
DISPATCH_AUTHORITY = "codex-lead-1"
CONTROL_SCHEMA = "wd.routing-task-control.v1"
CONTROL_FIELDS = ("schema", "task_id", "request_id", "request_digest", "state", "observed_utc")
CANCELLATION_SCHEMA = "wd.routing-cancellation-coverage.v1"
CANCELLATION_KEYS = ("schema", "complete", "cancelled")
CANCELLATION_SCHEMA_V2 = "wd.routing-cancellation-coverage.v2"
CANCELLATION_KEYS_V2 = ("schema", "complete", "cancelled", "unknown_tasks")
CANCELLED_KEYS = ("task_id", "request_id", "request_digest")
COVERAGE_SCHEMA = "wd.routing-task-control-coverage.v1"
HELD_REASON = "dispatch_held_newer_rejection"
SUPERSEDED_REASON = "superseded_revision"
MAX_DEPTH = 64
_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}", re.ASCII)
_LANE = re.compile(r"[a-z][a-z0-9_-]{1,32}", re.ASCII)
_HEX64 = re.compile(r"[0-9a-f]{64}", re.ASCII)

W_CONFLICT = "dispatch_conflict"
W_AMBIGUOUS = "dispatch_ambiguous"
W_FUTURE = "future_dated"
W_HELD = "held_conflict"
W_HELD_UNATTRIBUTED = "held_unattributed"
W_SUPERSESSION = "supersession_inconsistent"
W_CANCEL_UNKNOWN = "cancellation_coverage_unknown"
W_CANCEL_MISMATCH = "cancellation_mismatch"
W_CANCEL_TASK_UNKNOWN = "cancellation_unknown"
R_INPUT = "input_malformed"


class _Malformed(Exception):
    pass


def _strict(value: Any, depth: int = 0) -> bool:
    """Exact built-in JSON by exact type, checked before any lookup, equality or hash."""
    kind = type(value)
    if value is None or kind is bool or kind is int or kind is str:
        return True
    if kind is float:
        return value == value and value not in (float("inf"), float("-inf"))
    if depth >= MAX_DEPTH:
        return False
    if kind is list:
        return all(_strict(item, depth + 1) for item in value)
    if kind is dict:
        return all(type(key) is str and _strict(item, depth + 1) for key, item in dict.items(value))
    return False


def _text(value: Any) -> bool:
    return type(value) is str and bool(value)


def _match(pattern: re.Pattern, value: Any) -> bool:
    return type(value) is str and pattern.fullmatch(value) is not None


def _instant(value: Any) -> datetime | None:
    if type(value) is not str:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(timezone.utc) if parsed.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None


def _closed(record: Any, keys: tuple) -> bool:
    return type(record) is dict and set(record) == set(keys)


def _dispatch_ok(record: Any) -> bool:
    if not _closed(record, DISPATCH_FIELDS):
        return False
    worker = record["worker"]
    responders = record["expected_responders"]
    return (record["schema"] == DISPATCH_SCHEMA and _match(_ID, record["dispatch_id"]) and _text(record["task_id"])
            and _text(record["revision"]) and _match(_HEX64, record["input_digest"])
            and type(record["scope"]) is list and bool(record["scope"]) and all(_text(s) for s in record["scope"])
            and _match(_HEX64, record["dispatch_key"]) and _match(_LANE, worker)
            and record["requester"] == DISPATCH_AUTHORITY and _match(_HEX64, record["request_digest"])
            and _instant(record["dispatched_utc"]) is not None
            and type(responders) is dict and list(responders) == [worker]
            and _closed(responders[worker], ("agent_uuid", "session_id", "run_id"))
            and all(_text(v) for v in responders[worker].values()))


def _rejection_ok(record: Any) -> bool:
    return (_closed(record, REJECTION_KEYS) and _text(record["reason"])
            and (record["request_id"] is None or _text(record["request_id"]))
            and (record["task_id"] is None or _text(record["task_id"]))
            and (record["superseded_by"] is None or _text(record["superseded_by"]))
            and (record["index"] is None or (type(record["index"]) is int and record["index"] >= 0))
            and (record["observed_utc"] is None or _instant(record["observed_utc"]) is not None))


def _duplicate_ok(record: Any) -> bool:
    return (_closed(record, ("request_id", "copies_ignored")) and _text(record["request_id"])
            and type(record["copies_ignored"]) is int and record["copies_ignored"] > 0)


def _validated(dispatch_result: Any) -> dict:
    if not _strict(dispatch_result) or not _closed(dispatch_result, RESULT_KEYS):
        raise _Malformed
    if not all(type(dispatch_result[key]) is list for key in RESULT_KEYS):
        raise _Malformed
    if not (all(_dispatch_ok(r) for r in dispatch_result["dispatches"])
            and all(_rejection_ok(r) for r in dispatch_result["rejected"])
            and all(_duplicate_ok(r) for r in dispatch_result["duplicates_ignored"])):
        raise _Malformed
    return dispatch_result


def _cancellations(statement: Any) -> tuple[dict, frozenset] | None:
    """({task: {(request_id, digest)}}, unknown task ids) for an explicit complete v1 or v2 statement; else None.

    v1 = exactly {schema v1, complete, cancelled} (no task-level unknown). v2 = exactly {schema v2, complete,
    cancelled, unknown_tasks} with unknown_tasks a list of unique non-empty exact str task ids. A mixed version,
    a foreign or missing key, a duplicate unknown task or complete other than exact True is None (all unknown)."""
    if statement is None or not _strict(statement) or type(statement) is not dict:
        return None
    if _closed(statement, CANCELLATION_KEYS) and statement["schema"] == CANCELLATION_SCHEMA:
        unknown: list = []
    elif _closed(statement, CANCELLATION_KEYS_V2) and statement["schema"] == CANCELLATION_SCHEMA_V2:
        unknown = statement["unknown_tasks"]
        if type(unknown) is not list or not all(_text(task) for task in unknown) or len(set(unknown)) != len(unknown):
            return None
    else:
        return None
    if statement["complete"] is not True or type(statement["cancelled"]) is not list:
        return None
    by_task: dict[str, set] = {}
    for entry in statement["cancelled"]:
        if not (_closed(entry, CANCELLED_KEYS) and _text(entry["task_id"]) and _match(_ID, entry["request_id"])
                and _match(_HEX64, entry["request_digest"])):
            return None
        by_task.setdefault(entry["task_id"], set()).add((entry["request_id"], entry["request_digest"]))
    return by_task, frozenset(unknown)


def _coverage(complete: bool, cancellation: str, reason: str | None) -> dict:
    return {"schema": COVERAGE_SCHEMA, "complete": complete, "cancellation": cancellation, "reason": reason}


def task_controls(dispatch_result: Any, now: Any, *, cancellations: Any = None) -> dict:
    """Controls for the caller's S1 result at the caller's explicit ``now`` (see the module docstring)."""
    if type(now) is not datetime or now.utcoffset() is None:
        raise ValueError("now must be an offset-aware datetime")
    try:
        now = now.astimezone(timezone.utc)
    except OverflowError:
        raise ValueError("now is outside the representable UTC range") from None
    try:
        result = _validated(dispatch_result)
    except _Malformed:
        return {"controls": [], "withheld": [], "coverage": _coverage(False, "unknown", R_INPUT)}

    copies: dict[str, list[dict]] = {}
    for record in result["dispatches"]:
        copies.setdefault(record["dispatch_id"], []).append(record)
    withheld: dict[str, tuple[str | None, str]] = {}
    by_task: dict[str, dict] = {}
    for dispatch_id, group in copies.items():
        first = group[0]
        if any(copy != first for copy in group[1:]):
            for record in group:
                withheld[record["task_id"]] = (dispatch_id, W_CONFLICT)
            continue
        current = by_task.get(first["task_id"])
        if current is not None and current["dispatch_id"] != dispatch_id:
            withheld[first["task_id"]] = (None, W_AMBIGUOUS)
            continue
        by_task[first["task_id"]] = first

    held_rows = [r for r in result["rejected"] if r["reason"] == HELD_REASON]
    held = {r["task_id"] for r in held_rows if r["task_id"] is not None}
    # RCO1 P2-2: a hold whose task cannot be read might be any task's hold, so no task is provably current.
    held_unattributed = any(r["task_id"] is None for r in held_rows)
    statement = _cancellations(cancellations)
    cancelled_by_task, unknown_tasks = statement if statement is not None else (None, frozenset())
    controls = []
    for task, record in sorted(by_task.items()):
        if task in withheld:
            continue
        at = _instant(record["dispatched_utc"])
        # A superseded record of the task observed at/after the live dispatch (or at an unknown time), or one
        # that names any winner other than this live dispatch (RCO1 P2-1: a ghost or None), contradicts S1.
        superseded_after = any(
            r["reason"] == SUPERSEDED_REASON and r["task_id"] == task
            and (r["observed_utc"] is None or _instant(r["observed_utc"]) >= at
                 or r["superseded_by"] != record["dispatch_id"]) for r in result["rejected"])
        if at > now:
            reason = W_FUTURE
        elif held_unattributed:
            reason = W_HELD_UNATTRIBUTED
        elif task in held:
            reason = W_HELD
        elif superseded_after:
            reason = W_SUPERSESSION
        elif cancelled_by_task is None:
            reason = W_CANCEL_UNKNOWN
        elif task in unknown_tasks:
            # v2: this task's cancellation state is unknown (legacy, partial or HOLD control); it is withheld
            # before any cancel or live control, even if a known cancellation also names it.
            reason = W_CANCEL_TASK_UNKNOWN
        else:
            named = cancelled_by_task.get(task, set())
            mine = (record["dispatch_id"], record["request_digest"])
            if named - {mine}:
                reason = W_CANCEL_MISMATCH
            else:
                controls.append({"schema": CONTROL_SCHEMA, "task_id": task, "request_id": record["dispatch_id"],
                                 "request_digest": record["request_digest"],
                                 "state": "cancelled" if mine in named else "live",
                                 "observed_utc": record["dispatched_utc"]})
                continue
        withheld[task] = (record["dispatch_id"], reason)

    rows = [{"task_id": task, "request_id": rid, "reason": reason} for task, (rid, reason) in sorted(withheld.items())]
    cancellation = "unknown" if cancelled_by_task is None else "complete"
    return {"controls": controls, "withheld": rows,
            "coverage": _coverage(cancelled_by_task is not None and not rows, cancellation, None)}
