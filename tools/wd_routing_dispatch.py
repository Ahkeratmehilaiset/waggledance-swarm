#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Routing dispatch records from Lead's wake_requests (F26 S1, ``wd.routing-dispatch.v1``).

Pure and passive. ``dispatches(requests, now)`` reads only the request events the caller passes (as
parsed JSON) and the caller's explicit, offset-aware ``now``. It opens no file and reads no clock,
environment, queue, provider or process. A dispatch is scheduling evidence only: it never proves
completion, an accepted attempt, a remote push, evaluator identity or any release permission.

One record per accepted request, with exactly these fields::

    schema               "wd.routing-dispatch.v1"
    dispatch_id          the request's request_id, copied (ASCII [A-Za-z0-9._:-]{1,128})
    task_id              copied, non-empty
    revision             payload.task_revision, copied; never defaulted (absent: revision_missing)
    input_digest         wd_composer_select.digest of task_id, revision, message, payload.result_contract
                         and the normalized scope (Python canonical JSON)
    scope                wd_task_router.normalize_scope(write_scope), non-empty
    dispatch_key         wd_task_router.dispatch_key(task_id, revision, input_digest, scope)
    worker               the one addressee in ``to``
    requester            "codex-lead-1" (DISPATCH_AUTHORITY): a label, not proof of authorship
    request_digest       copied, 64 lowercase hex; never recomputed here (it is PowerShell canonical JSON)
    dispatched_utc       the request ts_utc, offset-aware and not after ``now``, as canonical UTC
    expected_responders  {worker: {agent_uuid, session_id, run_id}} copied from the request: labels only

Rules that keep it honest (Tools schema checkpoint a8837832, Fable design c96124d0 section 3a):

* Every request is checked to be strict built-in JSON (exact dict/list/str/int/float/bool/None, finite
  floats, exact str keys) BEFORE any equality, hashing or helper call, so a subclass or foreign object
  never runs its own hooks here.
* All copies of one request_id are compared first. Copies whose content or request_digest differ
  poison the whole id (request_binding_conflict), earlier valid copies included. Exact repeats give one
  record; the extra copies are listed in ``duplicates_ignored``.
* Per task, the newest valid dispatch wins by (dispatched_utc, dispatch_id); every other valid dispatch
  for that task is rejected as superseded_revision, whatever order the requests arrived in.
* A rejected request never lets an older revision become live (RCO2 f0e55 D-F1; Lead 17:11Z). Every
  rejection carries ``task_id`` and ``observed_utc`` when they are readable without running any hook (an
  exact non-empty str ``task_id``; a ``ts_utc`` exact str that parses as an offset time). When any
  rejection other than superseded_revision names a task at or after the would-be winner's instant, or
  at an unknown instant, the task is HELD: it has no dispatch, and each of its valid records is rejected
  as dispatch_held_newer_rejection (historical, visible, never live). A rejection whose task is not
  readable cannot be attributed and holds nothing.
* Only a would-be revision can hold (RCO2 1134 L; Lead decision a, 18:05Z): an input whose ``type`` is
  an exact str other than ``wake_request``, or whose ``agent`` is an exact str other than the dispatch
  authority, is not a task revision. Ordinary claim/message/reply/done events and other authors'
  wake_requests are still rejected and listed, but never hold a task, whatever else is wrong with them.
  An input whose type or agent cannot be read hook-free still holds (unknown is never cleared). This
  restricts freezing only; it grants no authority, and a forged authority label still holds (fail closed).
* ``to`` names exactly one worker: every comma piece (or list item) must be a canonical lane name as
  written, so an empty or padded piece is worker_invalid and two valid names are dispatch_target_ambiguous.
* There is no cancellation input. A dispatch is scheduling evidence only; the absence of a cancel record is
  never permission to act, and nothing here is wired to a caller.
