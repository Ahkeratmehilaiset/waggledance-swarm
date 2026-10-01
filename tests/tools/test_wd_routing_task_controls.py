# SPDX-License-Identifier: BUSL-1.1
"""F26 S6 P2 task-control producer: synthetic S1 results only (no S1/S6 import, no file, no clock)."""
from __future__ import annotations

import ast
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import tools.wd_routing_task_controls as tc  # noqa: E402

NOW = datetime(2026, 10, 1, 19, 0, tzinfo=timezone.utc)
TASK, OTHER = "codex-lead-1/t", "codex-lead-1/u"
COMPLETE = {"schema": tc.CANCELLATION_SCHEMA, "complete": True, "cancelled": []}


def dispatch(dispatch_id="req-1", task=TASK, revision="r1", ts="2026-10-01T18:00:00+00:00", digest="d" * 64):
    return {"schema": tc.DISPATCH_SCHEMA, "dispatch_id": dispatch_id, "task_id": task, "revision": revision,
            "input_digest": "a" * 64, "scope": ["tools/x.py"], "dispatch_key": "b" * 64, "worker": "claude-rco-2",
            "requester": "codex-lead-1", "request_digest": digest, "dispatched_utc": ts,
            "expected_responders": {"claude-rco-2": {"agent_uuid": "u", "session_id": "s", "run_id": "r"}}}


def rejection(request_id, reason, task=TASK, observed=None, superseded_by=None):
    return {"request_id": request_id, "reason": reason, "index": None, "superseded_by": superseded_by,
            "task_id": task, "observed_utc": observed}


def result(dispatches=(), rejected=(), duplicates=()):
    return {"dispatches": list(dispatches), "rejected": list(rejected), "duplicates_ignored": list(duplicates)}


def run(res, cancellations=COMPLETE, now=NOW):
    return tc.task_controls(res, now, cancellations=cancellations)


def why(out):
    return [(w["task_id"], w["reason"]) for w in out["withheld"]]


# --- valid controls ----------------------------------------------------------------------------------------

def test_one_live_dispatch_gives_one_closed_live_control_with_verbatim_digest_and_time():
    out = run(result([dispatch(digest="e" * 64, ts="2026-10-01T21:00:00+03:00")]))
    assert out["controls"] == [{"schema": tc.CONTROL_SCHEMA, "task_id": TASK, "request_id": "req-1",
                                "request_digest": "e" * 64, "state": "live",
                                "observed_utc": "2026-10-01T21:00:00+03:00"}]
    assert tuple(out["controls"][0]) == tc.CONTROL_FIELDS and out["withheld"] == []
    assert out["coverage"] == {"schema": tc.COVERAGE_SCHEMA, "complete": True, "cancellation": "complete",
                               "reason": None}


def test_two_tasks_each_get_their_own_control():
    out = run(result([dispatch(), dispatch("req-2", task=OTHER)]))
    assert [(c["task_id"], c["request_id"]) for c in out["controls"]] == [(TASK, "req-1"), (OTHER, "req-2")]


def test_no_output_ever_claims_identity():
    out = run(result([dispatch()]))
    assert "identity_verified" not in repr(out)


# --- cancellation coverage: absence is unknown, never live authority ----------------------------------------

def test_without_a_cancellation_statement_every_task_is_withheld_and_coverage_is_unknown():
    out = run(result([dispatch()]), cancellations=None)
    assert out["controls"] == [] and why(out) == [(TASK, "cancellation_coverage_unknown")]
    assert out["coverage"]["cancellation"] == "unknown" and out["coverage"]["complete"] is False


@pytest.mark.parametrize("statement", [
    dict(COMPLETE, complete=False), dict(COMPLETE, complete=1), {"schema": tc.CANCELLATION_SCHEMA, "cancelled": []},
    dict(COMPLETE, schema="other"), dict(COMPLETE, cancelled=[{"task_id": TASK}]), dict(COMPLETE, extra=1),
    dict(COMPLETE, cancelled=[{"task_id": TASK, "request_id": "req-1", "request_digest": "D" * 64}]),
])
def test_an_incomplete_or_malformed_cancellation_statement_is_unknown(statement):
    out = run(result([dispatch()]), cancellations=statement)
    assert out["controls"] == [] and why(out) == [(TASK, "cancellation_coverage_unknown")]


