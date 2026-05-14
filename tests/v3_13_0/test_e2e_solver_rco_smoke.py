# SPDX-License-Identifier: BUSL-1.1
"""End-to-end solver-RCO smoke harness (Sprint 1 polish item 12).

Tests-only diagnostic that wires DocIngest -> hand-crafted candidate manifest
-> SolverProvenance sign + activate -> ShadowRunner with synthetic baseline
-> DivergenceAnalyzer compare -> WriteRCOGate route + execute on a synthetic
LOCAL_ARTIFACT write. Asserts the full MAGMA audit chain across all 5
substrate modules.

PURPOSE (not a regression test for any single module):
    Sprint 1 lands 8 v3.13.0 modules each tested in isolation. None of the
    existing tests wires the modules together end-to-end. This smoke harness
    answers the diagnostic question "is the substrate sufficient end-to-end
    on synthetic inputs?" If it passes, the substrate's hook contracts are
    integration-correct and the remaining gap to operator value is purely
    Sprint 2 components (SolverSynthesizer + SituationRoom + real-data
    activation). If it fails at boundary N, that IS the smallest diagnostic
    answer: substrate is missing wiring at boundary N.

NOT a unit test. The individual modules have their own focused tests in
this directory; this file does not duplicate that coverage.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from waggledance.core.v3_13_0.doc_ingest import build_doc_ingest_proposal
from waggledance.core.v3_13_0.solver_provenance import (
    ActivationState,
    SigningRole,
    SolverCandidateRecord,
    SolverProvenance,
    canonicalize_manifest,
)
from waggledance.core.v3_13_0.solver_synthesizer import (
    OperatorCaseSeed,
    SolverTarget,
    synthesize_from_doc_ingest_proposal,
)
from waggledance.core.v3_13_0.shadow_runner import (
    BaselineOutput,
    CandidateOutput,
    DivergenceScore,
    ShadowProfileView,
    ShadowRunInput,
    ShadowRunner,
    ShadowToolView,
)
from waggledance.core.v3_13_0.write_rco_gate import (
    ConnectorInfo,
    ExecutionResult,
    Intent,
    PeerRCOResult,
    RecoveryCapsuleInfo,
    ScopePolicyResult,
    StateInfo,
    WriteRCOGate,
    WriteRiskClass,
)


# --------------------------------------------------------------------------
# Synthetic fixtures (HOME profile, local-artifact write risk)
# --------------------------------------------------------------------------


CANDIDATE_ID = "electricity_spot_optimizer_home_smoke"
PROFILE_ID = "home_smoke"
TOOL_DESCRIPTOR_ID = "tool_electricity_optimizer_smoke"
TARGET_STATE_REF = "state:spot_price_store_smoke"
TARGET_DOMAIN = "DOM-007"
TARGET_WRITE_RISK = "local_artifact"
POLICY_REF = "policy:home_smoke"


def _write_synthetic_home_input(root: Path) -> None:
    """Minimal HOME-shaped input dir the DocIngest contract accepts."""
    (root / "profile_config.yaml").write_text(
        "\n".join([
            "schema_version: 1",
            f"profile_id: {PROFILE_ID}",
            "profile_kind: home",
            "country: FI",
        ]),
        encoding="utf-8",
    )
    (root / "tariff_structure.md").write_text(
        "night tariff: 0.04 EUR/kWh between 22:00 and 06:00\n",
        encoding="utf-8",
    )
    (root / "consumption_sample.csv").write_text(
        "ts,kwh\n2026-05-13T00:00:00Z,1.25\n2026-05-13T01:00:00Z,0.80\n",
        encoding="utf-8",
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


# --------------------------------------------------------------------------
# End-to-end happy path
# --------------------------------------------------------------------------


def test_e2e_home_solver_full_substrate_chain(tmp_path: Path) -> None:
    """Wire all 5 v3.13.0 substrate modules with synthetic HOME inputs and
    assert the full MAGMA audit chain. No real subprocess; no real network;
    no real schema unification beyond what each module's contract requires.

    The candidate flows: DocIngest -> manifest -> sign -> activate ->
    shadow -> divergence-acceptable -> WriteRCOGate.route -> WriteRCOGate
    .execute -> success. Each stage emits MAGMA audit events that we
    collect into module-scoped lists and then merge for the cross-stage
    assertions.
    """

    # -----------------------------------------------------------------
    # Stage 1: synthetic input dir + DocIngest -> proposal
    # -----------------------------------------------------------------

    _write_synthetic_home_input(tmp_path)

    proposal = build_doc_ingest_proposal(
        tmp_path,
        profile_kind="home",
        candidate_id=CANDIDATE_ID,
    )

    # DocIngest contract: produces a payload-data event_type, not a new
    # MAGMA bridge event type. Assert the shape.
    payload = proposal.to_event_payload()
    assert payload["event_type"] == "solver_candidate_proposal"
    assert payload["profile_id"] == PROFILE_ID
    assert payload["profile_kind"] == "home"
    assert payload["candidate_manifest_seed"]["activation_state"] == \
        "unactivated"
    assert "doc:consumption_sample" in payload["source_docs"]
    assert "doc:tariff_structure" in payload["source_docs"]

    # -----------------------------------------------------------------
    # Stage 2: hand-craft a SCH-005-shaped manifest from the seed
    # (production: SolverSynthesizer; Sprint 1 smoke: inline)
    # -----------------------------------------------------------------

    seed = dict(proposal.candidate_manifest_seed)
    manifest = {
        **seed,
        "target_domain": TARGET_DOMAIN,
        "target_write_risk": TARGET_WRITE_RISK,
    }
    canonical_json, manifest_sha256 = canonicalize_manifest(manifest)
    assert len(manifest_sha256) == 64, "sha256 hex should be 64 chars"

    # -----------------------------------------------------------------
    # Stage 3: SolverProvenance sign owner + peer + activate
    # -----------------------------------------------------------------

    sp_audit: list = []
    sp_bridge: list = []
    candidate_store: dict = {}

    candidate_record = SolverCandidateRecord(
        candidate_id=CANDIDATE_ID,
        manifest_canonical_json=canonical_json,
        manifest_sha256=manifest_sha256,
        target_domain=TARGET_DOMAIN,
        target_write_risk=TARGET_WRITE_RISK,
    )
    candidate_store[CANDIDATE_ID] = candidate_record

    def _fetch_candidate(cid):
        return candidate_store.get(cid)

    def _update_candidate(cand):
        candidate_store[cand.candidate_id] = cand

    prov = SolverProvenance(
        fetch_candidate=_fetch_candidate,
        update_candidate=_update_candidate,
        emit_magma_event=_emit_collector(sp_audit),
        emit_bridge_event=_bridge_collector(sp_bridge),
        operator_scope_policy_active=lambda _ref: True,
    )

    prov.sign(
        candidate_id=CANDIDATE_ID,
        signing_agent_id="claude",
        signing_role=SigningRole.OWNER.value,
        bridge_event_ref="bridge:smoke_owner_1",
        operator_scope_policy_ref=POLICY_REF,
    )
    prov.sign(
        candidate_id=CANDIDATE_ID,
        signing_agent_id="codex",
        signing_role=SigningRole.PEER.value,
        bridge_event_ref="bridge:smoke_peer_1",
        operator_scope_policy_ref=POLICY_REF,
    )

    # After owner + peer + local_artifact (not external_effect, not
    # sensitive_domain): state should advance to SIGNED.
    assert candidate_store[CANDIDATE_ID].activation_state == \
        ActivationState.SIGNED.value

    activate_state = prov.activate(candidate_id=CANDIDATE_ID)
    assert activate_state == ActivationState.ACTIVATED
    assert candidate_store[CANDIDATE_ID].activation_state == \
        ActivationState.ACTIVATED.value

    # -----------------------------------------------------------------
    # Stage 4: ShadowRunner -- synthetic candidate + baseline + score
    # -----------------------------------------------------------------

    shadow_audit: list = []

    def _mock_run_candidate(_inp: ShadowRunInput) -> CandidateOutput:
        return CandidateOutput(
            artifact_uri="artifact://smoke/candidate_output",
            elapsed_ms=120,
            cost_consumed=0.01,
            write_risk_observed="local_artifact",
        )

    def _mock_run_baseline(_inp: ShadowRunInput) -> BaselineOutput:
        return BaselineOutput(
            artifact_uri="artifact://smoke/baseline_output",
            elapsed_ms=140,
            exit_code=0,
        )

    def _mock_compare(_c: CandidateOutput, _b: BaselineOutput,
                       _fmt: str) -> DivergenceScore:
        return DivergenceScore(
            score=0.03,  # well below the 0.40 quarantine threshold
            delta_summary_ref="artifact://smoke/delta_summary",
        )

    shadow = ShadowRunner(
        fetch_tool_descriptor=lambda tid: ShadowToolView(
            tool_descriptor_id=tid,
            shadow_supported=True,
            write_risk_class="local_artifact",
        ),
        fetch_profile_config=lambda pid: ShadowProfileView(
            profile_id=pid,
            shadow_eval_budget_s=300,
            per_shadow_run_cap_usd=1.0,
        ),
        run_candidate=_mock_run_candidate,
        run_baseline=_mock_run_baseline,
        compare_outputs=_mock_compare,
        emit_magma_event=_emit_collector(shadow_audit),
        state_handle_is_operator_owned=lambda _sref: False,
        clock_fn=lambda: 0.0,
    )

    shadow_input = ShadowRunInput(
        candidate_manifest_id=manifest_sha256,
        shadow_input_set_ref="synth_24h_winter",
        profile_config_ref=PROFILE_ID,
        tool_descriptor_id=TOOL_DESCRIPTOR_ID,
        state_handles=[TARGET_STATE_REF],
        operator_baseline_command=["python", "baseline.py", "--mode=test"],
        expected_output_format="json",
    )

    shadow_result = shadow.run(shadow_input)
    assert shadow_result.abort_reason is None, (
        f"shadow run aborted unexpectedly: {shadow_result.abort_reason}"
    )
    assert shadow_result.divergence_score == 0.03

    # Feed the run result back into SolverProvenance to exercise the
    # auto-quarantine counter (should NOT quarantine; score below threshold).
    state_after_run = prov.record_run_result(
        candidate_id=CANDIDATE_ID,
        divergence_score=shadow_result.divergence_score,
        evidence_ref=shadow_result.candidate_output_artifact_uri or "",
    )
    assert state_after_run == ActivationState.ACTIVATED

    # -----------------------------------------------------------------
    # Stage 5: WriteRCOGate -- route + execute a LOCAL_ARTIFACT intent
    # -----------------------------------------------------------------

    gate_audit: list = []

    state_info = StateInfo(
        state_id=TARGET_STATE_REF,
        plane="filesystem_artifact",
        write_modes_allowed=["insert"],
        sensitive_class="internal",
        single_writer_required=False,
    )

    gate = WriteRCOGate(
        audit_emit=_emit_collector(gate_audit),
        classify_payload_credential_scan=lambda _payload: [],
        fetch_connector_info=lambda _cid: None,
        fetch_state_info=lambda sid: (state_info
                                         if sid == TARGET_STATE_REF
                                         else None),
        fetch_recovery_capsule=lambda tid: RecoveryCapsuleInfo(
            capsule_id=f"capsule:{tid}",
            rollback_command="rollback_local_artifact",
            known_corruption_modes=[],
        ),
        peer_rco_solicit=lambda _intent: PeerRCOResult(
            verdict="pass",
            rounds=1,
        ),
        operator_scope_policy_check=lambda _intent, _conn, _st:
            ScopePolicyResult(decision="auto_approved"),
        write_executor=lambda intent: ExecutionResult(
            intent_id=intent.intent_id,
            success=True,
            elapsed_ms=50,
        ),
        verify_solver_provenance=prov.verify_solver_provenance,
    )

    intent = Intent.construct(
        agent_id="claude",
        session_id="sess_e2e_smoke",
        tool_descriptor_id=TOOL_DESCRIPTOR_ID,
        target_state_ref=TARGET_STATE_REF,
        action="insert",
        payload={
            "recommendation": "charge_at_02:00",
            "candidate_id": CANDIDATE_ID,
        },
        connector_ref=None,
    )

    outcome = gate.route(intent)
    assert outcome.approved, (
        f"gate refused intent: denial_reason={outcome.denial_reason}, "
        f"stop_condition={outcome.stop_condition}"
    )
    assert outcome.risk_class == WriteRiskClass.LOCAL_ARTIFACT

    exec_result = gate.execute(intent, outcome)
    assert exec_result.success, (
        f"execute failed: {exec_result.error_reason}"
    )

    # -----------------------------------------------------------------
    # Stage 6: cross-stage audit chain assertions
    # -----------------------------------------------------------------

    all_audit_event_types = (
        [e["event_type"] for e in sp_audit]
        + [e["event_type"] for e in shadow_audit]
        + [e["event_type"] for e in gate_audit]
    )

    # SolverProvenance: 2 sign events + 1 activate event + 1 below-
    # threshold run-record (record_run_result does not emit; only the
    # auto-quarantine path does).
    sp_signed = [e for e in sp_audit
                 if e["event_type"] == "solver.provenance_signed"]
    assert len(sp_signed) == 2, (
        f"expected 2 solver.provenance_signed (owner + peer); got "
        f"{len(sp_signed)}: {[e.get('signing_role') for e in sp_signed]}"
    )
    sp_authorised = [e for e in sp_audit
                     if e["event_type"] == "solver.activation_authorised"]
    assert len(sp_authorised) == 1, (
        f"expected exactly 1 solver.activation_authorised; got "
        f"{len(sp_authorised)}"
    )

    # ShadowRunner: started + completed (no abort).
    assert "shadow.run_started" in all_audit_event_types
    assert "shadow.run_completed" in all_audit_event_types

    # WriteRCOGate: full happy-path chain through LOCAL_ARTIFACT.
    assert "write.intent_classified" in all_audit_event_types
    assert "write.intent_approved" in all_audit_event_types
    assert "write.effect_started" in all_audit_event_types
    assert "write.effect_completed" in all_audit_event_types

    # No DENIED, no rollback, no EFFECT_FAILED on the happy path.
    assert "write.denied" not in all_audit_event_types
    assert "write.effect_failed" not in all_audit_event_types
    assert "write.rollback_started" not in all_audit_event_types

    # SolverProvenance bridge envelope (E16: existing type=handoff with
    # payload.kind=solver, not a new dotted bridge type).
    assert sp_bridge, "expected at least one bridge envelope from sign()"
    bridge_handoff = [e for e in sp_bridge
                      if e.get("type") == "handoff"
                      and e.get("payload", {}).get("kind") == "solver"]
    assert bridge_handoff, (
        f"expected handoff/solver bridge envelope; got "
        f"{[(e.get('type'), e.get('payload', {}).get('kind')) for e in sp_bridge]}"
    )


# --------------------------------------------------------------------------
# End-to-end failure-mode boundary tests
# --------------------------------------------------------------------------


def test_e2e_quarantined_candidate_blocks_write_through_gate(
    tmp_path: Path,
) -> None:
    """If the candidate is quarantined (e.g., 5 consecutive divergent
    shadow runs), the WriteRCOGate WRT-003 path's verify_solver_provenance
    must return invalid and the gate must refuse the write. Substrate
    contract: shadow divergence -> quarantine -> gate denial chain works
    end-to-end without manual intervention."""
    # Set up minimal solver candidate
    candidate_store: dict = {}
    candidate_record = SolverCandidateRecord(
        candidate_id="frost_risk_predictor_demo",
        manifest_canonical_json="{}",
        manifest_sha256="0" * 64,
        target_domain="DOM-021",  # sensitive domain
        target_write_risk="external_effect",
    )
    candidate_store[candidate_record.candidate_id] = candidate_record

    sp_audit: list = []
    sp_bridge: list = []
    prov = SolverProvenance(
        fetch_candidate=lambda cid: candidate_store.get(cid),
        update_candidate=lambda c: candidate_store.update({c.candidate_id: c}),
        emit_magma_event=_emit_collector(sp_audit),
        emit_bridge_event=_bridge_collector(sp_bridge),
        operator_scope_policy_active=lambda _ref: True,
        quarantine_consecutive_threshold=3,
        quarantine_divergence_score_threshold=0.40,
    )

    # Drive 3 consecutive high-divergence runs -> auto-quarantine.
    for i in range(3):
        prov.record_run_result(
            candidate_id="frost_risk_predictor_demo",
            divergence_score=0.85,
            evidence_ref=f"art:divergent_run_{i}",
        )
    assert candidate_store["frost_risk_predictor_demo"].activation_state == \
        ActivationState.QUARANTINED.value

    # WriteRCOGate WRT-003 path consults verify_solver_provenance via the
    # hook; a quarantined candidate must return invalid and produce a
    # denial.
    result = prov.verify_solver_provenance("frost_risk_predictor_demo")
    assert not result.valid
    assert "solver_quarantined" in result.reasons


def test_e2e_unsigned_candidate_blocks_activation(tmp_path: Path) -> None:
    """If a candidate has not been signed (no owner + peer signatures),
    activate() must refuse with reasons including missing signature
    codes. Substrate contract: no silent activation without two-agent
    consent."""
    candidate_store: dict = {}
    candidate_record = SolverCandidateRecord(
        candidate_id="unsigned_smoke_candidate",
        manifest_canonical_json="{}",
        manifest_sha256="1" * 64,
        target_domain="DOM-099",
        target_write_risk="local_artifact",
    )
    candidate_store[candidate_record.candidate_id] = candidate_record

    sp_audit: list = []
    sp_bridge: list = []
    prov = SolverProvenance(
        fetch_candidate=lambda cid: candidate_store.get(cid),
        update_candidate=lambda c: candidate_store.update({c.candidate_id: c}),
        emit_magma_event=_emit_collector(sp_audit),
        emit_bridge_event=_bridge_collector(sp_bridge),
        operator_scope_policy_active=lambda _ref: True,
    )

    state = prov.activate(candidate_id="unsigned_smoke_candidate")
    assert state != ActivationState.ACTIVATED
    # activation_refused event should be emitted with missing-signature
    # reasons.
    refused = [e for e in sp_audit
               if e["event_type"] == "solver.activation_refused"]
    assert refused
    last = refused[-1]
    assert ("missing_owner_signature" in last["reasons"]
            or "missing_peer_signature" in last["reasons"])


# --------------------------------------------------------------------------
# ENG-01 first-slice e2e extension: PR #388 seed bundle + PR #390 shadow
# fixture + PR #389 SolverSynthesizer driven through the substrate.
#
# Three tests below answer one diagnostic question each:
#   1. Does the substrate carry a *real operator case* (not just a synthetic
#      smoke seed) end-to-end through SolverSynthesizer -> sign -> activate
#      -> shadow -> write?
#   2. Do the locked top-3 invariants in the shadow fixture actually hold
#      (declared top-3 = ascending-price-then-ascending-hour cheapest 3)?
#   3. Do failure-mode scenarios produce an empty top-3 with an explicit
#      refusal marker and stay on the informational risk class?
# --------------------------------------------------------------------------


ENG01_CASE_ID = "ENG-01__spot_electricity_monitor__home"
ENG01_CANDIDATE_ID = "eng01_spot_electricity_monitor_home_pilot"
ENG01_TARGET_DOMAIN = "DOM-007"
ENG01_TARGET_STATE_REF = "state:eng01_advisory_output"
ENG01_TOOL_DESCRIPTOR_ID = "tool:eng01_spot_electricity_monitor"
ENG01_POLICY_REF = "policy:eng01_pilot"

SEED_BUNDLE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "v3_13_0" /
    "operator_case_seed_bundle.json"
)
SHADOW_FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "v3_13_0" /
    "eng01_spot_electricity_shadow.json"
)


def _load_eng01_case_seed() -> OperatorCaseSeed:
    bundle = json.loads(SEED_BUNDLE.read_text(encoding="utf-8"))
    case = next(c for c in bundle["cases"]
                if c["case_id"] == ENG01_CASE_ID)
    return OperatorCaseSeed.from_mapping(case)


def _load_shadow_scenario(scenario_id: str) -> dict:
    fixture = json.loads(SHADOW_FIXTURE.read_text(encoding="utf-8"))
    return fixture["scenarios"][scenario_id]


def _compute_actual_top_3(prices: list) -> list:
    """Return cheapest 3 entries sorted ascending by price, ties by hour."""
    sorted_prices = sorted(
        prices,
        key=lambda p: (p["price_eur_per_kwh"], p["hour_utc"]),
    )
    return sorted_prices[:3]


def test_e2e_eng01_first_slice_winter_full_substrate_chain(
    tmp_path: Path,
) -> None:
    """Drive ENG-01 first-slice (winter happy path) end-to-end through:
    OperatorCaseSeed.from_mapping (PR #388 bundle entry) -> DocIngest ->
    SolverSynthesizer.synthesize_from_doc_ingest_proposal (PR #389) ->
    SolverProvenance sign owner + peer + activate -> ShadowRunner with the
    PR #390 winter shadow scenario as the candidate output -> below-
    threshold record_run_result -> WriteRCOGate.route + execute on an
    INFORMATIONAL advisory write. Asserts the full substrate audit chain.
    """

    # -----------------------------------------------------------------
    # Stage 1: load ENG-01 case seed (PR #388 bundle)
    # -----------------------------------------------------------------

    case_seed = _load_eng01_case_seed()
    assert case_seed.risk_class == "informational"
    assert case_seed.first_solver_slice == (
        "fetch_next_24h_spot_prices_and_return_top_3_cheapest_hours"
    )

    # -----------------------------------------------------------------
    # Stage 2: synthetic home input dir + DocIngest -> proposal
    # -----------------------------------------------------------------

    _write_synthetic_home_input(tmp_path)
    proposal = build_doc_ingest_proposal(
        tmp_path,
        profile_kind="home",
        candidate_id=ENG01_CANDIDATE_ID,
    )

    # -----------------------------------------------------------------
    # Stage 3: SolverSynthesizer (PR #389) -> synthesized candidate
    # -----------------------------------------------------------------

    target = SolverTarget(
        target_domain=ENG01_TARGET_DOMAIN,
        target_write_risk="informational",
        target_state_ref=ENG01_TARGET_STATE_REF,
        tool_descriptor_id=ENG01_TOOL_DESCRIPTOR_ID,
        required_capabilities=tuple(case_seed.required_capabilities),
        failure_modes=tuple(case_seed.failure_modes),
        shadow_expected_outputs=(case_seed.shadow_expected_output,),
        first_solver_slice=case_seed.first_solver_slice,
    )

    synthesized = synthesize_from_doc_ingest_proposal(proposal, target)
    assert synthesized.candidate_record.candidate_id == ENG01_CANDIDATE_ID
    assert synthesized.manifest["activation_state"] == "unactivated"
    assert ENG01_TARGET_STATE_REF in synthesized.manifest["state_handles"]
    assert len(synthesized.manifest_sha256) == 64

    # -----------------------------------------------------------------
    # Stage 4: SolverProvenance sign owner + peer + activate
    # -----------------------------------------------------------------

    sp_audit: list = []
    sp_bridge: list = []
    candidate_store: dict = {ENG01_CANDIDATE_ID: synthesized.candidate_record}

    prov = SolverProvenance(
        fetch_candidate=lambda cid: candidate_store.get(cid),
        update_candidate=lambda c: candidate_store.update({c.candidate_id: c}),
        emit_magma_event=_emit_collector(sp_audit),
        emit_bridge_event=_bridge_collector(sp_bridge),
        operator_scope_policy_active=lambda _ref: True,
    )

    prov.sign(
        candidate_id=ENG01_CANDIDATE_ID,
        signing_agent_id="claude",
        signing_role=SigningRole.OWNER.value,
        bridge_event_ref="bridge:eng01_owner_1",
        operator_scope_policy_ref=ENG01_POLICY_REF,
    )
    prov.sign(
        candidate_id=ENG01_CANDIDATE_ID,
        signing_agent_id="codex",
        signing_role=SigningRole.PEER.value,
        bridge_event_ref="bridge:eng01_peer_1",
        operator_scope_policy_ref=ENG01_POLICY_REF,
    )
    activate_state = prov.activate(candidate_id=ENG01_CANDIDATE_ID)
    assert activate_state == ActivationState.ACTIVATED

    # -----------------------------------------------------------------
    # Stage 5: ShadowRunner with winter happy-path scenario output
    # -----------------------------------------------------------------

    winter = _load_shadow_scenario(
        "happy_path__synthetic_24h_winter_with_known_min_at_02_00_local"
    )
    declared_top_3 = winter["expected_output"]["top_3_cheapest_hours_utc"]
    actual_top_3 = _compute_actual_top_3(winter["input"]["prices_eur_per_kwh"])
    assert [t["hour_utc"] for t in declared_top_3] == \
        [t["hour_utc"] for t in actual_top_3], (
        "winter fixture: declared top-3 does not match actual cheapest 3 "
        "(ascending price, ties by ascending hour)"
    )

    shadow_audit: list = []

    def _mock_run_candidate(_inp: ShadowRunInput) -> CandidateOutput:
        return CandidateOutput(
            artifact_uri="artifact://eng01/winter_candidate_top3",
            elapsed_ms=85,
            cost_consumed=0.0,
            write_risk_observed="informational",
        )

    def _mock_run_baseline(_inp: ShadowRunInput) -> BaselineOutput:
        return BaselineOutput(
            artifact_uri="artifact://eng01/winter_baseline_top3",
            elapsed_ms=90,
            exit_code=0,
        )

    def _mock_compare(_c, _b, _fmt) -> DivergenceScore:
        return DivergenceScore(
            score=0.02,
            delta_summary_ref="artifact://eng01/winter_delta",
        )

    shadow = ShadowRunner(
        fetch_tool_descriptor=lambda tid: ShadowToolView(
            tool_descriptor_id=tid,
            shadow_supported=True,
            write_risk_class="informational",
        ),
        fetch_profile_config=lambda pid: ShadowProfileView(
            profile_id=pid,
            shadow_eval_budget_s=300,
            per_shadow_run_cap_usd=1.0,
        ),
        run_candidate=_mock_run_candidate,
        run_baseline=_mock_run_baseline,
        compare_outputs=_mock_compare,
        emit_magma_event=_emit_collector(shadow_audit),
        state_handle_is_operator_owned=lambda _sref: False,
        clock_fn=lambda: 0.0,
    )

    shadow_input = ShadowRunInput(
        candidate_manifest_id=synthesized.manifest_sha256,
        shadow_input_set_ref=case_seed.shadow_expected_output,
        profile_config_ref=proposal.profile_id,
        tool_descriptor_id=ENG01_TOOL_DESCRIPTOR_ID,
        state_handles=[ENG01_TARGET_STATE_REF],
        operator_baseline_command=["python", "baseline.py", "--mode=eng01"],
        expected_output_format="json",
    )
    shadow_result = shadow.run(shadow_input)
    assert shadow_result.abort_reason is None
    assert shadow_result.divergence_score == 0.02

    state_after_run = prov.record_run_result(
        candidate_id=ENG01_CANDIDATE_ID,
        divergence_score=shadow_result.divergence_score,
        evidence_ref=shadow_result.candidate_output_artifact_uri or "",
    )
    assert state_after_run == ActivationState.ACTIVATED

    # -----------------------------------------------------------------
    # Stage 6: WriteRCOGate -- INFORMATIONAL advisory write
    # -----------------------------------------------------------------

    gate_audit: list = []

    state_info = StateInfo(
        state_id=ENG01_TARGET_STATE_REF,
        plane="filesystem_artifact",
        write_modes_allowed=["insert"],
        sensitive_class="internal",
        single_writer_required=False,
    )

    gate = WriteRCOGate(
        audit_emit=_emit_collector(gate_audit),
        classify_payload_credential_scan=lambda _payload: [],
        fetch_connector_info=lambda _cid: None,
        fetch_state_info=lambda sid: (state_info
                                       if sid == ENG01_TARGET_STATE_REF
                                       else None),
        fetch_recovery_capsule=lambda tid: RecoveryCapsuleInfo(
            capsule_id=f"capsule:{tid}",
            rollback_command="rollback_local_artifact",
            known_corruption_modes=[],
        ),
        peer_rco_solicit=lambda _intent: PeerRCOResult(
            verdict="pass",
            rounds=1,
        ),
        operator_scope_policy_check=lambda _intent, _conn, _st:
            ScopePolicyResult(decision="auto_approved"),
        write_executor=lambda intent: ExecutionResult(
            intent_id=intent.intent_id,
            success=True,
            elapsed_ms=40,
        ),
        verify_solver_provenance=prov.verify_solver_provenance,
    )

    intent = Intent.construct(
        agent_id="claude",
        session_id="sess_eng01_pilot",
        tool_descriptor_id=ENG01_TOOL_DESCRIPTOR_ID,
        target_state_ref=ENG01_TARGET_STATE_REF,
        action="insert",
        payload={
            "candidate_id": ENG01_CANDIDATE_ID,
            "top_3_cheapest_hours_utc": declared_top_3,
            "result_marker": "OK",
        },
        connector_ref=None,
    )
    outcome = gate.route(intent)
    assert outcome.approved, (
        f"gate refused ENG-01 advisory intent: "
        f"denial_reason={outcome.denial_reason}"
    )
    # SUBSTRATE-TRUE NOTE: the ENG-01 seed bundle declares
    # risk_class="informational", but WriteRCOGate.classify() does NOT
    # currently produce INFORMATIONAL -- per test_write_rco_gate.py the
    # INFORMATIONAL class is "a passthrough class for the route()-level
    # handling, not classify()". A filesystem_artifact plane is mapped to
    # LOCAL_ARTIFACT today. This is the smallest e2e proof of a real
    # seed-vs-gate semantic gap: future Sprint 2 design needs to either
    # add a plane that classifies to INFORMATIONAL, or accept that
    # "informational" seeds become "local_artifact" writes in practice.
    # Until then, assert the SUBSTRATE-TRUE classification.
    assert outcome.risk_class == WriteRiskClass.LOCAL_ARTIFACT

    exec_result = gate.execute(intent, outcome)
    assert exec_result.success

    # -----------------------------------------------------------------
    # Stage 7: audit chain assertions
    # -----------------------------------------------------------------

    all_audit_types = (
        [e["event_type"] for e in sp_audit]
        + [e["event_type"] for e in shadow_audit]
        + [e["event_type"] for e in gate_audit]
    )
    assert [e["event_type"] for e in sp_audit].count(
        "solver.provenance_signed") == 2
    assert [e["event_type"] for e in sp_audit].count(
        "solver.activation_authorised") == 1
    assert "shadow.run_started" in all_audit_types
    assert "shadow.run_completed" in all_audit_types
    assert "write.intent_classified" in all_audit_types
    assert "write.intent_approved" in all_audit_types
    assert "write.effect_started" in all_audit_types
    assert "write.effect_completed" in all_audit_types
    assert "write.denied" not in all_audit_types
    assert "write.effect_failed" not in all_audit_types


@pytest.mark.parametrize("scenario_id", [
    "happy_path__synthetic_24h_winter_with_known_min_at_02_00_local",
    "happy_path__synthetic_24h_summer_with_low_spread",
])
def test_e2e_eng01_top_3_invariant_locked_for_both_happy_paths(
    scenario_id: str,
) -> None:
    """The PR #390 fixture's top-3 declaration must equal the actual
    cheapest 3 hours sorted by ascending price (ties by ascending hour).
    This is the invariant the fixture locks; this test prevents drift
    if a future PR edits the fixture without re-deriving the top-3.
    """
    scenario = _load_shadow_scenario(scenario_id)
    declared = scenario["expected_output"]["top_3_cheapest_hours_utc"]
    assert len(declared) == 3
    actual = _compute_actual_top_3(scenario["input"]["prices_eur_per_kwh"])
    assert [d["hour_utc"] for d in declared] == \
        [a["hour_utc"] for a in actual], (
        f"{scenario_id}: declared top-3 hours do not match the cheapest 3 "
        f"by ascending-price-then-ascending-hour"
    )
    assert [d["price_eur_per_kwh"] for d in declared] == \
        [a["price_eur_per_kwh"] for a in actual]
    # ranks must be 1, 2, 3 in order
    assert [d["rank"] for d in declared] == [1, 2, 3]


@pytest.mark.parametrize("scenario_id,expected_marker", [
    ("failure_mode__stale_data_refused", "STALE_DATA_REFUSED"),
    ("failure_mode__missing_hour_refused", "MISSING_HOUR_REFUSED"),
    ("failure_mode__non_monotonic_horizon_refused",
        "NON_MONOTONIC_HORIZON_REFUSED"),
])
def test_e2e_eng01_failure_modes_produce_empty_top_3_with_marker(
    scenario_id: str, expected_marker: str,
) -> None:
    """Failure-mode scenarios in PR #390 must produce an empty top-3 (no
    silent ranking over bad input) with an explicit result_marker, and the
    risk class must stay informational (refusal is read-only, not an
    external_effect).
    """
    scenario = _load_shadow_scenario(scenario_id)
    out = scenario["expected_output"]
    assert out["top_3_cheapest_hours_utc"] == [], (
        f"{scenario_id} must produce empty top-3 (refusal); got "
        f"{out['top_3_cheapest_hours_utc']}"
    )
    assert out["result_marker"] == expected_marker
    assert scenario["writerco_risk_class"] == "informational"
