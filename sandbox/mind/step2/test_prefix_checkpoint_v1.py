"""Separate prefix-required checkpoint contract, synthetic fixtures only."""
from dataclasses import replace
import hashlib
import json
import os

import pytest

from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
from waggledance.core.bridge_log_reader import BridgeCursor, BridgePrefixPin

pytestmark = pytest.mark.skipif(os.name != "nt", reason="checkpoint requires Windows fixed volumes")


def test_prefix_checkpoint_requires_explicit_separate_api():
    assert callable(getattr(checkpoint, "commit_prefix_genesis", None))
    assert callable(getattr(checkpoint, "commit_prefix_history", None))
    assert callable(getattr(checkpoint, "advance_prefix_empty", None))
    assert callable(getattr(checkpoint, "read_verified_prefix_pointer", None))


def test_synthetic_protocol_genesis_and_empty_cas_with_mock_artifact_verifier(tmp_path, monkeypatch):
    """Protocol only: mocked artifact verifier; no FAISS/index assurance."""
    from dataclasses import asdict
    from sandbox.mind.step2.test_status_public_filter import read_fixture, event, enc, digest, ROSTER

    log_dir = tmp_path / "log"
    log_dir.mkdir()
    _, pins, path = read_fixture(log_dir, [event()])
    anchor = json.loads(pins["anchor_bytes"])
    start = BridgePrefixPin(BridgeCursor(**anchor["cursor_start"]), hashlib.sha256(b"").hexdigest())
    tasks = frozenset({"public-task", "nonreview-private"})
    generation = anchor["generation_path"]
    def read(delta_start, pin, anchor_doc):
        anchor_raw = enc(anchor_doc)
        return snapshot.read_anchored_delta(path, delta_start, tasks,
            anchor_bytes=anchor_raw, anchor_sha256=digest(anchor_raw), generation_path=generation,
            max_bytes=65536, max_rows=100, policy_bytes=pins["policy_bytes"], policy_sha256=pins["policy_sha256"],
            roster_bytes=pins["roster_bytes"], roster_sha256=pins["roster_sha256"],
            public_policy_bytes=pins["public_policy_bytes"], public_policy_sha256=pins["public_policy_sha256"],
            expected_prefix_sha256=pin.sha256, max_prefix_bytes=65536), anchor_raw

    first, anchor_raw = read(start.cursor, start, anchor)
    projection_sha = digest(json.dumps(first["projection"], sort_keys=True, indent=1).encode())
    artifacts = {name: (projection_sha if name == "projection_sha256" else "a" * 64)
                 for name in checkpoint.ARTIFACTS}
    fake = dict(artifacts, source_mode={"status_mode": "public-v1",
                                       "public_policy_sha256": pins["public_policy_sha256"]},
                source_binding=checkpoint._source_binding(start.cursor, first["candidate_cursor"],
                    digest(anchor_raw), projection_sha, pins["policy_sha256"], pins["roster_sha256"]))
    monkeypatch.setattr(checkpoint, "_verify_generation", lambda *args: fake)
    root = tmp_path / "state"
    root.mkdir()
    common = dict(policy_bytes=pins["policy_bytes"], expected_policy_sha256=pins["policy_sha256"],
        roster_bytes=pins["roster_bytes"], expected_roster_sha256=pins["roster_sha256"],
        expected_mode="public-v1", expected_public_policy_sha256=pins["public_policy_sha256"],
        public_policy_bytes=pins["public_policy_bytes"])
    checkpoint.commit_prefix_genesis(root, "gen-a", start.cursor, first["candidate_cursor"], tasks,
        expected_pointer_sha256=None, expected_artifacts=artifacts, initial_cursor=start.cursor,
        snapshot_result=first, expected_start_prefix=start, expected_anchor_sha256=digest(anchor_raw),
        expected_anchor_bytes=anchor_raw, **common)
    pointer0 = digest((root / checkpoint.POINTER).read_bytes())
    current = first["candidate_prefix"]
    with path.open("ab") as writer:
        writer.write(enc(event(agent="claude-rco-1", agent_uuid=ROSTER["claude-rco-1"])) + b"\n")
    anchor["cursor_start"] = asdict(current.cursor)
    empty, empty_anchor = read(current.cursor, current, anchor)
    args = dict(expected_pointer_sha256=pointer0, expected_anchor_sha256=digest(empty_anchor),
                expected_anchor_bytes=empty_anchor, **common)
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint.advance_prefix_empty(root, empty, tasks,
            expected_start_prefix=replace(current, sha256="0" * 64), **args)
    assert digest((root / checkpoint.POINTER).read_bytes()) == pointer0
    advanced = checkpoint.advance_prefix_empty(root, empty, tasks, expected_start_prefix=current, **args)
    assert advanced["status"] == "advanced"
    view, next_pin = checkpoint.read_verified_prefix_pointer(root, tasks,
        expected_pointer_sha256=advanced["pointer_sha256"], policy_bytes=pins["policy_bytes"],
        policy_sha256=pins["policy_sha256"], roster_bytes=pins["roster_bytes"],
        roster_sha256=pins["roster_sha256"], expected_public_policy_sha256=pins["public_policy_sha256"])
    assert next_pin == empty["candidate_prefix"] and view["generation"] == "gen-a"


