"""Adversarial tests for the inert C8b supplied-family snapshot accountant.

The contract is deliberately one-way evidence accounting.  Exact equality in
one caller-supplied snapshot is not semantic equivalence, family reuse,
novelty, deduplication, build eligibility, or authority to invoke anything.
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
from waggledance.core.learning.understanding_gap_family_snapshot import (
    DECLARED_CLOSED_FAMILY_KINDS,
    GAP_FAMILY_MATCHING_POLICY_DIGEST,
    DeclaredCapabilityGapV1,
    GapFamilySnapshotContractError,
    GapFamilySnapshotDisposition,
    GapFamilySnapshotMode,
    GapFamilySnapshotPlanV1,
    GapFamilySnapshotPolicyV1,
    GapFamilySnapshotReasonCode,
    GapFamilySnapshotReceiptV1,
    GapFamilySnapshotRequestV1,
    SUPPLIED_FAMILY_SNAPSHOT_SCHEMA,
    derive_declared_capability_gap_digest,
    derive_supplied_family_snapshot_digest,
    evaluate_gap_family_snapshot,
)
from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest


_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_CLOSED_FAMILIES = {
    "bounded_interpolation",
    "interval_bucket_classifier",
    "linear_arithmetic",
    "lookup_table",
    "scalar_unit_conversion",
    "threshold_rule",
}
_SNAPSHOT_SCHEMA = SUPPLIED_FAMILY_SNAPSHOT_SCHEMA
_HARD_FALSE_RECEIPT_FIELDS = {
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
    "catalog_completeness_verified",
    "catalog_freshness_verified",
    "catalog_authenticity_verified",
    "snapshot_externally_pinned",
    "registry_snapshot_identity_independently_verified",
    "family_review_status_independently_verified",
    "cross_campaign_single_attempt_enforced",
    "scalability_50000_demonstrated",
    "independent_verification_applied",
    "receipt_origin_authenticated",
    "genesis_origin_independently_verified",
    "hex_cell_binding_independently_verified",
    "echo_chamber_absence_verified",
    "provider_invoked",
    "builder_host_invoked",
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


def _policy(**changes: Any) -> GapFamilySnapshotPolicyV1:
    return GapFamilySnapshotPolicyV1(
        mode=GapFamilySnapshotMode.STATIC_SHADOW,
        **changes,
    )


def _gap(
    *,
    gap_id: str = "gap-thermal-1",
    fingerprint: str | None = None,
    evidence_digest: str | None = None,
    cell_binding_digest: str | None = None,
) -> DeclaredCapabilityGapV1:
    return DeclaredCapabilityGapV1(
        gap_id=gap_id,
        declared_capability_fingerprint=(
            fingerprint or _digest("capability-linear-v1")
        ),
        gap_evidence_digest=evidence_digest or _digest("gap-evidence-v1"),
        cell_binding_digest=cell_binding_digest or _digest("cell-binding-v1"),
    )


def _family(
    family_id: str = "family-linear-v1",
    family_kind: str = "linear_arithmetic",
    fingerprint: str | None = None,
    descriptor_digest: str | None = None,
) -> dict[str, str]:
    return {
        "family_id": family_id,
        "family_kind": family_kind,
        "declared_capability_fingerprint": (
            fingerprint or _digest("capability-linear-v1")
        ),
        "descriptor_digest": descriptor_digest or _digest(f"descriptor-{family_id}"),
    }


def _snapshot_mapping(
    families: list[dict[str, Any]] | None = None,
    *,
    snapshot_id: str = "snapshot-local-1",
    registry_snapshot_digest: str | None = None,
) -> dict[str, Any]:
    entries = [_family()] if families is None else families
    entries = sorted(entries, key=lambda item: item["family_id"])
    return {
        "schema_version": _SNAPSHOT_SCHEMA,
        "snapshot_id": snapshot_id,
        "registry_snapshot_digest": (
            registry_snapshot_digest or _digest("caller-registry-snapshot")
        ),
        "families": entries,
    }


def _snapshot_bytes(
    families: list[dict[str, Any]] | None = None,
    **changes: Any,
) -> bytes:
    return canonical_json_bytes(_snapshot_mapping(families, **changes))


def _plan(
    gap: DeclaredCapabilityGapV1,
    snapshot_utf8: bytes,
    policy: GapFamilySnapshotPolicyV1,
    **changes: Any,
) -> GapFamilySnapshotPlanV1:
    snapshot = json.loads(snapshot_utf8.decode("utf-8"))
    values: dict[str, Any] = {
        "campaign_id": "campaign-c8b-1",
        "gap_descriptor_digest": derive_declared_capability_gap_digest(gap),
        "family_snapshot_digest": derive_supplied_family_snapshot_digest(
            snapshot_utf8, policy
        ),
        "registry_snapshot_digest": snapshot["registry_snapshot_digest"],
        "resource_policy_digest": policy.policy_digest,
        "matching_policy_digest": GAP_FAMILY_MATCHING_POLICY_DIGEST,
        "attempt_index": 1,
        "attempt_budget": 1,
        "shadow_only": True,
        "evaluation_only": True,
        "caller_supplied_snapshot_only": True,
    }
    values.update(changes)
    return GapFamilySnapshotPlanV1(**values)


def _request(
    *,
    gap: DeclaredCapabilityGapV1 | None = None,
    snapshot_utf8: bytes | None = None,
    policy: GapFamilySnapshotPolicyV1 | None = None,
    plan_changes: dict[str, Any] | None = None,
) -> tuple[GapFamilySnapshotRequestV1, GapFamilySnapshotPolicyV1]:
    selected_gap = gap or _gap()
    selected_snapshot = snapshot_utf8 or _snapshot_bytes()
    selected_policy = policy or _policy()
    plan = _plan(
        selected_gap,
        selected_snapshot,
        selected_policy,
        **(plan_changes or {}),
    )
    return (
        GapFamilySnapshotRequestV1(
            plan=plan,
            gap=selected_gap,
            family_snapshot_utf8=selected_snapshot,
        ),
        selected_policy,
    )


def _evaluate(
    *,
    gap: DeclaredCapabilityGapV1 | None = None,
    snapshot_utf8: bytes | None = None,
) -> GapFamilySnapshotReceiptV1:
    request, policy = _request(gap=gap, snapshot_utf8=snapshot_utf8)
    receipt = evaluate_gap_family_snapshot(request, policy=policy)
    assert isinstance(receipt, GapFamilySnapshotReceiptV1)
    return receipt


def _reseal_receipt(
    receipt: GapFamilySnapshotReceiptV1,
    **changes: Any,
) -> GapFamilySnapshotReceiptV1:
    constructor = {
        field.name: getattr(receipt, field.name)
        for field in dataclasses.fields(receipt)
    }
    constructor.update(changes)
    core = receipt.to_mapping()
    core.pop("receipt_digest")
    for name, value in changes.items():
        core[name] = value.value if hasattr(value, "value") else value
    constructor["receipt_digest"] = sha256_digest(
        {
            "domain": "wd.understanding.gap_family_snapshot_receipt.digest.v1",
            **core,
        }
    )
    return GapFamilySnapshotReceiptV1(**constructor)


def _cascade_reseal_policy(
    receipt: GapFamilySnapshotReceiptV1,
    **policy_changes: int,
) -> GapFamilySnapshotReceiptV1:
    policy_values = {
        "max_snapshot_bytes": receipt.max_snapshot_bytes,
        "max_family_entries": receipt.max_family_entries,
        "max_json_depth": receipt.max_json_depth,
        "max_json_nodes": receipt.max_json_nodes,
    }
    policy_values.update(policy_changes)
    forged_policy = _policy(**policy_values)
    forged_plan_digest = sha256_digest(
        {
            "domain": "wd.understanding.gap_family_snapshot_plan.digest.v1",
            **snapshot_module._plan_core_mapping(
                campaign_id_digest=receipt.campaign_id_digest,
                gap_descriptor_digest=receipt.gap_descriptor_digest,
                family_snapshot_digest=receipt.family_snapshot_digest,
                registry_snapshot_digest=receipt.registry_snapshot_digest,
                resource_policy_digest=forged_policy.policy_digest,
                matching_policy_digest=receipt.matching_policy_digest,
            ),
        }
    )
    forged_request_digest = snapshot_module._derive_request_digest(
        plan_digest=forged_plan_digest,
        gap_descriptor_digest=receipt.gap_descriptor_digest,
        family_snapshot_digest=receipt.family_snapshot_digest,
    )
    return _reseal_receipt(
        receipt,
        **policy_values,
        policy_digest=forged_policy.policy_digest,
        plan_digest=forged_plan_digest,
        request_digest=forged_request_digest,
    )


class _Bomb:
    def __getattribute__(self, name: str) -> Any:
        raise AssertionError(f"OFF mode inspected request attribute {name}")


def test_public_enum_tokens_are_narrow_and_non_authorizing() -> None:
    assert {item.value for item in GapFamilySnapshotMode} == {
        "off",
        "static_shadow",
    }
    assert {item.value for item in GapFamilySnapshotDisposition} == {
        "refused",
        "exact_declared_capability_match_in_supplied_snapshot",
        "no_exact_declared_capability_match_in_supplied_snapshot",
    }
    combined = " ".join(item.value for item in GapFamilySnapshotDisposition)
    for forbidden in ("novel", "reuse", "eligible"):
        assert forbidden not in combined


def test_closed_family_and_matching_policy_constants_are_exact() -> None:
    assert set(DECLARED_CLOSED_FAMILY_KINDS) == _CLOSED_FAMILIES
    assert len(DECLARED_CLOSED_FAMILY_KINDS) == len(_CLOSED_FAMILIES)
    assert _SHA256.fullmatch(GAP_FAMILY_MATCHING_POLICY_DIGEST)


def test_matching_policy_binds_vocabulary_and_ambiguity_rules() -> None:
    policy = snapshot_module._MATCHING_POLICY
    assert tuple(policy["closed_family_kinds"]) == DECLARED_CLOSED_FAMILY_KINDS
    assert policy["canonical_snapshot_required"] is True
    assert tuple(policy["family_entry_sort_key"]) == (
        "family_kind",
        "family_id",
        "declared_capability_fingerprint",
        "descriptor_digest",
    )
    assert policy["duplicate_family_id_rule"] == "refused"
    assert policy["duplicate_descriptor_digest_rule"] == "refused"
    assert policy["ambiguous_exact_match_rule"] == "refused_without_selection"
    assert sha256_digest(policy) == GAP_FAMILY_MATCHING_POLICY_DIGEST


def test_rebinding_public_family_tuple_cannot_expand_internal_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        snapshot_module,
        "DECLARED_CLOSED_FAMILY_KINDS",
        (*DECLARED_CLOSED_FAMILY_KINDS, "free_form_python"),
    )
    supplied = _snapshot_bytes([_family(family_kind="free_form_python")])
    with pytest.raises(GapFamilySnapshotContractError, match="closed set"):
        derive_supplied_family_snapshot_digest(supplied, _policy())
    assert snapshot_module.GAP_FAMILY_MATCHING_POLICY_DIGEST == (
        GAP_FAMILY_MATCHING_POLICY_DIGEST
    )


@pytest.mark.parametrize(
    "changes",
    (
        {"mode": "static_shadow"},
        {"max_snapshot_bytes": True},
        {"max_snapshot_bytes": 127},
        {"max_snapshot_bytes": 2_097_153},
        {"max_family_entries": -1},
        {"max_family_entries": 4_097},
        {"max_json_depth": 0},
        {"max_json_depth": 7},
        {"max_json_nodes": 0},
        {"max_json_nodes": 32_769},
    ),
)
def test_policy_exact_types_and_absolute_bounds_fail_closed(
    changes: dict[str, Any],
) -> None:
    values: dict[str, Any] = {
        "mode": GapFamilySnapshotMode.STATIC_SHADOW,
        "max_snapshot_bytes": 2_097_152,
        "max_family_entries": 4_096,
        "max_json_depth": 6,
        "max_json_nodes": 32_768,
    }
    values.update(changes)
    with pytest.raises(GapFamilySnapshotContractError):
        GapFamilySnapshotPolicyV1(**values)


def test_default_off_returns_before_request_inspection() -> None:
    assert evaluate_gap_family_snapshot(_Bomb()) is None  # type: ignore[arg-type]
    assert evaluate_gap_family_snapshot(
        _Bomb(),  # type: ignore[arg-type]
        policy=GapFamilySnapshotPolicyV1(mode=GapFamilySnapshotMode.OFF),
    ) is None


def test_declared_gap_digest_is_deterministic_and_binds_every_field() -> None:
    gap = _gap()
    baseline = derive_declared_capability_gap_digest(gap)
    assert _SHA256.fullmatch(baseline)
    assert derive_declared_capability_gap_digest(gap) == baseline

    variants = (
        replace(gap, gap_id="gap-thermal-2"),
        replace(gap, declared_capability_fingerprint=_digest("other-capability")),
        replace(gap, gap_evidence_digest=_digest("other-evidence")),
        replace(gap, cell_binding_digest=_digest("other-cell")),
    )
    assert all(derive_declared_capability_gap_digest(item) != baseline for item in variants)


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    (
        ("schema_version", "wd.invalid.v1"),
        ("gap_id", ""),
        ("gap_id", "contains spaces"),
        ("declared_capability_fingerprint", "not-a-digest"),
        ("gap_evidence_digest", "sha256:xyz"),
        ("cell_binding_digest", None),
    ),
)
def test_declared_gap_rejects_malformed_fields(
    field_name: str, bad_value: Any
) -> None:
    values = dataclasses.asdict(_gap())
    values[field_name] = bad_value
    with pytest.raises(GapFamilySnapshotContractError):
        DeclaredCapabilityGapV1(**values)


def test_exact_declared_capability_match_is_only_a_supplied_snapshot_fact() -> None:
    receipt = _evaluate()
    assert (
        receipt.disposition
        is GapFamilySnapshotDisposition.EXACT_DECLARED_CAPABILITY_MATCH_IN_SUPPLIED_SNAPSHOT
    )
    assert receipt.exact_match_count == 1
    assert _SHA256.fullmatch(receipt.matched_family_entry_digest or "")
    assert receipt.reason_code is GapFamilySnapshotReasonCode.EXACT_MATCH

    mapping = receipt.to_mapping()
    assert mapping["disposition"] == (
        "exact_declared_capability_match_in_supplied_snapshot"
    )
    assert mapping["exact_match_count"] == 1


def test_no_exact_match_is_not_novelty_reuse_or_build_eligibility() -> None:
    receipt = _evaluate(
        snapshot_utf8=_snapshot_bytes(
            [_family(fingerprint=_digest("different-capability"))]
        )
    )
    assert receipt.disposition is (
        GapFamilySnapshotDisposition.NO_EXACT_DECLARED_CAPABILITY_MATCH_IN_SUPPLIED_SNAPSHOT
    )
    assert receipt.exact_match_count == 0
    assert receipt.matched_family_entry_digest is None
    assert receipt.reason_code is GapFamilySnapshotReasonCode.NO_EXACT_MATCH
    assert "novel" not in receipt.disposition.value
    assert "reuse" not in receipt.disposition.value
    assert "eligible" not in receipt.disposition.value
    assert all(receipt.to_mapping()[name] is False for name in _HARD_FALSE_RECEIPT_FIELDS)


def test_ambiguous_exact_matches_refuse_without_selecting_a_family() -> None:
    fingerprint = _digest("capability-linear-v1")
    receipt = _evaluate(
        snapshot_utf8=_snapshot_bytes(
            [
                _family(
                    family_id="family-a",
                    family_kind="linear_arithmetic",
                    fingerprint=fingerprint,
                ),
                _family(
                    family_id="family-b",
                    family_kind="threshold_rule",
                    fingerprint=fingerprint,
                ),
            ]
        )
    )
    assert receipt.disposition is GapFamilySnapshotDisposition.REFUSED
    assert receipt.exact_match_count == 2
    assert receipt.matched_family_entry_digest is None
    assert receipt.reason_code is GapFamilySnapshotReasonCode.AMBIGUOUS_EXACT_MATCH


def test_exact_matching_compares_only_the_declared_fingerprint() -> None:
    fingerprint = _digest("capability-linear-v1")
    first = _evaluate(
        snapshot_utf8=_snapshot_bytes(
            [
                _family(
                    family_id="family-a",
                    family_kind="linear_arithmetic",
                    fingerprint=fingerprint,
                    descriptor_digest=_digest("descriptor-a"),
                )
            ]
        )
    )
    second = _evaluate(
        snapshot_utf8=_snapshot_bytes(
            [
                _family(
                    family_id="family-b",
                    family_kind="threshold_rule",
                    fingerprint=fingerprint,
                    descriptor_digest=_digest("descriptor-b"),
                )
            ]
        )
    )
    assert first.disposition is second.disposition is (
        GapFamilySnapshotDisposition.EXACT_DECLARED_CAPABILITY_MATCH_IN_SUPPLIED_SNAPSHOT
    )
    assert first.matched_family_entry_digest != second.matched_family_entry_digest


def test_self_consistent_caller_snapshot_never_becomes_external_pinning() -> None:
    receipt = _evaluate(
        snapshot_utf8=_snapshot_bytes(
            registry_snapshot_digest=_digest("self-minted-registry")
        )
    )
    mapping = receipt.to_mapping()
    assert _HARD_FALSE_RECEIPT_FIELDS <= mapping.keys()
    assert all(mapping[name] is False for name in _HARD_FALSE_RECEIPT_FIELDS)


def test_snapshot_digest_is_deterministic_and_binds_every_entry_field() -> None:
    policy = _policy()
    baseline_bytes = _snapshot_bytes()
    baseline = derive_supplied_family_snapshot_digest(baseline_bytes, policy)
    assert _SHA256.fullmatch(baseline)
    assert derive_supplied_family_snapshot_digest(baseline_bytes, policy) == baseline

    variants = (
        _snapshot_bytes(snapshot_id="snapshot-local-2"),
        _snapshot_bytes(registry_snapshot_digest=_digest("other-registry")),
        _snapshot_bytes([_family(family_id="family-other")]),
        _snapshot_bytes([_family(family_kind="threshold_rule")]),
        _snapshot_bytes([_family(fingerprint=_digest("other-capability"))]),
        _snapshot_bytes([_family(descriptor_digest=_digest("other-descriptor"))]),
    )
    assert all(
        derive_supplied_family_snapshot_digest(value, policy) != baseline
        for value in variants
    )


@pytest.mark.parametrize(
    "bad_snapshot",
    (
        b"",
        b"not-json",
        b"\xff",
        b"[]",
        b"null",
    ),
)
def test_snapshot_rejects_empty_invalid_utf8_or_non_object_json(
    bad_snapshot: bytes,
) -> None:
    with pytest.raises(GapFamilySnapshotContractError):
        derive_supplied_family_snapshot_digest(bad_snapshot, _policy())


@pytest.mark.parametrize(
    "bad_snapshot",
    (
        b'{"canary":"RAW-JSON-EXCEPTION-CANARY"',
        b"RAW-UTF8-EXCEPTION-CANARY-\xff",
    ),
)
def test_snapshot_parse_errors_do_not_retain_raw_input_in_exception_chain(
    bad_snapshot: bytes,
) -> None:
    with pytest.raises(GapFamilySnapshotContractError) as raised:
        derive_supplied_family_snapshot_digest(bad_snapshot, _policy())

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_json_parser_recursion_limit_fails_closed_as_contract_error() -> None:
    deeply_nested = (
        b'{"nested":'
        + (b"[" * 100_000)
        + b"0"
        + (b"]" * 100_000)
        + b"}"
    )
    assert len(deeply_nested) < _policy().max_snapshot_bytes

    with pytest.raises(GapFamilySnapshotContractError) as raised:
        derive_supplied_family_snapshot_digest(deeply_nested, _policy())

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_snapshot_requires_canonical_json_and_rejects_duplicate_keys() -> None:
    mapping = _snapshot_mapping()
    noncanonical = json.dumps(mapping, indent=2, sort_keys=True).encode("utf-8")
    with pytest.raises(GapFamilySnapshotContractError):
        derive_supplied_family_snapshot_digest(noncanonical, _policy())

    canonical_text = canonical_json_bytes(mapping).decode("utf-8")
    duplicate = canonical_text.replace(
        '"families":',
        '"families":[],"families":',
        1,
    ).encode("utf-8")
    with pytest.raises(GapFamilySnapshotContractError):
        derive_supplied_family_snapshot_digest(duplicate, _policy())

    nonfinite = canonical_text.replace(
        '"snapshot-local-1"',
        "NaN",
        1,
    ).encode("utf-8")
    with pytest.raises(GapFamilySnapshotContractError):
        derive_supplied_family_snapshot_digest(nonfinite, _policy())


@pytest.mark.parametrize(
    "mutation",
    (
        lambda value: {key: item for key, item in value.items() if key != "snapshot_id"},
        lambda value: {**value, "extra": False},
        lambda value: {**value, "schema_version": "wd.invalid.v1"},
        lambda value: {**value, "snapshot_id": ""},
        lambda value: {**value, "registry_snapshot_digest": "not-a-digest"},
        lambda value: {**value, "families": "not-a-list"},
    ),
)
def test_snapshot_root_shape_and_fields_are_exact(mutation: Any) -> None:
    bad = canonical_json_bytes(mutation(_snapshot_mapping()))
    with pytest.raises(GapFamilySnapshotContractError):
        derive_supplied_family_snapshot_digest(bad, _policy())


@pytest.mark.parametrize(
    "mutation",
    (
        lambda value: {key: item for key, item in value.items() if key != "family_id"},
        lambda value: {**value, "extra": False},
        lambda value: {**value, "family_id": ""},
        lambda value: {**value, "family_kind": "free_form_python"},
        lambda value: {**value, "declared_capability_fingerprint": "not-a-digest"},
        lambda value: {**value, "descriptor_digest": "sha256:abc"},
    ),
)
def test_family_entry_shape_digest_and_allowlist_are_exact(mutation: Any) -> None:
    bad_entry = mutation(_family())
    bad_mapping = _snapshot_mapping([])
    bad_mapping["families"] = [bad_entry]
    bad = canonical_json_bytes(bad_mapping)
    with pytest.raises(GapFamilySnapshotContractError):
        derive_supplied_family_snapshot_digest(bad, _policy())


def test_family_entries_must_be_sorted_and_unique_without_silent_dedup() -> None:
    first = _family(family_id="family-a", family_kind="linear_arithmetic")
    second = _family(family_id="family-b", family_kind="threshold_rule")

    unsorted_mapping = _snapshot_mapping([first, second])
    unsorted_mapping["families"] = [second, first]
    with pytest.raises(GapFamilySnapshotContractError):
        derive_supplied_family_snapshot_digest(
            canonical_json_bytes(unsorted_mapping), _policy()
        )

    duplicate = canonical_json_bytes(_snapshot_mapping([first, first]))
    with pytest.raises(GapFamilySnapshotContractError):
        derive_supplied_family_snapshot_digest(duplicate, _policy())

    duplicate_id_mapping = _snapshot_mapping([])
    duplicate_id_mapping["families"] = [
        _family(
            family_id="family-same",
            family_kind="linear_arithmetic",
            descriptor_digest=_digest("descriptor-a"),
        ),
        _family(
            family_id="family-same",
            family_kind="threshold_rule",
            descriptor_digest=_digest("descriptor-b"),
        ),
    ]
    with pytest.raises(GapFamilySnapshotContractError):
        derive_supplied_family_snapshot_digest(
            canonical_json_bytes(duplicate_id_mapping), _policy()
        )

    duplicate_descriptor_mapping = _snapshot_mapping([])
    shared_descriptor = _digest("shared-descriptor")
    duplicate_descriptor_mapping["families"] = [
        _family(
            family_id="family-a",
            family_kind="linear_arithmetic",
            descriptor_digest=shared_descriptor,
        ),
        _family(
            family_id="family-b",
            family_kind="threshold_rule",
            descriptor_digest=shared_descriptor,
        ),
    ]
    with pytest.raises(GapFamilySnapshotContractError):
        derive_supplied_family_snapshot_digest(
            canonical_json_bytes(duplicate_descriptor_mapping), _policy()
        )


def test_family_entry_order_uses_the_full_policy_bound_sort_key() -> None:
    bounded = _family(
        family_id="family-z",
        family_kind="bounded_interpolation",
    )
    threshold = _family(
        family_id="family-a",
        family_kind="threshold_rule",
    )
    mapping = _snapshot_mapping([])
    mapping["families"] = [bounded, threshold]
    accepted = canonical_json_bytes(mapping)
    assert derive_supplied_family_snapshot_digest(accepted, _policy()).startswith(
        "sha256:"
    )

    mapping["families"] = [threshold, bounded]
    with pytest.raises(GapFamilySnapshotContractError, match="canonically sorted"):
        derive_supplied_family_snapshot_digest(canonical_json_bytes(mapping), _policy())


def test_empty_family_snapshot_is_only_a_supplied_snapshot_no_match() -> None:
    snapshot_utf8 = _snapshot_bytes([])
    receipt = _evaluate(snapshot_utf8=snapshot_utf8)
    assert receipt.disposition is (
        GapFamilySnapshotDisposition.NO_EXACT_DECLARED_CAPABILITY_MATCH_IN_SUPPLIED_SNAPSHOT
    )
    assert receipt.exact_match_count == 0
    assert receipt.matched_family_entry_digest is None


def test_snapshot_resource_bounds_fail_closed() -> None:
    snapshot_utf8 = _snapshot_bytes()

    for changes in (
        {"max_snapshot_bytes": len(snapshot_utf8) - 1},
        {"max_family_entries": 0},
        {"max_json_depth": 2},
        {"max_json_nodes": 4},
    ):
        try:
            policy = _policy(**changes)
        except GapFamilySnapshotContractError:
            continue
        with pytest.raises(GapFamilySnapshotContractError):
            derive_supplied_family_snapshot_digest(snapshot_utf8, policy)

    two_entries = _snapshot_bytes(
        [
            _family(family_id="family-a", family_kind="linear_arithmetic"),
            _family(family_id="family-b", family_kind="threshold_rule"),
        ]
    )
    one_entry_policy = _policy(max_family_entries=1)
    with pytest.raises(GapFamilySnapshotContractError):
        derive_supplied_family_snapshot_digest(two_entries, one_entry_policy)


def test_snapshot_family_entry_absolute_boundary_is_enforced() -> None:
    entries = [
        _family(
            family_id=f"family-{index:04d}",
            family_kind="lookup_table",
            fingerprint=_digest(f"capability-{index}"),
            descriptor_digest=_digest(f"descriptor-{index}"),
        )
        for index in range(4_097)
    ]
    at_limit = _snapshot_bytes(entries[:4_096])
    assert derive_supplied_family_snapshot_digest(at_limit, _policy()).startswith(
        "sha256:"
    )

    above_limit = _snapshot_bytes(entries)
    with pytest.raises(GapFamilySnapshotContractError, match="entry count"):
        derive_supplied_family_snapshot_digest(above_limit, _policy())


def test_snapshot_requires_exact_bytes_not_mutable_or_text_substitutes() -> None:
    snapshot_utf8 = _snapshot_bytes()
    for bad in (snapshot_utf8.decode("utf-8"), bytearray(snapshot_utf8), memoryview(snapshot_utf8)):
        with pytest.raises(GapFamilySnapshotContractError):
            derive_supplied_family_snapshot_digest(bad, _policy())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("plan_field", "bad_value"),
    (
        ("campaign_id", ""),
        ("gap_descriptor_digest", "not-a-digest"),
        ("family_snapshot_digest", "sha256:abc"),
        ("registry_snapshot_digest", None),
        ("resource_policy_digest", "bad"),
        ("matching_policy_digest", _digest("wrong-matching-policy")),
        ("attempt_index", 2),
        ("attempt_budget", 2),
        ("shadow_only", False),
        ("evaluation_only", False),
        ("caller_supplied_snapshot_only", False),
    ),
)
def test_plan_rejects_malformed_or_authority_broadening_fields(
    plan_field: str, bad_value: Any
) -> None:
    gap = _gap()
    policy = _policy()
    snapshot_utf8 = _snapshot_bytes()
    values = {
        "campaign_id": "campaign-c8b-1",
        "gap_descriptor_digest": derive_declared_capability_gap_digest(gap),
        "family_snapshot_digest": derive_supplied_family_snapshot_digest(
            snapshot_utf8, policy
        ),
        "registry_snapshot_digest": json.loads(snapshot_utf8)[
            "registry_snapshot_digest"
        ],
        "resource_policy_digest": policy.policy_digest,
        "matching_policy_digest": GAP_FAMILY_MATCHING_POLICY_DIGEST,
        "attempt_index": 1,
        "attempt_budget": 1,
        "shadow_only": True,
        "evaluation_only": True,
        "caller_supplied_snapshot_only": True,
    }
    values[plan_field] = bad_value
    with pytest.raises(GapFamilySnapshotContractError):
        GapFamilySnapshotPlanV1(**values)


def test_plan_and_request_schema_versions_are_exact() -> None:
    request, _ = _request()
    with pytest.raises(GapFamilySnapshotContractError):
        replace(request.plan, schema_version="wd.invalid.v1")
    with pytest.raises(GapFamilySnapshotContractError):
        GapFamilySnapshotRequestV1(
            plan=request.plan,
            gap=request.gap,
            family_snapshot_utf8=request.family_snapshot_utf8,
            schema_version="wd.invalid.v1",
        )


@pytest.mark.parametrize(
    "plan_changes",
    (
        {"gap_descriptor_digest": _digest("wrong-gap")},
        {"family_snapshot_digest": _digest("wrong-snapshot")},
        {"registry_snapshot_digest": _digest("wrong-registry")},
        {"resource_policy_digest": _digest("wrong-resource-policy")},
    ),
)
def test_request_or_evaluation_refuses_digest_and_policy_mismatch(
    plan_changes: dict[str, Any],
) -> None:
    gap = _gap()
    policy = _policy()
    snapshot_utf8 = _snapshot_bytes()
    plan = _plan(gap, snapshot_utf8, policy, **plan_changes)
    try:
        request = GapFamilySnapshotRequestV1(
            plan=plan,
            gap=gap,
            family_snapshot_utf8=snapshot_utf8,
        )
    except GapFamilySnapshotContractError:
        return
    with pytest.raises(GapFamilySnapshotContractError):
        evaluate_gap_family_snapshot(request, policy=policy)


def test_request_rejects_exact_type_substitutions() -> None:
    request, policy = _request()
    with pytest.raises(GapFamilySnapshotContractError):
        GapFamilySnapshotRequestV1(
            plan=request.plan.to_mapping(),  # type: ignore[arg-type]
            gap=request.gap,
            family_snapshot_utf8=request.family_snapshot_utf8,
        )
    with pytest.raises(GapFamilySnapshotContractError):
        GapFamilySnapshotRequestV1(
            plan=request.plan,
            gap=dataclasses.asdict(request.gap),  # type: ignore[arg-type]
            family_snapshot_utf8=request.family_snapshot_utf8,
        )
    with pytest.raises(GapFamilySnapshotContractError):
        GapFamilySnapshotRequestV1(
            plan=request.plan,
            gap=request.gap,
            family_snapshot_utf8=(
                request.family_snapshot_utf8.decode("utf-8")
            ),  # type: ignore[arg-type]
        )
    assert evaluate_gap_family_snapshot(request, policy=policy) is not None


def test_post_construction_gap_snapshot_plan_and_policy_mutations_are_rechecked() -> None:
    request, policy = _request()
    object.__setattr__(
        request.gap,
        "declared_capability_fingerprint",
        _digest("mutated-gap-capability"),
    )
    with pytest.raises(GapFamilySnapshotContractError):
        evaluate_gap_family_snapshot(request, policy=policy)
    request, policy = _request()
    object.__setattr__(request, "family_snapshot_utf8", _snapshot_bytes(snapshot_id="changed"))
    with pytest.raises(GapFamilySnapshotContractError):
        evaluate_gap_family_snapshot(request, policy=policy)

    request, policy = _request()
    object.__setattr__(request.plan, "family_snapshot_digest", _digest("mutated-plan"))
    with pytest.raises(GapFamilySnapshotContractError):
        evaluate_gap_family_snapshot(request, policy=policy)

    request, policy = _request()
    object.__setattr__(policy, "max_family_entries", 1)
    with pytest.raises(GapFamilySnapshotContractError):
        evaluate_gap_family_snapshot(request, policy=policy)


def test_post_construction_snapshot_type_tamper_cannot_invoke_bytes_callback() -> None:
    class BytesBomb:
        called = False

        def __bytes__(self) -> bytes:
            self.called = True
            raise RuntimeError("caller callback must not run")

    request, policy = _request()
    bomb = BytesBomb()
    object.__setattr__(request, "family_snapshot_utf8", bomb)

    with pytest.raises(GapFamilySnapshotContractError):
        evaluate_gap_family_snapshot(request, policy=policy)
    assert bomb.called is False


def test_public_contract_objects_are_slotted_against_method_shadow_callbacks() -> None:
    request, policy = _request()
    receipt = evaluate_gap_family_snapshot(request, policy=policy)
    assert isinstance(receipt, GapFamilySnapshotReceiptV1)
    called = False

    def callback() -> None:
        nonlocal called
        called = True

    targets = (
        (policy, "to_mapping"),
        (request.gap, "__post_init__"),
        (request.gap, "to_mapping"),
        (request.plan, "to_mapping"),
        (receipt, "__post_init__"),
        (receipt, "_core_mapping"),
        (receipt, "to_mapping"),
    )
    for target, method_name in targets:
        assert not hasattr(target, "__dict__")
        with pytest.raises(AttributeError):
            object.__setattr__(target, method_name, callback)

    assert derive_declared_capability_gap_digest(request.gap).startswith("sha256:")
    assert receipt.to_mapping()["semantic_equivalence_verified"] is False
    assert called is False


def test_receipt_is_deterministic_digest_bound_and_raw_free() -> None:
    raw_canary = "RAW-CAPABILITY-CANARY-DO-NOT-EMIT"
    gap = _gap(gap_id=raw_canary)
    snapshot_utf8 = _snapshot_bytes(
        [_family(family_id=raw_canary)],
        snapshot_id=raw_canary,
    )
    request, policy = _request(gap=gap, snapshot_utf8=snapshot_utf8)
    first = evaluate_gap_family_snapshot(request, policy=policy)
    second = evaluate_gap_family_snapshot(request, policy=policy)
    assert isinstance(first, GapFamilySnapshotReceiptV1)
    assert isinstance(second, GapFamilySnapshotReceiptV1)
    assert first.to_mapping() == second.to_mapping()
    assert first.receipt_digest == second.receipt_digest
    assert _SHA256.fullmatch(first.receipt_digest)

    public_text = repr(first) + json.dumps(first.to_mapping(), sort_keys=True)
    assert raw_canary not in public_text
    assert snapshot_utf8.decode("utf-8") not in public_text
    for forbidden in ("source", "stdout", "stderr", "path", "hostname", "pid"):
        assert forbidden not in first.to_mapping()


def test_every_hard_false_claim_rejects_inflation() -> None:
    receipt = _evaluate()
    mapping = receipt.to_mapping()
    assert _HARD_FALSE_RECEIPT_FIELDS <= mapping.keys()
    assert all(mapping[name] is False for name in _HARD_FALSE_RECEIPT_FIELDS)
    receipt_fields = {field.name for field in dataclasses.fields(receipt)}
    for field_name in sorted(_HARD_FALSE_RECEIPT_FIELDS & receipt_fields):
        with pytest.raises(GapFamilySnapshotContractError):
            replace(receipt, **{field_name: True})


def test_receipt_disposition_count_and_match_digest_cannot_be_mutated() -> None:
    exact = _evaluate()
    with pytest.raises(GapFamilySnapshotContractError):
        replace(exact, exact_match_count=0)
    with pytest.raises(GapFamilySnapshotContractError):
        replace(exact, matched_family_entry_digest=None)
    with pytest.raises(GapFamilySnapshotContractError):
        replace(
            exact,
            disposition=(
                GapFamilySnapshotDisposition.NO_EXACT_DECLARED_CAPABILITY_MATCH_IN_SUPPLIED_SNAPSHOT
            ),
        )
    with pytest.raises(GapFamilySnapshotContractError):
        replace(exact, reason_code=GapFamilySnapshotReasonCode.NO_EXACT_MATCH)
    with pytest.raises(GapFamilySnapshotContractError):
        replace(exact, receipt_digest=_digest("forged-receipt"))
    with pytest.raises(GapFamilySnapshotContractError):
        replace(exact, schema_version="wd.invalid.v1")


@pytest.mark.parametrize(
    ("field_name", "bad_value", "message"),
    (
        ("policy_digest", _digest("unrelated-policy"), "policy relation"),
        ("max_json_nodes", 123, "policy relation"),
        ("campaign_id_digest", _digest("unrelated-campaign"), "plan relation"),
        ("plan_digest", _digest("unrelated-plan"), "plan relation"),
        ("request_digest", _digest("unrelated-request"), "request relation"),
    ),
)
def test_receipt_rejects_outer_resealed_policy_plan_and_request_relation_forges(
    field_name: str,
    bad_value: Any,
    message: str,
) -> None:
    with pytest.raises(GapFamilySnapshotContractError, match=message):
        _reseal_receipt(_evaluate(), **{field_name: bad_value})


@pytest.mark.parametrize(
    "policy_changes",
    (
        {"max_family_entries": 0},
        {"max_snapshot_bytes": 128},
        {"max_json_depth": 3},
        {"max_json_nodes": 9},
    ),
)
def test_receipt_rejects_cascade_resealed_observations_outside_policy(
    policy_changes: dict[str, int],
) -> None:
    receipt = _evaluate()
    with pytest.raises(GapFamilySnapshotContractError, match="exceeds receipt policy"):
        _cascade_reseal_policy(receipt, **policy_changes)


@pytest.mark.parametrize(
    "policy_changes",
    (
        {"max_json_depth": 1},
        {"max_json_nodes": 4},
    ),
)
def test_empty_snapshot_receipt_rejects_impossible_structural_policy_reseal(
    policy_changes: dict[str, int],
) -> None:
    receipt = _evaluate(snapshot_utf8=_snapshot_bytes([]))
    with pytest.raises(GapFamilySnapshotContractError, match="exceeds receipt policy"):
        _cascade_reseal_policy(receipt, **policy_changes)


@pytest.mark.parametrize(
    "snapshot_utf8",
    (
        _snapshot_bytes([]),
        _snapshot_bytes(),
    ),
)
def test_receipt_rejects_outer_resealed_impossible_snapshot_byte_count(
    snapshot_utf8: bytes,
) -> None:
    receipt = _evaluate(snapshot_utf8=snapshot_utf8)
    minimum, maximum = snapshot_module._canonical_snapshot_byte_bounds(
        receipt.family_entry_count
    )
    assert snapshot_module._canonical_snapshot_byte_bounds(0) == (198, 325)
    assert snapshot_module._canonical_snapshot_byte_bounds(1) == (446, 714)
    assert minimum <= receipt.family_snapshot_byte_count <= maximum

    for impossible_byte_count in (minimum - 1, maximum + 1):
        with pytest.raises(GapFamilySnapshotContractError, match="impossible"):
            _reseal_receipt(
                receipt,
                family_snapshot_byte_count=impossible_byte_count,
            )


def test_snapshot_byte_minimum_accounts_for_unique_family_ids_after_62() -> None:
    one_character_ids = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    assert len(one_character_ids) == 62
    entries = [
        _family(
            family_id=family_id,
            family_kind="lookup_table",
        )
        for family_id in (*one_character_ids, "00")
    ]
    snapshot_utf8 = _snapshot_bytes(entries, snapshot_id="a")
    minimum, _ = snapshot_module._canonical_snapshot_byte_bounds(len(entries))
    assert minimum == 15_885
    assert len(snapshot_utf8) == minimum

    receipt = _evaluate(snapshot_utf8=snapshot_utf8)
    assert receipt.family_entry_count == 63
    assert receipt.family_snapshot_byte_count == minimum
    with pytest.raises(GapFamilySnapshotContractError, match="impossible"):
        _reseal_receipt(receipt, family_snapshot_byte_count=minimum - 1)


def test_plan_and_receipt_use_raw_free_campaign_digest_relation() -> None:
    request, policy = _request()
    receipt = evaluate_gap_family_snapshot(request, policy=policy)
    assert isinstance(receipt, GapFamilySnapshotReceiptV1)
    plan_mapping = request.plan.to_mapping()
    receipt_mapping = receipt.to_mapping()

    assert "campaign_id" not in plan_mapping
    assert plan_mapping["campaign_id_digest"] == request.plan.campaign_id_digest
    assert receipt_mapping["campaign_id_digest"] == request.plan.campaign_id_digest
    assert request.plan.campaign_id not in json.dumps(receipt_mapping, sort_keys=True)


def test_module_has_no_forbidden_import_or_io_authority_seam() -> None:
    source = inspect.getsource(snapshot_module)
    tree = ast.parse(source)
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0])

    assert imported_roots.isdisjoint(
        {
            "asyncio",
            "httpx",
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
    )

    forbidden_symbols = {
        "build_understanding_coding_candidate",
        "execute_artifact",
        "open",
        "Path",
        "SolverFamilyRegistry",
        "ControlPlaneDB",
        "register",
        "route",
        "promote",
    }
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    called_attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert forbidden_symbols.isdisjoint(called_names | called_attributes)
