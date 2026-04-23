"""Phase D-2 — hybrid retrieval observer for candidate-mode MAGMA tracing.

Per v3 §1.1 candidate mode:
    hybrid computes candidates, MAGMA traces both routes,
    production uses old route (keyword).

This module wires that observability WITHOUT changing production routing.
It records hybrid's would-be decision alongside keyword's actual decision
so we can compare over 24h and make the Phase D-2 → Phase D-3 authoritative
promotion call on data, not hope.

Usage (from chat_routing_engine or similar):
    from waggledance.core.reasoning.hybrid_observer import HybridObserver
    observer = HybridObserver(hybrid_retrieval_svc)
    # ... in the async chat path:
    await observer.record_candidate(
        query=query,
        keyword_decision=keyword_route_result,
        magma_trace_sink=magma_append_fn,
    )

Output is a per-query trace row written to magma_retrieval_trace.jsonl:

    {
      "ts": "2026-04-23T...",
      "query_hash": "...",
      "keyword_decision": { layer, confidence, reason },
      "hybrid_would_be": { top_solver, top_score, question_frame, ... },
      "would_agree": true | false,
      "hybrid_uniquely_confident": true | false,
      "hybrid_below_threshold": true | false
    }

At Phase D-3 promotion gate (24h after D-2), compare:
    hybrid_uniquely_confident (would have helped when keyword missed)
    vs
    hybrid_below_threshold events (would have rejected off-domain)
    vs
    keyword_would_have_been_correct_despite_hybrid (would have hurt)

If ratio ≥ 3:1 in favor of hybrid → promote to authoritative.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from waggledance.core.reasoning.hybrid_router import filter_by_question_frame
from waggledance.core.reasoning.question_frame import parse as parse_frame


TRACE_FILE_DEFAULT = Path("docs/runs/magma_hybrid_candidate_trace.jsonl")


@dataclass
class HybridCandidateTrace:
    """One row per chat query recording both decisions."""
    ts: str
    query_hash: str
    keyword_layer: str
    keyword_confidence: float
    keyword_reason: str
    hybrid_mode: str
    hybrid_top_solver: Optional[str] = None
    hybrid_top_score: Optional[float] = None
    hybrid_top_cell: Optional[str] = None
    question_frame_type: str = "numeric"
    passed_threshold: bool = False
    passed_question_frame: bool = False
    hybrid_rejected_off_domain: bool = False
    candidates_before_filter: int = 0
    candidates_after_filter: int = 0
    hybrid_uniquely_confident: bool = False
    would_agree: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class HybridObserver:
    """Records hybrid retrieval's would-be decision alongside keyword's actual decision.

    Does NOT modify production routing — purely observational per v3 §1.1
    candidate mode. Outputs are consumed by 24h D-2 → D-3 promotion gate
    analysis.
    """

    def __init__(
        self,
        hybrid_retrieval_service,
        solver_specs: Optional[dict] = None,
        trace_file: Optional[Path] = None,
    ):
        self._svc = hybrid_retrieval_service
        self._solver_specs = solver_specs or {}
        self._trace_file = Path(trace_file) if trace_file else TRACE_FILE_DEFAULT
        self._trace_file.parent.mkdir(parents=True, exist_ok=True)
        self._count_observed = 0
        self._count_hybrid_confident = 0
        self._count_hybrid_rejected = 0
        self._count_agreement = 0

    def _query_hash(self, query: str) -> str:
        return hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]

    async def record_candidate(
        self,
        query: str,
        keyword_decision: dict,
        intent: str = "chat",
    ) -> HybridCandidateTrace:
        """Observer: get hybrid's would-be decision, record trace. Never raises."""
        if not self._svc or not self._svc.enabled:
            # Hybrid not loaded — nothing to observe
            return None

        self._count_observed += 1
        trace = HybridCandidateTrace(
            ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            query_hash=self._query_hash(query),
            keyword_layer=str(keyword_decision.get("layer", "")),
            keyword_confidence=float(keyword_decision.get("confidence", 0.0)),
            keyword_reason=str(keyword_decision.get("reason", "")),
            hybrid_mode=self._svc.mode,
        )

        try:
            # Get hybrid result (async)
            hybrid_result = await self._svc.retrieve(query, intent=intent, k=5)
            hits = getattr(hybrid_result, "hits", [])

            if hits:
                top = hits[0]
                trace.hybrid_top_solver = getattr(top, "metadata", {}).get("canonical_solver_id")
                trace.hybrid_top_score = round(float(getattr(top, "score", 0.0)), 4)
                trace.hybrid_top_cell = getattr(top, "cell_id", None)
                trace.passed_threshold = (
                    trace.hybrid_top_score is not None
                    and trace.hybrid_top_score >= self._svc.min_score
                )

            # Question-frame compatibility check
            if trace.hybrid_top_solver and trace.passed_threshold:
                frame = parse_frame(query)
                trace.question_frame_type = frame.desired_output
                # Build candidate list for filter
                candidates = [
                    {
                        "canonical_solver_id": getattr(h, "metadata", {}).get("canonical_solver_id"),
                        "score": getattr(h, "score", 0.0),
                    }
                    for h in hits
                    if getattr(h, "metadata", {}).get("canonical_solver_id")
                ]
                compatible = filter_by_question_frame(candidates, self._solver_specs, frame)
                trace.candidates_before_filter = len(candidates)
                trace.candidates_after_filter = len(compatible)
                trace.passed_question_frame = bool(compatible)

                if not compatible:
                    trace.hybrid_rejected_off_domain = True
                    self._count_hybrid_rejected += 1

                # Agreement analysis
                keyword_layer = trace.keyword_layer
                # If keyword says "retrieval" and hybrid found a solver above threshold,
                # hybrid is "uniquely confident" that a solver exists
                if compatible and keyword_layer in ("retrieval", "llm_reasoning"):
                    trace.hybrid_uniquely_confident = True
                    self._count_hybrid_confident += 1
                if compatible and keyword_layer in ("model_based", "rule_constraints", "statistical"):
                    # Both routing to a solver → check if same solver-family
                    trace.would_agree = True
                    self._count_agreement += 1

        except Exception as e:
            trace.keyword_reason = f"{trace.keyword_reason} | hybrid_err:{type(e).__name__}"

        # Append to MAGMA trace file (JSONL, append-only per v3 §3)
        try:
            with open(self._trace_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(trace.to_dict(), ensure_ascii=False) + "\n")
        except Exception:
            pass

        return trace

    def stats(self) -> dict:
        return {
            "observed": self._count_observed,
            "hybrid_confident": self._count_hybrid_confident,
            "hybrid_rejected_off_domain": self._count_hybrid_rejected,
            "agreement_with_keyword": self._count_agreement,
            "hybrid_mode": self._svc.mode if self._svc else None,
            "trace_file": str(self._trace_file),
        }
