#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Bridge v2: pure adapter from caller-verified F0 inputs to the F15 switch evidence.

``switch_activation(f0, now)`` returns the signed F15 parameters and the binding
facts, or raises ``Refusal`` with a stable code. It adds no authority and reads no
file, clock, environment variable, network or port. It takes the F0 inputs that
the trusted caller already loaded:

* ``pins``: caller-owned, never taken from the config.
  - ``trusted_policy_sha256`` comes from the operator-signed packet.
  - ``expected_head`` and ``expected_tree`` come from the deployed bundle.
  - ``min_revocation_version`` is the caller's persisted high-water mark.
* ``document``: the activation config ``{policy, signature}`` as parsed by the caller.
* ``revocation``: the revocation state the caller loaded.
* ``decision``: F0's ``evaluate("F15", ...)`` Decision, as a dict of its fields.

F0's refusal always wins: a Decision that is not enabled refuses before anything
else is read. An enabled Decision is never enough on its own. The adapter re-derives
the pure part of F0's decision from the same inputs, using F0's own validators
(tools/bridge_v2_activation.py), and the Decision must agree with it (same policy
digest and revocation version).

The adapter cannot check these, and they stay with F0 and the caller:
* the kill-switch variable (F0 reads the environment);
* the file reads themselves;
* the authenticity of the pins.

Default OFF: the shipped policy has F15 disabled and no signature.
"""
from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timedelta
from typing import Any

from tools.bridge_v2_activation import (FEATURE_NAME, HEX40, HEX64, MAX_FUTURE_SKEW, REVOCATION_KEYS,
                                        REVOCATION_SCHEMA, ActivationError, Decision, _blocked_dependency,
                                        _is_int, _parse_utc, canonical_sha256, validate_policy,
                                        validate_signature)

FEATURE = "F15"
F0_KEYS = frozenset({"pins", "document", "revocation", "decision"})
PIN_KEYS = frozenset({"trusted_policy_sha256", "expected_head", "expected_tree", "min_revocation_version"})
DOCUMENT_KEYS = frozenset({"policy", "signature"})
DECISION_KEYS = frozenset(f.name for f in fields(Decision))
# The F15 block of the signed policy's opaque ``parameters`` object. The F0 policy
# at 482c3f0e has parameters {}, so this layout is a proposal for the signing packet.
PARAMETER_KEYS = frozenset({"tick_seconds", "hysteresis_percent", "evidence_max_age_seconds", "budget_mode"})
_F0_ERRORS = (ActivationError, ValueError, TypeError, AttributeError, KeyError, RecursionError)


class Refusal(Exception):
    """The F0 inputs do not enable F15; ``code`` is a stable reason."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _refuse(condition: bool, code: str) -> None:
    if not condition:
        raise Refusal(code)


def _exact(value: Any, keys: frozenset, code: str) -> dict:
    _refuse(isinstance(value, dict) and set(value) == keys, code)
    return value


def _hex(value: Any, pattern) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _pins(value: Any) -> dict:
    pins = _exact(value, PIN_KEYS, "f0_pins_missing")
    _refuse(_hex(pins["trusted_policy_sha256"], HEX64) and _hex(pins["expected_head"], HEX40)
            and _hex(pins["expected_tree"], HEX40)
            and _is_int(pins["min_revocation_version"]) and pins["min_revocation_version"] >= 1,
            "f0_pins_missing")
    return pins


