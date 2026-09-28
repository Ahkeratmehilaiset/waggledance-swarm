# SPDX-License-Identifier: BUSL-1.1
"""Fail-closed tests for the nonprivileged Bridge v2 quota arithmetic."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import (DefaultContext, Decimal, Inexact, ROUND_UP, getcontext,
                     localcontext)

import pytest

from tools.bridge_v2_quota_contract import (
    ContractError,
    MAX_RESERVATIONS,
    MAX_UNITS_INT,
    evaluate_admission,
    parse_snapshot,
    snapshot_digest,
)


NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
DIGEST = "a" * 64


def snapshot() -> dict:
    return {
        "schema": "wd.bridge-v2-quota-snapshot.v1",
        "provider": "example",
        "account_id": "account-1",
        "pool_id": "shared-week",
        "limit_id": "weekly",
        "reset_epoch": "epoch-42",
        "unit": "pool_points",
        "remaining_lower_bound": 100,
        "uncertainty_upper_units": 4,
        "external_residual_upper_units": 6,
        "observed_at_utc": "2026-09-28T11:59:00Z",
        "expires_at_utc": "2026-09-28T12:10:00Z",
        "reset_at_utc": "2026-10-01T00:00:00Z",
        "source": "provider-observation",
        "source_digest": DIGEST,
        "binding_state": "verified",
    }


def demand(s: dict) -> dict:
    return {
        "schema": "wd.bridge-v2-quota-demand.v1",
        "provider": s["provider"],
        "account_id": s["account_id"],
        "pool_id": s["pool_id"],
        "limit_id": s["limit_id"],
        "reset_epoch": s["reset_epoch"],
        "unit": s["unit"],
        "snapshot_digest": snapshot_digest(s),
        "reservations": [
            {"id": "active-1", "upper_units": 10, **{
                key: s[key] for key in ("provider", "account_id", "pool_id", "limit_id", "reset_epoch", "unit")
            }}
        ],
        "forecast_upper_units": 12,
        "forecast_through_utc": s["reset_at_utc"],
        "incident_reserve_units": 8,
        "reviewer_reserve_units": 5,
        "proposed_upper_units": 20,
    }


def test_admission_is_pure_conditional_arithmetic_not_dispatch():
    s = snapshot()
    d = demand(s)
    result = evaluate_admission(s, d, now=NOW)
    assert result["state"] == "admissible"
    assert result["reason"] == "sufficient_conservative_headroom"
    assert result["remaining_after_proposal_units"] == "35"
    assert result["execution_allowed"] is False
    assert result["atomic_reservation_required"] is True
    assert result["snapshot_digest"] == d["snapshot_digest"]
    assert s == snapshot() and d == demand(s)


def test_exact_boundary_admitted_and_shortfall_denied():
    s = snapshot()
    d = demand(s)
    d["proposed_upper_units"] = 55
    boundary = evaluate_admission(s, d, now=NOW)
    assert (boundary["state"], boundary["remaining_after_proposal_units"]) == ("admissible", "0")
    d["proposed_upper_units"] = 55.01
    result = evaluate_admission(s, d, now=NOW)
    assert (result["state"], result["reason"]) == ("denied", "insufficient_conservative_headroom")


def test_known_zero_is_denied_but_unknown_is_not_zero():
    s = snapshot()
    s["remaining_lower_bound"] = 0
    assert evaluate_admission(s, demand(s), now=NOW)["state"] == "denied"
    s["remaining_lower_bound"] = None
    result = evaluate_admission(s, demand(s), now=NOW)
    assert (result["state"], result["reason"], result["remaining_after_proposal_units"]) == (
        "unknown", "remaining_unknown", None
    )


@pytest.mark.parametrize("field,reason", [
    ("uncertainty_upper_units", "uncertainty_unbounded"),
    ("external_residual_upper_units", "external_residual_unbounded"),
])
def test_unbounded_cost_terms_fail_closed(field, reason):
    s = snapshot()
    s[field] = None
    result = evaluate_admission(s, demand(s), now=NOW)
    assert (result["state"], result["reason"]) == ("unknown", reason)


@pytest.mark.parametrize("field,value,code", [
    ("remaining_lower_bound", True, "invalid_number"),
    ("remaining_lower_bound", -1, "invalid_number"),
    ("remaining_lower_bound", float("inf"), "invalid_number"),
    ("remaining_lower_bound", 10**30, "invalid_number"),
    ("observed_at_utc", "2026-09-28T14:00:00+02:00", "invalid_utc"),
    ("source_digest", "abc", "invalid_digest"),
])
def test_rejected_malformed_evidence_is_reproduced(field, value, code):
    s = snapshot()
    d = demand(s)
    s[field] = value
    with pytest.raises(ContractError) as exc:
        parse_snapshot(s)
    assert exc.value.code == code
    result = evaluate_admission(s, d, now=NOW)
    assert (result["state"], result["reason"], result["detail_code"]) == (
        "unknown", "invalid_snapshot", code
    )


def test_unknown_keys_and_missing_fields_do_not_silently_default():
    s = snapshot()
    s["unreviewed_override"] = 1
    with pytest.raises(ContractError, match="unknown_field"):
        parse_snapshot(s)
    del s["unreviewed_override"]
    del s["pool_id"]
    with pytest.raises(ContractError, match="missing_field"):
        parse_snapshot(s)


@pytest.mark.parametrize("change,reason", [
    ({"provider": "different"}, "binding_mismatch"),
    ({"account_id": "different"}, "binding_mismatch"),
    ({"pool_id": "different"}, "binding_mismatch"),
    ({"limit_id": "different"}, "binding_mismatch"),
    ({"reset_epoch": "new-epoch"}, "binding_mismatch"),
    ({"unit": "tokens"}, "binding_mismatch"),
    ({"snapshot_digest": DIGEST}, "snapshot_digest_mismatch"),
])
def test_binding_or_digest_drift_is_unknown(change, reason):
    s = snapshot()
    d = demand(s)
    d.update(change)
    result = evaluate_admission(s, d, now=NOW)
    assert (result["state"], result["reason"]) == ("unknown", reason)


@pytest.mark.parametrize("at,reason", [
    (datetime(2026, 9, 28, 11, 58, tzinfo=timezone.utc), "future_observation"),
    (datetime(2026, 9, 28, 12, 10, tzinfo=timezone.utc), "snapshot_expired"),
    (datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc), "snapshot_expired"),
])
def test_observation_freshness_and_reset_boundary(at, reason):
    s = snapshot()
    result = evaluate_admission(s, demand(s), now=at)
    assert (result["state"], result["reason"]) == ("unknown", reason)


def test_duplicate_or_wrong_pool_reservations_do_not_reduce_to_a_false_total():
    s = snapshot()
    d = demand(s)
    d["reservations"].append(deepcopy(d["reservations"][0]))
    assert evaluate_admission(s, d, now=NOW)["reason"] == "invalid_demand"
    d["reservations"][1]["id"] = "active-2"
    d["reservations"][1]["pool_id"] = "other-pool"
    assert evaluate_admission(s, d, now=NOW)["reason"] == "reservation_binding_mismatch"


def test_wrong_reservation_account_or_forecast_horizon_is_unknown():
    s = snapshot()
    d = demand(s)
    d["reservations"][0]["account_id"] = "other-account"
    assert evaluate_admission(s, d, now=NOW)["reason"] == "reservation_binding_mismatch"
    d = demand(s)
    d["forecast_through_utc"] = "2026-09-30T00:00:00Z"
    assert evaluate_admission(s, d, now=NOW)["reason"] == "forecast_horizon_mismatch"


def test_unverified_binding_and_invalid_now_are_unknown():
    s = snapshot()
    s["binding_state"] = "unknown"
    assert evaluate_admission(s, demand(s), now=NOW)["reason"] == "binding_unverified"
    assert evaluate_admission(snapshot(), demand(snapshot()), now=NOW.replace(tzinfo=None))["reason"] == "invalid_now"


@pytest.mark.parametrize("tiny", [1e-30, 5e-324])
def test_tiny_positive_cost_is_not_rounded_away(tiny):
    s = snapshot()
    s.update(remaining_lower_bound=1e15, uncertainty_upper_units=0,
             external_residual_upper_units=0)
    d = demand(s)
    d.update(reservations=[], forecast_upper_units=0, incident_reserve_units=0,
             reviewer_reserve_units=tiny, proposed_upper_units=1e15)
    result = evaluate_admission(s, d, now=NOW)
    assert (result["state"], result["reason"]) == (
        "denied", "insufficient_conservative_headroom"
    )
    assert result["remaining_after_proposal_units"].startswith("-0.")
    assert result["remaining_after_proposal_units"] != "0"


def test_reservation_sum_is_exact_and_context_independent():
    s = snapshot()
    s.update(remaining_lower_bound=1e15, uncertainty_upper_units=0,
             external_residual_upper_units=0)
    d = demand(s)
    d.update(forecast_upper_units=0, incident_reserve_units=0,
             reviewer_reserve_units=0, proposed_upper_units=1,
             reservations=[
                 {"id": "large", "upper_units": 999999999999999, **{
                     key: s[key] for key in ("provider", "account_id", "pool_id", "limit_id", "reset_epoch", "unit")
                 }},
                 {"id": "tiny", "upper_units": 5e-324, **{
                     key: s[key] for key in ("provider", "account_id", "pool_id", "limit_id", "reset_epoch", "unit")
                 }},
             ])
    with localcontext() as caller:
        caller.prec = 2
        caller.Emax = 9
        caller.Emin = -9
        caller.traps[Inexact] = True
        before = caller.copy()
        result = evaluate_admission(s, d, now=NOW)
        assert repr(caller) == repr(before)
    assert (result["state"], result["reason"]) == (
        "denied", "insufficient_conservative_headroom"
    )


def test_caller_rounding_and_traps_do_not_change_verdict_or_context():
    s = snapshot()
    s.update(remaining_lower_bound=996, uncertainty_upper_units=0,
             external_residual_upper_units=0)
    d = demand(s)
    d.update(reservations=[], forecast_upper_units=0, incident_reserve_units=0,
             reviewer_reserve_units=0, proposed_upper_units=1000)
    with localcontext() as caller:
        caller.prec = 2
        caller.traps[Inexact] = True
        before = caller.copy()
        result = evaluate_admission(s, d, now=NOW)
        assert repr(caller) == repr(before)
    assert (result["state"], result["remaining_after_proposal_units"]) == ("denied", "-4")


@pytest.mark.parametrize("location", ["snapshot", "demand", "reservation"])
def test_huge_int_is_stably_rejected_before_string_conversion(location):
    s = snapshot()
    d = demand(s)
    huge = 10**5000
    if location == "snapshot":
        s["remaining_lower_bound"] = huge
        expected = "invalid_snapshot"
    elif location == "demand":
        d["proposed_upper_units"] = huge
        expected = "invalid_demand"
    else:
        d["reservations"][0]["upper_units"] = huge
        expected = "invalid_demand"
    result = evaluate_admission(s, d, now=NOW)
    assert (result["state"], result["reason"], result["detail_code"]) == (
        "unknown", expected, "invalid_number"
    )


@pytest.mark.parametrize("observed,expires,reset", [
    ("2026-09-28T12:10:00Z", "2026-09-28T12:10:00Z", "2026-10-01T00:00:00Z"),
    ("2026-09-28T12:11:00Z", "2026-09-28T12:10:00Z", "2026-10-01T00:00:00Z"),
    ("2026-09-28T11:59:00Z", "2026-10-02T00:00:00Z", "2026-10-01T00:00:00Z"),
])
def test_snapshot_time_order_is_enforced(observed, expires, reset):
    s = snapshot()
    s.update(observed_at_utc=observed, expires_at_utc=expires, reset_at_utc=reset)
    with pytest.raises(ContractError, match="invalid_time_order"):
        parse_snapshot(s)
    result = evaluate_admission(s, demand(s), now=NOW)
    assert (result["reason"], result["detail_code"]) == (
        "invalid_snapshot", "invalid_time_order"
    )


def test_reservation_sum_cap_and_count_are_enforced():
    s = snapshot()
    d = demand(s)
    template = d["reservations"][0]
    d["reservations"] = [
        {**template, "id": "one", "upper_units": MAX_UNITS_INT},
        {**template, "id": "two", "upper_units": 1},
    ]
    result = evaluate_admission(s, d, now=NOW)
    assert (result["reason"], result["detail_code"]) == (
        "invalid_demand", "reservation_total_out_of_bounds"
    )
    d["reservations"] = [{**template, "id": f"r{i}", "upper_units": 0}
                         for i in range(MAX_RESERVATIONS + 1)]
    result = evaluate_admission(s, d, now=NOW)
    assert (result["reason"], result["detail_code"]) == (
        "invalid_demand", "invalid_reservations"
    )


def test_invalid_binding_state_is_rejected():
    s = snapshot()
    s["binding_state"] = "implicitly_verified"
    result = evaluate_admission(s, demand(s), now=NOW)
    assert (result["reason"], result["detail_code"]) == (
        "invalid_snapshot", "invalid_binding_state"
    )


@pytest.mark.parametrize("field,reason", [
    ("forecast_upper_units", "forecast_unknown"),
    ("incident_reserve_units", "incident_reserve_unknown"),
    ("reviewer_reserve_units", "reviewer_reserve_unknown"),
    ("proposed_upper_units", "proposed_upper_unknown"),
])
def test_unknown_demand_cost_is_not_substituted_with_zero(field, reason):
    s = snapshot()
    d = demand(s)
    d[field] = None
    result = evaluate_admission(s, d, now=NOW)
    assert (result["state"], result["reason"], result["remaining_after_proposal_units"]) == (
        "unknown", reason, None
    )


def test_decision_binds_exact_demand_and_reservation_ledger():
    s = snapshot()
    d = demand(s)
    first = evaluate_admission(s, d, now=NOW)
    assert first["reservation_count"] == 1
    assert len(first["demand_digest"]) == len(first["reservation_ledger_digest"]) == 64
    changed = deepcopy(d)
    changed["reservations"][0]["id"] = "other-active"
    second = evaluate_admission(s, changed, now=NOW)
    assert first["remaining_after_proposal_units"] == second["remaining_after_proposal_units"]
    assert first["demand_digest"] != second["demand_digest"]
    assert first["reservation_ledger_digest"] != second["reservation_ledger_digest"]
    changed["reservations"] = []
    third = evaluate_admission(s, changed, now=NOW)
    assert third["reservation_count"] == 0
    assert third["reservation_ledger_digest"] != second["reservation_ledger_digest"]


@pytest.mark.parametrize("stamp", [
    "2026-09-28 11:59:00Z", "2026-09-28T11:59:00.1234567Z",
    "2026-W40-1T11:59:00Z", "20260928T115900Z",
    "2026-09-28T11Z", "2026-09-28T11:59:00+00:00",
])
def test_noncanonical_utc_is_rejected_without_truncation(stamp):
    s = snapshot()
    s["observed_at_utc"] = stamp
    with pytest.raises(ContractError, match="invalid_utc"):
        parse_snapshot(s)


def test_float_units_follow_json_text_semantics():
    s = snapshot()
    s.update(remaining_lower_bound=1, uncertainty_upper_units=0,
             external_residual_upper_units=0)
    d = demand(s)
    d.update(reservations=[], forecast_upper_units=0, incident_reserve_units=0,
             reviewer_reserve_units=0, proposed_upper_units=0.1)
    assert evaluate_admission(s, d, now=NOW)["remaining_after_proposal_units"] == "0.9"


@pytest.mark.parametrize("location", ["snapshot", "demand", "reservation"])
def test_float_above_max_units_is_rejected_in_every_numeric_path(location):
    s = snapshot()
    d = demand(s)
    if location == "snapshot":
        s["remaining_lower_bound"] = 1e16
        reason = "invalid_snapshot"
    elif location == "demand":
        d["proposed_upper_units"] = 1e16
        reason = "invalid_demand"
    else:
        d["reservations"][0]["upper_units"] = 1e16
        reason = "invalid_demand"
    result = evaluate_admission(s, d, now=NOW)
    assert (result["state"], result["reason"], result["detail_code"]) == (
        "unknown", reason, "invalid_number"
    )


@pytest.mark.parametrize("location", ["snapshot", "demand", "reservation"])
def test_exact_max_units_float_is_accepted(location):
    s = snapshot()
    s.update(remaining_lower_bound=1e15, uncertainty_upper_units=0,
             external_residual_upper_units=0)
    d = demand(s)
    d.update(reservations=[], forecast_upper_units=0, incident_reserve_units=0,
             reviewer_reserve_units=0, proposed_upper_units=0)
    if location == "snapshot":
        s["remaining_lower_bound"] = 1e15
        d["snapshot_digest"] = snapshot_digest(s)
        d["proposed_upper_units"] = 1e15
    elif location == "demand":
        d["proposed_upper_units"] = 1e15
    else:
        template = demand(s)["reservations"][0]
        d["reservations"] = [{**template, "upper_units": 1e15}]
    result = evaluate_admission(s, d, now=NOW)
    assert (result["state"], result["remaining_after_proposal_units"]) == (
        "admissible", "0"
    )


def test_process_default_context_cannot_change_exact_verdict_or_mutate_contexts():
    s = snapshot()
    s.update(remaining_lower_bound=100, uncertainty_upper_units=0,
             external_residual_upper_units=0)
    d = demand(s)
    d.update(reservations=[], forecast_upper_units=0, incident_reserve_units=0,
             reviewer_reserve_units=0, proposed_upper_units=20)
    tiny = snapshot()
    tiny.update(remaining_lower_bound=1e15, uncertainty_upper_units=0,
                external_residual_upper_units=0)
    tiny_demand = demand(tiny)
    tiny_demand.update(reservations=[], forecast_upper_units=0,
                       incident_reserve_units=0, reviewer_reserve_units=5e-324,
                       proposed_upper_units=1e15)
    original_default = DefaultContext.copy()
    original_caller = getcontext().copy()
    try:
        DefaultContext.prec = 2
        DefaultContext.rounding = ROUND_UP
        DefaultContext.Emax = 1
        DefaultContext.Emin = -1
        DefaultContext.capitals = 0
        DefaultContext.clamp = 1
        for signal in original_default.flags:
            DefaultContext.flags[signal] = True
            DefaultContext.traps[signal] = not original_default.traps[signal]
        hostile_default = repr(DefaultContext)
        ordinary = evaluate_admission(s, d, now=NOW)
        shortfall = evaluate_admission(tiny, tiny_demand, now=NOW)
        assert repr(DefaultContext) == hostile_default
        assert repr(getcontext()) == repr(original_caller)
    finally:
        for field in ("prec", "rounding", "Emax", "Emin", "capitals", "clamp"):
            setattr(DefaultContext, field, getattr(original_default, field))
        for signal in original_default.flags:
            DefaultContext.flags[signal] = original_default.flags[signal]
            DefaultContext.traps[signal] = original_default.traps[signal]
    assert (ordinary["state"], ordinary["remaining_after_proposal_units"]) == (
        "admissible", "80"
    )
    assert shortfall["state"] == "denied"
    assert Decimal(shortfall["remaining_after_proposal_units"]) == Decimal("-5e-324")
    assert repr(DefaultContext) == repr(original_default)
