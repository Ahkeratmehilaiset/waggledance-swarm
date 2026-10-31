"""Synthetic, default-off strict-prefix read seam; never calls a model or live bridge."""
import copy
import hashlib
import importlib
import io
import json
import os
from pathlib import Path

import pytest

from waggledance.core.bridge_log_reader import BridgeCursor, BridgePrefixPin


def sha(data):
    return hashlib.sha256(data).hexdigest()


def enc(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


@pytest.fixture
def seam(monkeypatch, tmp_path):
    mod = importlib.import_module("tools.wd_q4_prefix_public_query")
    root = tmp_path / "root"
    generation = root / "gen-a"
    generation.mkdir(parents=True)
    encoder = {"model": "synthetic", "revision": "a" * 40, "weights_sha256": "c" * 64}
    memory = b"synthetic memory"
    projection = enc({"records": [{"meta": {"agent": "fable-5", "task_id": "public-task", "type": "status"}}]})
    descriptor = enc({"schema": "wd.bridge-mind-q4-memory-descriptor.v2", "scope": "wd.q4-shared-projected-events.v1",
                      "memory_sha256": sha(memory), "dim": 3, "encoder": encoder})
    (generation / "memory.json").write_bytes(memory)
    (generation / "projection.json").write_bytes(projection)
    (generation / "descriptor.json").write_bytes(descriptor)
    pointer = root / mod.checkpoint.POINTER
    pointer.write_bytes(b"synthetic prefix pointer")
    artifacts = {"memory_sha256": sha(memory), "projection_sha256": sha(projection),
                 "descriptor_sha256": sha(descriptor), "receipt_sha256": "a" * 64}
    pin = BridgePrefixPin(BridgeCursor(0, "posix-v1:1:2", "synthetic-generation"), "e" * 64)
    view = {"pointer_sha256": sha(pointer.read_bytes()), "generation": "gen-a", "artifacts": artifacts,
            "anchor_sha256": "b" * 64, "cursor_end_sha256": "c" * 64,
            "watermark_sha256": sha(mod.snapshot._token(pin.cursor).encode())}
    args = dict(root=root, expected_pointer_sha256=view["pointer_sha256"], expected_source_sha256=view["anchor_sha256"],
                expected_encoder_sha256=sha(enc(encoder)), policy_bytes=b"synthetic-policy", policy_sha256="1" * 64,
                roster_bytes=b"synthetic-roster", roster_sha256="2" * 64,
                public_policy_bytes=b"synthetic-public-policy", public_policy_sha256="3" * 64, enabled=True)
    config = object()
    state = dict(mod=mod, args=args, config=config, view=view, pin=pin, pointer=pointer, calls=[])
    def strict(root, tasks, **kwargs):
        state["calls"].append((root, tasks, kwargs))
        if (kwargs["expected_pointer_sha256"] != state["view"]["pointer_sha256"] or
                kwargs["policy_sha256"] != "1" * 64 or kwargs["roster_sha256"] != "2" * 64 or
                kwargs["expected_public_policy_sha256"] != "3" * 64):
            raise ValueError("mismatch")
        return copy.deepcopy(state["view"]), state["pin"]
    def forbidden(*args, **kwargs):
        pytest.fail("strict route must never use the legacy verifier")
    def read(path, expected, *, limit):
        raw = Path(path).read_bytes()
        if len(raw) > limit or sha(raw) != expected:
            raise ValueError("mismatch")
        return raw
    monkeypatch.setattr(mod.checkpoint, "read_verified_prefix_pointer", strict)
    monkeypatch.setattr(mod.checkpoint, "read_verified_pointer", forbidden)
    monkeypatch.setattr(mod.checkpoint, "_secure", lambda path: Path(path))
    monkeypatch.setattr(mod.resolver, "_read_matching", read)
    monkeypatch.setattr(mod.adapter, "strict_pins", lambda *a: (frozenset({"public-task"}), {}))
    monkeypatch.setattr(mod.adapter, "public_status_policy", lambda *a: (frozenset({"public-task"}), frozenset({"status"})))
    return state


def query(state, **changes):
    return state["mod"].query_public_prefix(state["config"], io.BytesIO(b"synthetic"), **dict(state["args"], **changes))


def resolve(state, **changes):
    args = dict(state["args"], **changes)
    del args["expected_source_sha256"], args["expected_encoder_sha256"]
    return state["mod"].resolve_prefix_public_pointer(**args)


def test_default_off_refuses_without_read(seam):
    with pytest.raises(seam["mod"].PrefixPublicRefused, match="^disabled$"):
        query(seam, enabled=False)
    assert not seam["calls"]


def test_enabled_query_refuses_before_stdin_or_old_launcher(seam, monkeypatch):
    from tools import wd_q4_agent_query as old
    class NoRead:
        def read(self, *args):
            pytest.fail("disabled transport must not consume stdin")
    def forbidden(*args, **kwargs):
        pytest.fail("disabled transport must not invoke legacy runtime")
    monkeypatch.setattr(old, "query_and_resolve", forbidden)
    monkeypatch.setattr(old, "_invoke_launcher", forbidden)
    with pytest.raises(seam["mod"].PrefixPublicRefused, match="^secure_transport_unavailable$"):
        seam["mod"].query_public_prefix(seam["config"], NoRead(), **seam["args"])
    assert not seam["calls"]


def test_prefix_resolver_returns_no_private_prefix(seam):
    out = resolve(seam)
    assert out["freshness"] == "UNKNOWN"
    assert out["publisher_authentication"] == "NOT_AUTHENTICATED"
    assert out["source_filter_attestation"] == "NOT_VERIFIED"
    assert seam["pin"].sha256 not in json.dumps(out)
    assert len(seam["calls"]) == 1


@pytest.mark.parametrize("field", ["expected_pointer_sha256", "policy_sha256", "roster_sha256", "public_policy_sha256"])
@pytest.mark.parametrize("bad", [None, "0" * 64])
def test_missing_or_wrong_independent_caller_pin_refuses(seam, field, bad):
    with pytest.raises(seam["mod"].PrefixPublicRefused):
        resolve(seam, **{field: bad})


def test_legacy_pointer_and_missing_prefix_refuse_without_legacy_fallback(seam, monkeypatch):
    def refused(*args, **kwargs):
        raise ValueError("legacy or missing prefix")
    monkeypatch.setattr(seam["mod"].checkpoint, "read_verified_prefix_pointer", refused)
    with pytest.raises(seam["mod"].PrefixPublicRefused):
        resolve(seam)


def test_pointer_replacement_during_resolution_refuses(seam, monkeypatch):
    original = seam["mod"].resolver._read_matching
    def replacement(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        if Path(path).name == "projection.json":
            seam["pointer"].write_bytes(b"changed")
        return value
    monkeypatch.setattr(seam["mod"].resolver, "_read_matching", replacement)
    with pytest.raises(seam["mod"].PrefixPublicRefused):
        resolve(seam)


def test_rco_projected_record_refuses(seam):
    path = seam["args"]["root"] / "gen-a" / "projection.json"
    path.write_bytes(enc({"records": [{"meta": {"agent": "claude-rco-1", "task_id": "public-task", "type": "status"}}]}))
    seam["view"]["artifacts"]["projection_sha256"] = sha(path.read_bytes())
    with pytest.raises(seam["mod"].PrefixPublicRefused):
        resolve(seam)


@pytest.mark.skipif(os.name != "nt", reason="checkpoint requires Windows fixed volumes")
def test_real_synthetic_prefix_genesis_and_history_resolve_without_legacy_fallback(tmp_path, monkeypatch):
    pytest.importorskip("faiss", reason="synthetic builder requires FAISS")
    from tools import wd_q4_prefix_public_query as mod
    from sandbox.mind.step2 import test_prefix_checkpoint_v1 as prefix_fixtures
    from sandbox.mind.step2 import test_shared_index_builder as fixtures

    root = tmp_path / "memory"
    root.mkdir()
    first, source_a, kw_a, start = prefix_fixtures._delta(tmp_path / "log", fixtures.PUBLIC_EVENTS)
    a = fixtures.build(root, name="gen-a", proj=first["projection"], approved_tasks=None, roster=None,
                       source=source_a, allow_unbound_source=False)
    first_receipt = mod.checkpoint.commit_prefix_genesis(root, "gen-a", start.cursor, first["candidate_cursor"],
        fixtures.APPROVED, expected_pointer_sha256=None, expected_artifacts=fixtures._artifacts_of(a),
        initial_cursor=start.cursor, snapshot_result=first, expected_start_prefix=start, **kw_a)
    pins = dict(policy_bytes=fixtures.POLICY_BYTES, policy_sha256=fixtures.POLICY_SHA,
                roster_bytes=fixtures.ROSTER_BYTES, roster_sha256=fixtures.ROSTER_SHA,
                public_policy_bytes=fixtures.PUBLIC_POLICY_BYTES, public_policy_sha256=fixtures.PUBLIC_POLICY_SHA,
                enabled=True)
    def forbidden(*args, **kwargs):
        pytest.fail("legacy verifier must never be called")
    monkeypatch.setattr(mod.checkpoint, "read_verified_pointer", forbidden)
    first_binding = mod.resolve_prefix_public_pointer(root, expected_pointer_sha256=first_receipt["pointer_sha256"], **pins)
    assert first_binding["generation"] == "gen-a" and first["candidate_prefix"].sha256 not in json.dumps(first_binding)
    with pytest.raises(mod.PrefixPublicRefused):
        mod.resolve_prefix_public_pointer(root, expected_pointer_sha256="0" * 64, **pins)
    prior = first["candidate_prefix"]
    second, source_b, kw_b, _ = prefix_fixtures._delta(tmp_path / "log", fixtures.LATER_PUBLIC_EVENTS,
                                                        prior.cursor, prior)
    b = fixtures.build(root, name="gen-b", proj=second["projection"], approved_tasks=None, roster=None,
        source=source_b, allow_unbound_source=False,
        predecessor=dict(fixtures.predecessor_of(root, "gen-a", a),
                         watermark={"root": str(root), "pointer_sha256": first_receipt["pointer_sha256"]}))
    next_receipt = mod.checkpoint.commit_prefix_history(root, "gen-b", prior.cursor, second["candidate_cursor"],
        fixtures.APPROVED, snapshot_result=second, expected_pointer_sha256=first_receipt["pointer_sha256"],
        expected_artifacts=fixtures._artifacts_of(b), expected_start_prefix=prior, **kw_b)
    next_binding = mod.resolve_prefix_public_pointer(root, expected_pointer_sha256=next_receipt["pointer_sha256"], **pins)
    assert next_binding["generation"] == "gen-b" and prior.sha256 not in json.dumps(next_binding)
    with pytest.raises(mod.PrefixPublicRefused):
        mod.resolve_prefix_public_pointer(root, expected_pointer_sha256=first_receipt["pointer_sha256"], **pins)
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    legacy, anchor_hash, anchor_bytes, cursor = fixtures._real_public_snapshot(
        mod.snapshot, tmp_path / "legacy-log", fixtures.PUBLIC_EVENTS)
    old = fixtures.build(legacy_root, name="gen-old", proj=legacy["projection"], approved_tasks=None, roster=None,
                         source=fixtures._public_source(legacy, anchor_hash, anchor_bytes), allow_unbound_source=False)
    mod.checkpoint.commit_generation(legacy_root, "gen-old", cursor, legacy["candidate_cursor"], fixtures.APPROVED,
        expected_pointer_sha256=None, expected_artifacts=fixtures._artifacts_of(old), initial_cursor=cursor,
        snapshot_result=legacy, policy_bytes=fixtures.POLICY_BYTES, expected_policy_sha256=fixtures.POLICY_SHA,
        roster_bytes=fixtures.ROSTER_BYTES, expected_roster_sha256=fixtures.ROSTER_SHA,
        expected_anchor_sha256=anchor_hash, expected_anchor_bytes=anchor_bytes, **fixtures.PUBLIC_CHECKPOINT_KWARGS)
    legacy_pointer = sha((legacy_root / mod.checkpoint.POINTER).read_bytes())
    with pytest.raises(mod.PrefixPublicRefused):
        mod.resolve_prefix_public_pointer(legacy_root, expected_pointer_sha256=legacy_pointer, **pins)
