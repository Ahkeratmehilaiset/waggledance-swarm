"""F26 S1 routing outcome producer. Every value here is SYNTHETIC fixture data, not a real dispatch."""
from __future__ import annotations

import ast
import copy
import hashlib
from pathlib import Path

import pytest

import tools.check_rco_pass_present as rco_gate
import tools.wd_composer_select as cs
import tools.wd_routing_outcomes as ro
import tools.wd_routing_weights as rw
import tools.wd_task_router as tr

NOW = "2026-10-01T15:00:00Z"
PUSHED = "2026-10-01T13:00:00Z"
PASSED = "2026-10-01T14:00:00Z"
LATER = "2026-10-01T14:30:00Z"
FUTURE = "2026-10-01T16:00:00Z"
TASK = "codex-lead-1/f26-fixture-task"
BRANCH = "fable-5/f26-fixture-branch"
HEAD = "a" * 40
OTHER_HEAD = "b" * 40
KEY = hashlib.sha256(b"dispatch-1").hexdigest()
RECORD_KEYS = {"schema", "feature", "mode", "state", "reasons", "now_utc", "outcomes", "rejected",
               "duplicates_ignored", "execution_allowed", "authority", "activation", "evidence_digest"}


def advice(**over):
    row = {"schema": tr.SCHEMA, "feature": "F19", "verdict": "route", "reasons": ["ranked_eligible_worker"],
           "task_id": TASK, "task_class": "implementation", "dispatch_key": KEY,
           "recommended": {"worker": "fable-5", "profile_id": "fable-strong", "route": "direct"},
           "ranking": [{"worker": "fable-5", "profile_id": "fable-strong", "route": "direct"},
                       {"worker": "codex-tools-1", "profile_id": "codex-strong", "route": "direct"}],
           "mode": "advice_only", "execution_allowed": False, "authority": "none",
           "dispatch_authority": "codex-lead-1", "evidence_digest": "c" * 64}
    row.update(over)
    return row


def artifact(commit=HEAD, verified=True, pushed=PUSHED, branch=BRANCH):
    return {"commit": commit, "branch": branch, "remote_verified": verified, "pushed_utc": pushed}


def attempt(attempt_id="att-1", worker="fable-5", state="accepted", artifacts=None, **over):
    row = {"schema": tr.ATTEMPT_SCHEMA, "attempt_id": attempt_id, "dispatch_key": KEY, "task_id": TASK,
           "worker": worker, "scope": ["tools/example.py"], "state": state,
           "lease_expires_utc": "2026-10-01T13:30:00Z",
           "artifacts": [artifact()] if artifacts is None else artifacts}
    row.update(over)
    return row


def event(agent="claude-rco-1", kind="pass", head=HEAD, ts=PASSED, task_id=TASK, **over):
    typ, status = {"pass": ("decision", "rco_pass"), "finding": ("finding", "changes_requested"),
                   "message": ("message", "advisory")}[kind]
    row = {"ts_utc": ts, "agent": agent, "type": typ, "status": status, "task_id": task_id,
           "message": "review of " + str(head), "payload": {"exact_head": head} if head else {}}
    row.update(over)
    return row


def envelope(*events):
    return {"schema": ro.EVENTS_SCHEMA, "identity_verified": True, "events": list(events)}


def produce(events, advices=None, attempts=None, now=NOW, raw_events=None):
    out = ro.outcomes([advice()] if advices is None else advices, [attempt()] if attempts is None else attempts,
                      envelope(*events) if raw_events is None else raw_events, now)
    assert set(out) == RECORD_KEYS and out["schema"] == ro.SCHEMA and out["feature"] == "F26"
    assert out["mode"] == "shadow" and out["authority"] == "none" and out["activation"] == "none"
    assert out["execution_allowed"] is False
    for item in out["outcomes"]:
        assert set(item) == set(rw.OUTCOME_KEYS) and item["schema"] == rw.OUTCOME_SCHEMA
        assert item["stop_signal"] in (None, "quality_regression") and item["kind"] == "outcome"
    return out


