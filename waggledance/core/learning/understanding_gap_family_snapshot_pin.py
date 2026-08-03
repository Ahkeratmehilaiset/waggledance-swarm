# SPDX-License-Identifier: Apache-2.0
"""Default-off equality accounting against supplied expected C8b digests.

C8c compares the family-snapshot and registry-snapshot digests carried by one
internally self-consistent C8b receipt with a separate, caller-supplied
expected-digest object.  It proves only the resulting exact equality relation.

Neither the expected values nor the C8b receipt are authenticated here.  A
self-minted receipt and matching expectations can therefore satisfy the local
relation.  This module does not rehash snapshot bytes, establish an external
pin, consult a registry, or authorize reuse, generation, execution, routing,
promotion, or writes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from enum import Enum
from typing import Any

from waggledance.core.learning.understanding_gap_family_snapshot import (
    GAP_FAMILY_SNAPSHOT_RECEIPT_SCHEMA,
    GapFamilySnapshotContractError,
    GapFamilySnapshotDisposition,
    GapFamilySnapshotReceiptV1,
)
from waggledance.core.magma.canonical import sha256_digest


GAP_FAMILY_SNAPSHOT_EXPECTED_DIGEST_PIN_SCHEMA = (
    "wd.understanding.gap_family_snapshot_expected_digest_pin.v1"
)
GAP_FAMILY_SNAPSHOT_PIN_POLICY_SCHEMA = (
    "wd.understanding.gap_family_snapshot_pin_policy.v1"
)
GAP_FAMILY_SNAPSHOT_PIN_REQUEST_SCHEMA = (
    "wd.understanding.gap_family_snapshot_pin_request.v1"
)
GAP_FAMILY_SNAPSHOT_PIN_RECEIPT_SCHEMA = (
    "wd.understanding.gap_family_snapshot_pin_receipt.v1"
)

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")

_RELATION_POLICY = {
    "schema": "wd.understanding.gap_family_snapshot_pin_relation_policy.v1",
    "source_receipt_schema": GAP_FAMILY_SNAPSHOT_RECEIPT_SCHEMA,
    "expectation_source": "caller_supplied_keyword_only",
    "family_comparison": (
        "source_family_snapshot_digest_equals_expected_family_snapshot_digest"
    ),
    "registry_comparison": (
        "source_registry_snapshot_digest_equals_expected_registry_snapshot_digest"
    ),
    "comparison_operator": "exact_public_digest_equality",
    "source_c8b_refused_disposition_dominates": True,
    "snapshot_bytes_rehashed": False,
    "external_pin_provenance_verified": False,
    "source_receipt_origin_authenticated": False,
    "c8b_snapshot_evaluator_invoked": False,
    "authority_granted": False,
}
GAP_FAMILY_SNAPSHOT_PIN_RELATION_POLICY_DIGEST = sha256_digest(
    _RELATION_POLICY
)


class GapFamilySnapshotPinContractError(ValueError):
    """A value is outside the C8c supplied-expectation contract."""


class GapFamilySnapshotPinMode(str, Enum):
    OFF = "off"
    STATIC_SHADOW = "static_shadow"


class GapFamilySnapshotPinDisposition(str, Enum):
    REFUSED = "refused"
    EXPECTED_DIGEST_RELATION_HOLDS = "expected_digest_relation_holds"
    EXPECTED_DIGEST_RELATION_MISMATCH = "expected_digest_relation_mismatch"


class GapFamilySnapshotPinReasonCode(str, Enum):
    SOURCE_C8B_RECEIPT_REFUSED = "source_c8b_receipt_refused"
    BOTH_EXPECTED_DIGESTS_MATCH = "both_expected_digests_match"
    FAMILY_SNAPSHOT_DIGEST_MISMATCH = "family_snapshot_digest_mismatch"
    REGISTRY_SNAPSHOT_DIGEST_MISMATCH = "registry_snapshot_digest_mismatch"
    BOTH_EXPECTED_DIGESTS_MISMATCH = "both_expected_digests_mismatch"


def _require_sha256(value: object, label: str) -> str:
    if type(value) is not str or not _SHA256.fullmatch(value):
        raise GapFamilySnapshotPinContractError(
            f"{label} must be a canonical sha256 digest"
        )
    return value


@dataclass(frozen=True, slots=True)
class GapFamilySnapshotPinPolicyV1:
    mode: GapFamilySnapshotPinMode = GapFamilySnapshotPinMode.OFF
    relation_policy_digest: str = GAP_FAMILY_SNAPSHOT_PIN_RELATION_POLICY_DIGEST
    schema_version: str = GAP_FAMILY_SNAPSHOT_PIN_POLICY_SCHEMA

    def __post_init__(self) -> None:
        if type(self) is not GapFamilySnapshotPinPolicyV1:
            raise GapFamilySnapshotPinContractError("policy exact type required")
        if type(self.schema_version) is not str or (
            self.schema_version != GAP_FAMILY_SNAPSHOT_PIN_POLICY_SCHEMA
        ):
            raise GapFamilySnapshotPinContractError("policy schema_version refused")
        if type(self.mode) is not GapFamilySnapshotPinMode:
            raise GapFamilySnapshotPinContractError(
                "mode must be an exact GapFamilySnapshotPinMode"
            )
        _require_sha256(self.relation_policy_digest, "relation_policy_digest")
        if (
            self.relation_policy_digest
            != GAP_FAMILY_SNAPSHOT_PIN_RELATION_POLICY_DIGEST
        ):
            raise GapFamilySnapshotPinContractError(
                "relation policy digest refused"
            )

    def to_mapping(self) -> dict[str, Any]:
        GapFamilySnapshotPinPolicyV1.__post_init__(self)
        return {
            "schema_version": self.schema_version,
            "mode": self.mode.value,
            "relation_policy_digest": self.relation_policy_digest,
        }

    @property
    def policy_digest(self) -> str:
        return sha256_digest(
            {
                "domain": (
                    "wd.understanding.gap_family_snapshot_pin_policy.digest.v1"
                ),
                **GapFamilySnapshotPinPolicyV1.to_mapping(self),
            }
        )


@dataclass(frozen=True, slots=True)
class GapFamilySnapshotExpectedDigestPinV1:
    expected_family_snapshot_digest: str
    expected_registry_snapshot_digest: str
    target_receipt_schema_version: str = GAP_FAMILY_SNAPSHOT_RECEIPT_SCHEMA
    schema_version: str = GAP_FAMILY_SNAPSHOT_EXPECTED_DIGEST_PIN_SCHEMA

    def __post_init__(self) -> None:
        if type(self) is not GapFamilySnapshotExpectedDigestPinV1:
            raise GapFamilySnapshotPinContractError(
                "expected digest pin exact type required"
            )
        if type(self.schema_version) is not str or (
            self.schema_version != GAP_FAMILY_SNAPSHOT_EXPECTED_DIGEST_PIN_SCHEMA
        ):
            raise GapFamilySnapshotPinContractError(
                "expected digest pin schema_version refused"
            )
        if type(self.target_receipt_schema_version) is not str or (
            self.target_receipt_schema_version
            != GAP_FAMILY_SNAPSHOT_RECEIPT_SCHEMA
        ):
            raise GapFamilySnapshotPinContractError(
                "expected digest pin target receipt schema refused"
            )
        _require_sha256(
            self.expected_family_snapshot_digest,
            "expected_family_snapshot_digest",
        )
        _require_sha256(
            self.expected_registry_snapshot_digest,
            "expected_registry_snapshot_digest",
        )

    def to_mapping(self) -> dict[str, Any]:
        GapFamilySnapshotExpectedDigestPinV1.__post_init__(self)
        return {
            "schema_version": self.schema_version,
            "target_receipt_schema_version": self.target_receipt_schema_version,
            "expected_family_snapshot_digest": (
                self.expected_family_snapshot_digest
            ),
            "expected_registry_snapshot_digest": (
                self.expected_registry_snapshot_digest
            ),
        }

    @property
    def pin_digest(self) -> str:
        return sha256_digest(
            {
                "domain": (
                    "wd.understanding.gap_family_snapshot_expected_digest_pin."
                    "digest.v1"
                ),
                **GapFamilySnapshotExpectedDigestPinV1.to_mapping(self),
            }
        )


def _copy_source_c8b_receipt(
    value: object,
) -> GapFamilySnapshotReceiptV1:
    if type(value) is not GapFamilySnapshotReceiptV1:
        raise GapFamilySnapshotPinContractError(
            "source_c8b_receipt must be an exact GapFamilySnapshotReceiptV1"
        )
    try:
        GapFamilySnapshotReceiptV1.__post_init__(value)
        copied = GapFamilySnapshotReceiptV1(
            **{
                item.name: getattr(value, item.name)
                for item in fields(GapFamilySnapshotReceiptV1)
            }
        )
    except (
        AttributeError,
        GapFamilySnapshotContractError,
        TypeError,
        ValueError,
    ):
        raise GapFamilySnapshotPinContractError(
            "source C8b receipt internal relations refused"
        ) from None
    return copied


def _copy_expected_pin(
    value: object,
) -> GapFamilySnapshotExpectedDigestPinV1:
    if type(value) is not GapFamilySnapshotExpectedDigestPinV1:
        raise GapFamilySnapshotPinContractError(
            "expected_pin must be an exact GapFamilySnapshotExpectedDigestPinV1"
        )
    try:
        GapFamilySnapshotExpectedDigestPinV1.__post_init__(value)
        copied = GapFamilySnapshotExpectedDigestPinV1(
            **{
                item.name: getattr(value, item.name)
                for item in fields(GapFamilySnapshotExpectedDigestPinV1)
            }
        )
    except (AttributeError, TypeError, ValueError):
        raise GapFamilySnapshotPinContractError(
            "expected digest pin refused"
        ) from None
    return copied


@dataclass(frozen=True, repr=False, slots=True)
class GapFamilySnapshotPinRequestV1:
    source_c8b_receipt: GapFamilySnapshotReceiptV1
    schema_version: str = GAP_FAMILY_SNAPSHOT_PIN_REQUEST_SCHEMA

    def __post_init__(self) -> None:
        if type(self) is not GapFamilySnapshotPinRequestV1:
            raise GapFamilySnapshotPinContractError("request exact type required")
        if type(self.schema_version) is not str or (
            self.schema_version != GAP_FAMILY_SNAPSHOT_PIN_REQUEST_SCHEMA
        ):
            raise GapFamilySnapshotPinContractError(
                "request schema_version refused"
            )
        _copy_source_c8b_receipt(self.source_c8b_receipt)


def _derive_request_digest(
    *,
    policy_digest: str,
    relation_policy_digest: str,
    expected_pin_digest: str,
    source_c8b_receipt_digest: str,
) -> str:
    return sha256_digest(
        {
            "domain": "wd.understanding.gap_family_snapshot_pin_request.digest.v1",
            "schema_version": GAP_FAMILY_SNAPSHOT_PIN_REQUEST_SCHEMA,
            "policy_digest": policy_digest,
            "relation_policy_digest": relation_policy_digest,
            "expected_pin_digest": expected_pin_digest,
            "source_c8b_receipt_digest": source_c8b_receipt_digest,
        }
    )


_TRUE_RECEIPT_FIELDS = (
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
)

_FALSE_RECEIPT_FIELDS = (
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
    "catalog_authenticity_verified",
    "catalog_freshness_verified",
    "catalog_completeness_verified",
    "registry_snapshot_identity_independently_verified",
    "semantic_equivalence_verified",
    "semantic_deduplication_verified",
    "global_deduplication_verified",
    "reuse_eligibility_claimed",
    "build_eligibility_claimed",
    "family_novelty_independently_verified",
    "new_family_need_independently_verified",
    "existing_family_deduplication_independently_verified",
    "family_review_status_independently_verified",
    "prior_attempt_history_consulted",
    "cross_campaign_single_attempt_enforced",
    "generation_authorized",
    "independent_verification_applied",
    "genesis_origin_independently_verified",
    "hex_cell_binding_independently_verified",
    "echo_chamber_absence_verified",
    "scalability_50000_demonstrated",
    "provider_invoked",
    "builder_host_invoked",
    "c8b_snapshot_evaluator_invoked",
    "c8a_authorized",
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
)


@dataclass(frozen=True, slots=True)
class GapFamilySnapshotPinReceiptV1:
    policy_digest: str
    relation_policy_digest: str
    expected_pin: GapFamilySnapshotExpectedDigestPinV1
    expected_pin_digest: str
    source_c8b_receipt: GapFamilySnapshotReceiptV1
    source_c8b_receipt_digest: str
    request_digest: str
    family_snapshot_digest_equal_to_supplied_expectation: bool | None
    registry_snapshot_digest_equal_to_supplied_expectation: bool | None
    disposition: GapFamilySnapshotPinDisposition
    reason_code: GapFamilySnapshotPinReasonCode
    receipt_digest: str
    schema_version: str = GAP_FAMILY_SNAPSHOT_PIN_RECEIPT_SCHEMA
    evaluation_only: bool = True
    shadow_only: bool = True
    static_accounting_only: bool = True
    raw_material_omitted: bool = True
    exact_expected_digest_comparison_only: bool = True
    separate_expected_digest_object_required: bool = True
    caller_supplied_expectations_only: bool = True
    source_c8b_receipt_internal_relations_rechecked: bool = True
    c8b_snapshot_evaluator_not_invoked: bool = True
    c8a_not_invoked: bool = True
    c7_not_invoked: bool = True
    no_side_effects_in_module: bool = True
    snapshot_externally_pinned: bool = False
    external_pin_provenance_verified: bool = False
    pin_precommit_independently_verified: bool = False
    pin_origin_authenticated: bool = False
    pin_key_custody_independently_verified: bool = False
    hmac_verified_in_process: bool = False
    signature_verified: bool = False
    snapshot_bytes_rehashed: bool = False
    source_c8b_receipt_origin_authenticated: bool = False
    receipt_origin_authenticated: bool = False
    catalog_authenticity_verified: bool = False
    catalog_freshness_verified: bool = False
    catalog_completeness_verified: bool = False
    registry_snapshot_identity_independently_verified: bool = False
    semantic_equivalence_verified: bool = False
    semantic_deduplication_verified: bool = False
    global_deduplication_verified: bool = False
    reuse_eligibility_claimed: bool = False
    build_eligibility_claimed: bool = False
    family_novelty_independently_verified: bool = False
    new_family_need_independently_verified: bool = False
    existing_family_deduplication_independently_verified: bool = False
    family_review_status_independently_verified: bool = False
    prior_attempt_history_consulted: bool = False
    cross_campaign_single_attempt_enforced: bool = False
    generation_authorized: bool = False
    independent_verification_applied: bool = False
    genesis_origin_independently_verified: bool = False
    hex_cell_binding_independently_verified: bool = False
    echo_chamber_absence_verified: bool = False
    scalability_50000_demonstrated: bool = False
    provider_invoked: bool = False
    builder_host_invoked: bool = False
    c8b_snapshot_evaluator_invoked: bool = False
    c8a_authorized: bool = False
    c8a_invoked: bool = False
    c7_execution_requested: bool = False
    candidate_code_executed: bool = False
    candidate_tests_executed: bool = False
    subprocess_spawned: bool = False
    network_accessed: bool = False
    os_sandbox_applied: bool = False
    hive_commit_applied: bool = False
    magma_write_applied: bool = False
    registry_read_applied: bool = False
    registry_write_requested: bool = False
    routing_influence_requested: bool = False
    solver_promotion_requested: bool = False
    runtime_authority_requested: bool = False
    product_external_system_writes_requested: bool = False

    def _core_mapping(self) -> dict[str, Any]:
        if type(self) is not GapFamilySnapshotPinReceiptV1:
            raise GapFamilySnapshotPinContractError("receipt exact type required")
        result: dict[str, Any] = {}
        for item in fields(GapFamilySnapshotPinReceiptV1):
            if item.name == "receipt_digest":
                continue
            value = getattr(self, item.name)
            if type(value) is GapFamilySnapshotExpectedDigestPinV1:
                value = GapFamilySnapshotExpectedDigestPinV1.to_mapping(value)
            elif type(value) is GapFamilySnapshotReceiptV1:
                value = GapFamilySnapshotReceiptV1.to_mapping(value)
            elif isinstance(value, Enum):
                value = value.value
            result[item.name] = value
        return result

    def __post_init__(self) -> None:
        if type(self) is not GapFamilySnapshotPinReceiptV1:
            raise GapFamilySnapshotPinContractError("receipt exact type required")
        if type(self.schema_version) is not str or (
            self.schema_version != GAP_FAMILY_SNAPSHOT_PIN_RECEIPT_SCHEMA
        ):
            raise GapFamilySnapshotPinContractError(
                "receipt schema_version refused"
            )
        for name in (
            "policy_digest",
            "relation_policy_digest",
            "expected_pin_digest",
            "source_c8b_receipt_digest",
            "request_digest",
            "receipt_digest",
        ):
            _require_sha256(getattr(self, name), name)
        if (
            self.relation_policy_digest
            != GAP_FAMILY_SNAPSHOT_PIN_RELATION_POLICY_DIGEST
        ):
            raise GapFamilySnapshotPinContractError(
                "receipt relation policy refused"
            )

        expected_policy = GapFamilySnapshotPinPolicyV1(
            mode=GapFamilySnapshotPinMode.STATIC_SHADOW
        )
        if self.policy_digest != expected_policy.policy_digest:
            raise GapFamilySnapshotPinContractError(
                "receipt policy relation mismatch"
            )

        expected_pin = _copy_expected_pin(self.expected_pin)
        if self.expected_pin_digest != expected_pin.pin_digest:
            raise GapFamilySnapshotPinContractError(
                "receipt expected pin relation mismatch"
            )
        source_receipt = _copy_source_c8b_receipt(self.source_c8b_receipt)
        if self.source_c8b_receipt_digest != source_receipt.receipt_digest:
            raise GapFamilySnapshotPinContractError(
                "receipt source C8b digest relation mismatch"
            )
        expected_request_digest = _derive_request_digest(
            policy_digest=self.policy_digest,
            relation_policy_digest=self.relation_policy_digest,
            expected_pin_digest=self.expected_pin_digest,
            source_c8b_receipt_digest=self.source_c8b_receipt_digest,
        )
        if self.request_digest != expected_request_digest:
            raise GapFamilySnapshotPinContractError(
                "receipt request relation mismatch"
            )
        if type(self.disposition) is not GapFamilySnapshotPinDisposition:
            raise GapFamilySnapshotPinContractError(
                "receipt disposition refused"
            )
        if type(self.reason_code) is not GapFamilySnapshotPinReasonCode:
            raise GapFamilySnapshotPinContractError("receipt reason_code refused")

        if source_receipt.disposition is GapFamilySnapshotDisposition.REFUSED:
            expected_relation = (
                None,
                None,
                GapFamilySnapshotPinDisposition.REFUSED,
                GapFamilySnapshotPinReasonCode.SOURCE_C8B_RECEIPT_REFUSED,
            )
        else:
            family_matches = (
                source_receipt.family_snapshot_digest
                == expected_pin.expected_family_snapshot_digest
            )
            registry_matches = (
                source_receipt.registry_snapshot_digest
                == expected_pin.expected_registry_snapshot_digest
            )
            if family_matches and registry_matches:
                disposition = (
                    GapFamilySnapshotPinDisposition.EXPECTED_DIGEST_RELATION_HOLDS
                )
                reason = (
                    GapFamilySnapshotPinReasonCode.BOTH_EXPECTED_DIGESTS_MATCH
                )
            elif not family_matches and registry_matches:
                disposition = (
                    GapFamilySnapshotPinDisposition.EXPECTED_DIGEST_RELATION_MISMATCH
                )
                reason = (
                    GapFamilySnapshotPinReasonCode.FAMILY_SNAPSHOT_DIGEST_MISMATCH
                )
            elif family_matches and not registry_matches:
                disposition = (
                    GapFamilySnapshotPinDisposition.EXPECTED_DIGEST_RELATION_MISMATCH
                )
                reason = (
                    GapFamilySnapshotPinReasonCode.REGISTRY_SNAPSHOT_DIGEST_MISMATCH
                )
            else:
                disposition = (
                    GapFamilySnapshotPinDisposition.EXPECTED_DIGEST_RELATION_MISMATCH
                )
                reason = (
                    GapFamilySnapshotPinReasonCode.BOTH_EXPECTED_DIGESTS_MISMATCH
                )
            expected_relation = (
                family_matches,
                registry_matches,
                disposition,
                reason,
            )
        for value, label in (
            (
                self.family_snapshot_digest_equal_to_supplied_expectation,
                "family snapshot equality",
            ),
            (
                self.registry_snapshot_digest_equal_to_supplied_expectation,
                "registry snapshot equality",
            ),
        ):
            if value is not None and type(value) is not bool:
                raise GapFamilySnapshotPinContractError(
                    f"{label} must be an exact bool or null"
                )
        actual_relation = (
            self.family_snapshot_digest_equal_to_supplied_expectation,
            self.registry_snapshot_digest_equal_to_supplied_expectation,
            self.disposition,
            self.reason_code,
        )
        if actual_relation != expected_relation:
            raise GapFamilySnapshotPinContractError(
                "receipt equality/disposition relation mismatch"
            )

        for name in _TRUE_RECEIPT_FIELDS:
            if getattr(self, name) is not True:
                raise GapFamilySnapshotPinContractError(
                    f"{name} must be literal true"
                )
        for name in _FALSE_RECEIPT_FIELDS:
            if getattr(self, name) is not False:
                raise GapFamilySnapshotPinContractError(
                    f"{name} must be literal false"
                )
        expected_receipt_digest = sha256_digest(
            {
                "domain": (
                    "wd.understanding.gap_family_snapshot_pin_receipt.digest.v1"
                ),
                **GapFamilySnapshotPinReceiptV1._core_mapping(self),
            }
        )
        if self.receipt_digest != expected_receipt_digest:
            raise GapFamilySnapshotPinContractError(
                "receipt digest does not match fields"
            )

    def to_mapping(self) -> dict[str, Any]:
        GapFamilySnapshotPinReceiptV1.__post_init__(self)
        return {
            **GapFamilySnapshotPinReceiptV1._core_mapping(self),
            "receipt_digest": self.receipt_digest,
        }


def _snapshot_policy(
    policy: GapFamilySnapshotPinPolicyV1,
) -> GapFamilySnapshotPinPolicyV1:
    GapFamilySnapshotPinPolicyV1.__post_init__(policy)
    return GapFamilySnapshotPinPolicyV1(
        mode=policy.mode,
        relation_policy_digest=policy.relation_policy_digest,
        schema_version=policy.schema_version,
    )


def _snapshot_request(
    request: GapFamilySnapshotPinRequestV1,
) -> GapFamilySnapshotPinRequestV1:
    GapFamilySnapshotPinRequestV1.__post_init__(request)
    return GapFamilySnapshotPinRequestV1(
        source_c8b_receipt=_copy_source_c8b_receipt(
            request.source_c8b_receipt
        ),
        schema_version=request.schema_version,
    )


def _receipt_core(values: dict[str, Any]) -> dict[str, Any]:
    return {
        **values,
        "schema_version": GAP_FAMILY_SNAPSHOT_PIN_RECEIPT_SCHEMA,
        **{name: True for name in _TRUE_RECEIPT_FIELDS},
        **{name: False for name in _FALSE_RECEIPT_FIELDS},
    }


def _normalize_receipt_core(values: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in values.items():
        if type(value) is GapFamilySnapshotExpectedDigestPinV1:
            value = GapFamilySnapshotExpectedDigestPinV1.to_mapping(value)
        elif type(value) is GapFamilySnapshotReceiptV1:
            value = GapFamilySnapshotReceiptV1.to_mapping(value)
        elif isinstance(value, Enum):
            value = value.value
        result[name] = value
    return result


def _make_receipt(values: dict[str, Any]) -> GapFamilySnapshotPinReceiptV1:
    core = _receipt_core(values)
    receipt_digest = sha256_digest(
        {
            "domain": "wd.understanding.gap_family_snapshot_pin_receipt.digest.v1",
            **_normalize_receipt_core(core),
        }
    )
    return GapFamilySnapshotPinReceiptV1(
        **values,
        receipt_digest=receipt_digest,
    )


def evaluate_gap_family_snapshot_pin(
    request: GapFamilySnapshotPinRequestV1 | None = None,
    *,
    expected_pin: GapFamilySnapshotExpectedDigestPinV1 | None = None,
    policy: GapFamilySnapshotPinPolicyV1 | None = None,
) -> GapFamilySnapshotPinReceiptV1 | None:
    """Compare a C8b receipt with separately supplied expected digests.

    OFF returns before inspecting ``request`` or ``expected_pin``.  A holding
    relation is only equality between supplied public digests; it is not proof
    of their provenance, authenticity, freshness, or completeness.
    """

    if policy is None:
        policy = GapFamilySnapshotPinPolicyV1()
    elif type(policy) is not GapFamilySnapshotPinPolicyV1:
        raise GapFamilySnapshotPinContractError(
            "policy must be an exact GapFamilySnapshotPinPolicyV1"
        )
    if policy.mode is GapFamilySnapshotPinMode.OFF:
        return None
    policy = _snapshot_policy(policy)
    if policy.mode is not GapFamilySnapshotPinMode.STATIC_SHADOW:
        raise GapFamilySnapshotPinContractError("unsupported C8c mode")
    if type(request) is not GapFamilySnapshotPinRequestV1:
        raise GapFamilySnapshotPinContractError(
            "STATIC_SHADOW requires an exact GapFamilySnapshotPinRequestV1"
        )
    request = _snapshot_request(request)
    expected_pin_copy = _copy_expected_pin(expected_pin)
    source_receipt = request.source_c8b_receipt

    if source_receipt.disposition is GapFamilySnapshotDisposition.REFUSED:
        family_matches: bool | None = None
        registry_matches: bool | None = None
        disposition = GapFamilySnapshotPinDisposition.REFUSED
        reason = GapFamilySnapshotPinReasonCode.SOURCE_C8B_RECEIPT_REFUSED
    else:
        family_matches = (
            source_receipt.family_snapshot_digest
            == expected_pin_copy.expected_family_snapshot_digest
        )
        registry_matches = (
            source_receipt.registry_snapshot_digest
            == expected_pin_copy.expected_registry_snapshot_digest
        )
        if family_matches and registry_matches:
            disposition = (
                GapFamilySnapshotPinDisposition.EXPECTED_DIGEST_RELATION_HOLDS
            )
            reason = GapFamilySnapshotPinReasonCode.BOTH_EXPECTED_DIGESTS_MATCH
        elif not family_matches and registry_matches:
            disposition = (
                GapFamilySnapshotPinDisposition.EXPECTED_DIGEST_RELATION_MISMATCH
            )
            reason = (
                GapFamilySnapshotPinReasonCode.FAMILY_SNAPSHOT_DIGEST_MISMATCH
            )
        elif family_matches and not registry_matches:
            disposition = (
                GapFamilySnapshotPinDisposition.EXPECTED_DIGEST_RELATION_MISMATCH
            )
            reason = (
                GapFamilySnapshotPinReasonCode.REGISTRY_SNAPSHOT_DIGEST_MISMATCH
            )
        else:
            disposition = (
                GapFamilySnapshotPinDisposition.EXPECTED_DIGEST_RELATION_MISMATCH
            )
            reason = GapFamilySnapshotPinReasonCode.BOTH_EXPECTED_DIGESTS_MISMATCH

    expected_pin_digest = expected_pin_copy.pin_digest
    request_digest = _derive_request_digest(
        policy_digest=policy.policy_digest,
        relation_policy_digest=policy.relation_policy_digest,
        expected_pin_digest=expected_pin_digest,
        source_c8b_receipt_digest=source_receipt.receipt_digest,
    )
    return _make_receipt(
        {
            "policy_digest": policy.policy_digest,
            "relation_policy_digest": policy.relation_policy_digest,
            "expected_pin": expected_pin_copy,
            "expected_pin_digest": expected_pin_digest,
            "source_c8b_receipt": source_receipt,
            "source_c8b_receipt_digest": source_receipt.receipt_digest,
            "request_digest": request_digest,
            "family_snapshot_digest_equal_to_supplied_expectation": (
                family_matches
            ),
            "registry_snapshot_digest_equal_to_supplied_expectation": (
                registry_matches
            ),
            "disposition": disposition,
            "reason_code": reason,
        }
    )


__all__ = [
    "GAP_FAMILY_SNAPSHOT_EXPECTED_DIGEST_PIN_SCHEMA",
    "GAP_FAMILY_SNAPSHOT_PIN_POLICY_SCHEMA",
    "GAP_FAMILY_SNAPSHOT_PIN_RECEIPT_SCHEMA",
    "GAP_FAMILY_SNAPSHOT_PIN_RELATION_POLICY_DIGEST",
    "GAP_FAMILY_SNAPSHOT_PIN_REQUEST_SCHEMA",
    "GapFamilySnapshotExpectedDigestPinV1",
    "GapFamilySnapshotPinContractError",
    "GapFamilySnapshotPinDisposition",
    "GapFamilySnapshotPinMode",
    "GapFamilySnapshotPinPolicyV1",
    "GapFamilySnapshotPinReasonCode",
    "GapFamilySnapshotPinReceiptV1",
    "GapFamilySnapshotPinRequestV1",
    "evaluate_gap_family_snapshot_pin",
]
