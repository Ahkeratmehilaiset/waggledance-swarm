# SPDX-License-Identifier: BUSL-1.1
"""F26 S1: wd.routing-dispatch.v1 from Lead's wake_requests (pure ``dispatches(requests, now)``).

The request fixture has the live writer's shape (Write-AgentEvent wake_request: top-level request_id,
request_digest, to, expected_responders, write_scope, payload.task_revision and payload.result_contract).
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import wd_routing_dispatch as module  # noqa: E402
from tools.wd_composer_select import digest  # noqa: E402
from tools.wd_routing_dispatch import DISPATCH_FIELDS, SCHEMA, dispatches  # noqa: E402
from tools.wd_task_router import dispatch_key, normalize_scope  # noqa: E402

NOW = datetime(2026, 10, 1, 16, 30, tzinfo=timezone.utc)
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def request(request_id="2eacadad-133c-471a-85f1-f35dd0d27e10", *, task="codex-lead-1/f26-s1",
            revision="r1", ts="2026-10-01T16:25:52.0184267Z", worker="claude-rco-1",
            scope=("tools/wd_routing_dispatch.py", "tests/tools/test_wd_routing_dispatch.py"),
            request_digest=DIGEST_A, message="Implement S1.") -> dict:
    return {
        "ts_utc": ts, "agent": "codex-lead-1", "type": "wake_request", "task_id": task, "status": "assigned",
        "to": worker, "message": message, "write_scope": list(scope),
        "payload": {"task_revision": revision,
                    "result_contract": {"required": ["summary", "head"], "schema": "wd.task-result-contract.v1"},
                    "result_fields": ["summary", "head"]},
        "request_id": request_id, "request_digest": request_digest,
        "expected_responders": {worker: {"agent_uuid": "2b2f6ff9-06c2-4ec8-b526-f10071ce7103",
                                         "session_id": "wd-reboot-20261001T101817Z",
                                         "run_id": "wd-reboot-20261001T101817Z"}},
        "agent_uuid": "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101", "session_id": "lead", "run_id": "lead",
    }


def reasons(result) -> list:
    return [(item["request_id"], item["reason"]) for item in result["rejected"]]


# --- the record ----------------------------------------------------------------------------------------

def test_a_live_shaped_request_gives_one_closed_dispatch_record():
    event = request()
    result = dispatches([event], NOW)
    assert result["rejected"] == [] and result["duplicates_ignored"] == []
    (record,) = result["dispatches"]
    assert tuple(record) == DISPATCH_FIELDS and record["schema"] == SCHEMA
    scope = normalize_scope(event["write_scope"])
    expected_input = digest({"task_id": "codex-lead-1/f26-s1", "revision": "r1", "message": "Implement S1.",
                             "result_contract": event["payload"]["result_contract"], "scope": scope})
    assert record == {
        "schema": SCHEMA, "dispatch_id": event["request_id"], "task_id": "codex-lead-1/f26-s1", "revision": "r1",
        "input_digest": expected_input, "scope": scope,
        "dispatch_key": dispatch_key("codex-lead-1/f26-s1", "r1", expected_input, scope),
        "worker": "claude-rco-1", "requester": "codex-lead-1", "request_digest": DIGEST_A,
        "dispatched_utc": "2026-10-01T16:25:52.018426+00:00",
        "expected_responders": event["expected_responders"],
    }


@pytest.mark.parametrize("value", ["0" * 64, "f" * 64, DIGEST_B])
def test_the_request_digest_is_copied_never_recomputed(value):
    # The PowerShell digest cannot be re-derived in Python; any well-formed value is carried as written.
    (record,) = dispatches([request(request_digest=value)], NOW)["dispatches"]
    assert record["request_digest"] == value


def test_an_offset_timestamp_is_reported_as_canonical_utc():
    (record,) = dispatches([request(ts="2026-10-01T19:25:52+03:00")], NOW)["dispatches"]
    assert record["dispatched_utc"] == "2026-10-01T16:25:52+00:00"


# --- required inputs: no defaults ----------------------------------------------------------------------

def test_a_request_without_task_revision_is_rejected_never_defaulted():
    event = request()
    del event["payload"]["task_revision"]
    result = dispatches([event], NOW)
    assert result["dispatches"] == [] and reasons(result) == [(event["request_id"], "revision_missing")]


@pytest.mark.parametrize("revision", ["", 1, None, ["r1"]])
def test_a_revision_must_be_a_nonempty_exact_string(revision):
    assert reasons(dispatches([request(revision=revision)], NOW))[0][1] == "revision_missing"


@pytest.mark.parametrize("to, reason", [
    ("claude-rco-1,claude-rco-2", "dispatch_target_ambiguous"),
    (["claude-rco-1", "fable-5"], "dispatch_target_ambiguous"),
    ("", "worker_invalid"), ("Claude RCO", "worker_invalid"), (None, "worker_invalid"), (["claude-rco-1", 2], "worker_invalid"),
])
def test_exactly_one_canonical_worker(to, reason):
    event = request()
    event["to"] = to
    assert reasons(dispatches([event], NOW)) == [(event["request_id"], reason)]


def test_a_one_item_worker_list_is_the_same_dispatch():
    event = request()
    event["to"] = ["claude-rco-1"]
    assert dispatches([event], NOW)["dispatches"][0]["worker"] == "claude-rco-1"


@pytest.mark.parametrize("requester", ["fable-5", "operator", "Codex-Lead-1", ""])
def test_only_lead_dispatches(requester):
    event = request()
    event["agent"] = requester
    assert reasons(dispatches([event], NOW)) == [(event["request_id"], "not_dispatch_authority")]


@pytest.mark.parametrize("value", ["A" * 64, "a" * 63, "g" * 64, None, 7])
def test_the_request_digest_must_be_64_lowercase_hex(value):
    assert reasons(dispatches([request(request_digest=value)], NOW))[0][1] == "request_digest_invalid"


@pytest.mark.parametrize("mutate", [
    lambda e: e.__setitem__("expected_responders", {}),
    lambda e: e["expected_responders"].__setitem__("claude-rco-2", dict(e["expected_responders"]["claude-rco-1"])),
    lambda e: e["expected_responders"]["claude-rco-1"].pop("run_id"),
    lambda e: e["expected_responders"]["claude-rco-1"].__setitem__("extra", "x"),
    lambda e: e["expected_responders"]["claude-rco-1"].__setitem__("session_id", ""),
    lambda e: e.__setitem__("expected_responders", {"fable-5": e["expected_responders"]["claude-rco-1"]}),
])
def test_expected_responders_are_one_closed_worker_binding(mutate):
    event = request()
    mutate(event)
    assert reasons(dispatches([event], NOW)) == [(event["request_id"], "expected_responders_invalid")]


@pytest.mark.parametrize("scope, reason", [([], "scope_missing"), (["tools/*.py"], "scope_invalid"),
                                           (["../x"], "scope_invalid"), ([3], "scope_invalid")])
def test_scope_goes_through_the_router_normalizer(scope, reason):
    assert reasons(dispatches([request(scope=scope)], NOW))[0][1] == reason


def test_a_payload_scope_is_used_only_when_it_agrees():
    event = request()
    event["payload"]["write_scope"] = list(reversed(event["write_scope"]))   # same list, other order
    assert reasons(dispatches([event], NOW)) == [(event["request_id"], "scope_conflict")]
    event["payload"]["write_scope"] = list(event["write_scope"])
    assert dispatches([event], NOW)["dispatches"]
    del event["write_scope"]
    assert dispatches([event], NOW)["dispatches"][0]["scope"] == normalize_scope(event["payload"]["write_scope"])


@pytest.mark.parametrize("field, value, reason", [
    ("type", "message", "not_a_wake_request"), ("task_id", "", "task_id_missing"),
    ("message", None, "message_invalid"), ("ts_utc", "2026-10-01T16:25:52", "timestamp_invalid"),
    ("ts_utc", 1700000000, "timestamp_invalid"), ("ts_utc", "2026-10-01T16:30:00.000001Z", "future_dated"),
])
def test_field_level_refusals(field, value, reason):
    event = request()
    event[field] = value
    assert reasons(dispatches([event], NOW)) == [(event["request_id"], reason)]


def test_a_request_exactly_at_now_is_not_future_dated():
    assert dispatches([request(ts="2026-10-01T16:30:00Z")], NOW)["dispatches"]


# --- strict JSON before anything else ------------------------------------------------------------------

class Liar(str):
    def __eq__(self, other):
        return True

    __hash__ = str.__hash__


class Boom(dict):
    def __iter__(self):
        raise KeyboardInterrupt("a hostile hook must never run")

    def items(self):
        raise KeyboardInterrupt("a hostile hook must never run")


@pytest.mark.parametrize("mutate", [
    lambda e: e.__setitem__("agent", Liar("someone-else")),
    lambda e: e.__setitem__("to", Liar("claude-rco-1")),
    lambda e: e["payload"].__setitem__("task_revision", Liar("r1")),
    lambda e: e.__setitem__("payload", Boom(e["payload"])),
    lambda e: e.__setitem__(Liar("extra"), 1),
    lambda e: e.__setitem__("write_scope", tuple(e["write_scope"])),
    lambda e: e["payload"].__setitem__("result_contract", {"bound": float("nan")}),
    lambda e: e["payload"].__setitem__("result_contract", {"bound": float("inf")}),
    lambda e: e.__setitem__("blob", b"bytes"),
    lambda e: e.__setitem__("big", 10 ** 5000),
])
def test_hostile_or_non_json_values_are_malformed_and_never_run_hooks(mutate):
    event = request()
    mutate(event)
    result = dispatches([event], NOW)
    assert result["dispatches"] == [] and [(r["reason"], r["index"]) for r in result["rejected"]] == [
        ("request_malformed", 0)]


@pytest.mark.parametrize("value, strict", [
    ({"x": 1.5, "y": [True, None, "s", 3]}, True),
    ({"x": float("nan")}, False), ({"x": [float("inf")]}, False), ({"x": float("-inf")}, False),
    ({Liar("k"): 1}, False), ([Liar("v")], False), ({"x": (1,)}, False), (Boom(), False),
])
def test_the_strict_json_gate_itself(value, strict):
    # Pinned on its own: the later canonical digest also refuses NaN, so this gate is checked directly.
    assert module._strict_json(value) is strict


def test_a_hostile_request_object_is_malformed_without_running_its_hooks():
    result = dispatches([Boom(request())], NOW)
    assert [(r["reason"], r["index"]) for r in result["rejected"]] == [("request_malformed", 0)]


def test_deep_nesting_is_refused_not_a_recursion_error():
    deep: object = "x"
    for _ in range(200):
        deep = [deep]
    event = request()
    event["payload"]["result_contract"] = deep
    assert reasons(dispatches([event], NOW))[0][1] == "request_malformed"


@pytest.mark.parametrize("request_id", ["", "x" * 129, "has space", "ünicode", None, 5])
def test_request_id_shape(request_id):
    result = dispatches([request(request_id=request_id)], NOW)
    assert [(r["request_id"], r["reason"], r["index"]) for r in result["rejected"]] == [(None, "request_id_invalid", 0)]


# --- duplicates and conflicts --------------------------------------------------------------------------

def test_an_exact_repeat_dedupes_to_one_record():
    event = request()
    result = dispatches([event, copy.deepcopy(event), copy.deepcopy(event)], NOW)
    assert len(result["dispatches"]) == 1
    assert result["duplicates_ignored"] == [{"request_id": event["request_id"], "copies_ignored": 2}]


@pytest.mark.parametrize("change", [
    lambda e: e.__setitem__("request_digest", DIGEST_B),
    lambda e: e.__setitem__("message", "Implement S1 differently."),
    lambda e: e["payload"].__setitem__("task_revision", "r2"),
])
def test_same_id_other_content_poisons_every_copy_including_the_valid_one(change):
    good = request()
    other = copy.deepcopy(good)
    change(other)
    for order in ([good, other], [other, good], [good, good, other]):
        result = dispatches(order, NOW)
        assert result["dispatches"] == []
        assert reasons(result) == [(good["request_id"], "request_binding_conflict")]


@pytest.mark.parametrize("spoil", [
    lambda e: e["payload"].__setitem__("result_contract", {"x": float("nan")}),
    lambda e: e.__setitem__("write_scope", tuple(e["write_scope"])),
    lambda e: e.__setitem__(Liar("extra"), 1),
])
def test_a_malformed_copy_of_a_valid_id_poisons_the_id(spoil):
    good = request()
    bad = copy.deepcopy(good)
    spoil(bad)
    for order in ([good, bad], [bad, good]):
        result = dispatches(order, NOW)
        assert result["dispatches"] == []
        assert ("2eacadad-133c-471a-85f1-f35dd0d27e10", "request_binding_conflict") in reasons(result)
        assert (None, "request_malformed") in reasons(result)


def test_a_malformed_dict_with_a_hostile_key_runs_no_hook_while_its_id_is_read():
    class Tripwire(str):
        def __eq__(self, other):
            raise KeyboardInterrupt("hook ran")

        __hash__ = str.__hash__

    bad = request()
    del bad["request_id"]
    bad[Tripwire("request_id")] = "other"   # the only id-like key: a lookup of "request_id" would call __eq__
    result = dispatches([bad], NOW)
    assert [(r["reason"], r["index"]) for r in result["rejected"]] == [("request_malformed", 0)]


def test_a_poisoned_id_does_not_touch_another_valid_id():
    bad, bad2, fine = request("id-1"), request("id-1", request_digest=DIGEST_B), request("id-2", task="other/task")
    result = dispatches([bad, fine, bad2], NOW)
    assert [r["dispatch_id"] for r in result["dispatches"]] == ["id-2"]


# --- revisions -----------------------------------------------------------------------------------------

def test_the_newest_revision_wins_whatever_the_arrival_order():
    old = request("id-old", revision="r1", ts="2026-10-01T16:00:00Z")
    new = request("id-new", revision="r2", ts="2026-10-01T16:10:00Z")
    for order in ([old, new], [new, old]):
        result = dispatches(order, NOW)
        assert [r["dispatch_id"] for r in result["dispatches"]] == ["id-new"]
        assert result["rejected"] == [{"request_id": "id-old", "reason": "superseded_revision", "index": None,
                                       "superseded_by": "id-new", "task_id": "codex-lead-1/f26-s1",
                                       "observed_utc": "2026-10-01T16:00:00+00:00"}]


def test_a_revision_change_changes_the_dispatch_key():
    one = dispatches([request("id-1", revision="r1")], NOW)["dispatches"][0]
    two = dispatches([request("id-2", revision="r2")], NOW)["dispatches"][0]
    assert one["dispatch_key"] != two["dispatch_key"] and one["input_digest"] != two["input_digest"]


def test_equal_instants_break_ties_by_dispatch_id_and_compare_as_instants_not_text():
    a = request("id-a", ts="2026-10-01T19:00:00+03:00")
    b = request("id-b", ts="2026-10-01T16:00:00Z")          # the same instant written differently
    c = request("id-c", ts="2026-10-01T16:00:00.5+00:00")   # half a second later
    assert [r["dispatch_id"] for r in dispatches([a, b], NOW)["dispatches"]] == ["id-b"]
    assert [r["dispatch_id"] for r in dispatches([c, a, b], NOW)["dispatches"]] == ["id-c"]


def test_distinct_tasks_never_supersede_each_other():
    result = dispatches([request("id-1", task="t/one"), request("id-2", task="t/two")], NOW)
    assert [r["dispatch_id"] for r in result["dispatches"]] == ["id-1", "id-2"] and result["rejected"] == []


def test_the_output_does_not_depend_on_input_order():
    events = [request("id-1", task="t/one", ts="2026-10-01T16:00:00Z"),
              request("id-2", task="t/one", ts="2026-10-01T16:05:00Z"),
              request("id-3", task="t/two"), request("id-4", request_digest=DIGEST_B, task="t/three"),
              request("id-4", task="t/three")]
    first = dispatches(events, NOW)
    assert dispatches(list(reversed(events)), NOW) == first


# --- caller contract, cancellation, purity -------------------------------------------------------------

@pytest.mark.parametrize("now", [datetime(2026, 10, 1, 16, 30), "2026-10-01T16:30:00Z", None])
def test_now_must_be_an_explicit_offset_aware_datetime(now):
    with pytest.raises(ValueError):
        dispatches([request()], now)


@pytest.mark.parametrize("requests", [None, (request(),), {"a": request()}])
def test_requests_must_be_a_list(requests):
    with pytest.raises(ValueError):
        dispatches(requests, NOW)


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_cancellation_inside_a_helper_propagates(monkeypatch, interrupt):
    def cancelled(raw):
        raise interrupt()
    monkeypatch.setattr(module, "normalize_scope", cancelled)
    with pytest.raises(interrupt):
        dispatches([request()], NOW)


def test_an_unexpected_helper_error_propagates_instead_of_becoming_a_rejection(monkeypatch):
    def broken(raw):
        raise RuntimeError("bug")
    monkeypatch.setattr(module, "normalize_scope", broken)
    with pytest.raises(RuntimeError):
        dispatches([request()], NOW)


def test_the_input_is_not_mutated():
    events = [request("id-1"), request("id-1"), request("id-2", task="t/two")]
    snapshot = json.dumps(events, sort_keys=True)
    dispatches(events, NOW)
    assert json.dumps(events, sort_keys=True) == snapshot


def test_the_module_reads_no_clock_file_environment_or_process():
    source = Path(module.__file__).read_text(encoding="utf-8")
    for forbidden in ("datetime.now", "utcnow", "time.time", "open(", "os.environ", "subprocess", "Path(",
                      "import os", "getenv"):
        assert forbidden not in source, forbidden


# --- D-F1: a rejected newer request never revives an older revision (RCO2 f0e55; Lead 17:11Z) ----------

OLD_TS, NEW_TS = "2026-10-01T16:00:00Z", "2026-10-01T16:20:00Z"


def held(result, task="codex-lead-1/f26-s1") -> list:
    pairs = [(r["request_id"], r["reason"]) for r in result["rejected"] if r["task_id"] == task]
    return sorted(pairs, key=lambda pair: (pair[0] is not None, pair[0] or "", pair[1]))


def test_df1_r1_a_poisoned_newer_revision_holds_the_task_instead_of_reviving_the_older_one():
    old, new = request("id-old", revision="r1", ts=OLD_TS), request("id-new", revision="r2", ts=NEW_TS)
    nan_copy = copy.deepcopy(new)
    nan_copy["payload"]["bound"] = float("nan")
    result = dispatches([old, new, nan_copy], NOW)
    assert result["dispatches"] == []
    assert held(result) == [(None, "request_malformed"), ("id-new", "request_binding_conflict"),
                            ("id-old", "dispatch_held_newer_rejection")]


def test_df1_r2_a_newer_request_without_revision_holds_the_task():
    old, new = request("id-old", revision="r1", ts=OLD_TS), request("id-new", ts=NEW_TS)
    del new["payload"]["task_revision"]
    result = dispatches([old, new], NOW)
    assert result["dispatches"] == []
    assert held(result) == [("id-new", "revision_missing"), ("id-old", "dispatch_held_newer_rejection")]


def test_df1_r3_a_future_dated_newer_request_holds_the_task():
    old = request("id-old", revision="r1", ts=OLD_TS)
    new = request("id-new", revision="r2", ts="2026-10-01T16:30:01Z")
    result = dispatches([old, new], NOW)
    assert result["dispatches"] == []
    assert held(result) == [("id-new", "future_dated"), ("id-old", "dispatch_held_newer_rejection")]


def test_df1_r4_rejections_carry_the_readable_task_id_and_time():
    new = request("id-new", ts=NEW_TS)
    del new["payload"]["task_revision"]
    (rejection,) = dispatches([new], NOW)["rejected"]
    assert rejection == {"request_id": "id-new", "reason": "revision_missing", "index": None, "superseded_by": None,
                         "task_id": "codex-lead-1/f26-s1", "observed_utc": "2026-10-01T16:20:00+00:00"}


def test_df1_a_newer_rejection_at_an_unknown_time_holds_the_task():
    old, new = request("id-old", revision="r1", ts=OLD_TS), request("id-new", revision="r2", ts="yesterday")
    result = dispatches([old, new], NOW)
    assert result["dispatches"] == []
    assert held(result) == [("id-new", "timestamp_invalid"), ("id-old", "dispatch_held_newer_rejection")]


def test_df1_a_rejection_at_the_same_instant_holds_the_task():
    old, new = request("id-old", revision="r1", ts=OLD_TS), request("id-new", ts="2026-10-01T19:00:00+03:00")
    del new["payload"]["task_revision"]
    assert dispatches([old, new], NOW)["dispatches"] == []


def test_df1_an_older_rejection_does_not_hold_the_newer_valid_revision():
    old, new = request("id-old", ts=OLD_TS), request("id-new", revision="r2", ts=NEW_TS)
    del old["payload"]["task_revision"]
    result = dispatches([old, new], NOW)
    assert [r["dispatch_id"] for r in result["dispatches"]] == ["id-new"]
    assert held(result) == [("id-old", "revision_missing")]


def test_df1_another_tasks_rejection_does_not_hold_this_task():
    old = request("id-old", revision="r1", ts=OLD_TS)
    other = request("id-other", task="codex-lead-1/other-task", ts=NEW_TS)
    del other["payload"]["task_revision"]
    assert [r["dispatch_id"] for r in dispatches([old, other], NOW)["dispatches"]] == ["id-old"]


def test_df1_a_plain_newer_revision_still_supersedes_without_a_hold():
    old, new = request("id-old", revision="r1", ts=OLD_TS), request("id-new", revision="r2", ts=NEW_TS)
    result = dispatches([old, new], NOW)
    assert [r["dispatch_id"] for r in result["dispatches"]] == ["id-new"]
    assert held(result) == [("id-old", "superseded_revision")]


def test_df1_a_malformed_copy_task_and_time_are_read_without_running_hooks():
    hostile = request("id-new", ts=NEW_TS)
    hostile["task_id"] = Liar("codex-lead-1/f26-s1")       # not an exact str: never read as the task
    hostile["payload"]["bound"] = float("nan")
    old = request("id-old", revision="r1", ts=OLD_TS)
    result = dispatches([old, hostile], NOW)
    # The id is poisoned, but the malformed copy names no exact task, so it cannot hold the task.
    assert [r["dispatch_id"] for r in result["dispatches"]] == ["id-old"]
    assert [(r["reason"], r["task_id"]) for r in result["rejected"]] == [("request_malformed", None)]


def test_df1_a_held_task_lists_every_older_valid_record_as_held_never_live():
    one, two = request("id-1", revision="r1", ts=OLD_TS), request("id-2", revision="r2", ts="2026-10-01T16:10:00Z")
    bad = request("id-3", ts=NEW_TS)
    del bad["payload"]["task_revision"]
    result = dispatches([one, two, bad], NOW)
    assert result["dispatches"] == []
    assert held(result) == [("id-1", "dispatch_held_newer_rejection"), ("id-2", "dispatch_held_newer_rejection"),
                            ("id-3", "revision_missing")]


def test_df1_a_lone_malformed_newer_request_holds_the_task():
    old = request("id-old", revision="r1", ts=OLD_TS)
    bad = request("id-bad", revision="r2", ts=NEW_TS)
    bad["payload"]["bound"] = float("inf")                 # its only copy: malformed, nothing else names the task
    result = dispatches([old, bad], NOW)
    assert result["dispatches"] == []
    assert held(result) == [(None, "request_malformed"), ("id-old", "dispatch_held_newer_rejection")]


def test_df1_a_conflict_between_two_strict_copies_alone_holds_the_task():
    old = request("id-old", revision="r1", ts=OLD_TS)
    one, two = request("id-new", revision="r2", ts=NEW_TS), request("id-new", revision="r2", ts=NEW_TS,
                                                                       message="Other text.")
    result = dispatches([old, one, two], NOW)
    assert result["dispatches"] == []
    assert held(result) == [("id-new", "request_binding_conflict"), ("id-old", "dispatch_held_newer_rejection")]


# --- L: only dispatch-authority wake_requests can hold a task (RCO2 1134 L; Lead decision a, 18:05Z) ------
# Ordinary claim/message/reply/done events and wake_requests from any other author are not task revisions:
# they stay visible as rejections but never freeze a legitimate dispatch. A label is never authority.

TASK = "codex-lead-1/f26-s1"
LATER = "2026-10-01T16:25:00Z"


def event(kind, agent="claude-rco-1", ts=LATER, **extra) -> dict:
    row = {"ts_utc": ts, "agent": agent, "type": kind, "task_id": TASK, "status": "active", "to": "codex-lead-1",
           "message": "ordinary event"}
    row.update(extra)
    return row


def test_l_a_mixed_canonical_event_stream_does_not_freeze_the_dispatch():
    lead = request("id-lead", revision="r1", ts=OLD_TS)
    stream = [lead,
              event("claim", status="active", write_scope=["tools/wd_routing_dispatch.py"]),
              event("message", status="progress"),
              event("message", status="answered", in_reply_to_request_id="id-lead",
                    in_reply_to_request_digest=DIGEST_A, payload={"result": {"summary": "x"}}),
              event("done", status="done"),
              event("handoff", status="handoff", request_id="ffff-1")]
    result = dispatches(stream, NOW)
    assert [r["dispatch_id"] for r in result["dispatches"]] == ["id-lead"]
    # The ordinary events stay visible as rejections; none of them is a hold.
    assert ("ffff-1", "not_a_wake_request") in reasons(result)
    assert all(r["reason"] != "dispatch_held_newer_rejection" for r in result["rejected"])


@pytest.mark.parametrize("kind", ["message", "status", "claim", "done", "review_request", "Wake_Request"])
def test_l_the_authority_labels_own_ordinary_events_do_not_hold(kind):
    # Lead posts progress/status/claim events on its own tasks: same author label, not a revision.
    lead = request("id-lead", revision="r1", ts=OLD_TS)
    own = event(kind, agent="codex-lead-1", request_id="dddd-1")
    result = dispatches([lead, own], NOW)
    assert [r["dispatch_id"] for r in result["dispatches"]] == ["id-lead"]
    assert held(result) == [("dddd-1", "not_a_wake_request")]


def test_l_rco2_l1_a_later_claim_event_with_a_request_id_does_not_hold():
    lead = request("aaaa-1", revision="r1", ts=OLD_TS)
    claim = event("claim", agent="claude-rco-1", request_id="eeee-9")
    result = dispatches([lead, claim], NOW)
    assert [r["dispatch_id"] for r in result["dispatches"]] == ["aaaa-1"]
    assert held(result) == [("eeee-9", "not_a_wake_request")]


@pytest.mark.parametrize("author", ["fable-5", "claude-rco-2", "operator", "system", "Codex-Lead-1", "codex-lead-2"])
def test_l_rco2_l2_a_wake_request_from_any_other_author_does_not_freeze_the_task(author):
    lead = request("id-lead", revision="r1", ts=OLD_TS)
    forged = request("id-forged", revision="r2", ts=NEW_TS)
    forged["agent"] = author
    result = dispatches([lead, forged], NOW)
    assert [r["dispatch_id"] for r in result["dispatches"]] == ["id-lead"]
    assert held(result) == [("id-forged", "not_dispatch_authority")]


@pytest.mark.parametrize("spoil", [
    lambda e: e.update(bound=float("nan")),          # malformed
    lambda e: e.update(request_id="not a valid id!"),  # invalid id
    lambda e: e.pop("request_id", None),             # no id at all (every real claim event)
])
def test_l_a_malformed_or_idless_ordinary_event_does_not_hold(spoil):
    lead = request("id-lead", revision="r1", ts=OLD_TS)
    ordinary = event("claim", request_id="eeee-9")
    spoil(ordinary)
    result = dispatches([lead, ordinary], NOW)
    assert [r["dispatch_id"] for r in result["dispatches"]] == ["id-lead"]


def test_l_conflicting_copies_of_an_ordinary_event_id_do_not_hold():
    lead = request("id-lead", revision="r1", ts=OLD_TS)
    one, two = event("message", request_id="eeee-9"), event("message", request_id="eeee-9", message="other")
    result = dispatches([lead, one, two], NOW)
    assert [r["dispatch_id"] for r in result["dispatches"]] == ["id-lead"]
    assert held(result) == [("eeee-9", "request_binding_conflict")]


def test_l_a_malformed_authority_request_still_holds_and_so_does_one_whose_author_or_type_is_unreadable():
    lead = request("id-lead", revision="r1", ts=OLD_TS)
    for spoil in (lambda e: e.pop("agent"), lambda e: e.update(agent=7), lambda e: e.pop("type"),
                  lambda e: e.update(type=["wake_request"]), lambda e: e.update(agent=Liar("codex-lead-1"))):
        newer = request("id-new", revision="r2", ts=NEW_TS)
        spoil(newer)
        newer["payload"]["bound"] = float("nan")
        result = dispatches([lead, newer], NOW)
        assert result["dispatches"] == [], newer


def test_h6_rco2_killer_a_newer_authority_request_with_an_invalid_id_holds_the_task():
    old = request("id-old", revision="r1", ts=OLD_TS)
    bad = request("not a valid id!", revision="r2", ts=NEW_TS)
    result = dispatches([old, bad], NOW)
    assert result["dispatches"] == []
    assert held(result) == [(None, "request_id_invalid"), ("id-old", "dispatch_held_newer_rejection")]


def test_l_an_older_ordinary_event_and_a_newer_one_never_change_the_twelve_fields():
    lead = request("id-lead", revision="r1", ts=OLD_TS)
    (alone,) = dispatches([lead], NOW)["dispatches"]
    (mixed,) = dispatches([event("claim", ts="2026-10-01T15:00:00Z"), lead, event("message")], NOW)["dispatches"]
    assert mixed == alone and tuple(mixed) == DISPATCH_FIELDS


# --- D-F2: one explicit worker, no empty pieces ------------------------------------------------------------

@pytest.mark.parametrize("to", ["claude-rco-1,", ",claude-rco-1", " claude-rco-1", "claude-rco-1 ",
                                ["claude-rco-1", ""], ["claude-rco-1 "], []])
def test_df2_empty_or_padded_worker_pieces_are_refused(to):
    event = request()
    event["to"] = to
    assert reasons(dispatches([event], NOW)) == [(event["request_id"], "worker_invalid")]


# --- D-F3: now at the datetime range end is the documented ValueError ---------------------------------------

@pytest.mark.parametrize("now", [datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=5))),
                                 datetime(9999, 12, 31, 23, 59, tzinfo=timezone(timedelta(hours=-5)))])
def test_df3_an_unrepresentable_now_is_a_value_error(now):
    with pytest.raises(ValueError):
        dispatches([], now)
