# SPDX-License-Identifier: BUSL-1.1
"""Render sanitized reviewer context for a MAGMA operator decision reference."""
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
from tools.validate_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference import (  # noqa: E402
    VALIDATION_VERSION,
)


SUMMARY_VERSION = "magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_summary.v1"

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$")
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,191}$")
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


class SafeInputError(ValueError):
    """Raised when operator-provided refs are unsafe to echo."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decision-validation-json", required=True, type=Path)
    parser.add_argument("--reviewer-agent", required=True)
    parser.add_argument("--handoff-ref", required=True)
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
        decision_validation_report = json.loads(
            args.decision_validation_json.read_text(encoding="utf-8")
        )
    except OSError:
        summary = _failure_summary("decision_validation_report_unreadable")
    except UnicodeDecodeError:
        summary = _failure_summary("decision_validation_report_decode_error")
    except json.JSONDecodeError:
        summary = _failure_summary("decision_validation_report_json_error")
    else:
        try:
            summary = (
                build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_summary(
                    decision_validation_report=decision_validation_report,
                    reviewer_agent_id=args.reviewer_agent,
                    handoff_ref=args.handoff_ref,
                    now_utc=_parse_utc(args.now) if args.now else None,
                )
            )
        except SafeInputError as exc:
            summary = _failure_summary(exc.code)
        except ValueError:
            summary = _failure_summary("operator_decision_reference_review_invalid")

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    elif summary["ok"]:
        print(render_operator_decision_reference_review_summary_markdown(summary))
    else:
        print(
            "MAGMA reviewer handoff operator decision-reference review "
            "summary FAILED: " + ", ".join(summary["blockers"]),
            file=sys.stderr,
        )
    return 0 if summary["ok"] else 1


def build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_summary(
    *,
    decision_validation_report: Mapping[str, Any],
    reviewer_agent_id: str,
    handoff_ref: str,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return path-free reviewer context for a validated decision reference."""

    if not isinstance(decision_validation_report, Mapping):
        raise ValueError("decision_validation_report_not_mapping")
    _assert_no_forbidden_input("decision_validation_report", decision_validation_report)
    _validate_safe_ref("reviewer_agent_id", reviewer_agent_id)
    _validate_safe_ref("handoff_ref", handoff_ref)

    report_blockers = _safe_token_list(decision_validation_report.get("blockers"))
    report_warnings = _safe_token_list(decision_validation_report.get("warnings"))
    contract_blockers = _decision_validation_contract_blockers(
        decision_validation_report
    )
    blockers = sorted(set(report_blockers + contract_blockers))
    reference = _mapping(
        decision_validation_report.get("operator_decision_reference")
    )
    verification = _mapping(decision_validation_report.get("bundle_verification"))
    validation_ok = (
        decision_validation_report.get("ok") is True
        and decision_validation_report.get("validation_version") == VALIDATION_VERSION
    )
    decision_reference = _safe_ref_or_invalid(reference.get("decision_reference"))
    expected_decision_reference = _safe_ref_or_invalid(
        reference.get("expected_decision_reference")
    )
    reference_matches = (
        decision_reference != "invalid_ref"
        and decision_reference == expected_decision_reference
        and reference.get("decision_reference_matches_expected") is True
    )
    boundary_ok = not _operator_boundary_blockers(decision_validation_report)
    summary = {
        "ok": validation_ok and not blockers,
        "summary_version": SUMMARY_VERSION,
        "created_at_utc": _utc_iso(now_utc or datetime.now(timezone.utc)),
        "release_ref": _safe_ref_or_invalid(
            decision_validation_report.get("release_ref")
        ),
        "commit_sha": _commit_or_invalid(
            decision_validation_report.get("commit_sha")
        ),
        "ci_run_ref": _safe_ref_or_invalid(
            decision_validation_report.get("ci_run_ref")
        ),
        "reviewer_ownership": {
            "reviewer_agent_id": reviewer_agent_id,
            "handoff_ref": handoff_ref,
            "manual_review_required": True,
            "approval_granted": False,
            "release_decision_made": False,
            "automatic_release_decision": False,
        },
        "operator_decision_reference_review": {
            "decision_reference": decision_reference,
            "expected_decision_reference": expected_decision_reference,
            "decision_reference_present": (
                reference.get("decision_reference_present") is True
            ),
            "decision_reference_validated": (
                reference.get("decision_reference_validated") is True
                and not blockers
            ),
            "decision_reference_matches_expected": reference_matches,
            "decision_reference_is_approval": False,
            "decision_reference_is_release_decision": False,
            "decision_must_be_recorded_separately": True,
            "review_context_only": True,
            "manual_review_required": True,
        },
        "bundle_verification": {
            "verification_summary_ok": (
                verification.get("verification_summary_ok") is True
            ),
            "verification_ok": verification.get("verification_ok") is True,
            "verification_summary_version": _safe_ref_or_invalid(
                verification.get("verification_summary_version")
            ),
            "bridge_template_version": _safe_ref_or_invalid(
                verification.get("bridge_template_version")
            ),
            "identity_match": verification.get("identity_match") is True,
        },
        "operator_boundary": {
            "decision_validation_boundary_ok": boundary_ok,
            "boundary_blockers": _operator_boundary_blockers(
                decision_validation_report
            ),
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
            "review_operator_decision_reference_context",
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
        "blockers": blockers,
        "warnings": report_warnings,
    }
    _assert_no_forbidden_output(json.dumps(summary, allow_nan=False, sort_keys=True))
    return summary


def render_operator_decision_reference_review_summary_markdown(
    summary: Mapping[str, Any],
) -> str:
    reference = _mapping(summary.get("operator_decision_reference_review"))
    verification = _mapping(summary.get("bundle_verification"))
    boundary = _mapping(summary.get("operator_boundary"))
    lines = [
        "# MAGMA Operator Decision-Reference Review Summary",
        "",
        f"- Summary version: `{summary.get('summary_version')}`",
        f"- Release ref: `{summary.get('release_ref')}`",
        f"- Commit SHA: `{summary.get('commit_sha')}`",
        f"- CI run ref: `{summary.get('ci_run_ref')}`",
        f"- Created at UTC: `{summary.get('created_at_utc')}`",
        "- Manual review required: `true`",
        "- Automatic release decision: `false`",
        "- Approval granted: `false`",
        "- Release decision made: `false`",
        "- Direct bridge write performed: `false`",
        "- Transport added: `false`",
        "- External fetch performed: `false`",
        "- Runtime controls added: `false`",
        "- Artifact payloads included: `false`",
        "- Local paths recorded: `false`",
        "",
        "## Decision Reference",
        "",
        f"- Decision reference: `{reference.get('decision_reference')}`",
        "- Expected decision reference: "
        f"`{reference.get('expected_decision_reference')}`",
        "- Decision reference validated: "
        f"`{reference.get('decision_reference_validated')}`",
        "- Decision reference matches expected: "
        f"`{reference.get('decision_reference_matches_expected')}`",
        "- Decision reference is approval: `false`",
        "- Decision reference is release decision: `false`",
        "- Decision must be recorded separately: `true`",
        "",
        "## Bundle Verification",
        "",
        "- Verification summary OK: "
        f"`{verification.get('verification_summary_ok')}`",
        f"- Verification OK: `{verification.get('verification_ok')}`",
        "- Verification summary version: "
        f"`{verification.get('verification_summary_version')}`",
        f"- Bridge template version: `{verification.get('bridge_template_version')}`",
        f"- Identity match: `{verification.get('identity_match')}`",
        "",
        "## Operator Boundary",
        "",
        "- Decision validation boundary OK: "
        f"`{boundary.get('decision_validation_boundary_ok')}`",
        "- Boundary blockers:",
    ]
    lines.extend(_markdown_token_list(boundary.get("boundary_blockers")))
    lines.append("- Summary blockers:")
    lines.extend(_markdown_token_list(summary.get("blockers")))
    lines.extend([
        "",
        "This summary is reviewer context only. It does not approve, merge, "
        "promote, append bridge events, transport artifacts, fetch endpoints, "
        "include payloads, record local paths, control feeds, or grant runtime "
        "authority. The operator decision must be recorded separately.",
        "",
    ])
    markdown = "\n".join(lines)
    _assert_no_forbidden_output(markdown)
    return markdown


def _failure_summary(reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "summary_version": SUMMARY_VERSION,
        "release_ref": "invalid_ref",
        "commit_sha": "invalid_commit",
        "ci_run_ref": "invalid_ref",
        "manual_review_required": True,
        "automatic_release_decision": False,
        "approval_granted": False,
        "release_decision_made": False,
        "direct_bridge_write_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "runtime_controls_added": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": [
            f"operator_decision_reference_review_summary_failed:{reason}"
        ],
        "warnings": [],
    }


def _decision_validation_contract_blockers(report: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    if report.get("validation_version") != VALIDATION_VERSION:
        blockers.append("decision_reference_validation_version_mismatch")
    if report.get("ok") is not True:
        blockers.append("decision_reference_validation_not_ok")
    if _safe_ref_or_invalid(report.get("release_ref")) == "invalid_ref":
        blockers.append("decision_reference_validation_release_ref_invalid")
    if _commit_or_invalid(report.get("commit_sha")) == "invalid_commit":
        blockers.append("decision_reference_validation_commit_sha_invalid")
    if _safe_ref_or_invalid(report.get("ci_run_ref")) == "invalid_ref":
        blockers.append("decision_reference_validation_ci_run_ref_invalid")
    if report.get("manual_review_required") is not True:
        blockers.append("decision_reference_validation_manual_review_not_true")
    for field in _AUTHORITY_FALSE_FIELDS:
        if report.get(field) is not False:
            blockers.append(f"decision_reference_validation_{field}_not_false")

    reference = _mapping(report.get("operator_decision_reference"))
    if not reference:
        blockers.append("operator_decision_reference_missing")
    decision_ref = _safe_ref_or_invalid(reference.get("decision_reference"))
    expected_ref = _safe_ref_or_invalid(
        reference.get("expected_decision_reference")
    )
    if decision_ref == "invalid_ref":
        blockers.append("operator_decision_reference_invalid")
    if expected_ref == "invalid_ref":
        blockers.append("operator_decision_reference_expected_invalid")
    if reference.get("decision_reference_present") is not True:
        blockers.append("operator_decision_reference_missing")
    if reference.get("decision_reference_validated") is not True:
        blockers.append("operator_decision_reference_not_validated")
    if reference.get("decision_reference_matches_expected") is not True:
        blockers.append("operator_decision_reference_mismatch")
    if (
        decision_ref != "invalid_ref"
        and expected_ref != "invalid_ref"
        and decision_ref != expected_ref
    ):
        blockers.append("operator_decision_reference_mismatch")
    for field in _REFERENCE_FALSE_FIELDS:
        if reference.get(field) is not False:
            blockers.append(f"operator_decision_reference_{field}_not_false")
    if reference.get("decision_must_be_recorded_separately") is not True:
        blockers.append(
            "operator_decision_reference_decision_must_be_recorded_separately_not_true"
        )

    verification = _mapping(report.get("bundle_verification"))
    if verification.get("verification_summary_ok") is not True:
        blockers.append("bundle_verification_summary_not_ok")
    if verification.get("verification_ok") is not True:
        blockers.append("bundle_verification_not_ok")
    if verification.get("identity_match") is not True:
        blockers.append("bundle_verification_identity_mismatch")
    if verification.get("verification_summary_version") != VERIFICATION_SUMMARY_VERSION:
        blockers.append("bundle_verification_summary_version_mismatch")
    if verification.get("bridge_template_version") != TEMPLATE_VERSION:
        blockers.append("bundle_verification_bridge_template_version_mismatch")

    blockers.extend(_operator_boundary_blockers(report))
    return sorted(set(blockers))


def _operator_boundary_blockers(report: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    boundary = _mapping(report.get("operator_boundary"))
    if not boundary:
        blockers.append("operator_boundary_missing")
    if boundary.get("validation_only") is not True:
        blockers.append("operator_boundary_validation_only_not_true")
    if boundary.get("manual_review_required") is not True:
        blockers.append("operator_boundary_manual_review_not_true")
    if _has_reported_items(boundary.get("boundary_blockers")):
        blockers.append("operator_boundary_blockers_present")
    for field in _AUTHORITY_FALSE_FIELDS:
        if boundary.get(field) is not False:
            blockers.append(f"operator_boundary_{field}_not_false")
    return sorted(set(blockers))


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
        if isinstance(value, str)
        and _SAFE_REF_RE.match(value)
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
        raise SafeInputError(f"{label}_unsafe")


def _safe_token(value: Any, fallback: str = "unsafe_marker_redacted") -> str:
    if (
        isinstance(value, str)
        and _SAFE_TOKEN_RE.match(value)
        and not _forbidden_output_markers(value)
    ):
        return value
    return fallback


def _safe_token_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw_values = value
    elif isinstance(value, (set, tuple)):
        raw_values = list(value)
    else:
        raw_values = [value]
    return sorted(set(_safe_token(item) for item in raw_values))


def _markdown_token_list(values: Any) -> list[str]:
    if not isinstance(values, list) or not values:
        return ["  - `none`"]
    return [f"  - `{_safe_token(value)}`" for value in values]


def _parse_utc(raw: str) -> datetime:
    if not raw.endswith("Z"):
        raise SafeInputError("now_utc_unsafe")
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as exc:
        raise SafeInputError("now_utc_unsafe") from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != timezone.utc.utcoffset(parsed)
    ):
        raise SafeInputError("now_utc_unsafe")
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


def _assert_no_forbidden_input(artifact_id: str, value: Mapping[str, Any]) -> None:
    if _forbidden_output_markers(json.dumps(value, sort_keys=True)):
        raise SafeInputError(f"{artifact_id}_forbidden_marker")


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
            "sanitized operator decision-reference review summary contains "
            "forbidden markers: " + ", ".join(found)
        )


if __name__ == "__main__":
    raise SystemExit(main())
