# SPDX-License-Identifier: BUSL-1.1
"""Relaunch steps 1-2 (request and safe boundary) and the shadow planner."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from tools.lane_profile_catalog import load_catalog
from tools.wd_lane_relaunch import ABORT, PARK, PROCEED, check_request, check_safe_boundary
from tools.wd_lane_profile_planner import (
    KEEP,
    WOULD_RELAUNCH,
    bucket_state,
    plan_lane,
    profile_for_observation,
)

ROOT = Path(__file__).resolve().parents[2]
CATALOG, DIGEST = load_catalog(ROOT / "configs" / "lane_profile_catalog.json")
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def req(lane="claude-rco-1", current="claude-sonnet-5-xhigh", target="claude-opus-5-5-xhigh"):
    return {"lane": lane, "current_profile": current, "target_profile": target}


# ------------------------------------------------------------ step 1

def test_raise_within_budget_proceeds_but_grants_nothing():
    result = check_request(CATALOG, req(), [], now=NOW)
    assert result["verdict"] == PROCEED and result["execution_allowed"] is False


@pytest.mark.parametrize("request_,reason", [
    (req(current="claude-opus-5-5-xhigh", target="claude-sonnet-5-xhigh"), "reviewer_lowering"),
    (req(lane="grok-scout-1"), "lane_not_in_catalog"),
    (req(target="codex-gpt-6-sol-high"), "target_not_allowed"),
    (req(current=None), "current_profile_unknown"),
])
def test_catalog_refusals_park_for_an_operator(request_, reason):
    result = check_request(CATALOG, request_, [], now=NOW)
    assert (result["verdict"], result["reasons"], result["operator_ack_required"]) == (PARK, [reason], True)


def test_same_profile_aborts():
    assert check_request(CATALOG, req(target="claude-sonnet-5-xhigh"), [], now=NOW)["verdict"] == ABORT


def receipt(lane, minutes_ago):
    return {"lane": lane, "ts_utc": iso(NOW - timedelta(minutes=minutes_ago)), "outcome": "applied"}


@pytest.mark.parametrize("history,reasons", [
    ([receipt("claude-rco-1", 30)], ["lane_budget_exhausted", "lane_cooldown"]),
    ([receipt("fable-5", 5), receipt("fable-5", 10), receipt("codex-tools-1", 15), receipt("codex-tools-1", 20)],
     ["fleet_budget_exhausted"]),
])
def test_budgets_and_cooldown_park(history, reasons):
    result = check_request(CATALOG, req(), history, now=NOW)
    assert result["verdict"] == PARK and result["reasons"] == reasons
    assert result["operator_ack_required"] is False


def test_cooldown_alone_parks():
    # fable-5 allows 2 relaunches per hour with a 30 min cooldown: budget ok, cooldown not.
    fable = req(lane="fable-5", current="claude-opus-5-5-medium", target="claude-opus-5-5-xhigh")
    result = check_request(CATALOG, fable, [receipt("fable-5", 20)], now=NOW)
    assert (result["verdict"], result["reasons"]) == (PARK, ["lane_cooldown"])
    assert check_request(CATALOG, fable, [receipt("fable-5", 31)], now=NOW)["verdict"] == PROCEED


def test_old_history_does_not_block():
    assert check_request(CATALOG, req(), [receipt("claude-rco-1", 120)], now=NOW)["verdict"] == PROCEED


@pytest.mark.parametrize("history", [None, [{"lane": "claude-rco-1"}], [{"ts_utc": iso(NOW)}], ["x"]])
def test_unknown_history_parks(history):
    result = check_request(CATALOG, req(), history, now=NOW)
    assert (result["verdict"], result["reasons"]) == (PARK, ["relaunch_history_unknown"])


def test_future_history_parks():
    result = check_request(CATALOG, req(), [receipt("fable-5", -30)], now=NOW)
    assert result["reasons"] == ["relaunch_history_from_the_future"]


# ------------------------------------------------------------ step 2

def state(**overrides):
    base = {"lane": "claude-rco-1", "is_supervisor": False, "observed_at": iso(NOW),
            "current_session_id": "sess-now", "idle": True, "pending_effects": False,
            "previous_turn_blocker": False, "open_claims": [], "unresolved_requests": []}
    base.update(overrides)
    return base


def test_clean_lane_is_at_a_safe_boundary():
    assert check_safe_boundary(state(), lane="claude-rco-1", now=NOW)["verdict"] == PROCEED


@pytest.mark.parametrize("change,reason", [
    (dict(idle=False), "not_idle"),
    (dict(idle=None), "not_idle"),
    (dict(pending_effects=True), "pending_effects_or_unknown"),
    (dict(pending_effects=None), "pending_effects_or_unknown"),
    (dict(previous_turn_blocker=True), "previous_turn_blocker_or_unknown"),
    (dict(open_claims=["claim-1"]), "open_claims"),
    (dict(open_claims=None), "open_claims_unknown"),
    (dict(unresolved_requests=None), "unresolved_requests_unknown"),
    # Lead LPS-B2: an old request bound to the current session still blocks.
    (dict(unresolved_requests=[{"request_id": "r", "bound_session_id": "sess-now", "age_hours": 40}]),
     "unresolved_request_bound_to_current_session"),
    (dict(unresolved_requests=[{"request_id": "r", "bound_session_id": "sess-old", "superseded": False}],
          session_lineage=[]), "unresolved_request_not_provably_superseded"),
    (dict(unresolved_requests=[{"request_id": "r", "bound_session_id": None}]), "request_binding_unknown"),
])
def test_unsafe_states_abort(change, reason):
    result = check_safe_boundary(state(**change), lane="claude-rco-1", now=NOW)
    assert result["verdict"] == ABORT and reason in result["reasons"]


LINEAGE = [{"session_id": "sess-old", "successor_session_id": "sess-mid"},
           {"session_id": "sess-mid", "successor_session_id": "sess-now"}]


def test_a_request_superseded_by_recorded_lineage_does_not_block():
    old = {"request_id": "r", "bound_session_id": "sess-old"}
    result = check_safe_boundary(state(unresolved_requests=[old], session_lineage=LINEAGE),
                                 lane="claude-rco-1", now=NOW)
    assert result["verdict"] == PROCEED


def test_a_caller_superseded_flag_is_never_trusted():
    # Lead review of #1738: superseded is derived from lineage, never a caller boolean.
    old = {"request_id": "r", "bound_session_id": "sess-old", "superseded": True}
    result = check_safe_boundary(state(unresolved_requests=[old], session_lineage=[]),
                                 lane="claude-rco-1", now=NOW)
    assert result["reasons"] == ["unresolved_request_not_provably_superseded"]


@pytest.mark.parametrize("start,lineage,expected", [
    ("sess-old", LINEAGE, True),
    ("sess-old", LINEAGE[:1], False),  # the chain stops before the current session
    ("sess-old", [{"session_id": "sess-other", "successor_session_id": "sess-now"}], False),  # not an ancestor
    ("sess-old", None, None),
    ("sess-old", "x", None),
    ("sess-old", [None], None),
    ("sess-old", [{"session_id": "sess-old"}], None),
    ("sess-old", [{"session_id": "sess-old", "successor_session_id": "sess-old"}], None),  # self-successor
    ("sess-old", LINEAGE + [{"session_id": "sess-old", "successor_session_id": "sess-fork"}], None),  # forked
    ("a", [{"session_id": "a", "successor_session_id": "b"}, {"session_id": "b", "successor_session_id": "a"}],
     None),  # cycle
    ("sess-old", [{"session_id": "sess-old", "successor_session_id": 7}], None),
])
def test_lineage_derivation(start, lineage, expected):
    from tools.wd_lane_relaunch import superseded_by_lineage
    assert superseded_by_lineage(start, "sess-now", lineage) is expected


def test_a_cyclic_lineage_is_unknown_and_blocks():
    cycle = [{"session_id": "sess-old", "successor_session_id": "x"},
             {"session_id": "x", "successor_session_id": "sess-old"}]
    old = {"request_id": "r", "bound_session_id": "sess-old"}
    result = check_safe_boundary(state(unresolved_requests=[old], session_lineage=cycle),
                                 lane="claude-rco-1", now=NOW)
    assert result["reasons"] == ["session_lineage_unknown"]


def test_a_long_but_finite_lineage_still_resolves():
    from tools.wd_lane_relaunch import MAX_LINEAGE_STEPS, superseded_by_lineage
    chain = [{"session_id": f"s{i}", "successor_session_id": f"s{i + 1}"} for i in range(MAX_LINEAGE_STEPS)]
    assert superseded_by_lineage("s0", f"s{MAX_LINEAGE_STEPS}", chain) is True
    longer = chain + [{"session_id": f"s{MAX_LINEAGE_STEPS}", "successor_session_id": "beyond"}]
    assert superseded_by_lineage("s0", "beyond", longer) is None


@pytest.mark.parametrize("change,reason", [
    (dict(lane="supervisor"), "lane_state_names_another_lane"),
    (dict(lane="claude-rco-2"), "lane_state_names_another_lane"),
    (dict(lane=None), "lane_state_names_another_lane"),
    (dict(is_supervisor=None), "target_is_or_may_be_the_supervisor"),
    (dict(observed_at=iso(NOW - timedelta(minutes=5))), "lane_state_stale_or_unknown"),
    (dict(observed_at=iso(NOW + timedelta(minutes=5))), "lane_state_stale_or_unknown"),
    (dict(current_session_id=""), "current_session_unknown"),
])
def test_boundary_preconditions(change, reason):
    assert check_safe_boundary(state(**change), lane="claude-rco-1", now=NOW)["reasons"] == [reason]


# ------------------------------------------------------------ planner (D5)

def binding(model="claude-sonnet-5", effort="xhigh", identity="valid", lane=None):
    return {"session_identity": identity, "observed_model_raw": model, "observed_effort": effort, "lane": lane}


CLAUDE_OK = {("claude", "claude"): "available"}


def plan(lane="claude-rco-1", **kwargs):
    args = dict(binding=binding(), admission="KEEP", quota_states=CLAUDE_OK, history=[], now=NOW)
    args.update(kwargs)
    if isinstance(args["binding"], dict) and args["binding"].get("lane") is None:
        args["binding"] = dict(args["binding"], lane=lane)
    return plan_lane(CATALOG, DIGEST, lane, **args)


def test_profile_lookup_and_bucket_state():
    assert profile_for_observation(CATALOG, "claude-rco-1", "claude-sonnet-5", "xhigh") == "claude-sonnet-5-xhigh"
    assert profile_for_observation(CATALOG, "claude-rco-1", "claude-sonnet-5", "low") is None
    assert bucket_state(CATALOG, "claude-sonnet-5-xhigh", {}) == "unknown"
    assert bucket_state(CATALOG, "claude-sonnet-5-xhigh", {("claude", "claude"): "weird"}) == "unknown"


def test_keep_when_healthy():
    decision = plan()
    assert decision["action"] == KEEP and decision["mode"] == "shadow"
    assert decision["execution_allowed"] is False and decision["catalog_sha256"] == DIGEST


def test_escalate_raises_a_reviewer_as_a_shadow_decision():
    decision = plan(admission="ESCALATE")
    assert decision["action"] == WOULD_RELAUNCH
    assert (decision["current_profile"], decision["target_profile"]) == (
        "claude-sonnet-5-xhigh", "claude-opus-5-5-xhigh")


def test_observed_context_suffix_maps_to_the_profile():
    decision = plan(lane="fable-5", binding=binding(model="claude-opus-5-5[1m]", effort="medium"),
                    admission="ESCALATE")
    assert decision["target_profile"] == "claude-opus-5-5-xhigh"


def test_shared_claude_bucket_exhaustion_has_no_escape():
    decision = plan(quota_states={("claude", "claude"): "exhausted"})
    assert decision["action"] == "park"
    assert decision["reasons"] == ["current_bucket_exhausted", "no_admissible_candidate"]
    assert decision["rejected"] == ["claude-opus-5-5-xhigh:bucket_exhausted"]


def test_reviewer_is_never_lowered_even_when_its_bucket_is_limited():
    decision = plan(binding=binding(model="claude-opus-5-5", effort="xhigh"),
                    quota_states={("claude", "claude"): "limited"})
    assert decision["action"] == "park"
    assert decision["rejected"] == []


@pytest.mark.parametrize("kwargs,reason", [
    (dict(binding=binding(identity="unbound")), "current_profile_unverified"),
    (dict(binding=None), "current_profile_unverified"),
    (dict(binding=binding(model="mystery")), "current_profile_not_in_catalog"),
    (dict(admission="PARK"), "admission_park"),
    (dict(admission="maybe"), "admission_unknown"),
])
def test_planner_parks_without_verified_inputs(kwargs, reason):
    decision = plan(**kwargs)
    assert decision["action"] == "park" and reason in decision["reasons"]


def test_planner_respects_budgets():
    history = [{"lane": "claude-rco-1", "ts_utc": iso(NOW - timedelta(minutes=10)), "outcome": "applied"}]
    decision = plan(admission="ESCALATE", history=history)
    assert decision["action"] == "park"
    assert decision["rejected"] == ["claude-opus-5-5-xhigh:lane_budget_exhausted+lane_cooldown"]


def test_planner_never_proposes_an_unknown_bucket():
    decision = plan(admission="ESCALATE", quota_states={})
    assert decision["action"] == "park"


def test_codex_exhaustion_is_a_shared_bucket_too():
    decision = plan(lane="codex-tools-1", binding=binding(model="gpt-5.6-terra", effort="medium"),
                    quota_states={("codex", "codex"): "exhausted"})
    assert decision["action"] == "park"
    assert decision["rejected"] == ["codex-gpt-6-sol-high:bucket_exhausted"]


def test_decisions_are_json_serialisable():
    json.dumps(plan(admission="ESCALATE"))


def distinct_bucket_catalog(floor: int = 1) -> dict:
    """codex-tools-1 with three profiles, each on its own quota bucket."""
    from tools.lane_profile_catalog import validate_catalog
    catalog = json.loads(json.dumps(CATALOG))
    policy = catalog["capacity_policy"]
    base = policy["profiles"]["codex-gpt-5.6-terra-medium"]
    ids = ["codex-a", "codex-b", "codex-c"]
    for name, model in zip(ids, ["model-a", "model-b", "model-c"]):
        policy["profiles"][name] = dict(base, model=model, limits=[{"id": name, "windows": ["primary"]}])
    policy["agents"]["codex-tools-1"]["profiles"] = ids
    catalog["lanes"]["codex-tools-1"].update(allowed_profiles=ids, floor=floor, default="codex-b")
    return validate_catalog(catalog)


def plan_tools(current_model, admission, states, floor=1):
    catalog = distinct_bucket_catalog(floor)
    return plan_lane(catalog, DIGEST, "codex-tools-1", binding=binding(model=current_model, effort="medium", lane="codex-tools-1"),
                     admission=admission, quota_states=states, history=[], now=NOW)


def test_planner_never_proposes_below_the_floor():
    # Current a (strongest) exhausted; b below? b is index 1 = floor, c is below the floor.
    states = {("codex", "codex-a"): "exhausted", ("codex", "codex-b"): "exhausted",
              ("codex", "codex-c"): "available"}
    decision = plan_tools("model-a", "KEEP", states)
    assert decision["action"] == "park"
    assert decision["rejected"] == ["codex-b:bucket_exhausted"]


def test_planner_picks_the_strongest_available_within_the_floor():
    states = {("codex", "codex-a"): "exhausted", ("codex", "codex-b"): "available",
              ("codex", "codex-c"): "available"}
    decision = plan_tools("model-a", "KEEP", states)
    assert (decision["action"], decision["target_profile"]) == (WOULD_RELAUNCH, "codex-b")


def test_escalate_only_ever_raises():
    # Current b; ESCALATE may only go to a, which is exhausted; c is weaker and never offered.
    catalog_states = {("codex", "codex-a"): "exhausted", ("codex", "codex-b"): "available",
                      ("codex", "codex-c"): "available"}
    # floor=2 puts c inside the floor, so only the escalate rule keeps it out.
    decision = plan_tools("model-b", "ESCALATE", catalog_states, floor=2)
    assert decision["action"] == "park"
    assert decision["rejected"] == ["codex-a:bucket_exhausted"]



# ---------------------------------------------------------------- Lead review of #1738

def test_supervisor_is_never_a_target_even_when_the_state_agrees():
    result = check_safe_boundary(state(lane="supervisor"), lane="supervisor", now=NOW)
    assert result["reasons"] == ["target_is_or_may_be_the_supervisor"]


@pytest.mark.parametrize("other", ["claude-rco-2", "codex-lead-1"])
def test_planner_refuses_a_binding_for_another_lane(other):
    decision = plan(binding=binding(lane=other))
    assert (decision["action"], decision["reasons"]) == ("park", ["binding_names_another_lane"])


def test_planner_refuses_a_binding_without_a_lane():
    decision = plan_lane(CATALOG, DIGEST, "claude-rco-1",
                         binding={"session_identity": "valid", "observed_model_raw": "claude-sonnet-5",
                                  "observed_effort": "xhigh"},
                         admission="KEEP", quota_states=CLAUDE_OK, history=[], now=NOW)
    assert decision["reasons"] == ["binding_names_another_lane"]


def test_planner_accepts_its_own_lane_binding():
    assert plan(binding=binding(lane="claude-rco-1"))["action"] == "keep"


HOSTILE = [None, True, 0, 1.5, "", "x", [], ["x"], {}, {"a": 1}, float("nan"), object()]
BOUNDARY_FIELDS = ["lane", "is_supervisor", "observed_at", "current_session_id", "idle", "pending_effects",
                   "previous_turn_blocker", "open_claims", "unresolved_requests", "session_lineage"]


@pytest.mark.parametrize("field", BOUNDARY_FIELDS)
@pytest.mark.parametrize("value", HOSTILE, ids=repr)
def test_hostile_boundary_fields_never_raise_and_never_proceed_unsafely(field, value):
    old = {"request_id": "r", "bound_session_id": "sess-old"}
    measured = state(unresolved_requests=[old], session_lineage=LINEAGE)
    measured[field] = value
    result = check_safe_boundary(measured, lane="claude-rco-1", now=NOW)
    valid = ((field in ("open_claims", "unresolved_requests") and type(value) is list and value == [])
             or (field == "idle" and value is True))
    assert result["verdict"] == (PROCEED if valid else ABORT)


def test_a_valid_state_with_lineage_is_the_success_twin():
    old = {"request_id": "r", "bound_session_id": "sess-old"}
    measured = state(unresolved_requests=[old], session_lineage=LINEAGE)
    assert check_safe_boundary(measured, lane="claude-rco-1", now=NOW)["verdict"] == PROCEED


@pytest.mark.parametrize("value", HOSTILE, ids=repr)
def test_hostile_request_items_block(value):
    result = check_safe_boundary(state(unresolved_requests=[value], session_lineage=LINEAGE),
                                 lane="claude-rco-1", now=NOW)
    assert result["verdict"] == ABORT


@pytest.mark.parametrize("value", HOSTILE, ids=repr)
def test_hostile_state_and_lane_arguments_never_raise(value):
    assert check_safe_boundary(value, lane="claude-rco-1", now=NOW)["verdict"] == ABORT
    assert check_safe_boundary(state(), lane=value, now=NOW)["verdict"] == ABORT


@pytest.mark.parametrize("value", [-1, 1.5, None, True, "60"], ids=repr)
def test_hostile_max_age_fails_closed(value):
    assert check_safe_boundary(state(), lane="claude-rco-1", now=NOW, max_age_seconds=value)["verdict"] == ABORT


def test_max_age_zero_accepts_a_same_instant_measurement():
    assert check_safe_boundary(state(), lane="claude-rco-1", now=NOW, max_age_seconds=0)["verdict"] == PROCEED


REQUEST = {"lane": "claude-rco-1", "current_profile": "claude-sonnet-5-xhigh",
           "target_profile": "claude-opus-5-5-xhigh"}


@pytest.mark.parametrize("field", ["lane", "current_profile", "target_profile"])
@pytest.mark.parametrize("value", HOSTILE, ids=repr)
def test_hostile_request_fields_park_and_never_raise(field, value):
    req = dict(REQUEST)
    req[field] = value
    assert check_request(CATALOG, req, [], now=NOW)["verdict"] != PROCEED


@pytest.mark.parametrize("value", HOSTILE, ids=repr)
def test_hostile_requests_and_histories_never_raise(value):
    assert check_request(CATALOG, value, [], now=NOW)["verdict"] == PARK
    verdict = check_request(CATALOG, dict(REQUEST), value, now=NOW)["verdict"]
    assert verdict == (PROCEED if type(value) is list and value == [] else PARK)
    assert check_request(CATALOG, dict(REQUEST), [value], now=NOW)["verdict"] == PARK


# ---------------------------------------------------------------- rco-2 N4: hostile planner inputs

@pytest.mark.parametrize("field", ["lane", "binding", "admission", "quota_states", "history"])
@pytest.mark.parametrize("value", HOSTILE, ids=repr)
def test_hostile_planner_arguments_never_raise_and_never_propose(field, value):
    args = dict(binding=binding(lane="claude-rco-1"), admission="ESCALATE", quota_states=CLAUDE_OK, history=[],
                now=NOW)
    lane = "claude-rco-1"
    if field == "lane":
        lane = value
    else:
        args[field] = value
    decision = plan_lane(CATALOG, DIGEST, lane, **args)
    # ESCALATE with a healthy bucket would propose opus; only the unchanged valid history ([]) may still do so.
    allowed = {"park", "would_relaunch"} if (field == "history" and type(value) is list and value == []) \
        else {"park"}
    assert decision["action"] in allowed and decision["execution_allowed"] is False


@pytest.mark.parametrize("field", ["session_identity", "observed_model_raw", "observed_effort", "lane"])
@pytest.mark.parametrize("value", HOSTILE, ids=repr)
def test_hostile_binding_fields_park(field, value):
    hostile = binding(lane="claude-rco-1")
    hostile[field] = value
    decision = plan_lane(CATALOG, DIGEST, "claude-rco-1", binding=hostile, admission="ESCALATE",
                         quota_states=CLAUDE_OK, history=[], now=NOW)  # not plan(): it fills a missing lane
    assert decision["action"] == "park"


def test_the_escalate_success_twin_still_proposes():
    decision = plan(binding=binding(lane="claude-rco-1"), admission="ESCALATE")
    assert (decision["action"], decision["target_profile"]) == ("would_relaunch", "claude-opus-5-5-xhigh")
