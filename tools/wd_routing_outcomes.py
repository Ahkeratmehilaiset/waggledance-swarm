#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Bridge v2 F26 S1: routing outcome records from verified evidence (shadow only).

``outcomes(advice, attempts, evaluator_events, now)`` turns router advice, accepted
attempts and recognized RCO evaluation events into ``wd.routing-outcome.v1`` records,
the input of ``tools/wd_routing_weights.derive_shadow_weights``. It returns one
``wd.routing-outcome-batch.v1`` record. It reads no file, clock, environment,
network or bridge, and it writes nothing. The record always says ``mode`` "shadow",
``authority`` "none", ``activation`` "none" and ``execution_allowed`` False.

Inputs (all injected; nothing here produces them):
* ``advice``: a list of ``wd.task-routing-advice.v1`` records with verdict ``route``
  (tools/wd_task_router). The advice binds a dispatch key to its task class and
  gives each ranked worker's profile.
* ``attempts``: a list of ``wd.routing-attempt.v1`` records, checked with the
  router's own closed schema. Only ``accepted`` attempts are evaluated, and the
  accepted head is the attempt's single remote-verified commit. No production code
  writes attempt records today.
* ``evaluator_events``: ``{"schema": "wd.routing-evaluator-events.v1",
  "identity_verified": true, "events": [...]}``. The caller must have verified each
  event's author identity and signature (this module cannot), and says so
  explicitly; anything else refuses the whole batch. No caller or verifier that
  builds this envelope exists today.
* ``now``: an ISO-8601 time with an offset. It is the only clock.

What counts (everything else is listed in ``rejected`` with a stable reason):
* An event counts only from a recognized RCO identity (exact spelling), with the
  attempt's task id or its accepted branch as ``task_id``, a structured head equal to
  the accepted commit, and a time between the push and ``now``. The structured head is
  ``payload.exact_head`` (``payload.head`` must agree when present) or, only when the
  ``exact_head`` key is absent, the live writer's ``payload.head`` that the message
  also contains. Prose alone never binds a head, so a free-text-only finding is
  ``unbound``; the merge gate's own veto reading is separate and unchanged.
* A pass is ``type`` decision or rco_review with ``status`` rco_pass, from an
  evaluator other than the worker (identities folded as in wd_routing_weights). A
  worker's own pass is ``self_evaluation`` and never counts. Type and status must be exact str,
  or the event is ``malformed``. A pass that binds more than one accepted attempt (two dispatch
  keys of one task at one head) credits none of them: ``event_target_ambiguous``.
* A finding is ``type`` finding at the exact head, any status: it can only restrict,
  so the worker's own finding counts too, and it restricts every accepted attempt it binds. A
  finding outranks every pass: the outcome
  is a ``failure`` with stop signal ``quality_regression``. A finding that binds no
  accepted head is ``unbound`` and does not count.
* An accepted attempt with no counted event is listed as ``no_independent_evaluation``
  and yields no outcome.

Never produced: ``limit_hit`` (a capacity limit says nothing about quality),
``failure`` without a finding, and ``requalification`` (no signed source exists).

Idempotence: ``outcome_id`` is the digest of the record's other fields, so the same
evidence always gives the same id and the weights count it once. More evidence for the
same task gives a new id with the same dispatch key, which the weights count once as a
``duplicate_task_outcome``, or as a conflict if the result changed. A repeated
identical input is counted in ``duplicates_ignored``.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from tools.lane_profile_record import _utc
from tools.wd_composer_select import digest
from tools.wd_routing_weights import OUTCOME_SCHEMA, _identity
from tools.wd_task_router import ATTEMPT_SCHEMA, TASK_CLASSES, _attempts, _Stop
from tools.wd_task_router import SCHEMA as ADVICE_SCHEMA

