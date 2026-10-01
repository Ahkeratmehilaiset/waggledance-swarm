#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Request-cancellation derivation from canonical Bridge events (F26 S-A; pure and DORMANT).

Two entrypoints, both pure: they open no file and read no clock, environment, shared reader, queue, provider
or process, and nothing calls them yet. Design: claude-rco-2 870a7df3; repairs of claude-rco-2 80813c0d
(H-1, E-1, A-1) and claude-rco-1 20:21:24Z (F1-F4).

* ``derive_cancellations_from_prefix(prefix, identity, now, *, max_age_seconds)``: ``prefix`` is the RAW bytes
  of the canonical log prefix and ``identity`` the closed S-C current-log identity {log_generation,
  file_identity, log_bytes, prefix_sha256, observed_utc, complete} (Get-BridgeCurrentLogIdentity). The bytes
  must have exactly ``log_bytes`` length and that lowercase sha256, ``complete`` must be exactly True and
  ``observed_utc`` fresh; then every line is parsed HERE (UTF-8, one JSON object per LF-terminated line, no
  duplicate key, no NaN/Infinity/overflowing float, no unfinished tail), so the events provably are the
  measured prefix. The identity labels are still S-C measurements, not provenance of any authority.
* ``derive_cancellations(events, snapshot, current_log, now, *, max_age_seconds)``: the LEGACY list API. The
  caller supplies parsed events plus a closed ``wd.routing-cancellation-snapshot.v1`` {schema, log_generation,
  file_identity, snapshot_bytes, prefix_sha256, observed_utc, truncated, event_count, events_sha256} and the
  current log identity {log_generation, file_identity, log_bytes, prefix_sha256}. ``event_count`` and
  ``events_sha256`` (sha256 of ``json.dumps(events, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
  allow_nan=False)`` as ASCII) are recomputed here, so an omitted, added, reordered or changed event is
  ``event_coverage_mismatch``; but a caller can filter and recompute, so this API is CORRELATION ONLY: never
  provenance, and never a basis for a global known-clear.

Output ``wd.routing-cancellation-derivation.v1`` (closed)::

    {schema, complete, cancelled: [{task_id, request_id, request_digest}], unknown_tasks: [task_id], reason}

The schema is deliberately NOT the P2 task-control input ``wd.routing-cancellation-coverage.v1``, so no existing
consumer reads this output silently. ``complete`` describes READ COVERAGE only.

BOUNDARY (F4): this pure output alone does NOT authorize anything. complete=true with a task absent from
unknown_tasks means only that no structured control in the read matched; it does not cover out-of-log state.
Independent global HOLD, flags, work_held and release_held inputs remain MANDATORY at any future live caller,
which must withhold everything while any of them is set.

Rules (fail closed):

* Every input is checked FIRST to be an acyclic, bounded tree of exact built-ins; a foreign or subclass object
  anywhere runs no hook and makes the whole result incomplete (``input_malformed``). Each event is bounded
  separately (MAX_DEPTH, MAX_EVENT_NODES) and a read holds at most MAX_EVENTS events
  (``event_count_over_policy``).
* Coverage is complete only for an untruncated read of the CURRENT log identity observed at or before ``now``
  and no more than ``max_age_seconds`` (caller policy, no default) earlier.
* Only an event with agent exactly ``codex-lead-1``, type an exact str, status exactly ``cancelled``, a
  non-empty task_id and the closed payload ``wd.request-cancellation.v1`` = {schema, cancelled_request_id,
  cancelled_request_digest, scope: "whole_request"} is a cancellation FACT; the digest is copied verbatim.
* Any other authority event is IGNORED only when its type and status are exact str forming a pair in
  BENIGN_AUTHORITY_PAIRS and it carries no control signal. Every other authority event (a novel or control
  pair, a non-str type or status, a homoglyph, a legacy or partial cancellation, a casefolded Cancelled) makes
  its TASK unknown, together with tasks it names under a task key; one whose own or named task cannot be read
  makes the whole result incomplete (``cancellation_unattributable``). No vocabulary ever becomes a fact.
