"""Chat delegation — agent dispatch and multi-agent collaboration.

Extracted from chat_handler.py (v3.3 refactor).
"""
import asyncio
import logging
import time

from core.hive_routing import (
    WEIGHTED_ROUTING, PRIMARY_WEIGHT, SECONDARY_WEIGHT,
    MASTER_NEGATIVE_KEYWORDS, AGENT_EN_PROMPTS,
)

log = logging.getLogger("hivemind")


class AgentDelegator:
    """Delegates chat messages to specialist agents."""

    def __init__(self, hive):
        self._hive = hive

    async def delegate_to_agent(self, delegate_to: str, message: str,
                                context: str, msg_lower: str, *,
                                translation_used: bool = False,
                                detected_lang: str = "fi",
                                use_en_prompts: bool = False,
                                fi_en_result=None) -> str:
        """Delegate message to chosen agent. Handles prompt injection + restore."""
        hive = self._hive
        agents = hive.spawner.get_agents_by_type(delegate_to)
        if not agents:
            try:
                agent = await hive.spawner.spawn(delegate_to)
                if agent:
                    hive.register_agent_to_scheduler(agent)
            except Exception:
                agent = None
        else:
            agent = agents[0]

        if not agent:
            return await hive.master_agent.think(message, context)

        _t0 = time.monotonic()

        # Check for learning engine prompt override
        _orig_agent_sys = None
        _override = None
        if hasattr(hive, 'learning') and hive.learning:
            _atype = getattr(agent, 'agent_type', getattr(agent, 'type', ''))
            _override = hive.learning.get_prompt_override(_atype)
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

        # Enriched prompt context manager handles inject + restore
        with hive._enriched_prompt(agent, inject_date=True,
                                   inject_knowledge=True,
                                   knowledge_max_chars=2000) as _enriched_agent:
            # Failure Twin — inject agent-specific error warnings
            if hasattr(hive, 'consciousness') and hive.consciousness:
                _ft_warning = hive.consciousness.get_agent_error_patterns(
                    agent_id=getattr(agent, 'id', ''),
                    query=message)
                if _ft_warning:
                    _enriched_agent.system_prompt = _enriched_agent.system_prompt + "\n\n" + _ft_warning

            # MAGMA: cross-agent provenance enrichment
            _xs = getattr(hive, '_cross_search', None)
            if _xs and hasattr(hive, 'consciousness') and hive.consciousness:
                try:
                    _emb = hive.consciousness.embed.embed_query(message)
                    if _emb:
                        _cross_results = _xs.search_with_provenance(_emb, top_k=3)
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
                if hive.scheduler:
                    hive.scheduler.record_task_start(agent.id)
                async with hive.throttle:
                    response = await _enriched_agent.think(message, context)
            except Exception as e:
                response = f"[Virhe: {e}]"
            finally:
                if hive.scheduler:
                    hive.scheduler.record_task_end(agent.id)

        _elapsed = (time.monotonic() - _t0) * 1000
        hive._report_llm_result(_elapsed, True, hive.llm.model)

        # Validate response before rewarding
        if self._is_valid_response(response):
            if hive.token_economy:
                await hive.token_economy.reward(agent.id, "question_answered")
            if hive.scheduler:
                hive.scheduler.record_task_result(
                    agent.id, success=True, latency_ms=_elapsed)
            if hive.learning:
                hive.learning.submit_for_evaluation(
                    agent_id=agent.id,
                    agent_type=agent.agent_type,
                    system_prompt=getattr(agent, 'system_prompt', '')[:500],
                    prompt=message,
                    response=response)
            if hive.agent_levels:
                try:
                    hive.agent_levels.record_response(
                        agent_id=agent.id,
                        agent_type=agent.agent_type,
                        was_correct=True,
                        was_hallucination=False)
                except Exception as e:
                    log.debug("agent_levels record failed: %s", e)
        else:
            if hive.scheduler:
                hive.scheduler.record_task_result(
                    agent.id, success=False, latency_ms=_elapsed)

        # Restore original prompt
        if _orig_agent_sys is not None:
            agent.system_prompt = _orig_agent_sys

        # EN Validator
        if hive.en_validator and use_en_prompts:
            _val = hive.en_validator.validate(response)
            if _val.was_corrected:
                if getattr(hive, 'monitor', None):
                    await hive.monitor.system(
                        f"🔍 EN-fix ({_val.method}, {_val.latency_ms:.1f}ms, "
                        f"{_val.correction_count} korjausta): "
                        f"{_val.corrections[:3]}")
                response = _val.corrected

        # EN→FI translation
        if translation_used and getattr(hive, 'translation_proxy', None):
            _en_fi = hive.translation_proxy.en_to_fi(response, force_opus=True)
            if _en_fi.method != "passthrough":
                if getattr(hive, 'monitor', None):
                    _src_ms = getattr(fi_en_result, 'latency_ms', 0) if fi_en_result else 0
                    await hive.monitor.system(
                        f"🔄 EN→FI ({_en_fi.method}, {_en_fi.latency_ms:.1f}ms, "
                        f"total: {_src_ms + _en_fi.latency_ms:.1f}ms)")
                response = _en_fi.text

        await hive._notify_ws("delegated", {
            "agent": agent.name, "type": delegate_to, "response": response,
            "language": detected_lang,
            "translated": translation_used
        })
        return f"[{agent.name}] {response}"

    async def multi_agent_collaboration(self, mission: str,
                                        plan: dict, context: str) -> str:
        """Run multiple agents in parallel and synthesize results."""
        hive = self._hive
        army = await hive.spawner.spawn_dynamic_army(mission)
        if not army:
            return await hive.master_agent.think(mission, context)

        tasks = await hive.memory.get_tasks(status="pending")
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
        synthesis = await hive.master_agent.think(
            f"Missio: {mission}\n\nAgenttien tulokset:\n{all_results}\n\n"
            f"Syntetisoi kokonaisvastaus.",
            context)
        return synthesis

    @staticmethod
    def _is_valid_response(response: str) -> bool:
        """Check if response is valid before storing/rewarding."""
        if not response or not response.strip():
            return False
        if len(response.strip()) < 5:
            return False
        bad_markers = ["[LLM-virhe", "[Ollama ei vastaa", "error", "503"]
        for marker in bad_markers:
            if marker in response[:50]:
                return False
        return True
