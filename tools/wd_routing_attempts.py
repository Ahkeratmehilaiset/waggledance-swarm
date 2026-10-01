#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Routing attempt records from dispatches, claims and releases (F26 S2, ``wd.routing-attempt.v1``).

Pure and passive. ``attempts(dispatches, claims, releases, push_receipts, acceptances, now)`` reads only
the records the caller passes (as parsed JSON) and the caller's explicit, offset-aware ``now``. It opens
no file and reads no clock, environment, queue, provider or process. An attempt is scheduling evidence
only: it never proves completion, a remote push, authorship, acceptance or any release permission.

Output ``{"attempts", "expired_unreleased", "rejected", "duplicates_ignored"}``. Each attempt has exactly
the router's ``ATTEMPT_KEYS`` (tools/wd_task_router):

    attempt_id         wd_composer_select.digest({dispatch_id, worker, owner_session_id}): one session
                       answering one dispatch is one attempt, so a ``-Force`` claim refresh keeps it
    dispatch_key       task_id, worker: copied from the dispatch
    scope              normalize_scope(claim write_scope); inside the dispatch scope
    state              "active" (a present claim) or "released" (a matching release record, any
                       release_status, stale_lease included). Never "accepted" in this version.
    lease_expires_utc  the claim's claim_lease_expires_utc, as canonical UTC
    artifacts          always [] in this version

Inputs:

* ``dispatches``: ``wd.routing-dispatch.v1`` records (tools/wd_routing_dispatch), closed field set.
* ``claims``: claim-file objects (Claim-AgentTask.ps1) that are present now; ``releases``: done-file
  objects (Release-AgentTask.ps1, Invoke-StaleClaimSweep.ps1). Extra claim fields are allowed; the ones
  read are checked exactly.
* ``push_receipts`` and ``acceptances``: accepted as lists and strictly checked, then every item is
  rejected. No accepted push-receipt source (S3) and no acceptance source (S4) exists yet, so no
  attempt is ever ``accepted``, no artifact is ever remote-verified and nothing here earns quality credit.

Rules that keep it honest (Fable design c96124d0 sections 3b and 5; S1 67a6d16a):

* Every item is checked to be strict built-in JSON BEFORE any lookup, equality or digest, so a subclass
  or foreign object never runs its own hooks here.
* A claim joins a dispatch only on exact str equality of task_id, agent == worker and owner_session_id,
  run_id and agent_uuid == the dispatch's expected_responders[worker] labels. These are labels written by
  the lanes themselves, never proof of identity.
* More than one valid dispatch for one task makes every claim and release of that task
  ``claim_dispatch_ambiguous``: this module never picks a newest revision, so a superseded or revised
  dispatch cannot be revived through it (S1 already keeps only the newest).
* Copies of one dispatch_id with different content poison that id. Two different present claims, or a
  present claim older than a matching release, for one attempt_id give no attempt.
* An active claim past its lease with no release stays ``active`` and is listed in ``expired_unreleased``;
  no release or failure is invented for it.
* Done, status, ACK and reply events are not inputs and never change a state.
* Cancellation (KeyboardInterrupt, SystemExit, GeneratorExit) and unexpected errors propagate.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from tools.lane_profile_record import _utc
from tools.wd_composer_select import digest
from tools.wd_routing_dispatch import DISPATCH_FIELDS, RESPONDER_FIELDS, _strict_json
from tools.wd_routing_dispatch import SCHEMA as DISPATCH_SCHEMA
from tools.wd_task_router import ATTEMPT_SCHEMA, DISPATCH_AUTHORITY, _Stop, normalize_scope

RELEASE_STATUSES = ("done", "handoff", "blocked", "abandoned", "stale_lease")
CLAIM_LABELS = ("owner_session_id", "run_id", "agent_uuid")
_LABEL_OF = {"owner_session_id": "session_id", "run_id": "run_id", "agent_uuid": "agent_uuid"}
_HEX = frozenset("0123456789abcdef")

# Stable rejection reasons.
R_MALFORMED = "malformed"
R_DISPATCH_CONFLICT = "dispatch_conflict"
R_AMBIGUOUS = "claim_dispatch_ambiguous"
R_UNBOUND = "no_matching_dispatch"
R_NOT_WRITE = "claim_not_write"
R_SCOPE = "scope_exceeds_dispatch"
R_BEFORE = "before_dispatch"
R_FUTURE = "future_dated"
R_CONFLICT = "attempt_conflict"
R_INCONSISTENT = "claim_release_inconsistent"
R_RECEIPT = "push_receipt_source_unaccepted"
R_ACCEPTANCE = "acceptance_source_absent"


