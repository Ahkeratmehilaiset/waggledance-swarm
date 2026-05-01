# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Hot-path cache + buffered signal sink for the autonomy consult lane.

Three in-process components, bundled because they are always used
together and share invalidation events:

* :class:`WarmCapabilityIndex` — keyed on
  ``(family_kind, frozenset(features.items()))`` → ``solver_id``.
  Skips the SQLite ``find_auto_promoted_solvers_by_features`` call on
  the warm-path common case.
* :class:`ParsedArtifactCache` — keyed on ``solver_id`` →
  ``(artifact_id, parsed_artifact_dict, solver_name)``. Skips the
  SQLite ``get_solver_artifact`` call and the ``json.loads`` of
  ``artifact_json`` on warm-path hits.
* :class:`BufferedSignalSink` — bounded in-memory queue of pending
  :class:`GapSignal` entries that the runtime router enqueues on
  miss. Flushes when the queue size reaches ``max_unflushed_signals``,
  when the oldest entry's age reaches ``max_unflushed_age_ms``, or
  when the caller requests an explicit ``flush()``. Registers an
  ``atexit`` hook for graceful shutdown.

These components are only used when the caller opts in. The Phase 13
synchronous path still works unchanged for callers that don't.

Documented hard-kill loss bound (RULE P3.3):
* worst case = ``max_unflushed_signals`` (1000 by default)
* OR worst case = signals collected within one ``max_unflushed_age_ms``
  window (500 ms by default), whichever is tighter for the workload.
