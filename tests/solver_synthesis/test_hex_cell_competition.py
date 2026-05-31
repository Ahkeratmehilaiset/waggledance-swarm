# SPDX-License-Identifier: Apache-2.0
"""Tests for the D4 hex-cell competition result contract.

The v0 contract is evidence-only. It can name a deterministic winner and
losers for same-cell candidates, but it must not route traffic, mutate solver
candidate state, or deprecate losers without a later operator-gated promotion
path.
"""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.solver_synthesis.hex_cell_competition import (
    HEX_CELL_ACTIVATION_PREFLIGHT_NEXT_GATE,
    HEX_CELL_ACTIVATION_PREFLIGHT_RECEIPT_EVENT_TYPE,
    HEX_CELL_ACTIVATION_PREFLIGHT_SCHEMA_VERSION,
    HEX_CELL_ACTIVATION_PREFLIGHT_STATUS,
    HEX_CELL_ACTIVATION_PREFLIGHT_TRANSITION,
    HEX_CELL_COMPETITION_AUTHORITY_STATUS,
    HEX_CELL_COMPETITION_DIGEST_ALGORITHM,
    HEX_CELL_COMPETITION_RANKING_RULE,
    HEX_CELL_COMPETITION_RESULT_SCHEMA_VERSION,
    HEX_CELL_OPERATOR_GATE_AUTHORIZATION_NEXT_GATE,
    HEX_CELL_OPERATOR_GATE_AUTHORIZATION_RECEIPT_EVENT_TYPE,
    HEX_CELL_OPERATOR_GATE_AUTHORIZATION_SCHEMA_VERSION,
    HEX_CELL_OPERATOR_GATE_AUTHORIZATION_STATUS,
    HEX_CELL_OPERATOR_GATE_DUPLICATE_RETRY_BEHAVIOR,
    HEX_CELL_PROMOTION_ACCEPTANCE_NEXT_GATE,
    HEX_CELL_PROMOTION_ACCEPTANCE_RECEIPT_EVENT_TYPE,
    HEX_CELL_PROMOTION_ACCEPTANCE_SCHEMA_VERSION,
    HEX_CELL_PROMOTION_ACCEPTANCE_STATUS,
    build_hex_cell_activation_preflight,
    build_hex_cell_competition_result,
    build_hex_cell_operator_gate_authorization,
    build_hex_cell_promotion_acceptance,
)
from waggledance.core.solver_synthesis.solver_candidate_store import (
    SolverCandidate,
)
from waggledance.core.v3_13_0.solver_provenance import (
    SolverCandidateRecord,
    VerificationResult,
    build_solver_provenance_transition_receipt,
    canonicalize_manifest,
)


FIXTURE_PATH = (
    Path(__file__).parents[1]
    / "fixtures"
    / "solver_synthesis"
    / "hex_cell_competition_v0.json"
)


def _candidate(
    *,
    candidate_id: str = "cand-a",
    solver_name: str = "solver_a",
    cell_id: str = "thermal",
    capability_id: str = "frost-risk-detection",
    no_runtime_mutation: bool = True,
) -> SolverCandidate:
    return SolverCandidate(
        schema_version=1,
        candidate_id=candidate_id,
        state="shadow_only",
        solver_name=solver_name,
        cell_id=cell_id,
        spec_or_code={
            "kind": "threshold_rule",
            "capability_id": capability_id,
        },
        source_gap_ref="gap:d4-hex-cell-competition",
        no_runtime_mutation=no_runtime_mutation,
        produced_by="test",
        branch_name="test/d4",
        base_commit_hash="abc123",
        pinned_input_manifest_sha256="sha256:fixture",
        match_confidence=0.7,
    )


