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
    {"exact_head": HEAD, "head": OTHER_HEAD},        # disagreeing structured claims bind nothing
    {"exact_head": HEAD.upper()},                    # exact lowercase spelling only
    {"exact_head": HEAD[:12]},
    {"exact_head": None, "head": HEAD},              # a present exact_head never falls back to head
    {"exact_head": HEAD.upper(), "head": HEAD},
    {"exact_head": OTHER_HEAD, "head": HEAD},
    {"head": HEAD.upper()},                          # the fallback head is exact lowercase 40-hex too
    {"head": HEAD[:12]},
    {"head": None},
])
def test_only_a_structured_head_binds(payload):
    out = produce([event(payload=payload, message="rco_pass at " + HEAD)])
    assert out["outcomes"] == [] and "unbound" in reasons(out, "event")


# --- S0: the live writer's rco_pass shape (BIN Write-AgentEvent.ps1:439-445) ---------------------------

def writer_pass(agent="claude-rco-1", head=HEAD, **over):
    """What the pinned writer emits: payload.head (40 lowercase hex), the head in the message, no exact_head."""
    row = event(agent=agent, payload={"head": head}, message="rco_pass at exact head " + head)
    row.update(over)
    return row


def test_s0_a_live_writer_shaped_pass_binds_and_counts():
    out = produce([writer_pass()])
    assert [o["result"] for o in out["outcomes"]] == ["success"] and out["rejected"] == []
    assert only_pair(weights(out["outcomes"]))["state"] == "known"


@pytest.mark.parametrize("over", [
    {"message": "rco_pass, head in the payload only"},       # the message must name the head
    {"message": None},
    {"message": ["rco_pass at " + HEAD]},
    {"message": "rco_pass at " + HEAD.upper()},              # ordinal, not case-insensitive
])
def test_s0_the_head_fallback_needs_the_exact_head_in_the_message(over):
    out = produce([writer_pass(**over)])
    assert out["outcomes"] == [] and "unbound" in reasons(out, "event")


def test_s0_the_message_key_absent_is_unbound():
    row = writer_pass()
    row.pop("message")
    out = produce([row])
    assert out["outcomes"] == [] and "unbound" in reasons(out, "event")


class Liar(str):
    """A str that also claims to equal every name in ``lies`` (RCO2 F1, 0e599be0)."""

    def __new__(cls, text, lies):
        value = super().__new__(cls, text)
        value.lies = frozenset(lies)
        return value

    def __eq__(self, other):
        return other in self.lies or str.__eq__(self, other)

    __hash__ = str.__hash__


class Containing(str):
    """A str that claims to contain anything."""

    def __contains__(self, item):
        return True


def test_s0_a_lying_message_cannot_fake_containment():
    lying = writer_pass(message=Containing("rco_pass with no head"))
    out = produce([lying])
    assert out["outcomes"] == [] and reasons(out, "event") == ["malformed"]   # A1: refused before it is read
    assert ro._head_claim(lying, "pass") is None                            # the exact-str message check itself


def test_s0_a_lying_head_cannot_agree_or_bind():
    liar = Liar("c" * 40, {HEAD})
    rows = [writer_pass(payload={"head": liar}, message="rco_pass at " + HEAD),
            event(payload={"exact_head": HEAD, "head": liar}),
            # Its text is in the message and it claims to equal the accepted head.
            writer_pass(payload={"head": Liar("rco_pass", {HEAD})}, message="rco_pass at " + HEAD)]
    for row in rows:
        out = produce([row])
        assert out["outcomes"] == [] and reasons(out, "event") == ["malformed"]   # A1 gate first
        assert ro._head_claim(row, "pass") is None   # and the exact-type head checks still refuse it alone


