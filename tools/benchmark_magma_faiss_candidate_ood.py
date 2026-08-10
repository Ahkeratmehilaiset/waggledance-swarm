#!/usr/bin/env python3
"""Measure off-domain score separation for one verified FAISS candidate.

This is a candidate-only evidence tool. It freezes the existing manually
adjudicated off-domain corpus, embeds positive and off-domain query text through
the same verified provider, and searches the same immutable all-cell snapshot.
The resulting fixed threshold sweep is descriptive. It neither calibrates nor
selects a runtime threshold and grants no runtime or promotion authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import benchmark_magma_faiss_candidate_latency as candidate_benchmark  # noqa: E402
from tools import benchmark_magma_solver_retrieval as retrieval_benchmark  # noqa: E402
from tools import materialize_magma_faiss_candidate as candidate_snapshot  # noqa: E402


REPORT_SCHEMA = "magma.faiss.candidate_ood_threshold_sweep.v1"
OOD_CORPUS_SCHEMA = "wd.magma.ood_eval.v1"
EXPECTED_OOD_CORPUS_CANONICAL_SHA256 = (
    "0cecc9c211b5778939b71531dd06b0f2f746dbcd2ccffca6feb68bd38404b32b"
)
DEFAULT_OOD_CORPUS = (
    ROOT / "configs" / "benchmarks" / "magma_solver_ood_rejection_v1.json"
)
DEFAULT_K = 5
FIXED_THRESHOLDS = (0.45, 0.50, 0.55, 0.60, 0.62, 0.65, 0.70, 0.75)
HISTORICAL_REFERENCE_THRESHOLD = 0.60
EXISTING_OUTCOME_DEFAULT_THRESHOLD = 0.55

_OOD_CORPUS_KEYS = frozenset(
    {
        "schema_version",
        "description",
        "adjudication_scope",
        "expected_projection_solver_count",
        "case_order_semantics",
        "source_history",
        "policies",
        "cases",
    }
)
_SOURCE_HISTORY_KEYS = frozenset(
    {"query_source", "historical_decision", "note"}
)
_POLICY_KEYS = frozenset({"retriever_must_not_receive"})
_CASE_KEYS = frozenset({"query_id", "query", "expected_disposition"})


class CandidateOodContractError(ValueError):
    """An input or returned value violates the OOD measurement contract."""


class CandidateOodUnavailable(RuntimeError):
    """A required live dependency is unavailable without fallback."""


def _exact_keys(value: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    actual = frozenset(value)
    if actual != expected:
        raise CandidateOodContractError(
            f"{label}_keys_mismatch:missing={sorted(expected - actual)},"
            f"extra={sorted(actual - expected)}"
        )


def load_ood_corpus(
    path: Path | str,
    documents: Sequence[Mapping[str, Any]],
    positive_queries: Sequence[str],
    *,
    repo_root: Path = ROOT,
    expected_sha256: str = EXPECTED_OOD_CORPUS_CANONICAL_SHA256,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load the immutable OOD corpus and bind it to the current projection."""
    corpus_file, corpus_relative = candidate_benchmark._resolve_repo_input(
        path,
        repo_root=repo_root,
        label="ood_corpus_path",
    )
    if corpus_file.is_symlink() or not corpus_file.is_file():
        raise CandidateOodContractError("ood_corpus_must_be_regular_file")
    raw = corpus_file.read_bytes()
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CandidateOodContractError("ood_corpus_json_invalid") from exc
    if type(value) is not dict:
        raise CandidateOodContractError("ood_corpus_must_be_object")
    _exact_keys(value, _OOD_CORPUS_KEYS, "ood_corpus")
    canonical_digest = retrieval_benchmark.canonical_json_sha256(value)
    if canonical_digest != expected_sha256:
        raise CandidateOodContractError("ood_corpus_hash_mismatch")
    if value["schema_version"] != OOD_CORPUS_SCHEMA:
        raise CandidateOodContractError("ood_corpus_schema_mismatch")
    if (
        value["adjudication_scope"]
        != "candidate_only_current_frozen_22_solver_projection"
        or value["expected_projection_solver_count"] != 22
        or value["case_order_semantics"]
        != (
            "historical_sequence_only_each_case_is_independent_"
            "no_translation_pairing_claim"
        )
    ):
        raise CandidateOodContractError("ood_corpus_scope_mismatch")
    if len(documents) != value["expected_projection_solver_count"]:
        raise CandidateOodContractError("ood_corpus_projection_count_mismatch")
    source_history = value["source_history"]
    policies = value["policies"]
    if type(source_history) is not dict or type(policies) is not dict:
        raise CandidateOodContractError("ood_corpus_metadata_invalid")
    _exact_keys(source_history, _SOURCE_HISTORY_KEYS, "ood_source_history")
    _exact_keys(policies, _POLICY_KEYS, "ood_policies")
    if source_history != {
        "query_source": "tests/oracle/_off_domain.yaml",
        "historical_decision": "docs/plans/phase_D_decision_v2_2026-04-23.md",
        "note": (
            "Promoted from the manually labeled oracle; historical thresholds "
            "are references, not current runtime selections."
        ),
    }:
        raise CandidateOodContractError("ood_corpus_source_history_mismatch")
    if policies["retriever_must_not_receive"] != [
        "expected_disposition",
        "query_id",
    ]:
        raise CandidateOodContractError("ood_retriever_policy_mismatch")

    cases = value["cases"]
    if type(cases) is not list or len(cases) != 32:
        raise CandidateOodContractError("ood_case_count_mismatch")
    validated_cases: list[dict[str, str]] = []
    seen_queries: set[str] = set()
    for index, case in enumerate(cases, start=1):
        if type(case) is not dict:
            raise CandidateOodContractError(f"ood_case_{index}_must_be_object")
        _exact_keys(case, _CASE_KEYS, f"ood_case_{index}")
        expected_id = f"O{index:02d}"
        if (
            case["query_id"] != expected_id
            or type(case["query"]) is not str
            or not case["query"]
            or case["query"] != " ".join(case["query"].split())
            or case["expected_disposition"] != "reject_no_solver"
        ):
            raise CandidateOodContractError(f"ood_case_{index}_invalid")
        if case["query"] in seen_queries or case["query"] in positive_queries:
            raise CandidateOodContractError("ood_query_not_unique_or_overlaps_positive")
        seen_queries.add(case["query"])
        validated_cases.append(dict(case))
    return (
        {**value, "cases": validated_cases},
        {
            "path": corpus_relative,
            "canonical_sha256": canonical_digest,
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "cases": len(validated_cases),
            "expected_projection_solver_count": value[
                "expected_projection_solver_count"
            ],
            "case_order_semantics": value["case_order_semantics"],
            "adjudication_source": source_history["query_source"],
            "historical_threshold_source": source_history["historical_decision"],
        },
    )


