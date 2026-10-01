"""F21 minimal qualification receipts (plan 2.1 and the F21 row). The fixtures under tests/fixtures/qualification
are SYNTHETIC: they say nothing about a real model, provider or pool."""
from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

import pytest

import tools.wd_composer_select as cs
import tools.wd_profile_qualification as pq
import tools.wd_task_router as tr

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "qualification"
NOW = "2026-09-30T17:00:00Z"
CODE_SHA = "c0de" * 10
PROFILE = {"profile_id": "claude-strong", "provider": "claude", "model": "model-a", "effort": "xhigh",
           "provider_version": "cli-2.1.0"}


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


BINDING = {"code_sha": CODE_SHA, "suite_sha256": cs.digest(load("suite.json")),
           "thresholds_sha256": load("thresholds.json")["sha256"], **PROFILE}


def suite():
    return load("suite.json")


def runs():
    return load("runs_profile_a.json")


def signed(**over):
    thresholds = dict(load("thresholds.json")["thresholds"], **over)
    return {"thresholds": thresholds, "sha256": cs.digest(thresholds)}


_FIXTURE = object()


def build(s=_FIXTURE, r=_FIXTURE, t=_FIXTURE, profile=_FIXTURE, code_sha=CODE_SHA, now=NOW):
    out = pq.build_receipt(suite() if s is _FIXTURE else s, runs() if r is _FIXTURE else r,
                           signed() if t is _FIXTURE else t, PROFILE if profile is _FIXTURE else profile,
                           code_sha, now)
    assert out["schema"] == pq.RECEIPT_SCHEMA and out["feature"] == "F21"
    assert out["authority"] == "none" and out["execution_allowed"] is False and out["claims"] == pq.CLAIMS
    assert out["receipt_sha256"] == cs.digest({k: v for k, v in out.items() if k != "receipt_sha256"})
    return out


def cls(receipt, name):
    rows = [row for row in receipt["classes"] if row["task_class"] == name]
    assert len(rows) == 1
    return rows[0]


def run_of(case_id, repeat, rows=None):
    for row in runs() if rows is None else rows:
        if (row["case_id"], row["repeat"]) == (case_id, repeat):
            return row
    raise KeyError(case_id)


# --- deterministic replay against the frozen fixture ---------------------------------------------------

def test_fixture_receipt_replays_exactly():
    receipt = build()
    assert receipt == load("expected_receipt_profile_a.json")
    assert pq.replay(receipt, suite(), runs(), signed(), PROFILE, CODE_SHA, NOW) is True
    assert build(r=list(reversed(runs()))) == receipt


def test_fixture_thresholds_pin_matches():
    fixture = load("thresholds.json")
    assert fixture["sha256"] == cs.digest(fixture["thresholds"])


def test_fixture_classes_hand_checked():
    receipt = build()
    impl, review = cls(receipt, "implementation"), cls(receipt, "review")
    assert (impl["state"], impl["samples"], impl["successes"], impl["min_repeats_seen"]) == ("qualified", 12, 12, 3)
    assert (impl["wilson_low"], impl["wilson_high"], impl["uncertainty"]) == (0.757506, 1.0, 0.121247)
    assert impl["holdout_cases"] == 1 and impl["holdout_pass_rate"] == 1.0
    assert (review["state"], review["reasons"]) == ("not_qualified", ["success_lower_bound_below_rate"])
    assert (review["samples"], review["successes"], review["wilson_low"]) == (9, 8, 0.565)
    assert receipt["measured_at_utc"] == "2026-09-30T12:20:00.000000Z"
    assert receipt["valid_until_utc"] == "2026-10-07T12:20:00.000000Z"
    assert receipt["rejected"] == [] and receipt["profile"] == PROFILE and receipt["code_sha"] == CODE_SHA


def test_wilson_matches_the_closed_form():
    assert pq.wilson(12, 12) == (0.757506, 1.0, 0.121247)
    low, high, half = pq.wilson(5, 10)
    assert low == pytest.approx(0.236593, abs=1e-6) and high == pytest.approx(0.763407, abs=1e-6)
    assert half == pytest.approx((high - low) / 2, abs=2e-6)


