from __future__ import annotations

import inspect
from types import SimpleNamespace

import numpy as np
import pytest

from tools import benchmark_magma_faiss_candidate_latency as latency
from tools import benchmark_magma_solver_retrieval as retrieval_benchmark


SNAPSHOT_ID = "faisscand_" + "a" * 64
SESSION_ID = "faisssession_" + "b" * 32
REOPEN_SESSION_ID = "faisssession_" + "c" * 32


def _row(index: int = 0, **overrides: object) -> dict:
    row = {
        "canonical_solver_id": f"solver_{index}",
        "cell_id": "one",
        "projection_id": "sha256:" + f"{index + 1:x}" * 64,
        "score": 1.0 - index / 10.0,
        "snapshot_id": SNAPSHOT_ID,
        "verification_session_id": SESSION_ID,
        "source_commit_reverified": True,
        "source_reverification_scope": "session_open",
        "source_reverified_during_search_call": False,
        "runtime_authority_granted": False,
        "receipt_authenticity_verified": False,
        "solver_outcome_verified": False,
    }
    row.update(overrides)
    return row


class _Session:
    snapshot_id = SNAPSHOT_ID

    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = rows if rows is not None else [_row(index) for index in range(5)]
        self.calls = 0
        self.closed = False

    def search(self, _vector: np.ndarray, *, k: int) -> list[dict]:
        self.calls += 1
        return self.rows[:k]

    def close(self) -> None:
        self.closed = True


def _clock(*durations_ms: float):
    values: list[int] = []
    cursor = 0
    for duration in durations_ms:
        values.extend([cursor, cursor + int(duration * 1_000_000)])
        cursor += int(duration * 1_000_000) + 1_000_000
    iterator = iter(values)
    return lambda: next(iterator)


def test_measurement_times_only_search_and_rederives_percentiles() -> None:
    session = _Session()
    result = latency.measure_verified_global_search(
        session,
        np.ones((2, 3), dtype=np.float32),
        expected_snapshot_id=SNAPSHOT_ID,
        k=1,
        repetitions=2,
        warmup_rounds=1,
        clock_ns=_clock(1.0, 2.0, 3.0, 4.0),
    )

    assert session.calls == 6
    assert result["warmup_search_count"] == 2
    assert result["search_execution_count"] == 4
    assert result["verification_session_id"] == SESSION_ID
    assert result["ranking_repeat_check_count"] == 2
    assert result["ranking_repeat_mismatch_count"] == 0
    assert result["latency_ms"]["search"] == {
        "p50": 2.5,
        "p95": 3.85,
        "p99": 3.97,
        "max": 4.0,
        "mean": 2.5,
    }
    assert result["latency_ms"]["per_query_p95"] == {
        "min": 2.9,
        "median": 3.4,
        "max": 3.9,
    }


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"snapshot_id": "faisscand_" + "c" * 64}, "snapshot_binding_mismatch"),
        ({"verification_session_id": "forged"}, "session_binding_invalid"),
        ({"source_commit_reverified": False}, "evidence_boundary_invalid"),
        ({"runtime_authority_granted": True}, "evidence_boundary_invalid"),
    ],
)
def test_measurement_rejects_invalid_search_evidence(
    overrides: dict,
    error: str,
) -> None:
    with pytest.raises(latency.CandidateLatencyContractError, match=error):
        latency.measure_verified_global_search(
            _Session([_row(**overrides)]),
            np.ones((1, 3), dtype=np.float32),
            expected_snapshot_id=SNAPSHOT_ID,
            k=1,
            repetitions=1,
            warmup_rounds=0,
            clock_ns=_clock(1.0),
        )


