"""
Tests for profile/domain separation — capsule isolation, profile policies,
threshold-based risk gating, and runtime capsule wiring.

Proves that:
  - APIARY capsule matches bee-specific queries; COTTAGE does not
  - Profile policy files are loaded and produce different rules per profile
  - Profile thresholds affect auto-approve/deny decisions
  - Deny-by-default is preserved across all profiles
  - Runtime wires capsule and enriches query context
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.domain.autonomy import (
    Action,
    CapabilityCategory,
    CapabilityContract,
)
from waggledance.core.policy.constitution import (
    Constitution,
    ConstitutionRule,
    ProfileThresholds,
    load_constitution,
)
from waggledance.core.policy.policy_engine import PolicyDecision, PolicyEngine
from waggledance.core.policy.risk_scoring import RiskScorer


# ── Capsule isolation ────────────────────────────────────────

class TestCapsuleIsolation:
    """APIARY capsule matches bee queries; COTTAGE does not."""

    @pytest.fixture
    def apiary_capsule(self):
        from core.domain_capsule import DomainCapsule
        return DomainCapsule.load("apiary")

    @pytest.fixture
    def cottage_capsule(self):
        from core.domain_capsule import DomainCapsule
        return DomainCapsule.load("cottage")

    def test_apiary_matches_varroa(self, apiary_capsule):
        m = apiary_capsule.match_decision("varroa treatment dosage")
        assert m is not None
        assert m.decision_id == "varroa_treatment"

    def test_apiary_matches_honey_yield(self, apiary_capsule):
        m = apiary_capsule.match_decision("how much honey this summer?")
        assert m is not None
        assert m.decision_id == "honey_yield"

    def test_apiary_matches_swarm_risk(self, apiary_capsule):
        m = apiary_capsule.match_decision("is there swarm risk?")
        assert m is not None
        assert m.decision_id == "swarm_risk"

    def test_apiary_matches_disease(self, apiary_capsule):
        m = apiary_capsule.match_decision("nosema disease symptoms")
        assert m is not None
        assert m.decision_id == "disease_detection"

    def test_apiary_matches_queen_status(self, apiary_capsule):
        m = apiary_capsule.match_decision("queen laying pattern check")
        assert m is not None
        assert m.decision_id == "queen_status"

    def test_apiary_matches_acoustic(self, apiary_capsule):
        m = apiary_capsule.match_decision("acoustic sound analysis piping")
        assert m is not None
        assert m.decision_id == "acoustic_analysis"

    def test_cottage_no_varroa(self, cottage_capsule):
        m = cottage_capsule.match_decision("varroa treatment dosage")
        assert m is None

    def test_cottage_no_honey(self, cottage_capsule):
        m = cottage_capsule.match_decision("how much honey this summer?")
        assert m is None

    def test_cottage_no_swarm(self, cottage_capsule):
        m = cottage_capsule.match_decision("is there swarm risk?")
        assert m is None

    def test_cottage_matches_frost(self, cottage_capsule):
        m = cottage_capsule.match_decision("frost protection for pipes")
        assert m is not None
        assert m.decision_id == "frost_protection"

    def test_cottage_matches_heating(self, cottage_capsule):
        m = cottage_capsule.match_decision("heating cost estimate")
        assert m is not None
        assert m.decision_id == "heating_cost"

    def test_cottage_matches_water(self, cottage_capsule):
        m = cottage_capsule.match_decision("water pump pressure check")
        assert m is not None
        assert m.decision_id == "water_system"

    def test_cottage_matches_energy(self, cottage_capsule):
        m = cottage_capsule.match_decision("electricity consumption")
        assert m is not None
        assert m.decision_id == "energy_consumption"

    def test_apiary_no_frost(self, apiary_capsule):
        m = apiary_capsule.match_decision("frost protection for pipes")
        assert m is None

    def test_apiary_domain_is_apiary(self, apiary_capsule):
        assert apiary_capsule.domain == "apiary"

    def test_cottage_domain_is_cottage(self, cottage_capsule):
        assert cottage_capsule.domain == "cottage"

    def test_apiary_layer_for_bee_query(self, apiary_capsule):
        layer = apiary_capsule.get_layer_for_query("varroa treatment")
        assert layer == "model_based"

    def test_cottage_layer_for_frost_query(self, cottage_capsule):
        layer = cottage_capsule.get_layer_for_query("frost protection")
        assert layer == "rule_constraints"


# ── Profile policy loading ───────────────────────────────────

class TestProfilePolicyLoading:
    """Profile YAML files are loaded and merged into constitution."""

    @pytest.fixture
    def constitution(self):
        return load_constitution()

    def test_all_four_profiles_loaded(self, constitution):
        profiles = set(constitution.profile_overrides.keys())
        assert {"APIARY", "COTTAGE", "HOME", "FACTORY"} <= profiles

    def test_apiary_has_overrides(self, constitution):
        rules = constitution.get_rules_for_profile("APIARY")
        rule_ids = [r.rule_id for r in rules]
        assert "varroa_treatment_alert" in rule_ids

    def test_cottage_has_overrides(self, constitution):
        rules = constitution.get_rules_for_profile("COTTAGE")
        rule_ids = [r.rule_id for r in rules]
        assert "frost_protection_emergency" in rule_ids

    def test_home_has_overrides(self, constitution):
        rules = constitution.get_rules_for_profile("HOME")
        rule_ids = [r.rule_id for r in rules]
        assert "energy_auto_optimize" in rule_ids

    def test_factory_has_overrides(self, constitution):
        rules = constitution.get_rules_for_profile("FACTORY")
        rule_ids = [r.rule_id for r in rules]
        assert "deny_unsupervised_shutdown" in rule_ids

    def test_apiary_varroa_rule_allows(self, constitution):
        rules = constitution.get_rules_for_profile("APIARY")
        varroa = next(r for r in rules if r.rule_id == "varroa_treatment_alert")
        assert varroa.enforcement == "allow"
        assert "detect" in varroa.applies_to

    def test_factory_shutdown_denies(self, constitution):
        rules = constitution.get_rules_for_profile("FACTORY")
        shutdown = next(r for r in rules if r.rule_id == "deny_unsupervised_shutdown")
        assert shutdown.enforcement == "deny"
        assert "act" in shutdown.applies_to

    def test_unknown_profile_gets_only_global(self, constitution):
        rules = constitution.get_rules_for_profile("UNKNOWN")
        # Should only have global rules
        global_count = len(constitution.global_rules)
        assert len(rules) == global_count

    def test_profile_override_rules_extend_global(self, constitution):
        global_count = len(constitution.global_rules)
        apiary_rules = constitution.get_rules_for_profile("APIARY")
        assert len(apiary_rules) > global_count


# ── Profile thresholds ───────────────────────────────────────

class TestProfileThresholds:
    """Profile thresholds are loaded and affect risk gating."""

    @pytest.fixture
    def constitution(self):
        return load_constitution()

    def test_apiary_thresholds(self, constitution):
        t = constitution.get_thresholds("APIARY")
        assert t.max_composite_auto_approve == 0.5
        assert t.max_severity_auto_approve == 0.6

    def test_cottage_thresholds(self, constitution):
        t = constitution.get_thresholds("COTTAGE")
        assert t.max_composite_auto_approve == 0.5
        assert t.max_severity_auto_approve == 0.6

    def test_home_thresholds(self, constitution):
        t = constitution.get_thresholds("HOME")
        assert t.max_composite_auto_approve == 0.4
        assert t.max_severity_auto_approve == 0.5

    def test_factory_thresholds(self, constitution):
        t = constitution.get_thresholds("FACTORY")
        assert t.max_composite_auto_approve == 0.3
        assert t.max_severity_auto_approve == 0.4

    def test_factory_stricter_than_apiary(self, constitution):
        factory = constitution.get_thresholds("FACTORY")
        apiary = constitution.get_thresholds("APIARY")
        assert factory.max_composite_auto_approve < apiary.max_composite_auto_approve
        assert factory.max_severity_auto_approve < apiary.max_severity_auto_approve

    def test_unknown_profile_safe_defaults(self, constitution):
        t = constitution.get_thresholds("UNKNOWN")
        assert t.max_composite_auto_approve == 0.3  # Conservative default

    def test_night_learning_budgets(self, constitution):
        apiary = constitution.get_thresholds("APIARY")
        factory = constitution.get_thresholds("FACTORY")
        assert apiary.night_learning_budget_hours == 8
        assert factory.night_learning_budget_hours == 4

    def test_proactive_goals(self, constitution):
        home = constitution.get_thresholds("HOME")
        factory = constitution.get_thresholds("FACTORY")
        assert home.max_proactive_goals_per_day == 10
        assert factory.max_proactive_goals_per_day == 15


# ── Policy engine with profile thresholds ────────────────────

class TestPolicyProfileThresholds:
    """Profile thresholds influence auto-approve decisions."""

    def _make_engine(self, profile):
        constitution = Constitution(
            default_action="deny",
            global_rules=[
                ConstitutionRule(
                    rule_id="allow_readonly",
                    applies_to=["sense", "retrieve", "verify"],
                    enforcement="allow",
                ),
            ],
            profile_thresholds={
                "APIARY": ProfileThresholds(
                    max_composite_auto_approve=0.5,
                    max_severity_auto_approve=0.6),
                "FACTORY": ProfileThresholds(
                    max_composite_auto_approve=0.2,
                    max_severity_auto_approve=0.3),
            },
        )
        return PolicyEngine(constitution=constitution, profile=profile)

    def test_apiary_approves_moderate_risk(self):
        engine = self._make_engine("APIARY")
        action = Action(capability_id="learn.store")
        cap = CapabilityContract("learn.store", CapabilityCategory.LEARN)
        decision = engine.evaluate(action, cap, quality_path="gold")
        # LEARN gold has very low composite (~0.05) — both profiles should allow
        assert decision.approved is True

    def test_factory_denies_moderate_risk(self):
        engine = self._make_engine("FACTORY")
        scorer = engine._risk_scorer
        scorer.set_override("act.moderate", {
            "severity": 0.35, "reversibility": 0.6,
            "observability": 0.6, "uncertainty": 0.3, "blast_radius": 0.2,
        })
        action = Action(capability_id="act.moderate")
        cap = CapabilityContract("act.moderate", CapabilityCategory.ACT)
        decision = engine.evaluate(action, cap, quality_path="gold")
        # composite ~0.31, severity 0.35 → exceeds FACTORY thresholds (0.2/0.3)
        # No explicit allow rule → deny by default
        assert decision.approved is False

    def test_apiary_approves_same_risk(self):
        engine = self._make_engine("APIARY")
        scorer = engine._risk_scorer
        scorer.set_override("act.moderate", {
            "severity": 0.35, "reversibility": 0.6,
            "observability": 0.6, "uncertainty": 0.3, "blast_radius": 0.2,
        })
        action = Action(capability_id="act.moderate")
        cap = CapabilityContract("act.moderate", CapabilityCategory.ACT)
        decision = engine.evaluate(action, cap, quality_path="gold")
        # composite ~0.31, severity 0.35 → within APIARY thresholds (0.5/0.6)
        assert decision.approved is True

    def test_stats_includes_thresholds(self):
        engine = self._make_engine("APIARY")
        stats = engine.stats()
        assert stats["max_composite_auto_approve"] == 0.5
        assert stats["max_severity_auto_approve"] == 0.6

    def test_factory_stats_thresholds(self):
        engine = self._make_engine("FACTORY")
        stats = engine.stats()
        assert stats["max_composite_auto_approve"] == 0.2
        assert stats["max_severity_auto_approve"] == 0.3


# ── Deny-by-default preserved ────────────────────────────────

class TestDenyByDefault:
    """Deny-by-default behavior must be preserved across all profiles."""

    @pytest.fixture(params=["APIARY", "COTTAGE", "HOME", "FACTORY", "UNKNOWN"])
    def engine(self, request):
        constitution = load_constitution()
        return PolicyEngine(constitution=constitution, profile=request.param)

    def test_readonly_always_approved(self, engine):
        action = Action(capability_id="sense.mqtt")
        cap = CapabilityContract("sense.mqtt", CapabilityCategory.SENSE)
        decision = engine.evaluate(action, cap)
        assert decision.approved is True

    def test_high_risk_act_not_auto_approved(self, engine):
        scorer = engine._risk_scorer
        scorer.set_override("act.dangerous", {
            "severity": 0.9, "reversibility": 0.1,
            "observability": 0.3, "uncertainty": 0.8, "blast_radius": 0.8,
        })
        action = Action(capability_id="act.dangerous")
        cap = CapabilityContract(
            "act.dangerous", CapabilityCategory.ACT, rollback_possible=False)
        decision = engine.evaluate(action, cap, quality_path="bronze")
        # Very high risk → should NOT be approved
        assert decision.approved is False

    def test_default_action_is_deny(self, engine):
        assert engine._constitution.default_action == "deny"


# ── ConstitutionRule pattern matching ────────────────────────

class TestRulePatternMatching:
    """Test _pattern and _max condition suffixes for profile rules."""

    def test_pattern_match(self):
        rule = ConstitutionRule(
            rule_id="r1",
            conditions={"capability_id_pattern": "*.varroa*"},
        )
        assert rule.evaluate_conditions(
            {"capability_id": "detect.varroa_check"}) is True
        assert rule.evaluate_conditions(
            {"capability_id": "solve.math"}) is False

    def test_max_match(self):
        rule = ConstitutionRule(
            rule_id="r1",
            conditions={"severity_max": 0.5},
        )
        assert rule.evaluate_conditions({"severity": 0.5}) is True
        assert rule.evaluate_conditions({"severity": 0.3}) is True
        assert rule.evaluate_conditions({"severity": 0.6}) is False

    def test_pattern_wildcard(self):
        rule = ConstitutionRule(
            rule_id="r1",
            conditions={"capability_id_pattern": "act.*"},
        )
        assert rule.evaluate_conditions(
            {"capability_id": "act.heating"}) is True
        assert rule.evaluate_conditions(
            {"capability_id": "sense.mqtt"}) is False

    def test_combined_pattern_and_max(self):
        rule = ConstitutionRule(
            rule_id="r1",
            conditions={
                "capability_id_pattern": "*.energy*",
                "severity_max": 0.4,
            },
        )
        assert rule.evaluate_conditions(
            {"capability_id": "optimize.energy_cost", "severity": 0.3}) is True
        assert rule.evaluate_conditions(
            {"capability_id": "optimize.energy_cost", "severity": 0.5}) is False
        assert rule.evaluate_conditions(
            {"capability_id": "act.heating", "severity": 0.3}) is False


# ── Runtime capsule wiring ───────────────────────────────────

class TestRuntimeCapsuleWiring:
    """AutonomyRuntime loads capsule and enriches query context."""

    def test_runtime_has_capsule_attribute(self):
        from waggledance.core.autonomy.runtime import AutonomyRuntime
        rt = AutonomyRuntime(profile="DEFAULT")
        assert hasattr(rt, "capsule")

    def test_runtime_stats_includes_capsule(self):
        from waggledance.core.autonomy.runtime import AutonomyRuntime
        rt = AutonomyRuntime(profile="DEFAULT")
        stats = rt.stats()
        assert "capsule" in stats

    def test_handle_query_returns_capsule_decision(self):
        from waggledance.core.autonomy.runtime import AutonomyRuntime
        from core.domain_capsule import DomainCapsule
        rt = AutonomyRuntime(profile="DEFAULT")
        rt.capsule = DomainCapsule.load("apiary")
        result = rt.handle_query("varroa treatment dosage")
        assert result.get("capsule_decision") == "varroa_treatment"
        assert result.get("capsule_layer") == "model_based"

    def test_handle_query_no_capsule_decision_for_generic(self):
        from waggledance.core.autonomy.runtime import AutonomyRuntime
        from core.domain_capsule import DomainCapsule
        rt = AutonomyRuntime(profile="DEFAULT")
        rt.capsule = DomainCapsule.load("cottage")
        result = rt.handle_query("what is 2 + 2?")
        # Math query doesn't match any cottage capsule decision
        assert "capsule_decision" not in result

    def test_handle_query_cottage_no_bee_decision(self):
        from waggledance.core.autonomy.runtime import AutonomyRuntime
        from core.domain_capsule import DomainCapsule
        rt = AutonomyRuntime(profile="DEFAULT")
        rt.capsule = DomainCapsule.load("cottage")
        result = rt.handle_query("varroa treatment dosage")
        # Cottage capsule has no bee decisions
        assert "capsule_decision" not in result

    def test_runtime_without_capsule_works(self):
        from waggledance.core.autonomy.runtime import AutonomyRuntime
        rt = AutonomyRuntime(profile="DEFAULT")
        rt.capsule = None
        result = rt.handle_query("what is 2 + 2?")
        assert "intent" in result

    def test_capsule_failure_does_not_break_query(self):
        from waggledance.core.autonomy.runtime import AutonomyRuntime
        rt = AutonomyRuntime(profile="DEFAULT")
        mock_capsule = MagicMock()
        mock_capsule.match_decision.side_effect = RuntimeError("broken")
        rt.capsule = mock_capsule
        # Should not raise
        result = rt.handle_query("varroa treatment")
        assert "intent" in result


# ── Profile-driven policy integration ────────────────────────

class TestProfilePolicyIntegration:
    """End-to-end: profile selection changes policy decisions."""

    def test_factory_apiary_different_thresholds(self):
        c = load_constitution()
        factory_t = c.get_thresholds("FACTORY")
        apiary_t = c.get_thresholds("APIARY")
        assert factory_t.max_composite_auto_approve < apiary_t.max_composite_auto_approve

    def test_home_lighting_allowed(self):
        c = load_constitution()
        rules = c.get_rules_for_profile("HOME")
        lighting = [r for r in rules if r.rule_id == "lighting_auto_control"]
        assert len(lighting) == 1
        assert lighting[0].enforcement == "allow"

    def test_factory_shutdown_denied(self):
        c = load_constitution()
        rules = c.get_rules_for_profile("FACTORY")
        shutdown = [r for r in rules if r.rule_id == "deny_unsupervised_shutdown"]
        assert len(shutdown) == 1
        assert shutdown[0].enforcement == "deny"

    def test_apiary_queen_requires_approval(self):
        c = load_constitution()
        rules = c.get_rules_for_profile("APIARY")
        queen = [r for r in rules if r.rule_id == "queen_replacement_approval"]
        assert len(queen) == 1
        assert queen[0].enforcement == "require_approval"

    def test_cottage_structural_requires_approval(self):
        c = load_constitution()
        rules = c.get_rules_for_profile("COTTAGE")
        structural = [r for r in rules if r.rule_id == "deny_structural_changes"]
        assert len(structural) == 1
        assert structural[0].enforcement == "require_approval"

    def test_profile_override_capability_pattern_evaluated(self):
        """Profile override with capability_pattern condition should match."""
        c = load_constitution()
        rules = c.get_rules_for_profile("APIARY")
        varroa = next(r for r in rules if r.rule_id == "varroa_treatment_alert")
        assert varroa.evaluate_conditions(
            {"capability_id": "detect.varroa_check"}) is True
        assert varroa.evaluate_conditions(
            {"capability_id": "solve.math"}) is False
