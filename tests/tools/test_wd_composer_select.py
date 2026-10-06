"""F24 composer selection (plan 2.10). AUTHORED, NOT RUN: tests are forbidden by the operator directive
until the final signature; the expected values below are hand-derived and the first isolated run must
confirm them. Every number here is SYNTHETIC fixture data, not a benchmark claim about a real model."""
from __future__ import annotations

import ast
import copy
from pathlib import Path

import pytest

import tools.wd_composer_select as cs

NOW = "2026-09-29T21:00:00Z"
FRESH = "2026-09-29T20:59:00Z"
POLICY = "a" * 64


def entry(provider, model, effort, score, **over):
    row = {"provider": provider, "model": model, "effort": effort, "score": score, "uncertainty": 0.5,
           "coding_score": 70.0, "measured_on": "2026-09-20"}
    row.update(over)
    return row


def snapshot(*rows, **over):
    snap = {"schema": cs.SNAPSHOT_SCHEMA, "index_name": "synthetic-index", "index_version": "1",
            "entries": list(rows) or [entry("claude", "model-a", "xhigh", 60.0),
                                      entry("codex", "model-b", "high", 55.0, coding_score=65.0),
                                      entry("grok", "model-c", "high", 50.0, coding_score=None)]}
    snap.update(over)
    return snap


def pool_block(pid="a", provider="claude", **over):
    row = {"profile_id": pid, "provider": provider, "pool_id": provider + "/pool", "state": "available",
           "observed_utc": FRESH, "projected_used_percent": 40.0, "provider_up": True,
           "valid_until_utc": "2026-09-29T21:30:00Z"}
    row.update(over)
    return row


def cost_block(value=1.0, pool_id="claude/pool", **over):
    row = {"value": value, "unit": "percent_of_pool", "pool_id": pool_id, "window": "5h",
           "workload": "task:compose", "observed_utc": FRESH}
    row.update(over)
    return row


def profile(pid, provider, model, effort, **over):
    """Every receipt names this profile: a fresh positive receipt about another subject proves nothing."""
    row = {"profile_id": pid, "provider": provider, "model": model, "effort": effort, "signed_in_envelope": True,
           "identity": {"verified": True, "observed_utc": FRESH, "profile_id": pid, "provider": provider,
                        "model": model, "effort": effort},
           "auth": {"verified": True, "observed_utc": FRESH, "profile_id": pid},
           "turn": {"ok": True, "observed_utc": FRESH, "profile_id": pid},
           "pool": pool_block(pid, provider),
           "measured_quota_cost": cost_block(pool_id=provider + "/pool")}
    row.update(over)
    return row


def profiles():
    return [profile("a", "claude", "model-a", "xhigh"), profile("b", "codex", "model-b", "high"),
            profile("c", "grok", "model-c", "high")]


def evidence(snap=None, rows=None, **over):
    snap = snapshot() if snap is None else snap
    ev = {"schema": cs.EVIDENCE_SCHEMA, "now_utc": NOW,
          "f0": {"feature": "F24", "enabled": True, "reason": "enabled by the signed policy",
                 "policy_sha256": POLICY, "revocation_version": 1},
          "policy_bits": {"policy_sha256": POLICY, "f24_composer_rule": True},
          "parameters": {"policy_sha256": POLICY, "index_name": "synthetic-index", "index_version": "1",
                         "registry_sha256": cs.digest(snap), "epsilon": 1.0, "plausibility_bound": 5.0,
                         "max_evidence_age_seconds": 900, "max_score_age_days": 30, "max_wait_seconds": 600,
                         "budget_mode": "steady", "planning_efforts": ["high", "xhigh"]},
          "registry_snapshot": snap,
          "task": {"task_id": "task-1", "created_utc": NOW},
          "profiles": profiles() if rows is None else rows}
    ev.update(over)
    return ev


def run(ev):
    out = cs.select(ev)
    assert out["schema"] == cs.SCHEMA and out["execution_allowed"] is False and out["authority"] == "none"
    return out


def with_profile(pid, **over):
    rows = profiles()
    for row in rows:
        if row["profile_id"] == pid:
            row.update(over)
    return rows


def test_success_twin_selects_the_ranked_top():
    out = run(evidence())
    assert out["verdict"] == cs.COMPOSER and out["selected_profile"] == "a" and out["route"] == "direct"
    assert out["ranking"] == ["a", "b", "c"] and out["ineligible"] == {} and out["unavailable"] == {}
    assert out["provisional_profile"] is None and out["registry_sha256"] == cs.digest(snapshot())
    assert out["inputs_digest"] == cs.digest(evidence())


