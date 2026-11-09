"""Inspect synthetic roster inputs without claiming an authenticated source.

This module does not read the bridge, fleet files or handshakes.  Its inputs
are caller-supplied in-memory objects; matching their content hashes does not
authenticate their origin, verify roles, prove currentness, or establish a complete roster.
Consequently it never supplies a usable roster to SituationPicture.  A future
integration needs an independently reviewed source trust and freshness contract.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re


LANES = frozenset(("codex-lead-1", "codex-tools-1", "claude-rco-1",
                   "claude-rco-2", "fable-5"))
_TOKEN = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_REF = re.compile(r"[0-9a-f]{64}\Z")
_IDENTITY = ("agent", "agent_uuid", "session_id", "run_id")
_MAX_IDENTITIES = 160
_MAX_EVENT_BYTES = 1_048_576


def _result(reason, matched_count=0):
    # No candidate identities, raw event data, exception text, or paths leave
    # this diagnostic.  In particular, no field here is a usable roster.
    return {"schema": "wd.situation-roster-assessment.v1", "authority": "none",
            "coverage": "UNKNOWN", "source_authentication": "UNKNOWN", "role_verification": "UNKNOWN",
            "reason": reason, "matched_count": matched_count, "roster": None}


def _token(value):
    return isinstance(value, str) and _TOKEN.fullmatch(value) is not None


def _reference(value):
    return isinstance(value, str) and _REF.fullmatch(value) is not None


def _event_ref(event):
    try:
        raw = json.dumps(event, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
        return None
    if len(raw) > _MAX_EVENT_BYTES:
        return None
    return hashlib.sha256(raw).hexdigest()


def assess_roster(*, fleet, handshakes, source_events):
    """Check candidate identities and content refs; always return UNKNOWN.

    ``fleet`` is an in-memory mapping with ``lanes`` and ``tools_supervisor``.
    Handshakes are a bounded list with agent, agent_uuid, session_id, run_id,
    current, status and source_event_ref fields.  Each source entry is a mapping
    with source_event_ref and event.  Historical entries need their own source
    event and each lane must have exactly one current entry.

    This is an advisory syntactic consistency check only.  Even a matching
    event reference is merely a content digest; there is no trusted input or
    freshness attestation here.  Callers must not treat matching candidates as
    authenticated or pass this result as a SituationPicture roster.
    """
    if (not isinstance(fleet, dict) or not isinstance(fleet.get("lanes"), list)
            or len(fleet["lanes"]) != 4 or not isinstance(fleet.get("tools_supervisor"), dict)
            or not isinstance(handshakes, list) or not 1 <= len(handshakes) <= _MAX_IDENTITIES
            or not isinstance(source_events, list) or not 1 <= len(source_events) <= _MAX_IDENTITIES):
        return _result("invalid_input")

    members = fleet["lanes"] + [fleet["tools_supervisor"]]
    expected = {}
    for member in members:
        if not isinstance(member, dict):
            return _result("invalid_fleet")
        agent, uuid = member.get("agent"), member.get("agent_uuid")
        if not isinstance(agent, str) or agent not in LANES or not _token(uuid) or agent in expected:
            return _result("invalid_fleet")
        expected[agent] = uuid
    if set(expected) != LANES or len(set(expected.values())) != len(LANES):
        return _result("invalid_fleet")

    sources = {}
    for source in source_events:
        if not isinstance(source, dict) or not isinstance(source.get("event"), dict):
            return _result("invalid_source")
        ref = source.get("source_event_ref")
        if not _reference(ref) or ref in sources:
            return _result("invalid_source")
        actual = _event_ref(source["event"])
        if actual is None or not hmac.compare_digest(ref, actual):
            return _result("source_reference_mismatch")
        sources[ref] = source["event"]

    sessions, current, used_refs = set(), {agent: 0 for agent in LANES}, set()
    for handshake in handshakes:
        if not isinstance(handshake, dict):
            return _result("invalid_handshake")
        identity = tuple(handshake.get(key) for key in _IDENTITY)
        agent, uuid, session, run = identity
        if (not isinstance(agent, str) or agent not in LANES or not all(_token(value) for value in (uuid, session, run))
                or uuid != expected[agent] or type(handshake.get("current")) is not bool
                or handshake.get("status") != "bridge_bootstrapped"):
            return _result("invalid_handshake")
        ref = handshake.get("source_event_ref")
        if (identity in sessions or not _reference(ref) or ref in used_refs
                or ref not in sources):
            return _result("identity_source_conflict")
        event = sources[ref]
        if (tuple(event.get(key) for key in _IDENTITY) != identity
                or event.get("type") != "status" or event.get("status") != "append_canary"):
            return _result("identity_source_conflict")
        sessions.add(identity)
        used_refs.add(ref)
        if handshake["current"]:
            current[agent] += 1
    if set(current.values()) != {1} or len(used_refs) != len(sources):
        return _result("identity_source_incomplete")
    return _result("identity_source_unverified", len(sessions))
