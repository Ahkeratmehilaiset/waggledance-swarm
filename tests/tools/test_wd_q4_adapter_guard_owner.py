"""Synthetic adapter-owner tests; no native process or production guard."""
import importlib.util
from pathlib import Path
import threading

import pytest

from tools import wd_q4_lane_caller_adapter as adapter


spec = importlib.util.spec_from_file_location(
    "synthetic_existing_caller_tests", Path(__file__).with_name("test_wd_q4_lane_caller_adapter.py"))
existing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(existing)


@pytest.fixture
def setup_call(monkeypatch):
    inputs = existing.synthetic_inputs()
    descriptor = existing.descriptor_for(inputs)
    identity = existing.identity_for(descriptor)
    monkeypatch.setattr(adapter, "_LOCK", threading.Lock())
    monkeypatch.setattr(adapter, "_QUARANTINED", False)
    monkeypatch.setattr(adapter, "_HOLD", [])
    monkeypatch.setattr(adapter, "_process_cwd", lambda: existing.PROCESS_CWD)
    answer = existing.answer_for(existing.LANE, descriptor)
    monkeypatch.setattr(adapter.session, "query_session", lambda **kwargs: answer)
    kw = dict(enabled=True, identity_bytes=identity, expected_identity_sha256=existing.sha(identity),
              descriptor_bytes=descriptor, expected_descriptor_sha256=existing.sha(descriptor),
              situation=existing.CANARY, time=None, inputs=inputs)
    events = []
    def event(name):
        # The owner callbacks must not be called while the adapter owns its lock.
        assert not adapter._LOCK.locked()
        events.append(name)
    owner = adapter._AdapterOwner(on_begin=lambda: event("begin"),
                                  on_complete=lambda: event("complete"),
                                  on_unknown=lambda: event("unknown"))
    return kw, events, owner


def test_owner_completes_only_after_real_adapter_path_releases_lock(setup_call):
    kw, events, owner = setup_call
    result = adapter.call_lane_session(**kw, _owner=owner)
    assert result["status"] == "VALIDATED"
    assert result["cleanup"] == "chain_confirmed"
    assert events == ["begin", "complete"]


@pytest.mark.parametrize("fault", ["precheck", "refused_output", "malformed_output", "timeout", "exception"])
def test_refusal_or_uncertain_chain_never_completes_owner(setup_call, monkeypatch, fault):
    kw, events, owner = setup_call
    if fault == "precheck":
        kw["expected_identity_sha256"] = "0" * 64
        with pytest.raises(adapter.CallerRefused):
            adapter.call_lane_session(**kw, _owner=owner)
    else:
        if fault == "refused_output":
            value = {"schema": "wd.q4-lane-answer.v1", "lane": existing.LANE, "status": "REFUSED",
                     "reason": "runtime_refused", "result_count": 0, "results": [], "binding": None,
                     "trust": dict(existing.TRUST)}
        elif fault == "malformed_output":
            value = {"status": "not-a-valid-answer"}
        elif fault == "timeout":
            value = TimeoutError("synthetic")
        else:
            value = RuntimeError("synthetic")
        def chain(**kwargs):
            if isinstance(value, BaseException):
                raise value
            return value
        monkeypatch.setattr(adapter.session, "query_session", chain)
        adapter.call_lane_session(**kw, _owner=owner)
    assert events == ["begin", "unknown"]


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
def test_interrupt_releases_lock_and_marks_owner_unknown(setup_call, monkeypatch, interrupt):
    kw, events, owner = setup_call
    def chain(**kwargs):
        raise interrupt()
    monkeypatch.setattr(adapter.session, "query_session", chain)
    with pytest.raises(interrupt):
        adapter.call_lane_session(**kw, _owner=owner)
    assert events == ["begin", "unknown"]


def test_owner_absent_keeps_default_off_unchanged():
    assert adapter.call_lane_session(enabled=False) == {"status": "OFF"}


