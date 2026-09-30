# SPDX-License-Identifier: BUSL-1.1
"""W3 (F19): worker load evidence and a worker-owned claim intent, both pure (RCO1 2026-09-30, Lead 58b182a1).

Nothing here reads a clock, a file, a collector, the queue or a claim, and nothing claims or dispatches. The
caller reads ONE queue-claims snapshot at one instant and passes it with the router's own ``now`` text and the
signed policy's ``max_evidence_age_seconds``. Every output is advice: authority none, execution not allowed.

* ``load_blocks`` gives the router's ``load`` evidence for member lanes: ``busy`` when any active or pending
  claim in the snapshot names the lane exactly, else ``idle``. Missing, foreign, stale, future, incomplete or
  unreadable evidence gives NO block, which the router already reads as load_unknown_or_stale: nothing is ever
  made idle. Grok gets no block: its single_flight is not queue state and is never invented here.
* ``claim_intent`` says what the recommended worker may ask its OWN v2 claim to do. It recomputes the dispatch
  key from the immutable task with the router's own rules, binds the exact advice record, and needs a fresh
  idle block. It is not a reservation and grants nothing: the queue's keyed claim is the fence, a later caller.

Caller-contract violations (``now``, the age bound, the worker list) raise ``RoutingLoadError``; evidence
problems are unknown (no block) or a refused intent with stable reasons.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from tools import wd_task_router as router
from tools.lane_profile_record import _utc          # the router's own aware-UTC normalizer

SNAPSHOT_SCHEMA = "wd.queue-claims-snapshot.v1"
LOAD_SCHEMA = "wd.routing-load.v1"
INTENT_SCHEMA = "wd.dispatch-claim-intent.v1"
SNAPSHOT_KEYS = frozenset(("schema", "observed_utc", "complete", "unreadable", "claims", "pending"))
ENTRY_KEYS = frozenset(("source", "agent", "task_id", "owner_session_id"))
LOAD_KEYS = frozenset(("schema", "worker", "observed_utc", "state", "claims"))
RECOMMENDED_KEYS = frozenset(("worker", "profile_id", "route"))
# The advice is a closed record: exactly the keys the router's own output builder writes.
ADVICE_KEYS = frozenset(router._advice(router.ROUTE, [], {}))
MAX_EVIDENCE_AGE_SECONDS = 86400
MAX_ENTRIES = 4096
MAX_TIME_TEXT = 64
MAX_FIELD_TEXT = 512


class RoutingLoadError(ValueError):
    """A caller contract was violated; nothing was derived."""


def _moment(now: Any, max_age: Any) -> datetime:
    moment = _utc(now) if type(now) is str and len(now) <= MAX_TIME_TEXT else None
    if moment is None:
        raise RoutingLoadError("now must be aware ISO-8601 text, as the router takes it")
    if type(max_age) is not int or not 0 < max_age <= MAX_EVIDENCE_AGE_SECONDS:
        raise RoutingLoadError("max_evidence_age_seconds must be an integer in 1..86400")
    return moment


def _fresh(observed: Any, moment: datetime, max_age: int) -> bool:
    """The router's own freshness rule, on exact text."""
    at = _utc(observed) if type(observed) is str and len(observed) <= MAX_TIME_TEXT else None
    return at is not None and moment - timedelta(seconds=max_age) <= at <= moment


def _lanes(workers: Any) -> list[str]:
    if type(workers) is not list or not workers or len(workers) > len(router.MEMBERS) + 1:
        raise RoutingLoadError("workers must be a non-empty list of member ids")
    if any(type(w) is not str or (w not in router.MEMBERS and w != router.GROK) for w in workers):
        raise RoutingLoadError("workers must be exact member ids (or grok)")
    if len(set(workers)) != len(workers):
        raise RoutingLoadError("workers must not repeat")
    return [w for w in workers if w != router.GROK]


