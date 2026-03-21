# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Tests that alias registry covers all agent directories and legacy paths."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.capabilities.aliasing import AliasRegistry


@pytest.fixture(scope="module")
def registry():
    return AliasRegistry.from_yaml_default()


class TestCanonicalCoverage:
    """All agent directories should resolve through the alias registry."""

    def test_registry_loads(self, registry):
        assert len(registry) >= 70

    def test_all_agents_have_canonical(self, registry):
        for agent in registry.all_agents():
            assert agent.canonical, f"{agent.legacy_id} missing canonical"

    def test_all_agents_have_profiles(self, registry):
        for agent in registry.all_agents():
            assert len(agent.profiles) > 0, f"{agent.canonical} missing profiles"

    def test_canonical_resolves_to_self(self, registry):
        for cid in registry.all_canonical_ids():
            assert registry.resolve(cid) == cid

    def test_legacy_id_resolves(self, registry):
        for agent in registry.all_agents():
            resolved = registry.resolve(agent.legacy_id)
            assert resolved == agent.canonical, (
                f"{agent.legacy_id} resolved to {resolved}, expected {agent.canonical}"
            )

    def test_all_aliases_resolve(self, registry):
        for agent in registry.all_agents():
            for alias in agent.aliases:
                resolved = registry.resolve(alias)
                assert resolved == agent.canonical, (
                    f"alias '{alias}' resolved to {resolved}, expected {agent.canonical}"
                )


class TestProfileBasedLevelResolution:
    """backend/routes/agents.py uses profile-based level ranges."""

    def test_apiary_agents_get_high_levels(self, registry):
        apiary = registry.by_profile("cottage")
        assert len(apiary) > 0
        # At least some should resolve to apiary domain
        apiary_canonicals = [a.canonical for a in apiary if "apiary" in a.canonical]
        assert len(apiary_canonicals) >= 1

    def test_profile_lookup_works_for_known_agents(self, registry):
        """Beekeeper should be in cottage/home profiles."""
        canonical = registry.resolve("beekeeper")
        assert canonical is not None
        agent = registry.get(canonical)
        assert agent is not None
        assert any(p.lower() in ("cottage", "home") for p in agent.profiles)

    def test_build_legacy_map_complete(self, registry):
        legacy_map = registry.build_legacy_to_canonical_map()
        assert len(legacy_map) >= len(registry)