def test_deterministic_and_input_order_independent():
    assert run(evidence()) == run(evidence())
    reordered = run(evidence(rows=list(reversed(profiles()))))
    base = run(evidence())
    for key in ("verdict", "selected_profile", "ranking", "ineligible", "unavailable"):
        assert reordered[key] == base[key]


def mutate(path, value):
    def apply(ev):
        node = ev
        for part in path[:-1]:
            node = node[part]
        if value is DELETE:
            node.pop(path[-1])
        else:
            node[path[-1]] = value
        return ev
    return apply


DELETE = object()


@pytest.mark.parametrize("change,reason", [
    (mutate(("f0",), DELETE), "feature_disabled"),
    (mutate(("f0", "enabled"), False), "feature_disabled"),
    (mutate(("f0", "enabled"), 1), "feature_disabled"),
    (mutate(("f0", "feature"), "F15"), "feature_disabled"),
    (mutate(("f0", "policy_sha256"), None), "f0_policy_unbound"),
    (mutate(("policy_bits",), DELETE), "feature_disabled"),
    (mutate(("policy_bits", "f24_composer_rule"), False), "policy_bit_off:f24_composer_rule"),
    (mutate(("policy_bits", "policy_sha256"), "b" * 64), "feature_disabled"),
    (mutate(("parameters", "policy_sha256"), "b" * 64), "parameters_unbound"),
    (mutate(("parameters", "epsilon"), -1), "parameters_invalid"),
    (mutate(("parameters", "epsilon"), True), "parameters_invalid"),
    (mutate(("parameters", "plausibility_bound"), 0), "parameters_invalid"),
    (mutate(("parameters", "max_evidence_age_seconds"), 0), "parameters_invalid"),
    (mutate(("parameters", "max_evidence_age_seconds"), 1.5), "parameters_invalid"),
    (mutate(("parameters", "max_score_age_days"), DELETE), "parameters_invalid"),
    (mutate(("parameters", "max_wait_seconds"), -1), "parameters_invalid"),
    (mutate(("parameters", "budget_mode"), "turbo"), "parameters_invalid"),
    (mutate(("parameters", "planning_efforts"), []), "parameters_invalid"),
    (mutate(("parameters", "planning_efforts"), "high"), "parameters_invalid"),
    (mutate(("parameters", "index_name"), ""), "parameters_invalid"),
    (mutate(("parameters", "registry_sha256"), "x"), "parameters_invalid"),
    (mutate(("schema",), "wd.composer-evidence.v0"), "evidence_malformed"),
    (mutate(("now_utc",), "2026-09-29T21:00:00"), "now_utc"),
    (mutate(("profiles",), {}), "profiles"),
    (mutate(("profiles",), []), "no_eligible_profile"),
])
def test_default_off_and_malformed_inputs_hold(change, reason):
    out = run(change(evidence()))
    assert out["verdict"] == cs.HOLD and reason in out["reasons"]
    assert out["selected_profile"] is None and out["provisional_profile"] is None and out["route"] is None


FRESH_BLOCK_STALE = "2026-09-29T20:00:00Z"
FUTURE = "2026-09-29T21:05:00Z"


