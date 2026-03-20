"""Chat routing engine — SmartRouter v2, swarm routing, layer-based routing.

Extracted from chat_handler.py (v3.3 refactor).
Extends ChatRouter (core/chat_router.py) with the full routing pipeline.
"""
import logging
import time
from datetime import datetime
from typing import Optional, Tuple

from core.hive_routing import (
    WEIGHTED_ROUTING, PRIMARY_WEIGHT, SECONDARY_WEIGHT,
    MASTER_NEGATIVE_KEYWORDS, AGENT_EN_PROMPTS,
)

log = logging.getLogger("hivemind")


class ChatRoutingEngine:
    """Full routing pipeline: consciousness → SmartRouter v2 → swarm → legacy.

    Handles all layer-based routing (model_based, rule_constraints,
    retrieval, statistical) and keyword-based agent selection.
    """

    def __init__(self, hive):
        self._hive = hive

    async def run_consciousness_precheck(self, message: str, original_message: str,
                                         detected_lang: str, chat_t0: float):
        """Run consciousness before_llm + memory_fast route. Returns response or None."""
        hive = self._hive
        if not getattr(hive, 'consciousness', None):
            return None, None

        _pre = None
        try:
            _pre = hive.consciousness.before_llm(message)
        except Exception as e:
            log.error(f"before_llm failed: {type(e).__name__}: {e}")
            return None, None

        if _pre and _pre.handled:
            if getattr(hive, 'monitor', None):
                await hive.monitor.system(f"🧠 {_pre.method}: {_pre.answer[:80]}")
            await hive._notify_ws("chat_response", {
                "message": message, "response": _pre.answer,
                "language": detected_lang, "method": _pre.method
            })
            hive._last_chat_message = message
            hive._last_chat_response = _pre.answer
            hive._last_chat_method = _pre.method
            hive._last_chat_agent_id = "consciousness"
            if hive.consciousness:
                hive._last_episode_id = hive.consciousness.store_episode(
                    query=message, response=_pre.answer,
                    prev_episode_id=hive._last_episode_id,
                    quality=_pre.confidence)
            hive.metrics.log_chat(
                query=original_message, method=_pre.method,
                agent_id="consciousness", model_used="none",
                confidence=_pre.confidence,
                response_time_ms=(time.perf_counter() - chat_t0) * 1000,
                cache_hit=(_pre.method in ("hot_cache", "math")),
                route=_pre.method, language=detected_lang)
            return _pre.answer, _pre

        # Tier 2: memory_fast → llama1b formats answer
        if (_pre and _pre.method == "memory_fast" and _pre.context
                and getattr(hive, 'llm_heartbeat', None)):
            _ans_lang = "Finnish" if detected_lang == "fi" else "English"
            _fast_prompt = f"{_pre.context}\n\nQuestion: {message}\nAnswer concisely in {_ans_lang}:"
            try:
                async with hive.throttle:
                    _resp = await hive.llm_heartbeat.generate(
                        _fast_prompt, max_tokens=200)
                if _resp and not _resp.error and _resp.content:
                    response = _resp.content
                    if getattr(hive, 'monitor', None):
                        await hive.monitor.system(
                            f"🧠 SmartRouter: llama1b + context ({_pre.confidence:.0%})")
                    await hive._notify_ws("chat_response", {
                        "message": message, "response": response,
                        "language": detected_lang, "method": "smart_router_fast"
                    })
                    hive._last_chat_message = message
                    hive._last_chat_response = response
                    hive._last_chat_method = "memory_fast"
                    hive._last_chat_agent_id = "llama1b"
                    if hive.consciousness:
                        hive._last_episode_id = hive.consciousness.store_episode(
                            query=message, response=response,
                            prev_episode_id=hive._last_episode_id,
                            quality=_pre.confidence)
                    hive.metrics.log_chat(
                        query=original_message, method="memory_fast",
                        agent_id="llama1b", model_used="llama3.2:1b",
                        confidence=_pre.confidence,
                        response_time_ms=(time.perf_counter() - chat_t0) * 1000,
                        route="smart_router_fast", language=detected_lang)
                    return response, _pre
            except Exception as _fast_err:
                log.warning("memory_fast route failed: %s", _fast_err)

        return None, _pre

    def get_smart_route(self, message: str, pre):
        """Get SmartRouter v2 route + explainability."""
        hive = self._hive
        _route = None
        if getattr(hive, 'smart_router_v2', None):
            try:
                _route = hive.smart_router_v2.route(message)
            except Exception as e:
                log.warning("SmartRouter v2 routing failed: %s", e)

        # Route explainability
        hive._last_route_explanation = None
        if _route:
            try:
                from core.route_explainability import explain_route as _explain
                _mm_on = bool(getattr(hive, 'micro_model', None))
                hive._last_route_explanation = _explain(
                    query=message,
                    hot_cache_hit=False,
                    memory_score=getattr(pre, 'confidence', 0.0) if pre else 0.0,
                    micromodel_enabled=_mm_on,
                    matched_keywords=getattr(_route, 'matched_keywords', []) or [],
                ).to_dict()
            except Exception:
                pass

        return _route

    async def try_model_based(self, message: str, route, original_message: str,
                              detected_lang: str, chat_t0: float, pre) -> Optional[str]:
        """Try model-based solver route. Returns response or None."""
        hive = self._hive
        if not (route and getattr(hive, 'symbolic_solver', None)):
            return None
        try:
            if route.layer != "model_based" or not route.decision_id:
                return None
            _model_id = route.model or route.decision_id
            _mr = hive.symbolic_solver.solve_for_chat(_model_id, message)
            if not (_mr.success and _mr.value is not None):
                return None

            _lang = "fi" if detected_lang == "fi" else "en"
            response = _mr.to_natural_language(_lang)

            # Explainability trace
            _expl_dict = None
            if getattr(hive, 'explainability', None):
                try:
                    _sr_for_expl = hive.symbolic_solver.solve(
                        _model_id, _mr.inputs_used)
                    _expl = hive.explainability.from_solver_result(
                        _sr_for_expl,
                        model_id=_model_id,
                        model_name=_model_id.replace("_", " ").title())
                    _expl_dict = _expl.to_dict()
                except Exception as e:
                    log.debug("Explainability trace failed: %s", e)

            if getattr(hive, 'monitor', None):
                await hive.monitor.system(
                    f"MODEL: {_model_id} -> {_mr.value:.4g} {_mr.unit}")
            _ws_data = {
                "message": message, "response": response,
                "language": detected_lang, "method": "model_based",
                "model_result": _mr.to_dict(),
            }
            if _expl_dict:
                _ws_data["explanation"] = _expl_dict
            await hive._notify_ws("chat_response", _ws_data)

            hive._last_chat_message = message
            hive._last_chat_response = response
            hive._last_chat_method = "model_based"
            hive._last_chat_agent_id = f"solver_{_model_id}"
            hive._last_model_result = _mr.to_dict()
            hive._last_explanation = _expl_dict
            if hasattr(hive, 'consciousness') and hive.consciousness:
                hive._last_episode_id = hive.consciousness.store_episode(
                    query=message, response=response,
                    prev_episode_id=hive._last_episode_id,
                    quality=_mr.confidence)
            _model_ms = (time.perf_counter() - chat_t0) * 1000
            hive.metrics.log_chat(
                query=original_message, method="model_based",
                agent_id=f"solver_{_model_id}",
                model_used="symbolic_solver",
                confidence=_mr.confidence,
                response_time_ms=_model_ms,
                route="model_based", language=detected_lang)
            return response
        except Exception as e:
            log.warning("Model-based solver failed: %s", e)
            return None

    async def try_rule_constraints(self, message: str, route,
                                   original_message: str,
                                   detected_lang: str, chat_t0: float) -> Optional[str]:
        """Try rule constraints route. Returns response or None."""
        hive = self._hive
        if not (route and getattr(hive, 'constraint_engine', None)):
            return None
        try:
            if route.layer != "rule_constraints" or not route.decision_id:
                return None
            _ctx = {}
            if hasattr(hive, 'sensor_hub') and hive.sensor_hub:
                try:
                    _ctx = hive.sensor_hub.get_context() or {}
                except Exception:
                    pass
            _lang = "fi" if detected_lang == "fi" else "en"
            _cr = hive.constraint_engine.evaluate_with_lang(_ctx, _lang)

            if _cr.triggered_rules:
                response = _cr.to_natural_language(_lang)
            elif getattr(route, 'confidence', 1.0) < 0.3:
                return None  # Low confidence, fall through
            else:
                response = ("Kaikki tarkistukset OK -- ei aktiivisia varoituksia."
                            if _lang == "fi"
                            else "All checks OK -- no active warnings.")

            await hive._notify_ws("chat_response", {
                "message": message, "response": response,
                "language": detected_lang, "method": "rule_constraints",
            })
            hive._last_chat_message = message
            hive._last_chat_response = response
            hive._last_chat_method = "rule_constraints"
            hive._last_chat_agent_id = "constraint_engine"
            hive._last_model_result = None
            hive._last_explanation = _cr.to_dict() if hasattr(_cr, 'to_dict') else None
            hive.metrics.log_chat(
                query=original_message, method="rule_constraints",
                agent_id="constraint_engine",
                model_used="constraint_engine",
                confidence=0.9,
                response_time_ms=(time.perf_counter() - chat_t0) * 1000,
                route="rule_constraints", language=detected_lang)
            return response
        except Exception as e:
            log.warning("Rule constraints failed: %s", e)
            return None

    async def try_retrieval(self, message: str, route,
                            original_message: str,
                            detected_lang: str, chat_t0: float) -> Optional[str]:
        """Try FAISS retrieval route. Returns response or None."""
        hive = self._hive
        if not (route and getattr(hive, 'faiss_registry', None)):
            return None
        try:
            if route.layer != "retrieval":
                return None
            _q_vec = None
            if hasattr(hive, 'consciousness') and hive.consciousness:
                import numpy as np
                _q_raw = hive.consciousness.embed.embed_query(message)
                if _q_raw is not None:
                    _q_vec = np.array(_q_raw, dtype=np.float32)
            if _q_vec is None:
                return None

            _ret_hits = []
            for _col_name in ("bee_knowledge", "axioms", "agent_knowledge", "training_pairs"):
                try:
                    _col = hive.faiss_registry.get_or_create(_col_name)
                    if _col.count > 0:
                        _ret_hits.extend(_col.search(_q_vec, k=5))
                except Exception:
                    pass
            _good = sorted(
                [h for h in _ret_hits if h.score > 0.35],
                key=lambda x: x.score, reverse=True)[:5]
            if not _good:
                return None

            _lang = "fi" if detected_lang == "fi" else "en"
            _header = "Löysin seuraavat tiedot:\n\n" if _lang == "fi" else "Here is what I found:\n\n"
            response = _header + "\n\n".join(
                f"{i}. {h.text[:300]}" for i, h in enumerate(_good, 1))
            hive._last_chat_message = message
            hive._last_chat_response = response
            hive._last_chat_method = "retrieval"
            hive._last_chat_agent_id = "faiss_retrieval"
            hive._last_model_result = None
            hive._last_explanation = {
                "method": "faiss_retrieval",
                "hits": [{"doc_id": h.doc_id, "score": round(h.score, 4),
                          "text": h.text[:120]} for h in _good],
            }
            hive.metrics.log_chat(
                query=original_message, method="retrieval",
                agent_id="faiss_retrieval", model_used="faiss",
                confidence=_good[0].score,
                response_time_ms=(time.perf_counter() - chat_t0) * 1000,
                route="retrieval", language=detected_lang)
            return response
        except Exception as e:
            log.warning("Retrieval layer failed: %s", e)
            return None

    async def try_statistical(self, message: str, route,
                              original_message: str,
                              detected_lang: str, chat_t0: float) -> Optional[str]:
        """Try statistical summary route. Returns response or None."""
        hive = self._hive
        if not route:
            return None
        try:
            if route.layer != "statistical":
                return None
            _lang = "fi" if detected_lang == "fi" else "en"
            _stats = {}
            if hasattr(hive, 'consciousness') and hive.consciousness:
                try:
                    _mem_stats = hive.consciousness.get_stats()
                    _stats["memory_entries"] = _mem_stats.get("total_memories", 0)
                    _stats["avg_importance"] = _mem_stats.get("avg_importance", 0)
                except Exception:
                    pass
            if hasattr(hive, 'elastic_scaler') and hive.elastic_scaler:
                try:
                    _hw = hive.elastic_scaler.summary()
                    _stats["hw_tier"] = _hw.get("tier", "unknown")
                    _stats["cpu_pct"] = _hw.get("cpu_pct", 0)
                    _stats["ram_pct"] = _hw.get("ram_pct", 0)
                except Exception:
                    pass
            if not _stats:
                return None

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
            hive._last_chat_message = message
            hive._last_chat_response = response
            hive._last_chat_method = "statistical"
            hive._last_chat_agent_id = "stats_engine"
            hive._last_model_result = None
            hive._last_explanation = {"method": "statistical", "stats": _stats}
            hive.metrics.log_chat(
                query=original_message, method="statistical",
                agent_id="stats_engine", model_used="none",
                confidence=0.8,
                response_time_ms=(time.perf_counter() - chat_t0) * 1000,
                route="statistical", language=detected_lang)
            return response
        except Exception as e:
            log.warning("Statistical layer failed: %s", e)
            return None

    def swarm_route(self, msg_lower: str, routing_rules: dict) -> Tuple[Optional[str], int]:
        """Swarm-aware routing: shortlist → keyword-score → fallback."""
        hive = self._hive
        task_tags = [w for w in msg_lower.split() if len(w) > 2]
        task_type = "user_question"

        candidates = hive.scheduler.select_candidates(
            task_type=task_type, task_tags=task_tags,
            routing_rules=routing_rules, top_k=hive.scheduler.top_k)

        if not candidates:
            return self.legacy_route(msg_lower, routing_rules)

        candidate_types = set()
        for cid in candidates:
            score = hive.scheduler._scores.get(cid)
            if score:
                candidate_types.add(score.agent_type)

        delegate_to = None
        best_score = 0
        for agent_type in candidate_types:
            weighted = WEIGHTED_ROUTING.get(agent_type)
            if weighted:
                score = (
                    sum(PRIMARY_WEIGHT for kw in weighted.get("primary", [])
                        if kw in msg_lower) +
                    sum(SECONDARY_WEIGHT for kw in weighted.get("secondary", [])
                        if kw in msg_lower))
            else:
                keywords = routing_rules.get(agent_type, [])
                score = sum(1 for kw in keywords if kw in msg_lower)
            if score > best_score:
                best_score = score
                delegate_to = agent_type

        if delegate_to and best_score > 0:
            return delegate_to, best_score
        return self.legacy_route(msg_lower, routing_rules)

    @staticmethod
    def legacy_route(msg_lower: str, routing_rules: dict) -> Tuple[Optional[str], int]:
        """Legacy routing: keyword-score across all agents."""
        delegate_to = None
        best_score = 0
        for agent_type, keywords in routing_rules.items():
            weighted = WEIGHTED_ROUTING.get(agent_type)
            if weighted:
                score = (
                    sum(PRIMARY_WEIGHT for kw in weighted.get("primary", [])
                        if kw in msg_lower) +
                    sum(SECONDARY_WEIGHT for kw in weighted.get("secondary", [])
                        if kw in msg_lower))
            else:
                score = sum(1 for kw in keywords if kw in msg_lower)
            if score > best_score:
                best_score = score
                delegate_to = agent_type
        return delegate_to, best_score

    async def enrich_with_faiss(self, message: str, en_message: str, context: str) -> str:
        """Enrich context with FAISS semantic search results."""
        hive = self._hive
        _faiss_reg = getattr(hive, 'faiss_registry', None)
        if not (_faiss_reg and hasattr(hive, 'consciousness') and hive.consciousness):
            return context
        try:
            import numpy as np
            _q_vec = hive.consciousness.embed.embed_query(en_message or message)
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
                    return f"{_faiss_ctx}\n\n{context}" if context else _faiss_ctx
        except Exception as e:
            log.debug("FAISS enrichment skipped: %s", e)
        return context
