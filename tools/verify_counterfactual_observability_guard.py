# SPDX-License-Identifier: BUSL-1.1
"""Fail-closed guard for V12 A3 counterfactual observability artifacts."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.autonomy_growth.counterfactual_replay import (  # noqa: E402
    A3_LABEL_RUNTIME_MEASURED,
    COUNTERFACTUAL_DELTA_SCHEMA,
    COUNTERFACTUAL_OBSERVABILITY_STATUS_SCHEMA,
    COUNTERFACTUAL_OBSERVABILITY_STATES,
    DEFAULT_A3_MIN_SAMPLES,
    summarize_counterfactual_observability,
)
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402


GUARD_SCHEMA_VERSION = "wd.counterfactual_observability_guard.v1"
PROMOTION_SUMMARY_SCHEMA = "magma.counterfactual_promotion_summary.v0"

_DIGEST_PREFIX = "sha256:"
_DIGEST_HEX_LENGTH = 64
_RAW_FIELD_NAMES = frozenset({
    "candidate_hash",
    "incumbent_hash",
    "per_arm",
    "divergences",
    "candidate_output",
    "incumbent_output",
    "inputs",
    "output",
})
_AUTHORITY_FALSE_FIELDS = (
    "controls_present",
    "runtime_authority_granted",
    "external_writes_applied",
    "payload_fields_exported",
)


class CounterfactualObservabilityGuardError(ValueError):
    """Raised when a counterfactual observability artifact is unsafe."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-json", required=True, type=Path)
    parser.add_argument(
        "--min-samples",
        type=int,
        default=DEFAULT_A3_MIN_SAMPLES,
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        artifact = _load_json(args.artifact_json)
        report = verify_counterfactual_observability_artifact(
            artifact,
            min_samples=args.min_samples,
        )
    except CounterfactualObservabilityGuardError as exc:
        report = _failure_report([str(exc)])

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            "counterfactual observability guard FAILED: "
            + ", ".join(report["blockers"]),
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def verify_counterfactual_observability_artifact(
    artifact: Any,
    *,
    min_samples: int = DEFAULT_A3_MIN_SAMPLES,
) -> dict[str, Any]:
    """Verify one counterfactual artifact and emit a sanitized report.

    The report intentionally omits source paths, raw per-arm rows,
    divergences, solver hashes, and digest strings.
    """

    blockers: list[str] = []
    if isinstance(min_samples, bool) or not isinstance(min_samples, int):
        blockers.append("min_samples_must_be_int")
        min_samples = DEFAULT_A3_MIN_SAMPLES
    elif min_samples < 1:
        blockers.append("min_samples_must_be_positive")
        min_samples = DEFAULT_A3_MIN_SAMPLES

    if not isinstance(artifact, Mapping):
        return _failure_report([*blockers, "artifact_must_be_mapping"])

    non_finite_path = _find_non_finite_path(artifact)
    if non_finite_path:
        blockers.append("non_finite_value:" + non_finite_path)

    schema_version = artifact.get("schema_version")
    if schema_version == COUNTERFACTUAL_DELTA_SCHEMA:
        blockers.extend(_raw_delta_blockers(artifact))
        summary = summarize_counterfactual_observability(
            artifact,
            min_samples=min_samples,
        )
    elif schema_version == COUNTERFACTUAL_OBSERVABILITY_STATUS_SCHEMA:
        summary = _status_summary_from_artifact(artifact, blockers)
    elif schema_version == PROMOTION_SUMMARY_SCHEMA:
        blockers.extend(_promotion_summary_blockers(artifact))
        summary = summarize_counterfactual_observability(
            artifact,
            min_samples=min_samples,
        )
    else:
        summary = summarize_counterfactual_observability(None)
        blockers.append("unsupported_schema_version")

    blockers.extend(_summary_blockers(summary, min_samples=min_samples))
    ok = not blockers
    runtime_measured_claim_safe = (
        ok
        and summary.get("status") == "runtime_measured"
        and summary.get("a3_label") == A3_LABEL_RUNTIME_MEASURED
        and summary.get("sample_count", 0) >= min_samples
        and summary.get("same_sample_set") is True
        and summary.get("deterministic") is True
        and summary.get("delta_digest_present") is True
    )
    return {
        "schema_version": GUARD_SCHEMA_VERSION,
        "ok": ok,
        "guard_kind": "counterfactual_observability_guard",
        "blockers": blockers,
        "observability_summary": summary,
        "runtime_measured_claim_safe": runtime_measured_claim_safe,
        "literal_future_claim_safe": False,
        "source_path_recorded": False,
        "raw_fields_exported": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
        "bridge_event_written": False,
    }


def _load_json(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CounterfactualObservabilityGuardError(
            "artifact_json_unreadable"
        ) from exc
    try:
        return json.loads(text, parse_constant=_reject_json_constant)
    except ValueError as exc:
        raise CounterfactualObservabilityGuardError(
            "artifact_json_invalid_or_non_finite"
        ) from exc


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def _raw_delta_blockers(artifact: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    digest = artifact.get("canonical_digest")
    if not _is_sha256_digest(digest):
        blockers.append("canonical_digest_missing_or_malformed")
        return blockers
    core = {key: value for key, value in artifact.items() if key != "canonical_digest"}
    try:
        expected = sha256_digest(core)
    except (TypeError, ValueError):
        blockers.append("canonical_digest_recompute_failed")
        return blockers
    if digest != expected:
        blockers.append("canonical_digest_mismatch")
    return blockers


def _promotion_summary_blockers(artifact: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    if artifact.get("status") == "computed" and artifact.get("delta_digest"):
        if not _is_sha256_digest(artifact.get("delta_digest")):
            blockers.append("delta_digest_malformed")
    if _contains_raw_field(artifact):
        blockers.append("promotion_summary_contains_raw_fields")
    return blockers


def _status_summary_from_artifact(
    artifact: Mapping[str, Any],
    blockers: list[str],
) -> dict[str, Any]:
    if _contains_raw_field(artifact):
        blockers.append("status_summary_contains_raw_fields")
    status = artifact.get("status")
    if status not in COUNTERFACTUAL_OBSERVABILITY_STATES:
        blockers.append("status_summary_unknown_status")
        status = "insufficient"
    return {
        "schema_version": COUNTERFACTUAL_OBSERVABILITY_STATUS_SCHEMA,
        "source_available": artifact.get("source_available") is True,
        "compute_status": str(artifact.get("compute_status") or "unknown"),
        "status": status,
        "a3_label": (
            artifact.get("a3_label")
            if isinstance(artifact.get("a3_label"), str)
            else "INSUFFICIENT"
        ),
        "sample_count": _strict_nonnegative_int(
            artifact,
            "sample_count",
            blockers,
        ),
        "divergence_count": _strict_nonnegative_int(
            artifact,
            "divergence_count",
            blockers,
        ),
        "same_sample_set": artifact.get("same_sample_set") is True,
        "deterministic": artifact.get("deterministic") is True,
        "no_delta": artifact.get("no_delta") is True,
        "delta_digest_present": artifact.get("delta_digest_present") is True,
        "controls_present": artifact.get("controls_present") is True,
        "runtime_authority_granted": (
            artifact.get("runtime_authority_granted") is True
        ),
        "external_writes_applied": artifact.get("external_writes_applied") is True,
        "payload_fields_exported": artifact.get("payload_fields_exported") is True,
    }


def _summary_blockers(
    summary: Mapping[str, Any],
    *,
    min_samples: int,
) -> list[str]:
    blockers: list[str] = []
    if summary.get("schema_version") != COUNTERFACTUAL_OBSERVABILITY_STATUS_SCHEMA:
        blockers.append("summary_schema_mismatch")
    if summary.get("status") not in COUNTERFACTUAL_OBSERVABILITY_STATES:
        blockers.append("summary_status_unknown")
    for field in _AUTHORITY_FALSE_FIELDS:
        if summary.get(field) is not False:
            blockers.append(field + "_must_be_false")
    if summary.get("status") == "runtime_measured":
        if summary.get("sample_count", 0) < min_samples:
            blockers.append("runtime_measured_sample_floor_not_met")
        if summary.get("same_sample_set") is not True:
            blockers.append("runtime_measured_same_sample_set_not_proven")
        if summary.get("deterministic") is not True:
            blockers.append("runtime_measured_determinism_not_proven")
        if summary.get("delta_digest_present") is not True:
            blockers.append("runtime_measured_delta_digest_missing")
    return blockers


def _strict_nonnegative_int(
    artifact: Mapping[str, Any],
    field: str,
    blockers: list[str],
) -> int:
    value = artifact.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        blockers.append(field + "_must_be_nonnegative_int")
        return 0
    return value


def _contains_raw_field(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in _RAW_FIELD_NAMES:
                return True
            if _contains_raw_field(item):
                return True
    elif isinstance(value, list):
        return any(_contains_raw_field(item) for item in value)
    return False


def _find_non_finite_path(value: Any, path: str = "$") -> str:
    if isinstance(value, float) and not math.isfinite(value):
        return path
    if isinstance(value, Mapping):
        for key, item in value.items():
            found = _find_non_finite_path(item, f"{path}.{key}")
            if found:
                return found
    if isinstance(value, list):
        for index, item in enumerate(value):
            found = _find_non_finite_path(item, f"{path}[{index}]")
            if found:
                return found
    return ""


def _is_sha256_digest(value: Any) -> bool:
    if not isinstance(value, str) or not value.startswith(_DIGEST_PREFIX):
        return False
    digest = value.removeprefix(_DIGEST_PREFIX)
    return (
        len(digest) == _DIGEST_HEX_LENGTH
        and all(char in "0123456789abcdef" for char in digest)
    )


def _failure_report(blockers: Sequence[str]) -> dict[str, Any]:
    return {
        "schema_version": GUARD_SCHEMA_VERSION,
        "ok": False,
        "guard_kind": "counterfactual_observability_guard",
        "blockers": list(blockers),
        "observability_summary": summarize_counterfactual_observability(None),
        "runtime_measured_claim_safe": False,
        "literal_future_claim_safe": False,
        "source_path_recorded": False,
        "raw_fields_exported": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
        "bridge_event_written": False,
    }


if __name__ == "__main__":
    raise SystemExit(main())
