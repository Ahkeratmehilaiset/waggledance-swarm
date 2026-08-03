# SPDX-License-Identifier: Apache-2.0
"""Pure CAS-precondition relations over one supplied reservation snapshot.

C8e evaluates an open, commit, or abort precondition against one bounded
canonical reservation-state-shaped snapshot supplied by the caller. A
separate keyword-only expected-digest object must match before reservation
identities, bindings, or states are inspected. The snapshot is not verified
current, authoritative, complete, or durable.

A positive disposition is only a local relation over supplied values. This
module performs no compare-and-swap, reservation, transition, write, lock,
lease, handoff, build, execution, or authorization. Two callers can evaluate
the same bytes concurrently and both observe a local open precondition. Any
real writer must compare a trusted durable head and install a successor
atomically under transactional and fenced control.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, fields
from enum import Enum
from typing import Any

from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest


SUPPLIED_ATTEMPT_RESERVATION_STATE_SNAPSHOT_SCHEMA = (
    "wd.understanding.supplied_attempt_reservation_state_snapshot.v1"
)
ATTEMPT_RESERVATION_EXPECTED_SNAPSHOT_DIGEST_SCHEMA = (
    "wd.understanding.attempt_reservation_expected_snapshot_digest.v1"
)
ATTEMPT_RESERVATION_CAS_POLICY_SCHEMA = (
    "wd.understanding.attempt_reservation_cas_policy.v1"
)
ATTEMPT_RESERVATION_CAS_REQUEST_SCHEMA = (
    "wd.understanding.attempt_reservation_cas_request.v1"
)
ATTEMPT_RESERVATION_TRANSITION_PROPOSAL_SCHEMA = (
    "wd.understanding.attempt_reservation_transition_proposal.v1"
)
ATTEMPT_RESERVATION_CAS_RECEIPT_SCHEMA = (
    "wd.understanding.attempt_reservation_cas_receipt.v1"
)

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")

_ABSOLUTE_MAX_SNAPSHOT_BYTES = 2_097_152
_ABSOLUTE_MAX_RESERVATION_RECORDS = 2_048
_ABSOLUTE_MAX_JSON_DEPTH = 6
_ABSOLUTE_MAX_JSON_NODES = 32_768

_CANONICAL_DIGEST_TEMPLATE = "sha256:" + ("0" * 64)
_MIN_EMPTY_SNAPSHOT_BYTES = len(
    canonical_json_bytes(
        {
            "schema_version": (
                SUPPLIED_ATTEMPT_RESERVATION_STATE_SNAPSHOT_SCHEMA
            ),
            "reservation_scope_digest": _CANONICAL_DIGEST_TEMPLATE,
            "reservations": [],
        }
    )
)


class AttemptReservationCasContractError(ValueError):
    """A value is outside the C8e supplied-relation contract."""


class AttemptReservationCasMode(str, Enum):
    OFF = "off"
    STATIC_SHADOW = "static_shadow"


class AttemptReservationState(str, Enum):
    RESERVED = "reserved"
    COMMITTED = "committed"
    ABORTED = "aborted"


class AttemptReservationTransition(str, Enum):
    OPEN_IF_ABSENT = "open_if_absent"
    COMMIT_IF_RESERVED = "commit_if_reserved"
    ABORT_IF_RESERVED = "abort_if_reserved"


class AttemptReservationCasDisposition(str, Enum):
    REFUSED = "refused"
    OPEN_IF_ABSENT_PRECONDITION_RELATION_HOLDS_IN_SUPPLIED_RESERVATION_STATE_SNAPSHOT = (
        "open_if_absent_precondition_relation_holds_in_supplied_"
        "reservation_state_snapshot"
    )
    OPEN_IF_ABSENT_PRECONDITION_RELATION_DOES_NOT_HOLD_IN_SUPPLIED_RESERVATION_STATE_SNAPSHOT = (
        "open_if_absent_precondition_relation_does_not_hold_in_supplied_"
        "reservation_state_snapshot"
    )
    COMMIT_IF_RESERVED_PRECONDITION_RELATION_HOLDS_IN_SUPPLIED_RESERVATION_STATE_SNAPSHOT = (
        "commit_if_reserved_precondition_relation_holds_in_supplied_"
        "reservation_state_snapshot"
    )
    COMMIT_IF_RESERVED_PRECONDITION_RELATION_DOES_NOT_HOLD_IN_SUPPLIED_RESERVATION_STATE_SNAPSHOT = (
        "commit_if_reserved_precondition_relation_does_not_hold_in_"
        "supplied_reservation_state_snapshot"
    )
    ABORT_IF_RESERVED_PRECONDITION_RELATION_HOLDS_IN_SUPPLIED_RESERVATION_STATE_SNAPSHOT = (
        "abort_if_reserved_precondition_relation_holds_in_supplied_"
        "reservation_state_snapshot"
    )
    ABORT_IF_RESERVED_PRECONDITION_RELATION_DOES_NOT_HOLD_IN_SUPPLIED_RESERVATION_STATE_SNAPSHOT = (
        "abort_if_reserved_precondition_relation_does_not_hold_in_"
        "supplied_reservation_state_snapshot"
    )


class AttemptReservationCasReasonCode(str, Enum):
    EXPECTED_RESERVATION_STATE_SNAPSHOT_DIGEST_MISMATCH = (
        "expected_reservation_state_snapshot_digest_mismatch"
    )
    CAPABILITY_AND_RESERVATION_ID_ABSENT_IN_SUPPLIED_SNAPSHOT = (
        "capability_and_reservation_id_absent_in_supplied_snapshot"
    )
    CAPABILITY_ALREADY_RESERVED_IN_SUPPLIED_SNAPSHOT = (
        "capability_already_reserved_in_supplied_snapshot"
    )
    CAPABILITY_ALREADY_COMMITTED_IN_SUPPLIED_SNAPSHOT = (
        "capability_already_committed_in_supplied_snapshot"
    )
    CAPABILITY_ALREADY_ABORTED_IN_SUPPLIED_SNAPSHOT = (
        "capability_already_aborted_in_supplied_snapshot"
    )
    RESERVATION_ID_ABSENT_IN_SUPPLIED_SNAPSHOT = (
        "reservation_id_absent_in_supplied_snapshot"
    )
    RESERVATION_BINDING_DIGEST_MISMATCH = (
        "reservation_binding_digest_mismatch"
    )
    EXACT_RESERVED_RELATION_HOLDS_FOR_COMMIT = (
        "exact_reserved_relation_holds_for_commit"
    )
    EXACT_RESERVED_RELATION_HOLDS_FOR_ABORT = (
        "exact_reserved_relation_holds_for_abort"
    )
    RESERVATION_ALREADY_COMMITTED_IN_SUPPLIED_SNAPSHOT = (
        "reservation_already_committed_in_supplied_snapshot"
    )
    RESERVATION_ALREADY_ABORTED_IN_SUPPLIED_SNAPSHOT = (
        "reservation_already_aborted_in_supplied_snapshot"
    )
    RESERVATION_ID_BOUND_TO_DIFFERENT_DECLARED_CAPABILITY = (
        "reservation_id_bound_to_different_declared_capability"
    )


def _require_sha256(value: object, label: str) -> str:
    if type(value) is not str or not _SHA256.fullmatch(value):
        raise AttemptReservationCasContractError(
            f"{label} must be a canonical sha256 digest"
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
        raise AttemptReservationCasContractError(
            f"{label} must be an integer in [{minimum}, {maximum}]"
        )
    return value


def derive_attempt_reservation_id(
    *,
    reservation_scope_digest: str,
    declared_capability_fingerprint: str,
) -> str:
    """Derive the stable caller-scope plus capability convergence key."""

    scope_digest = _require_sha256(
        reservation_scope_digest,
        "reservation_scope_digest",
    )
    capability_fingerprint = _require_sha256(
        declared_capability_fingerprint,
        "declared_capability_fingerprint",
    )
    return sha256_digest(
        {
            "domain": "wd.understanding.attempt_reservation_id.digest.v1",
            "reservation_scope_digest": scope_digest,
            "declared_capability_fingerprint": capability_fingerprint,
        }
    )


_MIN_RESERVATION_RECORD_BYTES = min(
    len(
        canonical_json_bytes(
            {
                "reservation_id": _CANONICAL_DIGEST_TEMPLATE,
                "declared_capability_fingerprint": (
                    _CANONICAL_DIGEST_TEMPLATE
                ),
                "state": state.value,
                "cell_binding_digest": _CANONICAL_DIGEST_TEMPLATE,
                "campaign_id_digest": _CANONICAL_DIGEST_TEMPLATE,
                "intent_digest": _CANONICAL_DIGEST_TEMPLATE,
                "state_evidence_digest": _CANONICAL_DIGEST_TEMPLATE,
            }
        )
    )
    for state in AttemptReservationState
)
_MAX_RESERVATION_RECORD_BYTES = max(
    len(
        canonical_json_bytes(
            {
                "reservation_id": _CANONICAL_DIGEST_TEMPLATE,
                "declared_capability_fingerprint": (
                    _CANONICAL_DIGEST_TEMPLATE
                ),
                "state": state.value,
                "cell_binding_digest": _CANONICAL_DIGEST_TEMPLATE,
                "campaign_id_digest": _CANONICAL_DIGEST_TEMPLATE,
                "intent_digest": _CANONICAL_DIGEST_TEMPLATE,
                "state_evidence_digest": _CANONICAL_DIGEST_TEMPLATE,
            }
        )
    )
    for state in AttemptReservationState
)


def _canonical_snapshot_byte_bounds(
    reservation_record_count: int,
) -> tuple[int, int]:
    separator_bytes = max(0, reservation_record_count - 1)
    return (
        _MIN_EMPTY_SNAPSHOT_BYTES
        + (_MIN_RESERVATION_RECORD_BYTES * reservation_record_count)
        + separator_bytes,
        _MIN_EMPTY_SNAPSHOT_BYTES
        + (_MAX_RESERVATION_RECORD_BYTES * reservation_record_count)
        + separator_bytes,
    )


def _derive_snapshot_digest_from_record_digests(
    *,
    reservation_scope_digest: str,
    reservation_record_digests: list[str] | tuple[str, ...],
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


_ACCOUNTING_POLICY = {
    "schema": "wd.understanding.attempt_reservation_cas_accounting_policy.v1",
    "snapshot_schema": SUPPLIED_ATTEMPT_RESERVATION_STATE_SNAPSHOT_SCHEMA,
    "snapshot_source": "caller_supplied_only",
    "expected_digest_source": "caller_supplied_keyword_only",
    "proposal_source": "caller_supplied_keyword_only",
    "reservation_identity_inputs": (
        "reservation_scope_digest",
        "declared_capability_fingerprint",
    ),
    "reservation_record_sort_key": (
        "declared_capability_fingerprint",
        "reservation_id",
        "campaign_id_digest",
        "cell_binding_digest",
        "intent_digest",
        "state",
        "state_evidence_digest",
    ),
    "duplicate_declared_capability_rule": "refused",
    "duplicate_reservation_id_rule": "refused",
    "duplicate_state_evidence_digest_rule": "allowed_non_key_metadata",
    "expected_mismatch_rule": "refused_without_semantic_checks",
    "terminal_reopen_rule": "precondition_does_not_hold",
    "campaign_id_is_reservation_identity": False,
    "cell_binding_digest_is_reservation_identity": False,
    "intent_digest_is_reservation_identity": False,
    "state_evidence_digest_is_match_key": False,
    "transition_evidence_digest_is_match_key": False,
    "snapshot_current": False,
    "atomic_cas_performed": False,
    "transition_applied": False,
    "authority_granted": False,
}
ATTEMPT_RESERVATION_CAS_ACCOUNTING_POLICY_DIGEST = sha256_digest(
    _ACCOUNTING_POLICY
)


@dataclass(frozen=True, slots=True)
class AttemptReservationCasPolicyV1:
    mode: AttemptReservationCasMode = AttemptReservationCasMode.OFF
    max_snapshot_bytes: int = _ABSOLUTE_MAX_SNAPSHOT_BYTES
    max_reservation_records: int = _ABSOLUTE_MAX_RESERVATION_RECORDS
    max_json_depth: int = _ABSOLUTE_MAX_JSON_DEPTH
    max_json_nodes: int = _ABSOLUTE_MAX_JSON_NODES
    accounting_policy_digest: str = (
        ATTEMPT_RESERVATION_CAS_ACCOUNTING_POLICY_DIGEST
    )
    schema_version: str = ATTEMPT_RESERVATION_CAS_POLICY_SCHEMA

    def __post_init__(self) -> None:
        if type(self) is not AttemptReservationCasPolicyV1:
            raise AttemptReservationCasContractError(
                "policy exact type required"
            )
        if type(self.schema_version) is not str or (
            self.schema_version != ATTEMPT_RESERVATION_CAS_POLICY_SCHEMA
        ):
            raise AttemptReservationCasContractError(
                "policy schema_version refused"
            )
        if type(self.mode) is not AttemptReservationCasMode:
            raise AttemptReservationCasContractError(
                "mode must be an exact AttemptReservationCasMode"
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
            != ATTEMPT_RESERVATION_CAS_ACCOUNTING_POLICY_DIGEST
        ):
            raise AttemptReservationCasContractError(
                "accounting policy digest refused"
            )

    def to_mapping(self) -> dict[str, Any]:
        mapping_refused = False
        result: dict[str, Any] = {}
        try:
            AttemptReservationCasPolicyV1.__post_init__(self)
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
            mapping_refused = True
        if mapping_refused:
            raise AttemptReservationCasContractError(
                "policy mapping refused"
            )
        return result

    @property
    def policy_digest(self) -> str:
        return sha256_digest(
            {
                "domain": (
                    "wd.understanding.attempt_reservation_cas_policy."
                    "digest.v1"
                ),
                **AttemptReservationCasPolicyV1.to_mapping(self),
            }
        )


@dataclass(frozen=True, slots=True)
class AttemptReservationExpectedSnapshotDigestV1:
    expected_reservation_state_snapshot_digest: str
    target_snapshot_schema_version: str = (
        SUPPLIED_ATTEMPT_RESERVATION_STATE_SNAPSHOT_SCHEMA
    )
    schema_version: str = (
        ATTEMPT_RESERVATION_EXPECTED_SNAPSHOT_DIGEST_SCHEMA
    )

    def __post_init__(self) -> None:
        if type(self) is not AttemptReservationExpectedSnapshotDigestV1:
            raise AttemptReservationCasContractError(
                "expected snapshot digest exact type required"
            )
        if type(self.schema_version) is not str or (
            self.schema_version
            != ATTEMPT_RESERVATION_EXPECTED_SNAPSHOT_DIGEST_SCHEMA
        ):
            raise AttemptReservationCasContractError(
                "expected snapshot digest schema_version refused"
            )
        if type(self.target_snapshot_schema_version) is not str or (
            self.target_snapshot_schema_version
            != SUPPLIED_ATTEMPT_RESERVATION_STATE_SNAPSHOT_SCHEMA
        ):
            raise AttemptReservationCasContractError(
                "expected snapshot digest target schema refused"
            )
        _require_sha256(
            self.expected_reservation_state_snapshot_digest,
            "expected_reservation_state_snapshot_digest",
        )

    def to_mapping(self) -> dict[str, Any]:
        mapping_refused = False
        result: dict[str, Any] = {}
        try:
            AttemptReservationExpectedSnapshotDigestV1.__post_init__(self)
            result = {
                "schema_version": self.schema_version,
                "target_snapshot_schema_version": (
                    self.target_snapshot_schema_version
                ),
                "expected_reservation_state_snapshot_digest": (
                    self.expected_reservation_state_snapshot_digest
                ),
            }
        except (AttributeError, TypeError, ValueError):
            mapping_refused = True
        if mapping_refused:
            raise AttemptReservationCasContractError(
                "expected snapshot digest mapping refused"
            )
        return result

    @property
    def expectation_digest(self) -> str:
        return sha256_digest(
            {
                "domain": (
                    "wd.understanding.attempt_reservation_expected_"
                    "snapshot_digest.digest.v1"
                ),
                **AttemptReservationExpectedSnapshotDigestV1.to_mapping(
                    self
                ),
            }
        )


@dataclass(frozen=True, repr=False, slots=True)
class AttemptReservationCasRequestV1:
    reservation_state_snapshot_utf8: bytes
    schema_version: str = ATTEMPT_RESERVATION_CAS_REQUEST_SCHEMA

    def __post_init__(self) -> None:
        if type(self) is not AttemptReservationCasRequestV1:
            raise AttemptReservationCasContractError(
                "request exact type required"
            )
        if type(self.schema_version) is not str or (
            self.schema_version != ATTEMPT_RESERVATION_CAS_REQUEST_SCHEMA
        ):
            raise AttemptReservationCasContractError(
                "request schema_version refused"
            )
        if type(self.reservation_state_snapshot_utf8) is not bytes:
            raise AttemptReservationCasContractError(
                "reservation_state_snapshot_utf8 must be exact bytes"
            )


@dataclass(frozen=True, slots=True)
class AttemptReservationTransitionProposalV1:
    transition: AttemptReservationTransition
    reservation_id: str
    declared_capability_fingerprint: str
    cell_binding_digest: str
    campaign_id_digest: str
    intent_digest: str
    transition_evidence_digest: str
    schema_version: str = ATTEMPT_RESERVATION_TRANSITION_PROPOSAL_SCHEMA

    def __post_init__(self) -> None:
        if type(self) is not AttemptReservationTransitionProposalV1:
            raise AttemptReservationCasContractError(
                "proposal exact type required"
            )
        if type(self.schema_version) is not str or (
            self.schema_version
            != ATTEMPT_RESERVATION_TRANSITION_PROPOSAL_SCHEMA
        ):
            raise AttemptReservationCasContractError(
                "proposal schema_version refused"
            )
        if type(self.transition) is not AttemptReservationTransition:
            raise AttemptReservationCasContractError(
                "transition must be an exact AttemptReservationTransition"
            )
        for name in (
            "reservation_id",
            "declared_capability_fingerprint",
            "cell_binding_digest",
            "campaign_id_digest",
            "intent_digest",
            "transition_evidence_digest",
        ):
            _require_sha256(getattr(self, name), name)

    def to_mapping(self) -> dict[str, Any]:
        mapping_refused = False
        result: dict[str, Any] = {}
        try:
            AttemptReservationTransitionProposalV1.__post_init__(self)
            result = {
                "schema_version": self.schema_version,
                "transition": self.transition.value,
                "reservation_id": self.reservation_id,
                "declared_capability_fingerprint": (
                    self.declared_capability_fingerprint
                ),
                "cell_binding_digest": self.cell_binding_digest,
                "campaign_id_digest": self.campaign_id_digest,
                "intent_digest": self.intent_digest,
                "transition_evidence_digest": (
                    self.transition_evidence_digest
                ),
            }
        except (AttributeError, TypeError, ValueError):
            mapping_refused = True
        if mapping_refused:
            raise AttemptReservationCasContractError(
                "proposal mapping refused"
            )
        return result

    @property
    def proposal_digest(self) -> str:
        return sha256_digest(
            {
                "domain": (
                    "wd.understanding.attempt_reservation_transition_"
                    "proposal.digest.v1"
                ),
                **AttemptReservationTransitionProposalV1.to_mapping(self),
            }
        )


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AttemptReservationCasContractError(
                "duplicate JSON key refused"
            )
        result[key] = value
    return result


def _refuse_json_constant(value: str) -> None:
    raise AttemptReservationCasContractError(
        f"non-finite JSON constant refused: {value}"
    )


def _count_json_nodes(
    value: Any,
    *,
    depth: int,
    policy: AttemptReservationCasPolicyV1,
) -> int:
    if depth > policy.max_json_depth:
        raise AttemptReservationCasContractError(
            "reservation snapshot JSON depth refused"
        )
    count = 1
    if type(value) is dict:
        for child in value.values():
            count += _count_json_nodes(
                child,
                depth=depth + 1,
                policy=policy,
            )
    elif type(value) is list:
        for child in value:
            count += _count_json_nodes(
                child,
                depth=depth + 1,
                policy=policy,
            )
    if count > policy.max_json_nodes:
        raise AttemptReservationCasContractError(
            "reservation snapshot JSON node count refused"
        )
    return count


@dataclass(frozen=True, slots=True)
class _ReservationRecordFacts:
    reservation_id: str
    declared_capability_fingerprint: str
    state: AttemptReservationState
    cell_binding_digest: str
    campaign_id_digest: str
    intent_digest: str
    state_evidence_digest: str
    record_digest: str


@dataclass(frozen=True, slots=True)
class _ReservationSnapshotFacts:
    reservation_scope_digest: str
    snapshot_digest: str
    byte_count: int
    records: tuple[_ReservationRecordFacts, ...]


def _validate_record_identity_relations(
    snapshot: _ReservationSnapshotFacts,
) -> None:
    for index, record in enumerate(snapshot.records):
        expected_id = derive_attempt_reservation_id(
            reservation_scope_digest=snapshot.reservation_scope_digest,
            declared_capability_fingerprint=(
                record.declared_capability_fingerprint
            ),
        )
        if record.reservation_id != expected_id:
            raise AttemptReservationCasContractError(
                f"reservation record {index} reservation_id derivation "
                "mismatch"
            )


def _decode_reservation_state_snapshot(
    value: bytes,
    policy: AttemptReservationCasPolicyV1,
    *,
    validate_record_identity_relations: bool,
) -> _ReservationSnapshotFacts:
    if type(value) is not bytes:
        raise AttemptReservationCasContractError(
            "reservation snapshot must be exact bytes"
        )
    if len(value) == 0 or len(value) > policy.max_snapshot_bytes:
        raise AttemptReservationCasContractError(
            "reservation snapshot byte count refused"
        )
    parse_refused = False
    try:
        text = value.decode("utf-8", errors="strict")
        decoded = json.loads(
            text,
            object_pairs_hook=_strict_json_object,
            parse_constant=_refuse_json_constant,
        )
    except (
        AttemptReservationCasContractError,
        RecursionError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
    ):
        parse_refused = True
    if parse_refused:
        raise AttemptReservationCasContractError(
            "reservation snapshot canonical JSON refused"
        )
    if type(decoded) is not dict:
        raise AttemptReservationCasContractError(
            "reservation snapshot root must be an object"
        )
    _count_json_nodes(decoded, depth=1, policy=policy)
    canonicalization_refused = False
    try:
        recoded = canonical_json_bytes(decoded)
    except (RecursionError, TypeError, ValueError):
        canonicalization_refused = True
    if canonicalization_refused:
        raise AttemptReservationCasContractError(
            "reservation snapshot canonicalization refused"
        )
    if recoded != value:
        raise AttemptReservationCasContractError(
            "reservation snapshot bytes are not canonical"
        )
    if set(decoded) != {
        "schema_version",
        "reservation_scope_digest",
        "reservations",
    }:
        raise AttemptReservationCasContractError(
            "reservation snapshot shape refused"
        )
    if type(decoded["schema_version"]) is not str or (
        decoded["schema_version"]
        != SUPPLIED_ATTEMPT_RESERVATION_STATE_SNAPSHOT_SCHEMA
    ):
        raise AttemptReservationCasContractError(
            "reservation snapshot schema refused"
        )
    scope_digest = _require_sha256(
        decoded["reservation_scope_digest"],
        "reservation_scope_digest",
    )
    reservations = decoded["reservations"]
    if type(reservations) is not list:
        raise AttemptReservationCasContractError(
            "reservations must be an exact JSON array"
        )
    if len(reservations) > policy.max_reservation_records:
        raise AttemptReservationCasContractError(
            "reservation record count refused"
        )

    record_mappings: list[dict[str, str]] = []
    valid_states = {state.value for state in AttemptReservationState}
    for index, record in enumerate(reservations):
        if type(record) is not dict or set(record) != {
            "reservation_id",
            "declared_capability_fingerprint",
            "state",
            "cell_binding_digest",
            "campaign_id_digest",
            "intent_digest",
            "state_evidence_digest",
        }:
            raise AttemptReservationCasContractError(
                f"reservation record {index} shape refused"
            )
        state = record["state"]
        if type(state) is not str or state not in valid_states:
            raise AttemptReservationCasContractError(
                f"reservation record {index} state refused"
            )
        record_mappings.append(
            {
                "reservation_id": _require_sha256(
                    record["reservation_id"],
                    f"reservation record {index} reservation_id",
                ),
                "declared_capability_fingerprint": _require_sha256(
                    record["declared_capability_fingerprint"],
                    f"reservation record {index} capability fingerprint",
                ),
                "state": state,
                "cell_binding_digest": _require_sha256(
                    record["cell_binding_digest"],
                    f"reservation record {index} cell binding digest",
                ),
                "campaign_id_digest": _require_sha256(
                    record["campaign_id_digest"],
                    f"reservation record {index} campaign digest",
                ),
                "intent_digest": _require_sha256(
                    record["intent_digest"],
                    f"reservation record {index} intent digest",
                ),
                "state_evidence_digest": _require_sha256(
                    record["state_evidence_digest"],
                    f"reservation record {index} state evidence digest",
                ),
            }
        )

    def sort_key(item: dict[str, str]) -> tuple[str, ...]:
        return (
            item["declared_capability_fingerprint"],
            item["reservation_id"],
            item["campaign_id_digest"],
            item["cell_binding_digest"],
            item["intent_digest"],
            item["state"],
            item["state_evidence_digest"],
        )

    if record_mappings != sorted(record_mappings, key=sort_key):
        raise AttemptReservationCasContractError(
            "reservation records must be canonically sorted"
        )
    capability_fingerprints = [
        record["declared_capability_fingerprint"]
        for record in record_mappings
    ]
    reservation_ids = [
        record["reservation_id"] for record in record_mappings
    ]
    if len(set(capability_fingerprints)) != len(capability_fingerprints):
        raise AttemptReservationCasContractError(
            "duplicate declared_capability_fingerprint refused"
        )
    if len(set(reservation_ids)) != len(reservation_ids):
        raise AttemptReservationCasContractError(
            "duplicate reservation_id refused"
        )

    records = tuple(
        _ReservationRecordFacts(
            reservation_id=record["reservation_id"],
            declared_capability_fingerprint=(
                record["declared_capability_fingerprint"]
            ),
            state=AttemptReservationState(record["state"]),
            cell_binding_digest=record["cell_binding_digest"],
            campaign_id_digest=record["campaign_id_digest"],
            intent_digest=record["intent_digest"],
            state_evidence_digest=record["state_evidence_digest"],
            record_digest=sha256_digest(
                {
                    "domain": (
                        "wd.understanding.supplied_attempt_reservation_"
                        "state_record.digest.v1"
                    ),
                    **record,
                }
            ),
        )
        for record in record_mappings
    )
    record_digests = [record.record_digest for record in records]
    if len(set(record_digests)) != len(record_digests):
        raise AttemptReservationCasContractError(
            "duplicate reservation record digest refused"
        )
    snapshot = _ReservationSnapshotFacts(
        reservation_scope_digest=scope_digest,
        snapshot_digest=_derive_snapshot_digest_from_record_digests(
            reservation_scope_digest=scope_digest,
            reservation_record_digests=record_digests,
        ),
        byte_count=len(value),
        records=records,
    )
    if validate_record_identity_relations:
        _validate_record_identity_relations(snapshot)
    return snapshot


def derive_supplied_attempt_reservation_state_snapshot_digest(
    reservation_state_snapshot_utf8: bytes,
    policy: AttemptReservationCasPolicyV1,
) -> str:
    """Derive a digest after validating structure and every row identity."""

    if type(policy) is not AttemptReservationCasPolicyV1:
        raise AttemptReservationCasContractError(
            "policy must be an exact AttemptReservationCasPolicyV1"
        )
    policy = _snapshot_policy(policy)
    return _decode_reservation_state_snapshot(
        reservation_state_snapshot_utf8,
        policy,
        validate_record_identity_relations=True,
    ).snapshot_digest


def _derive_request_digest(
    *,
    policy_digest: str,
    accounting_policy_digest: str,
    expectation_digest: str,
    proposal_digest: str,
    reservation_state_snapshot_digest: str,
    reservation_scope_digest: str,
) -> str:
    return sha256_digest(
        {
            "domain": (
                "wd.understanding.attempt_reservation_cas_request.digest.v1"
            ),
            "schema_version": ATTEMPT_RESERVATION_CAS_REQUEST_SCHEMA,
            "policy_digest": policy_digest,
            "accounting_policy_digest": accounting_policy_digest,
            "expectation_digest": expectation_digest,
            "proposal_digest": proposal_digest,
            "reservation_state_snapshot_digest": (
                reservation_state_snapshot_digest
            ),
            "reservation_scope_digest": reservation_scope_digest,
        }
    )


_TRUE_RECEIPT_FIELDS = (
    "evaluation_only",
    "shadow_only",
    "static_accounting_only",
    "raw_material_omitted",
    "supplied_reservation_state_snapshot_only",
    "supplied_state_not_verified_current",
    "canonical_snapshot_digest_recomputed_from_supplied_bytes",
    "separate_expected_snapshot_digest_required",
    "expected_snapshot_digest_keyword_only",
    "separate_transition_proposal_required",
    "transition_proposal_keyword_only",
    "caller_supplied_expected_snapshot_digest_only",
    "exact_expected_snapshot_digest_comparison_only",
    "cas_precondition_relation_only",
    "stable_reservation_identity_scope_and_capability_only",
    "campaign_id_not_reservation_identity",
    "cell_binding_digest_not_reservation_identity",
    "intent_digest_not_reservation_identity",
    "state_evidence_digest_not_reservation_identity",
    "transition_evidence_digest_not_reservation_identity",
    "evidence_digest_duplicates_allowed",
    "expected_mismatch_refused_without_semantic_checks",
    "terminal_states_block_reopen_in_supplied_relation",
    "no_successor_snapshot_returned",
    "toctou_window_remains_open",
    "parallel_local_holds_possible",
    "c8a_not_invoked",
    "c8b_not_invoked",
    "c8c_not_invoked",
    "c8d_not_invoked",
    "c7_not_invoked",
    "no_side_effects_in_module",
)

_FALSE_RECEIPT_FIELDS = (
    "expected_snapshot_externally_pinned",
    "expected_snapshot_digest_independently_configured",
    "expected_snapshot_digest_origin_authenticated",
    "expected_snapshot_digest_precommit_verified",
    "reservation_scope_externally_pinned",
    "reservation_scope_origin_authenticated",
    "reservation_state_snapshot_origin_authenticated",
    "reservation_snapshot_origin_authenticated",
    "reservation_record_origin_authenticated",
    "proposal_origin_authenticated",
    "state_evidence_origin_authenticated",
    "transition_evidence_origin_authenticated",
    "state_evidence_semantics_verified",
    "transition_evidence_semantics_verified",
    "receipt_origin_authenticated",
    "reservation_snapshot_current",
    "reservation_snapshot_authoritative",
    "reservation_snapshot_complete",
    "reservation_snapshot_fresh",
    "authoritative_store_head_consulted",
    "durable_reservation_store_consulted",
    "durable_reservation_journal_consulted",
    "reservation_history_verified",
    "reservation_history_chronology_verified",
    "reservation_history_append_only_verified",
    "reservation_history_monotonic",
    "reservation_history_rollback_protected",
    "reservation_history_fork_resolved",
    "anti_replay_enforced",
    "aba_prevented",
    "atomic_compare_and_swap_performed",
    "atomic_compare_and_swap_applied",
    "durable_compare_and_swap_applied",
    "atomicity_verified",
    "linearizability_verified",
    "candidate_transition_applied",
    "candidate_transition_persisted",
    "durable_reservation_created",
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
    "intent_origin_authenticated",
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
    "c8a_invoked",
    "c8b_invoked",
    "c8c_invoked",
    "c8d_invoked",
    "c7_execution_requested",
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


def _copy_expectation(
    value: object,
) -> AttemptReservationExpectedSnapshotDigestV1:
    if type(value) is not AttemptReservationExpectedSnapshotDigestV1:
        raise AttemptReservationCasContractError(
            "expected_snapshot_digest must be an exact "
            "AttemptReservationExpectedSnapshotDigestV1"
        )
    try:
        AttemptReservationExpectedSnapshotDigestV1.__post_init__(value)
        return AttemptReservationExpectedSnapshotDigestV1(
            **{
                item.name: getattr(value, item.name)
                for item in fields(
                    AttemptReservationExpectedSnapshotDigestV1
                )
            }
        )
    except (AttributeError, TypeError, ValueError):
        raise AttemptReservationCasContractError(
            "expected snapshot digest object refused"
        ) from None


def _copy_proposal(
    value: object,
) -> AttemptReservationTransitionProposalV1:
    if type(value) is not AttemptReservationTransitionProposalV1:
        raise AttemptReservationCasContractError(
            "proposal must be an exact "
            "AttemptReservationTransitionProposalV1"
        )
    try:
        AttemptReservationTransitionProposalV1.__post_init__(value)
        return AttemptReservationTransitionProposalV1(
            **{
                item.name: getattr(value, item.name)
                for item in fields(AttemptReservationTransitionProposalV1)
            }
        )
    except (AttributeError, TypeError, ValueError):
        raise AttemptReservationCasContractError(
            "proposal object refused"
        ) from None


def _require_optional_digest(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _require_sha256(value, label)


def _validate_receipt_outcome(
    receipt: "AttemptReservationCasReceiptV1",
) -> None:
    transition = receipt.proposal.transition
    hold_dispositions = {
        AttemptReservationTransition.OPEN_IF_ABSENT: (
            AttemptReservationCasDisposition.OPEN_IF_ABSENT_PRECONDITION_RELATION_HOLDS_IN_SUPPLIED_RESERVATION_STATE_SNAPSHOT
        ),
        AttemptReservationTransition.COMMIT_IF_RESERVED: (
            AttemptReservationCasDisposition.COMMIT_IF_RESERVED_PRECONDITION_RELATION_HOLDS_IN_SUPPLIED_RESERVATION_STATE_SNAPSHOT
        ),
        AttemptReservationTransition.ABORT_IF_RESERVED: (
            AttemptReservationCasDisposition.ABORT_IF_RESERVED_PRECONDITION_RELATION_HOLDS_IN_SUPPLIED_RESERVATION_STATE_SNAPSHOT
        ),
    }
    no_hold_dispositions = {
        AttemptReservationTransition.OPEN_IF_ABSENT: (
            AttemptReservationCasDisposition.OPEN_IF_ABSENT_PRECONDITION_RELATION_DOES_NOT_HOLD_IN_SUPPLIED_RESERVATION_STATE_SNAPSHOT
        ),
        AttemptReservationTransition.COMMIT_IF_RESERVED: (
            AttemptReservationCasDisposition.COMMIT_IF_RESERVED_PRECONDITION_RELATION_DOES_NOT_HOLD_IN_SUPPLIED_RESERVATION_STATE_SNAPSHOT
        ),
        AttemptReservationTransition.ABORT_IF_RESERVED: (
            AttemptReservationCasDisposition.ABORT_IF_RESERVED_PRECONDITION_RELATION_DOES_NOT_HOLD_IN_SUPPLIED_RESERVATION_STATE_SNAPSHOT
        ),
    }

    def require_phase(
        *,
        binding: bool,
        state: bool,
        relation: bool | None,
        matched: bool,
        observed: AttemptReservationState | None,
        disposition: AttemptReservationCasDisposition,
        reason: AttemptReservationCasReasonCode,
    ) -> None:
        if receipt.binding_check_performed is not binding:
            raise AttemptReservationCasContractError(
                "receipt binding phase relation mismatch"
            )
        if receipt.state_check_performed is not state:
            raise AttemptReservationCasContractError(
                "receipt state phase relation mismatch"
            )
        if receipt.supplied_precondition_relation_holds is not relation:
            raise AttemptReservationCasContractError(
                "receipt supplied relation result mismatch"
            )
        if matched:
            _require_sha256(
                receipt.matched_reservation_record_digest,
                "matched_reservation_record_digest",
            )
        elif receipt.matched_reservation_record_digest is not None:
            raise AttemptReservationCasContractError(
                "receipt must omit unmatched reservation record"
            )
        if receipt.observed_reservation_state is not observed:
            raise AttemptReservationCasContractError(
                "receipt observed state relation mismatch"
            )
        if receipt.disposition is not disposition:
            raise AttemptReservationCasContractError(
                "receipt disposition relation mismatch"
            )
        if receipt.reason_code is not reason:
            raise AttemptReservationCasContractError(
                "receipt reason relation mismatch"
            )

    if receipt.reason_code is (
        AttemptReservationCasReasonCode.RESERVATION_ID_BOUND_TO_DIFFERENT_DECLARED_CAPABILITY
    ):
        require_phase(
            binding=False,
            state=False,
            relation=None,
            matched=True,
            observed=None,
            disposition=AttemptReservationCasDisposition.REFUSED,
            reason=receipt.reason_code,
        )
        return

    if transition is AttemptReservationTransition.OPEN_IF_ABSENT:
        if receipt.reason_code is (
            AttemptReservationCasReasonCode.CAPABILITY_AND_RESERVATION_ID_ABSENT_IN_SUPPLIED_SNAPSHOT
        ):
            require_phase(
                binding=False,
                state=False,
                relation=True,
                matched=False,
                observed=None,
                disposition=hold_dispositions[transition],
                reason=receipt.reason_code,
            )
            return
        state_reasons = {
            AttemptReservationState.RESERVED: (
                AttemptReservationCasReasonCode.CAPABILITY_ALREADY_RESERVED_IN_SUPPLIED_SNAPSHOT
            ),
            AttemptReservationState.COMMITTED: (
                AttemptReservationCasReasonCode.CAPABILITY_ALREADY_COMMITTED_IN_SUPPLIED_SNAPSHOT
            ),
            AttemptReservationState.ABORTED: (
                AttemptReservationCasReasonCode.CAPABILITY_ALREADY_ABORTED_IN_SUPPLIED_SNAPSHOT
            ),
        }
        observed = receipt.observed_reservation_state
        if observed in state_reasons and (
            receipt.reason_code is state_reasons[observed]
        ):
            require_phase(
                binding=False,
                state=True,
                relation=False,
                matched=True,
                observed=observed,
                disposition=no_hold_dispositions[transition],
                reason=receipt.reason_code,
            )
            return
        raise AttemptReservationCasContractError(
            "open receipt outcome relation refused"
        )

    if receipt.reason_code is (
        AttemptReservationCasReasonCode.RESERVATION_ID_ABSENT_IN_SUPPLIED_SNAPSHOT
    ):
        require_phase(
            binding=False,
            state=False,
            relation=False,
            matched=False,
            observed=None,
            disposition=no_hold_dispositions[transition],
            reason=receipt.reason_code,
        )
        return
    if receipt.reason_code is (
        AttemptReservationCasReasonCode.RESERVATION_BINDING_DIGEST_MISMATCH
    ):
        require_phase(
            binding=True,
            state=False,
            relation=None,
            matched=True,
            observed=None,
            disposition=AttemptReservationCasDisposition.REFUSED,
            reason=receipt.reason_code,
        )
        return
    if receipt.observed_reservation_state is AttemptReservationState.RESERVED:
        expected_reason = (
            AttemptReservationCasReasonCode.EXACT_RESERVED_RELATION_HOLDS_FOR_COMMIT
            if transition is AttemptReservationTransition.COMMIT_IF_RESERVED
            else AttemptReservationCasReasonCode.EXACT_RESERVED_RELATION_HOLDS_FOR_ABORT
        )
        if receipt.reason_code is expected_reason:
            require_phase(
                binding=True,
                state=True,
                relation=True,
                matched=True,
                observed=AttemptReservationState.RESERVED,
                disposition=hold_dispositions[transition],
                reason=expected_reason,
            )
            return
    terminal_reasons = {
        AttemptReservationState.COMMITTED: (
            AttemptReservationCasReasonCode.RESERVATION_ALREADY_COMMITTED_IN_SUPPLIED_SNAPSHOT
        ),
        AttemptReservationState.ABORTED: (
            AttemptReservationCasReasonCode.RESERVATION_ALREADY_ABORTED_IN_SUPPLIED_SNAPSHOT
        ),
    }
    observed = receipt.observed_reservation_state
    if observed in terminal_reasons and (
        receipt.reason_code is terminal_reasons[observed]
    ):
        require_phase(
            binding=True,
            state=True,
            relation=False,
            matched=True,
            observed=observed,
            disposition=no_hold_dispositions[transition],
            reason=receipt.reason_code,
        )
        return
    raise AttemptReservationCasContractError(
        "commit or abort receipt outcome relation refused"
    )


@dataclass(frozen=True, slots=True)
class AttemptReservationCasReceiptV1:
    policy_digest: str
    accounting_policy_digest: str
    expected_snapshot_digest: AttemptReservationExpectedSnapshotDigestV1
    expectation_digest: str
    request_digest: str
    proposal: AttemptReservationTransitionProposalV1
    proposal_digest: str
    reservation_state_snapshot_digest: str
    reservation_scope_digest: str
    max_snapshot_bytes: int
    max_reservation_records: int
    max_json_depth: int
    max_json_nodes: int
    reservation_record_count: int
    reservation_state_snapshot_byte_count: int
    expected_snapshot_digest_matches: bool
    row_identity_derivation_performed: bool
    proposal_identity_derivation_performed: bool
    subject_lookup_performed: bool
    binding_check_performed: bool
    state_check_performed: bool
    supplied_precondition_relation_holds: bool | None
    matched_reservation_record_digest: str | None
    observed_reservation_state: AttemptReservationState | None
    disposition: AttemptReservationCasDisposition
    reason_code: AttemptReservationCasReasonCode
    receipt_digest: str
    schema_version: str = ATTEMPT_RESERVATION_CAS_RECEIPT_SCHEMA
    evaluation_only: bool = True
    shadow_only: bool = True
    static_accounting_only: bool = True
    raw_material_omitted: bool = True
    supplied_reservation_state_snapshot_only: bool = True
    supplied_state_not_verified_current: bool = True
    canonical_snapshot_digest_recomputed_from_supplied_bytes: bool = True
    separate_expected_snapshot_digest_required: bool = True
    expected_snapshot_digest_keyword_only: bool = True
    separate_transition_proposal_required: bool = True
    transition_proposal_keyword_only: bool = True
    caller_supplied_expected_snapshot_digest_only: bool = True
    exact_expected_snapshot_digest_comparison_only: bool = True
    cas_precondition_relation_only: bool = True
    stable_reservation_identity_scope_and_capability_only: bool = True
    campaign_id_not_reservation_identity: bool = True
    cell_binding_digest_not_reservation_identity: bool = True
    intent_digest_not_reservation_identity: bool = True
    state_evidence_digest_not_reservation_identity: bool = True
    transition_evidence_digest_not_reservation_identity: bool = True
    evidence_digest_duplicates_allowed: bool = True
    expected_mismatch_refused_without_semantic_checks: bool = True
    terminal_states_block_reopen_in_supplied_relation: bool = True
    no_successor_snapshot_returned: bool = True
    toctou_window_remains_open: bool = True
    parallel_local_holds_possible: bool = True
    c8a_not_invoked: bool = True
    c8b_not_invoked: bool = True
    c8c_not_invoked: bool = True
    c8d_not_invoked: bool = True
    c7_not_invoked: bool = True
    no_side_effects_in_module: bool = True
    expected_snapshot_externally_pinned: bool = False
    expected_snapshot_digest_independently_configured: bool = False
    expected_snapshot_digest_origin_authenticated: bool = False
    expected_snapshot_digest_precommit_verified: bool = False
    reservation_scope_externally_pinned: bool = False
    reservation_scope_origin_authenticated: bool = False
    reservation_state_snapshot_origin_authenticated: bool = False
    reservation_snapshot_origin_authenticated: bool = False
    reservation_record_origin_authenticated: bool = False
    proposal_origin_authenticated: bool = False
    state_evidence_origin_authenticated: bool = False
    transition_evidence_origin_authenticated: bool = False
    state_evidence_semantics_verified: bool = False
    transition_evidence_semantics_verified: bool = False
    receipt_origin_authenticated: bool = False
    reservation_snapshot_current: bool = False
    reservation_snapshot_authoritative: bool = False
    reservation_snapshot_complete: bool = False
    reservation_snapshot_fresh: bool = False
    authoritative_store_head_consulted: bool = False
    durable_reservation_store_consulted: bool = False
    durable_reservation_journal_consulted: bool = False
    reservation_history_verified: bool = False
    reservation_history_chronology_verified: bool = False
    reservation_history_append_only_verified: bool = False
    reservation_history_monotonic: bool = False
    reservation_history_rollback_protected: bool = False
    reservation_history_fork_resolved: bool = False
    anti_replay_enforced: bool = False
    aba_prevented: bool = False
    atomic_compare_and_swap_performed: bool = False
    atomic_compare_and_swap_applied: bool = False
    durable_compare_and_swap_applied: bool = False
    atomicity_verified: bool = False
    linearizability_verified: bool = False
    candidate_transition_applied: bool = False
    candidate_transition_persisted: bool = False
    durable_reservation_created: bool = False
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
    intent_origin_authenticated: bool = False
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
    c8a_invoked: bool = False
    c8b_invoked: bool = False
    c8c_invoked: bool = False
    c8d_invoked: bool = False
    c7_execution_requested: bool = False
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

    def _core_mapping(self) -> dict[str, Any]:
        if type(self) is not AttemptReservationCasReceiptV1:
            raise AttemptReservationCasContractError(
                "receipt exact type required"
            )
        result: dict[str, Any] = {}
        for item in fields(AttemptReservationCasReceiptV1):
            if item.name == "receipt_digest":
                continue
            value = getattr(self, item.name)
            if type(value) is AttemptReservationExpectedSnapshotDigestV1:
                value = (
                    AttemptReservationExpectedSnapshotDigestV1.to_mapping(
                        value
                    )
                )
            elif type(value) is AttemptReservationTransitionProposalV1:
                value = AttemptReservationTransitionProposalV1.to_mapping(
                    value
                )
            elif isinstance(value, Enum):
                value = value.value
            result[item.name] = value
        return result

    def __post_init__(self) -> None:
        if type(self) is not AttemptReservationCasReceiptV1:
            raise AttemptReservationCasContractError(
                "receipt exact type required"
            )
        if type(self.schema_version) is not str or (
            self.schema_version != ATTEMPT_RESERVATION_CAS_RECEIPT_SCHEMA
        ):
            raise AttemptReservationCasContractError(
                "receipt schema_version refused"
            )
        for name in (
            "policy_digest",
            "accounting_policy_digest",
            "expectation_digest",
            "request_digest",
            "proposal_digest",
            "reservation_state_snapshot_digest",
            "reservation_scope_digest",
            "receipt_digest",
        ):
            _require_sha256(getattr(self, name), name)
        if (
            self.accounting_policy_digest
            != ATTEMPT_RESERVATION_CAS_ACCOUNTING_POLICY_DIGEST
        ):
            raise AttemptReservationCasContractError(
                "receipt accounting policy refused"
            )
        expected_policy = AttemptReservationCasPolicyV1(
            mode=AttemptReservationCasMode.STATIC_SHADOW,
            max_snapshot_bytes=self.max_snapshot_bytes,
            max_reservation_records=self.max_reservation_records,
            max_json_depth=self.max_json_depth,
            max_json_nodes=self.max_json_nodes,
            accounting_policy_digest=self.accounting_policy_digest,
        )
        if self.policy_digest != expected_policy.policy_digest:
            raise AttemptReservationCasContractError(
                "receipt policy relation mismatch"
            )
        expectation = _copy_expectation(self.expected_snapshot_digest)
        if self.expectation_digest != expectation.expectation_digest:
            raise AttemptReservationCasContractError(
                "receipt expectation relation mismatch"
            )
        proposal = _copy_proposal(self.proposal)
        if self.proposal_digest != proposal.proposal_digest:
            raise AttemptReservationCasContractError(
                "receipt proposal relation mismatch"
            )
        expected_request_digest = _derive_request_digest(
            policy_digest=self.policy_digest,
            accounting_policy_digest=self.accounting_policy_digest,
            expectation_digest=self.expectation_digest,
            proposal_digest=self.proposal_digest,
            reservation_state_snapshot_digest=(
                self.reservation_state_snapshot_digest
            ),
            reservation_scope_digest=self.reservation_scope_digest,
        )
        if self.request_digest != expected_request_digest:
            raise AttemptReservationCasContractError(
                "receipt request relation mismatch"
            )
        _require_bounded_int(
            self.reservation_record_count,
            "reservation_record_count",
            minimum=0,
            maximum=_ABSOLUTE_MAX_RESERVATION_RECORDS,
        )
        _require_bounded_int(
            self.reservation_state_snapshot_byte_count,
            "reservation_state_snapshot_byte_count",
            minimum=1,
            maximum=_ABSOLUTE_MAX_SNAPSHOT_BYTES,
        )
        if self.reservation_record_count > self.max_reservation_records:
            raise AttemptReservationCasContractError(
                "reservation record count exceeds receipt policy"
            )
        if (
            self.reservation_state_snapshot_byte_count
            > self.max_snapshot_bytes
        ):
            raise AttemptReservationCasContractError(
                "reservation snapshot byte count exceeds receipt policy"
            )
        minimum_bytes, maximum_bytes = _canonical_snapshot_byte_bounds(
            self.reservation_record_count
        )
        if not (
            minimum_bytes
            <= self.reservation_state_snapshot_byte_count
            <= maximum_bytes
        ):
            raise AttemptReservationCasContractError(
                "reservation snapshot byte count is impossible for receipt "
                "count"
            )
        if self.reservation_record_count == 0:
            expected_empty_snapshot_digest = (
                _derive_snapshot_digest_from_record_digests(
                    reservation_scope_digest=self.reservation_scope_digest,
                    reservation_record_digests=(),
                )
            )
            if (
                self.reservation_state_snapshot_digest
                != expected_empty_snapshot_digest
            ):
                raise AttemptReservationCasContractError(
                    "empty reservation snapshot digest relation mismatch"
                )
            if self.matched_reservation_record_digest is not None:
                raise AttemptReservationCasContractError(
                    "zero-row receipt must omit matched reservation record"
                )
            if self.observed_reservation_state is not None:
                raise AttemptReservationCasContractError(
                    "zero-row receipt must omit observed reservation state"
                )
        required_json_nodes = 4 + (8 * self.reservation_record_count)
        if required_json_nodes > self.max_json_nodes:
            raise AttemptReservationCasContractError(
                "reservation snapshot JSON node count exceeds receipt policy"
            )
        required_json_depth = 4 if self.reservation_record_count else 2
        if required_json_depth > self.max_json_depth:
            raise AttemptReservationCasContractError(
                "reservation snapshot JSON depth exceeds receipt policy"
            )
        for name in (
            "expected_snapshot_digest_matches",
            "row_identity_derivation_performed",
            "proposal_identity_derivation_performed",
            "subject_lookup_performed",
            "binding_check_performed",
            "state_check_performed",
        ):
            if type(getattr(self, name)) is not bool:
                raise AttemptReservationCasContractError(
                    f"{name} must be an exact bool"
                )
        if (
            self.supplied_precondition_relation_holds is not None
            and type(self.supplied_precondition_relation_holds) is not bool
        ):
            raise AttemptReservationCasContractError(
                "supplied_precondition_relation_holds must be bool or None"
            )
        _require_optional_digest(
            self.matched_reservation_record_digest,
            "matched_reservation_record_digest",
        )
        if (
            self.observed_reservation_state is not None
            and type(self.observed_reservation_state)
            is not AttemptReservationState
        ):
            raise AttemptReservationCasContractError(
                "observed_reservation_state refused"
            )
        if type(self.disposition) is not AttemptReservationCasDisposition:
            raise AttemptReservationCasContractError(
                "receipt disposition refused"
            )
        if type(self.reason_code) is not AttemptReservationCasReasonCode:
            raise AttemptReservationCasContractError(
                "receipt reason_code refused"
            )

        expected_matches = (
            self.reservation_state_snapshot_digest
            == expectation.expected_reservation_state_snapshot_digest
        )
        if self.expected_snapshot_digest_matches is not expected_matches:
            raise AttemptReservationCasContractError(
                "receipt expected digest relation mismatch"
            )
        if not expected_matches:
            for name in (
                "row_identity_derivation_performed",
                "proposal_identity_derivation_performed",
                "subject_lookup_performed",
                "binding_check_performed",
                "state_check_performed",
            ):
                if getattr(self, name) is not False:
                    raise AttemptReservationCasContractError(
                        "expected mismatch must omit semantic phase claims"
                    )
            if self.supplied_precondition_relation_holds is not None:
                raise AttemptReservationCasContractError(
                    "expected mismatch must omit supplied relation result"
                )
            if self.matched_reservation_record_digest is not None:
                raise AttemptReservationCasContractError(
                    "expected mismatch must omit matched record"
                )
            if self.observed_reservation_state is not None:
                raise AttemptReservationCasContractError(
                    "expected mismatch must omit observed state"
                )
            if (
                self.disposition is not AttemptReservationCasDisposition.REFUSED
                or self.reason_code
                is not AttemptReservationCasReasonCode.EXPECTED_RESERVATION_STATE_SNAPSHOT_DIGEST_MISMATCH
            ):
                raise AttemptReservationCasContractError(
                    "expected mismatch outcome relation refused"
                )
        else:
            if (
                self.row_identity_derivation_performed is not True
                or self.proposal_identity_derivation_performed is not True
                or self.subject_lookup_performed is not True
            ):
                raise AttemptReservationCasContractError(
                    "matching expected digest requires identity and lookup "
                    "phases"
                )
            expected_reservation_id = derive_attempt_reservation_id(
                reservation_scope_digest=self.reservation_scope_digest,
                declared_capability_fingerprint=(
                    proposal.declared_capability_fingerprint
                ),
            )
            if proposal.reservation_id != expected_reservation_id:
                raise AttemptReservationCasContractError(
                    "receipt proposal reservation_id derivation mismatch"
                )
            _validate_receipt_outcome(self)

        for name in _TRUE_RECEIPT_FIELDS:
            if getattr(self, name) is not True:
                raise AttemptReservationCasContractError(
                    f"{name} must be literal true"
                )
        for name in _FALSE_RECEIPT_FIELDS:
            if getattr(self, name) is not False:
                raise AttemptReservationCasContractError(
                    f"{name} must be literal false"
                )
        expected_receipt_digest = sha256_digest(
            {
                "domain": (
                    "wd.understanding.attempt_reservation_cas_receipt."
                    "digest.v1"
                ),
                **AttemptReservationCasReceiptV1._core_mapping(self),
            }
        )
        if self.receipt_digest != expected_receipt_digest:
            raise AttemptReservationCasContractError(
                "receipt digest does not match fields"
            )

    def to_mapping(self) -> dict[str, Any]:
        mapping_refused = False
        result: dict[str, Any] = {}
        try:
            AttemptReservationCasReceiptV1.__post_init__(self)
            result = {
                **AttemptReservationCasReceiptV1._core_mapping(self),
                "receipt_digest": self.receipt_digest,
            }
        except (AttributeError, TypeError, ValueError):
            mapping_refused = True
        if mapping_refused:
            raise AttemptReservationCasContractError(
                "receipt mapping refused"
            )
        return result


def _snapshot_policy(
    policy: AttemptReservationCasPolicyV1,
) -> AttemptReservationCasPolicyV1:
    try:
        AttemptReservationCasPolicyV1.__post_init__(policy)
        values = {
            item.name: getattr(policy, item.name)
            for item in fields(AttemptReservationCasPolicyV1)
        }
    except (AttributeError, TypeError, ValueError):
        raise AttemptReservationCasContractError(
            "policy fields refused"
        ) from None
    return AttemptReservationCasPolicyV1(**values)


def _snapshot_request(
    request: AttemptReservationCasRequestV1,
) -> AttemptReservationCasRequestV1:
    try:
        AttemptReservationCasRequestV1.__post_init__(request)
        values = {
            "reservation_state_snapshot_utf8": bytes(
                request.reservation_state_snapshot_utf8
            ),
            "schema_version": request.schema_version,
        }
    except (AttributeError, TypeError, ValueError):
        raise AttemptReservationCasContractError(
            "request fields refused"
        ) from None
    return AttemptReservationCasRequestV1(**values)


def _receipt_core(values: dict[str, Any]) -> dict[str, Any]:
    return {
        **values,
        "schema_version": ATTEMPT_RESERVATION_CAS_RECEIPT_SCHEMA,
        **{name: True for name in _TRUE_RECEIPT_FIELDS},
        **{name: False for name in _FALSE_RECEIPT_FIELDS},
    }


def _normalize_receipt_core(values: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in values.items():
        if type(value) is AttemptReservationExpectedSnapshotDigestV1:
            value = AttemptReservationExpectedSnapshotDigestV1.to_mapping(
                value
            )
        elif type(value) is AttemptReservationTransitionProposalV1:
            value = AttemptReservationTransitionProposalV1.to_mapping(value)
        elif isinstance(value, Enum):
            value = value.value
        result[name] = value
    return result


def _make_receipt(values: dict[str, Any]) -> AttemptReservationCasReceiptV1:
    core = _receipt_core(values)
    receipt_digest = sha256_digest(
        {
            "domain": (
                "wd.understanding.attempt_reservation_cas_receipt.digest.v1"
            ),
            **_normalize_receipt_core(core),
        }
    )
    return AttemptReservationCasReceiptV1(
        **values,
        receipt_digest=receipt_digest,
    )


def _open_outcome(
    record: _ReservationRecordFacts | None,
) -> tuple[
    bool,
    bool,
    bool,
    str | None,
    AttemptReservationState | None,
    AttemptReservationCasDisposition,
    AttemptReservationCasReasonCode,
]:
    if record is None:
        return (
            False,
            False,
            True,
            None,
            None,
            AttemptReservationCasDisposition.OPEN_IF_ABSENT_PRECONDITION_RELATION_HOLDS_IN_SUPPLIED_RESERVATION_STATE_SNAPSHOT,
            AttemptReservationCasReasonCode.CAPABILITY_AND_RESERVATION_ID_ABSENT_IN_SUPPLIED_SNAPSHOT,
        )
    reason_by_state = {
        AttemptReservationState.RESERVED: (
            AttemptReservationCasReasonCode.CAPABILITY_ALREADY_RESERVED_IN_SUPPLIED_SNAPSHOT
        ),
        AttemptReservationState.COMMITTED: (
            AttemptReservationCasReasonCode.CAPABILITY_ALREADY_COMMITTED_IN_SUPPLIED_SNAPSHOT
        ),
        AttemptReservationState.ABORTED: (
            AttemptReservationCasReasonCode.CAPABILITY_ALREADY_ABORTED_IN_SUPPLIED_SNAPSHOT
        ),
    }
    return (
        False,
        True,
        False,
        record.record_digest,
        record.state,
        AttemptReservationCasDisposition.OPEN_IF_ABSENT_PRECONDITION_RELATION_DOES_NOT_HOLD_IN_SUPPLIED_RESERVATION_STATE_SNAPSHOT,
        reason_by_state[record.state],
    )


def _commit_or_abort_outcome(
    proposal: AttemptReservationTransitionProposalV1,
    record: _ReservationRecordFacts | None,
) -> tuple[
    bool,
    bool,
    bool | None,
    str | None,
    AttemptReservationState | None,
    AttemptReservationCasDisposition,
    AttemptReservationCasReasonCode,
]:
    if proposal.transition is AttemptReservationTransition.COMMIT_IF_RESERVED:
        hold_disposition = (
            AttemptReservationCasDisposition.COMMIT_IF_RESERVED_PRECONDITION_RELATION_HOLDS_IN_SUPPLIED_RESERVATION_STATE_SNAPSHOT
        )
        no_hold_disposition = (
            AttemptReservationCasDisposition.COMMIT_IF_RESERVED_PRECONDITION_RELATION_DOES_NOT_HOLD_IN_SUPPLIED_RESERVATION_STATE_SNAPSHOT
        )
        hold_reason = (
            AttemptReservationCasReasonCode.EXACT_RESERVED_RELATION_HOLDS_FOR_COMMIT
        )
    else:
        hold_disposition = (
            AttemptReservationCasDisposition.ABORT_IF_RESERVED_PRECONDITION_RELATION_HOLDS_IN_SUPPLIED_RESERVATION_STATE_SNAPSHOT
        )
        no_hold_disposition = (
            AttemptReservationCasDisposition.ABORT_IF_RESERVED_PRECONDITION_RELATION_DOES_NOT_HOLD_IN_SUPPLIED_RESERVATION_STATE_SNAPSHOT
        )
        hold_reason = (
            AttemptReservationCasReasonCode.EXACT_RESERVED_RELATION_HOLDS_FOR_ABORT
        )
    if record is None:
        return (
            False,
            False,
            False,
            None,
            None,
            no_hold_disposition,
            AttemptReservationCasReasonCode.RESERVATION_ID_ABSENT_IN_SUPPLIED_SNAPSHOT,
        )
    bindings_match = (
        record.cell_binding_digest == proposal.cell_binding_digest
        and record.campaign_id_digest == proposal.campaign_id_digest
        and record.intent_digest == proposal.intent_digest
    )
    if not bindings_match:
        return (
            True,
            False,
            None,
            record.record_digest,
            None,
            AttemptReservationCasDisposition.REFUSED,
            AttemptReservationCasReasonCode.RESERVATION_BINDING_DIGEST_MISMATCH,
        )
    if record.state is AttemptReservationState.RESERVED:
        return (
            True,
            True,
            True,
            record.record_digest,
            record.state,
            hold_disposition,
            hold_reason,
        )
    reason_by_state = {
        AttemptReservationState.COMMITTED: (
            AttemptReservationCasReasonCode.RESERVATION_ALREADY_COMMITTED_IN_SUPPLIED_SNAPSHOT
        ),
        AttemptReservationState.ABORTED: (
            AttemptReservationCasReasonCode.RESERVATION_ALREADY_ABORTED_IN_SUPPLIED_SNAPSHOT
        ),
    }
    return (
        True,
        True,
        False,
        record.record_digest,
        record.state,
        no_hold_disposition,
        reason_by_state[record.state],
    )


def evaluate_attempt_reservation_cas_relation(
    request: AttemptReservationCasRequestV1 | None = None,
    *,
    expected_snapshot_digest: (
        AttemptReservationExpectedSnapshotDigestV1 | None
    ) = None,
    proposal: AttemptReservationTransitionProposalV1 | None = None,
    policy: AttemptReservationCasPolicyV1 | None = None,
) -> AttemptReservationCasReceiptV1 | None:
    """Evaluate one local CAS-precondition relation without applying it."""

    if policy is None:
        policy = AttemptReservationCasPolicyV1()
    elif type(policy) is not AttemptReservationCasPolicyV1:
        raise AttemptReservationCasContractError(
            "policy must be an exact AttemptReservationCasPolicyV1"
        )
    try:
        selected_mode = policy.mode
    except AttributeError:
        raise AttemptReservationCasContractError(
            "policy fields refused"
        ) from None
    if selected_mode is AttemptReservationCasMode.OFF:
        return None
    policy = _snapshot_policy(policy)
    if policy.mode is not AttemptReservationCasMode.STATIC_SHADOW:
        raise AttemptReservationCasContractError("unsupported C8e mode")
    if type(request) is not AttemptReservationCasRequestV1:
        raise AttemptReservationCasContractError(
            "STATIC_SHADOW requires an exact AttemptReservationCasRequestV1"
        )
    request = _snapshot_request(request)
    expectation = _copy_expectation(expected_snapshot_digest)
    proposal_value = _copy_proposal(proposal)
    snapshot = _decode_reservation_state_snapshot(
        request.reservation_state_snapshot_utf8,
        policy,
        validate_record_identity_relations=False,
    )

    expected_matches = (
        snapshot.snapshot_digest
        == expectation.expected_reservation_state_snapshot_digest
    )
    if not expected_matches:
        row_identity_derivation_performed = False
        proposal_identity_derivation_performed = False
        subject_lookup_performed = False
        binding_check_performed = False
        state_check_performed = False
        relation_holds: bool | None = None
        matched_record_digest: str | None = None
        observed_state: AttemptReservationState | None = None
        disposition = AttemptReservationCasDisposition.REFUSED
        reason = (
            AttemptReservationCasReasonCode.EXPECTED_RESERVATION_STATE_SNAPSHOT_DIGEST_MISMATCH
        )
    else:
        _validate_record_identity_relations(snapshot)
        row_identity_derivation_performed = True
        expected_reservation_id = derive_attempt_reservation_id(
            reservation_scope_digest=snapshot.reservation_scope_digest,
            declared_capability_fingerprint=(
                proposal_value.declared_capability_fingerprint
            ),
        )
        proposal_identity_derivation_performed = True
        if proposal_value.reservation_id != expected_reservation_id:
            raise AttemptReservationCasContractError(
                "proposal reservation_id derivation mismatch"
            )
        by_capability = {
            record.declared_capability_fingerprint: record
            for record in snapshot.records
        }
        by_id = {
            record.reservation_id: record for record in snapshot.records
        }
        subject_lookup_performed = True
        id_record = by_id.get(proposal_value.reservation_id)
        if id_record is not None and (
            id_record.declared_capability_fingerprint
            != proposal_value.declared_capability_fingerprint
        ):
            binding_check_performed = False
            state_check_performed = False
            relation_holds = None
            matched_record_digest = id_record.record_digest
            observed_state = None
            disposition = AttemptReservationCasDisposition.REFUSED
            reason = (
                AttemptReservationCasReasonCode.RESERVATION_ID_BOUND_TO_DIFFERENT_DECLARED_CAPABILITY
            )
        elif (
            proposal_value.transition
            is AttemptReservationTransition.OPEN_IF_ABSENT
        ):
            (
                binding_check_performed,
                state_check_performed,
                relation_holds,
                matched_record_digest,
                observed_state,
                disposition,
                reason,
            ) = _open_outcome(
                by_capability.get(
                    proposal_value.declared_capability_fingerprint
                )
            )
        else:
            (
                binding_check_performed,
                state_check_performed,
                relation_holds,
                matched_record_digest,
                observed_state,
                disposition,
                reason,
            ) = _commit_or_abort_outcome(proposal_value, id_record)

    expectation_digest = expectation.expectation_digest
    proposal_digest = proposal_value.proposal_digest
    request_digest = _derive_request_digest(
        policy_digest=policy.policy_digest,
        accounting_policy_digest=policy.accounting_policy_digest,
        expectation_digest=expectation_digest,
        proposal_digest=proposal_digest,
        reservation_state_snapshot_digest=snapshot.snapshot_digest,
        reservation_scope_digest=snapshot.reservation_scope_digest,
    )
    return _make_receipt(
        {
            "policy_digest": policy.policy_digest,
            "accounting_policy_digest": policy.accounting_policy_digest,
            "expected_snapshot_digest": expectation,
            "expectation_digest": expectation_digest,
            "request_digest": request_digest,
            "proposal": proposal_value,
            "proposal_digest": proposal_digest,
            "reservation_state_snapshot_digest": snapshot.snapshot_digest,
            "reservation_scope_digest": snapshot.reservation_scope_digest,
            "max_snapshot_bytes": policy.max_snapshot_bytes,
            "max_reservation_records": policy.max_reservation_records,
            "max_json_depth": policy.max_json_depth,
            "max_json_nodes": policy.max_json_nodes,
            "reservation_record_count": len(snapshot.records),
            "reservation_state_snapshot_byte_count": snapshot.byte_count,
            "expected_snapshot_digest_matches": expected_matches,
            "row_identity_derivation_performed": (
                row_identity_derivation_performed
            ),
            "proposal_identity_derivation_performed": (
                proposal_identity_derivation_performed
            ),
            "subject_lookup_performed": subject_lookup_performed,
            "binding_check_performed": binding_check_performed,
            "state_check_performed": state_check_performed,
            "supplied_precondition_relation_holds": relation_holds,
            "matched_reservation_record_digest": matched_record_digest,
            "observed_reservation_state": observed_state,
            "disposition": disposition,
            "reason_code": reason,
        }
    )


__all__ = [
    "ATTEMPT_RESERVATION_CAS_ACCOUNTING_POLICY_DIGEST",
    "ATTEMPT_RESERVATION_CAS_POLICY_SCHEMA",
    "ATTEMPT_RESERVATION_CAS_RECEIPT_SCHEMA",
    "ATTEMPT_RESERVATION_CAS_REQUEST_SCHEMA",
    "ATTEMPT_RESERVATION_EXPECTED_SNAPSHOT_DIGEST_SCHEMA",
    "ATTEMPT_RESERVATION_TRANSITION_PROPOSAL_SCHEMA",
    "SUPPLIED_ATTEMPT_RESERVATION_STATE_SNAPSHOT_SCHEMA",
    "AttemptReservationCasContractError",
    "AttemptReservationCasDisposition",
    "AttemptReservationCasMode",
    "AttemptReservationCasPolicyV1",
    "AttemptReservationCasReasonCode",
    "AttemptReservationCasReceiptV1",
    "AttemptReservationCasRequestV1",
    "AttemptReservationExpectedSnapshotDigestV1",
    "AttemptReservationState",
    "AttemptReservationTransition",
    "AttemptReservationTransitionProposalV1",
    "derive_attempt_reservation_id",
    "derive_supplied_attempt_reservation_state_snapshot_digest",
    "evaluate_attempt_reservation_cas_relation",
]
