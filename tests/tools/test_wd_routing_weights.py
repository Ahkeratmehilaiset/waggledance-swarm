"""F26 shadow routing weights (plan 2.11). Every value here is SYNTHETIC fixture data, not a measurement of
a real profile."""
from __future__ import annotations

import copy
import hashlib

import pytest

import tools.wd_composer_select as cs
import tools.wd_routing_weights as rw

NOW = "2026-09-30T17:00:00Z"
HOUR_AGO = "2026-09-30T16:00:00Z"
DAY_AGO = "2026-09-29T17:00:00Z"
FUTURE = "2026-09-30T18:00:00Z"
OLD = "2026-08-30T16:59:59Z"
RECORD_KEYS = {"schema", "feature", "mode", "state", "reasons", "bounds_sha256", "now_utc", "weights", "rejected",
               "duplicates_ignored", "execution_allowed", "authority", "activation", "evidence_digest"}


def bounds(**over):
    row = {"schema": rw.BOUNDS_SCHEMA, "min_weight": 0.25, "max_weight": 4.0, "prior_strength": 1.0,
           "half_life_seconds": 86400, "max_outcome_age_seconds": 30 * 86400, "min_independent_evaluators": 1,
           "min_samples": 1}
    row.update(over)
    return row


def signed(b=None):
    b = bounds() if b is None else b
    return {"bounds": b, "sha256": cs.digest(b)}


def outcome(oid, result="success", observed=HOUR_AGO, evaluators=("claude-rco-1",), profile="claude-strong",
            worker="fable-5", kind="outcome", task_class="implementation", key=None, **over):
    row = {"schema": rw.OUTCOME_SCHEMA, "outcome_id": oid, "kind": kind,
           "dispatch_key": key or hashlib.sha256(oid.encode("ascii")).hexdigest(), "task_class": task_class,
           "profile_id": profile, "worker": worker, "result": result,
           "stop_signal": "failure" if result == "failure" else None, "evaluators": list(evaluators),
           "verified": True, "evidence_sha256": "f" * 64, "observed_utc": observed}
    row.update(over)
    return row


_SIGNED = object()


def derive(outcomes, sb=_SIGNED, now=NOW):
    out = rw.derive_shadow_weights(outcomes, signed() if sb is _SIGNED else sb, now)
    assert set(out) == RECORD_KEYS and out["schema"] == rw.SCHEMA and out["feature"] == "F26"
    assert out["mode"] == "shadow" and out["authority"] == "none" and out["activation"] == "none"
    assert out["execution_allowed"] is False
    return out


def pair(out, task_class="implementation", profile="claude-strong"):
    rows = [r for r in out["weights"] if (r["task_class"], r["profile_id"]) == (task_class, profile)]
    assert len(rows) <= 1
    return rows[0] if rows else None


def expected(successes, failures, prior=1.0, low=0.25, high=4.0):
    return round(min(max((successes + prior) / (failures + prior), low), high), 6)


# --- the dance: successes recruit, old news fades ------------------------------------------------------

def test_no_outcomes_means_no_weight_not_a_default():
    out = derive([])
    assert out["state"] == "derived" and out["weights"] == [] and out["rejected"] == []


def test_one_verified_success_raises_the_weight():
    row = pair(derive([outcome("o-1")]))
    decay = 0.5 ** (3600 / 86400)
    assert row["state"] == "known" and row["samples"] == 1 and row["reasons"] == []
    assert row["weight"] == expected(decay, 0.0) and row["decayed_successes"] == round(decay, 6)


def test_older_success_counts_less():
    day = pair(derive([outcome("o-1", observed=DAY_AGO)]))
    hour = pair(derive([outcome("o-1", observed=HOUR_AGO)]))
    assert day["weight"] == 1.5 and day["weight"] < hour["weight"]


