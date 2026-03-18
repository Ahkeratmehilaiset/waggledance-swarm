"""Safety cases — structured safety argument for high-risk autonomy actions.

A safety case is a structured argument that a system action is acceptably safe.
Each case documents: the claim, evidence, context, and counter-arguments.
Used by the PolicyEngine before approving high-risk actions.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SafetyEvidence:
    """Evidence supporting or undermining a safety claim."""
    evidence_type: str  # historical, analytical, simulation, sensor, verifier
    description: str
    strength: float = 0.5  # 0-1
    source: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class SafetyCase:
    """Structured safety argument for an action."""
    case_id: str
    action_id: str
    capability_id: str
    claim: str
    context: Dict[str, Any] = field(default_factory=dict)
    supporting_evidence: List[SafetyEvidence] = field(default_factory=list)
    counter_evidence: List[SafetyEvidence] = field(default_factory=list)
    mitigations: List[str] = field(default_factory=list)
    overall_confidence: float = 0.0
    verdict: str = "pending"  # safe, unsafe, needs_review, pending
    created_at: float = field(default_factory=time.time)

    def compute_confidence(self) -> float:
        """Compute overall safety confidence from evidence.

        Weighted sum of supporting evidence minus counter evidence.
        """
        if not self.supporting_evidence and not self.counter_evidence:
            return 0.0

        support = sum(e.strength for e in self.supporting_evidence)
        counter = sum(e.strength for e in self.counter_evidence)
        total_weight = len(self.supporting_evidence) + len(self.counter_evidence)

        if total_weight == 0:
            return 0.0

        raw = (support - counter) / total_weight
        self.overall_confidence = max(0.0, min(1.0, (raw + 1.0) / 2.0))
        return self.overall_confidence

    def determine_verdict(self, threshold: float = 0.7) -> str:
        """Determine safety verdict based on confidence and evidence."""
        confidence = self.compute_confidence()

        if confidence >= threshold and not self.counter_evidence:
            self.verdict = "safe"
        elif confidence >= threshold:
            self.verdict = "needs_review"
        elif confidence >= 0.4:
            self.verdict = "needs_review"
        else:
            self.verdict = "unsafe"

        return self.verdict

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "action_id": self.action_id,
            "capability_id": self.capability_id,
            "claim": self.claim,
            "supporting_evidence": len(self.supporting_evidence),
            "counter_evidence": len(self.counter_evidence),
            "mitigations": self.mitigations,
            "overall_confidence": round(self.overall_confidence, 4),
            "verdict": self.verdict,
        }


class SafetyCaseBuilder:
    """Builds safety cases for high-risk actions.

    Collects evidence from multiple sources:
    - Historical success rate of the capability
    - Verifier pass rate
    - World model residuals
    - Procedural memory (known good/bad patterns)
    - Risk score dimensions
    """

    def __init__(self):
        self._cases: List[SafetyCase] = []
        self._max_cases = 200

    def build_case(self, action_id: str, capability_id: str,
                   claim: str, risk_score=None,
                   historical_success_rate: float = None,
                   verifier_pass_rate: float = None,
                   is_known_procedure: bool = False,
                   is_anti_pattern: bool = False,
                   context: dict = None) -> SafetyCase:
        """Build a safety case from available evidence."""
        case = SafetyCase(
            case_id=f"sc-{action_id[:12]}",
            action_id=action_id,
            capability_id=capability_id,
            claim=claim,
            context=context or {},
        )

        # Historical evidence
        if historical_success_rate is not None:
            case.supporting_evidence.append(SafetyEvidence(
                evidence_type="historical",
                description=f"Capability success rate: {historical_success_rate:.0%}",
                strength=historical_success_rate,
                source="trust_adapter",
            ))

        # Verifier evidence
        if verifier_pass_rate is not None:
            case.supporting_evidence.append(SafetyEvidence(
                evidence_type="verifier",
                description=f"Verifier pass rate: {verifier_pass_rate:.0%}",
                strength=verifier_pass_rate,
                source="verifier",
            ))

        # Procedural evidence
        if is_known_procedure:
            case.supporting_evidence.append(SafetyEvidence(
                evidence_type="analytical",
                description="Known proven procedure (GOLD grade)",
                strength=0.9,
                source="procedural_memory",
            ))

        if is_anti_pattern:
            case.counter_evidence.append(SafetyEvidence(
                evidence_type="analytical",
                description="Known anti-pattern (QUARANTINE grade)",
                strength=0.8,
                source="procedural_memory",
            ))

        # Risk score evidence
        if risk_score is not None:
            reversibility = getattr(risk_score, "reversibility", 0.5)
            if reversibility > 0.7:
                case.supporting_evidence.append(SafetyEvidence(
                    evidence_type="analytical",
                    description=f"Highly reversible (reversibility={reversibility:.2f})",
                    strength=reversibility,
                    source="risk_scoring",
                ))
                case.mitigations.append("Action is reversible — rollback available")

            severity = getattr(risk_score, "severity", 0.5)
            if severity > 0.7:
                case.counter_evidence.append(SafetyEvidence(
                    evidence_type="analytical",
                    description=f"High severity ({severity:.2f})",
                    strength=severity,
                    source="risk_scoring",
                ))

            observability = getattr(risk_score, "observability", 0.5)
            if observability > 0.7:
                case.supporting_evidence.append(SafetyEvidence(
                    evidence_type="analytical",
                    description=f"Highly observable (observability={observability:.2f})",
                    strength=observability * 0.8,
                    source="risk_scoring",
                ))

        case.determine_verdict()

        self._cases.append(case)
        if len(self._cases) > self._max_cases:
            self._cases = self._cases[-self._max_cases:]

        return case

    def get_case(self, case_id: str) -> Optional[SafetyCase]:
        """Retrieve a safety case by ID."""
        for c in reversed(self._cases):
            if c.case_id == case_id:
                return c
        return None

    def get_recent_cases(self, limit: int = 20) -> List[SafetyCase]:
        """Get most recent safety cases."""
        return list(reversed(self._cases[-limit:]))

    def stats(self) -> Dict[str, Any]:
        total = len(self._cases)
        by_verdict: Dict[str, int] = {}
        for c in self._cases:
            by_verdict[c.verdict] = by_verdict.get(c.verdict, 0) + 1
        return {
            "total_cases": total,
            "by_verdict": by_verdict,
        }
