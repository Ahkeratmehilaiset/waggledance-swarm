"""Round Table discussion controller — extracted from HiveMind v0.9.0."""
import asyncio
import logging
import time
from datetime import datetime

log = logging.getLogger("hivemind")


class RoundTableController:
    """Handles Round Table discussions, agent selection, and streaming.

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

    async def _round_table(self, topic: str = None, agent_count: int = 6):
        """Round Table: 6 agents discuss a topic, Queen synthesizes consensus."""
        if not self.spawner:
            return
        _rt_cfg = self.config.get("round_table", {})
        if not _rt_cfg.get("enabled", True):
            return
        agent_count = _rt_cfg.get("agent_count", agent_count)
        min_agents = _rt_cfg.get("min_agents", 3)

        agents = list(self.spawner.active_agents.values())
        if len(agents) < min_agents:
            return
        agent_count = min(agent_count, len(agents))

        try:
            # Phase 1: Generate topic if not given
            if not topic:
                all_recent = await self.memory.get_recent_memories(limit=10)
                insights = [m.get("content", "")[:100] for m in all_recent
                            if m.get("memory_type") == "insight"]
                if insights:
                    _hb = self.llm_heartbeat
                    _prompt = (f"Recent insights:\n" +
                               "\n".join(insights[:5]) +
                               "\n\nSuggest ONE specific discussion topic for a panel "
                               "of beekeeping experts. One sentence in English.")
                    async with self.throttle:
                        _resp = await _hb.generate(_prompt, max_tokens=80)
                    topic = _resp.content.strip() if _resp and not _resp.error else None
                if not topic:
                    topic = "Current best practices for Finnish beekeeping"

            log.info(f"🏛️ Round Table starting: {topic[:80]}")
            await self._notify_ws("round_table_start", {
                "topic": topic,
                "agent_count": agent_count,
                "time": datetime.now().isoformat(),
            })

            # Phase 2: Select agents
            selected = self._select_round_table_agents(agents, topic, agent_count)

            # Phase 3: Sequential discussion (each sees previous)
            discussion = []
            for agent in selected:
                await self.priority.wait_if_chat()
                prev_text = ""
                if discussion:
                    prev_text = "\n".join(
                        [f"[{d['agent']}]: {d['response']}" for d in discussion[-3:]])
                _prompt = (
                    f"ROUND TABLE DISCUSSION\nTopic: {topic}\n"
                    + (f"Previous speakers:\n{prev_text}\n\n" if prev_text else "")
                    + f"You are {agent.name} ({agent.agent_type}). "
                    f"Share your expert perspective. 2 sentences max in English."
                )
                _hb = self.llm_heartbeat
                try:
                    async with self.throttle:
                        with self._enriched_prompt(agent, inject_date=True,
                                                    inject_knowledge=True,
                                                    knowledge_max_chars=800) as _ea:
                            _resp = await _hb.generate(
                                _prompt, system=_ea.system_prompt,
                                max_tokens=150)
                    response = _resp.content if _resp and not _resp.error else ""
                except Exception:
                    response = ""

                if response and self._is_valid_response(response):
                    entry = {
                        "agent": agent.name,
                        "agent_type": agent.agent_type,
                        "agent_id": agent.id,
                        "response": response,
                    }
                    discussion.append(entry)

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

            if not discussion:
                return

            # Phase 4: Queen synthesizes (with seasonal context)
            all_responses = "\n".join(
                [f"[{d['agent']}]: {d['response']}" for d in discussion])

            # Inject seasonal context for Queen
            _seasonal_ctx = ""
            try:
                from core.seasonal_guard import get_seasonal_guard
                _sg = get_seasonal_guard()
                _seasonal_ctx = f"\n{_sg.queen_context()}\n"
            except Exception:
                pass

            _synth_prompt = (
                f"ROUND TABLE SYNTHESIS\nTopic: {topic}\n"
                f"{_seasonal_ctx}\n"
                f"Agent responses:\n{all_responses}\n\n"
                f"Synthesize the key consensus. Identify agreements and "
                f"the most important practical takeaway. 3 sentences in English."
            )
            _hb = self.llm_heartbeat
            async with self.throttle:
                _resp = await _hb.generate(
                    _synth_prompt,
                    system=self.master_agent.system_prompt[:500],
                    max_tokens=200)
            synthesis = _resp.content if _resp and not _resp.error else ""

            # Phase 5: Store + stream via theater pipe
            # Seasonal guard: reject out-of-season synthesis
            if synthesis and self._is_valid_response(synthesis):
                try:
                    from core.seasonal_guard import get_seasonal_guard
                    _sg_ok, _sg_reason = get_seasonal_guard().filter_enrichment(synthesis)
                    if not _sg_ok:
                        log.info(f"🏛️ Round Table synthesis filtered: {_sg_reason}")
                except Exception:
                    _sg_ok = True
                if _sg_ok and self.consciousness:
                    self.consciousness.learn(
                        synthesis, agent_id="round_table",
                        source_type="round_table",
                        confidence=0.85, validated=True,
                        metadata={"topic": topic[:200]})

                # Layer 4: Record consensus provenance + broadcast
                _prov = getattr(self, '_provenance', None)
                _chreg = getattr(self, '_channel_registry', None)
                if _prov:
                    try:
                        _rt_agents = [d.get("agent_id", "") for d in discussion]
                        _prov.record_consensus(
                            f"round_table_{int(time.time())}",
                            _rt_agents, synthesis[:500])
                    except Exception as _e:
                        log.debug(f"Provenance consensus: {_e}")
                if _chreg:
                    try:
                        _chreg.broadcast("judges", "round_table",
                                         f"[Synthesis] {synthesis[:300]}")
                    except Exception:
                        pass

                # Layer 5: Record consensus participation trust signal
                _te = getattr(self, '_trust_engine', None)
                if _te:
                    for _d in discussion:
                        _aid = _d.get("agent_id", "")
                        if _aid:
                            try:
                                _te.record_signal(_aid, "consensus_participation", 1.0)
                            except Exception:
                                pass

                await self._theater_stream_round_table(topic, discussion, synthesis)
                log.info(f"🏛️ Round Table done: {len(discussion)} agents, topic: {topic[:60]}")

        except Exception as e:
            log.error(f"Round Table error: {e}")

    def _select_round_table_agents(self, agents: list, topic: str,
                                    count: int = 6) -> list:
        """Select agents for Round Table: relevance + level boost + 1 random."""
        if not agents:
            return []

        # Get routing rules for keyword matching
        routing_rules = {}
        if self.spawner and hasattr(self.spawner, 'yaml_bridge'):
            routing_rules = self.spawner.yaml_bridge.get_routing_rules()

        topic_lower = topic.lower()
        scored = []
        for agent in agents:
            keywords = routing_rules.get(agent.agent_type, [])
            relevance = sum(1 for kw in keywords if kw in topic_lower)
            level_boost = 0
            if self.agent_levels:
                try:
                    level_boost = self.agent_levels.get_level(agent.id) * 0.2
                except Exception:
                    pass
            score = relevance + level_boost
            scored.append((score, agent))

        scored.sort(key=lambda x: x[0], reverse=True)

        # Take top (count-1) by score, + 1 random for diversity
        selected = [a for _, a in scored[:max(1, count - 1)]]
        remaining = [a for _, a in scored[count - 1:] if a not in selected]
        if remaining:
            import random
            selected.append(random.choice(remaining))

        return selected[:count]

    def _should_translate_output(self) -> bool:
        """Check if output should be translated to Finnish based on language_mode."""
        if self.language_mode == "fi":
            return True
        if self.language_mode == "en":
            return False
        # auto: translate if last detected language was Finnish
        return getattr(self, '_detected_lang', 'en') == "fi"

    async def _theater_stream_round_table(self, topic: str,
                                           discussion: list, synthesis: str):
        """Stream Round Table results with 300ms delays (Theater Pipe)."""
        _translate = self._should_translate_output() and self.translation_proxy
        for entry in discussion:
            display_text = entry["response"]
            if _translate:
                try:
                    _fi = self.translation_proxy.en_to_fi(
                        display_text, force_opus=True)
                    if _fi.method != "passthrough":
                        display_text = _fi.text
                except Exception:
                    pass

            await self._notify_ws("round_table_insight", {
                "agent": entry["agent"],
                "agent_type": entry["agent_type"],
                "response": display_text,
                "response_en": entry["response"],
            })
            await asyncio.sleep(0.3)

        # Synthesis
        synth_display = synthesis
        if _translate:
            try:
                _fi = self.translation_proxy.en_to_fi(synthesis, force_opus=True)
                if _fi.method != "passthrough":
                    synth_display = _fi.text
            except Exception:
                pass

        await self._notify_ws("round_table_synthesis", {
            "topic": topic,
            "synthesis": synth_display,
            "synthesis_en": synthesis,
            "agent_count": len(discussion),
        })
        await self._notify_ws("round_table_end", {
            "topic": topic,
            "agent_count": len(discussion),
        })

    async def _round_table_v2(self, topic: str = None, agent_count: int = 6):
        """Round Table v2: blind phase + informed phase + Queen synthesis.

        Phase 0 (Blind): 2 agents answer WITHOUT seeing others — independent views
        Phase 1 (Informed): 4 agents see both blind responses — react, agree/disagree
        Phase 2 (Queen): Queen finds disagreements or explains why consensus is strong
        """
        if not self.spawner:
            return
        _rt_cfg = self.config.get("round_table", {})
        if not _rt_cfg.get("enabled", True):
            return
        agent_count = _rt_cfg.get("agent_count", agent_count)
        min_agents = _rt_cfg.get("min_agents", 3)

        agents = list(self.spawner.active_agents.values())
        if len(agents) < min_agents:
            return
        agent_count = min(agent_count, len(agents))

        try:
            # Generate topic if not given
            if not topic:
                all_recent = await self.memory.get_recent_memories(limit=10)
                insights = [m.get("content", "")[:100] for m in all_recent
                            if m.get("memory_type") == "insight"]
                if insights:
                    _hb = self.llm_heartbeat
                    _prompt = (f"Recent insights:\n" +
                               "\n".join(insights[:5]) +
                               "\n\nSuggest ONE specific discussion topic for a panel "
                               "of beekeeping experts. One sentence in English.")
                    async with self.throttle:
                        _resp = await _hb.generate(_prompt, max_tokens=80)
                    topic = _resp.content.strip() if _resp and not _resp.error else None
                if not topic:
                    topic = "Current best practices for Finnish beekeeping"

            log.info(f"🏛️ Round Table v2 starting: {topic[:80]}")
            await self._notify_ws("round_table_start", {
                "topic": topic,
                "agent_count": agent_count,
                "version": 2,
                "time": datetime.now().isoformat(),
            })

            # Select agents
            selected = self._select_round_table_agents(agents, topic, agent_count)
            if len(selected) < 2:
                return

            # === Phase 0: BLIND — 2 agents answer independently ===
            blind_agents = selected[:2]
            blind_responses = []
            for agent in blind_agents:
                await self.priority.wait_if_chat()
                _prompt = (
                    f"ROUND TABLE DISCUSSION (BLIND PHASE)\nTopic: {topic}\n"
                    f"You are {agent.name} ({agent.agent_type}). "
                    f"Share your independent expert perspective. "
                    f"You have NOT seen other responses. 2 sentences max in English."
                )
                _hb = self.llm_heartbeat
                try:
                    async with self.throttle:
                        with self._enriched_prompt(agent, inject_date=True,
                                                    inject_knowledge=True,
                                                    knowledge_max_chars=800) as _ea:
                            _resp = await _hb.generate(
                                _prompt, system=_ea.system_prompt,
                                max_tokens=150)
                    response = _resp.content if _resp and not _resp.error else ""
                except Exception:
                    response = ""

                if response and self._is_valid_response(response):
                    entry = {
                        "agent": agent.name,
                        "agent_type": agent.agent_type,
                        "agent_id": agent.id,
                        "response": response,
                        "phase": "blind",
                    }
                    blind_responses.append(entry)
                    if self.agent_levels:
                        try:
                            self.agent_levels.record_response(
                                agent_id=agent.id,
                                agent_type=agent.agent_type,
                                was_correct=True,
                                was_hallucination=False)
                        except Exception:
                            pass

            if not blind_responses:
                return

            # === Phase 1: INFORMED — remaining agents see blind responses ===
            blind_text = "\n".join(
                [f"[{d['agent']}]: {d['response']}" for d in blind_responses])
            informed_agents = selected[2:]
            informed_responses = []
            for agent in informed_agents:
                await self.priority.wait_if_chat()
                _prompt = (
                    f"ROUND TABLE DISCUSSION (INFORMED PHASE)\nTopic: {topic}\n\n"
                    f"Independent expert responses (blind phase):\n{blind_text}\n\n"
                    f"You are {agent.name} ({agent.agent_type}). "
                    f"React to the blind responses: agree, disagree, or add "
                    f"a new perspective. 2 sentences max in English."
                )
                _hb = self.llm_heartbeat
                try:
                    async with self.throttle:
                        with self._enriched_prompt(agent, inject_date=True,
                                                    inject_knowledge=True,
                                                    knowledge_max_chars=800) as _ea:
                            _resp = await _hb.generate(
                                _prompt, system=_ea.system_prompt,
                                max_tokens=150)
                    response = _resp.content if _resp and not _resp.error else ""
                except Exception:
                    response = ""

                if response and self._is_valid_response(response):
                    entry = {
                        "agent": agent.name,
                        "agent_type": agent.agent_type,
                        "agent_id": agent.id,
                        "response": response,
                        "phase": "informed",
                    }
                    informed_responses.append(entry)
                    if self.agent_levels:
                        try:
                            self.agent_levels.record_response(
                                agent_id=agent.id,
                                agent_type=agent.agent_type,
                                was_correct=True,
                                was_hallucination=False)
                        except Exception:
                            pass

            # === Phase 2: Queen synthesis ===
            all_discussion = blind_responses + informed_responses
            if not all_discussion:
                return

            all_responses = "\n".join(
                [f"[{d['agent']} ({d['phase']})] {d['response']}"
                 for d in all_discussion])
            _synth_prompt = (
                f"ROUND TABLE v2 SYNTHESIS\nTopic: {topic}\n\n"
                f"Agent responses:\n{all_responses}\n\n"
                f"Find disagreements between agents. If none exist, explain "
                f"why the consensus is strong. Identify the most important "
                f"practical takeaway. 3 sentences in English."
            )
            _hb = self.llm_heartbeat
            async with self.throttle:
                _resp = await _hb.generate(
                    _synth_prompt,
                    system=self.master_agent.system_prompt[:500],
                    max_tokens=200)
            synthesis = _resp.content if _resp and not _resp.error else ""

            # Store + stream
            if synthesis and self._is_valid_response(synthesis):
                if self.consciousness:
                    self.consciousness.learn(
                        synthesis, agent_id="round_table",
                        source_type="round_table",
                        confidence=0.85, validated=True,
                        metadata={"topic": topic[:200], "version": 2})

                # MAGMA: CognitiveGraph edges for Round Table
                _cg = getattr(self, '_cognitive_graph', None)
                if _cg:
                    try:
                        _cg.add_node(_fact_id, agent_id="round_table",
                                     source_type="round_table", topic=topic[:200])
                        # input_to edges: each participant's response → synthesis
                        for _d in all_discussion:
                            _aid = _d.get("agent_id", "")
                            if _aid:
                                _cg.add_edge(_aid, _fact_id, link_type="input_to")
                        # semantic edges between blind phase participants
                        _blind_ids = [d.get("agent_id", "") for d in blind_responses if d.get("agent_id")]
                        for i in range(len(_blind_ids)):
                            for j in range(i + 1, len(_blind_ids)):
                                _cg.add_edge(_blind_ids[i], _blind_ids[j],
                                             link_type="semantic", topic=topic[:100])
                    except Exception as _e:
                        log.debug(f"CogGraph RT edges: {_e}")

                # Layer 4: Record consensus provenance + agent validations
                _prov = getattr(self, '_provenance', None)
                _chreg = getattr(self, '_channel_registry', None)
                _fact_id = f"round_table_{int(time.time())}"
                if _prov:
                    try:
                        _rt_agents = [d.get("agent_id", "") for d in all_discussion]
                        _prov.record_consensus(_fact_id, _rt_agents, synthesis[:500])
                        # Record individual validations from informed phase
                        for _d in informed_responses:
                            _aid = _d.get("agent_id", "")
                            _text = _d.get("response", "").lower()
                            if not _aid:
                                continue
                            # Heuristic: detect agree/disagree from response
                            if any(w in _text for w in ["disagree", "incorrect",
                                                        "however", "but actually"]):
                                _verdict = "disagree"
                            elif any(w in _text for w in ["agree", "correct",
                                                          "exactly", "indeed"]):
                                _verdict = "agree"
                            else:
                                _verdict = "neutral"
                            try:
                                _prov.record_validation(_fact_id, _aid, _verdict)
                            except Exception:
                                pass
                    except Exception as _e:
                        log.debug(f"Provenance v2: {_e}")
                if _chreg:
                    try:
                        _chreg.broadcast("judges", "round_table",
                                         f"[v2 Synthesis] {synthesis[:300]}")
                    except Exception:
                        pass

                # Layer 5: Record trust signals per participant
                _te = getattr(self, '_trust_engine', None)
                if _te:
                    for _d in all_discussion:
                        _aid = _d.get("agent_id", "")
                        if _aid:
                            try:
                                _te.record_signal(_aid, "consensus_participation", 1.0)
                            except Exception:
                                pass

                await self._theater_stream_round_table(
                    topic, all_discussion, synthesis)
                log.info(f"🏛️ Round Table v2 done: "
                         f"{len(blind_responses)} blind + "
                         f"{len(informed_responses)} informed, "
                         f"topic: {topic[:60]}")

        except Exception as e:
            log.error(f"Round Table v2 error: {e}")
