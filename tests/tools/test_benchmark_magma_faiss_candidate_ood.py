from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

from tools import benchmark_magma_faiss_candidate_ood as ood
from tools import benchmark_magma_solver_retrieval as retrieval_benchmark


SNAPSHOT_ID = "faisscand_" + "a" * 64
SESSION_ID = "faisssession_" + "b" * 32


def _row(
    solver_id: str,
    score: float,
    *,
    index: int,
) -> dict:
    return {
        "canonical_solver_id": solver_id,
        "cell_id": "one" if index % 2 == 0 else "two",
        "projection_id": "sha256:" + f"{index + 1:x}" * 64,
        "score": score,
        "snapshot_id": SNAPSHOT_ID,
        "verification_session_id": SESSION_ID,
        "source_commit_reverified": True,
        "source_reverification_scope": "session_open",
        "source_reverified_during_search_call": False,
        "runtime_authority_granted": False,
        "receipt_authenticity_verified": False,
        "solver_outcome_verified": False,
    }


def _ranking(top_solver: str, top_score: float) -> list[dict]:
    solver_ids = [top_solver] + [f"fallback_{index}" for index in range(1, 5)]
    return [
        _row(solver_id, top_score - index / 100.0, index=index)
        for index, solver_id in enumerate(solver_ids)
    ]


class _Session:
    snapshot_id = SNAPSHOT_ID

    def __init__(self) -> None:
        self.closed = False
        self.calls = 0
        self.rankings = [
            _ranking("solver_a", 0.70),
            _ranking("solver_b", 0.54),
            _ranking("weather_solver", 0.65),
            _ranking("other_solver", 0.549999999),
        ]

    def search(self, _vector: np.ndarray, *, k: int) -> list[dict]:
        ranking = self.rankings[self.calls]
        self.calls += 1
        return ranking[:k]

    def close(self) -> None:
        self.closed = True


def test_frozen_ood_corpus_preserves_historical_query_sequence() -> None:
    value = json.loads(ood.DEFAULT_OOD_CORPUS.read_bytes())
    historical = yaml.safe_load(
        (ood.ROOT / "tests" / "oracle" / "_off_domain.yaml").read_bytes()
    )
    documents, _topology_digest = retrieval_benchmark.load_projection_documents()

    corpus, evidence = ood.load_ood_corpus(
        ood.DEFAULT_OOD_CORPUS,
        documents,
        [],
    )

    assert [case["query"] for case in corpus["cases"]] == historical["negative"]
    assert [case["query_id"] for case in corpus["cases"]] == [
        f"O{index:02d}" for index in range(1, 33)
    ]
    assert len({case["query"] for case in corpus["cases"]}) == 32
    assert {case["expected_disposition"] for case in corpus["cases"]} == {
        "reject_no_solver"
    }
    assert len(documents) == value["expected_projection_solver_count"] == 22
    assert evidence["canonical_sha256"] == (
        ood.EXPECTED_OOD_CORPUS_CANONICAL_SHA256
    )
    assert evidence["case_order_semantics"].endswith(
        "no_translation_pairing_claim"
    )


def test_ood_corpus_fails_closed_on_digest_or_projection_drift(tmp_path) -> None:
    value = json.loads(ood.DEFAULT_OOD_CORPUS.read_bytes())
    value["cases"][0]["query"] += " changed"
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(value), encoding="utf-8")
    documents, _topology_digest = retrieval_benchmark.load_projection_documents()

    with pytest.raises(ood.CandidateOodContractError, match="hash_mismatch"):
        ood.load_ood_corpus(changed, documents, [], repo_root=tmp_path)

    with pytest.raises(
        ood.CandidateOodContractError,
        match="projection_count_mismatch",
    ):
        ood.load_ood_corpus(
            ood.DEFAULT_OOD_CORPUS,
            documents[:-1],
            [],
        )


def test_ood_corpus_rejects_overlap_with_positive_queries() -> None:
    documents, _topology_digest = retrieval_benchmark.load_projection_documents()

    with pytest.raises(
        ood.CandidateOodContractError,
        match="overlaps_positive",
    ):
        ood.load_ood_corpus(
            ood.DEFAULT_OOD_CORPUS,
            documents,
            ["What is the weather forecast"],
        )


