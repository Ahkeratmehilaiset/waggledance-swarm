# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Runtime query router (Phase 13 P2).

This is the **narrow runtime seam** that connects real query traffic
to the Phase 12 self-starting autogrowth loop. Existing autonomy
runtime code can call ``RuntimeQueryRouter.route(query)`` instead of
a bare ``LowRiskSolverDispatcher.dispatch(...)``: the router dispatches
to auto-promoted low-risk solvers (built-ins still take precedence by
contract — they sit in the calling layer, not here) and, on miss,
emits a bounded, deduped gap signal automatically.

Design constraints:

* Built-in authoritative solvers are NOT this module's concern. The
  caller (e.g. a higher-level orchestrator) invokes built-ins first
  and only falls through to the router for residuals. The router is
  the "between built-ins and LLM fallback" lane on the call stack.
* Bounded harvest: an in-memory throttle prevents one DB write per
  query in a hot loop. Per (family_kind, intent_seed) pair, at most
  one signal is recorded inside ``min_signal_interval_seconds``.
  Throttled queries still report ``served=False`` so the caller can
  fall through to whatever it does next; they just don't write.
* Domain-neutral. No mention of any specific runtime call site.
* No actuator / policy / production-runtime-config writes (RULE 10).

The router is purely synchronous. A scheduler tick is the consumer.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from waggledance.core.storage.control_plane import ControlPlaneDB

from .gap_intake import GapSignal, RuntimeGapDetector
from .low_risk_policy import is_low_risk_family
from .solver_dispatcher import (
    DispatchQuery,
    DispatchResult,
    LowRiskSolverDispatcher,
)


@dataclass(frozen=True)
class RuntimeQuery:
    """Structured query envelope handed to the router."""

    family_kind: str
    inputs: Mapping[str, Any]
    cell_coord: Optional[str] = None
    intent_seed: Optional[str] = None
    features: Optional[Mapping[str, Any]] = None  # capability-aware hints
    spec_seed: Optional[Mapping[str, Any]] = None
    weight: float = 1.0


@dataclass(frozen=True)
class RuntimeRouteResult:
    served: bool
    source: str  # 'auto_promoted_solver' | 'gap_emitted'
                  # | 'gap_throttled' | 'family_not_low_risk'
    output: Any = None
    solver_id: Optional[int] = None
    solver_name: Optional[str] = None
    artifact_id: Optional[str] = None
    signal_id: Optional[int] = None
    miss_reason: Optional[str] = None
    error: Optional[str] = None


@dataclass
class RouterStats:
    queries_total: int = 0
    served_total: int = 0
    miss_total: int = 0
    throttled_total: int = 0
    out_of_envelope_total: int = 0
    by_family_served: dict[str, int] = field(default_factory=dict)
    by_family_emitted: dict[str, int] = field(default_factory=dict)


_DEFAULT_MIN_SIGNAL_INTERVAL_SECONDS: float = 1.0


