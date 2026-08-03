"""Pure C8f successor-snapshot relation accounting.

This module composes the public C8e precondition accountant with one bounded,
deterministic, in-memory successor derivation.  It does not read a store,
write the derived bytes, perform compare-and-swap, close a TOCTOU window, or
grant build, execution, routing, recovery, promotion, or runtime authority.
"""

from __future__ import annotations

import json
from dataclasses import MISSING, dataclass, fields
from enum import Enum
from typing import Any

from waggledance.core.learning.understanding_attempt_reservation_cas import (
    SUPPLIED_ATTEMPT_RESERVATION_STATE_SNAPSHOT_SCHEMA,
    AttemptReservationCasContractError,
    AttemptReservationCasDisposition,
    AttemptReservationCasMode,
    AttemptReservationCasPolicyV1,
    AttemptReservationCasReceiptV1,
    AttemptReservationCasRequestV1,
    AttemptReservationExpectedSnapshotDigestV1,
    AttemptReservationState,
    AttemptReservationTransition,
    AttemptReservationTransitionProposalV1,
    derive_supplied_attempt_reservation_state_snapshot_digest,
    evaluate_attempt_reservation_cas_relation,
)
from waggledance.core.magma.canonical import (
    canonical_json_bytes,
    sha256_digest,
)


ATTEMPT_RESERVATION_SUCCESSOR_POLICY_SCHEMA = (
    "wd.understanding.attempt_reservation_successor_policy.v1"
)
ATTEMPT_RESERVATION_SUCCESSOR_REQUEST_SCHEMA = (
    "wd.understanding.attempt_reservation_successor_request.v1"
)
ATTEMPT_RESERVATION_SUCCESSOR_RECEIPT_SCHEMA = (
    "wd.understanding.attempt_reservation_successor_receipt.v1"
)

_ABSOLUTE_MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024
_ABSOLUTE_MAX_RESERVATION_RECORDS = 2048
_ABSOLUTE_MAX_JSON_DEPTH = 6
_ABSOLUTE_MAX_JSON_NODES = 32768
_SHA256_PREFIX = "sha256:"
_SHA256_LENGTH = len(_SHA256_PREFIX) + 64


class AttemptReservationSuccessorContractError(ValueError):
    """Raised when the C8f contract is malformed or internally inconsistent."""


class AttemptReservationSuccessorMode(str, Enum):
    OFF = "off"
    STATIC_SHADOW = "static_shadow"


class AttemptReservationSuccessorDisposition(str, Enum):
    REFUSED = "refused"
    PRECONDITION_RELATION_DOES_NOT_HOLD_NO_SUCCESSOR = (
        "precondition_relation_does_not_hold_no_successor"
    )
    OPEN_SUCCESSOR_RELATION_HOLDS_IN_LOCALLY_DERIVED_SNAPSHOT = (
        "open_successor_relation_holds_in_locally_derived_snapshot"
    )
    COMMIT_SUCCESSOR_RELATION_HOLDS_IN_LOCALLY_DERIVED_SNAPSHOT = (
        "commit_successor_relation_holds_in_locally_derived_snapshot"
    )
    ABORT_SUCCESSOR_RELATION_HOLDS_IN_LOCALLY_DERIVED_SNAPSHOT = (
        "abort_successor_relation_holds_in_locally_derived_snapshot"
    )
    SUCCESSOR_RESOURCE_BOUNDS_REFUSED = (
        "successor_resource_bounds_refused"
    )


class AttemptReservationSuccessorReasonCode(str, Enum):
    SOURCE_PRECONDITION_REFUSED = "source_precondition_refused"
    SOURCE_PRECONDITION_DOES_NOT_HOLD = (
        "source_precondition_does_not_hold"
    )
    OPEN_RESERVED_SUCCESSOR_DERIVED = "open_reserved_successor_derived"
    COMMIT_COMMITTED_SUCCESSOR_DERIVED = (
        "commit_committed_successor_derived"
    )
    ABORT_ABORTED_SUCCESSOR_DERIVED = "abort_aborted_successor_derived"
    SUCCESSOR_RECORD_LIMIT_EXCEEDED = "successor_record_limit_exceeded"
    SUCCESSOR_BYTE_LIMIT_EXCEEDED = "successor_byte_limit_exceeded"


def _require_sha256(value: object, label: str) -> str:
    if (
        type(value) is not str
        or len(value) != _SHA256_LENGTH
        or not value.startswith(_SHA256_PREFIX)
    ):
        raise AttemptReservationSuccessorContractError(
            f"{label} must be canonical sha256"
        )
    suffix = value[len(_SHA256_PREFIX) :]
    if any(character not in "0123456789abcdef" for character in suffix):
        raise AttemptReservationSuccessorContractError(
            f"{label} must be canonical sha256"
        )
    return value


