from __future__ import annotations

import copy
import math
from typing import Any, Callable

import pytest

from tools import magma_faiss_holdout_protocol as holdout
from waggledance.core.hex_cell_topology import ALL_CELLS


class _StringSubclass(str):
    pass


class _DictSubclass(dict):
    pass


def _digest(character: str) -> str:
    return "sha256:" + character * 64


def _no_authority_boundary(artifact_class: str) -> dict[str, Any]:
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


def _protocol() -> dict[str, Any]:
    candidate_commit = "a" * 40
    return {
        "schema_version": holdout.PROTOCOL_SCHEMA,
        "protocol_id": "holdoutproto_" + "1" * 32,
        "candidate_identity": {
            "candidate_commit": candidate_commit,
            "solver_count": 22,
            "snapshot_id": "faisscand_" + "2" * 64,
            "topology_digest": _digest("3"),
            "projection_identity_set_digest": _digest("4"),
            "embedding_contract_digest": _digest("5"),
            "model_catalog_digest": _digest("6"),
            "source_projection_commit_ids": {
                cell_id: "proj_" + f"{index + 1:064x}"
                for index, cell_id in enumerate(ALL_CELLS)
            },
            "faiss_identity": {
                "faiss_version": "1.13.2",
                "faiss_compile_options": "AVX2",
                "faiss_binary_set_sha256": _digest("7"),
            },
            "capture_tool_path": "tools/capture_magma_faiss_candidate_holdout.py",
            "capture_tool_sha256": _digest("8"),
            "score_tool_path": "tools/score_magma_faiss_candidate_holdout.py",
            "score_tool_sha256": _digest("9"),
            "score_phase_dependency_policy": (
                "capture_artifact_and_labels_only_no_faiss_or_embedder"
            ),
            "search_k": 5,
            "search_algorithm": "verified_snapshot_session_global_all_cells_k5",
            "score_value_semantics": "raw_unrounded_float32_inner_product",
        },
        "cutoff": {
            "cutoff_commit": candidate_commit,
            "cutoff_utc": "2026-08-12T12:00:00Z",
            "collection_after_cutoff_required": True,
        },
        "sampling": {
            "sampling_unit": "unique_post_cutoff_intent_cluster",
            "translations_and_variants_share_cluster": True,
            "selection_method": (
                "stratified_uniform_without_replacement_after_frame_commit"
            ),
            "frame_commit_precedes_seed_reveal": True,
            "selection_attempt_count": 1,
            "selection_reseed_allowed": False,
            "post_selection_replacement_allowed": False,
            "minimum_frame_multiplier": 2,
            "frozen_solver_count": 22,
            "positive_cluster_count": 132,
            "ood_cluster_count": 125,
            "minimum_per_critical_class": 20,
            "positive_strata": list(holdout.POSITIVE_STRATA),
            "positive_per_stratum": 22,
            "positive_solver_coverage_policy": (
                "exactly_one_per_frozen_solver_per_positive_stratum"
            ),
            "positive_solver_identity_binding": (
                "candidate_projection_identity_set_digest"
            ),
            "ood_strata": list(holdout.OOD_STRATA),
            "ood_per_stratum": 25,
            "ood_language_counts_per_stratum": {"fi": 12, "en": 13},
            "selected_intent_clusters_globally_unique": True,
            "no_translation_pairs_across_clusters": True,
            "target_population": (
                "frozen_balanced_frame_mixture_not_production_prevalence"
            ),
        },
        "endpoint_gate": {
            "threshold": 0.60,
            "comparison": "top_score_greater_than_or_equal",
            "threshold_override_allowed": False,
            "threshold_sweep_allowed": False,
            "positive_error_predicate": (
                "top1_solver_wrong_or_top1_cell_wrong_or_top_score_below_threshold"
            ),
            "ood_error_predicate": "top_score_greater_than_or_equal_threshold",
            "zero_failures_required": True,
            "adjudication_agreement_required": 1.0,
        },
        "statistics": {
            "method": (
                "stratified_srswor_zero_failure_binomial_envelope_bonferroni"
            ),
            "finite_frame_estimand": (
                "selected_quota_weighted_design_cell_error_rate"
            ),
            "sampling_model": (
                "independent_stratified_simple_random_sampling_without_replacement"
            ),
            "bound_semantics": (
                "conservative_binomial_form_envelope_via_weighted_am_gm"
            ),
            "iid_clopper_pearson_claimed": False,
            "familywise_confidence": 0.95,
            "component_alpha": 0.025,
            "maximum_error_rate": 0.03,
            "simultaneous_component_bounds_required": True,
        },
        "role_separation": {
            "candidate_owner_authors_cases": False,
            "candidate_owner_adjudicates": False,
            "case_authors_see_scores": False,
            "adjudicators_see_scores": False,
            "adjudicators_are_independent": True,
            "custodian_external_to_candidate_owner": True,
            "positive_and_ood_case_authors_disjoint": True,
            "plaintext_pack_in_repository": False,
        },
        "capability_boundary": _no_authority_boundary(
            "protocol_substrate_only"
        ),
    }


