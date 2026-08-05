"""Focused tests for the immutable advisory admission policy."""

from __future__ import annotations

import pytest

from waggledance.core.orchestration import consensus_admission_policy as P
from waggledance.core.orchestration.evidence_attestation import (
    ATTESTATION_SCHEME,
)
from waggledance.core.orchestration.evidence_consensus import EVALUATION_SCHEMA


def _policy(**overrides: int) -> P.ConsensusAdmissionPolicyV1:
    values = {
        "required_independent_support": 3,
        "maximum_evidence_records": 16,
        "maximum_ballots": 24,
        "maximum_attestations": 24,
    }
    values.update(overrides)
    return P.build_consensus_admission_policy(**values)


def test_policy_is_deterministic_exact_and_authority_free() -> None:
    first = _policy()
    second = _policy()

    assert first == second
    assert first.policy_digest == second.policy_digest
    assert set(first.to_mapping()) == P.CONSENSUS_ADMISSION_POLICY_KEYS
    assert first.consensus_evaluation_schema == EVALUATION_SCHEMA
    assert first.attestation_scheme == ATTESTATION_SCHEME
    assert first.require_direct_log_append is True
    assert first.require_empty_target_at_base is True
    assert first.require_complete_committed_source_set is True
    assert first.require_trusted_provenance is True
    assert first.require_signer_identity_correlation is True
    assert first.require_reviewer_scope_correlation is True
    assert first.stop_latched_blocks is True
    assert first.veto_latched_blocks is True
    assert first.observer_only is True
    assert first.advisory_only is True
    assert first.authority_granted is False
    assert first.activation_performed is False
    assert first.routing_influence_applied is False
    assert P.parse_consensus_admission_policy(first) == first.to_mapping()
    assert P.verify_consensus_admission_policy(first.to_mapping()) == (True, None)


def test_policy_digest_binds_threshold_and_every_bound() -> None:
    baseline = _policy()
    variants = (
        _policy(required_independent_support=4),
        _policy(maximum_evidence_records=15),
        _policy(maximum_ballots=23, maximum_attestations=23),
    )
    assert all(item.policy_digest != baseline.policy_digest for item in variants)


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("require_direct_log_append", False, "require_direct_log_append"),
        (
            "require_empty_target_at_base",
            False,
            "require_empty_target_at_base",
        ),
        (
            "require_complete_committed_source_set",
            False,
            "require_complete_committed_source_set",
        ),
        ("require_trusted_provenance", False, "require_trusted_provenance"),
        (
            "require_signer_identity_correlation",
            False,
            "require_signer_identity_correlation",
        ),
        (
            "require_reviewer_scope_correlation",
            False,
            "require_reviewer_scope_correlation",
        ),
        ("stop_latched_blocks", False, "stop_latched_blocks"),
        ("veto_latched_blocks", False, "veto_latched_blocks"),
        ("observer_only", False, "observer_only"),
        ("advisory_only", False, "advisory_only"),
        ("authority_granted", True, "authority_granted"),
        ("activation_performed", True, "activation_performed"),
        ("routing_influence_applied", True, "routing_influence_applied"),
    ],
)
def test_policy_safety_laws_cannot_be_relaxed(
    field: str, value: object, reason: str
) -> None:
    wire = _policy().to_mapping()
    wire[field] = value
    assert P.verify_consensus_admission_policy(wire) == (False, reason)


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"required_independent_support": 0}, "required_independent_support"),
        ({"required_independent_support": True}, "required_independent_support"),
        ({"maximum_evidence_records": 0}, "maximum_evidence_records"),
        ({"maximum_ballots": 0}, "maximum_ballots"),
        ({"maximum_attestations": 0}, "maximum_attestations"),
        (
            {"required_independent_support": 17},
            "support_exceeds_evidence_bound",
        ),
        (
            {"maximum_evidence_records": 25},
            "evidence_exceeds_ballot_bound",
        ),
        (
            {"maximum_attestations": 23},
            "attestation_ballot_bound_mismatch",
        ),
    ],
)
def test_policy_bounds_and_relationships_fail_closed(
    overrides: dict[str, int], reason: str
) -> None:
    with pytest.raises(P.ConsensusAdmissionPolicyError) as exc:
        _policy(**overrides)
    assert exc.value.reason == reason


def test_wire_parser_rejects_subclasses_key_smuggling_and_tampering() -> None:
    class DictAlias(dict):
        pass

    wire = _policy().to_mapping()
    assert P.verify_consensus_admission_policy(DictAlias(wire)) == (
        False,
        "policy_type",
    )

    assert P.verify_consensus_admission_policy(
        {**wire, "allow_untrusted": True}
    ) == (False, "policy_keyset")

    missing = dict(wire)
    missing.pop("require_trusted_provenance")
    assert P.verify_consensus_admission_policy(missing) == (
        False,
        "policy_keyset",
    )

    tampered = dict(wire)
    tampered["required_independent_support"] = 4
    assert P.verify_consensus_admission_policy(tampered) == (
        False,
        "policy_digest_mismatch",
    )


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("schema_version", "wd.consensus_admission_policy.v2", "schema_version"),
        (
            "consensus_evaluation_schema",
            "wd.inhibitory_consensus_evaluation.v2",
            "consensus_evaluation_schema",
        ),
        (
            "attestation_scheme",
            "wd.evidence_attestation.none.v1",
            "attestation_scheme",
        ),
        ("policy_digest", 1, "policy_digest"),
    ],
)
def test_protocol_versions_and_digest_shape_are_pinned(
    field: str, value: object, reason: str
) -> None:
    wire = _policy().to_mapping()
    wire[field] = value
    assert P.verify_consensus_admission_policy(wire) == (False, reason)


def test_dataclass_revalidates_after_unsafe_instance_tampering() -> None:
    original = _policy()
    object.__setattr__(original, "authority_granted", True)
    with pytest.raises(P.ConsensusAdmissionPolicyError) as exc:
        P.ConsensusAdmissionPolicyV1(**original.__dict__)
    assert exc.value.reason == "authority_granted"
