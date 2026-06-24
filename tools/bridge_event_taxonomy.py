#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Single shared bridge-event gate taxonomy (RFC P2/D5) — DORMANT / UNWIRED.

Implements the taxonomy + classifier defined by
``docs/architecture/BRIDGE_EVENT_GATE_TAXONOMY_V1.md`` (PR #1387). **Consulted by
nothing yet** — pure, testable logic. Once the spec invariant is operator-signed,
the gate consumers (check_bridge_changes_requested / check_rco_pass_present /
verify_bridge_consensus / the merge driver) migrate to call ``classify`` instead
of re-deriving authority from free-text status names / message bodies.

Authority comes from STRUCTURED FIELDS only:
  - ``type``             event type — authority types MIRROR the live gate exactly
                         (decision/rco_review/finding/blocked/test block-side,
                         decision/rco_review/finding/done clear-side); finding +
                         rco_review are RCO-gated; message/handoff/wake_request etc.
                         never carry authority
  - ``decision_status``  CLOSED enum; unknown -> FAIL-CLOSED (block); negation-aware
                         (exact match, not substring presence)
  - ``head_sha``         blocks/approvals bind to the head; a head-mismatched signal
                         AUTO-EXPIRES (content-changing re-push); a malformed block
                         with no head fails CLOSED (still blocks)
  - ``author_identity``  identity-bound signer (recognized-RCO set drives veto power)

FAIL-CLOSED throughout: unknown status, malformed block, or any ambiguity blocks;
never default-allow. The status NAME string and message body have ZERO authority.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

# --- Event types that carry gate authority -----------------------------------
# V1 MIRRORS the LIVE gate authority EXACTLY so a consumer migration is
# byte-identical with NO dropped veto (rco-1 reconciliation finding 2026-06-24:
# check_bridge_changes_requested.py @origin/main BLOCKING_EVENT_TYPES =
# {decision, rco_review, finding, blocked, test}; CLEAR types =
# {decision, rco_review, finding, done}).
#
# Reconciliation is EVIDENCE-BASED (rco-1 emit-audit over 65,440 shared-log events,
# 2026-06-25), not a blind copy — each live type that V1 does NOT carry forward is a
# DELIBERATE, documented tightening proven to only ADD/preserve blocks (never drop a
# real veto), per spec §2.1 / option-(b):
#   - blocked  -> PRESERVED (38 ACTIVE PR-task merge-vetoes since 2026-06-01, e.g.
#                 build_consensus_blocked_pending, merge_blocked_operator_or_driver,
#                 build_slot_blocked_tools_author). Dropping it would be a REAL
#                 loosening, so it stays veto-authoritative (block-by-type).
#   - rco_review-> DROPPED: 0 emit instances EVER; RCOs veto via finding/decision.
#                 Dead authority; safe no-op (RCO veto preserved via `finding`).
#   - test     -> DROPPED from authority (deliberate tightening): 1433 events, 1252
#                 CI/smoke clears + 18 block-vocab; CI noise must not be a bridge
#                 gate signal. SAFE direction (a dropped clear leaves blocks
#                 standing). The 18 block-instances are confirmed non-load-bearing
#                 before migration #3 ships (rco-1 audit).
#   - done     -> DROPPED from CLEAR authority (deliberate tightening): 199 clears;
#                 a dropped clear only leaves blocks standing (fail-closed-safe).
BLOCK_AUTHORITY_TYPES = frozenset({"decision", "finding", "blocked"})
CLEAR_AUTHORITY_TYPES = frozenset({"decision", "finding"})
# Veto/approve power for these types requires a RECOGNIZED-RCO identity (the
# absolute, per-identity RCO veto). Non-RCO events of these types are advisory.
RCO_GATED_TYPES = frozenset({"finding"})
# These types BLOCK by type alone (regardless of status text).
BLOCK_BY_TYPE = frozenset({"finding", "blocked"})
# Every OTHER type (message, handoff, wake_request, heartbeat, liveness, status,
# intent, claim, release, rco_review, test, done) carries NO veto/approve authority
# — a status NAME or message body on them is ignored. message/handoff/wake_request
# were never live authority (clarification); rco_review/test/done are the
# evidence-based deliberate tightenings documented above.

# --- decision_status CLOSED enum (exact-match classification) -----------------
APPROVE_STATUSES = frozenset({
    "build_consensus_pass", "rco_pass", "no_changes_requested", "operator_sign",
})
BLOCK_STATUSES = frozenset({
    "changes_requested", "finding", "veto", "hold",
})
# CLEAR retracts a prior BLOCK from the SAME identity.
CLEAR_STATUSES = frozenset({
    "changes_requested_resolved", "changes_requested_cleared", "block_retracted",
})
# Authoritative-but-non-verdict statuses (carry no block/approve weight).
NEUTRAL_STATUSES = frozenset({
    "received", "info", "concurrence", "acknowledged",
})

# Classification buckets.
APPROVE, BLOCK, CLEAR, NEUTRAL, UNKNOWN = "APPROVE", "BLOCK", "CLEAR", "NEUTRAL", "UNKNOWN"


@dataclass
class GateVerdict:
    clear_to_merge: bool
    decision: str               # 'clear' | 'blocked'
    blocking: list = field(default_factory=list)   # [(identity, status, head, why)]
    approvals: list = field(default_factory=list)  # [(identity, status)]
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "clear_to_merge": self.clear_to_merge,
            "decision": self.decision,
            "blocking": self.blocking,
            "approvals": self.approvals,
            "reason": self.reason,
        }