class RuntimeQueryRouter:
    """Runtime seam for the low-risk autogrowth lane.

    Wraps :class:`LowRiskSolverDispatcher` and :class:`RuntimeGapDetector`
    into a single ``route(query)`` entry point. Callers in the runtime
    invoke this *after* their authoritative built-in solvers, *before*
    any LLM fallback. On miss the router emits a bounded, deduped
    runtime_gap_signal so the autogrowth scheduler can pick it up on
    the next tick.

    The router is thread-safe: the throttle map is guarded by a lock.
    """

    def __init__(
        self,
        control_plane: ControlPlaneDB,
        *,
        dispatcher: Optional[LowRiskSolverDispatcher] = None,
        detector: Optional[RuntimeGapDetector] = None,
        min_signal_interval_seconds: float = _DEFAULT_MIN_SIGNAL_INTERVAL_SECONDS,
        clock: Optional[Any] = None,
    ) -> None:
        self._cp = control_plane
        self._dispatcher = (
            dispatcher
            if dispatcher is not None
            else LowRiskSolverDispatcher(control_plane)
        )
        self._detector = (
            detector
            if detector is not None
            else RuntimeGapDetector(control_plane)
        )
        self._min_interval = float(min_signal_interval_seconds)
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._last_signal_at: dict[tuple[str, str], float] = {}
        self._stats = RouterStats()

    @property
    def stats(self) -> RouterStats:
        return self._stats

    def reset_stats(self) -> None:
        self._stats = RouterStats()

    @property
    def dispatcher(self) -> LowRiskSolverDispatcher:
        return self._dispatcher

    @property
    def detector(self) -> RuntimeGapDetector:
        return self._detector

    def route(self, query: RuntimeQuery) -> RuntimeRouteResult:
        self._stats.queries_total += 1
        family = query.family_kind

        if not is_low_risk_family(family):
            self._stats.out_of_envelope_total += 1
            return RuntimeRouteResult(
                served=False,
                source="family_not_low_risk",
                miss_reason="family_not_low_risk",
            )

        # Capability-aware dispatch first; gracefully degrade to the
        # family-FIFO path if no features were supplied or no
        # feature-matched solver exists yet. dispatch_by_features is
        # added in Phase 13 P3; until then ``hasattr`` lets this
        # module work both before and after that change.
        result: Optional[DispatchResult] = None
        if query.features and hasattr(
            self._dispatcher, "dispatch_by_features"
        ):
            result = self._dispatcher.dispatch_by_features(  # type: ignore[attr-defined]
                family_kind=family,
                features=dict(query.features),
                inputs=dict(query.inputs),
            )
        if result is None or not result.matched:
            # Family-FIFO fallback (Phase 11/12 behavior preserved).
            result = self._dispatcher.dispatch(DispatchQuery(
                family_kind=family,
                inputs=dict(query.inputs),
            ))

        if result.matched:
            self._stats.served_total += 1
            self._stats.by_family_served[family] = (
                self._stats.by_family_served.get(family, 0) + 1
            )
            return RuntimeRouteResult(
                served=True,
                source="auto_promoted_solver",
                output=result.output,
                solver_id=result.solver_id,
                solver_name=result.solver_name,
                artifact_id=result.artifact_id,
            )

        # Miss path → emit a deduped runtime gap signal.
        self._stats.miss_total += 1
        intent_seed = query.intent_seed or self._derive_intent_seed(query)
        throttle_key = (family, intent_seed)
        now = self._clock()
        emit = True
        with self._lock:
            last = self._last_signal_at.get(throttle_key)
            if last is not None and (now - last) < self._min_interval:
                emit = False
            else:
                self._last_signal_at[throttle_key] = now

        if not emit:
            self._stats.throttled_total += 1
            return RuntimeRouteResult(
                served=False,
                source="gap_throttled",
                miss_reason=result.reason or "miss_no_solver",
            )

        signal_payload = {
            "family_kind": family,
            "cell_coord": query.cell_coord,
            "intent_seed": intent_seed,
            "miss_reason": result.reason,
        }
        signal = GapSignal(
            kind="runtime_miss",
            family_kind=family,
            cell_coord=query.cell_coord,
            intent_seed=intent_seed,
            weight=query.weight,
            payload=signal_payload,
            spec_seed=dict(query.spec_seed) if query.spec_seed else None,
        )
        rec = self._detector.record(signal)
        self._stats.by_family_emitted[family] = (
            self._stats.by_family_emitted.get(family, 0) + 1
        )
        return RuntimeRouteResult(
            served=False,
            source="gap_emitted",
            signal_id=rec.id,
            miss_reason=result.reason or "miss_no_solver",
        )

    @staticmethod
    def _derive_intent_seed(query: RuntimeQuery) -> str:
        """Derive a stable intent_seed when the caller didn't supply one.

        Strategy: hash the structured features (or the inputs if no
        features were supplied) into a short suffix. We deliberately
        do NOT hash the actual values; we hash the *shape* (sorted
        keys + value-type tags) so different runtime values for the
        same query class collapse to one intent.
        """

        if query.features:
            keys = sorted(str(k) for k in query.features.keys())
        else:
            keys = sorted(str(k) for k in query.inputs.keys())
        return "::".join(keys) or "_default"


__all__ = [
    "RouterStats",
    "RuntimeQuery",
    "RuntimeQueryRouter",
    "RuntimeRouteResult",
]
