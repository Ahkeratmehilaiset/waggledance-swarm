# SPDX-License-Identifier: Apache-2.0
"""Default-off closed paired runner for understanding shadow evidence.

The runner accepts canonical JSON only and invokes the existing closed,
allowlisted declarative solver interpreter.  It deliberately has no callback
or free-form-code seam.  Each pair is executed from the same immutable input
bytes with a fresh JSON graph per arm, in deterministic counterbalanced order,
then compared with one precommitted expected value.

The public receipt is aggregate-only.  Raw cases, expected values, solver
outputs, HMAC key material, exception details, and paths are never returned.
This is still an in-process interpreter, not an operating-system sandbox.  It
does not prove actual held-outness, oracle independence, statistical
independence, causal lift, or eligibility for any runtime action.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from waggledance.core.autonomy_growth.solver_executor import (
    execute_artifact,
    supported_executor_kinds,
)
from waggledance.core.autonomy_growth.low_risk_policy import (
    LOW_RISK_FAMILY_KINDS,
)
from waggledance.core.magma.canonical import (
    canonical_json_bytes,
    sha256_digest,
)

from .understanding_paired_evaluator import (
    ArmOutcomeV1,
    PAIRED_LIFT_MAX_CASES_V1,
    PAIRED_LIFT_MIN_CASES_V1,
    PairIntegrityV1,
    PairedHeldOutObservationV1,
    PairedLiftMode,
    PairedLiftPlanV1,
    PairedLiftPolicyV1,
    PairedLiftReceiptV1,
    PairedLiftRequestV1,
    derive_held_out_pair_manifest_digest,
    evaluate_paired_lift,
)


PAIRED_RUNNER_RECEIPT_SCHEMA = "wd.understanding.paired_runner_receipt.v1"
PAIRED_RUNNER_COMMITMENT_SCHEME = "hmac-sha256-v1"
PAIRED_RUNNER_ORDER_ALGORITHM = (
    "sorted_case_commitment_alternating_candidate_incumbent_v1"
)

PAIRED_RUNNER_DEFAULT_MAX_ARTIFACT_BYTES = 65_536
PAIRED_RUNNER_DEFAULT_MAX_CONFIG_BYTES = 32_768
PAIRED_RUNNER_DEFAULT_MAX_CASE_COMPONENT_BYTES = 65_536
PAIRED_RUNNER_DEFAULT_MAX_TOTAL_CASE_BYTES = 16_777_216
PAIRED_RUNNER_DEFAULT_MAX_OUTPUT_BYTES = 65_536
PAIRED_RUNNER_DEFAULT_MAX_TOTAL_ARTIFACT_DECODE_WORK_BYTES = 67_108_864
PAIRED_RUNNER_DEFAULT_MAX_TOTAL_OUTPUT_WORK_BYTES = 67_108_864
PAIRED_RUNNER_DEFAULT_MAX_JSON_DEPTH = 16
PAIRED_RUNNER_DEFAULT_MAX_JSON_NODES = 4_096
PAIRED_RUNNER_DEFAULT_MAX_COLLECTION_ITEMS = 1_024

_HARD_MAX_ARTIFACT_BYTES = 1_048_576
_HARD_MAX_CONFIG_BYTES = 1_048_576
_HARD_MAX_CASE_COMPONENT_BYTES = 1_048_576
_HARD_MAX_TOTAL_CASE_BYTES = 67_108_864
_HARD_MAX_OUTPUT_BYTES = 1_048_576
_HARD_MAX_TOTAL_ARTIFACT_DECODE_WORK_BYTES = 268_435_456
_HARD_MAX_TOTAL_OUTPUT_WORK_BYTES = 268_435_456
_HARD_MAX_JSON_DEPTH = 64
_HARD_MAX_JSON_NODES = 65_536
_HARD_MAX_COLLECTION_ITEMS = 8_192
_MIN_HMAC_KEY_BYTES = 32
_MAX_HMAC_KEY_BYTES = 128

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_HMAC_SHA256 = re.compile(r"^hmac-sha256:[0-9a-f]{64}$")
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_INVALID_JSON = object()
_PAIRED_RUNNER_V1_FAMILIES = frozenset(
    {
        "bounded_interpolation",
        "interval_bucket_classifier",
        "linear_arithmetic",
        "lookup_table",
        "scalar_unit_conversion",
        "threshold_rule",
    }
)


PAIRED_RUNNER_HOLDOUT_ACCESS_POLICY_DIGEST = sha256_digest(
    {
        "domain": "wd.understanding.paired_runner.holdout_access_policy.v1",
        "executor_arguments": ["detached_artifact", "detached_input"],
        "expected_value_in_executor_arguments": False,
        "comparison_after_both_arm_terminals": True,
    }
)
PAIRED_RUNNER_ORACLE_ARTIFACT_DIGEST = sha256_digest(
    {
        "domain": "wd.understanding.paired_runner.oracle_contract.v1",
        "reference": "one_precommitted_expected_json_value_per_case",
    }
)
PAIRED_RUNNER_ORACLE_CONFIG_DIGEST = sha256_digest(
    {
        "domain": "wd.understanding.paired_runner.oracle_config.v1",
        "comparison": "exact_magma_canonical_json_bytes",
    }
)


class PairedRunnerContractError(ValueError):
    """A value is outside the closed paired-runner V1 contract."""


class PairedRunnerMode(str, Enum):
    OFF = "off"
    SHADOW = "shadow"


def _require_sha256(value: object, label: str) -> str:
    if type(value) is not str or not _SHA256.fullmatch(value):
        raise PairedRunnerContractError(
            f"{label} must be a canonical sha256 digest"
        )
    return value


def _require_hmac_sha256(value: object, label: str) -> str:
    if type(value) is not str or not _HMAC_SHA256.fullmatch(value):
        raise PairedRunnerContractError(
            f"{label} must be a canonical hmac-sha256 commitment"
        )
    return value


def _require_token(value: object, label: str) -> str:
    if type(value) is not str or not _TOKEN.fullmatch(value):
        raise PairedRunnerContractError(f"{label} must be a bounded token")
    return value


def _require_bounded_int(
    value: object,
    label: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int or value < minimum or value > maximum:
        raise PairedRunnerContractError(
            f"{label} must be an integer in [{minimum}, {maximum}]"
        )
    return value


def _require_key(value: object) -> bytes:
    if type(value) is not bytes:
        raise PairedRunnerContractError("commitment_key must be exact bytes")
    if not _MIN_HMAC_KEY_BYTES <= len(value) <= _MAX_HMAC_KEY_BYTES:
        raise PairedRunnerContractError("commitment_key length is out of bounds")
    return value


def _decode_canonical_json(
    value: object,
    label: str,
    *,
    hard_max_bytes: int,
    require_mapping: bool = False,
) -> Any:
    if type(value) is not bytes:
        raise PairedRunnerContractError(f"{label} must be exact canonical JSON bytes")
    if not value or len(value) > hard_max_bytes:
        raise PairedRunnerContractError(f"{label} byte length is out of bounds")
    parsed: Any = _INVALID_JSON
    recoded: bytes | None = None
    try:
        parsed = json.loads(value.decode("utf-8"))
        recoded = canonical_json_bytes(parsed)
    except (UnicodeDecodeError, ValueError, TypeError, RecursionError):
        pass
    if parsed is _INVALID_JSON or recoded is None:
        # Raise outside the handler so the public exception does not retain a
        # decoding exception whose attributes could contain the private bytes.
        raise PairedRunnerContractError(f"{label} is not bounded canonical JSON")
    if recoded != value:
        raise PairedRunnerContractError(f"{label} is not canonical JSON")
    if require_mapping and type(parsed) is not dict:
        raise PairedRunnerContractError(f"{label} must encode a JSON object")
    return parsed


def _json_shape(value: Any) -> tuple[int, int, int]:
    """Return ``(depth, nodes, largest_collection)`` for strict JSON data."""

    max_depth = 0
    node_count = 0
    largest_collection = 0
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        node_count += 1
        max_depth = max(max_depth, depth)
        if type(current) is dict:
            largest_collection = max(largest_collection, len(current))
            stack.extend((child, depth + 1) for child in current.values())
        elif type(current) is list:
            largest_collection = max(largest_collection, len(current))
            stack.extend((child, depth + 1) for child in current)
        elif current is None or type(current) in (str, int, float, bool):
            continue
        else:  # defensive: json.loads should make this unreachable
            raise PairedRunnerContractError("value is outside strict JSON types")
    return max_depth, node_count, largest_collection


def _check_shape(
    value: Any,
    label: str,
    *,
    max_depth: int,
    max_nodes: int,
    max_collection_items: int,
) -> None:
    depth, nodes, collection_items = _json_shape(value)
    if depth > max_depth:
        raise PairedRunnerContractError(f"{label} exceeds JSON depth bound")
    if nodes > max_nodes:
        raise PairedRunnerContractError(f"{label} exceeds JSON node bound")
    if collection_items > max_collection_items:
        raise PairedRunnerContractError(f"{label} exceeds collection bound")


def _hmac_commitment(key: bytes, value: Any) -> str:
    digest = hmac.new(key, canonical_json_bytes(value), hashlib.sha256).hexdigest()
    return "hmac-sha256:" + digest


def _require_finite_number(value: object, label: str) -> float:
    if type(value) not in (int, float):
        raise PairedRunnerContractError(f"{label} must be a JSON number")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise PairedRunnerContractError(f"{label} must be finite") from None
    if not math.isfinite(result):
        raise PairedRunnerContractError(f"{label} must be finite")
    return result


def _validate_artifact_semantics(
    artifact: dict[str, Any],
    *,
    max_collection_items: int,
) -> str:
    """Refuse malformed interpreter shapes before either arm can execute."""

    kind = artifact.get("kind")
    if type(kind) is not str:
        raise PairedRunnerContractError("artifact kind must be an exact string")
    if (
        kind not in _PAIRED_RUNNER_V1_FAMILIES
        or kind not in LOW_RISK_FAMILY_KINDS
        or kind not in supported_executor_kinds()
    ):
        raise PairedRunnerContractError("artifact family has no closed V1 executor")

    if kind == "scalar_unit_conversion":
        _require_finite_number(artifact.get("factor"), "factor")
        if "offset" in artifact:
            _require_finite_number(artifact["offset"], "offset")
    elif kind == "lookup_table":
        table = artifact.get("table")
        if type(table) is not dict:
            raise PairedRunnerContractError("lookup table must be a JSON object")
        if len(table) > max_collection_items:
            raise PairedRunnerContractError("lookup table exceeds collection bound")
        if "size" in artifact and (
            type(artifact["size"]) is not int or artifact["size"] != len(table)
        ):
            raise PairedRunnerContractError("lookup table size metadata is inconsistent")
    elif kind == "threshold_rule":
        operator = artifact.get("operator")
        if type(operator) is not str or operator not in (
            ">",
            ">=",
            "<",
            "<=",
            "==",
            "!=",
        ):
            raise PairedRunnerContractError("threshold operator is unsupported")
        _require_finite_number(artifact.get("threshold"), "threshold")
        for name in ("true_label", "false_label"):
            if name not in artifact:
                raise PairedRunnerContractError(f"threshold artifact lacks {name}")
    elif kind == "interval_bucket_classifier":
        intervals = artifact.get("intervals")
        if type(intervals) is not list:
            raise PairedRunnerContractError("intervals must be a JSON array")
        if len(intervals) > max_collection_items:
            raise PairedRunnerContractError("intervals exceed collection bound")
        for interval in intervals:
            if type(interval) is not dict:
                raise PairedRunnerContractError("interval entry must be a JSON object")
            minimum = _require_finite_number(interval.get("min"), "interval min")
            maximum = _require_finite_number(interval.get("max"), "interval max")
            if minimum > maximum:
                raise PairedRunnerContractError("interval min exceeds max")
    elif kind == "linear_arithmetic":
        coefficients = artifact.get("coefficients")
        if type(coefficients) is not list:
            raise PairedRunnerContractError("coefficients must be a JSON array")
        if len(coefficients) > max_collection_items:
            raise PairedRunnerContractError("coefficients exceed collection bound")
        for coefficient in coefficients:
            _require_finite_number(coefficient, "coefficient")
        _require_finite_number(artifact.get("intercept"), "intercept")
        columns = artifact.get("input_columns", [])
        if type(columns) is not list or any(type(column) is not str for column in columns):
            raise PairedRunnerContractError("input_columns must be a string array")
        if len(columns) > max_collection_items:
            raise PairedRunnerContractError("input_columns exceed collection bound")
        if columns and len(columns) != len(coefficients):
            raise PairedRunnerContractError("input_columns length is inconsistent")
    elif kind == "bounded_interpolation":
        knots = artifact.get("knots")
        if type(knots) is not list or len(knots) < 2:
            raise PairedRunnerContractError("knots must be an array of at least two")
        if len(knots) > max_collection_items:
            raise PairedRunnerContractError("knots exceed collection bound")
        knot_xs: list[float] = []
        for knot in knots:
            if type(knot) is not dict:
                raise PairedRunnerContractError("knot must be a JSON object")
            knot_xs.append(_require_finite_number(knot.get("x"), "knot x"))
            _require_finite_number(knot.get("y"), "knot y")
        if knot_xs != sorted(knot_xs):
            raise PairedRunnerContractError("knots must be sorted by x")
        minimum = _require_finite_number(artifact.get("min_x"), "min_x")
        maximum = _require_finite_number(artifact.get("max_x"), "max_x")
        if minimum > maximum:
            raise PairedRunnerContractError("min_x exceeds max_x")
        if artifact.get("method", "linear") != "linear":
            raise PairedRunnerContractError("only linear interpolation is executable")
        if artifact.get("out_of_range_policy", "clip") not in (
            "clip",
            "null",
            "raise",
        ):
            raise PairedRunnerContractError("out-of-range policy is unsupported")
    else:  # defensive against allowlist/executor drift
        raise PairedRunnerContractError("artifact family validator is absent")
    return kind


def derive_paired_runner_artifact_digest(artifact_json: bytes) -> str:
    """Domain-hash one detached declarative artifact."""

    artifact = _decode_canonical_json(
        artifact_json,
        "artifact_json",
        hard_max_bytes=_HARD_MAX_ARTIFACT_BYTES,
        require_mapping=True,
    )
    return sha256_digest(
        {
            "domain": "wd.understanding.paired_runner.artifact.v1",
            "artifact": artifact,
        }
    )


def derive_paired_runner_config_digest(config_json: bytes) -> str:
    """Domain-hash one detached configuration snapshot."""

    config = _decode_canonical_json(
        config_json,
        "config_json",
        hard_max_bytes=_HARD_MAX_CONFIG_BYTES,
        require_mapping=True,
    )
    return sha256_digest(
        {
            "domain": "wd.understanding.paired_runner.config.v1",
            "config": config,
        }
    )


def derive_paired_runner_solver_family_digest(family_kind: str) -> str:
    """Bind a plan to one exact closed executor family name."""

    _require_token(family_kind, "family_kind")
    if (
        family_kind not in _PAIRED_RUNNER_V1_FAMILIES
        or family_kind not in LOW_RISK_FAMILY_KINDS
        or family_kind not in supported_executor_kinds()
    ):
        raise PairedRunnerContractError("family_kind has no closed V1 executor")
    return sha256_digest(
        {
            "domain": "wd.understanding.paired_runner.solver_family.v1",
            "family_kind": family_kind,
        }
    )


@dataclass(frozen=True)
class PairedRunnerPolicyV1:
    """Local enablement and deterministic structural limits."""

    mode: PairedRunnerMode = PairedRunnerMode.OFF
    min_cases: int = PAIRED_LIFT_MIN_CASES_V1
    max_cases: int = PAIRED_LIFT_MAX_CASES_V1
    max_artifact_bytes: int = PAIRED_RUNNER_DEFAULT_MAX_ARTIFACT_BYTES
    max_config_bytes: int = PAIRED_RUNNER_DEFAULT_MAX_CONFIG_BYTES
    max_case_component_bytes: int = PAIRED_RUNNER_DEFAULT_MAX_CASE_COMPONENT_BYTES
    max_total_case_bytes: int = PAIRED_RUNNER_DEFAULT_MAX_TOTAL_CASE_BYTES
    max_output_bytes: int = PAIRED_RUNNER_DEFAULT_MAX_OUTPUT_BYTES
    max_total_artifact_decode_work_bytes: int = (
        PAIRED_RUNNER_DEFAULT_MAX_TOTAL_ARTIFACT_DECODE_WORK_BYTES
    )
    max_total_output_work_bytes: int = (
        PAIRED_RUNNER_DEFAULT_MAX_TOTAL_OUTPUT_WORK_BYTES
    )
    max_json_depth: int = PAIRED_RUNNER_DEFAULT_MAX_JSON_DEPTH
    max_json_nodes: int = PAIRED_RUNNER_DEFAULT_MAX_JSON_NODES
    max_collection_items: int = PAIRED_RUNNER_DEFAULT_MAX_COLLECTION_ITEMS

    def __post_init__(self) -> None:
        if type(self.mode) is not PairedRunnerMode:
            raise PairedRunnerContractError("mode must be a PairedRunnerMode")
        _require_bounded_int(
            self.min_cases,
            "min_cases",
            minimum=PAIRED_LIFT_MIN_CASES_V1,
            maximum=PAIRED_LIFT_MAX_CASES_V1,
        )
        _require_bounded_int(
            self.max_cases,
            "max_cases",
            minimum=PAIRED_LIFT_MIN_CASES_V1,
            maximum=PAIRED_LIFT_MAX_CASES_V1,
        )
        if self.min_cases > self.max_cases:
            raise PairedRunnerContractError("min_cases cannot exceed max_cases")
        bounds = (
            ("max_artifact_bytes", self.max_artifact_bytes, _HARD_MAX_ARTIFACT_BYTES),
            ("max_config_bytes", self.max_config_bytes, _HARD_MAX_CONFIG_BYTES),
            (
                "max_case_component_bytes",
                self.max_case_component_bytes,
                _HARD_MAX_CASE_COMPONENT_BYTES,
            ),
            (
                "max_total_case_bytes",
                self.max_total_case_bytes,
                _HARD_MAX_TOTAL_CASE_BYTES,
            ),
            ("max_output_bytes", self.max_output_bytes, _HARD_MAX_OUTPUT_BYTES),
            (
                "max_total_artifact_decode_work_bytes",
                self.max_total_artifact_decode_work_bytes,
                _HARD_MAX_TOTAL_ARTIFACT_DECODE_WORK_BYTES,
            ),
            (
                "max_total_output_work_bytes",
                self.max_total_output_work_bytes,
                _HARD_MAX_TOTAL_OUTPUT_WORK_BYTES,
            ),
            ("max_json_depth", self.max_json_depth, _HARD_MAX_JSON_DEPTH),
            ("max_json_nodes", self.max_json_nodes, _HARD_MAX_JSON_NODES),
            (
                "max_collection_items",
                self.max_collection_items,
                _HARD_MAX_COLLECTION_ITEMS,
            ),
        )
        for name, value, maximum in bounds:
            _require_bounded_int(value, name, minimum=1, maximum=maximum)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "min_cases": self.min_cases,
            "max_cases": self.max_cases,
            "max_artifact_bytes": self.max_artifact_bytes,
            "max_config_bytes": self.max_config_bytes,
            "max_case_component_bytes": self.max_case_component_bytes,
            "max_total_case_bytes": self.max_total_case_bytes,
            "max_output_bytes": self.max_output_bytes,
            "max_total_artifact_decode_work_bytes": (
                self.max_total_artifact_decode_work_bytes
            ),
            "max_total_output_work_bytes": self.max_total_output_work_bytes,
            "max_json_depth": self.max_json_depth,
            "max_json_nodes": self.max_json_nodes,
            "max_collection_items": self.max_collection_items,
        }

    @property
    def policy_digest(self) -> str:
        return sha256_digest(
            {
                "domain": "wd.understanding.paired_runner.policy.v1",
                **self.to_mapping(),
            }
        )

    @property
    def resource_policy_digest(self) -> str:
        values = self.to_mapping()
        values.pop("mode")
        return sha256_digest(
            {
                "domain": "wd.understanding.paired_runner.resource_policy.v1",
                **values,
            }
        )


@dataclass(frozen=True, repr=False)
class PairedRunnerCaseV1:
    """One private strict-JSON case; no field is safe for public export."""

    input_json: bytes
    expected_output_json: bytes
    declared_unit_json: bytes

    def __post_init__(self) -> None:
        for name in ("input_json", "expected_output_json", "declared_unit_json"):
            parsed = _decode_canonical_json(
                getattr(self, name),
                name,
                hard_max_bytes=_HARD_MAX_CASE_COMPONENT_BYTES,
                require_mapping=(name == "input_json"),
            )
            _check_shape(
                parsed,
                name,
                max_depth=_HARD_MAX_JSON_DEPTH,
                max_nodes=_HARD_MAX_JSON_NODES,
                max_collection_items=_HARD_MAX_COLLECTION_ITEMS,
            )


@dataclass(frozen=True)
class PairedRunnerCommitmentsV1:
    """Aggregate pre-outcome commitments needed to construct a C6 plan."""

    campaign_id: str
    case_count: int
    held_out_pack_commitment: str
    held_out_selection_manifest_digest: str
    held_out_pair_manifest_digest: str
    arm_order_policy_digest: str
    commitment_scheme: str = PAIRED_RUNNER_COMMITMENT_SCHEME

    def __post_init__(self) -> None:
        _require_token(self.campaign_id, "campaign_id")
        _require_bounded_int(
            self.case_count,
            "case_count",
            minimum=PAIRED_LIFT_MIN_CASES_V1,
            maximum=PAIRED_LIFT_MAX_CASES_V1,
        )
        _require_hmac_sha256(
            self.held_out_pack_commitment, "held_out_pack_commitment"
        )
        for name in (
            "held_out_selection_manifest_digest",
            "held_out_pair_manifest_digest",
            "arm_order_policy_digest",
        ):
            _require_sha256(getattr(self, name), name)
        if (
            type(self.commitment_scheme) is not str
            or self.commitment_scheme != PAIRED_RUNNER_COMMITMENT_SCHEME
        ):
            raise PairedRunnerContractError("unsupported commitment scheme")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "case_count": self.case_count,
            "held_out_pack_commitment": self.held_out_pack_commitment,
            "held_out_selection_manifest_digest": (
                self.held_out_selection_manifest_digest
            ),
            "held_out_pair_manifest_digest": self.held_out_pair_manifest_digest,
            "arm_order_policy_digest": self.arm_order_policy_digest,
            "commitment_scheme": self.commitment_scheme,
        }


@dataclass(frozen=True, repr=False)
class _PreparedCaseV1:
    input_json: bytes
    expected_output_json: bytes
    case_commitment: str
    declared_unit_commitment: str
    expected_output_commitment: str


def _prepare_cases(
    *,
    campaign_id: str,
    cases: tuple[PairedRunnerCaseV1, ...],
    key: bytes,
) -> tuple[_PreparedCaseV1, ...]:
    prepared: list[_PreparedCaseV1] = []
    for case in cases:
        unit_value = json.loads(case.declared_unit_json.decode("utf-8"))
        unit_commitment = _hmac_commitment(
            key,
            {
                "domain": "wd.understanding.paired_runner.declared_unit.v1",
                "campaign_id": campaign_id,
                "declared_unit": unit_value,
            },
        )
        input_value = json.loads(case.input_json.decode("utf-8"))
        case_commitment = _hmac_commitment(
            key,
            {
                "domain": "wd.understanding.paired_runner.case.v1",
                "campaign_id": campaign_id,
                "declared_unit_commitment": unit_commitment,
                "input": input_value,
            },
        )
        expected_value = json.loads(case.expected_output_json.decode("utf-8"))
        expected_commitment = _hmac_commitment(
            key,
            {
                "domain": "wd.understanding.paired_runner.expected_output.v1",
                "campaign_id": campaign_id,
                "case_commitment": case_commitment,
                "expected_output": expected_value,
            },
        )
        prepared.append(
            _PreparedCaseV1(
                input_json=case.input_json,
                expected_output_json=case.expected_output_json,
                case_commitment=case_commitment,
                declared_unit_commitment=unit_commitment,
                expected_output_commitment=expected_commitment,
            )
        )
    prepared.sort(
        key=lambda row: (
            row.case_commitment,
            row.declared_unit_commitment,
            row.expected_output_commitment,
        )
    )
    case_commitments = [row.case_commitment for row in prepared]
    unit_commitments = [row.declared_unit_commitment for row in prepared]
    if len(set(case_commitments)) != len(prepared):
        raise PairedRunnerContractError("duplicate materialized case commitment")
    if len(set(unit_commitments)) != len(prepared):
        raise PairedRunnerContractError("duplicate declared-unit commitment")
    return tuple(prepared)


def _total_case_bytes(cases: tuple[PairedRunnerCaseV1, ...]) -> int:
    return sum(
        len(getattr(case, name))
        for case in cases
        for name in ("input_json", "expected_output_json", "declared_unit_json")
    )


def _commitments_from_prepared(
    *,
    campaign_id: str,
    prepared: tuple[_PreparedCaseV1, ...],
    key: bytes,
) -> PairedRunnerCommitmentsV1:
    pair_manifest = derive_held_out_pair_manifest_digest(
        tuple(
            (row.case_commitment, row.declared_unit_commitment)
            for row in prepared
        )
    )
    selection_entries = [
        {
            "case_commitment": row.case_commitment,
            "declared_unit_commitment": row.declared_unit_commitment,
            "expected_output_commitment": row.expected_output_commitment,
        }
        for row in prepared
    ]
    selection_manifest = sha256_digest(
        {
            "domain": "wd.understanding.paired_runner.selection_manifest.v1",
            "campaign_id": campaign_id,
            "entries": selection_entries,
        }
    )
    order_assignments = [
        {
            "case_commitment": row.case_commitment,
            "logical_ordinal": index,
            "first_arm": "candidate" if index % 2 == 0 else "incumbent",
        }
        for index, row in enumerate(prepared)
    ]
    order_policy = sha256_digest(
        {
            "domain": "wd.understanding.paired_runner.arm_order_policy.v1",
            "algorithm": PAIRED_RUNNER_ORDER_ALGORITHM,
            "assignments": order_assignments,
        }
    )
    pack_commitment = _hmac_commitment(
        key,
        {
            "domain": "wd.understanding.paired_runner.held_out_pack.v1",
            "campaign_id": campaign_id,
            "selection_manifest_digest": selection_manifest,
            "pair_manifest_digest": pair_manifest,
            "arm_order_policy_digest": order_policy,
            "case_count": len(prepared),
        },
    )
    return PairedRunnerCommitmentsV1(
        campaign_id=campaign_id,
        case_count=len(prepared),
        held_out_pack_commitment=pack_commitment,
        held_out_selection_manifest_digest=selection_manifest,
        held_out_pair_manifest_digest=pair_manifest,
        arm_order_policy_digest=order_policy,
    )


def derive_paired_runner_commitments(
    *,
    campaign_id: str,
    cases: tuple[PairedRunnerCaseV1, ...],
    commitment_key: bytes,
) -> PairedRunnerCommitmentsV1:
    """Derive raw-free plan bindings from one private canonical corpus."""

    _require_token(campaign_id, "campaign_id")
    key = _require_key(commitment_key)
    if type(cases) is not tuple or any(
        type(case) is not PairedRunnerCaseV1 for case in cases
    ):
        raise PairedRunnerContractError("cases must be an exact immutable tuple")
    _require_bounded_int(
        len(cases),
        "case_count",
        minimum=PAIRED_LIFT_MIN_CASES_V1,
        maximum=PAIRED_LIFT_MAX_CASES_V1,
    )
    if _total_case_bytes(cases) > _HARD_MAX_TOTAL_CASE_BYTES:
        raise PairedRunnerContractError("case corpus exceeds the V1 hard byte ceiling")
    prepared = _prepare_cases(campaign_id=campaign_id, cases=cases, key=key)
    return _commitments_from_prepared(
        campaign_id=campaign_id,
        prepared=prepared,
        key=key,
    )


@dataclass(frozen=True, repr=False)
class PairedRunnerRequestV1:
    """One private request.  Key material is supplied only at invocation."""

    plan: PairedLiftPlanV1
    candidate_artifact_json: bytes
    candidate_config_json: bytes
    incumbent_artifact_json: bytes
    incumbent_config_json: bytes
    cases: tuple[PairedRunnerCaseV1, ...]
    commitment_key_id: str
    leakage_audit_digest: str
    leakage_check_passed_asserted: bool = False

    def __post_init__(self) -> None:
        if type(self.plan) is not PairedLiftPlanV1:
            raise PairedRunnerContractError("plan must be an exact PairedLiftPlanV1")
        _require_token(self.commitment_key_id, "commitment_key_id")
        _require_sha256(self.leakage_audit_digest, "leakage_audit_digest")
        if type(self.leakage_check_passed_asserted) is not bool:
            raise PairedRunnerContractError(
                "leakage_check_passed_asserted must be boolean"
            )
        for name, maximum in (
            ("candidate_artifact_json", _HARD_MAX_ARTIFACT_BYTES),
            ("incumbent_artifact_json", _HARD_MAX_ARTIFACT_BYTES),
            ("candidate_config_json", _HARD_MAX_CONFIG_BYTES),
            ("incumbent_config_json", _HARD_MAX_CONFIG_BYTES),
        ):
            _decode_canonical_json(
                getattr(self, name),
                name,
                hard_max_bytes=maximum,
                require_mapping=True,
            )
        if type(self.cases) is not tuple or any(
            type(case) is not PairedRunnerCaseV1 for case in self.cases
        ):
            raise PairedRunnerContractError("cases must be an exact immutable tuple")
        if len(self.cases) > PAIRED_LIFT_MAX_CASES_V1:
            raise PairedRunnerContractError("case count exceeds the V1 hard ceiling")
        if _total_case_bytes(self.cases) > _HARD_MAX_TOTAL_CASE_BYTES:
            raise PairedRunnerContractError(
                "case corpus exceeds the V1 hard byte ceiling"
            )


@dataclass(frozen=True, repr=False)
class _ArmTerminalV1:
    output_json: bytes | None
    output_commitment: str
    error: bool
    input_final_state_matches: bool
    artifact_final_state_matches: bool


def _execute_arm(
    *,
    artifact_json: bytes,
    input_json: bytes,
    artifact_digest: str,
    case_commitment: str,
    arm: str,
    max_output_bytes: int,
) -> _ArmTerminalV1:
    artifact = json.loads(artifact_json.decode("utf-8"))
    inputs = json.loads(input_json.decode("utf-8"))
    initial_artifact = canonical_json_bytes(artifact)
    initial_input = canonical_json_bytes(inputs)
    output_json: bytes | None = None
    error = False
    try:
        output = execute_artifact(artifact, inputs)
        output_json = canonical_json_bytes(output)
        if len(output_json) > max_output_bytes:
            output_json = None
            error = True
    except Exception:  # noqa: BLE001 - details intentionally stay private
        error = True
    try:
        input_matches = canonical_json_bytes(inputs) == initial_input
    except (TypeError, ValueError, RecursionError):
        input_matches = False
    try:
        artifact_matches = canonical_json_bytes(artifact) == initial_artifact
    except (TypeError, ValueError, RecursionError):
        artifact_matches = False
    if error:
        output_commitment = sha256_digest(
            {
                "domain": "wd.understanding.paired_runner.arm_terminal.v1",
                "artifact_digest": artifact_digest,
                "case_commitment": case_commitment,
                "arm": arm,
                "terminal": "error",
            }
        )
    else:
        output_commitment = sha256_digest(
            {
                "domain": "wd.understanding.paired_runner.arm_terminal.v1",
                "artifact_digest": artifact_digest,
                "case_commitment": case_commitment,
                "arm": arm,
                "terminal": "returned",
                "output": json.loads(output_json.decode("utf-8")),
            }
        )
    return _ArmTerminalV1(
        output_json=output_json,
        output_commitment=output_commitment,
        error=error,
        input_final_state_matches=input_matches,
        artifact_final_state_matches=artifact_matches,
    )


_TRUE_RECEIPT_FIELDS = (
    "shadow_only",
    "evaluation_only",
    "closed_executor_api_selected",
    "request_callback_seam_absent",
    "same_canonical_input_bytes_per_pair",
    "fresh_json_graph_per_arm",
    "expected_outputs_omitted_from_executor_arguments",
    "outputs_snapshotted_before_expected_output_comparison",
    "deterministic_counterbalanced_order_applied",
    "artifact_config_manifest_commitments_recomputed",
    "post_return_argument_canonical_bytes_checked",
)

_FALSE_RECEIPT_FIELDS = (
    "transient_mutation_absence_claimed",
    "randomized_assignment_applied",
    "order_effect_absence_verified",
    "os_sandbox_applied",
    "process_isolation_applied",
    "filesystem_isolation_independently_verified",
    "network_isolation_independently_verified",
    "wall_clock_timeout_enforced",
    "cpu_memory_quota_enforced",
    "actual_held_outness_independently_verified",
    "candidate_development_leakage_independently_verified",
    "oracle_independence_independently_verified",
    "commitment_key_custody_independently_verified",
    "commitment_key_zeroization_verified",
    "config_artifact_semantic_binding_verified",
    "hex_cell_binding_independently_verified",
    "registry_snapshot_identity_independently_verified",
    "plan_external_pin_independently_verified",
    "runner_artifact_identity_verified",
    "toolchain_environment_independently_verified",
    "statistical_unit_independence_independently_verified",
    "cross_campaign_multiplicity_independently_verified",
    "causal_generalization_claimed",
    "statistical_significance_claimed",
    "receipt_origin_authenticated",
    "executor_effects_independently_verified",
    "runtime_authority_requested_by_runner",
    "routing_influence_requested_by_runner",
    "solver_promotion_requested_by_runner",
    "builder_requested_by_runner",
    "registry_write_requested_by_runner",
    "external_writes_requested_by_runner",
)


@dataclass(frozen=True)
class PairedRunnerReceiptV1:
    """Aggregate-only result of one closed in-process shadow invocation."""

    plan_digest: str
    runner_policy_digest: str
    runner_request_digest: str
    arm_order_policy_digest: str
    execution_trace_digest: str
    execution_evidence_root_digest: str
    case_count: int
    complete_pair_count: int
    post_return_state_mismatch_pair_count: int
    candidate_first_count: int
    incumbent_first_count: int
    max_artifact_bytes: int
    max_config_bytes: int
    max_case_component_bytes: int
    max_total_case_bytes: int
    max_output_bytes: int
    max_total_artifact_decode_work_bytes: int
    max_total_output_work_bytes: int
    max_json_depth: int
    max_json_nodes: int
    max_collection_items: int
    paired_lift_receipt: PairedLiftReceiptV1
    receipt_digest: str
    schema_version: str = PAIRED_RUNNER_RECEIPT_SCHEMA
    shadow_only: bool = True
    evaluation_only: bool = True
    closed_executor_api_selected: bool = True
    request_callback_seam_absent: bool = True
    same_canonical_input_bytes_per_pair: bool = True
    fresh_json_graph_per_arm: bool = True
    expected_outputs_omitted_from_executor_arguments: bool = True
    outputs_snapshotted_before_expected_output_comparison: bool = True
    deterministic_counterbalanced_order_applied: bool = True
    artifact_config_manifest_commitments_recomputed: bool = True
    post_return_argument_canonical_bytes_checked: bool = True
    transient_mutation_absence_claimed: bool = False
    randomized_assignment_applied: bool = False
    order_effect_absence_verified: bool = False
    os_sandbox_applied: bool = False
    process_isolation_applied: bool = False
    filesystem_isolation_independently_verified: bool = False
    network_isolation_independently_verified: bool = False
    wall_clock_timeout_enforced: bool = False
    cpu_memory_quota_enforced: bool = False
    actual_held_outness_independently_verified: bool = False
    candidate_development_leakage_independently_verified: bool = False
    oracle_independence_independently_verified: bool = False
    commitment_key_custody_independently_verified: bool = False
    commitment_key_zeroization_verified: bool = False
    config_artifact_semantic_binding_verified: bool = False
    hex_cell_binding_independently_verified: bool = False
    registry_snapshot_identity_independently_verified: bool = False
    plan_external_pin_independently_verified: bool = False
    runner_artifact_identity_verified: bool = False
    toolchain_environment_independently_verified: bool = False
    statistical_unit_independence_independently_verified: bool = False
    cross_campaign_multiplicity_independently_verified: bool = False
    causal_generalization_claimed: bool = False
    statistical_significance_claimed: bool = False
    receipt_origin_authenticated: bool = False
    executor_effects_independently_verified: bool = False
    runtime_authority_requested_by_runner: bool = False
    routing_influence_requested_by_runner: bool = False
    solver_promotion_requested_by_runner: bool = False
    builder_requested_by_runner: bool = False
    registry_write_requested_by_runner: bool = False
    external_writes_requested_by_runner: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not str
            or self.schema_version != PAIRED_RUNNER_RECEIPT_SCHEMA
        ):
            raise PairedRunnerContractError("unsupported receipt schema")
        for name in (
            "plan_digest",
            "runner_policy_digest",
            "runner_request_digest",
            "arm_order_policy_digest",
            "execution_trace_digest",
            "execution_evidence_root_digest",
            "receipt_digest",
        ):
            _require_sha256(getattr(self, name), name)
        if type(self.paired_lift_receipt) is not PairedLiftReceiptV1:
            raise PairedRunnerContractError(
                "paired_lift_receipt must be an exact PairedLiftReceiptV1"
            )
        scalar_counts = (
            "case_count",
            "complete_pair_count",
            "post_return_state_mismatch_pair_count",
            "candidate_first_count",
            "incumbent_first_count",
        )
        for name in scalar_counts:
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise PairedRunnerContractError(f"{name} must be non-negative")
        if self.case_count != self.paired_lift_receipt.case_count_received:
            raise PairedRunnerContractError("runner and evaluator case counts differ")
        if self.complete_pair_count != self.paired_lift_receipt.case_count_complete:
            raise PairedRunnerContractError("complete pair count is inconsistent")
        if self.post_return_state_mismatch_pair_count != (
            self.case_count - self.complete_pair_count
        ):
            raise PairedRunnerContractError("state mismatch pair count is inconsistent")
        if self.candidate_first_count != (self.case_count + 1) // 2:
            raise PairedRunnerContractError("candidate-first count is inconsistent")
        if self.incumbent_first_count != self.case_count // 2:
            raise PairedRunnerContractError("incumbent-first count is inconsistent")
        if self.plan_digest != self.paired_lift_receipt.plan_digest:
            raise PairedRunnerContractError("runner and evaluator plans differ")
        if (
            self.execution_evidence_root_digest
            != self.paired_lift_receipt.pair_evidence_root_digest
        ):
            raise PairedRunnerContractError("execution evidence roots differ")
        for name in _TRUE_RECEIPT_FIELDS:
            if getattr(self, name) is not True:
                raise PairedRunnerContractError(f"{name} must be literal true")
        for name in _FALSE_RECEIPT_FIELDS:
            if getattr(self, name) is not False:
                raise PairedRunnerContractError(f"{name} must be literal false")
        expected_policy = PairedRunnerPolicyV1(
            mode=PairedRunnerMode.SHADOW,
            min_cases=self.paired_lift_receipt.required_min_cases,
            max_cases=self.paired_lift_receipt.allowed_max_cases,
            max_artifact_bytes=self.max_artifact_bytes,
            max_config_bytes=self.max_config_bytes,
            max_case_component_bytes=self.max_case_component_bytes,
            max_total_case_bytes=self.max_total_case_bytes,
            max_output_bytes=self.max_output_bytes,
            max_total_artifact_decode_work_bytes=(
                self.max_total_artifact_decode_work_bytes
            ),
            max_total_output_work_bytes=self.max_total_output_work_bytes,
            max_json_depth=self.max_json_depth,
            max_json_nodes=self.max_json_nodes,
            max_collection_items=self.max_collection_items,
        )
        if self.runner_policy_digest != expected_policy.policy_digest:
            raise PairedRunnerContractError("runner policy digest is inconsistent")
        expected_receipt_digest = sha256_digest(
            {
                "domain": "wd.understanding.paired_runner.receipt.digest.v1",
                **self._core_mapping(),
            }
        )
        if self.receipt_digest != expected_receipt_digest:
            raise PairedRunnerContractError("receipt digest does not match fields")

    def _core_mapping(self) -> dict[str, Any]:
        values = {
            name: getattr(self, name)
            for name in (
                "schema_version",
                "plan_digest",
                "runner_policy_digest",
                "runner_request_digest",
                "arm_order_policy_digest",
                "execution_trace_digest",
                "execution_evidence_root_digest",
                "case_count",
                "complete_pair_count",
                "post_return_state_mismatch_pair_count",
                "candidate_first_count",
                "incumbent_first_count",
                "max_artifact_bytes",
                "max_config_bytes",
                "max_case_component_bytes",
                "max_total_case_bytes",
                "max_output_bytes",
                "max_total_artifact_decode_work_bytes",
                "max_total_output_work_bytes",
                "max_json_depth",
                "max_json_nodes",
                "max_collection_items",
                *_TRUE_RECEIPT_FIELDS,
                *_FALSE_RECEIPT_FIELDS,
            )
        }
        values["paired_lift_receipt"] = self.paired_lift_receipt.to_mapping()
        return values

    def to_mapping(self) -> dict[str, Any]:
        return {**self._core_mapping(), "receipt_digest": self.receipt_digest}


def _validate_policy_shape(
    *,
    request: PairedRunnerRequestV1,
    policy: PairedRunnerPolicyV1,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not policy.min_cases <= len(request.cases) <= policy.max_cases:
        raise PairedRunnerContractError("case count is outside runner policy")
    if len(request.cases) != request.plan.planned_case_count:
        raise PairedRunnerContractError("case count differs from committed plan")
    byte_fields = (
        (request.candidate_artifact_json, policy.max_artifact_bytes, "candidate artifact"),
        (request.incumbent_artifact_json, policy.max_artifact_bytes, "incumbent artifact"),
        (request.candidate_config_json, policy.max_config_bytes, "candidate config"),
        (request.incumbent_config_json, policy.max_config_bytes, "incumbent config"),
    )
    for value, maximum, label in byte_fields:
        if len(value) > maximum:
            raise PairedRunnerContractError(f"{label} exceeds policy byte bound")
    total_case_bytes = 0
    for case in request.cases:
        for name in ("input_json", "expected_output_json", "declared_unit_json"):
            raw = getattr(case, name)
            if len(raw) > policy.max_case_component_bytes:
                raise PairedRunnerContractError(
                    "case component exceeds policy byte bound"
                )
            total_case_bytes += len(raw)
            value = json.loads(raw.decode("utf-8"))
            _check_shape(
                value,
                name,
                max_depth=policy.max_json_depth,
                max_nodes=policy.max_json_nodes,
                max_collection_items=policy.max_collection_items,
            )
    if total_case_bytes > policy.max_total_case_bytes:
        raise PairedRunnerContractError("case corpus exceeds total byte bound")
    artifact_decode_work = len(request.cases) * (
        len(request.candidate_artifact_json)
        + len(request.incumbent_artifact_json)
    )
    if artifact_decode_work > policy.max_total_artifact_decode_work_bytes:
        raise PairedRunnerContractError(
            "paired artifact decode work exceeds total byte-work bound"
        )
    output_work_upper_bound = len(request.cases) * (
        max(policy.max_output_bytes, len(request.candidate_artifact_json))
        + max(policy.max_output_bytes, len(request.incumbent_artifact_json))
    )
    if output_work_upper_bound > policy.max_total_output_work_bytes:
        raise PairedRunnerContractError(
            "paired output work exceeds total byte-work bound"
        )
    candidate = json.loads(request.candidate_artifact_json.decode("utf-8"))
    incumbent = json.loads(request.incumbent_artifact_json.decode("utf-8"))
    for label, value in (
        ("candidate artifact", candidate),
        ("incumbent artifact", incumbent),
        (
            "candidate config",
            json.loads(request.candidate_config_json.decode("utf-8")),
        ),
        (
            "incumbent config",
            json.loads(request.incumbent_config_json.decode("utf-8")),
        ),
    ):
        _check_shape(
            value,
            label,
            max_depth=policy.max_json_depth,
            max_nodes=policy.max_json_nodes,
            max_collection_items=policy.max_collection_items,
        )
    _validate_artifact_semantics(
        candidate,
        max_collection_items=policy.max_collection_items,
    )
    _validate_artifact_semantics(
        incumbent,
        max_collection_items=policy.max_collection_items,
    )
    return candidate, incumbent


def _validate_plan_bindings(
    *,
    request: PairedRunnerRequestV1,
    policy: PairedRunnerPolicyV1,
    commitments: PairedRunnerCommitmentsV1,
    candidate: dict[str, Any],
    incumbent: dict[str, Any],
) -> tuple[str, str]:
    plan = request.plan
    expected_pairs = {
        "held_out_pack_commitment": commitments.held_out_pack_commitment,
        "held_out_selection_manifest_digest": (
            commitments.held_out_selection_manifest_digest
        ),
        "held_out_pair_manifest_digest": commitments.held_out_pair_manifest_digest,
        "arm_order_policy_digest": commitments.arm_order_policy_digest,
        "holdout_access_policy_digest": (
            PAIRED_RUNNER_HOLDOUT_ACCESS_POLICY_DIGEST
        ),
        "oracle_artifact_digest": PAIRED_RUNNER_ORACLE_ARTIFACT_DIGEST,
        "oracle_config_digest": PAIRED_RUNNER_ORACLE_CONFIG_DIGEST,
        "resource_policy_digest": policy.resource_policy_digest,
    }
    for name, expected in expected_pairs.items():
        if getattr(plan, name) != expected:
            raise PairedRunnerContractError(f"plan {name} binding mismatch")
    if request.commitment_key_id != plan.commitment_key_id:
        raise PairedRunnerContractError("commitment key id differs from plan")
    candidate_digest = derive_paired_runner_artifact_digest(
        request.candidate_artifact_json
    )
    incumbent_digest = derive_paired_runner_artifact_digest(
        request.incumbent_artifact_json
    )
    if candidate_digest != plan.candidate_artifact_digest:
        raise PairedRunnerContractError("candidate artifact binding mismatch")
    if incumbent_digest != plan.incumbent_artifact_digest:
        raise PairedRunnerContractError("incumbent artifact binding mismatch")
    if derive_paired_runner_config_digest(
        request.candidate_config_json
    ) != plan.candidate_config_digest:
        raise PairedRunnerContractError("candidate config binding mismatch")
    if derive_paired_runner_config_digest(
        request.incumbent_config_json
    ) != plan.incumbent_config_digest:
        raise PairedRunnerContractError("incumbent config binding mismatch")
    candidate_kind = candidate.get("kind")
    incumbent_kind = incumbent.get("kind")
    if type(candidate_kind) is not str or candidate_kind != incumbent_kind:
        raise PairedRunnerContractError("paired artifacts must share one family kind")
    if (
        candidate_kind not in _PAIRED_RUNNER_V1_FAMILIES
        or candidate_kind not in LOW_RISK_FAMILY_KINDS
        or candidate_kind not in supported_executor_kinds()
    ):
        raise PairedRunnerContractError("artifact family has no closed V1 executor")
    if derive_paired_runner_solver_family_digest(
        candidate_kind
    ) != plan.solver_family_digest:
        raise PairedRunnerContractError("solver family binding mismatch")
    return candidate_digest, incumbent_digest


def run_understanding_paired(
    request: PairedRunnerRequestV1 | None = None,
    *,
    policy: PairedRunnerPolicyV1 = PairedRunnerPolicyV1(),
    commitment_key: bytes | None = None,
) -> PairedRunnerReceiptV1 | None:
    """Run one closed paired shadow request, or return before OFF inspection."""

    if type(policy) is not PairedRunnerPolicyV1:
        raise PairedRunnerContractError("policy must be a PairedRunnerPolicyV1")
    if policy.mode is PairedRunnerMode.OFF:
        return None
    if policy.mode is not PairedRunnerMode.SHADOW:
        raise PairedRunnerContractError("unsupported paired-runner mode")
    if type(request) is not PairedRunnerRequestV1:
        raise PairedRunnerContractError(
            "shadow mode requires an exact PairedRunnerRequestV1"
        )
    key = _require_key(commitment_key)

    candidate, incumbent = _validate_policy_shape(request=request, policy=policy)
    prepared = _prepare_cases(
        campaign_id=request.plan.campaign_id,
        cases=request.cases,
        key=key,
    )
    commitments = _commitments_from_prepared(
        campaign_id=request.plan.campaign_id,
        prepared=prepared,
        key=key,
    )
    candidate_digest, incumbent_digest = _validate_plan_bindings(
        request=request,
        policy=policy,
        commitments=commitments,
        candidate=candidate,
        incumbent=incumbent,
    )

    observations: list[PairedHeldOutObservationV1] = []
    trace: list[dict[str, Any]] = []
    for index, case in enumerate(prepared):
        candidate_first = index % 2 == 0
        call_order = (
            ("candidate", request.candidate_artifact_json, candidate_digest),
            ("incumbent", request.incumbent_artifact_json, incumbent_digest),
        )
        if not candidate_first:
            call_order = tuple(reversed(call_order))
        terminals: dict[str, _ArmTerminalV1] = {}
        for arm, artifact_json, artifact_digest in call_order:
            terminals[arm] = _execute_arm(
                artifact_json=artifact_json,
                input_json=case.input_json,
                artifact_digest=artifact_digest,
                case_commitment=case.case_commitment,
                arm=arm,
                max_output_bytes=policy.max_output_bytes,
            )
        candidate_terminal = terminals["candidate"]
        incumbent_terminal = terminals["incumbent"]
        input_mutated = not (
            candidate_terminal.input_final_state_matches
            and incumbent_terminal.input_final_state_matches
        )
        artifact_mutated = not (
            candidate_terminal.artifact_final_state_matches
            and incumbent_terminal.artifact_final_state_matches
        )
        if input_mutated:
            integrity = PairIntegrityV1.INPUT_MUTATED
        elif artifact_mutated:
            integrity = PairIntegrityV1.COMMITMENT_MISMATCH
        else:
            integrity = PairIntegrityV1.COMPLETE

        if integrity is PairIntegrityV1.COMPLETE:
            candidate_outcome = (
                ArmOutcomeV1.ERROR
                if candidate_terminal.error
                else ArmOutcomeV1.PASS
                if candidate_terminal.output_json == case.expected_output_json
                else ArmOutcomeV1.FAIL
            )
            incumbent_outcome = (
                ArmOutcomeV1.ERROR
                if incumbent_terminal.error
                else ArmOutcomeV1.PASS
                if incumbent_terminal.output_json == case.expected_output_json
                else ArmOutcomeV1.FAIL
            )
        else:
            candidate_outcome = ArmOutcomeV1.NOT_SCORED
            incumbent_outcome = ArmOutcomeV1.NOT_SCORED

        first_arm = "candidate" if candidate_first else "incumbent"
        pair_evidence_commitment = _hmac_commitment(
            key,
            {
                "domain": "wd.understanding.paired_runner.pair_evidence.v1",
                "plan_digest": request.plan.plan_digest,
                "case_commitment": case.case_commitment,
                "declared_unit_commitment": case.declared_unit_commitment,
                "expected_output_commitment": case.expected_output_commitment,
                "logical_ordinal": index,
                "first_arm": first_arm,
                "candidate_terminal_commitment": (
                    candidate_terminal.output_commitment
                ),
                "incumbent_terminal_commitment": (
                    incumbent_terminal.output_commitment
                ),
                "candidate_outcome": candidate_outcome.value,
                "incumbent_outcome": incumbent_outcome.value,
                "integrity": integrity.value,
            },
        )
        observations.append(
            PairedHeldOutObservationV1(
                case_commitment=case.case_commitment,
                declared_unit_commitment=case.declared_unit_commitment,
                pair_evidence_commitment=pair_evidence_commitment,
                candidate_outcome=candidate_outcome,
                incumbent_outcome=incumbent_outcome,
                integrity=integrity,
            )
        )
        trace.append(
            {
                "case_commitment": case.case_commitment,
                "logical_ordinal": index,
                "first_arm": first_arm,
                "candidate_terminal": (
                    "error" if candidate_terminal.error else "returned"
                ),
                "incumbent_terminal": (
                    "error" if incumbent_terminal.error else "returned"
                ),
                "integrity": integrity.value,
            }
        )

    lift_request = PairedLiftRequestV1(
        plan=request.plan,
        observations=tuple(observations),
        leakage_audit_digest=request.leakage_audit_digest,
        leakage_check_passed_asserted=request.leakage_check_passed_asserted,
    )
    lift_receipt = evaluate_paired_lift(
        lift_request,
        policy=PairedLiftPolicyV1(
            mode=PairedLiftMode.SHADOW,
            min_cases=policy.min_cases,
            max_cases=policy.max_cases,
        ),
    )
    if lift_receipt is None:  # defensive: the explicit SHADOW call cannot do this
        raise PairedRunnerContractError("paired evaluator returned no receipt")

    runner_request_digest = sha256_digest(
        {
            "domain": "wd.understanding.paired_runner.request.digest.v1",
            "plan_digest": request.plan.plan_digest,
            "runner_policy_digest": policy.policy_digest,
            "candidate_artifact_digest": candidate_digest,
            "candidate_config_digest": request.plan.candidate_config_digest,
            "incumbent_artifact_digest": incumbent_digest,
            "incumbent_config_digest": request.plan.incumbent_config_digest,
            "commitments": commitments.to_mapping(),
            "commitment_key_id": request.commitment_key_id,
            "leakage_audit_digest": request.leakage_audit_digest,
            "leakage_check_passed_asserted": (
                request.leakage_check_passed_asserted
            ),
        }
    )
    values: dict[str, Any] = {
        "plan_digest": request.plan.plan_digest,
        "runner_policy_digest": policy.policy_digest,
        "runner_request_digest": runner_request_digest,
        "arm_order_policy_digest": commitments.arm_order_policy_digest,
        "execution_trace_digest": sha256_digest(
            {
                "domain": "wd.understanding.paired_runner.execution_trace.v1",
                "entries": trace,
            }
        ),
        "execution_evidence_root_digest": (
            lift_receipt.pair_evidence_root_digest
        ),
        "case_count": len(prepared),
        "complete_pair_count": lift_receipt.case_count_complete,
        "post_return_state_mismatch_pair_count": (
            len(prepared) - lift_receipt.case_count_complete
        ),
        "candidate_first_count": (len(prepared) + 1) // 2,
        "incumbent_first_count": len(prepared) // 2,
        "max_artifact_bytes": policy.max_artifact_bytes,
        "max_config_bytes": policy.max_config_bytes,
        "max_case_component_bytes": policy.max_case_component_bytes,
        "max_total_case_bytes": policy.max_total_case_bytes,
        "max_output_bytes": policy.max_output_bytes,
        "max_total_artifact_decode_work_bytes": (
            policy.max_total_artifact_decode_work_bytes
        ),
        "max_total_output_work_bytes": policy.max_total_output_work_bytes,
        "max_json_depth": policy.max_json_depth,
        "max_json_nodes": policy.max_json_nodes,
        "max_collection_items": policy.max_collection_items,
        "paired_lift_receipt": lift_receipt,
    }
    provisional = PairedRunnerReceiptV1(
        **values,
        receipt_digest=sha256_digest(
            {
                "domain": "wd.understanding.paired_runner.receipt.digest.v1",
                **_runner_receipt_core_from_values(values),
            }
        ),
    )
    return provisional


def _runner_receipt_core_from_values(values: dict[str, Any]) -> dict[str, Any]:
    core = {
        "schema_version": PAIRED_RUNNER_RECEIPT_SCHEMA,
        **{
            name: values[name]
            for name in (
                "plan_digest",
                "runner_policy_digest",
                "runner_request_digest",
                "arm_order_policy_digest",
                "execution_trace_digest",
                "execution_evidence_root_digest",
                "case_count",
                "complete_pair_count",
                "post_return_state_mismatch_pair_count",
                "candidate_first_count",
                "incumbent_first_count",
                "max_artifact_bytes",
                "max_config_bytes",
                "max_case_component_bytes",
                "max_total_case_bytes",
                "max_output_bytes",
                "max_total_artifact_decode_work_bytes",
                "max_total_output_work_bytes",
                "max_json_depth",
                "max_json_nodes",
                "max_collection_items",
            )
        },
    }
    core.update({name: True for name in _TRUE_RECEIPT_FIELDS})
    core.update({name: False for name in _FALSE_RECEIPT_FIELDS})
    core["paired_lift_receipt"] = values["paired_lift_receipt"].to_mapping()
    return core


__all__ = [
    "PAIRED_RUNNER_COMMITMENT_SCHEME",
    "PAIRED_RUNNER_HOLDOUT_ACCESS_POLICY_DIGEST",
    "PAIRED_RUNNER_ORACLE_ARTIFACT_DIGEST",
    "PAIRED_RUNNER_ORACLE_CONFIG_DIGEST",
    "PAIRED_RUNNER_ORDER_ALGORITHM",
    "PAIRED_RUNNER_RECEIPT_SCHEMA",
    "PairedRunnerCaseV1",
    "PairedRunnerCommitmentsV1",
    "PairedRunnerContractError",
    "PairedRunnerMode",
    "PairedRunnerPolicyV1",
    "PairedRunnerReceiptV1",
    "PairedRunnerRequestV1",
    "derive_paired_runner_artifact_digest",
    "derive_paired_runner_commitments",
    "derive_paired_runner_config_digest",
    "derive_paired_runner_solver_family_digest",
    "run_understanding_paired",
]
