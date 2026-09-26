# SPDX-License-Identifier: BUSL-1.1
"""Boundary, lineage and planner cases the main relaunch suite leaves to chance.

Each case pins behaviour that a one-token mutation of the checks or the planner would otherwise change
silently: exact window and cooldown edges, which receipt the cooldown is measured from, the session type,
the lineage step bound and what makes a lineage well formed, and the planner's candidate order and bucket rule.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tools.lane_profile_binding import _base_model
from tools.lane_profile_catalog import load_catalog, validate_catalog
from tools.wd_lane_profile_planner import WOULD_RELAUNCH, bucket_state, plan_lane, profile_for_observation
from tools.wd_lane_relaunch import ABORT, MAX_LINEAGE_STEPS, PARK, PROCEED, check_request, check_safe_boundary, superseded_by_lineage

ROOT = Path(__file__).resolve().parents[2]
CATALOG, DIGEST = load_catalog(ROOT / "configs" / "lane_profile_catalog.json")
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def receipt(lane, minutes_ago=0, seconds_ago=0):
    return {"lane": lane, "ts_utc": iso(NOW - timedelta(minutes=minutes_ago, seconds=seconds_ago)),
            "outcome": "source_stopped"}


RAISE = {"lane": "claude-rco-1", "current_profile": "claude-sonnet-5-xhigh", "target_profile": "claude-opus-5-5-xhigh"}
FABLE = {"lane": "fable-5", "current_profile": "claude-opus-5-5-medium", "target_profile": "claude-opus-5-5-xhigh"}


# ---------------------------------------------------------------- step 1 boundaries

def test_lane_budget_window_is_exclusive_at_exactly_one_hour():
    # claude-rco-1 allows 1 relaunch per hour with a 3600 s cooldown: a receipt exactly one hour old is outside
    # both, one second younger is inside both.
    assert check_request(CATALOG, RAISE, [receipt("claude-rco-1", 60)], now=NOW)["verdict"] == PROCEED
    inside = check_request(CATALOG, RAISE, [receipt("claude-rco-1", 59, 59)], now=NOW)
    assert (inside["verdict"], inside["reasons"]) == (PARK, ["lane_budget_exhausted", "lane_cooldown"])


def test_fleet_budget_window_is_exclusive_at_exactly_one_hour():
    others = ["fable-5", "fable-5", "codex-tools-1", "codex-tools-1"]
    assert check_request(CATALOG, RAISE, [receipt(l, 60) for l in others], now=NOW)["verdict"] == PROCEED
    inside = check_request(CATALOG, RAISE, [receipt(l, 59, 59) for l in others], now=NOW)
    assert (inside["verdict"], inside["reasons"]) == (PARK, ["fleet_budget_exhausted"])


def test_cooldown_is_satisfied_at_exactly_the_cooldown():
    # fable-5: 2 per hour, 1800 s cooldown. Exactly 1800 s is enough; one second short is not.
    assert check_request(CATALOG, FABLE, [receipt("fable-5", 30)], now=NOW)["verdict"] == PROCEED
    short = check_request(CATALOG, FABLE, [receipt("fable-5", 29, 59)], now=NOW)
    assert (short["verdict"], short["reasons"]) == (PARK, ["lane_cooldown"])


def test_cooldown_is_measured_from_the_newest_receipt_not_the_oldest():
    result = check_request(CATALOG, FABLE, [receipt("fable-5", 100), receipt("fable-5", 20)], now=NOW)
    assert (result["verdict"], result["reasons"]) == (PARK, ["lane_cooldown"])


# ---------------------------------------------------------------- step 2

def state(**overrides):
    base = {"lane": "claude-rco-1", "is_supervisor": False, "observed_at": iso(NOW), "current_session_id": "cur",
            "idle": True, "pending_effects": False, "previous_turn_blocker": False, "open_claims": [],
            "unresolved_requests": [], "session_lineage": []}
    base.update(overrides)
    return base


@pytest.mark.parametrize("session", [None, 123, ["cur"]])
def test_a_session_id_that_is_not_a_string_aborts(session):
    result = check_safe_boundary(state(current_session_id=session), lane="claude-rco-1", now=NOW)
    assert (result["verdict"], result["reasons"]) == (ABORT, ["current_session_unknown"])


@pytest.mark.parametrize("bad_target", [None, "", ["x"], 5])
def test_a_request_without_a_usable_target_is_malformed_not_a_catalog_question(bad_target):
    result = check_request(CATALOG, dict(RAISE, target_profile=bad_target), [], now=NOW)
    assert (result["verdict"], result["reasons"]) == (PARK, ["request_malformed"])


@pytest.mark.parametrize("bad_current", [5, ["x"], {"a": 1}, True])
def test_a_current_profile_that_is_not_a_string_or_null_is_malformed(bad_current):
    result = check_request(CATALOG, dict(RAISE, current_profile=bad_current), [], now=NOW)
    assert (result["verdict"], result["reasons"]) == (PARK, ["request_malformed"])


# ---------------------------------------------------------------- lineage


def row(a, b):
    return {"session_id": a, "successor_session_id": b}


def chain(n):
    return [row(f"s{i}", f"s{i + 1}") for i in range(n)]


def test_a_lineage_chain_of_exactly_the_step_bound_resolves_and_one_more_is_unknown():
    assert MAX_LINEAGE_STEPS == 64
    assert superseded_by_lineage("s0", "s64", chain(64)) is True
    assert superseded_by_lineage("s0", "s65", chain(65)) is None


@pytest.mark.parametrize("lineage", [
    [row("old", "cur"), row("cur", "cur")],   # the current session is its own successor
    [row("old", "cur"), row("x", "x")],       # a self-successor row elsewhere
], ids=["current-self-successor", "elsewhere-self-successor"])
def test_a_self_successor_row_makes_the_whole_lineage_unknown(lineage):
    assert superseded_by_lineage("old", "cur", lineage) is None


@pytest.mark.parametrize("lineage", [
    [row("old", "cur"), row("cur", "newer")],   # the measured current session already has a successor
    [row("a", "b"), row("b", "a")],             # a cycle through the current session
], ids=["current-has-a-successor", "cycle-through-current"])
def test_a_lineage_whose_head_is_not_the_current_session_is_unknown(lineage):
    bound, current = ("old", "cur") if lineage[0]["session_id"] == "old" else ("a", "b")
    assert superseded_by_lineage(bound, current, lineage) is None


def test_such_a_lineage_blocks_the_boundary_check():
    old_request = {"request_id": "r", "bound_session_id": "old"}
    lineage = [row("old", "cur"), row("cur", "newer")]
    result = check_safe_boundary(state(unresolved_requests=[old_request], session_lineage=lineage),
                                 lane="claude-rco-1", now=NOW)
    assert (result["verdict"], result["reasons"]) == (ABORT, ["session_lineage_unknown"])
    # Control: the same request with a well-formed lineage that ends at the current session proceeds.
    clean = check_safe_boundary(state(unresolved_requests=[old_request], session_lineage=[row("old", "cur")]),
                                lane="claude-rco-1", now=NOW)
    assert clean["verdict"] == PROCEED


# ---------------------------------------------------------------- planner

def binding(model, effort="medium", lane="codex-tools-1"):
    return {"session_identity": "valid", "lane": lane, "observed_model_raw": model, "observed_effort": effort}


def test_planner_parks_a_lane_that_is_not_in_the_catalog():
    decision = plan_lane(CATALOG, DIGEST, "grok-scout-1", binding=binding("claude-sonnet-5", "xhigh", "grok-scout-1"),
                         admission="KEEP", quota_states={("claude", "claude"): "available"}, history=[], now=NOW)
    assert (decision["action"], decision["reasons"]) == ("park", ["lane_not_in_catalog"])


def three_profile_catalog(limits_of=None):
    """codex-tools-1 with profiles a > b > c, floor 2, each on its own bucket; limits_of overrides a profile's limits."""
    catalog = json.loads(json.dumps(CATALOG))
    policy = catalog["capacity_policy"]
    base = policy["profiles"]["codex-gpt-5.6-terra-medium"]
    ids = ["codex-a", "codex-b", "codex-c"]
    for name, model in zip(ids, ["model-a", "model-b", "model-c"]):
        limits = (limits_of or {}).get(name) or [{"id": name, "windows": ["primary"]}]
        policy["profiles"][name] = dict(base, model=model, limits=limits)
    policy["agents"]["codex-tools-1"]["profiles"] = ids
    catalog["lanes"]["codex-tools-1"].update(allowed_profiles=ids, floor=2, default="codex-b")
    return validate_catalog(catalog)


