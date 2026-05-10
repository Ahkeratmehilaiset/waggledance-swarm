"""Hex Topology Registry — loads and manages hex cell definitions.

Reads configs/hex_cells.yaml, validates topology, maps cells to agents.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from waggledance.core.domain.hex_mesh import HexCellDefinition, HexCoord

log = logging.getLogger(__name__)


class HexTopologyRegistry:
    """Registry that loads hex cell topology and maps cells to agents."""

    def __init__(
        self,
        config_path: str = "configs/hex_cells.yaml",
        agents: list | None = None,
    ):
        self._config_path = config_path
        self._cells: dict[str, HexCellDefinition] = {}
        self._coord_to_cell: dict[HexCoord, str] = {}
        self._cell_agents: dict[str, list] = {}
        # Phase D Priority 2 Candidate 1 (R18 hex scout): topology is
        # immutable post-load, so the per-cell ring-1 neighbor IDs can
        # be cached once instead of recomputing cell.coord.neighbors()
        # + six _coord_to_cell lookups on every get_neighbor_cells call.
        # Cache stores IDs only — enabled-state is still consulted at
        # query time so a cell that flips enabled=False after load
        # still gets filtered out.
        self._neighbor_cell_ids: dict[str, tuple[str, ...]] = {}
        # Phase D Priority 2 Candidate 3 (R18 hex scout): selector
        # strings are pre-lowercased at load time so select_origin_cell
        # doesn't pay O(cells × selectors) `.lower()` calls per query.
        # Stored as plain tuples (not frozensets) because the substring
        # match `sel in query_lower` requires string semantics.
        self._lower_domain_selectors: dict[str, tuple[str, ...]] = {}
        self._lower_tag_selectors: dict[str, tuple[str, ...]] = {}
        self._agents = agents or []

        self._load()

    def _load(self) -> None:
        """Load hex_cells.yaml and build topology."""
        path = Path(self._config_path)
        if not path.exists():
            log.warning("Hex cells config not found: %s", self._config_path)
            return

        try:
            import yaml

            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)

            if not data or not isinstance(data, dict):
                log.warning("Invalid hex cells config")
                return

            cells_list = data.get("cells", [])
            for cell_data in cells_list:
                cell_id = cell_data.get("id")
                if not cell_id:
                    continue

                coord_data = cell_data.get("coord", {})
                coord = HexCoord(q=coord_data.get("q", 0), r=coord_data.get("r", 0))

                cell = HexCellDefinition(
                    id=cell_id,
                    coord=coord,
                    description=cell_data.get("description", ""),
                    domain_selectors=cell_data.get("domain_selectors", []),
                    tag_selectors=cell_data.get("tag_selectors", []),
                    enabled=cell_data.get("enabled", True),
                    neighbor_policy=cell_data.get("neighbor_policy", "default"),
                )

                self._cells[cell_id] = cell
                self._coord_to_cell[coord] = cell_id

            self._build_neighbor_id_cache()
            self._build_selector_index()
            self._map_agents()
            self._validate()
            log.info(
                "Hex topology loaded: %d cells, %d agents mapped",
                len(self._cells),
                sum(len(v) for v in self._cell_agents.values()),
            )

        except Exception as e:
            log.warning("Failed to load hex topology: %s", e)

    def _build_neighbor_id_cache(self) -> None:
        """Precompute ring-1 neighbor cell IDs per cell from the
        immutable axial topology. Iteration order matches
        HexCoord.neighbors() so callers see the same neighbor
        sequence as the prior recompute-on-call implementation."""
        cache: dict[str, tuple[str, ...]] = {}
        for cell_id, cell in self._cells.items():
            nids: list[str] = []
            for nc in cell.coord.neighbors():
                nid = self._coord_to_cell.get(nc)
                if nid and nid != cell_id:
                    nids.append(nid)
            cache[cell_id] = tuple(nids)
        self._neighbor_cell_ids = cache

    def _build_selector_index(self) -> None:
        """Pre-lowercase every domain/tag selector at load time.
        select_origin_cell() does substring matching against the
        query (`sel in query_lower`), so we still need string values,
        not a token-inverted index — but the per-call `.lower()` per
        selector per cell was the dominant cost on a 7-cell config."""
        self._lower_domain_selectors = {
            cell_id: tuple(s.lower() for s in cell.domain_selectors)
            for cell_id, cell in self._cells.items()
        }
        self._lower_tag_selectors = {
            cell_id: tuple(s.lower() for s in cell.tag_selectors)
            for cell_id, cell in self._cells.items()
        }

    def _map_agents(self) -> None:
        """Map agents to cells based on domain/tag selectors."""
        for cell_id, cell in self._cells.items():
            matched = []
            domain_selectors = self._lower_domain_selectors.get(cell_id, ())
            tag_selectors = self._lower_tag_selectors.get(cell_id, ())
            for agent in self._agents:
                if not agent.active:
                    continue
                # Domain selectors are a gate: when present, an agent
                # must match the cell domain before tag/name fallbacks
                # are considered.
                if domain_selectors:
                    agent_domain = getattr(agent, "domain", "").lower()
                    if any(s in agent_domain for s in domain_selectors):
                        matched.append(agent)
                        continue
                    continue
                # Tag match
                if tag_selectors:
                    agent_tags = [t.lower() for t in getattr(agent, "tags", [])]
                    agent_skills = [s.lower() for s in getattr(agent, "skills", [])]
                    all_tags = agent_tags + agent_skills
                    if any(s in all_tags for s in tag_selectors):
                        matched.append(agent)
                        continue
                # Name/ID match as fallback
                agent_id = getattr(agent, "id", "").lower()
                if any(s in agent_id for s in domain_selectors + tag_selectors):
                    matched.append(agent)

            self._cell_agents[cell_id] = matched

    def _validate(self) -> None:
        """Validate topology consistency."""
        coords = list(self._coord_to_cell.keys())
        if not coords:
            return

        # Check for duplicate coords
        if len(coords) != len(set(coords)):
            log.warning("Hex topology has duplicate coordinates")

        # Check connectivity — every cell should have at least one neighbor in topology
        for cell_id, cell in self._cells.items():
            neighbor_coords = cell.coord.neighbors()
            has_neighbor = any(c in self._coord_to_cell for c in neighbor_coords)
            if not has_neighbor and len(self._cells) > 1:
                log.warning("Cell %s at %s has no neighbors in topology", cell_id, cell.coord)

    # ── Public API ──────────────────────────────────────────────

    @property
    def cells(self) -> dict[str, HexCellDefinition]:
        return dict(self._cells)

    @property
    def cell_count(self) -> int:
        return len(self._cells)

    def get_cell(self, cell_id: str) -> HexCellDefinition | None:
        return self._cells.get(cell_id)

    def get_cell_at(self, coord: HexCoord) -> HexCellDefinition | None:
        cell_id = self._coord_to_cell.get(coord)
        return self._cells.get(cell_id) if cell_id else None

    def get_cell_agents(self, cell_id: str) -> list:
        return list(self._cell_agents.get(cell_id, []))

    def get_neighbor_cells(self, cell_id: str) -> list[HexCellDefinition]:
        """Get ring-1 neighbor cells for a cell.

        Reads cached neighbor IDs (built once at load time) and looks
        up the live HexCellDefinition + enabled flag at query time so
        a runtime enabled=False flip still excludes the cell.
        """
        nids = self._neighbor_cell_ids.get(cell_id)
        if not nids:
            return []
        neighbors: list[HexCellDefinition] = []
        for nid in nids:
            ncell = self._cells.get(nid)
            if ncell and ncell.enabled:
                neighbors.append(ncell)
        return neighbors

    def select_origin_cell(self, query: str, intent: str = "") -> str | None:
        """Select the best origin cell for a query based on domain fit.

        Uses pre-lowercased selectors built at load time
        (_build_selector_index) so per-query work is just two
        `.lower()` calls + substring scans — no per-selector
        `.lower()` allocations.
        """
        if not self._cells:
            return None

        best_cell = None
        best_score = -1.0

        query_lower = query.lower()
        intent_lower = intent.lower()

        domain_index = self._lower_domain_selectors
        tag_index = self._lower_tag_selectors
        cell_agents = self._cell_agents

        for cell_id, cell in self._cells.items():
            if not cell.enabled:
                continue

            score = 0.0

            for sel in domain_index.get(cell_id, ()):
                if sel in query_lower or sel in intent_lower:
                    score += 2.0

            for sel in tag_index.get(cell_id, ()):
                if sel in query_lower or sel in intent_lower:
                    score += 1.5

            agent_count = len(cell_agents.get(cell_id, []))
            score += agent_count * 0.01

            if score > best_score:
                best_score = score
                best_cell = cell_id

        return best_cell

    def stats(self) -> dict[str, Any]:
        return {
            "cells_loaded": len(self._cells),
            "total_agents_mapped": sum(len(v) for v in self._cell_agents.values()),
            "cells": {
                cid: {
                    "coord": f"({c.coord.q},{c.coord.r})",
                    "enabled": c.enabled,
                    "agents": len(self._cell_agents.get(cid, [])),
                }
                for cid, c in self._cells.items()
            },
        }
