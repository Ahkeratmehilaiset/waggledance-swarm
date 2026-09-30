# SPDX-License-Identifier: BUSL-1.1
"""W3 load evidence and worker-owned claim intent (RCO1 2026-09-30, Lead 58b182a1). Pure: no I/O anywhere.

The 670 starter was context only; its reproduced gaps (a month-old idle block reserved, unreadable=False
admitted, a non-member given a block, the real advice field recommended.worker unread) are pinned here as
refusals or unknowns: N1 to N4 below.
"""
from __future__ import annotations

import pytest

from tools import wd_routing_load as w3
from tools import wd_task_router as router

NOW = "2026-09-30T21:00:00Z"
AGE = 900
WORKERS = ["codex-tools-1", "fable-5", "claude-rco-2", "grok"]


def _task(**changes) -> dict:
    record = {"schema": router.TASK_SCHEMA, "task_id": "team/w3", "revision": "r1", "input_digest": "d" * 64,
              "task_class": "implementation", "scope": ["repo:tools/x.py"], "author": "codex-lead-1",
              "created_utc": "2026-09-30T20:00:00Z"}
    record.update(changes)
    return record


def _advice(**changes) -> dict:
    task = _task()
    key = router.dispatch_key(task["task_id"], task["revision"], task["input_digest"],
                              router.normalize_scope(task["scope"]))
    recommended = {"worker": "claude-rco-2", "profile_id": "p-impl", "route": "direct"}
    record = {"schema": router.SCHEMA, "feature": router.FEATURE, "verdict": router.ROUTE,
              "reasons": ["ranked_eligible_worker"], "task_id": task["task_id"], "task_class": "implementation",
              "dispatch_key": key, "recommended": dict(recommended), "ranking": [dict(recommended)],
              "ineligible": {}, "unknown": {}, "unavailable": {}, "mode": "advice_only", "execution_allowed": False,
              "authority": "none", "dispatch_authority": router.DISPATCH_AUTHORITY, "evidence_digest": "e" * 64}
    record.update(changes)
    return record


def _snapshot(**changes) -> dict:
    record = {"schema": w3.SNAPSHOT_SCHEMA, "observed_utc": "2026-09-30T20:59:30Z", "complete": True,
              "unreadable": 0,
              "claims": [{"source": "claim", "agent": "codex-tools-1", "task_id": "t/1", "owner_session_id": "s-1"}],
              "pending": [{"source": "pending", "agent": "fable-5", "task_id": "t/2", "owner_session_id": None}]}
    record.update(changes)
    return record


def _entry(**changes) -> dict:
    entry = {"source": "claim", "agent": "codex-tools-1", "task_id": "t/1", "owner_session_id": "s-1"}
    entry.update(changes)
    return entry


# -- negatives first: evidence that cannot prove a lane's load is unknown (no block), never idle --------------

UNKNOWN_SNAPSHOTS = {
    "stale": _snapshot(observed_utc="2026-09-30T20:44:59Z"),
    "future": _snapshot(observed_utc="2026-09-30T21:00:01Z"),
    "naive": _snapshot(observed_utc="2026-09-30T20:59:30"),
    "not_text": _snapshot(observed_utc=1790000000),
    "incomplete": _snapshot(complete=False),
    "complete_truthy": _snapshot(complete=1),
    "unreadable_false_N2": _snapshot(unreadable=False),
    "unreadable_true": _snapshot(unreadable=True),
    "unreadable_one": _snapshot(unreadable=1),
    "unreadable_float": _snapshot(unreadable=0.0),
    "schema": _snapshot(schema="wd.queue-claims-snapshot.v0"),
    "extra_key": {**_snapshot(), "note": "x"},
    "missing_key": {k: v for k, v in _snapshot().items() if k != "pending"},
    "claims_not_list": _snapshot(claims={}),
    "pending_unreadable": _snapshot(pending=[None]),
    "entry_extra_key": _snapshot(claims=[{**_entry(), "lease_seconds": 60}]),
    "entry_case_variant_key": _snapshot(claims=[{("Agent" if k == "agent" else k): v for k, v in _entry().items()}]),
    "entry_case_variant_agent": _snapshot(claims=[_entry(agent="Claude-rco-2")]),
    "entry_source_mismatch": _snapshot(claims=[_entry(source="pending")]),
    "entry_empty_session": _snapshot(claims=[_entry(owner_session_id="")]),
    "entry_no_task": _snapshot(claims=[_entry(task_id="")]),
    "too_many": _snapshot(claims=[_entry()] * (w3.MAX_ENTRIES + 1)),
}


