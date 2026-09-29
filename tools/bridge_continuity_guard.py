# SPDX-License-Identifier: BUSL-1.1
"""Pure bridge continuity guard: decide whether a lane's open work has stalled.

``evaluate(snapshot, now_utc)`` turns an already-validated continuity snapshot
(``wd.continuity-snapshot.v1``) into a ``wd.continuity-decision.v1`` verdict:
``dispatch | decide | wait | hold | idle_ok | unknown``.

It is a pure function. It reads no files, environment, clock or bridge log, and
it never writes, wakes or grants authority. The runtime wiring collects the
snapshot through the pinned canonical reader and acts on the decision.

Incident this guards against (2026-09-28 22:59Z): an awaited result was posted
unbound with ``notification=informational``. No wake fired, and every lane
waited on another lane for hours. The guard therefore fails closed:

* Missing, stale or malformed evidence is ``unknown``, never ``idle_ok``.
* A dependency is done only on an exact bound reply that the pinned predicate
  accepted (``binding_valid`` and ``schema_valid``). Processing is proven only
  by an exact receipt from the subject for that event. Heartbeats and unrelated
  chatter never count.
* An unbound historical result never satisfies a binding. It becomes a
  recoverable ``decide`` item.
* An unfinished checkpoint with no wake-up is recovered after
  ``checkpoint_stale_seconds``. Expired claims never erase work.
* HOLDs and exact, authorised cancellations are respected. A cancellation
  never clears a HOLD and never widens beyond its exact target.
* A declared wait without a deadline, and a checkpoint that says it is waiting
  but has no structured wait or request behind it, are ``unknown``. Both
  escalate instead of waiting silently. A result that arrived before the wait
  was declared still counts.
* With ``evidence.scope == "checkpoint_only"`` (no request evidence), only an
  allow-listed status vocabulary may be recovered, and that recovery is one
  bounded reconcile wake, not acceptance of any dependency. No-wake statuses
  are held and unrecognised statuses are ``unknown``. In either scope, a
  control token in the checkpoint's free text (blockers, next action) means a
  HOLD is possible, so it is ``unknown``.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import re
import sys
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

SNAPSHOT_SCHEMA = "wd.continuity-snapshot.v1"
DECISION_SCHEMA = "wd.continuity-decision.v1"

PRIORITY = {"idle_ok": 0, "hold": 1, "wait": 2, "decide": 3, "dispatch": 4, "unknown": 5}
SCOPES = frozenset({"canonical", "checkpoint_only"})
DONE_STATUSES = frozenset({"done", "completed", "closed", "idle"})
PAUSED_STATUSES = frozenset({"paused", "hold", "held", "on_hold", "operator_pause"})
CANCELLED_STATUSES = frozenset({"cancelled", "canceled"})
WAITING_STATUS_PREFIXES = ("waiting", "awaiting", "blocked")
# checkpoint_only vocabulary gate (Lead/RCO safety contract 2026-09-29T05:30Z).
NO_WAKE_STATUS_SUBSTRINGS = ("hold", "held", "paus", "park", "defer", "cancel", "abandon",
                             "operator", "signature", "nonce", "idle", "sentinel",
                             "delivered", "replied")
RECOVERABLE_STATUSES = frozenset({"in_progress", "review_pending", "reviewing_exact_head",
                                  "awaiting_review", "awaiting-review",
                                  "awaiting_local_full_suite", "implemented_awaiting_full_suite",
                                  "source_complete_reviews_pending"})
_RECOVERABLE_STATUS_RE = re.compile(r"^r[0-9]+_pushed_awaiting_[a-z0-9_-]+$")
# Conservative, self-contained control-token contract. No shared helper exists at
# the c5f7c933 base, so it stays here to avoid dependency drift. A match in free
# text means a HOLD may be in force: escalate, never wake.
CONTROL_TOKENS = ("hold", "paus", "stop", "halt", "freeze", "do not", "do_not", "don't",
                  "dont ", "veto", "operator", "signature", "nonce", "approval", "cutover",
                  "rollback", "revert", "abort", "cancel", "abandon", "park", "defer")
NON_PROOF_EVENT_TYPES = frozenset({"heartbeat", "liveness"})
CLAIM_STATES = frozenset({"active", "expired", "released"})
PROCESSING_KINDS = frozenset({"receipt", "bound_reply"})
CANCELLATION_KINDS = frozenset({"request", "wait"})
LIST_FIELDS = ("claims", "inbound_requests", "waits", "events", "processing",
               "cancellations", "holds")
CHECKPOINT_ONLY_EMPTY = ("claims", "inbound_requests", "waits", "events", "processing",
                         "cancellations")
DEFAULT_POLICY = {"grace_seconds": 900, "checkpoint_stale_seconds": 1800,
                  "unbounded_decide_seconds": 7200, "evidence_max_age_seconds": 300}
POLICY_MAX_SECONDS = 7 * 86400
CLOCK_SKEW = timedelta(seconds=60)

_AGENT_RE = re.compile(r"^[a-z][a-z0-9_-]{1,32}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TS_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$")


class _Invalid(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# --- field validation -------------------------------------------------------------------

def _mapping(value: Any, where: str) -> Mapping:
    if not isinstance(value, Mapping):
        raise _Invalid(f"not_an_object:{where}")
    return value


def _field(obj: Mapping, key: str, where: str) -> Any:
    if key not in obj:
        raise _Invalid(f"missing_field:{where}.{key}")
    return obj[key]


def _text(obj: Mapping, key: str, where: str, *, empty: bool = False,
          nullable: bool = False) -> str | None:
    value = _field(obj, key, where)
    if value is None and nullable:
        return None
    if not isinstance(value, str) or (not empty and not value):
        raise _Invalid(f"bad_text:{where}.{key}")
    return value


def _flag(obj: Mapping, key: str, where: str) -> bool:
    value = _field(obj, key, where)
    if not isinstance(value, bool):
        raise _Invalid(f"bad_bool:{where}.{key}")
    return value


def _agent(value: Any, where: str) -> str:
    if not isinstance(value, str) or not _AGENT_RE.match(value):
        raise _Invalid(f"bad_agent:{where}")
    return value


def _sha(value: Any, where: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.match(value):
        raise _Invalid(f"bad_sha256:{where}")
    return value


def _parse_ts(value: Any, where: str) -> datetime:
    if not isinstance(value, str):
        raise _Invalid(f"bad_timestamp:{where}")
    text = value[4:] if value.startswith("utc:") else value
    match = _TS_RE.match(text)
    if not match:
        raise _Invalid(f"bad_timestamp:{where}")
    base, fraction, zone = match.groups()
    iso = base + ("." + fraction[:6].ljust(6, "0") if fraction else "")
    iso += "+00:00" if zone == "Z" else zone
    try:
        return datetime.fromisoformat(iso).astimezone(timezone.utc)
    except ValueError:
        raise _Invalid(f"bad_timestamp:{where}") from None


def _ts(obj: Mapping, key: str, where: str, *, nullable: bool = False) -> datetime | None:
    value = _field(obj, key, where)
    if value is None and nullable:
        return None
    return _parse_ts(value, f"{where}.{key}")


def _string_list(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) and v for v in value):
        raise _Invalid(f"bad_list:{where}")
    return tuple(value)


def _items(snapshot: Mapping, key: str) -> list:
    value = _field(snapshot, key, "snapshot")
    if not isinstance(value, (list, tuple)):
        raise _Invalid(f"bad_list:snapshot.{key}")
    return list(value)


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _dedupe(records: list[dict], key: str | None, where: str) -> list[dict]:
    """Drop identical duplicates; the same id with different content is a conflict."""
    seen: dict[str, str] = {}
    kept: list[dict] = []
    for record in records:
        body = _canon(record)
        ident = record[key] if key else body
        if ident in seen:
            if seen[ident] != body:
                raise _Invalid(f"conflicting_duplicate:{where}")
            continue
        seen[ident] = body
        kept.append(record)
    return kept


def _validate(snapshot: Any, now: datetime) -> dict:
    snap = _mapping(snapshot, "snapshot")
    if _field(snap, "schema", "snapshot") != SNAPSHOT_SCHEMA:
        raise _Invalid("bad_schema")
    agent = _agent(_field(snap, "agent", "snapshot"), "snapshot.agent")

    policy = dict(DEFAULT_POLICY)
    raw_policy = snap.get("policy")
    if raw_policy is not None:
        for key, value in _mapping(raw_policy, "policy").items():
            if key not in DEFAULT_POLICY:
                raise _Invalid(f"unknown_policy:{key}")
            if type(value) is not int or not 1 <= value <= POLICY_MAX_SECONDS:
                raise _Invalid(f"bad_policy:{key}")
            policy[key] = value

    ev = _mapping(_field(snap, "evidence", "snapshot"), "evidence")
    evidence = {
        "complete": _flag(ev, "complete", "evidence"),
        "scope": _field(ev, "scope", "evidence"),
        "collected_at": _ts(ev, "collected_at_utc", "evidence"),
        "source_digest": _text(ev, "source_digest", "evidence"),
        "errors": _field(ev, "errors", "evidence"),
    }
    if evidence["scope"] not in SCOPES:
        raise _Invalid("bad_scope")
    if not isinstance(evidence["errors"], (list, tuple)):
        raise _Invalid("bad_list:evidence.errors")

    raw_cp = _field(snap, "checkpoint", "snapshot")
    checkpoint = None
    if raw_cp is not None:
        c = _mapping(raw_cp, "checkpoint")
        checkpoint = {
            "task_id": _text(c, "task_id", "checkpoint"),
            "status": _text(c, "status", "checkpoint"),
            "next_action": _text(c, "next_action", "checkpoint", empty=True),
            "next_wakeup": _ts(c, "next_wakeup_utc", "checkpoint", nullable=True),
            "updated_at": _ts(c, "updated_at_utc", "checkpoint"),
            "blockers": tuple(c.get("blockers", ())) if isinstance(c.get("blockers", ()), (list, tuple))
            else None,
        }
        if checkpoint["blockers"] is None or not all(isinstance(b, str) for b in checkpoint["blockers"]):
            raise _Invalid("bad_list:checkpoint.blockers")

    claims = []
    for raw in _items(snap, "claims"):
        c = _mapping(raw, "claim")
        state = _field(c, "state", "claim")
        if state not in CLAIM_STATES:
            raise _Invalid("bad_claim_state")
        claims.append({"task_id": _text(c, "task_id", "claim"), "state": state,
                       "release_reason": _text(c, "release_reason", "claim", empty=True,
                                               nullable=True)})

    requests = []
    for raw in _items(snap, "inbound_requests"):
        r = _mapping(raw, "request")
        requests.append({
            "request_id": _text(r, "request_id", "request"),
            "request_digest": _text(r, "request_digest", "request"),
            "event_sha256": _sha(_field(r, "event_sha256", "request"), "request.event_sha256"),
            "task_id": _text(r, "task_id", "request"),
            "from_agent": _agent(_field(r, "from_agent", "request"), "request.from_agent"),
            "ts": _ts(r, "ts_utc", "request"),
            "deadline": _ts(r, "deadline_utc", "request", nullable=True),
        })

    waits = []
    for raw in _items(snap, "waits"):
        w = _mapping(raw, "wait")
        responder = _mapping(_field(w, "expected_responder", "wait"), "wait.expected_responder")
        session = responder.get("session_id")
        if session is not None and (not isinstance(session, str) or not session):
            raise _Invalid("bad_text:wait.expected_responder.session_id")
        record = {
            "wait_id": _text(w, "wait_id", "wait"),
            "waiter_task_id": _text(w, "waiter_task_id", "wait"),
            "dependency_task_id": _text(w, "dependency_task_id", "wait"),
            "request_id": _text(w, "request_id", "wait"),
            "request_digest": _text(w, "request_digest", "wait"),
            "responder": _agent(_field(responder, "agent", "wait.expected_responder"),
                                "wait.expected_responder.agent"),
            "session_id": session,
            "head_sha": _text(w, "head_sha", "wait", nullable=True),
            "deadline": _ts(w, "deadline_utc", "wait", nullable=True),
            "declared_at": _ts(w, "declared_at_utc", "wait"),
        }
        if record["responder"] == agent:
            raise _Invalid("self_wait")
        waits.append(record)

    events = []
    for raw in _items(snap, "events"):
        e = _mapping(raw, "event")
        session = e.get("session_id")
        if session is not None and not isinstance(session, str):
            raise _Invalid("bad_text:event.session_id")
        to = _text(e, "to_agent", "event")
        events.append({
            "event_sha256": _sha(_field(e, "event_sha256", "event"), "event.event_sha256"),
            "from_agent": _agent(_field(e, "from_agent", "event"), "event.from_agent"),
            "to": tuple(t.strip() for t in to.split(",") if t.strip()),
            "task_id": _text(e, "task_id", "event"),
            "type": _text(e, "type", "event"),
            "status": _text(e, "status", "event", empty=True),
            "ts": _ts(e, "ts_utc", "event"),
            "request_id": _text(e, "request_id", "event", nullable=True),
            "in_reply_to": _text(e, "in_reply_to_request_id", "event", nullable=True),
            "head": _text(e, "head", "event", nullable=True),
            "informational": _flag(e, "informational", "event"),
            "session_id": session,
            "binding_valid": _flag(e, "binding_valid", "event"),
            "schema_valid": _flag(e, "schema_valid", "event"),
        })

    processing = []
    for raw in _items(snap, "processing"):
        p = _mapping(raw, "processing")
        kind = _field(p, "kind", "processing")
        if kind not in PROCESSING_KINDS:
            raise _Invalid("bad_processing_kind")
        processing.append({
            "event_sha256": _sha(_field(p, "event_sha256", "processing"), "processing.event_sha256"),
            "by_agent": _agent(_field(p, "by_agent", "processing"), "processing.by_agent"),
            "ts": _ts(p, "ts_utc", "processing"),
            "kind": kind,
        })

    cancellations = []
    for raw in _items(snap, "cancellations"):
        c = _mapping(raw, "cancellation")
        kind = _field(c, "target_kind", "cancellation")
        if kind not in CANCELLATION_KINDS:
            raise _Invalid("bad_cancellation_kind")
        cancellations.append({
            "target_kind": kind,
            "target_id": _text(c, "target_id", "cancellation"),
            "by_agent": _agent(_field(c, "by_agent", "cancellation"), "cancellation.by_agent"),
            "ts": _ts(c, "ts_utc", "cancellation"),
        })

    holds = []
    for raw in _items(snap, "holds"):
        h = _mapping(raw, "hold")
        holds.append({
            "hold_id": _text(h, "hold_id", "hold"),
            "task_ids": _string_list(_field(h, "task_ids", "hold"), "hold.task_ids"),
            "request_ids": _string_list(_field(h, "request_ids", "hold"), "hold.request_ids"),
            "active": _flag(h, "active", "hold"),
        })

    return {
        "agent": agent, "policy": policy, "evidence": evidence, "checkpoint": checkpoint,
        "claims": _dedupe(claims, None, "claims"),
        "inbound_requests": _dedupe(requests, "request_id", "inbound_requests"),
        "waits": _dedupe(waits, "wait_id", "waits"),
        "events": _dedupe(events, "event_sha256", "events"),
        "processing": _dedupe(processing, None, "processing"),
        "cancellations": _dedupe(cancellations, None, "cancellations"),
        "holds": _dedupe(holds, "hold_id", "holds"),
    }


def _evidence_problems(snap: dict, now: datetime) -> list[str]:
    ev = snap["evidence"]
    problems = []
    if not ev["complete"]:
        problems.append("evidence_incomplete")
    if ev["errors"]:
        problems.append("evidence_errors")
    age = now - ev["collected_at"]
    if age > timedelta(seconds=snap["policy"]["evidence_max_age_seconds"]):
        problems.append("evidence_stale")
    if age < -CLOCK_SKEW:
        problems.append("evidence_from_future")
    if snap["checkpoint"] is None:
        problems.append("checkpoint_missing")
    if ev["scope"] == "checkpoint_only" and any(snap[k] for k in CHECKPOINT_ONLY_EMPTY):
        problems.append("scope_violation")
    return problems


# --- decision building ------------------------------------------------------------------

def _key(value: Any) -> str:
    return hashlib.sha256(_canon(value).encode("utf-8")).hexdigest()


def _ref(kind: str, ident: str, task_id: str) -> dict:
    return {"kind": kind, "id": ident, "task_id": task_id}


def _item(agent: str, kind: str, ident: str, task_id: str, verdict: str, target: str,
          reasons: list[str], refs: list[dict], **extra: Any) -> dict:
    reasons = sorted(set(reasons))
    refs = sorted({_canon(r): r for r in refs}.values(), key=_canon)
    item = {"kind": kind, "id": ident, "task_id": task_id, "verdict": verdict,
            "target": target, "reasons": reasons, "work_refs": refs}
    item.update(extra)
    item["action_key"] = _key({"agent": agent, "kind": kind, "id": ident, "verdict": verdict,
                               "target": target, "reasons": reasons, "work_refs": refs})
    return item


def _unknown(snapshot: Any, now_utc: Any, reasons: list[str]) -> dict:
    agent = snapshot.get("agent") if isinstance(snapshot, Mapping) else None
    agent = agent if isinstance(agent, str) and _AGENT_RE.match(agent) else None
    reasons = sorted(set(reasons))
    decision = {"schema": DECISION_SCHEMA, "agent": agent,
                "now_utc": now_utc if isinstance(now_utc, str) else None,
                "scope": None, "verdict": "unknown", "reasons": reasons,
                "escalation": "operator", "target": None, "targets": [], "work_refs": [],
                "items": [], "authority": "none"}
    decision["action_key"] = _key({"schema": DECISION_SCHEMA, "agent": agent,
                                   "verdict": "unknown", "reasons": reasons})
    return decision


def _held(holds: list[dict], task_ids: set[str], request_ids: set[str]) -> list[str]:
    return sorted(h["hold_id"] for h in holds if h["active"] and
                  (set(h["task_ids"]) & task_ids or set(h["request_ids"]) & request_ids))


def _control_token(cp: dict) -> bool:
    texts = [cp["next_action"], *cp["blockers"]]
    return any(token in text.lower() for text in texts for token in CONTROL_TOKENS)


def _evaluate(snap: dict, now: datetime) -> tuple[list[dict], list[str]]:
    agent, policy = snap["agent"], snap["policy"]
    grace = timedelta(seconds=policy["grace_seconds"])
    stale = timedelta(seconds=policy["checkpoint_stale_seconds"])
    unbounded = timedelta(seconds=policy["unbounded_decide_seconds"])
    holds = snap["holds"]
    notes: list[str] = []

    processed = {p["event_sha256"]: p["kind"] for p in snap["processing"]
                 if p["by_agent"] == agent}
    addressed = [e for e in snap["events"]
                 if agent in e["to"] and e["from_agent"] != agent
                 and e["type"] not in NON_PROOF_EVENT_TYPES]

    # Exact, authorised cancellations only; everything else is ignored, never widened.
    requests_by_id = {r["request_id"]: r for r in snap["inbound_requests"]}
    waits_by_id = {w["wait_id"]: w for w in snap["waits"]}
    cancelled_requests: set[str] = set()
    cancelled_waits: set[str] = set()
    for c in snap["cancellations"]:
        if c["target_kind"] == "request" and c["target_id"] in requests_by_id and \
                requests_by_id[c["target_id"]]["from_agent"] == c["by_agent"]:
            cancelled_requests.add(c["target_id"])
        elif c["target_kind"] == "wait" and c["target_id"] in waits_by_id and \
                c["by_agent"] == agent:
            cancelled_waits.add(c["target_id"])
        else:
            notes.append("cancellation_ignored")

    items: list[dict] = []

    for w in snap["waits"]:
        tasks = {w["waiter_task_id"], w["dependency_task_id"]}
        refs = [_ref("wait", w["wait_id"], w["waiter_task_id"])]
        hold_ids = _held(holds, tasks, {w["request_id"]})
        base = dict(agent=agent, kind="wait", ident=w["wait_id"], task_id=w["waiter_task_id"])
        if hold_ids:
            items.append(_item(**base, verdict="hold", target=agent, reasons=["held"],
                               refs=refs + [_ref("hold", h, w["waiter_task_id"]) for h in hold_ids],
                               dependency_done=False, processed=False))
            continue
        if w["wait_id"] in cancelled_waits:
            items.append(_item(**base, verdict="idle_ok", target=agent, reasons=["cancelled"],
                               refs=refs, dependency_done=False, processed=False))
            continue
        valid, mismatch = [], []
        for e in addressed:
            if e["in_reply_to"] != w["request_id"] or e["from_agent"] != w["responder"]:
                continue
            problems = []
            if not e["binding_valid"]:
                problems.append("bound_reply_binding_invalid")
            if not e["schema_valid"]:
                problems.append("bound_reply_schema_invalid")
            if w["session_id"] is not None and e["session_id"] != w["session_id"]:
                problems.append("bound_reply_session_mismatch")
            if w["head_sha"] is not None and e["head"] != w["head_sha"]:
                problems.append("bound_reply_head_mismatch")
            (mismatch if problems else valid).append((e, problems))
        if len(valid) == 1:
            e = valid[0][0]
            refs.append(_ref("result", e["event_sha256"], e["task_id"]))
            if e["event_sha256"] in processed:
                items.append(_item(**base, verdict="idle_ok", target=agent,
                                   reasons=["dependency_done", "processed"], refs=refs,
                                   dependency_done=True, processed=True))
            elif now - e["ts"] >= grace:
                items.append(_item(**base, verdict="dispatch", target=agent,
                                   reasons=["dependency_done", "stall_waiter"], refs=refs,
                                   dependency_done=True, processed=False))
            else:
                items.append(_item(**base, verdict="wait", target=agent,
                                   reasons=["dependency_done_processing_pending"], refs=refs,
                                   dependency_done=True, processed=False))
            continue
        reasons: list[str] = []
        if len(valid) > 1:
            reasons.append("multiple_bound_results")
            refs += [_ref("result", e["event_sha256"], e["task_id"]) for e, _ in valid]
        for e, problems in mismatch:
            reasons += problems
            refs.append(_ref("rejected_result", e["event_sha256"], e["task_id"]))
        if w["deadline"] is not None and now > w["deadline"]:
            verdict, target = "dispatch", w["responder"]
            reasons.append("dependency_overdue")
        elif w["deadline"] is not None:
            verdict, target = "wait", agent
            reasons.append("dependency_pending")
        else:
            verdict, target = "unknown", agent
            reasons.append("wait_deadline_missing")
        if (valid[1:] or mismatch) and verdict == "wait":
            verdict, target = "decide", agent
        items.append(_item(**base, verdict=verdict, target=target, reasons=reasons, refs=refs,
                           dependency_done=False, processed=False))

    for r in snap["inbound_requests"]:
        refs = [_ref("request", r["request_id"], r["task_id"])]
        base = dict(agent=agent, kind="request", ident=r["request_id"], task_id=r["task_id"])
        hold_ids = _held(holds, {r["task_id"]}, {r["request_id"]})
        seen = processed.get(r["event_sha256"])
        if hold_ids:
            verdict, reasons = "hold", ["held"]
            refs += [_ref("hold", h, r["task_id"]) for h in hold_ids]
        elif r["request_id"] in cancelled_requests:
            verdict, reasons = "idle_ok", ["cancelled"]
        elif seen == "bound_reply":
            verdict, reasons = "idle_ok", ["answered"]
        elif r["deadline"] is not None and now > r["deadline"]:
            verdict, reasons = "dispatch", ["request_overdue"]
        elif seen is None and now - r["ts"] >= grace:
            verdict, reasons = "dispatch", ["unprocessed_request"]
        elif seen is None:
            verdict, reasons = "wait", ["request_pending"]
        elif r["deadline"] is None and now - r["ts"] >= unbounded:
            verdict, reasons = "decide", ["unbounded_request"]
        else:
            verdict, reasons = "wait", ["request_in_progress"]
        items.append(_item(**base, verdict=verdict, target=agent, reasons=reasons, refs=refs))

    cp = snap["checkpoint"]
    unfinished = cp["status"] not in DONE_STATUSES and cp["status"] not in CANCELLED_STATUSES
    unprocessed = [e for e in addressed if e["event_sha256"] not in processed]

    # Unbound results on related work never satisfy anything; they need a decision.
    related = {w["dependency_task_id"] for w in snap["waits"]} | \
              {w["waiter_task_id"] for w in snap["waits"]} | \
              {r["task_id"] for r in snap["inbound_requests"]}
    if unfinished:
        related.add(cp["task_id"])
    for e in unprocessed:
        if e["in_reply_to"] is not None or e["task_id"] not in related:
            continue
        refs = [_ref("unbound_result", e["event_sha256"], e["task_id"])]
        base = dict(agent=agent, kind="unbound_result", ident=e["event_sha256"],
                    task_id=e["task_id"])
        hold_ids = _held(holds, {e["task_id"]}, set())
        if hold_ids:
            items.append(_item(**base, verdict="hold", target=agent, reasons=["held"], refs=refs))
        elif now - e["ts"] >= grace:
            items.append(_item(**base, verdict="decide", target=agent,
                               reasons=["unbound_result_candidate"], refs=refs))
        else:
            items.append(_item(**base, verdict="wait", target=agent,
                               reasons=["unbound_result_recent"], refs=refs))

    refs = [_ref("checkpoint", cp["task_id"], cp["task_id"])]
    base = dict(agent=agent, kind="checkpoint", ident=cp["task_id"], task_id=cp["task_id"])
    hold_ids = _held(holds, {cp["task_id"]}, set())
    covering = [i for i in items if i["kind"] in ("wait", "request")
                and i["task_id"] == cp["task_id"] and i["verdict"] in ("wait", "dispatch", "decide")]
    if cp["status"] in DONE_STATUSES:
        verdict, reasons = "idle_ok", ["checkpoint_done"]
    elif cp["status"] in CANCELLED_STATUSES:
        verdict, reasons = "idle_ok", ["checkpoint_cancelled"]
    elif hold_ids:
        verdict, reasons = "hold", ["held"]
        refs += [_ref("hold", h, cp["task_id"]) for h in hold_ids]
    elif cp["status"] in PAUSED_STATUSES:
        verdict, reasons = "hold", ["checkpoint_paused"]
    elif _control_token(cp):
        verdict, reasons = "unknown", ["hold_possible_control_token"]
    elif snap["evidence"]["scope"] == "checkpoint_only" and \
            any(t in cp["status"].lower() for t in NO_WAKE_STATUS_SUBSTRINGS):
        verdict, reasons = "hold", ["checkpoint_status_no_wake"]
    elif snap["evidence"]["scope"] == "checkpoint_only" and \
            cp["status"] not in RECOVERABLE_STATUSES and \
            not _RECOVERABLE_STATUS_RE.match(cp["status"]):
        verdict, reasons = "unknown", ["checkpoint_status_unrecognized"]
    elif covering:
        verdict, reasons = "wait", ["covered_by_open_work"]
        refs += [_ref(i["kind"], i["id"], i["task_id"]) for i in covering]
    elif snap["evidence"]["scope"] == "canonical" and \
            cp["status"].startswith(WAITING_STATUS_PREFIXES):
        verdict, reasons = "unknown", ["waiting_without_structured_predicate"]
    elif cp["next_wakeup"] is not None and now >= cp["next_wakeup"]:
        verdict, reasons = "dispatch", ["checkpoint_wakeup_due"]
    elif cp["next_wakeup"] is not None:
        verdict, reasons = "wait", ["checkpoint_wakeup_scheduled"]
    elif now - cp["updated_at"] >= stale:
        verdict, reasons = "dispatch", ["unfinished_no_wakeup"]
    else:
        verdict, reasons = "wait", ["checkpoint_recent"]
    if verdict == "dispatch" and snap["evidence"]["scope"] == "checkpoint_only":
        reasons.append("bounded_reconcile_not_acceptance")
    if verdict == "dispatch":
        # Tell the woken lane exactly which addressed events it never processed.
        refs += [_ref("unprocessed_event", e["event_sha256"], e["task_id"]) for e in unprocessed]
    items.append(_item(**base, verdict=verdict, target=agent, reasons=reasons, refs=refs))

    order = {"checkpoint": 0, "wait": 1, "request": 2, "unbound_result": 3}
    items.sort(key=lambda i: (order[i["kind"]], i["id"]))
    return items, notes


def evaluate(snapshot: Any, now_utc: Any) -> dict:
    """Return a ``wd.continuity-decision.v1`` dict. Never raises; never does I/O."""
    try:
        now = _parse_ts(now_utc, "now_utc")
    except _Invalid as exc:
        return _unknown(snapshot, now_utc, [exc.code])
    try:
        snap = _validate(snapshot, now)
    except _Invalid as exc:
        return _unknown(snapshot, now_utc, [exc.code])
    except Exception as exc:  # fail closed on anything unforeseen
        return _unknown(snapshot, now_utc, ["evaluation_error:" + type(exc).__name__])
    problems = _evidence_problems(snap, now)
    if problems:
        return _unknown(snapshot, now_utc, problems)
    try:
        items, notes = _evaluate(snap, now)
    except Exception as exc:
        return _unknown(snapshot, now_utc, ["evaluation_error:" + type(exc).__name__])

    scope = snap["evidence"]["scope"]
    if scope == "checkpoint_only":
        notes.append("request_completeness_not_evaluated")
    verdict = max((i["verdict"] for i in items), key=PRIORITY.__getitem__, default="idle_ok")
    top = [i for i in items if i["verdict"] == verdict]
    reasons = sorted({r for i in top for r in i["reasons"]} | set(notes)) or ["no_open_work"]
    targets = sorted({i["target"] for i in top}) if verdict in ("dispatch", "decide") else []
    refs = [] if verdict == "idle_ok" else \
        sorted({_canon(r): r for i in top for r in i["work_refs"]}.values(), key=_canon)
    decision = {"schema": DECISION_SCHEMA, "agent": snap["agent"], "now_utc": now_utc,
                "scope": scope, "verdict": verdict, "reasons": reasons,
                "escalation": "operator" if verdict == "unknown" else None,
                "target": targets[0] if len(targets) == 1 else None, "targets": targets,
                "work_refs": refs, "items": items, "authority": "none"}
    decision["action_key"] = _key({"schema": DECISION_SCHEMA, "agent": snap["agent"],
                                   "scope": scope, "verdict": verdict, "targets": targets,
                                   "reasons": reasons, "work_refs": refs})
    return decision


# --- CLI (strict JSON in, strict JSON out; no file, env or bridge access) ----------------

def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_constant(name: str) -> Any:
    raise ValueError("non-strict JSON constant " + name)


_BASE64_RE = re.compile(r"^(?:[A-Za-z0-9+/]{4})+$|^(?:[A-Za-z0-9+/]{4})*"
                        r"(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)$")


def _decode_snapshot_base64(text: str) -> str:
    """Strict standard base64 (padded, canonical, no whitespace) of UTF-8 JSON text.

    Windows PowerShell 5.1 strips embedded quotes from native argv, so the
    adapter passes the snapshot as quote-free base64 instead of raw JSON.
    """
    if not _BASE64_RE.match(text):
        raise _Invalid("snapshot_base64_invalid")
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise _Invalid("snapshot_base64_invalid") from None
    if base64.b64encode(raw).decode("ascii") != text:
        raise _Invalid("snapshot_base64_invalid")  # non-canonical padding bits
    try:
        decoded = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise _Invalid("snapshot_utf8_invalid") from None
    if decoded.startswith("\ufeff"):
        raise _Invalid("snapshot_utf8_invalid")
    return decoded


def main(argv: list[str] | None = None, stdout: Any = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--snapshot-json")
    source.add_argument("--snapshot-base64")
    parser.add_argument("--now-utc", required=True)
    args = parser.parse_args(argv)
    out = stdout if stdout is not None else sys.stdout
    try:
        text = args.snapshot_json if args.snapshot_base64 is None else \
            _decode_snapshot_base64(args.snapshot_base64)
        snapshot = json.loads(text, object_pairs_hook=_no_duplicate_keys,
                              parse_constant=_reject_constant)
    except _Invalid as exc:
        decision = _unknown(None, args.now_utc, [exc.code])
    except ValueError:
        decision = _unknown(None, args.now_utc, ["snapshot_json_invalid"])
    else:
        decision = evaluate(snapshot, args.now_utc)
    out.write(json.dumps(decision, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