@pytest.mark.parametrize("over,reason", [
    ({"signed_in_envelope": False}, "not_in_signed_envelope"),
    ({"signed_in_envelope": "true"}, "not_in_signed_envelope"),
    ({"identity": {"verified": False, "observed_utc": FRESH, "provider": "claude", "model": "model-a",
                   "effort": "xhigh"}}, "identity_unverified_or_stale"),
    ({"identity": {"verified": True, "observed_utc": FRESH_BLOCK_STALE, "provider": "claude",
                   "model": "model-a", "effort": "xhigh"}}, "identity_unverified_or_stale"),
    ({"identity": {"verified": True, "observed_utc": FUTURE, "provider": "claude", "model": "model-a",
                   "effort": "xhigh"}}, "identity_unverified_or_stale"),
    ({"identity": None}, "identity_unverified_or_stale"),
    # A claimed-but-unmeasured runtime identity never counts: the measured model/effort must match exactly.
    ({"identity": {"verified": True, "observed_utc": FRESH, "provider": "claude", "model": "model-z",
                   "effort": "xhigh"}}, "identity_mismatch"),
    ({"identity": {"verified": True, "observed_utc": FRESH, "provider": "claude", "model": "model-a",
                   "effort": "high"}}, "identity_mismatch"),
    ({"auth": None}, "auth_unverified_or_stale"),
    ({"auth": {"verified": True, "observed_utc": FRESH_BLOCK_STALE}}, "auth_unverified_or_stale"),
    ({"auth": {"verified": "yes", "observed_utc": FRESH}}, "auth_unverified_or_stale"),
    ({"turn": None}, "turn_unknown_or_stale"),
    ({"turn": {"ok": True, "observed_utc": FRESH_BLOCK_STALE}}, "turn_unknown_or_stale"),
    ({"turn": {"ok": "yes", "observed_utc": FRESH}}, "turn_unknown_or_stale"),
    ({"pool": None}, "quota_unknown_or_stale"),
    ({"pool": pool_block(observed_utc=FRESH_BLOCK_STALE)}, "quota_unknown_or_stale"),
    ({"pool": pool_block(state="unknown")}, "quota_unknown_or_stale"),
    ({"pool": pool_block(pool_id="")}, "quota_unknown_or_stale"),
    ({"pool": pool_block(projected_used_percent="40")}, "quota_unknown_or_stale"),
    ({"pool": pool_block(projected_used_percent=-1)}, "quota_unknown_or_stale"),
    ({"pool": pool_block(provider_up="yes")}, "quota_unknown_or_stale"),
    # The adapter's bound (receipt age and F3 pool TTL): passed, missing or malformed is unknown quota.
    ({"pool": pool_block(valid_until_utc=NOW)}, "quota_unknown_or_stale"),
    ({"pool": pool_block(valid_until_utc=None)}, "quota_unknown_or_stale"),
    ({"pool": {k: v for k, v in pool_block().items() if k != "valid_until_utc"}}, "quota_unknown_or_stale"),
    ({"pool": pool_block(projected_used_percent=70.5)}, "budget_over_trip_line"),
    # RCO/Tools unbound-eligibility: fresh positive receipts about another subject are refused.
    ({"identity": {"verified": True, "observed_utc": FRESH, "profile_id": "b", "provider": "claude",
                   "model": "model-a", "effort": "xhigh"}}, "identity_mismatch"),
    ({"auth": {"verified": True, "observed_utc": FRESH, "profile_id": "b"}}, "auth_unbound"),
    ({"auth": {"verified": True, "observed_utc": FRESH}}, "auth_unbound"),
    ({"turn": {"ok": True, "observed_utc": FRESH, "profile_id": "b"}}, "turn_unbound"),
    ({"pool": pool_block(pid="b")}, "quota_unbound"),
    ({"pool": pool_block(provider="codex", pool_id="claude/pool")}, "quota_unbound"),
])
def test_missing_or_stale_evidence_is_ineligible_never_a_fallback(over, reason):
    out = run(evidence(rows=with_profile("a", **over)))
    assert out["verdict"] == cs.COMPOSER and out["selected_profile"] == "b"
    assert reason in out["ineligible"]["a"] and "a" not in out["ranking"]


def test_effort_outside_the_planning_class_is_ineligible():
    rows = [profile("a", "claude", "model-a", "low")] + profiles()[1:]
    out = run(evidence(rows=rows))
    assert out["selected_profile"] == "b" and "effort_not_allowed_for_planning" in out["ineligible"]["a"]


def test_budget_mode_changes_the_trip_line():
    rows = with_profile("a", pool=pool_block(projected_used_percent=85.0))
    assert run(evidence(rows=rows))["selected_profile"] == "b"
    ev = evidence(rows=rows)
    ev["parameters"]["budget_mode"] = "burst"
    assert run(ev)["selected_profile"] == "a"


def test_no_eligible_profile_holds():
    rows = [dict(p, signed_in_envelope=False) for p in profiles()]
    out = run(evidence(rows=rows))
    assert out["verdict"] == cs.HOLD and out["reasons"] == ["no_eligible_profile"]
    assert sorted(out["ineligible"]) == ["a", "b", "c"]


def tie_snapshot(a_over, b_over):
    return snapshot(entry("claude", "model-a", "xhigh", 60.0, **a_over),
                    entry("codex", "model-b", "high", 59.5, **b_over),
                    entry("grok", "model-c", "high", 50.0, coding_score=None))


