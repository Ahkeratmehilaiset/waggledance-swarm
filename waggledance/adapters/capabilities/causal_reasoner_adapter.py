"""Adapter wrapping CausalEngine as a capability."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from waggledance.core.reasoning.causal_engine import CausalEngine

logger = logging.getLogger(__name__)


class CausalReasonerAdapter:
    """Capability adapter for the CausalEngine reasoning engine."""

    CAPABILITY_ID = "solve.causal"

    def __init__(self, cognitive_graph=None):
        self._engine = CausalEngine(cognitive_graph=cognitive_graph)
        self._call_count = 0
        self._success_count = 0

    @property
    def available(self) -> bool:
        return True

    def execute(self, entity: str = "", query_type: str = "root_causes",
                inputs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute causal reasoning.

        Args:
            entity: entity to analyze
            query_type: root_causes, impact, what_if, or chain
            inputs: additional parameters (target for chain, baselines for what_if)
        """
        t0 = time.monotonic()
        self._call_count += 1
        inputs = inputs or {}

        if query_type == "chain":
            target = inputs.get("target", "")
            chain = self._engine.find_causal_chain(entity, target)
            result = chain.to_dict() if chain else {"error": "No chain found"}
        elif query_type == "impact":
            magnitude = inputs.get("magnitude", 1.0)
            impact = self._engine.estimate_impact(entity, magnitude)
            result = impact.to_dict()
        elif query_type == "what_if":
            new_value = inputs.get("new_value", 0.0)
            baselines = inputs.get("baselines", {})
            projected = self._engine.what_if(entity, new_value, baselines)
            result = {"entity": entity, "projected_changes": projected}
        else:
            causes = self._engine.find_root_causes(entity)
            result = {
                "entity": entity,
                "root_causes": [{"cause": c, "depth": d} for c, d in causes],
            }

        elapsed = (time.monotonic() - t0) * 1000
        self._success_count += 1

        return {
            "success": True,
            "result": result,
            "query_type": query_type,
            "capability_id": self.CAPABILITY_ID,
            "quality_path": "gold",
            "latency_ms": round(elapsed, 2),
        }

    def stats(self) -> Dict[str, Any]:
        return {
            "capability_id": self.CAPABILITY_ID,
            "available": self.available,
            "calls": self._call_count,
            "successes": self._success_count,
            "success_rate": (self._success_count / self._call_count
                             if self._call_count else 0.0),
        }