def test_threshold_sweep_uses_unrounded_greater_than_or_equal_boundary() -> None:
    result = ood.evaluate_threshold_sweep(
        [0.70, 0.549999999],
        [True, False],
        [0.65, 0.549999999],
    )
    by_threshold = {row["threshold"]: row for row in result["rows"]}

    assert result["measurement_only_no_pass_gate"] is True
    assert by_threshold[0.55] == {
        "threshold": 0.55,
        "acceptance_comparison": "top_score_greater_than_or_equal",
        "positive_accepted_count": 1,
        "positive_total": 2,
        "correct_positive_top1_retained_count": 1,
        "correct_positive_top1_total": 1,
        "incorrect_positive_top1_accepted_count": 0,
        "incorrect_positive_top1_total": 1,
        "ood_rejected_count": 1,
        "ood_total": 2,
        "ood_false_accepted_count": 1,
    }
    assert by_threshold[0.65]["ood_false_accepted_count"] == 1
    assert by_threshold[0.70]["positive_accepted_count"] == 1
    assert [row["positive_accepted_count"] for row in result["rows"]] == sorted(
        [row["positive_accepted_count"] for row in result["rows"]],
        reverse=True,
    )
    assert [row["ood_rejected_count"] for row in result["rows"]] == sorted(
        row["ood_rejected_count"] for row in result["rows"]
    )


def _install_live_fakes(monkeypatch: pytest.MonkeyPatch) -> _Session:
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
        topology_digest="sha256:" + "e" * 64,
        cells=(("one", "proj_" + "1" * 64), ("two", "proj_" + "2" * 64)),
    )
    manifest = {
        "snapshot_id": SNAPSHOT_ID,
        "topology_digest": "sha256:" + "e" * 64,
        "cells": [{"cell_id": "one"}, {"cell_id": "two"}],
        "total_vector_count": 22,
        "faiss_version": "test-faiss",
        "faiss_compile_options": "test-options",
        "faiss_binary_set_sha256": "sha256:" + "f" * 64,
    }
    positive_cases = [
        {
            "query_id": "A01",
            "stratum": "anchored_natural",
            "query": "positive one",
            "expected_solver": "solver_a",
            "expected_cell": "one",
        },
        {
            "query_id": "S01",
            "stratum": "semantic_zero_overlap",
            "query": "positive two",
            "expected_solver": "fallback_1",
            "expected_cell": "two",
        },
    ]
    ood_cases = [
        {
            "query_id": "O01",
            "query": "weather question",
            "expected_disposition": "reject_no_solver",
        },
        {
            "query_id": "O02",
            "query": "joke question",
            "expected_disposition": "reject_no_solver",
        },
    ]
    session = _Session()

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
            assert texts == [
                "query: positive one",
                "query: positive two",
                "query: weather question",
                "query: joke question",
            ]
            assert observed_profile is profile
            assert label == "candidate_ood_query_embeddings"
            return np.ones((4, 3), dtype=np.float32)

    monkeypatch.setattr(
        ood.candidate_benchmark,
        "_load_query_suite",
        lambda *_args, **_kwargs: {
            "queries": [case["query"] for case in positive_cases],
            "cases": positive_cases,
            "documents": [
                {
                    "canonical_solver_id": (
                        "solver_a" if index == 0 else f"snapshot_solver_{index}"
                    ),
                    "cell_id": "one" if index % 2 == 0 else "two",
                    "projection_id": "sha256:" + f"{index + 1:x}" * 64,
                    "projection_digest": "sha256:" + f"{index + 2:x}" * 64,
                    "source_digest": "sha256:" + f"{index + 3:x}" * 64,
                }
                for index in range(22)
            ],
            "corpus": {
                "path": "positive.json",
                "canonical_sha256": "1" * 64,
                "raw_sha256": "2" * 64,
                "cases": 2,
                "solver_coverage": 2,
                "topology_digest": manifest["topology_digest"],
                "router_labels_sent_to_search": False,
            },
        },
    )
    monkeypatch.setattr(
        ood,
        "load_ood_corpus",
        lambda *_args, **_kwargs: (
            {"cases": ood_cases},
            {
                "path": "ood.json",
                "canonical_sha256": "3" * 64,
                "raw_sha256": "4" * 64,
                "cases": 2,
                "expected_projection_solver_count": 2,
            },
        ),
    )
    monkeypatch.setattr(
        ood.candidate_snapshot,
        "load_candidate_request",
        lambda *_args, **_kwargs: request,
    )
    monkeypatch.setattr(
        ood.candidate_snapshot,
        "load_verified_candidate_snapshot",
        lambda *_args, **_kwargs: {
            "manifest": manifest,
            "cells": [
                {
                    "manifest": {"cell_id": cell_id},
                    "rows": [
                        {
                            "canonical_solver_id": (
                                "solver_a"
                                if index == 0
                                else f"snapshot_solver_{index}"
                            ),
                            "projection_id": (
                                "sha256:" + f"{index + 1:x}" * 64
                            ),
                            "projection_digest": (
                                "sha256:" + f"{index + 2:x}" * 64
                            ),
                            "source_digest": (
                                "sha256:" + f"{index + 3:x}" * 64
                            ),
                        }
                        for index in range(22)
                        if ("one" if index % 2 == 0 else "two") == cell_id
                    ],
                }
                for cell_id in ("one", "two")
            ],
        },
    )
    monkeypatch.setattr(
        ood.candidate_snapshot,
        "open_verified_candidate_search_session",
        lambda *_args, **_kwargs: session,
    )
    monkeypatch.setattr(
        ood.candidate_snapshot,
        "_profile_from_contract",
        lambda _contract: profile,
    )
    monkeypatch.setattr(
        ood.retrieval_benchmark,
        "OllamaEmbeddingClient",
        _Embedder,
    )
    monkeypatch.setattr(
        ood.retrieval_benchmark,
        "provider_identity_matches_profile",
        lambda value, observed_profile: value == identity and observed_profile is profile,
    )
    return session