def test_callback_exception_does_not_retain_private_query_in_adapter_frame(setup_call):
    kw, _, owner = setup_call
    def failed_completion():
        raise RuntimeError("synthetic callback failure")
    owner._on_complete = failed_completion
    with pytest.raises(RuntimeError) as captured:
        adapter.call_lane_session(**kw, _owner=owner)
    tb = captured.value.__traceback__
    while tb:
        if tb.tb_frame.f_code.co_name == "call_lane_session":
            assert existing.CANARY not in tb.tb_frame.f_locals.values()
            assert tb.tb_frame.f_locals.get("result") is None
        tb = tb.tb_next


def test_off_unknown_callback_error_is_not_masked(setup_call):
    kw, _, owner = setup_call
    def fail():
        raise RuntimeError("synthetic unknown callback failure")
    owner._on_unknown = fail
    with pytest.raises(RuntimeError, match="synthetic unknown callback failure"):
        adapter.call_lane_session(**dict(kw, enabled=False), _owner=owner)


def test_reentrant_unknown_during_completion_prevents_success(setup_call):
    kw, events, owner = setup_call
    owner._on_complete = owner._unknown
    with pytest.raises(adapter.CallerCleanupUnknown):
        adapter.call_lane_session(**kw, _owner=owner)
    assert events == ["begin", "unknown"]


def test_lock_release_failure_marks_unknown_and_never_completes(setup_call, monkeypatch):
    kw, events, owner = setup_call
    class FailAfterRelease:
        def __init__(self):
            self.lock = threading.Lock()
        def acquire(self, blocking=False):
            return self.lock.acquire(blocking=blocking)
        def release(self):
            self.lock.release()
            raise RuntimeError("synthetic release failure")
        def locked(self):
            return self.lock.locked()
    monkeypatch.setattr(adapter, "_LOCK", FailAfterRelease())
    with pytest.raises(RuntimeError, match="synthetic release failure"):
        adapter.call_lane_session(**kw, _owner=owner)
    assert events == ["begin", "unknown"]


def test_owner_begin_failure_marks_unknown_before_chain(setup_call, monkeypatch):
    kw, events, owner = setup_call
    monkeypatch.setattr(adapter.session, "query_session", lambda **kwargs: pytest.fail("unexpected chain"))
    def fail():
        raise RuntimeError("synthetic begin failure")
    owner._on_begin = fail
    with pytest.raises(RuntimeError, match="synthetic begin failure"):
        adapter.call_lane_session(**kw, _owner=owner)
    assert events == ["unknown"]


def test_reentrant_unknown_during_begin_refuses_before_chain(setup_call, monkeypatch):
    kw, events, owner = setup_call
    owner._on_begin = owner._unknown
    monkeypatch.setattr(adapter.session, "query_session", lambda **kwargs: pytest.fail("unexpected chain"))
    with pytest.raises(adapter.CallerCleanupUnknown):
        adapter.call_lane_session(**kw, _owner=owner)
    assert events == ["unknown"]


def test_busy_lock_marks_unknown_without_completing(setup_call):
    kw, _, _ = setup_call
    events = []
    owner = adapter._AdapterOwner(on_begin=lambda: events.append("begin"),
                                  on_complete=lambda: events.append("complete"),
                                  on_unknown=lambda: events.append("unknown"))
    adapter._LOCK.acquire()
    try:
        with pytest.raises(adapter.CallerRefused, match="caller_busy"):
            adapter.call_lane_session(**kw, _owner=owner)
    finally:
        adapter._LOCK.release()
    assert events == ["begin", "unknown"]


def test_precheck_attestation_unknown_retains_lease_and_marks_owner(setup_call, monkeypatch):
    kw, events, owner = setup_call
    lease = object()
    def fail(*args, **kwargs):
        raise adapter.AttestationCleanupUnknown(lease)
    monkeypatch.setattr(adapter, "_parse", fail)
    with pytest.raises(adapter.CallerCleanupUnknown):
        adapter.call_lane_session(**kw, _owner=owner)
    assert adapter._HOLD == [lease] and adapter._QUARANTINED is True
    assert events == ["begin", "unknown"]