def _candidate_from_fixture(row: dict) -> SolverCandidate:
    return SolverCandidate(
        schema_version=1,
        candidate_id=row["candidate_id"],
        state="shadow_only",
        solver_name=row["solver_name"],
        cell_id=row["cell_id"],
        spec_or_code=dict(row["spec_or_code"]),
        source_gap_ref="gap:d4-hex-cell-competition",
        no_runtime_mutation=True,
        produced_by="test",
        branch_name="test/d4",
        base_commit_hash="abc123",
        pinned_input_manifest_sha256="sha256:fixture",
        match_confidence=0.7,
    )


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _build_from_fixture(payload: dict):
    candidates = [
        _candidate_from_fixture(row) for row in payload["candidates"]
    ]
    scores = {
        row["candidate_id"]: row["score"]
        for row in payload["candidates"]
    }
    evidence_refs = {
        row["candidate_id"]: row["evidence_refs"]
        for row in payload["candidates"]
    }
    result = build_hex_cell_competition_result(
        candidates=candidates,
        capability_id=payload["capability_id"],
        scores=scores,
        evidence_refs=evidence_refs,
    )
    return candidates, result


def _acceptance_receipt_digest(acceptance) -> str:
    return sha256_digest({
        "event_type": HEX_CELL_PROMOTION_ACCEPTANCE_RECEIPT_EVENT_TYPE,
        "acceptance_id": acceptance.acceptance_id,
        "acceptance_digest": acceptance.acceptance_digest,
    })


def _acceptance_digest(
    *,
    competition_id: str,
    cell_id: str,
    capability_id: str,
    accepted_candidate_id: str,
    rejected_candidate_ids: tuple[str, ...],
    competition_evidence_digest: str,
) -> str:
    return sha256_digest({
        "schema_version": HEX_CELL_PROMOTION_ACCEPTANCE_SCHEMA_VERSION,
        "competition_id": competition_id,
        "cell_id": cell_id,
        "capability_id": capability_id,
        "accepted_candidate_id": accepted_candidate_id,
        "rejected_candidate_ids": list(rejected_candidate_ids),
        "competition_evidence_digest": competition_evidence_digest,
        "promotion_acceptance_status": HEX_CELL_PROMOTION_ACCEPTANCE_STATUS,
        "required_next_gate": HEX_CELL_PROMOTION_ACCEPTANCE_NEXT_GATE,
        "operator_gate_required": True,
        "operator_gate_cleared": False,
        "runtime_authority_granted": False,
        "runtime_traffic_mutation_applied": False,
        "candidate_state_mutation_applied": False,
    })


def _acceptance_id(
    *,
    cell_id: str,
    capability_id: str,
    acceptance_digest: str,
) -> str:
    return (
        "hexcellaccept:"
        f"{cell_id}:{capability_id}:"
        f"{acceptance_digest.removeprefix('sha256:')[:16]}"
    )


def _authorization_from_fixture():
    payload = _load_fixture()
    _candidates, competition = _build_from_fixture(payload)
    acceptance = build_hex_cell_promotion_acceptance(
        competition=competition
    )
    return build_hex_cell_operator_gate_authorization(
        acceptance=acceptance,
        operator_approval_id="approval:hexcell:thermal:001",
        approved_by="operator:jkh",
        acceptance_receipt_digest=_acceptance_receipt_digest(acceptance),
    )


def _solver_provenance_activation_bundle(
    candidate_id: str,
    *,
    transition: str = "activation_authorised",
) -> dict:
    canonical, digest = canonicalize_manifest({
        "candidate_id": candidate_id,
        "template_family": "HexCellPreflightDemoSolver",
        "version": 1,
    })
    candidate = SolverCandidateRecord(
        candidate_id=candidate_id,
        manifest_canonical_json=canonical,
        manifest_sha256=digest,
        target_domain="DOM-011",
        target_write_risk="local_artifact",
        activation_state="signed",
    )
    verification = VerificationResult(
        valid=True,
        candidate_id=candidate_id,
        activation_state="signed",
        has_owner_signature=True,
        has_peer_signature=True,
        manifest_sha256_observed=digest,
    )
    return build_solver_provenance_transition_receipt(
        candidate=candidate,
        transition=transition,
        audit_event_ref="evt_hexcell_preflight",
        bridge_event_ref="bridge_hexcell_preflight",
        verification=verification,
        new_state="activated",
    )


