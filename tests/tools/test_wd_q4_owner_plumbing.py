"""RED contract for a not-yet-defined guarded Q4 owner context.

This does not fabricate owner tickets or launch a chain. The proposed keyword
name is provisional; the missing context factory and its validation must be
agreed before these tests can become a guarded integration claim.
"""
import inspect
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from tools import wd_q4_bound_lane_transport as bound
from tools import wd_q4_fixed_result_adapter as result
from tools import wd_q4_guard_completion as guard
from tools import wd_q4_lane_session_client as session


@pytest.mark.parametrize("function", [
    session.query_session,
    bound.make_bound_transport,
    result.invoke_fixed_public,
])
def test_optional_context_plumbing_boundary_exists(function):
    parameters = inspect.signature(function).parameters
    for name in ("_owner_guard", "_owner_context"):
        assert name in parameters, "call-bound owner-context plumbing is not implemented"
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert parameters[name].default is None


def test_guard_owns_context_factory_before_any_context_is_accepted():
    assert callable(getattr(guard._Guard, "_owner_context", None)), (
        "guard has no call-bound owner-context factory or validator")


def test_legacy_session_off_without_context_stops_before_any_binding(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("disabled session reached a binding or factory")
    monkeypatch.setattr(session.lane_binding, "resolve_lane_binding", unexpected)
    monkeypatch.setattr(bound, "make_bound_transport", unexpected)
    with pytest.raises(session.LaneSessionRefused, match="disabled"):
        session.query_session(lane=None, situation=None, time=None, fleet_bytes=None,
                              expected_fleet_sha256=None, binding_bytes=None,
                              expected_binding_sha256=None, bundle_root=None, public_plan_path=None,
                              executable=None, expected_interpreter_sha256=None, interpreter_max_bytes=None,
                              fixed_plan_bytes=None, expected_fixed_plan_sha256=None, fixed_bundle_bytes=None,
                              runtime_manifest_bytes=None, cwd=None, timeout_seconds=None, max_output_bytes=None)


def test_legacy_bound_and_result_off_without_context_do_not_launch(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("disabled call reached fixed peer")
    monkeypatch.setattr(result.fixed, "run_fixed_peer", unexpected)
    with pytest.raises(bound.BoundLaneTransportRefused):
        bound.make_bound_transport(bundle_root=None, expected_commit=None, expected_manifest_sha256=None,
            expected_wrapper_sha256=None, public_plan_path=None, expected_public_plan_sha256=None,
            executable=None, expected_interpreter_sha256=None, interpreter_max_bytes=None,
            fixed_plan_bytes=None, expected_fixed_plan_sha256=None, fixed_bundle_bytes=None,
            runtime_manifest_bytes=None, cwd=None, timeout_seconds=None, max_output_bytes=None)
    with pytest.raises(result.FixedResultAdapterRefused):
        result.invoke_fixed_public(executable=None, expected_interpreter_sha256=None, interpreter_max_bytes=None,
            plan_bytes=None, expected_plan_sha256=None, bundle_bytes=None, runtime_manifest_bytes=None,
            query_bytes=None, cwd=None, timeout_seconds=None, max_output_bytes=None)


def test_enabled_cli_refuses_missing_guard_before_reading_query(monkeypatch):
    from tools import wd_q4_lane_caller_cli as cli
    calls = []
    monkeypatch.setattr(cli, "_configuration", lambda: ({}, b"synthetic-descriptor", "b" * 64,
        b"synthetic-fleet", {}, {"max_output_bytes": 1024}))
    monkeypatch.setattr(cli, "_identity", lambda *args: ("codex-lead-1", b"synthetic-identity", "synthetic-token"))
    def read_query():
        calls.append("read")
        raise RuntimeError("synthetic query read")
    monkeypatch.setattr(cli, "_query", read_query)
    monkeypatch.setattr(cli, "_refuse", lambda code: code)
    assert cli.main(["--enabled"]) != 0
    assert calls == [], "enabled CLI read a query without an anchored guard context"


def test_disabled_cli_does_not_obtain_guard_or_read_query(monkeypatch):
    from tools import wd_q4_lane_caller_cli as cli
    calls = []
    monkeypatch.setattr(cli, "_configuration", lambda: calls.append("configuration"))
    monkeypatch.setattr(cli, "_query", lambda: calls.append("query"))
    monkeypatch.setattr(cli, "_write", lambda text: calls.append(text))
    assert cli.main([]) == 0
    assert calls == ['{"status":"OFF"}\n']


@pytest.mark.parametrize("foreign", [None, "transport", {}, object()])
def test_unregistered_owner_value_cannot_be_a_completion_proof(foreign):
    registry = guard._Registry(("synthetic-call", "synthetic-lane", "a" * 64, "b" * 64))
    with pytest.raises(guard.GuardCompletionUnavailable):
        registry._complete_owner(foreign)
    with pytest.raises(guard.GuardCompletionUnavailable):
        registry._issue()


def _synthetic_guard(base, lane):
    """Construct a real guard over a test-only provisioned directory."""
    runtime = base / "runtime"
    state = runtime / "shared" / ".codex-audit" / lane / "q4-caller-state"
    state.mkdir(parents=True)
    fleet = json.dumps({"runtime_root": str(runtime), "lanes": [{"agent": lane}]},
                       sort_keys=True).encode("ascii")
    item = guard.acquire_guard(fleet_bytes=fleet, expected_fleet_sha256=hashlib.sha256(fleet).hexdigest(),
                               lane=lane, identity_sha256="a" * 64, descriptor_sha256="b" * 64)
    return item, state / (lane + ".pending.json")


@pytest.mark.parametrize("foreign", [None, "context", {}, object()])
def test_guard_rejects_non_context_without_completing_or_clearing(tmp_path, foreign):
    item, marker = _synthetic_guard(tmp_path / "one", "codex-lead-1")
    before = marker.read_bytes()
    with pytest.raises(guard.GuardCompletionUnavailable):
        item._accept_owner_context(foreign)
    assert marker.read_bytes() == before
    with pytest.raises(guard.GuardCompletionUnavailable):
        item._registry._issue()


@pytest.mark.parametrize("other_lane", ["codex-lead-1", "codex-tools-1"])
def test_context_of_other_guard_is_rejected_even_with_same_lane(tmp_path, other_lane):
    left, left_marker = _synthetic_guard(tmp_path / "left", "codex-lead-1")
    right, right_marker = _synthetic_guard(tmp_path / "right", other_lane)
    before = left_marker.read_bytes(), right_marker.read_bytes()
    foreign = right._owner_context()
    with pytest.raises(guard.GuardCompletionUnavailable):
        left._accept_owner_context(foreign)
    assert (left_marker.read_bytes(), right_marker.read_bytes()) == before
    with pytest.raises(guard.GuardCompletionUnavailable):
        left._registry._issue()


def test_context_itself_is_not_an_owner_completion_capability(tmp_path):
    item, marker = _synthetic_guard(tmp_path / "one", "codex-lead-1")
    context = item._owner_context()
    before = marker.read_bytes()
    with pytest.raises(guard.GuardCompletionUnavailable):
        item._registry._complete_owner(context)
    with pytest.raises(guard.GuardCompletionUnavailable):
        item._registry._issue()
    assert marker.read_bytes() == before


def test_exact_context_has_three_distinct_noninterchangeable_tickets(tmp_path):
    item, marker = _synthetic_guard(tmp_path / "one", "codex-lead-1")
    context = item._owner_context()
    assert item._accept_owner_context(context) is context
    before = marker.read_bytes()
    tickets = {name: context._ticket(name) for name in ("adapter", "attestation", "transport")}
    assert len({id(ticket) for ticket in tickets.values()}) == 3
    for name, ticket in tickets.items():
        assert context._accept_ticket(name, ticket) is ticket
        wrong_name = next(other for other in tickets if other != name)
        with pytest.raises(guard.GuardCompletionUnavailable):
            context._accept_ticket(wrong_name, ticket)
        with pytest.raises(guard.GuardCompletionUnavailable):
            context._accept_ticket(name, object())
    assert marker.read_bytes() == before
    with pytest.raises(guard.GuardCompletionUnavailable):
        item._registry._issue()


def test_same_type_context_with_forged_identity_is_rejected(tmp_path):
    item, marker = _synthetic_guard(tmp_path / "one", "codex-lead-1")
    context = item._owner_context()
    before = marker.read_bytes()
    try:
        counterfeit = object.__new__(type(context))
    except TypeError:
        # A class that cannot be allocated outside its owner is also acceptable.
        counterfeit = None
    with pytest.raises(guard.GuardCompletionUnavailable):
        item._accept_owner_context(counterfeit)
    assert marker.read_bytes() == before
    with pytest.raises(guard.GuardCompletionUnavailable):
        item._registry._issue()


def test_aborted_context_is_not_reusable_or_a_completion(tmp_path):
    item, marker = _synthetic_guard(tmp_path / "one", "codex-lead-1")
    context = item._owner_context()
    before = marker.read_bytes()
    context._abort()
    with pytest.raises(guard.GuardCompletionUnavailable):
        item._accept_owner_context(context)
    with pytest.raises(guard.GuardCompletionUnavailable):
        item._registry._issue()
    assert marker.read_bytes() == before


@pytest.mark.parametrize("field", ["_guard", "_registry", "_binding"])
def test_context_binding_swap_refuses_or_is_immutable(tmp_path, field):
    left, left_marker = _synthetic_guard(tmp_path / "left", "codex-lead-1")
    right, right_marker = _synthetic_guard(tmp_path / "right", "codex-tools-1")
    context = left._owner_context()
    before = left_marker.read_bytes(), right_marker.read_bytes()
    replacement = right if field == "_guard" else getattr(right, field)
    try:
        object.__setattr__(context, field, replacement)
    except (AttributeError, TypeError):
        pass  # Immutable context is also acceptable.
    else:
        with pytest.raises(guard.GuardCompletionUnavailable):
            left._accept_owner_context(context)
    assert (left_marker.read_bytes(), right_marker.read_bytes()) == before
    with pytest.raises(guard.GuardCompletionUnavailable):
        left._registry._issue()


def test_context_cannot_finish_before_all_owner_cleanup(tmp_path):
    item, marker = _synthetic_guard(tmp_path / "one", "codex-lead-1")
    context = item._owner_context()
    before = marker.read_bytes()
    with pytest.raises(guard.GuardCompletionUnavailable):
        context._finish()
    with pytest.raises(guard.GuardCompletionUnavailable):
        item._registry._issue()
    assert marker.read_bytes() == before


def _session_case_and_guard(tmp_path, monkeypatch):
    helper_path = Path(__file__).with_name("test_wd_q4_lane_session_client.py")
    spec = importlib.util.spec_from_file_location("synthetic_plumbing_session_helpers", helper_path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    case_root = tmp_path / "case"
    case_root.mkdir()
    case = helper.case.__wrapped__(monkeypatch, case_root)
    runtime = tmp_path / "guard-runtime"
    state = runtime / "shared" / ".codex-audit" / "codex-lead-1" / "q4-caller-state"
    state.mkdir(parents=True)
    fleet = json.loads(case["fleet_bytes"])
    fleet["runtime_root"] = str(runtime)
    case["fleet_bytes"] = helper.canon(fleet)
    case["expected_fleet_sha256"] = helper.sha(case["fleet_bytes"])
    descriptor_pin = json.loads(case["binding_bytes"])["binding"]["descriptor_sha256"]
    item = guard.acquire_guard(fleet_bytes=case["fleet_bytes"],
                               expected_fleet_sha256=case["expected_fleet_sha256"],
                               lane="codex-lead-1", identity_sha256="a" * 64,
                               descriptor_sha256=descriptor_pin)
    marker = state / "codex-lead-1.pending.json"
    return case, item, marker


def test_foreign_real_guard_context_is_rejected_before_session_dispatch(tmp_path, monkeypatch):
    case, item, marker = _session_case_and_guard(tmp_path, monkeypatch)
    other, other_marker = _synthetic_guard(tmp_path / "other", "codex-lead-1")
    before = marker.read_bytes(), other_marker.read_bytes()
    context = other._owner_context()
    def unexpected(*args, **kwargs):
        pytest.fail("foreign context reached bound transport")
    monkeypatch.setattr(bound, "make_bound_transport", unexpected)
    with pytest.raises(session.LaneSessionRefused):
        session.query_session(**{key: value for key, value in case.items() if not key.startswith("_")},
                              _owner_guard=item, _owner_context=context)
    assert (marker.read_bytes(), other_marker.read_bytes()) == before
    with pytest.raises(guard.GuardCompletionUnavailable):
        item._registry._issue()


def test_foreign_context_rejected_by_bound_and_fixed_result_boundaries(tmp_path, monkeypatch):
    case, item, marker = _session_case_and_guard(tmp_path, monkeypatch)
    other, other_marker = _synthetic_guard(tmp_path / "other", "codex-lead-1")
    context = other._owner_context()
    binding = json.loads(case["binding_bytes"])
    def unexpected(*args, **kwargs):
        pytest.fail("foreign context reached native peer")
    monkeypatch.setattr(result.fixed, "run_fixed_peer", unexpected)
    with pytest.raises(bound.BoundLaneTransportRefused):
        bound.make_bound_transport(bundle_root=case["bundle_root"], expected_commit=binding["bundle_commit"],
            expected_manifest_sha256=binding["manifest_sha256"], expected_wrapper_sha256=binding["wrapper_sha256"],
            public_plan_path=case["public_plan_path"], expected_public_plan_sha256=binding["public_plan_sha256"],
            executable=case["executable"], expected_interpreter_sha256=case["expected_interpreter_sha256"],
            interpreter_max_bytes=case["interpreter_max_bytes"], fixed_plan_bytes=case["fixed_plan_bytes"],
            expected_fixed_plan_sha256=case["expected_fixed_plan_sha256"], fixed_bundle_bytes=case["fixed_bundle_bytes"],
            runtime_manifest_bytes=case["runtime_manifest_bytes"], cwd=case["cwd"],
            timeout_seconds=case["timeout_seconds"], max_output_bytes=case["max_output_bytes"], enabled=True,
            _owner_guard=item, _owner_context=context)
    with pytest.raises(result.FixedResultAdapterRefused):
        result.invoke_fixed_public(executable=case["executable"],
            expected_interpreter_sha256=case["expected_interpreter_sha256"],
            interpreter_max_bytes=case["interpreter_max_bytes"], plan_bytes=case["fixed_plan_bytes"],
            expected_plan_sha256=case["expected_fixed_plan_sha256"], bundle_bytes=case["fixed_bundle_bytes"],
            runtime_manifest_bytes=case["runtime_manifest_bytes"], query_bytes=b"synthetic-query", cwd=case["cwd"],
            timeout_seconds=case["timeout_seconds"], max_output_bytes=case["max_output_bytes"], enabled=True,
            _owner_guard=item, _owner_context=context)
    assert marker.is_file() and other_marker.is_file()
    with pytest.raises(guard.GuardCompletionUnavailable):
        item._registry._issue()


def test_fixed_result_guard_refusal_drops_query_from_own_traceback(tmp_path):
    item, _ = _synthetic_guard(tmp_path / "one", "codex-lead-1")
    foreign, _ = _synthetic_guard(tmp_path / "other", "codex-lead-1")
    context = foreign._owner_context()
    query = b"synthetic-private-query-not-a-real-secret"
    with pytest.raises(result.FixedResultAdapterRefused) as caught:
        result.invoke_fixed_public(executable=None, expected_interpreter_sha256=None, interpreter_max_bytes=None,
            plan_bytes=None, expected_plan_sha256=None, bundle_bytes=None, runtime_manifest_bytes=None,
            query_bytes=query, cwd=None, timeout_seconds=None, max_output_bytes=None, enabled=True,
            _owner_guard=item, _owner_context=context)
    tb = caught.value.__traceback__
    while tb:
        if tb.tb_frame.f_code.co_name == "invoke_fixed_public":
            assert query not in tb.tb_frame.f_locals.values()
        tb = tb.tb_next


@pytest.mark.parametrize("interrupt", [TimeoutError, KeyboardInterrupt, SystemExit])
def test_fixed_result_interrupt_preserves_type_without_query_in_own_frame(tmp_path, monkeypatch, interrupt):
    item, _ = _synthetic_guard(tmp_path / "one", "codex-lead-1")
    context = item._owner_context()
    query = b"synthetic-private-query-not-a-real-secret"

    def interrupted(*args):
        raise interrupt()

    monkeypatch.setattr(guard._Guard, "_accept_owner_context", interrupted)
    with pytest.raises(interrupt) as caught:
        result.invoke_fixed_public(executable=None, expected_interpreter_sha256=None, interpreter_max_bytes=None,
            plan_bytes=None, expected_plan_sha256=None, bundle_bytes=None, runtime_manifest_bytes=None,
            query_bytes=query, cwd=None, timeout_seconds=None, max_output_bytes=None, enabled=True,
            _owner_guard=item, _owner_context=context)
    tb = caught.value.__traceback__
    while tb:
        if tb.tb_frame.f_code.co_name == "invoke_fixed_public":
            assert query not in tb.tb_frame.f_locals.values()
        tb = tb.tb_next


@pytest.mark.parametrize("missing", ["guard", "context"])
def test_partial_owner_pair_is_rejected_before_session_dispatch(tmp_path, monkeypatch, missing):
    case, item, marker = _session_case_and_guard(tmp_path, monkeypatch)
    before = marker.read_bytes()
    context = item._owner_context()
    def unexpected(*args, **kwargs):
        pytest.fail("partial owner pair reached bound transport")
    monkeypatch.setattr(bound, "make_bound_transport", unexpected)
    owner_guard = None if missing == "guard" else item
    owner_context = None if missing == "context" else context
    with pytest.raises(session.LaneSessionRefused):
        session.query_session(**{key: value for key, value in case.items() if not key.startswith("_")},
                              _owner_guard=owner_guard, _owner_context=owner_context)
    assert marker.read_bytes() == before
    with pytest.raises(guard.GuardCompletionUnavailable):
        item._registry._issue()


@pytest.mark.parametrize("change", ["lane", "descriptor"])
def test_context_binding_mismatch_refuses_before_bound_factory(tmp_path, monkeypatch, change):
    case, item, marker = _session_case_and_guard(tmp_path, monkeypatch)
    context = item._owner_context()
    if change == "lane":
        case["lane"] = "fable-5"
    else:
        document = json.loads(case["binding_bytes"])
        document["binding"]["descriptor_sha256"] = "f" * 64
        case["binding_bytes"] = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("ascii")
        case["expected_binding_sha256"] = hashlib.sha256(case["binding_bytes"]).hexdigest()
    def unexpected(*args, **kwargs):
        pytest.fail("wrong owner binding reached bound factory")
    monkeypatch.setattr(bound, "make_bound_transport", unexpected)
    with pytest.raises(session.LaneSessionRefused):
        session.query_session(**{key: value for key, value in case.items() if not key.startswith("_")},
                              _owner_guard=item, _owner_context=context)
    assert marker.is_file()
    with pytest.raises(guard.GuardCompletionUnavailable):
        item._registry._issue()


def test_real_unwired_fixed_peer_refuses_without_attesting_or_spawning(tmp_path, monkeypatch):
    from tools import wd_q4_fixed_parent_attestation as attestation
    case, item, marker = _session_case_and_guard(tmp_path, monkeypatch)
    context = item._owner_context()
    def unexpected(*args, **kwargs):
        pytest.fail("unwired guarded route reached native admission")
    monkeypatch.setattr(attestation, "attest_file", unexpected)
    monkeypatch.setattr(result.fixed, "_run", unexpected)
    with pytest.raises(session.LaneSessionRefused):
        session.query_session(**{key: value for key, value in case.items() if not key.startswith("_")},
                              _owner_guard=item, _owner_context=context)
    assert marker.is_file()
    with pytest.raises(guard.GuardCompletionUnavailable):
        item._registry._issue()


def test_one_real_guard_context_reaches_all_three_boundaries_unchanged(tmp_path, monkeypatch):
    """Synthetic data, no native peer, and no owner completion is fabricated."""
    case, item, marker = _session_case_and_guard(tmp_path, monkeypatch)
    context = item._owner_context()
    observed = []
    original_bound = bound.make_bound_transport
    original_result = result.invoke_fixed_public

    def bound_spy(**kwargs):
        assert kwargs.get("_owner_guard") is item
        assert kwargs.get("_owner_context") is context
        observed.append("bound")
        return original_bound(**kwargs)

    def result_spy(**kwargs):
        assert kwargs.get("_owner_guard") is item
        assert kwargs.get("_owner_context") is context
        observed.append("fixed_result")
        return original_result(**kwargs)

    def no_native_peer(**kwargs):
        assert kwargs.get("_owner_guard") is item
        assert kwargs.get("_owner_context") is context
        observed.append("fixed_peer")
        raise result.fixed._Refused()

    monkeypatch.setattr(bound, "make_bound_transport", bound_spy)
    monkeypatch.setattr(result, "invoke_fixed_public", result_spy)
    monkeypatch.setattr(result.fixed, "run_fixed_peer", no_native_peer)
    answer = session.query_session(**{key: value for key, value in case.items() if not key.startswith("_")},
                                   _owner_guard=item, _owner_context=context)
    assert observed == ["bound", "fixed_result", "fixed_peer"]
    assert answer["status"] == "REFUSED"
    assert marker.is_file()
    with pytest.raises(guard.GuardCompletionUnavailable):
        item._registry._issue()