def test_s0_a_structured_finding_restricts_and_a_free_text_finding_does_not():
    structured = produce([writer_pass(agent="claude-rco-2"), event(kind="finding", payload={"head": HEAD},
                                                                    message="finding at " + HEAD)])
    assert [o["result"] for o in structured["outcomes"]] == ["failure"]
    assert only_pair(weights(structured["outcomes"]))["state"] == "quarantined"
    free_text = produce([writer_pass(agent="claude-rco-2"), event(kind="finding", payload={},
                                                                   message="finding at " + HEAD)])
    # A free-text finding never fails the task, but it withholds the success (RCO1 Lead 17:35Z).
    assert free_text["outcomes"] == [] and reasons(free_text) == ["restriction_unresolved", "unbound"]


def test_s0_writer_shaped_passes_keep_f1_and_f2():
    lying = produce([writer_pass(type=Liar("message", {"decision"}))])
    assert lying["outcomes"] == [] and "malformed" in reasons(lying, "event")
    twice = produce([writer_pass()], **two_targets())
    assert twice["outcomes"] == [] and "event_target_ambiguous" in reasons(twice, "event")


def structured_finding(agent="claude-rco-2", head=HEAD, **over):
    """A writer-shaped finding whose prose does not repeat the head: payload.head only (RCO2 ad022 R1)."""
    row = event(agent=agent, kind="finding", payload={"head": head}, message="changes requested, see the review")
    row.update(over)
    return row


def test_r1_a_structured_finding_restricts_without_the_head_in_its_message():
    out = produce([writer_pass(), structured_finding()])
    assert [o["result"] for o in out["outcomes"]] == ["failure"] and out["rejected"] == []
    assert out["outcomes"][0]["evaluators"] == ["claude-rco-2"]
    assert only_pair(weights(out["outcomes"]))["state"] == "quarantined"


def test_r1_a_pass_of_the_same_shape_still_needs_the_head_in_its_message():
    out = produce([writer_pass(message="rco_pass, see the review")])
    assert out["outcomes"] == [] and "unbound" in reasons(out, "event")


@pytest.mark.parametrize("over, reason, withheld", [
    ({"payload": {"head": None}}, "unbound", "restriction_unresolved"),
    ({"payload": {"head": OTHER_HEAD}}, "unbound", None),                  # another readable head: unrelated
    ({"payload": {"head": HEAD.upper()}}, "unbound", "restriction_unresolved"),
    ({"payload": {"head": HEAD[:12]}}, "unbound", "restriction_unresolved"),
    ({"payload": {}}, "unbound", "restriction_unresolved"),
    ({"payload": {"exact_head": None, "head": HEAD}}, "unbound", "restriction_unresolved"),   # no fallback
    ({"payload": {"exact_head": HEAD[:12], "head": HEAD}}, "unbound", "restriction_unresolved"),
    ({"payload": {"exact_head": OTHER_HEAD, "head": HEAD}}, "unbound", "restriction_unresolved"),
    ({"task_id": "codex-lead-1/other"}, "unbound", None),                   # another task: unrelated
    ({"agent": "codex-tools-1"}, "unrecognized_evaluator", None),
    ({"type": Liar("message", {"finding"})}, "malformed", "restriction_coverage_incomplete"),
])
def test_r1_a_structured_finding_restricts_only_when_it_binds(over, reason, withheld):
    out = produce([writer_pass(), structured_finding(**over)])
    assert reasons(out, "event") == [reason]   # it never becomes a failure
    if withheld is None:
        assert [o["result"] for o in out["outcomes"]] == ["success"]
    else:   # but an unresolved or unreadable finding withholds the success (RCO1 Lead 17:35Z)
        assert out["outcomes"] == [] and reasons(out, "attempt") == [withheld]


def test_r1_the_workers_own_structured_finding_still_restricts():
    out = produce([structured_finding(agent="claude-rco-1")], advices=[advice(ranking=[
        {"worker": "claude-rco-1", "profile_id": "rco-strong", "route": "direct"}])],
        attempts=[attempt(worker="claude-rco-1")])
    assert [o["result"] for o in out["outcomes"]] == ["failure"]


