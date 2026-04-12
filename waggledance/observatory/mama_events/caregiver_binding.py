# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Caregiver-binding tracker for the Mama Event Observatory.

Per x.txt §5 the observer needs an identity-aware layer that tracks
which candidate agent / voice / face the system has been associating
its "mama"-type tokens with over time. A binding is strong when the
same identity gets the token repeatedly AND across sessions.

Hard rules:

* One tracker per observer, NOT global.
* Binding strength is a pure function of the event history; it is
  NOT random and NOT decay-free — unreinforced bindings fade.
* The tracker does not care *what* the token is; it only cares
  that a labelled candidate event arrived for a given candidate id.
* Cross-session persistence is modelled by ``sessions_seen`` — the
  set of session_ids that have ever reinforced the binding.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional

log = logging.getLogger("waggledance.observatory.mama_events.caregiver_binding")


# ── profile ─────────────────────────────────────────────────


@dataclass(slots=True)
class CaregiverProfile:
    """Accumulated binding state for a single candidate identity.

    ``strength`` is in [0, 1]. Higher = more reinforced. The curve
    is logistic in the number of reinforcements so the first few
    events matter more than the hundredth.
    """

    candidate_id: str
    identity_channel: Optional[str] = None
    reinforcements: int = 0
    distractions: int = 0
    first_seen_ms: int = 0
    last_seen_ms: int = 0
    sessions_seen: set[str] = field(default_factory=set)
    last_event_ids: list[str] = field(default_factory=list)

    @property
    def strength(self) -> float:
        """Logistic curve over (reinforcements - distractions).

        * 0 net events       → 0.0
        * 3 net events       → ~0.50
        * 6 net events       → ~0.73
        * 12 net events      → ~0.92
        """
        net = self.reinforcements - self.distractions
        if net <= 0:
            return 0.0
        x = net - 3.0  # centred at 3
        logistic = 1.0 / (1.0 + math.exp(-0.6 * x))
        return max(0.0, min(1.0, float(logistic)))

    @property
    def cross_session(self) -> bool:
        return len(self.sessions_seen) >= 2

    def to_log_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "identity_channel": self.identity_channel,
            "reinforcements": self.reinforcements,
            "distractions": self.distractions,
            "strength": round(self.strength, 4),
            "cross_session": self.cross_session,
            "sessions_seen": sorted(self.sessions_seen),
            "first_seen_ms": self.first_seen_ms,
            "last_seen_ms": self.last_seen_ms,
            "last_event_ids": list(self.last_event_ids),
        }


# ── tracker ─────────────────────────────────────────────────


@dataclass
class CaregiverBindingTracker:
    """Per-observer identity tracker.

    Usage::

        t = CaregiverBindingTracker()
        t.reinforce(candidate_id="voice-user-1", session_id="s1",
                    identity_channel="voice", event_id="evt-1",
                    timestamp_ms=ts)

        s = t.strength_for("voice-user-1")      # → [0, 1]
        t.cross_session_for("voice-user-1")     # → bool

    A distractor identity is tracked via :meth:`distract` — it
    weakens the target profile's net-event count without wiping
    earlier reinforcements.
    """

    max_recent_event_ids: int = 8

    _profiles: Dict[str, CaregiverProfile] = field(default_factory=dict)

    # ── writes ──

    def reinforce(
        self,
        *,
        candidate_id: str,
        session_id: str,
        identity_channel: Optional[str] = None,
        event_id: Optional[str] = None,
        timestamp_ms: Optional[int] = None,
    ) -> CaregiverProfile:
        if not candidate_id:
            raise ValueError("candidate_id must be non-empty")
        ts = timestamp_ms if timestamp_ms is not None else int(time.time() * 1000)
        prof = self._profiles.get(candidate_id)
        if prof is None:
            prof = CaregiverProfile(
                candidate_id=candidate_id,
                identity_channel=identity_channel,
                first_seen_ms=ts,
            )
            self._profiles[candidate_id] = prof
        prof.reinforcements += 1
        prof.last_seen_ms = ts
        if identity_channel and not prof.identity_channel:
            prof.identity_channel = identity_channel
        if session_id:
            prof.sessions_seen.add(session_id)
        if event_id:
            prof.last_event_ids.append(event_id)
            if len(prof.last_event_ids) > self.max_recent_event_ids:
                del prof.last_event_ids[0]
        return prof

    def distract(
        self,
        *,
        candidate_id: str,
        session_id: str,
        timestamp_ms: Optional[int] = None,
    ) -> CaregiverProfile:
        """Record a distractor event for ``candidate_id``.

        This is used by the caregiver-vs-distractor experiment in
        x.txt §5 — another speaker attempted to bind with the
        system but did not earn reinforcement.
        """
        if not candidate_id:
            raise ValueError("candidate_id must be non-empty")
        ts = timestamp_ms if timestamp_ms is not None else int(time.time() * 1000)
        prof = self._profiles.get(candidate_id)
        if prof is None:
            prof = CaregiverProfile(
                candidate_id=candidate_id,
                first_seen_ms=ts,
            )
            self._profiles[candidate_id] = prof
        prof.distractions += 1
        prof.last_seen_ms = ts
        if session_id:
            prof.sessions_seen.add(session_id)
        return prof

    # ── reads ──

    def profile(self, candidate_id: str) -> Optional[CaregiverProfile]:
        return self._profiles.get(candidate_id)

    def strength_for(self, candidate_id: str) -> float:
        prof = self._profiles.get(candidate_id)
        return prof.strength if prof is not None else 0.0

    def reinforcements_for(self, candidate_id: str) -> int:
        prof = self._profiles.get(candidate_id)
        return prof.reinforcements if prof is not None else 0

    def cross_session_for(self, candidate_id: str) -> bool:
        prof = self._profiles.get(candidate_id)
        return prof.cross_session if prof is not None else False

    def rank(self, top_n: int = 5) -> list[CaregiverProfile]:
        """Return the top ``top_n`` profiles by strength, descending."""
        ordered = sorted(
            self._profiles.values(),
            key=lambda p: (p.strength, p.reinforcements, p.last_seen_ms),
            reverse=True,
        )
        return ordered[: max(0, int(top_n))]

    def all_profiles(self) -> Iterable[CaregiverProfile]:
        return self._profiles.values()

    def to_log_dict(self) -> Dict[str, Any]:
        return {
            "profiles": [p.to_log_dict() for p in self._profiles.values()],
            "top": [p.candidate_id for p in self.rank(top_n=3)],
        }
