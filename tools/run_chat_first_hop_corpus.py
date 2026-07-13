# SPDX-License-Identifier: BUSL-1.1
"""Representative ChatService first-hop corpus measurement.

W1B measurement harness for the 2026-07 image-targeted sprint. It runs corpus
rows through the production ChatService route order with deterministic fakes for
external dependencies, then emits only W1A-compatible digests, enums, ids, and
counts. It does not write receipts, grant runtime authority, or flip claim_safe.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import subprocess
import sys
import unicodedata
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
from waggledance.core.orchestration.routing_policy import select_route  # noqa: E402

REPORT_VERSION = "wd.chat_first_hop_corpus.v1"
SCHEMA_VERSION = "wd.chat_query_route_evidence.v1"
QUERY_NORMALIZATION_VERSION = "wd.chat_query_normalization.v1"
QUERY_DIGEST_DOMAIN = b"waggledance.chat_query_digest"
CORPUS_DIGEST_DOMAIN = "waggledance.chat_first_hop_corpus"
RUN_ID_DOMAIN = "waggledance.chat_first_hop_measurement_run"
DENOMINATOR_SCOPE = "all_non_cached_served_first_hops"
MEASUREMENT_SCOPE = "chatservice_handle_route_stage_trace_with_deterministic_fakes"
ROUTE_DECISIONS = frozenset({"solver_first", "fallback", "refused"})
FIRST_HOP_CLASSES = frozenset(
    {"authoritative", "heuristic", "fallback", "refused", "gap"}
)
GAP_REASONS = frozenset({"harness_exception", "missing_route_stage_trace"})
SAFE_RECORD_KEYS = frozenset(
    {
        "id",
        "query_digest",
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
        "first_hop_class",
        "gap_reason",
        "candidate_receipt_ref",
        "emitted_at_seq",
    }
)
RESOLUTION_STAGES = (
    "deterministic_solver",
    "hybrid_retrieval_8_cell",
    "hex_neighbor_assist_7_cell",
    "orchestrator_llm_fallback",
)

DEFAULT_CORPUS: tuple[dict[str, Any], ...] = (
    {
        "id": "solver_math_percent",
        "query": "what is 15% of 300",
        "profile": "HOME",
    },
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


def load_corpus(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("corpus JSON must be a list")
    return [dict(item) for item in data if isinstance(item, dict)]


def _normalize_query(query: str) -> str:
    normalized = unicodedata.normalize("NFC", str(query))
    return re.sub(r"\s+", " ", normalized).strip()


def _query_digest(query: str) -> str:
    payload = b"\x00".join(
        (
            QUERY_DIGEST_DOMAIN,
            QUERY_NORMALIZATION_VERSION.encode("utf-8"),
            _normalize_query(query).encode("utf-8"),
        )
    )
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _corpus_digest(corpus: Sequence[Mapping[str, Any]]) -> str:
    rows = [
        {
            "id": str(row.get("id", "")),
            "query_digest": _query_digest(str(row.get("query", ""))),
            "profile": str(row.get("profile", "HOME")),
            "language": str(row.get("language", "auto")),
            "cached_response_present": row.get("cached_response") is not None,
        }
        for row in corpus
    ]
    return sha256_digest(
        {
            "domain": CORPUS_DIGEST_DOMAIN,
            "schema_version": SCHEMA_VERSION,
            "normalization_version": QUERY_NORMALIZATION_VERSION,
            "rows": rows,
        }
    )


def _head_commit_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return result.stdout.strip().lower()


def _run_id(head_commit_sha: str, corpus_digest: str) -> str:
    return sha256_digest(
        {
            "domain": RUN_ID_DOMAIN,
            "head_commit_sha": head_commit_sha,
            "corpus_digest": corpus_digest,
            "schema_version": SCHEMA_VERSION,
            "normalization_version": QUERY_NORMALIZATION_VERSION,
        }
    )


def _measurement_header_is_bound(header: Mapping[str, Any]) -> bool:
    digest_re = re.compile(r"^sha256:[a-f0-9]{64}$")
    head_commit_sha = str(header.get("head_commit_sha", ""))
    corpus_digest = str(header.get("corpus_digest", ""))
    return bool(
        set(header)
        == {
            "head_commit_sha",
            "corpus_digest",
            "schema_version",
            "normalization_version",
            "run_id",
        }
        and re.fullmatch(r"[a-f0-9]{40}", head_commit_sha)
        and digest_re.fullmatch(corpus_digest)
        and header.get("schema_version") == SCHEMA_VERSION
        and header.get("normalization_version") == QUERY_NORMALIZATION_VERSION
        and header.get("run_id") == _run_id(head_commit_sha, corpus_digest)
    )


def _candidate_ref(record: Mapping[str, Any]) -> str:
    return sha256_digest(
        {
            "route_evidence": {
                key: record.get(key)
                for key in (
                    "id",
                    "query_digest",
                    "route_decision",
                    "first_hop_class",
                    "first_hop_stage",
                    "first_hop_solver",
                    "fallback_used",
                    "emitted_at_seq",
                )
            }
        }
    )


def _raw_query_markers(raw: str) -> set[str]:
    text = re.sub(r"\s+", " ", str(raw)).strip().lower()
    markers: set[str] = set()
    if len(text) >= 12:
        markers.add(text)
    for token in re.findall(r"[a-z0-9_+*.-]{6,}", text):
        if any(ch.isdigit() for ch in token) or any(ch in "+*.-_" for ch in token):
            markers.add(token)
    return markers


def _raw_query_not_emitted(report: Mapping[str, Any], raw_queries: Iterable[str]) -> bool:
    blob = json.dumps(report, sort_keys=True).lower()
    for query in raw_queries:
        for marker in _raw_query_markers(query):
            if marker and marker in blob:
                return False
    return True


def _first_event(
    trace: Sequence[Mapping[str, Any]],
    stage: str,
) -> Mapping[str, Any] | None:
    for event in trace:
        if event.get("stage") == stage:
            return event
    return None


def _first_resolution_stage(trace: Sequence[Mapping[str, Any]]) -> str | None:
    for event in trace:
        stage = str(event.get("stage", ""))
        if stage in RESOLUTION_STAGES:
            return stage
    return None


def _is_refusal(result: Any) -> bool:
    return "_REFUSED" in str(getattr(result, "response", "")) or "refus" in str(
        getattr(result, "source", "")
    ).lower()


def _classify_result(result: Any, emitted_at_seq: int, row: Mapping[str, Any]) -> dict:
    query = str(row["query"])
    trace = list(getattr(result, "route_stage_trace", None) or [])
    if not trace:
        gap = {
            "id": str(row["id"]),
            "query_digest": _query_digest(query),
            "first_hop_class": "gap",
            "gap_reason": "missing_route_stage_trace",
            "emitted_at_seq": emitted_at_seq,
        }
        gap["candidate_receipt_ref"] = _candidate_ref(gap)
        return {"gap": gap}

    route_selection = _first_event(trace, "route_selection") or {}
    deterministic = _first_event(trace, "deterministic_solver") or {}
    first_stage = _first_resolution_stage(trace)
    first_hop_solver = deterministic.get("intent") or route_selection.get(
        "solver_intent"
    )
    deterministic_answered = bool(deterministic.get("answered"))

    if _is_refusal(result):
        route_decision = "refused"
        first_hop_class = "refused"
        refusal_reason = "solver_or_policy_refusal"
        fallback_used = False
    elif (
        first_stage == "deterministic_solver"
        and deterministic_answered
        and getattr(result, "source", "") == "solver"
    ):
        route_decision = "solver_first"
        first_hop_class = "authoritative"
        refusal_reason = None
        fallback_used = False
    elif route_selection.get("route_type") == "solver":
        route_decision = "fallback"
        first_hop_class = "fallback"
        refusal_reason = None
        fallback_used = True
    else:
        route_decision = "fallback"
        first_hop_class = "heuristic"
        refusal_reason = None
        fallback_used = True
        first_hop_solver = None

    record = {
        "id": str(row["id"]),
        "query_digest": _query_digest(query),
        "route_decision": route_decision,
        "first_hop_class": first_hop_class,
        "first_hop_stage": first_stage,
        "first_hop_solver": first_hop_solver,
        "refusal_reason": refusal_reason,
        "fallback_used": fallback_used,
        "emitted_at_seq": emitted_at_seq,
    }
    record["candidate_receipt_ref"] = _candidate_ref(record)
    return {"record": record}


async def _run_rows(corpus: Sequence[Mapping[str, Any]]) -> tuple[list[dict], list[dict], int]:
    cached = {
        str(row["query"]): str(row["cached_response"])
        for row in corpus
        if row.get("query") and row.get("cached_response") is not None
    }
    service = ChatService(
        orchestrator=_StaticOrchestrator(),
        memory_service=_StaticMemoryService(),
        hot_cache=_StaticHotCache(cached),
        routing_policy_fn=select_route,
        config=_StaticConfig(),
    )
    records: list[dict] = []
    gaps: list[dict] = []
    cached_count = 0

    for emitted_at_seq, row in enumerate(corpus, start=1):
        if not row.get("id") or not row.get("query"):
            continue
        query = str(row["query"])
        try:
            result = await service.handle(
                ChatRequest(
                    query=query,
                    profile=str(row.get("profile", "HOME")),
                    language=str(row.get("language", "auto")),
                )
            )
        except Exception as exc:  # noqa: BLE001 - harness gaps are measured
            gap = {
                "id": str(row["id"]),
                "query_digest": _query_digest(query),
                "first_hop_class": "gap",
                "gap_reason": "harness_exception",
                "candidate_receipt_ref": sha256_digest(
                    {
                        "gap": {
                            "query_digest": _query_digest(query),
                            "reason": exc.__class__.__name__,
                            "emitted_at_seq": emitted_at_seq,
                        }
                    }
                ),
                "emitted_at_seq": emitted_at_seq,
            }
            gaps.append(gap)
            continue

        if result.cached:
            cached_count += 1
            continue
        classified = _classify_result(result, emitted_at_seq, row)
        if "gap" in classified:
            gaps.append(classified["gap"])
        else:
            records.append(classified["record"])

    return records, gaps, cached_count


def _validate_records(records: Sequence[Mapping[str, Any]], gaps: Sequence[Mapping[str, Any]]) -> bool:
    digest_re = re.compile(r"^sha256:[a-f0-9]{64}$")
    for record in records:
        if set(record) - SAFE_RECORD_KEYS:
            return False
        if record.get("route_decision") not in ROUTE_DECISIONS:
            return False
        if record.get("first_hop_class") not in FIRST_HOP_CLASSES:
            return False
        if not digest_re.match(str(record.get("query_digest", ""))):
            return False
        if not digest_re.match(str(record.get("candidate_receipt_ref", ""))):
            return False
    for gap in gaps:
        if set(gap) - SAFE_GAP_RECORD_KEYS:
            return False
        if gap.get("first_hop_class") != "gap":
            return False
        if gap.get("gap_reason") not in GAP_REASONS:
            return False
        if not digest_re.match(str(gap.get("query_digest", ""))):
            return False
        if not digest_re.match(str(gap.get("candidate_receipt_ref", ""))):
            return False
    return True


def diagnose(corpus: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    rows = list(DEFAULT_CORPUS if corpus is None else corpus)
    head_commit_sha = _head_commit_sha()
    corpus_digest = _corpus_digest(rows)
    measurement_run = {
        "head_commit_sha": head_commit_sha,
        "corpus_digest": corpus_digest,
        "schema_version": SCHEMA_VERSION,
        "normalization_version": QUERY_NORMALIZATION_VERSION,
        "run_id": _run_id(head_commit_sha, corpus_digest),
    }
    records, gaps, cached_count = asyncio.run(_run_rows(rows))
    class_counts = Counter(str(record["first_hop_class"]) for record in records)
    class_counts.update(str(gap["first_hop_class"]) for gap in gaps)
    route_counts = Counter(str(record["route_decision"]) for record in records)
    denominator = len(records) + len(gaps)

    report: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "measurement_run": measurement_run,
        "measurement_scope": MEASUREMENT_SCOPE,
        "measurement_not_a_correctness_gate": True,
        "denominator_scope": DENOMINATOR_SCOPE,
        "served_query_count": len([row for row in rows if row.get("id") and row.get("query")]),
        "cached_count": cached_count,
        "non_cached_served_first_hop_count": denominator,
        "measurement_available": denominator > 0,
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
        "claim_safe": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
    }
    raw_safe = _raw_query_not_emitted(
        report, [str(row.get("query", "")) for row in rows]
    )
    report["invariants"] = {
        "raw_query_not_emitted": raw_safe,
        "records_allowlisted": _validate_records(records, gaps),
        "counts_sum_to_denominator": sum(report["first_hop_counts"].values())
        == denominator,
        "measurement_only_no_claim_safe": report["claim_safe"] is False,
        "measurement_header_bound": _measurement_header_is_bound(measurement_run),
        "measurement_not_a_correctness_gate": report[
            "measurement_not_a_correctness_gate"
        ]
        is True,
        "runtime_authority_not_granted": report["runtime_authority_granted"] is False,
    }
    report["ok"] = bool(
        report["measurement_available"]
        and report["invariants"]["raw_query_not_emitted"]
        and report["invariants"]["records_allowlisted"]
        and report["invariants"]["counts_sum_to_denominator"]
        and report["invariants"]["measurement_header_bound"]
        and report["invariants"]["measurement_not_a_correctness_gate"]
        and report["first_hop_counts"]["gap"] == 0
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, help="Optional JSON list corpus.")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    corpus = load_corpus(args.corpus) if args.corpus else None
    report = diagnose(corpus)
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
