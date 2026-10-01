# SPDX-License-Identifier: BUSL-1.1
"""F26 P1a acquisition request-binding validator + refresh rule: pure fakes only (no file, clock, env or reader)."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import tools.wd_claim_request_binding as rb  # noqa: E402

TASK = "codex-lead-1/f26-binding-fixture"
AGENT = "fable-5"
RID = "a1b2c3d4-request"
DIGEST = "ab" * 32            # deliberately NOT the hash of the content: the stored digest is copied verbatim
REVISION = "20261001-fixture-v1"
LABELS = {"agent_uuid": "fixture-uuid", "session_id": "fixture-session", "run_id": "fixture-run"}
RECORD: list = []             # every hook call on a hostile object lands here


def request(**over):
    event = {"ts_utc": "2026-10-01T18:00:00Z", "agent": "codex-lead-1", "type": "wake_request", "task_id": TASK,
             "status": "assigned", "to": AGENT, "message": "do the fixture", "request_id": RID,
             "agent_uuid": "lead-uuid", "session_id": "lead-session", "run_id": "lead-run", "pid": 4242,
             "cwd": "C:\\fixture", "request_digest": DIGEST,
             "payload": {"task_revision": REVISION, "result_fields": ["summary"]},
             "expected_responders": {AGENT: dict(LABELS)}}
    event.update(over)
    return event


def control(**over):
    record = {"schema": rb.CONTROL_SCHEMA, "task_id": TASK, "request_id": RID, "request_digest": DIGEST,
              "state": "live", "observed_utc": "2026-10-01T18:00:00Z"}
    record.update(over)
    return record


_DEFAULT = object()


class Ports:
    def __init__(self, copies=_DEFAULT, controls=_DEFAULT):
        self.copies = [request()] if copies is _DEFAULT else copies
        self.controls = [control()] if controls is _DEFAULT else controls
        self.calls = []

    def lookup(self, request_id):
        self.calls.append(("lookup", request_id))
        return self.copies

    def current(self, task_id):
        self.calls.append(("current", task_id))
        return self.controls


def bind(event=None, ports=None, **over):
    ports = ports or Ports()
    args = dict(task_id=TASK, agent=AGENT, own_labels=dict(LABELS), canonical_lookup=ports.lookup,
                current_controls=ports.current)
    args.update(over)
    return rb.claim_request_binding(request() if event is None else event, **args)


def refused(reason, event=None, ports=None, **over):
    with pytest.raises(rb.BindingRefused) as caught:
        bind(event, ports, **over)
    assert caught.value.reason == reason
    return caught.value


# --- recording hostile objects: any hook call is recorded (and would be a defect) --------------------------

def _hooks(base):
    names = ("__eq__", "__ne__", "__hash__", "__bool__", "__len__", "__iter__", "__contains__", "__getitem__",
             "get", "keys", "items", "values", "split", "strip", "startswith", "__str__", "__format__",
             "__lt__", "__gt__", "__index__", "__float__", "__int__", "__repr__")

    def make(name):
        real = getattr(base, name, None)

        def hook(self, *args, **kwargs):
            RECORD.append((base.__name__, name))
            return real(self, *args, **kwargs)
        return hook
    return {name: make(name) for name in names if hasattr(base, name)}


HStr = type("HStr", (str,), _hooks(str))
HDict = type("HDict", (dict,), _hooks(dict))
HList = type("HList", (list,), _hooks(list))
HInt = type("HInt", (int,), _hooks(int))
HFloat = type("HFloat", (float,), _hooks(float))


@pytest.fixture(autouse=True)
def clean_record():
    RECORD.clear()   # each test touching a hostile object asserts RECORD == [] itself (a failure, not a teardown error)
    yield
    RECORD.clear()


# --- valid binding --------------------------------------------------------------------------------------

def test_a_valid_request_binds_the_closed_record_with_the_stored_digest_verbatim():
    event, ports = request(), Ports()
    binding = bind(event, ports)
    assert binding == {"schema": rb.BINDING_SCHEMA, "request_id": RID, "request_digest": DIGEST,
                       "task_revision": REVISION}
    assert type(binding) is dict and list(binding) == list(rb.BINDING_FIELDS)
    assert binding["request_digest"] is event["request_digest"]          # copied, never recomputed
    content = json.dumps({k: event.get(k) for k in rb.CONTENT_FIELDS}, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False)
    assert hashlib.sha256(content.encode()).hexdigest() != DIGEST       # a recompute would NOT give the stored one
    assert "identity_verified" not in binding and "authority" not in binding
    assert ports.calls == [("lookup", RID), ("current", TASK)]
    assert rb.validate_binding(binding) is binding


def test_inputs_are_not_mutated_and_the_output_is_a_new_dict():
    event, ports = request(), Ports()
    before = copy.deepcopy((event, ports.copies, ports.controls))
    binding = bind(event, ports)
    assert (event, ports.copies, ports.controls) == before and binding is not event


def test_transport_fields_are_not_canonical_a_copy_differing_only_in_them_binds():
    copy_ = request(ts_utc="2026-10-01T18:00:09Z", pid=1, cwd="D:\\elsewhere")
    assert bind(ports=Ports(copies=[copy_, request()]))["request_id"] == RID


def test_payload_fields_are_read_like_the_bridge_contract_and_must_agree_with_top_level():
    event = request(task_revision=REVISION)                              # both places, equal: fine
    assert bind(event, Ports(copies=[event]))["task_revision"] == REVISION
    event = request(task_revision="other")                               # both places, different
    refused(rb.R_FIELD_CONFLICT, event, Ports(copies=[event]))


def test_the_revision_may_come_from_top_level_only():
    event = request(task_revision=REVISION, payload={"result_fields": ["summary"]})
    assert bind(event, Ports(copies=[event]))["task_revision"] == REVISION


# --- refusals of the request itself ---------------------------------------------------------------------

@pytest.mark.parametrize("over, reason", [
    ({"type": "message"}, rb.R_MALFORMED),
    ({"type": None}, rb.R_MALFORMED),
    ({"payload": ["not", "a", "dict"]}, rb.R_MALFORMED),
    ({"agent": "codex-tools-1"}, rb.R_NOT_AUTHORITY),
    ({"agent": None}, rb.R_NOT_AUTHORITY),
    ({"request_id": "has space"}, rb.R_REQUEST_ID),
    ({"request_id": ""}, rb.R_REQUEST_ID),
    ({"request_id": 7}, rb.R_REQUEST_ID),
    ({"task_id": "codex-lead-1/other"}, rb.R_TASK),
    ({"task_id": None}, rb.R_TASK),
    ({"to": "fable-5,codex-tools-1"}, rb.R_RECIPIENT),
    ({"to": " fable-5"}, rb.R_RECIPIENT),
    ({"to": "FABLE-5"}, rb.R_RECIPIENT),
    ({"to": ["fable-5"]}, rb.R_RECIPIENT),
    ({"payload": {"result_fields": ["summary"]}}, rb.R_REVISION),
    ({"payload": {"task_revision": ""}}, rb.R_REVISION),
    ({"payload": {"task_revision": 3}}, rb.R_REVISION),
    ({"request_digest": "AB" * 32}, rb.R_DIGEST),
    ({"request_digest": "ab" * 31}, rb.R_DIGEST),
    ({"request_digest": None}, rb.R_DIGEST),
    ({"expected_responders": {}}, rb.R_OWNER),
    ({"expected_responders": None}, rb.R_OWNER),
    ({"expected_responders": {AGENT: dict(LABELS, run_id="other-run")}}, rb.R_OWNER),
    ({"expected_responders": {AGENT: dict(LABELS, extra="x")}}, rb.R_OWNER),
    ({"expected_responders": {AGENT: {"agent_uuid": "fixture-uuid", "session_id": "fixture-session"}}}, rb.R_OWNER),
    ({"expected_responders": {AGENT: dict(LABELS, session_id="")}}, rb.R_OWNER),
    ({"expected_responders": {"codex-tools-1": dict(LABELS)}}, rb.R_OWNER),
])
def test_each_request_refusal_is_stable_and_no_port_is_called(over, reason):
    event, ports = request(**over), Ports()
    refused(reason, event, ports)
    assert ports.calls == []


def test_labels_compare_exactly_by_type_too():
    refused(rb.R_OWNER, own_labels=dict(LABELS, run_id="fixture-run "))


@pytest.mark.parametrize("over", [
    {"task_id": ""}, {"task_id": None}, {"agent": "Fable-5"}, {"agent": ""}, {"own_labels": None},
    {"own_labels": dict(LABELS, extra="x")}, {"own_labels": dict(LABELS, run_id="")},
    {"canonical_lookup": None}, {"current_controls": "not callable"},
])
def test_invalid_caller_arguments_are_refused_before_anything_else(over):
    ports = Ports()
    refused(rb.R_CALLER, None, ports, **over)
    assert ports.calls == []


# --- strict validation before any comparison: non-finite, cycles, depth, foreign types -------------------

@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), {1: "int key"}, (1, 2), {"a"}, b"x",
                                 object()])
def test_a_non_json_value_anywhere_in_the_request_is_malformed(bad):
    event = request(payload={"task_revision": REVISION, "deep": [{"x": bad}]})
    ports = Ports()
    refused(rb.R_MALFORMED, event, ports)
    assert ports.calls == []


def test_a_cycle_is_malformed_and_terminates():
    event = request()
    event["payload"]["self"] = event["payload"]
    refused(rb.R_MALFORMED, event)


def test_excessive_depth_is_malformed():
    deep = "leaf"
    for _ in range(rb.MAX_DEPTH + 2):
        deep = [deep]
    refused(rb.R_MALFORMED, request(message=deep))


def test_a_shared_but_acyclic_subtree_is_accepted():
    shared = {"k": "v"}
    event = request(payload={"task_revision": REVISION, "a": shared, "b": shared})
    assert bind(event, Ports(copies=[copy.deepcopy(event)]))["task_revision"] == REVISION


# --- adversarial recording-hook twins and their exact built-in safe twins ---------------------------------

HOSTILE_REQUESTS = {
    "request_dict_subclass": lambda: HDict(request()),
    "type_str_subclass": lambda: request(type=HStr("wake_request")),
    "agent_str_subclass": lambda: request(agent=HStr("codex-lead-1")),
    "request_id_str_subclass": lambda: request(request_id=HStr(RID)),
    "task_id_str_subclass": lambda: request(task_id=HStr(TASK)),
    "to_str_subclass": lambda: request(to=HStr(AGENT)),
    "digest_str_subclass": lambda: request(request_digest=HStr(DIGEST)),
    "payload_dict_subclass": lambda: request(payload=HDict({"task_revision": REVISION})),
    "revision_str_subclass": lambda: request(payload={"task_revision": HStr(REVISION)}),
    "responders_dict_subclass": lambda: request(expected_responders=HDict({AGENT: dict(LABELS)})),
    "labels_dict_subclass": lambda: request(expected_responders={AGENT: HDict(LABELS)}),
    "label_value_str_subclass": lambda: request(expected_responders={AGENT: dict(LABELS, run_id=HStr("fixture-run"))}),
    "responder_key_str_subclass": lambda: request(expected_responders={HStr(AGENT): dict(LABELS)}),
    "deep_list_subclass": lambda: request(payload={"task_revision": REVISION, "x": HList([1])}),
    "deep_int_subclass": lambda: request(pid=HInt(5)),
    "deep_float_subclass": lambda: request(message=[HFloat(1.5)]),
    "top_level_key_str_subclass": lambda: dict({HStr("extra"): 1}, **request()),
}


@pytest.mark.parametrize("name", sorted(HOSTILE_REQUESTS))
def test_hostile_request_values_never_run_a_hook_and_are_refused_before_any_port(name):
    ports, hostile = Ports(), HOSTILE_REQUESTS[name]()
    RECORD.clear()                      # building a str-subclass KEY hashes it; only the code under test counts
    refused(rb.R_MALFORMED, hostile, ports)
    assert ports.calls == [] and RECORD == []


def _plain(value):
    """The exact built-in twin of a hostile structure (same content, plain types)."""
    if isinstance(value, dict):
        return {str.__str__(k) if isinstance(k, str) else k: _plain(v) for k, v in dict.items(value)}
    if isinstance(value, list):
        return [_plain(v) for v in list.__iter__(value)]
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return str.__str__(value)
    if isinstance(value, int):
        return int.__int__(value)
    if isinstance(value, float):
        return float.__float__(value)
    return value


@pytest.mark.parametrize("name", sorted(HOSTILE_REQUESTS))
def test_the_exact_built_in_twin_of_each_hostile_request_binds(name):
    hostile = HOSTILE_REQUESTS[name]()
    twin = _plain(hostile)
    RECORD.clear()                      # building the twin may touch the hostile object; the code under test must not
    assert bind(twin, Ports(copies=[copy.deepcopy(twin)]))["request_id"] == RID


@pytest.mark.parametrize("over", [
    {"task_id": HStr(TASK)}, {"agent": HStr(AGENT)}, {"own_labels": HDict(LABELS)},
    {"own_labels": dict(LABELS, run_id=HStr("fixture-run"))},
])
def test_hostile_caller_arguments_never_run_a_hook(over):
    refused(rb.R_CALLER, **over)
    assert RECORD == []


HOSTILE_COPIES = {
    "copies_list_subclass": lambda: HList([request()]),
    "copy_dict_subclass": lambda: [HDict(request())],
    "copy_deep_str_subclass": lambda: [request(message=HStr("do the fixture"))],
    "copy_nonfinite": lambda: [request(message=float("nan"))],
    "copies_tuple": lambda: (request(),),
    "copies_none": lambda: None,
    "copies_unknown": lambda: rb.UNKNOWN,
    "copy_not_a_dict": lambda: ["request"],
    "copy_other_request_id": lambda: [request(request_id="other-request")],
}


@pytest.mark.parametrize("name", sorted(HOSTILE_COPIES))
def test_an_unreadable_lookup_answer_stays_unknown_without_hooks_and_controls_are_not_asked(name):
    ports = Ports(copies=HOSTILE_COPIES[name]())
    refused(rb.R_LOOKUP_UNKNOWN, None, ports)
    assert ports.calls == [("lookup", RID)] and RECORD == []


HOSTILE_CONTROLS = {
    "controls_list_subclass": lambda: HList([control()]),
    "control_dict_subclass": lambda: [HDict(control())],
    "control_state_str_subclass": lambda: [control(state=HStr("live"))],
    "control_request_id_str_subclass": lambda: [control(request_id=HStr(RID))],
    "control_extra_field": lambda: [dict(control(), extra=1)],
    "control_missing_field": lambda: [{k: v for k, v in control().items() if k != "observed_utc"}],
    "control_unknown_state": lambda: [control(state="paused")],
    "control_bad_stamp": lambda: [control(observed_utc="yesterday")],
    "control_other_task": lambda: [control(task_id="codex-lead-1/other")],
    "control_bad_digest": lambda: [control(request_digest="AB" * 32)],
    "control_nonfinite": lambda: [dict(control(), state=float("nan"))],
    "controls_unknown": lambda: rb.UNKNOWN,
    "controls_none": lambda: None,
    "controls_empty": lambda: [],
    "controls_tuple": lambda: (control(),),
    "one_good_one_unreadable": lambda: [control(), "a cancellation?"],
}


@pytest.mark.parametrize("name", sorted(HOSTILE_CONTROLS))
def test_an_unknown_or_unreadable_control_stays_unknown_without_hooks(name):
    refused(rb.R_CONTROL_UNKNOWN, None, Ports(controls=HOSTILE_CONTROLS[name]()))
    assert RECORD == []


def test_the_exact_built_in_control_twin_binds_and_identical_duplicates_are_one_control():
    assert bind(ports=Ports(controls=[control(), control()]))["request_digest"] == DIGEST


@pytest.mark.parametrize("controls, reason", [
    ([control(state="cancelled")], rb.R_CANCELLED),
    ([control(request_id="newer-request")], rb.R_SUPERSEDED),
    ([control(request_digest="cd" * 32)], rb.R_SUPERSEDED),
    ([control(), control(state="cancelled")], rb.R_CONTROL_CONFLICT),
    ([control(), control(observed_utc="2026-10-01T18:00:01Z")], rb.R_CONTROL_CONFLICT),
])
def test_a_cancelled_superseded_or_contradictory_current_control_refuses(controls, reason):
    refused(reason, None, Ports(controls=controls))


# --- canonical copies: duplicates, conflicts, absent -----------------------------------------------------

def test_no_canonical_copy_is_absent():
    ports = Ports(copies=[])
    refused(rb.R_ABSENT, None, ports)
    assert ports.calls == [("lookup", RID)]


@pytest.mark.parametrize("copies", [
    [request(message="changed")],                                       # tampered singleton in the log
    [request(), request(message="changed")],                            # conflicting duplicate
    [request(), request(request_digest="cd" * 32)],                     # stored digest differs
    [request(), request(payload={"task_revision": REVISION, "result_fields": ["summary"], "x": 1})],
    [request(), request(pid=None, status="cancelled")],
    [request(), request(expected_responders={AGENT: dict(LABELS, run_id="r2")})],
    [request(payload={"task_revision": REVISION, "result_fields": ["summary"], "n": 0.0})],
    [request(), request(payload={"task_revision": REVISION, "result_fields": ["summary"]}, request_digest=None)],
    [request(), request(payload={"task_revision": REVISION, "result_fields": ["summary"], "request_digest": "cd" * 32})],
])
def test_any_canonical_copy_differing_in_content_or_stored_digest_is_a_conflict(copies):
    ports = Ports(copies=copies)
    refused(rb.R_CONFLICT, None, ports)
    assert ports.calls == [("lookup", RID)]                             # controls are not consulted after a conflict


@pytest.mark.parametrize("left, right", [(1, 1.0), (1, True), (0.0, -0.0), ("1", 1), ([1], [1, 1]),
                                         ({"a": 1}, {"a": 1, "b": 1}), ({"a": 1}, {"b": 1}), (None, "")])
def test_equality_is_exact_by_type_sign_and_shape(left, right):
    event = request(payload={"task_revision": REVISION, "v": left})
    other = request(payload={"task_revision": REVISION, "v": right})
    refused(rb.R_CONFLICT, event, Ports(copies=[other]))
    refused(rb.R_CONFLICT, other, Ports(copies=[event]))
    assert bind(event, Ports(copies=[copy.deepcopy(event)]))["request_id"] == RID


def test_an_absent_content_field_equals_an_explicit_null_like_the_bridge_contract():
    event = request(session_id=None)
    other = {k: v for k, v in request().items() if k != "session_id"}
    assert bind(event, Ports(copies=[other]))["request_id"] == RID


def test_ports_raising_propagate_unchanged():
    class Boom(Exception):
        pass

    def lookup(request_id):
        raise Boom()

    with pytest.raises(Boom):
        bind(canonical_lookup=lookup)
    with pytest.raises(KeyboardInterrupt):
        bind(current_controls=lambda task_id: (_ for _ in ()).throw(KeyboardInterrupt()))


# --- refresh rule ----------------------------------------------------------------------------------------

STORED = {"schema": rb.BINDING_SCHEMA, "request_id": RID, "request_digest": DIGEST, "task_revision": REVISION}


def test_refresh_absent_absent_is_none_and_identical_keeps_the_stored_record():
    assert rb.check_refresh(None, None) is None
    stored = dict(STORED)
    assert rb.check_refresh(stored, dict(STORED)) is stored


@pytest.mark.parametrize("stored, presented, reason", [
    (dict(STORED), None, rb.R_DROPPED),
    (None, dict(STORED), rb.R_RETROFIT),
    (dict(STORED), dict(STORED, request_id="other-request"), rb.R_CHANGED),
    (dict(STORED), dict(STORED, request_digest="cd" * 32), rb.R_CHANGED),
    (dict(STORED), dict(STORED, task_revision="v2"), rb.R_CHANGED),
    (dict(STORED, extra="x"), dict(STORED), rb.R_STORED),
    (dict(STORED, schema="wd.claim-request-binding.v2"), dict(STORED), rb.R_STORED),
    ({k: v for k, v in STORED.items() if k != "task_revision"}, dict(STORED), rb.R_STORED),
    (dict(STORED), dict(STORED, request_digest="AB" * 32), rb.R_PRESENTED),
    (dict(STORED), dict(STORED, task_revision=""), rb.R_PRESENTED),
    ("not a binding", None, rb.R_STORED),
    (dict(STORED), [], rb.R_PRESENTED),
])
def test_refresh_refusals(stored, presented, reason):
    with pytest.raises(rb.BindingRefused) as caught:
        rb.check_refresh(stored, presented)
    assert caught.value.reason == reason


@pytest.mark.parametrize("hostile", [
    lambda: HDict(STORED), lambda: dict(STORED, request_id=HStr(RID)), lambda: dict(STORED, task_revision=HStr(REVISION)),
    lambda: dict({HStr("schema"): rb.BINDING_SCHEMA}, request_id=RID, request_digest=DIGEST, task_revision=REVISION),
])
def test_refresh_never_runs_a_hook_on_a_hostile_stored_or_presented_binding(hostile):
    for stored, presented, reason in ((hostile(), dict(STORED), rb.R_STORED), (dict(STORED), hostile(), rb.R_PRESENTED)):
        probe = hostile()
        RECORD.clear()
        with pytest.raises(rb.BindingRefused) as caught:
            rb.check_refresh(stored, presented)
        assert caught.value.reason == reason
        assert rb.validate_binding(probe) is None and RECORD == []


def test_a_produced_binding_round_trips_through_json_and_refreshes_identically():
    binding = bind()
    assert rb.check_refresh(json.loads(json.dumps(binding)), binding) == binding


# --- purity ----------------------------------------------------------------------------------------------

def test_the_module_reads_no_file_clock_environment_process_or_shared_reader():
    source = Path(rb.__file__).read_text(encoding="utf-8")
    for forbidden in ("open(", "import os", "os.", "datetime", "time.", "os.environ", "getenv", "subprocess", "socket",
                      "urllib", "Read-AgentBridge", "wd_routing_reader", "pathlib", "hashlib", "sha256(",
                      "identity_verified =", "\"identity_verified\":"):
        assert forbidden not in source, forbidden
