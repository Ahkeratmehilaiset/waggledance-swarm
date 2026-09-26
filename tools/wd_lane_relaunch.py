#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Lane relaunch decision logic (D4 of lane profile switching), pure part.

This module holds steps 1 and 2 of the relaunch order in spec v3:

1. ``check_request``: the request against the catalog, per-lane and fleet
   budgets, and the lane cooldown.
2. ``check_safe_boundary``: the target lane is at a safe boundary.

Every input is caller-measured data; every function returns a verdict and
reasons and changes nothing. There is no process control, no file I/O and no
bridge access here, and nothing in the runtime calls this module yet. The
executor (steps 3-9, restart source/target epochs, rollback, receipts) is a
separate change that drives these checks with injected ports.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from tools.lane_profile_catalog import classify_transition
from tools.lane_profile_record import _utc

PROCEED = "proceed"
PARK = "park"
ABORT = "abort"
SUPERVISOR = "supervisor"


def _verdict(verdict: str, reasons: list[str], **extra: Any) -> dict:
    return {"verdict": verdict, "reasons": reasons, "execution_allowed": False, **extra}


def check_request(catalog: dict, request: dict, history: list, *, now: datetime | None = None) -> dict:
    """Step 1: is this relaunch request within the catalog and its budgets?

    ``request``: lane, current_profile, target_profile. ``history``: prior
    transition receipts, each with lane, ts_utc and outcome; unparseable
    entries make the budget unknown, which parks. ``execution_allowed`` is
    always False: passing a check is not authority to act.
    """
    now = now or datetime.now(timezone.utc)
    lane = request.get("lane")
    spec = catalog["lanes"].get(lane)
    if spec is None:
        return _verdict(PARK, ["lane_not_in_catalog"], operator_ack_required=True)
    transition = classify_transition(catalog, lane, request.get("current_profile"),
                                     request.get("target_profile"))
    if transition["verdict"] == "park":
        return _verdict(PARK, [transition["reason"]], operator_ack_required=True)
    if transition["verdict"] == "same":
        return _verdict(ABORT, ["no_change"])
    reasons: list[str] = []
    stamps = []
    for entry in history if isinstance(history, list) else [None]:
        stamp = _utc(entry.get("ts_utc")) if isinstance(entry, dict) else None
        if stamp is None or not isinstance(entry.get("lane"), str):
            return _verdict(PARK, ["relaunch_history_unknown"], operator_ack_required=False)
        stamps.append((entry["lane"], stamp))
    hour_ago = now - timedelta(hours=1)
    lane_recent = [s for l, s in stamps if l == lane and hour_ago < s <= now]
    fleet_recent = [s for _, s in stamps if hour_ago < s <= now]
    if len(lane_recent) >= spec["max_relaunches_per_hour"]:
        reasons.append("lane_budget_exhausted")
    if len(fleet_recent) >= catalog["fleet"]["max_relaunches_per_hour_total"]:
        reasons.append("fleet_budget_exhausted")
    last = max((s for l, s in stamps if l == lane), default=None)
    if last is not None and (now - last).total_seconds() < spec["cooldown_seconds"]:
        reasons.append("lane_cooldown")
    if any(s > now for _, s in stamps):
        return _verdict(PARK, ["relaunch_history_from_the_future"], operator_ack_required=False)
    if reasons:
        return _verdict(PARK, reasons, operator_ack_required=False)
    return _verdict(PROCEED, [transition["reason"]], transition=transition["verdict"])


def check_safe_boundary(state: dict, *, now: datetime | None = None, max_age_seconds: int = 60) -> dict:
    """Step 2: may this lane be stopped now without abandoning anything?

    ``state`` is a fresh measurement of the lane: current_session_id,
    observed_at, idle, pending_effects, previous_turn_blocker, open_claims and
    unresolved_requests. Each unresolved request carries request_id,
    bound_session_id and superseded (True only when a successor session is
    recorded).

    Every unresolved request bound to the lane's current session blocks,
    regardless of age (Lead review LPS-B2): the selector's 12 h freshness
    cutoff does not apply here. A request bound to another session blocks
    unless it is provably superseded. Unknown values block. ``supervisor`` is
    never a relaunch target.
    """
    now = now or datetime.now(timezone.utc)
    reasons: list[str] = []
    if not isinstance(state, dict):
        return _verdict(ABORT, ["lane_state_missing"])
    if state.get("lane") == SUPERVISOR or state.get("is_supervisor") is not False:
        return _verdict(ABORT, ["target_is_or_may_be_the_supervisor"])
    observed = _utc(state.get("observed_at"))
    if observed is None or not 0 <= (now - observed).total_seconds() <= max_age_seconds:
        return _verdict(ABORT, ["lane_state_stale_or_unknown"])
    session = state.get("current_session_id")
    if not isinstance(session, str) or not session:
        return _verdict(ABORT, ["current_session_unknown"])
    if state.get("idle") is not True:
        reasons.append("not_idle")
    if state.get("pending_effects") is not False:
        reasons.append("pending_effects_or_unknown")
    if state.get("previous_turn_blocker") is not False:
        reasons.append("previous_turn_blocker_or_unknown")
    claims = state.get("open_claims")
    if not isinstance(claims, list):
        reasons.append("open_claims_unknown")
    elif claims:
        reasons.append("open_claims")
    requests = state.get("unresolved_requests")
    if not isinstance(requests, list):
        reasons.append("unresolved_requests_unknown")
    else:
        for item in requests:
            bound = item.get("bound_session_id") if isinstance(item, dict) else None
            if not isinstance(bound, str) or not bound:
                reasons.append("request_binding_unknown")
            elif bound == session:
                reasons.append("unresolved_request_bound_to_current_session")
            elif item.get("superseded") is not True:
                reasons.append("unresolved_request_not_provably_superseded")
    if reasons:
        return _verdict(ABORT, sorted(set(reasons)))
    return _verdict(PROCEED, ["safe_boundary"])
