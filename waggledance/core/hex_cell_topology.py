"""
Logical hex-cell topology for hybrid retrieval.

Provides deterministic domain-based cell assignment with bounded neighbor lists.
Each cell represents a knowledge domain; neighbors are adjacent domains that
share conceptual overlap.

This is a LOGICAL overlay only — no visual hex grid.
Cells map queries to local FAISS indices for fast retrieval before
falling back to global ChromaDB.

Cell assignment rules (deterministic, no ML clustering):
  - Safety override: high-confidence incident tokens preempt ordinary intents
  - Intent-based: math queries → math cell, thermal → thermal cell, etc.
  - Keyword fallback: other domain keywords checked for general/unmapped intents
  - Default: general cell for unclassified queries

Neighbor topology (ring-1 adjacency):
  Each cell has 2-4 neighbors based on domain overlap.
  Ring-2 (neighbor-of-neighbor) is computed but bounded.
"""

from __future__ import annotations

import logging
import re
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

# ── Keyword routing ──────────────────────────────────────────

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
        "safety", "turvallisuus", "alarm", "hälytys", "palovaroitin",
        "fire", "tulipalo", "smoke", "savu", "violation", "risk", "riski",
        "danger",
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

# These token sequences are deliberately narrower than the ordinary safety-cell
# vocabulary.  They may preempt an already classified intent, so each sequence
# must describe a high-confidence incident rather than an ambiguous domain
# word such as "risk", "smoke", or "alarm" on its own.
_SMOKE_DETECTION_TOKEN_SEQUENCES: tuple[tuple[str, ...], ...] = (
    ("smoke", "detected"),
    ("smoke", "was", "detected"),
    ("smoke", "has", "been", "detected"),
)
_ACTIVE_ALARM_SUFFIXES: tuple[tuple[str, ...], ...] = (
    ("going", "off"),
    ("is", "going", "off"),
    ("was", "going", "off"),
    ("has", "gone", "off"),
    ("went", "off"),
    ("sounding",),
    ("is", "sounding"),
    ("ringing",),
    ("is", "ringing"),
    ("beeping",),
    ("is", "beeping"),
    ("sounded",),
    ("activated",),
    ("has", "activated"),
    ("triggered",),
    ("has", "triggered"),
)
_ALARM_DESCRIPTOR_TOKENS: FrozenSet[str] = frozenset(
    {"detector", "device", "system"}
)
_PALOVAROITIN_ACTIVE_TOKENS: FrozenSet[str] = frozenset(
    {"hälyttää", "piippaa", "soi", "ulvoo"}
)
_NON_INCIDENT_ACTIVITY_TOKENS: FrozenSet[str] = frozenset(
    {
        "demo",
        "demonstration",
        "drill",
        "practice",
        "schedule",
        "scheduled",
        "simulated",
        "simulation",
        "test",
        "testing",
        "training",
    }
)
_NEGATION_TOKENS: FrozenSet[str] = frozenset(
    {"ei", "eikä", "älä", "never", "no", "not"}
)
_NON_NEGATING_NOT_FOLLOWERS: FrozenSet[str] = frozenset({"only"})
_NEGATION_LOOKBACK = 5
_MAX_FIRE_ALARM_TOKEN_DISTANCE = 8
_MAX_PALOVAROITIN_CUE_DISTANCE = 4
_FIRE_ALARM_CONTEXT_PADDING = 8
_WORD_TOKEN_RE = re.compile(r"\w+", flags=re.UNICODE)
_SENTENCE_BOUNDARY_RE = re.compile(r"[.!?\n]+")


def _keyword_matches(query: str, keyword: str) -> bool:
    pattern = rf"(?<!\w){re.escape(keyword.lower())}(?!\w)"
    return re.search(pattern, query) is not None


def _sequence_indexes(
    tokens: tuple[str, ...],
    sequence: tuple[str, ...],
) -> List[int]:
    return [
        index
        for index in range(len(tokens) - len(sequence) + 1)
        if tokens[index:index + len(sequence)] == sequence
    ]


def _context_tokens(
    tokens: tuple[str, ...],
    start: int,
    end: int,
) -> tuple[str, ...]:
    return tokens[
        max(0, start - _FIRE_ALARM_CONTEXT_PADDING):
        min(len(tokens), end + _FIRE_ALARM_CONTEXT_PADDING)
    ]


def _has_effective_negation(
    tokens: tuple[str, ...],
    start: int,
    end: int,
) -> bool:
    for index in range(max(0, start), min(len(tokens), end)):
        token = tokens[index]
        if token not in _NEGATION_TOKENS:
            continue
        if (
            token == "not"
            and index + 1 < len(tokens)
            and tokens[index + 1] in _NON_NEGATING_NOT_FOLLOWERS
        ):
            continue
        return True
    return False


