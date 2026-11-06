"""Synthetic public/legacy isolation checks for Q4 checkpoint bindings."""
import pytest
import hashlib
import json
import os
from dataclasses import asdict

from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
from sandbox.mind.step2.test_shared_producer import config, public_config  # synthetic fixtures
from sandbox.mind.step2.test_shared_index_checkpoint import POLICY, ROSTER, STRICT, cursor, binding


PUBLIC = {"status_mode": "public-v1", "public_policy_sha256": "a" * 64}
LEGACY = {"status_mode": "legacy", "public_policy_sha256": None}


def test_mode_contract_accepts_legacy_and_exact_public_pin():
    assert checkpoint._source_mode({}, {}) == LEGACY
    assert checkpoint._source_mode({"source_mode": PUBLIC}, {"source_mode": PUBLIC}) == PUBLIC


@pytest.mark.parametrize("memory,receipt", [
    ({"source_mode": PUBLIC}, {}),
    ({}, {"source_mode": PUBLIC}),
    ({"source_mode": PUBLIC}, {"source_mode": {**PUBLIC, "public_policy_sha256": "b" * 64}}),
    ({"source_mode": {**PUBLIC, "extra": True}}, {"source_mode": PUBLIC}),
    ({"source_mode": {"status_mode": "public-v1", "public_policy_sha256": None}},
     {"source_mode": {"status_mode": "public-v1", "public_policy_sha256": None}}),
])
def test_mode_mismatch_or_malformed_refused(memory, receipt):
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint._source_mode(memory, receipt)


def test_read_does_not_compare_new_delta_anchor_to_old_pointer(monkeypatch, tmp_path):
    # A reader authenticates its predecessor pointer, not the next input's anchor.
    seen = []
    monkeypatch.setattr(checkpoint, "_secure", lambda p, **kw: p)
    monkeypatch.setattr(checkpoint, "_read", lambda *a, **kw: b"pointer")
    monkeypatch.setattr(checkpoint, "_validated_previous", lambda *a, **kw: (
        seen.append(kw), {"generation": "first", "artifacts": {}, "anchor_sha256": "b" * 64,
                          "cursor_end": {"offset": 0, "file_identity": "synthetic", "generation": "s"},
                          "watermark": {"offset": 0, "file_identity": "synthetic", "generation": "s"}})[1])
    from sandbox.mind.step2 import shared_capture_adapter
    monkeypatch.setattr(shared_capture_adapter, "strict_pins", lambda *a: (frozenset(), {}))
    result = checkpoint.read_verified_pointer(tmp_path, frozenset(),
        expected_pointer_sha256=hashlib.sha256(b"pointer").hexdigest(), policy_bytes=b"p", policy_sha256="a" * 64,
        roster_bytes=b"r", roster_sha256="b" * 64, expected_mode="public-v1",
        expected_public_policy_sha256=PUBLIC["public_policy_sha256"])
    assert result["anchor_sha256"] == "b" * 64
    assert seen[0]["expected_source_mode"] == PUBLIC


