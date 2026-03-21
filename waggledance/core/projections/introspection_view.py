# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""
Introspection View — internal state monitoring and self-assessment (v3.2).

Technical (not anthropomorphic) dashboard panel showing:
  - observed / inferred / simulated facts
  - verified outcomes
  - promised-to-user items
  - current uncertainty score + attention allocation
  - observability gaps
  - recent self-reflection entries

Read-only projection. No new store — aggregates from existing sources.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger("waggledance.projections.introspection")


# ── Data types ───────────────────────────────────────────────

@dataclass
class IntrospectionSnapshot:
    """A single introspection view snapshot."""
    timestamp: float = field(default_factory=time.time)
    observed_facts: List[Dict[str, Any]] = field(default_factory=list)
    inferred_facts: List[Dict[str, Any]] = field(default_factory=list)
    simulated_facts: List[Dict[str, Any]] = field(default_factory=list)
    verified_outcomes: List[Dict[str, Any]] = field(default_factory=list)
    promised_to_user: List[Dict[str, Any]] = field(default_factory=list)
    uncertainty_score: float = 0.0
    attention_allocation: Dict[str, Any] = field(default_factory=dict)
    observability_gaps: List[Dict[str, Any]] = field(default_factory=list)
    self_reflection_entries: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "observed_facts": self.observed_facts,
            "inferred_facts": self.inferred_facts,
            "simulated_facts": self.simulated_facts,
            "verified_outcomes": self.verified_outcomes,
            "promised_to_user": self.promised_to_user,
            "uncertainty_score": round(self.uncertainty_score, 4),
            "attention_allocation": self.attention_allocation,
            "observability_gaps": self.observability_gaps,
            "self_reflection_entries": self.self_reflection_entries,
            "fact_counts": {
                "observed": len(self.observed_facts),
                "inferred": len(self.inferred_facts),
                "simulated": len(self.simulated_facts),
                "verified": len(self.verified_outcomes),
            },
        }


# ── Core function ────────────────────────────────────────────

def build_introspection(
    observed_facts: Optional[List[Dict[str, Any]]] = None,
    inferred_facts: Optional[List[Dict[str, Any]]] = None,
    simulated_facts: Optional[List[Dict[str, Any]]] = None,
    verified_outcomes: Optional[List[Dict[str, Any]]] = None,
    promised_to_user: Optional[List[Dict[str, Any]]] = None,
    uncertainty_score: float = 0.0,
    attention_allocation: Optional[Dict[str, Any]] = None,
    observability_gaps: Optional[List[Dict[str, Any]]] = None,
    self_reflection_entries: Optional[List[Dict[str, Any]]] = None,
) -> IntrospectionSnapshot:
    """
    Build an introspection snapshot from current system state.

    All inputs are plain dicts/lists — caller extracts from runtime objects.
    This is a read-only aggregation with no side effects.
    """
    return IntrospectionSnapshot(
        observed_facts=observed_facts or [],
        inferred_facts=inferred_facts or [],
        simulated_facts=simulated_facts or [],
        verified_outcomes=verified_outcomes or [],
        promised_to_user=promised_to_user or [],
        uncertainty_score=uncertainty_score,
        attention_allocation=attention_allocation or {},
        observability_gaps=observability_gaps or [],
        self_reflection_entries=self_reflection_entries or [],
    )


def filter_by_profile(
    snapshot: IntrospectionSnapshot,
    profile: str,
) -> Dict[str, Any]:
    """
    Filter introspection view by profile level.

    APIARY: full view
    HOME/FACTORY: no simulated facts or self-reflection
    GADGET/COTTAGE: summary only (counts, no detail)
    """
    full = snapshot.to_dict()

    if profile.upper() == "APIARY":
        return full

    if profile.upper() in ("HOME", "FACTORY"):
        # Remove simulated facts and self-reflection detail
        full["simulated_facts"] = []
        full["self_reflection_entries"] = []
        return full

    # GADGET, COTTAGE: summary only
    return {
        "timestamp": full["timestamp"],
        "uncertainty_score": full["uncertainty_score"],
        "attention_allocation": full["attention_allocation"],
        "fact_counts": full["fact_counts"],
        "promises_count": len(full["promised_to_user"]),
        "gaps_count": len(full["observability_gaps"]),
    }