def _holders(snapshot: Any, moment: datetime, max_age: int) -> dict[str, int] | None:
    """Claims per agent, or None when the snapshot is not exact, complete and fresh."""
    if type(snapshot) is not dict or snapshot.keys() != SNAPSHOT_KEYS or snapshot["schema"] != SNAPSHOT_SCHEMA:
        return None
    if not _fresh(snapshot["observed_utc"], moment, max_age):
        return None
    if snapshot["complete"] is not True or type(snapshot["unreadable"]) is not int or snapshot["unreadable"] != 0:
        return None
    claims, pending = snapshot["claims"], snapshot["pending"]
    if type(claims) is not list or type(pending) is not list or len(claims) + len(pending) > MAX_ENTRIES:
        return None
    counts: dict[str, int] = {}
    for source, entries in (("claim", claims), ("pending", pending)):
        for entry in entries:
            if type(entry) is not dict or entry.keys() != ENTRY_KEYS or entry["source"] != source:
                return None                                 # an unreadable, foreign or case-variant fact
            agent, task_id, session = entry["agent"], entry["task_id"], entry["owner_session_id"]
            # A holder that is not an exact member (case variant, padded, str subclass, stranger) is a foreign
            # fact: the whole snapshot is unknown rather than letting any member read idle beside it.
            if type(agent) is not str or agent not in router.MEMBERS:
                return None
            if type(task_id) is not str or not 0 < len(task_id) <= MAX_FIELD_TEXT:
                return None
            if session is not None and (type(session) is not str or not 0 < len(session) <= MAX_FIELD_TEXT):
                return None
            counts[agent] = counts.get(agent, 0) + 1
    return counts


def load_blocks(workers: Any, snapshot: Any, now: Any, max_evidence_age_seconds: Any) -> dict[str, dict]:
    """{lane: load block}; {} when the snapshot cannot prove any lane's load (every lane is then unknown)."""
    moment = _moment(now, max_evidence_age_seconds)
    lanes = _lanes(workers)
    counts = _holders(snapshot, moment, max_evidence_age_seconds)
    if counts is None:
        return {}
    return {lane: {"schema": LOAD_SCHEMA, "worker": lane, "observed_utc": snapshot["observed_utc"],
                   "state": "busy" if counts.get(lane, 0) else "idle", "claims": counts.get(lane, 0)}
            for lane in lanes}


def _refused(*reasons: str) -> dict:
    return {"verdict": "refused", "reasons": list(reasons), "intent": None}


def claim_intent(task: Any, advice: Any, worker: Any, load: Any, now: Any, max_evidence_age_seconds: Any) -> dict:
    """{"verdict": "intent", ...} for the recommended worker's own keyed claim, or {"verdict": "refused", ...}."""
    moment = _moment(now, max_evidence_age_seconds)
    try:
        checked = router._task(task, moment)                # the router's own task rules and dispatch key
    except router._Stop as stop:
        return _refused("task_" + stop.verdict, *stop.reasons)
    if type(advice) is not dict or advice.keys() != ADVICE_KEYS:
        return _refused("advice_malformed")
    bound = {"schema": router.SCHEMA, "feature": router.FEATURE, "verdict": router.ROUTE, "mode": "advice_only",
             "authority": "none", "dispatch_authority": router.DISPATCH_AUTHORITY, "task_id": checked["task_id"],
             "task_class": checked["task_class"], "dispatch_key": checked["dispatch_key"]}
    for key, value in bound.items():
        if type(advice.get(key)) is not str or advice[key] != value:
            return _refused("advice_not_bound:" + key)
    if advice.get("execution_allowed") is not False:
        return _refused("advice_not_bound:execution_allowed")
    ranking, recommended = advice.get("ranking"), advice.get("recommended")
    if (type(ranking) is not list or not ranking or type(recommended) is not dict
            or recommended.keys() != RECOMMENDED_KEYS or ranking[0] != recommended):
        return _refused("advice_recommendation_malformed")
    if recommended["route"] != "direct" or recommended["worker"] not in router.MEMBERS:
        return _refused("recommended_worker_is_not_a_claiming_lane")
    if type(worker) is not str or worker != recommended["worker"]:
        return _refused("not_the_recommended_worker")
    for state in ("ineligible", "unknown", "unavailable"):
        listed = advice.get(state)
        if type(listed) is not dict or worker in listed:
            return _refused("recommended_worker_listed:" + state)
    if (type(load) is not dict or load.keys() != LOAD_KEYS or load["schema"] != LOAD_SCHEMA
            or not _fresh(load["observed_utc"], moment, max_evidence_age_seconds)):
        return _refused("load_unknown_or_stale")
    if type(load["worker"]) is not str or load["worker"] != worker:
        return _refused("load_not_for_this_worker")        # another lane's idle block proves nothing here
    if load["state"] != "idle" or type(load["claims"]) is not int or load["claims"] != 0:
        return _refused("worker_not_idle")
    return {"verdict": "intent", "reasons": [], "intent": {
        "schema": INTENT_SCHEMA, "authority": "none", "execution_allowed": False, "owner": worker,
        "task_id": checked["task_id"], "revision": task["revision"], "dispatch_key": checked["dispatch_key"],
        "mode": "write", "write_scope": list(checked["scope"]), "load_observed_utc": load["observed_utc"]}}
