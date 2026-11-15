# SPDX-License-Identifier: BUSL-1.1
"""Default-off, pure normalization of a caller-supplied capacity summary.

The caller independently anchors the observer source and install-manifest
hashes, obtains the summary through a trusted read-only invocation, and obtains
the time *after* that invocation. This module performs no I/O or process calls.
Hashing the supplied observer bytes does not prove who produced the summary,
authenticate its provider, establish quota ownership, or authorize execution.
The summary digest is a digest of supplied bytes, not a bridge event reference.
"""

import datetime as dt
import hashlib
import hmac
import json
import math
import re

SCHEMA = "wd.mind-capacity-observation.v1"
MAX_INPUT_BYTES = 2 * 1024 * 1024
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,63}\Z")
_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.([0-9]{1,9}))?(?:Z|[+-][0-9]{2}:[0-9]{2})\Z"
)
_EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
_NANOSECONDS = 1_000_000_000
_LANES = ("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5")
_KNOWN_LANES = frozenset(_LANES + ("claude-rco-3",))
_SOURCES = {"codex": "codex_native_rollout", "claude": "claude_native_hook"}
_VALUES = {
    "activity": frozenset(("turn_started", "turn_completed", "turn_aborted", "idle_observed", "work_requested", "blocked")),
    "auth": frozenset(("authenticated_at_successful_turn", "authenticated_at_metadata_observation", "auth_required")),
    "availability": frozenset(("successful_turn_observed", "rate_limited", "auth_required", "access_denied", "account_on_hold")),
}


class CapacityRefused(ValueError):
    """Fixed refusal with no source bytes, paths or exception details."""


def _deny():
    raise CapacityRefused("mind_capacity_status_refused") from None


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


def _bad_constant(_):
    raise ValueError("constant")


def _ticks(value):
    delta = value - _EPOCH
    return (delta.days * 86400 + delta.seconds) * _NANOSECONDS + delta.microseconds * 1000


def _time(value):
    if type(value) is not str or len(value) > 50:
        raise ValueError("time")
    match = _TIMESTAMP.fullmatch(value)
    if match is None:
        raise ValueError("timestamp_precision")
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("naive")
    # datetime truncates beyond six fractional digits. Retain the remaining
    # nanoseconds so a future or cross-process timestamp cannot round valid.
    fraction = int((match.group(1) or "").ljust(9, "0"))
    return _ticks(parsed.astimezone(dt.timezone.utc)) + fraction % 1000


def _text(value):
    seconds, nanoseconds = divmod(value, _NANOSECONDS)
    stamp = (_EPOCH + dt.timedelta(seconds=seconds)).isoformat(timespec="seconds")
    fraction = f"{nanoseconds:09d}".rstrip("0").ljust(6, "0")
    return stamp.removesuffix("+00:00") + "." + fraction + "Z"


def _field(value="UNKNOWN", observed=None, age=None, source=None, reason=None):
    return {"value": value, "observed_at_utc": observed, "age_seconds": age,
            "source_label": source, "reason": reason}


