#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Bridge v2 F19/F25: pure capacity adapter. Measured capacity evidence to one router capacity block.

``capacity_evidence(worker, row, paced, signed_policy, now)`` returns one
``wd.routing-capacity.v1`` record for one worker. Its ``capacity`` block carries exactly the
fields ``tools/wd_task_router.py`` reads (observed_utc, valid_until_utc, state, billing,
projected_used_percent, profile_id), or it is None. None means unknown: the router then keeps
the worker unknown, never ready. This module reads no file, clock, environment, network,
provider or bridge, and it never raises for ordinary input errors.

Inputs:
* ``worker``: {worker, profile_id, subject}. ``subject`` is the lane's own quota subject (the
  Codex auth context id or the Claude native session id) from the caller's lane evidence.
* ``row``: one observation row of ``bridge_capacity_collector.status()``: a
  ``wd.capacity-observation.v1`` with its ``freshness`` label and, when bound, the verified
  pool binding that ``apply_pool_binding`` stored.
* ``paced``: the output of ``wd_capacity_pacing.pace_windows`` over the observer's samples.
* ``signed_policy``: {"policy": P, "sha256": pin}, and the canonical digest of P must equal
  the pin. P names the accepted freshness labels per provider, the maximum observation age,
  and for each pool its billing (included or paid) and mode (normal or conserve). The caller
  verifies the signature behind the pin; this module only binds to it.
* ``now``: an aware ``datetime``, like the collector and the pacer take. The router takes the
  ISO string of the same instant; any other ``now`` is unknown (clock_invalid).

Each of these makes the capacity unknown (verdict "unknown", capacity None) with a stable
reason: malformed or non-plain input, a policy that is malformed or does not match its pin,
a row for another subject, a failed, stale or future observation, a freshness label the
policy does not accept, a pool that is not a verified binding or whose binding expired, a
pool the policy does not price, unknown quota windows, and any observed window the pacer
did not pace in the same window instance with a measured rate. Claude rows carry
``provider_timestamp_unknown`` (the statusline does not say when the provider measured the
quota), so they stay unknown unless the signed policy accepts that label for Claude.

A known capacity:
* ``projected_used_percent``: the highest forecast at reset over the pool's observed windows;
* ``state``: exhausted if the quota or any window is exhausted, otherwise conserve if the
  pool's policy mode says so, otherwise available;
* ``observed_utc`` is the OLDEST evidence time (the row and every paced window), so freshness
  is never overstated. ``valid_until_utc`` is the earliest of the binding expiry, a window
  reset, the pacer's sample age limit after the oldest paced sample, and the policy's age
  limit after the row.

Billing comes only from the signed policy: the collector never reports it. A paid pool is
reported as paid, and the router refuses paid capacity. Authority: none;
``execution_allowed`` is always False.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from tools.bridge_capacity_collector import POOL_SUBJECT_FIELDS, quota_details
from tools.bridge_pool_binding import POOL_ID_RE, _aware_utc
from tools.lane_profile_record import _utc
from tools.wd_capacity_pacing import MAX_SAMPLE_AGE_SECONDS
from tools.wd_composer_select import digest
from tools.wd_task_router import MEMBERS

SCHEMA = "wd.routing-capacity.v1"
POLICY_SCHEMA = "wd.routing-capacity-policy.v1"
OBSERVATION_SCHEMA = "wd.capacity-observation.v1"
FEATURE = "F19"
KNOWN, UNKNOWN = "known", "unknown"
PROVIDERS = ("codex", "claude")
FRESHNESS_LABELS = ("fresh", "provider_timestamp_unknown")
BILLING = ("included", "paid")
POOL_MODES = ("normal", "conserve")
PACED_VERDICTS = ("exhausted", "overrun", "on_pace", "underused")
WORKER_KEYS = frozenset({"worker", "profile_id", "subject"})
POLICY_KEYS = frozenset({"schema", "max_observation_age_seconds", "accepted_freshness", "pools"})
POOL_KEYS = frozenset({"billing", "mode"})
MAX_OBSERVATION_AGE_SECONDS = 3600
MAX_RESET_EPOCH = 253402300799  # the collector's own upper bound for a window reset


