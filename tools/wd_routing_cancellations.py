#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Request-cancellation derivation from canonical Bridge events (F26 S-A; pure and DORMANT).

``derive_cancellations(events, snapshot, current_log, now, *, max_age_seconds)`` reads only what the caller
passes: the parsed events of one read of the canonical log, that read's own snapshot identity, the current
log identity the caller observed, its explicit offset-aware ``now`` and an explicit freshness bound. It opens
no file and reads no clock, environment, shared reader, queue, provider or process, and nothing calls it
yet. It is MECHANICAL correlation: an actor label is a label, not provenance, and nothing here says
identity_verified. Design: claude-rco-2 870a7df3 (.codex-audit/rco2-cancellation-port-design-20261001).

Output ``wd.routing-cancellation-derivation.v1`` (closed)::

    {schema, complete, cancelled: [{task_id, request_id, request_digest}], unknown_tasks: [task_id], reason}

The schema is deliberately NOT the P2 task-control input ``wd.routing-cancellation-coverage.v1``: carrying
``unknown_tasks`` safely needs a future P2 extension, so no existing consumer reads this output silently.
``complete`` describes READ COVERAGE only (an untruncated, current, fresh read), never acceptance or authority.

Rules (fail closed):

* Every input is checked FIRST to be an acyclic, bounded tree of exact built-ins (dict with exact str keys,
  list, str, int, bool, finite float, None); a foreign or subclass object anywhere runs no hook and makes the
  whole result incomplete (``input_malformed``).
* Coverage is complete only when ``snapshot`` = {log_generation, file_identity, snapshot_bytes, prefix_sha256,
  observed_utc, truncated} has ``truncated`` exactly False, names the SAME generation, file identity, byte
  length and full-prefix sha256 as ``current_log`` = {log_generation, file_identity, log_bytes, prefix_sha256},
  and was observed at or before ``now`` and no more than ``max_age_seconds`` (caller policy, no default)
  earlier. A frozen historical inventory therefore never counts as current cancellation coverage.
* Only an event with agent exactly ``codex-lead-1``, status exactly ``cancelled``, a non-empty task_id and the
  closed payload ``wd.request-cancellation.v1`` = {schema, cancelled_request_id, cancelled_request_digest,
  scope: "whole_request"} is a cancellation FACT; the digest is copied verbatim, never recomputed.
* Any other authority cancellation (the legacy free-form shapes, a partial or list form, a missing or malformed
  digest, extra keys) makes its TASK unknown: never a fact, never ignored, and no legacy event is reinterpreted
  as live authority. One whose task cannot be read makes the whole result incomplete
  (``cancellation_unattributable``). Contradictory facts (one request with two digests, or one request id under
  two tasks) make every task involved unknown. A cancellation by any other label is not read: a label cannot
  cancel the authority's dispatch.
* Operator HOLDs and free-text controls outside this contract are NOT handled here and stay unknown to callers.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
SNAPSHOT_FIELDS = ("log_generation", "file_identity", "snapshot_bytes", "prefix_sha256", "observed_utc", "truncated")
CURRENT_LOG_FIELDS = ("log_generation", "file_identity", "log_bytes", "prefix_sha256")
MAX_DEPTH = 32
MAX_NODES = 200_000
_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}", re.ASCII)
_HEX64 = re.compile(r"[0-9a-f]{64}", re.ASCII)

R_INPUT = "input_malformed"
R_SNAPSHOT = "snapshot_incomplete"
R_NOT_CURRENT = "snapshot_not_current_log"
R_FUTURE = "snapshot_future"
R_STALE = "snapshot_stale"
R_UNATTRIBUTABLE = "cancellation_unattributable"


def _strict(value: Any) -> bool:
    """Exact built-ins only, acyclic, at most MAX_DEPTH levels and MAX_NODES nodes; reads only through type(),
    dict.items and exact-list iteration, so no foreign hook runs."""
    budget = [MAX_NODES]
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


def _get(record: Any, name: str) -> Any:
    """``name`` of an exact dict already accepted by _strict (None when absent or not a dict)."""
    return dict.get(record, name) if type(record) is dict else None


def _closed(record: Any, fields: tuple) -> bool:
    return type(record) is dict and len(record) == len(fields) and all(dict.__contains__(record, f) for f in fields)


