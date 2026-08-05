# SPDX-License-Identifier: BUSL-1.1
"""Adversarial tests for the scope-bound activation admission intent."""

from __future__ import annotations

import hashlib
from copy import deepcopy

import pytest

from waggledance.core.capabilities.activation_admission_intent import (
    ActivationAdmissionIntentError,
    build_activation_admission_intent,
    evidence_bindings_from_activation_admission_intent,
    parse_activation_admission_intent,
    verify_activation_admission_intent,
    verify_activation_admission_intent_bindings,
)
from waggledance.core.capabilities.activation_contracts import MAX_GENERATION


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("ascii")).hexdigest()


def _kwargs(**overrides):
    values = {
        "activation_scope_digest": _digest("scope"),
        "query_digest": _digest("query"),
        "expected_current_bundle_digest": _digest("bundle:current"),
        "expected_current_activation_head_digest": _digest("head:current"),
        "expected_current_store_revision": 7,
        "proposed_bundle_digest": _digest("bundle:proposed"),
        "proposed_activation_head_digest": _digest("head:proposed"),
        "proposed_store_revision": 8,
        "proposed_previous_bundle_digest": _digest("bundle:current"),
        "proposed_previous_activation_head_digest": _digest("head:current"),
        "trust_registry_head_digest": _digest("trust-head"),
        "attestation_log_base_head_digest": _digest("log-base-head"),
        "consensus_policy_digest": _digest("consensus-policy"),
        "required_independent_support": 3,
    }
    values.update(overrides)
    return values


def _external(intent: dict) -> dict:
    return {
        "expected_activation_scope_digest": intent["activation_scope_digest"],
        "expected_query_digest": intent["query_digest"],
        "expected_current_bundle_digest": intent["expected_current_pointer"][
            "bundle_digest"
        ],
        "expected_current_activation_head_digest": intent[
            "expected_current_pointer"
        ]["activation_head_digest"],
        "expected_current_store_revision": intent["expected_current_pointer"][
            "store_revision"
        ],
        "expected_proposed_bundle_digest": intent["proposed_pointer"][
            "bundle_digest"
        ],
        "expected_proposed_activation_head_digest": intent["proposed_pointer"][
            "activation_head_digest"
        ],
        "expected_proposed_store_revision": intent["proposed_pointer"][
            "store_revision"
        ],
        "expected_trust_registry_head_digest": intent[
            "trust_registry_head_digest"
        ],
        "expected_attestation_log_base_head_digest": intent[
            "attestation_log_base_head_digest"
        ],
        "expected_consensus_policy_digest": intent["consensus_policy_digest"],
        "expected_required_independent_support": intent[
            "required_independent_support"
        ],
    }


def test_build_is_deterministic_and_exports_exact_evidence_bindings() -> None:
    first = build_activation_admission_intent(**_kwargs())
    second = build_activation_admission_intent(**_kwargs())
    assert first == second
    assert verify_activation_admission_intent(first) == (True, None)
    assert parse_activation_admission_intent(first) == first
    assert verify_activation_admission_intent_bindings(
        first, **_external(first)
    ) == (True, None)

    bindings = evidence_bindings_from_activation_admission_intent(first)
    assert bindings == {
        "activation_scope_digest": first["activation_scope_digest"],
        "admission_challenge_digest": first["admission_challenge_digest"],
        "query_digest": first["query_digest"],
        "decision_digest": first["decision_digest"],
        "candidate_digest": first["proposed_pointer"]["bundle_digest"],
        "activation_head_digest": first["expected_current_pointer"][
            "activation_head_digest"
        ],
        "trust_registry_head_digest": first["trust_registry_head_digest"],
        "attestation_log_base_head_digest": first[
            "attestation_log_base_head_digest"
        ],
        "consensus_policy_digest": first["consensus_policy_digest"],
    }
    assert first["observation_only"] is True
    assert first["authority_granted"] is False
    assert first["activation_performed"] is False
    assert first["routing_influence_applied"] is False


@pytest.mark.parametrize(
    "field",
    [
        "activation_scope_digest",
        "query_digest",
        "expected_current_bundle_digest",
        "expected_current_activation_head_digest",
        "proposed_bundle_digest",
        "proposed_activation_head_digest",
        "trust_registry_head_digest",
        "attestation_log_base_head_digest",
        "consensus_policy_digest",
    ],
)
def test_every_digest_axis_changes_the_common_challenge(field: str) -> None:
    baseline = build_activation_admission_intent(**_kwargs())
    changed = _kwargs(**{field: _digest(f"changed:{field}")})
    if field == "expected_current_bundle_digest":
        changed["proposed_previous_bundle_digest"] = changed[field]
    if field == "expected_current_activation_head_digest":
        changed["proposed_previous_activation_head_digest"] = changed[field]
    candidate = build_activation_admission_intent(**changed)
    assert candidate["admission_challenge_digest"] != baseline[
        "admission_challenge_digest"
    ]
    assert candidate["decision_digest"] != baseline["decision_digest"]