* Only this module's own validation outcomes become rejections. Caller cancellation (KeyboardInterrupt,
  SystemExit, GeneratorExit) and unexpected errors propagate; a ``now`` that cannot be converted to UTC is
  a ValueError.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from tools.lane_profile_record import _utc
from tools.wd_composer_select import digest
from tools.wd_task_router import DISPATCH_AUTHORITY, _Stop, dispatch_key, normalize_scope

SCHEMA = "wd.routing-dispatch.v1"
REQUEST_TYPE = "wake_request"
DISPATCH_FIELDS = ("schema", "dispatch_id", "task_id", "revision", "input_digest", "scope", "dispatch_key",
                   "worker", "requester", "request_digest", "dispatched_utc", "expected_responders")
RESPONDER_FIELDS = ("agent_uuid", "session_id", "run_id")
REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}", re.ASCII)
LANE = re.compile(r"[a-z][a-z0-9_-]{1,32}", re.ASCII)
DIGEST = re.compile(r"[0-9a-f]{64}", re.ASCII)
MAX_DEPTH = 64

# Stable rejection reasons.
R_MALFORMED = "request_malformed"
R_REQUEST_ID = "request_id_invalid"
R_CONFLICT = "request_binding_conflict"
R_TYPE = "not_a_wake_request"
R_AUTHORITY = "not_dispatch_authority"
R_TASK = "task_id_missing"
R_REVISION = "revision_missing"
R_AMBIGUOUS = "dispatch_target_ambiguous"
R_WORKER = "worker_invalid"
R_DIGEST = "request_digest_invalid"
R_RESPONDERS = "expected_responders_invalid"
R_SCOPE_CONFLICT = "scope_conflict"
R_MESSAGE = "message_invalid"
R_TIME = "timestamp_invalid"
R_FUTURE = "future_dated"
R_SUPERSEDED = "superseded_revision"
R_HELD = "dispatch_held_newer_rejection"