* A control signal is a CONTROL_STEMS stem (after casefold) in the type or status, in any key at any depth, in
  the str value of a DIRECTIVE_KEYS key, or in ANY other str value, the message included. Only a TOKEN-shaped
  value (no whitespace) under an exact or separator-delimited id key (id, request_id, event-id; not valid or
  paid), a task key, a timestamp key (ts, *_utc), agent or write_scope is not scanned, so legitimate ids and
  scoped paths containing hold or cancel stay clear while free text under those keys is scanned; a one-word
  token such as HOLD under an id key is read as an id (boundary). Over-match only withholds a task.
* Events by any other label are never facts and never clear anything: a control signal there makes the task
  it names unknown (UNKNOWN-ONLY); an other-label control naming no readable task is not attributable to a
  task and is left to the mandatory global HOLD inputs above.
* Contradictory facts (one request with two digests, or one request id under two tasks) make every task
  involved unknown.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
from typing import Any

SCHEMA = "wd.routing-cancellation-derivation.v1"
OUTPUT_FIELDS = ("schema", "complete", "cancelled", "unknown_tasks", "reason")
FACT_SCHEMA = "wd.request-cancellation.v1"
FACT_FIELDS = ("schema", "cancelled_request_id", "cancelled_request_digest", "scope")
WHOLE_REQUEST = "whole_request"
AUTHORITY = "codex-lead-1"
CANCELLED_STATUS = "cancelled"
SNAPSHOT_SCHEMA = "wd.routing-cancellation-snapshot.v1"
SNAPSHOT_FIELDS = ("schema", "log_generation", "file_identity", "snapshot_bytes", "prefix_sha256", "observed_utc",
                   "truncated", "event_count", "events_sha256")
CURRENT_LOG_FIELDS = ("log_generation", "file_identity", "log_bytes", "prefix_sha256")
IDENTITY_FIELDS = ("log_generation", "file_identity", "log_bytes", "prefix_sha256", "observed_utc", "complete")
BENIGN_AUTHORITY_PAIRS = frozenset({
    ("wake_request", "assigned"), ("wake_request", "request"), ("claim", "active"), ("heartbeat", "active"),
    ("done", "done"), ("message", "answered"), ("message", "informational"), ("message", "progress"),
    ("handoff", "handoff"),
})
CONTROL_STEMS = ("cancel", "hold", "held", "paus", "withdr", "supersed", "revok", "abort", "suspend", "freez", "frozen",
                 "halt", "stop", "rescind", "retract")
DIRECTIVE_KEYS = frozenset({"action", "control", "command", "directive", "decision", "state", "status", "mode",
                            "kind", "type"})
MAX_DEPTH = 32
MAX_EVENT_NODES = 50_000
MAX_EVENTS = 1_000_000
MAX_PREFIX_BYTES = 268_435_456
_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}", re.ASCII)
_HEX64 = re.compile(r"[0-9a-f]{64}", re.ASCII)
_TASK_KEY = re.compile(r".*tasks?[_-]?(?:ids?)?", re.ASCII)
# Keys whose TOKEN-shaped values are not free text: exact or separator-delimited id names (id, request_id,
# event-id; never an English word that merely ends in "id" such as valid or paid), task names, timestamps, the
# agent label and write_scope paths (RCO1 R2 on 76a60086). A value is exempt only when it is a token (no
# whitespace: an id, a time or a path); free text under these keys is still scanned. Time keys stay exactly
# *_utc and ts as at 76a60086: <x>_ts, <x>-ts, utc and <x>-utc are ordinary keys (RCO2 2d4b F1).
_QUIET_KEY = re.compile(r"(?:.*[_-])?(?:ids?|tasks?(?:[_-]?ids?)?)|.*_utc|ts|agent|write_scope", re.ASCII)
_TOKEN = re.compile(r"[A-Za-z0-9._:/\\@+-]{1,512}", re.ASCII)