SCHEMA = "wd.routing-outcome-batch.v1"
EVENTS_SCHEMA = "wd.routing-evaluator-events.v1"
FEATURE = "F26"
# The recognized RCO set of CLAUDE.md rule 9a (tools/check_rco_pass_present.DEFAULT_RCO_AGENTS),
# repeated here so this module does not import the gate; a test pins them together.
EVALUATORS = ("claude-rco-1", "claude-rco-2")
PASS_TYPES = ("decision", "rco_review")
PASS_STATUS = "rco_pass"
FINDING_TYPE = "finding"
STOP_SIGNAL = "quality_regression"
_HEX = frozenset("0123456789abcdef")
_STAMP = "%Y-%m-%dT%H:%M:%S.%fZ"


def _text(value: Any) -> bool:
    # Exact str: a subclass can redefine equality and pass a membership test it should fail.
    return type(value) is str and bool(value)


def _hex(value: Any, length: int) -> bool:
    return type(value) is str and len(value) == length and set(value) <= _HEX


def _reject(rejected: list, source: str, ref: Any, reason: str) -> None:
    rejected.append({"source": source, "ref": ref if _text(ref) else None, "reason": reason})


def _unique(items: Any, key, source: str, rejected: list) -> tuple[dict, int]:
    """{key: record} for records whose key is unique or repeated byte-identically; (map, duplicates)."""
    groups: dict = {}
    for item in items:
        name = key(item)
        if not _text(name) or digest(item) is None:
            _reject(rejected, source, name, "malformed")
            continue
        groups.setdefault(name, {})[digest(item)] = item
    duplicates = sum(1 for item in items if _text(key(item)) and digest(item) is not None) - sum(
        len(copies) for copies in groups.values())
    unique = {}
    for name in sorted(groups):
        if len(groups[name]) > 1:
            _reject(rejected, source, name, "conflicting_duplicate")
        else:
            unique[name] = next(iter(groups[name].values()))
    return unique, duplicates


def _advice_ok(record: dict) -> bool:
    ranking = record.get("ranking")
    return (record.get("schema") == ADVICE_SCHEMA and record.get("verdict") == "route"
            and record.get("mode") == "advice_only" and record.get("authority") == "none"
            and record.get("execution_allowed") is False and _hex(record.get("dispatch_key"), 64)
            and record.get("task_class") in TASK_CLASSES and _text(record.get("task_id"))
            and _hex(record.get("evidence_digest"), 64) and isinstance(ranking, list) and ranking
            and all(isinstance(r, dict) and _text(r.get("worker")) and _text(r.get("profile_id")) for r in ranking)
            and len({r["worker"] for r in ranking}) == len(ranking))


def _head_claim(event: dict) -> str | None:
    """The structured head, or None. Prose alone never binds.

    ``payload.exact_head`` is primary: when the key is present it must be an exact lowercase 40-hex str,
    and a present ``payload.head`` must be the same exact str. With no ``exact_head`` key, the live
    writer's shape binds (BIN Write-AgentEvent.ps1:439-445): ``payload.head`` is an exact lowercase
    40-hex str and the exact-str message contains it (ordinal). A null, malformed or conflicting
    ``exact_head`` never falls back to ``head``."""
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None
    head = payload.get("head")
    if "exact_head" in payload:
        exact = payload["exact_head"]
        if not _hex(exact, 40) or ("head" in payload and not (_hex(head, 40) and head == exact)):
            return None
        return exact
    message = event.get("message")
    if _hex(head, 40) and type(message) is str and head in message:
        return head
    return None


def _kind(event: dict) -> str | None:
    """"finding", "pass", None (not an evaluation) or "malformed". Type and status are compared only as
    exact str: a subclass can redefine equality and forge a pass (RCO2 F1)."""
    typ, status = event.get("type"), event.get("status")
    if type(typ) is not str:
        return "malformed"
    if typ == FINDING_TYPE:
        return "finding"
    if typ in PASS_TYPES:
        if type(status) is not str:
            return "malformed"
        return "pass" if status == PASS_STATUS else None
    return None


