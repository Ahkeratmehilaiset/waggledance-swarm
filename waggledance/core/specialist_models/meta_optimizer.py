# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""
Meta-Optimizer — hyperparameter learning from canary results (v3.2).

Activation gate: minimum 3 successful canary cycles per model.
Learns from canary history to propose learning_rate and feature_set changes.
Rollback to defaults if proposals are worse for 2 consecutive cycles.

Feature-flagged: learning.meta_optimizer_enabled = true
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger("waggledance.specialist.meta_optimizer")


# ── Config defaults ──────────────────────────────────────────

DEFAULT_MIN_CANARY_CYCLES = 3
DEFAULT_ROLLBACK_AFTER_FAILURES = 2


# ── Data types ───────────────────────────────────────────────

@dataclass
class CanaryRecord:
    """A single canary cycle result."""
    model_id: str
    cycle: int
    learning_rate: float
    feature_count: int
    accuracy: float
    accuracy_delta: float = 0.0  # vs previous cycle
    convergence_speed: float = 0.0  # epochs to converge
    proposed_by_meta: bool = False  # was this meta-optimizer's suggestion?


@dataclass
class HyperparameterProposal:
    """Meta-optimizer's suggestion for next canary cycle."""
    model_id: str
    learning_rate: float
    feature_set_size: int
    reason: str


@dataclass
class MetaOptimizerState:
    """Per-model meta-optimizer state."""
    model_id: str
    canary_history: List[CanaryRecord] = field(default_factory=list)
    consecutive_failures: int = 0
    active: bool = False
    default_lr: float = 0.01
    default_feature_count: int = 50


# ── Core functions ───────────────────────────────────────────

def check_activation_gate(
    history: List[CanaryRecord],
    min_cycles: int = DEFAULT_MIN_CANARY_CYCLES,
) -> bool:
    """
    Meta-optimizer activates only after min_cycles successful canary cycles.
    A successful cycle has accuracy > 0.
    """
    successful = sum(1 for r in history if r.accuracy > 0)
    return successful >= min_cycles


def propose_hyperparameters(
    state: MetaOptimizerState,
    min_cycles: int = DEFAULT_MIN_CANARY_CYCLES,
) -> Optional[HyperparameterProposal]:
    """
    Propose hyperparameters for the next canary cycle.

    Returns None if activation gate not met.
    """
    if not check_activation_gate(state.canary_history, min_cycles):
        return None

    if not state.active:
        state.active = True
        log.info("Meta-optimizer activated for %s after %d canary cycles",
                 state.model_id, len(state.canary_history))

    # Simple strategy: learn from best historical LR and feature count
    history = state.canary_history
    if not history:
        return None

    # Find best cycle
    best = max(history, key=lambda r: r.accuracy)

    # Propose slight adjustment from best
    proposed_lr = best.learning_rate
    proposed_features = best.feature_count

    # If accuracy is improving, keep current direction
    recent = history[-3:] if len(history) >= 3 else history
    avg_delta = sum(r.accuracy_delta for r in recent) / len(recent) if recent else 0

    if avg_delta > 0:
        # Accuracy improving — try slightly higher LR
        proposed_lr = min(0.1, best.learning_rate * 1.1)
        reason = f"accuracy improving (avg_delta={avg_delta:.4f}), increase LR"
    elif avg_delta < -0.01:
        # Accuracy declining — reduce LR
        proposed_lr = max(0.0001, best.learning_rate * 0.8)
        reason = f"accuracy declining (avg_delta={avg_delta:.4f}), reduce LR"
    else:
        # Stable — keep current
        reason = "accuracy stable, maintain parameters"

    return HyperparameterProposal(
        model_id=state.model_id,
        learning_rate=round(proposed_lr, 6),
        feature_set_size=proposed_features,
        reason=reason,
    )


def record_canary_result(
    state: MetaOptimizerState,
    record: CanaryRecord,
    rollback_threshold: int = DEFAULT_ROLLBACK_AFTER_FAILURES,
) -> bool:
    """
    Record a canary result and check if rollback is needed.

    Returns True if rollback was triggered.
    """
    state.canary_history.append(record)

    if record.proposed_by_meta and record.accuracy_delta < 0:
        state.consecutive_failures += 1
        log.warning("Meta-optimizer proposal worse for %s (%d consecutive)",
                     state.model_id, state.consecutive_failures)
    else:
        state.consecutive_failures = 0

    if state.consecutive_failures >= rollback_threshold:
        log.warning("Meta-optimizer rollback for %s: %d consecutive failures",
                     state.model_id, state.consecutive_failures)
        state.consecutive_failures = 0
        return True  # rollback signal

    return False


def get_default_params(state: MetaOptimizerState) -> HyperparameterProposal:
    """Return default hyperparameters for rollback."""
    return HyperparameterProposal(
        model_id=state.model_id,
        learning_rate=state.default_lr,
        feature_set_size=state.default_feature_count,
        reason="rollback to defaults",
    )
