"""Pure reference classifier for bridge wake routing (contract wd.wake-class.v1).

Specification: docs/architecture/BRIDGE_WAKE_CLASS_CONTRACT_V1.md
Golden vectors: tests/fixtures/wake_class/v1/vectors.json

classify(event, target_agent) decides whether one decoded bridge event should
wake target_agent's inbox. It is routing only: waking grants no authority,
validates no binding and accepts no result. A ``bound_reply`` class means the
event *claims* ``in_reply_to_request_id``; the binding still has to be
validated by the request contract. Every exit that does not wake is exact and
demonstrably non-actionable; anything malformed, conflicting or unknown is
``ambiguous`` and wakes (ambiguity drains, control is never suppressed).

The module is deliberately stdlib-only and free of I/O so that it can serve as
the executable form of the contract for any consumer or port.
"""
from __future__ import annotations

import json
import re
from typing import Any

CONTRACT = 'wd.wake-class.v1'

CLASSES = ('request', 'bound_reply', 'control', 'notice', 'noise',
           'not_addressed', 'ambiguous')
NON_WAKING_CLASSES = frozenset({'notice', 'noise', 'not_addressed'})

# Reason codes, in precedence order of the step that emits them.
REASONS = (
    'malformed_event',
    'self_emission',
    'no_target', 'not_targeted', 'ambiguous_target',
    'case_variant_key',
    'missing_sender', 'sender_case_variant',
    'malformed_request_id',
    'malformed_type_or_status', 'non_ascii_field', 'oversized_field',
    'conflicting_noise_signal', 'liveness', 'ack',
    'conflicting_request_and_reply', 'claimed_reply', 'request_id',
    'control_type', 'unknown_type', 'control_status',
    'unhinted_notice', 'malformed_payload', 'payload_binding_field',
    'notification_variant',
    'informational_hint',
)

ENVELOPE_KEYS = ('agent', 'to', 'type', 'status', 'payload', 'request_id',
                 'in_reply_to_request_id')
NOTICE_TYPES = frozenset({'message', 'status', 'intent'})
CONTROL_TYPES = frozenset({'decision', 'finding', 'blocked', 'rco_review',
                           'test', 'done', 'release', 'wake_request'})
LIVENESS_TYPES = frozenset({'heartbeat', 'liveness'})
ACK_STATUSES = frozenset({'received', 'seen', 'acknowledged'})
INFORMATIONAL = 'informational'
PAYLOAD_BINDING_KEYS = frozenset({'request_id', 'in_reply_to_request_id'})

# A status token is a control token when it (or the token without a leading
# "un") starts with one of these roots. Prefix matching over-wakes on purpose
# (e.g. "failover"): a spurious wake costs a turn, a missed veto costs safety.
CONTROL_ROOTS = (
    'hold', 'held', 'veto', 'block', 'cancel', 'supersed', 'withdr',
    'retract', 'revok', 'revoc', 'reject', 'refus', 'deny', 'denied', 'nack',
    'fail', 'clos', 'stop', 'halt', 'abort', 'freez', 'frozen', 'quarantin',
    'rollback', 'revert',
)
MAX_FIELD_CHARS = 256
_ID_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z')
_TOKEN_SPLIT = re.compile(r'[^a-z0-9]+')
_LOOSE_SPLIT = re.compile(r'[^a-z0-9._-]+')
_AGENT_RE = re.compile(r'[a-z0-9][a-z0-9._-]{0,127}\Z')
_MISSING = object()


def _ascii_lower(text: str) -> str:
    return ''.join(chr(ord(c) + 32) if 'A' <= c <= 'Z' else c for c in text)


def _field(obj: dict, name: str) -> tuple[Any, bool]:
    """Return (value or _MISSING, has_case_variant_duplicate)."""
    variant = any(isinstance(k, str) and k != name and _ascii_lower(k) == name
                  for k in obj)
    return obj.get(name, _MISSING), variant


def status_tokens(status: str) -> list[str]:
    return [t for t in _TOKEN_SPLIT.split(_ascii_lower(status)) if t]


def has_control_token(status: str) -> bool:
    tokens = status_tokens(status)
    if 'changes_requested' in '_'.join(tokens):
        return True
    for token in tokens:
        stems = (token, token[2:]) if token.startswith('un') else (token,)
        if any(stem.startswith(root) for stem in stems for root in CONTROL_ROOTS):
            return True
    return False


