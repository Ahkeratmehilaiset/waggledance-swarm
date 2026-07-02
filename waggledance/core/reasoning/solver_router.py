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

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from waggledance.core.capabilities.registry import CapabilityRegistry
from waggledance.core.capabilities.selector import CapabilitySelector, SelectionResult
from waggledance.core.domain.autonomy import (
    Action,
    CapabilityCategory,
    CapabilityContract,
    WorldSnapshot,
)
from waggledance.core.memory.working_memory import WorkingMemory

log = logging.getLogger("waggledance.reasoning.solver_router")


# Phase 14 — autonomy consult adapter.
# A callable injected into SolverRouter that bridges the reasoning
# router to the low-risk autonomy lane. The callable receives a
# query envelope (family_kind, inputs, features, cell_coord, intent_seed)
# and returns an AutonomyConsultOutcome — or None when the consult
# was not attempted.
#
# This is a *backwards-compatible* extension: SolverRouter still works
# without an autonomy_consult, exactly as in Phase 13. Existing
# tests do not need to change.
AutonomyConsultCallable = Callable[
    [Dict[str, Any]], "Optional[AutonomyConsultOutcome]"
]


@dataclass(frozen=True)
class AutonomyConsultOutcome:
    """Outcome reported by the autonomy consult adapter."""

    served: bool
    source: str  # 'auto_promoted_solver' | 'gap_emitted' | 'gap_throttled'
                  # | 'family_not_low_risk' | 'consult_skipped'
    output: Any = None
    solver_id: Optional[int] = None
    solver_name: Optional[str] = None
    artifact_id: Optional[str] = None
    signal_id: Optional[int] = None
    miss_reason: Optional[str] = None


@dataclass
class SolverRouteResult:
    """Result of the solver routing pipeline."""
    intent: str
    selection: SelectionResult
    context_keys: List[str] = field(default_factory=list)
    world_snapshot: Optional[WorldSnapshot] = None
    execution_time_ms: float = 0.0
    # Phase 14 — autonomy consult outcome (None when not attempted).
    autonomy_consult: Optional[AutonomyConsultOutcome] = None
    # Image #1 solver-first proof boundary: privacy-safe trace of solver
    # capabilities selected for the caller to execute. Query text and free-form
    # descriptions are intentionally excluded.
    solver_call_trace: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def quality_path(self) -> str:
        return self.selection.quality_path

    @property
    def selected_ids(self) -> List[str]:
        return [c.capability_id for c in self.selection.selected]

    @property
    def autonomy_served(self) -> bool:
        """True iff the autonomy consult lane handled this query."""

        return (
            self.autonomy_consult is not None
            and self.autonomy_consult.served
        )

    def to_dict(self) -> dict:
        out = {
            "intent": self.intent,
            "quality_path": self.quality_path,
            "selected": self.selected_ids,
            "context_keys": self.context_keys,
            "execution_time_ms": self.execution_time_ms,
            "fallback_used": self.selection.fallback_used,
        }
        if self.solver_call_trace:
            out["solver_call_trace"] = list(self.solver_call_trace)
        if self.autonomy_consult is not None:
            out["autonomy_consult"] = {
                "served": self.autonomy_consult.served,
                "source": self.autonomy_consult.source,
                "solver_name": self.autonomy_consult.solver_name,
                "miss_reason": self.autonomy_consult.miss_reason,
            }
        return out


# ── classify_intent signal helper ──────────────────────────────
#
# Audit finding H22/H58: substring matching ("sum" in "consumption")
# routed natural-language queries to the wrong intent solver. The fix
# is to anchor every signal term at word boundaries. `\b` is unicode-
# aware under Python 3 default flags, so Finnish characters (ä/ö/å)
# participate correctly.
#
# Patterns are compiled once at module load. Each frozenset of signals
# becomes one alternation regex, so a query is scanned in a single pass
# per signal set rather than N substring checks.
_SIGNAL_PATTERN_CACHE: dict[frozenset[str], re.Pattern[str]] = {}
SOLVER_SIGNAL_CONFIG_PATH = (
    Path(__file__).resolve().parents[3] / "configs" / "solver_signals.yaml"
)

