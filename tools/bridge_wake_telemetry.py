#!/usr/bin/env python3
"""F1 read-only wake telemetry report over local observation sidecars.

Reads only what the caller names: one telemetry directory (``stage-<32 hex>.json``
records, schema ``wd.bridge-stage.v1``) and optional wake snapshots (schema
``wd.bridge-wake-observation.v1``). It never reads the canonical bridge log
(``events.jsonl``), never writes, never starts a process and never contacts a provider
or the network.

Unknown stays unknown. A missing stage, an unreadable or invalid record, an out-of-order
pair or a future-dated record gives null plus a counted reason, never zero. The no-op
ratio comes ONLY from explicit ``turn_completed`` outcomes (``acted`` or ``noop``); it is
never inferred from pending or missing stages or from a queue acceptance, and with no
explicit outcome it is null. Flows are keyed by request id, requester, requester
session and target (responder); post-answer stages are also keyed by the reply
timestamp. Telemetry is local observation, not authenticated evidence: no record
proves task completion or grants processing.

Exit codes: 0 a report was produced (it may contain unknowns), 3 invalid input.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import stat as stat_module
import sys
from typing import Any, Sequence

REPORT_SCHEMA = "wd.bridge-wake-telemetry-report.v1"
STAGE_SCHEMA = "wd.bridge-stage.v1"
WAKE_SCHEMA = "wd.bridge-wake-observation.v1"
# A constant, not __doc__: under python -OO the docstring is None.
DESCRIPTION = "F1 read-only wake telemetry report over local observation sidecars."

# The flow order used by the existing per-request latency report.
FLOW_STAGES = ("request_durable", "watcher_seen", "relay_enqueued", "model_turn_started",
               "answer_durable", "lead_processed", "user_reported")
OUTCOME_STAGE = "turn_completed"
STAGES = FLOW_STAGES + (OUTCOME_STAGE,)
AGENT_REPORTED = frozenset({"model_turn_started", "lead_processed", "user_reported", OUTCOME_STAGE})
REPLY_STAGES = ("answer_durable", "lead_processed", "user_reported")
OUTCOMES = ("acted", "noop")

STAGE_NAME_RE = re.compile(r"stage-[0-9a-f]{32}\.json\Z")
TOKEN_RE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
AGENT_RE = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
WINDOWS_DRIVE_PATH_RE = re.compile(r"[A-Za-z]:[\\/]")
TIMESTAMP_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})\Z")

STAGE_KEYS = frozenset({"schema", "stage", "observed_at_utc", "target", "request_id", "requester",
                        "requester_session_id", "delivery_id", "queue_id", "observer_pid",
                        "authority_effect", "observation_source", "reply_ts_utc", "report_reference"})
OPTIONAL_STAGE_KEYS = frozenset({"action_outcome", "metadata"})
WAKE_KEYS = frozenset({"schema", "observed_at_utc", "requests", "correlation_complete", "authority_effect"})
BINDING_KEYS = frozenset({"request_id", "agent", "session_id", "reply_ts_utc"})
METADATA_KEYS = frozenset({"reason", "watermark", "latency_ms", "latency_basis"})

MAX_STAGE_FILES = 20000
MAX_STAGE_BYTES = 64 * 1024
MAX_WAKE_FILES = 16
MAX_WAKE_BYTES = 512 * 1024
MAX_WAKE_BINDINGS = 256
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_JSON_DEPTH = 8
MAX_ID_CHARS = 256
MAX_FIELD_CHARS = 1024
MAX_LATENCY_MS = 86_400_000
MAX_CLOCK_SKEW = timedelta(minutes=5)


class TelemetryInputError(ValueError):
    """The invocation or a named input path is invalid; nothing was reported."""


class _Skip(Exception):
    """One sidecar is unusable; carries a stable reason code (never file content)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise TelemetryInputError("invalid command-line arguments; use --help for usage")


