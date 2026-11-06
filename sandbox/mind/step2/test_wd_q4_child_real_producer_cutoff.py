"""Synthetic child-to-real-fenced-producer integration; no live data or real model.

The existing child fixture replaces only the sentence-transformer backend with a
deterministic synthetic encoder. The producer, checkpoint and FAISS are real.
"""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os

import pytest

faiss = pytest.importorskip("faiss", reason="real FAISS required for this integration")
pytestmark = pytest.mark.skipif(os.name != "nt", reason="private checkpoint requires a Windows fixed volume")

from sandbox.mind.step2 import memory, prefix_private_state, shared_projection
from sandbox.mind.step2.test_public_catchup_producer_integration_new import _read_pointer, _request
from sandbox.mind.step2.test_shared_index_builder import public_event
from sandbox.mind.step2.test_wd_q4_prefix_build_child import backend, enc, model_dir, plan, sha  # noqa: F401
from sandbox.mind.step2.wd_q4_prefix_build_child import build_prefix_generation


@pytest.mark.parametrize("use_cutoff", [True, False], ids=["fenced-cutoff", "legacy-no-cutoff"])
def test_child_real_fenced_producer_keeps_young_row_until_ready(tmp_path, model_dir, backend, use_cutoff):
    assert faiss.IndexFlatIP is not None
    old = public_event(ts_utc="2026-10-31T11:58:00Z")
    young = public_event(ts_utc="2026-10-31T12:00:59Z", agent="fable-5")
    private = public_event(ts_utc="2026-10-31T11:57:00Z", to="fable-5", message="synthetic-private")
    request, fixture = _request(tmp_path, [old, young, private])
    doc = plan(model_dir, asdict(request.pins), schema="wd.q4-prefix-build-plan.v3", max_windows=8, fenced=True)
    raw = enc(doc)
    cutoff = datetime(2026, 10, 31, 12, 1, tzinfo=timezone.utc)
    args = dict(enabled=True, plan_bytes=raw, expected_plan_sha256=sha(raw), config=request.config,
                start_prefix=request.start_prefix, root=request.root, private_state_root=request.private_state_root,
                trusted_model_root=model_dir)
    if use_cutoff:
        args["cutoff_utc"] = cutoff
    result = build_prefix_generation(**args)
    assert result["status"] == "PUBLISHED_GENESIS"
    assert result["coverage"] == ("partial_ready_prefix" if use_cutoff else
                                   "caught_up_to_final_snapshot_not_history")
    assert result["freshness"] == result["source_authenticity"] == "UNKNOWN"
    _, cursor = _read_pointer(request, fixture, result["pointer_sha256"])
    source_bytes = request.config.source.source
    with open(source_bytes, "rb") as source:
        data = source.read()
    lines = data.splitlines(keepends=True)
    boundary = len(lines[0]) if use_cutoff else len(data)
    assert cursor.cursor.offset == boundary
    assert cursor.sha256 == hashlib.sha256(data[:boundary]).hexdigest()
    with prefix_private_state.locked_state(request.private_state_root, request.root, enabled=True) as state:
        assert state.load(result["pointer_sha256"]) == cursor
    index = request.root / "gen-a"
    projection_bytes = (index / "projection.json").read_bytes()
    projection = json.loads(projection_bytes)
    records = projection["records"]
    expected = [old] if use_cutoff else [old, young]
    expected_refs = {shared_projection.project_event(row, approved_tasks=fixture.APPROVED,
        roster=fixture.ROSTER, exclude_review_signal=True)[0]["provenance"]["event_ref"] for row in expected}
    assert {row["provenance"]["event_ref"] for row in records} == expected_refs
    assert all(row["provenance"]["ref_kind"] == "sha256_of_projected_metadata" and
               row["provenance"]["canonical_event"] is False for row in records)
    assert b"synthetic-private" not in projection_bytes
    loaded = memory.Memory(index)
    assert set(loaded.views) == {"general"}
    assert all(view.healthy and isinstance(view.collection._index, faiss.IndexFlatIP)
               for view in loaded.views.values())
    receipt = json.loads((index / "build_receipt.json").read_bytes())
    assert receipt["projection_sha256"] == hashlib.sha256(projection_bytes).hexdigest()
    if use_cutoff:
        assert receipt["source_coverage"]["cutoff_utc"] == cutoff.isoformat()
