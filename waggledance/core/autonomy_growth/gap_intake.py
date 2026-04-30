# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Self-starting gap intake (Phase 12).

Turns runtime evidence into queued growth intents *without a human
trigger*. This is the layer that closes the autonomy loop's missing
left-hand side.

Two responsibilities:

* :class:`RuntimeGapDetector` — records signals (dispatcher misses,
  fallbacks, replay/shadow mismatches, family-hole observations) into
  the ``runtime_gap_signals`` table.
* :func:`digest_signals_into_intents` — folds the recorded signals
  into ``growth_intents`` and enqueues those that meet the per-family
  threshold. The digest function is the one a scheduler tick calls
  before claiming work.

Hex-cell aware: every signal carries an optional ``cell_coord``;
intents are keyed by (family_kind, cell_coord, intent_seed). Per
RULE 7 nothing in this module writes to ad hoc JSON files; everything
flows through ``ControlPlaneDB``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence

from waggledance.core.storage.control_plane import (
    ControlPlaneDB,
    GrowthIntentRecord,
    RuntimeGapSignalRecord,
)

from .low_risk_policy import LOW_RISK_FAMILY_KINDS, is_low_risk_family


@dataclass(frozen=True)
class GapSignal:
    """In-memory descriptor for one runtime gap observation."""

    kind: str  # e.g. 'miss', 'fallback', 'shadow_mismatch', 'family_hole'
    family_kind: Optional[str]
    cell_coord: Optional[str]
    intent_seed: Optional[str] = None  # stable suffix for the intent_key
    weight: float = 1.0
    payload: Optional[Mapping[str, Any]] = None
    spec_seed: Optional[Mapping[str, Any]] = None


@dataclass
class IntakeStats:
    signals_recorded: int = 0
    intents_created: int = 0
    intents_enqueued: int = 0
    by_family: dict[str, int] = field(default_factory=dict)


class RuntimeGapDetector:
    """Records runtime gap signals into the control plane."""

    def __init__(self, control_plane: ControlPlaneDB) -> None:
        self._cp = control_plane
        self._stats = IntakeStats()

    @property
    def stats(self) -> IntakeStats:
        return self._stats

    def reset_stats(self) -> None:
        self._stats = IntakeStats()

    def record(self, signal: GapSignal) -> RuntimeGapSignalRecord:
        payload_json = (
            json.dumps(dict(signal.payload), sort_keys=True)
            if signal.payload is not None else None
        )
        rec = self._cp.record_runtime_gap_signal(
            kind=signal.kind,
            family_kind=signal.family_kind,
            cell_coord=signal.cell_coord,
            signal_payload=payload_json,
            weight=signal.weight,
        )
        self._cp.emit_growth_event(
            "signal_recorded",
            entity_kind="runtime_gap_signal",
            entity_id=rec.id,
            family_kind=signal.family_kind,
            cell_coord=signal.cell_coord,
            payload=payload_json,
        )
        self._stats.signals_recorded += 1
        if signal.family_kind:
            self._stats.by_family[signal.family_kind] = (
                self._stats.by_family.get(signal.family_kind, 0) + 1
            )
        return rec


def _intent_key(
    family_kind: str,
    cell_coord: Optional[str],
    intent_seed: Optional[str],
) -> str:
    cell = cell_coord or "_"
    seed = intent_seed or "_default"
    return f"{family_kind}:{cell}:{seed}"


def digest_signals_into_intents(
    cp: ControlPlaneDB,
    *,
    candidate_signals: Sequence[GapSignal],
    min_signals_per_intent: int = 1,
    autoenqueue: bool = True,
    base_priority: int = 0,
) -> IntakeStats:
    """Fold a batch of signals into growth_intents, optionally enqueuing.

    The function is bounded: it processes only the signals passed in.
    A scheduler tick that wants self-starting behavior calls
    ``RuntimeGapDetector.record(...)`` for each runtime observation and
    then calls this function with the corresponding signals, the same
    way a "cron tick" would aggregate over a window.

    Returns aggregate intake stats for the batch.
    """

    stats = IntakeStats()
    by_intent: dict[str, list[GapSignal]] = {}
    for s in candidate_signals:
        if s.family_kind is None:
            continue
        if not is_low_risk_family(s.family_kind):
            # Out-of-allowlist signals are NOT auto-enqueued. They can
            # still be persisted as raw signals (already done by
            # RuntimeGapDetector.record) for later analysis, but the
            # bounded autonomy loop refuses to act on them — RULE 13.
            continue
        key = _intent_key(s.family_kind, s.cell_coord, s.intent_seed)
        by_intent.setdefault(key, []).append(s)

    for intent_key, sigs in by_intent.items():
        if len(sigs) < min_signals_per_intent:
            continue
        # Use the highest-weight, latest spec_seed as the canonical seed
        # for this intent.
        seed_signal = next(
            (s for s in reversed(sigs) if s.spec_seed is not None),
            None,
        )
        spec_seed_json = (
            json.dumps(dict(seed_signal.spec_seed), sort_keys=True)
            if seed_signal is not None else None
        )
        priority = base_priority + sum(int(s.weight * 10) for s in sigs)
        first = sigs[0]
        intent: GrowthIntentRecord = cp.upsert_growth_intent(
            family_kind=first.family_kind,  # type: ignore[arg-type]
            intent_key=intent_key,
            cell_coord=first.cell_coord,
            priority=priority,
            spec_seed_json=spec_seed_json,
        )
        was_existing = (intent.signal_count > 0 and intent.status not in
                        ("pending", "enqueued"))
        if intent.signal_count == 0:
            stats.intents_created += 1
            cp.emit_growth_event(
                "intent_created",
                entity_kind="growth_intent",
                entity_id=intent.id,
                family_kind=intent.family_kind,
                cell_coord=intent.cell_coord,
                payload=json.dumps(
                    {"intent_key": intent.intent_key, "priority": priority}
                ),
            )
        if autoenqueue and intent.status in ("pending", "enqueued"):
            queue_row = cp.enqueue_growth_intent(
                intent.id, priority=priority
            )
            if queue_row.attempt_count == 0:
                stats.intents_enqueued += 1
                cp.emit_growth_event(
                    "intent_enqueued",
                    entity_kind="autogrowth_queue",
                    entity_id=queue_row.id,
                    family_kind=intent.family_kind,
                    cell_coord=intent.cell_coord,
                )
        stats.by_family[first.family_kind] = (  # type: ignore[index]
            stats.by_family.get(first.family_kind or "", 0) + 1
        )
        # silence unused (kept for clarity that we know about already-enqueued)
        _ = was_existing
    return stats


__all__ = [
    "GapSignal",
    "IntakeStats",
    "RuntimeGapDetector",
    "digest_signals_into_intents",
    "LOW_RISK_FAMILY_KINDS",
]