class _Reject(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _strict_json(value: Any, depth: int = 0) -> bool:
    """Exact built-in JSON values only; checked by exact type before anything else touches the value."""
    kind = type(value)
    if value is None or kind is bool or kind is int or kind is str:
        return True
    if kind is float:
        return value == value and value not in (float("inf"), float("-inf"))
    if depth >= MAX_DEPTH:
        return False
    if kind is list:
        return all(_strict_json(item, depth + 1) for item in value)
    if kind is dict:
        return all(type(key) is str and _strict_json(item, depth + 1) for key, item in value.items())
    return False


def _text(value: Any) -> bool:
    return type(value) is str and bool(value)


def _rejection(request_id: str | None, reason: str, index: int | None = None,
               superseded_by: str | None = None, task_id: str | None = None,
               observed: datetime | None = None) -> dict:
    return {"request_id": request_id, "reason": reason, "index": index, "superseded_by": superseded_by,
            "task_id": task_id, "observed_utc": observed.isoformat() if observed is not None else None}


def _readable(request: Any) -> tuple[str | None, datetime | None]:
    """(task_id, ts) read without any lookup or hook: only an exact dict, exact str keys and exact str values."""
    task, moment = None, None
    if type(request) is dict:
        for key, value in dict.items(request):
            if type(key) is not str or type(value) is not str:
                continue
            if key == "task_id" and value:
                task = value
            elif key == "ts_utc":
                moment = _utc(value)
    return task, moment


def _may_revise(request: Any) -> bool:
    """False only when an exact str type or agent, read without any hook, shows the input is not a wake_request
    of the dispatch authority; such an input is never a task revision and cannot hold a task."""
    if type(request) is not dict:
        return True
    for key, value in dict.items(request):
        if type(key) is not str or type(value) is not str:
            continue
        if (key == "type" and value != REQUEST_TYPE) or (key == "agent" and value != DISPATCH_AUTHORITY):
            return False
    return True


def _worker(to: Any) -> str:
    if type(to) is str:
        names = to.split(",")
    elif type(to) is list and all(type(item) is str for item in to):
        names = list(to)
    else:
        raise _Reject(R_WORKER)
    # Exactly as written: an empty or padded piece is not a worker (RCO2 D-F2).
    if not names or not all(LANE.fullmatch(name) for name in names):
        raise _Reject(R_WORKER)
    if len(names) > 1:
        raise _Reject(R_AMBIGUOUS)
    return names[0]


def _scope(request: dict, payload: dict) -> list[str]:
    top, nested = request.get("write_scope"), payload.get("write_scope")
    if "write_scope" in request and "write_scope" in payload and digest(top) != digest(nested):
        raise _Reject(R_SCOPE_CONFLICT)
    raw = top if "write_scope" in request else nested
    try:
        return normalize_scope(raw)
    except _Stop as stop:
        raise _Reject(stop.reasons[0] if stop.reasons else "scope_invalid") from None


def _dispatch(request: dict, now: datetime) -> dict:
    """One strict-JSON request with a valid request_id -> its dispatch record, or _Reject."""
    if request.get("type") != REQUEST_TYPE:
        raise _Reject(R_TYPE)
    if request.get("agent") != DISPATCH_AUTHORITY:
        raise _Reject(R_AUTHORITY)
    task_id = request.get("task_id")
    if not _text(task_id):
        raise _Reject(R_TASK)
    payload = request.get("payload")
    if type(payload) is not dict or not _text(payload.get("task_revision")):
        raise _Reject(R_REVISION)
    revision = payload["task_revision"]
    worker = _worker(request.get("to"))
    request_digest = request.get("request_digest")
    if type(request_digest) is not str or not DIGEST.fullmatch(request_digest):
        raise _Reject(R_DIGEST)
    responders = request.get("expected_responders")
    if type(responders) is not dict or list(responders) != [worker]:
        raise _Reject(R_RESPONDERS)
    binding = responders[worker]
    if (type(binding) is not dict or set(binding) != set(RESPONDER_FIELDS)
            or not all(_text(binding[field]) for field in RESPONDER_FIELDS)):
        raise _Reject(R_RESPONDERS)
    scope = _scope(request, payload)
    message = request.get("message")
    if type(message) is not str:
        raise _Reject(R_MESSAGE)
    stamp = request.get("ts_utc")
    dispatched = _utc(stamp) if type(stamp) is str else None
    if dispatched is None:
        raise _Reject(R_TIME)
    if dispatched > now:
        raise _Reject(R_FUTURE)
    input_digest = digest({"task_id": task_id, "revision": revision, "message": message,
                           "result_contract": payload.get("result_contract"), "scope": scope})
    key = dispatch_key(task_id, revision, input_digest, scope) if input_digest is not None else None
    if key is None:
        raise _Reject(R_MALFORMED)
    return {"schema": SCHEMA, "dispatch_id": request["request_id"], "task_id": task_id, "revision": revision,
            "input_digest": input_digest, "scope": scope, "dispatch_key": key, "worker": worker,
            "requester": DISPATCH_AUTHORITY, "request_digest": request_digest,
            "dispatched_utc": dispatched.isoformat(),
            "expected_responders": {worker: {field: binding[field] for field in RESPONDER_FIELDS}}}


def dispatches(requests: Any, now: Any) -> dict:
    """Dispatch records for the caller's request events at the caller's explicit, offset-aware ``now``.

    Returns ``{"dispatches", "rejected", "duplicates_ignored"}``. Dispatches are sorted by dispatch_id and
    rejections by (request_id, reason); a rejection without a usable request_id carries its input index.
    """
    if type(now) is not datetime or now.utcoffset() is None:
        raise ValueError("now must be an offset-aware datetime")
    if type(requests) is not list:
        raise ValueError("requests must be a list")
    try:
        now = now.astimezone(timezone.utc)
    except OverflowError:
        raise ValueError("now is outside the representable UTC range") from None
    rejected: list[dict] = []
    # (task_id, instant or None) of every input behind a non-superseded rejection; they can hold a task.
    blockers: list[tuple[str | None, datetime | None]] = []
    groups: dict[str, list[dict]] = {}
    poisoned: set[str] = set()
    for index, request in enumerate(requests):
        if type(request) is not dict or not _strict_json(request) or digest(request) is None:
            task, moment = _readable(request)
            if _may_revise(request):
                blockers.append((task, moment))
            rejected.append(_rejection(None, R_MALFORMED, index, task_id=task, observed=moment))
            # A malformed copy that names a valid id still differs from every other copy of that id, so the
            # whole id is poisoned. Read without a lookup: only exact-str keys and value are compared.
            if type(request) is dict:
                for key, value in dict.items(request):
                    if (type(key) is str and key == "request_id" and type(value) is str
                            and REQUEST_ID.fullmatch(value)):
                        poisoned.add(value)
            continue
        request_id = request.get("request_id")
        if type(request_id) is not str or not REQUEST_ID.fullmatch(request_id):
            task, moment = _readable(request)
            if _may_revise(request):
                blockers.append((task, moment))
            rejected.append(_rejection(None, R_REQUEST_ID, index, task_id=task, observed=moment))
            continue
        groups.setdefault(request_id, []).append(request)

    duplicates: list[dict] = []
    candidates: list[dict] = []
    for request_id, copies in groups.items():
        # Same id, other content or another request_digest: nothing under that id is trusted.
        if request_id in poisoned or len({digest(copy) for copy in copies}) != 1:
            seen = [_readable(copy) for copy in copies]
            blockers.extend(pair for pair, copy in zip(seen, copies) if _may_revise(copy))
            tasks = {task for task, _ in seen}
            task = next(iter(tasks)) if len(tasks) == 1 else None
            moments = [moment for _, moment in seen]
            moment = max(moments) if task is not None and None not in moments else None
            rejected.append(_rejection(request_id, R_CONFLICT, task_id=task, observed=moment))
            continue
        if len(copies) > 1:
            duplicates.append({"request_id": request_id, "copies_ignored": len(copies) - 1})
        try:
            candidates.append(_dispatch(copies[0], now))
        except _Reject as reject:
            task, moment = _readable(copies[0])
            if _may_revise(copies[0]):
                blockers.append((task, moment))
            rejected.append(_rejection(request_id, reject.reason, task_id=task, observed=moment))

    def order(record: dict) -> tuple:
        # Compared as instants, never as text; the id breaks a tie.
        return _utc(record["dispatched_utc"]), record["dispatch_id"]

    newest: dict[str, dict] = {}
    for record in candidates:
        current = newest.get(record["task_id"])
        if current is None or order(record) > order(current):
            newest[record["task_id"]] = record
    def held(task: str) -> bool:
        # A rejection for the task at or after the winner's instant, or at an unknown instant, holds it.
        at = order(newest[task])[0]
        return any(name == task and (moment is None or moment >= at) for name, moment in blockers)

    kept = []
    for record in candidates:
        winner = newest[record["task_id"]]
        moment = _utc(record["dispatched_utc"])
        if held(record["task_id"]):
            rejected.append(_rejection(record["dispatch_id"], R_HELD, task_id=record["task_id"], observed=moment))
        elif record is winner:
            kept.append(record)
        else:
            rejected.append(_rejection(record["dispatch_id"], R_SUPERSEDED, superseded_by=winner["dispatch_id"],
                                       task_id=record["task_id"], observed=moment))

    return {"dispatches": sorted(kept, key=lambda record: record["dispatch_id"]),
            "rejected": sorted(rejected, key=lambda item: (item["request_id"] is None, item["request_id"] or "",
                                                           item["reason"], item["index"] or 0)),
            "duplicates_ignored": sorted(duplicates, key=lambda item: item["request_id"])}
