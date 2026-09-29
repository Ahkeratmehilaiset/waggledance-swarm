# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import ast
import copy
import hashlib
import io
import json
from pathlib import Path

import pytest

import tools.bridge_continuity_guard as guard
from tools.bridge_continuity_guard import evaluate

LEAD = "codex-lead-1"
TOOLS = "codex-tools-1"
RCO2 = "claude-rco-2"
RCO1 = "claude-rco-1"
PKG_TASK = "codex-lead-1/bridge-v2-whole-package-20260928"
REVIEW_TASK = "codex-lead-1/bridge-v2-package-closure-diagnostic-review-20260928"
HOTPATH_TASK = "codex-lead-1/bridge-v2-hotpath-diagnostic-evidence-20260929"
REQ = "615b5fca-49d3-4899-857b-7e1fb876b9e8"
HEAD = "a" * 40
NOW = "2026-09-29T00:00:00Z"


def sha(label: object) -> str:
    return hashlib.sha256(str(label).encode()).hexdigest()


def cp(task=PKG_TASK, status="in_progress", next_wakeup=None,
       updated="2026-09-28T21:59:06Z") -> dict:
    return {"task_id": task, "status": status, "next_action": "await RCO2 full suite",
            "next_wakeup_utc": next_wakeup, "updated_at_utc": updated}


def snap(agent=LEAD, *, collected="2026-09-28T23:59:00Z", **over) -> dict:
    base = {
        "schema": "wd.continuity-snapshot.v1",
        "agent": agent,
        "evidence": {"complete": True, "scope": "canonical", "collected_at_utc": collected,
                     "source_digest": "d" * 64, "errors": []},
        "checkpoint": cp(),
        "claims": [], "inbound_requests": [], "waits": [], "events": [],
        "processing": [], "cancellations": [], "holds": [],
    }
    base.update(over)
    return base


def event(n=1, *, frm=RCO2, to=LEAD, task=REVIEW_TASK, type="message",
          status="full_suite_result", ts="2026-09-28T22:59:32.1625339Z",
          request_id=None, reply_to=None, head=None, informational=True,
          session_id=None, binding_valid=None, schema_valid=True) -> dict:
    if binding_valid is None:
        binding_valid = reply_to is not None
    return {"event_sha256": sha(n), "from_agent": frm, "to_agent": to, "task_id": task,
            "type": type, "status": status, "ts_utc": ts, "request_id": request_id,
            "in_reply_to_request_id": reply_to, "head": head,
            "informational": informational, "session_id": session_id,
            "binding_valid": binding_valid, "schema_valid": schema_valid}


def wait(wid="w1", *, responder=RCO2, session=None, head=None, deadline=None,
         declared="2026-09-28T21:58:00Z", waiter_task=PKG_TASK,
         dep_task=REVIEW_TASK, request_id=REQ) -> dict:
    return {"wait_id": wid, "waiter_task_id": waiter_task, "dependency_task_id": dep_task,
            "request_id": request_id, "request_digest": sha("digest-" + request_id),
            "expected_responder": {"agent": responder, "session_id": session},
            "head_sha": head, "deadline_utc": deadline, "declared_at_utc": declared}


def req(rid=REQ, *, frm=LEAD, task=PKG_TASK, ts="2026-09-28T23:00:00Z",
        deadline=None, n="req") -> dict:
    return {"request_id": rid, "request_digest": sha("digest-" + rid),
            "event_sha256": sha(n), "task_id": task, "from_agent": frm,
            "ts_utc": ts, "deadline_utc": deadline}


def proc(n, *, by=LEAD, kind="receipt", ts="2026-09-28T23:10:00Z") -> dict:
    return {"event_sha256": sha(n), "by_agent": by, "ts_utc": ts, "kind": kind}


def bound_reply(n=7, *, frm=RCO2, head=None, reply_to=REQ, session=None,
                ts="2026-09-28T22:59:32Z", type="message", binding_valid=True,
                schema_valid=True) -> dict:
    return event(n, frm=frm, reply_to=reply_to, head=head, informational=False,
                 session_id=session, ts=ts, type=type, status="answered",
                 binding_valid=binding_valid, schema_valid=schema_valid)


def items_of(decision, kind):
    return [i for i in decision["items"] if i["kind"] == kind]


def only(decision, kind):
    found = items_of(decision, kind)
    assert len(found) == 1, decision
    return found[0]


# --- the 2026-09-28 22:59Z incident, replayed ------------------------------------

def test_incident_replay_lead_unfinished_checkpoint_dispatches():
    d = evaluate(snap(events=[event()]), NOW)
    assert d["verdict"] == "dispatch"
    assert d["target"] == LEAD
    item = only(d, "checkpoint")
    assert item["verdict"] == "dispatch"
    assert "unfinished_no_wakeup" in item["reasons"]
    # the woken lane is told exactly which addressed event it never processed
    assert {"kind": "unprocessed_event", "id": sha(1), "task_id": REVIEW_TASK} in item["work_refs"]
    assert not items_of(d, "unbound_result")  # unrelated task: context only, no decision item
    assert d["authority"] == "none"


def test_incident_is_detected_exactly_at_the_checkpoint_stale_threshold():
    before = evaluate(snap(collected="2026-09-28T22:28:30Z"), "2026-09-28T22:29:05Z")
    at = evaluate(snap(collected="2026-09-28T22:28:30Z"), "2026-09-28T22:29:06Z")
    assert before["verdict"] == "wait"
    assert "checkpoint_recent" in only(before, "checkpoint")["reasons"]
    assert at["verdict"] == "dispatch"


