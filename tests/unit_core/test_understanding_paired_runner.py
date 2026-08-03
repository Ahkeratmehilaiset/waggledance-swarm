# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from waggledance.core.autonomy_growth.solver_executor import (
    execute_artifact as real_execute_artifact,
)
from waggledance.core.learning import understanding_paired_runner as runner_module
from waggledance.core.learning.understanding_paired_evaluator import (
    ArmOutcomeV1,
    PairedLiftContractError,
    PairedLiftEvidenceLabel,
    PairedLiftPlanV1,
)
from waggledance.core.learning.understanding_paired_runner import (
    PAIRED_RUNNER_HOLDOUT_ACCESS_POLICY_DIGEST,
    PAIRED_RUNNER_ORACLE_ARTIFACT_DIGEST,
    PAIRED_RUNNER_ORACLE_CONFIG_DIGEST,
    PairedRunnerCaseV1,
    PairedRunnerContractError,
    PairedRunnerMode,
    PairedRunnerPolicyV1,
    PairedRunnerReceiptV1,
    PairedRunnerRequestV1,
    derive_paired_runner_artifact_digest,
    derive_paired_runner_commitments,
    derive_paired_runner_config_digest,
    derive_paired_runner_solver_family_digest,
    run_understanding_paired,
)
from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest


KEY = b"paired-runner-test-key-material-v1!!"
OTHER_KEY = b"paired-runner-other-key-material-v1!"
KEY_ID = "paired-runner-test-key-v1"
CAMPAIGN = "paired-runner-campaign-v1"


def _digest(label: str) -> str:
    return sha256_digest({"test": label})


def _artifact(factor: float) -> bytes:
    return canonical_json_bytes(
        {
            "kind": "scalar_unit_conversion",
            "factor": factor,
            "offset": 0.0,
        }
    )


def _cases(
    count: int = 20,
    *,
    reverse: bool = False,
    unit_prefix: str = "unit",
    raw_canary: str | None = None,
) -> tuple[PairedRunnerCaseV1, ...]:
    rows = []
    for index in range(count):
        x = index + 1
        inputs: dict[str, Any] = {"nested": {"values": [x]}, "x": x}
        if raw_canary is not None:
            inputs["private_marker"] = raw_canary
        rows.append(
            PairedRunnerCaseV1(
                input_json=canonical_json_bytes(inputs),
                expected_output_json=canonical_json_bytes(float(x * 2)),
                declared_unit_json=canonical_json_bytes(
                    {"marker": raw_canary, "unit": f"{unit_prefix}-{index}"}
                ),
            )
        )
    if reverse:
        rows.reverse()
    return tuple(rows)