def _policy(value: Any, pins: dict, now: datetime) -> tuple[dict, str]:
    """F0 load_policy plus the evaluate() time checks, without the file read (activation :279-298, :388-395)."""
    document = _exact(value, DOCUMENT_KEYS, "f0_document_malformed")
    try:
        policy = validate_policy(document["policy"])
        digest = canonical_sha256(policy)
    except _F0_ERRORS:
        raise Refusal("f0_policy_invalid") from None
    # The trusted digest is the caller's pin, never signature.policy_sha256 (the file must not authorize itself).
    _refuse(digest == pins["trusted_policy_sha256"], "f0_policy_unbound")
    _refuse(document["signature"] is not None, "f0_policy_unsigned")
    _refuse(policy["expires_utc"] is not None, "f0_policy_unbounded")
    try:
        signature = validate_signature(document["signature"], digest, expected_head=pins["expected_head"],
                                       expected_tree=pins["expected_tree"])
        policy_expires = _parse_utc(policy["expires_utc"], "policy expires_utc")
        signature_expires = _parse_utc(signature["expires_utc"], "signature.expires_utc")
        signed = _parse_utc(signature["signed_utc"], "signature.signed_utc")
    except _F0_ERRORS:
        raise Refusal("f0_signature_invalid") from None
    _refuse(signature_expires <= policy_expires, "f0_signature_outlives_policy")
    _refuse(now < policy_expires and now < signature_expires, "f0_expired")
    _refuse(signed - now <= MAX_FUTURE_SKEW, "f0_signature_from_the_future")
    return policy, digest


def _revocation(value: Any, policy: dict, digest: str, pins: dict, now: datetime) -> dict:
    """F0 load_revocation without the file read (activation :305-328); the freshness window is required."""
    state = _exact(value, frozenset(REVOCATION_KEYS), "f0_revocation_malformed")
    _refuse(state["schema"] == REVOCATION_SCHEMA and _is_int(state["version"]) and state["version"] >= 1
            and type(state["frozen"]) is bool and isinstance(state["revoked"], list)
            and all(_hex(r, FEATURE_NAME) for r in state["revoked"]), "f0_revocation_malformed")
    _refuse(state["version"] >= pins["min_revocation_version"], "f0_revocation_rollback")
    _refuse(state["policy_sha256"] == digest, "f0_revocation_unbound")
    try:
        updated = _parse_utc(state["updated_utc"], "revocation updated_utc")
    except _F0_ERRORS:
        raise Refusal("f0_revocation_malformed") from None
    _refuse(updated - now <= MAX_FUTURE_SKEW, "f0_revocation_from_the_future")
    age = policy["revocation_max_age_seconds"]
    _refuse(age is not None and now - updated <= timedelta(seconds=age), "f0_revocation_stale")
    return state


def switch_activation(f0: Any, now: Any) -> dict:
    """Verified F15 activation facts and signed parameters. Raises Refusal; never enables on its own."""
    _refuse(isinstance(now, datetime) and now.utcoffset() is not None, "f0_time_unknown")
    f0 = _exact(f0, F0_KEYS, "f0_inputs_missing")
    decision = _exact(f0["decision"], DECISION_KEYS, "f0_decision_missing")
    _refuse(decision["feature"] == FEATURE, "f0_feature_mismatch")
    _refuse(decision["enabled"] is True, "f0_decision_disabled")  # F0's refusal always wins
    pins = _pins(f0["pins"])
    policy, digest = _policy(f0["document"], pins, now)
    _refuse(decision["policy_sha256"] == digest, "f0_decision_unbound")
    state = _revocation(f0["revocation"], policy, digest, pins, now)
    _refuse(decision["revocation_version"] == state["version"], "f0_decision_unbound")
    _refuse(state["frozen"] is False, "f0_frozen")
    _refuse(FEATURE not in state["revoked"], "f0_revoked")
    spec = policy["features"].get(FEATURE)
    _refuse(spec is not None and spec["enabled"] is True, "f0_feature_off")
    _refuse(_blocked_dependency(FEATURE, policy["features"], set(state["revoked"])) is None,
            "f0_dependency_blocked")
    parameters = policy["parameters"].get(FEATURE)
    _refuse(isinstance(parameters, dict) and set(parameters) == PARAMETER_KEYS, "f0_parameters_unknown")
    return {"feature": FEATURE, "policy_sha256": digest, "revocation_version": state["version"],
            "head": pins["expected_head"], "tree": pins["expected_tree"], "parameters": dict(parameters)}
