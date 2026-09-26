#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Lane runtime profile record (D2 of lane profile switching): schema, I/O, decision.

A record at ``<runtime_root>/lane_profiles/<lane>.json`` states the profile a
lane should be launched with next. It is runtime state, never bundle-pinned:
wd-fleet.json keeps ``"model": "native"``. This module validates a record
against the operator catalog, writes it atomically, and answers the launcher's
question ``launch_decision`` purely. In ``shadow`` - the only effective mode
while the advisor is shadow-only - the answer is always to launch native and
log ``would_apply``. Nothing here launches, stops or signals a process.

See docs/BRIDGE_LANE_PROFILES.md.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.lane_profile_catalog import LANES, classify_transition, effective_mode  # noqa: E402

SCHEMA = "wd.lane-profile-record.v1"
MAX_RECORD_BYTES = 64 * 1024
MAX_TTL_SECONDS = 24 * 3600
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
REQUIRED = ("schema", "lane", "desired_profile", "previous_profile", "reason", "requested_by",
            "request_id", "transition_id", "created_at", "expires_at", "catalog_sha256", "launched")
LAUNCHED = ("native_thread_id", "pid", "process_started_at", "session_id", "run_id", "launched_at")


class RecordError(ValueError):
    """A record is malformed, stale or not permitted; never a request to fall back silently."""


def _text(value: Any, limit: int = 512) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.utcoffset() is not None else None


def record_path(runtime_root: str | Path, lane: str) -> Path:
    """The one path a lane's record may live at; refuses anything but a known lane."""
    if lane not in LANES:
        raise RecordError(f"unknown lane {lane!r}")
    return Path(runtime_root) / "lane_profiles" / f"{lane}.json"


def _validate_launched(launched: Any, created: datetime, now: datetime) -> None:
    if launched is None:
        return
    if not isinstance(launched, dict) or set(launched) != set(LAUNCHED):
        raise RecordError(f"launched must be null or define exactly {list(LAUNCHED)}")
    if not isinstance(launched["native_thread_id"], str) or not _UUID.fullmatch(launched["native_thread_id"]):
        raise RecordError("launched.native_thread_id must be a lowercase UUID")
    if type(launched["pid"]) is not int or not 0 < launched["pid"] <= 2 ** 31 - 1:
        raise RecordError("launched.pid must be a positive int")
    for key in ("process_started_at", "launched_at"):
        if _utc(launched[key]) is None:
            raise RecordError(f"launched.{key} must be an aware ISO timestamp")
    for key in ("session_id", "run_id"):
        if not _text(launched[key], 256):
            raise RecordError(f"launched.{key} required")
    started, recorded = _utc(launched["process_started_at"]), _utc(launched["launched_at"])
    # Causal order (Lead PR1737-B2): the relaunched process is created after the
    # transition record, and the launcher records it after it starts.
    if started < created:
        raise RecordError("launched.process_started_at precedes the record creation")
    if recorded < started:
        raise RecordError("launched.launched_at precedes the process start")
    if recorded > now:
        raise RecordError("launched.launched_at is in the future")


