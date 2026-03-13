"""
Domain Capsule — profile-based reasoning layer configuration.

Each capsule defines which reasoning layers are available (rules, models,
statistics, retrieval, LLM) and maps key decisions to their primary layer.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

log = logging.getLogger(__name__)

VALID_LAYERS = frozenset([
    "rule_constraints", "model_based", "statistical",
    "retrieval", "llm_reasoning",
])

REQUIRED_CAPSULE_FIELDS = {"domain", "version", "layers", "key_decisions"}

CAPSULE_DIR = Path(__file__).resolve().parent.parent / "configs" / "capsules"


@dataclass
class DecisionMatch:
    """Result of matching a query to a key decision."""
    decision_id: str
    layer: str
    confidence: float
    model: Optional[str] = None
    rules: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    fallback: Optional[str] = None


@dataclass
class LayerConfig:
    """Configuration for a single reasoning layer."""
    name: str
    enabled: bool
    priority: int


class DomainCapsule:
    """Loads and validates a domain capsule YAML, provides query matching."""

    def __init__(self, data: dict[str, Any], source_path: Optional[str] = None):
        self._raw = data
        self._source = source_path
        self._validate(data)
        self.domain: str = data["domain"]
        self.version: str = str(data["version"])
        self.description: str = data.get("description", "")
        self.layers: list[LayerConfig] = self._parse_layers(data["layers"])
        self.key_decisions: list[dict] = data.get("key_decisions", [])
        self.rules: list[dict] = data.get("rules", [])
        self.models: list[dict] = data.get("models", [])
        self.data_sources: list[dict] = data.get("data_sources", [])
        # Pre-compile keyword patterns for fast matching
        self._decision_keywords: list[tuple[dict, list[re.Pattern]]] = []
        for dec in self.key_decisions:
            patterns = []
            for kw in dec.get("keywords", []):
                patterns.append(re.compile(r'\b' + re.escape(kw.lower())))
            self._decision_keywords.append((dec, patterns))

    # ── Factory methods ──────────────────────────────────────

    @classmethod
    def load(cls, profile: str, capsule_dir: Optional[Path] = None
             ) -> "DomainCapsule":
        """Load a capsule YAML by profile name (gadget/cottage/home/factory)."""
        d = capsule_dir or CAPSULE_DIR
        path = d / f"{profile}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"Capsule not found: {path}")
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        log.info("Loaded domain capsule: %s from %s", profile, path)
        return cls(data, source_path=str(path))

    @classmethod
    def load_from_settings(cls, settings_path: Optional[Path] = None
                           ) -> "DomainCapsule":
        """Load the capsule matching the active profile in settings.yaml."""
        sp = settings_path or (
            Path(__file__).resolve().parent.parent / "configs" / "settings.yaml")
        with open(sp, "r", encoding="utf-8") as f:
            settings = yaml.safe_load(f)
        profile = settings.get("profile", "cottage")
        return cls.load(profile)

    # ── Validation ───────────────────────────────────────────

    @staticmethod
    def _validate(data: dict) -> None:
        """Validate capsule schema — raises ValueError on bad input."""
        missing = REQUIRED_CAPSULE_FIELDS - set(data.keys())
        if missing:
            raise ValueError(f"Capsule missing required fields: {missing}")
        layers = data.get("layers", {})
        for name in layers:
            if name not in VALID_LAYERS:
                raise ValueError(
                    f"Unknown layer '{name}'. Valid: {sorted(VALID_LAYERS)}")
        for dec in data.get("key_decisions", []):
            if "id" not in dec:
                raise ValueError(f"Key decision missing 'id': {dec}")
            pl = dec.get("primary_layer")
            if pl and pl not in VALID_LAYERS:
                raise ValueError(
                    f"Decision '{dec['id']}' has invalid primary_layer: {pl}")

    # ── Layer queries ────────────────────────────────────────

    def get_layers_by_priority(self) -> list[LayerConfig]:
        """Return enabled layers sorted by priority (lowest = highest priority)."""
        return sorted(
            [lc for lc in self.layers if lc.enabled],
            key=lambda lc: lc.priority)

    def is_layer_enabled(self, layer_name: str) -> bool:
        """Check if a specific layer is enabled."""
        return any(lc.name == layer_name and lc.enabled for lc in self.layers)

    # ── Decision matching ────────────────────────────────────

    def match_decision(self, query: str) -> Optional[DecisionMatch]:
        """Match a user query to the best key decision by keyword overlap."""
        q_lower = query.lower()
        best: Optional[tuple[int, dict]] = None
        for dec, patterns in self._decision_keywords:
            hits = sum(1 for p in patterns if p.search(q_lower))
            if hits > 0 and (best is None or hits > best[0]):
                best = (hits, dec)
        if best is None:
            return None
        hits, dec = best
        confidence = min(1.0, hits / max(len(dec.get("keywords", [])), 1))
        return DecisionMatch(
            decision_id=dec["id"],
            layer=dec.get("primary_layer", "llm_reasoning"),
            confidence=confidence,
            model=dec.get("model"),
            rules=dec.get("rules", []),
            inputs=dec.get("inputs", []),
            fallback=dec.get("fallback"),
        )

    def get_layer_for_query(self, query: str) -> str:
        """Return the best reasoning layer for a query.

        Tries key_decision match first, then falls back to highest-priority
        enabled layer.
        """
        match = self.match_decision(query)
        if match and match.confidence >= 0.2:
            if self.is_layer_enabled(match.layer):
                return match.layer
            # Primary layer disabled — use fallback or priority
            if match.fallback and self.is_layer_enabled(match.fallback):
                return match.fallback
        # No match — return highest priority enabled layer
        ordered = self.get_layers_by_priority()
        return ordered[0].name if ordered else "llm_reasoning"

    # ── Serialization ────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Serialize capsule for API responses."""
        return {
            "domain": self.domain,
            "version": self.version,
            "description": self.description,
            "layers": [
                {"name": lc.name, "enabled": lc.enabled, "priority": lc.priority}
                for lc in self.layers
            ],
            "key_decisions": [
                {"id": d["id"],
                 "question": d.get("question", ""),
                 "primary_layer": d.get("primary_layer", ""),
                 "keywords": d.get("keywords", [])}
                for d in self.key_decisions
            ],
            "rules_count": len(self.rules),
            "models_count": len(self.models),
            "data_sources_count": len(self.data_sources),
        }

    # ── Internal ─────────────────────────────────────────────

    @staticmethod
    def _parse_layers(layers_dict: dict) -> list[LayerConfig]:
        result = []
        for name, cfg in layers_dict.items():
            result.append(LayerConfig(
                name=name,
                enabled=cfg.get("enabled", True),
                priority=cfg.get("priority", 99),
            ))
        return result

    def __repr__(self) -> str:
        enabled = [lc.name for lc in self.layers if lc.enabled]
        return (f"DomainCapsule(domain={self.domain!r}, "
                f"layers={enabled}, decisions={len(self.key_decisions)})")