def test_incident_related_unbound_result_is_decide_never_satisfied():
    d = evaluate(snap(checkpoint=cp(task=REVIEW_TASK), events=[event()]), NOW)
    unbound = only(d, "unbound_result")
    assert unbound["verdict"] == "decide"
    assert unbound["target"] == LEAD
    assert "unbound_result_candidate" in unbound["reasons"]
    assert only(d, "checkpoint")["verdict"] == "dispatch"
    assert d["verdict"] == "dispatch"
    assert not [i for i in d["items"] if i["verdict"] == "idle_ok"]


def test_incident_tools_waiting_on_a_different_task_dispatches():
    # Tools waited on RCO2's full suite under its own hotpath task; the result
    # was addressed to Lead only, so Tools' snapshot has no event at all.
    d = evaluate(snap(TOOLS, checkpoint=cp(task=HOTPATH_TASK, updated="2026-09-28T21:58:42Z")), NOW)
    assert d["verdict"] == "dispatch"
    assert d["target"] == TOOLS
    assert "unfinished_no_wakeup" in only(d, "checkpoint")["reasons"]


def test_historical_unbound_result_cannot_satisfy_a_new_bound_wait():
    d = evaluate(snap(waits=[wait()], events=[event()]), NOW)
    w = only(d, "wait")
    assert w["dependency_done"] is False
    assert w["processed"] is False
    assert w["verdict"] != "idle_ok"
    assert only(d, "unbound_result")["verdict"] == "decide"


# --- bound waits: dependency_done is separate from processed ------------------------

def test_bound_result_processed_by_subject_is_idle_ok():
    d = evaluate(snap(checkpoint=cp(status="done"), waits=[wait()],
                      events=[bound_reply()], processing=[proc(7)]), NOW)
    w = only(d, "wait")
    assert (w["dependency_done"], w["processed"], w["verdict"]) == (True, True, "idle_ok")
    assert d["verdict"] == "idle_ok"


def test_bound_result_unprocessed_after_grace_is_stall_waiter():
    d = evaluate(snap(waits=[wait()], events=[bound_reply()]), NOW)
    w = only(d, "wait")
    assert (w["dependency_done"], w["processed"]) == (True, False)
    assert w["verdict"] == "dispatch"
    assert w["target"] == LEAD
    assert "stall_waiter" in w["reasons"]


def test_bound_result_within_grace_waits_for_processing():
    d = evaluate(snap(waits=[wait()], events=[bound_reply(ts="2026-09-28T23:50:00Z")]), NOW)
    w = only(d, "wait")
    assert w["verdict"] == "wait"
    assert "dependency_done_processing_pending" in w["reasons"]


@pytest.mark.parametrize("record", [
    proc(7, by=TOOLS),             # processed by somebody else
    proc(8),                       # receipt for a different event
])
def test_processing_must_be_exact_subject_and_exact_event(record):
    d = evaluate(snap(waits=[wait()], events=[bound_reply()], processing=[record]), NOW)
    assert only(d, "wait")["processed"] is False
    assert only(d, "wait")["verdict"] == "dispatch"


def test_dependency_overdue_dispatches_the_responder():
    d = evaluate(snap(waits=[wait(deadline="2026-09-28T23:30:00Z")]), NOW)
    w = only(d, "wait")
    assert (w["verdict"], w["target"]) == ("dispatch", RCO2)
    assert "dependency_overdue" in w["reasons"]


def test_dependency_before_deadline_waits():
    d = evaluate(snap(waits=[wait(deadline="2026-09-29T01:00:00Z")]), NOW)
    assert only(d, "wait")["verdict"] == "wait"
    assert "dependency_pending" in only(d, "wait")["reasons"]


@pytest.mark.parametrize("declared", ["2026-09-28T23:59:00Z", "2026-09-28T21:00:00Z"])
def test_wait_without_deadline_is_unknown_never_silent(declared):
    # RCO2 FM2: a null deadline must escalate, not wait silently.
    d = evaluate(snap(waits=[wait(declared=declared)]), NOW)
    assert only(d, "wait")["verdict"] == "unknown"
    assert "wait_deadline_missing" in only(d, "wait")["reasons"]
    assert d["verdict"] == "unknown"
    assert d["escalation"] == "operator"
    assert d["items"]  # item-level unknown keeps the evidence for the operator


@pytest.mark.parametrize("kind", ["heartbeat", "liveness"])
def test_unrelated_heartbeat_from_responder_is_not_proof(kind):
    d = evaluate(snap(waits=[wait()], events=[bound_reply(type=kind)],
                      processing=[proc(7)]), NOW)
    assert only(d, "wait")["dependency_done"] is False
    assert not items_of(d, "unbound_result")


def test_reply_to_a_different_request_is_not_proof():
    d = evaluate(snap(waits=[wait()], events=[bound_reply(reply_to="other-request")]), NOW)
    assert only(d, "wait")["dependency_done"] is False


def test_reply_from_wrong_responder_is_not_proof():
    d = evaluate(snap(waits=[wait()], events=[bound_reply(frm=RCO1)]), NOW)
    assert only(d, "wait")["dependency_done"] is False


def test_reply_from_wrong_session_is_not_proof():
    d = evaluate(snap(waits=[wait(session="s-new")], events=[bound_reply(session="s-old")]), NOW)
    assert only(d, "wait")["dependency_done"] is False
    ok = evaluate(snap(waits=[wait(session="s-new")], events=[bound_reply(session="s-new")]), NOW)
    assert only(ok, "wait")["dependency_done"] is True


