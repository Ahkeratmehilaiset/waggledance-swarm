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
from datetime import datetime, timezone
from pathlib import Path

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
    assert derive_instrumented_served_points(observations) == ("solver",)
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
    assert derive_instrumented_served_points(observations) == ("solver",)


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
    observed = set(derive_instrumented_served_points(_read_jsonl(paths["served_points"])))

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
