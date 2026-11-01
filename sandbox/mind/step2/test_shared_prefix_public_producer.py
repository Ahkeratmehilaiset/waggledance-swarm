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
                              start_prefix=old, timeout=0.2, config=config, pins=pins, max_prefix_bytes=10,
                              fenced=False)
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


# --- opt-in fenced producer route (request.fenced=True) over the private fence; synthetic, real builder ------------

def _fenced_chain(tmp_path):
    """Real synthetic log + FAISS builder, as in test_synthetic_prefix_genesis_empty_history_cas_and_reader."""
    pytest.importorskip("faiss", reason="synthetic builder needs FAISS")
    from sandbox.mind.step2 import test_prefix_checkpoint_v1 as pf
    from sandbox.mind.step2 import test_shared_index_builder as f
    from sandbox.mind.step2 import shared_producer as old, shared_public_producer as pub
    mod = module()
    root, private_root = tmp_path / "state", tmp_path / "private-state"
    root.mkdir()
    private_root.mkdir()

    def request(kw, prior, pointer, generation, *, fenced=True, private=None):
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
            identity=f.IDENTITY, dim=f.DIM, private_state_root=private or private_root, fenced=fenced)

    def delta(events, start=None, prior=None):
        return pf._delta(tmp_path / "log", events, start, prior)

    return SimpleNamespace(mod=mod, root=root, private=private_root, request=request, delta=delta, f=f)


def _fence_files(root):
    return sorted(p.name for p in root.iterdir() if p.name.startswith("q4-prefix-fence"))


def _private_state(chain):
    from sandbox.mind.step2 import prefix_private_state as ps
    return ps.locked_state(chain.private, chain.root, enabled=True)


def _sha_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_fenced_flag_is_a_strict_default_off_bool():
    mod = module()
    assert mod.PrefixPublicRequest.__dataclass_fields__["fenced"].default is False
    request = SimpleNamespace()
    with pytest.raises(mod.PrefixProducerRefused, match="^request_invalid$"):
        mod.publish_public_prefix(enabled=True, request=request)


@pytest.mark.skipif(os.name != "nt", reason="checkpoint requires Windows fixed volumes")
@pytest.mark.parametrize("flag", [1, "yes", None])
def test_non_bool_fenced_flag_refuses_before_any_read(tmp_path, monkeypatch, flag):
    chain = _fenced_chain(tmp_path)
    first, _, kw, start = chain.delta(chain.f.PUBLIC_EVENTS)
    monkeypatch.setattr(chain.mod.snapshot, "read_anchored_delta", lambda *a, **k: pytest.fail("must not read"))
    with pytest.raises(chain.mod.PrefixProducerRefused, match="^request_invalid$"):
        chain.mod.publish_public_prefix(enabled=True, request=replace(chain.request(kw, start, None, "gen-a"),
                                                                      fenced=flag))


@pytest.mark.skipif(os.name != "nt", reason="checkpoint requires Windows fixed volumes")
def test_default_route_opens_no_fence_and_keeps_the_legacy_marker_protocol(tmp_path):
    chain = _fenced_chain(tmp_path)
    first, _, kw, start = chain.delta(chain.f.PUBLIC_EVENTS)
    a = chain.mod.publish_public_prefix(enabled=True, request=chain.request(kw, start, None, "gen-a", fenced=False))
    assert a["status"] == "PUBLISHED_GENESIS" and _fence_files(chain.root) == []
    with _private_state(chain) as state:
        assert state.load(a["pointer_sha256"]) == first["candidate_prefix"]


@pytest.mark.skipif(os.name != "nt", reason="checkpoint requires Windows fixed volumes")
def test_fenced_genesis_empty_and_history_land_through_the_fence(tmp_path):
    chain = _fenced_chain(tmp_path)
    mod, root = chain.mod, chain.root
    first, _, kw_a, start = chain.delta(chain.f.PUBLIC_EVENTS)
    a = mod.publish_public_prefix(enabled=True, request=chain.request(kw_a, start, None, "gen-a"))
    assert a["status"] == "PUBLISHED_GENESIS" and a["pointer_sha256"] == _sha_file(root / "q4-latest.json")
    assert _fence_files(root) == ["q4-prefix-fence-" + a["pointer_sha256"] + ".landed.json"]
    with _private_state(chain) as state:
        end_a = state.load(a["pointer_sha256"])
    assert end_a == first["candidate_prefix"]
    empty, _, kw_e, _ = chain.delta([chain.f.event(agent="claude-rco-1")], end_a.cursor, end_a)
    b = mod.publish_public_prefix(enabled=True, request=chain.request(kw_e, end_a, a["pointer_sha256"], "unused"))
    assert b["status"] == "EMPTY_ADVANCED" and b["pointer_sha256"] == _sha_file(root / "q4-latest.json")
    with _private_state(chain) as state:
        end_b = state.load(b["pointer_sha256"])
    assert end_b == empty["candidate_prefix"]
    later, _, kw_b, _ = chain.delta(chain.f.LATER_PUBLIC_EVENTS, end_b.cursor, end_b)
    c = mod.publish_public_prefix(enabled=True, request=chain.request(kw_b, end_b, b["pointer_sha256"], "gen-b"))
    assert c["status"] == "PUBLISHED_HISTORY" and c["pointer_sha256"] == _sha_file(root / "q4-latest.json")
    with _private_state(chain) as state:
        assert state.load(c["pointer_sha256"]) == later["candidate_prefix"]
    assert "q4-prefix-fence.json" not in _fence_files(root)
    assert len([n for n in _fence_files(root) if n.endswith(".landed.json")]) == 3
    for report in (a, b, c):
        assert start.sha256 not in json.dumps(report) and end_a.sha256 not in json.dumps(report)