def reasons(out, source=None):
    return sorted(r["reason"] for r in out["rejected"] if source is None or r["source"] == source)


def weights(records, **bound_over):
    b = {"schema": rw.BOUNDS_SCHEMA, "min_weight": 0.25, "max_weight": 4.0, "prior_strength": 1.0,
         "half_life_seconds": 86400, "max_outcome_age_seconds": 30 * 86400, "min_independent_evaluators": 1,
         "min_samples": 1}
    b.update(bound_over)
    return rw.derive_shadow_weights(records, {"bounds": b, "sha256": cs.digest(b)}, NOW)


def only_pair(record):
    assert len(record["weights"]) == 1
    return record["weights"][0]


# --- the happy path ---------------------------------------------------------------------------------

def test_independent_pass_at_the_accepted_head_is_one_success():
    out = produce([event()])
    assert out["state"] == "produced" and out["rejected"] == [] and len(out["outcomes"]) == 1
    item = out["outcomes"][0]
    assert item["result"] == "success" and item["stop_signal"] is None and item["verified"] is True
    assert item["evaluators"] == ["claude-rco-1"] and item["worker"] == "fable-5"
    assert item["profile_id"] == "fable-strong" and item["task_class"] == "implementation"
    assert item["dispatch_key"] == KEY and item["observed_utc"] == "2026-10-01T14:00:00.000000Z"
    body = {k: v for k, v in item.items() if k != "outcome_id"}
    assert item["outcome_id"] == cs.digest(body)
    chained = weights(out["outcomes"])
    assert chained["rejected"] == [] and only_pair(chained)["state"] == "known"


def test_the_branch_name_is_the_canonical_task_id_too():
    out = produce([event(task_id=BRANCH)])
    assert [o["result"] for o in out["outcomes"]] == ["success"]


# --- twin 1: head binding ---------------------------------------------------------------------------

def test_twin1_pass_at_another_head_counts_nothing_and_the_exact_head_counts():
    wrong = produce([event(head=OTHER_HEAD)])
    assert wrong["outcomes"] == []
    assert reasons(wrong) == ["no_independent_evaluation", "unbound"]
    right = produce([event(head=HEAD)])
    assert len(right["outcomes"]) == 1 and right["rejected"] == []


@pytest.mark.parametrize("payload", [
    {},                                              # prose only: the message names the head
    {"head": HEAD},                                  # payload.head alone never binds
    {"exact_head": HEAD, "head": OTHER_HEAD},        # disagreeing structured claims bind nothing
    {"exact_head": HEAD.upper()},                    # exact lowercase spelling only
    {"exact_head": HEAD[:12]},
])
def test_only_a_structured_exact_head_binds(payload):
    out = produce([event(payload=payload, message="rco_pass at " + HEAD)])
    assert out["outcomes"] == [] and "unbound" in reasons(out, "event")


def test_an_event_for_another_task_is_unbound():
    out = produce([event(task_id="codex-lead-1/some-other-task")])
    assert out["outcomes"] == [] and "unbound" in reasons(out, "event")


# --- twin 2: a finding restricts, a message does not -------------------------------------------------

def test_twin2_recognized_finding_at_the_head_quarantines_and_a_message_does_not():
    found = produce([event(kind="finding")])
    item = found["outcomes"][0]
    assert item["result"] == "failure" and item["stop_signal"] == "quality_regression"
    assert only_pair(weights(found["outcomes"]))["state"] == "quarantined"
    told = produce([event(kind="message"), event()])
    assert [o["result"] for o in told["outcomes"]] == ["success"]
    assert reasons(told) == ["not_an_evaluation"]
    assert only_pair(weights(told["outcomes"]))["state"] == "known"


