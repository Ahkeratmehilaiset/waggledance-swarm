# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Memory consolidation + deterministic replay for the Mama Event Observatory.

Per x.txt §7 the observer needs a way for candidate events to
influence later behaviour: episodic memory writes, caregiver
salience tracking, a consolidation pass, and a replay path.

Hard rules:

* Consolidation is deterministic given an ordered list of episodic
  records. No randomness, no wall-clock dependence in the pure
  functions.
* The episodic store is bounded (``max_records``) so long-running
  observers do not accumulate unbounded state.
* Replay must reproduce the consolidated salience exactly when fed
  the same records with the same seed.

This module ships no I/O. Persistence to disk is the observer's
job and happens through the NDJSON logger.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

log = logging.getLogger("waggledance.observatory.mama_events.consolidation")


# ── episodic record ────────────────────────────────────────


@dataclass(slots=True)
class EpisodicRecord:
    """A single consolidation-ready record.

    Only the minimum needed to influence later behaviour. Raw text
    and PII do NOT belong here — those stay in the NDJSON audit log.
    """

    event_id: str
    timestamp_ms: int
    session_id: str
    caregiver_candidate_id: Optional[str]
    score_total: int
    score_band: str
    self_state_delta: Dict[str, float] = field(default_factory=dict)
    contamination_flags: list[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "timestamp_ms": self.timestamp_ms,
            "session_id": self.session_id,
            "caregiver_candidate_id": self.caregiver_candidate_id,
            "score_total": int(self.score_total),
            "score_band": self.score_band,
            "self_state_delta": {k: float(v) for k, v in self.self_state_delta.items()},
            "contamination_flags": list(self.contamination_flags),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EpisodicRecord":
        return cls(
            event_id=str(data["event_id"]),
            timestamp_ms=int(data["timestamp_ms"]),
            session_id=str(data.get("session_id") or ""),
            caregiver_candidate_id=data.get("caregiver_candidate_id"),
            score_total=int(data.get("score_total", 0)),
            score_band=str(data.get("score_band", "artifact_or_parrot")),
            self_state_delta={
                k: float(v) for k, v in (data.get("self_state_delta") or {}).items()
            },
            contamination_flags=list(data.get("contamination_flags") or []),
        )


# ── store ──────────────────────────────────────────────────


@dataclass
class EpisodicStore:
    """Bounded in-memory episodic store with deterministic consolidation.

    The store behaves as a FIFO queue capped at ``max_records``.
    ``consolidate`` produces a caregiver-salience map from the
    current contents; ``replay`` produces the same map from a
    supplied (possibly serialised) record list — this is what the
    observer test harness uses to validate reproducibility.
    """

    max_records: int = 1024
    _records: list[EpisodicRecord] = field(default_factory=list)

    # ── writes ──

    def write(self, record: EpisodicRecord) -> None:
        """Append ``record``, dropping the oldest if the cap is exceeded."""
        if record.score_total < 0:
            raise ValueError("score_total must be non-negative")
        self._records.append(record)
        if len(self._records) > self.max_records:
            del self._records[0 : len(self._records) - self.max_records]

    def clear(self) -> None:
        self._records.clear()

    # ── reads ──

    def __len__(self) -> int:
        return len(self._records)

    def records(self) -> Sequence[EpisodicRecord]:
        return tuple(self._records)

    def count_by_band(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for r in self._records:
            counts[r.score_band] = counts.get(r.score_band, 0) + 1
        return counts

    # ── consolidation ──

    def consolidate(self) -> Dict[str, float]:
        """Return a deterministic caregiver-salience map.

        ``salience[candidate_id]`` is a weighted sum of the event
        score totals (clamped at 100), favouring recent high-score
        events. The return is normalised so the maximum value is
        1.0 (or 0.0 if the store is empty).
        """
        return _consolidate_records(self._records)

    def preferred_caregiver(self) -> Optional[str]:
        salience = self.consolidate()
        if not salience:
            return None
        return max(salience.items(), key=lambda kv: (kv[1], kv[0]))[0]

    def serialise(self) -> list[Dict[str, Any]]:
        return [r.to_dict() for r in self._records]

    @classmethod
    def from_serialised(cls, data: Iterable[Mapping[str, Any]], *, max_records: int = 1024) -> "EpisodicStore":
        s = cls(max_records=max_records)
        for row in data:
            s.write(EpisodicRecord.from_dict(row))
        return s

    # ── determinism hash (used by replay tests) ──

    def fingerprint(self) -> str:
        payload = json.dumps(self.serialise(), sort_keys=True).encode("utf-8")
        return hashlib.sha1(payload).hexdigest()[:16]


# ── pure consolidation function ────────────────────────────


def _consolidate_records(records: Sequence[EpisodicRecord]) -> Dict[str, float]:
    """Pure: same input → same output, no I/O, no randomness.

    Weighted sum where:

    * base weight = score_total / 100  (clamped to [0, 1])
    * recency boost: newer records are amplified by (idx / N) * 0.5
      — so the first record contributes weight, the last contributes
      weight * 1.5 — rewarding fresh reinforcement
    * contamination-flagged records contribute half
    * entries with no caregiver candidate are ignored

    Finally the map is normalised so its maximum is 1.0.
    """
    if not records:
        return {}
    n = len(records)
    raw: Dict[str, float] = {}
    for idx, r in enumerate(records):
        if not r.caregiver_candidate_id:
            continue
        base = max(0.0, min(1.0, float(r.score_total) / 100.0))
        recency_boost = 1.0 + (idx / max(1, n - 1)) * 0.5 if n > 1 else 1.0
        contamination_factor = 0.5 if r.contamination_flags else 1.0
        contribution = base * recency_boost * contamination_factor
        raw[r.caregiver_candidate_id] = raw.get(r.caregiver_candidate_id, 0.0) + contribution
    if not raw:
        return {}
    peak = max(raw.values())
    if peak <= 0.0:
        return {k: 0.0 for k in raw}
    return {k: round(v / peak, 6) for k, v in raw.items()}


# ── deterministic replay ───────────────────────────────────


def replay(records: Sequence[Mapping[str, Any]]) -> Dict[str, float]:
    """Given a serialised record list (same shape as :meth:`EpisodicStore.serialise`),
    return the consolidated salience map. Used by the ablation harness
    and the gate regression test to prove that the consolidation pass
    is reproducible from disk.
    """
    parsed = [EpisodicRecord.from_dict(row) for row in records]
    return _consolidate_records(parsed)
