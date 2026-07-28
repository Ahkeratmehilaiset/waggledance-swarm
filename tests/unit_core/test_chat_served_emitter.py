# SPDX-License-Identifier: BUSL-1.1
"""Tests for the P2 S1b ChatServedEmitter (ChatService -> chat-served sink bridge).

Covers: disabled = no-op, sync-pending writes NORMALIZED metadata (no raw leak),
FAIL-OPEN (a sink error surfaces via pending_append_failures, never raises), the
fire-and-forget resolution end-to-end (pending -> receipt -> eligible, raw query/
response never persisted), and a resolution failure -> GAP (never a silent hole).
"""
from __future__ import annotations

import asyncio
import inspect
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tools.verify_magma_receipt import verify_manifest
from waggledance.core.magma import chat_served_ledger as L
from waggledance.core.magma.chat_served_accounting import (
    coverage_from_ledger,
    REQUIRED_CHAT_SERVED_POINTS,
    read_pending_append_failures,
    valid_pending_append_failure,
)
from waggledance.core.magma.chat_served_claim_window_evidence import (
    derive_enabled_across_window,
    derive_instrumented_served_points,
    read_clean_shutdown_marker,
    read_latest_head_anchor,
)
from waggledance.core.magma.chat_query_route_evidence import (
    NORMALIZATION_VERSION,
    canonical_query_digest,
)
from waggledance.core.magma.chat_served_emitter import ChatServedEmitter, new_served_id
from waggledance.core.magma.chat_served_ledger import is_path_safe_token
from waggledance.core.magma.chat_served_metadata import is_conforming_token
from waggledance.core.magma.chat_served_sink import ChatServedReceiptSink

_KNOWN = frozenset({"HOME", "FACTORY", "COTTAGE", "GADGET"})
_WINDOW = "window:phase2g"


def _fixed_now() -> datetime:
    return datetime(2026, 7, 4, 7, 0, 0, tzinfo=timezone.utc)


def _emitter(tmp_path, *, enabled=True):
    ledger = str(tmp_path / "ledger.jsonl")
    out_dir = tmp_path / "bundles"
    out_dir.mkdir(exist_ok=True)
    sink = ChatServedReceiptSink(ledger, fsync_every=0)
    emitter = ChatServedEmitter(
        sink=sink, out_dir=out_dir, verify_manifest=verify_manifest,
        known_profiles=_KNOWN, enabled=enabled, now_fn=_fixed_now,
    )
    return emitter, sink, ledger


def _emitter_with_claim_window(tmp_path):
    ledger = str(tmp_path / "ledger.jsonl")
    out_dir = tmp_path / "bundles"
    evidence_dir = tmp_path / "claim-window"
    out_dir.mkdir(exist_ok=True)
    sink = ChatServedReceiptSink(ledger, fsync_every=0)
    paths = {
        "anchors": evidence_dir / "head-anchors.jsonl",
        "enabled": evidence_dir / "enabled-samples.jsonl",
        "clean": evidence_dir / "clean.json",
        "served_points": evidence_dir / "served-points.jsonl",
    }
    emitter = ChatServedEmitter(
        sink=sink,
        out_dir=out_dir,
        verify_manifest=verify_manifest,
        known_profiles=_KNOWN,
        enabled=True,
        now_fn=_fixed_now,
        ledger_path=ledger,
        claim_window_window_id=_WINDOW,
        claim_window_anchor_store_path=paths["anchors"],
        claim_window_enabled_samples_path=paths["enabled"],
        claim_window_clean_shutdown_marker_path=paths["clean"],
        claim_window_served_point_observations_path=paths["served_points"],
    )
    return emitter, sink, ledger, paths


