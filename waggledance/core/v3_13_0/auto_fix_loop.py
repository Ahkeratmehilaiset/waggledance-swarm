# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""AutoFixLoop v1 -- event consumer + repair proposer.

Sprint 1 wave 3 generalization of the operator's existing
_auto_fix_loop.py / _auto_fix_state / _auto_fix_tasks pattern
(catalog entry CC-05 mapped to ARCH-013).

Per spec, AutoFixLoop is an EVENT CONSUMER + REPAIR PROPOSER --
not a hidden private control loop. All authoritative inputs and
outputs are MAGMA events + ToolDescriptor state + ContractCatalog
failures.

Boundaries:
* PROPOSES repairs. Never auto-applies WRT-003 (external_effect)
  without WriteRCOGate + scope policy.
* AUTO-APPLIES WRT-001 (internal_memory) when MemoryWriteProxy role
  permits.
* AUTO-APPLIES WRT-002 (local_artifact) when:
  - RecoveryCapsule.rollback_command exists
  - Idempotency check passes
  - LatencyBudget.shadow_eval_budget_s respected
* WRT-003 always routes through peer RCO + scope policy.
* REFUSES repair in DOM-015, DOM-021, PER-01..04 (operator-only).

Concurrency:
* Single-instance daemon per profile via MAGMA lease event.
* Stale lease (default 60 min, ProfileConfig override) can be
  claimed by another instance.

Design spec:
iterations/anchor_use_case/sprint_1/claude_lane/auto_fix_loop_runtime_spec.md
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional


# --------------------------------------------------------------------------
# Repair states + classes
# --------------------------------------------------------------------------


class RepairOutcome(str, Enum):
    PROPOSED = "proposed"
    APPLIED = "applied"
    DENIED = "denied"
    FAILED = "failed"
    REFUSED_SENSITIVE_DOMAIN = "refused_sensitive_domain"
    REFUSED_NO_CAPSULE = "refused_no_capsule"
    REFUSED_IDEMPOTENT_REPLAY = "refused_idempotent_replay"


SENSITIVE_DOMAINS = frozenset({"DOM-015", "DOM-021"})
OPERATOR_ONLY_PREFIX = "PER-"


# --------------------------------------------------------------------------
# RepairIntent dataclass
# --------------------------------------------------------------------------


@dataclass
class RepairIntent:
    """A proposed repair. Equivalent to write_rco_gate.Intent for the
    AutoFixLoop lane, but carries the triggering event + capsule refs."""

    repair_id: str
    triggering_event_id: str
    target_tool_id: str
    target_state_refs: list[str]
    repair_command: str
    risk_class: str                          # WriteRiskClass value
    idempotency_key: str
    rollback_plan_ref: str
    proposed_by: str = "auto_fix_loop_v1"
    proposal_ts_utc: str = ""
    contract_failures_referenced: list[str] = field(default_factory=list)


@dataclass
class RepairResult:
    """Outcome of one repair attempt."""

    repair_id: str
    triggering_event_id: str
    outcome: str                             # RepairOutcome value
    reason: str = ""
    audit_event_refs: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Hook-supplied views
# --------------------------------------------------------------------------


@dataclass
class TriggerEvent:
    """A MAGMA event surfaced as actionable by the filter."""

    event_id: str
    event_type: str                          # e.g. contract_failure,
                                              # divergence_drift_alert
    target_tool_id: str
    target_state_refs: list[str]
    target_domain: str                       # DOM-* ref
    contract_failures: list[str] = field(default_factory=list)
    ts_utc: str = ""


@dataclass
class RecoveryCapsuleView:
    """Subset of SCH-004 RecoveryCapsule AutoFixLoop reads."""

    capsule_id: str
    target_tool_id: str
    rollback_command: Optional[str]
    rebuild_command: Optional[str]
    known_corruption_modes: list[str] = field(default_factory=list)


@dataclass
class LeaseRecord:
    """Active-instance lease record (mirrored from MAGMA)."""

    instance_id: str
    acquired_at_utc: str
    last_renewed_at_utc: str


# --------------------------------------------------------------------------
# Exceptions
# --------------------------------------------------------------------------


class LeaseNotHeld(Exception):
    """Raised when an operation requires the active lease but caller
    does not hold it."""


# --------------------------------------------------------------------------
# AutoFixLoop itself
# --------------------------------------------------------------------------


