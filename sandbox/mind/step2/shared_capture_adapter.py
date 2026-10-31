"""In-memory, caller-asserted bridge-detail to shared projection adapter.

No capture or source is read or written here, no caller, policy, wall clock,
cursor coverage or source is authenticated. The producer's detail hashes prove
row consistency, not that rows belong to the caller-asserted cursor interval.
RCO-origin or RCO-addressed events and tasks outside the caller's explicit
non-review policy are excluded completely. Caller must independently authorize
that policy and source; the classification here is not authentication.
"""

import base64
import datetime as dt
import hashlib
import hmac
import json
import math
import re

from sandbox.mind import mind_capture_producer as producer
from sandbox.mind.step2 import shared_projection as projection
from tools.bridge_compact_view import event_id

_HEX = re.compile(r"[0-9a-f]{64}\Z")
_GENERATION = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_IDENTITY = re.compile(r"(?:windows-v1:[0-9a-f]{8}:[0-9a-f]{16}|posix-v1:[0-9a-f]+:[0-9a-f]+)\Z")
_SNAPSHOT_KEYS = frozenset({"file_identity", "cursor_start", "cursor_end", "generation", "snapshot_length",
                            "position", "unconsumed_bytes", "observed_utc"})
_ROWS_LIMIT = 100000
_EVENT_LIMIT = 256 * 1024
_BATCH_LIMIT = 8 * 1024 * 1024
_NODES_LIMIT = 10000
_DEPTH_LIMIT = 32
_RCO = frozenset({"claude-rco-1", "claude-rco-2"})
_RCO_NAME = re.compile(r"(?<![a-z0-9])(?:claude[-_]rco[-_]?[12]|rco[-_ ]?[12])(?![a-z0-9])", re.IGNORECASE)
MAX_TTL_SECONDS = 900


class AdapterRefused(ValueError):
    """Fixed refusal; never includes row content or paths."""


def _utc_now():
    return dt.datetime.now(dt.timezone.utc)


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError()
        out[key] = value
    return out


def _cursor(value):
    if not isinstance(value, str) or not producer.CURSOR.fullmatch(value):
        raise ValueError()
    encoded = value[4:]
    raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if (not isinstance(parsed, dict) or set(parsed) != {"offset", "file_identity", "generation"}
            or type(parsed["offset"]) is not int or parsed["offset"] < 0
            or producer.cursor_source(value) is None):
        raise ValueError()
    return parsed


