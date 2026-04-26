"""Cross-capsule observer — Phase 9 §Q.

Observes ABSTRACT signal summaries from multiple capsules and detects
recurring structural patterns that span capsule boundaries. The
observer:

- consumes only `CapsuleSignalSummary` records (no raw capsule text)
- emits redacted `CrossCapsuleObservation` records that name the
  pattern kind, the participating capsule_ids, and an integer
  occurrence count — never the underlying content
- is fully deterministic (sorted inputs → sorted outputs)
- never imports a runtime/LLM/HTTP/retrieval client

This is the no-raw-data-leakage guarantee from Phase 9 §Q.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Sequence


OBSERVABLE_PATTERN_KINDS = (
    "recurring_oscillation",
    "recurring_blind_spot_class",
    "recurring_proposal_bottleneck",
    "recurring_contradiction_pattern",
)


class CrossCapsuleObserverError(ValueError):
    """Raised when an input or invariant is violated."""


@dataclass(frozen=True)
class CapsuleSignalSummary:
    """A redacted, structural summary of one capsule's recent signals.

    Carries pattern *kinds* the capsule has emitted recently. Does NOT
    carry raw text, raw inferences, or per-IR ids. Treat each kind as
    "this capsule has surfaced this kind of pattern in this window".
    """

    capsule_id: str
    pattern_kinds: tuple[str, ...]
    window_id: str
    redacted: bool

    def __post_init__(self) -> None:
        if not self.capsule_id:
            raise CrossCapsuleObserverError("capsule_id required")
        if not self.window_id:
            raise CrossCapsuleObserverError("window_id required")
        if self.redacted is not True:
            raise CrossCapsuleObserverError(
                "summary must be redacted=True (no raw data)"
            )
        for k in self.pattern_kinds:
            if k not in OBSERVABLE_PATTERN_KINDS:
                raise CrossCapsuleObserverError(
                    f"unknown pattern kind {k!r}"
                )

    def to_dict(self) -> dict:
        return {
            "capsule_id": self.capsule_id,
            "pattern_kinds": list(self.pattern_kinds),
            "window_id": self.window_id,
            "redacted": self.redacted,
        }


@dataclass(frozen=True)
class CrossCapsuleObservation:
    pattern_kind: str
    participating_capsule_ids: tuple[str, ...]
    occurrence_count: int
    window_id: str
    redacted: bool
    advisory_only: bool
    no_raw_data_leakage: bool

    def __post_init__(self) -> None:
        if self.pattern_kind not in OBSERVABLE_PATTERN_KINDS:
            raise CrossCapsuleObserverError(
                f"unknown pattern kind {self.pattern_kind!r}"
            )
        if self.occurrence_count <= 0:
            raise CrossCapsuleObserverError(
                "occurrence_count must be > 0"
            )
        if self.redacted is not True:
            raise CrossCapsuleObserverError("redacted must be True")
        if self.advisory_only is not True:
            raise CrossCapsuleObserverError("advisory_only must be True")
        if self.no_raw_data_leakage is not True:
            raise CrossCapsuleObserverError(
                "no_raw_data_leakage must be True"
            )

    def to_dict(self) -> dict:
        return {
            "pattern_kind": self.pattern_kind,
            "participating_capsule_ids": list(
                self.participating_capsule_ids),
            "occurrence_count": self.occurrence_count,
            "window_id": self.window_id,
            "redacted": self.redacted,
            "advisory_only": self.advisory_only,
            "no_raw_data_leakage": self.no_raw_data_leakage,
        }


def compute_observation_id(obs: CrossCapsuleObservation) -> str:
    canon = json.dumps({
        "pattern_kind": obs.pattern_kind,
        "participating_capsule_ids": sorted(obs.participating_capsule_ids),
        "window_id": obs.window_id,
    }, sort_keys=True).encode("utf-8")
    return hashlib.sha256(canon).hexdigest()[:12]


@dataclass
class CrossCapsuleObserver:
    min_capsules_for_pattern: int = 2

    def observe(self, summaries: Sequence[CapsuleSignalSummary]
                 ) -> tuple[CrossCapsuleObservation, ...]:
        if not summaries:
            return ()

        # Validate that all summaries belong to the same window —
        # cross-window observation is a separate operation.
        windows = {s.window_id for s in summaries}
        if len(windows) != 1:
            raise CrossCapsuleObserverError(
                f"summaries span multiple windows: {sorted(windows)!r}"
            )
        window_id = next(iter(windows))

        # Bucket capsule_ids by pattern kind. Sorting at every step
        # keeps the output deterministic.
        kind_to_capsules: dict[str, list[str]] = defaultdict(list)
        for s in summaries:
            for k in s.pattern_kinds:
                kind_to_capsules[k].append(s.capsule_id)

        observations: list[CrossCapsuleObservation] = []
        for kind in sorted(kind_to_capsules):
            ids = sorted(set(kind_to_capsules[kind]))
            if len(ids) < self.min_capsules_for_pattern:
                continue
            observations.append(CrossCapsuleObservation(
                pattern_kind=kind,
                participating_capsule_ids=tuple(ids),
                occurrence_count=len(kind_to_capsules[kind]),
                window_id=window_id,
                redacted=True,
                advisory_only=True,
                no_raw_data_leakage=True,
            ))
        return tuple(observations)
