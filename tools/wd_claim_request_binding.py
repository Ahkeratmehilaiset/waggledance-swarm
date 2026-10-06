# SPDX-License-Identifier: BUSL-1.1
"""F26 P1a: pure, DORMANT acquisition request-binding validator and refresh rule (no caller is wired).

``claim_request_binding`` returns the closed ``wd.claim-request-binding.v1`` record that a future claim writer
may store for a claim acquired for one authority ``wake_request``, or raises ``BindingRefused(reason)``:

* ``{schema, request_id, request_digest, task_revision}`` and nothing else; ``request_digest`` is the STORED
  digest copied verbatim. It is never recomputed: the stored digest depends on the PowerShell version that
  wrote it (5.1 escapes ' < > & as \\u0027.., pwsh 7 and Python do not), so recompute-to-verify would refuse
  valid cross-shell requests. Old requests are never re-hashed.
* exact request id, task, recipient (``to`` is exactly the claiming agent), revision and the claiming agent's
  own ``expected_responders`` labels; a field present both top-level and in ``payload`` must agree (the
  Bridge contract treats a disagreement as an invalid binding). type, agent, to, request_id and task_id are
  read from the top level only, as S1 does; contract fields (task_revision, request_digest, ...) keep the
  payload fallback. A control's observed_utc must parse as an offset-aware time representable in UTC (S6).
* the injected ``canonical_lookup(request_id)`` port must return every canonical copy of the request; zero is
  ``binding_request_absent`` and a copy whose canonical content or stored digest differs is
  ``binding_request_conflict``. Transport fields (ts_utc, pid, cwd, ...) are not canonical and not compared.
* the injected ``current_controls(task_id)`` port must return the explicit ``wd.routing-task-control.v1``
  records of the task; absence, an unreadable record or a port that cannot answer (``UNKNOWN``) stays
  ``binding_control_unknown`` and is never read as live; a cancelled or different current request refuses.

``check_refresh(stored, presented)`` is the refresh rule a future writer applies on a same-owner refresh: both
absent is fine (None), a bound claim refreshed without the binding, a binding added to an unbound claim
(retrofit) and any changed field are refused; an accepted refresh keeps the STORED binding verbatim.

Hook-free and fail-closed: every caller and port value is validated as a tree of EXACT built-ins (dict with
exact str keys, list, str, int, bool, finite float, None), acyclic and bounded, BEFORE any comparison, so no
foreign ``__eq__``, ``__hash__``, ``__bool__``, ``__iter__``, ``get`` or ``keys`` ever runs. Correlation only:
the binding grants no authority, acceptance or permission, and neither a caller label nor a digest match sets
``identity_verified`` (provenance stays with the per-session owner token). No file, clock, environment,
process, network or shared-reader call is made here; the ports are the only inputs.
"""
from __future__ import annotations

from datetime import datetime as _Instant, timezone   # parsing only: no clock is ever read
import math
import re
from typing import Any, Callable

BINDING_SCHEMA = "wd.claim-request-binding.v1"
BINDING_FIELDS = ("schema", "request_id", "request_digest", "task_revision")
CONTROL_SCHEMA = "wd.routing-task-control.v1"
CONTROL_FIELDS = ("schema", "task_id", "request_id", "request_digest", "state", "observed_utc")
CONTROL_STATES = ("live", "cancelled")
REQUEST_TYPE = "wake_request"
DISPATCH_AUTHORITY = "codex-lead-1"
LABEL_FIELDS = ("agent_uuid", "session_id", "run_id")
# Read from the top level only, exactly as S1 dispatches() does; never from the payload (RCO1 P1a-2).
IDENTITY_FIELDS = ("type", "agent", "to", "request_id", "task_id")
# The Bridge request content identity (BridgeRequestContract Get-BridgeRequestContent): absent == null.
CONTENT_FIELDS = ("request_id", "agent", "agent_uuid", "session_id", "run_id", "task_id", "to", "type", "status",
                  "message", "payload", "expected_responders")
MAX_DEPTH = 32
MAX_NODES = 100_000
_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}", re.ASCII)
_LANE = re.compile(r"[a-z][a-z0-9_-]{1,32}", re.ASCII)
_DIGEST = re.compile(r"[0-9a-f]{64}", re.ASCII)
_STAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,7})?(Z|[+-][0-9]{2}:[0-9]{2})",
                    re.ASCII)


class _Unknown:
    __slots__ = ()

    def __repr__(self) -> str:
        return "UNKNOWN"


UNKNOWN = _Unknown()   # a port that cannot answer returns this; it is never read as "nothing newer"