def _mutate(
    value: dict[str, Any],
    path: tuple[str, ...],
    replacement: Any,
) -> dict[str, Any]:
    result = copy.deepcopy(value)
    target: dict[str, Any] = result
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = replacement
    return result


def test_preregistration_is_closed_world_and_binds_conservative_counts() -> None:
    source = _protocol()

    validated = holdout.validate_preregistration(source)
    digest = holdout.preregistration_digest(source)
    projection = holdout.statistics_projection()

    assert validated == source
    assert validated is not source
    assert validated["candidate_identity"] is not source["candidate_identity"]
    assert digest.startswith("sha256:") and len(digest) == 71
    assert projection["positive_cluster_count"] == 132
    assert projection["ood_cluster_count"] == 125
    assert projection["iid_clopper_pearson_claimed"] is False
    assert validated["candidate_identity"]["solver_count"] == 22
    assert validated["sampling"]["positive_per_stratum"] == 22
    assert validated["sampling"]["ood_language_counts_per_stratum"] == {
        "fi": 12,
        "en": 13,
    }
    assert projection["positive_zero_failure_upper_bound"] == pytest.approx(
        1.0 - 0.025 ** (1.0 / 132)
    )
    assert projection["ood_zero_failure_upper_bound"] == pytest.approx(
        1.0 - 0.025 ** (1.0 / 125)
    )
    assert projection["positive_zero_failure_upper_bound"] < 0.03
    assert projection["ood_zero_failure_upper_bound"] < 0.03
    assert projection["structural_count_contract_meets_bound"] is True
    assert projection["observed_holdout_result_available"] is False
    assert projection["holdout_evidence_gate_met"] is False