def test_a_complete_statement_naming_the_current_request_gives_a_cancelled_control():
    statement = dict(COMPLETE, cancelled=[{"task_id": TASK, "request_id": "req-1", "request_digest": "d" * 64}])
    (control,) = run(result([dispatch()]), cancellations=statement)["controls"]
    assert control["state"] == "cancelled"


def test_a_cancellation_naming_another_request_of_the_task_withholds_it():
    statement = dict(COMPLETE, cancelled=[{"task_id": TASK, "request_id": "req-9", "request_digest": "d" * 64}])
    out = run(result([dispatch()]), cancellations=statement)
    assert out["controls"] == [] and why(out) == [(TASK, "cancellation_mismatch")]


def test_a_cancellation_with_another_digest_for_the_same_id_withholds_it():
    statement = dict(COMPLETE, cancelled=[{"task_id": TASK, "request_id": "req-1", "request_digest": "f" * 64}])
    assert why(run(result([dispatch()]), cancellations=statement)) == [(TASK, "cancellation_mismatch")]


# --- D-F1: an old revision never becomes a live control --------------------------------------------------------

def test_a_task_s1_held_gets_no_control_even_if_a_dispatch_is_present():
    res = result([dispatch()], [rejection("req-1", tc.HELD_REASON, observed="2026-10-01T18:00:00+00:00")])
    out = run(res)
    assert out["controls"] == [] and why(out) == [(TASK, "held_conflict")]


def test_a_superseded_record_newer_than_the_live_dispatch_withholds_the_task():
    res = result([dispatch()], [rejection("req-2", tc.SUPERSEDED_REASON, observed="2026-10-01T18:30:00+00:00",
                                          superseded_by="req-1")])
    assert why(run(res)) == [(TASK, "supersession_inconsistent")]


def test_an_older_superseded_revision_is_the_normal_case_and_the_newer_dispatch_is_live():
    res = result([dispatch("req-2", revision="r2")],
                 [rejection("req-1", tc.SUPERSEDED_REASON, observed="2026-10-01T17:00:00+00:00", superseded_by="req-2")])
    assert [(c["request_id"], c["state"]) for c in run(res)["controls"]] == [("req-2", "live")]


def test_a_superseded_record_with_unknown_time_withholds_the_task():
    res = result([dispatch()], [rejection("req-2", tc.SUPERSEDED_REASON, observed=None, superseded_by="req-1")])
    assert why(run(res)) == [(TASK, "supersession_inconsistent")]


@pytest.mark.parametrize("superseded_by", ["req-ghost", None])
def test_p2_1_a_superseded_record_that_names_another_or_no_winner_withholds_the_task(superseded_by):
    # RCO1 P2-1: S1 always names the live winner; a record naming a ghost or no winner contradicts it.
    res = result([dispatch("req-2", revision="r2")],
                 [rejection("req-1", tc.SUPERSEDED_REASON, observed="2026-10-01T17:00:00+00:00",
                            superseded_by=superseded_by)])
    out = run(res)
    assert out["controls"] == [] and why(out) == [(TASK, "supersession_inconsistent")]
    assert out["coverage"]["complete"] is False


def test_p2_2_an_unattributable_hold_withholds_every_task_and_coverage_is_incomplete():
    # RCO1 P2-2: a hold whose task cannot be read might be any task's hold, so nothing is current.
    res = result([dispatch(), dispatch("req-2", task=OTHER)], [rejection(None, tc.HELD_REASON, task=None)])
    out = run(res)
    assert out["controls"] == [] and why(out) == [(TASK, "held_unattributed"), (OTHER, "held_unattributed")]
    assert out["coverage"]["complete"] is False


