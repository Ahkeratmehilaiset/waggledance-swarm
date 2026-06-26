# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import pytest

from tools.post_merge_canary import (
    CONFIRMED_REGRESS,
    INCONCLUSIVE,
    PASS,
    SUSPECT,
    canary_receipt,
    classify,
)

SHA = "a" * 40
GOOD = "b" * 40


def run(checks: dict, *, completed=True, within_budget=True) -> dict:
    return {"checks": checks, "completed": completed, "within_budget": within_budget}


def cls(runs, **kw):
    kw.setdefault("merged_sha", SHA)
    kw.setdefault("prior_good_sha", GOOD)
    kw.setdefault("fp_rate", 0.0)   # explicit KNOWN good fp_rate; omission is fail-closed (tested separately)
    return classify(runs, **kw)


# --- PASS --------------------------------------------------------------------

def test_all_green_is_pass_no_signal():
    v = cls([run({"smoke": True, "contract": True}), run({"smoke": True, "contract": True})])
    assert v.outcome == PASS and v.p4a_signal is None


# --- CONFIRMED_REGRESS (debounced same-cause) --------------------------------

def test_same_cause_two_runs_confirms_and_emits_signal():
    v = cls([run({"smoke": True, "contract": False}),
             run({"smoke": True, "contract": False})])
    assert v.outcome == CONFIRMED_REGRESS
    assert v.failing_check_ids == ["contract"] and v.confirmations == 2
    s = v.p4a_signal
    assert s and s["kind"] == "p4b_confirmed_regress"
    assert s["merged_sha"] == SHA and s["prior_good_sha"] == GOOD
    assert s["failing_check_ids"] == ["contract"]


def test_three_runs_one_clean_still_confirms_if_two_same_cause():
    v = cls([run({"contract": False}), run({"contract": True}), run({"contract": False})])
    assert v.outcome == CONFIRMED_REGRESS and v.confirmations == 2


# --- SUSPECT (not yet debounced) ---------------------------------------------

def test_single_run_failure_is_suspect_no_signal():
    v = cls([run({"smoke": True, "contract": False})])
    assert v.outcome == SUSPECT and v.p4a_signal is None


def test_two_runs_different_failures_not_same_cause_suspect():
    # different failing checks across runs are NOT a confirmation (same-cause binding)
    v = cls([run({"smoke": False, "contract": True}),
             run({"smoke": True, "contract": False})])
    assert v.outcome == SUSPECT and v.p4a_signal is None


# --- required_confirmations FLOOR (caller cannot lower below 2) ---------------

def test_caller_cannot_lower_confirmation_floor_below_two():
    # caller asks for 1, but the floor is 2 -> a single same-cause failure stays SUSPECT
    v = cls([run({"contract": False})], required_confirmations=1)
    assert v.outcome == SUSPECT


def test_higher_required_confirmations_respected():
    # caller raises the bar to 3 -> two same-cause failures are NOT yet confirmed
    v = cls([run({"contract": False}), run({"contract": False})],
            required_confirmations=3)
    assert v.outcome == SUSPECT


# --- INCONCLUSIVE (fail-safe: uncertainty never triggers rollback) -----------

def test_run_over_budget_is_inconclusive_no_signal():
    v = cls([run({"contract": False}, within_budget=False),
             run({"contract": False})])
    assert v.outcome == INCONCLUSIVE and v.p4a_signal is None


def test_run_not_completed_is_inconclusive():
    v = cls([run({"contract": False}, completed=False),
             run({"contract": False})])
    assert v.outcome == INCONCLUSIVE and v.p4a_signal is None


def test_fp_rate_unknown_is_inconclusive_fail_closed():
    # rco-1 #1391: omitting fp_rate must NOT default permissive -> a confirmed regress
    # with an UNKNOWN (unmeasured) fp_rate is fail-CLOSED (INCONCLUSIVE, no rollback).
    v = classify([run({"contract": False}), run({"contract": False})],
                 merged_sha=SHA, prior_good_sha=GOOD)   # fp_rate omitted -> None
    assert v.outcome == INCONCLUSIVE and v.p4a_signal is None
    assert v.fp_rate is None
    r = canary_receipt(v, merged_sha=SHA, canary_manifest_version="v1")
    assert r["fp_rate"] is None


@pytest.mark.parametrize("bad", [
    -1.0,                 # rco-2 #1391: negative slipped through (-1 > 0.01 is False)
    -0.0001,
    1.5,                  # out of [0,1]
    float("nan"),         # rco-2 #1391: NaN > 0.01 is False -> bypassed
    float("inf"),
    "x",                  # rco-2 #1391: non-numeric -> was an uncaught TypeError crash
    True,                 # bool is not a valid probability
])
def test_invalid_fp_rate_is_inconclusive_fail_closed(bad):
    # an out-of-range / NaN / non-numeric fp_rate must NOT be accepted as "low FP";
    # validate to a finite probability in [0,1], else fail-closed -> INCONCLUSIVE.
    v = cls([run({"contract": False}), run({"contract": False})], fp_rate=bad)
    assert v.outcome == INCONCLUSIVE and v.p4a_signal is None
    assert v.fp_rate is None
    r = canary_receipt(v, merged_sha=SHA, canary_manifest_version="v1")
    assert r["fp_rate"] is None


def test_fp_rate_over_threshold_is_inconclusive_not_eligible():
    # even a clean same-cause regress is NOT P4a-eligible if the canary's FP-rate
    # exceeds the acceptance threshold (spec §5).
    v = cls([run({"contract": False}), run({"contract": False})], fp_rate=0.05)
    assert v.outcome == INCONCLUSIVE and v.p4a_signal is None


def test_fp_rate_at_threshold_still_eligible():
    v = cls([run({"contract": False}), run({"contract": False})],
            fp_rate=0.01, fp_threshold=0.01)
    assert v.outcome == CONFIRMED_REGRESS


def test_no_runs_is_inconclusive():
    assert cls([]).outcome == INCONCLUSIVE


# --- fail-safe invariant: only CONFIRMED_REGRESS ever carries a P4a signal ----

@pytest.mark.parametrize("runs,kw", [
    ([run({"contract": False})], {}),                                   # SUSPECT
    ([run({"contract": False}, within_budget=False)], {}),             # INCONCLUSIVE
    ([run({"smoke": True})], {}),                                       # PASS
    ([], {}),                                                            # INCONCLUSIVE
])
def test_only_confirmed_regress_emits_p4a_signal(runs, kw):
    v = cls(runs, **kw)
    assert (v.p4a_signal is not None) == (v.outcome == CONFIRMED_REGRESS)


# --- receipt -----------------------------------------------------------------

def test_receipt_records_outcome_and_trigger_flag():
    v = cls([run({"contract": False}), run({"contract": False})])
    r = canary_receipt(v, merged_sha=SHA, canary_manifest_version="v1",
                       budget_seconds_used=120.0, run_count=2)
    assert r["type"] == "p4b_canary_receipt" and r["merged_sha"] == SHA
    assert r["outcome"] == CONFIRMED_REGRESS and r["p4a_trigger_emitted"] is True
    assert r["fp_rate"] == 0.0
    assert r["run_count"] == 2


def test_receipt_pass_has_no_trigger():
    v = cls([run({"smoke": True}), run({"smoke": True})])
    r = canary_receipt(v, merged_sha=SHA, canary_manifest_version="v1")
    assert r["p4a_trigger_emitted"] is False
