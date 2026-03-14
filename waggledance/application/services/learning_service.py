"""Learning service — night mode learning with convergence stall detection (BUG 3 fix).

Ported from core/night_enricher.py and hivemind.py night learning logic.
LearningService NEVER writes to MemoryRepositoryPort directly —
all memory writes go through MemoryService.ingest().
"""

import logging
import time

from waggledance.core.domain.events import DomainEvent, EventType
from waggledance.core.domain.memory_record import MemoryRecord
from waggledance.core.orchestration.orchestrator import Orchestrator
from waggledance.core.ports.event_bus_port import EventBusPort
from waggledance.core.ports.llm_port import LLMPort

log = logging.getLogger(__name__)


class LearningService:
    """Night mode learning with convergence stall detection."""

    STALL_THRESHOLD: int = 10

    def __init__(
        self,
        orchestrator: Orchestrator,
        memory_service: "MemoryService",  # noqa: F821 — forward ref
        llm: LLMPort,
        event_bus: EventBusPort,
        stall_threshold: int = 10,
    ) -> None:
        self._orchestrator = orchestrator
        self._memory_service = memory_service
        self._llm = llm
        self._event_bus = event_bus
        self._stall_threshold = stall_threshold
        self._consecutive_empty_cycles: int = 0
        self._topics_used: set[str] = set()
        self._topic_pool: list[str] = [
            "Finnish beekeeping best practices",
            "Varroa mite treatment methods",
            "Seasonal hive management tasks",
            "Honey harvest quality control",
            "Winter bee colony survival",
            "Swarm prevention techniques",
            "Queen bee health indicators",
            "Hive temperature regulation",
            "Bee disease identification",
            "Pollination garden planning",
        ]
        self._topic_index: int = 0

    async def run_night_tick(self) -> dict:
        """One learning cycle with stall detection.

        1. Select topic from pool
        2. Generate fact candidates (via LLM)
        3. Validate candidates
        4. Store validated facts via memory_service.ingest()
        5. Track consecutive empty cycles
        6. If stall detected: reset topic selection, emit event
        7. Return tick stats
        """
        topic = self._select_topic()

        generate_prompt = (
            f"Generate a specific, verifiable fact about: {topic}\n"
            f"The fact should be practical and actionable. "
            f"Respond with just the fact, no preamble."
        )

        candidate = await self._llm.generate(
            prompt=generate_prompt,
            model="default",
            temperature=0.8,
            max_tokens=200,
        )

        new_facts = 0
        if candidate:
            validate_prompt = (
                f"Is this a valid, specific, and useful fact?\n"
                f"Fact: {candidate}\n"
                f"Answer YES or NO with a brief reason."
            )
            validation = await self._llm.generate(
                prompt=validate_prompt,
                temperature=0.3,
                max_tokens=50,
            )

            if validation and validation.strip().upper().startswith("YES"):
                await self._memory_service.ingest(
                    content=candidate,
                    source="night_learning",
                    tags=["auto_generated", topic.split()[0].lower()],
                )
                new_facts = 1

        if new_facts == 0:
            self._consecutive_empty_cycles += 1
        else:
            self._consecutive_empty_cycles = 0

        stall_detected = self._consecutive_empty_cycles >= self._stall_threshold
        if stall_detected:
            self._reset_topic_selection()
            self._consecutive_empty_cycles = 0
            await self._event_bus.publish(DomainEvent(
                type=EventType.NIGHT_STALL_DETECTED,
                payload={"threshold": self._stall_threshold},
                timestamp=time.time(),
                source="learning_service",
            ))

        return {
            "new_facts": new_facts,
            "stall_detected": stall_detected,
            "consecutive_empty": self._consecutive_empty_cycles,
        }

    async def enrich_gap(self, topic: str) -> list[MemoryRecord]:
        """Generate additional facts for a specific topic gap."""
        records: list[MemoryRecord] = []
        prompt = (
            f"List 3 specific facts about: {topic}\n"
            f"Each fact on a new line. Be practical and verifiable."
        )
        response = await self._llm.generate(prompt=prompt, max_tokens=300)

        if response:
            for line in response.strip().split("\n"):
                line = line.strip().lstrip("0123456789.-) ")
                if len(line) > 20:
                    record = await self._memory_service.ingest(
                        content=line,
                        source="gap_enrichment",
                        tags=[topic.split()[0].lower()],
                    )
                    records.append(record)

        return records

    async def retrain_micromodel_if_due(self) -> bool:
        """Placeholder — micromodel training excluded from this sprint."""
        return False

    def _select_topic(self) -> str:
        """Select next topic from pool, cycling through available topics."""
        if self._topic_index >= len(self._topic_pool):
            self._topic_index = 0
        topic = self._topic_pool[self._topic_index]
        self._topic_index += 1
        self._topics_used.add(topic)
        return topic

    def _reset_topic_selection(self) -> None:
        """Reset topic pool to break convergence stall."""
        self._topic_index = 0
        self._topics_used.clear()
        log.info("Topic selection reset due to convergence stall")