def _is_negated(tokens: tuple[str, ...], index: int) -> bool:
    return _has_effective_negation(
        tokens,
        index - _NEGATION_LOOKBACK,
        index,
    )


def _has_unnegated_non_incident_activity(tokens: tuple[str, ...]) -> bool:
    return any(
        token in _NON_INCIDENT_ACTIVITY_TOKENS
        and not _is_negated(tokens, index)
        for index, token in enumerate(tokens)
    )


def _has_active_fire_alarm(tokens: tuple[str, ...]) -> bool:
    """Recognize bounded fire + active-alarm descriptions.

    Fire and alarm need not be adjacent, but they must remain close and the
    alarm must have an explicit activation cue.  Drill/test vocabulary in the
    same local window keeps simulated activity on its typed route.
    """
    for alarm_index, token in enumerate(tokens):
        if token != "alarm":
            continue
        cue_index = alarm_index + 1
        if (
            cue_index < len(tokens)
            and tokens[cue_index] in _ALARM_DESCRIPTOR_TOKENS
        ):
            cue_index += 1
        suffix_end = None
        for suffix in _ACTIVE_ALARM_SUFFIXES:
            suffix_end = cue_index + len(suffix)
            if tokens[cue_index:suffix_end] == suffix:
                break
        else:
            continue

        # Scan only the fixed local window.  This makes the matcher O(n) even
        # for adversarial input containing thousands of fire/alarm tokens.
        fire_start = max(0, alarm_index - _MAX_FIRE_ALARM_TOKEN_DISTANCE)
        fire_end = min(len(tokens), alarm_index + _MAX_FIRE_ALARM_TOKEN_DISTANCE + 1)
        for fire_index in range(fire_start, fire_end):
            if tokens[fire_index] != "fire":
                continue
            if tokens[fire_index + 1:fire_index + 2] == ("sale",):
                continue
            context = _context_tokens(
                tokens,
                min(fire_index, alarm_index),
                max(fire_index + 1, suffix_end),
            )
            if not _has_unnegated_non_incident_activity(context):
                return True
    return False


def _has_active_palovaroitin(tokens: tuple[str, ...]) -> bool:
    for alarm_index, token in enumerate(tokens):
        if token != "palovaroitin":
            continue
        cue_end = min(
            len(tokens),
            alarm_index + _MAX_PALOVAROITIN_CUE_DISTANCE + 1,
        )
        for cue_index in range(alarm_index + 1, cue_end):
            if tokens[cue_index] not in _PALOVAROITIN_ACTIVE_TOKENS:
                continue
            context = _context_tokens(tokens, alarm_index, cue_index + 1)
            if (
                not _has_effective_negation(
                    tokens,
                    alarm_index - 1,
                    cue_index,
                )
                and not _has_unnegated_non_incident_activity(context)
            ):
                return True
    return False


def _has_safety_override(query: str) -> bool:
    if not query:
        return False
    # Keep anchors within one sentence-like segment so unrelated statements
    # cannot combine into an incident.  Commas and semicolons deliberately do
    # not split: natural incident descriptions often use either.
    for segment in _SENTENCE_BOUNDARY_RE.split(query.lower()):
        tokens = tuple(_WORD_TOKEN_RE.findall(segment))
        if not tokens:
            continue
        for sequence in _SMOKE_DETECTION_TOKEN_SEQUENCES:
            for index in _sequence_indexes(tokens, sequence):
                context = _context_tokens(
                    tokens,
                    index,
                    index + len(sequence),
                )
                if (
                    not _is_negated(tokens, index)
                    and not _has_unnegated_non_incident_activity(context)
                ):
                    return True
        if _has_active_palovaroitin(tokens) or _has_active_fire_alarm(tokens):
            return True
    return False


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
          1. High-confidence safety incident override
          2. Direct intent mapping (from SolverRouter.classify_intent)
          3. Keyword scan (if intent is general or unmapped)
          4. Default to CELL_GENERAL
        """
        cell_id: Optional[str] = None
        method = "default"

        # 1. High-confidence incidents must not be hidden by an ordinary
        # inferred intent.  Ambiguous safety-domain words remain part of the
        # ordinary general-intent keyword scan below.
        if _has_safety_override(query):
            cell_id = CELL_SAFETY
            method = "keyword"

        # 2. Intent mapping
        elif intent in _INTENT_TO_CELL:
            cell_id = _INTENT_TO_CELL[intent]
            method = "intent"

        # 3. Keyword fallback for general or unmapped intents
        if cell_id is None or cell_id == CELL_GENERAL:
            keyword_cell = self._keyword_scan(query)
            if keyword_cell is not None:
                cell_id = keyword_cell
                method = "keyword"

        # 4. Default
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
            count = sum(1 for kw in keywords if _keyword_matches(q, kw))
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