def test_r1_a_structured_finding_that_binds_two_targets_restricts_both():
    out = produce([structured_finding()], **two_targets())
    assert sorted(o["dispatch_key"] for o in out["outcomes"]) == sorted([KEY, KEY2])
    assert {o["result"] for o in out["outcomes"]} == {"failure"} and out["rejected"] == []


def test_r1_a_free_text_only_finding_stays_unbound():
    out = produce([writer_pass(), structured_finding(payload={}, message="finding at " + HEAD)])
    assert out["outcomes"] == [] and reasons(out) == ["restriction_unresolved", "unbound"]


def test_t1_a_full_length_hex_liar_head_never_binds():
    """Liar is 40 lowercase hex and claims to equal the accepted head; its own text is in the message.
    Only the exact-type check in _hex stops it (mutant: _hex accepts any str subclass)."""
    liar = Liar("c" * 40, {HEAD})
    fallback = writer_pass(payload={"head": liar}, message="rco_pass at exact head " + "c" * 40)
    exact = event(payload={"exact_head": liar}, message="rco_pass at exact head " + "c" * 40)
    finding = structured_finding(payload={"head": liar})
    # End to end the A1 gate refuses each before it is read; a genuine pass beside the finding still counts.
    for row in (fallback, exact):
        out = produce([row])
        assert out["outcomes"] == [] and reasons(out, "event") == ["malformed"]
    out = produce([writer_pass(), finding])
    assert out["outcomes"] == [] and reasons(out) == ["malformed", "restriction_coverage_incomplete"]
    # T1 proper: _head_claim alone (the gate bypassed) must still refuse the full-hex Liar, so the
    # _hex exact-type mutant dies here, not only behind the A1 gate.
    assert ro._head_claim(fallback, "pass") is None and ro._head_claim(exact, "pass") is None
    assert ro._head_claim(finding, "finding") is None


def test_s0_an_exact_head_with_an_agreeing_head_still_binds():
    out = produce([event(payload={"exact_head": HEAD, "head": HEAD})])
    assert [o["result"] for o in out["outcomes"]] == ["success"]


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


@pytest.mark.parametrize("over, withheld", [
    ({"head": OTHER_HEAD}, None), ({"head": None}, "restriction_unresolved"),
    ({"task_id": "codex-lead-1/other"}, None), ({"agent": "codex-tools-1"}, None)])
def test_an_unbound_or_unrecognized_finding_does_not_count(over, withheld):
    out = produce([event(kind="finding", **over), event(agent="claude-rco-2")])
    assert reasons(out, "event") in (["unbound"], ["unrecognized_evaluator"])   # never a failure
    if withheld is None:
        assert [o["result"] for o in out["outcomes"]] == ["success"]
    else:
        assert out["outcomes"] == [] and reasons(out, "attempt") == [withheld]


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


def test_f1_a_lying_type_cannot_forge_a_pass():
    out = produce([event(type=Liar("message", {"decision", "rco_review"}))])
    assert out["outcomes"] == []
    assert reasons(out) == ["malformed", "no_independent_evaluation"]


def test_f1_a_lying_status_cannot_forge_a_pass():
    out = produce([event(status=Liar("advisory", {"rco_pass"}))])
    assert out["outcomes"] == []
    assert reasons(out) == ["malformed", "no_independent_evaluation"]


def test_f1_a_lying_finding_type_is_malformed_and_a_genuine_finding_still_restricts():
    lying = produce([event(kind="finding", type=Liar("message", {"finding"})), event(agent="claude-rco-2")])
    assert lying["outcomes"] == [] and reasons(lying) == ["malformed", "restriction_coverage_incomplete"]
    genuine = produce([event(kind="finding"), event(agent="claude-rco-2")])
    assert [o["result"] for o in genuine["outcomes"]] == ["failure"]


KEY2 = hashlib.sha256(b"dispatch-2").hexdigest()


def two_targets(second_branch=BRANCH):
    """Two accepted attempts on different dispatch keys of one task at the same head (RCO2 F2)."""
    return dict(advices=[advice(), advice(dispatch_key=KEY2)],
                attempts=[attempt(), attempt("att-2", dispatch_key=KEY2,
                                             artifacts=[artifact(branch=second_branch)])])