def test_weights_converge_to_the_better_measured_profile():
    outcomes = [outcome("a-%d" % i, profile="profile-a") for i in range(6)]
    outcomes += [outcome("b-%d" % i, profile="profile-b") for i in range(2)]
    out = derive(outcomes)
    assert pair(out, profile="profile-a")["weight"] > pair(out, profile="profile-b")["weight"] > 1.0


def test_hard_bounds_clamp_both_ways():
    many = [outcome("o-%d" % i, observed=NOW) for i in range(20)]
    assert pair(derive(many))["weight"] == 4.0
    failures = [outcome("f-%d" % i, result="failure", observed=DAY_AGO) for i in range(10)]
    requalified = failures + [outcome("rq-1", kind="requalification", observed=HOUR_AGO,
                                      evaluators=("claude-rco-1", "claude-rco-2"))]
    row = pair(derive(requalified))
    assert row["state"] == "known" and row["weight"] == 0.25 and row["samples"] == 10


def test_below_min_samples_stays_unknown():
    row = pair(derive([outcome("o-1"), outcome("o-2")], sb=signed(bounds(min_samples=3))))
    assert row["state"] == "unknown" and row["weight"] is None and row["reasons"] == ["below_min_samples"]


# --- independence and quorum ---------------------------------------------------------------------------

def test_self_grading_never_counts():
    out = derive([outcome("o-1", evaluators=("fable-5",))])
    assert out["weights"] == [] and [r["reason"] for r in out["rejected"]] == ["quorum_not_met"]


@pytest.mark.parametrize("evaluators,counted", [(("claude-rco-1",), False),
                                                (("claude-rco-1", "claude-rco-2"), True),
                                                (("fable-5", "claude-rco-1"), False)])
def test_success_needs_the_signed_independent_quorum(evaluators, counted):
    out = derive([outcome("o-1", evaluators=evaluators)], sb=signed(bounds(min_independent_evaluators=2)))
    assert (pair(out) is not None) is counted


@pytest.mark.parametrize("evaluators", [("FABLE-5",), ("fable_5",), (" fable-5",), ("Fable_5 ",)])
def test_a_spelling_of_the_worker_is_not_an_independent_evaluator(evaluators):
    """RCO1 SF3: case, padding and the '_'/'-' separator do not make the worker someone else."""
    out = derive([outcome("o-1", evaluators=evaluators)])
    assert out["weights"] == [] and [r["reason"] for r in out["rejected"]] == ["quorum_not_met"]


@pytest.mark.parametrize("evaluators", [("operator",), ("claude-rco-9",), ("CLAUDE-RCO-1",)])
def test_only_exact_member_ids_count_toward_the_quorum(evaluators):
    out = derive([outcome("o-1", evaluators=evaluators)])
    assert out["weights"] == [] and [r["reason"] for r in out["rejected"]] == ["quorum_not_met"]


def test_alias_spellings_of_one_evaluator_count_once():
    out = derive([outcome("o-1", evaluators=("claude-rco-1", "CLAUDE-RCO-1", "claude_rco_1"))],
                 sb=signed(bounds(min_independent_evaluators=2)))
    assert out["weights"] == [] and [r["reason"] for r in out["rejected"]] == ["quorum_not_met"]


class _Liar(str):
    def __eq__(self, other):
        return True

    __hash__ = str.__hash__


def test_a_str_subclass_evaluator_is_malformed_not_a_member():
    out = derive([outcome("o-1", evaluators=[_Liar("anyone")])])
    assert out["weights"] == [] and [r["reason"] for r in out["rejected"]] == ["malformed"]


def test_three_self_spellings_never_build_a_weight():
    outcomes = [outcome("o-%d" % i, evaluators=("fable-5", "FABLE-5", "fable_5")) for i in range(3)]
    out = derive(outcomes)
    assert out["weights"] == [] and [r["reason"] for r in out["rejected"]] == ["quorum_not_met"] * 3


# --- stop signal and requalification ---------------------------------------------------------------

STOP = "2026-09-30T15:00:00Z"


