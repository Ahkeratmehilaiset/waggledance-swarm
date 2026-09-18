"""Request identity and reply binding shared by bridge routing/reporting.

Legacy unversioned requests retain their historical single-request behavior.
Explicit IDs or correlation metadata never fall back to task/time matching.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

_CONFLICT = object()


def field(event: Mapping[str, Any], key: str) -> Any:
    payload = event.get("payload")
    nested = payload.get(key) if isinstance(payload, Mapping) else None
    direct = event.get(key)
    if direct is not None and nested is not None and direct != nested:
        return _CONFLICT
    return direct if direct is not None else nested


def timestamp(value: Any) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result if result.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def request_is_bound(request: Mapping[str, Any]) -> bool:
    return any(field(request, key) is not None for key in (
        "request_id", "nonce", "token", "task_revision", "expected_responders",
    ))


def reply_matches_request(
    request: Mapping[str, Any], reply: Mapping[str, Any], target: str,
    *, requester_closure: bool = False, ambiguous_legacy: bool = False,
) -> bool:
    """Check correlation only; callers must also require a substantive closure."""
    if request.get("request_binding_conflict"):
        return False
    requester = str(request.get("agent", ""))
    if reply.get("agent") != (requester if requester_closure else target):
        return False
    if reply.get("task_id", "") != request.get("task_id", ""):
        return False
    sent, answered = timestamp(request.get("ts_utc")), timestamp(reply.get("ts_utc"))
    if sent is None or answered is None or answered <= sent:
        return False
    recipients = {s.strip() for s in str(reply.get("to", "")).split(",") if s.strip()}
    recipient = target if requester_closure else requester
    if recipients and recipient not in recipients:
        return False
    rid = field(request, "request_id")
    if rid is not None:
        if not isinstance(rid, str) or not rid or field(reply, "in_reply_to_request_id") != rid:
            return False
        if recipient not in recipients:
            return False
        digest = field(request, "request_digest")
        if digest is not None and field(reply, "in_reply_to_request_digest") != digest:
            return False
        context = field(reply, "in_reply_to_requester")
        if not isinstance(context, Mapping):
            return False
        for key in ("agent", "agent_uuid", "session_id", "run_id"):
            if request.get(key) and context.get(key) != request[key]:
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
        if expected is not None:
            # IDs bind a reply without copying arbitrary payload fields, but an
            # explicitly supplied wrong revision/nonce must never be accepted.
            if (rid is None or actual is not None) and actual != expected:
                return False
            correlated = True
    if rid is None and ambiguous_legacy and not correlated:
        return False
    if requester_closure:
        expected_identity = request
    else:
        expected = field(request, "expected_responders")
        if expected is not None and not isinstance(expected, Mapping):
            return False
        if expected and target not in expected:
            return False
        expected_identity = expected.get(target, {}) if expected else {}
    if not isinstance(expected_identity, Mapping):
        return False
    for key in ("agent_uuid", "session_id", "run_id"):
        if expected_identity.get(key) and reply.get(key) != expected_identity[key]:
            return False
    return True


def request_key(request: Mapping[str, Any], target: str) -> tuple[str, ...]:
    rid = field(request, "request_id")
    if rid:
        return ("id", str(request.get("agent", "")), str(rid), target)
    return ("legacy", str(request.get("agent", "")), str(request.get("task_id", "")),
            str(request.get("status", "")), target)
