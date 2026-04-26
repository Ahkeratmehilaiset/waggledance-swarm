"""Abstract pattern registry — Phase 9 §Q.

Append-only registry of cross-capsule observations. Each record is
deterministic in its observation_id and carries a redacted
participating_capsule_ids list — never raw capsule content.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Iterable

from .cross_capsule_observer import (
    CrossCapsuleObservation,
    OBSERVABLE_PATTERN_KINDS,
    compute_observation_id,
)


class AbstractPatternRegistryError(ValueError):
    """Raised on duplicate id, unknown kind, or invariant violation."""


@dataclass(frozen=True)
class AbstractPatternRecord:
    observation_id: str
    pattern_kind: str
    participating_capsule_ids: tuple[str, ...]
    occurrence_count: int
    window_id: str
    redacted: bool
    advisory_only: bool
    no_raw_data_leakage: bool
    no_runtime_auto_action: bool

    def __post_init__(self) -> None:
        if self.pattern_kind not in OBSERVABLE_PATTERN_KINDS:
            raise AbstractPatternRegistryError(
                f"unknown pattern kind {self.pattern_kind!r}"
            )
        for flag, name in (
            (self.redacted, "redacted"),
            (self.advisory_only, "advisory_only"),
            (self.no_raw_data_leakage, "no_raw_data_leakage"),
            (self.no_runtime_auto_action, "no_runtime_auto_action"),
        ):
            if flag is not True:
                raise AbstractPatternRegistryError(
                    f"{name} must be True"
                )

    def to_dict(self) -> dict:
        return {
            "observation_id": self.observation_id,
            "pattern_kind": self.pattern_kind,
            "participating_capsule_ids": list(
                self.participating_capsule_ids),
            "occurrence_count": self.occurrence_count,
            "window_id": self.window_id,
            "redacted": self.redacted,
            "advisory_only": self.advisory_only,
            "no_raw_data_leakage": self.no_raw_data_leakage,
            "no_runtime_auto_action": self.no_runtime_auto_action,
        }


@dataclass
class AbstractPatternRegistry:
    records: list[AbstractPatternRecord] = field(default_factory=list)
    _seen_ids: set[str] = field(default_factory=set)

    def add(self, observation: CrossCapsuleObservation
              ) -> AbstractPatternRecord:
        oid = compute_observation_id(observation)
        if oid in self._seen_ids:
            raise AbstractPatternRegistryError(
                f"duplicate observation_id {oid!r}"
            )
        rec = AbstractPatternRecord(
            observation_id=oid,
            pattern_kind=observation.pattern_kind,
            participating_capsule_ids=observation.participating_capsule_ids,
            occurrence_count=observation.occurrence_count,
            window_id=observation.window_id,
            redacted=True,
            advisory_only=True,
            no_raw_data_leakage=True,
            no_runtime_auto_action=True,
        )
        self.records.append(rec)
        self._seen_ids.add(oid)
        return rec

    def by_kind(self, pattern_kind: str) -> tuple[AbstractPatternRecord, ...]:
        if pattern_kind not in OBSERVABLE_PATTERN_KINDS:
            raise AbstractPatternRegistryError(
                f"unknown pattern_kind {pattern_kind!r}"
            )
        return tuple(r for r in self.records if r.pattern_kind == pattern_kind)

    def to_dict(self) -> dict:
        return {
            "records": [r.to_dict() for r in self.records],
            "no_raw_data_leakage_invariant": True,
            "no_runtime_auto_action_invariant": True,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, indent=2)
