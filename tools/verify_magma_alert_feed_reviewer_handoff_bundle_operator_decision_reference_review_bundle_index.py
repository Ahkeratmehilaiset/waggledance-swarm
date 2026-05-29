# SPDX-License-Identifier: BUSL-1.1
"""Verify a local MAGMA operator decision-reference review bundle index."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index import (  # noqa: E402
    BUNDLE_INDEX_VERSION,
    DecisionReferenceReviewBundleIndexError,
    build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index,
)
from tools.package_magma_alert_feed_release_evidence import (  # noqa: E402
    FORBIDDEN_OUTPUT_MARKERS,
)


VERIFICATION_VERSION = "magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification.v1"

_REQUIRED_ARTIFACTS = (
    "operator_decision_reference_validation",
    "operator_decision_reference_review_summary",
)
_AUTHORITY_FALSE_FIELDS = (
    "approval_granted",
    "release_decision_made",
    "automatic_release_decision",
    "direct_bridge_write_performed",
    "transport_added",
    "external_fetch_performed",
    "runtime_controls_added",
    "artifact_payloads_included",
    "local_paths_recorded",
)
_REFERENCE_FALSE_FIELDS = (
    "decision_reference_is_approval",
    "decision_reference_is_release_decision",
)


class DecisionReferenceReviewBundleVerificationError(ValueError):
    """Raised when verifier inputs cannot be safely read."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--review-bundle-index-json",
        "--bundle-index-json",
        dest="review_bundle_index_json",
        required=True,
        type=Path,
    )
    parser.add_argument("--decision-validation-json", required=True, type=Path)
    parser.add_argument("--review-summary-json", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        _, review_bundle_index = _load_json_artifact(
            args.review_bundle_index_json,
            "operator_decision_reference_review_bundle_index",
        )
        validation_bytes, validation_report = _load_json_artifact(
            args.decision_validation_json,
            "operator_decision_reference_validation",
        )
        summary_bytes, review_summary = _load_json_artifact(
            args.review_summary_json,
            "operator_decision_reference_review_summary",
        )
        report = verify_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index(
            review_bundle_index=review_bundle_index,
            decision_validation_report=validation_report,
            review_summary=review_summary,
            decision_validation_bytes=validation_bytes,
            review_summary_bytes=summary_bytes,
        )
    except DecisionReferenceReviewBundleVerificationError as exc:
        report = _failure_report(exc.code)
    except ValueError:
        report = _failure_report(
            "operator_decision_reference_review_bundle_verification_invalid"
        )

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            "MAGMA operator decision-reference review bundle verification "
            "FAILED: "
            + ", ".join(report["blockers"]),
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def verify_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index(
    *,
    review_bundle_index: Mapping[str, Any],
    decision_validation_report: Mapping[str, Any],
    review_summary: Mapping[str, Any],
    decision_validation_bytes: bytes,
    review_summary_bytes: bytes,
) -> dict[str, Any]:
    """Recompute artifact checks for a decision-reference review bundle index."""

    _assert_mapping(
        "operator_decision_reference_review_bundle_index",
        review_bundle_index,
    )
    _assert_no_forbidden_input(
        "operator_decision_reference_review_bundle_index",
        review_bundle_index,
    )
    artifact_inputs = {
        "operator_decision_reference_validation": (
            decision_validation_report,
            decision_validation_bytes,
        ),
        "operator_decision_reference_review_summary": (
            review_summary,
            review_summary_bytes,
        ),
    }
    for artifact_id, (artifact, _) in artifact_inputs.items():
        _assert_mapping(artifact_id, artifact)
        _assert_no_forbidden_input(artifact_id, artifact)

    blockers: list[str] = []
    warnings: list[str] = []
    rebuilt_index = _rebuilt_source_index(
        decision_validation_report=decision_validation_report,
        review_summary=review_summary,
        decision_validation_bytes=decision_validation_bytes,
        review_summary_bytes=review_summary_bytes,
        blockers=blockers,
    )
    rebuilt_index_check = "failed"

    if review_bundle_index.get("bundle_index_version") != BUNDLE_INDEX_VERSION:
        blockers.append("bundle_index_version_mismatch")
    if review_bundle_index.get("ok") is not True:
        blockers.append("bundle_index_not_ok")
    if (
        not isinstance(review_bundle_index.get("artifact_count"), int)
        or review_bundle_index.get("artifact_count") != len(_REQUIRED_ARTIFACTS)
    ):
        blockers.append("artifact_count_mismatch")

    _collect_boundary_blockers(review_bundle_index, blockers)
    _collect_consistency_blockers(review_bundle_index, blockers)
    source_reference = _operator_decision_reference(
        decision_validation_report,
        review_summary,
        blockers,
    )
    index_reference = _index_operator_decision_reference(
        review_bundle_index,
        blockers,
    )
    if source_reference != index_reference:
        blockers.append("operator_decision_reference_mismatch")

    identity = _verified_identity(
        (review_bundle_index, decision_validation_report, review_summary),
        blockers,
    )
    if rebuilt_index is not None:
        rebuilt_index_check = _compare_rebuilt_index(
            observed=review_bundle_index,
            rebuilt=rebuilt_index,
            blockers=blockers,
        )
        rebuilt_identity = {
            "release_ref": rebuilt_index.get("release_ref"),
            "commit_sha": rebuilt_index.get("commit_sha"),
            "ci_run_ref": rebuilt_index.get("ci_run_ref"),
        }
        for field, expected in rebuilt_identity.items():
            if review_bundle_index.get(field) != expected:
                blockers.append(f"rebuilt_identity_{field}_mismatch")

    records = _artifact_records(review_bundle_index, blockers)
    digest_checks: dict[str, str] = {}
    size_checks: dict[str, str] = {}
    schema_version_checks: dict[str, str] = {}
    for artifact_id in _REQUIRED_ARTIFACTS:
        artifact, raw = artifact_inputs[artifact_id]
        record = records.get(artifact_id)
        if record is None:
            digest_checks[artifact_id] = "missing_index_record"
            size_checks[artifact_id] = "missing_index_record"
            schema_version_checks[artifact_id] = "missing_index_record"
            continue
        digest_checks[artifact_id] = _check_equal(
            record.get("sha256"),
            _sha256_hex(raw),
            f"digest_mismatch:{artifact_id}",
            blockers,
        )
        size_checks[artifact_id] = _check_equal(
            record.get("size_bytes"),
            len(raw),
            f"size_mismatch:{artifact_id}",
            blockers,
        )
        schema_version_checks[artifact_id] = _check_equal(
            record.get("json_schema_version"),
            _schema_version(artifact),
            f"schema_version_mismatch:{artifact_id}",
            blockers,
        )
        if record.get("payload_included") is not False:
            blockers.append(f"payload_included_not_false:{artifact_id}")
        if record.get("local_path_recorded") is not False:
            blockers.append(f"local_path_recorded_not_false:{artifact_id}")

    report = {
        "ok": not blockers,
        "verification_version": VERIFICATION_VERSION,
        "bundle_index_version": review_bundle_index.get("bundle_index_version"),
        "release_ref": identity["release_ref"],
        "commit_sha": identity["commit_sha"],
        "ci_run_ref": identity["ci_run_ref"],
        "operator_decision_reference": {
            "decision_reference": index_reference["decision_reference"],
            "expected_decision_reference": index_reference[
                "expected_decision_reference"
            ],
            "decision_reference_verified": not blockers,
            "decision_reference_is_approval": False,
            "decision_reference_is_release_decision": False,
            "decision_must_be_recorded_separately": True,
            "review_context_only": True,
        },
        "artifact_count_checked": len(_REQUIRED_ARTIFACTS),
        "digest_checks": digest_checks,
        "size_checks": size_checks,
        "schema_version_checks": schema_version_checks,
        "source_contract_check": "match" if rebuilt_index is not None else "failed",
        "rebuilt_index_check": rebuilt_index_check,
        "manual_review_required": True,
        "approval_granted": False,
        "release_decision_made": False,
        "automatic_release_decision": False,
        "direct_bridge_write_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "runtime_controls_added": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
    }
    _assert_no_forbidden_output(json.dumps(report, allow_nan=False, sort_keys=True))
    return report


def _rebuilt_source_index(
    *,
    decision_validation_report: Mapping[str, Any],
    review_summary: Mapping[str, Any],
    decision_validation_bytes: bytes,
    review_summary_bytes: bytes,
    blockers: list[str],
) -> Mapping[str, Any] | None:
    try:
        return build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index(
            decision_validation_report=decision_validation_report,
            review_summary=review_summary,
            decision_validation_bytes=decision_validation_bytes,
            review_summary_bytes=review_summary_bytes,
        )
    except DecisionReferenceReviewBundleIndexError as exc:
        blockers.append(f"source_contract_failed:{exc.code}")
    except ValueError:
        blockers.append("source_contract_failed:invalid_source_artifact")
    return None


def _compare_rebuilt_index(
    *,
    observed: Mapping[str, Any],
    rebuilt: Mapping[str, Any],
    blockers: list[str],
) -> str:
    if _deterministic_index(observed) == _deterministic_index(rebuilt):
        return "match"
    blockers.append("rebuilt_index_mismatch")
    return "mismatch"


def _deterministic_index(index: Mapping[str, Any]) -> dict[str, Any]:
    normalized = json.loads(json.dumps(index, allow_nan=False, sort_keys=True))
    if isinstance(normalized, dict):
        normalized.pop("created_at_utc", None)
        return normalized
    return {}


def _verified_identity(
    artifacts: Sequence[Mapping[str, Any]],
    blockers: list[str],
) -> dict[str, str]:
    return {
        field: _required_matching_identity(artifacts, field, blockers)
        for field in ("release_ref", "commit_sha", "ci_run_ref")
    }


def _required_matching_identity(
    artifacts: Sequence[Mapping[str, Any]],
    field: str,
    blockers: list[str],
) -> str:
    values: set[str] = set()
    missing = False
    for artifact in artifacts:
        value = artifact.get(field)
        if not isinstance(value, str) or not value:
            missing = True
            continue
        values.add(value)
    if missing:
        blockers.append(f"artifact_identity_{field}_missing")
    if len(values) != 1:
        if values:
            blockers.append(f"artifact_identity_{field}_mismatch")
        return "invalid_ref"
    return next(iter(values)) if not missing else "invalid_ref"


def _operator_decision_reference(
    validation_report: Mapping[str, Any],
    review_summary: Mapping[str, Any],
    blockers: list[str],
) -> dict[str, str]:
    validation_reference = _mapping(
        validation_report.get("operator_decision_reference")
    )
    summary_reference = _mapping(
        review_summary.get("operator_decision_reference_review")
    )
    decision_reference = validation_reference.get("decision_reference")
    expected_decision_reference = validation_reference.get(
        "expected_decision_reference"
    )
    if summary_reference.get("decision_reference") != decision_reference:
        blockers.append("source_decision_reference_mismatch")
    if summary_reference.get("expected_decision_reference") != expected_decision_reference:
        blockers.append("source_expected_decision_reference_mismatch")
    return {
        "decision_reference": decision_reference
        if isinstance(decision_reference, str)
        else "invalid_ref",
        "expected_decision_reference": expected_decision_reference
        if isinstance(expected_decision_reference, str)
        else "invalid_ref",
    }


def _index_operator_decision_reference(
    review_bundle_index: Mapping[str, Any],
    blockers: list[str],
) -> dict[str, str]:
    reference = _mapping(review_bundle_index.get("operator_decision_reference"))
    decision_reference = reference.get("decision_reference")
    expected_decision_reference = reference.get("expected_decision_reference")
    if not isinstance(decision_reference, str) or not decision_reference:
        blockers.append("operator_decision_reference_missing")
        decision_reference = "invalid_ref"
    if (
        not isinstance(expected_decision_reference, str)
        or not expected_decision_reference
    ):
        blockers.append("operator_decision_reference_expected_missing")
        expected_decision_reference = "invalid_ref"
    if reference.get("decision_reference_validated") is not True:
        blockers.append("operator_decision_reference_not_validated")
    if reference.get("decision_reference_matches_expected") is not True:
        blockers.append("operator_decision_reference_not_matching_expected")
    if reference.get("decision_must_be_recorded_separately") is not True:
        blockers.append("operator_decision_reference_record_separately_not_true")
    if reference.get("review_context_only") is not True:
        blockers.append("operator_decision_reference_review_context_only_not_true")
    for field in _REFERENCE_FALSE_FIELDS:
        if reference.get(field) is not False:
            blockers.append(f"operator_decision_reference_{field}_not_false")
    for field in _AUTHORITY_FALSE_FIELDS:
        if field in reference and reference.get(field) is not False:
            blockers.append(f"operator_decision_reference_{field}_not_false")
    return {
        "decision_reference": decision_reference,
        "expected_decision_reference": expected_decision_reference,
    }


def _load_json_artifact(path: Path, artifact_id: str) -> tuple[bytes, Mapping[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise DecisionReferenceReviewBundleVerificationError(
            f"{artifact_id}_unreadable"
        ) from exc
    parsed = _parse_json_bytes(raw, artifact_id)
    if not isinstance(parsed, Mapping):
        raise DecisionReferenceReviewBundleVerificationError(
            f"{artifact_id}_not_mapping"
        )
    return raw, parsed


def _parse_json_bytes(raw: bytes, artifact_id: str) -> Any:
    def reject_constant(_value: str) -> None:
        raise ValueError("non_finite_json_constant")

    try:
        return json.loads(
            raw.decode("utf-8"),
            parse_constant=reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise DecisionReferenceReviewBundleVerificationError(
            f"{artifact_id}_decode_error"
        ) from exc
    except json.JSONDecodeError as exc:
        raise DecisionReferenceReviewBundleVerificationError(
            f"{artifact_id}_json_error"
        ) from exc
    except ValueError as exc:
        raise DecisionReferenceReviewBundleVerificationError(
            f"{artifact_id}_json_error"
        ) from exc


def _failure_report(reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "verification_version": VERIFICATION_VERSION,
        "manual_review_required": True,
        "approval_granted": False,
        "release_decision_made": False,
        "automatic_release_decision": False,
        "direct_bridge_write_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "runtime_controls_added": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": [
            "operator_decision_reference_review_bundle_verification_failed:"
            f"{reason}"
        ],
        "warnings": [],
    }


def _artifact_records(
    review_bundle_index: Mapping[str, Any],
    blockers: list[str],
) -> dict[str, Mapping[str, Any]]:
    raw_records = review_bundle_index.get("artifacts")
    if not isinstance(raw_records, list):
        blockers.append("artifact_records_missing")
        return {}
    if len(raw_records) != len(_REQUIRED_ARTIFACTS):
        blockers.append("artifact_record_count_mismatch")
    records: dict[str, Mapping[str, Any]] = {}
    for raw_record in raw_records:
        if not isinstance(raw_record, Mapping):
            blockers.append("artifact_record_not_mapping")
            continue
        artifact_id = raw_record.get("artifact_id")
        if not isinstance(artifact_id, str):
            blockers.append("artifact_record_id_missing")
            continue
        if artifact_id not in _REQUIRED_ARTIFACTS:
            blockers.append(f"artifact_record_unknown:{artifact_id}")
            continue
        if artifact_id in records:
            blockers.append(f"artifact_record_duplicate:{artifact_id}")
            continue
        records[artifact_id] = raw_record
    for artifact_id in _REQUIRED_ARTIFACTS:
        if artifact_id not in records:
            blockers.append(f"artifact_record_missing:{artifact_id}")
    return records


def _collect_boundary_blockers(
    review_bundle_index: Mapping[str, Any],
    blockers: list[str],
) -> None:
    operator_boundary = _mapping(review_bundle_index.get("operator_boundary"))
    if review_bundle_index.get("manual_review_required") is not True:
        blockers.append("manual_review_required_not_true")
    if operator_boundary.get("manual_review_required") is not True:
        blockers.append("operator_boundary_manual_review_required_not_true")
    for field in _AUTHORITY_FALSE_FIELDS:
        if review_bundle_index.get(field) is not False:
            blockers.append(f"{field}_not_false")
        if operator_boundary.get(field) is not False:
            blockers.append(f"operator_boundary_{field}_not_false")


def _collect_consistency_blockers(
    review_bundle_index: Mapping[str, Any],
    blockers: list[str],
) -> None:
    consistency = _mapping(review_bundle_index.get("consistency"))
    if consistency.get("required_artifacts_present") != list(_REQUIRED_ARTIFACTS):
        blockers.append("consistency_required_artifacts_mismatch")
    for field in (
        "release_ref_match",
        "commit_sha_match",
        "ci_run_ref_match",
        "decision_reference_match",
        "all_artifact_digests_recorded",
    ):
        if consistency.get(field) is not True:
            blockers.append(f"consistency_{field}_not_true")
    for field in (
        "artifact_payloads_included",
        "local_paths_recorded",
    ):
        if consistency.get(field) is not False:
            blockers.append(f"consistency_{field}_not_false")


def _check_equal(
    observed: Any,
    expected: Any,
    blocker: str,
    blockers: list[str],
) -> str:
    if observed == expected:
        return "match"
    blockers.append(blocker)
    return "mismatch"


def _schema_version(artifact: Mapping[str, Any]) -> str:
    for field in ("validation_version", "summary_version"):
        value = artifact.get(field)
        if isinstance(value, str):
            return value
    return "invalid_schema"


def _assert_mapping(artifact_id: str, value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping):
        raise DecisionReferenceReviewBundleVerificationError(
            f"{artifact_id}_not_mapping"
        )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sha256_hex(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _assert_no_forbidden_input(artifact_id: str, value: Mapping[str, Any]) -> None:
    try:
        serialized = json.dumps(value, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise DecisionReferenceReviewBundleVerificationError(
            f"{artifact_id}_non_finite_json_value"
        ) from exc
    if _forbidden_output_markers(serialized):
        raise DecisionReferenceReviewBundleVerificationError(
            f"{artifact_id}_forbidden_marker"
        )


def _forbidden_output_markers(text: str) -> list[str]:
    lower_text = text.lower()
    return sorted(
        marker
        for marker in FORBIDDEN_OUTPUT_MARKERS
        if marker.lower() in lower_text
    )


def _assert_no_forbidden_output(text: str) -> None:
    if _forbidden_output_markers(text):
        raise ValueError(
            "sanitized operator decision-reference review bundle "
            "verification contains forbidden markers"
        )


if __name__ == "__main__":
    raise SystemExit(main())
