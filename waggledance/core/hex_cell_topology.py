"""
Logical hex-cell topology for hybrid retrieval.

Provides deterministic domain-based cell assignment with bounded neighbor lists.
Each cell represents a knowledge domain; neighbors are adjacent domains that
share conceptual overlap.

This is a LOGICAL overlay only — no visual hex grid.
Cells map queries to local FAISS indices for fast retrieval before
falling back to global ChromaDB.

Cell assignment rules (deterministic, no ML clustering):
  - Intent-based: math queries → math cell, thermal → thermal cell, etc.
  - Keyword fallback: domain keywords checked if intent is "chat"
  - Default: general cell for unclassified queries

Neighbor topology (ring-1 adjacency):
  Each cell has 2-4 neighbors based on domain overlap.
  Ring-2 (neighbor-of-neighbor) is computed but bounded.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Set

log = logging.getLogger(__name__)


# ── Cell definitions ──────────────────────────────────────────

CELL_GENERAL = "general"
CELL_THERMAL = "thermal"
CELL_ENERGY = "energy"
CELL_SAFETY = "safety"
CELL_SEASONAL = "seasonal"
CELL_MATH = "math"
CELL_SYSTEM = "system"
CELL_LEARNING = "learning"

ALL_CELLS: List[str] = [
    CELL_GENERAL,
    CELL_THERMAL,
    CELL_ENERGY,
    CELL_SAFETY,
    CELL_SEASONAL,
    CELL_MATH,
    CELL_SYSTEM,
    CELL_LEARNING,
]

# ── Ring-1 neighbor adjacency (bidirectional) ─────────────────
# Each entry: cell -> set of ring-1 neighbors
# Rationale:
#   thermal <-> energy (heating costs), thermal <-> seasonal (temp varies by season)
#   energy <-> safety (grid overload), energy <-> math (cost calculations)
#   safety <-> system (system health), safety <-> general (general advice)
#   seasonal <-> general (general knowledge), seasonal <-> learning (seasonal patterns)
#   math <-> general (general calculations), math <-> system (resource math)
#   learning <-> system (model metrics), learning <-> general (knowledge)

_ADJACENCY: Dict[str, FrozenSet[str]] = {
    CELL_GENERAL:  frozenset({CELL_SAFETY, CELL_SEASONAL, CELL_MATH, CELL_LEARNING}),
    CELL_THERMAL:  frozenset({CELL_ENERGY, CELL_SEASONAL, CELL_SAFETY}),
    CELL_ENERGY:   frozenset({CELL_THERMAL, CELL_SAFETY, CELL_MATH}),
    CELL_SAFETY:   frozenset({CELL_THERMAL, CELL_ENERGY, CELL_SYSTEM, CELL_GENERAL}),
    CELL_SEASONAL: frozenset({CELL_THERMAL, CELL_GENERAL, CELL_LEARNING}),
    CELL_MATH:     frozenset({CELL_ENERGY, CELL_GENERAL, CELL_SYSTEM}),
    CELL_SYSTEM:   frozenset({CELL_SAFETY, CELL_MATH, CELL_LEARNING}),
    CELL_LEARNING: frozenset({CELL_SEASONAL, CELL_GENERAL, CELL_SYSTEM}),
}

# ── Intent → cell mapping ────────────────────────────────────

_INTENT_TO_CELL: Dict[str, str] = {
    "math": CELL_MATH,
    "thermal": CELL_THERMAL,
    "optimization": CELL_ENERGY,
    "seasonal": CELL_SEASONAL,
    "constraint": CELL_SAFETY,
    "stats": CELL_SYSTEM,
    "statistical": CELL_SYSTEM,
    "symbolic": CELL_MATH,
    "causal": CELL_GENERAL,
    "anomaly": CELL_SYSTEM,
    "retrieval": CELL_GENERAL,
    "chat": CELL_GENERAL,
}

# ── Keyword-based fallback (checked only when intent="chat") ──

_DOMAIN_KEYWORDS: Dict[str, List[str]] = {
    CELL_THERMAL: [
        "temperature", "lämpötila", "heat", "frost", "pakkanen",
        "celsius", "fahrenheit", "heating", "cooling", "lämmitys",
    ],
    CELL_ENERGY: [
        "energy", "energia", "power", "teho", "watt", "kilowatt",
        "electricity", "sähkö", "grid", "solar", "battery",
    ],
    CELL_SAFETY: [
        "safety", "turvallisuus", "alarm", "hälytys", "fire", "tulipalo",
        "smoke", "savu", "violation", "risk", "riski", "danger",
    ],
    CELL_SEASONAL: [
        "season", "vuodenaika", "spring", "kevät", "summer", "kesä",
        "autumn", "syksy", "winter", "talvi", "month", "kuukausi",
    ],
    CELL_MATH: [
        "calculate", "laske", "formula", "kaava", "equation", "yhtälö",
        "percent", "prosentti", "multiply", "divide",
    ],
    CELL_SYSTEM: [
        "system", "järjestelmä", "cpu", "memory", "muisti", "disk",
        "process", "status", "tila", "health", "uptime",
    ],
    CELL_LEARNING: [
        "learn", "oppi", "train", "koulut", "model", "malli",
        "specialist", "dream", "night", "yö",
    ],
}


@dataclass
class CellAssignment:
    """Result of assigning a query to a hex cell."""
    cell_id: str
    intent: str
    method: str  # "intent" | "keyword" | "default"
    neighbors_ring1: List[str] = field(default_factory=list)
    neighbors_ring2: List[str] = field(default_factory=list)

    def all_neighbor_cells(self) -> List[str]:
        """Return ring-1 + ring-2 neighbors (deduplicated, excludes self)."""
        seen: Set[str] = set()
        result: List[str] = []
        for c in self.neighbors_ring1 + self.neighbors_ring2:
            if c not in seen and c != self.cell_id:
                seen.add(c)
                result.append(c)
        return result


class HexCellTopology:
    """Deterministic hex-cell topology for hybrid retrieval.

    Maps queries to logical cells, provides neighbor lookups,
    and supports ring-1 and ring-2 traversal.
    """

    def __init__(self) -> None:
        self._adjacency = dict(_ADJACENCY)
        self._stats = {
            "assignments": 0,
            "by_intent": 0,
            "by_keyword": 0,
            "by_default": 0,
            "cell_counts": {c: 0 for c in ALL_CELLS},
        }

    def assign_cell(self, intent: str, query: str = "") -> CellAssignment:
        """Assign a query to a hex cell based on intent and keywords.

        Priority:
          1. Direct intent mapping (from SolverRouter.classify_intent)
          2. Keyword scan (if intent is "chat" or unmapped)
          3. Default to CELL_GENERAL
        """
        cell_id: Optional[str] = None
        method = "default"

        # 1. Intent mapping
        if intent in _INTENT_TO_CELL:
            cell_id = _INTENT_TO_CELL[intent]
            method = "intent"

        # 2. Keyword fallback for chat/general intents
        if cell_id is None or cell_id == CELL_GENERAL:
            kw_cell = self._keyword_scan(query)
            if kw_cell is not None:
                cell_id = kw_cell
                method = "keyword"

        # 3. Default
        if cell_id is None:
            cell_id = CELL_GENERAL
            method = "default"

        # Neighbors
        ring1 = sorted(self._adjacency.get(cell_id, frozenset()))
        ring2 = self._ring2(cell_id)

        # Stats
        self._stats["assignments"] += 1
        self._stats[f"by_{method}"] += 1
        self._stats["cell_counts"][cell_id] = self._stats["cell_counts"].get(cell_id, 0) + 1

        return CellAssignment(
            cell_id=cell_id,
            intent=intent,
            method=method,
            neighbors_ring1=ring1,
            neighbors_ring2=ring2,
        )

    def get_neighbors(self, cell_id: str, max_ring: int = 1) -> List[str]:
        """Get neighbor cell IDs up to max_ring hops."""
        ring1 = sorted(self._adjacency.get(cell_id, frozenset()))
        if max_ring <= 1:
            return ring1
        ring2 = self._ring2(cell_id)
        return ring1 + ring2

    def stats(self) -> dict:
        return dict(self._stats)

    # ── Internal ──────────────────────────────────────────────

    def _keyword_scan(self, query: str) -> Optional[str]:
        """Scan query for domain keywords. Returns best-matching cell or None."""
        if not query:
            return None
        q = query.lower()
        best_cell: Optional[str] = None
        best_count = 0
        for cell_id, keywords in _DOMAIN_KEYWORDS.items():
            count = sum(1 for kw in keywords if kw in q)
            if count > best_count:
                best_count = count
                best_cell = cell_id
        return best_cell if best_count > 0 else None

    def _ring2(self, cell_id: str) -> List[str]:
        """Compute ring-2 neighbors (neighbor-of-neighbor, excluding self and ring-1)."""
        ring1 = self._adjacency.get(cell_id, frozenset())
        ring2: Set[str] = set()
        for n in ring1:
            for nn in self._adjacency.get(n, frozenset()):
                if nn != cell_id and nn not in ring1:
                    ring2.add(nn)
        return sorted(ring2)
