# SPDX-License-Identifier: BUSL-1.1
"""W3 (F19): worker load evidence and a worker-owned claim intent, both pure (RCO1 2026-09-30, Lead 58b182a1).

Nothing here reads a clock, a file, a collector, the queue or a claim, and nothing claims or dispatches. The
caller reads ONE queue-claims snapshot at one instant and passes it with the router's own ``now`` text and the
signed routing policy itself; the age bound is that policy's ``max_evidence_age_seconds`` (validated by the
router's own rules), never a free caller number. Every output is advice: authority none, execution not allowed.

* ``load_blocks`` gives the router's ``load`` evidence for member lanes: ``busy`` when any active or pending
  claim in the snapshot names the lane exactly, else ``idle``. Missing, foreign, stale, future, incomplete or
  unreadable evidence gives NO block, which the router already reads as load_unknown_or_stale: nothing is ever
  made idle. Grok gets no block: its single_flight is not queue state and is never invented here.
* ``claim_intent`` says what the recommended worker may ask its OWN v2 claim to do. It recomputes the dispatch
  key from the immutable task with the router's own rules, binds the exact advice record (its reasons, evidence
  digest and the digest of THIS policy), and needs a fresh idle block for exactly that worker. It is not a
  reservation and grants nothing: the queue's keyed claim is the fence, a later caller.

Trust boundary (what these checks do NOT prove): ``policy_sha256`` is verified only against the policy object
the caller hands in, and nothing here verifies that policy's signature (no signature loader exists yet; none is
fabricated), so the caller remains answerable for passing the signed policy. ``evidence_digest`` is only
format-checked (64 lowercase hex) and carried into the intent for audit: a well-formed digest is not proof that
the router saw that evidence. The snapshot's truth rests on its producer. The intent grants nothing either way.

Every compared value is exact-typed first, so a hostile object is refused, never raised through. Caller-contract
violations (``now``, the policy for ``load_blocks``, the worker list) raise ``RoutingLoadError``; evidence
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
ROUTE_REASONS = ["ranked_eligible_worker"]
MAX_ENTRIES = 4096
MAX_TIME_TEXT = 64
MAX_FIELD_TEXT = 512
_HEX = frozenset("0123456789abcdef")


class RoutingLoadError(ValueError):
    """A caller contract was violated; nothing was derived."""


def _moment(now: Any) -> datetime:
    moment = _utc(now) if type(now) is str and len(now) <= MAX_TIME_TEXT else None
    if moment is None:
        raise RoutingLoadError("now must be aware ISO-8601 text, as the router takes it")
    return moment


def _age(policy: Any) -> int | None:
    """The signed policy's evidence age under the router's own policy rules, or None when it is not valid."""
    try:
        return router._policy(policy)["max_evidence_age_seconds"]
    except router._Stop:
        return None


def _text(value: Any, limit: int = MAX_FIELD_TEXT) -> bool:
    return type(value) is str and 0 < len(value) <= limit


def _hex64(value: Any) -> bool:
    return type(value) is str and len(value) == 64 and set(value) <= _HEX


def _fresh(observed: Any, moment: datetime, max_age: int) -> bool:
    """The router's own freshness rule (now - max_age <= observed <= now) on exact text, without overflow."""
    at = _utc(observed) if type(observed) is str and len(observed) <= MAX_TIME_TEXT else None
    if at is None or at > moment:
        return False
    try:
        return moment - at <= timedelta(seconds=max_age)
    except OverflowError:                                    # an age beyond timedelta's range bounds nothing
        return True


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
    if (type(snapshot) is not dict or snapshot.keys() != SNAPSHOT_KEYS or type(snapshot["schema"]) is not str
            or snapshot["schema"] != SNAPSHOT_SCHEMA):
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
            if type(entry) is not dict or entry.keys() != ENTRY_KEYS or not _text(entry["source"], 16):
                return None                                  # an unreadable, foreign or case-variant fact
            agent, task_id, session = entry["agent"], entry["task_id"], entry["owner_session_id"]
            if entry["source"] != source:
                return None
            # A holder that is not an exact member (case variant, padded, str subclass, stranger) is a foreign
            # fact: the whole snapshot is unknown rather than letting any member read idle beside it.
            if type(agent) is not str or agent not in router.MEMBERS:
                return None
            if not _text(task_id) or (session is not None and not _text(session)):
                return None
            counts[agent] = counts.get(agent, 0) + 1
    return counts


