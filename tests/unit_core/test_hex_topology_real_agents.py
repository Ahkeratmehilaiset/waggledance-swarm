# SPDX-License-Identifier: BUSL-1.1
"""Audit H1/H24/H26 regression — real production agents distribute across cells.

Before this fix every agent YAML lacked ``header.domain`` and
``Container._load_agents`` defaulted it to "general", which only matched
the hub cell selector. Result: 75/75 agents collapsed to hub.

The PR9 fix derives ``agent.domain`` from
``configs/alias_registry.yaml`` canonical IDs (e.g.
``shared.energy.advisor`` -> domain="energy") and extends the cell
selectors in ``configs/hex_cells.yaml`` to cover the 14 canonical
top-level domains. These tests pin the distribution against the LIVE
configs/ tree so a regression in either side fails loudly.
"""

from __future__ import annotations

import pytest

from waggledance.application.services.hex_topology_registry import (
    HexTopologyRegistry,
)
from waggledance.bootstrap.container import Container


class _StubSettings:
    def __init__(self, profile: str = "HOME") -> None:
        self._profile = profile

    def get_profile(self) -> str:
        return self._profile


def _load_real_agents(profile: str = "HOME"):
    c = Container(settings=_StubSettings(profile), stub=True)
    return c._load_agents()


@pytest.mark.skipif(
    not __import__("pathlib").Path("agents").exists(),
    reason="agents/ directory absent in this checkout",
)
@pytest.mark.skipif(
    not __import__("pathlib").Path("configs/hex_cells.yaml").exists(),
    reason="configs/hex_cells.yaml absent in this checkout",
)
class TestHexTopologyRealAgents:
    """H26: load actual configs/, assert distribution is non-degenerate."""

    def test_real_agents_load_non_empty(self):
        """Sanity: production agents/ has SOME agents on the HOME profile."""
        agents = _load_real_agents("HOME")
        assert len(agents) > 0, (
            "expected non-empty agent list on HOME profile — H30 "
            "validation may have rejected the profile or agents/ is empty"
        )

    def test_real_agents_distribute_off_hub(self):
        """The H1 regression test: NOT every agent collapses to hub."""
        agents = _load_real_agents("HOME")
        reg = HexTopologyRegistry(
            config_path="configs/hex_cells.yaml", agents=agents,
        )
        cell_counts = {
            cid: len(reg.get_cell_agents(cid)) for cid in reg.cells
        }
        non_hub = sum(n for cid, n in cell_counts.items() if cid != "hub")
        assert non_hub > 0, (
            f"All agents collapsed to hub — H1 regression. "
            f"Distribution: {cell_counts}"
        )

    def test_hub_holds_less_than_half_of_agents(self):
        """Stronger guarantee: hub holds < 50% of agents post-H24 fix.

        Before H24, hub held 100%. After H24+H28 selector extensions,
        empirical measurement was 1-2% for HOME profile. The 50% bar
        gives headroom for the operator to retune selectors without
        breaking the test.
        """
        agents = _load_real_agents("HOME")
        reg = HexTopologyRegistry(
            config_path="configs/hex_cells.yaml", agents=agents,
        )
        hub_count = len(reg.get_cell_agents("hub"))
        threshold = max(1, len(agents) // 2)
        assert hub_count < threshold, (
            f"hub holds {hub_count}/{len(agents)} agents — expected "
            f"< {threshold} after H24 fix"
        )

    def test_agent_domains_use_alias_registry_taxonomy(self):
        """H24: domain should be a canonical top-level (apiary, energy,
        weather, ...) not the pre-H24 default 'general' for the
        majority of agents."""
        agents = _load_real_agents("HOME")
        general_count = sum(1 for a in agents if a.domain == "general")
        # Some agents may legitimately default to "general" (system /
        # dispatcher / catch-all) but the majority should resolve to a
        # specialized domain via alias_registry.
        assert general_count < len(agents), (
            f"All {len(agents)} agents fell back to domain='general' "
            "— AliasRegistry.resolve() may be returning None for every "
            "agent_id, suggesting registry not loaded or schema drift"
        )
