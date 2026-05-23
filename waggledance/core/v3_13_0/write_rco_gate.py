# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""WriteRCOGate v1 -- single choke point for all v3.13.0 writes.

Classifies every write intent into a risk class (WRT-001/002/003)
and routes it through the matching gate policy. Emits an audit
envelope event chain through MAGMA (intent_classified, peer_rco_*,
scope_policy_decided, intent_approved, effect_started,
effect_completed, denied, rollback_*).

This is the v1 design-spec implementation: pure-Python data shapes
and routing logic without external integrations yet. Code paths
that call into AuthenticatedConnector / MemoryWriteProxy / MAGMA
emit are stubbed with hooks the next implementation PRs fill in.

Design spec:
iterations/anchor_use_case/sprint_1/claude_lane/write_rco_gate_v1_spec.md
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Callable, Optional

from waggledance.core.magma.canonical import sha256_digest

from .solver_provenance import VerificationResult


# --------------------------------------------------------------------------
# Risk classes (per ANTI-* + WRT-001/002/003 in v3.13.0 reconciled catalog)
# --------------------------------------------------------------------------


class WriteRiskClass(str, Enum):
    """Four risk tiers; classify decides which gate path."""

    INFORMATIONAL = "informational"
    INTERNAL_MEMORY = "internal_memory"
    LOCAL_ARTIFACT = "local_artifact"
    EXTERNAL_EFFECT = "external_effect"


# --------------------------------------------------------------------------
# Audit event types
# --------------------------------------------------------------------------


class AuditEventType(str, Enum):
    """Twelve audit event types. Flow through MAGMA AuditLog, not bridge."""

    INTENT_CLASSIFIED = "write.intent_classified"
    PEER_RCO_REQUESTED = "write.peer_rco_requested"
    PEER_RCO_COMPLETED = "write.peer_rco_completed"
    SCOPE_POLICY_DECIDED = "write.scope_policy_decided"
    INTENT_APPROVED = "write.intent_approved"
    EFFECT_STARTED = "write.effect_started"
    EFFECT_COMPLETED = "write.effect_completed"
    EFFECT_FAILED = "write.effect_failed"
    EFFECT_OUTCOME_UNKNOWN = "write.effect_outcome_unknown"
    DENIED = "write.denied"
    ROLLBACK_STARTED = "write.rollback_started"
    ROLLBACK_COMPLETED = "write.rollback_completed"


# --------------------------------------------------------------------------
# Stop conditions
# --------------------------------------------------------------------------


class StopCondition(str, Enum):
    """Stop-conditions that pause-and-escalate to operator.

    v1 actively routes 5: PEER_RCO_TIMEOUT, PEER_RCO_NONCONVERGENT,
    AUDIT_WRITE_FAILED, ANTI_PATTERN_VIOLATION, NO_ROLLBACK_PLAN.
    The remaining 3 (OPERATOR_UNAVAILABLE, COST_CEILING_REACHED,
    IDEMPOTENCY_CAP_REACHED) are reserved enum values for future
    runtime wiring (cost telemetry, idempotency replay, operator
    presence) -- not yet active in v1 routing logic.
    """

    PEER_RCO_TIMEOUT = "peer_rco_timeout"
    PEER_RCO_NONCONVERGENT = "peer_rco_nonconvergent"
    AUDIT_WRITE_FAILED = "audit_write_failed"
    OPERATOR_UNAVAILABLE = "operator_unavailable"          # reserved (v1.x)
    ANTI_PATTERN_VIOLATION = "anti_pattern_violation"
    NO_ROLLBACK_PLAN = "no_rollback_plan"
    COST_CEILING_REACHED = "cost_ceiling_reached"          # reserved (v1.x)
    IDEMPOTENCY_CAP_REACHED = "idempotency_cap_reached"    # reserved (v1.x)


# --------------------------------------------------------------------------
# Intent + Outcome data shapes
# --------------------------------------------------------------------------


def _canonical_payload_hash(payload: dict) -> str:
    payload_canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload_canonical.encode("utf-8")).hexdigest()


@dataclass
class Intent:
    """A proposed write. Constructed by a tool or solver."""

    intent_id: str
    agent_id: str
    session_id: str
    tool_descriptor_id: str
    connector_ref: Optional[str]            # AuthenticatedConnector.connector_id
    target_state_ref: str                    # StateHandle.state_id
    action: str                              # "insert" | "update" | "delete"
                                              # | "post" | "patch" | "put"
                                              # | "append"
    payload: dict                            # the proposed write body
    payload_hash: str                        # sha256 hex of canonical payload
    provenance_chain: str = ""               # parent intent_id for retry / rollback
    proposed_at_utc: str = field(default_factory=lambda: _utc_iso())

    @classmethod
    def construct(cls, *, agent_id: str, session_id: str,
                  tool_descriptor_id: str, target_state_ref: str,
                  action: str, payload: dict,
                  connector_ref: Optional[str] = None,
                  provenance_chain: str = "") -> "Intent":
        intent_id = str(uuid.uuid4())
        payload_hash = _canonical_payload_hash(payload)
        return cls(
            intent_id=intent_id,
            agent_id=agent_id,
            session_id=session_id,
            tool_descriptor_id=tool_descriptor_id,
            connector_ref=connector_ref,
            target_state_ref=target_state_ref,
            action=action,
            payload=payload,
            payload_hash=payload_hash,
            provenance_chain=provenance_chain,
        )


