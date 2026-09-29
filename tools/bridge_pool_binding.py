#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Bridge v2 F3: pure pool-binding adapter. It decides; it never acts or writes.

Map c099c211 section 4, F3 row: the capacity collector's ``account_pool`` comes only
from VALIDATED provenance, and unknown stays unknown. The collector's raw auth context
(``auth_context_id``: a digest of the CODEX_HOME path and the visible account shape) and
a Claude native session id are only SUBJECTS that a receipt may name. Neither is ever a
pool, and nothing here derives a pool from them.

``bind_pool(observation, receipt, registry, *, verifier, now)`` returns one
``wd.pool-binding-decision.v1`` record. It carries a pool id and
``pool_identity_state: "verified_binding"`` only when every check passes:

* the observation is a successful ``wd.capacity-observation.v1`` exactly as the collector
  produces it (no pre-filled pool), and its observed limit ids are known;
* the receipt has the exact ``wd.pool-binding-receipt.v1`` shape, and its provenance is a
  measuring kind (operator reading, local measurement or an F21 receipt), never a
  transcription, documentation or benchmark;
* the receipt is valid at ``now`` (issued no more than 5 minutes ahead, not expired, a
  lifetime of at most 24 hours), and the observation was made inside that window;
* the provider, the subject (Codex auth context or Claude native session) and every
  observed limit id match the receipt;
* the pool exists in a valid v2 model registry (RCO1 af1d0ef8 or later), belongs to the
  same provider, is ``verified`` at ``now`` by the registry's own ``pool_state`` (a verified
  pool past its TTL is stale, a future-dated one unknown: both refuse), and its
  ``limit_id`` (when set) is covered by the receipt. The decision's ``expires_at_utc`` is
  the EARLIER of the receipt expiry and the pool's freshness expiry (``measured_at`` +
  ``ttl_seconds``), so a stored binding never outlives the registry verification;
* last, an injected verifier returns exactly ``True`` for a copy of the receipt. The
  receipt's authenticity comes from the trusted caller's reviewed verifier; none ships
  with this module, so nothing binds by default.

Anything else gives ``account_pool: None``, ``pool_identity_state: "unverified"`` and one
stable reason code. The receipt's free-text provenance reference is never copied into
the decision. ``execution_allowed`` is always false and ``authority_effect`` is none.
Not runtime-tested: written under the operator's no-runs directive (2026-09-29).
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any, Callable

from tools.wd_model_registry import MEASURING_KINDS, SCHEMA_V2, pool_state, validate_registry

RECEIPT_SCHEMA = "wd.pool-binding-receipt.v1"
DECISION_SCHEMA = "wd.pool-binding-decision.v1"
OBSERVATION_SCHEMA = "wd.capacity-observation.v1"
VERIFIED = "verified_binding"
UNVERIFIED = "unverified"

RECEIPT_KEYS = frozenset({"schema", "receipt_id", "provider", "pool", "limit_ids", "subject",
                          "issued_at_utc", "expires_at_utc", "provenance"})
SUBJECT_KEYS = frozenset({"kind", "id"})
PROVENANCE_KEYS = frozenset({"kind", "reference", "observer"})
# The subject a receipt must name per provider, and the pool state the collector itself
# writes for that provider (anything else was not produced by the collector).
SUBJECT_KINDS = {"codex": "auth_context", "claude": "native_session"}
COLLECTOR_POOL_STATES = {"codex": "unverified_auth_context", "claude": "unknown"}
# Every Claude statusline window is reported under this limit id by the collector.
CLAUDE_LIMIT_ID = "claude"

MAX_RECEIPT_LIFETIME = timedelta(hours=24)
FUTURE_SKEW = timedelta(minutes=5)
MAX_LIMIT_IDS = 16
HEX32 = re.compile(r"[0-9a-f]{32}")
HEX64 = re.compile(r"[0-9a-f]{64}")
POOL_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
LIMIT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}")
SESSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})")
# The registry's measured_at format: a UTC date (read as its start) or a UTC second.
REGISTRY_WHEN_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}Z)?")