@pytest.mark.parametrize(
    ("label", "mutator"),
    [
        ("top_extra", lambda value: {**value, "extra": False}),
        (
            "candidate_extra",
            lambda value: {
                **value,
                "candidate_identity": {
                    **value["candidate_identity"],
                    "extra": False,
                },
            },
        ),
        (
            "schema_subclass",
            lambda value: _mutate(
                value,
                ("schema_version",),
                _StringSubclass(holdout.PROTOCOL_SCHEMA),
            ),
        ),
        (
            "search_algorithm_subclass",
            lambda value: _mutate(
                value,
                ("candidate_identity", "search_algorithm"),
                _StringSubclass(
                    "verified_snapshot_session_global_all_cells_k5"
                ),
            ),
        ),
        (
            "faiss_version_subclass",
            lambda value: _mutate(
                value,
                ("candidate_identity", "faiss_identity", "faiss_version"),
                _StringSubclass("1.13.2"),
            ),
        ),
        (
            "threshold_int",
            lambda value: _mutate(value, ("endpoint_gate", "threshold"), 0),
        ),
        (
            "threshold_bool",
            lambda value: _mutate(value, ("endpoint_gate", "threshold"), True),
        ),
        (
            "threshold_nan",
            lambda value: _mutate(
                value, ("endpoint_gate", "threshold"), float("nan")
            ),
        ),
        (
            "positive_count_bool",
            lambda value: _mutate(
                value, ("sampling", "positive_cluster_count"), True
            ),
        ),
        (
            "selection_attempt_count_wrong",
            lambda value: _mutate(
                value, ("sampling", "selection_attempt_count"), 2
            ),
        ),
        (
            "selection_attempt_count_bool",
            lambda value: _mutate(
                value, ("sampling", "selection_attempt_count"), True
            ),
        ),
        (
            "selection_reseed_allowed",
            lambda value: _mutate(
                value, ("sampling", "selection_reseed_allowed"), True
            ),
        ),
        (
            "post_selection_replacement_allowed",
            lambda value: _mutate(
                value,
                ("sampling", "post_selection_replacement_allowed"),
                True,
            ),
        ),
        (
            "positive_count_wrong",
            lambda value: _mutate(
                value, ("sampling", "positive_cluster_count"), 131
            ),
        ),
        (
            "candidate_solver_count_wrong",
            lambda value: _mutate(
                value, ("candidate_identity", "solver_count"), 21
            ),
        ),
        (
            "candidate_solver_count_bool",
            lambda value: _mutate(
                value, ("candidate_identity", "solver_count"), True
            ),
        ),
        (
            "sampling_solver_count_wrong",
            lambda value: _mutate(
                value, ("sampling", "frozen_solver_count"), 21
            ),
        ),
        (
            "positive_strata_reordered",
            lambda value: _mutate(
                value,
                ("sampling", "positive_strata"),
                list(reversed(holdout.POSITIVE_STRATA)),
            ),
        ),
        (
            "positive_solver_coverage_drift",
            lambda value: _mutate(
                value,
                ("sampling", "positive_solver_coverage_policy"),
                "at_least_one_solver_per_stratum",
            ),
        ),
        (
            "positive_solver_binding_drift",
            lambda value: _mutate(
                value,
                ("sampling", "positive_solver_identity_binding"),
                "model_catalog_only",
            ),
        ),
        (
            "selected_clusters_not_globally_unique",
            lambda value: _mutate(
                value,
                ("sampling", "selected_intent_clusters_globally_unique"),
                False,
            ),
        ),
        (
            "ood_language_counts_swapped",
            lambda value: _mutate(
                value,
                ("sampling", "ood_language_counts_per_stratum"),
                {"fi": 13, "en": 12},
            ),
        ),
        (
            "ood_language_counts_extra",
            lambda value: _mutate(
                value,
                ("sampling", "ood_language_counts_per_stratum"),
                {"fi": 12, "en": 13, "sv": 0},
            ),
        ),
        (
            "translation_policy_false",
            lambda value: _mutate(
                value,
                ("sampling", "translations_and_variants_share_cluster"),
                False,
            ),
        ),
        (
            "threshold_override_true",
            lambda value: _mutate(
                value, ("endpoint_gate", "threshold_override_allowed"), True
            ),
        ),
        (
            "candidate_owner_authors",
            lambda value: _mutate(
                value,
                ("role_separation", "candidate_owner_authors_cases"),
                True,
            ),
        ),
        (
            "adjudicator_not_independent",
            lambda value: _mutate(
                value,
                ("role_separation", "adjudicators_are_independent"),
                False,
            ),
        ),
        (
            "positive_and_ood_authors_overlap",
            lambda value: _mutate(
                value,
                ("role_separation", "positive_and_ood_case_authors_disjoint"),
                False,
            ),
        ),
        (
            "authority_smuggling",
            lambda value: _mutate(
                value,
                ("capability_boundary", "runtime_authority_granted"),
                True,
            ),
        ),
        (
            "authority_nonbool_smuggling",
            lambda value: _mutate(
                value,
                ("capability_boundary", "production_promotion_gate_pass"),
                0,
            ),
        ),
        (
            "artifact_class_subclass",
            lambda value: _mutate(
                value,
                ("capability_boundary", "artifact_class"),
                _StringSubclass("protocol_substrate_only"),
            ),
        ),
        (
            "cutoff_commit_drift",
            lambda value: _mutate(
                value, ("cutoff", "cutoff_commit"), "b" * 40
            ),
        ),
        (
            "noncanonical_cutoff",
            lambda value: _mutate(
                value, ("cutoff", "cutoff_utc"), "2026-08-12T12:00:00+00:00"
            ),
        ),
        (
            "path_escape",
            lambda value: _mutate(
                value,
                ("candidate_identity", "capture_tool_path"),
                "../capture.py",
            ),
        ),
        (
            "windows_path",
            lambda value: _mutate(
                value,
                ("candidate_identity", "capture_tool_path"),
                "tools\\capture.py",
            ),
        ),
        (
            "windows_drive_absolute_capture",
            lambda value: _mutate(
                value,
                ("candidate_identity", "capture_tool_path"),
                "C:/outside.py",
            ),
        ),
        (
            "windows_drive_relative_score",
            lambda value: _mutate(
                value,
                ("candidate_identity", "score_tool_path"),
                "C:outside.py",
            ),
        ),
        (
            "ntfs_ads_capture",
            lambda value: _mutate(
                value,
                ("candidate_identity", "capture_tool_path"),
                "tools/capture.py:alternate.py",
            ),
        ),
        (
            "control_character_score",
            lambda value: _mutate(
                value,
                ("candidate_identity", "score_tool_path"),
                "tools/score\x01.py",
            ),
        ),
        (
            "noncanonical_dot_capture",
            lambda value: _mutate(
                value,
                ("candidate_identity", "capture_tool_path"),
                "tools/./capture.py",
            ),
        ),
        (
            "noncanonical_double_slash_score",
            lambda value: _mutate(
                value,
                ("candidate_identity", "score_tool_path"),
                "tools//score.py",
            ),
        ),
        (
            "windows_reserved_capture",
            lambda value: _mutate(
                value,
                ("candidate_identity", "capture_tool_path"),
                "tools/CON.py",
            ),
        ),
        (
            "windows_reserved_score",
            lambda value: _mutate(
                value,
                ("candidate_identity", "score_tool_path"),
                "tools/lpt1.py",
            ),
        ),
        (
            "windows_superscript_reserved_capture",
            lambda value: _mutate(
                value,
                ("candidate_identity", "capture_tool_path"),
                "tools/COM¹.py",
            ),
        ),
        (
            "windows_console_reserved_score",
            lambda value: _mutate(
                value,
                ("candidate_identity", "score_tool_path"),
                "tools/CONOUT$.py",
            ),
        ),
        (
            "windows_trimmed_reserved_capture",
            lambda value: _mutate(
                value,
                ("candidate_identity", "capture_tool_path"),
                "tools/NUL .py",
            ),
        ),
        (
            "windows_trimmed_reserved_score",
            lambda value: _mutate(
                value,
                ("candidate_identity", "score_tool_path"),
                "tools/COM1 .x.py",
            ),
        ),
        (
            "git_metadata_capture",
            lambda value: _mutate(
                value,
                ("candidate_identity", "capture_tool_path"),
                ".git/hooks/capture.py",
            ),
        ),
        (
            "nested_git_metadata_score",
            lambda value: _mutate(
                value,
                ("candidate_identity", "score_tool_path"),
                "tools/.GIT/score.py",
            ),
        ),
        (
            "windows_wildcard_capture",
            lambda value: _mutate(
                value,
                ("candidate_identity", "capture_tool_path"),
                "tools/cap*.py",
            ),
        ),
        (
            "windows_question_mark_score",
            lambda value: _mutate(
                value,
                ("candidate_identity", "score_tool_path"),
                "tools/score?.py",
            ),
        ),
        (
            "windows_pipe_capture",
            lambda value: _mutate(
                value,
                ("candidate_identity", "capture_tool_path"),
                "tools/cap|x.py",
            ),
        ),
        (
            "windows_angle_bracket_score",
            lambda value: _mutate(
                value,
                ("candidate_identity", "score_tool_path"),
                "tools/cap<x>.py",
            ),
        ),
        (
            "windows_quote_capture",
            lambda value: _mutate(
                value,
                ("candidate_identity", "capture_tool_path"),
                'tools/cap"x.py',
            ),
        ),
        (
            "same_capture_and_score_path",
            lambda value: _mutate(
                value,
                ("candidate_identity", "score_tool_path"),
                value["candidate_identity"]["capture_tool_path"],
            ),
        ),
        (
            "windows_case_alias_capture_and_score_path",
            lambda value: _mutate(
                _mutate(
                    value,
                    ("candidate_identity", "capture_tool_path"),
                    "tools/Holdout.py",
                ),
                ("candidate_identity", "score_tool_path"),
                "tools/holdout.py",
            ),
        ),
        (
            "same_capture_and_score_digest",
            lambda value: _mutate(
                value,
                ("candidate_identity", "score_tool_sha256"),
                value["candidate_identity"]["capture_tool_sha256"],
            ),
        ),
        (
            "score_dependency_policy_drift",
            lambda value: _mutate(
                value,
                ("candidate_identity", "score_phase_dependency_policy"),
                "may_open_faiss",
            ),
        ),
        (
            "search_k_bool",
            lambda value: _mutate(
                value, ("candidate_identity", "search_k"), True
            ),
        ),
        (
            "statistics_alpha_drift",
            lambda value: _mutate(
                value, ("statistics", "component_alpha"), 0.05
            ),
        ),
        (
            "statistics_iid_overclaim",
            lambda value: _mutate(
                value,
                ("statistics", "iid_clopper_pearson_claimed"),
                True,
            ),
        ),
        (
            "statistics_sampling_model_drift",
            lambda value: _mutate(
                value,
                ("statistics", "sampling_model"),
                "iid_binomial",
            ),
        ),
    ],
)
def test_preregistration_rejects_shape_type_and_policy_mutations(
    label: str,
    mutator: Callable[[dict[str, Any]], Any],
) -> None:
    assert label
    with pytest.raises(holdout.HoldoutProtocolError):
        holdout.validate_preregistration(mutator(_protocol()))