def quarantined_history():
    return [outcome("f-1", result="failure", observed=STOP, evaluators=("fable-5",), stop_signal="limit_hit"),
            outcome("s-1", observed="2026-09-30T16:00:00Z"), outcome("s-2", observed="2026-09-30T16:10:00Z"),
            outcome("s-3", observed="2026-09-30T16:20:00Z")]


def test_a_self_reported_stop_signal_quarantines_and_successes_do_not_lift_it():
    row = pair(derive(quarantined_history()))
    assert row["state"] == "quarantined" and row["weight"] is None and row["samples"] == 4
    assert row["reasons"] == ["stop_signal_without_later_requalification"]


def test_only_a_later_independent_requalification_lifts_the_quarantine():
    lift = outcome("rq-1", kind="requalification", observed="2026-09-30T16:30:00Z",
                   evaluators=("claude-rco-1", "claude-rco-2"))
    assert pair(derive(quarantined_history() + [lift]))["state"] == "known"
    early = dict(lift, observed_utc="2026-09-30T14:00:00Z")
    assert pair(derive(quarantined_history() + [early]))["state"] == "quarantined"
    selfie = dict(lift, evaluators=["fable-5"])
    out = derive(quarantined_history() + [selfie])
    assert pair(out)["state"] == "quarantined" and "quorum_not_met" in [r["reason"] for r in out["rejected"]]


@pytest.mark.parametrize("signal", rw.STOP_SIGNALS)
def test_every_stop_signal_quarantines(signal):
    row = pair(derive([outcome("f-1", result="failure", stop_signal=signal)]))
    assert row["state"] == "quarantined"


# --- dedupe and conflicts ---------------------------------------------------------------------------

def test_a_replayed_outcome_counts_once():
    out = derive([outcome("o-1"), outcome("o-1")])
    assert pair(out)["samples"] == 1 and out["duplicates_ignored"] == 1 and out["rejected"] == []


def test_conflicting_copies_of_one_outcome_id_leave_the_pair_conflicted():
    out = derive([outcome("o-1"), outcome("o-1", result="failure")])
    assert pair(out)["state"] == "conflicted" and pair(out)["weight"] is None
    assert [r["reason"] for r in out["rejected"]] == ["conflicting_duplicate", "conflicting_duplicate"]


def test_one_task_counts_once_even_under_a_new_outcome_id():
    key = "b" * 64
    out = derive([outcome("o-1", key=key), outcome("o-2", key=key, observed=DAY_AGO)])
    assert pair(out)["samples"] == 1 and pair(out)["decayed_successes"] == 0.5
    assert out["rejected"][0]["outcome_id"] == "o-1" and out["rejected"][0]["reason"] == "duplicate_task_outcome"
    split = derive([outcome("o-1", key=key), outcome("o-2", key=key, result="failure")])
    assert pair(split)["state"] == "conflicted"
    assert sorted(r["reason"] for r in split["rejected"]) == ["conflicting_task_outcome"] * 2


# --- what never counts ------------------------------------------------------------------------------

@pytest.mark.parametrize("record,reason", [
    (outcome("o-1", observed=OLD), "stale"),
    (outcome("o-1", observed=FUTURE), "future_dated"),
    (outcome("o-1", verified=False), "unverified"),
    (outcome("o-1", verified="yes"), "unverified"),
    (outcome("o-1", grant_role="lead"), "malformed"),
    (outcome("o-1", stop_signal="failure"), "malformed"),
    (outcome("o-1", result="failure", stop_signal=None), "malformed"),
    (outcome("o-1", result="failure", kind="requalification"), "malformed"),
    (outcome("o-1", task_class="routine"), "malformed"),
    (outcome("o-1", evaluators=[]), "malformed"),
    (outcome("o-1", evaluators=["claude-rco-1", "claude-rco-1"]), "malformed"),
    (outcome("o-1", evidence_sha256="nope"), "malformed"),
    (outcome("o-1", observed="2026-09-30T16:00:00"), "malformed"),
])
def test_rejected_outcomes_never_count(record, reason):
    out = derive([record])
    assert out["weights"] == [] and [r["reason"] for r in out["rejected"]] == [reason]


