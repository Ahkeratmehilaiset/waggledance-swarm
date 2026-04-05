"""Tests for Gemma-assisted candidate lab and verifier advisory.

Covers:
- candidate-lab path uses heavy model only when configured
- verifier-assist remains advisory only
- structured JSON schema validation
- no production route mutation
- provenance logging
- graceful degradation when Gemma unavailable
"""

import asyncio
import json
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from waggledance.application.services.solver_candidate_lab import (
    CandidateState,
    GemmaVerifierAdvisor,
    SolverCandidate,
    SolverCandidateLab,
)
from waggledance.application.services.gemma_profile_router import (
    GemmaProfileRouter,
    GemmaTier,
)


# ── Fixtures ──────────────────────────────────────────────


@dataclass
class FakeSettings:
    gemma_enabled: bool = True
    gemma_fast_model: str = "gemma4:e4b"
    gemma_heavy_model: str = "gemma4:26b"
    gemma_active_profile: str = "dual_tier"
    gemma_heavy_reasoning_only: bool = True
    gemma_degrade_to_default: bool = True


def make_gemma_router(response='{"rationale": "enriched", "confidence_adjustment": 0.1, "suggested_rules": ["rule1"]}'):
    llm = AsyncMock()
    llm.generate = AsyncMock(return_value=response)
    settings = FakeSettings()
    return GemmaProfileRouter(settings=settings, default_llm=llm)


def make_disabled_router():
    settings = FakeSettings(gemma_enabled=False, gemma_active_profile="disabled")
    llm = AsyncMock()
    llm.generate = AsyncMock(return_value="default")
    return GemmaProfileRouter(settings=settings, default_llm=llm)


def make_failure_cases(n=4, intent="thermal"):
    return [
        {
            "trajectory_id": f"traj_{i}",
            "intent": intent,
            "data": json.dumps({"query": f"What is the temperature in zone {i}?"}),
        }
        for i in range(n)
    ]


# ── Candidate Lab: Gemma-assisted ────────────────────────


class TestGemmaAssistedAnalysis:
    @pytest.mark.asyncio
    async def test_gemma_enriches_candidates(self):
        router = make_gemma_router()
        lab = SolverCandidateLab(gemma_router=router)

        candidates = await lab.gemma_assisted_analysis(make_failure_cases())

        assert len(candidates) >= 1
        # Should have been enriched with Gemma advisory
        c = candidates[0]
        assert "Gemma advisory" in c.rationale
        assert lab._gemma_assisted_count >= 1

    @pytest.mark.asyncio
    async def test_falls_back_to_deterministic_when_disabled(self):
        router = make_disabled_router()
        lab = SolverCandidateLab(gemma_router=router)

        candidates = await lab.gemma_assisted_analysis(make_failure_cases())

        # Still produces candidates (deterministic path)
        assert len(candidates) >= 1
        # But no Gemma enrichment
        assert lab._gemma_assisted_count == 0

    @pytest.mark.asyncio
    async def test_falls_back_when_no_router(self):
        lab = SolverCandidateLab(gemma_router=None)
        candidates = await lab.gemma_assisted_analysis(make_failure_cases())
        assert len(candidates) >= 1
        assert lab._gemma_assisted_count == 0

    @pytest.mark.asyncio
    async def test_gemma_failure_does_not_break_candidates(self):
        router = make_gemma_router()
        # Make Gemma generation fail
        router._default_llm.generate = AsyncMock(side_effect=Exception("model crashed"))
        lab = SolverCandidateLab(gemma_router=router)

        candidates = await lab.gemma_assisted_analysis(make_failure_cases())

        # Should still have candidates from deterministic path
        assert len(candidates) >= 1
        # No enrichment happened
        assert lab._gemma_assisted_count == 0

    @pytest.mark.asyncio
    async def test_candidate_never_goes_to_production(self):
        """Candidates remain in isolated registry, not production routing."""
        router = make_gemma_router()
        lab = SolverCandidateLab(gemma_router=router)

        candidates = await lab.gemma_assisted_analysis(make_failure_cases())

        for c in candidates:
            # No candidate should be in READY_FOR_CANARY or beyond
            assert c.state in (CandidateState.PROPOSED, CandidateState.COMPILED,
                              CandidateState.FAILED_VALIDATION)

    @pytest.mark.asyncio
    async def test_confidence_clamped(self):
        # Return a huge confidence adjustment
        router = make_gemma_router(
            '{"rationale": "great", "confidence_adjustment": 5.0, "suggested_rules": []}'
        )
        lab = SolverCandidateLab(gemma_router=router)

        candidates = await lab.gemma_assisted_analysis(make_failure_cases())

        for c in candidates:
            assert c.confidence <= 0.95  # Clamped

    @pytest.mark.asyncio
    async def test_status_includes_gemma_info(self):
        router = make_gemma_router()
        lab = SolverCandidateLab(gemma_router=router)
        status = lab.status()
        assert "gemma_assist" in status
        assert status["gemma_assist"]["available"] is True

    @pytest.mark.asyncio
    async def test_status_without_gemma(self):
        lab = SolverCandidateLab(gemma_router=None)
        status = lab.status()
        assert status["gemma_assist"]["available"] is False