def test_fixture_builds_expected_non_authority_result():
    payload = _load_fixture()
    candidates, result = _build_from_fixture(payload)
    expected = payload["expected"]

    assert result.schema_version == (
        HEX_CELL_COMPETITION_RESULT_SCHEMA_VERSION
    )
    assert result.competition_id == expected["competition_id"]
    assert result.evidence_digest == expected["evidence_digest"]
    assert result.evidence_digest_algorithm == (
        HEX_CELL_COMPETITION_DIGEST_ALGORITHM
    )
    assert result.ranking_rule == HEX_CELL_COMPETITION_RANKING_RULE
    assert result.authority_status == HEX_CELL_COMPETITION_AUTHORITY_STATUS
    assert result.winner_id == expected["winner_id"]
    assert list(result.loser_ids) == expected["loser_ids"]

    assert result.runtime_traffic_mutation_applied is False
    assert result.candidate_state_mutation_applied is False
    assert result.operator_gate_required_for_authority is True
    assert [candidate.state for candidate in candidates] == [
        "shadow_only",
        "shadow_only",
        "shadow_only",
    ]


def test_digest_and_ranking_are_stable_when_input_order_changes():
    payload = _load_fixture()
    candidates, first = _build_from_fixture(payload)
    reversed_rows = list(reversed(payload["candidates"]))
    reversed_candidates = [
        _candidate_from_fixture(row) for row in reversed_rows
    ]

    second = build_hex_cell_competition_result(
        candidates=reversed_candidates,
        capability_id=payload["capability_id"],
        scores={
            row["candidate_id"]: row["score"]
            for row in reversed_rows
        },
        evidence_refs={
            row["candidate_id"]: row["evidence_refs"]
            for row in reversed_rows
        },
    )

    assert [candidate.candidate_id for candidate in candidates] == [
        "cand-alpha",
        "cand-beta",
        "cand-gamma",
    ]
    assert second.to_dict() == first.to_dict()


def test_tie_breaks_by_candidate_id_after_score_descending():
    result = build_hex_cell_competition_result(
        candidates=[
            _candidate(candidate_id="cand-z", solver_name="z"),
            _candidate(candidate_id="cand-a", solver_name="a"),
        ],
        capability_id="frost-risk-detection",
        scores={"cand-z": 0.9, "cand-a": 0.9},
    )

    assert result.winner_id == "cand-a"
    assert result.loser_ids == ("cand-z",)


def test_builds_operator_gated_promotion_acceptance_without_authority():
    payload = _load_fixture()
    _candidates, result = _build_from_fixture(payload)

    acceptance = build_hex_cell_promotion_acceptance(competition=result)

    assert acceptance.schema_version == (
        HEX_CELL_PROMOTION_ACCEPTANCE_SCHEMA_VERSION
    )
    assert acceptance.competition_id == result.competition_id
    assert acceptance.cell_id == result.cell_id
    assert acceptance.capability_id == result.capability_id
    assert acceptance.accepted_candidate_id == result.winner_id
    assert acceptance.rejected_candidate_ids == result.loser_ids
    assert acceptance.competition_evidence_digest == result.evidence_digest
    assert acceptance.evidence_digest_algorithm == (
        HEX_CELL_COMPETITION_DIGEST_ALGORITHM
    )
    assert acceptance.promotion_acceptance_status == (
        HEX_CELL_PROMOTION_ACCEPTANCE_STATUS
    )
    assert acceptance.required_next_gate == (
        HEX_CELL_PROMOTION_ACCEPTANCE_NEXT_GATE
    )
    assert acceptance.operator_gate_required is True
    assert acceptance.operator_gate_cleared is False
    assert acceptance.runtime_authority_granted is False
    assert acceptance.runtime_traffic_mutation_applied is False
    assert acceptance.candidate_state_mutation_applied is False

    as_dict = acceptance.to_dict()
    assert as_dict["accepted_candidate_id"] == result.winner_id
    assert as_dict["rejected_candidate_ids"] == list(result.loser_ids)
    assert as_dict["operator_gate_cleared"] is False
    assert as_dict["runtime_authority_granted"] is False
    assert acceptance.acceptance_id.startswith("hexcellaccept:")
    assert acceptance.acceptance_digest.startswith("sha256:")