@pytest.mark.parametrize("items", [[None], [1], ["x"], [{"outcome_id": 5}], [{"outcome_id": "o-1", "x": (1,)}]])
def test_garbage_items_are_rejected_not_raised(items):
    out = derive(items)
    assert out["state"] == "derived" and out["weights"] == [] and out["rejected"][0]["reason"] == "malformed"


# --- signed bounds: never learned, never self-activating -----------------------------------------------

@pytest.mark.parametrize("sb,reason", [
    ({"bounds": bounds(), "sha256": "0" * 64}, "bounds_unbound"),
    (signed(dict(bounds(), activate=True)), "bounds_invalid"),
    (signed(dict(bounds(), mode="active")), "bounds_invalid"),
    (signed(bounds(min_weight=1.5)), "bounds_invalid"),
    (signed(bounds(max_weight=0.9)), "bounds_invalid"),
    (signed(bounds(min_weight=0)), "bounds_invalid"),
    (signed(bounds(prior_strength=0)), "bounds_invalid"),
    (signed(bounds(half_life_seconds=0)), "bounds_invalid"),
    (signed(bounds(min_samples=True)), "bounds_invalid"),
    (signed(bounds(max_weight=float("inf"))), "bounds_malformed"),
    ({"bounds": bounds()}, "bounds_malformed"),
    (None, "bounds_malformed"),
])
def test_bounds_must_be_exact_signed_and_sane(sb, reason):
    out = derive([outcome("o-1")], sb=sb)
    assert out["state"] == "refused" and out["weights"] == [] and out["reasons"] in ([reason], ["input_malformed"])


def test_now_must_be_an_aware_timestamp():
    for now in ("2026-09-30T17:00:00", "later", None):
        assert derive([outcome("o-1")], now=now)["reasons"] == ["now_invalid"]


def test_never_raises():
    for args in ((None, None, None), ("x", 1, 2.5), ({}, {}, {})):
        out = rw.derive_shadow_weights(*args)
        assert out["state"] == "refused" and out["authority"] == "none" and out["activation"] == "none"


def test_outcomes_cannot_carry_authority():
    out = derive([outcome("o-1", authority="lead"), outcome("o-2", activation="on")])
    assert out["weights"] == [] and {r["reason"] for r in out["rejected"]} == {"malformed"}


# --- deterministic replay and the frozen evidence digest ------------------------------------------------

def history():
    return quarantined_history() + [outcome("x-%d" % i, profile="profile-x", observed=DAY_AGO) for i in range(3)] + [
        outcome("bad", verified=False)]


def test_replay_is_deterministic_and_order_independent():
    record = derive(history())
    assert derive(list(reversed(history()))) == record
    assert rw.replay(record, history(), signed(), NOW) is True
    changed = history()
    changed[4] = dict(changed[4], observed_utc=HOUR_AGO)
    assert rw.replay(record, changed, signed(), NOW) is False
    assert rw.replay(record, history(), signed(), "2026-09-30T17:00:01Z") is False
    tampered = copy.deepcopy(record)
    tampered["weights"][0]["weight"] = 9.0
    assert rw.replay(tampered, history(), signed(), NOW) is False


def test_evidence_digest_freezes_every_accepted_and_rejected_outcome():
    base = derive(history())["evidence_digest"]
    assert len(base) == 64
    edited = history()
    edited[1] = dict(edited[1], evidence_sha256="e" * 64)
    assert derive(edited)["evidence_digest"] != base
    assert derive(history() + [outcome("late", observed=FUTURE)])["evidence_digest"] != base
    assert derive(history(), sb=signed(bounds(max_weight=5.0)))["evidence_digest"] != base


def test_inputs_are_not_mutated():
    outcomes, sb = history(), signed()
    before = copy.deepcopy((outcomes, sb))
    derive(outcomes, sb=sb)
    assert (outcomes, sb) == before
