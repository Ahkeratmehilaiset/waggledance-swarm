"""
Phase 1 Tests: AliasRegistry — canonical ID resolution.

Tests cover:
- YAML loading
- Legacy → canonical resolution
- Canonical → legacy resolution
- Alias (Finnish) → canonical resolution
- Profile filtering
- Unknown agent handling
- Case insensitivity
- Registry completeness (75 agents)
- Migration script dry-run
"""

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest
import yaml

# Ensure project root is importable
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.capabilities.aliasing import AgentAlias, AliasRegistry


# ── Fixtures ─────────────────────────────────────────────────

YAML_PATH = Path(__file__).resolve().parents[2] / "configs" / "alias_registry.yaml"


@pytest.fixture
def registry():
    """Load the real alias_registry.yaml."""
    return AliasRegistry.from_yaml(YAML_PATH)


@pytest.fixture
def mini_registry(tmp_path):
    """A small registry for focused tests."""
    data = {
        "beekeeper": {
            "canonical": "domain.apiary.beekeeper",
            "aliases": ["beekeeper", "tarhaaja", "bee_keeper"],
            "profiles": ["cottage", "home"],
        },
        "meteorologist": {
            "canonical": "shared.weather.meteorology",
            "aliases": ["meteorologist", "meteorologi"],
            "profiles": ["gadget", "cottage", "home", "factory"],
        },
        "production_line": {
            "canonical": "domain.factory.line",
            "aliases": ["production_line", "tuotantolinja"],
            "profiles": ["factory"],
        },
    }
    yaml_file = tmp_path / "test_registry.yaml"
    yaml_file.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
    return AliasRegistry.from_yaml(yaml_file)


# ── YAML Loading ─────────────────────────────────────────────

class TestYAMLLoading:
    def test_loads_from_real_yaml(self, registry):
        assert len(registry) == 75

    def test_file_not_found_raises(self):
        with pytest.raises(FileNotFoundError):
            AliasRegistry.from_yaml("/nonexistent/path.yaml")

    def test_empty_yaml(self, tmp_path):
        f = tmp_path / "empty.yaml"
        f.write_text("", encoding="utf-8")
        reg = AliasRegistry.from_yaml(f)
        assert len(reg) == 0

    def test_all_entries_have_canonical(self, registry):
        for agent in registry.all_agents():
            assert agent.canonical, f"{agent.legacy_id} missing canonical"
            assert "." in agent.canonical, f"{agent.canonical} should be dotted"


# ── Legacy → Canonical Resolution ────────────────────────────

class TestLegacyToCanonical:
    def test_resolve_beekeeper(self, registry):
        assert registry.resolve("beekeeper") == "domain.apiary.beekeeper"

    def test_resolve_meteorologist(self, registry):
        assert registry.resolve("meteorologist") == "shared.weather.meteorology"

    def test_resolve_production_line(self, registry):
        assert registry.resolve("production_line") == "domain.factory.line"

    def test_resolve_core_dispatcher(self, registry):
        assert registry.resolve("core_dispatcher") == "system.dispatcher"

    def test_resolve_smart_home(self, registry):
        assert registry.resolve("smart_home") == "domain.home.automation"

    def test_case_insensitive(self, registry):
        assert registry.resolve("Beekeeper") == registry.resolve("beekeeper")
        assert registry.resolve("METEOROLOGIST") == registry.resolve("meteorologist")

    def test_resolve_strict_raises(self, registry):
        with pytest.raises(KeyError, match="Unknown agent"):
            registry.resolve_strict("nonexistent_agent_xyz")


# ── Canonical → Legacy Resolution ────────────────────────────

class TestCanonicalToLegacy:
    def test_primary_legacy(self, registry):
        assert registry.primary_legacy("domain.apiary.beekeeper") == "beekeeper"

    def test_primary_legacy_unknown(self, registry):
        assert registry.primary_legacy("nonexistent.canonical") is None

    def test_all_legacy_ids(self, registry):
        ids = registry.all_legacy_ids("domain.apiary.beekeeper")
        assert "beekeeper" in ids
        assert "tarhaaja" in ids

    def test_all_legacy_ids_unknown(self, registry):
        assert registry.all_legacy_ids("nonexistent") == []


