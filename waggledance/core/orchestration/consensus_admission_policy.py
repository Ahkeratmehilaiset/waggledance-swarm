# SPDX-License-Identifier: BUSL-1.1
"""Content-addressed safety policy for advisory activation admission.

Version 1 deliberately exposes no permissive switches.  Completeness,
trusted provenance, a direct attestation-log append, and inhibitory stop/veto
semantics are protocol laws.  The configurable values only tighten bounded
batch sizes and select the independent-support threshold.

The policy is a pure value contract.  It performs no I/O, grants no authority,
and cannot activate a candidate or influence routing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.orchestration.evidence_attestation import (
    ATTESTATION_SCHEME,
    MAX_ATTESTATIONS,
)
from waggledance.core.orchestration.evidence_consensus import (
    EVALUATION_SCHEMA,
    MAX_BALLOTS,
    MAX_EVIDENCE_RECORDS,
    MAX_REQUIRED_SUPPORT,
)

CONSENSUS_ADMISSION_POLICY_SCHEMA = "wd.consensus_admission_policy.v1"
CONSENSUS_ADMISSION_POLICY_DIGEST_DOMAIN = (
    "wd.consensus_admission_policy.digest.v1"
)

_SAFETY_LAWS = {
    "require_direct_log_append": True,
    "require_empty_target_at_base": True,
    "require_complete_committed_source_set": True,
    "require_trusted_provenance": True,
    "require_signer_identity_correlation": True,
    "require_reviewer_scope_correlation": True,
    "stop_latched_blocks": True,
    "veto_latched_blocks": True,
}

_NO_AUTHORITY_FLAGS = {
    "observer_only": True,
    "advisory_only": True,
    "authority_granted": False,
    "activation_performed": False,
    "routing_influence_applied": False,
}

CONSENSUS_ADMISSION_POLICY_CORE_KEYS = frozenset(
    {
        "schema_version",
        "consensus_evaluation_schema",
        "attestation_scheme",
        "required_independent_support",
        "maximum_evidence_records",
        "maximum_ballots",
        "maximum_attestations",
        *_SAFETY_LAWS,
        *_NO_AUTHORITY_FLAGS,
    }
)
CONSENSUS_ADMISSION_POLICY_KEYS = (
    CONSENSUS_ADMISSION_POLICY_CORE_KEYS | {"policy_digest"}
)


class ConsensusAdmissionPolicyError(ValueError):
    """A policy value is malformed, unsafe, or self-inconsistent."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _refuse(reason: str, message: str) -> None:
    raise ConsensusAdmissionPolicyError(reason, message)


def _exact_dict(value: object, keys: frozenset[str]) -> dict[str, object]:
    if type(value) is not dict:
        _refuse("policy_type", "policy must be an exact dict")
    if dict.__len__(value) > len(keys):
        _refuse("policy_keyset", "policy keyset exceeds its exact bound")
    snapshot = value.copy()
    if dict.__len__(snapshot) > len(keys):
        _refuse("policy_keyset", "policy keyset exceeds its exact bound")
    if set(snapshot) != keys or any(type(key) is not str for key in snapshot):
        _refuse("policy_keyset", "policy must have the exact v1 keyset")
    return snapshot