def _snapshot(value, ttl_seconds):
    if not isinstance(value, dict) or set(value) != _SNAPSHOT_KEYS:
        raise ValueError()
    identity, generation = value["file_identity"], value["generation"]
    if not isinstance(identity, str) or not _IDENTITY.fullmatch(identity):
        raise ValueError()
    if generation is not None and (not isinstance(generation, str) or not _GENERATION.fullmatch(generation)):
        raise ValueError()
    start, end = _cursor(value["cursor_start"]), _cursor(value["cursor_end"])
    if any(c["file_identity"] != identity or c["generation"] != generation for c in (start, end)):
        raise ValueError()
    if any(type(value[k]) is not int or value[k] < 0 for k in ("snapshot_length", "position", "unconsumed_bytes")):
        raise ValueError()
    if (start["offset"] > end["offset"] or end["offset"] != value["position"]
            or value["position"] != value["snapshot_length"] or value["unconsumed_bytes"] != 0):
        raise ValueError()
    if (type(ttl_seconds) not in (int, float) or not math.isfinite(ttl_seconds)
            or not 0 < ttl_seconds <= MAX_TTL_SECONDS):
        raise ValueError()
    observed = value["observed_utc"]
    if not isinstance(observed, str) or re.search(r"[.,]\d{7,}", observed):
        raise ValueError()
    parsed = dt.datetime.fromisoformat(observed.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError()
    parsed = parsed.astimezone(dt.timezone.utc)
    age = (_utc_now() - parsed).total_seconds()
    if age < 0 or age > ttl_seconds:
        raise ValueError()
    sha = lambda text: hashlib.sha256(text.encode("utf-8")).hexdigest()
    return {"verification": "caller_asserted_not_authenticated", "coverage": "caller_asserted_not_verified",
            "generation_status": "UNKNOWN" if generation is None else "caller_asserted",
            "generation_sha256": None if generation is None else sha(generation),
            "source_identity_sha256": sha(identity), "cursor_start_sha256": sha(value["cursor_start"]),
            "cursor_end_sha256": sha(value["cursor_end"]), "observed_utc": parsed.isoformat(),
            "age_seconds": age, "ttl_seconds": ttl_seconds,
            "snapshot_length": value["snapshot_length"], "position": value["position"],
            "unconsumed_bytes": 0}


def _unsafe_recipient(value):
    """Exclude named RCO recipients and recipient values that cannot be classified."""
    if value is None:
        return False
    if isinstance(value, str):
        return _RCO_NAME.search(value) is not None
    if isinstance(value, list):
        return any(not isinstance(item, str) or _RCO_NAME.search(item) is not None for item in value)
    return True


def _exclude_rco_event(event):
    fields = {key.lower(): value for key, value in event.items()}
    agent = fields.get("agent")
    if not isinstance(agent, str) or agent in _RCO or _RCO_NAME.search(agent):
        return True
    if any(_unsafe_recipient(fields.get(key)) for key in ("to", "recipient")):
        return True
    payload = fields.get("payload")
    if payload is None:
        return False
    if not isinstance(payload, dict):
        return True
    payload_fields = {key.lower(): value for key, value in payload.items()}
    return any(_unsafe_recipient(payload_fields.get(key)) for key in ("to", "recipient"))


def _event_budget(event):
    """Bound JSON size/depth before either canonical serializer sees a row.

    Each string character is charged at its worst JSON escape size; this is a
    conservative upper bound, not a byte-exact serialization estimate.
    """
    stack, total, nodes = [(event, 0)], 0, 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > _NODES_LIMIT or depth > _DEPTH_LIMIT:
            raise ValueError()
        kind = type(value)
        if kind is str:
            total += 2 + 6 * len(value)
        elif kind is dict:
            if (len(value) > _NODES_LIMIT or any(type(key) is not str or not key.isascii() for key in value)
                    or len({key.lower() for key in value}) != len(value)):
                raise ValueError()
            total += 2 + 2 * len(value)
            stack.extend((item, depth + 1) for pair in value.items() for item in pair)
        elif kind is list:
            if len(value) > _NODES_LIMIT:
                raise ValueError()
            total += 2 + len(value)
            stack.extend((item, depth + 1) for item in value)
        elif value is None or kind is bool:
            total += 5
        elif kind is int:
            if value.bit_length() > 4096:
                raise ValueError()
            total += len(str(value))
        elif kind is float and math.isfinite(value):
            total += len(repr(value))
        else:
            raise ValueError()
        if total > _EVENT_LIMIT:
            raise ValueError()
    return total


def adapt_rows(rows, snapshot, approved_non_review_tasks, *, ttl_seconds):
    """Validate supplied rows and produce an in-memory metadata-only projection."""
    try:
        source = _snapshot(snapshot, ttl_seconds)
        if type(approved_non_review_tasks) is not frozenset:
            raise ValueError()
        # Enforce the shared projection's own task-shape/marker rules.
        projection._approved_tasks(approved_non_review_tasks)
        if not isinstance(rows, list) or len(rows) > _ROWS_LIMIT:
            raise ValueError()
        events, seen, batch_bytes = [], set(), 0
        for row in rows:
            if (not isinstance(row, dict) or set(row) != producer.ROW_KEYS or not isinstance(row["event"], dict)
                    or row["source"] not in ("detail", "seed")
                    or not isinstance(row["ref"], str) or not _HEX.fullmatch(row["ref"])
                    or not isinstance(row["row_sha256"], str) or not _HEX.fullmatch(row["row_sha256"])):
                raise ValueError()
            event = row["event"]
            batch_bytes += _event_budget(event)
            if batch_bytes > _BATCH_LIMIT:
                raise ValueError()
            if not hmac.compare_digest(event_id(event), row["ref"]) or not hmac.compare_digest(
                    producer.row_digest(event), row["row_sha256"]):
                raise ValueError()
            if row["ref"] in seen:
                continue
            seen.add(row["ref"])
            if _exclude_rco_event(event) or event.get("task_id") not in approved_non_review_tasks:
                continue
            events.append(event)
        value = projection.project_events(events, approved_tasks=approved_non_review_tasks, include_refs=False)
        projection.validate_projection(value, approved_tasks=approved_non_review_tasks, include_refs=False)
        return {"projection": value, "source": source}
    except Exception:
        raise AdapterRefused("capture_refused") from None
