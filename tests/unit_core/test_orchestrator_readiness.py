# SPDX-License-Identifier: BUSL-1.1
"""Audit H30 regression: Orchestrator.is_ready honors agent count.

Before this fix, a misconfigured WAGGLE_PROFILE silently filtered every
agent out and the runtime still served /ready as green because is_ready
only checked LLM + vector_store. These tests pin the behavior that a
zero-agent orchestrator reports not-ready so a routine probe (curl
/ready) fails loudly and the operator notices the misconfiguration.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from waggledance.bootstrap.event_bus import InMemoryEventBus
from waggledance.core.domain.agent import AgentDefinition
from waggledance.core.orchestration.orchestrator import Orchestrator
from waggledance.core.orchestration.round_table import RoundTableEngine
from waggledance.core.orchestration.scheduler import Scheduler
from waggledance.adapters.memory.in_memory_repository import InMemoryRepository
from waggledance.adapters.memory.in_memory_vector_store import InMemoryVectorStore
from waggledance.adapters.trust.in_memory_trust_store import InMemoryTrustStore


class _StubConfig:
    def get(self, key, default=None):
        return default

    def get_profile(self):
        return "HOME"


class _StubLLM:
    async def is_available(self):
        return True

    async def generate(self, **kwargs):
        return ""


def _make_orchestrator(agents):
    cfg = _StubConfig()
    llm = _StubLLM()
    eb = InMemoryEventBus()
    rt = RoundTableEngine(llm=llm, event_bus=eb)
    return Orchestrator(
        scheduler=Scheduler(config=cfg),
        round_table=rt,
        memory=InMemoryRepository(),
        vector_store=InMemoryVectorStore(),
        llm=llm,
        trust_store=InMemoryTrustStore(),
        event_bus=eb,
        config=cfg,
        agents=agents,
    )


class TestOrchestratorReadinessAgentCount:
    def test_zero_agents_reports_not_ready(self):
        """Audit H30: empty agent list -> /ready returns 503."""
        orch = _make_orchestrator(agents=[])
        ready = asyncio.run(orch.is_ready())
        assert ready is False

    def test_one_agent_can_report_ready(self):
        """Sanity: a single populated agent is enough to clear the gate."""
        dummy = AgentDefinition(
            id="dummy",
            name="Dummy",
            domain="general",
            tags=[],
            skills=[],
            trust_level=0,
            specialization_score=0.0,
            active=True,
            profile="HOME",
        )
        orch = _make_orchestrator(agents=[dummy])
        ready = asyncio.run(orch.is_ready())
        assert ready is True