@dataclass
class GateOutcome:
    """Outcome of routing an intent through the gate."""

    intent_id: str
    risk_class: WriteRiskClass
    payload_hash: str
    intent_fingerprint: str
    approved: bool
    denial_reason: Optional[str] = None
    stop_condition: Optional[StopCondition] = None
    audit_event_ids: list[str] = field(default_factory=list)
    diff_preview_uri: Optional[str] = None
    rollback_plan_ref: Optional[str] = None
    rco_decision_artifact: Optional[dict[str, Any]] = None
    rco_decision_digest: Optional[str] = None


@dataclass
class ExecutionResult:
    """Outcome of executing an approved intent."""

    intent_id: str
    success: bool
    outcome_unknown: bool = False           # e.g. external network timed out
    error_reason: Optional[str] = None
    elapsed_ms: int = 0
    rollback_executed: bool = False


def _intent_approval_fingerprint(intent: Intent) -> str:
    """Hash the immutable write-authority fields approved by route()."""
    return sha256_digest({
        "intent_id": intent.intent_id,
        "agent_id": intent.agent_id,
        "session_id": intent.session_id,
        "tool_descriptor_id": intent.tool_descriptor_id,
        "connector_ref": intent.connector_ref or "",
        "target_state_ref": intent.target_state_ref,
        "action": intent.action,
        "payload_hash": intent.payload_hash,
        "provenance_chain": intent.provenance_chain,
    })


def _new_gate_outcome(
    intent: Intent,
    *,
    risk_class: WriteRiskClass,
    approved: bool,
    denial_reason: Optional[str] = None,
    stop_condition: Optional[StopCondition] = None,
    audit_event_ids: Optional[list[str]] = None,
    diff_preview_uri: Optional[str] = None,
    rollback_plan_ref: Optional[str] = None,
) -> GateOutcome:
    return GateOutcome(
        intent_id=intent.intent_id,
        risk_class=risk_class,
        payload_hash=intent.payload_hash,
        intent_fingerprint=_intent_approval_fingerprint(intent),
        approved=approved,
        denial_reason=denial_reason,
        stop_condition=stop_condition,
        audit_event_ids=list(audit_event_ids or []),
        diff_preview_uri=diff_preview_uri,
        rollback_plan_ref=rollback_plan_ref,
    )


# --------------------------------------------------------------------------
# Connector / state info -- minimal shapes the gate reads
# --------------------------------------------------------------------------


@dataclass
class ConnectorInfo:
    """The subset of AuthenticatedConnector fields the gate consults."""

    connector_id: str
    write_risk: WriteRiskClass               # informational / local_artifact
                                              # / external_effect
    auth_mode: str
    can_run_headless: bool
    rate_limit_max_workers: int
    rate_limit_request_delay_s: float


@dataclass
class StateInfo:
    """The subset of StateHandle fields the gate consults."""

    state_id: str
    plane: str                               # magma_history / control_state
                                              # / retrieval_data / filesystem_artifact
                                              # / informational_artifact
                                              # / browser_profile / external_readonly
                                              # / external_system / audit_projection
    write_modes_allowed: list[str]
    sensitive_class: str
    single_writer_required: bool


@dataclass
class RecoveryCapsuleInfo:
    """Minimal RecoveryCapsule view the gate reads."""

    capsule_id: str
    rollback_command: Optional[str]
    known_corruption_modes: list[str]


# --------------------------------------------------------------------------
# Stop-condition exception
# --------------------------------------------------------------------------


class GateStopCondition(Exception):
    """Raised when a stop-condition fires; gate escalates to operator."""

    def __init__(self, intent_id: str, condition: StopCondition,
                 reason: str = ""):
        super().__init__(f"stop_condition={condition.value} reason={reason}")
        self.intent_id = intent_id
        self.condition = condition
        self.reason = reason


# --------------------------------------------------------------------------
# The gate itself
# --------------------------------------------------------------------------