@pytest.mark.parametrize("mutate", [
    lambda s, r, t: r.__setitem__(0, dict(r[0], passed=False)),
    lambda s, r, t: s["cases"][0].__setitem__("input_sha256", "0" * 64),
    lambda s, r, t: t.update(signed(min_success_rate=0.6)),
])
def test_replay_detects_any_changed_input(mutate):
    receipt = build()
    s, r, t = suite(), runs(), signed()
    mutate(s, r, t)
    assert pq.replay(receipt, s, r, t, PROFILE, CODE_SHA, NOW) is False


def test_replay_detects_a_tampered_receipt():
    tampered = copy.deepcopy(build())
    cls(tampered, "review")["state"] = "qualified"
    assert pq.replay(tampered, suite(), runs(), signed(), PROFILE, CODE_SHA, NOW) is False


# --- what counts ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("over,reason", [
    ({"profile_id": "codex-std"}, "other_profile"),
    ({"provider_version": "cli-2.2.0"}, "other_profile"),
    ({"effort": "high"}, "other_profile"),
    ({"code_sha": "beef" * 10}, "other_code"),
    ({"grader_sha256": "1" * 64}, "grader_not_pinned"),
    ({"case_id": "nope"}, "unknown_case"),
    ({"observed_utc": "2026-09-30T18:00:00Z"}, "future_dated"),
    ({"observed_utc": "2026-08-01T12:00:00Z"}, "stale"),
    ({"observed_utc": "2026-09-30T12:00:00"}, "malformed"),
    ({"repeat": 0}, "malformed"),
    ({"passed": "yes"}, "malformed"),
    ({"production_writes": -1}, "malformed"),
    ({"self_graded": True}, "malformed"),
])
def test_unbound_or_malformed_runs_never_count(over, reason):
    rows = runs()
    rows[0] = dict(rows[0], **over)
    receipt = build(r=rows)
    assert [row["reason"] for row in receipt["rejected"]] == [reason]
    assert cls(receipt, "implementation")["samples"] == 11
    assert cls(receipt, "implementation")["reasons"] == ["below_min_repeats"]


@pytest.mark.parametrize("over", [{"isolated": False}, {"production_writes": 1}])
def test_one_unsafe_run_refuses_the_whole_receipt(over):
    rows = runs()
    rows[5] = dict(rows[5], **over)
    receipt = build(r=rows)
    assert receipt["state"] == "refused" and receipt["reasons"][0] == "isolation_violation"
    assert receipt["classes"] == [] and receipt["valid_until_utc"] is None
    assert pq.router_qualification(receipt, BINDING, NOW) == []


def test_identical_repeat_copies_count_once():
    receipt = build(r=runs() + [run_of("impl-syn-1", 1)])
    assert receipt["runs_sha256"] != build()["runs_sha256"]
    assert receipt["classes"] == build()["classes"] and receipt["rejected"] == []


def test_conflicting_repeat_copies_reject_every_copy():
    rows = runs() + [dict(run_of("impl-syn-1", 1), passed=False, transcript_sha256="9" * 64)]
    receipt = build(r=rows)
    assert [row["reason"] for row in receipt["rejected"]] == ["conflicting_repeat", "conflicting_repeat"]
    assert cls(receipt, "implementation")["samples"] == 11


# --- thresholds ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("over,reason", [
    ({"min_samples": 13}, "below_min_samples"),
    ({"min_repeats": 4}, "below_min_repeats"),
    ({"min_holdout_cases": 2}, "too_few_holdout_cases"),
    ({"min_success_rate": 0.8}, "success_lower_bound_below_rate"),
    ({"max_uncertainty": 0.1}, "uncertainty_above_max"),
])
def test_each_threshold_can_fail_a_class_on_its_own(over, reason):
    assert cls(build(t=signed(**over)), "implementation")["reasons"] == [reason]


def test_a_failed_holdout_fails_the_class():
    rows = runs()
    rows[rows.index(run_of("impl-hold-1", 2, rows))] = dict(run_of("impl-hold-1", 2, rows), passed=False)
    impl = cls(build(r=rows), "implementation")
    assert impl["state"] == "not_qualified" and "holdout_below_rate" in impl["reasons"]
    assert impl["holdout_pass_rate"] == round(2 / 3, 6)


def test_a_class_without_counted_runs_is_unmeasured_not_qualified():
    rows = [row for row in runs() if not row["case_id"].startswith("rev-")]
    review = cls(build(r=rows), "review")
    assert review["state"] == "unmeasured" and review["reasons"] == ["no_counted_runs"]
    assert review["wilson_low"] is None and review["samples"] == 0


