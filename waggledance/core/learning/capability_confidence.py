# SPDX-License-Identifier: Apache-2.0
"""
Capability Confidence — EMA-updated per-solver confidence scores.

Tracks how reliably each solver/capability produces verified results.
Updated after every verifier result:

    new_confidence = alpha * old_confidence + (1 - alpha) * (1.0 if verified else 0.0)

Default alpha = 0.95 (slow-moving average — emphasises history).

Stored on self-entity in the CognitiveGraph as:
    self_entity["capability_confidence"] = {"solve.math": 0.97, ...}

Persisted to JSON file for restart resilience.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("waggledance.learning.capability_confidence")

DEFAULT_CONFIDENCE_PATH = "data/capability_confidence.json"
DEFAULT_ALPHA = 0.95
DEFAULT_INITIAL_CONFIDENCE = 0.5


@dataclass
class ConfidenceEntry:
    """Confidence state for a single capability."""
    capability_id: str
    confidence: float = DEFAULT_INITIAL_CONFIDENCE
    total_observations: int = 0
    successes: int = 0
    last_updated: float = field(default_factory=time.time)


class CapabilityConfidenceTracker:
    """Tracks and updates per-solver capability confidence via EMA."""

    def __init__(
        self,
        persist_path: str = DEFAULT_CONFIDENCE_PATH,
        alpha: float = DEFAULT_ALPHA,
        initial_confidence: float = DEFAULT_INITIAL_CONFIDENCE,
    ):
        self._path = Path(persist_path)
        self._alpha = alpha
        self._initial = initial_confidence
        self._entries: Dict[str, ConfidenceEntry] = {}
        self._load()

    def update(self, capability_id: str, verified: bool) -> float:
        """Update confidence for a capability after a verifier result.

        Returns the new confidence value.
        """
        if capability_id not in self._entries:
            self._entries[capability_id] = ConfidenceEntry(
                capability_id=capability_id,
                confidence=self._initial,
            )

        entry = self._entries[capability_id]
        observation = 1.0 if verified else 0.0
        entry.confidence = self._alpha * entry.confidence + (1.0 - self._alpha) * observation
        entry.total_observations += 1
        if verified:
            entry.successes += 1
        entry.last_updated = time.time()

        self._save()
        return entry.confidence

    def get_confidence(self, capability_id: str) -> float:
        """Get current confidence for a capability."""
        entry = self._entries.get(capability_id)
        return entry.confidence if entry else self._initial

    def get_all(self) -> Dict[str, float]:
        """Get confidence map for all tracked capabilities."""
        return {
            cap_id: entry.confidence
            for cap_id, entry in self._entries.items()
        }

    def get_lowest(self, n: int = 5) -> List[Tuple[str, float]]:
        """Get the N capabilities with lowest confidence."""
        items = sorted(self._entries.items(), key=lambda x: x[1].confidence)
        return [(cap_id, entry.confidence) for cap_id, entry in items[:n]]

    def get_trends(self, n: int = 3) -> Tuple[List[Tuple[str, float]], List[Tuple[str, float]]]:
        """Get top-N improving and degrading capabilities.

        Improving = confidence is higher than raw success rate (EMA boosted).
        Degrading = confidence is lower than raw success rate (EMA dragged down).

        Returns: (improving, degrading) lists of (capability_id, delta).
        """
        deltas: List[Tuple[str, float]] = []
        for cap_id, entry in self._entries.items():
            if entry.total_observations < 3:
                continue
            raw_rate = entry.successes / entry.total_observations
            delta = entry.confidence - raw_rate
            deltas.append((cap_id, delta))

        deltas.sort(key=lambda x: x[1], reverse=True)
        improving = deltas[:n]
        degrading = deltas[-n:] if len(deltas) > n else []
        # Degrading: filter to negative deltas only
        degrading = [(c, d) for c, d in degrading if d < 0]
        return improving, degrading

    def sync_to_self_entity(self, world_model) -> bool:
        """Push confidence map to self-entity in CognitiveGraph."""
        if world_model is None or world_model.graph is None:
            return False
        confidence_map = self.get_all()
        if not confidence_map:
            return False
        # Round for readability
        rounded = {k: round(v, 4) for k, v in confidence_map.items()}
        world_model.update_self_entity(capability_confidence=rounded)
        return True

    def stats(self) -> Dict[str, Any]:
        all_conf = self.get_all()
        return {
            "tracked_capabilities": len(self._entries),
            "mean_confidence": (
                sum(all_conf.values()) / len(all_conf) if all_conf else 0.0
            ),
            "lowest": self.get_lowest(3),
            "alpha": self._alpha,
        }

    # ── Persistence ──────────────────────────────────────────

    def _save(self):
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            for cap_id, entry in self._entries.items():
                data[cap_id] = {
                    "confidence": entry.confidence,
                    "total_observations": entry.total_observations,
                    "successes": entry.successes,
                    "last_updated": entry.last_updated,
                }
            self._path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            log.warning("Failed to save capability confidence: %s", exc)

    def _load(self):
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for cap_id, vals in data.items():
                self._entries[cap_id] = ConfidenceEntry(
                    capability_id=cap_id,
                    confidence=vals.get("confidence", self._initial),
                    total_observations=vals.get("total_observations", 0),
                    successes=vals.get("successes", 0),
                    last_updated=vals.get("last_updated", 0.0),
                )
        except Exception as exc:
            log.warning("Failed to load capability confidence: %s", exc)
