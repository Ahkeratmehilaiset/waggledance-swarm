#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Differential benchmark for non-empty MAGMA solver retrieval.

This is an evidence tool, not a runtime router.  It compares the same frozen
queries against:

* A0: deterministic TF-IDF over the strict solver projection;
* A1: an existing-index preflight (never rebuilt or silently substituted);
* A2: an in-memory, normalized exact inner-product search.

A2 intentionally uses NumPy rather than importing FAISS.  Its score operation
is equivalent to ``IndexFlatIP`` for validated L2-normalized vectors, but the
report names it an exact NumPy proxy and never claims a persisted FAISS index
exists.  Labels are withheld until after rankings have been produced.

Reports may only be written below ``.codex-audit``.  This tool never mutates a
runtime index, MAGMA event log, topology, solver contract, or routing policy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import stat
import sys
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import httpx
import numpy as np
import yaml


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.magma import vector_projection  # noqa: E402
from waggledance.core.hex_cell_topology import ALL_CELLS, HexCellTopology  # noqa: E402
from waggledance.core.reasoning.solver_router import SolverRouter  # noqa: E402


CORPUS_SCHEMA = "wd.magma.semantic_eval.v1"
CORPUS_SOURCE_CONTRACT = vector_projection.SOLVER_PROJECTION_VERSION
EXPECTED_CORPUS_CANONICAL_SHA256 = (
    "c3316271929f9cfa02394bd7c0bb710b2d887ee8aec18f1bf5eb97d6f9c75dd6"
)
DEFAULT_CORPUS = ROOT / "configs" / "benchmarks" / "magma_solver_retrieval_v1.json"
DEFAULT_AXIOMS = ROOT / "configs" / "axioms"
DEFAULT_VECTOR_ROOT = ROOT / "data" / "vector"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
_WINDOWS_REPARSE_POINT = getattr(
    stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400
)

_CASE_KEYS = frozenset(
    {"query_id", "stratum", "query", "expected_solver", "expected_cell"}
)
_CORPUS_KEYS = frozenset(
    {
        "schema_version",
        "source_contract",
        "target_text_field",
        "normalization",
        "policies",
        "cases",
    }
)
_NORMALIZATION_KEYS = frozenset(
    {
        "unicode",
        "token_regex",
        "drop_single_character_tokens",
        "drop_numeric_tokens",
        "stemming",
        "stopwords",
        "projection_boilerplate",
        "unit_tokens",
    }
)
_POLICY_KEYS = frozenset(
    {
        "query_writer_must_not_see",
        "retriever_must_not_receive",
        "forbidden_query_label_match",
        "semantic_zero_overlap_gate",
    }
)
_STRATA = frozenset({"anchored_natural", "semantic_zero_overlap"})
_PROVIDER_IDENTITY_CORE_KEYS = frozenset(
    {"provider", "requested_model_tag", "catalog_digest"}
)


@dataclass(frozen=True)
class EmbeddingProfile:
    name: str
    model_id: str
    model_digest: str
    dimension: int
    document_prefix: str
    query_prefix: str


def provider_identity_matches_profile(
    value: Any,
    profile: EmbeddingProfile,
) -> bool:
    """Return whether one live catalog result exactly satisfies the contract."""
    return (
        type(value) is dict
        and frozenset(value) == _PROVIDER_IDENTITY_CORE_KEYS
        and isinstance(value["provider"], str)
        and bool(value["provider"])
        and value["requested_model_tag"] == profile.model_id
        and value["catalog_digest"] == profile.model_digest
    )


EMBEDDING_PROFILES: dict[str, EmbeddingProfile] = {
    "nomic": EmbeddingProfile(
        name="nomic",
        model_id="nomic-embed-text:latest",
        model_digest=(
            "0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f"
        ),
        dimension=768,
        document_prefix="search_document: ",
        query_prefix="search_query: ",
    ),
    "all-minilm": EmbeddingProfile(
        name="all-minilm",
        model_id="all-minilm:latest",
        model_digest=(
            "1b226e2802dbb772b5fc32a58f103ca1804ef7501331012de126ab22f67475ef"
        ),
        dimension=384,
        document_prefix="",
        query_prefix="",
    ),
}


class BenchmarkContractError(ValueError):
    """The benchmark input violated a frozen or safety-critical contract."""


class EmbeddingValidationError(BenchmarkContractError):
    """The embedding provider returned data unsafe for exact search."""


class BenchmarkUnavailable(RuntimeError):
    """A requested measurement could not be run without a fallback."""


