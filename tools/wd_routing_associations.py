#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Claim-to-dispatch associations from acquisition-recorded requests (F26 S6 source preparation).

Pure and passive. ``associations(dispatches, acquisitions, controls, now)`` reads only the records the
caller passes (as parsed JSON) and the caller's explicit, offset-aware ``now``. It opens no file and reads
no clock, environment, queue, provider or process. It produces ``wd.routing-claim-association.v1`` records,
the S2 input of tools/wd_routing_attempts, by MECHANICAL correlation of caller-supplied stored fields only:
an association is not immutable provenance, not authentication, not acceptance and no live permission.

Output ``{"associations", "unlinked", "rejected", "coverage", "duplicates_ignored"}``.

Inputs:

* ``dispatches``: ``wd.routing-dispatch.v1`` records (S1, the canonical requests), checked with the S2
  rules: closed fields, a closed normalized scope, and any malformed or differing copy poisons its id.
* ``acquisitions``: claim-file and done-file objects. The ONLY link source is the closed nested object
  ``request_binding`` = ``{schema: wd.claim-request-binding.v1, request_id, request_digest, task_revision}``
  that a writer would record when the claim is acquired and keep through a ``-Force`` refresh. No writer
  records it today, so every current claim is unlinked ``no_recorded_request``: nothing is ever inferred
  from task, session, turn or time proximity.
* ``controls``: explicit task-control coverage ``wd.routing-task-control.v1`` = ``{schema, task_id,
  request_id, request_digest, state: live|cancelled, observed_utc}``: the caller's statement of which
  request is current for a task. Copies for one task must agree on request and state; a malformed or
  future-dated copy poisons the task (control_conflict).

An association is emitted only when the binding names exactly one supplied dispatch with equal request_id,
request_digest and revision (task_revision), equal task_id, agent == worker, owner_session_id, run_id and
agent_uuid equal to the dispatch's responder labels, a 64-hex owner_token_sha256, AND exactly one live
control for that task names the same request. Its ``basis`` is the closed ``acquisition_recorded_request_v1``.

Every acquisition ends in exactly one coverage bucket: ``duplicate`` (an exact repeat), ``rejected``
(malformed), ``unlinked`` (closed reason) or ``associated``. Unlinked reasons: no_recorded_request,
request_absent, request_mismatch, owner_mismatch, coverage_unknown, control_conflict, request_cancelled,
request_superseded (the control names another request) and linkage_conflict: one S2 claim identity
(task_id, agent, owner_session_id, owner_token_sha256) is associated only when EVERY supplied record of it
carries the identical binding and links; a missing, malformed or different binding of the same identity
poisons it, because S2 joins all of that identity's claims and releases through one association.

Unknown coverage stays unknown (RCO1, Lead 18:05Z): a refused control whose task_id, or a refused
acquisition whose S2 identity, cannot be read hook-free might be any task's cancellation or any identity's
other binding, so it proves nothing safe. Then NO association is emitted: every acquisition that would link
is unlinked as control_coverage_incomplete (checked first) or acquisition_coverage_incomplete, and
``coverage.complete`` is false. ``complete`` is true only when every refused control and acquisition is
attributable. Readable malformed or future controls still poison only their own task. Unknown is not a
failure and not authentication.

Every item is checked to be strict built-in JSON before any lookup, equality or digest; ids of refused
items are read only from exact dicts through ``dict.items`` (no foreign hook runs). Cancellation
(KeyboardInterrupt, SystemExit, GeneratorExit) and unexpected errors propagate.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from tools.wd_composer_select import digest
from tools.wd_routing_attempts import (ASSOCIATION_FIELDS, ASSOCIATION_SCHEMA, _dispatch_ok, _exact_id, _hex,
                                       _rejection, _stamp, _strict_items, _text)

BINDING_SCHEMA = "wd.claim-request-binding.v1"
BINDING_FIELDS = ("schema", "request_id", "request_digest", "task_revision")
CONTROL_SCHEMA = "wd.routing-task-control.v1"
CONTROL_FIELDS = ("schema", "task_id", "request_id", "request_digest", "state", "observed_utc")
CONTROL_STATES = ("live", "cancelled")
COVERAGE_SCHEMA = "wd.routing-association-coverage.v1"
BASIS = "acquisition_recorded_request_v1"
IDENTITY = ("task_id", "agent", "owner_session_id", "owner_token_sha256")
_LABEL_OF = {"owner_session_id": "session_id", "run_id": "run_id", "agent_uuid": "agent_uuid"}

