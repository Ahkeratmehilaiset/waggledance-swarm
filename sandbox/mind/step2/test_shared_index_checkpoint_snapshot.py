"""Synthetic tests for the checkpoint's independently pinned pointer parser.

Parsing a pointer does not validate its generation artifacts or history.
"""
import hashlib
import json

import pytest

from sandbox.mind.step2 import shared_index_checkpoint as checkpoint


def _pointer():
    cursor = {"file_identity": "posix-v1:1:2", "offset": 0, "generation": None}
    value = {
        "schema": checkpoint.SCHEMA,
        "generation": "generation-1",
        "artifacts": {name: "a" * 64 for name in checkpoint.ARTIFACTS},
        "policy_sha256": "b" * 64,
        "roster_sha256": "c" * 64,
        "anchor_sha256": "d" * 64,
        "cursor_start": cursor,
        "cursor_end": cursor,
        "watermark": cursor,
        "empty_advance": None,
        "previous": None,
        "previous_pointer_sha256": None,
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def test_pointer_snapshot_uses_independent_pin_without_filesystem_reads(monkeypatch):
    raw = _pointer()
    digest = hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(checkpoint, "_read", lambda *a, **k: pytest.fail("unexpected path read"))
    parsed = checkpoint._parse_pointer_snapshot(
        raw, expected_pointer_sha256=digest, policy_sha="b" * 64,
        roster_sha="c" * 64, prefix_required=False,
    )
    assert parsed["generation"] == "generation-1"


@pytest.mark.parametrize("expected", [None, "e" * 64, "BAD"])
def test_pointer_snapshot_refuses_missing_or_incorrect_external_pin(expected):
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint._parse_pointer_snapshot(
            _pointer(), expected_pointer_sha256=expected, policy_sha="b" * 64,
            roster_sha="c" * 64, prefix_required=False,
        )


def test_pointer_snapshot_reuses_full_pointer_schema_check():
    raw = _pointer().replace(b'"generation-1"', b'"../outside"')
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint._parse_pointer_snapshot(
            raw, expected_pointer_sha256=hashlib.sha256(raw).hexdigest(),
            policy_sha="b" * 64, roster_sha="c" * 64, prefix_required=False,
        )


def test_current_validator_uses_the_same_pointer_parser(monkeypatch):
    from sandbox.mind.step2 import shared_projection

    raw = _pointer()
    seen = []
    monkeypatch.setattr(shared_projection, "_projection", lambda *args: {})

    def sentinel(data, **kwargs):
        seen.append((data, kwargs))
        raise RuntimeError("synthetic sentinel")

    monkeypatch.setattr(checkpoint, "_parse_bound_pointer", sentinel)
    with pytest.raises(RuntimeError, match="synthetic sentinel"):
        checkpoint._validated_previous(None, raw, {}, {}, "b" * 64, "c" * 64)
    assert seen == [(raw, {"policy_sha": "b" * 64, "roster_sha": "c" * 64,
                           "prefix_required": False})]


@pytest.mark.parametrize("invalid_generation", [False, True])
def test_pinned_snapshot_calls_shared_pointer_parser(monkeypatch, invalid_generation):
    raw = _pointer()
    if invalid_generation:
        raw = raw.replace(b'"generation-1"', b'"../outside"')
    parser = checkpoint._pointer_contract._parse_bound_pointer
    seen = []

    def record_call(data, **kwargs):
        seen.append((data, kwargs))
        return parser(data, **kwargs)

    monkeypatch.setattr(checkpoint._pointer_contract, "_parse_bound_pointer", record_call)
    args = {"expected_pointer_sha256": hashlib.sha256(raw).hexdigest(),
            "policy_sha": "b" * 64, "roster_sha": "c" * 64, "prefix_required": False}
    if invalid_generation:
        with pytest.raises(checkpoint.CheckpointRefused):
            checkpoint._parse_pointer_snapshot(raw, **args)
    else:
        assert checkpoint._parse_pointer_snapshot(raw, **args)["generation"] == "generation-1"
    assert seen == [(raw, {"policy_sha": "b" * 64, "roster_sha": "c" * 64,
                           "prefix_required": False})]


def test_snapshot_requires_expected_prefix_schema():
    raw = _pointer()
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint._parse_pointer_snapshot(
            raw, expected_pointer_sha256=hashlib.sha256(raw).hexdigest(),
            policy_sha="b" * 64, roster_sha="c" * 64, prefix_required=True,
        )


def test_archived_mode_uses_only_pinned_snapshot_bytes(monkeypatch):
    raw = b'{}'
    pointer = {"generation": "gen-a", "artifacts": {
        "memory_sha256": hashlib.sha256(raw).hexdigest(),
        "receipt_sha256": hashlib.sha256(raw).hexdigest(),
    }}
    snapshot = {"gen-a/memory.json": raw, "gen-a/build_receipt.json": raw}
    monkeypatch.setattr(checkpoint, "_read", lambda *a, **kw: pytest.fail("disk fallback"))
    budget = [0]
    assert checkpoint._archived_generation_mode(None, pointer, budget, snapshot_bytes=snapshot) == {
        "status_mode": "legacy", "public_policy_sha256": None,
    }
    assert budget == [2 * len(raw)]


def test_archived_mode_snapshot_missing_or_changed_never_falls_back(monkeypatch):
    raw = b'{}'
    digest = hashlib.sha256(raw).hexdigest()
    pointer = {"generation": "gen-a", "artifacts": {"memory_sha256": digest, "receipt_sha256": digest}}
    monkeypatch.setattr(checkpoint, "_read", lambda *a, **kw: pytest.fail("disk fallback"))
    for snapshot in ({"gen-a/memory.json": raw},
                     {"gen-a/memory.json": b'{"tampered":true}', "gen-a/build_receipt.json": raw}):
        with pytest.raises(checkpoint.CheckpointRefused):
            checkpoint._archived_generation_mode(None, pointer, [0], snapshot_bytes=snapshot)


@pytest.mark.parametrize("snapshot", [
    {"../private": b'{}'},
    {"gen-a/memory.json": bytearray(b'{}')},
    {"gen-a\\memory.json": b'{}'},
])
def test_history_snapshot_rejects_unsafe_names_and_mutable_bytes(snapshot):
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint._freeze_history_snapshot(snapshot)


def test_history_snapshot_respects_budget(monkeypatch):
    monkeypatch.setattr(checkpoint, "_MAX_HISTORY_BYTES", 3)
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint._freeze_history_snapshot({"gen-a/memory.json": b'{}',
                                              "gen-a/build_receipt.json": b'{}'})


def test_history_transition_snapshot_validates_artifact_hash_before_parsing(monkeypatch):
    pointer = {"generation": "gen-a", "artifacts": {"projection_sha256": "a" * 64}}
    monkeypatch.setattr(checkpoint, "_read", lambda *a, **kw: pytest.fail("disk fallback"))
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint._validate_history_transition(
            None, pointer, pointer, "b" * 64, frozenset(), {}, "c" * 64, "d" * 64,
            b'{}', b'{}', [0], snapshot_bytes={"gen-a/projection.json": b'{}'},
        )


def test_history_transition_snapshot_uses_frozen_map_and_existing_receipt_checks(monkeypatch):
    from sandbox.mind.step2 import shared_history, shared_projection
    from waggledance.core.bridge_log_reader import BridgeCursor

    def enc(value):
        return json.dumps(value, sort_keys=True, indent=1).encode()

    def sha(raw):
        return hashlib.sha256(raw).hexdigest()

    old_projection = {"records": [{"record_id": "one"}], "refused": {}}
    delta = {"records": [{"record_id": "two"}], "refused": {}}
    merged = {"records": old_projection["records"] + delta["records"], "refused": {}}
    old_raw, delta_raw, merged_raw = map(enc, (old_projection, delta, merged))
    cursor = {"file_identity": "posix-v1:1:2", "offset": 0, "generation": None}
    old = {"schema": checkpoint.SCHEMA, "generation": "gen-a", "cursor_start": cursor,
           "cursor_end": cursor, "watermark": cursor, "anchor_sha256": "a" * 64,
           "artifacts": {"projection_sha256": sha(old_raw), "memory_sha256": "b" * 64}}
    new = {"schema": checkpoint.HISTORY_SCHEMA, "generation": "gen-b", "cursor_start": cursor,
           "cursor_end": cursor, "watermark": cursor, "anchor_sha256": "c" * 64,
           "delta_projection_sha256": sha(delta_raw), "artifacts": {"projection_sha256": sha(merged_raw)}}
    old_binding = checkpoint._source_binding(BridgeCursor(**cursor), BridgeCursor(**cursor),
                                              old["anchor_sha256"], sha(old_raw), "d" * 64, "e" * 64)
    old_receipt = enc({"source_binding": old_binding})
    old["artifacts"]["receipt_sha256"] = sha(old_receipt)
    summary = {"memory_sha256": old["artifacts"]["memory_sha256"], "projection_sha256": sha(old_raw),
               "build_receipt_sha256": sha(old_receipt), "records": 1, "source_bound": True,
               "cursor_end_sha256": old_binding["cursor_end_sha256"]}
    history = {"schema": "wd.q4-shared-index-history.v1", "helper": "sandbox.mind.step2.shared_history",
               "predecessor": summary, "delta_projection_sha256": sha(delta_raw), "delta_records": 1,
               "delta_refused": {}, "overlap_records": 0, "merged_records": 2,
               "merged_projection_sha256": sha(merged_raw), "continuity_checked": True,
               "continuity_route": "receipt_end", "watermark": None}
    memory = enc({"history": {"predecessor_memory_sha256": old["artifacts"]["memory_sha256"],
                              "predecessor_projection_sha256": sha(old_raw),
                              "delta_projection_sha256": sha(delta_raw)}})
    new_binding = checkpoint._source_binding(BridgeCursor(**cursor), BridgeCursor(**cursor),
                                              new["anchor_sha256"], sha(delta_raw), "d" * 64, "e" * 64)
    receipt = enc({"source_binding": new_binding, "history": history,
                   "delta_projection_sha256": sha(delta_raw)})
    new["artifacts"].update(memory_sha256=sha(memory), receipt_sha256=sha(receipt))
    snapshot = {"gen-a/projection.json": old_raw, "gen-b/projection.json": merged_raw,
                "q4-delta-" + sha(delta_raw) + ".json": delta_raw,
                "gen-a/build_receipt.json": old_receipt, "gen-b/build_receipt.json": receipt,
                "gen-b/memory.json": memory}
    monkeypatch.setattr(checkpoint, "_read", lambda *a, **kw: pytest.fail("disk fallback"))
    monkeypatch.setattr(shared_projection, "validate_projection", lambda *a, **kw: None)

    def merge(*args, **kwargs):
        snapshot["gen-b/build_receipt.json"] = b"changed after snapshot"
        return merged

    monkeypatch.setattr(shared_history, "merge_projections", merge)
    checkpoint._validate_history_transition(None, new, old, "f" * 64, frozenset(), {}, "d" * 64,
                                            "e" * 64, b'{}', b'{}', [0], snapshot_bytes=snapshot)


def _two_pointer_history():
    previous = json.loads(_pointer())
    previous["generation"] = "gen-a"
    previous_raw = checkpoint._canonical(previous)
    previous_hash = hashlib.sha256(previous_raw).hexdigest()
    current = dict(previous, generation="gen-b", previous="gen-a", previous_pointer_sha256=previous_hash)
    raw = checkpoint._canonical(current)
    return raw, current, {"q4-pointer-" + previous_hash + ".json": previous_raw}


def test_snapshot_history_traversal_uses_existing_rules_without_disk(monkeypatch):
    raw, current, snapshot = _two_pointer_history()
    monkeypatch.setattr(checkpoint, "_read", lambda *a, **kw: pytest.fail("disk fallback"))
    result = checkpoint._validate_pointer_history(
        None, raw, current, frozenset(), {}, "b" * 64, "c" * 64, snapshot_bytes=snapshot,
    )
    assert result == current


@pytest.mark.parametrize("mode", ["missing", "tampered"])
def test_snapshot_history_refuses_missing_or_tampered_archive(monkeypatch, mode):
    raw, current, snapshot = _two_pointer_history()
    key = next(iter(snapshot))
    if mode == "missing":
        del snapshot[key]
    else:
        snapshot[key] += b" "
    monkeypatch.setattr(checkpoint, "_read", lambda *a, **kw: pytest.fail("disk fallback"))
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint._validate_pointer_history(
            None, raw, current, frozenset(), {}, "b" * 64, "c" * 64, snapshot_bytes=snapshot,
        )


def test_snapshot_checkpoint_refuses_when_active_cell_has_no_bytes_verifier(monkeypatch):
    raw, _, snapshot = _two_pointer_history()
    monkeypatch.setattr(checkpoint, "_read", lambda *a, **kw: pytest.fail("disk fallback"))
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint._read_snapshot_checkpoint(
            raw, expected_pointer_sha256=hashlib.sha256(raw).hexdigest(),
            tasks=frozenset(), roster={}, policy_sha="b" * 64, roster_sha="c" * 64,
            snapshot_bytes=snapshot,
        )


def test_snapshot_history_routes_bound_map_to_existing_delta_validator(monkeypatch):
    raw, current, snapshot = _two_pointer_history()
    delta = b'{"records": []}'
    digest = hashlib.sha256(delta).hexdigest()
    current.update(schema=checkpoint.HISTORY_SCHEMA, delta_projection_sha256=digest)
    raw = checkpoint._canonical(current)
    snapshot["q4-delta-" + digest + ".json"] = delta
    seen = []
    monkeypatch.setattr(checkpoint, "_read", lambda *a, **kw: pytest.fail("disk fallback"))
    monkeypatch.setattr(checkpoint, "_validate_history_transition",
                        lambda *args, **kw: seen.append(kw["snapshot_bytes"]["q4-delta-" + digest + ".json"]))
    checkpoint._validate_pointer_history(None, raw, current, frozenset(), {}, "b" * 64, "c" * 64,
                                          snapshot_bytes=snapshot)
    assert seen == [delta]


@pytest.mark.parametrize("entry", ["parse", "checkpoint"])
def test_malformed_snapshot_refusal_does_not_retain_private_json_context(entry):
    private_sentinel = "PRIVATE-SYNTHETIC-SENTINEL-4ef2"
    raw = ('{"unclosed":"' + private_sentinel).encode()
    digest = hashlib.sha256(raw).hexdigest()
    with pytest.raises(checkpoint.CheckpointRefused) as captured:
        if entry == "parse":
            checkpoint._parse_pointer_snapshot(raw, expected_pointer_sha256=digest,
                policy_sha="b" * 64, roster_sha="c" * 64, prefix_required=False)
        else:
            checkpoint._read_snapshot_checkpoint(raw, expected_pointer_sha256=digest,
                tasks=frozenset(), roster={}, policy_sha="b" * 64, roster_sha="c" * 64,
                snapshot_bytes={})
    assert captured.value.__context__ is None
    assert captured.value.__cause__ is None
    assert private_sentinel not in str(captured.value)
    tb = captured.value.__traceback__
    while tb:
        if tb.tb_frame.f_code.co_name in {"_parse_pointer_snapshot", "_read_snapshot_checkpoint"}:
            assert raw not in tb.tb_frame.f_locals.values()
        tb = tb.tb_next
