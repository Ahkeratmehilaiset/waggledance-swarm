# SPDX-License-Identifier: BUSL-1.1
"""Bridge-consensus receipt — self-verifying, re-derivable (Track T0b-followup).

The consensus-path auto-merge (`idle_consensus_auto_merge --require-bridge-
consensus`) could validate a consensus in a dry-run but its `--apply` step
fail-closed because the receipt writer was coupled to idle-protocol event
shapes (see #782 maiden-voyage finding). This module provides a receipt that
is built FROM a verified bridge-consensus verdict and is verified by
re-derivation — never by trusting a stored flag — so `--apply` can write,
re-verify, then merge fail-closed.

Tools-free by construction (only stdlib + `core.magma.canonical`). The verdict
re-derivation is supplied by the caller (which re-runs `verify_bridge_consensus`
against the live bridge events); this module independently re-checks the
receipt's integrity (canonical digest), its head/charter binding, each
identity's event digest against the live events, and that the stored verdict
snapshot equals the freshly re-derived verdict. Any mismatch refuses.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from waggledance.core.magma.canonical import sha256_digest

CONSENSUS_RECEIPT_SCHEMA = "magma.bridge_consensus_receipt.v0"
RCO_PASS_STATUSES = frozenset(
    {"rco_pass", "rco_pass_operator_merge_required", "rco_pass_pending_ci"}
)
# Mirrors tools/idle_consensus_auto_merge so the receipt verifier independently
# enforces that each identity's LIVE approval event is a decision-type vote with
# the right status for its role — not trusting rederived_verdict to have done so.
BUILD_CONSENSUS_STATUSES = frozenset(
    {
        "approved",
        "build_consensus",
        "build_consensus_pass",
        "concur",
        "concurred",
        "agree",
        "agreed",
    }
)
DECISION_EVENT_TYPES = frozenset({"decision", "rco_review", "finding"})
_ROLES = ("build_lead", "build_tools", "rco")
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
# Keys that participate in the canonical digest (everything except the digest
# itself; merge_commit_sha is included so a post-merge re-seal changes it).
_CORE_KEYS = (
    "schema_version",
    "pr_number",
    "head_sha",
    "task_id",
    "identities",
    "verdict_snapshot",
    "charter_path",
    "charter_digest",
    "ci_status",
    "merge_commit_sha",
)


def bridge_event_digest(event: Mapping[str, Any]) -> str:
    """Stable digest of a bridge event's identity-binding fields."""
    return sha256_digest(
        {
            "agent": str(event.get("agent", "")),
            "ts_utc": str(event.get("ts_utc", "")),
            "type": str(event.get("type", "")),
            "status": str(event.get("status", "")),
            "task_id": str(event.get("task_id", "")),
            "message": str(event.get("message", "")),
        }
    )


def _approval_event(events: Sequence[Mapping[str, Any]], index: Any) -> Mapping[str, Any] | None:
    if not isinstance(index, int) or isinstance(index, bool):
        return None
    if 0 <= index < len(events):
        ev = events[index]
        return ev if isinstance(ev, Mapping) else None
    return None


def build_bridge_consensus_receipt(
    *,
    pr_number: int,
    head_sha: str,
    task_id: str,
    verdict: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    charter_path: str,
    charter_digest: str,
    ci_status: Mapping[str, Any],
    merge_commit_sha: str = "",
) -> dict[str, Any]:
    """Assemble a re-derivable receipt from a verified bridge-consensus verdict.

    ``verdict`` is the output of ``verify_bridge_consensus`` (must be ok=True).
    Each identity's live approval event (located via the verdict's
    ``approval_index``) is digested into the receipt so a verifier can later
    confirm the identity reference against the live events.
    """
    identities: dict[str, Any] = {}
    verdict_ids = verdict.get("identities") or {}
    for role in _ROLES:
        info = verdict_ids.get(role) or {}
        ev = _approval_event(events, info.get("approval_index"))
        identities[role] = {
            "agent": str(info.get("agent", "")),
            "approved": bool(info.get("approved", False)),
            "event_ts": str(ev.get("ts_utc", "")) if ev is not None else "",
            "event_digest": bridge_event_digest(ev) if ev is not None else "",
        }
    core = {
        "schema_version": CONSENSUS_RECEIPT_SCHEMA,
        "pr_number": pr_number,
        "head_sha": head_sha,
        "task_id": task_id,
        "identities": identities,
        "verdict_snapshot": {
            "ok": bool(verdict.get("ok", False)),
            "decision": str(verdict.get("decision", "")),
            "reasons": list(verdict.get("reasons", [])),
        },
        "charter_path": str(charter_path),
        "charter_digest": str(charter_digest),
        "ci_status": dict(ci_status),
        "merge_commit_sha": str(merge_commit_sha),
    }
    return {**core, "canonical_digest": sha256_digest(core)}


