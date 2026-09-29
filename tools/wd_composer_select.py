"""Pure composer selection for Bridge v2 (F24; plan section 2.10).

``select(evidence)`` advises which catalog profile composes. It uses injected
evidence only: the F0 decision for feature F24 and the ``f24_composer_rule``
policy bit (both default OFF), the signed parameters, one frozen model-index
snapshot pinned by digest, and per-profile measurements. It reads no file,
clock, environment, network or provider and actuates nothing. The result
always carries ``execution_allowed`` False and ``authority`` "none".

Step 1 (eligibility) runs before step 2 (ranking). A profile with missing or
stale evidence is ineligible, and it never becomes a fallback. Step 2 compares
only the pinned snapshot's scores on the signed index name and version, bound
to the profile's exact provider, model and effort. If any candidate cannot be
ranked, the verdict is composer_unknown, never a guess. The measured quota
cost is only a tie-breaker. It must be in an F3 quota unit (never an API price),
on the profile's own pool, and on a stated window and workload basis.

Every receipt (identity, auth, turn, pool) must name the profile it is about.
The wait for an unavailable top is bounded per task: it starts at the task's
creation, so no caller-held wait record can extend or shorten it.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timedelta
from typing import Any

from tools.lane_profile_record import _utc
from tools.wd_model_registry import QUOTA_UNITS

SCHEMA = "wd.composer-selection.v1"
EVIDENCE_SCHEMA = "wd.composer-evidence.v1"
SNAPSHOT_SCHEMA = "wd.composer-index-snapshot.v1"
FEATURE = "F24"
POLICY_BIT = "f24_composer_rule"
COMPOSER, WAIT, FALLBACK, UNKNOWN, HOLD = "composer", "wait", "composer_fallback", "composer_unknown", "hold"
# Plan section 2.2 trip lines. This duplicates wd_switch_policy.TRIP_LINES (F15, on another
# branch); dedupe the two at integration.
TRIP_LINES = {"steady": 70.0, "burst": 90.0, "sprint": 95.0}
BLOCKED_POOL_STATES = ("exhausted", "conserve")
POOL_STATES = ("available",) + BLOCKED_POOL_STATES
PROFILE_KEYS = ("profile_id", "provider", "model", "effort")
HEX = frozenset("0123456789abcdef")


class _Hold(Exception):
    def __init__(self, *reasons: str) -> None:
        super().__init__(reasons[0] if reasons else HOLD)
        self.reasons = list(reasons)


class _Unknown(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _require(condition: bool, *reasons: str) -> None:
    if not condition:
        raise _Hold(*reasons)


def digest(value: Any) -> str | None:
    """sha256 of the canonical JSON form. Returns None if the value has no exact JSON form (NaN, non-string keys, tuples...)."""
    try:
        text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        if json.loads(text) != value:
            return None
    except (TypeError, ValueError, RecursionError):
        return None
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def _number(value: Any) -> float | None:
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    return float(value)


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _hex64(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= HEX


def _date(value: Any) -> date | None:
    if not isinstance(value, str) or len(value) != 10 or value[4] != "-" or value[7] != "-":
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _fresh(block: Any, now: datetime, max_age: int) -> bool:
    if not isinstance(block, dict):
        return False
    observed = _utc(block.get("observed_utc"))
    return observed is not None and now - timedelta(seconds=max_age) <= observed <= now


def _gate(evidence: dict) -> dict:
    """Default OFF: the F0 decision for F24 and the policy bit must both be on, bound to one signed policy."""
    decision = evidence.get("f0")
    _require(isinstance(decision, dict) and decision.get("feature") == FEATURE
             and decision.get("enabled") is True, "feature_disabled")
    policy = decision.get("policy_sha256")
    _require(_hex64(policy), "feature_disabled", "f0_policy_unbound")
    bits = evidence.get("policy_bits")
    _require(isinstance(bits, dict) and bits.get("policy_sha256") == policy and bits.get(POLICY_BIT) is True,
             "feature_disabled", "policy_bit_off:" + POLICY_BIT)
    parameters = evidence.get("parameters")
    _require(isinstance(parameters, dict) and parameters.get("policy_sha256") == policy, "parameters_unbound")
    efforts = parameters.get("planning_efforts")
    epsilon = _number(parameters.get("epsilon"))
    bound = _number(parameters.get("plausibility_bound"))
    _require(_text(parameters.get("index_name")) and _text(parameters.get("index_version"))
             and _hex64(parameters.get("registry_sha256"))
             and epsilon is not None and epsilon >= 0 and bound is not None and bound > 0
             and all(type(parameters.get(k)) is int and parameters[k] > 0
                     for k in ("max_evidence_age_seconds", "max_score_age_days"))
             and type(parameters.get("max_wait_seconds")) is int and parameters["max_wait_seconds"] >= 0
             and parameters.get("budget_mode") in TRIP_LINES
             and isinstance(efforts, list) and efforts and all(_text(e) for e in efforts), "parameters_invalid")
    return parameters


def _eligibility(profile: dict, parameters: dict, now: datetime) -> tuple[str, list[str]]:
    """Step 1: ("ineligible" | "unavailable" | "available", reasons). Missing or stale evidence is ineligible."""
    max_age = parameters["max_evidence_age_seconds"]
    reasons = []
    if profile.get("signed_in_envelope") is not True:
        reasons.append("not_in_signed_envelope")
    if profile["effort"] not in parameters["planning_efforts"]:
        reasons.append("effort_not_allowed_for_planning")
    identity = profile.get("identity")
    if not (_fresh(identity, now, max_age) and identity.get("verified") is True):
        reasons.append("identity_unverified_or_stale")
    elif any(identity.get(k) != profile[k] for k in PROFILE_KEYS):
        reasons.append("identity_mismatch")
    # A fresh positive receipt about another subject proves nothing about this profile.
    auth = profile.get("auth")
    if not (_fresh(auth, now, max_age) and auth.get("verified") is True):
        reasons.append("auth_unverified_or_stale")
    elif auth.get("profile_id") != profile["profile_id"]:
        reasons.append("auth_unbound")
    turn = profile.get("turn")
    if not (_fresh(turn, now, max_age) and type(turn.get("ok")) is bool):
        reasons.append("turn_unknown_or_stale")
    elif turn.get("profile_id") != profile["profile_id"]:
        reasons.append("turn_unbound")
    pool = profile.get("pool")
    if not (_fresh(pool, now, max_age) and _text(pool.get("pool_id")) and pool.get("state") in POOL_STATES
            and type(pool.get("provider_up")) is bool):
        reasons.append("quota_unknown_or_stale")
    elif pool.get("profile_id") != profile["profile_id"] or pool.get("provider") != profile["provider"]:
        reasons.append("quota_unbound")
    else:
        projected = _number(pool.get("projected_used_percent"))
        if projected is None or projected < 0:
            reasons.append("quota_unknown_or_stale")
        elif projected > TRIP_LINES[parameters["budget_mode"]]:
            reasons.append("budget_over_trip_line")
    if reasons:
        return "ineligible", reasons
    blocked = ["pool_" + pool["state"]] if pool["state"] in BLOCKED_POOL_STATES else []
    if pool["provider_up"] is not True:
        blocked.append("provider_down")
    if turn["ok"] is not True:
        blocked.append("turn_not_ok")
    return ("unavailable", blocked) if blocked else ("available", [])


def _entries(snapshot: Any, parameters: dict, *, pinned: bool) -> dict | None:
    """Snapshot entries keyed by the exact (provider, model, effort). Raises _Unknown if the snapshot is unusable.

    Returns None for an unpinned (previous) snapshot on another index. That is a new signed
    index, not a refresh, so the plausibility check does not apply to it.
    """
    if not isinstance(snapshot, dict) or snapshot.get("schema") != SNAPSHOT_SCHEMA:
        raise _Unknown("registry_snapshot_malformed")
    if pinned and digest(snapshot) != parameters["registry_sha256"]:
        raise _Unknown("registry_digest_mismatch")
    if (snapshot.get("index_name"), snapshot.get("index_version")) != (parameters["index_name"],
                                                                     parameters["index_version"]):
        if pinned:
            raise _Unknown("registry_index_mismatch")
        return None
    entries = snapshot.get("entries")
    if not isinstance(entries, list):
        raise _Unknown("registry_snapshot_malformed")
    table: dict = {}
    for entry in entries:
        key = tuple(entry.get(k) for k in PROFILE_KEYS[1:]) if isinstance(entry, dict) else None
        if key is None or not all(_text(part) for part in key):
            raise _Unknown("registry_snapshot_malformed")
        if key in table:
            raise _Unknown("registry_duplicate_entry")
        table[key] = entry
    return table


def _score(entry: Any, previous: Any, parameters: dict, now: datetime) -> tuple[dict | None, str]:
    """A comparable, dated, plausible score for one exact (provider, model, effort), or the reason there is none."""
    if entry is None:
        return None, "no_registry_entry"
    score, uncertainty = _number(entry.get("score")), _number(entry.get("uncertainty"))
    coding = entry.get("coding_score")
    if score is None or uncertainty is None or uncertainty < 0 or (coding is not None and _number(coding) is None):
        return None, "score_malformed"
    measured = _date(entry.get("measured_on"))
    if measured is None:
        return None, "score_undated"
    if measured > now.date() or (now.date() - measured).days > parameters["max_score_age_days"]:
        return None, "score_stale"
    coding = None if coding is None else _number(coding)
    if previous is not None:
        old, old_measured = _number(previous.get("score")), _date(previous.get("measured_on"))
        old_uncertainty, old_coding = _number(previous.get("uncertainty")), previous.get("coding_score")
        if old is None or old_measured is None or old_uncertainty is None \
                or (old_coding is not None and _number(old_coding) is None):
            return None, "previous_score_malformed"
        old_coding = None if old_coding is None else _number(old_coding)
        if old_measured > measured:
            return None, "score_measurement_regressed"
        # Every metric that can change the order (score, uncertainty, coding) needs a new measurement.
        if (old, old_uncertainty, old_coding) != (score, uncertainty, coding) and old_measured == measured:
            return None, "refresh_without_new_measurement"
        bound = parameters["plausibility_bound"]
        if abs(score - old) > bound or (coding is not None and old_coding is not None
                                        and abs(coding - old_coding) > bound):
            return None, "refresh_implausible"
    return {"score": score, "uncertainty": uncertainty, "coding": coding}, ""


def _cost(profile: dict, parameters: dict, now: datetime) -> dict | None:
    """The measured quota cost (a tie-breaker only), or None, which counts as missing.

    Only an F3 quota unit counts (API dollars never do), measured on this profile's own
    pool, with a stated window and workload. Its basis (unit, window, workload) must be
    the same for every tied member, or the key is skipped."""
    cost = profile.get("measured_quota_cost")
    pool = profile.get("pool")
    if not _fresh(cost, now, parameters["max_evidence_age_seconds"]) or cost.get("unit") not in QUOTA_UNITS \
            or not isinstance(pool, dict) or cost.get("pool_id") != pool.get("pool_id") \
            or not _text(cost.get("window")) or not _text(cost.get("workload")):
        return None
    value = _number(cost.get("value"))
    return None if value is None or value < 0 else {"value": value,
                                                   "basis": (cost["unit"], cost["window"], cost["workload"])}


def _tied(a: dict, b: dict, epsilon: float) -> bool:
    return abs(a["score"] - b["score"]) <= epsilon or (
        a["score"] - a["uncertainty"] <= b["score"] + b["uncertainty"]
        and b["score"] - b["uncertainty"] <= a["score"] + a["uncertainty"])


def _break_tie(group: list[dict]) -> dict:
    """Break a tie by the coding index (higher), then the measured quota cost (lower, same unit only), then profile_id.

    A key is skipped for the whole group if any member lacks it.
    """
    keys = []
    if all(c["coding"] is not None for c in group):
        keys.append(lambda c: -c["coding"])
    bases = {c["cost"]["basis"] if c["cost"] else None for c in group}
    if None not in bases and len(bases) == 1:
        keys.append(lambda c: c["cost"]["value"])
    keys.append(lambda c: c["profile_id"])
    return min(group, key=lambda c: tuple(k(c) for k in keys))


def rank(candidates: list[dict], epsilon: float) -> list[dict]:
    """Return a total order: repeatedly take the tie group of the highest remaining score and break its tie.

    The group is anchored on the highest remaining score. "Tied" is not transitive: a member
    tied with the anchor may be tied with a third profile that is not tied with the anchor.
    That third profile is compared again in a later round, so the order stays deterministic."""
    remaining = sorted(candidates, key=lambda c: (-c["score"], c["profile_id"]))
    order = []
    while remaining:
        winner = _break_tie([c for c in remaining if _tied(remaining[0], c, epsilon)])
        order.append(winner)
        remaining = [c for c in remaining if c is not winner]
    return order


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _result(verdict: str, reasons: list[str], context: dict, *, selected: dict | None = None,
            provisional: dict | None = None, waiting_for: dict | None = None,
            deadline: datetime | None = None) -> dict:
    chosen = selected or provisional
    return {
        "schema": SCHEMA,
        "verdict": verdict,
        "reasons": reasons,
        "selected_profile": selected["profile_id"] if selected else None,
        "provisional_profile": provisional["profile_id"] if provisional else None,
        "route": None if chosen is None else ("grok_consult" if chosen["provider"] == "grok" else "direct"),
        "waiting_for": waiting_for["profile_id"] if waiting_for else None,
        "wait_deadline_utc": _stamp(deadline) if deadline else None,
        # A wait (or its fallback) is bound to this task, this policy and this snapshot.
        "task_id": context.get("task_id"),
        "policy_sha256": context.get("policy_sha256"),
        "ranking": context.get("ranking", []),
        "ineligible": context.get("ineligible", {}),
        "unavailable": context.get("unavailable", {}),
        "registry_sha256": context.get("registry_sha256"),
        "inputs_digest": context.get("inputs_digest"),
        "execution_allowed": False,
        "authority": "none",
    }


def _select(evidence: Any, context: dict) -> dict:
    _require(isinstance(evidence, dict) and evidence.get("schema") == EVIDENCE_SCHEMA, "evidence_malformed")
    now = _utc(evidence.get("now_utc"))
    _require(now is not None, "evidence_malformed", "now_utc")
    parameters = _gate(evidence)
    context["policy_sha256"] = parameters["policy_sha256"]
    profiles = evidence.get("profiles")
    _require(isinstance(profiles, list), "evidence_malformed", "profiles")
    for profile in profiles:
        _require(isinstance(profile, dict) and all(_text(profile.get(k)) for k in PROFILE_KEYS),
                 "evidence_malformed", "profile")
    ids = [p["profile_id"] for p in profiles]
    _require(len(set(ids)) == len(ids), "evidence_malformed", "duplicate_profile_id")
    task = evidence.get("task")
    created = _utc(task.get("created_utc")) if isinstance(task, dict) else None
    _require(created is not None and created <= now and _text(task.get("task_id")), "evidence_malformed", "task")
    context["task_id"] = task["task_id"]
    status = {p["profile_id"]: _eligibility(p, parameters, now) for p in profiles}
    context["ineligible"] = {pid: why for pid, (state, why) in sorted(status.items()) if state == "ineligible"}
    context["unavailable"] = {pid: why for pid, (state, why) in sorted(status.items()) if state == "unavailable"}
    candidates = [p for p in profiles if status[p["profile_id"]][0] != "ineligible"]
    _require(bool(candidates), "no_eligible_profile")

    snapshot = evidence.get("registry_snapshot")
    context["registry_sha256"] = digest(snapshot)
    try:
        table = _entries(snapshot, parameters, pinned=True)
        previous = evidence.get("previous_snapshot")
        prior = None if previous is None else _entries(previous, parameters, pinned=False)
    except _Unknown as unknown:
        return _result(UNKNOWN, ["ranking_unknown", unknown.reason], context)
    scored, unranked = [], []
    for profile in candidates:
        key = tuple(profile[k] for k in PROFILE_KEYS[1:])
        score, why = _score(table.get(key), (prior or {}).get(key), parameters, now)
        if score is None:
            unranked.append("unranked:" + profile["profile_id"] + ":" + why)
            continue
        scored.append({**score, "profile_id": profile["profile_id"], "provider": profile["provider"],
                       "cost": _cost(profile, parameters, now), "state": status[profile["profile_id"]][0]})
    order = rank(scored, parameters["epsilon"])
    context["ranking"] = [c["profile_id"] for c in order]
    available = [c for c in order if c["state"] == "available"]
    if unranked:
        # An unranked candidate may outrank every scored one, so nothing is selected. At most a
        # labelled provisional pick is offered (plan 2.10).
        return _result(UNKNOWN, ["ranking_unknown"] + unranked, context,
                       provisional=available[0] if available else None)
    top = order[0]
    if top["state"] == "available":
        return _result(COMPOSER, ["ranked_top_available"], context, selected=top)

    # Bounded per task: the wait starts when the task was created, so a caller cannot extend it by
    # omitting a wait record or cut it short with an ancient one.
    deadline = created + timedelta(seconds=parameters["max_wait_seconds"])
    if now < deadline:
        return _result(WAIT, ["top_unavailable_waiting"], context, waiting_for=top, deadline=deadline)
    if not available:
        return _result(HOLD, ["top_unavailable_deadline_passed:" + top["profile_id"], "no_available_fallback"],
                       context)
    return _result(FALLBACK, ["top_unavailable_deadline_passed:" + top["profile_id"]], context,
                   selected=available[0])


def select(evidence: Any) -> dict:
    """Composer selection advice. Never raises: every failure is a hold or composer_unknown."""
    context: dict = {"inputs_digest": digest(evidence)}
    try:
        _require(context["inputs_digest"] is not None, "evidence_malformed", "evidence_not_canonical_json")
        return _select(evidence, context)
    except _Hold as stop:
        return _result(HOLD, stop.reasons, context)
    except Exception:  # noqa: BLE001 - advice never raises; anything unexpected holds
        return _result(HOLD, ["evidence_malformed"], context)
