# SPDX-License-Identifier: BUSL-1.1
"""Validate a MAGMA reviewer handoff bundle operator decision reference."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
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
from tools.build_magma_alert_feed_reviewer_handoff_bundle_verification_summary import (  # noqa: E402
    SUMMARY_VERSION as VERIFICATION_SUMMARY_VERSION,
)
from tools.package_magma_alert_feed_release_evidence import (  # noqa: E402
    FORBIDDEN_OUTPUT_MARKERS,
)


VALIDATION_VERSION = (
    "magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_validation.v1"
)

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$")

_ARTIFACT_IDS = (
    "release_evidence_package",
    "validator_report",
    "reviewer_handoff_summary",
    "bridge_event_template",
)
_VERIFICATION_CHECK_NAMES = (
    "digest_checks",
    "size_checks",
    "schema_version_checks",
)
_SUMMARY_FALSE_FIELDS = (
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
_BRIDGE_REPORT_FALSE_FIELDS = (
    "direct_bridge_write_performed",
    "automatic_release_decision",
    "approval_granted",
    "release_decision_made",
    "runtime_controls_added",
    "transport_added",
    "external_fetch_performed",
)
_BRIDGE_PAYLOAD_FALSE_FIELDS = (
    "direct_bridge_write_performed",
    "transport_added",
    "external_fetch_performed",
    "runtime_controls_added",
)
_OPERATOR_DECISION_FALSE_FIELDS = (
    "approval_granted",
    "release_decision_made",
    "automatic_release_decision",
    "decision_reference_is_approval",
    "decision_reference_is_release_decision",
)


class DecisionReferenceValidationError(ValueError):
    """Raised when local validator inputs cannot be safely echoed."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verification-summary-json", required=True, type=Path)
    parser.add_argument("--bridge-template-json", required=True, type=Path)
    parser.add_argument("--expected-decision-ref", required=True)
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override such as 2026-05-28T08:00:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        verification_summary = _load_json_artifact(
            args.verification_summary_json,
            "verification_summary",
        )
        bridge_template_report = _load_json_artifact(
            args.bridge_template_json,
            "bridge_event_template",
        )
        report = validate_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference(
            verification_summary=verification_summary,
            bridge_template_report=bridge_template_report,
            expected_decision_ref=args.expected_decision_ref,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except DecisionReferenceValidationError as exc:
        report = _failure_report(exc.code)
    except ValueError:
        report = _failure_report("operator_decision_reference_validation_invalid")

    if args.json or report["ok"]:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            "MAGMA reviewer handoff operator decision-reference validation "
            "FAILED: " + ", ".join(report["blockers"]),
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def validate_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference(
    *,
    verification_summary: Mapping[str, Any],
    bridge_template_report: Mapping[str, Any],
    expected_decision_ref: str,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Validate a context-only operator decision reference for a local bundle."""

    _assert_mapping("verification_summary", verification_summary)
    _assert_mapping("bridge_event_template", bridge_template_report)
    _assert_no_forbidden_input("verification_summary", verification_summary)
    _assert_no_forbidden_input("bridge_event_template", bridge_template_report)
    _validate_safe_ref("expected_decision_ref", expected_decision_ref)

    blockers: list[str] = []
    _collect_verification_summary_blockers(verification_summary, blockers)
    bridge_payload = _bridge_event_payload(bridge_template_report, blockers)
    operator_decision = _mapping(bridge_payload.get("operator_decision"))
    _collect_bridge_template_blockers(
        bridge_template_report,
        bridge_payload,
        operator_decision,
        blockers,
    )
    identity = _matching_identity(verification_summary, bridge_payload, blockers)
    observed_decision_ref = _safe_ref_or_invalid(
        operator_decision.get("decision_reference")
    )
    decision_reference_present = (
        operator_decision.get("decision_reference_present") is True
    )
    if not decision_reference_present:
        blockers.append("operator_decision_reference_missing")
    if observed_decision_ref == "invalid_ref":
        blockers.append("operator_decision_reference_invalid")
    if observed_decision_ref != "invalid_ref" and not decision_reference_present:
        blockers.append("operator_decision_reference_present_inconsistent")
    if (
        observed_decision_ref != "invalid_ref"
        and observed_decision_ref != expected_decision_ref
    ):
        blockers.append("operator_decision_reference_mismatch")

    blocker_set = sorted(set(blockers))
    report = {
        "ok": not blocker_set,
        "validation_version": VALIDATION_VERSION,
        "created_at_utc": _utc_iso(now_utc or datetime.now(timezone.utc)),
        "release_ref": identity["release_ref"],
        "commit_sha": identity["commit_sha"],
        "ci_run_ref": identity["ci_run_ref"],
        "operator_decision_reference": {
            "decision_reference": observed_decision_ref,
            "expected_decision_reference": expected_decision_ref,
            "decision_reference_present": decision_reference_present,
            "decision_reference_validated": not blocker_set,
            "decision_reference_matches_expected": (
                observed_decision_ref == expected_decision_ref
            ),
            "decision_reference_is_approval": False,
            "decision_reference_is_release_decision": False,
            "decision_must_be_recorded_separately": True,
        },
        "bundle_verification": {
            "verification_summary_ok": verification_summary.get("ok") is True,
            "verification_ok": _mapping(
                verification_summary.get("bundle_verification")
            ).get("verification_ok")
            is True,
            "verification_summary_version": _safe_ref_or_invalid(
                verification_summary.get("summary_version")
            ),
            "bridge_template_version": _safe_ref_or_invalid(
                bridge_template_report.get("template_version")
            ),
            "identity_match": (
                identity["release_ref"] != "invalid_ref"
                and identity["commit_sha"] != "invalid_commit"
                and identity["ci_run_ref"] != "invalid_ref"
            ),
        },
        "operator_boundary": {
            "validation_only": True,
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
            "review_operator_decision_reference_validation",
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
        "blockers": blocker_set,
        "warnings": [],
    }
    _assert_no_forbidden_output(json.dumps(report, allow_nan=False, sort_keys=True))
    return report


def _collect_verification_summary_blockers(
    summary: Mapping[str, Any],
    blockers: list[str],
) -> None:
    if summary.get("summary_version") != VERIFICATION_SUMMARY_VERSION:
        blockers.append("verification_summary_version_mismatch")
    if summary.get("ok") is not True:
        blockers.append("verification_summary_not_ok")
    verification = _mapping(summary.get("bundle_verification"))
    if verification.get("verification_ok") is not True:
        blockers.append("verification_summary_verification_not_ok")
    if _has_reported_items(summary.get("blockers")):
        blockers.append("verification_summary_blockers_present")
    if _has_reported_items(verification.get("blockers")):
        blockers.append("verification_summary_report_blockers_present")
    for check_name in _VERIFICATION_CHECK_NAMES:
        checks = _mapping(verification.get(check_name))
        for artifact_id in _ARTIFACT_IDS:
            if checks.get(artifact_id) != "match":
                blockers.append(
                    "verification_summary_check_not_match:"
                    f"{check_name}:{artifact_id}"
                )
    for field in _SUMMARY_FALSE_FIELDS:
        if summary.get(field) is not False:
            blockers.append(f"verification_summary_{field}_not_false")
    boundary = _mapping(summary.get("operator_boundary"))
    for field in _SUMMARY_FALSE_FIELDS:
        if boundary.get(field) is not False:
            blockers.append(f"verification_summary_operator_boundary_{field}_not_false")


def _collect_bridge_template_blockers(
    report: Mapping[str, Any],
    payload: Mapping[str, Any],
    operator_decision: Mapping[str, Any],
    blockers: list[str],
) -> None:
    if report.get("template_version") != TEMPLATE_VERSION:
        blockers.append("bridge_event_template_version_mismatch")
    if report.get("ok") is not True:
        blockers.append("bridge_event_template_not_ok")
    if report.get("template_only") is not True:
        blockers.append("bridge_event_template_not_template_only")
    for field in _BRIDGE_REPORT_FALSE_FIELDS:
        if report.get(field) is not False:
            blockers.append(f"bridge_event_template_{field}_not_false")
    if payload.get("schema_version") != TEMPLATE_VERSION:
        blockers.append("bridge_event_template_payload_version_mismatch")
    if payload.get("template_only") is not True:
        blockers.append("bridge_event_template_not_template_only")
    for field in _BRIDGE_PAYLOAD_FALSE_FIELDS:
        if payload.get(field) is not False:
            blockers.append(f"bridge_event_template_{field}_not_false")
    if not operator_decision:
        blockers.append("operator_decision_reference_missing")
    for field in _OPERATOR_DECISION_FALSE_FIELDS:
        if operator_decision.get(field) is not False:
            blockers.append(f"operator_decision_{field}_not_false")
    if operator_decision.get("decision_must_be_recorded_separately") is not True:
        blockers.append(
            "operator_decision_decision_must_be_recorded_separately_not_true"
        )


def _matching_identity(
    verification_summary: Mapping[str, Any],
    bridge_payload: Mapping[str, Any],
    blockers: list[str],
) -> dict[str, str]:
    return {
        "release_ref": _matching_safe_ref(
            verification_summary,
            bridge_payload,
            "release_ref",
            blockers,
        ),
        "commit_sha": _matching_commit(
            verification_summary,
            bridge_payload,
            blockers,
        ),
        "ci_run_ref": _matching_safe_ref(
            verification_summary,
            bridge_payload,
            "ci_run_ref",
            blockers,
        ),
    }


def _matching_safe_ref(
    verification_summary: Mapping[str, Any],
    bridge_payload: Mapping[str, Any],
    field: str,
    blockers: list[str],
) -> str:
    summary_value = _safe_ref_or_invalid(verification_summary.get(field))
    bridge_value = _safe_ref_or_invalid(bridge_payload.get(field))
    if summary_value == "invalid_ref":
        blockers.append(f"verification_summary_{field}_invalid")
    if bridge_value == "invalid_ref":
        blockers.append(f"bridge_event_template_{field}_invalid")
    if summary_value != "invalid_ref" and bridge_value != "invalid_ref":
        if summary_value != bridge_value:
            blockers.append(f"artifact_identity_{field}_mismatch")
            return "invalid_ref"
        return summary_value
    return "invalid_ref"


def _matching_commit(
    verification_summary: Mapping[str, Any],
    bridge_payload: Mapping[str, Any],
    blockers: list[str],
) -> str:
    summary_value = _commit_or_invalid(verification_summary.get("commit_sha"))
    bridge_value = _commit_or_invalid(bridge_payload.get("commit_sha"))
    if summary_value == "invalid_commit":
        blockers.append("verification_summary_commit_sha_invalid")
    if bridge_value == "invalid_commit":
        blockers.append("bridge_event_template_commit_sha_invalid")
    if summary_value != "invalid_commit" and bridge_value != "invalid_commit":
        if summary_value != bridge_value:
            blockers.append("artifact_identity_commit_sha_mismatch")
            return "invalid_commit"
        return summary_value
    return "invalid_commit"


def _bridge_event_payload(
    bridge_template_report: Mapping[str, Any],
    blockers: list[str],
) -> Mapping[str, Any]:
    event = _mapping(bridge_template_report.get("bridge_event_template"))
    payload = _mapping(event.get("payload"))
    if not payload:
        blockers.append("bridge_event_payload_missing")
    return payload


def _load_json_artifact(path: Path, artifact_id: str) -> Mapping[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DecisionReferenceValidationError(f"{artifact_id}_unreadable") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DecisionReferenceValidationError(f"{artifact_id}_json_error") from exc
    if not isinstance(parsed, Mapping):
        raise DecisionReferenceValidationError(f"{artifact_id}_not_mapping")
    return parsed


def _failure_report(reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "validation_version": VALIDATION_VERSION,
        "release_ref": "invalid_ref",
        "commit_sha": "invalid_commit",
        "ci_run_ref": "invalid_ref",
        "operator_decision_reference": {
            "decision_reference": "invalid_ref",
            "expected_decision_reference": "invalid_ref",
            "decision_reference_present": False,
            "decision_reference_validated": False,
            "decision_reference_matches_expected": False,
            "decision_reference_is_approval": False,
            "decision_reference_is_release_decision": False,
            "decision_must_be_recorded_separately": True,
        },
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
        "blockers": [f"operator_decision_reference_validation_failed:{reason}"],
        "warnings": [],
    }


def _assert_mapping(artifact_id: str, value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping):
        raise DecisionReferenceValidationError(f"{artifact_id}_not_mapping")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _has_reported_items(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, tuple, set)):
        return len(value) > 0
    return True


def _safe_ref_or_invalid(value: Any) -> str:
    return (
        value
        if isinstance(value, str) and _SAFE_REF_RE.match(value)
        and not _forbidden_output_markers(value)
        else "invalid_ref"
    )


def _commit_or_invalid(value: Any) -> str:
    return (
        value
        if isinstance(value, str) and _COMMIT_RE.match(value)
        else "invalid_commit"
    )


def _validate_safe_ref(label: str, value: str) -> None:
    if (
        not isinstance(value, str)
        or not _SAFE_REF_RE.match(value)
        or _forbidden_output_markers(value)
    ):
        raise DecisionReferenceValidationError(f"{label}_unsafe")


def _parse_utc(raw: str) -> datetime:
    if not raw.endswith("Z"):
        raise DecisionReferenceValidationError("now_utc_unsafe")
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as exc:
        raise DecisionReferenceValidationError("now_utc_unsafe") from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != timezone.utc.utcoffset(parsed)
    ):
        raise DecisionReferenceValidationError("now_utc_unsafe")
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


def _assert_no_forbidden_input(artifact_id: str, value: Mapping[str, Any]) -> None:
    if _forbidden_output_markers(json.dumps(value, sort_keys=True)):
        raise DecisionReferenceValidationError(f"{artifact_id}_forbidden_marker")


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
            "sanitized operator decision-reference validation contains "
            "forbidden markers: " + ", ".join(found)
        )


if __name__ == "__main__":
    raise SystemExit(main())
