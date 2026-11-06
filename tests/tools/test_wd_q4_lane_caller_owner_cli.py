"""Synthetic CLI orchestration tests; no native chain or production proof."""
import hashlib
import json
import threading

import pytest

from tools import wd_q4_guard_completion as guard_module
from tools import wd_q4_lane_caller_adapter as adapter
from tools import wd_q4_lane_caller_cli as cli


@pytest.fixture
def setup_cli(tmp_path, monkeypatch):
    lane = "codex-lead-1"
    runtime = tmp_path / "synthetic-runtime"
    state = runtime / "shared" / ".codex-audit" / lane / "q4-caller-state"
    state.mkdir(parents=True)
    fleet = json.dumps({"runtime_root": str(runtime), "lanes": [{"agent": lane}]},
                       sort_keys=True).encode("ascii")
    inputs = {"expected_fleet_sha256": hashlib.sha256(fleet).hexdigest(), "max_output_bytes": 1024}
    monkeypatch.setattr(cli, "_configuration", lambda: ({}, b"synthetic-descriptor", "b" * 64, fleet, {}, inputs))
    monkeypatch.setattr(cli, "_identity", lambda *args: (lane, b"synthetic-identity", "synthetic-token"))
    monkeypatch.setattr(cli, "_refuse", lambda code: code)
    writes = []
    monkeypatch.setattr(cli, "_write", lambda text: writes.append(text))
    acquired = []
    actual_acquire = guard_module.acquire_guard
    def acquire(**kwargs):
        item = actual_acquire(**kwargs)
        acquired.append(item)
        return item
    monkeypatch.setattr(guard_module, "acquire_guard", acquire)
    return {"marker": state / (lane + ".pending.json"), "acquired": acquired, "writes": writes}


def test_enabled_cli_requires_anchored_guard_before_reading_query(setup_cli, monkeypatch):
    calls = []
    def refuse(**kwargs):
        raise guard_module.GuardCompletionUnavailable()
    monkeypatch.setattr(guard_module, "acquire_guard", refuse)
    monkeypatch.setattr(cli, "_query", lambda: calls.append("query"))
    assert cli.main(["--enabled"]) != 0
    assert calls == [] and setup_cli["writes"] == []


def test_disabled_cli_does_not_acquire_guard_or_read_query(setup_cli, monkeypatch):
    monkeypatch.setattr(cli, "_query", lambda: pytest.fail("disabled query"))
    assert cli.main([]) == 0
    assert setup_cli["acquired"] == [] and setup_cli["writes"] == ['{"status":"OFF"}\n']


def test_query_failure_cancels_unstarted_guard_and_releases_marker(setup_cli, monkeypatch):
    def fail():
        assert setup_cli["marker"].is_file()
        raise ValueError("synthetic query failure")
    monkeypatch.setattr(cli, "_query", fail)
    assert cli.main(["--enabled"]) != 0
    assert len(setup_cli["acquired"]) == 1 and not setup_cli["marker"].exists()
    assert setup_cli["acquired"][-1]._registry._uncertain is False
    assert setup_cli["writes"] == []


def test_owner_context_handoff_precedes_adapter_dispatch(setup_cli, monkeypatch):
    monkeypatch.setattr(cli, "_query", lambda: ("synthetic-query", None))
    events = []
    original = guard_module._OwnerContext._handoff
    def handoff(context):
        events.append("handoff")
        return original(context)
    def adapter_call(**kwargs):
        events.append("adapter")
        assert events == ["handoff", "adapter"]
        raise adapter.CallerCleanupUnknown()
    monkeypatch.setattr(guard_module._OwnerContext, "_handoff", handoff)
    monkeypatch.setattr(adapter, "call_lane_session", adapter_call)
    assert cli.main(["--enabled"]) != 0
    assert events == ["handoff", "adapter"] and setup_cli["marker"].is_file()
    assert setup_cli["acquired"][-1]._registry._uncertain is True


