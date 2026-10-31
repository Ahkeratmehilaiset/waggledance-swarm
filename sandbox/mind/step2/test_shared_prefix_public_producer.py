"""Synthetic strict-prefix publication contract; no live bridge or model."""
from dataclasses import replace
import importlib
import hashlib
import json
import os
from types import SimpleNamespace

import pytest


def module():
    return importlib.import_module("sandbox.mind.step2.shared_prefix_public_producer")


def test_default_off_does_not_inspect_request_or_read_source():
    class Poison:
        def __getattribute__(self, name):
            raise AssertionError("disabled must not inspect request")
    assert module().publish_public_prefix(request=Poison()) == {"status": "OFF"}


def test_enabled_invalid_request_refuses_fixed_message_without_echo():
    mod = module()
    with pytest.raises(mod.PrefixProducerRefused, match="^request_invalid$") as error:
        mod.publish_public_prefix(request="synthetic-private", enabled=True)
    assert "synthetic-private" not in repr(error.value)


@pytest.mark.skipif(os.name != "nt", reason="private state requires Windows fixed volume")
def test_existing_pointer_without_independent_private_state_refuses_before_snapshot(tmp_path, monkeypatch):
    from waggledance.core.bridge_log_reader import BridgeCursor, BridgePrefixPin
    mod = module()
    private, shared = tmp_path / "private", tmp_path / "shared"
    private.mkdir()
    shared.mkdir()
    monkeypatch.setattr(mod, "_validate", lambda _: None)
    monkeypatch.setattr(mod, "_bind", lambda _: None)
    monkeypatch.setattr(mod.snapshot, "read_anchored_delta", lambda *a, **k: pytest.fail("must not read"))
    request = SimpleNamespace(root=shared, private_state_root=private, expected_pointer_sha256="a" * 64,
        start_prefix=BridgePrefixPin(BridgeCursor(0, "posix-v1:1:2", "synthetic"), "a" * 64), timeout=0.2)
    with pytest.raises(mod.PrefixProducerRefused, match="^private_state_refused$"):
        mod.publish_public_prefix(enabled=True, request=request)


@pytest.mark.skipif(os.name != "nt", reason="private state requires Windows fixed volume")
@pytest.mark.parametrize("state_failure", [False, True])
def test_source_candidate_saved_only_after_publication_and_failure_is_pending(tmp_path, monkeypatch, state_failure):
    from sandbox.mind.step2 import prefix_private_state as ps
    from waggledance.core.bridge_log_reader import BridgeCursor, BridgePrefixPin
    mod = module()
    private, shared = tmp_path / "private", tmp_path / "shared"
    private.mkdir()
    shared.mkdir()
    old = BridgePrefixPin(BridgeCursor(0, "posix-v1:1:2", "synthetic"), "1" * 64)
    candidate = BridgePrefixPin(BridgeCursor(1, "posix-v1:1:2", "synthetic"), "2" * 64)
    with ps.locked_state(private, shared, enabled=True) as state:
        state.begin(None)
        state.commit(None, "a" * 64, old)
    source = SimpleNamespace(source="synthetic", cursor=old.cursor, anchor_bytes=b"x", generation_path="synthetic",
        max_bytes=10, max_rows=10, policy_bytes=b"p", roster_bytes=b"r")
    config = SimpleNamespace(source=source, public_policy_bytes=b"q")
    pins = SimpleNamespace(anchor_sha256="d" * 64, policy_sha256="e" * 64, roster_sha256="f" * 64,
                           public_policy_sha256="3" * 64)
    request = SimpleNamespace(root=shared, private_state_root=private, expected_pointer_sha256="a" * 64,
                              start_prefix=old, timeout=0.2, config=config, pins=pins, max_prefix_bytes=10)
    monkeypatch.setattr(mod, "_validate", lambda _: None)
    monkeypatch.setattr(mod, "_bind", lambda _: None)
    monkeypatch.setattr(mod.adapter, "strict_pins", lambda *a: (frozenset(), {}))
    monkeypatch.setattr(mod.adapter, "public_status_policy", lambda *a: None)
    monkeypatch.setattr(mod.checkpoint, "read_verified_prefix_pointer", lambda *a, **k: ({}, old))
    monkeypatch.setattr(mod.snapshot, "read_anchored_delta", lambda *a, **k: {
        "projection": {"records": []}, "candidate_prefix": candidate})
    monkeypatch.setattr(mod.snapshot, "verify_anchored_projection", lambda *a, **k: True)
    monkeypatch.setattr(mod.checkpoint, "advance_prefix_empty", lambda *a, **k: {
        "status": "advanced", "pointer_sha256": "b" * 64})
    if state_failure:
        monkeypatch.setattr(ps._LockedState, "commit", lambda *a: (_ for _ in ()).throw(OSError("failure")))
        with pytest.raises(mod.PrefixProducerRefused, match="^publication_state_unknown$") as exc:
            mod.publish_public_prefix(enabled=True, request=request)
        assert candidate.sha256 not in repr(exc.value)
        assert exc.value.__context__ is None and exc.value.__cause__ is None
        with ps.locked_state(private, shared, enabled=True) as state:
            with pytest.raises(ps.StateRefused):
                state.require("b" * 64, candidate)
    else:
        reply = mod.publish_public_prefix(enabled=True, request=request)
        assert reply["status"] == "EMPTY_ADVANCED" and candidate.sha256 not in repr(reply)
        with ps.locked_state(private, shared, enabled=True) as state:
            state.require("b" * 64, candidate)