def test_preregistration_rejects_missing_or_extra_cell_authority() -> None:
    missing = _protocol()
    missing["candidate_identity"]["source_projection_commit_ids"].pop(ALL_CELLS[0])
    extra = _protocol()
    extra["candidate_identity"]["source_projection_commit_ids"]["other"] = (
        "proj_" + "9" * 64
    )

    with pytest.raises(
        holdout.HoldoutProtocolError,
        match="source_projection_commit_ids_cells_mismatch",
    ):
        holdout.validate_preregistration(missing)
    with pytest.raises(
        holdout.HoldoutProtocolError,
        match="source_projection_commit_ids_cells_mismatch",
    ):
        holdout.validate_preregistration(extra)


def test_preregistration_rejects_mapping_subclasses() -> None:
    with pytest.raises(holdout.HoldoutProtocolError, match="must_be_exact_object"):
        holdout.validate_preregistration(_DictSubclass(_protocol()))


@pytest.mark.parametrize(
    ("sample_count", "alpha"),
    [
        (True, 0.025),
        (0, 0.025),
        (-1, 0.025),
        (1.0, 0.025),
        (100, True),
        (100, 0),
        (100, 0.0),
        (100, 1.0),
        (100, float("nan")),
        (100, float("inf")),
    ],
)
def test_zero_failure_bound_rejects_coercions_and_invalid_inputs(
    sample_count: Any,
    alpha: Any,
) -> None:
    with pytest.raises(holdout.HoldoutProtocolError):
        holdout.zero_failure_upper_bound(sample_count, alpha)


