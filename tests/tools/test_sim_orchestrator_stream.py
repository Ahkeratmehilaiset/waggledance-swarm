# SPDX-License-Identifier: BUSL-1.1
"""Tests for sim_orchestrator streaming mode (Sprint 1 wave 3 instrumentation).

Covers acceptance criteria from sim_orchestrator_runtime_instrumentation_spec.md:
* Retrospective mode unchanged (regression smoke test)
* Streaming mode: events appended at intervals, snapshot emitted at
  emit_interval_s with correct counts
* Incremental update matches full-rescan result for the same event log
* No personal data in fixtures (synthetic events only)
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SIM_PATH = ROOT / "tools" / "sim_orchestrator.py"


def _load_sim_orchestrator():
    spec = importlib.util.spec_from_file_location("sim_orchestrator", SIM_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["sim_orchestrator"] = module
    spec.loader.exec_module(module)
    return module


sim = _load_sim_orchestrator()


def _ev(*, ts: str, agent: str, event_type: str, task_id: str,
         status: str = "", message: str = "",
         payload: dict | None = None) -> dict:
    """Build a synthetic bridge event dict (no personal data)."""
    return {
        "ts_utc": ts,
        "agent": agent,
        "type": event_type,
        "task_id": task_id,
        "status": status,
        "message": message,
        "payload": payload or {},
    }


def _write_events(path: Path, events: list[dict]) -> None:
    with path.open("a", encoding="utf-8") as f:
        for d in events:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------
# Retrospective mode unchanged (regression smoke)
# --------------------------------------------------------------------------


class TestRetrospectiveUnchanged:

    def test_compute_aligned_metrics_still_works(self, tmp_path):
        events_file = tmp_path / "events.jsonl"
        _write_events(events_file, [
            _ev(ts="2026-05-13T08:00:00Z", agent="claude",
                event_type="claim", task_id="T1", status="active"),
            _ev(ts="2026-05-13T08:01:00Z", agent="codex",
                event_type="handoff", task_id="T1", status="rco_done"),
        ])
        from datetime import datetime, timezone, timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=365)
        events = sim.parse_events(events_file, cutoff)
        threads = sim.build_threads(events)
        metrics = sim.compute_aligned_metrics(threads, sim._load_statuses())
        assert metrics["task_threads_total"] == 1
        assert metrics["threads_with_claim"] == 1


# --------------------------------------------------------------------------
# Streaming mode happy path
# --------------------------------------------------------------------------


class TestStreamingMode:

    def test_three_events_then_one_snapshot_with_correct_counts(self, tmp_path):
        """3 events at 1s intervals + 5s emit_interval -> 1 snapshot."""
        events_file = tmp_path / "events.jsonl"
        events_file.touch()

        # Inject all 3 events up-front; the streaming loop will read them
        # in the first poll iteration. Then the fake clock advances past
        # the emit interval to trigger one snapshot.
        _write_events(events_file, [
            _ev(ts="2026-05-13T08:00:00Z", agent="claude",
                event_type="claim", task_id="T1"),
            _ev(ts="2026-05-13T08:00:01Z", agent="codex",
                event_type="handoff", task_id="T1", status="rco_done"),
            _ev(ts="2026-05-13T08:00:02Z", agent="claude",
                event_type="handoff", task_id="T2", status="rco_requested"),
        ])

        snapshots: list[dict] = []
        def emit(env: dict) -> str:
            snapshots.append(env)
            return f"evt_{len(snapshots)}"

        # Fake clock: returns 0, 6, 6 -- 0 at start, 6 after first poll
        # (>= 5s emit_interval), then stop after 1 snapshot.
        ticks = iter([0.0, 6.0, 6.0, 6.0])
        final = sim.stream(
            events_path=events_file,
            emit_interval_s=5.0,
            emit_snapshot=emit,
            clock_fn=lambda: next(ticks),
            sleep_fn=lambda _t: None,
            stop_after_snapshots=1,
            poll_interval_s=0.01,
        )

        assert len(snapshots) == 1
        snap = snapshots[0]
        assert snap["mode"] == "live"
        assert snap["schema_version"] == "agent-flight-plan-live-v1"
        assert snap["metrics"]["task_threads_total"] == 2
        assert snap["metrics"]["threads_with_claim"] == 1
        assert snap["source"]["event_count"] == 3
        # cursor_bytes should match file size after consuming events
        assert final.cursor_bytes == events_file.stat().st_size
        assert final.snapshot_count == 1

    def test_no_emit_before_interval_elapses(self, tmp_path):
        """Snapshot must NOT emit before emit_interval_s has elapsed."""
        events_file = tmp_path / "events.jsonl"
        events_file.touch()
        _write_events(events_file, [
            _ev(ts="2026-05-13T08:00:00Z", agent="claude",
                event_type="claim", task_id="T1"),
        ])

        snapshots: list[dict] = []
        # Clock ticks 0, 2, 7 -- 2 < 5 (no emit), 7 - 0 >= 5 (emit)
        ticks = iter([0.0, 2.0, 7.0, 7.0])
        sim.stream(
            events_path=events_file,
            emit_interval_s=5.0,
            emit_snapshot=lambda e: snapshots.append(e) or "id",
            clock_fn=lambda: next(ticks),
            sleep_fn=lambda _t: None,
            stop_after_snapshots=1,
            poll_interval_s=0.01,
        )
        assert len(snapshots) == 1

    def test_source_events_path_matches_actual_path(self, tmp_path):
        """Codex RCO round-2 fix #1: emitted snapshot's
        source.events_path must reflect the path being tailed, not the
        global default EVENTS_PATH."""
        events_file = tmp_path / "custom_audit.jsonl"
        events_file.touch()
        _write_events(events_file, [
            _ev(ts="2026-05-13T08:00:00Z", agent="claude",
                event_type="claim", task_id="T1"),
        ])
        snapshots: list[dict] = []
        ticks = iter([0.0, 6.0, 6.0])
        sim.stream(
            events_path=events_file,
            emit_interval_s=5.0,
            emit_snapshot=lambda e: snapshots.append(e) or "id",
            clock_fn=lambda: next(ticks),
            sleep_fn=lambda _t: None,
            stop_after_snapshots=1,
            poll_interval_s=0.01,
        )
        assert len(snapshots) == 1
        assert snapshots[0]["source"]["events_path"] == str(events_file)
        # And must NOT be the global default
        from pathlib import Path
        assert snapshots[0]["source"]["events_path"] != str(sim.EVENTS_PATH)

    def test_emitted_snapshot_count_matches_state_count(self, tmp_path):
        """Codex RCO round-2 fix #2: emitted envelope's snapshot_count
        must match the post-emit StreamState.snapshot_count. Previous
        behavior emitted 0 for the first snapshot while StreamState
        ended at 1."""
        events_file = tmp_path / "events.jsonl"
        events_file.touch()
        _write_events(events_file, [
            _ev(ts="2026-05-13T08:00:00Z", agent="claude",
                event_type="claim", task_id="T1"),
        ])
        snapshots: list[dict] = []
        ticks = iter([0.0, 6.0, 12.0, 12.0])
        final = sim.stream(
            events_path=events_file,
            emit_interval_s=5.0,
            emit_snapshot=lambda e: snapshots.append(e) or "id",
            clock_fn=lambda: next(ticks),
            sleep_fn=lambda _t: None,
            stop_after_snapshots=2,
            poll_interval_s=0.01,
        )
        # First emitted snapshot is count 1; second is 2.
        # State.snapshot_count after the loop ends == 2 (matches the
        # last emitted snapshot).
        assert [s["snapshot_count"] for s in snapshots] == [1, 2]
        assert final.snapshot_count == snapshots[-1]["snapshot_count"]


# --------------------------------------------------------------------------
# Incremental update equivalence
# --------------------------------------------------------------------------


class TestIncrementalEquivalence:

    def test_incremental_update_matches_full_rescan(self, tmp_path):
        """Streaming state after consuming events == full retrospective scan."""
        events_file = tmp_path / "events.jsonl"
        events_file.touch()
        seed = [
            _ev(ts="2026-05-13T08:00:00Z", agent="claude",
                event_type="claim", task_id="T1"),
            _ev(ts="2026-05-13T08:00:30Z", agent="codex",
                event_type="handoff", task_id="T1", status="rco_done"),
            _ev(ts="2026-05-13T08:01:00Z", agent="claude",
                event_type="handoff", task_id="T2", status="rco_requested"),
            _ev(ts="2026-05-13T08:01:30Z", agent="codex",
                event_type="handoff", task_id="T2",
                status="consensus_proposal"),
            _ev(ts="2026-05-13T08:02:00Z", agent="claude",
                event_type="decision", task_id="T2",
                status="consensus_accepted"),
        ]
        _write_events(events_file, seed)

        # Streaming path: consume all events, snapshot once
        snapshots: list[dict] = []
        ticks = iter([0.0, 10.0, 10.0])
        sim.stream(
            events_path=events_file,
            emit_interval_s=5.0,
            emit_snapshot=lambda e: snapshots.append(e) or "id",
            clock_fn=lambda: next(ticks),
            sleep_fn=lambda _t: None,
            stop_after_snapshots=1,
            poll_interval_s=0.01,
        )
        stream_metrics = snapshots[0]["metrics"]

        # Retrospective path: same events, full scan
        from datetime import datetime, timezone, timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=365)
        events = sim.parse_events(events_file, cutoff)
        threads = sim.build_threads(events)
        retro_metrics = sim.compute_aligned_metrics(
            threads, sim._load_statuses()
        )

        # Both modes must converge to the same metric block
        assert stream_metrics == retro_metrics


# --------------------------------------------------------------------------
# Cursor / read_events_from_offset
# --------------------------------------------------------------------------


class TestCursor:

    def test_read_from_offset_returns_only_new_events(self, tmp_path):
        events_file = tmp_path / "events.jsonl"
        events_file.touch()
        _write_events(events_file, [
            _ev(ts="2026-05-13T08:00:00Z", agent="claude",
                event_type="claim", task_id="T1"),
        ])
        first_offset = events_file.stat().st_size
        evs1, new_off1 = sim.read_events_from_offset(events_file, 0)
        assert len(evs1) == 1
        assert new_off1 == first_offset

        # Append a second event; second read returns only that
        _write_events(events_file, [
            _ev(ts="2026-05-13T08:00:01Z", agent="codex",
                event_type="handoff", task_id="T1", status="rco_done"),
        ])
        evs2, new_off2 = sim.read_events_from_offset(events_file, new_off1)
        assert len(evs2) == 1
        assert evs2[0].agent == "codex"
        assert new_off2 == events_file.stat().st_size

    def test_missing_path_returns_empty(self, tmp_path):
        missing = tmp_path / "does_not_exist.jsonl"
        evs, off = sim.read_events_from_offset(missing, 0)
        assert evs == []
        assert off == 0

    def test_malformed_line_skipped(self, tmp_path):
        events_file = tmp_path / "events.jsonl"
        events_file.write_text(
            "not-json\n"
            + json.dumps(_ev(ts="2026-05-13T08:00:00Z", agent="claude",
                              event_type="claim", task_id="T1")) + "\n",
            encoding="utf-8",
        )
        evs, _off = sim.read_events_from_offset(events_file, 0)
        assert len(evs) == 1
        assert evs[0].task_id == "T1"


# --------------------------------------------------------------------------
# Pull endpoint get_current_metrics
# --------------------------------------------------------------------------


class TestGetCurrentMetrics:

    def test_empty_state_yields_zero_counts(self):
        state = sim.StreamState()
        snap = sim.get_current_metrics(state, profile_config_ref="profile_42")
        assert snap["metrics"]["task_threads_total"] == 0
        assert snap["mode"] == "live"
        assert snap["profile_config_ref"] == "profile_42"

    def test_pull_after_update_reflects_new_thread(self):
        state = sim.StreamState()
        from datetime import datetime, timezone
        ev = sim.Event(
            ts=datetime(2026, 5, 13, 8, 0, tzinfo=timezone.utc),
            agent="claude", type="claim", task_id="T1", status="",
            message="", payload={}, raw={},
        )
        sim.update_state_with_event(state, ev)
        snap = sim.get_current_metrics(state)
        assert snap["metrics"]["task_threads_total"] == 1
        assert snap["metrics"]["threads_with_claim"] == 1
