#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Bridge v2 F19: pure task router. It advises; it never dispatches.

``decide(task, observed_workers, prepared_artifacts, policy, now)`` (also
``TaskRouter().decide``) returns one ``wd.task-routing-advice.v1`` record for one
task. It reads no file, clock, environment, network, provider or bridge. It never
enqueues, claims, calls a model or writes an intent. The same inputs always give the
same record, and ``evidence_digest`` lets a reviewer recompute it.

Authority: none. The advice cannot grant a role, activate a flag, request paid
capacity or relax a veto. Lead (``dispatch_authority``) stays the assignment
authority. A live caller must check its own F0 decision for F19 before it uses the
advice for anything; this module neither checks nor implies one.

Checks, in order (the first that decides wins):
1. Malformed task, policy, worker or attempt records give ``hold``. Every schema is
   closed, so a quota counter, an activation flag, a role grant or a veto override
   cannot ride in on an extra key. A task with a veto in force gives ``hold``.
2. Dedupe on the task's immutable identity. ``dispatch_key`` is the digest of the
   task id, revision, input digest and normalized scope. An accepted attempt with
   that key gives ``satisfied``, and a live lease on it gives ``duplicate``. Either
   way nothing new is dispatched, so one outcome cannot cause a second dispatch.
3. Scope: a live lease of another key whose file or resource scope overlaps gives
   ``wait``. Scope entries are compared case-folded, with ``/`` separators and
   directory prefixes, so the check errs toward a conflict.
4. Workers: role, qualification, capacity and load evidence must be present, fresh,
   verified and bound to the worker and its current profile. Missing or stale
   evidence makes the worker unknown; it is never a fallback and never ready.
5. With eligible workers the verdict is ``route``. The ranking is the signed
   per-class profile order, then capacity headroom, then the worker name. Without
   one: ``wait`` if a permitted non-Grok worker is only busy or over its budget,
   ``unknown`` if a non-Grok worker lacks evidence, ``skipped`` for advisory work,
   otherwise ``hold``.

planning_synthesis reuses the F24 composer: the task carries composer evidence, and
``wd_composer_select.select`` decides the profile. Only workers already on the
selected profile are eligible; the router never proposes a switch (F15 does).

Lease expiry never deletes or invalidates a pushed artifact. An expired attempt
stops blocking, every artifact recorded for this key is returned unchanged in
``preserved_artifacts``, and the remote-verified ones are offered in
``resume_from``. The router never changes an attempt's state.

Grok is optional. The ``grok`` worker is ranked like any other only while its
single-flight reservation is idle and no live attempt holds it. A busy or unknown
Grok never makes the router wait or report unknown. There is no hourly, weekly or
per-agent Grok quota here or in the policy schema.

Shadow weights (F26, tools/wd_routing_weights.py) may be passed. They produce a
separate ``shadow`` ranking only. ``ranking``, ``recommended`` and the verdict never
depend on them.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from tools.lane_profile_record import _utc
from tools.wd_composer_select import COMPOSER as COMPOSER_PICK
from tools.wd_composer_select import FALLBACK as COMPOSER_FALLBACK
from tools.wd_composer_select import HOLD as COMPOSER_HOLD
from tools.wd_composer_select import SCHEMA as COMPOSER_SCHEMA
from tools.wd_composer_select import UNKNOWN as COMPOSER_UNKNOWN
from tools.wd_composer_select import WAIT as COMPOSER_WAIT
from tools.wd_composer_select import digest
from tools.wd_composer_select import select as composer_select

SCHEMA = "wd.task-routing-advice.v1"
TASK_SCHEMA = "wd.routing-task.v1"
POLICY_SCHEMA = "wd.task-routing-policy.v1"
WORKER_SCHEMA = "wd.routing-worker.v1"
ATTEMPT_SCHEMA = "wd.routing-attempt.v1"
# The F26 record schema (tools/wd_routing_weights.SCHEMA). It is repeated here so this
# module does not import the learning module; a test pins the two together.
SHADOW_WEIGHTS_SCHEMA = "wd.routing-shadow-weights.v1"
FEATURE = "F19"
DISPATCH_AUTHORITY = "codex-lead-1"
GROK = "grok"
# tools/wd_switch_policy.MEMBERS and TRIP_LINES, repeated here so this module does not import the
# switch policy and, through it, the switch evidence, relaunch and planner modules; a test pins
# them together.
MEMBERS = ("codex-lead-1", "codex-tools-1", "fable-5", "claude-rco-1", "claude-rco-2")
TRIP_LINES = {"steady": 70.0, "burst": 90.0, "sprint": 95.0}

