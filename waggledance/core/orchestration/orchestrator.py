"""Orchestrator — the new HiveMind, small and injectable.

Ported from hivemind.py. All I/O through ports.
BUG 1 fix: Never mixes incompatible types in typed attributes.
BUG 2 fix: All asyncio tasks tracked via _track_task() with error callbacks.
"""

import asyncio
import logging
import time

from waggledance.core.domain.agent import AgentDefinition, AgentResult
from waggledance.core.domain.events import DomainEvent, EventType
from waggledance.core.domain.task import TaskRequest, TaskRoute
from waggledance.core.domain.trust_score import TrustSignals
from waggledance.core.orchestration.round_table import ConsensusResult, RoundTableEngine
from waggledance.core.orchestration.scheduler import Scheduler, SchedulerState
from waggledance.core.ports.config_port import ConfigPort
from waggledance.core.ports.event_bus_port import EventBusPort
from waggledance.core.ports.llm_port import LLMPort
from waggledance.core.ports.memory_repository_port import MemoryRepositoryPort
from waggledance.core.ports.trust_store_port import TrustStorePort
from waggledance.core.ports.vector_store_port import VectorStorePort

log = logging.getLogger(__name__)


class Orchestrator:
    """Central task orchestration — routes, executes, and updates trust."""

    def __init__(
        self,
        scheduler: Scheduler,
        round_table: RoundTableEngine,
        memory: MemoryRepositoryPort,
        vector_store: VectorStorePort,
        llm: LLMPort,
        trust_store: TrustStorePort,
        event_bus: EventBusPort,
        config: ConfigPort,
        agents: list[AgentDefinition],
        parallel_dispatcher=None,
    ) -> None:
        self._scheduler = scheduler
        self._round_table = round_table
        self._memory = memory
        self._vector_store = vector_store
        self._llm = llm
        self._trust_store = trust_store
        self._event_bus = event_bus
        self._config = config
        self._agents = list(agents)
        self._parallel_dispatcher = parallel_dispatcher
        self._scheduler_state = SchedulerState()
        self._background_tasks: set[asyncio.Task] = set()

    async def handle_task(self, task: TaskRequest, route: TaskRoute) -> AgentResult:
        """Main entry point — execute a routed task."""
        start = time.monotonic()

        await self._event_bus.publish(DomainEvent(
            type=EventType.TASK_RECEIVED,
            payload={"query": task.query, "route": route.route_type},
            timestamp=time.time(),
            source="orchestrator",
        ))

        trust_scores = {}
        for agent in self._agents:
            trust = await self._trust_store.get_trust(agent.id)
            if trust:
                trust_scores[agent.id] = trust

        selected = self._scheduler.select_agents(
            task=task,
            candidates=self._agents,
            state=self._scheduler_state,
            trust_scores=trust_scores,
            n=int(self._config.get("swarm.top_k", 6)),
        )

        await self._event_bus.publish(DomainEvent(
            type=EventType.ROUTE_SELECTED,
            payload={
                "route_type": route.route_type,
                "selected_agents": [a.id for a in selected],
            },
            timestamp=time.time(),
            source="orchestrator",
        ))

        if route.route_type == "micromodel":
            mm_result = self._try_micromodel(task.query)
            if mm_result is not None:
                elapsed = (time.monotonic() - start) * 1000
                return AgentResult(
                    agent_id="micromodel",
                    response=mm_result["answer"],
                    confidence=float(mm_result.get("confidence", route.confidence)),
                    latency_ms=elapsed,
                    source="micromodel",
                )
            log.info("Micromodel miss, falling back to LLM")

        if route.route_type in ("llm", "micromodel", "solver") or not selected:
            response = await self._llm.generate(
                prompt=task.query,
                temperature=0.7,
                max_tokens=1000,
            )
            elapsed = (time.monotonic() - start) * 1000
            return AgentResult(
                agent_id="llm_direct",
                response=response,
                confidence=route.confidence if response else 0.0,
                latency_ms=elapsed,
                source="llm",
            )

        best_result = await self._execute_agents(task, selected, route)

        self._scheduler_state = self._scheduler.update_state(
            self._scheduler_state,
            best_result.agent_id,
            best_result,
        )

        await self._update_trust(best_result)

        return best_result

    async def run_round_table(self, task: TaskRequest) -> ConsensusResult:
        """Escalate to multi-agent consensus."""
        trust_scores = {}
        for agent in self._agents:
            trust = await self._trust_store.get_trust(agent.id)
            if trust:
                trust_scores[agent.id] = trust

        selected = self._scheduler.select_agents(
            task=task,
            candidates=self._agents,
            state=self._scheduler_state,
            trust_scores=trust_scores,
            n=6,
        )

        # Build prompts for all agents
        prompts = [
            (
                f"You are {agent.name} ({agent.domain}). "
                f"Answer briefly: {task.query}"
            )
            for agent in selected
        ]

        # Parallel dispatch when enabled — agents are independent at this stage
        if (self._parallel_dispatcher
                and self._parallel_dispatcher.enabled):
            batch = [(p, "default", 0.7, 150) for p in prompts]
            raw = await self._parallel_dispatcher.dispatch_batch(batch)
        else:
            raw = []
            for p in prompts:
                r = await self._llm.generate(prompt=p, max_tokens=150, temperature=0.7)
                raw.append(r)

        responses: list[AgentResult] = []
        for agent, response in zip(selected, raw):
            responses.append(AgentResult(
                agent_id=agent.id,
                response=response,
                confidence=0.5,
                latency_ms=0.0,
                source="swarm",
            ))

        return await self._round_table.deliberate(task, selected, responses)

    async def is_ready(self) -> bool:
        """Check all ports are healthy."""
        try:
            llm_ok = await self._llm.is_available()
            vector_ok = await self._vector_store.is_ready()
            return llm_ok and vector_ok
        except Exception as e:
            log.warning("Readiness check failed: %s", e)
            return False

    @staticmethod
    def _try_micromodel(query: str) -> dict | None:
        """Try legacy micro_model.PatternMatchEngine. Returns result dict or None."""
        try:
            from core.micro_model import PatternMatchEngine
            engine = PatternMatchEngine()
            return engine.predict(query)
        except Exception:
            return None

    def _track_task(self, coro: object) -> asyncio.Task:
        """Create a tracked background task with error logging (BUG 2 fix)."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        task.add_done_callback(self._log_task_error)
        return task

    @staticmethod
    def _log_task_error(task: asyncio.Task) -> None:
        """Log errors from background tasks — prevents silent failures."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logging.getLogger(__name__).error(
                "Background task failed: %s", exc, exc_info=exc,
            )

    async def _execute_agents(
        self, task: TaskRequest, agents: list[AgentDefinition], route: TaskRoute
    ) -> AgentResult:
        """Execute task with selected agents, return best result.

        When parallel_dispatcher is enabled, all agent LLM calls run concurrently
        (they are independent — each agent answers the same query).
        """
        start = time.monotonic()

        # Build prompts
        prompts = [
            (
                f"You are {agent.name}, expert in {agent.domain}. "
                f"Answer: {task.query}"
            )
            for agent in agents
        ]

        # Publish AGENT_STARTED for all
        for agent in agents:
            await self._event_bus.publish(DomainEvent(
                type=EventType.AGENT_STARTED,
                payload={"agent_id": agent.id, "query": task.query},
                timestamp=time.time(),
                source="orchestrator",
            ))

        # Dispatch: parallel or sequential
        if (self._parallel_dispatcher
                and self._parallel_dispatcher.enabled):
            batch = [(p, "default", 0.7, 500) for p in prompts]
            raw_responses = await self._parallel_dispatcher.dispatch_batch(batch)
        else:
            raw_responses = []
            for p in prompts:
                r = await self._llm.generate(prompt=p, max_tokens=500, temperature=0.7)
                raw_responses.append(r)

        # Build results and find best
        best: AgentResult | None = None
        for agent, response in zip(agents, raw_responses):
            elapsed = (time.monotonic() - start) * 1000
            result = AgentResult(
                agent_id=agent.id,
                response=response,
                confidence=route.confidence if response else 0.0,
                latency_ms=elapsed,
                source="swarm",
            )

            await self._event_bus.publish(DomainEvent(
                type=EventType.AGENT_COMPLETED,
                payload={"agent_id": agent.id, "confidence": result.confidence},
                timestamp=time.time(),
                source="orchestrator",
            ))

            if best is None or result.confidence > best.confidence:
                best = result

        if best is None:
            return AgentResult(
                agent_id="none",
                response="",
                confidence=0.0,
                latency_ms=(time.monotonic() - start) * 1000,
                source="llm",
            )
        return best

    # Audit H53: trust signal collapse mitigation. Of the 6 TrustSignals
    # fields, 4 are still hardcoded to constants pending Phase-2 work
    # that pulls them from verifier_store/case_store/round_table outcomes.
    # freshness_score is the easiest signal to ground in real data
    # because the scheduler already tracks per-agent success timestamps,
    # so we compute it from scheduler_state.recent_successes.
    # validation_rate is the existing real signal.
    #
    # Decay window matches typical agent "warm-cache" expectation:
    # 1.0 immediately after success, fading linearly over 7 days to 0.
    _FRESHNESS_DECAY_SECONDS = 7 * 86400  # 1 week

    def _compute_freshness(self, agent_id: str) -> float:
        last_success_ts = self._scheduler_state.recent_successes.get(agent_id, 0.0)
        if last_success_ts <= 0:
            return 0.0
        age = time.time() - last_success_ts
        if age <= 0:
            return 1.0
        if age >= self._FRESHNESS_DECAY_SECONDS:
            return 0.0
        return max(0.0, 1.0 - age / self._FRESHNESS_DECAY_SECONDS)

    async def _update_trust(self, result: AgentResult) -> None:
        """Update trust scores after task completion (Orchestrator is the sole writer).

        Audit H53 Phase 1: freshness_score is computed from the
        scheduler's recent_successes timestamp. The other 4 signals
        (hallucination_rate, consensus_agreement, correction_rate,
        fact_production_rate) remain constant pending Phase 2, which
        pulls them from verifier_store / case_store / round_table
        outcomes. Validation_rate continues to take result.confidence
        as before.
        """
        signals = TrustSignals(
            hallucination_rate=0.0,
            validation_rate=result.confidence,
            consensus_agreement=0.0,
            correction_rate=0.0,
            fact_production_rate=0.0,
            freshness_score=self._compute_freshness(result.agent_id),
        )
        await self._trust_store.update_trust(result.agent_id, signals)