def _norm_event(e: dict) -> dict:
    """A normalized view; missing fields default to safe-blocking-friendly values."""
    return {
        "type": (e.get("type") or "").strip(),
        "decision_status": (e.get("decision_status") or "").strip(),
        "head_sha": (e.get("head_sha") or "").strip(),
        "author_identity": (e.get("author_identity") or "").strip(),
        "task_id": (e.get("task_id") or "").strip(),
        "ts_utc": (e.get("ts_utc") or "").strip(),
    }


def _is_authoritative(e: dict, recognized_rcos: frozenset) -> bool:
    """An event carries gate authority iff its type is in the live BLOCK/CLEAR
    authority sets, AND (for the RCO-gated types finding/rco_review) it is from a
    recognized-RCO identity. All other types/identities are advisory."""
    t = e["type"]
    if t not in (BLOCK_AUTHORITY_TYPES | CLEAR_AUTHORITY_TYPES):
        return False
    if t in RCO_GATED_TYPES:
        return e["author_identity"] in recognized_rcos
    return True


def _classify_status(e: dict) -> str:
    """Bucket an authoritative event by type + EXACT decision_status enum match.

    - ``finding`` / ``blocked``: BLOCK by type (veto/block regardless of status).
    - ``decision``: verdict-bearing, closed enum; a status in no set is UNKNOWN ->
      fail-closed (caller blocks). Negation-aware: ``block_not_resolved`` etc. are
      NOT in CLEAR (no substring match) -> UNKNOWN.
    (rco_review/test/done are not authoritative in V1 — see the type-set
    reconciliation note — so they never reach this function.)
    """
    if e["type"] in BLOCK_BY_TYPE:
        return BLOCK
    s = e["decision_status"]          # decision: the only status-driven type
    if s in APPROVE_STATUSES:
        return APPROVE
    if s in CLEAR_STATUSES:
        return CLEAR
    if s in BLOCK_STATUSES:
        return BLOCK
    if s in NEUTRAL_STATUSES:
        return NEUTRAL
    return UNKNOWN


def _block_applies(e: dict, head_sha: str) -> bool:
    """A BLOCK applies at the current head if its head matches, OR if it carries
    no head_sha (malformed -> fail-closed, conservatively still blocks). A BLOCK
    bound to a DIFFERENT head has auto-expired (content-changing re-push)."""
    h = e["head_sha"]
    if not h:
        return True            # malformed block -> fail-closed (applies)
    return h == head_sha


def _approve_applies(e: dict, head_sha: str) -> bool:
    """An APPROVE/CLEAR counts only when bound to the EXACT current head (a
    head-less or head-mismatched approval does NOT count -> fail-closed)."""
    return bool(e["head_sha"]) and e["head_sha"] == head_sha


