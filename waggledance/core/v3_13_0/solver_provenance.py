# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""SolverProvenance v1 -- signing chain for solver activation.

When a solver candidate transitions Shadow -> Hybrid -> Autonomous,
the runtime needs a verifiable claim that:
  1) Two independent agents reviewed the candidate (peer RCO).
  2) The candidate matches a specific signed manifest (provenance).
  3) The signing chain is auditable end-to-end via MAGMA.

This module implements:
* ProvenanceSignature dataclass.
* sign(candidate_id, role, ...) -> ProvenanceSignature.
* verify_solver_provenance(candidate_id) -> VerificationResult.
* revoke(candidate_id, reason) -> RevocationResult.
* auto_quarantine helper for divergence-driven downgrades.

Per spec edit E13: signing_role is EXPLICIT in both bridge payload
AND the ProvenanceSignature manifest -- never inferred.

Per spec edit E15: auto-quarantine (reversible) and permanent revoke
(operator-driven) are distinct states.

Per spec edit E16: bridge events use existing type=handoff /
type=decision with payload.kind=solver. NO invented dotted bridge
types.

Design spec:
iterations/anchor_use_case/sprint_1/claude_lane/solver_rco_provenance_signing_spec.md
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from waggledance.core.magma.canonical import sha256_digest


SOLVER_PROVENANCE_POLICY_VERSION = "policy:solver_provenance:v1"
SOLVER_PROVENANCE_CHARTER_VERSION = "charter:v1"
SOLVER_PROVENANCE_DOMAIN_THRESHOLD_VERSION = "threshold:solver_provenance:v1"


# --------------------------------------------------------------------------
# Signing roles + activation states
# --------------------------------------------------------------------------


class SigningRole(str, Enum):
    OWNER = "owner"
    PEER = "peer"
    OPERATOR = "operator"


class ActivationState(str, Enum):
    UNACTIVATED = "unactivated"
    AWAITING_SIGNING = "awaiting_signing"
    SIGNED = "signed"
    ACTIVATED = "activated"
    QUARANTINED = "quarantined"            # reversible auto-state
    REVOKED = "revoked"                     # one-way, operator-driven


class RevocationActor(str, Enum):
    OPERATOR = "operator"
    OWNER_AGENT = "owner_agent"
    PEER_AGENT = "peer_agent"
    AUTOMATIC_DRIFT_DETECTION = "automatic_drift_detection"


# --------------------------------------------------------------------------
# ProvenanceSignature dataclass
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ProvenanceSignature:
    """One signing event in the provenance chain.

    Public-key-free integrity record pointing back to canonical manifest
    + MAGMA event IDs. Anyone with read access to MAGMA can verify.
    """

    signature_id: str
    solver_candidate_id: str
    solver_manifest_canonical_json: str
    manifest_sha256: str
    signing_agent_id: str
    signing_role: str                       # SigningRole value
    signing_timestamp_utc: str
    bridge_event_ref: str                   # rco_pass-equivalent event ID
    audit_event_ref: str                    # MAGMA event ID
    operator_scope_policy_ref: str          # policy under which valid


# --------------------------------------------------------------------------
# VerificationResult + RevocationResult
# --------------------------------------------------------------------------


@dataclass
class VerificationResult:
    """Outcome of verify_solver_provenance()."""

    valid: bool
    candidate_id: str
    reasons: list[str] = field(default_factory=list)
    activation_state: str = ActivationState.UNACTIVATED.value
    has_owner_signature: bool = False
    has_peer_signature: bool = False
    has_operator_signature: bool = False
    manifest_sha256_observed: Optional[str] = None
    quarantine_evidence_refs: list[str] = field(default_factory=list)


@dataclass
class RevocationResult:
    """Outcome of revoke()."""

    success: bool
    candidate_id: str
    new_state: str                          # ActivationState value
    audit_event_ref: Optional[str] = None
    reason: str = ""


# --------------------------------------------------------------------------
# SolverProvenance store -- in-memory v1; production wires to MAGMA
# --------------------------------------------------------------------------


@dataclass
class SolverCandidateRecord:
    """Minimal v1 record. Production wires to SCH-005 manifests."""

    candidate_id: str
    manifest_canonical_json: str
    manifest_sha256: str
    target_domain: str                      # DOM-* ref
    target_write_risk: str                  # informational/internal_memory
                                             # /local_artifact/external_effect
    activation_state: str = ActivationState.UNACTIVATED.value
    signatures: list[ProvenanceSignature] = field(default_factory=list)
    consecutive_divergent_runs: int = 0     # auto-quarantine counter
    quarantine_evidence_refs: list[str] = field(default_factory=list)
    revocation_audit_ref: Optional[str] = None
    last_signing_timestamp_utc: Optional[str] = None


# --------------------------------------------------------------------------
# Provenance manager
# --------------------------------------------------------------------------


