# SPDX-License-Identifier: Apache-2.0
"""Default-off accounting over one supplied declared-attempt snapshot.

C8d compares one exact declared-capability fingerprint with records inside a
bounded canonical snapshot supplied by the caller. A separate keyword-only
caller-supplied expected-digest object must match before the subject is
scanned. Equality and record counts are local facts about those supplied
values, not an authenticated or complete attempt history.

The module is pure and inert. It does not invoke earlier understanding slices,
BuilderHost, providers, registries, MAGMA storage, routing, promotion, runtime,
candidate execution, or an OS sandbox. No result authorizes a retry, build,
generation, write, or activation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, fields
from enum import Enum
from typing import Any

from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest


SUPPLIED_DECLARED_ATTEMPT_SNAPSHOT_SCHEMA = (
    "wd.understanding.supplied_declared_attempt_snapshot.v1"
)
DECLARED_ATTEMPT_EXPECTED_SNAPSHOT_DIGEST_SCHEMA = (
    "wd.understanding.declared_attempt_expected_snapshot_digest.v1"
)
DECLARED_ATTEMPT_SNAPSHOT_POLICY_SCHEMA = (
    "wd.understanding.declared_attempt_snapshot_policy.v1"
)
DECLARED_ATTEMPT_SNAPSHOT_REQUEST_SCHEMA = (
    "wd.understanding.declared_attempt_snapshot_request.v1"
)
DECLARED_ATTEMPT_SNAPSHOT_RECEIPT_SCHEMA = (
    "wd.understanding.declared_attempt_snapshot_receipt.v1"
)

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")

_ABSOLUTE_MAX_SNAPSHOT_BYTES = 2_097_152
_ABSOLUTE_MAX_ATTEMPT_RECORDS = 4_096
_ABSOLUTE_MAX_JSON_DEPTH = 6
_ABSOLUTE_MAX_JSON_NODES = 32_768

_CANONICAL_DIGEST_TEMPLATE = "sha256:" + ("0" * 64)
_MIN_EMPTY_SNAPSHOT_BYTES = len(
    canonical_json_bytes(
        {
            "schema_version": SUPPLIED_DECLARED_ATTEMPT_SNAPSHOT_SCHEMA,
            "snapshot_id": "a",
            "attempt_history_scope_digest": _CANONICAL_DIGEST_TEMPLATE,
            "attempts": [],
        }
    )
)
_MAX_EMPTY_SNAPSHOT_BYTES = len(
    canonical_json_bytes(
        {
            "schema_version": SUPPLIED_DECLARED_ATTEMPT_SNAPSHOT_SCHEMA,
            "snapshot_id": "a" * 128,
            "attempt_history_scope_digest": _CANONICAL_DIGEST_TEMPLATE,
            "attempts": [],
        }
    )
)
_MIN_ATTEMPT_RECORD_BYTES = len(
    canonical_json_bytes(
        {
            "attempt_record_id": "a",
            "declared_capability_fingerprint": _CANONICAL_DIGEST_TEMPLATE,
            "campaign_id_digest": _CANONICAL_DIGEST_TEMPLATE,
            "cell_binding_digest": _CANONICAL_DIGEST_TEMPLATE,
            "attempt_evidence_digest": _CANONICAL_DIGEST_TEMPLATE,
        }
    )
)
_MAX_ATTEMPT_RECORD_BYTES = len(
    canonical_json_bytes(
        {
            "attempt_record_id": "a" * 128,
            "declared_capability_fingerprint": _CANONICAL_DIGEST_TEMPLATE,
            "campaign_id_digest": _CANONICAL_DIGEST_TEMPLATE,
            "cell_binding_digest": _CANONICAL_DIGEST_TEMPLATE,
            "attempt_evidence_digest": _CANONICAL_DIGEST_TEMPLATE,
        }
    )
)
_SINGLE_CHARACTER_TOKEN_COUNT = 62


def _canonical_snapshot_byte_bounds(
    attempt_record_count: int,
) -> tuple[int, int]:
    separator_bytes = max(0, attempt_record_count - 1)
    unique_attempt_record_id_bytes = max(
        0,
        attempt_record_count - _SINGLE_CHARACTER_TOKEN_COUNT,
    )
    return (
        _MIN_EMPTY_SNAPSHOT_BYTES
        + (_MIN_ATTEMPT_RECORD_BYTES * attempt_record_count)
        + separator_bytes
        + unique_attempt_record_id_bytes,
        _MAX_EMPTY_SNAPSHOT_BYTES
        + (_MAX_ATTEMPT_RECORD_BYTES * attempt_record_count)
        + separator_bytes,
    )


_ACCOUNTING_POLICY = {
    "schema": "wd.understanding.declared_attempt_snapshot_accounting_policy.v1",
    "snapshot_schema": SUPPLIED_DECLARED_ATTEMPT_SNAPSHOT_SCHEMA,
    "snapshot_source": "caller_supplied_only",
    "expected_digest_source": "caller_supplied_keyword_only",
    "comparison_subject": "declared_capability_fingerprint_only",
    "comparison_operator": "exact_public_digest_equality",
    "attempt_record_sort_key": (
        "declared_capability_fingerprint",
        "campaign_id_digest",
        "cell_binding_digest",
        "attempt_record_id",
        "attempt_evidence_digest",
    ),
    "duplicate_attempt_record_id_rule": "refused",
    "duplicate_attempt_evidence_digest_rule": "refused",
    "expected_digest_mismatch_rule": "refused_without_subject_scan",
    "ambiguous_subject_match_rule": "refused_without_selection",
    "campaign_id_is_match_key": False,
    "cell_binding_digest_is_match_key": False,
    "attempt_record_id_is_match_key": False,
    "attempt_evidence_digest_is_match_key": False,
    "attempt_history_authenticated": False,
    "single_attempt_enforced": False,
    "authority_granted": False,
}
DECLARED_ATTEMPT_ACCOUNTING_POLICY_DIGEST = sha256_digest(_ACCOUNTING_POLICY)


class DeclaredAttemptSnapshotContractError(ValueError):
    """A value is outside the C8d supplied-snapshot contract."""


class DeclaredAttemptSnapshotMode(str, Enum):
    OFF = "off"
    STATIC_SHADOW = "static_shadow"


class DeclaredAttemptSnapshotDisposition(str, Enum):
    REFUSED = "refused"
    EXACTLY_ONE_DECLARED_CAPABILITY_MATCH_IN_SUPPLIED_ATTEMPT_SNAPSHOT = (
        "exactly_one_declared_capability_match_in_supplied_attempt_snapshot"
    )
    NO_EXACT_DECLARED_CAPABILITY_MATCH_IN_SUPPLIED_ATTEMPT_SNAPSHOT = (
        "no_exact_declared_capability_match_in_supplied_attempt_snapshot"
    )


class DeclaredAttemptSnapshotReasonCode(str, Enum):
    EXPECTED_ATTEMPT_SNAPSHOT_DIGEST_MISMATCH = (
        "expected_attempt_snapshot_digest_mismatch"
    )
    AMBIGUOUS_MULTIPLE_EXACT_DECLARED_CAPABILITY_MATCHES = (
        "ambiguous_multiple_exact_declared_capability_matches"
    )
    EXACTLY_ONE_EXACT_DECLARED_CAPABILITY_MATCH = (
        "exactly_one_exact_declared_capability_match"
    )
    NO_EXACT_DECLARED_CAPABILITY_MATCH = (
        "no_exact_declared_capability_match"
    )


def _require_sha256(value: object, label: str) -> str:
    if type(value) is not str or not _SHA256.fullmatch(value):
        raise DeclaredAttemptSnapshotContractError(
            f"{label} must be a canonical sha256 digest"
        )
    return value


def _require_token(value: object, label: str) -> str:
    if type(value) is not str or not _TOKEN.fullmatch(value):
        raise DeclaredAttemptSnapshotContractError(
            f"{label} must be a bounded token"
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
        raise DeclaredAttemptSnapshotContractError(
            f"{label} must be an integer in [{minimum}, {maximum}]"
        )
    return value


@dataclass(frozen=True, slots=True)
class DeclaredAttemptSnapshotPolicyV1:
    mode: DeclaredAttemptSnapshotMode = DeclaredAttemptSnapshotMode.OFF
    max_snapshot_bytes: int = _ABSOLUTE_MAX_SNAPSHOT_BYTES
    max_attempt_records: int = _ABSOLUTE_MAX_ATTEMPT_RECORDS
    max_json_depth: int = _ABSOLUTE_MAX_JSON_DEPTH
    max_json_nodes: int = _ABSOLUTE_MAX_JSON_NODES
    accounting_policy_digest: str = DECLARED_ATTEMPT_ACCOUNTING_POLICY_DIGEST
    schema_version: str = DECLARED_ATTEMPT_SNAPSHOT_POLICY_SCHEMA

    def __post_init__(self) -> None:
        if type(self) is not DeclaredAttemptSnapshotPolicyV1:
            raise DeclaredAttemptSnapshotContractError(
                "policy exact type required"
            )
        if type(self.schema_version) is not str or (
            self.schema_version != DECLARED_ATTEMPT_SNAPSHOT_POLICY_SCHEMA
        ):
            raise DeclaredAttemptSnapshotContractError(
                "policy schema_version refused"
            )
        if type(self.mode) is not DeclaredAttemptSnapshotMode:
            raise DeclaredAttemptSnapshotContractError(
                "mode must be an exact DeclaredAttemptSnapshotMode"
            )
        _require_bounded_int(
            self.max_snapshot_bytes,
            "max_snapshot_bytes",
            minimum=128,
            maximum=_ABSOLUTE_MAX_SNAPSHOT_BYTES,
        )
        _require_bounded_int(
            self.max_attempt_records,
            "max_attempt_records",
            minimum=0,
            maximum=_ABSOLUTE_MAX_ATTEMPT_RECORDS,
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
            != DECLARED_ATTEMPT_ACCOUNTING_POLICY_DIGEST
        ):
            raise DeclaredAttemptSnapshotContractError(
                "accounting policy digest refused"
            )

    def to_mapping(self) -> dict[str, Any]:
        DeclaredAttemptSnapshotPolicyV1.__post_init__(self)
        return {
            "schema_version": self.schema_version,
            "mode": self.mode.value,
            "max_snapshot_bytes": self.max_snapshot_bytes,
            "max_attempt_records": self.max_attempt_records,
            "max_json_depth": self.max_json_depth,
            "max_json_nodes": self.max_json_nodes,
            "accounting_policy_digest": self.accounting_policy_digest,
        }

    @property
    def policy_digest(self) -> str:
        return sha256_digest(
            {
                "domain": (
                    "wd.understanding.declared_attempt_snapshot_policy.digest.v1"
                ),
                **DeclaredAttemptSnapshotPolicyV1.to_mapping(self),
            }
        )


@dataclass(frozen=True, slots=True)
class DeclaredAttemptExpectedSnapshotDigestV1:
    expected_attempt_snapshot_digest: str
    target_snapshot_schema_version: str = (
        SUPPLIED_DECLARED_ATTEMPT_SNAPSHOT_SCHEMA
    )
    schema_version: str = DECLARED_ATTEMPT_EXPECTED_SNAPSHOT_DIGEST_SCHEMA

    def __post_init__(self) -> None:
        if type(self) is not DeclaredAttemptExpectedSnapshotDigestV1:
            raise DeclaredAttemptSnapshotContractError(
                "expected snapshot digest exact type required"
            )
        if type(self.schema_version) is not str or (
            self.schema_version
            != DECLARED_ATTEMPT_EXPECTED_SNAPSHOT_DIGEST_SCHEMA
        ):
            raise DeclaredAttemptSnapshotContractError(
                "expected snapshot digest schema_version refused"
            )
        if type(self.target_snapshot_schema_version) is not str or (
            self.target_snapshot_schema_version
            != SUPPLIED_DECLARED_ATTEMPT_SNAPSHOT_SCHEMA
        ):
            raise DeclaredAttemptSnapshotContractError(
                "expected snapshot digest target schema refused"
            )
        _require_sha256(
            self.expected_attempt_snapshot_digest,
            "expected_attempt_snapshot_digest",
        )

    def to_mapping(self) -> dict[str, Any]:
        DeclaredAttemptExpectedSnapshotDigestV1.__post_init__(self)
        return {
            "schema_version": self.schema_version,
            "target_snapshot_schema_version": (
                self.target_snapshot_schema_version
            ),
            "expected_attempt_snapshot_digest": (
                self.expected_attempt_snapshot_digest
            ),
        }

    @property
    def expectation_digest(self) -> str:
        return sha256_digest(
            {
                "domain": (
                    "wd.understanding.declared_attempt_expected_snapshot_"
                    "digest.digest.v1"
                ),
                **DeclaredAttemptExpectedSnapshotDigestV1.to_mapping(self),
            }
        )


@dataclass(frozen=True, repr=False, slots=True)
class DeclaredAttemptSnapshotRequestV1:
    declared_capability_fingerprint: str
    attempt_snapshot_utf8: bytes
    schema_version: str = DECLARED_ATTEMPT_SNAPSHOT_REQUEST_SCHEMA

    def __post_init__(self) -> None:
        if type(self) is not DeclaredAttemptSnapshotRequestV1:
            raise DeclaredAttemptSnapshotContractError(
                "request exact type required"
            )
        if type(self.schema_version) is not str or (
            self.schema_version != DECLARED_ATTEMPT_SNAPSHOT_REQUEST_SCHEMA
        ):
            raise DeclaredAttemptSnapshotContractError(
                "request schema_version refused"
            )
        _require_sha256(
            self.declared_capability_fingerprint,
            "declared_capability_fingerprint",
        )
        if type(self.attempt_snapshot_utf8) is not bytes:
            raise DeclaredAttemptSnapshotContractError(
                "attempt_snapshot_utf8 must be exact bytes"
            )


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DeclaredAttemptSnapshotContractError(
                "duplicate JSON key refused"
            )
        result[key] = value
    return result


def _refuse_json_constant(value: str) -> None:
    raise DeclaredAttemptSnapshotContractError(
        f"non-finite JSON constant refused: {value}"
    )


def _count_json_nodes(
    value: Any,
    *,
    depth: int,
    policy: DeclaredAttemptSnapshotPolicyV1,
) -> int:
    if depth > policy.max_json_depth:
        raise DeclaredAttemptSnapshotContractError(
            "attempt snapshot JSON depth refused"
        )
    count = 1
    if type(value) is dict:
        for child in value.values():
            count += _count_json_nodes(
                child, depth=depth + 1, policy=policy
            )
    elif type(value) is list:
        for child in value:
            count += _count_json_nodes(
                child, depth=depth + 1, policy=policy
            )
    if count > policy.max_json_nodes:
        raise DeclaredAttemptSnapshotContractError(
            "attempt snapshot JSON node count refused"
        )
    return count


@dataclass(frozen=True, slots=True)
class _DeclaredAttemptRecordFacts:
    attempt_record_id: str
    declared_capability_fingerprint: str
    campaign_id_digest: str
    cell_binding_digest: str
    attempt_evidence_digest: str
    record_digest: str


@dataclass(frozen=True, slots=True)
class _DeclaredAttemptSnapshotFacts:
    attempt_history_scope_digest: str
    snapshot_digest: str
    byte_count: int
    records: tuple[_DeclaredAttemptRecordFacts, ...]


def _decode_attempt_snapshot(
    value: bytes,
    policy: DeclaredAttemptSnapshotPolicyV1,
) -> _DeclaredAttemptSnapshotFacts:
    if type(value) is not bytes:
        raise DeclaredAttemptSnapshotContractError(
            "attempt snapshot must be exact bytes"
        )
    if len(value) == 0 or len(value) > policy.max_snapshot_bytes:
        raise DeclaredAttemptSnapshotContractError(
            "attempt snapshot byte count refused"
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
        DeclaredAttemptSnapshotContractError,
        RecursionError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
    ):
        parse_refused = True
    if parse_refused:
        raise DeclaredAttemptSnapshotContractError(
            "attempt snapshot canonical JSON refused"
        )
    if type(decoded) is not dict:
        raise DeclaredAttemptSnapshotContractError(
            "attempt snapshot root must be an object"
        )
    _count_json_nodes(decoded, depth=1, policy=policy)
    canonicalization_refused = False
    try:
        recoded = canonical_json_bytes(decoded)
    except (RecursionError, TypeError, ValueError):
        canonicalization_refused = True
    if canonicalization_refused:
        raise DeclaredAttemptSnapshotContractError(
            "attempt snapshot canonicalization refused"
        )
    if recoded != value:
        raise DeclaredAttemptSnapshotContractError(
            "attempt snapshot bytes are not canonical"
        )
    if set(decoded) != {
        "schema_version",
        "snapshot_id",
        "attempt_history_scope_digest",
        "attempts",
    }:
        raise DeclaredAttemptSnapshotContractError(
            "attempt snapshot shape refused"
        )
    if decoded["schema_version"] != SUPPLIED_DECLARED_ATTEMPT_SNAPSHOT_SCHEMA:
        raise DeclaredAttemptSnapshotContractError(
            "attempt snapshot schema refused"
        )
    snapshot_id = _require_token(decoded["snapshot_id"], "snapshot_id")
    scope_digest = _require_sha256(
        decoded["attempt_history_scope_digest"],
        "attempt_history_scope_digest",
    )
    attempts = decoded["attempts"]
    if type(attempts) is not list:
        raise DeclaredAttemptSnapshotContractError(
            "attempts must be an exact JSON array"
        )
    if len(attempts) > policy.max_attempt_records:
        raise DeclaredAttemptSnapshotContractError(
            "attempt record count refused"
        )

    record_mappings: list[dict[str, str]] = []
    for index, record in enumerate(attempts):
        if type(record) is not dict or set(record) != {
            "attempt_record_id",
            "declared_capability_fingerprint",
            "campaign_id_digest",
            "cell_binding_digest",
            "attempt_evidence_digest",
        }:
            raise DeclaredAttemptSnapshotContractError(
                f"attempt record {index} shape refused"
            )
        record_mappings.append(
            {
                "attempt_record_id": _require_token(
                    record["attempt_record_id"],
                    f"attempt record {index} id",
                ),
                "declared_capability_fingerprint": _require_sha256(
                    record["declared_capability_fingerprint"],
                    f"attempt record {index} capability fingerprint",
                ),
                "campaign_id_digest": _require_sha256(
                    record["campaign_id_digest"],
                    f"attempt record {index} campaign digest",
                ),
                "cell_binding_digest": _require_sha256(
                    record["cell_binding_digest"],
                    f"attempt record {index} cell binding digest",
                ),
                "attempt_evidence_digest": _require_sha256(
                    record["attempt_evidence_digest"],
                    f"attempt record {index} evidence digest",
                ),
            }
        )

    def sort_key(item: dict[str, str]) -> tuple[str, ...]:
        return (
            item["declared_capability_fingerprint"],
            item["campaign_id_digest"],
            item["cell_binding_digest"],
            item["attempt_record_id"],
            item["attempt_evidence_digest"],
        )

    if record_mappings != sorted(record_mappings, key=sort_key):
        raise DeclaredAttemptSnapshotContractError(
            "attempt records must be canonically sorted"
        )
    record_ids = [record["attempt_record_id"] for record in record_mappings]
    evidence_digests = [
        record["attempt_evidence_digest"] for record in record_mappings
    ]
    if len(set(record_ids)) != len(record_ids):
        raise DeclaredAttemptSnapshotContractError(
            "duplicate attempt_record_id refused"
        )
    if len(set(evidence_digests)) != len(evidence_digests):
        raise DeclaredAttemptSnapshotContractError(
            "duplicate attempt_evidence_digest refused"
        )

    records = tuple(
        _DeclaredAttemptRecordFacts(
            **record,
            record_digest=sha256_digest(
                {
                    "domain": (
                        "wd.understanding.supplied_declared_attempt_record."
                        "digest.v1"
                    ),
                    **record,
                }
            ),
        )
        for record in record_mappings
    )
    record_digests = [record.record_digest for record in records]
    if len(set(record_digests)) != len(record_digests):
        raise DeclaredAttemptSnapshotContractError(
            "duplicate attempt record digest refused"
        )
    snapshot_digest = sha256_digest(
        {
            "domain": (
                "wd.understanding.supplied_declared_attempt_snapshot.digest.v1"
            ),
            "schema_version": SUPPLIED_DECLARED_ATTEMPT_SNAPSHOT_SCHEMA,
            "snapshot_id": snapshot_id,
            "attempt_history_scope_digest": scope_digest,
            "attempt_record_digests": record_digests,
        }
    )
    return _DeclaredAttemptSnapshotFacts(
        attempt_history_scope_digest=scope_digest,
        snapshot_digest=snapshot_digest,
        byte_count=len(value),
        records=records,
    )


def derive_supplied_declared_attempt_snapshot_digest(
    attempt_snapshot_utf8: bytes,
    policy: DeclaredAttemptSnapshotPolicyV1,
) -> str:
    """Derive the content digest of one valid supplied snapshot."""

    if type(policy) is not DeclaredAttemptSnapshotPolicyV1:
        raise DeclaredAttemptSnapshotContractError(
            "policy must be an exact DeclaredAttemptSnapshotPolicyV1"
        )
    policy = _snapshot_policy(policy)
    return _decode_attempt_snapshot(
        attempt_snapshot_utf8, policy
    ).snapshot_digest


def _derive_request_digest(
    *,
    policy_digest: str,
    accounting_policy_digest: str,
    expectation_digest: str,
    declared_capability_fingerprint: str,
    attempt_snapshot_digest: str,
    attempt_history_scope_digest: str,
) -> str:
    return sha256_digest(
        {
            "domain": (
                "wd.understanding.declared_attempt_snapshot_request.digest.v1"
            ),
            "schema_version": DECLARED_ATTEMPT_SNAPSHOT_REQUEST_SCHEMA,
            "policy_digest": policy_digest,
            "accounting_policy_digest": accounting_policy_digest,
            "expectation_digest": expectation_digest,
            "declared_capability_fingerprint": (
                declared_capability_fingerprint
            ),
            "attempt_snapshot_digest": attempt_snapshot_digest,
            "attempt_history_scope_digest": attempt_history_scope_digest,
        }
    )


_TRUE_RECEIPT_FIELDS = (
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
)

_FALSE_RECEIPT_FIELDS = (
    "expected_snapshot_externally_pinned",
    "expected_snapshot_digest_independently_configured",
    "expected_snapshot_digest_origin_authenticated",
    "expected_snapshot_digest_precommit_verified",
    "attempt_snapshot_origin_authenticated",
    "attempt_entry_origin_authenticated",
    "receipt_origin_authenticated",
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
    "independent_verification_applied",
    "genesis_origin_independently_verified",
    "hex_cell_binding_independently_verified",
    "echo_chamber_absence_verified",
    "scalability_50000_demonstrated",
    "provider_invoked",
    "builder_host_invoked",
    "c8a_invoked",
    "c8b_invoked",
    "c8c_invoked",
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


def _copy_expectation(
    value: object,
) -> DeclaredAttemptExpectedSnapshotDigestV1:
    if type(value) is not DeclaredAttemptExpectedSnapshotDigestV1:
        raise DeclaredAttemptSnapshotContractError(
            "expected_snapshot_digest must be an exact "
            "DeclaredAttemptExpectedSnapshotDigestV1"
        )
    try:
        DeclaredAttemptExpectedSnapshotDigestV1.__post_init__(value)
        return DeclaredAttemptExpectedSnapshotDigestV1(
            **{
                item.name: getattr(value, item.name)
                for item in fields(DeclaredAttemptExpectedSnapshotDigestV1)
            }
        )
    except (AttributeError, TypeError, ValueError):
        raise DeclaredAttemptSnapshotContractError(
            "expected snapshot digest object refused"
        ) from None


@dataclass(frozen=True, slots=True)
class DeclaredAttemptSnapshotReceiptV1:
    policy_digest: str
    accounting_policy_digest: str
    expected_snapshot_digest: DeclaredAttemptExpectedSnapshotDigestV1
    expectation_digest: str
    request_digest: str
    declared_capability_fingerprint: str
    attempt_snapshot_digest: str
    attempt_history_scope_digest: str
    max_snapshot_bytes: int
    max_attempt_records: int
    max_json_depth: int
    max_json_nodes: int
    attempt_record_count: int
    attempt_snapshot_byte_count: int
    expected_snapshot_digest_matches: bool
    subject_scan_performed: bool
    exact_match_count: int | None
    matched_attempt_record_digest: str | None
    disposition: DeclaredAttemptSnapshotDisposition
    reason_code: DeclaredAttemptSnapshotReasonCode
    receipt_digest: str
    schema_version: str = DECLARED_ATTEMPT_SNAPSHOT_RECEIPT_SCHEMA
    evaluation_only: bool = True
    shadow_only: bool = True
    static_accounting_only: bool = True
    raw_material_omitted: bool = True
    canonical_caller_supplied_attempt_snapshot_only: bool = True
    canonical_snapshot_digest_recomputed_from_supplied_bytes: bool = True
    separate_expected_snapshot_digest_required: bool = True
    expected_snapshot_digest_keyword_only: bool = True
    caller_supplied_expected_snapshot_digest_only: bool = True
    exact_expected_snapshot_digest_comparison_only: bool = True
    exact_declared_capability_fingerprint_comparison_only: bool = True
    comparison_subject_is_declared_capability_fingerprint_only: bool = True
    snapshot_local_match_count_only: bool = True
    campaign_id_not_a_match_key: bool = True
    cell_binding_digest_not_a_match_key: bool = True
    attempt_record_id_not_a_match_key: bool = True
    attempt_evidence_digest_not_a_match_key: bool = True
    expected_mismatch_refused_without_match_claim: bool = True
    ambiguous_multi_match_refused_without_selection: bool = True
    c8a_not_invoked: bool = True
    c8b_not_invoked: bool = True
    c8c_not_invoked: bool = True
    c7_not_invoked: bool = True
    no_side_effects_in_module: bool = True
    expected_snapshot_externally_pinned: bool = False
    expected_snapshot_digest_independently_configured: bool = False
    expected_snapshot_digest_origin_authenticated: bool = False
    expected_snapshot_digest_precommit_verified: bool = False
    attempt_snapshot_origin_authenticated: bool = False
    attempt_entry_origin_authenticated: bool = False
    receipt_origin_authenticated: bool = False
    durable_attempt_history_consulted: bool = False
    attempt_history_authoritative: bool = False
    attempt_history_complete: bool = False
    attempt_history_fresh: bool = False
    attempt_history_chronology_verified: bool = False
    attempt_history_monotonic: bool = False
    attempt_history_rollback_protected: bool = False
    attempt_history_fork_resolved: bool = False
    anti_replay_enforced: bool = False
    attempt_occurrence_independently_verified: bool = False
    attempt_execution_verified: bool = False
    attempt_outcome_verified: bool = False
    retry_prevented: bool = False
    state_transition_validated: bool = False
    cross_campaign_single_attempt_enforced: bool = False
    cross_cell_single_attempt_enforced: bool = False
    global_single_attempt_enforced: bool = False
    atomic_attempt_reservation_applied: bool = False
    semantic_equivalence_verified: bool = False
    semantic_deduplication_verified: bool = False
    global_deduplication_verified: bool = False
    reuse_eligibility_claimed: bool = False
    build_eligibility_claimed: bool = False
    generation_authorized: bool = False
    family_novelty_independently_verified: bool = False
    new_family_need_independently_verified: bool = False
    existing_family_deduplication_independently_verified: bool = False
    catalog_completeness_verified: bool = False
    catalog_freshness_verified: bool = False
    catalog_authenticity_verified: bool = False
    registry_snapshot_identity_independently_verified: bool = False
    family_review_status_independently_verified: bool = False
    independent_verification_applied: bool = False
    genesis_origin_independently_verified: bool = False
    hex_cell_binding_independently_verified: bool = False
    echo_chamber_absence_verified: bool = False
    scalability_50000_demonstrated: bool = False
    provider_invoked: bool = False
    builder_host_invoked: bool = False
    c8a_invoked: bool = False
    c8b_invoked: bool = False
    c8c_invoked: bool = False
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
        if type(self) is not DeclaredAttemptSnapshotReceiptV1:
            raise DeclaredAttemptSnapshotContractError(
                "receipt exact type required"
            )
        result: dict[str, Any] = {}
        for item in fields(DeclaredAttemptSnapshotReceiptV1):
            if item.name == "receipt_digest":
                continue
            value = getattr(self, item.name)
            if type(value) is DeclaredAttemptExpectedSnapshotDigestV1:
                value = DeclaredAttemptExpectedSnapshotDigestV1.to_mapping(
                    value
                )
            elif isinstance(value, Enum):
                value = value.value
            result[item.name] = value
        return result

    def __post_init__(self) -> None:
        if type(self) is not DeclaredAttemptSnapshotReceiptV1:
            raise DeclaredAttemptSnapshotContractError(
                "receipt exact type required"
            )
        if type(self.schema_version) is not str or (
            self.schema_version != DECLARED_ATTEMPT_SNAPSHOT_RECEIPT_SCHEMA
        ):
            raise DeclaredAttemptSnapshotContractError(
                "receipt schema_version refused"
            )
        for name in (
            "policy_digest",
            "accounting_policy_digest",
            "expectation_digest",
            "request_digest",
            "declared_capability_fingerprint",
            "attempt_snapshot_digest",
            "attempt_history_scope_digest",
            "receipt_digest",
        ):
            _require_sha256(getattr(self, name), name)
        if (
            self.accounting_policy_digest
            != DECLARED_ATTEMPT_ACCOUNTING_POLICY_DIGEST
        ):
            raise DeclaredAttemptSnapshotContractError(
                "receipt accounting policy refused"
            )
        expected_policy = DeclaredAttemptSnapshotPolicyV1(
            mode=DeclaredAttemptSnapshotMode.STATIC_SHADOW,
            max_snapshot_bytes=self.max_snapshot_bytes,
            max_attempt_records=self.max_attempt_records,
            max_json_depth=self.max_json_depth,
            max_json_nodes=self.max_json_nodes,
            accounting_policy_digest=self.accounting_policy_digest,
        )
        if self.policy_digest != expected_policy.policy_digest:
            raise DeclaredAttemptSnapshotContractError(
                "receipt policy relation mismatch"
            )
        expectation = _copy_expectation(self.expected_snapshot_digest)
        if self.expectation_digest != expectation.expectation_digest:
            raise DeclaredAttemptSnapshotContractError(
                "receipt expectation relation mismatch"
            )
        expected_request_digest = _derive_request_digest(
            policy_digest=self.policy_digest,
            accounting_policy_digest=self.accounting_policy_digest,
            expectation_digest=self.expectation_digest,
            declared_capability_fingerprint=(
                self.declared_capability_fingerprint
            ),
            attempt_snapshot_digest=self.attempt_snapshot_digest,
            attempt_history_scope_digest=self.attempt_history_scope_digest,
        )
        if self.request_digest != expected_request_digest:
            raise DeclaredAttemptSnapshotContractError(
                "receipt request relation mismatch"
            )
        _require_bounded_int(
            self.attempt_record_count,
            "attempt_record_count",
            minimum=0,
            maximum=_ABSOLUTE_MAX_ATTEMPT_RECORDS,
        )
        _require_bounded_int(
            self.attempt_snapshot_byte_count,
            "attempt_snapshot_byte_count",
            minimum=1,
            maximum=_ABSOLUTE_MAX_SNAPSHOT_BYTES,
        )
        if self.attempt_record_count > self.max_attempt_records:
            raise DeclaredAttemptSnapshotContractError(
                "attempt record count exceeds receipt policy"
            )
        if self.attempt_snapshot_byte_count > self.max_snapshot_bytes:
            raise DeclaredAttemptSnapshotContractError(
                "attempt snapshot byte count exceeds receipt policy"
            )
        minimum_snapshot_bytes, maximum_snapshot_bytes = (
            _canonical_snapshot_byte_bounds(self.attempt_record_count)
        )
        if not (
            minimum_snapshot_bytes
            <= self.attempt_snapshot_byte_count
            <= maximum_snapshot_bytes
        ):
            raise DeclaredAttemptSnapshotContractError(
                "attempt snapshot byte count is impossible for receipt count"
            )
        required_json_nodes = 5 + (6 * self.attempt_record_count)
        if required_json_nodes > self.max_json_nodes:
            raise DeclaredAttemptSnapshotContractError(
                "attempt snapshot JSON node count exceeds receipt policy"
            )
        required_json_depth = 4 if self.attempt_record_count else 2
        if required_json_depth > self.max_json_depth:
            raise DeclaredAttemptSnapshotContractError(
                "attempt snapshot JSON depth exceeds receipt policy"
            )
        if type(self.expected_snapshot_digest_matches) is not bool:
            raise DeclaredAttemptSnapshotContractError(
                "expected_snapshot_digest_matches must be an exact bool"
            )
        if type(self.subject_scan_performed) is not bool:
            raise DeclaredAttemptSnapshotContractError(
                "subject_scan_performed must be an exact bool"
            )
        expected_digest_matches = (
            self.attempt_snapshot_digest
            == expectation.expected_attempt_snapshot_digest
        )
        if self.expected_snapshot_digest_matches is not expected_digest_matches:
            raise DeclaredAttemptSnapshotContractError(
                "receipt expected digest relation mismatch"
            )
        if type(self.disposition) is not DeclaredAttemptSnapshotDisposition:
            raise DeclaredAttemptSnapshotContractError(
                "receipt disposition refused"
            )
        if type(self.reason_code) is not DeclaredAttemptSnapshotReasonCode:
            raise DeclaredAttemptSnapshotContractError(
                "receipt reason_code refused"
            )

        if not expected_digest_matches:
            if self.subject_scan_performed is not False:
                raise DeclaredAttemptSnapshotContractError(
                    "expected mismatch must not claim a subject scan"
                )
            if self.exact_match_count is not None:
                raise DeclaredAttemptSnapshotContractError(
                    "expected mismatch must omit match count"
                )
            if self.matched_attempt_record_digest is not None:
                raise DeclaredAttemptSnapshotContractError(
                    "expected mismatch must omit matched record"
                )
            if (
                self.disposition
                is not DeclaredAttemptSnapshotDisposition.REFUSED
                or self.reason_code
                is not DeclaredAttemptSnapshotReasonCode.EXPECTED_ATTEMPT_SNAPSHOT_DIGEST_MISMATCH
            ):
                raise DeclaredAttemptSnapshotContractError(
                    "expected mismatch disposition relation refused"
                )
        else:
            if self.subject_scan_performed is not True:
                raise DeclaredAttemptSnapshotContractError(
                    "matching expected digest requires subject scan"
                )
            exact_match_count = _require_bounded_int(
                self.exact_match_count,
                "exact_match_count",
                minimum=0,
                maximum=_ABSOLUTE_MAX_ATTEMPT_RECORDS,
            )
            if exact_match_count > self.attempt_record_count:
                raise DeclaredAttemptSnapshotContractError(
                    "match count exceeds attempt record count"
                )
            if exact_match_count == 0:
                if self.matched_attempt_record_digest is not None:
                    raise DeclaredAttemptSnapshotContractError(
                        "no-match receipt must omit matched record"
                    )
                if (
                    self.disposition
                    is not DeclaredAttemptSnapshotDisposition.NO_EXACT_DECLARED_CAPABILITY_MATCH_IN_SUPPLIED_ATTEMPT_SNAPSHOT
                    or self.reason_code
                    is not DeclaredAttemptSnapshotReasonCode.NO_EXACT_DECLARED_CAPABILITY_MATCH
                ):
                    raise DeclaredAttemptSnapshotContractError(
                        "no-match disposition relation refused"
                    )
            elif exact_match_count == 1:
                _require_sha256(
                    self.matched_attempt_record_digest,
                    "matched_attempt_record_digest",
                )
                if (
                    self.disposition
                    is not DeclaredAttemptSnapshotDisposition.EXACTLY_ONE_DECLARED_CAPABILITY_MATCH_IN_SUPPLIED_ATTEMPT_SNAPSHOT
                    or self.reason_code
                    is not DeclaredAttemptSnapshotReasonCode.EXACTLY_ONE_EXACT_DECLARED_CAPABILITY_MATCH
                ):
                    raise DeclaredAttemptSnapshotContractError(
                        "one-match disposition relation refused"
                    )
            else:
                if self.matched_attempt_record_digest is not None:
                    raise DeclaredAttemptSnapshotContractError(
                        "ambiguous receipt must omit matched record"
                    )
                if (
                    self.disposition
                    is not DeclaredAttemptSnapshotDisposition.REFUSED
                    or self.reason_code
                    is not DeclaredAttemptSnapshotReasonCode.AMBIGUOUS_MULTIPLE_EXACT_DECLARED_CAPABILITY_MATCHES
                ):
                    raise DeclaredAttemptSnapshotContractError(
                        "ambiguous disposition relation refused"
                    )

        for name in _TRUE_RECEIPT_FIELDS:
            if getattr(self, name) is not True:
                raise DeclaredAttemptSnapshotContractError(
                    f"{name} must be literal true"
                )
        for name in _FALSE_RECEIPT_FIELDS:
            if getattr(self, name) is not False:
                raise DeclaredAttemptSnapshotContractError(
                    f"{name} must be literal false"
                )
        expected_receipt_digest = sha256_digest(
            {
                "domain": (
                    "wd.understanding.declared_attempt_snapshot_receipt."
                    "digest.v1"
                ),
                **DeclaredAttemptSnapshotReceiptV1._core_mapping(self),
            }
        )
        if self.receipt_digest != expected_receipt_digest:
            raise DeclaredAttemptSnapshotContractError(
                "receipt digest does not match fields"
            )

    def to_mapping(self) -> dict[str, Any]:
        DeclaredAttemptSnapshotReceiptV1.__post_init__(self)
        return {
            **DeclaredAttemptSnapshotReceiptV1._core_mapping(self),
            "receipt_digest": self.receipt_digest,
        }


def _snapshot_policy(
    policy: DeclaredAttemptSnapshotPolicyV1,
) -> DeclaredAttemptSnapshotPolicyV1:
    try:
        DeclaredAttemptSnapshotPolicyV1.__post_init__(policy)
        values = {
            item.name: getattr(policy, item.name)
            for item in fields(DeclaredAttemptSnapshotPolicyV1)
        }
    except AttributeError:
        raise DeclaredAttemptSnapshotContractError(
            "policy fields refused"
        ) from None
    return DeclaredAttemptSnapshotPolicyV1(**values)


def _snapshot_request(
    request: DeclaredAttemptSnapshotRequestV1,
) -> DeclaredAttemptSnapshotRequestV1:
    try:
        DeclaredAttemptSnapshotRequestV1.__post_init__(request)
        values = {
            "declared_capability_fingerprint": (
                request.declared_capability_fingerprint
            ),
            "attempt_snapshot_utf8": bytes(request.attempt_snapshot_utf8),
            "schema_version": request.schema_version,
        }
    except AttributeError:
        raise DeclaredAttemptSnapshotContractError(
            "request fields refused"
        ) from None
    return DeclaredAttemptSnapshotRequestV1(**values)


def _receipt_core(values: dict[str, Any]) -> dict[str, Any]:
    return {
        **values,
        "schema_version": DECLARED_ATTEMPT_SNAPSHOT_RECEIPT_SCHEMA,
        **{name: True for name in _TRUE_RECEIPT_FIELDS},
        **{name: False for name in _FALSE_RECEIPT_FIELDS},
    }


def _normalize_receipt_core(values: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in values.items():
        if type(value) is DeclaredAttemptExpectedSnapshotDigestV1:
            value = DeclaredAttemptExpectedSnapshotDigestV1.to_mapping(value)
        elif isinstance(value, Enum):
            value = value.value
        result[name] = value
    return result


def _make_receipt(
    values: dict[str, Any],
) -> DeclaredAttemptSnapshotReceiptV1:
    core = _receipt_core(values)
    receipt_digest = sha256_digest(
        {
            "domain": (
                "wd.understanding.declared_attempt_snapshot_receipt.digest.v1"
            ),
            **_normalize_receipt_core(core),
        }
    )
    return DeclaredAttemptSnapshotReceiptV1(
        **values,
        receipt_digest=receipt_digest,
    )


def evaluate_declared_attempt_snapshot(
    request: DeclaredAttemptSnapshotRequestV1 | None = None,
    *,
    expected_snapshot_digest: (
        DeclaredAttemptExpectedSnapshotDigestV1 | None
    ) = None,
    policy: DeclaredAttemptSnapshotPolicyV1 | None = None,
) -> DeclaredAttemptSnapshotReceiptV1 | None:
    """Account for exact subject matches inside one supplied snapshot.

    OFF returns before inspecting request or expectation. A no-match result is
    only absence from this supplied snapshot; it is not novelty, retry, build,
    or generation permission.
    """

    if policy is None:
        policy = DeclaredAttemptSnapshotPolicyV1()
    elif type(policy) is not DeclaredAttemptSnapshotPolicyV1:
        raise DeclaredAttemptSnapshotContractError(
            "policy must be an exact DeclaredAttemptSnapshotPolicyV1"
        )
    try:
        selected_mode = policy.mode
    except AttributeError:
        raise DeclaredAttemptSnapshotContractError(
            "policy fields refused"
        ) from None
    if selected_mode is DeclaredAttemptSnapshotMode.OFF:
        return None
    policy = _snapshot_policy(policy)
    if policy.mode is not DeclaredAttemptSnapshotMode.STATIC_SHADOW:
        raise DeclaredAttemptSnapshotContractError("unsupported C8d mode")
    if type(request) is not DeclaredAttemptSnapshotRequestV1:
        raise DeclaredAttemptSnapshotContractError(
            "STATIC_SHADOW requires an exact DeclaredAttemptSnapshotRequestV1"
        )
    request = _snapshot_request(request)
    expectation = _copy_expectation(expected_snapshot_digest)
    snapshot = _decode_attempt_snapshot(
        request.attempt_snapshot_utf8,
        policy,
    )

    expected_matches = (
        snapshot.snapshot_digest
        == expectation.expected_attempt_snapshot_digest
    )
    if not expected_matches:
        subject_scan_performed = False
        exact_match_count: int | None = None
        matched_record_digest: str | None = None
        disposition = DeclaredAttemptSnapshotDisposition.REFUSED
        reason = (
            DeclaredAttemptSnapshotReasonCode.EXPECTED_ATTEMPT_SNAPSHOT_DIGEST_MISMATCH
        )
    else:
        subject_scan_performed = True
        matches = tuple(
            record
            for record in snapshot.records
            if record.declared_capability_fingerprint
            == request.declared_capability_fingerprint
        )
        exact_match_count = len(matches)
        if exact_match_count == 0:
            matched_record_digest = None
            disposition = (
                DeclaredAttemptSnapshotDisposition.NO_EXACT_DECLARED_CAPABILITY_MATCH_IN_SUPPLIED_ATTEMPT_SNAPSHOT
            )
            reason = (
                DeclaredAttemptSnapshotReasonCode.NO_EXACT_DECLARED_CAPABILITY_MATCH
            )
        elif exact_match_count == 1:
            matched_record_digest = matches[0].record_digest
            disposition = (
                DeclaredAttemptSnapshotDisposition.EXACTLY_ONE_DECLARED_CAPABILITY_MATCH_IN_SUPPLIED_ATTEMPT_SNAPSHOT
            )
            reason = (
                DeclaredAttemptSnapshotReasonCode.EXACTLY_ONE_EXACT_DECLARED_CAPABILITY_MATCH
            )
        else:
            matched_record_digest = None
            disposition = DeclaredAttemptSnapshotDisposition.REFUSED
            reason = (
                DeclaredAttemptSnapshotReasonCode.AMBIGUOUS_MULTIPLE_EXACT_DECLARED_CAPABILITY_MATCHES
            )

    expectation_digest = expectation.expectation_digest
    request_digest = _derive_request_digest(
        policy_digest=policy.policy_digest,
        accounting_policy_digest=policy.accounting_policy_digest,
        expectation_digest=expectation_digest,
        declared_capability_fingerprint=(
            request.declared_capability_fingerprint
        ),
        attempt_snapshot_digest=snapshot.snapshot_digest,
        attempt_history_scope_digest=snapshot.attempt_history_scope_digest,
    )
    return _make_receipt(
        {
            "policy_digest": policy.policy_digest,
            "accounting_policy_digest": policy.accounting_policy_digest,
            "expected_snapshot_digest": expectation,
            "expectation_digest": expectation_digest,
            "request_digest": request_digest,
            "declared_capability_fingerprint": (
                request.declared_capability_fingerprint
            ),
            "attempt_snapshot_digest": snapshot.snapshot_digest,
            "attempt_history_scope_digest": (
                snapshot.attempt_history_scope_digest
            ),
            "max_snapshot_bytes": policy.max_snapshot_bytes,
            "max_attempt_records": policy.max_attempt_records,
            "max_json_depth": policy.max_json_depth,
            "max_json_nodes": policy.max_json_nodes,
            "attempt_record_count": len(snapshot.records),
            "attempt_snapshot_byte_count": snapshot.byte_count,
            "expected_snapshot_digest_matches": expected_matches,
            "subject_scan_performed": subject_scan_performed,
            "exact_match_count": exact_match_count,
            "matched_attempt_record_digest": matched_record_digest,
            "disposition": disposition,
            "reason_code": reason,
        }
    )


__all__ = [
    "DECLARED_ATTEMPT_ACCOUNTING_POLICY_DIGEST",
    "DECLARED_ATTEMPT_EXPECTED_SNAPSHOT_DIGEST_SCHEMA",
    "DECLARED_ATTEMPT_SNAPSHOT_POLICY_SCHEMA",
    "DECLARED_ATTEMPT_SNAPSHOT_RECEIPT_SCHEMA",
    "DECLARED_ATTEMPT_SNAPSHOT_REQUEST_SCHEMA",
    "SUPPLIED_DECLARED_ATTEMPT_SNAPSHOT_SCHEMA",
    "DeclaredAttemptExpectedSnapshotDigestV1",
    "DeclaredAttemptSnapshotContractError",
    "DeclaredAttemptSnapshotDisposition",
    "DeclaredAttemptSnapshotMode",
    "DeclaredAttemptSnapshotPolicyV1",
    "DeclaredAttemptSnapshotReasonCode",
    "DeclaredAttemptSnapshotReceiptV1",
    "DeclaredAttemptSnapshotRequestV1",
    "derive_supplied_declared_attempt_snapshot_digest",
    "evaluate_declared_attempt_snapshot",
]
