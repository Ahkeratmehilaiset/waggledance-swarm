"""Synthetic fixed-peer READY protocol; never sends a real agent query."""
import json
import traceback

import pytest

from tools import wd_q4_secure_transport as transport


def _request():
    return {"schema": "wd.q4-fixed-control.v1", "nonce": "a" * 32,
            "binding_sha256": "b" * 64}


def _ready(**changes):
    value = {"schema": "wd.q4-fixed-ready.v1", "status": "READY",
             "nonce": "a" * 32, "binding_sha256": "b" * 64}
    value.update(changes)
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def test_ready_primitive_documents_echo_order_not_authentication():
    assert "echo/order only, not authentication" in transport._fixed_ready_exchange.__doc__


def test_matching_echo_from_unauthenticated_peer_is_not_runtime_authentication(monkeypatch):
    events = []
    # Simulate a peer-identity check that is falsely treated as successful;
    # the sender controls and can simply mirror both nonce and digest.
    monkeypatch.setattr(transport, "_peer", lambda *a, **kw: None)
    monkeypatch.setattr(transport, "_frame_write",
                        lambda _k, _p, data, _d: events.append(data))
    monkeypatch.setattr(transport, "_frame_read", lambda *a, **kw: _ready())
    query = b"synthetic-query-only"
    transport._fixed_ready_exchange(None, None, 123, 456, _request(), query, 1)
    assert events[-1] == query


@pytest.mark.parametrize("fault", ["pid", "creation", "nonce", "digest", "schema", "status", "extra", "timeout", "duplicate", "malformed"])
def test_mock_peer_or_echo_refusal_never_receives_query(monkeypatch, fault):
    assert callable(getattr(transport, "_fixed_ready_exchange", None))
    events = []
    query = b"synthetic-query-only"
    def peer(_kernel, _pipe, pid, created, *, client):
        events.append(("peer", pid, created, client))
        if fault in ("pid", "creation"):
            raise ValueError("synthetic-peer-refused")
    def write(_kernel, _pipe, data, _deadline):
        events.append(("write", data))
    def read(_kernel, _pipe, _limit, _deadline):
        events.append(("read",))
        if fault == "timeout":
            raise TimeoutError("synthetic")
        if fault == "duplicate":
            return b'{"schema":"wd.q4-fixed-ready.v1","status":"READY","nonce":"a","nonce":"b"}'
        if fault == "malformed":
            return b'{"schema":'
        changes = {"nonce": {"nonce": "c" * 32}, "digest": {"binding_sha256": "d" * 64},
                   "schema": {"schema": "wrong"}, "status": {"status": "REFUSED"},
                   "extra": {"extra": 1}}
        return _ready(**changes.get(fault, {}))
    monkeypatch.setattr(transport, "_peer", peer)
    monkeypatch.setattr(transport, "_frame_write", write)
    monkeypatch.setattr(transport, "_frame_read", read)
    with pytest.raises(transport._Refused):
        transport._fixed_ready_exchange(None, None, 123, 456, _request(), query, 1)
    assert ("write", query) not in events


def test_query_only_after_mock_peer_and_matching_ready_echo(monkeypatch):
    events = []
    monkeypatch.setattr(transport, "_peer", lambda _k, _p, pid, created, *, client:
                        events.append(("peer", pid, created, client)))
    monkeypatch.setattr(transport, "_frame_write", lambda _k, _p, data, _d:
                        events.append(("write", data)))
    monkeypatch.setattr(transport, "_frame_read", lambda _k, _p, _l, _d:
                        (events.append(("read",)), _ready())[1])
    query = b"synthetic-query-only"
    transport._fixed_ready_exchange(None, None, 123, 456, _request(), query, 1)
    assert events[0] == ("peer", 123, 456, True)
    assert events[1][0] == "write" and events[1][1] != query
    assert events[2] == ("read",) and events[3] == ("write", query)


def test_real_fixed_peer_is_fail_closed_without_authorized_runtime():
    from tools import wd_q4_fixed_peer as peer
    with pytest.raises(peer.FixedPeerRefused):
        peer.prepare_fixed_q4(_request(), trusted_binding=None, enabled=True)


def test_peer_default_off_before_inspecting_inputs():
    from tools import wd_q4_fixed_peer as peer
    with pytest.raises(peer.FixedPeerRefused):
        peer.prepare_fixed_q4(object(), trusted_binding=object())