@dataclass
class SolverProvenance:
    """Manage solver-RCO signing chain + verification + revocation.

    Pluggable hooks; v1 wires mock implementations in tests.
    """

    # --- hooks the caller injects --------------------------------------------

    fetch_candidate: Callable[[str], Optional[SolverCandidateRecord]]
    update_candidate: Callable[[SolverCandidateRecord], None]
    emit_magma_event: Callable[[dict], str]
    emit_bridge_event: Callable[[dict], None]
    """Emit a bridge envelope. Per spec edit E16: use existing
    type=handoff / type=decision with payload.kind=solver."""

    operator_scope_policy_active: Callable[[str], bool]
    """Resolve operator_scope_policy_ref -> True if still in force."""

    emit_receipt_bundle: Optional[Callable[[dict], None]] = None
    """Optional hook for MAGMA receipt-bound transition bundles.

    If configured, authority transitions build a payload-free
    RCO/EvaluationResult/receipt bundle before durable state changes, then
    emit it only after update_candidate succeeds. Hook failure propagates after
    the durable transition and does not advance the local receipt chain head.

    Successive emissions are chained via prev_receipt_hash so the sink
    receives a verifiable receipt chain (single-genesis per provenance
    instance). The chain head is reset when emit_receipt_bundle is None.
    """

    _last_emitted_receipt: Optional[dict] = field(default=None, repr=False)
    """Internal chain head for emit_receipt_bundle linkage. Not a config."""

    # --- gate config ---------------------------------------------------------

    quarantine_consecutive_threshold: int = 5
    quarantine_divergence_score_threshold: float = 0.40
    max_rco_rounds: int = 3
    sensitive_domains: tuple = ("DOM-015", "DOM-021")
    """Solvers targeting these domains require operator signature
    for activation; cannot graduate to autonomous."""

    # =========================================================================
    # PUBLIC API
    # =========================================================================

    def sign(self, *, candidate_id: str, signing_agent_id: str,
              signing_role: str,
              bridge_event_ref: str,
              operator_scope_policy_ref: str,
              round_number: int = 1) -> ProvenanceSignature:
        """Record a signature in the candidate's chain.

        Emits a MAGMA audit event AND a bridge handoff event with
        status=provenance_signed (per spec edit E16, existing bridge
        statuses + payload.kind=solver).

        Raises ValueError if signing_role is not a valid SigningRole
        value (per spec edit E13: explicit, never inferred).
        """
        if signing_role not in {r.value for r in SigningRole}:
            raise ValueError(
                f"signing_role must be one of "
                f"{[r.value for r in SigningRole]}; got {signing_role!r}"
            )
        if round_number > self.max_rco_rounds:
            raise ValueError(
                f"round_number {round_number} exceeds max "
                f"{self.max_rco_rounds}; escalate to operator"
            )

        candidate = self.fetch_candidate(candidate_id)
        if candidate is None:
            raise KeyError(f"unknown candidate: {candidate_id}")
        if candidate.activation_state == ActivationState.REVOKED.value:
            # Codex RCO round-2 fix #3: permanent revoke is one-way.
            # Reactivation requires a fresh candidate with a new
            # manifest_sha256, not re-signing the revoked one.
            raise PermissionError(
                f"candidate {candidate_id} is REVOKED; signing refused. "
                "Reactivation requires a fresh candidate with new "
                "manifest_sha256."
            )
        if not self.operator_scope_policy_active(operator_scope_policy_ref):
            raise PermissionError(
                f"operator scope policy {operator_scope_policy_ref} "
                f"not active; signing refused"
            )

        sig_id = str(uuid.uuid4())
        now = _utc_iso()

        # Emit MAGMA audit FIRST so the signature can carry the audit ref.
        audit_event_ref = self.emit_magma_event({
            "event_type": "solver.provenance_signed",
            "signature_id": sig_id,
            "solver_candidate_id": candidate_id,
            "signing_agent_id": signing_agent_id,
            "signing_role": signing_role,
            "manifest_sha256": candidate.manifest_sha256,
            "bridge_event_ref": bridge_event_ref,
            "operator_scope_policy_ref": operator_scope_policy_ref,
            "round_number": round_number,
            "ts_utc": now,
        })

        signature = ProvenanceSignature(
            signature_id=sig_id,
            solver_candidate_id=candidate_id,
            solver_manifest_canonical_json=candidate.manifest_canonical_json,
            manifest_sha256=candidate.manifest_sha256,
            signing_agent_id=signing_agent_id,
            signing_role=signing_role,
            signing_timestamp_utc=now,
            bridge_event_ref=bridge_event_ref,
            audit_event_ref=audit_event_ref,
            operator_scope_policy_ref=operator_scope_policy_ref,
        )

        candidate.signatures.append(signature)
        candidate.last_signing_timestamp_utc = now
        # State transition: progress to awaiting_signing when first
        # signature lands; signed when we have owner+peer; final
        # activation happens via activate() (not here).
        if candidate.activation_state == ActivationState.UNACTIVATED.value:
            candidate.activation_state = ActivationState.AWAITING_SIGNING.value
        if self._has_independent_owner_peer_signatures(candidate):
            # Operator signature is required when:
            # * target_domain is sensitive (existing rule), OR
            # * target_write_risk == external_effect (Codex RCO
            #   round-2 fix #1 -- spec E12/E14: WRT-003 activation
            #   always needs operator scope/signature).
            needs_operator = (
                candidate.target_domain in self.sensitive_domains
                or candidate.target_write_risk == "external_effect"
            )
            if needs_operator:
                if self._has_signature(candidate, SigningRole.OPERATOR.value):
                    candidate.activation_state = ActivationState.SIGNED.value
                # else stay in AWAITING_SIGNING
            else:
                candidate.activation_state = ActivationState.SIGNED.value
        self.update_candidate(candidate)

        # Emit bridge event per spec edit E16
        self.emit_bridge_event({
            "type": "handoff",
            "status": "provenance_signed",
            "to": "peer" if signing_role == SigningRole.OWNER.value else "owner",
            "payload": {
                "kind": "solver",
                "solver_candidate_id": candidate_id,
                "solver_manifest_hash": candidate.manifest_sha256,
                "owner_agent_id": (signing_agent_id
                                       if signing_role == SigningRole.OWNER.value
                                       else ""),
                "peer_agent_id": (signing_agent_id
                                       if signing_role == SigningRole.PEER.value
                                       else ""),
                "signing_role": signing_role,
                "round_number": round_number,
                "audit_event_id": audit_event_ref,
                "signature_id": sig_id,
            },
            "ts_utc": now,
        })

        return signature

    def verify_solver_provenance(self, candidate_id: str
                                    ) -> VerificationResult:
        """Verify a candidate's signing chain.

        Checks (per spec):
        1. Manifest exists at the stored manifest_sha256.
        2. >= 1 owner + 1 peer signature with matching manifest_sha256.
        3. All bridge events referenced exist and were emitted by the
           declared agents. (v1: trust bridge_event_ref strings; the
           bridge ledger itself is the source of truth.)
        4. All MAGMA audit events referenced exist and are not invalidated.
           (v1: trust audit_event_ref strings; MAGMA is source of truth.)
        5. Operator scope policy referenced is still active.
        6. No solver.activation_revoked event for this solver.
        """
        result = VerificationResult(valid=False, candidate_id=candidate_id)
        candidate = self.fetch_candidate(candidate_id)
        if candidate is None:
            result.reasons.append("candidate_unknown")
            return result

        result.activation_state = candidate.activation_state
        result.manifest_sha256_observed = candidate.manifest_sha256
        result.has_owner_signature = self._has_signature(
            candidate, SigningRole.OWNER.value
        )
        result.has_peer_signature = self._has_signature(
            candidate, SigningRole.PEER.value
        )
        result.has_operator_signature = self._has_signature(
            candidate, SigningRole.OPERATOR.value
        )
        result.quarantine_evidence_refs = list(
            candidate.quarantine_evidence_refs
        )

        if candidate.activation_state == ActivationState.REVOKED.value:
            result.reasons.append("solver_revoked")
            return result

        # Codex RCO round-2 fix #2: QUARANTINED solvers MUST verify as
        # invalid. WriteRCOGate's WRT-003 verify_solver_provenance call
        # needs a fail-closed signal during the quarantine window.
        # Reactivation requires explicit operator-driven flow (a new
        # signing chain with a fresh manifest_sha256), not just
        # waiting for the counter to reset.
        if candidate.activation_state == ActivationState.QUARANTINED.value:
            result.reasons.append("solver_quarantined")
            return result

        if not result.has_owner_signature:
            result.reasons.append("missing_owner_signature")
        if not result.has_peer_signature:
            result.reasons.append("missing_peer_signature")
        if self._owner_peer_agent_overlap(candidate):
            result.reasons.append("owner_peer_same_agent")

        # Check: every signature's manifest_sha256 matches the current
        # candidate manifest hash. Mismatch invalidates the chain.
        for sig in candidate.signatures:
            if sig.manifest_sha256 != candidate.manifest_sha256:
                result.reasons.append(
                    f"manifest_hash_mismatch:{sig.signature_id}"
                )

        # Check: operator scope policy active for each signature
        for sig in candidate.signatures:
            if not self.operator_scope_policy_active(
                    sig.operator_scope_policy_ref):
                result.reasons.append(
                    f"scope_policy_revoked:{sig.signature_id}"
                )

        # Codex RCO round-2 fix #1: WRT-003 external_effect ALWAYS
        # requires operator signature, in addition to the existing
        # sensitive-domain rule. Spec E12/E14 are explicit that
        # activation of a WRT-003 solver needs operator scope /
        # signature even when target_domain is not sensitive.
        if candidate.target_write_risk == "external_effect":
            if not result.has_operator_signature:
                result.reasons.append(
                    "external_effect_requires_operator_signature"
                )

        # Sensitive-domain rule: needs operator signature
        if candidate.target_domain in self.sensitive_domains:
            if not result.has_operator_signature:
                result.reasons.append("sensitive_domain_requires_operator")

        if not result.reasons:
            result.valid = True
        return result

    def record_run_result(self, *, candidate_id: str,
                             divergence_score: float,
                             evidence_ref: str) -> ActivationState:
        """Update the auto-quarantine counter after a shadow/hybrid run.

        Per spec edit E15: divergence >= 0.40 for 5 consecutive runs
        triggers auto-quarantine (REVERSIBLE). NOT permanent revoke.

        Returns the resulting activation state.
        """
        candidate = self.fetch_candidate(candidate_id)
        if candidate is None:
            raise KeyError(f"unknown candidate: {candidate_id}")
        if candidate.activation_state == ActivationState.REVOKED.value:
            return ActivationState.REVOKED
        if (divergence_score
                >= self.quarantine_divergence_score_threshold):
            candidate.consecutive_divergent_runs += 1
            candidate.quarantine_evidence_refs.append(evidence_ref)
            above_threshold = True
        else:
            # Reset on any below-threshold run
            candidate.consecutive_divergent_runs = 0
            candidate.quarantine_evidence_refs = []
            above_threshold = False

        quarantine_threshold_reached = (
            candidate.consecutive_divergent_runs
            >= self.quarantine_consecutive_threshold
        )
        self.emit_magma_event({
            "event_type": "solver.run_result_recorded",
            "solver_candidate_id": candidate_id,
            "divergence_score": divergence_score,
            "evidence_ref": evidence_ref,
            "threshold": self.quarantine_divergence_score_threshold,
            "above_threshold": above_threshold,
            "consecutive_divergent_runs": (
                candidate.consecutive_divergent_runs
            ),
            "quarantine_threshold_reached": quarantine_threshold_reached,
            "activation_state": candidate.activation_state,
            "ts_utc": _utc_iso(),
        })

        if (quarantine_threshold_reached
                and candidate.activation_state
                != ActivationState.QUARANTINED.value
                and candidate.activation_state
                != ActivationState.REVOKED.value):
            self._auto_quarantine(candidate)
        else:
            self.update_candidate(candidate)
        return ActivationState(candidate.activation_state)

    def revoke(self, *, candidate_id: str, reason: str,
                revoked_by: str = RevocationActor.OPERATOR.value
                ) -> RevocationResult:
        """Permanently revoke a candidate's activation.

        Per spec edit E15: permanent revocation is one-way; reactivating
        requires a NEW signing flow with a fresh manifest hash.

        revoked_by == 'automatic_drift_detection' is REJECTED here;
        automatic detection only downgrades to quarantine via
        record_run_result(). Use that path instead.
        """
        if revoked_by == RevocationActor.AUTOMATIC_DRIFT_DETECTION.value:
            return RevocationResult(
                success=False,
                candidate_id=candidate_id,
                new_state=ActivationState.UNACTIVATED.value,
                reason=(
                    "automatic_drift_detection cannot permanently revoke; "
                    "use record_run_result for auto-quarantine"
                ),
            )
        candidate = self.fetch_candidate(candidate_id)
        if candidate is None:
            return RevocationResult(
                success=False,
                candidate_id=candidate_id,
                new_state=ActivationState.UNACTIVATED.value,
                reason="candidate_unknown",
            )
        if candidate.activation_state == ActivationState.REVOKED.value:
            return RevocationResult(
                success=True,
                candidate_id=candidate_id,
                new_state=ActivationState.REVOKED.value,
                audit_event_ref=candidate.revocation_audit_ref,
                reason="already_revoked",
            )
        audit_ref = self.emit_magma_event({
            "event_type": "solver.activation_revoked",
            "solver_candidate_id": candidate_id,
            "revoked_by": revoked_by,
            "reason": reason,
            "supersedes_signature_ids": [s.signature_id
                                            for s in candidate.signatures],
            "ts_utc": _utc_iso(),
        })
        bridge_event_ref = _transition_bridge_ref(
            "activation_revoked", candidate_id, audit_ref
        )
        receipt_bundle = self._build_transition_receipt_bundle(
            candidate=candidate,
            transition="activation_revoked",
            audit_event_ref=audit_ref,
            bridge_event_ref=bridge_event_ref,
            revoked_by=revoked_by,
            reason=reason,
            new_state=ActivationState.REVOKED.value,
        )
        candidate.activation_state = ActivationState.REVOKED.value
        candidate.revocation_audit_ref = audit_ref
        self.update_candidate(candidate)
        self._emit_built_transition_receipt_bundle(receipt_bundle)
        # Bridge event per spec edit E16 (decision/activation_revoked)
        self.emit_bridge_event({
            "type": "decision",
            "status": "activation_revoked",
            "to": "operator",
            "payload": {
                "kind": "solver",
                "solver_candidate_id": candidate_id,
                "revocation_reason": reason,
                "revoked_by": revoked_by,
                "supersedes_signature_ids": [s.signature_id
                                                for s in candidate.signatures],
                "bridge_event_ref": bridge_event_ref,
                "audit_event_id": audit_ref,
            },
            "ts_utc": _utc_iso(),
        })
        return RevocationResult(
            success=True,
            candidate_id=candidate_id,
            new_state=candidate.activation_state,
            audit_event_ref=audit_ref,
            reason=reason,
        )

    def activate(self, *, candidate_id: str) -> ActivationState:
        """Promote a SIGNED candidate to ACTIVATED.

        verify_solver_provenance must return valid=True; otherwise the
        state stays SIGNED (or AWAITING_SIGNING) and an audit event
        records the refusal.
        """
        candidate = self.fetch_candidate(candidate_id)
        if candidate is None:
            raise KeyError(f"unknown candidate: {candidate_id}")
        result = self.verify_solver_provenance(candidate_id)
        if not result.valid:
            audit_ref = self.emit_magma_event({
                "event_type": "solver.activation_refused",
                "solver_candidate_id": candidate_id,
                "reasons": result.reasons,
                "ts_utc": _utc_iso(),
            })
            self._emit_transition_receipt_bundle(
                candidate=candidate,
                transition="activation_refused",
                audit_event_ref=audit_ref,
                verification=result,
            )
            return ActivationState(candidate.activation_state)
        if candidate.activation_state == ActivationState.ACTIVATED.value:
            return ActivationState.ACTIVATED
        if candidate.activation_state in (
                ActivationState.QUARANTINED.value,
                ActivationState.REVOKED.value):
            return ActivationState(candidate.activation_state)
        if candidate.activation_state != ActivationState.SIGNED.value:
            audit_ref = self.emit_magma_event({
                "event_type": "solver.activation_refused",
                "solver_candidate_id": candidate_id,
                "reasons": ["candidate_not_signed"],
                "activation_state": candidate.activation_state,
                "ts_utc": _utc_iso(),
            })
            self._emit_transition_receipt_bundle(
                candidate=candidate,
                transition="activation_refused",
                audit_event_ref=audit_ref,
                verification=result,
                reason_codes=["candidate_not_signed"],
            )
            return ActivationState(candidate.activation_state)
        audit_ref = self.emit_magma_event({
            "event_type": "solver.activation_authorised",
            "solver_candidate_id": candidate_id,
            "ts_utc": _utc_iso(),
        })
        bridge_event_ref = _transition_bridge_ref(
            "activation_authorised", candidate_id, audit_ref
        )
        receipt_bundle = self._build_transition_receipt_bundle(
            candidate=candidate,
            transition="activation_authorised",
            audit_event_ref=audit_ref,
            bridge_event_ref=bridge_event_ref,
            verification=result,
            new_state=ActivationState.ACTIVATED.value,
        )
        candidate.activation_state = ActivationState.ACTIVATED.value
        self.update_candidate(candidate)
        self._emit_built_transition_receipt_bundle(receipt_bundle)
        self.emit_bridge_event({
            "type": "handoff",
            "status": "activation_authorised",
            "to": "operator",
            "payload": {
                "kind": "solver",
                "solver_candidate_id": candidate_id,
                "bridge_event_ref": bridge_event_ref,
                "audit_event_id": audit_ref,
            },
            "ts_utc": _utc_iso(),
        })
        return ActivationState.ACTIVATED

    # =========================================================================
    # INTERNAL HELPERS
    # =========================================================================

    def _has_signature(self, candidate: SolverCandidateRecord,
                          role: str) -> bool:
        return any(s.signing_role == role for s in candidate.signatures)

    def _signing_agent_ids(self, candidate: SolverCandidateRecord,
                              role: str) -> set[str]:
        return {
            s.signing_agent_id
            for s in candidate.signatures
            if s.signing_role == role
        }

    def _owner_peer_agent_overlap(self,
                                    candidate: SolverCandidateRecord
                                    ) -> set[str]:
        return (
            self._signing_agent_ids(candidate, SigningRole.OWNER.value)
            & self._signing_agent_ids(candidate, SigningRole.PEER.value)
        )

    def _has_independent_owner_peer_signatures(
            self, candidate: SolverCandidateRecord) -> bool:
        return (
            self._has_signature(candidate, SigningRole.OWNER.value)
            and self._has_signature(candidate, SigningRole.PEER.value)
            and not self._owner_peer_agent_overlap(candidate)
        )

    def _emit_transition_receipt_bundle(
        self,
        *,
        candidate: SolverCandidateRecord,
        transition: str,
        audit_event_ref: str,
        bridge_event_ref: str = "",
        verification: VerificationResult | None = None,
        reason_codes: list[str] | None = None,
        revoked_by: str = "",
        reason: str = "",
        new_state: str | None = None,
    ) -> None:
        self._emit_built_transition_receipt_bundle(
            self._build_transition_receipt_bundle(
                candidate=candidate,
                transition=transition,
                audit_event_ref=audit_event_ref,
                bridge_event_ref=bridge_event_ref,
                verification=verification,
                reason_codes=reason_codes,
                revoked_by=revoked_by,
                reason=reason,
                new_state=new_state,
            )
        )

    def _build_transition_receipt_bundle(
        self,
        *,
        candidate: SolverCandidateRecord,
        transition: str,
        audit_event_ref: str,
        bridge_event_ref: str = "",
        verification: VerificationResult | None = None,
        reason_codes: list[str] | None = None,
        revoked_by: str = "",
        reason: str = "",
        new_state: str | None = None,
    ) -> Optional[dict]:
        if self.emit_receipt_bundle is None:
            return None
        return build_solver_provenance_transition_receipt(
            candidate=candidate,
            transition=transition,
            audit_event_ref=audit_event_ref,
            bridge_event_ref=bridge_event_ref,
            verification=verification,
            reason_codes=reason_codes,
            revoked_by=revoked_by,
            reason=reason,
            new_state=new_state,
            previous_receipt=self._last_emitted_receipt,
        )

    def _emit_built_transition_receipt_bundle(
        self,
        bundle: Optional[dict],
    ) -> None:
        if bundle is None:
            return
        # Sink call first: if the sink raises, the chain head must NOT
        # advance (the bundle was never persisted). Only commit chain
        # head on successful sink return so a retry with a working sink
        # emits with prev_receipt_hash=None as if the failed attempt
        # never happened.
        self.emit_receipt_bundle(bundle)
        self._last_emitted_receipt = bundle["receipt"]

    def _auto_quarantine(self, candidate: SolverCandidateRecord) -> None:
        audit_ref = self.emit_magma_event({
            "event_type": "solver.quarantined",
            "solver_candidate_id": candidate.candidate_id,
            "evidence_refs": list(candidate.quarantine_evidence_refs),
            "threshold": self.quarantine_divergence_score_threshold,
            "consecutive_runs": candidate.consecutive_divergent_runs,
            "ts_utc": _utc_iso(),
        })
        bridge_event_ref = _transition_bridge_ref(
            "quarantined", candidate.candidate_id, audit_ref
        )
        receipt_bundle = self._build_transition_receipt_bundle(
            candidate=candidate,
            transition="quarantined",
            audit_event_ref=audit_ref,
            bridge_event_ref=bridge_event_ref,
            reason_codes=["quarantine_threshold_reached"],
            new_state=ActivationState.QUARANTINED.value,
        )
        candidate.activation_state = ActivationState.QUARANTINED.value
        self.update_candidate(candidate)
        self._emit_built_transition_receipt_bundle(receipt_bundle)
        self.emit_bridge_event({
            "type": "decision",
            "status": "quarantined",
            "to": "operator",
            "payload": {
                "kind": "solver",
                "solver_candidate_id": candidate.candidate_id,
                "evidence_refs": list(candidate.quarantine_evidence_refs),
                "bridge_event_ref": bridge_event_ref,
                "audit_event_id": audit_ref,
            },
            "ts_utc": _utc_iso(),
        })


