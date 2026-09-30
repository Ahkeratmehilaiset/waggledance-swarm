"""F19 pure task router (plan 2.7). Every value here is SYNTHETIC fixture data, not a claim about a real
worker, profile or pool."""
from __future__ import annotations

import ast
import copy
from pathlib import Path

import pytest

import tools.wd_composer_select as cs
import tools.wd_routing_weights as rw
import tools.wd_task_router as tr

NOW = "2026-09-30T17:00:00Z"
FRESH = "2026-09-30T16:59:00Z"
STALE = "2026-09-30T16:00:00Z"
EARLIER = "2026-09-30T16:30:00Z"
LATER = "2026-09-30T18:00:00Z"


def policy(**over):
    row = {"schema": tr.POLICY_SCHEMA, "max_evidence_age_seconds": 900, "budget_mode": "steady",
           "class_roles": {"planning_synthesis": ["producer", "lead"], "implementation": ["producer", "lead", "tools"],
                           "review": ["rco"], "test": ["producer", "tools"],
                           "advisory": ["grok_consult", "producer"]},
           "class_profiles": {"planning_synthesis": ["claude-strong", "codex-strong"],
                              "implementation": ["codex-std", "claude-strong"],
                              "review": ["claude-strong"], "test": ["codex-std", "claude-strong"],
                              "advisory": ["grok-default", "claude-strong"]}}
    row.update(over)
    return row


def worker(name, profile, roles, kind="lane", projected=40.0, **over):
    row = {"schema": tr.WORKER_SCHEMA, "worker": name, "kind": kind, "profile_id": profile,
           "role": {"worker": name, "roles": list(roles), "verified": True, "observed_utc": FRESH},
           "qualification": [{"task_class": c, "profile_id": profile, "qualified": True, "receipt_sha256": "e" * 64,
                              "observed_utc": EARLIER, "valid_until_utc": LATER} for c in tr.TASK_CLASSES],
           "capacity": {"profile_id": profile, "state": "available", "billing": "included",
                        "projected_used_percent": projected, "observed_utc": FRESH, "valid_until_utc": LATER},
           "load": {"state": "idle", "observed_utc": FRESH}}
    if kind == "grok":
        row["single_flight"] = {"state": "idle", "observed_utc": FRESH}
    row.update(over)
    return row


def fleet():
    return [worker("fable-5", "claude-strong", ["producer"]),
            worker("codex-tools-1", "codex-std", ["tools"]),
            worker("claude-rco-1", "claude-strong", ["rco"]),
            worker("grok", "grok-default", ["grok_consult"], kind="grok")]


def fleet_with(name, **over):
    rows = fleet()
    for row in rows:
        if row["worker"] == name:
            row.update(over)
    return rows


def task(task_class="implementation", **over):
    row = {"schema": tr.TASK_SCHEMA, "task_id": "task-1", "revision": "r1", "input_digest": "a" * 64,
           "task_class": task_class, "scope": ["repo:tools/wd_task_router.py"], "author": "fable-5",
           "created_utc": EARLIER}
    row.update(over)
    return row


def key_of(t):
    return tr.dispatch_key(t["task_id"], t["revision"], t["input_digest"], tr.normalize_scope(t["scope"]))


def artifact(commit="c" * 40, verified=True):
    return {"commit": commit, "branch": "fable-5/routing", "remote_verified": verified, "pushed_utc": EARLIER}


def attempt(attempt_id, t=None, state="active", lease=LATER, who="codex-tools-1", artifacts=None, **over):
    t = task() if t is None else t
    row = {"schema": tr.ATTEMPT_SCHEMA, "attempt_id": attempt_id, "dispatch_key": key_of(t), "task_id": t["task_id"],
           "worker": who, "scope": list(t["scope"]), "state": state, "lease_expires_utc": lease,
           "artifacts": [] if artifacts is None else artifacts}
    row.update(over)
    return row