@dataclass
class WriteRCOGate:
    """Single choke point for v3.13.0 writes.

    Pluggable hooks for the integration points -- in v1 these are
    callables; Sprint 1 PR #2+ replaces them with real
    AuthenticatedConnector / MemoryWriteProxy / MAGMA / scope-policy
    integrations.
    """

    # --- hooks the caller injects --------------------------------------------

    audit_emit: Callable[[dict], str]
    """Emit an audit event; returns audit_event_id."""

    classify_payload_credential_scan: Callable[[dict], list[str]]
    """Return list of credential-pattern hits in payload; empty if clean.

    Used for WRT-002 gate-level credential-pattern scan (Codex RCO edit #4)."""

    fetch_connector_info: Callable[[str], Optional[ConnectorInfo]]
    """Resolve connector_ref -> ConnectorInfo or None."""

    fetch_state_info: Callable[[str], Optional[StateInfo]]
    """Resolve target_state_ref -> StateInfo or None."""

    fetch_recovery_capsule: Callable[[str], Optional[RecoveryCapsuleInfo]]
    """Resolve tool_descriptor_id -> RecoveryCapsuleInfo or None."""

    peer_rco_solicit: Callable[[Intent], "PeerRCOResult"]
    """Request peer RCO; returns a PeerRCOResult (pass / changes / blocked
    / timeout). Default impl is synchronous-stub for v1; real bridge call
    in next iteration."""

    operator_scope_policy_check: Callable[[Intent, "ConnectorInfo",
                                            "StateInfo"], "ScopePolicyResult"]
    """Resolve whether the intent is in operator's pre-approved scope."""

    write_executor: Callable[[Intent], ExecutionResult]
    """Actually perform the write. v1 is a stub; production wires to
    AuthenticatedConnector / MemoryWriteProxy / filesystem."""

    emit_receipt_bundle: Optional[Callable[[dict[str, Any]], None]] = None
    """Optional fail-closed MAGMA receipt sink for route decisions."""

    resolve_external_effect_approval_id: Optional[
        Callable[[Intent, GateOutcome], Optional[str]]
    ] = None
    """Resolve the explicit approval id required for external-effect receipts."""

    verify_solver_provenance: Optional[Callable[[str], VerificationResult]] = None
    """Optional SCH-005 SolverCandidateManifest provenance verifier.

    Required fail-closed when a WRT-003 intent carries solver_candidate_id.
    Plain operator/tool writes without a solver candidate keep the existing
    peer-RCO + scope-policy gate path.
    """

    # --- gate config ----------------------------------------------------------

    peer_rco_timeout_seconds: int = 1800     # 30 min
    peer_rco_max_rounds: int = 3
    wrt_002_idempotency_cap: int = 3
    cost_ceiling_per_decision: float = 0.05  # USD; placeholder
    _last_emitted_receipt: Optional[dict[str, Any]] = field(
        default=None,
        init=False,
        repr=False,
    )

    # =========================================================================
    # PUBLIC API
    # =========================================================================

    def classify(self, intent: Intent) -> WriteRiskClass:
        """Decide which risk class the intent belongs to.

        Per write_rco_gate_v1_spec.md Section "Risk classification rules"
        and Codex RCO edit #2: WRT-003 routes via
        AuthenticatedConnector.write_risk OR non-readonly external_system
        plane (NOT external_readonly + write_modes_allowed -- those are
        mutually exclusive per Band A schema allOf-constraint).
        """
        state = self.fetch_state_info(intent.target_state_ref)
        connector = self.fetch_connector_info(intent.connector_ref) if intent.connector_ref else None

        # Rule 1: WRT-003 external_effect
        if connector and connector.write_risk == WriteRiskClass.EXTERNAL_EFFECT:
            return WriteRiskClass.EXTERNAL_EFFECT
        if state and state.plane == "external_system":
            return WriteRiskClass.EXTERNAL_EFFECT
        if intent.action in ("post", "patch", "put") and state and \
                state.plane not in ("magma_history", "control_state",
                                     "retrieval_data", "filesystem_artifact",
                                     "informational_artifact",
                                     "audit_projection"):
            return WriteRiskClass.EXTERNAL_EFFECT

        # Rule 2: operator advisory artifact with informational write risk
        if state and state.plane == "informational_artifact":
            return WriteRiskClass.INFORMATIONAL

        # Rule 3: WRT-002 local_artifact
        if state and state.plane == "filesystem_artifact":
            return WriteRiskClass.LOCAL_ARTIFACT
        if state and state.plane == "retrieval_data":
            return WriteRiskClass.LOCAL_ARTIFACT

        # Rule 4: WRT-001 internal_memory
        if state and state.plane in ("magma_history", "control_state",
                                      "audit_projection"):
            return WriteRiskClass.INTERNAL_MEMORY

        # Conservative default: classify as external_effect when ambiguous
        # rather than falsely permit
        return WriteRiskClass.EXTERNAL_EFFECT

    def route(self, intent: Intent) -> GateOutcome:
        """Classify, emit intent_classified, and dispatch by risk class.

        Returns GateOutcome with approved / denied / stop-condition
        details.
        """
        risk = self.classify(intent)
        audit_id = self._audit(AuditEventType.INTENT_CLASSIFIED, intent, {
            "risk_class": risk.value,
            "action": intent.action,
            "target_state_ref": intent.target_state_ref,
        })

        try:
            if risk == WriteRiskClass.INFORMATIONAL:
                outcome = self._route_informational(intent, audit_id)
            elif risk == WriteRiskClass.INTERNAL_MEMORY:
                outcome = self._route_internal_memory(intent, audit_id)
            elif risk == WriteRiskClass.LOCAL_ARTIFACT:
                outcome = self._route_local_artifact(intent, audit_id)
            elif risk == WriteRiskClass.EXTERNAL_EFFECT:
                outcome = self._route_external_effect(intent, audit_id)
            else:
                raise RuntimeError(f"unhandled risk class: {risk}")
            if not outcome.approved and outcome.stop_condition is None:
                denied_audit = self._audit(AuditEventType.DENIED, intent, {
                    "reason": outcome.denial_reason or "denied",
                    "risk_class": risk.value,
                })
                outcome.audit_event_ids.append(denied_audit)
            self._attach_rco_decision_artifact(intent, outcome)
            self._emit_route_receipt_bundle(intent, outcome)
            return outcome
        except GateStopCondition as stop:
            denied_audit = self._audit(AuditEventType.DENIED, intent, {
                "stop_condition": stop.condition.value,
                "reason": stop.reason,
            })
            outcome = _new_gate_outcome(
                intent,
                risk_class=risk,
                approved=False,
                denial_reason=stop.reason,
                stop_condition=stop.condition,
                audit_event_ids=[audit_id, denied_audit],
            )
            self._attach_rco_decision_artifact(intent, outcome)
            return outcome

    def execute(self, intent: Intent, outcome: GateOutcome) -> ExecutionResult:
        """Execute an approved intent. Caller checks outcome.approved first."""
        if not outcome.approved:
            return ExecutionResult(intent_id=intent.intent_id, success=False,
                                    error_reason="gate did not approve intent")
        binding_error = self._execution_binding_error(intent, outcome)
        if binding_error:
            return ExecutionResult(
                intent_id=intent.intent_id,
                success=False,
                error_reason=binding_error,
            )

        try:
            self._audit(AuditEventType.EFFECT_STARTED, intent, {})
        except GateStopCondition as stop:
            return _audit_failure_execution_result(intent, stop)
        t_start = time.perf_counter()
        try:
            result = self.write_executor(intent)
        except Exception as exc:  # pragma: no cover -- caller wraps real exec
            result = ExecutionResult(
                intent_id=intent.intent_id,
                success=False,
                error_reason=str(exc),
                elapsed_ms=int((time.perf_counter() - t_start) * 1000),
            )

        if result.outcome_unknown:
            try:
                self._audit(AuditEventType.EFFECT_OUTCOME_UNKNOWN, intent, {
                    "error": result.error_reason or "",
                })
            except GateStopCondition as stop:
                return _audit_failure_execution_result(
                    intent, stop, elapsed_ms=result.elapsed_ms
                )
            return result
        if not result.success:
            try:
                self._audit(AuditEventType.EFFECT_FAILED, intent, {
                    "error": result.error_reason or "",
                })
            except GateStopCondition as stop:
                return _audit_failure_execution_result(
                    intent, stop, elapsed_ms=result.elapsed_ms
                )
            return result
        try:
            self._audit(AuditEventType.EFFECT_COMPLETED, intent, {
                "elapsed_ms": result.elapsed_ms,
            })
        except GateStopCondition as stop:
            return _audit_failure_execution_result(
                intent, stop, elapsed_ms=result.elapsed_ms
            )
        return result

    # =========================================================================
    # EXECUTION BINDING
    # =========================================================================

    def _execution_binding_error(
        self,
        intent: Intent,
        outcome: GateOutcome,
    ) -> Optional[str]:
        if outcome.intent_id != intent.intent_id:
            return (
                "gate outcome intent_id does not match execute intent_id: "
                f"{outcome.intent_id} != {intent.intent_id}"
            )

        try:
            current_payload_hash = _canonical_payload_hash(intent.payload)
        except Exception as exc:
            return f"intent payload_hash recompute failed: {exc}"
        if current_payload_hash != intent.payload_hash:
            return "intent payload_hash does not match current payload"
        if outcome.payload_hash != intent.payload_hash:
            return "gate outcome payload_hash does not match intent payload_hash"

        expected_fingerprint = _intent_approval_fingerprint(intent)
        if outcome.intent_fingerprint != expected_fingerprint:
            return (
                "gate outcome intent_fingerprint does not match current "
                "execute intent"
            )

        try:
            current_risk = self.classify(intent)
        except Exception as exc:
            return f"execution risk reclassification failed: {exc}"
        if outcome.risk_class != current_risk:
            return (
                "gate outcome risk_class does not match current "
                f"classification: {outcome.risk_class.value} != "
                f"{current_risk.value}"
            )

        return None

    # =========================================================================
    # PRIVATE ROUTING
    # =========================================================================

    def _route_informational(self, intent: Intent, classify_audit_id: str) -> GateOutcome:
        cred_hits = self.classify_payload_credential_scan(intent.payload)
        if cred_hits:
            raise GateStopCondition(
                intent.intent_id,
                StopCondition.ANTI_PATTERN_VIOLATION,
                f"ANTI-004 credential pattern detected: {cred_hits[:3]}",
            )

        state = self.fetch_state_info(intent.target_state_ref)
        if state is None:
            raise GateStopCondition(
                intent.intent_id,
                StopCondition.ANTI_PATTERN_VIOLATION,
                "target_state_ref unresolved",
            )
        if intent.action not in (state.write_modes_allowed or []):
            raise GateStopCondition(
                intent.intent_id,
                StopCondition.ANTI_PATTERN_VIOLATION,
                f"action '{intent.action}' not in state.write_modes_allowed="
                f"{state.write_modes_allowed!r}",
            )

        approved_audit = self._audit(AuditEventType.INTENT_APPROVED, intent,
                                      {"risk_class": "informational",
                                       "target_plane": state.plane})
        return _new_gate_outcome(
            intent,
            risk_class=WriteRiskClass.INFORMATIONAL,
            approved=True,
            audit_event_ids=[classify_audit_id, approved_audit],
        )

    def _route_internal_memory(self, intent: Intent, classify_audit_id: str) -> GateOutcome:
        # ANTI-007: original layer immutability -- caller must enforce role
        # check at MemoryWriteProxy level. Gate emits the audit envelope.
        approved_audit = self._audit(AuditEventType.INTENT_APPROVED, intent,
                                      {"risk_class": "internal_memory"})
        return _new_gate_outcome(
            intent,
            risk_class=WriteRiskClass.INTERNAL_MEMORY,
            approved=True,
            audit_event_ids=[classify_audit_id, approved_audit],
        )

    def _route_local_artifact(self, intent: Intent, classify_audit_id: str) -> GateOutcome:
        # Gate-level credential pattern scan (Codex RCO edit #4)
        cred_hits = self.classify_payload_credential_scan(intent.payload)
        if cred_hits:
            raise GateStopCondition(
                intent.intent_id,
                StopCondition.ANTI_PATTERN_VIOLATION,
                f"ANTI-004 credential pattern detected: {cred_hits[:3]}",
            )

        # Path scope check via fetch_state_info
        state = self.fetch_state_info(intent.target_state_ref)
        if state is None:
            raise GateStopCondition(
                intent.intent_id,
                StopCondition.ANTI_PATTERN_VIOLATION,
                "target_state_ref unresolved",
            )

        # write_modes_allowed enforcement (Codex RCO round-2 fix #2):
        # intent.action must be present in state.write_modes_allowed.
        # Fail-closed: empty list -> deny all.
        if intent.action not in (state.write_modes_allowed or []):
            raise GateStopCondition(
                intent.intent_id,
                StopCondition.ANTI_PATTERN_VIOLATION,
                f"action '{intent.action}' not in state.write_modes_allowed="
                f"{state.write_modes_allowed!r}",
            )

        approved_audit = self._audit(AuditEventType.INTENT_APPROVED, intent,
                                      {"risk_class": "local_artifact",
                                       "target_plane": state.plane})
        return _new_gate_outcome(
            intent,
            risk_class=WriteRiskClass.LOCAL_ARTIFACT,
            approved=True,
            audit_event_ids=[classify_audit_id, approved_audit],
        )

    def _route_external_effect(self, intent: Intent, classify_audit_id: str) -> GateOutcome:
        # Check 1: connector exists + write_risk matches
        connector = self.fetch_connector_info(intent.connector_ref) if intent.connector_ref else None
        if connector is None:
            raise GateStopCondition(
                intent.intent_id,
                StopCondition.ANTI_PATTERN_VIOLATION,
                "WRT-003 requires connector_ref",
            )
        if connector.write_risk != WriteRiskClass.EXTERNAL_EFFECT:
            raise GateStopCondition(
                intent.intent_id,
                StopCondition.ANTI_PATTERN_VIOLATION,
                "connector write_risk does not match WRT-003 classification",
            )

        # Check 2: state must resolve (Codex RCO round-2 fix #1).
        # WRT-003 cannot approve against an unresolved StateInfo; an
        # unknown target plane makes write_modes_allowed and scope
        # policy uncheckable -> deny fail-closed.
        state = self.fetch_state_info(intent.target_state_ref)
        if state is None:
            raise GateStopCondition(
                intent.intent_id,
                StopCondition.ANTI_PATTERN_VIOLATION,
                "WRT-003 requires resolved target_state_ref",
            )

        # Check 3: write_modes_allowed enforcement (Codex RCO round-2
        # fix #2). intent.action must be present in
        # state.write_modes_allowed. Fail-closed on empty list.
        if intent.action not in (state.write_modes_allowed or []):
            raise GateStopCondition(
                intent.intent_id,
                StopCondition.ANTI_PATTERN_VIOLATION,
                f"action '{intent.action}' not in state.write_modes_allowed="
                f"{state.write_modes_allowed!r}",
            )

        # Check 4: solver provenance for solver-driven WRT-003 writes.
        # If a solver_candidate_id is present, the standalone
        # SolverProvenance module must verify valid before the gate can
        # continue. Missing hook or invalid/quarantined/revoked provenance
        # fails closed.
        solver_candidate_id = _solver_candidate_id(intent)
        solver_provenance_result = None
        if solver_candidate_id:
            if self.verify_solver_provenance is None:
                raise GateStopCondition(
                    intent.intent_id,
                    StopCondition.ANTI_PATTERN_VIOLATION,
                    "WRT-003 solver write requires verify_solver_provenance hook",
                )
            solver_provenance_result = self.verify_solver_provenance(
                solver_candidate_id
            )
            if not solver_provenance_result.valid:
                reasons = ",".join(solver_provenance_result.reasons) or "invalid"
                raise GateStopCondition(
                    intent.intent_id,
                    StopCondition.ANTI_PATTERN_VIOLATION,
                    "WRT-003 solver provenance invalid for "
                    f"{solver_candidate_id}: {reasons}",
                )

        # Check 5: rollback plan exists (RecoveryCapsule)
        capsule = self.fetch_recovery_capsule(intent.tool_descriptor_id)
        if capsule is None or not capsule.rollback_command:
            raise GateStopCondition(
                intent.intent_id,
                StopCondition.NO_ROLLBACK_PLAN,
                "WRT-003 requires RecoveryCapsule with rollback_command",
            )

        # Check 6: peer RCO
        peer_audit = self._audit(AuditEventType.PEER_RCO_REQUESTED, intent, {})
        peer_result = self.peer_rco_solicit(intent)
        self._audit(AuditEventType.PEER_RCO_COMPLETED, intent, {
            "verdict": peer_result.verdict,
            "rounds": peer_result.rounds,
        })
        if peer_result.timed_out:
            raise GateStopCondition(
                intent.intent_id,
                StopCondition.PEER_RCO_TIMEOUT,
                "peer RCO 30 min hard stop",
            )
        if peer_result.verdict != "pass":
            if peer_result.rounds >= self.peer_rco_max_rounds:
                raise GateStopCondition(
                    intent.intent_id,
                    StopCondition.PEER_RCO_NONCONVERGENT,
                    f"peer RCO {peer_result.rounds} rounds without convergence",
                )
            return _new_gate_outcome(
                intent,
                risk_class=WriteRiskClass.EXTERNAL_EFFECT,
                approved=False,
                denial_reason="peer RCO did not pass",
                audit_event_ids=[classify_audit_id, peer_audit],
            )

        # Check 7: operator scope policy
        scope_result = self.operator_scope_policy_check(intent, connector, state)
        self._audit(AuditEventType.SCOPE_POLICY_DECIDED, intent, {
            "decision": scope_result.decision,
        })
        if scope_result.decision == "denied":
            return _new_gate_outcome(
                intent,
                risk_class=WriteRiskClass.EXTERNAL_EFFECT,
                approved=False,
                denial_reason=f"operator scope policy denied: {scope_result.reason}",
                audit_event_ids=[classify_audit_id],
            )

        # All checks pass -- emit intent_approved
        approved_detail = {
            "risk_class": "external_effect",
            "scope_policy": scope_result.decision,
            "rollback_plan_ref": capsule.capsule_id,
        }
        if solver_candidate_id and solver_provenance_result:
            approved_detail.update({
                "solver_candidate_id": solver_candidate_id,
                "solver_manifest_sha256": (
                    solver_provenance_result.manifest_sha256_observed
                ),
                "solver_activation_state": (
                    solver_provenance_result.activation_state
                ),
            })
        approved_audit = self._audit(
            AuditEventType.INTENT_APPROVED, intent, approved_detail
        )
        return _new_gate_outcome(
            intent,
            risk_class=WriteRiskClass.EXTERNAL_EFFECT,
            approved=True,
            audit_event_ids=[classify_audit_id, approved_audit],
            rollback_plan_ref=capsule.capsule_id,
        )

    # =========================================================================
    # INTERNAL HELPERS
    # =========================================================================

    def _audit(self, event_type: AuditEventType, intent: Intent,
                extra: dict) -> str:
        """Emit audit event via the hook; return audit_event_id."""
        envelope = {
            "event_type": event_type.value,
            "intent_id": intent.intent_id,
            "agent_id": intent.agent_id,
            "session_id": intent.session_id,
            "tool_descriptor_id": intent.tool_descriptor_id,
            "connector_ref": intent.connector_ref,
            "target_state_ref": intent.target_state_ref,
            "action": intent.action,
            "payload_hash": intent.payload_hash,
            "provenance_chain": intent.provenance_chain,
            "ts_utc": _utc_iso(),
            **extra,
        }
        try:
            return self.audit_emit(envelope)
        except Exception as exc:
            # Audit failure denies the write regardless of risk class.
            raise GateStopCondition(
                intent.intent_id,
                StopCondition.AUDIT_WRITE_FAILED,
                f"audit emit failed: {exc}",
            )

    def _attach_rco_decision_artifact(
        self,
        intent: Intent,
        outcome: GateOutcome,
    ) -> None:
        """Bind every route outcome to a payload-free RCO decision artifact."""
        try:
            artifact = build_rco_decision_artifact_for_gate(intent, outcome)
        except Exception as exc:
            raise GateStopCondition(
                intent.intent_id,
                StopCondition.AUDIT_WRITE_FAILED,
                f"RCO decision artifact build failed: {exc}",
            ) from exc
        outcome.rco_decision_artifact = artifact
        outcome.rco_decision_digest = sha256_digest(artifact)

    def _emit_route_receipt_bundle(
        self,
        intent: Intent,
        outcome: GateOutcome,
    ) -> None:
        """Emit an opt-in receipt bundle for a completed route decision."""
        if self.emit_receipt_bundle is None:
            return
        approval_id = None
        if outcome.risk_class == WriteRiskClass.EXTERNAL_EFFECT:
            if self.resolve_external_effect_approval_id is None:
                raise GateStopCondition(
                    intent.intent_id,
                    StopCondition.AUDIT_WRITE_FAILED,
                    "external_effect WriteRCOGate receipt requires "
                    "resolve_external_effect_approval_id",
                )
            approval_id = self.resolve_external_effect_approval_id(intent, outcome)
            if not approval_id:
                raise GateStopCondition(
                    intent.intent_id,
                    StopCondition.AUDIT_WRITE_FAILED,
                    "external_effect WriteRCOGate receipt requires approval_id",
                )
        try:
            bundle = build_rco_decision_receipt_for_gate(
                intent,
                outcome,
                approval_id=approval_id,
                previous_receipt=self._last_emitted_receipt,
            )
            self.emit_receipt_bundle(bundle)
            self._last_emitted_receipt = bundle["receipt"]
        except GateStopCondition:
            raise
        except Exception as exc:
            raise GateStopCondition(
                intent.intent_id,
                StopCondition.AUDIT_WRITE_FAILED,
                f"receipt emit failed: {exc}",
            ) from exc