def _delta(root, events, start=None, prior=None):
    from sandbox.mind.step2.test_shared_index_builder import (
        APPROVED, POLICY_BYTES, POLICY_SHA, ROSTER_BYTES, ROSTER_SHA,
        PUBLIC_POLICY_BYTES, PUBLIC_POLICY_SHA, _real_public_snapshot, _public_source,
    )
    _, anchor_sha, anchor_bytes, start = _real_public_snapshot(snapshot, root, events, start=start)
    anchor = json.loads(anchor_bytes)
    if prior is None:
        prior = BridgePrefixPin(start, hashlib.sha256(b"").hexdigest())
    result = snapshot.read_anchored_delta(
        anchor["source_path"], start, APPROVED, anchor_bytes=anchor_bytes, anchor_sha256=anchor_sha,
        generation_path=anchor["generation_path"], max_bytes=1 << 20, max_rows=100,
        policy_bytes=POLICY_BYTES, policy_sha256=POLICY_SHA, roster_bytes=ROSTER_BYTES,
        roster_sha256=ROSTER_SHA, public_policy_bytes=PUBLIC_POLICY_BYTES,
        public_policy_sha256=PUBLIC_POLICY_SHA, expected_prefix_sha256=prior.sha256,
        max_prefix_bytes=1 << 20)
    source = _public_source(result, anchor_sha, anchor_bytes)
    source["expected_start_prefix"] = prior
    kwargs = dict(policy_bytes=POLICY_BYTES, expected_policy_sha256=POLICY_SHA,
        roster_bytes=ROSTER_BYTES, expected_roster_sha256=ROSTER_SHA,
        expected_anchor_sha256=anchor_sha, expected_mode="public-v1",
        expected_public_policy_sha256=PUBLIC_POLICY_SHA, public_policy_bytes=PUBLIC_POLICY_BYTES,
        expected_anchor_bytes=anchor_bytes)
    return result, source, kwargs, prior


def _read(root, pointer_sha):
    from sandbox.mind.step2.test_shared_index_builder import (
        APPROVED, POLICY_BYTES, POLICY_SHA, ROSTER_BYTES, ROSTER_SHA, PUBLIC_POLICY_SHA,
    )
    return checkpoint.read_verified_prefix_pointer(root, APPROVED, expected_pointer_sha256=pointer_sha,
        policy_bytes=POLICY_BYTES, policy_sha256=POLICY_SHA, roster_bytes=ROSTER_BYTES,
        roster_sha256=ROSTER_SHA, expected_public_policy_sha256=PUBLIC_POLICY_SHA)