def test_ordinary_rejections_naming_the_task_do_not_withhold():
    res = result([dispatch()], [rejection("evt-1", "not_a_wake_request", observed="2026-10-01T18:30:00+00:00"),
                                rejection(None, "request_malformed", task=None)])
    assert [c["state"] for c in run(res)["controls"]] == ["live"]


# --- copies, ambiguity, time --------------------------------------------------------------------------------

def test_identical_copies_of_one_dispatch_give_one_control():
    out = run(result([dispatch(), dispatch()], duplicates=[{"request_id": "req-1", "copies_ignored": 1}]))
    assert len(out["controls"]) == 1


def test_differing_copies_of_one_dispatch_id_withhold_the_task():
    out = run(result([dispatch(), dispatch(digest="f" * 64)]))
    assert out["controls"] == [] and why(out) == [(TASK, "dispatch_conflict")]


def test_two_live_dispatches_for_one_task_are_ambiguous():
    out = run(result([dispatch(), dispatch("req-2", revision="r2")]))
    assert out["controls"] == [] and why(out) == [(TASK, "dispatch_ambiguous")]


def test_a_dispatch_after_now_is_withheld_and_exactly_now_is_live():
    assert why(run(result([dispatch(ts="2026-10-01T19:00:00.000001+00:00")]))) == [(TASK, "future_dated")]
    assert run(result([dispatch(ts="2026-10-01T19:00:00+00:00")]))["controls"][0]["state"] == "live"


@pytest.mark.parametrize("now", [datetime(2026, 10, 1), "2026-10-01T19:00:00Z", None,
                                 datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=5)))])
def test_now_must_be_an_aware_representable_datetime(now):
    with pytest.raises(ValueError):
        tc.task_controls(result([dispatch()]), now, cancellations=COMPLETE)


# --- strictness before any hook ------------------------------------------------------------------------------

class Recording(str):
    calls: list = []

    def __eq__(self, other):
        Recording.calls.append("eq")
        return str.__eq__(self, other)

    def __hash__(self):
        Recording.calls.append("hash")
        return str.__hash__(self)


class RecordingDict(dict):
    calls: list = []

    def __getitem__(self, key):
        RecordingDict.calls.append("get")
        return dict.__getitem__(self, key)


def _spoil(field, value):
    record = dispatch()
    record[field] = value
    return record


@pytest.mark.parametrize("res", [
    result([_spoil("dispatch_id", Recording("req-1"))]),
    result([_spoil("request_digest", Recording("d" * 64))]),
    result([RecordingDict(dispatch())]),
    RecordingDict(result([dispatch()])),
    result([dict(dispatch(), extra=1)]),
    result([{k: v for k, v in dispatch().items() if k != "revision"}]),
    result([_spoil("schema", "wd.routing-dispatch.v2")]),
    result([_spoil("requester", "fable-5")]),
    result([_spoil("request_digest", "D" * 64)]),
    result([_spoil("dispatched_utc", "2026-10-01T18:00:00")]),
    result([_spoil("scope", [float("nan")])]),
    result([dispatch()], [dict(rejection("x", "r"), extra=1)]),
    result([dispatch()], duplicates=[{"request_id": "req-1", "copies_ignored": 0}]),
    {"dispatches": [dispatch()], "rejected": []},
    [dispatch()],
])
def test_any_malformed_part_refuses_the_whole_result_without_running_hooks(res):
    Recording.calls, RecordingDict.calls = [], []
    out = run(res)
    assert out["controls"] == [] and out["withheld"] == []
    assert out["coverage"] == {"schema": tc.COVERAGE_SCHEMA, "complete": False, "cancellation": "unknown",
                               "reason": "input_malformed"}
    assert Recording.calls == [] and RecordingDict.calls == []


def test_the_module_reads_no_clock_environment_file_process_or_routing_module():
    tree = ast.parse(Path(tc.__file__).read_text(encoding="utf-8"))
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
                for alias in node.names} | {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not imported & {"os", "subprocess", "socket", "time", "pathlib", "tools.wd_routing_dispatch",
                           "tools.wd_routing_associations", "tools.wd_routing_attempts"}
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not names & {"now", "utcnow", "today", "environ", "getenv"}
