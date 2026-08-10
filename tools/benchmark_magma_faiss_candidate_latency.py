#!/usr/bin/env python3
"""Measure verified all-cell MAGMA FAISS candidate search latency.

This is a candidate-only evidence tool. It verifies an immutable snapshot and
its source projection commits, checks the pinned Ollama catalog identity before
and after embedding, and times only repeated searches through the opaque
verified session. Query embedding and session-open verification are excluded
from the search latency samples.

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


REPORT_SCHEMA = "magma.faiss.candidate_all_cell_latency.v1"
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
    for row in rows:
        if type(row) is not dict:
            raise CandidateLatencyContractError("verified_search_row_invalid")
        if row.get("snapshot_id") != expected_snapshot_id:
            raise CandidateLatencyContractError("search_snapshot_binding_mismatch")
        session_id = row.get("verification_session_id")
        if type(session_id) is not str or not _SESSION_ID.fullmatch(session_id):
            raise CandidateLatencyContractError("search_session_binding_invalid")
        session_ids.add(session_id)
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
            elapsed_ms = elapsed_ns / 1_000_000.0
            samples_ms.append(elapsed_ms)
            per_query_samples[query_index].append(elapsed_ms)

    if len(observed_session_ids) != 1:
        raise CandidateLatencyContractError("search_session_changed_during_run")
    per_query_p95 = [
        float(np.percentile(query_samples, 95))
        for query_samples in per_query_samples
    ]
    return {
        "query_count": len(vectors),
        "repetitions": repetitions,
        "warmup_rounds": warmup_rounds,
        "warmup_search_count": warmup_rounds * len(vectors),
        "search_execution_count": repetitions * len(vectors),
        "k": k,
        "verification_session_id": next(iter(observed_session_ids)),
        "latency_ms": {
            "search": _latency_summary(samples_ms),
            "per_query_p95": {
                "min": round(min(per_query_p95), 6),
                "median": round(float(np.median(per_query_p95)), 6),
                "max": round(max(per_query_p95), 6),
            },
        },
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
    corpus, _tokenizer, canonical_digest, raw_digest = (
        retrieval_benchmark.load_corpus(corpus_file, documents)
    )
    return {
        "queries": [case["query"] for case in corpus["cases"]],
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
                vectors = embedder.embed(
                    [profile.query_prefix + query for query in suite["queries"]],
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

    p95 = measurement["latency_ms"]["search"]["p95"]
    threshold = DEFAULT_MAX_SEARCH_P95_MS
    latency_target_pass = p95 < threshold
    cell_count = len(manifest["cells"])
    search_count = measurement["search_execution_count"]
    return {
        "schema_version": REPORT_SCHEMA,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "retrieval_evidence_scope": LIVE_RETRIEVAL_SCOPE,
        "status": (
            "MEASURED_RETAIN_ALL_CELLS"
            if latency_target_pass
            else "MEASURED_SCALE_TRIGGER_CROSSED"
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
        "embedding_provider_identity": {
            **identity_before,
            "catalog_contract_verified_before_embedding": True,
            "catalog_contract_verified_after_embedding": True,
            "response_digest_attested": False,
        },
        "measurement": {
            **measurement,
            "all_live_leaf_cells_searched_per_operation": True,
            "cells_searched_per_operation": cell_count,
            "total_cell_searches": search_count * cell_count,
            "query_embedding_excluded_from_search_latency": True,
            "snapshot_open_verification_excluded_from_search_latency": True,
            "timer": "time.perf_counter_ns",
        },
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
        "decision_scope": "latency_only_current_snapshot",
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
        },
        "comparison_gaps": [
            "cell_local_faiss_quality_latency_not_evaluated",
            "centroid_top_m_not_evaluated",
            "faiss_ivf_nprobe_not_evaluated",
            "multi_scale_curve_not_evaluated",
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
        f".codex-audit/magma_faiss_candidate_latency_{timestamp}.json"
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
        return 0 if report["latency_gate"]["passed"] is True else 1
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
