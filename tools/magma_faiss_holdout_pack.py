# SPDX-License-Identifier: BUSL-1.1
"""Pure query-pack and dual-adjudication projections for MAGMA holdout.

The public query-capture projection is derived from the exact committed frame
and selection, uses fresh keyed capture IDs, and contains no stage metadata:
only its schema, count, all-false capability boundary, capture IDs, and query
text.  A separate private binding manifest binds that payload to the protocol,
frame, selection, declared chronology, and key identifiers.  Two raw
adjudication packs independently bind the resulting query-pack commitment.  A
seal projection is produced only when both packs cover all 257 selected cases,
agree exactly, contain no invalid/ambiguous label, and match the preselection
design targets.

This module never writes a pack, captures a query, opens Git/FAISS, scores a
candidate, reveals labels, or grants evidence/runtime/promotion authority.
Actor identity, score/design/peer-label blindness, key custody, trusted time,
publication, one-shot execution, and real capture isolation remain external
adapter responsibilities and are explicitly false in every capability block.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import datetime, timezone
from typing import Any, Sequence

from tools import magma_faiss_holdout_frame as frame_contract
from tools import magma_faiss_holdout_protocol as protocol_contract
from waggledance.core.hex_cell_topology import ALL_CELLS
from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest


QUERY_CAPTURE_SCHEMA = "wd.magma.faiss_holdout_query_capture_projection.v1"
QUERY_PACK_BINDING_SCHEMA = "wd.magma.faiss_holdout_query_pack_binding.v1"
RAW_LABEL_PACK_SCHEMA = "wd.magma.faiss_holdout_raw_label_pack.v1"
ADJUDICATION_COMPARISON_SCHEMA = (
    "wd.magma.faiss_holdout_adjudication_comparison_projection.v1"
)
CONSENSUS_LABEL_PACK_SCHEMA = "wd.magma.faiss_holdout_consensus_label_pack.v1"
PACK_SEAL_SCHEMA = "wd.magma.faiss_holdout_pack_seal_projection.v1"
CAPTURE_ID_CONTEXT = b"WD-MAGMA-FAISS-HOLDOUT-CAPTURE-ID-v1"

_FULL_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_HMAC_SHA256 = re.compile(r"^hmac-sha256:[0-9a-f]{64}$")
_SOLVER_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_CAPTURE_ID = re.compile(r"^capture_[0-9a-f]{64}$")
_ACTOR_ID = re.compile(r"^actor_[0-9a-f]{64}$")
_KEY_ID = re.compile(r"^key_[0-9a-f]{64}$")
_CANONICAL_UTC = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
)

_QUERY_CAPTURE_KEYS = frozenset(
    {
        "schema_version",
        "query_count",
        "queries",
        "capability_boundary",
    }
)
_QUERY_BINDING_KEYS = frozenset(
    {
        "schema_version",
        "protocol_digest",
        "frame_commitment",
        "selection_projection_digest",
        "selection_attempt",
        "declared_selection_completed_at_utc",
        "declared_query_pack_frozen_at_utc",
        "capture_id_key_id",
        "commitment_key_id",
        "query_capture_projection_digest",
        "query_count",
        "capability_boundary",
    }
)
_QUERY_ROW_KEYS = frozenset({"capture_id", "query"})
_RAW_PACK_KEYS = frozenset(
    {
        "schema_version",
        "protocol_digest",
        "frame_commitment",
        "selection_projection_digest",
        "query_pack_commitment",
        "adjudicator_actor_id",
        "commitment_key_id",
        "declared_adjudication_started_at_utc",
        "declared_raw_pack_sealed_at_utc",
        "attestations",
        "label_count",
        "labels",
        "capability_boundary",
    }
)
_RAW_ATTESTATION_KEYS = frozenset(
    {
        "query_pack_only_case_input",
        "candidate_scores_unseen",
        "peer_raw_label_plaintext_unseen_before_own_seal",
        "design_cell_metadata_unseen",
        "reconciliation_performed",
        "post_selection_replacement_performed",
    }
)
_RAW_LABEL_KEYS = frozenset(
    {
        "capture_id",
        "disposition",
        "expected_solver_id",
        "expected_cell_id",
        "ood_class",
    }
)
_COMPARISON_KEYS = frozenset(
    {
        "schema_version",
        "protocol_digest",
        "frame_commitment",
        "selection_projection_digest",
        "query_pack_commitment",
        "raw_label_bindings",
        "adjudication_key_id",
        "compared_case_count",
        "equal_label_count",
        "disagreement_count",
        "invalid_or_ambiguous_count",
        "design_target_mismatch_count",
        "exact_agreement",
        "reconciliation_performed",
        "replacement_performed",
        "declared_agreement_checked_at_utc",
    }
)
_RAW_BINDING_KEYS = frozenset(
    {"adjudicator_actor_id", "key_id", "hmac_commitment"}
)
_PACK_SEAL_KEYS = frozenset(
    {
        "schema_version",
        "protocol_digest",
        "frame_commitment",
        "selection_projection_digest",
        "selection_attempt",
        "query_pack_key_id",
        "query_pack_commitment",
        "raw_label_bindings",
        "adjudication_key_id",
        "adjudication_commitment",
        "label_pack_key_id",
        "label_pack_commitment",
        "declared_agreement_checked_at_utc",
        "declared_pack_sealed_at_utc",
        "capture_not_started_attestation",
        "capability_boundary",
    }
)
_CONSENSUS_LABEL_PACK_KEYS = frozenset(
    {
        "schema_version",
        "protocol_digest",
        "frame_commitment",
        "selection_projection_digest",
        "query_pack_commitment",
        "raw_label_bindings",
        "adjudication_commitment",
        "label_pack_key_id",
        "declared_pack_sealed_at_utc",
        "case_count",
        "labels",
    }
)
_CAPABILITY_KEYS = frozenset(
    {
        "artifact_class",
        "external_provenance_verified",
        "actor_identity_verified",
        "trusted_time_verified",
        "score_blindness_verified",
        "adjudicator_independence_verified",
        "adjudicator_model_family_independence_verified",
        "query_order_blinding_verified",
        "adjudication_authenticity_verified",
        "label_seal_publication_verified",
        "query_pack_custody_verified",
        "hmac_key_custody_verified",
        "private_pack_confidentiality_verified",
        "capture_isolation_verified",
        "candidate_population_authoring_blindness_verified",
        "opaque_case_id_blinding_verified",
        "semantic_uniqueness_externally_verified",
        "semantic_label_correctness_verified",
        "seed_unbiased_verified",
        "seed_precommit_verified",
        "git_ancestry_verified",
        "one_shot_enforced",
        "query_capture_performed",
        "holdout_pack_created",
        "holdout_evidence_gate_met",
        "runtime_threshold_selected",
        "runtime_configuration_written",
        "production_runtime_path_evaluated",
        "runtime_authority_granted",
        "candidate_mode_change_authorized",
        "cell_pruning_authorized",
        "production_promotion_gate_pass",
    }
)


class HoldoutPackError(ValueError):
    """A query-pack, raw-label, or pack-seal projection is invalid."""


def _exact_dict(
    value: Any,
    expected_keys: frozenset[str],
    label: str,
) -> dict[str, Any]:
    if type(value) is not dict:
        raise HoldoutPackError(f"{label}_must_be_exact_object")
    if any(type(key) is not str for key in value):
        raise HoldoutPackError(f"{label}_keys_must_be_exact_strings")
    actual = frozenset(value)
    if actual != expected_keys:
        raise HoldoutPackError(
            f"{label}_keys_mismatch:missing={sorted(expected_keys - actual)},"
            f"extra={sorted(actual - expected_keys)}"
        )
    return value


def _exact_list(value: Any, label: str) -> list[Any]:
    if type(value) is not list:
        raise HoldoutPackError(f"{label}_must_be_exact_list")
    return value


def _exact_string(value: Any, label: str) -> str:
    if type(value) is not str or not value:
        raise HoldoutPackError(f"{label}_must_be_nonempty_exact_string")
    return value


def _regex_string(value: Any, pattern: re.Pattern[str], label: str) -> str:
    result = _exact_string(value, label)
    if pattern.fullmatch(result) is None:
        raise HoldoutPackError(f"{label}_invalid")
    return result


def _exact_bool(value: Any, expected: bool, label: str) -> None:
    if type(value) is not bool or value is not expected:
        raise HoldoutPackError(f"{label}_must_be_{str(expected).lower()}")


def _exact_int(value: Any, expected: int, label: str) -> None:
    if type(value) is not int or value != expected:
        raise HoldoutPackError(f"{label}_must_equal_{expected}")


def _canonical_utc(value: Any, label: str) -> tuple[str, datetime]:
    result = _regex_string(value, _CANONICAL_UTC, label)
    try:
        parsed = datetime.strptime(result, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise HoldoutPackError(f"{label}_invalid") from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != result:
        raise HoldoutPackError(f"{label}_not_canonical")
    return result, parsed


def _canonical_clone(value: Any, label: str) -> Any:
    try:
        return json.loads(canonical_json_bytes(value))
    except (TypeError, ValueError, OverflowError) as exc:
        raise HoldoutPackError(f"{label}_is_not_strict_json") from exc


def _no_authority_boundary(artifact_class: str) -> dict[str, Any]:
    return {
        "artifact_class": artifact_class,
        **{key: False for key in _CAPABILITY_KEYS - {"artifact_class"}},
    }


def _validate_capability_boundary(
    value: Any,
    *,
    artifact_class: str,
) -> None:
    boundary = _exact_dict(value, _CAPABILITY_KEYS, "capability_boundary")
    if _exact_string(
        boundary["artifact_class"],
        "capability_boundary_artifact_class",
    ) != artifact_class:
        raise HoldoutPackError("capability_boundary_artifact_class_mismatch")
    for key in _CAPABILITY_KEYS - {"artifact_class"}:
        _exact_bool(boundary[key], False, f"capability_boundary_{key}")


def _validated_protocol(value: Any) -> dict[str, Any]:
    try:
        return protocol_contract.validate_preregistration(value)
    except protocol_contract.HoldoutProtocolError as exc:
        raise HoldoutPackError("preregistration_invalid") from exc


def _validated_frame(
    value: Any,
    *,
    protocol: Any,
    preregistration_state: Any,
) -> dict[str, Any]:
    try:
        return frame_contract.validate_frame(
            value,
            protocol=protocol,
            preregistration_state=preregistration_state,
        )
    except frame_contract.HoldoutFrameError as exc:
        raise HoldoutPackError("frame_invalid") from exc


def _validated_selection(
    value: Any,
    *,
    frame: Any,
    protocol: Any,
    preregistration_state: Any,
    selection_seed: bytes,
    frame_commitment_key: bytes,
    expected_frame_commitment: str,
) -> dict[str, Any]:
    try:
        return frame_contract.validate_selection_projection(
            value,
            frame=frame,
            protocol=protocol,
            preregistration_state=preregistration_state,
            selection_seed=selection_seed,
            frame_commitment_key=frame_commitment_key,
            expected_frame_commitment=expected_frame_commitment,
        )
    except frame_contract.HoldoutFrameError as exc:
        raise HoldoutPackError("selection_projection_invalid") from exc


def _exact_key(value: Any, label: str) -> bytes:
    if type(value) is not bytes or len(value) != 32:
        raise HoldoutPackError(f"{label}_must_be_exactly_32_bytes")
    return value


def _validate_key_map(value: Any, expected_ids: set[str]) -> dict[str, bytes]:
    if (
        type(value) is not dict
        or any(type(key) is not str for key in value)
        or set(value) != expected_ids
    ):
        raise HoldoutPackError("raw_label_pack_keys_mismatch")
    return {
        key_id: _exact_key(value[key_id], f"raw_label_pack_key_{key_id}")
        for key_id in sorted(value)
    }


def _validate_distinct_key_material(
    named_keys: Sequence[tuple[str, bytes]],
) -> None:
    if len({value for _, value in named_keys}) != len(named_keys):
        raise HoldoutPackError("holdout_pack_keys_must_be_pairwise_distinct")


def _validate_distinct_key_ids(named_ids: Sequence[tuple[str, str]]) -> None:
    for label, value in named_ids:
        _regex_string(value, _KEY_ID, label)
    if len({value for _, value in named_ids}) != len(named_ids):
        raise HoldoutPackError("holdout_pack_key_ids_must_be_pairwise_distinct")


def _capture_id(
    key: bytes,
    *,
    protocol_digest: str,
    frame_commitment: str,
    selection_projection_digest: str,
    case: dict[str, Any],
) -> str:
    payload = canonical_json_bytes(
        {
            "protocol_digest": protocol_digest,
            "frame_commitment": frame_commitment,
            "selection_projection_digest": selection_projection_digest,
            "opaque_case_id": case["opaque_case_id"],
            "case_record_digest": sha256_digest(case),
        }
    )
    digest = hmac.new(
        key,
        b"\0".join((CAPTURE_ID_CONTEXT, payload)),
        hashlib.sha256,
    ).hexdigest()
    return f"capture_{digest}"


def _bound_inputs(
    frame: Any,
    *,
    protocol: Any,
    preregistration_state: Any,
    selection_projection: Any,
    selection_seed: bytes,
    frame_commitment_key: bytes,
    expected_frame_commitment: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str, str]:
    validated_protocol = _validated_protocol(protocol)
    protocol_digest = protocol_contract.preregistration_digest(
        validated_protocol
    )
    frame_commitment = _regex_string(
        expected_frame_commitment,
        _HMAC_SHA256,
        "expected_frame_commitment",
    )
    validated_frame = _validated_frame(
        frame,
        protocol=validated_protocol,
        preregistration_state=preregistration_state,
    )
    validated_selection = _validated_selection(
        selection_projection,
        frame=validated_frame,
        protocol=validated_protocol,
        preregistration_state=preregistration_state,
        selection_seed=selection_seed,
        frame_commitment_key=frame_commitment_key,
        expected_frame_commitment=frame_commitment,
    )
    return (
        validated_protocol,
        validated_frame,
        validated_selection,
        protocol_digest,
        frame_commitment,
    )


def build_query_capture_projection(
    frame: Any,
    *,
    protocol: Any,
    preregistration_state: Any,
    selection_projection: Any,
    selection_seed: bytes,
    frame_commitment_key: bytes,
    expected_frame_commitment: str,
    capture_id_key: bytes,
) -> dict[str, Any]:
    """Derive the exact label-free payload exposed to query capture."""

    capture_key = _exact_key(capture_id_key, "capture_id_key")
    (
        _,
        validated_frame,
        validated_selection,
        protocol_digest,
        frame_commitment,
    ) = _bound_inputs(
        frame,
        protocol=protocol,
        preregistration_state=preregistration_state,
        selection_projection=selection_projection,
        selection_seed=selection_seed,
        frame_commitment_key=frame_commitment_key,
        expected_frame_commitment=expected_frame_commitment,
    )
    selection_digest = sha256_digest(validated_selection)
    case_by_id = {
        case["opaque_case_id"]: case
        for case in [
            *validated_frame["positive_cases"],
            *validated_frame["ood_cases"],
        ]
    }
    queries: list[dict[str, str]] = []
    for case_id in validated_selection["selected_case_ids"]:
        case = case_by_id[case_id]
        queries.append(
            {
                "capture_id": _capture_id(
                    capture_key,
                    protocol_digest=protocol_digest,
                    frame_commitment=frame_commitment,
                    selection_projection_digest=selection_digest,
                    case=case,
                ),
                "query": case["query"],
            }
        )
    queries.sort(key=lambda row: row["capture_id"])
    if len({row["capture_id"] for row in queries}) != len(queries):
        raise HoldoutPackError("capture_id_collision")
    projection = {
        "schema_version": QUERY_CAPTURE_SCHEMA,
        "query_count": len(queries),
        "queries": queries,
        "capability_boundary": _no_authority_boundary(
            "label_free_query_capture_projection_only"
        ),
    }
    return _validate_query_capture_shape(projection)


def _validate_query_capture_shape(value: Any) -> dict[str, Any]:
    projection = _exact_dict(
        value,
        _QUERY_CAPTURE_KEYS,
        "query_capture_projection",
    )
    if _exact_string(
        projection["schema_version"], "query_capture_schema_version"
    ) != QUERY_CAPTURE_SCHEMA:
        raise HoldoutPackError("query_capture_schema_mismatch")
    expected_count = (
        protocol_contract.POSITIVE_CLUSTER_COUNT
        + protocol_contract.OOD_CLUSTER_COUNT
    )
    _exact_int(
        projection["query_count"], expected_count, "query_capture_query_count"
    )
    queries = _exact_list(projection["queries"], "query_capture_queries")
    if len(queries) != expected_count:
        raise HoldoutPackError("query_capture_query_count_mismatch")
    capture_ids: list[str] = []
    for index, raw in enumerate(queries):
        row = _exact_dict(
            raw,
            _QUERY_ROW_KEYS,
            f"query_capture_queries_{index}",
        )
        capture_ids.append(
            _regex_string(
                row["capture_id"],
                _CAPTURE_ID,
                f"query_capture_queries_{index}_capture_id",
            )
        )
        query = _exact_string(
            row["query"], f"query_capture_queries_{index}_query"
        )
        if len(query.encode("utf-8")) > frame_contract.MAX_QUERY_UTF8_BYTES:
            raise HoldoutPackError(
                f"query_capture_queries_{index}_query_too_large"
            )
    if len(set(capture_ids)) != len(capture_ids):
        raise HoldoutPackError("query_capture_ids_must_be_unique")
    if capture_ids != sorted(capture_ids):
        raise HoldoutPackError("query_capture_ids_not_canonical")
    _validate_capability_boundary(
        projection["capability_boundary"],
        artifact_class="label_free_query_capture_projection_only",
    )
    return _canonical_clone(projection, "query_capture_projection")


def validate_query_capture_projection(
    value: Any,
    *,
    frame: Any,
    protocol: Any,
    preregistration_state: Any,
    selection_projection: Any,
    selection_seed: bytes,
    frame_commitment_key: bytes,
    expected_frame_commitment: str,
    capture_id_key: bytes,
) -> dict[str, Any]:
    """Validate a capture payload by exact frame/selection recomputation."""

    observed = _validate_query_capture_shape(value)
    expected = build_query_capture_projection(
        frame,
        protocol=protocol,
        preregistration_state=preregistration_state,
        selection_projection=selection_projection,
        selection_seed=selection_seed,
        frame_commitment_key=frame_commitment_key,
        expected_frame_commitment=expected_frame_commitment,
        capture_id_key=capture_id_key,
    )
    if not hmac.compare_digest(
        canonical_json_bytes(observed), canonical_json_bytes(expected)
    ):
        raise HoldoutPackError("query_capture_recomputation_mismatch")
    return observed


def build_query_pack_binding(
    query_capture_projection: Any,
    *,
    frame: Any,
    protocol: Any,
    preregistration_state: Any,
    selection_projection: Any,
    selection_seed: bytes,
    frame_commitment_key: bytes,
    expected_frame_commitment: str,
    capture_id_key: bytes,
    capture_id_key_id: str,
    query_pack_key_id: str,
    declared_selection_completed_at_utc: str,
    declared_query_pack_frozen_at_utc: str,
) -> dict[str, Any]:
    """Bind the private protocol/stage metadata to the capture payload."""

    capture = validate_query_capture_projection(
        query_capture_projection,
        frame=frame,
        protocol=protocol,
        preregistration_state=preregistration_state,
        selection_projection=selection_projection,
        selection_seed=selection_seed,
        frame_commitment_key=frame_commitment_key,
        expected_frame_commitment=expected_frame_commitment,
        capture_id_key=capture_id_key,
    )
    _validate_distinct_key_ids(
        (
            ("capture_id_key_id", capture_id_key_id),
            ("query_pack_key_id", query_pack_key_id),
        )
    )
    (
        _,
        validated_frame,
        validated_selection,
        protocol_digest,
        frame_commitment,
    ) = _bound_inputs(
        frame,
        protocol=protocol,
        preregistration_state=preregistration_state,
        selection_projection=selection_projection,
        selection_seed=selection_seed,
        frame_commitment_key=frame_commitment_key,
        expected_frame_commitment=expected_frame_commitment,
    )
    selection_text, selection_at = _canonical_utc(
        declared_selection_completed_at_utc,
        "declared_selection_completed_at_utc",
    )
    frozen_text, query_frozen_at = _canonical_utc(
        declared_query_pack_frozen_at_utc,
        "declared_query_pack_frozen_at_utc",
    )
    _, frame_frozen_at = _canonical_utc(
        validated_frame["frame_frozen_at_utc"], "frame_frozen_at_utc"
    )
    if selection_at <= frame_frozen_at:
        raise HoldoutPackError("selection_must_follow_frame_freeze")
    if query_frozen_at < selection_at:
        raise HoldoutPackError("query_pack_freeze_must_not_precede_selection")
    return _validate_query_binding_shape(
        {
            "schema_version": QUERY_PACK_BINDING_SCHEMA,
            "protocol_digest": protocol_digest,
            "frame_commitment": frame_commitment,
            "selection_projection_digest": sha256_digest(validated_selection),
            "selection_attempt": 0,
            "declared_selection_completed_at_utc": selection_text,
            "declared_query_pack_frozen_at_utc": frozen_text,
            "capture_id_key_id": capture_id_key_id,
            "commitment_key_id": query_pack_key_id,
            "query_capture_projection_digest": sha256_digest(capture),
            "query_count": capture["query_count"],
            "capability_boundary": _no_authority_boundary(
                "private_query_pack_binding_only"
            ),
        }
    )


def _validate_query_binding_shape(value: Any) -> dict[str, Any]:
    binding = _exact_dict(value, _QUERY_BINDING_KEYS, "query_pack_binding")
    if _exact_string(
        binding["schema_version"], "query_pack_binding_schema_version"
    ) != QUERY_PACK_BINDING_SCHEMA:
        raise HoldoutPackError("query_pack_binding_schema_mismatch")
    _regex_string(
        binding["protocol_digest"], _FULL_SHA256, "query_pack_protocol_digest"
    )
    _regex_string(
        binding["frame_commitment"],
        _HMAC_SHA256,
        "query_pack_frame_commitment",
    )
    _regex_string(
        binding["selection_projection_digest"],
        _FULL_SHA256,
        "query_pack_selection_projection_digest",
    )
    _exact_int(binding["selection_attempt"], 0, "query_pack_selection_attempt")
    _, selection_at = _canonical_utc(
        binding["declared_selection_completed_at_utc"],
        "query_pack_declared_selection_completed_at_utc",
    )
    _, query_frozen_at = _canonical_utc(
        binding["declared_query_pack_frozen_at_utc"],
        "query_pack_declared_query_pack_frozen_at_utc",
    )
    if query_frozen_at < selection_at:
        raise HoldoutPackError("query_pack_chronology_invalid")
    capture_key_id = _regex_string(
        binding["capture_id_key_id"], _KEY_ID, "query_pack_capture_id_key_id"
    )
    commitment_key_id = _regex_string(
        binding["commitment_key_id"],
        _KEY_ID,
        "query_pack_commitment_key_id",
    )
    if capture_key_id == commitment_key_id:
        raise HoldoutPackError("query_pack_key_ids_must_be_distinct")
    _regex_string(
        binding["query_capture_projection_digest"],
        _FULL_SHA256,
        "query_capture_projection_digest",
    )
    expected_count = (
        protocol_contract.POSITIVE_CLUSTER_COUNT
        + protocol_contract.OOD_CLUSTER_COUNT
    )
    _exact_int(binding["query_count"], expected_count, "query_pack_query_count")
    _validate_capability_boundary(
        binding["capability_boundary"],
        artifact_class="private_query_pack_binding_only",
    )
    return _canonical_clone(binding, "query_pack_binding")


def validate_query_pack_binding(
    value: Any,
    *,
    query_capture_projection: Any,
    frame: Any,
    protocol: Any,
    preregistration_state: Any,
    selection_projection: Any,
    selection_seed: bytes,
    frame_commitment_key: bytes,
    expected_frame_commitment: str,
    capture_id_key: bytes,
) -> dict[str, Any]:
    """Validate the private binding by exact root/capture recomputation."""

    observed = _validate_query_binding_shape(value)
    expected = build_query_pack_binding(
        query_capture_projection,
        frame=frame,
        protocol=protocol,
        preregistration_state=preregistration_state,
        selection_projection=selection_projection,
        selection_seed=selection_seed,
        frame_commitment_key=frame_commitment_key,
        expected_frame_commitment=expected_frame_commitment,
        capture_id_key=capture_id_key,
        capture_id_key_id=observed["capture_id_key_id"],
        query_pack_key_id=observed["commitment_key_id"],
        declared_selection_completed_at_utc=observed[
            "declared_selection_completed_at_utc"
        ],
        declared_query_pack_frozen_at_utc=observed[
            "declared_query_pack_frozen_at_utc"
        ],
    )
    if not hmac.compare_digest(
        canonical_json_bytes(observed), canonical_json_bytes(expected)
    ):
        raise HoldoutPackError("query_pack_binding_recomputation_mismatch")
    return observed


def _create_commitment(
    domain: str,
    *,
    key: bytes,
    protocol_digest: str,
    payload: Any,
) -> str:
    try:
        return protocol_contract.create_hmac_commitment(
            domain,
            key=key,
            protocol_digest=protocol_digest,
            payload=payload,
        )
    except protocol_contract.HoldoutProtocolError as exc:
        raise HoldoutPackError(f"{domain}_commitment_invalid") from exc


def create_query_pack_commitment(
    query_capture_projection: Any,
    *,
    query_pack_binding: Any,
    protocol: Any,
    query_pack_key: bytes,
) -> str:
    """HMAC the exact capture payload and its private binding envelope."""

    capture = _validate_query_capture_shape(query_capture_projection)
    binding = _validate_query_binding_shape(query_pack_binding)
    validated_protocol = _validated_protocol(protocol)
    protocol_digest = protocol_contract.preregistration_digest(
        validated_protocol
    )
    if binding["protocol_digest"] != protocol_digest:
        raise HoldoutPackError("query_pack_protocol_digest_mismatch")
    if binding["query_count"] != capture["query_count"]:
        raise HoldoutPackError("query_pack_query_count_mismatch")
    if binding["query_capture_projection_digest"] != sha256_digest(capture):
        raise HoldoutPackError("query_capture_projection_digest_mismatch")
    return _create_commitment(
        "query_pack",
        key=_exact_key(query_pack_key, "query_pack_key"),
        protocol_digest=protocol_digest,
        payload={
            "query_capture_projection": capture,
            "query_pack_binding": binding,
        },
    )


def _validate_raw_label_row(value: Any, *, label: str) -> dict[str, Any]:
    row = _exact_dict(value, _RAW_LABEL_KEYS, label)
    capture_id = _regex_string(
        row["capture_id"], _CAPTURE_ID, f"{label}_capture_id"
    )
    disposition = _exact_string(row["disposition"], f"{label}_disposition")
    if disposition not in {"positive", "ood", "invalid_or_ambiguous"}:
        raise HoldoutPackError(f"{label}_disposition_invalid")
    solver_id = row["expected_solver_id"]
    cell_id = row["expected_cell_id"]
    ood_class = row["ood_class"]
    if disposition == "positive":
        solver_id = _regex_string(
            solver_id, _SOLVER_ID, f"{label}_expected_solver_id"
        )
        cell_id = _exact_string(cell_id, f"{label}_expected_cell_id")
        if cell_id not in ALL_CELLS:
            raise HoldoutPackError(f"{label}_expected_cell_id_invalid")
        if ood_class is not None:
            raise HoldoutPackError(f"{label}_positive_ood_class_must_be_null")
    elif disposition == "ood":
        if solver_id is not None or cell_id is not None:
            raise HoldoutPackError(f"{label}_ood_target_must_be_null")
        ood_class = _exact_string(ood_class, f"{label}_ood_class")
        if ood_class not in protocol_contract.OOD_STRATA:
            raise HoldoutPackError(f"{label}_ood_class_invalid")
    elif solver_id is not None or cell_id is not None or ood_class is not None:
        raise HoldoutPackError(f"{label}_invalid_targets_must_be_null")
    return {
        "capture_id": capture_id,
        "disposition": disposition,
        "expected_solver_id": solver_id,
        "expected_cell_id": cell_id,
        "ood_class": ood_class,
    }


def _validate_raw_pack_shape(
    value: Any,
    *,
    query_capture: dict[str, Any],
    query_binding: dict[str, Any],
    query_pack_commitment: str,
) -> dict[str, Any]:
    pack = _exact_dict(value, _RAW_PACK_KEYS, "raw_label_pack")
    if _exact_string(
        pack["schema_version"], "raw_label_pack_schema_version"
    ) != RAW_LABEL_PACK_SCHEMA:
        raise HoldoutPackError("raw_label_pack_schema_mismatch")
    expected_strings = {
        "protocol_digest": query_binding["protocol_digest"],
        "frame_commitment": query_binding["frame_commitment"],
        "selection_projection_digest": query_binding[
            "selection_projection_digest"
        ],
        "query_pack_commitment": query_pack_commitment,
    }
    patterns = {
        "protocol_digest": _FULL_SHA256,
        "frame_commitment": _HMAC_SHA256,
        "selection_projection_digest": _FULL_SHA256,
        "query_pack_commitment": _HMAC_SHA256,
    }
    for key, expected in expected_strings.items():
        if _regex_string(
            pack[key], patterns[key], f"raw_label_pack_{key}"
        ) != expected:
            raise HoldoutPackError(f"raw_label_pack_{key}_mismatch")
    _regex_string(
        pack["adjudicator_actor_id"],
        _ACTOR_ID,
        "raw_label_pack_adjudicator_actor_id",
    )
    _regex_string(
        pack["commitment_key_id"],
        _KEY_ID,
        "raw_label_pack_commitment_key_id",
    )
    _, started_at = _canonical_utc(
        pack["declared_adjudication_started_at_utc"],
        "raw_label_pack_declared_adjudication_started_at_utc",
    )
    _, sealed_at = _canonical_utc(
        pack["declared_raw_pack_sealed_at_utc"],
        "raw_label_pack_declared_raw_pack_sealed_at_utc",
    )
    _, query_frozen_at = _canonical_utc(
        query_binding["declared_query_pack_frozen_at_utc"],
        "query_pack_declared_query_pack_frozen_at_utc",
    )
    if started_at < query_frozen_at or sealed_at < started_at:
        raise HoldoutPackError("raw_label_pack_chronology_invalid")
    attestations = _exact_dict(
        pack["attestations"],
        _RAW_ATTESTATION_KEYS,
        "raw_label_pack_attestations",
    )
    for key in (
        "query_pack_only_case_input",
        "candidate_scores_unseen",
        "peer_raw_label_plaintext_unseen_before_own_seal",
        "design_cell_metadata_unseen",
    ):
        _exact_bool(attestations[key], True, f"raw_label_pack_{key}")
    for key in (
        "reconciliation_performed",
        "post_selection_replacement_performed",
    ):
        _exact_bool(attestations[key], False, f"raw_label_pack_{key}")
    expected_count = query_capture["query_count"]
    _exact_int(pack["label_count"], expected_count, "raw_label_pack_label_count")
    labels_raw = _exact_list(pack["labels"], "raw_label_pack_labels")
    if len(labels_raw) != expected_count:
        raise HoldoutPackError("raw_label_pack_label_count_mismatch")
    labels = [
        _validate_raw_label_row(raw, label=f"raw_label_pack_labels_{index}")
        for index, raw in enumerate(labels_raw)
    ]
    query_order = [row["capture_id"] for row in query_capture["queries"]]
    by_capture: dict[str, dict[str, Any]] = {}
    for label in labels:
        capture_id = label["capture_id"]
        if capture_id in by_capture:
            raise HoldoutPackError("raw_label_pack_capture_ids_must_be_unique")
        by_capture[capture_id] = label
    if set(by_capture) != set(query_order):
        raise HoldoutPackError("raw_label_pack_capture_id_set_mismatch")
    normalized = dict(pack)
    normalized["attestations"] = dict(attestations)
    normalized["labels"] = [by_capture[capture_id] for capture_id in query_order]
    _validate_capability_boundary(
        pack["capability_boundary"],
        artifact_class="raw_adjudication_declaration_only",
    )
    return _canonical_clone(normalized, "raw_label_pack")


def create_raw_label_pack_commitment(
    raw_label_pack: Any,
    *,
    query_capture_projection: Any,
    query_pack_binding: Any,
    protocol: Any,
    query_pack_key: bytes,
    raw_label_pack_key: bytes,
) -> str:
    """Commit one independently authored raw pack before peer comparison."""

    query_capture = _validate_query_capture_shape(query_capture_projection)
    query_binding = _validate_query_binding_shape(query_pack_binding)
    validated_protocol = _validated_protocol(protocol)
    protocol_digest = protocol_contract.preregistration_digest(
        validated_protocol
    )
    if query_binding["protocol_digest"] != protocol_digest:
        raise HoldoutPackError("query_pack_protocol_digest_mismatch")
    query_commitment = create_query_pack_commitment(
        query_capture,
        query_pack_binding=query_binding,
        protocol=validated_protocol,
        query_pack_key=query_pack_key,
    )
    pack = _validate_raw_pack_shape(
        raw_label_pack,
        query_capture=query_capture,
        query_binding=query_binding,
        query_pack_commitment=query_commitment,
    )
    return _create_commitment(
        "adjudication_receipt",
        key=_exact_key(raw_label_pack_key, "raw_label_pack_key"),
        protocol_digest=protocol_digest,
        payload=pack,
    )


def _expected_label_for_case(case: dict[str, Any]) -> tuple[Any, ...]:
    if case["design_case_class"] == "positive":
        return (
            "positive",
            case["design_solver_id"],
            case["design_cell_id"],
            None,
        )
    return ("ood", None, None, case["stratum"])


def _label_semantics(label: dict[str, Any]) -> tuple[Any, ...]:
    return (
        label["disposition"],
        label["expected_solver_id"],
        label["expected_cell_id"],
        label["ood_class"],
    )


def _comparison_projection(
    packs: Sequence[dict[str, Any]],
    *,
    validated_frame: dict[str, Any],
    validated_selection: dict[str, Any],
    query_capture: dict[str, Any],
    query_binding: dict[str, Any],
    query_pack_commitment: str,
    raw_bindings: list[dict[str, str]],
    adjudication_key_id: str,
    declared_agreement_checked_at_utc: str,
) -> dict[str, Any]:
    checked_text, checked_at = _canonical_utc(
        declared_agreement_checked_at_utc,
        "declared_agreement_checked_at_utc",
    )
    raw_seal_times = [
        _canonical_utc(
            pack["declared_raw_pack_sealed_at_utc"],
            "raw_label_pack_declared_raw_pack_sealed_at_utc",
        )[1]
        for pack in packs
    ]
    if any(checked_at < sealed_at for sealed_at in raw_seal_times):
        raise HoldoutPackError("agreement_check_must_follow_both_raw_seals")
    query_ids = [row["capture_id"] for row in query_capture["queries"]]
    labels_by_pack = [
        {row["capture_id"]: row for row in pack["labels"]} for pack in packs
    ]
    case_by_id = {
        case["opaque_case_id"]: case
        for case in [
            *validated_frame["positive_cases"],
            *validated_frame["ood_cases"],
        ]
    }
    selected_cases = {
        case_by_id[case_id]["query"]: case_by_id[case_id]
        for case_id in validated_selection["selected_case_ids"]
    }
    equal = 0
    disagreements = 0
    invalid = 0
    design_mismatches = 0
    for query_row in query_capture["queries"]:
        capture_id = query_row["capture_id"]
        case = selected_cases[query_row["query"]]
        left = _label_semantics(labels_by_pack[0][capture_id])
        right = _label_semantics(labels_by_pack[1][capture_id])
        if left == right:
            equal += 1
        else:
            disagreements += 1
        if "invalid_or_ambiguous" in {left[0], right[0]}:
            invalid += 1
        if left != _expected_label_for_case(case) or right != (
            _expected_label_for_case(case)
        ):
            design_mismatches += 1
    total = len(query_ids)
    exact_agreement = (
        equal == total
        and disagreements == 0
        and invalid == 0
        and design_mismatches == 0
    )
    return {
        "schema_version": ADJUDICATION_COMPARISON_SCHEMA,
        "protocol_digest": query_binding["protocol_digest"],
        "frame_commitment": query_binding["frame_commitment"],
        "selection_projection_digest": query_binding[
            "selection_projection_digest"
        ],
        "query_pack_commitment": query_pack_commitment,
        "raw_label_bindings": raw_bindings,
        "adjudication_key_id": adjudication_key_id,
        "compared_case_count": total,
        "equal_label_count": equal,
        "disagreement_count": disagreements,
        "invalid_or_ambiguous_count": invalid,
        "design_target_mismatch_count": design_mismatches,
        "exact_agreement": exact_agreement,
        "reconciliation_performed": False,
        "replacement_performed": False,
        "declared_agreement_checked_at_utc": checked_text,
    }


def _validate_raw_binding(value: Any, label: str) -> dict[str, str]:
    binding = _exact_dict(value, _RAW_BINDING_KEYS, label)
    return {
        "adjudicator_actor_id": _regex_string(
            binding["adjudicator_actor_id"],
            _ACTOR_ID,
            f"{label}_adjudicator_actor_id",
        ),
        "key_id": _regex_string(binding["key_id"], _KEY_ID, f"{label}_key_id"),
        "hmac_commitment": _regex_string(
            binding["hmac_commitment"],
            _HMAC_SHA256,
            f"{label}_hmac_commitment",
        ),
    }


def _validate_comparison_shape(value: Any) -> dict[str, Any]:
    comparison = _exact_dict(value, _COMPARISON_KEYS, "comparison_projection")
    if _exact_string(
        comparison["schema_version"], "comparison_schema_version"
    ) != ADJUDICATION_COMPARISON_SCHEMA:
        raise HoldoutPackError("comparison_schema_mismatch")
    _regex_string(
        comparison["protocol_digest"],
        _FULL_SHA256,
        "comparison_protocol_digest",
    )
    _regex_string(
        comparison["frame_commitment"],
        _HMAC_SHA256,
        "comparison_frame_commitment",
    )
    _regex_string(
        comparison["selection_projection_digest"],
        _FULL_SHA256,
        "comparison_selection_projection_digest",
    )
    _regex_string(
        comparison["query_pack_commitment"],
        _HMAC_SHA256,
        "comparison_query_pack_commitment",
    )
    bindings_raw = _exact_list(
        comparison["raw_label_bindings"], "comparison_raw_label_bindings"
    )
    if len(bindings_raw) != 2:
        raise HoldoutPackError("comparison_requires_two_raw_bindings")
    bindings = [
        _validate_raw_binding(raw, f"comparison_raw_binding_{index}")
        for index, raw in enumerate(bindings_raw)
    ]
    if bindings != sorted(
        bindings, key=lambda binding: binding["adjudicator_actor_id"]
    ):
        raise HoldoutPackError("comparison_raw_bindings_not_canonical")
    if (
        len({binding["adjudicator_actor_id"] for binding in bindings}) != 2
        or len({binding["key_id"] for binding in bindings}) != 2
        or len({binding["hmac_commitment"] for binding in bindings}) != 2
    ):
        raise HoldoutPackError("comparison_raw_bindings_must_be_distinct")
    adjudication_key_id = _regex_string(
        comparison["adjudication_key_id"],
        _KEY_ID,
        "comparison_adjudication_key_id",
    )
    if adjudication_key_id in {binding["key_id"] for binding in bindings}:
        raise HoldoutPackError("comparison_key_ids_must_be_distinct")
    expected_count = (
        protocol_contract.POSITIVE_CLUSTER_COUNT
        + protocol_contract.OOD_CLUSTER_COUNT
    )
    expected_counts = {
        "compared_case_count": expected_count,
        "equal_label_count": expected_count,
        "disagreement_count": 0,
        "invalid_or_ambiguous_count": 0,
        "design_target_mismatch_count": 0,
    }
    for key, expected in expected_counts.items():
        _exact_int(comparison[key], expected, f"comparison_{key}")
    _exact_bool(comparison["exact_agreement"], True, "comparison_exact_agreement")
    _exact_bool(
        comparison["reconciliation_performed"],
        False,
        "comparison_reconciliation_performed",
    )
    _exact_bool(
        comparison["replacement_performed"],
        False,
        "comparison_replacement_performed",
    )
    _canonical_utc(
        comparison["declared_agreement_checked_at_utc"],
        "comparison_declared_agreement_checked_at_utc",
    )
    normalized = dict(comparison)
    normalized["raw_label_bindings"] = bindings
    return _canonical_clone(normalized, "comparison_projection")


def _build_consensus_label_pack(
    *,
    query_binding: dict[str, Any],
    query_pack_commitment: str,
    raw_bindings: list[dict[str, str]],
    adjudication_commitment: str,
    label_pack_key_id: str,
    declared_pack_sealed_at_utc: str,
    labels: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    consensus_labels = [
        {key: label[key] for key in _RAW_LABEL_KEYS} for label in labels
    ]
    payload = _exact_dict(
        {
            "schema_version": CONSENSUS_LABEL_PACK_SCHEMA,
            "protocol_digest": query_binding["protocol_digest"],
            "frame_commitment": query_binding["frame_commitment"],
            "selection_projection_digest": query_binding[
                "selection_projection_digest"
            ],
            "query_pack_commitment": query_pack_commitment,
            "raw_label_bindings": raw_bindings,
            "adjudication_commitment": adjudication_commitment,
            "label_pack_key_id": label_pack_key_id,
            "declared_pack_sealed_at_utc": declared_pack_sealed_at_utc,
            "case_count": len(consensus_labels),
            "labels": consensus_labels,
        },
        _CONSENSUS_LABEL_PACK_KEYS,
        "consensus_label_pack",
    )
    return _canonical_clone(payload, "consensus_label_pack")


def build_pack_seal_projection(
    frame: Any,
    *,
    protocol: Any,
    preregistration_state: Any,
    selection_projection: Any,
    query_capture_projection: Any,
    query_pack_binding: Any,
    raw_label_packs: Any,
    raw_label_pack_keys: Any,
    selection_seed: bytes,
    frame_commitment_key: bytes,
    expected_frame_commitment: str,
    capture_id_key: bytes,
    query_pack_key: bytes,
    adjudication_key: bytes,
    adjudication_key_id: str,
    label_pack_key: bytes,
    label_pack_key_id: str,
    declared_agreement_checked_at_utc: str,
    declared_pack_sealed_at_utc: str,
) -> dict[str, Any]:
    """Seal only exact agreeing raw packs; return metadata, never labels."""

    query_capture = validate_query_capture_projection(
        query_capture_projection,
        frame=frame,
        protocol=protocol,
        preregistration_state=preregistration_state,
        selection_projection=selection_projection,
        selection_seed=selection_seed,
        frame_commitment_key=frame_commitment_key,
        expected_frame_commitment=expected_frame_commitment,
        capture_id_key=capture_id_key,
    )
    query_binding = validate_query_pack_binding(
        query_pack_binding,
        query_capture_projection=query_capture,
        frame=frame,
        protocol=protocol,
        preregistration_state=preregistration_state,
        selection_projection=selection_projection,
        selection_seed=selection_seed,
        frame_commitment_key=frame_commitment_key,
        expected_frame_commitment=expected_frame_commitment,
        capture_id_key=capture_id_key,
    )
    validated_protocol = _validated_protocol(protocol)
    protocol_digest = protocol_contract.preregistration_digest(
        validated_protocol
    )
    query_key = _exact_key(query_pack_key, "query_pack_key")
    adjudication_key_value = _exact_key(
        adjudication_key, "adjudication_key"
    )
    label_key = _exact_key(label_pack_key, "label_pack_key")
    query_commitment = create_query_pack_commitment(
        query_capture,
        query_pack_binding=query_binding,
        protocol=validated_protocol,
        query_pack_key=query_key,
    )
    packs_raw = _exact_list(raw_label_packs, "raw_label_packs")
    if len(packs_raw) != 2:
        raise HoldoutPackError("exactly_two_raw_label_packs_required")
    packs = [
        _validate_raw_pack_shape(
            raw,
            query_capture=query_capture,
            query_binding=query_binding,
            query_pack_commitment=query_commitment,
        )
        for raw in packs_raw
    ]
    packs.sort(key=lambda pack: pack["adjudicator_actor_id"])
    expected_adjudicators = sorted(
        _validated_frame(
            frame,
            protocol=validated_protocol,
            preregistration_state=preregistration_state,
        )["roles"]["adjudicator_actor_ids"]
    )
    if [pack["adjudicator_actor_id"] for pack in packs] != expected_adjudicators:
        raise HoldoutPackError("raw_label_pack_adjudicator_roster_mismatch")
    raw_key_ids = {pack["commitment_key_id"] for pack in packs}
    if len(raw_key_ids) != 2:
        raise HoldoutPackError("raw_label_pack_key_ids_must_be_distinct")
    raw_keys = _validate_key_map(raw_label_pack_keys, raw_key_ids)
    capture_key = _exact_key(capture_id_key, "capture_id_key")
    selection_key = _exact_key(selection_seed, "selection_seed")
    frame_key = _exact_key(frame_commitment_key, "frame_commitment_key")
    _validate_distinct_key_material(
        [
            ("selection_seed", selection_key),
            ("frame_commitment_key", frame_key),
            ("capture_id_key", capture_key),
            ("query_pack_key", query_key),
            ("adjudication_key", adjudication_key_value),
            ("label_pack_key", label_key),
            *[(key_id, raw_keys[key_id]) for key_id in sorted(raw_keys)],
        ]
    )
    _validate_distinct_key_ids(
        [
            ("capture_id_key_id", query_binding["capture_id_key_id"]),
            ("query_pack_key_id", query_binding["commitment_key_id"]),
            ("adjudication_key_id", adjudication_key_id),
            ("label_pack_key_id", label_pack_key_id),
            *[("raw_label_pack_key_id", key_id) for key_id in sorted(raw_key_ids)],
        ]
    )
    raw_bindings = [
        {
            "adjudicator_actor_id": pack["adjudicator_actor_id"],
            "key_id": pack["commitment_key_id"],
            "hmac_commitment": create_raw_label_pack_commitment(
                pack,
                query_capture_projection=query_capture,
                query_pack_binding=query_binding,
                protocol=validated_protocol,
                query_pack_key=query_key,
                raw_label_pack_key=raw_keys[pack["commitment_key_id"]],
            ),
        }
        for pack in packs
    ]
    (
        _,
        validated_frame,
        validated_selection,
        _,
        _,
    ) = _bound_inputs(
        frame,
        protocol=validated_protocol,
        preregistration_state=preregistration_state,
        selection_projection=selection_projection,
        selection_seed=selection_key,
        frame_commitment_key=frame_key,
        expected_frame_commitment=expected_frame_commitment,
    )
    comparison = _comparison_projection(
        packs,
        validated_frame=validated_frame,
        validated_selection=validated_selection,
        query_capture=query_capture,
        query_binding=query_binding,
        query_pack_commitment=query_commitment,
        raw_bindings=raw_bindings,
        adjudication_key_id=adjudication_key_id,
        declared_agreement_checked_at_utc=(
            declared_agreement_checked_at_utc
        ),
    )
    if not comparison["exact_agreement"]:
        if comparison["invalid_or_ambiguous_count"]:
            raise HoldoutPackError("invalid_or_ambiguous_adjudication_is_terminal")
        if comparison["disagreement_count"]:
            raise HoldoutPackError("adjudication_disagreement_is_terminal")
        raise HoldoutPackError("adjudication_design_mismatch_is_terminal")
    comparison = _validate_comparison_shape(comparison)
    adjudication_commitment = _create_commitment(
        "adjudication_receipt",
        key=adjudication_key_value,
        protocol_digest=protocol_digest,
        payload=comparison,
    )
    agreement_text, agreement_at = _canonical_utc(
        declared_agreement_checked_at_utc,
        "declared_agreement_checked_at_utc",
    )
    seal_text, seal_at = _canonical_utc(
        declared_pack_sealed_at_utc, "declared_pack_sealed_at_utc"
    )
    if seal_at < agreement_at:
        raise HoldoutPackError("pack_seal_must_not_precede_agreement_check")
    consensus_label_pack = _build_consensus_label_pack(
        query_binding=query_binding,
        query_pack_commitment=query_commitment,
        raw_bindings=raw_bindings,
        adjudication_commitment=adjudication_commitment,
        label_pack_key_id=label_pack_key_id,
        declared_pack_sealed_at_utc=seal_text,
        labels=packs[0]["labels"],
    )
    label_commitment = _create_commitment(
        "label_pack",
        key=label_key,
        protocol_digest=protocol_digest,
        payload=consensus_label_pack,
    )
    return _validate_pack_seal_shape(
        {
            "schema_version": PACK_SEAL_SCHEMA,
            "protocol_digest": protocol_digest,
            "frame_commitment": query_binding["frame_commitment"],
            "selection_projection_digest": query_binding[
                "selection_projection_digest"
            ],
            "selection_attempt": 0,
            "query_pack_key_id": query_binding["commitment_key_id"],
            "query_pack_commitment": query_commitment,
            "raw_label_bindings": raw_bindings,
            "adjudication_key_id": adjudication_key_id,
            "adjudication_commitment": adjudication_commitment,
            "label_pack_key_id": label_pack_key_id,
            "label_pack_commitment": label_commitment,
            "declared_agreement_checked_at_utc": agreement_text,
            "declared_pack_sealed_at_utc": seal_text,
            "capture_not_started_attestation": True,
            "capability_boundary": _no_authority_boundary(
                "pack_seal_projection_only"
            ),
        },
        protocol=validated_protocol,
    )


def _validate_pack_seal_shape(value: Any, *, protocol: Any) -> dict[str, Any]:
    validated_protocol = _validated_protocol(protocol)
    protocol_digest = protocol_contract.preregistration_digest(
        validated_protocol
    )
    projection = _exact_dict(value, _PACK_SEAL_KEYS, "pack_seal_projection")
    if _exact_string(
        projection["schema_version"], "pack_seal_schema_version"
    ) != PACK_SEAL_SCHEMA:
        raise HoldoutPackError("pack_seal_schema_mismatch")
    if _regex_string(
        projection["protocol_digest"],
        _FULL_SHA256,
        "pack_seal_protocol_digest",
    ) != protocol_digest:
        raise HoldoutPackError("pack_seal_protocol_digest_mismatch")
    _regex_string(
        projection["frame_commitment"],
        _HMAC_SHA256,
        "pack_seal_frame_commitment",
    )
    _regex_string(
        projection["selection_projection_digest"],
        _FULL_SHA256,
        "pack_seal_selection_projection_digest",
    )
    _exact_int(
        projection["selection_attempt"], 0, "pack_seal_selection_attempt"
    )
    query_key_id = _regex_string(
        projection["query_pack_key_id"],
        _KEY_ID,
        "pack_seal_query_pack_key_id",
    )
    _regex_string(
        projection["query_pack_commitment"],
        _HMAC_SHA256,
        "pack_seal_query_pack_commitment",
    )
    raw_bindings_value = _exact_list(
        projection["raw_label_bindings"], "pack_seal_raw_label_bindings"
    )
    if len(raw_bindings_value) != 2:
        raise HoldoutPackError("pack_seal_requires_two_raw_bindings")
    raw_bindings = [
        _validate_raw_binding(raw, f"pack_seal_raw_binding_{index}")
        for index, raw in enumerate(raw_bindings_value)
    ]
    if raw_bindings != sorted(
        raw_bindings, key=lambda binding: binding["adjudicator_actor_id"]
    ):
        raise HoldoutPackError("pack_seal_raw_bindings_not_canonical")
    if (
        len({binding["adjudicator_actor_id"] for binding in raw_bindings}) != 2
        or len({binding["hmac_commitment"] for binding in raw_bindings}) != 2
    ):
        raise HoldoutPackError("pack_seal_raw_bindings_must_be_distinct")
    adjudication_key_id = _regex_string(
        projection["adjudication_key_id"],
        _KEY_ID,
        "pack_seal_adjudication_key_id",
    )
    if len(
        {
            query_key_id,
            adjudication_key_id,
            *(binding["key_id"] for binding in raw_bindings),
        }
    ) != 4:
        raise HoldoutPackError("pack_seal_key_ids_must_be_distinct")
    _regex_string(
        projection["adjudication_commitment"],
        _HMAC_SHA256,
        "pack_seal_adjudication_commitment",
    )
    label_pack_key_id = _regex_string(
        projection["label_pack_key_id"],
        _KEY_ID,
        "pack_seal_label_pack_key_id",
    )
    _regex_string(
        projection["label_pack_commitment"],
        _HMAC_SHA256,
        "pack_seal_label_pack_commitment",
    )
    if label_pack_key_id in {
        query_key_id,
        adjudication_key_id,
        *(binding["key_id"] for binding in raw_bindings),
    }:
        raise HoldoutPackError("pack_seal_key_ids_must_be_distinct")
    _, agreement_at = _canonical_utc(
        projection["declared_agreement_checked_at_utc"],
        "pack_seal_declared_agreement_checked_at_utc",
    )
    _, seal_at = _canonical_utc(
        projection["declared_pack_sealed_at_utc"],
        "pack_seal_declared_pack_sealed_at_utc",
    )
    if seal_at < agreement_at:
        raise HoldoutPackError("pack_seal_chronology_invalid")
    _exact_bool(
        projection["capture_not_started_attestation"],
        True,
        "pack_seal_capture_not_started_attestation",
    )
    _validate_capability_boundary(
        projection["capability_boundary"],
        artifact_class="pack_seal_projection_only",
    )
    normalized = dict(projection)
    normalized["raw_label_bindings"] = raw_bindings
    return _canonical_clone(normalized, "pack_seal_projection")


def validate_pack_seal_projection(
    value: Any,
    *,
    frame: Any,
    protocol: Any,
    preregistration_state: Any,
    selection_projection: Any,
    query_capture_projection: Any,
    query_pack_binding: Any,
    raw_label_packs: Any,
    raw_label_pack_keys: Any,
    selection_seed: bytes,
    frame_commitment_key: bytes,
    expected_frame_commitment: str,
    capture_id_key: bytes,
    query_pack_key: bytes,
    adjudication_key: bytes,
    label_pack_key: bytes,
) -> dict[str, Any]:
    """Validate a seal projection by exact private-input recomputation."""

    observed = _validate_pack_seal_shape(value, protocol=protocol)
    expected = build_pack_seal_projection(
        frame,
        protocol=protocol,
        preregistration_state=preregistration_state,
        selection_projection=selection_projection,
        query_capture_projection=query_capture_projection,
        query_pack_binding=query_pack_binding,
        raw_label_packs=raw_label_packs,
        raw_label_pack_keys=raw_label_pack_keys,
        selection_seed=selection_seed,
        frame_commitment_key=frame_commitment_key,
        expected_frame_commitment=expected_frame_commitment,
        capture_id_key=capture_id_key,
        query_pack_key=query_pack_key,
        adjudication_key=adjudication_key,
        adjudication_key_id=observed["adjudication_key_id"],
        label_pack_key=label_pack_key,
        label_pack_key_id=observed["label_pack_key_id"],
        declared_agreement_checked_at_utc=observed[
            "declared_agreement_checked_at_utc"
        ],
        declared_pack_sealed_at_utc=observed[
            "declared_pack_sealed_at_utc"
        ],
    )
    if not hmac.compare_digest(
        canonical_json_bytes(observed), canonical_json_bytes(expected)
    ):
        raise HoldoutPackError("pack_seal_projection_recomputation_mismatch")
    return observed


__all__ = [
    "ADJUDICATION_COMPARISON_SCHEMA",
    "CONSENSUS_LABEL_PACK_SCHEMA",
    "HoldoutPackError",
    "PACK_SEAL_SCHEMA",
    "QUERY_CAPTURE_SCHEMA",
    "QUERY_PACK_BINDING_SCHEMA",
    "RAW_LABEL_PACK_SCHEMA",
    "build_pack_seal_projection",
    "build_query_capture_projection",
    "build_query_pack_binding",
    "create_raw_label_pack_commitment",
    "create_query_pack_commitment",
    "validate_pack_seal_projection",
    "validate_query_capture_projection",
    "validate_query_pack_binding",
]