# --------------------------------------------------------------------------
# Canonical manifest helpers
# --------------------------------------------------------------------------


def canonicalize_manifest(manifest: dict) -> tuple[str, str]:
    """Return (canonical_json, sha256_hex) for strict JSON-native data."""
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return canonical, digest


# --------------------------------------------------------------------------
# MAGMA receipt binding helpers
# --------------------------------------------------------------------------


def build_solver_provenance_transition_artifact(
    *,
    candidate: SolverCandidateRecord,
    transition: str,
    audit_event_ref: str,
    bridge_event_ref: str = "",
    verification: VerificationResult | None = None,
    reason_codes: list[str] | None = None,
    revoked_by: str = "",
    reason: str = "",
    new_state: str | None = None,
    ts_utc: str | None = None,
    policy_version: str = SOLVER_PROVENANCE_POLICY_VERSION,
    charter_version: str = SOLVER_PROVENANCE_CHARTER_VERSION,
) -> dict[str, Any]:
    """Build a payload-free RCO artifact for a provenance transition."""
    from waggledance.core.magma.rco_decision_artifact import (
        build_rco_decision_artifact,
    )

    gate_decision = _transition_gate_decision(transition)
    approved = gate_decision == "allow"
    intent = {
        "subject_type": "solver",
        "candidate_id": candidate.candidate_id,
        "transition": transition,
        "target_domain": candidate.target_domain,
        "target_write_risk": candidate.target_write_risk,
    }
    return build_rco_decision_artifact(
        decision_id=_safe_ref(
            "rco:solver_provenance:"
            f"{transition}:{candidate.candidate_id}"
        ),
        ts_utc=ts_utc or _utc_iso(),
        intent=intent,
        write_payload=_transition_artifact_payload(
            candidate=candidate,
            transition=transition,
            audit_event_ref=audit_event_ref,
            bridge_event_ref=bridge_event_ref,
            verification=verification,
            reason=reason,
            revoked_by=revoked_by,
            new_state=new_state,
        ),
        risk_class="local_artifact",
        gate_decision=gate_decision,
        approved=approved,
        operator_required=False,
        policy_version=policy_version,
        charter_version=charter_version,
        scope_policy_decision="auto_approved" if approved else "denied",
        peer_rco_verdict=(
            "pass" if transition == "activation_authorised"
            else "not_requested"
        ),
        verifier_path=[
            "solver_provenance_v1",
            "rco_decision_artifact_v0",
        ],
        reason_codes=_transition_reason_codes(
            candidate=candidate,
            transition=transition,
            gate_decision=gate_decision,
            verification=verification,
            reason_codes=reason_codes,
            revoked_by=revoked_by,
        ),
        audit_event_ids=[_safe_ref(f"audit:{audit_event_ref}")],
    )


