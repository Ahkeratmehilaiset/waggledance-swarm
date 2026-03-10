"""Chat routing and delegation — extracted from HiveMind v0.9.0."""
import asyncio
import logging
import time
from datetime import datetime

from core.hive_routing import (
    WEIGHTED_ROUTING, PRIMARY_WEIGHT, SECONDARY_WEIGHT,
    MASTER_NEGATIVE_KEYWORDS, AGENT_EN_PROMPTS,
)

try:
    from core.translation_proxy import detect_language
    _TRANSLATION_AVAILABLE = True
except ImportError:
    _TRANSLATION_AVAILABLE = False
    def detect_language(t): return "fi"

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

        # ═══ Kielentunnistus ═══
        if language == "auto":
            _detected_lang = detect_language(message) if _TRANSLATION_AVAILABLE else "fi"

        # ═══ Phase 4: Detect user correction ("ei", "väärin", correction text) ═══
        _CORRECTION_WORDS = {"ei", "väärin", "wrong", "väärä", "virhe",
                             "korjaus", "eikä", "ei ole", "tarkoitin"}
        _CORRECTION_PHRASES = {"ei vaan", "oikea vastaus", "tarkoitin että",
                               "ei vaan ", "väärä vastaus"}
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
                    except Exception:
                        pass
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
                except Exception:
                    pass  # Fall through to normal routing

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
            self.metrics.log_chat(
                query=_original_message, method="delegate",
                agent_id=delegate_to,
                model_used=getattr(self.llm, 'model', 'unknown'),
                confidence=_pre.confidence if _pre else 0.0,
                response_time_ms=(time.perf_counter() - _chat_t0) * 1000,
                route="agent_delegate", language=_detected_lang,
                translated=_translation_used)
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
            self.metrics.log_chat(
                query=_original_message, method="master",
                agent_id="master", model_used="phi4-mini",
                confidence=_quality,
                was_hallucination=bool(_hall and _hall.is_suspicious),
                response_time_ms=(time.perf_counter() - _chat_t0) * 1000,
                route="llm_master", language=_detected_lang,
                translated=_translation_used)
            return response

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

        # ═══ EN-prompt jos käännös aktiivinen ═══
        _orig_agent_sys = None
        if use_en_prompts:
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
                except Exception:
                    pass
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