R_INPUT = "input_malformed"
R_EVENT_POLICY = "event_count_over_policy"
R_SNAPSHOT = "snapshot_incomplete"
R_IDENTITY = "identity_incomplete"
R_PREFIX = "prefix_not_identity"
R_TAIL = "prefix_unfinished_row"
R_NOT_CURRENT = "snapshot_not_current_log"
R_FUTURE = "snapshot_future"
R_STALE = "snapshot_stale"
R_EVENTS = "event_coverage_mismatch"
R_UNATTRIBUTABLE = "cancellation_unattributable"


def _strict(value: Any) -> bool:
    """Exact built-ins only, acyclic, at most MAX_DEPTH levels and MAX_EVENT_NODES nodes; reads only through
    type(), dict.items and exact-list iteration, so no foreign hook runs."""
    budget = [MAX_EVENT_NODES]
    active: set = set()

    def walk(item: Any, depth: int) -> bool:
        budget[0] -= 1
        if budget[0] < 0 or depth > MAX_DEPTH:
            return False
        kind = type(item)
        if item is None or kind is str or kind is bool or kind is int:
            return True
        if kind is float:
            return math.isfinite(item)
        if kind is not dict and kind is not list:
            return False
        if id(item) in active:          # a cycle
            return False
        active.add(id(item))
        try:
            if kind is dict:
                return all(type(key) is str and walk(child, depth + 1) for key, child in dict.items(item))
            return all(walk(child, depth + 1) for child in item)
        finally:
            active.discard(id(item))

    return walk(value, 0)


def _events_sha256(events: list) -> str | None:
    """sha256 of the canonical JSON of the exact strict list (incremental, same bytes as one json.dumps of the
    whole list); None when a value cannot be encoded (an int beyond the str conversion limit)."""
    digest = hashlib.sha256(b"[")
    try:
        for index, event in enumerate(events):
            if index:
                digest.update(b",")
            digest.update(json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                                     allow_nan=False).encode("ascii"))
    except ValueError:
        return None
    digest.update(b"]")
    return digest.hexdigest()


def _get(record: Any, name: str) -> Any:
    """``name`` of an exact dict already accepted by _strict (None when absent or not a dict)."""
    return dict.get(record, name) if type(record) is dict else None


def _closed(record: Any, fields: tuple) -> bool:
    return type(record) is dict and len(record) == len(fields) and all(dict.__contains__(record, f) for f in fields)


def _text(value: Any) -> bool:
    return type(value) is str and value != ""


def _stem(value: Any) -> bool:
    """A str naming a control after casefold (an exact str: _strict ran first)."""
    if type(value) is not str:
        return False
    folded = value.casefold()
    return any(stem in folded for stem in CONTROL_STEMS)


def _directive(value: Any) -> bool:
    return _stem(value) or (type(value) is list and any(_stem(item) for item in value))


def _signals(event: dict) -> tuple[bool, list, bool]:
    """(control signal, task ids named under a task key, every task reference readable) for a strict event."""
    control = False
    tasks: list = []
    readable = True
    stack: list = [("", event)]
    while stack:
        key, item = stack.pop()
        if type(item) is str:
            if not (_QUIET_KEY.fullmatch(key) and _TOKEN.fullmatch(item)) and _stem(item):
                control = True                     # free text, the message included: unknown-only
            continue
        if type(item) is list:
            stack.extend((key, child) for child in item)
            continue
        if type(item) is not dict:
            continue
        for name, value in dict.items(item):
            folded = name.casefold()
            if _stem(folded) or (folded in DIRECTIVE_KEYS and _directive(value)):
                control = True
            if _TASK_KEY.fullmatch(folded):
                if _text(value):
                    tasks.append(value)
                elif type(value) is list and all(_text(entry) for entry in value):
                    tasks.extend(value)
                else:
                    readable = False
            stack.append((folded, value))
    return control, tasks, readable


def _instant(value: Any) -> datetime | None:
    if type(value) is not str:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None


def _count(value: Any) -> bool:
    return type(value) is int and value >= 0


def _hex64(value: Any) -> bool:
    return type(value) is str and _HEX64.fullmatch(value) is not None


def _result(complete: bool, cancelled: list, unknown: list, reason: str | None) -> dict:
    return {"schema": SCHEMA, "complete": complete, "cancelled": cancelled, "unknown_tasks": unknown, "reason": reason}


