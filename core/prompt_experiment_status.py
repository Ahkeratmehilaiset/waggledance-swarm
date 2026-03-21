"""Prompt experiment status — format experiment data for API responses."""

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

@dataclass
class ExperimentSummary:
    id: str
    status: str  # "running", "completed", "rolled_back"
    baseline_score: float = 0.0
    current_score: float = 0.0
    improvement: float = 0.0
    samples: int = 0
    created_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "baseline_score": round(self.baseline_score, 3),
            "current_score": round(self.current_score, 3),
            "improvement": round(self.improvement, 3),
            "samples": self.samples,
            "created_at": self.created_at,
        }

class ExperimentStatusFormatter:
    """Formats experiment data for API consumption."""

    @staticmethod
    def format_experiment(experiment_data: dict) -> ExperimentSummary:
        """Convert raw experiment dict to ExperimentSummary."""
        return ExperimentSummary(
            id=experiment_data.get("id", ""),
            status=experiment_data.get("status", "unknown"),
            baseline_score=experiment_data.get("baseline_score", 0.0),
            current_score=experiment_data.get("current_score", 0.0),
            improvement=experiment_data.get("improvement", 0.0),
            samples=experiment_data.get("samples", 0),
            created_at=experiment_data.get("created_at", 0.0),
        )

    @staticmethod
    def format_list(experiments: list[dict]) -> list[dict]:
        """Format a list of experiment dicts for API response."""
        return [
            ExperimentStatusFormatter.format_experiment(e).to_dict()
            for e in experiments
        ]
