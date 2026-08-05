"""Tests for immutable consensus-gate expectation pins."""

from __future__ import annotations

import hashlib
import json

import pytest

from waggledance.core.capabilities.activation_admission_intent import (
    build_activation_admission_intent,
)
from waggledance.core.capabilities.activation_contracts import MAX_GENERATION
from waggledance.core.orchestration.attested_consensus_expectation import (
    ATTESTED_CONSENSUS_EXPECTATION_KEYS,
    INITIAL_PREVIOUS_EXPECTATION_HEAD_DIGEST,
    AttestedConsensusExpectationError,
    build_attested_consensus_expectation,
    canonicalize_attested_consensus_expectation,
    expectation_bindings_from_attested_consensus_expectation,
    parse_attested_consensus_expectation,
    verify_attested_consensus_expectation,
    verify_attested_consensus_expectation_bindings,
    verify_attested_consensus_expectation_transition,
)
from waggledance.core.orchestration.attested_consensus_shadow import (
    GATE_EXPECTATION_KEYS,
)


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("ascii")).hexdigest()


def _intent(
    *,
    scope: str | None = None,
    current_revision: int = 3,
    suffix: str = "a",
):
    current_bundle = _digest(f"bundle:current:{suffix}")
    current_head = _digest(f"activation-head:current:{suffix}")
    return build_activation_admission_intent(
        activation_scope_digest=scope or _digest("scope"),
        query_digest=_digest(f"query:{suffix}"),
        expected_current_bundle_digest=current_bundle,
        expected_current_activation_head_digest=current_head,
        expected_current_store_revision=current_revision,
        proposed_bundle_digest=_digest(f"bundle:proposed:{suffix}"),
        proposed_activation_head_digest=_digest(
            f"activation-head:proposed:{suffix}"
        ),
        proposed_store_revision=current_revision + 1,
        proposed_previous_bundle_digest=current_bundle,
        proposed_previous_activation_head_digest=current_head,
        trust_registry_head_digest=_digest(f"trust:{suffix}"),
        attestation_log_base_head_digest=_digest(f"log-base:{suffix}"),
        consensus_policy_digest=_digest(f"policy:{suffix}"),
        required_independent_support=2,
    )


def _pin(
    *,
    generation: int = 0,
    previous: str = INITIAL_PREVIOUS_EXPECTATION_HEAD_DIGEST,
    intent=None,
    closed_label: str = "closed:a",
):
    return build_attested_consensus_expectation(
        generation=generation,
        previous_expectation_head_digest=previous,
        activation_admission_intent=intent or _intent(),
        expected_attestation_log_closed_head_digest=_digest(closed_label),
    )


def test_build_parse_canonicalize_and_project_exact_runtime_bindings() -> None:
    intent = _intent()
    pin = _pin(intent=intent)
    parsed = parse_attested_consensus_expectation(pin)
    canonical = canonicalize_attested_consensus_expectation(pin)
    bindings = expectation_bindings_from_attested_consensus_expectation(pin)

    assert set(pin) == ATTESTED_CONSENSUS_EXPECTATION_KEYS
    assert parsed == pin
    assert json.loads(canonical) == pin
    assert canonical == canonicalize_attested_consensus_expectation(
        json.loads(canonical)
    )
    assert set(bindings) == GATE_EXPECTATION_KEYS
    assert bindings == {
        "expected_consensus_policy_digest": intent[
            "consensus_policy_digest"
        ],
        "expected_activation_scope_digest": intent[
            "activation_scope_digest"
        ],
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
        "expected_proposed_activation_head_digest": intent[
            "proposed_pointer"
        ]["activation_head_digest"],
        "expected_proposed_store_revision": intent["proposed_pointer"][
            "store_revision"
        ],
        "expected_trust_registry_head_digest": intent[
            "trust_registry_head_digest"
        ],
        "expected_attestation_log_base_head_digest": intent[
            "attestation_log_base_head_digest"
        ],
        "expected_attestation_log_closed_head_digest": _digest("closed:a"),
    }
    assert pin["admission_challenge_digest"] == intent[
        "admission_challenge_digest"
    ]
    assert pin["advisory_only"] is True
    assert pin["authority_granted"] is False
    assert pin["activation_performed"] is False
    assert pin["routing_influence_applied"] is False
    assert verify_attested_consensus_expectation(pin) == (True, None)


