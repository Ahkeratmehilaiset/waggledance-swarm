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
import hashlib
import json
import math
import platform
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
from waggledance.core.hex_cell_topology import ALL_CELLS  # noqa: E402


REPORT_SCHEMA = "magma.faiss.candidate_all_cell_benchmark.v3"
LIVE_RETRIEVAL_SCOPE = "verified_snapshot_session_global_all_cells"
DEFAULT_REPETITIONS = 100
DEFAULT_WARMUP_ROUNDS = 1
DEFAULT_K = 5
DEFAULT_MAX_SEARCH_P95_MS = 10.0
SYNTHETIC_FORECAST_SCHEMA = "magma.faiss.synthetic_scale_forecast.v1"
SYNTHETIC_SCALE_MULTIPLIERS = (10, 100, 1000)
SYNTHETIC_SCALE_DISTRIBUTIONS = ("uniform", "observed_proportional")
SYNTHETIC_SCALE_ANCHOR_TOTAL = 22
SYNTHETIC_SCALE_DIMENSION = 768
SYNTHETIC_SCALE_SEED = 20260812
SYNTHETIC_SCALE_K = 5
SYNTHETIC_SCALE_REPETITIONS = 200
SYNTHETIC_SCALE_WARMUP_ROUNDS = 5
SYNTHETIC_SCALE_OMP_THREADS = 1
SYNTHETIC_SCALE_EXCLUSIVE_LEAF_LIMIT = 10_000
SYNTHETIC_SCALE_CELL_ORDER = tuple(sorted(ALL_CELLS))
SYNTHETIC_SCALE_ANCHOR_SNAPSHOT_ID = (
    "faisscand_468e550ca44fb64912e82fc663e27b3f597ad6cc50fa09e56ada0c0e17ba6ee7"
)
SYNTHETIC_SCALE_ANCHOR_TOPOLOGY_DIGEST = (
    "sha256:af10918c32175f637704cd030270c80750534aedd96d0368ddfbfe646378a006"
)
SYNTHETIC_SCALE_ANCHOR_COUNTS = (
    ("energy", 3),
    ("general", 1),
    ("learning", 2),
    ("math", 2),
    ("safety", 2),
    ("seasonal", 5),
    ("system", 3),
    ("thermal", 4),
)
SYNTHETIC_SCALE_ANCHOR_SOURCE_COMMITS = (
    (
        "energy",
        "proj_4ecb6a9d5888276ce255fdb398fca1aef5a1dc322f5db9facada9c7129edfbea",
    ),
    (
        "general",
        "proj_2744e0a0d09f2802350c17322183e6c5d5229278396a8f15fce4ed5d02a49f2e",
    ),
    (
        "learning",
        "proj_76ceb6c3db823c7798a3fa4aada0d260f9969c8fdd6710ff62d84155a261453c",
    ),
    (
        "math",
        "proj_209a67870c7591acc94a592cda27bf099aea233f218397dd042ab76a8588a1d5",
    ),
    (
        "safety",
        "proj_aa9007aa5f3f2b47ba39c7db248d47cc8e360d2add596d13fe35859a1d524890",
    ),
    (
        "seasonal",
        "proj_33abf23dc091133e2cbe0f06e6c841bc4d18d9f401150c59ef19d73c545d7990",
    ),
    (
        "system",
        "proj_cdbeb3b814a58072d3fd22c65e2ae5521ae69a7245be8b96869fc970e676b42a",
    ),
    (
        "thermal",
        "proj_e90fbdde0ba767eb451d28f571a891560c2b16b0dfb5a1ef1e7990a1cb0db94f",
    ),
)
SYNTHETIC_SCALE_ANCHOR_FAISS_IDENTITY = (
    ("faiss_version", "1.13.2"),
    ("faiss_compile_options", "AVX2"),
    (
        "faiss_binary_set_sha256",
        "sha256:e565b14a3f25198eafad0daf6cd566a2bcdda3327cdbebdf7d3bea4bd6bcbd94",
    ),
)
_SESSION_ID = re.compile(r"^faisssession_[0-9a-f]{32}$")
_FULL_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


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