def test_live_report_fails_closed_on_same_count_topology_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _install_live_fakes(monkeypatch)
    original = ood.candidate_benchmark._load_query_suite

    def mismatched_suite(*args, **kwargs):
        suite = original(*args, **kwargs)
        suite["corpus"] = {
            **suite["corpus"],
            "topology_digest": "sha256:" + "9" * 64,
        }
        return suite

    monkeypatch.setattr(
        ood.candidate_benchmark,
        "_load_query_suite",
        mismatched_suite,
    )

    with pytest.raises(
        ood.CandidateOodContractError,
        match="positive_suite_topology_binding_mismatch",
    ):
        ood.run_live_ood_measurement("request.json", "snapshot")

    assert session.closed is True
    assert session.calls == 0


def test_live_report_fails_closed_on_same_count_projection_identity_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _install_live_fakes(monkeypatch)
    original = ood.candidate_benchmark._load_query_suite

    def mismatched_suite(*args, **kwargs):
        suite = original(*args, **kwargs)
        suite["documents"] = [dict(row) for row in suite["documents"]]
        suite["documents"][0]["source_digest"] = "sha256:" + "9" * 64
        return suite

    monkeypatch.setattr(
        ood.candidate_benchmark,
        "_load_query_suite",
        mismatched_suite,
    )

    with pytest.raises(
        ood.CandidateOodContractError,
        match="positive_suite_projection_binding_mismatch",
    ):
        ood.run_live_ood_measurement("request.json", "snapshot")

    assert session.closed is True
    assert session.calls == 0


