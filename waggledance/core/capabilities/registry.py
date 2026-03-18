"""
Capability Registry — first-class registry of all system capabilities.

Each capability is a contract: what it can do, what it requires, how it
fails, and how it rolls back. The registry is loaded from:
  1. Existing solvers (symbolic, constraint, math)
  2. Existing retrievers (HotCache, FAISS, ChromaDB)
  3. Existing normalizers (Finnish, translation)
  4. Existing verifiers (hallucination, consensus)
  5. Agent definitions (legacy agents wrapped as capabilities)
  6. Sensor integrations (MQTT, HA, Frigate)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from waggledance.core.domain.autonomy import CapabilityCategory, CapabilityContract

log = logging.getLogger("waggledance.capabilities.registry")

# Default capability definitions mapping current components
_BUILTIN_CAPABILITIES: List[dict] = [
    # ── Solvers ───────────────────────────────────────────
    {
        "capability_id": "solve.math",
        "category": "solve",
        "description": "Mathematical expression evaluator",
        "preconditions": ["numbers_present"],
        "success_criteria": ["result_verified"],
    },
    {
        "capability_id": "solve.symbolic",
        "category": "solve",
        "description": "Axiom-based formula evaluation (ModelRegistry)",
        "preconditions": ["model_available", "inputs_present"],
        "success_criteria": ["result_verified", "validation_passed"],
    },
    {
        "capability_id": "solve.constraints",
        "category": "solve",
        "description": "Rule-based constraint evaluation",
        "preconditions": ["rules_loaded", "context_available"],
        "success_criteria": ["rules_evaluated"],
    },
    {
        "capability_id": "solve.pattern_match",
        "category": "solve",
        "description": "MicroModel V1 pattern matching",
        "preconditions": ["model_loaded"],
        "success_criteria": ["confidence_above_threshold"],
    },
    {
        "capability_id": "solve.neural_classifier",
        "category": "solve",
        "description": "MicroModel V2 neural classifier",
        "preconditions": ["model_loaded"],
        "success_criteria": ["confidence_above_threshold"],
    },
    # ── Retrieval ─────────────────────────────────────────
    {
        "capability_id": "retrieve.hot_cache",
        "category": "retrieve",
        "description": "Fast in-memory HotCache lookup",
        "preconditions": [],
        "success_criteria": ["cache_hit"],
    },
    {
        "capability_id": "retrieve.semantic_search",
        "category": "retrieve",
        "description": "ChromaDB semantic memory search",
        "preconditions": ["embeddings_available"],
        "success_criteria": ["results_found"],
    },
    {
        "capability_id": "retrieve.vector_search",
        "category": "retrieve",
        "description": "FAISS vector similarity search",
        "preconditions": ["index_loaded"],
        "success_criteria": ["results_above_threshold"],
    },
    # ── Normalization ─────────────────────────────────────
    {
        "capability_id": "normalize.finnish",
        "category": "normalize",
        "description": "Finnish text normalization (Voikko)",
        "preconditions": ["voikko_available"],
        "success_criteria": ["text_normalized"],
    },
    {
        "capability_id": "normalize.translate_fi_en",
        "category": "normalize",
        "description": "Finnish to English translation proxy",
        "preconditions": ["translation_model_available"],
        "success_criteria": ["translation_complete"],
    },
    # ── Sensing ───────────────────────────────────────────
    {
        "capability_id": "sense.intent_classify",
        "category": "sense",
        "description": "SmartRouterV2 keyword-based intent classification",
        "preconditions": [],
        "success_criteria": ["intent_resolved"],
    },
    {
        "capability_id": "sense.mqtt_ingest",
        "category": "sense",
        "description": "MQTT sensor data ingestion",
        "preconditions": ["mqtt_connected"],
        "success_criteria": ["data_received"],
        "rollback_possible": False,
    },
    {
        "capability_id": "sense.home_assistant",
        "category": "sense",
        "description": "Home Assistant entity polling",
        "preconditions": ["ha_api_available"],
        "success_criteria": ["entities_fetched"],
        "rollback_possible": False,
    },
    {
        "capability_id": "sense.camera_frigate",
        "category": "sense",
        "description": "Frigate NVR camera events",
        "preconditions": ["frigate_connected"],
        "success_criteria": ["events_processed"],
        "rollback_possible": False,
    },
    {
        "capability_id": "sense.audio",
        "category": "sense",
        "description": "Audio monitoring for bee colony and bird sounds",
        "preconditions": ["audio_monitor_available"],
        "success_criteria": ["audio_processed"],
        "rollback_possible": False,
    },
    {
        "capability_id": "sense.fusion",
        "category": "sense",
        "description": "Multi-source sensor fusion and context building",
        "preconditions": [],
        "success_criteria": ["observations_merged"],
        "rollback_possible": False,
    },
    # ── Verification ──────────────────────────────────────
    {
        "capability_id": "verify.hallucination",
        "category": "verify",
        "description": "Hallucination checker for LLM outputs",
        "preconditions": ["response_available"],
        "success_criteria": ["no_hallucination_detected"],
    },
    {
        "capability_id": "verify.consensus",
        "category": "verify",
        "description": "Round Table multi-agent consensus",
        "preconditions": ["multiple_agents_available"],
        "success_criteria": ["consensus_reached"],
    },
    {
        "capability_id": "verify.english_output",
        "category": "verify",
        "description": "English output quality validator",
        "preconditions": ["response_available"],
        "success_criteria": ["quality_check_passed"],
    },
    # ── Explanation ───────────────────────────────────────
    {
        "capability_id": "explain.llm_reasoning",
        "category": "explain",
        "description": "LLM-based reasoning and explanation (Ollama)",
        "preconditions": ["ollama_available"],
        "success_criteria": ["response_generated"],
    },
    # ── Detection ─────────────────────────────────────────
    {
        "capability_id": "detect.seasonal_rules",
        "category": "detect",
        "description": "Seasonal guard rule matching",
        "preconditions": ["calendar_available"],
        "success_criteria": ["rules_checked"],
    },
    {
        "capability_id": "detect.anomaly",
        "category": "detect",
        "description": "Statistical anomaly detection via residuals",
        "preconditions": ["baselines_available"],
        "success_criteria": ["anomaly_score_computed"],
    },
    # ── Reasoning engines ────────────────────────────────
    {
        "capability_id": "solve.thermal",
        "category": "solve",
        "description": "Physics-based thermal calculations (heat loss, frost, COP)",
        "preconditions": ["numbers_present"],
        "success_criteria": ["result_computed"],
    },
    {
        "capability_id": "solve.stats",
        "category": "solve",
        "description": "Statistical analysis and time-series summarization",
        "preconditions": [],
        "success_criteria": ["summary_computed"],
    },
    {
        "capability_id": "solve.causal",
        "category": "solve",
        "description": "Causal chain discovery and impact analysis",
        "preconditions": [],
        "success_criteria": ["chain_found"],
    },
    {
        "capability_id": "optimize.schedule",
        "category": "optimize",
        "description": "Energy cost minimization and task scheduling",
        "preconditions": [],
        "success_criteria": ["solution_found"],
    },
    {
        "capability_id": "analyze.routing",
        "category": "detect",
        "description": "Route accuracy tracking and optimization recommendations",
        "preconditions": [],
        "success_criteria": ["metrics_computed"],
    },
    # ── Domain engines (profile-gated) ───────────────────
    {
        "capability_id": "sense.seasonal",
        "category": "sense",
        "description": "Finnish beekeeping seasonal calendar and recommendations",
        "preconditions": ["seasonal_yaml_available"],
        "success_criteria": ["recommendations_returned"],
        "rollback_possible": True,
    },
    {
        "capability_id": "solve.bee_domain",
        "category": "solve",
        "description": "Bee colony health, swarm risk, honey yield, disease diagnosis",
        "preconditions": [],
        "success_criteria": ["assessment_computed"],
        "rollback_possible": True,
    },
]


class CapabilityRegistry:
    """
    Central registry of all capabilities the system can invoke.

    Capabilities are loaded from builtin definitions and can be
    extended at runtime by registering new capabilities.
    """

    def __init__(self, load_builtins: bool = True):
        self._capabilities: Dict[str, CapabilityContract] = {}
        self._executors: Dict[str, Any] = {}
        if load_builtins:
            self._load_builtins()

    def _load_builtins(self):
        """Load the default capability definitions."""
        for defn in _BUILTIN_CAPABILITIES:
            cap = CapabilityContract(
                capability_id=defn["capability_id"],
                category=CapabilityCategory(defn["category"]),
                description=defn.get("description", ""),
                preconditions=defn.get("preconditions", []),
                success_criteria=defn.get("success_criteria", []),
                rollback_possible=defn.get("rollback_possible", True),
            )
            self._capabilities[cap.capability_id] = cap
        log.info("Loaded %d builtin capabilities", len(self._capabilities))

    def load_yaml_configs(self, config_dir: Optional[str] = None) -> int:
        """Load capability configs from YAML files, enriching existing entries.

        YAML configs provide additional metadata (max_latency_ms, trust_baseline)
        that supplements the builtin Python definitions. If a capability ID in YAML
        matches an existing entry, it is enriched; otherwise a new entry is created.

        Returns:
            Number of capabilities enriched or added.
        """
        try:
            import yaml
        except ImportError:
            log.debug("PyYAML not available, skipping YAML config loading")
            return 0

        if config_dir is None:
            config_dir = str(Path(__file__).resolve().parents[3] / "configs" / "capabilities")

        config_path = Path(config_dir)
        if not config_path.is_dir():
            log.debug("Capability config dir not found: %s", config_path)
            return 0

        enriched = 0
        for yaml_file in sorted(config_path.glob("*.yaml")):
            try:
                with open(yaml_file, encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if not isinstance(data, dict):
                    continue
                caps = data.get("capabilities", [])
                for defn in caps:
                    cap_id = defn.get("id", "")
                    if not cap_id:
                        continue
                    existing = self._capabilities.get(cap_id)
                    if existing:
                        # Enrich existing capability with YAML metadata
                        if "max_latency_ms" in defn:
                            existing.max_latency_ms = float(defn["max_latency_ms"])
                        if "trust_baseline" in defn:
                            existing.trust_score = float(defn["trust_baseline"])
                        if "description" in defn and not existing.description:
                            existing.description = defn["description"]
                        enriched += 1
                    else:
                        # New capability from YAML
                        category_str = defn.get("category", "solve").lower()
                        try:
                            category = CapabilityCategory(category_str)
                        except ValueError:
                            category = CapabilityCategory.SOLVE
                        cap = CapabilityContract(
                            capability_id=cap_id,
                            category=category,
                            description=defn.get("description", ""),
                            preconditions=defn.get("preconditions", []),
                            success_criteria=defn.get("success_criteria", []),
                            rollback_possible=defn.get("rollback_possible", True),
                            max_latency_ms=float(defn.get("max_latency_ms", 5000.0)),
                            trust_score=float(defn.get("trust_baseline", 0.5)),
                        )
                        self._capabilities[cap_id] = cap
                        enriched += 1
            except Exception as exc:
                log.debug("Failed to load %s: %s", yaml_file, exc)

        if enriched:
            log.info("YAML config: enriched/added %d capabilities from %s", enriched, config_path)
        return enriched

    # ── Registration ──────────────────────────────────────

    def register(self, capability: CapabilityContract) -> None:
        """Register or replace a capability."""
        self._capabilities[capability.capability_id] = capability
        log.debug("Registered capability: %s", capability.capability_id)

    def unregister(self, capability_id: str) -> bool:
        if capability_id in self._capabilities:
            del self._capabilities[capability_id]
            return True
        return False

    # ── Executor binding ────────────────────────────────────

    def register_executor(self, capability_id: str, executor: Any) -> None:
        """Bind an executor (adapter) to a capability ID.

        Executors are duck-typed objects with execute() and available.
        """
        self._executors[capability_id] = executor
        log.debug("Bound executor for capability: %s", capability_id)

    def get_executor(self, capability_id: str) -> Optional[Any]:
        """Get the executor bound to a capability ID, or None."""
        return self._executors.get(capability_id)

    def executor_count(self) -> int:
        """Return number of bound executors."""
        return len(self._executors)

    def executor_ids(self) -> List[str]:
        """Return capability IDs that have bound executors."""
        return list(self._executors.keys())

    # ── Lookup ────────────────────────────────────────────

    def get(self, capability_id: str) -> Optional[CapabilityContract]:
        return self._capabilities.get(capability_id)

    def list_all(self) -> List[CapabilityContract]:
        return list(self._capabilities.values())

    def list_by_category(self, category: CapabilityCategory) -> List[CapabilityContract]:
        return [c for c in self._capabilities.values() if c.category == category]

    def list_ids(self) -> List[str]:
        return list(self._capabilities.keys())

    def has(self, capability_id: str) -> bool:
        return capability_id in self._capabilities

    def count(self) -> int:
        return len(self._capabilities)

    # ── Query helpers ─────────────────────────────────────

    def solvers(self) -> List[CapabilityContract]:
        return self.list_by_category(CapabilityCategory.SOLVE)

    def retrievers(self) -> List[CapabilityContract]:
        return self.list_by_category(CapabilityCategory.RETRIEVE)

    def verifiers(self) -> List[CapabilityContract]:
        return self.list_by_category(CapabilityCategory.VERIFY)

    def sensors(self) -> List[CapabilityContract]:
        return self.list_by_category(CapabilityCategory.SENSE)

    def categories(self) -> Set[str]:
        return {c.category.value for c in self._capabilities.values()}

    def stats(self) -> dict:
        cat_counts: Dict[str, int] = {}
        for c in self._capabilities.values():
            cat = c.category.value
            cat_counts[cat] = cat_counts.get(cat, 0) + 1
        return {
            "total": len(self._capabilities),
            "categories": cat_counts,
            "executors_bound": len(self._executors),
        }
