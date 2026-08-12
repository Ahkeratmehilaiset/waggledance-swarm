# SPDX-License-Identifier: BUSL-1.1
"""Pure contract substrate for a future sealed MAGMA FAISS holdout.

This module deliberately does not read Git, open FAISS, embed queries, persist
state, create a holdout pack, or score labels.  It provides only four things:

* a closed-world preregistration validator;
* the zero-failure confidence calculation used by that contract;
* domain-separated HMAC commitments for externally held canonical JSON; and
* a structural state-chain projection that makes the required ordering
  explicit without claiming that external commits or one-shot execution were
  verified.

Git ancestry, remote publication, exclusive capture, plaintext custody, and
scoring are separate future adapters.  Consequently every state produced here
keeps all evidence, runtime, pruning, promotion, and authority claims false.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from datetime import datetime
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Sequence

from waggledance.core.hex_cell_topology import ALL_CELLS
from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest


PROTOCOL_SCHEMA = "wd.magma.faiss_holdout_preregistration.v1"
STATE_SCHEMA = "wd.magma.faiss_holdout_state_projection.v1"
TRANSITION_SCHEMA = "wd.magma.faiss_holdout_transition_declaration.v1"
COMMITMENT_SCHEME = "hmac-sha256"
COMMITMENT_CONTEXT = b"WD-MAGMA-FAISS-HOLDOUT-HMAC-v1"
MAX_COMMITMENT_PAYLOAD_BYTES = 8 * 1024 * 1024
MAX_JSON_DEPTH = 32

FIXED_THRESHOLD = 0.60
FIXED_SEARCH_K = 5
FROZEN_SOLVER_COUNT = 22
POSITIVE_CLUSTER_COUNT = 132
OOD_CLUSTER_COUNT = 125
MINIMUM_PER_CRITICAL_CLASS = 20
FAMILYWISE_CONFIDENCE = 0.95
COMPONENT_ALPHA = 0.025
MAXIMUM_ERROR_RATE = 0.03

POSITIVE_STRATA = (
    "fi_direct",
    "fi_indirect",
    "fi_near_neighbor",
    "en_direct",
    "en_indirect",
    "en_near_neighbor",
)
OOD_STRATA = (
    "live_external_information",
    "open_domain_knowledge",
    "external_action_or_connector",
    "generative_or_language_transformation",
    "near_domain_unsupported_capability",
)

COMMITMENT_DOMAINS = frozenset(
    {
        "frame_manifest",
        "selection_seed",
        "query_pack",
        "label_pack",
        "adjudication_receipt",
    }
)

STAGES = (
    "preregistered",
    "frame_committed",
    "seed_revealed",
    "pack_sealed",
    "query_captured",
    "labels_revealed",
    "scored",
)
_EVIDENCE_KIND_BY_STAGE = {
    "preregistered": "preregistration",
    "frame_committed": "frame_manifest_commitment",
    "seed_revealed": "selection_seed_receipt",
    "pack_sealed": "query_label_adjudication_seal",
    "query_captured": "label_blind_capture_receipt",
    "labels_revealed": "label_release_receipt",
    "scored": "fixed_threshold_verdict",
}

_FULL_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_HMAC_SHA256 = re.compile(r"^hmac-sha256:[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_PROTOCOL_ID = re.compile(r"^holdoutproto_[0-9a-f]{32}$")
_SNAPSHOT_ID = re.compile(r"^faisscand_[0-9a-f]{64}$")
_PROJECTION_COMMIT_ID = re.compile(r"^proj_[0-9a-f]{64}$")
_CANONICAL_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_WINDOWS_RESERVED_BASENAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
    | {f"COM{index}" for index in "¹²³"}
    | {f"LPT{index}" for index in "¹²³"}
)

_PROTOCOL_KEYS = frozenset(
    {
        "schema_version",
        "protocol_id",
        "candidate_identity",
        "cutoff",
        "sampling",
        "endpoint_gate",
        "statistics",
        "role_separation",
        "capability_boundary",
    }
)
_CANDIDATE_KEYS = frozenset(
    {
        "candidate_commit",
        "solver_count",
        "snapshot_id",
        "topology_digest",
        "projection_identity_set_digest",
        "embedding_contract_digest",
        "model_catalog_digest",
        "source_projection_commit_ids",
        "faiss_identity",
        "capture_tool_path",
        "capture_tool_sha256",
        "score_tool_path",
        "score_tool_sha256",
        "score_phase_dependency_policy",
        "search_k",
        "search_algorithm",
        "score_value_semantics",
    }
)
_FAISS_KEYS = frozenset(
    {"faiss_version", "faiss_compile_options", "faiss_binary_set_sha256"}
)
_CUTOFF_KEYS = frozenset(
    {"cutoff_commit", "cutoff_utc", "collection_after_cutoff_required"}
)
_SAMPLING_KEYS = frozenset(
    {
        "sampling_unit",
        "translations_and_variants_share_cluster",
        "selection_method",
        "frame_commit_precedes_seed_reveal",
        "selection_attempt_count",
        "selection_reseed_allowed",
        "post_selection_replacement_allowed",
        "minimum_frame_multiplier",
        "frozen_solver_count",
        "positive_cluster_count",
        "ood_cluster_count",
        "minimum_per_critical_class",
        "positive_strata",
        "positive_per_stratum",
        "positive_solver_coverage_policy",
        "positive_solver_identity_binding",
        "ood_strata",
        "ood_per_stratum",
        "ood_language_counts_per_stratum",
        "selected_intent_clusters_globally_unique",
        "no_translation_pairs_across_clusters",
        "target_population",
    }
)
_ENDPOINT_KEYS = frozenset(
    {
        "threshold",
        "comparison",
        "threshold_override_allowed",
        "threshold_sweep_allowed",
        "positive_error_predicate",
        "ood_error_predicate",
        "zero_failures_required",
        "adjudication_agreement_required",
    }
)
_STATISTICS_KEYS = frozenset(
    {
        "method",
        "finite_frame_estimand",
        "sampling_model",
        "bound_semantics",
        "iid_clopper_pearson_claimed",
        "familywise_confidence",
        "component_alpha",
        "maximum_error_rate",
        "simultaneous_component_bounds_required",
    }
)
_ROLE_KEYS = frozenset(
    {
        "candidate_owner_authors_cases",
        "candidate_owner_adjudicates",
        "case_authors_see_scores",
        "adjudicators_see_scores",
        "adjudicators_are_independent",
        "custodian_external_to_candidate_owner",
        "positive_and_ood_case_authors_disjoint",
        "plaintext_pack_in_repository",
    }
)
_CAPABILITY_KEYS = frozenset(
    {
        "artifact_class",
        "external_provenance_verified",
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
_STATE_KEYS = frozenset(
    {
        "schema_version",
        "protocol_digest",
        "candidate_commit",
        "stage",
        "sequence",
        "previous_state_digest",
        "evidence",
        "declared_commit_chain",
        "capability_boundary",
    }
)
_EVIDENCE_KEYS = frozenset(
    {"kind", "declared_commit", "artifact_digest"}
)
_TRANSITION_KEYS = frozenset(
    {
        "schema_version",
        "protocol_digest",
        "from_stage",
        "to_stage",
        "sequence",
        "previous_state_digest",
        "evidence_kind",
        "declared_commit",
        "artifact_digest",
    }
)


class HoldoutProtocolError(ValueError):
    """A holdout protocol value violates the closed-world contract."""


def _exact_dict(
    value: Any,
    expected_keys: frozenset[str],
    label: str,
) -> dict[str, Any]:
    if type(value) is not dict:
        raise HoldoutProtocolError(f"{label}_must_be_exact_object")
    if any(type(key) is not str for key in value):
        raise HoldoutProtocolError(f"{label}_keys_must_be_exact_strings")
    actual = frozenset(value)
    if actual != expected_keys:
        raise HoldoutProtocolError(
            f"{label}_keys_mismatch:missing={sorted(expected_keys - actual)},"
            f"extra={sorted(actual - expected_keys)}"
        )
    return value


def _require_exact_string(value: Any, label: str) -> str:
    if type(value) is not str or not value:
        raise HoldoutProtocolError(f"{label}_must_be_nonempty_exact_string")
    return value


def _require_regex(value: Any, pattern: re.Pattern[str], label: str) -> str:
    result = _require_exact_string(value, label)
    if pattern.fullmatch(result) is None:
        raise HoldoutProtocolError(f"{label}_invalid")
    return result


def _require_exact_bool(value: Any, expected: bool, label: str) -> None:
    if type(value) is not bool or value is not expected:
        raise HoldoutProtocolError(f"{label}_must_be_{str(expected).lower()}")


def _require_exact_int(value: Any, expected: int, label: str) -> None:
    if type(value) is not int or value != expected:
        raise HoldoutProtocolError(f"{label}_must_equal_{expected}")


def _require_exact_float(value: Any, expected: float, label: str) -> None:
    if type(value) is not float or not math.isfinite(value) or value != expected:
        raise HoldoutProtocolError(f"{label}_must_equal_{expected}")


def _require_exact_string_list(
    value: Any,
    expected: Sequence[str],
    label: str,
) -> None:
    if (
        type(value) is not list
        or any(type(item) is not str for item in value)
        or tuple(value) != tuple(expected)
    ):
        raise HoldoutProtocolError(f"{label}_mismatch")


def _require_safe_repo_python_path(value: Any, label: str) -> str:
    result = _require_exact_string(value, label)
    if (
        "\\" in result
        or any(character in '<>:"|?*' for character in result)
        or any(ord(character) < 32 or ord(character) == 127 for character in result)
        or PureWindowsPath(result).drive
    ):
        raise HoldoutProtocolError(f"{label}_must_use_posix_separators")
    path = PurePosixPath(result)
    if (
        path.is_absolute()
        or not path.parts
        or path.as_posix() != result
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(part.casefold() == ".git" for part in path.parts)
        or any(part.endswith((" ", ".")) for part in path.parts)
        or any(
            part.split(".", 1)[0].rstrip(" .").upper()
            in _WINDOWS_RESERVED_BASENAMES
            for part in path.parts
        )
        or path.suffix != ".py"
    ):
        raise HoldoutProtocolError(f"{label}_must_be_safe_repo_python_path")
    return result


def _require_canonical_utc(value: Any, label: str) -> str:
    result = _require_regex(value, _CANONICAL_UTC, label)
    try:
        parsed = datetime.strptime(result, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise HoldoutProtocolError(f"{label}_invalid") from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != result:
        raise HoldoutProtocolError(f"{label}_not_canonical")
    return result


def zero_failure_upper_bound(sample_count: int, alpha: float) -> float:
    """Return the zero-event binomial-form upper envelope ``1-alpha**(1/n)``."""

    if type(sample_count) is not int or sample_count <= 0:
        raise HoldoutProtocolError("sample_count_must_be_positive_exact_integer")
    if (
        type(alpha) is not float
        or not math.isfinite(alpha)
        or not 0.0 < alpha < 1.0
    ):
        raise HoldoutProtocolError("alpha_must_be_finite_exact_float_between_zero_and_one")
    return 1.0 - alpha ** (1.0 / sample_count)


def _no_authority_boundary(*, artifact_class: str) -> dict[str, Any]:
    return {
        "artifact_class": artifact_class,
        "external_provenance_verified": False,
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


def _validate_capability_boundary(value: Any, *, artifact_class: str) -> None:
    boundary = _exact_dict(value, _CAPABILITY_KEYS, "capability_boundary")
    observed_class = _require_exact_string(
        boundary["artifact_class"], "capability_boundary_artifact_class"
    )
    if observed_class != artifact_class:
        raise HoldoutProtocolError("capability_boundary_artifact_class_mismatch")
    for key in _CAPABILITY_KEYS - {"artifact_class"}:
        _require_exact_bool(boundary[key], False, f"capability_boundary_{key}")


def _validate_candidate_identity(value: Any) -> None:
    candidate = _exact_dict(value, _CANDIDATE_KEYS, "candidate_identity")
    _require_regex(candidate["candidate_commit"], _GIT_COMMIT, "candidate_commit")
    _require_exact_int(
        candidate["solver_count"], FROZEN_SOLVER_COUNT, "candidate_solver_count"
    )
    _require_regex(candidate["snapshot_id"], _SNAPSHOT_ID, "snapshot_id")
    for key in (
        "topology_digest",
        "projection_identity_set_digest",
        "embedding_contract_digest",
        "model_catalog_digest",
        "capture_tool_sha256",
        "score_tool_sha256",
    ):
        _require_regex(candidate[key], _FULL_SHA256, key)

    source_commits = candidate["source_projection_commit_ids"]
    expected_cells = frozenset(ALL_CELLS)
    if (
        type(source_commits) is not dict
        or any(type(key) is not str for key in source_commits)
        or frozenset(source_commits) != expected_cells
    ):
        raise HoldoutProtocolError("source_projection_commit_ids_cells_mismatch")
    for cell_id in sorted(expected_cells):
        _require_regex(
            source_commits[cell_id],
            _PROJECTION_COMMIT_ID,
            f"source_projection_commit_ids_{cell_id}",
        )

    faiss = _exact_dict(candidate["faiss_identity"], _FAISS_KEYS, "faiss_identity")
    _require_exact_string(faiss["faiss_version"], "faiss_version")
    _require_exact_string(faiss["faiss_compile_options"], "faiss_compile_options")
    _require_regex(
        faiss["faiss_binary_set_sha256"],
        _FULL_SHA256,
        "faiss_binary_set_sha256",
    )
    capture_path = _require_safe_repo_python_path(
        candidate["capture_tool_path"], "capture_tool_path"
    )
    score_path = _require_safe_repo_python_path(
        candidate["score_tool_path"], "score_tool_path"
    )
    if PureWindowsPath(capture_path) == PureWindowsPath(score_path):
        raise HoldoutProtocolError("capture_and_score_tools_must_be_distinct")
    if candidate["capture_tool_sha256"] == candidate["score_tool_sha256"]:
        raise HoldoutProtocolError("capture_and_score_tool_digests_must_be_distinct")
    if _require_exact_string(
        candidate["score_phase_dependency_policy"],
        "score_phase_dependency_policy",
    ) != "capture_artifact_and_labels_only_no_faiss_or_embedder":
        raise HoldoutProtocolError("score_phase_dependency_policy_mismatch")
    _require_exact_int(candidate["search_k"], FIXED_SEARCH_K, "search_k")
    search_algorithm = _require_exact_string(
        candidate["search_algorithm"], "search_algorithm"
    )
    if search_algorithm != "verified_snapshot_session_global_all_cells_k5":
        raise HoldoutProtocolError("search_algorithm_mismatch")
    score_semantics = _require_exact_string(
        candidate["score_value_semantics"], "score_value_semantics"
    )
    if score_semantics != "raw_unrounded_float32_inner_product":
        raise HoldoutProtocolError("score_value_semantics_mismatch")


def _validate_cutoff(value: Any, candidate_commit: str) -> None:
    cutoff = _exact_dict(value, _CUTOFF_KEYS, "cutoff")
    commit = _require_regex(cutoff["cutoff_commit"], _GIT_COMMIT, "cutoff_commit")
    if commit != candidate_commit:
        raise HoldoutProtocolError("cutoff_commit_must_equal_candidate_commit")
    _require_canonical_utc(cutoff["cutoff_utc"], "cutoff_utc")
    _require_exact_bool(
        cutoff["collection_after_cutoff_required"],
        True,
        "collection_after_cutoff_required",
    )


def _validate_sampling(value: Any, *, candidate_solver_count: int) -> None:
    sampling = _exact_dict(value, _SAMPLING_KEYS, "sampling")
    expected_scalars = {
        "sampling_unit": "unique_post_cutoff_intent_cluster",
        "selection_method": "stratified_uniform_without_replacement_after_frame_commit",
        "target_population": "frozen_balanced_frame_mixture_not_production_prevalence",
    }
    for key, expected in expected_scalars.items():
        if _require_exact_string(sampling[key], key) != expected:
            raise HoldoutProtocolError(f"{key}_mismatch")
    for key in (
        "translations_and_variants_share_cluster",
        "frame_commit_precedes_seed_reveal",
        "selected_intent_clusters_globally_unique",
        "no_translation_pairs_across_clusters",
    ):
        _require_exact_bool(sampling[key], True, key)
    _require_exact_int(
        sampling["selection_attempt_count"],
        1,
        "selection_attempt_count",
    )
    _require_exact_bool(
        sampling["selection_reseed_allowed"],
        False,
        "selection_reseed_allowed",
    )
    _require_exact_bool(
        sampling["post_selection_replacement_allowed"],
        False,
        "post_selection_replacement_allowed",
    )
    _require_exact_int(sampling["minimum_frame_multiplier"], 2, "minimum_frame_multiplier")
    _require_exact_int(
        sampling["frozen_solver_count"],
        FROZEN_SOLVER_COUNT,
        "frozen_solver_count",
    )
    _require_exact_int(
        sampling["positive_cluster_count"],
        POSITIVE_CLUSTER_COUNT,
        "positive_cluster_count",
    )
    _require_exact_int(
        sampling["ood_cluster_count"], OOD_CLUSTER_COUNT, "ood_cluster_count"
    )
    _require_exact_int(
        sampling["minimum_per_critical_class"],
        MINIMUM_PER_CRITICAL_CLASS,
        "minimum_per_critical_class",
    )
    _require_exact_string_list(sampling["positive_strata"], POSITIVE_STRATA, "positive_strata")
    _require_exact_int(
        sampling["positive_per_stratum"],
        FROZEN_SOLVER_COUNT,
        "positive_per_stratum",
    )
    if _require_exact_string(
        sampling["positive_solver_coverage_policy"],
        "positive_solver_coverage_policy",
    ) != "exactly_one_per_frozen_solver_per_positive_stratum":
        raise HoldoutProtocolError("positive_solver_coverage_policy_mismatch")
    if _require_exact_string(
        sampling["positive_solver_identity_binding"],
        "positive_solver_identity_binding",
    ) != "candidate_projection_identity_set_digest":
        raise HoldoutProtocolError("positive_solver_identity_binding_mismatch")
    _require_exact_string_list(sampling["ood_strata"], OOD_STRATA, "ood_strata")
    _require_exact_int(sampling["ood_per_stratum"], 25, "ood_per_stratum")
    language_counts = _exact_dict(
        sampling["ood_language_counts_per_stratum"],
        frozenset({"fi", "en"}),
        "ood_language_counts_per_stratum",
    )
    _require_exact_int(language_counts["fi"], 12, "ood_fi_per_stratum")
    _require_exact_int(language_counts["en"], 13, "ood_en_per_stratum")
    if (
        candidate_solver_count != sampling["frozen_solver_count"]
        or sampling["positive_per_stratum"] != sampling["frozen_solver_count"]
        or len(POSITIVE_STRATA) * sampling["positive_per_stratum"]
        != sampling["positive_cluster_count"]
        or len(OOD_STRATA) * sampling["ood_per_stratum"]
        != sampling["ood_cluster_count"]
        or sum(language_counts.values()) != sampling["ood_per_stratum"]
        or sampling["positive_per_stratum"] < MINIMUM_PER_CRITICAL_CLASS
        or sampling["ood_per_stratum"] < MINIMUM_PER_CRITICAL_CLASS
    ):
        raise HoldoutProtocolError("sampling_count_derivation_mismatch")


def _validate_endpoint_gate(value: Any) -> None:
    gate = _exact_dict(value, _ENDPOINT_KEYS, "endpoint_gate")
    _require_exact_float(gate["threshold"], FIXED_THRESHOLD, "threshold")
    if (
        _require_exact_string(gate["comparison"], "comparison")
        != "top_score_greater_than_or_equal"
    ):
        raise HoldoutProtocolError("comparison_mismatch")
    _require_exact_bool(
        gate["threshold_override_allowed"], False, "threshold_override_allowed"
    )
    _require_exact_bool(
        gate["threshold_sweep_allowed"], False, "threshold_sweep_allowed"
    )
    if _require_exact_string(
        gate["positive_error_predicate"], "positive_error_predicate"
    ) != (
        "top1_solver_wrong_or_top1_cell_wrong_or_top_score_below_threshold"
    ):
        raise HoldoutProtocolError("positive_error_predicate_mismatch")
    if _require_exact_string(
        gate["ood_error_predicate"], "ood_error_predicate"
    ) != "top_score_greater_than_or_equal_threshold":
        raise HoldoutProtocolError("ood_error_predicate_mismatch")
    _require_exact_bool(gate["zero_failures_required"], True, "zero_failures_required")
    _require_exact_float(
        gate["adjudication_agreement_required"],
        1.0,
        "adjudication_agreement_required",
    )


def _validate_statistics(value: Any) -> None:
    statistics = _exact_dict(value, _STATISTICS_KEYS, "statistics")
    if (
        _require_exact_string(statistics["method"], "statistics_method")
        != "stratified_srswor_zero_failure_binomial_envelope_bonferroni"
    ):
        raise HoldoutProtocolError("statistics_method_mismatch")
    expected_strings = {
        "finite_frame_estimand": (
            "selected_quota_weighted_design_cell_error_rate"
        ),
        "sampling_model": (
            "independent_stratified_simple_random_sampling_without_replacement"
        ),
        "bound_semantics": (
            "conservative_binomial_form_envelope_via_weighted_am_gm"
        ),
    }
    for key, expected in expected_strings.items():
        if _require_exact_string(statistics[key], key) != expected:
            raise HoldoutProtocolError(f"{key}_mismatch")
    _require_exact_bool(
        statistics["iid_clopper_pearson_claimed"],
        False,
        "iid_clopper_pearson_claimed",
    )
    _require_exact_float(
        statistics["familywise_confidence"],
        FAMILYWISE_CONFIDENCE,
        "familywise_confidence",
    )
    _require_exact_float(
        statistics["component_alpha"], COMPONENT_ALPHA, "component_alpha"
    )
    _require_exact_float(
        statistics["maximum_error_rate"],
        MAXIMUM_ERROR_RATE,
        "maximum_error_rate",
    )
    _require_exact_bool(
        statistics["simultaneous_component_bounds_required"],
        True,
        "simultaneous_component_bounds_required",
    )
    positive_bound = zero_failure_upper_bound(POSITIVE_CLUSTER_COUNT, COMPONENT_ALPHA)
    ood_bound = zero_failure_upper_bound(OOD_CLUSTER_COUNT, COMPONENT_ALPHA)
    if max(positive_bound, ood_bound) > MAXIMUM_ERROR_RATE:
        raise HoldoutProtocolError("preregistered_cluster_counts_miss_error_bound")


def _validate_role_separation(value: Any) -> None:
    roles = _exact_dict(value, _ROLE_KEYS, "role_separation")
    expected = {
        "candidate_owner_authors_cases": False,
        "candidate_owner_adjudicates": False,
        "case_authors_see_scores": False,
        "adjudicators_see_scores": False,
        "adjudicators_are_independent": True,
        "custodian_external_to_candidate_owner": True,
        "positive_and_ood_case_authors_disjoint": True,
        "plaintext_pack_in_repository": False,
    }
    for key, expected_value in expected.items():
        _require_exact_bool(roles[key], expected_value, key)


def validate_preregistration(value: Any) -> dict[str, Any]:
    """Validate and return an isolated canonical clone of a preregistration."""

    protocol = _exact_dict(value, _PROTOCOL_KEYS, "protocol")
    if (
        _require_exact_string(protocol["schema_version"], "protocol_schema_version")
        != PROTOCOL_SCHEMA
    ):
        raise HoldoutProtocolError("protocol_schema_mismatch")
    _require_regex(protocol["protocol_id"], _PROTOCOL_ID, "protocol_id")
    _validate_candidate_identity(protocol["candidate_identity"])
    _validate_cutoff(
        protocol["cutoff"], protocol["candidate_identity"]["candidate_commit"]
    )
    _validate_sampling(
        protocol["sampling"],
        candidate_solver_count=protocol["candidate_identity"]["solver_count"],
    )
    _validate_endpoint_gate(protocol["endpoint_gate"])
    _validate_statistics(protocol["statistics"])
    _validate_role_separation(protocol["role_separation"])
    _validate_capability_boundary(
        protocol["capability_boundary"], artifact_class="protocol_substrate_only"
    )
    try:
        canonical = canonical_json_bytes(protocol)
    except (TypeError, ValueError, OverflowError) as exc:
        raise HoldoutProtocolError("protocol_is_not_strict_json") from exc
    return json.loads(canonical)


def preregistration_digest(value: Any) -> str:
    """Return the canonical digest of a valid preregistration."""

    return sha256_digest(validate_preregistration(value))


def statistics_projection() -> dict[str, Any]:
    """Return the preregistered zero-failure bounds without granting a gate."""

    positive_bound = zero_failure_upper_bound(
        POSITIVE_CLUSTER_COUNT, COMPONENT_ALPHA
    )
    ood_bound = zero_failure_upper_bound(OOD_CLUSTER_COUNT, COMPONENT_ALPHA)
    return {
        "method": (
            "stratified_srswor_zero_failure_binomial_envelope_bonferroni"
        ),
        "finite_frame_estimand": (
            "selected_quota_weighted_design_cell_error_rate"
        ),
        "iid_clopper_pearson_claimed": False,
        "positive_cluster_count": POSITIVE_CLUSTER_COUNT,
        "ood_cluster_count": OOD_CLUSTER_COUNT,
        "component_alpha": COMPONENT_ALPHA,
        "positive_zero_failure_upper_bound": positive_bound,
        "ood_zero_failure_upper_bound": ood_bound,
        "maximum_error_rate": MAXIMUM_ERROR_RATE,
        "structural_count_contract_meets_bound": (
            max(positive_bound, ood_bound) <= MAXIMUM_ERROR_RATE
        ),
        "observed_holdout_result_available": False,
        "holdout_evidence_gate_met": False,
    }


def _validate_json_native(
    value: Any,
    *,
    label: str,
    depth: int = 0,
    active_containers: set[int] | None = None,
) -> None:
    if depth > MAX_JSON_DEPTH:
        raise HoldoutProtocolError(f"{label}_exceeds_maximum_json_depth")
    value_type = type(value)
    if value is None or value_type in {bool, int, str}:
        return
    if value_type is float:
        if not math.isfinite(value):
            raise HoldoutProtocolError(f"{label}_contains_nonfinite_float")
        return
    if value_type not in {list, dict}:
        raise HoldoutProtocolError(f"{label}_contains_non_json_native_value")

    if active_containers is None:
        active_containers = set()
    identity = id(value)
    if identity in active_containers:
        raise HoldoutProtocolError(f"{label}_contains_cycle")
    active_containers.add(identity)
    try:
        if value_type is list:
            for index, item in enumerate(value):
                _validate_json_native(
                    item,
                    label=f"{label}_{index}",
                    depth=depth + 1,
                    active_containers=active_containers,
                )
        else:
            if any(type(key) is not str for key in value):
                raise HoldoutProtocolError(f"{label}_contains_non_string_key")
            for key, item in value.items():
                _validate_json_native(
                    item,
                    label=f"{label}_{key}",
                    depth=depth + 1,
                    active_containers=active_containers,
                )
    finally:
        active_containers.remove(identity)


def create_hmac_commitment(
    domain: str,
    *,
    key: bytes,
    protocol_digest: str,
    payload: Any,
) -> str:
    """Commit external canonical JSON with a protocol-bound 256-bit HMAC key."""

    if type(domain) is not str or domain not in COMMITMENT_DOMAINS:
        raise HoldoutProtocolError("commitment_domain_invalid")
    if type(key) is not bytes or len(key) != 32:
        raise HoldoutProtocolError("commitment_key_must_be_exactly_32_bytes")
    _require_regex(protocol_digest, _FULL_SHA256, "protocol_digest")
    _validate_json_native(payload, label="commitment_payload")
    try:
        payload_bytes = canonical_json_bytes(payload)
    except (TypeError, ValueError, OverflowError) as exc:
        raise HoldoutProtocolError("commitment_payload_is_not_strict_json") from exc
    if len(payload_bytes) > MAX_COMMITMENT_PAYLOAD_BYTES:
        raise HoldoutProtocolError("commitment_payload_too_large")
    message = b"\0".join(
        (
            COMMITMENT_CONTEXT,
            domain.encode("ascii"),
            protocol_digest.encode("ascii"),
            payload_bytes,
        )
    )
    return f"{COMMITMENT_SCHEME}:" + hmac.new(
        key,
        message,
        hashlib.sha256,
    ).hexdigest()


def verify_hmac_commitment(
    expected_commitment: str,
    domain: str,
    *,
    key: bytes,
    protocol_digest: str,
    payload: Any,
) -> bool:
    """Return whether a well-formed commitment matches the supplied plaintext."""

    _require_regex(expected_commitment, _HMAC_SHA256, "expected_commitment")
    observed = create_hmac_commitment(
        domain,
        key=key,
        protocol_digest=protocol_digest,
        payload=payload,
    )
    return hmac.compare_digest(expected_commitment, observed)


def _validate_evidence(value: Any, *, expected_kind: str) -> None:
    evidence = _exact_dict(value, _EVIDENCE_KEYS, "state_evidence")
    if _require_exact_string(evidence["kind"], "state_evidence_kind") != expected_kind:
        raise HoldoutProtocolError("state_evidence_kind_mismatch")
    _require_regex(evidence["declared_commit"], _GIT_COMMIT, "declared_commit")
    _require_regex(evidence["artifact_digest"], _FULL_SHA256, "artifact_digest")


def validate_state(value: Any, *, protocol: Any) -> dict[str, Any]:
    """Validate a state against its frozen preregistration and clone it."""

    validated_protocol = validate_preregistration(protocol)
    expected_protocol_digest = sha256_digest(validated_protocol)
    expected_candidate_commit = validated_protocol["candidate_identity"][
        "candidate_commit"
    ]
    state = _exact_dict(value, _STATE_KEYS, "state")
    if (
        _require_exact_string(state["schema_version"], "state_schema_version")
        != STATE_SCHEMA
    ):
        raise HoldoutProtocolError("state_schema_mismatch")
    state_protocol_digest = _require_regex(
        state["protocol_digest"], _FULL_SHA256, "protocol_digest"
    )
    if state_protocol_digest != expected_protocol_digest:
        raise HoldoutProtocolError("state_protocol_digest_mismatch")
    candidate_commit = _require_regex(
        state["candidate_commit"], _GIT_COMMIT, "state_candidate_commit"
    )
    if candidate_commit != expected_candidate_commit:
        raise HoldoutProtocolError("state_candidate_commit_mismatch")
    if type(state["stage"]) is not str or state["stage"] not in STAGES:
        raise HoldoutProtocolError("state_stage_invalid")
    expected_sequence = STAGES.index(state["stage"])
    _require_exact_int(state["sequence"], expected_sequence, "state_sequence")
    if expected_sequence == 0:
        if state["previous_state_digest"] is not None:
            raise HoldoutProtocolError("initial_previous_state_digest_must_be_null")
    else:
        _require_regex(
            state["previous_state_digest"],
            _FULL_SHA256,
            "previous_state_digest",
        )
    _validate_evidence(
        state["evidence"], expected_kind=_EVIDENCE_KIND_BY_STAGE[state["stage"]]
    )
    chain = state["declared_commit_chain"]
    if (
        type(chain) is not list
        or len(chain) != expected_sequence + 1
        or any(type(commit) is not str or _GIT_COMMIT.fullmatch(commit) is None for commit in chain)
        or len(set(chain)) != len(chain)
        or candidate_commit in chain
        or chain[-1] != state["evidence"]["declared_commit"]
    ):
        raise HoldoutProtocolError("declared_commit_chain_invalid")
    _validate_capability_boundary(
        state["capability_boundary"], artifact_class="structural_state_projection_only"
    )
    try:
        return json.loads(canonical_json_bytes(state))
    except (TypeError, ValueError, OverflowError) as exc:
        raise HoldoutProtocolError("state_is_not_strict_json") from exc


def state_digest(value: Any, *, protocol: Any) -> str:
    """Digest a state only after binding it to its frozen preregistration."""

    return sha256_digest(validate_state(value, protocol=protocol))


def initialize_state(
    protocol: Any,
    *,
    declared_preregistration_commit: str,
) -> dict[str, Any]:
    """Create the non-authoritative initial structural state projection."""

    validated_protocol = validate_preregistration(protocol)
    protocol_digest = sha256_digest(validated_protocol)
    commit = _require_regex(
        declared_preregistration_commit,
        _GIT_COMMIT,
        "declared_preregistration_commit",
    )
    if commit == validated_protocol["candidate_identity"]["candidate_commit"]:
        raise HoldoutProtocolError(
            "preregistration_requires_distinct_declared_post_candidate_commit"
        )
    return validate_state(
        {
            "schema_version": STATE_SCHEMA,
            "protocol_digest": protocol_digest,
            "candidate_commit": validated_protocol["candidate_identity"][
                "candidate_commit"
            ],
            "stage": "preregistered",
            "sequence": 0,
            "previous_state_digest": None,
            "evidence": {
                "kind": "preregistration",
                "declared_commit": commit,
                "artifact_digest": protocol_digest,
            },
            "declared_commit_chain": [commit],
            "capability_boundary": _no_authority_boundary(
                artifact_class="structural_state_projection_only"
            ),
        },
        protocol=validated_protocol,
    )


def advance_state(
    current_state: Any,
    transition: Any,
    *,
    protocol: Any,
) -> dict[str, Any]:
    """Project one declared transition; no external ordering is asserted."""

    validated_protocol = validate_preregistration(protocol)
    current = validate_state(current_state, protocol=validated_protocol)
    declaration = _exact_dict(transition, _TRANSITION_KEYS, "transition")
    if (
        _require_exact_string(
            declaration["schema_version"], "transition_schema_version"
        )
        != TRANSITION_SCHEMA
    ):
        raise HoldoutProtocolError("transition_schema_mismatch")
    transition_protocol_digest = _require_regex(
        declaration["protocol_digest"], _FULL_SHA256, "transition_protocol_digest"
    )
    if transition_protocol_digest != current["protocol_digest"]:
        raise HoldoutProtocolError("transition_protocol_digest_mismatch")
    from_stage = _require_exact_string(declaration["from_stage"], "from_stage")
    if from_stage != current["stage"]:
        raise HoldoutProtocolError("transition_from_stage_mismatch")
    if current["stage"] == STAGES[-1]:
        raise HoldoutProtocolError("scored_state_is_terminal")
    next_stage = STAGES[current["sequence"] + 1]
    to_stage = _require_exact_string(declaration["to_stage"], "to_stage")
    if to_stage != next_stage:
        raise HoldoutProtocolError("transition_must_follow_exact_stage_order")
    _require_exact_int(
        declaration["sequence"], current["sequence"] + 1, "transition_sequence"
    )
    transition_previous_digest = _require_regex(
        declaration["previous_state_digest"],
        _FULL_SHA256,
        "transition_previous_state_digest",
    )
    if transition_previous_digest != state_digest(
        current,
        protocol=validated_protocol,
    ):
        raise HoldoutProtocolError("transition_previous_state_digest_mismatch")
    expected_kind = _EVIDENCE_KIND_BY_STAGE[next_stage]
    evidence_kind = _require_exact_string(
        declaration["evidence_kind"], "transition_evidence_kind"
    )
    if evidence_kind != expected_kind:
        raise HoldoutProtocolError("transition_evidence_kind_mismatch")
    commit = _require_regex(
        declaration["declared_commit"], _GIT_COMMIT, "declared_commit"
    )
    if commit in current["declared_commit_chain"]:
        raise HoldoutProtocolError("each_stage_requires_distinct_declared_commit")
    if commit == current["candidate_commit"]:
        raise HoldoutProtocolError("stage_commit_cannot_reuse_candidate_commit")
    artifact_digest = _require_regex(
        declaration["artifact_digest"], _FULL_SHA256, "artifact_digest"
    )
    return validate_state(
        {
            "schema_version": STATE_SCHEMA,
            "protocol_digest": current["protocol_digest"],
            "candidate_commit": current["candidate_commit"],
            "stage": next_stage,
            "sequence": current["sequence"] + 1,
            "previous_state_digest": state_digest(
                current,
                protocol=validated_protocol,
            ),
            "evidence": {
                "kind": expected_kind,
                "declared_commit": commit,
                "artifact_digest": artifact_digest,
            },
            "declared_commit_chain": [*current["declared_commit_chain"], commit],
            "capability_boundary": _no_authority_boundary(
                artifact_class="structural_state_projection_only"
            ),
        },
        protocol=validated_protocol,
    )


__all__ = [
    "COMMITMENT_DOMAINS",
    "COMPONENT_ALPHA",
    "FAMILYWISE_CONFIDENCE",
    "FIXED_SEARCH_K",
    "FIXED_THRESHOLD",
    "HoldoutProtocolError",
    "MAXIMUM_ERROR_RATE",
    "OOD_CLUSTER_COUNT",
    "OOD_STRATA",
    "POSITIVE_CLUSTER_COUNT",
    "POSITIVE_STRATA",
    "PROTOCOL_SCHEMA",
    "STAGES",
    "STATE_SCHEMA",
    "TRANSITION_SCHEMA",
    "advance_state",
    "create_hmac_commitment",
    "initialize_state",
    "preregistration_digest",
    "state_digest",
    "statistics_projection",
    "validate_preregistration",
    "validate_state",
    "verify_hmac_commitment",
    "zero_failure_upper_bound",
]
