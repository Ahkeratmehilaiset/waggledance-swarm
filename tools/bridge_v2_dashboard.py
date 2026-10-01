"""F6 passive, deterministic projection of explicit caller-supplied snapshots.

No filesystem reads, collector, provider call, scheduling, activation or readiness
decision. Producers must supply timestamps and freshness limits per source and
per provider dimension. This is a library, not a live collector or launch gate.
Snapshot data is display data: callers must redact it before supplying it.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json

SNAPSHOT_SCHEMA = "wd.bridge-dashboard-snapshot.v1"
REPORT_SCHEMA = "wd.bridge-dashboard-report.v1"
SOURCES = ("package", "pins", "claims", "tasks", "stage", "effect_journals")
STATES = {
    "cli": {"installed", "missing"},
    "auth": {"valid", "invalid"},
    "quota": {"available", "exhausted"},
    "observed_turn": {"succeeded", "failed"},
}


class DashboardInputError(ValueError):
    """Invalid snapshot envelope; no partial readiness verdict is produced."""


def _time(value):
    if type(value) is not str or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None


def _freshness(entry, now):
    if entry is None:
        return {"state": "unknown", "reason": "absent"}
    if type(entry) is not dict:
        return {"state": "unknown", "reason": "invalid_entry"}
    if "read_error" in entry:
        return {"state": "unknown", "reason": "unreadable"}
    observed = _time(entry.get("observed_at_utc"))
    limit = entry.get("max_age_seconds")
    if observed is None:
        return {"state": "unknown", "reason": "invalid_timestamp"}
    if type(limit) is not int or not 1 <= limit <= 604800:
        return {"state": "unknown", "reason": "invalid_max_age"}
    age = (now - observed).total_seconds()
    details = {"observed_at_utc": observed.isoformat(), "age_seconds": age,
               "max_age_seconds": limit}
    if age < 0:
        return {**details, "state": "unknown", "reason": "future_dated"}
    if age > limit:
        return {**details, "state": "unknown", "reason": "stale"}
    return {**details, "state": "fresh", "reason": "fresh_evidence"}


def dashboard(snapshot: dict, now: datetime) -> dict:
    """Project independently aged sources, never synthesising all-ready.

    Envelope: schema, optional sources and lanes objects only. Known sources are
    named by SOURCES; a source has observed_at_utc, max_age_seconds, data and an
    optional exact boolean required. A read_error marker reports unreadability
    without echoing free-text errors. Lanes contain only the four STATES fields;
    each dimension has its own state, timestamp and max-age. Unknown dimensions
    cannot borrow another dimension's freshness. Malformed envelopes refuse;
    malformed/absent/stale evidence remains visibly unknown.
    """
    if (type(snapshot) is not dict
            or snapshot.get("schema") != SNAPSHOT_SCHEMA
            or set(snapshot) - {"schema", "sources", "lanes"}):
        raise DashboardInputError("invalid snapshot envelope")
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise DashboardInputError("now must be timezone-aware")
    now = now.astimezone(timezone.utc)
    sources, lanes = snapshot.get("sources", {}), snapshot.get("lanes", {})
    if type(sources) is not dict or type(lanes) is not dict or set(sources) - set(SOURCES):
        raise DashboardInputError("invalid sources or lanes")
    try:
        json.dumps(snapshot, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise DashboardInputError("snapshot must contain finite JSON data") from exc
    findings, reports = [], {}
    for name in SOURCES:
        entry = sources.get(name)
        if type(entry) is dict and "required" in entry and type(entry["required"]) is not bool:
            raise DashboardInputError("required must be a boolean")
        report = _freshness(entry, now)
        if report["state"] == "fresh" and "data" not in entry:
            report.update(state="unknown", reason="missing_data")
        report["data"] = deepcopy(entry["data"]) if report["state"] == "fresh" else None
        reports[name] = report
        if type(entry) is dict and entry.get("required") is True and report["state"] != "fresh":
            findings.append({"source": name, "reason": report["reason"]})
    lane_reports = {}
    for lane, dimensions in lanes.items():
        if (type(lane) is not str or not lane or len(lane) > 64
                or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in lane)
                or type(dimensions) is not dict or set(dimensions) - set(STATES)):
            raise DashboardInputError("invalid lane dimensions")
    for lane, dimensions in sorted(lanes.items()):
        projected = {}
        for dimension, allowed in STATES.items():
            entry = dimensions.get(dimension)
            report = _freshness(entry, now)
            if report["state"] == "fresh":
                state = entry.get("state")
                if type(state) is str and state in allowed:
                    report["state"] = state
                else:
                    report.update(state="unknown", reason="unrecognised_state")
            projected[dimension] = report
        lane_reports[lane] = projected
    return {"schema": REPORT_SCHEMA, "evaluated_at_utc": now.isoformat(),
            "sources": reports, "lanes": lane_reports, "findings": findings,
            "authority_effect": "none", "activation_assessed": False,
            "coverage": "Explicit snapshot only; no continuity or next-turn guarantee."}