# ── JSON schema validation ────────────────────────────────


class TestGemmaJsonParsing:
    def test_valid_json_parsed(self):
        lab = SolverCandidateLab()
        result = lab._parse_gemma_enrichment(
            '{"rationale": "test", "confidence_adjustment": 0.1, "suggested_rules": ["r1"]}'
        )
        assert result is not None
        assert result["rationale"] == "test"
        assert result["confidence_adjustment"] == 0.1

    def test_json_in_text_extracted(self):
        lab = SolverCandidateLab()
        result = lab._parse_gemma_enrichment(
            'Here is my analysis: {"rationale": "found pattern", "confidence_adjustment": 0.05} done.'
        )
        assert result is not None
        assert result["rationale"] == "found pattern"

    def test_invalid_json_returns_none(self):
        lab = SolverCandidateLab()
        assert lab._parse_gemma_enrichment("not json at all") is None

    def test_missing_rationale_returns_none(self):
        lab = SolverCandidateLab()
        assert lab._parse_gemma_enrichment('{"other": "field"}') is None

    def test_confidence_adjustment_clamped(self):
        lab = SolverCandidateLab()
        result = lab._parse_gemma_enrichment(
            '{"rationale": "x", "confidence_adjustment": 99.0}'
        )
        assert result["confidence_adjustment"] == 0.2  # Clamped to max 0.2

    def test_negative_confidence_clamped(self):
        lab = SolverCandidateLab()
        result = lab._parse_gemma_enrichment(
            '{"rationale": "x", "confidence_adjustment": -5.0}'
        )
        assert result["confidence_adjustment"] == -0.2  # Clamped to min -0.2

    def test_non_numeric_confidence_defaults_zero(self):
        lab = SolverCandidateLab()
        result = lab._parse_gemma_enrichment(
            '{"rationale": "x", "confidence_adjustment": "high"}'
        )
        assert result["confidence_adjustment"] == 0.0


# ── Verifier Advisory ────────────────────────────────────


class TestVerifierAdvisory:
    @pytest.mark.asyncio
    async def test_advisory_when_enabled(self):
        router = make_gemma_router('{"agree": true, "note": "looks correct"}')
        advisor = GemmaVerifierAdvisor(gemma_router=router)

        result = await advisor.advise_on_verification(
            action_summary="Solved thermal query",
            verifier_passed=True,
            verifier_confidence=0.85,
            residual_improvement=0.12,
        )

        assert result["advisory"] == "provided"
        assert result.get("agree") is True
        assert advisor._advisory_count == 1
        assert advisor._advisory_accepted == 1

    @pytest.mark.asyncio
    async def test_advisory_disagree(self):
        router = make_gemma_router('{"agree": false, "note": "suspicious confidence"}')
        advisor = GemmaVerifierAdvisor(gemma_router=router)

        result = await advisor.advise_on_verification(
            action_summary="Solved query",
            verifier_passed=True,
            verifier_confidence=0.5,
            residual_improvement=0.01,
        )

        assert result.get("agree") is False
        assert advisor._advisory_accepted == 0

    @pytest.mark.asyncio
    async def test_advisory_unavailable_when_disabled(self):
        router = make_disabled_router()
        advisor = GemmaVerifierAdvisor(gemma_router=router)

        result = await advisor.advise_on_verification(
            action_summary="test",
            verifier_passed=True,
            verifier_confidence=0.9,
            residual_improvement=0.1,
        )

        assert result["advisory"] == "unavailable"
        assert advisor._advisory_count == 0

    @pytest.mark.asyncio
    async def test_advisory_unavailable_without_router(self):
        advisor = GemmaVerifierAdvisor(gemma_router=None)
        assert not advisor.enabled
        result = await advisor.advise_on_verification("test", True, 0.9, 0.1)
        assert result["advisory"] == "unavailable"

    @pytest.mark.asyncio
    async def test_advisory_failure_returns_failed(self):
        router = make_gemma_router()
        router._default_llm.generate = AsyncMock(side_effect=Exception("crash"))
        advisor = GemmaVerifierAdvisor(gemma_router=router)

        result = await advisor.advise_on_verification("test", True, 0.9, 0.1)
        assert result["advisory"] == "failed"

    def test_status(self):
        router = make_gemma_router()
        advisor = GemmaVerifierAdvisor(gemma_router=router)
        status = advisor.status()
        assert status["enabled"] is True
        assert status["advisory_count"] == 0

    def test_parse_valid_advisory(self):
        advisor = GemmaVerifierAdvisor()
        result = advisor._parse_advisory('{"agree": true, "note": "ok"}')
        assert result is not None
        assert result["agree"] is True

    def test_parse_invalid_advisory(self):
        advisor = GemmaVerifierAdvisor()
        assert advisor._parse_advisory("not json") is None
        assert advisor._parse_advisory('{"note": "missing agree"}') is None