@pytest.mark.parametrize("name", sorted(UNKNOWN_SNAPSHOTS))
def test_evidence_that_cannot_prove_load_gives_no_block(name):
    assert w3.load_blocks(WORKERS, UNKNOWN_SNAPSHOTS[name], NOW, AGE) == {}


@pytest.mark.parametrize("workers,now,age", [
    (("claude-rco-2",), NOW, AGE), (["intruder"], NOW, AGE), (["claude-rco-2", "claude-rco-2"], NOW, AGE),
    ([], NOW, AGE), (["Claude-rco-2"], NOW, AGE), (["claude-rco-2"], "2026-09-30T21:00:00", AGE),
    (["claude-rco-2"], 1790000000, AGE), (["claude-rco-2"], "now", AGE), (["claude-rco-2"], NOW, 0),
    (["claude-rco-2"], NOW, True), (["claude-rco-2"], NOW, 1.5),
    (["claude-rco-2"], NOW, w3.MAX_EVIDENCE_AGE_SECONDS + 1),
], ids=["tuple", "non_member_N3", "repeated", "empty", "case_variant", "naive_now", "numeric_now", "garbage_now",
        "age_zero", "age_bool", "age_float", "age_over_bound"])
def test_caller_contract_violations_refuse(workers, now, age):
    with pytest.raises(w3.RoutingLoadError):
        w3.load_blocks(workers, _snapshot(), now, age)


INTENT_REFUSALS = {
    "stale_idle_N1": ({"load": {**w3.load_blocks(WORKERS, _snapshot(), NOW, AGE)["claude-rco-2"],
                                "observed_utc": "2026-09-30T20:40:00Z"}}, "load_unknown_or_stale"),
    "future_load": ({"load": {**w3.load_blocks(WORKERS, _snapshot(), NOW, AGE)["claude-rco-2"],
                              "observed_utc": "2026-09-30T21:00:05Z"}}, "load_unknown_or_stale"),
    "missing_load": ({"load": None}, "load_unknown_or_stale"),
    "busy": ({"load": w3.load_blocks(WORKERS, _snapshot(), NOW, AGE)["codex-tools-1"],
              "worker": "codex-tools-1", "advice": _advice(recommended={"worker": "codex-tools-1", "profile_id": "p",
                                                                        "route": "direct"},
                                                           ranking=[{"worker": "codex-tools-1", "profile_id": "p",
                                                                     "route": "direct"}])}, "worker_not_idle"),
    "forged_key": ({"advice": _advice(dispatch_key="a" * 64)}, "advice_not_bound:dispatch_key"),
    "different_task": ({"task": _task(task_id="team/other")}, "advice_not_bound:task_id"),
    "changed_revision": ({"task": _task(revision="r2")}, "advice_not_bound:dispatch_key"),
    "changed_scope": ({"task": _task(scope=["repo:tools/y.py"])}, "advice_not_bound:dispatch_key"),
    "wrong_worker": ({"worker": "fable-5"}, "not_the_recommended_worker"),
    "cancellation": ({"task": _task(vetoes=["cancelled by operator"])}, "task_hold"),
    "task_malformed": ({"task": _task(input_digest="D" * 64)}, "task_hold"),
    "paid": ({"advice": _advice(ineligible={"claude-rco-2": ["paid_capacity_not_requestable"]})},
             "recommended_worker_listed:ineligible"),
    "unknown_listed": ({"advice": _advice(unknown={"claude-rco-2": ["load_unknown_or_stale"]})},
                       "recommended_worker_listed:unknown"),
    "verdict_hold": ({"advice": _advice(verdict=router.HOLD)}, "advice_not_bound:verdict"),
    "authority": ({"advice": _advice(authority="codex-lead-1")}, "advice_not_bound:authority"),
    "execution_allowed": ({"advice": _advice(execution_allowed=True)}, "advice_not_bound:execution_allowed"),
    "not_first_in_ranking": ({"advice": _advice(ranking=[{"worker": "fable-5", "profile_id": "p", "route": "direct"},
                                                         {"worker": "claude-rco-2", "profile_id": "p-impl",
                                                          "route": "direct"}])}, "advice_recommendation_malformed"),
    "starter_field_shape_N4": ({"advice": {**_advice(recommended=None, ranking=[]),
                                           "recommended_worker": "claude-rco-2"}}, "advice_recommendation_malformed"),
    "grok_consult": ({"worker": "grok", "advice": _advice(recommended={"worker": "grok", "profile_id": "g",
                                                                       "route": "grok_consult"},
                                                          ranking=[{"worker": "grok", "profile_id": "g",
                                                                    "route": "grok_consult"}])},
                     "recommended_worker_is_not_a_claiming_lane"),
}


