# SPDX-License-Identifier: BUSL-1.1
"""Pure, scope-bound intent for reviewing one activation CAS transition.

The intent is the common challenge presented to evidence reviewers.  It binds
one exact current pointer, one exact proposed successor, the Genesis-derived
activation scope, and the external policy/trust/log heads under which the
review takes place.  It performs no I/O, authenticates none of those external
heads, grants no authority, and never applies the proposed transition.
"""

from __future__ import annotations

import re
from typing import Optional

from waggledance.core.capabilities.activation_contracts import MAX_GENERATION
from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.orchestration.evidence_consensus import (
    MAX_REQUIRED_SUPPORT,
)

INTENT_SCHEMA = "wd.activation_admission_intent.v1"
CHALLENGE_DIGEST_DOMAIN = "wd.activation_admission_challenge.digest.v1"
DECISION_DIGEST_DOMAIN = "wd.activation_admission_decision.digest.v1"

CURRENT_POINTER_KEYS = frozenset(
    {"bundle_digest", "activation_head_digest", "store_revision"}
)
PROPOSED_POINTER_KEYS = frozenset(
    {
        "bundle_digest",
        "activation_head_digest",
        "store_revision",
        "previous_bundle_digest",
        "previous_activation_head_digest",
    }
)

_NON_AUTHORITY_FLAGS = {
    "observation_only": True,
    "authority_granted": False,
    "activation_performed": False,
    "routing_influence_applied": False,
}