# --------------------------------------------------------------------------
# Helper result types for hook return values
# --------------------------------------------------------------------------


@dataclass
class PeerRCOResult:
    """Outcome of a peer RCO solicitation."""

    verdict: str                              # "pass" | "changes_requested"
                                              # | "blocked"
    rounds: int
    timed_out: bool = False
    notes: str = ""


@dataclass
class ScopePolicyResult:
    """Outcome of an operator scope policy check."""

    decision: str                             # "auto_approved" |
                                              # "operator_confirmed" |
                                              # "denied"
    reason: str = ""


# --------------------------------------------------------------------------
# Utility
# --------------------------------------------------------------------------


def _utc_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _solver_candidate_id(intent: Intent) -> str:
    """Return a solver candidate id if the intent declares one."""
    for key in ("solver_candidate_id", "candidate_id"):
        value = intent.payload.get(key)
        if isinstance(value, str) and value:
            return value
    if intent.provenance_chain.startswith("solver:"):
        return intent.provenance_chain
    return ""


def build_gate_decision_card(
    intent: Intent,
    outcome: GateOutcome,
) -> dict[str, Any]:
    """Build a payload-free operator/UI summary for a gate decision.

    The write gate may see sensitive payloads. This card intentionally echoes
    only identifiers, hashes, risk class, audit refs, and the next action needed
    to unblock or execute the intent.
    """
    return {
        "schema_version": "write_gate_decision_card.v1",
        "intent_id": outcome.intent_id,
        "agent_id": intent.agent_id,
        "session_id": intent.session_id,
        "tool_descriptor_id": intent.tool_descriptor_id,
        "connector_ref": intent.connector_ref or "",
        "target_state_ref": intent.target_state_ref,
        "action": intent.action,
        "payload_hash": intent.payload_hash,
        "risk_class": outcome.risk_class.value,
        "approved": outcome.approved,
        "operator_status": _decision_operator_status(outcome),
        "denial_reason": outcome.denial_reason or "",
        "stop_condition": (
            outcome.stop_condition.value if outcome.stop_condition else ""
        ),
        "required_action": _decision_required_action(outcome),
        "audit_event_ids": list(outcome.audit_event_ids),
        "diff_preview_uri": outcome.diff_preview_uri or "",
        "rollback_plan_ref": outcome.rollback_plan_ref or "",
    }