class _Unknown(Exception):
    """The capacity is unknown; carries the stable reasons."""

    def __init__(self, *reasons: str) -> None:
        super().__init__(reasons[0] if reasons else UNKNOWN)
        self.reasons = list(reasons)


def _need(condition: bool, *reasons: str) -> None:
    if not condition:
        raise _Unknown(*reasons)


def _label(value: Any, limit: int = 256) -> bool:
    return type(value) is str and 0 < len(value) <= limit and value.strip() == value


def _number(value: Any) -> bool:
    return type(value) in (int, float) and value == value and value not in (float("inf"), float("-inf"))


def _policy(signed: Any) -> tuple[dict, str]:
    _need(type(signed) is dict and set(signed) == {"policy", "sha256"}, "policy_invalid")
    policy, pin = signed["policy"], signed["sha256"]
    _need(type(policy) is dict and set(policy) == POLICY_KEYS and policy["schema"] == POLICY_SCHEMA,
          "policy_invalid")
    _need(type(pin) is str and digest(policy) == pin, "policy_digest_mismatch")
    age = policy["max_observation_age_seconds"]
    _need(type(age) is int and 0 < age <= MAX_OBSERVATION_AGE_SECONDS, "policy_invalid")
    accepted = policy["accepted_freshness"]
    _need(type(accepted) is dict and set(accepted) == set(PROVIDERS)
          and all(type(labels) is list and labels and len(set(labels)) == len(labels)
                  and all(label in FRESHNESS_LABELS for label in labels) for labels in accepted.values()),
          "policy_invalid")
    pools = policy["pools"]
    _need(type(pools) is dict and all(
        type(pool) is str and POOL_ID_RE.fullmatch(pool) is not None and type(entry) is dict
        and set(entry) == POOL_KEYS and entry["billing"] in BILLING and entry["mode"] in POOL_MODES
        for pool, entry in pools.items()), "policy_invalid")
    return policy, pin


def _worker(worker: Any) -> dict:
    _need(type(worker) is dict and set(worker) == WORKER_KEYS, "worker_invalid")
    _need(type(worker["worker"]) is str and worker["worker"] in MEMBERS, "worker_invalid")
    _need(_label(worker["profile_id"], 128) and _label(worker["subject"]), "worker_invalid")
    return worker


def _row(row: Any, worker: dict, policy: dict, now: datetime) -> tuple[str, str, datetime, datetime]:
    """provider, pool, observed time and binding expiry of a row this worker may use."""
    _need(type(row) is dict and row.get("schema") == OBSERVATION_SCHEMA, "observation_invalid")
    provider = row.get("provider")
    _need(type(provider) is str and provider in PROVIDERS, "observation_invalid")
    _need(row.get("reason") != "collection_failed", "observation_failed")
    _need(row.get(POOL_SUBJECT_FIELDS[provider]) == worker["subject"], "subject_mismatch")
    freshness = row.get("freshness")
    _need(type(freshness) is str and freshness in policy["accepted_freshness"][provider],
          "freshness_not_accepted")
    observed = _utc(row.get("observed_at"))
    _need(observed is not None, "observation_time_invalid")
    _need(observed <= now, "observation_from_the_future")
    _need(now - observed <= timedelta(seconds=policy["max_observation_age_seconds"]), "observation_stale")
    pool = row.get("account_pool")
    _need(row.get("pool_identity_state") == "verified_binding" and type(pool) is str
          and POOL_ID_RE.fullmatch(pool) is not None, "pool_unverified")
    binding = row.get("pool_binding")
    expires = _utc(binding.get("expires_at_utc")) if type(binding) is dict else None
    _need(expires is not None and now < expires, "pool_binding_expired")
    _need(pool in policy["pools"], "pool_not_in_policy")
    return provider, pool, observed, expires


