# SPDX-License-Identifier: Apache-2.0
"""Default-off supplied family-snapshot accounting for one declared gap.

C8b compares one caller-declared capability fingerprint with a bounded,
caller-supplied snapshot of declared family metadata.  It reports
only exact fingerprint equality inside that supplied snapshot.  It does not
prove semantic coverage, family reuse eligibility, catalog completeness,
family novelty, or a need to generate code.

The module is deliberately pure and inert.  It uses MAGMA's pure canonical
hashing helper, but does not import or invoke C8a, C7, BuilderHost, providers,
registries, MAGMA ledger/storage paths, routing, promotion, or runtime
components.  It parses no source and performs no filesystem, network,
subprocess, registry, or external-system operation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, fields
from enum import Enum
from typing import Any

from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest


GAP_FAMILY_SNAPSHOT_PLAN_SCHEMA = (
    "wd.understanding.gap_family_snapshot_plan.v1"
)
GAP_FAMILY_SNAPSHOT_REQUEST_SCHEMA = (
    "wd.understanding.gap_family_snapshot_request.v1"
)
GAP_FAMILY_SNAPSHOT_RECEIPT_SCHEMA = (
    "wd.understanding.gap_family_snapshot_receipt.v1"
)
DECLARED_CAPABILITY_GAP_SCHEMA = (
    "wd.understanding.declared_capability_gap.v1"
)
SUPPLIED_FAMILY_SNAPSHOT_SCHEMA = (
    "wd.understanding.supplied_family_snapshot.v1"
)

_DECLARED_CLOSED_FAMILY_KINDS: tuple[str, ...] = (
    "scalar_unit_conversion",
    "lookup_table",
    "threshold_rule",
    "interval_bucket_classifier",
    "linear_arithmetic",
    "bounded_interpolation",
)
DECLARED_CLOSED_FAMILY_KINDS: tuple[str, ...] = (
    _DECLARED_CLOSED_FAMILY_KINDS
)

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")

_ABSOLUTE_MAX_SNAPSHOT_BYTES = 2_097_152
_ABSOLUTE_MAX_FAMILY_ENTRIES = 4_096
_ABSOLUTE_MAX_JSON_DEPTH = 6
_ABSOLUTE_MAX_JSON_NODES = 32_768

_CANONICAL_DIGEST_TEMPLATE = "sha256:" + ("0" * 64)
_MIN_EMPTY_SNAPSHOT_BYTES = len(
    canonical_json_bytes(
        {
            "schema_version": SUPPLIED_FAMILY_SNAPSHOT_SCHEMA,
            "snapshot_id": "a",
            "registry_snapshot_digest": _CANONICAL_DIGEST_TEMPLATE,
            "families": [],
        }
    )
)
_MAX_EMPTY_SNAPSHOT_BYTES = len(
    canonical_json_bytes(
        {
            "schema_version": SUPPLIED_FAMILY_SNAPSHOT_SCHEMA,
            "snapshot_id": "a" * 128,
            "registry_snapshot_digest": _CANONICAL_DIGEST_TEMPLATE,
            "families": [],
        }
    )
)
_MIN_FAMILY_ENTRY_BYTES = len(
    canonical_json_bytes(
        {
            "family_id": "a",
            "family_kind": min(_DECLARED_CLOSED_FAMILY_KINDS, key=len),
            "declared_capability_fingerprint": _CANONICAL_DIGEST_TEMPLATE,
            "descriptor_digest": _CANONICAL_DIGEST_TEMPLATE,
        }
    )
)
_MAX_FAMILY_ENTRY_BYTES = len(
    canonical_json_bytes(
        {
            "family_id": "a" * 128,
            "family_kind": max(_DECLARED_CLOSED_FAMILY_KINDS, key=len),
            "declared_capability_fingerprint": _CANONICAL_DIGEST_TEMPLATE,
            "descriptor_digest": _CANONICAL_DIGEST_TEMPLATE,
        }
    )
)
_SINGLE_CHARACTER_TOKEN_COUNT = 62


def _canonical_snapshot_byte_bounds(family_entry_count: int) -> tuple[int, int]:
    separator_bytes = max(0, family_entry_count - 1)
    unique_family_id_bytes = max(
        0,
        family_entry_count - _SINGLE_CHARACTER_TOKEN_COUNT,
    )
    return (
        _MIN_EMPTY_SNAPSHOT_BYTES
        + (_MIN_FAMILY_ENTRY_BYTES * family_entry_count)
        + separator_bytes
        + unique_family_id_bytes,
        _MAX_EMPTY_SNAPSHOT_BYTES
        + (_MAX_FAMILY_ENTRY_BYTES * family_entry_count)
        + separator_bytes,
    )


_MATCHING_POLICY = {
    "schema": "wd.understanding.gap_family_exact_match_policy.v1",
    "comparison": "exact_declared_capability_fingerprint_equality",
    "snapshot_source": "caller_supplied_only",
    "closed_family_kinds": _DECLARED_CLOSED_FAMILY_KINDS,
    "canonical_snapshot_required": True,
    "family_entry_sort_key": (
        "family_kind",
        "family_id",
        "declared_capability_fingerprint",
        "descriptor_digest",
    ),
    "duplicate_family_id_rule": "refused",
    "duplicate_descriptor_digest_rule": "refused",
    "ambiguous_exact_match_rule": "refused_without_selection",
    "semantic_equivalence_verified": False,
    "reuse_eligibility_claimed": False,
    "new_family_need_independently_verified": False,
    "existing_family_deduplication_independently_verified": False,
}
GAP_FAMILY_MATCHING_POLICY_DIGEST = sha256_digest(_MATCHING_POLICY)


class GapFamilySnapshotContractError(ValueError):
    """A value is outside the C8b supplied-snapshot contract."""


class GapFamilySnapshotMode(str, Enum):
    OFF = "off"
    STATIC_SHADOW = "static_shadow"


class GapFamilySnapshotDisposition(str, Enum):
    REFUSED = "refused"
    EXACT_DECLARED_CAPABILITY_MATCH_IN_SUPPLIED_SNAPSHOT = (
        "exact_declared_capability_match_in_supplied_snapshot"
    )
    NO_EXACT_DECLARED_CAPABILITY_MATCH_IN_SUPPLIED_SNAPSHOT = (
        "no_exact_declared_capability_match_in_supplied_snapshot"
    )


class GapFamilySnapshotReasonCode(str, Enum):
    AMBIGUOUS_EXACT_MATCH = "ambiguous_exact_match"
    EXACT_MATCH = "exact_match"
    NO_EXACT_MATCH = "no_exact_match"


def _require_sha256(value: object, label: str) -> str:
    if type(value) is not str or not _SHA256.fullmatch(value):
        raise GapFamilySnapshotContractError(
            f"{label} must be a canonical sha256 digest"
        )
    return value


def _require_token(value: object, label: str) -> str:
    if type(value) is not str or not _TOKEN.fullmatch(value):
        raise GapFamilySnapshotContractError(f"{label} must be a bounded token")
    return value


def _require_bounded_int(
    value: object,
    label: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int or value < minimum or value > maximum:
        raise GapFamilySnapshotContractError(
            f"{label} must be an integer in [{minimum}, {maximum}]"
        )
    return value


@dataclass(frozen=True, slots=True)
class GapFamilySnapshotPolicyV1:
    mode: GapFamilySnapshotMode = GapFamilySnapshotMode.OFF
    max_snapshot_bytes: int = _ABSOLUTE_MAX_SNAPSHOT_BYTES
    max_family_entries: int = _ABSOLUTE_MAX_FAMILY_ENTRIES
    max_json_depth: int = _ABSOLUTE_MAX_JSON_DEPTH
    max_json_nodes: int = _ABSOLUTE_MAX_JSON_NODES

    def __post_init__(self) -> None:
        if type(self.mode) is not GapFamilySnapshotMode:
            raise GapFamilySnapshotContractError(
                "mode must be an exact GapFamilySnapshotMode"
            )
        _require_bounded_int(
            self.max_snapshot_bytes,
            "max_snapshot_bytes",
            minimum=128,
            maximum=_ABSOLUTE_MAX_SNAPSHOT_BYTES,
        )
        _require_bounded_int(
            self.max_family_entries,
            "max_family_entries",
            minimum=0,
            maximum=_ABSOLUTE_MAX_FAMILY_ENTRIES,
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

    def to_mapping(self) -> dict[str, Any]:
        GapFamilySnapshotPolicyV1.__post_init__(self)
        return {
            "mode": self.mode.value,
            "max_snapshot_bytes": self.max_snapshot_bytes,
            "max_family_entries": self.max_family_entries,
            "max_json_depth": self.max_json_depth,
            "max_json_nodes": self.max_json_nodes,
        }

    @property
    def policy_digest(self) -> str:
        return sha256_digest(
            {
                "domain": "wd.understanding.gap_family_snapshot_policy.digest.v1",
                **GapFamilySnapshotPolicyV1.to_mapping(self),
            }
        )


@dataclass(frozen=True, slots=True)
class DeclaredCapabilityGapV1:
    gap_id: str
    declared_capability_fingerprint: str
    gap_evidence_digest: str
    cell_binding_digest: str
    schema_version: str = DECLARED_CAPABILITY_GAP_SCHEMA

    def __post_init__(self) -> None:
        if type(self.schema_version) is not str or (
            self.schema_version != DECLARED_CAPABILITY_GAP_SCHEMA
        ):
            raise GapFamilySnapshotContractError("gap schema_version refused")
        _require_token(self.gap_id, "gap_id")
        _require_sha256(
            self.declared_capability_fingerprint,
            "declared_capability_fingerprint",
        )
        _require_sha256(self.gap_evidence_digest, "gap_evidence_digest")
        _require_sha256(self.cell_binding_digest, "cell_binding_digest")

    def to_mapping(self) -> dict[str, Any]:
        DeclaredCapabilityGapV1.__post_init__(self)
        return {
            "schema_version": self.schema_version,
            "gap_id": self.gap_id,
            "declared_capability_fingerprint": (
                self.declared_capability_fingerprint
            ),
            "gap_evidence_digest": self.gap_evidence_digest,
            "cell_binding_digest": self.cell_binding_digest,
        }


def derive_declared_capability_gap_digest(gap: DeclaredCapabilityGapV1) -> str:
    if type(gap) is not DeclaredCapabilityGapV1:
        raise GapFamilySnapshotContractError(
            "gap must be an exact DeclaredCapabilityGapV1"
        )
    DeclaredCapabilityGapV1.__post_init__(gap)
    return sha256_digest(
        {
            "domain": "wd.understanding.declared_capability_gap.digest.v1",
            **DeclaredCapabilityGapV1.to_mapping(gap),
        }
    )


def _plan_core_mapping(
    *,
    campaign_id_digest: str,
    gap_descriptor_digest: str,
    family_snapshot_digest: str,
    registry_snapshot_digest: str,
    resource_policy_digest: str,
    matching_policy_digest: str,
) -> dict[str, Any]:
    return {
        "schema_version": GAP_FAMILY_SNAPSHOT_PLAN_SCHEMA,
        "campaign_id_digest": campaign_id_digest,
        "gap_descriptor_digest": gap_descriptor_digest,
        "family_snapshot_digest": family_snapshot_digest,
        "registry_snapshot_digest": registry_snapshot_digest,
        "resource_policy_digest": resource_policy_digest,
        "matching_policy_digest": matching_policy_digest,
        "attempt_index": 1,
        "attempt_budget": 1,
        "shadow_only": True,
        "evaluation_only": True,
        "caller_supplied_snapshot_only": True,
    }


@dataclass(frozen=True, slots=True)
class GapFamilySnapshotPlanV1:
    campaign_id: str
    gap_descriptor_digest: str
    family_snapshot_digest: str
    registry_snapshot_digest: str
    resource_policy_digest: str
    matching_policy_digest: str = GAP_FAMILY_MATCHING_POLICY_DIGEST
    attempt_index: int = 1
    attempt_budget: int = 1
    shadow_only: bool = True
    evaluation_only: bool = True
    caller_supplied_snapshot_only: bool = True
    schema_version: str = GAP_FAMILY_SNAPSHOT_PLAN_SCHEMA

    def __post_init__(self) -> None:
        if type(self.schema_version) is not str or (
            self.schema_version != GAP_FAMILY_SNAPSHOT_PLAN_SCHEMA
        ):
            raise GapFamilySnapshotContractError("plan schema_version refused")
        _require_token(self.campaign_id, "campaign_id")
        for name in (
            "gap_descriptor_digest",
            "family_snapshot_digest",
            "registry_snapshot_digest",
            "resource_policy_digest",
            "matching_policy_digest",
        ):
            _require_sha256(getattr(self, name), name)
        if self.matching_policy_digest != GAP_FAMILY_MATCHING_POLICY_DIGEST:
            raise GapFamilySnapshotContractError("matching policy digest refused")
        if type(self.attempt_index) is not int or self.attempt_index != 1:
            raise GapFamilySnapshotContractError("C8b requires attempt_index=1")
        if type(self.attempt_budget) is not int or self.attempt_budget != 1:
            raise GapFamilySnapshotContractError("C8b requires attempt_budget=1")
        for name in (
            "shadow_only",
            "evaluation_only",
            "caller_supplied_snapshot_only",
        ):
            if getattr(self, name) is not True:
                raise GapFamilySnapshotContractError(f"{name} must be literal true")

    def to_mapping(self) -> dict[str, Any]:
        GapFamilySnapshotPlanV1.__post_init__(self)
        return _plan_core_mapping(
            campaign_id_digest=self.campaign_id_digest,
            gap_descriptor_digest=self.gap_descriptor_digest,
            family_snapshot_digest=self.family_snapshot_digest,
            registry_snapshot_digest=self.registry_snapshot_digest,
            resource_policy_digest=self.resource_policy_digest,
            matching_policy_digest=self.matching_policy_digest,
        )

    @property
    def campaign_id_digest(self) -> str:
        GapFamilySnapshotPlanV1.__post_init__(self)
        return sha256_digest(
            {
                "domain": "wd.understanding.gap_family_campaign_id.digest.v1",
                "campaign_id": self.campaign_id,
            }
        )

    @property
    def plan_digest(self) -> str:
        return sha256_digest(
            {
                "domain": "wd.understanding.gap_family_snapshot_plan.digest.v1",
                **GapFamilySnapshotPlanV1.to_mapping(self),
            }
        )


@dataclass(frozen=True, repr=False, slots=True)
class GapFamilySnapshotRequestV1:
    plan: GapFamilySnapshotPlanV1
    gap: DeclaredCapabilityGapV1
    family_snapshot_utf8: bytes
    schema_version: str = GAP_FAMILY_SNAPSHOT_REQUEST_SCHEMA

    def __post_init__(self) -> None:
        if type(self.schema_version) is not str or (
            self.schema_version != GAP_FAMILY_SNAPSHOT_REQUEST_SCHEMA
        ):
            raise GapFamilySnapshotContractError("request schema_version refused")
        if type(self.plan) is not GapFamilySnapshotPlanV1:
            raise GapFamilySnapshotContractError(
                "plan must be an exact GapFamilySnapshotPlanV1"
            )
        if type(self.gap) is not DeclaredCapabilityGapV1:
            raise GapFamilySnapshotContractError(
                "gap must be an exact DeclaredCapabilityGapV1"
            )
        if type(self.family_snapshot_utf8) is not bytes:
            raise GapFamilySnapshotContractError(
                "family_snapshot_utf8 must be exact bytes"
            )


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GapFamilySnapshotContractError("duplicate JSON key refused")
        result[key] = value
    return result


def _refuse_json_constant(value: str) -> None:
    raise GapFamilySnapshotContractError(f"non-finite JSON constant refused: {value}")


def _count_json_nodes(
    value: Any,
    *,
    depth: int,
    policy: GapFamilySnapshotPolicyV1,
) -> int:
    if depth > policy.max_json_depth:
        raise GapFamilySnapshotContractError("family snapshot JSON depth refused")
    count = 1
    if type(value) is dict:
        for child in value.values():
            count += _count_json_nodes(child, depth=depth + 1, policy=policy)
    elif type(value) is list:
        for child in value:
            count += _count_json_nodes(child, depth=depth + 1, policy=policy)
    if count > policy.max_json_nodes:
        raise GapFamilySnapshotContractError("family snapshot JSON node count refused")
    return count


@dataclass(frozen=True, slots=True)
class _FamilyEntryFacts:
    family_id: str
    family_kind: str
    declared_capability_fingerprint: str
    descriptor_digest: str
    entry_digest: str


@dataclass(frozen=True, slots=True)
class _FamilySnapshotFacts:
    registry_snapshot_digest: str
    snapshot_digest: str
    byte_count: int
    entries: tuple[_FamilyEntryFacts, ...]


def _decode_family_snapshot(
    value: bytes,
    policy: GapFamilySnapshotPolicyV1,
) -> _FamilySnapshotFacts:
    if type(value) is not bytes:
        raise GapFamilySnapshotContractError("family snapshot must be exact bytes")
    if len(value) == 0 or len(value) > policy.max_snapshot_bytes:
        raise GapFamilySnapshotContractError("family snapshot byte count refused")
    parse_refused = False
    try:
        text = value.decode("utf-8", errors="strict")
        decoded = json.loads(
            text,
            object_pairs_hook=_strict_json_object,
            parse_constant=_refuse_json_constant,
        )
    except (
        GapFamilySnapshotContractError,
        RecursionError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
    ):
        parse_refused = True
    if parse_refused:
        raise GapFamilySnapshotContractError(
            "family snapshot canonical JSON refused"
        )
    if type(decoded) is not dict:
        raise GapFamilySnapshotContractError("family snapshot root must be an object")
    _count_json_nodes(decoded, depth=1, policy=policy)
    canonicalization_refused = False
    try:
        recoded = canonical_json_bytes(decoded)
    except (RecursionError, TypeError, ValueError):
        canonicalization_refused = True
    if canonicalization_refused:
        raise GapFamilySnapshotContractError(
            "family snapshot canonicalization refused"
        )
    if recoded != value:
        raise GapFamilySnapshotContractError("family snapshot bytes are not canonical")
    if set(decoded) != {
        "schema_version",
        "snapshot_id",
        "registry_snapshot_digest",
        "families",
    }:
        raise GapFamilySnapshotContractError("family snapshot shape refused")
    if decoded["schema_version"] != SUPPLIED_FAMILY_SNAPSHOT_SCHEMA:
        raise GapFamilySnapshotContractError("family snapshot schema refused")
    snapshot_id = _require_token(decoded["snapshot_id"], "snapshot_id")
    registry_snapshot_digest = _require_sha256(
        decoded["registry_snapshot_digest"],
        "registry_snapshot_digest",
    )
    families = decoded["families"]
    if type(families) is not list:
        raise GapFamilySnapshotContractError("families must be an exact JSON array")
    if len(families) > policy.max_family_entries:
        raise GapFamilySnapshotContractError("family entry count refused")

    entry_mappings: list[dict[str, str]] = []
    for index, entry in enumerate(families):
        if type(entry) is not dict or set(entry) != {
            "family_id",
            "family_kind",
            "declared_capability_fingerprint",
            "descriptor_digest",
        }:
            raise GapFamilySnapshotContractError(
                f"family entry {index} shape refused"
            )
        family_id = _require_token(entry["family_id"], f"family entry {index} id")
        family_kind = _require_token(
            entry["family_kind"], f"family entry {index} kind"
        )
        if family_kind not in _DECLARED_CLOSED_FAMILY_KINDS:
            raise GapFamilySnapshotContractError(
                f"family entry {index} kind is outside the declared closed set"
            )
        fingerprint = _require_sha256(
            entry["declared_capability_fingerprint"],
            f"family entry {index} capability fingerprint",
        )
        descriptor_digest = _require_sha256(
            entry["descriptor_digest"],
            f"family entry {index} descriptor digest",
        )
        entry_mappings.append(
            {
                "family_id": family_id,
                "family_kind": family_kind,
                "declared_capability_fingerprint": fingerprint,
                "descriptor_digest": descriptor_digest,
            }
        )

    sort_key = lambda item: (
        item["family_kind"],
        item["family_id"],
        item["declared_capability_fingerprint"],
        item["descriptor_digest"],
    )
    if entry_mappings != sorted(entry_mappings, key=sort_key):
        raise GapFamilySnapshotContractError("family entries must be canonically sorted")
    family_ids = [entry["family_id"] for entry in entry_mappings]
    descriptor_digests = [entry["descriptor_digest"] for entry in entry_mappings]
    if len(set(family_ids)) != len(family_ids):
        raise GapFamilySnapshotContractError("duplicate family_id refused")
    if len(set(descriptor_digests)) != len(descriptor_digests):
        raise GapFamilySnapshotContractError("duplicate descriptor_digest refused")

    entries = tuple(
        _FamilyEntryFacts(
            **entry,
            entry_digest=sha256_digest(
                {
                    "domain": "wd.understanding.supplied_family_entry.digest.v1",
                    **entry,
                }
            ),
        )
        for entry in entry_mappings
    )
    snapshot_digest = sha256_digest(
        {
            "domain": "wd.understanding.supplied_family_snapshot.digest.v1",
            "schema_version": SUPPLIED_FAMILY_SNAPSHOT_SCHEMA,
            "snapshot_id": snapshot_id,
            "registry_snapshot_digest": registry_snapshot_digest,
            "family_entry_digests": [entry.entry_digest for entry in entries],
        }
    )
    return _FamilySnapshotFacts(
        registry_snapshot_digest=registry_snapshot_digest,
        snapshot_digest=snapshot_digest,
        byte_count=len(value),
        entries=entries,
    )


def derive_supplied_family_snapshot_digest(
    family_snapshot_utf8: bytes,
    policy: GapFamilySnapshotPolicyV1,
) -> str:
    if type(policy) is not GapFamilySnapshotPolicyV1:
        raise GapFamilySnapshotContractError(
            "policy must be an exact GapFamilySnapshotPolicyV1"
        )
    GapFamilySnapshotPolicyV1.__post_init__(policy)
    return _decode_family_snapshot(family_snapshot_utf8, policy).snapshot_digest


def _derive_request_digest(
    *,
    plan_digest: str,
    gap_descriptor_digest: str,
    family_snapshot_digest: str,
) -> str:
    return sha256_digest(
        {
            "domain": "wd.understanding.gap_family_snapshot_request.digest.v1",
            "schema_version": GAP_FAMILY_SNAPSHOT_REQUEST_SCHEMA,
            "plan_digest": plan_digest,
            "gap_descriptor_digest": gap_descriptor_digest,
            "family_snapshot_digest": family_snapshot_digest,
        }
    )


_TRUE_RECEIPT_FIELDS = (
    "evaluation_only",
    "shadow_only",
    "static_accounting_only",
    "raw_material_omitted",
    "exact_declared_fingerprint_comparison_only",
    "caller_supplied_snapshot_only",
    "c8a_not_invoked",
    "c7_not_invoked",
    "no_side_effects_in_module",
)

_FALSE_RECEIPT_FIELDS = (
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
)


@dataclass(frozen=True, slots=True)
class GapFamilySnapshotReceiptV1:
    plan_digest: str
    campaign_id_digest: str
    policy_digest: str
    request_digest: str
    gap_descriptor_digest: str
    gap_evidence_digest: str
    family_snapshot_digest: str
    registry_snapshot_digest: str
    matching_policy_digest: str
    max_snapshot_bytes: int
    max_family_entries: int
    max_json_depth: int
    max_json_nodes: int
    family_entry_count: int
    family_snapshot_byte_count: int
    exact_match_count: int
    matched_family_entry_digest: str | None
    disposition: GapFamilySnapshotDisposition
    reason_code: GapFamilySnapshotReasonCode
    receipt_digest: str
    schema_version: str = GAP_FAMILY_SNAPSHOT_RECEIPT_SCHEMA
    evaluation_only: bool = True
    shadow_only: bool = True
    static_accounting_only: bool = True
    raw_material_omitted: bool = True
    exact_declared_fingerprint_comparison_only: bool = True
    caller_supplied_snapshot_only: bool = True
    c8a_not_invoked: bool = True
    c7_not_invoked: bool = True
    no_side_effects_in_module: bool = True
    semantic_equivalence_verified: bool = False
    semantic_deduplication_verified: bool = False
    global_deduplication_verified: bool = False
    reuse_eligibility_claimed: bool = False
    build_eligibility_claimed: bool = False
    generation_authorized: bool = False
    c8a_authorized: bool = False
    family_novelty_independently_verified: bool = False
    new_family_need_independently_verified: bool = False
    existing_family_deduplication_independently_verified: bool = False
    catalog_completeness_verified: bool = False
    catalog_freshness_verified: bool = False
    catalog_authenticity_verified: bool = False
    snapshot_externally_pinned: bool = False
    registry_snapshot_identity_independently_verified: bool = False
    family_review_status_independently_verified: bool = False
    cross_campaign_single_attempt_enforced: bool = False
    scalability_50000_demonstrated: bool = False
    independent_verification_applied: bool = False
    receipt_origin_authenticated: bool = False
    genesis_origin_independently_verified: bool = False
    hex_cell_binding_independently_verified: bool = False
    echo_chamber_absence_verified: bool = False
    provider_invoked: bool = False
    builder_host_invoked: bool = False
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
        result: dict[str, Any] = {}
        for item in fields(self):
            if item.name == "receipt_digest":
                continue
            value = getattr(self, item.name)
            result[item.name] = value.value if isinstance(value, Enum) else value
        return result

    def __post_init__(self) -> None:
        if type(self.schema_version) is not str or (
            self.schema_version != GAP_FAMILY_SNAPSHOT_RECEIPT_SCHEMA
        ):
            raise GapFamilySnapshotContractError("receipt schema_version refused")
        for name in (
            "plan_digest",
            "campaign_id_digest",
            "policy_digest",
            "request_digest",
            "gap_descriptor_digest",
            "gap_evidence_digest",
            "family_snapshot_digest",
            "registry_snapshot_digest",
            "matching_policy_digest",
            "receipt_digest",
        ):
            _require_sha256(getattr(self, name), name)
        if self.matching_policy_digest != GAP_FAMILY_MATCHING_POLICY_DIGEST:
            raise GapFamilySnapshotContractError("receipt matching policy refused")
        expected_policy = GapFamilySnapshotPolicyV1(
            mode=GapFamilySnapshotMode.STATIC_SHADOW,
            max_snapshot_bytes=self.max_snapshot_bytes,
            max_family_entries=self.max_family_entries,
            max_json_depth=self.max_json_depth,
            max_json_nodes=self.max_json_nodes,
        )
        if self.policy_digest != expected_policy.policy_digest:
            raise GapFamilySnapshotContractError("receipt policy relation mismatch")
        expected_plan_digest = sha256_digest(
            {
                "domain": "wd.understanding.gap_family_snapshot_plan.digest.v1",
                **_plan_core_mapping(
                    campaign_id_digest=self.campaign_id_digest,
                    gap_descriptor_digest=self.gap_descriptor_digest,
                    family_snapshot_digest=self.family_snapshot_digest,
                    registry_snapshot_digest=self.registry_snapshot_digest,
                    resource_policy_digest=self.policy_digest,
                    matching_policy_digest=self.matching_policy_digest,
                ),
            }
        )
        if self.plan_digest != expected_plan_digest:
            raise GapFamilySnapshotContractError("receipt plan relation mismatch")
        expected_request_digest = _derive_request_digest(
            plan_digest=self.plan_digest,
            gap_descriptor_digest=self.gap_descriptor_digest,
            family_snapshot_digest=self.family_snapshot_digest,
        )
        if self.request_digest != expected_request_digest:
            raise GapFamilySnapshotContractError("receipt request relation mismatch")
        _require_bounded_int(
            self.family_entry_count,
            "family_entry_count",
            minimum=0,
            maximum=_ABSOLUTE_MAX_FAMILY_ENTRIES,
        )
        _require_bounded_int(
            self.family_snapshot_byte_count,
            "family_snapshot_byte_count",
            minimum=1,
            maximum=_ABSOLUTE_MAX_SNAPSHOT_BYTES,
        )
        _require_bounded_int(
            self.exact_match_count,
            "exact_match_count",
            minimum=0,
            maximum=_ABSOLUTE_MAX_FAMILY_ENTRIES,
        )
        if self.family_entry_count > self.max_family_entries:
            raise GapFamilySnapshotContractError(
                "family entry count exceeds receipt policy"
            )
        if self.family_snapshot_byte_count > self.max_snapshot_bytes:
            raise GapFamilySnapshotContractError(
                "family snapshot byte count exceeds receipt policy"
            )
        minimum_snapshot_bytes, maximum_snapshot_bytes = (
            _canonical_snapshot_byte_bounds(self.family_entry_count)
        )
        if not (
            minimum_snapshot_bytes
            <= self.family_snapshot_byte_count
            <= maximum_snapshot_bytes
        ):
            raise GapFamilySnapshotContractError(
                "family snapshot byte count is impossible for receipt entry count"
            )
        required_json_nodes = 5 + (5 * self.family_entry_count)
        if required_json_nodes > self.max_json_nodes:
            raise GapFamilySnapshotContractError(
                "family snapshot JSON node count exceeds receipt policy"
            )
        required_json_depth = 4 if self.family_entry_count else 2
        if required_json_depth > self.max_json_depth:
            raise GapFamilySnapshotContractError(
                "family snapshot JSON depth exceeds receipt policy"
            )
        if self.exact_match_count > self.family_entry_count:
            raise GapFamilySnapshotContractError("match count exceeds family count")
        if type(self.disposition) is not GapFamilySnapshotDisposition:
            raise GapFamilySnapshotContractError("receipt disposition refused")
        if type(self.reason_code) is not GapFamilySnapshotReasonCode:
            raise GapFamilySnapshotContractError("receipt reason_code refused")

        relation = (self.disposition, self.reason_code)
        exact_relation = (
            GapFamilySnapshotDisposition.EXACT_DECLARED_CAPABILITY_MATCH_IN_SUPPLIED_SNAPSHOT,
            GapFamilySnapshotReasonCode.EXACT_MATCH,
        )
        no_match_relation = (
            GapFamilySnapshotDisposition.NO_EXACT_DECLARED_CAPABILITY_MATCH_IN_SUPPLIED_SNAPSHOT,
            GapFamilySnapshotReasonCode.NO_EXACT_MATCH,
        )
        refused_relation = (
            GapFamilySnapshotDisposition.REFUSED,
            GapFamilySnapshotReasonCode.AMBIGUOUS_EXACT_MATCH,
        )
        if relation == exact_relation:
            if self.exact_match_count != 1:
                raise GapFamilySnapshotContractError("exact-match receipt count mismatch")
            _require_sha256(
                self.matched_family_entry_digest,
                "matched_family_entry_digest",
            )
        elif relation == no_match_relation:
            if self.exact_match_count != 0 or self.matched_family_entry_digest is not None:
                raise GapFamilySnapshotContractError("no-match receipt relation mismatch")
        elif relation == refused_relation:
            if self.exact_match_count < 2 or self.matched_family_entry_digest is not None:
                raise GapFamilySnapshotContractError("refused receipt relation mismatch")
        else:
            raise GapFamilySnapshotContractError("receipt disposition/reason mismatch")

        for name in _TRUE_RECEIPT_FIELDS:
            if getattr(self, name) is not True:
                raise GapFamilySnapshotContractError(f"{name} must be literal true")
        for name in _FALSE_RECEIPT_FIELDS:
            if getattr(self, name) is not False:
                raise GapFamilySnapshotContractError(f"{name} must be literal false")
        expected_digest = sha256_digest(
            {
                "domain": "wd.understanding.gap_family_snapshot_receipt.digest.v1",
                **GapFamilySnapshotReceiptV1._core_mapping(self),
            }
        )
        if self.receipt_digest != expected_digest:
            raise GapFamilySnapshotContractError("receipt digest does not match fields")

    def to_mapping(self) -> dict[str, Any]:
        GapFamilySnapshotReceiptV1.__post_init__(self)
        return {
            **GapFamilySnapshotReceiptV1._core_mapping(self),
            "receipt_digest": self.receipt_digest,
        }


def _snapshot_policy(policy: GapFamilySnapshotPolicyV1) -> GapFamilySnapshotPolicyV1:
    return GapFamilySnapshotPolicyV1(
        mode=policy.mode,
        max_snapshot_bytes=policy.max_snapshot_bytes,
        max_family_entries=policy.max_family_entries,
        max_json_depth=policy.max_json_depth,
        max_json_nodes=policy.max_json_nodes,
    )


def _snapshot_request(request: GapFamilySnapshotRequestV1) -> GapFamilySnapshotRequestV1:
    GapFamilySnapshotRequestV1.__post_init__(request)
    GapFamilySnapshotPlanV1.__post_init__(request.plan)
    DeclaredCapabilityGapV1.__post_init__(request.gap)
    plan = GapFamilySnapshotPlanV1(
        **{item.name: getattr(request.plan, item.name) for item in fields(request.plan)}
    )
    gap = DeclaredCapabilityGapV1(
        **{item.name: getattr(request.gap, item.name) for item in fields(request.gap)}
    )
    return GapFamilySnapshotRequestV1(
        plan=plan,
        gap=gap,
        family_snapshot_utf8=request.family_snapshot_utf8,
        schema_version=request.schema_version,
    )


def _receipt_core(values: dict[str, Any]) -> dict[str, Any]:
    return {
        **values,
        "schema_version": GAP_FAMILY_SNAPSHOT_RECEIPT_SCHEMA,
        **{name: True for name in _TRUE_RECEIPT_FIELDS},
        **{name: False for name in _FALSE_RECEIPT_FIELDS},
    }


def _make_receipt(values: dict[str, Any]) -> GapFamilySnapshotReceiptV1:
    normalized = {
        name: (value.value if isinstance(value, Enum) else value)
        for name, value in _receipt_core(values).items()
    }
    receipt_digest = sha256_digest(
        {
            "domain": "wd.understanding.gap_family_snapshot_receipt.digest.v1",
            **normalized,
        }
    )
    return GapFamilySnapshotReceiptV1(
        **values,
        receipt_digest=receipt_digest,
    )


def evaluate_gap_family_snapshot(
    request: GapFamilySnapshotRequestV1 | None = None,
    *,
    policy: GapFamilySnapshotPolicyV1 = GapFamilySnapshotPolicyV1(),
) -> GapFamilySnapshotReceiptV1 | None:
    """Account for exact equality in one supplied snapshot, or remain OFF.

    OFF returns before inspecting ``request`` or snapshot bytes.  A non-match
    is only absence from the supplied bag; it is not novelty, admission, reuse,
    or generation authority.
    """

    if type(policy) is not GapFamilySnapshotPolicyV1:
        raise GapFamilySnapshotContractError(
            "policy must be an exact GapFamilySnapshotPolicyV1"
        )
    if policy.mode is GapFamilySnapshotMode.OFF:
        return None
    policy = _snapshot_policy(policy)
    if policy.mode is not GapFamilySnapshotMode.STATIC_SHADOW:
        raise GapFamilySnapshotContractError("unsupported C8b mode")
    if type(request) is not GapFamilySnapshotRequestV1:
        raise GapFamilySnapshotContractError(
            "STATIC_SHADOW requires an exact GapFamilySnapshotRequestV1"
        )
    request = _snapshot_request(request)
    plan = request.plan
    gap = request.gap
    if plan.resource_policy_digest != policy.policy_digest:
        raise GapFamilySnapshotContractError("plan resource policy digest mismatch")

    gap_digest = derive_declared_capability_gap_digest(gap)
    if plan.gap_descriptor_digest != gap_digest:
        raise GapFamilySnapshotContractError("gap descriptor digest mismatch")
    snapshot = _decode_family_snapshot(request.family_snapshot_utf8, policy)
    if plan.family_snapshot_digest != snapshot.snapshot_digest:
        raise GapFamilySnapshotContractError("family snapshot digest mismatch")
    if plan.registry_snapshot_digest != snapshot.registry_snapshot_digest:
        raise GapFamilySnapshotContractError("registry snapshot digest mismatch")

    matches = tuple(
        entry
        for entry in snapshot.entries
        if entry.declared_capability_fingerprint
        == gap.declared_capability_fingerprint
    )
    if len(matches) == 0:
        disposition = (
            GapFamilySnapshotDisposition.NO_EXACT_DECLARED_CAPABILITY_MATCH_IN_SUPPLIED_SNAPSHOT
        )
        reason = GapFamilySnapshotReasonCode.NO_EXACT_MATCH
        matched_digest = None
    elif len(matches) == 1:
        disposition = (
            GapFamilySnapshotDisposition.EXACT_DECLARED_CAPABILITY_MATCH_IN_SUPPLIED_SNAPSHOT
        )
        reason = GapFamilySnapshotReasonCode.EXACT_MATCH
        matched_digest = matches[0].entry_digest
    else:
        disposition = GapFamilySnapshotDisposition.REFUSED
        reason = GapFamilySnapshotReasonCode.AMBIGUOUS_EXACT_MATCH
        matched_digest = None

    plan_digest = plan.plan_digest
    request_digest = _derive_request_digest(
        plan_digest=plan_digest,
        gap_descriptor_digest=gap_digest,
        family_snapshot_digest=snapshot.snapshot_digest,
    )
    return _make_receipt(
        {
            "plan_digest": plan_digest,
            "campaign_id_digest": plan.campaign_id_digest,
            "policy_digest": policy.policy_digest,
            "request_digest": request_digest,
            "gap_descriptor_digest": gap_digest,
            "gap_evidence_digest": gap.gap_evidence_digest,
            "family_snapshot_digest": snapshot.snapshot_digest,
            "registry_snapshot_digest": snapshot.registry_snapshot_digest,
            "matching_policy_digest": GAP_FAMILY_MATCHING_POLICY_DIGEST,
            "max_snapshot_bytes": policy.max_snapshot_bytes,
            "max_family_entries": policy.max_family_entries,
            "max_json_depth": policy.max_json_depth,
            "max_json_nodes": policy.max_json_nodes,
            "family_entry_count": len(snapshot.entries),
            "family_snapshot_byte_count": snapshot.byte_count,
            "exact_match_count": len(matches),
            "matched_family_entry_digest": matched_digest,
            "disposition": disposition,
            "reason_code": reason,
        }
    )


__all__ = [
    "DECLARED_CAPABILITY_GAP_SCHEMA",
    "DECLARED_CLOSED_FAMILY_KINDS",
    "GAP_FAMILY_MATCHING_POLICY_DIGEST",
    "GAP_FAMILY_SNAPSHOT_PLAN_SCHEMA",
    "GAP_FAMILY_SNAPSHOT_RECEIPT_SCHEMA",
    "GAP_FAMILY_SNAPSHOT_REQUEST_SCHEMA",
    "SUPPLIED_FAMILY_SNAPSHOT_SCHEMA",
    "DeclaredCapabilityGapV1",
    "GapFamilySnapshotContractError",
    "GapFamilySnapshotDisposition",
    "GapFamilySnapshotMode",
    "GapFamilySnapshotPlanV1",
    "GapFamilySnapshotPolicyV1",
    "GapFamilySnapshotReasonCode",
    "GapFamilySnapshotReceiptV1",
    "GapFamilySnapshotRequestV1",
    "derive_declared_capability_gap_digest",
    "derive_supplied_family_snapshot_digest",
    "evaluate_gap_family_snapshot",
]
