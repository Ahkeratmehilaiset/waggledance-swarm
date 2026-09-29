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
A cell is a quality observation of class ``general`` on the signed index unit whose
``provenance.reference`` is exactly the signed ``index_version`` (F3 v2 has no version
field), for a subject that is not an F3 candidate (a placeholder model is never rated).
Ambiguity is decided FIRST, over every FRESH measured observation (F3
``observation_state``) that F3 ``_column`` puts in the same quality column of the row:
the class decides the column, not the unit or the index version, exactly as F3
``model_table`` groups it (RCO1 S1; RCO2 residual, Lead option a). A second fresh value
of any uncertainty, unit or version, or a fresh model-level value (F3 fans that out to
every effort), makes the cell unknown, so a newer value can never be hidden by a filter
and leave an older one ranked. Only a genuinely separate F3 column (another class, such
as ``task:<name>``) does not compete. Only then must the one remaining observation be on
the signed unit and version, have an exact effort (a model-level value is never
inherited) and an interval uncertainty (exact, none_stated and unknown stay unknown).
Historical, unverified, stale, unknown and ambiguous cells stay absent. The selector
then treats them as unknown. Coding scores follow the same rules on unit
``coding_agent_index`` and the signed ``coding_index_version``.

Quota: a profile's pool receipt counts only if all of these hold:
* It names an F3 pool of the profile's provider whose ``pool_state`` is ``verified`` at
  ``now`` (inside its TTL, not dated in the future). The verification must carry a
  full timestamp: a date-only ``measured_at`` reads as 00:00Z and cannot order a
  same-day receipt (RCO1 S2).
* F3 names that pool for this profile through a FRESH pool-membership observation
  (F3 ``model_table`` speaks only through a fresh membership; RCO1 S3). The
  membership must be effort-exact, and every fresh membership that applies
  (effort-exact or model-level) must name the same pool. Anything else is unknown.
* The receipt was observed inside the verification window, so a later verification
  never upgrades an older receipt.
A kept receipt gets ``valid_until_utc``, the earliest of its own age bound, the pool's
verification expiry, and any bound the caller declared, truncated to whole seconds. A
receipt whose bound is not after ``now`` is dropped (RCO1 S4). Any other pool receipt is
dropped, so the selector finds the quota unknown and the profile ineligible. F3 has no
workload basis for a quota cost, so no measured cost is comparable. Every
``measured_quota_cost`` is None, whatever the caller sent, and API dollars are never used.
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
# _column is F3's own quality-column key (class only), imported for exact parity with model_table's grouping.
from tools.wd_model_registry import (MAX_REGISTRY_BYTES, SCHEMA_V2, UNITS, RegistryError, _column, _constant, _pairs,
                                     _when, observation_state, pool_state, validate_registry)

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
    """Fresh, exact-effort, interval-bounded quality cells on the signed index (and coding index).

    Ambiguity is decided the way F3 model_table groups a row (RCO1 S1; RCO2 23:27:08Z residual, Lead
    9465d68e option a): over EVERY fresh observation that F3 ``_column`` maps to the same quality column
    for (provider, model, effort), plus the model-level ones F3 offers to every effort, BEFORE the signed
    unit/version filter. A value on another index, unit or version of the same class therefore competes
    and unranks the cell; only a genuinely separate F3 column (another class) does not."""
    signed = {"quality_general": (0, parameters["index_name"], parameters["index_version"])}
    if parameters["coding_index_version"] is not None:
        signed["quality_coding_agent"] = (1, CODING_UNIT, parameters["coding_index_version"])
    fresh: dict = {}
    model_level: set = set()
    for obs in registry["observations"]:
        subject = obs["subject"]
        if (obs["kind"] != "quality" or _column(obs) not in signed
                or f"{subject['provider']}/{subject['model']}" in registry["candidates"]
                or observation_state(obs, now) != "fresh"):
            continue
        column = _column(obs)
        if subject["effort"] is None:
            model_level.add((column, subject["provider"], subject["model"]))  # F3 offers it to every effort
        else:
            fresh.setdefault((column, subject["provider"], subject["model"], subject["effort"]), []).append(obs)
    columns: tuple[dict, dict] = ({}, {})
    for (column, provider, model, effort), found in fresh.items():
        if len(found) != 1 or (column, provider, model) in model_level:
            continue  # two current values in one F3 cell, whatever their index, version or uncertainty: unknown
        obs = found[0]
        index, unit, version = signed[column]
        if obs["unit"] != unit or obs["provenance"]["reference"] != version:
            continue  # the one current value is not on the signed index/version: unknown, never a rank
        if obs["uncertainty"]["kind"] != "interval":
            continue  # exact, none_stated and unknown stay unknown
        value = float(obs["value"])
        low, high = obs["uncertainty"]["low"], obs["uncertainty"]["high"]
        columns[index][(provider, model, effort)] = {
            "value": value, "uncertainty": float(max(value - low, high - value)),
            "measured_on": obs["measured_at"][:10], "observation": obs["id"]}
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


