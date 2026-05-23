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

import json
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
    build_gate_decision_card,
    build_rco_decision_artifact_for_gate,
    build_rco_decision_receipt_for_gate,
)
from waggledance.core.magma.canonical import sha256_digest
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
                solver_provenance_result: VerificationResult = None,
                receipt_bundles: list = None,
                receipt_emit=None,
                external_effect_approval_id: str | None = None,
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

    if receipt_emit is None and receipt_bundles is not None:
        def receipt_emit(bundle: dict) -> None:
            receipt_bundles.append(bundle)

    def resolve_external_effect_approval_id(
        intent: Intent,
        outcome: GateOutcome,
    ) -> str | None:
        return external_effect_approval_id

    return WriteRCOGate(
        audit_emit=_audit_emit_collector(audit_collector),
        classify_payload_credential_scan=cred_scan,
        fetch_connector_info=lambda cid: connectors.get(cid),
        fetch_state_info=lambda sid: states.get(sid),
        fetch_recovery_capsule=lambda tid: capsules.get(tid),
        peer_rco_solicit=peer,
        operator_scope_policy_check=scope,
        write_executor=writer,
        emit_receipt_bundle=receipt_emit,
        resolve_external_effect_approval_id=(
            resolve_external_effect_approval_id
            if external_effect_approval_id is not None
            else None
        ),
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

    def test_classify_informational_when_plane_informational_artifact(self):
        audit = []
        gate = _make_gate(audit_collector=audit, states={
            "state:advisory": StateInfo(
                state_id="state:advisory",
                plane="informational_artifact",
                write_modes_allowed=["insert"],
                sensitive_class="internal",
                single_writer_required=False,
            ),
        })
        intent = _make_intent(target_state_ref="state:advisory")
        assert gate.classify(intent) == WriteRiskClass.INFORMATIONAL

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
# Classification matrix -- parametrized expansion (tests-only, no source).
#
# Locks the full classify() dispatch table across (action, plane, connector
# write_risk, connector_present) combinations. The targeted rules from
# write_rco_gate.py classify():
#   Rule 1a: connector.write_risk == EXTERNAL_EFFECT -> EXTERNAL_EFFECT
#   Rule 1b: state.plane == "external_system" -> EXTERNAL_EFFECT
#   Rule 1c: action in {post,patch,put} and state.plane NOT in internal list
#            -> EXTERNAL_EFFECT
#   Rule 2:  state.plane == informational_artifact -> INFORMATIONAL
#   Rule 3:  state.plane in {filesystem_artifact, retrieval_data}
#            -> LOCAL_ARTIFACT
#   Rule 4:  state.plane in {magma_history, control_state, audit_projection}
#            -> INTERNAL_MEMORY
#   Default: conservative EXTERNAL_EFFECT
#
# Internal-plane list (per source line 312-314): magma_history, control_state,
# retrieval_data, filesystem_artifact, informational_artifact,
# audit_projection.
#
# Each row asserts the (intent, gate) -> expected_risk_class mapping. The
# names are precise enough to debug a single failure without re-reading the
# source.
# --------------------------------------------------------------------------


def _state(plane: str, *, write_modes_allowed: Optional[list] = None,
           state_id: str = "state:p") -> StateInfo:
    return StateInfo(
        state_id=state_id,
        plane=plane,
        write_modes_allowed=write_modes_allowed or ["insert"],
        sensitive_class="internal",
        single_writer_required=False,
    )


def _connector(write_risk: WriteRiskClass,
               *, connector_id: str = "conn:test") -> ConnectorInfo:
    return ConnectorInfo(
        connector_id=connector_id,
        write_risk=write_risk,
        auth_mode="oauth_pkce_user_visible",
        can_run_headless=True,
        rate_limit_max_workers=1,
        rate_limit_request_delay_s=0.0,
    )


CLASSIFY_MATRIX = [
    # ----- Rule 1a: connector.write_risk == EXTERNAL_EFFECT short-circuits
    # all other rules, even for internal-plane state.
    ("rule_1a__connector_external_overrides_internal_plane",
     "insert", "magma_history", WriteRiskClass.EXTERNAL_EFFECT, True,
     WriteRiskClass.EXTERNAL_EFFECT),
    ("rule_1a__connector_external_overrides_filesystem_plane",
     "insert", "filesystem_artifact", WriteRiskClass.EXTERNAL_EFFECT, True,
     WriteRiskClass.EXTERNAL_EFFECT),
    ("rule_1a__connector_external_overrides_informational_plane",
     "insert", "informational_artifact", WriteRiskClass.EXTERNAL_EFFECT, True,
     WriteRiskClass.EXTERNAL_EFFECT),
    ("rule_1a__connector_external_overrides_unknown_plane",
     "insert", None, WriteRiskClass.EXTERNAL_EFFECT, True,
     WriteRiskClass.EXTERNAL_EFFECT),
    # ----- Rule 1b: state.plane == "external_system" -> EXTERNAL_EFFECT
    # regardless of action.
    ("rule_1b__plane_external_system_with_insert",
     "insert", "external_system", None, False, WriteRiskClass.EXTERNAL_EFFECT),
    ("rule_1b__plane_external_system_with_update",
     "update", "external_system", None, False, WriteRiskClass.EXTERNAL_EFFECT),
    ("rule_1b__plane_external_system_with_delete",
     "delete", "external_system", None, False, WriteRiskClass.EXTERNAL_EFFECT),
    ("rule_1b__plane_external_system_with_post",
     "post", "external_system", None, False, WriteRiskClass.EXTERNAL_EFFECT),
    # ----- Rule 1c: HTTP action on plane NOT in internal list ->
    # EXTERNAL_EFFECT. browser_profile and external_readonly are NOT in
    # the internal list (see source line 312-314).
    ("rule_1c__post_on_browser_profile",
     "post", "browser_profile", None, False, WriteRiskClass.EXTERNAL_EFFECT),
    ("rule_1c__patch_on_browser_profile",
     "patch", "browser_profile", None, False, WriteRiskClass.EXTERNAL_EFFECT),
    ("rule_1c__put_on_browser_profile",
     "put", "browser_profile", None, False, WriteRiskClass.EXTERNAL_EFFECT),
    ("rule_1c__post_on_external_readonly",
     "post", "external_readonly", None, False,
     WriteRiskClass.EXTERNAL_EFFECT),
    # ----- Rule 1c NEGATIVE: HTTP action on plane IN internal list does
    # NOT trigger Rule 1c; falls through to Rule 2, 3, or 4.
    ("rule_1c_negative__post_on_filesystem_artifact_is_LOCAL",
     "post", "filesystem_artifact", None, False, WriteRiskClass.LOCAL_ARTIFACT),
    ("rule_1c_negative__post_on_retrieval_data_is_LOCAL",
     "post", "retrieval_data", None, False, WriteRiskClass.LOCAL_ARTIFACT),
    ("rule_1c_negative__post_on_informational_artifact_is_INFORMATIONAL",
     "post", "informational_artifact", None, False,
     WriteRiskClass.INFORMATIONAL),
    ("rule_1c_negative__post_on_magma_history_is_INTERNAL",
     "post", "magma_history", None, False, WriteRiskClass.INTERNAL_MEMORY),
    ("rule_1c_negative__patch_on_audit_projection_is_INTERNAL",
     "patch", "audit_projection", None, False, WriteRiskClass.INTERNAL_MEMORY),
    ("rule_1c_negative__put_on_control_state_is_INTERNAL",
     "put", "control_state", None, False, WriteRiskClass.INTERNAL_MEMORY),
    # ----- Rule 2: informational_artifact -> INFORMATIONAL for advisory
    # artifact writes.
    ("rule_2__insert_on_informational_artifact",
     "insert", "informational_artifact", None, False,
     WriteRiskClass.INFORMATIONAL),
    ("rule_2__append_on_informational_artifact",
     "append", "informational_artifact", None, False,
     WriteRiskClass.INFORMATIONAL),
    # ----- Rule 3: filesystem_artifact + retrieval_data both -> LOCAL_ARTIFACT
    # for non-HTTP actions.
    ("rule_3__insert_on_filesystem_artifact",
     "insert", "filesystem_artifact", None, False,
     WriteRiskClass.LOCAL_ARTIFACT),
    ("rule_3__update_on_filesystem_artifact",
     "update", "filesystem_artifact", None, False,
     WriteRiskClass.LOCAL_ARTIFACT),
    ("rule_3__delete_on_filesystem_artifact",
     "delete", "filesystem_artifact", None, False,
     WriteRiskClass.LOCAL_ARTIFACT),
    ("rule_3__append_on_filesystem_artifact",
     "append", "filesystem_artifact", None, False,
     WriteRiskClass.LOCAL_ARTIFACT),
    ("rule_3__insert_on_retrieval_data",
     "insert", "retrieval_data", None, False, WriteRiskClass.LOCAL_ARTIFACT),
    ("rule_3__update_on_retrieval_data",
     "update", "retrieval_data", None, False, WriteRiskClass.LOCAL_ARTIFACT),
    # ----- Rule 4: magma_history, control_state, audit_projection all
    # -> INTERNAL_MEMORY for non-HTTP actions.
    ("rule_4__insert_on_magma_history",
     "insert", "magma_history", None, False, WriteRiskClass.INTERNAL_MEMORY),
    ("rule_4__update_on_magma_history",
     "update", "magma_history", None, False, WriteRiskClass.INTERNAL_MEMORY),
    ("rule_4__delete_on_magma_history",
     "delete", "magma_history", None, False, WriteRiskClass.INTERNAL_MEMORY),
    ("rule_4__append_on_magma_history",
     "append", "magma_history", None, False, WriteRiskClass.INTERNAL_MEMORY),
    ("rule_4__insert_on_control_state",
     "insert", "control_state", None, False, WriteRiskClass.INTERNAL_MEMORY),
    ("rule_4__update_on_audit_projection",
     "update", "audit_projection", None, False,
     WriteRiskClass.INTERNAL_MEMORY),
    # ----- Conservative default: unresolved state, no connector -> EXTERNAL.
    # Also: HTTP action with no state at all -> EXTERNAL (rule 1c short-
    # circuits on `state and`).
    ("default__no_state_no_connector_insert",
     "insert", None, None, False, WriteRiskClass.EXTERNAL_EFFECT),
    ("default__no_state_no_connector_post",
     "post", None, None, False, WriteRiskClass.EXTERNAL_EFFECT),
    ("default__no_state_no_connector_delete",
     "delete", None, None, False, WriteRiskClass.EXTERNAL_EFFECT),
    # ----- Connector non-EXTERNAL write_risk: connector is consulted by
    # Rule 1a but does NOT match, so the rest of the dispatch runs.
    ("connector_local_artifact_does_not_short_circuit__plane_magma",
     "insert", "magma_history", WriteRiskClass.LOCAL_ARTIFACT, True,
     WriteRiskClass.INTERNAL_MEMORY),
    ("connector_internal_memory_does_not_short_circuit__plane_filesystem",
     "insert", "filesystem_artifact", WriteRiskClass.INTERNAL_MEMORY, True,
     WriteRiskClass.LOCAL_ARTIFACT),
    ("connector_local_artifact_does_not_short_circuit__plane_informational",
     "insert", "informational_artifact", WriteRiskClass.LOCAL_ARTIFACT, True,
     WriteRiskClass.INFORMATIONAL),
    ("connector_informational_does_not_short_circuit__plane_external_system",
     "insert", "external_system", WriteRiskClass.INFORMATIONAL, True,
     WriteRiskClass.EXTERNAL_EFFECT),
    # ----- Empty connector_ref means no fetch; same as no connector.
    ("empty_connector_ref_is_treated_as_none__plane_magma",
     "insert", "magma_history", None, False, WriteRiskClass.INTERNAL_MEMORY),
    ("empty_connector_ref_is_treated_as_none__plane_external_system",
     "insert", "external_system", None, False,
     WriteRiskClass.EXTERNAL_EFFECT),
]


@pytest.mark.parametrize(
    "label,action,plane,connector_risk,connector_present,expected",
    CLASSIFY_MATRIX,
    ids=[row[0] for row in CLASSIFY_MATRIX],
)
def test_classify_matrix(
    label: str,
    action: str,
    plane: Optional[str],
    connector_risk: Optional[WriteRiskClass],
    connector_present: bool,
    expected: WriteRiskClass,
) -> None:
    """Parametrized matrix expansion of WriteRCOGate.classify() dispatch.

    Covers all four risk classes and the conservative default across a
    grid of (action, state.plane, connector.write_risk, connector_present).
    Each row asserts one dispatch outcome and is named so a single failure
    points directly at the failing rule.
    """
    states: dict[str, StateInfo] = {}
    target_state_ref = "state:matrix"
    if plane is not None:
        states[target_state_ref] = _state(plane, state_id=target_state_ref)

    connectors: dict[str, ConnectorInfo] = {}
    connector_ref: Optional[str] = None
    if connector_present:
        assert connector_risk is not None, (
            f"row {label!r} declares connector_present=True but "
            "connector_risk=None; fixture invariant"
        )
        connector_ref = "conn:matrix"
        connectors[connector_ref] = _connector(connector_risk,
                                                 connector_id=connector_ref)

    gate = _make_gate(audit_collector=[], states=states,
                       connectors=connectors)
    intent = _make_intent(
        target_state_ref=target_state_ref,
        connector_ref=connector_ref,
        action=action,
    )
    assert gate.classify(intent) == expected, (
        f"row {label!r}: expected {expected.value}, "
        f"got {gate.classify(intent).value}"
    )


def test_classify_matrix_covers_all_risk_classes() -> None:
    """Sanity check on the parametrize matrix: every WriteRiskClass enum
    member appears as an expected outcome at least once. Guards against
    accidentally dropping a class when editing the matrix."""
    expected_classes = {row[5] for row in CLASSIFY_MATRIX}
    assert expected_classes >= {
        WriteRiskClass.INFORMATIONAL,
        WriteRiskClass.INTERNAL_MEMORY,
        WriteRiskClass.LOCAL_ARTIFACT,
        WriteRiskClass.EXTERNAL_EFFECT,
    }, (
        "classify() matrix should cover INFORMATIONAL, INTERNAL_MEMORY, "
        "LOCAL_ARTIFACT, and EXTERNAL_EFFECT outcomes"
    )


# --------------------------------------------------------------------------
# INFORMATIONAL advisory artifact route
# --------------------------------------------------------------------------


class TestInformationalRoute:

    def test_informational_artifact_happy_path_emits_classify_and_approved(self):
        audit = []
        gate = _make_gate(audit_collector=audit, states={
            "state:advisory": StateInfo(
                state_id="state:advisory",
                plane="informational_artifact",
                write_modes_allowed=["insert"],
                sensitive_class="internal",
                single_writer_required=False,
            ),
        })
        intent = _make_intent(target_state_ref="state:advisory")
        outcome = gate.route(intent)

        assert outcome.approved is True
        assert outcome.risk_class == WriteRiskClass.INFORMATIONAL
        event_types = [e["event_type"] for e in audit]
        assert AuditEventType.INTENT_CLASSIFIED.value in event_types
        assert AuditEventType.INTENT_APPROVED.value in event_types
        assert AuditEventType.PEER_RCO_REQUESTED.value not in event_types
        approved = [
            e for e in audit
            if e["event_type"] == AuditEventType.INTENT_APPROVED.value
        ][0]
        assert approved["risk_class"] == "informational"
        assert approved["target_plane"] == "informational_artifact"

    def test_informational_artifact_action_not_allowed_denies(self):
        audit = []
        gate = _make_gate(audit_collector=audit, states={
            "state:advisory": StateInfo(
                state_id="state:advisory",
                plane="informational_artifact",
                write_modes_allowed=["append"],
                sensitive_class="internal",
                single_writer_required=False,
            ),
        })
        intent = _make_intent(target_state_ref="state:advisory",
                              action="delete")
        outcome = gate.route(intent)

        assert outcome.approved is False
        assert outcome.stop_condition == StopCondition.ANTI_PATTERN_VIOLATION
        assert "write_modes_allowed" in (outcome.denial_reason or "")
        event_types = [e["event_type"] for e in audit]
        assert AuditEventType.INTENT_APPROVED.value not in event_types

    def test_informational_artifact_credential_pattern_scan_denies(self):
        audit = []
        gate = _make_gate(
            audit_collector=audit,
            states={
                "state:advisory": StateInfo(
                    state_id="state:advisory",
                    plane="informational_artifact",
                    write_modes_allowed=["insert"],
                    sensitive_class="internal",
                    single_writer_required=False,
                ),
            },
            cred_scan=_cred_scan_finds("api_key:sk-abcd1234"),
        )
        intent = _make_intent(target_state_ref="state:advisory",
                              payload={"contents": "...secret..."})
        outcome = gate.route(intent)

        assert outcome.approved is False
        assert outcome.stop_condition == StopCondition.ANTI_PATTERN_VIOLATION
        assert "ANTI-004" in (outcome.denial_reason or "")


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

    def test_wrt_003_peer_rco_changes_requested_emits_denied_audit(self):
        audit = []
        gate = _make_gate(
            audit_collector=audit,
            states=self._setup_wrt_003_state(),
            connectors=self._setup_wrt_003_connector(),
            capsules=self._setup_wrt_003_capsule(),
            peer_verdict="changes_requested",
            peer_rounds=1,
        )
        intent = _make_intent(
            target_state_ref="state:logbook",
            connector_ref="conn:tomcat_rest",
            tool_descriptor_id="tool_logbook_update",
        )

        outcome = gate.route(intent)

        assert outcome.approved is False
        assert outcome.stop_condition is None
        assert outcome.denial_reason == "peer RCO did not pass"
        event_types = [e["event_type"] for e in audit]
        assert AuditEventType.DENIED.value in event_types
        assert outcome.audit_event_ids[-1].startswith("evt_")

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
        assert outcome.stop_condition is None
        event_types = [e["event_type"] for e in audit]
        assert AuditEventType.DENIED.value in event_types


# --------------------------------------------------------------------------
# Operator decision cards
# --------------------------------------------------------------------------


class TestGateDecisionCard:

    def test_decision_card_for_scope_denial_explains_block_without_payload(self):
        audit = []
        gate = _make_gate(
            audit_collector=audit,
            states=TestExternalEffectRoute()._setup_wrt_003_state(),
            connectors=TestExternalEffectRoute()._setup_wrt_003_connector(),
            capsules=TestExternalEffectRoute()._setup_wrt_003_capsule(),
            scope_decision="denied",
            scope_reason="outside_pre_approved_scope",
        )
        intent = _make_intent(
            target_state_ref="state:logbook",
            connector_ref="conn:tomcat_rest",
            tool_descriptor_id="tool_logbook_update",
            payload={
                "body": "customer secret text",
                "api_key": "sk-should-not-appear",
            },
        )

        outcome = gate.route(intent)
        card = build_gate_decision_card(intent, outcome)

        assert card["schema_version"] == "write_gate_decision_card.v1"
        assert card["approved"] is False
        assert card["operator_status"] == "blocked_policy_denied"
        assert card["required_action"] == (
            "update_operator_scope_or_request_confirmation"
        )
        assert card["denial_reason"] == (
            "operator scope policy denied: outside_pre_approved_scope"
        )
        encoded = repr(card)
        assert "customer secret text" not in encoded
        assert "sk-should-not-appear" not in encoded
        assert card["payload_hash"] == intent.payload_hash

    def test_decision_card_for_no_rollback_stop_names_next_action(self):
        audit = []
        gate = _make_gate(
            audit_collector=audit,
            states=TestExternalEffectRoute()._setup_wrt_003_state(),
            connectors=TestExternalEffectRoute()._setup_wrt_003_connector(),
        )
        intent = _make_intent(
            target_state_ref="state:logbook",
            connector_ref="conn:tomcat_rest",
            tool_descriptor_id="tool_missing_capsule",
        )

        outcome = gate.route(intent)
        card = build_gate_decision_card(intent, outcome)

        assert card["approved"] is False
        assert card["operator_status"] == "blocked_stop_condition"
        assert card["stop_condition"] == StopCondition.NO_ROLLBACK_PLAN.value
        assert card["required_action"] == "attach_recovery_capsule"
        assert card["rollback_plan_ref"] == ""

    def test_decision_card_for_approved_external_effect_keeps_audit_refs(self):
        audit = []
        gate = _make_gate(
            audit_collector=audit,
            states=TestExternalEffectRoute()._setup_wrt_003_state(),
            connectors=TestExternalEffectRoute()._setup_wrt_003_connector(),
            capsules=TestExternalEffectRoute()._setup_wrt_003_capsule(),
        )
        intent = _make_intent(
            target_state_ref="state:logbook",
            connector_ref="conn:tomcat_rest",
            tool_descriptor_id="tool_logbook_update",
        )

        outcome = gate.route(intent)
        card = build_gate_decision_card(intent, outcome)

        assert card["approved"] is True
        assert card["operator_status"] == "approved"
        assert card["risk_class"] == WriteRiskClass.EXTERNAL_EFFECT.value
        assert card["required_action"] == "execute_with_rollback_plan"
        assert card["rollback_plan_ref"] == "recovery:logbook_update_v1"
        assert card["audit_event_ids"] == outcome.audit_event_ids


# --------------------------------------------------------------------------
# MAGMA RCO decision artifact adapter
# --------------------------------------------------------------------------


class TestRcoDecisionArtifactAdapter:

    def test_artifact_adapter_binds_payload_hash_without_raw_payload(self):
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
        intent = _make_intent(
            target_state_ref="state:audit_log",
            payload={"body": "secret operator text"},
        )

        outcome = gate.route(intent)
        artifact = build_rco_decision_artifact_for_gate(
            intent,
            outcome,
            ts_utc="2026-05-20T12:00:00Z",
        )

        assert artifact["rco_decision_version"] == (
            "magma.rco_decision_artifact.v0"
        )
        assert artifact["risk_class"] == WriteRiskClass.INTERNAL_MEMORY.value
        assert artifact["gate_decision"] == "allow"
        assert artifact["approved"] is True
        assert artifact["operator_required"] is False
        assert artifact["write_payload_digest"] == sha256_digest(intent.payload)
        assert artifact["intent_digest"].startswith("sha256:")
        assert "secret operator text" not in repr(artifact)
        assert outcome.rco_decision_artifact is not None
        assert outcome.rco_decision_digest == sha256_digest(
            outcome.rco_decision_artifact
        )
        assert "secret operator text" not in repr(outcome.rco_decision_artifact)

    def test_artifact_adapter_maps_policy_denial_to_refuse(self):
        audit = []
        gate = _make_gate(
            audit_collector=audit,
            states=TestExternalEffectRoute()._setup_wrt_003_state(),
            connectors=TestExternalEffectRoute()._setup_wrt_003_connector(),
            capsules=TestExternalEffectRoute()._setup_wrt_003_capsule(),
            scope_decision="denied",
            scope_reason="outside_pre_approved_scope",
        )
        intent = _make_intent(
            target_state_ref="state:logbook",
            connector_ref="conn:tomcat_rest",
            tool_descriptor_id="tool_logbook_update",
        )

        outcome = gate.route(intent)
        artifact = build_rco_decision_artifact_for_gate(
            intent,
            outcome,
            ts_utc="2026-05-20T12:00:00Z",
            scope_policy_decision="denied",
            peer_rco_verdict="pass",
        )

        assert artifact["gate_decision"] == "refuse"
        assert artifact["approved"] is False
        assert artifact["operator_required"] is True
        assert artifact["scope_policy_decision"] == "denied"
        assert "rco:denied" in artifact["reason_codes"]

    def test_artifact_adapter_maps_stop_condition_to_review(self):
        audit = []
        gate = _make_gate(
            audit_collector=audit,
            states=TestExternalEffectRoute()._setup_wrt_003_state(),
            connectors=TestExternalEffectRoute()._setup_wrt_003_connector(),
        )
        intent = _make_intent(
            target_state_ref="state:logbook",
            connector_ref="conn:tomcat_rest",
            tool_descriptor_id="tool_missing_capsule",
        )

        outcome = gate.route(intent)
        artifact = build_rco_decision_artifact_for_gate(
            intent,
            outcome,
            ts_utc="2026-05-20T12:00:00Z",
        )

        assert artifact["gate_decision"] == "review"
        assert artifact["approved"] is False
        assert artifact["operator_required"] is True
        assert artifact["stop_condition"] == StopCondition.NO_ROLLBACK_PLAN.value
        assert f"rco:stop:{StopCondition.NO_ROLLBACK_PLAN.value}" in (
            artifact["reason_codes"]
        )

    def test_route_receipt_binding_builds_payload_free_receipt(self):
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
        intent = _make_intent(
            target_state_ref="state:audit_log",
            action="append",
            payload={"body": "secret operator text"},
        )

        outcome = gate.route(intent)
        bundle = build_rco_decision_receipt_for_gate(
            intent,
            outcome,
            ts_utc="2026-05-20T12:00:00Z",
        )

        receipt = bundle["receipt"]
        evaluation = bundle["evaluation_result"]
        assert receipt["receipt_version"] == "magma.receipt.v1"
        assert receipt["rco_decision_digest"] == outcome.rco_decision_digest
        assert receipt["canonical_payload_digest"] == evaluation["target_digest"]
        assert evaluation["actual_gate"] == "allow"
        assert "secret operator text" not in json.dumps(bundle, sort_keys=True)

    def test_external_effect_receipt_binding_requires_approval_id(self):
        audit = []
        gate = _make_gate(
            audit_collector=audit,
            states=TestExternalEffectRoute()._setup_wrt_003_state(),
            connectors=TestExternalEffectRoute()._setup_wrt_003_connector(),
            capsules=TestExternalEffectRoute()._setup_wrt_003_capsule(),
        )
        intent = _make_intent(
            target_state_ref="state:logbook",
            connector_ref="conn:tomcat_rest",
            tool_descriptor_id="tool_logbook_update",
        )

        outcome = gate.route(intent)
        with pytest.raises(ValueError, match="requires approval_id"):
            build_rco_decision_receipt_for_gate(intent, outcome)

        bundle = build_rco_decision_receipt_for_gate(
            intent,
            outcome,
            ts_utc="2026-05-20T12:00:00Z",
            approval_id="approval:operator:001",
        )

        assert bundle["receipt"]["operator_gate_required"] is True
        assert bundle["receipt"]["approval_id"] == "approval:operator:001"
        assert bundle["evaluation_result"]["operator_required"] is True

    def test_route_receipt_sink_emits_payload_free_bundle(self):
        audit = []
        bundles = []
        gate = _make_gate(
            audit_collector=audit,
            receipt_bundles=bundles,
            states={
                "state:audit_log": StateInfo(
                    state_id="state:audit_log",
                    plane="magma_history",
                    write_modes_allowed=["append"],
                    sensitive_class="internal",
                    single_writer_required=True,
                ),
            },
        )
        intent = _make_intent(
            target_state_ref="state:audit_log",
            action="append",
            payload={"body": "secret operator text"},
        )

        outcome = gate.route(intent)

        assert outcome.approved is True
        assert len(bundles) == 1
        bundle = bundles[0]
        assert bundle["rco_decision_artifact"] == outcome.rco_decision_artifact
        assert bundle["receipt"]["rco_decision_digest"] == (
            outcome.rco_decision_digest
        )
        assert bundle["receipt"]["canonical_payload_digest"] == (
            bundle["evaluation_result"]["target_digest"]
        )
        assert "secret operator text" not in json.dumps(bundle, sort_keys=True)

    def test_route_receipt_sink_failure_blocks_route(self):
        audit = []

        def boom(_bundle: dict) -> None:
            raise RuntimeError("receipt boom")

        gate = _make_gate(
            audit_collector=audit,
            receipt_emit=boom,
            states={
                "state:audit_log": StateInfo(
                    state_id="state:audit_log",
                    plane="magma_history",
                    write_modes_allowed=["append"],
                    sensitive_class="internal",
                    single_writer_required=True,
                ),
            },
        )
        intent = _make_intent(target_state_ref="state:audit_log", action="append")

        outcome = gate.route(intent)

        assert outcome.approved is False
        assert outcome.stop_condition == StopCondition.AUDIT_WRITE_FAILED
        assert "receipt emit failed: receipt boom" in (
            outcome.denial_reason or ""
        )

    def test_external_effect_receipt_sink_requires_approval_id_resolver(self):
        audit = []
        bundles = []
        gate = _make_gate(
            audit_collector=audit,
            receipt_bundles=bundles,
            states=TestExternalEffectRoute()._setup_wrt_003_state(),
            connectors=TestExternalEffectRoute()._setup_wrt_003_connector(),
            capsules=TestExternalEffectRoute()._setup_wrt_003_capsule(),
        )
        intent = _make_intent(
            target_state_ref="state:logbook",
            connector_ref="conn:tomcat_rest",
            tool_descriptor_id="tool_logbook_update",
        )

        outcome = gate.route(intent)

        assert outcome.approved is False
        assert outcome.stop_condition == StopCondition.AUDIT_WRITE_FAILED
        assert "resolve_external_effect_approval_id" in (
            outcome.denial_reason or ""
        )
        assert bundles == []

    def test_external_effect_receipt_sink_uses_approval_id_resolver(self):
        audit = []
        bundles = []
        gate = _make_gate(
            audit_collector=audit,
            receipt_bundles=bundles,
            external_effect_approval_id="approval:operator:001",
            states=TestExternalEffectRoute()._setup_wrt_003_state(),
            connectors=TestExternalEffectRoute()._setup_wrt_003_connector(),
            capsules=TestExternalEffectRoute()._setup_wrt_003_capsule(),
        )
        intent = _make_intent(
            target_state_ref="state:logbook",
            connector_ref="conn:tomcat_rest",
            tool_descriptor_id="tool_logbook_update",
        )

        outcome = gate.route(intent)

        assert outcome.approved is True
        assert len(bundles) == 1
        assert bundles[0]["receipt"]["operator_gate_required"] is True
        assert bundles[0]["receipt"]["approval_id"] == "approval:operator:001"


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

    def test_execute_effect_started_audit_failure_returns_typed_result(self):
        audit = []
        gate = _make_gate(audit_collector=audit)
        executed = []

        def audit_emit(envelope: dict) -> str:
            if envelope["event_type"] == AuditEventType.EFFECT_STARTED.value:
                raise RuntimeError("audit down")
            audit.append(envelope)
            return f"evt_{len(audit):04d}"

        gate.audit_emit = audit_emit
        gate.write_executor = lambda intent: (
            executed.append(intent)
            or ExecutionResult(intent_id=intent.intent_id, success=True)
        )
        intent = _make_intent()
        outcome = GateOutcome(
            intent_id=intent.intent_id,
            risk_class=WriteRiskClass.INTERNAL_MEMORY,
            approved=True,
        )

        result = gate.execute(intent, outcome)

        assert result.success is False
        assert result.outcome_unknown is True
        assert "audit emit failed: audit down" in (result.error_reason or "")
        assert executed == []

    def test_execute_effect_completed_audit_failure_returns_typed_result(self):
        audit = []
        gate = _make_gate(audit_collector=audit)
        executed = []

        def audit_emit(envelope: dict) -> str:
            if envelope["event_type"] == AuditEventType.EFFECT_COMPLETED.value:
                raise RuntimeError("audit down")
            audit.append(envelope)
            return f"evt_{len(audit):04d}"

        gate.audit_emit = audit_emit
        gate.write_executor = lambda intent: (
            executed.append(intent)
            or ExecutionResult(
                intent_id=intent.intent_id,
                success=True,
                elapsed_ms=7,
            )
        )
        intent = _make_intent()
        outcome = GateOutcome(
            intent_id=intent.intent_id,
            risk_class=WriteRiskClass.INTERNAL_MEMORY,
            approved=True,
        )

        result = gate.execute(intent, outcome)

        assert result.success is False
        assert result.outcome_unknown is True
        assert result.elapsed_ms == 7
        assert "audit emit failed: audit down" in (result.error_reason or "")
        assert executed == [intent]


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