def test_no_counted_runs_at_all_gives_an_undated_receipt_that_never_validates():
    receipt = build(r=[])
    assert receipt["state"] == "built" and receipt["measured_at_utc"] is None
    assert pq.validate_receipt(receipt, BINDING, NOW)["reasons"] == ["receipt_expired_or_undated"]


@pytest.mark.parametrize("t,reason", [
    ({"thresholds": load("thresholds.json")["thresholds"], "sha256": "0" * 64}, "thresholds_unbound"),
    (signed(activate=True), "thresholds_invalid"),
    (signed(min_success_rate=1.5), "thresholds_invalid"),
    (signed(min_repeats=0), "thresholds_invalid"),
    (signed(receipt_ttl_seconds=True), "thresholds_invalid"),
    ({"thresholds": {}}, "thresholds_malformed"),
    (None, "thresholds_malformed"),
])
def test_thresholds_must_be_pinned_closed_and_sane(t, reason):
    receipt = build(t=t)
    assert receipt["state"] == "refused" and receipt["reasons"] == [reason]


@pytest.mark.parametrize("kwargs,reason", [
    ({"now": "2026-09-30T17:00:00"}, "now_invalid"),
    ({"code_sha": "short"}, "code_sha_malformed"),
    ({"profile": dict(PROFILE, extra="x")}, "profile_malformed"),
    ({"s": dict(suite(), cases=[])}, "suite_malformed"),
    ({"s": dict(suite(), cases=suite()["cases"] + suite()["cases"][:1])}, "suite_duplicate_case"),
    ({"r": "all"}, "runs_malformed"),
    ({"r": [{"x": (1,)}]}, "runs_not_canonical_json"),
])
def test_malformed_inputs_refuse(kwargs, reason):
    assert build(**kwargs)["reasons"] == [reason]


def test_never_raises():
    for args in ((None,) * 6, (1, 2, 3, 4, 5, 6), ({}, [], {}, {}, "", "")):
        receipt = pq.build_receipt(*args)
        assert receipt["state"] == "refused" and receipt["authority"] == "none"
    assert pq.validate_receipt(None, None, None)["valid"] is False
    assert pq.router_qualification(None, None, None) == []


def test_inputs_are_not_mutated():
    s, r, t = suite(), runs(), signed()
    before = copy.deepcopy((s, r, t))
    build(s=s, r=r, t=t)
    assert (s, r, t) == before


# --- validation and the router adapter ---------------------------------------------------------------------

def test_valid_receipt_adapts_to_router_entries():
    entries = pq.router_qualification(build(), BINDING, NOW)
    receipt = build()
    assert entries == [
        {"task_class": "implementation", "profile_id": "claude-strong", "qualified": True,
         "receipt_sha256": receipt["receipt_sha256"], "observed_utc": receipt["measured_at_utc"],
         "valid_until_utc": receipt["valid_until_utc"]},
        {"task_class": "review", "profile_id": "claude-strong", "qualified": False,
         "receipt_sha256": receipt["receipt_sha256"], "observed_utc": receipt["measured_at_utc"],
         "valid_until_utc": receipt["valid_until_utc"]}]


@pytest.mark.parametrize("binding,reason", [
    (dict(BINDING, code_sha="beef" * 10), "code_sha_mismatch"),
    (dict(BINDING, suite_sha256="0" * 64), "suite_sha256_mismatch"),
    (dict(BINDING, thresholds_sha256=signed(min_success_rate=0.1)["sha256"]), "thresholds_sha256_mismatch"),
    (dict(BINDING, provider_version="cli-2.2.0"), "profile_mismatch:provider_version"),
    (dict(BINDING, profile_id="codex-std"), "profile_mismatch:profile_id"),
])
def test_binding_mismatch_invalidates(binding, reason):
    verdict = pq.validate_receipt(build(), binding, NOW)
    assert verdict == {"valid": False, "reasons": [reason]}
    assert pq.router_qualification(build(), binding, NOW) == []


def test_binding_must_name_every_field():
    partial = {k: v for k, v in BINDING.items() if k != "thresholds_sha256"}
    assert pq.validate_receipt(build(), partial, NOW)["reasons"] == ["binding_malformed"]
    assert pq.validate_receipt(build(), dict(BINDING, extra=1), NOW)["reasons"] == ["binding_malformed"]