@dataclass
class AutoFixLoop:
    """Consume MAGMA events; propose / apply repairs through WriteRCOGate.

    Pluggable hooks; v1 wires mock implementations in tests.
    """

    instance_id: str
    profile_config_ref: str

    # --- hooks the caller injects --------------------------------------------

    fetch_lease: Callable[[], Optional[LeaseRecord]]
    """Return current lease record (or None if no lease held)."""

    write_lease: Callable[[LeaseRecord], str]
    """Persist a lease record; return audit event ID."""

    fetch_actionable_events: Callable[[str], list[TriggerEvent]]
    """Given a cursor, return new actionable events since that cursor."""

    fetch_recovery_capsule: Callable[[str], Optional[RecoveryCapsuleView]]
    """Resolve target_tool_id -> capsule or None."""

    classify_intent: Callable[[RepairIntent], str]
    """Classify intent -> WriteRiskClass value (informational /
    internal_memory / local_artifact / external_effect)."""

    route_intent_through_gate: Callable[[RepairIntent], dict]
    """Pass intent through WriteRCOGate; return {approved: bool,
    reason: str, audit_event_id: str}."""

    execute_repair: Callable[[RepairIntent], dict]
    """Apply an approved repair; return {success: bool, error: str}."""

    emit_magma_event: Callable[[dict], str]

    idempotency_check: Callable[[RepairIntent], bool]
    """Return True if the repair is a duplicate (already applied for
    same idempotency_key recently)."""

    clock_fn: Callable[[], float]
    """Monotonic clock for stale-lease detection."""

    utc_iso_fn: Callable[[], str]
    """UTC timestamp generator (testable)."""

    # --- gate config ---------------------------------------------------------

    lease_ttl_seconds: int = 3600            # 60 min default per spec
    dry_run: bool = False

    # =========================================================================
    # LEASE
    # =========================================================================

    def acquire_lease(self) -> LeaseRecord:
        """Acquire the active-instance lease.

        If a non-stale lease is held by another instance, raises
        LeaseNotHeld. A stale lease (per Codex RCO round-2 fix: held
        for longer than lease_ttl_seconds since last_renewed_at_utc)
        is takeover-eligible: this instance claims it AND emits a
        stale-lease audit event so the takeover is auditable.
        """
        existing = self.fetch_lease()
        now = self.utc_iso_fn()
        if existing is not None and not existing.instance_id:
            existing = None
        if existing is not None and existing.instance_id != self.instance_id:
            is_stale = self._lease_is_stale(existing, now)
            if not is_stale:
                raise LeaseNotHeld(
                    f"lease held by {existing.instance_id}; "
                    f"refusing to acquire"
                )
            # Stale takeover -- emit dedicated audit event before
            # writing the new lease, so MAGMA records the
            # superseded instance.
            self.emit_magma_event({
                "event_type": "auto_fix_loop.lease_stale_takeover",
                "instance_id": self.instance_id,
                "superseded_instance_id": existing.instance_id,
                "superseded_last_renewed_at_utc": existing.last_renewed_at_utc,
                "lease_ttl_seconds": self.lease_ttl_seconds,
                "ts_utc": now,
            })
        record = LeaseRecord(
            instance_id=self.instance_id,
            acquired_at_utc=now,
            last_renewed_at_utc=now,
        )
        audit_ref = self.write_lease(record)
        self.emit_magma_event({
            "event_type": "auto_fix_loop.lease_acquired",
            "instance_id": self.instance_id,
            "ts_utc": now,
            "audit_event_id": audit_ref,
        })
        return record

    def _lease_is_stale(self, existing: LeaseRecord, now_utc: str) -> bool:
        """True if existing.last_renewed_at_utc is older than now_utc
        by more than lease_ttl_seconds.

        Both timestamps are ISO-8601 UTC strings (the utc_iso_fn hook
        format). Parses defensively: malformed timestamps are treated
        as NOT stale so a corrupted row cannot be silently taken over.
        """
        from datetime import datetime as _dt
        try:
            then = _dt.fromisoformat(
                existing.last_renewed_at_utc.replace("Z", "+00:00")
            )
            now = _dt.fromisoformat(now_utc.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return False
        elapsed = (now - then).total_seconds()
        return elapsed > self.lease_ttl_seconds

    def release_lease(self) -> None:
        """Release the active-instance lease."""
        existing = self.fetch_lease()
        if existing is None or existing.instance_id != self.instance_id:
            raise LeaseNotHeld(
                "release_lease called but lease is not held by this instance"
            )
        now = self.utc_iso_fn()
        # Write a release tombstone (instance_id=""). acquire_lease treats
        # it as no active lease even if the persistence layer keeps the row.
        self.write_lease(LeaseRecord(instance_id="",
                                       acquired_at_utc=existing.acquired_at_utc,
                                       last_renewed_at_utc=now))
        self.emit_magma_event({
            "event_type": "auto_fix_loop.lease_released",
            "instance_id": self.instance_id,
            "ts_utc": now,
        })

    # =========================================================================
    # RUN ONCE
    # =========================================================================

    def run_once(self, *, cursor: str) -> tuple[list[RepairResult], str]:
        """Process actionable events from the cursor.

        Returns (results, new_cursor). Caller is responsible for
        persisting the new cursor between runs (idempotency-friendly).
        """
        existing = self.fetch_lease()
        if (existing is None
                or existing.instance_id != self.instance_id):
            raise LeaseNotHeld(
                "run_once requires the active lease; call acquire_lease first"
            )

        events = self.fetch_actionable_events(cursor)
        results: list[RepairResult] = []
        new_cursor = cursor
        for ev in events:
            try:
                result = self._handle_event(ev)
            except Exception as exc:
                repair_id = str(uuid.uuid4())
                audit_ref = self.emit_magma_event({
                    "event_type": "auto_fix_loop.repair_failed",
                    "instance_id": self.instance_id,
                    "repair_id": repair_id,
                    "triggering_event_id": ev.event_id,
                    "reason": "handler_exception",
                    "exception_type": exc.__class__.__name__,
                    "error": str(exc)[:200],
                    "ts_utc": self.utc_iso_fn(),
                })
                result = RepairResult(
                    repair_id=repair_id,
                    triggering_event_id=ev.event_id,
                    outcome=RepairOutcome.FAILED.value,
                    reason=f"handler_exception:{exc.__class__.__name__}",
                    audit_event_refs=[audit_ref],
                )
            results.append(result)
            # Codex RCO round-2 fix: cursor_before MUST track the
            # current running cursor, not the original cursor passed
            # into run_once. Otherwise multi-event runs emit
            # [(start, A), (start, B), (start, C)] instead of
            # [(start, A), (A, B), (B, C)] and downstream audit
            # consumers cannot reconstruct the per-event chain.
            cursor_before = new_cursor
            new_cursor = ev.event_id
            self.emit_magma_event({
                "event_type": "auto_fix_loop.cursor_advanced",
                "instance_id": self.instance_id,
                "cursor_before": cursor_before,
                "cursor_after": new_cursor,
                "ts_utc": self.utc_iso_fn(),
            })
        return results, new_cursor

    # =========================================================================
    # INTERNAL HELPERS
    # =========================================================================

    def _handle_event(self, event: TriggerEvent) -> RepairResult:
        repair_id = str(uuid.uuid4())
        # Sensitive-domain / operator-only refusal
        if (event.target_domain in SENSITIVE_DOMAINS
                or event.target_domain.startswith(OPERATOR_ONLY_PREFIX)):
            audit_ref = self.emit_magma_event({
                "event_type": "auto_fix_loop.repair_denied",
                "instance_id": self.instance_id,
                "repair_id": repair_id,
                "triggering_event_id": event.event_id,
                "reason": "sensitive_domain",
                "target_domain": event.target_domain,
                "ts_utc": self.utc_iso_fn(),
            })
            return RepairResult(
                repair_id=repair_id,
                triggering_event_id=event.event_id,
                outcome=RepairOutcome.REFUSED_SENSITIVE_DOMAIN.value,
                reason=f"target_domain {event.target_domain} is sensitive",
                audit_event_refs=[audit_ref],
            )

        capsule = self.fetch_recovery_capsule(event.target_tool_id)
        if capsule is None or not capsule.rollback_command:
            audit_ref = self.emit_magma_event({
                "event_type": "auto_fix_loop.repair_denied",
                "instance_id": self.instance_id,
                "repair_id": repair_id,
                "triggering_event_id": event.event_id,
                "reason": "no_capsule_or_rollback_command",
                "ts_utc": self.utc_iso_fn(),
            })
            return RepairResult(
                repair_id=repair_id,
                triggering_event_id=event.event_id,
                outcome=RepairOutcome.REFUSED_NO_CAPSULE.value,
                reason="missing recovery capsule rollback_command",
                audit_event_refs=[audit_ref],
            )

        intent = RepairIntent(
            repair_id=repair_id,
            triggering_event_id=event.event_id,
            target_tool_id=event.target_tool_id,
            target_state_refs=list(event.target_state_refs),
            repair_command=capsule.rebuild_command or capsule.rollback_command,
            risk_class="",      # filled by classify_intent below
            idempotency_key=f"{event.target_tool_id}:{event.event_id}",
            rollback_plan_ref=capsule.capsule_id,
            proposal_ts_utc=self.utc_iso_fn(),
            contract_failures_referenced=list(event.contract_failures),
        )
        intent.risk_class = self.classify_intent(intent)

        if self.idempotency_check(intent):
            audit_ref = self.emit_magma_event({
                "event_type": "auto_fix_loop.repair_denied",
                "instance_id": self.instance_id,
                "repair_id": repair_id,
                "triggering_event_id": event.event_id,
                "reason": "idempotent_replay",
                "idempotency_key": intent.idempotency_key,
                "ts_utc": self.utc_iso_fn(),
            })
            return RepairResult(
                repair_id=repair_id,
                triggering_event_id=event.event_id,
                outcome=RepairOutcome.REFUSED_IDEMPOTENT_REPLAY.value,
                reason="repair already applied recently",
                audit_event_refs=[audit_ref],
            )

        # Emit repair_proposed audit event regardless of execution path
        proposed_audit = self.emit_magma_event({
            "event_type": "auto_fix_loop.repair_proposed",
            "instance_id": self.instance_id,
            "repair_id": repair_id,
            "triggering_event_id": event.event_id,
            "risk_class": intent.risk_class,
            "target_tool_id": event.target_tool_id,
            "target_state_refs": list(event.target_state_refs),
            "ts_utc": self.utc_iso_fn(),
        })

        # Dry-run mode: NEVER execute, just record proposal
        if self.dry_run:
            return RepairResult(
                repair_id=repair_id,
                triggering_event_id=event.event_id,
                outcome=RepairOutcome.PROPOSED.value,
                reason="dry_run; not applied",
                audit_event_refs=[proposed_audit],
            )

        # Route through WriteRCOGate. WRT-003 will block here unless
        # peer RCO + scope policy approve.
        gate_result = self.route_intent_through_gate(intent)
        if not gate_result.get("approved"):
            audit_ref = self.emit_magma_event({
                "event_type": "auto_fix_loop.repair_denied",
                "instance_id": self.instance_id,
                "repair_id": repair_id,
                "triggering_event_id": event.event_id,
                "reason": gate_result.get("reason", "gate_denied"),
                "risk_class": intent.risk_class,
                "ts_utc": self.utc_iso_fn(),
            })
            return RepairResult(
                repair_id=repair_id,
                triggering_event_id=event.event_id,
                outcome=RepairOutcome.DENIED.value,
                reason=gate_result.get("reason", "gate_denied"),
                audit_event_refs=[proposed_audit, audit_ref],
            )

        exec_result = self.execute_repair(intent)
        if exec_result.get("success"):
            audit_ref = self.emit_magma_event({
                "event_type": "auto_fix_loop.repair_applied",
                "instance_id": self.instance_id,
                "repair_id": repair_id,
                "triggering_event_id": event.event_id,
                "risk_class": intent.risk_class,
                "ts_utc": self.utc_iso_fn(),
            })
            return RepairResult(
                repair_id=repair_id,
                triggering_event_id=event.event_id,
                outcome=RepairOutcome.APPLIED.value,
                audit_event_refs=[proposed_audit, audit_ref],
            )
        audit_ref = self.emit_magma_event({
            "event_type": "auto_fix_loop.repair_failed",
            "instance_id": self.instance_id,
            "repair_id": repair_id,
            "triggering_event_id": event.event_id,
            "error": exec_result.get("error", "unknown"),
            "ts_utc": self.utc_iso_fn(),
        })
        return RepairResult(
            repair_id=repair_id,
            triggering_event_id=event.event_id,
            outcome=RepairOutcome.FAILED.value,
            reason=exec_result.get("error", "unknown"),
            audit_event_refs=[proposed_audit, audit_ref],
        )


__all__ = [
    "RepairOutcome",
    "SENSITIVE_DOMAINS",
    "OPERATOR_ONLY_PREFIX",
    "RepairIntent",
    "RepairResult",
    "TriggerEvent",
    "RecoveryCapsuleView",
    "LeaseRecord",
    "LeaseNotHeld",
    "AutoFixLoop",
]