# ── Alias (Finnish) Resolution ───────────────────────────────

class TestAliasResolution:
    def test_finnish_alias_tarhaaja(self, registry):
        assert registry.resolve("tarhaaja") == "domain.apiary.beekeeper"

    def test_finnish_alias_meteorologi(self, registry):
        assert registry.resolve("meteorologi") == "shared.weather.meteorology"

    def test_finnish_alias_tuotantolinja(self, registry):
        assert registry.resolve("tuotantolinja") == "domain.factory.line"

    def test_finnish_alias_parveiluvahti(self, registry):
        assert registry.resolve("parveiluvahti") == "domain.apiary.swarm"

    def test_canonical_resolves_to_itself(self, registry):
        assert registry.resolve("domain.apiary.beekeeper") == "domain.apiary.beekeeper"
        assert registry.resolve("system.dispatcher") == "system.dispatcher"


# ── Profile Filtering ────────────────────────────────────────

class TestProfileFiltering:
    def test_cottage_agents(self, registry):
        cottage = registry.by_profile("cottage")
        assert len(cottage) > 20  # many agents are cottage
        ids = [a.legacy_id for a in cottage]
        assert "beekeeper" in ids
        assert "astronomer" in ids

    def test_factory_agents(self, registry):
        factory = registry.by_profile("factory")
        assert len(factory) >= 13  # factory-only + shared
        ids = [a.legacy_id for a in factory]
        assert "production_line" in ids
        assert "quality_inspector" in ids

    def test_gadget_agents(self, registry):
        gadget = registry.by_profile("gadget")
        ids = [a.legacy_id for a in gadget]
        assert "beekeeper" in ids
        assert "core_dispatcher" in ids
        assert "meteorologist" in ids

    def test_canonicals_for_profile(self, registry):
        home_canonicals = registry.canonicals_for_profile("home")
        assert "domain.home.automation" in home_canonicals

    def test_unknown_profile(self, registry):
        assert registry.by_profile("nonexistent") == []

    def test_case_insensitive_profile(self, registry):
        assert len(registry.by_profile("COTTAGE")) == len(registry.by_profile("cottage"))


# ── Unknown Agent Handling ───────────────────────────────────

class TestUnknownAgent:
    def test_resolve_returns_none(self, registry):
        assert registry.resolve("totally_unknown_agent") is None

    def test_contains_known(self, registry):
        assert "beekeeper" in registry

    def test_not_contains_unknown(self, registry):
        assert "unknown_xyz" not in registry

    def test_get_unknown_returns_none(self, registry):
        assert registry.get("nonexistent.canonical") is None


# ── Bulk Operations ──────────────────────────────────────────

class TestBulkOperations:
    def test_build_lookup_map(self, registry):
        lookup = registry.build_legacy_to_canonical_map()
        # Should have more entries than agents (aliases + canonicals)
        assert len(lookup) > 75
        assert lookup["beekeeper"] == "domain.apiary.beekeeper"
        assert lookup["tarhaaja"] == "domain.apiary.beekeeper"

    def test_all_canonical_ids(self, registry):
        canonicals = registry.all_canonical_ids()
        assert len(canonicals) == 75
        assert "domain.apiary.beekeeper" in canonicals
        assert "system.dispatcher" in canonicals


# ── AgentAlias Dataclass ─────────────────────────────────────

class TestAgentAlias:
    def test_from_dict(self):
        alias = AgentAlias.from_dict("test", {
            "canonical": "domain.test.agent",
            "aliases": ["test", "testi"],
            "profiles": ["home"],
        })
        assert alias.legacy_id == "test"
        assert alias.canonical == "domain.test.agent"
        assert "test" in alias.aliases
        assert "testi" in alias.aliases
        assert alias.profiles == ("home",)

    def test_immutable(self):
        alias = AgentAlias.from_dict("test", {
            "canonical": "domain.test.agent",
            "aliases": ["test"],
            "profiles": ["home"],
        })
        with pytest.raises(AttributeError):
            alias.canonical = "changed"

    def test_legacy_id_always_in_aliases(self):
        alias = AgentAlias.from_dict("myagent", {
            "canonical": "test.agent",
            "aliases": ["other_name"],
            "profiles": [],
        })
        assert "myagent" in alias.aliases