def test_measurement_rejects_short_top_k_as_partial_search() -> None:
    with pytest.raises(
        latency.CandidateLatencyContractError,
        match="verified_search_rows_invalid",
    ):
        latency.measure_verified_global_search(
            _Session([_row(index) for index in range(4)]),
            np.ones((1, 3), dtype=np.float32),
            expected_snapshot_id=SNAPSHOT_ID,
            k=5,
            repetitions=1,
            warmup_rounds=0,
            clock_ns=_clock(1.0),
        )


def test_measurement_counts_ranking_change_across_repetitions() -> None:
    class _ChangingSession(_Session):
        def search(self, _vector: np.ndarray, *, k: int) -> list[dict]:
            self.calls += 1
            return [_row(score=1.0 if self.calls == 1 else 0.9)][:k]

    result = latency.measure_verified_global_search(
        _ChangingSession(),
        np.ones((1, 3), dtype=np.float32),
        expected_snapshot_id=SNAPSHOT_ID,
        k=1,
        repetitions=2,
        warmup_rounds=0,
        clock_ns=_clock(1.0, 1.0),
    )

    assert result["ranking_repeat_check_count"] == 1
    assert result["ranking_repeat_mismatch_count"] == 1


def test_quality_scores_labels_only_after_rankings_exist() -> None:
    quality = latency.evaluate_persisted_ranking_quality(
        [
            (
                ("solver_0", "one", "sha256:" + "1" * 64, 0.9),
                ("solver_1", "two", "sha256:" + "2" * 64, 0.8),
            )
        ],
        [
            {
                "query_id": "S01",
                "stratum": "semantic_zero_overlap",
                "query": "label-blind query",
                "expected_solver": "solver_1",
                "expected_cell": "two",
            }
        ],
    )

    assert quality["label_scoring_after_search"] is True
    assert quality["label_scoring_isolation_check_count"] == 1
    assert quality["label_scoring_isolation_enforced"] is True
    assert quality["retriever_input_fields"] == ["query"]
    assert quality["metrics"]["all"]["top1_hits"] == 0
    assert quality["metrics"]["all"]["recall_at_5_hits"] == 1
    assert quality["per_query"][0]["expected_rank_at_5"] == 2


def test_label_blind_query_builder_rejects_adjacent_case_objects() -> None:
    with pytest.raises(
        latency.CandidateLatencyContractError,
        match="label_blind_query_inputs_invalid",
    ):
        latency._build_label_blind_query_inputs(
            [{"query": "text", "expected_solver": "solver_0"}],
            query_prefix="query: ",
        )


def test_numpy_proxy_parity_rederives_exact_rankings_and_scores() -> None:
    cells = [
        {
            "manifest": {"cell_id": "one"},
            "vectors": np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32),
            "rows": [
                {
                    "canonical_solver_id": "solver_a",
                    "projection_id": "sha256:" + "1" * 64,
                },
                {
                    "canonical_solver_id": "solver_b",
                    "projection_id": "sha256:" + "2" * 64,
                },
            ],
        }
    ]
    persisted = [
        (
            ("solver_a", "one", "sha256:" + "1" * 64, 1.0),
            ("solver_b", "one", "sha256:" + "2" * 64, 0.0),
        )
    ]

    result = latency.evaluate_numpy_proxy_parity(
        cells,
        np.asarray([[1.0, 0.0]], dtype=np.float32),
        persisted,
        k=2,
        score_tolerance=1.0e-6,
    )

    assert result["ranking_comparison_count"] == 1
    assert result["ranking_mismatch_count"] == 0
    assert result["score_comparison_count"] == 2
    assert result["max_abs_score_error"] == 0.0
    assert result["passed"] is True