def _exact_keys(value: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    actual = frozenset(value)
    if actual != expected:
        raise BenchmarkContractError(
            f"{label}_keys_mismatch:missing={sorted(expected - actual)},"
            f"extra={sorted(actual - expected)}"
        )


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise BenchmarkContractError("corpus_is_not_canonical_json") from exc
    return rendered.encode("utf-8")


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _plain_tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", text)
    normalized = "".join(
        character for character in normalized if not unicodedata.combining(character)
    ).casefold()
    return re.findall(r"[a-z0-9]+", normalized)


class ContentTokenizer:
    """The exact, corpus-owned token normalizer used only for diagnostics."""

    def __init__(self, contract: Mapping[str, Any]) -> None:
        if type(contract) is not dict:
            raise BenchmarkContractError("normalization_must_be_object")
        _exact_keys(contract, _NORMALIZATION_KEYS, "normalization")
        if contract["unicode"] != "NFKD; remove combining marks; casefold":
            raise BenchmarkContractError("unsupported_unicode_normalization")
        if contract["token_regex"] != "[a-z0-9]+":
            raise BenchmarkContractError("unsupported_token_regex")
        if contract["drop_single_character_tokens"] is not True:
            raise BenchmarkContractError("single_character_tokens_must_be_dropped")
        if contract["drop_numeric_tokens"] is not True:
            raise BenchmarkContractError("numeric_tokens_must_be_dropped")
        if contract["stemming"] is not False:
            raise BenchmarkContractError("stemming_must_be_disabled")
        drop: set[str] = set()
        for key in ("stopwords", "projection_boilerplate", "unit_tokens"):
            values = contract[key]
            if type(values) is not list or any(type(item) is not str for item in values):
                raise BenchmarkContractError(f"{key}_must_be_string_list")
            if len(values) != len(set(values)):
                raise BenchmarkContractError(f"{key}_contains_duplicates")
            drop.update(values)
        self._drop = frozenset(drop)

    def __call__(self, text: str) -> list[str]:
        if type(text) is not str:
            raise BenchmarkContractError("tokenized_value_must_be_string")
        return [
            token
            for token in _plain_tokens(text)
            if len(token) > 1 and not token.isnumeric() and token not in self._drop
        ]


def load_projection_documents(
    axiom_dir: Path = DEFAULT_AXIOMS,
) -> tuple[list[dict[str, Any]], str]:
    """Build the live allowlisted documents without writing an index."""
    if not axiom_dir.is_dir():
        raise BenchmarkContractError("axiom_directory_missing")
    topology = vector_projection.build_retrieval_topology_contract()
    topology_digest = vector_projection.retrieval_topology_digest(topology)
    topology_cells = {row["cell_id"] for row in topology["cells"]}
    documents: list[dict[str, Any]] = []
    for path in sorted(axiom_dir.rglob("*.yaml")):
        if path.is_symlink() or not path.is_file():
            raise BenchmarkContractError("axiom_source_must_be_regular_file")
        raw = path.read_bytes()
        try:
            axiom = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise BenchmarkContractError("axiom_yaml_invalid") from exc
        if type(axiom) is not dict:
            raise BenchmarkContractError("axiom_must_be_object")
        document = vector_projection.build_solver_contract_projection(
            axiom,
            source_digest="sha256:" + hashlib.sha256(raw).hexdigest(),
            topology_digest=topology_digest,
        )
        if document["cell_id"] not in topology_cells:
            raise BenchmarkContractError("projection_cell_not_in_live_topology")
        documents.append(document)
    documents.sort(key=lambda row: row["canonical_solver_id"])
    solver_ids = [row["canonical_solver_id"] for row in documents]
    if not documents or len(solver_ids) != len(set(solver_ids)):
        raise BenchmarkContractError("projection_solver_ids_must_be_nonempty_unique")
    return documents, topology_digest


def _contains_sequence(haystack: Sequence[str], needle: Sequence[str]) -> bool:
    if not needle or len(needle) > len(haystack):
        return False
    width = len(needle)
    return any(
        list(haystack[index : index + width]) == list(needle)
        for index in range(len(haystack) - width + 1)
    )


def validate_corpus(
    value: Any,
    documents: Sequence[Mapping[str, Any]],
    *,
    expected_sha256: str = EXPECTED_CORPUS_CANONICAL_SHA256,
) -> tuple[dict[str, Any], ContentTokenizer, str]:
    """Validate freeze, coverage, leakage and semantic-overlap invariants."""
    if type(value) is not dict:
        raise BenchmarkContractError("corpus_must_be_object")
    _exact_keys(value, _CORPUS_KEYS, "corpus")
    digest = canonical_json_sha256(value)
    if digest != expected_sha256:
        raise BenchmarkContractError("corpus_hash_mismatch")
    if value["schema_version"] != CORPUS_SCHEMA:
        raise BenchmarkContractError("corpus_schema_mismatch")
    if value["source_contract"] != CORPUS_SOURCE_CONTRACT:
        raise BenchmarkContractError("corpus_source_contract_mismatch")
    if value["target_text_field"] != "embedding_text":
        raise BenchmarkContractError("corpus_target_field_mismatch")
    policies = value["policies"]
    if type(policies) is not dict:
        raise BenchmarkContractError("policies_must_be_object")
    _exact_keys(policies, _POLICY_KEYS, "policies")
    if set(policies["retriever_must_not_receive"]) != {
        "expected_solver",
        "expected_cell",
        "stratum",
    }:
        raise BenchmarkContractError("retriever_label_policy_mismatch")
    tokenizer = ContentTokenizer(value["normalization"])
    document_by_solver = {row["canonical_solver_id"]: row for row in documents}
    cases = value["cases"]
    if type(cases) is not list or not cases:
        raise BenchmarkContractError("cases_must_be_nonempty_list")
    seen_query_ids: set[str] = set()
    per_stratum: dict[str, list[str]] = {stratum: [] for stratum in _STRATA}
    validated_cases: list[dict[str, str]] = []
    for index, raw_case in enumerate(cases):
        if type(raw_case) is not dict:
            raise BenchmarkContractError(f"case_{index}_must_be_object")
        _exact_keys(raw_case, _CASE_KEYS, f"case_{index}")
        if any(type(raw_case[key]) is not str for key in _CASE_KEYS):
            raise BenchmarkContractError(f"case_{index}_fields_must_be_strings")
        case = {key: " ".join(raw_case[key].split()) for key in _CASE_KEYS}
        if any(not item for item in case.values()):
            raise BenchmarkContractError(f"case_{index}_contains_empty_field")
        query_id = case["query_id"]
        if query_id in seen_query_ids:
            raise BenchmarkContractError("duplicate_query_id")
        seen_query_ids.add(query_id)
        stratum = case["stratum"]
        if stratum not in _STRATA:
            raise BenchmarkContractError("unknown_query_stratum")
        expected = document_by_solver.get(case["expected_solver"])
        if expected is None:
            raise BenchmarkContractError("expected_solver_not_in_projection")
        if case["expected_cell"] != expected["cell_id"]:
            raise BenchmarkContractError("expected_cell_projection_mismatch")
        query_plain = _plain_tokens(case["query"])
        for candidate in documents:
            solver_plain = _plain_tokens(candidate["canonical_solver_id"])
            model_name_plain = _plain_tokens(
                candidate["contract_fields"]["model_name"]
            )
            if _contains_sequence(query_plain, solver_plain) or _contains_sequence(
                query_plain, model_name_plain
            ):
                raise BenchmarkContractError("query_label_leakage")
        overlap = set(tokenizer(case["query"])) & set(
            tokenizer(expected["embedding_text"])
        )
        if stratum == "semantic_zero_overlap" and overlap:
            raise BenchmarkContractError("semantic_zero_overlap_violation")
        if stratum == "anchored_natural" and len(overlap) < 2:
            raise BenchmarkContractError("anchored_query_has_insufficient_overlap")
        per_stratum[stratum].append(case["expected_solver"])
        validated_cases.append(case)
    solver_ids = set(document_by_solver)
    if len(validated_cases) != 2 * len(solver_ids):
        raise BenchmarkContractError("corpus_case_count_mismatch")
    for stratum, observed in per_stratum.items():
        if len(observed) != len(solver_ids) or set(observed) != solver_ids:
            raise BenchmarkContractError(f"{stratum}_solver_coverage_mismatch")
        if len(observed) != len(set(observed)):
            raise BenchmarkContractError(f"{stratum}_contains_duplicate_solver")
    canonical = {**value, "cases": validated_cases}
    return canonical, tokenizer, digest


def load_corpus(
    path: Path,
    documents: Sequence[Mapping[str, Any]],
    *,
    expected_sha256: str = EXPECTED_CORPUS_CANONICAL_SHA256,
) -> tuple[dict[str, Any], ContentTokenizer, str, str]:
    if path.is_symlink() or not path.is_file():
        raise BenchmarkContractError("corpus_must_be_regular_file")
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BenchmarkContractError("corpus_json_invalid") from exc
    corpus, tokenizer, canonical_digest = validate_corpus(
        value, documents, expected_sha256=expected_sha256
    )
    return corpus, tokenizer, canonical_digest, hashlib.sha256(raw).hexdigest()


def evaluate_label_blind_hex_router(
    cases: Sequence[Mapping[str, str]],
) -> dict[str, Any]:
    """Measure the existing intent -> origin + ring-1 policy without search.

    Only query text crosses the routing boundary. Expected cell and stratum are
    read after selection to score the frozen corpus. This axis deliberately
    does not claim that a cell-local FAISS search or its latency was measured.
    """
    if not cases:
        raise BenchmarkContractError("hex_router_cases_must_be_nonempty")
    topology = HexCellTopology()
    rows: list[dict[str, Any]] = []
    for index, raw_case in enumerate(cases):
        if type(raw_case) is not dict:
            raise BenchmarkContractError(f"hex_router_case_{index}_must_be_object")
        _exact_keys(raw_case, _CASE_KEYS, f"hex_router_case_{index}")
        if any(
            type(raw_case[key]) is not str or not raw_case[key]
            for key in _CASE_KEYS
        ):
            raise BenchmarkContractError(f"hex_router_case_{index}_fields_invalid")
        if raw_case["stratum"] not in _STRATA:
            raise BenchmarkContractError(f"hex_router_case_{index}_stratum_invalid")
        if raw_case["expected_cell"] not in ALL_CELLS:
            raise BenchmarkContractError(
                f"hex_router_case_{index}_expected_cell_invalid"
            )
        query = raw_case["query"]
        intent = SolverRouter.classify_intent(query)
        assignment = topology.assign_cell(intent, query)
        keyword_assignment = topology.assign_cell("chat", query)
        selected_cells = sorted(
            {assignment.cell_id, *assignment.neighbors_ring1}
        )
        dual_selected_cells = sorted(
            {
                *selected_cells,
                keyword_assignment.cell_id,
                *keyword_assignment.neighbors_ring1,
            }
        )
        rows.append(
            {
                "query_id": raw_case["query_id"],
                "stratum": raw_case["stratum"],
                "classified_intent": intent,
                "origin_cell": assignment.cell_id,
                "origin_method": assignment.method,
                "origin_expected_cell_present": (
                    raw_case["expected_cell"] == assignment.cell_id
                ),
                "selected_cells": selected_cells,
                "selected_cell_count": len(selected_cells),
                "expected_cell_present": raw_case["expected_cell"] in selected_cells,
                "keyword_origin_cell": keyword_assignment.cell_id,
                "intent_keyword_dual_ring1_cells": dual_selected_cells,
                "intent_keyword_dual_ring1_cell_count": len(dual_selected_cells),
                "intent_keyword_dual_ring1_expected_cell_present": (
                    raw_case["expected_cell"] in dual_selected_cells
                ),
            }
        )

    def summarize(
        selected: Sequence[Mapping[str, Any]],
        *,
        hit_key: str,
        count_key: str,
    ) -> dict[str, Any]:
        counts = [int(row[count_key]) for row in selected]
        hits = sum(row[hit_key] is True for row in selected)
        return {
            "cases": len(selected),
            "expected_cell_hits": hits,
            "expected_cell_coverage": round(hits / len(selected), 6),
            "selected_cell_count": {
                "min": min(counts),
                "mean": round(sum(counts) / len(counts), 6),
                "max": max(counts),
            },
        }

    def summarize_strategy(hit_key: str, count_key: str) -> dict[str, Any]:
        return {
            "all": summarize(rows, hit_key=hit_key, count_key=count_key),
            **{
                stratum: summarize(
                    [row for row in rows if row["stratum"] == stratum],
                    hit_key=hit_key,
                    count_key=count_key,
                )
                for stratum in sorted(_STRATA)
            },
        }

    for row in rows:
        row["origin_cell_count"] = 1
        row["all_cells_expected_cell_present"] = True
        row["all_cells_count"] = len(ALL_CELLS)
    strategy_comparison = {
        "origin_only": {
            "structural_reference_only": False,
            "metrics": summarize_strategy(
                "origin_expected_cell_present", "origin_cell_count"
            ),
        },
        "origin_plus_ring1": {
            "structural_reference_only": False,
            "metrics": summarize_strategy(
                "expected_cell_present", "selected_cell_count"
            ),
        },
        "intent_keyword_dual_ring1": {
            "structural_reference_only": False,
            "metrics": summarize_strategy(
                "intent_keyword_dual_ring1_expected_cell_present",
                "intent_keyword_dual_ring1_cell_count",
            ),
        },
        "all_cells_reference": {
            "structural_reference_only": True,
            "metrics": summarize_strategy(
                "all_cells_expected_cell_present", "all_cells_count"
            ),
        },
    }
    metrics = strategy_comparison["origin_plus_ring1"]["metrics"]
    policy_max_cells = max(
        1 + len(topology.get_neighbors(cell_id, max_ring=1))
        for cell_id in ALL_CELLS
    )
    criteria = {
        "all_expected_cells_covered": (
            metrics["all"]["expected_cell_coverage"] == 1.0
        ),
        "mean_cells_below_global_search": (
            metrics["all"]["selected_cell_count"]["mean"] < len(ALL_CELLS)
        ),
        "max_cells_within_origin_ring1_policy": (
            metrics["all"]["selected_cell_count"]["max"] <= policy_max_cells
        ),
    }
    passed = all(criteria.values())
    return {
        "axis": "hex_cell_label_blind_router",
        "axis_role": "router_coverage_only",
        "status": "MEASURED_PASS" if passed else "MEASURED_BLOCKED",
        "measurement_complete": True,
        "routing_policy": "solver_intent_origin_plus_ring1_v1",
        "router_inputs": ["query_text"],
        "router_input_fields": ["query"],
        "router_forbidden_inputs": [
            "expected_solver",
            "expected_cell",
            "stratum",
            "query_id",
        ],
        "labels_withheld_during_routing": True,
        "router_label_isolation_enforced": True,
        "topology_cell_count": len(ALL_CELLS),
        "actual_cell_local_faiss_search_evaluated": False,
        "cell_local_faiss_search_evaluated": False,
        "cell_local_faiss_search_executed_count": 0,
        "cell_local_search_latency_evaluated": False,
        "passed": passed,
        "criteria": criteria,
        "metrics": metrics,
        "max_cells_nominated_per_query": metrics["all"]["selected_cell_count"][
            "max"
        ],
        "max_cells_nominated_policy_limit": policy_max_cells,
        "strategy_comparison": strategy_comparison,
        "comparison_gaps": [
            "centroid_top_m_not_evaluated",
            "faiss_ivf_nprobe_not_evaluated",
            "all_cell_latency_scaling_threshold_not_evaluated",
        ],
        "per_query": rows,
        "runtime_authority_ready": False,
        "runtime_authority_granted": False,
        "production_promotion_gate_pass": False,
        "fallback_used": False,
    }


def normalize_embedding_matrix(
    values: Any,
    *,
    expected_rows: int,
    expected_dimension: int,
    label: str,
) -> np.ndarray:
    try:
        matrix = np.asarray(values, dtype=np.float32)
    except (TypeError, ValueError) as exc:
        raise EmbeddingValidationError(f"{label}_not_numeric_rectangular") from exc
    if matrix.ndim != 2:
        raise EmbeddingValidationError(f"{label}_must_be_2d")
    if matrix.shape != (expected_rows, expected_dimension):
        raise EmbeddingValidationError(
            f"{label}_shape_mismatch:{matrix.shape}!={(expected_rows, expected_dimension)}"
        )
    if not np.isfinite(matrix).all():
        raise EmbeddingValidationError(f"{label}_contains_nonfinite_value")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if not np.isfinite(norms).all() or np.any(norms <= 1.0e-12):
        raise EmbeddingValidationError(f"{label}_contains_zero_or_invalid_norm")
    normalized = np.ascontiguousarray(matrix / norms, dtype=np.float32)
    if not np.isfinite(normalized).all():
        raise EmbeddingValidationError(f"{label}_normalization_failed")
    return normalized


def rank_score_row(scores: np.ndarray, solver_ids: Sequence[str], k: int = 5) -> list[int]:
    row = np.asarray(scores, dtype=np.float32)
    if row.ndim != 1 or row.shape[0] != len(solver_ids):
        raise BenchmarkContractError("score_row_shape_mismatch")
    if not np.isfinite(row).all():
        raise BenchmarkContractError("score_row_contains_nonfinite_value")
    if len(solver_ids) != len(set(solver_ids)):
        raise BenchmarkContractError("ranking_solver_ids_must_be_unique")
    if type(k) is not int or k <= 0:
        raise BenchmarkContractError("ranking_k_must_be_positive_integer")
    # Explicit solver-id tiebreak means rankings do not depend on input order.
    order = sorted(range(len(solver_ids)), key=lambda index: (-float(row[index]), solver_ids[index]))
    return order[: min(k, len(order))]


def _percentiles(values: Sequence[float]) -> dict[str, float]:
    if not values:
        return {"p50": 0.0, "p95": 0.0}
    return {
        "p50": round(float(np.percentile(values, 50)), 6),
        "p95": round(float(np.percentile(values, 95)), 6),
    }


def _evaluate_rankings(
    rankings: Sequence[Sequence[int]],
    score_matrix: np.ndarray,
    solver_ids: Sequence[str],
    documents: Sequence[Mapping[str, Any]],
    cases: Sequence[Mapping[str, str]],
    tokenizer: ContentTokenizer,
    search_latency_ms: Sequence[float],
) -> dict[str, Any]:
    if len(rankings) != len(cases) or score_matrix.shape != (len(cases), len(solver_ids)):
        raise BenchmarkContractError("ranking_evaluation_shape_mismatch")
    if len(search_latency_ms) != len(cases):
        raise BenchmarkContractError("search_latency_count_mismatch")
    document_by_solver = {row["canonical_solver_id"]: row for row in documents}
    per_query: list[dict[str, Any]] = []
    for index, case in enumerate(cases):
        ranking = list(rankings[index])
        chosen = solver_ids[ranking[0]] if ranking else None
        expected = case["expected_solver"]
        expected_rank = next(
            (rank + 1 for rank, candidate in enumerate(ranking) if solver_ids[candidate] == expected),
            None,
        )
        query_tokens = set(tokenizer(case["query"]))
        expected_overlap = query_tokens & set(tokenizer(document_by_solver[expected]["embedding_text"]))
        chosen_overlap = (
            query_tokens & set(tokenizer(document_by_solver[chosen]["embedding_text"]))
            if chosen is not None
            else set()
        )
        per_query.append(
            {
                "query_id": case["query_id"],
                "stratum": case["stratum"],
                "expected_solver": expected,
                "chosen_solver": chosen,
                "correct_at_1": chosen == expected,
                "expected_rank_at_5": expected_rank,
                "expected_lexical_overlap_count": len(expected_overlap),
                "chosen_lexical_overlap_count": len(chosen_overlap),
                "top_k": [
                    {
                        "solver_id": solver_ids[candidate],
                        "score": round(float(score_matrix[index, candidate]), 8),
                    }
                    for candidate in ranking
                ],
            }
        )

    def aggregate(selected: Iterable[dict[str, Any]]) -> dict[str, Any]:
        rows = list(selected)
        if not rows:
            return {
                "cases": 0,
                "top1_accuracy": None,
                "recall_at_5": None,
                "nonempty_rate": None,
            }
        return {
            "cases": len(rows),
            "top1_accuracy": round(sum(row["correct_at_1"] for row in rows) / len(rows), 6),
            "recall_at_5": round(
                sum(row["expected_rank_at_5"] is not None for row in rows) / len(rows), 6
            ),
            "nonempty_rate": round(sum(row["chosen_solver"] is not None for row in rows) / len(rows), 6),
        }

    return {
        "measurement_status": "complete",
        "measurement_complete": True,
        "cases_total": len(cases),
        "cases_evaluated": len(cases),
        "metrics": {
            "all": aggregate(per_query),
            "anchored_natural": aggregate(
                row for row in per_query if row["stratum"] == "anchored_natural"
            ),
            "semantic_zero_overlap": aggregate(
                row for row in per_query if row["stratum"] == "semantic_zero_overlap"
            ),
        },
        "latency_ms": {"search": _percentiles(search_latency_ms)},
        "per_query": per_query,
    }


def run_lexical_benchmark(
    documents: Sequence[Mapping[str, Any]],
    query_texts: Sequence[str],
    cases: Sequence[Mapping[str, str]],
    tokenizer: ContentTokenizer,
) -> dict[str, Any]:
    """Run deterministic TF-IDF without receiving evaluation labels."""
    solver_ids = [row["canonical_solver_id"] for row in documents]
    document_tokens = [tokenizer(row["embedding_text"]) for row in documents]
    document_frequency = Counter(
        token for tokens in document_tokens for token in set(tokens)
    )
    vocabulary = sorted(document_frequency)
    token_index = {token: index for index, token in enumerate(vocabulary)}
    idf = {
        token: math.log((1 + len(documents)) / (1 + count)) + 1.0
        for token, count in document_frequency.items()
    }

    def vectorize(tokens: Sequence[str]) -> np.ndarray:
        counts = Counter(token for token in tokens if token in token_index)
        vector = np.zeros(len(vocabulary), dtype=np.float32)
        for token, count in counts.items():
            vector[token_index[token]] = float(count) * idf[token]
        norm = float(np.linalg.norm(vector))
        return vector / norm if norm > 0.0 else vector

    document_matrix = np.stack([vectorize(tokens) for tokens in document_tokens])
    rankings: list[list[int]] = []
    score_rows: list[np.ndarray] = []
    search_latency_ms: list[float] = []
    for query in query_texts:
        query_vector = vectorize(tokenizer(query))
        started = time.perf_counter_ns()
        scores = query_vector @ document_matrix.T
        ranking = rank_score_row(scores, solver_ids, k=5)
        search_latency_ms.append((time.perf_counter_ns() - started) / 1_000_000.0)
        score_rows.append(scores)
        rankings.append(ranking)
    result = _evaluate_rankings(
        rankings,
        np.stack(score_rows),
        solver_ids,
        documents,
        cases,
        tokenizer,
        search_latency_ms,
    )
    result.update(
        {
            "axis": "A0_lexical_strict_projection",
            "index_kind": "deterministic_tfidf",
            "fallback_used": False,
        }
    )
    return result


class OllamaEmbeddingClient:
    """Catalog-checked Ollama embedder with a persistent HTTP connection."""

    def __init__(self, base_url: str, timeout_seconds: float = 120.0) -> None:
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout_seconds)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "OllamaEmbeddingClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def verify_profile(self, profile: EmbeddingProfile) -> dict[str, Any]:
        try:
            response = self._client.get("/api/tags")
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise BenchmarkUnavailable("embedding_backend_unavailable") from exc
        models = payload.get("models") if type(payload) is dict else None
        if type(models) is not list:
            raise BenchmarkUnavailable("embedding_model_catalog_invalid")
        matched = next(
            (
                row
                for row in models
                if type(row) is dict
                and row.get("name") == profile.model_id
                and row.get("digest") == profile.model_digest
            ),
            None,
        )
        if matched is None:
            matching_name = any(
                type(row) is dict and row.get("name") == profile.model_id for row in models
            )
            reason = "embedding_model_digest_mismatch" if matching_name else "embedding_model_missing"
            raise BenchmarkUnavailable(reason)
        return {
            "provider": "ollama",
            "requested_model_tag": matched["name"],
            "catalog_digest": matched["digest"],
        }

    def embed(
        self,
        texts: Sequence[str],
        profile: EmbeddingProfile,
        *,
        label: str,
    ) -> np.ndarray:
        if not texts:
            raise EmbeddingValidationError(f"{label}_input_empty")
        if any(type(text) is not str or not text.strip() for text in texts):
            raise EmbeddingValidationError(f"{label}_input_invalid")
        try:
            response = self._client.post(
                "/api/embed",
                json={
                    "model": profile.model_id,
                    "input": list(texts),
                    "truncate": False,
                    "keep_alive": "30m",
                },
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise BenchmarkUnavailable("embedding_request_failed") from exc
        if type(payload) is not dict or payload.get("model") != profile.model_id:
            raise EmbeddingValidationError(f"{label}_response_model_mismatch")
        embeddings = payload.get("embeddings")
        return normalize_embedding_matrix(
            embeddings,
            expected_rows=len(texts),
            expected_dimension=profile.dimension,
            label=label,
        )


def run_vector_benchmark(
    documents: Sequence[Mapping[str, Any]],
    query_texts: Sequence[str],
    cases: Sequence[Mapping[str, str]],
    tokenizer: ContentTokenizer,
    profile: EmbeddingProfile,
    embedder: OllamaEmbeddingClient,
) -> dict[str, Any]:
    """Run label-blind exact retrieval under one catalog-checked contract."""
    solver_ids = [row["canonical_solver_id"] for row in documents]
    document_inputs = [profile.document_prefix + row["embedding_text"] for row in documents]
    query_inputs = [profile.query_prefix + query for query in query_texts]
    identity_before = embedder.verify_profile(profile)
    identity_before_verified = provider_identity_matches_profile(
        identity_before, profile
    )
    if not identity_before_verified:
        raise EmbeddingValidationError("provider_identity_before_mismatch")
    document_started = time.perf_counter_ns()
    document_matrix = embedder.embed(document_inputs, profile, label="document_embeddings")
    document_embedding_ms = (time.perf_counter_ns() - document_started) / 1_000_000.0
    query_vectors: list[np.ndarray] = []
    query_embedding_ms: list[float] = []
    for query_input in query_inputs:
        started = time.perf_counter_ns()
        vector = embedder.embed([query_input], profile, label="query_embeddings")
        query_embedding_ms.append((time.perf_counter_ns() - started) / 1_000_000.0)
        query_vectors.append(vector[0])
    query_matrix = normalize_embedding_matrix(
        query_vectors,
        expected_rows=len(query_inputs),
        expected_dimension=profile.dimension,
        label="query_embedding_matrix",
    )
    identity_after = embedder.verify_profile(profile)
    identity_after_verified = provider_identity_matches_profile(
        identity_after, profile
    )
    if not identity_after_verified:
        raise EmbeddingValidationError("provider_identity_after_mismatch")
    if identity_after != identity_before:
        raise BenchmarkUnavailable("embedding_model_catalog_changed_during_run")
    rankings: list[list[int]] = []
    score_rows: list[np.ndarray] = []
    search_latency_ms: list[float] = []
    for query_vector in query_matrix:
        started = time.perf_counter_ns()
        scores = query_vector @ document_matrix.T
        ranking = rank_score_row(scores, solver_ids, k=5)
        search_latency_ms.append((time.perf_counter_ns() - started) / 1_000_000.0)
        score_rows.append(scores)
        rankings.append(ranking)
    result = _evaluate_rankings(
        rankings,
        np.stack(score_rows),
        solver_ids,
        documents,
        cases,
        tokenizer,
        search_latency_ms,
    )
    contract = vector_projection.build_embedding_contract(
        model_id=profile.model_id,
        model_version="ollama-catalog-sha256:" + profile.model_digest,
        dimension=profile.dimension,
        normalization="l2-v1",
        document_prefix=profile.document_prefix,
        query_prefix=profile.query_prefix,
    )
    result["latency_ms"].update(
        {
            "document_embedding_batch": round(document_embedding_ms, 6),
            "query_embedding": _percentiles(query_embedding_ms),
            "query_end_to_end": _percentiles(
                [embed_ms + search_ms for embed_ms, search_ms in zip(query_embedding_ms, search_latency_ms)]
            ),
        }
    )
    result.update(
        {
            "axis": "A2_exact_vector_global",
            "profile": profile.name,
            "index_kind": "exact_numpy_flat_ip_proxy",
            "faiss_materialized": False,
            "embedding_contract": contract,
            "provider_identity_evidence": {
                **identity_before,
                "catalog_contract_verified_before_embedding": (
                    identity_before_verified
                ),
                "catalog_contract_verified_after_embedding": (
                    identity_after_verified
                ),
                "response_digest_attested": False,
                "limitation": (
                    "Ollama embeds by mutable tag and does not attest a model digest "
                    "in /api/embed responses; the catalog digest was equal before and "
                    "after this candidate-only run."
                ),
            },
            "fallback_used": False,
        }
    )
    return result


def unavailable_vector_result(profile: EmbeddingProfile, cases_total: int, reason: str) -> dict[str, Any]:
    return {
        "axis": "A2_exact_vector_global",
        "profile": profile.name,
        "measurement_status": "unavailable",
        "measurement_complete": False,
        "reason_code": reason,
        "cases_total": cases_total,
        "cases_evaluated": 0,
        "metrics": None,
        "latency_ms": None,
        "per_query": [],
        "index_kind": "exact_numpy_flat_ip_proxy",
        "faiss_materialized": False,
        "provider_identity_evidence": None,
        "fallback_used": False,
    }


def existing_index_preflight(vector_root: Path = DEFAULT_VECTOR_ROOT) -> dict[str, Any]:
    """Truthfully refuse A1 until a searchable, fully bound schema exists."""
    base = {
        "axis": "A1_existing_index",
        "status": "NOT_AVAILABLE_NOT_RUN",
        "available": False,
        "selected_artifact": None,
        "queries_attempted": 0,
        "artifacts_created": 0,
        "artifacts_imported": 0,
        "fallback_used": False,
    }
    if not vector_root.exists():
        return {**base, "reason_codes": ["vector_root_missing"]}
    if vector_root.is_symlink() or not vector_root.is_dir():
        return {**base, "reason_codes": ["vector_root_not_regular_directory"]}
    # P1 commits are contract projections with index_kind=none.  Legacy flat
    # artifacts do not bind model digest, prefixes, normalization and topology.
    # Neither is admissible as an A1 search result.
    return {**base, "reason_codes": ["searchable_materialization_contract_absent"]}


def _paired_sign_evidence(
    a0: Mapping[str, Any],
    a2: Mapping[str, Any],
    *,
    stratum: str,
    outcome: str,
) -> dict[str, Any]:
    baseline = {
        row["query_id"]: row
        for row in a0.get("per_query", [])
        if row.get("stratum") == stratum
    }
    candidate = {
        row["query_id"]: row
        for row in a2.get("per_query", [])
        if row.get("stratum") == stratum
    }
    if not baseline or set(baseline) != set(candidate):
        raise BenchmarkContractError("paired_gate_query_set_mismatch")

    def success(row: Mapping[str, Any]) -> bool:
        if outcome == "top1":
            return row.get("correct_at_1") is True
        if outcome == "recall_at_5":
            return row.get("expected_rank_at_5") is not None
        raise BenchmarkContractError("unknown_paired_gate_outcome")

    wins = 0
    losses = 0
    for query_id in sorted(baseline):
        before = success(baseline[query_id])
        after = success(candidate[query_id])
        wins += int(after and not before)
        losses += int(before and not after)
    discordant = wins + losses
    if discordant == 0:
        p_one_sided = 1.0
    else:
        p_one_sided = sum(
            math.comb(discordant, successes)
            for successes in range(wins, discordant + 1)
        ) / (2**discordant)
    return {
        "cases": len(baseline),
        "candidate_wins": wins,
        "candidate_losses": losses,
        "discordant_pairs": discordant,
        "one_sided_exact_sign_p": round(p_one_sided, 8),
        "passed": wins > losses and p_one_sided <= 0.05,
    }


def differential_gate(a0: Mapping[str, Any], a2: Mapping[str, Any]) -> dict[str, Any]:
    if not a2.get("measurement_complete"):
        return {
            "passed": False,
            "criteria": {"measurement_complete": False},
            "reason": "vector_measurement_unavailable",
        }
    baseline = a0["metrics"]
    candidate = a2["metrics"]
    semantic_top1 = _paired_sign_evidence(
        a0, a2, stratum="semantic_zero_overlap", outcome="top1"
    )
    semantic_recall5 = _paired_sign_evidence(
        a0, a2, stratum="semantic_zero_overlap", outcome="recall_at_5"
    )
    anchored_top1 = _paired_sign_evidence(
        a0, a2, stratum="anchored_natural", outcome="top1"
    )
    anchored_recall5 = _paired_sign_evidence(
        a0, a2, stratum="anchored_natural", outcome="recall_at_5"
    )
    identity = a2.get("provider_identity_evidence") or {}
    criteria = {
        "measurement_complete": True,
        "overall_top1_non_regression": (
            candidate["all"]["top1_accuracy"] >= baseline["all"]["top1_accuracy"]
        ),
        "overall_recall5_non_regression": (
            candidate["all"]["recall_at_5"] >= baseline["all"]["recall_at_5"]
        ),
        "anchored_top1_net_loss_at_most_one_case": (
            anchored_top1["candidate_losses"] - anchored_top1["candidate_wins"]
            <= 1
        ),
        "anchored_recall5_non_regression": (
            anchored_recall5["candidate_losses"]
            <= anchored_recall5["candidate_wins"]
        ),
        "semantic_top1_paired_evidence": semantic_top1["passed"],
        "semantic_recall5_paired_evidence": semantic_recall5["passed"],
        "rankings_present_all_queries": candidate["all"]["nonempty_rate"] == 1.0,
        "exact_search_p95_below_10ms": a2["latency_ms"]["search"]["p95"] < 10.0,
        "provider_catalog_contract_verified_before_embedding": identity.get(
            "catalog_contract_verified_before_embedding"
        ) is True,
        "provider_catalog_contract_verified_after_embedding": identity.get(
            "catalog_contract_verified_after_embedding"
        ) is True,
    }
    return {
        "passed": all(criteria.values()),
        "gate_scope": "paired_positive_ranking_only_no_rejection_calibration",
        "criteria": criteria,
        "paired_evidence": {
            "anchored_top1": anchored_top1,
            "anchored_recall5": anchored_recall5,
            "semantic_top1": semantic_top1,
            "semantic_recall5": semantic_recall5,
        },
        "deltas": {
            "overall_top1": round(
                candidate["all"]["top1_accuracy"] - baseline["all"]["top1_accuracy"], 6
            ),
            "overall_recall5": round(
                candidate["all"]["recall_at_5"] - baseline["all"]["recall_at_5"], 6
            ),
            "semantic_top1": round(
                candidate["semantic_zero_overlap"]["top1_accuracy"]
                - baseline["semantic_zero_overlap"]["top1_accuracy"],
                6,
            ),
            "semantic_recall5": round(
                candidate["semantic_zero_overlap"]["recall_at_5"]
                - baseline["semantic_zero_overlap"]["recall_at_5"],
                6,
            ),
        },
    }


def _is_link_like(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0)
        & _WINDOWS_REPARSE_POINT
    )