def build_rco_decision_artifact_for_gate(
    intent: Intent,
    outcome: GateOutcome,
    *,
    ts_utc: str | None = None,
    policy_version: str = "policy:write_rco_gate:v1",
    charter_version: str = "charter:v1",
    scope_policy_decision: str = "not_applicable",
    peer_rco_verdict: str = "not_requested",
) -> dict[str, Any]:
    """Build a payload-free MAGMA RCO decision artifact for a gate outcome.

    This is an opt-in adapter. It does not emit MAGMA events, write receipt
    bundles, or change ``WriteRCOGate.route`` behavior.
    """
    from waggledance.core.magma.rco_decision_artifact import (
        build_rco_decision_artifact as _build_rco_artifact,
    )

    gate_decision = _artifact_gate_decision(outcome)
    return _build_rco_artifact(
        decision_id=f"rco:decision:{_safe_ref(intent.intent_id)}",
        ts_utc=ts_utc or _utc_iso(),
        intent=_artifact_intent_summary(intent),
        write_payload=intent.payload,
        risk_class=outcome.risk_class.value,
        gate_decision=gate_decision,
        approved=outcome.approved,
        operator_required=outcome.risk_class == WriteRiskClass.EXTERNAL_EFFECT,
        policy_version=policy_version,
        charter_version=charter_version,
        scope_policy_decision=scope_policy_decision,
        peer_rco_verdict=peer_rco_verdict,
        verifier_path=["write_rco_gate_v1", "rco_decision_artifact_v0"],
        reason_codes=_artifact_reason_codes(outcome, gate_decision),
        audit_event_ids=[_safe_ref(f"audit:{event_id}") for event_id in outcome.audit_event_ids],
        stop_condition=outcome.stop_condition.value if outcome.stop_condition else None,
    )