def test_promotion_acceptance_is_stable_when_competition_is_stable():
    payload = _load_fixture()
    _candidates, first_competition = _build_from_fixture(payload)
    reversed_payload = dict(payload)
    reversed_payload["candidates"] = list(reversed(payload["candidates"]))
    _reversed_candidates, second_competition = _build_from_fixture(
        reversed_payload
    )

    first = build_hex_cell_promotion_acceptance(
        competition=first_competition
    )
    second = build_hex_cell_promotion_acceptance(
        competition=second_competition
    )

    assert second.to_dict() == first.to_dict()


def test_promotion_acceptance_rejects_loser_candidate():
    payload = _load_fixture()
    _candidates, result = _build_from_fixture(payload)

    with pytest.raises(ValueError, match="only accept the competition winner"):
        build_hex_cell_promotion_acceptance(
            competition=result,
            accepted_candidate_id=result.loser_ids[0],
        )


def test_promotion_acceptance_rejects_winner_drift():
    payload = _load_fixture()
    _candidates, result = _build_from_fixture(payload)
    drifted = replace(result, winner_id=result.loser_ids[0])

    with pytest.raises(ValueError, match="winner/losers"):
        build_hex_cell_promotion_acceptance(competition=drifted)


def test_promotion_acceptance_rejects_evidence_digest_drift():
    payload = _load_fixture()
    _candidates, result = _build_from_fixture(payload)
    drifted = replace(result, evidence_digest="sha256:" + "0" * 64)

    with pytest.raises(ValueError, match="evidence_digest"):
        build_hex_cell_promotion_acceptance(competition=drifted)


def test_promotion_acceptance_rejects_authority_status_drift():
    payload = _load_fixture()
    _candidates, result = _build_from_fixture(payload)
    drifted = replace(result, authority_status="runtime_authority_granted")

    with pytest.raises(ValueError, match="non-authority competition"):
        build_hex_cell_promotion_acceptance(competition=drifted)


def test_promotion_acceptance_rejects_missing_operator_gate():
    payload = _load_fixture()
    _candidates, result = _build_from_fixture(payload)
    drifted = replace(result, operator_gate_required_for_authority=False)

    with pytest.raises(ValueError, match="requires operator gate"):
        build_hex_cell_promotion_acceptance(competition=drifted)


def test_promotion_acceptance_rejects_mutation_drift():
    payload = _load_fixture()
    _candidates, result = _build_from_fixture(payload)
    runtime_drift = replace(result, runtime_traffic_mutation_applied=True)
    candidate_drift = replace(result, candidate_state_mutation_applied=True)

    with pytest.raises(ValueError, match="mutated runtime traffic"):
        build_hex_cell_promotion_acceptance(competition=runtime_drift)
    with pytest.raises(ValueError, match="mutated candidate state"):
        build_hex_cell_promotion_acceptance(competition=candidate_drift)


