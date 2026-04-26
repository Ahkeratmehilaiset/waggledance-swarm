# SPDX-License-Identifier: BUSL-1.1
"""
Compatibility Layer — wraps legacy HiveMind path for backward compatibility.

When compatibility_mode=True, queries can still flow through the legacy
HiveMind path. This layer adapts legacy requests/responses to the new
autonomy runtime format.

When compatibility_mode=False, this layer is inactive and all queries
go through the new AutonomyRuntime directly.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger("waggledance.autonomy.compatibility")


@dataclass
class LegacyResult:
    """Result from legacy HiveMind path."""
    answer: str = ""
    confidence: float = 0.0
    source: str = ""
    route_type: str = ""
    model: str = ""
    elapsed_ms: float = 0.0


@dataclass
class AutonomyResult:
    """Result from new autonomy runtime."""
    intent: str = ""
    quality_path: str = "bronze"
    capability: str = ""
    executed: bool = False
    result: Optional[Dict[str, Any]] = None
    approved: bool = False
    elapsed_ms: float = 0.0


class CompatibilityLayer:
    """
    Bridges legacy HiveMind and new AutonomyRuntime.

    Routes queries to the appropriate path based on mode:
      - compatibility_mode=True: legacy primary, new runtime shadows
      - compatibility_mode=False: new runtime primary, no legacy fallback
    """

    def __init__(
        self,
        runtime: Optional[Any] = None,
        legacy: Optional[Any] = None,
        compatibility_mode: bool = False,
    ):
        self._runtime = runtime
        self._legacy = legacy
        self._compatibility_mode = compatibility_mode
        self._legacy_calls = 0
        self._runtime_calls = 0
        self._shadow_mismatches = 0

    @property
    def compatibility_mode(self) -> bool:
        return self._compatibility_mode

    def set_compatibility_mode(self, enabled: bool):
        self._compatibility_mode = enabled
        log.info("Compatibility mode: %s", "enabled" if enabled else "disabled")

    def handle_query(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Route a query through the appropriate path.

        In compatibility mode: legacy primary
        In autonomy mode: new runtime primary
        """
        context = context or {}

        if self._compatibility_mode:
            return self._handle_legacy(query, context)
        else:
            return self._handle_autonomy(query, context)

    def adapt_legacy_to_autonomy(self, legacy: LegacyResult) -> Dict[str, Any]:
        """Convert a legacy result to autonomy format."""
        # Map route_type to quality_path
        quality_map = {
            "hotcache": "gold",
            "micromodel": "silver",
            "memory": "silver",
            "llm": "bronze",
            "swarm": "bronze",
        }
        quality_path = quality_map.get(legacy.route_type, "bronze")

        # Map route_type to capability
        cap_map = {
            "hotcache": "retrieve.hot_cache",
            "micromodel": "solve.pattern_match",
            "memory": "retrieve.semantic_search",
            "llm": "explain.llm_reasoning",
            "swarm": "explain.llm_reasoning",
        }
        capability = cap_map.get(legacy.route_type, "explain.llm_reasoning")

        return {
            "intent": "chat",
            "quality_path": quality_path,
            "capability": capability,
            "executed": True,
            "result": {"answer": legacy.answer},
            "approved": True,
            "elapsed_ms": legacy.elapsed_ms,
            "source": "legacy",
        }

    def adapt_autonomy_to_legacy(self, autonomy: Dict[str, Any]) -> LegacyResult:
        """Convert an autonomy result to legacy format."""
        result_data = autonomy.get("result") or {}
        answer = result_data.get("answer", str(result_data))

        # Map capability to route_type
        cap = autonomy.get("capability", "")
        if "retrieve" in cap:
            route_type = "memory"
        elif "solve" in cap:
            route_type = "micromodel"
        else:
            route_type = "llm"

        # Map quality_path to confidence
        confidence_map = {"gold": 0.95, "silver": 0.8, "bronze": 0.5}
        confidence = confidence_map.get(autonomy.get("quality_path", ""), 0.5)

        return LegacyResult(
            answer=answer,
            confidence=confidence,
            source=cap,
            route_type=route_type,
            elapsed_ms=autonomy.get("elapsed_ms", 0.0),
        )

    def stats(self) -> dict:
        return {
            "compatibility_mode": self._compatibility_mode,
            "legacy_calls": self._legacy_calls,
            "runtime_calls": self._runtime_calls,
            "shadow_mismatches": self._shadow_mismatches,
        }

    # ── Internal ───────────────────────────────────────────

    def _handle_legacy(
        self, query: str, context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Handle query via legacy path."""
        self._legacy_calls += 1

        if self._legacy is None:
            return {
                "intent": "chat",
                "quality_path": "bronze",
                "result": None,
                "error": "Legacy runtime not available",
                "source": "legacy",
                "elapsed_ms": 0.0,
            }

        # Use legacy HiveMind
        t0 = time.time()
        try:
            result = self._legacy.handle_query(query, context)
            elapsed = round((time.time() - t0) * 1000, 2)
            result["elapsed_ms"] = elapsed
            result["source"] = "legacy"
            return result
        except Exception as e:
            return {
                "intent": "chat",
                "quality_path": "bronze",
                "result": None,
                "error": str(e),
                "source": "legacy",
                "elapsed_ms": round((time.time() - t0) * 1000, 2),
            }

    def _handle_autonomy(
        self, query: str, context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Handle query via new autonomy runtime."""
        self._runtime_calls += 1

        if self._runtime is None:
            return {
                "intent": "chat",
                "quality_path": "bronze",
                "result": None,
                "error": "Autonomy runtime not available",
                "source": "autonomy",
                "elapsed_ms": 0.0,
            }

        try:
            result = self._runtime.handle_query(query, context)
            result["source"] = "autonomy"
            return result
        except Exception as e:
            return {
                "intent": "chat",
                "quality_path": "bronze",
                "result": None,
                "error": str(e),
                "source": "autonomy",
                "elapsed_ms": 0.0,
            }
