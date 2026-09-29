#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Bridge v2 F20: the pure Grok consult route (Tools fb59267c slice B-Grok-F20).

Three pure functions. None of them reads a file, clock, environment, network or
provider, and every result carries ``execution_allowed`` False and ``authority`` "none".

* ``prepare_grok_consult`` builds a typed consult INTENT, never a fleet worker wake.
  The intent binds the task, the request and its revision, the prompt digest, the
  exact read-only source snapshot, the model and effort, the budget class, an expiry,
  the authorization reference (the signed policy digest) and a caller nonce.
* ``admit(intent, evidence)`` decides admit, refuse or blocked_unknown. It needs all of:
  - a fresh, enabled F0 ``Decision`` for F20, bound to the signed policy digest and
    to the head/tree its signature was checked against;
  - caps from that same policy (``parameters.F20``);
  - the observed read-only snapshot, equal to that signed head/tree;
  - the helper's shared hourly budget: at most ONE attempted consultation per 60
    minutes, failures included, and a reserved or interrupted_or_unknown attempt
    always refuses;
  - a durable admission ledger that serializes admissions.
  A missing port or an unreadable fact is blocked_unknown, never a simulated
  readiness. There are no exemptions: an operator request or a relayed event text
  is not a signed activation.
* ``bind_answer`` accepts the helper's result only if it is the answered attempt for
  this task, the answer was read from exactly the report the helper hashed, its tool
  transcript uses allowlisted tools only (none by default), and the record binds the
  request, prompt, snapshot, nonce and answer.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from tools.bridge_v2_activation import Decision, canonical_sha256
from tools.lane_profile_record import _utc

INTENT_SCHEMA = "wd.grok-consult-intent.v1"
ADMISSION_SCHEMA = "wd.grok-consult-admission.v1"
ANSWER_SCHEMA = "wd.grok-consult-answer.v1"
HELPER_STATE_SCHEMA = "wd.grok-hourly.v1"  # the unchanged helper contract (RCO1 7da35242)
FEATURE = "F20"
BUDGET_CLASSES = ("shared_hourly",)
BUDGET_WINDOW = timedelta(hours=1)  # one attempted consultation per 60 min, failures included
MAX_F0_AGE = timedelta(seconds=60)
MAX_OBSERVATION_AGE = timedelta(seconds=120)
UNRESOLVED = ("reserved", "interrupted_or_unknown")
CAP_KEYS = frozenset({"model", "efforts", "allowed_tools", "max_prompt_bytes", "max_intent_ttl_seconds"})
INTENT_KEYS = frozenset({"schema", "feature", "task_id", "request_id", "request_revision", "prompt_sha256",
                         "prompt_bytes", "snapshot", "model", "effort", "budget_class", "authorization_ref",
                         "nonce", "created_utc", "expires_utc", "wakes_worker"})
TASK_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,159}")  # the helper's own bound
HEX32, HEX40, HEX64 = (re.compile(r"[0-9a-f]{%d}" % n) for n in (32, 40, 64))
ADMIT, REFUSE, BLOCKED = "admit", "refuse", "blocked_unknown"


