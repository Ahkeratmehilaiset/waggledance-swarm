# SPDX-License-Identifier: BUSL-1.1
"""Audit H53 regression - trust signals derive from real data.

Before this fix all 5 non-validation TrustSignals fields were
hardcoded to constants (hallucination=0, consensus=0, correction=0,
fact_production=0, freshness=1.0). The composite_score formula
therefore collapsed to a 0.50-0.70 band, narrower than the
trust_level thresholds (MASTER >= 0.8) could reach.

H53 mitigation grounds freshness_score in scheduler timestamps and
the other trust signals in AgentResult.metadata when runtime evidence
is present. Empty metadata preserves the old 0.0 defaults.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest

from waggledance.core.orchestration.orchestrator import Orchestrator
from waggledance.core.orchestration.scheduler import SchedulerState


class _StubScheduler:
    def __init__(self) -> None:
        pass


class _AgentResult:
    def __init__(self, agent_id, confidence=0.7, metadata=None):
        self.agent_id = agent_id
        self.confidence = confidence
        self.metadata = metadata or {}


def _make_orchestrator(recent_successes: dict[str, float]):
    """Build an Orchestrator with controlled scheduler state."""
    trust_store = AsyncMock()
    orch = Orchestrator(
        scheduler=_StubScheduler(),
        round_table=None,
        memory=None,
        vector_store=None,
        llm=None,
        trust_store=trust_store,
        event_bus=None,
        config=None,
        agents=[],
    )
    orch._scheduler_state = SchedulerState(recent_successes=dict(recent_successes))
    return orch, trust_store


class TestFreshnessComputation:
    def test_fresh_success_just_now_returns_one(self):
        orch, _ = _make_orchestrator({"alpha": time.time()})
        score = orch._compute_freshness("alpha")
        assert 0.99 < score <= 1.0

    def test_one_day_old_decays_to_about_six_sevenths(self):
        ts = time.time() - 86400  # 1 day ago
        orch, _ = _make_orchestrator({"alpha": ts})
        score = orch._compute_freshness("alpha")
        # Linear decay over 7 days: 1 - 1/7 ~= 0.857
        assert 0.84 < score < 0.87

    def test_full_decay_window_returns_zero(self):
        ts = time.time() - 7 * 86400  # exactly 1 week ago
        orch, _ = _make_orchestrator({"alpha": ts})
        score = orch._compute_freshness("alpha")
        assert score == 0.0

    def test_well_past_decay_window_returns_zero(self):
        ts = time.time() - 30 * 86400  # 30 days ago
        orch, _ = _make_orchestrator({"alpha": ts})
        assert orch._compute_freshness("alpha") == 0.0

    def test_unknown_agent_returns_zero(self):
        orch, _ = _make_orchestrator({"alpha": time.time()})
        assert orch._compute_freshness("never_seen") == 0.0

    def test_zero_timestamp_treated_as_unknown(self):
        # Defensive: agent registered but never had a success
        orch, _ = _make_orchestrator({"alpha": 0.0})
        assert orch._compute_freshness("alpha") == 0.0

    def test_future_timestamp_caps_at_one(self):
        # Clock skew / NTP correction: timestamp in the future
        ts = time.time() + 3600
        orch, _ = _make_orchestrator({"alpha": ts})
        assert orch._compute_freshness("alpha") == 1.0


class TestUpdateTrustSignals:
    @pytest.mark.asyncio
    async def test_update_trust_passes_real_freshness(self):
        ts = time.time() - 86400  # 1 day old
        orch, trust_store = _make_orchestrator({"alpha": ts})
        result = _AgentResult("alpha", confidence=0.5)

        await orch._update_trust(result)

        trust_store.update_trust.assert_awaited_once()
        call = trust_store.update_trust.await_args
        agent_id_arg, signals = call.args
        assert agent_id_arg == "alpha"
        assert 0.84 < signals.freshness_score < 0.87
        # Validation continues to come from result.confidence.
        assert signals.validation_rate == 0.5
        # Empty metadata preserves the old defaults.
        assert signals.hallucination_rate == 0.0
        assert signals.consensus_agreement == 0.0
        assert signals.correction_rate == 0.0
        assert signals.fact_production_rate == 0.0

    @pytest.mark.asyncio
    async def test_update_trust_unknown_agent_freshness_zero(self):
        orch, trust_store = _make_orchestrator({})  # empty scheduler state
        await orch._update_trust(_AgentResult("never_seen"))
        signals = trust_store.update_trust.await_args.args[1]
        assert signals.freshness_score == 0.0

    @pytest.mark.asyncio
    async def test_update_trust_uses_explicit_metadata_trust_signals(self):
        orch, trust_store = _make_orchestrator({"alpha": time.time()})
        result = _AgentResult(
            "alpha",
            confidence=0.8,
            metadata={
                "hallucination_rate": 0.25,
                "consensus_agreement": 0.75,
                "correction_rate": 0.10,
                "fact_production_rate": 0.60,
            },
        )

        await orch._update_trust(result)

        signals = trust_store.update_trust.await_args.args[1]
        assert signals.validation_rate == pytest.approx(0.8)
        assert signals.hallucination_rate == pytest.approx(0.25)
        assert signals.consensus_agreement == pytest.approx(0.75)
        assert signals.correction_rate == pytest.approx(0.10)
        assert signals.fact_production_rate == pytest.approx(0.60)
        assert 0.99 < signals.freshness_score <= 1.0

    @pytest.mark.asyncio
    async def test_update_trust_derives_metadata_from_runtime_evidence(self):
        orch, trust_store = _make_orchestrator({"alpha": time.time()})
        result = _AgentResult(
            "alpha",
            confidence=0.9,
            metadata={
                "verifier_result": {
                    "hallucination": True,
                    "has_correction": True,
                },
                "consensus": {"agreement": 0.5},
                "facts_produced": 2,
                "fact_window_hours": 4,
            },
        )

        await orch._update_trust(result)

        signals = trust_store.update_trust.await_args.args[1]
        assert signals.hallucination_rate == 1.0
        assert signals.consensus_agreement == pytest.approx(0.5)
        assert signals.correction_rate == 1.0
        assert signals.fact_production_rate == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_update_trust_clamps_metadata_rates(self):
        orch, trust_store = _make_orchestrator({"alpha": time.time()})
        result = _AgentResult(
            "alpha",
            confidence=1.25,
            metadata={
                "hallucination_rate": -0.25,
                "consensus_agreement": 1.25,
                "correction_rate": float("nan"),
                "fact_production_rate": 2.0,
            },
        )

        await orch._update_trust(result)

        signals = trust_store.update_trust.await_args.args[1]
        assert signals.validation_rate == 1.0
        assert signals.hallucination_rate == 0.0
        assert signals.consensus_agreement == 1.0
        assert signals.correction_rate == 0.0
        assert signals.fact_production_rate == 1.0