# ── Mini Registry Tests ──────────────────────────────────────

class TestMiniRegistry:
    def test_len(self, mini_registry):
        assert len(mini_registry) == 3

    def test_resolve(self, mini_registry):
        assert mini_registry.resolve("beekeeper") == "domain.apiary.beekeeper"
        assert mini_registry.resolve("tarhaaja") == "domain.apiary.beekeeper"

    def test_profile_filter(self, mini_registry):
        factory = mini_registry.by_profile("factory")
        assert len(factory) == 2  # meteorologist + production_line
        cottage = mini_registry.by_profile("cottage")
        assert len(cottage) == 2  # beekeeper + meteorologist


# ── Schema Migration Tests ───────────────────────────────────

class TestSchemaMigration:
    """Test that canonical_id columns are added correctly."""

    def test_audit_log_adds_canonical_column(self, tmp_path):
        """AuditLog should auto-add canonical_id column."""
        db_path = str(tmp_path / "test_audit.db")
        from core.audit_log import AuditLog
        al = AuditLog(db_path=db_path)
        cols = {r[1] for r in al._conn.execute("PRAGMA table_info(audit)").fetchall()}
        assert "canonical_id" in cols
        al.close()

    def test_audit_log_record_with_canonical(self, tmp_path):
        """AuditLog.record() accepts canonical_id."""
        db_path = str(tmp_path / "test_audit2.db")
        from core.audit_log import AuditLog
        al = AuditLog(db_path=db_path)
        row_id = al.record("test", "doc1", agent_id="beekeeper",
                           canonical_id="domain.apiary.beekeeper")
        assert row_id > 0
        row = al._conn.execute("SELECT canonical_id FROM audit WHERE id=?", (row_id,)).fetchone()
        assert row[0] == "domain.apiary.beekeeper"
        al.close()

    def test_trust_engine_adds_canonical_column(self, tmp_path):
        """TrustEngine should auto-add canonical_id to trust_signals."""
        db_path = str(tmp_path / "test_trust.db")
        from core.audit_log import AuditLog
        from core.trust_engine import TrustEngine
        al = AuditLog(db_path=db_path)
        te = TrustEngine(al)
        cols = {r[1] for r in te._conn.execute("PRAGMA table_info(trust_signals)").fetchall()}
        assert "canonical_id" in cols
        al.close()


# ── Backfill Script Tests ────────────────────────────────────

class TestBackfillScript:
    def test_backfill_dry_run(self, tmp_path):
        """Backfill script should resolve known agents in dry-run mode."""
        # Create a test audit_log.db with some rows
        db_path = tmp_path / "data" / "audit_log.db"
        db_path.parent.mkdir(parents=True)
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE audit (
                id INTEGER PRIMARY KEY,
                agent_id TEXT NOT NULL DEFAULT '',
                canonical_id TEXT NOT NULL DEFAULT '',
                timestamp REAL NOT NULL
            )
        """)
        conn.execute("INSERT INTO audit (agent_id, canonical_id, timestamp) VALUES ('beekeeper', '', 1.0)")
        conn.execute("INSERT INTO audit (agent_id, canonical_id, timestamp) VALUES ('meteorologist', '', 2.0)")
        conn.commit()
        conn.close()

        # Run backfill in the tmp dir context
        orig_dir = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            # Copy alias_registry.yaml
            configs_dir = tmp_path / "configs"
            configs_dir.mkdir()
            import shutil
            shutil.copy(str(YAML_PATH), str(configs_dir / "alias_registry.yaml"))

            from tools.migrations.backfill_canonical_ids import run
            stats = run(dry_run=True)
            # Dry run should report rows but not update
            conn = sqlite3.connect(str(db_path))
            row = conn.execute("SELECT canonical_id FROM audit WHERE agent_id='beekeeper'").fetchone()
            assert row[0] == ""  # not updated in dry run
            conn.close()
        finally:
            os.chdir(orig_dir)


# ── Registry Repr ────────────────────────────────────────────

class TestRegistryRepr:
    def test_repr(self, registry):
        assert "75 agents" in repr(registry)

    def test_repr_mini(self, mini_registry):
        assert "3 agents" in repr(mini_registry)