def test_builds_operator_gate_authorization_without_runtime_mutation():
    payload = _load_fixture()
    _candidates, result = _build_from_fixture(payload)
    acceptance = build_hex_cell_promotion_acceptance(competition=result)

    authorization = build_hex_cell_operator_gate_authorization(
        acceptance=acceptance,
        operator_approval_id="approval:hexcell:thermal:001",
        approved_by="operator:jkh",
        acceptance_receipt_digest=_acceptance_receipt_digest(acceptance),
    )

    assert authorization.schema_version == (
        HEX_CELL_OPERATOR_GATE_AUTHORIZATION_SCHEMA_VERSION
    )
    assert authorization.acceptance_id == acceptance.acceptance_id
    assert authorization.competition_id == acceptance.competition_id
    assert authorization.cell_id == acceptance.cell_id
    assert authorization.capability_id == acceptance.capability_id
    assert authorization.accepted_candidate_id == (
        acceptance.accepted_candidate_id
    )
    assert authorization.rejected_candidate_ids == (
        acceptance.rejected_candidate_ids
    )
    assert authorization.competition_evidence_digest == (
        acceptance.competition_evidence_digest
    )
    assert authorization.acceptance_digest == acceptance.acceptance_digest
    assert authorization.evidence_digest_algorithm == (
        HEX_CELL_COMPETITION_DIGEST_ALGORITHM
    )
    assert authorization.operator_decision == "approved"
    assert authorization.operator_scope == (
        HEX_CELL_PROMOTION_ACCEPTANCE_NEXT_GATE
    )
    assert authorization.authority_status == (
        HEX_CELL_OPERATOR_GATE_AUTHORIZATION_STATUS
    )
    assert authorization.required_next_gate == (
        HEX_CELL_OPERATOR_GATE_AUTHORIZATION_NEXT_GATE
    )
    assert authorization.required_receipt_event_type == (
        HEX_CELL_OPERATOR_GATE_AUTHORIZATION_RECEIPT_EVENT_TYPE
    )
    assert authorization.receipt_ordering_enforced is True
    assert (
        authorization.authorization_receipt_required_before_runtime_authority
        is True
    )
    assert authorization.duplicate_retry_behavior == (
        HEX_CELL_OPERATOR_GATE_DUPLICATE_RETRY_BEHAVIOR
    )
    assert authorization.operator_gate_required is True
    assert authorization.operator_gate_cleared is True
    assert authorization.operator_authorized_activation is True
    assert authorization.runtime_authority_granted is False
    assert authorization.runtime_traffic_mutation_applied is False
    assert authorization.candidate_state_mutation_applied is False
    assert authorization.authorization_id.startswith("hexcellauth:")
    assert authorization.authorization_digest.startswith("sha256:")

    as_dict = authorization.to_dict()
    assert as_dict["operator_gate_cleared"] is True
    assert as_dict["runtime_authority_granted"] is False
    assert as_dict["runtime_traffic_mutation_applied"] is False
    assert as_dict["candidate_state_mutation_applied"] is False


def test_operator_gate_authorization_is_stable_for_duplicate_retry():
    payload = _load_fixture()
    _candidates, result = _build_from_fixture(payload)
    acceptance = build_hex_cell_promotion_acceptance(competition=result)
    receipt_digest = _acceptance_receipt_digest(acceptance)

    first = build_hex_cell_operator_gate_authorization(
        acceptance=acceptance,
        operator_approval_id="approval:hexcell:thermal:001",
        approved_by="operator:jkh",
        acceptance_receipt_digest=receipt_digest,
    )
    second = build_hex_cell_operator_gate_authorization(
        acceptance=acceptance,
        operator_approval_id="approval:hexcell:thermal:001",
        approved_by="operator:jkh",
        acceptance_receipt_digest=receipt_digest,
    )

    assert second.to_dict() == first.to_dict()


def test_operator_gate_authorization_rejects_non_approved_decision():
    payload = _load_fixture()
    _candidates, result = _build_from_fixture(payload)
    acceptance = build_hex_cell_promotion_acceptance(competition=result)

    with pytest.raises(ValueError, match="operator_decision 'approved'"):
        build_hex_cell_operator_gate_authorization(
            acceptance=acceptance,
            operator_approval_id="approval:hexcell:thermal:001",
            approved_by="operator:jkh",
            operator_decision="denied",
            acceptance_receipt_digest=_acceptance_receipt_digest(acceptance),
        )