INTENT_CORE_KEYS = frozenset(
    {
        "schema_version",
        "activation_scope_digest",
        "query_digest",
        "candidate_digest",
        "activation_head_digest",
        "expected_current_pointer",
        "proposed_pointer",
        "trust_registry_head_digest",
        "attestation_log_head_digest",
        "consensus_policy_digest",
        "required_independent_support",
        *_NON_AUTHORITY_FLAGS,
    }
)
INTENT_KEYS = INTENT_CORE_KEYS | {
    "admission_challenge_digest",
    "decision_digest",
}

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class ActivationAdmissionIntentError(ValueError):
    """The admission intent is malformed, stale, or self-inconsistent."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _refuse(reason: str, message: str) -> None:
    raise ActivationAdmissionIntentError(reason, message)


def _exact_dict(value: object, keys: frozenset[str], label: str) -> dict:
    if type(value) is not dict:
        _refuse(f"{label}_type", f"{label} must be an exact dict")
    if dict.__len__(value) > len(keys):
        _refuse(f"{label}_keyset", f"{label} keyset")
    copied = value.copy()
    if set(copied) != keys or any(type(key) is not str for key in copied):
        _refuse(f"{label}_keyset", f"{label} keyset")
    return copied


def _digest(value: object, label: str) -> str:
    if type(value) is not str or not _SHA256.fullmatch(value):
        _refuse(label, f"{label} must be lowercase sha256:<64 hex>")
    return value


def _revision(value: object, label: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_GENERATION:
        _refuse(label, f"{label} must be an exact bounded int")
    return value


def _support(value: object) -> int:
    if (
        type(value) is not int
        or not 1 <= value <= MAX_REQUIRED_SUPPORT
    ):
        _refuse(
            "required_independent_support",
            "required_independent_support is outside its exact bound",
        )
    return value


def _current_pointer(value: object) -> dict[str, object]:
    pointer = _exact_dict(value, CURRENT_POINTER_KEYS, "expected_current_pointer")
    return {
        "bundle_digest": _digest(
            pointer["bundle_digest"], "expected_current_bundle_digest"
        ),
        "activation_head_digest": _digest(
            pointer["activation_head_digest"],
            "expected_current_activation_head_digest",
        ),
        "store_revision": _revision(
            pointer["store_revision"], "expected_current_store_revision"
        ),
    }


def _proposed_pointer(value: object) -> dict[str, object]:
    pointer = _exact_dict(value, PROPOSED_POINTER_KEYS, "proposed_pointer")
    return {
        "bundle_digest": _digest(
            pointer["bundle_digest"], "proposed_bundle_digest"
        ),
        "activation_head_digest": _digest(
            pointer["activation_head_digest"],
            "proposed_activation_head_digest",
        ),
        "store_revision": _revision(
            pointer["store_revision"], "proposed_store_revision"
        ),
        "previous_bundle_digest": _digest(
            pointer["previous_bundle_digest"], "proposed_previous_bundle_digest"
        ),
        "previous_activation_head_digest": _digest(
            pointer["previous_activation_head_digest"],
            "proposed_previous_activation_head_digest",
        ),
    }


def _normalize_core(value: object) -> dict[str, object]:
    core = _exact_dict(value, INTENT_CORE_KEYS, "activation_admission_intent")
    if (
        type(core["schema_version"]) is not str
        or core["schema_version"] != INTENT_SCHEMA
    ):
        _refuse("schema_version", "activation admission intent schema refused")

    current = _current_pointer(core["expected_current_pointer"])
    proposed = _proposed_pointer(core["proposed_pointer"])
    current_revision = current["store_revision"]
    proposed_revision = proposed["store_revision"]
    if current_revision == MAX_GENERATION:
        _refuse("revision_exhausted", "current revision is exhausted")
    if proposed_revision != current_revision + 1:
        _refuse("revision_step", "proposed revision must advance by one")
    if proposed["previous_bundle_digest"] != current["bundle_digest"]:
        _refuse("previous_bundle_binding", "proposed bundle predecessor is stale")
    if (
        proposed["previous_activation_head_digest"]
        != current["activation_head_digest"]
    ):
        _refuse("previous_head_binding", "proposed head predecessor is stale")
    if proposed["bundle_digest"] == current["bundle_digest"]:
        _refuse("bundle_not_advanced", "proposed bundle must be new")
    if proposed["activation_head_digest"] == current["activation_head_digest"]:
        _refuse("head_not_advanced", "proposed activation head must be new")

    normalized: dict[str, object] = {
        "schema_version": INTENT_SCHEMA,
        "activation_scope_digest": _digest(
            core["activation_scope_digest"], "activation_scope_digest"
        ),
        "query_digest": _digest(core["query_digest"], "query_digest"),
        # Review evidence uses candidate_digest and activation_head_digest.
        # Derive their permitted values from the CAS tuples instead of
        # allowing a parallel caller-selected identity.
        "candidate_digest": _digest(
            core["candidate_digest"], "candidate_digest"
        ),
        "activation_head_digest": _digest(
            core["activation_head_digest"], "activation_head_digest"
        ),
        "expected_current_pointer": current,
        "proposed_pointer": proposed,
        "trust_registry_head_digest": _digest(
            core["trust_registry_head_digest"], "trust_registry_head_digest"
        ),
        "attestation_log_head_digest": _digest(
            core["attestation_log_head_digest"],
            "attestation_log_head_digest",
        ),
        "consensus_policy_digest": _digest(
            core["consensus_policy_digest"], "consensus_policy_digest"
        ),
        "required_independent_support": _support(
            core["required_independent_support"]
        ),
    }
    if normalized["candidate_digest"] != proposed["bundle_digest"]:
        _refuse(
            "candidate_binding",
            "candidate digest must equal the proposed bundle digest",
        )
    if normalized["activation_head_digest"] != current["activation_head_digest"]:
        _refuse(
            "activation_head_binding",
            "evidence head must equal the expected current activation head",
        )
    for field, expected in _NON_AUTHORITY_FLAGS.items():
        if type(core[field]) is not bool or core[field] is not expected:
            _refuse(field, f"{field} must remain {expected}")
        normalized[field] = expected
    return normalized


def derive_admission_challenge_digest(core: object) -> str:
    """Derive the common review challenge from one exact normalized core."""

    normalized = _normalize_core(core)
    return sha256_digest(
        {
            "domain": CHALLENGE_DIGEST_DOMAIN,
            "intent": normalized,
        }
    )


def derive_admission_decision_digest(
    *,
    activation_scope_digest: str,
    admission_challenge_digest: str,
    query_digest: str,
    candidate_digest: str,
    activation_head_digest: str,
) -> str:
    """Derive the evidence decision binding; this is not a decision grant."""

    return sha256_digest(
        {
            "domain": DECISION_DIGEST_DOMAIN,
            "purpose": "observe_activation_candidate_consensus",
            "activation_scope_digest": _digest(
                activation_scope_digest, "activation_scope_digest"
            ),
            "admission_challenge_digest": _digest(
                admission_challenge_digest, "admission_challenge_digest"
            ),
            "query_digest": _digest(query_digest, "query_digest"),
            "candidate_digest": _digest(candidate_digest, "candidate_digest"),
            "activation_head_digest": _digest(
                activation_head_digest, "activation_head_digest"
            ),
            **_NON_AUTHORITY_FLAGS,
        }
    )


def build_activation_admission_intent(
    *,
    activation_scope_digest: str,
    query_digest: str,
    expected_current_bundle_digest: str,
    expected_current_activation_head_digest: str,
    expected_current_store_revision: int,
    proposed_bundle_digest: str,
    proposed_activation_head_digest: str,
    proposed_store_revision: int,
    proposed_previous_bundle_digest: str,
    proposed_previous_activation_head_digest: str,
    trust_registry_head_digest: str,
    attestation_log_head_digest: str,
    consensus_policy_digest: str,
    required_independent_support: int,
) -> dict[str, object]:
    """Build a deterministic observer-only intent for one successor CAS."""

    core = _normalize_core(
        {
            "schema_version": INTENT_SCHEMA,
            "activation_scope_digest": activation_scope_digest,
            "query_digest": query_digest,
            "candidate_digest": proposed_bundle_digest,
            "activation_head_digest": expected_current_activation_head_digest,
            "expected_current_pointer": {
                "bundle_digest": expected_current_bundle_digest,
                "activation_head_digest": expected_current_activation_head_digest,
                "store_revision": expected_current_store_revision,
            },
            "proposed_pointer": {
                "bundle_digest": proposed_bundle_digest,
                "activation_head_digest": proposed_activation_head_digest,
                "store_revision": proposed_store_revision,
                "previous_bundle_digest": proposed_previous_bundle_digest,
                "previous_activation_head_digest": (
                    proposed_previous_activation_head_digest
                ),
            },
            "trust_registry_head_digest": trust_registry_head_digest,
            "attestation_log_head_digest": attestation_log_head_digest,
            "consensus_policy_digest": consensus_policy_digest,
            "required_independent_support": required_independent_support,
            **_NON_AUTHORITY_FLAGS,
        }
    )
    challenge = derive_admission_challenge_digest(core)
    decision = derive_admission_decision_digest(
        activation_scope_digest=core["activation_scope_digest"],  # type: ignore[arg-type]
        admission_challenge_digest=challenge,
        query_digest=core["query_digest"],  # type: ignore[arg-type]
        candidate_digest=core["candidate_digest"],  # type: ignore[arg-type]
        activation_head_digest=core["activation_head_digest"],  # type: ignore[arg-type]
    )
    return {
        **core,
        "admission_challenge_digest": challenge,
        "decision_digest": decision,
    }


def parse_activation_admission_intent(value: object) -> dict[str, object]:
    intent = _exact_dict(value, INTENT_KEYS, "activation_admission_intent")
    core = _normalize_core({key: intent[key] for key in INTENT_CORE_KEYS})
    challenge = derive_admission_challenge_digest(core)
    if _digest(
        intent["admission_challenge_digest"], "admission_challenge_digest"
    ) != challenge:
        _refuse("challenge_digest_mismatch", "admission challenge digest mismatch")
    decision = derive_admission_decision_digest(
        activation_scope_digest=core["activation_scope_digest"],  # type: ignore[arg-type]
        admission_challenge_digest=challenge,
        query_digest=core["query_digest"],  # type: ignore[arg-type]
        candidate_digest=core["candidate_digest"],  # type: ignore[arg-type]
        activation_head_digest=core["activation_head_digest"],  # type: ignore[arg-type]
    )
    if _digest(intent["decision_digest"], "decision_digest") != decision:
        _refuse("decision_digest_mismatch", "admission decision digest mismatch")
    return {
        **core,
        "admission_challenge_digest": challenge,
        "decision_digest": decision,
    }


def verify_activation_admission_intent(
    value: object,
) -> tuple[bool, Optional[str]]:
    try:
        parse_activation_admission_intent(value)
    except ActivationAdmissionIntentError as exc:
        return False, exc.reason
    return True, None


def verify_activation_admission_intent_bindings(
    value: object,
    *,
    expected_activation_scope_digest: str,
    expected_query_digest: str,
    expected_current_bundle_digest: str,
    expected_current_activation_head_digest: str,
    expected_current_store_revision: int,
    expected_proposed_bundle_digest: str,
    expected_proposed_activation_head_digest: str,
    expected_proposed_store_revision: int,
    expected_trust_registry_head_digest: str,
    expected_attestation_log_head_digest: str,
    expected_consensus_policy_digest: str,
    expected_required_independent_support: int,
) -> tuple[bool, Optional[str]]:
    """Rebind an intent to independently obtained current external facts."""

    try:
        intent = parse_activation_admission_intent(value)
        expected = {
            "activation_scope_digest": _digest(
                expected_activation_scope_digest,
                "expected_activation_scope_digest",
            ),
            "query_digest": _digest(
                expected_query_digest, "expected_query_digest"
            ),
            "trust_registry_head_digest": _digest(
                expected_trust_registry_head_digest,
                "expected_trust_registry_head_digest",
            ),
            "attestation_log_head_digest": _digest(
                expected_attestation_log_head_digest,
                "expected_attestation_log_head_digest",
            ),
            "consensus_policy_digest": _digest(
                expected_consensus_policy_digest,
                "expected_consensus_policy_digest",
            ),
            "required_independent_support": _support(
                expected_required_independent_support
            ),
        }
        current = {
            "bundle_digest": _digest(
                expected_current_bundle_digest,
                "expected_current_bundle_digest",
            ),
            "activation_head_digest": _digest(
                expected_current_activation_head_digest,
                "expected_current_activation_head_digest",
            ),
            "store_revision": _revision(
                expected_current_store_revision,
                "expected_current_store_revision",
            ),
        }
        proposed = {
            "bundle_digest": _digest(
                expected_proposed_bundle_digest,
                "expected_proposed_bundle_digest",
            ),
            "activation_head_digest": _digest(
                expected_proposed_activation_head_digest,
                "expected_proposed_activation_head_digest",
            ),
            "store_revision": _revision(
                expected_proposed_store_revision,
                "expected_proposed_store_revision",
            ),
        }
    except ActivationAdmissionIntentError as exc:
        return False, exc.reason

    for field, expected_value in expected.items():
        if type(intent[field]) is not type(expected_value) or intent[field] != expected_value:
            return False, f"stale_{field}"
    stored_current = intent["expected_current_pointer"]
    stored_proposed = intent["proposed_pointer"]
    for field, expected_value in current.items():
        if (
            type(stored_current[field]) is not type(expected_value)  # type: ignore[index]
            or stored_current[field] != expected_value  # type: ignore[index]
        ):
            return False, f"stale_current_{field}"
    for field, expected_value in proposed.items():
        if (
            type(stored_proposed[field]) is not type(expected_value)  # type: ignore[index]
            or stored_proposed[field] != expected_value  # type: ignore[index]
        ):
            return False, f"stale_proposed_{field}"
    return True, None


def evidence_bindings_from_activation_admission_intent(
    value: object,
) -> dict[str, str]:
    """Return the exact bindings an attested evidence batch must use."""

    intent = parse_activation_admission_intent(value)
    return {
        "activation_scope_digest": intent["activation_scope_digest"],
        "admission_challenge_digest": intent["admission_challenge_digest"],
        "query_digest": intent["query_digest"],
        "decision_digest": intent["decision_digest"],
        "candidate_digest": intent["candidate_digest"],
        "activation_head_digest": intent["activation_head_digest"],
        "trust_registry_head_digest": intent["trust_registry_head_digest"],
        "attestation_log_head_digest": intent["attestation_log_head_digest"],
        "consensus_policy_digest": intent["consensus_policy_digest"],
    }  # type: ignore[return-value]


__all__ = [
    "ActivationAdmissionIntentError",
    "CHALLENGE_DIGEST_DOMAIN",
    "CURRENT_POINTER_KEYS",
    "DECISION_DIGEST_DOMAIN",
    "INTENT_KEYS",
    "INTENT_SCHEMA",
    "PROPOSED_POINTER_KEYS",
    "build_activation_admission_intent",
    "derive_admission_challenge_digest",
    "derive_admission_decision_digest",
    "evidence_bindings_from_activation_admission_intent",
    "parse_activation_admission_intent",
    "verify_activation_admission_intent",
    "verify_activation_admission_intent_bindings",
]