def _install_live_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    durations_ms: tuple[float, ...],
) -> _Session:
    profile = retrieval_benchmark.EmbeddingProfile(
        name="test",
        model_id="test:latest",
        model_digest="d" * 64,
        dimension=3,
        document_prefix="doc: ",
        query_prefix="query: ",
    )
    identity = {
        "provider": "ollama",
        "requested_model_tag": profile.model_id,
        "catalog_digest": profile.model_digest,
    }
    request = SimpleNamespace(
        embedding_contract={},
        cells=(("one", "proj_" + "1" * 64), ("two", "proj_" + "2" * 64)),
    )
    manifest = {
        "snapshot_id": SNAPSHOT_ID,
        "topology_digest": "sha256:" + "e" * 64,
        "cells": [{"cell_id": "one"}, {"cell_id": "two"}],
        "total_vector_count": 5,
        "faiss_version": "test-faiss",
        "faiss_compile_options": "test-options",
        "faiss_binary_set_sha256": "sha256:" + "f" * 64,
        "persisted_parity": {"score_tolerance": 1.0e-6},
    }
    session = _Session()
    reopen_session = _Session(
        [
            _row(index, verification_session_id=REOPEN_SESSION_ID)
            for index in range(5)
        ]
    )
    sessions = iter([session, reopen_session])

    class _Embedder:
        def __init__(self, _url: str, *, timeout_seconds: float) -> None:
            assert timeout_seconds == 120.0

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def verify_profile(self, observed_profile):
            assert observed_profile is profile
            return identity.copy()

        def embed(self, texts, observed_profile, *, label: str):
            assert texts == ["query: first", "query: second"]
            assert observed_profile is profile
            assert label == "candidate_latency_query_embeddings"
            return np.ones((2, 3), dtype=np.float32)

    monkeypatch.setattr(
        latency,
        "_load_query_suite",
        lambda *_args, **_kwargs: {
            "queries": ["first", "second"],
            "cases": [
                {
                    "query_id": "A01",
                    "stratum": "anchored_natural",
                    "query": "first",
                    "expected_solver": "solver_0",
                    "expected_cell": "one",
                },
                {
                    "query_id": "S01",
                    "stratum": "semantic_zero_overlap",
                    "query": "second",
                    "expected_solver": "solver_1",
                    "expected_cell": "one",
                },
            ],
            "documents": [],
            "tokenizer": object(),
            "corpus": {
                "path": "frozen.json",
                "canonical_sha256": "sha256:" + "3" * 64,
                "raw_sha256": "sha256:" + "4" * 64,
                "cases": 2,
                "solver_coverage": 3,
                "topology_digest": "sha256:" + "e" * 64,
                "router_labels_sent_to_search": False,
            },
        },
    )
    monkeypatch.setattr(
        latency.candidate_snapshot,
        "load_candidate_request",
        lambda *_args, **_kwargs: request,
    )
    monkeypatch.setattr(
        latency.candidate_snapshot,
        "load_verified_candidate_snapshot",
        lambda *_args, **_kwargs: {"manifest": manifest, "cells": []},
    )
    monkeypatch.setattr(
        latency.candidate_snapshot,
        "open_verified_candidate_search_session",
        lambda *_args, **_kwargs: next(sessions),
    )
    monkeypatch.setattr(
        latency.candidate_snapshot,
        "_profile_from_contract",
        lambda _contract: profile,
    )
    monkeypatch.setattr(
        latency.retrieval_benchmark,
        "OllamaEmbeddingClient",
        _Embedder,
    )
    monkeypatch.setattr(
        latency.retrieval_benchmark,
        "provider_identity_matches_profile",
        lambda value, observed_profile: value == identity and observed_profile is profile,
    )
    monkeypatch.setattr(
        latency.retrieval_benchmark,
        "run_lexical_benchmark",
        lambda *_args, **_kwargs: {"metrics": {"baseline": True}},
    )

    def _differential_gate(baseline, candidate):
        assert baseline == {"metrics": {"baseline": True}}
        assert candidate["metrics"]["all"]["top1_hits"] == 1
        assert candidate["metrics"]["all"]["recall_at_5_hits"] == 2
        assert candidate["provider_identity_evidence"][
            "catalog_contract_verified_after_embedding"
        ] is True
        return {"passed": True, "criteria": {"persisted_quality": True}}

    monkeypatch.setattr(
        latency.retrieval_benchmark,
        "differential_gate",
        _differential_gate,
    )

    def _proxy_parity(_cells, _vectors, rankings, **_kwargs):
        return {
            "comparison_scope": "test",
            "ranking_comparison_predicate": "exact",
            "ranking_comparison_count": len(rankings),
            "ranking_mismatch_count": 0,
            "score_comparison_count": len(rankings) * latency.DEFAULT_K,
            "score_comparison_predicate": "absolute_error_at_most_tolerance",
            "score_tolerance": 1.0e-6,
            "max_abs_score_error": 0.0,
            "passed": True,
            "_proxy_ranking_identities": rankings,
        }

    monkeypatch.setattr(latency, "evaluate_numpy_proxy_parity", _proxy_parity)
    monkeypatch.setattr(latency.time, "perf_counter_ns", _clock(*durations_ms))
    return session