class RouteError(ValueError):
    """An intent cannot be built from these inputs; ``code`` is a stable reason."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _hex(value: Any, pattern) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _aware(now: Any) -> datetime | None:
    if not isinstance(now, datetime) or now.utcoffset() is None:
        return None
    try:
        return now.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        return None


def prompt_sha256(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def prepare_grok_consult(*, task_id: Any, request_id: Any, request_revision: Any, prompt: Any, snapshot: Any,
                         model: Any, effort: Any, budget_class: Any, authorization_ref: Any, nonce: Any,
                         ttl_seconds: Any, now: Any) -> dict:
    """A typed consult intent. It is advice for the broker: it wakes no worker and authorizes nothing."""
    moment = _aware(now)
    checks = [
        (moment is not None, "time_unknown"),
        (isinstance(task_id, str) and TASK_RE.fullmatch(task_id) is not None, "task_id_invalid"),
        (_hex(request_id, HEX32), "request_id_invalid"),
        (type(request_revision) is int and request_revision >= 1, "request_revision_invalid"),
        (_text(prompt), "prompt_empty"),
        (isinstance(snapshot, dict) and set(snapshot) == {"head", "tree"} and _hex(snapshot.get("head"), HEX40)
         and _hex(snapshot.get("tree"), HEX40), "snapshot_invalid"),
        (_text(model) and _text(effort), "model_effort_invalid"),
        (budget_class in BUDGET_CLASSES, "budget_class_invalid"),
        (_hex(authorization_ref, HEX64), "authorization_ref_invalid"),
        (_hex(nonce, HEX32), "nonce_invalid"),
        (type(ttl_seconds) is int and 1 <= ttl_seconds <= 3600, "ttl_invalid"),
    ]
    for ok, code in checks:
        if not ok:
            raise RouteError(code)
    return {"schema": INTENT_SCHEMA, "feature": FEATURE, "task_id": task_id, "request_id": request_id,
            "request_revision": request_revision, "prompt_sha256": prompt_sha256(prompt),
            "prompt_bytes": len(prompt.encode("utf-8")), "snapshot": dict(snapshot), "model": model,
            "effort": effort, "budget_class": budget_class, "authorization_ref": authorization_ref,
            "nonce": nonce, "created_utc": _stamp(moment),
            "expires_utc": _stamp(moment + timedelta(seconds=ttl_seconds)), "wakes_worker": False}


def _result(verdict: str, reasons: list, intent_sha256: str | None, **extra) -> dict:
    return {"schema": ADMISSION_SCHEMA, "verdict": verdict, "reasons": reasons, "intent_sha256": intent_sha256,
            **extra, "execution_allowed": False, "authority": "none"}


def _observed(block: Any, now: datetime) -> bool:
    if not isinstance(block, dict):
        return False
    observed = _utc(block.get("observed_utc"))
    return observed is not None and now - MAX_OBSERVATION_AGE <= observed <= now


def _intent_sha256(intent: Any) -> str | None:
    try:
        return canonical_sha256(intent)
    except (TypeError, ValueError, RecursionError):
        return None


def admit(intent: Any, evidence: Any) -> dict:
    """admit | refuse | blocked_unknown. Refusals are checked before readiness, and nothing grants an exemption."""
    digest = _intent_sha256(intent)
    # Exact keys: an added field (an "exemption", a relayed operator text) makes the intent malformed.
    if not (isinstance(intent, dict) and set(intent) == INTENT_KEYS and intent.get("schema") == INTENT_SCHEMA
            and intent.get("feature") == FEATURE and intent.get("wakes_worker") is False
            and intent.get("budget_class") in BUDGET_CLASSES and digest is not None and isinstance(evidence, dict)):
        return _result(BLOCKED, ["inputs_malformed"], digest)
    now = _utc(evidence.get("now_utc"))
    if now is None:
        return _result(BLOCKED, ["time_unknown"], digest)
    created, expires = _utc(intent.get("created_utc")), _utc(intent.get("expires_utc"))
    if created is None or expires is None or not created <= now < expires:
        return _result(REFUSE, ["intent_expired_or_future"], digest)

    # F0: a fresh, enabled Decision for F20 under the policy the intent names. A dict is never a Decision.
    f0 = evidence.get("f0")
    decision = f0.get("decision") if isinstance(f0, dict) else None
    evaluated = _utc(f0.get("evaluated_utc")) if isinstance(f0, dict) else None
    if type(decision) is not Decision or evaluated is None:
        return _result(BLOCKED, ["f0_unknown"], digest)
    if decision.feature != FEATURE or decision.enabled is not True:
        return _result(REFUSE, ["f0_disabled"], digest)
    if not now - MAX_F0_AGE <= evaluated <= now:
        return _result(REFUSE, ["f0_stale"], digest)
    policy = evidence.get("policy")
    policy_sha256 = _intent_sha256(policy) if isinstance(policy, dict) else None
    if policy_sha256 is None or not policy_sha256 == decision.policy_sha256 == intent.get("authorization_ref"):
        return _result(REFUSE, ["authorization_unbound"], digest)
    caps = policy["parameters"].get(FEATURE) if isinstance(policy.get("parameters"), dict) else None
    if not (isinstance(caps, dict) and set(caps) == CAP_KEYS and isinstance(caps["efforts"], list)
            and isinstance(caps["allowed_tools"], list) and type(caps["max_prompt_bytes"]) is int
            and type(caps["max_intent_ttl_seconds"]) is int):
        return _result(BLOCKED, ["caps_unknown"], digest)
    if intent.get("model") != caps["model"] or intent.get("effort") not in caps["efforts"]:
        return _result(REFUSE, ["model_effort_outside_caps"], digest)
    if type(intent.get("prompt_bytes")) is not int or intent["prompt_bytes"] > caps["max_prompt_bytes"]:
        return _result(REFUSE, ["prompt_outside_caps"], digest)
    if expires - created > timedelta(seconds=caps["max_intent_ttl_seconds"]):
        return _result(REFUSE, ["intent_ttl_outside_caps"], digest)

    # The read-only snapshot the consultation will see: exactly the intent's.
    snapshot = evidence.get("snapshot")
    if not _observed(snapshot, now) or snapshot.get("readonly") is not True:
        return _result(BLOCKED, ["snapshot_unknown"], digest)
    if {k: snapshot.get(k) for k in ("head", "tree")} != intent.get("snapshot"):
        return _result(REFUSE, ["snapshot_mismatch"], digest)
    if {k: f0.get(k) for k in ("head", "tree")} != intent.get("snapshot"):
        return _result(REFUSE, ["snapshot_unsigned"], digest)  # F0 checked the signature at another head/tree

    # The shared hourly budget, as the unchanged helper reports it.
    budget = evidence.get("budget")
    if not _observed(budget, now) or budget.get("schema") != HELPER_STATE_SCHEMA:
        return _result(BLOCKED, ["budget_unknown"], digest)
    if budget.get("status") in UNRESOLVED:
        return _result(REFUSE, ["unreconciled_attempt:" + budget["status"]], digest)
    last_attempt = _utc(budget.get("last_attempt_utc"))
    if last_attempt is None or type(budget.get("eligible")) is not bool:
        return _result(BLOCKED, ["budget_unknown"], digest)
    if budget["eligible"] is not True or now - last_attempt < BUDGET_WINDOW:
        return _result(REFUSE, ["hourly_budget_used"], digest)

    # The durable admission ledger serializes admissions; without it, readiness is unknown.
    ledger = evidence.get("admission_ledger")
    if not _observed(ledger, now) or not isinstance(ledger.get("open"), list):
        return _result(BLOCKED, ["admission_ledger_unknown"], digest)
    if ledger["open"]:
        return _result(REFUSE, ["admission_in_flight"], digest)
    last_admitted = ledger.get("last_admitted_utc")
    if last_admitted is not None:
        admitted = _utc(last_admitted)
        if admitted is None:
            return _result(BLOCKED, ["admission_ledger_unknown"], digest)
        if now - admitted < BUDGET_WINDOW:
            return _result(REFUSE, ["hourly_budget_used"], digest)
    return _result(ADMIT, ["all_gates_passed"], digest, policy_sha256=policy_sha256,
                   admitted_utc=_stamp(now), allowed_tools=list(caps["allowed_tools"]))


def bind_answer(intent: dict, admission: dict, report: Any, answer: Any) -> dict:
    """Bind the helper's answered attempt to the intent, or refuse. No retry is ever implied."""
    def refuse(reason: str) -> dict:
        return {"schema": ANSWER_SCHEMA, "verdict": REFUSE, "reasons": [reason],
                "intent_sha256": admission.get("intent_sha256"), "execution_allowed": False, "authority": "none"}

    if admission.get("verdict") != ADMIT or admission.get("intent_sha256") != _intent_sha256(intent):
        return refuse("admission_unbound")
    if not isinstance(report, dict) or report.get("schema") != HELPER_STATE_SCHEMA:
        return refuse("report_unknown")
    if report.get("status") != "answered" or report.get("task_id") != intent["task_id"]:
        return refuse("not_the_answered_attempt")
    attempted, admitted = _utc(report.get("last_attempt_utc")), _utc(admission.get("admitted_utc"))
    if not _hex(report.get("request_id"), HEX32) or attempted is None or admitted is None or attempted < admitted:
        return refuse("attempt_unbound")  # an older attempt can never answer this intent
    if not isinstance(answer, dict) or not isinstance(answer.get("text"), str) \
            or not isinstance(answer.get("tool_calls"), list):
        return refuse("answer_unknown")
    if not _hex(report.get("report_sha256"), HEX64) or answer.get("report_sha256") != report["report_sha256"]:
        return refuse("answer_not_from_report")  # the reader must have hashed exactly the helper's report bytes
    allowed = set(admission.get("allowed_tools") or [])
    if any(not isinstance(tool, str) or tool not in allowed for tool in answer["tool_calls"]):
        return refuse("forbidden_tool_in_transcript")
    return {"schema": ANSWER_SCHEMA, "verdict": "answered_bound", "reasons": [],
            "intent_sha256": admission["intent_sha256"], "helper_request_id": report["request_id"],
            "request_id": intent["request_id"], "request_revision": intent["request_revision"],
            "prompt_sha256": intent["prompt_sha256"], "snapshot": intent["snapshot"], "nonce": intent["nonce"],
            "report_sha256": report["report_sha256"],
            "answer_sha256": hashlib.sha256(answer["text"].encode("utf-8")).hexdigest(),
            "execution_allowed": False, "authority": "none"}
