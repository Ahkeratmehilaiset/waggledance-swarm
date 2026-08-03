"""Adversarial tests for the inert C8c expected-digest relation accountant.

C8c compares fields from one internally self-consistent C8b receipt with a
separately supplied expected-digest object.  Equality is only a local relation
between caller-supplied values.  It is not an external pin, authentication,
freshness, registry identity, reuse decision, or build authority.
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

import waggledance.core.learning.understanding_gap_family_snapshot as snapshot_module
import waggledance.core.learning.understanding_gap_family_snapshot_pin as pin_module
from waggledance.core.learning.understanding_gap_family_snapshot import (
    GAP_FAMILY_MATCHING_POLICY_DIGEST,
    GAP_FAMILY_SNAPSHOT_RECEIPT_SCHEMA,
    SUPPLIED_FAMILY_SNAPSHOT_SCHEMA,
    DeclaredCapabilityGapV1,
    GapFamilySnapshotDisposition,
    GapFamilySnapshotMode,
    GapFamilySnapshotPlanV1,
    GapFamilySnapshotPolicyV1,
    GapFamilySnapshotReceiptV1,
    GapFamilySnapshotRequestV1,
    derive_declared_capability_gap_digest,
    derive_supplied_family_snapshot_digest,
    evaluate_gap_family_snapshot,
)
from waggledance.core.learning.understanding_gap_family_snapshot_pin import (
    GAP_FAMILY_SNAPSHOT_PIN_RELATION_POLICY_DIGEST,
    GapFamilySnapshotExpectedDigestPinV1,
    GapFamilySnapshotPinContractError,
    GapFamilySnapshotPinDisposition,
    GapFamilySnapshotPinMode,
    GapFamilySnapshotPinPolicyV1,
    GapFamilySnapshotPinReasonCode,
    GapFamilySnapshotPinReceiptV1,
    GapFamilySnapshotPinRequestV1,
    evaluate_gap_family_snapshot_pin,
)
from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest


_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")

_HARD_TRUE_RECEIPT_FIELDS = {
    "evaluation_only",
    "shadow_only",
    "static_accounting_only",
    "raw_material_omitted",
    "exact_expected_digest_comparison_only",
    "separate_expected_digest_object_required",
    "caller_supplied_expectations_only",
    "source_c8b_receipt_internal_relations_rechecked",
    "c8b_snapshot_evaluator_not_invoked",
    "c8a_not_invoked",
    "c7_not_invoked",
    "no_side_effects_in_module",
}

_HARD_FALSE_RECEIPT_FIELDS = {
    "snapshot_externally_pinned",
    "external_pin_provenance_verified",
    "pin_precommit_independently_verified",
    "pin_origin_authenticated",
    "pin_key_custody_independently_verified",
    "hmac_verified_in_process",
    "signature_verified",
    "snapshot_bytes_rehashed",
    "source_c8b_receipt_origin_authenticated",
    "receipt_origin_authenticated",
    "catalog_completeness_verified",
    "catalog_freshness_verified",
    "catalog_authenticity_verified",
    "registry_snapshot_identity_independently_verified",
    "semantic_equivalence_verified",
    "semantic_deduplication_verified",
    "global_deduplication_verified",
    "reuse_eligibility_claimed",
    "build_eligibility_claimed",
    "generation_authorized",
    "c8a_authorized",
    "family_novelty_independently_verified",
    "new_family_need_independently_verified",
    "existing_family_deduplication_independently_verified",
    "family_review_status_independently_verified",
    "prior_attempt_history_consulted",
    "cross_campaign_single_attempt_enforced",
    "scalability_50000_demonstrated",
    "independent_verification_applied",
    "genesis_origin_independently_verified",
    "hex_cell_binding_independently_verified",
    "echo_chamber_absence_verified",
    "provider_invoked",
    "builder_host_invoked",
    "c8b_snapshot_evaluator_invoked",
    "c8a_invoked",
    "c7_execution_requested",
    "candidate_code_executed",
    "candidate_tests_executed",
    "subprocess_spawned",
    "network_accessed",
    "os_sandbox_applied",
    "hive_commit_applied",
    "magma_write_applied",
    "registry_read_applied",
    "registry_write_requested",
    "routing_influence_requested",
    "solver_promotion_requested",
    "runtime_authority_requested",
    "product_external_system_writes_requested",
}


def _digest(label: str) -> str:
    return sha256_digest({"test_label": label})


def _source_policy() -> GapFamilySnapshotPolicyV1:
    return GapFamilySnapshotPolicyV1(mode=GapFamilySnapshotMode.STATIC_SHADOW)


def _gap(*, gap_id: str = "gap-c8c-1") -> DeclaredCapabilityGapV1:
    return DeclaredCapabilityGapV1(
        gap_id=gap_id,
        declared_capability_fingerprint=_digest("declared-capability"),
        gap_evidence_digest=_digest("gap-evidence"),
        cell_binding_digest=_digest("cell-binding"),
    )


def _family(
    family_id: str,
    *,
    fingerprint: str | None = None,
    family_kind: str = "linear_arithmetic",
) -> dict[str, str]:
    return {
        "family_id": family_id,
        "family_kind": family_kind,
        "declared_capability_fingerprint": (
            fingerprint or _digest("declared-capability")
        ),
        "descriptor_digest": _digest(f"descriptor-{family_id}"),
    }


def _snapshot_bytes(
    families: list[dict[str, str]],
    *,
    snapshot_id: str = "snapshot-c8c-1",
    registry_snapshot_digest: str | None = None,
) -> bytes:
    ordered = sorted(
        families,
        key=lambda item: (
            item["family_kind"],
            item["family_id"],
            item["declared_capability_fingerprint"],
            item["descriptor_digest"],
        ),
    )
    return canonical_json_bytes(
        {
            "schema_version": SUPPLIED_FAMILY_SNAPSHOT_SCHEMA,
            "snapshot_id": snapshot_id,
            "registry_snapshot_digest": (
                registry_snapshot_digest or _digest("registry-snapshot")
            ),
            "families": ordered,
        }
    )


def _source_receipt(
    kind: str = "exact",
    *,
    raw_canary: str | None = None,
    registry_snapshot_digest: str | None = None,
) -> GapFamilySnapshotReceiptV1:
    gap = _gap(gap_id=raw_canary or "gap-c8c-1")
    if kind == "exact":
        families = [_family(raw_canary or "family-c8c-a")]
    elif kind == "no_match":
        families = [
            _family(
                raw_canary or "family-c8c-a",
                fingerprint=_digest("different-capability"),
            )
        ]
    elif kind == "refused":
        families = [
            _family("family-c8c-a", family_kind="linear_arithmetic"),
            _family("family-c8c-b", family_kind="threshold_rule"),
        ]
    else:
        raise AssertionError(f"unsupported source-receipt kind: {kind}")

    snapshot_utf8 = _snapshot_bytes(
        families,
        snapshot_id=raw_canary or "snapshot-c8c-1",
        registry_snapshot_digest=registry_snapshot_digest,
    )
    policy = _source_policy()
    snapshot_mapping = json.loads(snapshot_utf8.decode("utf-8"))
    plan = GapFamilySnapshotPlanV1(
        campaign_id="campaign-c8c-1",
        gap_descriptor_digest=derive_declared_capability_gap_digest(gap),
        family_snapshot_digest=derive_supplied_family_snapshot_digest(
            snapshot_utf8,
            policy,
        ),
        registry_snapshot_digest=snapshot_mapping["registry_snapshot_digest"],
        resource_policy_digest=policy.policy_digest,
        matching_policy_digest=GAP_FAMILY_MATCHING_POLICY_DIGEST,
    )
    request = GapFamilySnapshotRequestV1(
        plan=plan,
        gap=gap,
        family_snapshot_utf8=snapshot_utf8,
    )
    receipt = evaluate_gap_family_snapshot(request, policy=policy)
    assert isinstance(receipt, GapFamilySnapshotReceiptV1)
    return receipt


def _expected_pin(
    source: GapFamilySnapshotReceiptV1,
    *,
    family_snapshot_digest: str | None = None,
    registry_snapshot_digest: str | None = None,
) -> GapFamilySnapshotExpectedDigestPinV1:
    return GapFamilySnapshotExpectedDigestPinV1(
        expected_family_snapshot_digest=(
            source.family_snapshot_digest
            if family_snapshot_digest is None
            else family_snapshot_digest
        ),
        expected_registry_snapshot_digest=(
            source.registry_snapshot_digest
            if registry_snapshot_digest is None
            else registry_snapshot_digest
        ),
    )


def _pin_policy() -> GapFamilySnapshotPinPolicyV1:
    return GapFamilySnapshotPinPolicyV1(
        mode=GapFamilySnapshotPinMode.STATIC_SHADOW
    )


def _evaluate_pin(
    *,
    source_kind: str = "exact",
    family_snapshot_digest: str | None = None,
    registry_snapshot_digest: str | None = None,
) -> GapFamilySnapshotPinReceiptV1:
    source = _source_receipt(source_kind)
    expected_pin = _expected_pin(
        source,
        family_snapshot_digest=family_snapshot_digest,
        registry_snapshot_digest=registry_snapshot_digest,
    )
    receipt = evaluate_gap_family_snapshot_pin(
        GapFamilySnapshotPinRequestV1(source_c8b_receipt=source),
        expected_pin=expected_pin,
        policy=_pin_policy(),
    )
    assert isinstance(receipt, GapFamilySnapshotPinReceiptV1)
    return receipt


def _normalize_mapping_value(value: Any) -> Any:
    if hasattr(value, "to_mapping"):
        return value.to_mapping()
    if hasattr(value, "value"):
        return value.value
    return value


def _reseal_pin_receipt(
    receipt: GapFamilySnapshotPinReceiptV1,
    **changes: Any,
) -> GapFamilySnapshotPinReceiptV1:
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
            "domain": "wd.understanding.gap_family_snapshot_pin_receipt.digest.v1",
            **core,
        }
    )
    return GapFamilySnapshotPinReceiptV1(**constructor)


def _cascade_reseal_source_digests(
    receipt: GapFamilySnapshotReceiptV1,
    *,
    family_snapshot_digest: str,
    registry_snapshot_digest: str,
) -> GapFamilySnapshotReceiptV1:
    plan_digest = sha256_digest(
        {
            "domain": "wd.understanding.gap_family_snapshot_plan.digest.v1",
            **snapshot_module._plan_core_mapping(
                campaign_id_digest=receipt.campaign_id_digest,
                gap_descriptor_digest=receipt.gap_descriptor_digest,
                family_snapshot_digest=family_snapshot_digest,
                registry_snapshot_digest=registry_snapshot_digest,
                resource_policy_digest=receipt.policy_digest,
                matching_policy_digest=receipt.matching_policy_digest,
            ),
        }
    )
    request_digest = snapshot_module._derive_request_digest(
        plan_digest=plan_digest,
        gap_descriptor_digest=receipt.gap_descriptor_digest,
        family_snapshot_digest=family_snapshot_digest,
    )
    changes = {
        "family_snapshot_digest": family_snapshot_digest,
        "registry_snapshot_digest": registry_snapshot_digest,
        "plan_digest": plan_digest,
        "request_digest": request_digest,
    }
    constructor = {
        field.name: getattr(receipt, field.name)
        for field in dataclasses.fields(receipt)
    }
    constructor.update(changes)
    core = receipt.to_mapping()
    core.pop("receipt_digest")
    core.update(changes)
    constructor["receipt_digest"] = sha256_digest(
        {
            "domain": "wd.understanding.gap_family_snapshot_receipt.digest.v1",
            **core,
        }
    )
    return GapFamilySnapshotReceiptV1(**constructor)


class _Bomb:
    def __getattribute__(self, name: str) -> Any:
        raise AssertionError(f"OFF mode inspected hostile input attribute {name}")


def test_public_enum_tokens_are_exact_and_non_authorizing() -> None:
    assert {item.value for item in GapFamilySnapshotPinMode} == {
        "off",
        "static_shadow",
    }
    assert {item.value for item in GapFamilySnapshotPinDisposition} == {
        "refused",
        "expected_digest_relation_holds",
        "expected_digest_relation_mismatch",
    }
    assert {item.value for item in GapFamilySnapshotPinReasonCode} == {
        "source_c8b_receipt_refused",
        "both_expected_digests_match",
        "family_snapshot_digest_mismatch",
        "registry_snapshot_digest_mismatch",
        "both_expected_digests_mismatch",
    }
    combined = " ".join(item.value for item in GapFamilySnapshotPinDisposition)
    for forbidden in (
        "pinned",
        "verified",
        "authenticated",
        "trusted",
        "novel",
        "reuse",
        "eligible",
    ):
        assert forbidden not in combined
    assert _SHA256.fullmatch(GAP_FAMILY_SNAPSHOT_PIN_RELATION_POLICY_DIGEST)


def test_policy_is_exact_typed_and_default_off() -> None:
    assert GapFamilySnapshotPinPolicyV1().mode is GapFamilySnapshotPinMode.OFF
    assert _pin_policy().mode is GapFamilySnapshotPinMode.STATIC_SHADOW
    assert evaluate_gap_family_snapshot_pin.__kwdefaults__["policy"] is None
    assert evaluate_gap_family_snapshot_pin() is None
    with pytest.raises(GapFamilySnapshotPinContractError):
        GapFamilySnapshotPinPolicyV1(mode="static_shadow")  # type: ignore[arg-type]
    with pytest.raises(GapFamilySnapshotPinContractError):
        GapFamilySnapshotPinPolicyV1(mode=None)  # type: ignore[arg-type]


def test_expected_pin_is_deterministic_role_bound_and_targets_c8b_v1() -> None:
    source = _source_receipt()
    pin = _expected_pin(source)
    again = _expected_pin(source)
    assert pin.to_mapping() == again.to_mapping()
    assert pin.pin_digest == again.pin_digest
    assert _SHA256.fullmatch(pin.pin_digest)
    assert pin.target_receipt_schema_version == GAP_FAMILY_SNAPSHOT_RECEIPT_SCHEMA

    family_changed = replace(
        pin,
        expected_family_snapshot_digest=_digest("other-family-snapshot"),
    )
    registry_changed = replace(
        pin,
        expected_registry_snapshot_digest=_digest("other-registry-snapshot"),
    )
    assert family_changed.pin_digest != pin.pin_digest
    assert registry_changed.pin_digest != pin.pin_digest
    assert family_changed.pin_digest != registry_changed.pin_digest


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    (
        ("expected_family_snapshot_digest", "not-a-digest"),
        ("expected_registry_snapshot_digest", None),
        ("target_receipt_schema_version", "wd.invalid.receipt.v1"),
        ("schema_version", "wd.invalid.pin.v1"),
    ),
)
def test_expected_pin_rejects_malformed_fields(
    field_name: str,
    bad_value: Any,
) -> None:
    source = _source_receipt()
    values = {
        field.name: getattr(_expected_pin(source), field.name)
        for field in dataclasses.fields(GapFamilySnapshotExpectedDigestPinV1)
    }
    values[field_name] = bad_value
    with pytest.raises(GapFamilySnapshotPinContractError):
        GapFamilySnapshotExpectedDigestPinV1(**values)


def test_request_contains_only_one_exact_source_c8b_receipt() -> None:
    source = _source_receipt()
    request = GapFamilySnapshotPinRequestV1(source_c8b_receipt=source)
    assert type(request.source_c8b_receipt) is GapFamilySnapshotReceiptV1
    assert {field.name for field in dataclasses.fields(request)} == {
        "source_c8b_receipt",
        "schema_version",
    }
    with pytest.raises(GapFamilySnapshotPinContractError):
        GapFamilySnapshotPinRequestV1(
            source_c8b_receipt=object(),  # type: ignore[arg-type]
        )


def test_off_returns_before_request_or_expected_pin_inspection() -> None:
    assert evaluate_gap_family_snapshot_pin(
        _Bomb(),  # type: ignore[arg-type]
        expected_pin=_Bomb(),  # type: ignore[arg-type]
    ) is None
    assert evaluate_gap_family_snapshot_pin(
        _Bomb(),  # type: ignore[arg-type]
        expected_pin=_Bomb(),  # type: ignore[arg-type]
        policy=GapFamilySnapshotPinPolicyV1(
            mode=GapFamilySnapshotPinMode.OFF
        ),
    ) is None


@pytest.mark.parametrize(
    (
        "expected_family",
        "expected_registry",
        "expected_disposition",
        "expected_reason",
        "family_equal",
        "registry_equal",
    ),
    (
        (
            None,
            None,
            GapFamilySnapshotPinDisposition.EXPECTED_DIGEST_RELATION_HOLDS,
            GapFamilySnapshotPinReasonCode.BOTH_EXPECTED_DIGESTS_MATCH,
            True,
            True,
        ),
        (
            _digest("wrong-family"),
            None,
            GapFamilySnapshotPinDisposition.EXPECTED_DIGEST_RELATION_MISMATCH,
            GapFamilySnapshotPinReasonCode.FAMILY_SNAPSHOT_DIGEST_MISMATCH,
            False,
            True,
        ),
        (
            None,
            _digest("wrong-registry"),
            GapFamilySnapshotPinDisposition.EXPECTED_DIGEST_RELATION_MISMATCH,
            GapFamilySnapshotPinReasonCode.REGISTRY_SNAPSHOT_DIGEST_MISMATCH,
            True,
            False,
        ),
        (
            _digest("wrong-family"),
            _digest("wrong-registry"),
            GapFamilySnapshotPinDisposition.EXPECTED_DIGEST_RELATION_MISMATCH,
            GapFamilySnapshotPinReasonCode.BOTH_EXPECTED_DIGESTS_MISMATCH,
            False,
            False,
        ),
    ),
)
def test_nonrefused_source_has_exact_four_row_relation_table(
    expected_family: str | None,
    expected_registry: str | None,
    expected_disposition: GapFamilySnapshotPinDisposition,
    expected_reason: GapFamilySnapshotPinReasonCode,
    family_equal: bool,
    registry_equal: bool,
) -> None:
    receipt = _evaluate_pin(
        family_snapshot_digest=expected_family,
        registry_snapshot_digest=expected_registry,
    )
    assert receipt.disposition is expected_disposition
    assert receipt.reason_code is expected_reason
    assert (
        receipt.family_snapshot_digest_equal_to_supplied_expectation
        is family_equal
    )
    assert (
        receipt.registry_snapshot_digest_equal_to_supplied_expectation
        is registry_equal
    )


def test_c8b_no_match_remains_orthogonal_to_expected_digest_relation() -> None:
    receipt = _evaluate_pin(source_kind="no_match")
    assert receipt.disposition is (
        GapFamilySnapshotPinDisposition.EXPECTED_DIGEST_RELATION_HOLDS
    )
    assert receipt.reason_code is (
        GapFamilySnapshotPinReasonCode.BOTH_EXPECTED_DIGESTS_MATCH
    )
    assert receipt.source_c8b_receipt.disposition is (
        GapFamilySnapshotDisposition.NO_EXACT_DECLARED_CAPABILITY_MATCH_IN_SUPPLIED_SNAPSHOT
    )
    assert receipt.reuse_eligibility_claimed is False
    assert receipt.family_novelty_independently_verified is False
    assert receipt.generation_authorized is False


@pytest.mark.parametrize(
    ("family_digest", "registry_digest"),
    (
        (None, None),
        (_digest("wrong-family"), None),
        (None, _digest("wrong-registry")),
        (_digest("wrong-family"), _digest("wrong-registry")),
    ),
)
def test_source_c8b_refusal_dominates_without_equality_results(
    family_digest: str | None,
    registry_digest: str | None,
) -> None:
    receipt = _evaluate_pin(
        source_kind="refused",
        family_snapshot_digest=family_digest,
        registry_snapshot_digest=registry_digest,
    )
    assert receipt.disposition is GapFamilySnapshotPinDisposition.REFUSED
    assert receipt.reason_code is (
        GapFamilySnapshotPinReasonCode.SOURCE_C8B_RECEIPT_REFUSED
    )
    assert receipt.source_c8b_receipt.disposition is (
        GapFamilySnapshotDisposition.REFUSED
    )
    assert receipt.family_snapshot_digest_equal_to_supplied_expectation is None
    assert receipt.registry_snapshot_digest_equal_to_supplied_expectation is None


def test_receipt_nests_exact_defensive_copies_of_both_inputs() -> None:
    source = _source_receipt()
    expected_pin = _expected_pin(source)
    request = GapFamilySnapshotPinRequestV1(source_c8b_receipt=source)
    receipt = evaluate_gap_family_snapshot_pin(
        request,
        expected_pin=expected_pin,
        policy=_pin_policy(),
    )
    assert isinstance(receipt, GapFamilySnapshotPinReceiptV1)
    assert type(receipt.expected_pin) is GapFamilySnapshotExpectedDigestPinV1
    assert type(receipt.source_c8b_receipt) is GapFamilySnapshotReceiptV1
    assert receipt.expected_pin is not expected_pin
    assert receipt.source_c8b_receipt is not source
    assert receipt.expected_pin.to_mapping() == expected_pin.to_mapping()
    assert receipt.source_c8b_receipt.to_mapping() == source.to_mapping()


def test_self_supplied_digest_equality_never_becomes_external_pinning() -> None:
    receipt = _evaluate_pin()
    mapping = receipt.to_mapping()
    assert receipt.disposition is (
        GapFamilySnapshotPinDisposition.EXPECTED_DIGEST_RELATION_HOLDS
    )
    assert _HARD_TRUE_RECEIPT_FIELDS <= mapping.keys()
    assert _HARD_FALSE_RECEIPT_FIELDS <= mapping.keys()
    assert all(mapping[name] is True for name in _HARD_TRUE_RECEIPT_FIELDS)
    assert all(mapping[name] is False for name in _HARD_FALSE_RECEIPT_FIELDS)


def test_fully_resealed_c8b_receipt_can_only_produce_local_equality() -> None:
    source = _source_receipt()
    forged_source = _cascade_reseal_source_digests(
        source,
        family_snapshot_digest=_digest("self-minted-family-snapshot"),
        registry_snapshot_digest=_digest("self-minted-registry-snapshot"),
    )
    assert forged_source.to_mapping()["receipt_origin_authenticated"] is False

    receipt = evaluate_gap_family_snapshot_pin(
        GapFamilySnapshotPinRequestV1(source_c8b_receipt=forged_source),
        expected_pin=_expected_pin(forged_source),
        policy=_pin_policy(),
    )
    assert isinstance(receipt, GapFamilySnapshotPinReceiptV1)
    assert receipt.disposition is (
        GapFamilySnapshotPinDisposition.EXPECTED_DIGEST_RELATION_HOLDS
    )
    mapping = receipt.to_mapping()
    assert all(mapping[name] is False for name in _HARD_FALSE_RECEIPT_FIELDS)


def test_exact_input_types_reject_subclasses_and_duck_types() -> None:
    source = _source_receipt()

    class PolicySubclass(GapFamilySnapshotPinPolicyV1):
        pass

    class PinSubclass(GapFamilySnapshotExpectedDigestPinV1):
        pass

    class RequestSubclass(GapFamilySnapshotPinRequestV1):
        pass

    class SourceReceiptSubclass(GapFamilySnapshotReceiptV1):
        pass

    with pytest.raises(GapFamilySnapshotPinContractError):
        PolicySubclass(mode=GapFamilySnapshotPinMode.STATIC_SHADOW)
    with pytest.raises(GapFamilySnapshotPinContractError):
        PinSubclass(
            expected_family_snapshot_digest=source.family_snapshot_digest,
            expected_registry_snapshot_digest=source.registry_snapshot_digest,
        )
    source_subclass = SourceReceiptSubclass(
        **{
            field.name: getattr(source, field.name)
            for field in dataclasses.fields(source)
        }
    )
    with pytest.raises(GapFamilySnapshotPinContractError):
        RequestSubclass(source_c8b_receipt=source)
    with pytest.raises(GapFamilySnapshotPinContractError):
        GapFamilySnapshotPinRequestV1(source_c8b_receipt=source_subclass)


def test_str_subclass_equality_callback_is_rejected_without_invocation() -> None:
    class EqualityBomb(str):
        called = False

        def __eq__(self, other: object) -> bool:
            self.called = True
            raise AssertionError("digest equality callback must not run")

    source = _source_receipt()
    bomb = EqualityBomb(source.family_snapshot_digest)
    with pytest.raises(GapFamilySnapshotPinContractError):
        GapFamilySnapshotExpectedDigestPinV1(
            expected_family_snapshot_digest=bomb,
            expected_registry_snapshot_digest=source.registry_snapshot_digest,
        )
    assert bomb.called is False


def test_public_contract_objects_are_slotted_against_method_shadowing() -> None:
    source = _source_receipt()
    expected_pin = _expected_pin(source)
    policy = _pin_policy()
    request = GapFamilySnapshotPinRequestV1(source_c8b_receipt=source)
    receipt = evaluate_gap_family_snapshot_pin(
        request,
        expected_pin=expected_pin,
        policy=policy,
    )
    assert isinstance(receipt, GapFamilySnapshotPinReceiptV1)
    called = False

    def callback() -> None:
        nonlocal called
        called = True

    targets = (
        (policy, "to_mapping"),
        (expected_pin, "__post_init__"),
        (expected_pin, "to_mapping"),
        (request, "__post_init__"),
        (receipt, "__post_init__"),
        (receipt, "to_mapping"),
    )
    for target, method_name in targets:
        assert not hasattr(target, "__dict__")
        with pytest.raises(AttributeError):
            object.__setattr__(target, method_name, callback)
    assert receipt.to_mapping()["snapshot_externally_pinned"] is False
    assert called is False


def test_postconstruction_input_mutations_are_revalidated() -> None:
    source = _source_receipt()
    expected_pin = _expected_pin(source)
    request = GapFamilySnapshotPinRequestV1(source_c8b_receipt=source)
    object.__setattr__(
        expected_pin,
        "expected_family_snapshot_digest",
        _digest("mutated-expected-family"),
    )
    changed_expectation = evaluate_gap_family_snapshot_pin(
        request,
        expected_pin=expected_pin,
        policy=_pin_policy(),
    )
    assert isinstance(changed_expectation, GapFamilySnapshotPinReceiptV1)
    assert changed_expectation.disposition is (
        GapFamilySnapshotPinDisposition.EXPECTED_DIGEST_RELATION_MISMATCH
    )
    assert changed_expectation.reason_code is (
        GapFamilySnapshotPinReasonCode.FAMILY_SNAPSHOT_DIGEST_MISMATCH
    )

    source = _source_receipt()
    expected_pin = _expected_pin(source)
    request = GapFamilySnapshotPinRequestV1(source_c8b_receipt=source)
    object.__setattr__(expected_pin, "expected_family_snapshot_digest", object())
    with pytest.raises(GapFamilySnapshotPinContractError):
        evaluate_gap_family_snapshot_pin(
            request,
            expected_pin=expected_pin,
            policy=_pin_policy(),
        )

    source = _source_receipt()
    expected_pin = _expected_pin(source)
    request = GapFamilySnapshotPinRequestV1(source_c8b_receipt=source)
    object.__setattr__(source, "receipt_digest", _digest("stale-source-receipt"))
    with pytest.raises(GapFamilySnapshotPinContractError):
        evaluate_gap_family_snapshot_pin(
            request,
            expected_pin=expected_pin,
            policy=_pin_policy(),
        )

    source = _source_receipt()
    expected_pin = _expected_pin(source)
    request = GapFamilySnapshotPinRequestV1(source_c8b_receipt=source)
    object.__setattr__(request, "source_c8b_receipt", object())
    with pytest.raises(GapFamilySnapshotPinContractError):
        evaluate_gap_family_snapshot_pin(
            request,
            expected_pin=expected_pin,
            policy=_pin_policy(),
        )


def test_evaluation_snapshots_inputs_before_returning() -> None:
    source = _source_receipt()
    expected_pin = _expected_pin(source)
    original_family_digest = source.family_snapshot_digest
    original_expected_family_digest = expected_pin.expected_family_snapshot_digest
    receipt = evaluate_gap_family_snapshot_pin(
        GapFamilySnapshotPinRequestV1(source_c8b_receipt=source),
        expected_pin=expected_pin,
        policy=_pin_policy(),
    )
    assert isinstance(receipt, GapFamilySnapshotPinReceiptV1)

    object.__setattr__(source, "family_snapshot_digest", _digest("later-source"))
    object.__setattr__(
        expected_pin,
        "expected_family_snapshot_digest",
        _digest("later-pin"),
    )
    assert receipt.source_c8b_receipt.family_snapshot_digest == original_family_digest
    assert (
        receipt.expected_pin.expected_family_snapshot_digest
        == original_expected_family_digest
    )
    assert receipt.to_mapping()["receipt_digest"] == receipt.receipt_digest


def test_receipt_is_deterministic_digest_bound_and_raw_free() -> None:
    raw_canary = "raw-canary-do-not-emit"
    source = _source_receipt(raw_canary=raw_canary)
    expected_pin = _expected_pin(source)
    request = GapFamilySnapshotPinRequestV1(source_c8b_receipt=source)
    first = evaluate_gap_family_snapshot_pin(
        request,
        expected_pin=expected_pin,
        policy=_pin_policy(),
    )
    second = evaluate_gap_family_snapshot_pin(
        request,
        expected_pin=expected_pin,
        policy=_pin_policy(),
    )
    assert isinstance(first, GapFamilySnapshotPinReceiptV1)
    assert isinstance(second, GapFamilySnapshotPinReceiptV1)
    assert first.to_mapping() == second.to_mapping()
    assert first.receipt_digest == second.receipt_digest
    assert _SHA256.fullmatch(first.receipt_digest)

    public_text = repr(first) + json.dumps(first.to_mapping(), sort_keys=True)
    assert raw_canary not in public_text
    for forbidden in (
        "family_snapshot_utf8",
        "stdout",
        "stderr",
        "hostname",
        "path",
        "pid",
    ):
        assert forbidden not in public_text


def test_every_hard_claim_rejects_inflation_or_deflation() -> None:
    receipt = _evaluate_pin()
    mapping = receipt.to_mapping()
    receipt_fields = {field.name for field in dataclasses.fields(receipt)}
    assert _HARD_TRUE_RECEIPT_FIELDS <= receipt_fields
    assert _HARD_FALSE_RECEIPT_FIELDS <= receipt_fields

    for field_name in sorted(_HARD_TRUE_RECEIPT_FIELDS):
        assert mapping[field_name] is True
        with pytest.raises(GapFamilySnapshotPinContractError):
            replace(receipt, **{field_name: False})
    for field_name in sorted(_HARD_FALSE_RECEIPT_FIELDS):
        assert mapping[field_name] is False
        with pytest.raises(GapFamilySnapshotPinContractError):
            replace(receipt, **{field_name: True})


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    (
        (
            "family_snapshot_digest_equal_to_supplied_expectation",
            False,
        ),
        (
            "registry_snapshot_digest_equal_to_supplied_expectation",
            False,
        ),
        (
            "disposition",
            GapFamilySnapshotPinDisposition.EXPECTED_DIGEST_RELATION_MISMATCH,
        ),
        (
            "reason_code",
            GapFamilySnapshotPinReasonCode.BOTH_EXPECTED_DIGESTS_MISMATCH,
        ),
        ("policy_digest", _digest("forged-policy")),
        ("relation_policy_digest", _digest("forged-relation-policy")),
        ("request_digest", _digest("forged-request")),
    ),
)
def test_outer_reseal_cannot_change_relations_or_digest_bindings(
    field_name: str,
    bad_value: Any,
) -> None:
    with pytest.raises(GapFamilySnapshotPinContractError):
        _reseal_pin_receipt(_evaluate_pin(), **{field_name: bad_value})


def test_source_refusal_cannot_be_resealed_into_positive_relation() -> None:
    receipt = _evaluate_pin(source_kind="refused")
    with pytest.raises(GapFamilySnapshotPinContractError):
        _reseal_pin_receipt(
            receipt,
            family_snapshot_digest_equal_to_supplied_expectation=True,
            registry_snapshot_digest_equal_to_supplied_expectation=True,
            disposition=(
                GapFamilySnapshotPinDisposition.EXPECTED_DIGEST_RELATION_HOLDS
            ),
            reason_code=GapFamilySnapshotPinReasonCode.BOTH_EXPECTED_DIGESTS_MATCH,
        )


def test_request_and_receipt_digests_cross_bind_pin_and_source_roles() -> None:
    source = _source_receipt()
    pin = _expected_pin(source)
    baseline = evaluate_gap_family_snapshot_pin(
        GapFamilySnapshotPinRequestV1(source_c8b_receipt=source),
        expected_pin=pin,
        policy=_pin_policy(),
    )
    assert isinstance(baseline, GapFamilySnapshotPinReceiptV1)

    changed_pin = replace(
        pin,
        expected_family_snapshot_digest=_digest("cross-bound-family"),
    )
    pin_changed = evaluate_gap_family_snapshot_pin(
        GapFamilySnapshotPinRequestV1(source_c8b_receipt=source),
        expected_pin=changed_pin,
        policy=_pin_policy(),
    )
    assert isinstance(pin_changed, GapFamilySnapshotPinReceiptV1)
    assert pin_changed.request_digest != baseline.request_digest
    assert pin_changed.receipt_digest != baseline.receipt_digest

    other_source = _source_receipt(
        registry_snapshot_digest=_digest("other-registry")
    )
    source_changed = evaluate_gap_family_snapshot_pin(
        GapFamilySnapshotPinRequestV1(source_c8b_receipt=other_source),
        expected_pin=_expected_pin(other_source),
        policy=_pin_policy(),
    )
    assert isinstance(source_changed, GapFamilySnapshotPinReceiptV1)
    assert source_changed.request_digest != baseline.request_digest
    assert source_changed.receipt_digest != baseline.receipt_digest

    swapped_roles = GapFamilySnapshotExpectedDigestPinV1(
        expected_family_snapshot_digest=source.registry_snapshot_digest,
        expected_registry_snapshot_digest=source.family_snapshot_digest,
    )
    swapped = evaluate_gap_family_snapshot_pin(
        GapFamilySnapshotPinRequestV1(source_c8b_receipt=source),
        expected_pin=swapped_roles,
        policy=_pin_policy(),
    )
    assert isinstance(swapped, GapFamilySnapshotPinReceiptV1)
    assert swapped.disposition is (
        GapFamilySnapshotPinDisposition.EXPECTED_DIGEST_RELATION_MISMATCH
    )
    assert swapped.reason_code is (
        GapFamilySnapshotPinReasonCode.BOTH_EXPECTED_DIGESTS_MISMATCH
    )


def test_nested_pin_or_source_mutation_invalidates_existing_receipt_mapping() -> None:
    receipt = _evaluate_pin()
    object.__setattr__(
        receipt.expected_pin,
        "expected_family_snapshot_digest",
        _digest("nested-pin-mutation"),
    )
    with pytest.raises(GapFamilySnapshotPinContractError):
        receipt.to_mapping()

    receipt = _evaluate_pin()
    object.__setattr__(
        receipt.source_c8b_receipt,
        "receipt_digest",
        _digest("nested-source-mutation"),
    )
    with pytest.raises(GapFamilySnapshotPinContractError):
        receipt.to_mapping()


def test_malformed_digest_error_does_not_echo_raw_input() -> None:
    raw_canary = "raw-secret-digest-value-do-not-echo"
    with pytest.raises(GapFamilySnapshotPinContractError) as captured:
        GapFamilySnapshotExpectedDigestPinV1(
            expected_family_snapshot_digest=raw_canary,
            expected_registry_snapshot_digest=_digest("registry"),
        )
    assert raw_canary not in str(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_module_has_no_forbidden_import_or_io_authority_seam() -> None:
    source = inspect.getsource(pin_module)
    tree = ast.parse(source)
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])

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
        "understanding_coding_candidate_builder",
        "understanding_paired_runner",
        "builder_host",
        "registry",
        "runtime",
    )
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert all(
        fragment not in module_name
        for fragment in forbidden_import_fragments
        for module_name in imported_modules
    )