def run(t=None, workers=None, attempts=None, pol=None, now=NOW, weights=None):
    out = tr.decide(task() if t is None else t, fleet() if workers is None else workers,
                    [] if attempts is None else attempts, policy() if pol is None else pol, now, weights)
    assert out["schema"] == tr.SCHEMA and out["feature"] == "F19" and out["verdict"] in tr.VERDICTS
    assert out["execution_allowed"] is False and out["authority"] == "none" and out["mode"] == "advice_only"
    assert out["dispatch_authority"] == "codex-lead-1"
    if out["verdict"] != tr.ROUTE:
        assert out["recommended"] is None and out["ranking"] == [] and out["resume_from"] == []
    return out


# --- success twin, determinism, classes ---------------------------------------------------------------

def test_success_twin_ranks_the_signed_profile_order():
    out = run()
    assert out["verdict"] == tr.ROUTE and out["reasons"] == ["ranked_eligible_worker"]
    assert out["recommended"] == {"worker": "codex-tools-1", "profile_id": "codex-std", "route": "direct"}
    assert [r["worker"] for r in out["ranking"]] == ["codex-tools-1", "fable-5"]
    assert out["ineligible"] == {"claude-rco-1": ["role_not_permitted"],
                                "grok": ["profile_not_signed_for_class", "role_not_permitted"]}
    assert out["unknown"] == {} and out["unavailable"] == {}
    assert out["dispatch_key"] == key_of(task()) and out["policy_sha256"] == cs.digest(policy())
    assert out["evidence_digest"] == cs.digest({"task": task(), "observed_workers": fleet(), "prepared_artifacts": [],
                                                "policy": policy(), "now": NOW, "shadow_weights": None})


def test_deterministic_and_input_order_independent():
    assert run() == run()
    reordered = run(workers=list(reversed(fleet())))
    for field in ("verdict", "recommended", "ranking", "ineligible", "unknown", "unavailable", "dispatch_key"):
        assert reordered[field] == run()[field]


def test_dispatch_key_ignores_scope_order_case_and_separators():
    one = task(scope=["repo:tools/a.py", "repo:tools/b.py"])
    two = task(scope=["REPO:Tools\\B.py", "repo:tools/a.py", "repo:tools/a.py"])
    assert key_of(one) == key_of(two)
    assert key_of(one) != key_of(task(scope=["repo:tools/a.py"]))
    assert key_of(task(revision="r2")) != key_of(task()) and key_of(task(input_digest="b" * 64)) != key_of(task())


def test_higher_class_wins_a_class_conflict():
    out = run(t=task(class_claims=["review", "advisory"]))
    assert out["task_class"] == "review" and out["recommended"]["worker"] == "claude-rco-1"


def test_author_never_reviews_own_work():
    out = run(t=task("review", author="claude-rco-1"))
    assert out["verdict"] == tr.HOLD and out["reasons"] == ["no_permitted_worker"]
    assert "author_cannot_review_own_work" in out["ineligible"]["claude-rco-1"]


# --- one outcome, one dispatch -----------------------------------------------------------------------

def test_live_lease_on_the_same_key_is_a_duplicate():
    out = run(attempts=[attempt("att-1")])
    assert out["verdict"] == tr.DUPLICATE and out["in_flight"] == "att-1"
    assert out["reasons"] == ["live_lease_on_dispatch_key"]


def test_replayed_attempt_row_is_still_one_duplicate():
    out = run(attempts=[attempt("att-1"), attempt("att-1")])
    assert out["verdict"] == tr.DUPLICATE and out["in_flight"] == "att-1"


def test_conflicting_attempt_rows_hold():
    out = run(attempts=[attempt("att-1"), attempt("att-1", who="fable-5")])
    assert out["verdict"] == tr.HOLD and out["reasons"] == ["attempt_conflict", "att-1"]


def test_accepted_attempt_satisfies_the_task():
    out = run(attempts=[attempt("att-1", state="accepted", artifacts=[artifact()])])
    assert out["verdict"] == tr.SATISFIED and out["satisfied_by"] == "att-1"
    assert out["preserved_artifacts"] == [{"attempt_id": "att-1", **artifact()}]


def test_accepted_attempt_without_a_verified_artifact_holds():
    out = run(attempts=[attempt("att-1", state="accepted", artifacts=[artifact(verified=False)])])
    assert out["verdict"] == tr.HOLD and "accepted_without_verified_artifact" in out["reasons"]