def test_f2_one_pass_that_binds_two_accepted_targets_credits_neither():
    out = produce([event()], **two_targets())
    assert out["outcomes"] == []
    assert reasons(out) == ["event_target_ambiguous", "no_independent_evaluation", "no_independent_evaluation"]


def test_f2_a_finding_that_binds_two_targets_still_restricts_both():
    out = produce([event(kind="finding")], **two_targets())
    assert sorted(o["dispatch_key"] for o in out["outcomes"]) == sorted([KEY, KEY2])
    assert {o["result"] for o in out["outcomes"]} == {"failure"} and out["rejected"] == []


def test_f2_a_pass_on_the_branch_that_names_exactly_one_target_counts_once():
    out = produce([event(task_id=BRANCH)], **two_targets(second_branch="fable-5/f26-other-branch"))
    assert [(o["dispatch_key"], o["result"]) for o in out["outcomes"]] == [(KEY, "success")]
    assert reasons(out) == ["no_independent_evaluation"]


@pytest.mark.parametrize("args", [
    ("advice", [attempt()]), ([advice()], {"attempt": 1}), (None, None)])
def test_non_list_inputs_refuse(args):
    out = ro.outcomes(args[0], args[1], envelope(event()), NOW)
    assert out["state"] == "refused" and out["reasons"] == ["inputs_malformed"]


@pytest.mark.parametrize("signal", [KeyboardInterrupt, SystemExit])
def test_cancellation_propagates_instead_of_refusing(signal, monkeypatch):
    # A1: a list subclass is refused unread, so cancellation is injected inside a real helper instead.
    def cancelled(value):
        raise signal()

    monkeypatch.setattr(ro, "_kind", cancelled)
    with pytest.raises(signal):
        ro.outcomes([advice()], [attempt()], envelope(event()), NOW)


def test_an_unexpected_error_refuses_instead_of_raising(monkeypatch):
    class Broken(list):
        def __iter__(self):
            raise RuntimeError("boom")

    # A1: the hostile list is refused unread (its __iter__ never runs) ...
    raw = {"schema": ro.EVENTS_SCHEMA, "identity_verified": True, "events": Broken([event()])}
    out = ro.outcomes([advice()], [attempt()], raw, NOW)
    assert out["state"] == "refused" and out["reasons"] == ["evaluator_events_unverified"]

    # ... and a genuine unexpected error inside a helper still refuses instead of raising.
    def broken(value):
        raise RuntimeError("boom")

    monkeypatch.setattr(ro, "_kind", broken)
    out = ro.outcomes([advice()], [attempt()], envelope(event()), NOW)
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


# --- A1 (RCO1 16:47Z, aba4db01): every input container is exact built-in JSON before it is read -----------

class _HookRan(Exception):
    """Raised by a hostile hook. An ordinary Exception on purpose: outcomes() turns it into a visible
    refused/input_malformed record, so a hook that runs fails the assertion (exit 1), never an interrupt."""


class _Tripwire(dict):
    """A dict subclass whose every read hook raises _HookRan if it is ever called."""

    def _trip(self, *args, **kwargs):
        raise _HookRan("A1: a hostile container hook ran")

    get = __getitem__ = __contains__ = __iter__ = keys = items = values = __eq__ = __ne__ = __len__ = _trip
    __hash__ = None


class _ForgedPayload(dict):
    """Real content {}; claims an exact_head equal to the accepted head."""

    def get(self, key, default=None):
        return HEAD if key == "exact_head" else default

    def __contains__(self, key):
        return key == "exact_head"

    def __getitem__(self, key):
        return HEAD if key == "exact_head" else dict.__getitem__(self, key)


class _ForgedEvent(dict):
    """Real content: an advisory message; .get claims a decision rco_pass with a forged payload."""

    def get(self, key, default=None):
        forged = {"type": "decision", "status": "rco_pass", "payload": _ForgedPayload()}
        return forged[key] if key in forged else dict.get(self, key, default)