def test_operator_gate_authorization_rejects_wrong_scope():
    payload = _load_fixture()
    _candidates, result = _build_from_fixture(payload)
    acceptance = build_hex_cell_promotion_acceptance(competition=result)

    with pytest.raises(ValueError, match="requires operator_scope"):
        build_hex_cell_operator_gate_authorization(
            acceptance=acceptance,
            operator_approval_id="approval:hexcell:thermal:001",
            approved_by="operator:jkh",
            operator_scope="external_effect_runtime_write",
            acceptance_receipt_digest=_acceptance_receipt_digest(acceptance),
        )


def test_operator_gate_authorization_requires_acceptance_receipt_digest():
    payload = _load_fixture()
    _candidates, result = _build_from_fixture(payload)
    acceptance = build_hex_cell_promotion_acceptance(competition=result)

    with pytest.raises(ValueError, match="acceptance_receipt_digest"):
        build_hex_cell_operator_gate_authorization(
            acceptance=acceptance,
            operator_approval_id="approval:hexcell:thermal:001",
            approved_by="operator:jkh",
            acceptance_receipt_digest="sha256:fixture",
        )


def test_operator_gate_authorization_rejects_unrelated_receipt_digest():
    payload = _load_fixture()
    _candidates, result = _build_from_fixture(payload)
    acceptance = build_hex_cell_promotion_acceptance(competition=result)

    with pytest.raises(ValueError, match="acceptance_receipt_digest"):
        build_hex_cell_operator_gate_authorization(
            acceptance=acceptance,
            operator_approval_id="approval:hexcell:thermal:001",
            approved_by="operator:jkh",
            acceptance_receipt_digest="sha256:" + "0" * 64,
        )


def test_operator_gate_authorization_rejects_competition_id_forgery():
    payload = _load_fixture()
    _candidates, result = _build_from_fixture(payload)
    acceptance = build_hex_cell_promotion_acceptance(competition=result)
    forged_competition_id = "hexcellcmp:thermal:frost-risk-detection:forged"
    forged_digest = _acceptance_digest(
        competition_id=forged_competition_id,
        cell_id=acceptance.cell_id,
        capability_id=acceptance.capability_id,
        accepted_candidate_id=acceptance.accepted_candidate_id,
        rejected_candidate_ids=acceptance.rejected_candidate_ids,
        competition_evidence_digest=acceptance.competition_evidence_digest,
    )
    forged = replace(
        acceptance,
        competition_id=forged_competition_id,
        acceptance_digest=forged_digest,
        acceptance_id=_acceptance_id(
            cell_id=acceptance.cell_id,
            capability_id=acceptance.capability_id,
            acceptance_digest=forged_digest,
        ),
    )

    with pytest.raises(ValueError, match="competition_id"):
        build_hex_cell_operator_gate_authorization(
            acceptance=forged,
            operator_approval_id="approval:hexcell:thermal:001",
            approved_by="operator:jkh",
            acceptance_receipt_digest=_acceptance_receipt_digest(forged),
        )


def test_operator_gate_authorization_rejects_acceptance_digest_drift():
    payload = _load_fixture()
    _candidates, result = _build_from_fixture(payload)
    acceptance = build_hex_cell_promotion_acceptance(competition=result)
    drifted = replace(
        acceptance,
        rejected_candidate_ids=tuple(
            reversed(acceptance.rejected_candidate_ids)
        ),
    )

    with pytest.raises(ValueError, match="acceptance_digest"):
        build_hex_cell_operator_gate_authorization(
            acceptance=drifted,
            operator_approval_id="approval:hexcell:thermal:001",
            approved_by="operator:jkh",
            acceptance_receipt_digest=_acceptance_receipt_digest(acceptance),
        )