def test_failed_unstarted_cancellation_retains_marker_and_marks_unknown(setup_cli, monkeypatch):
    def fail_query():
        raise ValueError("invalid synthetic query")
    def fail_cancel(*args):
        raise guard_module.GuardCompletionUnavailable()
    monkeypatch.setattr(cli, "_query", fail_query)
    monkeypatch.setattr(guard_module._Guard, "_cancel_unstarted", fail_cancel)
    assert cli.main(["--enabled"]) != 0
    assert setup_cli["marker"].is_file() and setup_cli["acquired"][-1]._registry._uncertain
    assert setup_cli["writes"] == []


def test_query_interrupt_preserves_unknown_instead_of_cancelling(setup_cli, monkeypatch):
    def interrupted():
        raise KeyboardInterrupt()
    monkeypatch.setattr(cli, "_query", interrupted)
    assert cli.main(["--enabled"]) == 130
    assert setup_cli["marker"].is_file() and setup_cli["acquired"][-1]._registry._uncertain
    assert setup_cli["writes"] == []


def test_failed_handoff_does_not_dispatch_or_cancel(setup_cli, monkeypatch):
    monkeypatch.setattr(cli, "_query", lambda: ("synthetic-query", None))
    def fail_handoff(*args):
        raise guard_module.GuardCompletionUnavailable()
    monkeypatch.setattr(guard_module._OwnerContext, "_handoff", fail_handoff)
    monkeypatch.setattr(adapter, "call_lane_session", lambda **kwargs: pytest.fail("failed handoff dispatched"))
    assert cli.main(["--enabled"]) != 0
    assert setup_cli["marker"].is_file() and setup_cli["acquired"][-1]._registry._uncertain
    assert setup_cli["writes"] == []


def test_incomplete_owners_never_release_or_publish(setup_cli, monkeypatch):
    monkeypatch.setattr(cli, "_query", lambda: ("synthetic-query", None))
    monkeypatch.setattr(adapter, "call_lane_session", lambda **kwargs: {
        "status": "VALIDATED", "cleanup": "chain_confirmed", "inner_status": "ANSWER"})
    monkeypatch.setattr(cli, "_output", lambda *args: "synthetic-output")
    assert cli.main(["--enabled"]) != 0
    assert setup_cli["marker"].is_file() and setup_cli["writes"] == []
    assert setup_cli["acquired"][0]._registry._uncertain is True


def test_synthetic_protocol_success_does_not_abort_after_release(setup_cli, monkeypatch):
    """Only checks CLI ordering; synthetic ticket completion is not cleanup proof."""
    monkeypatch.setattr(cli, "_query", lambda: ("synthetic-query", None))
    def fake_adapter(**kwargs):
        item, context = kwargs["_owner_guard"], kwargs["_owner_context"]
        assert item._accept_owner_context(context) is context
        for name in ("adapter", "attestation", "transport"):
            ticket = context._ticket(name)
            assert context._accept_ticket(name, ticket) is ticket
            ticket._begin()
            ticket._complete()
        return {"status": "VALIDATED", "cleanup": "chain_confirmed", "inner_status": "ANSWER"}
    monkeypatch.setattr(adapter, "call_lane_session", fake_adapter)
    monkeypatch.setattr(cli, "_output", lambda *args: "synthetic-output")
    assert cli.main(["--enabled"]) == 0
    assert not setup_cli["marker"].exists()
    assert setup_cli["acquired"][0]._registry._uncertain is False
    assert setup_cli["writes"] == ["synthetic-output"]


def test_foreign_guard_context_refuses_before_query(setup_cli, tmp_path, monkeypatch):
    lane = "codex-lead-1"
    runtime = tmp_path / "foreign-runtime"
    state = runtime / "shared" / ".codex-audit" / lane / "q4-caller-state"
    state.mkdir(parents=True)
    fleet = json.dumps({"runtime_root": str(runtime), "lanes": [{"agent": lane}]},
                       sort_keys=True).encode("ascii")
    foreign = guard_module.acquire_guard(fleet_bytes=fleet,
        expected_fleet_sha256=hashlib.sha256(fleet).hexdigest(), lane=lane,
        identity_sha256="a" * 64, descriptor_sha256="b" * 64)
    context = foreign._owner_context()
    monkeypatch.setattr(guard_module._Guard, "_owner_context", lambda self: context)
    calls = []
    monkeypatch.setattr(cli, "_query", lambda: calls.append("query"))
    assert cli.main(["--enabled"]) != 0
    assert calls == [] and setup_cli["writes"] == [] and setup_cli["marker"].is_file()
    assert setup_cli["acquired"][-1]._registry._uncertain is True