def test_new_revision_waits_for_the_live_old_revision_on_the_same_scope():
    out = run(t=task(revision="r2"), attempts=[attempt("att-1")])
    assert out["verdict"] == tr.WAIT and out["scope_conflicts"] == ["att-1"]
    assert out["reasons"] == ["scope_conflict:att-1"]


# --- lease expiry never loses pushed work -------------------------------------------------------------

def test_expired_lease_keeps_pushed_artifacts_and_offers_them_for_resume():
    pushed, local = artifact("1" * 40), artifact("2" * 40, verified=False)
    attempts = [attempt("att-1", lease=EARLIER, artifacts=[pushed, local])]
    before = copy.deepcopy(attempts)
    out = run(attempts=attempts)
    assert out["verdict"] == tr.ROUTE and out["expired_attempts"] == ["att-1"]
    assert out["preserved_artifacts"] == [{"attempt_id": "att-1", **pushed}, {"attempt_id": "att-1", **local}]
    assert out["resume_from"] == [{"attempt_id": "att-1", **pushed}]
    assert attempts == before


def test_released_attempt_keeps_its_artifacts():
    out = run(attempts=[attempt("att-1", state="released", artifacts=[artifact()])])
    assert out["verdict"] == tr.ROUTE and out["expired_attempts"] == []
    assert out["preserved_artifacts"] == out["resume_from"] == [{"attempt_id": "att-1", **artifact()}]


def test_expired_lease_of_another_task_no_longer_blocks_the_scope():
    other = task(task_id="task-2")
    assert run(attempts=[attempt("att-9", t=other, lease=EARLIER)])["verdict"] == tr.ROUTE
    assert run(attempts=[attempt("att-9", t=other)])["verdict"] == tr.WAIT


# --- scope ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("scope", [["repo:tools/"], ["repo:tools"], ["REPO:Tools\\wd_task_router.py"],
                                   ["repo:tools/wd_task_router.py", "repo:configs/x.json"]])
def test_overlapping_scope_of_another_live_task_waits(scope):
    other = task(task_id="task-2", scope=scope)
    out = run(attempts=[attempt("att-9", t=other)])
    assert out["verdict"] == tr.WAIT and out["scope_conflicts"] == ["att-9"]


@pytest.mark.parametrize("scope", [["repo:tools/wd_task_router.pyc"], ["repo:tests/tools/"], ["resource:grok"]])
def test_disjoint_scope_routes(scope):
    other = task(task_id="task-2", scope=scope)
    assert run(attempts=[attempt("att-9", t=other)])["verdict"] == tr.ROUTE


@pytest.mark.parametrize("scope,reason", [([], "scope_missing"), (["repo:tools/*.py"], "scope_invalid"),
                                          (["repo:tools/../configs/x"], "scope_invalid"),
                                          (["repo:tools//x.py"], "scope_invalid"), ([""], "scope_invalid"),
                                          ("repo:tools/x.py", "scope_missing")])
def test_unsafe_scope_holds(scope, reason):
    out = run(t=task(scope=scope))
    assert out["verdict"] == tr.HOLD and out["reasons"] == [reason]


# --- missing evidence is never ready -----------------------------------------------------------------

ONLY = "codex-tools-1"


def solo(**over):
    return [worker(ONLY, "codex-std", ["tools"], **over)]


@pytest.mark.parametrize("field,reason", [("role", "role_unknown_or_stale"),
                                          ("qualification", "qualification_missing"),
                                          ("capacity", "capacity_unknown_or_stale"),
                                          ("load", "load_unknown_or_stale")])
def test_missing_evidence_block_is_unknown_never_ready(field, reason):
    rows = solo()
    del rows[0][field]
    out = run(workers=rows)
    assert out["verdict"] == tr.UNKNOWN and out["reasons"] == ["worker_evidence_missing_or_stale"]
    assert reason in out["unknown"][ONLY]


def capacity(**over):
    row = {"profile_id": "codex-std", "state": "available", "billing": "included", "projected_used_percent": 40.0,
           "observed_utc": FRESH, "valid_until_utc": LATER}
    row.update(over)
    return row


