# SPDX-License-Identifier: BUSL-1.1
"""Tests for the P2 S1b ChatServedEmitter (ChatService -> chat-served sink bridge).

Covers: disabled = no-op, sync-pending writes NORMALIZED metadata (no raw leak),
FAIL-OPEN (a sink error surfaces via pending_append_failures, never raises), the
fire-and-forget resolution end-to-end (pending -> receipt -> eligible, raw query/
response never persisted), and a resolution failure -> GAP (never a silent hole).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from tools.verify_magma_receipt import verify_manifest
from waggledance.core.magma import chat_served_ledger as L
from waggledance.core.magma.chat_served_accounting import coverage_from_ledger
from waggledance.core.magma.chat_served_emitter import ChatServedEmitter, new_served_id
from waggledance.core.magma.chat_served_metadata import is_conforming_token
from waggledance.core.magma.chat_served_sink import ChatServedReceiptSink

_KNOWN = frozenset({"HOME", "FACTORY", "COTTAGE", "GADGET"})


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


def test_new_served_id_unique_and_conforming() -> None:
    a, b = new_served_id(), new_served_id()
    assert a != b
    assert is_conforming_token(a) and is_conforming_token(b)   # ledger builder accepts it


def test_disabled_emitter_is_noop(tmp_path) -> None:
    emitter, sink, _ = _emitter(tmp_path, enabled=False)
    assert emitter.record_pending(new_served_id(), source="solver", route_type="solver",
                                  language="fi", profile="HOME", agent_id=None) is False
    emitter.schedule_receipt(new_served_id(), query="q", response="a", source="solver",
                             route_type="solver", confidence=1.0, latency_ms=1.0, cached=False,
                             round_table=False, agent_id=None, language="fi", profile="HOME")
    assert sink.counts()["served"] == 0


def test_record_pending_writes_normalized_metadata_no_raw(tmp_path) -> None:
    emitter, sink, ledger = _emitter(tmp_path)
    sid = new_served_id()
    ok = emitter.record_pending(sid, source="solver", route_type="solver", language="fi",
                                profile="RAW SECRET PROFILE", agent_id="round_table")
    assert ok is True and sink.counts()["served"] == 1
    raw = open(ledger, "rb").read()
    assert b"RAW SECRET PROFILE" not in raw                    # raw odd profile never entered
    md = L.read_entries(ledger)[0][0]["metadata"]
    assert md["profile"] == "unknown"                          # normalized honest token
    assert md["source"] == "solver" and md["language"] == "fi" and md["agent_id"] == "round_table"


def test_record_pending_fail_open_surfaces_not_swallows(tmp_path) -> None:
    emitter, _sink, _ = _emitter(tmp_path)

    class _Boom:
        def record_pending(self, *a, **k):
            raise RuntimeError("disk full")

    emitter._sink = _Boom()
    result = emitter.record_pending(new_served_id(), source="solver", route_type="solver",
                                    language="fi", profile="HOME", agent_id=None)
    assert result is False                                     # fail-OPEN: never raises
    assert emitter.pending_append_failures == 1                # bc4: surfaced, not swallowed


def test_schedule_receipt_no_running_loop_is_safe(tmp_path) -> None:
    emitter, sink, _ = _emitter(tmp_path)
    sid = new_served_id()
    emitter.record_pending(sid, source="solver", route_type="solver", language="fi",
                           profile="HOME", agent_id=None)
    # called outside any event loop -> safe no-op (no crash), pending stays unresolved
    emitter.schedule_receipt(sid, query="q", response="a", source="solver", route_type="solver",
                             confidence=0.9, latency_ms=1.0, cached=False, round_table=False,
                             agent_id=None, language="fi", profile="HOME")
    assert sink.counts()["pending_unresolved"] == 1


def test_end_to_end_pending_then_receipt_eligible(tmp_path) -> None:
    emitter, sink, ledger = _emitter(tmp_path)
    sid = new_served_id()
    assert emitter.record_pending(sid, source="solver", route_type="solver", language="fi",
                                  profile="HOME", agent_id=None) is True

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
    emitter.record_pending(sid, source="solver", route_type="solver", language="fi",
                           profile="HOME", agent_id=None)

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


def test_path_traversal_served_id_bundle_stays_in_out_dir(tmp_path) -> None:
    emitter, sink, _ledger = _emitter(tmp_path)
    out_dir = tmp_path / "bundles"
    evil = "x/../../../pwned"
    assert is_conforming_token(evil)
    emitter.record_pending(evil, source="solver", route_type="solver", language="fi",
                           profile="HOME", agent_id=None)

    async def run() -> None:
        await emitter._resolve(evil, query="q", response="a", source="solver", route_type="solver",
                               confidence=0.9, latency_ms=1.0, cached=False, round_table=False,
                               agent_id=None, language="fi", profile="HOME", route_stage_trace=None)

    asyncio.run(run())
    assert sink.counts()["receipts"] == 1                     # still resolves correctly
    for child in out_dir.iterdir():                           # every bundle stays a safe child
        assert child.parent == out_dir and ".." not in child.name and "/" not in child.name
        assert child.resolve().is_relative_to(out_dir.resolve())
    assert not (tmp_path / "pwned").exists()                  # nothing escaped out_dir
    assert not (tmp_path.parent / "pwned").exists()
