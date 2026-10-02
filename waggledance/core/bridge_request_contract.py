"""Request identity and reply binding shared by bridge routing/reporting.

Legacy unversioned requests retain their historical single-request behavior.
Explicit IDs or correlation metadata never fall back to task/time matching.
"""
from __future__ import annotations

from datetime import datetime
import json
import math
import re
from typing import Any, Mapping

_CONFLICT = object()
_EXACT_FLOAT_LIMIT = 2.0 ** 53


def _same(left: Any, right: Any) -> bool:
    """Type-strict structural equality: true is not 1, 1 is not 1.0, NaN is never the same."""
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(_same(left[key], right[key]) for key in left)
    if type(left) is list:
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    if type(left) is float and (math.isnan(left) or math.isnan(right)):
        return False
    return left == right


def _differs(left: Any, right: Any) -> bool:
    """Identity, digest and context values (PowerShell Test-BridgeContractValuesDiffer): two exact strings compare
    ordinally; two absent values are not different; ANY other pair (bool, number, list, object) is different."""
    if type(left) is str and type(right) is str:
        return left != right
    return not (left is None and right is None)


def _correlation_differs(left: Any, right: Any) -> bool:
    """nonce/token/task_revision (PowerShell Test-BridgeContractCorrelationDiffers): strings as _differs; otherwise
    only the SAME exact bool, int or float with an equal value matches, a float only when finite and below 2**53.
    Exact Python ints are compared exactly (no clamp); lists, objects and type changes (true vs 1, 3 vs 3.0) differ."""
    if type(left) is str or type(right) is str or left is None or right is None:
        return _differs(left, right)
    if type(left) is not type(right) or type(left) not in (bool, int, float):
        return True
    if type(left) is float and not all(math.isfinite(v) and abs(v) < _EXACT_FLOAT_LIMIT for v in (left, right)):
        return True
    return left != right


def _blank(value: Any) -> bool:
    """Only an exact empty string is a documented blank label (Write-AgentEvent omits it from the reply context)."""
    return type(value) is str and value == ""


def field(event: Mapping[str, Any], key: str) -> Any:
    payload = event.get("payload")
    nested = payload.get(key) if isinstance(payload, Mapping) else None
    direct = event.get(key)
    if direct is not None and nested is not None and not _same(direct, nested):
        return _CONFLICT
    return direct if direct is not None else nested


def timestamp(value: Any) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00").replace("z", "+00:00"))
        return result if result.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def request_is_bound(request: Mapping[str, Any]) -> bool:
    return any(field(request, key) is not None for key in (
        "request_id", "nonce", "token", "task_revision", "expected_responders",
    ))


def reply_follows_request(
    request: Mapping[str, Any], reply: Mapping[str, Any], *,
    request_position: int | None = None, reply_position: int | None = None,
) -> bool:
    """Require strict UTC time and, when available, canonical append order.

    Positions are supplied by the reader, never trusted event payload fields.
    A caller without a log can check time only; log consumers must supply both.
    """
    if request_position is not None or reply_position is not None:
        if (type(request_position) is not int or type(reply_position) is not int
                or request_position < 0 or reply_position <= request_position):
            return False
    sent, answered = timestamp(request.get("ts_utc")), timestamp(reply.get("ts_utc"))
    return sent is not None and answered is not None and answered > sent


def terminal_status_negated(status: str) -> bool:
    return bool(set(re.split(r"[^a-z0-9]+", status.lower())) & {
        "not", "no", "undone", "incomplete", "unfinished", "unresolved",
        "unverified", "unmerged", "failed", "pending", "queued", "running",
        "processing",
    })


def reply_matches_request(
    request: Mapping[str, Any], reply: Mapping[str, Any], target: str,
    *, requester_closure: bool = False, ambiguous_legacy: bool = False,
    require_explicit_correlation: bool = False,
    request_position: int | None = None, reply_position: int | None = None,
) -> bool:
    """Check correlation only; callers must also require a substantive closure."""
    if request.get("request_binding_conflict"):
        return False
    requester = request.get("agent")
    if type(requester) is not str:
        return False
    if _differs(reply.get("agent"), requester if requester_closure else target):
        return False
    if _differs(reply.get("task_id"), request.get("task_id")):
        return False
    sent = timestamp(request.get("ts_utc"))
    if not reply_follows_request(request, reply, request_position=request_position,
                                 reply_position=reply_position):
        return False
    to = reply.get("to")
    if to is not None and type(to) is not str:
        return False
    recipients = {s.strip() for s in (to or "").split(",") if s.strip()}
    recipient = target if requester_closure else requester
    if recipients and recipient not in recipients:
        return False
    rid = field(request, "request_id")
    if rid is not None:
        if type(rid) is not str or not rid or _differs(field(reply, "in_reply_to_request_id"), rid):
            return False
        if recipient not in recipients:
            return False
        digest = field(request, "request_digest")
        if digest is not None and _differs(field(reply, "in_reply_to_request_digest"), digest):
            return False
        context = field(reply, "in_reply_to_requester")
        if not isinstance(context, Mapping):
            return False
        for key in ("agent", "agent_uuid", "session_id", "run_id"):
            expected_label = request.get(key)
            if expected_label is not None and not _blank(expected_label) and _differs(context.get(key), expected_label):
                return False
    elif field(reply, "in_reply_to_request_id") is not None:
        return False
    reference = field(reply, "request_ts_utc")
    if reference is not None and timestamp(reference) != sent:
        return False
    correlated = reference is not None
    for key in ("nonce", "token", "task_revision"):
        expected = field(request, key)
        actual = field(reply, key)
        if expected is _CONFLICT:
            return False                     # the request's own value disagrees (top vs payload): no single correlation
        if expected is not None:
            # IDs bind a reply without copying arbitrary payload fields, but an
            # explicitly supplied wrong revision/nonce must never be accepted.
            if (rid is None or actual is not None) and _correlation_differs(actual, expected):
                return False
            correlated = True
    if rid is None and (ambiguous_legacy or require_explicit_correlation) and not correlated:
        return False
    if requester_closure:
        expected_identity = request
    else:
        expected = field(request, "expected_responders")
        if expected is not None and not isinstance(expected, Mapping):
            return False
        if expected is not None and target not in expected:
            return False
        expected_identity = expected.get(target, {}) if expected else {}
    if not isinstance(expected_identity, Mapping):
        return False
    for key in ("agent_uuid", "session_id", "run_id"):
        if not requester_closure and field(request, "expected_responders") is not None and (
            not isinstance(expected_identity.get(key), str) or not expected_identity[key]
        ):
            return False
        value = expected_identity.get(key)
        if value is not None and not _blank(value) and _differs(reply.get(key), value):
            return False
    return True


def request_key(request: Mapping[str, Any], target: str) -> tuple[str, ...]:
    rid = field(request, "request_id")
    if rid:
        return ("id", str(request.get("agent", "")), str(rid), target)
    return ("legacy", str(request.get("agent", "")), str(request.get("task_id", "")),
            str(request.get("status", "")), target)


def request_content(request: Mapping[str, Any]) -> str:
    """Stable retry identity; transport timestamps/PIDs do not change intent."""
    return json.dumps({key: request.get(key) for key in (
        "request_id", "agent", "agent_uuid", "session_id", "run_id", "task_id",
        "to", "type", "status", "message", "payload", "expected_responders",
    )}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
