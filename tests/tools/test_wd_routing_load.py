# SPDX-License-Identifier: BUSL-1.1
"""W3 load evidence and worker-owned claim intent (RCO1 2026-09-30, Lead 58b182a1). Pure: no I/O anywhere.

The 670 starter was context only; its reproduced gaps (a month-old idle block reserved, unreadable=False
admitted, a non-member given a block, the real advice field recommended.worker unread) are pinned here as
refusals or unknowns: N1 to N4. RCO2's fixture plan (20:59:34Z, Lead ae1d4b41) added L3/L6/L10/R2/R3/R5/R7/R8;
RCO2's review of 4fd2d39d (21:13:10Z) added F1 (the age bound is the signed policy's, bound by policy_sha256),
F2 (reasons and digests bound), F3 (hostile ranking objects refused, never raised) and F4 (exact-typed fields).
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from tools import wd_routing_load as w3
from tools import wd_task_router as router

NOW = "2026-09-30T21:00:00Z"
WORKERS = ["codex-tools-1", "fable-5", "claude-rco-2", "claude-rco-1", "grok"]
RECOMMENDED = {"worker": "claude-rco-2", "profile_id": "p-impl", "route": "direct"}


def _policy(age: object = 900, **changes) -> dict:
    record = {"schema": router.POLICY_SCHEMA, "max_evidence_age_seconds": age, "budget_mode": "steady",
              "class_roles": {name: ["impl"] for name in router.TASK_CLASSES},
              "class_profiles": {name: ["p-impl"] for name in router.TASK_CLASSES}}
    record.update(changes)
    return record


POLICY = _policy()


def _task(**changes) -> dict:
    record = {"schema": router.TASK_SCHEMA, "task_id": "team/w3", "revision": "r1", "input_digest": "d" * 64,
              "task_class": "implementation", "scope": ["repo:tools/x.py"], "author": "codex-lead-1",
              "created_utc": "2026-09-30T20:00:00Z"}
    record.update(changes)
    return record


def _advice(task: dict | None = None, recommended: dict | None = None, policy: dict | None = None,
            **changes) -> dict:
    """Built by the router's OWN output builder, so the positive twin has exactly the router's shape."""
    task = task or _task()
    rec = dict(recommended or RECOMMENDED)
    key = router.dispatch_key(task["task_id"], task["revision"], task["input_digest"],
                              router.normalize_scope(task["scope"]))
    record = router._advice(router.ROUTE, ["ranked_eligible_worker"],
                            {"task_id": task["task_id"], "task_class": "implementation", "dispatch_key": key,
                             "ranking": [rec], "evidence_digest": "e" * 64,
                             "policy_sha256": router.digest(policy or POLICY)})
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


def _block(worker: str = "claude-rco-2", policy: dict = POLICY, **snapshot_changes) -> dict:
    return w3.load_blocks(WORKERS, _snapshot(**snapshot_changes), NOW, policy)[worker]


class _Str(str):
    pass


class _Hostile(dict):
    def __eq__(self, other):
        raise TypeError("hostile comparison")

    def __ne__(self, other):
        raise TypeError("hostile comparison")

    __hash__ = None


class _HostileText:
    def __eq__(self, other):
        raise TypeError("hostile comparison")

    def __ne__(self, other):
        raise TypeError("hostile comparison")

    __hash__ = None


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
    "schema_hostile": _snapshot(schema=_HostileText()),
    "extra_key": {**_snapshot(), "note": "x"},
    "missing_key": {k: v for k, v in _snapshot().items() if k != "pending"},
    "claims_not_list": _snapshot(claims={}),
    "pending_unreadable": _snapshot(pending=[None]),
    "entry_extra_key": _snapshot(claims=[{**_entry(), "lease_seconds": 60}]),
    "entry_case_variant_key": _snapshot(claims=[{("Agent" if k == "agent" else k): v for k, v in _entry().items()}]),
    "entry_case_variant_agent": _snapshot(claims=[_entry(agent="Claude-rco-2")]),
    "entry_padded_agent": _snapshot(claims=[_entry(agent=" fable-5")]),
    "entry_str_subclass_agent": _snapshot(claims=[_entry(agent=_Str("fable-5"))]),
    "entry_malformed_agent": _snapshot(claims=[_entry(agent="Stranger!")]),
    "entry_upper_member": _snapshot(claims=[_entry(agent="CLAUDE-RCO-2")]),
    "entry_separator_member_alias": _snapshot(claims=[_entry(agent="claude_rco_2")]),   # never "external"
    "entry_overlong_external": _snapshot(claims=[_entry(agent="g" + "x" * 33)]),
    "entry_source_mismatch": _snapshot(claims=[_entry(source="pending")]),
    "entry_source_hostile": _snapshot(claims=[_entry(source=_HostileText())]),
    "entry_empty_session": _snapshot(claims=[_entry(owner_session_id="")]),
    "entry_no_task": _snapshot(claims=[_entry(task_id="")]),
    "entry_oversized_task": _snapshot(claims=[_entry(task_id="t" * (w3.MAX_FIELD_TEXT + 1))]),
    "too_many": _snapshot(claims=[_entry()] * (w3.MAX_ENTRIES + 1)),
}


@pytest.mark.parametrize("name", sorted(UNKNOWN_SNAPSHOTS))
def test_evidence_that_cannot_prove_load_gives_no_block(name):
    assert w3.load_blocks(WORKERS, UNKNOWN_SNAPSHOTS[name], NOW, POLICY) == {}


@pytest.mark.parametrize("workers,now,policy", [
    (("claude-rco-2",), NOW, POLICY), (["intruder"], NOW, POLICY), (["claude-rco-2", "claude-rco-2"], NOW, POLICY),
    ([], NOW, POLICY), (["Claude-rco-2"], NOW, POLICY), (["GROK"], NOW, POLICY),
    (["claude-rco-2"], "2026-09-30T21:00:00", POLICY), (["claude-rco-2"], 1790000000, POLICY),
    (["claude-rco-2"], "now", POLICY), (["claude-rco-2"], "2026-09-30T21:00:00." + "0" * 100 + "Z", POLICY),
    (["claude-rco-2"], NOW, _policy(0)), (["claude-rco-2"], NOW, _policy(-1)), (["claude-rco-2"], NOW, _policy(True)),
    (["claude-rco-2"], NOW, _policy(1.5)), (["claude-rco-2"], NOW, {**POLICY, "note": 1}),
    (["claude-rco-2"], NOW, None),
], ids=["tuple", "non_member_N3", "repeated", "empty", "case_variant", "grok_upper", "naive_now", "numeric_now",
        "garbage_now", "oversized_now", "policy_age_zero", "policy_age_negative", "policy_age_bool",
        "policy_age_float", "policy_extra_key", "policy_missing"])
def test_caller_contract_violations_refuse(workers, now, policy):
    with pytest.raises(w3.RoutingLoadError):
        w3.load_blocks(workers, _snapshot(), now, policy)


def _refusal(changes: dict) -> dict:
    arguments = {"task": _task(), "advice": _advice(), "worker": "claude-rco-2", "load": _block(), "policy": POLICY}
    arguments.update(changes)
    return w3.claim_intent(arguments["task"], arguments["advice"], arguments["worker"], arguments["load"], NOW,
                           arguments["policy"])


BUSY_REC = {"worker": "codex-tools-1", "profile_id": "p", "route": "direct"}
GROK_REC = {"worker": "grok", "profile_id": "g", "route": "grok_consult"}
INTENT_REFUSALS = {
    "stale_idle_N1": ({"load": {**_block(), "observed_utc": "2026-09-30T20:40:00Z"}}, "load_unknown_or_stale"),
    "future_load": ({"load": {**_block(), "observed_utc": "2026-09-30T21:00:05Z"}}, "load_unknown_or_stale"),
    "missing_load": ({"load": None}, "load_unknown_or_stale"),
    "load_extra_key": ({"load": {**_block(), "note": 1}}, "load_unknown_or_stale"),
    "load_schema_hostile": ({"load": {**_block(), "schema": _HostileText()}}, "load_unknown_or_stale"),
    "load_state_hostile": ({"load": {**_block(), "state": _HostileText()}}, "worker_not_idle"),
    "another_workers_idle_block": ({"load": _block("claude-rco-1")}, "load_not_for_this_worker"),
    "busy": ({"load": _block("codex-tools-1"), "worker": "codex-tools-1", "advice": _advice(recommended=BUSY_REC)},
             "worker_not_idle"),
    # F1: the age bound is the signed policy's and the advice must have been decided under it.
    "stale_under_the_advices_policy_F1": ({"load": {**_block(), "observed_utc": "2026-09-30T20:43:20Z"}},
                                          "load_unknown_or_stale"),
    "wider_policy_than_the_advices_F1": ({"load": {**_block(), "observed_utc": "2026-09-30T20:43:20Z"},
                                          "policy": _policy(3600)}, "advice_not_bound:policy_sha256"),
    "invalid_policy": ({"policy": _policy(0)}, "policy_invalid"),
    # RCO2 21:43Z: where the router's freshness arithmetic overflows it holds; W3 must not call the load fresh.
    "overflowing_policy_age": ({"policy": _policy(10 ** 12), "advice": _advice(policy=_policy(10 ** 12)),
                                "load": {**_block(), "observed_utc": "1995-01-01T00:00:00Z"}},
                               "load_unknown_or_stale"),
    # F2: reasons and both digests are bound.
    "forged_reasons_F2": ({"advice": _advice(reasons=["forged"])}, "advice_not_bound:reasons"),
    "extra_reason": ({"advice": _advice(reasons=["ranked_eligible_worker", "x"])}, "advice_not_bound:reasons"),
    "evidence_digest_not_hex_F2": ({"advice": _advice(evidence_digest="E" * 64)}, "advice_not_bound:evidence_digest"),
    "evidence_digest_none": ({"advice": _advice(evidence_digest=None)}, "advice_not_bound:evidence_digest"),
    "policy_sha256_none_F2": ({"advice": _advice(policy_sha256=None)}, "advice_not_bound:policy_sha256"),
    "policy_sha256_other_F2": ({"advice": _advice(policy_sha256="0" * 64)}, "advice_not_bound:policy_sha256"),
    # F3/F4: exact types before any comparison.
    "hostile_first_ranking_F3": ({"advice": _advice(ranking=[_Hostile(RECOMMENDED)])},
                                 "advice_recommendation_malformed"),
    "hostile_recommended_F3": ({"advice": {**_advice(), "recommended": _Hostile(RECOMMENDED)}},
                               "advice_recommendation_malformed"),
    "profile_id_int_F4": ({"advice": _advice(recommended={**RECOMMENDED, "profile_id": 7},
                                             ranking=[{**RECOMMENDED, "profile_id": 7}])},
                          "advice_recommendation_malformed"),
    "route_empty_F4": ({"advice": _advice(recommended={**RECOMMENDED, "route": ""},
                                          ranking=[{**RECOMMENDED, "route": ""}])}, "advice_recommendation_malformed"),
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
    "verdict_hostile": ({"advice": _advice(verdict=_HostileText())}, "advice_not_bound:verdict"),
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
    blocks = w3.load_blocks(WORKERS, _snapshot(), NOW, POLICY)
    assert {lane: (b["worker"], b["state"], b["claims"]) for lane, b in blocks.items()} == {
        "codex-tools-1": ("codex-tools-1", "busy", 1), "fable-5": ("fable-5", "busy", 1),
        "claude-rco-2": ("claude-rco-2", "idle", 0), "claude-rco-1": ("claude-rco-1", "idle", 0)}
    assert all(b["observed_utc"] == "2026-09-30T20:59:30Z" and b["schema"] == w3.LOAD_SCHEMA for b in blocks.values())


@pytest.mark.parametrize("observed", ["2026-09-30T20:45:00Z", "2026-09-30T21:00:00Z",
                                      "2026-09-30T20:59:30.1234567+00:00", "2026-09-30T23:59:30+03:00"],
                         ids=["exactly_now_minus_age", "exactly_now", "dotnet_o_form", "offset_form"])
def test_the_inclusive_boundaries_and_every_accepted_form_keep_the_observed_text_exactly(observed):
    blocks = w3.load_blocks(WORKERS, _snapshot(observed_utc=observed), NOW, POLICY)
    assert blocks["claude-rco-2"]["observed_utc"] == observed and blocks["claude-rco-2"]["state"] == "idle"


def test_an_equivalent_offset_now_is_the_same_instant():
    assert (w3.load_blocks(WORKERS, _snapshot(), "2026-09-30T23:00:00+02:00", POLICY)
            == w3.load_blocks(WORKERS, _snapshot(), NOW, POLICY))


def test_a_long_valid_policy_age_is_honoured_not_capped_F1():
    # A router-valid policy of 172800 s: a day-old block is fresh under it (4fd2d39d raised on its 86400 cap).
    policy = _policy(172800)
    load = _block(policy=policy, observed_utc="2026-09-29T21:00:00Z")
    result = w3.claim_intent(_task(), _advice(policy=policy), "claude-rco-2", load, NOW, policy)
    assert result["verdict"] == "intent" and result["intent"]["policy_sha256"] == router.digest(policy)


@pytest.mark.parametrize("age", [10 ** 12, 10 ** 17], ids=["year_underflow", "timedelta_overflow"])
def test_an_age_whose_arithmetic_overflows_is_never_fresh_as_the_router_holds(age):
    # RCO2 21:43Z (reproduced at 1ab8d8b9): W3 marked a 31-year-old snapshot idle where the router's own
    # now - timedelta(max_age) overflows and it holds input_malformed. W3 now mirrors that arithmetic.
    snapshot = _snapshot(observed_utc="1995-01-01T00:00:00Z")
    assert w3.load_blocks(WORKERS, snapshot, NOW, _policy(age)) == {}
    with pytest.raises(OverflowError):
        router._fresh({"observed_utc": "1995-01-01T00:00:00Z"}, router._utc(NOW), age)


@pytest.mark.parametrize("age", [10 ** 12, 10 ** 14], ids=["1e12", "1e14"])
def test_the_actual_router_decide_holds_under_an_overflowing_age_and_w3_never_reads_fresh(age):
    # Lead 9252bfad: reproduce with the ACTUAL router.decide (its own test builders), not only its _fresh.
    rt = pytest.importorskip("test_wd_task_router")
    extreme = rt.policy(max_evidence_age_seconds=age)
    assert tr_decide_verdict(rt, extreme) == router.HOLD
    assert tr_decide_verdict(rt, rt.policy()) != router.HOLD                  # twin: the same inputs route
    assert w3.load_blocks(WORKERS, _snapshot(), NOW, _policy(age)) == {}      # W3 is never fresher than it


def tr_decide_verdict(rt, policy: dict) -> str:
    return router.decide(rt.task(), rt.fleet(), [], policy, rt.NOW)["verdict"]


def test_a_well_formed_non_member_holder_is_counted_apart_and_blinds_no_lane():
    # RCO2 21:43Z liveness: one claim by a real non-member id (grok-scout-1, operator) made EVERY lane unknown.
    claims = [_entry(agent="grok-scout-1", task_id="t/g"), _entry(agent="operator", task_id="t/o"),
              _entry(agent="codex-tools-1")]
    blocks = w3.load_blocks(WORKERS, _snapshot(claims=claims), NOW, POLICY)
    assert {lane: b["state"] for lane, b in blocks.items()} == {
        "codex-tools-1": "busy", "fable-5": "busy", "claude-rco-2": "idle", "claude-rco-1": "idle"}


def test_intent_for_the_recommended_idle_worker_is_exact_and_grants_nothing():
    task = _task()
    result = w3.claim_intent(task, _advice(), "claude-rco-2", _block(), NOW, POLICY)
    assert (result["verdict"], result["reasons"]) == ("intent", [])
    intent = result["intent"]
    assert intent == {"schema": w3.INTENT_SCHEMA, "authority": "none", "execution_allowed": False,
                      "owner": "claude-rco-2", "task_id": "team/w3", "revision": "r1",
                      "dispatch_key": _advice()["dispatch_key"], "mode": "write", "write_scope": ["repo:tools/x.py"],
                      "load_observed_utc": "2026-09-30T20:59:30Z", "evidence_digest": "e" * 64,
                      "policy_sha256": router.digest(POLICY)}
    assert intent["write_scope"] is not task["scope"]                       # a copy, never an alias


def test_an_equivalent_scope_spelling_is_the_same_dispatch_and_a_trailing_slash_is_not():
    spelled = _task(scope=["repo:Tools\\x.py", "repo:tools/x.py"])      # case-folded, backslashed, duplicated
    result = w3.claim_intent(spelled, _advice(), "claude-rco-2", _block(), NOW, POLICY)
    assert result["verdict"] == "intent" and result["intent"]["write_scope"] == ["repo:tools/x.py"]
    key = router.dispatch_key
    assert (key("t", "r", "d" * 64, router.normalize_scope(["repo:tools/"]))
            != key("t", "r", "d" * 64, router.normalize_scope(["repo:tools"])))   # the router's own rule


def test_intents_are_advisory_not_exclusive():
    # One idle snapshot yields an intent for two different tasks for the same worker: exclusivity is the
    # queue's (the worker's own keyed claim is the only atomic step), never this advice.
    other = _task(task_id="team/w3-b")
    first = w3.claim_intent(_task(), _advice(), "claude-rco-2", _block(), NOW, POLICY)
    second = w3.claim_intent(other, _advice(task=other), "claude-rco-2", _block(), NOW, POLICY)
    assert first["verdict"] == second["verdict"] == "intent"
    assert first["intent"]["dispatch_key"] != second["intent"]["dispatch_key"]


def test_outputs_are_fresh_objects_and_the_snapshot_is_not_aliased():
    snapshot = _snapshot()
    snapshot_before = deepcopy(snapshot)
    first = w3.load_blocks(WORKERS, snapshot, NOW, POLICY)
    assert snapshot == snapshot_before
    first["claude-rco-2"]["state"] = "busy"
    snapshot["claims"].append(_entry(agent="claude-rco-2"))
    assert w3.load_blocks(WORKERS, _snapshot(), NOW, POLICY)["claude-rco-2"]["state"] == "idle"


def test_the_module_does_no_io_and_touches_no_queue():
    source = Path(w3.__file__).read_text(encoding="utf-8")
    for forbidden in ("import os", "subprocess", "import time", "open(", "socket", "claim_task", "release_task",
                      "bridge_v2_work_queue", "bridge_v2_queue_transactions", "except Exception", "except:"):
        assert forbidden not in source, forbidden