def test_operator_gate_authorization_rejects_precleared_authority_drift():
    payload = _load_fixture()
    _candidates, result = _build_from_fixture(payload)
    acceptance = build_hex_cell_promotion_acceptance(competition=result)
    cleared = replace(acceptance, operator_gate_cleared=True)
    authorized = replace(acceptance, runtime_authority_granted=True)
    mutated = replace(acceptance, runtime_traffic_mutation_applied=True)

    with pytest.raises(ValueError, match="pre-cleared operator gate"):
        build_hex_cell_operator_gate_authorization(
            acceptance=cleared,
            operator_approval_id="approval:hexcell:thermal:001",
            approved_by="operator:jkh",
            acceptance_receipt_digest=_acceptance_receipt_digest(acceptance),
        )
    with pytest.raises(ValueError, match="pre-granted runtime authority"):
        build_hex_cell_operator_gate_authorization(
            acceptance=authorized,
            operator_approval_id="approval:hexcell:thermal:001",
            approved_by="operator:jkh",
            acceptance_receipt_digest=_acceptance_receipt_digest(acceptance),
        )
    with pytest.raises(ValueError, match="mutated runtime traffic"):
        build_hex_cell_operator_gate_authorization(
            acceptance=mutated,
            operator_approval_id="approval:hexcell:thermal:001",
            approved_by="operator:jkh",
            acceptance_receipt_digest=_acceptance_receipt_digest(acceptance),
        )


def test_builds_activation_preflight_without_runtime_authority():
    authorization = _authorization_from_fixture()
    bundle = _solver_provenance_activation_bundle(
        authorization.accepted_candidate_id
    )

    preflight = build_hex_cell_activation_preflight(
        authorization=authorization,
        solver_provenance_bundle=bundle,
    )

    assert preflight.schema_version == (
        HEX_CELL_ACTIVATION_PREFLIGHT_SCHEMA_VERSION
    )
    assert preflight.authorization_id == authorization.authorization_id
    assert preflight.authorization_digest == authorization.authorization_digest
    assert preflight.accepted_candidate_id == (
        authorization.accepted_candidate_id
    )
    assert preflight.solver_candidate_id == authorization.accepted_candidate_id
    assert preflight.activation_transition == (
        HEX_CELL_ACTIVATION_PREFLIGHT_TRANSITION
    )
    assert preflight.activation_preflight_status == (
        HEX_CELL_ACTIVATION_PREFLIGHT_STATUS
    )
    assert preflight.required_next_gate == (
        HEX_CELL_ACTIVATION_PREFLIGHT_NEXT_GATE
    )
    assert preflight.required_receipt_event_type == (
        HEX_CELL_ACTIVATION_PREFLIGHT_RECEIPT_EVENT_TYPE
    )
    assert preflight.receipt_bound_activation_verified is True
    assert preflight.operator_gate_cleared is True
    assert preflight.runtime_authority_granted is False
    assert preflight.runtime_traffic_mutation_applied is False
    assert preflight.candidate_state_mutation_applied is False
    assert preflight.preflight_id.startswith("hexcellpreflight:")
    assert preflight.preflight_digest.startswith("sha256:")

    as_dict = preflight.to_dict()
    assert as_dict["runtime_authority_granted"] is False
    assert as_dict["receipt_bound_activation_verified"] is True


def test_activation_preflight_rejects_candidate_mismatch():
    authorization = _authorization_from_fixture()
    bundle = _solver_provenance_activation_bundle("other-candidate")

    with pytest.raises(ValueError, match="accepted_candidate_id"):
        build_hex_cell_activation_preflight(
            authorization=authorization,
            solver_provenance_bundle=bundle,
        )


def test_activation_preflight_rejects_non_activation_receipt():
    authorization = _authorization_from_fixture()
    bundle = _solver_provenance_activation_bundle(
        authorization.accepted_candidate_id,
    )
    forged = dict(bundle)
    forged["payload"] = dict(bundle["payload"])
    forged["payload"]["transition"] = "activation_revoked"

    with pytest.raises(ValueError, match="activation_authorised"):
        build_hex_cell_activation_preflight(
            authorization=authorization,
            solver_provenance_bundle=forged,
        )


