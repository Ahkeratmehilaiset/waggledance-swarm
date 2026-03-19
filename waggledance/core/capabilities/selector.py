"""
Capability Selector — routes queries to the correct capability chain.

Selection strategy (solver-first, LLM-last):
  1. Exact solver match (math, symbolic, constraints) → GOLD path
  2. Rule/detection match (seasonal, anomaly) → GOLD path
  3. Retrieval match (HotCache, FAISS, ChromaDB) → SILVER path
  4. MicroModel match (pattern, classifier) → SILVER path
  5. LLM fallback (explain.llm_reasoning) → BRONZE path

The selector uses capability preconditions and available context
to decide which capabilities can actually run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from waggledance.core.capabilities.registry import CapabilityRegistry
from waggledance.core.domain.autonomy import CapabilityCategory, CapabilityContract

log = logging.getLogger("waggledance.capabilities.selector")

# Priority order: solvers first, LLM last
_CATEGORY_PRIORITY = {
    CapabilityCategory.SOLVE: 10,
    CapabilityCategory.DETECT: 20,
    CapabilityCategory.RETRIEVE: 30,
    CapabilityCategory.NORMALIZE: 40,
    CapabilityCategory.VERIFY: 50,
    CapabilityCategory.PREDICT: 55,
    CapabilityCategory.OPTIMIZE: 57,
    CapabilityCategory.PLAN: 58,
    CapabilityCategory.SENSE: 60,
    CapabilityCategory.ESTIMATE: 65,
    CapabilityCategory.LEARN: 70,
    CapabilityCategory.ACT: 80,
    CapabilityCategory.EXPLAIN: 90,  # LLM last
}


@dataclass
class SelectionResult:
    """Result of capability selection."""
    selected: List[CapabilityContract] = field(default_factory=list)
    reason: str = ""
    quality_path: str = "bronze"  # gold / silver / bronze
    fallback_used: bool = False

    def to_dict(self) -> dict:
        return {
            "selected": [c.capability_id for c in self.selected],
            "reason": self.reason,
            "quality_path": self.quality_path,
            "fallback_used": self.fallback_used,
        }


class CapabilitySelector:
    """
    Selects the best capability chain for a given query context.

    Uses solver-first strategy: prefer deterministic solvers over
    statistical models over LLM generation.
    """

    def __init__(self, registry: CapabilityRegistry):
        self._registry = registry
        self._confidence_scores: Dict[str, float] = {}

    def select(
        self,
        intent: str,
        context: Optional[Dict[str, Any]] = None,
        available_conditions: Optional[Dict[str, bool]] = None,
    ) -> SelectionResult:
        """
        Select capabilities based on intent and available context.

        Args:
            intent: classified intent (e.g., "math", "seasonal", "retrieval", "chat")
            context: additional context dict
            available_conditions: which preconditions are met
                e.g. {"numbers_present": True, "model_available": True}

        Returns:
            SelectionResult with ordered capability chain
        """
        conditions = available_conditions or {}
        context = context or {}

        # Try solver-first
        result = self._try_solvers(intent, conditions)
        if result:
            return result

        # Try detection/rules
        result = self._try_detection(intent, conditions)
        if result:
            return result

        # Try optimization
        result = self._try_optimization(intent, conditions)
        if result:
            return result

        # Try retrieval
        result = self._try_retrieval(intent, conditions)
        if result:
            return result

        # Try micromodels
        result = self._try_micromodels(intent, conditions)
        if result:
            return result

        # LLM fallback
        return self._llm_fallback(conditions)

    def set_confidence_scores(self, scores: Dict[str, float]):
        """Set capability confidence scores for tiebreaking."""
        self._confidence_scores = dict(scores)

    def select_for_capability_ids(
        self, capability_ids: List[str]
    ) -> SelectionResult:
        """Select specific capabilities by ID (for plan execution)."""
        selected = []
        for cid in capability_ids:
            cap = self._registry.get(cid)
            if cap:
                selected.append(cap)

        if not selected:
            return SelectionResult(reason="No matching capabilities found")

        quality = self._determine_quality(selected)
        return SelectionResult(
            selected=selected,
            reason=f"Explicit selection: {', '.join(capability_ids)}",
            quality_path=quality,
        )

    # ── Private selection strategies ──────────────────────

    def _try_solvers(self, intent: str, conditions: Dict[str, bool]) -> Optional[SelectionResult]:
        """Try to match a solver capability."""
        solver_intents = {"math", "symbolic", "constraint", "calculate", "solve",
                          "formula", "thermal", "stats", "causal"}
        if intent not in solver_intents:
            return None

        solvers = self._registry.solvers()
        usable = self._filter_by_preconditions(solvers, conditions)

        if not usable:
            return None

        # Pick the most specific solver
        solver_map = {
            "math": "solve.math",
            "calculate": "solve.math",
            "symbolic": "solve.symbolic",
            "formula": "solve.symbolic",
            "constraint": "solve.constraints",
            "solve": "solve.symbolic",  # default solver
            "thermal": "solve.thermal",
            "stats": "solve.stats",
            "causal": "solve.causal",
        }
        preferred_id = solver_map.get(intent)
        preferred = [s for s in usable if s.capability_id == preferred_id]

        selected = preferred if preferred else usable[:1]

        # Add verifier
        verifiers = self._filter_by_preconditions(self._registry.verifiers(), conditions)
        if verifiers:
            selected.extend(verifiers[:1])

        return SelectionResult(
            selected=selected,
            reason=f"Solver match for intent '{intent}'",
            quality_path="gold",
        )

    def _try_detection(self, intent: str, conditions: Dict[str, bool]) -> Optional[SelectionResult]:
        """Try detection/rule capabilities."""
        detect_intents = {"seasonal", "anomaly", "rule", "detect", "check",
                          "routing", "route"}
        if intent not in detect_intents:
            return None

        detectors = self._registry.list_by_category(CapabilityCategory.DETECT)
        usable = self._filter_by_preconditions(detectors, conditions)

        if not usable:
            return None

        return SelectionResult(
            selected=usable,
            reason=f"Detection match for intent '{intent}'",
            quality_path="gold",
        )

    def _try_optimization(self, intent: str, conditions: Dict[str, bool]) -> Optional[SelectionResult]:
        """Try optimization capabilities."""
        optim_intents = {"optimization", "optimize", "schedule", "allocate"}
        if intent not in optim_intents:
            return None

        optimizers = self._registry.list_by_category(CapabilityCategory.OPTIMIZE)
        usable = self._filter_by_preconditions(optimizers, conditions)

        if not usable:
            return None

        return SelectionResult(
            selected=usable,
            reason=f"Optimization match for intent '{intent}'",
            quality_path="gold",
        )

    def _try_retrieval(self, intent: str, conditions: Dict[str, bool]) -> Optional[SelectionResult]:
        """Try retrieval capabilities."""
        retrieval_intents = {"retrieval", "search", "lookup", "find", "recall"}
        if intent not in retrieval_intents:
            return None

        retrievers = self._registry.retrievers()
        usable = self._filter_by_preconditions(retrievers, conditions)

        if not usable:
            return None

        # Sort by priority: hot_cache > vector > semantic
        priority = {"retrieve.hot_cache": 0, "retrieve.vector_search": 1, "retrieve.semantic_search": 2}
        usable.sort(key=lambda c: priority.get(c.capability_id, 99))

        return SelectionResult(
            selected=usable,
            reason=f"Retrieval match for intent '{intent}'",
            quality_path="silver",
        )

    def _try_micromodels(self, intent: str, conditions: Dict[str, bool]) -> Optional[SelectionResult]:
        """Try micromodel capabilities."""
        micro_ids = ["solve.pattern_match", "solve.neural_classifier"]
        micros = [c for c in self._registry.solvers() if c.capability_id in micro_ids]
        usable = self._filter_by_preconditions(micros, conditions)

        if not usable:
            return None

        return SelectionResult(
            selected=usable,
            reason="MicroModel fallback",
            quality_path="silver",
        )

    def _llm_fallback(self, conditions: Dict[str, bool]) -> SelectionResult:
        """LLM as last resort."""
        llm = self._registry.get("explain.llm_reasoning")
        if llm and self._check_preconditions(llm, conditions):
            return SelectionResult(
                selected=[llm],
                reason="LLM fallback (no solver/retrieval match)",
                quality_path="bronze",
                fallback_used=True,
            )
        return SelectionResult(
            reason="No capabilities available",
            fallback_used=True,
        )

    # ── Helpers ───────────────────────────────────────────

    def _filter_by_preconditions(
        self, capabilities: List[CapabilityContract], conditions: Dict[str, bool]
    ) -> List[CapabilityContract]:
        """Return capabilities whose preconditions are all met.

        When confidence scores are set, ties within the same category
        are broken by confidence (higher first).
        """
        usable = [c for c in capabilities if self._check_preconditions(c, conditions)]
        if self._confidence_scores and len(usable) > 1:
            usable.sort(
                key=lambda c: self._confidence_scores.get(c.capability_id, 0.5),
                reverse=True,
            )
        return usable

    @staticmethod
    def _check_preconditions(cap: CapabilityContract, conditions: Dict[str, bool]) -> bool:
        """Check if all preconditions for a capability are met."""
        if not cap.preconditions:
            return True
        return all(conditions.get(p, False) for p in cap.preconditions)

    @staticmethod
    def _determine_quality(capabilities: List[CapabilityContract]) -> str:
        """Determine quality path from selected capabilities."""
        has_solver = any(c.category == CapabilityCategory.SOLVE for c in capabilities)
        has_verifier = any(c.category == CapabilityCategory.VERIFY for c in capabilities)

        if has_solver and has_verifier:
            return "gold"
        if has_solver or has_verifier:
            return "silver"
        return "bronze"
