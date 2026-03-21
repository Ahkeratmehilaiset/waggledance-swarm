"""ChatHandler — thin orchestrator that delegates to focused modules.

v3.3 refactor: split from 1136-line god object into 5 focused modules:
- chat_preprocessing.py  — language, corrections, teaching, datetime
- chat_routing_engine.py — SmartRouter v2, swarm, layers, FAISS
- chat_delegation.py     — agent dispatch, multi-agent collaboration
- chat_telemetry.py      — metrics, hot cache, route telemetry
- chat_router.py         — autonomy → legacy → hex → fallback chain
"""
import asyncio
import logging
import time

from core.hive_routing import MASTER_NEGATIVE_KEYWORDS, AGENT_EN_PROMPTS
from core.chat_preprocessing import ChatPreprocessor
from core.chat_routing_engine import ChatRoutingEngine
from core.chat_delegation import AgentDelegator
from core.chat_telemetry import ChatTelemetry
from core.tracing import get_tracer

_autonomy_init_lock = asyncio.Lock()

_tracer = get_tracer("waggledance.chat")

# v2.0: Autonomy runtime (optional)
_AUTONOMY_AVAILABLE = False
try:
    from waggledance.core.autonomy.runtime import AutonomyRuntime as _AutonomyRuntime
    from waggledance.application.services.autonomy_service import AutonomyService as _AutonomyService
    _AUTONOMY_AVAILABLE = True
except ImportError:
    pass

log = logging.getLogger("hivemind")