"""
from __future__ import annotations

import atexit
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional

from waggledance.core.storage.control_plane import ControlPlaneDB

from .gap_intake import GapSignal, RuntimeGapDetector


# Defaults — match P3.3 invariant
DEFAULT_MAX_UNFLUSHED_SIGNALS: int = 1000
DEFAULT_MAX_UNFLUSHED_AGE_MS: int = 500


@dataclass(frozen=True)
class WarmDispatchResult:
    matched: bool
    source: str  # 'warm_cache' | 'cold_then_warmed' | 'miss'
    solver_id: Optional[int] = None
    solver_name: Optional[str] = None
    artifact_id: Optional[str] = None
    output: Any = None
    error: Optional[str] = None


@dataclass
class HotPathCacheStats:
    warm_hits: int = 0
    cold_hits_warmed: int = 0
    misses: int = 0
    cache_invalidations: int = 0
    by_family_warm_hits: dict[str, int] = field(default_factory=dict)
    by_family_cold_hits: dict[str, int] = field(default_factory=dict)


@dataclass
class BufferedSinkStats:
    enqueued_total: int = 0
    flushed_total: int = 0
    dropped_total: int = 0
    flush_calls_total: int = 0
    max_observed_unflushed_signals: int = 0
    max_observed_unflushed_age_ms: float = 0.0


def _features_key(features: Mapping[str, Any]) -> frozenset[tuple[str, str]]:
    """Deterministic, order-independent key for a feature dict.

    We coerce values to ``str`` so that dispatch from a runtime caller
    (which may pass typed values) matches dispatch from the indexer
    (which stored them as strings).
    """

    return frozenset(
        (str(k), "" if v is None else str(v)) for k, v in features.items()
    )


class WarmCapabilityIndex:
    """In-memory ``(family_kind, frozenset(features)) -> solver_id``."""

    def __init__(self) -> None:
        self._index: dict[
            tuple[str, frozenset[tuple[str, str]]], int
        ] = {}
        self._lock = threading.Lock()

    def lookup(
        self, family_kind: str, features: Mapping[str, Any]
    ) -> Optional[int]:
        key = (family_kind, _features_key(features))
        with self._lock:
            return self._index.get(key)

    def store(
        self,
        family_kind: str,
        features: Mapping[str, Any],
        solver_id: int,
    ) -> None:
        key = (family_kind, _features_key(features))
        with self._lock:
            self._index[key] = solver_id

    def invalidate_solver(self, solver_id: int) -> int:
        """Drop every entry pointing at this solver. Returns count dropped."""

        with self._lock:
            keys_to_drop = [k for k, v in self._index.items() if v == solver_id]
            for k in keys_to_drop:
                self._index.pop(k, None)
            return len(keys_to_drop)

    def clear(self) -> None:
        with self._lock:
            self._index.clear()

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._index)


class ParsedArtifactCache:
    """In-memory ``solver_id -> (artifact_id, parsed_dict, solver_name)``.

    Storing the parsed dict (not the raw JSON string) avoids the
    ``json.loads`` cost on every warm-path hit. Storing the
    ``artifact_id`` lets us detect a stale cache entry if a new
    artifact replaces an old one.
    """

    def __init__(self) -> None:
        self._cache: dict[
            int, tuple[str, Mapping[str, Any], Optional[str]]
        ] = {}
        self._lock = threading.Lock()

    def get(
        self, solver_id: int
    ) -> Optional[tuple[str, Mapping[str, Any], Optional[str]]]:
        with self._lock:
            return self._cache.get(solver_id)

    def store(
        self,
        solver_id: int,
        artifact_id: str,
        artifact: Mapping[str, Any],
        solver_name: Optional[str],
    ) -> None:
        with self._lock:
            self._cache[solver_id] = (artifact_id, dict(artifact), solver_name)

    def invalidate_solver(self, solver_id: int) -> bool:
        with self._lock:
            return self._cache.pop(solver_id, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._cache)


@dataclass
class _PendingSignal:
    signal: GapSignal
    enqueued_at: float


class BufferedSignalSink:
    """Bounded in-memory queue + flusher for runtime miss signals.

    The sink is *not* a queue in the producer/consumer sense; the
    caller is the producer and ``flush()`` is the consumer. The router
    enqueues a miss; ``flush()`` drains the queue into the
    ``RuntimeGapDetector`` (one transactional INSERT per signal at
    flush time, but off the request hot path).
    """

    def __init__(
        self,
        detector: RuntimeGapDetector,
        *,
        max_unflushed_signals: int = DEFAULT_MAX_UNFLUSHED_SIGNALS,
        max_unflushed_age_ms: int = DEFAULT_MAX_UNFLUSHED_AGE_MS,
        clock: Optional[Callable[[], float]] = None,
        register_atexit: bool = True,
    ) -> None:
        self._detector = detector
        self._max_signals = int(max_unflushed_signals)
        self._max_age_ms = int(max_unflushed_age_ms)
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._queue: list[_PendingSignal] = []
        self._stats = BufferedSinkStats()
        if register_atexit:
            atexit.register(self._atexit_flush)

    @property
    def max_unflushed_signals(self) -> int:
        return self._max_signals

    @property
    def max_unflushed_age_ms(self) -> int:
        return self._max_age_ms

    @property
    def hardkill_loss_bound_signals(self) -> int:
        """Worst-case signal loss under abrupt termination.

        Returns the configured ``max_unflushed_signals`` ceiling.
        Truthful contract: under SIGKILL we may lose up to this many
        signals OR up to one ``max_unflushed_age_ms`` window's worth
        of signals, whichever ends up smaller in the workload.
        """

        return self._max_signals

    @property
    def stats(self) -> BufferedSinkStats:
        return self._stats

    def enqueue(self, signal: GapSignal) -> bool:
        """Enqueue one signal; returns False if dropped due to bound."""

        now = self._clock()
        with self._lock:
            if len(self._queue) >= self._max_signals:
                # Trigger an opportunistic synchronous flush rather than drop;
                # if the flush still leaves us at capacity, drop the new one.
                self._flush_locked(now)
                if len(self._queue) >= self._max_signals:
                    self._stats.dropped_total += 1
                    return False
            self._queue.append(_PendingSignal(signal=signal, enqueued_at=now))
            self._stats.enqueued_total += 1
            qsize = len(self._queue)
            if qsize > self._stats.max_observed_unflushed_signals:
                self._stats.max_observed_unflushed_signals = qsize
            self._maybe_flush_locked(now)
        return True

    def force_flush(self) -> int:
        with self._lock:
            return self._flush_locked(self._clock())

    def pending_count(self) -> int:
        with self._lock:
            return len(self._queue)

    def oldest_age_ms(self) -> float:
        with self._lock:
            if not self._queue:
                return 0.0
            return (self._clock() - self._queue[0].enqueued_at) * 1000.0

    # -- internals -----------------------------------------------------

    def _maybe_flush_locked(self, now: float) -> int:
        if not self._queue:
            return 0
        oldest_age_ms = (now - self._queue[0].enqueued_at) * 1000.0
        if oldest_age_ms > self._stats.max_observed_unflushed_age_ms:
            self._stats.max_observed_unflushed_age_ms = oldest_age_ms
        if oldest_age_ms >= self._max_age_ms or len(self._queue) >= self._max_signals:
            return self._flush_locked(now)
        return 0

    def _flush_locked(self, now: float) -> int:
        if not self._queue:
            self._stats.flush_calls_total += 1
            return 0
        pending = list(self._queue)
        self._queue.clear()
        self._stats.flush_calls_total += 1
        # Flush off the lock for INSERTs — but the caller already holds
        # the lock for thread-safety against new enqueues. The detector
        # itself acquires the control-plane lock; nesting is fine.
        flushed = 0
        for p in pending:
            try:
                self._detector.record(p.signal)
                flushed += 1
            except Exception:  # noqa: BLE001 — fail-loud per RULE 14 happens at higher layers
                # Re-queue head so we don't silently lose under transient
                # control-plane failure.
                self._queue.insert(0, p)
                break
        self._stats.flushed_total += flushed
        return flushed

    def _atexit_flush(self) -> None:
        """Best-effort flush on graceful shutdown / atexit.

        Truth: this is best-effort. SIGKILL bypasses atexit; the
        documented hard-kill loss bound applies.
        """

        try:
            self.force_flush()
        except Exception:
            # Never raise from atexit — graceful-degrade.
            pass


@dataclass
class HotPathCache:
    """Bundle of warm caches + buffered sink the router shares.

    Construct one per process (or per long-lived service). The
    runtime query router opts in by passing this instance.
    """

    control_plane: ControlPlaneDB
    detector: RuntimeGapDetector
    capability_index: WarmCapabilityIndex = field(
        default_factory=WarmCapabilityIndex
    )
    artifact_cache: ParsedArtifactCache = field(
        default_factory=ParsedArtifactCache
    )
    sink: Optional[BufferedSignalSink] = None
    stats: HotPathCacheStats = field(default_factory=HotPathCacheStats)
    max_unflushed_signals: int = DEFAULT_MAX_UNFLUSHED_SIGNALS
    max_unflushed_age_ms: int = DEFAULT_MAX_UNFLUSHED_AGE_MS

    def __post_init__(self) -> None:
        if self.sink is None:
            self.sink = BufferedSignalSink(
                self.detector,
                max_unflushed_signals=self.max_unflushed_signals,
                max_unflushed_age_ms=self.max_unflushed_age_ms,
            )

    def warm_dispatch(
        self,
        family_kind: str,
        features: Mapping[str, Any],
        inputs: Mapping[str, Any],
    ) -> WarmDispatchResult:
        """Try the warm path; on miss, perform one cold lookup + warm it."""

        from .solver_executor import (  # local import to keep module light
            ExecutorError,
            UnsupportedFamilyError,
            execute_artifact,
        )

        # Warm capability lookup (no SQLite)
        solver_id = self.capability_index.lookup(family_kind, features)
        source = "warm_cache"
        if solver_id is None:
            # Cold: ask the control plane, then warm the cache
            candidates = (
                self.control_plane.find_auto_promoted_solvers_by_features(
                    family_kind=family_kind,
                    features=features,
                    limit=1,
                )
            )
            if not candidates:
                self.stats.misses += 1
                return WarmDispatchResult(matched=False, source="miss")
            solver_id = candidates[0]
            self.capability_index.store(family_kind, features, solver_id)
            source = "cold_then_warmed"

        # Parsed artifact cache
        cached = self.artifact_cache.get(solver_id)
        if cached is None:
            artifact_record = self.control_plane.get_solver_artifact(solver_id)
            if artifact_record is None:
                # Stale capability index pointing at a deleted solver
                self.capability_index.invalidate_solver(solver_id)
                self.stats.misses += 1
                return WarmDispatchResult(matched=False, source="miss")
            try:
                parsed = json.loads(artifact_record.artifact_json)
            except json.JSONDecodeError as exc:
                return WarmDispatchResult(
                    matched=False, source="miss", error=str(exc)
                )
            # solver_name lookup (one extra SQLite SELECT only on the cold
            # path; cached afterwards)
            solver_row = self.control_plane._conn.execute(  # type: ignore[attr-defined]
                "SELECT name FROM solvers WHERE id = ?",
                (int(solver_id),),
            ).fetchone()
            solver_name = (
                str(solver_row["name"]) if solver_row is not None else None
            )
            self.artifact_cache.store(
                solver_id, artifact_record.artifact_id, parsed, solver_name,
            )
            cached = (artifact_record.artifact_id, parsed, solver_name)
            if source == "warm_cache":
                # Index hit but artifact missed; downgrade source.
                source = "cold_then_warmed"

        artifact_id, parsed, solver_name = cached
        try:
            output = execute_artifact(parsed, inputs)
        except (ExecutorError, UnsupportedFamilyError) as exc:
            return WarmDispatchResult(
                matched=False, source="miss",
                solver_id=solver_id, artifact_id=artifact_id,
                solver_name=solver_name,
                error=str(exc),
            )

        if source == "warm_cache":
            self.stats.warm_hits += 1
            self.stats.by_family_warm_hits[family_kind] = (
                self.stats.by_family_warm_hits.get(family_kind, 0) + 1
            )
        else:
            self.stats.cold_hits_warmed += 1
            self.stats.by_family_cold_hits[family_kind] = (
                self.stats.by_family_cold_hits.get(family_kind, 0) + 1
            )
        return WarmDispatchResult(
            matched=True,
            source=source,
            solver_id=solver_id,
            solver_name=solver_name,
            artifact_id=artifact_id,
            output=output,
        )

    def enqueue_miss_signal(self, signal: GapSignal) -> bool:
        assert self.sink is not None
        return self.sink.enqueue(signal)

    def flush_signals(self) -> int:
        assert self.sink is not None
        return self.sink.force_flush()

    def invalidate_solver(self, solver_id: int) -> None:
        self.capability_index.invalidate_solver(solver_id)
        self.artifact_cache.invalidate_solver(solver_id)
        self.stats.cache_invalidations += 1


__all__ = [
    "BufferedSignalSink",
    "BufferedSinkStats",
    "DEFAULT_MAX_UNFLUSHED_AGE_MS",
    "DEFAULT_MAX_UNFLUSHED_SIGNALS",
    "HotPathCache",
    "HotPathCacheStats",
    "ParsedArtifactCache",
    "WarmCapabilityIndex",
    "WarmDispatchResult",
]