def test_receipt_built_under_laxer_thresholds_never_validates_against_the_signed_pin():
    lax = build(t=signed(min_success_rate=0.1))
    assert cls(lax, "review")["state"] == "qualified"
    assert pq.validate_receipt(lax, BINDING, NOW)["reasons"] == ["thresholds_sha256_mismatch"]


def test_expired_or_premature_receipt_invalidates():
    assert pq.validate_receipt(build(), BINDING, "2026-10-07T12:20:00Z")["reasons"] == ["receipt_expired_or_undated"]
    assert pq.validate_receipt(build(), BINDING, "2026-09-30T12:19:59Z")["reasons"] == ["receipt_expired_or_undated"]
    assert pq.validate_receipt(build(), BINDING, "2026-10-07T12:19:59Z")["valid"] is True


def test_edited_or_overreaching_receipt_invalidates():
    edited = build()
    cls(edited, "review")["state"] = "qualified"
    assert pq.validate_receipt(edited, BINDING, NOW)["reasons"] == ["receipt_digest_mismatch"]
    overreach = build()
    overreach["claims"] = dict(pq.CLAIMS, universal=True)
    overreach["receipt_sha256"] = cs.digest({k: v for k, v in overreach.items() if k != "receipt_sha256"})
    assert pq.validate_receipt(overreach, BINDING, NOW)["reasons"] == ["claims_overreach"]
    assert pq.validate_receipt(build(t=signed(min_repeats=0)), BINDING, NOW)["reasons"] == ["receipt_not_built"]


def test_router_uses_the_adapted_receipt():
    """End to end with the F19 router: the receipt decides eligibility; without it the worker is unknown."""
    entries = pq.router_qualification(build(), BINDING, NOW)
    worker = {"schema": tr.WORKER_SCHEMA, "worker": "fable-5", "kind": "lane", "profile_id": "claude-strong",
              "role": {"worker": "fable-5", "roles": ["producer"], "verified": True,
                       "observed_utc": "2026-09-30T16:59:00Z"},
              "qualification": entries,
              "capacity": {"profile_id": "claude-strong", "state": "available", "billing": "included",
                           "projected_used_percent": 40.0, "observed_utc": "2026-09-30T16:59:00Z",
                           "valid_until_utc": "2026-09-30T18:00:00Z"},
              "load": {"state": "idle", "observed_utc": "2026-09-30T16:59:00Z"}}
    policy = {"schema": tr.POLICY_SCHEMA, "max_evidence_age_seconds": 900, "budget_mode": "steady",
              "class_roles": {c: ["producer", "rco"] for c in tr.TASK_CLASSES},
              "class_profiles": {c: ["claude-strong"] for c in tr.TASK_CLASSES}}

    def task(task_class):
        return {"schema": tr.TASK_SCHEMA, "task_id": "t", "revision": "1", "input_digest": "a" * 64,
                "task_class": task_class, "scope": ["repo:tools/x.py"], "author": "claude-rco-1",
                "created_utc": "2026-09-30T16:00:00Z"}

    assert tr.decide(task("implementation"), [worker], [], policy, NOW)["verdict"] == tr.ROUTE
    review = tr.decide(task("review"), [worker], [], policy, NOW)
    assert review["verdict"] == tr.HOLD and review["ineligible"] == {"fable-5": ["not_qualified"]}
    test_class = tr.decide(task("test"), [worker], [], policy, NOW)
    assert test_class["verdict"] == tr.UNKNOWN and test_class["unknown"] == {"fable-5": ["qualification_missing"]}


FORBIDDEN_IMPORTS = {"os", "sys", "subprocess", "socket", "pathlib", "time", "random", "urllib", "http",
                     "requests", "shutil", "io", "tempfile", "threading", "asyncio"}
FORBIDDEN_CALLS = {"open", "print", "exec", "eval", "compile", "__import__", "input", "now", "utcnow",
                   "today", "getenv", "system"}


def test_module_is_pure_by_construction():
    tree = ast.parse(Path(pq.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not {a.name.split(".")[0] for a in node.names} & FORBIDDEN_IMPORTS
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in FORBIDDEN_IMPORTS
        elif isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            assert name not in FORBIDDEN_CALLS, name
