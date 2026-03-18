"""Capability loader — binds adapter executors to CapabilityRegistry entries.

Called during AutonomyRuntime boot to connect capability metadata
(what the system can do) to concrete executors (how it does it).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from waggledance.core.capabilities.registry import CapabilityRegistry

log = logging.getLogger("waggledance.bootstrap.capability_loader")


def bind_executors(registry: "CapabilityRegistry") -> int:
    """Instantiate and bind all available capability adapters.

    Each adapter is imported with try/except — if the underlying legacy
    module is unavailable the adapter is skipped gracefully.

    Returns:
        Number of executors successfully bound.
    """
    bound = 0

    # MathSolverAdapter → solve.math
    try:
        from waggledance.adapters.capabilities.math_solver_adapter import MathSolverAdapter
        adapter = MathSolverAdapter()
        if adapter.available:
            registry.register_executor(adapter.CAPABILITY_ID, adapter)
            bound += 1
            log.info("Bound executor: %s", adapter.CAPABILITY_ID)
        else:
            log.debug("MathSolverAdapter: legacy module not available, skipping")
    except Exception as exc:
        log.debug("MathSolverAdapter import failed: %s", exc)

    # SymbolicSolverAdapter → solve.symbolic
    try:
        from waggledance.adapters.capabilities.symbolic_solver_adapter import SymbolicSolverAdapter
        adapter = SymbolicSolverAdapter()
        if adapter.available:
            registry.register_executor(adapter.CAPABILITY_ID, adapter)
            bound += 1
            log.info("Bound executor: %s", adapter.CAPABILITY_ID)
        else:
            log.debug("SymbolicSolverAdapter: legacy module not available, skipping")
    except Exception as exc:
        log.debug("SymbolicSolverAdapter import failed: %s", exc)

    # ConstraintEngineAdapter → solve.constraints
    try:
        from waggledance.adapters.capabilities.constraint_engine_adapter import ConstraintEngineAdapter
        adapter = ConstraintEngineAdapter()
        if adapter.available:
            registry.register_executor(adapter.CAPABILITY_ID, adapter)
            bound += 1
            log.info("Bound executor: %s", adapter.CAPABILITY_ID)
        else:
            log.debug("ConstraintEngineAdapter: legacy module not available, skipping")
    except Exception as exc:
        log.debug("ConstraintEngineAdapter import failed: %s", exc)

    # MicroModelAdapter → solve.pattern_match (V1) + solve.neural_classifier (V2)
    try:
        from waggledance.adapters.capabilities.micromodel_adapter import MicroModelAdapter
        adapter = MicroModelAdapter()
        if adapter.available:
            registry.register_executor(adapter.CAPABILITY_ID_V1, adapter)
            bound += 1
            log.info("Bound executor: %s", adapter.CAPABILITY_ID_V1)
            if adapter.v2_available:
                registry.register_executor(adapter.CAPABILITY_ID_V2, adapter)
                bound += 1
                log.info("Bound executor: %s", adapter.CAPABILITY_ID_V2)
        else:
            log.debug("MicroModelAdapter: legacy module not available, skipping")
    except Exception as exc:
        log.debug("MicroModelAdapter import failed: %s", exc)

    # LLMExplainerAdapter → explain.llm_reasoning
    try:
        from waggledance.adapters.capabilities.llm_explainer_adapter import LLMExplainerAdapter
        adapter = LLMExplainerAdapter()
        if adapter.available:
            registry.register_executor(adapter.CAPABILITY_ID, adapter)
            bound += 1
            log.info("Bound executor: %s", adapter.CAPABILITY_ID)
        else:
            log.debug("LLMExplainerAdapter: legacy module not available, skipping")
    except Exception as exc:
        log.debug("LLMExplainerAdapter import failed: %s", exc)

    # ── Reasoning engine adapters ─────────────────────────

    # ThermalSolverAdapter → solve.thermal
    try:
        from waggledance.adapters.capabilities.thermal_solver_adapter import ThermalSolverAdapter
        adapter = ThermalSolverAdapter()
        if adapter.available:
            registry.register_executor(adapter.CAPABILITY_ID, adapter)
            bound += 1
            log.info("Bound executor: %s", adapter.CAPABILITY_ID)
    except Exception as exc:
        log.debug("ThermalSolverAdapter import failed: %s", exc)

    # AnomalyDetectorAdapter → detect.anomaly
    try:
        from waggledance.adapters.capabilities.anomaly_detector_adapter import AnomalyDetectorAdapter
        adapter = AnomalyDetectorAdapter()
        if adapter.available:
            registry.register_executor(adapter.CAPABILITY_ID, adapter)
            bound += 1
            log.info("Bound executor: %s", adapter.CAPABILITY_ID)
    except Exception as exc:
        log.debug("AnomalyDetectorAdapter import failed: %s", exc)

    # StatsEngineAdapter → solve.stats
    try:
        from waggledance.adapters.capabilities.stats_engine_adapter import StatsEngineAdapter
        adapter = StatsEngineAdapter()
        if adapter.available:
            registry.register_executor(adapter.CAPABILITY_ID, adapter)
            bound += 1
            log.info("Bound executor: %s", adapter.CAPABILITY_ID)
    except Exception as exc:
        log.debug("StatsEngineAdapter import failed: %s", exc)

    # OptimizationAdapter → optimize.schedule
    try:
        from waggledance.adapters.capabilities.optimization_adapter import OptimizationAdapter
        adapter = OptimizationAdapter()
        if adapter.available:
            registry.register_executor(adapter.CAPABILITY_ID, adapter)
            bound += 1
            log.info("Bound executor: %s", adapter.CAPABILITY_ID)
    except Exception as exc:
        log.debug("OptimizationAdapter import failed: %s", exc)

    # CausalReasonerAdapter → solve.causal
    try:
        from waggledance.adapters.capabilities.causal_reasoner_adapter import CausalReasonerAdapter
        adapter = CausalReasonerAdapter()
        if adapter.available:
            registry.register_executor(adapter.CAPABILITY_ID, adapter)
            bound += 1
            log.info("Bound executor: %s", adapter.CAPABILITY_ID)
    except Exception as exc:
        log.debug("CausalReasonerAdapter import failed: %s", exc)

    # RouteAnalyzerAdapter → analyze.routing
    try:
        from waggledance.adapters.capabilities.route_analyzer_adapter import RouteAnalyzerAdapter
        adapter = RouteAnalyzerAdapter()
        if adapter.available:
            registry.register_executor(adapter.CAPABILITY_ID, adapter)
            bound += 1
            log.info("Bound executor: %s", adapter.CAPABILITY_ID)
    except Exception as exc:
        log.debug("RouteAnalyzerAdapter import failed: %s", exc)

    # ── Legacy wrapper adapters ──────────────────────────

    # HotCacheAdapter → retrieve.hot_cache
    try:
        from waggledance.adapters.capabilities.hot_cache_adapter import HotCacheAdapter
        adapter = HotCacheAdapter()
        if adapter.available:
            registry.register_executor(adapter.CAPABILITY_ID, adapter)
            bound += 1
            log.info("Bound executor: %s", adapter.CAPABILITY_ID)
        else:
            log.debug("HotCacheAdapter: legacy module not available, skipping")
    except Exception as exc:
        log.debug("HotCacheAdapter import failed: %s", exc)

    # SemanticSearchAdapter → retrieve.semantic_search
    try:
        from waggledance.adapters.capabilities.semantic_search_adapter import SemanticSearchAdapter
        adapter = SemanticSearchAdapter()
        if adapter.available:
            registry.register_executor(adapter.CAPABILITY_ID, adapter)
            bound += 1
            log.info("Bound executor: %s", adapter.CAPABILITY_ID)
        else:
            log.debug("SemanticSearchAdapter: not available (needs chromadb_adapter instance)")
    except Exception as exc:
        log.debug("SemanticSearchAdapter import failed: %s", exc)

    # VectorSearchAdapter → retrieve.vector_search
    try:
        from waggledance.adapters.capabilities.vector_search_adapter import VectorSearchAdapter
        adapter = VectorSearchAdapter()
        if adapter.available:
            registry.register_executor(adapter.CAPABILITY_ID, adapter)
            bound += 1
            log.info("Bound executor: %s", adapter.CAPABILITY_ID)
        else:
            log.debug("VectorSearchAdapter: legacy module not available, skipping")
    except Exception as exc:
        log.debug("VectorSearchAdapter import failed: %s", exc)

    # HallucinationCheckerAdapter → verify.hallucination
    try:
        from waggledance.adapters.capabilities.hallucination_checker_adapter import HallucinationCheckerAdapter
        adapter = HallucinationCheckerAdapter()
        if adapter.available:
            registry.register_executor(adapter.CAPABILITY_ID, adapter)
            bound += 1
            log.info("Bound executor: %s", adapter.CAPABILITY_ID)
        else:
            log.debug("HallucinationCheckerAdapter: legacy module not available, skipping")
    except Exception as exc:
        log.debug("HallucinationCheckerAdapter import failed: %s", exc)

    # EnglishValidatorAdapter → verify.english_output
    try:
        from waggledance.adapters.capabilities.english_validator_adapter import EnglishValidatorAdapter
        adapter = EnglishValidatorAdapter()
        if adapter.available:
            registry.register_executor(adapter.CAPABILITY_ID, adapter)
            bound += 1
            log.info("Bound executor: %s", adapter.CAPABILITY_ID)
        else:
            log.debug("EnglishValidatorAdapter: legacy module not available, skipping")
    except Exception as exc:
        log.debug("EnglishValidatorAdapter import failed: %s", exc)

    # ConsensusAdapter → verify.consensus
    try:
        from waggledance.adapters.capabilities.consensus_adapter import ConsensusAdapter
        adapter = ConsensusAdapter()
        if adapter.available:
            registry.register_executor(adapter.CAPABILITY_ID, adapter)
            bound += 1
            log.info("Bound executor: %s", adapter.CAPABILITY_ID)
        else:
            log.debug("ConsensusAdapter: legacy module not available, skipping")
    except Exception as exc:
        log.debug("ConsensusAdapter import failed: %s", exc)

    # FinnishNormalizerAdapter → normalize.finnish
    try:
        from waggledance.adapters.capabilities.finnish_normalizer_adapter import FinnishNormalizerAdapter
        adapter = FinnishNormalizerAdapter()
        if adapter.available:
            registry.register_executor(adapter.CAPABILITY_ID, adapter)
            bound += 1
            log.info("Bound executor: %s", adapter.CAPABILITY_ID)
        else:
            log.debug("FinnishNormalizerAdapter: legacy module not available, skipping")
    except Exception as exc:
        log.debug("FinnishNormalizerAdapter import failed: %s", exc)

    # TranslationAdapter → normalize.translate_fi_en
    try:
        from waggledance.adapters.capabilities.translation_adapter import TranslationAdapter
        adapter = TranslationAdapter()
        if adapter.available:
            registry.register_executor(adapter.CAPABILITY_ID, adapter)
            bound += 1
            log.info("Bound executor: %s", adapter.CAPABILITY_ID)
        else:
            log.debug("TranslationAdapter: legacy module not available, skipping")
    except Exception as exc:
        log.debug("TranslationAdapter import failed: %s", exc)

    # IntentClassifierAdapter → sense.intent_classify
    try:
        from waggledance.adapters.capabilities.intent_classifier_adapter import IntentClassifierAdapter
        adapter = IntentClassifierAdapter()
        if adapter.available:
            registry.register_executor(adapter.CAPABILITY_ID, adapter)
            bound += 1
            log.info("Bound executor: %s", adapter.CAPABILITY_ID)
        else:
            log.debug("IntentClassifierAdapter: legacy module not available, skipping")
    except Exception as exc:
        log.debug("IntentClassifierAdapter import failed: %s", exc)

    # SeasonalGuardAdapter → detect.seasonal_rules
    try:
        from waggledance.adapters.capabilities.seasonal_guard_adapter import SeasonalGuardAdapter
        adapter = SeasonalGuardAdapter()
        if adapter.available:
            registry.register_executor(adapter.CAPABILITY_ID, adapter)
            bound += 1
            log.info("Bound executor: %s", adapter.CAPABILITY_ID)
        else:
            log.debug("SeasonalGuardAdapter: legacy module not available, skipping")
    except Exception as exc:
        log.debug("SeasonalGuardAdapter import failed: %s", exc)

    # ── Sensor adapters ──────────────────────────────────

    # MQTTAdapter → sense.mqtt_ingest
    try:
        from waggledance.adapters.sensors.mqtt_adapter import MQTTAdapter
        adapter = MQTTAdapter()
        if adapter.available:
            registry.register_executor(adapter.CAPABILITY_ID, adapter)
            bound += 1
            log.info("Bound executor: %s", adapter.CAPABILITY_ID)
        else:
            log.debug("MQTTAdapter: no MQTT hub available, skipping")
    except Exception as exc:
        log.debug("MQTTAdapter import failed: %s", exc)

    # FrigateAdapter → sense.camera_frigate
    try:
        from waggledance.adapters.sensors.frigate_adapter import FrigateAdapter
        adapter = FrigateAdapter()
        if adapter.available:
            registry.register_executor(adapter.CAPABILITY_ID, adapter)
            bound += 1
            log.info("Bound executor: %s", adapter.CAPABILITY_ID)
        else:
            log.debug("FrigateAdapter: no Frigate instance available, skipping")
    except Exception as exc:
        log.debug("FrigateAdapter import failed: %s", exc)

    # HomeAssistantAdapter → sense.home_assistant
    try:
        from waggledance.adapters.sensors.home_assistant_adapter import HomeAssistantAdapter
        adapter = HomeAssistantAdapter()
        if adapter.available:
            registry.register_executor(adapter.CAPABILITY_ID, adapter)
            bound += 1
            log.info("Bound executor: %s", adapter.CAPABILITY_ID)
        else:
            log.debug("HomeAssistantAdapter: no HA bridge available, skipping")
    except Exception as exc:
        log.debug("HomeAssistantAdapter import failed: %s", exc)

    # AudioAdapter → sense.audio
    try:
        from waggledance.adapters.sensors.audio_adapter import AudioAdapter
        adapter = AudioAdapter()
        if adapter.available:
            registry.register_executor(adapter.CAPABILITY_ID, adapter)
            bound += 1
            log.info("Bound executor: %s", adapter.CAPABILITY_ID)
        else:
            log.debug("AudioAdapter: no audio monitor available, skipping")
    except Exception as exc:
        log.debug("AudioAdapter import failed: %s", exc)

    # SensorFusionAdapter → sense.fusion
    try:
        from waggledance.adapters.sensors.sensor_fusion_adapter import SensorFusionAdapter
        adapter = SensorFusionAdapter()
        if adapter.available:
            registry.register_executor(adapter.CAPABILITY_ID, adapter)
            bound += 1
            log.info("Bound executor: %s", adapter.CAPABILITY_ID)
        else:
            log.debug("SensorFusionAdapter: no sensor sources available, skipping")
    except Exception as exc:
        log.debug("SensorFusionAdapter import failed: %s", exc)

    # ── Domain engine adapters (Priority 5) ────────────────

    # SeasonalAdapter → sense.seasonal
    try:
        from waggledance.adapters.capabilities.seasonal_adapter import SeasonalAdapter
        adapter = SeasonalAdapter()
        if adapter.available:
            registry.register_executor(adapter.CAPABILITY_ID, adapter)
            bound += 1
            log.info("Bound executor: %s", adapter.CAPABILITY_ID)
        else:
            log.debug("SeasonalAdapter: seasonal YAML not available, skipping")
    except Exception as exc:
        log.debug("SeasonalAdapter import failed: %s", exc)

    # BeeDomainAdapter → solve.bee_domain
    try:
        from waggledance.adapters.capabilities.bee_domain_adapter import BeeDomainAdapter
        adapter = BeeDomainAdapter()
        if adapter.available:
            registry.register_executor(adapter.CAPABILITY_ID, adapter)
            bound += 1
            log.info("Bound executor: %s", adapter.CAPABILITY_ID)
        else:
            log.debug("BeeDomainAdapter: not available, skipping")
    except Exception as exc:
        log.debug("BeeDomainAdapter import failed: %s", exc)

    # ── Enrich capabilities from YAML configs ─────────────
    try:
        enriched = registry.load_yaml_configs()
        if enriched:
            log.info("YAML configs enriched %d capabilities", enriched)
    except Exception as exc:
        log.debug("YAML config loading failed: %s", exc)

    log.info("Capability loader: %d executors bound", bound)
    return bound
