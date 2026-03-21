"""
World Model — unified situation picture built on CognitiveGraph + BaselineStore.

The world model is the single source of truth for "what is happening now":
  - Entities (hives, devices, areas, processes) → CognitiveGraph nodes
  - Relations (causal, derived, semantic) → CognitiveGraph edges
  - Baselines (expected normal values) → BaselineStore (SQLite)
  - Residuals (current - baseline) → computed on demand
  - Snapshots → point-in-time serializations for CaseTrajectory
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from waggledance.core.domain.autonomy import SourceType, WorldSnapshot
from waggledance.core.world.baseline_store import BaselineStore
from waggledance.core.world.entity_registry import Entity, EntityRegistry

log = logging.getLogger("waggledance.world.model")


_UNSET = object()  # sentinel for "auto-detect graph"


class WorldModel:
    """
    Combines CognitiveGraph (relational layer) + BaselineStore (metric layer)
    + EntityRegistry (typed entity catalogue) into a unified world model.
    """

    def __init__(
        self,
        cognitive_graph=_UNSET,
        baseline_store: Optional[BaselineStore] = None,
        entity_registry: Optional[EntityRegistry] = None,
        profile: str = "DEFAULT",
    ):
        # Lazy import to avoid circular dependency with legacy core/
        if cognitive_graph is _UNSET:
            try:
                from core.cognitive_graph import CognitiveGraph
                cognitive_graph = CognitiveGraph()
            except ImportError:
                cognitive_graph = None
                log.error(
                    "CognitiveGraph not available — learning loop INACTIVE. "
                    "Install core.cognitive_graph or pass an instance explicitly."
                )

        self._graph = cognitive_graph
        self._baselines = baseline_store or BaselineStore()
        self._entities = entity_registry or EntityRegistry()
        self._profile = profile
        self._last_snapshot_time: float = 0.0
        log.info("WorldModel initialised (profile=%s, graph=%s)",
                 profile, "yes" if cognitive_graph else "no")

    # ── Properties ────────────────────────────────────────────

    @property
    def profile(self) -> str:
        return self._profile

    @property
    def graph(self):
        return self._graph

    @property
    def baselines(self) -> BaselineStore:
        return self._baselines

    @property
    def entities(self) -> EntityRegistry:
        return self._entities

    # ── Entity management (delegates to EntityRegistry + Graph) ──

    def register_entity(
        self,
        entity_id: str,
        entity_type: str,
        attributes: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Entity:
        """Register an entity in both the registry and the graph."""
        entity = self._entities.register(entity_id, entity_type, attributes, **kwargs)
        if self._graph is not None:
            self._graph.add_node(
                entity_id,
                entity_type=entity_type,
                **(attributes or {}),
            )
        return entity

    def get_entity(self, entity_id: str) -> Optional[Entity]:
        return self._entities.get(entity_id)

    def list_entities(self, entity_type: Optional[str] = None) -> List[Entity]:
        return self._entities.list(entity_type)

    # ── Relations (delegates to CognitiveGraph) ───────────────

    def add_relation(self, source: str, target: str, link_type: str = "semantic", **attrs):
        if self._graph is None:
            log.warning("No graph layer — relation not added")
            return
        self._graph.add_edge(source, target, link_type=link_type, **attrs)

    def get_relations(self, entity_id: str) -> List[dict]:
        if self._graph is None:
            return []
        return self._graph.get_edges(entity_id)

    def find_dependents(self, entity_id: str, max_depth: int = 5):
        if self._graph is None:
            return []
        return self._graph.find_dependents(entity_id, max_depth)

    def find_ancestors(self, entity_id: str, max_depth: int = 5):
        if self._graph is None:
            return []
        return self._graph.find_ancestors(entity_id, max_depth)

    # ── Baselines & residuals ─────────────────────────────────

    def update_baseline(
        self,
        entity_id: str,
        metric_name: str,
        value: float,
        source_type: str = "observed",
        confidence: float = 0.5,
    ):
        """Update a baseline value (EMA-smoothed)."""
        return self._baselines.upsert(entity_id, metric_name, value, source_type, confidence)

    def get_baseline(self, entity_id: str, metric_name: str) -> Optional[float]:
        bl = self._baselines.get(entity_id, metric_name)
        return bl.baseline_value if bl else None

    def compute_residual(self, entity_id: str, metric_name: str, current: float) -> Optional[float]:
        return self._baselines.compute_residual(entity_id, metric_name, current)

    def compute_all_residuals(self, observations: Dict[str, float]) -> Dict[str, float]:
        """
        Given {entity.metric: current_value}, compute all residuals.
        Returns {entity.metric: residual}.
        """
        residuals = {}
        for key, current in observations.items():
            parts = key.split(".", 1)
            if len(parts) != 2:
                continue
            entity_id, metric_name = parts
            r = self._baselines.compute_residual(entity_id, metric_name, current)
            if r is not None:
                residuals[key] = r
        return residuals

    # ── Snapshots ─────────────────────────────────────────────

    def take_snapshot(
        self,
        observations: Optional[Dict[str, float]] = None,
        source_type: str = "observed",
    ) -> WorldSnapshot:
        """Create a point-in-time WorldSnapshot from current state."""
        now = time.time()
        self._last_snapshot_time = now

        # Entities as {id: {type, ...attrs}}
        entity_dict: Dict[str, Any] = {}
        for e in self._entities.list():
            entity_dict[e.entity_id] = {
                "type": e.entity_type,
                **(e.attributes or {}),
            }

        # Baselines
        baselines = self._baselines.get_baselines_dict()

        # Residuals
        residuals: Dict[str, float] = {}
        if observations:
            residuals = self.compute_all_residuals(observations)

        return WorldSnapshot(
            entities=entity_dict,
            baselines=baselines,
            residuals=residuals,
            profile=self._profile,
            source_type=source_type,
        )

    # ── Self-entity (v3.2) ─────────────────────────────────────

    def ensure_self_entity(self, **overrides) -> Optional[dict]:
        """Ensure self-entity exists in the CognitiveGraph."""
        if self._graph is None:
            return None
        return self._graph.ensure_self_entity(**overrides)

    def get_self_entity(self) -> Optional[dict]:
        if self._graph is None:
            return None
        return self._graph.get_self_entity()

    def update_self_entity(self, **attrs) -> Optional[dict]:
        if self._graph is None:
            return None
        return self._graph.update_self_entity(**attrs)

    # ── Epistemic uncertainty (v3.2) ───────────────────────────

    def compute_epistemic_uncertainty(
        self,
        open_observe_goals: int = 0,
        stale_ttl_seconds: float = 3600,
    ):
        """Compute uncertainty and update self-entity."""
        from waggledance.core.world.epistemic_uncertainty import compute_uncertainty

        entities = self._entities.list()
        baseline_keys = set(self._baselines.get_baselines_dict().keys())
        report = compute_uncertainty(
            entities=entities,
            baseline_keys=baseline_keys,
            open_observe_goals=open_observe_goals,
            stale_ttl_seconds=stale_ttl_seconds,
        )
        # Update self-entity with uncertainty score
        if self._graph is not None:
            self._graph.update_self_entity(
                epistemic_uncertainty_score=report.score,
                uncertainty_areas=report.stale_entity_ids[:20],
            )
        return report

    # ── Graph stats ───────────────────────────────────────────

    def stats(self) -> dict:
        graph_stats = self._graph.stats() if self._graph else {"nodes": 0, "edges": 0}
        return {
            "profile": self._profile,
            "entities": self._entities.count(),
            "baselines": self._baselines.count(),
            "graph": graph_stats,
            "last_snapshot": self._last_snapshot_time,
        }

    # ── Persistence ───────────────────────────────────────────

    def save(self):
        """Persist graph layer (BaselineStore auto-commits via SQLite)."""
        if self._graph is not None:
            self._graph.save()

    def close(self):
        """Release resources."""
        self._baselines.close()
        if self._graph is not None:
            try:
                self._graph.save()
            except Exception as e:
                log.warning("Failed to save graph on close: %s", e)