def load_blocks(workers: Any, snapshot: Any, now: Any, policy: Any) -> dict[str, dict]:
    """{lane: load block}; {} when the snapshot cannot prove any lane's load (every lane is then unknown)."""
    moment = _moment(now)
    max_age = _age(policy)
    if max_age is None:
        raise RoutingLoadError("policy must be a valid routing policy (the router's own rules)")
    lanes = _lanes(workers)
    counts = _holders(snapshot, moment, max_age)
    if counts is None:
        return {}
    return {lane: {"schema": LOAD_SCHEMA, "worker": lane, "observed_utc": snapshot["observed_utc"],
                   "state": "busy" if counts.get(lane, 0) else "idle", "claims": counts.get(lane, 0)}
            for lane in lanes}


def _refused(*reasons: str) -> dict:
    return {"verdict": "refused", "reasons": list(reasons), "intent": None}


def _recommendation(record: Any) -> bool:
    return type(record) is dict and record.keys() == RECOMMENDED_KEYS and all(
        _text(record[key], 128) for key in RECOMMENDED_KEYS)


def claim_intent(task: Any, advice: Any, worker: Any, load: Any, now: Any, policy: Any) -> dict:
    """{"verdict": "intent", ...} for the recommended worker's own keyed claim, or {"verdict": "refused", ...}."""
    moment = _moment(now)
    try:
        checked = router._task(task, moment)                # the router's own task rules and dispatch key
    except router._Stop as stop:
        return _refused("task_" + stop.verdict, *stop.reasons)
    max_age = _age(policy)
    if max_age is None:
        return _refused("policy_invalid")
    if type(advice) is not dict or advice.keys() != ADVICE_KEYS:
        return _refused("advice_malformed")
    bound = {"schema": router.SCHEMA, "feature": router.FEATURE, "verdict": router.ROUTE, "mode": "advice_only",
             "authority": "none", "dispatch_authority": router.DISPATCH_AUTHORITY, "task_id": checked["task_id"],
             "task_class": checked["task_class"], "dispatch_key": checked["dispatch_key"]}
    for key, value in bound.items():
        if type(advice[key]) is not str or advice[key] != value:
            return _refused("advice_not_bound:" + key)
    if advice["execution_allowed"] is not False:
        return _refused("advice_not_bound:execution_allowed")
    reasons = advice["reasons"]
    if type(reasons) is not list or len(reasons) != 1 or type(reasons[0]) is not str or reasons != ROUTE_REASONS:
        return _refused("advice_not_bound:reasons")
    if not _hex64(advice["evidence_digest"]):
        return _refused("advice_not_bound:evidence_digest")
    if not _hex64(advice["policy_sha256"]) or advice["policy_sha256"] != router.digest(policy):
        return _refused("advice_not_bound:policy_sha256")    # the advice was decided under THIS policy
    ranking, recommended = advice["ranking"], advice["recommended"]
    if (not _recommendation(recommended) or type(ranking) is not list or not ranking
            or not _recommendation(ranking[0]) or ranking[0] != recommended):
        return _refused("advice_recommendation_malformed")
    if recommended["route"] != "direct" or recommended["worker"] not in router.MEMBERS:
        return _refused("recommended_worker_is_not_a_claiming_lane")
    if type(worker) is not str or worker != recommended["worker"]:
        return _refused("not_the_recommended_worker")
    for state in ("ineligible", "unknown", "unavailable"):
        listed = advice[state]
        if type(listed) is not dict or worker in listed:
            return _refused("recommended_worker_listed:" + state)
    if (type(load) is not dict or load.keys() != LOAD_KEYS or type(load["schema"]) is not str
            or load["schema"] != LOAD_SCHEMA or not _fresh(load["observed_utc"], moment, max_age)):
        return _refused("load_unknown_or_stale")
    if type(load["worker"]) is not str or load["worker"] != worker:
        return _refused("load_not_for_this_worker")        # another lane's idle block proves nothing here
    if type(load["state"]) is not str or load["state"] != "idle" or type(load["claims"]) is not int or load["claims"]:
        return _refused("worker_not_idle")
    return {"verdict": "intent", "reasons": [], "intent": {
        "schema": INTENT_SCHEMA, "authority": "none", "execution_allowed": False, "owner": worker,
        "task_id": checked["task_id"], "revision": task["revision"], "dispatch_key": checked["dispatch_key"],
        "mode": "write", "write_scope": list(checked["scope"]), "load_observed_utc": load["observed_utc"],
        "evidence_digest": advice["evidence_digest"], "policy_sha256": advice["policy_sha256"]}}