ROUTE, DUPLICATE, SATISFIED, WAIT, UNKNOWN, SKIPPED, HOLD = (
    "route", "duplicate", "satisfied", "wait", "unknown", "skipped", "hold")
VERDICTS = (ROUTE, DUPLICATE, SATISFIED, WAIT, UNKNOWN, SKIPPED, HOLD)

TASK_CLASSES = ("planning_synthesis", "implementation", "review", "test", "advisory")
# Plan 2.7: when members give one task different classes, the higher class wins.
CLASS_PRECEDENCE = ("review", "planning_synthesis", "implementation", "test", "advisory")
# Only these classes exclude the task's author as the worker (author != reviewer).
INDEPENDENT_CLASSES = ("review",)

CAPACITY_STATES = ("available", "exhausted", "conserve")
BILLING = ("included", "paid")
LOAD_STATES = ("idle", "busy")
SINGLE_FLIGHT_STATES = ("idle", "reserved")
ATTEMPT_STATES = ("active", "released", "accepted")

TASK_REQUIRED = ("schema", "task_id", "revision", "input_digest", "task_class", "scope", "author", "created_utc")
TASK_OPTIONAL = ("class_claims", "composer_evidence", "vetoes")
POLICY_KEYS = ("schema", "max_evidence_age_seconds", "budget_mode", "class_roles", "class_profiles")
WORKER_REQUIRED = ("schema", "worker", "kind", "profile_id")
WORKER_OPTIONAL = ("role", "qualification", "capacity", "load", "single_flight")
ATTEMPT_KEYS = ("schema", "attempt_id", "dispatch_key", "task_id", "worker", "scope", "state",
                "lease_expires_utc", "artifacts")
ARTIFACT_KEYS = ("commit", "branch", "remote_verified", "pushed_utc")
_HEX = frozenset("0123456789abcdef")


class _Stop(Exception):
    """A check decided; carries the verdict and its stable reasons."""

    def __init__(self, verdict: str, *reasons: str) -> None:
        super().__init__(verdict)
        self.verdict = verdict
        self.reasons = list(reasons)


def _require(condition: bool, verdict: str, *reasons: str) -> None:
    if not condition:
        raise _Stop(verdict, *reasons)


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _hex(value: Any, length: int) -> bool:
    return isinstance(value, str) and len(value) == length and set(value) <= _HEX


def _number(value: Any) -> float | None:
    if type(value) not in (int, float) or value != value or value in (float("inf"), float("-inf")):
        return None
    return float(value)


def _closed(record: Any, required: tuple, optional: tuple = ()) -> bool:
    return isinstance(record, dict) and set(required) <= set(record) <= set(required) | set(optional)


def _texts(value: Any) -> bool:
    return isinstance(value, list) and all(_text(item) for item in value) and len(set(value)) == len(value)


def _fresh(block: Any, now: datetime, max_age: int) -> bool:
    if not isinstance(block, dict):
        return False
    observed = _utc(block.get("observed_utc"))
    return observed is not None and now - timedelta(seconds=max_age) <= observed <= now


def normalize_scope(raw: Any) -> list[str]:
    """Sorted, case-folded, ``/``-separated scope entries. Raises _Stop(hold) on anything unsafe to compare."""
    _require(isinstance(raw, list) and bool(raw), HOLD, "scope_missing")
    entries = set()
    for item in raw:
        _require(_text(item) and not any(ch in item for ch in "*?[]"), HOLD, "scope_invalid")
        path = item.replace("\\", "/").casefold()
        body = path.split(":", 1)[-1].rstrip("/")
        _require(all(part not in ("", ".", "..") for part in body.split("/")), HOLD, "scope_invalid")
        entries.add(path)
    return sorted(entries)


def _as_dir(path: str) -> str:
    return path if path.endswith("/") else path + "/"


def scopes_overlap(left: list[str], right: list[str]) -> bool:
    """True when any entry equals, contains or is contained by an entry of the other scope."""
    return any(a == b or a.startswith(_as_dir(b)) or b.startswith(_as_dir(a)) for a in left for b in right)


def dispatch_key(task_id: str, revision: str, input_digest: str, scope: list[str]) -> str | None:
    """The immutable identity one dispatch is deduplicated on."""
    return digest({"task_id": task_id, "revision": revision, "input_digest": input_digest, "scope": scope})