def _read_jsonl(path: Path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_new_served_id_unique_and_conforming() -> None:
    a, b = new_served_id(), new_served_id()
    assert a != b
    assert is_conforming_token(a) and is_conforming_token(b)   # ledger builder accepts it


def test_disabled_emitter_is_noop(tmp_path) -> None:
    emitter, sink, _ = _emitter(tmp_path, enabled=False)
    assert emitter.record_pending(
        new_served_id(), query="q", source="solver", route_type="solver",
        language="fi", profile="HOME", agent_id=None,
    ) is False
    emitter.schedule_receipt(new_served_id(), query="q", response="a", source="solver",
                             route_type="solver", confidence=1.0, latency_ms=1.0, cached=False,
                             round_table=False, agent_id=None, language="fi", profile="HOME")
    assert sink.counts()["served"] == 0


def test_record_pending_writes_normalized_metadata_no_raw(tmp_path) -> None:
    emitter, sink, ledger = _emitter(tmp_path)
    sid = new_served_id()
    ok = emitter.record_pending(
        sid, query="private query", source="solver", route_type="solver",
        language="fi", profile="RAW SECRET PROFILE", agent_id="round_table",
    )
    assert ok is True and sink.counts()["served"] == 1
    raw = open(ledger, "rb").read()
    assert b"RAW SECRET PROFILE" not in raw                    # raw odd profile never entered
    md = L.read_entries(ledger)[0][0]["metadata"]
    assert md["profile"] == "unknown"                          # normalized honest token
    assert md["source"] == "solver" and md["language"] == "fi" and md["agent_id"] == "round_table"
    assert md["route_type"] == "solver"
    assert "served_point" not in md


def test_record_pending_binds_shared_canonical_query_identity(tmp_path) -> None:
    emitter, sink, ledger = _emitter(tmp_path)
    raw_query = "  cafe\u0301 private marker  "

    assert emitter.record_pending(
        new_served_id(), query=raw_query, source="solver", route_type="solver",
        language="en", profile="HOME", agent_id=None,
    ) is True

    metadata = L.read_entries(ledger)[0][0]["metadata"]
    assert sink.counts()["served"] == 1
    assert metadata["query_digest"] == canonical_query_digest(raw_query)
    assert metadata["query_digest"] == canonical_query_digest(
        "caf\u00e9 private marker"
    )
    assert metadata["normalization_version"] == NORMALIZATION_VERSION
    assert raw_query.encode("utf-8") not in Path(ledger).read_bytes()


def test_record_pending_caller_cannot_supply_query_digest() -> None:
    parameters = inspect.signature(ChatServedEmitter.record_pending).parameters

    assert "query" in parameters
    assert "query_digest" not in parameters


def test_query_identity_failure_is_private_and_measurement_ineligible(tmp_path) -> None:
    emitter, sink, _ledger = _emitter(tmp_path)
    marker = "RAW_QUERY_OBJECT_MUST_NOT_LEAK"

    class _InvalidQuery:
        def __str__(self) -> str:
            return marker

    assert emitter.record_pending(
        new_served_id(), query=_InvalidQuery(), source="solver", route_type="solver",
        language="en", profile="HOME", agent_id=None,
    ) is False
    assert emitter.pending_append_failures == 1
    assert sink.counts()["served"] == 0
    failure_bytes = Path(emitter.pending_failure_ledger_path).read_bytes()
    assert marker.encode("utf-8") not in failure_bytes
    failure = json.loads(failure_bytes)
    assert failure["reason"] == "metadata_rejected"
    assert "query" not in failure["metadata"]


def test_claim_window_recorder_observes_served_points_and_explicit_markers(tmp_path) -> None:
    emitter, sink, ledger, paths = _emitter_with_claim_window(tmp_path)
    sid = new_served_id()

    assert emitter.record_pending(
        sid,
        query="q",
        source="solver",
        route_type="solver",
        language="fi",
        profile="HOME",
        agent_id=None,
    ) is True
    observations = _read_jsonl(paths["served_points"])
    assert derive_instrumented_served_points(
        observations, window_id=_WINDOW
    ) == ("solver",)
    assert not paths["clean"].exists()

    assert emitter.record_claim_window_enabled_sample(True) is True
    samples = _read_jsonl(paths["enabled"])
    assert derive_enabled_across_window(samples, window_id=_WINDOW) is True

    assert emitter.checkpoint_claim_window_head() is True
    anchor = read_latest_head_anchor(str(paths["anchors"]), ledger, window_id=_WINDOW)
    assert anchor.ok is True
    assert anchor.expected_head == sink.head

    assert emitter.mark_claim_window_clean_shutdown() is True
    assert read_clean_shutdown_marker(str(paths["clean"]), window_id=_WINDOW) is True


def test_claim_window_served_point_observation_is_once_per_point(tmp_path) -> None:
    emitter, sink, _ledger, paths = _emitter_with_claim_window(tmp_path)

    assert emitter.record_pending(
        new_served_id(),
        query="q",
        source="solver",
        route_type="solver",
        language="fi",
        profile="HOME",
        agent_id=None,
    ) is True
    assert emitter.record_pending(
        new_served_id(),
        query="q",
        source="solver",
        route_type="solver",
        language="fi",
        profile="HOME",
        agent_id=None,
    ) is True
    assert sink.counts()["served"] == 2

    observations = _read_jsonl(paths["served_points"])
    assert len(observations) == 1
    assert derive_instrumented_served_points(
        observations, window_id=_WINDOW
    ) == ("solver",)


def test_explicit_served_point_is_closed_and_preserves_route_truth(tmp_path) -> None:
    emitter, sink, ledger, paths = _emitter_with_claim_window(tmp_path)

    assert emitter.record_pending(
        new_served_id(),
        query="q",
        source="llm",
        route_type="memory",
        served_point="llm",
        language="fi",
        profile="HOME",
        agent_id="a1",
    ) is True

    metadata = L.read_entries(ledger)[0][0]["metadata"]
    assert metadata["route_type"] == "memory"
    assert "served_point" not in metadata
    assert set(metadata) == {
        "source",
        "route_type",
        "language",
        "profile",
        "world_snapshot_ref",
        "query_digest",
        "normalization_version",
        "agent_id",
    }
    assert L.verify_chain(L.read_entries(ledger)[0]).ok is True
    assert derive_instrumented_served_points(
        _read_jsonl(paths["served_points"]), window_id=_WINDOW
    ) == ("llm",)
    assert sink.counts()["served"] == 1


def test_non_closed_served_point_fails_open_and_latches_ineligible(tmp_path) -> None:
    emitter, sink, _ledger = _emitter(tmp_path)

    assert emitter.record_pending(
        new_served_id(),
        query="q",
        source="llm",
        route_type="memory",
        served_point="memory",
        language="fi",
        profile="HOME",
        agent_id="a1",
    ) is False

    assert emitter.pending_append_failures == 1
    assert sink.counts()["served"] == 0
    failure = json.loads(Path(emitter.pending_failure_ledger_path).read_text("utf-8"))
    assert failure["reason"] == "metadata_rejected"
    assert failure["metadata"]["route_type"] == "memory"
    assert "served_point" not in failure["metadata"]


def test_claim_window_recorder_does_not_turn_partial_observations_complete(tmp_path) -> None:
    emitter, _sink, _ledger, paths = _emitter_with_claim_window(tmp_path)

    assert emitter.record_pending(
        new_served_id(),
        query="q",
        source="solver",
        route_type="solver",
        language="fi",
        profile="HOME",
        agent_id=None,
    ) is True
    observed = set(
        derive_instrumented_served_points(
            _read_jsonl(paths["served_points"]), window_id=_WINDOW
        )
    )

    assert observed == {"solver"}
    assert observed != set(REQUIRED_CHAT_SERVED_POINTS)


def test_record_pending_fail_open_surfaces_not_swallows(tmp_path) -> None:
    emitter, _sink, _ = _emitter(tmp_path)

    class _Boom:
        def record_pending(self, *a, **k):
            raise RuntimeError("disk full")

    emitter._sink = _Boom()
    result = emitter.record_pending(
        new_served_id(), query="q", source="solver", route_type="solver",
        language="fi", profile="HOME", agent_id=None,
    )
    assert result is False                                     # fail-OPEN: never raises
    assert emitter.pending_append_failures == 1                # bc4: surfaced, not swallowed
    assert read_pending_append_failures(emitter.pending_failure_ledger_path) == 1


def test_pending_failure_writer_rejects_raw_or_nested_metadata(tmp_path) -> None:
    emitter, _sink, _ = _emitter(tmp_path)
    emitter._write_pending_append_failure(
        new_served_id(),
        "2026-07-04T07:00:00.000000Z",
        "sink_write_failed",
        {
            "route_type": "solver",
            "profile": "RAW SECRET USER QUERY with spaces",
            "nested": {"raw": "X"},  # type: ignore[dict-item]
        },
    )
    assert not Path(emitter.pending_failure_ledger_path).exists()

    emitter._write_pending_append_failure(
        new_served_id(),
        "2026-07-04T07:00:00.000000Z",
        "sink_write_failed",
        {"route_type": "solver"},
    )
    raw = open(emitter.pending_failure_ledger_path, "rb").read()
    assert b"RAW SECRET" not in raw
    assert b"nested" not in raw
    entry = json.loads(raw.decode("utf-8"))
    assert entry["metadata"] == {"route_type": "solver"}
    assert valid_pending_append_failure(entry) is True
    assert read_pending_append_failures(emitter.pending_failure_ledger_path) == 1


def test_schedule_receipt_no_running_loop_is_safe(tmp_path) -> None:
    emitter, sink, _ = _emitter(tmp_path)
    sid = new_served_id()
    emitter.record_pending(
        sid, query="q", source="solver", route_type="solver", language="fi",
        profile="HOME", agent_id=None,
    )
    # called outside any event loop -> safe no-op (no crash), pending stays unresolved
    emitter.schedule_receipt(sid, query="q", response="a", source="solver", route_type="solver",
                             confidence=0.9, latency_ms=1.0, cached=False, round_table=False,
                             agent_id=None, language="fi", profile="HOME")
    assert sink.counts()["pending_unresolved"] == 1
    emitter.close_intake()
    result = asyncio.run(emitter.drain(0.1))
    assert result["reason"] == "schedule_failures"
    assert result["schedule_failures"] == 1


def test_close_intake_is_idempotent_and_latches_post_close_attempts(tmp_path) -> None:
    emitter, sink, _ledger = _emitter(tmp_path)

    assert emitter.close_intake() is True
    assert emitter.close_intake() is False
    assert emitter.intake_closed is True
    assert emitter.record_pending(
        new_served_id(),
        query="q",
        source="solver",
        route_type="solver",
        served_point="solver",
        language="fi",
        profile="HOME",
        agent_id=None,
    ) is False
    emitter.schedule_receipt(
        new_served_id(),
        query="q",
        response="a",
        source="solver",
        route_type="solver",
        confidence=1.0,
        latency_ms=1.0,
        cached=False,
        round_table=False,
        agent_id=None,
        language="fi",
        profile="HOME",
    )

    result = asyncio.run(emitter.drain(0.1))
    assert result == {
        "status": "not_clean",
        "reason": "post_close_attempts",
        "intake_closed": True,
        "scheduled": 0,
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
        "pending": 0,
        "post_close_attempts": 2,
        "schedule_failures": 0,
        "timed_out": False,
        "caller_cancelled": False,
    }
    assert emitter.post_close_attempts == 2
    assert sink.counts()["served"] == 0


def test_disabled_emitter_lifecycle_and_post_close_paths_are_noops(tmp_path) -> None:
    emitter, sink, _ledger = _emitter(tmp_path, enabled=False)

    assert emitter.close_intake() is False
    assert emitter.intake_closed is False
    assert emitter.record_pending(
        new_served_id(),
        query="q",
        source="solver",
        route_type="solver",
        served_point="solver",
        language="fi",
        profile="HOME",
        agent_id=None,
    ) is False
    emitter.schedule_receipt(
        new_served_id(),
        query="q",
        response="a",
        source="solver",
        route_type="solver",
        confidence=1.0,
        latency_ms=1.0,
        cached=False,
        round_table=False,
        agent_id=None,
        language="fi",
        profile="HOME",
    )

    assert emitter.post_close_attempts == 0
    assert sink.counts()["served"] == 0


def test_bounded_drain_and_flush_are_successful_and_idempotent(tmp_path) -> None:
    emitter, sink, _ledger = _emitter(tmp_path)
    sid = new_served_id()
    assert emitter.record_pending(
        sid,
        query="q",
        source="solver",
        route_type="solver",
        served_point="solver",
        language="fi",
        profile="HOME",
        agent_id=None,
    )

    async def run() -> tuple[dict[str, object], dict[str, object]]:
        emitter.schedule_receipt(
            sid,
            query="q",
            response="a",
            source="solver",
            route_type="solver",
            confidence=1.0,
            latency_ms=1.0,
            cached=False,
            round_table=False,
            agent_id=None,
            language="fi",
            profile="HOME",
        )
        assert emitter.close_intake() is True
        first = await emitter.drain(1.0)
        second = await emitter.drain(1.0)
        return first, second

    first, second = asyncio.run(run())
    assert first == second
    assert first["status"] == "drained"
    assert first["reason"] is None
    assert first["scheduled"] == 1
    assert first["completed"] == 1
    assert first["failed"] == 0
    assert first["cancelled"] == 0
    assert first["pending"] == 0
    assert emitter.flush_sink() is True
    assert emitter.flush_sink() is True
    assert sink.counts() == {
        "served": 1,
        "receipts": 1,
        "gaps": 0,
        "pending_unresolved": 0,
    }


def test_drain_resamples_tasks_scheduled_during_drain(tmp_path) -> None:
    emitter, _sink, _ledger = _emitter(tmp_path)
    spawned = False

    def schedule(sid: str) -> None:
        emitter.schedule_receipt(
            sid,
            query="q",
            response="a",
            source="solver",
            route_type="solver",
            confidence=1.0,
            latency_ms=1.0,
            cached=False,
            round_table=False,
            agent_id=None,
            language="fi",
            profile="HOME",
        )

    async def fake_resolve(served_id, **_kwargs):
        nonlocal spawned
        await asyncio.sleep(0)
        if not spawned:
            spawned = True
            schedule("second")
        await asyncio.sleep(0)
        return "receipt"

    emitter._resolve = fake_resolve

    async def run() -> tuple[dict[str, object], dict[str, object]]:
        schedule("first")
        open_result = await emitter.drain(1.0)
        emitter.close_intake()
        closed_result = await emitter.drain(1.0)
        return open_result, closed_result

    open_result, closed_result = asyncio.run(run())
    assert open_result["reason"] == "intake_open"
    assert open_result["scheduled"] == 2
    assert open_result["completed"] == 2
    assert open_result["pending"] == 0
    assert closed_result["status"] == "drained"
    assert closed_result["scheduled"] == 2
    assert closed_result["completed"] == 2


def test_drain_timeout_keeps_unfinished_work_tracked_until_retry(tmp_path) -> None:
    emitter, _sink, _ledger = _emitter(tmp_path)
    never = asyncio.Event()

    async def fake_resolve(_served_id, **_kwargs):
        await never.wait()
        return "receipt"

    emitter._resolve = fake_resolve

    async def run() -> tuple[dict[str, object], dict[str, object]]:
        emitter.schedule_receipt(
            "slow",
            query="q",
            response="a",
            source="solver",
            route_type="solver",
            confidence=1.0,
            latency_ms=1.0,
            cached=False,
            round_table=False,
            agent_id=None,
            language="fi",
            profile="HOME",
        )
        emitter.close_intake()
        timed_out = await emitter.drain(0.01)
        never.set()
        drained = await emitter.drain(1.0)
        return timed_out, drained

    timed_out, drained = asyncio.run(run())
    assert timed_out["status"] == "not_clean"
    assert timed_out["reason"] == "timeout"
    assert timed_out["timed_out"] is True
    assert timed_out["scheduled"] == 1
    assert timed_out["cancelled"] == 0
    assert timed_out["pending"] == 1
    assert drained["status"] == "drained"
    assert drained["pending"] == 0
    assert drained["completed"] == 1


def test_drain_timeout_does_not_detach_running_to_thread_write(tmp_path) -> None:
    emitter, sink, _ledger = _emitter(tmp_path)
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    emitter._write_bundle = lambda *_args: {"receipt": {"schema": "probe"}}

    def blocking_resolve(*_args) -> None:
        started.set()
        release.wait(2.0)
        finished.set()

    sink.resolve_receipt = blocking_resolve

    async def run() -> tuple[dict[str, object], dict[str, object], bool]:
        emitter.schedule_receipt(
            "safe-id",
            query="q",
            response="a",
            source="solver",
            route_type="solver",
            confidence=1.0,
            latency_ms=1.0,
            cached=False,
            round_table=False,
            agent_id=None,
            language="fi",
            profile="HOME",
        )
        while not started.is_set():
            await asyncio.sleep(0.001)
        emitter.close_intake()
        timed_out = await emitter.drain(0.01)
        finished_at_return = finished.is_set()
        release.set()
        drained = await emitter.drain(1.0)
        return timed_out, drained, finished_at_return

    timed_out, drained, finished_at_return = asyncio.run(run())
    assert finished_at_return is False
    assert timed_out["reason"] == "timeout"
    assert timed_out["pending"] == 1
    assert timed_out["cancelled"] == 0
    assert drained["status"] == "drained"
    assert drained["pending"] == 0
    assert finished.is_set()


def test_concurrent_terminal_appends_keep_ledger_timestamps_monotonic(tmp_path) -> None:
    """Bundle completion order must not reorder terminal ledger timestamps."""
    base = datetime(2026, 7, 27, tzinfo=timezone.utc)
    clock_lock = threading.Lock()
    tick = 0

    def advancing_now() -> datetime:
        nonlocal tick
        with clock_lock:
            tick += 1
            return base + timedelta(microseconds=tick)

    emitter, sink, ledger = _emitter(tmp_path)
    emitter._now_fn = advancing_now
    worker_count = 5
    all_workers_started = threading.Event()
    barrier = threading.Barrier(worker_count, action=all_workers_started.set)
    releases = {ordinal: threading.Event() for ordinal in range(1, worker_count + 1)}

    def controlled_bundle(_summary, _now, ordinal, _served_id):
        barrier.wait(timeout=2.0)
        assert releases[ordinal].wait(timeout=2.0)
        return {"receipt": {"schema": "probe", "ordinal": ordinal}}

    emitter._write_bundle = controlled_bundle

    async def run() -> dict[str, object]:
        for ordinal in range(1, worker_count + 1):
            served_id = f"served-{ordinal}"
            assert emitter.record_pending(
                served_id,
                query="q",
                source="solver",
                route_type="solver",
                served_point="solver",
                language="fi",
                profile="HOME",
                agent_id=None,
            )
            emitter.schedule_receipt(
                served_id,
                query="q",
                response="a",
                source="solver",
                route_type="solver",
                confidence=1.0,
                latency_ms=1.0,
                cached=False,
                round_table=False,
                agent_id=None,
                language="fi",
                profile="HOME",
            )
        while not all_workers_started.is_set():
            await asyncio.sleep(0.001)
        for expected_receipts, ordinal in enumerate(
            range(worker_count, 0, -1),
            start=1,
        ):
            releases[ordinal].set()
            while sink.counts()["receipts"] < expected_receipts:
                await asyncio.sleep(0.001)
        emitter.close_intake()
        return await emitter.drain(1.0)

    result = asyncio.run(run())
    assert result["status"] == "drained"
    entries = L.read_entries(ledger)[0]
    terminal_timestamps = [
        entry["ts_utc"]
        for entry in entries
        if entry["entry_type"] == L.RECEIPT_TERMINAL
    ]
    assert len(terminal_timestamps) == worker_count
    assert terminal_timestamps == sorted(terminal_timestamps)


def test_caller_cancelled_drain_propagates_and_keeps_work_tracked(tmp_path) -> None:
    emitter, _sink, _ledger = _emitter(tmp_path)
    never = asyncio.Event()

    async def fake_resolve(_served_id, **_kwargs):
        await never.wait()
        return "receipt"

    emitter._resolve = fake_resolve

    async def run() -> dict[str, object]:
        emitter.schedule_receipt(
            "slow",
            query="q",
            response="a",
            source="solver",
            route_type="solver",
            confidence=1.0,
            latency_ms=1.0,
            cached=False,
            round_table=False,
            agent_id=None,
            language="fi",
            profile="HOME",
        )
        emitter.close_intake()
        drain_task = asyncio.create_task(emitter.drain(10.0))
        await asyncio.sleep(0)
        drain_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await drain_task
        assert len(emitter._tasks) == 1
        assert emitter._cancelled_tasks == 0
        never.set()
        return await emitter.drain(1.0)

    result = asyncio.run(run())
    assert result["status"] == "drained"
    assert result["caller_cancelled"] is False
    assert result["cancelled"] == 0
    assert result["pending"] == 0


def test_drain_accounts_receipt_failure_resolved_as_gap(tmp_path) -> None:
    emitter, sink, _ledger = _emitter(tmp_path)
    sid = new_served_id()
    assert emitter.record_pending(
        sid,
        query="q",
        source="solver",
        route_type="solver",
        served_point="solver",
        language="fi",
        profile="HOME",
        agent_id=None,
    )

    def boom(*_args, **_kwargs):
        raise RuntimeError("bundle write failed")

    emitter._write_bundle = boom

    async def run() -> dict[str, object]:
        emitter.schedule_receipt(
            sid,
            query="q",
            response="a",
            source="solver",
            route_type="solver",
            confidence=1.0,
            latency_ms=1.0,
            cached=False,
            round_table=False,
            agent_id=None,
            language="fi",
            profile="HOME",
        )
        emitter.close_intake()
        return await emitter.drain(1.0)

    result = asyncio.run(run())
    assert result["status"] == "drained"
    assert result["reason"] is None
    assert result["completed"] == 1
    assert result["failed"] == 0
    assert result["cancelled"] == 0
    assert sink.counts()["gaps"] == 1


class _ForgedTerminalString(str):
    pass


class _EqualityForgingOutcome:
    def __eq__(self, _other):
        return True

    def __ne__(self, _other):
        return False


@pytest.mark.parametrize(
    "outcome",
    [
        None,
        "unresolved",
        _ForgedTerminalString("receipt"),
        _ForgedTerminalString("gap"),
        _EqualityForgingOutcome(),
    ],
)
def test_drain_rejects_nonexact_or_unresolved_terminal_outcomes(
    tmp_path,
    outcome,
) -> None:
    emitter, _sink, _ledger = _emitter(tmp_path)

    async def fake_resolve(_served_id, **_kwargs):
        return outcome

    emitter._resolve = fake_resolve

    async def run() -> dict[str, object]:
        emitter.schedule_receipt(
            new_served_id(),
            query="q",
            response="a",
            source="solver",
            route_type="solver",
            confidence=1.0,
            latency_ms=1.0,
            cached=False,
            round_table=False,
            agent_id=None,
            language="fi",
            profile="HOME",
        )
        emitter.close_intake()
        return await emitter.drain(1.0)

    result = asyncio.run(run())
    assert result["status"] == "not_clean"
    assert result["reason"] == "task_failures"
    assert result["completed"] == 1
    assert result["failed"] == 1


def test_drain_rejects_receipt_task_exception(tmp_path) -> None:
    emitter, _sink, _ledger = _emitter(tmp_path)

    async def fake_resolve(_served_id, **_kwargs):
        raise RuntimeError("receipt task failed")

    emitter._resolve = fake_resolve

    async def run() -> dict[str, object]:
        emitter.schedule_receipt(
            new_served_id(),
            query="q",
            response="a",
            source="solver",
            route_type="solver",
            confidence=1.0,
            latency_ms=1.0,
            cached=False,
            round_table=False,
            agent_id=None,
            language="fi",
            profile="HOME",
        )
        emitter.close_intake()
        return await emitter.drain(1.0)

    result = asyncio.run(run())
    assert result["status"] == "not_clean"
    assert result["reason"] == "task_failures"
    assert result["completed"] == 1
    assert result["failed"] == 1


def test_end_to_end_pending_then_receipt_eligible(tmp_path) -> None:
    emitter, sink, ledger = _emitter(tmp_path)
    sid = new_served_id()
    assert emitter.record_pending(
        sid, query="what time cheapest", source="solver", route_type="solver",
        language="fi", profile="HOME", agent_id=None,
    ) is True

    async def run() -> None:
        emitter.schedule_receipt(sid, query="what time cheapest", response="hours 02-05",
                                 source="solver", route_type="solver", confidence=0.95,
                                 latency_ms=12.0, cached=False, round_table=False, agent_id=None,
                                 language="fi", profile="HOME",
                                 route_stage_trace=[{"stage": "deterministic_solver"}])
        await asyncio.gather(*list(emitter._tasks))            # await the fire-and-forget task

    asyncio.run(run())
    assert sink.counts() == {"served": 1, "receipts": 1, "gaps": 0, "pending_unresolved": 0}
    cov = coverage_from_ledger(ledger)
    assert cov.eligible is True and cov.ratio == 1.0
    assert b"what time cheapest" not in open(ledger, "rb").read()   # only digests persisted


def test_resolve_failure_records_gap_not_swallowed(tmp_path) -> None:
    emitter, sink, ledger = _emitter(tmp_path)
    sid = new_served_id()
    emitter.record_pending(
        sid, query="q", source="solver", route_type="solver", language="fi",
        profile="HOME", agent_id=None,
    )

    def _boom(*a, **k):
        raise RuntimeError("bundle write failed")

    emitter._write_bundle = _boom                             # force receipt build to fail

    async def run() -> None:
        await emitter._resolve(sid, query="q", response="a", source="solver", route_type="solver",
                               confidence=0.95, latency_ms=1.0, cached=False, round_table=False,
                               agent_id=None, language="fi", profile="HOME", route_stage_trace=None)

    asyncio.run(run())
    assert sink.counts() == {"served": 1, "receipts": 0, "gaps": 1, "pending_unresolved": 0}
    assert coverage_from_ledger(ledger).eligible is False     # a gap -> not eligible


# --- path-escape (tools finding): served_id is a token that allows '/' and '.' -----
def test_safe_bundle_name_neutralizes_path_traversal() -> None:
    evil = "a/../../../escape"
    assert is_conforming_token(evil)                          # a VALID ledger token...
    name = ChatServedEmitter._safe_bundle_name(evil)
    assert name.startswith("served-")                         # ...but a single SAFE path segment
    assert "/" not in name and "\\" not in name and ".." not in name


def test_path_traversal_served_id_rejected_at_ingress(tmp_path) -> None:
    emitter, sink, _ledger = _emitter(tmp_path)
    evil = "x/../../../pwned"
    assert is_conforming_token(evil) and not is_path_safe_token(evil)   # valid TOKEN, UNSAFE path
    # INGRESS rejects it: no pending, surfaced (bc4), NEVER a counted receipt.
    assert emitter.record_pending(
        evil, query="q", source="solver", route_type="solver", language="fi",
        profile="HOME", agent_id=None,
    ) is False
    assert emitter.pending_append_failures == 1
    assert read_pending_append_failures(emitter.pending_failure_ledger_path) == 1
    assert sink.counts()["served"] == 0

    async def run() -> None:
        emitter.schedule_receipt(evil, query="q", response="a", source="solver", route_type="solver",
                                 confidence=0.9, latency_ms=1.0, cached=False, round_table=False,
                                 agent_id=None, language="fi", profile="HOME")   # no-op for unsafe id
        await asyncio.gather(*list(emitter._tasks))

    asyncio.run(run())
    assert sink.counts() == {"served": 0, "receipts": 0, "gaps": 0, "pending_unresolved": 0}
    assert not (tmp_path / "pwned").exists() and not (tmp_path.parent / "pwned").exists()


def test_pending_append_failure_ledger_makes_coverage_ineligible(tmp_path) -> None:
    emitter, sink, ledger = _emitter(tmp_path)
    sid = new_served_id()

    class _Boom:
        def record_pending(self, *a, **k):
            raise RuntimeError("disk full")

    emitter._sink = _Boom()
    assert emitter.record_pending(sid, query="q", source="solver", route_type="solver",
                                  language="fi", profile="HOME", agent_id=None) is False
    assert sink.counts()["served"] == 0

    report = coverage_from_ledger(
        ledger,
        pending_failure_ledger_path=emitter.pending_failure_ledger_path,
    )
    assert report.served == 1
    assert report.receipts == 0
    assert report.gaps == 1
    assert report.pending_append_failures == 1
    assert report.eligible is False
    assert report.reason == "pending_append_failures"