def _quota(row, now, snapshot_fresh):
    result = {**_field(reason="quota_unavailable_or_unverified"), "windows": [],
              "pool_binding": "UNKNOWN", "independent_capacity": False,
              "lane_quota_attribution": "UNKNOWN"}
    if row is None:
        result["reason"] = "missing_observation"
        return result
    if row.get("quota_freshness") == "provider_timestamp_unknown":
        result["reason"] = "provider_timestamp_unknown"
        return result
    try:
        stamp = _time(row.get("quota_observed_at"))
        process_start = _time(row.get("native_process_start_utc"))
        age = (_ticks(now) - stamp) / _NANOSECONDS
        state = row.get("observed_quota_state")
        windows = row.get("quota_windows")
        if (not snapshot_fresh or row.get("provider") != "codex"
                or row.get("identity_state") != "observed_process_ancestry"
                or not process_start <= stamp <= _ticks(now)
                or row.get("quota_freshness") != "fresh" or not 0 <= age <= 300
                or state not in ("observed_headroom", "exhausted")
                or type(windows) is not list or not 1 <= len(windows) <= 16):
            return result
        clean = []
        keys = {"limit_id", "name", "used_percent", "resets_at", "window_duration_minutes", "window_duration_state"}
        for window in windows:
            if type(window) is not dict or set(window) != keys:
                raise ValueError("quota_window")
            label, name = window["limit_id"], window["name"]
            used, reset = window["used_percent"], window["resets_at"]
            duration, duration_state = window["window_duration_minutes"], window["window_duration_state"]
            if (type(label) is not str or not _LABEL.fullmatch(label) or name not in ("primary", "secondary")
                    or type(used) not in (int, float) or not math.isfinite(used) or not 0 <= used <= 1000000
                    or type(reset) not in (int, float) or not math.isfinite(reset) or not now.timestamp() < reset <= 253402300799
                    or duration_state not in ("valid", "unknown", "not_supplied")
                    or (duration_state == "valid" and (type(duration) is not int or not 0 < duration <= 2147483647))
                    or (duration_state != "valid" and duration is not None)
                    or (state == "observed_headroom" and used >= 100)):
                raise ValueError("quota_values")
            clean.append({"limit_id": "codex" if label == "codex" else "UNKNOWN",
                          "limit_id_sha256": None if label == "codex" else hashlib.sha256(label.encode("utf-8")).hexdigest(),
                          "name": name, "used_percent": used,
                          "resets_at": reset, "window_duration_minutes": duration,
                          "window_duration_state": duration_state})
        result.update(_field(state, _text(stamp), age, "observer_summary", None), windows=clean)
    except (ValueError, TypeError, OverflowError):
        pass
    return result


def _row(row, now, snapshot_fresh):
    if row is None:
        return {**{key: _field(reason="missing_observation") for key in ("activity", "auth", "availability")},
                "quota": _quota(None, now, snapshot_fresh)}
    provider = row["provider"]
    valid = row.get("identity_state") == "observed_process_ancestry" and row.get("source") == _SOURCES[provider]
    try:
        process_start = _time(row.get("native_process_start_utc"))
        activity_stamp = _time(row.get("observed_at"))
        valid = valid and process_start <= activity_stamp <= _ticks(now)
    except (ValueError, TypeError, OverflowError):
        process_start, activity_stamp, valid = None, None, False
    result = {}
    for name, key in (("activity", "activity_state"), ("auth", "auth_state"), ("availability", "availability_state")):
        value = row.get(key)
        # Auth/success fields may survive later work_requested callbacks. Do
        # not attach the later activity timestamp to that older observation.
        if name == "activity":
            time_key = "observed_at"
        elif value in ("authenticated_at_successful_turn", "successful_turn_observed"):
            time_key = "last_successful_turn_at"
        elif value in ("auth_required", "rate_limited", "access_denied", "account_on_hold"):
            time_key = "first_error_at"
        else:
            time_key = None
        try:
            stamp = _time(row.get(time_key)) if time_key else None
            age = (_ticks(now) - stamp) / _NANOSECONDS if stamp is not None else None
        except (ValueError, TypeError, OverflowError):
            stamp, age = None, None
        fresh = (snapshot_fresh and valid and row.get("freshness") == "fresh"
                 and age is not None and 0 <= age <= 300
                 and process_start <= stamp <= activity_stamp)
        if fresh and type(value) is str and value in _VALUES[name]:
            result[name] = _field(value, _text(stamp), age, _SOURCES[provider], None)
        else:
            result[name] = _field(reason="unavailable_or_unverified")
    # A reported window is not a verified lane/pool binding or execution grant.
    result["quota"] = _quota(row, now, snapshot_fresh)
    return result


