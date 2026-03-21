"""
Domain Model Miner — assists human domain experts in building axiom YAMLs.

Stub implementation with three components:
- SchemaInspector: analyzes data sources for units, column types, ranges
- DocumentReader: extracts patterns from SOPs, FMEAs, and manuals
- LayerRecommender: scores which reasoning layer fits each decision

This is a human-in-the-loop tool: AI suggests, human validates and refines.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)


@dataclass
class ColumnInfo:
    """Metadata about a data column/field."""
    name: str
    dtype: str = "unknown"     # float, int, str, bool, datetime
    unit: str = ""
    sample_values: list[Any] = field(default_factory=list)
    range_min: Optional[float] = None
    range_max: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "dtype": self.dtype,
            "unit": self.unit,
            "range": [self.range_min, self.range_max]
                     if self.range_min is not None else None,
        }


@dataclass
class DocumentPattern:
    """A pattern extracted from a document (SOP, FMEA, manual)."""
    pattern_type: str   # "threshold", "formula", "condition", "procedure"
    text: str
    source: str = ""
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.pattern_type,
            "text": self.text,
            "source": self.source,
            "confidence": round(self.confidence, 2),
        }


@dataclass
class LayerScore:
    """Score for how well a decision fits a reasoning layer."""
    layer: str
    score: float
    reasons: list[str] = field(default_factory=list)


class SchemaInspector:
    """Analyzes data structures for units, types, and ranges."""

    # Common unit patterns
    _UNIT_PATTERNS = {
        r"(?:temp|temperature|lampotila)": "C",
        r"(?:kwh|energy|energia)": "kWh",
        r"(?:watt|power|teho)": "W",
        r"(?:price|hinta|cost|kustannus)": "EUR",
        r"(?:percent|pct|prosentti)": "%",
        r"(?:pressure|paine)": "Pa",
        r"(?:humidity|kosteus)": "%RH",
        r"(?:area|pinta)": "m2",
        r"(?:speed|nopeus)": "m/s",
        r"(?:voltage|jannite)": "V",
        r"(?:current|virta)": "A",
        r"(?:time|aika|duration|kesto)": "s",
    }

    def inspect_dict(self, data: dict[str, Any]) -> list[ColumnInfo]:
        """Inspect a flat dict and infer column metadata."""
        result = []
        for key, value in data.items():
            col = ColumnInfo(name=key)

            # Type detection
            if isinstance(value, bool):
                col.dtype = "bool"
            elif isinstance(value, int):
                col.dtype = "int"
            elif isinstance(value, float):
                col.dtype = "float"
            elif isinstance(value, str):
                col.dtype = "str"

            # Unit inference from key name
            for pattern, unit in self._UNIT_PATTERNS.items():
                if re.search(pattern, key, re.IGNORECASE):
                    col.unit = unit
                    break

            col.sample_values = [value]
            if isinstance(value, (int, float)):
                col.range_min = float(value)
                col.range_max = float(value)

            result.append(col)
        return result

    def inspect_rows(self, rows: list[dict[str, Any]]) -> list[ColumnInfo]:
        """Inspect a list of row dicts and aggregate metadata."""
        if not rows:
            return []

        # Collect all values per column
        columns: dict[str, list[Any]] = {}
        for row in rows:
            for key, val in row.items():
                columns.setdefault(key, []).append(val)

        result = []
        for key, values in columns.items():
            col = ColumnInfo(name=key)
            col.sample_values = values[:5]

            # Type from first non-None value
            for v in values:
                if v is not None:
                    if isinstance(v, bool):
                        col.dtype = "bool"
                    elif isinstance(v, int):
                        col.dtype = "int"
                    elif isinstance(v, float):
                        col.dtype = "float"
                    elif isinstance(v, str):
                        col.dtype = "str"
                    break

            # Range for numerics
            nums = [v for v in values if isinstance(v, (int, float))]
            if nums:
                col.range_min = float(min(nums))
                col.range_max = float(max(nums))

            # Unit inference
            for pattern, unit in self._UNIT_PATTERNS.items():
                if re.search(pattern, key, re.IGNORECASE):
                    col.unit = unit
                    break

            result.append(col)
        return result


class DocumentReader:
    """Extracts threshold, formula, and condition patterns from text documents."""

    _THRESHOLD_RE = re.compile(
        r"(?:threshold|raja|limit|max|min|alert)\s*[:=]?\s*(\d+(?:\.\d+)?)",
        re.IGNORECASE)

    _FORMULA_RE = re.compile(
        r"(?:formula|kaava|equation)\s*[:=]?\s*(.+?)(?:\n|$)",
        re.IGNORECASE)

    _CONDITION_RE = re.compile(
        r"(?:if|when|kun|jos)\s+(.+?)(?:then|niin|,|\n|$)",
        re.IGNORECASE)

    def extract_patterns(self, text: str,
                         source: str = "") -> list[DocumentPattern]:
        """Extract patterns from a text document."""
        patterns = []

        for m in self._THRESHOLD_RE.finditer(text):
            patterns.append(DocumentPattern(
                pattern_type="threshold",
                text=m.group(0).strip(),
                source=source,
                confidence=0.7,
            ))

        for m in self._FORMULA_RE.finditer(text):
            patterns.append(DocumentPattern(
                pattern_type="formula",
                text=m.group(1).strip(),
                source=source,
                confidence=0.5,
            ))

        for m in self._CONDITION_RE.finditer(text):
            patterns.append(DocumentPattern(
                pattern_type="condition",
                text=m.group(1).strip(),
                source=source,
                confidence=0.6,
            ))

        return patterns


class LayerRecommender:
    """Recommends which reasoning layer best fits a decision type."""

    # Keyword -> layer scoring
    _LAYER_KEYWORDS = {
        "rule_constraints": [
            "threshold", "limit", "alert", "safety", "check", "must",
            "forbidden", "allowed", "raja", "turvallisuus", "hälytys",
        ],
        "model_based": [
            "calculate", "formula", "cost", "predict", "estimate",
            "laske", "kaava", "hinta", "ennusta",
        ],
        "statistical": [
            "trend", "average", "anomaly", "baseline", "deviation",
            "normal", "trendi", "keskiarvo", "poikkeama",
        ],
        "retrieval": [
            "history", "previous", "lookup", "find", "search",
            "historia", "haku", "etsi",
        ],
        "llm_reasoning": [
            "explain", "why", "recommend", "suggest", "opinion",
            "selitä", "miksi", "suosittele",
        ],
    }

    def recommend(self, description: str,
                  keywords: Optional[list[str]] = None
                  ) -> list[LayerScore]:
        """Score all layers for a given decision description."""
        text = description.lower()
        if keywords:
            text += " " + " ".join(k.lower() for k in keywords)

        scores: list[LayerScore] = []
        for layer, layer_kws in self._LAYER_KEYWORDS.items():
            hits = sum(1 for kw in layer_kws if kw in text)
            score = min(1.0, hits / max(len(layer_kws), 1) * 3)
            reasons = [kw for kw in layer_kws if kw in text]
            scores.append(LayerScore(
                layer=layer, score=round(score, 2), reasons=reasons))

        scores.sort(key=lambda s: s.score, reverse=True)
        return scores

    def best_layer(self, description: str,
                   keywords: Optional[list[str]] = None) -> str:
        """Return the single best layer recommendation."""
        scores = self.recommend(description, keywords)
        return scores[0].layer if scores else "llm_reasoning"


class DomainModelMiner:
    """Orchestrator: combines inspector, reader, and recommender."""

    def __init__(self):
        self.inspector = SchemaInspector()
        self.reader = DocumentReader()
        self.recommender = LayerRecommender()

    def analyze_data(self, data: dict[str, Any]) -> list[dict]:
        """Analyze a data sample and return column metadata."""
        columns = self.inspector.inspect_dict(data)
        return [c.to_dict() for c in columns]

    def analyze_document(self, text: str,
                         source: str = "") -> list[dict]:
        """Extract patterns from a document."""
        patterns = self.reader.extract_patterns(text, source)
        return [p.to_dict() for p in patterns]

    def recommend_layer(self, description: str,
                        keywords: Optional[list[str]] = None
                        ) -> dict[str, Any]:
        """Recommend the best reasoning layer for a decision."""
        scores = self.recommender.recommend(description, keywords)
        return {
            "recommended": scores[0].layer if scores else "llm_reasoning",
            "scores": [
                {"layer": s.layer, "score": s.score, "reasons": s.reasons}
                for s in scores
            ],
        }
