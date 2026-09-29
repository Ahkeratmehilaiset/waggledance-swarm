#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Bridge v2: pure adapter from a caller-verified F0 Decision and the real F3 registry to F24 evidence.

``compose(...)`` builds one ``wd.composer-evidence.v1`` for ``wd_composer_select.select``,
or raises ``Refusal`` with a stable code. It reads no file, clock, environment,
network, provider or catalog, and it admits and actuates nothing.

F0: this is NOT a second F0 evaluator. The caller runs F0's ``evaluate("F24", ...)``
with its pins and injects the resulting ``Decision`` (the real type, not a dict), the
pins, and the parsed policy. The adapter only binds them: the Decision is enabled
for F24, and the policy's canonical digest equals both the trusted pin and the
Decision's digest. The Decision's revocation version meets the caller's high-water
mark, and ``now`` is before the policy expiry, so an old Decision cannot be replayed
past a newer revocation or past its policy. The ``f24_composer_rule`` bit and the
parameters come from that same bound policy, never from separate caller values.
A forged Decision is outside what a pure function can detect; the trusted caller
boundary is documented in the F15 contract, section 3.1.

F3: the adapter takes the registry's SOURCE BYTES, not a parsed dict. It hashes them
itself and requires the digest to equal the signed ``registry_source_sha256``, then
parses them with F3's own strict hooks and ``validate_registry``. The derived snapshot
has its own canonical digest (``registry_snapshot_sha256``). That digest depends on
``now``, because cells go stale, so it is derived and never signed.
A snapshot entry exists only for a FRESH measured observation (F3 ``observation_state``)
that meets all of these conditions:
* it is a quality observation of class ``general`` on the signed index unit, and
  ``provenance.reference`` is exactly the signed ``index_version`` (F3 v2 has no
  version field);
* its effort is exact, because a model-level value is never inherited by an effort;
* its uncertainty is an interval (exact, none_stated and unknown stay unknown);
* its subject is not an F3 candidate, because a placeholder model is never rated.
Historical, unverified, stale, unknown and duplicated cells stay absent. The selector
then treats them as unknown. Coding scores follow the same rules on unit
``coding_agent_index`` and the signed ``coding_index_version``.

Quota: a profile's pool receipt counts only if it names an F3 pool of the profile's
provider whose ``pool_state`` is ``verified`` at ``now`` (inside its TTL, not dated in
the future). The receipt must also have been observed inside that verification
window, so a later verification never upgrades an older receipt. A kept receipt gets
``valid_until_utc``, the earliest of its own age bound, the pool's verification
expiry, and any bound the caller declared. Any other pool receipt is dropped, so the
selector finds the quota unknown and the profile ineligible. F3 has no workload basis for a quota
cost, so no measured cost is comparable. Every ``measured_quota_cost`` is None,
whatever the caller sent, and API dollars are never used.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from tools.bridge_v2_activation import (HEX64, ActivationError, Decision, _is_int, _parse_utc, canonical_sha256,
                                        validate_policy)
from tools.lane_profile_record import _utc
from tools.wd_composer_select import EVIDENCE_SCHEMA, FEATURE, POLICY_BIT, SNAPSHOT_SCHEMA, digest
# pool_state is F3 af1d0ef8 (RCO2 S1: a verified pool expires). A base without it fails at import (fail-closed).
from tools.wd_model_registry import (MAX_REGISTRY_BYTES, SCHEMA_V2, UNITS, RegistryError, _constant, _pairs, _when,
                                     observation_state, pool_state, validate_registry)

PIN_KEYS = frozenset({"trusted_policy_sha256", "min_revocation_version"})
SELECTOR_KEYS = ("index_name", "index_version", "epsilon", "plausibility_bound", "max_evidence_age_seconds",
                 "max_score_age_days", "max_wait_seconds", "budget_mode", "planning_efforts")
# The F24 block of the signed policy's opaque parameters. This is a proposal for the signing packet.
PARAMETER_KEYS = frozenset(SELECTOR_KEYS) | {"registry_source_sha256", "coding_index_version"}
CODING_UNIT = "coding_agent_index"
_ERRORS = (ActivationError, RegistryError, ValueError, TypeError, AttributeError, KeyError, RecursionError)


