# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Self-state / homeostasis layer for the Mama Event Observatory.

Per x.txt §6 the observer needs a "light but real" interoceptive
surrogate — uncertainty, novelty, trust, safety, fatigue,
need_for_reassurance, coherence.

Hard rules:

* **None of these values are allowed to be random.** Each update has
  a documented cause; the tests assert direction.
* Values are bounded to [0, 1]. A helper clamps every mutation.
* The state is a plain dataclass carrying only floats. The
  :class:`SelfStateUpdater` holds the update logic so it can be
  unit-tested without a running observer.
* There is no "emotion" column and no "consciousness" column. The
  enum is closed.

The observer writes one row of the state to NDJSON per observed
event. Callers can diff two rows to get the "state delta" signal
the scoring layer's axis D reads.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Mapping, Optional

log = logging.getLogger("waggledance.observatory.mama_events.self_state")


# ── value type ──────────────────────────────────────────────


def _clamp01(value: float) -> float:
    """Clamp to [0.0, 1.0]. Returns 0.0 for NaN to keep downstream math sane."""
    if math.isnan(value):
        return 0.0
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return float(value)


@dataclass(slots=True)
class SelfState:
    """Interoceptive surrogate, all dimensions in [0, 1].

    The dimensions are deliberately chosen to map 1:1 onto the
    scoring axis D inputs:

    * ``uncertainty``        — general epistemic uncertainty
    * ``novelty``            — how unfamiliar recent input feels
    * ``trust``              — social trust / attachment proxy
    * ``safety``             — how regulated / safe the context feels
    * ``fatigue``            — context saturation / exhaustion
    * ``need_for_reassurance`` — derived-but-not-random (see updater)
    * ``coherence``          — self-model coherence proxy
    """

    uncertainty: float = 0.0
    novelty: float = 0.0
    trust: float = 0.5
    safety: float = 1.0
    fatigue: float = 0.0
    need_for_reassurance: float = 0.0
    coherence: float = 1.0
    updated_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    update_count: int = 0

    # ── accessors / serialisation ──

    def as_dict(self) -> Dict[str, float]:
        return {
            "uncertainty": self.uncertainty,
            "novelty": self.novelty,
            "trust": self.trust,
            "safety": self.safety,
            "fatigue": self.fatigue,
            "need_for_reassurance": self.need_for_reassurance,
            "coherence": self.coherence,
        }

    def to_log_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {**self.as_dict()}
        d["updated_ms"] = self.updated_ms
        d["update_count"] = self.update_count
        return d

    def snapshot(self) -> "SelfState":
        """Return an independent copy."""
        return SelfState(
            uncertainty=self.uncertainty,
            novelty=self.novelty,
            trust=self.trust,
            safety=self.safety,
            fatigue=self.fatigue,
            need_for_reassurance=self.need_for_reassurance,
            coherence=self.coherence,
            updated_ms=self.updated_ms,
            update_count=self.update_count,
        )


# ── updater ─────────────────────────────────────────────────