def test_a_finding_outranks_every_pass_at_the_same_head():
    out = produce([event(agent="claude-rco-1"), event(agent="claude-rco-2", kind="finding", ts=LATER)])
    assert [o["result"] for o in out["outcomes"]] == ["failure"]
    assert out["outcomes"][0]["evaluators"] == ["claude-rco-2"]


@pytest.mark.parametrize("over", [
    {"head": OTHER_HEAD}, {"head": None}, {"task_id": "codex-lead-1/other"}, {"agent": "codex-tools-1"}])
def test_an_unbound_or_unrecognized_finding_does_not_count(over):
    out = produce([event(kind="finding", **over), event(agent="claude-rco-2")])
    assert [o["result"] for o in out["outcomes"]] == ["success"]
    assert reasons(out, "event") in (["unbound"], ["unrecognized_evaluator"])


def test_the_workers_own_finding_still_restricts():
    out = produce([event(kind="finding")], advices=[advice(ranking=[
        {"worker": "claude-rco-1", "profile_id": "rco-strong", "route": "direct"}])],
        attempts=[attempt(worker="claude-rco-1")])
    assert [o["result"] for o in out["outcomes"]] == ["failure"]


# --- twin 3 (S1 part): the output is inert ------------------------------------------------------------

def test_twin3_output_is_shadow_with_no_authority_and_the_inputs_are_untouched():
    inputs = ([advice()], [attempt()], envelope(event(), event(kind="finding", ts=LATER)))
    before = copy.deepcopy(inputs)
    out = ro.outcomes(*inputs, NOW)
    assert inputs == before
    assert (out["mode"], out["authority"], out["activation"], out["execution_allowed"]) == (
        "shadow", "none", "none", False)


# --- twin 4: no self-evaluation, no double count ------------------------------------------------------

@pytest.mark.parametrize("worker", ["claude-rco-1", "Claude_RCO-1", " claude_rco_1 "])
def test_twin4_the_worker_never_evaluates_its_own_pass(worker):
    out = produce([event(agent="claude-rco-1")], advices=[advice(ranking=[
        {"worker": worker, "profile_id": "rco-strong", "route": "direct"}])], attempts=[attempt(worker=worker)])
    assert out["outcomes"] == []
    assert reasons(out) == ["no_independent_evaluation", "self_evaluation"]


def test_twin4_a_single_evaluator_below_the_signed_quorum_is_quorum_not_met():
    out = produce([event()])
    chained = weights(out["outcomes"], min_independent_evaluators=2)
    assert [r["reason"] for r in chained["rejected"]] == ["quorum_not_met"]
    two = produce([event(), event(agent="claude-rco-2", ts=LATER)])
    assert two["outcomes"][0]["evaluators"] == ["claude-rco-1", "claude-rco-2"]
    assert weights(two["outcomes"], min_independent_evaluators=2)["rejected"] == []


def test_twin4_a_rerun_gives_the_same_outcome_id_and_counts_once():
    first, again = produce([event()]), produce([event()])
    assert first == again
    chained = weights(first["outcomes"] + again["outcomes"])
    assert chained["duplicates_ignored"] == 1 and only_pair(chained)["samples"] == 1


def test_a_repeated_identical_event_counts_once():
    once, twice = produce([event()]), produce([event(), event()])
    assert twice["duplicates_ignored"] == 1 and twice["outcomes"] == once["outcomes"]


def test_more_evidence_later_is_a_new_id_that_the_weights_count_once():
    first = produce([event()])
    more = produce([event(), event(agent="claude-rco-2", ts=LATER)])
    assert first["outcomes"][0]["outcome_id"] != more["outcomes"][0]["outcome_id"]
    chained = weights(first["outcomes"] + more["outcomes"])
    assert [r["reason"] for r in chained["rejected"]] == ["duplicate_task_outcome"]
    assert only_pair(chained)["samples"] == 1


