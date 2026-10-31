"""Synthetic real-chain contract; no live bridge data or mocked history.

Windows checkpoint storage is required. These tests do not establish caller or
source authenticity; the in-process snapshot binding is an integrity check.
"""

import copy
import json
import os

import pytest

from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
from sandbox.mind.step2 import wd_q4_private_read as reader
from sandbox.mind.step2.test_shared_index_builder import (
    APPROVED, EVENTS, LATER_EVENTS, POLICY_BYTES, POLICY_SHA, ROSTER_BYTES,
    ROSTER_SHA, _real_snapshot, build, event, predecessor_of, sha,
)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="checkpoint requires fixed Windows volume")


def _pins(anchor):
    return dict(policy_bytes=POLICY_BYTES, expected_policy_sha256=POLICY_SHA,
                roster_bytes=ROSTER_BYTES, expected_roster_sha256=ROSTER_SHA,
                expected_anchor_sha256=anchor)


def _artifacts(result):
    return {"descriptor_sha256": result["descriptor_sha256"],
            "memory_sha256": result["memory_sha256"],
            "projection_sha256": result["projection_sha256"],
            "receipt_sha256": result["build_receipt_sha256"]}


@pytest.fixture
def chain(tmp_path):
    root = tmp_path / "memory"
    root.mkdir()
    a, a_anchor, start = _real_snapshot(snapshot, tmp_path / "log", EVENTS)
    first = build(root, name="gen-a", proj=a["projection"], approved_tasks=None, roster=None,
                  source={"snapshot_result": a, "expected_anchor_sha256": a_anchor}, allow_unbound_source=False)
    checkpoint.commit_generation(root, "gen-a", start, a["candidate_cursor"], APPROVED,
                                 expected_pointer_sha256=None, expected_artifacts=_artifacts(first),
                                 initial_cursor=start, **_pins(a_anchor))
    p0 = sha((root / checkpoint.POINTER).read_bytes())
    empty, empty_anchor, _ = _real_snapshot(snapshot, tmp_path / "log",
        [event(agent="claude-rco-1", ts_utc="2026-10-31T12:25:00Z")], start=a["candidate_cursor"])
    p1 = checkpoint.advance_empty_delta(root, empty, APPROVED, expected_pointer_sha256=p0,
                                       **_pins(empty_anchor))["pointer_sha256"]
    b, b_anchor, resume = _real_snapshot(snapshot, tmp_path / "log", LATER_EVENTS,
                                          start=empty["candidate_cursor"])
    second = build(root, name="gen-b", proj=b["projection"], approved_tasks=None, roster=None,
        source={"snapshot_result": b, "expected_anchor_sha256": b_anchor}, allow_unbound_source=False,
        predecessor=dict(predecessor_of(root, "gen-a", first),
                         watermark={"root": str(root), "pointer_sha256": p1}))
    return root, p0, p1, b, b_anchor, resume, first, second


def _commit(chain, *, pointer=None, delta=None):
    root, _, p1, b, anchor, start, _, second = chain
    return checkpoint.commit_history_generation(
        root, "gen-b", start, b["candidate_cursor"], APPROVED,
        snapshot_result=b if delta is None else delta,
        expected_pointer_sha256=p1 if pointer is None else pointer,
        expected_artifacts=_artifacts(second), **_pins(anchor))


def test_real_a_empty_b_commit_and_read_preserves_both(chain):
    root, _, p1, b, _, _, first, second = chain
    _commit(chain)
    raw = (root / checkpoint.POINTER).read_bytes()
    assert sha(raw) != p1
    verified = checkpoint.read_verified_pointer(root, APPROVED, expected_pointer_sha256=sha(raw),
        policy_bytes=POLICY_BYTES, policy_sha256=POLICY_SHA, roster_bytes=ROSTER_BYTES, roster_sha256=ROSTER_SHA)
    assert verified["generation"] == "gen-b"
    a = json.loads((root / "gen-a" / "projection.json").read_bytes())
    merged = json.loads((root / "gen-b" / "projection.json").read_bytes())
    ids = lambda p: {row["record_id"] for row in p["records"]}
    assert ids(merged) == ids(a) | ids(b["projection"])
    assert verified["artifacts"] == _artifacts(second)
    descriptor = reader.read_descriptor(root / "gen-b" / "descriptor.json", second["descriptor_sha256"])
    memory = reader.bind_memory(root / "gen-b", descriptor)
    indexed_ids = {row["provenance"]["record"] for view in memory.views.values() for row in view.rows}
    assert indexed_ids == ids(a) | ids(b["projection"])


