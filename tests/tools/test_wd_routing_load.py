# SPDX-License-Identifier: BUSL-1.1
"""W3 load evidence and worker-owned claim intent (RCO1 2026-09-30, Lead 58b182a1). Pure: no I/O anywhere.

The 670 starter was context only; its reproduced gaps (a month-old idle block reserved, unreadable=False
admitted, a non-member given a block, the real advice field recommended.worker unread) are pinned here as
refusals or unknowns: N1 to N4. RCO2's fixture plan (20:59:34Z, Lead ae1d4b41) added L3/L6/L10/R2/R3/R5/R7/R8.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tools import wd_routing_load as w3
from tools import wd_task_router as router

NOW = "2026-09-30T21:00:00Z"
AGE = 900
WORKERS = ["codex-tools-1", "fable-5", "claude-rco-2", "claude-rco-1", "grok"]
RECOMMENDED = {"worker": "claude-rco-2", "profile_id": "p-impl", "route": "direct"}


def _task(**changes) -> dict:
    record = {"schema": router.TASK_SCHEMA, "task_id": "team/w3", "revision": "r1", "input_digest": "d" * 64,
              "task_class": "implementation", "scope": ["repo:tools/x.py"], "author": "codex-lead-1",
              "created_utc": "2026-09-30T20:00:00Z"}
    record.update(changes)
    return record


def _advice(task: dict | None = None, recommended: dict | None = None, **changes) -> dict:
    """Built by the router's OWN output builder, so the positive twin has exactly the router's shape."""
    task = task or _task()
    rec = dict(recommended or RECOMMENDED)
    key = router.dispatch_key(task["task_id"], task["revision"], task["input_digest"],
                              router.normalize_scope(task["scope"]))
    record = router._advice(router.ROUTE, ["ranked_eligible_worker"],
                            {"task_id": task["task_id"], "task_class": "implementation", "dispatch_key": key,
                             "ranking": [rec], "evidence_digest": "e" * 64, "policy_sha256": "f" * 64})
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


def _block(worker: str = "claude-rco-2", **snapshot_changes) -> dict:
    return w3.load_blocks(WORKERS, _snapshot(**snapshot_changes), NOW, AGE)[worker]


class _Str(str):
    pass


# -- negatives first: evidence that cannot prove a lane's load is unknown (no block), never idle --------------

UNKNOWN_SNAPSHOTS = {
    "stale": _snapshot(observed_utc="2026-09-30T20:44:59Z"),
    "stale_by_1us": _snapshot(observed_utc="2026-09-30T20:44:59.999999Z"),
    "future": _snapshot(observed_utc="2026-09-30T21:00:01Z"),
    "future_by_1us": _snapshot(observed_utc="2026-09-30T21:00:00.000001Z"),
    "naive": _snapshot(observed_utc="2026-09-30T20:59:30"),
    "unparseable": _snapshot(observed_utc="garbage"),
    "oversized_text": _snapshot(observed_utc="2026-09-30T20:59:30." + "1" * 100 + "Z"),   # parses if unbounded
    "not_text": _snapshot(observed_utc=1790000000),
    "incomplete": _snapshot(complete=False),
    "complete_truthy": _snapshot(complete=1),
    "complete_text": _snapshot(complete="true"),
    "unreadable_false_N2": _snapshot(unreadable=False),
    "unreadable_true": _snapshot(unreadable=True),
    "unreadable_one": _snapshot(unreadable=1),
    "unreadable_negative": _snapshot(unreadable=-1),
    "unreadable_float": _snapshot(unreadable=0.0),
    "unreadable_huge": _snapshot(unreadable=10 ** 30),
    "unreadable_text": _snapshot(unreadable="0"),
    "schema": _snapshot(schema="wd.queue-claims-snapshot.v0"),
    "extra_key": {**_snapshot(), "note": "x"},
    "missing_key": {k: v for k, v in _snapshot().items() if k != "pending"},
    "claims_not_list": _snapshot(claims={}),
    "pending_unreadable": _snapshot(pending=[None]),
    "entry_extra_key": _snapshot(claims=[{**_entry(), "lease_seconds": 60}]),
    "entry_case_variant_key": _snapshot(claims=[{("Agent" if k == "agent" else k): v for k, v in _entry().items()}]),
    "entry_case_variant_agent": _snapshot(claims=[_entry(agent="Claude-rco-2")]),
    "entry_padded_agent": _snapshot(claims=[_entry(agent=" fable-5")]),
    "entry_str_subclass_agent": _snapshot(claims=[_entry(agent=_Str("fable-5"))]),
    "entry_non_member_holder": _snapshot(claims=[_entry(agent="stranger")]),
    "entry_source_mismatch": _snapshot(claims=[_entry(source="pending")]),
    "entry_empty_session": _snapshot(claims=[_entry(owner_session_id="")]),
    "entry_no_task": _snapshot(claims=[_entry(task_id="")]),
    "entry_oversized_task": _snapshot(claims=[_entry(task_id="t" * (w3.MAX_FIELD_TEXT + 1))]),
    "too_many": _snapshot(claims=[_entry()] * (w3.MAX_ENTRIES + 1)),
}


@pytest.mark.parametrize("name", sorted(UNKNOWN_SNAPSHOTS))
def test_evidence_that_cannot_prove_load_gives_no_block(name):
    assert w3.load_blocks(WORKERS, UNKNOWN_SNAPSHOTS[name], NOW, AGE) == {}


@pytest.mark.parametrize("workers,now,age", [
    (("claude-rco-2",), NOW, AGE), (["intruder"], NOW, AGE), (["claude-rco-2", "claude-rco-2"], NOW, AGE),
    ([], NOW, AGE), (["Claude-rco-2"], NOW, AGE), (["GROK"], NOW, AGE),
    (["claude-rco-2"], "2026-09-30T21:00:00", AGE), (["claude-rco-2"], 1790000000, AGE),
    (["claude-rco-2"], "now", AGE), (["claude-rco-2"], "2026-09-30T21:00:00." + "0" * 100 + "Z", AGE),
    (["claude-rco-2"], NOW, 0),
    (["claude-rco-2"], NOW, -1), (["claude-rco-2"], NOW, True), (["claude-rco-2"], NOW, 1.5),
    (["claude-rco-2"], NOW, w3.MAX_EVIDENCE_AGE_SECONDS + 1),
], ids=["tuple", "non_member_N3", "repeated", "empty", "case_variant", "grok_upper", "naive_now", "numeric_now",
        "garbage_now", "oversized_now", "age_zero", "age_negative", "age_bool", "age_float", "age_over_bound"])
def test_caller_contract_violations_refuse(workers, now, age):
    with pytest.raises(w3.RoutingLoadError):
        w3.load_blocks(workers, _snapshot(), now, age)


def _refusal(changes: dict) -> dict:
    arguments = {"task": _task(), "advice": _advice(), "worker": "claude-rco-2", "load": _block()}
    arguments.update(changes)
    return w3.claim_intent(arguments["task"], arguments["advice"], arguments["worker"], arguments["load"], NOW, AGE)


BUSY_REC = {"worker": "codex-tools-1", "profile_id": "p", "route": "direct"}
GROK_REC = {"worker": "grok", "profile_id": "g", "route": "grok_consult"}
INTENT_REFUSALS = {
    "stale_idle_N1": ({"load": {**_block(), "observed_utc": "2026-09-30T20:40:00Z"}}, "load_unknown_or_stale"),
    "future_load": ({"load": {**_block(), "observed_utc": "2026-09-30T21:00:05Z"}}, "load_unknown_or_stale"),
    "missing_load": ({"load": None}, "load_unknown_or_stale"),
    "load_extra_key": ({"load": {**_block(), "note": 1}}, "load_unknown_or_stale"),
    "another_workers_idle_block": ({"load": _block("claude-rco-1")}, "load_not_for_this_worker"),
    "busy": ({"load": _block("codex-tools-1"), "worker": "codex-tools-1", "advice": _advice(recommended=BUSY_REC)},
             "worker_not_idle"),
    "forged_key": ({"advice": _advice(dispatch_key="a" * 64)}, "advice_not_bound:dispatch_key"),
    "different_task": ({"task": _task(task_id="team/other")}, "advice_not_bound:task_id"),
    "changed_revision": ({"task": _task(revision="r2")}, "advice_not_bound:dispatch_key"),
    "changed_input_digest": ({"task": _task(input_digest="c" * 64)}, "advice_not_bound:dispatch_key"),
    "changed_scope": ({"task": _task(scope=["repo:tools/y.py"])}, "advice_not_bound:dispatch_key"),
    "wrong_worker": ({"worker": "fable-5"}, "not_the_recommended_worker"),
    "cancellation": ({"task": _task(vetoes=["cancelled by operator"])}, "task_hold"),
    "task_malformed": ({"task": _task(input_digest="D" * 64)}, "task_hold"),
    "int_revision": ({"task": _task(revision=1)}, "task_hold"),
    "future_created": ({"task": _task(created_utc="2026-09-30T21:00:01Z")}, "task_hold"),
    "unknown_task_class": ({"task": _task(task_class="chaos")}, "task_hold"),
    "paid": ({"advice": _advice(ineligible={"claude-rco-2": ["paid_capacity_not_requestable"]})},
             "recommended_worker_listed:ineligible"),
    "unknown_listed": ({"advice": _advice(unknown={"claude-rco-2": ["load_unknown_or_stale"]})},
                       "recommended_worker_listed:unknown"),
    "unavailable_listed": ({"advice": _advice(unavailable={"claude-rco-2": ["pool_exhausted"]})},
                           "recommended_worker_listed:unavailable"),
    **{"verdict_" + verdict: ({"advice": _advice(verdict=verdict)}, "advice_not_bound:verdict")
       for verdict in (router.HOLD, router.WAIT, router.UNKNOWN, router.DUPLICATE, router.SATISFIED, router.SKIPPED)},
    "authority": ({"advice": _advice(authority="codex-lead-1")}, "advice_not_bound:authority"),
    "mode": ({"advice": _advice(mode="dispatch")}, "advice_not_bound:mode"),
    "schema": ({"advice": _advice(schema="wd.task-routing-advice.v0")}, "advice_not_bound:schema"),
    "dispatch_authority": ({"advice": _advice(dispatch_authority="claude-rco-1")},
                           "advice_not_bound:dispatch_authority"),
    "execution_allowed": ({"advice": _advice(execution_allowed=True)}, "advice_not_bound:execution_allowed"),
    "advice_extra_key": ({"advice": {**_advice(), "note": 1}}, "advice_malformed"),
    "advice_missing_key": ({"advice": {k: v for k, v in _advice().items() if k != "shadow"}}, "advice_malformed"),
    "recommended_none": ({"advice": {**_advice(), "recommended": None}}, "advice_recommendation_malformed"),
    "not_first_in_ranking": ({"advice": _advice(ranking=[dict(BUSY_REC), dict(RECOMMENDED)])},
                             "advice_recommendation_malformed"),
    "starter_field_shape_N4": ({"advice": {**_advice(ranking=[]), "recommended": None,
                                           "recommended_worker": "claude-rco-2"}}, "advice_malformed"),
    "grok_consult": ({"worker": "grok", "advice": _advice(recommended=GROK_REC)},
                     "recommended_worker_is_not_a_claiming_lane"),
}


@pytest.mark.parametrize("name", sorted(INTENT_REFUSALS))
def test_claim_intent_refusal_twins(name):
    changes, reason = INTENT_REFUSALS[name]
    result = _refusal(changes)
    assert (result["verdict"], result["intent"]) == ("refused", None)
    assert result["reasons"][0] == reason, result


# -- positives -------------------------------------------------------------------------------------------------

def test_claim_and_pending_holders_are_busy_the_rest_idle_and_grok_gets_nothing():
    blocks = w3.load_blocks(WORKERS, _snapshot(), NOW, AGE)
    assert {lane: (b["worker"], b["state"], b["claims"]) for lane, b in blocks.items()} == {
        "codex-tools-1": ("codex-tools-1", "busy", 1), "fable-5": ("fable-5", "busy", 1),
        "claude-rco-2": ("claude-rco-2", "idle", 0), "claude-rco-1": ("claude-rco-1", "idle", 0)}
    assert all(b["observed_utc"] == "2026-09-30T20:59:30Z" and b["schema"] == w3.LOAD_SCHEMA for b in blocks.values())


@pytest.mark.parametrize("observed", ["2026-09-30T20:45:00Z", "2026-09-30T21:00:00Z",
                                      "2026-09-30T20:59:30.1234567+00:00", "2026-09-30T23:59:30+03:00"],
                         ids=["exactly_now_minus_age", "exactly_now", "dotnet_o_form", "offset_form"])
def test_the_inclusive_boundaries_and_every_accepted_form_keep_the_observed_text_exactly(observed):
    blocks = w3.load_blocks(WORKERS, _snapshot(observed_utc=observed), NOW, AGE)
    assert blocks["claude-rco-2"]["observed_utc"] == observed and blocks["claude-rco-2"]["state"] == "idle"


def test_an_equivalent_offset_now_is_the_same_instant():
    assert (w3.load_blocks(WORKERS, _snapshot(), "2026-09-30T23:00:00+02:00", AGE)
            == w3.load_blocks(WORKERS, _snapshot(), NOW, AGE))


def test_intent_for_the_recommended_idle_worker_is_exact_and_grants_nothing():
    task = _task()
    result = w3.claim_intent(task, _advice(), "claude-rco-2", _block(), NOW, AGE)
    assert (result["verdict"], result["reasons"]) == ("intent", [])
    intent = result["intent"]
    assert intent == {"schema": w3.INTENT_SCHEMA, "authority": "none", "execution_allowed": False,
                      "owner": "claude-rco-2", "task_id": "team/w3", "revision": "r1",
                      "dispatch_key": _advice()["dispatch_key"], "mode": "write", "write_scope": ["repo:tools/x.py"],
                      "load_observed_utc": "2026-09-30T20:59:30Z"}
    assert intent["write_scope"] is not task["scope"]                       # a copy, never an alias


def test_an_equivalent_scope_spelling_is_the_same_dispatch_and_a_trailing_slash_is_not():
    spelled = _task(scope=["repo:Tools\\x.py", "repo:tools/x.py"])      # case-folded, backslashed, duplicated
    result = w3.claim_intent(spelled, _advice(), "claude-rco-2", _block(), NOW, AGE)
    assert result["verdict"] == "intent" and result["intent"]["write_scope"] == ["repo:tools/x.py"]
    key = router.dispatch_key
    assert (key("t", "r", "d" * 64, router.normalize_scope(["repo:tools/"]))
            != key("t", "r", "d" * 64, router.normalize_scope(["repo:tools"])))   # the router's own rule


def test_intents_are_advisory_not_exclusive():
    # One idle snapshot yields an intent for two different tasks for the same worker: exclusivity is the
    # queue's (the worker's own keyed claim is the only atomic step), never this advice.
    other = _task(task_id="team/w3-b")
    first = w3.claim_intent(_task(), _advice(), "claude-rco-2", _block(), NOW, AGE)
    second = w3.claim_intent(other, _advice(task=other), "claude-rco-2", _block(), NOW, AGE)
    assert first["verdict"] == second["verdict"] == "intent"
    assert first["intent"]["dispatch_key"] != second["intent"]["dispatch_key"]


def test_outputs_are_fresh_objects_and_the_snapshot_is_not_aliased():
    snapshot = _snapshot()
    first = w3.load_blocks(WORKERS, snapshot, NOW, AGE)
    first["claude-rco-2"]["state"] = "busy"
    snapshot["claims"].append(_entry(agent="claude-rco-2"))
    assert w3.load_blocks(WORKERS, _snapshot(), NOW, AGE)["claude-rco-2"]["state"] == "idle"


def test_the_module_does_no_io_and_touches_no_queue():
    source = Path(w3.__file__).read_text(encoding="utf-8")
    for forbidden in ("import os", "subprocess", "import time", "open(", "socket", "claim_task", "release_task",
                      "bridge_v2_work_queue", "bridge_v2_queue_transactions", "except Exception", "except:"):
        assert forbidden not in source, forbidden