def build_solver_provenance_transition_receipt(
    *,
    candidate: SolverCandidateRecord,
    transition: str,
    audit_event_ref: str,
    bridge_event_ref: str = "",
    verification: VerificationResult | None = None,
    reason_codes: list[str] | None = None,
    revoked_by: str = "",
    reason: str = "",
    new_state: str | None = None,
    ts_utc: str | None = None,
    policy_digest: str | None = None,
    charter_digest: str | None = None,
    world_snapshot_digest: str | None = None,
    solver_contract_digest: str | None = None,
    previous_receipt: dict[str, Any] | None = None,
    domain_threshold_version: str = SOLVER_PROVENANCE_DOMAIN_THRESHOLD_VERSION,
) -> dict[str, Any]:
    """Build EvaluationResult + MAGMA receipt for a provenance transition.

    The returned bundle intentionally omits raw manifests, revocation reasons,
    and quarantine evidence payloads. It binds only IDs, state metadata, and
    digests so callers can emit it safely into MAGMA.
    """
    from waggledance.core.magma.evaluation_result import build_evaluation_result
    from waggledance.core.magma.receipt import build_magma_receipt

    effective_ts = ts_utc or _utc_iso()
    artifact = build_solver_provenance_transition_artifact(
        candidate=candidate,
        transition=transition,
        audit_event_ref=audit_event_ref,
        bridge_event_ref=bridge_event_ref,
        verification=verification,
        reason_codes=reason_codes,
        revoked_by=revoked_by,
        reason=reason,
        new_state=new_state,
        ts_utc=effective_ts,
    )
    artifact_digest = sha256_digest(artifact)
    payload = _transition_receipt_payload(
        candidate=candidate,
        transition=transition,
        audit_event_ref=audit_event_ref,
        bridge_event_ref=bridge_event_ref,
        verification=verification,
        reason=reason,
        revoked_by=revoked_by,
        new_state=new_state,
        rco_decision_digest=artifact_digest,
    )
    gate_decision = str(artifact["gate_decision"])
    evaluation = build_evaluation_result(
        case_id=_safe_ref(
            "case:solver_provenance:"
            f"{transition}:{candidate.candidate_id}"
        ),
        subject_type="solver",
        target_payload=payload,
        risk_class="local_artifact",
        expected_gate=gate_decision,
        actual_gate=gate_decision,
        verifier_path=[
            "solver_provenance_v1",
            "rco_decision_artifact_v0",
            "magma_receipt_v1",
        ],
        solver_selection=[f"solver:{_safe_ref(candidate.candidate_id)}"],
        policy_version=str(artifact["policy_version"]),
        charter_version=str(artifact["charter_version"]),
        domain_threshold_version=domain_threshold_version,
        verdict=_transition_verdict(gate_decision),
        reason_codes=list(artifact["reason_codes"]),
        confidence_score=0.95 if gate_decision == "allow" else 0.85,
        uncertainty_sources=[],
    )
    receipt = build_magma_receipt(
        event_id=_safe_ref(
            "magma:solver_provenance:"
            f"{transition}:{candidate.candidate_id}"
        ),
        ts_utc=effective_ts,
        risk_class="local_artifact",
        payload=payload,
        evaluation_result=evaluation,
        policy_digest=policy_digest or sha256_digest({
            "policy_version": artifact["policy_version"],
        }),
        charter_digest=charter_digest or sha256_digest({
            "charter_version": artifact["charter_version"],
        }),
        rco_decision_digest=artifact_digest,
        world_snapshot_digest=world_snapshot_digest or sha256_digest({
            "candidate_id": candidate.candidate_id,
            "transition": transition,
            "activation_state": candidate.activation_state,
            "new_state": new_state or candidate.activation_state,
            "audit_event_ref": audit_event_ref,
            "bridge_event_ref": bridge_event_ref,
        }),
        solver_contract_digest=solver_contract_digest or sha256_digest({
            "candidate_id": candidate.candidate_id,
            "manifest_sha256": candidate.manifest_sha256,
            "target_domain": candidate.target_domain,
            "target_write_risk": candidate.target_write_risk,
        }),
        previous_receipt=previous_receipt,
    )
    return {
        "solver_provenance_transition_artifact": artifact,
        "evaluation_result": evaluation,
        "receipt": receipt,
        "payload": payload,
    }