_DEFAULT_SIGNAL_SETS: dict[str, frozenset[str]] = {
    "math": frozenset({
        "laske", "calculate", "compute", "paljonko", "how much", "sum",
    }),
    "formula": frozenset({
        "formula", "kaava", "model", "malli", "axiom", "solve for", "ratkaise",
    }),
    "constraint": frozenset({
        "rule", "sääntö", "constraint", "check", "tarkista",
        "compliant", "violation",
    }),
    "seasonal": frozenset({
        "season", "vuodenaika", "kausi", "spring", "kevät", "summer", "kesä",
        "autumn", "syksy", "winter", "talvi",
    }),
    "time_series_metrics": frozenset({
        "average", "trend", "compare", "cost", "consumption",
        "energy", "keskiarvo", "keskimääräinen",
    }),
    "time_series_windows": frozenset({
        "last", "this", "week", "month", "days", "yesterday",
        "today", "viime", "tämä", "viikko", "kuukausi", "päivää",
    }),
    "thermal": frozenset({
        "temperature", "lämpötila", "heat", "frost", "pakkanen",
        "pakkasvaara", "thermal", "heating", "cooling", "lämmitys",
        "celsius", "fahrenheit", "degrees", "astetta", "too hot",
        "too cold", "liian kuuma", "liian kylmä", "warm", "lämmin",
        "cold", "kylmä",
    }),
    "optimization_verbs": frozenset({
        "optimize", "optimoi", "minimize", "maximize", "allocate",
        "cheapest", "halvin", "optimization",
    }),
    "schedule_words": frozenset({
        "schedule", "aikataulu", "kalenteri", "calendar", "timetable",
    }),
    "schedule_verbs": frozenset({
        "optimize", "optimoi", "minimize", "maximize", "allocate",
        "create", "build", "schedule this", "optimization",
    }),
    "optimization": frozenset({
        "optimize", "optimoi", "aikatauluta", "minimize", "allocate",
        "cheapest", "halvin", "optimization",
    }),
    "statistics": frozenset({
        "statistics", "tilasto", "trend", "keskiarvo", "median",
        "percentile", "correlation", "summary",
    }),
    "causal": frozenset({
        "cause", "syy", "why", "miksi", "impact", "vaikutus",
        "root cause", "because", "koska", "depends",
    }),
    "anomaly": frozenset({
        "anomaly", "anomalia", "deviation", "poikkeama",
        "outlier", "unusual", "epätavallinen",
    }),
    "retrieval": frozenset({
        "what is", "mikä on", "tell me", "kerro", "explain", "selitä",
        "search", "hae", "find", "etsi",
    }),
}


def _normalize_signal_sets(raw: object) -> dict[str, frozenset[str]]:
    if not isinstance(raw, dict):
        raise ValueError("solver signal config must be a mapping")

    signal_sets: dict[str, frozenset[str]] = {}
    for name, fallback in _DEFAULT_SIGNAL_SETS.items():
        if name not in raw:
            signal_sets[name] = fallback
            continue
        values = raw[name]
        if not isinstance(values, list):
            raise ValueError(f"solver signal group {name!r} must be a list")
        normalized = {
            str(value).strip().lower()
            for value in values
            if str(value).strip()
        }
        signal_sets[name] = frozenset(normalized or fallback)
    return signal_sets


def _load_solver_signal_sets(
    path: Path = SOLVER_SIGNAL_CONFIG_PATH,
) -> dict[str, frozenset[str]]:
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return _normalize_signal_sets(data)
    except Exception as exc:
        log.warning(
            "Solver signal registry unavailable at %s; using defaults: %s",
            path, exc,
        )
        return dict(_DEFAULT_SIGNAL_SETS)


_SIGNAL_SETS = _load_solver_signal_sets()


def _signals(name: str) -> frozenset[str]:
    return _SIGNAL_SETS.get(name, _DEFAULT_SIGNAL_SETS[name])


def _has_signal(query_lower: str, signals: set[str] | frozenset[str]) -> bool:
    """Return True iff any of *signals* appears as a whole word in *query_lower*.

    *query_lower* MUST already be lower-cased; the caller in
    SolverRouter.classify_intent does the .lower().strip() once and
    reuses the same string for every signal-set check.
    """
    key = frozenset(signals)
    pat = _SIGNAL_PATTERN_CACHE.get(key)
    if pat is None:
        # Sort longest-first so e.g. "how much" is tried before "much"
        # would have been (defensive; current sets don't have overlaps
        # but the cost is negligible).
        alts = "|".join(re.escape(s) for s in sorted(key, key=len, reverse=True))
        pat = re.compile(r"\b(?:" + alts + r")\b")
        _SIGNAL_PATTERN_CACHE[key] = pat
    return bool(pat.search(query_lower))