def _windows(row: dict, provider: str, paced: Any, now: datetime) -> tuple[str, list[dict]]:
    """The quota state and one measured entry per observed window, or every reason it is not."""
    try:
        # The freshness label was already accepted by policy; the quota is read as the pacer does.
        quota_state, windows = quota_details(dict(row, freshness="fresh"), now)
    except Exception:  # noqa: BLE001 - an unreadable quota payload is unknown, never guessed
        raise _Unknown("quota_unreadable") from None
    _need(quota_state in ("observed_headroom", "exhausted") and bool(windows), "quota_unknown")
    _need(type(paced) is dict, "paced_invalid")
    reasons, measured = [], []
    for window in windows:
        key = "/".join((provider, str(window.get("limit_id")), str(window.get("name"))))
        entry, reset = paced.get(key), window.get("resets_at")
        if type(entry) is not dict:
            reasons.append("window_not_paced:" + key)
            continue
        forecast, sampled = entry.get("forecast_percent_at_reset"), _utc(entry.get("observed_at"))
        if entry.get("verdict") not in PACED_VERDICTS or not _number(forecast) or forecast < 0:
            reasons.append("window_rate_unknown:" + key)
        elif not (_number(reset) and now.timestamp() < reset <= MAX_RESET_EPOCH):
            reasons.append("window_reset_invalid:" + key)
        elif not (_number(entry.get("resets_at")) and entry["resets_at"] == reset):
            reasons.append("window_instance_mismatch:" + key)
        elif sampled is None or sampled > now:
            reasons.append("window_time_invalid:" + key)
        else:
            measured.append({"key": key, "forecast": float(forecast), "verdict": entry["verdict"],
                             "reset": datetime.fromtimestamp(reset, timezone.utc), "sampled": sampled})
    if reasons:
        raise _Unknown(*reasons)
    return quota_state, measured


def capacity_evidence(worker: Any, row: Any, paced: Any, signed_policy: Any, now: Any) -> dict:
    """One wd.routing-capacity.v1 record; see the module docstring."""
    record: dict[str, Any] = {"schema": SCHEMA, "feature": FEATURE, "worker": None, "profile_id": None,
                              "verdict": UNKNOWN, "reasons": [], "capacity": None, "policy_sha256": None,
                              "evidence_digest": None, "authority": "none", "execution_allowed": False}
    try:
        current = _aware_utc(now)
        _need(current is not None, "clock_invalid")
        record["evidence_digest"] = digest({"worker": worker, "row": row, "paced": paced,
                                            "signed_policy": signed_policy, "now": current.isoformat()})
        _need(record["evidence_digest"] is not None, "input_not_plain_data")
        own = _worker(worker)
        record.update(worker=own["worker"], profile_id=own["profile_id"])
        policy, pin = _policy(signed_policy)
        record["policy_sha256"] = pin
        provider, pool, observed, expires = _row(row, own, policy, current)
        quota_state, measured = _windows(row, provider, paced, current)
        oldest_sample = min(item["sampled"] for item in measured)
        valid_until = min([expires, oldest_sample + timedelta(seconds=MAX_SAMPLE_AGE_SECONDS),
                           observed + timedelta(seconds=policy["max_observation_age_seconds"])]
                          + [item["reset"] for item in measured])
        _need(current < valid_until, "evidence_expired")
        exhausted = quota_state == "exhausted" or any(item["verdict"] == "exhausted" for item in measured)
        state = ("exhausted" if exhausted else
                 "conserve" if policy["pools"][pool]["mode"] == "conserve" else "available")
        record.update(verdict=KNOWN, capacity={
            "observed_utc": min(observed, oldest_sample).isoformat(), "valid_until_utc": valid_until.isoformat(),
            "state": state, "billing": policy["pools"][pool]["billing"],
            "projected_used_percent": round(max(item["forecast"] for item in measured), 2),
            "profile_id": own["profile_id"], "pool": pool, "windows": sorted(item["key"] for item in measured),
            "policy_sha256": pin})
    except _Unknown as unknown:
        record.update(verdict=UNKNOWN, reasons=unknown.reasons, capacity=None)
    except Exception as exc:  # noqa: BLE001 - malformed input is unknown; it never crashes the caller
        record.update(verdict=UNKNOWN, reasons=["adapter_error:" + type(exc).__name__], capacity=None)
    return record