def _mentions(value: Any, target: str) -> bool:
    """Loose, case-insensitive search for target inside any to-like value."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return _ascii_lower(target) in _LOOSE_SPLIT.split(_ascii_lower(text))


def _id_state(value: Any) -> str:
    if value is _MISSING or value is None or value == '':
        return 'absent'
    if isinstance(value, str) and _ID_RE.match(value):
        return 'valid'
    return 'malformed'


def _result(cls: str, reason: str, control_signal: bool = False) -> dict:
    return {'contract': CONTRACT, 'class': cls,
            'wakes': cls not in NON_WAKING_CLASSES,
            'reason': reason, 'control_signal': control_signal}


def _control_signal(event: dict) -> bool:
    etype, status = event.get('type'), event.get('status')
    return ((isinstance(etype, str) and etype in CONTROL_TYPES) or
            (isinstance(status, str) and has_control_token(status)))


def classify(event: Any, target_agent: str) -> dict:
    """Classify one decoded bridge event for target_agent's wake inbox."""
    if not isinstance(target_agent, str) or not _AGENT_RE.match(target_agent):
        raise ValueError('target_agent must be a lowercase bridge agent name')
    if not isinstance(event, dict):
        return _result('ambiguous', 'malformed_event')
    ctl = _control_signal(event)

    agent, agent_variant = _field(event, 'agent')
    if agent == target_agent and not agent_variant:
        return _result('not_addressed', 'self_emission', ctl)

    to_like = [v for k, v in event.items()
               if isinstance(k, str) and _ascii_lower(k) == 'to'
               and v is not None and v != '']
    if not to_like:
        return _result('not_addressed', 'no_target', ctl)
    to, to_variant = _field(event, 'to')
    exact = (not to_variant and isinstance(to, str) and
             target_agent in [t.strip() for t in to.split(',') if t.strip()])
    if not exact:
        if any(_mentions(v, target_agent) for v in to_like):
            return _result('ambiguous', 'ambiguous_target', ctl)
        return _result('not_addressed', 'not_targeted', ctl)

    if any(_field(event, key)[1] for key in ENVELOPE_KEYS):
        return _result('ambiguous', 'case_variant_key', ctl)
    if not isinstance(agent, str) or not agent.strip():
        return _result('ambiguous', 'missing_sender', ctl)
    if _ascii_lower(agent.strip()) == target_agent:
        return _result('ambiguous', 'sender_case_variant', ctl)

    rid = _id_state(event.get('request_id', _MISSING))
    irr = _id_state(event.get('in_reply_to_request_id', _MISSING))
    if 'malformed' in (rid, irr):
        return _result('ambiguous', 'malformed_request_id', ctl)

    etype, status = event.get('type'), event.get('status')
    if not (isinstance(etype, str) and etype and isinstance(status, str) and status):
        return _result('ambiguous', 'malformed_type_or_status', ctl)
    if not (etype.isascii() and status.isascii()):
        return _result('ambiguous', 'non_ascii_field', ctl)
    if len(etype) > MAX_FIELD_CHARS or len(status) > MAX_FIELD_CHARS:
        return _result('ambiguous', 'oversized_field', ctl)
    control_status = has_control_token(status)

    if etype in LIVENESS_TYPES:
        if control_status or rid != 'absent' or irr != 'absent':
            return _result('ambiguous', 'conflicting_noise_signal', ctl)
        return _result('noise', 'liveness')
    if status in ACK_STATUSES:
        if etype not in NOTICE_TYPES or rid != 'absent':
            return _result('ambiguous', 'conflicting_noise_signal', ctl)
        return _result('noise', 'ack')

    if irr == 'valid' and rid == 'valid':
        return _result('ambiguous', 'conflicting_request_and_reply', ctl)
    if irr == 'valid':
        return _result('bound_reply', 'claimed_reply', ctl)
    if rid == 'valid':
        return _result('request', 'request_id', ctl)

    if etype in CONTROL_TYPES:
        return _result('control', 'control_type', ctl)
    if etype not in NOTICE_TYPES:
        return _result('ambiguous', 'unknown_type', ctl)
    if control_status:
        return _result('control', 'control_status', ctl)

    payload = event.get('payload', _MISSING)
    if payload is _MISSING or payload is None:
        return _result('ambiguous', 'unhinted_notice', ctl)
    if not isinstance(payload, dict):
        return _result('ambiguous', 'malformed_payload', ctl)
    if any(isinstance(k, str) and _ascii_lower(k) in PAYLOAD_BINDING_KEYS and
           v is not None and v != '' for k, v in payload.items()):
        return _result('ambiguous', 'payload_binding_field', ctl)
    notification, notification_variant = _field(payload, 'notification')
    if notification_variant or (notification is not _MISSING and
                                notification != INFORMATIONAL):
        return _result('ambiguous', 'notification_variant', ctl)
    if notification is _MISSING:
        return _result('ambiguous', 'unhinted_notice', ctl)
    return _result('notice', 'informational_hint')