def receipt(**over):
    row = {"task_class": "implementation", "profile_id": "codex-std", "qualified": True, "receipt_sha256": "e" * 64,
           "observed_utc": EARLIER, "valid_until_utc": LATER}
    row.update(over)
    return row


@pytest.mark.parametrize("over,reason", [
    ({"capacity": capacity(observed_utc=STALE)}, "capacity_unknown_or_stale"),
    ({"capacity": capacity(observed_utc=LATER)}, "capacity_unknown_or_stale"),
    ({"capacity": capacity(valid_until_utc=EARLIER)}, "capacity_unknown_or_stale"),
    ({"capacity": capacity(projected_used_percent=None)}, "capacity_unknown_or_stale"),
    ({"capacity": capacity(projected_used_percent=True)}, "capacity_unknown_or_stale"),
    ({"capacity": capacity(billing="unknown")}, "capacity_unknown_or_stale"),
    ({"capacity": capacity(state="maybe")}, "capacity_unknown_or_stale"),
    ({"capacity": capacity(profile_id="claude-strong")}, "capacity_unbound"),
    ({"role": {"worker": ONLY, "roles": ["tools"], "verified": False, "observed_utc": FRESH}}, "role_unknown_or_stale"),
    ({"role": {"worker": "fable-5", "roles": ["tools"], "verified": True, "observed_utc": FRESH}},
     "role_unknown_or_stale"),
    ({"role": {"worker": ONLY, "roles": ["tools"], "verified": True, "observed_utc": STALE}}, "role_unknown_or_stale"),
    ({"qualification": [receipt(valid_until_utc=EARLIER)]}, "qualification_unknown_or_expired"),
    ({"qualification": [receipt(receipt_sha256="nope")]}, "qualification_unknown_or_expired"),
    ({"qualification": [receipt(profile_id="claude-strong")]}, "qualification_missing"),
    ({"qualification": [receipt(task_class="review")]}, "qualification_missing"),
    ({"qualification": [receipt(), receipt(receipt_sha256="f" * 64)]}, "qualification_ambiguous"),
    ({"qualification": "all"}, "qualification_missing"),
    ({"load": {"state": "idle", "observed_utc": STALE}}, "load_unknown_or_stale"),
])
def test_stale_unbound_or_ambiguous_evidence_is_unknown(over, reason):
    out = run(workers=solo(**over))
    assert out["verdict"] == tr.UNKNOWN and reason in out["unknown"][ONLY]


def test_negative_qualification_is_ineligible_not_unknown():
    out = run(workers=solo(qualification=[receipt(qualified=False)]))
    assert out["verdict"] == tr.HOLD and out["ineligible"] == {ONLY: ["not_qualified"]}


@pytest.mark.parametrize("over,reason", [({"load": {"state": "busy", "observed_utc": FRESH}}, "busy"),
                                         ({"capacity": capacity(state="exhausted")}, "pool_exhausted"),
                                         ({"capacity": capacity(state="conserve")}, "pool_conserve"),
                                         ({"capacity": capacity(projected_used_percent=80.0)},
                                          "budget_over_trip_line")])
def test_known_but_blocked_worker_waits(over, reason):
    out = run(workers=solo(**over))
    assert out["verdict"] == tr.WAIT and out["unavailable"] == {ONLY: [reason]}


def test_trip_line_follows_the_signed_budget_mode():
    rows = solo(capacity=capacity(projected_used_percent=80.0))
    assert run(workers=rows, pol=policy(budget_mode="burst"))["verdict"] == tr.ROUTE


def test_headroom_breaks_a_profile_tie():
    rows = [worker("fable-5", "claude-strong", ["producer"], projected=60.0),
            worker("codex-lead-1", "claude-strong", ["lead"], projected=20.0)]
    assert [r["worker"] for r in run(workers=rows)["ranking"]] == ["codex-lead-1", "fable-5"]


# --- no authority ------------------------------------------------------------------------------------

@pytest.mark.parametrize("extra", ["grok_hourly_limit", "grok_weekly_limit", "per_agent_grok_quota", "activate",
                                   "grant_roles", "allow_paid_capacity", "relax_vetoes"])
def test_policy_is_closed_so_no_quota_flag_grant_or_override_rides_in(extra):
    out = run(pol=policy(**{extra: True}))
    assert out["verdict"] == tr.HOLD and out["reasons"] == ["policy_invalid"]