def _task(task: Any, now: datetime) -> dict:
    _require(_closed(task, TASK_REQUIRED, TASK_OPTIONAL) and task["schema"] == TASK_SCHEMA, HOLD, "task_malformed")
    _require(_text(task["task_id"]) and _text(task["revision"]) and _hex(task["input_digest"], 64)
             and _text(task["author"]), HOLD, "task_malformed")
    created = _utc(task["created_utc"])
    _require(created is not None and created <= now, HOLD, "task_malformed", "created_utc")
    claims = task.get("class_claims", [])
    _require(isinstance(claims, list), HOLD, "task_malformed", "class_claims")
    classes = [task["task_class"], *claims]
    _require(all(isinstance(c, str) and c in TASK_CLASSES for c in classes), HOLD, "task_class_unknown")
    vetoes = task.get("vetoes", [])
    _require(isinstance(vetoes, list) and all(_text(v) for v in vetoes), HOLD, "task_malformed", "vetoes")
    # The router can only honour a veto; nothing it reads can lift one.
    _require(not vetoes, HOLD, "veto_in_force")
    scope = normalize_scope(task["scope"])
    return {"task_id": task["task_id"], "task_class": min(classes, key=CLASS_PRECEDENCE.index),
            "author": task["author"], "scope": scope,
            "dispatch_key": dispatch_key(task["task_id"], task["revision"], task["input_digest"], scope)}


def _policy(policy: Any) -> dict:
    _require(_closed(policy, POLICY_KEYS) and policy["schema"] == POLICY_SCHEMA, HOLD, "policy_invalid")
    age = policy["max_evidence_age_seconds"]
    _require(type(age) is int and age > 0 and policy["budget_mode"] in TRIP_LINES, HOLD, "policy_invalid")
    for name in ("class_roles", "class_profiles"):
        table = policy[name]
        _require(isinstance(table, dict) and set(table) == set(TASK_CLASSES)
                 and all(_texts(value) for value in table.values()), HOLD, "policy_invalid", name)
    return policy


def _artifact(raw: Any) -> dict:
    _require(_closed(raw, ARTIFACT_KEYS) and _hex(raw["commit"], 40) and _text(raw["branch"])
             and type(raw["remote_verified"]) is bool and _utc(raw["pushed_utc"]) is not None,
             HOLD, "attempt_malformed", "artifact")
    return dict(raw)


def _attempts(raw: Any, now: datetime) -> list[dict]:
    """Validated attempt records, deduplicated by attempt_id. A conflicting duplicate holds."""
    _require(isinstance(raw, list), HOLD, "attempts_malformed")
    by_id: dict = {}
    for record in raw:
        _require(_closed(record, ATTEMPT_KEYS) and record["schema"] == ATTEMPT_SCHEMA, HOLD, "attempt_malformed")
        _require(_text(record["attempt_id"]) and _hex(record["dispatch_key"], 64) and _text(record["task_id"])
                 and _text(record["worker"]) and record["state"] in ATTEMPT_STATES
                 and isinstance(record["artifacts"], list), HOLD, "attempt_malformed")
        lease = _utc(record["lease_expires_utc"])
        _require(lease is not None, HOLD, "attempt_malformed", "lease_expires_utc")
        artifacts = [_artifact(item) for item in record["artifacts"]]
        # An accepted attempt must name the pushed, remote-verified work it was accepted on.
        _require(record["state"] != "accepted" or any(a["remote_verified"] for a in artifacts),
                 HOLD, "attempt_malformed", "accepted_without_verified_artifact")
        entry = {**record, "scope": normalize_scope(record["scope"]), "artifacts": artifacts,
                 "live": record["state"] == "active" and now < lease}
        prior = by_id.get(record["attempt_id"])
        _require(prior is None or digest(prior["raw"]) == digest(record), HOLD, "attempt_conflict",
                 record["attempt_id"])
        by_id[record["attempt_id"]] = {**entry, "raw": record}
    return [by_id[key] for key in sorted(by_id)]