def validate_record(record: Any, catalog: dict, catalog_sha256: str, *,
                    now: datetime | None = None) -> dict:
    """Validate a record against the catalog; return it unchanged or raise RecordError."""
    now = now or datetime.now(timezone.utc)
    if not isinstance(record, dict) or set(record) != set(REQUIRED):
        raise RecordError(f"record must define exactly {list(REQUIRED)}")
    if record["schema"] != SCHEMA:
        raise RecordError(f"schema must be {SCHEMA}")
    lane = record["lane"]
    if lane not in LANES or lane not in catalog["lanes"]:
        raise RecordError("record lane is not a catalog lane")
    if not isinstance(record["catalog_sha256"], str) or not _SHA256.fullmatch(record["catalog_sha256"]):
        raise RecordError("catalog_sha256 must be a lowercase sha256")
    if record["catalog_sha256"] != catalog_sha256:
        raise RecordError("record was written against a different catalog")
    for key in ("reason", "request_id", "transition_id"):
        if not _text(record[key]):
            raise RecordError(f"{key} required")
    who = record["requested_by"]
    if (not isinstance(who, dict) or set(who) != {"agent", "agent_uuid", "session_id"}
            or who["agent"] not in LANES or not isinstance(who["agent_uuid"], str)
            or not _UUID.fullmatch(who["agent_uuid"]) or not _text(who["session_id"], 256)):
        raise RecordError("requested_by must name a lane with agent_uuid and session_id")
    created, expires = _utc(record["created_at"]), _utc(record["expires_at"])
    if created is None or expires is None:
        raise RecordError("created_at and expires_at must be aware ISO timestamps")
    if not 0 < (expires - created).total_seconds() <= MAX_TTL_SECONDS:
        raise RecordError("record lifetime must be positive and at most 24 h")
    if created > now:
        raise RecordError("record created in the future")
    if expires <= now:
        raise RecordError("record expired")
    allowed = catalog["lanes"][lane]["allowed_profiles"]
    for key in ("desired_profile", "previous_profile"):
        if record[key] not in allowed:
            raise RecordError(f"{key} is not an allowed profile for {lane}")
    verdict = classify_transition(catalog, lane, record["previous_profile"], record["desired_profile"])
    if verdict["verdict"] == "park":
        raise RecordError(f"transition requires an operator ack: {verdict['reason']}")
    _validate_launched(record["launched"], created, now)
    return record


def read_record(path: str | Path) -> dict:
    """Read one record file, bounded, plain JSON; a missing file is RecordError too."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        raise RecordError("no record") from None
    if len(data) > MAX_RECORD_BYTES:
        raise RecordError("record exceeds the size bound")
    try:
        return json.loads(data.decode("utf-8"), parse_constant=_refuse_constant)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RecordError("record is not UTF-8 JSON") from None


def _refuse_constant(name: str) -> None:
    raise RecordError(f"non-finite JSON constant {name}")


def write_record(path: str | Path, record: dict) -> None:
    """Atomically replace the record: temp file in the same directory, fsync, rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(record, indent=2, sort_keys=True).encode("utf-8")
    if len(payload) > MAX_RECORD_BYTES:
        raise RecordError("record exceeds the size bound")
    fd, temp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass
        raise


def launch_decision(runtime_root: str | Path, lane: str, catalog: dict, catalog_sha256: str, *,
                    now: datetime | None = None) -> dict:
    """What the launcher should do for ``lane``; pure apart from reading one record.

    Returns ``action``: ``native`` (launch without a model override) or ``apply``
    (launch with ``profile``), plus ``would_apply`` and a ``fallback_event`` when
    a record exists but cannot be used. ``apply`` is possible only when the
    catalog's effective mode is ``auto``. In ``shadow`` it never is, and in
    ``approve`` it fails closed until an operator ack can be verified.
    """
    mode = effective_mode(catalog)
    decision = {"lane": lane, "mode": mode, "action": "native", "profile": None,
                "would_apply": None, "fallback_event": None, "catalog_sha256": catalog_sha256}
    try:
        path = record_path(runtime_root, lane)
    except RecordError as exc:
        decision["fallback_event"] = {"reason": "invalid_lane", "detail": str(exc)}
        return decision
    try:
        record = validate_record(read_record(path), catalog, catalog_sha256, now=now)
    except RecordError as exc:
        if str(exc) != "no record":
            decision["fallback_event"] = {"reason": "record_unusable", "detail": str(exc)}
        return decision
    if record["lane"] != lane:
        decision["fallback_event"] = {"reason": "record_unusable", "detail": "record names another lane"}
        return decision
    profile = catalog["capacity_policy"]["profiles"][record["desired_profile"]]
    target = {"profile_id": record["desired_profile"], "provider": profile["provider"],
              "model": profile["model"], "effort": profile["effort"],
              "transition_id": record["transition_id"]}
    decision["would_apply"] = target
    if mode == "auto":
        decision.update(action="apply", profile=target)
    elif mode == "approve":
        # approve needs a verified per-transition operator ack. No operator
        # identity can be verified yet (spec v3 B5/H), so approve fails closed.
        decision["fallback_event"] = {"reason": "operator_ack_unverifiable",
                                      "detail": "approve mode cannot verify an operator ack"}
    return decision