def test_epsilon_tie_breaks_on_coding_index():
    out = run(evidence(snap=tie_snapshot({"coding_score": 70.0}, {"coding_score": 75.0})))
    assert out["selected_profile"] == "b" and out["ranking"] == ["b", "a", "c"]


def test_overlapping_uncertainty_is_a_tie():
    snap = snapshot(entry("claude", "model-a", "xhigh", 60.0, uncertainty=3.0, coding_score=70.0),
                    entry("codex", "model-b", "high", 56.0, uncertainty=2.0, coding_score=75.0))
    out = run(evidence(snap=snap, rows=profiles()[:2]))
    assert out["selected_profile"] == "b"


def test_separated_scores_ignore_the_tie_breakers():
    snap = snapshot(entry("claude", "model-a", "xhigh", 60.0, coding_score=10.0),
                    entry("codex", "model-b", "high", 58.0, coding_score=90.0))
    assert run(evidence(snap=snap, rows=profiles()[:2]))["selected_profile"] == "a"


def test_missing_coding_defers_to_measured_quota_cost_then_profile_id():
    snap = tie_snapshot({"coding_score": None}, {"coding_score": 75.0})
    rows = profiles()
    rows[1]["measured_quota_cost"] = cost_block(0.5, pool_id="codex/pool")
    assert run(evidence(snap=snap, rows=rows))["selected_profile"] == "b"  # quota success twin: cheaper wins
    rows[1]["measured_quota_cost"] = cost_block(9.0, pool_id="codex/pool")
    assert run(evidence(snap=snap, rows=rows))["selected_profile"] == "a"
    rows[1]["measured_quota_cost"] = cost_block(0.5, pool_id="codex/pool", observed_utc=FRESH_BLOCK_STALE)
    assert run(evidence(snap=snap, rows=rows))["selected_profile"] == "a"  # stale cost is missing: profile_id


@pytest.mark.parametrize("over", [
    {"unit": "usd_per_task_api_price"},  # API dollars are never quota, even when both sides share the unit
    {"unit": "usd_per_mtok_api_price"},
    {"unit": "usd_api_price"},
    {"pool_id": "claude/pool"},  # measured on another profile's pool
    {"window": "weekly"},  # a different window basis than the other member
    {"workload": "task:other"},
    {"workload": None},
])
def test_non_quota_or_incomparable_cost_is_ignored(over):
    snap = tie_snapshot({"coding_score": None}, {"coding_score": 75.0})
    rows = profiles()
    same_unit = {k: v for k, v in over.items() if k == "unit"}
    rows[0]["measured_quota_cost"] = cost_block(9.0, **same_unit)
    rows[1]["measured_quota_cost"] = dict(cost_block(0.5, pool_id="codex/pool"), **over)
    # b is cheaper only on an ignored cost, so the key is skipped and profile_id decides.
    assert run(evidence(snap=snap, rows=rows))["selected_profile"] == "a"


@pytest.mark.parametrize("snap_change,reason", [
    (lambda s: s["entries"][0].pop("measured_on"), "score_undated"),
    (lambda s: s["entries"][0].update(measured_on="2026-08-01"), "score_stale"),
    (lambda s: s["entries"][0].update(measured_on="2026-10-01"), "score_stale"),
    (lambda s: s["entries"][0].update(measured_on="20260920"), "score_undated"),
    (lambda s: s["entries"][0].pop("uncertainty"), "score_malformed"),
    (lambda s: s["entries"][0].update(uncertainty=-0.1), "score_malformed"),
    (lambda s: s["entries"][0].update(score="60"), "score_malformed"),
    (lambda s: s["entries"][0].update(coding_score="70"), "score_malformed"),
    (lambda s: s["entries"][0].update(effort="max"), "no_registry_entry"),
    (lambda s: s["entries"][0].update(model="Model-A"), "no_registry_entry"),
])
def test_unrankable_candidate_makes_the_ranking_unknown(snap_change, reason):
    snap = snapshot()
    snap_change(snap)
    out = run(evidence(snap=snap))
    assert out["verdict"] == cs.UNKNOWN and "unranked:a:" + reason in out["reasons"]
    assert out["selected_profile"] is None and out["provisional_profile"] == "b" and "a" not in out["ranking"]


@pytest.mark.parametrize("change,reason", [
    (mutate(("parameters", "registry_sha256"), "b" * 64), "registry_digest_mismatch"),
    (mutate(("registry_snapshot", "schema"), "wd.model-registry.v1"), "registry_snapshot_malformed"),
    (mutate(("registry_snapshot", "entries"), {}), "registry_digest_mismatch"),
])
def test_unpinned_snapshot_is_unknown_without_a_provisional(change, reason):
    out = run(change(evidence()))
    assert out["verdict"] == cs.UNKNOWN and reason in out["reasons"]
    assert out["selected_profile"] is None and out["provisional_profile"] is None and out["ranking"] == []