def build_rco_decision_receipt_for_gate(
    intent: Intent,
    outcome: GateOutcome,
    *,
    ts_utc: str | None = None,
    approval_id: str | None = None,
    policy_digest: str | None = None,
    charter_digest: str | None = None,
    world_snapshot_digest: str | None = None,
    solver_contract_digest: str | None = None,
    previous_receipt: dict[str, Any] | None = None,
    domain_threshold_version: str = "threshold:write_rco_gate:v1",
) -> dict[str, Any]:
    """Build payload-free RCO decision, EvaluationResult, and receipt objects.

    This helper does not write files or execute the intent. It gives callers a
    receipt-ready bundle for the already-computed gate route. External effects
    still require an explicit approval_id because MAGMA receipt v1 is
    operator-gated for that risk tier.
    """
    from waggledance.core.magma.evaluation_result import build_evaluation_result
    from waggledance.core.magma.receipt import build_magma_receipt

    if outcome.risk_class == WriteRiskClass.EXTERNAL_EFFECT and not approval_id:
        raise ValueError("external_effect WriteRCOGate receipt requires approval_id")

    rco_decision = outcome.rco_decision_artifact or build_rco_decision_artifact_for_gate(
        intent,
        outcome,
        ts_utc=ts_utc,
    )
    intent_payload = _artifact_intent_summary(intent)
    gate_decision = str(rco_decision["gate_decision"])
    risk_class = outcome.risk_class.value
    evaluation = build_evaluation_result(
        case_id=f"case:write_rco_gate:{_safe_ref(intent.intent_id)}",
        subject_type="policy",
        target_payload=intent_payload,
        risk_class=risk_class,
        expected_gate=gate_decision,
        actual_gate=gate_decision,
        verifier_path=[
            "write_rco_gate_route",
            "rco_decision_artifact_v0",
            "magma_receipt_v1",
        ],
        solver_selection=[],
        policy_version=str(rco_decision["policy_version"]),
        charter_version=str(rco_decision["charter_version"]),
        domain_threshold_version=domain_threshold_version,
        verdict="pass" if outcome.approved else "review",
        reason_codes=list(rco_decision["reason_codes"]),
        confidence_score=0.95 if outcome.approved else 0.75,
        uncertainty_sources=[],
        allow_external_effect=(risk_class == WriteRiskClass.EXTERNAL_EFFECT.value),
    )
    receipt = build_magma_receipt(
        event_id=f"magma:write_rco_gate:{_safe_ref(intent.intent_id)}",
        ts_utc=ts_utc or _utc_iso(),
        risk_class=risk_class,
        payload=intent_payload,
        evaluation_result=evaluation,
        policy_digest=policy_digest or sha256_digest({
            "policy_version": rco_decision["policy_version"],
        }),
        charter_digest=charter_digest or sha256_digest({
            "charter_version": rco_decision["charter_version"],
        }),
        rco_decision_digest=sha256_digest(rco_decision),
        world_snapshot_digest=world_snapshot_digest or sha256_digest({
            "target_state_ref": intent.target_state_ref,
            "risk_class": risk_class,
        }),
        solver_contract_digest=solver_contract_digest or sha256_digest({
            "solver_candidate_id": _solver_candidate_id(intent),
        }),
        previous_receipt=previous_receipt,
        approval_id=approval_id,
        allow_external_effect=(risk_class == WriteRiskClass.EXTERNAL_EFFECT.value),
    )
    return {
        "payload": intent_payload,
        "rco_decision_artifact": rco_decision,
        "evaluation_result": evaluation,
        "receipt": receipt,
    }