def _advisory():
    return {"ts_utc": PASSED, "agent": "claude-rco-1", "type": "message", "status": "advisory",
            "task_id": TASK, "message": "just a note", "payload": {}}


def test_a1_the_plain_advisory_control_is_not_an_evaluation():
    out = produce([_advisory()])
    assert out["outcomes"] == [] and "not_an_evaluation" in reasons(out, "event")


def test_a1_a_dict_subclass_event_cannot_forge_a_counted_pass():
    out = produce([_ForgedEvent(_advisory())])
    assert out["outcomes"] == [] and reasons(out, "event") == ["malformed"]


def test_a1_a_dict_subclass_payload_cannot_forge_a_counted_pass():
    out = produce([dict(_advisory(), type="decision", status="rco_pass", payload=_ForgedPayload())])
    assert out["outcomes"] == [] and reasons(out, "event") == ["malformed"]


def test_a1_a_forged_event_is_refused_and_withholds_the_genuine_pass_beside_it():
    out = produce([_ForgedEvent(_advisory()), event()])
    assert out["outcomes"] == [] and reasons(out, "event") == ["malformed"]
    assert reasons(out, "attempt") == ["restriction_coverage_incomplete"]


@pytest.mark.parametrize("hostile", [
    lambda: _Tripwire(event()),
    lambda: dict(event(), payload=_Tripwire({"exact_head": HEAD})),
    lambda: dict(event(), payload={"exact_head": HEAD, "nested": [_Tripwire()]}),
])
def test_a1_a_hostile_event_container_is_refused_without_running_its_hooks(hostile):
    out = produce([hostile(), event()])            # a hook that ran would give a refused record
    assert out["state"] == "produced" and reasons(out, "event") == ["malformed"]
    assert out["outcomes"] == [] and reasons(out, "attempt") == ["restriction_coverage_incomplete"]


@pytest.mark.parametrize("spoil", [
    lambda e: e["payload"].__setitem__("note", float("nan")),
    lambda e: e["payload"].__setitem__("note", [float("inf")]),
    lambda e: e["payload"].__setitem__("note", (1, 2)),
    lambda e: e.__setitem__(Liar("extra", {"type"}), "decision"),
    lambda e: e["payload"].__setitem__("note", Liar("x", {"y"})),
    lambda e: e["payload"].__setitem__("note", b"bytes"),
])
def test_a1_non_json_or_subclass_values_anywhere_in_an_event_are_malformed(spoil):
    row = event()
    spoil(row)
    out = produce([row])
    assert out["outcomes"] == [] and reasons(out, "event") == ["malformed"]


@pytest.mark.parametrize("value, plain", [
    ({"x": 1.5, "y": [True, None, "s", 3]}, True),
    ({"x": float("nan")}, False), ({"x": [float("inf")]}, False), ({"x": float("-inf")}, False),
    ({Liar("k", set()): 1}, False), ([Liar("v", set())], False), ({"x": (1,)}, False), (_Tripwire(), False),
])
def test_a1_the_plain_gate_itself(value, plain):
    # Pinned on its own: the later canonical digest also refuses NaN, so this gate is checked directly.
    assert ro._plain(value) is plain


def test_a1_deep_nesting_and_cycles_are_malformed_not_a_crash_or_refusal():
    deep = event()
    nested: object = "x"
    for _ in range(200):
        nested = [nested]
    deep["payload"]["note"] = nested
    cyclic = event()
    loop: list = []
    loop.append(loop)
    cyclic["payload"]["note"] = loop
    out = produce([deep, cyclic, event(agent="claude-rco-2")])
    assert out["state"] == "produced" and reasons(out, "event") == ["malformed", "malformed"]
    assert out["outcomes"] == [] and reasons(out, "attempt") == ["restriction_coverage_incomplete"]


