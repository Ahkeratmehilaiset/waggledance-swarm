"""Chat service — owns hot cache reads/writes per STATE_OWNERSHIP.md.

Ported from core/chat_handler.py and backend/routes/chat.py.
All memory access goes through MemoryService.retrieve_context().
"""

import logging
import time
import uuid

from waggledance.application.dto.chat_dto import ChatRequest, ChatResult
from waggledance.core.domain.task import TaskRequest
from waggledance.core.orchestration.orchestrator import Orchestrator
from waggledance.core.orchestration.routing_policy import (
    extract_features,
    select_route,
)
from waggledance.core.policies.confidence_policy import should_cache_result

# Solver caching: high-confidence solver results are always cache-worthy
def should_cache_result_simple(response: str, freq: int) -> bool:
    return bool(response) and freq >= 2
from waggledance.core.policies.escalation_policy import EscalationPolicy
from waggledance.core.ports.config_port import ConfigPort
from waggledance.core.ports.hot_cache_port import HotCachePort

log = logging.getLogger(__name__)

FI_CHARS = set("äöåÄÖÅ")

# v1.18.0: use shared helpers (convergence layer)
from core.shared_routing_helpers import probe_micromodel as _shared_probe_micromodel


class ChatService:
    """Handles chat requests end-to-end: cache, route, execute, escalate."""

    def __init__(
        self,
        orchestrator: Orchestrator,
        memory_service: "MemoryService",  # noqa: F821 — forward ref
        hot_cache: HotCachePort,
        routing_policy_fn: object,
        config: ConfigPort,
        case_builder: "CaseTrajectoryBuilder | None" = None,  # noqa: F821
        case_store: object | None = None,
        verifier_store: object | None = None,
        hybrid_retrieval: object | None = None,
        hex_neighbor_assist: object | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._memory_service = memory_service
        self._hot_cache = hot_cache
        self._routing_policy_fn = routing_policy_fn
        self._config = config
        self._escalation = EscalationPolicy()
        self._query_frequency: dict[str, int] = {}
        # Case recording: connects chat traffic to the learning funnel
        self._case_builder = case_builder
        self._case_store = case_store
        self._verifier_store = verifier_store
        # v3.4: hybrid FAISS + hex-cell retrieval
        self._hybrid_retrieval = hybrid_retrieval
        # v3.5.4: hex neighbor mesh
        self._hex_neighbor_assist = hex_neighbor_assist
        # v1.18.0: telemetry + ledger (lazy-init)
        self._telemetry = None
        self._ledger = None

    async def handle(self, req: ChatRequest) -> ChatResult:
        """Process a chat request through the full pipeline.

        1. Detect language
        2. Check hot cache
        3. Fetch memory context
        4. Extract routing features
        5. Select route
        6. Execute via orchestrator
        7. Apply escalation policy if needed
        8. Store result if worth caching
        9. Return ChatResult
        """
        start = time.monotonic()

        language = self._detect_language(req.query, req.language)

        cache_key = req.query.strip().lower()
        cached = self._hot_cache.get(cache_key)
        if cached is not None:
            elapsed = (time.monotonic() - start) * 1000
            self._record_telemetry("hotcache", 1.0, elapsed, True, req.query)
            return ChatResult(
                response=cached,
                language=language,
                source="hotcache",
                confidence=1.0,
                latency_ms=elapsed,
                agent_id=None,
                round_table=False,
                cached=True,
            )

        memory_context = await self._memory_service.retrieve_context(
            query=req.query,
            language=language,
            limit=5,
        )
        memory_score = max(
            (r.confidence for r in memory_context), default=0.0
        )

        self._query_frequency[cache_key] = (
            self._query_frequency.get(cache_key, 0) + 1
        )

        mm_enabled = bool(self._config.get("advanced_learning.micro_model_enabled", False))
        mm_hit = False
        mm_confidence = 0.0
        if mm_enabled:
            mm_hit, mm_confidence = self._probe_micromodel(req.query)

        features = extract_features(
            query=req.query,
            hot_cache_hit=False,
            memory_score=memory_score,
            matched_keywords=[],
            profile=req.profile,
            language=language,
            micromodel_enabled=mm_enabled,
            micromodel_hit=mm_hit,
            micromodel_confidence=mm_confidence,
        )
        route = select_route(features, self._config)

        # Solver-first: try deterministic solver before LLM
        if route.route_type == "solver":
            solver_result = self._try_solver(req.query, features.solver_intent)
            if solver_result is not None:
                elapsed = (time.monotonic() - start) * 1000
                if should_cache_result_simple(solver_result, self._query_frequency.get(cache_key, 0)):
                    self._hot_cache.set(cache_key, solver_result, ttl=3600)
                self._record_telemetry("solver", 0.95, elapsed, True, req.query)
                self._record_case(req.query, solver_result, 0.95,
                                  "solver", "solver", elapsed)
                return ChatResult(
                    response=solver_result,
                    language=language,
                    source="solver",
                    confidence=0.95,
                    latency_ms=elapsed,
                    agent_id=None,
                    round_table=False,
                    cached=False,
                )
            # Solver miss — fall through to hybrid retrieval or LLM
            route = route.__class__(
                route_type="llm", confidence=0.6,
                routing_latency_ms=route.routing_latency_ms,
            )

        # v3.4: Hybrid FAISS retrieval — after solver, before LLM
        hybrid_trace = None
        if self._hybrid_retrieval and self._hybrid_retrieval.enabled:
            hybrid_trace = await self._try_hybrid_retrieval(
                req.query, features.solver_intent, language, cache_key, start)
            if hybrid_trace and hybrid_trace.get("answered"):
                return hybrid_trace["result"]

        # v3.5.4: Hex neighbor mesh — after solver/hybrid, before orchestrator
        # v3.5.6: hex_trace only populated when hex actually ran (trace alignment)
        hex_trace = None
        if self._hex_neighbor_assist and self._hex_neighbor_assist.enabled:
            try:
                hex_result = await self._hex_neighbor_assist.resolve(
                    query=req.query,
                    intent=features.solver_intent,
                    context={"language": language, "profile": req.profile},
                )
                if hex_result and hex_result.get("confidence", 0) >= 0.72:
                    elapsed = (time.monotonic() - start) * 1000
                    hex_trace = hex_result.get("trace")
                    self._record_telemetry(
                        "hex_mesh", hex_result["confidence"], elapsed, True, req.query)
                    self._record_case(
                        req.query, hex_result["response"],
                        hex_result["confidence"], hex_result["source"],
                        "hex_mesh", elapsed)
                    return ChatResult(
                        response=hex_result["response"],
                        language=language,
                        source=hex_result["source"],
                        confidence=hex_result["confidence"],
                        latency_ms=elapsed,
                        agent_id=None,
                        round_table=False,
                        cached=False,
                        hybrid_trace=hybrid_trace,
                    )
                # v3.5.6: if hex ran but didn't resolve, record the trace for
                # telemetry (skipped/escalated) — but don't attribute to hex
                if hex_result and hex_result.get("trace"):
                    hex_trace = hex_result.get("trace")
            except Exception as e:
                log.debug("Hex mesh resolve failed: %s", e)

        task = TaskRequest(
            id=str(uuid.uuid4()),
            query=req.query,
            language=language,
            profile=req.profile,
            user_id=req.user_id,
            context=[],
            timestamp=time.time(),
        )

        result = await self._orchestrator.handle_task(task, route)

        round_table_used = False
        if self._escalation.needs_round_table(result, task):
            consensus = await self._orchestrator.run_round_table(task)
            if consensus.confidence > result.confidence:
                result = result.__class__(
                    agent_id="round_table",
                    response=consensus.consensus,
                    confidence=consensus.confidence,
                    latency_ms=consensus.latency_ms,
                    source="swarm",
                    metadata={},
                )
                round_table_used = True

        if should_cache_result(result, self._query_frequency.get(cache_key, 0)):
            self._hot_cache.set(cache_key, result.response, ttl=3600)

        elapsed = (time.monotonic() - start) * 1000

        # v1.18.0: record telemetry + ledger
        self._record_telemetry(
            route.route_type, result.confidence, elapsed, True, req.query)

        # Record case trajectory for learning funnel
        self._record_case(
            req.query, result.response, result.confidence,
            result.source, route.route_type, elapsed)

        return ChatResult(
            response=result.response,
            language=language,
            source=result.source,
            confidence=result.confidence,
            latency_ms=elapsed,
            agent_id=result.agent_id,
            round_table=round_table_used,
            cached=False,
            hybrid_trace=hybrid_trace,
        )

    def _record_telemetry(self, route_type: str, confidence: float,
                           latency_ms: float, success: bool, query: str):
        """v1.18.0: Record telemetry + low-confidence ledger entries."""
        try:
            if self._telemetry is None:
                from core.route_telemetry import RouteTelemetry
                self._telemetry = RouteTelemetry()
            self._telemetry.record(route_type, latency_ms, success)
        except Exception:
            pass
        try:
            if confidence < 0.6:
                if self._ledger is None:
                    from core.learning_ledger import LearningLedger
                    self._ledger = LearningLedger()
                self._ledger.log(
                    "low_confidence_query",
                    agent_id=route_type,
                    query=query[:500],
                    confidence=confidence,
                    route=route_type,
                )
        except Exception:
            pass

    def _record_case(self, query: str, response: str, confidence: float,
                      source: str, route_type: str, elapsed_ms: float):
        """Record a CaseTrajectory from chat traffic via build_from_legacy.

        Chat traffic doesn't go through the full autonomy pipeline, so we use
        build_from_legacy() which truthfully records the Q&A without fabricating
        execution/verifier data that didn't happen.
        """
        if self._case_builder is None:
            return
        try:
            case = self._case_builder.build_from_legacy(
                question=query,
                answer=response,
                confidence=confidence,
                source=source,
                route_type=route_type,
            )
            if self._case_store is not None:
                self._case_store.save_case(
                    case.to_dict(),
                    intent=route_type,
                    elapsed_ms=elapsed_ms,
                )
            log.debug("Chat case recorded: %s grade=%s",
                      case.trajectory_id, case.quality_grade.value)
        except Exception:
            log.debug("Failed to record chat case", exc_info=True)

    async def _try_hybrid_retrieval(
        self, query: str, intent: str, language: str,
        cache_key: str, start: float,
    ) -> dict | None:
        """Try hybrid FAISS + hex-cell retrieval. Returns dict with trace and optional result."""
        try:
            trace_result = await self._hybrid_retrieval.retrieve(
                query=query, intent=intent, k=5)
            trace_dict = trace_result.to_dict()

            if trace_result.hits and not trace_result.llm_fallback:
                # Format hits into a response
                _lang = language
                _header = ("Löysin seuraavat tiedot:\n\n" if _lang == "fi"
                           else "Here is what I found:\n\n")
                response = _header + "\n\n".join(
                    f"{i}. {h.text[:300]}"
                    for i, h in enumerate(trace_result.hits[:5], 1)
                )
                confidence = trace_result.hits[0].score
                elapsed = (time.monotonic() - start) * 1000

                source = trace_result.answered_by_layer
                if should_cache_result_simple(response, self._query_frequency.get(cache_key, 0)):
                    self._hot_cache.set(cache_key, response, ttl=3600)

                self._record_telemetry(source, confidence, elapsed, True, query)
                self._record_case(query, response, confidence, source, source, elapsed)

                result = ChatResult(
                    response=response,
                    language=language,
                    source=source,
                    confidence=confidence,
                    latency_ms=elapsed,
                    agent_id="hybrid_retrieval",
                    round_table=False,
                    cached=False,
                    hybrid_trace=trace_dict,
                )
                return {"answered": True, "result": result, **trace_dict}

            # Not enough hits — return trace but let LLM handle it
            return trace_dict

        except Exception as e:
            log.debug("Hybrid retrieval failed: %s", e)
            return None

    @staticmethod
    def _try_solver(query: str, intent: str) -> str | None:
        """Try deterministic solver for math/thermal/stats. Returns answer or None."""
        try:
            if intent == "math":
                from core.math_solver import MathSolver
                if MathSolver.is_math(query):
                    result = MathSolver.solve(query)
                    if result is not None:
                        return result
            elif intent == "thermal":
                from waggledance.core.reasoning.thermal_solver import ThermalSolver
                import re
                solver = ThermalSolver()
                # Frost risk: extract temperature
                m = re.search(r'(-?\d+(?:\.\d+)?)\s*°?[cC]', query)
                if m and ("frost" in query.lower() or "pakkas" in query.lower()):
                    t = float(m.group(1))
                    r = solver.frost_risk(t, pipe_insulated=True)
                    return f"Frost risk at {t}°C: {r.value:.1f} ({_risk_label(r.value)})"
                # Temperature conversion
                if "celsius" in query.lower() or "to c" in query.lower():
                    from core.math_solver import MathSolver
                    result = MathSolver.solve(query)
                    if result is not None:
                        return result
                if "fahrenheit" in query.lower() or "to f" in query.lower():
                    from core.math_solver import MathSolver
                    result = MathSolver.solve(query)
                    if result is not None:
                        return result
                # "is X degrees too hot?" — threshold check
                m = re.search(r'(\d+(?:\.\d+)?)\s*(?:degrees|°|astetta)', query.lower())
                if m:
                    t = float(m.group(1))
                    label = "too hot" if t > 40 else "comfortable" if t > 15 else "cold"
                    return f"{t}° is {label} (threshold: >40 hot, 15-40 comfortable, <15 cold)"
            elif intent == "stats":
                # Stats queries describe time-series aggregations — LLM needs context
                # but we can at least acknowledge the intent for proper routing
                return None
        except Exception:
            pass
        return None

    @staticmethod
    def _probe_micromodel(query: str) -> tuple[bool, float]:
        """Try legacy PatternMatchEngine (cached singleton). Returns (hit, confidence)."""
        return _shared_probe_micromodel(query)

    @staticmethod
    def _detect_language(query: str, hint: str) -> str:
        """Detect query language. FI chars -> fi, otherwise use hint or default."""
        if hint != "auto":
            return hint
        if any(c in FI_CHARS for c in query):
            return "fi"
        return "en"


def _risk_label(score: float) -> str:
    if score <= 0.0:
        return "safe"
    elif score <= 0.3:
        return "low risk"
    elif score <= 0.6:
        return "medium risk"
    return "high risk"