def _text(value: Any) -> bool:
    return type(value) is str and value != ""


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


def _result(complete: bool, cancelled: list, unknown: list, reason: str | None) -> dict:
    return {"schema": SCHEMA, "complete": complete, "cancelled": cancelled, "unknown_tasks": unknown, "reason": reason}


def _incomplete(reason: str) -> dict:
    return _result(False, [], [], reason)


def _coverage_reason(snapshot: Any, current_log: Any, now: datetime, max_age: timedelta) -> str | None:
    """None when the read is untruncated, of the CURRENT log identity and fresh; else the stable reason."""
    if not (_closed(snapshot, SNAPSHOT_FIELDS) and _text(_get(snapshot, "log_generation"))
            and _text(_get(snapshot, "file_identity")) and _count(_get(snapshot, "snapshot_bytes"))
            and type(_get(snapshot, "prefix_sha256")) is str and _HEX64.fullmatch(_get(snapshot, "prefix_sha256"))
            and _get(snapshot, "truncated") is False and _instant(_get(snapshot, "observed_utc")) is not None):
        return R_SNAPSHOT
    if not (_closed(current_log, CURRENT_LOG_FIELDS) and _text(_get(current_log, "log_generation"))
            and _text(_get(current_log, "file_identity")) and _count(_get(current_log, "log_bytes"))
            and type(_get(current_log, "prefix_sha256")) is str
            and _HEX64.fullmatch(_get(current_log, "prefix_sha256"))):
        return R_SNAPSHOT
    if (_get(snapshot, "log_generation") != _get(current_log, "log_generation")
            or _get(snapshot, "file_identity") != _get(current_log, "file_identity")
            or _get(snapshot, "snapshot_bytes") != _get(current_log, "log_bytes")
            or _get(snapshot, "prefix_sha256") != _get(current_log, "prefix_sha256")):
        return R_NOT_CURRENT
    observed = _instant(_get(snapshot, "observed_utc"))
    if observed > now:
        return R_FUTURE
    if now - observed > max_age:
        return R_STALE
    return None


def _fact(event: dict) -> tuple | None:
    """(request_id, digest) for a closed whole-request v1 fact, else None (the task becomes unknown)."""
    payload = _get(event, "payload")
    if not (_closed(payload, FACT_FIELDS) and _get(payload, "schema") == FACT_SCHEMA
            and _get(payload, "scope") == WHOLE_REQUEST):
        return None
    request_id, digest = _get(payload, "cancelled_request_id"), _get(payload, "cancelled_request_digest")
    if not (type(request_id) is str and _ID.fullmatch(request_id) and type(digest) is str and _HEX64.fullmatch(digest)):
        return None
    return request_id, digest


def derive_cancellations(events: Any, snapshot: Any, current_log: Any, now: Any, *, max_age_seconds: Any) -> dict:
    """The cancellation derivation for one canonical read (see the module docstring)."""
    if type(now) is not datetime or now.utcoffset() is None:
        raise ValueError("now must be an offset-aware datetime")
    if type(max_age_seconds) is not int or max_age_seconds <= 0:
        raise ValueError("max_age_seconds must be a positive int (caller policy, no default)")
    try:
        now = now.astimezone(timezone.utc)
    except OverflowError:
        raise ValueError("now is outside the representable UTC range") from None
    # Every input as a whole before anything is read: a foreign object anywhere is unknown coverage.
    if not (type(events) is list and _strict(events) and _strict(snapshot) and _strict(current_log)):
        return _incomplete(R_INPUT)
    reason = _coverage_reason(snapshot, current_log, now, timedelta(seconds=max_age_seconds))
    if reason is not None:
        return _incomplete(reason)

    facts: dict[tuple, set] = {}     # (task, request_id) -> digests
    unknown: set[str] = set()
    for event in events:
        if type(event) is not dict:
            return _incomplete(R_INPUT)            # an event that is not an object cannot be ruled out
        if _get(event, "agent") != AUTHORITY or _get(event, "status") != CANCELLED_STATUS:
            continue                               # not an authority cancellation: not read
        task = _get(event, "task_id")
        if not _text(task):
            return _incomplete(R_UNATTRIBUTABLE)   # might be any task's cancellation
        fact = _fact(event)
        if fact is None:
            unknown.add(task)                      # legacy, partial, list, missing digest: task unknown
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