@dataclass
class SelfStateUpdater:
    """Deterministic state-transition rules.

    The updater exposes small, auditable methods — each accepts a
    :class:`SelfState` and mutates it in place. Every rule is
    documented in the docstring AND enforced by a test in
    test_mama_self_state.py.

    Absolutely no random numbers.
    """

    # per-step decay towards baseline, used by tick()
    uncertainty_decay: float = 0.05
    novelty_decay: float = 0.1
    fatigue_recovery_rate: float = 0.02
    trust_baseline: float = 0.5
    trust_drift: float = 0.01
    coherence_recovery_rate: float = 0.02

    # response magnitudes
    surprise_uncertainty_gain: float = 0.2
    surprise_novelty_gain: float = 0.25
    stress_safety_drop: float = 0.3
    soothing_safety_gain: float = 0.2
    soothing_trust_gain: float = 0.1
    neglect_trust_drop: float = 0.15
    fatigue_gain_per_event: float = 0.03

    # ── update entry points ──

    def on_surprise(self, state: SelfState, magnitude: float = 1.0) -> None:
        """Caller observed something unexpected.

        ``uncertainty`` and ``novelty`` increase proportional to
        ``magnitude``; ``coherence`` drops a little; ``need_for_reassurance``
        is derived.
        """
        m = max(0.0, float(magnitude))
        state.uncertainty = _clamp01(state.uncertainty + self.surprise_uncertainty_gain * m)
        state.novelty = _clamp01(state.novelty + self.surprise_novelty_gain * m)
        state.coherence = _clamp01(state.coherence - 0.05 * m)
        self._derive_reassurance_need(state)
        self._touch(state)

    def on_stress(self, state: SelfState, magnitude: float = 1.0) -> None:
        """Safety drops, uncertainty rises, need_for_reassurance rises.

        This is the "help me" pathway. A stressed state is where
        grounded mama events become possible.
        """
        m = max(0.0, float(magnitude))
        state.safety = _clamp01(state.safety - self.stress_safety_drop * m)
        state.uncertainty = _clamp01(state.uncertainty + 0.1 * m)
        self._derive_reassurance_need(state)
        self._touch(state)

    def on_soothing(self, state: SelfState, magnitude: float = 1.0) -> None:
        """Caregiver interaction soothed the state.

        * safety recovers
        * trust rises towards 1
        * need_for_reassurance falls
        * uncertainty drops slightly
        """
        m = max(0.0, float(magnitude))
        state.safety = _clamp01(state.safety + self.soothing_safety_gain * m)
        state.trust = _clamp01(state.trust + self.soothing_trust_gain * m)
        state.uncertainty = _clamp01(state.uncertainty - 0.05 * m)
        self._derive_reassurance_need(state)
        self._touch(state)

    def on_neglect(self, state: SelfState, magnitude: float = 1.0) -> None:
        """Caregiver did not respond when help was sought.

        Trust drops; need_for_reassurance stays high or rises.
        """
        m = max(0.0, float(magnitude))
        state.trust = _clamp01(state.trust - self.neglect_trust_drop * m)
        state.need_for_reassurance = _clamp01(
            state.need_for_reassurance + 0.1 * m
        )
        self._touch(state)

    def on_memory_recall(self, state: SelfState, was_caregiver: bool) -> None:
        """A memory was surfaced in this turn.

        Caregiver recalls drop uncertainty and raise trust slightly.
        Non-caregiver recalls simply tick ``update_count``.
        """
        if was_caregiver:
            state.uncertainty = _clamp01(state.uncertainty - 0.05)
            state.trust = _clamp01(state.trust + 0.05)
            state.coherence = _clamp01(state.coherence + 0.02)
            self._derive_reassurance_need(state)
        self._touch(state)

    def tick(self, state: SelfState, dt_seconds: float = 1.0) -> None:
        """Per-tick decay/recovery pass.

        * uncertainty decays toward 0
        * novelty decays toward 0
        * trust drifts toward baseline
        * fatigue recovers a little
        * coherence recovers toward 1
        * need_for_reassurance is recomputed
        """
        dt = max(0.0, float(dt_seconds))
        if dt == 0.0:
            return
        state.uncertainty = _clamp01(state.uncertainty - self.uncertainty_decay * dt)
        state.novelty = _clamp01(state.novelty - self.novelty_decay * dt)
        state.fatigue = _clamp01(state.fatigue - self.fatigue_recovery_rate * dt)
        state.coherence = _clamp01(state.coherence + self.coherence_recovery_rate * dt)
        # trust drifts towards baseline
        if state.trust > self.trust_baseline:
            state.trust = _clamp01(state.trust - self.trust_drift * dt)
        elif state.trust < self.trust_baseline:
            state.trust = _clamp01(state.trust + self.trust_drift * dt)
        self._derive_reassurance_need(state)
        self._touch(state)

    def on_event_load(self, state: SelfState, count: int = 1) -> None:
        """Increase fatigue proportional to processed events.

        Pure bookkeeping, no stress implied — stress goes through
        :meth:`on_stress` explicitly.
        """
        c = max(0, int(count))
        state.fatigue = _clamp01(state.fatigue + self.fatigue_gain_per_event * c)
        self._touch(state)

    # ── internal ──

    def _derive_reassurance_need(self, state: SelfState) -> None:
        """need_for_reassurance = f(uncertainty, 1-safety, 1-trust).

        Documented weighted sum — *not* random. The maximum of the
        three dominant components is used rather than a plain mean
        so a single acute dimension (safety crash) still surfaces
        the need.
        """
        unc = state.uncertainty
        lack_safety = 1.0 - state.safety
        lack_trust = 1.0 - state.trust
        weighted_mean = (0.4 * unc + 0.4 * lack_safety + 0.2 * lack_trust)
        acute_max = max(unc, lack_safety, lack_trust)
        state.need_for_reassurance = _clamp01(max(weighted_mean, acute_max * 0.9))

    def _touch(self, state: SelfState) -> None:
        state.updated_ms = int(time.time() * 1000)
        state.update_count += 1