def test_public_history_cannot_splice_in_a_legacy_predecessor(tmp_path, monkeypatch):
    art1 = {"descriptor_sha256": "1" * 64, "memory_sha256": "2" * 64,
            "projection_sha256": "3" * 64, "receipt_sha256": "4" * 64}
    art2 = {"descriptor_sha256": "5" * 64, "memory_sha256": "6" * 64,
            "projection_sha256": "7" * 64, "receipt_sha256": "8" * 64}
    def pointer(name, artifacts, start, end):
        return {"schema": checkpoint.SCHEMA, "generation": name, "artifacts": artifacts,
            "policy_sha256": STRICT["expected_policy_sha256"], "roster_sha256": STRICT["expected_roster_sha256"],
            "anchor_sha256": STRICT["expected_anchor_sha256"], "cursor_start": asdict(cursor(start)),
            "cursor_end": asdict(cursor(end)), "watermark": asdict(cursor(end)), "empty_advance": None,
            "previous": None, "previous_pointer_sha256": None}
    old = pointer("old", art1, 0, 1)
    old_raw = checkpoint._canonical(old)
    old_hash = hashlib.sha256(old_raw).hexdigest()
    current = pointer("new", art2, 1, 2)
    current.update(previous="old", previous_pointer_sha256=old_hash)
    monkeypatch.setattr(checkpoint, "_read", lambda path, **kw: old_raw)
    monkeypatch.setattr(checkpoint, "_verify_generation", lambda root, name, *args:
        ({"generation": "new", **art2, "source_binding": binding(cursor(1), cursor(2), art2["projection_sha256"]),
          "source_mode": PUBLIC} if name == "new" else
         {"generation": "old", **art1, "source_binding": binding(cursor(0), cursor(1), art1["projection_sha256"]),
          "source_mode": LEGACY}))
    monkeypatch.setattr(checkpoint, "_archived_generation_mode", lambda *args: LEGACY)
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint._validated_previous(tmp_path, checkpoint._canonical(current), POLICY, ROSTER,
            STRICT["expected_policy_sha256"], STRICT["expected_roster_sha256"], expected_source_mode=PUBLIC)
    # Even an all-public predecessor must not authorize a second delta-only
    # v3 generation: it needs v4 union evidence, not merely cursor continuity.
    monkeypatch.setattr(checkpoint, "_archived_generation_mode", lambda *args: PUBLIC)
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint._validated_previous(tmp_path, checkpoint._canonical(current), POLICY, ROSTER,
            STRICT["expected_policy_sha256"], STRICT["expected_roster_sha256"], expected_source_mode=PUBLIC)


def test_archived_mode_checks_metadata_hashes_and_history_budget(tmp_path, monkeypatch):
    memory = json.dumps({"source_mode": PUBLIC}).encode()
    receipt = json.dumps({"source_mode": PUBLIC}).encode()
    data = {"memory.json": memory, "build_receipt.json": receipt}
    pointer = {"generation": "old", "artifacts": {
        "memory_sha256": hashlib.sha256(memory).hexdigest(),
        "receipt_sha256": hashlib.sha256(receipt).hexdigest()}}
    monkeypatch.setattr(checkpoint, "_read", lambda path, **kw: data[path.name])
    budget = [0]
    assert checkpoint._archived_generation_mode(tmp_path, pointer, budget) == PUBLIC
    assert budget[0] == len(memory) + len(receipt)
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint._archived_generation_mode(tmp_path, pointer, [checkpoint._MAX_HISTORY_BYTES])
    data["build_receipt.json"] = b"{}"
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint._archived_generation_mode(tmp_path, pointer, [0])