def test_policy_needs_every_class():
    table = policy()["class_roles"]
    del table["advisory"]
    assert run(pol=policy(class_roles=table))["reasons"] == ["policy_invalid", "class_roles"]


def test_veto_in_force_holds_and_cannot_be_relaxed():
    out = run(t=task(vetoes=["veto-event-1"]))
    assert out["verdict"] == tr.HOLD and out["reasons"] == ["veto_in_force"]
    assert run(t=task(override_veto=True))["reasons"] == ["task_malformed"]


def test_worker_record_is_closed():
    rows = fleet()
    rows[0]["granted_roles"] = ["lead"]
    assert run(workers=rows)["reasons"] == ["worker_malformed"]


def test_paid_capacity_is_never_recommended():
    out = run(workers=solo(capacity=capacity(billing="paid")))
    assert out["verdict"] == tr.HOLD and out["ineligible"] == {ONLY: ["paid_capacity_not_requestable"]}


def test_non_member_and_kind_mismatch_are_ineligible():
    rows = [worker("stranger", "codex-std", ["tools"]), worker("codex-tools-1", "codex-std", ["tools"], kind="grok")]
    out = run(workers=rows)
    assert out["ineligible"]["stranger"] == ["not_a_bridge_member"]
    assert out["ineligible"]["codex-tools-1"][0] == "kind_mismatch"


def test_duplicate_worker_holds():
    assert run(workers=fleet() + fleet()[:1])["reasons"] == ["duplicate_worker"]


@pytest.mark.parametrize("args", [(None, None, None, None, None), (1, "x", {}, [], 2.5),
                                  ({}, [], [], {}, NOW), (task(), fleet(), None, policy(), NOW),
                                  (task(), [None], [], policy(), NOW), (task(), fleet(), [None], policy(), NOW)])
def test_never_raises(args):
    out = tr.decide(*args)
    assert out["verdict"] == tr.HOLD and out["recommended"] is None and out["authority"] == "none"


@pytest.mark.parametrize("now", ["2026-09-30T17:00:00", "not a time", None])
def test_now_must_be_an_aware_timestamp(now):
    assert run(now=now)["reasons"] in (["now_invalid"], ["evidence_not_canonical_json"])


def test_non_json_input_holds():
    out = run(t=task(scope=("repo:tools/x.py",)))
    assert out["verdict"] == tr.HOLD and out["reasons"] == ["evidence_not_canonical_json"]
    assert out["evidence_digest"] is None


def test_task_router_facade_is_stateless():
    router = tr.TaskRouter()
    assert router.decide(task(), fleet(), [], policy(), NOW) == run()
    assert vars(router) == {}


# --- Grok: optional and single-flight ------------------------------------------------------------------

def test_advisory_routes_to_an_idle_grok():
    out = run(t=task("advisory"))
    assert out["recommended"] == {"worker": "grok", "profile_id": "grok-default", "route": "grok_consult"}
    assert [r["worker"] for r in out["ranking"]] == ["grok", "fable-5"]


def test_reserved_grok_is_never_a_second_flight():
    out = run(t=task("advisory"), workers=fleet_with("grok", single_flight={"state": "reserved", "observed_utc": FRESH}))
    assert out["recommended"]["worker"] == "fable-5" and out["unavailable"] == {"grok": ["grok_single_flight_busy"]}


def test_live_grok_attempt_blocks_a_second_flight():
    other = task(task_id="task-2", scope=["resource:grok-consult/task-2"])
    out = run(t=task("advisory"), attempts=[attempt("att-g", t=other, who="grok")])
    assert out["recommended"]["worker"] == "fable-5" and out["unavailable"] == {"grok": ["grok_single_flight_busy"]}


def test_busy_grok_never_makes_advisory_work_wait():
    rows = [worker("grok", "grok-default", ["grok_consult"], kind="grok",
                   single_flight={"state": "reserved", "observed_utc": FRESH}),
            worker("claude-rco-1", "claude-strong", ["rco"])]
    out = run(t=task("advisory"), workers=rows)
    assert out["verdict"] == tr.SKIPPED and out["reasons"] == ["no_eligible_advisory_worker"]


