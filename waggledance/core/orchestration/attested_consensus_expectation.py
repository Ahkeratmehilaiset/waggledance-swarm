# SPDX-License-Identifier: BUSL-1.1
"""Immutable pins for independently sourced consensus-gate expectations.

Eleven runtime gate bindings are derived from one verified activation
admission intent.  Only the final closed attestation-log head is accepted as a
separate input because it cannot exist when that intent opens the collection
window.  Pins are deterministic, append-only, advisory-only facts: they do not
authenticate their writer, activate a candidate, change routing, or grant
runtime authority.
"""

from __future__ import annotations

import re
from typing import Optional

from waggledance.core.capabilities.activation_admission_intent import (
    ActivationAdmissionIntentError,
    parse_activation_admission_intent,
)
from waggledance.core.capabilities.activation_contracts import MAX_GENERATION
from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest
from waggledance.core.orchestration.attested_consensus_shadow import (
    GATE_EXPECTATION_KEYS,
    AttestedConsensusShadowError,
    parse_attested_consensus_shadow_expectations,
)

ATTESTED_CONSENSUS_EXPECTATION_SCHEMA = (
    "wd.attested_consensus_expectation.v1"
)
ATTESTED_CONSENSUS_EXPECTATION_HEAD_DIGEST_DOMAIN = (
    "wd.attested_consensus_expectation_head.digest.v1"
)
INITIAL_PREVIOUS_EXPECTATION_HEAD_DIGEST = "sha256:" + "0" * 64

_NO_AUTHORITY_FLAGS = {
    "advisory_only": True,
    "authority_granted": False,
    "activation_performed": False,
    "routing_influence_applied": False,
}