class Refusal(Exception):
    """The inputs do not yield trustworthy F24 evidence; ``code`` is a stable reason."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _refuse(condition: bool, code: str) -> None:
    if not condition:
        raise Refusal(code)


def _hex64(value: Any) -> bool:
    return isinstance(value, str) and HEX64.fullmatch(value) is not None


def _bound_policy(decision: Any, pins: Any, policy: Any, now: datetime) -> tuple[dict, str]:
    """Bind the injected F0 Decision to the pins and the policy. It does not re-evaluate F0."""
    _refuse(type(decision) is Decision, "f0_decision_missing")
    _refuse(decision.feature == FEATURE, "f0_feature_mismatch")
    _refuse(decision.enabled is True, "f0_decision_disabled")  # F0's refusal always wins
    _refuse(isinstance(pins, dict) and set(pins) == PIN_KEYS and _hex64(pins["trusted_policy_sha256"])
            and _is_int(pins["min_revocation_version"]) and pins["min_revocation_version"] >= 1, "f0_pins_missing")
    try:
        policy_sha256 = canonical_sha256(validate_policy(policy))
    except _ERRORS:
        raise Refusal("f0_policy_invalid") from None
    # Never the policy's own claim: the pin comes from the operator-signed packet.
    _refuse(policy_sha256 == pins["trusted_policy_sha256"] == decision.policy_sha256, "f0_policy_unbound")
    _refuse(_is_int(decision.revocation_version)
            and decision.revocation_version >= pins["min_revocation_version"], "f0_revocation_rollback")
    try:
        expires = _parse_utc(policy["expires_utc"], "policy expires_utc")
    except _ERRORS:
        raise Refusal("f0_policy_unbounded") from None
    _refuse(now < expires, "f0_policy_expired")
    spec = policy["features"].get(FEATURE)
    _refuse(spec is not None and spec["enabled"] is True, "f0_feature_off")
    _refuse(policy["policy_bits"][POLICY_BIT] is True, "f0_policy_bit_off")
    return policy, policy_sha256


def _parameters(policy: dict) -> dict:
    parameters = policy["parameters"].get(FEATURE)
    _refuse(isinstance(parameters, dict) and set(parameters) == PARAMETER_KEYS, "f0_parameters_unknown")
    _refuse(parameters["index_name"] in UNITS["quality"] and parameters["index_name"] != CODING_UNIT
            and isinstance(parameters["index_version"], str) and parameters["index_version"]
            and _hex64(parameters["registry_source_sha256"])
            and (parameters["coding_index_version"] is None
                 or (isinstance(parameters["coding_index_version"], str) and parameters["coding_index_version"])),
            "f0_parameters_invalid")
    return parameters


def _registry(source: Any, parameters: dict) -> tuple[dict, str]:
    """Parse the registry source bytes. The adapter hashes them itself, so no dict can be paired with a wrong digest."""
    _refuse(type(source) is bytes and len(source) <= MAX_REGISTRY_BYTES, "f3_source_missing")
    source_sha256 = hashlib.sha256(source).hexdigest()
    _refuse(source_sha256 == parameters["registry_source_sha256"], "f3_source_unbound")
    try:
        registry = validate_registry(json.loads(source.decode("utf-8"), object_pairs_hook=_pairs,
                                                parse_constant=_constant))
    except _ERRORS:
        raise Refusal("f3_registry_invalid") from None
    _refuse(registry["schema"] == SCHEMA_V2, "f3_registry_not_v2")
    return registry, source_sha256


def _cells(registry: dict, parameters: dict, now: datetime) -> tuple[dict, dict]:
    """Fresh, exact-effort, interval-bounded quality cells on the signed index (and coding index)."""
    wanted = {("general", parameters["index_name"], parameters["index_version"]): 0}
    if parameters["coding_index_version"] is not None:
        wanted[("coding_agent", CODING_UNIT, parameters["coding_index_version"])] = 1
    columns: tuple[dict, dict] = ({}, {})
    ambiguous: set = set()
    for obs in registry["observations"]:
        subject = obs["subject"]
        column = wanted.get((obs["class"], obs["unit"], obs["provenance"]["reference"]))
        if (column is None or obs["kind"] != "quality" or subject["effort"] is None
                or f"{subject['provider']}/{subject['model']}" in registry["candidates"]
                or obs["uncertainty"]["kind"] != "interval" or observation_state(obs, now) != "fresh"):
            continue
        key = (subject["provider"], subject["model"], subject["effort"])
        if key in columns[column]:
            ambiguous.add((column, key))  # two current values for one cell: unknown, never a pick
            continue
        value = float(obs["value"])
        low, high = obs["uncertainty"]["low"], obs["uncertainty"]["high"]
        columns[column][key] = {"value": value, "uncertainty": float(max(value - low, high - value)),
                                "measured_on": obs["measured_at"][:10], "observation": obs["id"]}
    for column, key in ambiguous:
        columns[column].pop(key, None)
    return columns


def _snapshot(registry: dict, parameters: dict, now: datetime) -> dict:
    scores, coding = _cells(registry, parameters, now)
    entries = []
    for key, cell in sorted(scores.items()):
        secondary = coding.get(key)
        entries.append({"provider": key[0], "model": key[1], "effort": key[2], "score": cell["value"],
                        "uncertainty": cell["uncertainty"], "measured_on": cell["measured_on"],
                        "coding_score": None if secondary is None else secondary["value"],
                        "source": {"score_observation": cell["observation"],
                                   "coding_observation": None if secondary is None else secondary["observation"]}})
    return {"schema": SNAPSHOT_SCHEMA, "index_name": parameters["index_name"],
            "index_version": parameters["index_version"], "entries": entries}


def _pool_receipt(pool: Any, provider: Any, registry: dict, max_age: Any, now: datetime) -> dict | None:
    """The caller's pool receipt while F3 verifies its pool, bounded by both expiries; otherwise None (unknown)."""
    if not isinstance(pool, dict) or not isinstance(pool.get("pool_id"), str):
        return None
    known = registry["pools"].get(pool["pool_id"])
    if known is None or known["provider"] != provider or pool_state(known, now) != "verified":
        return None  # unverified, stale, future-dated or unknown pools never count
    observed = _utc(pool.get("observed_utc"))
    verified_at = _when(known["measured_at"], "pool measured_at")
    if observed is None or not verified_at <= observed <= now or type(max_age) is not int or max_age <= 0:
        return None  # a later verification never upgrades an older receipt
    try:
        bounds = [observed + timedelta(seconds=max_age), verified_at + timedelta(seconds=known["ttl_seconds"])]
    except OverflowError:
        return None
    if "valid_until_utc" in pool:
        declared = _utc(pool["valid_until_utc"])
        if declared is None:
            return None
        bounds.append(declared)  # a caller's own bound can only shorten the validity
    return dict(pool, valid_until_utc=min(bounds).strftime("%Y-%m-%dT%H:%M:%SZ"))


