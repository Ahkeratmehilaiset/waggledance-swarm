# SPDX-License-Identifier: Apache-2.0
"""
Prediction Error Ledger — append-only record of verifier prediction errors.

Every time the verifier runs, the prediction error is recorded:
  - query_id, solver_used, expected/actual outcome, error magnitude, timestamp.

Night learning reads this ledger to identify:
  - Which solvers have systematic bias
  - Which query types cause the most errors
  - Whether errors are increasing or decreasing over time

Storage: append-only JSONL in data/prediction_errors.jsonl.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("waggledance.learning.prediction_error_ledger")

DEFAULT_LEDGER_PATH = "data/prediction_errors.jsonl"


@dataclass
class PredictionError:
    """A single prediction error entry."""
    query_id: str
    solver_used: str
    expected_outcome: str  # "pass" or "fail"
    actual_outcome: str    # "pass" or "fail"
    error_magnitude: float  # 0.0 = correct, 1.0 = mismatch
    confidence: float = 0.0
    intent: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SolverErrorProfile:
    """Aggregated error profile for a single solver."""
    solver_id: str
    total_predictions: int = 0
    total_errors: int = 0
    error_rate: float = 0.0
    mean_error_magnitude: float = 0.0
    recent_trend: float = 0.0  # positive = getting worse, negative = improving


@dataclass
class LedgerAnalysis:
    """Analysis result from reading the ledger."""
    total_entries: int = 0
    total_errors: int = 0
    overall_error_rate: float = 0.0
    solver_profiles: Dict[str, SolverErrorProfile] = field(default_factory=dict)
    intent_error_rates: Dict[str, float] = field(default_factory=dict)
    improving_solvers: List[str] = field(default_factory=list)
    degrading_solvers: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_entries": self.total_entries,
            "total_errors": self.total_errors,
            "overall_error_rate": round(self.overall_error_rate, 4),
            "solver_profiles": {
                k: asdict(v) for k, v in self.solver_profiles.items()
            },
            "intent_error_rates": {
                k: round(v, 4) for k, v in self.intent_error_rates.items()
            },
            "improving_solvers": self.improving_solvers,
            "degrading_solvers": self.degrading_solvers,
        }


class PredictionErrorLedger:
    """Append-only ledger of prediction errors."""

    def __init__(self, ledger_path: str = DEFAULT_LEDGER_PATH):
        self._path = Path(ledger_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._buffer: List[PredictionError] = []

    def record(
        self,
        query_id: str,
        solver_used: str,
        verified: bool,
        confidence: float = 0.0,
        intent: str = "",
    ) -> PredictionError:
        """Record a verifier result as a prediction error entry.

        The "prediction" is that the solver should produce a correct result.
        error_magnitude = 0.0 if verified, 1.0 if not.
        """
        entry = PredictionError(
            query_id=query_id,
            solver_used=solver_used,
            expected_outcome="pass",
            actual_outcome="pass" if verified else "fail",
            error_magnitude=0.0 if verified else 1.0,
            confidence=confidence,
            intent=intent,
        )
        self._buffer.append(entry)
        self._append_to_file(entry)
        return entry

    def recent(self, limit: int = 100) -> List[PredictionError]:
        """Return recent entries from the in-memory buffer."""
        return self._buffer[-limit:]

    def analyze(self, max_entries: int = 5000) -> LedgerAnalysis:
        """Read the ledger and produce an analysis.

        Identifies systematic bias, per-solver error rates, and trends.
        """
        entries = self._read_all(max_entries)
        if not entries:
            return LedgerAnalysis()

        analysis = LedgerAnalysis(total_entries=len(entries))

        # Group by solver
        solver_entries: Dict[str, List[PredictionError]] = {}
        intent_entries: Dict[str, List[PredictionError]] = {}
        for e in entries:
            solver_entries.setdefault(e.solver_used, []).append(e)
            if e.intent:
                intent_entries.setdefault(e.intent, []).append(e)

        # Per-solver profiles
        for solver_id, solver_list in solver_entries.items():
            total = len(solver_list)
            errors = sum(1 for e in solver_list if e.error_magnitude > 0)
            mean_mag = sum(e.error_magnitude for e in solver_list) / total

            # Trend: compare first half vs second half error rate
            mid = total // 2
            if mid > 0:
                first_half_rate = sum(
                    1 for e in solver_list[:mid] if e.error_magnitude > 0
                ) / mid
                second_half_rate = sum(
                    1 for e in solver_list[mid:] if e.error_magnitude > 0
                ) / (total - mid)
                trend = second_half_rate - first_half_rate
            else:
                trend = 0.0

            profile = SolverErrorProfile(
                solver_id=solver_id,
                total_predictions=total,
                total_errors=errors,
                error_rate=errors / total if total > 0 else 0.0,
                mean_error_magnitude=mean_mag,
                recent_trend=round(trend, 4),
            )
            analysis.solver_profiles[solver_id] = profile

            if trend < -0.05:
                analysis.improving_solvers.append(solver_id)
            elif trend > 0.05:
                analysis.degrading_solvers.append(solver_id)

        # Per-intent error rates
        for intent, intent_list in intent_entries.items():
            errors = sum(1 for e in intent_list if e.error_magnitude > 0)
            analysis.intent_error_rates[intent] = (
                errors / len(intent_list) if intent_list else 0.0
            )

        analysis.total_errors = sum(
            1 for e in entries if e.error_magnitude > 0
        )
        analysis.overall_error_rate = (
            analysis.total_errors / len(entries) if entries else 0.0
        )

        # Sort improving/degrading by magnitude
        analysis.improving_solvers.sort(
            key=lambda s: analysis.solver_profiles[s].recent_trend,
        )
        analysis.degrading_solvers.sort(
            key=lambda s: analysis.solver_profiles[s].recent_trend,
            reverse=True,
        )

        return analysis

    def stats(self) -> Dict[str, Any]:
        return {
            "buffer_size": len(self._buffer),
            "ledger_path": str(self._path),
            "file_exists": self._path.exists(),
        }

    # ── Internal ──────────────────────────────────────────

    def _append_to_file(self, entry: PredictionError):
        try:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
        except Exception as exc:
            log.warning("Failed to append to ledger: %s", exc)

    def _read_all(self, max_entries: int = 5000) -> List[PredictionError]:
        """Read entries from file. Returns up to max_entries most recent."""
        if not self._path.exists():
            return list(self._buffer)

        entries: List[PredictionError] = []
        try:
            lines = self._path.read_text(encoding="utf-8").strip().split("\n")
            # Take last max_entries lines
            for line in lines[-max_entries:]:
                if not line.strip():
                    continue
                d = json.loads(line)
                entries.append(PredictionError(**d))
        except Exception as exc:
            log.warning("Failed to read ledger: %s", exc)

        return entries