def parse_utc(value: Any) -> datetime | None:
    """Strict ISO-8601 with a timezone; up to 9 fraction digits (PowerShell writes 7)."""
    if type(value) is not str or len(value) > 40:
        return None
    match = TIMESTAMP_RE.match(value)
    if match is None:
        return None
    base, fraction, zone = match.groups()
    text = base + ("." + (fraction + "000000")[:6] if fraction else "") + ("+00:00" if zone == "Z" else zone)
    try:
        return datetime.fromisoformat(text).astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def _local_absolute(raw: str) -> bool:
    """A drive-letter path on Windows, '/...' elsewhere; UNC, device and '//' refused."""
    if not raw or "\0" in raw:
        return False
    if os.name == "nt":
        return WINDOWS_DRIVE_PATH_RE.match(raw) is not None
    return raw.startswith("/") and not raw.startswith("//")


def _unique(pairs: list[tuple[str, Any]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise _Skip("duplicate_key")
        result[key] = value
    return result


def _reject_constant(value: str):
    raise _Skip("non_finite_constant")


def _check_depth(text: str) -> None:
    depth, in_string, escaped = 0, False, False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise _Skip("too_deep")
        elif char in "]}":
            depth -= 1


def _read_json(path: Path, limit: int) -> tuple[Any, int]:
    """One bounded, non-blocking read of a regular file; returns (value, bytes read)."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
    except (OSError, ValueError):
        raise _Skip("unreadable") from None
    with os.fdopen(fd, "rb") as stream:
        try:
            if not stat_module.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise _Skip("not_a_regular_file")
            raw = stream.read(limit + 1)
        except OSError:
            raise _Skip("unreadable") from None
    if len(raw) > limit:
        raise _Skip("oversized")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError:
        raise _Skip("not_utf8") from None
    _check_depth(text)
    try:
        return json.loads(text, object_pairs_hook=_unique, parse_constant=_reject_constant), len(raw)
    except (ValueError, RecursionError):
        raise _Skip("not_json") from None


def _bounded_id(value: Any, *, allow_empty: bool = False) -> bool:
    return (type(value) is str and (allow_empty or value != "") and len(value) <= MAX_ID_CHARS
            and value.isprintable())


def _metadata(value: Any) -> dict:
    """Validate optional F1 metadata exactly as the writer produces it."""
    if type(value) is not dict or not value or not set(value) <= METADATA_KEYS:
        raise _Skip("metadata_invalid")
    if "reason" in value and not (type(value["reason"]) is str and TOKEN_RE.fullmatch(value["reason"])):
        raise _Skip("metadata_invalid")
    if "watermark" in value and not (type(value["watermark"]) is int and value["watermark"] >= 0):
        raise _Skip("metadata_invalid")
    if ("latency_ms" in value) != ("latency_basis" in value):
        raise _Skip("metadata_invalid")
    if "latency_ms" in value:
        latency, basis = value["latency_ms"], value["latency_basis"]
        if (type(latency) not in (int, float) or not math.isfinite(latency)
                or not 0 <= latency <= MAX_LATENCY_MS
                or not (type(basis) is str and TOKEN_RE.fullmatch(basis))):
            raise _Skip("metadata_invalid")
    return dict(value)


def validate_stage(record: Any, now: datetime | None) -> dict:
    """A stage record exactly as BridgeTelemetry.ps1 writes it, or _Skip(reason)."""
    if type(record) is not dict:
        raise _Skip("not_an_object")
    keys = set(record)
    if not STAGE_KEYS <= keys or not keys <= STAGE_KEYS | OPTIONAL_STAGE_KEYS:
        raise _Skip("keys_mismatch")
    if record["schema"] != STAGE_SCHEMA:
        raise _Skip("schema_mismatch")
    stage = record["stage"]
    if stage not in STAGES:
        raise _Skip("unknown_stage")
    if record["authority_effect"] != "none":
        raise _Skip("authority_claimed")
    expected_source = "agent_reported" if stage in AGENT_REPORTED else "runtime_observed"
    if record["observation_source"] != expected_source:
        raise _Skip("provenance_mismatch")
    observed = parse_utc(record["observed_at_utc"])
    if observed is None:
        raise _Skip("time_invalid")
    if now is not None and observed - now > MAX_CLOCK_SKEW:
        raise _Skip("future_dated")
    if not (type(record["target"]) is str and AGENT_RE.fullmatch(record["target"])):
        raise _Skip("target_invalid")
    request_id = record["request_id"]
    if request_id is None:
        if record["requester"] is not None or record["requester_session_id"] is not None:
            raise _Skip("binding_invalid")
    elif not _bounded_id(request_id) or not all(
            value is None or _bounded_id(value, allow_empty=True)
            for value in (record["requester"], record["requester_session_id"])):
        raise _Skip("binding_invalid")
    for name in ("delivery_id", "queue_id", "report_reference"):
        value = record[name]
        if type(value) is not str or len(value) > MAX_FIELD_CHARS:
            raise _Skip("field_invalid")
    pid = record["observer_pid"]
    if type(pid) is not int or pid <= 0:
        raise _Skip("field_invalid")
    reply = record["reply_ts_utc"]
    if type(reply) is not str or (reply and parse_utc(reply) is None):
        raise _Skip("time_invalid")
    if (stage == OUTCOME_STAGE) != ("action_outcome" in record):
        raise _Skip("outcome_misplaced")
    if stage == OUTCOME_STAGE and record["action_outcome"] not in OUTCOMES:
        raise _Skip("outcome_invalid")
    result = {"stage": stage, "observed": observed, "target": record["target"], "request_id": request_id,
              "requester": record["requester"], "session": record["requester_session_id"],
              "delivery_id": record["delivery_id"], "reply": parse_utc(reply) if reply else None,
              "outcome": record.get("action_outcome")}
    if "metadata" in record:
        result["metadata"] = _metadata(record["metadata"])
    return result


def validate_wake(snapshot: Any, now: datetime | None) -> dict:
    if type(snapshot) is not dict:
        raise _Skip("not_an_object")
    keys = set(snapshot)
    if not WAKE_KEYS <= keys or not keys <= WAKE_KEYS | {"metadata"}:
        raise _Skip("keys_mismatch")
    if snapshot["schema"] != WAKE_SCHEMA:
        raise _Skip("schema_mismatch")
    if snapshot["authority_effect"] != "none":
        raise _Skip("authority_claimed")
    observed = parse_utc(snapshot["observed_at_utc"])
    if observed is None:
        raise _Skip("time_invalid")
    if now is not None and observed - now > MAX_CLOCK_SKEW:
        raise _Skip("future_dated")
    if type(snapshot["correlation_complete"]) is not bool:
        raise _Skip("field_invalid")
    requests = snapshot["requests"]
    if type(requests) is not list or len(requests) > MAX_WAKE_BINDINGS:
        raise _Skip("requests_invalid")
    for binding in requests:
        if (type(binding) is not dict or set(binding) != BINDING_KEYS
                or not all(type(binding[k]) is str and len(binding[k]) <= MAX_ID_CHARS for k in BINDING_KEYS)
                or not binding["request_id"]
                or (binding["reply_ts_utc"] and parse_utc(binding["reply_ts_utc"]) is None)):
            raise _Skip("requests_invalid")
    report = {"observed_at_utc": observed.isoformat(), "bindings": len(requests),
              "correlation_complete": snapshot["correlation_complete"], "metadata": None}
    if "metadata" in snapshot:
        report["metadata"] = _metadata(snapshot["metadata"])
    return report


def turn_identity(record: dict) -> tuple | None:
    """A reliable identity for one turn_completed outcome, or None (unknown, never invented).

    A delivery id is the per-turn id: the relay's per-wake id, or one id minted once at
    model_turn_started for a non-relay turn and passed to every stage of that turn. Without
    one, only a request-bound outcome that names its requester session AND the reply the turn
    wrote identifies a turn: that reply is the turn's own answer. A request alone names the
    REQUEST, not the turn, so two separate reply-less wakes on one request stay two
    unidentified outcomes, never one merged turn (RCO1 SF1). One turn must keep ONE identity:
    written once with a delivery id and once without, it is counted twice (documented)."""
    if record["delivery_id"]:
        return ("delivery", record["target"], record["delivery_id"])
    if record["request_id"] is not None and record["session"] and record["reply"] is not None:
        return ("reply", record["target"], record["request_id"], record["requester"], record["session"],
                record["reply"].isoformat())
    return None


def _percentile(sorted_values: list[float], fraction: float) -> float:
    """Nearest-rank percentile over a non-empty ascending list (deterministic)."""
    rank = max(1, math.ceil(fraction * len(sorted_values)))
    return sorted_values[rank - 1]


def _summary(durations: list[float], unknown: int, out_of_order: int) -> dict:
    values = sorted(durations)
    result = {"known": len(values), "unknown": unknown, "out_of_order": out_of_order,
              "min_seconds": None, "p50_seconds": None, "p90_seconds": None, "max_seconds": None}
    if values:
        result.update(min_seconds=round(values[0], 6), p50_seconds=round(_percentile(values, 0.5), 6),
                      p90_seconds=round(_percentile(values, 0.9), 6), max_seconds=round(values[-1], 6))
    return result


def _interval(start: datetime | None, end: datetime | None) -> tuple[float | None, bool]:
    """(seconds, out_of_order); a missing end point or a negative interval is unknown."""
    if start is None or end is None:
        return None, False
    seconds = (end - start).total_seconds()
    if seconds < 0:
        return None, True
    return seconds, False


def build_report(records: list[dict], *, wake: list[dict], inputs: dict, errors: dict,
                 now: datetime | None) -> dict:
    flows: dict[tuple, dict] = {}
    relay_by_delivery: dict[tuple, list[datetime]] = {}
    unbound: dict[str, int] = {}
    outcomes = []
    for record in records:
        if record["stage"] == OUTCOME_STAGE:
            outcomes.append(record)
            continue
        if record["request_id"] is None:
            # An unbound relay joins a flow only through the turn start's delivery id.
            if record["stage"] == "relay_enqueued" and record["delivery_id"]:
                relay_by_delivery.setdefault((record["delivery_id"], record["target"]), []).append(record["observed"])
            else:
                unbound[record["stage"]] = unbound.get(record["stage"], 0) + 1
            continue
        key = (record["request_id"], record["requester"], record["session"], record["target"])
        flow = flows.setdefault(key, {"stages": {}, "replies": {}, "deliveries": set()})
        if record["stage"] in REPLY_STAGES:
            reply_key = record["reply"].isoformat() if record["reply"] else ""
            slot = flow["replies"].setdefault(reply_key, {})
            slot[record["stage"]] = min(filter(None, (slot.get(record["stage"]), record["observed"])))
        current = flow["stages"].get(record["stage"])
        flow["stages"][record["stage"]] = record["observed"] if current is None else min(current, record["observed"])
        if record["stage"] == "model_turn_started" and record["delivery_id"]:
            flow["deliveries"].add(record["delivery_id"])

    joined = set()
    for key, flow in flows.items():
        for delivery in sorted(flow["deliveries"]):
            times = relay_by_delivery.get((delivery, key[3]))
            if times:
                joined.add((delivery, key[3]))
                earliest = min(times)
                current = flow["stages"].get("relay_enqueued")
                flow["stages"]["relay_enqueued"] = earliest if current is None else min(current, earliest)

    names = [f"{a}->{b}" for a, b in zip(FLOW_STAGES, FLOW_STAGES[1:])]
    durations = {name: [] for name in names}
    unknown = {name: 0 for name in names}
    disorder = {name: 0 for name in names}
    for flow in flows.values():
        for start, end in zip(FLOW_STAGES, FLOW_STAGES[1:]):
            name = f"{start}->{end}"
            if start in REPLY_STAGES and end in REPLY_STAGES:
                # Post-answer stages pair only within the same reply.
                pairs = [_interval(slot.get(start), slot.get(end)) for _, slot in sorted(flow["replies"].items())]
                pairs = [p for p in pairs if p != (None, False)] or [(None, False)]
            else:
                pairs = [_interval(flow["stages"].get(start), flow["stages"].get(end))]
            for seconds, out_of_order in pairs:
                if seconds is None:
                    unknown[name] += 1
                    disorder[name] += out_of_order
                else:
                    durations[name].append(seconds)

    # One outcome per TURN (RCO1 S1): a replayed record is counted once, a turn whose records
    # disagree is unknown, and an outcome without a reliable turn identity is unknown too.
    turns: dict[tuple, list[str]] = {}
    unidentified = duplicates = 0
    for record in outcomes:
        key = turn_identity(record)
        if key is None:
            unidentified += 1
            continue
        seen = turns.setdefault(key, [])
        duplicates += bool(seen)
        seen.append(record["outcome"])
    acted = noop = conflicts = 0
    per_target: dict[str, list[int]] = {}
    for key, values in turns.items():
        if len(set(values)) > 1:
            conflicts += 1
            continue
        counts = per_target.setdefault(key[1], [0, 0])
        if values[0] == "acted":
            acted += 1
            counts[0] += 1
        else:
            noop += 1
            counts[1] += 1
    by_target = {target: {"acted": a, "noop": n, "value": round(n / (a + n), 6)}
                 for target, (a, n) in sorted(per_target.items())}
    reason = None
    if not acted + noop:
        reason = "no_explicit_outcomes" if not outcomes else "no_identified_consistent_outcomes"
    noop_ratio = {"value": round(noop / (acted + noop), 6) if acted + noop else None,
                  "acted": acted, "noop": noop, "by_target": by_target, "reason": reason,
                  "turns": acted + noop, "duplicate_outcomes": duplicates, "conflicting_turns": conflicts,
                  "unidentified_outcomes": unidentified,
                  "basis": ("explicit turn_completed outcomes only, one per turn identity (delivery id, or "
                            "request + requester + session + reply per target); unidentified or conflicting "
                            "turns are unknown; never inferred from pending or missing stages or from queue "
                            "acceptance")}
    metadata_reasons: dict[str, int] = {}
    for record in records:
        reason = record.get("metadata", {}).get("reason")
        if reason:
            metadata_reasons[reason] = metadata_reasons.get(reason, 0) + 1

    return {
        "schema": REPORT_SCHEMA,
        "evaluated_at_utc": now.isoformat() if now is not None else None,
        "inputs": inputs,
        "errors": dict(sorted(errors.items())),
        "flows": {"count": len(flows), "unbound_records": dict(sorted(unbound.items())),
                  "unjoined_relay_deliveries": len(set(relay_by_delivery) - joined)},
        "latency": {name: _summary(durations[name], unknown[name], disorder[name]) for name in names},
        "noop_ratio": noop_ratio,
        "observation_reasons": dict(sorted(metadata_reasons.items())),
        "wake_snapshots": wake,
        "authority_effect": "none",
        "processing_granted": False,
        "task_completion_verified": False,
        "timing_basis": ("local observations; model_turn_started is the first agent-reported marker, "
                         "not engine timing; negative or missing intervals are unknown, never zero"),
        "limits": ["telemetry is local observation, not authenticated evidence",
                   "without --now, future-dated records cannot be detected"
                   if now is None else "records more than 5 minutes after --now are rejected as future_dated"],
    }


def run(telemetry_directory: Path, wake_paths: Sequence[Path], now: datetime | None) -> tuple[int, dict]:
    directory = str(telemetry_directory)
    if not _local_absolute(directory):
        raise TelemetryInputError("the telemetry directory must be a local absolute path")
    for path in wake_paths:
        if not _local_absolute(str(path)):
            raise TelemetryInputError("a wake snapshot path must be a local absolute path")
        if path.name.lower() == "events.jsonl":
            raise TelemetryInputError("the canonical bridge log is never read by this report")
    if len(wake_paths) > MAX_WAKE_FILES:
        raise TelemetryInputError("too many wake snapshots")
    try:
        info = os.stat(directory)
    except (OSError, ValueError):
        raise TelemetryInputError("the telemetry directory is unreadable") from None
    if not stat_module.S_ISDIR(info.st_mode):
        raise TelemetryInputError("the telemetry directory is not a directory")

    errors: dict[str, int] = {}
    names, ignored = [], 0
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if STAGE_NAME_RE.fullmatch(entry.name):
                    names.append(entry.name)
                else:
                    ignored += 1
    except OSError:
        raise TelemetryInputError("the telemetry directory cannot be listed") from None
    names.sort()
    truncated = len(names) > MAX_STAGE_FILES
    records, total = [], 0
    for name in names[:MAX_STAGE_FILES]:
        path = Path(directory) / name
        try:
            if path.is_symlink():
                raise _Skip("symlink")
            if total >= MAX_TOTAL_BYTES:
                raise _Skip("total_budget_exhausted")
            value, size = _read_json(path, MAX_STAGE_BYTES)
            total += size
            records.append(validate_stage(value, now))
        except _Skip as skip:
            errors[skip.reason] = errors.get(skip.reason, 0) + 1

    wake = []
    for index, path in enumerate(wake_paths):
        try:
            if path.is_symlink():
                raise _Skip("symlink")
            value, _ = _read_json(path, MAX_WAKE_BYTES)
            wake.append({"index": index, "valid": True, "reason": None, **validate_wake(value, now)})
        except _Skip as skip:
            wake.append({"index": index, "valid": False, "reason": skip.reason})

    inputs = {"stage_files": len(names), "stage_files_read": min(len(names), MAX_STAGE_FILES),
              "stage_records_valid": len(records), "stage_records_invalid": sum(errors.values()),
              "ignored_names": ignored, "truncated": truncated, "wake_snapshots": len(wake_paths),
              "coverage": "partial" if truncated or errors else "complete"}
    return 0, build_report(records, wake=wake, inputs=inputs, errors=errors, now=now)


def invalid_input(error: Exception) -> tuple[int, dict]:
    return 3, {"schema": REPORT_SCHEMA, "verdict": "invalid_input", "error": str(error)[:300],
               "authority_effect": "none", "processing_granted": False}


def main(argv: Sequence[str] | None = None) -> int:
    parser = _ArgumentParser(description=DESCRIPTION, allow_abbrev=False)
    parser.add_argument("--telemetry-directory", type=Path, required=True,
                        help="directory holding stage-*.json sidecars (shared/telemetry)")
    parser.add_argument("--wake-observation", type=Path, action="append", default=[],
                        help="a wd.bridge-wake-observation.v1 snapshot; repeatable (at most 16)")
    parser.add_argument("--now", help="evaluation time (ISO-8601 with timezone) for future-dated checks")
    try:
        args = parser.parse_args(argv)
        now = None
        if args.now is not None:
            now = parse_utc(args.now)
            if now is None:
                raise TelemetryInputError("--now must be an ISO-8601 timestamp with a timezone")
        code, report = run(args.telemetry_directory, list(args.wake_observation), now)
    except TelemetryInputError as exc:
        code, report = invalid_input(exc)
    except Exception as exc:  # noqa: BLE001 - never a traceback; only the type name is reported
        code, report = invalid_input(TelemetryInputError("unexpected " + type(exc).__name__))
    sys.stdout.write(json.dumps(report, sort_keys=True, ensure_ascii=True) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
