#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Bridge v2 F15: pure lane-profile switch policy. It decides; it never acts.

``decide(evidence)`` returns one ``wd.switch-decision.v1`` record for one proposed
switch of one lane to one catalog profile. It implements the authority split and
the decision record of docs/architecture/BRIDGE_V2_SWITCH_INTERFACE_CONTRACT.md
and the mechanical guardrails of docs/architecture/BRIDGE_NEXT_WORK_PLAN_20260928.md
section 2.2 (plan commit c099c211).

Pure: every input arrives in ``evidence``, including the clock value, the F0
activation snapshot, the revocation state, the port-verified catalog signature,
the quota samples and the relaunch history. The module reads no file, environment
variable, network or clock, calls no port and writes no intent. The same evidence
always gives the same record, and ``inputs_digest`` lets a reviewer recompute it.

Verdicts:
* ``switch``: every gate passed. It is the only verdict an intent may carry.
* ``stay``: no change is warranted (same profile, raise inside the hysteresis
  margin, a contest the incumbent keeps, a duplicate of an earlier intent).
* ``park``: something is unknown, stale, disabled, revoked, frozen or not yet
  allowed. Unknown is never replaced by a default or a guess.
* ``operator_required``: only the operator may make this change (outside the
  envelope, below a floor, lowering or weakening a reviewer).

A decision has no authority. ``execution_allowed`` is always false; the executor
re-checks F0, the revocation state and the safe boundary at dispatch and before
every side effect. A pacing forecast alone never produces ``switch``: a verified
requester must propose the switch, and every gate must pass.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
import re
from typing import Any

from tools.lane_profile_binding import _base_model
from tools.lane_profile_catalog import REVIEWER_LANES, classify_transition, effective_mode, is_signed
from tools.lane_profile_record import _utc
from tools.wd_capacity_pacing import pace_windows
from tools.wd_lane_profile_planner import profile_for_observation
from tools.wd_lane_relaunch import ABORT, PROCEED, check_request, check_safe_boundary

SCHEMA = "wd.switch-decision.v1"
EVIDENCE_SCHEMA = "wd.switch-evidence.v1"
ACTIVATION_SCHEMA = "wd.bridge-v2-activation.v1"
# The F0 flag name is a design: F0 (tools/bridge_v2_activation.py) does not exist yet.
FEATURE = "switch_policy"

STAY, SWITCH, PARK, OPERATOR = "stay", "switch", "park", "operator_required"
VERDICTS = (STAY, SWITCH, PARK, OPERATOR)

MEMBERS = ("codex-lead-1", "codex-tools-1", "fable-5", "claude-rco-1", "claude-rco-2")
PRINCIPALS = ("codex-lead-1", "fable-5")
OPERATOR_CHANNELS = ("operator_terminal", "operator_window")
# Plan 2.2 precedence: the operator first, then the principals, then the other members.
PRECEDENCE_OPERATOR, PRECEDENCE_PRINCIPAL, PRECEDENCE_MEMBER = 0, 1, 2

# Plan 2.2 guardrail 1: the projection must stay within these trip lines.
TRIP_LINES = {"steady": 70.0, "burst": 90.0, "sprint": 95.0}
# Plan 2.2: tick length and hysteresis margin are signed parameters; so is the
# evidence freshness bound. None has a default here.
SIGNED_PARAMETERS = ("tick_seconds", "hysteresis_percent", "evidence_max_age_seconds", "budget_mode")

# Only the operator may: leave the envelope, go below a floor, lower a reviewer.
OPERATOR_ONLY_TRANSITIONS = ("target_not_allowed", "below_floor", "reviewer_lowering")

HEX64 = re.compile(r"[0-9a-f]{64}")
HEX32 = re.compile(r"[0-9a-f]{32}")


class _Stop(Exception):
    """A gate decided; carries the verdict and its stable reasons."""

    def __init__(self, verdict: str, *reasons: str):
        super().__init__(verdict)
        self.verdict = verdict
        self.reasons = list(reasons)


