"""Tier manager — Phase 9 §L.

Owns tier transitions across hot/warm/cold/glacier. Pinned (foundational)
nodes are PROTECTED from cold/glacier demotion. Invariants are
extracted before any deep tiering so glacier-stored bodies don't lose
their structural meaning.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import (
    MEMORY_TIERING_SCHEMA_VERSION,
    PROMOTABLE_ANCHOR_STATUSES_PINNED,
    TIERS,
)
from .access_pattern_tracker import AccessPatternTracker
from .cold_tier import ColdTier
from .glacier_tier import GlacierTier
from .hot_tier import HotTier
from .invariant_extractor import (
    ExtractedInvariant,
    InvariantStore,
    extract_invariants,
)
from .pinning_engine import PinningEngine
from .warm_tier import WarmTier


@dataclass(frozen=True)
class TierAssignment:
    schema_version: int
    node_id: str
    tier: str
    access_count: int
    last_access_iso: str
    last_promoted_iso: str | None
    last_demoted_iso: str | None
    pinned: bool
    pin_reason: str | None
    anchor_status: str | None
    invariant_count: int

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "node_id": self.node_id,
            "tier": self.tier,
            "access_count": self.access_count,
            "last_access_iso": self.last_access_iso,
            "last_promoted_iso": self.last_promoted_iso,
            "last_demoted_iso": self.last_demoted_iso,
            "pinned": self.pinned,
            "pin_reason": self.pin_reason,
            "anchor_status": self.anchor_status,
            "invariant_count": self.invariant_count,
        }


class TierViolation(ValueError):
    """Raised on attempts to violate the foundational pin rule."""


@dataclass
class TierManager:
    hot: HotTier = field(default_factory=HotTier)
    warm: WarmTier = field(default_factory=WarmTier)
    cold: ColdTier = field(default_factory=ColdTier)
    glacier: GlacierTier = field(default_factory=GlacierTier)
    access: AccessPatternTracker = field(default_factory=AccessPatternTracker)
    pins: PinningEngine = field(default_factory=PinningEngine)
    invariants: InvariantStore = field(default_factory=InvariantStore)
    assignments: dict[str, TierAssignment] = field(default_factory=dict)

    def _tier_obj(self, tier: str):
        return {"hot": self.hot, "warm": self.warm,
                "cold": self.cold, "glacier": self.glacier}[tier]

    def assign(self, *, node_id: str, payload: dict, tier: str,
                  ts_iso: str,
                  anchor_status: str | None = None) -> TierAssignment:
        """Place a node in a tier. Auto-pins foundational anchors."""
        if tier not in TIERS:
            raise TierViolation(f"unknown tier: {tier!r}")
        # Auto-pin foundational anchors
        if anchor_status == "foundational":
            self.pins.pin(node_id=node_id,
                            reason="foundational_anchor_auto_pin",
                            anchor_status=anchor_status)
        # Extract invariants BEFORE deep tiering
        invs = extract_invariants(node_id=node_id, payload=payload)
        for inv in invs:
            self.invariants.add(inv)
        # Pinned nodes refused for cold/glacier
        if self.pins.is_pinned(node_id) and tier in ("cold", "glacier"):
            raise TierViolation(
                f"pinned node {node_id!r} cannot demote to {tier!r}"
            )
        # Place in tier
        self._tier_obj(tier).put(node_id, payload)
        a = self.assignments.get(node_id)
        ass = TierAssignment(
            schema_version=MEMORY_TIERING_SCHEMA_VERSION,
            node_id=node_id, tier=tier,
            access_count=a.access_count if a else 0,
            last_access_iso=ts_iso,
            last_promoted_iso=ts_iso if (
                a and TIERS.index(tier) < TIERS.index(a.tier)
            ) else (a.last_promoted_iso if a else None),
            last_demoted_iso=ts_iso if (
                a and TIERS.index(tier) > TIERS.index(a.tier)
            ) else (a.last_demoted_iso if a else None),
            pinned=self.pins.is_pinned(node_id),
            pin_reason=(self.pins.pins.get(node_id).pin_reason
                          if self.pins.pins.get(node_id) else None),
            anchor_status=anchor_status,
            invariant_count=len(invs),
        )
        self.assignments[node_id] = ass
        return ass

    def record_access(self, node_id: str, ts_iso: str) -> None:
        self.access.record_access(node_id, ts_iso)
        a = self.assignments.get(node_id)
        if a is not None:
            self.assignments[node_id] = TierAssignment(
                schema_version=a.schema_version, node_id=a.node_id,
                tier=a.tier,
                access_count=a.access_count + 1,
                last_access_iso=ts_iso,
                last_promoted_iso=a.last_promoted_iso,
                last_demoted_iso=a.last_demoted_iso,
                pinned=a.pinned, pin_reason=a.pin_reason,
                anchor_status=a.anchor_status,
                invariant_count=a.invariant_count,
            )

    def promote(self, node_id: str, target_tier: str,
                  ts_iso: str) -> TierAssignment:
        """Move node to a hotter tier. Pinned-status preserved."""
        if target_tier not in TIERS:
            raise TierViolation(f"unknown tier: {target_tier!r}")
        a = self.assignments.get(node_id)
        if a is None:
            raise TierViolation(f"unknown node: {node_id!r}")
        if TIERS.index(target_tier) >= TIERS.index(a.tier):
            raise TierViolation(
                f"promote target {target_tier!r} is not hotter than "
                f"current {a.tier!r}"
            )
        # Move payload
        payload = self._tier_obj(a.tier).evict(node_id)
        if payload is None:
            payload = {}
        self._tier_obj(target_tier).put(node_id, payload)
        new = TierAssignment(
            schema_version=a.schema_version, node_id=node_id,
            tier=target_tier,
            access_count=a.access_count, last_access_iso=ts_iso,
            last_promoted_iso=ts_iso,
            last_demoted_iso=a.last_demoted_iso,
            pinned=a.pinned, pin_reason=a.pin_reason,
            anchor_status=a.anchor_status,
            invariant_count=a.invariant_count,
        )
        self.assignments[node_id] = new
        return new

    def demote(self, node_id: str, target_tier: str,
                  ts_iso: str) -> TierAssignment:
        if target_tier not in TIERS:
            raise TierViolation(f"unknown tier: {target_tier!r}")
        a = self.assignments.get(node_id)
        if a is None:
            raise TierViolation(f"unknown node: {node_id!r}")
        if a.pinned and target_tier in ("cold", "glacier"):
            raise TierViolation(
                f"pinned node {node_id!r} cannot demote to {target_tier!r}"
            )
        if TIERS.index(target_tier) <= TIERS.index(a.tier):
            raise TierViolation(
                f"demote target {target_tier!r} is not colder than "
                f"current {a.tier!r}"
            )
        payload = self._tier_obj(a.tier).evict(node_id)
        if payload is None:
            payload = {}
        self._tier_obj(target_tier).put(node_id, payload)
        new = TierAssignment(
            schema_version=a.schema_version, node_id=node_id,
            tier=target_tier,
            access_count=a.access_count, last_access_iso=ts_iso,
            last_promoted_iso=a.last_promoted_iso,
            last_demoted_iso=ts_iso,
            pinned=a.pinned, pin_reason=a.pin_reason,
            anchor_status=a.anchor_status,
            invariant_count=a.invariant_count,
        )
        self.assignments[node_id] = new
        return new

    def fetch(self, node_id: str) -> dict | None:
        a = self.assignments.get(node_id)
        if a is None:
            return None
        return self._tier_obj(a.tier).get(node_id)

    def deterministic_rebuild(self) -> "TierManager":
        """Reproduce the same tier layout from scratch using the
        recorded assignments. Used by tools/rebuild_memory_tiers.
        Returns a new TierManager; caller may compare snapshots for
        byte-identity."""
        new_mgr = TierManager()
        new_mgr.invariants = self.invariants
        new_mgr.pins = self.pins
        new_mgr.access = self.access
        for nid in sorted(self.assignments):
            a = self.assignments[nid]
            payload = self._tier_obj(a.tier).get(nid) or {}
            new_mgr._tier_obj(a.tier).put(nid, payload)
            new_mgr.assignments[nid] = a
        return new_mgr

    def tier_snapshot(self) -> dict:
        return {
            "schema_version": MEMORY_TIERING_SCHEMA_VERSION,
            "tier_sizes": {
                "hot": self.hot.size(),
                "warm": self.warm.size(),
                "cold": self.cold.size(),
                "glacier": self.glacier.size(),
            },
            "assignments": {nid: a.to_dict()
                              for nid, a in sorted(self.assignments.items())},
            "invariants": self.invariants.to_dict(),
            "pins": self.pins.to_dict(),
        }
