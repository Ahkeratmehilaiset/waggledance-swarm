#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Lane profile planner (D5 of lane profile switching): shadow decisions only.

For one lane, from measured inputs, the planner chooses whether a catalog
profile change is warranted and which one. It returns a decision record; it
never relaunches, writes a lane record or sends anything. In ``shadow`` - the
only effective mode while the advisor is shadow-only - the decision is
``would_relaunch`` at most, which an operator reviews against the catalog's
shadow exit criterion.

Rules (spec v3 D5):

* The current profile must be known from a valid session binding for this
  lane; otherwise PARK.
* Admission ``KEEP`` with an available current quota bucket: keep.
* Admission ``PARK`` or anything unknown: park.
* The current bucket exhausted or limited, or admission ``ESCALATE``: look for
  another allowed profile, strongest first, within the floor.
* Never propose a profile whose quota bucket is not ``available``. Raising
  into an exhausted or unknown bucket is a stall, not a fix. All Claude
  profiles share the ``claude`` bucket, so exhaustion there has no Claude escape.
* Reviewer lanes: raise or park, never lower.
* The candidate must pass ``check_request`` (budgets, cooldown, floor).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from tools.lane_profile_catalog import effective_mode
from tools.wd_lane_relaunch import PROCEED, check_request

KEEP = "keep"
WOULD_RELAUNCH = "would_relaunch"
PARK = "park"
AVAILABLE = "available"


def profile_for_observation(catalog: dict, lane: str, model: Any, effort: Any) -> str | None:
    """The allowed profile id matching an observed (model, effort), or None."""
    profiles = catalog["capacity_policy"]["profiles"]
    for profile_id in catalog["lanes"][lane]["allowed_profiles"]:
        profile = profiles[profile_id]
        if profile["model"] == model and profile["effort"] == effort:
            return profile_id
    return None


def bucket_state(catalog: dict, profile_id: str, quota_states: dict) -> str:
    """The weakest state across every quota bucket of the profile; unknown if any is missing."""
    profile = catalog["capacity_policy"]["profiles"][profile_id]
    order = ("available", "limited", "exhausted", "unknown")
    worst = "available"
    for limit in profile["limits"]:
        state = quota_states.get((profile["provider"], limit["id"]), "unknown")
        if state not in order:
            state = "unknown"
        worst = max(worst, state, key=order.index)
    return worst


def _decision(lane: str, action: str, reasons: list[str], mode: str, **extra: Any) -> dict:
    return {"schema": "wd.lane-profile-plan.v1", "lane": lane, "action": action,
            "reasons": reasons, "mode": mode, "execution_allowed": False, **extra}


def plan_lane(catalog: dict, catalog_sha256: str, lane: str, *, binding: dict, admission: str,
              quota_states: dict, history: list, now: datetime | None = None) -> dict:
    """One shadow decision for one lane; see the module docstring for the rules."""
    now = now or datetime.now(timezone.utc)
    mode = effective_mode(catalog)
    base = {"catalog_sha256": catalog_sha256, "decided_at": now.isoformat()}
    if lane not in catalog["lanes"]:
        return _decision(lane, PARK, ["lane_not_in_catalog"], mode, **base)
    if not isinstance(binding, dict) or binding.get("session_identity") != "valid":
        return _decision(lane, PARK, ["current_profile_unverified"], mode, **base)
    if binding.get("lane") != lane:
        # Lead review of #1738: a binding for another lane must not count as a
        # decision for this one, even in shadow, or it corrupts the exit metrics.
        return _decision(lane, PARK, ["binding_names_another_lane"], mode, **base)
    current = profile_for_observation(catalog, lane, _strip(binding.get("observed_model_raw")),
                                      binding.get("observed_effort"))
    if current is None:
        return _decision(lane, PARK, ["current_profile_not_in_catalog"], mode, **base)
    current_state = bucket_state(catalog, current, quota_states)
    if admission not in ("KEEP", "PARK", "ESCALATE"):
        return _decision(lane, PARK, ["admission_unknown"], mode, current_profile=current, **base)
    if admission == "PARK":
        return _decision(lane, PARK, ["admission_park"], mode, current_profile=current, **base)
    if admission == "KEEP" and current_state == AVAILABLE:
        return _decision(lane, KEEP, ["current_profile_healthy"], mode, current_profile=current, **base)
    spec = catalog["lanes"][lane]
    allowed = spec["allowed_profiles"]
    current_index = allowed.index(current)
    # Strongest first; within the floor; reviewers may only raise.
    candidates = [p for i, p in enumerate(allowed) if i <= spec["floor"] and p != current
                  and not (spec["reviewer"] and i > current_index)]
    if admission == "ESCALATE":
        candidates = [p for p in candidates if allowed.index(p) < current_index]
    why = "admission_escalate" if admission == "ESCALATE" else f"current_bucket_{current_state}"
    rejected = []
    for target in candidates:
        state = bucket_state(catalog, target, quota_states)
        if state != AVAILABLE:
            rejected.append(f"{target}:bucket_{state}")
            continue
        check = check_request(catalog, {"lane": lane, "current_profile": current,
                                        "target_profile": target}, history, now=now)
        if check["verdict"] != PROCEED:
            rejected.append(f"{target}:{'+'.join(check['reasons'])}")
            continue
        return _decision(lane, WOULD_RELAUNCH, [why], mode, current_profile=current,
                         target_profile=target, **base)
    return _decision(lane, PARK, [why, "no_admissible_candidate"], mode, current_profile=current,
                     rejected=rejected, **base)


def _strip(model: Any) -> Any:
    if isinstance(model, str) and model.endswith("]") and "[" in model:
        return model[: model.rindex("[")]
    return model
