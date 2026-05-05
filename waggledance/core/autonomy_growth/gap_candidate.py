# SPDX-License-Identifier: BUSL-1.1
"""Phase 18B - Gap candidate dataclasses + verdict enum.

Pure dataclasses. No I/O. The mainline gap-mining contract for the
runtime-signal -> verdict feedback loop.

Verdicts (the six-element enum the mainline mining function emits):

    ALLOWLISTED_SOLVER_SPEC   - low-risk allowlisted family, sufficient
                                evidence, deterministic spec emitted.
    INSUFFICIENT_EVIDENCE     - allowlisted family but signal count or
                                confidence below threshold.
    OUT_OF_FAMILY_REJECTED    - family not in the six-family low-risk
                                allowlist (and not "builder_handoff").
    HIGH_RISK_REJECTED        - risk_label="high_risk" - blocked.
    BUILDER_HANDOFF_QUARANTINED - explicit family_kind="builder_handoff",
                                  payload retained but no_auto_promotion.
    DUPLICATE_SUPPRESSED      - same candidate_id already emitted in this
                                run; later occurrences suppressed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class GapVerdict(str, Enum):
    """Six-element verdict enum emitted by ``mine_runtime_gaps``."""

    ALLOWLISTED_SOLVER_SPEC = "ALLOWLISTED_SOLVER_SPEC"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    OUT_OF_FAMILY_REJECTED = "OUT_OF_FAMILY_REJECTED"
    HIGH_RISK_REJECTED = "HIGH_RISK_REJECTED"
    BUILDER_HANDOFF_QUARANTINED = "BUILDER_HANDOFF_QUARANTINED"
    DUPLICATE_SUPPRESSED = "DUPLICATE_SUPPRESSED"


@dataclass(frozen=True)
class GapCandidate:
    """One mined runtime-gap candidate with its verdict.

    candidate_id is a stable 16-hex-char SHA-256 prefix derived from
    family_kind + canonical-feature-key. Two runs over the same input
    produce identical candidate_ids.
    """

    candidate_id: str
    family_kind: str
    feature_dict: Mapping[str, Any]
    evidence_refs: tuple[str, ...]
    confidence: float
    risk_label: str
    provenance: Mapping[str, Any]
    signal_count: int
    verdict: GapVerdict
    rejection_reason: str | None = None
    builder_handoff_payload: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "family_kind": self.family_kind,
            "feature_dict": dict(self.feature_dict),
            "evidence_refs": list(self.evidence_refs),
            "confidence": self.confidence,
            "risk_label": self.risk_label,
            "provenance": dict(self.provenance),
            "signal_count": self.signal_count,
            "verdict": self.verdict.value,
            "rejection_reason": self.rejection_reason,
            "builder_handoff_payload": (
                dict(self.builder_handoff_payload)
                if self.builder_handoff_payload is not None else None
            ),
        }


@dataclass(frozen=True)
class GapMiningResult:
    """Outcome of one ``mine_runtime_gaps`` invocation.

    counters maps each ``GapVerdict`` value (string) to an integer
    count, plus ``signals_total`` and ``candidates_total`` fields
    convenient for proof harness assertions.
    """

    candidates: tuple[GapCandidate, ...]
    counters: Mapping[str, int]
    config_snapshot: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidates": [c.to_dict() for c in self.candidates],
            "counters": dict(self.counters),
            "config_snapshot": dict(self.config_snapshot),
        }