def test_revision_and_predecessor_chain_fail_closed() -> None:
    cases = [
        (
            {"proposed_store_revision": 9},
            "revision_step",
        ),
        (
            {"proposed_previous_bundle_digest": _digest("stale")},
            "previous_bundle_binding",
        ),
        (
            {"proposed_previous_activation_head_digest": _digest("stale")},
            "previous_head_binding",
        ),
        (
            {
                "proposed_bundle_digest": _digest("bundle:current"),
            },
            "bundle_not_advanced",
        ),
        (
            {
                "proposed_activation_head_digest": _digest("head:current"),
            },
            "head_not_advanced",
        ),
    ]
    for overrides, reason in cases:
        with pytest.raises(ActivationAdmissionIntentError) as exc_info:
            build_activation_admission_intent(**_kwargs(**overrides))
        assert exc_info.value.reason == reason

    with pytest.raises(ActivationAdmissionIntentError) as exc_info:
        build_activation_admission_intent(
            **_kwargs(
                expected_current_store_revision=MAX_GENERATION,
                proposed_store_revision=MAX_GENERATION,
            )
        )
    assert exc_info.value.reason == "revision_exhausted"


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (True, "expected_current_store_revision"),
        (-1, "expected_current_store_revision"),
        (1.0, "expected_current_store_revision"),
    ],
)
def test_revision_requires_an_exact_bounded_int(value: object, reason: str) -> None:
    with pytest.raises(ActivationAdmissionIntentError) as exc_info:
        build_activation_admission_intent(
            **_kwargs(expected_current_store_revision=value)
        )
    assert exc_info.value.reason == reason


def test_tampering_digest_or_authority_flags_is_refused() -> None:
    intent = build_activation_admission_intent(**_kwargs())
    for field, expected_reason in (
        ("admission_challenge_digest", "challenge_digest_mismatch"),
        ("decision_digest", "decision_digest_mismatch"),
    ):
        tampered = deepcopy(intent)
        tampered[field] = _digest(f"tampered:{field}")
        ok, reason = verify_activation_admission_intent(tampered)
        assert ok is False
        assert reason == expected_reason

    for field, hostile in (
        ("observation_only", False),
        ("authority_granted", True),
        ("activation_performed", True),
        ("routing_influence_applied", True),
    ):
        tampered = deepcopy(intent)
        tampered[field] = hostile
        assert verify_activation_admission_intent(tampered) == (False, field)


def test_exact_dict_keysets_and_digest_spelling_are_required() -> None:
    intent = build_activation_admission_intent(**_kwargs())
    extra = {**intent, "authority": "smuggled"}
    assert verify_activation_admission_intent(extra) == (
        False,
        "activation_admission_intent_keyset",
    )

    class DictSubclass(dict):
        pass

    assert verify_activation_admission_intent(DictSubclass(intent)) == (
        False,
        "activation_admission_intent_type",
    )
    uppercase = deepcopy(intent)
    uppercase["activation_scope_digest"] = "sha256:" + "A" * 64
    assert verify_activation_admission_intent(uppercase) == (
        False,
        "activation_scope_digest",
    )


@pytest.mark.parametrize(
    ("external_field", "reason"),
    [
        ("expected_activation_scope_digest", "stale_activation_scope_digest"),
        ("expected_query_digest", "stale_query_digest"),
        (
            "expected_current_bundle_digest",
            "stale_current_bundle_digest",
        ),
        (
            "expected_current_activation_head_digest",
            "stale_current_activation_head_digest",
        ),
        ("expected_proposed_bundle_digest", "stale_proposed_bundle_digest"),
        (
            "expected_proposed_activation_head_digest",
            "stale_proposed_activation_head_digest",
        ),
        (
            "expected_trust_registry_head_digest",
            "stale_trust_registry_head_digest",
        ),
        (
            "expected_attestation_log_base_head_digest",
            "stale_attestation_log_base_head_digest",
        ),
        (
            "expected_consensus_policy_digest",
            "stale_consensus_policy_digest",
        ),
    ],
)
def test_external_rebinding_rejects_every_stale_digest(
    external_field: str, reason: str
) -> None:
    intent = build_activation_admission_intent(**_kwargs())
    external = _external(intent)
    external[external_field] = _digest(f"stale:{external_field}")
    assert verify_activation_admission_intent_bindings(
        intent, **external
    ) == (False, reason)


def test_external_rebinding_rejects_stale_revisions_and_threshold() -> None:
    intent = build_activation_admission_intent(**_kwargs())
    for field, value, reason in (
        (
            "expected_current_store_revision",
            6,
            "stale_current_store_revision",
        ),
        (
            "expected_proposed_store_revision",
            9,
            "stale_proposed_store_revision",
        ),
        (
            "expected_required_independent_support",
            4,
            "stale_required_independent_support",
        ),
    ):
        external = _external(intent)
        external[field] = value
        assert verify_activation_admission_intent_bindings(
            intent, **external
        ) == (False, reason)


def test_builder_detaches_all_nested_inputs() -> None:
    intent = build_activation_admission_intent(**_kwargs())
    parsed = parse_activation_admission_intent(intent)
    intent["expected_current_pointer"]["bundle_digest"] = _digest("mutated")
    intent["proposed_pointer"]["bundle_digest"] = _digest("mutated-proposed")
    assert parsed["expected_current_pointer"]["bundle_digest"] == _digest(
        "bundle:current"
    )
    assert parsed["proposed_pointer"]["bundle_digest"] == _digest(
        "bundle:proposed"
    )