def test_planner_tries_the_strongest_candidate_first():
    states = {("codex", "codex-a"): "available", ("codex", "codex-b"): "available", ("codex", "codex-c"): "exhausted"}
    decision = plan_lane(three_profile_catalog(), DIGEST, "codex-tools-1", binding=binding("model-c"),
                         admission="KEEP", quota_states=states, history=[], now=NOW)
    assert (decision["action"], decision["target_profile"]) == (WOULD_RELAUNCH, "codex-a")


def test_a_profile_with_several_buckets_is_as_weak_as_its_weakest_bucket():
    catalog = three_profile_catalog(limits_of={"codex-a": [{"id": "a1", "windows": ["primary"]},
                                                           {"id": "a2", "windows": ["primary"]}]})
    assert bucket_state(catalog, "codex-a", {("codex", "a1"): "available", ("codex", "a2"): "available"}) == "available"
    assert bucket_state(catalog, "codex-a", {("codex", "a1"): "exhausted", ("codex", "a2"): "available"}) == "exhausted"
    assert bucket_state(catalog, "codex-a", {("codex", "a1"): "available", ("codex", "a2"): "exhausted"}) == "exhausted"
    assert bucket_state(catalog, "codex-a", {("codex", "a1"): "limited", ("codex", "a2"): "available"}) == "limited"
    assert bucket_state(catalog, "codex-a", {("codex", "a1"): "available"}) == "unknown"