def _artifact_intent_summary(intent: Intent) -> dict[str, Any]:
    return {
        "intent_id": intent.intent_id,
        "agent_id": intent.agent_id,
        "session_id": intent.session_id,
        "tool_descriptor_id": intent.tool_descriptor_id,
        "connector_ref": intent.connector_ref or "",
        "target_state_ref": intent.target_state_ref,
        "action": intent.action,
        "payload_hash": intent.payload_hash,
        "provenance_chain": intent.provenance_chain,
        "proposed_at_utc": intent.proposed_at_utc,
    }


def _artifact_gate_decision(outcome: GateOutcome) -> str:
    if outcome.approved:
        return "allow"
    if outcome.stop_condition is not None:
        return "review"
    return "refuse"


def _artifact_reason_codes(outcome: GateOutcome, gate_decision: str) -> list[str]:
    reason_codes = [
        f"rco:risk:{outcome.risk_class.value}",
        f"rco:gate:{gate_decision}",
    ]
    if outcome.approved:
        reason_codes.append("rco:approved")
    elif outcome.stop_condition is not None:
        reason_codes.append(f"rco:stop:{outcome.stop_condition.value}")
    else:
        reason_codes.append("rco:denied")
    return reason_codes


def _safe_ref(value: str) -> str:
    safe = "".join(
        char if char.isalnum() or char in ":._-" else "_"
        for char in str(value)
    ).strip("_")
    if not safe or not safe[0].isalpha():
        safe = f"ref:{safe or 'unknown'}"
    return safe[:180]


