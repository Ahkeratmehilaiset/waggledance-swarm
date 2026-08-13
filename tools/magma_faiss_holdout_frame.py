# SPDX-License-Identifier: BUSL-1.1
"""Pure external-frame validation and deterministic holdout selection.

This module handles plaintext only in caller memory.  It never reads or writes
files, opens Git or FAISS, embeds a query, reveals a label pack, or grants an
evidence/runtime/promotion decision.  A valid frame is bound to the frozen
preregistration, committed with a protocol-scoped HMAC, and sampled by a
deterministic stratified HMAC ranking after a 256-bit seed is supplied.

Publication order, seed independence, actor identity, candidate-population
authoring blindness, opaque-ID blinding, semantic de-duplication, key custody,
one-shot execution, and Git ancestry remain external adapter responsibilities.
All capability fields returned here therefore remain false.  Design-cell
metadata required for stratification is not an adjudicated disposition or
score label.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from tools import magma_faiss_holdout_protocol as protocol_contract
from waggledance.core.hex_cell_topology import ALL_CELLS
from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest


FRAME_SCHEMA = "wd.magma.faiss_holdout_frame.v1"
AUTHOR_BATCH_SCHEMA = "wd.magma.faiss_holdout_author_batch.v1"
SELECTION_SCHEMA = "wd.magma.faiss_holdout_selection_projection.v1"
SELECTION_ALGORITHM = (
    "hmac_sha256_rank_per_locked_stratum_then_hmac_mixed_order_v1"
)
SELECTION_CONTEXT = b"WD-MAGMA-FAISS-HOLDOUT-SELECTION-v1"
MAX_QUERY_UTF8_BYTES = 4096
MAX_FRAME_CASE_COUNT = 4096

_FULL_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_HMAC_SHA256 = re.compile(r"^hmac-sha256:[0-9a-f]{64}$")
_PROJECTION_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_SOLVER_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_OPAQUE_CASE_ID = re.compile(r"^case_[0-9a-f]{64}$")
_INTENT_CLUSTER_ID = re.compile(r"^intent_[0-9a-f]{64}$")
_ACTOR_ID = re.compile(r"^actor_[0-9a-f]{64}$")
_CANONICAL_UTC = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
)

_FRAME_KEYS = frozenset(
    {
        "schema_version",
        "protocol_digest",
        "preregistration_state_digest",
        "declared_preregistration_published_at_utc",
        "frame_frozen_at_utc",
        "development_exclusion_set_digest",
        "solver_identity_set_digest",
        "solver_identities",
        "roles",
        "attestations",
        "positive_cases",
        "ood_cases",
        "capability_boundary",
    }
)
_AUTHOR_BATCH_KEYS = frozenset(
    {
        "schema_version",
        "protocol_digest",
        "preregistration_state_digest",
        "declared_preregistration_published_at_utc",
        "solver_identity_set_digest",
        "case_class",
        "author_actor_id",
        "cases",
        "capability_boundary",
    }
)
_SOLVER_IDENTITY_KEYS = frozenset(
    {
        "canonical_solver_id",
        "cell_id",
        "projection_id",
        "projection_digest",
        "source_digest",
    }
)
_ROLE_KEYS = frozenset(
    {
        "candidate_owner_actor_id",
        "custodian_actor_id",
        "positive_author_actor_ids",
        "ood_author_actor_ids",
        "adjudicator_actor_ids",
    }
)
_ATTESTATION_KEYS = frozenset(
    {
        "case_authors_score_blind",
        "future_adjudicators_score_blind",
        "adjudication_occurs_after_selection",
        "adjudication_occurs_before_query_capture",
        "no_reseed",
        "no_post_selection_replacement",
        "semantic_intent_clustering_completed",
        "translation_template_variants_share_cluster",
        "opaque_case_ids_random_and_class_blind",
        "candidate_owner_excluded_from_case_content",
    }
)
_CASE_KEYS = frozenset(
    {
        "opaque_case_id",
        "intent_cluster_id",
        "query",
        "language",
        "stratum",
        "author_actor_id",
        "authored_at_utc",
        "semantic_family_digest",
        "design_case_class",
        "design_solver_id",
        "design_cell_id",
    }
)
_CAPABILITY_KEYS = frozenset(
    {
        "artifact_class",
        "external_provenance_verified",
        "actor_identity_verified",
        "trusted_time_verified",
        "semantic_uniqueness_externally_verified",
        "candidate_population_authoring_blindness_verified",
        "opaque_case_id_blinding_verified",
        "development_exclusion_verified",
        "frame_publication_verified",
        "seed_unbiased_verified",
        "seed_precommit_verified",
        "git_ancestry_verified",
        "one_shot_enforced",
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
_SELECTION_KEYS = frozenset(
    {
        "schema_version",
        "protocol_digest",
        "frame_commitment",
        "selection_seed_sha256",
        "selection_algorithm",
        "selection_attempt",
        "selected_case_ids",
        "selected_case_count",
        "positive_case_count",
        "ood_case_count",
        "positive_group_count",
        "ood_group_count",
        "capability_boundary",
    }
)


class HoldoutFrameError(ValueError):
    """An external frame or deterministic selection violates the contract."""


def _validated_protocol(value: Any) -> dict[str, Any]:
    try:
        return protocol_contract.validate_preregistration(value)
    except protocol_contract.HoldoutProtocolError as exc:
        raise HoldoutFrameError("preregistration_invalid") from exc


def _exact_dict(
    value: Any,
    expected_keys: frozenset[str],
    label: str,
) -> dict[str, Any]:
    if type(value) is not dict:
        raise HoldoutFrameError(f"{label}_must_be_exact_object")
    if any(type(key) is not str for key in value):
        raise HoldoutFrameError(f"{label}_keys_must_be_exact_strings")
    actual = frozenset(value)
    if actual != expected_keys:
        raise HoldoutFrameError(
            f"{label}_keys_mismatch:missing={sorted(expected_keys - actual)},"
            f"extra={sorted(actual - expected_keys)}"
        )
    return value


def _exact_list(value: Any, label: str) -> list[Any]:
    if type(value) is not list:
        raise HoldoutFrameError(f"{label}_must_be_exact_list")
    return value


def _exact_string(value: Any, label: str) -> str:
    if type(value) is not str or not value:
        raise HoldoutFrameError(f"{label}_must_be_nonempty_exact_string")
    return value


def _regex_string(value: Any, pattern: re.Pattern[str], label: str) -> str:
    result = _exact_string(value, label)
    if pattern.fullmatch(result) is None:
        raise HoldoutFrameError(f"{label}_invalid")
    return result


def _exact_bool(value: Any, expected: bool, label: str) -> None:
    if type(value) is not bool or value is not expected:
        raise HoldoutFrameError(f"{label}_must_be_{str(expected).lower()}")


def _exact_int(value: Any, expected: int, label: str) -> None:
    if type(value) is not int or value != expected:
        raise HoldoutFrameError(f"{label}_must_equal_{expected}")


def _canonical_utc(value: Any, label: str) -> tuple[str, datetime]:
    result = _regex_string(value, _CANONICAL_UTC, label)
    try:
        parsed = datetime.strptime(result, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise HoldoutFrameError(f"{label}_invalid") from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != result:
        raise HoldoutFrameError(f"{label}_not_canonical")
    return result, parsed


def _canonical_clone(
    value: Any,
    label: str,
    *,
    maximum_bytes: int | None = None,
) -> Any:
    try:
        canonical = canonical_json_bytes(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise HoldoutFrameError(f"{label}_is_not_strict_json") from exc
    if maximum_bytes is not None and len(canonical) > maximum_bytes:
        raise HoldoutFrameError(f"{label}_exceeds_commitment_payload_limit")
    return json.loads(canonical)


def _no_authority_boundary(artifact_class: str) -> dict[str, Any]:
    return {
        "artifact_class": artifact_class,
        "external_provenance_verified": False,
        "actor_identity_verified": False,
        "trusted_time_verified": False,
        "semantic_uniqueness_externally_verified": False,
        "candidate_population_authoring_blindness_verified": False,
        "opaque_case_id_blinding_verified": False,
        "development_exclusion_verified": False,
        "frame_publication_verified": False,
        "seed_unbiased_verified": False,
        "seed_precommit_verified": False,
        "git_ancestry_verified": False,
        "one_shot_enforced": False,
        "holdout_pack_created": False,
        "holdout_evidence_gate_met": False,
        "runtime_threshold_selected": False,
        "runtime_configuration_written": False,
        "production_runtime_path_evaluated": False,
        "runtime_authority_granted": False,
        "candidate_mode_change_authorized": False,
        "cell_pruning_authorized": False,
        "production_promotion_gate_pass": False,
    }


def _validate_capability_boundary(
    value: Any,
    *,
    artifact_class: str,
) -> None:
    boundary = _exact_dict(value, _CAPABILITY_KEYS, "capability_boundary")
    if _exact_string(
        boundary["artifact_class"], "capability_boundary_artifact_class"
    ) != artifact_class:
        raise HoldoutFrameError("capability_boundary_artifact_class_mismatch")
    for key in _CAPABILITY_KEYS - {"artifact_class"}:
        _exact_bool(boundary[key], False, f"capability_boundary_{key}")


def _validate_actor_list(
    value: Any,
    *,
    label: str,
    exact_count: int | None = None,
) -> list[str]:
    actors = _exact_list(value, label)
    if not actors:
        raise HoldoutFrameError(f"{label}_must_not_be_empty")
    if exact_count is not None and len(actors) != exact_count:
        raise HoldoutFrameError(f"{label}_count_mismatch")
    normalized = [
        _regex_string(actor, _ACTOR_ID, f"{label}_{index}")
        for index, actor in enumerate(actors)
    ]
    if len(set(normalized)) != len(normalized):
        raise HoldoutFrameError(f"{label}_must_be_unique")
    return sorted(normalized)


def _validated_solver_identities(value: Any) -> list[dict[str, str]]:
    rows = _exact_list(value, "solver_identities")
    if len(rows) != protocol_contract.FROZEN_SOLVER_COUNT:
        raise HoldoutFrameError("solver_identity_count_mismatch")
    validated: list[dict[str, str]] = []
    for index, raw in enumerate(rows):
        row = _exact_dict(
            raw,
            _SOLVER_IDENTITY_KEYS,
            f"solver_identities_{index}",
        )
        solver_id = _regex_string(
            row["canonical_solver_id"],
            _SOLVER_ID,
            f"solver_identities_{index}_canonical_solver_id",
        )
        cell_id = _exact_string(
            row["cell_id"], f"solver_identities_{index}_cell_id"
        )
        if cell_id not in ALL_CELLS:
            raise HoldoutFrameError("solver_identity_cell_id_invalid")
        projection_id = _regex_string(
            row["projection_id"],
            _PROJECTION_ID,
            f"solver_identities_{index}_projection_id",
        )
        projection_digest = _regex_string(
            row["projection_digest"],
            _FULL_SHA256,
            f"solver_identities_{index}_projection_digest",
        )
        source_digest = _regex_string(
            row["source_digest"],
            _FULL_SHA256,
            f"solver_identities_{index}_source_digest",
        )
        validated.append(
            {
                "canonical_solver_id": solver_id,
                "cell_id": cell_id,
                "projection_id": projection_id,
                "projection_digest": projection_digest,
                "source_digest": source_digest,
            }
        )
    solver_ids = [row["canonical_solver_id"] for row in validated]
    projection_ids = [row["projection_id"] for row in validated]
    if len(set(solver_ids)) != len(solver_ids):
        raise HoldoutFrameError("solver_identities_must_be_unique")
    if len(set(projection_ids)) != len(projection_ids):
        raise HoldoutFrameError("solver_projection_ids_must_be_unique")
    return sorted(validated, key=lambda row: row["canonical_solver_id"])


def solver_identity_set_digest(value: Any) -> str:
    """Digest the candidate's canonical sorted five-field identity list."""

    identities = _validated_solver_identities(value)
    return sha256_digest(identities)