def test_build_rejects_malformed_intent_and_closed_head() -> None:
    malformed = _intent()
    malformed["decision_digest"] = _digest("wrong-decision")
    with pytest.raises(AttestedConsensusExpectationError) as intent_error:
        _pin(intent=malformed)
    assert intent_error.value.reason == "intent:decision_digest_mismatch"

    with pytest.raises(AttestedConsensusExpectationError) as head_error:
        build_attested_consensus_expectation(
            generation=0,
            previous_expectation_head_digest=(
                INITIAL_PREVIOUS_EXPECTATION_HEAD_DIGEST
            ),
            activation_admission_intent=_intent(),
            expected_attestation_log_closed_head_digest="not-a-digest",
        )
    assert (
        head_error.value.reason
        == "expected_attestation_log_closed_head_digest"
    )
    intent = _intent()
    with pytest.raises(AttestedConsensusExpectationError) as same_head_error:
        build_attested_consensus_expectation(
            generation=0,
            previous_expectation_head_digest=(
                INITIAL_PREVIOUS_EXPECTATION_HEAD_DIGEST
            ),
            activation_admission_intent=intent,
            expected_attestation_log_closed_head_digest=intent[
                "attestation_log_base_head_digest"
            ],
        )
    assert same_head_error.value.reason == "closed_log_head_not_advanced"


def test_external_binding_verifier_rederives_intent_and_closed_head() -> None:
    intent = _intent(suffix="a")
    pin = _pin(intent=intent, closed_label="closed:a")
    assert verify_attested_consensus_expectation_bindings(
        pin,
        activation_admission_intent=intent,
        expected_attestation_log_closed_head_digest=_digest("closed:a"),
    ) == (True, None)
    assert verify_attested_consensus_expectation_bindings(
        pin,
        activation_admission_intent=_intent(suffix="foreign"),
        expected_attestation_log_closed_head_digest=_digest("closed:a"),
    ) == (False, "admission_challenge_binding")
    assert verify_attested_consensus_expectation_bindings(
        pin,
        activation_admission_intent=intent,
        expected_attestation_log_closed_head_digest=_digest("closed:stale"),
    ) == (False, "expected_bindings_binding")


@pytest.mark.parametrize(
    ("generation", "previous", "reason"),
    [
        (0, _digest("noninitial"), "initial_previous_expectation_head"),
        (
            1,
            INITIAL_PREVIOUS_EXPECTATION_HEAD_DIGEST,
            "noninitial_previous_expectation_head",
        ),
        (True, INITIAL_PREVIOUS_EXPECTATION_HEAD_DIGEST, "generation"),
        (-1, INITIAL_PREVIOUS_EXPECTATION_HEAD_DIGEST, "generation"),
    ],
)
def test_generation_and_predecessor_rules_fail_closed(
    generation, previous, reason
) -> None:
    with pytest.raises(AttestedConsensusExpectationError) as error:
        _pin(generation=generation, previous=previous)
    assert error.value.reason == reason


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("advisory_only", False, "advisory_only"),
        ("authority_granted", True, "authority_granted"),
        ("activation_performed", True, "activation_performed"),
        ("routing_influence_applied", True, "routing_influence_applied"),
        ("expectation_head_digest", "bad", "expectation_head_digest"),
    ],
)
def test_no_authority_and_digest_fields_are_literal_and_exact(
    field, value, reason
) -> None:
    pin = _pin()
    pin[field] = value
    ok, actual = verify_attested_consensus_expectation(pin)
    assert ok is False
    assert actual == reason