@pytest.mark.skipif(os.name != "nt", reason="checkpoint requires Windows fixed volumes")
def test_fenced_empty_no_change_opens_nothing(tmp_path):
    chain = _fenced_chain(tmp_path)
    mod, root = chain.mod, chain.root
    first, _, kw_a, start = chain.delta(chain.f.PUBLIC_EVENTS)
    a = mod.publish_public_prefix(enabled=True, request=chain.request(kw_a, start, None, "gen-a"))
    end_a = first["candidate_prefix"]
    _, _, kw_n, _ = chain.delta([], end_a.cursor, end_a)
    before = _fence_files(root)
    n = mod.publish_public_prefix(enabled=True, request=chain.request(kw_n, end_a, a["pointer_sha256"], "unused"))
    assert n["status"] == "EMPTY_NO_CHANGE" and n["pointer_sha256"] == a["pointer_sha256"]
    assert _fence_files(root) == before
    with _private_state(chain) as state:
        assert state.load(a["pointer_sha256"]) == end_a


@pytest.mark.skipif(os.name != "nt", reason="checkpoint requires Windows fixed volumes")
def test_a_refused_fenced_commit_reports_unknown_and_keeps_the_intent(tmp_path, monkeypatch):
    from sandbox.mind.step2 import prefix_private_state as ps
    chain = _fenced_chain(tmp_path)
    mod, root = chain.mod, chain.root
    first, _, kw_a, start = chain.delta(chain.f.PUBLIC_EVENTS)
    request = chain.request(kw_a, start, None, "gen-a")

    def refused(*args, **kwargs):
        raise mod.checkpoint.CheckpointRefused("checkpoint_refused")
    with monkeypatch.context() as patch:
        patch.setattr(mod.checkpoint, "commit_prefix_genesis", refused)
        with pytest.raises(mod.PrefixProducerRefused, match="^publication_state_unknown$") as error:
            mod.publish_public_prefix(enabled=True, request=request)
    assert error.value.__context__ is None and error.value.__cause__ is None
    assert not (root / "q4-latest.json").exists()
    records = _fence_files(root)
    assert len(records) == 1 and records[0].endswith(".not_landed.json")
    assert (chain.private / ps._PENDING).exists()  # intent kept, never closed or retried
    with pytest.raises(mod.PrefixProducerRefused, match="^private_state_refused$"):
        mod.publish_public_prefix(enabled=True, request=request)  # the open intent blocks the next publication
    # A contradictory extra record is refused, never resolved.
    (root / records[0].replace(".not_landed.", ".landed.")).write_bytes((root / records[0]).read_bytes())
    with _private_state(chain) as state:
        with pytest.raises(ps.StateRefused):
            state.resolve_intent(enabled=True)
    assert (chain.private / ps._PENDING).exists()


@pytest.mark.skipif(os.name != "nt", reason="checkpoint requires Windows fixed volumes")
def test_an_interruption_after_the_commit_resolves_landed_later(tmp_path, monkeypatch):
    from sandbox.mind.step2 import prefix_private_state as ps
    chain = _fenced_chain(tmp_path)
    mod, root = chain.mod, chain.root
    first, _, kw_a, start = chain.delta(chain.f.PUBLIC_EVENTS)

    def interrupted(*args, **kwargs):
        raise OSError("interrupted before resolution")
    with monkeypatch.context() as patch:
        patch.setattr(ps._LockedState, "resolve_intent", interrupted)
        with pytest.raises(mod.PrefixProducerRefused, match="^publication_state_unknown$"):
            mod.publish_public_prefix(enabled=True, request=chain.request(kw_a, start, None, "gen-a"))
    landed = _sha_file(root / "q4-latest.json")
    assert (root / "q4-prefix-fence.json").exists() and (chain.private / ps._PENDING).exists()
    with _private_state(chain) as state:
        assert state.resolve_intent(enabled=True) == "landed"
    with _private_state(chain) as state:
        assert state.load(landed) == first["candidate_prefix"]


