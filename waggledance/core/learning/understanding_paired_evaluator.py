# SPDX-License-Identifier: Apache-2.0
"""Default-off paired solver-lift evidence for the understanding shadow lane.

This module aggregates caller-supplied, already-scored incumbent/candidate
pairs that a precommitted plan asserts are held out.  It does not execute
solvers, call an oracle, persist state, influence routing, or promote anything.
The two important boundaries are:

* the plan commits to both arms, the asserted held-out pack, scorer, runner,
  resource policy, and one candidate attempt before observations are supplied;
* the receipt exports only aggregate counts and digest commitments.  Case
  material, labels, outputs, exception text, and paths are never serialized.

The result is a reported binary pass-rate delta on one asserted held-out pack.
It is not proof of MAC authenticity, temporal sealing, same-input execution,
oracle or statistical-unit independence, leakage isolation, statistical
significance, population-level causal effect, or promotion eligibility.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from waggledance.core.magma.canonical import sha256_digest


PAIRED_LIFT_PLAN_SCHEMA = "wd.understanding.paired_lift_plan.v1"
PAIRED_LIFT_REQUEST_SCHEMA = "wd.understanding.paired_lift_request.v1"
PAIRED_LIFT_RECEIPT_SCHEMA = "wd.understanding.paired_lift_receipt.v1"
PAIRED_LIFT_METRIC = "binary_oracle_agreement_v1"
PAIRED_LIFT_COMMITMENT_SCHEME = "hmac-sha256-v1"
PAIRED_LIFT_MIN_CASES_V1 = 20
PAIRED_LIFT_MAX_CASES_V1 = 4096

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_HMAC_SHA256 = re.compile(r"^hmac-sha256:[0-9a-f]{64}$")
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


class PairedLiftContractError(ValueError):
    """A value is outside the paired-lift V1 evidence contract."""


class PairedLiftMode(str, Enum):
    OFF = "off"
    SHADOW = "shadow"


class ArmOutcomeV1(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    TIMEOUT = "timeout"
    ERROR = "error"
    NOT_SCORED = "not_scored"


class PairIntegrityV1(str, Enum):
    COMPLETE = "complete"
    LABEL_UNAVAILABLE = "label_unavailable"
    INFRASTRUCTURE_ERROR = "infrastructure_error"
    INPUT_MUTATED = "input_mutated"
    COMMITMENT_MISMATCH = "commitment_mismatch"
    ARM_RESULT_MISSING = "arm_result_missing"


class PairedLiftEvidenceLabel(str, Enum):
    POSITIVE = "positive_reported_paired_pass_delta"
    NO_POSITIVE = "no_positive_reported_delta"
    REGRESSION = "reported_regression"
    INCONCLUSIVE = "inconclusive"


_REASON_CODES = frozenset(
    {
        "case_count_below_minimum",
        "case_count_above_maximum",
        "case_count_mismatch",
        "duplicate_case_commitment",
        "duplicate_declared_unit_commitment",
        "duplicate_pair_evidence_commitment",
        "held_out_pair_manifest_mismatch",
        "leakage_pass_assertion_absent",
        "reported_candidate_pass_delta_negative",
        "reported_candidate_pass_delta_not_positive",
        "reported_candidate_pass_delta_positive",
        "pair_integrity_not_complete",
    }
)


def _require_sha256(value: object, label: str) -> str:
    if type(value) is not str or not _SHA256.fullmatch(value):
        raise PairedLiftContractError(f"{label} must be a canonical sha256 digest")
    return value


def _require_hmac_sha256(value: object, label: str) -> str:
    if type(value) is not str or not _HMAC_SHA256.fullmatch(value):
        raise PairedLiftContractError(
            f"{label} must be a canonical hmac-sha256 commitment"
        )
    return value


def _require_token(value: object, label: str) -> str:
    if type(value) is not str or not _TOKEN.fullmatch(value):
        raise PairedLiftContractError(f"{label} must be a bounded token")
    return value


def _require_positive_int(value: object, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise PairedLiftContractError(f"{label} must be a positive integer")
    return value


def derive_held_out_pair_manifest_digest(
    pairs: tuple[tuple[str, str], ...],
) -> str:
    """Commit to the planned case/unit membership without exporting either.

    Each entry is ``(case_commitment, declared_unit_commitment)``.  Both values
    are externally asserted HMAC-form commitments: this function validates
    their canonical shape, not MAC authenticity or key custody.
    """

    if type(pairs) is not tuple:
        raise PairedLiftContractError("held-out manifest pairs must be a tuple")
    if not pairs or len(pairs) > PAIRED_LIFT_MAX_CASES_V1:
        raise PairedLiftContractError("held-out manifest pair count is out of bounds")
    normalized: list[tuple[str, str]] = []
    for pair in pairs:
        if type(pair) is not tuple or len(pair) != 2:
            raise PairedLiftContractError(
                "held-out manifest entries must be two-item tuples"
            )
        case_commitment = _require_hmac_sha256(pair[0], "case_commitment")
        unit_commitment = _require_hmac_sha256(
            pair[1], "declared_unit_commitment"
        )
        normalized.append((case_commitment, unit_commitment))
    if len({case for case, _ in normalized}) != len(normalized):
        raise PairedLiftContractError(
            "held-out manifest contains duplicate case commitments"
        )
    if len({unit for _, unit in normalized}) != len(normalized):
        raise PairedLiftContractError(
            "held-out manifest contains duplicate declared-unit commitments"
        )
    return _held_out_pair_manifest_digest(normalized)


def _held_out_pair_manifest_digest(
    pairs: list[tuple[str, str]],
) -> str:
    return sha256_digest(
        {
            "domain": "wd.understanding.paired_lift_held_out_pair_manifest.v1",
            "pairs": [
                {"case_commitment": case, "declared_unit_commitment": unit}
                for case, unit in sorted(pairs)
            ],
        }
    )


@dataclass(frozen=True)
class PairedLiftPolicyV1:
    """Local enablement and hard evidence bounds; OFF is the shipped default."""

    mode: PairedLiftMode = PairedLiftMode.OFF
    min_cases: int = PAIRED_LIFT_MIN_CASES_V1
    max_cases: int = PAIRED_LIFT_MAX_CASES_V1

    def __post_init__(self) -> None:
        if type(self.mode) is not PairedLiftMode:
            raise PairedLiftContractError("mode must be a PairedLiftMode")
        _require_positive_int(self.min_cases, "min_cases")
        _require_positive_int(self.max_cases, "max_cases")
        if self.min_cases < PAIRED_LIFT_MIN_CASES_V1:
            raise PairedLiftContractError("min_cases is below the V1 evidence floor")
        if self.min_cases > self.max_cases:
            raise PairedLiftContractError("min_cases cannot exceed max_cases")
        if self.max_cases > PAIRED_LIFT_MAX_CASES_V1:
            raise PairedLiftContractError("max_cases exceeds the V1 hard ceiling")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "min_cases": self.min_cases,
            "max_cases": self.max_cases,
        }

    @property
    def policy_digest(self) -> str:
        return sha256_digest(
            {"domain": "wd.understanding.paired_lift_policy.v1", **self.to_mapping()}
        )


@dataclass(frozen=True)
class PairedLiftPlanV1:
    """Pre-outcome commitment for one incumbent/candidate shadow comparison."""

    campaign_id: str
    solver_family_digest: str
    cell_address_digest: str
    subdivision_address_digest: str
    candidate_artifact_digest: str
    candidate_config_digest: str
    incumbent_artifact_digest: str
    incumbent_config_digest: str
    registry_snapshot_digest: str
    held_out_pack_commitment: str
    held_out_selection_manifest_digest: str
    held_out_pair_manifest_digest: str
    arm_order_policy_digest: str
    holdout_access_policy_digest: str
    oracle_artifact_digest: str
    oracle_config_digest: str
    runner_artifact_digest: str
    toolchain_digest: str
    environment_digest: str
    resource_policy_digest: str
    planned_case_count: int
    commitment_key_id: str
    holdout_seal_asserted: bool = True
    candidate_holdout_access_blocked_asserted: bool = True
    oracle_arm_independence_asserted: bool = True
    candidate_attempt_index: int = 1
    candidate_attempt_budget: int = 1
    metric: str = PAIRED_LIFT_METRIC
    commitment_scheme: str = PAIRED_LIFT_COMMITMENT_SCHEME
    shadow_only: bool = True
    evaluation_only: bool = True

    def __post_init__(self) -> None:
        _require_token(self.campaign_id, "campaign_id")
        _require_token(self.commitment_key_id, "commitment_key_id")
        for name in (
            "solver_family_digest",
            "cell_address_digest",
            "subdivision_address_digest",
            "candidate_artifact_digest",
            "candidate_config_digest",
            "incumbent_artifact_digest",
            "incumbent_config_digest",
            "registry_snapshot_digest",
            "held_out_selection_manifest_digest",
            "held_out_pair_manifest_digest",
            "arm_order_policy_digest",
            "holdout_access_policy_digest",
            "oracle_artifact_digest",
            "oracle_config_digest",
            "runner_artifact_digest",
            "toolchain_digest",
            "environment_digest",
            "resource_policy_digest",
        ):
            _require_sha256(getattr(self, name), name)
        _require_hmac_sha256(
            self.held_out_pack_commitment, "held_out_pack_commitment"
        )
        if self.candidate_artifact_digest == self.incumbent_artifact_digest:
            raise PairedLiftContractError(
                "candidate and incumbent artifact digests must differ"
            )
        count = _require_positive_int(self.planned_case_count, "planned_case_count")
        if count < PAIRED_LIFT_MIN_CASES_V1:
            raise PairedLiftContractError(
                "planned_case_count is below the V1 evidence floor"
            )
        if count > PAIRED_LIFT_MAX_CASES_V1:
            raise PairedLiftContractError(
                "planned_case_count exceeds the V1 hard ceiling"
            )
        if type(self.metric) is not str or self.metric != PAIRED_LIFT_METRIC:
            raise PairedLiftContractError("metric must be binary oracle agreement V1")
        if (
            type(self.commitment_scheme) is not str
            or self.commitment_scheme != PAIRED_LIFT_COMMITMENT_SCHEME
        ):
            raise PairedLiftContractError(
                "commitment_scheme must use keyed V1 commitments"
            )
        for name in (
            "holdout_seal_asserted",
            "candidate_holdout_access_blocked_asserted",
            "oracle_arm_independence_asserted",
            "shadow_only",
            "evaluation_only",
        ):
            if getattr(self, name) is not True:
                raise PairedLiftContractError(f"{name} must be literal true")
        if type(self.candidate_attempt_index) is not int or self.candidate_attempt_index != 1:
            raise PairedLiftContractError("V1 requires candidate_attempt_index=1")
        if type(self.candidate_attempt_budget) is not int or self.candidate_attempt_budget != 1:
            raise PairedLiftContractError("V1 requires one predeclared candidate attempt")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": PAIRED_LIFT_PLAN_SCHEMA,
            "campaign_id": self.campaign_id,
            "solver_family_digest": self.solver_family_digest,
            "cell_address_digest": self.cell_address_digest,
            "subdivision_address_digest": self.subdivision_address_digest,
            "candidate_artifact_digest": self.candidate_artifact_digest,
            "candidate_config_digest": self.candidate_config_digest,
            "incumbent_artifact_digest": self.incumbent_artifact_digest,
            "incumbent_config_digest": self.incumbent_config_digest,
            "registry_snapshot_digest": self.registry_snapshot_digest,
            "held_out_pack_commitment": self.held_out_pack_commitment,
            "held_out_selection_manifest_digest": (
                self.held_out_selection_manifest_digest
            ),
            "held_out_pair_manifest_digest": self.held_out_pair_manifest_digest,
            "arm_order_policy_digest": self.arm_order_policy_digest,
            "holdout_access_policy_digest": self.holdout_access_policy_digest,
            "oracle_artifact_digest": self.oracle_artifact_digest,
            "oracle_config_digest": self.oracle_config_digest,
            "runner_artifact_digest": self.runner_artifact_digest,
            "toolchain_digest": self.toolchain_digest,
            "environment_digest": self.environment_digest,
            "resource_policy_digest": self.resource_policy_digest,
            "planned_case_count": self.planned_case_count,
            "commitment_key_id": self.commitment_key_id,
            "holdout_seal_asserted": True,
            "candidate_holdout_access_blocked_asserted": True,
            "oracle_arm_independence_asserted": True,
            "candidate_attempt_index": 1,
            "candidate_attempt_budget": 1,
            "metric": PAIRED_LIFT_METRIC,
            "metric_direction": "higher_is_better",
            "commitment_scheme": PAIRED_LIFT_COMMITMENT_SCHEME,
            "shadow_only": True,
            "evaluation_only": True,
        }

    @property
    def plan_digest(self) -> str:
        return sha256_digest(
            {"domain": "wd.understanding.paired_lift_plan.digest.v1", **self.to_mapping()}
        )


@dataclass(frozen=True, repr=False)
class PairedHeldOutObservationV1:
    """One raw-free pair commitment and its bounded scored outcomes."""

    case_commitment: str
    declared_unit_commitment: str
    pair_evidence_commitment: str
    candidate_outcome: ArmOutcomeV1
    incumbent_outcome: ArmOutcomeV1
    integrity: PairIntegrityV1 = PairIntegrityV1.COMPLETE

    def __post_init__(self) -> None:
        _require_hmac_sha256(self.case_commitment, "case_commitment")
        _require_hmac_sha256(
            self.declared_unit_commitment, "declared_unit_commitment"
        )
        _require_hmac_sha256(
            self.pair_evidence_commitment, "pair_evidence_commitment"
        )
        if type(self.candidate_outcome) is not ArmOutcomeV1:
            raise PairedLiftContractError(
                "candidate_outcome must be an ArmOutcomeV1"
            )
        if type(self.incumbent_outcome) is not ArmOutcomeV1:
            raise PairedLiftContractError(
                "incumbent_outcome must be an ArmOutcomeV1"
            )
        if type(self.integrity) is not PairIntegrityV1:
            raise PairedLiftContractError("integrity must be a PairIntegrityV1")
        outcomes = (self.candidate_outcome, self.incumbent_outcome)
        if self.integrity is PairIntegrityV1.COMPLETE:
            if ArmOutcomeV1.NOT_SCORED in outcomes:
                raise PairedLiftContractError(
                    "a complete pair cannot contain a not_scored arm"
                )
        elif outcomes != (ArmOutcomeV1.NOT_SCORED, ArmOutcomeV1.NOT_SCORED):
            raise PairedLiftContractError(
                "an incomplete pair must leave both arms not_scored"
            )

    def _commitment_mapping(self) -> dict[str, Any]:
        return {
            "case_commitment": self.case_commitment,
            "declared_unit_commitment": self.declared_unit_commitment,
            "pair_evidence_commitment": self.pair_evidence_commitment,
            "candidate_outcome": self.candidate_outcome.value,
            "incumbent_outcome": self.incumbent_outcome.value,
            "integrity": self.integrity.value,
        }


@dataclass(frozen=True, repr=False)
class PairedLiftRequestV1:
    """One bounded shadow request; observations remain private to evaluation."""

    plan: PairedLiftPlanV1
    observations: tuple[PairedHeldOutObservationV1, ...]
    leakage_audit_digest: str
    leakage_check_passed_asserted: bool

    def __post_init__(self) -> None:
        if type(self.plan) is not PairedLiftPlanV1:
            raise PairedLiftContractError("plan must be a PairedLiftPlanV1")
        if type(self.observations) is not tuple:
            raise PairedLiftContractError("observations must be an immutable tuple")
        if len(self.observations) > PAIRED_LIFT_MAX_CASES_V1:
            raise PairedLiftContractError("observations exceed the V1 hard ceiling")
        if any(type(row) is not PairedHeldOutObservationV1 for row in self.observations):
            raise PairedLiftContractError(
                "every observation must be a PairedHeldOutObservationV1"
            )
        _require_sha256(self.leakage_audit_digest, "leakage_audit_digest")
        if type(self.leakage_check_passed_asserted) is not bool:
            raise PairedLiftContractError(
                "leakage_check_passed_asserted must be boolean"
            )

    def _sorted_observations(self) -> tuple[PairedHeldOutObservationV1, ...]:
        return tuple(
            sorted(
                self.observations,
                key=lambda row: (
                    row.case_commitment,
                    row.declared_unit_commitment,
                    row.pair_evidence_commitment,
                    row.candidate_outcome.value,
                    row.incumbent_outcome.value,
                    row.integrity.value,
                ),
            )
        )

    @property
    def request_digest(self) -> str:
        return sha256_digest(
            {
                "domain": "wd.understanding.paired_lift_request.digest.v1",
                "schema_version": PAIRED_LIFT_REQUEST_SCHEMA,
                "plan_digest": self.plan.plan_digest,
                "leakage_audit_digest": self.leakage_audit_digest,
                "leakage_check_passed_asserted": (
                    self.leakage_check_passed_asserted
                ),
                "observations": [
                    row._commitment_mapping() for row in self._sorted_observations()
                ],
            }
        )


def _derive_evidence_decision(
    *,
    planned_case_count: int,
    required_min_cases: int,
    allowed_max_cases: int,
    case_count_received: int,
    case_count_complete: int,
    unique_case_count: int,
    unique_declared_unit_count: int,
    unique_pair_evidence_count: int,
    held_out_pair_manifest_matches_plan: bool,
    leakage_check_passed_asserted: bool,
    reported_pass_delta_numerator: int,
) -> tuple[PairedLiftEvidenceLabel, tuple[str, ...]]:
    issues: set[str] = set()
    if case_count_received < required_min_cases:
        issues.add("case_count_below_minimum")
    if case_count_received > allowed_max_cases:
        issues.add("case_count_above_maximum")
    if case_count_received != planned_case_count:
        issues.add("case_count_mismatch")
    if unique_case_count != case_count_received:
        issues.add("duplicate_case_commitment")
    if unique_declared_unit_count != case_count_received:
        issues.add("duplicate_declared_unit_commitment")
    if unique_pair_evidence_count != case_count_received:
        issues.add("duplicate_pair_evidence_commitment")
    if case_count_complete != case_count_received:
        issues.add("pair_integrity_not_complete")
    if held_out_pair_manifest_matches_plan is not True:
        issues.add("held_out_pair_manifest_mismatch")
    if leakage_check_passed_asserted is not True:
        issues.add("leakage_pass_assertion_absent")

    if issues:
        return PairedLiftEvidenceLabel.INCONCLUSIVE, tuple(sorted(issues))
    if reported_pass_delta_numerator > 0:
        return (
            PairedLiftEvidenceLabel.POSITIVE,
            ("reported_candidate_pass_delta_positive",),
        )
    if reported_pass_delta_numerator < 0:
        return (
            PairedLiftEvidenceLabel.REGRESSION,
            ("reported_candidate_pass_delta_negative",),
        )
    return (
        PairedLiftEvidenceLabel.NO_POSITIVE,
        ("reported_candidate_pass_delta_not_positive",),
    )


@dataclass(frozen=True)
class PairedLiftReceiptV1:
    """Aggregate-only, deterministic receipt for one shadow evaluation."""

    plan_digest: str
    policy_digest: str
    request_digest: str
    pair_evidence_root_digest: str
    candidate_artifact_digest: str
    incumbent_artifact_digest: str
    oracle_artifact_digest: str
    leakage_audit_digest: str
    leakage_check_passed_asserted: bool
    held_out_pair_manifest_matches_plan: bool
    planned_case_count: int
    required_min_cases: int
    allowed_max_cases: int
    case_count_received: int
    case_count_complete: int
    unique_case_count: int
    unique_declared_unit_count: int
    unique_pair_evidence_count: int
    candidate_pass_count: int
    candidate_fail_count: int
    candidate_timeout_count: int
    candidate_error_count: int
    incumbent_pass_count: int
    incumbent_fail_count: int
    incumbent_timeout_count: int
    incumbent_error_count: int
    both_pass_count: int
    candidate_only_pass_count: int
    incumbent_only_pass_count: int
    neither_pass_count: int
    reported_pass_delta_numerator: int
    reported_pass_delta_denominator: int
    evidence_label: PairedLiftEvidenceLabel
    reason_codes: tuple[str, ...]
    receipt_digest: str
    shadow_only: bool = True
    evaluation_only: bool = True
    causal_generalization_claimed: bool = False
    statistical_significance_claimed: bool = False
    temporal_holdout_seal_independently_verified: bool = False
    oracle_independence_independently_verified: bool = False
    artifact_family_cell_binding_independently_verified: bool = False
    commitment_mac_authenticity_independently_verified: bool = False
    leakage_audit_independently_verified: bool = False
    same_input_execution_independently_verified: bool = False
    statistical_unit_independence_independently_verified: bool = False
    cross_campaign_multiplicity_independently_verified: bool = False
    receipt_origin_authenticated: bool = False
    runtime_authority_applied: bool = False
    routing_influence_applied: bool = False
    solver_promotion_applied: bool = False
    builder_invoked: bool = False
    registry_write_applied: bool = False
    external_writes_applied: bool = False

    def __post_init__(self) -> None:
        for name in (
            "plan_digest",
            "policy_digest",
            "request_digest",
            "pair_evidence_root_digest",
            "candidate_artifact_digest",
            "incumbent_artifact_digest",
            "oracle_artifact_digest",
            "leakage_audit_digest",
            "receipt_digest",
        ):
            _require_sha256(getattr(self, name), name)
        if self.candidate_artifact_digest == self.incumbent_artifact_digest:
            raise PairedLiftContractError(
                "candidate and incumbent receipt artifacts must differ"
            )
        for name in (
            "leakage_check_passed_asserted",
            "held_out_pair_manifest_matches_plan",
        ):
            if type(getattr(self, name)) is not bool:
                raise PairedLiftContractError(f"{name} must be boolean")
        for name in _COUNT_FIELDS:
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise PairedLiftContractError(
                    f"{name} must be a non-negative integer"
                )
        if self.planned_case_count < PAIRED_LIFT_MIN_CASES_V1:
            raise PairedLiftContractError("planned_case_count is below the V1 floor")
        if self.planned_case_count > PAIRED_LIFT_MAX_CASES_V1:
            raise PairedLiftContractError("planned_case_count exceeds the V1 ceiling")
        if self.required_min_cases < PAIRED_LIFT_MIN_CASES_V1:
            raise PairedLiftContractError("required_min_cases is below the V1 floor")
        if self.required_min_cases > self.allowed_max_cases:
            raise PairedLiftContractError(
                "required_min_cases cannot exceed allowed_max_cases"
            )
        if self.allowed_max_cases > PAIRED_LIFT_MAX_CASES_V1:
            raise PairedLiftContractError("allowed_max_cases exceeds the V1 ceiling")
        if self.case_count_received > PAIRED_LIFT_MAX_CASES_V1:
            raise PairedLiftContractError("case_count_received exceeds the V1 ceiling")
        expected_policy_digest = sha256_digest(
            {
                "domain": "wd.understanding.paired_lift_policy.v1",
                "mode": PairedLiftMode.SHADOW.value,
                "min_cases": self.required_min_cases,
                "max_cases": self.allowed_max_cases,
            }
        )
        if self.policy_digest != expected_policy_digest:
            raise PairedLiftContractError(
                "policy_digest does not match serialized shadow policy bounds"
            )
        if type(self.reported_pass_delta_numerator) is not int:
            raise PairedLiftContractError(
                "reported_pass_delta_numerator must be an integer"
            )
        if type(self.evidence_label) is not PairedLiftEvidenceLabel:
            raise PairedLiftContractError(
                "evidence_label must be a PairedLiftEvidenceLabel"
            )
        if type(self.reason_codes) is not tuple:
            raise PairedLiftContractError("reason_codes must be an immutable tuple")
        if any(type(code) is not str for code in self.reason_codes):
            raise PairedLiftContractError("reason_codes must contain strings")
        if tuple(sorted(set(self.reason_codes))) != self.reason_codes:
            raise PairedLiftContractError("reason_codes must be sorted and unique")
        if any(code not in _REASON_CODES for code in self.reason_codes):
            raise PairedLiftContractError("receipt contains an unknown reason code")
        self._validate_count_relations()
        expected_label, expected_reasons = _derive_evidence_decision(
            planned_case_count=self.planned_case_count,
            required_min_cases=self.required_min_cases,
            allowed_max_cases=self.allowed_max_cases,
            case_count_received=self.case_count_received,
            case_count_complete=self.case_count_complete,
            unique_case_count=self.unique_case_count,
            unique_declared_unit_count=self.unique_declared_unit_count,
            unique_pair_evidence_count=self.unique_pair_evidence_count,
            held_out_pair_manifest_matches_plan=(
                self.held_out_pair_manifest_matches_plan
            ),
            leakage_check_passed_asserted=self.leakage_check_passed_asserted,
            reported_pass_delta_numerator=self.reported_pass_delta_numerator,
        )
        if self.evidence_label is not expected_label:
            raise PairedLiftContractError("evidence_label is inconsistent")
        if self.reason_codes != expected_reasons:
            raise PairedLiftContractError("reason_codes are inconsistent")
        self._validate_authority_boundary()
        expected_digest = sha256_digest(
            {
                "domain": "wd.understanding.paired_lift_receipt.digest.v1",
                **self._core_mapping(),
            }
        )
        if self.receipt_digest != expected_digest:
            raise PairedLiftContractError("receipt_digest does not match receipt fields")

    def _validate_count_relations(self) -> None:
        if self.case_count_complete > self.case_count_received:
            raise PairedLiftContractError("complete case count exceeds received count")
        for name in (
            "unique_case_count",
            "unique_declared_unit_count",
            "unique_pair_evidence_count",
        ):
            if getattr(self, name) > self.case_count_received:
                raise PairedLiftContractError(f"{name} exceeds received count")
        candidate_total = (
            self.candidate_pass_count
            + self.candidate_fail_count
            + self.candidate_timeout_count
            + self.candidate_error_count
        )
        incumbent_total = (
            self.incumbent_pass_count
            + self.incumbent_fail_count
            + self.incumbent_timeout_count
            + self.incumbent_error_count
        )
        pair_total = (
            self.both_pass_count
            + self.candidate_only_pass_count
            + self.incumbent_only_pass_count
            + self.neither_pass_count
        )
        if candidate_total != self.case_count_complete:
            raise PairedLiftContractError("candidate outcome counts do not sum")
        if incumbent_total != self.case_count_complete:
            raise PairedLiftContractError("incumbent outcome counts do not sum")
        if pair_total != self.case_count_complete:
            raise PairedLiftContractError("paired category counts do not sum")
        if self.candidate_pass_count != (
            self.both_pass_count + self.candidate_only_pass_count
        ):
            raise PairedLiftContractError("candidate pass count is inconsistent")
        if self.incumbent_pass_count != (
            self.both_pass_count + self.incumbent_only_pass_count
        ):
            raise PairedLiftContractError("incumbent pass count is inconsistent")
        expected_delta = (
            self.candidate_only_pass_count - self.incumbent_only_pass_count
        )
        if self.reported_pass_delta_numerator != expected_delta:
            raise PairedLiftContractError(
                "reported_pass_delta_numerator is inconsistent"
            )
        if self.reported_pass_delta_denominator != self.case_count_complete:
            raise PairedLiftContractError(
                "reported_pass_delta_denominator must equal complete pairs"
            )

    def _validate_authority_boundary(self) -> None:
        for name in ("shadow_only", "evaluation_only"):
            if getattr(self, name) is not True:
                raise PairedLiftContractError(f"{name} must be literal true")
        for name in _FALSE_RECEIPT_FIELDS:
            if getattr(self, name) is not False:
                raise PairedLiftContractError(f"{name} must be literal false")

    def _core_mapping(self) -> dict[str, Any]:
        values = {
            name: getattr(self, name)
            for name in _RECEIPT_VALUE_FIELDS
        }
        return _receipt_core_from_values(values)

    def to_mapping(self) -> dict[str, Any]:
        return {**self._core_mapping(), "receipt_digest": self.receipt_digest}


_COUNT_FIELDS = (
    "planned_case_count",
    "required_min_cases",
    "allowed_max_cases",
    "case_count_received",
    "case_count_complete",
    "unique_case_count",
    "unique_declared_unit_count",
    "unique_pair_evidence_count",
    "candidate_pass_count",
    "candidate_fail_count",
    "candidate_timeout_count",
    "candidate_error_count",
    "incumbent_pass_count",
    "incumbent_fail_count",
    "incumbent_timeout_count",
    "incumbent_error_count",
    "both_pass_count",
    "candidate_only_pass_count",
    "incumbent_only_pass_count",
    "neither_pass_count",
    "reported_pass_delta_denominator",
)

_FALSE_RECEIPT_FIELDS = (
    "causal_generalization_claimed",
    "statistical_significance_claimed",
    "temporal_holdout_seal_independently_verified",
    "oracle_independence_independently_verified",
    "artifact_family_cell_binding_independently_verified",
    "commitment_mac_authenticity_independently_verified",
    "leakage_audit_independently_verified",
    "same_input_execution_independently_verified",
    "statistical_unit_independence_independently_verified",
    "cross_campaign_multiplicity_independently_verified",
    "receipt_origin_authenticated",
    "runtime_authority_applied",
    "routing_influence_applied",
    "solver_promotion_applied",
    "builder_invoked",
    "registry_write_applied",
    "external_writes_applied",
)

_RECEIPT_VALUE_FIELDS = (
    "plan_digest",
    "policy_digest",
    "request_digest",
    "pair_evidence_root_digest",
    "candidate_artifact_digest",
    "incumbent_artifact_digest",
    "oracle_artifact_digest",
    "leakage_audit_digest",
    "leakage_check_passed_asserted",
    "held_out_pair_manifest_matches_plan",
    *_COUNT_FIELDS,
    "reported_pass_delta_numerator",
    "evidence_label",
    "reason_codes",
)


def evaluate_paired_lift(
    request: PairedLiftRequestV1 | None = None,
    *,
    policy: PairedLiftPolicyV1 = PairedLiftPolicyV1(),
) -> PairedLiftReceiptV1 | None:
    """Evaluate one bounded request or return immediately while default-OFF.

    The OFF branch deliberately returns before inspecting ``request``.  The
    SHADOW branch consumes only immutable, pre-scored commitments; it has no
    callback through which it could execute an arm or write external state.
    """

    if type(policy) is not PairedLiftPolicyV1:
        raise PairedLiftContractError("policy must be a PairedLiftPolicyV1")
    if policy.mode is PairedLiftMode.OFF:
        return None
    if policy.mode is not PairedLiftMode.SHADOW:
        raise PairedLiftContractError("unsupported paired-lift mode")
    if type(request) is not PairedLiftRequestV1:
        raise PairedLiftContractError(
            "shadow mode requires an exact PairedLiftRequestV1"
        )

    rows = request._sorted_observations()
    case_commitments = [row.case_commitment for row in rows]
    unit_commitments = [row.declared_unit_commitment for row in rows]
    pair_commitments = [row.pair_evidence_commitment for row in rows]
    unique_case_count = len(set(case_commitments))
    unique_unit_count = len(set(unit_commitments))
    unique_pair_evidence_count = len(set(pair_commitments))
    complete_rows = [
        row for row in rows if row.integrity is PairIntegrityV1.COMPLETE
    ]

    candidate_counts = _arm_counts(complete_rows, candidate=True)
    incumbent_counts = _arm_counts(complete_rows, candidate=False)
    pair_counts = _paired_counts(complete_rows)
    reported_pass_delta_numerator = (
        pair_counts["candidate_only_pass"]
        - pair_counts["incumbent_only_pass"]
    )
    received_count = len(rows)
    actual_pair_manifest_digest = _held_out_pair_manifest_digest(
        list(zip(case_commitments, unit_commitments))
    )
    pair_manifest_matches = (
        actual_pair_manifest_digest
        == request.plan.held_out_pair_manifest_digest
    )
    label, reason_codes = _derive_evidence_decision(
        planned_case_count=request.plan.planned_case_count,
        required_min_cases=policy.min_cases,
        allowed_max_cases=policy.max_cases,
        case_count_received=received_count,
        case_count_complete=len(complete_rows),
        unique_case_count=unique_case_count,
        unique_declared_unit_count=unique_unit_count,
        unique_pair_evidence_count=unique_pair_evidence_count,
        held_out_pair_manifest_matches_plan=pair_manifest_matches,
        leakage_check_passed_asserted=(
            request.leakage_check_passed_asserted
        ),
        reported_pass_delta_numerator=reported_pass_delta_numerator,
    )

    pair_evidence_root_digest = sha256_digest(
        {
            "domain": "wd.understanding.paired_lift_pair_evidence_root.v1",
            "pair_evidence_commitments": [
                row.pair_evidence_commitment for row in rows
            ],
        }
    )
    values: dict[str, Any] = {
        "plan_digest": request.plan.plan_digest,
        "policy_digest": policy.policy_digest,
        "request_digest": request.request_digest,
        "pair_evidence_root_digest": pair_evidence_root_digest,
        "candidate_artifact_digest": request.plan.candidate_artifact_digest,
        "incumbent_artifact_digest": request.plan.incumbent_artifact_digest,
        "oracle_artifact_digest": request.plan.oracle_artifact_digest,
        "leakage_audit_digest": request.leakage_audit_digest,
        "leakage_check_passed_asserted": (
            request.leakage_check_passed_asserted
        ),
        "held_out_pair_manifest_matches_plan": pair_manifest_matches,
        "planned_case_count": request.plan.planned_case_count,
        "required_min_cases": policy.min_cases,
        "allowed_max_cases": policy.max_cases,
        "case_count_received": received_count,
        "case_count_complete": len(complete_rows),
        "unique_case_count": unique_case_count,
        "unique_declared_unit_count": unique_unit_count,
        "unique_pair_evidence_count": unique_pair_evidence_count,
        "candidate_pass_count": candidate_counts["pass"],
        "candidate_fail_count": candidate_counts["fail"],
        "candidate_timeout_count": candidate_counts["timeout"],
        "candidate_error_count": candidate_counts["error"],
        "incumbent_pass_count": incumbent_counts["pass"],
        "incumbent_fail_count": incumbent_counts["fail"],
        "incumbent_timeout_count": incumbent_counts["timeout"],
        "incumbent_error_count": incumbent_counts["error"],
        "both_pass_count": pair_counts["both_pass"],
        "candidate_only_pass_count": pair_counts["candidate_only_pass"],
        "incumbent_only_pass_count": pair_counts["incumbent_only_pass"],
        "neither_pass_count": pair_counts["neither_pass"],
        "reported_pass_delta_numerator": reported_pass_delta_numerator,
        "reported_pass_delta_denominator": len(complete_rows),
        "evidence_label": label,
        "reason_codes": reason_codes,
    }
    provisional = PairedLiftReceiptV1(
        **values,
        receipt_digest=sha256_digest(
            {
                "domain": "wd.understanding.paired_lift_receipt.digest.v1",
                **_receipt_core_from_values(values),
            }
        ),
    )
    return provisional


def _arm_counts(
    rows: list[PairedHeldOutObservationV1],
    *,
    candidate: bool,
) -> dict[str, int]:
    counts = {"pass": 0, "fail": 0, "timeout": 0, "error": 0}
    for row in rows:
        outcome = row.candidate_outcome if candidate else row.incumbent_outcome
        counts[outcome.value] += 1
    return counts


def _paired_counts(rows: list[PairedHeldOutObservationV1]) -> dict[str, int]:
    counts = {
        "both_pass": 0,
        "candidate_only_pass": 0,
        "incumbent_only_pass": 0,
        "neither_pass": 0,
    }
    for row in rows:
        candidate_passed = row.candidate_outcome is ArmOutcomeV1.PASS
        incumbent_passed = row.incumbent_outcome is ArmOutcomeV1.PASS
        if candidate_passed and incumbent_passed:
            counts["both_pass"] += 1
        elif candidate_passed:
            counts["candidate_only_pass"] += 1
        elif incumbent_passed:
            counts["incumbent_only_pass"] += 1
        else:
            counts["neither_pass"] += 1
    return counts


def _receipt_core_from_values(values: dict[str, Any]) -> dict[str, Any]:
    """Build the exact serialized core before the frozen receipt exists."""

    return {
        "schema_version": PAIRED_LIFT_RECEIPT_SCHEMA,
        "plan_digest": values["plan_digest"],
        "policy_digest": values["policy_digest"],
        "request_digest": values["request_digest"],
        "pair_evidence_root_digest": values["pair_evidence_root_digest"],
        "candidate_artifact_digest": values["candidate_artifact_digest"],
        "incumbent_artifact_digest": values["incumbent_artifact_digest"],
        "oracle_artifact_digest": values["oracle_artifact_digest"],
        "leakage_audit_digest": values["leakage_audit_digest"],
        "leakage_check_passed_asserted": values[
            "leakage_check_passed_asserted"
        ],
        "held_out_pair_manifest_matches_plan": values[
            "held_out_pair_manifest_matches_plan"
        ],
        "planned_case_count": values["planned_case_count"],
        "required_min_cases": values["required_min_cases"],
        "allowed_max_cases": values["allowed_max_cases"],
        "case_count_received": values["case_count_received"],
        "case_count_complete": values["case_count_complete"],
        "unique_case_count": values["unique_case_count"],
        "unique_declared_unit_count": values["unique_declared_unit_count"],
        "unique_pair_evidence_count": values["unique_pair_evidence_count"],
        "candidate_outcome_counts": {
            "pass": values["candidate_pass_count"],
            "fail": values["candidate_fail_count"],
            "timeout": values["candidate_timeout_count"],
            "error": values["candidate_error_count"],
        },
        "incumbent_outcome_counts": {
            "pass": values["incumbent_pass_count"],
            "fail": values["incumbent_fail_count"],
            "timeout": values["incumbent_timeout_count"],
            "error": values["incumbent_error_count"],
        },
        "paired_counts": {
            "both_pass": values["both_pass_count"],
            "candidate_only_pass": values["candidate_only_pass_count"],
            "incumbent_only_pass": values["incumbent_only_pass_count"],
            "neither_pass": values["neither_pass_count"],
        },
        "reported_pass_delta_numerator": values[
            "reported_pass_delta_numerator"
        ],
        "reported_pass_delta_denominator": values[
            "reported_pass_delta_denominator"
        ],
        "evidence_label": values["evidence_label"].value,
        "reason_codes": list(values["reason_codes"]),
        "shadow_only": True,
        "evaluation_only": True,
        "causal_generalization_claimed": False,
        "statistical_significance_claimed": False,
        "temporal_holdout_seal_independently_verified": False,
        "oracle_independence_independently_verified": False,
        "artifact_family_cell_binding_independently_verified": False,
        "commitment_mac_authenticity_independently_verified": False,
        "leakage_audit_independently_verified": False,
        "same_input_execution_independently_verified": False,
        "statistical_unit_independence_independently_verified": False,
        "cross_campaign_multiplicity_independently_verified": False,
        "receipt_origin_authenticated": False,
        "runtime_authority_applied": False,
        "routing_influence_applied": False,
        "solver_promotion_applied": False,
        "builder_invoked": False,
        "registry_write_applied": False,
        "external_writes_applied": False,
    }


__all__ = [
    "ArmOutcomeV1",
    "PairIntegrityV1",
    "PairedHeldOutObservationV1",
    "PairedLiftContractError",
    "PairedLiftEvidenceLabel",
    "PairedLiftMode",
    "PairedLiftPlanV1",
    "PairedLiftPolicyV1",
    "PairedLiftReceiptV1",
    "PairedLiftRequestV1",
    "derive_held_out_pair_manifest_digest",
    "evaluate_paired_lift",
]