def test_nested_binding_tamper_and_extra_keys_are_refused() -> None:
    pin = _pin()
    pin["expected_bindings"]["expected_query_digest"] = _digest("tampered")
    ok, reason = verify_attested_consensus_expectation(pin)
    assert ok is False
    assert reason == "expectation_head_digest_mismatch"

    extra = _pin()
    extra["expected_bindings"]["self_selected_head"] = _digest("forbidden")
    ok, reason = verify_attested_consensus_expectation(extra)
    assert ok is False
    assert reason == "expected_bindings:gate_expectations_keyset"

    unadvanced = _pin()
    unadvanced["expected_bindings"][
        "expected_attestation_log_closed_head_digest"
    ] = unadvanced["expected_bindings"][
        "expected_attestation_log_base_head_digest"
    ]
    ok, reason = verify_attested_consensus_expectation(unadvanced)
    assert ok is False
    assert reason == "closed_log_head_not_advanced"


def test_projection_is_a_private_copy() -> None:
    pin = _pin()
    projected = expectation_bindings_from_attested_consensus_expectation(pin)
    projected["expected_query_digest"] = _digest("mutated-projection")
    reparsed = expectation_bindings_from_attested_consensus_expectation(pin)
    assert reparsed["expected_query_digest"] == _digest("query:a")


def test_valid_scope_local_transition_and_stale_current_refusal() -> None:
    scope = _digest("scope")
    current = _pin(intent=_intent(scope=scope, suffix="a"))
    proposed = _pin(
        generation=1,
        previous=current["expectation_head_digest"],
        intent=_intent(scope=scope, current_revision=4, suffix="b"),
        closed_label="closed:b",
    )
    assert verify_attested_consensus_expectation_transition(
        current,
        proposed,
        expected_current_expectation_head_digest=current[
            "expectation_head_digest"
        ],
    ) == (True, None)
    assert verify_attested_consensus_expectation_transition(
        current,
        proposed,
        expected_current_expectation_head_digest=_digest("stale"),
    ) == (False, "stale_current_expectation_head")


def test_transition_rejects_generation_skip_wrong_predecessor_and_scope() -> None:
    scope = _digest("scope")
    current = _pin(intent=_intent(scope=scope))
    cases = (
        (
            _pin(
                generation=2,
                previous=current["expectation_head_digest"],
                intent=_intent(scope=scope, suffix="skip"),
            ),
            "generation_step",
        ),
        (
            _pin(
                generation=1,
                previous=_digest("wrong-predecessor"),
                intent=_intent(scope=scope, suffix="predecessor"),
            ),
            "previous_expectation_head_binding",
        ),
        (
            _pin(
                generation=1,
                previous=current["expectation_head_digest"],
                intent=_intent(scope=_digest("foreign"), suffix="scope"),
            ),
            "activation_scope_binding",
        ),
        (
            _pin(
                generation=1,
                previous=current["expectation_head_digest"],
                intent=_intent(scope=scope),
                closed_label="closed:replayed",
            ),
            "admission_challenge_replay",
        ),
    )
    for proposed, reason in cases:
        assert verify_attested_consensus_expectation_transition(
            current,
            proposed,
            expected_current_expectation_head_digest=current[
                "expectation_head_digest"
            ],
        ) == (False, reason)


def test_transition_refuses_exhausted_generation() -> None:
    current = _pin(
        generation=MAX_GENERATION,
        previous=_digest("real-predecessor"),
    )
    assert verify_attested_consensus_expectation_transition(
        current,
        current,
        expected_current_expectation_head_digest=current[
            "expectation_head_digest"
        ],
    ) == (False, "generation_exhausted")


def test_exact_dict_boundary_refuses_subclasses_and_extra_fields() -> None:
    class _Mapping(dict):
        pass

    pin = _pin()
    with pytest.raises(AttestedConsensusExpectationError) as type_error:
        parse_attested_consensus_expectation(_Mapping(pin))
    assert type_error.value.reason == "expectation_type"

    pin["unexpected"] = False
    ok, reason = verify_attested_consensus_expectation(pin)
    assert ok is False
    assert reason == "expectation_keyset"