def test_zero_failure_bound_matches_closed_form_at_canonical_boundaries() -> None:
    assert holdout.zero_failure_upper_bound(100, 0.05) == pytest.approx(
        0.029513049607039932
    )
    assert holdout.zero_failure_upper_bound(121, 0.025) > 0.03
    assert holdout.zero_failure_upper_bound(122, 0.025) < 0.03
    assert holdout.zero_failure_upper_bound(132, 0.025) == pytest.approx(
        0.027559177723457506
    )
    assert holdout.zero_failure_upper_bound(125, 0.025) == pytest.approx(
        0.029079837136431474
    )


def test_hmac_commitment_is_canonical_domain_and_protocol_bound() -> None:
    protocol_digest = holdout.preregistration_digest(_protocol())
    key = bytes(range(32))
    payload_a = {"b": [2, 3], "a": "query"}
    payload_b = {"a": "query", "b": [2, 3]}

    commitment = holdout.create_hmac_commitment(
        "query_pack",
        key=key,
        protocol_digest=protocol_digest,
        payload=payload_a,
    )

    assert commitment.startswith("hmac-sha256:")
    assert len(commitment) == 76
    assert holdout.create_hmac_commitment(
        "query_pack",
        key=key,
        protocol_digest=protocol_digest,
        payload=payload_b,
    ) == commitment
    assert holdout.verify_hmac_commitment(
        commitment,
        "query_pack",
        key=key,
        protocol_digest=protocol_digest,
        payload=payload_b,
    )
    assert not holdout.verify_hmac_commitment(
        commitment,
        "query_pack",
        key=key,
        protocol_digest=protocol_digest,
        payload={"a": "changed", "b": [2, 3]},
    )
    assert holdout.create_hmac_commitment(
        "label_pack",
        key=key,
        protocol_digest=protocol_digest,
        payload=payload_a,
    ) != commitment
    assert holdout.create_hmac_commitment(
        "query_pack",
        key=b"z" * 32,
        protocol_digest=protocol_digest,
        payload=payload_a,
    ) != commitment
    assert holdout.create_hmac_commitment(
        "query_pack",
        key=key,
        protocol_digest=_digest("f"),
        payload=payload_a,
    ) != commitment
    assert key.hex() not in commitment