# Stable refusal reasons.
R_CALLER = "binding_caller_invalid"
R_MALFORMED = "binding_request_malformed"
R_FIELD_CONFLICT = "binding_field_conflict"
R_NOT_AUTHORITY = "binding_not_authority"
R_REQUEST_ID = "binding_request_id_invalid"
R_TASK = "binding_task_mismatch"
R_RECIPIENT = "binding_recipient_mismatch"
R_REVISION = "binding_revision_missing"
R_DIGEST = "binding_digest_invalid"
R_OWNER = "binding_owner_mismatch"
R_LOOKUP_UNKNOWN = "binding_lookup_unknown"
R_ABSENT = "binding_request_absent"
R_CONFLICT = "binding_request_conflict"
R_CONTROL_UNKNOWN = "binding_control_unknown"
R_CONTROL_CONFLICT = "binding_control_conflict"
R_CANCELLED = "binding_request_cancelled"
R_SUPERSEDED = "binding_request_superseded"
R_STORED = "binding_stored_malformed"
R_PRESENTED = "binding_presented_malformed"
R_DROPPED = "binding_refresh_dropped"
R_RETROFIT = "binding_refresh_retrofit"
R_CHANGED = "binding_refresh_changed"


class BindingRefused(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _refuse(reason: str) -> None:
    raise BindingRefused(reason)


def _strict(value: Any) -> bool:
    """True only for an acyclic, bounded tree of EXACT built-ins with exact str keys and finite floats.
    Reads only through type(), dict.items and list iteration of exact types: no hook on a foreign object."""
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


def _same(a: Any, b: Any) -> bool:
    """Structural equality of two values ALREADY accepted by ``_strict`` (so only exact built-ins are touched).
    Types must match exactly (1 != 1.0 != True) and -0.0 differs from 0.0, as their JSON text does."""
    if type(a) is not type(b):
        return False
    if type(a) is dict:
        if len(a) != len(b):
            return False
        for key, child in dict.items(a):
            if not dict.__contains__(b, key) or not _same(child, dict.__getitem__(b, key)):
                return False
        return True
    if type(a) is list:
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if type(a) is float:
        return a == b and math.copysign(1.0, a) == math.copysign(1.0, b)
    return a == b


def _get(record: Any, name: str) -> Any:
    """The value of ``name`` in a strict-validated exact dict (None when absent)."""
    return dict.get(record, name) if type(record) is dict else None


def _field(request: dict, name: str) -> Any:
    """Top-level value, else the ``payload`` value; both present and different is a refusal (Bridge contract).
    Identity fields (IDENTITY_FIELDS) are read from the top level ONLY, as S1 does (RCO1 P1a-2): a payload copy
    can never stand in for them, but a payload copy that contradicts them is still a field conflict."""
    direct, nested = _get(request, name), _get(_get(request, "payload"), name)
    if direct is not None and nested is not None and not _same(direct, nested):
        _refuse(R_FIELD_CONFLICT)
    if name in IDENTITY_FIELDS:
        return direct
    return direct if direct is not None else nested


def _stamp_ok(value: Any) -> bool:
    """The S6 control time rule (082bcbc4 _control_ok -> lane_profile_record._utc): an offset-aware ISO time
    representable in UTC; shape alone is not enough (RCO1 P1a-1: month 13, Feb 30, +99:99, range overflow)."""
    if type(value) is not str or not _STAMP.fullmatch(value):
        return False
    try:
        parsed = _Instant.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            return False
        parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return False
    return True


def _text(value: Any) -> bool:
    return type(value) is str and value != ""


def _labels_of(value: Any) -> dict | None:
    """Exactly the three label fields, each a non-empty exact str (value already strict-validated)."""
    if type(value) is not dict or len(value) != len(LABEL_FIELDS):
        return None
    for key, child in dict.items(value):
        if key not in LABEL_FIELDS or not _text(child):
            return None
    return value


def _content_same(a: dict, b: dict) -> bool:
    if not all(_same(_get(a, name), _get(b, name)) for name in CONTENT_FIELDS):
        return False
    # the stored digest is compared AS STORED (never recomputed), wherever the contract reads it from
    return _same(_field(a, "request_digest"), _field(b, "request_digest"))


def _control_of(record: Any, task_id: str) -> dict | None:
    if not _strict(record) or type(record) is not dict or len(record) != len(CONTROL_FIELDS):
        return None
    if any(not dict.__contains__(record, name) for name in CONTROL_FIELDS):
        return None
    if (_get(record, "schema") != CONTROL_SCHEMA or _get(record, "task_id") != task_id
            or not (_text(_get(record, "request_id")) and _REQUEST_ID.fullmatch(_get(record, "request_id")))
            or not (type(_get(record, "request_digest")) is str and _DIGEST.fullmatch(_get(record, "request_digest")))
            or _get(record, "state") not in CONTROL_STATES
            or not _stamp_ok(_get(record, "observed_utc"))):
        return None
    return record


def claim_request_binding(request: Any, *, task_id: Any, agent: Any, own_labels: Any,
                          canonical_lookup: Callable[[str], Any], current_controls: Callable[[str], Any]) -> dict:
    """The closed binding for a claim of ``agent`` on ``task_id`` acquired for ``request``; BindingRefused otherwise.
    Ports are called only after every caller value and the request passed strict validation."""
    if not (_text(task_id) and type(agent) is str and _LANE.fullmatch(agent) and _strict(own_labels)
            and _labels_of(own_labels) is not None and callable(canonical_lookup) and callable(current_controls)):
        _refuse(R_CALLER)
    if type(request) is not dict or not _strict(request):
        _refuse(R_MALFORMED)
    payload = _get(request, "payload")
    if payload is not None and type(payload) is not dict:
        _refuse(R_MALFORMED)
    if _field(request, "type") != REQUEST_TYPE:
        _refuse(R_MALFORMED)
    if _field(request, "agent") != DISPATCH_AUTHORITY:
        _refuse(R_NOT_AUTHORITY)
    request_id = _field(request, "request_id")
    if type(request_id) is not str or not _REQUEST_ID.fullmatch(request_id):
        _refuse(R_REQUEST_ID)
    if _field(request, "task_id") != task_id:
        _refuse(R_TASK)
    if _field(request, "to") != agent:
        _refuse(R_RECIPIENT)
    revision = _field(request, "task_revision")
    if not _text(revision):
        _refuse(R_REVISION)
    digest = _field(request, "request_digest")
    if type(digest) is not str or not _DIGEST.fullmatch(digest):
        _refuse(R_DIGEST)
    responders = _field(request, "expected_responders")
    labels = _labels_of(_get(responders, agent)) if type(responders) is dict else None
    if labels is None or not all(_same(_get(labels, name), _get(own_labels, name)) for name in LABEL_FIELDS):
        _refuse(R_OWNER)

    copies = canonical_lookup(request_id)
    if copies is UNKNOWN or type(copies) is not list or not _strict(copies):
        _refuse(R_LOOKUP_UNKNOWN)
    if not copies:
        _refuse(R_ABSENT)
    for copy in copies:
        if type(copy) is not dict:
            _refuse(R_LOOKUP_UNKNOWN)     # the port answered something else: unknown, never dismissed
        try:
            copy_id, same = _field(copy, "request_id"), _content_same(copy, request)
        except BindingRefused:
            _refuse(R_CONFLICT)           # a canonical copy with contradictory top-level/payload fields
        if copy_id != request_id:
            _refuse(R_LOOKUP_UNKNOWN)
        if not same:
            _refuse(R_CONFLICT)

    controls = current_controls(task_id)
    if controls is UNKNOWN or type(controls) is not list:
        _refuse(R_CONTROL_UNKNOWN)
    current = []
    for record in controls:
        control = _control_of(record, task_id)
        if control is None:
            _refuse(R_CONTROL_UNKNOWN)    # an unreadable control may be this task's cancellation
        if not any(_same(control, seen) for seen in current):
            current.append(control)
    if not current:
        _refuse(R_CONTROL_UNKNOWN)        # no explicit control: absence is never "still live"
    if len(current) != 1:
        _refuse(R_CONTROL_CONFLICT)
    control = current[0]
    if _get(control, "state") == "cancelled":
        _refuse(R_CANCELLED)
    if _get(control, "request_id") != request_id or _get(control, "request_digest") != digest:
        _refuse(R_SUPERSEDED)
    return {"schema": BINDING_SCHEMA, "request_id": request_id, "request_digest": digest, "task_revision": revision}


def validate_binding(value: Any) -> dict | None:
    """The closed binding itself when ``value`` is exactly one, else None (hook-free)."""
    if type(value) is not dict or not _strict(value) or len(value) != len(BINDING_FIELDS):
        return None
    if any(not dict.__contains__(value, name) for name in BINDING_FIELDS):
        return None
    if (_get(value, "schema") != BINDING_SCHEMA
            or not (type(_get(value, "request_id")) is str and _REQUEST_ID.fullmatch(_get(value, "request_id")))
            or not (type(_get(value, "request_digest")) is str and _DIGEST.fullmatch(_get(value, "request_digest")))
            or not _text(_get(value, "task_revision"))):
        return None
    return value


def check_refresh(stored: Any, presented: Any) -> dict | None:
    """The binding a same-owner refresh keeps: None when both are absent, the STORED binding when the presented
    one is identical in every field; BindingRefused for a dropped binding, a retrofit or any change."""
    if stored is not None and validate_binding(stored) is None:
        _refuse(R_STORED)
    if presented is not None and validate_binding(presented) is None:
        _refuse(R_PRESENTED)
    if stored is None and presented is None:
        return None
    if presented is None:
        _refuse(R_DROPPED)
    if stored is None:
        _refuse(R_RETROFIT)
    if not _same(stored, presented):
        _refuse(R_CHANGED)
    return stored
