"""Capability loader — registers adapter factories with CapabilityRegistry.

Per ADR-041 (L54-reframed): the loader registers LAZY FACTORIES for each
adapter. Factory closes over the (module_path, class_name) pair and, on
first ``get_executor(cap_id)`` call, imports the module + constructs
the adapter + returns it. The import AND the constructor are both
deferred until first use.

Empirical baseline (.tmp-claude-1000-obs/bench-capability-loader.py):
* Old eager `bind_executors()` cold cost: 6855 ms median (93% instantiation).
* New lazy `bind_executors()` cold cost target: < 100 ms (just factory
  registration; no import, no construction).

ADR-041 invariants enforced here:
* CFB-002: factory defers BOTH import AND construct.
* CFB-006: existing register_executor() eager API preserved (not used
  here; available for runtime-extended capabilities).
* MicroModelAdapter exposes two capability IDs (V1 + V2) for the same
  underlying instance. Both factories close over the same module/class
  pair so a single instantiation backs both bindings on first use.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from waggledance.core.capabilities.registry import CapabilityRegistry

log = logging.getLogger("waggledance.bootstrap.capability_loader")


# (capability_id, module_path, class_name)
#
# Each tuple is verified against the adapter's CAPABILITY_ID class
# attribute. Adding a new adapter: append a new tuple here AFTER the
# adapter file is created with a CAPABILITY_ID class-level constant.
_ADAPTER_REGISTRY: list[tuple[str, str, str]] = [
    # Solver adapters
    ("solve.math", "waggledance.adapters.capabilities.math_solver_adapter", "MathSolverAdapter"),
    ("solve.symbolic", "waggledance.adapters.capabilities.symbolic_solver_adapter", "SymbolicSolverAdapter"),
    ("solve.constraints", "waggledance.adapters.capabilities.constraint_engine_adapter", "ConstraintEngineAdapter"),
    # MicroModelAdapter — two capability IDs, single underlying instance (handled below).
    ("explain.llm_reasoning", "waggledance.adapters.capabilities.llm_explainer_adapter", "LLMExplainerAdapter"),
    # Reasoning engine adapters
    ("solve.thermal", "waggledance.adapters.capabilities.thermal_solver_adapter", "ThermalSolverAdapter"),
    ("detect.anomaly", "waggledance.adapters.capabilities.anomaly_detector_adapter", "AnomalyDetectorAdapter"),
    ("solve.stats", "waggledance.adapters.capabilities.stats_engine_adapter", "StatsEngineAdapter"),
    ("optimize.schedule", "waggledance.adapters.capabilities.optimization_adapter", "OptimizationAdapter"),
    ("solve.causal", "waggledance.adapters.capabilities.causal_reasoner_adapter", "CausalReasonerAdapter"),
    ("analyze.routing", "waggledance.adapters.capabilities.route_analyzer_adapter", "RouteAnalyzerAdapter"),
    # Sense + normalize + verify
    ("sense.intent_classify", "waggledance.adapters.capabilities.intent_classifier_adapter", "IntentClassifierAdapter"),
    ("sense.seasonal", "waggledance.adapters.capabilities.seasonal_adapter", "SeasonalAdapter"),
    ("detect.seasonal_rules", "waggledance.adapters.capabilities.seasonal_guard_adapter", "SeasonalGuardAdapter"),
    ("normalize.finnish", "waggledance.adapters.capabilities.finnish_normalizer_adapter", "FinnishNormalizerAdapter"),
    ("normalize.translate_fi_en", "waggledance.adapters.capabilities.translation_adapter", "TranslationAdapter"),
    ("verify.english_output", "waggledance.adapters.capabilities.english_validator_adapter", "EnglishValidatorAdapter"),
    ("verify.hallucination", "waggledance.adapters.capabilities.hallucination_checker_adapter", "HallucinationCheckerAdapter"),
    ("verify.consensus", "waggledance.adapters.capabilities.consensus_adapter", "ConsensusAdapter"),
    # Retrievers
    ("retrieve.hot_cache", "waggledance.adapters.capabilities.hot_cache_adapter", "HotCacheAdapter"),
    ("retrieve.semantic_search", "waggledance.adapters.capabilities.semantic_search_adapter", "SemanticSearchAdapter"),
    ("retrieve.vector_search", "waggledance.adapters.capabilities.vector_search_adapter", "VectorSearchAdapter"),
    # Domain
    ("solve.bee_domain", "waggledance.adapters.capabilities.bee_domain_adapter", "BeeDomainAdapter"),
]


# MicroModelAdapter is special: one adapter instance serves two capability_ids
# (V1 = solve.pattern_match, V2 = solve.neural_classifier). Both factories
# below close over a shared lazy holder so the adapter is constructed exactly
# once on first access of EITHER capability_id.
_MICROMODEL_MODULE = "waggledance.adapters.capabilities.micromodel_adapter"
_MICROMODEL_CLASS = "MicroModelAdapter"


def _make_adapter_factory(
    module_path: str, class_name: str
) -> Callable[[], Any]:
    """Build a factory closure that imports + instantiates on first call.

    Each call returns the same instance (memoized in the closure) so that
    if the factory is invoked multiple times (e.g., by a misconfigured
    caller), we don't pay for repeated instantiation. The CapabilityRegistry
    caches the result in _executors after the first call, so in normal
    operation the factory is invoked exactly once anyway.
    """
    holder: list[Any] = []

    def factory() -> Any:
        if holder:
            return holder[0]
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
        instance = cls()
        holder.append(instance)
        return instance

    return factory


def _make_micromodel_shared_factory() -> Callable[[], Any]:
    """Build the MicroModelAdapter factory once. Both V1 and V2 bindings
    point at it so the adapter is constructed once and shared."""
    return _make_adapter_factory(_MICROMODEL_MODULE, _MICROMODEL_CLASS)


def bind_executors(registry: "CapabilityRegistry") -> int:
    """Register adapter factories with CapabilityRegistry (lazy binding).

    Per ADR-041, this function does NOT import any adapter module at call
    time and does NOT construct any adapter instance. It only registers
    factory closures. First invocation of get_executor(cap_id) triggers
    the actual import + construction.

    Returns:
        Number of factories registered.
    """
    registered = 0

    for cap_id, module_path, class_name in _ADAPTER_REGISTRY:
        factory = _make_adapter_factory(module_path, class_name)
        registry.register_executor_factory(cap_id, factory)
        registered += 1

    # MicroModelAdapter: two capability IDs share one factory + one instance.
    micromodel_factory = _make_micromodel_shared_factory()
    registry.register_executor_factory("solve.pattern_match", micromodel_factory)
    registry.register_executor_factory("solve.neural_classifier", micromodel_factory)
    registered += 2

    # ── Enrich capabilities from YAML configs ─────────────
    # YAML enrichment is metadata-only (max_latency_ms, trust_baseline) and
    # does not trigger adapter import. Still performed at boot.
    try:
        enriched = registry.load_yaml_configs()
        if enriched:
            log.info("YAML configs enriched %d capabilities", enriched)
    except Exception as exc:
        log.debug("YAML config loading failed: %s", exc)

    log.info(
        "Capability loader: %d adapter factories registered (lazy; "
        "first get_executor() call triggers import + construct)",
        registered,
    )
    return registered