def _validate_roles(value: Any) -> dict[str, Any]:
    roles = _exact_dict(value, _ROLE_KEYS, "roles")
    candidate_owner = _regex_string(
        roles["candidate_owner_actor_id"],
        _ACTOR_ID,
        "candidate_owner_actor_id",
    )
    custodian = _regex_string(
        roles["custodian_actor_id"], _ACTOR_ID, "custodian_actor_id"
    )
    positive_authors = _validate_actor_list(
        roles["positive_author_actor_ids"],
        label="positive_author_actor_ids",
    )
    ood_authors = _validate_actor_list(
        roles["ood_author_actor_ids"], label="ood_author_actor_ids"
    )
    adjudicators = _validate_actor_list(
        roles["adjudicator_actor_ids"],
        label="adjudicator_actor_ids",
        exact_count=2,
    )
    role_groups = [
        {candidate_owner},
        {custodian},
        set(positive_authors),
        set(ood_authors),
        set(adjudicators),
    ]
    for left_index, left in enumerate(role_groups):
        for right in role_groups[left_index + 1 :]:
            if left & right:
                raise HoldoutFrameError("holdout_actor_roles_must_be_disjoint")
    return {
        "candidate_owner_actor_id": candidate_owner,
        "custodian_actor_id": custodian,
        "positive_author_actor_ids": positive_authors,
        "ood_author_actor_ids": ood_authors,
        "adjudicator_actor_ids": adjudicators,
    }


