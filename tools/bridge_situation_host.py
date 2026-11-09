"""Caller-owned, serialized, read-only situation snapshots; no runtime startup.

One host retains one SituationPicture in memory. Budgets limit update calls and
delta pages, not total I/O or latency: the reader probes and rehashes accepted
prefixes, and rendering scans retained metadata. Those costs are unmeasured.
Coverage describes the reader's snapshot, not atomic current-path identity or
writer authentication. No file persistence, service, tail fallback or eviction.
"""
from __future__ import annotations

import copy
from pathlib import Path
import threading

from tools.bridge_situation_picture import SituationPicture
from waggledance.core import bridge_log_reader as reader


class SituationHost:
    """Keep a fixed caller tuple and serialize ticks, output copying and reset.

    Incomplete snapshots resume on a later tick. Other UNKNOWN outcomes and
    exceptions require explicit reset. The host exposes no previously complete
    snapshot after an exception and never returns exception text.
    """

    def __init__(self, *, events_path, roster, generation_path=None,
                 ttl_seconds=900, max_events=100000, max_bytes=4 * 1024 * 1024,
                 max_rows=10000, max_pages_per_tick=1, operator_relay_refs=()):
        for value, ceiling in ((max_pages_per_tick, 128),
                               (max_bytes, reader.MAX_MAX_BYTES),
                               (max_rows, reader.MAX_MAX_ROWS)):
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ValueError('invalid_host_budget')
        self._events_path = Path(events_path).absolute()
        self._generation_path = Path(generation_path).absolute() if generation_path is not None else None
        self._max_bytes, self._max_rows = max_bytes, max_rows
        self._max_pages_per_tick = max_pages_per_tick
        self._picture = SituationPicture(roster=copy.deepcopy(roster),
            ttl_seconds=ttl_seconds, max_events=max_events,
            operator_relay_refs=copy.deepcopy(operator_relay_refs))
        self._lock = threading.Lock()
        self._blocked = False

    @staticmethod
    def _unknown(reason, calls=0):
        return {'schema': 'wd.bridge-situation-host.v1', 'authority': 'none',
                'coverage': 'UNKNOWN', 'reason': reason, 'update_calls': calls,
                'snapshot': None}

    def tick(self, *, now):
        """Read at most the configured update count; never auto-reset history."""
        with self._lock:
            if self._blocked:
                return self._unknown('reset_required')
            calls = 0
            try:
                for _ in range(self._max_pages_per_tick):
                    calls += 1
                    snapshot = self._picture.update(self._events_path, now=now,
                        generation_path=self._generation_path,
                        max_bytes=self._max_bytes, max_rows=self._max_rows)
                    if (not isinstance(snapshot, dict)
                            or snapshot.get('schema') != 'wd.bridge-situation-picture.v1'
                            or snapshot.get('authority') != 'none'
                            or snapshot.get('coverage') not in ('COMPLETE', 'UNKNOWN')
                            or not isinstance(snapshot.get('reason'), str)
                            or not snapshot['reason']):
                        raise ValueError('invalid_internal_snapshot')
                    report = copy.deepcopy({'schema': 'wd.bridge-situation-host.v1',
                        'authority': 'none', 'coverage': snapshot['coverage'],
                        'reason': snapshot['reason'], 'update_calls': calls,
                        'snapshot': snapshot})
                    if snapshot['coverage'] == 'COMPLETE':
                        return report
                    if snapshot['reason'] != 'incomplete_snapshot':
                        self._blocked = True
                        return report
                return report
            except Exception:
                # Fail closed even if core update or defensive copying failed.
                # Do not echo raw source, caller input, paths or exception text.
                self._blocked = True
                return self._unknown('host_update_failed', calls)

    def reset(self):
        """Explicitly discard in-memory history; the next tick starts at zero."""
        with self._lock:
            self._blocked = True
            try:
                self._picture.reset()
            except Exception:
                return self._unknown('host_reset_failed')
            self._blocked = False
            return self._unknown('reset_not_read')