def test_public_history_commit_accounts_for_previously_active_generation(tmp_path, monkeypatch):
    """The next reader must be able to budget both old metadata and the new delta."""
    from contextlib import nullcontext
    from sandbox.mind.step2 import shared_bridge_snapshot as snapshot, shared_capture_adapter as adapter

    old_dir = tmp_path / "old"
    old_dir.mkdir()
    memory = json.dumps({"source_mode": PUBLIC}).encode()
    receipt = json.dumps({"source_mode": PUBLIC}).encode()
    (old_dir / "memory.json").write_bytes(memory)
    (old_dir / "build_receipt.json").write_bytes(receipt)
    old = {"generation": "old", "watermark": asdict(cursor(1)), "artifacts": {
        "memory_sha256": hashlib.sha256(memory).hexdigest(),
        "receipt_sha256": hashlib.sha256(receipt).hexdigest()}}
    before = checkpoint._canonical(old)
    (tmp_path / checkpoint.POINTER).write_bytes(before)
    pointer_hash = hashlib.sha256(before).hexdigest()
    delta = {"records": [], "padding": "x" * 512}
    delta_bytes = json.dumps(delta, sort_keys=True, indent=1).encode("utf-8")
    token_hash = lambda value: hashlib.sha256(snapshot._token(value).encode("utf-8")).hexdigest()
    source = {"coverage": "delta_only_not_complete_history",
              "cursor_start_sha256": token_hash(cursor(1)), "cursor_end_sha256": token_hash(cursor(2))}
    result = {"candidate_cursor": cursor(2), "projection": delta, "source": source}
    artifacts = {"descriptor_sha256": "1" * 64, "memory_sha256": "2" * 64,
                 "projection_sha256": "3" * 64, "receipt_sha256": "4" * 64}
    # Either metadata file alone and the new delta fit; both together do not.
    limit = len(delta_bytes) + max(len(memory), len(receipt))
    assert len(memory) + len(receipt) <= limit < len(memory) + len(receipt) + len(delta_bytes)
    monkeypatch.setattr(checkpoint, "_MAX_HISTORY_BYTES", limit)
    monkeypatch.setattr(checkpoint, "_secure", lambda path, **kw: path)
    monkeypatch.setattr(checkpoint, "reader_slot", lambda *a, **kw: nullcontext())
    monkeypatch.setattr(adapter, "strict_pins", lambda *a: (frozenset({"public-task"}), {}))
    monkeypatch.setattr(checkpoint, "_snapshot_mode_args", lambda *a: {})
    monkeypatch.setattr(snapshot, "verify_anchored_projection", lambda *a, **kw: True)
    monkeypatch.setattr(checkpoint, "_validated_previous", lambda *a, **kw: old)
    monkeypatch.setattr(checkpoint, "_verify_generation", lambda *a: {**artifacts, "source_mode": PUBLIC})
    charged = []
    def charge_new_transition(*args, delta_bytes, **kwargs):
        budget = args[-1]
        charged.append(budget[0])
        budget[0] += len(delta_bytes)
        if budget[0] > checkpoint._MAX_HISTORY_BYTES:
            checkpoint._refuse()
    monkeypatch.setattr(checkpoint, "_validate_history_transition", charge_new_transition)
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint.commit_history_generation(tmp_path, "new", cursor(1), cursor(2), frozenset({"public-task"}),
            snapshot_result=result, expected_pointer_sha256=pointer_hash, expected_artifacts=artifacts,
            policy_bytes=b"policy", expected_policy_sha256="5" * 64,
            roster_bytes=b"roster", expected_roster_sha256="6" * 64,
            expected_anchor_sha256="7" * 64, expected_mode="public-v1",
            expected_public_policy_sha256=PUBLIC["public_policy_sha256"], public_policy_bytes=b"public",
            expected_anchor_bytes=b"anchor")
    assert charged == [len(memory) + len(receipt)]
    assert (tmp_path / checkpoint.POINTER).read_bytes() == before


