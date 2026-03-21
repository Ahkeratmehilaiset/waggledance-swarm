"""Heartbeat loop and background tasks — extracted from HiveMind v0.9.0."""
import asyncio
import copy
import json
import logging
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger("hivemind")


class HeartbeatController:
    """Handles heartbeat loop and background tasks.

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

    async def _heartbeat_loop(self):
        """
        Autonominen Waggle Cycle v0.0.3 — TIMEOUT-ESTO:
          - Käyttää throttle.state.heartbeat_interval (adaptiivinen)
          - Pending-task gate: max 3 rinnakkaista taustatehtävää
          - Jos edellinen kierros ei ole valmis → SKIP

        - ~2.5min: agentti ajattelee (1 kerrallaan)
        - ~5min: Queen syntetisoi
        - ~7.5min: Dance Language (kuiskaus)
        - ~10min: Reflektio + Oracle
        - ~15min: Oracle tutkii
        - Jatkuvasti: kuormituslaskurit puhdistetaan
        """
        _pending = 0          # Rinnakkaiset taustatehtävät
        _MAX_PENDING = (self.throttle.state.max_concurrent
                        if self.throttle else 3)

        async def _guarded(coro_func, *args):
            """Suorita tehtävä vain jos slotteja vapaana."""
            nonlocal _pending
            if _pending >= _MAX_PENDING:
                return  # SKIP — liikaa jonossa
            _pending += 1
            try:
                # PHASE1 TASK3: wait if chat is active before LLM call
                await self.priority.wait_if_chat()
                await coro_func(*args)
            finally:
                _pending -= 1

        # ── Tulosta heartbeat-konfiguraatio ────────────────────
        _hb_interval = (self.throttle.state.heartbeat_interval
                        if self.throttle else 60)
        _hb_idle_n = (self.throttle.state.idle_every_n_heartbeat
                      if self.throttle else 5)
        log.info(f"Heartbeat loop käynnistyy: interval={_hb_interval:.0f}s, "
                 f"idle_every={_hb_idle_n}, max_pending={_MAX_PENDING}")

        # ── Ensimmäinen tick heti (1s viive riittää) ──────────
        _first_tick = True

        while self.running:
            try:
                # Phase 3: Night Mode overrides interval
                if self._check_night_mode():
                    if not self._night_mode_active:
                        self._night_mode_active = True
                        self._night_mode_start = time.monotonic()
                        log.info(f"🌙 Night mode ON (user idle), cumulative facts: "
                                 f"{self._night_mode_facts_learned}")
                    interval = self._get_night_mode_interval()
                else:
                    # ADAPTIIVINEN intervalli (throttle säätää koneen mukaan)
                    interval = (self.throttle.state.heartbeat_interval
                                if self.throttle
                                else self.config.get("hivemind", {}).get(
                                    "heartbeat_interval", 60))

                # Ensimmäinen heartbeat heti — ei turhaa odotusta
                if _first_tick:
                    _first_tick = False
                    await asyncio.sleep(2)  # 2s riittää alustukseen
                else:
                    await asyncio.sleep(interval)
                if not self.running:
                    break

                self._heartbeat_count += 1

                # Puhdista kuormituslaskurit
                if self.scheduler:
                    self.scheduler.cleanup_load_counters()

                # ── Nomic embed health check (every 10th HB) ─────
                if (self._heartbeat_count % 10 == 0
                        and hasattr(self, 'consciousness') and self.consciousness):
                    embed = self.consciousness.embed
                    was_available = embed._available
                    embed._check_available()
                    if not embed._available and was_available:
                        log.error("nomic-embed-text WENT DOWN — search/learning degraded")
                        await self._notify_ws("alert", {
                            "type": "embed_down",
                            "message": "nomic-embed-text not responding",
                            "severity": "critical",
                        })
                    elif embed._available and not was_available:
                        log.info("nomic-embed-text recovered")
                        await self._notify_ws("alert", {
                            "type": "embed_recovered",
                            "message": "nomic-embed-text back online",
                            "severity": "info",
                        })

                # ── CHAT-PRIORITEETTI: skip jos chat käynnissä ────
                if self.priority.should_skip:
                    log.debug(f"HB #{self._heartbeat_count}: skip (chat active)")
                    await self._notify_ws("heartbeat", {
                        "time": datetime.now().isoformat(),
                        "count": self._heartbeat_count,
                        "title": "heartbeat (SKIP: chat-prioriteetti)",
                    })
                    continue

                # ── TIMEOUT-GATE: skip koko kierros jos liikaa jonossa ──
                if _pending >= _MAX_PENDING:
                    log.debug(f"HB #{self._heartbeat_count}: skip ({_pending} pending)")
                    await self._notify_ws("heartbeat", {
                        "time": datetime.now().isoformat(),
                        "count": self._heartbeat_count,
                        "title": f"heartbeat (SKIP: {_pending} pending)",
                    })
                    continue

                # ── Heartbeat status print ──────────────────────
                _actions = []

                # Agentti miettii: joka idle_every_n HB (adaptiivinen)
                _idle_n = (self.throttle.state.idle_every_n_heartbeat
                           if self.throttle else 5)
                if self._heartbeat_count % _idle_n == 0 and self.spawner:
                    agents = list(self.spawner.active_agents.values())
                    if agents:
                        agent = agents[self._heartbeat_count % len(agents)]
                        _actions.append(f"think:{agent.name}")
                        self._track_task(
                            _guarded(self._agent_proactive_think, agent))

                # Queen syntetisoi: joka 2*idle_n HB
                if self._heartbeat_count % max(2 * _idle_n, 2) == 0 and self.master_agent:
                    _actions.append("queen")
                    self._track_task(
                        _guarded(self._master_generate_insight))

                # Whisper: joka 3*idle_n HB
                if self._heartbeat_count % max(3 * _idle_n, 3) == 0 and self.whisper and self.spawner:
                    agents = list(self.spawner.active_agents.values())
                    if len(agents) >= 2:
                        _actions.append("whisper")
                        self._track_task(
                            _guarded(self._whisper_cycle, agents))

                # Reflect: joka 4*idle_n HB
                if self._heartbeat_count % max(4 * _idle_n, 4) == 0 and self.spawner:
                    agents = list(self.spawner.active_agents.values())
                    if agents:
                        agent = agents[self._heartbeat_count // max(4 * _idle_n, 4) % len(agents)]
                        _actions.append(f"reflect:{agent.name}")
                        self._track_task(
                            _guarded(self._agent_reflect, agent))

                # Oracle: joka 4*idle_n HB
                if self._heartbeat_count % max(4 * _idle_n, 4) == 0 and self.spawner:
                    _actions.append("oracle")
                    self._track_task(
                        _guarded(self._oracle_consultation_cycle))

                # Oracle tutkii: joka 6*idle_n HB
                if self._heartbeat_count % max(6 * _idle_n, 6) == 0 and self.spawner:
                    _actions.append("oracle_research")
                    self._track_task(
                        _guarded(self._oracle_research_cycle))

                # Phase 3: Round Table (every 20th heartbeat, offset by 1
                # to avoid slot competition with reflect/oracle at HB 20/40/60)
                _rt_every = self.config.get("round_table", {}).get(
                    "every_n_heartbeat", 20)
                if (self._heartbeat_count > 1
                        and (self._heartbeat_count - 1) % _rt_every == 0
                        and self.spawner
                        and self.config.get("round_table", {}).get("enabled", True)):
                    _rt_version = self.config.get("round_table", {}).get("version", 1)
                    _actions.append(f"round_table_v{_rt_version}")
                    if _rt_version >= 2:
                        self._track_task(
                            _guarded(self._round_table_v2))
                    else:
                        self._track_task(
                            _guarded(self._round_table))

                # Phase 3: Night mode learning (every other cycle)
                # Night learn bypasses _guarded — it's the whole point
                # of night mode and should not be starved by other tasks
                if (self._night_mode_active
                        and self._heartbeat_count % 2 == 0):
                    _actions.append("night_learn")
                    self._track_task(self._night_learning_cycle())
                    # Save progress every 5th heartbeat (was 20)
                    if self._heartbeat_count % 5 == 0:
                        self._save_learning_progress()

                # ── Weekly report trigger (runs even outside night mode) ──
                # Check every 50 heartbeats (~50 min) if weekly report is due
                if self._heartbeat_count % 50 == 0:
                    self._track_task(self._maybe_weekly_report())

                # ── MAGMA: CognitiveGraph periodic save (every 50 HBs) ──
                if self._heartbeat_count % 50 == 0:
                    _cg = getattr(self, '_cognitive_graph', None)
                    if _cg:
                        try:
                            _cg.save()
                            _s = _cg.stats()
                            log.info(f"CognitiveGraph saved: {_s['nodes']} nodes, {_s['edges']} edges")
                        except Exception as e:
                            log.warning(f"CognitiveGraph periodic save failed: {e}")

                # ── MAGMA: TrustEngine decay inactive agents (every 100 HBs) ──
                if self._heartbeat_count % 100 == 0:
                    _te = getattr(self, '_trust_engine', None)
                    if _te:
                        try:
                            _te.decay_inactive()
                        except Exception as e:
                            log.warning(f"TrustEngine decay failed: {e}")

                # Odottavat tehtävät (max 1 kerrallaan)
                if _pending < _MAX_PENDING:
                    pending_tasks = await self.memory.get_tasks(status="pending")
                    for task in pending_tasks[:1]:
                        agent_id = task.get("assigned_agent")
                        if agent_id and self.spawner:
                            agent = self.spawner.get_agent(agent_id)
                            if agent and agent.status == "idle":
                                _actions.append(f"task:{agent_id}")
                                self._track_task(
                                    _guarded(agent.execute_task, task))

                # idle_research: batch agentteja kerrallaan (ei rajoitettu 1:een)
                # OpsAgent voi pysäyttää idle-tutkimuksen kuormituksen takia
                idle_n = self.throttle.state.idle_every_n_heartbeat
                _ops_idle_ok = not (self.ops_agent and self.ops_agent.idle_paused)
                if (self._heartbeat_count % idle_n == 0
                        and self.spawner and _pending < _MAX_PENDING
                        and _ops_idle_ok):
                    idle_agents = [
                        a for a in self.spawner.active_agents.values()
                        if a.status == "idle"
                    ]
                    batch = self.throttle.state.idle_batch_size
                    for agent in idle_agents[:batch]:
                        _actions.append(f"idle:{agent.name}")
                        self._track_task(
                            _guarded(self._idle_research, agent))

                # Priority invite: alisuoriutuvat (max 1, harvemmin)
                if (self._swarm_enabled and self.scheduler
                        and self._heartbeat_count % 15 == 0
                        and _pending < _MAX_PENDING
                        and _ops_idle_ok):
                    underused = self.scheduler.get_underused_agents()
                    if underused and self.spawner:
                        aid = underused[0]
                        agent_obj = self.spawner.get_agent(aid)
                        if agent_obj and agent_obj.status == "idle":
                            _actions.append(f"invite:{aid}")
                            self._track_task(
                                _guarded(self._idle_research, agent_obj))

                # ── Console print: mitä tapahtui tällä kierroksella ──
                if _actions:
                    log.debug(f"HB #{self._heartbeat_count}: {', '.join(_actions)} (pending={_pending})")
                else:
                    log.debug(f"HB #{self._heartbeat_count}: idle "
                              f"(next action at #{self._heartbeat_count + (_idle_n - self._heartbeat_count % _idle_n)})")

                await self._notify_ws("heartbeat", {
                    "time": datetime.now().isoformat(),
                    "count": self._heartbeat_count,
                    "pending": _pending,
                    "actions": _actions,
                })

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Heartbeat-virhe: {e}")

    async def _agent_proactive_think(self, agent):
        """Agentti ajattelee + lukee muiden oivallukset."""
        try:
            own = await self.memory.get_recent_memories(agent_id=agent.id, limit=3)
            own_text = "\n".join(
                [m.get("content", "")[:150] for m in own]
            ) if own else ""

            all_recent = await self.memory.get_recent_memories(limit=15)
            others_insights = [
                m.get("content", "")[:150] for m in all_recent
                if m.get("memory_type") == "insight"
                and m.get("agent_id") != agent.id
            ][:5]
            others_text = "\n".join(others_insights) if others_insights else ""

            messages = await self.memory.get_messages(agent.id)
            msg_text = "\n".join(
                [f"- {m.get('content', '')[:100]}" for m in messages[:3]]
            ) if messages else ""
            if messages:
                await self.memory.mark_messages_read(agent.id)

            now = datetime.now().strftime("%d.%m.%Y klo %H:%M")

            # Phase 3: Use guided task if available
            guided_task = None
            if (hasattr(self, 'consciousness') and self.consciousness
                    and self.consciousness.task_queue):
                guided_task = self.consciousness.task_queue.next_task(
                    agent_id=getattr(agent, 'id', None),
                    agent_type=getattr(agent, 'agent_type', None))

            if guided_task:
                prompt = (
                    f"You are {agent.name}. Date: {now}\n\n"
                    f"LEARNING TASK ({guided_task['type']}): {guided_task['prompt']}\n\n"
                    + (f"Your recent observations:\n{own_text[:200]}\n\n"
                       if own_text else "")
                    + "Provide a factual answer. 2 sentences max in English."
                )
            else:
                prompt = (
                    f"You are {agent.name}. Date: {now}\n\n"
                    f"Your observations:\n{own_text[:200]}\n\n"
                    + (f"Other agents' insights:\n{others_text[:300]}\n\n"
                       if others_text else "")
                    + (f"Messages for you:\n{msg_text[:200]}\n\n"
                       if msg_text else "")
                    + "React to other agents' insights OR propose ONE NEW concrete observation. "
                    + "One sentence in English."
                )

            # PHASE1 TASK2: heartbeat MUST use llama3.2:1b — never phi4-mini
            _hb = self.llm_heartbeat
            _t0 = time.monotonic()
            try:
                async with self.throttle:
                    # FIX-1: context manager hoitaa injektoinnin + palautuksen
                    with self._enriched_prompt(agent, inject_date=True,
                                                inject_knowledge=True,
                                                knowledge_max_chars=1500) as _ea:
                        _resp = await _hb.generate(
                            prompt, system=_ea.system_prompt,
                            max_tokens=200
                        )
                _elapsed = (time.monotonic() - _t0) * 1000
                self._report_llm_result(_elapsed, True, _hb.model)
                insight = _resp.content if _resp and not _resp.error else ""
                if insight:
                    log.debug(f"{agent.name} ({_elapsed:.0f}ms): {insight[:80]}")
            except Exception as exc:
                _elapsed = (time.monotonic() - _t0) * 1000
                self._report_llm_result(_elapsed, False, _hb.model)
                log.warning(f"{agent.name} LLM error ({_elapsed:.0f}ms): {exc}")
                insight = ""

            # KORJAUS K10: validoi ennen tallennusta
            if insight and self._is_valid_response(insight):
                # ═══ EN Validator: standardisoi heartbeat-insight ═══
                if self.en_validator:
                    _val = self.en_validator.validate(insight)
                    if _val.was_corrected:
                        insight = _val.corrected
                await self.memory.store_memory(
                    content=f"[{agent.name}] {insight}",
                    agent_id=agent.id,
                    memory_type="insight",
                    importance=0.7
                )
                await self._notify_ws("agent_insight", {
                    "agent": agent.name,
                    "type": agent.agent_type,
                    "insight": insight,
                })
                await self._share_insight(agent, insight)
                await self._log_finetune(agent, prompt, insight)

        except Exception as e:
            log.warning(f"Ajattelu epäonnistui ({agent.name}): {e}")

    async def _share_insight(self, from_agent, insight: str):
        """Jaa insight relevanteille agenteille (max 2 vastaanottajaa)."""
        if not self.spawner:
            return
        try:
            insight_lower = insight.lower()
            routing = {}
            if hasattr(self.spawner, 'yaml_bridge'):
                routing = self.spawner.yaml_bridge.get_routing_rules()

            # Pisteytä relevanssi ja valitse TOP 2
            candidates = []
            for agent in list(self.spawner.active_agents.values()):
                if agent.id == from_agent.id:
                    continue
                keywords = routing.get(agent.agent_type, [])
                relevance = sum(1 for kw in keywords if kw in insight_lower)
                if relevance > 0:
                    candidates.append((relevance, agent))

            # Järjestä relevanssin mukaan, ota max 2
            candidates.sort(key=lambda x: x[0], reverse=True)
            for _, agent in candidates[:2]:
                await from_agent.communicate(
                    agent.id,
                    f"[Insight] {insight[:200]}",
                    "insight_share"
                )
        except Exception as e:
            log.warning(f"Share epäonnistui: {e}")

    async def _log_finetune(self, agent, prompt: str, response: str):
        """Tallenna Q/A-pari finetuning-dataan + lähetä arviointijonoon."""
        try:
            sys_prompt = (agent.system_prompt[:500]
                          if hasattr(agent, 'system_prompt') else "")
            entry = {
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": prompt[:500]},
                    {"role": "assistant", "content": response[:500]}
                ],
                "agent": agent.agent_type,
                "timestamp": datetime.now().isoformat()
            }
            finetune_path = Path("data/finetune_live.jsonl")
            finetune_path.parent.mkdir(exist_ok=True)
            # Rotate if >50MB to prevent unbounded growth
            try:
                if (finetune_path.exists()
                        and finetune_path.stat().st_size > 50 * 1024 * 1024):
                    rotated = finetune_path.parent / (
                        f"finetune_live_{datetime.now():%Y%m%d}.jsonl")
                    import os
                    os.replace(str(finetune_path), str(rotated))
                    log.info("Rotated finetune_live.jsonl → %s", rotated.name)
            except Exception:
                pass
            with open(finetune_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

            # ── LearningEngine: lähetä arviointijonoon ────────
            if self.learning:
                agent_id = agent.id if hasattr(agent, 'id') else "unknown"
                agent_type = agent.agent_type if hasattr(agent, 'agent_type') else "unknown"
                self.learning.submit_for_evaluation(
                    agent_id=agent_id,
                    agent_type=agent_type,
                    system_prompt=sys_prompt,
                    prompt=prompt,
                    response=response,
                )
        except Exception:
            pass

    async def _master_generate_insight(self):
        """Queen syntetisoi oivallukset. PHASE1 TASK2: uses llama3.2:1b only."""
        try:
            all_memories = await self.memory.get_recent_memories(limit=20)
            insights = [m for m in all_memories if m.get("memory_type") == "insight"]
            if not insights:
                insights = all_memories[:10]

            insights_text = "\n".join(
                [m.get("content", "") for m in insights[:10]]
            )

            # PHASE1 TASK2: heartbeat MUST use llama3.2:1b — never phi4-mini
            _hb = self.llm_heartbeat
            _prompt = (f"Insights:\n{insights_text[:500]}\n\n"
                       f"Synthesize ONE strategic insight. 2 sentences in English.")
            async with self.throttle:
                _resp = await _hb.generate(
                    _prompt,
                    system=self.master_agent.system_prompt[:500],
                    max_tokens=200
                )
            synthesis = _resp.content if _resp and not _resp.error else ""

            # KORJAUS K10: validoi synteesi
            if synthesis and self._is_valid_response(synthesis):
                await self.memory.store_memory(
                    content=f"[Swarm Queen] {synthesis}",
                    agent_id=(self.master_agent.id
                              if hasattr(self.master_agent, 'id')
                              else "hivemind"),
                    memory_type="insight",
                    importance=0.9
                )
                await self._notify_ws("queen_synthesis", {"synthesis": synthesis})
        except Exception as e:
            log.warning(f"Synteesi epäonnistui: {e}")

    async def _idle_research(self, agent):
        """Idle-agentti tutkii autonomisesti."""
        try:
            agent.status = "researching"
            all_recent = await self.memory.get_recent_memories(limit=15)
            others = [
                m.get("content", "")[:100] for m in all_recent
                if m.get("agent_id") != agent.id
                and m.get("memory_type") == "insight"
            ][:3]
            others_text = "\n".join(others) if others else ""

            messages = await self.memory.get_messages(agent.id)
            msg_text = "\n".join(
                [m.get("content", "")[:80] for m in messages[:3]]
            ) if messages else ""
            if messages:
                await self.memory.mark_messages_read(agent.id)

            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            prompt = f"You are {agent.name}. Date: {now}\n"
            if others_text:
                prompt += f"Other agents' insights:\n{others_text}\n"
            if msg_text:
                prompt += f"Messages for you:\n{msg_text}\n"
            prompt += ("Research something NEW related to your specialty. "
                       "ONE concrete fact or recommendation. One sentence in English.")

            # Knowledge injection promptin sisään (ei system_promptiin)
            if self.knowledge_loader:
                _agent_type = getattr(agent, 'agent_type', '')
                _kb = self.knowledge_loader.get_knowledge_summary(_agent_type)
                if _kb:
                    prompt += "\n\nKNOWLEDGE BASE:\n" + _kb[:800]

            # PHASE1 TASK2: heartbeat MUST use llama3.2:1b — never phi4-mini
            _hb = self.llm_heartbeat
            _t0 = time.monotonic()
            try:
                async with self.throttle:
                    # FIX-1: context manager päivämääräinjektiolle
                    with self._enriched_prompt(agent, inject_date=True,
                                                inject_knowledge=False) as _ea:
                        resp = await _hb.generate(
                            prompt, system=_ea.system_prompt, max_tokens=150
                        )
                _elapsed = (time.monotonic() - _t0) * 1000
                self._report_llm_result(_elapsed, True, _hb.model)
                insight = (resp.content
                           if resp and not resp.error and resp.content
                           else None)
            except Exception as _err:
                _elapsed = (time.monotonic() - _t0) * 1000
                self._report_llm_result(_elapsed, False, _hb.model)
                log.warning("Oracle LLM error (%.0fms): %s", _elapsed, _err)
                insight = None

            # KORJAUS K10: validoi + KORJAUS: duplikaatti-reward poistettu
            if insight and self._is_valid_response(insight):
                await self.memory.store_memory(
                    content=f"[{agent.name}] tutkimus: {insight}",
                    agent_id=agent.id,
                    memory_type="insight",
                    importance=0.6
                )
                # Reward KERRAN (oli 2x aiemmin)
                if self.token_economy:
                    try:
                        await self.token_economy.reward(
                            agent.id, "idle_research", custom_amount=2
                        )
                    except Exception:
                        pass
                if self.scheduler:
                    self.scheduler.record_task_result(
                        agent.id, success=True, latency_ms=_elapsed
                    )
                await self._share_insight(agent, insight)
                await self._log_finetune(agent, prompt, insight)
                await self._notify_ws("idle_research", {
                    "agent": agent.name, "finding": insight[:150]
                })

            agent.status = "idle"
        except Exception:
            agent.status = "idle"

    async def _whisper_cycle(self, agents):
        """Whisper cycle using enriched copies — no mutation of originals."""
        try:
            await self.priority.wait_if_chat()
            agent_list = (agents if isinstance(agents, list)
                          else list(agents.values()))
            # Create enriched copies (same logic as _enriched_prompt context manager)
            enriched_list = []
            for _a in agent_list:
                enriched = copy.copy(_a)
                enriched.system_prompt = _a.system_prompt
                if self.knowledge_loader:
                    agent_type = getattr(_a, 'agent_type', getattr(_a, 'type', ''))
                    if agent_type:
                        kb = self.knowledge_loader.get_knowledge_summary(agent_type)
                        if kb:
                            enriched.system_prompt += "\n" + kb[:800]
                enriched_list.append(enriched)
            await self.whisper.auto_whisper_cycle(enriched_list, self.llm)
        except Exception as e:
            log.warning(f"Whisper epäonnistui: {e}")

    async def _oracle_consultation_cycle(self):
        try:
            oracle = self.get_oracle()
            if not oracle or not hasattr(oracle, 'auto_generate_questions'):
                return
            agents = list(self.spawner.active_agents.values())
            if not agents:
                return
            count = await oracle.auto_generate_questions(agents)
            if count > 0:
                await self._notify_ws("oracle_questions_ready", {
                    "count": count,
                    "message": f"🔮 {count} kysymystä odottaa!"
                })
        except Exception as e:
            log.warning(f"Oracle-konsultaatio epäonnistui: {e}")

    async def _oracle_research_cycle(self):
        try:
            oracle = self.get_oracle()
            if not oracle or not hasattr(oracle, 'auto_research_cycle'):
                return
            agents = list(self.spawner.active_agents.values())
            if not agents:
                return
            count = await oracle.auto_research_cycle(agents)
            if count > 0:
                await self._notify_ws("oracle_research", {
                    "count": count,
                    "message": f"🔍 Oracle tutki {count} aihetta!"
                })
        except Exception as e:
            log.warning(f"Oracle-tutkimus epäonnistui: {e}")

    async def _agent_reflect(self, agent):
        """Agentti arvioi omaa toimintaansa."""
        try:
            recent = await self.memory.get_recent_memories(
                agent_id=agent.id, limit=3
            )
            if not recent:
                return
            recent_text = "\n".join(
                [m.get("content", "")[:80] for m in recent]
            )
            prompt = (f"Evaluate your actions: {recent_text}\n"
                      f"What did you learn? One sentence in English.")
            # PHASE1 TASK2: heartbeat MUST use llama3.2:1b — never phi4-mini
            _hb = self.llm_heartbeat
            async with self.throttle:
                resp = await _hb.generate(
                    prompt, system=agent.system_prompt[:500], max_tokens=100
                )
            if resp and not resp.error and self._is_valid_response(resp.content):
                await self.memory.store_memory(
                    content=f"[{agent.name}] reflektio: {resp.content}",
                    agent_id=agent.id,
                    memory_type="reflection",
                    importance=0.4
                )
        except Exception:
            pass