ATTESTED_CONSENSUS_EXPECTATION_CORE_KEYS = frozenset(
    {
        "schema_version",
        "generation",
        "previous_expectation_head_digest",
        "admission_challenge_digest",
        "expected_bindings",
        *_NO_AUTHORITY_FLAGS,
    }
)
ATTESTED_CONSENSUS_EXPECTATION_KEYS = (
    ATTESTED_CONSENSUS_EXPECTATION_CORE_KEYS | {"expectation_head_digest"}
)

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class AttestedConsensusExpectationError(ValueError):
    """An expectation pin is malformed, stale, or self-inconsistent."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _refuse(reason: str, message: str) -> None:
    raise AttestedConsensusExpectationError(reason, message)


def _exact_dict(
    value: object, keys: frozenset[str], label: str
) -> dict[str, object]:
    if type(value) is not dict:
        _refuse(f"{label}_type", f"{label} must be an exact dict")
    if dict.__len__(value) != len(keys):
        _refuse(f"{label}_keyset", f"{label} has a non-exact keyset")
    copied = value.copy()
    if any(type(key) is not str for key in copied) or set(copied) != keys:
        _refuse(f"{label}_keyset", f"{label} has a non-exact keyset")
    return copied


def _digest(value: object, label: str) -> str:
    if type(value) is not str or not _SHA256.fullmatch(value):
        _refuse(label, f"{label} must be a lowercase sha256 digest")
    return value


def _generation(value: object) -> int:
    if type(value) is not int or not 0 <= value <= MAX_GENERATION:
        _refuse("generation", "generation must be an exact bounded integer")
    return value


def _expected_bindings_from_intent(
    intent: dict[str, object], closed_log_head: str
) -> dict[str, object]:
    current = intent["expected_current_pointer"]
    proposed = intent["proposed_pointer"]
    bindings = {
        "expected_consensus_policy_digest": intent["consensus_policy_digest"],
        "expected_activation_scope_digest": intent["activation_scope_digest"],
        "expected_query_digest": intent["query_digest"],
        "expected_current_bundle_digest": current["bundle_digest"],
        "expected_current_activation_head_digest": current[
            "activation_head_digest"
        ],
        "expected_current_store_revision": current["store_revision"],
        "expected_proposed_bundle_digest": proposed["bundle_digest"],
        "expected_proposed_activation_head_digest": proposed[
            "activation_head_digest"
        ],
        "expected_proposed_store_revision": proposed["store_revision"],
        "expected_trust_registry_head_digest": intent[
            "trust_registry_head_digest"
        ],
        "expected_attestation_log_base_head_digest": intent[
            "attestation_log_base_head_digest"
        ],
        "expected_attestation_log_closed_head_digest": closed_log_head,
    }
    try:
        return parse_attested_consensus_shadow_expectations(bindings)
    except AttestedConsensusShadowError as exc:
        raise AttestedConsensusExpectationError(
            f"expected_bindings:{exc.reason}",
            "derived gate expectations were refused",
        ) from exc


def _closed_log_head_from_intent(
    intent: dict[str, object], value: object
) -> str:
    closed_head = _digest(
        value,
        "expected_attestation_log_closed_head_digest",
    )
    if closed_head == intent["attestation_log_base_head_digest"]:
        _refuse(
            "closed_log_head_not_advanced",
            "closed attestation-log head must advance beyond the base head",
        )
    return closed_head


def _normalize_core(value: object) -> dict[str, object]:
    core = _exact_dict(
        value,
        ATTESTED_CONSENSUS_EXPECTATION_CORE_KEYS,
        "expectation",
    )
    if (
        type(core["schema_version"]) is not str
        or core["schema_version"] != ATTESTED_CONSENSUS_EXPECTATION_SCHEMA
    ):
        _refuse("schema_version", "expectation pin schema refused")
    generation = _generation(core["generation"])
    previous = _digest(
        core["previous_expectation_head_digest"],
        "previous_expectation_head_digest",
    )
    if generation == 0 and previous != INITIAL_PREVIOUS_EXPECTATION_HEAD_DIGEST:
        _refuse(
            "initial_previous_expectation_head",
            "generation zero must use the initial predecessor sentinel",
        )
    if generation > 0 and previous == INITIAL_PREVIOUS_EXPECTATION_HEAD_DIGEST:
        _refuse(
            "noninitial_previous_expectation_head",
            "noninitial pins must bind a real predecessor",
        )
    challenge = _digest(
        core["admission_challenge_digest"], "admission_challenge_digest"
    )
    try:
        bindings = parse_attested_consensus_shadow_expectations(
            core["expected_bindings"]
        )
    except AttestedConsensusShadowError as exc:
        raise AttestedConsensusExpectationError(
            f"expected_bindings:{exc.reason}",
            "expectation bindings were refused",
        ) from exc
    if (
        bindings["expected_attestation_log_closed_head_digest"]
        == bindings["expected_attestation_log_base_head_digest"]
    ):
        _refuse(
            "closed_log_head_not_advanced",
            "closed attestation-log head must advance beyond the base head",
        )
    normalized: dict[str, object] = {
        "schema_version": ATTESTED_CONSENSUS_EXPECTATION_SCHEMA,
        "generation": generation,
        "previous_expectation_head_digest": previous,
        "admission_challenge_digest": challenge,
        "expected_bindings": bindings,
    }
    for field, expected in _NO_AUTHORITY_FLAGS.items():
        if type(core[field]) is not bool or core[field] is not expected:
            _refuse(field, f"{field} must remain {expected!r}")
        normalized[field] = expected
    return normalized


def derive_attested_consensus_expectation_head_digest(core: object) -> str:
    """Derive the domain-separated head for one exact normalized pin core."""

    normalized = _normalize_core(core)
    return sha256_digest(
        {
            "domain": ATTESTED_CONSENSUS_EXPECTATION_HEAD_DIGEST_DOMAIN,
            "expectation": normalized,
        }
    )


def build_attested_consensus_expectation(
    *,
    generation: int,
    previous_expectation_head_digest: str,
    activation_admission_intent: object,
    expected_attestation_log_closed_head_digest: str,
) -> dict[str, object]:
    """Build one pin from a verified intent and independent closed-log head."""

    try:
        intent = parse_activation_admission_intent(
            activation_admission_intent
        )
    except ActivationAdmissionIntentError as exc:
        raise AttestedConsensusExpectationError(
            f"intent:{exc.reason}", "activation admission intent refused"
        ) from exc
    closed_head = _closed_log_head_from_intent(
        intent, expected_attestation_log_closed_head_digest
    )
    bindings = _expected_bindings_from_intent(intent, closed_head)
    core = _normalize_core(
        {
            "schema_version": ATTESTED_CONSENSUS_EXPECTATION_SCHEMA,
            "generation": generation,
            "previous_expectation_head_digest": (
                previous_expectation_head_digest
            ),
            "admission_challenge_digest": intent[
                "admission_challenge_digest"
            ],
            "expected_bindings": bindings,
            **_NO_AUTHORITY_FLAGS,
        }
    )
    return {
        **core,
        "expectation_head_digest": (
            derive_attested_consensus_expectation_head_digest(core)
        ),
    }


def parse_attested_consensus_expectation(
    value: object,
) -> dict[str, object]:
    """Parse and privately copy one exact immutable expectation pin."""

    pin = _exact_dict(
        value,
        ATTESTED_CONSENSUS_EXPECTATION_KEYS,
        "expectation",
    )
    core = _normalize_core(
        {
            key: pin[key] for key in ATTESTED_CONSENSUS_EXPECTATION_CORE_KEYS
        }
    )
    claimed = _digest(pin["expectation_head_digest"], "expectation_head_digest")
    expected = derive_attested_consensus_expectation_head_digest(core)
    if claimed != expected:
        _refuse(
            "expectation_head_digest_mismatch",
            "expectation head digest mismatches content",
        )
    return {**core, "expectation_head_digest": claimed}


def canonicalize_attested_consensus_expectation(value: object) -> bytes:
    """Return canonical bytes for one fully verified expectation pin."""

    return canonical_json_bytes(parse_attested_consensus_expectation(value))


def expectation_bindings_from_attested_consensus_expectation(
    value: object,
) -> dict[str, object]:
    """Return a private exact 12-field runtime gate expectation mapping."""

    parsed = parse_attested_consensus_expectation(value)
    bindings = parsed["expected_bindings"]
    return {key: bindings[key] for key in GATE_EXPECTATION_KEYS}


def verify_attested_consensus_expectation(
    value: object,
) -> tuple[bool, Optional[str]]:
    try:
        parse_attested_consensus_expectation(value)
    except AttestedConsensusExpectationError as exc:
        return False, exc.reason
    return True, None


def verify_attested_consensus_expectation_bindings(
    value: object,
    *,
    activation_admission_intent: object,
    expected_attestation_log_closed_head_digest: str,
) -> tuple[bool, Optional[str]]:
    """Rebind a pin to its independently supplied intent and closed head."""

    try:
        pin = parse_attested_consensus_expectation(value)
        intent = parse_activation_admission_intent(
            activation_admission_intent
        )
        closed_head = _closed_log_head_from_intent(
            intent, expected_attestation_log_closed_head_digest
        )
        expected_bindings = _expected_bindings_from_intent(intent, closed_head)
    except AttestedConsensusExpectationError as exc:
        return False, exc.reason
    except ActivationAdmissionIntentError as exc:
        return False, f"intent:{exc.reason}"
    if pin["admission_challenge_digest"] != intent[
        "admission_challenge_digest"
    ]:
        return False, "admission_challenge_binding"
    if pin["expected_bindings"] != expected_bindings:
        return False, "expected_bindings_binding"
    return True, None


def verify_attested_consensus_expectation_transition(
    current: object,
    proposed: object,
    *,
    expected_current_expectation_head_digest: str,
) -> tuple[bool, Optional[str]]:
    """Verify one strict scope-local append transition without doing I/O."""

    try:
        expected_current = _digest(
            expected_current_expectation_head_digest,
            "expected_current_expectation_head_digest",
        )
        current_pin = parse_attested_consensus_expectation(current)
    except AttestedConsensusExpectationError as exc:
        return False, exc.reason
    if current_pin["expectation_head_digest"] != expected_current:
        return False, "stale_current_expectation_head"
    try:
        proposed_pin = parse_attested_consensus_expectation(proposed)
    except AttestedConsensusExpectationError as exc:
        return False, exc.reason
    current_generation = current_pin["generation"]
    if current_generation == MAX_GENERATION:
        return False, "generation_exhausted"
    if proposed_pin["generation"] != current_generation + 1:
        return False, "generation_step"
    if (
        proposed_pin["previous_expectation_head_digest"]
        != current_pin["expectation_head_digest"]
    ):
        return False, "previous_expectation_head_binding"
    current_scope = current_pin["expected_bindings"][
        "expected_activation_scope_digest"
    ]
    proposed_scope = proposed_pin["expected_bindings"][
        "expected_activation_scope_digest"
    ]
    if proposed_scope != current_scope:
        return False, "activation_scope_binding"
    if (
        proposed_pin["admission_challenge_digest"]
        == current_pin["admission_challenge_digest"]
    ):
        return False, "admission_challenge_replay"
    return True, None


__all__ = [
    "ATTESTED_CONSENSUS_EXPECTATION_CORE_KEYS",
    "ATTESTED_CONSENSUS_EXPECTATION_HEAD_DIGEST_DOMAIN",
    "ATTESTED_CONSENSUS_EXPECTATION_KEYS",
    "ATTESTED_CONSENSUS_EXPECTATION_SCHEMA",
    "INITIAL_PREVIOUS_EXPECTATION_HEAD_DIGEST",
    "AttestedConsensusExpectationError",
    "build_attested_consensus_expectation",
    "canonicalize_attested_consensus_expectation",
    "derive_attested_consensus_expectation_head_digest",
    "expectation_bindings_from_attested_consensus_expectation",
    "parse_attested_consensus_expectation",
    "verify_attested_consensus_expectation",
    "verify_attested_consensus_expectation_bindings",
    "verify_attested_consensus_expectation_transition",
]
