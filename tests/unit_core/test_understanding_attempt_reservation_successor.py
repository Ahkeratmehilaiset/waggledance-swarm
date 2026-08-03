"""Adversarial tests for the inert C8f successor relation accountant.

C8f composes the pure C8e precondition evaluator and, only when that supplied
relation holds, derives commitments for one canonical successor snapshot.  It
does not return raw successor bytes and performs no CAS, exclusion, durable
write, handoff, build, execution, routing, or authorization.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Any

import pytest

import waggledance.core.learning.understanding_attempt_reservation_successor as successor_module
from waggledance.core.learning.understanding_attempt_reservation_cas import (
    SUPPLIED_ATTEMPT_RESERVATION_STATE_SNAPSHOT_SCHEMA,
    AttemptReservationCasMode,
    AttemptReservationCasPolicyV1,
    AttemptReservationCasReceiptV1,
    AttemptReservationCasRequestV1,
    AttemptReservationExpectedSnapshotDigestV1,
    AttemptReservationState,
    AttemptReservationTransition,
    AttemptReservationTransitionProposalV1,
    derive_attempt_reservation_id,
    derive_supplied_attempt_reservation_state_snapshot_digest,
)
from waggledance.core.learning.understanding_attempt_reservation_successor import (
    ATTEMPT_RESERVATION_SUCCESSOR_ACCOUNTING_POLICY_DIGEST,
    ATTEMPT_RESERVATION_SUCCESSOR_POLICY_SCHEMA,
    ATTEMPT_RESERVATION_SUCCESSOR_RECEIPT_SCHEMA,
    ATTEMPT_RESERVATION_SUCCESSOR_REQUEST_SCHEMA,
    AttemptReservationSuccessorContractError,
    AttemptReservationSuccessorDisposition,
    AttemptReservationSuccessorMode,
    AttemptReservationSuccessorPolicyV1,
    AttemptReservationSuccessorReasonCode,
    AttemptReservationSuccessorReceiptV1,
    AttemptReservationSuccessorRequestV1,
    evaluate_attempt_reservation_successor_relation,
)
from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest


_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


def _digest(label: str) -> str:
    return sha256_digest({"test_label": label})


def _policy(**changes: Any) -> AttemptReservationSuccessorPolicyV1:
    return AttemptReservationSuccessorPolicyV1(
        mode=AttemptReservationSuccessorMode.STATIC_SHADOW,
        **changes,
    )


def _c8e_policy(
    policy: AttemptReservationSuccessorPolicyV1 | None = None,
) -> AttemptReservationCasPolicyV1:
    selected = policy or _policy()
    return AttemptReservationCasPolicyV1(
        mode=AttemptReservationCasMode.STATIC_SHADOW,
        max_snapshot_bytes=selected.max_snapshot_bytes,
        max_reservation_records=selected.max_reservation_records,
        max_json_depth=selected.max_json_depth,
        max_json_nodes=selected.max_json_nodes,
    )


def _reservation(
    label: str = "one",
    *,
    scope: str | None = None,
    fingerprint: str | None = None,
    state: AttemptReservationState | str = AttemptReservationState.RESERVED,
    cell_binding_digest: str | None = None,
    campaign_id_digest: str | None = None,
    intent_digest: str | None = None,
    state_evidence_digest: str | None = None,
) -> dict[str, str]:
    selected_scope = scope or _digest("reservation-scope")
    selected_fingerprint = fingerprint or _digest(f"capability-{label}")
    return {
        "reservation_id": derive_attempt_reservation_id(
            reservation_scope_digest=selected_scope,
            declared_capability_fingerprint=selected_fingerprint,
        ),
        "declared_capability_fingerprint": selected_fingerprint,
        "state": state.value if isinstance(state, AttemptReservationState) else state,
        "cell_binding_digest": cell_binding_digest or _digest(f"cell-{label}"),
        "campaign_id_digest": campaign_id_digest or _digest(f"campaign-{label}"),
        "intent_digest": intent_digest or _digest(f"intent-{label}"),
        "state_evidence_digest": (
            state_evidence_digest or _digest(f"state-evidence-{label}")
        ),
    }


def _sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item["declared_capability_fingerprint"],
        item["reservation_id"],
        item["campaign_id_digest"],
        item["cell_binding_digest"],
        item["intent_digest"],
        item["state"],
        item["state_evidence_digest"],
    )


def _snapshot_bytes(
    reservations: list[dict[str, Any]] | None = None,
    *,
    scope: str | None = None,
) -> bytes:
    selected_scope = scope or _digest("reservation-scope")
    selected = (
        [_reservation(scope=selected_scope)]
        if reservations is None
        else reservations
    )
    return canonical_json_bytes(
        {
            "schema_version": (
                SUPPLIED_ATTEMPT_RESERVATION_STATE_SNAPSHOT_SCHEMA
            ),
            "reservation_scope_digest": selected_scope,
            "reservations": sorted(selected, key=_sort_key),
        }
    )


def _expected(
    snapshot_utf8: bytes,
    policy: AttemptReservationSuccessorPolicyV1,
    *,
    digest: str | None = None,
) -> AttemptReservationExpectedSnapshotDigestV1:
    return AttemptReservationExpectedSnapshotDigestV1(
        expected_reservation_state_snapshot_digest=(
            digest
            if digest is not None
            else derive_supplied_attempt_reservation_state_snapshot_digest(
                snapshot_utf8,
                _c8e_policy(policy),
            )
        )
    )


def _proposal(
    transition: AttemptReservationTransition,
    *,
    scope: str | None = None,
    fingerprint: str | None = None,
    cell_binding_digest: str | None = None,
    campaign_id_digest: str | None = None,
    intent_digest: str | None = None,
    transition_evidence_digest: str | None = None,
) -> AttemptReservationTransitionProposalV1:
    selected_scope = scope or _digest("reservation-scope")
    selected_fingerprint = fingerprint or _digest("capability-one")
    return AttemptReservationTransitionProposalV1(
        transition=transition,
        reservation_id=derive_attempt_reservation_id(
            reservation_scope_digest=selected_scope,
            declared_capability_fingerprint=selected_fingerprint,
        ),
        declared_capability_fingerprint=selected_fingerprint,
        cell_binding_digest=cell_binding_digest or _digest("cell-one"),
        campaign_id_digest=campaign_id_digest or _digest("campaign-one"),
        intent_digest=intent_digest or _digest("intent-one"),
        transition_evidence_digest=(
            transition_evidence_digest or _digest("transition-evidence")
        ),
    )


def _evaluate(
    transition: AttemptReservationTransition,
    *,
    reservations: list[dict[str, Any]] | None = None,
    scope: str | None = None,
    policy: AttemptReservationSuccessorPolicyV1 | None = None,
    expected_digest: str | None = None,
    fingerprint: str | None = None,
    cell_binding_digest: str | None = None,
    campaign_id_digest: str | None = None,
    intent_digest: str | None = None,
    transition_evidence_digest: str | None = None,
) -> AttemptReservationSuccessorReceiptV1:
    selected_scope = scope or _digest("reservation-scope")
    selected_policy = policy or _policy()
    snapshot = _snapshot_bytes(reservations, scope=selected_scope)
    receipt = evaluate_attempt_reservation_successor_relation(
        AttemptReservationSuccessorRequestV1(
            reservation_state_snapshot_utf8=snapshot,
        ),
        expected_snapshot_digest=_expected(
            snapshot,
            selected_policy,
            digest=expected_digest,
        ),
        proposal=_proposal(
            transition,
            scope=selected_scope,
            fingerprint=fingerprint,
            cell_binding_digest=cell_binding_digest,
            campaign_id_digest=campaign_id_digest,
            intent_digest=intent_digest,
            transition_evidence_digest=transition_evidence_digest,
        ),
        policy=selected_policy,
    )
    assert isinstance(receipt, AttemptReservationSuccessorReceiptV1)
    return receipt


def _record_digest(record: dict[str, Any]) -> str:
    return sha256_digest(
        {
            "domain": (
                "wd.understanding.supplied_attempt_reservation_"
                "state_record.digest.v1"
            ),
            **record,
        }
    )


def _snapshot_digest_from_record_digests(
    *,
    scope: str,
    record_digests: tuple[str, ...],
) -> str:
    return sha256_digest(
        {
            "domain": (
                "wd.understanding.supplied_attempt_reservation_state_"
                "snapshot.digest.v1"
            ),
            "schema_version": SUPPLIED_ATTEMPT_RESERVATION_STATE_SNAPSHOT_SCHEMA,
            "reservation_scope_digest": scope,
            "reservation_record_digests": list(record_digests),
        }
    )


def _normalize(value: Any) -> Any:
    if hasattr(value, "to_mapping"):
        return value.to_mapping()
    if hasattr(value, "value"):
        return value.value
    if type(value) is tuple:
        return list(value)
    return value


def _reseal(
    receipt: AttemptReservationSuccessorReceiptV1,
    **changes: Any,
) -> AttemptReservationSuccessorReceiptV1:
    constructor = {
        field.name: getattr(receipt, field.name)
        for field in dataclasses.fields(receipt)
    }
    constructor.update(changes)
    core = receipt.to_mapping()
    core.pop("receipt_digest")
    for name, value in changes.items():
        if name != "receipt_digest":
            core[name] = _normalize(value)
    constructor["receipt_digest"] = sha256_digest(
        {
            "domain": (
                "wd.understanding.attempt_reservation_successor_receipt."
                "digest.v1"
            ),
            **core,
        }
    )
    return AttemptReservationSuccessorReceiptV1(**constructor)


class _Bomb:
    def __getattribute__(self, name: str) -> Any:
        raise AssertionError(f"OFF inspected hostile attribute {name}")


def test_public_schemas_enums_and_accounting_digest_are_exact() -> None:
    assert {
        ATTEMPT_RESERVATION_SUCCESSOR_POLICY_SCHEMA,
        ATTEMPT_RESERVATION_SUCCESSOR_REQUEST_SCHEMA,
        ATTEMPT_RESERVATION_SUCCESSOR_RECEIPT_SCHEMA,
    } == {
        "wd.understanding.attempt_reservation_successor_policy.v1",
        "wd.understanding.attempt_reservation_successor_request.v1",
        "wd.understanding.attempt_reservation_successor_receipt.v1",
    }
    assert {item.value for item in AttemptReservationSuccessorMode} == {
        "off",
        "static_shadow",
    }
    assert {item.value for item in AttemptReservationSuccessorDisposition} == {
        "refused",
        "precondition_relation_does_not_hold_no_successor",
        "open_successor_relation_holds_in_locally_derived_snapshot",
        "commit_successor_relation_holds_in_locally_derived_snapshot",
        "abort_successor_relation_holds_in_locally_derived_snapshot",
        "successor_resource_bounds_refused",
    }
    assert {item.value for item in AttemptReservationSuccessorReasonCode} == {
        "source_precondition_refused",
        "source_precondition_does_not_hold",
        "open_reserved_successor_derived",
        "commit_committed_successor_derived",
        "abort_aborted_successor_derived",
        "successor_record_limit_exceeded",
        "successor_byte_limit_exceeded",
    }
    combined = " ".join(item.value for item in AttemptReservationSuccessorDisposition)
    for forbidden in ("applied", "written", "durable", "authorized", "cas_succeeded"):
        assert forbidden not in combined
    assert _SHA256.fullmatch(
        ATTEMPT_RESERVATION_SUCCESSOR_ACCOUNTING_POLICY_DIGEST
    )
    accounting = successor_module._ACCOUNTING_POLICY
    assert accounting["successor_derivation"] == (
        "pure_local_canonical_relation_only"
    )
    assert accounting["state_evidence"] == (
        "fixed_domain_derived_from_base_and_proposal"
    )
    assert accounting["successor_raw_bytes_returned"] is False
    assert accounting["atomic_cas_performed"] is False
    assert accounting["transition_persisted"] is False
    assert accounting["authority_granted"] is False
    assert sha256_digest(accounting) == (
        ATTEMPT_RESERVATION_SUCCESSOR_ACCOUNTING_POLICY_DIGEST
    )


@pytest.mark.parametrize(
    "changes",
    (
        {"mode": "static_shadow"},
        {"max_snapshot_bytes": True},
        {"max_snapshot_bytes": 127},
        {"max_snapshot_bytes": 2_097_153},
        {"max_reservation_records": -1},
        {"max_reservation_records": 2_049},
        {"max_json_depth": 0},
        {"max_json_depth": 7},
        {"max_json_nodes": 0},
        {"max_json_nodes": 32_769},
    ),
)
def test_policy_is_default_off_exact_typed_and_absolutely_bounded(
    changes: dict[str, Any],
) -> None:
    values: dict[str, Any] = {
        "mode": AttemptReservationSuccessorMode.STATIC_SHADOW,
        "max_snapshot_bytes": 2_097_152,
        "max_reservation_records": 2_048,
        "max_json_depth": 6,
        "max_json_nodes": 32_768,
    }
    values.update(changes)
    with pytest.raises(AttemptReservationSuccessorContractError):
        AttemptReservationSuccessorPolicyV1(**values)


def test_default_off_precedes_request_expected_proposal_inspection() -> None:
    assert AttemptReservationSuccessorPolicyV1().mode is (
        AttemptReservationSuccessorMode.OFF
    )
    assert evaluate_attempt_reservation_successor_relation.__kwdefaults__["policy"] is None
    assert evaluate_attempt_reservation_successor_relation() is None
    assert evaluate_attempt_reservation_successor_relation(
        _Bomb(),  # type: ignore[arg-type]
        expected_snapshot_digest=_Bomb(),  # type: ignore[arg-type]
        proposal=_Bomb(),  # type: ignore[arg-type]
    ) is None
    assert evaluate_attempt_reservation_successor_relation(
        _Bomb(),  # type: ignore[arg-type]
        expected_snapshot_digest=_Bomb(),  # type: ignore[arg-type]
        proposal=_Bomb(),  # type: ignore[arg-type]
        policy=AttemptReservationSuccessorPolicyV1(
            mode=AttemptReservationSuccessorMode.OFF
        ),
    ) is None


@pytest.mark.parametrize("deleted", ("mode", "max_json_nodes"))
def test_deleted_policy_slots_are_chain_free_contract_errors(deleted: str) -> None:
    policy = _policy()
    object.__delattr__(policy, deleted)
    with pytest.raises(AttemptReservationSuccessorContractError) as raised:
        evaluate_attempt_reservation_successor_relation(policy=policy)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_request_is_exact_raw_bytes_only_and_deleted_slot_is_normalized() -> None:
    snapshot = _snapshot_bytes([])
    request = AttemptReservationSuccessorRequestV1(
        reservation_state_snapshot_utf8=snapshot
    )
    assert set(dataclasses.asdict(request)) == {
        "reservation_state_snapshot_utf8",
        "schema_version",
    }
    for value in (bytearray(snapshot), memoryview(snapshot), snapshot.decode("utf-8")):
        with pytest.raises(AttemptReservationSuccessorContractError):
            AttemptReservationSuccessorRequestV1(
                reservation_state_snapshot_utf8=value  # type: ignore[arg-type]
            )
    object.__delattr__(request, "reservation_state_snapshot_utf8")
    with pytest.raises(AttemptReservationSuccessorContractError) as raised:
        evaluate_attempt_reservation_successor_relation(
            request,
            expected_snapshot_digest=_Bomb(),  # type: ignore[arg-type]
            proposal=_Bomb(),  # type: ignore[arg-type]
            policy=_policy(),
        )
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_static_shadow_requires_exact_request_and_reused_c8e_inputs() -> None:
    scope = _digest("reservation-scope")
    snapshot = _snapshot_bytes([], scope=scope)
    policy = _policy()
    expected = _expected(snapshot, policy)
    proposal = _proposal(
        AttemptReservationTransition.OPEN_IF_ABSENT,
        scope=scope,
    )
    calls = (
        (None, expected, proposal),
        ({"reservation_state_snapshot_utf8": snapshot}, expected, proposal),
        (
            AttemptReservationSuccessorRequestV1(
                reservation_state_snapshot_utf8=snapshot
            ),
            None,
            proposal,
        ),
        (
            AttemptReservationSuccessorRequestV1(
                reservation_state_snapshot_utf8=snapshot
            ),
            expected,
            None,
        ),
    )
    for request, selected_expected, selected_proposal in calls:
        with pytest.raises(AttemptReservationSuccessorContractError):
            evaluate_attempt_reservation_successor_relation(
                request,  # type: ignore[arg-type]
                expected_snapshot_digest=selected_expected,
                proposal=selected_proposal,
                policy=policy,
            )

    class ExpectedSubclass(AttemptReservationExpectedSnapshotDigestV1):
        pass

    class ProposalSubclass(AttemptReservationTransitionProposalV1):
        pass

    expected_subclass = object.__new__(ExpectedSubclass)
    for field in dataclasses.fields(AttemptReservationExpectedSnapshotDigestV1):
        object.__setattr__(
            expected_subclass,
            field.name,
            getattr(expected, field.name),
        )
    with pytest.raises(AttemptReservationSuccessorContractError):
        evaluate_attempt_reservation_successor_relation(
            AttemptReservationSuccessorRequestV1(
                reservation_state_snapshot_utf8=snapshot
            ),
            expected_snapshot_digest=expected_subclass,
            proposal=proposal,
            policy=policy,
        )
    proposal_subclass = object.__new__(ProposalSubclass)
    for field in dataclasses.fields(AttemptReservationTransitionProposalV1):
        object.__setattr__(
            proposal_subclass,
            field.name,
            getattr(proposal, field.name),
        )
    with pytest.raises(AttemptReservationSuccessorContractError):
        evaluate_attempt_reservation_successor_relation(
            AttemptReservationSuccessorRequestV1(
                reservation_state_snapshot_utf8=snapshot
            ),
            expected_snapshot_digest=expected,
            proposal=proposal_subclass,
            policy=policy,
        )


def test_expected_mismatch_dominates_successor_derivation() -> None:
    receipt = _evaluate(
        AttemptReservationTransition.OPEN_IF_ABSENT,
        reservations=[],
        expected_digest=_digest("wrong-snapshot"),
    )
    assert receipt.disposition is AttemptReservationSuccessorDisposition.REFUSED
    assert receipt.reason_code is (
        AttemptReservationSuccessorReasonCode.SOURCE_PRECONDITION_REFUSED
    )
    assert isinstance(receipt.source_c8e_receipt, AttemptReservationCasReceiptV1)
    assert receipt.source_c8e_receipt.expected_snapshot_digest_matches is False
    assert receipt.source_precondition_relation_holds is None
    assert receipt.successor_derivation_performed is False
    assert receipt.successor_snapshot_relation_holds is None
    for field in (
        "target_reservation_state",
        "derived_state_evidence_digest",
        "successor_reservation_state_snapshot_digest",
        "successor_reservation_record_count",
        "successor_reservation_state_snapshot_byte_count",
        "successor_reservation_record_digests",
    ):
        assert getattr(receipt, field) is None


@pytest.mark.parametrize(
    "bad_snapshot",
    (
        b'{"canary":"RAW-SUCCESSOR-JSON-CANARY"',
        b"RAW-SUCCESSOR-UTF8-CANARY-\xff",
    ),
)
def test_source_parse_failures_are_raw_free_chain_free_successor_errors(
    bad_snapshot: bytes,
) -> None:
    scope = _digest("reservation-scope")
    with pytest.raises(AttemptReservationSuccessorContractError) as raised:
        evaluate_attempt_reservation_successor_relation(
            AttemptReservationSuccessorRequestV1(
                reservation_state_snapshot_utf8=bad_snapshot
            ),
            expected_snapshot_digest=AttemptReservationExpectedSnapshotDigestV1(
                expected_reservation_state_snapshot_digest=_digest("snapshot")
            ),
            proposal=_proposal(
                AttemptReservationTransition.OPEN_IF_ABSENT,
                scope=scope,
            ),
            policy=_policy(),
        )
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert "CANARY" not in str(raised.value)


def test_open_existing_precondition_false_has_no_successor() -> None:
    scope = _digest("reservation-scope")
    receipt = _evaluate(
        AttemptReservationTransition.OPEN_IF_ABSENT,
        reservations=[_reservation(scope=scope)],
        scope=scope,
    )
    assert receipt.disposition is (
        AttemptReservationSuccessorDisposition.PRECONDITION_RELATION_DOES_NOT_HOLD_NO_SUCCESSOR
    )
    assert receipt.reason_code is (
        AttemptReservationSuccessorReasonCode.SOURCE_PRECONDITION_DOES_NOT_HOLD
    )
    assert receipt.source_precondition_relation_holds is False
    assert receipt.successor_derivation_performed is False
    assert receipt.successor_snapshot_relation_holds is False
    assert receipt.successor_reservation_state_snapshot_digest is None


@pytest.mark.parametrize(
    ("transition", "target", "disposition", "reason"),
    (
        (
            AttemptReservationTransition.OPEN_IF_ABSENT,
            AttemptReservationState.RESERVED,
            AttemptReservationSuccessorDisposition.OPEN_SUCCESSOR_RELATION_HOLDS_IN_LOCALLY_DERIVED_SNAPSHOT,
            AttemptReservationSuccessorReasonCode.OPEN_RESERVED_SUCCESSOR_DERIVED,
        ),
        (
            AttemptReservationTransition.COMMIT_IF_RESERVED,
            AttemptReservationState.COMMITTED,
            AttemptReservationSuccessorDisposition.COMMIT_SUCCESSOR_RELATION_HOLDS_IN_LOCALLY_DERIVED_SNAPSHOT,
            AttemptReservationSuccessorReasonCode.COMMIT_COMMITTED_SUCCESSOR_DERIVED,
        ),
        (
            AttemptReservationTransition.ABORT_IF_RESERVED,
            AttemptReservationState.ABORTED,
            AttemptReservationSuccessorDisposition.ABORT_SUCCESSOR_RELATION_HOLDS_IN_LOCALLY_DERIVED_SNAPSHOT,
            AttemptReservationSuccessorReasonCode.ABORT_ABORTED_SUCCESSOR_DERIVED,
        ),
    ),
)
def test_all_three_transitions_derive_exact_canonical_successor(
    transition: AttemptReservationTransition,
    target: AttemptReservationState,
    disposition: AttemptReservationSuccessorDisposition,
    reason: AttemptReservationSuccessorReasonCode,
) -> None:
    scope = _digest("reservation-scope")
    base_rows = (
        []
        if transition is AttemptReservationTransition.OPEN_IF_ABSENT
        else [_reservation(scope=scope)]
    )
    receipt = _evaluate(
        transition,
        reservations=base_rows,
        scope=scope,
    )
    again = _evaluate(
        transition,
        reservations=base_rows,
        scope=scope,
    )
    assert receipt.to_mapping() == again.to_mapping()
    base_bytes = _snapshot_bytes(base_rows, scope=scope)
    assert receipt.source_c8e_receipt_digest == (
        receipt.source_c8e_receipt.receipt_digest
    )
    assert receipt.base_reservation_state_snapshot_digest == (
        receipt.source_c8e_receipt.reservation_state_snapshot_digest
    )
    assert receipt.reservation_scope_digest == scope
    assert receipt.base_reservation_record_count == len(base_rows)
    assert receipt.base_reservation_state_snapshot_byte_count == len(base_bytes)
    for digest in (
        receipt.policy_digest,
        receipt.accounting_policy_digest,
        receipt.request_digest,
        receipt.source_c8e_receipt_digest,
        receipt.base_reservation_state_snapshot_digest,
    ):
        assert _SHA256.fullmatch(digest)
    assert receipt.disposition is disposition
    assert receipt.reason_code is reason
    assert receipt.source_precondition_relation_holds is True
    assert receipt.successor_derivation_performed is True
    assert receipt.successor_snapshot_relation_holds is True
    assert receipt.target_reservation_state is target
    assert _SHA256.fullmatch(receipt.derived_state_evidence_digest or "")
    assert receipt.derived_state_evidence_digest == sha256_digest(
        {
            "domain": (
                "wd.understanding.attempt_reservation_successor_"
                "state_evidence.digest.v1"
            ),
            "base_reservation_state_snapshot_digest": (
                receipt.source_c8e_receipt.reservation_state_snapshot_digest
            ),
            "proposal_digest": receipt.source_c8e_receipt.proposal_digest,
            "reservation_id": (
                receipt.source_c8e_receipt.proposal.reservation_id
            ),
            "transition": (
                receipt.source_c8e_receipt.proposal.transition.value
            ),
            "target_reservation_state": target.value,
        }
    )
    assert _SHA256.fullmatch(
        receipt.successor_reservation_state_snapshot_digest or ""
    )
    assert receipt.successor_reservation_record_count == 1

    proposal = receipt.source_c8e_receipt.proposal
    expected_row = {
        "reservation_id": proposal.reservation_id,
        "declared_capability_fingerprint": (
            proposal.declared_capability_fingerprint
        ),
        "state": target.value,
        "cell_binding_digest": proposal.cell_binding_digest,
        "campaign_id_digest": proposal.campaign_id_digest,
        "intent_digest": proposal.intent_digest,
        "state_evidence_digest": receipt.derived_state_evidence_digest,
    }
    successor_bytes = _snapshot_bytes([expected_row], scope=scope)
    expected_record_digests = (_record_digest(expected_row),)
    assert receipt.successor_reservation_record_digests == expected_record_digests
    assert receipt.successor_reservation_state_snapshot_byte_count == len(
        successor_bytes
    )
    assert receipt.successor_reservation_state_snapshot_digest == (
        derive_supplied_attempt_reservation_state_snapshot_digest(
            successor_bytes,
            _c8e_policy(),
        )
    )


def test_open_insertion_preserves_canonical_order_and_existing_record_digests() -> None:
    scope = _digest("reservation-scope")
    new_fingerprint = _digest("capability-new")
    existing = [
        _reservation("a", scope=scope),
        _reservation("b", scope=scope, state=AttemptReservationState.COMMITTED),
        _reservation("c", scope=scope, state=AttemptReservationState.ABORTED),
    ]
    receipt = _evaluate(
        AttemptReservationTransition.OPEN_IF_ABSENT,
        reservations=existing,
        scope=scope,
        fingerprint=new_fingerprint,
        cell_binding_digest=_digest("cell-new"),
        campaign_id_digest=_digest("campaign-new"),
        intent_digest=_digest("intent-new"),
    )
    proposal = receipt.source_c8e_receipt.proposal
    inserted = {
        "reservation_id": proposal.reservation_id,
        "declared_capability_fingerprint": new_fingerprint,
        "state": AttemptReservationState.RESERVED.value,
        "cell_binding_digest": proposal.cell_binding_digest,
        "campaign_id_digest": proposal.campaign_id_digest,
        "intent_digest": proposal.intent_digest,
        "state_evidence_digest": receipt.derived_state_evidence_digest,
    }
    successor_rows = sorted([*existing, inserted], key=_sort_key)
    expected_digests = tuple(_record_digest(row) for row in successor_rows)
    assert receipt.successor_reservation_record_digests == expected_digests
    assert receipt.successor_reservation_record_count == 4
    assert receipt.successor_reservation_state_snapshot_digest == (
        _snapshot_digest_from_record_digests(
            scope=scope,
            record_digests=expected_digests,
        )
    )


def test_transition_evidence_changes_derived_evidence_not_stable_identity() -> None:
    scope = _digest("reservation-scope")
    first = _evaluate(
        AttemptReservationTransition.OPEN_IF_ABSENT,
        reservations=[],
        scope=scope,
        transition_evidence_digest=_digest("transition-evidence-a"),
    )
    second = _evaluate(
        AttemptReservationTransition.OPEN_IF_ABSENT,
        reservations=[],
        scope=scope,
        transition_evidence_digest=_digest("transition-evidence-b"),
    )
    assert (
        first.source_c8e_receipt.proposal.reservation_id
        == second.source_c8e_receipt.proposal.reservation_id
    )
    assert first.request_digest != second.request_digest
    assert first.derived_state_evidence_digest != second.derived_state_evidence_digest
    assert (
        first.successor_reservation_state_snapshot_digest
        != second.successor_reservation_state_snapshot_digest
    )


def test_successor_commitment_is_invariant_across_nonbinding_policy_bounds() -> None:
    scope = _digest("reservation-scope")
    default = _evaluate(
        AttemptReservationTransition.OPEN_IF_ABSENT,
        reservations=[],
        scope=scope,
    )
    constrained = _evaluate(
        AttemptReservationTransition.OPEN_IF_ABSENT,
        reservations=[],
        scope=scope,
        policy=_policy(
            max_snapshot_bytes=4_096,
            max_reservation_records=16,
            max_json_depth=4,
            max_json_nodes=128,
        ),
    )
    assert default.policy_digest != constrained.policy_digest
    assert (
        default.source_c8e_receipt.receipt_digest
        != constrained.source_c8e_receipt.receipt_digest
    )
    assert (
        default.source_c8e_receipt.proposal_digest
        == constrained.source_c8e_receipt.proposal_digest
    )
    assert (
        default.base_reservation_state_snapshot_digest
        == constrained.base_reservation_state_snapshot_digest
    )
    assert (
        default.derived_state_evidence_digest
        == constrained.derived_state_evidence_digest
    )
    assert (
        default.successor_reservation_record_digests
        == constrained.successor_reservation_record_digests
    )
    assert (
        default.successor_reservation_state_snapshot_digest
        == constrained.successor_reservation_state_snapshot_digest
    )


@pytest.mark.parametrize(
    "transition",
    (
        AttemptReservationTransition.COMMIT_IF_RESERVED,
        AttemptReservationTransition.ABORT_IF_RESERVED,
    ),
)
def test_absent_terminal_and_binding_failures_produce_no_successor(
    transition: AttemptReservationTransition,
) -> None:
    scope = _digest("reservation-scope")
    cases = (
        _evaluate(transition, reservations=[], scope=scope),
        _evaluate(
            transition,
            reservations=[
                _reservation(scope=scope, state=AttemptReservationState.COMMITTED)
            ],
            scope=scope,
        ),
        _evaluate(
            transition,
            reservations=[_reservation(scope=scope)],
            scope=scope,
            cell_binding_digest=_digest("wrong-cell"),
        ),
    )
    assert cases[0].source_precondition_relation_holds is False
    assert cases[1].source_precondition_relation_holds is False
    assert cases[2].source_precondition_relation_holds is None
    assert cases[0].successor_snapshot_relation_holds is False
    assert cases[1].successor_snapshot_relation_holds is False
    assert cases[2].successor_snapshot_relation_holds is None
    for receipt in cases:
        assert receipt.successor_derivation_performed is False
        assert receipt.successor_reservation_state_snapshot_digest is None
        assert receipt.target_reservation_state is None


def test_configured_record_and_byte_successor_bounds_refuse_without_output() -> None:
    scope = _digest("reservation-scope")
    empty = _snapshot_bytes([], scope=scope)
    record_limited = _evaluate(
        AttemptReservationTransition.OPEN_IF_ABSENT,
        reservations=[],
        scope=scope,
        policy=_policy(max_reservation_records=0),
    )
    assert record_limited.disposition is (
        AttemptReservationSuccessorDisposition.SUCCESSOR_RESOURCE_BOUNDS_REFUSED
    )
    assert record_limited.reason_code is (
        AttemptReservationSuccessorReasonCode.SUCCESSOR_RECORD_LIMIT_EXCEEDED
    )
    assert record_limited.source_precondition_relation_holds is True
    assert record_limited.successor_derivation_performed is True
    assert record_limited.successor_snapshot_relation_holds is False
    assert record_limited.successor_reservation_state_snapshot_digest is None
    assert record_limited.target_reservation_state is None
    assert record_limited.derived_state_evidence_digest is None
    assert record_limited.successor_reservation_record_count is None
    assert record_limited.successor_reservation_state_snapshot_byte_count is None
    assert record_limited.successor_reservation_record_digests is None

    byte_limited = _evaluate(
        AttemptReservationTransition.OPEN_IF_ABSENT,
        reservations=[],
        scope=scope,
        policy=_policy(max_snapshot_bytes=len(empty)),
    )
    assert byte_limited.disposition is (
        AttemptReservationSuccessorDisposition.SUCCESSOR_RESOURCE_BOUNDS_REFUSED
    )
    assert byte_limited.reason_code is (
        AttemptReservationSuccessorReasonCode.SUCCESSOR_BYTE_LIMIT_EXCEEDED
    )
    assert byte_limited.source_precondition_relation_holds is True
    assert byte_limited.successor_derivation_performed is True
    assert byte_limited.successor_snapshot_relation_holds is False
    assert byte_limited.successor_reservation_state_snapshot_digest is None
    assert byte_limited.target_reservation_state is None
    assert byte_limited.derived_state_evidence_digest is None
    assert byte_limited.successor_reservation_record_count is None
    assert byte_limited.successor_reservation_state_snapshot_byte_count is None
    assert byte_limited.successor_reservation_record_digests is None


@pytest.mark.parametrize(
    "transition,reservations,max_records",
    (
        (
            AttemptReservationTransition.COMMIT_IF_RESERVED,
            [_reservation()],
            10,
        ),
        (
            AttemptReservationTransition.ABORT_IF_RESERVED,
            [_reservation()],
            10,
        ),
        (
            AttemptReservationTransition.OPEN_IF_ABSENT,
            [],
            1,
        ),
    ),
)
def test_resealed_record_limit_refusal_requires_full_open_transition(
    transition: AttemptReservationTransition,
    reservations: list[dict[str, str]],
    max_records: int,
) -> None:
    receipt = _evaluate(
        transition,
        reservations=reservations,
        policy=_policy(max_reservation_records=max_records),
    )
    assert receipt.source_precondition_relation_holds is True
    assert receipt.successor_snapshot_relation_holds is True
    with pytest.raises(AttemptReservationSuccessorContractError):
        _reseal(
            receipt,
            successor_snapshot_relation_holds=False,
            target_reservation_state=None,
            derived_state_evidence_digest=None,
            successor_reservation_state_snapshot_digest=None,
            successor_reservation_record_count=None,
            successor_reservation_state_snapshot_byte_count=None,
            successor_reservation_record_digests=None,
            disposition=(
                AttemptReservationSuccessorDisposition.SUCCESSOR_RESOURCE_BOUNDS_REFUSED
            ),
            reason_code=(
                AttemptReservationSuccessorReasonCode.SUCCESSOR_RECORD_LIMIT_EXCEEDED
            ),
        )


@pytest.mark.parametrize(
    "transition,reservations,extra",
    (
        (AttemptReservationTransition.OPEN_IF_ABSENT, [], {}),
        (
            AttemptReservationTransition.OPEN_IF_ABSENT,
            [_reservation("existing")],
            {
                "fingerprint": _digest("capability-new"),
                "cell_binding_digest": _digest("cell-new"),
                "campaign_id_digest": _digest("campaign-new"),
                "intent_digest": _digest("intent-new"),
            },
        ),
        (
            AttemptReservationTransition.COMMIT_IF_RESERVED,
            [_reservation()],
            {},
        ),
        (
            AttemptReservationTransition.ABORT_IF_RESERVED,
            [_reservation()],
            {},
        ),
    ),
)
def test_resealed_byte_limit_refusal_requires_exact_successor_overflow(
    transition: AttemptReservationTransition,
    reservations: list[dict[str, str]],
    extra: dict[str, str],
) -> None:
    receipt = _evaluate(
        transition,
        reservations=reservations,
        **extra,
    )
    assert receipt.source_precondition_relation_holds is True
    assert receipt.successor_snapshot_relation_holds is True
    assert receipt.successor_reservation_state_snapshot_byte_count is not None
    assert (
        receipt.successor_reservation_state_snapshot_byte_count
        <= receipt.max_snapshot_bytes
    )
    with pytest.raises(AttemptReservationSuccessorContractError):
        _reseal(
            receipt,
            successor_snapshot_relation_holds=False,
            target_reservation_state=None,
            derived_state_evidence_digest=None,
            successor_reservation_state_snapshot_digest=None,
            successor_reservation_record_count=None,
            successor_reservation_state_snapshot_byte_count=None,
            successor_reservation_record_digests=None,
            disposition=(
                AttemptReservationSuccessorDisposition.SUCCESSOR_RESOURCE_BOUNDS_REFUSED
            ),
            reason_code=(
                AttemptReservationSuccessorReasonCode.SUCCESSOR_BYTE_LIMIT_EXCEEDED
            ),
        )


def test_positive_receipt_byte_count_is_exactly_bound_to_transition() -> None:
    receipt = _evaluate(
        AttemptReservationTransition.OPEN_IF_ABSENT,
        reservations=[_reservation("existing")],
        fingerprint=_digest("capability-new"),
        cell_binding_digest=_digest("cell-new"),
        campaign_id_digest=_digest("campaign-new"),
        intent_digest=_digest("intent-new"),
    )
    assert receipt.successor_reservation_state_snapshot_byte_count is not None
    with pytest.raises(AttemptReservationSuccessorContractError):
        _reseal(
            receipt,
            successor_reservation_state_snapshot_byte_count=(
                receipt.successor_reservation_state_snapshot_byte_count + 1
            ),
        )


def test_commit_can_truthfully_hit_exact_successor_byte_limit() -> None:
    scope = _digest("reservation-scope")
    reservations = [_reservation(scope=scope)]
    base = _snapshot_bytes(reservations, scope=scope)
    receipt = _evaluate(
        AttemptReservationTransition.COMMIT_IF_RESERVED,
        reservations=reservations,
        scope=scope,
        policy=_policy(max_snapshot_bytes=len(base)),
    )
    assert receipt.source_precondition_relation_holds is True
    assert receipt.successor_derivation_performed is True
    assert receipt.successor_snapshot_relation_holds is False
    assert receipt.disposition is (
        AttemptReservationSuccessorDisposition.SUCCESSOR_RESOURCE_BOUNDS_REFUSED
    )
    assert receipt.reason_code is (
        AttemptReservationSuccessorReasonCode.SUCCESSOR_BYTE_LIMIT_EXCEEDED
    )


def test_absolute_2048_row_open_overflow_has_no_successor() -> None:
    scope = _digest("reservation-scope")
    rows = [_reservation(f"row-{index}", scope=scope) for index in range(2_048)]
    receipt = _evaluate(
        AttemptReservationTransition.OPEN_IF_ABSENT,
        reservations=rows,
        scope=scope,
        fingerprint=_digest("overflow-new-capability"),
        cell_binding_digest=_digest("overflow-cell"),
        campaign_id_digest=_digest("overflow-campaign"),
        intent_digest=_digest("overflow-intent"),
    )
    assert receipt.base_reservation_record_count == 2_048
    assert receipt.source_precondition_relation_holds is True
    assert receipt.disposition is (
        AttemptReservationSuccessorDisposition.SUCCESSOR_RESOURCE_BOUNDS_REFUSED
    )
    assert receipt.reason_code is (
        AttemptReservationSuccessorReasonCode.SUCCESSOR_RECORD_LIMIT_EXCEEDED
    )
    assert receipt.successor_derivation_performed is True
    assert receipt.successor_snapshot_relation_holds is False
    assert receipt.target_reservation_state is None
    assert receipt.derived_state_evidence_digest is None
    assert receipt.successor_reservation_record_count is None
    assert receipt.successor_reservation_state_snapshot_byte_count is None
    assert receipt.successor_reservation_record_digests is None


def test_parallel_pure_open_calls_return_identical_successor_without_cas() -> None:
    scope = _digest("reservation-scope")
    snapshot = _snapshot_bytes([], scope=scope)
    policy = _policy()
    expected = _expected(snapshot, policy)
    proposal = _proposal(
        AttemptReservationTransition.OPEN_IF_ABSENT,
        scope=scope,
    )

    def evaluate_once(_: int) -> AttemptReservationSuccessorReceiptV1:
        receipt = evaluate_attempt_reservation_successor_relation(
            AttemptReservationSuccessorRequestV1(
                reservation_state_snapshot_utf8=snapshot
            ),
            expected_snapshot_digest=expected,
            proposal=proposal,
            policy=policy,
        )
        assert isinstance(receipt, AttemptReservationSuccessorReceiptV1)
        return receipt

    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(evaluate_once, range(32)))
    assert len(
        {
            receipt.successor_reservation_state_snapshot_digest
            for receipt in receipts
        }
    ) == 1
    assert all(receipt.successor_snapshot_relation_holds for receipt in receipts)
    assert all(receipt.parallel_identical_successors_possible for receipt in receipts)
    assert all(receipt.atomic_compare_and_swap_applied is False for receipt in receipts)
    assert all(receipt.reservation_written is False for receipt in receipts)


def test_successor_composes_c8e_once_with_same_exact_snapshot_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = successor_module.evaluate_attempt_reservation_cas_relation
    observed: list[bytes] = []

    def wrapped(
        request: AttemptReservationCasRequestV1 | None = None,
        **kwargs: Any,
    ) -> AttemptReservationCasReceiptV1 | None:
        assert isinstance(request, AttemptReservationCasRequestV1)
        observed.append(request.reservation_state_snapshot_utf8)
        return original(request, **kwargs)

    monkeypatch.setattr(
        successor_module,
        "evaluate_attempt_reservation_cas_relation",
        wrapped,
    )
    receipt = _evaluate(
        AttemptReservationTransition.OPEN_IF_ABSENT,
        reservations=[],
    )
    assert len(observed) == 1
    assert observed == [
        _snapshot_bytes([], scope=_digest("reservation-scope"))
    ]
    assert receipt.any_successor_uses_same_supplied_snapshot_bytes_as_precondition
    assert receipt.positive_successor_canonicalization_revalidation_required
    assert receipt.source_c8e_precondition_evaluator_invoked
    assert receipt.c8a_not_invoked
    assert receipt.c8b_not_invoked
    assert receipt.c8c_not_invoked
    assert receipt.c8d_not_invoked
    assert receipt.c7_not_invoked


def test_exact_types_deleted_slots_and_mutations_fail_closed() -> None:
    class PolicySubclass(AttemptReservationSuccessorPolicyV1):
        pass

    class RequestSubclass(AttemptReservationSuccessorRequestV1):
        pass

    with pytest.raises(AttemptReservationSuccessorContractError):
        PolicySubclass(mode=AttemptReservationSuccessorMode.STATIC_SHADOW)
    with pytest.raises(AttemptReservationSuccessorContractError):
        RequestSubclass(reservation_state_snapshot_utf8=_snapshot_bytes([]))

    scope = _digest("reservation-scope")
    snapshot = _snapshot_bytes([], scope=scope)
    policy = _policy()
    request = AttemptReservationSuccessorRequestV1(
        reservation_state_snapshot_utf8=snapshot
    )

    class BytesBomb:
        called = False

        def __bytes__(self) -> bytes:
            self.called = True
            raise AssertionError("caller conversion callback must not run")

    bomb = BytesBomb()
    object.__setattr__(request, "reservation_state_snapshot_utf8", bomb)
    with pytest.raises(AttemptReservationSuccessorContractError):
        evaluate_attempt_reservation_successor_relation(
            request,
            expected_snapshot_digest=_expected(snapshot, policy),
            proposal=_proposal(
                AttemptReservationTransition.OPEN_IF_ABSENT,
                scope=scope,
            ),
            policy=policy,
        )
    assert bomb.called is False


def test_nested_c8e_receipt_is_defensively_validated() -> None:
    receipt = _evaluate(
        AttemptReservationTransition.OPEN_IF_ABSENT,
        reservations=[],
    )
    assert receipt.source_c8e_receipt_digest == (
        receipt.source_c8e_receipt.receipt_digest
    )
    object.__setattr__(
        receipt.source_c8e_receipt,
        "receipt_digest",
        _digest("mutated-source-receipt"),
    )
    with pytest.raises(AttemptReservationSuccessorContractError) as raised:
        receipt.to_mapping()
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_receipt_is_slotted_deterministic_raw_free_and_has_no_successor_bytes() -> None:
    scope = _digest("reservation-scope")
    snapshot = _snapshot_bytes([], scope=scope)
    receipt = _evaluate(
        AttemptReservationTransition.OPEN_IF_ABSENT,
        reservations=[],
        scope=scope,
    )
    assert not hasattr(receipt, "__dict__")
    assert _SHA256.fullmatch(receipt.receipt_digest)
    public_text = repr(receipt) + json.dumps(receipt.to_mapping(), sort_keys=True)
    assert snapshot.decode("utf-8") not in public_text
    for forbidden in (
        "reservation_state_snapshot_utf8",
        "successor_snapshot_utf8",
        "stdout",
        "stderr",
        "hostname",
        "path",
        "pid",
    ):
        assert forbidden not in public_text
    assert "successor_bytes" not in receipt.to_mapping()
    assert receipt.successor_bytes_not_returned is True


def test_every_default_bool_receipt_claim_is_locked_to_true_or_false() -> None:
    receipt = _evaluate(
        AttemptReservationTransition.OPEN_IF_ABSENT,
        reservations=[],
    )
    mapping = receipt.to_mapping()
    fields = dataclasses.fields(receipt)
    true_fields = {field.name for field in fields if field.default is True}
    false_fields = {field.name for field in fields if field.default is False}
    assert true_fields == set(successor_module._TRUE_FACT_FIELDS)
    assert false_fields == set(successor_module._FALSE_CLAIM_FIELDS)
    assert all(mapping[name] is True for name in true_fields)
    assert all(mapping[name] is False for name in false_fields)
    for name in sorted(true_fields):
        with pytest.raises(AttemptReservationSuccessorContractError):
            replace(receipt, **{name: False})
    for name in sorted(false_fields):
        with pytest.raises(AttemptReservationSuccessorContractError):
            replace(receipt, **{name: True})
    required_false = {
        "source_snapshot_current",
        "source_snapshot_authoritative",
        "successor_snapshot_persisted",
        "successor_snapshot_written",
        "atomic_compare_and_swap_performed",
        "atomic_compare_and_swap_applied",
        "lock_acquired",
        "toctou_window_closed",
        "concurrent_safety_guaranteed",
        "reservation_written",
        "registry_write_requested",
        "magma_write_applied",
        "builder_host_invoked",
        "provider_invoked",
        "candidate_code_executed",
        "runtime_authority_requested",
        "generation_authorized",
    }
    assert required_false <= false_fields


def test_raw_free_successor_record_commitments_are_publicly_remintable() -> None:
    scope = _digest("reservation-scope")
    base_row = _reservation("existing", scope=scope)
    new_fingerprint = _digest("capability-new")
    receipt = _evaluate(
        AttemptReservationTransition.OPEN_IF_ABSENT,
        reservations=[base_row],
        scope=scope,
        fingerprint=new_fingerprint,
        cell_binding_digest=_digest("cell-new"),
        campaign_id_digest=_digest("campaign-new"),
        intent_digest=_digest("intent-new"),
    )
    assert receipt.successor_reservation_record_digests is not None
    proposal = receipt.source_c8e_receipt.proposal
    inserted = {
        "reservation_id": proposal.reservation_id,
        "declared_capability_fingerprint": new_fingerprint,
        "state": AttemptReservationState.RESERVED.value,
        "cell_binding_digest": proposal.cell_binding_digest,
        "campaign_id_digest": proposal.campaign_id_digest,
        "intent_digest": proposal.intent_digest,
        "state_evidence_digest": receipt.derived_state_evidence_digest,
    }
    inserted_digest = _record_digest(inserted)
    assert inserted_digest in receipt.successor_reservation_record_digests
    forged_list = list(receipt.successor_reservation_record_digests)
    existing_index = 1 - forged_list.index(inserted_digest)
    forged_list[existing_index] = _digest("self-minted-existing-record")
    forged_records = tuple(forged_list)
    forged_snapshot = _snapshot_digest_from_record_digests(
        scope=receipt.reservation_scope_digest,
        record_digests=forged_records,
    )
    reminted = _reseal(
        receipt,
        successor_reservation_record_digests=forged_records,
        successor_reservation_state_snapshot_digest=forged_snapshot,
    )
    assert reminted.successor_reservation_record_digests == forged_records
    assert reminted.successor_reservation_state_snapshot_digest == forged_snapshot
    assert reminted.receipt_origin_authenticated is False
    assert reminted.successor_snapshot_origin_authenticated is False
    assert reminted.successor_snapshot_persisted is False
    assert reminted.atomic_compare_and_swap_applied is False
    assert reminted.reservation_written is False
    assert reminted.runtime_authority_requested is False


def test_resealed_successor_record_digest_tuple_rejects_duplicates_and_overflow() -> None:
    scope = _digest("reservation-scope")
    base_row = _reservation("existing", scope=scope)
    receipt = _evaluate(
        AttemptReservationTransition.OPEN_IF_ABSENT,
        reservations=[base_row],
        scope=scope,
        policy=_policy(max_reservation_records=2),
        fingerprint=_digest("capability-new"),
        cell_binding_digest=_digest("cell-new"),
        campaign_id_digest=_digest("campaign-new"),
        intent_digest=_digest("intent-new"),
    )
    record_digests = receipt.successor_reservation_record_digests
    assert record_digests is not None and len(record_digests) == 2

    duplicate_digests = (record_digests[0], record_digests[0])
    with pytest.raises(AttemptReservationSuccessorContractError):
        _reseal(
            receipt,
            successor_reservation_record_digests=duplicate_digests,
            successor_reservation_state_snapshot_digest=(
                _snapshot_digest_from_record_digests(
                    scope=scope,
                    record_digests=duplicate_digests,
                )
            ),
        )

    over_limit_digests = (*record_digests, _digest("third-record"))
    plausible_three_row_bytes = len(
        _snapshot_bytes(
            [
                _reservation("size-a", scope=scope),
                _reservation("size-b", scope=scope),
                _reservation("size-c", scope=scope),
            ],
            scope=scope,
        )
    )
    with pytest.raises(AttemptReservationSuccessorContractError):
        _reseal(
            receipt,
            successor_reservation_record_count=3,
            successor_reservation_state_snapshot_byte_count=(
                plausible_three_row_bytes
            ),
            successor_reservation_record_digests=over_limit_digests,
            successor_reservation_state_snapshot_digest=(
                _snapshot_digest_from_record_digests(
                    scope=scope,
                    record_digests=over_limit_digests,
                )
            ),
        )


def test_deleted_receipt_slots_and_nested_mutation_are_normalized() -> None:
    receipt = _evaluate(
        AttemptReservationTransition.OPEN_IF_ABSENT,
        reservations=[],
    )
    object.__delattr__(receipt, "successor_reservation_state_snapshot_digest")
    with pytest.raises(AttemptReservationSuccessorContractError) as raised:
        receipt.to_mapping()
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_module_has_no_io_runtime_builderhost_or_store_authority_seam() -> None:
    source = inspect.getsource(successor_module)
    tree = ast.parse(source)
    imported_roots: set[str] = set()
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])
            imported_modules.add(node.module)
    assert not imported_roots & {
        "asyncio",
        "http",
        "importlib",
        "multiprocessing",
        "os",
        "pathlib",
        "requests",
        "shutil",
        "socket",
        "sqlite3",
        "subprocess",
        "tempfile",
        "threading",
        "urllib",
    }
    forbidden_calls = {"open", "exec", "eval", "compile", "__import__"}
    observed_calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not forbidden_calls & observed_calls
    forbidden_import_fragments = (
        "builder_host",
        "registry",
        "runtime",
        "sqlite",
        "store",
        "ledger",
        "understanding_coding_candidate_builder",
        "understanding_paired_runner",
    )
    assert all(
        fragment not in imported
        for fragment in forbidden_import_fragments
        for imported in imported_modules
    )