def test_unknown_grok_never_makes_routing_unknown():
    rows = fleet_with("grok", single_flight=None)
    assert run(t=task("advisory"), workers=rows)["recommended"]["worker"] == "fable-5"
    lone = [worker("grok", "grok-default", ["grok_consult"], kind="grok", single_flight=None),
            worker("claude-rco-1", "claude-strong", ["rco"])]
    assert run(t=task("advisory"), workers=lone)["verdict"] == tr.SKIPPED
    assert run(t=task("implementation"), workers=lone)["verdict"] == tr.HOLD


def test_no_grok_quota_exists_in_any_schema():
    names = tr.POLICY_KEYS + tr.TASK_REQUIRED + tr.TASK_OPTIONAL + tr.WORKER_REQUIRED + tr.WORKER_OPTIONAL
    assert not [n for n in names if any(word in n for word in ("quota", "hour", "week", "limit", "budget_per"))]


# --- planning_synthesis reuses the F24 composer ---------------------------------------------------------

POLICY_PIN = "a" * 64


def composer_profile(pid, provider, model, effort):
    return {"profile_id": pid, "provider": provider, "model": model, "effort": effort, "signed_in_envelope": True,
            "identity": {"verified": True, "observed_utc": FRESH, "profile_id": pid, "provider": provider,
                         "model": model, "effort": effort},
            "auth": {"verified": True, "observed_utc": FRESH, "profile_id": pid},
            "turn": {"ok": True, "observed_utc": FRESH, "profile_id": pid},
            "pool": {"profile_id": pid, "provider": provider, "pool_id": provider + "/pool", "state": "available",
                     "observed_utc": FRESH, "projected_used_percent": 40.0, "provider_up": True,
                     "valid_until_utc": LATER}}


def composer_evidence(task_id="task-1", enabled=True):
    snap = {"schema": cs.SNAPSHOT_SCHEMA, "index_name": "synthetic-index", "index_version": "1",
            "entries": [{"provider": "claude", "model": "model-a", "effort": "xhigh", "score": 60.0,
                         "uncertainty": 0.5, "coding_score": 70.0, "measured_on": "2026-09-20"},
                        {"provider": "codex", "model": "model-b", "effort": "high", "score": 50.0,
                         "uncertainty": 0.5, "coding_score": 65.0, "measured_on": "2026-09-20"}]}
    return {"schema": cs.EVIDENCE_SCHEMA, "now_utc": NOW,
            "f0": {"feature": "F24", "enabled": enabled, "reason": "synthetic", "policy_sha256": POLICY_PIN,
                   "revocation_version": 1},
            "policy_bits": {"policy_sha256": POLICY_PIN, "f24_composer_rule": True},
            "parameters": {"policy_sha256": POLICY_PIN, "index_name": "synthetic-index", "index_version": "1",
                           "registry_sha256": cs.digest(snap), "epsilon": 1.0, "plausibility_bound": 5.0,
                           "max_evidence_age_seconds": 900, "max_score_age_days": 30, "max_wait_seconds": 600,
                           "budget_mode": "steady", "planning_efforts": ["high", "xhigh"]},
            "registry_snapshot": snap, "task": {"task_id": task_id, "created_utc": EARLIER},
            "profiles": [composer_profile("claude-strong", "claude", "model-a", "xhigh"),
                         composer_profile("codex-strong", "codex", "model-b", "high")]}


def planning_fleet():
    return [worker("fable-5", "claude-strong", ["producer"]), worker("codex-lead-1", "codex-strong", ["lead"])]


def test_planning_synthesis_routes_to_the_composer_pick():
    out = run(t=task("planning_synthesis", composer_evidence=composer_evidence()), workers=planning_fleet())
    assert out["verdict"] == tr.ROUTE and out["recommended"]["worker"] == "fable-5"
    assert out["ineligible"] == {"codex-lead-1": ["not_the_composer_profile"]}
    assert out["composer"]["verdict"] == cs.COMPOSER and out["composer"]["selected_profile"] == "claude-strong"
    assert out["composer"]["inputs_digest"] == cs.digest(composer_evidence())


