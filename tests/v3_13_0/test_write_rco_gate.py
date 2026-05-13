# SPDX-License-Identifier: BUSL-1.1
"""Tests for WriteRCOGate v1.

Covers:
* Classification across the 4 risk classes
* WRT-001 / WRT-002 / WRT-003 happy path
* Each stop-condition
* Rollback artifact stored
* Audit envelope content
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import pytest

from waggledance.core.v3_13_0.write_rco_gate import (
    Intent,
    GateOutcome,
    ExecutionResult,
    ConnectorInfo,
    StateInfo,
    RecoveryCapsuleInfo,
    WriteRCOGate,
    WriteRiskClass,
    AuditEventType,
    StopCondition,
    PeerRCOResult,
    ScopePolicyResult,
)
from waggledance.core.v3_13_0.solver_provenance import (
    ActivationState,
    VerificationResult,
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _audit_emit_collector(collector: list):
    def emit(envelope: dict) -> str:
        envelope_id = f"evt_{len(collector):04d}"
        envelope["__id"] = envelope_id
        collector.append(envelope)
        return envelope_id
    return emit


def _no_cred_scan(payload):
    return []


def _cred_scan_finds(*hits):
    def scanner(payload):
        return list(hits)
    return scanner


def _make_gate(*, audit_collector: list,
                connectors: dict[str, ConnectorInfo] = None,
                states: dict[str, StateInfo] = None,
                capsules: dict[str, RecoveryCapsuleInfo] = None,
                peer_verdict: str = "pass",
                peer_rounds: int = 1,
                peer_timed_out: bool = False,
                scope_decision: str = "auto_approved",
                scope_reason: str = "",
                cred_scan=_no_cred_scan,
                write_result: ExecutionResult = None,
                solver_provenance_result: VerificationResult = None
                ) -> WriteRCOGate:
    connectors = connectors or {}
    states = states or {}
    capsules = capsules or {}

    def peer(intent: Intent) -> PeerRCOResult:
        return PeerRCOResult(
            verdict=peer_verdict,
            rounds=peer_rounds,
            timed_out=peer_timed_out,
        )

    def scope(intent, conn, st) -> ScopePolicyResult:
        return ScopePolicyResult(decision=scope_decision, reason=scope_reason)

    def writer(intent: Intent) -> ExecutionResult:
        if write_result is not None:
            return write_result
        return ExecutionResult(intent_id=intent.intent_id, success=True,
                                elapsed_ms=10)

    def verify_solver_provenance(candidate_id: str) -> VerificationResult:
        if solver_provenance_result is not None:
            return solver_provenance_result
        return VerificationResult(
            valid=True,
            candidate_id=candidate_id,
            activation_state=ActivationState.ACTIVATED.value,
            has_owner_signature=True,
            has_peer_signature=True,
            has_operator_signature=True,
            manifest_sha256_observed="a" * 64,
        )

    return WriteRCOGate(
        audit_emit=_audit_emit_collector(audit_collector),
        classify_payload_credential_scan=cred_scan,
        fetch_connector_info=lambda cid: connectors.get(cid),
        fetch_state_info=lambda sid: states.get(sid),
        fetch_recovery_capsule=lambda tid: capsules.get(tid),
        peer_rco_solicit=peer,
        operator_scope_policy_check=scope,
        write_executor=writer,
        verify_solver_provenance=verify_solver_provenance,
    )


def _make_intent(*, target_state_ref: str = "state:test",
                  connector_ref: Optional[str] = None,
                  tool_descriptor_id: str = "tool_test",
                  action: str = "insert",
                  payload: dict = None) -> Intent:
    return Intent.construct(
        agent_id="claude",
        session_id="sess_test",
        tool_descriptor_id=tool_descriptor_id,
        target_state_ref=target_state_ref,
        action=action,
        payload=payload or {"key": "value"},
        connector_ref=connector_ref,
    )


# --------------------------------------------------------------------------
# Intent construction
# --------------------------------------------------------------------------


class TestIntent:

    def test_intent_construct_creates_uuid(self):
        intent = _make_intent()
        assert len(intent.intent_id) == 36
        assert intent.intent_id.count("-") == 4

    def test_intent_construct_canonical_payload_hash(self):
        i1 = _make_intent(payload={"a": 1, "b": 2})
        i2 = _make_intent(payload={"b": 2, "a": 1})
        assert i1.payload_hash == i2.payload_hash

    def test_intent_construct_different_payloads_differ(self):
        i1 = _make_intent(payload={"a": 1})
        i2 = _make_intent(payload={"a": 2})
        assert i1.payload_hash != i2.payload_hash

    def test_intent_construct_rejects_non_json_native_payload(self):
        with pytest.raises(TypeError):
            _make_intent(payload={
                "dt": datetime(2026, 5, 13, tzinfo=timezone.utc),
            })


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------


class TestClassification:

    def test_classify_internal_memory_when_plane_magma_history(self):
        audit = []
        gate = _make_gate(audit_collector=audit, states={
            "state:audit_log": StateInfo(
                state_id="state:audit_log",
                plane="magma_history",
                write_modes_allowed=["append"],
                sensitive_class="internal",
                single_writer_required=True,
            ),
        })
        intent = _make_intent(target_state_ref="state:audit_log")
        assert gate.classify(intent) == WriteRiskClass.INTERNAL_MEMORY

    def test_classify_local_artifact_when_plane_filesystem(self):
        audit = []
        gate = _make_gate(audit_collector=audit, states={
            "state:report_dir": StateInfo(
                state_id="state:report_dir",
                plane="filesystem_artifact",
                write_modes_allowed=["insert"],
                sensitive_class="restricted",
                single_writer_required=False,
            ),
        })
        intent = _make_intent(target_state_ref="state:report_dir")
        assert gate.classify(intent) == WriteRiskClass.LOCAL_ARTIFACT

    def test_classify_external_effect_when_plane_external_system(self):
        audit = []
        gate = _make_gate(audit_collector=audit, states={
            "state:logbook": StateInfo(
                state_id="state:logbook",
                plane="external_system",
                write_modes_allowed=["insert", "update"],
                sensitive_class="restricted",
                single_writer_required=True,
            ),
        })
        intent = _make_intent(target_state_ref="state:logbook")
        assert gate.classify(intent) == WriteRiskClass.EXTERNAL_EFFECT

    def test_classify_external_effect_when_connector_write_risk(self):
        audit = []
        gate = _make_gate(
            audit_collector=audit,
            connectors={
                "conn:webhook": ConnectorInfo(
                    connector_id="conn:webhook",
                    write_risk=WriteRiskClass.EXTERNAL_EFFECT,
                    auth_mode="api_key",
                    can_run_headless=True,
                    rate_limit_max_workers=3,
                    rate_limit_request_delay_s=0.15,
                ),
            },
            states={
                "state:dest": StateInfo(
                    state_id="state:dest",
                    plane="external_system",
                    write_modes_allowed=["post"],
                    sensitive_class="internal",
                    single_writer_required=False,
                ),
            },
        )
        intent = _make_intent(target_state_ref="state:dest",
                               connector_ref="conn:webhook",
                               action="post")
        assert gate.classify(intent) == WriteRiskClass.EXTERNAL_EFFECT

    def test_classify_ambiguous_defaults_external_effect_conservative(self):
        """When neither state nor connector clearly identifies the risk, the
        gate conservatively classifies as external_effect to avoid silent
        permit."""
        audit = []
        gate = _make_gate(audit_collector=audit)
        intent = _make_intent(target_state_ref="state:unknown")
        assert gate.classify(intent) == WriteRiskClass.EXTERNAL_EFFECT


# --------------------------------------------------------------------------
# WRT-001 internal_memory happy path
# --------------------------------------------------------------------------


class TestInternalMemoryRoute:

    def test_wrt_001_happy_path_emits_classify_and_approved(self):
        audit = []
        gate = _make_gate(audit_collector=audit, states={
            "state:audit_log": StateInfo(
                state_id="state:audit_log",
                plane="magma_history",
                write_modes_allowed=["append"],
                sensitive_class="internal",
                single_writer_required=True,
            ),
        })
        intent = _make_intent(target_state_ref="state:audit_log")
        outcome = gate.route(intent)

        assert outcome.approved is True
        assert outcome.risk_class == WriteRiskClass.INTERNAL_MEMORY
        event_types = [e["event_type"] for e in audit]
        assert AuditEventType.INTENT_CLASSIFIED.value in event_types
        assert AuditEventType.INTENT_APPROVED.value in event_types
        assert outcome.stop_condition is None


# --------------------------------------------------------------------------
# WRT-002 local_artifact -- happy path + path-scope violation
# --------------------------------------------------------------------------


class TestLocalArtifactRoute:

    def test_wrt_002_happy_path(self):
        audit = []
        gate = _make_gate(audit_collector=audit, states={
            "state:report_dir": StateInfo(
                state_id="state:report_dir",
                plane="filesystem_artifact",
                write_modes_allowed=["insert"],
                sensitive_class="restricted",
                single_writer_required=False,
            ),
        })
        intent = _make_intent(target_state_ref="state:report_dir")
        outcome = gate.route(intent)
        assert outcome.approved is True
        assert outcome.risk_class == WriteRiskClass.LOCAL_ARTIFACT

    def test_wrt_002_credential_pattern_scan_denies(self):
        """Gate-level credential scan (Codex RCO edit #4)."""
        audit = []
        gate = _make_gate(
            audit_collector=audit,
            states={
                "state:report_dir": StateInfo(
                    state_id="state:report_dir",
                    plane="filesystem_artifact",
                    write_modes_allowed=["insert"],
                    sensitive_class="restricted",
                    single_writer_required=False,
                ),
            },
            cred_scan=_cred_scan_finds("api_key:sk-abcd1234"),
        )
        intent = _make_intent(target_state_ref="state:report_dir",
                               payload={"contents": "...secret..."})
        outcome = gate.route(intent)
        assert outcome.approved is False
        assert outcome.stop_condition == StopCondition.ANTI_PATTERN_VIOLATION
        assert "ANTI-004" in (outcome.denial_reason or "")

    def test_wrt_002_action_not_in_write_modes_allowed_denies(self):
        """Codex RCO round-2 fix #2: WRT-002 with action='delete' against
        state.write_modes_allowed=['append'] must deny fail-closed
        with ANTI_PATTERN_VIOLATION."""
        audit = []
        gate = _make_gate(audit_collector=audit, states={
            "state:append_only_log": StateInfo(
                state_id="state:append_only_log",
                plane="filesystem_artifact",
                write_modes_allowed=["append"],
                sensitive_class="internal",
                single_writer_required=False,
            ),
        })
        intent = _make_intent(target_state_ref="state:append_only_log",
                               action="delete")
        outcome = gate.route(intent)
        assert outcome.approved is False
        assert outcome.stop_condition == StopCondition.ANTI_PATTERN_VIOLATION
        assert "write_modes_allowed" in (outcome.denial_reason or "")
        event_types = [e["event_type"] for e in audit]
        assert AuditEventType.INTENT_APPROVED.value not in event_types

    def test_wrt_002_empty_write_modes_allowed_denies(self):
        """Fail-closed: state with empty write_modes_allowed denies all
        writes."""
        audit = []
        gate = _make_gate(audit_collector=audit, states={
            "state:read_only_dir": StateInfo(
                state_id="state:read_only_dir",
                plane="filesystem_artifact",
                write_modes_allowed=[],
                sensitive_class="internal",
                single_writer_required=False,
            ),
        })
        intent = _make_intent(target_state_ref="state:read_only_dir",
                               action="insert")
        outcome = gate.route(intent)
        assert outcome.approved is False
        assert outcome.stop_condition == StopCondition.ANTI_PATTERN_VIOLATION

    def test_wrt_002_unknown_state_denies(self):
        audit = []
        gate = _make_gate(audit_collector=audit, states={
            # No matching state for "state:does_not_exist"
        })
        # Manually classify by forcing a known plane via patching;
        # easiest: use filesystem_artifact-resolving state, then point intent
        # at a different unresolved one
        intent = _make_intent(target_state_ref="state:does_not_exist")
        outcome = gate.route(intent)
        # ambiguous classification defaults external_effect, then external_effect
        # requires connector_ref which is None -> denied
        assert outcome.approved is False


# --------------------------------------------------------------------------
# WRT-003 external_effect
# --------------------------------------------------------------------------


class TestExternalEffectRoute:

    def _setup_wrt_003_state(self):
        return {
            "state:logbook": StateInfo(
                state_id="state:logbook",
                plane="external_system",
                write_modes_allowed=["insert", "update"],
                sensitive_class="restricted",
                single_writer_required=True,
            ),
        }

    def _setup_wrt_003_connector(self):
        return {
            "conn:tomcat_rest": ConnectorInfo(
                connector_id="conn:tomcat_rest",
                write_risk=WriteRiskClass.EXTERNAL_EFFECT,
                auth_mode="session_cookie",
                can_run_headless=True,
                rate_limit_max_workers=3,
                rate_limit_request_delay_s=0.15,
            ),
        }

    def _setup_wrt_003_capsule(self):
        return {
            "tool_logbook_update": RecoveryCapsuleInfo(
                capsule_id="recovery:logbook_update_v1",
                rollback_command="logbook.delete_entry_by_id",
                known_corruption_modes=[],
            ),
        }

    def test_wrt_003_happy_path(self):
        audit = []
        gate = _make_gate(
            audit_collector=audit,
            states=self._setup_wrt_003_state(),
            connectors=self._setup_wrt_003_connector(),
            capsules=self._setup_wrt_003_capsule(),
        )
        intent = _make_intent(
            target_state_ref="state:logbook",
            connector_ref="conn:tomcat_rest",
            tool_descriptor_id="tool_logbook_update",
        )
        outcome = gate.route(intent)
        assert outcome.approved is True
        assert outcome.risk_class == WriteRiskClass.EXTERNAL_EFFECT
        assert outcome.rollback_plan_ref == "recovery:logbook_update_v1"
        event_types = [e["event_type"] for e in audit]
        for expected in [AuditEventType.INTENT_CLASSIFIED.value,
                          AuditEventType.PEER_RCO_REQUESTED.value,
                          AuditEventType.PEER_RCO_COMPLETED.value,
                          AuditEventType.SCOPE_POLICY_DECIDED.value,
                          AuditEventType.INTENT_APPROVED.value]:
            assert expected in event_types

    def test_wrt_003_solver_write_requires_provenance_hook(self):
        audit = []
        gate = WriteRCOGate(
            audit_emit=_audit_emit_collector(audit),
            classify_payload_credential_scan=_no_cred_scan,
            fetch_connector_info=lambda cid: self._setup_wrt_003_connector().get(cid),
            fetch_state_info=lambda sid: self._setup_wrt_003_state().get(sid),
            fetch_recovery_capsule=lambda tid: self._setup_wrt_003_capsule().get(tid),
            peer_rco_solicit=lambda intent: PeerRCOResult(
                verdict="pass", rounds=1, timed_out=False
            ),
            operator_scope_policy_check=lambda intent, conn, st: ScopePolicyResult(
                decision="auto_approved", reason=""
            ),
            write_executor=lambda intent: ExecutionResult(
                intent_id=intent.intent_id, success=True
            ),
        )
        intent = _make_intent(
            target_state_ref="state:logbook",
            connector_ref="conn:tomcat_rest",
            tool_descriptor_id="tool_logbook_update",
            payload={"solver_candidate_id": "cand:spot_optimizer_001"},
        )
        outcome = gate.route(intent)
        assert outcome.approved is False
        assert outcome.stop_condition == StopCondition.ANTI_PATTERN_VIOLATION
        assert "verify_solver_provenance" in (outcome.denial_reason or "")

    def test_wrt_003_solver_write_denies_invalid_provenance(self):
        audit = []
        gate = _make_gate(
            audit_collector=audit,
            states=self._setup_wrt_003_state(),
            connectors=self._setup_wrt_003_connector(),
            capsules=self._setup_wrt_003_capsule(),
            solver_provenance_result=VerificationResult(
                valid=False,
                candidate_id="cand:spot_optimizer_001",
                reasons=["external_effect_requires_operator_signature"],
                activation_state=ActivationState.AWAITING_SIGNING.value,
            ),
        )
        intent = _make_intent(
            target_state_ref="state:logbook",
            connector_ref="conn:tomcat_rest",
            tool_descriptor_id="tool_logbook_update",
            payload={"solver_candidate_id": "cand:spot_optimizer_001"},
        )
        outcome = gate.route(intent)
        assert outcome.approved is False
        assert outcome.stop_condition == StopCondition.ANTI_PATTERN_VIOLATION
        assert "external_effect_requires_operator_signature" in (
            outcome.denial_reason or ""
        )
        event_types = [e["event_type"] for e in audit]
        assert AuditEventType.PEER_RCO_REQUESTED.value not in event_types
        assert AuditEventType.INTENT_APPROVED.value not in event_types

    def test_wrt_003_solver_write_records_valid_provenance(self):
        audit = []
        gate = _make_gate(
            audit_collector=audit,
            states=self._setup_wrt_003_state(),
            connectors=self._setup_wrt_003_connector(),
            capsules=self._setup_wrt_003_capsule(),
            solver_provenance_result=VerificationResult(
                valid=True,
                candidate_id="cand:spot_optimizer_001",
                activation_state=ActivationState.ACTIVATED.value,
                has_owner_signature=True,
                has_peer_signature=True,
                has_operator_signature=True,
                manifest_sha256_observed="b" * 64,
            ),
        )
        intent = _make_intent(
            target_state_ref="state:logbook",
            connector_ref="conn:tomcat_rest",
            tool_descriptor_id="tool_logbook_update",
            payload={"solver_candidate_id": "cand:spot_optimizer_001"},
        )
        outcome = gate.route(intent)
        assert outcome.approved is True
        approved = [
            e for e in audit
            if e["event_type"] == AuditEventType.INTENT_APPROVED.value
        ][0]
        assert approved["solver_candidate_id"] == "cand:spot_optimizer_001"
        assert approved["solver_manifest_sha256"] == "b" * 64

    def test_wrt_003_solver_write_denies_quarantined_provenance(self):
        audit = []
        gate = _make_gate(
            audit_collector=audit,
            states=self._setup_wrt_003_state(),
            connectors=self._setup_wrt_003_connector(),
            capsules=self._setup_wrt_003_capsule(),
            solver_provenance_result=VerificationResult(
                valid=False,
                candidate_id="cand:spot_optimizer_001",
                reasons=["solver_quarantined"],
                activation_state=ActivationState.QUARANTINED.value,
            ),
        )
        intent = _make_intent(
            target_state_ref="state:logbook",
            connector_ref="conn:tomcat_rest",
            tool_descriptor_id="tool_logbook_update",
            payload={"solver_candidate_id": "cand:spot_optimizer_001"},
        )
        outcome = gate.route(intent)
        assert outcome.approved is False
        assert "solver_quarantined" in (outcome.denial_reason or "")

    def test_wrt_003_no_connector_denies(self):
        audit = []
        gate = _make_gate(
            audit_collector=audit,
            states=self._setup_wrt_003_state(),
            capsules=self._setup_wrt_003_capsule(),
        )
        intent = _make_intent(
            target_state_ref="state:logbook",
            connector_ref=None,  # missing
            tool_descriptor_id="tool_logbook_update",
        )
        outcome = gate.route(intent)
        assert outcome.approved is False
        assert outcome.stop_condition == StopCondition.ANTI_PATTERN_VIOLATION

    def test_wrt_003_no_rollback_plan_denies(self):
        audit = []
        gate = _make_gate(
            audit_collector=audit,
            states=self._setup_wrt_003_state(),
            connectors=self._setup_wrt_003_connector(),
            # No capsule -> no rollback plan
        )
        intent = _make_intent(
            target_state_ref="state:logbook",
            connector_ref="conn:tomcat_rest",
            tool_descriptor_id="tool_logbook_update",
        )
        outcome = gate.route(intent)
        assert outcome.approved is False
        assert outcome.stop_condition == StopCondition.NO_ROLLBACK_PLAN

    def test_wrt_003_peer_rco_timeout_hard_stop(self):
        """Codex RCO edit #3: 30 min timeout = HARD STOP."""
        audit = []
        gate = _make_gate(
            audit_collector=audit,
            states=self._setup_wrt_003_state(),
            connectors=self._setup_wrt_003_connector(),
            capsules=self._setup_wrt_003_capsule(),
            peer_timed_out=True,
        )
        intent = _make_intent(
            target_state_ref="state:logbook",
            connector_ref="conn:tomcat_rest",
            tool_descriptor_id="tool_logbook_update",
        )
        outcome = gate.route(intent)
        assert outcome.approved is False
        assert outcome.stop_condition == StopCondition.PEER_RCO_TIMEOUT

    def test_wrt_003_peer_rco_nonconvergent_hard_stop(self):
        audit = []
        gate = _make_gate(
            audit_collector=audit,
            states=self._setup_wrt_003_state(),
            connectors=self._setup_wrt_003_connector(),
            capsules=self._setup_wrt_003_capsule(),
            peer_verdict="changes_requested",
            peer_rounds=3,
        )
        intent = _make_intent(
            target_state_ref="state:logbook",
            connector_ref="conn:tomcat_rest",
            tool_descriptor_id="tool_logbook_update",
        )
        outcome = gate.route(intent)
        assert outcome.approved is False
        assert outcome.stop_condition == StopCondition.PEER_RCO_NONCONVERGENT

    def test_wrt_003_unresolved_state_denies(self):
        """Codex RCO round-2 fix #1: WRT-003 with unresolved target_state_ref
        must deny fail-closed even when scope policy would auto-approve."""
        audit = []
        gate = _make_gate(
            audit_collector=audit,
            states={},  # state lookup returns None
            connectors=self._setup_wrt_003_connector(),
            capsules=self._setup_wrt_003_capsule(),
            scope_decision="auto_approved",
        )
        intent = _make_intent(
            target_state_ref="state:does_not_exist",
            connector_ref="conn:tomcat_rest",
            tool_descriptor_id="tool_logbook_update",
        )
        outcome = gate.route(intent)
        assert outcome.approved is False
        assert outcome.stop_condition == StopCondition.ANTI_PATTERN_VIOLATION
        assert "target_state_ref" in (outcome.denial_reason or "")
        # No INTENT_APPROVED event must have been emitted
        event_types = [e["event_type"] for e in audit]
        assert AuditEventType.INTENT_APPROVED.value not in event_types

    def test_wrt_003_action_not_in_write_modes_allowed_denies(self):
        """Codex RCO round-2 fix #2: WRT-003 with action='delete' against
        state.write_modes_allowed=['insert', 'update'] must deny fail-closed
        with ANTI_PATTERN_VIOLATION."""
        audit = []
        gate = _make_gate(
            audit_collector=audit,
            states=self._setup_wrt_003_state(),  # allows insert/update only
            connectors=self._setup_wrt_003_connector(),
            capsules=self._setup_wrt_003_capsule(),
            scope_decision="auto_approved",
        )
        intent = _make_intent(
            target_state_ref="state:logbook",
            connector_ref="conn:tomcat_rest",
            tool_descriptor_id="tool_logbook_update",
            action="delete",  # not in write_modes_allowed
        )
        outcome = gate.route(intent)
        assert outcome.approved is False
        assert outcome.stop_condition == StopCondition.ANTI_PATTERN_VIOLATION
        assert "write_modes_allowed" in (outcome.denial_reason or "")
        event_types = [e["event_type"] for e in audit]
        assert AuditEventType.INTENT_APPROVED.value not in event_types

    def test_wrt_003_scope_policy_denied(self):
        audit = []
        gate = _make_gate(
            audit_collector=audit,
            states=self._setup_wrt_003_state(),
            connectors=self._setup_wrt_003_connector(),
            capsules=self._setup_wrt_003_capsule(),
            scope_decision="denied",
            scope_reason="outside_pre_approved_scope",
        )
        intent = _make_intent(
            target_state_ref="state:logbook",
            connector_ref="conn:tomcat_rest",
            tool_descriptor_id="tool_logbook_update",
        )
        outcome = gate.route(intent)
        assert outcome.approved is False
        assert "outside_pre_approved_scope" in (outcome.denial_reason or "")


# --------------------------------------------------------------------------
# Execution + audit envelope
# --------------------------------------------------------------------------


class TestExecution:

    def test_execute_success_emits_effect_started_and_completed(self):
        audit = []
        gate = _make_gate(audit_collector=audit, states={
            "state:audit_log": StateInfo(
                state_id="state:audit_log",
                plane="magma_history",
                write_modes_allowed=["append"],
                sensitive_class="internal",
                single_writer_required=True,
            ),
        })
        intent = _make_intent(target_state_ref="state:audit_log")
        outcome = gate.route(intent)
        result = gate.execute(intent, outcome)
        assert result.success is True
        event_types = [e["event_type"] for e in audit]
        assert AuditEventType.EFFECT_STARTED.value in event_types
        assert AuditEventType.EFFECT_COMPLETED.value in event_types

    def test_execute_outcome_unknown_emits_outcome_unknown(self):
        audit = []
        gate = _make_gate(
            audit_collector=audit,
            states={
                "state:audit_log": StateInfo(
                    state_id="state:audit_log",
                    plane="magma_history",
                    write_modes_allowed=["append"],
                    sensitive_class="internal",
                    single_writer_required=True,
                ),
            },
            write_result=ExecutionResult(
                intent_id="x",  # gets overwritten by intent
                success=False,
                outcome_unknown=True,
                error_reason="network timeout",
            ),
        )
        intent = _make_intent(target_state_ref="state:audit_log")
        outcome = gate.route(intent)
        result = gate.execute(intent, outcome)
        event_types = [e["event_type"] for e in audit]
        assert AuditEventType.EFFECT_OUTCOME_UNKNOWN.value in event_types


# --------------------------------------------------------------------------
# Audit envelope content shape
# --------------------------------------------------------------------------


class TestAuditEnvelope:

    def test_audit_envelope_has_required_fields(self):
        audit = []
        gate = _make_gate(audit_collector=audit, states={
            "state:audit_log": StateInfo(
                state_id="state:audit_log",
                plane="magma_history",
                write_modes_allowed=["append"],
                sensitive_class="internal",
                single_writer_required=True,
            ),
        })
        intent = _make_intent(target_state_ref="state:audit_log")
        gate.route(intent)
        # First emitted event is INTENT_CLASSIFIED
        first = audit[0]
        for field in [
            "event_type", "intent_id", "agent_id", "session_id",
            "tool_descriptor_id", "target_state_ref", "action",
            "payload_hash", "ts_utc",
        ]:
            assert field in first
        assert first["payload_hash"] == intent.payload_hash