@pytest.mark.skipif(os.name != "nt", reason="checkpoint requires Windows fixed volumes")
def test_an_old_writer_landing_after_not_landed_is_refused_with_the_intent_kept(tmp_path, monkeypatch):
    from sandbox.mind.step2 import prefix_private_state as ps
    chain = _fenced_chain(tmp_path)
    mod, root = chain.mod, chain.root
    first, _, kw_a, start = chain.delta(chain.f.PUBLIC_EVENTS)
    captured = []

    def refused(*args, **kwargs):
        captured.append((args, kwargs))
        raise mod.checkpoint.CheckpointRefused("checkpoint_refused")
    with monkeypatch.context() as patch:
        patch.setattr(mod.checkpoint, "commit_prefix_genesis", refused)
        with pytest.raises(mod.PrefixProducerRefused, match="^publication_state_unknown$"):
            mod.publish_public_prefix(enabled=True, request=chain.request(kw_a, start, None, "gen-a"))
    args, kwargs = captured[0]
    assert kwargs["fenced"] is True and kwargs["expected_attempt_sha256"]
    kwargs = {key: value for key, value in kwargs.items() if key != "fenced"}
    with monkeypatch.context() as old_process:  # an already-running OLD writer ignores the gate
        old_process.setattr(mod.checkpoint, "_fence_gate", lambda *a: None)
        assert mod.checkpoint.commit_prefix_genesis(*args, **kwargs)["pointer_sha256"] == kwargs["expected_attempt_sha256"]
    kept = (chain.private / ps._PENDING).read_bytes()
    with _private_state(chain) as state:
        with pytest.raises(ps.StateRefused):
            state.resolve_intent(enabled=True)  # contradicts the not_landed record: unresolved, never landed
    assert (chain.private / ps._PENDING).read_bytes() == kept


@pytest.mark.skipif(os.name != "nt", reason="checkpoint requires Windows fixed volumes")
def test_a_legacy_writer_is_fenced_out_while_a_fenced_publication_is_open(tmp_path, monkeypatch):
    from sandbox.mind.step2 import prefix_private_state as ps
    chain = _fenced_chain(tmp_path)
    mod, root = chain.mod, chain.root
    first, _, kw_a, start = chain.delta(chain.f.PUBLIC_EVENTS)

    def interrupted(*args, **kwargs):
        raise OSError("interrupted")
    with monkeypatch.context() as patch:  # crash after the intent, before the commit and the resolution
        patch.setattr(mod.checkpoint, "commit_prefix_genesis", interrupted)
        patch.setattr(ps._LockedState, "resolve_intent", interrupted)
        with pytest.raises(mod.PrefixProducerRefused, match="^publication_state_unknown$"):
            mod.publish_public_prefix(enabled=True, request=chain.request(kw_a, start, None, "gen-a"))
    assert (root / "q4-prefix-fence.json").exists()
    other = tmp_path / "other-private"
    other.mkdir()
    with pytest.raises(mod.PrefixProducerRefused, match="^publication_state_unknown$"):
        mod.publish_public_prefix(enabled=True, request=chain.request(kw_a, start, None, "gen-b", fenced=False,
                                                                      private=other))
    assert not (root / "q4-latest.json").exists()
    with _private_state(chain) as state:
        assert state.resolve_intent(enabled=True) == "not_landed"
    assert (chain.private / ps._PENDING).exists()


@pytest.mark.skipif(os.name != "nt", reason="checkpoint requires Windows fixed volumes")
def test_fenced_multiwindow_catchup_genesis_and_history_land_through_the_fence(tmp_path):
    chain = _fenced_chain(tmp_path)
    mod, root = chain.mod, chain.root

    def windowed(request):  # one row per window: the catch-up source must reach the fenced commit intact
        return replace(request, config=replace(request.config, source=replace(request.config.source, max_rows=1)))
    first, _, kw_a, start = chain.delta(chain.f.PUBLIC_EVENTS)
    a = mod.publish_public_catchup(enabled=True, request=windowed(chain.request(kw_a, start, None, "gen-a")),
                                   max_windows=8)
    assert a["status"] == "PUBLISHED_GENESIS" and a["pointer_sha256"] == _sha_file(root / "q4-latest.json")
    assert _fence_files(root) == ["q4-prefix-fence-" + a["pointer_sha256"] + ".landed.json"]
    with _private_state(chain) as state:
        end_a = state.load(a["pointer_sha256"])
    assert end_a == first["candidate_prefix"]
    later, _, kw_b, _ = chain.delta(chain.f.LATER_PUBLIC_EVENTS, end_a.cursor, end_a)
    b = mod.publish_public_catchup(enabled=True, request=windowed(chain.request(kw_b, end_a, a["pointer_sha256"], "gen-b")),
                                   max_windows=8)
    assert b["status"] == "PUBLISHED_HISTORY" and b["pointer_sha256"] == _sha_file(root / "q4-latest.json")
    with _private_state(chain) as state:
        assert state.load(b["pointer_sha256"]) == later["candidate_prefix"]
    assert len([n for n in _fence_files(root) if n.endswith(".landed.json")]) == 2
    assert "q4-prefix-fence.json" not in _fence_files(root)
    for report in (a, b):
        assert start.sha256 not in json.dumps(report) and end_a.sha256 not in json.dumps(report)