def _profile(profile: dict, registry: dict, parameters: dict, now: datetime) -> dict:
    """The caller's profile with the quota facts F3 cannot confirm set to unknown. It can only lose authority."""
    pool = _pool_receipt(profile.get("pool"), profile.get("provider"), registry,
                         parameters["max_evidence_age_seconds"], now)
    return dict(profile, pool=pool, measured_quota_cost=None)


def compose(*, decision: Any, pins: Any, policy: Any, registry_source: Any, profiles: Any, task: Any, now: Any,
            previous_snapshot: Any = None) -> dict:
    """Return the F24 evidence envelope and both registry digests. Raises Refusal and confers no authority."""
    _refuse(isinstance(now, datetime) and now.utcoffset() is not None, "time_unknown")
    try:
        now = now.astimezone(timezone.utc).replace(microsecond=0)  # one instant for F3 and for the selector
    except (OverflowError, ValueError):
        raise Refusal("time_unknown") from None  # an extreme aware time has no UTC form
    policy, policy_sha256 = _bound_policy(decision, pins, policy, now)
    parameters = _parameters(policy)
    registry, source_sha256 = _registry(registry_source, parameters)
    _refuse(isinstance(profiles, list) and all(isinstance(p, dict) for p in profiles), "profiles_malformed")
    snapshot = _snapshot(registry, parameters, now)
    snapshot_sha256 = digest(snapshot)
    evidence = {
        "schema": EVIDENCE_SCHEMA,
        "now_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "f0": {"feature": FEATURE, "enabled": True, "reason": decision.reason, "policy_sha256": policy_sha256,
               "revocation_version": decision.revocation_version},
        "policy_bits": {"policy_sha256": policy_sha256, POLICY_BIT: True},
        "parameters": dict({k: parameters[k] for k in SELECTOR_KEYS}, policy_sha256=policy_sha256,
                           registry_sha256=snapshot_sha256),
        "registry_snapshot": snapshot,
        "task": task,
        "profiles": [_profile(p, registry, parameters, now) for p in profiles],
    }
    if previous_snapshot is not None:
        # The caller's previously emitted snapshot. It can only make entries unranked (the refresh checks).
        evidence["previous_snapshot"] = previous_snapshot
    return {"evidence": evidence, "registry_source_sha256": source_sha256,
            "registry_snapshot_sha256": snapshot_sha256}