def test_live_report_proves_dependencies_but_grants_no_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _install_live_fakes(
        monkeypatch,
        durations_ms=(0.25, 0.5) * latency.DEFAULT_REPETITIONS,
    )

    report = latency.run_live_latency_benchmark(
        "request.json",
        "snapshot",
    )

    assert session.closed is True
    assert report["schema_version"] == latency.REPORT_SCHEMA
    assert report["retrieval_evidence_scope"] == latency.LIVE_RETRIEVAL_SCOPE
    assert report["candidate_snapshot_verified"] is True
    assert report["candidate_snapshot_id"] == SNAPSHOT_ID
    assert report["embedding_catalog_verified"] is True
    assert report["global_all_cell_search_verified"] is True
    assert report["measurement"]["search_execution_count"] == 200
    assert report["measurement"]["ranking_repeat_check_count"] == 198
    assert report["measurement"]["ranking_repeat_mismatch_count"] == 0
    assert report["measurement"]["cells_searched_per_operation"] == 2
    assert report["measurement"]["total_cell_searches"] == 400
    assert report["measurement"]["query_embedding_excluded_from_search_latency"] is True
    assert report["embedding_time_excluded_from_latency"] is True
    assert report["session_open_verification_time_excluded_from_latency"] is True
    assert report["end_to_end_query_latency_evaluated"] is False
    assert report["latency_gate"]["passed"] is True
    assert report["persisted_candidate_quality"]["metrics"]["all"] == {
        "cases": 2,
        "top1_hits": 1,
        "top1_accuracy": 0.5,
        "recall_at_5_hits": 2,
        "recall_at_5": 1.0,
        "expected_cell_top1_hits": 2,
        "expected_cell_top1_accuracy": 1.0,
        "nonempty_rate": 1.0,
    }
    assert report["persisted_candidate_quality"][
        "label_scoring_after_search"
    ] is True
    assert report["persisted_candidate_differential_gate"]["passed"] is True
    assert report["ranking_stability"]["within_verified_session"]["passed"] is True
    assert report["ranking_stability"]["snapshot_reopen"] == {
        "search_count": 2,
        "ranking_mismatch_count": 0,
        "verification_session_id": REOPEN_SESSION_ID,
        "previous_session_id_distinct": True,
        "passed": True,
    }
    assert report["numpy_proxy_parity"]["passed"] is True
    assert report["numpy_proxy_parity"]["quality_metrics_equal"] is True
    assert report["retrieval_label_isolation_check_count"] == 2
    assert report["retrieval_label_isolation_enforced"] is True
    assert report["actual_persisted_faiss_quality_evaluated"] is True
    assert report["positive_ranking_quality_evaluated"] is True
    assert report["off_domain_rejection_evaluated"] is False
    assert report["candidate_benchmark_scope"] == (
        "paired_positive_ranking_and_search_latency_only_"
        "no_rejection_calibration"
    )
    assert report["candidate_benchmark_pass"] is True
    assert report["cell_pruning_scale_trigger_crossed"] is False
    assert report["latency_gate_decision"] == (
        "retain_verified_global_all_cells_at_observed_scale"
    )
    assert report["decision_scope"] == (
        "positive_ranking_quality_and_latency_current_snapshot"
    )
    assert report["scale_scope"]["multi_scale_generalization_supported"] is False
    assert report["scale_scope"]["remeasure_on_snapshot_change"] is True
    assert report["reproducibility"] == {
        "measurement_contract_frozen": True,
        "independent_run_count": 1,
        "cross_run_stability_evaluated": False,
        "snapshot_reopen_ranking_stability_evaluated": True,
    }
    assert report["cell_pruning_evaluated"] is False
    assert report["cell_pruning_authorized"] is False
    assert report["runtime_authority_granted"] is False
    assert report["production_promotion_gate_pass"] is False