@pytest.mark.skipif(os.name != "nt", reason="checkpoint requires Windows fixed volumes")
def test_synthetic_prefix_genesis_empty_history_cas_and_reader(tmp_path):
    pytest.importorskip("faiss", reason="synthetic builder needs FAISS")
    from sandbox.mind.step2 import test_prefix_checkpoint_v1 as pf
    from sandbox.mind.step2 import test_shared_index_builder as f
    from sandbox.mind.step2 import shared_producer as old, shared_public_producer as pub
    from tools import wd_q4_prefix_public_query as reader
    mod = module()
    root = tmp_path / "state"
    root.mkdir()
    private_root = tmp_path / "private-state"
    private_root.mkdir()
    read_pins = dict(policy_bytes=f.POLICY_BYTES, policy_sha256=f.POLICY_SHA,
                     roster_bytes=f.ROSTER_BYTES, roster_sha256=f.ROSTER_SHA,
                     public_policy_bytes=f.PUBLIC_POLICY_BYTES, public_policy_sha256=f.PUBLIC_POLICY_SHA,
                     enabled=True)

    def request(result, build_source, kw, prior, pointer, generation):
        anchor = json.loads(kw["expected_anchor_bytes"])
        source = old.SourceConfig(source=anchor["source_path"], generation_path=anchor["generation_path"],
            cursor=prior.cursor, anchor_bytes=kw["expected_anchor_bytes"], anchor_sha256=kw["expected_anchor_sha256"],
            policy_bytes=f.POLICY_BYTES, policy_sha256=f.POLICY_SHA, roster_bytes=f.ROSTER_BYTES,
            roster_sha256=f.ROSTER_SHA, max_bytes=1 << 20, max_rows=100)
        config = old.PublicSourceConfig(source=source, public_policy_bytes=f.PUBLIC_POLICY_BYTES,
                                        public_policy_sha256=f.PUBLIC_POLICY_SHA)
        pins = pub.CallerPins(policy_sha256=f.POLICY_SHA, roster_sha256=f.ROSTER_SHA,
                             public_policy_sha256=f.PUBLIC_POLICY_SHA, anchor_sha256=kw["expected_anchor_sha256"])
        return mod.PrefixPublicRequest(config=config, pins=pins, start_prefix=prior, max_prefix_bytes=1 << 20,
            root=root, generation=generation, expected_pointer_sha256=pointer, encode=f.FakeEncoder(),
            identity=f.IDENTITY, dim=f.DIM, private_state_root=private_root)

    first, src_a, kw_a, start = pf._delta(tmp_path / "log", f.PUBLIC_EVENTS)
    req_a = request(first, src_a, kw_a, start, None, "gen-a")
    with pytest.raises(mod.PrefixProducerRefused, match="^request_invalid$"):
        mod.publish_public_prefix(enabled=True, request=replace(req_a, start_prefix=None))
    a = mod.publish_public_prefix(enabled=True, request=req_a)
    assert a["status"] == "PUBLISHED_GENESIS" and start.sha256 not in json.dumps(a)
    out = reader.resolve_prefix_public_pointer(root, expected_pointer_sha256=a["pointer_sha256"], **read_pins)
    assert out["generation"] == "gen-a"
    from sandbox.mind.step2 import prefix_private_state as ps
    with ps.locked_state(private_root, root, enabled=True) as state:
        end_a = state.load(a["pointer_sha256"])
    assert end_a == first["candidate_prefix"]
    empty, src_empty, kw_empty, _ = pf._delta(tmp_path / "log", [f.event(agent="claude-rco-1")],
                                              end_a.cursor, end_a)
    req_empty = request(empty, src_empty, kw_empty, end_a, a["pointer_sha256"], "unused")
    with pytest.raises(mod.PrefixProducerRefused):
        mod.publish_public_prefix(enabled=True, request=replace(req_empty, start_prefix=replace(end_a, sha256="0" * 64)))
    b = mod.publish_public_prefix(enabled=True, request=req_empty)
    assert b["status"] == "EMPTY_ADVANCED" and end_a.sha256 not in json.dumps(b)
    with ps.locked_state(private_root, root, enabled=True) as state:
        next_start = state.load(b["pointer_sha256"])
    assert next_start == empty["candidate_prefix"]
    later, src_b, kw_b, _ = pf._delta(tmp_path / "log", f.LATER_PUBLIC_EVENTS, next_start.cursor, next_start)
    req_b = request(later, src_b, kw_b, next_start, b["pointer_sha256"], "gen-b")
    with pytest.raises(mod.PrefixProducerRefused):
        mod.publish_public_prefix(enabled=True, request=replace(req_b, expected_pointer_sha256=a["pointer_sha256"]))
    c = mod.publish_public_prefix(enabled=True, request=req_b)
    assert c["status"] == "PUBLISHED_HISTORY" and next_start.sha256 not in json.dumps(c)
    resolved = reader.resolve_prefix_public_pointer(root, expected_pointer_sha256=c["pointer_sha256"], **read_pins)
    assert resolved["generation"] == "gen-b" and later["candidate_prefix"].sha256 not in json.dumps(resolved)
    with pytest.raises(reader.PrefixPublicRefused):
        reader.resolve_prefix_public_pointer(root, expected_pointer_sha256=a["pointer_sha256"], **read_pins)
    # Test-owned synthetic pointer only: a legacy schema must never be read as
    # a prefix checkpoint even if the caller pins its exact bytes.
    from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
    pointer_path = root / checkpoint.POINTER
    legacy = json.loads(pointer_path.read_bytes())
    legacy["schema"] = checkpoint.HISTORY_SCHEMA
    del legacy["prefix_start_sha256"], legacy["prefix_watermark_sha256"]
    legacy_bytes = json.dumps(legacy, sort_keys=True, separators=(",", ":")).encode()
    pointer_path.write_bytes(legacy_bytes)
    with pytest.raises(reader.PrefixPublicRefused):
        reader.resolve_prefix_public_pointer(root, expected_pointer_sha256=hashlib.sha256(legacy_bytes).hexdigest(),
                                             **read_pins)
    with pytest.raises(mod.PrefixProducerRefused, match="^private_state_refused$"):
        mod.publish_public_prefix(enabled=True, request=replace(req_b,
            expected_pointer_sha256=hashlib.sha256(legacy_bytes).hexdigest()))


