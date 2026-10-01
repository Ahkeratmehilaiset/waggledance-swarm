"""Pure classifier of the structured task control declaration (slice 1: preparation only, no caller).

Contract: fable-5 CONTRACT-HOSTILE.md (sha256 37F02065...) and its model, with the shared S-A helpers IMPORTED
from tools/wd_routing_cancellations.py (never copied). Nothing here reads a file, the clock, the environment or a
process, and nothing calls this module yet: it is not wired into S-A, any adapter, writer or live caller.

classify_declarations(rows, identity) -> list of (kind, target_request_id), one per row, where

* rows is the exact list S-A parsed from the S-C-identified prefix, in VERIFIED prefix order. More than S-A
  MAX_EVENTS rows raises ValueError BEFORE any row is read (callers treat it as incomplete).
* identity is the externally supplied CLOSED authority identity {agent, agent_uuid, session_id, run_id}: exact
  dict, exactly those keys, exact non-empty str values, agent exactly the S-A AUTHORITY. Otherwise every
  declaration row is unknown. The identity never comes from the log.
* kind is one of v1, unknown, none, cancel, hold, resume. There is no clear and no permission.

Row decision, first match: a row that S-A _strict refuses (subclass, cycle, depth, node count, non-finite float)
is unknown; a row with neither payload.control nor an envelope control key is v1 (every historical row keeps the
S-A v1 rules); if ANY row is refused, every declaring row is unknown (its request id cannot be read hook-free, so
uniqueness and targets are unprovable); identity mismatch or an envelope control key is unknown; the declaration must be the closed
{schema, task_id, request_id, state, target} with schema wd.task-control-declaration.v1, task_id and request_id
equal to the envelope (S-A id charset for the request id), state in STATES, envelope request_digest lowercase
hex64, else unknown; the envelope (type, status) must be (wake_request, assigned|request) and the S-A _signals
control signal must be silent on the row with ONLY message and payload.control removed (so quiet keys exempt
token-shaped values only, as in S-A), else unknown; the row request id must occur at no other position, else
unknown; none needs target null; cancel, hold and resume need a closed {request_id, request_digest} target that
occurs at exactly ONE strictly earlier position, on a row with exact agent AUTHORITY, agent_uuid equal to the
identity, the same task, a request pair and the identical STORED request_digest (string compare; a digest is
never recomputed: the pinned canonical JSON differs between PowerShell 5.1 and 7).

Limits: per row only; aggregation across rows (cancel against another outcome, hold then resume) and every
global input stay with S-A and the adapter. A target from an earlier Lead session matches by agent label and
profile agent_uuid only. A declaration correlates its own row; it never authorizes anything.
"""
from __future__ import annotations

from typing import Any

from tools import wd_routing_cancellations as _sa

SCHEMA = "wd.task-control-declaration.v1"
STATES = ("none", "cancel", "hold", "resume")
DECL_FIELDS = ("schema", "task_id", "request_id", "state", "target")
TARGET_FIELDS = ("request_id", "request_digest")
IDENTITY_FIELDS = ("agent", "agent_uuid", "session_id", "run_id")
REQUEST_PAIRS = frozenset({("wake_request", "assigned"), ("wake_request", "request")})
V1 = "v1"
UNKNOWN = "unknown"


def _id(value: Any) -> bool:
    return type(value) is str and _sa._ID.fullmatch(value) is not None


def _pair(row: Any) -> tuple | None:
    kind, status = _sa._get(row, "type"), _sa._get(row, "status")
    return (kind, status) if type(kind) is str and type(status) is str else None


def _identity_valid(identity: Any) -> bool:
    return (_sa._strict(identity) and _sa._closed(identity, IDENTITY_FIELDS)
            and all(_sa._text(dict.get(identity, name)) for name in IDENTITY_FIELDS)
            and dict.get(identity, "agent") == _sa.AUTHORITY)


def _same_identity(row: dict, identity: dict) -> bool:
    return all(type(_sa._get(row, name)) is str and _sa._get(row, name) == dict.get(identity, name)
               for name in IDENTITY_FIELDS)