def test_stale_cas_and_spliced_snapshot_refuse_without_publication(chain):
    root, p0, _, b, _, _, _, _ = chain
    before = (root / checkpoint.POINTER).read_bytes()
    with pytest.raises(checkpoint.CheckpointRefused):
        _commit(chain, pointer=p0)
    spliced = copy.deepcopy(b)
    spliced["projection"]["records"] = []
    with pytest.raises(checkpoint.CheckpointRefused):
        _commit(chain, delta=spliced)
    assert (root / checkpoint.POINTER).read_bytes() == before


def test_retained_delta_tampering_refuses_verified_read(chain):
    root, _, _, _, _, _, _, _ = chain
    _commit(chain)
    raw = (root / checkpoint.POINTER).read_bytes()
    value = json.loads(raw)
    delta_path = root / ("q4-delta-" + value["delta_projection_sha256"] + ".json")
    delta_path.write_bytes(b"{}")  # Synthetic fixture, never production history.
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint.read_verified_pointer(root, APPROVED, expected_pointer_sha256=sha(raw),
            policy_bytes=POLICY_BYTES, policy_sha256=POLICY_SHA,
            roster_bytes=ROSTER_BYTES, roster_sha256=ROSTER_SHA)


def test_history_budget_includes_existing_and_new_transition(chain, monkeypatch):
    root, _, _, b, _, _, _, second = chain
    _commit(chain)
    before = (root / checkpoint.POINTER).read_bytes()
    p2 = sha(before)
    b_pointer = json.loads(before)
    c, c_anchor, start = _real_snapshot(snapshot, root.parent / "log",
        [event(type="done", status="info", ts_utc="2026-10-31T12:50:00Z")],
        start=b["candidate_cursor"])
    third = build(root, name="gen-c", proj=c["projection"], approved_tasks=None, roster=None,
        source={"snapshot_result": c, "expected_anchor_sha256": c_anchor}, allow_unbound_source=False,
        predecessor=dict(predecessor_of(root, "gen-b", second),
                         watermark={"root": str(root), "pointer_sha256": p2}))
    def size(*paths):
        return sum(path.stat().st_size for path in paths)
    a_dir, b_dir, c_dir = (root / name for name in ("gen-a", "gen-b", "gen-c"))
    used_old = size(a_dir / "projection.json", b_dir / "projection.json", b_dir / "build_receipt.json",
                    b_dir / "memory.json", a_dir / "build_receipt.json",
                    root / ("q4-delta-" + b_pointer["delta_projection_sha256"] + ".json"))
    delta_bytes = json.dumps(c["projection"], sort_keys=True, indent=1).encode("utf-8")
    used_new = size(b_dir / "projection.json", c_dir / "projection.json", c_dir / "build_receipt.json",
                    c_dir / "memory.json", b_dir / "build_receipt.json") + len(delta_bytes)
    monkeypatch.setattr(checkpoint, "_MAX_HISTORY_BYTES", max(used_old, used_new))
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint.commit_history_generation(root, "gen-c", start, c["candidate_cursor"], APPROVED,
            snapshot_result=c, expected_pointer_sha256=p2, expected_artifacts=_artifacts(third), **_pins(c_anchor))
    assert (root / checkpoint.POINTER).read_bytes() == before


def test_corrupt_delta_write_never_publishes_pointer(chain, monkeypatch):
    root, _, _, _, _, _, _, _ = chain
    before = (root / checkpoint.POINTER).read_bytes()
    original = checkpoint._write_new
    def corrupt_delta(path, data):
        if path.name.startswith(".q4-delta-"):
            data = data[:-1]
        return original(path, data)
    monkeypatch.setattr(checkpoint, "_write_new", corrupt_delta)
    with pytest.raises(checkpoint.CheckpointRefused):
        _commit(chain)
    assert (root / checkpoint.POINTER).read_bytes() == before