def test_reply_at_wrong_head_is_a_decision_not_completion():
    d = evaluate(snap(waits=[wait(head=HEAD, deadline="2026-09-29T01:00:00Z")],
                      events=[bound_reply(head="b" * 40)]), NOW)
    w = only(d, "wait")
    assert w["dependency_done"] is False
    assert w["verdict"] == "decide"
    assert "bound_reply_head_mismatch" in w["reasons"]
    right = evaluate(snap(waits=[wait(head=HEAD)], events=[bound_reply(head=HEAD)]), NOW)
    assert only(right, "wait")["dependency_done"] is True


def test_multiple_distinct_bound_results_need_a_decision():
    d = evaluate(snap(waits=[wait(deadline="2026-09-29T01:00:00Z")],
                      events=[bound_reply(7), bound_reply(9)]), NOW)
    assert only(d, "wait")["verdict"] == "decide"
    assert "multiple_bound_results" in only(d, "wait")["reasons"]


def test_self_wait_is_unknown():
    d = evaluate(snap(waits=[wait(responder=LEAD, deadline="2026-09-29T01:00:00Z")]), NOW)
    assert d["reasons"] == ["self_wait"]


# --- inbound requests the subject owes -----------------------------------------------

def test_unprocessed_inbound_request_dispatches_after_grace_only():
    fresh = evaluate(snap(TOOLS, checkpoint=cp(task=PKG_TASK, status="done"),
                          inbound_requests=[req(ts="2026-09-28T23:55:00Z")]), NOW)
    stale = evaluate(snap(TOOLS, checkpoint=cp(task=PKG_TASK, status="done"),
                          inbound_requests=[req()]), NOW)
    assert only(fresh, "request")["verdict"] == "wait"
    item = only(stale, "request")
    assert (item["verdict"], item["target"]) == ("dispatch", TOOLS)
    assert "unprocessed_request" in item["reasons"]


def test_received_request_waits_and_answered_request_is_idle():
    base = dict(checkpoint=cp(status="done"), inbound_requests=[req()])
    received = evaluate(snap(TOOLS, processing=[proc("req", by=TOOLS)], **base), NOW)
    answered = evaluate(snap(TOOLS, processing=[proc("req", by=TOOLS, kind="bound_reply")], **base), NOW)
    assert only(received, "request")["verdict"] == "wait"
    assert only(answered, "request")["verdict"] == "idle_ok"
    assert answered["verdict"] == "idle_ok"


def test_overdue_request_dispatches_even_when_received():
    d = evaluate(snap(TOOLS, checkpoint=cp(status="done"),
                      inbound_requests=[req(deadline="2026-09-28T23:30:00Z")],
                      processing=[proc("req", by=TOOLS)]), NOW)
    assert only(d, "request")["verdict"] == "dispatch"
    assert "request_overdue" in only(d, "request")["reasons"]


def test_received_request_without_deadline_decides_when_old():
    d = evaluate(snap(TOOLS, checkpoint=cp(status="done"),
                      inbound_requests=[req(ts="2026-09-28T21:00:00Z")],
                      processing=[proc("req", by=TOOLS)]), NOW)
    assert only(d, "request")["verdict"] == "decide"
    assert "unbounded_request" in only(d, "request")["reasons"]


# --- cancellation: exact and authorised only -----------------------------------------

def test_requester_can_cancel_its_request_exactly():
    d = evaluate(snap(TOOLS, checkpoint=cp(status="done"), inbound_requests=[req()],
                      cancellations=[{"target_kind": "request", "target_id": REQ,
                                      "by_agent": LEAD, "ts_utc": "2026-09-28T23:20:00Z"}]), NOW)
    assert only(d, "request")["verdict"] == "idle_ok"
    assert "cancelled" in only(d, "request")["reasons"]


def test_cancellation_by_a_non_owner_is_ignored():
    d = evaluate(snap(TOOLS, checkpoint=cp(status="done"), inbound_requests=[req()],
                      cancellations=[{"target_kind": "request", "target_id": REQ,
                                      "by_agent": RCO1, "ts_utc": "2026-09-28T23:20:00Z"}]), NOW)
    assert only(d, "request")["verdict"] == "dispatch"
    assert "cancellation_ignored" in d["reasons"]


def test_waiter_cancels_its_wait_but_responder_cannot():
    mine = {"target_kind": "wait", "target_id": "w1", "by_agent": LEAD, "ts_utc": "2026-09-28T23:20:00Z"}
    theirs = dict(mine, by_agent=RCO2)
    overdue = wait(deadline="2026-09-28T23:30:00Z")
    assert only(evaluate(snap(waits=[overdue], cancellations=[mine]), NOW), "wait")["verdict"] == "idle_ok"
    assert only(evaluate(snap(waits=[overdue], cancellations=[theirs]), NOW), "wait")["verdict"] == "dispatch"


def test_cancellation_of_an_unknown_id_does_not_widen_scope():
    d = evaluate(snap(TOOLS, checkpoint=cp(status="done"), inbound_requests=[req()],
                      cancellations=[{"target_kind": "request", "target_id": "someone-else",
                                      "by_agent": LEAD, "ts_utc": "2026-09-28T23:20:00Z"}]), NOW)
    assert only(d, "request")["verdict"] == "dispatch"
    assert "cancellation_ignored" in d["reasons"]