@pytest.mark.parametrize("name", sorted(INTENT_REFUSALS))
def test_claim_intent_refusal_twins(name):
    changes, reason = INTENT_REFUSALS[name]
    arguments = {"task": _task(), "advice": _advice(), "worker": "claude-rco-2",
                 "load": w3.load_blocks(WORKERS, _snapshot(), NOW, AGE)["claude-rco-2"]}
    arguments.update(changes)
    result = w3.claim_intent(arguments["task"], arguments["advice"], arguments["worker"], arguments["load"], NOW, AGE)
    assert (result["verdict"], result["intent"]) == ("refused", None)
    assert result["reasons"][0] == reason, result


# -- positives -------------------------------------------------------------------------------------------------

def test_claim_and_pending_holders_are_busy_the_rest_idle_and_grok_gets_nothing():
    blocks = w3.load_blocks(WORKERS, _snapshot(), NOW, AGE)
    assert {lane: (b["state"], b["claims"]) for lane, b in blocks.items()} == {
        "codex-tools-1": ("busy", 1), "fable-5": ("busy", 1), "claude-rco-2": ("idle", 0)}
    assert all(b["observed_utc"] == "2026-09-30T20:59:30Z" and b["schema"] == w3.LOAD_SCHEMA for b in blocks.values())


def test_an_equivalent_offset_now_is_the_same_instant_and_observed_text_is_kept_exactly():
    snapshot = _snapshot(observed_utc="2026-09-30T23:59:30+03:00")
    blocks = w3.load_blocks(WORKERS, snapshot, "2026-09-30T23:00:00+02:00", AGE)
    assert blocks["claude-rco-2"]["observed_utc"] == "2026-09-30T23:59:30+03:00"
    assert w3.load_blocks(WORKERS, snapshot, NOW, AGE) == blocks


def test_intent_for_the_recommended_idle_worker_is_exact_and_grants_nothing():
    task = _task()
    load = w3.load_blocks(WORKERS, _snapshot(), NOW, AGE)["claude-rco-2"]
    result = w3.claim_intent(task, _advice(), "claude-rco-2", load, NOW, AGE)
    assert (result["verdict"], result["reasons"]) == ("intent", [])
    intent = result["intent"]
    assert intent == {"schema": w3.INTENT_SCHEMA, "authority": "none", "execution_allowed": False,
                      "owner": "claude-rco-2", "task_id": "team/w3", "revision": "r1",
                      "dispatch_key": _advice()["dispatch_key"], "mode": "write", "write_scope": ["repo:tools/x.py"],
                      "load_observed_utc": "2026-09-30T20:59:30Z"}
    assert intent["write_scope"] is not task["scope"]                       # a copy, never an alias


def test_outputs_are_fresh_objects_on_every_call():
    first = w3.load_blocks(WORKERS, _snapshot(), NOW, AGE)
    first["claude-rco-2"]["state"] = "busy"
    assert w3.load_blocks(WORKERS, _snapshot(), NOW, AGE)["claude-rco-2"]["state"] == "idle"