def test_composer_default_off_holds_the_route():
    out = run(t=task("planning_synthesis", composer_evidence=composer_evidence(enabled=False)),
              workers=planning_fleet())
    assert out["verdict"] == tr.HOLD and out["reasons"][:2] == ["composer_hold", "feature_disabled"]
    assert out["composer"]["verdict"] == cs.HOLD


def test_composer_evidence_is_required_for_planning_synthesis():
    out = run(t=task("planning_synthesis"), workers=planning_fleet())
    assert out["verdict"] == tr.UNKNOWN and out["reasons"] == ["composer_evidence_missing"]


def test_composer_bound_to_another_task_holds():
    out = run(t=task("planning_synthesis", composer_evidence=composer_evidence(task_id="task-2")),
              workers=planning_fleet())
    assert out["verdict"] == tr.HOLD and out["reasons"] == ["composer_task_mismatch"]


def test_composer_evidence_on_another_class_holds():
    out = run(t=task("implementation", composer_evidence=composer_evidence()))
    assert out["reasons"] == ["composer_evidence_not_for_class"]


# --- F26 shadow weights never change the advice ------------------------------------------------------

def shadow_weights():
    bounds = {"schema": rw.BOUNDS_SCHEMA, "min_weight": 0.25, "max_weight": 4.0, "prior_strength": 1.0,
              "half_life_seconds": 86400, "max_outcome_age_seconds": 2592000, "min_independent_evaluators": 1,
              "min_samples": 1}
    outcomes = [{"schema": rw.OUTCOME_SCHEMA, "outcome_id": "o-%d" % i, "kind": "outcome",
                 "dispatch_key": "%064x" % (i + 1), "task_class": "implementation", "profile_id": "claude-strong",
                 "worker": "fable-5", "result": "success", "stop_signal": None, "evaluators": ["claude-rco-1"],
                 "verified": True, "evidence_sha256": "f" * 64, "observed_utc": EARLIER} for i in range(3)]
    return rw.derive_shadow_weights(outcomes, {"bounds": bounds, "sha256": cs.digest(bounds)}, NOW)


def test_shadow_weights_reorder_only_the_shadow_view():
    weights = shadow_weights()
    assert weights["state"] == "derived"
    plain, shadowed = run(), run(weights=weights)
    for field in ("verdict", "reasons", "recommended", "ranking", "ineligible", "unknown", "unavailable"):
        assert shadowed[field] == plain[field]
    assert plain["shadow"] == {"state": "absent", "affects_advice": False}
    assert shadowed["shadow"] == {"state": "derived", "weights_digest": weights["evidence_digest"],
                                  "affects_advice": False, "ranking": ["fable-5", "codex-tools-1"]}


@pytest.mark.parametrize("mutate", [lambda w: w.update(mode="active"), lambda w: w.update(authority="lead"),
                                    lambda w: w.update(state="refused"), lambda w: w.update(schema="x"),
                                    lambda w: w.update(weights="all")])
def test_invalid_shadow_weights_are_ignored(mutate):
    weights = shadow_weights()
    mutate(weights)
    out = run(weights=weights)
    assert out["shadow"] == {"state": "ignored", "reason": "shadow_weights_invalid", "affects_advice": False}
    assert out["recommended"] == run()["recommended"]


def test_shadow_schema_is_pinned_to_the_learning_module():
    assert tr.SHADOW_WEIGHTS_SCHEMA == rw.SCHEMA


# --- purity ------------------------------------------------------------------------------------------

FORBIDDEN_IMPORTS = {"os", "sys", "subprocess", "socket", "pathlib", "time", "random", "urllib", "http",
                     "requests", "shutil", "io", "tempfile", "threading", "asyncio"}
FORBIDDEN_CALLS = {"open", "print", "exec", "eval", "compile", "__import__", "input", "now", "utcnow",
                   "today", "getenv", "system"}


@pytest.mark.parametrize("module", [tr, rw])
def test_modules_are_pure_by_construction(module):
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not {a.name.split(".")[0] for a in node.names} & FORBIDDEN_IMPORTS
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in FORBIDDEN_IMPORTS
        elif isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            assert name not in FORBIDDEN_CALLS, name