def test_other_index_version_and_duplicate_entries_are_unknown():
    out = run(evidence(snap=snapshot(index_version="2")))
    assert out["verdict"] == cs.UNKNOWN and "registry_index_mismatch" in out["reasons"]
    dup = snapshot(entry("claude", "model-a", "xhigh", 60.0), entry("claude", "model-a", "xhigh", 61.0))
    out = run(evidence(snap=dup))
    assert out["verdict"] == cs.UNKNOWN and "registry_duplicate_entry" in out["reasons"]


def previous(score, measured_on, **over):
    snap = snapshot()
    snap["entries"][0].update(score=score, measured_on=measured_on)
    snap.update(over)
    return snap


def previous_with(measured_on="2026-09-20", **over):
    snap = snapshot()
    snap["entries"][0].update(measured_on=measured_on, **over)
    return snap


@pytest.mark.parametrize("prior,reason", [
    (previous(50.0, "2026-09-10"), "refresh_implausible"),
    (previous(59.0, "2026-09-20"), "refresh_without_new_measurement"),
    # Secondary metrics change the order too, so they need the same refresh evidence.
    (previous_with(coding_score=60.0), "refresh_without_new_measurement"),
    (previous_with(uncertainty=2.0), "refresh_without_new_measurement"),
    (previous_with("2026-09-10", coding_score=50.0), "refresh_implausible"),
    (previous_with(uncertainty="x"), "previous_score_malformed"),
    (previous_with(coding_score="70"), "previous_score_malformed"),
    (previous(60.0, "2026-09-25"), "score_measurement_regressed"),
    (previous("x", "2026-09-10"), "previous_score_malformed"),
])
def test_refresh_needs_a_new_dated_plausible_measurement(prior, reason):
    out = run(evidence(previous_snapshot=prior))
    assert out["verdict"] == cs.UNKNOWN and "unranked:a:" + reason in out["reasons"]
    assert out["selected_profile"] is None


def test_plausible_refresh_and_new_index_previous_pass():
    assert run(evidence(previous_snapshot=previous(57.0, "2026-09-10")))["selected_profile"] == "a"
    assert run(evidence(previous_snapshot=previous(10.0, "2026-09-10", index_version="0")))[
        "selected_profile"] == "a"
    out = run(evidence(previous_snapshot={"schema": "bogus"}))
    assert out["verdict"] == cs.UNKNOWN and "registry_snapshot_malformed" in out["reasons"]


EXHAUSTED = pool_block(state="exhausted")


@pytest.mark.parametrize("over,why", [
    ({"pool": EXHAUSTED}, "pool_exhausted"),
    ({"pool": dict(EXHAUSTED, state="conserve")}, "pool_conserve"),
    ({"pool": dict(EXHAUSTED, state="available", provider_up=False)}, "provider_down"),
    ({"turn": {"ok": False, "observed_utc": FRESH, "profile_id": "a"}}, "turn_not_ok"),
])
def test_unavailable_top_waits_until_the_signed_deadline(over, why):
    out = run(evidence(rows=with_profile("a", **over)))
    assert out["verdict"] == cs.WAIT and out["waiting_for"] == "a" and out["selected_profile"] is None
    assert out["wait_deadline_utc"] == "2026-09-29T21:10:00Z" and out["unavailable"]["a"] == [why]


def task(created_utc):
    return {"task_id": "task-1", "created_utc": created_utc}


def test_wait_deadline_then_labelled_fallback():
    rows = with_profile("a", pool=EXHAUSTED)
    out = run(evidence(rows=rows, task=task("2026-09-29T20:55:00Z")))
    assert out["verdict"] == cs.WAIT and out["wait_deadline_utc"] == "2026-09-29T21:05:00Z"
    assert out["task_id"] == "task-1" and out["policy_sha256"] == POLICY
    out = run(evidence(rows=rows, task=task("2026-09-29T20:40:00Z")))
    assert out["verdict"] == cs.FALLBACK and out["selected_profile"] == "b"
    assert out["reasons"] == ["top_unavailable_deadline_passed:a"]
    ev = evidence(rows=rows)
    ev["parameters"]["max_wait_seconds"] = 0
    assert run(ev)["verdict"] == cs.FALLBACK


