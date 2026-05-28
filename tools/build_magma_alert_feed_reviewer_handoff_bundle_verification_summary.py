# SPDX-License-Identifier: BUSL-1.1
"""Render a sanitized MAGMA reviewer handoff bundle verification summary."""
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

from tools.package_magma_alert_feed_release_evidence import (  # noqa: E402
    FORBIDDEN_OUTPUT_MARKERS,
)
from tools.build_magma_alert_feed_reviewer_handoff_bundle_index import (  # noqa: E402
    BUNDLE_INDEX_VERSION,
)
from tools.verify_magma_alert_feed_reviewer_handoff_bundle_index import (  # noqa: E402
    VERIFICATION_VERSION,
)


SUMMARY_VERSION = "magma_alert_feed_reviewer_handoff_bundle_verification_summary.v1"

_ARTIFACT_IDS = (
    "release_evidence_package",
    "validator_report",
    "reviewer_handoff_summary",
    "bridge_event_template",
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
_CHECK_STATUS = frozenset({
    "match",
    "mismatch",
    "missing_index_record",
    "not_checked",
    "unknown",
})
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$")
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,191}$")


class SafeInputError(ValueError):
    """Raised when an operator-supplied reference is unsafe to echo."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verification-json", required=True, type=Path)
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
        verification_report = json.loads(
            args.verification_json.read_text(encoding="utf-8")
        )
    except OSError:
        summary = _failure_summary("verification_report_unreadable")
    except json.JSONDecodeError:
        summary = _failure_summary("verification_report_json_error")
    else:
        try:
            summary = (
                build_magma_alert_feed_reviewer_handoff_bundle_verification_summary(
                    verification_report=verification_report,
                    reviewer_agent_id=args.reviewer_agent,
                    handoff_ref=args.handoff_ref,
                    now_utc=_parse_utc(args.now) if args.now else None,
                )
            )
        except SafeInputError as exc:
            summary = _failure_summary(exc.code)
        except ValueError:
            summary = _failure_summary("verification_summary_invalid")

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    elif summary["ok"]:
        print(render_bundle_verification_summary_markdown(summary))
    else:
        print(
            "MAGMA reviewer handoff bundle verification summary FAILED: "
            + ", ".join(summary["blockers"]),
            file=sys.stderr,
        )
    return 0 if summary["ok"] else 1


def build_magma_alert_feed_reviewer_handoff_bundle_verification_summary(
    *,
    verification_report: Mapping[str, Any],
    reviewer_agent_id: str,
    handoff_ref: str,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return path-free reviewer context for a local bundle verification."""

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
            "automatic_release_decision": False,
            "approval_granted": False,
        },
        "bundle_verification": {
            "verification_ok": verification_ok,
            "verification_version": _safe_ref_or_invalid(
                verification_report.get("verification_version")
            ),
            "bundle_index_version": _safe_ref_or_invalid(
                verification_report.get("bundle_index_version")
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
            "blocker_count": len(report_blockers),
            "blockers": report_blockers,
            "warning_count": len(report_warnings),
            "warnings": report_warnings,
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
            "review_bundle_verification_blockers",
            "compare_bundle_summary_to_local_evidence",
            "record_operator_decision_separately",
        ],
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
        "blockers": blockers,
        "warnings": report_warnings,
    }
    _assert_no_forbidden_output(json.dumps(summary, sort_keys=True))
    return summary


def render_bundle_verification_summary_markdown(summary: Mapping[str, Any]) -> str:
    verification = _mapping(summary.get("bundle_verification"))
    boundary = _mapping(summary.get("operator_boundary"))
    lines = [
        "# MAGMA Reviewer Handoff Bundle Verification Summary",
        "",
        f"- Summary version: `{summary.get('summary_version')}`",
        f"- Release ref: `{summary.get('release_ref')}`",
        f"- Commit SHA: `{summary.get('commit_sha')}`",
        f"- CI run ref: `{summary.get('ci_run_ref')}`",
        f"- Created at UTC: `{summary.get('created_at_utc')}`",
        f"- Bundle verification OK: `{verification.get('verification_ok')}`",
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
        "## Verification",
        "",
        f"- Verification version: `{verification.get('verification_version')}`",
        f"- Bundle index version: `{verification.get('bundle_index_version')}`",
        f"- Artifact count checked: `{verification.get('artifact_count_checked')}`",
        "- Blockers:",
    ]
    lines.extend(_markdown_token_list(verification.get("blockers")))
    lines.append("- Warnings:")
    lines.extend(_markdown_token_list(verification.get("warnings")))
    lines.extend([
        "",
        "## Operator Boundary",
        "",
        "- Verification report boundary OK: "
        f"`{boundary.get('verification_report_boundary_ok')}`",
        "- Boundary blockers:",
    ])
    lines.extend(_markdown_token_list(boundary.get("boundary_blockers")))
    lines.extend([
        "",
        "This summary is reviewer context only. It does not approve, merge, "
        "promote, append bridge events, transport artifacts, fetch endpoints, "
        "include payloads, record local paths, control feeds, or grant runtime "
        "authority.",
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
        "blockers": [f"handoff_bundle_verification_summary_failed:{reason}"],
        "warnings": [],
    }


def _boundary_blockers(report: Mapping[str, Any]) -> list[str]:
    blockers = []
    for field in _AUTHORITY_FALSE_FIELDS:
        if report.get(field) is not False:
            blockers.append(f"verification_report_{field}_not_false")
    return sorted(blockers)


def _verification_report_contract_blockers(report: Mapping[str, Any]) -> list[str]:
    blockers = []
    if _safe_ref_or_invalid(report.get("release_ref")) == "invalid_ref":
        blockers.append("verification_report_release_ref_invalid")
    if _commit_or_invalid(report.get("commit_sha")) == "invalid_commit":
        blockers.append("verification_report_commit_sha_invalid")
    if _safe_ref_or_invalid(report.get("ci_run_ref")) == "invalid_ref":
        blockers.append("verification_report_ci_run_ref_invalid")
    if report.get("verification_version") != VERIFICATION_VERSION:
        blockers.append("verification_report_verification_version_mismatch")
    if report.get("bundle_index_version") != BUNDLE_INDEX_VERSION:
        blockers.append("verification_report_bundle_index_version_mismatch")
    for check_name in ("digest_checks", "size_checks", "schema_version_checks"):
        checks = _check_statuses(_mapping(report.get(check_name)))
        for artifact_id, status in checks.items():
            if status != "match":
                blockers.append(
                    f"verification_check_not_match:{check_name}:{artifact_id}"
                )
    return sorted(blockers)


def _check_statuses(raw: Mapping[str, Any]) -> dict[str, str]:
    checks: dict[str, str] = {}
    for artifact_id in _ARTIFACT_IDS:
        status = raw.get(artifact_id)
        checks[artifact_id] = status if status in _CHECK_STATUS else "unknown"
    return checks


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _safe_ref_or_invalid(value: Any) -> str:
    return (
        value
        if isinstance(value, str) and _SAFE_REF_RE.match(value)
        else "invalid_ref"
    )


def _commit_or_invalid(value: Any) -> str:
    return (
        value
        if isinstance(value, str) and _COMMIT_RE.match(value)
        else "invalid_commit"
    )


def _validate_safe_ref(label: str, value: str) -> None:
    if not isinstance(value, str) or not _SAFE_REF_RE.match(value):
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
            "sanitized bundle verification summary contains forbidden markers: "
            + ", ".join(found)
        )


if __name__ == "__main__":
    raise SystemExit(main())
