from __future__ import annotations

import copy
import json
import stat
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from tools import benchmark_magma_solver_retrieval as benchmark


@pytest.fixture(scope="module")
def projection_documents() -> list[dict]:
    documents, _topology_digest = benchmark.load_projection_documents()
    return documents


@pytest.fixture(scope="module")
def corpus_value() -> dict:
    return json.loads(benchmark.DEFAULT_CORPUS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def validated(
    projection_documents: list[dict], corpus_value: dict
) -> tuple[dict, benchmark.ContentTokenizer, str]:
    return benchmark.validate_corpus(corpus_value, projection_documents)


def test_frozen_corpus_hash_shape_and_projection_binding(
    projection_documents: list[dict], corpus_value: dict
) -> None:
    corpus, _tokenizer, digest = benchmark.validate_corpus(
        corpus_value, projection_documents
    )

    assert digest == benchmark.EXPECTED_CORPUS_CANONICAL_SHA256
    assert len(projection_documents) == 22
    assert len(corpus["cases"]) == 44
    assert {case["query_id"] for case in corpus["cases"]} == {
        *(f"A{index:02d}" for index in range(1, 23)),
        *(f"S{index:02d}" for index in range(1, 23)),
    }
    solver_ids = {row["canonical_solver_id"] for row in projection_documents}
    cell_by_solver = {
        row["canonical_solver_id"]: row["cell_id"] for row in projection_documents
    }
    for stratum in ("anchored_natural", "semantic_zero_overlap"):
        cases = [case for case in corpus["cases"] if case["stratum"] == stratum]
        assert len(cases) == 22
        assert {case["expected_solver"] for case in cases} == solver_ids
        assert len({case["expected_solver"] for case in cases}) == 22
        assert all(
            case["expected_cell"] == cell_by_solver[case["expected_solver"]]
            for case in cases
        )


def test_label_blind_hex_router_axis_measures_current_coverage_without_search(
    validated: tuple[dict, benchmark.ContentTokenizer, str],
) -> None:
    corpus, _tokenizer, _digest = validated

    result = benchmark.evaluate_label_blind_hex_router(corpus["cases"])

    assert result["status"] == "MEASURED_BLOCKED"
    assert result["measurement_complete"] is True
    assert result["axis_role"] == "router_coverage_only"
    assert result["routing_policy"] == "solver_intent_origin_plus_ring1_v1"
    assert result["router_inputs"] == ["query_text"]
    assert result["router_input_fields"] == ["query"]
    assert result["router_forbidden_inputs"] == [
        "expected_solver",
        "expected_cell",
        "stratum",
        "query_id",
    ]
    assert result["labels_withheld_during_routing"] is True
    assert result["router_label_isolation_check_count"] == 44
    assert result["router_label_isolation_enforced"] is True
    assert result["topology_cell_count"] == 8
    assert result["actual_cell_local_faiss_search_evaluated"] is False
    assert result["cell_local_faiss_search_evaluated"] is False
    assert result["cell_local_faiss_search_executed_count"] == 0
    assert result["cell_local_search_latency_evaluated"] is False
    assert result["passed"] is False
    assert result["criteria"] == {
        "all_expected_cells_covered": False,
        "mean_cells_below_global_search": True,
        "max_cells_within_origin_ring1_policy": True,
    }
    assert result["metrics"] == {
        "all": {
            "cases": 44,
            "expected_cell_hits": 26,
            "expected_cell_coverage": 0.590909,
            "selected_cell_count": {"min": 4, "mean": 4.477273, "max": 5},
        },
        "anchored_natural": {
            "cases": 22,
            "expected_cell_hits": 12,
            "expected_cell_coverage": 0.545455,
            "selected_cell_count": {"min": 4, "mean": 4.318182, "max": 5},
        },
        "semantic_zero_overlap": {
            "cases": 22,
            "expected_cell_hits": 14,
            "expected_cell_coverage": 0.636364,
            "selected_cell_count": {"min": 4, "mean": 4.636364, "max": 5},
        },
    }
    assert result["strategy_comparison"]["origin_only"]["metrics"]["all"] == {
        "cases": 44,
        "expected_cell_hits": 12,
        "expected_cell_coverage": 0.272727,
        "selected_cell_count": {"min": 1, "mean": 1.0, "max": 1},
    }
    assert result["strategy_comparison"]["origin_plus_ring1"]["metrics"] == (
        result["metrics"]
    )
    assert result["strategy_comparison"]["intent_keyword_dual_ring1"][
        "metrics"
    ] == {
        "all": {
            "cases": 44,
            "expected_cell_hits": 30,
            "expected_cell_coverage": 0.681818,
            "selected_cell_count": {"min": 4, "mean": 5.272727, "max": 7},
        },
        "anchored_natural": {
            "cases": 22,
            "expected_cell_hits": 15,
            "expected_cell_coverage": 0.681818,
            "selected_cell_count": {"min": 4, "mean": 5.136364, "max": 7},
        },
        "semantic_zero_overlap": {
            "cases": 22,
            "expected_cell_hits": 15,
            "expected_cell_coverage": 0.681818,
            "selected_cell_count": {"min": 4, "mean": 5.409091, "max": 7},
        },
    }
    assert result["strategy_comparison"]["all_cells_reference"] == {
        "structural_reference_only": True,
        "metrics": {
            "all": {
                "cases": 44,
                "expected_cell_hits": 44,
                "expected_cell_coverage": 1.0,
                "selected_cell_count": {"min": 8, "mean": 8.0, "max": 8},
            },
            "anchored_natural": {
                "cases": 22,
                "expected_cell_hits": 22,
                "expected_cell_coverage": 1.0,
                "selected_cell_count": {"min": 8, "mean": 8.0, "max": 8},
            },
            "semantic_zero_overlap": {
                "cases": 22,
                "expected_cell_hits": 22,
                "expected_cell_coverage": 1.0,
                "selected_cell_count": {"min": 8, "mean": 8.0, "max": 8},
            },
        },
    }
    assert result["comparison_gaps"] == [
        "centroid_top_m_not_evaluated",
        "faiss_ivf_nprobe_not_evaluated",
        "all_cell_latency_scaling_threshold_not_evaluated",
    ]
    assert result["max_cells_nominated_per_query"] == 5
    assert result["max_cells_nominated_policy_limit"] == 5
    assert len(result["per_query"]) == 44
    assert result["runtime_authority_ready"] is False
    assert result["runtime_authority_granted"] is False
    assert result["production_promotion_gate_pass"] is False
    assert result["fallback_used"] is False


def test_hex_router_selection_is_independent_of_expected_labels(
    validated: tuple[dict, benchmark.ContentTokenizer, str],
) -> None:
    corpus, _tokenizer, _digest = validated
    original_cases = [dict(case) for case in corpus["cases"]]
    relabeled_cases = [dict(case) for case in original_cases]
    relabeled_cases[0]["expected_cell"] = "general"

    original = benchmark.evaluate_label_blind_hex_router(original_cases)[
        "per_query"
    ][0]
    relabeled = benchmark.evaluate_label_blind_hex_router(relabeled_cases)[
        "per_query"
    ][0]

    routing_fields = (
        "classified_intent",
        "origin_cell",
        "origin_method",
        "selected_cells",
        "selected_cell_count",
    )
    assert {key: original[key] for key in routing_fields} == {
        key: relabeled[key] for key in routing_fields
    }


def test_hex_router_classifier_receives_only_query_text(
    validated: tuple[dict, benchmark.ContentTokenizer, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus, _tokenizer, _digest = validated
    observed: list[str] = []
    classify = benchmark.SolverRouter.classify_intent

    def recording_classifier(query: str) -> str:
        observed.append(query)
        return classify(query)

    monkeypatch.setattr(
        benchmark.SolverRouter, "classify_intent", recording_classifier
    )

    benchmark.evaluate_label_blind_hex_router(corpus["cases"])

    assert observed == [case["query"] for case in corpus["cases"]]
    assert all(type(query) is str for query in observed)


@pytest.mark.parametrize(
    "forbidden_field",
    benchmark._ROUTER_FORBIDDEN_INPUTS,
)
def test_hex_router_runtime_boundary_rejects_label_bearing_input(
    forbidden_field: str,
) -> None:
    with pytest.raises(
        benchmark.BenchmarkContractError,
        match="hex_router_input_keys_mismatch",
    ):
        benchmark._route_label_blind_query(
            {
                "query": "Will the hive remain safe?",
                forbidden_field: "forbidden",
            },
            benchmark.HexCellTopology(),
        )


def test_no_write_summary_exposes_blocked_hex_router_axis(capsys) -> None:
    exit_code = benchmark.main(["--no-write", "--skip-vector"])

    assert exit_code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["schema_version"] == "wd.magma.solver_retrieval_benchmark.v3"
    assert summary["hex_cell_router_axis"]["axis_role"] == "router_coverage_only"
    assert summary["hex_cell_router_axis"]["status"] == "MEASURED_BLOCKED"
    assert summary["hex_cell_router_axis"]["passed"] is False
    assert summary["hex_cell_router_axis"]["metrics"]["all"][
        "expected_cell_hits"
    ] == 26
    assert summary["hex_cell_router_axis"]["strategy_comparison"]["origin_only"][
        "metrics"
    ]["all"]["expected_cell_hits"] == 12
    assert summary["hex_cell_router_axis"]["strategy_comparison"][
        "all_cells_reference"
    ]["metrics"]["all"]["expected_cell_hits"] == 44
    assert summary["hex_cell_router_axis"][
        "actual_cell_local_faiss_search_evaluated"
    ] is False
    assert summary["hex_cell_router_axis"][
        "cell_local_faiss_search_executed_count"
    ] == 0
    assert summary["hex_cell_router_axis"]["router_input_fields"] == ["query"]
    assert summary["hex_cell_router_axis"][
        "router_label_isolation_check_count"
    ] == 44
    assert summary["hex_cell_router_axis"][
        "router_label_isolation_enforced"
    ] is True
    assert summary["hex_cell_router_axis"]["runtime_authority_granted"] is False
    assert summary["hex_cell_router_axis"][
        "production_promotion_gate_pass"
    ] is False
    assert summary["runtime_authority_ready"] is False


def test_semantic_corpus_mutation_fails_frozen_hash_before_retrieval(
    projection_documents: list[dict], corpus_value: dict
) -> None:
    mutated = copy.deepcopy(corpus_value)
    mutated["cases"][22]["query"] += " mite"

    with pytest.raises(benchmark.BenchmarkContractError, match="corpus_hash_mismatch"):
        benchmark.validate_corpus(mutated, projection_documents)


def test_overlap_and_label_guards_are_rederived(
    projection_documents: list[dict], corpus_value: dict
) -> None:
    overlap_mutation = copy.deepcopy(corpus_value)
    overlap_mutation["cases"][22]["query"] += " mite"
    with pytest.raises(
        benchmark.BenchmarkContractError, match="semantic_zero_overlap_violation"
    ):
        benchmark.validate_corpus(
            overlap_mutation,
            projection_documents,
            expected_sha256=benchmark.canonical_json_sha256(overlap_mutation),
        )

    leakage_mutation = copy.deepcopy(corpus_value)
    leakage_mutation["cases"][22]["query"] += " autumn preparation"
    with pytest.raises(benchmark.BenchmarkContractError, match="query_label_leakage"):
        benchmark.validate_corpus(
            leakage_mutation,
            projection_documents,
            expected_sha256=benchmark.canonical_json_sha256(leakage_mutation),
        )

    other_solver_leakage = copy.deepcopy(corpus_value)
    other_solver_leakage["cases"][22]["query"] += " battery discharge"
    with pytest.raises(benchmark.BenchmarkContractError, match="query_label_leakage"):
        benchmark.validate_corpus(
            other_solver_leakage,
            projection_documents,
            expected_sha256=benchmark.canonical_json_sha256(other_solver_leakage),
        )

    stratum_mutation = copy.deepcopy(corpus_value)
    stratum_mutation["cases"][22]["stratum"] = "anchored_natural"
    with pytest.raises(benchmark.BenchmarkContractError):
        benchmark.validate_corpus(
            stratum_mutation,
            projection_documents,
            expected_sha256=benchmark.canonical_json_sha256(stratum_mutation),
        )


def test_lexical_baseline_is_deterministic_and_exposes_semantic_failure(
    projection_documents: list[dict],
    validated: tuple[dict, benchmark.ContentTokenizer, str],
) -> None:
    corpus, tokenizer, _digest = validated
    queries = [case["query"] for case in corpus["cases"]]

    first = benchmark.run_lexical_benchmark(
        projection_documents, queries, corpus["cases"], tokenizer
    )
    second = benchmark.run_lexical_benchmark(
        projection_documents, queries, corpus["cases"], tokenizer
    )

    assert first["metrics"] == second["metrics"]
    assert first["metrics"]["all"] == {
        "cases": 44,
        "top1_accuracy": 0.5,
        "recall_at_5": 0.568182,
        "nonempty_rate": 1.0,
    }
    assert first["metrics"]["semantic_zero_overlap"]["top1_accuracy"] == 0.0
    assert first["fallback_used"] is False


@pytest.mark.parametrize(
    ("values", "rows", "dimension", "message"),
    [
        ([], 1, 2, "must_be_2d"),
        ([[0.0, 0.0]], 1, 2, "zero_or_invalid_norm"),
        ([[float("nan"), 1.0]], 1, 2, "nonfinite"),
        ([[float("inf"), 1.0]], 1, 2, "nonfinite"),
        ([[1.0, 2.0, 3.0]], 1, 2, "shape_mismatch"),
        ([[1.0, 2.0]], 2, 2, "shape_mismatch"),
        ([["not-a-number", 2.0]], 1, 2, "not_numeric_rectangular"),
        ([[1.0], [1.0, 2.0]], 2, 2, "not_numeric_rectangular"),
    ],
)
def test_invalid_embeddings_fail_closed(
    values: object, rows: int, dimension: int, message: str
) -> None:
    with pytest.raises(benchmark.EmbeddingValidationError, match=message):
        benchmark.normalize_embedding_matrix(
            values,
            expected_rows=rows,
            expected_dimension=dimension,
            label="test_vectors",
        )


def test_valid_embeddings_are_finite_normalized_float32() -> None:
    matrix = benchmark.normalize_embedding_matrix(
        [[3.0, 4.0], [1.0, -1.0]],
        expected_rows=2,
        expected_dimension=2,
        label="test_vectors",
    )

    assert matrix.dtype == np.float32
    assert matrix.flags.c_contiguous
    assert np.isfinite(matrix).all()
    np.testing.assert_allclose(np.linalg.norm(matrix, axis=1), [1.0, 1.0])


@pytest.mark.parametrize("response_model", [None, "wrong-model:latest"])
def test_embedding_response_model_must_match_requested_profile(
    response_model: str | None,
) -> None:
    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            payload = {"embeddings": [[3.0, 4.0]]}
            if response_model is not None:
                payload["model"] = response_model
            return payload

    class _Client:
        def post(self, *_args: object, **_kwargs: object) -> _Response:
            return _Response()

    profile = benchmark.EmbeddingProfile(
        name="test",
        model_id="expected-model:latest",
        model_digest="a" * 64,
        dimension=2,
        document_prefix="",
        query_prefix="",
    )
    embedder = object.__new__(benchmark.OllamaEmbeddingClient)
    embedder._client = _Client()  # type: ignore[attr-defined]

    with pytest.raises(
        benchmark.EmbeddingValidationError, match="response_model_mismatch"
    ):
        embedder.embed(["query"], profile, label="test")


def test_equal_score_ranking_is_input_order_independent() -> None:
    assert [
        ["zeta", "alpha"][index]
        for index in benchmark.rank_score_row(
            np.array([0.5, 0.5], dtype=np.float32), ["zeta", "alpha"]
        )
    ] == ["alpha", "zeta"]
    assert [
        ["alpha", "zeta"][index]
        for index in benchmark.rank_score_row(
            np.array([0.5, 0.5], dtype=np.float32), ["alpha", "zeta"]
        )
    ] == ["alpha", "zeta"]


class _SpyEmbedder:
    def __init__(self, document_count: int, dimension: int) -> None:
        self.document_count = document_count
        self.dimension = dimension
        self.inputs: list[list[str]] = []
        self.verified: list[str] = []

    def verify_profile(self, profile: benchmark.EmbeddingProfile) -> dict:
        self.verified.append(profile.name)
        return {
            "provider": "spy",
            "requested_model_tag": profile.model_id,
            "catalog_digest": profile.model_digest,
        }

    def embed(
        self,
        texts: list[str],
        profile: benchmark.EmbeddingProfile,
        *,
        label: str,
    ) -> np.ndarray:
        del profile, label
        self.inputs.append(list(texts))
        if len(texts) == self.document_count:
            return np.eye(self.document_count, self.dimension, dtype=np.float32)
        vector = np.ones((len(texts), self.dimension), dtype=np.float32)
        return benchmark.normalize_embedding_matrix(
            vector,
            expected_rows=len(texts),
            expected_dimension=self.dimension,
            label="spy_vectors",
        )


def test_provider_identity_match_requires_exact_live_catalog_result() -> None:
    profile = benchmark.EmbeddingProfile(
        name="strict",
        model_id="strict-model",
        model_digest="a" * 64,
        dimension=2,
        document_prefix="doc: ",
        query_prefix="query: ",
    )
    valid = {
        "provider": "ollama",
        "requested_model_tag": profile.model_id,
        "catalog_digest": profile.model_digest,
    }

    assert benchmark.provider_identity_matches_profile(valid, profile) is True
    assert benchmark.provider_identity_matches_profile(
        {**valid, "catalog_digest": "b" * 64}, profile
    ) is False
    assert benchmark.provider_identity_matches_profile(
        {**valid, "unverified": True}, profile
    ) is False


def test_retrieval_receives_only_documents_and_query_text(
    projection_documents: list[dict],
    validated: tuple[dict, benchmark.ContentTokenizer, str],
) -> None:
    corpus, tokenizer, _digest = validated
    queries = [case["query"] for case in corpus["cases"]]
    profile = benchmark.EmbeddingProfile(
        name="spy",
        model_id="spy-model",
        model_digest="a" * 64,
        dimension=len(projection_documents),
        document_prefix="doc: ",
        query_prefix="query: ",
    )
    embedder = _SpyEmbedder(len(projection_documents), profile.dimension)

    result = benchmark.run_vector_benchmark(
        projection_documents,
        queries,
        corpus["cases"],
        tokenizer,
        profile,
        embedder,  # type: ignore[arg-type]
    )

    assert embedder.verified == ["spy", "spy"]
    assert embedder.inputs[0] == [
        profile.document_prefix + row["embedding_text"]
        for row in projection_documents
    ]
    assert embedder.inputs[1:] == [[profile.query_prefix + query] for query in queries]
    assert result["measurement_complete"] is True
    assert result["fallback_used"] is False
    assert (
        result["provider_identity_evidence"][
            "catalog_contract_verified_before_embedding"
        ]
        is True
    )
    assert (
        result["provider_identity_evidence"][
            "catalog_contract_verified_after_embedding"
        ]
        is True
    )
    assert result["provider_identity_evidence"]["response_digest_attested"] is False


def test_evaluation_labels_cannot_change_existing_rankings(
    projection_documents: list[dict],
    validated: tuple[dict, benchmark.ContentTokenizer, str],
) -> None:
    corpus, tokenizer, _digest = validated
    cases = corpus["cases"][:2]
    solver_ids = [row["canonical_solver_id"] for row in projection_documents]
    scores = np.zeros((2, len(solver_ids)), dtype=np.float32)
    scores[:, 0] = 1.0
    rankings = [benchmark.rank_score_row(row, solver_ids) for row in scores]
    relabeled = copy.deepcopy(cases)
    relabeled[0]["expected_solver"] = cases[1]["expected_solver"]

    first = benchmark._evaluate_rankings(
        rankings,
        scores,
        solver_ids,
        projection_documents,
        cases,
        tokenizer,
        [0.1, 0.1],
    )
    second = benchmark._evaluate_rankings(
        rankings,
        scores,
        solver_ids,
        projection_documents,
        relabeled,
        tokenizer,
        [0.1, 0.1],
    )

    assert [row["chosen_solver"] for row in first["per_query"]] == [
        row["chosen_solver"] for row in second["per_query"]
    ]
    assert [row["top_k"] for row in first["per_query"]] == [
        row["top_k"] for row in second["per_query"]
    ]


def test_existing_index_absence_is_not_rebuilt_or_fallback(tmp_path: Path) -> None:
    vector_root = tmp_path / "vector"

    result = benchmark.existing_index_preflight(vector_root)

    assert result == {
        "axis": "A1_existing_index",
        "status": "NOT_AVAILABLE_NOT_RUN",
        "available": False,
        "reason_codes": ["vector_root_missing"],
        "selected_artifact": None,
        "queries_attempted": 0,
        "artifacts_created": 0,
        "artifacts_imported": 0,
        "fallback_used": False,
    }
    assert not vector_root.exists()


def test_existing_projection_root_is_not_misreported_as_searchable(
    tmp_path: Path,
) -> None:
    vector_root = tmp_path / "vector"
    vector_root.mkdir()

    result = benchmark.existing_index_preflight(vector_root)

    assert result["available"] is False
    assert result["reason_codes"] == ["searchable_materialization_contract_absent"]
    assert result["queries_attempted"] == 0


def test_backend_unavailable_is_not_zero_accuracy() -> None:
    profile = benchmark.EMBEDDING_PROFILES["nomic"]
    result = benchmark.unavailable_vector_result(
        profile, 44, "embedding_backend_unavailable"
    )

    assert result["measurement_status"] == "unavailable"
    assert result["measurement_complete"] is False
    assert result["cases_total"] == 44
    assert result["cases_evaluated"] == 0
    assert result["metrics"] is None
    assert result["per_query"] == []
    assert result["fallback_used"] is False
    assert benchmark.differential_gate({"metrics": {}}, result) == {
        "passed": False,
        "criteria": {"measurement_complete": False},
        "reason": "vector_measurement_unavailable",
    }


@pytest.mark.parametrize(
    "raw_path",
    [
        "report.json",
        "../report.json",
        ".codex-audit/../report.json",
        ".codex-audit-evil/report.json",
    ],
)
def test_output_must_resolve_beneath_codex_audit(
    tmp_path: Path, raw_path: str
) -> None:
    (tmp_path / ".codex-audit").mkdir()

    with pytest.raises(benchmark.BenchmarkContractError):
        benchmark.resolve_audit_output(raw_path, repo_root=tmp_path)


def test_output_accepts_new_repo_relative_audit_path(tmp_path: Path) -> None:
    (tmp_path / ".codex-audit").mkdir()

    output = benchmark.resolve_audit_output(
        ".codex-audit/magma-retrieval/report.json", repo_root=tmp_path
    )

    assert output == (tmp_path / ".codex-audit/magma-retrieval/report.json").resolve()
    assert not output.parent.exists()


def test_output_rejects_absolute_path(tmp_path: Path) -> None:
    (tmp_path / ".codex-audit").mkdir()

    with pytest.raises(
        benchmark.BenchmarkContractError, match="output_path_must_be_repo_relative"
    ):
        benchmark.resolve_audit_output(str(tmp_path / "absolute.json"), repo_root=tmp_path)


def test_output_rejects_symlinked_parent_that_escapes_audit_root(
    tmp_path: Path,
) -> None:
    audit_root = tmp_path / ".codex-audit"
    outside = tmp_path / "outside"
    audit_root.mkdir()
    outside.mkdir()
    link = audit_root / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this Windows host")

    with pytest.raises(
        benchmark.BenchmarkContractError,
        match="output_path_must_be_beneath_codex_audit",
    ):
        benchmark.resolve_audit_output(
            ".codex-audit/escape/report.json", repo_root=tmp_path
        )


def test_output_rejects_symlinked_audit_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    audit_root = tmp_path / ".codex-audit"
    try:
        audit_root.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this Windows host")

    with pytest.raises(
        benchmark.BenchmarkContractError,
        match="codex_audit_root_must_not_be_link",
    ):
        benchmark.resolve_audit_output(
            ".codex-audit/report.json", repo_root=tmp_path
        )


def test_link_guard_detects_windows_reparse_point_without_is_junction() -> None:
    class Pre312Junction:
        def lstat(self) -> SimpleNamespace:
            return SimpleNamespace(
                st_mode=stat.S_IFDIR,
                st_file_attributes=0x0400,
            )

    assert benchmark._is_link_like(Pre312Junction()) is True


def test_link_guard_fails_closed_when_metadata_is_unavailable() -> None:
    class UnreadablePath:
        def lstat(self) -> None:
            raise PermissionError("denied")

    assert benchmark._is_link_like(UnreadablePath()) is True


def test_output_rejects_link_like_audit_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit_root = tmp_path / ".codex-audit"
    audit_root.mkdir()
    real_is_link_like = benchmark._is_link_like
    monkeypatch.setattr(
        benchmark,
        "_is_link_like",
        lambda path: path == audit_root or real_is_link_like(path),
    )

    with pytest.raises(
        benchmark.BenchmarkContractError,
        match="codex_audit_root_must_not_be_link",
    ):
        benchmark.resolve_audit_output(
            ".codex-audit/report.json", repo_root=tmp_path
        )


def test_gate_requires_semantic_lift_and_keeps_authority_separate() -> None:
    baseline_rows = [
        {
            "query_id": f"S{index:02d}",
            "stratum": "semantic_zero_overlap",
            "correct_at_1": False,
            "expected_rank_at_5": None if index > 3 else index,
        }
        for index in range(1, 23)
    ]
    baseline_rows += [
        {
            "query_id": f"A{index:02d}",
            "stratum": "anchored_natural",
            "correct_at_1": True,
            "expected_rank_at_5": 1,
        }
        for index in range(1, 23)
    ]
    candidate_rows = [
        {
            "query_id": f"S{index:02d}",
            "stratum": "semantic_zero_overlap",
            "correct_at_1": index <= 14,
            "expected_rank_at_5": index if index <= 20 else None,
        }
        for index in range(1, 23)
    ]
    candidate_rows += copy.deepcopy(baseline_rows[22:])
    a0 = {
        "metrics": {
            "all": {"top1_accuracy": 0.5, "recall_at_5": 0.6},
            "anchored_natural": {
                "cases": 22,
                "top1_accuracy": 1.0,
                "recall_at_5": 1.0,
            },
            "semantic_zero_overlap": {"top1_accuracy": 0.0, "recall_at_5": 0.1},
        },
        "per_query": baseline_rows,
    }
    a2 = {
        "measurement_complete": True,
        "metrics": {
            "all": {
                "top1_accuracy": 0.7,
                "recall_at_5": 0.8,
                "nonempty_rate": 1.0,
            },
            "anchored_natural": {
                "cases": 22,
                "top1_accuracy": 1.0,
                "recall_at_5": 1.0,
            },
            "semantic_zero_overlap": {
                "top1_accuracy": 0.4,
                "recall_at_5": 0.7,
            },
        },
        "latency_ms": {"search": {"p95": 0.1}},
        "per_query": candidate_rows,
        "provider_identity_evidence": {
            "catalog_contract_verified_before_embedding": True,
            "catalog_contract_verified_after_embedding": True,
        },
    }

    gate = benchmark.differential_gate(a0, a2)

    assert gate["passed"] is True
    assert gate["deltas"]["semantic_top1"] == 0.4
    assert gate["paired_evidence"]["semantic_top1"]["candidate_wins"] == 14


def test_gate_rejects_one_query_lift_as_insufficient_evidence() -> None:
    baseline_rows = [
        {
            "query_id": f"S{index:02d}",
            "stratum": "semantic_zero_overlap",
            "correct_at_1": False,
            "expected_rank_at_5": None,
        }
        for index in range(1, 23)
    ]
    baseline_rows += [
        {
            "query_id": f"A{index:02d}",
            "stratum": "anchored_natural",
            "correct_at_1": True,
            "expected_rank_at_5": 1,
        }
        for index in range(1, 23)
    ]
    candidate_rows = copy.deepcopy(baseline_rows)
    candidate_rows[0]["correct_at_1"] = True
    candidate_rows[0]["expected_rank_at_5"] = 1
    a0 = {
        "metrics": {
            "all": {"top1_accuracy": 0.5, "recall_at_5": 0.5},
            "anchored_natural": {
                "cases": 22,
                "top1_accuracy": 1.0,
                "recall_at_5": 1.0,
            },
            "semantic_zero_overlap": {"top1_accuracy": 0.0, "recall_at_5": 0.0},
        },
        "per_query": baseline_rows,
    }
    a2 = {
        "measurement_complete": True,
        "metrics": {
            "all": {
                "top1_accuracy": 0.500001,
                "recall_at_5": 0.500001,
                "nonempty_rate": 1.0,
            },
            "anchored_natural": {
                "cases": 22,
                "top1_accuracy": 1.0,
                "recall_at_5": 1.0,
            },
            "semantic_zero_overlap": {
                "top1_accuracy": 1 / 22,
                "recall_at_5": 1 / 22,
            },
        },
        "latency_ms": {"search": {"p95": 0.1}},
        "per_query": candidate_rows,
        "provider_identity_evidence": {
            "catalog_contract_verified_before_embedding": True,
            "catalog_contract_verified_after_embedding": True,
        },
    }

    gate = benchmark.differential_gate(a0, a2)

    assert gate["passed"] is False
    assert gate["criteria"]["semantic_top1_paired_evidence"] is False
    assert gate["paired_evidence"]["semantic_top1"]["one_sided_exact_sign_p"] == 0.5


def test_gate_rejects_anchored_collapse_despite_semantic_gain() -> None:
    baseline_rows = [
        {
            "query_id": f"S{index:02d}",
            "stratum": "semantic_zero_overlap",
            "correct_at_1": False,
            "expected_rank_at_5": None,
        }
        for index in range(1, 23)
    ]
    baseline_rows += [
        {
            "query_id": f"A{index:02d}",
            "stratum": "anchored_natural",
            "correct_at_1": True,
            "expected_rank_at_5": 1,
        }
        for index in range(1, 23)
    ]
    candidate_rows = [
        {
            **row,
            "correct_at_1": True,
            "expected_rank_at_5": 1,
        }
        for row in baseline_rows[:22]
    ]
    candidate_rows += [
        {
            **row,
            "correct_at_1": index == 0,
            "expected_rank_at_5": 1 if index < 3 else None,
        }
        for index, row in enumerate(baseline_rows[22:])
    ]
    a0 = {
        "metrics": {
            "all": {"top1_accuracy": 0.5, "recall_at_5": 0.5},
            "anchored_natural": {
                "cases": 22,
                "top1_accuracy": 1.0,
                "recall_at_5": 1.0,
            },
            "semantic_zero_overlap": {"top1_accuracy": 0.0, "recall_at_5": 0.0},
        },
        "per_query": baseline_rows,
    }
    a2 = {
        "measurement_complete": True,
        "metrics": {
            "all": {
                "top1_accuracy": 23 / 44,
                "recall_at_5": 25 / 44,
                "nonempty_rate": 1.0,
            },
            "anchored_natural": {
                "cases": 22,
                "top1_accuracy": 1 / 22,
                "recall_at_5": 3 / 22,
            },
            "semantic_zero_overlap": {
                "top1_accuracy": 1.0,
                "recall_at_5": 1.0,
            },
        },
        "latency_ms": {"search": {"p95": 0.1}},
        "per_query": candidate_rows,
        "provider_identity_evidence": {
            "catalog_contract_verified_before_embedding": True,
            "catalog_contract_verified_after_embedding": True,
        },
    }

    gate = benchmark.differential_gate(a0, a2)

    assert gate["passed"] is False
    assert gate["criteria"]["anchored_top1_net_loss_at_most_one_case"] is False
    assert gate["criteria"]["anchored_recall5_non_regression"] is False


def test_duplicate_profiles_fail_before_embedding(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    class _UnexpectedClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            nonlocal called
            called = True

    monkeypatch.setattr(benchmark, "OllamaEmbeddingClient", _UnexpectedClient)

    exit_code = benchmark.main(
        ["--no-write", "--profile", "nomic", "--profile", "nomic"]
    )

    assert exit_code == 2
    assert called is False
