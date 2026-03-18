"""Chat routing and delegation — extracted from HiveMind v0.9.0."""
import asyncio
import logging
import time
from datetime import datetime

from core.hive_routing import (
    WEIGHTED_ROUTING, PRIMARY_WEIGHT, SECONDARY_WEIGHT,
    MASTER_NEGATIVE_KEYWORDS, AGENT_EN_PROMPTS,
)

# v1.18.0: shared singletons (convergence layer)
from core.shared_routing_helpers import get_route_telemetry as _get_route_telemetry
from core.shared_routing_helpers import get_learning_ledger as _get_learning_ledger

try:
    from core.translation_proxy import detect_language
    _TRANSLATION_AVAILABLE = True
except ImportError:
    _TRANSLATION_AVAILABLE = False
    def detect_language(t): return "fi"

# v2.0: Autonomy runtime (optional — graceful degradation)
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

    def __getattr__(self, name):
        """Proxy attribute reads to HiveMind."""
        return getattr(self.hive, name)

    def __setattr__(self, name, value):
        """Proxy attribute writes to HiveMind."""
        if name == 'hive':
            object.__setattr__(self, name, value)
        else:
            setattr(self.hive, name, value)

    async def _do_chat(self, message: str, language: str = "auto", source: str = "chat") -> str:
        """Varsinainen chat-logiikka. Tukee FI<->EN kaannosta: auto/fi/en."""
        _chat_t0 = time.perf_counter()
        _original_message = message
        _translation_used = False
        _fi_en_result = None
        _detected_lang = language
        # Reset per-request structured results (read by /api/chat after return)
        self._last_model_result = None
        self._last_explanation = None

        # ═══ Kielentunnistus ═══
        if language == "auto":
            _detected_lang = detect_language(message) if _TRANSLATION_AVAILABLE else "fi"

        # ═══ v2.0: Autonomy Runtime path (solver-first, LLM-last) ═══
        # When runtime.primary=waggledance and compatibility_mode=false,
        # route through AutonomyRuntime instead of legacy path.
        _rt_cfg = self.config.get("runtime", {})
        if (_rt_cfg.get("primary") == "waggledance"
                and not _rt_cfg.get("compatibility_mode", True)
                and _AUTONOMY_AVAILABLE):
            try:
                _autonomy_svc = getattr(self, '_autonomy_service', None)
                if _autonomy_svc is None:
                    _svc = _AutonomyService(
                        profile=self.config.get("profile", "DEFAULT"))
                    _svc.start()
                    # Avoid race: only set if still None (first writer wins)
                    if getattr(self, '_autonomy_service', None) is None:
                        self._autonomy_service = _svc
                    else:
                        _svc.stop()
                    _autonomy_svc = self._autonomy_service
                result = _autonomy_svc.handle_query(
                    message, {"language": _detected_lang, "source": source})
                # Format response from autonomy result
                if result.get("error"):
                    # Autonomy failed — fall through to legacy path
                    log.warning("Autonomy runtime error, falling back: %s",
                                result["error"])
                else:
                    _r = result.get("result") or {}
                    response = _r.get("answer", str(_r)) if _r else str(result)
                    self._last_chat_message = message
                    self._last_chat_response = response
                    self._last_chat_method = f"autonomy_{result.get('quality_path', 'bronze')}"
                    self._last_chat_agent_id = result.get("capability", "autonomy")
                    self._last_model_result = result
                    _auto_ms = (time.perf_counter() - _chat_t0) * 1000
                    self.metrics.log_chat(
                        query=_original_message,
                        method=self._last_chat_method,
                        agent_id=self._last_chat_agent_id,
                        model_used="autonomy_runtime",
                        confidence=0.9 if result.get("quality_path") == "gold" else 0.7,
                        response_time_ms=_auto_ms,
                        route="autonomy", language=_detected_lang)
                    self._record_request_telemetry(
                        "autonomy", 0.9, _auto_ms, True, message)
                    return response
            except Exception as _auto_err:
                log.warning("Autonomy runtime unavailable, legacy fallback: %s",
                            _auto_err)

        # ═══ Phase 4: Detect user correction ("ei", "väärin", correction text) ═══
        _CORRECTION_WORDS = {"ei", "väärin", "wrong", "väärä", "virhe",
                             "korjaus", "eikä", "tarkoitin"}
        _CORRECTION_PHRASES = {"ei vaan", "ei ole", "oikea vastaus",
                               "tarkoitin että", "väärä vastaus"}
        if (self._last_chat_message and self._last_chat_response
                and hasattr(self, 'consciousness') and self.consciousness):
            msg_lower = message.lower()
            msg_words = set(msg_lower.split())
            _has_correction_word = bool(msg_words & _CORRECTION_WORDS)
            _has_correction_phrase = any(p in msg_lower for p in _CORRECTION_PHRASES)
            if (_has_correction_word or _has_correction_phrase) and len(message) > 5:
                self.consciousness.store_correction(
                    query=self._last_chat_message,
                    bad_answer=self._last_chat_response,
                    good_answer=message,
                    agent_id=self._last_chat_agent_id or "unknown")
                # Penalize agent trust
                if self.agent_levels and self._last_chat_agent_id:
                    try:
                        self.agent_levels.record_response(
                            agent_id=self._last_chat_agent_id,
                            agent_type="unknown",
                            was_correct=False, was_hallucination=False,
                            was_corrected=True)
                    except Exception as _lvl_err:
                        log.debug("agent_levels correction record failed: %s", _lvl_err)
                if self.monitor:
                    await self.monitor.system("📝 Korjaus tallennettu — opin virheestä!")
                await self._notify_ws("correction_stored", {
                    "query": self._last_chat_message[:100],
                    "good_answer": message[:100],
                })
                response = "Kiitos korjauksesta! Opin virheestä ja muistan tämän jatkossa."
                self._last_chat_message = message
                self._last_chat_response = response
                self._last_chat_method = ""
                self.metrics.log_chat(
                    query=_original_message, method="correction",
                    agent_id=self._last_chat_agent_id or "user",
                    model_used="none",
                    response_time_ms=(time.perf_counter() - _chat_t0) * 1000,
                    route="correction", language=_detected_lang)
                return response

        # ═══ Phase 4: Detect user teaching (after active_learning response) ═══
        if (self._last_chat_method == "active_learning"
                and hasattr(self, 'consciousness') and self.consciousness
                and self.consciousness.detect_user_teaching(
                    message, self._last_chat_method)):
            self.consciousness.learn_from_user(message, self._last_chat_message)
            if self.monitor:
                await self.monitor.system(f"🎓 Opittu käyttäjältä: {message[:60]}")
            await self._notify_ws("user_teaching", {
                "query": self._last_chat_message[:100],
                "teaching": message[:100],
            })
            response = f"Kiitos! Opin juuri: {message[:100]}. Muistan tämän jatkossa."
            self._last_chat_message = message
            self._last_chat_response = response
            self._last_chat_method = ""
            self.metrics.log_chat(
                query=_original_message, method="user_teaching",
                agent_id="user", model_used="none", confidence=0.9,
                response_time_ms=(time.perf_counter() - _chat_t0) * 1000,
                route="user_teaching", language=_detected_lang)
            return response

        # ═══ Direct datetime answers (no LLM needed) ═══
        _dt_now = datetime.now()
        _msg_l = message.lower()
        # Phrases that unambiguously ask for current time/date
        _TIME_PHRASES = {"paljonko kello", "mikä kello", "kellonaika", "what time",
                         "current time", "monako kello", "paljon kello", "kerro kello"}
        _DATE_PHRASES = {"mikä päivä", "monesko päivä", "päivämäärä", "what date",
                         "what day", "mikä tänään", "tänään on"}
        _is_time_q = any(w in _msg_l for w in _TIME_PHRASES)
        _is_date_q = any(w in _msg_l for w in _DATE_PHRASES)
        if _is_time_q or _is_date_q:
            _weekdays_fi = ["maanantai", "tiistai", "keskiviikko", "torstai",
                           "perjantai", "lauantai", "sunnuntai"]
            _weekday_fi = _weekdays_fi[_dt_now.weekday()]
            _time_str = _dt_now.strftime("%H.%M")  # Finnish format: 14.30
            _date_str = f"{_dt_now.day}.{_dt_now.month}.{_dt_now.year}"  # Finnish: 8.3.2026
            if _is_time_q and _is_date_q:
                response = f"Tänään on {_weekday_fi} {_date_str}, kello on {_time_str}."
            elif _is_time_q:
                response = f"Kello on {_time_str}."
            else:
                response = f"Tänään on {_weekday_fi} {_date_str}."
            self._last_chat_message = message
            self._last_chat_response = response
            self._last_chat_method = "datetime_direct"
            self.metrics.log_chat(
                query=_original_message, method="datetime_direct",
                agent_id="system", model_used="none", confidence=1.0,
                response_time_ms=(time.perf_counter() - _chat_t0) * 1000,
                route="datetime_direct", language=_detected_lang)
            if self.monitor:
                await self.monitor.system(f"🕐 Aikakysely: {response}")
            await self._notify_ws("chat_response", {
                "message": message, "response": response,
                "language": _detected_lang, "method": "datetime_direct"
            })
            return response

        # ═══ PHASE1 TASK4: Smart Router — confidence-based model selection ═══
        _pre = None
        if self.consciousness:
            try:
                _pre = self.consciousness.before_llm(message)
            except Exception as e:
                log.error(f"before_llm failed: {type(e).__name__}: {e}")
                _pre = None
            if _pre and _pre.handled:
                if self.monitor:
                    await self.monitor.system(
                        f"🧠 {_pre.method}: {_pre.answer[:80]}")
                await self._notify_ws("chat_response", {
                    "message": message, "response": _pre.answer,
                    "language": _detected_lang,
                    "method": _pre.method
                })
                # Phase 4: track for correction detection + episode
                self._last_chat_message = message
                self._last_chat_response = _pre.answer
                self._last_chat_method = _pre.method
                self._last_chat_agent_id = "consciousness"
                if self.consciousness:
                    self._last_episode_id = self.consciousness.store_episode(
                        query=message, response=_pre.answer,
                        prev_episode_id=self._last_episode_id,
                        quality=_pre.confidence)
                self.metrics.log_chat(
                    query=_original_message, method=_pre.method,
                    agent_id="consciousness", model_used="none",
                    confidence=_pre.confidence,
                    response_time_ms=(time.perf_counter() - _chat_t0) * 1000,
                    cache_hit=(_pre.method in ("hot_cache", "math")),
                    route=_pre.method, language=_detected_lang)
                return _pre.answer

            # Tier 2: memory_fast → llama1b formats answer with context
            if _pre and _pre.method == "memory_fast" and _pre.context and self.llm_heartbeat:
                _ans_lang = "Finnish" if _detected_lang == "fi" else "English"
                _fast_prompt = f"{_pre.context}\n\nQuestion: {message}\nAnswer concisely in {_ans_lang}:"
                try:
                    async with self.throttle:
                        _resp = await self.llm_heartbeat.generate(
                            _fast_prompt, max_tokens=200)
                    if _resp and not _resp.error and _resp.content:
                        response = _resp.content
                        if self.monitor:
                            await self.monitor.system(
                                f"🧠 SmartRouter: llama1b + context ({_pre.confidence:.0%})")
                        await self._notify_ws("chat_response", {
                            "message": message, "response": response,
                            "language": _detected_lang,
                            "method": "smart_router_fast"
                        })
                        # C1: auto-populate HotCache
                        self._populate_hot_cache(
                            _original_message, response,
                            score=_pre.confidence, source="memory_fast",
                            detected_lang=_detected_lang)
                        # Phase 4: track for correction detection + episode
                        self._last_chat_message = message
                        self._last_chat_response = response
                        self._last_chat_method = "memory_fast"
                        self._last_chat_agent_id = "llama1b"
                        if self.consciousness:
                            self._last_episode_id = self.consciousness.store_episode(
                                query=message, response=response,
                                prev_episode_id=self._last_episode_id,
                                quality=_pre.confidence)
                        self.metrics.log_chat(
                            query=_original_message, method="memory_fast",
                            agent_id="llama1b", model_used="llama3.2:1b",
                            confidence=_pre.confidence,
                            response_time_ms=(time.perf_counter() - _chat_t0) * 1000,
                            route="smart_router_fast",
                            language=_detected_lang)
                        return response
                except Exception as _fast_err:
                    log.warning("memory_fast route failed: %s", _fast_err)

        # Route once — all layer checks below share this result (avoids 3–4 calls)
        _route = None
        if getattr(self, 'smart_router_v2', None):
            try:
                _route = self.smart_router_v2.route(message)
            except Exception as _re:
                log.warning("SmartRouter v2 routing failed: %s", _re)

        # ═══ v1.18.0: Route Telemetry + Explainability ═══
        self._last_route_explanation = None
        if _route:
            try:
                from core.route_explainability import explain_route as _explain
                _mm_on = bool(getattr(self, 'micro_model', None))
                self._last_route_explanation = _explain(
                    query=message,
                    hot_cache_hit=False,
                    memory_score=getattr(_pre, 'confidence', 0.0) if _pre else 0.0,
                    micromodel_enabled=_mm_on,
                    matched_keywords=getattr(_route, 'matched_keywords', []) or [],
                ).to_dict()
            except Exception:
                pass

        # ═══ Model-based solver (SmartRouter v2 → SymbolicSolver) ═══
        if _route and getattr(self, 'symbolic_solver', None):
            try:
                if _route.layer == "model_based" and _route.decision_id:
                    _model_id = _route.model or _route.decision_id
                    _mr = self.symbolic_solver.solve_for_chat(_model_id, message)
                    if _mr.success and _mr.value is not None:
                        _lang = "fi" if _detected_lang == "fi" else "en"
                        response = _mr.to_natural_language(_lang)
                        # Add explainability trace if available
                        _expl_dict = None
                        if getattr(self, 'explainability', None):
                            try:
                                _sr = self.symbolic_solver.solve(
                                    _model_id,
                                    self.symbolic_solver.registry.get(_model_id)
                                    and {} or {})
                                # Use the original solver result for richer explanation
                                _sr_for_expl = self.symbolic_solver.solve(
                                    _model_id, _mr.inputs_used)
                                _expl = self.explainability.from_solver_result(
                                    _sr_for_expl,
                                    model_id=_model_id,
                                    model_name=_model_id.replace("_", " ").title())
                                _expl_dict = _expl.to_dict()
                            except Exception as _expl_err:
                                log.debug("Explainability trace failed: %s", _expl_err)
                        if self.monitor:
                            await self.monitor.system(
                                f"MODEL: {_model_id} -> {_mr.value:.4g} {_mr.unit}")
                        _ws_data = {
                            "message": message, "response": response,
                            "language": _detected_lang,
                            "method": "model_based",
                            "model_result": _mr.to_dict(),
                        }
                        if _expl_dict:
                            _ws_data["explanation"] = _expl_dict
                        await self._notify_ws("chat_response", _ws_data)
                        self._populate_hot_cache(
                            _original_message, response,
                            score=_mr.confidence, source=f"model_{_model_id}",
                            detected_lang=_detected_lang)
                        self._last_chat_message = message
                        self._last_chat_response = response
                        self._last_chat_method = "model_based"
                        self._last_chat_agent_id = f"solver_{_model_id}"
                        self._last_model_result = _mr.to_dict()
                        self._last_explanation = _expl_dict
                        if hasattr(self, 'consciousness') and self.consciousness:
                            self._last_episode_id = self.consciousness.store_episode(
                                query=message, response=response,
                                prev_episode_id=self._last_episode_id,
                                quality=_mr.confidence)
                        _model_ms = (time.perf_counter() - _chat_t0) * 1000
                        self.metrics.log_chat(
                            query=_original_message, method="model_based",
                            agent_id=f"solver_{_model_id}",
                            model_used="symbolic_solver",
                            confidence=_mr.confidence,
                            response_time_ms=_model_ms,
                            route="model_based", language=_detected_lang)
                        self._record_request_telemetry(
                            "model_based", _mr.confidence, _model_ms, True, message)
                        return response
            except Exception as _solver_err:
                log.warning("Model-based solver failed: %s", _solver_err)

        # ═══ Rule constraints (SmartRouter v2 → ConstraintEngine) ═══
        if _route and getattr(self, 'constraint_engine', None):
            try:
                if _route.layer == "rule_constraints" and _route.decision_id:
                    # Build context from available sensor data
                    _ctx = {}
                    if hasattr(self, 'sensor_hub') and self.sensor_hub:
                        try:
                            _ctx = self.sensor_hub.get_context() or {}
                        except Exception:
                            pass
                    _lang = "fi" if _detected_lang == "fi" else "en"
                    _cr = self.constraint_engine.evaluate_with_lang(_ctx, _lang)
                    if _cr.triggered_rules:
                        response = _cr.to_natural_language(_lang)
                    else:
                        if _lang == "fi":
                            response = "Kaikki tarkistukset OK -- ei aktiivisia varoituksia."
                        else:
                            response = "All checks OK -- no active warnings."
                    await self._notify_ws("chat_response", {
                        "message": message, "response": response,
                        "language": _detected_lang,
                        "method": "rule_constraints",
                    })
                    self._last_chat_message = message
                    self._last_chat_response = response
                    self._last_chat_method = "rule_constraints"
                    self._last_chat_agent_id = "constraint_engine"
                    self._last_model_result = None
                    self._last_explanation = _cr.to_dict() if hasattr(_cr, 'to_dict') else None
                    self.metrics.log_chat(
                        query=_original_message, method="rule_constraints",
                        agent_id="constraint_engine",
                        model_used="constraint_engine",
                        confidence=0.9,
                        response_time_ms=(time.perf_counter() - _chat_t0) * 1000,
                        route="rule_constraints", language=_detected_lang)
                    return response
            except Exception as _rule_err:
                log.warning("Rule constraints failed: %s", _rule_err)

        # ═══ Retrieval layer (SmartRouter v2 → FAISS direct search) ═══
        if _route and getattr(self, 'faiss_registry', None):
            try:
                if _route.layer == "retrieval":
                    _q_vec = None
                    if hasattr(self, 'consciousness') and self.consciousness:
                        import numpy as np
                        _q_raw = self.consciousness.embed.embed_query(message)
                        if _q_raw is not None:
                            _q_vec = np.array(_q_raw, dtype=np.float32)
                    if _q_vec is not None:
                        _ret_hits = []
                        for _col_name in ("bee_knowledge", "axioms", "agent_knowledge", "training_pairs"):
                            try:
                                _col = self.faiss_registry.get_or_create(_col_name)
                                if _col.count > 0:
                                    _ret_hits.extend(_col.search(_q_vec, k=5))
                            except Exception:
                                pass
                        _good = sorted(
                            [h for h in _ret_hits if h.score > 0.35],
                            key=lambda x: x.score, reverse=True)[:5]
                        if _good:
                            _lang = "fi" if _detected_lang == "fi" else "en"
                            _header = "Löysin seuraavat tiedot:\n\n" if _lang == "fi" else "Here is what I found:\n\n"
                            response = _header + "\n\n".join(
                                f"{i}. {h.text[:300]}" for i, h in enumerate(_good, 1))
                            self._last_chat_message = message
                            self._last_chat_response = response
                            self._last_chat_method = "retrieval"
                            self._last_chat_agent_id = "faiss_retrieval"
                            self._last_model_result = None
                            self._last_explanation = {
                                "method": "faiss_retrieval",
                                "hits": [{"doc_id": h.doc_id, "score": round(h.score, 4),
                                          "text": h.text[:120]} for h in _good],
                            }
                            self.metrics.log_chat(
                                query=_original_message, method="retrieval",
                                agent_id="faiss_retrieval", model_used="faiss",
                                confidence=_good[0].score,
                                response_time_ms=(time.perf_counter() - _chat_t0) * 1000,
                                route="retrieval", language=_detected_lang)
                            return response
            except Exception as _ret_err:
                log.warning("Retrieval layer failed: %s", _ret_err)

        # ═══ Statistical layer (SmartRouter v2 → system metrics summary) ═══
        if _route:
            try:
                if _route.layer == "statistical":
                    _lang = "fi" if _detected_lang == "fi" else "en"
                    _stats: dict = {}
                    # Collect metrics from available subsystems
                    if hasattr(self, 'consciousness') and self.consciousness:
                        try:
                            _mem_stats = self.consciousness.get_stats()
                            _stats["memory_entries"] = _mem_stats.get("total_memories", 0)
                            _stats["avg_importance"] = _mem_stats.get("avg_importance", 0)
                        except Exception:
                            pass
                    if hasattr(self, 'elastic_scaler') and self.elastic_scaler:
                        try:
                            _hw = self.elastic_scaler.summary()
                            _stats["hw_tier"] = _hw.get("tier", "unknown")
                            _stats["cpu_pct"] = _hw.get("cpu_pct", 0)
                            _stats["ram_pct"] = _hw.get("ram_pct", 0)
                        except Exception:
                            pass
                    if _stats:
                        if _lang == "fi":
                            _lines = ["Järjestelmätilastot:"]
                            if "memory_entries" in _stats:
                                _lines.append(f"• Muistimerkintöjä: {_stats['memory_entries']}")
                            if "hw_tier" in _stats:
                                _lines.append(f"• HW-taso: {_stats['hw_tier']}, CPU {_stats.get('cpu_pct',0):.0f}%, RAM {_stats.get('ram_pct',0):.0f}%")
                        else:
                            _lines = ["System statistics:"]
                            if "memory_entries" in _stats:
                                _lines.append(f"• Memory entries: {_stats['memory_entries']}")
                            if "hw_tier" in _stats:
                                _lines.append(f"• HW tier: {_stats['hw_tier']}, CPU {_stats.get('cpu_pct',0):.0f}%, RAM {_stats.get('ram_pct',0):.0f}%")
                        response = "\n".join(_lines)
                        self._last_chat_message = message
                        self._last_chat_response = response
                        self._last_chat_method = "statistical"
                        self._last_chat_agent_id = "stats_engine"
                        self._last_model_result = None
                        self._last_explanation = {"method": "statistical", "stats": _stats}
                        self.metrics.log_chat(
                            query=_original_message, method="statistical",
                            agent_id="stats_engine", model_used="none",
                            confidence=0.8,
                            response_time_ms=(time.perf_counter() - _chat_t0) * 1000,
                            route="statistical", language=_detected_lang)
                        return response
            except Exception as _stat_err:
                log.warning("Statistical layer failed: %s", _stat_err)

        # ═══ FI→EN käännös (~2ms) ═══
        if _detected_lang == "fi" and self.translation_proxy:
            try:
                _fi_en_result = self.translation_proxy.fi_to_en(message, force_opus=True)
                if _fi_en_result.coverage >= 0.5 and _fi_en_result.method != "passthrough":
                    _translation_used = True
                    _en_message = _fi_en_result.text
                    if self.monitor:
                        await self.monitor.system(
                            f"🔄 FI→EN ({_fi_en_result.method}, "
                            f"{_fi_en_result.latency_ms:.1f}ms, "
                            f"{_fi_en_result.coverage:.0%}): {_en_message[:80]}")
                else:
                    _en_message = message
            except Exception as e:
                log.error(f"FI->EN translation failed: {type(e).__name__}: {e}")
                _en_message = message
        else:
            _en_message = message

        # Viesti agentille
        _routed_message = _en_message if (_translation_used or _detected_lang == "en") else message
        _use_en_prompts = _translation_used or _detected_lang == "en"

        await self.memory.store_memory(
            content=f"Käyttäjä sanoi: {message}",
            agent_id="user",
            memory_type="observation",
            importance=0.6
        )

        context = await self.memory.get_full_context(_original_message)
        msg_lower = _original_message.lower()  # Reititys aina FI-sanoilla

        # ═══ FAISS Semantic Context Enrichment ═══
        _faiss_reg = getattr(self, 'faiss_registry', None)
        if _faiss_reg and hasattr(self, 'consciousness') and self.consciousness:
            try:
                import numpy as np
                _q_vec = self.consciousness.embed.embed_query(_en_message or _original_message)
                if _q_vec is not None:
                    _vec = np.array(_q_vec, dtype=np.float32)
                    _faiss_hits = []
                    for _col_name in ("bee_knowledge", "axioms", "agent_knowledge"):
                        try:
                            _col = _faiss_reg.get_or_create(_col_name)
                            if _col.count > 0:
                                _faiss_hits.extend(_col.search(_vec, k=3))
                        except Exception:
                            pass
                    _good_hits = sorted(
                        [h for h in _faiss_hits if h.score > 0.35],
                        key=lambda x: x.score, reverse=True)[:5]
                    if _good_hits:
                        _faiss_ctx = "DOMAIN KNOWLEDGE:\n" + "\n".join(
                            f"- {h.text[:200]}" for h in _good_hits)
                        context = f"{_faiss_ctx}\n\n{context}" if context else _faiss_ctx
            except Exception as _fe:
                log.debug("FAISS enrichment skipped: %s", _fe)

        # Multi-agent check (sama kuin ennen)
        multi_keywords = ["kaikki", "tilanne", "yhteenveto", "status", "yleiskatsaus"]
        is_multi = any(w in msg_lower for w in multi_keywords)
        if is_multi:
            return await self._multi_agent_collaboration(message, {}, context)

        # ── Routing rules (YAMLBridge + fallback) ─────────────
        if self.spawner and hasattr(self.spawner, 'yaml_bridge'):
            routing_rules = self.spawner.yaml_bridge.get_routing_rules()
        else:
            routing_rules = {}
        routing_rules.setdefault("hacker", ["bugi", "refaktor", "koodi", "tietoturva"])
        routing_rules.setdefault("oracle", ["haku", "etsi", "tutki", "google", "claude"])

        # ── Reititysvalinta ───────────────────────────────────
        if self._swarm_enabled and self.scheduler and self.scheduler.agent_count > 0:
            delegate_to, best_score = self._swarm_route(msg_lower, routing_rules)
        else:
            delegate_to, best_score = self._legacy_route(msg_lower, routing_rules)

        if delegate_to and best_score > 0:
            response = await self._delegate_to_agent(
                delegate_to, _routed_message, context, msg_lower,
                translation_used=_translation_used, detected_lang=_detected_lang,
                use_en_prompts=_use_en_prompts, fi_en_result=_fi_en_result
            )
            # C1: auto-populate HotCache
            self._populate_hot_cache(
                _original_message, response,
                score=0.75, source=f"agent_{delegate_to}",
                detected_lang=_detected_lang)
            # Phase 4: track for correction detection + episode
            self._last_chat_message = message
            self._last_chat_response = response
            self._last_chat_method = (_pre.method
                                      if _pre else "")
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
                confidence=_del_conf,
                response_time_ms=_del_ms,
                route="agent_delegate", language=_detected_lang,
                translated=_translation_used)
            self._record_request_telemetry(
                "delegate", _del_conf, _del_ms, True, message)
            return response
        else:
            # AUDIT FIX: Negatiiviset avainsanat → pakota delegointi
            # Jos viesti sisältää spesialistin termejä, ÄLÄ anna masterille
            for neg_kw in MASTER_NEGATIVE_KEYWORDS:
                if neg_kw in msg_lower:
                    # Yritä löytää spesialisti negatiivisella avainsanalla
                    for agent_type, keywords in routing_rules.items():
                        if neg_kw in keywords or any(
                            neg_kw in kw for kw in keywords
                        ):
                            response = await self._delegate_to_agent(
                                agent_type, message, context, msg_lower,
                                translation_used=_translation_used, detected_lang=_detected_lang,
                                use_en_prompts=_use_en_prompts, fi_en_result=_fi_en_result
                            )
                            # C1: auto-populate HotCache
                            self._populate_hot_cache(
                                _original_message, response,
                                score=0.75, source=f"agent_{agent_type}",
                                detected_lang=_detected_lang)
                            # Phase 4: track
                            self._last_chat_message = message
                            self._last_chat_response = response
                            self._last_chat_method = (_pre.method
                                                      if _pre else "")
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
                                route="agent_delegate",
                                language=_detected_lang,
                                translated=_translation_used)
                            return response
                    break  # Ei löytynyt → anna masterille

            # Fallback: Master (Swarm Queen)
            _orig_master_sys = None
            if _use_en_prompts and "hivemind" in AGENT_EN_PROMPTS:
                _orig_master_sys = self.master_agent.system_prompt
                from datetime import datetime as _dt
                # Tietoisuus: muistikonteksti
                _consciousness_context = ""
                if self.consciousness:
                    _ctx_q = _en_message if _translation_used else message
                    _consciousness_context = self.consciousness.get_context(_ctx_q)
                    if _consciousness_context:
                        _consciousness_context = "\n" + _consciousness_context
                    # Phase 4: inject corrections context
                    _corrections_ctx = self.consciousness.check_previous_corrections(message)
                    if _corrections_ctx:
                        _consciousness_context += (
                            "\n\nCORRECTIONS (avoid repeating these mistakes):\n"
                            + _corrections_ctx)
                self.master_agent.system_prompt = f"Date: {_dt.now():%Y-%m-%d %H:%M}. " + AGENT_EN_PROMPTS["hivemind"] + _consciousness_context
            try:
                with self._enriched_prompt(self.master_agent, knowledge_max_chars=2000) as _enriched_master:
                    response = await _enriched_master.think(_routed_message, context)
            except Exception as e:
                log.error(f"Master agent think failed: {type(e).__name__}: {e}")
                response = "Anteeksi, en pystynyt vastaamaan juuri nyt. Yrita hetken kuluttua uudelleen."
            finally:
                if _orig_master_sys is not None:
                    self.master_agent.system_prompt = _orig_master_sys
            if self.en_validator and _use_en_prompts:
                _val = self.en_validator.validate(response)
                if _val.was_corrected:
                    response = _val.corrected

            if _translation_used and self.translation_proxy:
                try:
                    _en_fi = self.translation_proxy.en_to_fi(response, force_opus=True)
                    if _en_fi.method != "passthrough":
                        response = _en_fi.text
                except Exception as e:
                    log.error(f"EN->FI translation failed: {type(e).__name__}: {e}")
                    # Keep English response as fallback
            # Tietoisuus: hallusinaatio + oppiminen
            _hall = None
            _quality = 0.7
            if self.consciousness:
                try:
                    _hall = self.consciousness.check_hallucination(message, response)
                    if _hall.is_suspicious and self.monitor:
                        await self.monitor.system(f"⚠️ Hallusinaatio? {_hall.reason}")
                    _quality = _hall.relevance if not _hall.is_suspicious else 0.3
                    self.consciousness.learn_conversation(message, response, quality_score=_quality)
                except Exception as e:
                    log.debug(f"Hallucination check failed: {e}")

            # C1: auto-populate HotCache
            self._populate_hot_cache(
                _original_message, response,
                score=_quality, source="master",
                detected_lang=_detected_lang)
            await self._notify_ws("chat_response", {
                "message": message, "response": response,
                "language": _detected_lang, "translated": _translation_used
            })
            # Phase 4: track for correction detection + episode
            self._last_chat_message = message
            self._last_chat_response = response
            self._last_chat_method = (_pre.method
                                      if _pre else "")
            self._last_chat_agent_id = "master"
            if hasattr(self, 'consciousness') and self.consciousness:
                self._last_episode_id = self.consciousness.store_episode(
                    query=message, response=response,
                    prev_episode_id=self._last_episode_id,
                    quality=_quality)
            _master_ms = (time.perf_counter() - _chat_t0) * 1000
            self.metrics.log_chat(
                query=_original_message, method="master",
                agent_id="master", model_used="phi4-mini",
                confidence=_quality,
                was_hallucination=bool(_hall and _hall.is_suspicious),
                response_time_ms=_master_ms,
                route="llm_master", language=_detected_lang,
                translated=_translation_used)
            self._record_request_telemetry(
                "llm", _quality, _master_ms, True, message, was_fallback=True)
            return response

    def _record_request_telemetry(self, route_type: str, confidence: float,
                                    latency_ms: float, success: bool,
                                    query: str, was_fallback: bool = False):
        """v1.18.0: Record route telemetry and low-confidence ledger entries."""
        try:
            _get_route_telemetry().record(route_type, latency_ms, success, was_fallback)
        except Exception:
            pass
        try:
            if confidence < 0.6:
                _get_learning_ledger().log(
                    "low_confidence_query",
                    agent_id=route_type,
                    query=query[:500],
                    confidence=confidence,
                    route=route_type,
                    latency_ms=round(latency_ms, 1),
                )
        except Exception:
            pass

    def _swarm_route(self, msg_lower: str, routing_rules: dict) -> tuple:
        """
        FIX-2: Swarm-aware routing pipeline.
        (A) Luo task meta -> (B) Top-K shortlist -> (C) keyword-score shortlistille
        (D) Fallback legacy-routingiin jos shortlist tyhjä.
        """
        # (A) Task meta: poimi sanat tageiksi
        task_tags = [w for w in msg_lower.split() if len(w) > 2]
        task_type = "user_question"

        # (B) Shortlist Top-K schedulerista (HALPA: ei LLM-kutsuja)
        candidates = self.scheduler.select_candidates(
            task_type=task_type,
            task_tags=task_tags,
            routing_rules=routing_rules,
            top_k=self.scheduler.top_k,
        )

        if not candidates:
            # Fallback: vanha reititys
            return self._legacy_route(msg_lower, routing_rules)

        # (C) Keyword-score VAIN shortlistatuille (ei kaikille 50:lle)
        candidate_types = set()
        for cid in candidates:
            score = self.scheduler._scores.get(cid)
            if score:
                candidate_types.add(score.agent_type)

        delegate_to = None
        best_score = 0
        for agent_type in candidate_types:
            keywords = routing_rules.get(agent_type, [])
            weighted = WEIGHTED_ROUTING.get(agent_type)
            if weighted:
                score = (
                    sum(PRIMARY_WEIGHT for kw in weighted.get("primary", [])
                        if kw in msg_lower) +
                    sum(SECONDARY_WEIGHT for kw in weighted.get("secondary", [])
                        if kw in msg_lower)
                )
            else:
                score = sum(1 for kw in keywords if kw in msg_lower)

            if score > best_score:
                best_score = score
                delegate_to = agent_type

        if delegate_to and best_score > 0:
            return delegate_to, best_score

        # (D) Shortlist ei tuottanut keyword-matcheja → fallback legacy
        return self._legacy_route(msg_lower, routing_rules)

    def _legacy_route(self, msg_lower: str, routing_rules: dict) -> tuple:
        """Vanha reititys: käy KAIKKI agentit läpi keyword-scorella."""
        delegate_to = None
        best_score = 0
        for agent_type, keywords in routing_rules.items():
            weighted = WEIGHTED_ROUTING.get(agent_type)
            if weighted:
                score = (
                    sum(PRIMARY_WEIGHT for kw in weighted.get("primary", [])
                        if kw in msg_lower) +
                    sum(SECONDARY_WEIGHT for kw in weighted.get("secondary", [])
                        if kw in msg_lower)
                )
            else:
                score = sum(1 for kw in keywords if kw in msg_lower)

            if score > best_score:
                best_score = score
                delegate_to = agent_type

        return delegate_to, best_score

    async def _delegate_to_agent(self, delegate_to: str, message: str,
                                  context: str, msg_lower: str, *,
                                  translation_used: bool = False,
                                  detected_lang: str = "fi",
                                  use_en_prompts: bool = False,
                                  fi_en_result=None) -> str:
        """Delegoi viesti valitulle agentille. FIX-1: prompt-restore korjattu."""
        agents = self.spawner.get_agents_by_type(delegate_to)
        if not agents:
            try:
                agent = await self.spawner.spawn(delegate_to)
                # FIX-4: auto-register scheduleriin
                if agent:
                    self.register_agent_to_scheduler(agent)
            except Exception:
                agent = None
        else:
            agent = agents[0]

        if not agent:
            return await self.master_agent.think(message, context)

        _t0 = time.monotonic()

        # ═══ v1.16.0: Check for learning engine prompt override ═══
        _orig_agent_sys = None
        _override = None
        if hasattr(self, 'learning') and self.learning:
            _atype = getattr(agent, 'agent_type', getattr(agent, 'type', ''))
            _override = self.learning.get_prompt_override(_atype)
        if _override:
            _orig_agent_sys = agent.system_prompt
            from datetime import datetime as _dt
            agent.system_prompt = f"Date: {_dt.now():%Y-%m-%d %H:%M}. " + _override
        elif use_en_prompts:
            _atype = getattr(agent, 'agent_type', getattr(agent, 'type', ''))
            if _atype in AGENT_EN_PROMPTS:
                _orig_agent_sys = agent.system_prompt
                from datetime import datetime as _dt
                agent.system_prompt = f"Date: {_dt.now():%Y-%m-%d %H:%M}. " + AGENT_EN_PROMPTS[_atype]

        # FIX-1: Yksi context manager hoitaa kaiken injektoinnin ja palautuksen
        with self._enriched_prompt(agent, inject_date=True,
                                    inject_knowledge=True,
                                    knowledge_max_chars=2000) as _enriched_agent:
            # Phase 4: Failure Twin — inject agent-specific error warnings
            if hasattr(self, 'consciousness') and self.consciousness:
                _ft_warning = self.consciousness.get_agent_error_patterns(
                    agent_id=getattr(agent, 'id', ''),
                    query=message)
                if _ft_warning:
                    _enriched_agent.system_prompt = _enriched_agent.system_prompt + "\n\n" + _ft_warning

            # MAGMA: Enrich with cross-agent provenance if available
            _xs = getattr(self, '_cross_search', None)
            if _xs and hasattr(self, 'consciousness') and self.consciousness:
                try:
                    _emb = self.consciousness.embed.embed_query(message)
                    if _emb:
                        _cross_results = _xs.search_with_provenance(
                            _emb, top_k=3)
                        if _cross_results:
                            _cross_ctx = "\n".join(
                                f"- [{r.get('agent_id', '?')}] {r.get('text', '')[:120]}"
                                for r in _cross_results[:3])
                            _enriched_agent.system_prompt = (
                                _enriched_agent.system_prompt
                                + "\n\nOTHER AGENTS' KNOWLEDGE:\n" + _cross_ctx)
                except Exception:
                    pass

            try:
                # Merkitse task alkaneeksi schedulerille
                if self.scheduler:
                    self.scheduler.record_task_start(agent.id)
                async with self.throttle:
                    response = await _enriched_agent.think(message, context)
            except Exception as e:
                response = f"[Virhe: {e}]"
            finally:
                if self.scheduler:
                    self.scheduler.record_task_end(agent.id)

        # Prompt on nyt VARMASTI palautunut alkuperäiseen ↑

        _elapsed = (time.monotonic() - _t0) * 1000
        self._report_llm_result(_elapsed, True, self.llm.model)

        # KORJAUS K10: Validoi vastaus ennen palkitsemista
        if self._is_valid_response(response):
            if self.token_economy:
                await self.token_economy.reward(agent.id, "question_answered")
            if self.scheduler:
                self.scheduler.record_task_result(
                    agent.id, success=True, latency_ms=_elapsed
                )
            # LearningEngine: arvioi chat-vastaus
            if self.learning:
                self.learning.submit_for_evaluation(
                    agent_id=agent.id,
                    agent_type=agent.agent_type,
                    system_prompt=getattr(agent, 'system_prompt', '')[:500],
                    prompt=message,
                    response=response,
                )
            # Phase 3: Record for agent levels
            if self.agent_levels:
                try:
                    self.agent_levels.record_response(
                        agent_id=agent.id,
                        agent_type=agent.agent_type,
                        was_correct=True,
                        was_hallucination=False)
                except Exception as _lvl_err:
                    log.debug("agent_levels record failed: %s", _lvl_err)
        else:
            if self.scheduler:
                self.scheduler.record_task_result(
                    agent.id, success=False, latency_ms=_elapsed
                )

        # Palauta FI-prompt
        if _orig_agent_sys is not None:
            agent.system_prompt = _orig_agent_sys

        # ═══ EN Validator: standardisoi terminologia ═══
        if self.en_validator and use_en_prompts:
            _val = self.en_validator.validate(response)
            if _val.was_corrected:
                if self.monitor:
                    await self.monitor.system(
                        f"🔍 EN-fix ({_val.method}, {_val.latency_ms:.1f}ms, "
                        f"{_val.correction_count} korjausta): "
                        f"{_val.corrections[:3]}")
                response = _val.corrected

        # ═══ EN→FI käännös ═══
        if translation_used and self.translation_proxy:
            _en_fi = self.translation_proxy.en_to_fi(response, force_opus=True)
            if _en_fi.method != "passthrough":
                if self.monitor:
                    _src_ms = getattr(fi_en_result, 'latency_ms', 0) if fi_en_result else 0
                    await self.monitor.system(
                        f"🔄 EN→FI ({_en_fi.method}, {_en_fi.latency_ms:.1f}ms, "
                        f"total: {_src_ms + _en_fi.latency_ms:.1f}ms)")
                response = _en_fi.text

        await self._notify_ws("delegated", {
            "agent": agent.name, "type": delegate_to, "response": response,
            "language": detected_lang,
            "translated": translation_used
        })
        return f"[{agent.name}] {response}"

    # ── Vastauksen validointi (KORJAUS K10) ──────────────────

    def _is_valid_response(self, response: str) -> bool:
        """Tarkista onko vastaus kelvollinen ennen muistiin tallennusta."""
        if not response or not response.strip():
            return False
        if len(response.strip()) < 5:
            return False
        bad_markers = ["[LLM-virhe", "[Ollama ei vastaa", "error", "503"]
        for marker in bad_markers:
            if marker in response[:50]:
                return False
        return True

    def _populate_hot_cache(self, query: str, response: str,
                             score: float = 0.75, source: str = "chat",
                             detected_lang: str = "fi"):
        """C1: Auto-populate HotCache with successful Finnish answers."""
        if not (self.consciousness and self.consciousness.hot_cache):
            return
        if not self._is_valid_response(response):
            return
        if detected_lang != "fi":
            return
        if score < 0.6:
            return
        try:
            self.consciousness.hot_cache.put(query, response, score, source=source)
        except Exception:
            pass

    # ── Multi-agent ─────────────────────────────────────────

    async def _multi_agent_collaboration(self, mission: str,
                                          plan: dict, context: str) -> str:
        army = await self.spawner.spawn_dynamic_army(mission)
        if not army:
            return await self.master_agent.think(mission, context)

        tasks = await self.memory.get_tasks(status="pending")
        agent_tasks = {}
        for task in tasks:
            if task.get("assigned_agent"):
                agent_tasks.setdefault(task["assigned_agent"], []).append(task)

        async_tasks = []
        for agent in army:
            agent_specific_tasks = agent_tasks.get(agent.id, [])
            if agent_specific_tasks:
                for task in agent_specific_tasks:
                    async_tasks.append(agent.execute_task(task))
            else:
                async_tasks.append(agent.think(mission, context))

        results = []
        if async_tasks:
            completed = await asyncio.gather(*async_tasks, return_exceptions=True)
            results = [str(r) for r in completed if not isinstance(r, Exception)]

        all_results = "\n\n".join(results)
        synthesis = await self.master_agent.think(
            f"Missio: {mission}\n\nAgenttien tulokset:\n{all_results}\n\n"
            f"Syntetisoi kokonaisvastaus.",
            context
        )
        return synthesis