def test_a1_a_subclass_advice_record_is_rejected_unread():
    out = produce([event()], advices=[_Tripwire(advice())])
    assert out["outcomes"] == [] and "malformed" in reasons(out, "advice")
    assert "advice_missing" in reasons(out, "attempt")


def test_a1_a_subclass_attempt_record_is_rejected_unread():
    out = produce([event()], attempts=[_Tripwire(attempt())])
    assert out["outcomes"] == [] and reasons(out, "attempt") == ["malformed"]


def test_a1_a_subclass_value_inside_an_attempt_is_rejected():
    out = produce([event()], attempts=[attempt(artifacts=[_Tripwire(artifact())])])
    assert out["outcomes"] == [] and reasons(out, "attempt") == ["malformed"]


@pytest.mark.parametrize("raw", [
    lambda: _Tripwire(envelope(event())),
    lambda: {"schema": ro.EVENTS_SCHEMA, "identity_verified": True, "events": type("L", (list,), {})([event()])},
    lambda: {Liar("schema", {"schema"}): ro.EVENTS_SCHEMA, "identity_verified": True, "events": [event()]},
    lambda: {"schema": Liar(ro.EVENTS_SCHEMA, set()), "identity_verified": True, "events": [event()]},
])
def test_a1_a_subclass_envelope_or_event_list_refuses_the_batch_unread(raw):
    out = produce([], raw_events=raw())
    assert out["state"] == "refused" and out["reasons"] == ["evaluator_events_unverified"]


@pytest.mark.parametrize("which", ["advice", "attempts"])
def test_a1_a_subclass_input_list_refuses_the_batch(which):
    listish = type("L", (list,), {})
    args = {"advices": listish([advice()]) if which == "advice" else None,
            "attempts": listish([attempt()]) if which == "attempts" else None}
    out = produce([event()], **args)
    assert out["state"] == "refused" and out["reasons"] == ["inputs_malformed"]


def test_a1_a_str_subclass_now_is_refused_and_not_echoed():
    out = ro.outcomes([advice()], [attempt()], envelope(event()), Liar(NOW, set()))
    assert out["state"] == "refused" and out["reasons"] == ["now_invalid"] and out["now_utc"] is None


def test_a1_writer_shaped_and_finding_controls_still_count():
    out = produce([writer_pass(), structured_finding()])
    assert [o["result"] for o in out["outcomes"]] == ["failure"]


# --- RCO2 fb9e A1-T1 / A1b: the F1 guards and the nested-list gate pinned directly ---------------------------

def test_k1_k2_k3_the_f1_exact_type_guards_hold_without_the_a1_gate():
    # Called directly, bypassing _plain: a later gate change must not silently reopen F1 (RCO2 fb9e K1/K2/K3).
    assert ro._kind({"type": Liar("message", {"decision"}), "status": "rco_pass"}) == "malformed"
    assert ro._kind({"type": "decision", "status": Liar("advisory", {"rco_pass"})}) == "malformed"
    assert ro._text(Liar("claude-rco-1", {"claude-rco-1"})) is False


class _HostileList(list):
    def __iter__(self):
        raise _HookRan("A1b: a nested list-subclass hook ran")


def test_a1b_a_nested_list_subclass_is_malformed_and_its_hook_never_runs():
    row = event(agent="claude-rco-2")
    row["payload"]["note"] = _HostileList([1, 2])
    out = produce([row, event()])
    assert out["state"] == "produced" and reasons(out, "event") == ["malformed"]   # a hook run would refuse
    assert ro._plain({"note": _HostileList()}) is False


# --- incomplete restriction evidence never yields a success (RCO1, Lead 17:35Z) ---------------------------

def _nan_finding():
    row = structured_finding()
    row["payload"]["note"] = float("nan")
    return row


def _deep_finding():
    row = structured_finding()
    nested: object = "x"
    for _ in range(80):
        nested = [nested]
    row["payload"]["note"] = nested
    return row


def test_incomplete_control_a_complete_batch_still_succeeds():
    out = produce([writer_pass(), structured_finding(payload={"head": OTHER_HEAD}), event(kind="message")])
    assert [o["result"] for o in out["outcomes"]] == ["success"]
    assert reasons(out) == ["not_an_evaluation", "unbound"]


