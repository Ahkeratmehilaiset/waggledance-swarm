# SPDX-License-Identifier: BUSL-1.1
"""Fail-closed, advisory gate over committed authenticated consensus.

The gate composes five previously separate proofs:

* an independently rebound activation-admission intent,
* a fixed, content-addressed consensus policy,
* a direct append from the intent's log base to a pinned closed head,
* an exact bijection between the committed entries and supplied payloads, and
* active trusted provenance plus HMAC authentication for every ballot.

The returned receipt is an auditable recommendation only.  This module never
performs the activation CAS, changes routing, grants authority, or authenticates
the external registry/log heads.  Completeness is relative to the independently
pinned closed head; a persistence adapter must provide authenticated closure and
later fence that head again before any separate activation operation.
"""

from __future__ import annotations

import re
from typing import Callable, Optional

from waggledance.core.capabilities.activation_admission_intent import (
    CURRENT_POINTER_KEYS,
    PROPOSED_POINTER_KEYS,
    ActivationAdmissionIntentError,
    build_activation_admission_intent,
    parse_activation_admission_intent,
    verify_activation_admission_intent_bindings,
)
from waggledance.core.capabilities.activation_contracts import MAX_GENERATION
from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.orchestration.attestation_log import (
    AttestationLogContractError,
    build_attestation_log_entry,
    derive_committed_attestation_set,
    parse_attestation_log_snapshot,
    verify_attestation_log_transition,
)
from waggledance.core.orchestration.consensus_admission_policy import (
    ConsensusAdmissionPolicyError,
    parse_consensus_admission_policy,
)
from waggledance.core.orchestration.evidence_attestation import (
    MAX_ATTESTATIONS,
    OBSERVATION_DIGEST_DOMAIN,
    OBSERVATION_KEYS,
    OBSERVATION_SCHEMA,
    EvidenceAttestationError,
    canonicalize_evidence_attestation,
    evaluate_attested_inhibitory_consensus,
)
from waggledance.core.orchestration.evidence_consensus import (
    MAX_BALLOTS,
    MAX_EVIDENCE_RECORDS,
    MAX_REQUIRED_SUPPORT,
    PROVENANCE_DIMENSIONS,
    EvidenceConsensusError,
    parse_consensus_evaluation_structure,
    parse_evidence_diversity,
    parse_inhibitory_ballot,
)
from waggledance.core.orchestration.provenance_registry import (
    ProvenanceRegistryError,
    parse_provenance_registry_snapshot,
)

ATTESTED_CONSENSUS_GATE_RECEIPT_SCHEMA = (
    "wd.attested_consensus_gate_receipt.v1"
)
ATTESTED_CONSENSUS_GATE_RECEIPT_DIGEST_DOMAIN = (
    "wd.attested_consensus_gate_receipt.digest.v1"
)

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_CONTEXT_FIELDS = (
    "query_digest",
    "decision_digest",
    "candidate_digest",
    "activation_head_digest",
)

_VERIFICATION_FLAGS = {
    "intent_bindings_verified": True,
    "policy_verified": True,
    "direct_log_transition_verified": True,
    "base_target_set_verified_empty": True,
    "source_set_completeness_verified": True,
    "trusted_provenance_verified": True,
    "all_committed_attestations_authenticated": True,
}

_NO_AUTHORITY_FLAGS = {
    "activation_performed": False,
    "routing_influence_applied": False,
    "advisory_only": True,
    "authority_granted": False,
}

ATTESTED_CONSENSUS_GATE_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "expected_current_pointer",
        "proposed_pointer",
        "activation_scope_digest",
        *_CONTEXT_FIELDS,
        "admission_challenge_digest",
        "trust_registry_head_digest",
        "attestation_log_base_head_digest",
        "attestation_log_closed_head_digest",
        "consensus_policy_digest",
        "required_independent_support",
        "committed_attestation_set_digest",
        "committed_entry_count",
        "trusted_provenance_binding_digests",
        "attested_consensus_observation",
        *_VERIFICATION_FLAGS,
        "consensus_gate_passed",
        "positive_admission_ready",
        "activation_admission_advised",
        *_NO_AUTHORITY_FLAGS,
        "gate_receipt_digest",
    }
)

KeyLookup = Callable[[str, str, str], Optional[bytes]]