def _composer(task: dict, raw_task: dict, context: dict) -> str | None:
    """planning_synthesis: run the F24 composer on the task's evidence and map its verdict."""
    if task["task_class"] != "planning_synthesis":
        _require("composer_evidence" not in raw_task, HOLD, "composer_evidence_not_for_class")
        return None
    _require("composer_evidence" in raw_task, UNKNOWN, "composer_evidence_missing")
    selection = composer_select(raw_task["composer_evidence"])
    context["composer"] = {"verdict": selection.get("verdict"), "selected_profile": selection.get("selected_profile"),
                           "reasons": selection.get("reasons"), "inputs_digest": selection.get("inputs_digest")}
    _require(selection.get("schema") == COMPOSER_SCHEMA and selection.get("authority") == "none"
             and selection.get("execution_allowed") is False, HOLD, "composer_result_malformed")
    verdict = selection["verdict"]
    if verdict == COMPOSER_HOLD:
        raise _Stop(HOLD, "composer_hold", *selection["reasons"])
    _require(selection.get("task_id") == task["task_id"], HOLD, "composer_task_mismatch")
    if verdict == COMPOSER_WAIT:
        raise _Stop(WAIT, "composer_wait")
    if verdict == COMPOSER_UNKNOWN:
        raise _Stop(UNKNOWN, "composer_unknown")
    _require(verdict in (COMPOSER_PICK, COMPOSER_FALLBACK) and _text(selection.get("selected_profile")),
             HOLD, "composer_result_malformed")
    return selection["selected_profile"]


def _assess(worker: dict, task: dict, policy: dict, now: datetime, composer_profile: str | None,
            grok_leased: bool) -> tuple[str, list[str], float]:
    """("eligible" | "unavailable" | "unknown" | "ineligible", reasons, projected headroom use)."""
    name, profile, task_class = worker["worker"], worker["profile_id"], task["task_class"]
    max_age = policy["max_evidence_age_seconds"]
    ineligible, unknown, unavailable = [], [], []
    if name not in MEMBERS and name != GROK:
        ineligible.append("not_a_bridge_member")
    if (worker["kind"] == GROK) != (name == GROK):
        ineligible.append("kind_mismatch")
    if task_class in INDEPENDENT_CLASSES and name == task["author"]:
        ineligible.append("author_cannot_review_own_work")
    if profile not in policy["class_profiles"][task_class]:
        ineligible.append("profile_not_signed_for_class")
    if composer_profile is not None and profile != composer_profile:
        ineligible.append("not_the_composer_profile")

    role = worker.get("role")
    if not (_fresh(role, now, max_age) and role.get("verified") is True and role.get("worker") == name
            and _texts(role.get("roles"))):
        unknown.append("role_unknown_or_stale")
    elif not set(role["roles"]) & set(policy["class_roles"][task_class]):
        ineligible.append("role_not_permitted")

    receipts = worker.get("qualification")
    matching = [r for r in receipts if isinstance(r, dict) and r.get("task_class") == task_class
                and r.get("profile_id") == profile] if isinstance(receipts, list) else None
    if not matching:
        unknown.append("qualification_missing")
    elif len(matching) > 1:
        unknown.append("qualification_ambiguous")
    else:
        receipt = matching[0]
        observed, until = _utc(receipt.get("observed_utc")), _utc(receipt.get("valid_until_utc"))
        if not (observed is not None and until is not None and observed <= now < until
                and _hex(receipt.get("receipt_sha256"), 64) and type(receipt.get("qualified")) is bool):
            unknown.append("qualification_unknown_or_expired")
        elif receipt["qualified"] is not True:
            ineligible.append("not_qualified")

    capacity = worker.get("capacity")
    until = _utc(capacity.get("valid_until_utc")) if isinstance(capacity, dict) else None
    projected = _number(capacity.get("projected_used_percent")) if isinstance(capacity, dict) else None
    if not (_fresh(capacity, now, max_age) and until is not None and now < until
            and capacity.get("state") in CAPACITY_STATES and capacity.get("billing") in BILLING
            and projected is not None and projected >= 0):
        unknown.append("capacity_unknown_or_stale")
    elif capacity.get("profile_id") != profile:
        unknown.append("capacity_unbound")
    elif capacity["billing"] == "paid":
        ineligible.append("paid_capacity_not_requestable")
    elif capacity["state"] != "available":
        unavailable.append("pool_" + capacity["state"])
    elif projected > TRIP_LINES[policy["budget_mode"]]:
        unavailable.append("budget_over_trip_line")

    load = worker.get("load")
    if not (_fresh(load, now, max_age) and load.get("state") in LOAD_STATES):
        unknown.append("load_unknown_or_stale")
    elif load["state"] == "busy":
        unavailable.append("busy")

    if worker["kind"] == GROK:
        flight = worker.get("single_flight")
        if not (_fresh(flight, now, max_age) and flight.get("state") in SINGLE_FLIGHT_STATES):
            unknown.append("grok_single_flight_unknown")
        elif flight["state"] == "reserved" or grok_leased:
            unavailable.append("grok_single_flight_busy")

    headroom = projected if projected is not None else 0.0
    if ineligible:
        return "ineligible", ineligible + unknown + unavailable, headroom
    if unknown:
        return "unknown", unknown + unavailable, headroom
    if unavailable:
        return "unavailable", unavailable, headroom
    return "eligible", [], headroom