def test_input_order_does_not_change_the_record():
    events = [event(), event(agent="claude-rco-2", ts=LATER), event(kind="message"), event(head=OTHER_HEAD)]
    attempts = [attempt(), attempt("att-0", state="released")]
    assert produce(events, attempts=attempts) == produce(events[::-1], attempts=attempts[::-1])


# --- limit_hit and requalification are never produced ----------------------------------------------

def test_a_limit_event_is_not_an_evaluation_and_no_limit_hit_is_produced():
    out = produce([event(type="status", status="limit_hit"), event()])
    assert reasons(out) == ["not_an_evaluation"]
    assert all(o["stop_signal"] != "limit_hit" and o["kind"] == "outcome" for o in out["outcomes"])
    source = Path(ro.__file__).read_text(encoding="utf-8")
    assert '"limit_hit"' not in source and '"requalification"' not in source


# --- time -------------------------------------------------------------------------------------------

def test_future_dated_and_before_push_events_do_not_count():
    out = produce([event(ts=FUTURE), event(agent="claude-rco-2", ts="2026-10-01T12:59:59Z")])
    assert out["outcomes"] == []
    assert reasons(out) == ["before_push", "future_dated", "no_independent_evaluation"]


@pytest.mark.parametrize("now", ["2026-10-01T15:00:00", "not a time", None, 1759330800])
def test_a_missing_or_naive_now_refuses(now):
    out = produce([event()], now=now)
    assert out["state"] == "refused" and out["reasons"] == ["now_invalid"] and out["outcomes"] == []


@pytest.mark.parametrize("ts", ["2026-10-01T14:00:00", "yesterday", None, 5])
def test_an_event_without_an_offset_time_is_malformed(ts):
    out = produce([event(ts=ts)])
    assert out["outcomes"] == [] and "malformed" in reasons(out, "event")


# --- binding of advice and attempts ------------------------------------------------------------------

@pytest.mark.parametrize("attempts, reason", [
    ([attempt(state="active")], "not_accepted"),
    ([attempt(state="released")], "not_accepted"),
    ([attempt(artifacts=[artifact(verified=False)])], "attempt_malformed"),
    ([attempt(artifacts=[artifact(), artifact(commit=OTHER_HEAD)])], "accepted_head_ambiguous"),
    ([attempt(worker="claude-rco-2")], "worker_not_in_advice"),
    ([attempt(task_id="codex-lead-1/renamed")], "advice_task_mismatch"),
    ([attempt(extra="quota")], "attempt_malformed"),
    ([attempt(schema="wd.routing-attempt.v2")], "attempt_malformed"),
    ([attempt("att-1"), attempt("att-2")], "dispatch_key_ambiguous"),
    ([attempt(), attempt(worker="codex-tools-1")], "conflicting_duplicate"),
])
def test_an_unbindable_attempt_yields_no_outcome(attempts, reason):
    out = produce([event()], attempts=attempts)
    assert out["outcomes"] == [] and reason in reasons(out, "attempt")


@pytest.mark.parametrize("over", [
    {"verdict": "satisfied"}, {"authority": "lead"}, {"execution_allowed": True}, {"mode": "dispatch"},
    {"schema": "wd.task-routing-advice.v2"}, {"task_class": "deploy"}, {"evidence_digest": None},
    {"ranking": []}, {"ranking": [{"worker": "fable-5", "profile_id": "a"}, {"worker": "fable-5", "profile_id": "b"}]},
])
def test_unusable_advice_is_rejected_and_the_attempt_has_no_advice(over):
    out = produce([event()], advices=[advice(**over)])
    assert out["outcomes"] == []
    assert "advice_malformed" in reasons(out, "advice") and "advice_missing" in reasons(out, "attempt")


def test_two_different_advice_records_for_one_key_conflict():
    out = produce([event()], advices=[advice(), advice(task_class="review")])
    assert out["outcomes"] == [] and reasons(out, "advice") == ["conflicting_duplicate"]


# --- the evaluator envelope and hostile data ---------------------------------------------------------