class _Reject(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _text(value: Any) -> bool:
    return type(value) is str and bool(value)


def _hex(value: Any, length: int) -> bool:
    return type(value) is str and len(value) == length and set(value) <= _HEX


def _stamp(value: Any) -> datetime | None:
    return _utc(value) if type(value) is str else None


def _rejection(source: str, index: int | None, ref: Any, reason: str) -> dict:
    return {"source": source, "index": index, "ref": ref if _text(ref) else None, "reason": reason}


def _dispatch_ok(record: dict) -> bool:
    if set(record) != set(DISPATCH_FIELDS):
        return False
    responders = record["expected_responders"]
    worker = record["worker"]
    return (record["schema"] == DISPATCH_SCHEMA and record["requester"] == DISPATCH_AUTHORITY
            and all(_text(record[f]) for f in ("dispatch_id", "task_id", "revision", "worker"))
            and _hex(record["dispatch_key"], 64) and _hex(record["input_digest"], 64)
            and _hex(record["request_digest"], 64) and _stamp(record["dispatched_utc"]) is not None
            and type(record["scope"]) is list and bool(record["scope"])
            and type(responders) is dict and list(responders) == [worker]
            and type(responders[worker]) is dict and set(responders[worker]) == set(RESPONDER_FIELDS)
            and all(_text(responders[worker][f]) for f in RESPONDER_FIELDS))


def _inside(entry: str, scope: list[str]) -> bool:
    def kind(path: str) -> str:
        return path if ":" in path else "repo:" + path
    return any(kind(entry) == kind(d) or kind(entry).startswith(kind(d).rstrip("/") + "/") for d in scope)


def _labels(record: dict) -> tuple:
    return tuple(record.get(name) for name in ("task_id", "agent", *CLAIM_LABELS))


def _match(record: dict, by_task: dict) -> dict:
    """The one dispatch a claim or release record binds to, or _Reject."""
    if not all(_text(value) for value in _labels(record)):
        raise _Reject(R_MALFORMED)
    candidates = by_task.get(record["task_id"], [])
    if len(candidates) > 1:
        raise _Reject(R_AMBIGUOUS)
    if not candidates:
        raise _Reject(R_UNBOUND)
    dispatch = candidates[0]
    binding = dispatch["expected_responders"][dispatch["worker"]]
    if record["agent"] != dispatch["worker"] or any(record[name] != binding[_LABEL_OF[name]] for name in CLAIM_LABELS):
        raise _Reject(R_UNBOUND)
    return dispatch


def _claim_view(record: dict, dispatch: dict, now: datetime) -> dict:
    """Checks shared by present claims and release records; returns the attempt fields."""
    if record.get("mode") != "write":
        raise _Reject(R_NOT_WRITE)
    try:
        scope = normalize_scope(record.get("write_scope"))
    except _Stop:
        raise _Reject(R_MALFORMED) from None
    if not all(_inside(entry, dispatch["scope"]) for entry in scope):
        raise _Reject(R_SCOPE)
    claimed, lease = _stamp(record.get("claimed_at_utc")), _stamp(record.get("claim_lease_expires_utc"))
    if claimed is None or lease is None:
        raise _Reject(R_MALFORMED)
    if claimed > now:
        raise _Reject(R_FUTURE)
    if claimed < _utc(dispatch["dispatched_utc"]):
        raise _Reject(R_BEFORE)
    attempt_id = digest({"dispatch_id": dispatch["dispatch_id"], "worker": dispatch["worker"],
                         "owner_session_id": record["owner_session_id"]})
    return {"attempt_id": attempt_id, "dispatch_key": dispatch["dispatch_key"], "task_id": dispatch["task_id"],
            "worker": dispatch["worker"], "scope": scope, "claimed": claimed, "lease": lease}


def _strict_items(items: list, source: str, rejected: list) -> list[tuple[int, dict]]:
    kept = []
    for index, item in enumerate(items):
        if type(item) is not dict or not _strict_json(item) or digest(item) is None:
            rejected.append(_rejection(source, index, None, R_MALFORMED))
        else:
            kept.append((index, item))
    return kept


def attempts(dispatches: Any, claims: Any, releases: Any, push_receipts: Any, acceptances: Any,
             now: Any) -> dict:
    """Attempt records for the caller's evidence at the caller's explicit, offset-aware ``now``."""
    if type(now) is not datetime or now.utcoffset() is None:
        raise ValueError("now must be an offset-aware datetime")
    inputs = (dispatches, claims, releases, push_receipts, acceptances)
    if any(type(value) is not list for value in inputs):
        raise ValueError("every input must be a list")
    now = now.astimezone(timezone.utc)
    rejected: list[dict] = []
    duplicates = 0

    # Dispatches: strict, closed, then per id: all copies identical or the whole id is poisoned.
    groups: dict[str, list[tuple[int, dict]]] = {}
    for index, record in _strict_items(dispatches, "dispatch", rejected):
        if not _dispatch_ok(record):
            rejected.append(_rejection("dispatch", index, record.get("dispatch_id"), R_MALFORMED))
            continue
        groups.setdefault(record["dispatch_id"], []).append((index, record))
    by_task: dict[str, list[dict]] = {}
    for dispatch_id in sorted(groups):
        copies = groups[dispatch_id]
        if len({digest(record) for _, record in copies}) != 1:
            rejected.append(_rejection("dispatch", None, dispatch_id, R_DISPATCH_CONFLICT))
            continue
        duplicates += len(copies) - 1
        by_task.setdefault(copies[0][1]["task_id"], []).append(copies[0][1])

    # Present claims and release records, each bound to exactly one dispatch.
    present: dict[str, list[tuple[dict, dict]]] = {}
    released: dict[str, list[tuple[dict, datetime]]] = {}
    seen: set = set()
    for source, items in (("claim", claims), ("release", releases)):
        for index, record in _strict_items(items, source, rejected):
            key = (source, digest(record))
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
            try:
                view = _claim_view(record, _match(record, by_task), now)
                if source == "release":
                    status, at = record.get("release_status"), _stamp(record.get("released_at_utc"))
                    if type(status) is not str or status not in RELEASE_STATUSES or at is None:
                        raise _Reject(R_MALFORMED)
                    if at > now:
                        raise _Reject(R_FUTURE)
                    released.setdefault(view["attempt_id"], []).append((view, at))
                else:
                    present.setdefault(view["attempt_id"], []).append((view, record))
            except _Reject as reject:
                rejected.append(_rejection(source, index, record.get("task_id"), reject.reason))

    produced: list[dict] = []
    expired: list[str] = []
    for attempt_id in sorted(set(present) | set(released)):
        claims_here, releases_here = present.get(attempt_id, []), released.get(attempt_id, [])
        if len(claims_here) > 1:
            rejected.append(_rejection("claim", None, attempt_id, R_CONFLICT))
            continue
        if claims_here:
            view = claims_here[0][0]
            if any(view["claimed"] <= at for _, at in releases_here):
                rejected.append(_rejection("claim", None, attempt_id, R_INCONSISTENT))
                continue
            state = "active"
            if view["lease"] <= now:
                expired.append(attempt_id)
        else:
            view = max(releases_here, key=lambda pair: (pair[1], digest(pair[0]["scope"])))[0]
            state = "released"
        produced.append({"schema": ATTEMPT_SCHEMA, "attempt_id": attempt_id, "dispatch_key": view["dispatch_key"],
                         "task_id": view["task_id"], "worker": view["worker"], "scope": view["scope"],
                         "state": state, "lease_expires_utc": view["lease"].isoformat(), "artifacts": []})

    # No accepted source exists yet: strictly checked, then refused one by one.
    for source, items, reason in (("push_receipt", push_receipts, R_RECEIPT), ("acceptance", acceptances, R_ACCEPTANCE)):
        for index, record in _strict_items(items, source, rejected):
            rejected.append(_rejection(source, index, record.get("attempt_id"), reason))

    return {"attempts": produced, "expired_unreleased": expired,
            "rejected": sorted(rejected, key=lambda r: (r["source"], r["index"] is None, r["index"] or 0,
                                                        r["ref"] or "", r["reason"])),
            "duplicates_ignored": duplicates}
