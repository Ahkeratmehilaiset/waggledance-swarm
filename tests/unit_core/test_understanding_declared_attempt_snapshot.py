"""Adversarial tests for the inert C8d declared-attempt accountant.

C8d reports exact declared-capability fingerprint occurrence in one bounded,
canonical, caller-supplied snapshot only after a separately caller-supplied
expected digest matches.  Neither input is authenticated or externally pinned.
The result is not attempt history, chronology, deduplication, a reservation, or
authority to generate, build, execute, route, promote, or write anything.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
import re
from dataclasses import replace
from typing import Any

import pytest

import waggledance.core.learning.understanding_declared_attempt_snapshot as attempt_module
from waggledance.core.learning.understanding_declared_attempt_snapshot import (
    DECLARED_ATTEMPT_ACCOUNTING_POLICY_DIGEST,
    SUPPLIED_DECLARED_ATTEMPT_SNAPSHOT_SCHEMA,
    DeclaredAttemptExpectedSnapshotDigestV1,
    DeclaredAttemptSnapshotContractError,
    DeclaredAttemptSnapshotDisposition,
    DeclaredAttemptSnapshotMode,
    DeclaredAttemptSnapshotPolicyV1,
    DeclaredAttemptSnapshotReasonCode,
    DeclaredAttemptSnapshotReceiptV1,
    DeclaredAttemptSnapshotRequestV1,
    derive_supplied_declared_attempt_snapshot_digest,
    evaluate_declared_attempt_snapshot,
)
from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest


_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")

_HARD_TRUE_RECEIPT_FIELDS = {
    "evaluation_only",
    "shadow_only",
    "static_accounting_only",
    "raw_material_omitted",
    "canonical_caller_supplied_attempt_snapshot_only",
    "canonical_snapshot_digest_recomputed_from_supplied_bytes",
    "separate_expected_snapshot_digest_required",
    "expected_snapshot_digest_keyword_only",
    "caller_supplied_expected_snapshot_digest_only",
    "exact_expected_snapshot_digest_comparison_only",
    "exact_declared_capability_fingerprint_comparison_only",
    "comparison_subject_is_declared_capability_fingerprint_only",
    "snapshot_local_match_count_only",
    "campaign_id_not_a_match_key",
    "cell_binding_digest_not_a_match_key",
    "attempt_record_id_not_a_match_key",
    "attempt_evidence_digest_not_a_match_key",
    "expected_mismatch_refused_without_match_claim",
    "ambiguous_multi_match_refused_without_selection",
    "c8a_not_invoked",
    "c8b_not_invoked",
    "c8c_not_invoked",
    "c7_not_invoked",
    "no_side_effects_in_module",
}

_HARD_FALSE_RECEIPT_FIELDS = {
    "expected_snapshot_externally_pinned",
    "expected_snapshot_digest_independently_configured",
    "expected_snapshot_digest_origin_authenticated",
    "expected_snapshot_digest_precommit_verified",
    "attempt_snapshot_origin_authenticated",
    "attempt_entry_origin_authenticated",
    "durable_attempt_history_consulted",
    "attempt_history_authoritative",
    "attempt_history_complete",
    "attempt_history_fresh",
    "attempt_history_chronology_verified",
    "attempt_history_monotonic",
    "attempt_history_rollback_protected",
    "attempt_history_fork_resolved",
    "anti_replay_enforced",
    "attempt_occurrence_independently_verified",
    "attempt_execution_verified",
    "attempt_outcome_verified",
    "retry_prevented",
    "state_transition_validated",
    "cross_campaign_single_attempt_enforced",
    "cross_cell_single_attempt_enforced",
    "global_single_attempt_enforced",
    "atomic_attempt_reservation_applied",
    "semantic_equivalence_verified",
    "semantic_deduplication_verified",
    "global_deduplication_verified",
    "reuse_eligibility_claimed",
    "build_eligibility_claimed",
    "generation_authorized",
    "family_novelty_independently_verified",
    "new_family_need_independently_verified",
    "existing_family_deduplication_independently_verified",
    "catalog_completeness_verified",
    "catalog_freshness_verified",
    "catalog_authenticity_verified",
    "registry_snapshot_identity_independently_verified",
    "family_review_status_independently_verified",
    "receipt_origin_authenticated",
    "independent_verification_applied",
    "genesis_origin_independently_verified",
    "hex_cell_binding_independently_verified",
    "echo_chamber_absence_verified",
    "scalability_50000_demonstrated",
    "provider_invoked",
    "builder_host_invoked",
    "c8b_invoked",
    "c8c_invoked",
    "c8a_invoked",
    "c7_execution_requested",
    "candidate_code_executed",
    "candidate_tests_executed",
    "subprocess_spawned",
    "network_accessed",
    "os_sandbox_applied",
    "registry_read_applied",
    "registry_write_requested",
    "magma_write_applied",
    "hive_commit_applied",
    "routing_influence_requested",
    "solver_promotion_requested",
    "runtime_authority_requested",
    "product_external_system_writes_requested",
}


def _digest(label: str) -> str:
    return sha256_digest({"test_label": label})


def _policy(**changes: Any) -> DeclaredAttemptSnapshotPolicyV1:
    return DeclaredAttemptSnapshotPolicyV1(
        mode=DeclaredAttemptSnapshotMode.STATIC_SHADOW,
        **changes,
    )


def _attempt(
    attempt_record_id: str = "attempt-c8d-1",
    *,
    fingerprint: str | None = None,
    campaign_id_digest: str | None = None,
    cell_binding_digest: str | None = None,
    attempt_evidence_digest: str | None = None,
) -> dict[str, str]:
    return {
        "attempt_record_id": attempt_record_id,
        "declared_capability_fingerprint": (
            fingerprint or _digest("declared-capability")
        ),
        "campaign_id_digest": (
            campaign_id_digest or _digest(f"campaign-{attempt_record_id}")
        ),
        "cell_binding_digest": (
            cell_binding_digest or _digest(f"cell-{attempt_record_id}")
        ),
        "attempt_evidence_digest": (
            attempt_evidence_digest or _digest(f"evidence-{attempt_record_id}")
        ),
    }


def _attempt_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item["declared_capability_fingerprint"],
        item["campaign_id_digest"],
        item["cell_binding_digest"],
        item["attempt_record_id"],
        item["attempt_evidence_digest"],
    )


def _snapshot_mapping(
    attempts: list[dict[str, Any]] | None = None,
    *,
    snapshot_id: str = "attempt-snapshot-c8d-1",
    attempt_history_scope_digest: str | None = None,
) -> dict[str, Any]:
    selected = [_attempt()] if attempts is None else attempts
    return {
        "schema_version": SUPPLIED_DECLARED_ATTEMPT_SNAPSHOT_SCHEMA,
        "snapshot_id": snapshot_id,
        "attempt_history_scope_digest": (
            attempt_history_scope_digest or _digest("attempt-history-scope")
        ),
        "attempts": sorted(selected, key=_attempt_sort_key),
    }


def _snapshot_bytes(
    attempts: list[dict[str, Any]] | None = None,
    **changes: Any,
) -> bytes:
    return canonical_json_bytes(_snapshot_mapping(attempts, **changes))


def _expected(
    snapshot_utf8: bytes,
    policy: DeclaredAttemptSnapshotPolicyV1,
    *,
    digest: str | None = None,
) -> DeclaredAttemptExpectedSnapshotDigestV1:
    return DeclaredAttemptExpectedSnapshotDigestV1(
        expected_attempt_snapshot_digest=(
            digest
            if digest is not None
            else derive_supplied_declared_attempt_snapshot_digest(
                snapshot_utf8,
                policy,
            )
        )
    )


def _request(
    *,
    fingerprint: str | None = None,
    snapshot_utf8: bytes | None = None,
) -> DeclaredAttemptSnapshotRequestV1:
    return DeclaredAttemptSnapshotRequestV1(
        declared_capability_fingerprint=(
            fingerprint or _digest("declared-capability")
        ),
        attempt_snapshot_utf8=(
            snapshot_utf8 if snapshot_utf8 is not None else _snapshot_bytes()
        ),
    )


def _evaluate(
    *,
    fingerprint: str | None = None,
    snapshot_utf8: bytes | None = None,
    expected_digest: str | None = None,
) -> DeclaredAttemptSnapshotReceiptV1:
    selected_snapshot = snapshot_utf8 if snapshot_utf8 is not None else _snapshot_bytes()
    policy = _policy()
    receipt = evaluate_declared_attempt_snapshot(
        _request(fingerprint=fingerprint, snapshot_utf8=selected_snapshot),
        expected_snapshot_digest=_expected(
            selected_snapshot,
            policy,
            digest=expected_digest,
        ),
        policy=policy,
    )
    assert isinstance(receipt, DeclaredAttemptSnapshotReceiptV1)
    return receipt


def _normalize_mapping_value(value: Any) -> Any:
    if hasattr(value, "to_mapping"):
        return value.to_mapping()
    if hasattr(value, "value"):
        return value.value
    return value


def _reseal_receipt(
    receipt: DeclaredAttemptSnapshotReceiptV1,
    **changes: Any,
) -> DeclaredAttemptSnapshotReceiptV1:
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
                "wd.understanding.declared_attempt_snapshot_receipt.digest.v1"
            ),
            **core,
        }
    )
    return DeclaredAttemptSnapshotReceiptV1(**constructor)


class _Bomb:
    def __getattribute__(self, name: str) -> Any:
        raise AssertionError(f"OFF mode inspected hostile attribute {name}")


def test_public_enum_tokens_are_exact_and_non_authorizing() -> None:
    assert {item.value for item in DeclaredAttemptSnapshotMode} == {
        "off",
        "static_shadow",
    }
    assert {item.value for item in DeclaredAttemptSnapshotDisposition} == {
        "refused",
        "exactly_one_declared_capability_match_in_supplied_attempt_snapshot",
        "no_exact_declared_capability_match_in_supplied_attempt_snapshot",
    }
    assert {item.value for item in DeclaredAttemptSnapshotReasonCode} == {
        "expected_attempt_snapshot_digest_mismatch",
        "ambiguous_multiple_exact_declared_capability_matches",
        "exactly_one_exact_declared_capability_match",
        "no_exact_declared_capability_match",
    }
    combined = " ".join(
        item.value for item in DeclaredAttemptSnapshotDisposition
    )
    for forbidden in (
        "history",
        "prior",
        "already",
        "novel",
        "reuse",
        "eligible",
        "authorized",
        "deduplicated",
    ):
        assert forbidden not in combined
    assert _SHA256.fullmatch(DECLARED_ATTEMPT_ACCOUNTING_POLICY_DIGEST)


def test_accounting_policy_binds_exact_subject_sort_and_ambiguity_rules() -> None:
    accounting_policy = attempt_module._ACCOUNTING_POLICY
    assert accounting_policy["comparison_subject"] == (
        "declared_capability_fingerprint_only"
    )
    assert tuple(accounting_policy["attempt_record_sort_key"]) == (
        "declared_capability_fingerprint",
        "campaign_id_digest",
        "cell_binding_digest",
        "attempt_record_id",
        "attempt_evidence_digest",
    )
    assert accounting_policy["duplicate_attempt_record_id_rule"] == "refused"
    assert accounting_policy["duplicate_attempt_evidence_digest_rule"] == "refused"
    assert accounting_policy["ambiguous_subject_match_rule"] == (
        "refused_without_selection"
    )
    assert sha256_digest(accounting_policy) == DECLARED_ATTEMPT_ACCOUNTING_POLICY_DIGEST


@pytest.mark.parametrize(
    "changes",
    (
        {"mode": "static_shadow"},
        {"max_snapshot_bytes": True},
        {"max_snapshot_bytes": 127},
        {"max_snapshot_bytes": 2_097_153},
        {"max_attempt_records": -1},
        {"max_attempt_records": 4_097},
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
        "mode": DeclaredAttemptSnapshotMode.STATIC_SHADOW,
        "max_snapshot_bytes": 2_097_152,
        "max_attempt_records": 4_096,
        "max_json_depth": 6,
        "max_json_nodes": 32_768,
    }
    values.update(changes)
    with pytest.raises(DeclaredAttemptSnapshotContractError):
        DeclaredAttemptSnapshotPolicyV1(**values)


def test_default_off_precedes_request_expectation_and_mutable_default_inspection() -> None:
    assert DeclaredAttemptSnapshotPolicyV1().mode is DeclaredAttemptSnapshotMode.OFF
    assert evaluate_declared_attempt_snapshot.__kwdefaults__["policy"] is None
    assert evaluate_declared_attempt_snapshot() is None
    assert evaluate_declared_attempt_snapshot(
        _Bomb(),  # type: ignore[arg-type]
        expected_snapshot_digest=_Bomb(),  # type: ignore[arg-type]
    ) is None
    assert evaluate_declared_attempt_snapshot(
        _Bomb(),  # type: ignore[arg-type]
        expected_snapshot_digest=_Bomb(),  # type: ignore[arg-type]
        policy=DeclaredAttemptSnapshotPolicyV1(
            mode=DeclaredAttemptSnapshotMode.OFF
        ),
    ) is None

    missing_snapshot_request = _request()
    object.__delattr__(missing_snapshot_request, "attempt_snapshot_utf8")
    assert evaluate_declared_attempt_snapshot(
        missing_snapshot_request,
        expected_snapshot_digest=_Bomb(),  # type: ignore[arg-type]
    ) is None


@pytest.mark.parametrize("deleted_field", ("mode", "max_json_nodes"))
@pytest.mark.parametrize("operation", ("evaluate", "derive"))
def test_deleted_policy_slots_are_normalized_to_contract_errors(
    deleted_field: str,
    operation: str,
) -> None:
    snapshot = _snapshot_bytes()
    intact_policy = _policy()
    expected = _expected(snapshot, intact_policy)
    policy = _policy()
    object.__delattr__(policy, deleted_field)

    with pytest.raises(DeclaredAttemptSnapshotContractError):
        if operation == "evaluate":
            evaluate_declared_attempt_snapshot(
                _request(snapshot_utf8=snapshot),
                expected_snapshot_digest=expected,
                policy=policy,
            )
        else:
            derive_supplied_declared_attempt_snapshot_digest(snapshot, policy)


def test_deleted_request_snapshot_slot_is_normalized_to_contract_error() -> None:
    snapshot = _snapshot_bytes()
    policy = _policy()
    request = _request(snapshot_utf8=snapshot)
    expected = _expected(snapshot, policy)
    object.__delattr__(request, "attempt_snapshot_utf8")

    with pytest.raises(DeclaredAttemptSnapshotContractError):
        evaluate_declared_attempt_snapshot(
            request,
            expected_snapshot_digest=expected,
            policy=policy,
        )


def test_snapshot_digest_is_deterministic_and_binds_every_role() -> None:
    policy = _policy()
    baseline_bytes = _snapshot_bytes()
    baseline = derive_supplied_declared_attempt_snapshot_digest(
        baseline_bytes,
        policy,
    )
    assert _SHA256.fullmatch(baseline)
    assert derive_supplied_declared_attempt_snapshot_digest(
        baseline_bytes,
        policy,
    ) == baseline

    base = _attempt()
    variants = (
        _snapshot_bytes(snapshot_id="attempt-snapshot-c8d-2"),
        _snapshot_bytes(attempt_history_scope_digest=_digest("other-scope")),
        _snapshot_bytes([{**base, "attempt_record_id": "attempt-c8d-2"}]),
        _snapshot_bytes(
            [{**base, "declared_capability_fingerprint": _digest("other-capability")}]
        ),
        _snapshot_bytes([{**base, "campaign_id_digest": _digest("other-campaign")}]),
        _snapshot_bytes([{**base, "cell_binding_digest": _digest("other-cell")}]),
        _snapshot_bytes(
            [{**base, "attempt_evidence_digest": _digest("other-evidence")}]
        ),
    )
    assert all(
        derive_supplied_declared_attempt_snapshot_digest(item, policy) != baseline
        for item in variants
    )


@pytest.mark.parametrize(
    "bad_snapshot",
    (b"", b"not-json", b"\xff", b"[]", b"null"),
)
def test_snapshot_rejects_empty_invalid_utf8_or_non_object_json(
    bad_snapshot: bytes,
) -> None:
    with pytest.raises(DeclaredAttemptSnapshotContractError):
        derive_supplied_declared_attempt_snapshot_digest(bad_snapshot, _policy())


@pytest.mark.parametrize(
    "bad_snapshot",
    (
        b'{"canary":"RAW-JSON-EXCEPTION-CANARY"',
        b"RAW-UTF8-EXCEPTION-CANARY-\xff",
    ),
)
def test_snapshot_parse_errors_are_raw_free_and_chain_free(
    bad_snapshot: bytes,
) -> None:
    with pytest.raises(DeclaredAttemptSnapshotContractError) as raised:
        derive_supplied_declared_attempt_snapshot_digest(bad_snapshot, _policy())
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert "CANARY" not in str(raised.value)


def test_json_parser_recursion_limit_fails_closed_as_contract_error() -> None:
    deeply_nested = (
        b'{"nested":'
        + (b"[" * 100_000)
        + b"0"
        + (b"]" * 100_000)
        + b"}"
    )
    assert len(deeply_nested) < _policy().max_snapshot_bytes
    with pytest.raises(DeclaredAttemptSnapshotContractError) as raised:
        derive_supplied_declared_attempt_snapshot_digest(deeply_nested, _policy())
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_snapshot_requires_canonical_json_and_rejects_duplicate_keys() -> None:
    mapping = _snapshot_mapping()
    noncanonical = json.dumps(mapping, indent=2, sort_keys=True).encode("utf-8")
    with pytest.raises(DeclaredAttemptSnapshotContractError):
        derive_supplied_declared_attempt_snapshot_digest(noncanonical, _policy())

    canonical_text = canonical_json_bytes(mapping).decode("utf-8")
    duplicate = canonical_text.replace(
        '"attempts":',
        '"attempts":[],"attempts":',
        1,
    ).encode("utf-8")
    with pytest.raises(DeclaredAttemptSnapshotContractError):
        derive_supplied_declared_attempt_snapshot_digest(duplicate, _policy())

    nonfinite = canonical_text.replace(
        '"attempt-snapshot-c8d-1"',
        "NaN",
        1,
    ).encode("utf-8")
    with pytest.raises(DeclaredAttemptSnapshotContractError):
        derive_supplied_declared_attempt_snapshot_digest(nonfinite, _policy())


@pytest.mark.parametrize(
    "mutation",
    (
        lambda value: {
            key: item for key, item in value.items() if key != "snapshot_id"
        },
        lambda value: {**value, "extra": False},
        lambda value: {**value, "schema_version": "wd.invalid.v1"},
        lambda value: {**value, "snapshot_id": ""},
        lambda value: {**value, "attempt_history_scope_digest": "not-a-digest"},
        lambda value: {**value, "attempts": "not-a-list"},
    ),
)
def test_snapshot_root_shape_and_fields_are_exact(mutation: Any) -> None:
    bad = canonical_json_bytes(mutation(_snapshot_mapping()))
    with pytest.raises(DeclaredAttemptSnapshotContractError):
        derive_supplied_declared_attempt_snapshot_digest(bad, _policy())


@pytest.mark.parametrize(
    "mutation",
    (
        lambda value: {
            key: item for key, item in value.items() if key != "attempt_record_id"
        },
        lambda value: {**value, "extra": False},
        lambda value: {**value, "attempt_record_id": ""},
        lambda value: {**value, "attempt_record_id": "contains spaces"},
        lambda value: {**value, "declared_capability_fingerprint": "not-a-digest"},
        lambda value: {**value, "campaign_id_digest": "sha256:abc"},
        lambda value: {**value, "cell_binding_digest": None},
        lambda value: {**value, "attempt_evidence_digest": True},
        lambda value: {**value, "attempt_state": "failed"},
        lambda value: {**value, "outcome": "failure"},
    ),
)
def test_attempt_entry_shape_and_digest_fields_are_exact(mutation: Any) -> None:
    bad_mapping = _snapshot_mapping([])
    bad_mapping["attempts"] = [mutation(_attempt())]
    with pytest.raises(DeclaredAttemptSnapshotContractError):
        derive_supplied_declared_attempt_snapshot_digest(
            canonical_json_bytes(bad_mapping),
            _policy(),
        )


def test_attempts_require_full_key_sort_and_structural_uniqueness() -> None:
    first = _attempt(
        "attempt-a",
        fingerprint=_digest("capability-a"),
        campaign_id_digest=_digest("campaign-a"),
        cell_binding_digest=_digest("cell-a"),
        attempt_evidence_digest=_digest("evidence-a"),
    )
    second = _attempt(
        "attempt-b",
        fingerprint=_digest("capability-b"),
        campaign_id_digest=_digest("campaign-b"),
        cell_binding_digest=_digest("cell-b"),
        attempt_evidence_digest=_digest("evidence-b"),
    )
    mapping = _snapshot_mapping([])
    mapping["attempts"] = sorted([first, second], key=_attempt_sort_key, reverse=True)
    with pytest.raises(DeclaredAttemptSnapshotContractError, match="sorted"):
        derive_supplied_declared_attempt_snapshot_digest(
            canonical_json_bytes(mapping),
            _policy(),
        )

    duplicate_id = _snapshot_mapping(
        [
            first,
            {**second, "attempt_record_id": first["attempt_record_id"]},
        ]
    )
    with pytest.raises(DeclaredAttemptSnapshotContractError, match="record_id"):
        derive_supplied_declared_attempt_snapshot_digest(
            canonical_json_bytes(duplicate_id),
            _policy(),
        )

    duplicate_evidence = _snapshot_mapping(
        [
            first,
            {
                **second,
                "attempt_evidence_digest": first["attempt_evidence_digest"],
            },
        ]
    )
    with pytest.raises(DeclaredAttemptSnapshotContractError, match="evidence"):
        derive_supplied_declared_attempt_snapshot_digest(
            canonical_json_bytes(duplicate_evidence),
            _policy(),
        )


def test_full_sort_key_orders_each_role_without_partial_key_shortcuts() -> None:
    shared_fingerprint = _digest("shared-capability")
    baseline = _attempt(
        "attempt-a",
        fingerprint=shared_fingerprint,
        campaign_id_digest=_digest("campaign-a"),
        cell_binding_digest=_digest("cell-a"),
        attempt_evidence_digest=_digest("evidence-a"),
    )
    variants = [
        _attempt(
            "attempt-b",
            fingerprint=shared_fingerprint,
            campaign_id_digest=_digest("campaign-b"),
            cell_binding_digest=_digest("cell-b"),
            attempt_evidence_digest=_digest("evidence-b"),
        ),
        baseline,
    ]
    mapping = _snapshot_mapping([])
    mapping["attempts"] = sorted(variants, key=_attempt_sort_key)
    accepted = canonical_json_bytes(mapping)
    assert _SHA256.fullmatch(
        derive_supplied_declared_attempt_snapshot_digest(accepted, _policy())
    )

    mapping["attempts"] = list(reversed(mapping["attempts"]))
    with pytest.raises(DeclaredAttemptSnapshotContractError, match="sorted"):
        derive_supplied_declared_attempt_snapshot_digest(
            canonical_json_bytes(mapping),
            _policy(),
        )


def test_snapshot_resource_bounds_and_absolute_entry_boundary_fail_closed() -> None:
    one = _snapshot_bytes()
    for changes in (
        {"max_snapshot_bytes": len(one) - 1},
        {"max_attempt_records": 0},
        {"max_json_depth": 3},
        {"max_json_nodes": 10},
    ):
        policy = _policy(**changes)
        with pytest.raises(DeclaredAttemptSnapshotContractError):
            derive_supplied_declared_attempt_snapshot_digest(one, policy)

    entries = [
        _attempt(
            f"attempt-{index:04d}",
            fingerprint=_digest(f"capability-{index}"),
            campaign_id_digest=_digest(f"campaign-{index}"),
            cell_binding_digest=_digest(f"cell-{index}"),
            attempt_evidence_digest=_digest(f"evidence-{index}"),
        )
        for index in range(4_097)
    ]
    at_limit = _snapshot_bytes(entries[:4_096])
    assert _SHA256.fullmatch(
        derive_supplied_declared_attempt_snapshot_digest(at_limit, _policy())
    )
    above_limit = _snapshot_bytes(entries)
    with pytest.raises(DeclaredAttemptSnapshotContractError, match="record count"):
        derive_supplied_declared_attempt_snapshot_digest(above_limit, _policy())


def test_snapshot_byte_minimum_accounts_for_unique_record_ids_after_62() -> None:
    one_character_ids = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    assert len(one_character_ids) == 62
    fingerprint = _digest("shared-capability")
    campaign = _digest("shared-campaign")
    cell = _digest("shared-cell")
    entries = [
        _attempt(
            record_id,
            fingerprint=fingerprint,
            campaign_id_digest=campaign,
            cell_binding_digest=cell,
            attempt_evidence_digest=_digest(f"evidence-{index}"),
        )
        for index, record_id in enumerate((*one_character_ids, "00"))
    ]
    snapshot = _snapshot_bytes(entries, snapshot_id="a")
    minimum, maximum = attempt_module._canonical_snapshot_byte_bounds(63)
    assert minimum <= len(snapshot) <= maximum

    receipt = _evaluate(fingerprint=fingerprint, snapshot_utf8=snapshot)
    assert receipt.attempt_record_count == 63
    assert receipt.attempt_snapshot_byte_count == len(snapshot)
    assert receipt.exact_match_count == 63
    for impossible_byte_count in (minimum - 1, maximum + 1):
        with pytest.raises(DeclaredAttemptSnapshotContractError, match="impossible"):
            _reseal_receipt(
                receipt,
                attempt_snapshot_byte_count=impossible_byte_count,
            )


def test_snapshot_requires_exact_immutable_bytes() -> None:
    snapshot = _snapshot_bytes()
    for substitute in (
        snapshot.decode("utf-8"),
        bytearray(snapshot),
        memoryview(snapshot),
    ):
        with pytest.raises(DeclaredAttemptSnapshotContractError):
            derive_supplied_declared_attempt_snapshot_digest(
                substitute,  # type: ignore[arg-type]
                _policy(),
            )


def test_expected_digest_object_is_separate_deterministic_and_role_bound() -> None:
    snapshot = _snapshot_bytes()
    policy = _policy()
    expected = _expected(snapshot, policy)
    again = _expected(snapshot, policy)
    assert expected.to_mapping() == again.to_mapping()
    assert expected.expectation_digest == again.expectation_digest
    assert _SHA256.fullmatch(expected.expectation_digest)
    assert expected.target_snapshot_schema_version == (
        SUPPLIED_DECLARED_ATTEMPT_SNAPSHOT_SCHEMA
    )
    assert "expected_snapshot_digest" in inspect.signature(
        evaluate_declared_attempt_snapshot
    ).parameters
    assert (
        inspect.signature(evaluate_declared_attempt_snapshot)
        .parameters["expected_snapshot_digest"]
        .kind
        is inspect.Parameter.KEYWORD_ONLY
    )


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    (
        ("expected_attempt_snapshot_digest", "not-a-digest"),
        ("target_snapshot_schema_version", "wd.invalid.v1"),
        ("schema_version", "wd.invalid.expected.v1"),
    ),
)
def test_expected_digest_object_rejects_malformed_fields(
    field_name: str,
    bad_value: Any,
) -> None:
    expected = _expected(_snapshot_bytes(), _policy())
    values = {
        field.name: getattr(expected, field.name)
        for field in dataclasses.fields(expected)
    }
    values[field_name] = bad_value
    with pytest.raises(DeclaredAttemptSnapshotContractError):
        DeclaredAttemptExpectedSnapshotDigestV1(**values)


def test_request_contains_only_subject_and_raw_snapshot_with_exact_types() -> None:
    request = _request()
    assert {field.name for field in dataclasses.fields(request)} == {
        "declared_capability_fingerprint",
        "attempt_snapshot_utf8",
        "schema_version",
    }
    with pytest.raises(DeclaredAttemptSnapshotContractError):
        DeclaredAttemptSnapshotRequestV1(
            declared_capability_fingerprint="not-a-digest",
            attempt_snapshot_utf8=_snapshot_bytes(),
        )
    with pytest.raises(DeclaredAttemptSnapshotContractError):
        DeclaredAttemptSnapshotRequestV1(
            declared_capability_fingerprint=_digest("declared-capability"),
            attempt_snapshot_utf8=bytearray(_snapshot_bytes()),  # type: ignore[arg-type]
        )


def test_static_shadow_requires_exact_request_and_separate_expectation() -> None:
    snapshot = _snapshot_bytes()
    policy = _policy()
    expectation = _expected(snapshot, policy)
    with pytest.raises(DeclaredAttemptSnapshotContractError):
        evaluate_declared_attempt_snapshot(
            None,
            expected_snapshot_digest=expectation,
            policy=policy,
        )
    with pytest.raises(DeclaredAttemptSnapshotContractError):
        evaluate_declared_attempt_snapshot(
            _request(snapshot_utf8=snapshot),
            expected_snapshot_digest=None,
            policy=policy,
        )
    with pytest.raises(DeclaredAttemptSnapshotContractError):
        evaluate_declared_attempt_snapshot(
            _request(snapshot_utf8=snapshot),
            expected_snapshot_digest={  # type: ignore[arg-type]
                "expected_attempt_snapshot_digest": (
                    expectation.expected_attempt_snapshot_digest
                )
            },
            policy=policy,
        )


def test_expected_mismatch_dominates_subject_scan_but_not_structure_validation() -> None:
    receipt = _evaluate(expected_digest=_digest("wrong-snapshot"))
    assert receipt.disposition is DeclaredAttemptSnapshotDisposition.REFUSED
    assert receipt.reason_code is (
        DeclaredAttemptSnapshotReasonCode.EXPECTED_ATTEMPT_SNAPSHOT_DIGEST_MISMATCH
    )
    assert receipt.expected_snapshot_digest_matches is False
    assert receipt.subject_scan_performed is False
    assert receipt.exact_match_count is None
    assert receipt.matched_attempt_record_digest is None

    bad = b'{"malformed":true}'
    with pytest.raises(DeclaredAttemptSnapshotContractError):
        evaluate_declared_attempt_snapshot(
            _request(snapshot_utf8=bad),
            expected_snapshot_digest=DeclaredAttemptExpectedSnapshotDigestV1(
                expected_attempt_snapshot_digest=_digest("wrong-snapshot")
            ),
            policy=_policy(),
        )


def test_expected_holds_zero_one_and_multiple_match_table_is_exact() -> None:
    fingerprint = _digest("declared-capability")
    other = _attempt(
        "attempt-other",
        fingerprint=_digest("other-capability"),
    )
    zero = _evaluate(fingerprint=fingerprint, snapshot_utf8=_snapshot_bytes([other]))
    assert zero.disposition is (
        DeclaredAttemptSnapshotDisposition.NO_EXACT_DECLARED_CAPABILITY_MATCH_IN_SUPPLIED_ATTEMPT_SNAPSHOT
    )
    assert zero.reason_code is (
        DeclaredAttemptSnapshotReasonCode.NO_EXACT_DECLARED_CAPABILITY_MATCH
    )
    assert zero.expected_snapshot_digest_matches is True
    assert zero.subject_scan_performed is True
    assert zero.exact_match_count == 0
    assert zero.matched_attempt_record_digest is None

    one = _evaluate(
        fingerprint=fingerprint,
        snapshot_utf8=_snapshot_bytes([_attempt(fingerprint=fingerprint)]),
    )
    assert one.disposition is (
        DeclaredAttemptSnapshotDisposition.EXACTLY_ONE_DECLARED_CAPABILITY_MATCH_IN_SUPPLIED_ATTEMPT_SNAPSHOT
    )
    assert one.reason_code is (
        DeclaredAttemptSnapshotReasonCode.EXACTLY_ONE_EXACT_DECLARED_CAPABILITY_MATCH
    )
    assert one.exact_match_count == 1
    assert _SHA256.fullmatch(one.matched_attempt_record_digest or "")

    multiple = _evaluate(
        fingerprint=fingerprint,
        snapshot_utf8=_snapshot_bytes(
            [
                _attempt("attempt-a", fingerprint=fingerprint),
                _attempt("attempt-b", fingerprint=fingerprint),
            ]
        ),
    )
    assert multiple.disposition is DeclaredAttemptSnapshotDisposition.REFUSED
    assert multiple.reason_code is (
        DeclaredAttemptSnapshotReasonCode.AMBIGUOUS_MULTIPLE_EXACT_DECLARED_CAPABILITY_MATCHES
    )
    assert multiple.exact_match_count == 2
    assert multiple.matched_attempt_record_digest is None


def test_empty_snapshot_is_only_no_match_in_this_supplied_snapshot() -> None:
    receipt = _evaluate(snapshot_utf8=_snapshot_bytes([]))
    assert receipt.disposition is (
        DeclaredAttemptSnapshotDisposition.NO_EXACT_DECLARED_CAPABILITY_MATCH_IN_SUPPLIED_ATTEMPT_SNAPSHOT
    )
    assert receipt.exact_match_count == 0
    assert receipt.generation_authorized is False
    assert receipt.new_family_need_independently_verified is False
    assert receipt.global_single_attempt_enforced is False


def test_subject_comparison_is_only_fingerprint_and_metadata_changes_selection_digest() -> None:
    fingerprint = _digest("declared-capability")
    base = _attempt("attempt-a", fingerprint=fingerprint)
    variants = (
        {**base, "attempt_record_id": "attempt-b"},
        {**base, "campaign_id_digest": _digest("other-campaign")},
        {**base, "cell_binding_digest": _digest("other-cell")},
        {**base, "attempt_evidence_digest": _digest("other-evidence")},
    )
    baseline = _evaluate(snapshot_utf8=_snapshot_bytes([base]))
    for variant in variants:
        receipt = _evaluate(snapshot_utf8=_snapshot_bytes([variant]))
        assert receipt.disposition is (
            DeclaredAttemptSnapshotDisposition.EXACTLY_ONE_DECLARED_CAPABILITY_MATCH_IN_SUPPLIED_ATTEMPT_SNAPSHOT
        )
        assert receipt.exact_match_count == 1
        assert receipt.matched_attempt_record_digest != (
            baseline.matched_attempt_record_digest
        )


def test_campaign_cell_record_and_evidence_changes_cannot_evade_ambiguity() -> None:
    fingerprint = _digest("declared-capability")
    attempts = [
        _attempt(
            "attempt-a",
            fingerprint=fingerprint,
            campaign_id_digest=_digest("campaign-a"),
            cell_binding_digest=_digest("cell-a"),
            attempt_evidence_digest=_digest("evidence-a"),
        ),
        _attempt(
            "attempt-b",
            fingerprint=fingerprint,
            campaign_id_digest=_digest("campaign-b"),
            cell_binding_digest=_digest("cell-b"),
            attempt_evidence_digest=_digest("evidence-b"),
        ),
    ]
    receipt = _evaluate(fingerprint=fingerprint, snapshot_utf8=_snapshot_bytes(attempts))
    assert receipt.disposition is DeclaredAttemptSnapshotDisposition.REFUSED
    assert receipt.reason_code is (
        DeclaredAttemptSnapshotReasonCode.AMBIGUOUS_MULTIPLE_EXACT_DECLARED_CAPABILITY_MATCHES
    )
    assert receipt.exact_match_count == 2
    assert receipt.cross_campaign_single_attempt_enforced is False
    assert receipt.atomic_attempt_reservation_applied is False


def test_self_minted_stale_and_forked_snapshots_never_gain_authority() -> None:
    fingerprint = _digest("declared-capability")
    first_snapshot = _snapshot_bytes(
        [_attempt("attempt-a", fingerprint=fingerprint)],
        snapshot_id="same-snapshot-id",
        attempt_history_scope_digest=_digest("same-scope"),
    )
    forked_snapshot = _snapshot_bytes(
        [
            _attempt(
                "attempt-fork",
                fingerprint=fingerprint,
                campaign_id_digest=_digest("fork-campaign"),
                cell_binding_digest=_digest("fork-cell"),
                attempt_evidence_digest=_digest("fork-evidence"),
            )
        ],
        snapshot_id="same-snapshot-id",
        attempt_history_scope_digest=_digest("same-scope"),
    )
    first = _evaluate(fingerprint=fingerprint, snapshot_utf8=first_snapshot)
    fork = _evaluate(fingerprint=fingerprint, snapshot_utf8=forked_snapshot)
    assert first.disposition is fork.disposition is (
        DeclaredAttemptSnapshotDisposition.EXACTLY_ONE_DECLARED_CAPABILITY_MATCH_IN_SUPPLIED_ATTEMPT_SNAPSHOT
    )
    assert first.attempt_snapshot_digest != fork.attempt_snapshot_digest
    assert first.matched_attempt_record_digest != fork.matched_attempt_record_digest
    for receipt in (first, fork):
        mapping = receipt.to_mapping()
        assert all(mapping[name] is False for name in _HARD_FALSE_RECEIPT_FIELDS)


def test_exact_types_reject_subclasses_duck_types_and_equality_callbacks() -> None:
    class PolicySubclass(DeclaredAttemptSnapshotPolicyV1):
        pass

    class ExpectedSubclass(DeclaredAttemptExpectedSnapshotDigestV1):
        pass

    class RequestSubclass(DeclaredAttemptSnapshotRequestV1):
        pass

    class EqualityBomb(str):
        called = False

        def __eq__(self, other: object) -> bool:
            self.called = True
            raise AssertionError("caller equality callback must not run")

    with pytest.raises(DeclaredAttemptSnapshotContractError):
        PolicySubclass(mode=DeclaredAttemptSnapshotMode.STATIC_SHADOW)
    with pytest.raises(DeclaredAttemptSnapshotContractError):
        ExpectedSubclass(expected_attempt_snapshot_digest=_digest("snapshot"))
    with pytest.raises(DeclaredAttemptSnapshotContractError):
        RequestSubclass(
            declared_capability_fingerprint=_digest("capability"),
            attempt_snapshot_utf8=_snapshot_bytes(),
        )

    bomb = EqualityBomb(_digest("snapshot"))
    with pytest.raises(DeclaredAttemptSnapshotContractError):
        DeclaredAttemptExpectedSnapshotDigestV1(
            expected_attempt_snapshot_digest=bomb,
        )
    assert bomb.called is False


def test_postconstruction_mutations_are_revalidated_without_callbacks() -> None:
    snapshot = _snapshot_bytes()
    policy = _policy()
    request = _request(snapshot_utf8=snapshot)
    expected = _expected(snapshot, policy)
    object.__setattr__(
        expected,
        "expected_attempt_snapshot_digest",
        _digest("changed-expectation"),
    )
    changed = evaluate_declared_attempt_snapshot(
        request,
        expected_snapshot_digest=expected,
        policy=policy,
    )
    assert isinstance(changed, DeclaredAttemptSnapshotReceiptV1)
    assert changed.disposition is DeclaredAttemptSnapshotDisposition.REFUSED
    assert changed.subject_scan_performed is False

    expected = _expected(snapshot, policy)
    object.__setattr__(expected, "expected_attempt_snapshot_digest", object())
    with pytest.raises(DeclaredAttemptSnapshotContractError):
        evaluate_declared_attempt_snapshot(
            request,
            expected_snapshot_digest=expected,
            policy=policy,
        )

    class BytesBomb:
        called = False

        def __bytes__(self) -> bytes:
            self.called = True
            raise AssertionError("bytes callback must not run")

    bomb = BytesBomb()
    object.__setattr__(request, "attempt_snapshot_utf8", bomb)
    with pytest.raises(DeclaredAttemptSnapshotContractError):
        evaluate_declared_attempt_snapshot(
            request,
            expected_snapshot_digest=_expected(snapshot, policy),
            policy=policy,
        )
    assert bomb.called is False


def test_evaluation_defensively_copies_expectation_and_revalidates_policy() -> None:
    snapshot = _snapshot_bytes()
    policy = _policy()
    request = _request(snapshot_utf8=snapshot)
    expected = _expected(snapshot, policy)
    original_expected_digest = expected.expected_attempt_snapshot_digest
    receipt = evaluate_declared_attempt_snapshot(
        request,
        expected_snapshot_digest=expected,
        policy=policy,
    )
    assert isinstance(receipt, DeclaredAttemptSnapshotReceiptV1)
    assert receipt.expected_snapshot_digest is not expected

    object.__setattr__(
        expected,
        "expected_attempt_snapshot_digest",
        _digest("later-expectation-mutation"),
    )
    assert (
        receipt.expected_snapshot_digest.expected_attempt_snapshot_digest
        == original_expected_digest
    )
    assert receipt.to_mapping()["receipt_digest"] == receipt.receipt_digest

    invalid_policy = _policy()
    object.__setattr__(invalid_policy, "max_attempt_records", True)
    with pytest.raises(DeclaredAttemptSnapshotContractError):
        evaluate_declared_attempt_snapshot(
            request,
            expected_snapshot_digest=_expected(snapshot, _policy()),
            policy=invalid_policy,
        )

    invalid_request = _request(snapshot_utf8=snapshot)
    object.__setattr__(
        invalid_request,
        "declared_capability_fingerprint",
        object(),
    )
    with pytest.raises(DeclaredAttemptSnapshotContractError):
        evaluate_declared_attempt_snapshot(
            invalid_request,
            expected_snapshot_digest=_expected(snapshot, _policy()),
            policy=_policy(),
        )


def test_contract_objects_are_slotted_against_method_shadow_callbacks() -> None:
    snapshot = _snapshot_bytes()
    policy = _policy()
    request = _request(snapshot_utf8=snapshot)
    expected = _expected(snapshot, policy)
    receipt = evaluate_declared_attempt_snapshot(
        request,
        expected_snapshot_digest=expected,
        policy=policy,
    )
    assert isinstance(receipt, DeclaredAttemptSnapshotReceiptV1)
    called = False

    def callback() -> None:
        nonlocal called
        called = True

    targets = (
        (policy, "to_mapping"),
        (expected, "to_mapping"),
        (request, "to_mapping"),
        (receipt, "__post_init__"),
        (receipt, "to_mapping"),
    )
    for target, method_name in targets:
        assert not hasattr(target, "__dict__")
        with pytest.raises(AttributeError):
            object.__setattr__(target, method_name, callback)
    assert receipt.to_mapping()["generation_authorized"] is False
    assert called is False


def test_receipt_is_deterministic_digest_bound_and_raw_free() -> None:
    raw_canary = "RAW-ATTEMPT-CANARY-DO-NOT-EMIT"
    fingerprint = _digest("declared-capability")
    snapshot = _snapshot_bytes(
        [
            _attempt(
                raw_canary,
                fingerprint=fingerprint,
            )
        ],
        snapshot_id=raw_canary,
    )
    first = _evaluate(fingerprint=fingerprint, snapshot_utf8=snapshot)
    second = _evaluate(fingerprint=fingerprint, snapshot_utf8=snapshot)
    assert first.to_mapping() == second.to_mapping()
    assert first.receipt_digest == second.receipt_digest
    assert _SHA256.fullmatch(first.receipt_digest)

    public_text = repr(first) + json.dumps(first.to_mapping(), sort_keys=True)
    assert raw_canary not in public_text
    assert snapshot.decode("utf-8") not in public_text
    for forbidden in (
        "attempt_snapshot_utf8",
        "stdout",
        "stderr",
        "hostname",
        "path",
        "pid",
    ):
        assert forbidden not in public_text


def test_every_hard_claim_rejects_inflation_or_deflation() -> None:
    receipt = _evaluate()
    mapping = receipt.to_mapping()
    receipt_fields = {field.name for field in dataclasses.fields(receipt)}
    assert _HARD_TRUE_RECEIPT_FIELDS == set(attempt_module._TRUE_RECEIPT_FIELDS)
    assert _HARD_FALSE_RECEIPT_FIELDS == set(attempt_module._FALSE_RECEIPT_FIELDS)
    assert _HARD_TRUE_RECEIPT_FIELDS <= mapping.keys()
    assert _HARD_FALSE_RECEIPT_FIELDS <= mapping.keys()
    assert all(mapping[name] is True for name in _HARD_TRUE_RECEIPT_FIELDS)
    assert all(mapping[name] is False for name in _HARD_FALSE_RECEIPT_FIELDS)

    for field_name in sorted(_HARD_TRUE_RECEIPT_FIELDS & receipt_fields):
        with pytest.raises(DeclaredAttemptSnapshotContractError):
            replace(receipt, **{field_name: False})
    for field_name in sorted(_HARD_FALSE_RECEIPT_FIELDS & receipt_fields):
        with pytest.raises(DeclaredAttemptSnapshotContractError):
            replace(receipt, **{field_name: True})


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    (
        ("expected_snapshot_digest_matches", False),
        ("subject_scan_performed", False),
        ("exact_match_count", 0),
        ("matched_attempt_record_digest", None),
        (
            "disposition",
            DeclaredAttemptSnapshotDisposition.NO_EXACT_DECLARED_CAPABILITY_MATCH_IN_SUPPLIED_ATTEMPT_SNAPSHOT,
        ),
        (
            "reason_code",
            DeclaredAttemptSnapshotReasonCode.NO_EXACT_DECLARED_CAPABILITY_MATCH,
        ),
        ("policy_digest", _digest("forged-policy")),
        ("accounting_policy_digest", _digest("forged-accounting-policy")),
        ("request_digest", _digest("forged-request")),
    ),
)
def test_outer_reseal_cannot_change_relations_or_digest_bindings(
    field_name: str,
    bad_value: Any,
) -> None:
    with pytest.raises(DeclaredAttemptSnapshotContractError):
        _reseal_receipt(_evaluate(), **{field_name: bad_value})


def test_mismatch_cannot_be_resealed_but_unauthenticated_count_can_be_reminted() -> None:
    mismatch = _evaluate(expected_digest=_digest("wrong-snapshot"))
    with pytest.raises(DeclaredAttemptSnapshotContractError):
        _reseal_receipt(
            mismatch,
            expected_snapshot_digest_matches=True,
            subject_scan_performed=True,
            exact_match_count=1,
            matched_attempt_record_digest=_digest("forged-selection"),
            disposition=(
                DeclaredAttemptSnapshotDisposition.EXACTLY_ONE_DECLARED_CAPABILITY_MATCH_IN_SUPPLIED_ATTEMPT_SNAPSHOT
            ),
            reason_code=(
                DeclaredAttemptSnapshotReasonCode.EXACTLY_ONE_EXACT_DECLARED_CAPABILITY_MATCH
            ),
        )

    fingerprint = _digest("declared-capability")
    ambiguous = _evaluate(
        snapshot_utf8=_snapshot_bytes(
            [
                _attempt("attempt-a", fingerprint=fingerprint),
                _attempt("attempt-b", fingerprint=fingerprint),
            ]
        )
    )
    reminted = _reseal_receipt(
        ambiguous,
        exact_match_count=1,
        matched_attempt_record_digest=_digest("self-minted-selection"),
        disposition=(
            DeclaredAttemptSnapshotDisposition.EXACTLY_ONE_DECLARED_CAPABILITY_MATCH_IN_SUPPLIED_ATTEMPT_SNAPSHOT
        ),
        reason_code=(
            DeclaredAttemptSnapshotReasonCode.EXACTLY_ONE_EXACT_DECLARED_CAPABILITY_MATCH
        ),
    )
    # With raw records deliberately omitted, the public receipt constructor
    # cannot rederive the count or selected-record relation. A caller can mint
    # another internally shaped receipt, which is why none is authenticated or
    # authoritative and why C8d never grants generation or reservation power.
    assert reminted.exact_match_count == 1
    assert reminted.receipt_origin_authenticated is False
    assert reminted.attempt_snapshot_origin_authenticated is False
    assert reminted.attempt_history_authoritative is False
    assert reminted.atomic_attempt_reservation_applied is False
    assert reminted.generation_authorized is False


def test_nested_expectation_mutation_invalidates_existing_receipt_mapping() -> None:
    receipt = _evaluate()
    object.__setattr__(
        receipt.expected_snapshot_digest,
        "expected_attempt_snapshot_digest",
        _digest("nested-mutation"),
    )
    with pytest.raises(DeclaredAttemptSnapshotContractError):
        receipt.to_mapping()


def test_malformed_digest_error_does_not_echo_raw_input() -> None:
    raw_canary = "raw-secret-digest-value-do-not-echo"
    with pytest.raises(DeclaredAttemptSnapshotContractError) as captured:
        DeclaredAttemptExpectedSnapshotDigestV1(
            expected_attempt_snapshot_digest=raw_canary,
        )
    assert raw_canary not in str(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_module_has_no_forbidden_import_or_io_authority_seam() -> None:
    source = inspect.getsource(attempt_module)
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