def _workers(raw: Any) -> list[dict]:
    _require(isinstance(raw, list), HOLD, "workers_malformed")
    names = []
    for worker in raw:
        _require(_closed(worker, WORKER_REQUIRED, WORKER_OPTIONAL) and worker["schema"] == WORKER_SCHEMA
                 and _text(worker["worker"]) and worker["kind"] in ("lane", GROK) and _text(worker["profile_id"]),
                 HOLD, "worker_malformed")
        names.append(worker["worker"])
    _require(len(set(names)) == len(names), HOLD, "duplicate_worker")
    return sorted(raw, key=lambda w: w["worker"])


def _shadow(weights: Any, task_class: str, ranked: list[dict]) -> dict:
    """A separate what-if order under F26 shadow weights. It never feeds back into the advice."""
    if weights is None:
        return {"state": "absent", "affects_advice": False}
    rows = weights.get("weights") if isinstance(weights, dict) else None
    if not (isinstance(weights, dict) and weights.get("schema") == SHADOW_WEIGHTS_SCHEMA
            and weights.get("mode") == "shadow" and weights.get("state") == "derived"
            and weights.get("authority") == "none" and _hex(weights.get("evidence_digest"), 64)
            and isinstance(rows, list) and all(isinstance(r, dict) for r in rows)):
        return {"state": "ignored", "reason": "shadow_weights_invalid", "affects_advice": False}
    table = {(r.get("task_class"), r.get("profile_id")): r for r in rows}

    def order(item: tuple[int, dict]) -> tuple:
        position, entry = item
        row = table.get((task_class, entry["profile_id"]))
        state = row.get("state") if row else None
        weight = _number(row.get("weight")) if row else None
        if state == "known" and weight is not None:
            return (0, -weight, position)
        return (2 if state == "quarantined" else 1, 0.0, position)

    return {"state": "derived", "weights_digest": weights["evidence_digest"], "affects_advice": False,
            "ranking": [entry["worker"] for _, entry in sorted(enumerate(ranked), key=order)]}


def _advice(verdict: str, reasons: list[str], context: dict) -> dict:
    ranking = context.get("ranking", [])
    return {
        "schema": SCHEMA,
        "feature": FEATURE,
        "verdict": verdict,
        "reasons": reasons,
        "task_id": context.get("task_id"),
        "task_class": context.get("task_class"),
        "dispatch_key": context.get("dispatch_key"),
        "recommended": ranking[0] if verdict == ROUTE and ranking else None,
        "ranking": ranking if verdict == ROUTE else [],
        "ineligible": context.get("ineligible", {}),
        "unknown": context.get("unknown", {}),
        "unavailable": context.get("unavailable", {}),
        "in_flight": context.get("in_flight"),
        "satisfied_by": context.get("satisfied_by"),
        "scope_conflicts": context.get("scope_conflicts", []),
        "expired_attempts": context.get("expired_attempts", []),
        "preserved_artifacts": context.get("preserved_artifacts", []),
        "resume_from": context.get("resume_from", []) if verdict == ROUTE else [],
        "composer": context.get("composer"),
        "shadow": context.get("shadow", {"state": "absent", "affects_advice": False}),
        "policy_sha256": context.get("policy_sha256"),
        "evidence_digest": context.get("evidence_digest"),
        "mode": "advice_only",
        "execution_allowed": False,
        "authority": "none",
        "dispatch_authority": DISPATCH_AUTHORITY,
    }