def _score_summary(values: Sequence[float]) -> dict[str, float]:
    if not values or any(not math.isfinite(value) for value in values):
        raise CandidateOodContractError("top_score_samples_invalid")
    samples = np.asarray(values, dtype=np.float64)
    return {
        "min": round(float(samples.min()), 6),
        "p50": round(float(np.percentile(samples, 50)), 6),
        "p95": round(float(np.percentile(samples, 95)), 6),
        "max": round(float(samples.max()), 6),
        "mean": round(float(samples.mean()), 6),
    }


def evaluate_threshold_sweep(
    positive_top_scores: Sequence[float],
    positive_top1_correct: Sequence[bool],
    ood_top_scores: Sequence[float],
    *,
    thresholds: Sequence[float] = FIXED_THRESHOLDS,
) -> dict[str, Any]:
    """Describe score tradeoffs; this function deliberately has no pass gate."""
    if (
        not positive_top_scores
        or len(positive_top_scores) != len(positive_top1_correct)
        or not ood_top_scores
        or tuple(thresholds) != FIXED_THRESHOLDS
        or any(type(value) is not bool for value in positive_top1_correct)
    ):
        raise CandidateOodContractError("threshold_sweep_inputs_invalid")
    positive_scores = [float(value) for value in positive_top_scores]
    ood_scores = [float(value) for value in ood_top_scores]
    if any(not math.isfinite(value) for value in positive_scores + ood_scores):
        raise CandidateOodContractError("threshold_sweep_scores_invalid")
    correct_total = sum(positive_top1_correct)
    rows: list[dict[str, Any]] = []
    for threshold in thresholds:
        positive_accepted = [score >= threshold for score in positive_scores]
        ood_rejected = [score < threshold for score in ood_scores]
        correct_retained = sum(
            accepted and correct
            for accepted, correct in zip(
                positive_accepted,
                positive_top1_correct,
            )
        )
        rows.append(
            {
                "threshold": threshold,
                "acceptance_comparison": "top_score_greater_than_or_equal",
                "positive_accepted_count": sum(positive_accepted),
                "positive_total": len(positive_scores),
                "correct_positive_top1_retained_count": correct_retained,
                "correct_positive_top1_total": correct_total,
                "incorrect_positive_top1_accepted_count": sum(
                    accepted and not correct
                    for accepted, correct in zip(
                        positive_accepted,
                        positive_top1_correct,
                    )
                ),
                "incorrect_positive_top1_total": (
                    len(positive_scores) - correct_total
                ),
                "ood_rejected_count": sum(ood_rejected),
                "ood_total": len(ood_scores),
                "ood_false_accepted_count": len(ood_scores) - sum(ood_rejected),
            }
        )
    return {
        "measurement_only_no_pass_gate": True,
        "threshold_count": len(rows),
        "positive_top_score_distribution": _score_summary(positive_scores),
        "ood_top_score_distribution": _score_summary(ood_scores),
        "rows": rows,
    }