def classify(
    events: Iterable[dict],
    *,
    task_id: str,
    head_sha: str,
    recognized_rcos: Sequence[str] = (),
) -> GateVerdict:
    """Single source of truth for the gate's block/approve verdict on ``task_id``
    at ``head_sha``. Pure; fail-closed. ``events`` are normalized dicts with keys
    {type, decision_status, head_sha, author_identity, task_id, ts_utc}.

    Algorithm: keep authoritative events for this task; take each identity's
    LATEST authoritative event (by ts_utc); apply head-binding (blocks auto-expire
    on head mismatch, malformed blocks fail closed, approvals must bind to head);
    an identity blocks if its applicable latest is BLOCK or UNKNOWN; veto outranks
    pass (any blocking identity -> blocked).
    """
    rcos = frozenset(recognized_rcos)
    norm = [_norm_event(e) for e in events]
    auth = [e for e in norm if e["task_id"] == task_id and _is_authoritative(e, rcos)]

    # per identity, the latest VERDICT-BEARING event (newest first, skipping
    # NEUTRAL): a later neutral/informational event must NOT clear a standing block.
    by_ident: dict[str, list] = {}
    for e in sorted(auth, key=lambda x: x["ts_utc"]):
        by_ident.setdefault(e["author_identity"], []).append(e)
    latest: dict[str, dict] = {}
    for ident, evs in by_ident.items():
        for e in reversed(evs):
            if _classify_status(e) != NEUTRAL:
                latest[ident] = e
                break

    blocking: list = []
    approvals: list = []
    for ident, e in latest.items():
        cls = _classify_status(e)
        if cls == BLOCK:
            if _block_applies(e, head_sha):
                blocking.append((ident, e["decision_status"] or e["type"],
                                 e["head_sha"], "authoritative block at head"))
            # else: head-mismatched block auto-expired -> no current verdict
        elif cls == UNKNOWN:
            # unknown authoritative status -> fail-closed (block), head-bound like a block
            if _block_applies(e, head_sha):
                blocking.append((ident, f"{e['decision_status']} (unknown->fail-closed)",
                                 e["head_sha"], "unrecognized decision_status"))
        elif cls in (APPROVE, CLEAR):
            if _approve_applies(e, head_sha):
                approvals.append((ident, e["decision_status"]))
            # head-mismatched/headless approval does not count
        # NEUTRAL: neither blocks nor approves

    if blocking:
        return GateVerdict(
            clear_to_merge=False, decision="blocked",
            blocking=blocking, approvals=approvals,
            reason=f"{len(blocking)} authoritative block(s) at head {head_sha[:12]}",
        )
    return GateVerdict(
        clear_to_merge=True, decision="clear",
        blocking=[], approvals=approvals,
        reason=f"no authoritative block at head {head_sha[:12]}",
    )


def normalize_raw(raw: dict) -> dict:
    """Best-effort map a RAW bridge event to the normalized taxonomy view. Reads
    ONLY structured fields (payload + top-level), never the status name or message
    body, for authority. Provided for the consumer-migration phase (#3); the
    classifier logic is tested directly on normalized events.

    MIGRATION CRUX (rco-1 #1388 forward note) — DO NOT wire this without it:
    today's bridge events carry FREE-TEXT status NAMES (e.g.
    ``build_consensus_pass_supersede``, ``rco1_..._at_head``) in the top-level
    ``status`` field and have NO ``payload.decision_status``. Fed through this
    fallback they classify to an UNKNOWN enum -> fail-CLOSED -> MASS OVER-BLOCK.
    That is the SAFE direction, but it means migration #3 is NOT byte-identical to
    the live gate until EITHER events start emitting a canonical
    ``payload.decision_status`` field, OR a vetted free-text->enum mapping is added
    here. The §5 conformance corpus MUST be seeded from current real verdicts WITH
    that mapping, or migration wedges the gate. Until then this is a forward-only
    helper, not a drop-in for the live free-text reads."""
    payload = raw.get("payload") or {}
    return {
        "type": raw.get("type"),
        # the canonical decision_status lives in payload.decision_status; fall back
        # to the top-level status field ONLY as the literal enum value (still not a
        # substring parse).
        "decision_status": payload.get("decision_status") or raw.get("status"),
        "head_sha": payload.get("head_sha") or payload.get("exact_head")
        or payload.get("head"),
        "author_identity": payload.get("author_uuid") or raw.get("agent_uuid")
        or raw.get("agent"),
        "task_id": raw.get("task_id") or raw.get("task"),
        "ts_utc": raw.get("ts_utc"),
    }
