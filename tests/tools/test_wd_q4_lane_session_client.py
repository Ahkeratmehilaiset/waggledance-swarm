"""Synthetic session composition: no live bridge, installed runtime or identity."""

import hashlib
import importlib.util
import json
from datetime import datetime
from pathlib import Path

import pytest

from tools import wd_q4_lane_link, wd_q4_roster_from_fleet
from tools import wd_q4_lane_session_client as session


def canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha(value):
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def case(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("bound_lane_test_helpers_session",
        Path(__file__).with_name("test_wd_q4_bound_lane_transport.py"))
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    previous = helper.case.__wrapped__(monkeypatch, tmp_path)
    roster = json.loads(previous["_parts"]["roster_bytes"])
    fleet_bytes = canon({"lanes": roster["lanes"], "tools_supervisor": roster["tools_supervisor"]})
    roster_raw, roster_sha = wd_q4_roster_from_fleet.build_roster_snapshot(fleet_bytes, sha(fleet_bytes), enabled=True)
    assert roster_raw == previous["_parts"]["roster_bytes"] and roster_sha == previous["_binding"]["roster_sha256"]
    binding_bytes = canon({"schema": "wd.q4-lane-binding.v1", "bundle_commit": previous["expected_commit"],
        "manifest_sha256": previous["expected_manifest_sha256"], "wrapper_sha256": previous["expected_wrapper_sha256"],
        "public_plan_sha256": previous["expected_public_plan_sha256"], "binding": previous["_binding"],
        "lanes": sorted(wd_q4_lane_link._LANES)})
    query = json.loads(previous["_query"])
    return {"lane": "codex-lead-1", "situation": query["situation"],
            "time": datetime.fromisoformat(query["time"].replace("Z", "+00:00")),
            "fleet_bytes": fleet_bytes, "expected_fleet_sha256": sha(fleet_bytes),
            "binding_bytes": binding_bytes, "expected_binding_sha256": sha(binding_bytes),
            **{key: previous[key] for key in ("bundle_root", "public_plan_path", "executable",
                "expected_interpreter_sha256", "interpreter_max_bytes", "fixed_plan_bytes", "expected_fixed_plan_sha256",
                "fixed_bundle_bytes", "runtime_manifest_bytes", "cwd", "timeout_seconds", "max_output_bytes")},
            "enabled": True, "_expected": previous["_expected"]}


def call(case):
    return session.query_session(**{k: v for k, v in case.items() if not k.startswith("_")})


@pytest.mark.parametrize("lane", sorted(wd_q4_lane_link._LANES))
def test_real_synthetic_fixed_peer_is_available_to_each_declared_lane(case, lane):
    case["lane"] = lane
    answer = call(case)
    assert answer["status"] == "ANSWER" and answer["result_count"] == len(case["_expected"])
    assert answer["lane"] == lane and answer["trust"]["lane_authentication"] == "UNKNOWN"
    assert answer["trust"]["freshness"] == "UNKNOWN"


@pytest.mark.parametrize("pin", sorted(wd_q4_lane_link._PUBLIC_BIND_PINS | {"projection_sha256"}))
def test_binding_crosswire_refuses_before_peer(case, pin, monkeypatch):
    document = json.loads(case["binding_bytes"])
    document["binding"][pin] = "0" * 64
    case["binding_bytes"] = canon(document)
    case["expected_binding_sha256"] = sha(case["binding_bytes"])
    monkeypatch.setattr(session.fixed, "run_fixed_peer", lambda **_: pytest.fail("peer started for crosswired binding"))
    with pytest.raises(session.LaneSessionRefused): call(case)


@pytest.mark.parametrize("field", ["fleet", "binding", "fixed_plan", "fixed_bundle", "runtime_manifest", "interpreter",
                                  "oversize_plan", "oversize_manifest"])
def test_bad_caller_pins_refuse_before_peer(case, field, monkeypatch):
    if field == "fleet": case["expected_fleet_sha256"] = "0" * 64
    if field == "binding": case["expected_binding_sha256"] = "0" * 64
    if field == "fixed_plan": case["expected_fixed_plan_sha256"] = "0" * 64
    if field == "fixed_bundle": case["fixed_bundle_bytes"] += b" "
    if field == "runtime_manifest": case["runtime_manifest_bytes"] += b" "
    if field == "interpreter": case["expected_interpreter_sha256"] = "0" * 64
    if field == "oversize_plan": case["fixed_plan_bytes"] = b"x" * (session.fixed._MAX_PLAN + 1)
    if field == "oversize_manifest": case["runtime_manifest_bytes"] = b"x" * (session.fixed._MAX_RUNTIME_MANIFEST + 1)
    monkeypatch.setattr(session.fixed, "run_fixed_peer", lambda **_: pytest.fail("peer started for wrong pin"))
    with pytest.raises(session.LaneSessionRefused): call(case)


def test_default_off_before_any_factory_or_input_validation(case, monkeypatch):
    case.update(enabled=False, fleet_bytes=None, situation="SYNTHETIC_PRIVATE_MARKER")
    monkeypatch.setattr(session.bound, "make_bound_transport", lambda **_: pytest.fail("called while off"))
    with pytest.raises(session.LaneSessionRefused, match="disabled") as caught: call(case)
    assert caught.value.__context__ is None and "SYNTHETIC_PRIVATE_MARKER" not in str(caught.value)


@pytest.mark.parametrize("failure", ["timeout", "transport_cleanup", "attestation_cleanup"])
def test_critical_failure_is_sanitized_but_preserves_state(case, failure, monkeypatch):
    import traceback
    from tools.wd_q4_fixed_parent_attestation import AttestationCleanupUnknown
    marker, lease = "SYNTHETIC_PRIVATE_MARKER", object()
    case["situation"] = marker
    original = {"timeout": TimeoutError(marker), "transport_cleanup": session.fixed.TransportCleanupUnknown(marker),
                "attestation_cleanup": AttestationCleanupUnknown(lease)}[failure]
    def fail(**_):
        raise original
    monkeypatch.setattr(session.bound, "make_bound_transport", lambda **_: fail)
    with pytest.raises(type(original)) as caught: call(case)
    assert caught.value is not original and caught.value.__context__ is None and caught.value.__cause__ is None
    assert marker not in str(caught.value)
    if failure == "timeout": assert str(caught.value) == "secure_transport_timeout"
    if failure == "transport_cleanup": assert caught.value.prior_outcome == "other"
    if failure == "attestation_cleanup": assert caught.value.lease is lease
    frames = traceback.TracebackException.from_exception(caught.value, capture_locals=True).stack
    own = [frame for frame in frames if Path(frame.filename).name == "wd_q4_lane_session_client.py"]
    assert own and all(marker not in repr(frame.locals) for frame in own)


def test_fixed_refusal_is_returned_without_fabricated_answer(case, monkeypatch):
    refusal = canon({"schema": "wd.q4-public-query.v2", "status": "REFUSED", "reason": "runtime_refused"})
    monkeypatch.setattr(session.bound, "make_bound_transport", lambda **_: lambda **__: (refusal, 2))
    actual = call(case)
    assert actual["status"] == "REFUSED" and actual["reason"] == "runtime_refused"
    assert actual["results"] == [] and actual["result_count"] == 0 and actual["binding"] is None


def test_closed_fixed_plan_rejects_extra_field_before_peer(case, monkeypatch):
    document = json.loads(case["fixed_plan_bytes"])
    document["extra"] = "synthetic"
    case["fixed_plan_bytes"] = canon(document)
    case["expected_fixed_plan_sha256"] = sha(case["fixed_plan_bytes"])
    monkeypatch.setattr(session.fixed, "run_fixed_peer", lambda **_: pytest.fail("peer started for extra plan field"))
    with pytest.raises(session.LaneSessionRefused): call(case)


def test_refusal_traceback_scrubs_only_session_frame(case):
    import traceback
    case["situation"] = "SYNTHETIC_PRIVATE_MARKER"
    case["expected_binding_sha256"] = "0" * 64
    with pytest.raises(session.LaneSessionRefused) as caught: call(case)
    frames = traceback.TracebackException.from_exception(caught.value, capture_locals=True).stack
    own = [frame for frame in frames if Path(frame.filename).name == "wd_q4_lane_session_client.py"]
    assert own and all("SYNTHETIC_PRIVATE_MARKER" not in repr(frame.locals) for frame in own)


@pytest.mark.parametrize('failure', ['interrupt', 'exit'])
@pytest.mark.parametrize('stage', ['binding', 'transport_factory'])
def test_control_flow_interrupts_scrub_own_frame_and_keep_signal(case, monkeypatch, failure, stage):
    import traceback

    case['situation'] = 'SYNTHETIC_PRIVATE_MARKER'
    original = KeyboardInterrupt('synthetic_private_interrupt') if failure == 'interrupt' else SystemExit(37)
    def fail(*args, **kwargs):
        raise original
    if stage == 'binding':
        monkeypatch.setattr(session.lane_binding, 'resolve_lane_binding', fail)
    else:
        monkeypatch.setattr(session.bound, 'make_bound_transport', fail)
    with pytest.raises(type(original)) as caught:
        call(case)
    error = caught.value
    assert error is not original and error.__context__ is None and error.__cause__ is None
    if failure == 'exit':
        assert error.code == 37
    else:
        assert type(error) is KeyboardInterrupt
    frames = [frame for frame, _ in traceback.walk_tb(error.__traceback__)
              if Path(frame.f_code.co_filename).name == 'wd_q4_lane_session_client.py']
    assert len(frames) == 1
    assert not {'situation', 'fleet_bytes', 'binding_bytes', 'fixed_plan_bytes', 'fixed_bundle_bytes',
                'runtime_manifest_bytes', 'bundle_root', 'public_plan_path'} & set(frames[0].f_locals)


@pytest.mark.parametrize('kind, expected', [
    ('none', None), ('zero', 0), ('integer', 37), ('string', 1), ('object', 1), ('bool', 1),
])
def test_system_exit_code_never_exposes_noninteger_payload(case, monkeypatch, kind, expected):
    import traceback

    marker = 'SYNTHETIC_PRIVATE_EXIT_PAYLOAD'
    class PrivateObject:
        def __repr__(self):
            return marker
    values = {'none': None, 'zero': 0, 'integer': 37, 'string': marker,
              'object': PrivateObject(), 'bool': True}
    def fail(*args, **kwargs):
        raise SystemExit(values[kind])
    monkeypatch.setattr(session.lane_binding, 'resolve_lane_binding', fail)
    with pytest.raises(SystemExit) as caught:
        call(case)
    error = caught.value
    assert error.code == expected and type(error.code) is type(expected)
    assert error.__context__ is None and error.__cause__ is None
    assert marker not in str(error) and marker not in repr(error.args)
    frames = [frame for frame, _ in traceback.walk_tb(error.__traceback__)
              if Path(frame.f_code.co_filename).name == 'wd_q4_lane_session_client.py']
    assert len(frames) == 1 and marker not in repr(frames[0].f_locals)