def test_fixed_q4_run_refuses_before_process_and_query(monkeypatch, tmp_path):
    calls = []

    def api():
        calls.append("process_setup")
        raise RuntimeError("unexpected process setup")

    monkeypatch.setattr(transport, "_api", api)
    monkeypatch.setattr(transport, "_frame_write",
                        lambda *args, **kwargs: calls.append("query_write"))
    with pytest.raises(transport._Refused):
        transport._run(["untrusted-executable"], str(tmp_path),
                       {"mode": "fixed-q4-v1", "nonce": "a" * 32,
                        "binding_sha256": "b" * 64},
                       b"synthetic-query", 5, 1024)
    assert calls == []


def _locals_for(exc, function):
    return next(frame.f_locals for frame, _ in traceback.walk_tb(exc.__traceback__)
                if frame.f_code.co_name == function)


def test_fixed_run_refusal_drops_direct_input_locals(tmp_path):
    query = b"fixed-run-secret-sentinel"
    metadata = {"mode": "fixed-q4-v1", "marker": object()}
    with pytest.raises(transport._Refused) as error:
        transport._run(["untrusted"], str(tmp_path), metadata, query, 5, 1024)
    frame = _locals_for(error.value, "_run")
    assert all(value is not query and value is not metadata
               for value in frame.values())
    assert "stdin_bytes" not in frame and "metadata" not in frame


@pytest.mark.parametrize("query,timeout,limit", [
    (object(), 5, 1024),
    (b"x" * (transport._MAX_INPUT + 1), 5, 1024),
    (b"fixed-input-sentinel", 0, 1024),
    (b"fixed-input-sentinel", 5, 0),
], ids=["wrong-type", "oversize", "timeout", "output-limit"])
def test_fixed_mode_precedes_validation_without_input_locals(tmp_path, query, timeout, limit):
    metadata = {"mode": "fixed-q4-v1", "marker": object()}
    with pytest.raises(transport._Refused) as error:
        transport._run(["untrusted"], str(tmp_path), metadata, query, timeout, limit)
    frames = list(traceback.walk_tb(error.value.__traceback__))
    assert all(frame.f_code.co_name != "_validate" for frame, _ in frames)
    own = _locals_for(error.value, "_run")
    assert "stdin_bytes" not in own and "metadata" not in own
    assert all(value is not query and value is not metadata for value in own.values())


def test_bad_metadata_refuses_without_mapping_code_or_input_locals(tmp_path):
    calls = []
    class HostileMapping:
        def keys(self):
            calls.append("keys")
            raise RuntimeError("must not run")
    metadata = HostileMapping()
    query = b"bad-metadata-input-sentinel"
    with pytest.raises(transport._Refused) as error:
        transport._run(["untrusted"], str(tmp_path), metadata, query, 0, 0)
    own = _locals_for(error.value, "_run")
    assert calls == []
    assert "stdin_bytes" not in own and "metadata" not in own
    assert all(value is not query and value is not metadata for value in own.values())


def test_ready_refusal_drops_direct_payload_locals(monkeypatch):
    query = b"ready-query-secret-sentinel"
    control = _request()
    monkeypatch.setattr(transport, "_peer", lambda *a, **kw: None)
    monkeypatch.setattr(transport, "_frame_write", lambda *a, **kw: None)
    monkeypatch.setattr(transport, "_frame_read", lambda *a, **kw: _ready(status="NO"))
    with pytest.raises(transport._Refused) as error:
        transport._fixed_ready_exchange(None, None, 123, 456, control, query, 1)
    frame = _locals_for(error.value, "_fixed_ready_exchange")
    assert all(value is not query and value is not control for value in frame.values())
    assert not {"control", "query", "raw", "ready", "ready_raw"} & set(frame)


def test_fixed_peer_refusal_drops_direct_payload_locals():
    from tools import wd_q4_fixed_peer as peer
    control, binding, enabled = object(), object(), object()
    with pytest.raises(peer.FixedPeerRefused) as error:
        peer.prepare_fixed_q4(control, trusted_binding=binding, enabled=enabled)
    frame = _locals_for(error.value, "prepare_fixed_q4")
    assert all(value is not control and value is not binding and value is not enabled
               for value in frame.values())
    assert not {"control", "trusted_binding", "enabled"} & set(frame)


@pytest.mark.parametrize("change", ["missing", "extra", "bad_nonce", "bad_binding"])
def test_bad_control_refuses_before_any_peer_io(monkeypatch, change):
    assert callable(getattr(transport, "_fixed_ready_exchange", None))
    control = _request()
    if change == "missing":
        del control["nonce"]
    elif change == "extra":
        control["extra"] = "x"
    elif change == "bad_nonce":
        control["nonce"] = "BAD"
    else:
        control["binding_sha256"] = "BAD"
    monkeypatch.setattr(transport, "_peer", lambda *a, **kw: pytest.fail("unexpected peer I/O"))
    with pytest.raises(transport._Refused):
        transport._fixed_ready_exchange(None, None, 123, 456, control, b"synthetic", 1)
