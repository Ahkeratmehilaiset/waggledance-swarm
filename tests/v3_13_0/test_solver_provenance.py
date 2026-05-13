# SPDX-License-Identifier: BUSL-1.1
"""Tests for SolverProvenance v1.

Covers acceptance criteria from solver_rco_provenance_signing_spec.md:
* Successful signing chain (owner + peer + verify)
* Rejected signing chain (mismatched manifest_hash)
* Operator confirm path (sensitive domain)
* Auto-quarantine (5 consecutive divergent runs)
* Permanent revocation
* automatic_drift_detection cannot permanent-revoke (only quarantine)
* Explicit signing_role validation (spec edit E13)
* Bridge payload uses kind=solver (spec edit E16)
* No personal data in fixtures
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from waggledance.core.v3_13_0.solver_provenance import (
    ActivationState,
    ProvenanceSignature,
    RevocationActor,
    SigningRole,
    SolverCandidateRecord,
    SolverProvenance,
    canonicalize_manifest,
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _make_candidate(*, candidate_id: str = "cand:demo",
                      target_domain: str = "DOM-006",
                      target_write_risk: str = "external_effect"
                      ) -> SolverCandidateRecord:
    canonical, digest = canonicalize_manifest({
        "candidate_id": candidate_id,
        "template_family": "RecordReconciler",
        "version": 1,
    })
    return SolverCandidateRecord(
        candidate_id=candidate_id,
        manifest_canonical_json=canonical,
        manifest_sha256=digest,
        target_domain=target_domain,
        target_write_risk=target_write_risk,
    )


def _emit_collector(collector: list):
    def emit(envelope: dict) -> str:
        envelope_id = f"evt_{len(collector):04d}"
        envelope["__id"] = envelope_id
        collector.append(envelope)
        return envelope_id
    return emit


def _bridge_collector(collector: list):
    def emit(envelope: dict) -> None:
        collector.append(envelope)
    return emit


def _make_provenance(*, candidate: SolverCandidateRecord,
                      events: list = None,
                      bridge_events: list = None,
                      scope_active: bool = True
                      ) -> tuple[SolverProvenance, dict[str, SolverCandidateRecord]]:
    store: dict[str, SolverCandidateRecord] = {candidate.candidate_id: candidate}
    events = events if events is not None else []
    bridge_events = bridge_events if bridge_events is not None else []
    prov = SolverProvenance(
        fetch_candidate=lambda cid: store.get(cid),
        update_candidate=lambda rec: store.__setitem__(rec.candidate_id, rec),
        emit_magma_event=_emit_collector(events),
        emit_bridge_event=_bridge_collector(bridge_events),
        operator_scope_policy_active=lambda _ref: scope_active,
    )
    return prov, store


# --------------------------------------------------------------------------
# Canonical manifest
# --------------------------------------------------------------------------


class TestCanonicalManifest:

    def test_canonical_json_is_sorted_no_whitespace(self):
        canon, _digest = canonicalize_manifest({"b": 2, "a": 1})
        assert canon == '{"a":1,"b":2}'

    def test_digest_is_stable_across_key_order(self):
        _c1, d1 = canonicalize_manifest({"a": 1, "b": 2})
        _c2, d2 = canonicalize_manifest({"b": 2, "a": 1})
        assert d1 == d2


# --------------------------------------------------------------------------
# Successful owner + peer signing
# --------------------------------------------------------------------------


class TestSigningChain:

    def test_owner_then_peer_signing_yields_signed_state(self):
        cand = _make_candidate()
        prov, store = _make_provenance(candidate=cand)
        prov.sign(
            candidate_id=cand.candidate_id,
            signing_agent_id="claude",
            signing_role=SigningRole.OWNER.value,
            bridge_event_ref="bridge:evt_owner_1",
            operator_scope_policy_ref="policy:home_factory",
        )
        prov.sign(
            candidate_id=cand.candidate_id,
            signing_agent_id="codex",
            signing_role=SigningRole.PEER.value,
            bridge_event_ref="bridge:evt_peer_1",
            operator_scope_policy_ref="policy:home_factory",
        )
        assert store[cand.candidate_id].activation_state == \
            ActivationState.SIGNED.value
        result = prov.verify_solver_provenance(cand.candidate_id)
        assert result.valid is True
        assert result.has_owner_signature is True
        assert result.has_peer_signature is True

    def test_invalid_signing_role_rejected(self):
        """Spec edit E13: signing_role must be EXPLICIT and valid."""
        cand = _make_candidate()
        prov, _ = _make_provenance(candidate=cand)
        with pytest.raises(ValueError, match="signing_role"):
            prov.sign(
                candidate_id=cand.candidate_id,
                signing_agent_id="claude",
                signing_role="reviewer",       # not a valid role
                bridge_event_ref="bridge:x",
                operator_scope_policy_ref="policy:home",
            )

    def test_unknown_candidate_raises_keyerror(self):
        cand = _make_candidate()
        prov, _ = _make_provenance(candidate=cand)
        with pytest.raises(KeyError):
            prov.sign(
                candidate_id="cand:does_not_exist",
                signing_agent_id="claude",
                signing_role=SigningRole.OWNER.value,
                bridge_event_ref="b",
                operator_scope_policy_ref="policy:home",
            )

    def test_revoked_scope_policy_refuses_signing(self):
        cand = _make_candidate()
        prov, _ = _make_provenance(candidate=cand, scope_active=False)
        with pytest.raises(PermissionError):
            prov.sign(
                candidate_id=cand.candidate_id,
                signing_agent_id="claude",
                signing_role=SigningRole.OWNER.value,
                bridge_event_ref="bridge:x",
                operator_scope_policy_ref="policy:revoked",
            )

    def test_round_number_exceeded_rejected(self):
        cand = _make_candidate()
        prov, _ = _make_provenance(candidate=cand)
        with pytest.raises(ValueError, match="round_number"):
            prov.sign(
                candidate_id=cand.candidate_id,
                signing_agent_id="claude",
                signing_role=SigningRole.OWNER.value,
                bridge_event_ref="b",
                operator_scope_policy_ref="policy:home",
                round_number=4,
            )


# --------------------------------------------------------------------------
# Manifest-hash mismatch invalidates chain
# --------------------------------------------------------------------------


class TestManifestHashMismatch:

    def test_manifest_hash_change_invalidates_existing_signatures(self):
        cand = _make_candidate()
        prov, store = _make_provenance(candidate=cand)
        prov.sign(candidate_id=cand.candidate_id,
                    signing_agent_id="claude",
                    signing_role=SigningRole.OWNER.value,
                    bridge_event_ref="b1",
                    operator_scope_policy_ref="policy:home")
        prov.sign(candidate_id=cand.candidate_id,
                    signing_agent_id="codex",
                    signing_role=SigningRole.PEER.value,
                    bridge_event_ref="b2",
                    operator_scope_policy_ref="policy:home")
        # Mutate the candidate's manifest hash (as if manifest changed)
        store[cand.candidate_id] = replace(
            store[cand.candidate_id],
            manifest_sha256="0" * 64,
        )
        # Note: existing signatures retain their original manifest_sha256;
        # they no longer match the current candidate hash.
        result = prov.verify_solver_provenance(cand.candidate_id)
        assert result.valid is False
        assert any("manifest_hash_mismatch" in r for r in result.reasons)


# --------------------------------------------------------------------------
# Sensitive domain requires operator signature
# --------------------------------------------------------------------------


class TestSensitiveDomainOperatorRequired:

    def test_dom_015_owner_plus_peer_alone_not_enough(self):
        cand = _make_candidate(target_domain="DOM-015")
        prov, store = _make_provenance(candidate=cand)
        prov.sign(candidate_id=cand.candidate_id,
                    signing_agent_id="claude",
                    signing_role=SigningRole.OWNER.value,
                    bridge_event_ref="b1",
                    operator_scope_policy_ref="policy:home")
        prov.sign(candidate_id=cand.candidate_id,
                    signing_agent_id="codex",
                    signing_role=SigningRole.PEER.value,
                    bridge_event_ref="b2",
                    operator_scope_policy_ref="policy:home")
        result = prov.verify_solver_provenance(cand.candidate_id)
        assert result.valid is False
        assert "sensitive_domain_requires_operator" in result.reasons
        # State remains AWAITING_SIGNING until operator signs
        assert store[cand.candidate_id].activation_state == \
            ActivationState.AWAITING_SIGNING.value

    def test_dom_015_with_operator_signature_passes(self):
        cand = _make_candidate(target_domain="DOM-015")
        prov, store = _make_provenance(candidate=cand)
        for role, agent in [
            (SigningRole.OWNER.value, "claude"),
            (SigningRole.PEER.value, "codex"),
            (SigningRole.OPERATOR.value, "operator"),
        ]:
            prov.sign(candidate_id=cand.candidate_id,
                        signing_agent_id=agent,
                        signing_role=role,
                        bridge_event_ref=f"b:{role}",
                        operator_scope_policy_ref="policy:home")
        result = prov.verify_solver_provenance(cand.candidate_id)
        assert result.valid is True
        assert result.has_operator_signature is True
        assert store[cand.candidate_id].activation_state == \
            ActivationState.SIGNED.value


# --------------------------------------------------------------------------
# Auto-quarantine (spec edit E15)
# --------------------------------------------------------------------------


class TestAutoQuarantine:

    def test_five_consecutive_divergent_runs_quarantine(self):
        cand = _make_candidate()
        prov, store = _make_provenance(candidate=cand)
        # Land owner + peer signatures first
        prov.sign(candidate_id=cand.candidate_id,
                    signing_agent_id="claude",
                    signing_role=SigningRole.OWNER.value,
                    bridge_event_ref="b1",
                    operator_scope_policy_ref="policy:home")
        prov.sign(candidate_id=cand.candidate_id,
                    signing_agent_id="codex",
                    signing_role=SigningRole.PEER.value,
                    bridge_event_ref="b2",
                    operator_scope_policy_ref="policy:home")
        prov.activate(candidate_id=cand.candidate_id)
        assert store[cand.candidate_id].activation_state == \
            ActivationState.ACTIVATED.value

        # 4 consecutive divergent runs -> not yet quarantined
        for i in range(4):
            state = prov.record_run_result(
                candidate_id=cand.candidate_id,
                divergence_score=0.5,
                evidence_ref=f"art:div_{i}",
            )
            assert state == ActivationState.ACTIVATED
        # 5th tips into quarantine
        state = prov.record_run_result(
            candidate_id=cand.candidate_id,
            divergence_score=0.5,
            evidence_ref="art:div_4",
        )
        assert state == ActivationState.QUARANTINED
        assert len(store[cand.candidate_id].quarantine_evidence_refs) == 5

    def test_below_threshold_run_resets_counter(self):
        cand = _make_candidate()
        prov, store = _make_provenance(candidate=cand)
        prov.sign(candidate_id=cand.candidate_id,
                    signing_agent_id="claude",
                    signing_role=SigningRole.OWNER.value,
                    bridge_event_ref="b1",
                    operator_scope_policy_ref="policy:home")
        prov.sign(candidate_id=cand.candidate_id,
                    signing_agent_id="codex",
                    signing_role=SigningRole.PEER.value,
                    bridge_event_ref="b2",
                    operator_scope_policy_ref="policy:home")
        prov.activate(candidate_id=cand.candidate_id)
        for _ in range(4):
            prov.record_run_result(
                candidate_id=cand.candidate_id,
                divergence_score=0.5,
                evidence_ref="art:d",
            )
        # One good run resets the counter
        state = prov.record_run_result(
            candidate_id=cand.candidate_id,
            divergence_score=0.03,
            evidence_ref="art:ok",
        )
        assert state == ActivationState.ACTIVATED
        assert store[cand.candidate_id].consecutive_divergent_runs == 0


# --------------------------------------------------------------------------
# Permanent revocation
# --------------------------------------------------------------------------


class TestRevocation:

    def test_operator_revoke_marks_revoked_and_emits_audit(self):
        cand = _make_candidate()
        events = []
        prov, store = _make_provenance(candidate=cand, events=events)
        prov.sign(candidate_id=cand.candidate_id,
                    signing_agent_id="claude",
                    signing_role=SigningRole.OWNER.value,
                    bridge_event_ref="b1",
                    operator_scope_policy_ref="policy:home")
        result = prov.revoke(candidate_id=cand.candidate_id,
                                reason="superseded by v2")
        assert result.success is True
        assert result.new_state == ActivationState.REVOKED.value
        assert store[cand.candidate_id].activation_state == \
            ActivationState.REVOKED.value
        types = [e["event_type"] for e in events]
        assert "solver.activation_revoked" in types

    def test_automatic_drift_detection_cannot_permanent_revoke(self):
        """Spec edit E15: automatic detection only quarantines.
        Permanent revoke is operator-driven."""
        cand = _make_candidate()
        prov, store = _make_provenance(candidate=cand)
        result = prov.revoke(
            candidate_id=cand.candidate_id,
            reason="drift detected",
            revoked_by=RevocationActor.AUTOMATIC_DRIFT_DETECTION.value,
        )
        assert result.success is False
        assert "automatic_drift_detection" in result.reason
        assert store[cand.candidate_id].activation_state != \
            ActivationState.REVOKED.value

    def test_revoked_solver_fails_verification(self):
        cand = _make_candidate()
        prov, _ = _make_provenance(candidate=cand)
        prov.sign(candidate_id=cand.candidate_id,
                    signing_agent_id="claude",
                    signing_role=SigningRole.OWNER.value,
                    bridge_event_ref="b1",
                    operator_scope_policy_ref="policy:home")
        prov.sign(candidate_id=cand.candidate_id,
                    signing_agent_id="codex",
                    signing_role=SigningRole.PEER.value,
                    bridge_event_ref="b2",
                    operator_scope_policy_ref="policy:home")
        prov.revoke(candidate_id=cand.candidate_id, reason="bad")
        result = prov.verify_solver_provenance(cand.candidate_id)
        assert result.valid is False
        assert "solver_revoked" in result.reasons


# --------------------------------------------------------------------------
# Bridge schema compatibility (spec edit E16)
# --------------------------------------------------------------------------


class TestBridgeSchemaCompatibility:

    def test_bridge_event_uses_existing_handoff_type_with_kind_solver(self):
        cand = _make_candidate()
        bridge_events = []
        prov, _ = _make_provenance(candidate=cand,
                                      bridge_events=bridge_events)
        prov.sign(candidate_id=cand.candidate_id,
                    signing_agent_id="claude",
                    signing_role=SigningRole.OWNER.value,
                    bridge_event_ref="b1",
                    operator_scope_policy_ref="policy:home")
        assert len(bridge_events) == 1
        evt = bridge_events[0]
        # Per spec edit E16: existing bridge type + payload.kind=solver
        # (NOT invented dotted bridge types)
        assert evt["type"] == "handoff"
        assert evt["payload"]["kind"] == "solver"
        assert evt["payload"]["signing_role"] == SigningRole.OWNER.value
        # signing_role is EXPLICIT in payload (spec edit E13)
        assert "signing_role" in evt["payload"]

    def test_revocation_bridge_event_uses_existing_decision_type(self):
        cand = _make_candidate()
        bridge_events = []
        prov, _ = _make_provenance(candidate=cand,
                                      bridge_events=bridge_events)
        prov.sign(candidate_id=cand.candidate_id,
                    signing_agent_id="claude",
                    signing_role=SigningRole.OWNER.value,
                    bridge_event_ref="b1",
                    operator_scope_policy_ref="policy:home")
        prov.revoke(candidate_id=cand.candidate_id, reason="bad")
        decision_events = [e for e in bridge_events
                            if e["type"] == "decision"]
        assert decision_events
        evt = decision_events[0]
        assert evt["status"] == "activation_revoked"
        assert evt["payload"]["kind"] == "solver"


# --------------------------------------------------------------------------
# Activation gates
# --------------------------------------------------------------------------


class TestActivation:

    def test_activate_requires_valid_chain(self):
        cand = _make_candidate()
        prov, store = _make_provenance(candidate=cand)
        # No signatures -> activate refuses
        state = prov.activate(candidate_id=cand.candidate_id)
        assert state != ActivationState.ACTIVATED

    def test_activate_succeeds_when_signed(self):
        cand = _make_candidate()
        prov, store = _make_provenance(candidate=cand)
        prov.sign(candidate_id=cand.candidate_id,
                    signing_agent_id="claude",
                    signing_role=SigningRole.OWNER.value,
                    bridge_event_ref="b1",
                    operator_scope_policy_ref="policy:home")
        prov.sign(candidate_id=cand.candidate_id,
                    signing_agent_id="codex",
                    signing_role=SigningRole.PEER.value,
                    bridge_event_ref="b2",
                    operator_scope_policy_ref="policy:home")
        state = prov.activate(candidate_id=cand.candidate_id)
        assert state == ActivationState.ACTIVATED
        assert store[cand.candidate_id].activation_state == \
            ActivationState.ACTIVATED.value

    def test_revoked_candidate_cannot_activate(self):
        cand = _make_candidate()
        prov, _ = _make_provenance(candidate=cand)
        prov.sign(candidate_id=cand.candidate_id,
                    signing_agent_id="claude",
                    signing_role=SigningRole.OWNER.value,
                    bridge_event_ref="b1",
                    operator_scope_policy_ref="policy:home")
        prov.sign(candidate_id=cand.candidate_id,
                    signing_agent_id="codex",
                    signing_role=SigningRole.PEER.value,
                    bridge_event_ref="b2",
                    operator_scope_policy_ref="policy:home")
        prov.revoke(candidate_id=cand.candidate_id, reason="bad")
        state = prov.activate(candidate_id=cand.candidate_id)
        assert state == ActivationState.REVOKED
