"""Chat service — owns hot cache reads/writes per STATE_OWNERSHIP.md.

Ported from core/chat_handler.py and backend/routes/chat.py.
All memory access goes through MemoryService.retrieve_context().
"""

import asyncio
import hashlib
import logging
import re
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
    if (
        '"source":"v3_13_0_solver_registry"' in response
        and "_REFUSED" in response
    ):
        return False
    return bool(response) and freq >= 2
from waggledance.core.policies.escalation_policy import EscalationPolicy
from waggledance.core.ports.config_port import ConfigPort
from waggledance.core.ports.hot_cache_port import HotCachePort

log = logging.getLogger(__name__)

FI_CHARS = set("äöåÄÖÅ")

# Audit H42: a single ä/ö/å detector misclassifies diacritic-less
# Finnish (mobile typing) as English and English with German/Swedish
# proper nouns ("Möbel", "Schrödinger") as Finnish. Stopword overlap
# gives a confident vote when either set hits; unresolved ties fall
# back to the old diacritic check.
#
# Sets are small and deliberately uncontroversial — common function
# words that appear in almost every natural-language sentence in each
# language. Adding domain terms (e.g. "mehiläispesä") would bloat the
# set without improving signal, since domain terms are rarer than
# function words.
_FI_STOPWORDS = frozenset({
    "on", "ei", "ja", "tai", "mutta", "kun", "että", "joka", "jos",
    "vai", "kuin", "myös", "vielä", "jo",
    "mitä", "miksi", "kuinka", "paljonko", "mikä", "milloin", "missä",
    "kuka", "mistä", "mihin", "miten", "kumpi",
    "minä", "sinä", "hän", "me", "te", "he", "se",
    "tämä", "tuo", "nämä", "nuo",
    "olen", "olet", "olemme", "olette", "ovat", "oli", "ollut",
    "kerro", "kerrotko", "kerrohan",
    "voi", "voiko", "saako", "haluan", "tarvitsen",
    "nyt", "tänään", "huomenna", "eilen",
    "talvella", "kesällä", "syksyllä", "keväällä",
})
_EN_STOPWORDS = frozenset({
    "the", "a", "an",
    "is", "are", "was", "were", "be", "been", "being",
    "of", "in", "on", "at", "to", "for", "with", "by", "from",
    "and", "or", "but", "if", "when", "where", "why", "how",
    "what", "who", "which", "this", "that", "these", "those",
    "i", "you", "he", "she", "it", "we", "they",
    "my", "your", "his", "her", "its", "our", "their",
    "do", "does", "did", "have", "has", "had",
    "can", "could", "should", "would", "will", "shall", "may", "might",
    "tell", "me", "us", "give", "show", "please", "explain", "translate",
    "today", "yesterday", "tomorrow", "now",
})

# Bounded query-token sequences can resolve a known high-confidence tie without
# turning individual domain nouns into broad language votes. They are checked
# only after stopword scores are equal, so explicit English context still wins.
_FI_TOKEN_HINT_SEQUENCES = (
    ("palovaroitin", "piippaa"),
    ("piippaa", "palovaroitin"),
)

# Cheap token splitter — unicode-aware via Python 3 default `\w` flags
# so Finnish letters survive splitting.
_TOKEN_RE = re.compile(r"\w+", flags=re.UNICODE)

LOW_CONFIDENCE_GAP_THRESHOLD = 0.6

# P2 S1b Phase 2b: known chat profiles for served-metadata normalization (an odd/
# unknown profile normalizes to an honest "unknown" token, never a user-triggerable gap).
_CHAT_SERVED_KNOWN_PROFILES = frozenset({"GADGET", "COTTAGE", "HOME", "FACTORY"})
_DEFAULT_CHAT_SERVED_RECEIPT_OUT_DIR = "data/runtime/chat_served_receipts"
_DEFAULT_CHAT_CLAIM_WINDOW_ID = "chat_served_runtime_window"


