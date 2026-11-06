"""Synthetic context routing; never calls the live session chain or bridge."""
import importlib.util
from pathlib import Path
import threading

import pytest

from tools import wd_q4_guard_completion as completion
from tools import wd_q4_lane_caller_adapter as adapter

spec = importlib.util.spec_from_file_location(
    "synthetic_adapter_context_inputs", Path(__file__).with_name("test_wd_q4_lane_caller_adapter.py"))
existing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(existing)
guard_spec = importlib.util.spec_from_file_location(
    "synthetic_guard_context_inputs", Path(__file__).with_name("test_wd_q4_guard_context.py"))
guard_existing = importlib.util.module_from_spec(guard_spec)
guard_spec.loader.exec_module(guard_existing)


@pytest.fixture
def case(monkeypatch):
    inputs = existing.synthetic_inputs()
    descriptor = existing.descriptor_for(inputs)
    identity = existing.identity_for(descriptor)
    monkeypatch.setattr(adapter, "_LOCK", threading.Lock())
    monkeypatch.setattr(adapter, "_QUARANTINED", False)
    monkeypatch.setattr(adapter, "_HOLD", [])
    monkeypatch.setattr(adapter, "_process_cwd", lambda: existing.PROCESS_CWD)
    kw = dict(enabled=True, identity_bytes=identity, expected_identity_sha256=existing.sha(identity),
              descriptor_bytes=descriptor, expected_descriptor_sha256=existing.sha(descriptor),
              situation=existing.CANARY, time=None, inputs=inputs)
    class Root:
        def revalidate(self):
            return None
    def make_guard(binding=None):
        bound = binding or ("1" * 32, existing.LANE, existing.sha(identity), existing.sha(descriptor))
        guard = completion._Guard(Root(), None, b"", None, bound)
        return guard, guard._owner_context()
    return kw, descriptor, make_guard


def test_exact_context_passed_to_session_and_only_adapter_ticket_completed(case, monkeypatch):
    kw, descriptor, make_guard = case
    guard, context = make_guard()
    calls = []
    def query(**kwargs):
        calls.append(kwargs)
        assert adapter._LOCK.locked()
        return existing.answer_for(existing.LANE, descriptor)
    monkeypatch.setattr(adapter.session, "query_session", query)
    result = adapter.call_lane_session(**kw, _owner_guard=guard, _owner_context=context)
    assert result["status"] == "VALIDATED"
    assert len(calls) == 1 and calls[0]["_owner_guard"] is guard and calls[0]["_owner_context"] is context
    assert guard._registry._done == {"adapter"}
    with pytest.raises(completion.GuardCompletionUnavailable):
        guard._registry._issue()


@pytest.mark.parametrize("which", ["lane", "identity", "descriptor"])
def test_guard_binding_mismatch_refuses_before_session(case, monkeypatch, which):
    kw, _, make_guard = case
    bound = ["1" * 32, existing.LANE, kw["expected_identity_sha256"], kw["expected_descriptor_sha256"]]
    bound[{"lane": 1, "identity": 2, "descriptor": 3}[which]] = "0" * (32 if which == "lane" else 64)
    guard, context = make_guard(tuple(bound))
    monkeypatch.setattr(adapter.session, "query_session", lambda **_: pytest.fail("chain must not run"))
    with pytest.raises(adapter.CallerRefused):
        adapter.call_lane_session(**kw, _owner_guard=guard, _owner_context=context)
    assert guard._registry._uncertain and not guard._registry._done


def test_cross_guard_and_partial_context_refused_before_session(case, monkeypatch):
    kw, _, make_guard = case
    guard, context = make_guard()
    other, foreign = make_guard()
    monkeypatch.setattr(adapter.session, "query_session", lambda **_: pytest.fail("chain must not run"))
    for extra in ({"_owner_guard": guard}, {"_owner_context": context},
                  {"_owner_guard": guard, "_owner_context": foreign}):
        with pytest.raises(adapter.CallerRefused):
            adapter.call_lane_session(**kw, **extra)
    assert guard._registry._done == set() and other._registry._done == set()


def test_chain_timeout_marks_only_uncertain_and_does_not_complete(case, monkeypatch):
    kw, _, make_guard = case
    guard, context = make_guard()
    def query(**kwargs):
        raise TimeoutError("synthetic")
    monkeypatch.setattr(adapter.session, "query_session", query)
    result = adapter.call_lane_session(**kw, _owner_guard=guard, _owner_context=context)
    assert result["status"] == "UNKNOWN"
    assert guard._registry._uncertain and not guard._registry._done


@pytest.mark.parametrize("error", [KeyboardInterrupt, SystemExit])
def test_interrupt_marks_guard_unknown_and_releases_lock(case, monkeypatch, error):
    kw, _, make_guard = case
    guard, context = make_guard()
    def query(**kwargs):
        raise error()
    monkeypatch.setattr(adapter.session, "query_session", query)
    with pytest.raises(error):
        adapter.call_lane_session(**kw, _owner_guard=guard, _owner_context=context)
    assert not adapter._LOCK.locked()
    assert guard._registry._uncertain and not guard._registry._done


def test_unowned_call_preserves_existing_session_signature(case, monkeypatch):
    kw, descriptor, _ = case
    def query(**kwargs):
        assert "_owner_guard" not in kwargs and "_owner_context" not in kwargs
        return existing.answer_for(existing.LANE, descriptor)
    monkeypatch.setattr(adapter.session, "query_session", query)
    assert adapter.call_lane_session(**kw)["status"] == "VALIDATED"


@pytest.mark.parametrize("outcome", ["answer", "timeout"])
def test_real_synthetic_pending_marker_remains_after_adapter_only(case, tmp_path, monkeypatch, outcome):
    kw, descriptor, _ = case
    guard, marker = guard_existing.new_guard(tmp_path, lane=existing.LANE, identity=kw["expected_identity_sha256"],
                                            descriptor=kw["expected_descriptor_sha256"])
    context = guard._owner_context()
    def query(**kwargs):
        if outcome == "timeout":
            raise TimeoutError("synthetic")
        return existing.answer_for(existing.LANE, descriptor)
    monkeypatch.setattr(adapter.session, "query_session", query)
    result = adapter.call_lane_session(**kw, _owner_guard=guard, _owner_context=context)
    assert result["status"] == ("VALIDATED" if outcome == "answer" else "UNKNOWN")
    assert marker.is_file()
    with pytest.raises(completion.GuardCompletionUnavailable):
        guard._registry._issue()


def test_off_does_not_read_guard_or_context():
    class NoTouch:
        def __getattribute__(self, name):
            raise AssertionError("disabled path accessed context")
    assert adapter.call_lane_session(enabled=False, _owner_guard=NoTouch(), _owner_context=NoTouch()) == {"status": "OFF"}


def test_unowned_disabled_remains_off():
    assert adapter.call_lane_session(enabled=False) == {"status": "OFF"}