@pytest.mark.parametrize("raw", [
    [event()],
    {"schema": ro.EVENTS_SCHEMA, "identity_verified": "true", "events": [event()]},
    {"schema": ro.EVENTS_SCHEMA, "identity_verified": 1, "events": [event()]},
    {"schema": ro.EVENTS_SCHEMA, "events": [event()]},
    {"schema": ro.EVENTS_SCHEMA, "identity_verified": True, "events": [event()], "signature": "x"},
    {"schema": "wd.bridge-events.v1", "identity_verified": True, "events": [event()]},
    {"schema": ro.EVENTS_SCHEMA, "identity_verified": True, "events": "[]"},
])
def test_evaluator_events_need_the_explicit_verified_envelope(raw):
    out = produce([], raw_events=raw)
    assert out["state"] == "refused" and out["reasons"] == ["evaluator_events_unverified"]


class Sneaky(str):
    def __eq__(self, other):
        return True

    __hash__ = str.__hash__


@pytest.mark.parametrize("hostile", [
    event(agent=Sneaky("codex-tools-1")),
    event(payload={"exact_head": HEAD, "score": float("nan")}),
    event(payload={"exact_head": HEAD, 1: "x"}),
    "rco_pass " + HEAD,
    None,
])
def test_hostile_events_are_rejected_visibly(hostile):
    out = produce([hostile])
    assert out["outcomes"] == [] and "malformed" in reasons(out, "event")


@pytest.mark.parametrize("args", [
    ("advice", [attempt()]), ([advice()], {"attempt": 1}), (None, None)])
def test_non_list_inputs_refuse(args):
    out = ro.outcomes(args[0], args[1], envelope(event()), NOW)
    assert out["state"] == "refused" and out["reasons"] == ["inputs_malformed"]


@pytest.mark.parametrize("signal", [KeyboardInterrupt, SystemExit])
def test_cancellation_propagates_instead_of_refusing(signal):
    class Cancelling(list):
        def __iter__(self):
            raise signal()

    raw = {"schema": ro.EVENTS_SCHEMA, "identity_verified": True, "events": Cancelling([event()])}
    with pytest.raises(signal):
        ro.outcomes([advice()], [attempt()], raw, NOW)


def test_an_unexpected_error_refuses_instead_of_raising():
    class Broken(list):
        def __iter__(self):
            raise RuntimeError("boom")

    raw = {"schema": ro.EVENTS_SCHEMA, "identity_verified": True, "events": Broken([event()])}
    out = ro.outcomes([advice()], [attempt()], raw, NOW)
    assert out["state"] == "refused" and out["reasons"] == ["input_malformed"]


# --- pins and purity ---------------------------------------------------------------------------------

def test_evaluators_are_the_rule9a_rco_set_and_bridge_members():
    assert ro.EVALUATORS == rco_gate.DEFAULT_RCO_AGENTS
    assert set(ro.EVALUATORS) <= set(tr.MEMBERS) and set(ro.EVALUATORS) <= set(rw.MEMBERS)
    assert ro.PASS_TYPES == tuple(sorted(rco_gate.DECISION_TYPES_FOR_PASS))
    assert {ro.PASS_STATUS} == set(rco_gate.RCO_PASS_STATUSES)
    assert ro.STOP_SIGNAL in rw.STOP_SIGNALS


def test_the_module_reads_no_clock_environment_file_or_network():
    tree = ast.parse(Path(ro.__file__).read_text(encoding="utf-8"))
    modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert modules <= {"__future__", "datetime", "typing", "tools.lane_profile_record", "tools.wd_composer_select",
                       "tools.wd_routing_weights", "tools.wd_task_router"}
    # Attribute reads and called names (the ``now`` parameter is the injected clock, not a call).
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | {
        n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not names & {"now", "utcnow", "today", "time", "environ", "getenv", "open", "urlopen", "subprocess"}