def test_wait_is_bounded_per_task_and_no_wait_record_moves_it():
    rows = with_profile("a", pool=EXHAUSTED)
    # Tools wait-state forgery: an ancient record cannot force an early fallback, and omitting one
    # cannot extend the wait. The only start is the task's creation.
    for record in (None, {"profile_id": "a", "started_utc": "2026-09-01T00:00:00Z"},
                   {"profile_id": "a", "started_utc": FUTURE}):
        ev = evidence(rows=rows)
        if record is not None:
            ev["wait"] = record
        out = run(ev)
        assert out["verdict"] == cs.WAIT and out["wait_deadline_utc"] == "2026-09-29T21:10:00Z"
    for bad in (None, task(FUTURE), {"created_utc": NOW}, {"task_id": "task-1"}, "task-1"):
        ev = evidence(rows=rows)
        ev["task"] = bad
        out = run(ev)
        assert out["verdict"] == cs.HOLD and "task" in out["reasons"]


def test_stale_quota_is_never_the_fallback():
    rows = with_profile("a", pool=EXHAUSTED)
    rows[1]["pool"] = pool_block("b", "codex", observed_utc=FRESH_BLOCK_STALE)
    late = task("2026-09-29T20:40:00Z")
    out = run(evidence(rows=rows, task=late))
    assert out["verdict"] == cs.FALLBACK and out["selected_profile"] == "c" and out["route"] == "grok_consult"
    out = run(evidence(rows=rows[:2], task=late))
    assert out["verdict"] == cs.HOLD and "no_available_fallback" in out["reasons"]
    assert out["selected_profile"] is None


def test_grok_composes_only_via_grok_consult():
    rows = [dict(p, signed_in_envelope=p["provider"] == "grok") for p in profiles()]
    out = run(evidence(rows=rows))
    assert out["verdict"] == cs.COMPOSER and out["selected_profile"] == "c" and out["route"] == "grok_consult"


def test_unknown_offers_only_an_available_labelled_provisional():
    rows = with_profile("a", pool=EXHAUSTED) + [profile("d", "claude", "model-d", "high")]
    out = run(evidence(rows=rows))
    assert out["verdict"] == cs.UNKNOWN and "unranked:d:no_registry_entry" in out["reasons"]
    assert out["ranking"] == ["a", "b", "c"] and out["unavailable"]["a"] == ["pool_exhausted"]
    assert out["selected_profile"] is None and out["provisional_profile"] == "b" and out["route"] == "direct"


@pytest.mark.parametrize("bad", [None, [], "x", 0, {}, {"schema": cs.EVIDENCE_SCHEMA}])
def test_garbage_never_raises(bad):
    out = run(bad)
    assert out["verdict"] == cs.HOLD and "evidence_malformed" in out["reasons"]


@pytest.mark.parametrize("change", [
    mutate(("parameters", "epsilon"), float("nan")),
    mutate(("profiles",), (1, 2)),
    lambda ev: ev.update({1: "int key"}) or ev,
])
def test_non_canonical_evidence_holds(change):
    out = run(change(evidence()))
    assert out["verdict"] == cs.HOLD and "evidence_not_canonical_json" in out["reasons"]
    assert out["inputs_digest"] is None


def test_profile_shape_and_duplicate_ids_hold():
    rows = profiles()
    rows[1]["profile_id"] = "a"
    assert "duplicate_profile_id" in run(evidence(rows=rows))["reasons"]
    rows = profiles()
    del rows[0]["effort"]
    assert "profile" in run(evidence(rows=rows))["reasons"]


def test_input_is_not_mutated():
    ev = evidence()
    before = copy.deepcopy(ev)
    run(ev)
    assert ev == before


FORBIDDEN_IMPORTS = {"os", "sys", "subprocess", "socket", "pathlib", "time", "random", "urllib", "http",
                     "requests", "shutil", "io", "tempfile", "threading", "asyncio"}
FORBIDDEN_CALLS = {"open", "print", "exec", "eval", "compile", "__import__", "input", "now", "utcnow",
                   "today", "getenv", "system"}


def test_module_is_pure_by_construction():
    tree = ast.parse(Path(cs.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not {a.name.split(".")[0] for a in node.names} & FORBIDDEN_IMPORTS
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in FORBIDDEN_IMPORTS
        elif isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            assert name not in FORBIDDEN_CALLS, name