def _build(
    *,
    cases: tuple[PairedRunnerCaseV1, ...] | None = None,
    policy: PairedRunnerPolicyV1 | None = None,
    leakage_asserted: bool = True,
    candidate_artifact: bytes | None = None,
    incumbent_artifact: bytes | None = None,
    candidate_config: bytes = b"{}",
    incumbent_config: bytes = b"{}",
    campaign: str = CAMPAIGN,
    key: bytes = KEY,
) -> tuple[PairedRunnerPolicyV1, PairedRunnerRequestV1]:
    if cases is None:
        cases = _cases()
    if policy is None:
        policy = PairedRunnerPolicyV1(mode=PairedRunnerMode.SHADOW)
    if candidate_artifact is None:
        candidate_artifact = _artifact(2.0)
    if incumbent_artifact is None:
        incumbent_artifact = _artifact(1.0)
    bindings = derive_paired_runner_commitments(
        campaign_id=campaign,
        cases=cases,
        commitment_key=key,
    )
    candidate_value = json.loads(candidate_artifact.decode("utf-8"))
    family_kind = candidate_value["kind"]
    plan = PairedLiftPlanV1(
        campaign_id=campaign,
        solver_family_digest=(
            derive_paired_runner_solver_family_digest(family_kind)
            if family_kind in runner_module.supported_executor_kinds()
            else _digest("unsupported-family")
        ),
        cell_address_digest=_digest("cell"),
        subdivision_address_digest=_digest("subdivision"),
        candidate_artifact_digest=derive_paired_runner_artifact_digest(
            candidate_artifact
        ),
        candidate_config_digest=derive_paired_runner_config_digest(
            candidate_config
        ),
        incumbent_artifact_digest=derive_paired_runner_artifact_digest(
            incumbent_artifact
        ),
        incumbent_config_digest=derive_paired_runner_config_digest(
            incumbent_config
        ),
        registry_snapshot_digest=_digest("registry"),
        held_out_pack_commitment=bindings.held_out_pack_commitment,
        held_out_selection_manifest_digest=(
            bindings.held_out_selection_manifest_digest
        ),
        held_out_pair_manifest_digest=bindings.held_out_pair_manifest_digest,
        arm_order_policy_digest=bindings.arm_order_policy_digest,
        holdout_access_policy_digest=PAIRED_RUNNER_HOLDOUT_ACCESS_POLICY_DIGEST,
        oracle_artifact_digest=PAIRED_RUNNER_ORACLE_ARTIFACT_DIGEST,
        oracle_config_digest=PAIRED_RUNNER_ORACLE_CONFIG_DIGEST,
        runner_artifact_digest=_digest("runner-source"),
        toolchain_digest=_digest("toolchain"),
        environment_digest=_digest("environment"),
        resource_policy_digest=policy.resource_policy_digest,
        planned_case_count=len(cases),
        commitment_key_id=KEY_ID,
    )
    request = PairedRunnerRequestV1(
        plan=plan,
        candidate_artifact_json=candidate_artifact,
        candidate_config_json=candidate_config,
        incumbent_artifact_json=incumbent_artifact,
        incumbent_config_json=incumbent_config,
        cases=cases,
        commitment_key_id=KEY_ID,
        leakage_audit_digest=_digest("external-leakage-audit"),
        leakage_check_passed_asserted=leakage_asserted,
    )
    return policy, request


def _run(**kwargs: Any) -> PairedRunnerReceiptV1:
    policy, request = _build(**kwargs)
    receipt = run_understanding_paired(
        request,
        policy=policy,
        commitment_key=kwargs.get("key", KEY),
    )
    assert type(receipt) is PairedRunnerReceiptV1
    return receipt


def test_default_off_returns_before_inspecting_request_or_key() -> None:
    class Exploding:
        def __getattribute__(self, name: str) -> Any:
            raise AssertionError(name)

        def __repr__(self) -> str:
            raise AssertionError("repr")

    assert run_understanding_paired(Exploding(), commitment_key=Exploding()) is None


def test_positive_closed_paired_run_feeds_c6() -> None:
    receipt = _run()
    lift = receipt.paired_lift_receipt

    assert receipt.case_count == 20
    assert receipt.candidate_first_count == 10
    assert receipt.incumbent_first_count == 10
    assert receipt.complete_pair_count == 20
    assert lift.candidate_pass_count == 20
    assert lift.incumbent_pass_count == 0
    assert lift.evidence_label is PairedLiftEvidenceLabel.POSITIVE
    assert lift.pair_evidence_root_digest == receipt.execution_evidence_root_digest
    assert lift.same_input_execution_independently_verified is False
    assert lift.solver_promotion_applied is False


def test_odd_case_schedule_is_balanced_with_candidate_first_extra() -> None:
    receipt = _run(cases=_cases(21))
    assert receipt.candidate_first_count == 11
    assert receipt.incumbent_first_count == 10


def test_case_permutation_preserves_plan_schedule_and_receipt() -> None:
    policy, forward = _build(cases=_cases())
    reverse_request = replace(forward, cases=_cases(reverse=True))

    first = run_understanding_paired(forward, policy=policy, commitment_key=KEY)
    second = run_understanding_paired(
        reverse_request,
        policy=policy,
        commitment_key=KEY,
    )

    assert first == second