def resolve_audit_output(raw_path: str, repo_root: Path = ROOT) -> Path:
    if not raw_path or Path(raw_path).is_absolute():
        raise BenchmarkContractError("output_path_must_be_repo_relative")
    unresolved_audit_root = repo_root / ".codex-audit"
    if _is_link_like(unresolved_audit_root):
        raise BenchmarkContractError("codex_audit_root_must_not_be_link")
    audit_root = unresolved_audit_root.resolve()
    candidate = (repo_root / raw_path).resolve()
    try:
        candidate.relative_to(audit_root)
    except ValueError as exc:
        raise BenchmarkContractError("output_path_must_be_beneath_codex_audit") from exc
    if candidate == audit_root:
        raise BenchmarkContractError("output_path_must_name_a_file")
    cursor = candidate.parent
    while cursor != audit_root:
        if cursor.exists() and _is_link_like(cursor):
            raise BenchmarkContractError("output_parent_must_not_be_link")
        if cursor.parent == cursor:
            raise BenchmarkContractError("output_path_parent_invalid")
        cursor = cursor.parent
    if candidate.exists():
        raise BenchmarkContractError("output_path_already_exists")
    return candidate


def write_audit_report(report: Mapping[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
    with output_path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(rendered)
        handle.write("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS.relative_to(ROOT)))
    parser.add_argument("--axioms-dir", default=str(DEFAULT_AXIOMS.relative_to(ROOT)))
    parser.add_argument("--vector-root", default=str(DEFAULT_VECTOR_ROOT.relative_to(ROOT)))
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument(
        "--profile",
        action="append",
        choices=sorted(EMBEDDING_PROFILES),
        help="Pinned A2 profile; repeat to compare. Defaults to nomic.",
    )
    parser.add_argument("--skip-vector", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--output", help="Repo-relative path beneath .codex-audit")
    parser.add_argument("--no-write", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    raw_output = args.output or f".codex-audit/magma_solver_retrieval_{timestamp}.json"
    try:
        output_path = None if args.no_write else resolve_audit_output(raw_output)
        corpus_path = (ROOT / args.corpus).resolve()
        axiom_dir = (ROOT / args.axioms_dir).resolve()
        vector_root = (ROOT / args.vector_root).resolve()
        documents, topology_digest = load_projection_documents(axiom_dir)
        corpus, tokenizer, corpus_digest, raw_corpus_digest = load_corpus(
            corpus_path, documents
        )
        hex_router_axis = evaluate_label_blind_hex_router(corpus["cases"])
        # Only query text crosses into retrieval.  Labels remain in `cases` and
        # are consumed solely by post-ranking evaluation.
        query_texts = [case["query"] for case in corpus["cases"]]
        a0 = run_lexical_benchmark(
            documents, query_texts, corpus["cases"], tokenizer
        )
        a1 = existing_index_preflight(vector_root)
        a2_results: list[dict[str, Any]] = []
        gates: dict[str, Any] = {}
        requested_profiles = args.profile or ["nomic"]
        if len(requested_profiles) != len(set(requested_profiles)):
            raise BenchmarkContractError("duplicate_embedding_profile")
        if args.skip_vector and args.profile:
            raise BenchmarkContractError("embedding_profile_conflicts_with_skip_vector")
        profiles = [] if args.skip_vector else requested_profiles
        for profile_name in profiles:
            profile = EMBEDDING_PROFILES[profile_name]
            try:
                with OllamaEmbeddingClient(
                    args.ollama_url, timeout_seconds=args.timeout_seconds
                ) as embedder:
                    result = run_vector_benchmark(
                        documents,
                        query_texts,
                        corpus["cases"],
                        tokenizer,
                        profile,
                        embedder,
                    )
            except BenchmarkUnavailable as exc:
                result = unavailable_vector_result(profile, len(query_texts), str(exc))
            except EmbeddingValidationError:
                result = unavailable_vector_result(
                    profile, len(query_texts), "embedding_response_invalid"
                )
            a2_results.append(result)
            gates[profile_name] = differential_gate(a0, result)
        report = {
            "schema_version": "wd.magma.solver_retrieval_benchmark.v2",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "evidence_scope": "candidate_only_no_runtime_authority",
            "corpus": {
                "path": corpus_path.relative_to(ROOT).as_posix(),
                "canonical_sha256": corpus_digest,
                "raw_sha256": raw_corpus_digest,
                "cases": len(corpus["cases"]),
                "solver_coverage": len(documents),
            },
            "projection": {
                "schema_version": vector_projection.SOLVER_PROJECTION_VERSION,
                "documents": len(documents),
                "topology_digest": topology_digest,
            },
            "A0": a0,
            "A1": a1,
            "A2": a2_results,
            "differential_gates": gates,
            "hex_cell_router_axis": hex_router_axis,
            "hex_cell_local_axis": {
                "status": "NOT_RUN",
                "reason": (
                    "requires_passing_label_blind_router_and_actual_cell_local_search"
                ),
                "router_axis_passed": hex_router_axis["passed"],
                "actual_cell_local_faiss_search_evaluated": False,
                "cell_local_faiss_search_executed_count": 0,
            },
            "runtime_authority_ready": False,
            "runtime_authority_blockers": [
                "A1_searchable_materialization_absent",
                "A3_guarded_authoritative_trial_not_run",
                "independent_corpus_adjudication_pending",
                "off_domain_rejection_not_calibrated",
                "ollama_embed_response_digest_not_attested",
                "hex_cell_local_faiss_search_not_run",
                *(
                    []
                    if hex_router_axis["passed"]
                    else ["hex_cell_router_expected_cell_coverage_below_gate"]
                ),
            ],
        }
        if output_path is not None:
            write_audit_report(report, output_path)
            print(output_path.relative_to(ROOT).as_posix())
        print(json.dumps({
            "A0": a0["metrics"],
            "A1": a1,
            "gates": gates,
            "hex_cell_router_axis": {
                "axis_role": hex_router_axis["axis_role"],
                "status": hex_router_axis["status"],
                "passed": hex_router_axis["passed"],
                "metrics": hex_router_axis["metrics"],
                "strategy_comparison": hex_router_axis["strategy_comparison"],
                "comparison_gaps": hex_router_axis["comparison_gaps"],
                "actual_cell_local_faiss_search_evaluated": False,
                "cell_local_faiss_search_executed_count": 0,
                "runtime_authority_granted": False,
                "production_promotion_gate_pass": False,
            },
            "runtime_authority_ready": False,
        }, indent=2, sort_keys=True, allow_nan=False))
        if profiles and any(not gate["passed"] for gate in gates.values()):
            return 1
        return 0
    except BenchmarkContractError as exc:
        print(f"BENCHMARK CONTRACT ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
