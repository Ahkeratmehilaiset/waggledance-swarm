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
  - the helper's own state: a reserved or interrupted_or_unknown (unreconciled) attempt
    always refuses, and a helper that reports itself not eligible (its own provider
    limit or deferral) refuses as helper_not_eligible. There is NO local rate quota
    (operator direction 2026-09-30): completed attempts may follow back to back, and
    provider limits stay the provider's, never a required gate;
  - a durable admission ledger that serializes admissions: an open (unreconciled)
    entry refuses as admission_in_flight, so there is one flight at a time.
  A missing port or an unreadable fact is blocked_unknown, never a simulated
  readiness. There are no exemptions: an operator request or a relayed event text
  is not a signed activation.
* ``bind_answer`` accepts the helper's result only if it is the answered attempt for
  this task, started between the admission and the intent's expiry, the answer was
  read from exactly the report the helper hashed, its tool
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
# The helper contract this route and the broker rely on (RCO1 e855 S3), pinned to the reviewed helper blobs of
# tools/wd_grok_helper.py. A different blob fails the contract fixture until it is re-reviewed here.
HELPER_BLOBS = {"ae94754cfd88d24ffa9f7828315b513a6040e1b0": "7da35242 (RCO1-reviewed contract)",
                "790a7b0897c12eefe2bfc71d938d707db9e30ea5": "the F20 branch copy",
                "9c2cb61c18197606ce64f3562246b8b2d797b29a": "55026fad (strict lifecycle receipts)",
                "0085e3e2255d4de7cc1a349e8e3c2a2106225d77": "Fable 0621 no-hour helper (eligible = local availability)",
                "1b0299f436d6ed7fd1d625661764e6a4274f0767": "Fable 0700 fleet context isolation (same local eligibility)"}
HELPER_STATUS_FIELDS = ("schema", "status", "eligible")  # what admit reads from status(); no local hour
HELPER_REPORT_FIELDS = ("schema", "status", "task_id", "request_id", "last_attempt_utc", "report_sha256")
HELPER_MAX_PROMPT_BYTES = 48000  # wd_grok_helper.consult refuses more itself, but after the broker's reservation
FEATURE = "F20"
BUDGET_CLASSES = ("shared_hourly",)  # the one shared broker lane's label; no local hourly quota (2026-09-30)
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


MAX_OFFSET = timedelta(hours=24)  # datetime's own bound: an offset strictly inside +-24 h


def _stamp(moment: datetime) -> str:
    """'YYYY-MM-DDTHH:MM:SSZ' (four-digit year, whole seconds) of a datetime that is ALREADY UTC: every caller
    passes an _aware or _utc result. Nothing here reads an offset or converts a zone."""
    return moment.replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


def _aware(now: Any) -> datetime | None:
    """``now`` in UTC at full precision, or None (unknown). The one rule of the F3/F24 normalizers: exactly a
    datetime; its offset read ONCE, inside the try; exactly a timedelta strictly inside +-24 h; the naive wall
    time minus that offset, marked UTC. There is no astimezone and no local-time fallback, so a stateful
    tzinfo (an offset, then None) or a subclass can never make a wall time pass as local time. Every
    ordinary error (NotImplementedError, TypeError, ValueError, OverflowError past datetime.min/max) is None."""
    try:
        if type(now) is not datetime:
            return None
        offset = now.utcoffset()
        if type(offset) is not timedelta or not -MAX_OFFSET < offset < MAX_OFFSET:
            return None  # naive (None), a timedelta subclass, anything else, or out of range
        return (now.replace(tzinfo=None) - offset).replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001 - an unreadable or unrepresentable time is unknown, never raised
        return None


def utc_stamp(moment: Any) -> str | None:
    """A whole-second UTC stamp; None when the time is unknown (see _aware)."""
    aware = _aware(moment)
    return None if aware is None else _stamp(aware)


def aware_utc(moment: Any) -> datetime | None:
    """The time in UTC at full precision; None when it is unknown (see _aware)."""
    return _aware(moment)


def parse_utc(value: Any) -> datetime | None:
    """A port-supplied fact time (ISO 8601 with an offset or Z) in UTC; None when it is unknown."""
    return _utc(value)


