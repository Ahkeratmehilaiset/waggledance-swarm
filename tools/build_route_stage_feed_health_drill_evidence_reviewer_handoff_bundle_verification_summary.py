#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Render a path-free route-stage reviewer handoff bundle verification summary."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index import (  # noqa: E402
    BUNDLE_INDEX_VERSION,
    FINAL_VERIFICATION_ARTIFACT_ID,
    REVIEWER_HANDOFF_SUMMARY_ARTIFACT_ID,
)
from tools.build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary import (  # noqa: E402
    AUTHORITY_FALSE_FIELDS,
    SafeInputError,
    _as_nonnegative_int,
    _assert_no_forbidden_input,
    _assert_no_forbidden_output,
    _boundary_blockers,
    _check_status,
    _forbidden_output_markers,
    _load_json_report,
    _mapping,
    _markdown_token_list,
    _parse_utc,
    _recursive_boundary_blockers,
    _safe_ref_or_invalid,
    _safe_token,
    _safe_token_list,
    _token_list_schema_blockers,
    _utc_iso,
    _validate_safe_ref,
)
from tools.verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index import (  # noqa: E402
    VERIFICATION_VERSION,
)


SUMMARY_VERSION = (
    "waggledance.route_stage_feed_health_drill_evidence_reviewer_"
    "handoff_bundle_verification_summary.v1"
)
PROOF_ID = (
    "route_stage_feed_health_drill_evidence_reviewer_"
    "handoff_bundle_verification_summary_v1"
)
ROUTE_STAGE_BUNDLE_VERIFICATION_KEY = (
    "route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification"
)
_REQUIRED_ARTIFACTS = (
    FINAL_VERIFICATION_ARTIFACT_ID,
    REVIEWER_HANDOFF_SUMMARY_ARTIFACT_ID,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bundle-verification-json",
        "--verification-json",
        dest="bundle_verification_json",
        required=True,
        type=Path,
    )
    parser.add_argument("--reviewer-agent", required=True)
    parser.add_argument("--handoff-ref", required=True)
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override such as 2026-06-19T07:00:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        verification_report = _load_json_report(args.bundle_verification_json)
    except SafeInputError as exc:
        summary = _failure_summary(exc.code)
    else:
        try:
            summary = build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary(
                verification_report=verification_report,
                reviewer_agent_id=args.reviewer_agent,
                handoff_ref=args.handoff_ref,
                now_utc=_parse_utc(args.now) if args.now else None,
            )
        except SafeInputError as exc:
            summary = _failure_summary(exc.code)
        except ValueError:
            summary = _failure_summary(
                "route_stage_feed_health_drill_evidence_reviewer_"
                "handoff_bundle_verification_summary_invalid"
            )

    encoded = json.dumps(summary, indent=2, sort_keys=True, allow_nan=False)
    if args.json:
        print(encoded)
    elif summary["ok"]:
        print(
            render_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_markdown(
                summary
            )
        )
    else:
        print(
            "route-stage feed-health reviewer handoff bundle verification "
            "summary FAILED: "
            + ", ".join(summary["blockers"]),
            file=sys.stderr,
        )
    return 0 if summary["ok"] else 1