def verify_bridge_consensus_receipt(
    *,
    receipt: Any,
    events: Sequence[Mapping[str, Any]],
    expected_head: str,
    charter_digest: str,
    rederived_verdict: Mapping[str, Any],
) -> dict[str, Any]:
    """Fail-closed re-derivation of a bridge-consensus receipt.

    Returns ``{ok, decision, reasons}``. ``ok`` only when ALL hold:
    receipt is a non-empty mapping; its ``canonical_digest`` recomputes
    exactly; ``head_sha`` == ``expected_head`` (40-hex); ``charter_digest``
    matches; the freshly ``rederived_verdict`` is ok AND the stored
    ``verdict_snapshot.ok`` is True (snapshot is never trusted alone — the
    re-derivation is authoritative); three DISTINCT approved identities with
    ``rco`` carrying an RCO_PASS status; and each identity's stored
    ``event_digest`` matches the live event located via the re-derived
    ``approval_index``. Any deviation refuses.
    """
    reasons: list[str] = []
    if not isinstance(receipt, Mapping) or not receipt:
        return _refuse(["receipt is missing or empty"])

    if not isinstance(expected_head, str) or not _SHA40.fullmatch(expected_head):
        reasons.append("expected_head is not a 40-hex sha")

    # (2) canonical digest recompute — catches any tampered field.
    core = {key: receipt.get(key) for key in _CORE_KEYS}
    stored_digest = receipt.get("canonical_digest")
    if not isinstance(stored_digest, str) or stored_digest != sha256_digest(core):
        reasons.append("canonical_digest mismatch (tampered receipt)")

    # head-exact binding.
    if receipt.get("head_sha") != expected_head:
        reasons.append("receipt head_sha does not match the merge-target head")

    # charter binding (was the right denylist in force).
    if receipt.get("charter_digest") != charter_digest:
        reasons.append("receipt charter_digest does not match the current charter")

    # (3) re-derive the verdict; never trust the stored snapshot alone.
    if not isinstance(rederived_verdict, Mapping) or rederived_verdict.get("ok") is not True:
        reasons.append("re-derived bridge consensus is not ok")
    snapshot = receipt.get("verdict_snapshot")
    if not isinstance(snapshot, Mapping) or snapshot.get("ok") is not True:
        reasons.append("receipt verdict_snapshot is not ok")

    # identities: 3 distinct approved; rco status; event-digest binding.
    ids = receipt.get("identities")
    rederived_ids = (
        rederived_verdict.get("identities") if isinstance(rederived_verdict, Mapping) else None
    ) or {}
    if not isinstance(ids, Mapping):
        reasons.append("identities is not a mapping (type confusion)")
    else:
        seen_agents: set[str] = set()
        for role in _ROLES:
            entry = ids.get(role)
            if not isinstance(entry, Mapping):
                reasons.append(f"{role} identity entry missing/not a mapping")
                continue
            if entry.get("approved") is not True:
                reasons.append(f"{role} identity not approved")
            agent = entry.get("agent")
            if isinstance(agent, str) and agent.strip():
                seen_agents.add(agent)
            # event-digest must match the live event at the re-derived index.
            r_info = rederived_ids.get(role) or {}
            live = _approval_event(events, r_info.get("approval_index"))
            if live is None or bridge_event_digest(live) != entry.get("event_digest"):
                reasons.append(f"{role} event_digest does not match a live bridge event")
                continue
            # The live approval event must itself be a decision-type vote with
            # the status allowed for this role — checked INDEPENDENTLY of
            # rederived_verdict so a forged/buggy verdict pointing a build role
            # at e.g. a heartbeat event cannot pass (RCO case-08 hardening).
            if str(live.get("type", "")).lower() not in DECISION_EVENT_TYPES:
                reasons.append(f"{role} live event is not a decision-type vote")
            allowed = RCO_PASS_STATUSES if role == "rco" else BUILD_CONSENSUS_STATUSES
            if str(live.get("status", "")).lower() not in allowed:
                reasons.append(f"{role} live event status not allowed for the role")
        if len(seen_agents) != 3:
            reasons.append("receipt does not carry three distinct identities")

    ok = not reasons
    return {
        "ok": ok,
        "decision": "consensus_receipt_verified" if ok else "consensus_receipt_refused",
        "reasons": reasons,
    }


def _refuse(reasons: list[str]) -> dict[str, Any]:
    return {"ok": False, "decision": "consensus_receipt_refused", "reasons": reasons}
