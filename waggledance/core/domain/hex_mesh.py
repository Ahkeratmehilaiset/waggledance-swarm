"""Hex Neighbor Mesh — domain types.

Axial-coordinate honeycomb topology for local-first cooperative resolution.
Cells cluster agents by domain; queries route: local → neighbor → global → LLM.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


# ── Axial coordinates ────────────────────────────────────────────

AXIAL_DIRECTIONS = [(1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1)]


@dataclass(frozen=True, slots=True)
class HexCoord:
    """Axial hex coordinate (q, r)."""

    q: int
    r: int

    def neighbors(self) -> list[HexCoord]:
        return [HexCoord(self.q + dq, self.r + dr) for dq, dr in AXIAL_DIRECTIONS]

    def distance(self, other: HexCoord) -> int:
        dq = self.q - other.q
        dr = self.r - other.r
        return (abs(dq) + abs(dq + dr) + abs(dr)) // 2

    def ring(self, radius: int) -> list[HexCoord]:
        if radius == 0:
            return [self]
        results: list[HexCoord] = []
        cur = HexCoord(self.q - radius, self.r + radius)
        for d, (dq, dr) in enumerate(AXIAL_DIRECTIONS):
            for _ in range(radius):
                results.append(cur)
                cur = HexCoord(cur.q + dq, cur.r + dr)
        return results

    def is_adjacent(self, other: HexCoord) -> bool:
        return self.distance(other) == 1


# ── Cell definitions ─────────────────────────────────────────────

@dataclass
class HexCellDefinition:
    """One cell in the hex topology."""

    id: str
    coord: HexCoord
    description: str = ""
    domain_selectors: list[str] = field(default_factory=list)
    tag_selectors: list[str] = field(default_factory=list)
    enabled: bool = True
    neighbor_policy: str = "default"


@dataclass
class HexCellHealth:
    """Runtime health state of a cell."""

    cell_id: str
    recent_error_count: int = 0
    recent_timeout_count: int = 0
    recent_success_count: int = 0
    total_queries: int = 0
    last_success_ts: float = 0.0
    quarantine_until: float = 0.0
    cooldown_probe_pending: bool = False

    @property
    def is_quarantined(self) -> bool:
        return self.quarantine_until > time.time()

    @property
    def health_score(self) -> float:
        total = self.recent_success_count + self.recent_error_count + self.recent_timeout_count
        if total == 0:
            return 1.0
        return self.recent_success_count / total


# ── Request/response types ───────────────────────────────────────

@dataclass
class HexNeighborRequest:
    """Request sent to a neighbor cell for assistance."""

    trace_id: str
    query: str
    origin_cell_id: str
    requesting_cell_id: str
    ttl: int = 2
    visited: frozenset[str] = field(default_factory=frozenset)
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class HexNeighborResponse:
    """Response from a neighbor cell."""

    cell_id: str
    response: str
    confidence: float
    latency_ms: float
    model_used: str = ""
    source: str = "neighbor"
    error: str = ""

    @property
    def is_success(self) -> bool:
        return not self.error and self.confidence > 0


# ── Resolution planning ─────────────────────────────────────────

@dataclass
class HexResolutionPlan:
    """Plan for resolving a query via hex mesh."""

    trace_id: str
    origin_cell_id: str
    query: str
    local_threshold: float = 0.72
    neighbor_threshold: float = 0.82
    global_threshold: float = 0.90
    ttl: int = 2
    max_neighbors: int = 2
    selected_neighbors: list[str] = field(default_factory=list)


@dataclass
class HexResolutionTrace:
    """Full provenance trace for a hex query resolution."""

    trace_id: str
    origin_cell_id: str
    query: str
    started_at: float = 0.0
    completed_at: float = 0.0

    # Stage results
    local_confidence: float = 0.0
    local_response: str = ""
    local_latency_ms: float = 0.0

    neighbor_cells_consulted: list[str] = field(default_factory=list)
    neighbor_responses: list[HexNeighborResponse] = field(default_factory=list)
    neighbor_latency_ms: float = 0.0

    merged_confidence: float = 0.0
    merged_response: str = ""

    escalated_global: bool = False
    escalated_llm: bool = False
    global_confidence: float = 0.0
    llm_confidence: float = 0.0

    # Final
    final_response: str = ""
    final_confidence: float = 0.0
    final_source: str = ""
    total_latency_ms: float = 0.0

    # Thresholds used
    local_threshold: float = 0.72
    neighbor_threshold: float = 0.82

    # Cache
    cache_hit: bool = False

    # Models
    models_used: list[str] = field(default_factory=list)

    # TTL path
    ttl_path: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "origin_cell_id": self.origin_cell_id,
            "query_len": len(self.query),
            "local_confidence": self.local_confidence,
            "local_latency_ms": self.local_latency_ms,
            "neighbor_cells": self.neighbor_cells_consulted,
            "neighbor_latency_ms": self.neighbor_latency_ms,
            "merged_confidence": self.merged_confidence,
            "escalated_global": self.escalated_global,
            "escalated_llm": self.escalated_llm,
            "final_confidence": self.final_confidence,
            "final_source": self.final_source,
            "total_latency_ms": self.total_latency_ms,
            "ttl_path": self.ttl_path,
            "models_used": self.models_used,
            "cache_hit": self.cache_hit,
        }