def _refused(reason: str, now: Any) -> dict:
    record = {"schema": SCHEMA, "feature": FEATURE, "mode": "shadow", "state": "refused", "reasons": [reason],
              "now_utc": now if isinstance(now, str) else None, "outcomes": [], "rejected": [],
              "duplicates_ignored": 0, "execution_allowed": False, "authority": "none", "activation": "none"}
    record["evidence_digest"] = digest({"state": "refused", "reason": reason, "now_utc": record["now_utc"]})
    return record


def _produce(advice: Any, attempts: Any, evaluator_events: Any, now: Any) -> dict:
    moment = _utc(now) if isinstance(now, str) else None
    if moment is None:
        return _refused("now_invalid", now)
    if not (isinstance(evaluator_events, dict) and set(evaluator_events) == {"schema", "identity_verified", "events"}
            and evaluator_events["schema"] == EVENTS_SCHEMA and evaluator_events["identity_verified"] is True
            and isinstance(evaluator_events["events"], list)):
        return _refused("evaluator_events_unverified", now)
    if not (isinstance(advice, list) and isinstance(attempts, list)):
        return _refused("inputs_malformed", now)

    rejected: list[dict] = []
    duplicates = 0

    advised, extra = _unique(advice, lambda r: r.get("dispatch_key") if isinstance(r, dict) else None,
                             "advice", rejected)
    duplicates += extra
    for key in list(advised):
        if not _advice_ok(advised[key]):
            _reject(rejected, "advice", key, "advice_malformed")
            del advised[key]

    by_id, extra = _unique(attempts, lambda r: r.get("attempt_id") if isinstance(r, dict) else None,
                           "attempt", rejected)
    duplicates += extra
    accepted: dict = {}
    for attempt_id, record in by_id.items():
        try:
            _attempts([record], moment)
        except _Stop:
            _reject(rejected, "attempt", attempt_id, "attempt_malformed")
            continue
        if record["schema"] != ATTEMPT_SCHEMA or record["state"] != "accepted":
            _reject(rejected, "attempt", attempt_id, "not_accepted")
            continue
        accepted.setdefault(record["dispatch_key"], []).append(record)

    bound: list[dict] = []
    for dispatch_key in sorted(accepted):
        group = accepted[dispatch_key]
        if len(group) > 1:
            for record in group:
                _reject(rejected, "attempt", record["attempt_id"], "dispatch_key_ambiguous")
            continue
        record = group[0]
        heads = {a["commit"] for a in record["artifacts"] if a["remote_verified"]}
        task_advice = advised.get(dispatch_key)
        if len(heads) != 1:
            _reject(rejected, "attempt", record["attempt_id"], "accepted_head_ambiguous")
        elif task_advice is None:
            _reject(rejected, "attempt", record["attempt_id"], "advice_missing")
        elif task_advice["task_id"] != record["task_id"]:
            _reject(rejected, "attempt", record["attempt_id"], "advice_task_mismatch")
        elif record["worker"] not in {r["worker"] for r in task_advice["ranking"]}:
            _reject(rejected, "attempt", record["attempt_id"], "worker_not_in_advice")
        else:
            head = next(iter(heads))
            artifact = min((a for a in record["artifacts"] if a["remote_verified"] and a["commit"] == head),
                           key=lambda a: (_utc(a["pushed_utc"]), a["branch"]))
            profile = next(r["profile_id"] for r in task_advice["ranking"] if r["worker"] == record["worker"])
            bound.append({"attempt": record, "advice": task_advice, "head": head, "profile_id": profile,
                          "task_ids": {record["task_id"], artifact["branch"]},
                          "pushed": _utc(artifact["pushed_utc"]), "passes": [], "findings": []})

    seen: dict = {}
    for event in evaluator_events["events"]:
        event_digest = digest(event) if isinstance(event, dict) else None
        if event_digest is None:
            _reject(rejected, "event", None, "malformed")
            continue
        if event_digest in seen:
            duplicates += 1
            continue
        seen[event_digest] = event
    for event_digest in sorted(seen):
        event = seen[event_digest]
        kind = _kind(event)
        observed = _utc(event.get("ts_utc"))
        if kind is None:
            _reject(rejected, "event", event_digest, "not_an_evaluation")
            continue
        if kind == "malformed" or observed is None or not _text(event.get("agent")) or not _text(event.get("task_id")):
            _reject(rejected, "event", event_digest, "malformed")
            continue
        if event["agent"] not in EVALUATORS:
            _reject(rejected, "event", event_digest, "unrecognized_evaluator")
            continue
        if observed > moment:
            _reject(rejected, "event", event_digest, "future_dated")
            continue
        head = _head_claim(event)
        targets = [b for b in bound if head == b["head"] and event["task_id"] in b["task_ids"]]
        if head is None or not targets:
            _reject(rejected, "event", event_digest, "unbound")
            continue
        if kind == "pass" and len(targets) > 1:
            # One pass never credits two accepted attempts (RCO2 F2): without evidence that names exactly
            # one target it credits none. A finding still restricts every target it binds.
            _reject(rejected, "event", event_digest, "event_target_ambiguous")
            continue
        for target in targets:
            if observed < target["pushed"]:
                _reject(rejected, "event", event_digest, "before_push")
            elif kind == "pass" and _identity(event["agent"]) == _identity(target["attempt"]["worker"]):
                _reject(rejected, "event", event_digest, "self_evaluation")
            else:
                target["passes" if kind == "pass" else "findings"].append((observed, event_digest, event["agent"]))

    produced = []
    for target in bound:
        attempt = target["attempt"]
        counted = target["findings"] or target["passes"]
        if not counted:
            _reject(rejected, "attempt", attempt["attempt_id"], "no_independent_evaluation")
            continue
        failure = bool(target["findings"])
        outcome = {
            "schema": OUTCOME_SCHEMA, "kind": "outcome", "dispatch_key": attempt["dispatch_key"],
            "task_class": target["advice"]["task_class"], "profile_id": target["profile_id"],
            "worker": attempt["worker"], "result": "failure" if failure else "success",
            "stop_signal": STOP_SIGNAL if failure else None,
            "evaluators": sorted({agent for _, _, agent in counted}), "verified": True,
            "evidence_sha256": digest({"advice": target["advice"]["evidence_digest"], "attempt": digest(attempt),
                                       "head": target["head"], "events": sorted(d for _, d, _ in counted)}),
            "observed_utc": max(observed for observed, _, _ in counted).strftime(_STAMP)}
        outcome["outcome_id"] = digest(outcome)
        produced.append(outcome)

    produced.sort(key=lambda o: o["outcome_id"])
    rejected.sort(key=lambda r: (r["source"], str(r["ref"]), r["reason"]))
    now_text = moment.strftime(_STAMP)
    record = {"schema": SCHEMA, "feature": FEATURE, "mode": "shadow", "state": "produced", "reasons": [],
              "now_utc": now_text, "outcomes": produced, "rejected": rejected, "duplicates_ignored": duplicates,
              "execution_allowed": False, "authority": "none", "activation": "none"}
    record["evidence_digest"] = digest({"now_utc": now_text, "outcomes": [o["outcome_id"] for o in produced],
                                        "rejected": rejected})
    return record


def outcomes(advice: Any, attempts: Any, evaluator_events: Any, now: Any) -> dict:
    """Shadow outcome records. Never raises an ordinary error: anything unexpected is a refused record.
    Cancellation (KeyboardInterrupt, SystemExit, GeneratorExit) is not an Exception and propagates."""
    try:
        return _produce(advice, attempts, evaluator_events, now)
    except Exception:  # noqa: BLE001 - production never raises; anything unexpected refuses
        return _refused("input_malformed", now)
