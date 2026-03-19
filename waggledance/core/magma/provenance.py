# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Provenance adapter — wraps legacy ProvenanceTracker with tiered source types.

Extends L4 provenance to distinguish between observed, inferred_by_solver,
inferred_by_stats, inferred_by_rule, proposed_by_llm, confirmed_by_verifier,
and learned_from_case source types.
"""

from __future__ import annotations

import logging
import time
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_ProvenanceTracker = None


def _get_provenance_class():
    global _ProvenanceTracker
    if _ProvenanceTracker is None:
        try:
            from core.provenance import ProvenanceTracker
            _ProvenanceTracker = ProvenanceTracker
        except ImportError:
            _ProvenanceTracker = None
    return _ProvenanceTracker


VALID_SOURCE_TYPES = frozenset({
    "observed",
    "inferred_by_solver",
    "inferred_by_stats",
    "inferred_by_rule",
    "proposed_by_llm",
    "confirmed_by_verifier",
    "learned_from_case",
    # v3.2 MAGMA expansion
    "self_reflection",
    "simulated",
})


@dataclass
class ProvenanceRecord:
    """Extended provenance record with tiered source type."""
    fact_id: str
    source_type: str
    canonical_id: str = ""
    capability_id: str = ""
    quality_grade: str = ""
    confidence: float = 0.0
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def trust_weight(self) -> float:
        """Return trust weight based on source type hierarchy."""
        weights = {
            "confirmed_by_verifier": 1.0,
            "observed": 0.95,
            "inferred_by_solver": 0.90,
            "inferred_by_rule": 0.85,
            "inferred_by_stats": 0.80,
            "learned_from_case": 0.75,
            "self_reflection": 0.60,
            "proposed_by_llm": 0.50,
            "simulated": 0.30,
        }
        return weights.get(self.source_type, 0.50)


class ProvenanceAdapter:
    """Wraps legacy ProvenanceTracker with tiered source type support.

    The legacy tracker records origin/validation/consensus per fact_id.
    This adapter adds source_type classification and trust weighting
    for the autonomy core's evidence layer.
    """

    def __init__(self, legacy_tracker=None, audit_log=None):
        if legacy_tracker is not None:
            self._tracker = legacy_tracker
        else:
            cls = _get_provenance_class()
            if cls and audit_log:
                self._tracker = cls(audit_log)
            else:
                self._tracker = None
        self._lock = threading.Lock()
        self._records: Dict[str, ProvenanceRecord] = {}

    def record_provenance(self, fact_id: str, source_type: str,
                          canonical_id: str = "", capability_id: str = "",
                          quality_grade: str = "", confidence: float = 0.0,
                          metadata: dict = None) -> ProvenanceRecord:
        """Record provenance for a fact with full source type classification."""
        if source_type not in VALID_SOURCE_TYPES:
            raise ValueError(f"Invalid source_type: {source_type}")

        record = ProvenanceRecord(
            fact_id=fact_id,
            source_type=source_type,
            canonical_id=canonical_id,
            capability_id=capability_id,
            quality_grade=quality_grade,
            confidence=confidence,
            metadata=metadata or {},
        )

        with self._lock:
            self._records[fact_id] = record

        if self._tracker:
            try:
                self._tracker.record_validation(
                    fact_id=fact_id,
                    validator_agent_id=f"autonomy:{source_type}",
                    verdict="agree" if confidence > 0.5 else "neutral",
                )
            except Exception as exc:
                logger.warning("ProvenanceAdapter: legacy write failed: %s", exc)

        return record

    def get_provenance(self, fact_id: str) -> Optional[ProvenanceRecord]:
        """Get provenance record for a fact."""
        with self._lock:
            record = self._records.get(fact_id)

        if record is None and self._tracker:
            try:
                chain = self._tracker.get_provenance_chain(fact_id)
                if chain and chain.get("origin"):
                    record = ProvenanceRecord(
                        fact_id=fact_id,
                        source_type="observed",
                        confidence=0.5,
                    )
            except Exception:
                pass

        return record

    def get_trust_weight(self, fact_id: str) -> float:
        """Get trust weight for a fact based on its provenance."""
        record = self.get_provenance(fact_id)
        return record.trust_weight() if record else 0.50

    def get_by_source_type(self, source_type: str) -> List[ProvenanceRecord]:
        """Get all records of a specific source type."""
        with self._lock:
            return [r for r in self._records.values()
                    if r.source_type == source_type]

    def get_verified_facts(self) -> List[ProvenanceRecord]:
        """Get all facts confirmed by a verifier."""
        with self._lock:
            return [r for r in self._records.values()
                    if r.source_type == "confirmed_by_verifier"]

    def upgrade_source_type(self, fact_id: str, new_source_type: str,
                            reason: str = "") -> bool:
        """Upgrade a fact's source type (e.g., proposed_by_llm -> confirmed_by_verifier)."""
        if new_source_type not in VALID_SOURCE_TYPES:
            return False
        with self._lock:
            record = self._records.get(fact_id)
            if not record:
                return False
            old_weight = record.trust_weight()
            record.source_type = new_source_type
            new_weight = record.trust_weight()
            if new_weight < old_weight:
                logger.info(
                    "Provenance downgrade: %s %s->%s (weight %.2f->%.2f) reason=%s",
                    fact_id, record.source_type, new_source_type,
                    old_weight, new_weight, reason,
                )
            record.metadata["upgrade_reason"] = reason
            return True

    def stats(self) -> Dict[str, Any]:
        """Summary statistics."""
        with self._lock:
            by_type: Dict[str, int] = {}
            total_weight = 0.0
            for r in self._records.values():
                by_type[r.source_type] = by_type.get(r.source_type, 0) + 1
                total_weight += r.trust_weight()
            count = len(self._records)
            return {
                "total_records": count,
                "by_source_type": by_type,
                "avg_trust_weight": total_weight / count if count else 0.0,
                "legacy_tracker_available": self._tracker is not None,
            }