def test_activation_preflight_rejects_evaluation_digest_drift():
    authorization = _authorization_from_fixture()
    bundle = _solver_provenance_activation_bundle(
        authorization.accepted_candidate_id,
    )
    forged = dict(bundle)
    forged["evaluation_result"] = dict(bundle["evaluation_result"])
    forged["evaluation_result"]["target_digest"] = "sha256:" + "0" * 64

    with pytest.raises(ValueError, match="target_digest"):
        build_hex_cell_activation_preflight(
            authorization=authorization,
            solver_provenance_bundle=forged,
        )


def test_activation_preflight_rejects_pregranted_authority():
    authorization = _authorization_from_fixture()
    forged_authorization = replace(
        authorization,
        runtime_authority_granted=True,
    )
    bundle = _solver_provenance_activation_bundle(
        authorization.accepted_candidate_id,
    )

    with pytest.raises(ValueError, match="pre-granted runtime authority"):
        build_hex_cell_activation_preflight(
            authorization=forged_authorization,
            solver_provenance_bundle=bundle,
        )


def test_requires_at_least_two_candidates():
    with pytest.raises(ValueError, match="at least two"):
        build_hex_cell_competition_result(
            candidates=[_candidate()],
            capability_id="frost-risk-detection",
            scores={"cand-a": 0.8},
        )


def test_rejects_mixed_cell_ids():
    with pytest.raises(ValueError, match="share cell_id"):
        build_hex_cell_competition_result(
            candidates=[
                _candidate(candidate_id="cand-a", cell_id="thermal"),
                _candidate(candidate_id="cand-b", cell_id="energy"),
            ],
            capability_id="frost-risk-detection",
            scores={"cand-a": 0.8, "cand-b": 0.9},
        )


def test_rejects_mixed_capability_ids():
    with pytest.raises(ValueError, match="share capability_id"):
        build_hex_cell_competition_result(
            candidates=[
                _candidate(
                    candidate_id="cand-a",
                    capability_id="frost-risk-detection",
                ),
                _candidate(
                    candidate_id="cand-b",
                    capability_id="heat-pump-dispatch",
                ),
            ],
            capability_id="frost-risk-detection",
            scores={"cand-a": 0.8, "cand-b": 0.9},
        )


def test_rejects_missing_score():
    with pytest.raises(ValueError, match="missing scores"):
        build_hex_cell_competition_result(
            candidates=[
                _candidate(candidate_id="cand-a"),
                _candidate(candidate_id="cand-b"),
            ],
            capability_id="frost-risk-detection",
            scores={"cand-a": 0.8},
        )


def test_rejects_unknown_score_candidate():
    with pytest.raises(ValueError, match="unknown candidates"):
        build_hex_cell_competition_result(
            candidates=[
                _candidate(candidate_id="cand-a"),
                _candidate(candidate_id="cand-b"),
            ],
            capability_id="frost-risk-detection",
            scores={"cand-a": 0.8, "cand-b": 0.9, "cand-c": 0.7},
        )


def test_rejects_runtime_mutable_candidate():
    with pytest.raises(ValueError, match="no_runtime_mutation"):
        build_hex_cell_competition_result(
            candidates=[
                _candidate(candidate_id="cand-a"),
                _candidate(
                    candidate_id="cand-b",
                    no_runtime_mutation=False,
                ),
            ],
            capability_id="frost-risk-detection",
            scores={"cand-a": 0.8, "cand-b": 0.9},
        )


def test_rejects_non_finite_score():
    with pytest.raises(ValueError, match="score must be finite"):
        build_hex_cell_competition_result(
            candidates=[
                _candidate(candidate_id="cand-a"),
                _candidate(candidate_id="cand-b"),
            ],
            capability_id="frost-risk-detection",
            scores={"cand-a": 0.8, "cand-b": float("nan")},
        )