class Refused(Exception):
    """A check failed; carries one stable reason code (never input text)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise Refused(code)


def _utc(value: Any) -> datetime | None:
    """Strict ISO-8601 with a timezone, as UTC; anything else is None."""
    if type(value) is not str or len(value) > 40 or not TIMESTAMP_RE.fullmatch(value):
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def canonical_sha256(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def observed_limit_ids(observation: dict) -> frozenset | None:
    """The provider limit ids an observation's quota payload covers; None when unknown.

    Mirrors the collector's quota_details: a Codex bucket counts only when its key equals
    its own limitId; every Claude window is limit id ``claude``. No payload, an empty map
    or any malformed bucket is unknown, never "no limits"."""
    payload = observation.get("payload")
    provider = observation.get("provider")
    if type(payload) is not dict:
        return None
    if provider == "codex":
        if "rateLimitsByLimitId" in payload:
            buckets = payload["rateLimitsByLimitId"]
            if type(buckets) is not dict or not buckets:
                return None
            ids = []
            for key, bucket in buckets.items():
                if type(bucket) is not dict or bucket.get("limitId") != key:
                    return None
                ids.append(key)
        else:
            bucket = payload.get("rateLimits")
            if type(bucket) is not dict:
                return None
            ids = [bucket.get("limitId")]
    elif provider == "claude":
        windows = payload.get("rate_limits")
        if type(windows) is not dict or not windows:
            return None
        ids = [CLAUDE_LIMIT_ID]
    else:
        return None
    if not all(type(item) is str and LIMIT_ID_RE.fullmatch(item) for item in ids):
        return None
    return frozenset(ids)


def _observation(observation: Any) -> tuple[str, str, datetime, frozenset]:
    _require(type(observation) is dict and observation.get("schema") == OBSERVATION_SCHEMA,
             "observation_invalid")
    provider = observation.get("provider")
    _require(type(provider) is str and provider in SUBJECT_KINDS, "observation_invalid")
    _require(observation.get("reason") != "collection_failed", "observation_failed")
    # The collector never fills a pool; an observation that arrives with one is not trusted.
    _require(observation.get("account_pool") is None, "observation_already_claims_a_pool")
    _require(observation.get("pool_identity_state") == COLLECTOR_POOL_STATES[provider],
             "observation_pool_state_unexpected")
    subject = observation.get("auth_context_id" if provider == "codex" else "native_thread_id")
    _require(type(subject) is str and subject != "", "observation_subject_missing")
    observed = _utc(observation.get("observed_at"))
    _require(observed is not None, "observation_time_invalid")
    limits = observed_limit_ids(observation)
    _require(limits is not None, "observation_limits_unknown")
    return provider, subject, observed, limits


def _receipt(receipt: Any) -> dict:
    _require(type(receipt) is dict, "receipt_missing")
    _require(set(receipt) == RECEIPT_KEYS, "receipt_shape_invalid")
    _require(receipt["schema"] == RECEIPT_SCHEMA, "receipt_schema_invalid")
    _require(type(receipt["receipt_id"]) is str and HEX32.fullmatch(receipt["receipt_id"]) is not None,
             "receipt_shape_invalid")
    provider = receipt["provider"]
    _require(type(provider) is str and provider in SUBJECT_KINDS, "receipt_provider_invalid")
    _require(type(receipt["pool"]) is str and POOL_ID_RE.fullmatch(receipt["pool"]) is not None,
             "receipt_shape_invalid")
    limits = receipt["limit_ids"]
    _require(type(limits) is list and 0 < len(limits) <= MAX_LIMIT_IDS
             and all(type(item) is str and LIMIT_ID_RE.fullmatch(item) for item in limits)
             and len(set(limits)) == len(limits), "receipt_limits_invalid")
    subject = receipt["subject"]
    _require(type(subject) is dict and set(subject) == SUBJECT_KEYS, "receipt_subject_invalid")
    _require(subject["kind"] == SUBJECT_KINDS[provider], "receipt_subject_invalid")
    pattern = HEX64 if subject["kind"] == "auth_context" else SESSION_RE
    _require(type(subject["id"]) is str and pattern.fullmatch(subject["id"]) is not None,
             "receipt_subject_invalid")
    provenance = receipt["provenance"]
    _require(type(provenance) is dict and set(provenance) == PROVENANCE_KEYS, "receipt_provenance_invalid")
    _require(type(provenance["kind"]) is str and provenance["kind"] in MEASURING_KINDS,
             "receipt_provenance_not_measured")
    _require(type(provenance["reference"]) is str and provenance["reference"].strip() != ""
             and len(provenance["reference"]) <= 512, "receipt_provenance_invalid")
    _require(provenance["observer"] is None or (type(provenance["observer"]) is str
                                                and 0 < len(provenance["observer"]) <= 128),
             "receipt_provenance_invalid")
    issued, expires = _utc(receipt["issued_at_utc"]), _utc(receipt["expires_at_utc"])
    _require(issued is not None and expires is not None, "receipt_time_invalid")
    _require(issued < expires <= issued + MAX_RECEIPT_LIFETIME, "receipt_lifetime_invalid")
    return receipt


def _pool_fresh_until(pool: dict) -> datetime:
    """measured_at + ttl_seconds of a pool the registry already called verified."""
    measured = pool.get("measured_at")
    ttl = pool.get("ttl_seconds")
    _require(type(measured) is str and REGISTRY_WHEN_RE.fullmatch(measured) is not None
             and type(ttl) is int and ttl > 0, "pool_freshness_unknown")
    try:
        if "T" in measured:
            start = datetime.strptime(measured[:-1] + "+0000", "%Y-%m-%dT%H:%M:%S%z")
        else:
            start = datetime.strptime(measured + "+0000", "%Y-%m-%d%z")
        return start + timedelta(seconds=ttl)
    except (ValueError, OverflowError):
        raise Refused("pool_freshness_unknown") from None


def _pool(registry: Any, pool_id: str, provider: str, now: datetime) -> tuple[dict, datetime]:
    """The verified pool and the moment its registry verification expires."""
    try:
        validate_registry(registry)
    except Exception:  # noqa: BLE001 - any registry defect is a refusal, never a guess
        raise Refused("registry_invalid") from None
    _require(registry["schema"] == SCHEMA_V2, "registry_has_no_pools")
    pool = registry["pools"].get(pool_id)
    _require(pool is not None, "pool_not_in_registry")
    _require(pool["provider"] == provider, "pool_provider_mismatch")
    # The registry's own rule: verified only inside the pool's TTL; stale and unknown refuse.
    try:
        state = pool_state(pool, now)
    except Exception:  # noqa: BLE001 - an unreadable pool date is unknown, never verified
        state = "unknown"
    _require(state == "verified", "pool_state_" + (state if state in ("stale", "unverified") else "unknown"))
    fresh_until = _pool_fresh_until(pool)
    _require(now < fresh_until, "pool_state_stale")  # belt and braces with pool_state
    return pool, fresh_until


def bind_pool(observation: Any, receipt: Any, registry: Any, *,
              verifier: Callable[[dict], Any] | None, now: datetime) -> dict:
    """One deterministic decision for one observation and one receipt; never raises."""
    decision: dict[str, Any] = {
        "schema": DECISION_SCHEMA, "account_pool": None, "pool_identity_state": UNVERIFIED,
        "reason": None, "provider": None, "subject_kind": None, "subject_id": None,
        "receipt_id": None, "receipt_sha256": None, "provenance_kind": None,
        "expires_at_utc": None, "receipt_expires_at_utc": None, "pool_fresh_until_utc": None,
        "execution_allowed": False, "authority_effect": "none"}
    try:
        _require(isinstance(now, datetime) and now.tzinfo is not None, "clock_invalid")
        current = now.astimezone(timezone.utc)
        provider, subject, observed, limits = _observation(observation)
        decision.update(provider=provider, subject_kind=SUBJECT_KINDS[provider], subject_id=subject)
        body = _receipt(receipt)
        issued, expires = _utc(body["issued_at_utc"]), _utc(body["expires_at_utc"])
        decision.update(receipt_id=body["receipt_id"], receipt_sha256=canonical_sha256(body),
                        provenance_kind=body["provenance"]["kind"], expires_at_utc=expires.isoformat())
        _require(body["provider"] == provider, "provider_mismatch")
        _require(body["subject"]["id"] == subject, "subject_mismatch")
        _require(issued - current <= FUTURE_SKEW, "receipt_not_yet_valid")
        _require(current < expires, "receipt_expired")
        _require(observed - current <= FUTURE_SKEW, "observation_from_the_future")
        _require(issued <= observed < expires, "observation_outside_receipt_window")
        _require(limits <= set(body["limit_ids"]), "limit_not_covered")
        pool, pool_fresh_until = _pool(registry, body["pool"], provider, current)
        _require(pool["limit_id"] is None or pool["limit_id"] in body["limit_ids"], "pool_limit_not_covered")
        # The binding lasts only while BOTH the receipt and the registry verification hold.
        decision.update(receipt_expires_at_utc=expires.isoformat(),
                        pool_fresh_until_utc=pool_fresh_until.isoformat(),
                        expires_at_utc=min(expires, pool_fresh_until).isoformat())
        # Authenticity last, on a copy: the answer must be exactly True.
        _require(verifier is not None and callable(verifier), "verifier_missing")
        try:
            verdict = verifier(copy.deepcopy(body))
        except Exception:  # noqa: BLE001 - a failing verifier is a refusal, never a pass
            raise Refused("verifier_failed") from None
        _require(verdict is True, "verifier_refused")
        decision.update(account_pool=body["pool"], pool_identity_state=VERIFIED)
    except Refused as refusal:
        decision.update(account_pool=None, pool_identity_state=UNVERIFIED, reason=refusal.code)
    except Exception as exc:  # noqa: BLE001 - malformed input refuses; it never crashes the caller
        decision.update(account_pool=None, pool_identity_state=UNVERIFIED,
                        reason="binding_error:" + type(exc).__name__)
    return decision