def _utf8(text: Any) -> bytes | None:
    """The UTF-8 bytes of exactly a str; None for a lone surrogate or a non-str (never raises)."""
    if type(text) is not str:
        return None
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError:
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
        (_utf8(prompt) is not None, "prompt_malformed"),  # a lone surrogate refuses, never raises
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
    try:
        expires = moment + timedelta(seconds=ttl_seconds)
    except OverflowError:
        raise RouteError("time_unknown") from None  # an expiry past datetime.max is unknown, never raised raw
    return {"schema": INTENT_SCHEMA, "feature": FEATURE, "task_id": task_id, "request_id": request_id,
            "request_revision": request_revision, "prompt_sha256": prompt_sha256(prompt),
            "prompt_bytes": len(prompt.encode("utf-8")), "snapshot": dict(snapshot), "model": model,
            "effort": effort, "budget_class": budget_class, "authorization_ref": authorization_ref,
            "nonce": nonce, "created_utc": _stamp(moment), "expires_utc": _stamp(expires), "wakes_worker": False}


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
            and type(caps["max_intent_ttl_seconds"]) is int and caps["max_prompt_bytes"] >= 1
            and caps["max_intent_ttl_seconds"] >= 1):
        return _result(BLOCKED, ["caps_unknown"], digest)
    if intent.get("model") != caps["model"] or intent.get("effort") not in caps["efforts"]:
        return _result(REFUSE, ["model_effort_outside_caps"], digest)
    # The helper's own cap binds too: a signed cap above it would admit a prompt the helper then refuses.
    if type(intent.get("prompt_bytes")) is not int \
            or intent["prompt_bytes"] > min(caps["max_prompt_bytes"], HELPER_MAX_PROMPT_BYTES):
        return _result(REFUSE, ["prompt_outside_caps"], digest)
    if (expires - created).total_seconds() > caps["max_intent_ttl_seconds"]:  # never builds a timedelta from a cap
        return _result(REFUSE, ["intent_ttl_outside_caps"], digest)

    # The read-only snapshot the consultation will see: exactly the intent's.
    snapshot = evidence.get("snapshot")
    if not _observed(snapshot, now) or snapshot.get("readonly") is not True:
        return _result(BLOCKED, ["snapshot_unknown"], digest)
    if {k: snapshot.get(k) for k in ("head", "tree")} != intent.get("snapshot"):
        return _result(REFUSE, ["snapshot_mismatch"], digest)
    if {k: f0.get(k) for k in ("head", "tree")} != intent.get("snapshot"):
        return _result(REFUSE, ["snapshot_unsigned"], digest)  # F0 checked the signature at another head/tree

    # The helper's own state; no local rate quota (operator direction 2026-09-30). An unreconciled attempt
    # refuses (one flight), and a helper that reports itself not eligible is skipped, never a local hour.
    budget = evidence.get("budget")
    if not _observed(budget, now) or budget.get("schema") != HELPER_STATE_SCHEMA:
        return _result(BLOCKED, ["budget_unknown"], digest)
    if budget.get("status") in UNRESOLVED:
        return _result(REFUSE, ["unreconciled_attempt:" + budget["status"]], digest)
    if type(budget.get("eligible")) is not bool:
        return _result(BLOCKED, ["budget_unknown"], digest)
    if budget["eligible"] is not True:
        return _result(REFUSE, ["helper_not_eligible"], digest)  # its own provider limit or deferral

    # The durable admission ledger serializes admissions (one flight); without it, readiness is unknown.
    ledger = evidence.get("admission_ledger")
    if not _observed(ledger, now) or not isinstance(ledger.get("open"), list):
        return _result(BLOCKED, ["admission_ledger_unknown"], digest)
    if ledger["open"]:
        return _result(REFUSE, ["admission_in_flight"], digest)
    last_admitted = ledger.get("last_admitted_utc")
    if last_admitted is not None and _utc(last_admitted) is None:
        return _result(BLOCKED, ["admission_ledger_unknown"], digest)  # a malformed view; there is no rate gate
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
    expires = _utc(intent.get("expires_utc"))
    if not _hex(report.get("request_id"), HEX32) or attempted is None or admitted is None or expires is None \
            or not admitted <= attempted <= expires:
        return refuse("attempt_unbound")  # only an attempt inside [admission, intent expiry] answers this intent
    if not isinstance(answer, dict) or _utf8(answer.get("text")) is None \
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
