"""Synthetic multi-window public producer integration; no live bridge or model."""
from dataclasses import replace
import json
import os

import pytest


pytestmark = pytest.mark.skipif(os.name != "nt", reason="private checkpoints require a Windows fixed volume")


def _request(tmp_path, events):
    pytest.importorskip("faiss", reason="this integration exercises real FAISS")
    from sandbox.mind.step2 import shared_prefix_public_producer as producer
    from sandbox.mind.step2 import shared_producer as old, shared_public_producer as public
    from sandbox.mind.step2 import test_prefix_checkpoint_v1 as prefix_fixture
    from sandbox.mind.step2 import test_shared_index_builder as f

    root, private = tmp_path / "state", tmp_path / "private-state"
    root.mkdir()
    private.mkdir()
    _full_delta, _source, kwargs, start = prefix_fixture._delta(tmp_path / "log", events)
    anchor = json.loads(kwargs["expected_anchor_bytes"])
    source = old.SourceConfig(source=anchor["source_path"], generation_path=anchor["generation_path"],
        cursor=start.cursor, anchor_bytes=kwargs["expected_anchor_bytes"],
        anchor_sha256=kwargs["expected_anchor_sha256"], policy_bytes=f.POLICY_BYTES,
        policy_sha256=f.POLICY_SHA, roster_bytes=f.ROSTER_BYTES, roster_sha256=f.ROSTER_SHA,
        max_bytes=1 << 20, max_rows=1)
    config = old.PublicSourceConfig(source=source, public_policy_bytes=f.PUBLIC_POLICY_BYTES,
                                    public_policy_sha256=f.PUBLIC_POLICY_SHA)
    pins = public.CallerPins(policy_sha256=f.POLICY_SHA, roster_sha256=f.ROSTER_SHA,
        public_policy_sha256=f.PUBLIC_POLICY_SHA, anchor_sha256=kwargs["expected_anchor_sha256"])
    request = producer.PrefixPublicRequest(config=config, pins=pins, start_prefix=start,
        max_prefix_bytes=1 << 20, root=root, generation="gen-a", expected_pointer_sha256=None,
        encode=f.FakeEncoder(), identity=f.IDENTITY, dim=f.DIM, private_state_root=private)
    return request, f


def _catchup(request, windows):
    from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
    source, config, pins = request.config.source, request.config, request.pins
    from sandbox.mind.step2.test_shared_index_builder import APPROVED
    return snapshot.read_public_catchup(source.source, source.cursor, APPROVED,
        anchor_bytes=source.anchor_bytes, anchor_sha256=pins.anchor_sha256,
        generation_path=source.generation_path, max_bytes=source.max_bytes, max_rows=source.max_rows,
        max_windows=windows, policy_bytes=source.policy_bytes, policy_sha256=pins.policy_sha256,
        roster_bytes=source.roster_bytes, roster_sha256=pins.roster_sha256,
        public_policy_bytes=config.public_policy_bytes, public_policy_sha256=pins.public_policy_sha256,
        expected_prefix_sha256=request.start_prefix.sha256, max_prefix_bytes=request.max_prefix_bytes,
        enabled=True)


def _read_pointer(request, f, pointer_sha):
    from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
    return checkpoint.read_verified_prefix_pointer(request.root, f.APPROVED, expected_pointer_sha256=pointer_sha,
        policy_bytes=f.POLICY_BYTES, policy_sha256=f.POLICY_SHA, roster_bytes=f.ROSTER_BYTES,
        roster_sha256=f.ROSTER_SHA, expected_public_policy_sha256=f.PUBLIC_POLICY_SHA)


def test_multiwindow_genesis_publishes_deduplicated_projection_without_prefix_leak(tmp_path):
    from sandbox.mind.step2 import shared_prefix_public_producer as producer
    from sandbox.mind.step2 import prefix_private_state
    from sandbox.mind.step2.test_shared_index_builder import PUBLIC_EVENTS

    # An exact duplicate in a different window exercises cross-window deduplication.
    request, f = _request(tmp_path, PUBLIC_EVENTS + [PUBLIC_EVENTS[0]])
    observed = _catchup(request, 8)
    assert observed["caught_up"] is True and observed["window_count"] >= 2
    response = producer.publish_public_catchup(enabled=True, request=request, max_windows=8)
    assert response["status"] == "PUBLISHED_GENESIS"
    view, candidate = _read_pointer(request, f, response["pointer_sha256"])
    assert view["generation"] == "gen-a" and candidate == observed["candidate_prefix"]
    projection = json.loads((request.root / "gen-a" / "projection.json").read_bytes())
    assert len(projection["records"]) == len(PUBLIC_EVENTS)
    assert len({record["record_id"] for record in projection["records"]}) == len(PUBLIC_EVENTS)
    with prefix_private_state.locked_state(request.private_state_root, request.root, enabled=True) as state:
        assert state.load(response["pointer_sha256"]) == candidate
    encoded = json.dumps(response, sort_keys=True)
    assert request.start_prefix.sha256 not in encoded and candidate.sha256 not in encoded