class ChatHandler:
    """Handles chat routing, delegation, and response processing.

    Uses __getattr__/__setattr__ to proxy attribute access to the parent
    HiveMind instance, so method bodies work without modification.
    """

    def __init__(self, hive):
        object.__setattr__(self, 'hive', hive)
        object.__setattr__(self, 'preprocessor', ChatPreprocessor(hive))
        object.__setattr__(self, 'routing', ChatRoutingEngine(hive))
        object.__setattr__(self, 'delegator', AgentDelegator(hive))
        object.__setattr__(self, 'telemetry', ChatTelemetry(hive))

    def __getattr__(self, name):
        """Proxy attribute reads to HiveMind."""
        return getattr(self.hive, name)

    def __setattr__(self, name, value):
        """Proxy attribute writes to HiveMind."""
        if name in ('hive', 'preprocessor', 'routing', 'delegator', 'telemetry'):
            object.__setattr__(self, name, value)
        else:
            setattr(self.hive, name, value)

    async def _do_chat(self, message: str, language: str = "auto", source: str = "chat") -> str:
        """Main chat pipeline: preprocess → route → delegate → telemetry."""
        _chat_t0 = time.perf_counter()
        _original_message = message
        self._last_model_result = None
        self._last_explanation = None

        with _tracer.start_as_current_span("chat_request") as _span:
            _span.set_attribute("query", message[:100])
            _span.set_attribute("source", source)
            return await self._do_chat_inner(
                message, language, source, _chat_t0, _original_message, _span)

    async def _do_chat_inner(self, message, language, source, _chat_t0,
                             _original_message, _span) -> str:
        """Inner chat logic wrapped by tracing span."""
        # ═══ 1. Language detection ═══
        _detected_lang = self.preprocessor.detect_lang(message, language)
        _span.set_attribute("language", _detected_lang)

        # ═══ 2. Autonomy routing (ChatRouter) ═══
        _rt_cfg = self.config.get("runtime", {})
        if (_rt_cfg.get("primary") == "waggledance"
                and not _rt_cfg.get("compatibility_mode", True)
                and _AUTONOMY_AVAILABLE):
            try:
                _autonomy_svc = getattr(self, '_autonomy_service', None)
                if _autonomy_svc is None:
                    async with _autonomy_init_lock:
                        # Re-check after acquiring lock
                        _autonomy_svc = getattr(self, '_autonomy_service', None)
                        if _autonomy_svc is None:
                            _svc = _AutonomyService(
                                profile=self.config.get("profile", "DEFAULT"))
                            _svc.start()
                            self._autonomy_service = _svc
                            _autonomy_svc = _svc

                from core.chat_router import ChatRouter
                _router = ChatRouter(autonomy_service=_autonomy_svc)
                _chat_result = await _router.route(message, _detected_lang, source)
                if _chat_result.method == "autonomy" and _chat_result.response:
                    self._last_chat_message = message
                    self._last_chat_response = _chat_result.response
                    self._last_chat_method = f"autonomy_{_chat_result.method}"
                    self._last_chat_agent_id = _chat_result.agent_id
                    self._last_model_result = None
                    _auto_ms = _chat_result.latency_ms
                    self.metrics.log_chat(
                        query=_original_message,
                        method=self._last_chat_method,
                        agent_id=self._last_chat_agent_id,
                        model_used="autonomy_runtime",
                        confidence=_chat_result.confidence,
                        response_time_ms=_auto_ms,
                        route="autonomy", language=_detected_lang)
                    self.telemetry.record_request_telemetry(
                        "autonomy", _chat_result.confidence, _auto_ms, True, message)
                    return _chat_result.response
            except Exception as _auto_err:
                log.warning("Autonomy runtime unavailable, legacy fallback: %s", _auto_err)

        # ═══ 3. Preprocessing: corrections, teaching, datetime ═══
        resp = await self.preprocessor.detect_correction(
            message, _detected_lang, _original_message, _chat_t0)
        if resp:
            return resp

        resp = await self.preprocessor.detect_teaching(
            message, _detected_lang, _original_message, _chat_t0)
        if resp:
            return resp

        resp = await self.preprocessor.detect_datetime(
            message, _detected_lang, _original_message, _chat_t0)
        if resp:
            return resp

        # ═══ 4. Consciousness pre-check (SmartRouter fast path) ═══
        resp, _pre = await self.routing.run_consciousness_precheck(
            message, _original_message, _detected_lang, _chat_t0)
        if resp:
            return resp

        # ═══ 5. SmartRouter v2 layer routing ═══
        _route = self.routing.get_smart_route(message, _pre)

        resp = await self.routing.try_model_based(
            message, _route, _original_message, _detected_lang, _chat_t0, _pre)
        if resp:
            self.telemetry.record_request_telemetry(
                "model_based", 0.9, (time.perf_counter() - _chat_t0) * 1000, True, message)
            return resp

        resp = await self.routing.try_rule_constraints(
            message, _route, _original_message, _detected_lang, _chat_t0)
        if resp:
            return resp

        resp = await self.routing.try_retrieval(
            message, _route, _original_message, _detected_lang, _chat_t0)
        if resp:
            return resp

        resp = await self.routing.try_statistical(
            message, _route, _original_message, _detected_lang, _chat_t0)
        if resp:
            return resp

        # ═══ 6. Translation ═══
        _en_message, _translation_used, _fi_en_result = \
            await self.preprocessor.translate_if_needed(message, _detected_lang)
        _routed_message = _en_message if (_translation_used or _detected_lang == "en") else message
        _use_en_prompts = _translation_used or _detected_lang == "en"

        # ═══ 7. Memory + FAISS enrichment ═══
        await self.memory.store_memory(
            content=f"Käyttäjä sanoi: {message}",
            agent_id="user", memory_type="observation", importance=0.6)
        context = await self.memory.get_full_context(_original_message)
        context = await self.routing.enrich_with_faiss(
            _original_message, _en_message, context)

        msg_lower = _original_message.lower()

        # ═══ 8. Multi-agent check ═══
        multi_keywords = ["kaikki", "tilanne", "yhteenveto", "status", "yleiskatsaus"]
        if any(w in msg_lower for w in multi_keywords):
            return await self.delegator.multi_agent_collaboration(message, {}, context)

        # ═══ 9. Agent routing + delegation ═══
        if self.spawner and hasattr(self.spawner, 'yaml_bridge'):
            routing_rules = self.spawner.yaml_bridge.get_routing_rules()
        else:
            routing_rules = {}
        routing_rules.setdefault("hacker", ["bugi", "refaktor", "koodi", "tietoturva"])
        routing_rules.setdefault("oracle", ["haku", "etsi", "tutki", "google", "claude"])

        if getattr(self, '_swarm_enabled', False) and self.scheduler and self.scheduler.agent_count > 0:
            delegate_to, best_score = self.routing.swarm_route(msg_lower, routing_rules)
        else:
            delegate_to, best_score = self.routing.legacy_route(msg_lower, routing_rules)

        if delegate_to and best_score > 0:
            response = await self.delegator.delegate_to_agent(
                delegate_to, _routed_message, context, msg_lower,
                translation_used=_translation_used, detected_lang=_detected_lang,
                use_en_prompts=_use_en_prompts, fi_en_result=_fi_en_result)
            self.telemetry.populate_hot_cache(
                _original_message, response, score=0.75,
                source=f"agent_{delegate_to}", detected_lang=_detected_lang)
            self._last_chat_message = message
            self._last_chat_response = response
            self._last_chat_method = _pre.method if _pre else ""
            self._last_chat_agent_id = delegate_to
            if hasattr(self, 'consciousness') and self.consciousness:
                self._last_episode_id = self.consciousness.store_episode(
                    query=message, response=response,
                    prev_episode_id=self._last_episode_id)
            _del_conf = _pre.confidence if _pre else 0.0
            _del_ms = (time.perf_counter() - _chat_t0) * 1000
            self.metrics.log_chat(
                query=_original_message, method="delegate",
                agent_id=delegate_to,
                model_used=getattr(self.llm, 'model', 'unknown'),
                confidence=_del_conf, response_time_ms=_del_ms,
                route="agent_delegate", language=_detected_lang,
                translated=_translation_used)
            self.telemetry.record_request_telemetry(
                "delegate", _del_conf, _del_ms, True, message)
            return response

        # ═══ 10. Negative keyword forced delegation ═══
        for neg_kw in MASTER_NEGATIVE_KEYWORDS:
            if neg_kw in msg_lower:
                for agent_type, keywords in routing_rules.items():
                    if neg_kw in keywords or any(neg_kw in kw for kw in keywords):
                        response = await self.delegator.delegate_to_agent(
                            agent_type, message, context, msg_lower,
                            translation_used=_translation_used,
                            detected_lang=_detected_lang,
                            use_en_prompts=_use_en_prompts,
                            fi_en_result=_fi_en_result)
                        self.telemetry.populate_hot_cache(
                            _original_message, response, score=0.75,
                            source=f"agent_{agent_type}", detected_lang=_detected_lang)
                        self._last_chat_message = message
                        self._last_chat_response = response
                        self._last_chat_method = _pre.method if _pre else ""
                        self._last_chat_agent_id = agent_type
                        if hasattr(self, 'consciousness') and self.consciousness:
                            self._last_episode_id = self.consciousness.store_episode(
                                query=message, response=response,
                                prev_episode_id=self._last_episode_id)
                        self.metrics.log_chat(
                            query=_original_message, method="neg_kw_delegate",
                            agent_id=agent_type,
                            model_used=getattr(self.llm, 'model', 'unknown'),
                            confidence=_pre.confidence if _pre else 0.0,
                            response_time_ms=(time.perf_counter() - _chat_t0) * 1000,
                            route="agent_delegate", language=_detected_lang,
                            translated=_translation_used)
                        return response
                break  # No specialist found → master fallback

        # ═══ 11. Master fallback ═══
        return await self._master_fallback(
            message, _routed_message, context, _original_message,
            _detected_lang, _chat_t0, _pre,
            _translation_used, _fi_en_result, _use_en_prompts)

    async def _master_fallback(self, message, routed_message, context,
                               original_message, detected_lang, chat_t0, pre,
                               translation_used, fi_en_result, use_en_prompts):
        """Master agent fallback — last resort."""
        _orig_master_sys = None
        if use_en_prompts and "hivemind" in AGENT_EN_PROMPTS:
            _orig_master_sys = self.master_agent.system_prompt
            from datetime import datetime as _dt
            _consciousness_context = ""
            if self.consciousness:
                _ctx_q = routed_message if translation_used else message
                _consciousness_context = self.consciousness.get_context(_ctx_q)
                if _consciousness_context:
                    _consciousness_context = "\n" + _consciousness_context
                _corrections_ctx = self.consciousness.check_previous_corrections(message)
                if _corrections_ctx:
                    _consciousness_context += (
                        "\n\nCORRECTIONS (avoid repeating these mistakes):\n"
                        + _corrections_ctx)
            self.master_agent.system_prompt = (
                f"Date: {_dt.now():%Y-%m-%d %H:%M}. "
                + AGENT_EN_PROMPTS["hivemind"] + _consciousness_context)
        try:
            with self._enriched_prompt(self.master_agent, knowledge_max_chars=2000) as _enriched:
                response = await _enriched.think(routed_message, context)
        except Exception as e:
            log.error(f"Master agent think failed: {type(e).__name__}: {e}")
            response = "Anteeksi, en pystynyt vastaamaan juuri nyt. Yrita hetken kuluttua uudelleen."
        finally:
            if _orig_master_sys is not None:
                self.master_agent.system_prompt = _orig_master_sys

        if self.en_validator and use_en_prompts:
            _val = self.en_validator.validate(response)
            if _val.was_corrected:
                response = _val.corrected

        if translation_used and getattr(self, 'translation_proxy', None):
            try:
                _en_fi = self.translation_proxy.en_to_fi(response, force_opus=True)
                if _en_fi.method != "passthrough":
                    response = _en_fi.text
            except Exception as e:
                log.error(f"EN->FI translation failed: {type(e).__name__}: {e}")

        # Hallucination check
        _hall = None
        _quality = 0.7
        if self.consciousness:
            try:
                _hall = self.consciousness.check_hallucination(message, response)
                if _hall.is_suspicious and getattr(self, 'monitor', None):
                    await self.monitor.system(f"⚠️ Hallusinaatio? {_hall.reason}")
                _quality = _hall.relevance if not _hall.is_suspicious else 0.3
                self.consciousness.learn_conversation(message, response, quality_score=_quality)
            except Exception as e:
                log.debug(f"Hallucination check failed: {e}")

        self.telemetry.populate_hot_cache(
            original_message, response, score=_quality,
            source="master", detected_lang=detected_lang)
        await self._notify_ws("chat_response", {
            "message": message, "response": response,
            "language": detected_lang, "translated": translation_used
        })
        self._last_chat_message = message
        self._last_chat_response = response
        self._last_chat_method = pre.method if pre else ""
        self._last_chat_agent_id = "master"
        if hasattr(self, 'consciousness') and self.consciousness:
            self._last_episode_id = self.consciousness.store_episode(
                query=message, response=response,
                prev_episode_id=self._last_episode_id,
                quality=_quality)
        _master_ms = (time.perf_counter() - chat_t0) * 1000
        self.metrics.log_chat(
            query=original_message, method="master",
            agent_id="master", model_used="phi4-mini",
            confidence=_quality,
            was_hallucination=bool(_hall and _hall.is_suspicious),
            response_time_ms=_master_ms,
            route="llm_master", language=detected_lang,
            translated=translation_used)
        self.telemetry.record_request_telemetry(
            "llm", _quality, _master_ms, True, message, was_fallback=True)
        return response

    # Keep these for backward compatibility (used by other modules)
    def _record_request_telemetry(self, route_type, confidence, latency_ms,
                                  success, query, was_fallback=False):
        self.telemetry.record_request_telemetry(
            route_type, confidence, latency_ms, success, query, was_fallback)

    def _populate_hot_cache(self, query, response, score=0.75,
                            source="chat", detected_lang="fi"):
        self.telemetry.populate_hot_cache(query, response, score, source, detected_lang)

    def _is_valid_response(self, response):
        return AgentDelegator._is_valid_response(response)

    def _swarm_route(self, msg_lower, routing_rules):
        return self.routing.swarm_route(msg_lower, routing_rules)

    def _legacy_route(self, msg_lower, routing_rules):
        return self.routing.legacy_route(msg_lower, routing_rules)

    async def _delegate_to_agent(self, delegate_to, message, context, msg_lower, **kwargs):
        return await self.delegator.delegate_to_agent(
            delegate_to, message, context, msg_lower, **kwargs)

    async def _multi_agent_collaboration(self, mission, plan, context):
        return await self.delegator.multi_agent_collaboration(mission, plan, context)
