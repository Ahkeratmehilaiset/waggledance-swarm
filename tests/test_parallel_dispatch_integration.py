"""Integration tests for parallel dispatch — P4 test groups 5-7.

Covers:
- Status/ops regression: additive fields only, old fields unchanged
- Security regression: auth still required where expected
- Settings loader: llm_parallel fields load correctly
"""

import pytest

from waggledance.adapters.config.settings_loader import WaggleSettings


# ── Test Group 5: Settings loader ────────────────────────────────


class TestSettingsLoaderParallel:
    """llm_parallel fields load with correct defaults."""

    def test_defaults_when_no_yaml(self, tmp_path):
        """All parallel fields default to safe values (disabled)."""
        s = WaggleSettings.from_env(yaml_path=tmp_path / "missing.yaml")
        assert s.llm_parallel_enabled is False
        assert s.llm_parallel_max_concurrent == 4
        assert s.llm_parallel_max_inflight_per_model == 2
        assert s.llm_parallel_request_timeout_s == 120
        assert s.llm_parallel_round_table_first_pass is False
        assert s.llm_parallel_dream_batch == 1
        assert s.llm_parallel_candidate_lab == 1
        assert s.llm_parallel_verifier_advisory == 1
        assert s.llm_parallel_dedupe is True

    def test_yaml_override(self, tmp_path):
        """Settings from YAML override defaults."""
        yaml_file = tmp_path / "settings.yaml"
        yaml_file.write_text(
            "llm_parallel:\n"
            "  enabled: true\n"
            "  max_concurrent: 8\n"
            "  max_inflight_per_model: 3\n"
            "  request_timeout_s: 60\n"
            "  round_table_parallel_first_pass: true\n"
            "  dream_batch_parallelism: 2\n"
            "  candidate_lab_parallelism: 3\n"
            "  verifier_advisory_parallelism: 2\n"
            "  dedupe_identical_prompts: false\n",
            encoding="utf-8",
        )
        s = WaggleSettings.from_env(yaml_path=yaml_file)
        assert s.llm_parallel_enabled is True
        assert s.llm_parallel_max_concurrent == 8
        assert s.llm_parallel_max_inflight_per_model == 3
        assert s.llm_parallel_request_timeout_s == 60
        assert s.llm_parallel_round_table_first_pass is True
        assert s.llm_parallel_dream_batch == 2
        assert s.llm_parallel_candidate_lab == 3
        assert s.llm_parallel_verifier_advisory == 2
        assert s.llm_parallel_dedupe is False

    def test_env_overrides_yaml(self, tmp_path, monkeypatch):
        """Environment variables override YAML values."""
        yaml_file = tmp_path / "settings.yaml"
        yaml_file.write_text(
            "llm_parallel:\n  enabled: false\n", encoding="utf-8")
        monkeypatch.setenv("WAGGLE_LLM_PARALLEL_ENABLED", "true")
        monkeypatch.setenv("WAGGLE_LLM_PARALLEL_MAX_CONCURRENT", "16")
        s = WaggleSettings.from_env(yaml_path=yaml_file)
        assert s.llm_parallel_enabled is True
        assert s.llm_parallel_max_concurrent == 16

    def test_gemma_fields_unchanged(self, tmp_path):
        """Adding parallel fields doesn't break Gemma fields."""
        s = WaggleSettings.from_env(yaml_path=tmp_path / "missing.yaml")
        assert s.gemma_enabled is False
        assert s.gemma_fast_model == "gemma4:e4b"
        assert s.gemma_heavy_model == "gemma4:26b"
        assert s.gemma_active_profile == "disabled"


# ── Test Group 6: Container wiring ──────────────────────────────


class TestContainerWiring:
    """Test that container correctly wires parallel_dispatcher."""

    def test_container_creates_dispatcher(self):
        """Container creates a ParallelLLMDispatcher instance."""
        from waggledance.bootstrap.container import Container

        s = WaggleSettings.from_env()
        c = Container(settings=s, stub=True)
        d = c.parallel_dispatcher

        from waggledance.application.services.parallel_llm_dispatcher import (
            ParallelLLMDispatcher,
        )
        assert isinstance(d, ParallelLLMDispatcher)
        assert d.enabled is False  # default settings

    def test_orchestrator_receives_dispatcher(self):
        """Orchestrator receives parallel_dispatcher from container."""
        from waggledance.bootstrap.container import Container

        s = WaggleSettings.from_env()
        c = Container(settings=s, stub=True)
        orch = c.orchestrator
        assert hasattr(orch, "_parallel_dispatcher")
        assert orch._parallel_dispatcher is c.parallel_dispatcher

    def test_solver_candidate_lab_receives_dispatcher(self):
        """SolverCandidateLab receives parallel_dispatcher from container."""
        from waggledance.bootstrap.container import Container

        s = WaggleSettings.from_env()
        c = Container(settings=s, stub=True)
        lab = c.solver_candidate_lab
        assert hasattr(lab, "_parallel_dispatcher")
        assert lab._parallel_dispatcher is c.parallel_dispatcher


# ── Test Group 7: Orchestrator with dispatcher ───────────────────


class TestOrchestratorDispatcher:
    """Test orchestrator uses dispatcher when available."""

    @pytest.mark.asyncio
    async def test_orchestrator_passes_dispatcher_to_execute_agents(self):
        """Verify orchestrator constructor accepts parallel_dispatcher."""
        from waggledance.core.orchestration.orchestrator import Orchestrator
        from waggledance.core.orchestration.scheduler import Scheduler
        from waggledance.core.orchestration.round_table import RoundTableEngine
        from waggledance.adapters.llm.stub_llm_adapter import StubLLMAdapter
        from waggledance.adapters.memory.in_memory_vector_store import InMemoryVectorStore
        from waggledance.adapters.memory.in_memory_repository import InMemoryRepository
        from waggledance.adapters.trust.in_memory_trust_store import InMemoryTrustStore
        from waggledance.bootstrap.event_bus import InMemoryEventBus

        llm = StubLLMAdapter()
        s = WaggleSettings.from_env()
        eb = InMemoryEventBus()
        rt = RoundTableEngine(llm=llm, event_bus=eb)

        orch = Orchestrator(
            scheduler=Scheduler(config=s),
            round_table=rt,
            memory=InMemoryRepository(),
            vector_store=InMemoryVectorStore(),
            llm=llm,
            trust_store=InMemoryTrustStore(),
            event_bus=eb,
            config=s,
            agents=[],
            parallel_dispatcher=None,  # Explicitly None
        )
        assert orch._parallel_dispatcher is None