def _sha256_json(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _validate_scale_anchor(manifest: Any) -> dict[str, Any]:
    """Validate the one immutable snapshot authorized as forecast input."""
    if not isinstance(manifest, Mapping):
        raise CandidateLatencyContractError("synthetic_scale_anchor_invalid")
    if SYNTHETIC_SCALE_CELL_ORDER != tuple(
        cell_id for cell_id, _count in SYNTHETIC_SCALE_ANCHOR_COUNTS
    ):
        raise CandidateLatencyContractError("synthetic_scale_contract_cells_invalid")
    snapshot_id = manifest.get("snapshot_id")
    if (
        type(snapshot_id) is not str
        or snapshot_id != SYNTHETIC_SCALE_ANCHOR_SNAPSHOT_ID
    ):
        raise CandidateLatencyContractError(
            "synthetic_scale_anchor_snapshot_mismatch"
        )
    topology_digest = manifest.get("topology_digest")
    if (
        type(topology_digest) is not str
        or topology_digest != SYNTHETIC_SCALE_ANCHOR_TOPOLOGY_DIGEST
    ):
        raise CandidateLatencyContractError(
            "synthetic_scale_anchor_topology_mismatch"
        )
    total_vector_count = manifest.get("total_vector_count")
    if (
        type(total_vector_count) is not int
        or total_vector_count != SYNTHETIC_SCALE_ANCHOR_TOTAL
    ):
        raise CandidateLatencyContractError("synthetic_scale_anchor_total_mismatch")

    embedding_contract = manifest.get("embedding_contract")
    if (
        type(embedding_contract) is not dict
        or type(embedding_contract.get("dimension")) is not int
        or embedding_contract["dimension"] != SYNTHETIC_SCALE_DIMENSION
    ):
        raise CandidateLatencyContractError(
            "synthetic_scale_anchor_dimension_mismatch"
        )

    expected_counts = dict(SYNTHETIC_SCALE_ANCHOR_COUNTS)
    expected_commits = dict(SYNTHETIC_SCALE_ANCHOR_SOURCE_COMMITS)
    cells = manifest.get("cells")
    if type(cells) is not list or len(cells) != len(SYNTHETIC_SCALE_CELL_ORDER):
        raise CandidateLatencyContractError("synthetic_scale_anchor_cells_invalid")
    normalized_cells: list[dict[str, Any]] = []
    observed_ids: list[str] = []
    for cell in cells:
        if type(cell) is not dict:
            raise CandidateLatencyContractError(
                "synthetic_scale_anchor_cell_invalid"
            )
        cell_id = cell.get("cell_id")
        vector_count = cell.get("vector_count")
        source_commit_id = cell.get("source_projection_commit_id")
        if (
            type(cell_id) is not str
            or cell_id not in expected_counts
            or type(vector_count) is not int
            or vector_count < 0
            or vector_count != expected_counts[cell_id]
            or type(source_commit_id) is not str
            or source_commit_id != expected_commits[cell_id]
        ):
            raise CandidateLatencyContractError(
                "synthetic_scale_anchor_cell_binding_mismatch"
            )
        observed_ids.append(cell_id)
        normalized_cells.append(
            {
                "cell_id": cell_id,
                "vector_count": vector_count,
                "source_projection_commit_id": source_commit_id,
            }
        )
    if tuple(observed_ids) != SYNTHETIC_SCALE_CELL_ORDER:
        raise CandidateLatencyContractError("synthetic_scale_anchor_cell_order_invalid")
    if sum(cell["vector_count"] for cell in normalized_cells) != (
        SYNTHETIC_SCALE_ANCHOR_TOTAL
    ):
        raise CandidateLatencyContractError("synthetic_scale_anchor_sum_mismatch")

    expected_faiss = dict(SYNTHETIC_SCALE_ANCHOR_FAISS_IDENTITY)
    for key, expected in expected_faiss.items():
        observed = manifest.get(key)
        if type(observed) is not str or observed != expected:
            raise CandidateLatencyContractError(
                f"synthetic_scale_anchor_{key}_mismatch"
            )
    return {
        "snapshot_id": manifest["snapshot_id"],
        "topology_digest": manifest["topology_digest"],
        "total_vector_count": manifest["total_vector_count"],
        "dimension": embedding_contract["dimension"],
        "cells": normalized_cells,
        **expected_faiss,
    }


def _require_forecast_leaf_limit(cell_counts: Mapping[str, Any]) -> None:
    if not cell_counts:
        raise CandidateLatencyContractError("synthetic_scale_plan_cells_invalid")
    for cell_id, count in cell_counts.items():
        if type(cell_id) is not str or not cell_id or type(count) is not int:
            raise CandidateLatencyContractError("synthetic_scale_plan_cells_invalid")
        if count <= 0:
            raise CandidateLatencyContractError("synthetic_scale_plan_cells_invalid")
        if count >= SYNTHETIC_SCALE_EXCLUSIVE_LEAF_LIMIT:
            raise CandidateLatencyContractError("index_tier_transition_required")


def _validate_forecast_plan(plan: Any) -> dict[str, Any]:
    if type(plan) is not dict or any(type(key) is not str for key in plan):
        raise CandidateLatencyContractError("synthetic_scale_plan_invalid")
    multiplier = plan.get("scale_multiplier")
    distribution = plan.get("distribution")
    if type(multiplier) is not int or multiplier not in SYNTHETIC_SCALE_MULTIPLIERS:
        raise CandidateLatencyContractError("synthetic_scale_plan_invalid")
    if (
        type(distribution) is not str
        or distribution not in SYNTHETIC_SCALE_DISTRIBUTIONS
    ):
        raise CandidateLatencyContractError("synthetic_scale_plan_invalid")
    total = SYNTHETIC_SCALE_ANCHOR_TOTAL * multiplier
    if (
        type(plan.get("total_vector_count")) is not int
        or plan["total_vector_count"] != total
    ):
        raise CandidateLatencyContractError("synthetic_scale_plan_invalid")
    if distribution == "observed_proportional":
        expected_counts = {
            cell_id: count * multiplier
            for cell_id, count in SYNTHETIC_SCALE_ANCHOR_COUNTS
        }
    else:
        quotient, remainder = divmod(total, len(SYNTHETIC_SCALE_CELL_ORDER))
        expected_counts = {
            cell_id: quotient + (index < remainder)
            for index, cell_id in enumerate(SYNTHETIC_SCALE_CELL_ORDER)
        }
    cells = plan.get("cell_counts")
    if type(cells) is not list or len(cells) != len(SYNTHETIC_SCALE_CELL_ORDER):
        raise CandidateLatencyContractError("synthetic_scale_plan_cells_invalid")
    normalized_cells: list[dict[str, Any]] = []
    for index, cell in enumerate(cells):
        expected_cell_id = SYNTHETIC_SCALE_CELL_ORDER[index]
        if (
            type(cell) is not dict
            or any(type(key) is not str for key in cell)
            or set(cell) != {"cell_id", "vector_count"}
            or type(cell.get("cell_id")) is not str
            or cell["cell_id"] != expected_cell_id
            or type(cell.get("vector_count")) is not int
            or cell["vector_count"] != expected_counts[expected_cell_id]
        ):
            raise CandidateLatencyContractError(
                "synthetic_scale_plan_cells_invalid"
            )
        normalized_cells.append(dict(cell))
    _require_forecast_leaf_limit(expected_counts)
    raw_bytes = total * SYNTHETIC_SCALE_DIMENSION * np.dtype(np.float32).itemsize
    if (
        type(plan.get("max_leaf_vector_count")) is not int
        or plan["max_leaf_vector_count"] != max(expected_counts.values())
        or type(plan.get("raw_float32_vector_payload_bytes")) is not int
        or plan["raw_float32_vector_payload_bytes"] != raw_bytes
        or set(plan)
        != {
            "scale_multiplier",
            "distribution",
            "total_vector_count",
            "cell_counts",
            "max_leaf_vector_count",
            "raw_float32_vector_payload_bytes",
        }
    ):
        raise CandidateLatencyContractError("synthetic_scale_plan_invalid")
    return {
        "scale_multiplier": multiplier,
        "distribution": distribution,
        "total_vector_count": total,
        "cell_counts": normalized_cells,
        "max_leaf_vector_count": max(expected_counts.values()),
        "raw_float32_vector_payload_bytes": raw_bytes,
    }


def _derive_forecast_cell_counts(
    manifest: Any,
    *,
    multiplier: int,
    distribution: str,
) -> dict[str, Any]:
    anchor = _validate_scale_anchor(manifest)
    if type(multiplier) is not int or multiplier not in SYNTHETIC_SCALE_MULTIPLIERS:
        raise CandidateLatencyContractError(
            "synthetic_scale_multiplier_not_in_frozen_contract"
        )
    if (
        type(distribution) is not str
        or distribution not in SYNTHETIC_SCALE_DISTRIBUTIONS
    ):
        raise CandidateLatencyContractError(
            "synthetic_scale_distribution_not_in_frozen_contract"
        )
    total = anchor["total_vector_count"] * multiplier
    if distribution == "observed_proportional":
        counts = {
            cell["cell_id"]: cell["vector_count"] * multiplier
            for cell in anchor["cells"]
        }
    else:
        quotient, remainder = divmod(total, len(SYNTHETIC_SCALE_CELL_ORDER))
        counts = {
            cell_id: quotient + (index < remainder)
            for index, cell_id in enumerate(SYNTHETIC_SCALE_CELL_ORDER)
        }
    if sum(counts.values()) != total:
        raise CandidateLatencyContractError("synthetic_scale_plan_sum_mismatch")
    _require_forecast_leaf_limit(counts)
    return _validate_forecast_plan({
        "scale_multiplier": multiplier,
        "distribution": distribution,
        "total_vector_count": total,
        "cell_counts": [
            {"cell_id": cell_id, "vector_count": counts[cell_id]}
            for cell_id in SYNTHETIC_SCALE_CELL_ORDER
        ],
        "max_leaf_vector_count": max(counts.values()),
        "raw_float32_vector_payload_bytes": (
            total * SYNTHETIC_SCALE_DIMENSION * np.dtype(np.float32).itemsize
        ),
    })


def _synthetic_seed(value: Any) -> int:
    digest = hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def _synthetic_normalized_matrix(
    rows: int,
    dimension: int,
    *,
    seed_material: Any,
    label: str,
) -> np.ndarray:
    rng = np.random.default_rng(_synthetic_seed(seed_material))
    values = rng.standard_normal((rows, dimension), dtype=np.float32)
    try:
        return retrieval_benchmark.normalize_embedding_matrix(
            values,
            expected_rows=rows,
            expected_dimension=dimension,
            label=label,
        )
    except retrieval_benchmark.EmbeddingValidationError as exc:
        raise CandidateLatencyContractError(str(exc)) from exc


def _validate_synthetic_search_results(
    results: Any,
    *,
    k: int,
    expected_snapshot_id: str,
) -> None:
    if type(results) is not list or len(results) != k:
        raise CandidateLatencyContractError("synthetic_scale_search_rows_invalid")
    solver_ids: set[str] = set()
    for result in results:
        if (
            type(result) is not dict
            or type(result.get("canonical_solver_id")) is not str
            or not result["canonical_solver_id"]
            or result["canonical_solver_id"] in solver_ids
            or type(result.get("cell_id")) is not str
            or result.get("cell_id") not in SYNTHETIC_SCALE_CELL_ORDER
            or type(result.get("projection_id")) is not str
            or not _FULL_DIGEST.fullmatch(result["projection_id"])
            or result.get("snapshot_id") != expected_snapshot_id
            or result.get("verification_session_id") is not None
            or result.get("source_commit_reverified") is not False
            or result.get("source_reverification_scope") != "none"
            or result.get("source_reverified_during_search_call") is not False
            or result.get("receipt_bound") is not False
            or result.get("receipt_structure_reverified") is not False
            or result.get("receipt_authenticity_verified") is not False
            or result.get("solver_outcome_verified") is not False
            or result.get("runtime_authority_granted") is not False
            or not isinstance(result.get("score"), (int, float))
            or isinstance(result.get("score"), bool)
            or not math.isfinite(float(result["score"]))
        ):
            raise CandidateLatencyContractError(
                "synthetic_scale_search_evidence_boundary_invalid"
            )
        solver_ids.add(result["canonical_solver_id"])


def _measure_synthetic_scale_row(
    plan: Mapping[str, Any],
    query_vector: np.ndarray,
    *,
    faiss_module: Any,
    clock_ns: Callable[[], int],
) -> dict[str, Any]:
    validated_plan = _validate_forecast_plan(plan)
    cell_counts = validated_plan["cell_counts"]
    scenario_contract = {
        "base_seed": SYNTHETIC_SCALE_SEED,
        "anchor_snapshot_id": SYNTHETIC_SCALE_ANCHOR_SNAPSHOT_ID,
        "scale_multiplier": validated_plan["scale_multiplier"],
        "distribution": validated_plan["distribution"],
        "total_vector_count": validated_plan["total_vector_count"],
        "cell_counts": cell_counts,
        "dimension": SYNTHETIC_SCALE_DIMENSION,
        "k": SYNTHETIC_SCALE_K,
    }
    scenario_contract_digest = _sha256_json(scenario_contract)
    synthetic_snapshot_id = "synthetic_" + scenario_contract_digest.removeprefix(
        "sha256:"
    )
    vector_hasher = hashlib.sha256()
    loaded_cells: list[dict[str, Any]] = []
    for cell in cell_counts:
        cell_id = cell["cell_id"]
        count = cell["vector_count"]
        matrix = _synthetic_normalized_matrix(
            count,
            SYNTHETIC_SCALE_DIMENSION,
            seed_material={**scenario_contract, "cell_id": cell_id},
            label=f"synthetic_scale_{cell_id}",
        )
        vector_hasher.update(cell_id.encode("utf-8") + b"\0")
        vector_hasher.update(count.to_bytes(8, "big", signed=False))
        vector_hasher.update(matrix.tobytes(order="C"))
        try:
            index = faiss_module.IndexFlatIP(SYNTHETIC_SCALE_DIMENSION)
            index.add(matrix)
        except Exception as exc:
            raise CandidateLatencyUnavailable("synthetic_faiss_build_failed") from exc
        if int(getattr(index, "ntotal", -1)) != count:
            raise CandidateLatencyContractError("synthetic_faiss_count_mismatch")
        rows = [
            {
                "canonical_solver_id": f"synthetic_{cell_id}_{row_index:05d}",
                "projection_id": _sha256_json(
                    {
                        "scenario": scenario_contract_digest,
                        "cell_id": cell_id,
                        "row_index": row_index,
                    }
                ),
                "receipt_bound": False,
            }
            for row_index in range(count)
        ]
        loaded_cells.append(
            {
                "manifest": {"cell_id": cell_id},
                "index": index,
                "rows": rows,
            }
        )

    synthetic_manifest = {
        "snapshot_id": synthetic_snapshot_id,
        "embedding_contract": {"dimension": SYNTHETIC_SCALE_DIMENSION},
    }

    def search_once() -> list[dict[str, Any]]:
        try:
            results = candidate_snapshot._search_loaded_candidate(
                synthetic_manifest,
                loaded_cells,
                query_vector,
                k=SYNTHETIC_SCALE_K,
                source_reverification_scope="none",
                verification_session_id=None,
            )
        except candidate_snapshot.CandidateContractError as exc:
            raise CandidateLatencyContractError(str(exc)) from exc
        except retrieval_benchmark.EmbeddingValidationError as exc:
            raise CandidateLatencyContractError(str(exc)) from exc
        _validate_synthetic_search_results(
            results,
            k=SYNTHETIC_SCALE_K,
            expected_snapshot_id=synthetic_snapshot_id,
        )
        return results

    for _ in range(SYNTHETIC_SCALE_WARMUP_ROUNDS):
        search_once()
    samples_ms: list[float] = []
    ranking_identity: tuple[tuple[str, str, str, float], ...] | None = None
    ranking_mismatch_count = 0
    for _ in range(SYNTHETIC_SCALE_REPETITIONS):
        started = clock_ns()
        results = search_once()
        elapsed = clock_ns() - started
        if type(elapsed) is not int or elapsed < 0:
            raise CandidateLatencyContractError("synthetic_scale_clock_invalid")
        current_ranking = _ranking_identity(results)
        if ranking_identity is None:
            ranking_identity = current_ranking
        elif current_ranking != ranking_identity:
            ranking_mismatch_count += 1
        samples_ms.append(elapsed / 1_000_000.0)
    if ranking_mismatch_count:
        raise CandidateLatencyContractError(
            "synthetic_scale_ranking_changed_during_measurement"
        )
    latency = _latency_summary(samples_ms)
    raw_bytes = validated_plan["raw_float32_vector_payload_bytes"]
    return {
        **validated_plan,
        "scenario_contract_digest": scenario_contract_digest,
        "synthetic_vector_input_sha256": "sha256:" + vector_hasher.hexdigest(),
        "query_vector_sha256": "sha256:"
        + hashlib.sha256(
            np.ascontiguousarray(query_vector, dtype=np.float32).tobytes(order="C")
        ).hexdigest(),
        "raw_float32_vector_payload_mib": round(raw_bytes / (1024**2), 6),
        "raw_float32_vector_payload_is_memory_lower_bound_only": True,
        "peak_process_memory_evaluated": False,
        "faiss_index_overhead_memory_evaluated": False,
        "warmup_search_count": SYNTHETIC_SCALE_WARMUP_ROUNDS,
        "search_execution_count": SYNTHETIC_SCALE_REPETITIONS,
        "k": SYNTHETIC_SCALE_K,
        "query_count": 1,
        "synthetic_input_generation_excluded_from_latency": True,
        "index_build_excluded_from_latency": True,
        "candidate_search_call_includes_query_normalization_and_python_merge": True,
        "search_result_evidence_validation_included_in_latency": True,
        "latency_ms": {"candidate_all_cell_search": latency},
        "latency_reference": {
            "max_p95_ms": DEFAULT_MAX_SEARCH_P95_MS,
            "observed_p95_ms": latency["p95"],
            "target_met": latency["p95"] < DEFAULT_MAX_SEARCH_P95_MS,
            "hard_gate": False,
            "authority_effect": "none",
        },
        "candidate_search_algorithm_reused": True,
        "candidate_search_algorithm": (
            "materialize_magma_faiss_candidate._search_loaded_candidate"
        ),
        "production_runtime_path_evaluated": False,
        "cutoff_tie_worst_case_evaluated": False,
        "solver_quality_evaluated": False,
        "routing_recall_evaluated": False,
        "cell_pruning_evaluated": False,
        "cell_pruning_authorized": False,
        "runtime_authority_granted": False,
        "production_promotion_gate_pass": False,
    }


def _run_synthetic_scale_forecast(
    manifest: Any,
    *,
    faiss_module: Any,
    clock_ns: Callable[[], int],
) -> dict[str, Any]:
    anchor = _validate_scale_anchor(manifest)
    try:
        observed_faiss_identity = candidate_snapshot._faiss_identity(faiss_module)
    except candidate_snapshot.CandidateUnavailable as exc:
        raise CandidateLatencyUnavailable(str(exc)) from exc
    expected_faiss_identity = dict(SYNTHETIC_SCALE_ANCHOR_FAISS_IDENTITY)
    if (
        type(observed_faiss_identity) is not dict
        or set(observed_faiss_identity) != set(expected_faiss_identity)
        or any(
            type(observed_faiss_identity.get(key)) is not str
            or observed_faiss_identity[key] != expected
            for key, expected in expected_faiss_identity.items()
        )
    ):
        raise CandidateLatencyUnavailable("synthetic_scale_faiss_build_mismatch")

    get_threads = getattr(faiss_module, "omp_get_max_threads", None)
    set_threads = getattr(faiss_module, "omp_set_num_threads", None)
    if not callable(get_threads) or not callable(set_threads):
        raise CandidateLatencyUnavailable("synthetic_scale_omp_api_unavailable")
    try:
        previous_threads = get_threads()
    except Exception as exc:
        raise CandidateLatencyUnavailable("synthetic_scale_omp_read_failed") from exc
    if type(previous_threads) is not int or previous_threads <= 0:
        raise CandidateLatencyUnavailable("synthetic_scale_omp_value_invalid")

    primary_error: BaseException | None = None
    try:
        try:
            set_threads(SYNTHETIC_SCALE_OMP_THREADS)
            configured_threads = get_threads()
        except Exception as exc:
            raise CandidateLatencyUnavailable(
                "synthetic_scale_omp_configuration_failed"
            ) from exc
        if (
            type(configured_threads) is not int
            or configured_threads != SYNTHETIC_SCALE_OMP_THREADS
        ):
            raise CandidateLatencyUnavailable(
                "synthetic_scale_omp_configuration_mismatch"
            )
        query_vector = _synthetic_normalized_matrix(
            1,
            SYNTHETIC_SCALE_DIMENSION,
            seed_material={
                "base_seed": SYNTHETIC_SCALE_SEED,
                "anchor_snapshot_id": anchor["snapshot_id"],
                "scope": "shared_forecast_query",
            },
            label="synthetic_scale_query",
        )[0]
        plans = [
            _derive_forecast_cell_counts(
                manifest,
                multiplier=multiplier,
                distribution=distribution,
            )
            for multiplier in SYNTHETIC_SCALE_MULTIPLIERS
            for distribution in SYNTHETIC_SCALE_DISTRIBUTIONS
        ]
        rows = [
            _measure_synthetic_scale_row(
                plan,
                query_vector,
                faiss_module=faiss_module,
                clock_ns=clock_ns,
            )
            for plan in plans
        ]
        return {
            "schema_version": SYNTHETIC_FORECAST_SCHEMA,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "measurement_complete": True,
            "evidence_scope": "synthetic_candidate_search_algorithm_capacity_only",
            "anchor": anchor,
            "faiss_identity": observed_faiss_identity,
            "faiss_index_type": "IndexFlatIP",
            "similarity_contract": "l2_normalized_inner_product",
            "numpy_version": np.__version__,
            "runtime_identity": {
                "python_implementation": platform.python_implementation(),
                "python_version": platform.python_version(),
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
                "processor": platform.processor(),
            },
            "base_seed": SYNTHETIC_SCALE_SEED,
            "omp_threads": SYNTHETIC_SCALE_OMP_THREADS,
            "omp_scope": "process_global_restored_after_measurement",
            "scenario_count": len(rows),
            "scenarios": rows,
            "shared_query_vector_across_scenarios": True,
            "maximum_tested_leaf_vector_count": max(
                row["max_leaf_vector_count"] for row in rows
            ),
            "exclusive_leaf_limit": SYNTHETIC_SCALE_EXCLUSIVE_LEAF_LIMIT,
            "exclusive_leaf_limit_semantics": (
                "conservative_index_tier_review_boundary_not_authority"
            ),
            "candidate_search_algorithm_reused": True,
            "production_runtime_path_evaluated": False,
            "real_snapshot_multi_scale_curve_evaluated": False,
            "cross_run_stability_evaluated": False,
            "cutoff_tie_worst_case_evaluated": False,
            "solver_quality_evaluated": False,
            "routing_recall_evaluated": False,
            "cell_pruning_evaluated": False,
            "cell_pruning_authorized": False,
            "runtime_authority_granted": False,
            "production_promotion_gate_pass": False,
        }
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            set_threads(previous_threads)
            restored_threads = get_threads()
            if type(restored_threads) is not int or restored_threads != previous_threads:
                raise RuntimeError("OMP thread count did not restore")
        except Exception as cleanup_exc:
            if primary_error is not None:
                primary_error.add_note(
                    "synthetic forecast OMP restoration failed: "
                    f"{type(cleanup_exc).__name__}: {cleanup_exc}"
                )
            else:
                raise CandidateLatencyUnavailable(
                    "synthetic_scale_omp_restoration_failed"
                ) from cleanup_exc


def run_synthetic_scale_forecast(manifest: Any) -> dict[str, Any]:
    """Measure the frozen synthetic candidate-kernel curve without authority."""
    _validate_scale_anchor(manifest)
    try:
        faiss_module = candidate_snapshot._import_faiss()
    except candidate_snapshot.CandidateUnavailable as exc:
        raise CandidateLatencyUnavailable(str(exc)) from exc
    return _run_synthetic_scale_forecast(
        manifest,
        faiss_module=faiss_module,
        clock_ns=time.perf_counter_ns,
    )


def _unavailable_synthetic_scale_forecast(exc: Exception) -> dict[str, Any]:
    return {
        "schema_version": SYNTHETIC_FORECAST_SCHEMA,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "measurement_complete": False,
        "evidence_scope": "synthetic_candidate_search_algorithm_capacity_only",
        "error_type": type(exc).__name__,
        "error": str(exc) or type(exc).__name__,
        "candidate_search_algorithm_reused": False,
        "production_runtime_path_evaluated": False,
        "real_snapshot_multi_scale_curve_evaluated": False,
        "cross_run_stability_evaluated": False,
        "cutoff_tie_worst_case_evaluated": False,
        "solver_quality_evaluated": False,
        "routing_recall_evaluated": False,
        "cell_pruning_evaluated": False,
        "cell_pruning_authorized": False,
        "runtime_authority_granted": False,
        "production_promotion_gate_pass": False,
    }


_FORECAST_FALSE_CAPABILITY_KEYS = frozenset(
    {
        "production_runtime_path_evaluated",
        "real_snapshot_multi_scale_curve_evaluated",
        "cross_run_stability_evaluated",
        "cutoff_tie_worst_case_evaluated",
        "solver_quality_evaluated",
        "routing_recall_evaluated",
        "cell_pruning_evaluated",
        "cell_pruning_authorized",
        "runtime_authority_granted",
        "production_promotion_gate_pass",
    }
)
_FORECAST_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "generated_at_utc",
        "measurement_complete",
        "evidence_scope",
        "anchor",
        "faiss_identity",
        "faiss_index_type",
        "similarity_contract",
        "numpy_version",
        "runtime_identity",
        "base_seed",
        "omp_threads",
        "omp_scope",
        "scenario_count",
        "scenarios",
        "shared_query_vector_across_scenarios",
        "maximum_tested_leaf_vector_count",
        "exclusive_leaf_limit",
        "exclusive_leaf_limit_semantics",
        "candidate_search_algorithm_reused",
        *_FORECAST_FALSE_CAPABILITY_KEYS,
    }
)
_FORECAST_UNAVAILABLE_KEYS = frozenset(
    {
        "schema_version",
        "generated_at_utc",
        "measurement_complete",
        "evidence_scope",
        "error_type",
        "error",
        "candidate_search_algorithm_reused",
        *_FORECAST_FALSE_CAPABILITY_KEYS,
    }
)
_FORECAST_ANCHOR_KEYS = frozenset(
    {
        "snapshot_id",
        "topology_digest",
        "total_vector_count",
        "dimension",
        "cells",
        "faiss_version",
        "faiss_compile_options",
        "faiss_binary_set_sha256",
    }
)
_FORECAST_ANCHOR_CELL_KEYS = frozenset(
    {"cell_id", "vector_count", "source_projection_commit_id"}
)
_FORECAST_FAISS_IDENTITY_KEYS = frozenset(
    {"faiss_version", "faiss_compile_options", "faiss_binary_set_sha256"}
)
_FORECAST_RUNTIME_IDENTITY_KEYS = frozenset(
    {
        "python_implementation",
        "python_version",
        "system",
        "release",
        "machine",
        "processor",
    }
)
_FORECAST_SCENARIO_KEYS = frozenset(
    {
        "scale_multiplier",
        "distribution",
        "total_vector_count",
        "cell_counts",
        "max_leaf_vector_count",
        "raw_float32_vector_payload_bytes",
        "scenario_contract_digest",
        "synthetic_vector_input_sha256",
        "query_vector_sha256",
        "raw_float32_vector_payload_mib",
        "raw_float32_vector_payload_is_memory_lower_bound_only",
        "peak_process_memory_evaluated",
        "faiss_index_overhead_memory_evaluated",
        "warmup_search_count",
        "search_execution_count",
        "k",
        "query_count",
        "synthetic_input_generation_excluded_from_latency",
        "index_build_excluded_from_latency",
        "candidate_search_call_includes_query_normalization_and_python_merge",
        "search_result_evidence_validation_included_in_latency",
        "latency_ms",
        "latency_reference",
        "candidate_search_algorithm_reused",
        "candidate_search_algorithm",
        "production_runtime_path_evaluated",
        "cutoff_tie_worst_case_evaluated",
        "solver_quality_evaluated",
        "routing_recall_evaluated",
        "cell_pruning_evaluated",
        "cell_pruning_authorized",
        "runtime_authority_granted",
        "production_promotion_gate_pass",
    }
)
_FORECAST_SCENARIO_FALSE_KEYS = frozenset(
    {
        "peak_process_memory_evaluated",
        "faiss_index_overhead_memory_evaluated",
        "production_runtime_path_evaluated",
        "cutoff_tie_worst_case_evaluated",
        "solver_quality_evaluated",
        "routing_recall_evaluated",
        "cell_pruning_evaluated",
        "cell_pruning_authorized",
        "runtime_authority_granted",
        "production_promotion_gate_pass",
    }
)
_FORECAST_SCENARIO_TRUE_KEYS = frozenset(
    {
        "raw_float32_vector_payload_is_memory_lower_bound_only",
        "synthetic_input_generation_excluded_from_latency",
        "index_build_excluded_from_latency",
        "candidate_search_call_includes_query_normalization_and_python_merge",
        "search_result_evidence_validation_included_in_latency",
        "candidate_search_algorithm_reused",
    }
)
_FORECAST_LATENCY_KEYS = frozenset({"candidate_all_cell_search"})
_FORECAST_LATENCY_SUMMARY_KEYS = frozenset({"p50", "p95", "p99", "max", "mean"})
_FORECAST_LATENCY_REFERENCE_KEYS = frozenset(
    {"max_p95_ms", "observed_p95_ms", "target_met", "hard_gate", "authority_effect"}
)