def _membership_names(pool_id: str, profile: dict, registry: dict, now: datetime) -> bool:
    """True only if F3 names ``pool_id`` for this profile through a FRESH membership (RCO1 S3).

    F3 ``model_table`` resolves a row's pool from its membership observations: the best state
    wins, the latest wins within it, and a model-level membership is offered to every effort.
    Conservatively: an effort-exact fresh membership must exist, and EVERY fresh membership that
    applies (effort-exact or model-level) must name the same pool, so no newer fresh membership
    can name another pool. Stale, historical, unverified and unknown memberships never name one."""
    applies = [obs for obs in registry["observations"]
               if obs["kind"] == "pool" and obs["subject"]["provider"] == profile.get("provider")
               and obs["subject"]["model"] == profile.get("model")
               and obs["subject"]["effort"] in (None, profile.get("effort"))
               and observation_state(obs, now) == "fresh"]
    return {obs["value"] for obs in applies} == {pool_id} \
        and any(obs["subject"]["effort"] is not None for obs in applies)


def _pool_receipt(pool: Any, profile: dict, registry: dict, max_age: Any, now: datetime) -> dict | None:
    """The caller's pool receipt while F3 verifies its pool AND names it for this profile, bounded by both
    expiries; otherwise None (unknown)."""
    if not isinstance(pool, dict) or not isinstance(pool.get("pool_id"), str):
        return None
    known = registry["pools"].get(pool["pool_id"])
    if known is None or known["provider"] != profile.get("provider") or pool_state(known, now) != "verified":
        return None  # unverified, stale, future-dated or unknown pools never count
    if "T" not in known["measured_at"]:
        return None  # a date-only verification reads as 00:00Z: it cannot order a same-day receipt (RCO1 S2)
    if not _membership_names(pool["pool_id"], profile, registry, now):
        return None  # F3 does not name this pool for this profile (RCO1 S3)
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
    until = min(bounds).replace(microsecond=0)  # the emitted whole-second bound is the one compared
    if until <= now:
        return None  # already expired: a dead receipt never reaches the selector (RCO1 S4)
    return dict(pool, valid_until_utc=until.strftime("%Y-%m-%dT%H:%M:%SZ"))


def _profile(profile: dict, registry: dict, parameters: dict, now: datetime) -> dict:
    """The caller's profile with the quota facts F3 cannot confirm set to unknown. It can only lose authority."""
    pool = _pool_receipt(profile.get("pool"), profile, registry, parameters["max_evidence_age_seconds"], now)
    return dict(profile, pool=pool, measured_quota_cost=None)


def compose(*, decision: Any, pins: Any, policy: Any, registry_source: Any, profiles: Any, task: Any, now: Any,
            previous_snapshot: Any = None) -> dict:
    """Return the F24 evidence envelope and both registry digests. Raises Refusal and confers no authority."""
    _refuse(type(now) is datetime, "time_unknown")  # the exact type, like the Decision (RCO1 N1)
    try:
        _refuse(now.utcoffset() is not None, "time_unknown")
        now = now.astimezone(timezone.utc).replace(microsecond=0)  # one instant for F3 and for the selector
    except (OverflowError, ValueError, TypeError):
        # an extreme aware time has no UTC form; a tzinfo with an invalid offset fails visibly too
        raise Refusal("time_unknown") from None
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
