from __future__ import annotations

import copy
import hashlib
from typing import Any, Callable

import pytest

from tools import magma_faiss_holdout_frame as holdout_frame
from tools import magma_faiss_holdout_protocol as holdout_protocol
from waggledance.core.hex_cell_topology import ALL_CELLS
from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest


FRAME_KEY = b"f" * 32
SELECTION_SEED = b"s" * 32


class _StringSubclass(str):
    pass


class _DictSubclass(dict):
    pass


FROZEN_IDENTITY_VECTOR = (
    (
        "autumn_preparation",
        "seasonal",
        "sha256:e84897b88bec75b38a71364a0c590363b7f4c6165b6d27369279b2eace9a93ad",
        "sha256:035401dd7b5cfbe81ceeaf2bb6d0c9b5153f60fb4ba32ac20516303893a1b01f",
        "sha256:7ff7c4064dac12188497ff8bdd1b4411cf1b97e1957276ef73df1eb06b3b56a6",
    ),
    (
        "battery_discharge",
        "energy",
        "sha256:d61e1eb8ec50d6420c424cb49cfa3b07c36e45fbad96bf0cd9c46a1fd578e134",
        "sha256:89aab4e12608ff876391df6984607bef3c7d8dbe5a57b65e2e30cbe80398b549",
        "sha256:f4d3b710a09430408b68ba600f78cf16f76973c0dfb1ecdc224d876767d537aa",
    ),
    (
        "colony_food_reserves",
        "math",
        "sha256:8ffa8f0eb85860ab3b10727413d423195608f9ada5d9b3c76dd970f2614be7f0",
        "sha256:6fd76e6f28772f07dd9ae27d1b9e7e31ab1811cd1fa34f8ee6414c2c6140454d",
        "sha256:c6b5e72a50a6a6e00612f691c79b2279ec36cdbc8c23d56b489428fe42b76f12",
    ),
    (
        "colony_growth_rate",
        "learning",
        "sha256:009ae8780ba7689603ea307550e8f315067e1c7cf01ce09fa106dcb5437d1b95",
        "sha256:c21dee5d779c7bd02563a834ed81984837cb6db542fc6dc68b91bdc130c565ac",
        "sha256:8e3fad6a87eb608e0afab97b08f22fb8f8f0b12c4aae38eb8ca72b180bae43cc",
    ),
    (
        "comfort_energy_tradeoff",
        "energy",
        "sha256:9375cb8b8b9f8b8127a95f9e6030c0187072c70820b4b34ca38c99946902942c",
        "sha256:7838874917bfe5578b93992ddcffd6a8af7af498c67cf617841c7784ea1e91c2",
        "sha256:47c826a963691d6de7f70b18c92d9457e8bc696587487372a205e59e6ed65f31",
    ),
    (
        "heat_pump_cop",
        "thermal",
        "sha256:430a2f9b34ccdfb5ab26d0e828d65f6fb8d7076aded37b63288321fe9ad12aef",
        "sha256:2097ce48db733e8ad961528aa877fd85b726f2385f585dafdfa52fbcb1f122ac",
        "sha256:35004a2524e1621d7f01287fe3fb6fa8dc1746419be6d4a7850b9609bb91428e",
    ),
    (
        "heating_cost",
        "thermal",
        "sha256:184de53e7d7d67d7fafb161779ed6c11eccf23e8c0e9ec6656285dddd3a64acb",
        "sha256:473ac3046a89b857df57a8d9cbf2bbc42c7283a28ef960037c0a1afefba8c89f",
        "sha256:3e04b87533240aa74905cbe985430b90b37f1012972e2af02b333452bc5e6d56",
    ),
    (
        "hive_thermal_balance",
        "thermal",
        "sha256:3d18d2141ab6f0a9871412d7baa8930baccc7abc88ba3fa5d4ea7a4e18e9f09a",
        "sha256:cc673e4b911934dbbaacd74296840c788a47636cd234e1494f908efa76fb78ca",
        "sha256:39e2348f3ccac7dc132cb298f5c9de1bbe133ef18bcd977bfb68d56de7c006c3",
    ),
    (
        "honey_yield",
        "math",
        "sha256:d6daaa1048bbacf1f8aa7f8e838b15f4c08141f1f80be4f571c91d04c0075cbf",
        "sha256:187ef497df0688bf967157010953dfb37d7c02f4c99434c99fd404d45839ba8c",
        "sha256:b1b5e4a8341b1260ca5bfef583bed0ab6ba759801ccaa697532dc3f127a636b9",
    ),
    (
        "indoor_air_quality",
        "general",
        "sha256:935879d0f1ae1ed0d0cc800810777bcdf178c32ed6fb84e3c36c354d3c0f6b9e",
        "sha256:371dacf09db6e07b48c6c200370e1c312fb968d021d42671e12b8080ed333217",
        "sha256:dda3bde7db016d021a6f1307543179233e361a03ff4e0eedf19b76e09e3575c3",
    ),
    (
        "mtbf_prediction",
        "system",
        "sha256:76e8b856f2a3aec9a4a27d2bacd74a15540126b8a495e42c6c0edf10999b8f42",
        "sha256:34718279a3cbfb4c7f2cdc37e1416fe2439e98bb88b828c424fbc3e175c9232e",
        "sha256:bdf384cdbbf7fb71912a1975b79f0ea88b01b650175d40bd7838070ed5e3444e",
    ),
    (
        "nectar_flow_timing",
        "seasonal",
        "sha256:e56452221e61d965918d3be1893eeff0c9c6e6e003c7ca172b9c385d0db0e817",
        "sha256:6c97848932d9a176587a1e5d02cd346d20bf07b80a4a43690bb2e62e33b6187f",
        "sha256:4e472663fa2ce60af6b094a0a0a901394db26f0bf472266140265cb8c3bcd9d2",
    ),
    (
        "oee_decomposition",
        "system",
        "sha256:3fba59f4084bbcca62139452ab139964f022c4668a9e027ee5bf47c209302959",
        "sha256:d7070eb6a828b8c3a99da816e65b8967c2c5bf46f84577c5ffd1e7d4d38d70fe",
        "sha256:d3053f92ff1ab75fcc499c70bef0bfe7f116899b1f90395549ab4804f197832e",
    ),
    (
        "pipe_freezing",
        "thermal",
        "sha256:bae3d5c4484d808e3497bf788f77245855a8ce66d2b268292c7f320c18012879",
        "sha256:7da8ad69ae89d657627c2268d64995542aade9594c0d1f5a00aa7dc210fa140a",
        "sha256:c7578723b1cc9ea9e8ac88a5d8bd51cf5c4058ba42192e6e695f696d59474e59",
    ),
    (
        "queen_age_replacement",
        "learning",
        "sha256:170f5f1eae4644f9a7c3bd3fcdcab7e20291095ba41925d172a2a30988f76fec",
        "sha256:b6965ce029a399900e5f16a4bf54427342ce189a516ab4eb4071e914201fb68a",
        "sha256:057d027dede67fbcb30d572ea805959d9fc4c361eb565cc42fa8fd2de239cefd",
    ),
    (
        "signal_propagation",
        "system",
        "sha256:5cde1704ea05c0c5c6761bb1727c6e1fbda0af8fd20c493b770497f1667bec1a",
        "sha256:bb7a589f23b4e928458324cb6fa4f541d17e3343665571bd8726bdf0daed57ff",
        "sha256:8793af4372039009605ca266278d4b8714245bf5c95044b001be5d8e7ed067f2",
    ),
    (
        "solar_yield",
        "energy",
        "sha256:ea28cbbee4448d69bdceb34ed31b4c761cf59fdb78aeb8535d3d536bb116edda",
        "sha256:5b368ae77a35dcbaecd8339a895dcad6f905faeaf29e88d29d958ab03ce98293",
        "sha256:f0b0cf5ff6d69a8be059af2a5c39b7246a6004e069eabe076529b8d2bc24f504",
    ),
    (
        "spring_inspection_timing",
        "seasonal",
        "sha256:6d903615188f5d009b31c39821fd33f198ee66d1e7967d5c51de5248bf8ef36c",
        "sha256:ddc15966e51750dca71da318ed63533d45a920801b38db3641d4ad61dcb7ce23",
        "sha256:a7036d89df0fe02cbf25d25ec5f4034c310b50a838f52a19451d13f9089d62b4",
    ),
    (
        "swarm_risk",
        "safety",
        "sha256:20c4902e41e208c487e0aab29949bbdbf4a92b5d0e679f3d357c8cddd01d2652",
        "sha256:28d309beece0f0c5c7da40f16fb5420775d2e380228b4dec967d859a1ad56fe3",
        "sha256:0410cf739b00c9dc2b0f738cd0073e98392ea53bfb6d7a76f4ba1e0216f60bc1",
    ),
    (
        "varroa_treatment",
        "safety",
        "sha256:f469c3d46037611a9a58de7b13c469043f366e54d4b588b8d6fdd2dfd7c17de4",
        "sha256:8bbc9a47e35e09f855dcde716e7f6884a17aa453f8710c1be587b4a2eb39237f",
        "sha256:95916441cf96eccfc13703803b611e87acf36d9d20fbfcd7595ebcb937be8dc2",
    ),
    (
        "varroa_treatment_calendar",
        "seasonal",
        "sha256:203c68b4b7951b5bd5b70d3609f4b58997ed23098a562f27387ab73be0b6125d",
        "sha256:36c7b96a017e4093722445642fd24f268773e938c4ae3c34bc23e85e2d722960",
        "sha256:9ce0e16c5623f5b19d25c0dd02f70125aec1ebf3fdd3a282a8896927eaefe37f",
    ),
    (
        "winter_feeding_decision",
        "seasonal",
        "sha256:2d68b7347f0958fc166aa5f4c4903aeb8c067c09417e4a7e18b14373fa3939ce",
        "sha256:6918cdf9802b30cc187c022427647cef9e585a5a63127790285343e5cc2489a1",
        "sha256:aa7826713af3151164f925bb86a4331fb9a542c9cb01d34991ceee1dcd10bb9a",
    ),
)


