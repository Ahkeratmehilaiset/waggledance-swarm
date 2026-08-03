"""Adversarial tests for the inert C8e reservation-CAS relation accountant.

C8e evaluates transition preconditions against one bounded, canonical,
caller-supplied reservation-state snapshot only after a separately supplied
expected digest matches.  A positive result is only a relation in supplied
bytes: it is not a durable reservation, a compare-and-swap, or authority to
generate, build, execute, route, promote, or write anything.
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

import waggledance.core.learning.understanding_attempt_reservation_cas as cas_module
from waggledance.core.learning.understanding_attempt_reservation_cas import (
    ATTEMPT_RESERVATION_CAS_ACCOUNTING_POLICY_DIGEST,
    ATTEMPT_RESERVATION_CAS_POLICY_SCHEMA,
    ATTEMPT_RESERVATION_CAS_RECEIPT_SCHEMA,
    ATTEMPT_RESERVATION_CAS_REQUEST_SCHEMA,
    ATTEMPT_RESERVATION_EXPECTED_SNAPSHOT_DIGEST_SCHEMA,
    ATTEMPT_RESERVATION_TRANSITION_PROPOSAL_SCHEMA,
    SUPPLIED_ATTEMPT_RESERVATION_STATE_SNAPSHOT_SCHEMA,
    AttemptReservationCasContractError,
    AttemptReservationCasDisposition,
    AttemptReservationCasMode,
    AttemptReservationCasPolicyV1,
    AttemptReservationCasReasonCode,
    AttemptReservationCasReceiptV1,
    AttemptReservationCasRequestV1,
    AttemptReservationExpectedSnapshotDigestV1,
    AttemptReservationState,
    AttemptReservationTransition,
    AttemptReservationTransitionProposalV1,
    derive_attempt_reservation_id,
    derive_supplied_attempt_reservation_state_snapshot_digest,
    evaluate_attempt_reservation_cas_relation,
)
from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest


_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


def _digest(label: str) -> str:
    return sha256_digest({"test_label": label})


def _policy(**changes: Any) -> AttemptReservationCasPolicyV1:
    return AttemptReservationCasPolicyV1(
        mode=AttemptReservationCasMode.STATIC_SHADOW,
        **changes,
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
    reservation_id: str | None = None,
) -> dict[str, str]:
    selected_scope = scope or _digest("reservation-scope")
    selected_fingerprint = fingerprint or _digest(f"capability-{label}")
    return {
        "reservation_id": reservation_id
        or derive_attempt_reservation_id(
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


def _reservation_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item["declared_capability_fingerprint"],
        item["reservation_id"],
        item["campaign_id_digest"],
        item["cell_binding_digest"],
        item["intent_digest"],
        item["state"],
        item["state_evidence_digest"],
    )


def _snapshot_mapping(
    reservations: list[dict[str, Any]] | None = None,
    *,
    scope: str | None = None,
) -> dict[str, Any]:
    selected_scope = scope or _digest("reservation-scope")
    selected = (
        [_reservation(scope=selected_scope)]
        if reservations is None
        else reservations
    )
    return {
        "schema_version": SUPPLIED_ATTEMPT_RESERVATION_STATE_SNAPSHOT_SCHEMA,
        "reservation_scope_digest": selected_scope,
        "reservations": sorted(selected, key=_reservation_sort_key),
    }


def _snapshot_bytes(
    reservations: list[dict[str, Any]] | None = None,
    *,
    scope: str | None = None,
) -> bytes:
    return canonical_json_bytes(_snapshot_mapping(reservations, scope=scope))


def _structural_snapshot_digest_without_identity_validation(
    snapshot_utf8: bytes,
) -> str:
    mapping = json.loads(snapshot_utf8.decode("utf-8"))
    record_digests = [
        sha256_digest(
            {
                "domain": (
                    "wd.understanding.supplied_attempt_reservation_"
                    "state_record.digest.v1"
                ),
                **record,
            }
        )
        for record in mapping["reservations"]
    ]
    return sha256_digest(
        {
            "domain": (
                "wd.understanding.supplied_attempt_reservation_state_"
                "snapshot.digest.v1"
            ),
            "schema_version": mapping["schema_version"],
            "reservation_scope_digest": mapping["reservation_scope_digest"],
            "reservation_record_digests": record_digests,
        }
    )


def _expected(
    snapshot_utf8: bytes,
    policy: AttemptReservationCasPolicyV1,
    *,
    digest: str | None = None,
) -> AttemptReservationExpectedSnapshotDigestV1:
    return AttemptReservationExpectedSnapshotDigestV1(
        expected_reservation_state_snapshot_digest=(
            digest
            if digest is not None
            else derive_supplied_attempt_reservation_state_snapshot_digest(
                snapshot_utf8,
                policy,
            )
        )
    )


def _request(snapshot_utf8: bytes) -> AttemptReservationCasRequestV1:
    return AttemptReservationCasRequestV1(
        reservation_state_snapshot_utf8=snapshot_utf8,
    )


def _proposal(
    transition: AttemptReservationTransition,
    *,
    scope: str | None = None,
    fingerprint: str | None = None,
    reservation_id: str | None = None,
    cell_binding_digest: str | None = None,
    campaign_id_digest: str | None = None,
    intent_digest: str | None = None,
    transition_evidence_digest: str | None = None,
) -> AttemptReservationTransitionProposalV1:
    selected_scope = scope or _digest("reservation-scope")
    selected_fingerprint = fingerprint or _digest("capability-one")
    return AttemptReservationTransitionProposalV1(
        transition=transition,
        reservation_id=reservation_id
        or derive_attempt_reservation_id(
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
    fingerprint: str | None = None,
    expected_digest: str | None = None,
    reservation_id: str | None = None,
    cell_binding_digest: str | None = None,
    campaign_id_digest: str | None = None,
    intent_digest: str | None = None,
) -> AttemptReservationCasReceiptV1:
    selected_scope = scope or _digest("reservation-scope")
    snapshot = _snapshot_bytes(reservations, scope=selected_scope)
    policy = _policy()
    receipt = evaluate_attempt_reservation_cas_relation(
        _request(snapshot),
        expected_snapshot_digest=_expected(
            snapshot,
            policy,
            digest=expected_digest,
        ),
        proposal=_proposal(
            transition,
            scope=selected_scope,
            fingerprint=fingerprint,
            reservation_id=reservation_id,
            cell_binding_digest=cell_binding_digest,
            campaign_id_digest=campaign_id_digest,
            intent_digest=intent_digest,
        ),
        policy=policy,
    )
    assert isinstance(receipt, AttemptReservationCasReceiptV1)
    return receipt


def _normalize_mapping_value(value: Any) -> Any:
    if hasattr(value, "to_mapping"):
        return value.to_mapping()
    if hasattr(value, "value"):
        return value.value
    return value


def _reseal_receipt(
    receipt: AttemptReservationCasReceiptV1,
    **changes: Any,
) -> AttemptReservationCasReceiptV1:
    constructor = {
        field.name: getattr(receipt, field.name)
        for field in dataclasses.fields(receipt)
    }
    constructor.update(changes)
    core = receipt.to_mapping()
    core.pop("receipt_digest")
    for name, value in changes.items():
        if name != "receipt_digest":
            core[name] = _normalize_mapping_value(value)
    constructor["receipt_digest"] = sha256_digest(
        {
            "domain": (
                "wd.understanding.attempt_reservation_cas_receipt.digest.v1"
            ),
            **core,
        }
    )
    return AttemptReservationCasReceiptV1(**constructor)


class _Bomb:
    def __getattribute__(self, name: str) -> Any:
        raise AssertionError(f"OFF mode inspected hostile attribute {name}")


def test_public_schemas_enums_and_policy_digest_are_exact() -> None:
    schemas = {
        SUPPLIED_ATTEMPT_RESERVATION_STATE_SNAPSHOT_SCHEMA,
        ATTEMPT_RESERVATION_EXPECTED_SNAPSHOT_DIGEST_SCHEMA,
        ATTEMPT_RESERVATION_CAS_POLICY_SCHEMA,
        ATTEMPT_RESERVATION_CAS_REQUEST_SCHEMA,
        ATTEMPT_RESERVATION_TRANSITION_PROPOSAL_SCHEMA,
        ATTEMPT_RESERVATION_CAS_RECEIPT_SCHEMA,
    }
    assert len(schemas) == 6
    assert all(type(value) is str and value.startswith("wd.") for value in schemas)
    assert {item.value for item in AttemptReservationCasMode} == {
        "off",
        "static_shadow",
    }
    assert {item.value for item in AttemptReservationState} == {
        "reserved",
        "committed",
        "aborted",
    }
    assert {item.value for item in AttemptReservationTransition} == {
        "open_if_absent",
        "commit_if_reserved",
        "abort_if_reserved",
    }
    assert {item.value for item in AttemptReservationCasDisposition} == {
        "refused",
        "open_if_absent_precondition_relation_holds_in_supplied_reservation_state_snapshot",
        "open_if_absent_precondition_relation_does_not_hold_in_supplied_reservation_state_snapshot",
        "commit_if_reserved_precondition_relation_holds_in_supplied_reservation_state_snapshot",
        "commit_if_reserved_precondition_relation_does_not_hold_in_supplied_reservation_state_snapshot",
        "abort_if_reserved_precondition_relation_holds_in_supplied_reservation_state_snapshot",
        "abort_if_reserved_precondition_relation_does_not_hold_in_supplied_reservation_state_snapshot",
    }
    assert {item.value for item in AttemptReservationCasReasonCode} == {
        "expected_reservation_state_snapshot_digest_mismatch",
        "capability_and_reservation_id_absent_in_supplied_snapshot",
        "capability_already_reserved_in_supplied_snapshot",
        "capability_already_committed_in_supplied_snapshot",
        "capability_already_aborted_in_supplied_snapshot",
        "reservation_id_absent_in_supplied_snapshot",
        "reservation_binding_digest_mismatch",
        "exact_reserved_relation_holds_for_commit",
        "exact_reserved_relation_holds_for_abort",
        "reservation_already_committed_in_supplied_snapshot",
        "reservation_already_aborted_in_supplied_snapshot",
        "reservation_id_bound_to_different_declared_capability",
    }
    combined = " ".join(item.value for item in AttemptReservationCasDisposition)
    for forbidden in (
        "succeeded",
        "applied",
        "durable",
        "authorized",
        "acquired",
        "granted",
    ):
        assert forbidden not in combined
    assert _SHA256.fullmatch(ATTEMPT_RESERVATION_CAS_ACCOUNTING_POLICY_DIGEST)


def test_accounting_policy_binds_sort_identity_uniqueness_and_no_cas() -> None:
    accounting_policy = cas_module._ACCOUNTING_POLICY
    assert tuple(accounting_policy["reservation_record_sort_key"]) == (
        "declared_capability_fingerprint",
        "reservation_id",
        "campaign_id_digest",
        "cell_binding_digest",
        "intent_digest",
        "state",
        "state_evidence_digest",
    )
    assert tuple(accounting_policy["reservation_identity_inputs"]) == (
        "reservation_scope_digest",
        "declared_capability_fingerprint",
    )
    assert accounting_policy["duplicate_declared_capability_rule"] == (
        "refused"
    )
    assert accounting_policy["duplicate_reservation_id_rule"] == "refused"
    assert accounting_policy["duplicate_state_evidence_digest_rule"] == (
        "allowed_non_key_metadata"
    )
    assert accounting_policy["atomic_cas_performed"] is False
    assert sha256_digest(accounting_policy) == (
        ATTEMPT_RESERVATION_CAS_ACCOUNTING_POLICY_DIGEST
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
def test_policy_is_exact_typed_default_off_and_absolutely_bounded(
    changes: dict[str, Any],
) -> None:
    values: dict[str, Any] = {
        "mode": AttemptReservationCasMode.STATIC_SHADOW,
        "max_snapshot_bytes": 2_097_152,
        "max_reservation_records": 2_048,
        "max_json_depth": 6,
        "max_json_nodes": 32_768,
    }
    values.update(changes)
    with pytest.raises(AttemptReservationCasContractError):
        AttemptReservationCasPolicyV1(**values)


def test_default_off_precedes_all_input_inspection() -> None:
    assert AttemptReservationCasPolicyV1().mode is AttemptReservationCasMode.OFF
    assert evaluate_attempt_reservation_cas_relation.__kwdefaults__["policy"] is None
    assert evaluate_attempt_reservation_cas_relation() is None
    assert evaluate_attempt_reservation_cas_relation(
        _Bomb(),  # type: ignore[arg-type]
        expected_snapshot_digest=_Bomb(),  # type: ignore[arg-type]
        proposal=_Bomb(),  # type: ignore[arg-type]
    ) is None
    assert evaluate_attempt_reservation_cas_relation(
        _Bomb(),  # type: ignore[arg-type]
        expected_snapshot_digest=_Bomb(),  # type: ignore[arg-type]
        proposal=_Bomb(),  # type: ignore[arg-type]
        policy=AttemptReservationCasPolicyV1(mode=AttemptReservationCasMode.OFF),
    ) is None


@pytest.mark.parametrize("deleted_field", ("mode", "max_json_nodes"))
@pytest.mark.parametrize("operation", ("evaluate", "derive"))
def test_deleted_policy_slots_are_normalized_to_contract_errors(
    deleted_field: str,
    operation: str,
) -> None:
    snapshot = _snapshot_bytes([])
    intact_policy = _policy()
    policy = _policy()
    object.__delattr__(policy, deleted_field)
    with pytest.raises(AttemptReservationCasContractError):
        if operation == "derive":
            derive_supplied_attempt_reservation_state_snapshot_digest(
                snapshot,
                policy,
            )
        else:
            evaluate_attempt_reservation_cas_relation(
                _request(snapshot),
                expected_snapshot_digest=_expected(snapshot, intact_policy),
                proposal=_proposal(AttemptReservationTransition.OPEN_IF_ABSENT),
                policy=policy,
            )


@pytest.mark.parametrize(
    ("kind", "deleted_field"),
    (
        ("request", "reservation_state_snapshot_utf8"),
        ("expected", "expected_reservation_state_snapshot_digest"),
        ("proposal", "reservation_id"),
    ),
)
def test_deleted_input_slots_are_normalized_to_contract_errors(
    kind: str,
    deleted_field: str,
) -> None:
    snapshot = _snapshot_bytes([])
    policy = _policy()
    request = _request(snapshot)
    expected = _expected(snapshot, policy)
    proposal = _proposal(AttemptReservationTransition.OPEN_IF_ABSENT)
    target = {"request": request, "expected": expected, "proposal": proposal}[kind]
    object.__delattr__(target, deleted_field)
    with pytest.raises(AttemptReservationCasContractError):
        evaluate_attempt_reservation_cas_relation(
            request,
            expected_snapshot_digest=expected,
            proposal=proposal,
            policy=policy,
        )


@pytest.mark.parametrize(
    ("kind", "deleted_field"),
    (
        ("policy", "accounting_policy_digest"),
        ("expected", "target_snapshot_schema_version"),
        ("proposal", "intent_digest"),
        ("receipt", "disposition"),
    ),
)
def test_deleted_slots_in_public_mappings_are_chain_free_contract_errors(
    kind: str,
    deleted_field: str,
) -> None:
    objects: dict[str, Any] = {
        "policy": _policy(),
        "expected": AttemptReservationExpectedSnapshotDigestV1(
            expected_reservation_state_snapshot_digest=_digest("snapshot")
        ),
        "proposal": _proposal(
            AttemptReservationTransition.OPEN_IF_ABSENT
        ),
        "receipt": _evaluate(
            AttemptReservationTransition.OPEN_IF_ABSENT,
            reservations=[],
        ),
    }
    target = objects[kind]
    object.__delattr__(target, deleted_field)
    with pytest.raises(AttemptReservationCasContractError) as captured:
        target.to_mapping()
    assert captured.value.__context__ is None

    digest_property = {
        "policy": "policy_digest",
        "expected": "expectation_digest",
        "proposal": "proposal_digest",
    }.get(kind)
    if digest_property is not None:
        with pytest.raises(AttemptReservationCasContractError) as captured:
            getattr(target, digest_property)
        assert captured.value.__context__ is None


def test_stable_reservation_id_is_scope_and_capability_only() -> None:
    signature = inspect.signature(derive_attempt_reservation_id)
    assert tuple(signature.parameters) == (
        "reservation_scope_digest",
        "declared_capability_fingerprint",
    )
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in signature.parameters.values()
    )
    scope = _digest("scope")
    capability = _digest("capability")
    first = derive_attempt_reservation_id(
        reservation_scope_digest=scope,
        declared_capability_fingerprint=capability,
    )
    second = derive_attempt_reservation_id(
        reservation_scope_digest=scope,
        declared_capability_fingerprint=capability,
    )
    assert first == second
    assert _SHA256.fullmatch(first)
    assert derive_attempt_reservation_id(
        reservation_scope_digest=_digest("other-scope"),
        declared_capability_fingerprint=capability,
    ) != first
    assert derive_attempt_reservation_id(
        reservation_scope_digest=scope,
        declared_capability_fingerprint=_digest("other-capability"),
    ) != first
    for forbidden in (
        "cell",
        "campaign",
        "intent",
        "state",
        "evidence",
        "lease",
        "fence",
    ):
        assert forbidden not in signature.parameters


@pytest.mark.parametrize(
    ("scope", "fingerprint"),
    (
        ("not-a-digest", _digest("capability")),
        (_digest("scope"), "sha256:abc"),
        (True, _digest("capability")),
        (_digest("scope"), object()),
    ),
)
def test_stable_reservation_id_rejects_noncanonical_inputs(
    scope: Any,
    fingerprint: Any,
) -> None:
    with pytest.raises(AttemptReservationCasContractError):
        derive_attempt_reservation_id(
            reservation_scope_digest=scope,
            declared_capability_fingerprint=fingerprint,
        )


def test_snapshot_digest_is_deterministic_and_binds_every_field() -> None:
    policy = _policy()
    scope = _digest("reservation-scope")
    row = _reservation(scope=scope)
    baseline_bytes = _snapshot_bytes([row], scope=scope)
    baseline = derive_supplied_attempt_reservation_state_snapshot_digest(
        baseline_bytes,
        policy,
    )
    assert _SHA256.fullmatch(baseline)
    assert derive_supplied_attempt_reservation_state_snapshot_digest(
        baseline_bytes,
        policy,
    ) == baseline
    variants = (
        _snapshot_bytes([], scope=scope),
        _snapshot_bytes([], scope=_digest("other-scope")),
        _snapshot_bytes([{**row, "state": "committed"}], scope=scope),
        _snapshot_bytes(
            [{**row, "cell_binding_digest": _digest("other-cell")}],
            scope=scope,
        ),
        _snapshot_bytes(
            [{**row, "campaign_id_digest": _digest("other-campaign")}],
            scope=scope,
        ),
        _snapshot_bytes(
            [{**row, "intent_digest": _digest("other-intent")}],
            scope=scope,
        ),
        _snapshot_bytes(
            [{**row, "state_evidence_digest": _digest("other-evidence")}],
            scope=scope,
        ),
    )
    assert all(
        derive_supplied_attempt_reservation_state_snapshot_digest(item, policy)
        != baseline
        for item in variants
    )


@pytest.mark.parametrize("bad", (b"", b"not-json", b"\xff", b"[]", b"null"))
def test_snapshot_rejects_empty_invalid_utf8_or_non_object_json(bad: bytes) -> None:
    with pytest.raises(AttemptReservationCasContractError):
        derive_supplied_attempt_reservation_state_snapshot_digest(bad, _policy())


@pytest.mark.parametrize(
    "bad",
    (
        b'{"canary":"RAW-JSON-EXCEPTION-CANARY"',
        b"RAW-UTF8-EXCEPTION-CANARY-\xff",
    ),
)
def test_snapshot_parse_errors_are_raw_free_and_chain_free(bad: bytes) -> None:
    with pytest.raises(AttemptReservationCasContractError) as raised:
        derive_supplied_attempt_reservation_state_snapshot_digest(bad, _policy())
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert "CANARY" not in str(raised.value)


def test_json_parser_recursion_limit_fails_closed_without_exception_chain() -> None:
    deeply_nested = (
        b'{"nested":' + (b"[" * 100_000) + b"0" + (b"]" * 100_000) + b"}"
    )
    assert len(deeply_nested) < _policy().max_snapshot_bytes
    with pytest.raises(AttemptReservationCasContractError) as raised:
        derive_supplied_attempt_reservation_state_snapshot_digest(
            deeply_nested,
            _policy(),
        )
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_snapshot_requires_canonical_json_and_rejects_duplicate_keys() -> None:
    mapping = _snapshot_mapping([])
    noncanonical = json.dumps(mapping, indent=2, sort_keys=True).encode("utf-8")
    with pytest.raises(AttemptReservationCasContractError):
        derive_supplied_attempt_reservation_state_snapshot_digest(
            noncanonical,
            _policy(),
        )
    canonical_text = canonical_json_bytes(mapping).decode("utf-8")
    duplicate = canonical_text.replace(
        '"reservations":',
        '"reservations":[],"reservations":',
        1,
    ).encode("utf-8")
    with pytest.raises(AttemptReservationCasContractError):
        derive_supplied_attempt_reservation_state_snapshot_digest(
            duplicate,
            _policy(),
        )


@pytest.mark.parametrize(
    "mutation",
    (
        lambda value: {
            key: item for key, item in value.items() if key != "reservation_scope_digest"
        },
        lambda value: {**value, "extra": False},
        lambda value: {**value, "schema_version": "wd.invalid.v1"},
        lambda value: {**value, "reservation_scope_digest": "not-a-digest"},
        lambda value: {**value, "reservations": "not-a-list"},
    ),
)
def test_snapshot_root_shape_and_fields_are_exact(mutation: Any) -> None:
    bad = canonical_json_bytes(mutation(_snapshot_mapping([])))
    with pytest.raises(AttemptReservationCasContractError):
        derive_supplied_attempt_reservation_state_snapshot_digest(bad, _policy())


@pytest.mark.parametrize(
    "mutation",
    (
        lambda value: {key: item for key, item in value.items() if key != "reservation_id"},
        lambda value: {**value, "extra": False},
        lambda value: {**value, "reservation_id": "not-a-digest"},
        lambda value: {**value, "declared_capability_fingerprint": "sha256:abc"},
        lambda value: {**value, "state": "open"},
        lambda value: {**value, "state": True},
        lambda value: {**value, "cell_binding_digest": None},
        lambda value: {**value, "campaign_id_digest": "not-a-digest"},
        lambda value: {**value, "intent_digest": object()},
        lambda value: {**value, "state_evidence_digest": "sha256:abc"},
        lambda value: {**value, "lease_expires_at": "later"},
        lambda value: {**value, "fence_token": 1},
        lambda value: {**value, "sequence": 1},
        lambda value: {**value, "successor": _digest("successor")},
    ),
)
def test_reservation_row_shape_and_fields_are_exact(mutation: Any) -> None:
    scope = _digest("reservation-scope")
    row = mutation(_reservation(scope=scope))
    with pytest.raises((AttemptReservationCasContractError, TypeError)):
        derive_supplied_attempt_reservation_state_snapshot_digest(
            canonical_json_bytes(
                {
                    "schema_version": (
                        SUPPLIED_ATTEMPT_RESERVATION_STATE_SNAPSHOT_SCHEMA
                    ),
                    "reservation_scope_digest": scope,
                    "reservations": [row],
                }
            ),
            _policy(),
        )


def test_public_snapshot_digest_validates_every_row_id_derivation() -> None:
    scope = _digest("reservation-scope")
    row = _reservation(scope=scope)
    forged = {**row, "reservation_id": _digest("forged-id")}
    with pytest.raises(AttemptReservationCasContractError):
        derive_supplied_attempt_reservation_state_snapshot_digest(
            _snapshot_bytes([forged], scope=scope),
            _policy(),
        )


def test_rows_require_full_sort_and_independent_subject_and_id_uniqueness() -> None:
    scope = _digest("reservation-scope")
    first = _reservation("first", scope=scope)
    second = _reservation("second", scope=scope)
    ordered = sorted([first, second], key=_reservation_sort_key)
    reversed_bytes = canonical_json_bytes(
        {
            "schema_version": SUPPLIED_ATTEMPT_RESERVATION_STATE_SNAPSHOT_SCHEMA,
            "reservation_scope_digest": scope,
            "reservations": list(reversed(ordered)),
        }
    )
    with pytest.raises(AttemptReservationCasContractError):
        derive_supplied_attempt_reservation_state_snapshot_digest(
            reversed_bytes,
            _policy(),
        )

    duplicate_capability = {
        **first,
        "reservation_id": derive_attempt_reservation_id(
            reservation_scope_digest=scope,
            declared_capability_fingerprint=first["declared_capability_fingerprint"],
        ),
        "cell_binding_digest": _digest("different-cell"),
    }
    with pytest.raises(AttemptReservationCasContractError):
        derive_supplied_attempt_reservation_state_snapshot_digest(
            _snapshot_bytes([first, duplicate_capability], scope=scope),
            _policy(),
        )

    duplicate_id = {
        **second,
        "reservation_id": first["reservation_id"],
    }
    with pytest.raises(AttemptReservationCasContractError):
        derive_supplied_attempt_reservation_state_snapshot_digest(
            _snapshot_bytes([first, duplicate_id], scope=scope),
            _policy(),
        )


def test_duplicate_state_evidence_is_explicitly_allowed() -> None:
    scope = _digest("reservation-scope")
    evidence = _digest("shared-state-evidence")
    rows = [
        _reservation("first", scope=scope, state_evidence_digest=evidence),
        _reservation("second", scope=scope, state_evidence_digest=evidence),
    ]
    digest = derive_supplied_attempt_reservation_state_snapshot_digest(
        _snapshot_bytes(rows, scope=scope),
        _policy(),
    )
    assert _SHA256.fullmatch(digest)


def test_snapshot_resource_bounds_cover_zero_one_2048_and_reject_2049() -> None:
    scope = _digest("reservation-scope")
    zero = _snapshot_bytes([], scope=scope)
    one = _snapshot_bytes([_reservation(scope=scope)], scope=scope)
    assert _SHA256.fullmatch(
        derive_supplied_attempt_reservation_state_snapshot_digest(zero, _policy())
    )
    assert _SHA256.fullmatch(
        derive_supplied_attempt_reservation_state_snapshot_digest(one, _policy())
    )

    rows = [_reservation(f"row-{index}", scope=scope) for index in range(2_049)]
    at_limit = _snapshot_bytes(rows[:2_048], scope=scope)
    assert len(at_limit) < 2_097_152
    assert _SHA256.fullmatch(
        derive_supplied_attempt_reservation_state_snapshot_digest(
            at_limit,
            _policy(),
        )
    )
    with pytest.raises(AttemptReservationCasContractError):
        derive_supplied_attempt_reservation_state_snapshot_digest(
            _snapshot_bytes(rows, scope=scope),
            _policy(),
        )

    with pytest.raises(AttemptReservationCasContractError):
        derive_supplied_attempt_reservation_state_snapshot_digest(
            b"x" * 2_097_153,
            _policy(),
        )


def test_configured_byte_record_depth_and_node_limits_fail_closed() -> None:
    scope = _digest("reservation-scope")
    empty = _snapshot_bytes([], scope=scope)
    one = _snapshot_bytes([_reservation(scope=scope)], scope=scope)
    assert _SHA256.fullmatch(
        derive_supplied_attempt_reservation_state_snapshot_digest(
            empty,
            _policy(
                max_snapshot_bytes=len(empty),
                max_reservation_records=0,
                max_json_depth=2,
                max_json_nodes=4,
            ),
        )
    )
    policies = (
        _policy(max_snapshot_bytes=len(one) - 1),
        _policy(max_reservation_records=0),
        _policy(max_json_depth=3),
        _policy(max_json_nodes=11),
    )
    for policy in policies:
        with pytest.raises(AttemptReservationCasContractError):
            derive_supplied_attempt_reservation_state_snapshot_digest(one, policy)


def test_snapshot_requires_exact_immutable_bytes() -> None:
    snapshot = _snapshot_bytes([])
    for value in (bytearray(snapshot), memoryview(snapshot), snapshot.decode("utf-8")):
        with pytest.raises(AttemptReservationCasContractError):
            derive_supplied_attempt_reservation_state_snapshot_digest(
                value,  # type: ignore[arg-type]
                _policy(),
            )


def test_expected_request_and_proposal_are_separate_exact_objects() -> None:
    snapshot = _snapshot_bytes([])
    policy = _policy()
    expected = _expected(snapshot, policy)
    request = _request(snapshot)
    proposal = _proposal(AttemptReservationTransition.OPEN_IF_ABSENT)
    assert set(expected.to_mapping()) == {
        "schema_version",
        "target_snapshot_schema_version",
        "expected_reservation_state_snapshot_digest",
    }
    assert set(dataclasses.asdict(request)) == {
        "reservation_state_snapshot_utf8",
        "schema_version",
    }
    assert set(proposal.to_mapping()) == {
        "schema_version",
        "transition",
        "reservation_id",
        "declared_capability_fingerprint",
        "cell_binding_digest",
        "campaign_id_digest",
        "intent_digest",
        "transition_evidence_digest",
    }
    signature = inspect.signature(evaluate_attempt_reservation_cas_relation)
    assert signature.parameters["expected_snapshot_digest"].kind is (
        inspect.Parameter.KEYWORD_ONLY
    )
    assert signature.parameters["proposal"].kind is inspect.Parameter.KEYWORD_ONLY


@pytest.mark.parametrize(
    "factory",
    (
        lambda: AttemptReservationExpectedSnapshotDigestV1(
            expected_reservation_state_snapshot_digest="not-a-digest"
        ),
        lambda: AttemptReservationCasRequestV1(
            reservation_state_snapshot_utf8=bytearray(_snapshot_bytes([]))
        ),
        lambda: AttemptReservationTransitionProposalV1(
            transition="open_if_absent",
            reservation_id=_digest("reservation"),
            declared_capability_fingerprint=_digest("capability"),
            cell_binding_digest=_digest("cell"),
            campaign_id_digest=_digest("campaign"),
            intent_digest=_digest("intent"),
            transition_evidence_digest=_digest("evidence"),
        ),
    ),
)
def test_input_objects_reject_malformed_exact_types(factory: Any) -> None:
    with pytest.raises(AttemptReservationCasContractError):
        factory()


def test_static_shadow_requires_all_three_exact_inputs() -> None:
    snapshot = _snapshot_bytes([])
    policy = _policy()
    expected = _expected(snapshot, policy)
    proposal = _proposal(AttemptReservationTransition.OPEN_IF_ABSENT)
    calls = (
        (None, expected, proposal),
        (_request(snapshot), None, proposal),
        (_request(snapshot), expected, None),
        ({"reservation_state_snapshot_utf8": snapshot}, expected, proposal),
    )
    for request, selected_expected, selected_proposal in calls:
        with pytest.raises(AttemptReservationCasContractError):
            evaluate_attempt_reservation_cas_relation(
                request,  # type: ignore[arg-type]
                expected_snapshot_digest=selected_expected,
                proposal=selected_proposal,
                policy=policy,
            )


def test_expected_mismatch_dominates_semantic_evaluation_not_structure() -> None:
    receipt = _evaluate(
        AttemptReservationTransition.OPEN_IF_ABSENT,
        reservations=[],
        expected_digest=_digest("wrong-snapshot"),
    )
    assert receipt.disposition is AttemptReservationCasDisposition.REFUSED
    assert receipt.reason_code.value == (
        "expected_reservation_state_snapshot_digest_mismatch"
    )
    assert receipt.expected_snapshot_digest_matches is False
    for flag in (
        "row_identity_derivation_performed",
        "proposal_identity_derivation_performed",
        "subject_lookup_performed",
        "binding_check_performed",
        "state_check_performed",
    ):
        assert getattr(receipt, flag) is False
    for field in (
        "supplied_precondition_relation_holds",
        "observed_reservation_state",
        "matched_reservation_record_digest",
    ):
        assert getattr(receipt, field) is None

    malformed = canonical_json_bytes({"malformed": True})
    with pytest.raises(AttemptReservationCasContractError):
        evaluate_attempt_reservation_cas_relation(
            _request(malformed),
            expected_snapshot_digest=AttemptReservationExpectedSnapshotDigestV1(
                expected_reservation_state_snapshot_digest=_digest("wrong-snapshot")
            ),
            proposal=_proposal(AttemptReservationTransition.OPEN_IF_ABSENT),
            policy=_policy(),
        )

    scope = _digest("reservation-scope")
    valid_snapshot = _snapshot_bytes([], scope=scope)
    policy = _policy()
    forged_proposal = _proposal(
        AttemptReservationTransition.OPEN_IF_ABSENT,
        scope=scope,
        reservation_id=_digest("forged-proposal-id"),
    )
    dominated = evaluate_attempt_reservation_cas_relation(
        _request(valid_snapshot),
        expected_snapshot_digest=_expected(
            valid_snapshot,
            policy,
            digest=_digest("wrong-snapshot"),
        ),
        proposal=forged_proposal,
        policy=policy,
    )
    assert isinstance(dominated, AttemptReservationCasReceiptV1)
    assert dominated.disposition is AttemptReservationCasDisposition.REFUSED
    assert dominated.proposal_identity_derivation_performed is False

    row = _reservation(scope=scope)
    malformed_identity = _snapshot_bytes(
        [{**row, "reservation_id": _digest("forged-row-id")}],
        scope=scope,
    )
    dominated_row_identity = evaluate_attempt_reservation_cas_relation(
        _request(malformed_identity),
        expected_snapshot_digest=AttemptReservationExpectedSnapshotDigestV1(
            expected_reservation_state_snapshot_digest=_digest("wrong-snapshot")
        ),
        proposal=_proposal(
            AttemptReservationTransition.OPEN_IF_ABSENT,
            scope=scope,
        ),
        policy=policy,
    )
    assert isinstance(dominated_row_identity, AttemptReservationCasReceiptV1)
    assert dominated_row_identity.disposition is AttemptReservationCasDisposition.REFUSED
    assert dominated_row_identity.row_identity_derivation_performed is False

    matching_structural_digest = (
        _structural_snapshot_digest_without_identity_validation(
            malformed_identity
        )
    )
    with pytest.raises(AttemptReservationCasContractError):
        evaluate_attempt_reservation_cas_relation(
            _request(malformed_identity),
            expected_snapshot_digest=AttemptReservationExpectedSnapshotDigestV1(
                expected_reservation_state_snapshot_digest=(
                    matching_structural_digest
                )
            ),
            proposal=_proposal(
                AttemptReservationTransition.OPEN_IF_ABSENT,
                scope=scope,
            ),
            policy=policy,
        )


def test_matching_expectation_then_proposal_id_mismatch_is_contract_error() -> None:
    with pytest.raises(AttemptReservationCasContractError):
        _evaluate(
            AttemptReservationTransition.OPEN_IF_ABSENT,
            reservations=[],
            reservation_id=_digest("forged-proposal-id"),
        )


def test_open_transition_holds_only_when_subject_is_absent() -> None:
    absent = _evaluate(
        AttemptReservationTransition.OPEN_IF_ABSENT,
        reservations=[],
    )
    assert absent.disposition is (
        AttemptReservationCasDisposition.OPEN_IF_ABSENT_PRECONDITION_RELATION_HOLDS_IN_SUPPLIED_RESERVATION_STATE_SNAPSHOT
    )
    assert absent.reason_code is (
        AttemptReservationCasReasonCode.CAPABILITY_AND_RESERVATION_ID_ABSENT_IN_SUPPLIED_SNAPSHOT
    )
    assert absent.expected_snapshot_digest_matches is True
    assert absent.row_identity_derivation_performed is True
    assert absent.proposal_identity_derivation_performed is True
    assert absent.subject_lookup_performed is True
    assert absent.binding_check_performed is False
    assert absent.state_check_performed is False
    assert absent.supplied_precondition_relation_holds is True
    assert absent.matched_reservation_record_digest is None
    assert absent.observed_reservation_state is None
    assert absent.atomic_compare_and_swap_applied is False

    scope = _digest("reservation-scope")
    for state in AttemptReservationState:
        present = _evaluate(
            AttemptReservationTransition.OPEN_IF_ABSENT,
            reservations=[_reservation(scope=scope, state=state)],
            scope=scope,
        )
        assert present.disposition is (
            AttemptReservationCasDisposition.OPEN_IF_ABSENT_PRECONDITION_RELATION_DOES_NOT_HOLD_IN_SUPPLIED_RESERVATION_STATE_SNAPSHOT
        )
        assert present.reason_code is {
            AttemptReservationState.RESERVED: (
                AttemptReservationCasReasonCode.CAPABILITY_ALREADY_RESERVED_IN_SUPPLIED_SNAPSHOT
            ),
            AttemptReservationState.COMMITTED: (
                AttemptReservationCasReasonCode.CAPABILITY_ALREADY_COMMITTED_IN_SUPPLIED_SNAPSHOT
            ),
            AttemptReservationState.ABORTED: (
                AttemptReservationCasReasonCode.CAPABILITY_ALREADY_ABORTED_IN_SUPPLIED_SNAPSHOT
            ),
        }[state]
        assert present.supplied_precondition_relation_holds is False
        assert _SHA256.fullmatch(present.matched_reservation_record_digest or "")
        assert present.binding_check_performed is False
        assert present.state_check_performed is True
        assert present.observed_reservation_state is state


@pytest.mark.parametrize(
    ("transition", "holds_disposition", "does_not_hold_disposition"),
    (
        (
            AttemptReservationTransition.COMMIT_IF_RESERVED,
            AttemptReservationCasDisposition.COMMIT_IF_RESERVED_PRECONDITION_RELATION_HOLDS_IN_SUPPLIED_RESERVATION_STATE_SNAPSHOT,
            AttemptReservationCasDisposition.COMMIT_IF_RESERVED_PRECONDITION_RELATION_DOES_NOT_HOLD_IN_SUPPLIED_RESERVATION_STATE_SNAPSHOT,
        ),
        (
            AttemptReservationTransition.ABORT_IF_RESERVED,
            AttemptReservationCasDisposition.ABORT_IF_RESERVED_PRECONDITION_RELATION_HOLDS_IN_SUPPLIED_RESERVATION_STATE_SNAPSHOT,
            AttemptReservationCasDisposition.ABORT_IF_RESERVED_PRECONDITION_RELATION_DOES_NOT_HOLD_IN_SUPPLIED_RESERVATION_STATE_SNAPSHOT,
        ),
    ),
)
def test_terminal_transition_requires_reserved_exact_binding(
    transition: AttemptReservationTransition,
    holds_disposition: AttemptReservationCasDisposition,
    does_not_hold_disposition: AttemptReservationCasDisposition,
) -> None:
    scope = _digest("reservation-scope")
    reserved = _reservation(scope=scope, state=AttemptReservationState.RESERVED)
    holds = _evaluate(
        transition,
        reservations=[reserved],
        scope=scope,
    )
    assert holds.disposition is holds_disposition
    assert holds.reason_code is {
        AttemptReservationTransition.COMMIT_IF_RESERVED: (
            AttemptReservationCasReasonCode.EXACT_RESERVED_RELATION_HOLDS_FOR_COMMIT
        ),
        AttemptReservationTransition.ABORT_IF_RESERVED: (
            AttemptReservationCasReasonCode.EXACT_RESERVED_RELATION_HOLDS_FOR_ABORT
        ),
    }[transition]
    assert holds.supplied_precondition_relation_holds is True
    assert _SHA256.fullmatch(holds.matched_reservation_record_digest or "")
    assert holds.binding_check_performed is True
    assert holds.state_check_performed is True
    assert holds.observed_reservation_state is AttemptReservationState.RESERVED

    absent = _evaluate(transition, reservations=[], scope=scope)
    assert absent.disposition is does_not_hold_disposition
    assert absent.reason_code is (
        AttemptReservationCasReasonCode.RESERVATION_ID_ABSENT_IN_SUPPLIED_SNAPSHOT
    )
    assert absent.supplied_precondition_relation_holds is False
    assert absent.matched_reservation_record_digest is None
    assert absent.binding_check_performed is False
    assert absent.state_check_performed is False
    assert absent.observed_reservation_state is None

    for terminal_state in (
        AttemptReservationState.COMMITTED,
        AttemptReservationState.ABORTED,
    ):
        terminal = _evaluate(
            transition,
            reservations=[_reservation(scope=scope, state=terminal_state)],
            scope=scope,
        )
        assert terminal.disposition is does_not_hold_disposition
        assert terminal.reason_code is {
            AttemptReservationState.COMMITTED: (
                AttemptReservationCasReasonCode.RESERVATION_ALREADY_COMMITTED_IN_SUPPLIED_SNAPSHOT
            ),
            AttemptReservationState.ABORTED: (
                AttemptReservationCasReasonCode.RESERVATION_ALREADY_ABORTED_IN_SUPPLIED_SNAPSHOT
            ),
        }[terminal_state]
        assert terminal.supplied_precondition_relation_holds is False
        assert terminal.binding_check_performed is True
        assert terminal.state_check_performed is True
        assert terminal.observed_reservation_state is terminal_state


@pytest.mark.parametrize(
    ("field", "wrong"),
    (
        ("cell_binding_digest", _digest("wrong-cell")),
        ("campaign_id_digest", _digest("wrong-campaign")),
        ("intent_digest", _digest("wrong-intent")),
    ),
)
@pytest.mark.parametrize(
    "transition",
    (
        AttemptReservationTransition.COMMIT_IF_RESERVED,
        AttemptReservationTransition.ABORT_IF_RESERVED,
    ),
)
def test_binding_mismatch_is_refused_before_state_comparison(
    field: str,
    wrong: str,
    transition: AttemptReservationTransition,
) -> None:
    scope = _digest("reservation-scope")
    row = _reservation(scope=scope, state=AttemptReservationState.COMMITTED)
    changes = {field: wrong}
    receipt = _evaluate(
        transition,
        reservations=[row],
        scope=scope,
        **changes,
    )
    assert receipt.disposition is AttemptReservationCasDisposition.REFUSED
    assert receipt.reason_code.value == "reservation_binding_digest_mismatch"
    assert receipt.binding_check_performed is True
    assert receipt.state_check_performed is False
    assert receipt.supplied_precondition_relation_holds is None
    assert receipt.observed_reservation_state is None


def test_metadata_cannot_change_stable_id_or_reopen_terminal_subject() -> None:
    scope = _digest("reservation-scope")
    fingerprint = _digest("capability-one")
    row = _reservation(
        scope=scope,
        fingerprint=fingerprint,
        state=AttemptReservationState.COMMITTED,
    )
    stable_id = row["reservation_id"]
    for label in ("a", "b", "c"):
        receipt = _evaluate(
            AttemptReservationTransition.OPEN_IF_ABSENT,
            reservations=[row],
            scope=scope,
            fingerprint=fingerprint,
            reservation_id=stable_id,
            cell_binding_digest=_digest(f"cell-{label}"),
            campaign_id_digest=_digest(f"campaign-{label}"),
            intent_digest=_digest(f"intent-{label}"),
        )
        assert receipt.disposition is (
            AttemptReservationCasDisposition.OPEN_IF_ABSENT_PRECONDITION_RELATION_DOES_NOT_HOLD_IN_SUPPLIED_RESERVATION_STATE_SNAPSHOT
        )
        assert receipt.observed_reservation_state is AttemptReservationState.COMMITTED


def test_two_parallel_open_evaluations_both_hold_and_prove_no_cas() -> None:
    scope = _digest("reservation-scope")
    snapshot = _snapshot_bytes([], scope=scope)
    policy = _policy()
    expected = _expected(snapshot, policy)
    proposal = _proposal(
        AttemptReservationTransition.OPEN_IF_ABSENT,
        scope=scope,
    )

    def evaluate_once(_: int) -> AttemptReservationCasReceiptV1:
        receipt = evaluate_attempt_reservation_cas_relation(
            _request(snapshot),
            expected_snapshot_digest=expected,
            proposal=proposal,
            policy=policy,
        )
        assert isinstance(receipt, AttemptReservationCasReceiptV1)
        return receipt

    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(evaluate_once, range(32)))
    assert all(
        receipt.disposition
        is AttemptReservationCasDisposition.OPEN_IF_ABSENT_PRECONDITION_RELATION_HOLDS_IN_SUPPLIED_RESERVATION_STATE_SNAPSHOT
        for receipt in receipts
    )
    assert len({receipt.receipt_digest for receipt in receipts}) == 1
    assert all(receipt.atomic_compare_and_swap_applied is False for receipt in receipts)
    assert all(receipt.reservation_written is False for receipt in receipts)


def test_exact_types_subclasses_and_equality_callbacks_are_rejected() -> None:
    class PolicySubclass(AttemptReservationCasPolicyV1):
        pass

    class ExpectedSubclass(AttemptReservationExpectedSnapshotDigestV1):
        pass

    class RequestSubclass(AttemptReservationCasRequestV1):
        pass

    class ProposalSubclass(AttemptReservationTransitionProposalV1):
        pass

    class EqualityBomb(str):
        called = False

        def __eq__(self, other: object) -> bool:
            self.called = True
            raise AssertionError("caller equality callback must not run")

    constructors = (
        lambda: PolicySubclass(mode=AttemptReservationCasMode.STATIC_SHADOW),
        lambda: ExpectedSubclass(
            expected_reservation_state_snapshot_digest=_digest("snapshot")
        ),
        lambda: RequestSubclass(reservation_state_snapshot_utf8=_snapshot_bytes([])),
        lambda: ProposalSubclass(
            transition=AttemptReservationTransition.OPEN_IF_ABSENT,
            reservation_id=_digest("reservation"),
            declared_capability_fingerprint=_digest("capability"),
            cell_binding_digest=_digest("cell"),
            campaign_id_digest=_digest("campaign"),
            intent_digest=_digest("intent"),
            transition_evidence_digest=_digest("evidence"),
        ),
    )
    for constructor in constructors:
        with pytest.raises(AttemptReservationCasContractError):
            constructor()

    bomb = EqualityBomb(_digest("snapshot"))
    with pytest.raises(AttemptReservationCasContractError):
        AttemptReservationExpectedSnapshotDigestV1(
            expected_reservation_state_snapshot_digest=bomb,
        )
    assert bomb.called is False


def test_postconstruction_mutations_are_revalidated_without_callbacks() -> None:
    snapshot = _snapshot_bytes([])
    policy = _policy()
    request = _request(snapshot)
    expected = _expected(snapshot, policy)
    proposal = _proposal(AttemptReservationTransition.OPEN_IF_ABSENT)
    object.__setattr__(expected, "expected_reservation_state_snapshot_digest", object())
    with pytest.raises(AttemptReservationCasContractError):
        evaluate_attempt_reservation_cas_relation(
            request,
            expected_snapshot_digest=expected,
            proposal=proposal,
            policy=policy,
        )

    class BytesBomb:
        called = False

        def __bytes__(self) -> bytes:
            self.called = True
            raise AssertionError("bytes callback must not run")

    bomb = BytesBomb()
    object.__setattr__(request, "reservation_state_snapshot_utf8", bomb)
    with pytest.raises(AttemptReservationCasContractError):
        evaluate_attempt_reservation_cas_relation(
            request,
            expected_snapshot_digest=_expected(snapshot, policy),
            proposal=proposal,
            policy=policy,
        )
    assert bomb.called is False


def test_evaluation_defensively_copies_expected_and_proposal_objects() -> None:
    scope = _digest("reservation-scope")
    snapshot = _snapshot_bytes([], scope=scope)
    policy = _policy()
    expected = _expected(snapshot, policy)
    proposal = _proposal(
        AttemptReservationTransition.OPEN_IF_ABSENT,
        scope=scope,
    )
    expected_value = expected.expected_reservation_state_snapshot_digest
    proposal_value = proposal.transition_evidence_digest
    receipt = evaluate_attempt_reservation_cas_relation(
        _request(snapshot),
        expected_snapshot_digest=expected,
        proposal=proposal,
        policy=policy,
    )
    assert isinstance(receipt, AttemptReservationCasReceiptV1)
    assert receipt.expected_snapshot_digest is not expected
    assert receipt.proposal is not proposal

    object.__setattr__(
        expected,
        "expected_reservation_state_snapshot_digest",
        _digest("later-expected-mutation"),
    )
    object.__setattr__(
        proposal,
        "transition_evidence_digest",
        _digest("later-proposal-mutation"),
    )
    assert (
        receipt.expected_snapshot_digest.expected_reservation_state_snapshot_digest
        == expected_value
    )
    assert receipt.proposal.transition_evidence_digest == proposal_value
    assert receipt.to_mapping()["receipt_digest"] == receipt.receipt_digest


def test_contract_objects_are_slotted_and_receipt_is_raw_free() -> None:
    raw_canary = "RAW-RESERVATION-CANARY-DO-NOT-EMIT"
    scope = _digest("reservation-scope")
    snapshot = _snapshot_bytes([], scope=scope)
    policy = _policy()
    objects = (
        policy,
        _expected(snapshot, policy),
        _request(snapshot),
        _proposal(AttemptReservationTransition.OPEN_IF_ABSENT, scope=scope),
        _evaluate(
            AttemptReservationTransition.OPEN_IF_ABSENT,
            reservations=[],
            scope=scope,
        ),
    )
    assert all(not hasattr(item, "__dict__") for item in objects)

    proposal = _proposal(
        AttemptReservationTransition.OPEN_IF_ABSENT,
        scope=scope,
        transition_evidence_digest=sha256_digest({"canary": raw_canary}),
    )
    receipt = evaluate_attempt_reservation_cas_relation(
        _request(snapshot),
        expected_snapshot_digest=_expected(snapshot, policy),
        proposal=proposal,
        policy=policy,
    )
    assert isinstance(receipt, AttemptReservationCasReceiptV1)
    public_text = repr(receipt) + json.dumps(receipt.to_mapping(), sort_keys=True)
    assert raw_canary not in public_text
    assert snapshot.decode("utf-8") not in public_text
    for forbidden in (
        "reservation_state_snapshot_utf8",
        "stdout",
        "stderr",
        "hostname",
        "path",
        "pid",
    ):
        assert forbidden not in public_text
    assert "successor" not in receipt.to_mapping()
    assert receipt.no_successor_snapshot_returned is True


def test_receipt_hard_claims_are_literal_and_resist_replace() -> None:
    receipt = _evaluate(
        AttemptReservationTransition.OPEN_IF_ABSENT,
        reservations=[],
    )
    mapping = receipt.to_mapping()
    true_fields = set(cas_module._TRUE_RECEIPT_FIELDS)
    false_fields = set(cas_module._FALSE_RECEIPT_FIELDS)
    assert true_fields
    assert false_fields
    receipt_dataclass_fields = dataclasses.fields(receipt)
    assert true_fields == {
        field.name for field in receipt_dataclass_fields if field.default is True
    }
    assert false_fields == {
        field.name for field in receipt_dataclass_fields if field.default is False
    }
    assert all(mapping[name] is True for name in true_fields)
    assert all(mapping[name] is False for name in false_fields)
    receipt_fields = {field.name for field in receipt_dataclass_fields}
    for name in sorted(true_fields & receipt_fields):
        with pytest.raises(AttemptReservationCasContractError):
            replace(receipt, **{name: False})
    for name in sorted(false_fields & receipt_fields):
        with pytest.raises(AttemptReservationCasContractError):
            replace(receipt, **{name: True})
    required_false = {
        "durable_reservation_store_consulted",
        "atomic_compare_and_swap_performed",
        "atomic_compare_and_swap_applied",
        "durable_compare_and_swap_applied",
        "reservation_written",
        "reservation_state_written",
        "candidate_transition_persisted",
        "concurrent_safety_guaranteed",
        "atomicity_verified",
        "linearizability_verified",
        "anti_replay_enforced",
        "lease_granted",
        "lease_or_expiry_enforced",
        "fence_token_verified",
        "toctou_window_closed",
        "execution_resume_authorized",
        "recovery_or_resume_authorized",
        "generation_authorized",
        "builder_host_invoked",
        "provider_invoked",
        "candidate_code_executed",
        "registry_write_requested",
        "magma_write_applied",
        "runtime_authority_requested",
    }
    assert required_false <= false_fields


def test_zero_row_receipt_rejects_matched_row_outcomes() -> None:
    receipt = _evaluate(
        AttemptReservationTransition.OPEN_IF_ABSENT,
        reservations=[],
    )
    with pytest.raises(AttemptReservationCasContractError):
        _reseal_receipt(
            receipt,
            state_check_performed=True,
            supplied_precondition_relation_holds=False,
            matched_reservation_record_digest=_digest("impossible-record"),
            observed_reservation_state=AttemptReservationState.RESERVED,
            disposition=(
                AttemptReservationCasDisposition.OPEN_IF_ABSENT_PRECONDITION_RELATION_DOES_NOT_HOLD_IN_SUPPLIED_RESERVATION_STATE_SNAPSHOT
            ),
            reason_code=(
                AttemptReservationCasReasonCode.CAPABILITY_ALREADY_RESERVED_IN_SUPPLIED_SNAPSHOT
            ),
        )


def test_zero_row_receipt_requires_unique_empty_snapshot_digest() -> None:
    receipt = _evaluate(
        AttemptReservationTransition.OPEN_IF_ABSENT,
        reservations=[],
    )
    forged_snapshot_digest = _digest("forged-empty-snapshot")
    forged_expectation = AttemptReservationExpectedSnapshotDigestV1(
        expected_reservation_state_snapshot_digest=forged_snapshot_digest
    )
    forged_request_digest = cas_module._derive_request_digest(
        policy_digest=receipt.policy_digest,
        accounting_policy_digest=receipt.accounting_policy_digest,
        expectation_digest=forged_expectation.expectation_digest,
        proposal_digest=receipt.proposal_digest,
        reservation_state_snapshot_digest=forged_snapshot_digest,
        reservation_scope_digest=receipt.reservation_scope_digest,
    )
    with pytest.raises(AttemptReservationCasContractError):
        _reseal_receipt(
            receipt,
            expected_snapshot_digest=forged_expectation,
            expectation_digest=forged_expectation.expectation_digest,
            request_digest=forged_request_digest,
            reservation_state_snapshot_digest=forged_snapshot_digest,
        )


def test_raw_free_receipt_is_publicly_remintable_but_never_authoritative() -> None:
    receipt = _evaluate(
        AttemptReservationTransition.OPEN_IF_ABSENT,
        reservations=[],
    )
    scope = receipt.reservation_scope_digest
    plausible_present_bytes = len(
        _snapshot_bytes([_reservation(scope=scope)], scope=scope)
    )
    reminted = _reseal_receipt(
        receipt,
        reservation_record_count=1,
        reservation_state_snapshot_byte_count=plausible_present_bytes,
        state_check_performed=True,
        supplied_precondition_relation_holds=False,
        matched_reservation_record_digest=_digest("self-minted-record"),
        observed_reservation_state=AttemptReservationState.RESERVED,
        disposition=(
            AttemptReservationCasDisposition.OPEN_IF_ABSENT_PRECONDITION_RELATION_DOES_NOT_HOLD_IN_SUPPLIED_RESERVATION_STATE_SNAPSHOT
        ),
        reason_code=(
            AttemptReservationCasReasonCode.CAPABILITY_ALREADY_RESERVED_IN_SUPPLIED_SNAPSHOT
        ),
    )
    # Raw rows are deliberately omitted, so the public constructor cannot
    # rederive the count, bytes, lookup, or state relation.  It can be resealed
    # from an empty-snapshot receipt into a shaped "present" relation.  That is
    # why neither receipt is authenticated, authoritative, or a CAS result.
    assert receipt.reservation_record_count == 0
    assert reminted.reservation_record_count == 1
    assert reminted.reservation_state_snapshot_byte_count == plausible_present_bytes
    assert receipt.supplied_precondition_relation_holds is True
    assert reminted.supplied_precondition_relation_holds is False
    assert reminted.observed_reservation_state is AttemptReservationState.RESERVED
    assert reminted.receipt_origin_authenticated is False
    assert reminted.reservation_snapshot_origin_authenticated is False
    assert reminted.atomic_compare_and_swap_applied is False
    assert reminted.reservation_written is False
    assert reminted.generation_authorized is False


def test_nested_expectation_mutation_invalidates_receipt_mapping() -> None:
    receipt = _evaluate(
        AttemptReservationTransition.OPEN_IF_ABSENT,
        reservations=[],
    )
    object.__setattr__(
        receipt.expected_snapshot_digest,
        "expected_reservation_state_snapshot_digest",
        _digest("nested-mutation"),
    )
    with pytest.raises(AttemptReservationCasContractError):
        receipt.to_mapping()

    receipt = _evaluate(
        AttemptReservationTransition.OPEN_IF_ABSENT,
        reservations=[],
    )
    object.__setattr__(
        receipt.proposal,
        "transition_evidence_digest",
        _digest("nested-proposal-mutation"),
    )
    with pytest.raises(AttemptReservationCasContractError):
        receipt.to_mapping()


def test_malformed_digest_errors_do_not_echo_raw_input() -> None:
    raw_canary = "raw-secret-digest-value-do-not-echo"
    with pytest.raises(AttemptReservationCasContractError) as captured:
        AttemptReservationExpectedSnapshotDigestV1(
            expected_reservation_state_snapshot_digest=raw_canary,
        )
    assert raw_canary not in str(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_module_has_no_forbidden_import_io_or_upstream_invocation_seam() -> None:
    source = inspect.getsource(cas_module)
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
        "understanding_declared_attempt_snapshot",
        "understanding_gap_family_snapshot",
        "understanding_gap_family_snapshot_pin",
        "understanding_coding_candidate_builder",
        "understanding_paired_runner",
        "builder_host",
        "registry",
        "runtime",
    )
    assert all(
        fragment not in module_name
        for fragment in forbidden_import_fragments
        for module_name in imported_modules
    )