@pytest.mark.parametrize("field", ["policy_sha256", "roster_sha256", "public_policy_sha256", "anchor_sha256"])
def test_pin_shape_missing_never_calls_snapshot(monkeypatch, field):
    from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
    from sandbox.mind.step2 import shared_producer as old, shared_public_producer as pub
    from waggledance.core.bridge_log_reader import BridgeCursor, BridgePrefixPin
    mod = module()
    def forbidden(*args, **kwargs):
        pytest.fail("invalid input must not read snapshot")
    monkeypatch.setattr(snapshot, "read_anchored_delta", forbidden)
    data = b"synthetic"
    digest = hashlib.sha256(data).hexdigest()
    cursor = BridgeCursor(0, "synthetic-id", "synthetic-generation")
    source = old.SourceConfig(source=r"C:\synthetic\events", generation_path=r"C:\synthetic\generation",
        cursor=cursor, anchor_bytes=data, anchor_sha256=digest, policy_bytes=data, policy_sha256=digest,
        roster_bytes=data, roster_sha256=digest, max_bytes=100, max_rows=10)
    config = old.PublicSourceConfig(source=source, public_policy_bytes=data, public_policy_sha256=digest)
    pins = pub.CallerPins(policy_sha256=digest, roster_sha256=digest,
                          public_policy_sha256=digest, anchor_sha256=digest)
    request = mod.PrefixPublicRequest(config=config, pins=replace(pins, **{field: None}),
        start_prefix=BridgePrefixPin(cursor, digest), max_prefix_bytes=100, root=r"C:\synthetic",
        generation="gen-a", expected_pointer_sha256=None, encode=lambda x: x, identity={}, dim=3)
    with pytest.raises(mod.PrefixProducerRefused, match="^pins_mismatch$"):
        mod.publish_public_prefix(request=request, enabled=True)
