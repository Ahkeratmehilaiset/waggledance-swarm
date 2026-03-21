"""Round Table consensus engine.

Ported from core/round_table_controller.py. Key logic preserved:
- Sequential discussion: each agent sees previous 3 responses
- Queen synthesis: combined summary with consensus identification
- Agent selection by keyword relevance + trust level boost
- All LLM calls through LLMPort (no direct Ollama)
"""

import time
from dataclasses import dataclass, field

from waggledance.core.domain.agent import AgentDefinition, AgentResult
from waggledance.core.domain.events import DomainEvent, EventType
from waggledance.core.domain.task import TaskRequest
from waggledance.core.ports.event_bus_port import EventBusPort
from waggledance.core.ports.llm_port import LLMPort


@dataclass
class ConsensusResult:
    """Outcome of a Round Table deliberation."""

    consensus: str
    confidence: float
    participating_agents: list[str] = field(default_factory=list)
    dissenting_views: list[str] = field(default_factory=list)
    latency_ms: float = 0.0


class RoundTableEngine:
    """Multi-agent consensus through sequential discussion and synthesis."""

    def __init__(self, llm: LLMPort, event_bus: EventBusPort) -> None:
        self._llm = llm
        self._event_bus = event_bus

    async def deliberate(
        self,
        task: TaskRequest,
        agents: list[AgentDefinition],
        agent_responses: list[AgentResult],
    ) -> ConsensusResult:
        """Run Round Table deliberation with synthesis.

        Phase 1: Each agent reviews others' responses (sequential, last 3 visible)
        Phase 2: Synthesize consensus from all perspectives
        """
        start = time.monotonic()

        await self._event_bus.publish(DomainEvent(
            type=EventType.ROUND_TABLE_STARTED,
            payload={"query": task.query, "agent_count": len(agents)},
            timestamp=time.time(),
            source="round_table",
        ))

        discussion: list[dict[str, str]] = []
        for agent, result in zip(agents, agent_responses):
            prev_text = ""
            if discussion:
                recent = discussion[-3:]
                prev_text = "\n".join(
                    f"[{d['agent']}]: {d['response']}" for d in recent
                )

            review_prompt = (
                f"ROUND TABLE DISCUSSION\n"
                f"Topic: {task.query}\n"
                + (f"Previous speakers:\n{prev_text}\n\n" if prev_text else "")
                + f"You are {agent.name} ({agent.domain}). "
                f"Your initial answer was: {result.response[:200]}\n"
                f"Do you agree with the group? Refine your view. 2 sentences max."
            )

            refined = await self._llm.generate(
                prompt=review_prompt,
                max_tokens=150,
                temperature=0.6,
            )
            if refined:
                discussion.append({
                    "agent": agent.name,
                    "response": refined,
                })

        if not discussion:
            return ConsensusResult(
                consensus="No agents participated",
                confidence=0.0,
                latency_ms=(time.monotonic() - start) * 1000,
            )

        all_views = "\n".join(
            f"[{d['agent']}]: {d['response']}" for d in discussion
        )
        synthesis_prompt = (
            f"ROUND TABLE SYNTHESIS\n"
            f"Topic: {task.query}\n"
            f"Agent responses:\n{all_views}\n\n"
            f"Synthesize the key consensus. Identify agreements and "
            f"the most important practical takeaway. 3 sentences."
        )

        synthesis = await self._llm.generate(
            prompt=synthesis_prompt,
            max_tokens=200,
            temperature=0.5,
        )

        participating = [d["agent"] for d in discussion]
        dissenting = self._find_dissent(discussion)

        confidence = min(0.9, 0.5 + len(discussion) * 0.1)

        elapsed_ms = (time.monotonic() - start) * 1000

        await self._event_bus.publish(DomainEvent(
            type=EventType.ROUND_TABLE_COMPLETED,
            payload={
                "query": task.query,
                "participants": len(participating),
                "confidence": confidence,
            },
            timestamp=time.time(),
            source="round_table",
        ))

        return ConsensusResult(
            consensus=synthesis or "Synthesis unavailable",
            confidence=confidence,
            participating_agents=participating,
            dissenting_views=dissenting,
            latency_ms=elapsed_ms,
        )

    def _find_dissent(self, discussion: list[dict[str, str]]) -> list[str]:
        """Identify dissenting views by checking for disagreement markers."""
        dissent_markers = {
            "disagree", "however", "but i think", "on the contrary",
            "not necessarily", "eri mieltä", "mutta",
        }
        dissenting: list[str] = []
        for entry in discussion:
            response_lower = entry["response"].lower()
            if any(marker in response_lower for marker in dissent_markers):
                dissenting.append(
                    f"{entry['agent']}: {entry['response'][:100]}"
                )
        return dissenting
