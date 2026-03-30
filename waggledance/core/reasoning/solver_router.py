"""
Solver Router — solver-first reasoning pipeline.

Extends CapabilitySelector with full query lifecycle:
  1. Intent classification (from SmartRouterV2 keywords or specialist model)
  2. World model context enrichment
  3. Capability selection (solver-first, LLM-last)
  4. Policy check via PolicyEngine
  5. Execution via SafeActionBus
  6. Verification of outcome
  7. Case trajectory recording

SmartRouterV2 is NOT replaced — it feeds intent classification into this router.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from waggledance.core.capabilities.registry import CapabilityRegistry
from waggledance.core.capabilities.selector import CapabilitySelector, SelectionResult
from waggledance.core.domain.autonomy import (
    Action,
    CapabilityContract,
    WorldSnapshot,
)
from waggledance.core.memory.working_memory import WorkingMemory

log = logging.getLogger("waggledance.reasoning.solver_router")


@dataclass
class SolverRouteResult:
    """Result of the solver routing pipeline."""
    intent: str
    selection: SelectionResult
    context_keys: List[str] = field(default_factory=list)
    world_snapshot: Optional[WorldSnapshot] = None
    execution_time_ms: float = 0.0

    @property
    def quality_path(self) -> str:
        return self.selection.quality_path

    @property
    def selected_ids(self) -> List[str]:
        return [c.capability_id for c in self.selection.selected]

    def to_dict(self) -> dict:
        return {
            "intent": self.intent,
            "quality_path": self.quality_path,
            "selected": self.selected_ids,
            "context_keys": self.context_keys,
            "execution_time_ms": self.execution_time_ms,
            "fallback_used": self.selection.fallback_used,
        }


class SolverRouter:
    """
    Solver-first reasoning router.

    Pipeline:
      intent → context enrichment → capability selection → route result

    The actual execution (policy check, action bus) is handled by the caller
    (AutonomyRuntime), not by this router. This router only decides WHAT
    to invoke, not whether it's allowed.
    """

    def __init__(
        self,
        registry: Optional[CapabilityRegistry] = None,
        selector: Optional[CapabilitySelector] = None,
        working_memory: Optional[WorkingMemory] = None,
    ):
        self._registry = registry or CapabilityRegistry()
        self._selector = selector or CapabilitySelector(self._registry)
        self._working_memory = working_memory or WorkingMemory()
        self._route_history: List[SolverRouteResult] = []
        self._capability_confidence: Optional[Dict[str, float]] = None

    # ── Main routing ──────────────────────────────────────

    def route(
        self,
        intent: str,
        query: str = "",
        context: Optional[Dict[str, Any]] = None,
    ) -> SolverRouteResult:
        """
        Route a query through the solver-first pipeline.

        Args:
            intent: classified intent (e.g., "math", "seasonal", "retrieval", "chat")
            query: original user query text
            context: additional context (e.g., profile, entity_id, language)

        Returns:
            SolverRouteResult with selected capabilities and quality path
        """
        t0 = time.time()
        context = context or {}

        # 1. Detect available conditions from context + query text
        conditions = self._detect_conditions(context, query)

        # 2. Enrich with working memory context
        wm_context = self._working_memory.as_context_dict()
        context_keys = list(wm_context.keys())

        # 3. Select capabilities (solver-first)
        selection = self._selector.select(intent, context, conditions)

        elapsed = (time.time() - t0) * 1000

        result = SolverRouteResult(
            intent=intent,
            selection=selection,
            context_keys=context_keys,
            execution_time_ms=round(elapsed, 2),
        )

        self._record(result)
        return result

    def route_direct(
        self,
        capability_ids: List[str],
    ) -> SolverRouteResult:
        """Route to specific capabilities by ID (for plan execution)."""
        t0 = time.time()
        selection = self._selector.select_for_capability_ids(capability_ids)
        elapsed = (time.time() - t0) * 1000

        result = SolverRouteResult(
            intent="direct",
            selection=selection,
            execution_time_ms=round(elapsed, 2),
        )
        self._record(result)
        return result

    # ── Intent helpers ────────────────────────────────────

    @staticmethod
    def classify_intent(query: str) -> str:
        """
        Basic intent classification from query text.
        In production, this is replaced by the SmartRouterV2 or
        specialist domain-language adapter.
        """
        q = query.lower().strip()

        # ── Early arithmetic detection (overrides "what is" retrieval) ──
        # "what is 15% of 300", "paljonko on 15% sadasta"
        if re.search(r'\d+\s*%\s*(?:of|sadasta)?\s*\d+', q):
            return "math"
        # "what is 12 squared", "5 cubed"
        if re.search(r'\d+\s*(?:squared|cubed)', q):
            return "math"

        # Math / calculation keywords + digit-operator-digit
        math_signals = {"laske", "calculate", "compute", "paljonko", "how much",
                        "sum"}
        if any(s in q for s in math_signals):
            return "math"
        # Arithmetic: digit OP digit (excludes bare "-" which appears in "-5C")
        if re.search(r'\d\s*[+*/=]\s*\d', q):
            return "math"
        # Subtraction with whitespace: "256 - 89" but not "-5C" (no left digit)
        if re.search(r'\d\s+-\s+\d', q):
            return "math"

        # Symbolic / formula
        formula_signals = {"formula", "kaava", "model", "malli", "axiom",
                          "solve for", "ratkaise"}
        if any(s in q for s in formula_signals):
            return "symbolic"

        # Constraint / rule check
        rule_signals = {"rule", "sääntö", "constraint", "check", "tarkista",
                       "compliant", "violation"}
        if any(s in q for s in rule_signals):
            return "constraint"

        # Seasonal
        seasonal_signals = {"season", "vuodenaika", "kausi", "spring", "kevät",
                           "summer", "kesä", "autumn", "syksy", "winter", "talvi"}
        if any(s in q for s in seasonal_signals):
            return "seasonal"

        # ── Time-series stats: metric + time window (before thermal) ──
        _TS_METRICS = {"average", "trend", "compare", "cost", "consumption",
                       "energy", "keskiarvo", "keskimääräinen"}
        _TS_WINDOWS = {"last", "this", "week", "month", "days", "yesterday",
                       "today", "viime", "tämä", "viikko", "kuukausi", "päivää"}
        if any(m in q for m in _TS_METRICS) and any(w in q for w in _TS_WINDOWS):
            return "stats"

        # Thermal — but skip if optimization verb present ("optimize heating")
        thermal_signals = {"temperature", "lämpötila", "heat", "frost", "pakkanen",
                           "pakkasvaara", "thermal", "heating", "cooling", "lämmitys",
                           "celsius", "fahrenheit", "degrees", "astetta",
                           "too hot", "too cold", "liian kuuma", "liian kylmä",
                           "warm", "lämmin", "cold", "kylmä"}
        _OPTIM_VERBS = {"optimize", "optimoi", "minimize", "maximize", "allocate",
                        "cheapest", "halvin", "optimization"}
        has_thermal = any(s in q for s in thermal_signals) or re.search(r'\d+\s*°?[cf]\b', q)
        if has_thermal and not any(ov in q for ov in _OPTIM_VERBS):
            return "thermal"

        # Schedule disambiguation: schedule without active verb → retrieval
        _SCHED_WORDS = {"schedule", "aikataulu", "kalenteri", "calendar", "timetable"}
        _SCHED_VERBS = {"optimize", "optimoi", "minimize", "maximize", "allocate",
                        "create", "build", "schedule this", "optimization"}
        if any(sw in q for sw in _SCHED_WORDS):
            if any(ov in q for ov in _SCHED_VERBS):
                return "optimization"
            return "retrieval"

        # Optimization
        optim_signals = {"optimize", "optimoi", "aikatauluta",
                         "minimize", "allocate", "cheapest", "halvin",
                         "optimization"}
        if any(s in q for s in optim_signals):
            return "optimization"

        # Statistics (no time window required)
        stats_signals = {"statistics", "tilasto", "trend", "keskiarvo",
                         "median", "percentile", "correlation", "summary"}
        if any(s in q for s in stats_signals):
            return "stats"

        # Causal
        causal_signals = {"cause", "syy", "why", "miksi", "impact", "vaikutus",
                          "root cause", "because", "koska", "depends"}
        if any(s in q for s in causal_signals):
            return "causal"

        # Anomaly / deviation
        anomaly_signals = {"anomaly", "anomalia", "deviation", "poikkeama",
                          "outlier", "unusual", "epätavallinen"}
        if any(s in q for s in anomaly_signals):
            return "anomaly"

        # Retrieval / search
        retrieval_signals = {"what is", "mikä on", "tell me", "kerro",
                            "explain", "selitä", "search", "hae", "find", "etsi"}
        if any(s in q for s in retrieval_signals):
            return "retrieval"

        # Default: chat (LLM fallback)
        return "chat"

    # ── Dream mode routing hints ─────────────────────────

    def apply_dream_hints(self, dream_session) -> int:
        """Ingest routing hints from a DreamSession into working memory.

        Each successful counterfactual is stored as a routing hint so the
        next time the same intent is encountered, the alternative capability
        is preferred.

        Returns the number of hints applied.
        """
        applied = 0
        for traj in dream_session.simulated_trajectories:
            outcome = traj.verifier_result.get("outcome", "inconclusive")
            if outcome != "success":
                continue
            alt_chain = traj.counterfactual_alternatives or []
            original_id = traj.verifier_result.get("original_trajectory_id", "")
            if alt_chain:
                hint_key = f"dream_hint:{original_id}"
                self._working_memory.put(
                    hint_key,
                    {"alternative_chain": alt_chain, "outcome": outcome},
                    category="dream_hint",
                    salience=0.7,
                )
                applied += 1
        if applied:
            log.info("Applied %d dream routing hints to working memory", applied)
        return applied

    # ── Capability confidence integration ─────────────────

    def set_capability_confidence(self, scores: Dict[str, float]):
        """Feed capability confidence scores for tiebreaking in selection."""
        self._capability_confidence = dict(scores)
        self._selector.set_confidence_scores(scores)

    # ── Working memory integration ────────────────────────

    def set_context(self, key: str, value: Any, category: str = "observation",
                    salience: float = 0.5):
        """Push context into working memory for next route."""
        self._working_memory.put(key, value, category=category, salience=salience)

    def clear_context(self, category: Optional[str] = None):
        self._working_memory.clear(category)

    # ── Stats ─────────────────────────────────────────────

    def recent_routes(self, limit: int = 50) -> List[SolverRouteResult]:
        return self._route_history[-limit:]

    def stats(self) -> dict:
        total = len(self._route_history)
        if total == 0:
            return {"total": 0, "quality_distribution": {}}

        quality_counts: Dict[str, int] = {}
        for r in self._route_history:
            q = r.quality_path
            quality_counts[q] = quality_counts.get(q, 0) + 1

        return {
            "total": total,
            "quality_distribution": quality_counts,
            "avg_time_ms": sum(r.execution_time_ms for r in self._route_history) / total,
        }

    # ── Internal ──────────────────────────────────────────

    # Regex for detecting numbers in query text (digits, decimals, percentages)
    _NUMBER_RE = re.compile(r'\d')

    def _detect_conditions(self, context: Dict[str, Any], query: str = "") -> Dict[str, bool]:
        """Detect which preconditions are available from context and query text."""
        conditions: Dict[str, bool] = {}

        # Always available capabilities
        conditions["text_normalized"] = True

        # Detect numbers from query text (enables solve.math, solve.thermal)
        if query and self._NUMBER_RE.search(query):
            conditions["numbers_present"] = True

        # Check for specific signals from caller context
        if context.get("numbers_present") or context.get("has_numbers"):
            conditions["numbers_present"] = True
        if context.get("model_available") or context.get("axiom_model"):
            conditions["model_available"] = True
            conditions["inputs_present"] = True
        if context.get("rules_loaded") or context.get("has_rules"):
            conditions["rules_loaded"] = True
            conditions["context_available"] = True
        if context.get("calendar_available", True):
            conditions["calendar_available"] = True
        if context.get("ollama_available", True):
            conditions["ollama_available"] = True
        if context.get("embeddings_available", True):
            conditions["embeddings_available"] = True
        if context.get("index_loaded", True):
            conditions["index_loaded"] = True
        if context.get("response_available"):
            conditions["response_available"] = True
        if context.get("baselines_available"):
            conditions["baselines_available"] = True

        return conditions

    def _record(self, result: SolverRouteResult):
        self._route_history.append(result)
        if len(self._route_history) > 1000:
            self._route_history = self._route_history[-500:]