def _forecast_boundary_error() -> CandidateLatencyContractError:
    return CandidateLatencyContractError(
        "synthetic_scale_forecast_authority_boundary_invalid"
    )


def _require_forecast_keys(value: Any, expected: frozenset[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != expected:
        raise _forecast_boundary_error()
    if any(type(key) is not str for key in value):
        raise _forecast_boundary_error()
    return value


def _require_forecast_string(value: Any, *, allow_empty: bool = False) -> str:
    if type(value) is not str or (not allow_empty and not value):
        raise _forecast_boundary_error()
    return value


def _require_forecast_utc_timestamp(value: Any) -> None:
    raw = _require_forecast_string(value)
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise _forecast_boundary_error() from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != timezone.utc.utcoffset(parsed)
        or raw != parsed.isoformat()
    ):
        raise _forecast_boundary_error()


def _require_forecast_digest(value: Any) -> str:
    raw = _require_forecast_string(value)
    if not _FULL_DIGEST.fullmatch(raw):
        raise _forecast_boundary_error()
    return raw


def _require_forecast_float(value: Any) -> float:
    if type(value) is not float or not math.isfinite(value) or value < 0.0:
        raise _forecast_boundary_error()
    return value


def _validate_forecast_false_capabilities(value: Mapping[str, Any]) -> None:
    if any(value[key] is not False for key in _FORECAST_FALSE_CAPABILITY_KEYS):
        raise _forecast_boundary_error()


def _validate_forecast_anchor(value: Any) -> dict[str, Any]:
    anchor = _require_forecast_keys(value, _FORECAST_ANCHOR_KEYS)
    if (
        type(anchor["snapshot_id"]) is not str
        or anchor["snapshot_id"] != SYNTHETIC_SCALE_ANCHOR_SNAPSHOT_ID
        or type(anchor["topology_digest"]) is not str
        or anchor["topology_digest"] != SYNTHETIC_SCALE_ANCHOR_TOPOLOGY_DIGEST
        or type(anchor["total_vector_count"]) is not int
        or anchor["total_vector_count"] != SYNTHETIC_SCALE_ANCHOR_TOTAL
        or type(anchor["dimension"]) is not int
        or anchor["dimension"] != SYNTHETIC_SCALE_DIMENSION
    ):
        raise _forecast_boundary_error()
    expected_counts = dict(SYNTHETIC_SCALE_ANCHOR_COUNTS)
    expected_commits = dict(SYNTHETIC_SCALE_ANCHOR_SOURCE_COMMITS)
    cells = anchor["cells"]
    if type(cells) is not list or len(cells) != len(SYNTHETIC_SCALE_CELL_ORDER):
        raise _forecast_boundary_error()
    for index, cell_value in enumerate(cells):
        cell = _require_forecast_keys(cell_value, _FORECAST_ANCHOR_CELL_KEYS)
        cell_id = SYNTHETIC_SCALE_CELL_ORDER[index]
        if (
            type(cell["cell_id"]) is not str
            or cell["cell_id"] != cell_id
            or type(cell["vector_count"]) is not int
            or cell["vector_count"] != expected_counts[cell_id]
            or type(cell["source_projection_commit_id"]) is not str
            or cell["source_projection_commit_id"] != expected_commits[cell_id]
        ):
            raise _forecast_boundary_error()
    for key, expected in SYNTHETIC_SCALE_ANCHOR_FAISS_IDENTITY:
        if type(anchor[key]) is not str or anchor[key] != expected:
            raise _forecast_boundary_error()
    return anchor


def _validate_forecast_faiss_identity(value: Any) -> dict[str, Any]:
    identity = _require_forecast_keys(value, _FORECAST_FAISS_IDENTITY_KEYS)
    for key, expected in SYNTHETIC_SCALE_ANCHOR_FAISS_IDENTITY:
        if type(identity[key]) is not str or identity[key] != expected:
            raise _forecast_boundary_error()
    return identity


def _validate_forecast_runtime_identity(value: Any) -> dict[str, Any]:
    identity = _require_forecast_keys(value, _FORECAST_RUNTIME_IDENTITY_KEYS)
    expected = {
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "processor": platform.processor(),
    }
    if any(
        type(identity[key]) is not str or identity[key] != expected[key]
        for key in _FORECAST_RUNTIME_IDENTITY_KEYS
    ):
        raise _forecast_boundary_error()
    return identity


def _validate_forecast_latency_summary(value: Any) -> dict[str, Any]:
    summary = _require_forecast_keys(value, _FORECAST_LATENCY_SUMMARY_KEYS)
    for key in _FORECAST_LATENCY_SUMMARY_KEYS:
        _require_forecast_float(summary[key])
    if not (
        summary["p50"] <= summary["p95"] <= summary["p99"] <= summary["max"]
        and summary["mean"] <= summary["max"]
    ):
        raise _forecast_boundary_error()
    return summary


def _validate_forecast_scenario(
    value: Any,
    *,
    expected_multiplier: int,
    expected_distribution: str,
) -> dict[str, Any]:
    scenario = _require_forecast_keys(value, _FORECAST_SCENARIO_KEYS)
    plan = _validate_forecast_plan(
        {
            key: scenario[key]
            for key in (
                "scale_multiplier",
                "distribution",
                "total_vector_count",
                "cell_counts",
                "max_leaf_vector_count",
                "raw_float32_vector_payload_bytes",
            )
        }
    )
    if (
        plan["scale_multiplier"] != expected_multiplier
        or plan["distribution"] != expected_distribution
    ):
        raise _forecast_boundary_error()
    scenario_contract = {
        "base_seed": SYNTHETIC_SCALE_SEED,
        "anchor_snapshot_id": SYNTHETIC_SCALE_ANCHOR_SNAPSHOT_ID,
        "scale_multiplier": plan["scale_multiplier"],
        "distribution": plan["distribution"],
        "total_vector_count": plan["total_vector_count"],
        "cell_counts": plan["cell_counts"],
        "dimension": SYNTHETIC_SCALE_DIMENSION,
        "k": SYNTHETIC_SCALE_K,
    }
    if (
        _require_forecast_digest(scenario["scenario_contract_digest"])
        != _sha256_json(scenario_contract)
    ):
        raise _forecast_boundary_error()
    _require_forecast_digest(scenario["synthetic_vector_input_sha256"])
    _require_forecast_digest(scenario["query_vector_sha256"])
    expected_mib = round(
        plan["raw_float32_vector_payload_bytes"] / (1024**2), 6
    )
    if (
        _require_forecast_float(scenario["raw_float32_vector_payload_mib"])
        != expected_mib
        or any(scenario[key] is not False for key in _FORECAST_SCENARIO_FALSE_KEYS)
        or any(scenario[key] is not True for key in _FORECAST_SCENARIO_TRUE_KEYS)
        or type(scenario["warmup_search_count"]) is not int
        or scenario["warmup_search_count"] != SYNTHETIC_SCALE_WARMUP_ROUNDS
        or type(scenario["search_execution_count"]) is not int
        or scenario["search_execution_count"] != SYNTHETIC_SCALE_REPETITIONS
        or type(scenario["k"]) is not int
        or scenario["k"] != SYNTHETIC_SCALE_K
        or type(scenario["query_count"]) is not int
        or scenario["query_count"] != 1
        or type(scenario["candidate_search_algorithm"]) is not str
        or scenario["candidate_search_algorithm"]
        != "materialize_magma_faiss_candidate._search_loaded_candidate"
    ):
        raise _forecast_boundary_error()
    latency_container = _require_forecast_keys(
        scenario["latency_ms"], _FORECAST_LATENCY_KEYS
    )
    summary = _validate_forecast_latency_summary(
        latency_container["candidate_all_cell_search"]
    )
    reference = _require_forecast_keys(
        scenario["latency_reference"], _FORECAST_LATENCY_REFERENCE_KEYS
    )
    if (
        type(reference["max_p95_ms"]) is not float
        or reference["max_p95_ms"] != DEFAULT_MAX_SEARCH_P95_MS
        or type(reference["observed_p95_ms"]) is not float
        or reference["observed_p95_ms"] != summary["p95"]
        or type(reference["target_met"]) is not bool
        or reference["target_met"]
        is not (summary["p95"] < DEFAULT_MAX_SEARCH_P95_MS)
        or reference["hard_gate"] is not False
        or type(reference["authority_effect"]) is not str
        or reference["authority_effect"] != "none"
    ):
        raise _forecast_boundary_error()
    return scenario


def _validate_unavailable_forecast(value: Any) -> dict[str, Any]:
    forecast = _require_forecast_keys(value, _FORECAST_UNAVAILABLE_KEYS)
    _require_forecast_utc_timestamp(forecast["generated_at_utc"])
    if (
        type(forecast["schema_version"]) is not str
        or forecast["schema_version"] != SYNTHETIC_FORECAST_SCHEMA
        or forecast["measurement_complete"] is not False
        or type(forecast["evidence_scope"]) is not str
        or forecast["evidence_scope"]
        != "synthetic_candidate_search_algorithm_capacity_only"
        or type(forecast["error_type"]) is not str
        or not forecast["error_type"]
        or type(forecast["error"]) is not str
        or not forecast["error"]
        or forecast["candidate_search_algorithm_reused"] is not False
    ):
        raise _forecast_boundary_error()
    _validate_forecast_false_capabilities(forecast)
    return forecast


def _validate_forecast_authority_boundary(forecast: Any) -> dict[str, Any]:
    if type(forecast) is not dict:
        raise _forecast_boundary_error()
    if forecast.get("measurement_complete") is False:
        validated = _validate_unavailable_forecast(forecast)
    else:
        validated = _require_forecast_keys(forecast, _FORECAST_TOP_LEVEL_KEYS)
        _require_forecast_utc_timestamp(validated["generated_at_utc"])
        if (
            type(validated["schema_version"]) is not str
            or validated["schema_version"] != SYNTHETIC_FORECAST_SCHEMA
            or validated["measurement_complete"] is not True
            or type(validated["evidence_scope"]) is not str
            or validated["evidence_scope"]
            != "synthetic_candidate_search_algorithm_capacity_only"
            or type(validated["faiss_index_type"]) is not str
            or validated["faiss_index_type"] != "IndexFlatIP"
            or type(validated["similarity_contract"]) is not str
            or validated["similarity_contract"] != "l2_normalized_inner_product"
            or type(validated["numpy_version"]) is not str
            or validated["numpy_version"] != np.__version__
            or type(validated["base_seed"]) is not int
            or validated["base_seed"] != SYNTHETIC_SCALE_SEED
            or type(validated["omp_threads"]) is not int
            or validated["omp_threads"] != SYNTHETIC_SCALE_OMP_THREADS
            or type(validated["omp_scope"]) is not str
            or validated["omp_scope"]
            != "process_global_restored_after_measurement"
            or type(validated["scenario_count"]) is not int
            or validated["scenario_count"]
            != len(SYNTHETIC_SCALE_MULTIPLIERS)
            * len(SYNTHETIC_SCALE_DISTRIBUTIONS)
            or validated["shared_query_vector_across_scenarios"] is not True
            or type(validated["exclusive_leaf_limit"]) is not int
            or validated["exclusive_leaf_limit"]
            != SYNTHETIC_SCALE_EXCLUSIVE_LEAF_LIMIT
            or type(validated["exclusive_leaf_limit_semantics"]) is not str
            or validated["exclusive_leaf_limit_semantics"]
            != "conservative_index_tier_review_boundary_not_authority"
            or validated["candidate_search_algorithm_reused"] is not True
        ):
            raise _forecast_boundary_error()
        _validate_forecast_anchor(validated["anchor"])
        _validate_forecast_faiss_identity(validated["faiss_identity"])
        _validate_forecast_runtime_identity(validated["runtime_identity"])
        _validate_forecast_false_capabilities(validated)
        scenarios = validated["scenarios"]
        expected_scenarios = tuple(
            (multiplier, distribution)
            for multiplier in SYNTHETIC_SCALE_MULTIPLIERS
            for distribution in SYNTHETIC_SCALE_DISTRIBUTIONS
        )
        if type(scenarios) is not list or len(scenarios) != len(expected_scenarios):
            raise _forecast_boundary_error()
        validated_scenarios = [
            _validate_forecast_scenario(
                scenario,
                expected_multiplier=expected_multiplier,
                expected_distribution=expected_distribution,
            )
            for scenario, (expected_multiplier, expected_distribution) in zip(
                scenarios, expected_scenarios, strict=True
            )
        ]
        if (
            type(validated["maximum_tested_leaf_vector_count"]) is not int
            or validated["maximum_tested_leaf_vector_count"]
            != max(row["max_leaf_vector_count"] for row in validated_scenarios)
            or len(
                {row["scenario_contract_digest"] for row in validated_scenarios}
            )
            != len(validated_scenarios)
            or len(
                {row["synthetic_vector_input_sha256"] for row in validated_scenarios}
            )
            != len(validated_scenarios)
            or len({row["query_vector_sha256"] for row in validated_scenarios}) != 1
        ):
            raise _forecast_boundary_error()
    try:
        json.dumps(validated, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise _forecast_boundary_error() from exc
    return validated


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
    try:
        synthetic_scale_forecast = run_synthetic_scale_forecast(manifest)
    except (CandidateLatencyContractError, CandidateLatencyUnavailable) as exc:
        synthetic_scale_forecast = _unavailable_synthetic_scale_forecast(exc)
    synthetic_scale_forecast = _validate_forecast_authority_boundary(
        synthetic_scale_forecast
    )
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
        "synthetic_scale_forecast_affects_candidate_benchmark_pass": False,
        "synthetic_scale_forecast_affects_cell_pruning_scale_trigger": False,
        "synthetic_scale_forecast_affects_latency_gate_decision": False,
        "synthetic_scale_forecast": synthetic_scale_forecast,
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
            "synthetic_candidate_algorithm_curve_attached": (
                synthetic_scale_forecast["measurement_complete"] is True
            ),
            "production_runtime_scale_curve_evaluated": False,
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
            "verified_candidate_multi_scale_curve_not_evaluated",
            "production_runtime_scale_curve_not_evaluated",
            "synthetic_cutoff_tie_worst_case_not_evaluated",
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
                    "synthetic_scale_forecast_affects_candidate_benchmark_pass": (
                        False
                    ),
                    "synthetic_scale_forecast_affects_cell_pruning_scale_trigger": (
                        False
                    ),
                    "synthetic_scale_forecast_affects_latency_gate_decision": False,
                    "synthetic_scale_forecast": (
                        _unavailable_synthetic_scale_forecast(exc)
                    ),
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