def _decide(task: Any, observed_workers: Any, prepared_artifacts: Any, policy: Any, now: Any,
            shadow_weights: Any, context: dict) -> dict:
    _require(context["evidence_digest"] is not None, HOLD, "evidence_not_canonical_json")
    moment = _utc(now) if isinstance(now, str) else None
    _require(moment is not None, HOLD, "now_invalid")
    context["policy_sha256"] = digest(policy)
    checked_policy = _policy(policy)
    checked = _task(task, moment)
    context.update(task_id=checked["task_id"], task_class=checked["task_class"],
                   dispatch_key=checked["dispatch_key"])
    attempts = _attempts(prepared_artifacts, moment)
    workers = _workers(observed_workers)

    own = [a for a in attempts if a["dispatch_key"] == checked["dispatch_key"]]
    context["preserved_artifacts"] = [
        {"attempt_id": a["attempt_id"], **artifact} for a in own for artifact in a["artifacts"]]
    context["expired_attempts"] = [a["attempt_id"] for a in own if a["state"] == "active" and not a["live"]]
    context["resume_from"] = [
        {"attempt_id": a["attempt_id"], **artifact} for a in own if not a["live"] and a["state"] != "accepted"
        for artifact in a["artifacts"] if artifact["remote_verified"]]
    accepted = [a["attempt_id"] for a in own if a["state"] == "accepted"]
    if accepted:
        context["satisfied_by"] = accepted[0]
        raise _Stop(SATISFIED, "accepted_attempt_exists")
    live = [a["attempt_id"] for a in own if a["live"]]
    if live:
        context["in_flight"] = live[0]
        raise _Stop(DUPLICATE, "live_lease_on_dispatch_key")
    conflicts = [a["attempt_id"] for a in attempts if a["live"] and a["dispatch_key"] != checked["dispatch_key"]
                 and scopes_overlap(a["scope"], checked["scope"])]
    if conflicts:
        context["scope_conflicts"] = conflicts
        raise _Stop(WAIT, *("scope_conflict:" + attempt for attempt in conflicts))

    composer_profile = _composer(checked, task, context)
    grok_leased = any(a["live"] and a["worker"] == GROK for a in attempts)
    assessed = {w["worker"]: (w, *_assess(w, checked, checked_policy, moment, composer_profile, grok_leased))
                for w in workers}
    for state in ("ineligible", "unknown", "unavailable"):
        context[state] = {name: entry[2] for name, entry in sorted(assessed.items(), key=lambda kv: kv[0])
                          if entry[1] == state}
    order = checked_policy["class_profiles"][checked["task_class"]]
    eligible = sorted((entry for entry in assessed.values() if entry[1] == "eligible"),
                      key=lambda entry: (order.index(entry[0]["profile_id"]), entry[3], entry[0]["worker"]))
    context["ranking"] = [{"worker": w["worker"], "profile_id": w["profile_id"],
                           "route": "grok_consult" if w["kind"] == GROK else "direct"} for w, *_ in eligible]
    context["shadow"] = _shadow(shadow_weights, checked["task_class"], context["ranking"])
    if eligible:
        return _advice(ROUTE, ["ranked_eligible_worker"], context)
    # Grok is optional: its state never makes the router wait or report unknown.
    others = {s for name, (_, s, _, _) in assessed.items() if name != GROK}
    if "unavailable" in others:
        raise _Stop(WAIT, "permitted_workers_unavailable")
    if "unknown" in others:
        raise _Stop(UNKNOWN, "worker_evidence_missing_or_stale")
    if checked["task_class"] == "advisory":
        raise _Stop(SKIPPED, "no_eligible_advisory_worker")
    raise _Stop(HOLD, "no_permitted_worker")


def decide(task: Any, observed_workers: Any, prepared_artifacts: Any, policy: Any, now: Any,
           shadow_weights: Any = None) -> dict:
    """Routing advice for one task. Never raises: anything unexpected is a hold."""
    context: dict = {"evidence_digest": digest({
        "task": task, "observed_workers": observed_workers, "prepared_artifacts": prepared_artifacts,
        "policy": policy, "now": now, "shadow_weights": shadow_weights})}
    try:
        return _decide(task, observed_workers, prepared_artifacts, policy, now, shadow_weights, context)
    except _Stop as stop:
        return _advice(stop.verdict, stop.reasons, context)
    except Exception:  # noqa: BLE001 - advice never raises; anything unexpected holds
        return _advice(HOLD, ["input_malformed"], context)


class TaskRouter:
    """Stateless facade over ``decide``: it holds no queue, lease, clock or port."""

    def decide(self, task: Any, observed_workers: Any, prepared_artifacts: Any, policy: Any, now: Any,
               shadow_weights: Any = None) -> dict:
        return decide(task, observed_workers, prepared_artifacts, policy, now, shadow_weights)