def test_same_lane_pending_marker_blocks_second_query(setup_cli, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    reads, errors = [], []
    def query():
        reads.append("read")
        entered.set()
        if not release.wait(5):
            raise RuntimeError("test release timed out")
        raise ValueError("synthetic invalid query")
    monkeypatch.setattr(cli, "_query", query)
    def first():
        try:
            cli.main(["--enabled"])
        except BaseException as exc:
            errors.append(exc)
    thread = threading.Thread(target=first)
    thread.start()
    try:
        assert entered.wait(5)
        assert cli.main(["--enabled"]) != 0
        assert reads == ["read"] and setup_cli["marker"].is_file()
    finally:
        release.set()
        thread.join(timeout=5)
    assert not thread.is_alive() and not errors
    assert not setup_cli["marker"].exists() and setup_cli["writes"] == []


def test_interrupt_after_adapter_before_finish_marks_unknown(setup_cli, monkeypatch):
    monkeypatch.setattr(cli, "_query", lambda: ("synthetic-query", None))
    monkeypatch.setattr(adapter, "call_lane_session", lambda **kwargs: {
        "status": "VALIDATED", "cleanup": "chain_confirmed", "inner_status": "ANSWER"})
    def interrupt(*args):
        raise KeyboardInterrupt()
    monkeypatch.setattr(cli, "_output", interrupt)
    assert cli.main(["--enabled"]) == 130
    assert setup_cli["marker"].is_file() and setup_cli["writes"] == []
    assert setup_cli["acquired"][0]._registry._uncertain is True


def test_release_failure_does_not_publish_and_marks_unknown(setup_cli, monkeypatch):
    monkeypatch.setattr(cli, "_query", lambda: ("synthetic-query", None))
    def fake_adapter(**kwargs):
        context = kwargs["_owner_context"]
        for name in ("adapter", "attestation", "transport"):
            ticket = context._ticket(name)
            ticket._begin()
            ticket._complete()
        return {"status": "VALIDATED", "cleanup": "chain_confirmed", "inner_status": "ANSWER"}
    monkeypatch.setattr(adapter, "call_lane_session", fake_adapter)
    monkeypatch.setattr(cli, "_output", lambda *args: "synthetic-output")
    def fail_release(self, witness):
        raise OSError("synthetic release failure")
    monkeypatch.setattr(guard_module._Guard, "release", fail_release)
    assert cli.main(["--enabled"]) != 0
    assert setup_cli["marker"].is_file() and setup_cli["writes"] == []
    assert setup_cli["acquired"][0]._registry._uncertain is True


def test_output_failure_after_release_does_not_abort_consumed_guard(setup_cli, monkeypatch):
    monkeypatch.setattr(cli, "_query", lambda: ("synthetic-query", None))
    def fake_adapter(**kwargs):
        context = kwargs["_owner_context"]
        for name in ("adapter", "attestation", "transport"):
            ticket = context._ticket(name)
            ticket._begin()
            ticket._complete()
        return {"status": "VALIDATED", "cleanup": "chain_confirmed", "inner_status": "ANSWER"}
    monkeypatch.setattr(adapter, "call_lane_session", fake_adapter)
    monkeypatch.setattr(cli, "_output", lambda *args: "synthetic-output")
    def failed_write(*args):
        raise OSError("synthetic output failure")
    monkeypatch.setattr(cli, "_write", failed_write)
    assert cli.main(["--enabled"]) != 0
    item = setup_cli["acquired"][0]
    assert item._registry._used is True and item._registry._uncertain is False
    assert not setup_cli["marker"].exists()