def test_prefix_genesis_empty_history_cas_no_legacy_downgrade(tmp_path):
    pytest.importorskip("faiss")
    from sandbox.mind.step2.test_shared_index_builder import (
        APPROVED, PUBLIC_EVENTS, LATER_PUBLIC_EVENTS, POLICY_BYTES, POLICY_SHA, ROSTER_BYTES, ROSTER_SHA,
        PUBLIC_POLICY_SHA, _artifacts_of, build, event, predecessor_of, sha,
    )
    root = tmp_path / "memory"
    root.mkdir()
    a, source_a, kw_a, start = _delta(tmp_path / "log", PUBLIC_EVENTS)
    first = build(root, name="gen-a", proj=a["projection"], approved_tasks=None, roster=None,
                  source=source_a, allow_unbound_source=False)
    genesis_receipt = checkpoint.commit_prefix_genesis(root, "gen-a", start.cursor, a["candidate_cursor"], APPROVED,
        expected_pointer_sha256=None, expected_artifacts=_artifacts_of(first), initial_cursor=start.cursor,
        snapshot_result=a, expected_start_prefix=start, **kw_a)
    assert start.sha256 not in json.dumps(genesis_receipt)
    assert a["candidate_prefix"].sha256 not in json.dumps(genesis_receipt)
    pointer0 = sha((root / checkpoint.POINTER).read_bytes())
    view, first_pin = _read(root, pointer0)
    assert first_pin == a["candidate_prefix"] and view["generation"] == "gen-a"
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint.read_verified_pointer(root, APPROVED, expected_pointer_sha256=pointer0,
            policy_bytes=POLICY_BYTES, policy_sha256=POLICY_SHA, roster_bytes=ROSTER_BYTES,
            roster_sha256=ROSTER_SHA, expected_mode="public-v1", expected_public_policy_sha256=PUBLIC_POLICY_SHA)
    empty, _, kw_empty, _ = _delta(tmp_path / "log", [event(agent="claude-rco-1")], first_pin.cursor, first_pin)
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint.advance_prefix_empty(root, empty, APPROVED, expected_pointer_sha256=pointer0,
            expected_start_prefix=replace(first_pin, sha256="0" * 64), **kw_empty)
    assert sha((root / checkpoint.POINTER).read_bytes()) == pointer0
    empty_out = checkpoint.advance_prefix_empty(root, empty, APPROVED, expected_pointer_sha256=pointer0,
                                                expected_start_prefix=first_pin, **kw_empty)
    pointer1 = empty_out["pointer_sha256"]
    _, empty_pin = _read(root, pointer1)
    assert empty_pin == empty["candidate_prefix"]
    b, source_b, kw_b, _ = _delta(tmp_path / "log", LATER_PUBLIC_EVENTS, empty_pin.cursor, empty_pin)
    second = build(root, name="gen-b", proj=b["projection"], approved_tasks=None, roster=None,
        source=source_b, allow_unbound_source=False,
        predecessor=dict(predecessor_of(root, "gen-a", first),
                         watermark={"root": str(root), "pointer_sha256": pointer1}))
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint.commit_prefix_history(root, "gen-b", empty_pin.cursor, b["candidate_cursor"], APPROVED,
            snapshot_result=b, expected_pointer_sha256=pointer0, expected_artifacts=_artifacts_of(second),
            expected_start_prefix=empty_pin, **kw_b)
    assert sha((root / checkpoint.POINTER).read_bytes()) == pointer1
    history_receipt = checkpoint.commit_prefix_history(root, "gen-b", empty_pin.cursor, b["candidate_cursor"], APPROVED,
        snapshot_result=b, expected_pointer_sha256=pointer1, expected_artifacts=_artifacts_of(second),
        expected_start_prefix=empty_pin, **kw_b)
    assert empty_pin.sha256 not in json.dumps(history_receipt)
    assert b["candidate_prefix"].sha256 not in json.dumps(history_receipt)
    pointer2 = sha((root / checkpoint.POINTER).read_bytes())
    view, end_pin = _read(root, pointer2)
    assert view["generation"] == "gen-b" and end_pin == b["candidate_prefix"]
    receipt = json.loads((root / "gen-b" / "build_receipt.json").read_bytes())
    assert empty_pin.sha256 not in json.dumps(receipt)