@pytest.mark.parametrize("bad", [_nan_finding, _deep_finding,
                                 lambda: structured_finding(ts_utc=FUTURE),
                                 lambda: event(ts_utc=FUTURE),                       # a future PASS too
                                 lambda: dict(event(kind="message"), payload={"x": float("inf")}),
                                 lambda: event(agent=None)],
                         ids=["nan_finding", "deep_finding", "future_finding", "future_pass", "malformed_message",
                              "agentless"])
def test_incomplete_an_unread_or_future_event_withholds_every_success_in_the_batch(bad):
    out = produce([writer_pass(), bad()])
    assert out["outcomes"] == [] and reasons(out, "attempt") == ["restriction_coverage_incomplete"]
    assert set(reasons(out, "event")) <= {"malformed", "future_dated"}


def test_incomplete_a_known_failure_is_still_produced_beside_an_unread_event():
    out = produce([writer_pass(), structured_finding(), _nan_finding()])
    assert [o["result"] for o in out["outcomes"]] == ["failure"]


OTHER_TASK = "codex-lead-1/f26-other-task"


def _two_tasks():
    """Two accepted attempts on two tasks at two heads, each with its own genuine pass."""
    other = advice(dispatch_key=KEY2, task_id=OTHER_TASK)
    second = attempt(attempt_id="att-2", dispatch_key=KEY2, task_id=OTHER_TASK,
                     artifacts=[artifact(commit=OTHER_HEAD, branch="fable-5/other-branch")])
    passes = [writer_pass(), writer_pass(head=OTHER_HEAD, task_id=OTHER_TASK)]
    return dict(advices=[advice(), other], attempts=[attempt(), second]), passes


def test_incomplete_control_two_clean_tasks_both_succeed():
    inputs, passes = _two_tasks()
    out = produce(passes, **inputs)
    assert sorted(o["result"] for o in out["outcomes"]) == ["success", "success"]


def test_incomplete_withholding_covers_every_target_in_the_batch():
    inputs, passes = _two_tasks()
    out = produce([*passes, _nan_finding()], **inputs)
    assert out["outcomes"] == []
    assert reasons(out, "attempt") == ["restriction_coverage_incomplete"] * 2


def test_unresolved_a_free_text_finding_withholds_only_its_own_task():
    inputs, passes = _two_tasks()
    out = produce([*passes, structured_finding(payload={}, message="this is broken")], **inputs)   # at TASK only
    assert [(o["dispatch_key"], o["result"]) for o in out["outcomes"]] == [(KEY2, "success")]
    assert reasons(out, "attempt") == ["restriction_unresolved"]


def test_unresolved_a_free_text_finding_on_the_branch_name_also_withholds():
    out = produce([writer_pass(), structured_finding(payload={}, task_id=BRANCH, message="broken")])
    assert out["outcomes"] == [] and reasons(out, "attempt") == ["restriction_unresolved"]


# --- RCO2 92dbb4c4 G1/G2/G3 (Lead 17:58Z) -----------------------------------------------------------------

EARLY = "2026-10-01T12:00:00Z"   # before PUSHED (13:00): the lane-observed push time is later than the finding


def test_g1_a_finding_at_the_exact_head_before_the_reported_push_still_fails():
    out = produce([writer_pass(), structured_finding(ts_utc=EARLY)])
    assert [o["result"] for o in out["outcomes"]] == ["failure"] and out["rejected"] == []
    alone = produce([structured_finding(ts_utc=EARLY)])
    assert [o["result"] for o in alone["outcomes"]] == ["failure"]


def test_g1_a_pass_before_the_push_is_still_refused():
    out = produce([writer_pass(ts_utc=EARLY)])
    assert out["outcomes"] == [] and reasons(out) == ["before_push", "no_independent_evaluation"]