def build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary(
    *,
    verification_report: Mapping[str, Any],
    reviewer_agent_id: str,
    handoff_ref: str,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return path-free reviewer context for a local handoff-bundle verifier."""

    if not isinstance(verification_report, Mapping):
        raise ValueError("verification_report_not_mapping")
    _assert_no_forbidden_input("verification_report", verification_report)
    _validate_safe_ref("reviewer_agent_id", reviewer_agent_id)
    _validate_safe_ref("handoff_ref", handoff_ref)

    report_blockers = _safe_token_list(verification_report.get("blockers"))
    report_warnings = _safe_token_list(verification_report.get("warnings"))
    boundary_blockers = _boundary_blockers(verification_report)
    boundary_blockers.extend(_recursive_boundary_blockers(verification_report))
    contract_blockers = _verification_report_contract_blockers(verification_report)
    contract_blockers.extend(_token_list_schema_blockers(verification_report, "blockers"))
    contract_blockers.extend(_token_list_schema_blockers(verification_report, "warnings"))
    blockers = sorted(set(report_blockers + boundary_blockers + contract_blockers))
    verification_ok = (
        verification_report.get("ok") is True
        and verification_report.get("verification_version") == VERIFICATION_VERSION
    )

    summary = {
        "proof_id": PROOF_ID,
        "ok": verification_ok and not blockers,
        "summary_version": SUMMARY_VERSION,
        "created_at_utc": _utc_iso(now_utc or datetime.now(timezone.utc)),
        "reviewer_ownership": {
            "reviewer_agent_id": reviewer_agent_id,
            "handoff_ref": handoff_ref,
            "manual_review_required": True,
            "approval_granted": False,
            "release_decision_made": False,
            "automatic_release_decision": False,
        },
        ROUTE_STAGE_BUNDLE_VERIFICATION_KEY: {
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
            "source_contract_check": _check_status(
                verification_report.get("source_contract_check")
            ),
            "rebuilt_bundle_index_check": _check_status(
                verification_report.get("rebuilt_bundle_index_check")
            ),
            "reviewer_handoff_summary_check": _check_status(
                verification_report.get("reviewer_handoff_summary_check")
            ),
            "template_only": verification_report.get("template_only") is True,
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
            "controls_present": False,
            "runtime_authority_granted": False,
            "external_writes_applied": False,
            "network_access_performed": False,
            "artifact_payloads_included": False,
            "local_paths_recorded": False,
        },
        "reviewer_next_actions": [
            "review_route_stage_feed_health_reviewer_handoff_bundle_verification_summary",
            "compare_bundle_verification_summary_to_local_artifacts",
            "append_bridge_event_separately_only_after_manual_review",
        ],
        "template_only": True,
        "manual_review_required": True,
        "approval_granted": False,
        "release_decision_made": False,
        "automatic_release_decision": False,
        "direct_bridge_write_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "runtime_controls_added": False,
        "controls_present": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
        "network_access_performed": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": blockers,
        "warnings": report_warnings,
    }
    _assert_no_forbidden_output(json.dumps(summary, allow_nan=False, sort_keys=True))
    return summary


def render_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_markdown(
    summary: Mapping[str, Any],
) -> str:
    verification = _mapping(summary.get(ROUTE_STAGE_BUNDLE_VERIFICATION_KEY))
    boundary = _mapping(summary.get("operator_boundary"))
    lines = [
        "# Route-Stage Feed-Health Handoff Bundle Verification Summary",
        "",
        f"- Summary version: `{summary.get('summary_version')}`",
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
        "## Handoff Bundle Verification",
        "",
        f"- Verification OK: `{verification.get('verification_ok')}`",
        f"- Verification version: `{verification.get('verification_version')}`",
        f"- Bundle index version: `{verification.get('bundle_index_version')}`",
        f"- Artifact count checked: `{verification.get('artifact_count_checked')}`",
        f"- Source contract check: `{verification.get('source_contract_check')}`",
        "- Rebuilt bundle-index check: "
        f"`{verification.get('rebuilt_bundle_index_check')}`",
        "- Reviewer handoff summary check: "
        f"`{verification.get('reviewer_handoff_summary_check')}`",
        f"- Template only: `{verification.get('template_only')}`",
        "- Verification blockers:",
    ]
    lines.extend(_markdown_token_list(verification.get("blockers")))
    lines.append("- Verification warnings:")
    lines.extend(_markdown_token_list(verification.get("warnings")))
    lines.extend(
        [
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
            "authority.",
            "",
        ]
    )
    markdown = "\n".join(lines)
    _assert_no_forbidden_output(markdown)
    return markdown


def _failure_summary(reason: str) -> dict[str, Any]:
    return {
        "proof_id": PROOF_ID,
        "ok": False,
        "summary_version": SUMMARY_VERSION,
        "template_only": True,
        "manual_review_required": True,
        "approval_granted": False,
        "release_decision_made": False,
        "automatic_release_decision": False,
        "direct_bridge_write_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "runtime_controls_added": False,
        "controls_present": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
        "network_access_performed": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": [
            "route_stage_feed_health_drill_evidence_reviewer_"
            "handoff_bundle_verification_summary_failed:"
            f"{_safe_token(reason)}"
        ],
        "warnings": [],
    }


def _verification_report_contract_blockers(report: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    if report.get("ok") is not True:
        blockers.append("verification_report_not_ok")
    if report.get("verification_version") != VERIFICATION_VERSION:
        blockers.append("verification_report_verification_version_mismatch")
    if report.get("bundle_index_version") != BUNDLE_INDEX_VERSION:
        blockers.append("verification_report_bundle_index_version_mismatch")
    if report.get("artifact_count_checked") != len(_REQUIRED_ARTIFACTS):
        blockers.append("verification_report_artifact_count_checked_mismatch")
    if report.get("source_contract_check") != "match":
        blockers.append("verification_report_source_contract_check_not_match")
    if report.get("rebuilt_bundle_index_check") != "match":
        blockers.append("verification_report_rebuilt_bundle_index_check_not_match")
    if report.get("reviewer_handoff_summary_check") != "match":
        blockers.append("verification_report_reviewer_handoff_summary_check_not_match")
    for check_name in ("digest_checks", "size_checks", "schema_version_checks"):
        checks = _check_statuses(_mapping(report.get(check_name)))
        for artifact_id, status in checks.items():
            if status != "match":
                blockers.append(
                    f"verification_check_not_match:{check_name}:{artifact_id}"
                )
    return sorted(set(blockers))


def _check_statuses(raw: Mapping[str, Any]) -> dict[str, str]:
    return {
        artifact_id: _check_status(raw.get(artifact_id))
        for artifact_id in _REQUIRED_ARTIFACTS
    }


if __name__ == "__main__":
    raise SystemExit(main())
