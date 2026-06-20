#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Render a path-free hex verifier-summary template index-entry verifier summary."""

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

from tools.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary import (  # noqa: E402
    SafeInputError,
    _as_nonnegative_int,
    _assert_no_forbidden_input,
    _assert_no_forbidden_output,
    _boundary_blockers,
    _check_status,
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
from tools.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry import (  # noqa: E402
    INDEX_ENTRY_VERSION,
    PROOF_ID as INDEX_ENTRY_PROOF_ID,
    SUMMARY_ARTIFACT_ID,
    TEMPLATE_ARTIFACT_ID,
)
from tools.verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry import (  # noqa: E402
    VERIFICATION_PROOF_ID,
    VERIFICATION_VERSION,
)


SUMMARY_VERSION = (
    "wd.hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_"
    "verification_summary_bridge_event_template_index_entry_verification_summary.v1"
)
PROOF_ID = (
    "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_"
    "verification_summary_bridge_event_template_index_entry_verification_summary_v1"
)
_REQUIRED_ARTIFACTS = (SUMMARY_ARTIFACT_ID, TEMPLATE_ARTIFACT_ID)


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
        help="Optional UTC timestamp override such as 2026-06-20T11:05:00Z.",
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
            summary = build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary(
                verification_report=verification_report,
                reviewer_agent_id=args.reviewer_agent,
                handoff_ref=args.handoff_ref,
                now_utc=_parse_utc(args.now) if args.now else None,
            )
        except SafeInputError as exc:
            summary = _failure_summary(exc.code)
        except ValueError:
            summary = _failure_summary(
                "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_"
                "verification_summary_bridge_event_template_index_entry_verification_summary_invalid"
            )

    encoded = json.dumps(summary, indent=2, sort_keys=True, allow_nan=False)
    if args.json:
        print(encoded)
    elif summary["ok"]:
        print(
            render_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary_markdown(
                summary
            )
        )
    else:
        print(
            "hex verifier-summary bridge-template index-entry verification summary "
            "FAILED: "
            + ", ".join(summary["blockers"]),
            file=sys.stderr,
        )
    return 0 if summary["ok"] else 1


def build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary(
    *,
    verification_report: Mapping[str, Any],
    reviewer_agent_id: str,
    handoff_ref: str,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return path-free reviewer context for the summary-template index verifier."""

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
        "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification": {
            "verification_ok": verification_ok,
            "proof_id": _safe_ref_or_invalid(verification_report.get("proof_id")),
            "verification_version": _safe_ref_or_invalid(
                verification_report.get("verification_version")
            ),
            "verified_proof_id": _safe_ref_or_invalid(
                verification_report.get("verified_proof_id")
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
            "claim_safe": verification_report.get("claim_safe") is True,
            "runtime_authority_granted": verification_report.get(
                "runtime_authority_granted"
            )
            is True,
            "runtime_subdivision_authority_granted": verification_report.get(
                "runtime_subdivision_authority_granted"
            )
            is True,
            "bridge_event_written": verification_report.get("bridge_event_written")
            is True,
            "fast_track_priority": verification_report.get("fast_track_priority")
            is True,
            "gate_skip_allowed": verification_report.get("gate_skip_allowed") is True,
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
            "merge_decision_made": False,
            "promotion_granted": False,
            "automatic_release_decision": False,
            "claim_safe": False,
            "literal_future_claim_safe": False,
            "direct_bridge_write_performed": False,
            "transport_added": False,
            "external_fetch_performed": False,
            "runtime_controls_added": False,
            "runtime_authority_granted": False,
            "runtime_subdivision_authority_granted": False,
            "bridge_event_written": False,
            "gate_skip_allowed": False,
            "fast_track_priority": False,
            "digest_payloads_included": False,
            "artifact_payloads_included": False,
            "local_paths_recorded": False,
        },
        "reviewer_next_actions": [
            "review_hex_cross_consistency_summary_template_index_entry_verification_summary",
            "compare_summary_template_index_entry_verification_to_local_artifacts",
            "append_bridge_event_separately_only_after_manual_review",
        ],
        "template_only": True,
        "manual_review_required": True,
        "approval_granted": False,
        "release_decision_made": False,
        "merge_decision_made": False,
        "promotion_granted": False,
        "automatic_release_decision": False,
        "claim_safe": False,
        "literal_future_claim_safe": False,
        "direct_bridge_write_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "runtime_controls_added": False,
        "runtime_authority_granted": False,
        "runtime_subdivision_authority_granted": False,
        "bridge_event_written": False,
        "gate_skip_allowed": False,
        "fast_track_priority": False,
        "digest_payloads_included": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": blockers,
        "warnings": report_warnings,
    }
    _assert_no_forbidden_output(json.dumps(summary, allow_nan=False, sort_keys=True))
    return summary


def render_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary_markdown(
    summary: Mapping[str, Any],
) -> str:
    verification = _mapping(
        summary.get(
            "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification"
        )
    )
    boundary = _mapping(summary.get("operator_boundary"))
    lines = [
        "# Hex Cross-Consistency Summary-Template Index-Entry Verification Summary",
        "",
        f"- Summary version: `{summary.get('summary_version')}`",
        f"- Created at UTC: `{summary.get('created_at_utc')}`",
        "- Manual review required: `true`",
        "- Automatic release decision: `false`",
        "- Approval granted: `false`",
        "- Release decision made: `false`",
        "- Merge decision made: `false`",
        "- Promotion granted: `false`",
        "- Claim safe: `false`",
        "- Direct bridge write performed: `false`",
        "- Transport added: `false`",
        "- External fetch performed: `false`",
        "- Runtime controls added: `false`",
        "- Runtime authority granted: `false`",
        "- Runtime subdivision authority granted: `false`",
        "- Bridge event written: `false`",
        "- Fast-track priority: `false`",
        "- Gate skip allowed: `false`",
        "- Digest payloads included: `false`",
        "- Artifact payloads included: `false`",
        "- Local paths recorded: `false`",
        "",
        "## Summary-Template Index-Entry Verification",
        "",
        f"- Verification OK: `{verification.get('verification_ok')}`",
        f"- Verification version: `{verification.get('verification_version')}`",
        f"- Verified proof id: `{verification.get('verified_proof_id')}`",
        f"- Index-entry version: `{verification.get('index_entry_version')}`",
        f"- Artifact count checked: `{verification.get('artifact_count_checked')}`",
        f"- Source contract check: `{verification.get('source_contract_check')}`",
        "- Rebuilt index-entry check: "
        f"`{verification.get('rebuilt_index_entry_check')}`",
        "- Bridge event schema check: "
        f"`{verification.get('bridge_event_schema_check')}`",
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
            "include payloads, record local paths, fast-track priority, skip gates, "
            "upgrade claims, or grant runtime subdivision authority.",
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
        "merge_decision_made": False,
        "promotion_granted": False,
        "automatic_release_decision": False,
        "claim_safe": False,
        "literal_future_claim_safe": False,
        "direct_bridge_write_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "runtime_controls_added": False,
        "runtime_authority_granted": False,
        "runtime_subdivision_authority_granted": False,
        "bridge_event_written": False,
        "gate_skip_allowed": False,
        "fast_track_priority": False,
        "digest_payloads_included": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": [
            "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_"
            "verification_summary_bridge_event_template_index_entry_"
            "verification_summary_failed:"
            f"{_safe_token(reason)}"
        ],
        "warnings": [],
    }


def _verification_report_contract_blockers(report: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    if report.get("ok") is not True:
        blockers.append("verification_report_not_ok")
    if report.get("proof_id") != VERIFICATION_PROOF_ID:
        blockers.append("verification_report_proof_id_mismatch")
    if report.get("verification_version") != VERIFICATION_VERSION:
        blockers.append("verification_report_verification_version_mismatch")
    if report.get("verified_proof_id") != INDEX_ENTRY_PROOF_ID:
        blockers.append("verification_report_verified_proof_id_mismatch")
    if report.get("index_entry_version") != INDEX_ENTRY_VERSION:
        blockers.append("verification_report_index_entry_version_mismatch")
    if report.get("artifact_count_checked") != len(_REQUIRED_ARTIFACTS):
        blockers.append("verification_report_artifact_count_checked_mismatch")
    for field in (
        "source_contract_check",
        "rebuilt_index_entry_check",
        "bridge_event_schema_check",
    ):
        if report.get(field) != "match":
            blockers.append(f"verification_report_{field}_not_match")
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