def _require_bounded_int(
    value: object,
    label: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int or value < minimum or value > maximum:
        raise AttemptReservationSuccessorContractError(f"{label} refused")
    return value


_ACCOUNTING_POLICY = {
    "schema": (
        "wd.understanding.attempt_reservation_successor_"
        "accounting_policy.v1"
    ),
    "source_precondition_contract": (
        "wd.understanding.attempt_reservation_cas_receipt.v1"
    ),
    "snapshot_schema": SUPPLIED_ATTEMPT_RESERVATION_STATE_SNAPSHOT_SCHEMA,
    "source_snapshot": "caller_supplied_only",
    "expected_digest_source": "caller_supplied_keyword_only",
    "proposal_source": "caller_supplied_keyword_only",
    "successor_derivation": "pure_local_canonical_relation_only",
    "target_state_by_transition": {
        AttemptReservationTransition.OPEN_IF_ABSENT.value: (
            AttemptReservationState.RESERVED.value
        ),
        AttemptReservationTransition.COMMIT_IF_RESERVED.value: (
            AttemptReservationState.COMMITTED.value
        ),
        AttemptReservationTransition.ABORT_IF_RESERVED.value: (
            AttemptReservationState.ABORTED.value
        ),
    },
    "state_evidence": "fixed_domain_derived_from_base_and_proposal",
    "successor_raw_bytes_returned": False,
    "source_snapshot_current": False,
    "successor_snapshot_current": False,
    "atomic_cas_performed": False,
    "transition_persisted": False,
    "authority_granted": False,
}
ATTEMPT_RESERVATION_SUCCESSOR_ACCOUNTING_POLICY_DIGEST = sha256_digest(
    _ACCOUNTING_POLICY
)


@dataclass(frozen=True, slots=True)
class AttemptReservationSuccessorPolicyV1:
    mode: AttemptReservationSuccessorMode = AttemptReservationSuccessorMode.OFF
    max_snapshot_bytes: int = _ABSOLUTE_MAX_SNAPSHOT_BYTES
    max_reservation_records: int = _ABSOLUTE_MAX_RESERVATION_RECORDS
    max_json_depth: int = _ABSOLUTE_MAX_JSON_DEPTH
    max_json_nodes: int = _ABSOLUTE_MAX_JSON_NODES
    accounting_policy_digest: str = (
        ATTEMPT_RESERVATION_SUCCESSOR_ACCOUNTING_POLICY_DIGEST
    )
    schema_version: str = ATTEMPT_RESERVATION_SUCCESSOR_POLICY_SCHEMA

    def __post_init__(self) -> None:
        if type(self) is not AttemptReservationSuccessorPolicyV1:
            raise AttemptReservationSuccessorContractError(
                "policy exact type required"
            )
        if type(self.schema_version) is not str or (
            self.schema_version != ATTEMPT_RESERVATION_SUCCESSOR_POLICY_SCHEMA
        ):
            raise AttemptReservationSuccessorContractError(
                "policy schema_version refused"
            )
        if type(self.mode) is not AttemptReservationSuccessorMode:
            raise AttemptReservationSuccessorContractError(
                "mode must be an exact AttemptReservationSuccessorMode"
            )
        _require_bounded_int(
            self.max_snapshot_bytes,
            "max_snapshot_bytes",
            minimum=128,
            maximum=_ABSOLUTE_MAX_SNAPSHOT_BYTES,
        )
        _require_bounded_int(
            self.max_reservation_records,
            "max_reservation_records",
            minimum=0,
            maximum=_ABSOLUTE_MAX_RESERVATION_RECORDS,
        )
        _require_bounded_int(
            self.max_json_depth,
            "max_json_depth",
            minimum=1,
            maximum=_ABSOLUTE_MAX_JSON_DEPTH,
        )
        _require_bounded_int(
            self.max_json_nodes,
            "max_json_nodes",
            minimum=1,
            maximum=_ABSOLUTE_MAX_JSON_NODES,
        )
        _require_sha256(
            self.accounting_policy_digest,
            "accounting_policy_digest",
        )
        if (
            self.accounting_policy_digest
            != ATTEMPT_RESERVATION_SUCCESSOR_ACCOUNTING_POLICY_DIGEST
        ):
            raise AttemptReservationSuccessorContractError(
                "accounting policy digest refused"
            )

    def to_mapping(self) -> dict[str, Any]:
        refused = False
        result: dict[str, Any] = {}
        try:
            AttemptReservationSuccessorPolicyV1.__post_init__(self)
            result = {
                "schema_version": self.schema_version,
                "mode": self.mode.value,
                "max_snapshot_bytes": self.max_snapshot_bytes,
                "max_reservation_records": self.max_reservation_records,
                "max_json_depth": self.max_json_depth,
                "max_json_nodes": self.max_json_nodes,
                "accounting_policy_digest": self.accounting_policy_digest,
            }
        except (AttributeError, TypeError, ValueError):
            refused = True
        if refused:
            raise AttemptReservationSuccessorContractError(
                "policy mapping refused"
            )
        return result

    @property
    def policy_digest(self) -> str:
        return sha256_digest(
            {
                "domain": (
                    "wd.understanding.attempt_reservation_successor_"
                    "policy.digest.v1"
                ),
                **AttemptReservationSuccessorPolicyV1.to_mapping(self),
            }
        )


@dataclass(frozen=True, repr=False, slots=True)
class AttemptReservationSuccessorRequestV1:
    reservation_state_snapshot_utf8: bytes
    schema_version: str = ATTEMPT_RESERVATION_SUCCESSOR_REQUEST_SCHEMA

    def __post_init__(self) -> None:
        if type(self) is not AttemptReservationSuccessorRequestV1:
            raise AttemptReservationSuccessorContractError(
                "request exact type required"
            )
        if type(self.schema_version) is not str or (
            self.schema_version != ATTEMPT_RESERVATION_SUCCESSOR_REQUEST_SCHEMA
        ):
            raise AttemptReservationSuccessorContractError(
                "request schema_version refused"
            )
        if type(self.reservation_state_snapshot_utf8) is not bytes:
            raise AttemptReservationSuccessorContractError(
                "reservation_state_snapshot_utf8 must be exact bytes"
            )


@dataclass(frozen=True, slots=True)
class _SuccessorFacts:
    snapshot_digest: str
    record_count: int
    byte_count: int
    record_digests: tuple[str, ...]
    target_state: AttemptReservationState
    state_evidence_digest: str


_TRUE_FACT_FIELDS = (
    "evaluation_only",
    "shadow_only",
    "static_accounting_only",
    "pure_successor_relation_only",
    "raw_material_omitted",
    "source_c8e_receipt_revalidated",
    "any_successor_uses_same_supplied_snapshot_bytes_as_precondition",
    "positive_successor_canonicalization_revalidation_required",
    "successor_bytes_not_returned",
    "toctou_window_remains_open",
    "parallel_identical_successors_possible",
    "source_c8e_precondition_evaluator_invoked",
    "c8a_not_invoked",
    "c8b_not_invoked",
    "c8c_not_invoked",
    "c8d_not_invoked",
    "c7_not_invoked",
    "no_side_effects_in_module",
)

_FALSE_CLAIM_FIELDS = (
    "expected_snapshot_externally_pinned",
    "expected_snapshot_digest_origin_authenticated",
    "reservation_scope_externally_pinned",
    "reservation_scope_origin_authenticated",
    "source_snapshot_origin_authenticated",
    "source_snapshot_current",
    "source_snapshot_authoritative",
    "source_snapshot_complete",
    "source_snapshot_fresh",
    "successor_snapshot_origin_authenticated",
    "successor_snapshot_current",
    "successor_snapshot_authoritative",
    "successor_snapshot_complete",
    "successor_snapshot_fresh",
    "receipt_origin_authenticated",
    "authoritative_store_head_consulted",
    "durable_reservation_store_consulted",
    "durable_reservation_journal_consulted",
    "reservation_history_verified",
    "reservation_history_chronology_verified",
    "reservation_history_append_only_verified",
    "reservation_history_rollback_protected",
    "anti_replay_enforced",
    "aba_prevented",
    "atomic_compare_and_swap_performed",
    "atomic_compare_and_swap_applied",
    "durable_compare_and_swap_applied",
    "atomicity_verified",
    "linearizability_verified",
    "candidate_transition_applied",
    "candidate_transition_persisted",
    "successor_snapshot_written",
    "successor_snapshot_persisted",
    "reservation_state_written",
    "reservation_written",
    "reservation_persisted",
    "lock_acquired",
    "lease_granted",
    "lease_or_expiry_enforced",
    "fence_token_verified",
    "toctou_window_closed",
    "concurrent_safety_guaranteed",
    "retry_prevented",
    "cross_campaign_single_attempt_enforced",
    "cross_cell_single_attempt_enforced",
    "cross_shard_single_attempt_enforced",
    "global_single_attempt_enforced",
    "owner_handoff_authorized",
    "cell_recovery_handoff_applied",
    "dead_cell_rebind_authorized",
    "execution_resume_authorized",
    "recovery_or_resume_authorized",
    "intent_meaning_verified",
    "generation_need_verified",
    "semantic_equivalence_verified",
    "semantic_deduplication_verified",
    "global_deduplication_verified",
    "family_novelty_independently_verified",
    "new_family_need_independently_verified",
    "reuse_eligibility_claimed",
    "build_eligibility_claimed",
    "generation_authorized",
    "execution_authorized",
    "provider_invoked",
    "builder_host_invoked",
    "candidate_code_executed",
    "candidate_tests_executed",
    "subprocess_spawned",
    "network_accessed",
    "os_sandbox_applied",
    "filesystem_write_applied",
    "hive_commit_applied",
    "magma_read_applied",
    "magma_write_applied",
    "registry_read_applied",
    "registry_write_requested",
    "routing_influence_requested",
    "solver_promotion_requested",
    "runtime_authority_requested",
    "product_external_system_writes_requested",
    "genesis_origin_independently_verified",
    "hex_cell_binding_independently_verified",
    "echo_chamber_absence_verified",
    "scalability_50000_demonstrated",
)


@dataclass(frozen=True, slots=True)
class AttemptReservationSuccessorReceiptV1:
    policy_digest: str
    accounting_policy_digest: str
    request_digest: str
    source_c8e_receipt: AttemptReservationCasReceiptV1
    source_c8e_receipt_digest: str
    base_reservation_state_snapshot_digest: str
    reservation_scope_digest: str
    max_snapshot_bytes: int
    max_reservation_records: int
    max_json_depth: int
    max_json_nodes: int
    base_reservation_record_count: int
    base_reservation_state_snapshot_byte_count: int
    source_precondition_relation_holds: bool | None
    successor_derivation_performed: bool
    successor_snapshot_relation_holds: bool | None
    target_reservation_state: AttemptReservationState | None
    derived_state_evidence_digest: str | None
    successor_reservation_state_snapshot_digest: str | None
    successor_reservation_record_count: int | None
    successor_reservation_state_snapshot_byte_count: int | None
    successor_reservation_record_digests: tuple[str, ...] | None
    disposition: AttemptReservationSuccessorDisposition
    reason_code: AttemptReservationSuccessorReasonCode
    receipt_digest: str
    schema_version: str = ATTEMPT_RESERVATION_SUCCESSOR_RECEIPT_SCHEMA
    evaluation_only: bool = True
    shadow_only: bool = True
    static_accounting_only: bool = True
    pure_successor_relation_only: bool = True
    raw_material_omitted: bool = True
    source_c8e_receipt_revalidated: bool = True
    any_successor_uses_same_supplied_snapshot_bytes_as_precondition: bool = True
    positive_successor_canonicalization_revalidation_required: bool = True
    successor_bytes_not_returned: bool = True
    toctou_window_remains_open: bool = True
    parallel_identical_successors_possible: bool = True
    source_c8e_precondition_evaluator_invoked: bool = True
    c8a_not_invoked: bool = True
    c8b_not_invoked: bool = True
    c8c_not_invoked: bool = True
    c8d_not_invoked: bool = True
    c7_not_invoked: bool = True
    no_side_effects_in_module: bool = True
    expected_snapshot_externally_pinned: bool = False
    expected_snapshot_digest_origin_authenticated: bool = False
    reservation_scope_externally_pinned: bool = False
    reservation_scope_origin_authenticated: bool = False
    source_snapshot_origin_authenticated: bool = False
    source_snapshot_current: bool = False
    source_snapshot_authoritative: bool = False
    source_snapshot_complete: bool = False
    source_snapshot_fresh: bool = False
    successor_snapshot_origin_authenticated: bool = False
    successor_snapshot_current: bool = False
    successor_snapshot_authoritative: bool = False
    successor_snapshot_complete: bool = False
    successor_snapshot_fresh: bool = False
    receipt_origin_authenticated: bool = False
    authoritative_store_head_consulted: bool = False
    durable_reservation_store_consulted: bool = False
    durable_reservation_journal_consulted: bool = False
    reservation_history_verified: bool = False
    reservation_history_chronology_verified: bool = False
    reservation_history_append_only_verified: bool = False
    reservation_history_rollback_protected: bool = False
    anti_replay_enforced: bool = False
    aba_prevented: bool = False
    atomic_compare_and_swap_performed: bool = False
    atomic_compare_and_swap_applied: bool = False
    durable_compare_and_swap_applied: bool = False
    atomicity_verified: bool = False
    linearizability_verified: bool = False
    candidate_transition_applied: bool = False
    candidate_transition_persisted: bool = False
    successor_snapshot_written: bool = False
    successor_snapshot_persisted: bool = False
    reservation_state_written: bool = False
    reservation_written: bool = False
    reservation_persisted: bool = False
    lock_acquired: bool = False
    lease_granted: bool = False
    lease_or_expiry_enforced: bool = False
    fence_token_verified: bool = False
    toctou_window_closed: bool = False
    concurrent_safety_guaranteed: bool = False
    retry_prevented: bool = False
    cross_campaign_single_attempt_enforced: bool = False
    cross_cell_single_attempt_enforced: bool = False
    cross_shard_single_attempt_enforced: bool = False
    global_single_attempt_enforced: bool = False
    owner_handoff_authorized: bool = False
    cell_recovery_handoff_applied: bool = False
    dead_cell_rebind_authorized: bool = False
    execution_resume_authorized: bool = False
    recovery_or_resume_authorized: bool = False
    intent_meaning_verified: bool = False
    generation_need_verified: bool = False
    semantic_equivalence_verified: bool = False
    semantic_deduplication_verified: bool = False
    global_deduplication_verified: bool = False
    family_novelty_independently_verified: bool = False
    new_family_need_independently_verified: bool = False
    reuse_eligibility_claimed: bool = False
    build_eligibility_claimed: bool = False
    generation_authorized: bool = False
    execution_authorized: bool = False
    provider_invoked: bool = False
    builder_host_invoked: bool = False
    candidate_code_executed: bool = False
    candidate_tests_executed: bool = False
    subprocess_spawned: bool = False
    network_accessed: bool = False
    os_sandbox_applied: bool = False
    filesystem_write_applied: bool = False
    hive_commit_applied: bool = False
    magma_read_applied: bool = False
    magma_write_applied: bool = False
    registry_read_applied: bool = False
    registry_write_requested: bool = False
    routing_influence_requested: bool = False
    solver_promotion_requested: bool = False
    runtime_authority_requested: bool = False
    product_external_system_writes_requested: bool = False
    genesis_origin_independently_verified: bool = False
    hex_cell_binding_independently_verified: bool = False
    echo_chamber_absence_verified: bool = False
    scalability_50000_demonstrated: bool = False

    def __post_init__(self) -> None:
        _validate_receipt(self)

    def _core_mapping(self) -> dict[str, Any]:
        if type(self) is not AttemptReservationSuccessorReceiptV1:
            raise AttemptReservationSuccessorContractError(
                "receipt exact type required"
            )
        return _mapping_from_instance(self, include_receipt_digest=False)

    def to_mapping(self) -> dict[str, Any]:
        refused = False
        result: dict[str, Any] = {}
        try:
            _validate_receipt(self)
            result = self._core_mapping()
            result["receipt_digest"] = self.receipt_digest
        except (
            AttributeError,
            AttemptReservationCasContractError,
            AttemptReservationSuccessorContractError,
            TypeError,
            ValueError,
        ):
            refused = True
        if refused:
            raise AttemptReservationSuccessorContractError(
                "receipt mapping refused"
            )
        return result


def _serialize_value(value: Any) -> Any:
    if type(value) is AttemptReservationCasReceiptV1:
        return value.to_mapping()
    if isinstance(value, Enum):
        return value.value
    if type(value) is tuple:
        return list(value)
    return value


def _mapping_from_instance(
    receipt: AttemptReservationSuccessorReceiptV1,
    *,
    include_receipt_digest: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in fields(AttemptReservationSuccessorReceiptV1):
        if item.name == "receipt_digest" and not include_receipt_digest:
            continue
        result[item.name] = _serialize_value(getattr(receipt, item.name))
    return result


def _mapping_from_values(values: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in fields(AttemptReservationSuccessorReceiptV1):
        if item.name == "receipt_digest":
            continue
        if item.name in values:
            value = values[item.name]
        elif item.default is not MISSING:
            value = item.default
        else:
            raise AttemptReservationSuccessorContractError(
                f"missing receipt field {item.name}"
            )
        result[item.name] = _serialize_value(value)
    return result


def _derive_snapshot_digest(
    *,
    reservation_scope_digest: str,
    reservation_record_digests: tuple[str, ...],
) -> str:
    return sha256_digest(
        {
            "domain": (
                "wd.understanding.supplied_attempt_reservation_state_"
                "snapshot.digest.v1"
            ),
            "schema_version": (
                SUPPLIED_ATTEMPT_RESERVATION_STATE_SNAPSHOT_SCHEMA
            ),
            "reservation_scope_digest": reservation_scope_digest,
            "reservation_record_digests": list(
                reservation_record_digests
            ),
        }
    )


def _derive_state_evidence_digest(
    source_receipt: AttemptReservationCasReceiptV1,
    target_state: AttemptReservationState,
) -> str:
    return sha256_digest(
        {
            "domain": (
                "wd.understanding.attempt_reservation_successor_"
                "state_evidence.digest.v1"
            ),
            "base_reservation_state_snapshot_digest": (
                source_receipt.reservation_state_snapshot_digest
            ),
            "proposal_digest": source_receipt.proposal_digest,
            "reservation_id": source_receipt.proposal.reservation_id,
            "transition": source_receipt.proposal.transition.value,
            "target_reservation_state": target_state.value,
        }
    )


def _derive_request_digest(
    *,
    policy_digest: str,
    source_receipt: AttemptReservationCasReceiptV1,
) -> str:
    return sha256_digest(
        {
            "domain": (
                "wd.understanding.attempt_reservation_successor_"
                "request.digest.v1"
            ),
            "schema_version": ATTEMPT_RESERVATION_SUCCESSOR_REQUEST_SCHEMA,
            "policy_digest": policy_digest,
            "accounting_policy_digest": (
                ATTEMPT_RESERVATION_SUCCESSOR_ACCOUNTING_POLICY_DIGEST
            ),
            "source_c8e_receipt_digest": source_receipt.receipt_digest,
            "expectation_digest": source_receipt.expectation_digest,
            "proposal_digest": source_receipt.proposal_digest,
            "base_reservation_state_snapshot_digest": (
                source_receipt.reservation_state_snapshot_digest
            ),
            "reservation_scope_digest": (
                source_receipt.reservation_scope_digest
            ),
        }
    )


def _snapshot_policy(
    policy: AttemptReservationSuccessorPolicyV1,
) -> AttemptReservationSuccessorPolicyV1:
    refused = False
    result: AttemptReservationSuccessorPolicyV1 | None = None
    try:
        result = AttemptReservationSuccessorPolicyV1(
            mode=policy.mode,
            max_snapshot_bytes=policy.max_snapshot_bytes,
            max_reservation_records=policy.max_reservation_records,
            max_json_depth=policy.max_json_depth,
            max_json_nodes=policy.max_json_nodes,
            accounting_policy_digest=policy.accounting_policy_digest,
            schema_version=policy.schema_version,
        )
    except (AttributeError, TypeError, ValueError):
        refused = True
    if refused or result is None:
        raise AttemptReservationSuccessorContractError(
            "policy fields refused"
        )
    return result


def _snapshot_request(
    request: AttemptReservationSuccessorRequestV1,
) -> AttemptReservationSuccessorRequestV1:
    refused = False
    result: AttemptReservationSuccessorRequestV1 | None = None
    try:
        result = AttemptReservationSuccessorRequestV1(
            reservation_state_snapshot_utf8=(
                request.reservation_state_snapshot_utf8
            ),
            schema_version=request.schema_version,
        )
    except (AttributeError, TypeError, ValueError):
        refused = True
    if refused or result is None:
        raise AttemptReservationSuccessorContractError(
            "request fields refused"
        )
    return result


def _to_c8e_policy(
    policy: AttemptReservationSuccessorPolicyV1,
) -> AttemptReservationCasPolicyV1:
    return AttemptReservationCasPolicyV1(
        mode=AttemptReservationCasMode.STATIC_SHADOW,
        max_snapshot_bytes=policy.max_snapshot_bytes,
        max_reservation_records=policy.max_reservation_records,
        max_json_depth=policy.max_json_depth,
        max_json_nodes=policy.max_json_nodes,
    )


def _record_sort_key(record: dict[str, str]) -> tuple[str, ...]:
    return (
        record["declared_capability_fingerprint"],
        record["reservation_id"],
        record["campaign_id_digest"],
        record["cell_binding_digest"],
        record["intent_digest"],
        record["state"],
        record["state_evidence_digest"],
    )


def _target_state(
    transition: AttemptReservationTransition,
) -> AttemptReservationState:
    return {
        AttemptReservationTransition.OPEN_IF_ABSENT: (
            AttemptReservationState.RESERVED
        ),
        AttemptReservationTransition.COMMIT_IF_RESERVED: (
            AttemptReservationState.COMMITTED
        ),
        AttemptReservationTransition.ABORT_IF_RESERVED: (
            AttemptReservationState.ABORTED
        ),
    }[transition]


def _positive_disposition(
    transition: AttemptReservationTransition,
) -> tuple[
    AttemptReservationSuccessorDisposition,
    AttemptReservationSuccessorReasonCode,
]:
    return {
        AttemptReservationTransition.OPEN_IF_ABSENT: (
            AttemptReservationSuccessorDisposition.OPEN_SUCCESSOR_RELATION_HOLDS_IN_LOCALLY_DERIVED_SNAPSHOT,
            AttemptReservationSuccessorReasonCode.OPEN_RESERVED_SUCCESSOR_DERIVED,
        ),
        AttemptReservationTransition.COMMIT_IF_RESERVED: (
            AttemptReservationSuccessorDisposition.COMMIT_SUCCESSOR_RELATION_HOLDS_IN_LOCALLY_DERIVED_SNAPSHOT,
            AttemptReservationSuccessorReasonCode.COMMIT_COMMITTED_SUCCESSOR_DERIVED,
        ),
        AttemptReservationTransition.ABORT_IF_RESERVED: (
            AttemptReservationSuccessorDisposition.ABORT_SUCCESSOR_RELATION_HOLDS_IN_LOCALLY_DERIVED_SNAPSHOT,
            AttemptReservationSuccessorReasonCode.ABORT_ABORTED_SUCCESSOR_DERIVED,
        ),
    }[transition]


def _derive_successor_facts(
    *,
    base_snapshot_utf8: bytes,
    source_receipt: AttemptReservationCasReceiptV1,
    policy: AttemptReservationSuccessorPolicyV1,
    c8e_policy: AttemptReservationCasPolicyV1,
) -> tuple[_SuccessorFacts | None, AttemptReservationSuccessorReasonCode | None]:
    proposal = source_receipt.proposal
    if (
        proposal.transition is AttemptReservationTransition.OPEN_IF_ABSENT
        and source_receipt.reservation_record_count
        >= policy.max_reservation_records
    ):
        return (
            None,
            AttemptReservationSuccessorReasonCode.SUCCESSOR_RECORD_LIMIT_EXCEEDED,
        )

    parse_failed = False
    decoded: dict[str, Any] = {}
    try:
        candidate = json.loads(base_snapshot_utf8.decode("utf-8"))
        if type(candidate) is not dict:
            parse_failed = True
        else:
            decoded = candidate
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        parse_failed = True
    if parse_failed:
        raise AttemptReservationSuccessorContractError(
            "validated source snapshot could not be decoded"
        )

    rows = [dict(row) for row in decoded["reservations"]]
    target_state = _target_state(proposal.transition)
    state_evidence_digest = _derive_state_evidence_digest(
        source_receipt,
        target_state,
    )
    if proposal.transition is AttemptReservationTransition.OPEN_IF_ABSENT:
        rows.append(
            {
                "reservation_id": proposal.reservation_id,
                "declared_capability_fingerprint": (
                    proposal.declared_capability_fingerprint
                ),
                "state": target_state.value,
                "cell_binding_digest": proposal.cell_binding_digest,
                "campaign_id_digest": proposal.campaign_id_digest,
                "intent_digest": proposal.intent_digest,
                "state_evidence_digest": state_evidence_digest,
            }
        )
    else:
        matched = 0
        for row in rows:
            if row["reservation_id"] == proposal.reservation_id:
                matched += 1
                row["state"] = target_state.value
                row["state_evidence_digest"] = state_evidence_digest
        if matched != 1:
            raise AttemptReservationSuccessorContractError(
                "holding source precondition did not identify one row"
            )

    rows.sort(key=_record_sort_key)
    successor_mapping = {
        "schema_version": SUPPLIED_ATTEMPT_RESERVATION_STATE_SNAPSHOT_SCHEMA,
        "reservation_scope_digest": source_receipt.reservation_scope_digest,
        "reservations": rows,
    }
    try:
        successor_utf8 = canonical_json_bytes(successor_mapping)
    except (RecursionError, TypeError, ValueError):
        raise AttemptReservationSuccessorContractError(
            "successor canonicalization refused"
        ) from None
    if len(successor_utf8) > policy.max_snapshot_bytes:
        return (
            None,
            AttemptReservationSuccessorReasonCode.SUCCESSOR_BYTE_LIMIT_EXCEEDED,
        )

    successor_validation_refused = False
    validated_digest: str | None = None
    try:
        validated_digest = (
            derive_supplied_attempt_reservation_state_snapshot_digest(
                successor_utf8,
                c8e_policy,
            )
        )
    except AttemptReservationCasContractError:
        successor_validation_refused = True
    if successor_validation_refused or validated_digest is None:
        raise AttemptReservationSuccessorContractError(
            "derived successor failed C8e snapshot validation"
        )
    record_digests = tuple(
        sha256_digest(
            {
                "domain": (
                    "wd.understanding.supplied_attempt_reservation_"
                    "state_record.digest.v1"
                ),
                **row,
            }
        )
        for row in rows
    )
    if validated_digest != _derive_snapshot_digest(
        reservation_scope_digest=source_receipt.reservation_scope_digest,
        reservation_record_digests=record_digests,
    ):
        raise AttemptReservationSuccessorContractError(
            "successor digest relation mismatch"
        )
    return (
        _SuccessorFacts(
            snapshot_digest=validated_digest,
            record_count=len(rows),
            byte_count=len(successor_utf8),
            record_digests=record_digests,
            target_state=target_state,
            state_evidence_digest=state_evidence_digest,
        ),
        None,
    )


def _validate_receipt(receipt: AttemptReservationSuccessorReceiptV1) -> None:
    if type(receipt) is not AttemptReservationSuccessorReceiptV1:
        raise AttemptReservationSuccessorContractError(
            "receipt exact type required"
        )
    if type(receipt.schema_version) is not str or (
        receipt.schema_version != ATTEMPT_RESERVATION_SUCCESSOR_RECEIPT_SCHEMA
    ):
        raise AttemptReservationSuccessorContractError(
            "receipt schema refused"
        )
    for name in (
        "policy_digest",
        "accounting_policy_digest",
        "request_digest",
        "source_c8e_receipt_digest",
        "base_reservation_state_snapshot_digest",
        "reservation_scope_digest",
        "receipt_digest",
    ):
        _require_sha256(getattr(receipt, name), name)
    if (
        receipt.accounting_policy_digest
        != ATTEMPT_RESERVATION_SUCCESSOR_ACCOUNTING_POLICY_DIGEST
    ):
        raise AttemptReservationSuccessorContractError(
            "receipt accounting policy digest refused"
        )
    policy = AttemptReservationSuccessorPolicyV1(
        mode=AttemptReservationSuccessorMode.STATIC_SHADOW,
        max_snapshot_bytes=receipt.max_snapshot_bytes,
        max_reservation_records=receipt.max_reservation_records,
        max_json_depth=receipt.max_json_depth,
        max_json_nodes=receipt.max_json_nodes,
    )
    if receipt.policy_digest != policy.policy_digest:
        raise AttemptReservationSuccessorContractError(
            "receipt policy digest mismatch"
        )
    if type(receipt.source_c8e_receipt) is not AttemptReservationCasReceiptV1:
        raise AttemptReservationSuccessorContractError(
            "source C8e receipt exact type required"
        )
    source_receipt_refused = False
    try:
        receipt.source_c8e_receipt.to_mapping()
    except AttemptReservationCasContractError:
        source_receipt_refused = True
    if source_receipt_refused:
        raise AttemptReservationSuccessorContractError(
            "source C8e receipt refused"
        )
    source = receipt.source_c8e_receipt
    if receipt.source_c8e_receipt_digest != source.receipt_digest:
        raise AttemptReservationSuccessorContractError(
            "source C8e receipt digest mismatch"
        )
    if (
        source.max_snapshot_bytes != receipt.max_snapshot_bytes
        or source.max_reservation_records != receipt.max_reservation_records
        or source.max_json_depth != receipt.max_json_depth
        or source.max_json_nodes != receipt.max_json_nodes
    ):
        raise AttemptReservationSuccessorContractError(
            "source C8e receipt bounds mismatch"
        )
    if (
        receipt.base_reservation_state_snapshot_digest
        != source.reservation_state_snapshot_digest
        or receipt.reservation_scope_digest
        != source.reservation_scope_digest
        or receipt.base_reservation_record_count
        != source.reservation_record_count
        or receipt.base_reservation_state_snapshot_byte_count
        != source.reservation_state_snapshot_byte_count
        or receipt.source_precondition_relation_holds
        is not source.supplied_precondition_relation_holds
    ):
        raise AttemptReservationSuccessorContractError(
            "source C8e receipt relation mismatch"
        )
    if receipt.request_digest != _derive_request_digest(
        policy_digest=receipt.policy_digest,
        source_receipt=source,
    ):
        raise AttemptReservationSuccessorContractError(
            "request digest mismatch"
        )
    for name in (
        "base_reservation_record_count",
        "base_reservation_state_snapshot_byte_count",
    ):
        value = getattr(receipt, name)
        if type(value) is not int or value < 0:
            raise AttemptReservationSuccessorContractError(
                f"{name} refused"
            )
    for name in _TRUE_FACT_FIELDS:
        if getattr(receipt, name) is not True:
            raise AttemptReservationSuccessorContractError(
                f"{name} must remain literal true"
            )
    for name in _FALSE_CLAIM_FIELDS:
        if getattr(receipt, name) is not False:
            raise AttemptReservationSuccessorContractError(
                f"{name} must remain literal false"
            )
    if type(receipt.disposition) is not AttemptReservationSuccessorDisposition:
        raise AttemptReservationSuccessorContractError(
            "successor disposition exact type required"
        )
    if type(receipt.reason_code) is not AttemptReservationSuccessorReasonCode:
        raise AttemptReservationSuccessorContractError(
            "successor reason exact type required"
        )
    for name in (
        "source_precondition_relation_holds",
        "successor_snapshot_relation_holds",
    ):
        value = getattr(receipt, name)
        if value is not None and type(value) is not bool:
            raise AttemptReservationSuccessorContractError(
                f"{name} refused"
            )
    if type(receipt.successor_derivation_performed) is not bool:
        raise AttemptReservationSuccessorContractError(
            "successor_derivation_performed refused"
        )

    successor_fields = (
        receipt.target_reservation_state,
        receipt.derived_state_evidence_digest,
        receipt.successor_reservation_state_snapshot_digest,
        receipt.successor_reservation_record_count,
        receipt.successor_reservation_state_snapshot_byte_count,
        receipt.successor_reservation_record_digests,
    )
    source_relation = source.supplied_precondition_relation_holds
    if receipt.disposition is AttemptReservationSuccessorDisposition.REFUSED:
        if (
            source.disposition is not AttemptReservationCasDisposition.REFUSED
            or source_relation is not None
            or receipt.successor_derivation_performed
            or receipt.successor_snapshot_relation_holds is not None
            or any(value is not None for value in successor_fields)
            or receipt.reason_code
            is not AttemptReservationSuccessorReasonCode.SOURCE_PRECONDITION_REFUSED
        ):
            raise AttemptReservationSuccessorContractError(
                "refused successor outcome mismatch"
            )
    elif (
        receipt.disposition
        is AttemptReservationSuccessorDisposition.PRECONDITION_RELATION_DOES_NOT_HOLD_NO_SUCCESSOR
    ):
        if (
            source_relation is not False
            or receipt.successor_derivation_performed
            or receipt.successor_snapshot_relation_holds is not False
            or any(value is not None for value in successor_fields)
            or receipt.reason_code
            is not AttemptReservationSuccessorReasonCode.SOURCE_PRECONDITION_DOES_NOT_HOLD
        ):
            raise AttemptReservationSuccessorContractError(
                "non-holding successor outcome mismatch"
            )
    elif (
        receipt.disposition
        is AttemptReservationSuccessorDisposition.SUCCESSOR_RESOURCE_BOUNDS_REFUSED
    ):
        if (
            source_relation is not True
            or not receipt.successor_derivation_performed
            or receipt.successor_snapshot_relation_holds is not False
            or any(value is not None for value in successor_fields)
            or receipt.reason_code
            not in {
                AttemptReservationSuccessorReasonCode.SUCCESSOR_RECORD_LIMIT_EXCEEDED,
                AttemptReservationSuccessorReasonCode.SUCCESSOR_BYTE_LIMIT_EXCEEDED,
            }
        ):
            raise AttemptReservationSuccessorContractError(
                "bounded successor outcome mismatch"
            )
        if (
            receipt.reason_code
            is AttemptReservationSuccessorReasonCode.SUCCESSOR_RECORD_LIMIT_EXCEEDED
            and (
                source.proposal.transition
                is not AttemptReservationTransition.OPEN_IF_ABSENT
                or source.reservation_record_count
                < receipt.max_reservation_records
            )
        ):
            raise AttemptReservationSuccessorContractError(
                "successor record-limit refusal relation mismatch"
            )
    else:
        transition = source.proposal.transition
        target_state = _target_state(transition)
        disposition, reason = _positive_disposition(transition)
        if (
            source_relation is not True
            or not receipt.successor_derivation_performed
            or receipt.successor_snapshot_relation_holds is not True
            or receipt.disposition is not disposition
            or receipt.reason_code is not reason
            or receipt.target_reservation_state is not target_state
        ):
            raise AttemptReservationSuccessorContractError(
                "positive successor outcome mismatch"
            )
        _require_sha256(
            receipt.derived_state_evidence_digest,
            "derived_state_evidence_digest",
        )
        if receipt.derived_state_evidence_digest != _derive_state_evidence_digest(
            source,
            target_state,
        ):
            raise AttemptReservationSuccessorContractError(
                "derived state evidence digest mismatch"
            )
        _require_sha256(
            receipt.successor_reservation_state_snapshot_digest,
            "successor_reservation_state_snapshot_digest",
        )
        if type(receipt.successor_reservation_record_digests) is not tuple:
            raise AttemptReservationSuccessorContractError(
                "successor reservation record digests refused"
            )
        if (
            len(receipt.successor_reservation_record_digests)
            > receipt.max_reservation_records
        ):
            raise AttemptReservationSuccessorContractError(
                "successor reservation record limit exceeded"
            )
        for index, digest in enumerate(
            receipt.successor_reservation_record_digests
        ):
            _require_sha256(digest, f"successor record digest {index}")
        if len(set(receipt.successor_reservation_record_digests)) != len(
            receipt.successor_reservation_record_digests
        ):
            raise AttemptReservationSuccessorContractError(
                "duplicate successor record digest refused"
            )
        if (
            type(receipt.successor_reservation_record_count) is not int
            or receipt.successor_reservation_record_count
            != len(receipt.successor_reservation_record_digests)
        ):
            raise AttemptReservationSuccessorContractError(
                "successor reservation record count mismatch"
            )
        expected_count = source.reservation_record_count + (
            1
            if transition is AttemptReservationTransition.OPEN_IF_ABSENT
            else 0
        )
        if receipt.successor_reservation_record_count != expected_count:
            raise AttemptReservationSuccessorContractError(
                "successor transition record count mismatch"
            )
        if (
            type(receipt.successor_reservation_state_snapshot_byte_count)
            is not int
            or receipt.successor_reservation_state_snapshot_byte_count <= 0
            or receipt.successor_reservation_state_snapshot_byte_count
            > receipt.max_snapshot_bytes
        ):
            raise AttemptReservationSuccessorContractError(
                "successor snapshot byte count refused"
            )
        if (
            receipt.successor_reservation_state_snapshot_digest
            != _derive_snapshot_digest(
                reservation_scope_digest=receipt.reservation_scope_digest,
                reservation_record_digests=(
                    receipt.successor_reservation_record_digests
                ),
            )
        ):
            raise AttemptReservationSuccessorContractError(
                "successor snapshot digest mismatch"
            )

    expected_receipt_digest = sha256_digest(
        {
            "domain": (
                "wd.understanding.attempt_reservation_successor_"
                "receipt.digest.v1"
            ),
            **receipt._core_mapping(),
        }
    )
    if receipt.receipt_digest != expected_receipt_digest:
        raise AttemptReservationSuccessorContractError(
            "receipt digest mismatch"
        )


def _make_receipt(values: dict[str, Any]) -> AttemptReservationSuccessorReceiptV1:
    core = _mapping_from_values(values)
    receipt_digest = sha256_digest(
        {
            "domain": (
                "wd.understanding.attempt_reservation_successor_"
                "receipt.digest.v1"
            ),
            **core,
        }
    )
    return AttemptReservationSuccessorReceiptV1(
        **values,
        receipt_digest=receipt_digest,
    )


def evaluate_attempt_reservation_successor_relation(
    request: AttemptReservationSuccessorRequestV1 | None = None,
    *,
    expected_snapshot_digest: (
        AttemptReservationExpectedSnapshotDigestV1 | None
    ) = None,
    proposal: AttemptReservationTransitionProposalV1 | None = None,
    policy: AttemptReservationSuccessorPolicyV1 | None = None,
) -> AttemptReservationSuccessorReceiptV1 | None:
    """Account for one canonical local successor without applying it."""

    if policy is None:
        policy = AttemptReservationSuccessorPolicyV1()
    elif type(policy) is not AttemptReservationSuccessorPolicyV1:
        raise AttemptReservationSuccessorContractError(
            "policy must be an exact AttemptReservationSuccessorPolicyV1"
        )
    policy_fields_refused = False
    selected_mode: AttemptReservationSuccessorMode | None = None
    try:
        selected_mode = policy.mode
    except AttributeError:
        policy_fields_refused = True
    if policy_fields_refused:
        raise AttemptReservationSuccessorContractError(
            "policy fields refused"
        )
    if selected_mode is AttemptReservationSuccessorMode.OFF:
        return None
    policy = _snapshot_policy(policy)
    if policy.mode is not AttemptReservationSuccessorMode.STATIC_SHADOW:
        raise AttemptReservationSuccessorContractError(
            "unsupported C8f mode"
        )
    if type(request) is not AttemptReservationSuccessorRequestV1:
        raise AttemptReservationSuccessorContractError(
            "STATIC_SHADOW requires an exact AttemptReservationSuccessorRequestV1"
        )
    request = _snapshot_request(request)
    c8e_policy = _to_c8e_policy(policy)
    source_precondition_refused = False
    source_receipt: AttemptReservationCasReceiptV1 | None = None
    try:
        source_receipt = evaluate_attempt_reservation_cas_relation(
            AttemptReservationCasRequestV1(
                request.reservation_state_snapshot_utf8
            ),
            expected_snapshot_digest=expected_snapshot_digest,
            proposal=proposal,
            policy=c8e_policy,
        )
    except AttemptReservationCasContractError:
        source_precondition_refused = True
    if source_precondition_refused:
        raise AttemptReservationSuccessorContractError(
            "source C8e precondition contract refused"
        )
    if type(source_receipt) is not AttemptReservationCasReceiptV1:
        raise AttemptReservationSuccessorContractError(
            "source C8e precondition receipt missing"
        )
    generated_source_receipt_refused = False
    try:
        source_receipt.to_mapping()
    except AttemptReservationCasContractError:
        generated_source_receipt_refused = True
    if generated_source_receipt_refused:
        raise AttemptReservationSuccessorContractError(
            "generated source C8e receipt refused"
        )
    source_relation = source_receipt.supplied_precondition_relation_holds

    successor_facts: _SuccessorFacts | None = None
    if source_receipt.disposition is AttemptReservationCasDisposition.REFUSED:
        successor_derivation_performed = False
        successor_relation_holds: bool | None = None
        disposition = AttemptReservationSuccessorDisposition.REFUSED
        reason = (
            AttemptReservationSuccessorReasonCode.SOURCE_PRECONDITION_REFUSED
        )
    elif source_relation is not True:
        successor_derivation_performed = False
        successor_relation_holds = False
        disposition = (
            AttemptReservationSuccessorDisposition.PRECONDITION_RELATION_DOES_NOT_HOLD_NO_SUCCESSOR
        )
        reason = (
            AttemptReservationSuccessorReasonCode.SOURCE_PRECONDITION_DOES_NOT_HOLD
        )
    else:
        successor_derivation_performed = True
        successor_facts, resource_reason = _derive_successor_facts(
            base_snapshot_utf8=request.reservation_state_snapshot_utf8,
            source_receipt=source_receipt,
            policy=policy,
            c8e_policy=c8e_policy,
        )
        if successor_facts is None:
            successor_relation_holds = False
            disposition = (
                AttemptReservationSuccessorDisposition.SUCCESSOR_RESOURCE_BOUNDS_REFUSED
            )
            if resource_reason is None:
                raise AttemptReservationSuccessorContractError(
                    "missing successor resource refusal reason"
                )
            reason = resource_reason
        else:
            successor_relation_holds = True
            disposition, reason = _positive_disposition(
                source_receipt.proposal.transition
            )

    request_digest = _derive_request_digest(
        policy_digest=policy.policy_digest,
        source_receipt=source_receipt,
    )
    return _make_receipt(
        {
            "policy_digest": policy.policy_digest,
            "accounting_policy_digest": policy.accounting_policy_digest,
            "request_digest": request_digest,
            "source_c8e_receipt": source_receipt,
            "source_c8e_receipt_digest": source_receipt.receipt_digest,
            "base_reservation_state_snapshot_digest": (
                source_receipt.reservation_state_snapshot_digest
            ),
            "reservation_scope_digest": source_receipt.reservation_scope_digest,
            "max_snapshot_bytes": policy.max_snapshot_bytes,
            "max_reservation_records": policy.max_reservation_records,
            "max_json_depth": policy.max_json_depth,
            "max_json_nodes": policy.max_json_nodes,
            "base_reservation_record_count": (
                source_receipt.reservation_record_count
            ),
            "base_reservation_state_snapshot_byte_count": (
                source_receipt.reservation_state_snapshot_byte_count
            ),
            "source_precondition_relation_holds": source_relation,
            "successor_derivation_performed": (
                successor_derivation_performed
            ),
            "successor_snapshot_relation_holds": successor_relation_holds,
            "target_reservation_state": (
                None if successor_facts is None else successor_facts.target_state
            ),
            "derived_state_evidence_digest": (
                None
                if successor_facts is None
                else successor_facts.state_evidence_digest
            ),
            "successor_reservation_state_snapshot_digest": (
                None
                if successor_facts is None
                else successor_facts.snapshot_digest
            ),
            "successor_reservation_record_count": (
                None
                if successor_facts is None
                else successor_facts.record_count
            ),
            "successor_reservation_state_snapshot_byte_count": (
                None
                if successor_facts is None
                else successor_facts.byte_count
            ),
            "successor_reservation_record_digests": (
                None
                if successor_facts is None
                else successor_facts.record_digests
            ),
            "disposition": disposition,
            "reason_code": reason,
        }
    )
