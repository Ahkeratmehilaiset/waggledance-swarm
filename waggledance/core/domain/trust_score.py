"""Trust scoring domain models."""

from dataclasses import dataclass


@dataclass
class TrustSignals:
    """Six-signal trust input for an agent."""

    hallucination_rate: float  # 0.0-1.0, lower is better
    validation_rate: float  # 0.0-1.0, higher is better
    consensus_agreement: float  # 0.0-1.0
    correction_rate: float  # 0.0-1.0, lower is better
    fact_production_rate: float  # facts/hour
    freshness_score: float  # 0.0-1.0, recency weight


@dataclass
class AgentTrust:
    """Computed trust score for an agent."""

    agent_id: str
    composite_score: float  # 0.0-1.0
    signals: TrustSignals
    updated_at: float