def _incomplete(reason: str) -> dict:
    return _result(False, [], [], reason)


def _caller_contract(now: Any, max_age_seconds: Any) -> tuple[datetime, timedelta]:
    if type(now) is not datetime or now.utcoffset() is None:
        raise ValueError("now must be an offset-aware datetime")
    if type(max_age_seconds) is not int or max_age_seconds <= 0:
        raise ValueError("max_age_seconds must be a positive int (caller policy, no default)")
    try:
        return now.astimezone(timezone.utc), timedelta(seconds=max_age_seconds)
    except OverflowError:
        raise ValueError("now is outside the representable UTC range") from None


def _freshness(observed_utc: Any, now: datetime, max_age: timedelta) -> str | None:
    observed = _instant(observed_utc)
    if observed > now:
        return R_FUTURE
    if now - observed > max_age:
        return R_STALE
    return None


def _coverage_reason(snapshot: Any, current_log: Any, now: datetime, max_age: timedelta) -> str | None:
    """None when the read is untruncated, of the CURRENT log identity and fresh; else the stable reason."""
    if not (_closed(snapshot, SNAPSHOT_FIELDS) and _get(snapshot, "schema") == SNAPSHOT_SCHEMA
            and _text(_get(snapshot, "log_generation"))
            and _text(_get(snapshot, "file_identity")) and _count(_get(snapshot, "snapshot_bytes"))
            and _hex64(_get(snapshot, "prefix_sha256"))
            and _get(snapshot, "truncated") is False and _instant(_get(snapshot, "observed_utc")) is not None
            and _count(_get(snapshot, "event_count")) and _hex64(_get(snapshot, "events_sha256"))):
        return R_SNAPSHOT
    if not (_closed(current_log, CURRENT_LOG_FIELDS) and _text(_get(current_log, "log_generation"))
            and _text(_get(current_log, "file_identity")) and _count(_get(current_log, "log_bytes"))
            and _hex64(_get(current_log, "prefix_sha256"))):
        return R_SNAPSHOT
    if (_get(snapshot, "log_generation") != _get(current_log, "log_generation")
            or _get(snapshot, "file_identity") != _get(current_log, "file_identity")
            or _get(snapshot, "snapshot_bytes") != _get(current_log, "log_bytes")
            or _get(snapshot, "prefix_sha256") != _get(current_log, "prefix_sha256")):
        return R_NOT_CURRENT
    return _freshness(_get(snapshot, "observed_utc"), now, max_age)


def _fact(event: dict) -> tuple | None:
    """(request_id, digest) for a closed whole-request v1 fact, else None (the task becomes unknown)."""
    if _get(event, "status") != CANCELLED_STATUS or type(_get(event, "type")) is not str:
        return None
    payload = _get(event, "payload")
    if not (_closed(payload, FACT_FIELDS) and _get(payload, "schema") == FACT_SCHEMA
            and _get(payload, "scope") == WHOLE_REQUEST):
        return None
    request_id, digest = _get(payload, "cancelled_request_id"), _get(payload, "cancelled_request_digest")
    if not (type(request_id) is str and _ID.fullmatch(request_id) and _hex64(digest)):
        return None
    return request_id, digest


