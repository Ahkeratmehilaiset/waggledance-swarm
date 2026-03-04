"""
WaggleDance — Phase 6: Bird Monitor
=====================================
Bird species identification with BirdNET-Lite stub.
Graceful degradation: if BirdNET/tflite not installed → log warning, continue.
Predator detection triggers alerts.
"""

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("bird_monitor")


@dataclass
class BirdDetection:
    """Single bird detection result."""
    species: str = ""
    species_fi: str = ""
    confidence: float = 0.0
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    is_predator: bool = False


class BirdMonitor:
    """Bird species identification and predator detection."""

    # Predator species that trigger alerts
    PREDATOR_SPECIES = {
        "bear", "wolf", "eagle", "hawk", "owl", "fox",
        "marten", "badger", "raccoon dog",
    }

    # Finnish translations for common species
    SPECIES_FI = {
        "great tit": "talitiainen",
        "blue tit": "sinitiainen",
        "european robin": "punarinta",
        "common blackbird": "mustarastas",
        "chaffinch": "peippo",
        "house sparrow": "varpunen",
        "magpie": "harakka",
        "hooded crow": "varis",
        "common cuckoo": "kaki",
        "barn swallow": "haarapaasky",
        "white wagtail": "vastarak",
        "goldfinch": "tikli",
        "greenfinch": "viherpeippo",
        "starling": "kottarainen",
        "woodpecker": "tikka",
        "eagle": "kotka",
        "hawk": "haukka",
        "owl": "pollo",
        "crane": "kurki",
        "swan": "joutsen",
        "goose": "hanhi",
        "bear": "karhu",
        "wolf": "susi",
        "fox": "kettu",
        "marten": "nata",
        "badger": "makra",
        "raccoon dog": "supikoira",
    }

    def __init__(self, config: dict):
        """
        Args:
            config: audio.bird_monitor section from settings.yaml
        """
        self._config = config
        self._enabled = config.get("enabled", False)
        self._model_name = config.get("model", "BirdNET-Lite")

        self._model_loaded = False
        self._recent_detections: deque = deque(maxlen=100)
        self._total_classifications = 0
        self._predator_alerts = 0

        log.info("BirdMonitor initialized (enabled=%s, model=%s)",
                 self._enabled, self._model_name)

    async def initialize(self) -> bool:
        """Try to load BirdNET model. Gracefully degrade if unavailable.

        Returns:
            True if model loaded, False if degraded mode
        """
        if not self._enabled:
            log.info("BirdMonitor disabled in config")
            return False

        try:
            # Attempt to import BirdNET / tflite
            import importlib
            birdnet = importlib.import_module("birdnet")
            self._model_loaded = True
            log.info("BirdNET model loaded successfully")
            return True
        except (ImportError, ModuleNotFoundError):
            log.warning("BirdNET/tflite not installed — bird classification disabled. "
                        "Install with: pip install birdnetlib")
            self._model_loaded = False
            return False
        except Exception as e:
            log.warning("BirdNET init failed: %s — continuing without bird classification", e)
            self._model_loaded = False
            return False

    def classify(self, audio_data: dict) -> Optional[BirdDetection]:
        """Classify audio data for bird species.

        Args:
            audio_data: Dict with "species", "confidence" keys (from BirdNET pipeline)
                       or raw audio spectrum for model-based classification

        Returns:
            BirdDetection if identified, None if no model or no match
        """
        if not self._model_loaded and not audio_data.get("species"):
            return None

        self._total_classifications += 1

        species = audio_data.get("species", "").lower()
        confidence = float(audio_data.get("confidence", 0.0))

        if not species or confidence < 0.3:
            return None

        is_predator = species in self.PREDATOR_SPECIES
        species_fi = self.SPECIES_FI.get(species, species)

        detection = BirdDetection(
            species=species,
            species_fi=species_fi,
            confidence=round(confidence, 3),
            is_predator=is_predator,
        )

        self._recent_detections.appendleft(detection)

        if is_predator:
            self._predator_alerts += 1
            log.warning("Predator detected: %s (%s) confidence=%.2f",
                        species, species_fi, confidence)

        return detection

    def get_recent_detections(self, limit: int = 20) -> list:
        """Get recent bird detections.

        Returns:
            List of detection dicts
        """
        detections = list(self._recent_detections)[:limit]
        return [
            {
                "species": d.species,
                "species_fi": d.species_fi,
                "confidence": d.confidence,
                "timestamp": d.timestamp,
                "is_predator": d.is_predator,
            }
            for d in detections
        ]

    @property
    def stats(self) -> dict:
        return {
            "enabled": self._enabled,
            "model_loaded": self._model_loaded,
            "model_name": self._model_name,
            "total_classifications": self._total_classifications,
            "predator_alerts": self._predator_alerts,
            "recent_count": len(self._recent_detections),
        }
