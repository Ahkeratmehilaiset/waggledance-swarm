# SPDX-License-Identifier: BUSL-1.1
"""Build a local MAGMA operator decision-reference review bundle index."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_magma_alert_feed_reviewer_bridge_event_template import (  # noqa: E402
    TEMPLATE_VERSION,
)
from tools.build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_summary import (  # noqa: E402
    SUMMARY_VERSION as REVIEW_SUMMARY_VERSION,
)
from tools.build_magma_alert_feed_reviewer_handoff_bundle_verification_summary import (  # noqa: E402
    SUMMARY_VERSION as VERIFICATION_SUMMARY_VERSION,
)
from tools.package_magma_alert_feed_release_evidence import (  # noqa: E402
    FORBIDDEN_OUTPUT_MARKERS,
)
from tools.validate_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference import (  # noqa: E402
    VALIDATION_VERSION,
)


BUNDLE_INDEX_VERSION = "magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index.v1"

_ARTIFACT_ORDER = (
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
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$")


class DecisionReferenceReviewBundleIndexError(ValueError):
    """Raised when a review bundle index input is unsafe or inconsistent."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decision-validation-json", required=True, type=Path)
    parser.add_argument("--review-summary-json", required=True, type=Path)
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override such as 2026-05-29T08:00:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        validation_bytes, validation_report = _load_json_artifact(
            args.decision_validation_json,
            "operator_decision_reference_validation",
        )
        summary_bytes, review_summary = _load_json_artifact(
            args.review_summary_json,
            "operator_decision_reference_review_summary",
        )
        report = build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index(
            decision_validation_report=validation_report,
            review_summary=review_summary,
            decision_validation_bytes=validation_bytes,
            review_summary_bytes=summary_bytes,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except DecisionReferenceReviewBundleIndexError as exc:
        report = _failure_report(exc.code)
    except ValueError:
        report = _failure_report("operator_decision_reference_review_bundle_index_invalid")

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            "MAGMA operator decision-reference review bundle index FAILED: "
            + ", ".join(report["blockers"]),
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index(
    *,
    decision_validation_report: Mapping[str, Any],
    review_summary: Mapping[str, Any],
    decision_validation_bytes: bytes,
    review_summary_bytes: bytes,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return a path-free local index for decision-reference review artifacts."""

    _assert_mapping(
        "operator_decision_reference_validation",
        decision_validation_report,
    )
    _assert_mapping("operator_decision_reference_review_summary", review_summary)
    _assert_no_forbidden_input(
        "operator_decision_reference_validation",
        decision_validation_report,
    )
    _assert_no_forbidden_input(
        "operator_decision_reference_review_summary",
        review_summary,
    )
    _assert_bytes_match_artifact(
        "operator_decision_reference_validation",
        decision_validation_report,
        decision_validation_bytes,
    )
    _assert_bytes_match_artifact(
        "operator_decision_reference_review_summary",
        review_summary,
        review_summary_bytes,
    )

    validation_reference = _assert_validation_contract(decision_validation_report)
    review_reference = _assert_review_summary_contract(review_summary)
    identity = _matching_identity(decision_validation_report, review_summary)
    if validation_reference["decision_reference"] != review_reference["decision_reference"]:
        raise DecisionReferenceReviewBundleIndexError(
            "operator_decision_reference_artifact_mismatch"
        )
    if (
        validation_reference["expected_decision_reference"]
        != review_reference["expected_decision_reference"]
    ):
        raise DecisionReferenceReviewBundleIndexError(
            "operator_decision_reference_artifact_mismatch"
        )

    artifacts = [
        _artifact_record(
            "operator_decision_reference_validation",
            "validated_operator_decision_reference_context",
            decision_validation_report,
            decision_validation_bytes,
        ),
        _artifact_record(
            "operator_decision_reference_review_summary",
            "path_free_operator_decision_reference_review_context",
            review_summary,
            review_summary_bytes,
        ),
    ]
    index = {
        "ok": True,
        "bundle_index_version": BUNDLE_INDEX_VERSION,
        "created_at_utc": _utc_iso(now_utc or datetime.now(timezone.utc)),
        "release_ref": identity["release_ref"],
        "commit_sha": identity["commit_sha"],
        "ci_run_ref": identity["ci_run_ref"],
        "operator_decision_reference": {
            "decision_reference": validation_reference["decision_reference"],
            "expected_decision_reference": validation_reference[
                "expected_decision_reference"
            ],
            "decision_reference_validated": True,
            "decision_reference_matches_expected": True,
            "decision_reference_is_approval": False,
            "decision_reference_is_release_decision": False,
            "decision_must_be_recorded_separately": True,
            "review_context_only": True,
        },
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "consistency": {
            "release_ref_match": True,
            "commit_sha_match": True,
            "ci_run_ref_match": True,
            "decision_reference_match": True,
            "required_artifacts_present": list(_ARTIFACT_ORDER),
            "all_artifact_digests_recorded": True,
            "artifact_payloads_included": False,
            "local_paths_recorded": False,
        },
        "operator_boundary": {
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
        },
        "reviewer_next_actions": [
            "review_operator_decision_reference_review_bundle_index",
            "record_operator_decision_separately",
        ],
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
        "blockers": [],
        "warnings": [],
    }
    _assert_no_forbidden_output(json.dumps(index, allow_nan=False, sort_keys=True))
    return index


def _load_json_artifact(path: Path, artifact_id: str) -> tuple[bytes, Mapping[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise DecisionReferenceReviewBundleIndexError(
            f"{artifact_id}_unreadable"
        ) from exc
    parsed = _parse_json_bytes(raw, artifact_id)
    if not isinstance(parsed, Mapping):
        raise DecisionReferenceReviewBundleIndexError(
            f"{artifact_id}_not_mapping"
        )
    return raw, parsed


def _failure_report(reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "bundle_index_version": BUNDLE_INDEX_VERSION,
        "release_ref": "invalid_ref",
        "commit_sha": "invalid_commit",
        "ci_run_ref": "invalid_ref",
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
            f"operator_decision_reference_review_bundle_index_failed:{reason}"
        ],
        "warnings": [],
    }


def _assert_validation_contract(report: Mapping[str, Any]) -> dict[str, str]:
    if report.get("validation_version") != VALIDATION_VERSION:
        raise DecisionReferenceReviewBundleIndexError(
            "operator_decision_reference_validation_version_mismatch"
        )
    if report.get("ok") is not True:
        raise DecisionReferenceReviewBundleIndexError(
            "operator_decision_reference_validation_not_ok"
        )
    _expect_empty_items(
        report.get("blockers"),
        "operator_decision_reference_validation_blockers_present",
    )
    _expect_true(
        report,
        "manual_review_required",
        "operator_decision_reference_validation",
    )
    _expect_authority_false(report, "operator_decision_reference_validation")
    reference = _mapping(report.get("operator_decision_reference"))
    result = _assert_reference_contract(
        reference,
        "operator_decision_reference_validation",
    )
    verification = _mapping(report.get("bundle_verification"))
    _expect_authority_absent_or_false(
        verification,
        "operator_decision_reference_validation_verification",
    )
    for field in ("verification_summary_ok", "verification_ok", "identity_match"):
        _expect_true(verification, field, "operator_decision_reference_validation")
    if verification.get("verification_summary_version") != VERIFICATION_SUMMARY_VERSION:
        raise DecisionReferenceReviewBundleIndexError(
            "operator_decision_reference_validation_verification_summary_version_mismatch"
        )
    if verification.get("bridge_template_version") != TEMPLATE_VERSION:
        raise DecisionReferenceReviewBundleIndexError(
            "operator_decision_reference_validation_bridge_template_version_mismatch"
        )
    boundary = _mapping(report.get("operator_boundary"))
    _expect_true(boundary, "validation_only", "operator_decision_reference_validation_boundary")
    _expect_true(boundary, "manual_review_required", "operator_decision_reference_validation_boundary")
    _expect_empty_items(
        boundary.get("boundary_blockers"),
        "operator_decision_reference_validation_boundary_blockers_present",
    )
    _expect_authority_false(
        boundary,
        "operator_decision_reference_validation_boundary",
    )
    return result


def _assert_review_summary_contract(summary: Mapping[str, Any]) -> dict[str, str]:
    if summary.get("summary_version") != REVIEW_SUMMARY_VERSION:
        raise DecisionReferenceReviewBundleIndexError(
            "operator_decision_reference_review_summary_version_mismatch"
        )
    if summary.get("ok") is not True:
        raise DecisionReferenceReviewBundleIndexError(
            "operator_decision_reference_review_summary_not_ok"
        )
    _expect_empty_items(
        summary.get("blockers"),
        "operator_decision_reference_review_summary_blockers_present",
    )
    _expect_true(
        summary,
        "manual_review_required",
        "operator_decision_reference_review_summary",
    )
    _expect_authority_false(summary, "operator_decision_reference_review_summary")
    ownership = _mapping(summary.get("reviewer_ownership"))
    _expect_true(
        ownership,
        "manual_review_required",
        "operator_decision_reference_review_summary_ownership",
    )
    _expect_authority_absent_or_false(
        ownership,
        "operator_decision_reference_review_summary_ownership",
    )
    for field in (
        "approval_granted",
        "release_decision_made",
        "automatic_release_decision",
    ):
        _expect_false(
            ownership,
            field,
            "operator_decision_reference_review_summary_ownership",
        )
    reference = _mapping(summary.get("operator_decision_reference_review"))
    result = _assert_reference_contract(
        reference,
        "operator_decision_reference_review_summary",
    )
    if reference.get("review_context_only") is not True:
        raise DecisionReferenceReviewBundleIndexError(
            "operator_decision_reference_review_summary_context_only_not_true"
        )
    verification = _mapping(summary.get("bundle_verification"))
    _expect_authority_absent_or_false(
        verification,
        "operator_decision_reference_review_summary_verification",
    )
    for field in ("verification_summary_ok", "verification_ok", "identity_match"):
        _expect_true(verification, field, "operator_decision_reference_review_summary")
    if verification.get("verification_summary_version") != VERIFICATION_SUMMARY_VERSION:
        raise DecisionReferenceReviewBundleIndexError(
            "operator_decision_reference_review_summary_verification_summary_version_mismatch"
        )
    if verification.get("bridge_template_version") != TEMPLATE_VERSION:
        raise DecisionReferenceReviewBundleIndexError(
            "operator_decision_reference_review_summary_bridge_template_version_mismatch"
        )
    boundary = _mapping(summary.get("operator_boundary"))
    _expect_true(
        boundary,
        "decision_validation_boundary_ok",
        "operator_decision_reference_review_summary_boundary",
    )
    _expect_true(
        boundary,
        "validation_only",
        "operator_decision_reference_review_summary_boundary",
    )
    _expect_true(
        boundary,
        "manual_review_required",
        "operator_decision_reference_review_summary_boundary",
    )
    _expect_empty_items(
        boundary.get("boundary_blockers"),
        "operator_decision_reference_review_summary_boundary_blockers_present",
    )
    _expect_authority_false(
        boundary,
        "operator_decision_reference_review_summary_boundary",
    )
    return result


def _assert_reference_contract(
    reference: Mapping[str, Any],
    label: str,
) -> dict[str, str]:
    decision_reference = _required_safe_ref(
        reference.get("decision_reference"),
        f"{label}_decision_reference_invalid",
    )
    expected_decision_reference = _required_safe_ref(
        reference.get("expected_decision_reference"),
        f"{label}_expected_decision_reference_invalid",
    )
    if reference.get("decision_reference_present") is not True:
        raise DecisionReferenceReviewBundleIndexError(
            f"{label}_decision_reference_missing"
        )
    if reference.get("decision_reference_validated") is not True:
        raise DecisionReferenceReviewBundleIndexError(
            f"{label}_decision_reference_not_validated"
        )
    if reference.get("decision_reference_matches_expected") is not True:
        raise DecisionReferenceReviewBundleIndexError(
            f"{label}_decision_reference_mismatch"
        )
    if decision_reference != expected_decision_reference:
        raise DecisionReferenceReviewBundleIndexError(
            f"{label}_decision_reference_mismatch"
        )
    for field in _REFERENCE_FALSE_FIELDS:
        _expect_false(reference, field, label)
    _expect_authority_absent_or_false(reference, label)
    if reference.get("decision_must_be_recorded_separately") is not True:
        raise DecisionReferenceReviewBundleIndexError(
            f"{label}_decision_must_be_recorded_separately_not_true"
        )
    return {
        "decision_reference": decision_reference,
        "expected_decision_reference": expected_decision_reference,
    }


def _matching_identity(
    validation_report: Mapping[str, Any],
    review_summary: Mapping[str, Any],
) -> dict[str, str]:
    validation_identity = _identity(validation_report, "operator_decision_reference_validation")
    summary_identity = _identity(review_summary, "operator_decision_reference_review_summary")
    if validation_identity != summary_identity:
        raise DecisionReferenceReviewBundleIndexError("artifact_identity_mismatch")
    return validation_identity


def _identity(artifact: Mapping[str, Any], label: str) -> dict[str, str]:
    return {
        "release_ref": _required_safe_ref(
            artifact.get("release_ref"),
            f"{label}_release_ref_invalid",
        ),
        "commit_sha": _required_commit(
            artifact.get("commit_sha"),
            f"{label}_commit_sha_invalid",
        ),
        "ci_run_ref": _required_safe_ref(
            artifact.get("ci_run_ref"),
            f"{label}_ci_run_ref_invalid",
        ),
    }


def _artifact_record(
    artifact_id: str,
    role: str,
    artifact: Mapping[str, Any],
    raw: bytes,
) -> dict[str, Any]:
    return {
        "artifact_id": artifact_id,
        "role": role,
        "sha256": _sha256_hex(raw),
        "size_bytes": len(raw),
        "json_schema_version": _schema_version(artifact),
        "payload_included": False,
        "local_path_recorded": False,
    }


def _schema_version(artifact: Mapping[str, Any]) -> str:
    for field in ("validation_version", "summary_version"):
        value = artifact.get(field)
        if isinstance(value, str):
            return value
    return "invalid_schema"


def _assert_mapping(artifact_id: str, value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping):
        raise DecisionReferenceReviewBundleIndexError(f"{artifact_id}_not_mapping")


def _assert_no_forbidden_input(artifact_id: str, value: Mapping[str, Any]) -> None:
    try:
        serialized = json.dumps(value, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise DecisionReferenceReviewBundleIndexError(
            f"{artifact_id}_non_finite_json_value"
        ) from exc
    if _forbidden_output_markers(serialized):
        raise DecisionReferenceReviewBundleIndexError(f"{artifact_id}_forbidden_marker")


def _expect_authority_false(artifact: Mapping[str, Any], label: str) -> None:
    for field in _AUTHORITY_FALSE_FIELDS:
        _expect_false(artifact, field, label)


def _expect_authority_absent_or_false(artifact: Mapping[str, Any], label: str) -> None:
    for field in _AUTHORITY_FALSE_FIELDS:
        if field in artifact:
            _expect_false(artifact, field, label)


def _expect_false(artifact: Mapping[str, Any], field: str, label: str) -> None:
    if artifact.get(field) is not False:
        raise DecisionReferenceReviewBundleIndexError(f"{label}_{field}_not_false")


def _expect_true(artifact: Mapping[str, Any], field: str, label: str) -> None:
    if artifact.get(field) is not True:
        raise DecisionReferenceReviewBundleIndexError(f"{label}_{field}_not_true")


def _expect_empty_items(value: Any, reason: str) -> None:
    if value is None:
        return
    if isinstance(value, (list, tuple, set)):
        if value:
            raise DecisionReferenceReviewBundleIndexError(reason)
        return
    raise DecisionReferenceReviewBundleIndexError(reason)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _required_safe_ref(value: Any, reason: str) -> str:
    if (
        isinstance(value, str)
        and _SAFE_REF_RE.match(value)
        and not _forbidden_output_markers(value)
    ):
        return value
    raise DecisionReferenceReviewBundleIndexError(reason)


def _required_commit(value: Any, reason: str) -> str:
    if isinstance(value, str) and _COMMIT_RE.match(value):
        return value
    raise DecisionReferenceReviewBundleIndexError(reason)


def _parse_json_bytes(raw: bytes, artifact_id: str) -> Any:
    def reject_constant(_value: str) -> None:
        raise ValueError("non_finite_json_constant")

    try:
        return json.loads(
            raw.decode("utf-8"),
            parse_constant=reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise DecisionReferenceReviewBundleIndexError(
            f"{artifact_id}_decode_error"
        ) from exc
    except json.JSONDecodeError as exc:
        raise DecisionReferenceReviewBundleIndexError(
            f"{artifact_id}_json_error"
        ) from exc
    except ValueError as exc:
        raise DecisionReferenceReviewBundleIndexError(
            f"{artifact_id}_json_error"
        ) from exc


def _assert_bytes_match_artifact(
    artifact_id: str,
    artifact: Mapping[str, Any],
    raw: bytes,
) -> None:
    parsed = _parse_json_bytes(raw, artifact_id)
    if parsed != artifact:
        raise DecisionReferenceReviewBundleIndexError(
            f"{artifact_id}_bytes_mismatch"
        )


def _parse_utc(raw: str) -> datetime:
    if not raw.endswith("Z"):
        raise DecisionReferenceReviewBundleIndexError("now_utc_unsafe")
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as exc:
        raise DecisionReferenceReviewBundleIndexError("now_utc_unsafe") from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != timezone.utc.utcoffset(parsed)
    ):
        raise DecisionReferenceReviewBundleIndexError("now_utc_unsafe")
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


def _sha256_hex(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _forbidden_output_markers(text: str) -> list[str]:
    lower_text = text.lower()
    return sorted(
        marker
        for marker in FORBIDDEN_OUTPUT_MARKERS
        if marker.lower() in lower_text
    )


def _assert_no_forbidden_output(text: str) -> None:
    found = _forbidden_output_markers(text)
    if found:
        raise ValueError(
            "sanitized operator decision-reference review bundle index contains "
            "forbidden markers: " + ", ".join(found)
        )


if __name__ == "__main__":
    raise SystemExit(main())