def is_v3_13_solver_request(query: str) -> bool:
    """Return True for explicit registry-backed v3.13 solver JSON requests."""

    stripped = query.lstrip()
    if not stripped.startswith("{") or '"case_id"' not in stripped:
        return False
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    case_id = payload.get("case_id")
    solver_payload = payload.get("payload")
    return (
        isinstance(case_id, str)
        and "__" in case_id
        and isinstance(solver_payload, dict)
    )


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
        autonomy_consult: Optional[AutonomyConsultCallable] = None,
    ):
        self._registry = registry or CapabilityRegistry()
        self._selector = selector or CapabilitySelector(self._registry)
        self._working_memory = working_memory or WorkingMemory()
        self._autonomy_consult = autonomy_consult
        self._route_history: List[SolverRouteResult] = []
        self._capability_confidence: Optional[Dict[str, float]] = None

    @property
    def autonomy_consult_enabled(self) -> bool:
        return self._autonomy_consult is not None

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

        # 3.5 Phase 14 — autonomy consult.
        # When the built-in capability selection fell back AND the
        # caller passed a structured low-risk autonomy hint, ask the
        # autonomy consult lane to serve the query before the caller
        # falls back to LLM. The consult is *backwards-compatible*:
        # when no consult callable is wired or no hint is supplied,
        # behaviour is exactly the Phase 13 baseline.
        autonomy_outcome: Optional[AutonomyConsultOutcome] = None
        if (
            self._autonomy_consult is not None
            and selection.fallback_used
        ):
            hint = context.get("low_risk_autonomy_query")
            if isinstance(hint, dict) and hint.get("family_kind"):
                try:
                    autonomy_outcome = self._autonomy_consult(hint)
                except Exception as exc:  # noqa: BLE001 — never let
                    # autonomy errors break the production reasoning
                    # path; record a structured 'consult_skipped'.
                    log.warning(
                        "autonomy_consult raised; skipped: %r", exc
                    )
                    autonomy_outcome = AutonomyConsultOutcome(
                        served=False,
                        source="consult_skipped",
                        miss_reason=f"consult_error:{type(exc).__name__}",
                    )

        elapsed = (time.time() - t0) * 1000

        result = SolverRouteResult(
            intent=intent,
            selection=selection,
            context_keys=context_keys,
            execution_time_ms=round(elapsed, 2),
            autonomy_consult=autonomy_outcome,
            solver_call_trace=self._solver_call_trace(
                intent=intent,
                selection=selection,
            ),
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
            solver_call_trace=self._solver_call_trace(
                intent="direct",
                selection=selection,
            ),
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
        if is_v3_13_solver_request(query):
            return "v3_13_solver"

        q = query.lower().strip()

        # ── Early arithmetic detection (overrides "what is" retrieval) ──
        # "what is 15% of 300", "paljonko on 15% sadasta"
        if re.search(r'\d+\s*%\s*(?:of|sadasta)?\s*\d+', q):
            return "math"
        # "what is 12 squared", "5 cubed"
        if re.search(r'\d+\s*(?:squared|cubed)', q):
            return "math"

        # Math / calculation keywords + digit-operator-digit
        if _has_signal(q, _signals("math")):
            return "math"
        # Arithmetic: digit OP digit (excludes bare "-" which appears in "-5C")
        if re.search(r'\d\s*[+*/=]\s*\d', q):
            return "math"
        # Subtraction with whitespace: "256 - 89" but not "-5C" (no left digit)
        if re.search(r'\d\s+-\s+\d', q):
            return "math"

        # Symbolic / formula
        if _has_signal(q, _signals("formula")):
            return "symbolic"

        # Constraint / rule check
        if _has_signal(q, _signals("constraint")):
            return "constraint"

        # Seasonal
        if _has_signal(q, _signals("seasonal")):
            return "seasonal"

        # ── Time-series stats: metric + time window (before thermal) ──
        _TS_METRICS = _signals("time_series_metrics")
        _TS_WINDOWS = _signals("time_series_windows")
        if _has_signal(q, _TS_METRICS) and _has_signal(q, _TS_WINDOWS):
            return "stats"

        # Thermal — but skip if optimization verb present ("optimize heating")
        thermal_signals = _signals("thermal")
        _OPTIM_VERBS = _signals("optimization_verbs")
        has_thermal = _has_signal(q, thermal_signals) or re.search(r'\d+\s*°?[cf]\b', q)
        if has_thermal and not _has_signal(q, _OPTIM_VERBS):
            return "thermal"

        # Schedule disambiguation: schedule without active verb → retrieval
        _SCHED_WORDS = _signals("schedule_words")
        _SCHED_VERBS = _signals("schedule_verbs")
        if _has_signal(q, _SCHED_WORDS):
            if _has_signal(q, _SCHED_VERBS):
                return "optimization"
            return "retrieval"

        # Optimization
        if _has_signal(q, _signals("optimization")):
            return "optimization"

        # Statistics (no time window required)
        if _has_signal(q, _signals("statistics")):
            return "stats"

        # Causal
        if _has_signal(q, _signals("causal")):
            return "causal"

        # Anomaly / deviation
        if _has_signal(q, _signals("anomaly")):
            return "anomaly"

        # Retrieval / search
        if _has_signal(q, _signals("retrieval")):
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

    @staticmethod
    def _solver_call_trace(
        *,
        intent: str,
        selection: SelectionResult,
    ) -> List[Dict[str, Any]]:
        """Build a privacy-safe selected-solver trace.

        SolverRouter selects capabilities; the caller performs execution via
        SafeActionBus. The trace records that boundary explicitly so this is
        not mistaken for a completed solver execution receipt.
        """

        trace: List[Dict[str, Any]] = []
        for selected_index, capability in enumerate(selection.selected):
            if capability.category != CapabilityCategory.SOLVE:
                continue
            trace.append({
                "stage": "solver_call",
                "status": "selected",
                "intent": intent,
                "capability_id": capability.capability_id,
                "selected_index": selected_index,
                "quality_path": selection.quality_path,
                "execution_boundary": "safe_action_bus",
            })
        return trace

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