def _other_control(row: dict) -> bool:
    """S-A control signal on the row without its free-text message and without its declaration."""
    stripped = {name: value for name, value in dict.items(row) if name != "message"}
    stripped["payload"] = {name: value for name, value in dict.items(dict.get(row, "payload")) if name != "control"}
    return _sa._signals(stripped)[0]


def classify_declarations(rows: Any, identity: Any) -> list:
    """Per-row (kind, target_request_id) for rows in verified prefix order; see the module doc."""
    if type(rows) is not list:
        raise TypeError("rows must be the exact list of parsed prefix rows")
    if len(rows) > _sa.MAX_EVENTS:
        raise ValueError("row count over policy")                 # before any row is read
    authority = identity if _identity_valid(identity) else None
    strict = [type(row) is dict and _sa._strict(row) for row in rows]
    damaged = not all(strict)
    positions: dict = {}
    for index, row in enumerate(rows):
        request_id = _sa._get(row, "request_id") if strict[index] else None
        if type(request_id) is str:
            positions.setdefault(request_id, []).append(index)
    out = []
    for index, row in enumerate(rows):
        if not strict[index]:
            out.append((UNKNOWN, None))
            continue
        payload = dict.get(row, "payload")
        if not (dict.__contains__(row, "control") or (type(payload) is dict and dict.__contains__(payload, "control"))):
            out.append((V1, None))
            continue
        if damaged:
            out.append((UNKNOWN, None))       # a non-strict row's request id is unreadable hook-free (RCO2 5b799 D)
            continue
        out.append(_classify(rows, strict, positions, index, row, authority))
    return out


def _classify(rows: list, strict: list, positions: dict, index: int, row: dict, identity: dict | None) -> tuple:
    if identity is None or dict.__contains__(row, "control") or not _same_identity(row, identity):
        return UNKNOWN, None
    declaration = dict.get(dict.get(row, "payload"), "control")
    task, request_id = _sa._get(row, "task_id"), _sa._get(row, "request_id")
    if not (_sa._closed(declaration, DECL_FIELDS)
            and type(dict.get(declaration, "schema")) is str and dict.get(declaration, "schema") == SCHEMA
            and _sa._text(task) and _id(request_id)
            and type(dict.get(declaration, "task_id")) is str and dict.get(declaration, "task_id") == task
            and type(dict.get(declaration, "request_id")) is str and dict.get(declaration, "request_id") == request_id
            and type(dict.get(declaration, "state")) is str and dict.get(declaration, "state") in STATES
            and _sa._hex64(_sa._get(row, "request_digest"))):
        return UNKNOWN, None
    if _pair(row) not in REQUEST_PAIRS or _other_control(row):
        return UNKNOWN, None
    if len(positions.get(request_id, ())) != 1:
        return UNKNOWN, None
    state, target = dict.get(declaration, "state"), dict.get(declaration, "target")
    if state == "none":
        return ("none", None) if target is None else (UNKNOWN, None)
    if not (_sa._closed(target, TARGET_FIELDS) and _id(dict.get(target, "request_id"))
            and _sa._hex64(dict.get(target, "request_digest"))):
        return UNKNOWN, None
    target_id = dict.get(target, "request_id")
    hits = positions.get(target_id, [])
    if len(hits) != 1 or hits[0] >= index or not strict[hits[0]]:
        return UNKNOWN, None
    earlier = rows[hits[0]]
    if not (type(_sa._get(earlier, "agent")) is str and _sa._get(earlier, "agent") == _sa.AUTHORITY
            and type(_sa._get(earlier, "agent_uuid")) is str and _sa._get(earlier, "agent_uuid") == dict.get(identity, "agent_uuid")
            and type(_sa._get(earlier, "task_id")) is str and _sa._get(earlier, "task_id") == task
            and _pair(earlier) in REQUEST_PAIRS
            and type(_sa._get(earlier, "request_digest")) is str
            and _sa._get(earlier, "request_digest") == dict.get(target, "request_digest")):
        return UNKNOWN, None
    return state, target_id
