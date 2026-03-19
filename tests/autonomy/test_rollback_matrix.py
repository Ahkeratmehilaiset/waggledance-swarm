# SPDX-License-Identifier: Apache-2.0
"""Tests for v3.2 rollback matrix — every high-risk feature can be disabled (Phase 10)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class TestMetaOptimizerRollback:
    """Meta-optimizer can be rolled back to default hyperparameters."""

    def test_rollback_returns_defaults(self):
        from waggledance.core.specialist_models.meta_optimizer import (
            MetaOptimizerState, get_default_params,
        )
        state = MetaOptimizerState(
            model_id="test", default_lr=0.01, default_feature_count=50,
        )
        defaults = get_default_params(state)
        assert defaults.learning_rate == 0.01
        assert defaults.feature_set_size == 50
        assert defaults.reason == "rollback to defaults"

    def test_consecutive_failures_trigger_rollback(self):
        from waggledance.core.specialist_models.meta_optimizer import (
            MetaOptimizerState, CanaryRecord, record_canary_result,
        )
        state = MetaOptimizerState(model_id="test")
        r1 = CanaryRecord(model_id="test", cycle=1, learning_rate=0.05,
                          feature_count=50, accuracy=0.8,
                          accuracy_delta=-0.05, proposed_by_meta=True)
        r2 = CanaryRecord(model_id="test", cycle=2, learning_rate=0.05,
                          feature_count=50, accuracy=0.75,
                          accuracy_delta=-0.05, proposed_by_meta=True)
        record_canary_result(state, r1)
        rollback = record_canary_result(state, r2)
        assert rollback is True

    def test_activation_gate_prevents_premature_proposals(self):
        from waggledance.core.specialist_models.meta_optimizer import (
            MetaOptimizerState, CanaryRecord, propose_hyperparameters,
        )
        state = MetaOptimizerState(model_id="test", canary_history=[
            CanaryRecord("test", cycle=1, learning_rate=0.01, feature_count=50, accuracy=0.8),
        ])
        assert propose_hyperparameters(state, min_cycles=3) is None


class TestDreamModeRollback:
    """Dream mode can be disabled via config."""

    def test_zero_simulations_disables(self):
        from waggledance.core.domain.autonomy import (
            CaseTrajectory, Goal, QualityGrade,
        )
        from waggledance.core.learning.dream_mode import run_dream_session
        trajs = [CaseTrajectory(
            goal=Goal(description="test"),
            quality_grade=QualityGrade.QUARANTINE,
        )]
        session = run_dream_session(trajs, ["cap_1"], max_simulations=0)
        assert session.simulations_run == 0
        assert len(session.simulated_trajectories) == 0

    def test_simulated_never_contaminates_production(self):
        from waggledance.core.domain.autonomy import (
            CaseTrajectory, Goal, QualityGrade,
        )
        from waggledance.core.learning.dream_mode import run_dream_session
        trajs = [CaseTrajectory(
            goal=Goal(description="test"),
            quality_grade=QualityGrade.QUARANTINE,
        )]
        session = run_dream_session(trajs, ["cap_1", "cap_2"], max_simulations=5)
        for t in session.simulated_trajectories:
            assert t.trajectory_origin == "simulated"
            assert t.synthetic is True
            assert t.quality_grade == QualityGrade.BRONZE


class TestConsolidationRollback:
    """Consolidation eviction can be disabled (summarize-only fallback)."""

    def test_evict_disabled_summarizes_only(self):
        import time
        from waggledance.core.learning.consolidator import (
            evaluate_episodes, EpisodeRecord,
        )
        now = time.time()
        episodes = [EpisodeRecord(
            episode_id="ep1",
            created_at=now - 200 * 86400,
            access_count=0,
            quality_grade="bronze",
            significance_score=0.1,
            consolidated=False,
            metadata={"goal_description": "test", "outcome": "ok"},
        )]
        result = evaluate_episodes(episodes, evict_enabled=False)
        assert result.summarized >= 1
        assert result.evicted == 0

    def test_significance_protects_from_consolidation(self):
        import time
        from waggledance.core.learning.consolidator import (
            evaluate_episodes, EpisodeRecord,
        )
        now = time.time()
        episodes = [EpisodeRecord(
            episode_id="ep_high_sig",
            created_at=now - 200 * 86400,
            access_count=0,
            quality_grade="bronze",
            significance_score=0.9,
            consolidated=False,
            metadata={"goal_description": "important", "outcome": "ok"},
        )]
        result = evaluate_episodes(episodes, significance_threshold=0.7)
        assert result.protected == 1
        assert result.summarized == 0


class TestAttentionBudgetRollback:
    """Attention budget reverts to default allocation."""

    def test_default_allocation_is_safe(self):
        from waggledance.core.autonomy.attention_budget import AttentionBudget
        budget = AttentionBudget()
        alloc = budget.current
        assert alloc.critical == 40
        assert alloc.normal == 35
        assert alloc.background == 15
        assert alloc.reflection == 10
        assert alloc.total == 100

    def test_reallocate_then_reset(self):
        from waggledance.core.autonomy.attention_budget import AttentionBudget
        budget = AttentionBudget()
        budget.reallocate(load_factor=0.96)
        assert budget.current.background == 0  # emergency

        # "Rollback": create fresh budget with defaults
        budget2 = AttentionBudget()
        assert budget2.current.background == 15  # back to base


class TestCuriosityGoalRollback:
    """Curiosity goals can be disabled by raising threshold."""

    def test_high_threshold_no_goals(self):
        from waggledance.core.world.epistemic_uncertainty import (
            generate_curiosity_goals, UncertaintyReport,
        )
        report = UncertaintyReport(
            score=0.5,
            stale_entity_ids=["sensor_1"],
            missing_baseline_keys=["sensor_2"],
        )
        # Threshold higher than report score → no goals generated
        goals = generate_curiosity_goals(report, threshold=1.0, profile="HOME")
        assert len(goals) == 0

    def test_below_threshold_no_goals(self):
        from waggledance.core.world.epistemic_uncertainty import (
            generate_curiosity_goals, UncertaintyReport,
        )
        report = UncertaintyReport(score=0.3)
        goals = generate_curiosity_goals(report, threshold=0.4, profile="HOME")
        assert len(goals) == 0

    def test_zero_budget_no_goals(self):
        from waggledance.core.world.epistemic_uncertainty import (
            generate_curiosity_goals, UncertaintyReport,
        )
        report = UncertaintyReport(
            score=0.8,
            stale_entity_ids=["s1"],
        )
        # Already at max active → no new goals
        goals = generate_curiosity_goals(
            report, threshold=0.4, profile="GADGET",
            existing_curiosity_count=3,  # GADGET max is 3
        )
        assert len(goals) == 0


class TestV32ModuleImports:
    """All v3.2 modules are importable."""

    @pytest.mark.parametrize("module", [
        "waggledance.core.world.epistemic_uncertainty",
        "waggledance.core.goals.motives",
        "waggledance.core.learning.consolidator",
        "waggledance.core.learning.dream_mode",
        "waggledance.core.specialist_models.meta_optimizer",
        "waggledance.core.autonomy.attention_budget",
        "waggledance.core.projections",
        "waggledance.core.projections.narrative_projector",
        "waggledance.core.projections.introspection_view",
        "waggledance.core.projections.autobiographical_index",
        "waggledance.core.projections.projection_validator",
    ])
    def test_importable(self, module):
        import importlib
        mod = importlib.import_module(module)
        assert mod is not None
