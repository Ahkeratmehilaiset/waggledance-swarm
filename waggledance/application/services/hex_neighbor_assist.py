"""Hex Neighbor Assist — local→neighbor→global→LLM resolution coordinator.

Feature-flagged: does nothing when hex_mesh.enabled=false.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import time
import uuid
import threading
from dataclasses import dataclass, field
from typing import Any

from waggledance.core.domain.hex_mesh import (
    HexNeighborRequest,
    HexNeighborResponse,
    HexResolutionTrace,
)

log = logging.getLogger(__name__)


@dataclass
class HexMeshMetrics:
    """Counters exposed via /api/status."""

    origin_cell_resolutions: int = 0
    local_only_resolutions: int = 0
    neighbor_assist_resolutions: int = 0
    global_escalations: int = 0
    llm_last_resolutions: int = 0
    completed_hex_neighbor_batches: int = 0
    neighbors_consulted_total: int = 0
    quarantined_cells: int = 0
    self_heal_events: int = 0
    magma_traces_written: int = 0
    ttl_exhaustions: int = 0
    # v3.5.6: efficiency counters
    skipped_local_attempts: int = 0
    skipped_neighbor_attempts: int = 0
    budget_exhaustions: int = 0
    preflight_skips: int = 0
    preflight_passes: int = 0


@dataclass
class _AdaptiveSuccessMemory:
    """Rolling per-cell and per-category success tracking."""

    # Per cell: deque of (timestamp, succeeded: bool)
    cell_history: dict = field(default_factory=dict)
    # Per query category: deque of (timestamp, succeeded: bool)
    category_history: dict = field(default_factory=dict)
    window_s: float = 3600.0  # 1-hour rolling window

    def record(self, cell_id: str, category: str, succeeded: bool) -> None:
        import time as _t
        now = _t.time()
        if cell_id not in self.cell_history:
            self.cell_history[cell_id] = collections.deque(maxlen=100)
        self.cell_history[cell_id].append((now, succeeded))
        if category not in self.category_history:
            self.category_history[category] = collections.deque(maxlen=100)
        self.category_history[category].append((now, succeeded))

    def cell_success_ratio(self, cell_id: str) -> float | None:
        """Return success ratio for cell in rolling window, or None if no data."""
        import time as _t
        hist = self.cell_history.get(cell_id)
        if not hist:
            return None
        cutoff = _t.time() - self.window_s
        recent = [(t, s) for t, s in hist if t > cutoff]
        if not recent:
            return None
        return sum(1 for _, s in recent if s) / len(recent)

    def category_success_ratio(self, category: str) -> float | None:
        """Return success ratio for category in rolling window."""
        import time as _t
        hist = self.category_history.get(category)
        if not hist:
            return None
        cutoff = _t.time() - self.window_s
        recent = [(t, s) for t, s in hist if t > cutoff]
        if not recent:
            return None
        return sum(1 for _, s in recent if s) / len(recent)


class HexNeighborAssist:
    """Coordinates hex mesh query resolution.

    Resolution order:
    1. Select origin cell for query
    2. Try local solve with origin cell's agents
    3. If confidence < local_threshold: ask ring-1 neighbors in parallel
    4. Merge responses with weighted confidence
    5. If merged < neighbor_threshold: escalate to global/swarm
    6. LLM as last resort
    """

    def __init__(
        self,
        topology_registry,
        health_monitor,
        llm_service=None,
        parallel_dispatcher=None,
        magma_audit=None,
        *,
        enabled: bool = False,
        local_threshold: float = 0.72,
        neighbor_threshold: float = 0.82,
        global_threshold: float = 0.90,
        ttl_default: int = 2,
        max_neighbors_per_hop: int = 2,
        parallel_neighbor: bool = True,
        merge_policy: str = "weighted_confidence",
        magma_trace_enabled: bool = True,
        allow_neighbor_llm: bool = True,
        # v3.5.6: efficiency settings
        local_budget_ms: float = 15000.0,
        neighbor_budget_ms: float = 10000.0,
        total_hex_budget_ms: float = 25000.0,
        skip_low_value_neighbor_when_sequential: bool = True,
        preflight_min_score: float = 0.3,
    ):
        self._registry = topology_registry
        self._health = health_monitor
        self._llm = llm_service
        self._dispatcher = parallel_dispatcher
        self._magma = magma_audit
        self.enabled = enabled

        self._local_threshold = local_threshold
        self._neighbor_threshold = neighbor_threshold
        self._global_threshold = global_threshold
        self._ttl_default = ttl_default
        self._max_neighbors = max_neighbors_per_hop
        self._parallel_neighbor = parallel_neighbor
        self._merge_policy = merge_policy
        self._magma_trace = magma_trace_enabled
        self._allow_neighbor_llm = allow_neighbor_llm

        # v3.5.6: efficiency controls
        self._local_budget_ms = local_budget_ms
        self._neighbor_budget_ms = neighbor_budget_ms
        self._total_hex_budget_ms = total_hex_budget_ms
        self._skip_low_neighbor_seq = skip_low_value_neighbor_when_sequential
        self._preflight_min_score = preflight_min_score

        self._lock = threading.Lock()
        self._metrics = HexMeshMetrics()
        self._success_memory = _AdaptiveSuccessMemory()
        # Trace ring buffer — last N completed traces for hologram
        self._trace_buffer: collections.deque[dict] = collections.deque(maxlen=20)
        self._last_trace: dict | None = None

    # ── Main entry point ─────────────────────────────────────────

    async def resolve(
        self,
        query: str,
        intent: str = "",
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Try to resolve query via hex mesh.

        Returns dict with keys: response, confidence, source, trace
        or None if hex mesh is disabled or cannot help.
        """
        if not self.enabled or self._registry.cell_count == 0:
            return None

        trace_id = uuid.uuid4().hex[:12]
        trace = HexResolutionTrace(
            trace_id=trace_id,
            origin_cell_id="",
            query=query,
            started_at=time.time(),
            local_threshold=self._local_threshold,
            neighbor_threshold=self._neighbor_threshold,
        )

        self._emit_magma("HEX_QUERY_STARTED", trace_id=trace_id, query_len=len(query))

        try:
            result = await self._do_resolve(query, intent, trace, context or {})
            trace.completed_at = time.time()
            trace.total_latency_ms = (trace.completed_at - trace.started_at) * 1000

            self._emit_magma(
                "HEX_QUERY_COMPLETED",
                trace_id=trace_id,
                source=trace.final_source,
                confidence=trace.final_confidence,
                latency_ms=trace.total_latency_ms,
            )

            with self._lock:
                self._metrics.origin_cell_resolutions += 1
                if self._magma_trace:
                    self._metrics.magma_traces_written += 1
                trace_dict = trace.to_dict()
                self._trace_buffer.append(trace_dict)
                self._last_trace = trace_dict

            return result

        except Exception as e:
            log.warning("Hex resolve failed: %s", e)
            return None

    def _preflight_score(
        self, query: str, intent: str, origin_id: str,
    ) -> float:
        """Cheap preflight scoring — no LLM calls, pure heuristics.

        Returns 0.0–1.0. Higher = more likely to resolve locally.
        Uses only cheap signals: tokens, domain match, query length,
        recent success ratio per cell, known solver intent.
        """
        score = 0.0

        # 1. Domain match: do any cell domain selectors appear in the query?
        cell_def = self._registry.cells.get(origin_id)
        if cell_def:
            query_lower = query.lower()
            for selector in cell_def.domain_selectors:
                if selector.lower() in query_lower:
                    score += 0.3
                    break

        # 2. Query length: very short queries rarely resolve locally
        if len(query) > 80:
            score += 0.1
        elif len(query) < 20:
            score -= 0.1

        # 3. Recent cell success ratio from adaptive memory
        cell_ratio = self._success_memory.cell_success_ratio(origin_id)
        if cell_ratio is not None:
            if cell_ratio > 0.3:
                score += 0.3
            elif cell_ratio == 0.0:
                score -= 0.3  # Cell has never succeeded — strong skip signal

        # 4. Known solver intent → skip hex, solver handles it deterministically
        solver_intents = {"math", "thermal", "stats", "symbolic", "constraint"}
        if intent in solver_intents:
            score -= 0.5  # Solver path is better

        # 5. Category success ratio
        category = self._classify_query_category(query)
        cat_ratio = self._success_memory.category_success_ratio(category)
        if cat_ratio is not None:
            if cat_ratio == 0.0:
                score -= 0.2

        return max(0.0, min(1.0, score))

    @staticmethod
    def _classify_query_category(query: str) -> str:
        """Cheap query classification for adaptive memory tracking."""
        q = query.lower()
        if any(w in q for w in ("sähkö", "electric", "price", "hinta", "kwh")):
            return "electricity"
        if any(w in q for w in ("sää", "weather", "lämpö", "temp", "frost")):
            return "weather"
        if any(w in q for w in ("math", "laske", "calculate", "+", "*", "=")):
            return "math"
        if any(w in q for w in ("status", "tila", "health")):
            return "system"
        if any(w in q for w in ("koti", "home", "turva", "safety", "security")):
            return "home"
        return "general"

    async def _do_resolve(
        self,
        query: str,
        intent: str,
        trace: HexResolutionTrace,
        context: dict,
    ) -> dict[str, Any] | None:
        """Core resolution logic with preflight gating and budget controls."""

        resolve_start = time.time()

        # Step 1: Select origin cell
        origin_id = self._registry.select_origin_cell(query, intent)
        if not origin_id:
            return None
        trace.origin_cell_id = origin_id
        trace.ttl_path.append(origin_id)
        self._emit_magma("HEX_ORIGIN_CELL_SELECTED", trace_id=trace.trace_id, cell_id=origin_id)

        # v3.5.6: Preflight scoring — cheap check before expensive LLM call
        preflight = self._preflight_score(query, intent, origin_id)
        category = self._classify_query_category(query)
        trace.preflight_score = preflight

        if preflight < self._preflight_min_score:
            # Skip local + neighbor entirely — go straight to global
            self._emit_magma(
                "HEX_PREFLIGHT_SKIP",
                trace_id=trace.trace_id,
                preflight_score=preflight,
                cell_id=origin_id,
            )
            trace.escalated_global = True
            trace.preflight_skipped = True
            trace.neighbor_skipped = True
            with self._lock:
                self._metrics.preflight_skips += 1
                self._metrics.skipped_local_attempts += 1
                self._metrics.skipped_neighbor_attempts += 1
                self._metrics.global_escalations += 1
            self._success_memory.record(origin_id, category, False)
            return None

        with self._lock:
            self._metrics.preflight_passes += 1

        # Step 2: Local attempt (with budget)
        self._emit_magma("HEX_LOCAL_ATTEMPT", trace_id=trace.trace_id, cell_id=origin_id)
        local_result = await self._try_local(origin_id, query, trace)

        # Check local budget
        elapsed_ms = (time.time() - resolve_start) * 1000
        if elapsed_ms > self._local_budget_ms:
            trace.budget_exhausted = True
            self._emit_magma("HEX_LOCAL_BUDGET_EXHAUSTED", trace_id=trace.trace_id, elapsed_ms=elapsed_ms)
            with self._lock:
                self._metrics.budget_exhaustions += 1

        if local_result and local_result["confidence"] >= self._local_threshold:
            trace.final_response = local_result["response"]
            trace.final_confidence = local_result["confidence"]
            trace.final_source = "hex_local"
            self._emit_magma("HEX_LOCAL_SUCCESS", trace_id=trace.trace_id, confidence=local_result["confidence"])
            with self._lock:
                self._metrics.local_only_resolutions += 1
            self._success_memory.record(origin_id, category, True)
            return {
                "response": trace.final_response,
                "confidence": trace.final_confidence,
                "source": "hex_local",
                "trace": trace.to_dict(),
            }

        # Check total hex budget before neighbor attempt
        elapsed_ms = (time.time() - resolve_start) * 1000
        if elapsed_ms > self._total_hex_budget_ms:
            trace.escalated_global = True
            trace.budget_exhausted = True
            trace.neighbor_skipped = True
            self._emit_magma("HEX_TOTAL_BUDGET_EXHAUSTED", trace_id=trace.trace_id, elapsed_ms=elapsed_ms)
            with self._lock:
                self._metrics.budget_exhaustions += 1
                self._metrics.skipped_neighbor_attempts += 1
                self._metrics.global_escalations += 1
            self._success_memory.record(origin_id, category, False)
            return None

        # v3.5.6: Skip sequential neighbor for low-value queries
        is_sequential = not (self._parallel_neighbor and self._dispatcher and self._dispatcher.enabled)
        if is_sequential and self._skip_low_neighbor_seq and preflight < 0.5:
            # Not worth the sequential LLM call
            trace.escalated_global = True
            trace.neighbor_skipped = True
            self._emit_magma(
                "HEX_NEIGHBOR_SKIPPED_SEQUENTIAL",
                trace_id=trace.trace_id,
                preflight_score=preflight,
            )
            with self._lock:
                self._metrics.skipped_neighbor_attempts += 1
                self._metrics.global_escalations += 1
            self._success_memory.record(origin_id, category, False)
            return None

        # Step 3: Neighbor assist (only if parallel dispatch available or sequential fallback)
        self._emit_magma("HEX_NEIGHBOR_ASSIST_STARTED", trace_id=trace.trace_id, cell_id=origin_id)
        neighbor_results = await self._try_neighbors(origin_id, query, trace)
        log.debug("Hex neighbor results: %d responses", len(neighbor_results))

        if neighbor_results:
            merged = self._merge_responses(local_result, neighbor_results, trace)

            if merged["confidence"] >= self._neighbor_threshold:
                trace.final_response = merged["response"]
                trace.final_confidence = merged["confidence"]
                trace.final_source = "hex_neighbor"
                self._emit_magma(
                    "HEX_NEIGHBOR_MERGED",
                    trace_id=trace.trace_id,
                    confidence=merged["confidence"],
                    neighbors=len(neighbor_results),
                )
                with self._lock:
                    self._metrics.neighbor_assist_resolutions += 1
                self._success_memory.record(origin_id, category, True)
                return {
                    "response": trace.final_response,
                    "confidence": trace.final_confidence,
                    "source": "hex_neighbor",
                    "trace": trace.to_dict(),
                }

        # Step 4: Escalate to global/swarm (return None to let caller handle)
        trace.escalated_global = True
        self._emit_magma("HEX_ESCALATED_GLOBAL", trace_id=trace.trace_id)
        with self._lock:
            self._metrics.global_escalations += 1
        self._success_memory.record(origin_id, category, False)

        return None  # Caller (chat_service) will continue to swarm/LLM path

    # ── Local solve ──────────────────────────────────────────────

    async def _try_local(
        self, cell_id: str, query: str, trace: HexResolutionTrace,
    ) -> dict[str, Any] | None:
        """Try to solve query using origin cell's agents."""
        if not self._health.is_healthy(cell_id):
            return None

        agents = self._registry.get_cell_agents(cell_id)
        if not agents:
            return None

        t0 = time.time()
        try:
            # Use the LLM with agent context as a local attempt
            if self._llm is None:
                return None

            agent_context = ", ".join(a.name for a in agents[:5])
            prompt = (
                f"You are an expert in: {agent_context}. "
                f"Answer concisely: {query}"
            )

            response = await self._llm.generate(
                prompt=prompt, max_tokens=500, temperature=0.7,
            )

            latency = (time.time() - t0) * 1000
            trace.local_latency_ms = latency

            if not response:
                return None

            # Estimate confidence based on response quality
            confidence = self._estimate_confidence(response, agents, query)
            trace.local_confidence = confidence
            trace.local_response = response
            trace.models_used.append("local")

            self._health.record_success(cell_id)
            return {"response": response, "confidence": confidence}

        except Exception as e:
            self._health.record_error(cell_id)
            log.debug("Local solve failed for cell %s: %s", cell_id, e)
            return None

    # ── Neighbor assist ──────────────────────────────────────────

    async def _try_neighbors(
        self, origin_id: str, query: str, trace: HexResolutionTrace,
    ) -> list[HexNeighborResponse]:
        """Ask ring-1 neighbors for help, in parallel."""
        neighbor_cells = self._registry.get_neighbor_cells(origin_id)
        if not neighbor_cells:
            return []

        # Filter by health and select top N
        healthy = [c for c in neighbor_cells if self._health.is_healthy(c.id)]
        selected = healthy[: self._max_neighbors]

        if not selected:
            return []

        trace.neighbor_cells_consulted = [c.id for c in selected]
        visited = frozenset([origin_id] + [c.id for c in selected])

        t0 = time.time()
        responses: list[HexNeighborResponse] = []

        if self._parallel_neighbor and self._dispatcher and self._dispatcher.enabled:
            # Parallel neighbor dispatch via existing ParallelLLMDispatcher
            batch = []
            for cell in selected:
                agents = self._registry.get_cell_agents(cell.id)
                agent_context = ", ".join(a.name for a in agents[:5])
                prompt = (
                    f"You are an expert in: {agent_context}. "
                    f"Help answer: {query}"
                )
                batch.append((prompt, "default", 0.7, 500))
                self._emit_magma(
                    "HEX_NEIGHBOR_REQUESTED",
                    trace_id=trace.trace_id,
                    neighbor_cell=cell.id,
                )

            try:
                raw = await self._dispatcher.dispatch_batch(batch)
                with self._lock:
                    self._metrics.completed_hex_neighbor_batches += 1

                for i, (cell, resp_text) in enumerate(zip(selected, raw)):
                    latency = (time.time() - t0) * 1000
                    if resp_text:
                        agents = self._registry.get_cell_agents(cell.id)
                        conf = self._estimate_confidence(resp_text, agents, query)
                        responses.append(HexNeighborResponse(
                            cell_id=cell.id,
                            response=resp_text,
                            confidence=conf,
                            latency_ms=latency,
                            source="neighbor",
                        ))
                        self._health.record_success(cell.id)
                        self._emit_magma(
                            "HEX_NEIGHBOR_RESPONSE",
                            trace_id=trace.trace_id,
                            neighbor_cell=cell.id,
                            confidence=conf,
                        )
                    else:
                        self._health.record_error(cell.id)

            except Exception as e:
                log.warning("Parallel neighbor dispatch failed: %s", e)

        else:
            # Sequential fallback — limit to 1 neighbor, budget 10s max
            selected = selected[:1]
            seq_budget_s = 10.0
            for cell in selected:
                if (time.time() - t0) > seq_budget_s:
                    log.debug("Hex neighbor sequential budget exhausted")
                    break
                self._emit_magma(
                    "HEX_NEIGHBOR_REQUESTED",
                    trace_id=trace.trace_id,
                    neighbor_cell=cell.id,
                )
                try:
                    agents = self._registry.get_cell_agents(cell.id)
                    if not agents or not self._llm:
                        continue
                    agent_context = ", ".join(a.name for a in agents[:5])
                    prompt = (
                        f"You are an expert in: {agent_context}. "
                        f"Help answer: {query}"
                    )
                    resp_text = await self._llm.generate(
                        prompt=prompt, max_tokens=500, temperature=0.7,
                    )
                    latency = (time.time() - t0) * 1000
                    if resp_text:
                        conf = self._estimate_confidence(resp_text, agents, query)
                        responses.append(HexNeighborResponse(
                            cell_id=cell.id,
                            response=resp_text,
                            confidence=conf,
                            latency_ms=latency,
                            source="neighbor",
                        ))
                        self._health.record_success(cell.id)
                        self._emit_magma(
                            "HEX_NEIGHBOR_RESPONSE",
                            trace_id=trace.trace_id,
                            neighbor_cell=cell.id,
                            confidence=conf,
                        )
                except Exception as e:
                    self._health.record_error(cell.id)
                    self._emit_magma(
                        "HEX_NEIGHBOR_TIMEOUT",
                        trace_id=trace.trace_id,
                        neighbor_cell=cell.id,
                        error=str(e),
                    )

        trace.neighbor_latency_ms = (time.time() - t0) * 1000
        trace.neighbor_responses = responses

        with self._lock:
            self._metrics.neighbors_consulted_total += len(selected)

        return responses

    # ── Merge policy ─────────────────────────────────────────────

    def _merge_responses(
        self,
        local_result: dict | None,
        neighbor_responses: list[HexNeighborResponse],
        trace: HexResolutionTrace,
    ) -> dict[str, Any]:
        """Merge local + neighbor responses using weighted confidence."""
        candidates: list[tuple[str, float, float]] = []  # (response, confidence, weight)

        # Include local if available
        if local_result and local_result.get("response"):
            candidates.append((
                local_result["response"],
                local_result["confidence"],
                1.0,  # local gets full weight
            ))

        # Include neighbors
        for nr in neighbor_responses:
            if nr.is_success:
                health = self._health.get_health(nr.cell_id)
                health_weight = health.health_score
                candidates.append((
                    nr.response,
                    nr.confidence,
                    health_weight * 0.9,  # neighbor slightly discounted
                ))

        if not candidates:
            return {"response": "", "confidence": 0.0}

        # Weighted confidence merge — pick best response, boost confidence
        best_resp, best_conf, _ = max(candidates, key=lambda x: x[1] * x[2])

        # Boost confidence based on agreement
        total_weight = sum(w for _, _, w in candidates)
        weighted_sum = sum(c * w for _, c, w in candidates)
        merged_conf = weighted_sum / total_weight if total_weight > 0 else best_conf

        # Cap at 0.95
        merged_conf = min(merged_conf, 0.95)
        trace.merged_confidence = merged_conf
        trace.merged_response = best_resp

        return {"response": best_resp, "confidence": merged_conf}

    # ── Confidence estimation ────────────────────────────────────

    @staticmethod
    def _estimate_confidence(
        response: str, agents: list, query: str,
    ) -> float:
        """Estimate response confidence based on heuristics."""
        if not response:
            return 0.0

        conf = 0.5  # baseline

        # Length bonus (substantive responses)
        if len(response) > 100:
            conf += 0.1
        if len(response) > 300:
            conf += 0.05

        # Agent match bonus
        query_lower = query.lower()
        for agent in agents[:5]:
            name = getattr(agent, "name", "").lower()
            domain = getattr(agent, "domain", "").lower()
            if name in query_lower or domain in query_lower:
                conf += 0.1
                break

        return min(conf, 0.95)

    # ── MAGMA integration ────────────────────────────────────────

    def _emit_magma(self, event_type: str, **payload: Any) -> None:
        if not self._magma or not self._magma_trace:
            return
        try:
            from waggledance.core.magma.audit_projector import AuditEntry

            self._magma.record(AuditEntry(
                event_type=event_type,
                payload=payload,
                source="hex_mesh",
            ))
        except Exception:
            pass

    # ── Metrics ──────────────────────────────────────────────────

    def get_metrics(self) -> dict[str, Any]:
        with self._lock:
            m = self._metrics
            quarantined = len(self._health.get_quarantined_cells())
            return {
                "enabled": self.enabled,
                "cells_loaded": self._registry.cell_count,
                "origin_cell_resolutions": m.origin_cell_resolutions,
                "local_only_resolutions": m.local_only_resolutions,
                "neighbor_assist_resolutions": m.neighbor_assist_resolutions,
                "global_escalations": m.global_escalations,
                "llm_last_resolutions": m.llm_last_resolutions,
                "completed_hex_neighbor_batches": m.completed_hex_neighbor_batches,
                "neighbors_consulted_total": m.neighbors_consulted_total,
                "avg_neighbors_per_assist": (
                    m.neighbors_consulted_total / max(m.neighbor_assist_resolutions, 1)
                ),
                "quarantined_cells": quarantined,
                "self_heal_events": m.self_heal_events,
                "magma_traces_written": m.magma_traces_written,
                "ttl_exhaustions": m.ttl_exhaustions,
                # v3.5.6: efficiency counters
                "skipped_local_attempts": m.skipped_local_attempts,
                "skipped_neighbor_attempts": m.skipped_neighbor_attempts,
                "budget_exhaustions": m.budget_exhaustions,
                "preflight_skips": m.preflight_skips,
                "preflight_passes": m.preflight_passes,
            }

    def get_efficiency_stats(self) -> dict[str, Any]:
        """Return v3.5.6 efficiency statistics for /api/ops and hologram."""
        with self._lock:
            m = self._metrics
            total_queries = m.origin_cell_resolutions + m.preflight_skips
            return {
                "total_hex_queries": total_queries,
                "preflight_skips": m.preflight_skips,
                "preflight_passes": m.preflight_passes,
                "preflight_skip_ratio": (
                    m.preflight_skips / max(total_queries, 1)
                ),
                "skipped_local_attempts": m.skipped_local_attempts,
                "skipped_neighbor_attempts": m.skipped_neighbor_attempts,
                "budget_exhaustions": m.budget_exhaustions,
                "local_success_ratio": (
                    m.local_only_resolutions / max(m.origin_cell_resolutions, 1)
                ),
                "neighbor_success_ratio": (
                    m.neighbor_assist_resolutions / max(m.origin_cell_resolutions, 1)
                ),
                "escalation_ratio": (
                    m.global_escalations / max(total_queries, 1)
                ),
                "cell_success_memory": {
                    cell_id: self._success_memory.cell_success_ratio(cell_id)
                    for cell_id in list(self._success_memory.cell_history.keys())
                },
            }

    def get_last_trace(self) -> dict | None:
        """Return the most recent resolution trace (for hologram)."""
        with self._lock:
            return self._last_trace

    def get_trace_buffer(self) -> list[dict]:
        """Return the trace ring buffer (last N traces for hologram)."""
        with self._lock:
            return list(self._trace_buffer)

    def get_cell_states(self) -> list[dict]:
        """Return cell states for hologram mesh view."""
        cells = []
        for cell_id, cell_def in self._registry.cells.items():
            health = self._health.get_health(cell_id)
            agents = self._registry.get_cell_agents(cell_id)
            cells.append({
                "id": cell_id,
                "coord": {"q": cell_def.coord.q, "r": cell_def.coord.r},
                "domain": ", ".join(cell_def.domain_selectors[:3]),
                "state": "quarantined" if health.is_quarantined else (
                    "active" if health.recent_success_count > 0 else "idle"),
                "health_score": round(health.health_score, 2),
                "load": health.total_queries,
                "quarantined": health.is_quarantined,
                "quarantine_until": health.quarantine_until,
                "self_heal_pending": health.cooldown_probe_pending,
                "last_confidence": 0.0,
                "recent_errors": health.recent_error_count,
                "recent_timeouts": health.recent_timeout_count,
                "recent_successes": health.recent_success_count,
                "agent_count": len(agents),
                "enabled": cell_def.enabled,
            })
        return cells