def test_public_empty_advance_rejects_delta_changed_during_second_history_check(tmp_path, monkeypatch):
    """The second history walk cannot authorize a changed caller-owned delta."""
    from contextlib import nullcontext
    from sandbox.mind.step2 import shared_bridge_snapshot as snapshot, shared_capture_adapter as adapter
    from sandbox.mind.step2 import shared_projection

    old = {"generation": "old", "watermark": asdict(cursor(1))}
    before = checkpoint._canonical(old)
    (tmp_path / checkpoint.POINTER).write_bytes(before)
    pointer_hash = hashlib.sha256(before).hexdigest()
    delta = {"accepted": 0, "records": [], "refused": {}, "dropped_fields": 0}
    token_hash = lambda value: hashlib.sha256(snapshot._token(value).encode("utf-8")).hexdigest()
    source = {"coverage": "delta_only_not_complete_history",
              "cursor_start_sha256": token_hash(cursor(1)), "cursor_end_sha256": token_hash(cursor(2))}
    result = {"candidate_cursor": cursor(2), "projection": delta, "source": source}
    monkeypatch.setattr(checkpoint, "_secure", lambda path, **kw: path)
    monkeypatch.setattr(checkpoint, "reader_slot", lambda *a, **kw: nullcontext())
    monkeypatch.setattr(adapter, "strict_pins", lambda *a: (frozenset({"public-task"}), {}))
    monkeypatch.setattr(checkpoint, "_snapshot_mode_args", lambda *a: {})
    # Keep the verifier permissive to prove the canonical-byte check itself
    # protects publication even if a separate verifier misses this mutation.
    monkeypatch.setattr(snapshot, "verify_anchored_projection", lambda *a, **kw: True)
    monkeypatch.setattr(shared_projection, "validate_projection", lambda *a, **kw: None)
    calls = []
    def walk(*args, **kwargs):
        calls.append(kwargs.get("reserve", 0))
        if len(calls) == 2:
            delta["records"].append({"synthetic": "changed during history check"})
        return old
    monkeypatch.setattr(checkpoint, "_validated_previous", walk)
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint.advance_empty_delta(tmp_path, result, frozenset({"public-task"}),
            expected_pointer_sha256=pointer_hash, policy_bytes=b"policy", expected_policy_sha256="5" * 64,
            roster_bytes=b"roster", expected_roster_sha256="6" * 64, expected_anchor_sha256="7" * 64,
            expected_mode="public-v1", expected_public_policy_sha256=PUBLIC["public_policy_sha256"],
            public_policy_bytes=b"public", expected_anchor_bytes=b"anchor")
    assert calls == [0, 1]
    assert (tmp_path / checkpoint.POINTER).read_bytes() == before


def test_public_empty_advance_requires_versioned_policy_binding(tmp_path, monkeypatch):
    from sandbox.mind.step2 import shared_projection as projection
    from sandbox.mind.step2.shared_bridge_snapshot import _token
    artifacts = {"descriptor_sha256": "a" * 64, "memory_sha256": "b" * 64,
                 "projection_sha256": "c" * 64, "receipt_sha256": "d" * 64}
    old = {"schema": checkpoint.SCHEMA, "generation": "one", "artifacts": artifacts,
           "policy_sha256": STRICT["expected_policy_sha256"], "roster_sha256": STRICT["expected_roster_sha256"],
           "anchor_sha256": STRICT["expected_anchor_sha256"], "cursor_start": asdict(cursor(0)),
           "cursor_end": asdict(cursor(1)), "watermark": asdict(cursor(1)), "empty_advance": None,
           "previous": None, "previous_pointer_sha256": None}
    old_raw = checkpoint._canonical(old)
    token_hash = lambda item: hashlib.sha256(_token(item).encode()).hexdigest()
    empty_hash = hashlib.sha256(checkpoint._canonical(projection._projection([], {}, 0, ROSTER, True))).hexdigest()
    advance = {"schema": "wd.q4-empty-watermark.v2", "anchor_sha256": "f" * 64,
               "projection_sha256": empty_hash, "cursor_start_sha256": token_hash(cursor(1)),
               "cursor_end_sha256": token_hash(cursor(2)), "policy_sha256": STRICT["expected_policy_sha256"],
               "roster_sha256": STRICT["expected_roster_sha256"], "source_mode": PUBLIC}
    current = dict(old, watermark=asdict(cursor(2)), previous="one",
                   previous_pointer_sha256=hashlib.sha256(old_raw).hexdigest(), empty_advance=advance)
    monkeypatch.setattr(checkpoint, "_read", lambda path, **kw: old_raw)
    monkeypatch.setattr(checkpoint, "_verify_generation", lambda *args: {
        "generation": "one", **artifacts, "source_binding": binding(cursor(0), cursor(1)), "source_mode": PUBLIC})
    args = (tmp_path, checkpoint._canonical(current), POLICY, ROSTER,
            STRICT["expected_policy_sha256"], STRICT["expected_roster_sha256"])
    assert checkpoint._validated_previous(*args, expected_source_mode=PUBLIC) == current
    legacy_advance = {key: value for key, value in advance.items() if key != "source_mode"}
    legacy_advance["schema"] = "wd.q4-empty-watermark.v1"
    bad = dict(current, empty_advance=legacy_advance)
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint._validated_previous(tmp_path, checkpoint._canonical(bad), *args[2:], expected_source_mode=PUBLIC)
    bad = dict(current, empty_advance={**advance, "source_mode": {**PUBLIC, "public_policy_sha256": "9" * 64}})
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint._validated_previous(tmp_path, checkpoint._canonical(bad), *args[2:], expected_source_mode=PUBLIC)