def test_partial_chain_refuses_before_pointer_or_private_state_commit(tmp_path):
    from sandbox.mind.step2 import prefix_private_state, shared_index_checkpoint as checkpoint
    from sandbox.mind.step2 import shared_prefix_public_producer as producer
    from sandbox.mind.step2.test_shared_index_builder import PUBLIC_EVENTS

    request, _ = _request(tmp_path, PUBLIC_EVENTS)
    observed = _catchup(request, 1)
    assert observed["caught_up"] is False
    with pytest.raises(producer.PrefixProducerRefused):
        producer.publish_public_catchup(enabled=True, request=request, max_windows=1)
    assert not (request.root / checkpoint.POINTER).exists()
    with prefix_private_state.locked_state(request.private_state_root, request.root, enabled=True) as state:
        state.require(None, request.start_prefix)


def test_multiwindow_history_extends_prior_projection_without_duplicate(tmp_path):
    from sandbox.mind.step2 import shared_prefix_public_producer as producer
    from sandbox.mind.step2 import test_prefix_checkpoint_v1 as prefix_fixture
    from sandbox.mind.step2.test_shared_index_builder import PUBLIC_EVENTS, LATER_PUBLIC_EVENTS

    request, f = _request(tmp_path, PUBLIC_EVENTS)
    first = producer.publish_public_catchup(enabled=True, request=request, max_windows=8)
    _, end = _read_pointer(request, f, first["pointer_sha256"])
    _delta, _source, kwargs, start = prefix_fixture._delta(
        tmp_path / "log", LATER_PUBLIC_EVENTS + [PUBLIC_EVENTS[0]], end.cursor, end)
    updated_source = replace(request.config.source, cursor=start.cursor,
        anchor_bytes=kwargs["expected_anchor_bytes"], anchor_sha256=kwargs["expected_anchor_sha256"])
    next_request = replace(request, config=replace(request.config, source=updated_source),
        pins=replace(request.pins, anchor_sha256=kwargs["expected_anchor_sha256"]),
        start_prefix=start, expected_pointer_sha256=first["pointer_sha256"], generation="gen-b")
    observed = _catchup(next_request, 8)
    assert observed["caught_up"] is True and observed["window_count"] >= 2
    second = producer.publish_public_catchup(enabled=True, request=next_request, max_windows=8)
    assert second["status"] == "PUBLISHED_HISTORY"
    view, candidate = _read_pointer(next_request, f, second["pointer_sha256"])
    assert view["generation"] == "gen-b" and candidate == observed["candidate_prefix"]
    projection = json.loads((request.root / "gen-b" / "projection.json").read_bytes())
    # Two later public rows are admitted; a targeted/private row is not.
    assert len(projection["records"]) == 5
    assert len({record["record_id"] for record in projection["records"]}) == 5
    assert end.sha256 not in json.dumps(second) and candidate.sha256 not in json.dumps(second)


def test_disabled_does_not_inspect_request():
    from sandbox.mind.step2 import shared_prefix_public_producer as producer
    class Poison:
        def __getattribute__(self, name):
            raise AssertionError("OFF must not inspect request")
    assert producer.publish_public_catchup(request=Poison(), max_windows=1) == {"status": "OFF"}


@pytest.mark.parametrize("fault", ["partial", "upstream", "interrupt"])
def test_catchup_failures_clear_new_producer_frames_and_interrupt_propagates(tmp_path, monkeypatch, fault):
    import traceback
    from sandbox.mind.step2 import shared_bridge_snapshot as snapshot, shared_prefix_public_producer as producer
    from sandbox.mind.step2.test_shared_index_builder import PUBLIC_EVENTS

    request, _ = _request(tmp_path, PUBLIC_EVENTS)
    secret_values = (request.start_prefix.sha256, str(request.config.source.source), str(request.root))
    if fault != "partial":
        def failed_reader(*_args, **_kwargs):
            if fault == "interrupt":
                raise KeyboardInterrupt()
            raise ValueError("synthetic-backend-error")
        monkeypatch.setattr(snapshot, "read_public_catchup", failed_reader)
    expected = KeyboardInterrupt if fault == "interrupt" else producer.PrefixProducerRefused
    with pytest.raises(expected) as caught:
        producer.publish_public_catchup(enabled=True, request=request, max_windows=1 if fault == "partial" else 8)
    assert caught.value.__context__ is None
    frames = [frame for frame, _ in traceback.walk_tb(caught.value.__traceback__) if frame.f_code.co_name in
              ("publish_public_catchup", "_publish_locked_catchup")]
    assert frames
    for frame in frames:
        assert all(value is not request for value in frame.f_locals.values())
        assert all(secret not in repr(frame.f_locals) for secret in secret_values)