def test_g1_an_early_finding_elsewhere_or_headless_keeps_its_old_meaning():
    other = produce([writer_pass(), structured_finding(ts_utc=EARLY, payload={"head": OTHER_HEAD})])
    assert [o["result"] for o in other["outcomes"]] == ["success"] and reasons(other) == ["unbound"]
    headless = produce([writer_pass(), structured_finding(ts_utc=EARLY, payload={}, message="broken")])
    assert headless["outcomes"] == [] and reasons(headless) == ["restriction_unresolved", "unbound"]


def _changes_requested(agent="claude-rco-2", typ="decision", **over):
    row = event(agent=agent, type=typ, status="changes_requested", payload={"head": HEAD},
                message="changes requested, see the review")
    row.update(over)
    return row


@pytest.mark.parametrize("typ", ["decision", "rco_review"])
def test_g2_a_recognized_changes_requested_at_the_head_is_a_failure(typ):
    out = produce([writer_pass(), _changes_requested(typ=typ)])
    assert [o["result"] for o in out["outcomes"]] == ["failure"] and out["rejected"] == []
    assert out["outcomes"][0]["evaluators"] == ["claude-rco-2"]


def test_g2_a_headless_changes_requested_at_the_task_withholds():
    out = produce([writer_pass(), _changes_requested(payload={})])
    assert out["outcomes"] == [] and reasons(out) == ["restriction_unresolved", "unbound"]


@pytest.mark.parametrize("over, reason", [
    ({"agent": "codex-tools-1"}, "unrecognized_evaluator"),     # an unrecognized author gains no veto
    ({"task_id": "codex-lead-1/other"}, "unbound"),              # nor does a changes_requested on another task
    ({"payload": {"head": OTHER_HEAD}}, "unbound"),              # nor one at another readable head
    ({"status": "advisory"}, "not_an_evaluation"),               # other decision statuses stay non-evaluations
])
def test_g2_changes_requested_restricts_only_when_recognized_and_bound(over, reason):
    out = produce([writer_pass(), _changes_requested(**over)])
    assert [o["result"] for o in out["outcomes"]] == ["success"] and reasons(out) == [reason]


def test_g2_the_kind_mapping_itself():
    assert ro._kind({"type": "decision", "status": "changes_requested"}) == "finding"
    assert ro._kind({"type": "rco_review", "status": "changes_requested"}) == "finding"
    assert ro._kind({"type": "decision", "status": "rco_pass"}) == "pass"
    assert ro._kind({"type": "decision", "status": "answered"}) is None


@pytest.mark.parametrize("extra", ["attempt", "advice"])
def test_g3_a_malformed_advice_or_attempt_record_alone_never_suppresses_a_valid_pass(extra):
    # Only evaluator events leave restriction coverage unknown (RCO2 N9 killer).
    advices, attempts = [advice()], [attempt()]
    if extra == "attempt":
        attempts.append({"attempt_id": "att-bad", "note": float("nan")})
    else:
        advices.append({"dispatch_key": KEY2, "note": float("nan")})
    out = produce([writer_pass()], advices=advices, attempts=attempts)
    assert [o["result"] for o in out["outcomes"]] == ["success"]
    assert reasons(out, extra) == ["malformed"] and reasons(out, "event") == []


# --- RCO2 065eb40e J5/J6: changes_requested is an exact status on the decision types only ----------------

def test_j5_a_wrong_case_changes_requested_status_gains_no_veto():
    out = produce([writer_pass(), _changes_requested(status="Changes_Requested")])
    assert [o["result"] for o in out["outcomes"]] == ["success"] and reasons(out) == ["not_an_evaluation"]
    assert ro._kind({"type": "decision", "status": "Changes_Requested"}) is None


def test_j6_changes_requested_on_a_non_decision_type_gains_no_veto():
    out = produce([writer_pass(), _changes_requested(typ="message")])
    assert [o["result"] for o in out["outcomes"]] == ["success"] and reasons(out) == ["not_an_evaluation"]
    assert ro._kind({"type": "message", "status": "changes_requested"}) is None
