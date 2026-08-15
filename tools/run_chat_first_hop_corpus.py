# SPDX-License-Identifier: BUSL-1.1
"""Deterministic, measurement-only ChatService first-hop corpus harness.

The report binds every corpus row and route-evidence record to a W1A-v3 run
header. Raw queries, cached responses, and caller-provided row IDs never enter
the output. This harness does not persist receipts, grant runtime authority, or
flip ``claim_safe``.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.application.dto.chat_dto import ChatRequest  # noqa: E402
from waggledance.application.services.chat_service import ChatService  # noqa: E402
from waggledance.core.domain.agent import AgentResult  # noqa: E402
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402
from waggledance.core.magma.chat_query_route_evidence import (  # noqa: E402
    NORMALIZATION_VERSION,
    QUERY_DIGEST_DOMAIN,
    canonical_query_digest,
)
from waggledance.core.orchestration.routing_policy import select_route  # noqa: E402

REPORT_VERSION = "wd.chat_first_hop_corpus.v1"
SCHEMA_VERSION = "wd.chat_query_route_evidence.v1"
DENOMINATOR_SCOPE = "all_non_cached_served_first_hops"
MEASUREMENT_SCOPE = "chatservice_handle_route_stage_trace_with_deterministic_fakes"
REPRESENTATIVENESS_SCOPE = "non_production_representative_deterministic_corpus"
CORPUS_DIGEST_DOMAIN = "wd.chat_query_route_evidence.corpus_digest.v1"
ROW_ID_DOMAIN = "wd.chat_first_hop_row_id.v1"
CANDIDATE_REF_SCHEMA_VERSION = "wd.chat_query_candidate_receipt_ref.v1"
GAP_REF_SCHEMA_VERSION = "wd.chat_query_gap_candidate_receipt_ref.v1"
RUN_ID_DOMAIN = "wd.chat_first_hop_run.v1"

ROUTE_DECISIONS = frozenset({"solver_first", "fallback", "refused"})
FIRST_HOP_CLASSES = frozenset(
    {"authoritative", "heuristic", "fallback", "refused", "gap"}
)
GAP_REASONS = frozenset({"harness_exception", "missing_served_route_stage"})
RESOLUTION_STAGES = (
    "deterministic_solver",
    "hybrid_retrieval_8_cell",
    "hex_neighbor_assist_7_cell",
    "orchestrator_llm_fallback",
)
RUN_HEADER_KEYS = frozenset(
    {
        "head_commit_sha",
        "corpus_digest",
        "schema_version",
        "normalization_version",
        "run_id",
    }
)
SAFE_RECORD_KEYS = frozenset(
    {
        "id",
        "query_digest",
        "normalization_version",
        "route_decision",
        "first_hop_class",
        "first_hop_stage",
        "first_hop_solver",
        "refusal_reason",
        "fallback_used",
        "candidate_receipt_ref",
        "emitted_at_seq",
    }
)
SAFE_GAP_RECORD_KEYS = frozenset(
    {
        "id",
        "query_digest",
        "normalization_version",
        "first_hop_class",
        "gap_reason",
        "candidate_receipt_ref",
        "emitted_at_seq",
    }
)
CORPUS_ROW_KEYS = frozenset(
    {"id", "query", "profile", "language", "cached_response"}
)
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_HEAD_SHA_RE = re.compile(r"^[a-f0-9]{40}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_METADATA_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")

DEFAULT_CORPUS: tuple[dict[str, Any], ...] = (
    {"id": "solver_math_percent", "query": "what is 15% of 300", "profile": "HOME"},
    {
        "id": "solver_stats_fallback",
        "query": "statistics summary for hive readings",
        "profile": "HOME",
    },
    {
        "id": "general_llm_heuristic",
        "query": "explain routine hive care",
        "profile": "HOME",
    },
    {
        "id": "hotcache_excluded",
        "query": "what is varroa",
        "profile": "HOME",
        "cached_response": "cached varroa answer",
    },
)


class CorpusValidationError(ValueError):
    """Raised with a privacy-safe reason code for invalid corpus input."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class HeaderValidationError(ValueError):
    """Raised with a privacy-safe reason code for an invalid run header."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _StaticConfig:
    def __init__(self) -> None:
        self._settings = {
            "swarm.enabled": False,
            "advanced_learning.micro_model_enabled": False,
            "chat_served_receipts.enabled": False,
        }

    def get(self, key: str, default: Any = None) -> Any:
        return self._settings.get(key, default)

    def get_profile(self) -> str:
        return "COTTAGE"

    def get_hardware_tier(self) -> str:
        return "standard"


class _StaticHotCache:
    def __init__(self, cached: Mapping[str, str]) -> None:
        self._cached = {self._key(k): str(v) for k, v in cached.items()}

    @staticmethod
    def _key(query: str) -> str:
        return str(query).strip().lower()

    def get(self, key: str) -> str | None:
        return self._cached.get(self._key(key))

    def set(self, key: str, value: str, ttl: int | None = None) -> None:
        return None


class _StaticMemoryService:
    async def retrieve_context(self, **_: Any) -> list[Any]:
        return []


class _StaticOrchestrator:
    async def handle_task(self, task: Any, route: Any) -> AgentResult:
        return AgentResult(
            agent_id="w1b-fake-llm",
            response="Synthetic measurement answer",
            confidence=0.8,
            latency_ms=1.0,
            source="llm",
        )

    async def run_round_table(self, task: Any) -> Any:  # pragma: no cover
        raise AssertionError("W1B harness should not need round table consensus")


def load_corpus(path: Path) -> list[Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise CorpusValidationError("corpus_not_list")
    return list(data)


# Compatibility name for existing harness callers. The algorithm lives in core so
# every route-evidence producer consumes the same versioned preimage contract.
_query_digest = canonical_query_digest


def _opaque_row_id(row_id: str) -> str:
    return sha256_digest({"domain": ROW_ID_DOMAIN, "id": row_id})


def _validate_corpus(corpus: Sequence[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_queries: set[str] = set()
    for index, item in enumerate(corpus):
        prefix = f"row_{index}"
        if not isinstance(item, Mapping):
            raise CorpusValidationError(f"{prefix}_not_mapping")
        if set(item) - CORPUS_ROW_KEYS:
            raise CorpusValidationError(f"{prefix}_unknown_fields")

        row_id = item.get("id")
        query = item.get("query")
        if not isinstance(row_id, str) or not row_id.strip():
            raise CorpusValidationError(f"{prefix}_invalid_id")
        if not isinstance(query, str) or not query.strip():
            raise CorpusValidationError(f"{prefix}_invalid_query")
        if row_id in seen_ids:
            raise CorpusValidationError("duplicate_id")
        query_digest = _query_digest(query)
        if query_digest in seen_queries:
            raise CorpusValidationError("duplicate_query_digest")

        profile = item.get("profile", "HOME")
        language = item.get("language", "auto")
        if not isinstance(profile, str) or not _METADATA_RE.fullmatch(profile):
            raise CorpusValidationError(f"{prefix}_invalid_profile")
        if not isinstance(language, str) or not _METADATA_RE.fullmatch(language):
            raise CorpusValidationError(f"{prefix}_invalid_language")
        if "cached_response" in item and not isinstance(item["cached_response"], str):
            raise CorpusValidationError(f"{prefix}_invalid_cached_response")

        row = {
            "id": row_id,
            "query": query,
            "profile": profile,
            "language": language,
        }
        if "cached_response" in item:
            row["cached_response"] = item["cached_response"]
        rows.append(row)
        seen_ids.add(row_id)
        seen_queries.add(query_digest)
    return rows


def _corpus_digest_from_query_digests(query_digests: Iterable[str]) -> str:
    digests = list(query_digests)
    if any(
        not isinstance(digest, str) or not _DIGEST_RE.fullmatch(digest)
        for digest in digests
    ):
        raise CorpusValidationError("invalid_corpus_query_digest")
    if len(digests) != len(set(digests)):
        raise CorpusValidationError("duplicate_corpus_query_digest")
    return sha256_digest(
        {
            "domain": CORPUS_DIGEST_DOMAIN,
            "schema_version": SCHEMA_VERSION,
            "normalization_version": NORMALIZATION_VERSION,
            "query_digests": sorted(digests),
        }
    )


def _corpus_digest(rows: Sequence[Mapping[str, Any]]) -> str:
    return _corpus_digest_from_query_digests(
        _query_digest(str(row["query"]))
        for row in rows
        if "cached_response" not in row
    )


def _current_head_commit_sha() -> str:
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if status.returncode != 0 or status.stdout.strip():
            raise HeaderValidationError("source_worktree_not_clean")
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise HeaderValidationError("head_commit_unavailable") from exc
    if completed.returncode != 0:
        raise HeaderValidationError("head_commit_unavailable")
    return completed.stdout.strip()


def _build_run_header(
    rows: Sequence[Mapping[str, Any]],
    *,
    expected_head_commit_sha: str | None = None,
    run_id: str | None = None,
) -> dict[str, str]:
    head = _current_head_commit_sha()
    if not isinstance(head, str) or not _HEAD_SHA_RE.fullmatch(head):
        raise HeaderValidationError("invalid_head_commit_sha")
    if expected_head_commit_sha is not None:
        if (
            not isinstance(expected_head_commit_sha, str)
            or not _HEAD_SHA_RE.fullmatch(expected_head_commit_sha)
        ):
            raise HeaderValidationError("invalid_expected_head_commit_sha")
        if expected_head_commit_sha != head:
            raise HeaderValidationError("head_commit_mismatch")
    corpus_digest = _corpus_digest(rows)
    if run_id is None:
        run_id = sha256_digest(
            {
                "domain": RUN_ID_DOMAIN,
                "head_commit_sha": head,
                "corpus_digest": corpus_digest,
                "schema_version": SCHEMA_VERSION,
                "normalization_version": NORMALIZATION_VERSION,
            }
        )
    if not isinstance(run_id, str) or not _DIGEST_RE.fullmatch(run_id):
        raise HeaderValidationError("invalid_run_id")
    return {
        "head_commit_sha": head,
        "corpus_digest": corpus_digest,
        "schema_version": SCHEMA_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "run_id": run_id,
    }


def _validate_run_header(header: Mapping[str, Any]) -> bool:
    if not isinstance(header, Mapping):
        return False
    return bool(
        set(header) == RUN_HEADER_KEYS
        and _HEAD_SHA_RE.fullmatch(str(header.get("head_commit_sha", "")))
        and _DIGEST_RE.fullmatch(str(header.get("corpus_digest", "")))
        and header.get("schema_version") == SCHEMA_VERSION
        and header.get("normalization_version") == NORMALIZATION_VERSION
        and _DIGEST_RE.fullmatch(str(header.get("run_id", "")))
    )


def _candidate_ref(
    record: Mapping[str, Any],
    header: Mapping[str, Any],
    *,
    kind: str,
) -> str:
    if kind == "record":
        evidence = {
            key: record[key]
            for key in (
                "query_digest",
                "normalization_version",
                "route_decision",
                "first_hop_solver",
                "refusal_reason",
                "fallback_used",
                "emitted_at_seq",
            )
        }
        payload = {
            "schema_version": CANDIDATE_REF_SCHEMA_VERSION,
            "header": dict(header),
            "route_evidence": evidence,
        }
    elif kind == "gap":
        evidence = {
            key: record[key]
            for key in (
                "query_digest",
                "normalization_version",
                "gap_reason",
                "emitted_at_seq",
            )
        }
        payload = {
            "schema_version": GAP_REF_SCHEMA_VERSION,
            "header": dict(header),
            "gap_evidence": evidence,
        }
    else:  # pragma: no cover - callers use a closed local enum
        raise ValueError("invalid_candidate_ref_kind")
    return sha256_digest(payload)


def _iter_string_values(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _iter_string_values(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_string_values(item)


def _raw_inputs_not_emitted(
    report: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]
) -> bool:
    emitted = tuple(_iter_string_values(report))
    raw_values: set[str] = set()
    for row in rows:
        raw_values.add(str(row["id"]))
        raw_values.add(str(row["query"]))
        if "cached_response" in row:
            raw_values.add(str(row["cached_response"]))
    for raw in raw_values:
        if not raw:
            continue
        folded = raw.casefold()
        for value in emitted:
            if value == raw:
                return False
            if len(raw) >= 6 and folded in value.casefold():
                return False
    return True


def _first_event(
    trace: Sequence[Mapping[str, Any]], stage: str
) -> Mapping[str, Any] | None:
    for event in trace:
        if isinstance(event, Mapping) and event.get("stage") == stage:
            return event
    return None


def _first_served_event(
    trace: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    for event in trace:
        if not isinstance(event, Mapping):
            continue
        stage = str(event.get("stage", ""))
        if stage not in RESOLUTION_STAGES:
            continue
        if stage == "orchestrator_llm_fallback":
            if event.get("answered") is not False:
                return event
        elif event.get("answered") is True:
            return event
    return None


def _is_refusal(result: Any) -> bool:
    return "_REFUSED" in str(getattr(result, "response", "")) or "refus" in str(
        getattr(result, "source", "")
    ).lower()


def _first_hop_solver(stage: str, event: Mapping[str, Any]) -> str | None:
    if stage == "deterministic_solver":
        intent = event.get("intent")
        if isinstance(intent, str) and _TOKEN_RE.fullmatch(intent):
            return intent
        return "deterministic_solver"
    if stage == "hybrid_retrieval_8_cell":
        return "hybrid_retrieval"
    if stage == "hex_neighbor_assist_7_cell":
        return "hex_neighbor_assist"
    if stage == "orchestrator_llm_fallback":
        # The selected route is admission evidence, not proof of who served.
        # Orchestrator may miss a micromodel and answer with the LLM, or execute
        # a memory route through swarm agents. Attribute the observed source.
        source = event.get("source")
        return {
            "llm": "orchestrator_llm",
            "memory": "memory",
            "micromodel": "micromodel",
            "swarm": "swarm",
        }.get(source)
    return None


def _gap_record(
    row: Mapping[str, Any],
    emitted_at_seq: int,
    gap_reason: str,
    run_header: Mapping[str, Any],
) -> dict[str, Any]:
    gap: dict[str, Any] = {
        "id": _opaque_row_id(str(row["id"])),
        "query_digest": _query_digest(str(row["query"])),
        "normalization_version": NORMALIZATION_VERSION,
        "first_hop_class": "gap",
        "gap_reason": gap_reason,
        "emitted_at_seq": emitted_at_seq,
    }
    gap["candidate_receipt_ref"] = _candidate_ref(gap, run_header, kind="gap")
    return gap


def _classify_result(
    result: Any,
    emitted_at_seq: int,
    row: Mapping[str, Any],
    run_header: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    trace = list(getattr(result, "route_stage_trace", None) or [])
    served = _first_served_event(trace)
    if served is None:
        return {
            "gap": _gap_record(
                row, emitted_at_seq, "missing_served_route_stage", run_header
            )
        }

    route_selection = _first_event(trace, "route_selection") or {}
    first_stage = str(served["stage"])
    fallback_used = first_stage != "deterministic_solver"
    if _is_refusal(result):
        route_decision = "refused"
        first_hop_class = "refused"
        refusal_reason: str | None = "solver_or_policy_refusal"
    elif first_stage == "deterministic_solver":
        route_decision = "solver_first"
        first_hop_class = "authoritative"
        refusal_reason = None
    else:
        route_decision = "fallback"
        refusal_reason = None
        if first_stage in {
            "hybrid_retrieval_8_cell",
            "hex_neighbor_assist_7_cell",
        } and served.get("authoritative") is True:
            first_hop_class = "authoritative"
        elif route_selection.get("route_type") == "solver":
            first_hop_class = "fallback"
        else:
            first_hop_class = "heuristic"

    first_hop_solver = (
        None
        if first_hop_class == "refused"
        else _first_hop_solver(first_stage, served)
    )
    record: dict[str, Any] = {
        "id": _opaque_row_id(str(row["id"])),
        "query_digest": _query_digest(str(row["query"])),
        "normalization_version": NORMALIZATION_VERSION,
        "route_decision": route_decision,
        "first_hop_class": first_hop_class,
        "first_hop_stage": first_stage,
        "first_hop_solver": first_hop_solver,
        "refusal_reason": refusal_reason,
        "fallback_used": fallback_used,
        "emitted_at_seq": emitted_at_seq,
    }
    record["candidate_receipt_ref"] = _candidate_ref(
        record, run_header, kind="record"
    )
    return {"record": record}


async def _run_rows(
    corpus: Sequence[Mapping[str, Any]], run_header: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    cached = {
        str(row["query"]): str(row["cached_response"])
        for row in corpus
        if "cached_response" in row
    }
    service = ChatService(
        orchestrator=_StaticOrchestrator(),
        memory_service=_StaticMemoryService(),
        hot_cache=_StaticHotCache(cached),
        routing_policy_fn=select_route,
        config=_StaticConfig(),
    )
    records: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    cached_count = 0

    for emitted_at_seq, row in enumerate(corpus, start=1):
        try:
            result = await service.handle(
                ChatRequest(
                    query=str(row["query"]),
                    profile=str(row["profile"]),
                    language=str(row["language"]),
                )
            )
            if result.cached:
                cached_count += 1
                continue
            classified = _classify_result(
                result, emitted_at_seq, row, run_header
            )
        except Exception:  # noqa: BLE001 - every failed row remains in the denominator
            gaps.append(
                _gap_record(row, emitted_at_seq, "harness_exception", run_header)
            )
            continue
        if "gap" in classified:
            gaps.append(classified["gap"])
        else:
            records.append(classified["record"])

    return records, gaps, cached_count


def _valid_sequence(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _runner_output_shape_safe(
    records: Any, gaps: Any, cached_count: Any
) -> bool:
    return bool(
        isinstance(records, list)
        and isinstance(gaps, list)
        and isinstance(cached_count, int)
        and not isinstance(cached_count, bool)
        and cached_count >= 0
        and all(
            isinstance(record, Mapping) and set(record) == SAFE_RECORD_KEYS
            for record in records
        )
        and all(
            isinstance(gap, Mapping) and set(gap) == SAFE_GAP_RECORD_KEYS
            for gap in gaps
        )
    )


def _expected_bindings(
    corpus: Sequence[Mapping[str, Any]],
) -> dict[str, tuple[str, int]]:
    return {
        _query_digest(str(row["query"])): (
            _opaque_row_id(str(row["id"])),
            emitted_at_seq,
        )
        for emitted_at_seq, row in enumerate(corpus, start=1)
        if "cached_response" not in row
    }


def _validate_records(
    records: Sequence[Mapping[str, Any]],
    gaps: Sequence[Mapping[str, Any]],
    run_header: Mapping[str, Any],
    expected_bindings: Mapping[str, tuple[str, int]],
    *,
    require_complete: bool = True,
) -> bool:
    if not _validate_run_header(run_header):
        return False
    identities: set[str] = set()
    query_digests: set[str] = set()
    candidate_refs: set[str] = set()
    sequences: set[int] = set()
    prior_record_sequence = 0

    for record in records:
        if set(record) != SAFE_RECORD_KEYS:
            return False
        row_id = str(record.get("id", ""))
        query_digest = str(record.get("query_digest", ""))
        candidate_ref = str(record.get("candidate_receipt_ref", ""))
        if not all(
            _DIGEST_RE.fullmatch(value)
            for value in (row_id, query_digest, candidate_ref)
        ):
            return False
        if not _valid_sequence(record.get("emitted_at_seq")):
            return False
        sequence = int(record["emitted_at_seq"])
        if sequence <= prior_record_sequence or sequence in sequences:
            return False
        prior_record_sequence = sequence
        if record.get("route_decision") not in ROUTE_DECISIONS:
            return False
        if record.get("normalization_version") != NORMALIZATION_VERSION:
            return False
        if record.get("first_hop_class") not in FIRST_HOP_CLASSES - {"gap"}:
            return False
        stage = record.get("first_hop_stage")
        if stage not in RESOLUTION_STAGES:
            return False
        solver = record.get("first_hop_solver")
        if solver is not None and (
            not isinstance(solver, str) or not _TOKEN_RE.fullmatch(solver)
        ):
            return False
        if not isinstance(record.get("fallback_used"), bool):
            return False
        expected_binding = expected_bindings.get(query_digest)
        if expected_binding != (row_id, sequence):
            return False

        route = record["route_decision"]
        first_class = record["first_hop_class"]
        refusal_reason = record["refusal_reason"]
        if (first_class == "refused") is not (solver is None):
            return False
        if route == "solver_first":
            if not (
                stage == "deterministic_solver"
                and first_class == "authoritative"
                and record["fallback_used"] is False
                and refusal_reason is None
            ):
                return False
        elif route == "refused":
            if not (
                first_class == "refused"
                and refusal_reason == "solver_or_policy_refusal"
                and record["fallback_used"] is (stage != "deterministic_solver")
            ):
                return False
        elif not (
            stage != "deterministic_solver"
            and first_class in {"authoritative", "heuristic", "fallback"}
            and record["fallback_used"] is True
            and refusal_reason is None
        ):
            return False
        if candidate_ref != _candidate_ref(record, run_header, kind="record"):
            return False
        if (
            row_id in identities
            or query_digest in query_digests
            or candidate_ref in candidate_refs
        ):
            return False
        identities.add(row_id)
        query_digests.add(query_digest)
        candidate_refs.add(candidate_ref)
        sequences.add(sequence)

    prior_gap_sequence = 0
    for gap in gaps:
        if set(gap) != SAFE_GAP_RECORD_KEYS:
            return False
        row_id = str(gap.get("id", ""))
        query_digest = str(gap.get("query_digest", ""))
        candidate_ref = str(gap.get("candidate_receipt_ref", ""))
        if not all(
            _DIGEST_RE.fullmatch(value)
            for value in (row_id, query_digest, candidate_ref)
        ):
            return False
        if gap.get("first_hop_class") != "gap":
            return False
        if gap.get("normalization_version") != NORMALIZATION_VERSION:
            return False
        if gap.get("gap_reason") not in GAP_REASONS:
            return False
        if not _valid_sequence(gap.get("emitted_at_seq")):
            return False
        sequence = int(gap["emitted_at_seq"])
        if sequence <= prior_gap_sequence or sequence in sequences:
            return False
        prior_gap_sequence = sequence
        expected_binding = expected_bindings.get(query_digest)
        if expected_binding != (row_id, sequence):
            return False
        if candidate_ref != _candidate_ref(gap, run_header, kind="gap"):
            return False
        if (
            row_id in identities
            or query_digest in query_digests
            or candidate_ref in candidate_refs
        ):
            return False
        identities.add(row_id)
        query_digests.add(query_digest)
        candidate_refs.add(candidate_ref)
        sequences.add(sequence)
    return not require_complete or query_digests == set(expected_bindings)


def _fail_closed_report(
    input_row_count: int, code: str, *, corpus_valid: bool
) -> dict[str, Any]:
    return {
        "report_version": REPORT_VERSION,
        "measurement_scope": MEASUREMENT_SCOPE,
        "denominator_scope": DENOMINATOR_SCOPE,
        "representativeness_scope": REPRESENTATIVENESS_SCOPE,
        "production_representative": False,
        "measurement_not_a_correctness_gate": True,
        "header": None,
        "input_row_count": input_row_count,
        "served_query_count": 0,
        "cached_count": 0,
        "non_cached_served_first_hop_count": 0,
        "measurement_available": False,
        "first_hop_coverage_available": False,
        "first_hop_counts": {
            "authoritative": 0,
            "heuristic": 0,
            "fallback": 0,
            "refused": 0,
            "gap": 0,
        },
        "route_decision_counts": {"solver_first": 0, "fallback": 0, "refused": 0},
        "first_hop_records": [],
        "gap_records": [],
        "validation_errors": [code],
        "claim_safe": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
        "invariants": {
            "corpus_valid": corpus_valid,
            "header_valid": False,
            "raw_query_not_emitted": True,
            "records_allowlisted": True,
            "counts_sum_to_denominator": True,
            "served_query_conservation": False,
            "complete_query_bijection": False,
            "cached_rows_accounted_for": False,
            "corpus_cardinality_preserved": False,
            "measurement_only_no_claim_safe": True,
            "runtime_authority_not_granted": True,
        },
        "ok": False,
    }


def diagnose(
    corpus: Sequence[Any] | None = None,
    *,
    expected_head_commit_sha: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    input_rows = list(DEFAULT_CORPUS if corpus is None else corpus)
    try:
        validated_rows = _validate_corpus(input_rows)
    except CorpusValidationError as exc:
        return _fail_closed_report(len(input_rows), exc.code, corpus_valid=False)
    rows = tuple(dict(row) for row in validated_rows)
    try:
        run_header = _build_run_header(
            rows,
            expected_head_commit_sha=expected_head_commit_sha,
            run_id=run_id,
        )
    except HeaderValidationError as exc:
        return _fail_closed_report(len(input_rows), exc.code, corpus_valid=True)

    try:
        records, gaps, cached_count = asyncio.run(
            _run_rows([dict(row) for row in rows], dict(run_header))
        )
    except Exception:  # noqa: BLE001 - never expose harness exception details
        return _fail_closed_report(
            len(input_rows), "harness_execution_failed", corpus_valid=True
        )
    if not _runner_output_shape_safe(records, gaps, cached_count):
        return _fail_closed_report(
            len(input_rows), "invalid_harness_output", corpus_valid=True
        )
    try:
        post_run_head = _current_head_commit_sha()
    except HeaderValidationError:
        return _fail_closed_report(
            len(input_rows), "measurement_context_changed", corpus_valid=True
        )
    if (
        post_run_head != run_header["head_commit_sha"]
        or _corpus_digest(rows) != run_header["corpus_digest"]
    ):
        return _fail_closed_report(
            len(input_rows), "measurement_context_changed", corpus_valid=True
        )
    expected_bindings = _expected_bindings(rows)
    if not _validate_records(
        records,
        gaps,
        run_header,
        expected_bindings,
        require_complete=False,
    ):
        return _fail_closed_report(
            len(input_rows), "invalid_harness_output", corpus_valid=True
        )
    class_counts = Counter(str(record["first_hop_class"]) for record in records)
    class_counts.update(str(gap["first_hop_class"]) for gap in gaps)
    route_counts = Counter(str(record["route_decision"]) for record in records)
    expected_query_digests = {
        _query_digest(str(row["query"]))
        for row in rows
        if "cached_response" not in row
    }
    measured_query_digests = [
        str(entry.get("query_digest", "")) for entry in (*records, *gaps)
    ]
    denominator = len(expected_query_digests)
    measured_count = len(measured_query_digests)
    records_valid = _validate_records(
        records, gaps, run_header, expected_bindings
    )
    header_valid = _validate_run_header(run_header)
    complete_query_bijection = bool(
        denominator > 0
        and measured_count == denominator
        and set(measured_query_digests) == expected_query_digests
    )
    cached_rows_accounted_for = cached_count == len(rows) - denominator
    first_hop_coverage_available = bool(
        header_valid
        and records_valid
        and complete_query_bijection
        and cached_rows_accounted_for
    )

    report: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "measurement_scope": MEASUREMENT_SCOPE,
        "denominator_scope": DENOMINATOR_SCOPE,
        "representativeness_scope": REPRESENTATIVENESS_SCOPE,
        "production_representative": False,
        "measurement_not_a_correctness_gate": True,
        "header": run_header,
        "input_row_count": len(input_rows),
        "served_query_count": len(rows),
        "cached_count": cached_count,
        "non_cached_served_first_hop_count": denominator,
        "measurement_available": first_hop_coverage_available,
        "first_hop_coverage_available": first_hop_coverage_available,
        "first_hop_counts": {
            "authoritative": class_counts.get("authoritative", 0),
            "heuristic": class_counts.get("heuristic", 0),
            "fallback": class_counts.get("fallback", 0),
            "refused": class_counts.get("refused", 0),
            "gap": class_counts.get("gap", 0),
        },
        "route_decision_counts": {
            "solver_first": route_counts.get("solver_first", 0),
            "fallback": route_counts.get("fallback", 0),
            "refused": route_counts.get("refused", 0),
        },
        "first_hop_records": records,
        "gap_records": gaps,
        "validation_errors": [],
        "claim_safe": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
    }
    cardinality_preserved = len(rows) == cached_count + measured_count
    report["invariants"] = {
        "corpus_valid": True,
        "header_valid": header_valid,
        "raw_query_not_emitted": _raw_inputs_not_emitted(report, rows),
        "records_allowlisted": records_valid,
        "counts_sum_to_denominator": sum(report["first_hop_counts"].values())
        == denominator,
        "served_query_conservation": (
            len(report["first_hop_records"])
            + len(report["gap_records"])
            + report["cached_count"]
            == report["served_query_count"]
        ),
        "complete_query_bijection": complete_query_bijection,
        "cached_rows_accounted_for": cached_rows_accounted_for,
        "corpus_cardinality_preserved": cardinality_preserved,
        "measurement_only_no_claim_safe": report["claim_safe"] is False,
        "runtime_authority_not_granted": report["runtime_authority_granted"] is False,
    }
    report["ok"] = bool(
        report["first_hop_coverage_available"]
        and all(report["invariants"].values())
        and report["measurement_not_a_correctness_gate"] is True
        and report["production_representative"] is False
        and report["first_hop_counts"]["gap"] == 0
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, help="Optional JSON list corpus.")
    parser.add_argument(
        "--head-commit-sha",
        help="Expected full 40-character measurement head.",
    )
    parser.add_argument("--run-id", help="Optional opaque run identifier.")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        corpus = load_corpus(args.corpus) if args.corpus else None
    except (OSError, UnicodeError, json.JSONDecodeError, CorpusValidationError):
        payload = {"ok": False, "error": "corpus_load_failed"}
        print(
            json.dumps(payload, sort_keys=True)
            if args.json
            else "ok=False error=corpus_load_failed"
        )
        return 2
    report = diagnose(
        corpus,
        expected_head_commit_sha=args.head_commit_sha,
        run_id=args.run_id,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            "ok={ok} non_cached={n} solver_first={s} fallback={f} "
            "heuristic={h} refused={r} gap={g}".format(
                ok=report["ok"],
                n=report["non_cached_served_first_hop_count"],
                s=report["route_decision_counts"]["solver_first"],
                f=report["first_hop_counts"]["fallback"],
                h=report["first_hop_counts"]["heuristic"],
                r=report["first_hop_counts"]["refused"],
                g=report["first_hop_counts"]["gap"],
            )
        )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