def _bounded_count(value: object, label: str, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        _refuse(label, f"{label} must be an exact integer within 1..{maximum}")
    return value


def _normalize_policy_core(value: object) -> dict[str, object]:
    core = _exact_dict(value, CONSENSUS_ADMISSION_POLICY_CORE_KEYS)
    if (
        type(core["schema_version"]) is not str
        or core["schema_version"] != CONSENSUS_ADMISSION_POLICY_SCHEMA
    ):
        _refuse("schema_version", "consensus admission policy schema refused")
    if (
        type(core["consensus_evaluation_schema"]) is not str
        or core["consensus_evaluation_schema"] != EVALUATION_SCHEMA
    ):
        _refuse(
            "consensus_evaluation_schema",
            "consensus evaluation schema is not the pinned v1 algorithm",
        )
    if (
        type(core["attestation_scheme"]) is not str
        or core["attestation_scheme"] != ATTESTATION_SCHEME
    ):
        _refuse("attestation_scheme", "attestation scheme is not pinned")

    required_support = _bounded_count(
        core["required_independent_support"],
        "required_independent_support",
        MAX_REQUIRED_SUPPORT,
    )
    maximum_evidence = _bounded_count(
        core["maximum_evidence_records"],
        "maximum_evidence_records",
        MAX_EVIDENCE_RECORDS,
    )
    maximum_ballots = _bounded_count(
        core["maximum_ballots"], "maximum_ballots", MAX_BALLOTS
    )
    maximum_attestations = _bounded_count(
        core["maximum_attestations"],
        "maximum_attestations",
        MAX_ATTESTATIONS,
    )
    if required_support > maximum_evidence:
        _refuse(
            "support_exceeds_evidence_bound",
            "required support cannot exceed the evidence-record bound",
        )
    if maximum_evidence > maximum_ballots:
        _refuse(
            "evidence_exceeds_ballot_bound",
            "every admitted evidence record must fit at least one ballot",
        )
    if maximum_attestations != maximum_ballots:
        _refuse(
            "attestation_ballot_bound_mismatch",
            "one exact attestation slot is required for every bounded ballot",
        )

    for field, expected in {**_SAFETY_LAWS, **_NO_AUTHORITY_FLAGS}.items():
        if type(core[field]) is not bool or core[field] is not expected:
            _refuse(field, f"{field} must remain literal {expected!r}")

    return {
        "schema_version": CONSENSUS_ADMISSION_POLICY_SCHEMA,
        "consensus_evaluation_schema": EVALUATION_SCHEMA,
        "attestation_scheme": ATTESTATION_SCHEME,
        "required_independent_support": required_support,
        "maximum_evidence_records": maximum_evidence,
        "maximum_ballots": maximum_ballots,
        "maximum_attestations": maximum_attestations,
        **_SAFETY_LAWS,
        **_NO_AUTHORITY_FLAGS,
    }


def derive_consensus_admission_policy_digest(core: object) -> str:
    """Derive the domain-separated digest of one exact policy core."""

    normalized = _normalize_policy_core(core)
    return sha256_digest(
        {
            "domain": CONSENSUS_ADMISSION_POLICY_DIGEST_DOMAIN,
            **normalized,
        }
    )


@dataclass(frozen=True)
class ConsensusAdmissionPolicyV1:
    """Immutable policy manifest with hard fail-closed safety laws."""

    required_independent_support: int
    maximum_evidence_records: int
    maximum_ballots: int
    maximum_attestations: int
    policy_digest: str
    consensus_evaluation_schema: str = EVALUATION_SCHEMA
    attestation_scheme: str = ATTESTATION_SCHEME
    require_direct_log_append: bool = True
    require_empty_target_at_base: bool = True
    require_complete_committed_source_set: bool = True
    require_trusted_provenance: bool = True
    require_signer_identity_correlation: bool = True
    require_reviewer_scope_correlation: bool = True
    stop_latched_blocks: bool = True
    veto_latched_blocks: bool = True
    observer_only: bool = True
    advisory_only: bool = True
    authority_granted: bool = False
    activation_performed: bool = False
    routing_influence_applied: bool = False
    schema_version: str = CONSENSUS_ADMISSION_POLICY_SCHEMA

    def __post_init__(self) -> None:
        parsed = parse_consensus_admission_policy(self.to_mapping())
        if parsed["policy_digest"] != self.policy_digest:
            _refuse("policy_digest_mismatch", "policy digest mismatch")

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "consensus_evaluation_schema": self.consensus_evaluation_schema,
            "attestation_scheme": self.attestation_scheme,
            "required_independent_support": self.required_independent_support,
            "maximum_evidence_records": self.maximum_evidence_records,
            "maximum_ballots": self.maximum_ballots,
            "maximum_attestations": self.maximum_attestations,
            "require_direct_log_append": self.require_direct_log_append,
            "require_empty_target_at_base": self.require_empty_target_at_base,
            "require_complete_committed_source_set": (
                self.require_complete_committed_source_set
            ),
            "require_trusted_provenance": self.require_trusted_provenance,
            "require_signer_identity_correlation": (
                self.require_signer_identity_correlation
            ),
            "require_reviewer_scope_correlation": (
                self.require_reviewer_scope_correlation
            ),
            "stop_latched_blocks": self.stop_latched_blocks,
            "veto_latched_blocks": self.veto_latched_blocks,
            "observer_only": self.observer_only,
            "advisory_only": self.advisory_only,
            "authority_granted": self.authority_granted,
            "activation_performed": self.activation_performed,
            "routing_influence_applied": self.routing_influence_applied,
            "policy_digest": self.policy_digest,
        }