@pytest.mark.parametrize(
    ("domain", "key", "protocol_digest"),
    [
        ("unknown", b"k" * 32, _digest("1")),
        (_StringSubclass("query_pack"), b"k" * 32, _digest("1")),
        ("query_pack", bytearray(b"k" * 32), _digest("1")),
        ("query_pack", b"k" * 31, _digest("1")),
        ("query_pack", b"k" * 33, _digest("1")),
        ("query_pack", b"k" * 32, "1" * 64),
    ],
)
def test_hmac_commitment_rejects_untyped_or_unbound_inputs(
    domain: Any,
    key: Any,
    protocol_digest: Any,
) -> None:
    with pytest.raises(holdout.HoldoutProtocolError):
        holdout.create_hmac_commitment(
            domain,
            key=key,
            protocol_digest=protocol_digest,
            payload={"query": "x"},
        )


@pytest.mark.parametrize(
    "payload",
    [
        ("tuple",),
        _DictSubclass({"query": "x"}),
        {"query": _StringSubclass("x")},
        {1: "non-string-key"},
        {"score": float("nan")},
        {"score": float("inf")},
        {"opaque": object()},
    ],
)
def test_hmac_commitment_rejects_non_exact_json_native_payloads(
    payload: Any,
) -> None:
    with pytest.raises(holdout.HoldoutProtocolError):
        holdout.create_hmac_commitment(
            "query_pack",
            key=b"k" * 32,
            protocol_digest=_digest("1"),
            payload=payload,
        )


def test_hmac_commitment_rejects_cycles_depth_and_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cyclic: list[Any] = []
    cyclic.append(cyclic)
    deep: Any = "leaf"
    for _ in range(holdout.MAX_JSON_DEPTH + 2):
        deep = [deep]

    with pytest.raises(holdout.HoldoutProtocolError, match="contains_cycle"):
        holdout.create_hmac_commitment(
            "query_pack",
            key=b"k" * 32,
            protocol_digest=_digest("1"),
            payload=cyclic,
        )
    with pytest.raises(holdout.HoldoutProtocolError, match="maximum_json_depth"):
        holdout.create_hmac_commitment(
            "query_pack",
            key=b"k" * 32,
            protocol_digest=_digest("1"),
            payload=deep,
        )

    monkeypatch.setattr(holdout, "MAX_COMMITMENT_PAYLOAD_BYTES", 8)
    with pytest.raises(holdout.HoldoutProtocolError, match="payload_too_large"):
        holdout.create_hmac_commitment(
            "query_pack",
            key=b"k" * 32,
            protocol_digest=_digest("1"),
            payload={"query": "long"},
        )


def test_hmac_verifier_rejects_malformed_expected_commitment() -> None:
    with pytest.raises(holdout.HoldoutProtocolError, match="expected_commitment"):
        holdout.verify_hmac_commitment(
            "sha256:" + "1" * 64,
            "query_pack",
            key=b"k" * 32,
            protocol_digest=_digest("1"),
            payload={"query": "x"},
        )


_EVIDENCE_KIND = {
    "frame_committed": "frame_manifest_commitment",
    "seed_revealed": "selection_seed_receipt",
    "pack_sealed": "query_label_adjudication_seal",
    "query_captured": "label_blind_capture_receipt",
    "labels_revealed": "label_release_receipt",
    "scored": "fixed_threshold_verdict",
}