def _config_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)

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
        control_plane_db: object | None = None,
        runtime_gap_detector: object | None = None,
        chat_served_emitter: object | None = None,
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
        self._runtime_gap_detector = runtime_gap_detector
        self._chat_served_emitter = chat_served_emitter
        if self._runtime_gap_detector is None and control_plane_db is not None:
            from waggledance.core.autonomy_growth.gap_intake import RuntimeGapDetector

            self._runtime_gap_detector = RuntimeGapDetector(control_plane_db)
        # v1.18.0: telemetry + ledger (lazy-init)
        self._telemetry = None
        self._ledger = None
        # P2 S1b Phase 2b: chat-served receipt emitter (separate from
        # _ledger, which is a LearningLedger). An injected emitter is used as-is;
        # otherwise _chat_served_emitter_or_none lazily builds the config-gated one.

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
        route_stage_trace: list[dict[str, object]] = []

        def record_route_stage(stage: str, **details: object) -> None:
            event = {"stage": stage}
            event.update(details)
            route_stage_trace.append(event)

        language = self._detect_language(req.query, req.language)
        trace_language = language if language in {"en", "fi"} else "custom"
        record_route_stage(
            "language_detection",
            explicit_hint=req.language != "auto",
            detected_language=trace_language,
        )

        cache_key = req.query.strip().lower()
        cached = self._hot_cache.get(cache_key)
        record_route_stage("hot_cache", hit=cached is not None)
        if cached is not None:
            elapsed = (time.monotonic() - start) * 1000
            self._record_telemetry("hotcache", 1.0, elapsed, True, req.query)
            self._emit_served(
                query=req.query, response=cached, source="hotcache", route_type="hotcache",
                confidence=1.0, latency_ms=elapsed, cached=True, round_table=False,
                agent_id=None, language=language, profile=req.profile,
                route_stage_trace=route_stage_trace)
            return ChatResult(
                response=cached,
                language=language,
                source="hotcache",
                confidence=1.0,
                latency_ms=elapsed,
                agent_id=None,
                round_table=False,
                cached=True,
                route_stage_trace=route_stage_trace,
            )

        memory_context = await self._memory_service.retrieve_context(
            query=req.query,
            language=language,
            limit=5,
        )
        memory_score = max(
            (r.confidence for r in memory_context), default=0.0
        )
        record_route_stage(
            "memory_context",
            language=trace_language,
            limit=5,
            result_count=len(memory_context),
            memory_score=memory_score,
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
        record_route_stage(
            "route_selection",
            route_type=route.route_type,
            solver_intent=features.solver_intent,
            memory_score=memory_score,
        )

        # Solver-first: try deterministic solver before LLM
        if route.route_type == "solver":
            solver_result = self._try_solver(req.query, features.solver_intent)
            record_route_stage(
                "deterministic_solver",
                intent=features.solver_intent,
                answered=solver_result is not None,
            )
            if solver_result is not None:
                elapsed = (time.monotonic() - start) * 1000
                if should_cache_result_simple(solver_result, self._query_frequency.get(cache_key, 0)):
                    self._hot_cache.set(cache_key, solver_result, ttl=3600)
                self._record_telemetry("solver", 0.95, elapsed, True, req.query)
                await self._record_case(req.query, solver_result, 0.95,
                                        "solver", "solver", elapsed)
                self._emit_served(
                    query=req.query, response=solver_result, source="solver", route_type="solver",
                    confidence=0.95, latency_ms=elapsed, cached=False, round_table=False,
                    agent_id=None, language=language, profile=req.profile,
                    route_stage_trace=route_stage_trace)
                return ChatResult(
                    response=solver_result,
                    language=language,
                    source="solver",
                    confidence=0.95,
                    latency_ms=elapsed,
                    agent_id=None,
                    round_table=False,
                    cached=False,
                    route_stage_trace=route_stage_trace,
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
                req.query, features.solver_intent, language, cache_key, start,
                req.profile)
            hybrid_answered = bool(hybrid_trace and hybrid_trace.get("answered"))
            record_route_stage(
                "hybrid_retrieval_8_cell",
                enabled=True,
                authoritative=bool(
                    getattr(self._hybrid_retrieval, "is_authoritative", False)
                ),
                answered=hybrid_answered,
                retrieval_mode=(
                    hybrid_trace.get("retrieval_mode")
                    if isinstance(hybrid_trace, dict)
                    else None
                ),
                hit_count=(
                    hybrid_trace.get("hit_count")
                    if isinstance(hybrid_trace, dict)
                    else None
                ),
                cell_id=(
                    hybrid_trace.get("cell_id")
                    if isinstance(hybrid_trace, dict)
                    else None
                ),
            )
            if hybrid_trace and hybrid_trace.get("answered"):
                result = hybrid_trace["result"]
                result.route_stage_trace = route_stage_trace
                self._emit_served(
                    query=req.query, response=result.response, source=result.source,
                    route_type="hybrid_retrieval", confidence=result.confidence, latency_ms=result.latency_ms,
                    cached=result.cached, round_table=result.round_table, agent_id=result.agent_id,
                    language=result.language, profile=req.profile,
                    route_stage_trace=route_stage_trace)
                return result

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
                hex_answered = bool(
                    hex_result and hex_result.get("confidence", 0) >= 0.72
                )
                record_route_stage(
                    "hex_neighbor_assist_7_cell",
                    enabled=True,
                    answered=hex_answered,
                    confidence=(
                        hex_result.get("confidence")
                        if isinstance(hex_result, dict)
                        else None
                    ),
                    source=(
                        hex_result.get("source")
                        if isinstance(hex_result, dict)
                        else None
                    ),
                )
                if hex_answered:
                    elapsed = (time.monotonic() - start) * 1000
                    hex_trace = hex_result.get("trace")
                    self._record_telemetry(
                        "hex_mesh", hex_result["confidence"], elapsed, True, req.query)
                    await self._record_case(
                        req.query, hex_result["response"],
                        hex_result["confidence"], hex_result["source"],
                        "hex_mesh", elapsed)
                    self._emit_served(
                        query=req.query, response=hex_result["response"],
                        source=hex_result["source"], route_type="hex_mesh",
                        confidence=hex_result["confidence"], latency_ms=elapsed, cached=False,
                        round_table=False, agent_id=None, language=language, profile=req.profile,
                        route_stage_trace=route_stage_trace)
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
                        route_stage_trace=route_stage_trace,
                    )
                # v3.5.6: if hex ran but didn't resolve, record the trace for
                # telemetry (skipped/escalated) — but don't attribute to hex
                if hex_result and hex_result.get("trace"):
                    hex_trace = hex_result.get("trace")
            except Exception as e:
                log.debug("Hex mesh resolve failed: %s", e)
                record_route_stage(
                    "hex_neighbor_assist_7_cell",
                    enabled=True,
                    answered=False,
                    error=e.__class__.__name__,
                )

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
        record_route_stage(
            "orchestrator_llm_fallback",
            route_type=route.route_type,
            source=result.source,
            confidence=result.confidence,
            round_table_used=round_table_used,
        )

        if should_cache_result(result, self._query_frequency.get(cache_key, 0)):
            self._hot_cache.set(cache_key, result.response, ttl=3600)

        elapsed = (time.monotonic() - start) * 1000

        # v1.18.0: record telemetry + ledger
        self._record_telemetry(
            route.route_type, result.confidence, elapsed, True, req.query)
        await self._record_low_confidence_gap(
            query=req.query,
            confidence=result.confidence,
            latency_ms=elapsed,
            route_type=route.route_type,
            source=result.source,
            language=language,
            profile=req.profile,
            round_table_used=round_table_used,
        )

        # Record case trajectory for learning funnel
        await self._record_case(
            req.query, result.response, result.confidence,
            result.source, route.route_type, elapsed)
        self._emit_served(
            query=req.query, response=result.response, source=result.source,
            route_type=route.route_type, confidence=result.confidence, latency_ms=elapsed,
            cached=False, round_table=round_table_used, agent_id=result.agent_id,
            language=language, profile=req.profile, route_stage_trace=route_stage_trace)

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
            route_stage_trace=route_stage_trace,
        )

    async def _record_low_confidence_gap(
        self,
        *,
        query: str,
        confidence: float,
        latency_ms: float,
        route_type: str,
        source: str,
        language: str,
        profile: str,
        round_table_used: bool,
        cell_coord: str | None = None,
    ) -> None:
        """Persist low-confidence chat observations into RuntimeGapDetector."""

        if confidence >= LOW_CONFIDENCE_GAP_THRESHOLD:
            return
        detector = self._runtime_gap_detector
        if detector is None:
            return
        try:
            query_hash = hashlib.sha256(
                query.encode("utf-8", errors="replace")
            ).hexdigest()[:16]
            payload = {
                "confidence": float(confidence),
                "latency_ms": float(latency_ms),
                "route_type": route_type,
                "source": source,
                "language": language,
                "profile": profile,
                "round_table_used": bool(round_table_used),
                "query_hash": query_hash,
                "query_length": len(query),
            }
            def _record_signal() -> None:
                from waggledance.core.autonomy_growth.gap_intake import GapSignal

                detector.record(GapSignal(
                    kind="low_confidence_chat",
                    family_kind=None,
                    cell_coord=cell_coord,
                    intent_seed=query_hash,
                    weight=max(
                        0.1,
                        LOW_CONFIDENCE_GAP_THRESHOLD - float(confidence),
                    ),
                    payload=payload,
                ))

            await asyncio.to_thread(_record_signal)
        except Exception:
            log.debug("Failed to record low-confidence chat gap", exc_info=True)

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

    # ---- P2 S1b Phase 2b: chat-served receipt emission (dormant, fail-open) ----
    def _chat_served_emitter_or_none(self):
        """Lazily build the config-gated chat-served emitter, or return None.

        Returns None when ``chat_served_receipts.enabled`` is false (DORMANT default)
        or on ANY init error -- serving must never be affected. Does NOT flip claim_safe.
        """
        if self._chat_served_emitter is not None:
            return self._chat_served_emitter
        try:
            if not _config_bool(self._config.get("chat_served_receipts.enabled", False)):
                return None
            import os

            from tools.verify_magma_receipt import verify_manifest
            from waggledance.core.magma.chat_served_emitter import ChatServedEmitter
            from waggledance.core.magma.chat_served_sink import ChatServedReceiptSink

            out_dir = str(self._config.get(
                "chat_served_receipts.out_dir", _DEFAULT_CHAT_SERVED_RECEIPT_OUT_DIR))
            ledger_path = os.path.join(out_dir, "ledger.jsonl")
            os.makedirs(out_dir, exist_ok=True)
            sink = ChatServedReceiptSink(ledger_path)
            evidence_kwargs = {}
            if _config_bool(self._config.get(
                "chat_served_receipts.claim_window_evidence.enabled",
                False,
            )):
                evidence_dir = str(self._config.get(
                    "chat_served_receipts.claim_window_evidence.out_dir",
                    out_dir,
                ))
                evidence_kwargs = {
                    "ledger_path": ledger_path,
                    "claim_window_window_id": str(self._config.get(
                        "chat_served_receipts.claim_window_evidence.window_id",
                        _DEFAULT_CHAT_CLAIM_WINDOW_ID,
                    )),
                    "claim_window_anchor_store_path": str(self._config.get(
                        "chat_served_receipts.claim_window_evidence.anchor_store_path",
                        os.path.join(evidence_dir, "claim_window_head_anchors.jsonl"),
                    )),
                    "claim_window_enabled_samples_path": str(self._config.get(
                        "chat_served_receipts.claim_window_evidence.enabled_samples_path",
                        os.path.join(evidence_dir, "claim_window_enabled_samples.jsonl"),
                    )),
                    "claim_window_clean_shutdown_marker_path": str(self._config.get(
                        "chat_served_receipts.claim_window_evidence.clean_shutdown_marker_path",
                        os.path.join(evidence_dir, "claim_window_clean_shutdown.json"),
                    )),
                    "claim_window_served_point_observations_path": str(self._config.get(
                        "chat_served_receipts.claim_window_evidence.served_point_observations_path",
                        os.path.join(evidence_dir, "claim_window_served_points.jsonl"),
                    )),
                }
            self._chat_served_emitter = ChatServedEmitter(
                sink=sink, out_dir=out_dir, verify_manifest=verify_manifest,
                known_profiles=_CHAT_SERVED_KNOWN_PROFILES, enabled=True,
                **evidence_kwargs,
            )
            return self._chat_served_emitter
        except Exception:
            log.debug("chat-served emitter init failed (serving continues)", exc_info=True)
            self._chat_served_emitter = None
            return None

    def _emit_served(self, *, query: str, response: str, source: str, route_type: str,
                     confidence: float, latency_ms: float, cached: bool, round_table: bool,
                     agent_id: str | None, language: str, profile: str,
                     route_stage_trace) -> None:
        """Record the SYNC served_pending denominator + schedule the OFF-loop receipt for
        one served response. FAIL-OPEN: never raises to the caller; disabled -> no-op.
        Called at EVERY served return point (all five, incl. hybrid)."""
        try:
            emitter = self._chat_served_emitter_or_none()
            if emitter is None:
                return
            from waggledance.core.magma.chat_served_emitter import new_served_id

            served_id = new_served_id()
            if emitter.record_pending(served_id, source=source, route_type=route_type,
                                      language=language, profile=profile, agent_id=agent_id):
                emitter.schedule_receipt(
                    served_id, query=query, response=response, source=source,
                    route_type=route_type, confidence=confidence, latency_ms=latency_ms,
                    cached=cached, round_table=round_table, agent_id=agent_id,
                    language=language, profile=profile, route_stage_trace=route_stage_trace)
        except Exception:  # noqa: BLE001 -- serving must never break on receipt emission
            log.debug("chat-served emit failed (serving continues)", exc_info=True)

    async def _record_case(self, query: str, response: str, confidence: float,
                            source: str, route_type: str, elapsed_ms: float):
        """Record a CaseTrajectory from chat traffic via build_from_legacy.

        Chat traffic doesn't go through the full autonomy pipeline, so we use
        build_from_legacy() which truthfully records the Q&A without fabricating
        execution/verifier data that didn't happen.

        Audit H47: SQLiteCaseStore.save_case is synchronous and calls
        sqlite3.commit() inside a threading.Lock — that blocks the asyncio
        event loop for the duration of the fsync. Wrapped here in
        asyncio.to_thread so the commit runs on a worker thread and the
        event loop is free to handle other chat requests.
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
                await asyncio.to_thread(
                    self._case_store.save_case,
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
        cache_key: str, start: float, profile: str,
    ) -> dict | None:
        """Try hybrid FAISS + hex-cell retrieval. Returns dict with trace and optional result."""
        try:
            trace_result = await self._hybrid_retrieval.retrieve(
                query=query, intent=intent, k=5)
            trace_dict = trace_result.to_dict()

            # Phase D-2 observer — record hybrid's would-be solver decision
            # alongside what keyword routing chose (intent here = layer hint).
            # Fire-and-forget — never blocks the chat response.
            try:
                if not hasattr(self, "_hybrid_observer"):
                    from waggledance.core.reasoning.hybrid_observer import HybridObserver
                    from pathlib import Path as _Path
                    import yaml as _yaml
                    _specs = {}
                    _ax_dir = _Path("configs/axioms")
                    if _ax_dir.exists():
                        for _ax_path in _ax_dir.rglob("*.yaml"):
                            try:
                                _ax = _yaml.safe_load(open(_ax_path, encoding="utf-8")) or {}
                                if _ax.get("model_id"):
                                    _specs[_ax["model_id"]] = _ax.get("solver_output_schema", {})
                            except Exception:
                                pass
                    self._hybrid_observer = HybridObserver(
                        self._hybrid_retrieval, solver_specs=_specs,
                    )
                _kw_decision = {
                    "layer": intent or "chat",
                    "confidence": 0.5,
                    "reason": f"intent={intent}",
                }
                import asyncio as _asyncio
                _asyncio.create_task(self._hybrid_observer.record_candidate(
                    query=query, keyword_decision=_kw_decision, intent=intent or "chat",
                ))
            except Exception:
                pass

            if (
                trace_result.hits
                and not trace_result.llm_fallback
                and getattr(self._hybrid_retrieval, "is_authoritative", False)
            ):
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
                await self._record_low_confidence_gap(
                    query=query,
                    confidence=confidence,
                    latency_ms=elapsed,
                    route_type=source,
                    source=source,
                    language=language,
                    profile=profile,
                    round_table_used=False,
                    cell_coord=trace_dict.get("cell_coord"),
                )
                await self._record_case(query, response, confidence, source, source, elapsed)

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
            if intent == "v3_13_0_solver":
                from waggledance.core.v3_13_0.chat_dispatch import (
                    run_v313_solver_chat_request,
                )

                return run_v313_solver_chat_request(query)
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
        """Detect query language. Stopword-overlap with diacritic fallback.

        Audit H42 refinement of the prior FI_CHARS-only check. The old
        single-char detector misclassified:
        - "kuinka talvi vaikuttaa" (diacritic-less Finnish) -> en
        - "Schrödinger equation"   (English w/ German letter)  -> fi
        - "Möbel sale at IKEA"     (English w/ German noun)    -> fi
        - "paljonko maksaa"        (pure Finnish w/o ä/ö)      -> en
        Now: count tokens overlapping with each language's stopword
        set. Winner takes the language. On a tie, a bounded query-token
        sequence may resolve known high-confidence Finnish content; otherwise we
        fall back to the diacritic check to preserve the pre-H42 behavior for
        very short / proper-noun-only queries.

        Explicit ``hint != "auto"`` always wins — operator/client
        knows best.
        """
        if hint != "auto":
            return hint
        token_sequence = tuple(tok.lower() for tok in _TOKEN_RE.findall(query))
        tokens = set(token_sequence)
        fi_hits = len(tokens & _FI_STOPWORDS)
        en_hits = len(tokens & _EN_STOPWORDS)
        if fi_hits > en_hits:
            return "fi"
        if en_hits > fi_hits:
            return "en"
        if any(
            token_sequence[index:index + len(hint_tokens)] == hint_tokens
            for hint_tokens in _FI_TOKEN_HINT_SEQUENCES
            for index in range(len(token_sequence) - len(hint_tokens) + 1)
        ):
            return "fi"
        # Unresolved tie: defer to the original diacritic check, then default en.
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