class AttestedConsensusGateError(ValueError):
    """A stable refusal at the composed advisory admission boundary."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _refuse(reason: str, message: str) -> None:
    raise AttestedConsensusGateError(reason, message)


def _digest(value: object, label: str) -> str:
    if type(value) is not str or not _SHA256.fullmatch(value):
        _refuse(label, f"{label} must be a lowercase sha256 digest")
    return value


def _exact_dict(
    value: object, keys: frozenset[str], label: str
) -> dict[str, object]:
    if type(value) is not dict:
        _refuse(f"{label}_type", f"{label} must be an exact dict")
    if dict.__len__(value) > len(keys):
        _refuse(f"{label}_keyset", f"{label} exceeds its exact key bound")
    snapshot = value.copy()
    if dict.__len__(snapshot) > len(keys):
        _refuse(f"{label}_keyset", f"{label} exceeds its exact key bound")
    if set(snapshot) != keys or any(type(key) is not str for key in snapshot):
        _refuse(f"{label}_keyset", f"{label} has a non-exact keyset")
    return snapshot


def _bounded_wire_list(value: object, label: str, maximum: int) -> list:
    """Copy at most ``maximum + 1`` exact-list members, then recheck."""

    if type(value) is not list:
        _refuse(f"{label}_type", f"{label} must be an exact list")
    if list.__len__(value) > maximum:
        _refuse(f"{label}_count_exceeded", f"{label} exceeds its bound")
    snapshot = value[: maximum + 1]
    if list.__len__(snapshot) > maximum:
        _refuse(f"{label}_count_exceeded", f"{label} exceeds its bound")
    return snapshot


def _pointer(
    value: object,
    *,
    keys: frozenset[str],
    label: str,
) -> dict[str, object]:
    pointer = _exact_dict(value, keys, label)
    for field in keys - {"store_revision"}:
        pointer[field] = _digest(pointer[field], f"{label}_{field}")
    revision = pointer["store_revision"]
    if type(revision) is not int or not 0 <= revision <= MAX_GENERATION:
        _refuse(f"{label}_store_revision", "store revision must be nonnegative")
    return pointer


def _parse_policy_and_intent(
    *,
    activation_admission_intent: object,
    policy: object,
    expected_consensus_policy_digest: str,
    expected_activation_scope_digest: str,
    expected_query_digest: str,
    expected_current_bundle_digest: str,
    expected_current_activation_head_digest: str,
    expected_current_store_revision: int,
    expected_proposed_bundle_digest: str,
    expected_proposed_activation_head_digest: str,
    expected_proposed_store_revision: int,
    expected_trust_registry_head_digest: str,
    expected_attestation_log_base_head_digest: str,
) -> tuple[dict[str, object], dict[str, object]]:
    try:
        parsed_policy = parse_consensus_admission_policy(policy)
    except ConsensusAdmissionPolicyError as exc:
        _refuse(f"policy:{exc.reason}", "consensus policy refused")
    expected_policy_digest = _digest(
        expected_consensus_policy_digest,
        "expected_consensus_policy_digest",
    )
    if parsed_policy["policy_digest"] != expected_policy_digest:
        _refuse("stale_consensus_policy", "policy does not match the pinned digest")

    try:
        intent = parse_activation_admission_intent(
            activation_admission_intent
        )
    except ActivationAdmissionIntentError as exc:
        _refuse(f"intent:{exc.reason}", "activation admission intent refused")
    ok, reason = verify_activation_admission_intent_bindings(
        intent,
        expected_activation_scope_digest=expected_activation_scope_digest,
        expected_query_digest=expected_query_digest,
        expected_current_bundle_digest=expected_current_bundle_digest,
        expected_current_activation_head_digest=(
            expected_current_activation_head_digest
        ),
        expected_current_store_revision=expected_current_store_revision,
        expected_proposed_bundle_digest=expected_proposed_bundle_digest,
        expected_proposed_activation_head_digest=(
            expected_proposed_activation_head_digest
        ),
        expected_proposed_store_revision=expected_proposed_store_revision,
        expected_trust_registry_head_digest=expected_trust_registry_head_digest,
        expected_attestation_log_base_head_digest=(
            expected_attestation_log_base_head_digest
        ),
        expected_consensus_policy_digest=expected_policy_digest,
        expected_required_independent_support=parsed_policy[
            "required_independent_support"
        ],
    )
    if not ok:
        _refuse(f"intent_binding:{reason or 'unknown'}", "intent binding refused")
    return parsed_policy, intent


def _parse_registry(
    snapshot: object, expected_head: str
) -> tuple[dict[str, object], str]:
    head = _digest(expected_head, "expected_trust_registry_head_digest")
    try:
        parsed = parse_provenance_registry_snapshot(snapshot)
    except ProvenanceRegistryError as exc:
        _refuse(f"registry:{exc.reason}", "provenance registry refused")
    if parsed["registry_head_digest"] != head:
        _refuse("stale_trust_registry_head", "registry head is not pinned head")
    return parsed, head


def _parse_log_window(
    *,
    base_snapshot: object,
    closed_snapshot: object,
    expected_base_head: str,
    expected_closed_head: str,
    activation_scope_digest: str,
    admission_challenge_digest: str,
) -> tuple[object, object, object]:
    base_head = _digest(
        expected_base_head, "expected_attestation_log_base_head_digest"
    )
    closed_head = _digest(
        expected_closed_head, "expected_attestation_log_closed_head_digest"
    )
    try:
        base = parse_attestation_log_snapshot(base_snapshot)
        closed = parse_attestation_log_snapshot(closed_snapshot)
    except AttestationLogContractError as exc:
        _refuse(f"attestation_log:{exc.reason}", "attestation log refused")
    if base.log_head_digest != base_head:
        _refuse("stale_attestation_log_base_head", "base log head is stale")
    if closed.log_head_digest != closed_head:
        _refuse("stale_attestation_log_closed_head", "closed log head is stale")
    ok, reason = verify_attestation_log_transition(
        base,
        closed,
        expected_current_log_head_digest=base_head,
    )
    if not ok:
        _refuse(
            f"attestation_log_transition:{reason or 'unknown'}",
            "closed log is not the direct append successor",
        )
    try:
        base_target = derive_committed_attestation_set(
            base,
            base_head,
            activation_scope_digest,
            admission_challenge_digest,
        )
        committed = derive_committed_attestation_set(
            closed,
            closed_head,
            activation_scope_digest,
            admission_challenge_digest,
        )
    except AttestationLogContractError as exc:
        _refuse(f"committed_set:{exc.reason}", "committed set refused")
    if not base_target.empty:
        _refuse(
            "preexisting_challenge_entries",
            "target scope/challenge was not empty at the collection base",
        )
    return base, closed, committed


def _parse_exact_sources(
    *,
    evidence_input: list,
    ballot_input: list,
    attestation_input: list,
    context: dict[str, str],
    trust_registry_head_digest: str,
    activation_scope_digest: str,
    admission_challenge_digest: str,
) -> tuple[
    list[dict[str, str]],
    list[dict[str, object]],
    list[dict[str, object]],
]:
    try:
        evidence = [parse_evidence_diversity(item) for item in evidence_input]
    except EvidenceConsensusError:
        _refuse("evidence_invalid", "an evidence payload is malformed")
    try:
        ballots = [parse_inhibitory_ballot(item) for item in ballot_input]
    except EvidenceConsensusError:
        _refuse("ballot_invalid", "a ballot payload is malformed")
    try:
        attestations = [
            canonicalize_evidence_attestation(item)
            for item in attestation_input
        ]
    except EvidenceAttestationError as exc:
        _refuse(f"attestation:{exc}", "an attestation payload is malformed")

    for label, items, digest_field in (
        ("evidence", evidence, "evidence_digest"),
        ("ballot", ballots, "ballot_digest"),
        ("attestation", attestations, "attestation_digest"),
    ):
        digests = [item[digest_field] for item in items]
        if len(digests) != len(set(digests)):
            _refuse(f"duplicate_{label}_digest", f"duplicate {label} refused")

    if any(
        any(item[field] != context[field] for field in _CONTEXT_FIELDS)
        for item in (*evidence, *ballots)
    ):
        _refuse("source_context_mismatch", "evidence or ballot context is stale")
    if any(
        item["trust_registry_head_digest"] != trust_registry_head_digest
        or item["activation_scope_digest"] != activation_scope_digest
        or item["admission_challenge_digest"] != admission_challenge_digest
        for item in attestations
    ):
        _refuse("attestation_context_mismatch", "attestation context is stale")
    return evidence, ballots, attestations


def _verify_committed_payload_bijection(
    *,
    committed: object,
    evidence: list[dict[str, str]],
    ballots: list[dict[str, object]],
    attestations: list[dict[str, object]],
) -> None:
    evidence_by_digest = {item["evidence_digest"]: item for item in evidence}
    ballot_by_digest = {item["ballot_digest"]: item for item in ballots}
    attestation_by_digest = {
        item["attestation_digest"]: item for item in attestations
    }
    attestation_by_ballot: dict[str, dict[str, object]] = {}
    for attestation in attestations:
        ballot_digest = attestation["ballot_digest"]
        if ballot_digest in attestation_by_ballot:
            _refuse(
                "ballot_attestation_cardinality",
                "every ballot must have exactly one attestation",
            )
        attestation_by_ballot[ballot_digest] = attestation

    if set(attestation_by_ballot) != set(ballot_by_digest):
        _refuse(
            "ballot_attestation_set_mismatch",
            "ballot and attestation sets are not bijective",
        )
    referenced_evidence = {
        ballot["evidence_digest"] for ballot in ballot_by_digest.values()
    }
    if referenced_evidence != set(evidence_by_digest):
        _refuse(
            "evidence_ballot_set_mismatch",
            "every and only supplied evidence must be referenced",
        )

    expected_entries: dict[str, dict[str, object]] = {}
    for ballot_digest, ballot in ballot_by_digest.items():
        evidence_item = evidence_by_digest.get(ballot["evidence_digest"])
        if evidence_item is None:
            _refuse("ballot_orphan", "ballot references absent evidence")
        attestation = attestation_by_ballot[ballot_digest]
        if (
            attestation["evidence_digest"] != evidence_item["evidence_digest"]
            or attestation["reviewer_lineage_digest"]
            != evidence_item["reviewer_lineage_digest"]
        ):
            _refuse(
                "attestation_source_binding_mismatch",
                "attestation is not bound to its exact evidence source",
            )
        entry = build_attestation_log_entry(
            activation_scope_digest=attestation["activation_scope_digest"],
            admission_challenge_digest=attestation[
                "admission_challenge_digest"
            ],
            evidence_digest=evidence_item["evidence_digest"],
            ballot_digest=ballot_digest,
            attestation_digest=attestation["attestation_digest"],
            reviewer_lineage_digest=evidence_item[
                "reviewer_lineage_digest"
            ],
        )
        expected_entries[entry.entry_digest] = entry.to_mapping()

    committed_entries = {
        item.entry_digest: item.to_mapping() for item in committed.entries
    }
    if expected_entries != committed_entries:
        _refuse(
            "committed_payload_set_mismatch",
            "committed entries and supplied payloads are not exactly equal",
        )
    if set(attestation_by_digest) != {
        item.attestation_digest for item in committed.entries
    }:
        _refuse(
            "committed_attestation_set_mismatch",
            "committed attestation digests are not exact",
        )


def _verify_trusted_provenance(
    *,
    registry_snapshot: dict[str, object],
    expected_registry_head: str,
    evidence: list[dict[str, str]],
    ballots: list[dict[str, object]],
    attestations: list[dict[str, object]],
) -> tuple[list[str], set[tuple[str, str, str]]]:
    evidence_by_digest = {item["evidence_digest"]: item for item in evidence}
    ballot_by_digest = {item["ballot_digest"]: item for item in ballots}
    trusted_by_key = {
        binding["signing_key_digest"]: binding
        for binding in registry_snapshot["bindings"]
    }
    resolved_by_key: dict[str, dict[str, object]] = {}
    facts_by_signer_cell: dict[str, tuple[object, ...]] = {}
    facts_by_reviewer_scope: dict[str, tuple[object, ...]] = {}
    allowed_key_lookups: set[tuple[str, str, str]] = set()

    for attestation in attestations:
        key_digest = attestation["signing_key_digest"]
        binding = resolved_by_key.get(key_digest)
        if binding is None:
            resolved = trusted_by_key.get(key_digest)
            if resolved is None:
                _refuse(
                    "trusted_provenance:signing_key_not_found",
                    "signing key has no trusted provenance binding",
                )
            if resolved["status"] != "active":
                _refuse(
                    "trusted_provenance:signing_key_revoked",
                    "trusted provenance binding is revoked",
                )
            binding = dict(resolved)
            resolved_by_key[key_digest] = binding

        provenance_facts = tuple(
            binding[field] for field in PROVENANCE_DIMENSIONS
        )
        signer_cell = binding["signer_cell_id"]
        reviewer_scope = binding["reviewer_activation_scope_digest"]
        cell_facts = (reviewer_scope, *provenance_facts)
        prior_cell_facts = facts_by_signer_cell.setdefault(
            signer_cell, cell_facts
        )
        if prior_cell_facts != cell_facts:
            _refuse(
                "signer_cell_identity_conflict",
                "one signer cell claims multiple reviewer identities",
            )
        scope_facts = (signer_cell, *provenance_facts)
        prior_scope_facts = facts_by_reviewer_scope.setdefault(
            reviewer_scope, scope_facts
        )
        if prior_scope_facts != scope_facts:
            _refuse(
                "reviewer_scope_identity_conflict",
                "one reviewer scope claims multiple signer identities",
            )
        ballot = ballot_by_digest[attestation["ballot_digest"]]
        evidence_item = evidence_by_digest[ballot["evidence_digest"]]
        if (
            binding["reviewer_lineage_digest"]
            != attestation["reviewer_lineage_digest"]
        ):
            _refuse(
                "trusted_lineage_mismatch",
                "attestation lineage differs from trusted provenance",
            )
        if any(
            evidence_item[field] != binding[field]
            for field in PROVENANCE_DIMENSIONS
        ):
            _refuse(
                "trusted_provenance_mismatch",
                "evidence provenance differs from the trusted binding",
            )
        allowed_key_lookups.add(
            (
                expected_registry_head,
                attestation["reviewer_lineage_digest"],
                key_digest,
            )
        )

    binding_digests = sorted(
        {binding["binding_digest"] for binding in resolved_by_key.values()}
    )
    return binding_digests, allowed_key_lookups


def _derive_receipt_digest(receipt_without_digest: dict[str, object]) -> str:
    return sha256_digest(
        {
            "domain": ATTESTED_CONSENSUS_GATE_RECEIPT_DIGEST_DOMAIN,
            **receipt_without_digest,
        }
    )


def evaluate_attested_consensus_gate(
    *,
    activation_admission_intent: object,
    policy: object,
    expected_consensus_policy_digest: str,
    expected_activation_scope_digest: str,
    expected_query_digest: str,
    expected_current_bundle_digest: str,
    expected_current_activation_head_digest: str,
    expected_current_store_revision: int,
    expected_proposed_bundle_digest: str,
    expected_proposed_activation_head_digest: str,
    expected_proposed_store_revision: int,
    provenance_registry_snapshot: object,
    expected_trust_registry_head_digest: str,
    attestation_log_base_snapshot: object,
    expected_attestation_log_base_head_digest: str,
    attestation_log_closed_snapshot: object,
    expected_attestation_log_closed_head_digest: str,
    evidence_records: object,
    ballots: object,
    attestations: object,
    key_lookup: KeyLookup,
) -> dict[str, object]:
    """Evaluate one complete committed batch and return an advisory receipt."""

    # Copy untrusted aggregates once at hard protocol bounds before any key
    # lookup.  The policy may tighten these bounds after it is authenticated.
    evidence_input = _bounded_wire_list(
        evidence_records, "evidence_records", MAX_EVIDENCE_RECORDS
    )
    ballot_input = _bounded_wire_list(ballots, "ballots", MAX_BALLOTS)
    attestation_input = _bounded_wire_list(
        attestations, "attestations", MAX_ATTESTATIONS
    )

    parsed_policy, intent = _parse_policy_and_intent(
        activation_admission_intent=activation_admission_intent,
        policy=policy,
        expected_consensus_policy_digest=expected_consensus_policy_digest,
        expected_activation_scope_digest=expected_activation_scope_digest,
        expected_query_digest=expected_query_digest,
        expected_current_bundle_digest=expected_current_bundle_digest,
        expected_current_activation_head_digest=(
            expected_current_activation_head_digest
        ),
        expected_current_store_revision=expected_current_store_revision,
        expected_proposed_bundle_digest=expected_proposed_bundle_digest,
        expected_proposed_activation_head_digest=(
            expected_proposed_activation_head_digest
        ),
        expected_proposed_store_revision=expected_proposed_store_revision,
        expected_trust_registry_head_digest=expected_trust_registry_head_digest,
        expected_attestation_log_base_head_digest=(
            expected_attestation_log_base_head_digest
        ),
    )
    for label, values, bound_field in (
        ("evidence_records", evidence_input, "maximum_evidence_records"),
        ("ballots", ballot_input, "maximum_ballots"),
        ("attestations", attestation_input, "maximum_attestations"),
    ):
        if len(values) > parsed_policy[bound_field]:
            _refuse(f"policy_{label}_count_exceeded", f"{label} exceeds policy")

    registry, registry_head = _parse_registry(
        provenance_registry_snapshot,
        expected_trust_registry_head_digest,
    )
    if intent["trust_registry_head_digest"] != registry_head:
        _refuse("intent_trust_registry_head_mismatch", "intent trust head stale")

    base_head = _digest(
        expected_attestation_log_base_head_digest,
        "expected_attestation_log_base_head_digest",
    )
    if intent["attestation_log_base_head_digest"] != base_head:
        _refuse("intent_attestation_log_base_head_mismatch", "intent base stale")
    _, closed, committed = _parse_log_window(
        base_snapshot=attestation_log_base_snapshot,
        closed_snapshot=attestation_log_closed_snapshot,
        expected_base_head=base_head,
        expected_closed_head=expected_attestation_log_closed_head_digest,
        activation_scope_digest=intent["activation_scope_digest"],
        admission_challenge_digest=intent["admission_challenge_digest"],
    )
    if committed.entry_count > parsed_policy["maximum_attestations"]:
        _refuse(
            "policy_committed_entry_count_exceeded",
            "committed matching entries exceed policy",
        )

    context = {
        field: intent[field] for field in _CONTEXT_FIELDS
    }  # type: ignore[assignment]
    evidence, parsed_ballots, parsed_attestations = _parse_exact_sources(
        evidence_input=evidence_input,
        ballot_input=ballot_input,
        attestation_input=attestation_input,
        context=context,
        trust_registry_head_digest=registry_head,
        activation_scope_digest=intent["activation_scope_digest"],
        admission_challenge_digest=intent["admission_challenge_digest"],
    )
    _verify_committed_payload_bijection(
        committed=committed,
        evidence=evidence,
        ballots=parsed_ballots,
        attestations=parsed_attestations,
    )
    binding_digests, allowed_key_lookups = _verify_trusted_provenance(
        registry_snapshot=registry,
        expected_registry_head=registry_head,
        evidence=evidence,
        ballots=parsed_ballots,
        attestations=parsed_attestations,
    )

    def pinned_key_lookup(head: str, lineage: str, key_digest: str) -> Optional[bytes]:
        if (head, lineage, key_digest) not in allowed_key_lookups:
            return None
        return key_lookup(head, lineage, key_digest)

    try:
        observation = evaluate_attested_inhibitory_consensus(
            **context,
            expected_trust_registry_head_digest=registry_head,
            expected_activation_scope_digest=intent["activation_scope_digest"],
            expected_admission_challenge_digest=intent[
                "admission_challenge_digest"
            ],
            evidence_records=evidence,
            ballots=parsed_ballots,
            attestations=parsed_attestations,
            required_independent_support=parsed_policy[
                "required_independent_support"
            ],
            key_lookup=pinned_key_lookup,
        )
    except EvidenceAttestationError as exc:
        _refuse(f"authenticated_consensus:{exc}", "HMAC consensus refused")

    consensus = observation["claimed_provenance_consensus"]
    gate_passed = consensus["acceptance_advised"] is True
    receipt: dict[str, object] = {
        "schema_version": ATTESTED_CONSENSUS_GATE_RECEIPT_SCHEMA,
        "expected_current_pointer": dict(intent["expected_current_pointer"]),
        "proposed_pointer": dict(intent["proposed_pointer"]),
        "activation_scope_digest": intent["activation_scope_digest"],
        **context,
        "admission_challenge_digest": intent["admission_challenge_digest"],
        "trust_registry_head_digest": registry_head,
        "attestation_log_base_head_digest": base_head,
        "attestation_log_closed_head_digest": closed.log_head_digest,
        "consensus_policy_digest": parsed_policy["policy_digest"],
        "required_independent_support": parsed_policy[
            "required_independent_support"
        ],
        "committed_attestation_set_digest": (
            committed.committed_attestation_set_digest
        ),
        "committed_entry_count": committed.entry_count,
        "trusted_provenance_binding_digests": binding_digests,
        "attested_consensus_observation": observation,
        **_VERIFICATION_FLAGS,
        "consensus_gate_passed": gate_passed,
        "positive_admission_ready": gate_passed,
        "activation_admission_advised": gate_passed,
        **_NO_AUTHORITY_FLAGS,
    }
    receipt["gate_receipt_digest"] = _derive_receipt_digest(receipt)
    return receipt


def _parse_observation_shell(value: object) -> dict[str, object]:
    observation = _exact_dict(value, OBSERVATION_KEYS, "observation")
    if (
        type(observation["schema_version"]) is not str
        or observation["schema_version"] != OBSERVATION_SCHEMA
    ):
        _refuse("observation_schema", "nested observation schema refused")
    for field in (
        *_CONTEXT_FIELDS,
        "trust_registry_head_digest",
        "activation_scope_digest",
        "admission_challenge_digest",
        "claimed_provenance_consensus_digest",
        "observation_digest",
    ):
        _digest(observation[field], f"observation_{field}")

    support = observation["required_independent_support"]
    if type(support) is not int or not 1 <= support <= MAX_REQUIRED_SUPPORT:
        _refuse("observation_required_support", "observation support refused")
    for field, maximum in (
        ("evidence_digests", MAX_EVIDENCE_RECORDS),
        ("ballot_digests", MAX_BALLOTS),
        ("attestation_digests", MAX_ATTESTATIONS),
    ):
        values = _bounded_wire_list(observation[field], field, maximum)
        if any(
            type(item) is not str or not _SHA256.fullmatch(item)
            for item in values
        ) or values != sorted(set(values)):
            _refuse(f"observation_{field}", f"observation {field} refused")
        observation[field] = values

    observation_flags = {
        "claimed_provenance_only": True,
        "source_set_completeness_verified": False,
        "positive_admission_ready": False,
        "activation_admission_advised": False,
        "activation_performed": False,
        "routing_influence_applied": False,
        "advisory_only": True,
        "authority_granted": False,
    }
    for field, expected in observation_flags.items():
        if type(observation[field]) is not bool or observation[field] is not expected:
            _refuse(
                "observation_authority_flags",
                f"nested observation {field} must remain {expected!r}",
            )

    try:
        consensus = parse_consensus_evaluation_structure(
            observation["claimed_provenance_consensus"]
        )
    except EvidenceConsensusError as exc:
        _refuse(f"claimed_consensus:{exc}", "claimed consensus refused")
    if (
        observation["claimed_provenance_consensus_digest"]
        != consensus["evaluation_digest"]
    ):
        _refuse(
            "observation_consensus_digest_mismatch",
            "observation does not bind its claimed consensus",
        )
    observation["claimed_provenance_consensus"] = consensus

    unsigned = dict(observation)
    claimed = unsigned.pop("observation_digest")
    try:
        expected_observation_digest = sha256_digest(
            {"domain": OBSERVATION_DIGEST_DOMAIN, **unsigned}
        )
    except (TypeError, ValueError):
        _refuse("observation_shape", "nested observation is not canonical")
    if claimed != expected_observation_digest:
        _refuse("observation_digest_mismatch", "nested observation digest mismatch")
    return observation


def parse_attested_consensus_gate_receipt(value: object) -> dict[str, object]:
    """Parse a receipt structurally; this alone does not prove its sources."""

    receipt = _exact_dict(
        value,
        ATTESTED_CONSENSUS_GATE_RECEIPT_KEYS,
        "gate_receipt",
    )
    if (
        type(receipt["schema_version"]) is not str
        or receipt["schema_version"] != ATTESTED_CONSENSUS_GATE_RECEIPT_SCHEMA
    ):
        _refuse("receipt_schema", "gate receipt schema refused")
    receipt["expected_current_pointer"] = _pointer(
        receipt["expected_current_pointer"],
        keys=CURRENT_POINTER_KEYS,
        label="expected_current_pointer",
    )
    receipt["proposed_pointer"] = _pointer(
        receipt["proposed_pointer"],
        keys=PROPOSED_POINTER_KEYS,
        label="proposed_pointer",
    )
    for field in (
        "activation_scope_digest",
        *_CONTEXT_FIELDS,
        "admission_challenge_digest",
        "trust_registry_head_digest",
        "attestation_log_base_head_digest",
        "attestation_log_closed_head_digest",
        "consensus_policy_digest",
        "committed_attestation_set_digest",
        "gate_receipt_digest",
    ):
        receipt[field] = _digest(receipt[field], field)
    if (
        receipt["attestation_log_closed_head_digest"]
        == receipt["attestation_log_base_head_digest"]
    ):
        _refuse(
            "attestation_log_head_not_advanced",
            "a direct append must advance the attestation-log head",
        )
    support = receipt["required_independent_support"]
    if type(support) is not int or not 1 <= support <= MAX_REQUIRED_SUPPORT:
        _refuse("required_independent_support", "receipt support bound refused")
    count = receipt["committed_entry_count"]
    if type(count) is not int or not 0 <= count <= MAX_ATTESTATIONS:
        _refuse("committed_entry_count", "receipt entry count refused")
    binding_digests = _bounded_wire_list(
        receipt["trusted_provenance_binding_digests"],
        "trusted_provenance_binding_digests",
        MAX_ATTESTATIONS,
    )
    if any(
        type(item) is not str or not _SHA256.fullmatch(item)
        for item in binding_digests
    ) or binding_digests != sorted(set(binding_digests)):
        _refuse("trusted_provenance_binding_digests", "binding digests refused")
    receipt["trusted_provenance_binding_digests"] = binding_digests
    observation = _parse_observation_shell(
        receipt["attested_consensus_observation"]
    )
    receipt["attested_consensus_observation"] = observation

    current = receipt["expected_current_pointer"]
    proposed = receipt["proposed_pointer"]
    if receipt["candidate_digest"] != proposed["bundle_digest"]:
        _refuse("candidate_pointer_mismatch", "candidate is not proposed bundle")
    if receipt["activation_head_digest"] != current["activation_head_digest"]:
        _refuse(
            "activation_head_pointer_mismatch",
            "review head is not the expected current activation head",
        )
    if current["store_revision"] == MAX_GENERATION:
        _refuse("revision_exhausted", "current pointer revision is exhausted")
    if proposed["store_revision"] != current["store_revision"] + 1:
        _refuse("revision_step", "proposed pointer must advance by one")
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

    try:
        rebuilt_intent = build_activation_admission_intent(
            activation_scope_digest=receipt["activation_scope_digest"],
            query_digest=receipt["query_digest"],
            expected_current_bundle_digest=current["bundle_digest"],
            expected_current_activation_head_digest=current[
                "activation_head_digest"
            ],
            expected_current_store_revision=current["store_revision"],
            proposed_bundle_digest=proposed["bundle_digest"],
            proposed_activation_head_digest=proposed["activation_head_digest"],
            proposed_store_revision=proposed["store_revision"],
            proposed_previous_bundle_digest=proposed["previous_bundle_digest"],
            proposed_previous_activation_head_digest=proposed[
                "previous_activation_head_digest"
            ],
            trust_registry_head_digest=receipt["trust_registry_head_digest"],
            attestation_log_base_head_digest=receipt[
                "attestation_log_base_head_digest"
            ],
            consensus_policy_digest=receipt["consensus_policy_digest"],
            required_independent_support=receipt[
                "required_independent_support"
            ],
        )
    except ActivationAdmissionIntentError as exc:
        _refuse(f"receipt_intent:{exc.reason}", "receipt intent binding refused")
    for field in (
        "candidate_digest",
        "activation_head_digest",
        "admission_challenge_digest",
        "decision_digest",
    ):
        if receipt[field] != rebuilt_intent[field]:
            _refuse(
                f"{field}_intent_mismatch",
                "receipt field differs from its rederived admission intent",
            )

    for field in _CONTEXT_FIELDS:
        if observation[field] != receipt[field]:
            _refuse(
                f"observation_{field}_mismatch",
                "nested observation context differs from receipt",
            )
    for observation_field, receipt_field in (
        ("trust_registry_head_digest", "trust_registry_head_digest"),
        ("activation_scope_digest", "activation_scope_digest"),
        ("admission_challenge_digest", "admission_challenge_digest"),
        ("required_independent_support", "required_independent_support"),
    ):
        if observation[observation_field] != receipt[receipt_field]:
            _refuse(
                f"observation_{observation_field}_mismatch",
                "nested observation binding differs from receipt",
            )
    nested_consensus = observation["claimed_provenance_consensus"]
    for field in (*_CONTEXT_FIELDS, "required_independent_support"):
        if nested_consensus[field] != observation[field]:
            _refuse(
                f"consensus_{field}_mismatch",
                "nested consensus binding differs from observation",
            )
    evidence_count = len(observation["evidence_digests"])
    ballot_count = len(observation["ballot_digests"])
    attestation_count = len(observation["attestation_digests"])
    if (
        count != ballot_count
        or count != attestation_count
        or evidence_count > count
        or (count > 0 and evidence_count == 0)
    ):
        _refuse(
            "committed_source_count_mismatch",
            "receipt counts do not describe one exact committed payload set",
        )
    if (
        nested_consensus["unique_evidence_count"] != evidence_count
        or nested_consensus["unique_ballot_count"] != ballot_count
    ):
        _refuse(
            "observation_consensus_count_mismatch",
            "observation digest lists differ from consensus unique counts",
        )
    if (
        nested_consensus["submitted_evidence_count"] != evidence_count
        or nested_consensus["submitted_ballot_count"] != ballot_count
        or nested_consensus["invalid_item_count"] != 0
        or nested_consensus["invalid_reasons"] != []
    ):
        _refuse(
            "gate_consensus_source_metadata_mismatch",
            "gate consensus must describe the exact fully valid source set",
        )
    observed_evidence_digests = set(observation["evidence_digests"])
    for field in (
        "stop_evidence_digests",
        "veto_evidence_digests",
        "negative_evidence_digests",
    ):
        if not set(nested_consensus[field]).issubset(
            observed_evidence_digests
        ):
            _refuse(
                f"consensus_{field}_outside_observation",
                "consensus inhibition references absent observed evidence",
            )
    if (
        len(binding_digests) < evidence_count
        or len(binding_digests) > attestation_count
        or (
        bool(binding_digests) != bool(attestation_count)
        )
    ):
        _refuse(
            "trusted_binding_count_mismatch",
            "trusted binding count exceeds committed attestations",
        )

    for field, expected in {**_VERIFICATION_FLAGS, **_NO_AUTHORITY_FLAGS}.items():
        if type(receipt[field]) is not bool or receipt[field] is not expected:
            _refuse(field, f"receipt {field} must remain {expected!r}")
    for field in (
        "consensus_gate_passed",
        "positive_admission_ready",
        "activation_admission_advised",
    ):
        if type(receipt[field]) is not bool:
            _refuse(field, f"receipt {field} must be an exact bool")
    advised = receipt["consensus_gate_passed"]
    if (
        receipt["positive_admission_ready"] is not advised
        or receipt["activation_admission_advised"] is not advised
    ):
        _refuse("advice_flags_mismatch", "receipt advice flags disagree")
    if nested_consensus["acceptance_advised"] is not advised:
        _refuse("nested_advice_mismatch", "receipt advice differs from consensus")

    unsigned = dict(receipt)
    claimed_digest = unsigned.pop("gate_receipt_digest")
    if claimed_digest != _derive_receipt_digest(unsigned):
        _refuse("gate_receipt_digest_mismatch", "receipt digest mismatch")
    return receipt


def verify_attested_consensus_gate_receipt(
    value: object,
    *,
    activation_admission_intent: object,
    policy: object,
    expected_consensus_policy_digest: str,
    expected_activation_scope_digest: str,
    expected_query_digest: str,
    expected_current_bundle_digest: str,
    expected_current_activation_head_digest: str,
    expected_current_store_revision: int,
    expected_proposed_bundle_digest: str,
    expected_proposed_activation_head_digest: str,
    expected_proposed_store_revision: int,
    provenance_registry_snapshot: object,
    expected_trust_registry_head_digest: str,
    attestation_log_base_snapshot: object,
    expected_attestation_log_base_head_digest: str,
    attestation_log_closed_snapshot: object,
    expected_attestation_log_closed_head_digest: str,
    evidence_records: object,
    ballots: object,
    attestations: object,
    key_lookup: KeyLookup,
) -> tuple[bool, Optional[str]]:
    """Recompute a receipt from caller-owned facts and exact source payloads."""

    try:
        parsed = parse_attested_consensus_gate_receipt(value)
        recomputed = evaluate_attested_consensus_gate(
            activation_admission_intent=activation_admission_intent,
            policy=policy,
            expected_consensus_policy_digest=expected_consensus_policy_digest,
            expected_activation_scope_digest=expected_activation_scope_digest,
            expected_query_digest=expected_query_digest,
            expected_current_bundle_digest=expected_current_bundle_digest,
            expected_current_activation_head_digest=(
                expected_current_activation_head_digest
            ),
            expected_current_store_revision=expected_current_store_revision,
            expected_proposed_bundle_digest=expected_proposed_bundle_digest,
            expected_proposed_activation_head_digest=(
                expected_proposed_activation_head_digest
            ),
            expected_proposed_store_revision=expected_proposed_store_revision,
            provenance_registry_snapshot=provenance_registry_snapshot,
            expected_trust_registry_head_digest=(
                expected_trust_registry_head_digest
            ),
            attestation_log_base_snapshot=attestation_log_base_snapshot,
            expected_attestation_log_base_head_digest=(
                expected_attestation_log_base_head_digest
            ),
            attestation_log_closed_snapshot=attestation_log_closed_snapshot,
            expected_attestation_log_closed_head_digest=(
                expected_attestation_log_closed_head_digest
            ),
            evidence_records=evidence_records,
            ballots=ballots,
            attestations=attestations,
            key_lookup=key_lookup,
        )
    except AttestedConsensusGateError as exc:
        return False, exc.reason
    if parsed != recomputed:
        return False, "gate_receipt_recompute_mismatch"
    return True, None


__all__ = [
    "ATTESTED_CONSENSUS_GATE_RECEIPT_DIGEST_DOMAIN",
    "ATTESTED_CONSENSUS_GATE_RECEIPT_KEYS",
    "ATTESTED_CONSENSUS_GATE_RECEIPT_SCHEMA",
    "AttestedConsensusGateError",
    "evaluate_attested_consensus_gate",
    "parse_attested_consensus_gate_receipt",
    "verify_attested_consensus_gate_receipt",
]
