# SPDX-License-Identifier: BUSL-1.1
"""Render a sanitized MAGMA verification template index-entry summary."""
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

from tools.build_magma_decision_review_verification_template_index_entry import (  # noqa: E402
    INDEX_ENTRY_VERSION,
)
from tools.package_magma_alert_feed_release_evidence import (  # noqa: E402
    FORBIDDEN_OUTPUT_MARKERS,
)
from tools.verify_magma_decision_review_verification_template_index_entry import (  # noqa: E402
    VERIFICATION_VERSION,
)


SUMMARY_VERSION = "magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary.v1"

_ARTIFACT_IDS = (
    "operator_decision_reference_review_bundle_verification_summary",
    "operator_decision_reference_review_bundle_verification_bridge_event_template",
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
_CHECK_STATUS = frozenset(
    {
        "match",
        "mismatch",
        "missing_index_record",
        "not_checked",
        "failed",
        "unknown",
    }
)
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,191}$")
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,191}$")


class SafeInputError(ValueError):
    """Raised when operator-provided refs are unsafe to echo."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--index-entry-verification-json",
        "--verification-json",
        dest="index_entry_verification_json",
        required=True,
        type=Path,
    )
    parser.add_argument("--reviewer-agent", required=True)
    parser.add_argument("--handoff-ref", required=True)
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
        verification_report = _load_json_report(args.index_entry_verification_json)
    except SafeInputError as exc:
        summary = _failure_summary(exc.code)
    else:
        try:
            summary = build_magma_decision_review_verification_template_index_entry_summary(
                verification_report=verification_report,
                reviewer_agent_id=args.reviewer_agent,
                handoff_ref=args.handoff_ref,
                now_utc=_parse_utc(args.now) if args.now else None,
            )
        except SafeInputError as exc:
            summary = _failure_summary(exc.code)
        except ValueError:
            summary = _failure_summary(
                "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_invalid"
            )

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    elif summary["ok"]:
        print(render_magma_decision_review_verification_template_index_entry_summary_markdown(summary))
    else:
        print(
            "MAGMA operator decision-reference review bundle verification "
            "bridge-event template index-entry verification summary FAILED: "
            + ", ".join(summary["blockers"]),
            file=sys.stderr,
        )
    return 0 if summary["ok"] else 1


def build_magma_decision_review_verification_template_index_entry_summary(
    *,
    verification_report: Mapping[str, Any],
    reviewer_agent_id: str,
    handoff_ref: str,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return path-free reviewer context for a local index-entry verification."""

    if not isinstance(verification_report, Mapping):
        raise ValueError("verification_report_not_mapping")
    _assert_no_forbidden_input("verification_report", verification_report)
    _validate_safe_ref("reviewer_agent_id", reviewer_agent_id)
    _validate_safe_ref("handoff_ref", handoff_ref)

    report_blockers = _safe_token_list(verification_report.get("blockers"))
    report_warnings = _safe_token_list(verification_report.get("warnings"))
    boundary_blockers = _boundary_blockers(verification_report)
    contract_blockers = _verification_report_contract_blockers(verification_report)
    blockers = sorted(set(report_blockers + boundary_blockers + contract_blockers))
    verification_ok = (
        verification_report.get("ok") is True
        and verification_report.get("verification_version") == VERIFICATION_VERSION
    )
    reference = _mapping(verification_report.get("operator_decision_reference_review"))
    decision_reference = _safe_ref_or_invalid(reference.get("decision_reference"))
    expected_decision_reference = _safe_ref_or_invalid(
        reference.get("expected_decision_reference")
    )
    reference_verified = (
        reference.get("decision_reference_verified") is True
        and decision_reference != "invalid_ref"
        and decision_reference == expected_decision_reference
        and not blockers
    )
    summary = {
        "ok": verification_ok and not blockers,
        "summary_version": SUMMARY_VERSION,
        "created_at_utc": _utc_iso(now_utc or datetime.now(timezone.utc)),
        "release_ref": _safe_ref_or_invalid(verification_report.get("release_ref")),
        "commit_sha": _commit_or_invalid(verification_report.get("commit_sha")),
        "ci_run_ref": _safe_ref_or_invalid(verification_report.get("ci_run_ref")),
        "reviewer_ownership": {
            "reviewer_agent_id": reviewer_agent_id,
            "handoff_ref": handoff_ref,
            "manual_review_required": True,
            "approval_granted": False,
            "release_decision_made": False,
            "automatic_release_decision": False,
        },
        "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification": {
            "verification_ok": verification_ok,
            "verification_version": _safe_ref_or_invalid(
                verification_report.get("verification_version")
            ),
            "index_entry_version": _safe_ref_or_invalid(
                verification_report.get("index_entry_version")
            ),
            "artifact_count_checked": _as_nonnegative_int(
                verification_report.get("artifact_count_checked")
            ),
            "digest_checks": _check_statuses(
                _mapping(verification_report.get("digest_checks"))
            ),
            "size_checks": _check_statuses(
                _mapping(verification_report.get("size_checks"))
            ),
            "schema_version_checks": _check_statuses(
                _mapping(verification_report.get("schema_version_checks"))
            ),
            "source_contract_check": _check_status(
                verification_report.get("source_contract_check")
            ),
            "rebuilt_index_entry_check": _check_status(
                verification_report.get("rebuilt_index_entry_check")
            ),
            "bridge_event_schema_check": _check_status(
                verification_report.get("bridge_event_schema_check")
            ),
            "template_only": verification_report.get("template_only") is True,
            "blocker_count": len(report_blockers),
            "blockers": report_blockers,
            "warning_count": len(report_warnings),
            "warnings": report_warnings,
        },
        "operator_decision_reference_review": {
            "decision_reference": decision_reference,
            "expected_decision_reference": expected_decision_reference,
            "decision_reference_verified": reference_verified,
            "decision_reference_is_approval": False,
            "decision_reference_is_release_decision": False,
            "decision_must_be_recorded_separately": True,
            "review_context_only": True,
            "manual_review_required": True,
        },
        "operator_boundary": {
            "verification_report_boundary_ok": not (
                boundary_blockers or contract_blockers
            ),
            "boundary_blockers": sorted(set(boundary_blockers + contract_blockers)),
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
            "review_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification",
            "compare_index_entry_verification_to_local_artifacts",
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


def render_magma_decision_review_verification_template_index_entry_summary_markdown(
    summary: Mapping[str, Any],
) -> str:
    verification = _mapping(
        summary.get(
            "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification"
        )
    )
    reference = _mapping(summary.get("operator_decision_reference_review"))
    boundary = _mapping(summary.get("operator_boundary"))
    lines = [
        "# MAGMA Operator Decision-Reference Template Index-Entry Verification Summary",
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
        "## Index-Entry Verification",
        "",
        f"- Verification OK: `{verification.get('verification_ok')}`",
        f"- Verification version: `{verification.get('verification_version')}`",
        f"- Index-entry version: `{verification.get('index_entry_version')}`",
        f"- Artifact count checked: `{verification.get('artifact_count_checked')}`",
        f"- Source contract check: `{verification.get('source_contract_check')}`",
        f"- Rebuilt index-entry check: `{verification.get('rebuilt_index_entry_check')}`",
        f"- Bridge event schema check: `{verification.get('bridge_event_schema_check')}`",
        f"- Template only: `{verification.get('template_only')}`",
        "- Verification blockers:",
    ]
    lines.extend(_markdown_token_list(verification.get("blockers")))
    lines.append("- Verification warnings:")
    lines.extend(_markdown_token_list(verification.get("warnings")))
    lines.extend(
        [
            "",
            "## Operator Decision Reference",
            "",
            f"- Decision reference: `{reference.get('decision_reference')}`",
            "- Expected decision reference: "
            f"`{reference.get('expected_decision_reference')}`",
            "- Decision reference verified: "
            f"`{reference.get('decision_reference_verified')}`",
            "- Decision reference is approval: `false`",
            "- Decision reference is release decision: `false`",
            "- Decision must be recorded separately: `true`",
            "",
            "## Operator Boundary",
            "",
            "- Verification report boundary OK: "
            f"`{boundary.get('verification_report_boundary_ok')}`",
            "- Boundary blockers:",
        ]
    )
    lines.extend(_markdown_token_list(boundary.get("boundary_blockers")))
    lines.extend(
        [
            "",
            "This summary is reviewer context only. It does not approve, merge, "
            "promote, append bridge events, transport artifacts, fetch endpoints, "
            "include payloads, record local paths, control feeds, or grant runtime "
            "authority. The operator decision must be recorded separately.",
            "",
        ]
    )
    markdown = "\n".join(lines)
    _assert_no_forbidden_output(markdown)
    return markdown


def _load_json_report(path: Path) -> Mapping[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SafeInputError("verification_report_unreadable") from exc

    def reject_constant(_value: str) -> None:
        raise ValueError("non_finite_json_constant")

    try:
        parsed = json.loads(raw.decode("utf-8"), parse_constant=reject_constant)
    except UnicodeDecodeError as exc:
        raise SafeInputError("verification_report_decode_error") from exc
    except json.JSONDecodeError as exc:
        raise SafeInputError("verification_report_json_error") from exc
    except ValueError as exc:
        raise SafeInputError("verification_report_json_error") from exc
    if not isinstance(parsed, Mapping):
        raise SafeInputError("verification_report_not_mapping")
    return parsed


def _failure_summary(reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "summary_version": SUMMARY_VERSION,
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
            "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_failed:"
            f"{reason}"
        ],
        "warnings": [],
    }


def _boundary_blockers(report: Mapping[str, Any]) -> list[str]:
    blockers = []
    if report.get("manual_review_required") is not True:
        blockers.append("verification_report_manual_review_required_not_true")
    for field in _AUTHORITY_FALSE_FIELDS:
        if report.get(field) is not False:
            blockers.append(f"verification_report_{field}_not_false")
    if report.get("template_only") is not True:
        blockers.append("verification_report_template_only_not_true")
    return sorted(blockers)


def _verification_report_contract_blockers(report: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    if report.get("ok") is not True:
        blockers.append("verification_report_not_ok")
    if _safe_ref_or_invalid(report.get("release_ref")) == "invalid_ref":
        blockers.append("verification_report_release_ref_invalid")
    if _commit_or_invalid(report.get("commit_sha")) == "invalid_commit":
        blockers.append("verification_report_commit_sha_invalid")
    if _safe_ref_or_invalid(report.get("ci_run_ref")) == "invalid_ref":
        blockers.append("verification_report_ci_run_ref_invalid")
    if report.get("verification_version") != VERIFICATION_VERSION:
        blockers.append("verification_report_verification_version_mismatch")
    if report.get("index_entry_version") != INDEX_ENTRY_VERSION:
        blockers.append("verification_report_index_entry_version_mismatch")
    if report.get("source_contract_check") != "match":
        blockers.append("verification_report_source_contract_check_not_match")
    if report.get("rebuilt_index_entry_check") != "match":
        blockers.append("verification_report_rebuilt_index_entry_check_not_match")
    if report.get("bridge_event_schema_check") != "match":
        blockers.append("verification_report_bridge_event_schema_check_not_match")
    if report.get("artifact_count_checked") != len(_ARTIFACT_IDS):
        blockers.append("verification_report_artifact_count_checked_mismatch")
    for check_name in ("digest_checks", "size_checks", "schema_version_checks"):
        checks = _check_statuses(_mapping(report.get(check_name)))
        for artifact_id, status in checks.items():
            if status != "match":
                blockers.append(
                    f"verification_check_not_match:{check_name}:{artifact_id}"
                )
    blockers.extend(_operator_decision_reference_blockers(report))
    return sorted(set(blockers))


def _operator_decision_reference_blockers(report: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    reference = _mapping(report.get("operator_decision_reference_review"))
    if not reference:
        blockers.append("operator_decision_reference_missing")
    decision_ref = _safe_ref_or_invalid(reference.get("decision_reference"))
    expected_ref = _safe_ref_or_invalid(reference.get("expected_decision_reference"))
    if decision_ref == "invalid_ref":
        blockers.append("operator_decision_reference_invalid")
    if expected_ref == "invalid_ref":
        blockers.append("operator_decision_reference_expected_invalid")
    if (
        decision_ref != "invalid_ref"
        and expected_ref != "invalid_ref"
        and decision_ref != expected_ref
    ):
        blockers.append("operator_decision_reference_mismatch")
    if reference.get("decision_reference_verified") is not True:
        blockers.append("operator_decision_reference_not_verified")
    for field in _REFERENCE_FALSE_FIELDS:
        if reference.get(field) is not False:
            blockers.append(f"operator_decision_reference_{field}_not_false")
    if reference.get("decision_must_be_recorded_separately") is not True:
        blockers.append(
            "operator_decision_reference_decision_must_be_recorded_separately_not_true"
        )
    if reference.get("review_context_only") is not True:
        blockers.append("operator_decision_reference_review_context_only_not_true")
    if reference.get("manual_review_required") is not True:
        blockers.append("operator_decision_reference_manual_review_required_not_true")
    return sorted(set(blockers))


def _check_statuses(raw: Mapping[str, Any]) -> dict[str, str]:
    return {
        artifact_id: _check_status(raw.get(artifact_id))
        for artifact_id in _ARTIFACT_IDS
    }


def _check_status(value: Any) -> str:
    return value if value in _CHECK_STATUS else "unknown"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


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
    tokens = [_safe_token(item) for item in raw_values]
    return sorted(set(tokens))


def _markdown_token_list(values: Any) -> list[str]:
    if not isinstance(values, list) or not values:
        return ["  - `none`"]
    return [f"  - `{_safe_token(value)}`" for value in values]


def _as_nonnegative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int) and value >= 0:
        return value
    return 0


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
    try:
        serialized = json.dumps(value, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise SafeInputError(f"{artifact_id}_non_finite_json_value") from exc
    if _forbidden_output_markers(serialized):
        raise SafeInputError(f"{artifact_id}_forbidden_marker")


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
            "sanitized operator decision-reference review bundle verification "
            "bridge-event template index-entry verification summary contains "
            "forbidden markers"
        )


if __name__ == "__main__":
    raise SystemExit(main())
