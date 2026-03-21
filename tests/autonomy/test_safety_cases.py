"""
Tests for SafetyCase + SafetyCaseBuilder.

Covers:
- SafetyEvidence creation
- SafetyCase confidence computation
- SafetyCase verdict determination
- SafetyCaseBuilder with various evidence combinations
- Edge cases (no evidence, only counter evidence)
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.policy.safety_cases import (
    SafetyCase,
    SafetyCaseBuilder,
    SafetyEvidence,
)


class TestSafetyEvidence:
    def test_create_evidence(self):
        e = SafetyEvidence(
            evidence_type="historical",
            description="High success rate",
            strength=0.9,
            source="trust_adapter",
        )
        assert e.evidence_type == "historical"
        assert e.strength == 0.9
        assert e.timestamp > 0

    def test_default_strength(self):
        e = SafetyEvidence(evidence_type="analytical", description="test")
        assert e.strength == 0.5


class TestSafetyCase:
    def test_empty_case_confidence(self):
        case = SafetyCase(
            case_id="sc-test", action_id="a1",
            capability_id="c1", claim="test claim",
        )
        assert case.compute_confidence() == 0.0

    def test_supporting_only(self):
        case = SafetyCase(
            case_id="sc-test", action_id="a1",
            capability_id="c1", claim="test",
        )
        case.supporting_evidence.append(
            SafetyEvidence("historical", "good", strength=0.9)
        )
        conf = case.compute_confidence()
        assert 0.5 < conf <= 1.0

    def test_counter_only(self):
        case = SafetyCase(
            case_id="sc-test", action_id="a1",
            capability_id="c1", claim="test",
        )
        case.counter_evidence.append(
            SafetyEvidence("analytical", "bad", strength=0.9)
        )
        conf = case.compute_confidence()
        assert 0.0 <= conf < 0.5

    def test_verdict_safe(self):
        case = SafetyCase(
            case_id="sc-test", action_id="a1",
            capability_id="c1", claim="safe action",
        )
        case.supporting_evidence.append(
            SafetyEvidence("historical", "high rate", strength=0.95)
        )
        verdict = case.determine_verdict(threshold=0.7)
        assert verdict == "safe"
        assert case.verdict == "safe"

    def test_verdict_needs_review_with_counter(self):
        case = SafetyCase(
            case_id="sc-test", action_id="a1",
            capability_id="c1", claim="risky",
        )
        case.supporting_evidence.append(
            SafetyEvidence("historical", "ok", strength=0.9)
        )
        case.counter_evidence.append(
            SafetyEvidence("analytical", "risk", strength=0.3)
        )
        verdict = case.determine_verdict(threshold=0.7)
        assert verdict == "needs_review"

    def test_verdict_unsafe(self):
        case = SafetyCase(
            case_id="sc-test", action_id="a1",
            capability_id="c1", claim="bad",
        )
        case.counter_evidence.append(
            SafetyEvidence("analytical", "very bad", strength=0.95)
        )
        verdict = case.determine_verdict()
        assert verdict == "unsafe"

    def test_to_dict(self):
        case = SafetyCase(
            case_id="sc-abc", action_id="a1",
            capability_id="c1", claim="test",
        )
        d = case.to_dict()
        assert d["case_id"] == "sc-abc"
        assert "verdict" in d
        assert "overall_confidence" in d


class TestSafetyCaseBuilder:
    def test_build_simple_case(self):
        builder = SafetyCaseBuilder()
        case = builder.build_case(
            action_id="action-123",
            capability_id="solve.math",
            claim="Math solve is safe",
            historical_success_rate=0.95,
        )
        assert case.case_id.startswith("sc-")
        assert len(case.supporting_evidence) >= 1
        assert case.verdict in ("safe", "needs_review", "unsafe", "pending")

    def test_build_with_verifier(self):
        builder = SafetyCaseBuilder()
        case = builder.build_case(
            action_id="action-456",
            capability_id="solve.symbolic",
            claim="Symbolic solve safe",
            historical_success_rate=0.90,
            verifier_pass_rate=0.92,
        )
        assert len(case.supporting_evidence) >= 2

    def test_build_with_anti_pattern(self):
        builder = SafetyCaseBuilder()
        case = builder.build_case(
            action_id="action-789",
            capability_id="bad.cap",
            claim="Known bad pattern",
            is_anti_pattern=True,
        )
        assert len(case.counter_evidence) >= 1
        assert case.verdict in ("unsafe", "needs_review")

    def test_build_with_known_procedure(self):
        builder = SafetyCaseBuilder()
        case = builder.build_case(
            action_id="action-aaa",
            capability_id="good.cap",
            claim="Proven procedure",
            is_known_procedure=True,
            historical_success_rate=0.95,
        )
        assert case.verdict == "safe"

    def test_get_case(self):
        builder = SafetyCaseBuilder()
        case = builder.build_case(
            action_id="action-lookup",
            capability_id="c1",
            claim="test",
        )
        found = builder.get_case(case.case_id)
        assert found is not None
        assert found.action_id == "action-lookup"

    def test_get_recent_cases(self):
        builder = SafetyCaseBuilder()
        for i in range(5):
            builder.build_case(f"a-{i}", f"c-{i}", f"claim {i}")
        recent = builder.get_recent_cases(limit=3)
        assert len(recent) == 3

    def test_stats(self):
        builder = SafetyCaseBuilder()
        builder.build_case("a1", "c1", "safe", historical_success_rate=0.95)
        builder.build_case("a2", "c2", "bad", is_anti_pattern=True)
        stats = builder.stats()
        assert stats["total_cases"] == 2
        assert isinstance(stats["by_verdict"], dict)

    def test_max_cases_eviction(self):
        builder = SafetyCaseBuilder()
        builder._max_cases = 5
        for i in range(10):
            builder.build_case(f"a-{i}", f"c-{i}", f"claim {i}")
        assert len(builder._cases) == 5