def _transition(
    state: dict[str, Any],
    *,
    protocol: dict[str, Any],
    next_stage: str | None = None,
    commit_index: int | None = None,
) -> dict[str, Any]:
    sequence = state["sequence"] + 1
    stage = next_stage or holdout.STAGES[sequence]
    index = commit_index if commit_index is not None else sequence + 1
    return {
        "schema_version": holdout.TRANSITION_SCHEMA,
        "protocol_digest": state["protocol_digest"],
        "from_stage": state["stage"],
        "to_stage": stage,
        "sequence": sequence,
        "previous_state_digest": holdout.state_digest(state, protocol=protocol),
        "evidence_kind": _EVIDENCE_KIND[stage],
        "declared_commit": f"{index:040x}",
        "artifact_digest": "sha256:" + f"{index:064x}",
    }


def test_state_chain_enforces_order_and_never_claims_external_authority() -> None:
    protocol = _protocol()
    state = holdout.initialize_state(
        protocol,
        declared_preregistration_commit=f"{1:040x}",
    )
    observed_stages = [state["stage"]]

    for _ in holdout.STAGES[1:]:
        previous = state
        state = holdout.advance_state(
            state,
            _transition(state, protocol=protocol),
            protocol=protocol,
        )
        observed_stages.append(state["stage"])
        assert state["previous_state_digest"] == holdout.state_digest(
            previous,
            protocol=protocol,
        )
        assert state["capability_boundary"] == _no_authority_boundary(
            "structural_state_projection_only"
        )

    assert tuple(observed_stages) == holdout.STAGES
    assert state["sequence"] == len(holdout.STAGES) - 1
    assert state["candidate_commit"] == protocol["candidate_identity"][
        "candidate_commit"
    ]
    assert len(state["declared_commit_chain"]) == len(holdout.STAGES)
    assert len(set(state["declared_commit_chain"])) == len(holdout.STAGES)
    assert state["stage"] == "scored"
    assert state["capability_boundary"]["holdout_evidence_gate_met"] is False
    assert state["capability_boundary"]["external_provenance_verified"] is False
    assert state["capability_boundary"]["git_ancestry_verified"] is False
    assert state["capability_boundary"]["one_shot_enforced"] is False
    assert state["capability_boundary"]["runtime_authority_granted"] is False

    terminal_transition = {
        "schema_version": holdout.TRANSITION_SCHEMA,
        "protocol_digest": state["protocol_digest"],
        "from_stage": "scored",
        "to_stage": "scored",
        "sequence": len(holdout.STAGES),
        "previous_state_digest": holdout.state_digest(state, protocol=protocol),
        "evidence_kind": "fixed_threshold_verdict",
        "declared_commit": f"{len(holdout.STAGES) + 1:040x}",
        "artifact_digest": _digest("f"),
    }
    with pytest.raises(holdout.HoldoutProtocolError, match="terminal"):
        holdout.advance_state(state, terminal_transition, protocol=protocol)


def test_initial_state_binds_protocol_and_requires_exact_commit() -> None:
    protocol = _protocol()
    state = holdout.initialize_state(
        protocol,
        declared_preregistration_commit="b" * 40,
    )

    assert state["protocol_digest"] == holdout.preregistration_digest(protocol)
    assert state["stage"] == "preregistered"
    assert state["sequence"] == 0
    assert state["previous_state_digest"] is None
    assert state["evidence"] == {
        "kind": "preregistration",
        "declared_commit": "b" * 40,
        "artifact_digest": holdout.preregistration_digest(protocol),
    }

    for invalid in (
        "B" * 40,
        "b" * 39,
        _StringSubclass("b" * 40),
        protocol["candidate_identity"]["candidate_commit"],
    ):
        with pytest.raises(holdout.HoldoutProtocolError):
            holdout.initialize_state(
                protocol,
                declared_preregistration_commit=invalid,
            )