def normalize(*, enabled=False, observer_source_bytes=None, expected_observer_sha256=None,
              expected_manifest_sha256=None, summary_bytes=None, now_utc=None):
    """Return non-authoritative values and independent snapshot digest, or refuse.

    ``now_utc`` must be supplied by the trusted caller after observer execution.
    Unavailable, future, stale, contradictory or out-of-process observations
    remain UNKNOWN. Missing process-start evidence cannot establish freshness.
    RFC3339 timestamps retain up to nine fractional digits; comparisons do not
    round them to the microsecond resolution of the caller's ``now_utc``.
    A fresh quota observation is shown separately from its always-UNKNOWN
    lane/pool attribution; it is not a readiness or execution claim.
    """
    try:
        if (enabled is not True or type(observer_source_bytes) is not bytes or not observer_source_bytes
                or len(observer_source_bytes) > MAX_INPUT_BYTES or type(summary_bytes) is not bytes
                or not summary_bytes or len(summary_bytes) > MAX_INPUT_BYTES
                or type(expected_observer_sha256) is not str or not _HEX.fullmatch(expected_observer_sha256)
                or type(expected_manifest_sha256) is not str or not _HEX.fullmatch(expected_manifest_sha256)
                or type(now_utc) is not dt.datetime or now_utc.tzinfo is not dt.timezone.utc
                or not hmac.compare_digest(hashlib.sha256(observer_source_bytes).hexdigest(), expected_observer_sha256)):
            raise ValueError("input")
        doc = json.loads(summary_bytes.decode("utf-8-sig"), object_pairs_hook=_pairs, parse_constant=_bad_constant)
        required = {"schema", "observed_at", "execution_allowed", "collection", "installation", "agents", "note"}
        if (type(doc) is not dict or set(doc) != required or doc["schema"] != "wd.capacity-summary.v1"
                or doc["execution_allowed"] is not False or type(doc["agents"]) is not list
                or len(doc["agents"]) > len(_KNOWN_LANES)):
            raise ValueError("summary")
        installation = doc["installation"]
        if (type(installation) is not dict or installation.get("source_verified") is not True
                or installation.get("agent_quota_binding") != "unverified"
                or type(installation.get("source_commit")) is not str
                or not _COMMIT.fullmatch(installation["source_commit"])
                or type(installation.get("manifest_sha256")) is not str
                or not hmac.compare_digest(installation["manifest_sha256"].lower(), expected_manifest_sha256)):
            raise ValueError("installation")
        summary_time = _time(doc["observed_at"])
        summary_age = (_ticks(now_utc) - summary_time) / _NANOSECONDS
        snapshot_fresh = 0 <= summary_age <= 300
        rows = {}
        for row in doc["agents"]:
            if type(row) is not dict or row.get("agent") not in _KNOWN_LANES or row["agent"] in rows:
                raise ValueError("lane")
            expected_provider = "codex" if row["agent"].startswith("codex-") else "claude"
            if row.get("provider") != expected_provider or row.get("execution_allowed") is not False:
                raise ValueError("provider")
            rows[row["agent"]] = row
        lanes = []
        for name in _LANES:
            lanes.append({"lane": name, **_row(rows.get(name), now_utc, snapshot_fresh)})
        return {"schema": SCHEMA, "authority": "none", "execution_allowed": False,
                "observer_output_authentication": "UNKNOWN", "provider_authentication": "UNKNOWN",
                "quota_pool_binding": "UNKNOWN", "independent_capacity": False,
                "observer_source_sha256": expected_observer_sha256,
                "observer_manifest_sha256": expected_manifest_sha256,
                "summary_sha256": hashlib.sha256(summary_bytes).hexdigest(),
                "summary_ref_kind": "sha256_of_caller_supplied_summary",
                "summary_observed_at_utc": _text(summary_time), "as_of_utc": _text(_ticks(now_utc)), "lanes": lanes}
    except Exception:
        _deny()