def _search_label_blind_queries(
    session: Any,
    vectors: Any,
    *,
    expected_snapshot_id: str,
) -> tuple[list[tuple[tuple[Any, ...], ...]], str]:
    query_vectors = np.asarray(vectors, dtype=np.float32)
    if query_vectors.ndim != 2 or not len(query_vectors):
        raise CandidateOodContractError("query_vectors_invalid")
    rankings: list[tuple[tuple[Any, ...], ...]] = []
    session_ids: set[str] = set()
    for vector in query_vectors:
        rows = session.search(vector, k=DEFAULT_K)
        try:
            session_ids.add(
                candidate_benchmark._validate_search_rows(
                    rows,
                    expected_snapshot_id=expected_snapshot_id,
                    k=DEFAULT_K,
                )
            )
        except candidate_benchmark.CandidateLatencyContractError as exc:
            raise CandidateOodContractError(str(exc)) from exc
        rankings.append(candidate_benchmark._ranking_identity(rows))
    if len(session_ids) != 1:
        raise CandidateOodContractError("search_session_changed_during_run")
    return rankings, next(iter(session_ids))


def _ood_ranking_evidence(
    rankings: Sequence[Sequence[tuple[Any, ...]]],
    cases: Sequence[Mapping[str, str]],
) -> list[dict[str, Any]]:
    if not cases or len(rankings) != len(cases):
        raise CandidateOodContractError("ood_ranking_case_count_mismatch")
    evidence: list[dict[str, Any]] = []
    for ranking, case in zip(rankings, cases):
        if len(ranking) != DEFAULT_K:
            raise CandidateOodContractError("ood_ranking_length_mismatch")
        top_score = float(ranking[0][3])
        evidence.append(
            {
                "query_id": case["query_id"],
                "query": case["query"],
                "expected_disposition": case["expected_disposition"],
                "top_score": top_score,
                "top_solver": ranking[0][0],
                "top_cell": ranking[0][1],
                "top_projection_id": ranking[0][2],
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
    return evidence


def run_live_ood_measurement(
    request_path: Path | str,
    snapshot_dir: Path | str,
    *,
    positive_corpus_path: Path | str = retrieval_benchmark.DEFAULT_CORPUS,
    ood_corpus_path: Path | str = DEFAULT_OOD_CORPUS,
    axioms_dir: Path | str = retrieval_benchmark.DEFAULT_AXIOMS,
    ollama_url: str = retrieval_benchmark.DEFAULT_OLLAMA_URL,
    timeout_seconds: float = 120.0,
    repo_root: Path = ROOT,
) -> dict[str, Any]:
    """Run one verified score-separation measurement without selecting policy."""
    if (
        not isinstance(timeout_seconds, (int, float))
        or isinstance(timeout_seconds, bool)
        or not math.isfinite(float(timeout_seconds))
        or float(timeout_seconds) <= 0.0
    ):
        raise CandidateOodContractError("timeout_seconds_must_be_positive_finite")

    try:
        positive_suite = candidate_benchmark._load_query_suite(
            positive_corpus_path,
            axioms_dir,
            repo_root=repo_root,
        )
        ood_corpus, ood_corpus_evidence = load_ood_corpus(
            ood_corpus_path,
            positive_suite["documents"],
            positive_suite["queries"],
            repo_root=repo_root,
        )
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
        raise CandidateOodUnavailable(str(exc)) from exc
    except (
        candidate_snapshot.CandidateContractError,
        candidate_benchmark.CandidateLatencyContractError,
        retrieval_benchmark.BenchmarkContractError,
    ) as exc:
        raise CandidateOodContractError(str(exc)) from exc

    manifest = verified["manifest"]
    snapshot_id = manifest["snapshot_id"]
    if session.snapshot_id != snapshot_id:
        session.close()
        raise CandidateOodContractError("session_snapshot_binding_mismatch")
    if DEFAULT_K > manifest["total_vector_count"]:
        session.close()
        raise CandidateOodContractError("k_exceeds_candidate_vector_count")
    if len(positive_suite["documents"]) != manifest["total_vector_count"]:
        session.close()
        raise CandidateOodContractError("snapshot_projection_count_mismatch")
    if len(request.cells) != len(manifest["cells"]):
        session.close()
        raise CandidateOodContractError("request_cell_count_mismatch")
    try:
        profile = candidate_snapshot._profile_from_contract(
            request.embedding_contract
        )
    except candidate_snapshot.CandidateContractError as exc:
        session.close()
        raise CandidateOodContractError(str(exc)) from exc

    positive_queries = list(positive_suite["queries"])
    ood_queries = [case["query"] for case in ood_corpus["cases"]]
    combined_queries = positive_queries + ood_queries
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
                    raise CandidateOodContractError(
                        "embedding_provider_identity_before_mismatch"
                    )
                query_inputs, isolation_check_count = (
                    candidate_benchmark._build_label_blind_query_inputs(
                        combined_queries,
                        query_prefix=profile.query_prefix,
                    )
                )
                vectors = embedder.embed(
                    query_inputs,
                    profile,
                    label="candidate_ood_query_embeddings",
                )
                rankings, verification_session_id = _search_label_blind_queries(
                    session,
                    vectors,
                    expected_snapshot_id=snapshot_id,
                )
                identity_after = embedder.verify_profile(profile)
                if identity_before != identity_after:
                    raise CandidateOodUnavailable(
                        "embedding_model_catalog_changed_during_run"
                    )
                if not retrieval_benchmark.provider_identity_matches_profile(
                    identity_after,
                    profile,
                ):
                    raise CandidateOodContractError(
                        "embedding_provider_identity_after_mismatch"
                    )
        except retrieval_benchmark.BenchmarkUnavailable as exc:
            raise CandidateOodUnavailable(str(exc)) from exc
        except retrieval_benchmark.EmbeddingValidationError as exc:
            raise CandidateOodContractError(str(exc)) from exc
    finally:
        session.close()

    positive_rankings = rankings[: len(positive_queries)]
    ood_rankings = rankings[len(positive_queries) :]
    positive_quality = candidate_benchmark.evaluate_persisted_ranking_quality(
        positive_rankings,
        positive_suite["cases"],
    )
    ood_evidence = _ood_ranking_evidence(ood_rankings, ood_corpus["cases"])
    positive_scores = [float(ranking[0][3]) for ranking in positive_rankings]
    positive_correct = [
        bool(row["correct_at_1"])
        for row in positive_quality["per_query"]
    ]
    ood_scores = [float(ranking[0][3]) for ranking in ood_rankings]
    sweep = evaluate_threshold_sweep(
        positive_scores,
        positive_correct,
        ood_scores,
    )
    score_distributions_overlap = max(ood_scores) >= min(positive_scores)
    highest_ood = max(ood_evidence, key=lambda row: float(row["top_score"]))
    provider_identity_evidence = {
        **identity_before,
        "catalog_contract_verified_before_embedding": True,
        "catalog_contract_verified_after_embedding": True,
        "response_digest_attested": False,
    }
    cell_count = len(manifest["cells"])
    search_count = len(rankings)
    return {
        "schema_version": REPORT_SCHEMA,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "MEASURED_NOT_CALIBRATED",
        "retrieval_evidence_scope": (
            "verified_snapshot_session_global_all_cells_positive_and_ood"
        ),
        "measurement_complete": True,
        "candidate_snapshot_verified": True,
        "candidate_snapshot_id": snapshot_id,
        "embedding_catalog_verified": True,
        "global_all_cell_search_verified": True,
        "source_reverification_scope": "session_open",
        "snapshot": {
            "snapshot_id": snapshot_id,
            "topology_digest": manifest["topology_digest"],
            "cell_count": cell_count,
            "total_vector_count": manifest["total_vector_count"],
            "faiss_version": manifest["faiss_version"],
            "faiss_compile_options": manifest["faiss_compile_options"],
            "faiss_binary_set_sha256": manifest["faiss_binary_set_sha256"],
        },
        "positive_corpus": positive_suite["corpus"],
        "ood_corpus": ood_corpus_evidence,
        "embedding_provider_identity": provider_identity_evidence,
        "measurement": {
            "verification_session_id": verification_session_id,
            "positive_query_count": len(positive_queries),
            "ood_query_count": len(ood_queries),
            "query_embedding_count": len(combined_queries),
            "search_execution_count": search_count,
            "k": DEFAULT_K,
            "all_live_leaf_cells_searched_per_operation": True,
            "cells_searched_per_operation": cell_count,
            "total_cell_searches": search_count * cell_count,
        },
        "positive_ranking_quality": positive_quality,
        "off_domain_ranking_evidence": ood_evidence,
        "threshold_sweep": sweep,
        "threshold_provenance": {
            "fixed_sweep_values": list(FIXED_THRESHOLDS),
            "fixed_sweep_source": (
                "docs/plans/phase_D_decision_v2_2026-04-23.md historical "
                "comparison grid"
            ),
            "existing_outcome_default_reference": {
                "threshold": EXISTING_OUTCOME_DEFAULT_THRESHOLD,
                "source": "tools/evaluate_magma_executable_outcomes.py DEFAULT_MIN_SCORE",
                "runtime_selection_for_this_candidate": False,
            },
            "historical_candidate_reference": {
                "threshold": HISTORICAL_REFERENCE_THRESHOLD,
                "source": "docs/plans/phase_D_decision_v2_2026-04-23.md",
                "source_scope": "older_14_solver_nonpersisted_measurement",
                "current_runtime_selection": False,
            },
        },
        "highest_ood_false_accept_at_historical_reference": {
            **highest_ood,
            "reference_threshold": HISTORICAL_REFERENCE_THRESHOLD,
            "accepted_at_reference": (
                float(highest_ood["top_score"])
                >= HISTORICAL_REFERENCE_THRESHOLD
            ),
        },
        "retriever_input_fields": ["query"],
        "retriever_forbidden_inputs": [
            "expected_solver",
            "expected_cell",
            "stratum",
            "expected_disposition",
            "query_id",
        ],
        "retrieval_label_isolation_check_count": isolation_check_count,
        "retrieval_label_isolation_expected_count": len(combined_queries),
        "retrieval_label_isolation_enforced": (
            isolation_check_count == len(combined_queries)
        ),
        "positive_ranking_quality_evaluated": True,
        "off_domain_rejection_evaluated": True,
        "off_domain_rejection_calibrated": False,
        "calibration_gate_defined": False,
        "calibration_gate_pass": False,
        "runtime_threshold_selected": False,
        "selected_runtime_threshold": None,
        "threshold_sweep_is_descriptive_only": True,
        "calibration_readiness": {
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
            "score_distributions_overlap": score_distributions_overlap,
            "single_score_threshold_perfect_separation_observed": (
                not score_distributions_overlap
            ),
            "required_input_admission_signal_evaluated": False,
        },
        "measurement_exit_zero_semantics": (
            "measurement_completed_not_gate_pass_or_runtime_selection"
        ),
        "candidate_mode_change_authorized": False,
        "cell_pruning_authorized": False,
        "runtime_authority_granted": False,
        "production_promotion_gate_pass": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument(
        "--positive-corpus",
        default=str(retrieval_benchmark.DEFAULT_CORPUS.relative_to(ROOT)),
    )
    parser.add_argument(
        "--ood-corpus",
        default=str(DEFAULT_OOD_CORPUS.relative_to(ROOT)),
    )
    parser.add_argument(
        "--axioms-dir",
        default=str(retrieval_benchmark.DEFAULT_AXIOMS.relative_to(ROOT)),
    )
    parser.add_argument("--ollama-url", default=retrieval_benchmark.DEFAULT_OLLAMA_URL)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--output")
    parser.add_argument("--no-write", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    raw_output = args.output or (
        f".codex-audit/magma_faiss_candidate_ood_{timestamp}.json"
    )
    try:
        output_path = (
            None
            if args.no_write
            else retrieval_benchmark.resolve_audit_output(raw_output)
        )
        report = run_live_ood_measurement(
            args.request,
            args.snapshot,
            positive_corpus_path=args.positive_corpus,
            ood_corpus_path=args.ood_corpus,
            axioms_dir=args.axioms_dir,
            ollama_url=args.ollama_url,
            timeout_seconds=args.timeout_seconds,
        )
        if output_path is not None:
            retrieval_benchmark.write_audit_report(report, output_path)
            print(output_path.relative_to(ROOT).as_posix())
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
        return 0
    except (
        CandidateOodContractError,
        CandidateOodUnavailable,
        retrieval_benchmark.BenchmarkContractError,
    ) as exc:
        print(
            json.dumps(
                {
                    "schema_version": REPORT_SCHEMA,
                    "status": "MEASUREMENT_FAILED",
                    "measurement_complete": False,
                    "candidate_snapshot_verified": False,
                    "embedding_catalog_verified": False,
                    "global_all_cell_search_verified": False,
                    "off_domain_rejection_evaluated": False,
                    "off_domain_rejection_calibrated": False,
                    "calibration_gate_defined": False,
                    "calibration_gate_pass": False,
                    "runtime_threshold_selected": False,
                    "selected_runtime_threshold": None,
                    "candidate_mode_change_authorized": False,
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