def test_live_entrypoint_freezes_the_latency_gate_contract() -> None:
    parameters = inspect.signature(latency.run_live_latency_benchmark).parameters

    assert "clock_ns" not in parameters
    assert "k" not in parameters
    assert "repetitions" not in parameters
    assert "warmup_rounds" not in parameters
    assert "max_search_p95_ms" not in parameters
    assert latency.DEFAULT_K == 5
    assert latency.DEFAULT_REPETITIONS == 100
    assert latency.DEFAULT_WARMUP_ROUNDS == 1
    assert latency.DEFAULT_MAX_SEARCH_P95_MS == 10.0


def test_live_report_crosses_scale_trigger_without_authorizing_pruning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_live_fakes(
        monkeypatch,
        durations_ms=(12.0, 14.0) * latency.DEFAULT_REPETITIONS,
    )

    report = latency.run_live_latency_benchmark(
        "request.json",
        "snapshot",
    )

    assert report["latency_gate"]["passed"] is False
    assert report["persisted_candidate_differential_gate"]["passed"] is True
    assert report["candidate_benchmark_pass"] is False
    assert report["cell_pruning_scale_trigger_crossed"] is True
    assert report["latency_gate_decision"] == (
        "evaluate_pruning_alternatives_before_scale_increase"
    )
    assert report["cell_pruning_authorized"] is False
    assert report["runtime_authority_granted"] is False


def test_live_report_rejects_invalid_timeout_contract() -> None:
    with pytest.raises(
        latency.CandidateLatencyContractError,
        match="timeout_seconds_must_be_positive_finite",
    ):
        latency.run_live_latency_benchmark(
            "request.json",
            "snapshot",
            timeout_seconds=0.0,
        )


def test_main_fails_closed_without_live_evidence(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        latency,
        "run_live_latency_benchmark",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            latency.CandidateLatencyUnavailable("faiss_missing")
        ),
    )

    assert latency.main(["--request", "r", "--snapshot", "s", "--no-write"]) == 2
    payload = __import__("json").loads(capsys.readouterr().out)
    assert payload["candidate_snapshot_verified"] is False
    assert payload["global_all_cell_search_verified"] is False
    assert payload["actual_persisted_faiss_quality_evaluated"] is False
    assert payload["candidate_benchmark_pass"] is False
    assert payload["runtime_authority_granted"] is False
    assert payload["production_promotion_gate_pass"] is False


def test_main_returns_one_when_scoped_candidate_benchmark_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        latency,
        "run_live_latency_benchmark",
        lambda *_args, **_kwargs: {
            "schema_version": latency.REPORT_SCHEMA,
            "candidate_benchmark_pass": False,
            "runtime_authority_granted": False,
        },
    )

    assert latency.main(["--request", "r", "--snapshot", "s", "--no-write"]) == 1
    payload = __import__("json").loads(capsys.readouterr().out)
    assert payload["candidate_benchmark_pass"] is False
    assert payload["runtime_authority_granted"] is False
