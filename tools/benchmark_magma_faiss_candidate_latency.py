#!/usr/bin/env python3
"""Measure verified all-cell MAGMA FAISS candidate quality and latency.

This is a candidate-only evidence tool. It verifies an immutable snapshot and
its source projection commits, checks the pinned Ollama catalog identity before
and after embedding, scores frozen labels only after retrieval, compares the
persisted rankings with an exact dense proxy and a fresh-session reopen, and
times repeated searches through the opaque verified session. Query embedding
and session-open verification are excluded from the search latency samples.

The report can show that an observed scale does or does not cross the existing
10 ms exact-search p95 target. It never enables cell pruning, changes a runtime
index, grants routing authority, or passes a production promotion gate.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import benchmark_magma_solver_retrieval as retrieval_benchmark  # noqa: E402
from tools import materialize_magma_faiss_candidate as candidate_snapshot  # noqa: E402


REPORT_SCHEMA = "magma.faiss.candidate_all_cell_benchmark.v2"
LIVE_RETRIEVAL_SCOPE = "verified_snapshot_session_global_all_cells"
DEFAULT_REPETITIONS = 100
DEFAULT_WARMUP_ROUNDS = 1
DEFAULT_K = 5
DEFAULT_MAX_SEARCH_P95_MS = 10.0
_SESSION_ID = re.compile(r"^faisssession_[0-9a-f]{32}$")


class CandidateLatencyContractError(ValueError):
    """An input or returned evidence violates the latency contract."""


class CandidateLatencyUnavailable(RuntimeError):
    """A required local dependency is unavailable without fallback."""


def _require_positive_int(value: Any, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise CandidateLatencyContractError(f"{label}_must_be_positive_integer")
    return value


def _require_nonnegative_int(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise CandidateLatencyContractError(
            f"{label}_must_be_nonnegative_integer"
        )
    return value


def _latency_summary(values: Sequence[float]) -> dict[str, float]:
    if not values or any(not math.isfinite(value) or value < 0.0 for value in values):
        raise CandidateLatencyContractError("search_latency_samples_invalid")
    samples = np.asarray(values, dtype=np.float64)
    return {
        "p50": round(float(np.percentile(samples, 50)), 6),
        "p95": round(float(np.percentile(samples, 95)), 6),
        "p99": round(float(np.percentile(samples, 99)), 6),
        "max": round(float(samples.max()), 6),
        "mean": round(float(samples.mean()), 6),
    }


def _validate_search_rows(
    rows: Any,
    *,
    expected_snapshot_id: str,
    k: int,
) -> str:
    if type(rows) is not list or len(rows) != k:
        raise CandidateLatencyContractError("verified_search_rows_invalid")
    session_ids: set[str] = set()
    solver_ids: set[str] = set()
    for row in rows:
        if type(row) is not dict:
            raise CandidateLatencyContractError("verified_search_row_invalid")
        if row.get("snapshot_id") != expected_snapshot_id:
            raise CandidateLatencyContractError("search_snapshot_binding_mismatch")
        session_id = row.get("verification_session_id")
        if type(session_id) is not str or not _SESSION_ID.fullmatch(session_id):
            raise CandidateLatencyContractError("search_session_binding_invalid")
        session_ids.add(session_id)
        solver_id = row.get("canonical_solver_id")
        cell_id = row.get("cell_id")
        projection_id = row.get("projection_id")
        score = row.get("score")
        if (
            type(solver_id) is not str
            or not solver_id
            or solver_id in solver_ids
            or type(cell_id) is not str
            or not cell_id
            or type(projection_id) is not str
            or not projection_id
            or not isinstance(score, (int, float))
            or isinstance(score, bool)
            or not math.isfinite(float(score))
        ):
            raise CandidateLatencyContractError("search_ranking_row_invalid")
        solver_ids.add(solver_id)
        if (
            row.get("source_commit_reverified") is not True
            or row.get("source_reverification_scope") != "session_open"
            or row.get("source_reverified_during_search_call") is not False
            or row.get("runtime_authority_granted") is not False
            or row.get("receipt_authenticity_verified") is not False
            or row.get("solver_outcome_verified") is not False
        ):
            raise CandidateLatencyContractError("search_evidence_boundary_invalid")
    if len(session_ids) != 1:
        raise CandidateLatencyContractError("search_session_binding_mismatch")
    return next(iter(session_ids))


def _ranking_identity(rows: Sequence[Mapping[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            row["canonical_solver_id"],
            row["cell_id"],
            row["projection_id"],
            float(row["score"]),
        )
        for row in rows
    )


def measure_verified_global_search(
    session: Any,
    query_vectors: Any,
    *,
    expected_snapshot_id: str,
    k: int = DEFAULT_K,
    repetitions: int = DEFAULT_REPETITIONS,
    warmup_rounds: int = DEFAULT_WARMUP_ROUNDS,
    clock_ns: Callable[[], int] | None = None,
) -> dict[str, Any]:
    """Time only verified-session searches over precomputed query vectors."""
    k = _require_positive_int(k, "k")
    repetitions = _require_positive_int(repetitions, "repetitions")
    warmup_rounds = _require_nonnegative_int(warmup_rounds, "warmup_rounds")
    vectors = np.asarray(query_vectors, dtype=np.float32)
    if vectors.ndim != 2 or not len(vectors) or not np.isfinite(vectors).all():
        raise CandidateLatencyContractError("query_vectors_invalid")
    if type(expected_snapshot_id) is not str or not expected_snapshot_id:
        raise CandidateLatencyContractError("expected_snapshot_id_invalid")
    clock = time.perf_counter_ns if clock_ns is None else clock_ns

    observed_session_ids: set[str] = set()
    for _ in range(warmup_rounds):
        for vector in vectors:
            rows = session.search(vector, k=k)
            observed_session_ids.add(
                _validate_search_rows(
                    rows,
                    expected_snapshot_id=expected_snapshot_id,
                    k=k,
                )
            )

    samples_ms: list[float] = []
    per_query_samples: list[list[float]] = [[] for _ in vectors]
    ranking_identities: list[tuple[tuple[Any, ...], ...] | None] = [
        None for _ in vectors
    ]
    ranking_repeat_check_count = 0
    ranking_repeat_mismatch_count = 0
    for _ in range(repetitions):
        for query_index, vector in enumerate(vectors):
            started = clock()
            rows = session.search(vector, k=k)
            elapsed_ns = clock() - started
            if type(elapsed_ns) is not int or elapsed_ns < 0:
                raise CandidateLatencyContractError("search_clock_invalid")
            observed_session_ids.add(
                _validate_search_rows(
                    rows,
                    expected_snapshot_id=expected_snapshot_id,
                    k=k,
                )
            )
            ranking_identity = _ranking_identity(rows)
            if ranking_identities[query_index] is None:
                ranking_identities[query_index] = ranking_identity
            else:
                ranking_repeat_check_count += 1
                if ranking_identities[query_index] != ranking_identity:
                    ranking_repeat_mismatch_count += 1
            elapsed_ms = elapsed_ns / 1_000_000.0
            samples_ms.append(elapsed_ms)
            per_query_samples[query_index].append(elapsed_ms)

    if len(observed_session_ids) != 1:
        raise CandidateLatencyContractError("search_session_changed_during_run")
    per_query_p95 = [
        float(np.percentile(query_samples, 95))
        for query_samples in per_query_samples
    ]
    if any(ranking is None for ranking in ranking_identities):
        raise CandidateLatencyContractError("search_ranking_capture_incomplete")
    return {
        "query_count": len(vectors),
        "repetitions": repetitions,
        "warmup_rounds": warmup_rounds,
        "warmup_search_count": warmup_rounds * len(vectors),
        "search_execution_count": repetitions * len(vectors),
        "k": k,
        "verification_session_id": next(iter(observed_session_ids)),
        "ranking_repeat_check_count": ranking_repeat_check_count,
        "ranking_repeat_mismatch_count": ranking_repeat_mismatch_count,
        "_ranking_identities": ranking_identities,
        "latency_ms": {
            "search": _latency_summary(samples_ms),
            "per_query_p95": {
                "min": round(min(per_query_p95), 6),
                "median": round(float(np.median(per_query_p95)), 6),
                "max": round(max(per_query_p95), 6),
            },
        },
    }


def verify_snapshot_reopen_ranking_stability(
    request: Any,
    snapshot_dir: Path | str,
    query_vectors: Any,
    baseline_rankings: Sequence[Sequence[tuple[Any, ...]]],
    *,
    expected_snapshot_id: str,
    previous_session_id: str,
    k: int = DEFAULT_K,
) -> dict[str, Any]:
    """Compare rankings after reopening the same immutable snapshot."""
    vectors = np.asarray(query_vectors, dtype=np.float32)
    if vectors.ndim != 2 or len(vectors) != len(baseline_rankings):
        raise CandidateLatencyContractError(
            "snapshot_reopen_query_ranking_count_mismatch"
        )
    try:
        session = candidate_snapshot.open_verified_candidate_search_session(
            snapshot_dir,
            expected_request=request,
        )
    except candidate_snapshot.CandidateUnavailable as exc:
        raise CandidateLatencyUnavailable(str(exc)) from exc
    except candidate_snapshot.CandidateContractError as exc:
        raise CandidateLatencyContractError(str(exc)) from exc
    try:
        if session.snapshot_id != expected_snapshot_id:
            raise CandidateLatencyContractError(
                "snapshot_reopen_snapshot_binding_mismatch"
            )
        mismatch_count = 0
        observed_session_ids: set[str] = set()
        for vector, baseline in zip(vectors, baseline_rankings):
            rows = session.search(vector, k=k)
            observed_session_ids.add(
                _validate_search_rows(
                    rows,
                    expected_snapshot_id=expected_snapshot_id,
                    k=k,
                )
            )
            mismatch_count += int(_ranking_identity(rows) != tuple(baseline))
        if len(observed_session_ids) != 1:
            raise CandidateLatencyContractError(
                "snapshot_reopen_session_binding_mismatch"
            )
        session_id = next(iter(observed_session_ids))
        if session_id == previous_session_id:
            raise CandidateLatencyContractError(
                "snapshot_reopen_session_id_not_fresh"
            )
        return {
            "search_count": len(vectors),
            "ranking_mismatch_count": mismatch_count,
            "verification_session_id": session_id,
            "previous_session_id_distinct": True,
        }
    finally:
        session.close()


def _build_label_blind_query_inputs(
    queries: Sequence[Any],
    *,
    query_prefix: str,
) -> tuple[list[str], int]:
    if (
        not queries
        or type(query_prefix) is not str
        or any(type(query) is not str or not query for query in queries)
    ):
        raise CandidateLatencyContractError("label_blind_query_inputs_invalid")
    inputs = [query_prefix + query for query in queries]
    return inputs, len(inputs)


def evaluate_numpy_proxy_parity(
    loaded_cells: Sequence[Mapping[str, Any]],
    query_vectors: Any,
    persisted_rankings: Sequence[Sequence[tuple[Any, ...]]],
    *,
    k: int,
    score_tolerance: float,
) -> dict[str, Any]:
    """Compare persisted FAISS rankings with an exact dense NumPy proxy."""
    if (
        not loaded_cells
        or not math.isfinite(score_tolerance)
        or score_tolerance < 0.0
    ):
        raise CandidateLatencyContractError("numpy_proxy_contract_invalid")
    vectors: list[np.ndarray] = []
    identities: list[tuple[str, str, str]] = []
    for cell in loaded_cells:
        if type(cell) is not dict:
            raise CandidateLatencyContractError("numpy_proxy_cell_invalid")
        matrix = np.asarray(cell.get("vectors"), dtype=np.float32)
        rows = cell.get("rows")
        cell_id = cell.get("manifest", {}).get("cell_id")
        if (
            matrix.ndim != 2
            or type(rows) is not list
            or len(rows) != len(matrix)
            or type(cell_id) is not str
            or not cell_id
        ):
            raise CandidateLatencyContractError("numpy_proxy_cell_invalid")
        for row_index, row in enumerate(rows):
            if type(row) is not dict:
                raise CandidateLatencyContractError("numpy_proxy_row_invalid")
            solver_id = row.get("canonical_solver_id")
            projection_id = row.get("projection_id")
            if (
                type(solver_id) is not str
                or not solver_id
                or type(projection_id) is not str
                or not projection_id
            ):
                raise CandidateLatencyContractError("numpy_proxy_row_invalid")
            vectors.append(matrix[row_index])
            identities.append((solver_id, cell_id, projection_id))
    if len({identity[0] for identity in identities}) != len(identities):
        raise CandidateLatencyContractError("numpy_proxy_solver_ids_not_unique")
    document_matrix = np.ascontiguousarray(np.stack(vectors), dtype=np.float32)
    queries = retrieval_benchmark.normalize_embedding_matrix(
        query_vectors,
        expected_rows=len(persisted_rankings),
        expected_dimension=document_matrix.shape[1],
        label="numpy_proxy_query_embeddings",
    )
    if k > len(identities):
        raise CandidateLatencyContractError("numpy_proxy_k_exceeds_documents")

    ranking_mismatch_count = 0
    score_comparison_count = 0
    max_abs_score_error = 0.0
    proxy_rankings: list[tuple[tuple[Any, ...], ...]] = []
    for query, persisted in zip(queries, persisted_rankings):
        scores = np.asarray(document_matrix @ query, dtype=np.float32)
        order = sorted(
            range(len(identities)),
            key=lambda index: (-float(scores[index]), identities[index][0]),
        )[:k]
        proxy = tuple(
            (
                identities[index][0],
                identities[index][1],
                identities[index][2],
                float(scores[index]),
            )
            for index in order
        )
        proxy_rankings.append(proxy)
        persisted_structure = tuple(tuple(row[:3]) for row in persisted)
        proxy_structure = tuple(tuple(row[:3]) for row in proxy)
        ranking_mismatch_count += int(persisted_structure != proxy_structure)
        if len(persisted) != len(proxy):
            raise CandidateLatencyContractError(
                "numpy_proxy_ranking_length_mismatch"
            )
        for persisted_row, proxy_row in zip(persisted, proxy):
            score_comparison_count += 1
            max_abs_score_error = max(
                max_abs_score_error,
                abs(float(persisted_row[3]) - float(proxy_row[3])),
            )
    return {
        "comparison_scope": "same_query_vectors_exact_dense_numpy_proxy",
        "ranking_comparison_predicate": (
            "exact_solver_cell_projection_order_equality"
        ),
        "ranking_comparison_count": len(proxy_rankings),
        "ranking_mismatch_count": ranking_mismatch_count,
        "score_comparison_count": score_comparison_count,
        "score_comparison_predicate": "absolute_error_at_most_tolerance",
        "score_tolerance": score_tolerance,
        "max_abs_score_error": round(max_abs_score_error, 10),
        "passed": (
            ranking_mismatch_count == 0
            and max_abs_score_error <= score_tolerance
        ),
        "_proxy_ranking_identities": proxy_rankings,
    }


def evaluate_persisted_ranking_quality(
    ranking_identities: Sequence[Sequence[tuple[Any, ...]]],
    cases: Sequence[Mapping[str, str]],
) -> dict[str, Any]:
    """Score frozen labels only after persisted-session rankings exist."""
    if len(ranking_identities) != len(cases) or not cases:
        raise CandidateLatencyContractError("quality_ranking_case_count_mismatch")
    per_query: list[dict[str, Any]] = []
    for index, (ranking, case) in enumerate(zip(ranking_identities, cases)):
        if type(case) is not dict:
            raise CandidateLatencyContractError(
                f"quality_case_{index}_must_be_object"
            )
        required = {
            "query_id",
            "stratum",
            "query",
            "expected_solver",
            "expected_cell",
        }
        if not required.issubset(case):
            raise CandidateLatencyContractError(
                f"quality_case_{index}_fields_missing"
            )
        solver_ids = [str(row[0]) for row in ranking]
        expected_solver = case["expected_solver"]
        expected_rank = next(
            (
                rank
                for rank, solver_id in enumerate(solver_ids, start=1)
                if solver_id == expected_solver
            ),
            None,
        )
        per_query.append(
            {
                "query_id": case["query_id"],
                "stratum": case["stratum"],
                "expected_solver": expected_solver,
                "chosen_solver": solver_ids[0],
                "correct_at_1": solver_ids[0] == expected_solver,
                "expected_rank_at_5": expected_rank,
                "expected_cell": case["expected_cell"],
                "chosen_cell": ranking[0][1],
                "expected_cell_at_1": ranking[0][1] == case["expected_cell"],
                "top_k": [
                    {
                        "solver_id": row[0],
                        "cell_id": row[1],
                        "projection_id": row[2],
                        "score": round(float(row[3]), 8),
                    }
                    for row in ranking
                ],
            }
        )

    def aggregate(selected: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        rows = list(selected)
        return {
            "cases": len(rows),
            "top1_hits": sum(row["correct_at_1"] is True for row in rows),
            "top1_accuracy": round(
                sum(row["correct_at_1"] is True for row in rows) / len(rows),
                6,
            ),
            "recall_at_5_hits": sum(
                row["expected_rank_at_5"] is not None for row in rows
            ),
            "recall_at_5": round(
                sum(row["expected_rank_at_5"] is not None for row in rows)
                / len(rows),
                6,
            ),
            "expected_cell_top1_hits": sum(
                row["expected_cell_at_1"] is True for row in rows
            ),
            "expected_cell_top1_accuracy": round(
                sum(row["expected_cell_at_1"] is True for row in rows)
                / len(rows),
                6,
            ),
            "nonempty_rate": 1.0,
        }

    strata = sorted({row["stratum"] for row in per_query})
    return {
        "measurement_complete": True,
        "label_scoring_after_search": True,
        "label_scoring_isolation_check_count": len(per_query),
        "label_scoring_isolation_enforced": len(per_query) == len(cases),
        "retriever_input_fields": ["query"],
        "retriever_forbidden_inputs": [
            "expected_solver",
            "expected_cell",
            "stratum",
            "query_id",
        ],
        "metrics": {
            "all": aggregate(per_query),
            **{
                stratum: aggregate(
                    [row for row in per_query if row["stratum"] == stratum]
                )
                for stratum in strata
            },
        },
        "per_query": per_query,
    }


def _resolve_repo_input(
    raw_path: Path | str,
    *,
    repo_root: Path,
    label: str,
) -> tuple[Path, str]:
    root = repo_root.resolve()
    path = Path(raw_path)
    resolved = (root / path).resolve() if not path.is_absolute() else path.resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise CandidateLatencyContractError(
            f"{label}_must_be_beneath_repository"
        ) from exc
    return resolved, relative.as_posix()


def _load_query_suite(
    corpus_path: Path | str,
    axioms_dir: Path | str,
    *,
    repo_root: Path,
) -> dict[str, Any]:
    corpus_file, corpus_relative = _resolve_repo_input(
        corpus_path,
        repo_root=repo_root,
        label="corpus_path",
    )
    axioms_root, _ = _resolve_repo_input(
        axioms_dir,
        repo_root=repo_root,
        label="axioms_dir",
    )
    documents, topology_digest = retrieval_benchmark.load_projection_documents(
        axioms_root
    )
    corpus, tokenizer, canonical_digest, raw_digest = (
        retrieval_benchmark.load_corpus(corpus_file, documents)
    )
    return {
        "queries": [case["query"] for case in corpus["cases"]],
        "cases": corpus["cases"],
        "documents": documents,
        "tokenizer": tokenizer,
        "corpus": {
            "path": corpus_relative,
            "canonical_sha256": canonical_digest,
            "raw_sha256": raw_digest,
            "cases": len(corpus["cases"]),
            "solver_coverage": len(documents),
            "topology_digest": topology_digest,
            "router_labels_sent_to_search": False,
        },
    }


def run_live_latency_benchmark(
    request_path: Path | str,
    snapshot_dir: Path | str,
    *,
    corpus_path: Path | str = retrieval_benchmark.DEFAULT_CORPUS,
    axioms_dir: Path | str = retrieval_benchmark.DEFAULT_AXIOMS,
    ollama_url: str = retrieval_benchmark.DEFAULT_OLLAMA_URL,
    timeout_seconds: float = 120.0,
    repo_root: Path = ROOT,
) -> dict[str, Any]:
    """Run the live-only latency measurement without granting authority."""
    if (
        not isinstance(timeout_seconds, (int, float))
        or isinstance(timeout_seconds, bool)
        or not math.isfinite(float(timeout_seconds))
        or float(timeout_seconds) <= 0.0
    ):
        raise CandidateLatencyContractError(
            "timeout_seconds_must_be_positive_finite"
        )

    suite = _load_query_suite(
        corpus_path,
        axioms_dir,
        repo_root=repo_root,
    )
    try:
        request = candidate_snapshot.load_candidate_request(
            request_path,
            repo_root=repo_root,
        )
        verified = candidate_snapshot.load_verified_candidate_snapshot(
            snapshot_dir,
            expected_request=request,
        )
        session = candidate_snapshot.open_verified_candidate_search_session(
            snapshot_dir,
            expected_request=request,
        )
    except candidate_snapshot.CandidateUnavailable as exc:
        raise CandidateLatencyUnavailable(str(exc)) from exc
    except candidate_snapshot.CandidateContractError as exc:
        raise CandidateLatencyContractError(str(exc)) from exc

    manifest = verified["manifest"]
    snapshot_id = manifest["snapshot_id"]
    if session.snapshot_id != snapshot_id:
        session.close()
        raise CandidateLatencyContractError("session_snapshot_binding_mismatch")
    if DEFAULT_K > manifest["total_vector_count"]:
        session.close()
        raise CandidateLatencyContractError("k_exceeds_candidate_vector_count")

    try:
        profile = candidate_snapshot._profile_from_contract(
            request.embedding_contract
        )
    except candidate_snapshot.CandidateContractError as exc:
        session.close()
        raise CandidateLatencyContractError(str(exc)) from exc
    try:
        try:
            with retrieval_benchmark.OllamaEmbeddingClient(
                ollama_url,
                timeout_seconds=float(timeout_seconds),
            ) as embedder:
                identity_before = embedder.verify_profile(profile)
                if not retrieval_benchmark.provider_identity_matches_profile(
                    identity_before,
                    profile,
                ):
                    raise CandidateLatencyContractError(
                        "embedding_provider_identity_before_mismatch"
                    )
                query_inputs, retrieval_label_isolation_check_count = (
                    _build_label_blind_query_inputs(
                        suite["queries"],
                        query_prefix=profile.query_prefix,
                    )
                )
                vectors = embedder.embed(
                    query_inputs,
                    profile,
                    label="candidate_latency_query_embeddings",
                )
                measurement = measure_verified_global_search(
                    session,
                    vectors,
                    expected_snapshot_id=snapshot_id,
                    k=DEFAULT_K,
                    repetitions=DEFAULT_REPETITIONS,
                    warmup_rounds=DEFAULT_WARMUP_ROUNDS,
                )
                identity_after = embedder.verify_profile(profile)
                if identity_before != identity_after:
                    raise CandidateLatencyUnavailable(
                        "embedding_model_catalog_changed_during_run"
                    )
                if not retrieval_benchmark.provider_identity_matches_profile(
                    identity_after,
                    profile,
                ):
                    raise CandidateLatencyContractError(
                        "embedding_provider_identity_after_mismatch"
                    )
        except retrieval_benchmark.BenchmarkUnavailable as exc:
            raise CandidateLatencyUnavailable(str(exc)) from exc
        except retrieval_benchmark.EmbeddingValidationError as exc:
            raise CandidateLatencyContractError(str(exc)) from exc
    finally:
        session.close()

    ranking_identities = measurement.pop("_ranking_identities")
    snapshot_reopen_stability = verify_snapshot_reopen_ranking_stability(
        request,
        snapshot_dir,
        vectors,
        ranking_identities,
        expected_snapshot_id=snapshot_id,
        previous_session_id=measurement["verification_session_id"],
        k=DEFAULT_K,
    )
    proxy_parity = evaluate_numpy_proxy_parity(
        verified["cells"],
        vectors,
        ranking_identities,
        k=DEFAULT_K,
        score_tolerance=manifest["persisted_parity"]["score_tolerance"],
    )
    proxy_ranking_identities = proxy_parity.pop(
        "_proxy_ranking_identities"
    )
    quality = evaluate_persisted_ranking_quality(
        ranking_identities,
        suite["cases"],
    )
    proxy_quality = evaluate_persisted_ranking_quality(
        proxy_ranking_identities,
        suite["cases"],
    )
    proxy_quality_metrics_equal = quality["metrics"] == proxy_quality["metrics"]
    proxy_parity["persisted_session_metrics"] = quality["metrics"]
    proxy_parity["numpy_proxy_metrics"] = proxy_quality["metrics"]
    proxy_parity["quality_metrics_equal"] = proxy_quality_metrics_equal
    proxy_parity["passed"] = (
        proxy_parity["passed"] is True and proxy_quality_metrics_equal
    )
    lexical_baseline = retrieval_benchmark.run_lexical_benchmark(
        suite["documents"],
        suite["queries"],
        suite["cases"],
        suite["tokenizer"],
    )
    provider_identity_evidence = {
        **identity_before,
        "catalog_contract_verified_before_embedding": True,
        "catalog_contract_verified_after_embedding": True,
        "response_digest_attested": False,
    }
    persisted_candidate_gate_input = {
        "measurement_complete": True,
        "metrics": quality["metrics"],
        "per_query": quality["per_query"],
        "latency_ms": measurement["latency_ms"],
        "provider_identity_evidence": provider_identity_evidence,
    }
    differential_gate = retrieval_benchmark.differential_gate(
        lexical_baseline,
        persisted_candidate_gate_input,
    )
    p95 = measurement["latency_ms"]["search"]["p95"]
    threshold = DEFAULT_MAX_SEARCH_P95_MS
    latency_target_pass = p95 < threshold
    within_session_stability_pass = (
        measurement["ranking_repeat_mismatch_count"] == 0
    )
    snapshot_reopen_stability_pass = (
        snapshot_reopen_stability["ranking_mismatch_count"] == 0
    )
    candidate_benchmark_pass = (
        latency_target_pass
        and differential_gate["passed"] is True
        and within_session_stability_pass
        and snapshot_reopen_stability_pass
        and proxy_parity["passed"] is True
    )
    cell_count = len(manifest["cells"])
    search_count = measurement["search_execution_count"]
    return {
        "schema_version": REPORT_SCHEMA,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "retrieval_evidence_scope": LIVE_RETRIEVAL_SCOPE,
        "status": (
            "MEASURED_CANDIDATE_PASS"
            if candidate_benchmark_pass
            else "MEASURED_CANDIDATE_BLOCKED"
        ),
        "candidate_snapshot_verified": True,
        "candidate_snapshot_id": snapshot_id,
        "embedding_catalog_verified": True,
        "global_all_cell_search_verified": True,
        "source_reverification_scope": "session_open",
        "embedding_time_excluded_from_latency": True,
        "session_open_verification_time_excluded_from_latency": True,
        "embedding_latency_evaluated": False,
        "session_open_latency_evaluated": False,
        "end_to_end_query_latency_evaluated": False,
        "snapshot": {
            "snapshot_id": snapshot_id,
            "topology_digest": manifest["topology_digest"],
            "cell_count": cell_count,
            "total_vector_count": manifest["total_vector_count"],
            "faiss_version": manifest["faiss_version"],
            "faiss_compile_options": manifest["faiss_compile_options"],
            "faiss_binary_set_sha256": manifest["faiss_binary_set_sha256"],
        },
        "corpus": suite["corpus"],
        "embedding_provider_identity": provider_identity_evidence,
        "measurement": {
            **measurement,
            "all_live_leaf_cells_searched_per_operation": True,
            "cells_searched_per_operation": cell_count,
            "total_cell_searches": search_count * cell_count,
            "query_embedding_excluded_from_search_latency": True,
            "snapshot_open_verification_excluded_from_search_latency": True,
            "timer": "time.perf_counter_ns",
        },
        "persisted_candidate_quality": quality,
        "lexical_baseline_metrics": lexical_baseline["metrics"],
        "persisted_candidate_differential_gate": differential_gate,
        "differential_gate_definition_source": (
            "benchmark_magma_solver_retrieval.differential_gate"
        ),
        "ranking_stability": {
            "comparison_predicate": (
                "exact_solver_cell_projection_score_order_equality"
            ),
            "within_verified_session": {
                "baseline_search_count": measurement["query_count"],
                "repeat_comparison_count": measurement[
                    "ranking_repeat_check_count"
                ],
                "ranking_mismatch_count": measurement[
                    "ranking_repeat_mismatch_count"
                ],
                "passed": within_session_stability_pass,
            },
            "snapshot_reopen": {
                **snapshot_reopen_stability,
                "passed": snapshot_reopen_stability_pass,
            },
        },
        "numpy_proxy_parity": proxy_parity,
        "retrieval_label_isolation_check_count": (
            retrieval_label_isolation_check_count
        ),
        "retrieval_label_isolation_enforced": (
            retrieval_label_isolation_check_count == len(suite["cases"])
        ),
        "actual_persisted_faiss_quality_evaluated": True,
        "positive_ranking_quality_evaluated": True,
        "off_domain_rejection_evaluated": False,
        "candidate_benchmark_scope": (
            "paired_positive_ranking_and_search_latency_only_"
            "no_rejection_calibration"
        ),
        "candidate_benchmark_pass": candidate_benchmark_pass,
        "latency_gate": {
            "metric": "measurement.latency_ms.search.p95",
            "comparison": "strictly_less_than",
            "max_search_p95_ms": threshold,
            "observed_search_p95_ms": p95,
            "threshold_source": (
                "benchmark_magma_solver_retrieval."
                "differential_gate.exact_search_p95_below_10ms"
            ),
            "passed": latency_target_pass,
        },
        "cell_pruning_scale_trigger_crossed": not latency_target_pass,
        "latency_gate_decision": (
            "retain_verified_global_all_cells_at_observed_scale"
            if latency_target_pass
            else "evaluate_pruning_alternatives_before_scale_increase"
        ),
        "decision_scope": "positive_ranking_quality_and_latency_current_snapshot",
        "scale_scope": {
            "observed_cell_count": cell_count,
            "observed_total_vector_count": manifest["total_vector_count"],
            "multi_scale_generalization_supported": False,
            "remeasure_on_snapshot_change": True,
            "remeasurement_triggers": [
                "snapshot_id_changes",
                "cell_count_changes",
                "total_vector_count_changes",
                "faiss_build_identity_changes",
                "embedding_contract_changes",
            ],
        },
        "reproducibility": {
            "measurement_contract_frozen": True,
            "independent_run_count": 1,
            "cross_run_stability_evaluated": False,
            "snapshot_reopen_ranking_stability_evaluated": True,
        },
        "comparison_gaps": [
            "cell_local_faiss_quality_latency_not_evaluated",
            "centroid_top_m_not_evaluated",
            "faiss_ivf_nprobe_not_evaluated",
            "multi_scale_curve_not_evaluated",
            "off_domain_rejection_not_calibrated",
        ],
        "cell_pruning_evaluated": False,
        "cell_pruning_authorized": False,
        "runtime_authority_granted": False,
        "production_promotion_gate_pass": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument(
        "--corpus",
        default=str(retrieval_benchmark.DEFAULT_CORPUS.relative_to(ROOT)),
    )
    parser.add_argument(
        "--axioms-dir",
        default=str(retrieval_benchmark.DEFAULT_AXIOMS.relative_to(ROOT)),
    )
    parser.add_argument(
        "--ollama-url",
        default=retrieval_benchmark.DEFAULT_OLLAMA_URL,
    )
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--output")
    parser.add_argument("--no-write", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    raw_output = args.output or (
        f".codex-audit/magma_faiss_candidate_benchmark_{timestamp}.json"
    )
    try:
        output_path = (
            None
            if args.no_write
            else retrieval_benchmark.resolve_audit_output(raw_output)
        )
        report = run_live_latency_benchmark(
            args.request,
            args.snapshot,
            corpus_path=args.corpus,
            axioms_dir=args.axioms_dir,
            ollama_url=args.ollama_url,
            timeout_seconds=args.timeout_seconds,
        )
        if output_path is not None:
            retrieval_benchmark.write_audit_report(report, output_path)
            print(output_path.relative_to(ROOT).as_posix())
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
        return 0 if report["candidate_benchmark_pass"] is True else 1
    except (
        CandidateLatencyContractError,
        CandidateLatencyUnavailable,
        retrieval_benchmark.BenchmarkContractError,
    ) as exc:
        print(
            json.dumps(
                {
                    "schema_version": REPORT_SCHEMA,
                    "candidate_snapshot_verified": False,
                    "embedding_catalog_verified": False,
                    "global_all_cell_search_verified": False,
                    "actual_persisted_faiss_quality_evaluated": False,
                    "candidate_benchmark_pass": False,
                    "cell_pruning_authorized": False,
                    "runtime_authority_granted": False,
                    "production_promotion_gate_pass": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