def _validate_attestations(value: Any) -> dict[str, bool]:
    attestations = _exact_dict(value, _ATTESTATION_KEYS, "attestations")
    for key in _ATTESTATION_KEYS:
        _exact_bool(attestations[key], True, f"attestations_{key}")
    return {key: True for key in sorted(_ATTESTATION_KEYS)}


def _validate_query(value: Any, label: str) -> str:
    query = _exact_string(value, label)
    if (
        query != query.strip()
        or unicodedata.normalize("NFC", query) != query
        or " ".join(query.split()) != query
        or any(
            unicodedata.category(character) in {"Cc", "Cf", "Cs"}
            for character in query
        )
        or len(query.encode("utf-8")) > MAX_QUERY_UTF8_BYTES
    ):
        raise HoldoutFrameError(f"{label}_must_be_canonical_single_line_text")
    return query


def _query_dedup_key(query: str) -> str:
    normalized = " ".join(
        unicodedata.normalize("NFKC", query).casefold().split()
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _validate_case_common(
    raw: Any,
    *,
    label: str,
    allowed_authors: set[str],
    authored_after: datetime,
    frozen_at: datetime,
) -> dict[str, Any]:
    case = _exact_dict(raw, _CASE_KEYS, label)
    case_id = _regex_string(case["opaque_case_id"], _OPAQUE_CASE_ID, f"{label}_id")
    cluster_id = _regex_string(
        case["intent_cluster_id"],
        _INTENT_CLUSTER_ID,
        f"{label}_intent_cluster_id",
    )
    query = _validate_query(case["query"], f"{label}_query")
    language = _exact_string(case["language"], f"{label}_language")
    if language not in {"fi", "en"}:
        raise HoldoutFrameError(f"{label}_language_invalid")
    stratum = _exact_string(case["stratum"], f"{label}_stratum")
    author_id = _regex_string(
        case["author_actor_id"], _ACTOR_ID, f"{label}_author_actor_id"
    )
    if author_id not in allowed_authors:
        raise HoldoutFrameError(f"{label}_author_not_bound_to_expected_role")
    authored_text, authored_at = _canonical_utc(
        case["authored_at_utc"], f"{label}_authored_at_utc"
    )
    if authored_at <= authored_after:
        raise HoldoutFrameError(f"{label}_must_be_authored_after_preregistration")
    if authored_at > frozen_at:
        raise HoldoutFrameError(f"{label}_authored_after_frame_freeze")
    family_digest = _regex_string(
        case["semantic_family_digest"],
        _FULL_SHA256,
        f"{label}_semantic_family_digest",
    )
    case_class = _exact_string(
        case["design_case_class"], f"{label}_design_case_class"
    )
    if case_class not in {"positive", "ood"}:
        raise HoldoutFrameError(f"{label}_design_case_class_invalid")
    return {
        "opaque_case_id": case_id,
        "intent_cluster_id": cluster_id,
        "query": query,
        "language": language,
        "stratum": stratum,
        "author_actor_id": author_id,
        "authored_at_utc": authored_text,
        "semantic_family_digest": family_digest,
        "design_case_class": case_class,
        "design_solver_id": case["design_solver_id"],
        "design_cell_id": case["design_cell_id"],
    }


def _positive_case_target(
    case: dict[str, Any],
    *,
    label: str,
    solver_cells: dict[str, str],
) -> tuple[str, str]:
    if case["design_case_class"] != "positive":
        raise HoldoutFrameError(f"{label}_must_have_positive_design_class")
    solver_id = _regex_string(
        case["design_solver_id"], _SOLVER_ID, f"{label}_design_solver_id"
    )
    if solver_id not in solver_cells:
        raise HoldoutFrameError(f"{label}_solver_not_in_frozen_identity_set")
    cell_id = _exact_string(case["design_cell_id"], f"{label}_design_cell_id")
    if cell_id != solver_cells[solver_id]:
        raise HoldoutFrameError(f"{label}_cell_does_not_match_frozen_solver")
    return solver_id, cell_id


def _validate_ood_target(case: dict[str, Any], *, label: str) -> None:
    if case["design_case_class"] != "ood":
        raise HoldoutFrameError(f"{label}_must_have_ood_design_class")
    if case["design_solver_id"] is not None or case["design_cell_id"] is not None:
        raise HoldoutFrameError(f"{label}_ood_design_target_must_be_null")


def _case_record_digest(case: dict[str, Any]) -> str:
    return sha256_digest(case)


def _validate_cases(
    frame: dict[str, Any],
    *,
    protocol: dict[str, Any],
    roles: dict[str, Any],
    identities: Sequence[dict[str, str]],
    authored_after: datetime,
    frozen_at: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    positive_raw = _exact_list(frame["positive_cases"], "positive_cases")
    ood_raw = _exact_list(frame["ood_cases"], "ood_cases")
    if len(positive_raw) + len(ood_raw) > MAX_FRAME_CASE_COUNT:
        raise HoldoutFrameError("frame_case_count_exceeds_limit")
    solver_cells = {
        row["canonical_solver_id"]: row["cell_id"] for row in identities
    }
    positive_authors = set(roles["positive_author_actor_ids"])
    ood_authors = set(roles["ood_author_actor_ids"])

    positive: list[dict[str, Any]] = []
    positive_groups: dict[tuple[str, str], int] = {}
    for index, raw in enumerate(positive_raw):
        label = f"positive_cases_{index}"
        case = _validate_case_common(
            raw,
            label=label,
            allowed_authors=positive_authors,
            authored_after=authored_after,
            frozen_at=frozen_at,
        )
        if case["stratum"] not in protocol_contract.POSITIVE_STRATA:
            raise HoldoutFrameError(f"{label}_stratum_invalid")
        if case["language"] != case["stratum"].split("_", 1)[0]:
            raise HoldoutFrameError(f"{label}_language_stratum_mismatch")
        solver_id, _ = _positive_case_target(
            case,
            label=label,
            solver_cells=solver_cells,
        )
        positive_groups[(case["stratum"], solver_id)] = (
            positive_groups.get((case["stratum"], solver_id), 0) + 1
        )
        positive.append(case)

    ood: list[dict[str, Any]] = []
    ood_groups: dict[tuple[str, str], int] = {}
    for index, raw in enumerate(ood_raw):
        label = f"ood_cases_{index}"
        case = _validate_case_common(
            raw,
            label=label,
            allowed_authors=ood_authors,
            authored_after=authored_after,
            frozen_at=frozen_at,
        )
        if case["stratum"] not in protocol_contract.OOD_STRATA:
            raise HoldoutFrameError(f"{label}_stratum_invalid")
        _validate_ood_target(case, label=label)
        ood_groups[(case["stratum"], case["language"])] = (
            ood_groups.get((case["stratum"], case["language"]), 0) + 1
        )
        ood.append(case)

    expected_positive_groups = {
        (stratum, solver_id)
        for stratum in protocol_contract.POSITIVE_STRATA
        for solver_id in solver_cells
    }
    frame_multiplier = protocol["sampling"]["minimum_frame_multiplier"]
    if set(positive_groups) != expected_positive_groups or any(
        count < frame_multiplier for count in positive_groups.values()
    ):
        raise HoldoutFrameError("positive_frame_group_counts_mismatch")

    selected_languages = protocol["sampling"]["ood_language_counts_per_stratum"]
    expected_ood_groups = {
        (stratum, language)
        for stratum in protocol_contract.OOD_STRATA
        for language in ("fi", "en")
    }
    if set(ood_groups) != expected_ood_groups or any(
        ood_groups[group]
        < selected_languages[group[1]] * frame_multiplier
        for group in expected_ood_groups
    ):
        raise HoldoutFrameError("ood_frame_group_counts_mismatch")

    positive.sort(key=lambda case: case["opaque_case_id"])
    ood.sort(key=lambda case: case["opaque_case_id"])
    all_cases = [*positive, *ood]
    case_ids = [case["opaque_case_id"] for case in all_cases]
    cluster_ids = [case["intent_cluster_id"] for case in all_cases]
    family_digests = [case["semantic_family_digest"] for case in all_cases]
    record_digests = [_case_record_digest(case) for case in all_cases]
    query_keys = [_query_dedup_key(case["query"]) for case in all_cases]
    for values, error in (
        (case_ids, "opaque_case_ids_must_be_globally_unique"),
        (cluster_ids, "intent_clusters_must_be_globally_unique"),
        (family_digests, "semantic_families_must_be_globally_unique"),
        (record_digests, "case_records_must_be_globally_unique"),
        (query_keys, "normalized_queries_must_be_globally_unique"),
    ):
        if len(set(values)) != len(values):
            raise HoldoutFrameError(error)
    if {case["author_actor_id"] for case in positive} != positive_authors:
        raise HoldoutFrameError("positive_author_roster_has_unused_or_missing_actor")
    if {case["author_actor_id"] for case in ood} != ood_authors:
        raise HoldoutFrameError("ood_author_roster_has_unused_or_missing_actor")
    return positive, ood


def validate_frame(
    frame_value: Any,
    *,
    protocol: Any,
    preregistration_state: Any,
) -> dict[str, Any]:
    """Validate an in-memory plaintext frame against a frozen protocol."""

    validated_protocol = _validated_protocol(protocol)
    protocol_digest = protocol_contract.preregistration_digest(validated_protocol)
    try:
        validated_state = protocol_contract.validate_state(
            preregistration_state,
            protocol=validated_protocol,
        )
    except protocol_contract.HoldoutProtocolError as exc:
        raise HoldoutFrameError("preregistration_state_invalid") from exc
    if validated_state["stage"] != "preregistered":
        raise HoldoutFrameError("frame_requires_preregistered_state")
    preregistration_state_digest = protocol_contract.state_digest(
        validated_state,
        protocol=validated_protocol,
    )
    frame = _exact_dict(frame_value, _FRAME_KEYS, "frame")
    if _exact_string(frame["schema_version"], "frame_schema_version") != FRAME_SCHEMA:
        raise HoldoutFrameError("frame_schema_mismatch")
    observed_protocol_digest = _regex_string(
        frame["protocol_digest"], _FULL_SHA256, "frame_protocol_digest"
    )
    if observed_protocol_digest != protocol_digest:
        raise HoldoutFrameError("frame_protocol_digest_mismatch")
    observed_state_digest = _regex_string(
        frame["preregistration_state_digest"],
        _FULL_SHA256,
        "frame_preregistration_state_digest",
    )
    if observed_state_digest != preregistration_state_digest:
        raise HoldoutFrameError("frame_preregistration_state_digest_mismatch")
    preregistration_text, preregistration_published_at = _canonical_utc(
        frame["declared_preregistration_published_at_utc"],
        "declared_preregistration_published_at_utc",
    )
    frozen_text, frozen_at = _canonical_utc(
        frame["frame_frozen_at_utc"], "frame_frozen_at_utc"
    )
    _, cutoff = _canonical_utc(
        validated_protocol["cutoff"]["cutoff_utc"], "protocol_cutoff_utc"
    )
    authored_after = max(cutoff, preregistration_published_at)
    if frozen_at <= authored_after:
        raise HoldoutFrameError("frame_freeze_must_follow_preregistration")
    exclusion_digest = _regex_string(
        frame["development_exclusion_set_digest"],
        _FULL_SHA256,
        "development_exclusion_set_digest",
    )

    identities = _validated_solver_identities(frame["solver_identities"])
    identity_digest = solver_identity_set_digest(identities)
    observed_identity_digest = _regex_string(
        frame["solver_identity_set_digest"],
        _FULL_SHA256,
        "frame_solver_identity_set_digest",
    )
    expected_identity_digest = validated_protocol["candidate_identity"][
        "projection_identity_set_digest"
    ]
    if (
        observed_identity_digest != identity_digest
        or identity_digest != expected_identity_digest
    ):
        raise HoldoutFrameError("frame_solver_identity_set_digest_mismatch")

    roles = _validate_roles(frame["roles"])
    attestations = _validate_attestations(frame["attestations"])
    positive, ood = _validate_cases(
        frame,
        protocol=validated_protocol,
        roles=roles,
        identities=identities,
        authored_after=authored_after,
        frozen_at=frozen_at,
    )
    _validate_capability_boundary(
        frame["capability_boundary"],
        artifact_class="external_plaintext_frame_validation_only",
    )
    return _canonical_clone(
        {
            "schema_version": FRAME_SCHEMA,
            "protocol_digest": protocol_digest,
            "preregistration_state_digest": preregistration_state_digest,
            "declared_preregistration_published_at_utc": preregistration_text,
            "frame_frozen_at_utc": frozen_text,
            "development_exclusion_set_digest": exclusion_digest,
            "solver_identity_set_digest": identity_digest,
            "solver_identities": identities,
            "roles": roles,
            "attestations": attestations,
            "positive_cases": positive,
            "ood_cases": ood,
            "capability_boundary": frame["capability_boundary"],
        },
        "frame",
        maximum_bytes=protocol_contract.MAX_COMMITMENT_PAYLOAD_BYTES,
    )


def _validate_external_author_batch_with_context(
    value: Any,
    *,
    case_class: str,
    protocol_digest: str,
    state_digest: str,
    publication_text: str,
    authored_after: datetime,
    frozen_at: datetime,
    identity_digest: str,
    solver_cells: dict[str, str],
    allowed_authors: set[str],
) -> dict[str, Any]:
    batch = _exact_dict(value, _AUTHOR_BATCH_KEYS, "author_batch")
    if (
        _exact_string(batch["schema_version"], "author_batch_schema_version")
        != AUTHOR_BATCH_SCHEMA
    ):
        raise HoldoutFrameError("author_batch_schema_mismatch")
    if (
        _regex_string(
            batch["protocol_digest"],
            _FULL_SHA256,
            "author_batch_protocol_digest",
        )
        != protocol_digest
    ):
        raise HoldoutFrameError("author_batch_protocol_digest_mismatch")
    if (
        _regex_string(
            batch["preregistration_state_digest"],
            _FULL_SHA256,
            "author_batch_preregistration_state_digest",
        )
        != state_digest
    ):
        raise HoldoutFrameError("author_batch_preregistration_state_digest_mismatch")
    batch_publication_text, _ = _canonical_utc(
        batch["declared_preregistration_published_at_utc"],
        "author_batch_declared_preregistration_published_at_utc",
    )
    if batch_publication_text != publication_text:
        raise HoldoutFrameError("author_batch_preregistration_publication_mismatch")
    if (
        _regex_string(
            batch["solver_identity_set_digest"],
            _FULL_SHA256,
            "author_batch_solver_identity_set_digest",
        )
        != identity_digest
    ):
        raise HoldoutFrameError("author_batch_solver_identity_set_digest_mismatch")
    if _exact_string(batch["case_class"], "author_batch_case_class") != case_class:
        raise HoldoutFrameError("author_batch_case_class_mismatch")

    author_id = _regex_string(
        batch["author_actor_id"],
        _ACTOR_ID,
        "author_batch_author_actor_id",
    )
    if author_id not in allowed_authors:
        raise HoldoutFrameError("author_batch_author_not_in_expected_role")

    raw_cases = list(_exact_list(batch["cases"], "author_batch_cases"))
    if not raw_cases:
        raise HoldoutFrameError("author_batch_cases_must_not_be_empty")
    if len(raw_cases) > MAX_FRAME_CASE_COUNT:
        raise HoldoutFrameError("author_batch_case_count_exceeds_limit")
    cases: list[dict[str, Any]] = []
    for index, raw_case in enumerate(raw_cases):
        label = f"author_batch_cases_{index}"
        case = _validate_case_common(
            raw_case,
            label=label,
            allowed_authors={author_id},
            authored_after=authored_after,
            frozen_at=frozen_at,
        )
        if case_class == "positive":
            if case["stratum"] not in protocol_contract.POSITIVE_STRATA:
                raise HoldoutFrameError(f"{label}_stratum_invalid")
            if case["language"] != case["stratum"].split("_", 1)[0]:
                raise HoldoutFrameError(f"{label}_language_stratum_mismatch")
            _positive_case_target(
                case,
                label=label,
                solver_cells=solver_cells,
            )
        else:
            if case["stratum"] not in protocol_contract.OOD_STRATA:
                raise HoldoutFrameError(f"{label}_stratum_invalid")
            _validate_ood_target(case, label=label)
        cases.append(case)

    uniqueness_vectors = (
        ([case["opaque_case_id"] for case in cases], "opaque_case_ids"),
        ([case["intent_cluster_id"] for case in cases], "intent_clusters"),
        ([case["semantic_family_digest"] for case in cases], "semantic_families"),
        ([_case_record_digest(case) for case in cases], "case_records"),
        ([_query_dedup_key(case["query"]) for case in cases], "normalized_queries"),
    )
    for values, label in uniqueness_vectors:
        if len(set(values)) != len(values):
            raise HoldoutFrameError(f"author_batch_{label}_must_be_unique")

    _validate_capability_boundary(
        batch["capability_boundary"],
        artifact_class="external_author_batch_validation_only",
    )
    return _canonical_clone(
        {
            "schema_version": AUTHOR_BATCH_SCHEMA,
            "protocol_digest": protocol_digest,
            "preregistration_state_digest": state_digest,
            "declared_preregistration_published_at_utc": publication_text,
            "solver_identity_set_digest": identity_digest,
            "case_class": case_class,
            "author_actor_id": author_id,
            "cases": sorted(cases, key=lambda case: case["opaque_case_id"]),
            "capability_boundary": _no_authority_boundary(
                "external_author_batch_validation_only"
            ),
        },
        "author_batch",
        maximum_bytes=protocol_contract.MAX_COMMITMENT_PAYLOAD_BYTES,
    )


def validate_external_author_batch(
    value: Any,
    *,
    expected_case_class: str,
    protocol: Any,
    preregistration_state: Any,
    declared_preregistration_published_at_utc: str,
    frame_frozen_at_utc: str,
    solver_identities: Any,
    roles: Any,
) -> dict[str, Any]:
    """Validate one score-blind author batch without granting provenance.

    The caller supplies every identifier, timestamp, query, role declaration,
    and frozen candidate binding.  This pure validator neither authenticates
    the declared author nor creates missing values.  Its returned capability
    boundary therefore remains entirely false.
    """

    case_class = _exact_string(expected_case_class, "expected_case_class")
    if case_class not in {"positive", "ood"}:
        raise HoldoutFrameError("expected_case_class_invalid")

    validated_protocol = _validated_protocol(protocol)
    protocol_digest = protocol_contract.preregistration_digest(validated_protocol)
    try:
        validated_state = protocol_contract.validate_state(
            preregistration_state,
            protocol=validated_protocol,
        )
    except protocol_contract.HoldoutProtocolError as exc:
        raise HoldoutFrameError("preregistration_state_invalid") from exc
    if validated_state["stage"] != "preregistered":
        raise HoldoutFrameError("author_batch_requires_preregistered_state")
    state_digest = protocol_contract.state_digest(
        validated_state,
        protocol=validated_protocol,
    )

    identities = _validated_solver_identities(solver_identities)
    identity_digest = solver_identity_set_digest(identities)
    if identity_digest != validated_protocol["candidate_identity"][
        "projection_identity_set_digest"
    ]:
        raise HoldoutFrameError("author_batch_solver_identity_set_mismatch")
    solver_cells = {
        row["canonical_solver_id"]: row["cell_id"] for row in identities
    }
    validated_roles = _validate_roles(roles)
    roster_key = (
        "positive_author_actor_ids"
        if case_class == "positive"
        else "ood_author_actor_ids"
    )

    publication_text, publication_at = _canonical_utc(
        declared_preregistration_published_at_utc,
        "declared_preregistration_published_at_utc",
    )
    _, frozen_at = _canonical_utc(
        frame_frozen_at_utc,
        "frame_frozen_at_utc",
    )
    _, cutoff = _canonical_utc(
        validated_protocol["cutoff"]["cutoff_utc"],
        "protocol_cutoff_utc",
    )
    authored_after = max(cutoff, publication_at)
    if frozen_at <= authored_after:
        raise HoldoutFrameError("frame_freeze_must_follow_preregistration")

    return _validate_external_author_batch_with_context(
        value,
        case_class=case_class,
        protocol_digest=protocol_digest,
        state_digest=state_digest,
        publication_text=publication_text,
        authored_after=authored_after,
        frozen_at=frozen_at,
        identity_digest=identity_digest,
        solver_cells=solver_cells,
        allowed_authors=set(validated_roles[roster_key]),
    )


def assemble_frame_from_external_author_batches(
    *,
    acting_actor_id: str,
    protocol: Any,
    preregistration_state: Any,
    declared_preregistration_published_at_utc: str,
    frame_frozen_at_utc: str,
    development_exclusion_set_digest: str,
    solver_identities: Any,
    roles: Any,
    attestations: Any,
    positive_author_batches: Any,
    ood_author_batches: Any,
) -> dict[str, Any]:
    """Assemble a frame only for the declared custodian, without auth claims.

    ``acting_actor_id`` is checked only against the caller-supplied role
    declaration.  It is not an authenticated identity or provenance proof.
    Every raw author batch is revalidated before the unchanged frame validator
    enforces global coverage, uniqueness, and candidate bindings.
    """

    validated_protocol = _validated_protocol(protocol)
    protocol_digest = protocol_contract.preregistration_digest(validated_protocol)
    try:
        validated_state = protocol_contract.validate_state(
            preregistration_state,
            protocol=validated_protocol,
        )
    except protocol_contract.HoldoutProtocolError as exc:
        raise HoldoutFrameError("preregistration_state_invalid") from exc
    if validated_state["stage"] != "preregistered":
        raise HoldoutFrameError("frame_requires_preregistered_state")
    state_digest = protocol_contract.state_digest(
        validated_state,
        protocol=validated_protocol,
    )
    identities = _validated_solver_identities(solver_identities)
    identity_digest = solver_identity_set_digest(identities)
    if identity_digest != validated_protocol["candidate_identity"][
        "projection_identity_set_digest"
    ]:
        raise HoldoutFrameError("frame_solver_identity_set_digest_mismatch")
    validated_roles = _validate_roles(roles)
    actor_id = _regex_string(
        acting_actor_id,
        _ACTOR_ID,
        "acting_actor_id",
    )
    if actor_id != validated_roles["custodian_actor_id"]:
        raise HoldoutFrameError("frame_assembly_requires_declared_custodian")
    validated_attestations = _validate_attestations(attestations)
    exclusion_digest = _regex_string(
        development_exclusion_set_digest,
        _FULL_SHA256,
        "development_exclusion_set_digest",
    )
    publication_text, publication_at = _canonical_utc(
        declared_preregistration_published_at_utc,
        "declared_preregistration_published_at_utc",
    )
    frozen_text, frozen_at = _canonical_utc(
        frame_frozen_at_utc,
        "frame_frozen_at_utc",
    )
    _, cutoff = _canonical_utc(
        validated_protocol["cutoff"]["cutoff_utc"],
        "protocol_cutoff_utc",
    )
    authored_after = max(cutoff, publication_at)
    if frozen_at <= authored_after:
        raise HoldoutFrameError("frame_freeze_must_follow_preregistration")
    solver_cells = {
        row["canonical_solver_id"]: row["cell_id"] for row in identities
    }

    def preflight_lane_batches(
        raw_batches: Any,
        *,
        case_class: str,
        roster_key: str,
    ) -> tuple[list[Any], int]:
        batches = list(
            _exact_list(raw_batches, f"{case_class}_author_batches")
        )
        expected_authors = validated_roles[roster_key]
        if len(batches) != len(expected_authors):
            raise HoldoutFrameError(f"{case_class}_author_batch_roster_mismatch")
        authors: list[str] = []
        case_count = 0
        snapshots: list[dict[str, Any]] = []
        for index, raw_batch in enumerate(batches):
            batch = _exact_dict(
                raw_batch,
                _AUTHOR_BATCH_KEYS,
                f"{case_class}_author_batches_{index}",
            )
            authors.append(
                _regex_string(
                    batch["author_actor_id"],
                    _ACTOR_ID,
                    f"{case_class}_author_batches_{index}_author_actor_id",
                )
            )
            raw_cases = _exact_list(
                batch["cases"],
                f"{case_class}_author_batches_{index}_cases",
            )
            if not raw_cases:
                raise HoldoutFrameError("author_batch_cases_must_not_be_empty")
            case_count += len(raw_cases)
            if case_count > MAX_FRAME_CASE_COUNT:
                raise HoldoutFrameError("frame_case_count_exceeds_limit")
            snapshot = dict(batch)
            snapshot["cases"] = list(raw_cases)
            snapshots.append(snapshot)
        if len(authors) != len(set(authors)):
            raise HoldoutFrameError(f"{case_class}_author_batches_must_be_unique")
        if set(authors) != set(expected_authors):
            raise HoldoutFrameError(f"{case_class}_author_batch_roster_mismatch")
        return snapshots, case_count

    positive_raw_batches, positive_case_count = preflight_lane_batches(
        positive_author_batches,
        case_class="positive",
        roster_key="positive_author_actor_ids",
    )
    ood_raw_batches, ood_case_count = preflight_lane_batches(
        ood_author_batches,
        case_class="ood",
        roster_key="ood_author_actor_ids",
    )
    if positive_case_count + ood_case_count > MAX_FRAME_CASE_COUNT:
        raise HoldoutFrameError("frame_case_count_exceeds_limit")

    def validated_lane_batches(
        batches: list[Any],
        *,
        case_class: str,
        roster_key: str,
    ) -> list[dict[str, Any]]:
        allowed_authors = set(validated_roles[roster_key])
        validated = [
            _validate_external_author_batch_with_context(
                raw_batch,
                case_class=case_class,
                protocol_digest=protocol_digest,
                state_digest=state_digest,
                publication_text=publication_text,
                authored_after=authored_after,
                frozen_at=frozen_at,
                identity_digest=identity_digest,
                solver_cells=solver_cells,
                allowed_authors=allowed_authors,
            )
            for raw_batch in batches
        ]
        authors = [batch["author_actor_id"] for batch in validated]
        if len(authors) != len(set(authors)):
            raise HoldoutFrameError(f"{case_class}_author_batches_must_be_unique")
        if set(authors) != set(validated_roles[roster_key]):
            raise HoldoutFrameError(f"{case_class}_author_batch_roster_mismatch")
        return sorted(validated, key=lambda batch: batch["author_actor_id"])

    positive_batches = validated_lane_batches(
        positive_raw_batches,
        case_class="positive",
        roster_key="positive_author_actor_ids",
    )
    ood_batches = validated_lane_batches(
        ood_raw_batches,
        case_class="ood",
        roster_key="ood_author_actor_ids",
    )
    positive_cases = [
        case for batch in positive_batches for case in batch["cases"]
    ]
    ood_cases = [case for batch in ood_batches for case in batch["cases"]]
    frame = {
        "schema_version": FRAME_SCHEMA,
        "protocol_digest": protocol_digest,
        "preregistration_state_digest": state_digest,
        "declared_preregistration_published_at_utc": publication_text,
        "frame_frozen_at_utc": frozen_text,
        "development_exclusion_set_digest": exclusion_digest,
        "solver_identity_set_digest": identity_digest,
        "solver_identities": identities,
        "roles": validated_roles,
        "attestations": validated_attestations,
        "positive_cases": positive_cases,
        "ood_cases": ood_cases,
        "capability_boundary": _no_authority_boundary(
            "external_plaintext_frame_validation_only"
        ),
    }
    return validate_frame(
        frame,
        protocol=validated_protocol,
        preregistration_state=validated_state,
    )


def create_frame_commitment(
    frame: Any,
    *,
    protocol: Any,
    preregistration_state: Any,
    key: bytes,
) -> str:
    """Create a protocol-bound HMAC commitment without exposing plaintext."""

    validated_frame = validate_frame(
        frame,
        protocol=protocol,
        preregistration_state=preregistration_state,
    )
    try:
        return protocol_contract.create_hmac_commitment(
            "frame_manifest",
            key=key,
            protocol_digest=validated_frame["protocol_digest"],
            payload=validated_frame,
        )
    except protocol_contract.HoldoutProtocolError as exc:
        raise HoldoutFrameError("frame_commitment_input_invalid") from exc


def _selection_rank(
    seed: bytes,
    *,
    domain: str,
    protocol_digest: str,
    frame_commitment: str,
    group: Sequence[str],
    case_id: str,
    record_digest: str,
) -> bytes:
    payload = canonical_json_bytes(
        {
            "domain": domain,
            "protocol_digest": protocol_digest,
            "frame_commitment": frame_commitment,
            "group": list(group),
            "case_id": case_id,
            "record_digest": record_digest,
        }
    )
    return hmac.new(
        seed,
        b"\0".join((SELECTION_CONTEXT, payload)),
        hashlib.sha256,
    ).digest()


def _select_group(
    cases: Iterable[dict[str, Any]],
    *,
    count: int,
    seed: bytes,
    domain: str,
    protocol_digest: str,
    frame_commitment: str,
    group: Sequence[str],
) -> list[str]:
    ranked = [
        (
            _selection_rank(
                seed,
                domain=domain,
                protocol_digest=protocol_digest,
                frame_commitment=frame_commitment,
                group=group,
                case_id=case["opaque_case_id"],
                record_digest=_case_record_digest(case),
            ),
            case["opaque_case_id"],
        )
        for case in cases
    ]
    if len(ranked) < count:
        raise HoldoutFrameError("selection_group_is_undersized")
    ranks = [rank for rank, _ in ranked]
    if len(set(ranks)) != len(ranks):
        raise HoldoutFrameError("selection_rank_collision")
    ranked.sort(key=lambda row: row[0])
    return [case_id for _, case_id in ranked[:count]]


def _validate_seed_and_key(seed: Any, frame_key: Any) -> tuple[bytes, bytes]:
    if type(seed) is not bytes or len(seed) != 32:
        raise HoldoutFrameError("selection_seed_must_be_exactly_32_bytes")
    if type(frame_key) is not bytes or len(frame_key) != 32:
        raise HoldoutFrameError("frame_commitment_key_must_be_exactly_32_bytes")
    if hmac.compare_digest(seed, frame_key):
        raise HoldoutFrameError("selection_seed_and_frame_key_must_be_distinct")
    return seed, frame_key


def select_frame_cases(
    frame: Any,
    *,
    protocol: Any,
    preregistration_state: Any,
    selection_seed: bytes,
    frame_commitment_key: bytes,
    expected_frame_commitment: str,
) -> dict[str, Any]:
    """Recompute the exact 132-positive/125-OOD opaque selection projection."""

    seed, frame_key = _validate_seed_and_key(
        selection_seed, frame_commitment_key
    )
    commitment = _regex_string(
        expected_frame_commitment,
        _HMAC_SHA256,
        "expected_frame_commitment",
    )
    validated_protocol = _validated_protocol(protocol)
    validated_frame = validate_frame(
        frame,
        protocol=validated_protocol,
        preregistration_state=preregistration_state,
    )
    observed_commitment = create_frame_commitment(
        validated_frame,
        protocol=validated_protocol,
        preregistration_state=preregistration_state,
        key=frame_key,
    )
    if not hmac.compare_digest(commitment, observed_commitment):
        raise HoldoutFrameError("frame_commitment_mismatch")
    protocol_digest = protocol_contract.preregistration_digest(
        validated_protocol
    )

    positive_by_group: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for case in validated_frame["positive_cases"]:
        solver_id = case["design_solver_id"]
        key = (case["stratum"], solver_id)
        positive_by_group.setdefault(key, []).append(case)
    selected_positive: list[str] = []
    for group in sorted(positive_by_group):
        selected_positive.extend(
            _select_group(
                positive_by_group[group],
                count=1,
                seed=seed,
                domain="positive_stratum_solver",
                protocol_digest=protocol_digest,
                frame_commitment=commitment,
                group=group,
            )
        )

    ood_by_group: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for case in validated_frame["ood_cases"]:
        key = (case["stratum"], case["language"])
        ood_by_group.setdefault(key, []).append(case)
    selected_ood: list[str] = []
    language_counts = validated_protocol["sampling"][
        "ood_language_counts_per_stratum"
    ]
    for group in sorted(ood_by_group):
        selected_ood.extend(
            _select_group(
                ood_by_group[group],
                count=language_counts[group[1]],
                seed=seed,
                domain="ood_stratum_language",
                protocol_digest=protocol_digest,
                frame_commitment=commitment,
                group=group,
            )
        )

    selected = [*selected_positive, *selected_ood]
    if (
        len(selected_positive)
        != validated_protocol["sampling"]["positive_cluster_count"]
        or len(selected_ood)
        != validated_protocol["sampling"]["ood_cluster_count"]
        or len(set(selected)) != len(selected)
    ):
        raise HoldoutFrameError("selected_case_count_derivation_mismatch")
    case_by_id = {
        case["opaque_case_id"]: case
        for case in [
            *validated_frame["positive_cases"],
            *validated_frame["ood_cases"],
        ]
    }
    mixed_ranks = [
        (
            _selection_rank(
                seed,
                domain="opaque_mixed_order",
                protocol_digest=protocol_digest,
                frame_commitment=commitment,
                group=(),
                case_id=case_id,
                record_digest=_case_record_digest(case_by_id[case_id]),
            ),
            case_id,
        )
        for case_id in selected
    ]
    if len({rank for rank, _ in mixed_ranks}) != len(mixed_ranks):
        raise HoldoutFrameError("mixed_order_rank_collision")
    mixed = [
        case_id
        for _, case_id in sorted(mixed_ranks, key=lambda row: row[0])
    ]
    return _validate_selection_shape(
        {
            "schema_version": SELECTION_SCHEMA,
            "protocol_digest": protocol_digest,
            "frame_commitment": commitment,
            "selection_seed_sha256": (
                "sha256:" + hashlib.sha256(seed).hexdigest()
            ),
            "selection_algorithm": SELECTION_ALGORITHM,
            "selection_attempt": 0,
            "selected_case_ids": mixed,
            "selected_case_count": len(mixed),
            "positive_case_count": len(selected_positive),
            "ood_case_count": len(selected_ood),
            "positive_group_count": len(positive_by_group),
            "ood_group_count": len(ood_by_group),
            "capability_boundary": _no_authority_boundary(
                "deterministic_selection_projection_only"
            ),
        },
        protocol=validated_protocol,
    )


def _validate_selection_shape(
    value: Any,
    *,
    protocol: Any,
) -> dict[str, Any]:
    validated_protocol = _validated_protocol(protocol)
    projection = _exact_dict(value, _SELECTION_KEYS, "selection_projection")
    if _exact_string(
        projection["schema_version"], "selection_schema_version"
    ) != SELECTION_SCHEMA:
        raise HoldoutFrameError("selection_schema_mismatch")
    protocol_digest = _regex_string(
        projection["protocol_digest"],
        _FULL_SHA256,
        "selection_protocol_digest",
    )
    if protocol_digest != protocol_contract.preregistration_digest(
        validated_protocol
    ):
        raise HoldoutFrameError("selection_protocol_digest_mismatch")
    _regex_string(
        projection["frame_commitment"],
        _HMAC_SHA256,
        "selection_frame_commitment",
    )
    _regex_string(
        projection["selection_seed_sha256"],
        _FULL_SHA256,
        "selection_seed_sha256",
    )
    if _exact_string(
        projection["selection_algorithm"], "selection_algorithm"
    ) != SELECTION_ALGORITHM:
        raise HoldoutFrameError("selection_algorithm_mismatch")
    _exact_int(projection["selection_attempt"], 0, "selection_attempt")
    case_ids = _exact_list(
        projection["selected_case_ids"], "selected_case_ids"
    )
    for index, case_id in enumerate(case_ids):
        _regex_string(case_id, _OPAQUE_CASE_ID, f"selected_case_ids_{index}")
    if len(set(case_ids)) != len(case_ids):
        raise HoldoutFrameError("selected_case_ids_must_be_unique")
    expected_positive = validated_protocol["sampling"]["positive_cluster_count"]
    expected_ood = validated_protocol["sampling"]["ood_cluster_count"]
    _exact_int(
        projection["selected_case_count"],
        expected_positive + expected_ood,
        "selected_case_count",
    )
    _exact_int(
        projection["positive_case_count"],
        expected_positive,
        "positive_case_count",
    )
    _exact_int(
        projection["ood_case_count"], expected_ood, "ood_case_count"
    )
    _exact_int(
        projection["positive_group_count"],
        len(protocol_contract.POSITIVE_STRATA)
        * validated_protocol["sampling"]["frozen_solver_count"],
        "positive_group_count",
    )
    _exact_int(
        projection["ood_group_count"],
        len(protocol_contract.OOD_STRATA) * 2,
        "ood_group_count",
    )
    if len(case_ids) != projection["selected_case_count"]:
        raise HoldoutFrameError("selected_case_ids_length_mismatch")
    _validate_capability_boundary(
        projection["capability_boundary"],
        artifact_class="deterministic_selection_projection_only",
    )
    return _canonical_clone(projection, "selection_projection")


def validate_selection_projection(
    value: Any,
    *,
    frame: Any,
    protocol: Any,
    preregistration_state: Any,
    selection_seed: bytes,
    frame_commitment_key: bytes,
    expected_frame_commitment: str,
) -> dict[str, Any]:
    """Validate a projection and require exact recomputation from bound inputs."""

    validated = _validate_selection_shape(value, protocol=protocol)
    expected = select_frame_cases(
        frame,
        protocol=protocol,
        preregistration_state=preregistration_state,
        selection_seed=selection_seed,
        frame_commitment_key=frame_commitment_key,
        expected_frame_commitment=expected_frame_commitment,
    )
    if not hmac.compare_digest(
        canonical_json_bytes(validated), canonical_json_bytes(expected)
    ):
        raise HoldoutFrameError("selection_projection_recomputation_mismatch")
    return validated


__all__ = [
    "AUTHOR_BATCH_SCHEMA",
    "FRAME_SCHEMA",
    "HoldoutFrameError",
    "MAX_FRAME_CASE_COUNT",
    "MAX_QUERY_UTF8_BYTES",
    "SELECTION_ALGORITHM",
    "SELECTION_SCHEMA",
    "assemble_frame_from_external_author_batches",
    "create_frame_commitment",
    "select_frame_cases",
    "solver_identity_set_digest",
    "validate_external_author_batch",
    "validate_frame",
    "validate_selection_projection",
]