# Stable rejection and unlinked reasons.
R_MALFORMED = "malformed"
R_FUTURE = "future_dated"
R_DISPATCH_CONFLICT = "dispatch_conflict"
U_NO_RECORD = "no_recorded_request"
U_ABSENT = "request_absent"
U_MISMATCH = "request_mismatch"
U_OWNER = "owner_mismatch"
U_COVERAGE = "coverage_unknown"
U_CONTROL_CONFLICT = "control_conflict"
U_CANCELLED = "request_cancelled"
U_SUPERSEDED = "request_superseded"
U_LINKAGE_CONFLICT = "linkage_conflict"
U_CONTROL_INCOMPLETE = "control_coverage_incomplete"
U_ACQUISITION_INCOMPLETE = "acquisition_coverage_incomplete"
_NO_BINDING, _BAD_BINDING = "no_binding", "bad_binding"


class _Unlinked(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _exact_text(item: Any, name: str) -> str | None:
    """Field ``name`` of an exact built-in dict when key and value are exact non-empty str, read only
    through dict.items (no lookup, equality or hook on a foreign object); None otherwise."""
    if type(item) is not dict:
        return None
    for key, value in dict.items(item):
        if type(key) is str and key == name:
            return value if _text(value) else None
    return None


def _identity(item: Any) -> tuple | None:
    parts = tuple(_exact_text(item, name) for name in IDENTITY)
    return parts if all(part is not None for part in parts) else None


def _binding(record: dict) -> Any:
    if "request_binding" not in record:
        return _NO_BINDING
    binding = record["request_binding"]
    if (type(binding) is dict and set(binding) == set(BINDING_FIELDS) and binding["schema"] == BINDING_SCHEMA
            and _text(binding["request_id"]) and _hex(binding["request_digest"], 64)
            and _text(binding["task_revision"])):
        return binding
    return _BAD_BINDING


def _control_ok(record: dict) -> bool:
    return (set(record) == set(CONTROL_FIELDS) and record["schema"] == CONTROL_SCHEMA
            and _text(record["task_id"]) and _text(record["request_id"]) and _hex(record["request_digest"], 64)
            and type(record["state"]) is str and record["state"] in CONTROL_STATES
            and _stamp(record["observed_utc"]) is not None)


def _link(record: dict, binding: dict, by_id: dict, control_of: dict, control_bad: set) -> dict:
    """The association this record's recorded binding yields, or _Unlinked."""
    dispatch = by_id.get(binding["request_id"])
    if dispatch is None:
        raise _Unlinked(U_ABSENT)
    if (binding["request_digest"], binding["task_revision"], record["task_id"]) != (
            dispatch["request_digest"], dispatch["revision"], dispatch["task_id"]):
        raise _Unlinked(U_MISMATCH)
    labels = dispatch["expected_responders"][dispatch["worker"]]
    if record["agent"] != dispatch["worker"] or any(record[name] != labels[_LABEL_OF[name]] for name in _LABEL_OF):
        raise _Unlinked(U_OWNER)
    task = dispatch["task_id"]
    if task in control_bad:
        raise _Unlinked(U_CONTROL_CONFLICT)
    control = control_of.get(task)
    if control is None:
        raise _Unlinked(U_COVERAGE)
    if (control["request_id"], control["request_digest"]) != (dispatch["dispatch_id"], dispatch["request_digest"]):
        raise _Unlinked(U_SUPERSEDED)
    if control["state"] != "live":
        raise _Unlinked(U_CANCELLED)
    return {"schema": ASSOCIATION_SCHEMA, "dispatch_id": dispatch["dispatch_id"],
            "request_digest": dispatch["request_digest"], "task_id": task, "worker": dispatch["worker"],
            "owner_session_id": record["owner_session_id"], "owner_token_sha256": record["owner_token_sha256"],
            "basis": BASIS}


def associations(dispatches: Any, acquisitions: Any, controls: Any, now: Any) -> dict:
    """Associations for the caller's evidence at the caller's explicit, offset-aware ``now``."""
    if type(now) is not datetime or now.utcoffset() is None:
        raise ValueError("now must be an offset-aware datetime")
    if any(type(value) is not list for value in (dispatches, acquisitions, controls)):
        raise ValueError("every input must be a list")
    try:
        now = now.astimezone(timezone.utc)
    except OverflowError:
        raise ValueError("now is outside the representable UTC range") from None
    rejected: list[dict] = []
    duplicates = 0

    # Dispatches (S2 rules): refused copies poison their exact id; copies of one id must be identical.
    kept = _strict_items(dispatches, "dispatch", rejected)
    strict = {index for index, _ in kept}
    poisoned = {_exact_id(item) for index, item in enumerate(dispatches) if index not in strict} - {None}
    groups: dict[str, list[dict]] = {}
    for index, record in kept:
        if not _dispatch_ok(record):
            rejected.append(_rejection("dispatch", index, record.get("dispatch_id"), R_MALFORMED))
            poisoned.add(_exact_id(record))
            continue
        groups.setdefault(record["dispatch_id"], []).append(record)
    by_id: dict[str, dict] = {}
    for dispatch_id in sorted(groups):
        copies = groups[dispatch_id]
        if dispatch_id in poisoned or len({digest(record) for record in copies}) != 1:
            rejected.append(_rejection("dispatch", None, dispatch_id, R_DISPATCH_CONFLICT))
            continue
        duplicates += len(copies) - 1
        by_id[dispatch_id] = copies[0]

    # Controls: per task one agreed (request, state); a refused or differing copy poisons the task.
    kept = _strict_items(controls, "control", rejected)
    strict = {index for index, _ in kept}
    refused_tasks = [_exact_text(item, "task_id") for index, item in enumerate(controls) if index not in strict]
    control_bad = set(refused_tasks) - {None}
    control_unattributed = None in refused_tasks   # a refused control that names no readable task
    control_groups: dict[str, list[dict]] = {}
    for index, record in kept:
        if not _control_ok(record):
            reason = R_MALFORMED
        elif _stamp(record["observed_utc"]) > now:
            reason = R_FUTURE
        else:
            control_groups.setdefault(record["task_id"], []).append(record)
            continue
        rejected.append(_rejection("control", index, record.get("task_id"), reason))
        task = _exact_text(record, "task_id")
        control_bad.add(task)
        if task is None:
            control_unattributed = True
    control_of: dict[str, dict] = {}
    for task in sorted(control_groups):
        copies = control_groups[task]
        if task in control_bad or len({(c["request_id"], c["request_digest"], c["state"]) for c in copies}) != 1:
            control_bad.add(task)
            continue
        duplicates += len(copies) - 1
        control_of[task] = copies[0]
    control_bad.discard(None)

    # Acquisitions: every binding marker per claim identity first, so one identity links all or nothing.
    kept = _strict_items(acquisitions, "acquisition", rejected)
    strict = {index for index, _ in kept}
    rejected_count = len(acquisitions) - len(kept)
    markers: dict[tuple, set] = {}
    acquisition_unattributed = False
    for index, item in enumerate(acquisitions):
        if index in strict:
            continue
        identity = _identity(item)
        if identity is None:
            acquisition_unattributed = True   # a refused acquisition of no readable identity
        else:
            markers.setdefault(identity, set()).add(_BAD_BINDING)
    seen: set = set()
    views = []
    acquisition_duplicates = 0
    for index, record in kept:
        record_digest = digest(record)
        if record_digest in seen:
            acquisition_duplicates += 1
            continue
        seen.add(record_digest)
        identity, binding = _identity(record), _binding(record)
        if identity is not None:
            markers.setdefault(identity, set()).add(binding if type(binding) is str else digest(binding))
        views.append((index, record, identity, binding))

    unlinked: list[dict] = []
    failed: set = set()
    linked: dict[tuple, list[tuple[int, dict]]] = {}
    for index, record, identity, binding in views:
        try:
            if binding is _NO_BINDING:
                raise _Unlinked(U_NO_RECORD)
            if (binding is _BAD_BINDING or identity is None or not _hex(record["owner_token_sha256"], 64)
                    or not all(_text(record.get(name)) for name in _LABEL_OF)):
                rejected.append(_rejection("acquisition", index, record.get("task_id"), R_MALFORMED))
                rejected_count += 1
                failed.add(identity)
                if identity is None:
                    acquisition_unattributed = True
                continue
            if len(markers[identity]) != 1:
                raise _Unlinked(U_LINKAGE_CONFLICT)
            linked.setdefault(identity, []).append((index, _link(record, binding, by_id, control_of, control_bad)))
        except _Unlinked as unlink:
            unlinked.append(_rejection("acquisition", index, record.get("task_id"), unlink.reason))
            failed.add(identity)

    incomplete = (U_CONTROL_INCOMPLETE if control_unattributed
                  else U_ACQUISITION_INCOMPLETE if acquisition_unattributed else None)
    produced: list[dict] = []
    associated = 0
    for identity in sorted(linked):
        rows = linked[identity]
        if identity in failed or len({digest(association) for _, association in rows}) != 1:
            unlinked.extend(_rejection("acquisition", index, identity[0], U_LINKAGE_CONFLICT) for index, _ in rows)
            continue
        if incomplete is not None:
            # Unattributable refused evidence may be this identity's cancellation or other binding.
            unlinked.extend(_rejection("acquisition", index, identity[0], incomplete) for index, _ in rows)
            continue
        produced.append(rows[0][1])
        associated += len(rows)
    duplicates += acquisition_duplicates

    coverage = {"schema": COVERAGE_SCHEMA, "acquisitions": len(acquisitions), "associated": associated,
                "unlinked": len(unlinked), "rejected": rejected_count, "duplicates": acquisition_duplicates,
                "complete": incomplete is None}
    order = (lambda r: (r["source"], r["index"] is None, r["index"] or 0, r["ref"] or "", r["reason"]))
    return {"associations": produced, "unlinked": sorted(unlinked, key=order), "rejected": sorted(rejected, key=order),
            "coverage": coverage, "duplicates_ignored": duplicates}