def test_task_wide_cancellation_is_rejected_as_unknown():
    d = evaluate(snap(cancellations=[{"target_kind": "task", "target_id": PKG_TASK,
                                      "by_agent": LEAD, "ts_utc": "2026-09-28T23:20:00Z"}]), NOW)
    assert d["verdict"] == "unknown"


# --- HOLD: scoped, immutable, never dispatches ----------------------------------------

def hold(task_ids=(), request_ids=(), active=True, hid="HOLD-1549"):
    return {"hold_id": hid, "task_ids": list(task_ids), "request_ids": list(request_ids),
            "active": active}


def test_held_overdue_wait_is_hold_not_dispatch():
    d = evaluate(snap(checkpoint=cp(status="done"), waits=[wait(deadline="2026-09-28T23:30:00Z")],
                      holds=[hold([REVIEW_TASK])]), NOW)
    assert only(d, "wait")["verdict"] == "hold"
    assert d["verdict"] == "hold"
    assert not [i for i in d["items"] if i["verdict"] == "dispatch"]


def test_cancellation_cannot_clear_a_hold():
    d = evaluate(snap(checkpoint=cp(status="done"), waits=[wait(deadline="2026-09-28T23:30:00Z")],
                      holds=[hold(request_ids=[REQ])],
                      cancellations=[{"target_kind": "wait", "target_id": "w1", "by_agent": LEAD,
                                      "ts_utc": "2026-09-28T23:20:00Z"}]), NOW)
    assert only(d, "wait")["verdict"] == "hold"


def test_held_checkpoint_never_dispatches():
    d = evaluate(snap(holds=[hold([PKG_TASK])]), NOW)
    assert only(d, "checkpoint")["verdict"] == "hold"
    assert d["verdict"] == "hold"


def test_inactive_or_other_scope_hold_does_not_apply():
    for h in (hold([PKG_TASK], active=False), hold(["another/task"])):
        assert evaluate(snap(holds=[h]), NOW)["verdict"] == "dispatch"


def test_dispatch_outranks_hold_across_items():
    d = evaluate(snap(waits=[wait(deadline="2026-09-28T23:30:00Z")],
                      holds=[hold(request_ids=[REQ])]), NOW)
    assert only(d, "wait")["verdict"] == "hold"
    assert d["verdict"] == "dispatch"
    assert d["target"] == LEAD


# --- checkpoints and claims ------------------------------------------------------------

@pytest.mark.parametrize("state,reason", [("expired", None), ("released", "stale_lease"),
                                          ("active", None)])
def test_claim_state_never_erases_unfinished_work(state, reason):
    d = evaluate(snap(claims=[{"task_id": PKG_TASK, "state": state, "release_reason": reason}]), NOW)
    assert d["verdict"] == "dispatch"


def test_checkpoint_wakeup_due_and_scheduled():
    due = evaluate(snap(checkpoint=cp(next_wakeup="2026-09-28T23:30:00Z", updated="2026-09-28T23:00:00Z")), NOW)
    later = evaluate(snap(checkpoint=cp(next_wakeup="2026-09-29T02:00:00Z")), NOW)
    assert "checkpoint_wakeup_due" in only(due, "checkpoint")["reasons"]
    assert due["verdict"] == "dispatch"
    assert later["verdict"] == "wait"


@pytest.mark.parametrize("status", ["done", "completed", "closed", "idle"])
def test_done_checkpoint_without_other_work_is_idle_ok(status):
    d = evaluate(snap(checkpoint=cp(status=status)), NOW)
    assert d["verdict"] == "idle_ok"
    assert d["target"] is None
    assert d["work_refs"] == [] and d["targets"] == []


@pytest.mark.parametrize("status", ["Done", "weird", "audit_replied", "in_progress"])
def test_non_terminal_or_unknown_status_is_unfinished(status):
    assert evaluate(snap(checkpoint=cp(status=status)), NOW)["verdict"] == "dispatch"


def test_checkpoint_covered_by_open_wait_is_not_dispatched():
    d = evaluate(snap(waits=[wait(deadline="2026-09-29T01:00:00Z")]), NOW)
    item = only(d, "checkpoint")
    assert item["verdict"] == "wait"
    assert "covered_by_open_work" in item["reasons"]
    assert d["verdict"] == "wait"


def test_satisfied_wait_no_longer_covers_a_stale_checkpoint():
    d = evaluate(snap(waits=[wait()], events=[bound_reply()], processing=[proc(7)]), NOW)
    assert only(d, "wait")["verdict"] == "idle_ok"
    assert only(d, "checkpoint")["verdict"] == "dispatch"


# --- missing / stale / malformed evidence is UNKNOWN, never idle ----------------------

def _without(key):
    s = snap(checkpoint=cp(status="done"))
    del s[key]
    return s