@pytest.mark.skipif(os.name != "nt", reason="checkpoint requires fixed Windows volume")
def test_first_public_generation_exact_anchor_and_mode_or_no_pointer(public_config, tmp_path, monkeypatch):
    from sandbox.mind.step2 import shared_producer as producer
    from sandbox.mind.step2.shared_bridge_snapshot import _token
    source = public_config.source
    result = producer.prepare_public_delta(enabled=True, config=public_config).result
    end = result["candidate_cursor"]
    projection = json.dumps(result["projection"], sort_keys=True, indent=1).encode("utf-8")
    artifacts = {"descriptor_sha256": "a" * 64, "memory_sha256": "b" * 64,
                 "projection_sha256": hashlib.sha256(projection).hexdigest(), "receipt_sha256": "d" * 64}
    mode = {"status_mode": "public-v1", "public_policy_sha256": public_config.public_policy_sha256}
    binding = checkpoint._source_binding(source.cursor, end, source.anchor_sha256,
        artifacts["projection_sha256"], source.policy_sha256, source.roster_sha256)
    actual = {"generation": "first", **artifacts, "source_binding": binding, "source_mode": mode}
    monkeypatch.setattr(checkpoint, "_verify_generation", lambda *a: actual)
    kwargs = dict(expected_pointer_sha256=None, expected_artifacts=artifacts, initial_cursor=source.cursor,
        policy_bytes=source.policy_bytes, expected_policy_sha256=source.policy_sha256,
        roster_bytes=source.roster_bytes, expected_roster_sha256=source.roster_sha256,
        expected_anchor_sha256=source.anchor_sha256, expected_mode="public-v1",
        expected_public_policy_sha256=public_config.public_policy_sha256,
        public_policy_bytes=public_config.public_policy_bytes, expected_anchor_bytes=source.anchor_bytes,
        snapshot_result=result)
    bad_root = tmp_path / "bad"
    bad_root.mkdir()
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint.commit_generation(bad_root, "first", source.cursor, end, producer.adapter.strict_pins(
            source.policy_bytes, source.policy_sha256, source.roster_bytes, source.roster_sha256)[0],
            **{**kwargs, "expected_anchor_bytes": b"wrong"})
    assert not (bad_root / checkpoint.POINTER).exists()
    root = tmp_path / "good"
    root.mkdir()
    tasks = producer.adapter.strict_pins(source.policy_bytes, source.policy_sha256,
                                          source.roster_bytes, source.roster_sha256)[0]
    value = checkpoint.commit_generation(root, "first", source.cursor, end, tasks, **kwargs)
    raw = (root / checkpoint.POINTER).read_bytes()
    result_view = checkpoint.read_verified_pointer(root, tasks, expected_pointer_sha256=hashlib.sha256(raw).hexdigest(),
        policy_bytes=source.policy_bytes, policy_sha256=source.policy_sha256, roster_bytes=source.roster_bytes,
        roster_sha256=source.roster_sha256, expected_mode="public-v1",
        expected_public_policy_sha256=public_config.public_policy_sha256)
    assert result_view["anchor_sha256"] == source.anchor_sha256
    assert result_view["watermark_sha256"] == hashlib.sha256(_token(end).encode()).hexdigest()
    assert value["schema"] == checkpoint.SCHEMA  # Existing pointer wire schema is unchanged.
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint.read_verified_pointer(root, tasks, expected_pointer_sha256=hashlib.sha256(raw).hexdigest(),
            policy_bytes=source.policy_bytes, policy_sha256=source.policy_sha256,
            roster_bytes=source.roster_bytes, roster_sha256=source.roster_sha256)