def _derive(events: list) -> dict:
    """Facts and unknown tasks of strict events whose coverage is already established."""
    facts: dict[tuple, set] = {}     # (task, request_id) -> digests
    unknown: set[str] = set()
    for event in events:
        if type(event) is not dict:
            return _incomplete(R_INPUT)            # an event that is not an object cannot be ruled out
        control, named, readable = _signals(event)
        task = _get(event, "task_id")
        if _get(event, "agent") != AUTHORITY:
            if control and _text(task) and readable:
                unknown.add(task)                  # another label: unknown-only, never a fact or a clear
                unknown.update(named)
            continue
        kind, status = _get(event, "type"), _get(event, "status")
        if not control and type(kind) is str and type(status) is str and (kind, status) in BENIGN_AUTHORITY_PAIRS:
            continue                               # an exact-str allowlisted benign pair with no control signal
        if not _text(task) or not readable:
            return _incomplete(R_UNATTRIBUTABLE)   # might be any task's control
        fact = _fact(event)
        unknown.update(name for name in named if name != task)
        if fact is None:
            unknown.add(task)                      # every other authority shape: task unknown
            continue
        facts.setdefault((task, fact[0]), set()).add(fact[1])

    tasks_of: dict[str, set] = {}
    for task, request_id in facts:
        tasks_of.setdefault(request_id, set()).add(task)
    for (task, request_id), digests in facts.items():
        if len(digests) != 1 or len(tasks_of[request_id]) != 1:
            unknown.update(tasks_of[request_id])   # contradictory facts: every task involved is unknown
            unknown.add(task)
    cancelled = [{"task_id": task, "request_id": request_id, "request_digest": next(iter(digests))}
                 for (task, request_id), digests in sorted(facts.items()) if task not in unknown]
    return _result(True, cancelled, sorted(unknown), None)


def derive_cancellations(events: Any, snapshot: Any, current_log: Any, now: Any, *, max_age_seconds: Any) -> dict:
    """LEGACY list API, correlation only (see the module docstring)."""
    now, max_age = _caller_contract(now, max_age_seconds)
    # Every input before anything is read: a foreign object anywhere is unknown coverage.
    if not (type(events) is list and _strict(snapshot) and _strict(current_log)):
        return _incomplete(R_INPUT)
    if len(events) > MAX_EVENTS:
        return _incomplete(R_EVENT_POLICY)
    if not all(_strict(event) for event in events):
        return _incomplete(R_INPUT)
    reason = _coverage_reason(snapshot, current_log, now, max_age)
    if reason is not None:
        return _incomplete(reason)
    if len(events) != _get(snapshot, "event_count"):
        return _incomplete(R_EVENTS)
    digest = _events_sha256(events)
    if digest is None:
        return _incomplete(R_INPUT)
    if digest != _get(snapshot, "events_sha256"):
        return _incomplete(R_EVENTS)
    return _derive(events)


def _unique_object(pairs: list) -> dict:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate key")
    return dict(pairs)


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("non-finite number")
    return value


def _no_constant(text: str) -> None:
    raise ValueError("non-finite constant")


def derive_cancellations_from_prefix(prefix: Any, identity: Any, now: Any, *, max_age_seconds: Any) -> dict:
    """Raw-prefix API: the bytes must be exactly the S-C measured prefix; every line is parsed here."""
    now, max_age = _caller_contract(now, max_age_seconds)
    if type(prefix) is not bytes or not _strict(identity):
        return _incomplete(R_INPUT)
    if not (_closed(identity, IDENTITY_FIELDS) and _get(identity, "complete") is True
            and _text(_get(identity, "log_generation")) and _text(_get(identity, "file_identity"))
            and _count(_get(identity, "log_bytes")) and _get(identity, "log_bytes") <= MAX_PREFIX_BYTES
            and _hex64(_get(identity, "prefix_sha256")) and _instant(_get(identity, "observed_utc")) is not None):
        return _incomplete(R_IDENTITY)
    if len(prefix) != _get(identity, "log_bytes") or hashlib.sha256(prefix).hexdigest() != _get(identity, "prefix_sha256"):
        return _incomplete(R_PREFIX)
    reason = _freshness(_get(identity, "observed_utc"), now, max_age)
    if reason is not None:
        return _incomplete(reason)
    if prefix and not prefix.endswith(b"\n"):
        return _incomplete(R_TAIL)
    if prefix.count(b"\n") > MAX_EVENTS:
        return _incomplete(R_EVENT_POLICY)
    events = []
    for line in prefix.split(b"\n")[:-1]:
        try:
            event = json.loads(line.decode("utf-8"), object_pairs_hook=_unique_object, parse_float=_finite_float,
                               parse_constant=_no_constant)
        except (ValueError, RecursionError):
            return _incomplete(R_INPUT)            # malformed, duplicate key, non-finite, invalid UTF-8, too deep
        if type(event) is not dict or not _strict(event):
            return _incomplete(R_INPUT)
        events.append(event)
    return _derive(events)