def test_order_alternates_after_preoutcome_sort(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[float] = []

    def spy(artifact: dict[str, Any], inputs: dict[str, Any]) -> Any:
        calls.append(float(artifact["factor"]))
        return real_execute_artifact(artifact, inputs)

    monkeypatch.setattr(runner_module, "execute_artifact", spy)
    _run()

    assert calls[:8] == [2.0, 1.0, 1.0, 2.0, 2.0, 1.0, 1.0, 2.0]
    assert len(calls) == 40


def test_each_arm_receives_a_fresh_nested_json_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retained_inputs: list[dict[str, Any]] = []

    def spy(artifact: dict[str, Any], inputs: dict[str, Any]) -> Any:
        retained_inputs.append(inputs)
        return real_execute_artifact(artifact, inputs)

    monkeypatch.setattr(runner_module, "execute_artifact", spy)
    _run()

    for index in range(0, len(retained_inputs), 2):
        first = retained_inputs[index]
        second = retained_inputs[index + 1]
        assert first == second
        assert first is not second
        assert first["nested"] is not second["nested"]
        assert first["nested"]["values"] is not second["nested"]["values"]


def test_post_return_mutation_makes_pair_incomplete_without_cross_arm_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incumbent_saw_mutation: list[bool] = []

    def mutating(artifact: dict[str, Any], inputs: dict[str, Any]) -> Any:
        result = real_execute_artifact(artifact, inputs)
        if float(artifact["factor"]) == 2.0 and inputs["x"] == 1:
            inputs["nested"]["values"].append("candidate-only")
        if float(artifact["factor"]) == 1.0:
            incumbent_saw_mutation.append(
                "candidate-only" in inputs["nested"]["values"]
            )
        return result

    monkeypatch.setattr(runner_module, "execute_artifact", mutating)
    receipt = _run()

    assert receipt.post_return_state_mismatch_pair_count == 1
    assert receipt.complete_pair_count == 19
    assert receipt.paired_lift_receipt.evidence_label is PairedLiftEvidenceLabel.INCONCLUSIVE
    assert not any(incumbent_saw_mutation)
    assert receipt.transient_mutation_absence_claimed is False


def test_timeout_exception_is_error_without_timeout_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[float] = []

    def timing_out(artifact: dict[str, Any], inputs: dict[str, Any]) -> Any:
        factor = float(artifact["factor"])
        calls.append(factor)
        if factor == 2.0:
            raise TimeoutError("private timeout detail")
        return real_execute_artifact(artifact, inputs)

    monkeypatch.setattr(runner_module, "execute_artifact", timing_out)
    receipt = _run()
    lift = receipt.paired_lift_receipt

    assert len(calls) == 40
    assert lift.candidate_error_count == 20
    assert lift.candidate_timeout_count == 0
    assert receipt.wall_clock_timeout_enforced is False


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("held_out_pack_commitment", "hmac-sha256:" + "0" * 64),
        ("held_out_selection_manifest_digest", _digest("wrong-selection")),
        ("held_out_pair_manifest_digest", _digest("wrong-pair-manifest")),
        ("arm_order_policy_digest", _digest("wrong-order")),
        ("holdout_access_policy_digest", _digest("wrong-access")),
        ("oracle_artifact_digest", _digest("wrong-oracle")),
        ("oracle_config_digest", _digest("wrong-oracle-config")),
        ("resource_policy_digest", _digest("wrong-resource")),
    ],
)
def test_plan_binding_mismatch_refuses_before_execution(
    field: str,
    replacement: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy, request = _build()
    request = replace(request, plan=replace(request.plan, **{field: replacement}))

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("executor invoked")

    monkeypatch.setattr(runner_module, "execute_artifact", explode)
    with pytest.raises(PairedRunnerContractError, match="binding mismatch"):
        run_understanding_paired(request, policy=policy, commitment_key=KEY)


def test_wrong_key_refuses_before_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    policy, request = _build()

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("executor invoked")

    monkeypatch.setattr(runner_module, "execute_artifact", explode)
    with pytest.raises(PairedRunnerContractError, match="binding mismatch"):
        run_understanding_paired(request, policy=policy, commitment_key=OTHER_KEY)


def test_changed_expected_value_after_plan_refuses_before_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy, request = _build()
    changed = list(request.cases)
    changed[0] = replace(changed[0], expected_output_json=canonical_json_bytes(-1))
    request = replace(request, cases=tuple(changed))

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("executor invoked")

    monkeypatch.setattr(runner_module, "execute_artifact", explode)
    with pytest.raises(PairedRunnerContractError, match="binding mismatch"):
        run_understanding_paired(request, policy=policy, commitment_key=KEY)


def test_changed_artifact_or_config_refuses_before_execution() -> None:
    policy, request = _build()
    changed_artifact = replace(
        request,
        candidate_artifact_json=_artifact(3.0),
    )
    with pytest.raises(PairedRunnerContractError, match="artifact binding mismatch"):
        run_understanding_paired(
            changed_artifact,
            policy=policy,
            commitment_key=KEY,
        )
    changed_config = replace(request, candidate_config_json=b'{"changed":true}')
    with pytest.raises(PairedRunnerContractError, match="config binding mismatch"):
        run_understanding_paired(changed_config, policy=policy, commitment_key=KEY)


def test_commitment_key_id_must_match_plan() -> None:
    policy, request = _build()
    request = replace(request, commitment_key_id="different-key-id")
    with pytest.raises(PairedRunnerContractError, match="key id"):
        run_understanding_paired(request, policy=policy, commitment_key=KEY)


def test_duplicate_materialized_case_and_unit_are_rejected() -> None:
    duplicate = _cases()[0]
    cases = (duplicate, duplicate) + _cases()[2:20]
    with pytest.raises(PairedRunnerContractError, match="duplicate"):
        derive_paired_runner_commitments(
            campaign_id=CAMPAIGN,
            cases=cases,
            commitment_key=KEY,
        )


def test_duplicate_declared_units_are_rejected() -> None:
    cases = list(_cases())
    cases[1] = replace(cases[1], declared_unit_json=cases[0].declared_unit_json)
    with pytest.raises(PairedRunnerContractError, match="declared-unit"):
        derive_paired_runner_commitments(
            campaign_id=CAMPAIGN,
            cases=tuple(cases),
            commitment_key=KEY,
        )


def test_noncanonical_and_non_object_json_are_rejected() -> None:
    with pytest.raises(PairedRunnerContractError, match="canonical"):
        PairedRunnerCaseV1(
            input_json=b'{"x": 1}',
            expected_output_json=b"2",
            declared_unit_json=b'"u"',
        )
    with pytest.raises(PairedRunnerContractError, match="JSON object"):
        PairedRunnerCaseV1(
            input_json=b"[]",
            expected_output_json=b"2",
            declared_unit_json=b'"u"',
        )


def test_invalid_private_json_is_not_retained_by_public_exception() -> None:
    raw = b'{"private":"RAW-ERROR-CANARY-' + bytes([0xFF]) + b'"}'
    with pytest.raises(PairedRunnerContractError) as captured:
        PairedRunnerCaseV1(
            input_json=raw,
            expected_output_json=b"2",
            declared_unit_json=b'"u"',
        )
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "RAW-ERROR-CANARY" not in str(captured.value)


def test_policy_rejects_bool_int_and_bounds() -> None:
    with pytest.raises(PairedRunnerContractError):
        PairedRunnerPolicyV1(min_cases=True)
    with pytest.raises(PairedRunnerContractError):
        PairedRunnerPolicyV1(min_cases=19)
    with pytest.raises(PairedRunnerContractError):
        PairedRunnerPolicyV1(max_cases=4097)


def test_case_count_and_shape_bounds_fail_before_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy, request = _build()

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("executor invoked")

    monkeypatch.setattr(runner_module, "execute_artifact", explode)
    with pytest.raises(PairedRunnerContractError, match="case count"):
        run_understanding_paired(
            replace(request, cases=request.cases[:-1]),
            policy=policy,
            commitment_key=KEY,
        )
    shallow = replace(policy, max_json_depth=1)
    shallow_request = replace(
        request,
        plan=replace(
            request.plan,
            resource_policy_digest=shallow.resource_policy_digest,
        ),
    )
    with pytest.raises(PairedRunnerContractError, match="depth"):
        run_understanding_paired(
            shallow_request,
            policy=shallow,
            commitment_key=KEY,
        )


def test_different_or_unsupported_families_refuse() -> None:
    threshold = canonical_json_bytes(
        {
            "false_label": "no",
            "kind": "threshold_rule",
            "operator": ">",
            "threshold": 1.0,
            "true_label": "yes",
        }
    )
    policy, request = _build(incumbent_artifact=threshold)
    with pytest.raises(PairedRunnerContractError, match="share one family"):
        run_understanding_paired(request, policy=policy, commitment_key=KEY)


@pytest.mark.parametrize(
    ("malformed", "valid", "message"),
    [
        (
            {"factor": "2", "kind": "scalar_unit_conversion"},
            {"factor": 1.0, "kind": "scalar_unit_conversion"},
            "JSON number",
        ),
        (
            {"kind": "lookup_table", "table": []},
            {"default": 0, "kind": "lookup_table", "table": {"x": 1}},
            "JSON object",
        ),
        (
            {
                "false_label": "no",
                "kind": "threshold_rule",
                "operator": "contains",
                "threshold": 1.0,
                "true_label": "yes",
            },
            {
                "false_label": "no",
                "kind": "threshold_rule",
                "operator": ">",
                "threshold": 1.0,
                "true_label": "yes",
            },
            "operator",
        ),
        (
            {"intervals": ["0:1"], "kind": "interval_bucket_classifier"},
            {
                "intervals": [{"label": "x", "max": 2.0, "min": 0.0}],
                "kind": "interval_bucket_classifier",
            },
            "interval entry",
        ),
        (
            {
                "coefficients": "1" * 30_000,
                "input_columns": "x" * 30_000,
                "intercept": 0.0,
                "kind": "linear_arithmetic",
            },
            {
                "coefficients": [1.0],
                "input_columns": ["x"],
                "intercept": 0.0,
                "kind": "linear_arithmetic",
            },
            "coefficients",
        ),
        (
            {
                "kind": "bounded_interpolation",
                "knots": "0,0;1,1",
                "max_x": 1.0,
                "method": "linear",
                "min_x": 0.0,
            },
            {
                "kind": "bounded_interpolation",
                "knots": [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 1.0}],
                "max_x": 1.0,
                "method": "linear",
                "min_x": 0.0,
            },
            "knots",
        ),
    ],
)
def test_family_semantic_preflight_rejects_malformed_shapes_without_execution(
    malformed: dict[str, Any],
    valid: dict[str, Any],
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy, request = _build(
        candidate_artifact=canonical_json_bytes(malformed),
        incumbent_artifact=canonical_json_bytes(valid),
    )

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("executor invoked")

    monkeypatch.setattr(runner_module, "execute_artifact", explode)
    with pytest.raises(PairedRunnerContractError, match=message):
        run_understanding_paired(request, policy=policy, commitment_key=KEY)


def test_total_output_work_product_refuses_before_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = PairedRunnerPolicyV1(
        mode=PairedRunnerMode.SHADOW,
        max_output_bytes=1_048_576,
        max_total_output_work_bytes=1,
    )
    _, request = _build(policy=policy)

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("executor invoked")

    monkeypatch.setattr(runner_module, "execute_artifact", explode)
    with pytest.raises(PairedRunnerContractError, match="output work"):
        run_understanding_paired(request, policy=policy, commitment_key=KEY)


def test_total_artifact_decode_product_refuses_before_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = PairedRunnerPolicyV1(
        mode=PairedRunnerMode.SHADOW,
        max_total_artifact_decode_work_bytes=1,
    )
    _, request = _build(policy=policy)

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("executor invoked")

    monkeypatch.setattr(runner_module, "execute_artifact", explode)
    with pytest.raises(PairedRunnerContractError, match="artifact decode work"):
        run_understanding_paired(request, policy=policy, commitment_key=KEY)

    unsupported_candidate = canonical_json_bytes(
        {"kind": "weighted_aggregation", "missing_policy": "drop", "weights": {}}
    )
    unsupported_incumbent = canonical_json_bytes(
        {
            "kind": "weighted_aggregation",
            "missing_policy": "zero",
            "weights": {"x": 1.0},
        }
    )
    policy, request = _build(
        candidate_artifact=unsupported_candidate,
        incumbent_artifact=unsupported_incumbent,
    )
    with pytest.raises(PairedRunnerContractError, match="no closed V1 executor"):
        run_understanding_paired(request, policy=policy, commitment_key=KEY)


def test_output_bound_maps_to_error_without_raw_output() -> None:
    policy = PairedRunnerPolicyV1(
        mode=PairedRunnerMode.SHADOW,
        max_output_bytes=1,
    )
    receipt = _run(policy=policy)
    lift = receipt.paired_lift_receipt
    assert lift.candidate_error_count == 20
    assert lift.incumbent_error_count == 20
    assert lift.candidate_timeout_count == 0
    assert lift.incumbent_timeout_count == 0


def test_external_leakage_assertion_defaults_to_inconclusive() -> None:
    receipt = _run(leakage_asserted=False)
    assert receipt.paired_lift_receipt.evidence_label is PairedLiftEvidenceLabel.INCONCLUSIVE
    assert receipt.candidate_development_leakage_independently_verified is False


def test_receipt_mapping_and_repr_are_raw_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canary = "RAW-CANARY-INPUT-UNIT-OUTPUT"
    error_canary = "RAW-CANARY-EXCEPTION-PATH-C:/private/file"

    def erroring(artifact: dict[str, Any], inputs: dict[str, Any]) -> Any:
        if float(artifact["factor"]) == 1.0 and inputs["x"] == 1:
            raise RuntimeError(error_canary)
        return real_execute_artifact(artifact, inputs)

    monkeypatch.setattr(runner_module, "execute_artifact", erroring)
    receipt = _run(cases=_cases(raw_canary=canary))
    public_text = json.dumps(receipt.to_mapping(), sort_keys=True) + repr(receipt)

    assert canary not in public_text
    assert error_canary not in public_text
    assert KEY.decode("ascii") not in public_text
    assert "case_commitment" not in receipt.to_mapping()
    assert "declared_unit_commitment" not in receipt.to_mapping()


def test_private_request_and_case_repr_do_not_expose_values() -> None:
    case = _cases(raw_canary="PRIVATE-REPR-CANARY")[0]
    _, request = _build(cases=_cases(raw_canary="PRIVATE-REPR-CANARY"))
    assert "PRIVATE-REPR-CANARY" not in repr(case)
    assert "PRIVATE-REPR-CANARY" not in repr(request)


def test_receipt_refuses_authority_and_relation_forges() -> None:
    receipt = _run()
    with pytest.raises(PairedRunnerContractError, match="literal false"):
        replace(receipt, runtime_authority_requested_by_runner=True)
    with pytest.raises(PairedRunnerContractError, match="candidate-first"):
        replace(receipt, candidate_first_count=9)
    with pytest.raises(PairedLiftContractError):
        replace(
            receipt,
            paired_lift_receipt=replace(
                receipt.paired_lift_receipt,
                solver_promotion_applied=True,
            ),
        )


def test_schema_and_commitment_scheme_require_exact_strings() -> None:
    class Text(str):
        pass

    commitments = derive_paired_runner_commitments(
        campaign_id=CAMPAIGN,
        cases=_cases(),
        commitment_key=KEY,
    )
    with pytest.raises(PairedRunnerContractError, match="commitment scheme"):
        replace(
            commitments,
            commitment_scheme=Text(commitments.commitment_scheme),
        )

    receipt = _run()
    with pytest.raises(PairedRunnerContractError, match="receipt schema"):
        replace(receipt, schema_version=Text(receipt.schema_version))


def test_campaign_and_expected_value_change_preoutcome_commitments() -> None:
    cases = _cases()
    first = derive_paired_runner_commitments(
        campaign_id=CAMPAIGN,
        cases=cases,
        commitment_key=KEY,
    )
    second = derive_paired_runner_commitments(
        campaign_id="paired-runner-campaign-v2",
        cases=cases,
        commitment_key=KEY,
    )
    changed_cases = list(cases)
    changed_cases[0] = replace(
        changed_cases[0],
        expected_output_json=canonical_json_bytes(-1),
    )
    third = derive_paired_runner_commitments(
        campaign_id=CAMPAIGN,
        cases=tuple(changed_cases),
        commitment_key=KEY,
    )
    assert first.held_out_pack_commitment != second.held_out_pack_commitment
    assert first.held_out_pack_commitment != third.held_out_pack_commitment
    assert (
        first.held_out_selection_manifest_digest
        != third.held_out_selection_manifest_digest
    )


def test_module_has_no_freeform_execution_or_io_imports() -> None:
    source = Path(runner_module.__file__).read_text(encoding="utf-8").lower()
    for forbidden in (
        "import asyncio",
        "import os",
        "import pathlib",
        "import socket",
        "import subprocess",
        "from subprocess",
        "multiprocessing",
        "eval(",
        "exec(",
        "open(",
    ):
        assert forbidden not in source


def test_receipt_nonclaims_cover_sandbox_statistics_and_authority() -> None:
    receipt = _run()
    mapping = receipt.to_mapping()
    for field in (
        "os_sandbox_applied",
        "process_isolation_applied",
        "filesystem_isolation_independently_verified",
        "network_isolation_independently_verified",
        "wall_clock_timeout_enforced",
        "cpu_memory_quota_enforced",
        "actual_held_outness_independently_verified",
        "oracle_independence_independently_verified",
        "hex_cell_binding_independently_verified",
        "registry_snapshot_identity_independently_verified",
        "plan_external_pin_independently_verified",
        "runner_artifact_identity_verified",
        "toolchain_environment_independently_verified",
        "statistical_unit_independence_independently_verified",
        "cross_campaign_multiplicity_independently_verified",
        "causal_generalization_claimed",
        "statistical_significance_claimed",
        "executor_effects_independently_verified",
        "runtime_authority_requested_by_runner",
        "routing_influence_requested_by_runner",
        "solver_promotion_requested_by_runner",
        "builder_requested_by_runner",
        "registry_write_requested_by_runner",
        "external_writes_requested_by_runner",
    ):
        assert mapping[field] is False


def test_exact_type_boundaries_reject_lists_and_key_subclasses() -> None:
    with pytest.raises(PairedRunnerContractError, match="immutable tuple"):
        derive_paired_runner_commitments(
            campaign_id=CAMPAIGN,
            cases=list(_cases()),  # type: ignore[arg-type]
            commitment_key=KEY,
        )

    class Key(bytes):
        pass

    policy, request = _build()
    with pytest.raises(PairedRunnerContractError, match="exact bytes"):
        run_understanding_paired(
            request,
            policy=policy,
            commitment_key=Key(KEY),
        )