def _require(condition: bool, verdict: str, *reasons: str) -> None:
    if not condition:
        raise _Stop(verdict, *reasons)


def inputs_digest(evidence: Any) -> str | None:
    """SHA-256 of the canonical JSON of the evidence; None if it is not canonical JSON."""
    try:
        canonical = json.dumps(evidence, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        return None
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def _number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _positive_int(value: Any) -> bool:
    return type(value) is int and value > 0


def _fresh(block: Any, now: datetime, max_age: int, what: str) -> dict:
    """A separately observed evidence block: a dict whose observed_utc is fresh and not ahead."""
    _require(isinstance(block, dict), PARK, what + "_unknown")
    observed = _utc(block.get("observed_utc"))
    _require(observed is not None, PARK, what + "_time_unknown")
    _require(observed <= now, PARK, what + "_from_the_future")
    _require((now - observed).total_seconds() <= max_age, PARK, what + "_stale")
    return block


def _activation(evidence: dict, now: datetime) -> dict:
    """F0 gate: trusted, signed, unexpired, enabled, not revoked or frozen; returns the parameters."""
    activation = evidence.get("activation")
    _require(isinstance(activation, dict) and activation.get("schema") == ACTIVATION_SCHEMA,
             PARK, "activation_unknown")
    provenance = activation.get("provenance")
    _require(isinstance(provenance, dict) and provenance.get("trusted") is True
             and isinstance(provenance.get("sha256"), str) and HEX64.fullmatch(provenance["sha256"]) is not None,
             PARK, "activation_provenance_untrusted")
    _require(activation.get("signed") is True, PARK, "activation_unsigned")
    expires = _utc(activation.get("expires_utc"))
    _require(expires is not None, PARK, "activation_expiry_unknown")
    _require(now < expires, PARK, "activation_expired")
    features = activation.get("features")
    _require(isinstance(features, dict) and features.get(FEATURE) is True, PARK, "feature_disabled")
    parameters = activation.get("parameters")
    _require(isinstance(parameters, dict), PARK, "parameters_unknown")
    for name in SIGNED_PARAMETERS:
        _require(name in parameters, PARK, "parameter_unknown:" + name)
    _require(_positive_int(parameters["tick_seconds"]), PARK, "parameter_invalid:tick_seconds")
    _require(_positive_int(parameters["evidence_max_age_seconds"]), PARK,
             "parameter_invalid:evidence_max_age_seconds")
    _require(_number(parameters["hysteresis_percent"]) and 0 <= parameters["hysteresis_percent"] <= 100,
             PARK, "parameter_invalid:hysteresis_percent")
    _require(parameters["budget_mode"] in TRIP_LINES, PARK, "parameter_invalid:budget_mode")
    # The durable revocation and freeze state is checked on every decision (map F0).
    revocation = _fresh(evidence.get("revocation"), now, parameters["evidence_max_age_seconds"], "revocation")
    _require(revocation.get("activation_sha256") == provenance["sha256"], PARK, "revocation_binding_mismatch")
    _require(type(revocation.get("version")) is int and revocation["version"] >= 0, PARK, "revocation_version_unknown")
    state = revocation.get("state")
    _require(state in ("clear", "revoked", "frozen"), PARK, "revocation_state_unknown")
    _require(state != "revoked", PARK, "activation_revoked")
    _require(state != "frozen", PARK, "operator_freeze")
    return parameters


def _catalog(evidence: dict) -> dict:
    """A qualified catalog: signed, port-verified signature, pinned digest, effective mode auto."""
    catalog = evidence.get("catalog")
    _require(isinstance(catalog, dict), PARK, "catalog_unknown")
    digest = evidence.get("catalog_sha256")
    _require(isinstance(digest, str) and HEX64.fullmatch(digest) is not None, PARK, "catalog_digest_unknown")
    _require(is_signed(catalog), PARK, "catalog_unsigned")
    # The signature is verified by a port before the policy runs; the policy only reads the result.
    _require(evidence.get("catalog_signature_verified") is True, PARK, "catalog_signature_unverified")
    mode = effective_mode(catalog)
    _require(mode == "auto", PARK, "catalog_mode_" + str(mode))
    return catalog


def _requester(evidence: dict) -> tuple[str, int]:
    """The verified requester and its precedence; a relay or an unverified label gives nothing."""
    requester = evidence.get("requester")
    _require(isinstance(requester, dict) and requester.get("verified") is True, PARK, "requester_unverified")
    agent, channel = requester.get("agent"), requester.get("channel")
    if agent == "operator":
        _require(channel in OPERATOR_CHANNELS, PARK, "operator_channel_unverified")
        return agent, PRECEDENCE_OPERATOR
    _require(agent in MEMBERS and channel == "member", PARK, "requester_not_a_member")
    return agent, PRECEDENCE_PRINCIPAL if agent in PRINCIPALS else PRECEDENCE_MEMBER


def _windows(profile: dict, paced: dict) -> list[dict]:
    """The paced windows of every quota limit of a profile; unknown parks (no default)."""
    windows = []
    for limit in profile["limits"]:
        for window in limit["windows"]:
            key = f"{profile['provider']}/{limit['id']}/{window}"
            entry = paced.get(key)
            _require(isinstance(entry, dict), PARK, "quota_unknown:" + key)
            _require(entry.get("verdict") != "unknown", PARK,
                     "quota_unknown:" + key + ":" + str(entry.get("reason")))
            windows.append(entry)
    return windows


def _samples(raw: Any, now: datetime) -> list[dict]:
    """Quota samples in the pacer's shape; a malformed row parks rather than being dropped."""
    _require(isinstance(raw, list) and raw, PARK, "quota_samples_unknown")
    samples = []
    for row in raw:
        _require(isinstance(row, dict), PARK, "quota_sample_malformed")
        observed = _utc(row.get("observed_at"))
        duration = row.get("duration_minutes")
        _require(observed is not None and observed <= now
                 and all(isinstance(row.get(k), str) and row[k] for k in ("provider", "limit_id", "window"))
                 and _number(row.get("used_percent")) and _number(row.get("resets_at"))
                 and (duration is None or _positive_int(duration)),
                 PARK, "quota_sample_malformed")
        samples.append({"provider": row["provider"], "limit_id": row["limit_id"], "window": row["window"],
                        "used_percent": float(row["used_percent"]), "resets_at": float(row["resets_at"]),
                        "duration_minutes": duration, "observed_at": observed})
    return samples


def _precedence_of(entry: dict) -> int | None:
    value = entry.get("precedence")
    return value if type(value) is int and value in (0, 1, 2) else None


def _contest(evidence: dict, lane: str, target: str, precedence: int, intent_class: str) -> None:
    """Deterministic contest rule (plan 2.2 guardrail 5); needs no operator step."""
    intent = evidence.get("intent")
    _require(isinstance(intent, dict) and isinstance(intent.get("intent_id"), str)
             and HEX32.fullmatch(intent["intent_id"]) is not None
             and _utc(intent.get("created_utc")) is not None, PARK, "intent_identity_unknown")
    own_key = (precedence, _utc(intent["created_utc"]), intent["intent_id"])
    competing = evidence.get("competing_intents")
    _require(isinstance(competing, list), PARK, "contention_unknown")
    for other in competing:
        _require(isinstance(other, dict) and other.get("lane") == lane
                 and isinstance(other.get("intent_id"), str) and HEX32.fullmatch(other["intent_id"]) is not None
                 and _utc(other.get("created_utc")) is not None and _precedence_of(other) is not None
                 and isinstance(other.get("target_profile"), str),
                 PARK, "contention_unknown")
        if other["intent_id"] == intent["intent_id"]:
            continue
        other_key = (other["precedence"], _utc(other["created_utc"]), other["intent_id"])
        if other["target_profile"] == target:
            # The same change requested twice: the earliest by (precedence, time, id) carries it.
            _require(own_key < other_key, STAY, "duplicate_of_earlier_intent")
        elif other["precedence"] <= precedence and intent_class != "conserve":
            # An equal-or-higher-precedence intent wants something else: the incumbent stays.
            raise _Stop(STAY, "contested_incumbent_stays")


def _decide(evidence: Any, context: dict) -> None:
    _require(isinstance(evidence, dict) and evidence.get("schema") == EVIDENCE_SCHEMA, PARK, "evidence_schema_unknown")
    now = _utc(evidence.get("now_utc"))
    _require(now is not None, PARK, "clock_unknown")
    parameters = _activation(evidence, now)
    max_age = parameters["evidence_max_age_seconds"]
    catalog = _catalog(evidence)

    lane, target = evidence.get("lane"), evidence.get("target_profile")
    _require(isinstance(lane, str) and lane in MEMBERS and lane in catalog["lanes"], PARK, "lane_unknown")
    context["lane"] = lane
    profiles = catalog["capacity_policy"]["profiles"]
    _require(isinstance(target, str) and target in profiles, PARK, "target_profile_unknown")
    context["target_profile"] = target
    agent, precedence = _requester(evidence)
    context["requester_precedence"] = precedence

    target_spec = profiles[target]
    # Plan 2.2: Grok is outside automatic switching (its pool cannot be read).
    _require(target_spec.get("provider") != "grok", PARK, "grok_pool_unreadable")
    # Plan 2.2: fast, priority and pay-per-use credit tiers are never in the envelope.
    _require(target_spec.get("billing") == "subscription", OPERATOR, "billing_outside_envelope")
    _require(target_spec.get("approved") is True, OPERATOR, "target_not_qualified")

    binding = evidence.get("binding")
    _require(isinstance(binding, dict) and binding.get("session_identity") == "valid"
             and binding.get("lane") == lane, PARK, "current_profile_unverified")
    current = profile_for_observation(catalog, lane, _base_model(binding.get("observed_model_raw")),
                                      binding.get("observed_effort"))
    _require(current is not None, PARK, "current_profile_not_in_catalog")
    context["current_profile"] = current

    transition = classify_transition(catalog, lane, current, target)
    if transition["verdict"] == "park":
        verdict = OPERATOR if transition["reason"] in OPERATOR_ONLY_TRANSITIONS else PARK
        raise _Stop(verdict, transition["reason"])
    _require(transition["verdict"] != "same", STAY, "no_change")
    direction = transition["verdict"]  # raise or lower
    context["direction"] = direction

    # Plan 2.2 guardrail 4: no member changes an RCO that is reviewing that member's work.
    reviews = evidence.get("active_reviews")
    _require(isinstance(reviews, list) and all(isinstance(r, dict) for r in reviews), PARK, "active_reviews_unknown")
    if lane in REVIEWER_LANES and agent != "operator":
        _require(not any(r.get("reviewer") == lane and r.get("author") == agent for r in reviews),
                 OPERATOR, "requester_under_review_by_lane")

    # Separately known evidence: authentication, an observed turn, the target pool binding.
    auth = _fresh(evidence.get("auth"), now, max_age, "auth")
    _require(auth.get("lane") == lane and auth.get("state") == "authenticated", PARK, "auth_not_proven")
    turn = _fresh(evidence.get("turn"), now, max_age, "turn")
    _require(turn.get("lane") == lane and turn.get("state") == "successful_turn_observed", PARK, "turn_not_observed")
    pool = f"{target_spec['provider']}/{target_spec['account_pool']}"
    context["pool"] = pool
    bindings = evidence.get("pool_bindings")
    _require(isinstance(bindings, dict), PARK, "pool_binding_unknown")
    pool_binding = _fresh(bindings.get(pool), now, max_age, "pool_binding")
    _require(pool_binding.get("binding") == "verified", PARK, "pool_binding_unverified")

    # Quota, paced by the existing pure pacer from the injected samples and clock.
    paced = pace_windows(_samples(evidence.get("quota_samples"), now), now=now)
    target_windows = _windows(target_spec, paced)
    current_windows = _windows(profiles[current], paced)
    _require(not any(w["verdict"] in ("overrun", "exhausted") for w in target_windows),
             PARK, "target_pool_at_risk")

    # The executor classifies the intent itself; caller labels are ignored (plan 2.2 guardrail 3).
    trip = TRIP_LINES[parameters["budget_mode"]]
    conserve = direction == "lower" and any(
        w["verdict"] in ("overrun", "exhausted")
        or (_number(w.get("forecast_percent_at_reset")) and w["forecast_percent_at_reset"] > trip)
        for w in current_windows)
    intent_class = ("revert" if target == evidence.get("last_verified_profile")
                    else "conserve" if conserve else direction)
    context["intent_class"] = intent_class

    # Atomic pool admission: measured forecast plus the pool's outstanding reservations.
    reservations = evidence.get("pool_reservations")
    _require(isinstance(reservations, dict) and _number(reservations.get(pool)) and reservations[pool] >= 0,
             PARK, "pool_reservations_unknown")
    long_windows = [w for w in target_windows if w.get("short") is False]
    _require(bool(long_windows), PARK, "long_window_unknown")
    for window in long_windows:
        forecast = window.get("forecast_percent_at_reset")
        _require(_number(forecast), PARK, "forecast_unknown")
        projection = forecast + reservations[pool]
        _require(projection <= trip, PARK, "budget_trip_line")
        if direction == "raise":
            # Hysteresis for raises: a raise needs headroom beyond the margin.
            _require(projection <= trip - parameters["hysteresis_percent"], STAY, "raise_within_hysteresis")
    changes = evidence.get("pool_changes_this_tick")
    _require(isinstance(changes, dict) and type(changes.get(pool)) is int and changes[pool] >= 0,
             PARK, "pool_tick_unknown")
    _require(changes[pool] == 0, PARK, "pool_tick_used")

    # Rate: the catalog's signed per-lane and fleet budgets and cooldown (check_request).
    # A revert skips the dwell only; it still counts toward the hourly caps.
    rate = check_request(catalog, {"lane": lane, "current_profile": current, "target_profile": target},
                         evidence.get("relaunch_history"), now=now)
    if rate["verdict"] == ABORT:
        raise _Stop(STAY, *rate["reasons"])
    if rate["verdict"] != PROCEED:
        blocking = [r for r in rate["reasons"] if not (intent_class == "revert" and r == "lane_cooldown")]
        if blocking or rate["verdict"] != "park" or not rate["reasons"]:
            raise _Stop(OPERATOR if rate.get("operator_ack_required") else PARK,
                        *("rate:" + r for r in (blocking or rate["reasons"])))

    # Safe boundary from the existing pure check, on the injected lane measurement.
    boundary = check_safe_boundary(evidence.get("boundary_state"), lane=lane, now=now)
    if boundary["verdict"] != PROCEED:
        raise _Stop(PARK, *("boundary:" + r for r in boundary["reasons"]))

    _contest(evidence, lane, target, precedence, intent_class)
    raise _Stop(SWITCH, "all_gates_passed", "transition:" + transition["reason"])


def decide(evidence: Any) -> dict:
    """One deterministic decision record; never raises, never acts."""
    digest = inputs_digest(evidence)
    context: dict[str, Any] = {"lane": None, "current_profile": None, "target_profile": None,
                               "direction": None, "intent_class": None, "pool": None,
                               "requester_precedence": None}
    if digest is None:
        verdict, reasons = PARK, ["evidence_not_canonical_json"]
    else:
        try:
            _decide(evidence, context)
            verdict, reasons = PARK, ["no_gate_decided"]  # unreachable: every path raises _Stop
        except _Stop as stop:
            verdict, reasons = stop.verdict, stop.reasons
        except Exception as exc:  # noqa: BLE001 - malformed evidence parks; it never crashes the caller
            verdict, reasons = PARK, ["evidence_malformed", type(exc).__name__]
    if verdict not in VERDICTS or not reasons:
        verdict, reasons = PARK, ["decision_invalid"]
    return {"schema": SCHEMA, "verdict": verdict, "reasons": reasons, "inputs_digest": digest,
            "execution_allowed": False, "authority": "none", **context}