@pytest.mark.parametrize("bad", [
    snap(checkpoint=None),
    snap(checkpoint=cp(status="done"), evidence={"complete": False, "scope": "canonical", "collected_at_utc": "2026-09-28T23:59:00Z",
                                                  "source_digest": "d" * 64, "errors": []}),
    snap(checkpoint=cp(status="done"), evidence={"complete": True, "scope": "canonical", "collected_at_utc": "2026-09-28T23:59:00Z",
                                                  "source_digest": "d" * 64, "errors": ["reader RETRY"]}),
    snap(checkpoint=cp(status="done"), collected="2026-09-28T23:50:00Z"),
    snap(checkpoint=cp(status="done"), collected="2026-09-29T00:05:00Z"),
    snap(checkpoint=cp(status="done"), schema="wd.continuity-snapshot.v2"),
    snap("Codex Lead", checkpoint=cp(status="done")),
    snap(checkpoint=cp(status="done", updated="2026-09-28 21:59:06")),
    snap(checkpoint=cp(status="done", updated="2026-09-28T21:59:06")),
    snap(checkpoint=cp(status="done", updated="2026-13-28T21:59:06Z")),
    snap(checkpoint=cp(status="done"), events=[event(ts="yesterday")]),
    snap(checkpoint=cp(status="done"), events=[dict(event(), event_sha256="not-a-sha")]),
    snap(checkpoint=cp(status="done"), events=[dict(event(), informational="yes")]),
    snap(checkpoint=cp(status="done"), policy={"grace_seconds": 0}),
    snap(checkpoint=cp(status="done"), policy={"grace_seconds": True}),
    snap(checkpoint=cp(status="done"), policy={"grace_seconds": 10**9}),
    snap(checkpoint=cp(status="done"), policy={"surprise": 5}),
    snap(checkpoint=cp(status="done"), claims=[{"task_id": PKG_TASK, "state": "gone", "release_reason": None}]),
    snap(checkpoint=cp(status="done"), processing=[proc(1, kind="read")]),
    _without("events"),
    _without("holds"),
    _without("evidence"),
    [],
    "snapshot",
    None,
], ids=lambda v: repr(v)[:40])
def test_missing_or_malformed_evidence_is_unknown_never_idle(bad):
    d = evaluate(bad, NOW)
    assert d["verdict"] == "unknown"
    assert d["escalation"] == "operator"
    assert d["reasons"]
    assert d["items"] == []
    assert d["target"] is None


def test_missing_checkpoint_has_its_own_reason():
    assert evaluate(snap(checkpoint=None), NOW)["reasons"] == ["checkpoint_missing"]


@pytest.mark.parametrize("now", ["now", "2026-09-29T00:00:00", "", None, 1790000000])
def test_bad_now_is_unknown(now):
    assert evaluate(snap(checkpoint=cp(status="done")), now)["verdict"] == "unknown"


def test_prefixed_and_seven_digit_timestamps_are_accepted():
    d = evaluate(snap(checkpoint=cp(status="done", updated="utc:2026-09-28T21:59:06.1234567Z"),
                      collected="2026-09-28T23:59:00.7996643+00:00"), "utc:" + NOW)
    assert d["verdict"] == "idle_ok"


# --- duplicates ------------------------------------------------------------------------

def test_identical_duplicates_are_deduped():
    once = evaluate(snap(waits=[wait()], events=[bound_reply()]), NOW)
    twice = evaluate(snap(waits=[wait(), wait()], events=[bound_reply(), bound_reply()]), NOW)
    assert once == twice


@pytest.mark.parametrize("field,items", [
    ("events", [event(), dict(event(), status="changed")]),
    ("waits", [wait(), wait(deadline="2026-09-29T01:00:00Z")]),
    ("inbound_requests", [req(), req(task="other/task")]),
    ("holds", [hold([PKG_TASK]), hold([REVIEW_TASK])]),
])
def test_conflicting_duplicates_are_unknown(field, items):
    d = evaluate(snap(**{field: items}), NOW)
    assert d["verdict"] == "unknown"
    assert any(r.startswith("conflicting_duplicate") for r in d["reasons"])


# --- action keys, determinism, authority ----------------------------------------------

def test_action_key_is_stable_across_time_and_input_order():
    s = snap(waits=[wait("w1", deadline="2026-09-28T23:30:00Z"), wait("w2", request_id="r2")],
             events=[event(1), event(2, task="x/y")])
    a = evaluate(s, NOW)
    b = evaluate(s, "2026-09-29T00:02:00Z")
    shuffled = copy.deepcopy(s)
    for key in ("waits", "events"):
        shuffled[key].reverse()
    assert shuffled["waits"] != s["waits"]
    c = evaluate(shuffled, NOW)
    assert a["action_key"] == b["action_key"] == c["action_key"]
    assert [i["action_key"] for i in a["items"]] == [i["action_key"] for i in c["items"]]
    assert json.dumps(a, sort_keys=True) == json.dumps(c, sort_keys=True)


def test_action_key_changes_when_work_changes():
    a = evaluate(snap(), NOW)
    b = evaluate(snap(events=[event()]), NOW)
    c = evaluate(snap(agent=TOOLS), NOW)
    assert len({a["action_key"], b["action_key"], c["action_key"]}) == 3
    keys = [i["action_key"] for i in b["items"]]
    assert len(keys) == len(set(keys))
    assert all(len(k) == 64 for k in keys + [a["action_key"]])


def test_targets_are_only_known_agents_and_no_authority_is_granted():
    d = evaluate(snap(waits=[wait(deadline="2026-09-28T23:30:00Z")], events=[event()],
                      inbound_requests=[req(frm=RCO1)]), NOW)
    assert set(d["targets"]) <= {LEAD, RCO2}
    assert all(i["target"] in (LEAD, RCO2) for i in d["items"])
    assert d["authority"] == "none"
    assert d["schema"] == "wd.continuity-decision.v1"
    assert d["agent"] == LEAD and d["now_utc"] == NOW
    assert sorted(d["targets"]) == d["targets"]
    assert d["target"] is None  # two distinct dispatch targets -> act per item


# --- purity ------------------------------------------------------------------------------

