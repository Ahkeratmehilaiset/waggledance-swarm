"""F5 passive projection of explicitly supplied participant observations.

No process enumeration, token/ACL query, root resolution, mutex access or I/O.
An observed fact is caller evidence, not verified identity or complete holder
coverage. No fact is derived from another fact, including PID. Token values
are limited to identity metadata, never credentials. Caller source labels are
labels only, not authority or authentication.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
import re

INPUT_SCHEMA = "wd.bridge-participant-observations.v1"
REPORT_SCHEMA = "wd.bridge-participant-snapshot.v1"
FACTS = ("pid", "start", "token", "acl", "root", "mutex")
LABEL = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")
SID = re.compile(r"S-1-[0-9]+(?:-[0-9]+)+\Z")
ENTRY_KEYS = {"value", "observed_at_utc", "max_age_seconds", "source", "read_error", "conflicting"}


class ParticipantInputError(ValueError):
    """Invalid envelope; no partial authority or readiness verdict is returned."""


def _time(value):
    if type(value) is not str or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None


def _valid_value(name, value):
    if name == "pid":
        return type(value) is int and 0 < value <= 4294967295
    if name == "start":
        return _time(value) is not None
    if name == "root":
        # A syntactically local absolute path only; do not resolve/stat/probe it.
        return (type(value) is str and 0 < len(value) <= 4096 and "\x00" not in value
                and (bool(re.match(r"[A-Za-z]:[\\/]", value))
                     or value.startswith("/") and not value.startswith("//")))
    if type(value) is not dict or not value:
        return False
    if name in ("token", "acl"):
        allowed = {"user_sid", "integrity_level", "elevated"} if name == "token" else {"owner_sid", "readable", "writable"}
        if set(value) - allowed:
            return False
        for key, item in value.items():
            if key in ("user_sid", "owner_sid"):
                if type(item) is not str or len(item) > 184 or not SID.fullmatch(item):
                    return False
            elif key == "integrity_level":
                if type(item) is not str or item not in {"untrusted", "low", "medium", "high", "system"}:
                    return False
            elif type(item) is not bool:
                return False
        return True
    if name == "mutex":
        return (set(value) == {"name", "observation_kind"}
                and type(value["name"]) is str and 0 < len(value["name"]) <= 256
                and "\x00" not in value["name"]
                and type(value["observation_kind"]) is str
                and value["observation_kind"] in {"configured", "participant_report", "handle_observed"})
    return False


def _fact(name, entry, now):
    unknown = {"state": "unknown", "value": None}
    if entry is None:
        return {**unknown, "reason": "absent"}
    if type(entry) is not dict or set(entry) - ENTRY_KEYS:
        return {**unknown, "reason": "invalid_entry"}
    if "read_error" in entry:
        return {**unknown, "reason": "unreadable"}
    if "conflicting" in entry and type(entry["conflicting"]) is not bool:
        return {**unknown, "reason": "invalid_conflict_marker"}
    if entry.get("conflicting") is True:
        return {**unknown, "reason": "conflicting"}
    source = entry.get("source")
    if type(source) is not str or not LABEL.fullmatch(source):
        return {**unknown, "reason": "invalid_source"}
    observed = _time(entry.get("observed_at_utc"))
    limit = entry.get("max_age_seconds")
    if observed is None:
        return {**unknown, "reason": "invalid_timestamp"}
    if type(limit) is not int or not 1 <= limit <= 604800:
        return {**unknown, "reason": "invalid_max_age"}
    age = (now - observed).total_seconds()
    details = {"source": source, "observed_at_utc": observed.isoformat(),
               "age_seconds": age, "max_age_seconds": limit}
    if age < 0:
        return {**details, **unknown, "reason": "future_dated"}
    if age > limit:
        return {**details, **unknown, "reason": "stale"}
    value = entry.get("value")
    if not _valid_value(name, value):
        return {**details, **unknown, "reason": "invalid_value"}
    if name == "start" and _time(value) > observed:
        return {**details, **unknown, "reason": "future_process_start"}
    return {**details, "state": "observed", "reason": "fresh_caller_observation",
            "value": deepcopy(value)}


def participant_snapshot(observations: dict, now: datetime) -> dict:
    """Return independently aged caller facts, never every-holder/readiness.

    Input has exactly schema and participants. Participants maps stable caller
    labels to optional FACTS entries. Each entry needs value, observed_at_utc,
    integer max_age_seconds and a source label; explicit read_error/conflicting
    markers withhold values. Token and ACL dictionaries contain only the closed
    metadata keys documented by _valid_value. Missing dictionary subfields remain
    absent: elevated/readable/writable are never defaulted. This cannot detect
    conflicting observations the caller omitted; an adapter must mark conflicts
    instead of choosing one. Per-fact timestamps/age express coverage only at now,
    not an uninterrupted monitoring interval or successful future model turn.
    """
    if (type(observations) is not dict or set(observations) != {"schema", "participants"}
            or observations.get("schema") != INPUT_SCHEMA
            or type(observations["participants"]) is not dict):
        raise ParticipantInputError("invalid observation envelope")
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ParticipantInputError("now must be timezone-aware")
    try:
        json.dumps(observations, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ParticipantInputError("observations must contain finite JSON data") from exc
    rows = observations["participants"]
    for label, facts in rows.items():
        if (type(label) is not str or not LABEL.fullmatch(label)
                or type(facts) is not dict or set(facts) - set(FACTS)):
            raise ParticipantInputError("invalid participant or facts")
    now = now.astimezone(timezone.utc)
    projected = {label: {name: _fact(name, facts.get(name), now) for name in FACTS}
                 for label, facts in sorted(rows.items())}
    return {"schema": REPORT_SCHEMA, "evaluated_at_utc": now.isoformat(),
            "participants": projected, "authority_effect": "none",
            "all_mutex_holders_verified": False,
            "coverage": "Caller-supplied facts only; no live probes, completeness, identity or continuity guarantee."}