def build_consensus_admission_policy(
    *,
    required_independent_support: int,
    maximum_evidence_records: int = MAX_EVIDENCE_RECORDS,
    maximum_ballots: int = MAX_BALLOTS,
    maximum_attestations: int = MAX_ATTESTATIONS,
) -> ConsensusAdmissionPolicyV1:
    """Build one deterministic safety policy without granting authority."""

    core = _normalize_policy_core(
        {
            "schema_version": CONSENSUS_ADMISSION_POLICY_SCHEMA,
            "consensus_evaluation_schema": EVALUATION_SCHEMA,
            "attestation_scheme": ATTESTATION_SCHEME,
            "required_independent_support": required_independent_support,
            "maximum_evidence_records": maximum_evidence_records,
            "maximum_ballots": maximum_ballots,
            "maximum_attestations": maximum_attestations,
            **_SAFETY_LAWS,
            **_NO_AUTHORITY_FLAGS,
        }
    )
    return ConsensusAdmissionPolicyV1(
        **{
            key: core[key]
            for key in (
                "required_independent_support",
                "maximum_evidence_records",
                "maximum_ballots",
                "maximum_attestations",
            )
        },
        policy_digest=derive_consensus_admission_policy_digest(core),
    )


def parse_consensus_admission_policy(value: object) -> dict[str, object]:
    """Strictly parse and privately copy an exact v1 policy mapping."""

    if type(value) is ConsensusAdmissionPolicyV1:
        try:
            value = value.to_mapping()
        except AttributeError:
            _refuse(
                "policy_malformed_instance",
                "policy instance is missing a required field",
            )
    policy = _exact_dict(value, CONSENSUS_ADMISSION_POLICY_KEYS)
    core = _normalize_policy_core(
        {key: policy[key] for key in CONSENSUS_ADMISSION_POLICY_CORE_KEYS}
    )
    claimed = policy["policy_digest"]
    if type(claimed) is not str:
        _refuse("policy_digest", "policy_digest must be a string digest")
    expected = derive_consensus_admission_policy_digest(core)
    if claimed != expected:
        _refuse("policy_digest_mismatch", "policy digest does not match content")
    return {**core, "policy_digest": expected}


def verify_consensus_admission_policy(
    value: object,
) -> tuple[bool, Optional[str]]:
    try:
        parse_consensus_admission_policy(value)
    except ConsensusAdmissionPolicyError as exc:
        return False, exc.reason
    return True, None


__all__ = [
    "CONSENSUS_ADMISSION_POLICY_CORE_KEYS",
    "CONSENSUS_ADMISSION_POLICY_DIGEST_DOMAIN",
    "CONSENSUS_ADMISSION_POLICY_KEYS",
    "CONSENSUS_ADMISSION_POLICY_SCHEMA",
    "ConsensusAdmissionPolicyError",
    "ConsensusAdmissionPolicyV1",
    "build_consensus_admission_policy",
    "derive_consensus_admission_policy_digest",
    "parse_consensus_admission_policy",
    "verify_consensus_admission_policy",
]