@pytest.mark.parametrize("current_bucket", [{}, {("codex", "codex-b"): "unknown"}, {("codex", "codex-b"): ["available"]}])
def test_keep_with_an_unmeasured_current_bucket_parks_instead_of_proposing_a_relaunch(current_bucket):
    states = {("codex", "codex-a"): "available", **current_bucket}
    decision = plan_lane(three_profile_catalog(), DIGEST, "codex-tools-1", binding=binding("model-b"),
                         admission="KEEP", quota_states=states, history=[], now=NOW)
    assert (decision["action"], decision["reasons"]) == ("park", ["current_bucket_unknown"])
    assert "target_profile" not in decision


def test_a_measured_exhausted_current_bucket_still_finds_the_available_profile():
    states = {("codex", "codex-a"): "available", ("codex", "codex-b"): "exhausted"}
    decision = plan_lane(three_profile_catalog(), DIGEST, "codex-tools-1", binding=binding("model-b"),
                         admission="KEEP", quota_states=states, history=[], now=NOW)
    assert (decision["action"], decision["target_profile"]) == ("would_relaunch", "codex-a")


@pytest.mark.parametrize("model", ["claude-sonnet-5", "claude-sonnet-5[1m]", "claude-sonnet-5[evil]",
                                   "claude-sonnet-5[a]b]", "claude-sonnet-5[[1m]", "claude-sonnet-5[1m][2m]",
                                   "claude-sonnet-5[]", "[1m]", "", None, ["claude-sonnet-5"]])
def test_the_planner_maps_a_model_name_exactly_as_the_binding_module_does(model):
    expected = "claude-sonnet-5-xhigh" if _base_model(model) == "claude-sonnet-5" else None
    assert profile_for_observation(CATALOG, "claude-rco-1", _base_model(model), "xhigh") == expected
    decision = plan_lane(CATALOG, DIGEST, "claude-rco-1", binding=binding(model, "xhigh", "claude-rco-1"),
                         admission="KEEP", quota_states={("claude", "claude"): "available"}, history=[], now=NOW)
    assert decision.get("current_profile") == expected