def test_evaluate_is_pure(monkeypatch):
    import builtins
    import os
    import socket
    import subprocess

    def boom(*a, **k):
        raise AssertionError("I/O from the pure evaluator")

    s = snap(waits=[wait(deadline="2026-09-28T23:30:00Z")], events=[event(), bound_reply()],
             inbound_requests=[req()], holds=[hold(["x/y"])], processing=[proc(1)],
             policy={"grace_seconds": 600})
    frozen = copy.deepcopy(s)
    monkeypatch.setattr(builtins, "open", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(os, "getenv", boom)
    first = evaluate(s, NOW)
    second = evaluate(s, NOW)
    assert s == frozen
    assert first == second


def test_module_imports_and_calls_stay_pure():
    tree = ast.parse(Path(guard.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module)
    assert imported <= {"__future__", "argparse", "hashlib", "json", "re", "sys",
                        "datetime", "collections.abc", "typing"}
    banned = {"now", "utcnow", "today", "time", "getenv", "environ", "system", "open"}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert not (attrs & banned)
    assert "open" not in names


# --- CLI ---------------------------------------------------------------------------------

def test_cli_takes_snapshot_argument_and_prints_strict_json():
    out = io.StringIO()
    s = snap(events=[event()])
    rc = guard.main(["--snapshot-json", json.dumps(s), "--now-utc", NOW], stdout=out)
    assert rc == 0
    text = out.getvalue()
    assert json.loads(text) == evaluate(s, NOW)
    assert text.count("\n") == 1 and "NaN" not in text


def test_cli_invalid_json_is_unknown():
    out = io.StringIO()
    assert guard.main(["--snapshot-json", "{not json", "--now-utc", NOW], stdout=out) == 0
    assert json.loads(out.getvalue())["verdict"] == "unknown"


@pytest.mark.parametrize("text", [
    json.dumps(snap(checkpoint=cp(status="done")))[:-1] + ', "agent": "codex-tools-1"}',
    json.dumps(snap(checkpoint=cp(status="done")))[:-1] + ', "x": NaN}',
])
def test_cli_duplicate_keys_and_non_strict_json_are_unknown(text):
    out = io.StringIO()
    guard.main(["--snapshot-json", text, "--now-utc", NOW], stdout=out)
    assert json.loads(out.getvalue())["verdict"] == "unknown"


def test_cli_usage_error_is_nonzero():
    with pytest.raises(SystemExit) as exc:
        guard.main(["--now-utc", NOW], stdout=io.StringIO())
    assert exc.value.code == 2


# --- binding validity (pinned predicate) -------------------------------------------------

@pytest.mark.parametrize("field,reason", [("binding_valid", "bound_reply_binding_invalid"),
                                          ("schema_valid", "bound_reply_schema_invalid")])
def test_reply_failing_the_pinned_predicate_is_not_done(field, reason):
    # e.g. wrong nonce or wrong request digest: id and sender match, the
    # pinned binding predicate does not -> never dependency_done.
    d = evaluate(snap(waits=[wait(deadline="2026-09-29T01:00:00Z")],
                      events=[bound_reply(**{field: False})], processing=[proc(7)]), NOW)
    w = only(d, "wait")
    assert w["dependency_done"] is False
    assert w["verdict"] == "decide"
    assert reason in w["reasons"]


@pytest.mark.parametrize("field", ["binding_valid", "schema_valid"])
def test_missing_or_non_bool_validity_flags_are_unknown(field):
    missing = bound_reply()
    del missing[field]
    assert evaluate(snap(waits=[wait()], events=[missing]), NOW)["verdict"] == "unknown"
    assert evaluate(snap(waits=[wait()], events=[dict(bound_reply(), **{field: 1})]), NOW)["verdict"] == "unknown"


# --- paused / held / cancelled checkpoints ------------------------------------------------

@pytest.mark.parametrize("status", ["paused", "hold", "held", "on_hold", "operator_pause"])
def test_paused_or_held_checkpoint_is_hold_not_dispatch(status):
    d = evaluate(snap(checkpoint=cp(status=status)), NOW)
    assert d["verdict"] == "hold"
    assert "checkpoint_paused" in only(d, "checkpoint")["reasons"]


@pytest.mark.parametrize("status", ["cancelled", "canceled"])
def test_cancelled_checkpoint_does_not_dispatch(status):
    d = evaluate(snap(checkpoint=cp(status=status)), NOW)
    assert d["verdict"] == "idle_ok"
    assert "checkpoint_cancelled" in only(d, "checkpoint")["reasons"]


# --- evidence.scope = checkpoint_only (runtime phase 1) -----------------------------------

def cponly(**over):
    s = snap(**over)
    s["evidence"] = dict(s["evidence"], scope="checkpoint_only")
    return s


def test_checkpoint_only_scope_recovers_stale_unfinished_checkpoint():
    d = evaluate(cponly(), NOW)
    assert d["verdict"] == "dispatch"
    assert d["scope"] == "checkpoint_only"
    assert "unfinished_no_wakeup" in only(d, "checkpoint")["reasons"]


def test_checkpoint_only_scope_recent_or_future_wake_waits():
    assert evaluate(cponly(checkpoint=cp(updated="2026-09-28T23:50:00Z")), NOW)["verdict"] == "wait"
    assert evaluate(cponly(checkpoint=cp(next_wakeup="2026-09-29T01:00:00Z")), NOW)["verdict"] == "wait"


def test_checkpoint_only_scope_never_claims_request_completeness():
    d = evaluate(cponly(checkpoint=cp(status="done")), NOW)
    assert d["verdict"] == "idle_ok"
    assert d["scope"] == "checkpoint_only"
    assert "request_completeness_not_evaluated" in d["reasons"]
    assert evaluate(snap(checkpoint=cp(status="done")), NOW)["scope"] == "canonical"


@pytest.mark.parametrize("field,value", [
    ("events", [event()]), ("waits", [wait()]), ("inbound_requests", [req()]),
    ("processing", [proc(1)]), ("cancellations", [{"target_kind": "wait", "target_id": "w1",
                                                   "by_agent": LEAD, "ts_utc": "2026-09-28T23:20:00Z"}]),
    ("claims", [{"task_id": PKG_TASK, "state": "active", "release_reason": None}]),
])
def test_checkpoint_only_scope_rejects_non_checkpoint_evidence(field, value):
    d = evaluate(cponly(**{field: value}), NOW)
    assert d["verdict"] == "unknown"
    assert "scope_violation" in d["reasons"]


def test_checkpoint_only_scope_keeps_holds():
    assert evaluate(cponly(holds=[hold([PKG_TASK])]), NOW)["verdict"] == "hold"


@pytest.mark.parametrize("scope", [None, "", "partial", "CANONICAL"])
def test_unknown_or_missing_scope_is_unknown(scope):
    s = snap(checkpoint=cp(status="done"))
    if scope is None:
        del s["evidence"]["scope"]
    else:
        s["evidence"]["scope"] = scope
    assert evaluate(s, NOW)["verdict"] == "unknown"


def test_checkpoint_only_scope_still_requires_complete_evidence():
    s = cponly()
    s["evidence"]["complete"] = False
    assert evaluate(s, NOW)["verdict"] == "unknown"


# --- mutation-driven gap closers ---------------------------------------------------------

def test_reply_not_addressed_to_subject_is_not_proof():
    d = evaluate(snap(waits=[wait()], events=[dict(bound_reply(), to_agent=TOOLS)],
                      processing=[proc(7)]), NOW)
    assert only(d, "wait")["dependency_done"] is False
    assert "unprocessed_event" not in json.dumps(d)


def test_pinned_predicate_failure_decides_even_before_the_deadline():
    d = evaluate(snap(waits=[wait(head=HEAD, deadline="2026-09-29T01:00:00Z")],
                      events=[bound_reply(head="b" * 40)]), NOW)
    w = only(d, "wait")
    assert w["verdict"] == "decide"
    assert {"bound_reply_head_mismatch", "dependency_pending"} <= set(w["reasons"])


def test_held_inbound_request_is_hold():
    d = evaluate(snap(TOOLS, checkpoint=cp(status="done"), inbound_requests=[req()],
                      holds=[hold(request_ids=[REQ])]), NOW)
    assert only(d, "request")["verdict"] == "hold"
    assert d["verdict"] == "hold"


def test_bound_reply_to_another_request_is_not_an_unbound_candidate():
    d = evaluate(snap(checkpoint=cp(task=REVIEW_TASK),
                      events=[event(reply_to="other-request")]), NOW)
    assert not items_of(d, "unbound_result")


def test_recent_unbound_result_waits_before_decision():
    d = evaluate(snap(checkpoint=cp(task=REVIEW_TASK, updated="2026-09-28T23:55:00Z"),
                      events=[event(ts="2026-09-28T23:55:00Z")]), NOW)
    item = only(d, "unbound_result")
    assert item["verdict"] == "wait"
    assert "unbound_result_recent" in item["reasons"]


def test_checkpoint_wakeup_is_due_exactly_at_its_time():
    d = evaluate(snap(checkpoint=cp(next_wakeup=NOW, updated="2026-09-28T23:59:00Z")), NOW)
    assert d["verdict"] == "dispatch"
    assert "checkpoint_wakeup_due" in only(d, "checkpoint")["reasons"]


def test_wait_outranks_hold():
    d = evaluate(snap(waits=[wait("w1", deadline="2026-09-28T23:30:00Z"),
                             wait("w2", request_id="r2", deadline="2026-09-29T01:00:00Z")],
                      holds=[hold(request_ids=[REQ])]), NOW)
    assert only(d, "checkpoint")["verdict"] == "wait"
    assert d["verdict"] == "wait"


def test_idle_decisions_for_different_agents_have_different_keys():
    a = evaluate(snap(LEAD, checkpoint=cp(status="done")), NOW)
    b = evaluate(snap(TOOLS, checkpoint=cp(status="done")), NOW)
    assert a["verdict"] == b["verdict"] == "idle_ok"
    assert a["action_key"] != b["action_key"]


# --- RCO2 FM1-FM3 and remaining mutation gaps ---------------------------------------------

def test_result_that_arrived_before_the_wait_was_declared_still_counts():
    # RCO2 FM1: the awaited result predates the watcher cursor / wait declaration.
    d = evaluate(snap(waits=[wait(declared="2026-09-28T23:10:00Z", deadline="2026-09-29T01:00:00Z")],
                      events=[bound_reply(ts="2026-09-28T22:59:32Z")]), NOW)
    w = only(d, "wait")
    assert (w["dependency_done"], w["processed"], w["verdict"]) == (True, False, "dispatch")
    assert "stall_waiter" in w["reasons"]


@pytest.mark.parametrize("status", ["waiting", "awaiting_rco2_full_suite", "blocked",
                                    "waiting_on_dependency"])
def test_waiting_checkpoint_without_structured_predicate_is_unknown(status):
    # RCO2 FM3: "I am waiting" with no wait/request record behind it escalates.
    fresh = evaluate(snap(checkpoint=cp(status=status, updated="2026-09-28T23:59:00Z")), NOW)
    assert fresh["verdict"] == "unknown"
    assert "waiting_without_structured_predicate" in only(fresh, "checkpoint")["reasons"]
    covered = evaluate(snap(checkpoint=cp(status=status),
                            waits=[wait(deadline="2026-09-29T01:00:00Z")]), NOW)
    assert only(covered, "checkpoint")["verdict"] == "wait"


def test_waiting_checkpoint_in_checkpoint_only_scope_is_bounded_reconciliation():
    recent = evaluate(cponly(checkpoint=cp(status="awaiting_review", updated="2026-09-28T23:59:00Z")), NOW)
    stale = evaluate(cponly(checkpoint=cp(status="awaiting_review")), NOW)
    assert recent["verdict"] == "wait"
    assert stale["verdict"] == "dispatch"
    assert "bounded_reconcile_not_acceptance" in only(stale, "checkpoint")["reasons"]
    assert "bounded_reconcile_not_acceptance" not in json.dumps(evaluate(snap(), NOW))


def test_cancelled_checkpoint_does_not_turn_its_events_into_decisions():
    d = evaluate(snap(checkpoint=cp(task=REVIEW_TASK, status="cancelled"), events=[event()]), NOW)
    assert d["verdict"] == "idle_ok"
    assert not items_of(d, "unbound_result")


def test_processed_events_are_not_reported_as_unprocessed():
    d = evaluate(snap(events=[event()], processing=[proc(1)]), NOW)
    assert d["verdict"] == "dispatch"
    assert not [r for r in only(d, "checkpoint")["work_refs"] if r["kind"] == "unprocessed_event"]


def test_wait_on_another_task_does_not_cover_the_checkpoint():
    d = evaluate(snap(waits=[wait(waiter_task="other/task", deadline="2026-09-29T01:00:00Z")]), NOW)
    assert only(d, "checkpoint")["verdict"] == "dispatch"


# --- checkpoint_only vocabulary gate + control tokens (safety contract 05:30Z) ----------

@pytest.mark.parametrize("status", sorted(guard.RECOVERABLE_STATUSES) +
                         ["r2_pushed_awaiting_rco_pass", "r13_pushed_awaiting_ci"])
def test_checkpoint_only_recovers_only_allow_listed_statuses(status):
    d = evaluate(cponly(checkpoint=cp(status=status)), NOW)
    assert d["verdict"] == "dispatch"


@pytest.mark.parametrize("status", [
    "on_hold", "held_by_lead", "paused_for_review", "parked", "deferred", "cancel_requested",
    "abandoned", "operator_review", "awaiting_signature", "nonce_pending", "idle_watch",
    "sentinel_armed", "delivered", "replied_bound", "r2_pushed_awaiting_operator",
    "in_progress_hold", "Parked", "HELD_BY_LEAD",
])
def test_checkpoint_only_no_wake_statuses_never_dispatch(status):
    d = evaluate(cponly(checkpoint=cp(status=status)), NOW)
    assert d["verdict"] == "hold"
    assert {"checkpoint_status_no_wake", "checkpoint_paused"} & set(only(d, "checkpoint")["reasons"])


@pytest.mark.parametrize("status", ["weird", "blocked", "waiting", "rx_pushed_awaiting_ci",
                                    "r2_pushed_awaiting_", "In_Progress", "audit_replied_x"])
def test_checkpoint_only_unrecognised_status_is_unknown(status):
    d = evaluate(cponly(checkpoint=cp(status=status)), NOW)
    if "replied" in status:
        assert d["verdict"] == "hold"
    else:
        assert d["verdict"] == "unknown"
        assert "checkpoint_status_unrecognized" in only(d, "checkpoint")["reasons"]


@pytest.mark.parametrize("scope_snap", [snap, cponly])
@pytest.mark.parametrize("field,text", [
    ("blockers", ["HOLD 1549 preserved"]), ("blockers", ["waiting for operator signature"]),
    ("blockers", ["do not merge"]), ("next_action", "Paused until the cutover is approved"),
    ("next_action", "STOP: veto open"), ("blockers", ["", "rollback pending"]),
])
def test_control_token_in_checkpoint_free_text_is_hold_possible(scope_snap, field, text):
    c = cp()
    c[field] = text
    d = evaluate(scope_snap(checkpoint=c), NOW)
    assert d["verdict"] == "unknown"
    assert "hold_possible_control_token" in only(d, "checkpoint")["reasons"]


def test_clean_blockers_do_not_block_recovery():
    c = dict(cp(), blockers=["RCO review of 076684f6 outstanding"])
    assert evaluate(snap(checkpoint=c), NOW)["verdict"] == "dispatch"
    assert evaluate(cponly(checkpoint=c), NOW)["verdict"] == "dispatch"


def test_done_checkpoint_ignores_control_tokens():
    c = dict(cp(status="done"), blockers=["HOLD 1549 preserved"])
    assert evaluate(snap(checkpoint=c), NOW)["verdict"] == "idle_ok"


@pytest.mark.parametrize("blockers", ["HOLD", [1], None, {"a": 1}])
def test_malformed_blockers_are_unknown(blockers):
    c = dict(cp(), blockers=blockers)
    assert evaluate(snap(checkpoint=c), NOW)["reasons"] == ["bad_list:checkpoint.blockers"]