def test_live_report_measures_thresholds_without_selecting_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _install_live_fakes(monkeypatch)

    report = ood.run_live_ood_measurement("request.json", "snapshot")

    rows = {
        row["threshold"]: row for row in report["threshold_sweep"]["rows"]
    }
    assert session.closed is True
    assert session.calls == 4
    assert report["status"] == "MEASURED_NOT_CALIBRATED"
    assert report["measurement_complete"] is True
    assert report["candidate_snapshot_verified"] is True
    assert report["embedding_catalog_verified"] is True
    assert report["global_all_cell_search_verified"] is True
    assert report["measurement"]["positive_query_count"] == 2
    assert report["measurement"]["ood_query_count"] == 2
    assert report["measurement"]["search_execution_count"] == 4
    assert report["measurement"]["total_cell_searches"] == 8
    assert report["positive_suite_snapshot_binding"] == {
        "topology_digest": "sha256:" + "e" * 64,
        "topology_binding_verified": True,
        "projection_identity_fields": [
            "canonical_solver_id",
            "cell_id",
            "projection_id",
            "projection_digest",
            "source_digest",
        ],
        "positive_suite_projection_identity_count": 22,
        "snapshot_projection_identity_count": 22,
        "exact_projection_identity_set_match": True,
    }
    assert report["positive_ranking_quality"]["metrics"]["all"]["top1_hits"] == 1
    assert rows[0.55]["positive_accepted_count"] == 1
    assert rows[0.55]["correct_positive_top1_retained_count"] == 1
    assert rows[0.55]["ood_rejected_count"] == 1
    assert rows[0.65]["ood_false_accepted_count"] == 1
    assert rows[0.70]["ood_rejected_count"] == 2
    assert report["highest_ood_false_accept_at_historical_reference"][
        "query_id"
    ] == "O01"
    assert report["highest_ood_false_accept_at_historical_reference"][
        "accepted_at_reference"
    ] is True
    assert report["retrieval_label_isolation_check_count"] == 4
    assert report["retrieval_label_isolation_enforced"] is True
    assert report["off_domain_rejection_evaluated"] is True
    assert report["off_domain_rejection_calibrated"] is False
    assert report["calibration_gate_defined"] is False
    assert report["calibration_gate_pass"] is False
    assert report["calibration_readiness"] == {
        "current_corpus_role": "development_probe_fully_observed",
        "current_corpus_eligible_as_untouched_holdout": False,
        "untouched_holdout_defined": False,
        "untouched_holdout_scored_count": 0,
        "future_holdout_requirement": (
            "new_independently_adjudicated_queries_not_used_in_this_sweep"
        ),
        "grouping_semantics": (
            "must_be_adjudicated_not_inferred_from_historical_case_order"
        ),
        "threshold_objective_preregistered": False,
        "confidence_intervals_evaluated": False,
        "score_distributions_overlap": True,
        "single_score_threshold_perfect_separation_observed": False,
        "required_input_admission_signal_evaluated": False,
    }
    assert report["runtime_threshold_selected"] is False
    assert report["selected_runtime_threshold"] is None
    assert report["candidate_mode_change_authorized"] is False
    assert report["cell_pruning_authorized"] is False
    assert report["runtime_authority_granted"] is False
    assert report["production_promotion_gate_pass"] is False


def test_live_entrypoint_freezes_thresholds_and_has_no_selection_override() -> None:
    parameters = inspect.signature(ood.run_live_ood_measurement).parameters

    assert "threshold" not in parameters
    assert "thresholds" not in parameters
    assert "selected_threshold" not in parameters
    assert ood.FIXED_THRESHOLDS == (
        0.45,
        0.50,
        0.55,
        0.60,
        0.62,
        0.65,
        0.70,
        0.75,
    )


def test_main_exit_zero_means_measurement_complete_not_calibration_pass(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        ood,
        "run_live_ood_measurement",
        lambda *_args, **_kwargs: {
            "schema_version": ood.REPORT_SCHEMA,
            "measurement_complete": True,
            "off_domain_rejection_calibrated": False,
            "calibration_gate_pass": False,
            "runtime_threshold_selected": False,
            "runtime_authority_granted": False,
        },
    )

    assert ood.main(["--request", "r", "--snapshot", "s", "--no-write"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["measurement_complete"] is True
    assert payload["calibration_gate_pass"] is False
    assert payload["runtime_threshold_selected"] is False


def test_main_fails_closed_without_live_evidence(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        ood,
        "run_live_ood_measurement",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ood.CandidateOodUnavailable("faiss_missing")
        ),
    )

    assert ood.main(["--request", "r", "--snapshot", "s", "--no-write"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["measurement_complete"] is False
    assert payload["candidate_snapshot_verified"] is False
    assert payload["off_domain_rejection_evaluated"] is False
    assert payload["off_domain_rejection_calibrated"] is False
    assert payload["runtime_threshold_selected"] is False
    assert payload["runtime_authority_granted"] is False