def _transition_artifact_payload(
    *,
    candidate: SolverCandidateRecord,
    transition: str,
    audit_event_ref: str,
    bridge_event_ref: str,
    verification: VerificationResult | None,
    reason: str,
    revoked_by: str,
    new_state: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": "solver_provenance_transition_payload.v1",
        "candidate_id": candidate.candidate_id,
        "transition": transition,
        "manifest_sha256": candidate.manifest_sha256,
        "target_domain": candidate.target_domain,
        "target_write_risk": candidate.target_write_risk,
        "activation_state": candidate.activation_state,
        "new_state": new_state or candidate.activation_state,
        "audit_event_ref": audit_event_ref,
        "bridge_event_ref": bridge_event_ref,
        "signature_count": len(candidate.signatures),
        "verification_valid": (
            verification.valid if verification is not None else None
        ),
        "verification_reasons_digest": (
            sha256_digest({"reasons": verification.reasons})
            if verification is not None
            else None
        ),
        "quarantine_evidence_digest": (
            sha256_digest({
                "evidence_refs": list(candidate.quarantine_evidence_refs),
            })
            if candidate.quarantine_evidence_refs
            else None
        ),
        "revoked_by": revoked_by,
        "reason_digest": (
            sha256_digest({"reason": reason}) if reason else None
        ),
    }


