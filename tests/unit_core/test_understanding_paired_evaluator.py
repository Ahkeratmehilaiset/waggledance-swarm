# SPDX-License-Identifier: Apache-2.0
"""Forge and behavior tests for paired solver-lift shadow evidence V1."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from waggledance.core.learning.understanding_paired_evaluator import (
    ArmOutcomeV1,
    PairIntegrityV1,
    PairedHeldOutObservationV1,
    PairedLiftContractError,
    PairedLiftEvidenceLabel,
    PairedLiftMode,
    PairedLiftPlanV1,
    PairedLiftPolicyV1,
    PairedLiftReceiptV1,
    PairedLiftRequestV1,
    _receipt_core_from_values,
    derive_held_out_pair_manifest_digest,
    evaluate_paired_lift,
)
from waggledance.core.magma.canonical import sha256_digest


def _sha(label: str) -> str:
    return sha256_digest({"fixture": label})


def _hmac(label: str) -> str:
    return "hmac-sha256:" + _sha(label).removeprefix("sha256:")


def _pair_manifest(case_count: int = 20) -> str:
    return derive_held_out_pair_manifest_digest(
        tuple(
            (_hmac(f"case-{index}"), _hmac(f"unit-{index}"))
            for index in range(case_count)
        )
    )


def _plan(*, planned_case_count: int = 20, **changes: object) -> PairedLiftPlanV1:
    values: dict[str, object] = {
        "campaign_id": "campaign:paired-lift:test-v1",
        "solver_family_digest": _sha("solver-family"),
        "cell_address_digest": _sha("cell-address"),
        "subdivision_address_digest": _sha("subdivision-address"),
        "candidate_artifact_digest": _sha("candidate-artifact"),
        "candidate_config_digest": _sha("candidate-config"),
        "incumbent_artifact_digest": _sha("incumbent-artifact"),
        "incumbent_config_digest": _sha("incumbent-config"),
        "registry_snapshot_digest": _sha("registry-snapshot"),
        "held_out_pack_commitment": _hmac("held-out-pack"),
        "held_out_selection_manifest_digest": _sha("selection-manifest"),
        "held_out_pair_manifest_digest": _pair_manifest(planned_case_count),
        "arm_order_policy_digest": _sha("arm-order-policy"),
        "holdout_access_policy_digest": _sha("holdout-access-policy"),
        "oracle_artifact_digest": _sha("oracle-artifact"),
        "oracle_config_digest": _sha("oracle-config"),
        "runner_artifact_digest": _sha("runner-artifact"),
        "toolchain_digest": _sha("toolchain"),
        "environment_digest": _sha("environment"),
        "resource_policy_digest": _sha("resource-policy"),
        "planned_case_count": planned_case_count,
        "commitment_key_id": "key:test-paired-lift-v1",
    }
    values.update(changes)
    return PairedLiftPlanV1(**values)  # type: ignore[arg-type]


def _row(
    index: int,
    candidate: ArmOutcomeV1,
    incumbent: ArmOutcomeV1,
    *,
    integrity: PairIntegrityV1 = PairIntegrityV1.COMPLETE,
    case_label: str | None = None,
    unit_label: str | None = None,
) -> PairedHeldOutObservationV1:
    return PairedHeldOutObservationV1(
        case_commitment=_hmac(case_label or f"case-{index}"),
        declared_unit_commitment=_hmac(unit_label or f"unit-{index}"),
        pair_evidence_commitment=_hmac(f"pair-evidence-{index}"),
        candidate_outcome=candidate,
        incumbent_outcome=incumbent,
        integrity=integrity,
    )


def _positive_rows() -> tuple[PairedHeldOutObservationV1, ...]:
    rows: list[PairedHeldOutObservationV1] = []
    rows.extend(
        _row(i, ArmOutcomeV1.PASS, ArmOutcomeV1.PASS)
        for i in range(8)
    )
    incumbent_losses = (
        ArmOutcomeV1.FAIL,
        ArmOutcomeV1.TIMEOUT,
        ArmOutcomeV1.ERROR,
        ArmOutcomeV1.FAIL,
        ArmOutcomeV1.TIMEOUT,
        ArmOutcomeV1.ERROR,
    )
    rows.extend(
        _row(8 + i, ArmOutcomeV1.PASS, outcome)
        for i, outcome in enumerate(incumbent_losses)
    )
    rows.append(_row(14, ArmOutcomeV1.FAIL, ArmOutcomeV1.PASS))
    rows.append(_row(15, ArmOutcomeV1.TIMEOUT, ArmOutcomeV1.PASS))
    rows.append(_row(16, ArmOutcomeV1.FAIL, ArmOutcomeV1.FAIL))
    rows.append(_row(17, ArmOutcomeV1.TIMEOUT, ArmOutcomeV1.TIMEOUT))
    rows.append(_row(18, ArmOutcomeV1.ERROR, ArmOutcomeV1.ERROR))
    rows.append(_row(19, ArmOutcomeV1.FAIL, ArmOutcomeV1.FAIL))
    return tuple(rows)


def _request(
    rows: tuple[PairedHeldOutObservationV1, ...] | None = None,
    *,
    plan: PairedLiftPlanV1 | None = None,
    leakage_check_passed_asserted: bool = True,
) -> PairedLiftRequestV1:
    return PairedLiftRequestV1(
        plan=plan or _plan(),
        observations=rows if rows is not None else _positive_rows(),
        leakage_audit_digest=_sha("leakage-audit"),
        leakage_check_passed_asserted=leakage_check_passed_asserted,
    )


def _shadow_policy(**changes: object) -> PairedLiftPolicyV1:
    values: dict[str, object] = {
        "mode": PairedLiftMode.SHADOW,
        "min_cases": 20,
        "max_cases": 4096,
    }
    values.update(changes)
    return PairedLiftPolicyV1(**values)  # type: ignore[arg-type]


def test_default_off_returns_before_inspecting_request() -> None:
    class ExplodingRequest:
        def __getattribute__(self, name: str) -> object:
            raise AssertionError(f"default-OFF inspected request field {name}")

    assert evaluate_paired_lift(ExplodingRequest()) is None  # type: ignore[arg-type]


def test_shadow_mode_requires_exact_request_and_policy_types() -> None:
    with pytest.raises(PairedLiftContractError, match="exact PairedLiftRequestV1"):
        evaluate_paired_lift(None, policy=_shadow_policy())
    with pytest.raises(PairedLiftContractError, match="policy must"):
        evaluate_paired_lift(_request(), policy=object())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"mode": "shadow"}, "PairedLiftMode"),
        ({"min_cases": True}, "positive integer"),
        ({"min_cases": 19}, "evidence floor"),
        ({"min_cases": 21, "max_cases": 20}, "cannot exceed"),
        ({"max_cases": 4097}, "hard ceiling"),
    ],
)
def test_policy_rejects_ambiguous_or_unbounded_values(
    changes: dict[str, object], message: str
) -> None:
    with pytest.raises(PairedLiftContractError, match=message):
        _shadow_policy(**changes)


def test_plan_is_digest_bound_and_rejects_equal_or_unpinned_arms() -> None:
    plan = _plan()
    mapping = plan.to_mapping()
    assert plan.plan_digest == sha256_digest(
        {"domain": "wd.understanding.paired_lift_plan.digest.v1", **mapping}
    )
    with pytest.raises(PairedLiftContractError, match="must differ"):
        _plan(incumbent_artifact_digest=plan.candidate_artifact_digest)
    with pytest.raises(PairedLiftContractError, match="canonical sha256"):
        _plan(registry_snapshot_digest="sha256:not-pinned")
    with pytest.raises(PairedLiftContractError, match="keyed V1"):
        _plan(commitment_scheme="sha256-v1")
    with pytest.raises(PairedLiftContractError, match="evidence floor"):
        _plan(planned_case_count=19)

    class EqualitySpoof:
        def __eq__(self, other: object) -> bool:
            return True

    with pytest.raises(PairedLiftContractError, match="metric"):
        _plan(metric=EqualitySpoof())
    with pytest.raises(PairedLiftContractError, match="commitment_scheme"):
        _plan(commitment_scheme=EqualitySpoof())


@pytest.mark.parametrize(
    "changes",
    [
        {"holdout_seal_asserted": False},
        {"candidate_holdout_access_blocked_asserted": False},
        {"oracle_arm_independence_asserted": False},
        {"candidate_attempt_index": 2},
        {"candidate_attempt_budget": 2},
        {"shadow_only": False},
        {"evaluation_only": False},
    ],
)
def test_plan_refuses_missing_assertions_retry_selection_and_authority_drift(
    changes: dict[str, object],
) -> None:
    with pytest.raises(PairedLiftContractError):
        _plan(**changes)


def test_observation_requires_keyed_commitments_and_exact_enums() -> None:
    with pytest.raises(PairedLiftContractError, match="hmac-sha256"):
        PairedHeldOutObservationV1(
            case_commitment=_sha("low-entropy-case"),
            declared_unit_commitment=_hmac("unit"),
            pair_evidence_commitment=_hmac("pair"),
            candidate_outcome=ArmOutcomeV1.PASS,
            incumbent_outcome=ArmOutcomeV1.FAIL,
        )
    with pytest.raises(PairedLiftContractError, match="ArmOutcomeV1"):
        _row(0, "pass", ArmOutcomeV1.FAIL)  # type: ignore[arg-type]


def test_incomplete_pair_cannot_smuggle_scored_arm_results() -> None:
    with pytest.raises(PairedLiftContractError, match="both arms not_scored"):
        _row(
            0,
            ArmOutcomeV1.PASS,
            ArmOutcomeV1.NOT_SCORED,
            integrity=PairIntegrityV1.LABEL_UNAVAILABLE,
        )
    row = _row(
        0,
        ArmOutcomeV1.NOT_SCORED,
        ArmOutcomeV1.NOT_SCORED,
        integrity=PairIntegrityV1.INFRASTRUCTURE_ERROR,
    )
    assert row.integrity is PairIntegrityV1.INFRASTRUCTURE_ERROR


def test_request_requires_immutable_bounded_rows_and_exact_leakage_flag() -> None:
    with pytest.raises(PairedLiftContractError, match="immutable tuple"):
        PairedLiftRequestV1(
            plan=_plan(),
            observations=list(_positive_rows()),  # type: ignore[arg-type]
            leakage_audit_digest=_sha("leakage"),
            leakage_check_passed_asserted=True,
        )
    with pytest.raises(PairedLiftContractError, match="must be boolean"):
        PairedLiftRequestV1(
            plan=_plan(),
            observations=_positive_rows(),
            leakage_audit_digest=_sha("leakage"),
            leakage_check_passed_asserted=1,  # type: ignore[arg-type]
        )


def test_positive_reported_delta_counts_failures_in_the_paired_denominator() -> None:
    receipt = evaluate_paired_lift(_request(), policy=_shadow_policy())
    assert receipt is not None
    assert receipt.evidence_label is PairedLiftEvidenceLabel.POSITIVE
    assert receipt.case_count_received == 20
    assert receipt.case_count_complete == 20
    assert receipt.both_pass_count == 8
    assert receipt.candidate_only_pass_count == 6
    assert receipt.incumbent_only_pass_count == 2
    assert receipt.neither_pass_count == 4
    assert receipt.candidate_pass_count == 14
    assert receipt.incumbent_pass_count == 10
    assert receipt.candidate_fail_count == 3
    assert receipt.candidate_timeout_count == 2
    assert receipt.candidate_error_count == 1
    assert receipt.incumbent_fail_count == 4
    assert receipt.incumbent_timeout_count == 3
    assert receipt.incumbent_error_count == 3
    assert receipt.reported_pass_delta_numerator == 4
    assert receipt.reported_pass_delta_denominator == 20
    assert receipt.reason_codes == ("reported_candidate_pass_delta_positive",)


def test_zero_and_negative_delta_never_receive_positive_evidence() -> None:
    equal_rows = tuple(
        _row(i, ArmOutcomeV1.PASS, ArmOutcomeV1.PASS) for i in range(20)
    )
    equal = evaluate_paired_lift(
        _request(equal_rows), policy=_shadow_policy()
    )
    assert equal is not None
    assert equal.evidence_label is PairedLiftEvidenceLabel.NO_POSITIVE
    assert equal.reported_pass_delta_numerator == 0

    regressed_rows = tuple(
        _row(i, ArmOutcomeV1.FAIL, ArmOutcomeV1.PASS) for i in range(20)
    )
    regressed = evaluate_paired_lift(
        _request(regressed_rows), policy=_shadow_policy()
    )
    assert regressed is not None
    assert regressed.evidence_label is PairedLiftEvidenceLabel.REGRESSION
    assert regressed.reported_pass_delta_numerator == -20


def test_leakage_failure_forces_inconclusive_even_with_positive_delta() -> None:
    receipt = evaluate_paired_lift(
        _request(leakage_check_passed_asserted=False), policy=_shadow_policy()
    )
    assert receipt is not None
    assert receipt.reported_pass_delta_numerator == 4
    assert receipt.evidence_label is PairedLiftEvidenceLabel.INCONCLUSIVE
    assert receipt.reason_codes == ("leakage_pass_assertion_absent",)


def test_submitted_pair_manifest_must_match_the_precommitted_plan() -> None:
    rows = list(_positive_rows())
    first = rows[0]
    rows[0] = _row(
        0,
        first.candidate_outcome,
        first.incumbent_outcome,
        case_label="post-outcome-substitution",
        unit_label="post-outcome-unit",
    )
    receipt = evaluate_paired_lift(
        _request(tuple(rows)), policy=_shadow_policy()
    )
    assert receipt is not None
    assert receipt.reported_pass_delta_numerator == 4
    assert receipt.held_out_pair_manifest_matches_plan is False
    assert receipt.evidence_label is PairedLiftEvidenceLabel.INCONCLUSIVE
    assert receipt.reason_codes == ("held_out_pair_manifest_mismatch",)


def test_partial_and_below_floor_runs_are_inconclusive() -> None:
    rows = _positive_rows()[:-1]
    receipt = evaluate_paired_lift(
        _request(rows, plan=_plan(planned_case_count=20)),
        policy=_shadow_policy(),
    )
    assert receipt is not None
    assert receipt.evidence_label is PairedLiftEvidenceLabel.INCONCLUSIVE
    assert receipt.reason_codes == (
        "case_count_below_minimum",
        "case_count_mismatch",
        "held_out_pair_manifest_mismatch",
    )


def test_incomplete_pair_invalidates_whole_run_without_dropping_a_solver_loss() -> None:
    rows = list(_positive_rows())
    rows[19] = _row(
        19,
        ArmOutcomeV1.NOT_SCORED,
        ArmOutcomeV1.NOT_SCORED,
        integrity=PairIntegrityV1.INPUT_MUTATED,
    )
    receipt = evaluate_paired_lift(
        _request(tuple(rows)), policy=_shadow_policy()
    )
    assert receipt is not None
    assert receipt.case_count_received == 20
    assert receipt.case_count_complete == 19
    assert receipt.reported_pass_delta_denominator == 19
    assert receipt.evidence_label is PairedLiftEvidenceLabel.INCONCLUSIVE
    assert receipt.reason_codes == ("pair_integrity_not_complete",)


@pytest.mark.parametrize("duplicate", ["case", "unit"])
def test_duplicate_cases_or_declared_units_do_not_meet_evidence_gate(
    duplicate: str,
) -> None:
    rows = list(_positive_rows())
    if duplicate == "case":
        rows[1] = _row(
            1,
            rows[1].candidate_outcome,
            rows[1].incumbent_outcome,
            case_label="case-0",
        )
        expected = "duplicate_case_commitment"
    else:
        rows[1] = _row(
            1,
            rows[1].candidate_outcome,
            rows[1].incumbent_outcome,
            unit_label="unit-0",
        )
        expected = "duplicate_declared_unit_commitment"
    receipt = evaluate_paired_lift(
        _request(tuple(rows)), policy=_shadow_policy()
    )
    assert receipt is not None
    assert receipt.evidence_label is PairedLiftEvidenceLabel.INCONCLUSIVE
    assert expected in receipt.reason_codes


def test_duplicate_pair_evidence_commitment_is_inconclusive() -> None:
    rows = list(_positive_rows())
    original = rows[0]
    second = rows[1]
    rows[1] = PairedHeldOutObservationV1(
        case_commitment=second.case_commitment,
        declared_unit_commitment=second.declared_unit_commitment,
        pair_evidence_commitment=original.pair_evidence_commitment,
        candidate_outcome=second.candidate_outcome,
        incumbent_outcome=second.incumbent_outcome,
    )
    receipt = evaluate_paired_lift(
        _request(tuple(rows)), policy=_shadow_policy()
    )
    assert receipt is not None
    assert receipt.evidence_label is PairedLiftEvidenceLabel.INCONCLUSIVE
    assert receipt.reason_codes == ("duplicate_pair_evidence_commitment",)


def test_permutation_is_deterministic_and_outcome_tampering_changes_digest() -> None:
    request = _request()
    reversed_request = _request(tuple(reversed(request.observations)))
    first = evaluate_paired_lift(request, policy=_shadow_policy())
    second = evaluate_paired_lift(reversed_request, policy=_shadow_policy())
    assert first is not None and second is not None
    assert request.request_digest == reversed_request.request_digest
    assert first.to_mapping() == second.to_mapping()

    tampered_rows = list(request.observations)
    tampered_rows[0] = _row(0, ArmOutcomeV1.FAIL, ArmOutcomeV1.PASS)
    tampered = _request(tuple(tampered_rows))
    assert tampered.request_digest != request.request_digest


def test_receipt_is_raw_free_canonical_and_authority_false() -> None:
    receipt = evaluate_paired_lift(_request(), policy=_shadow_policy())
    assert receipt is not None
    mapping = receipt.to_mapping()
    digest = mapping.pop("receipt_digest")
    assert digest == sha256_digest(
        {
            "domain": "wd.understanding.paired_lift_receipt.digest.v1",
            **mapping,
        }
    )
    encoded = json.dumps(mapping, sort_keys=True)
    keys: set[str] = set()

    def collect_keys(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                keys.add(str(key))
                collect_keys(child)
        elif isinstance(value, list):
            for child in value:
                collect_keys(child)

    collect_keys(mapping)
    for forbidden_key in (
        "case_commitment",
        "declared_unit_commitment",
        "pair_evidence_commitment",
        "output",
        "exception",
    ):
        assert forbidden_key not in keys
    assert "PRIVATE_CASE_CANARY" not in encoded
    assert mapping["causal_generalization_claimed"] is False
    assert mapping["statistical_significance_claimed"] is False
    assert mapping["runtime_authority_applied"] is False
    assert mapping["routing_influence_applied"] is False
    assert mapping["solver_promotion_applied"] is False
    assert mapping["builder_invoked"] is False
    assert mapping["registry_write_applied"] is False
    assert mapping["external_writes_applied"] is False
    assert mapping["commitment_mac_authenticity_independently_verified"] is False
    assert mapping["artifact_family_cell_binding_independently_verified"] is False
    assert mapping["leakage_audit_independently_verified"] is False
    assert mapping["same_input_execution_independently_verified"] is False
    assert mapping["statistical_unit_independence_independently_verified"] is False
    assert mapping["cross_campaign_multiplicity_independently_verified"] is False
    assert mapping["receipt_origin_authenticated"] is False


def test_receipt_refuses_count_tamper_and_authority_escalation() -> None:
    receipt = evaluate_paired_lift(_request(), policy=_shadow_policy())
    assert receipt is not None
    with pytest.raises(PairedLiftContractError, match="counts do not sum"):
        replace(receipt, candidate_timeout_count=3)
    with pytest.raises(PairedLiftContractError, match="literal false"):
        replace(receipt, solver_promotion_applied=True)
    with pytest.raises(PairedLiftContractError, match="artifacts must differ"):
        replace(
            receipt,
            incumbent_artifact_digest=receipt.candidate_artifact_digest,
        )
    with pytest.raises(PairedLiftContractError, match="policy_digest"):
        replace(receipt, policy_digest=_sha("unrelated-policy"))
    with pytest.raises(PairedLiftContractError, match="case_count_received"):
        replace(receipt, case_count_received=4097)


def _forge_redigested_receipt(
    receipt: PairedLiftReceiptV1,
    **changes: object,
) -> PairedLiftReceiptV1:
    values = dict(receipt.__dict__)
    values.pop("receipt_digest")
    values.update(changes)
    digest = sha256_digest(
        {
            "domain": "wd.understanding.paired_lift_receipt.digest.v1",
            **_receipt_core_from_values(values),
        }
    )
    return PairedLiftReceiptV1(**values, receipt_digest=digest)  # type: ignore[arg-type]


def test_redigested_negative_delta_cannot_be_relabelled_positive() -> None:
    rows = tuple(
        _row(i, ArmOutcomeV1.FAIL, ArmOutcomeV1.PASS) for i in range(20)
    )
    receipt = evaluate_paired_lift(
        _request(rows), policy=_shadow_policy()
    )
    assert receipt is not None
    assert receipt.reported_pass_delta_numerator == -20
    with pytest.raises(PairedLiftContractError, match="evidence_label"):
        _forge_redigested_receipt(
            receipt,
            evidence_label=PairedLiftEvidenceLabel.POSITIVE,
            reason_codes=("reported_candidate_pass_delta_positive",),
        )


def test_redigested_leakage_failure_cannot_be_relabelled_positive() -> None:
    receipt = evaluate_paired_lift(
        _request(leakage_check_passed_asserted=False),
        policy=_shadow_policy(),
    )
    assert receipt is not None
    with pytest.raises(PairedLiftContractError, match="evidence_label"):
        _forge_redigested_receipt(
            receipt,
            evidence_label=PairedLiftEvidenceLabel.POSITIVE,
            reason_codes=("reported_candidate_pass_delta_positive",),
        )


def test_malformed_reason_code_is_a_contract_error_not_type_error() -> None:
    receipt = evaluate_paired_lift(_request(), policy=_shadow_policy())
    assert receipt is not None
    with pytest.raises(PairedLiftContractError, match="contain strings"):
        replace(receipt, reason_codes=(1,))  # type: ignore[arg-type]


def test_evaluator_source_has_no_runtime_promotion_or_persistence_imports() -> None:
    source_path = (
        Path(__file__).resolve().parents[2]
        / "waggledance"
        / "core"
        / "learning"
        / "understanding_paired_evaluator.py"
    )
    source = source_path.read_text(encoding="utf-8")
    for forbidden_import in (
        "auto_promotion_engine",
        "solver_dispatcher",
        "control_plane",
        "understanding_ledger",
        "bootstrap.container",
    ):
        assert forbidden_import not in source