@pytest.mark.parametrize(
    ("label", "mutation"),
    [
        ("extra_key", lambda row: {**row, "extra": False}),
        (
            "skip_stage",
            lambda row: {
                **row,
                "to_stage": "seed_revealed",
                "evidence_kind": "selection_seed_receipt",
            },
        ),
        (
            "same_chain_commit",
            lambda row: {**row, "declared_commit": f"{1:040x}"},
        ),
        (
            "candidate_commit_reuse",
            lambda row: {**row, "declared_commit": "a" * 40},
        ),
        (
            "wrong_previous",
            lambda row: {**row, "previous_state_digest": _digest("f")},
        ),
        (
            "wrong_protocol",
            lambda row: {**row, "protocol_digest": _digest("e")},
        ),
        (
            "wrong_evidence",
            lambda row: {**row, "evidence_kind": "fixed_threshold_verdict"},
        ),
        ("bool_sequence", lambda row: {**row, "sequence": True}),
        (
            "stage_subclass",
            lambda row: {
                **row,
                "to_stage": _StringSubclass("frame_committed"),
            },
        ),
        (
            "schema_subclass",
            lambda row: {
                **row,
                "schema_version": _StringSubclass(holdout.TRANSITION_SCHEMA),
            },
        ),
    ],
)
def test_transition_rejects_skip_tampering_commit_reuse_and_coercion(
    label: str,
    mutation: Callable[[dict[str, Any]], dict[str, Any]],
) -> None:
    protocol = _protocol()
    state = holdout.initialize_state(
        protocol,
        declared_preregistration_commit=f"{1:040x}",
    )

    with pytest.raises(holdout.HoldoutProtocolError):
        holdout.advance_state(
            state,
            mutation(_transition(state, protocol=protocol)),
            protocol=protocol,
        )


def test_exact_transition_reprojection_is_deterministic_not_one_shot_evidence(
) -> None:
    protocol = _protocol()
    state = holdout.initialize_state(
        protocol,
        declared_preregistration_commit=f"{1:040x}",
    )
    transition = _transition(state, protocol=protocol)

    first = holdout.advance_state(state, transition, protocol=protocol)
    second = holdout.advance_state(state, transition, protocol=protocol)

    assert first == second
    assert first["capability_boundary"]["one_shot_enforced"] is False
    assert first["capability_boundary"]["git_ancestry_verified"] is False
    assert first["capability_boundary"]["external_provenance_verified"] is False


def test_state_validator_rejects_authority_and_commit_chain_forgery() -> None:
    protocol = _protocol()
    initial = holdout.initialize_state(
        protocol,
        declared_preregistration_commit=f"{1:040x}",
    )
    state = holdout.advance_state(
        initial,
        _transition(initial, protocol=protocol),
        protocol=protocol,
    )
    authority = copy.deepcopy(state)
    authority["capability_boundary"]["holdout_evidence_gate_met"] = True
    nonbool_authority = copy.deepcopy(state)
    nonbool_authority["capability_boundary"]["runtime_authority_granted"] = 0
    duplicate_commit = copy.deepcopy(state)
    duplicate_commit["declared_commit_chain"][-1] = duplicate_commit[
        "declared_commit_chain"
    ][0]
    wrong_tail = copy.deepcopy(state)
    wrong_tail["evidence"]["declared_commit"] = "f" * 40

    wrong_candidate = copy.deepcopy(state)
    wrong_candidate["candidate_commit"] = "c" * 40
    candidate_in_chain = copy.deepcopy(state)
    candidate_in_chain["declared_commit_chain"][0] = candidate_in_chain[
        "candidate_commit"
    ]

    for forged in (
        authority,
        nonbool_authority,
        duplicate_commit,
        wrong_tail,
        wrong_candidate,
        candidate_in_chain,
    ):
        with pytest.raises(holdout.HoldoutProtocolError):
            holdout.validate_state(forged, protocol=protocol)


def test_state_projection_does_not_mutate_inputs() -> None:
    protocol = _protocol()
    protocol_before = copy.deepcopy(protocol)
    state = holdout.initialize_state(
        protocol,
        declared_preregistration_commit=f"{1:040x}",
    )
    state_before = copy.deepcopy(state)
    transition = _transition(state, protocol=protocol)
    transition_before = copy.deepcopy(transition)

    holdout.advance_state(state, transition, protocol=protocol)

    assert protocol == protocol_before
    assert state == state_before
    assert transition == transition_before