def _transition_receipt_payload(
    *,
    candidate: SolverCandidateRecord,
    transition: str,
    audit_event_ref: str,
    bridge_event_ref: str,
    verification: VerificationResult | None,
    reason: str,
    revoked_by: str,
    new_state: str | None,
    rco_decision_digest: str,
) -> dict[str, Any]:
    payload = _transition_artifact_payload(
        candidate=candidate,
        transition=transition,
        audit_event_ref=audit_event_ref,
        bridge_event_ref=bridge_event_ref,
        verification=verification,
        reason=reason,
        revoked_by=revoked_by,
        new_state=new_state,
    )
    payload["rco_decision_digest"] = rco_decision_digest
    return payload


def _transition_gate_decision(transition: str) -> str:
    if transition == "activation_refused":
        return "refuse"
    return "allow"


def _transition_verdict(gate_decision: str) -> str:
    if gate_decision == "allow":
        return "pass"
    if gate_decision == "refuse":
        return "refuse"
    return "review"


def _transition_reason_codes(
    *,
    candidate: SolverCandidateRecord,
    transition: str,
    gate_decision: str,
    verification: VerificationResult | None,
    reason_codes: list[str] | None,
    revoked_by: str,
) -> list[str]:
    codes = [
        f"solver_provenance:transition:{_safe_ref(transition)}",
        f"solver_provenance:gate:{_safe_ref(gate_decision)}",
        f"solver_provenance:target_risk:{_safe_ref(candidate.target_write_risk)}",
        f"solver_provenance:target_domain:{_safe_ref(candidate.target_domain)}",
    ]
    if reason_codes:
        codes.extend(f"solver_provenance:reason:{_safe_ref(code)}"
                     for code in reason_codes)
    if verification is not None:
        codes.append(
            "solver_provenance:verification:pass"
            if verification.valid
            else "solver_provenance:verification:fail"
        )
        codes.extend(
            f"solver_provenance:verify_reason:{_safe_ref(reason)}"
            for reason in verification.reasons[:8]
        )
    if revoked_by:
        codes.append(f"solver_provenance:revoked_by:{_safe_ref(revoked_by)}")
    return codes[:30]


def _transition_bridge_ref(
    transition: str,
    candidate_id: str,
    audit_event_ref: str,
) -> str:
    return _safe_ref(
        f"bridge:solver_provenance:{transition}:{candidate_id}:{audit_event_ref}"
    )


def _safe_ref(value: str) -> str:
    safe = "".join(
        char if char.isalnum() or char in ":._-" else "_"
        for char in str(value)
    ).strip("_")
    if not safe or not safe[0].isalpha():
        safe = f"ref:{safe or 'unknown'}"
    return safe[:160]


# --------------------------------------------------------------------------
# Utility
# --------------------------------------------------------------------------


def _utc_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


__all__ = [
    "SigningRole",
    "ActivationState",
    "RevocationActor",
    "ProvenanceSignature",
    "VerificationResult",
    "RevocationResult",
    "SolverCandidateRecord",
    "SolverProvenance",
    "SOLVER_PROVENANCE_POLICY_VERSION",
    "SOLVER_PROVENANCE_CHARTER_VERSION",
    "SOLVER_PROVENANCE_DOMAIN_THRESHOLD_VERSION",
    "build_solver_provenance_transition_artifact",
    "build_solver_provenance_transition_receipt",
    "canonicalize_manifest",
]