def _sha(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def _hex(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _actor(label: str) -> str:
    return "actor_" + _hex(label)


def _protocol_boundary() -> dict[str, Any]:
    return {
        "artifact_class": "protocol_substrate_only",
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


def _frame_boundary(artifact_class: str) -> dict[str, Any]:
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


def _solver_identities() -> list[dict[str, str]]:
    return [
        {
            "canonical_solver_id": f"solver_{index:02d}",
            "cell_id": ALL_CELLS[index % len(ALL_CELLS)],
            "projection_id": _sha(f"projection-{index}"),
            "projection_digest": _sha(f"projection-content-{index}"),
            "source_digest": _sha(f"projection-source-{index}"),
        }
        for index in range(holdout_protocol.FROZEN_SOLVER_COUNT)
    ]


def test_solver_identity_digest_matches_frozen_snapshot_known_vector() -> None:
    identities = [
        {
            "canonical_solver_id": solver_id,
            "cell_id": cell_id,
            "projection_id": projection_id,
            "projection_digest": projection_digest,
            "source_digest": source_digest,
        }
        for (
            solver_id,
            cell_id,
            projection_id,
            projection_digest,
            source_digest,
        ) in FROZEN_IDENTITY_VECTOR
    ]

    assert holdout_frame.solver_identity_set_digest(identities) == (
        "sha256:981ba6d7aea7acc079fb188b8177198a2ebcf7003a499281bc3fb93c21ed5d36"
    )
    assert holdout_frame.solver_identity_set_digest(list(reversed(identities))) == (
        "sha256:981ba6d7aea7acc079fb188b8177198a2ebcf7003a499281bc3fb93c21ed5d36"
    )


def _protocol(identities: list[dict[str, str]]) -> dict[str, Any]:
    candidate_commit = "a" * 40
    return {
        "schema_version": holdout_protocol.PROTOCOL_SCHEMA,
        "protocol_id": "holdoutproto_" + "1" * 32,
        "candidate_identity": {
            "candidate_commit": candidate_commit,
            "solver_count": 22,
            "snapshot_id": "faisscand_" + "2" * 64,
            "topology_digest": _sha("topology"),
            "projection_identity_set_digest": (
                holdout_frame.solver_identity_set_digest(identities)
            ),
            "embedding_contract_digest": _sha("embedding"),
            "model_catalog_digest": _sha("models"),
            "source_projection_commit_ids": {
                cell_id: "proj_" + f"{index + 1:064x}"
                for index, cell_id in enumerate(ALL_CELLS)
            },
            "faiss_identity": {
                "faiss_version": "1.13.2",
                "faiss_compile_options": "AVX2",
                "faiss_binary_set_sha256": _sha("faiss-binary"),
            },
            "capture_tool_path": "tools/capture_holdout.py",
            "capture_tool_sha256": _sha("capture-tool"),
            "score_tool_path": "tools/score_holdout.py",
            "score_tool_sha256": _sha("score-tool"),
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
            "positive_strata": list(holdout_protocol.POSITIVE_STRATA),
            "positive_per_stratum": 22,
            "positive_solver_coverage_policy": (
                "exactly_one_per_frozen_solver_per_positive_stratum"
            ),
            "positive_solver_identity_binding": (
                "candidate_projection_identity_set_digest"
            ),
            "ood_strata": list(holdout_protocol.OOD_STRATA),
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
        "capability_boundary": _protocol_boundary(),
    }


def _case(
    *,
    label: str,
    query: str,
    language: str,
    stratum: str,
    author: str,
    case_class: str,
    solver_id: str | None,
    cell_id: str | None,
) -> dict[str, Any]:
    return {
        "opaque_case_id": "case_" + _hex(f"case:{label}"),
        "intent_cluster_id": "intent_" + _hex(f"intent:{label}"),
        "query": query,
        "language": language,
        "stratum": stratum,
        "author_actor_id": author,
        "authored_at_utc": "2026-08-12T14:00:00Z",
        "semantic_family_digest": _sha(f"family:{label}"),
        "design_case_class": case_class,
        "design_solver_id": solver_id,
        "design_cell_id": cell_id,
    }


def _fixture() -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    identities = _solver_identities()
    protocol = _protocol(identities)
    state = holdout_protocol.initialize_state(
        protocol,
        declared_preregistration_commit="b" * 40,
    )
    positive_authors = [_actor("positive-a"), _actor("positive-b")]
    ood_authors = [_actor("ood-a"), _actor("ood-b")]
    positive: list[dict[str, Any]] = []
    counter = 0
    for stratum in holdout_protocol.POSITIVE_STRATA:
        for identity in identities:
            for replica in range(2):
                label = f"positive:{stratum}:{identity['canonical_solver_id']}:{replica}"
                positive.append(
                    _case(
                        label=label,
                        query=(
                            f"Positive query {stratum} "
                            f"{identity['canonical_solver_id']} sample {replica}"
                        ),
                        language=stratum.split("_", 1)[0],
                        stratum=stratum,
                        author=positive_authors[counter % len(positive_authors)],
                        case_class="positive",
                        solver_id=identity["canonical_solver_id"],
                        cell_id=identity["cell_id"],
                    )
                )
                counter += 1

    ood: list[dict[str, Any]] = []
    counter = 0
    for stratum in holdout_protocol.OOD_STRATA:
        for language, count in (("fi", 24), ("en", 26)):
            for replica in range(count):
                label = f"ood:{stratum}:{language}:{replica}"
                ood.append(
                    _case(
                        label=label,
                        query=(
                            f"OOD query {stratum} {language} sample {replica}"
                        ),
                        language=language,
                        stratum=stratum,
                        author=ood_authors[counter % len(ood_authors)],
                        case_class="ood",
                        solver_id=None,
                        cell_id=None,
                    )
                )
                counter += 1

    frame = {
        "schema_version": holdout_frame.FRAME_SCHEMA,
        "protocol_digest": holdout_protocol.preregistration_digest(protocol),
        "preregistration_state_digest": holdout_protocol.state_digest(
            state,
            protocol=protocol,
        ),
        "declared_preregistration_published_at_utc": "2026-08-12T13:00:00Z",
        "frame_frozen_at_utc": "2026-08-12T15:00:00Z",
        "development_exclusion_set_digest": _sha("development-exclusions"),
        "solver_identity_set_digest": (
            holdout_frame.solver_identity_set_digest(identities)
        ),
        "solver_identities": identities,
        "roles": {
            "candidate_owner_actor_id": _actor("candidate-owner"),
            "custodian_actor_id": _actor("custodian"),
            "positive_author_actor_ids": positive_authors,
            "ood_author_actor_ids": ood_authors,
            "adjudicator_actor_ids": [
                _actor("adjudicator-a"),
                _actor("adjudicator-b"),
            ],
        },
        "attestations": {
            "case_authors_score_blind": True,
            "future_adjudicators_score_blind": True,
            "adjudication_occurs_after_selection": True,
            "adjudication_occurs_before_query_capture": True,
            "no_reseed": True,
            "no_post_selection_replacement": True,
            "semantic_intent_clustering_completed": True,
            "translation_template_variants_share_cluster": True,
            "opaque_case_ids_random_and_class_blind": True,
            "candidate_owner_excluded_from_case_content": True,
        },
        "positive_cases": positive,
        "ood_cases": ood,
        "capability_boundary": _frame_boundary(
            "external_plaintext_frame_validation_only"
        ),
    }
    return protocol, state, frame


def _selection(
    protocol: dict[str, Any],
    state: dict[str, Any],
    frame: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    commitment = holdout_frame.create_frame_commitment(
        frame,
        protocol=protocol,
        preregistration_state=state,
        key=FRAME_KEY,
    )
    receipt = holdout_frame.select_frame_cases(
        frame,
        protocol=protocol,
        preregistration_state=state,
        selection_seed=SELECTION_SEED,
        frame_commitment_key=FRAME_KEY,
        expected_frame_commitment=commitment,
    )
    return commitment, receipt


def _mutate_case(
    frame: dict[str, Any],
    endpoint: str,
    index: int,
    key: str,
    value: Any,
) -> dict[str, Any]:
    result = copy.deepcopy(frame)
    result[endpoint][index][key] = value
    return result


def test_valid_frame_and_selection_are_bound_balanced_and_non_authoritative() -> None:
    protocol, state, frame = _fixture()
    source = copy.deepcopy(frame)

    validated = holdout_frame.validate_frame(
        frame,
        protocol=protocol,
        preregistration_state=state,
    )
    commitment, receipt = _selection(protocol, state, frame)

    assert frame == source
    assert validated is not frame
    assert len(validated["positive_cases"]) == 264
    assert len(validated["ood_cases"]) == 250
    assert commitment.startswith("hmac-sha256:")
    assert commitment == (
        "hmac-sha256:b1573a974c9f8f18aedd59106c2d1e517c05254279b866a7f834dc4f5d474f17"
    )
    assert receipt["selected_case_count"] == 257
    assert receipt["positive_case_count"] == 132
    assert receipt["ood_case_count"] == 125
    assert receipt["positive_group_count"] == 132
    assert receipt["ood_group_count"] == 10
    assert receipt["selection_attempt"] == 0
    assert len(receipt["selected_case_ids"]) == 257
    assert len(set(receipt["selected_case_ids"])) == 257
    assert sha256_digest(receipt["selected_case_ids"]) == (
        "sha256:90cd99a19cb43ee9be845a74573a9224ca4155beaaa741e5506c28b00b0a4791"
    )
    assert receipt["selection_seed_sha256"] == _sha(
        SELECTION_SEED.decode("ascii")
    )
    assert receipt["capability_boundary"] == _frame_boundary(
        "deterministic_selection_projection_only"
    )

    by_id = {
        case["opaque_case_id"]: case
        for case in [
            *validated["positive_cases"],
            *validated["ood_cases"],
        ]
    }
    selected_cases = [by_id[case_id] for case_id in receipt["selected_case_ids"]]
    positive_groups = {
        (case["stratum"], case["design_solver_id"])
        for case in selected_cases
        if case["design_case_class"] == "positive"
    }
    assert len(positive_groups) == 132
    for stratum in holdout_protocol.OOD_STRATA:
        selected_stratum = [
            case
            for case in selected_cases
            if case["design_case_class"] == "ood" and case["stratum"] == stratum
        ]
        assert sum(case["language"] == "fi" for case in selected_stratum) == 12
        assert sum(case["language"] == "en" for case in selected_stratum) == 13


def test_frame_permutation_does_not_change_commitment_or_selection() -> None:
    protocol, state, frame = _fixture()
    commitment, receipt = _selection(protocol, state, frame)
    permuted = copy.deepcopy(frame)
    permuted["positive_cases"].reverse()
    permuted["ood_cases"].reverse()
    permuted["solver_identities"].reverse()
    permuted["roles"]["positive_author_actor_ids"].reverse()
    permuted["roles"]["ood_author_actor_ids"].reverse()
    permuted["roles"]["adjudicator_actor_ids"].reverse()

    other_commitment, other_receipt = _selection(protocol, state, permuted)

    assert other_commitment == commitment
    assert other_receipt == receipt


@pytest.mark.parametrize(
    ("endpoint", "key", "replacement"),
    [
        ("positive_cases", "opaque_case_id", _StringSubclass("case_" + "1" * 64)),
        ("positive_cases", "query", " leading"),
        ("positive_cases", "query", "two  spaces"),
        ("positive_cases", "query", "line\nbreak"),
        ("positive_cases", "language", "sv"),
        ("positive_cases", "design_case_class", "ood"),
        ("positive_cases", "design_solver_id", "unknown_solver"),
        ("positive_cases", "authored_at_utc", "2026-08-12T13:00:00Z"),
        ("positive_cases", "authored_at_utc", "2026-08-12T16:00:00Z"),
        ("ood_cases", "design_case_class", "positive"),
        ("ood_cases", "design_solver_id", "solver_00"),
        ("ood_cases", "design_cell_id", ALL_CELLS[0]),
    ],
)
def test_frame_rejects_invalid_case_shape_type_target_and_time(
    endpoint: str,
    key: str,
    replacement: Any,
) -> None:
    protocol, state, frame = _fixture()
    mutated = _mutate_case(frame, endpoint, 0, key, replacement)

    with pytest.raises(holdout_frame.HoldoutFrameError):
        holdout_frame.validate_frame(
            mutated,
            protocol=protocol,
            preregistration_state=state,
        )


@pytest.mark.parametrize(
    "duplicate_key",
    [
        "opaque_case_id",
        "intent_cluster_id",
        "semantic_family_digest",
        "query",
    ],
)
def test_frame_rejects_global_duplicate_authorities(
    duplicate_key: str,
) -> None:
    protocol, state, frame = _fixture()
    duplicate = copy.deepcopy(frame)
    duplicate["ood_cases"][0][duplicate_key] = duplicate["positive_cases"][0][
        duplicate_key
    ]

    with pytest.raises(holdout_frame.HoldoutFrameError):
        holdout_frame.validate_frame(
            duplicate,
            protocol=protocol,
            preregistration_state=state,
        )


def test_frame_rejects_normalized_query_duplicate_across_endpoints() -> None:
    protocol, state, frame = _fixture()
    duplicate = copy.deepcopy(frame)
    original = duplicate["positive_cases"][0]["query"]
    duplicate["ood_cases"][0]["query"] = original.swapcase()

    with pytest.raises(
        holdout_frame.HoldoutFrameError,
        match="normalized_queries_must_be_globally_unique",
    ):
        holdout_frame.validate_frame(
            duplicate,
            protocol=protocol,
            preregistration_state=state,
        )


def test_frame_rejects_nfkc_query_duplicate_across_endpoints() -> None:
    protocol, state, frame = _fixture()
    duplicate = copy.deepcopy(frame)
    original = duplicate["positive_cases"][0]["query"]
    duplicate["ood_cases"][0]["query"] = "".join(
        chr(ord(character) + 0xFEE0)
        if "!" <= character <= "~"
        else character
        for character in original
    )

    with pytest.raises(
        holdout_frame.HoldoutFrameError,
        match="normalized_queries_must_be_globally_unique",
    ):
        holdout_frame.validate_frame(
            duplicate,
            protocol=protocol,
            preregistration_state=state,
        )


def test_frame_rejects_thin_group_even_when_another_group_has_excess() -> None:
    protocol, state, frame = _fixture()
    thin_positive = copy.deepcopy(frame)
    removed = thin_positive["positive_cases"].pop(0)
    excess = copy.deepcopy(thin_positive["positive_cases"][2])
    excess["opaque_case_id"] = "case_" + _hex("positive-excess")
    excess["intent_cluster_id"] = "intent_" + _hex("positive-excess")
    excess["semantic_family_digest"] = _sha("positive-excess")
    excess["query"] = "Positive excess case"
    thin_positive["positive_cases"].append(excess)
    assert removed["design_solver_id"] != excess["design_solver_id"]

    thin_ood = copy.deepcopy(frame)
    fi_index = next(
        index
        for index, case in enumerate(thin_ood["ood_cases"])
        if case["language"] == "fi"
    )
    thin_ood["ood_cases"].pop(fi_index)
    excess = copy.deepcopy(
        next(case for case in thin_ood["ood_cases"] if case["language"] == "en")
    )
    excess["opaque_case_id"] = "case_" + _hex("ood-excess")
    excess["intent_cluster_id"] = "intent_" + _hex("ood-excess")
    excess["semantic_family_digest"] = _sha("ood-excess")
    excess["query"] = "OOD excess case"
    thin_ood["ood_cases"].append(excess)

    for mutated in (thin_positive, thin_ood):
        with pytest.raises(holdout_frame.HoldoutFrameError):
            holdout_frame.validate_frame(
                mutated,
                protocol=protocol,
                preregistration_state=state,
            )


def test_frame_allows_more_than_the_minimum_within_a_design_cell() -> None:
    protocol, state, frame = _fixture()
    expanded = copy.deepcopy(frame)
    extra = copy.deepcopy(expanded["positive_cases"][0])
    extra["opaque_case_id"] = "case_" + _hex("valid-positive-excess")
    extra["intent_cluster_id"] = "intent_" + _hex("valid-positive-excess")
    extra["semantic_family_digest"] = _sha("valid-positive-excess")
    extra["query"] = "Valid positive excess case"
    expanded["positive_cases"].append(extra)

    validated = holdout_frame.validate_frame(
        expanded,
        protocol=protocol,
        preregistration_state=state,
    )

    assert len(validated["positive_cases"]) == 265


def test_frame_rejects_role_overlap_unknown_author_and_false_attestation() -> None:
    protocol, state, frame = _fixture()
    overlap = copy.deepcopy(frame)
    overlap["roles"]["ood_author_actor_ids"][0] = overlap["roles"][
        "positive_author_actor_ids"
    ][0]
    unknown_author = _mutate_case(
        frame,
        "positive_cases",
        0,
        "author_actor_id",
        _actor("unknown"),
    )
    false_attestation = copy.deepcopy(frame)
    false_attestation["attestations"]["no_post_selection_replacement"] = False
    reseed_attestation = copy.deepcopy(frame)
    reseed_attestation["attestations"]["no_reseed"] = False
    unused_author = copy.deepcopy(frame)
    unused_author["roles"]["positive_author_actor_ids"].append(
        _actor("unused-positive-author")
    )

    for mutated in (
        overlap,
        unknown_author,
        false_attestation,
        reseed_attestation,
        unused_author,
    ):
        with pytest.raises(holdout_frame.HoldoutFrameError):
            holdout_frame.validate_frame(
                mutated,
                protocol=protocol,
                preregistration_state=state,
            )


def test_frame_freeze_must_follow_preregistration_publication() -> None:
    protocol, state, frame = _fixture()
    frame["frame_frozen_at_utc"] = frame[
        "declared_preregistration_published_at_utc"
    ]

    with pytest.raises(
        holdout_frame.HoldoutFrameError,
        match="frame_freeze_must_follow_preregistration",
    ):
        holdout_frame.validate_frame(
            frame,
            protocol=protocol,
            preregistration_state=state,
        )


def test_frame_rejects_protocol_state_identity_and_authority_forgery() -> None:
    protocol, state, frame = _fixture()
    wrong_protocol = copy.deepcopy(protocol)
    wrong_protocol["protocol_id"] = "holdoutproto_" + "f" * 32
    wrong_state = copy.deepcopy(state)
    wrong_state["candidate_commit"] = "c" * 40
    wrong_identity = copy.deepcopy(frame)
    wrong_identity["solver_identities"][0]["projection_id"] = _sha("wrong")
    authority = copy.deepcopy(frame)
    authority["capability_boundary"]["holdout_evidence_gate_met"] = True
    nonbool_authority = copy.deepcopy(frame)
    nonbool_authority["capability_boundary"]["runtime_authority_granted"] = 0

    cases = (
        (frame, wrong_protocol, state),
        (frame, protocol, wrong_state),
        (wrong_identity, protocol, state),
        (authority, protocol, state),
        (nonbool_authority, protocol, state),
    )
    for mutated_frame, mutated_protocol, mutated_state in cases:
        with pytest.raises(holdout_frame.HoldoutFrameError):
            holdout_frame.validate_frame(
                mutated_frame,
                protocol=mutated_protocol,
                preregistration_state=mutated_state,
            )


def test_frame_requires_the_initial_preregistered_state() -> None:
    protocol, state, frame = _fixture()
    transition = {
        "schema_version": holdout_protocol.TRANSITION_SCHEMA,
        "protocol_digest": state["protocol_digest"],
        "from_stage": "preregistered",
        "to_stage": "frame_committed",
        "sequence": 1,
        "previous_state_digest": holdout_protocol.state_digest(
            state,
            protocol=protocol,
        ),
        "evidence_kind": "frame_manifest_commitment",
        "declared_commit": "c" * 40,
        "artifact_digest": _sha("frame-artifact"),
    }
    advanced = holdout_protocol.advance_state(
        state,
        transition,
        protocol=protocol,
    )

    with pytest.raises(
        holdout_frame.HoldoutFrameError,
        match="frame_requires_preregistered_state",
    ):
        holdout_frame.validate_frame(
            frame,
            protocol=protocol,
            preregistration_state=advanced,
        )


def test_frame_enforces_bounded_case_count(monkeypatch: pytest.MonkeyPatch) -> None:
    protocol, state, frame = _fixture()
    monkeypatch.setattr(holdout_frame, "MAX_FRAME_CASE_COUNT", 513)

    with pytest.raises(holdout_frame.HoldoutFrameError, match="exceeds_limit"):
        holdout_frame.validate_frame(
            frame,
            protocol=protocol,
            preregistration_state=state,
        )


def test_frame_must_fit_the_protocol_commitment_payload_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protocol, state, frame = _fixture()
    validated = holdout_frame.validate_frame(
        frame,
        protocol=protocol,
        preregistration_state=state,
    )
    exact_size = len(canonical_json_bytes(validated))

    monkeypatch.setattr(
        holdout_protocol,
        "MAX_COMMITMENT_PAYLOAD_BYTES",
        exact_size,
    )
    assert holdout_frame.validate_frame(
        frame,
        protocol=protocol,
        preregistration_state=state,
    ) == validated

    monkeypatch.setattr(
        holdout_protocol,
        "MAX_COMMITMENT_PAYLOAD_BYTES",
        exact_size - 1,
    )
    with pytest.raises(
        holdout_frame.HoldoutFrameError,
        match="frame_exceeds_commitment_payload_limit",
    ):
        holdout_frame.validate_frame(
            frame,
            protocol=protocol,
            preregistration_state=state,
        )


def test_frame_rejects_container_subclasses_and_unknown_keys() -> None:
    protocol, state, frame = _fixture()
    top_subclass = _DictSubclass(frame)
    case_subclass = copy.deepcopy(frame)
    case_subclass["positive_cases"][0] = _DictSubclass(
        case_subclass["positive_cases"][0]
    )
    extra = copy.deepcopy(frame)
    extra["extra"] = False

    for mutated in (top_subclass, case_subclass, extra):
        with pytest.raises(holdout_frame.HoldoutFrameError):
            holdout_frame.validate_frame(
                mutated,
                protocol=protocol,
                preregistration_state=state,
            )


@pytest.mark.parametrize(
    ("seed", "key"),
    [
        (b"s" * 31, FRAME_KEY),
        (bytearray(SELECTION_SEED), FRAME_KEY),
        (SELECTION_SEED, b"f" * 31),
        (SELECTION_SEED, bytearray(FRAME_KEY)),
        (SELECTION_SEED, SELECTION_SEED),
    ],
)
def test_selection_rejects_invalid_or_shared_seed_and_key(
    seed: Any,
    key: Any,
) -> None:
    protocol, state, frame = _fixture()
    commitment = holdout_frame.create_frame_commitment(
        frame,
        protocol=protocol,
        preregistration_state=state,
        key=FRAME_KEY,
    )

    with pytest.raises(holdout_frame.HoldoutFrameError):
        holdout_frame.select_frame_cases(
            frame,
            protocol=protocol,
            preregistration_state=state,
            selection_seed=seed,
            frame_commitment_key=key,
            expected_frame_commitment=commitment,
        )


def test_frame_commitment_normalizes_invalid_key_error() -> None:
    protocol, state, frame = _fixture()

    with pytest.raises(
        holdout_frame.HoldoutFrameError,
        match="frame_commitment_input_invalid",
    ):
        holdout_frame.create_frame_commitment(
            frame,
            protocol=protocol,
            preregistration_state=state,
            key=b"short",
        )


def test_selection_rejects_commitment_mismatch_and_changes_with_seed() -> None:
    protocol, state, frame = _fixture()
    commitment, first = _selection(protocol, state, frame)
    second = holdout_frame.select_frame_cases(
        frame,
        protocol=protocol,
        preregistration_state=state,
        selection_seed=b"t" * 32,
        frame_commitment_key=FRAME_KEY,
        expected_frame_commitment=commitment,
    )
    assert first["selected_case_ids"] != second["selected_case_ids"]
    assert first["capability_boundary"]["seed_unbiased_verified"] is False

    with pytest.raises(holdout_frame.HoldoutFrameError, match="commitment_mismatch"):
        holdout_frame.select_frame_cases(
            frame,
            protocol=protocol,
            preregistration_state=state,
            selection_seed=SELECTION_SEED,
            frame_commitment_key=FRAME_KEY,
            expected_frame_commitment="hmac-sha256:" + "0" * 64,
        )


def test_selection_aborts_on_rank_collision(monkeypatch: pytest.MonkeyPatch) -> None:
    protocol, state, frame = _fixture()
    commitment = holdout_frame.create_frame_commitment(
        frame,
        protocol=protocol,
        preregistration_state=state,
        key=FRAME_KEY,
    )
    monkeypatch.setattr(
        holdout_frame,
        "_selection_rank",
        lambda *args, **kwargs: b"x" * 32,
    )

    with pytest.raises(holdout_frame.HoldoutFrameError, match="rank_collision"):
        holdout_frame.select_frame_cases(
            frame,
            protocol=protocol,
            preregistration_state=state,
            selection_seed=SELECTION_SEED,
            frame_commitment_key=FRAME_KEY,
            expected_frame_commitment=commitment,
        )


def test_selection_aborts_on_mixed_order_rank_collision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protocol, state, frame = _fixture()
    commitment = holdout_frame.create_frame_commitment(
        frame,
        protocol=protocol,
        preregistration_state=state,
        key=FRAME_KEY,
    )
    original_rank = holdout_frame._selection_rank

    def collide_only_in_mixed_order(*args: Any, **kwargs: Any) -> bytes:
        if kwargs["domain"] == "opaque_mixed_order":
            return b"x" * 32
        return original_rank(*args, **kwargs)

    monkeypatch.setattr(
        holdout_frame,
        "_selection_rank",
        collide_only_in_mixed_order,
    )

    with pytest.raises(
        holdout_frame.HoldoutFrameError,
        match="mixed_order_rank_collision",
    ):
        holdout_frame.select_frame_cases(
            frame,
            protocol=protocol,
            preregistration_state=state,
            selection_seed=SELECTION_SEED,
            frame_commitment_key=FRAME_KEY,
            expected_frame_commitment=commitment,
        )


def test_selection_projection_requires_exact_recomputation() -> None:
    protocol, state, frame = _fixture()
    commitment, receipt = _selection(protocol, state, frame)

    assert holdout_frame.validate_selection_projection(
        receipt,
        frame=frame,
        protocol=protocol,
        preregistration_state=state,
        selection_seed=SELECTION_SEED,
        frame_commitment_key=FRAME_KEY,
        expected_frame_commitment=commitment,
    ) == receipt

    reordered = copy.deepcopy(receipt)
    reordered["selected_case_ids"][0], reordered["selected_case_ids"][1] = (
        reordered["selected_case_ids"][1],
        reordered["selected_case_ids"][0],
    )
    authority = copy.deepcopy(receipt)
    authority["capability_boundary"]["production_promotion_gate_pass"] = True
    extra = copy.deepcopy(receipt)
    extra["selected_case_labels"] = ["positive"] * 257
    second_attempt = copy.deepcopy(receipt)
    second_attempt["selection_attempt"] = 1

    for forged in (reordered, authority, extra, second_attempt):
        with pytest.raises(holdout_frame.HoldoutFrameError):
            holdout_frame.validate_selection_projection(
                forged,
                frame=frame,
                protocol=protocol,
                preregistration_state=state,
                selection_seed=SELECTION_SEED,
                frame_commitment_key=FRAME_KEY,
                expected_frame_commitment=commitment,
            )
