"""
Legacy Converter — converts existing Q&A data into CaseTrajectory format.

Reads from:
  - training_collector pairs (question, answer, confidence, source)
  - learning_ledger events
  - corrections memory
  - route telemetry
  - audit_log entries
  - round_table consensus

Builds CaseTrajectory objects with inferred goals, capabilities, and
quality grades for Night Learning v2 consumption.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from waggledance.core.domain.autonomy import (
    CaseTrajectory,
    CapabilityCategory,
    CapabilityContract,
    Goal,
    GoalType,
    QualityGrade,
    WorldSnapshot,
)
from waggledance.core.learning.case_builder import CaseTrajectoryBuilder

log = logging.getLogger("waggledance.learning.legacy_converter")


@dataclass
class LegacyRecord:
    """A single record from legacy systems."""
    question: str = ""
    answer: str = ""
    confidence: float = 0.0
    source: str = ""
    route_type: str = ""
    corrections: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    agent_id: str = ""
    canonical_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class LegacyConverter:
    """
    Converts legacy Q&A data into CaseTrajectory format.

    Batch process: call convert_batch() with legacy records,
    get back graded CaseTrajectory objects.
    """

    def __init__(self, profile: str = "DEFAULT"):
        self._profile = profile
        self._builder = CaseTrajectoryBuilder(profile=profile)
        self._converted_count = 0
        self._error_count = 0

    def convert(self, record: LegacyRecord) -> CaseTrajectory:
        """Convert a single legacy record to CaseTrajectory."""
        try:
            case = self._builder.build_from_legacy(
                question=record.question,
                answer=record.answer,
                confidence=record.confidence,
                source=record.source,
                route_type=record.route_type,
                corrections=record.corrections,
            )
            self._converted_count += 1
            return case
        except Exception as e:
            log.warning("Failed to convert legacy record: %s", e)
            self._error_count += 1
            # Return a bronze-grade fallback
            goal = Goal(type=GoalType.OBSERVE, description=record.question[:200])
            case = CaseTrajectory(
                goal=goal,
                verifier_result={"passed": False, "error": str(e)},
                profile=self._profile,
            )
            case.quality_grade = QualityGrade.BRONZE
            return case

    def convert_batch(self, records: List[LegacyRecord]) -> List[CaseTrajectory]:
        """Convert a batch of legacy records."""
        return [self.convert(r) for r in records]

    def convert_from_dicts(self, records: List[Dict[str, Any]]) -> List[CaseTrajectory]:
        """Convert a batch of legacy records from dict format."""
        legacy_records = []
        for r in records:
            lr = LegacyRecord(
                question=r.get("question", r.get("query", "")),
                answer=r.get("answer", r.get("response", "")),
                confidence=r.get("confidence", 0.0),
                source=r.get("source", ""),
                route_type=r.get("route_type", r.get("method", "")),
                corrections=r.get("corrections", []),
                timestamp=r.get("timestamp", time.time()),
                agent_id=r.get("agent_id", ""),
                canonical_id=r.get("canonical_id", ""),
                metadata=r.get("metadata", {}),
            )
            legacy_records.append(lr)
        return self.convert_batch(legacy_records)

    def convert_training_pairs(
        self,
        pairs: List[Tuple[str, str, float, str]],
    ) -> List[CaseTrajectory]:
        """
        Convert training_collector style pairs.

        Each tuple: (question, answer, confidence, source)
        """
        records = [
            LegacyRecord(
                question=q,
                answer=a,
                confidence=c,
                source=s,
            )
            for q, a, c, s in pairs
        ]
        return self.convert_batch(records)

    def stats(self) -> dict:
        return {
            "converted": self._converted_count,
            "errors": self._error_count,
            "builder_stats": self._builder.stats(),
        }