def _decision_operator_status(outcome: GateOutcome) -> str:
    if outcome.approved:
        return "approved"
    if outcome.stop_condition is not None:
        return "blocked_stop_condition"
    return "blocked_policy_denied"


def _decision_required_action(outcome: GateOutcome) -> str:
    if outcome.approved:
        if outcome.risk_class == WriteRiskClass.EXTERNAL_EFFECT:
            return "execute_with_rollback_plan"
        return "execute_or_record_effect"
    if outcome.stop_condition == StopCondition.NO_ROLLBACK_PLAN:
        return "attach_recovery_capsule"
    if outcome.stop_condition == StopCondition.AUDIT_WRITE_FAILED:
        return "restore_audit_log_before_retry"
    if outcome.stop_condition == StopCondition.PEER_RCO_TIMEOUT:
        return "rerun_peer_rco_or_pause"
    if outcome.stop_condition == StopCondition.PEER_RCO_NONCONVERGENT:
        return "resolve_peer_rco_findings"
    if outcome.stop_condition == StopCondition.ANTI_PATTERN_VIOLATION:
        return "fix_intent_or_scope"
    if outcome.stop_condition == StopCondition.OPERATOR_UNAVAILABLE:
        return "wait_for_operator"
    if outcome.stop_condition == StopCondition.COST_CEILING_REACHED:
        return "raise_or_reduce_cost_budget"
    if outcome.stop_condition == StopCondition.IDEMPOTENCY_CAP_REACHED:
        return "manual_reconciliation_required"
    reason = outcome.denial_reason or ""
    if reason.startswith("operator scope policy denied"):
        return "update_operator_scope_or_request_confirmation"
    if reason == "peer RCO did not pass":
        return "address_peer_rco_findings"
    return "review_denial_reason"


def _audit_failure_execution_result(
    intent: Intent,
    stop: GateStopCondition,
    *,
    elapsed_ms: int = 0,
) -> ExecutionResult:
    return ExecutionResult(
        intent_id=intent.intent_id,
        success=False,
        outcome_unknown=True,
        error_reason=f"audit failure: {stop.reason}",
        elapsed_ms=elapsed_ms,
    )


__all__ = [
    "WriteRiskClass",
    "AuditEventType",
    "StopCondition",
    "Intent",
    "GateOutcome",
    "ExecutionResult",
    "ConnectorInfo",
    "StateInfo",
    "RecoveryCapsuleInfo",
    "WriteRCOGate",
    "GateStopCondition",
    "PeerRCOResult",
    "ScopePolicyResult",
    "build_gate_decision_card",
    "build_rco_decision_artifact_for_gate",
    "build_rco_decision_receipt_for_gate",
]
