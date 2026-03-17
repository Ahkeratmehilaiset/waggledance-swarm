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
from typing import Dict, List, Optional, Set

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
]


class CapabilityRegistry:
    """
    Central registry of all capabilities the system can invoke.

    Capabilities are loaded from builtin definitions and can be
    extended at runtime by registering new capabilities.
    """

    def __init__(self, load_builtins: bool = True):
        self._capabilities: Dict[str, CapabilityContract] = {}
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
        }
