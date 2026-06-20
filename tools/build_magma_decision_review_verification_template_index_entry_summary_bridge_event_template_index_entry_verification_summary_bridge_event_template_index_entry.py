# SPDX-License-Identifier: BUSL-1.1
"""Build a local MAGMA verifier-summary bridge-template index entry."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry import (  # noqa: E402
    INDEX_ENTRY_VERSION as SOURCE_INDEX_ENTRY_VERSION,
    SummaryBridgeTemplateIndexEntryError,
    _artifact_record,
    _assert_bytes_match_artifact,
    _assert_mapping,
    _assert_no_forbidden_input,
    _assert_no_forbidden_output,
    _commit_or_invalid,
    _deterministic_artifact,
    _expect_authority_false,
    _expect_empty_items,
    _identity,
    _load_json_artifact,
    _mapping,
    _optional_safe_ref,
    _parse_utc,
    _required_safe_ref,
    _required_severity,
    _required_targets,
    _safe_ref_or_invalid,
    _safe_token_list,
    _sha256_hex,
    _utc_iso,
    _assert_reference_contract,
)
from tools.build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary import (  # noqa: E402
    SUMMARY_VERSION as SOURCE_SUMMARY_VERSION,
)
from tools.build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template import (  # noqa: E402
    TEMPLATE_VERSION as SOURCE_TEMPLATE_VERSION,
    ContractError as SummaryBridgeTemplateContractError,
    SafeInputError as SummaryBridgeTemplateSafeInputError,
    build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template,
)
from tools.verify_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry import (  # noqa: E402
    VERIFICATION_VERSION as SOURCE_VERIFICATION_VERSION,
)
from waggledance.core.bridge_event_schema import validate_event  # noqa: E402


INDEX_ENTRY_VERSION = "magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry.v1"

SUMMARY_ARTIFACT_ID = (
    "operator_decision_reference_review_bundle_verification_bridge_event_template_"
    "index_entry_verification_summary_bridge_event_template_index_entry_"
    "verification_summary"
)
TEMPLATE_ARTIFACT_ID = (
    "operator_decision_reference_review_bundle_verification_bridge_event_template_"
    "index_entry_verification_summary_bridge_event_template_index_entry_"
    "verification_summary_bridge_event_template"
)
_ARTIFACT_ORDER = (SUMMARY_ARTIFACT_ID, TEMPLATE_ARTIFACT_ID)
_SOURCE_VERIFICATION_ARTIFACT_IDS = (
    "operator_decision_reference_review_bundle_verification_bridge_event_template_"
    "index_entry_verification_summary",
    "operator_decision_reference_review_bundle_verification_bridge_event_template_"
    "index_entry_verification_summary_bridge_event_template",
)
_VERIFICATION_KEY = (
    "operator_decision_reference_review_bundle_verification_bridge_event_template_"
    "index_entry_verification_summary_bridge_event_template_index_entry_"
    "verification"
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
_BRIDGE_TEMPLATE_EVENT_STATUS = (
    "decision_reference_review_verifier_summary_bridge_event_template_ready"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summary-bridge-template-index-entry-verification-summary-json",
        "--index-entry-verification-summary-json",
        "--verification-summary-json",
        "--summary-json",
        dest="index_entry_verification_summary_json",
        required=True,
        type=Path,
    )
    parser.add_argument(
        "--summary-bridge-event-template-json",
        "--bridge-event-template-json",
        "--template-json",
        dest="summary_bridge_event_template_json",
        required=True,
        type=Path,
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override such as 2026-05-29T09:05:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary_bytes, summary = _load_json_artifact(
            args.index_entry_verification_summary_json,
            SUMMARY_ARTIFACT_ID,
        )
        template_bytes, template_report = _load_json_artifact(
            args.summary_bridge_event_template_json,
            TEMPLATE_ARTIFACT_ID,
        )
        report = build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
            index_entry_verification_summary=summary,
            summary_bridge_event_template_report=template_report,
            index_entry_verification_summary_bytes=summary_bytes,
            summary_bridge_event_template_bytes=template_bytes,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except SummaryBridgeTemplateIndexEntryError as exc:
        report = _failure_report(exc.code)
    except ValueError:
        report = _failure_report(
            "operator_decision_reference_review_verifier_summary_bridge_event_template_index_entry_invalid"
        )

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            "MAGMA operator decision-reference review verifier-summary "
            "bridge-event template index entry FAILED: "
            + ", ".join(report["blockers"]),
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
    *,
    index_entry_verification_summary: Mapping[str, Any],
    summary_bridge_event_template_report: Mapping[str, Any],
    index_entry_verification_summary_bytes: bytes,
    summary_bridge_event_template_bytes: bytes,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return a path-free local index entry for the verifier-summary template."""

    _assert_mapping(SUMMARY_ARTIFACT_ID, index_entry_verification_summary)
    _assert_mapping(TEMPLATE_ARTIFACT_ID, summary_bridge_event_template_report)
    _assert_no_forbidden_input(
        SUMMARY_ARTIFACT_ID,
        index_entry_verification_summary,
    )
    _assert_no_forbidden_input(
        TEMPLATE_ARTIFACT_ID,
        summary_bridge_event_template_report,
    )
    _assert_bytes_match_artifact(
        SUMMARY_ARTIFACT_ID,
        index_entry_verification_summary,
        index_entry_verification_summary_bytes,
    )
    _assert_bytes_match_artifact(
        TEMPLATE_ARTIFACT_ID,
        summary_bridge_event_template_report,
        summary_bridge_event_template_bytes,
    )
    _assert_summary_contract(index_entry_verification_summary)

    rebuilt_template = _rebuilt_summary_bridge_template(
        index_entry_verification_summary,
        summary_bridge_event_template_report,
    )
    if _deterministic_artifact(rebuilt_template) != _deterministic_artifact(
        summary_bridge_event_template_report
    ):
        raise SummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_rebuilt_mismatch"
        )

    identity = _identity(index_entry_verification_summary)
    reference = _assert_reference_contract(
        _mapping(index_entry_verification_summary.get("operator_decision_reference_review"))
    )
    _assert_template_report_contract(
        summary_bridge_event_template_report,
        index_entry_verification_summary=index_entry_verification_summary,
        identity=identity,
        reference=reference,
    )

    event = _mapping(summary_bridge_event_template_report.get("bridge_event_template"))
    validate_event(event)
    summary_digest = _sha256_hex(index_entry_verification_summary_bytes)
    template_digest = _sha256_hex(summary_bridge_event_template_bytes)
    verification = _mapping(index_entry_verification_summary.get(_VERIFICATION_KEY))
    reviewer = _mapping(index_entry_verification_summary.get("reviewer_ownership"))
    artifacts = [
        _artifact_record(
            artifact_id=SUMMARY_ARTIFACT_ID,
            role="verified_index_entry_verification_summary_context",
            artifact=index_entry_verification_summary,
            raw=index_entry_verification_summary_bytes,
        ),
        _artifact_record(
            artifact_id=TEMPLATE_ARTIFACT_ID,
            role="template_only_bridge_handoff_context",
            artifact=summary_bridge_event_template_report,
            raw=summary_bridge_event_template_bytes,
        ),
    ]
    entry = {
        "ok": True,
        "index_entry_version": INDEX_ENTRY_VERSION,
        "created_at_utc": _utc_iso(now_utc or datetime.now(timezone.utc)),
        "release_ref": identity["release_ref"],
        "commit_sha": identity["commit_sha"],
        "ci_run_ref": identity["ci_run_ref"],
        "operator_decision_reference_review": {
            "decision_reference": reference["decision_reference"],
            "expected_decision_reference": reference["expected_decision_reference"],
            "decision_reference_verified": True,
            "decision_reference_is_approval": False,
            "decision_reference_is_release_decision": False,
            "decision_must_be_recorded_separately": True,
            "review_context_only": True,
            "manual_review_required": True,
        },
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "template_index_entry": {
            "artifact_id": TEMPLATE_ARTIFACT_ID,
            "template_version": SOURCE_TEMPLATE_VERSION,
            "template_only": True,
            "bridge_event_schema_validated": True,
            "source_summary_artifact_id": SUMMARY_ARTIFACT_ID,
            "source_summary_sha256": summary_digest,
            "template_sha256": template_digest,
            "source_contract_check": "match",
            "rebuilt_template_check": "match",
            "event_type": "handoff",
            "event_status": _BRIDGE_TEMPLATE_EVENT_STATUS,
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
        "verification_summary": {
            "summary_version": SOURCE_SUMMARY_VERSION,
            "verification_ok": True,
            "verification_version": _safe_ref_or_invalid(
                verification.get("verification_version")
            ),
            "index_entry_version": _safe_ref_or_invalid(
                verification.get("index_entry_version")
            ),
            "artifact_count_checked": len(_SOURCE_VERIFICATION_ARTIFACT_IDS),
            "source_contract_check": "match",
            "rebuilt_index_entry_check": "match",
            "bridge_event_schema_check": "match",
            "template_only": True,
            "reviewer_agent_id": _safe_ref_or_invalid(
                reviewer.get("reviewer_agent_id")
            ),
            "handoff_ref": _safe_ref_or_invalid(reviewer.get("handoff_ref")),
            "blocker_count": 0,
            "warning_count": len(
                _safe_token_list(index_entry_verification_summary.get("warnings"))
            ),
        },
        "consistency": {
            "release_ref_match": True,
            "commit_sha_match": True,
            "ci_run_ref_match": True,
            "decision_reference_match": True,
            "required_artifacts_present": list(_ARTIFACT_ORDER),
            "all_artifact_digests_recorded": True,
            "bridge_event_schema_validated": True,
            "source_contract_check": "match",
            "rebuilt_template_check": "match",
            "template_only": True,
            "artifact_payloads_included": False,
            "local_paths_recorded": False,
        },
        "operator_boundary": _authority_boundary(),
        "reviewer_next_actions": [
            "review_operator_decision_reference_review_verifier_summary_bridge_event_template_index_entry",
            "compare_verifier_summary_bridge_event_template_index_entry_to_local_artifacts",
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
        "warnings": _safe_token_list(index_entry_verification_summary.get("warnings"))
        + _safe_token_list(summary_bridge_event_template_report.get("warnings")),
    }
    _assert_no_forbidden_output(json.dumps(entry, allow_nan=False, sort_keys=True))
    return entry


def _rebuilt_summary_bridge_template(
    summary: Mapping[str, Any],
    template_report: Mapping[str, Any],
) -> Mapping[str, Any]:
    event = _mapping(template_report.get("bridge_event_template"))
    if not event:
        raise SummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_missing"
        )
    try:
        validate_event(event)
    except ValueError as exc:
        raise SummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_schema_invalid"
        ) from exc

    try:
        return build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template(
            summary=summary,
            agent_id=_required_safe_ref(
                event.get("agent"),
                "summary_bridge_event_template_agent_invalid",
            ),
            task_id=_required_safe_ref(
                event.get("task_id"),
                "summary_bridge_event_template_task_id_invalid",
            ),
            to=_required_targets(event.get("to")),
            severity=_required_severity(event.get("severity")),
            role=_required_safe_ref(
                event.get("role"),
                "summary_bridge_event_template_role_invalid",
            ),
            run_id=_optional_safe_ref(event.get("run_id")),
            session_id=_optional_safe_ref(event.get("session_id")),
            now_utc=_parse_utc(
                _required_safe_ref(
                    event.get("ts_utc"),
                    "summary_bridge_event_template_ts_utc_invalid",
                )
            ),
        )
    except (
        SummaryBridgeTemplateContractError,
        SummaryBridgeTemplateSafeInputError,
    ) as exc:
        raise SummaryBridgeTemplateIndexEntryError(
            f"summary_bridge_event_template_source_contract_failed:{exc.code}"
        ) from exc


def _assert_template_report_contract(
    template_report: Mapping[str, Any],
    *,
    index_entry_verification_summary: Mapping[str, Any],
    identity: Mapping[str, str],
    reference: Mapping[str, str],
) -> None:
    if template_report.get("ok") is not True:
        raise SummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_not_ok"
        )
    if template_report.get("template_version") != SOURCE_TEMPLATE_VERSION:
        raise SummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_version_mismatch"
        )
    if template_report.get("template_only") is not True:
        raise SummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_template_only_not_true"
        )
    _expect_empty_items(
        template_report.get("blockers"),
        "summary_bridge_event_template_blockers_present",
    )
    _expect_authority_false(template_report, "summary_bridge_event_template")

    event = _mapping(template_report.get("bridge_event_template"))
    if event.get("type") != "handoff":
        raise SummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_type_mismatch"
        )
    if event.get("status") != _BRIDGE_TEMPLATE_EVENT_STATUS:
        raise SummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_status_mismatch"
        )
    if event.get("paths") != []:
        raise SummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_paths_not_empty"
        )
    if event.get("write_scope") != []:
        raise SummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_write_scope_not_empty"
        )
    if event.get("pid") != 0:
        raise SummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_pid_not_zero"
        )
    if event.get("cwd") != "template_not_emitted":
        raise SummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_cwd_mismatch"
        )

    payload = _mapping(event.get("payload"))
    if payload.get("schema_version") != SOURCE_TEMPLATE_VERSION:
        raise SummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_payload_schema_mismatch"
        )
    if payload.get("summary_version") != SOURCE_SUMMARY_VERSION:
        raise SummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_payload_summary_version_mismatch"
        )
    if payload.get("template_only") is not True:
        raise SummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_payload_template_only_not_true"
        )
    _expect_authority_false(payload, "summary_bridge_event_template_payload")
    if payload.get("release_ref") != identity["release_ref"]:
        raise SummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_release_ref_mismatch"
        )
    if payload.get("commit_sha") != identity["commit_sha"]:
        raise SummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_commit_sha_mismatch"
        )
    if payload.get("ci_run_ref") != identity["ci_run_ref"]:
        raise SummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_ci_run_ref_mismatch"
        )

    payload_reference = _mapping(payload.get("operator_decision_reference_review"))
    if payload_reference.get("decision_reference") != reference["decision_reference"]:
        raise SummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_decision_reference_mismatch"
        )
    if (
        payload_reference.get("expected_decision_reference")
        != reference["expected_decision_reference"]
    ):
        raise SummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_expected_decision_reference_mismatch"
        )
    _assert_reference_contract(payload_reference)

    payload_verification = _mapping(payload.get(_VERIFICATION_KEY))
    summary_verification = _mapping(index_entry_verification_summary.get(_VERIFICATION_KEY))
    for field in (
        "source_contract_check",
        "rebuilt_index_entry_check",
        "bridge_event_schema_check",
        "artifact_count_checked",
    ):
        if payload_verification.get(field) != summary_verification.get(field):
            raise SummaryBridgeTemplateIndexEntryError(
                f"summary_bridge_event_template_verification_{field}_mismatch"
            )
    if payload_verification.get("verification_ok") is not True:
        raise SummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_verification_not_ok"
        )

    boundary = _mapping(payload.get("operator_boundary"))
    if boundary.get("manual_review_required") is not True:
        raise SummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_boundary_manual_review_required_not_true"
        )
    _expect_authority_false(boundary, "summary_bridge_event_template_boundary")


def _assert_summary_contract(summary: Mapping[str, Any]) -> None:
    blockers = _summary_contract_blockers(summary)
    if blockers:
        raise SummaryBridgeTemplateIndexEntryError(
            f"summary_bridge_event_template_source_contract_failed:{blockers[0]}"
        )


def _summary_contract_blockers(summary: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    if summary.get("ok") is not True:
        blockers.append("verifier_summary_not_ok")
    if summary.get("summary_version") != SOURCE_SUMMARY_VERSION:
        blockers.append("verifier_summary_version_mismatch")
    if _safe_ref_or_invalid(summary.get("release_ref")) == "invalid_ref":
        blockers.append("verifier_summary_release_ref_invalid")
    if _commit_or_invalid(summary.get("commit_sha")) == "invalid_commit":
        blockers.append("verifier_summary_commit_sha_invalid")
    if _safe_ref_or_invalid(summary.get("ci_run_ref")) == "invalid_ref":
        blockers.append("verifier_summary_ci_run_ref_invalid")
    if _safe_token_list(summary.get("blockers")):
        blockers.append("verifier_summary_blockers_present")
    for field in _AUTHORITY_FALSE_FIELDS:
        if summary.get(field) is not False:
            blockers.append(f"verifier_summary_{field}_not_false")

    reviewer = _mapping(summary.get("reviewer_ownership"))
    if reviewer.get("manual_review_required") is not True:
        blockers.append("reviewer_ownership_manual_review_required_not_true")
    for field in (
        "approval_granted",
        "release_decision_made",
        "automatic_release_decision",
    ):
        if reviewer.get(field) is not False:
            blockers.append(f"reviewer_ownership_{field}_not_false")
    if _safe_ref_or_invalid(reviewer.get("reviewer_agent_id")) == "invalid_ref":
        blockers.append("reviewer_ownership_reviewer_agent_id_invalid")
    if _safe_ref_or_invalid(reviewer.get("handoff_ref")) == "invalid_ref":
        blockers.append("reviewer_ownership_handoff_ref_invalid")

    verification = _mapping(summary.get(_VERIFICATION_KEY))
    if verification.get("verification_ok") is not True:
        blockers.append("verifier_summary_verification_not_ok")
    if verification.get("verification_version") != SOURCE_VERIFICATION_VERSION:
        blockers.append("verifier_summary_verification_version_mismatch")
    if verification.get("index_entry_version") != SOURCE_INDEX_ENTRY_VERSION:
        blockers.append("verifier_summary_index_entry_version_mismatch")
    if verification.get("artifact_count_checked") != len(_SOURCE_VERIFICATION_ARTIFACT_IDS):
        blockers.append("verifier_summary_artifact_count_mismatch")
    if verification.get("source_contract_check") != "match":
        blockers.append("verifier_summary_source_contract_not_match")
    if verification.get("rebuilt_index_entry_check") != "match":
        blockers.append("verifier_summary_rebuilt_index_entry_not_match")
    if verification.get("bridge_event_schema_check") != "match":
        blockers.append("verifier_summary_bridge_event_schema_not_match")
    if verification.get("template_only") is not True:
        blockers.append("verifier_summary_template_only_not_true")
    if verification.get("blocker_count") != 0:
        blockers.append("verifier_summary_blocker_count_nonzero")
    if _safe_token_list(verification.get("blockers")):
        blockers.append("verifier_summary_verification_blockers_present")
    for check_name in ("digest_checks", "size_checks", "schema_version_checks"):
        checks = _mapping(verification.get(check_name))
        for artifact_id in _SOURCE_VERIFICATION_ARTIFACT_IDS:
            if checks.get(artifact_id) != "match":
                blockers.append(
                    f"verifier_summary_{check_name}_{artifact_id}_not_match"
                )

    reference = _mapping(summary.get("operator_decision_reference_review"))
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

    boundary = _mapping(summary.get("operator_boundary"))
    if boundary.get("verification_report_boundary_ok") is not True:
        blockers.append("operator_boundary_verification_report_not_ok")
    if _safe_token_list(boundary.get("boundary_blockers")):
        blockers.append("operator_boundary_blockers_present")
    if boundary.get("manual_review_required") is not True:
        blockers.append("operator_boundary_manual_review_required_not_true")
    for field in _AUTHORITY_FALSE_FIELDS:
        if boundary.get(field) is not False:
            blockers.append(f"operator_boundary_{field}_not_false")
    return sorted(set(blockers))


def _authority_boundary() -> dict[str, bool]:
    return {
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
    }


def _failure_report(reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "index_entry_version": INDEX_ENTRY_VERSION,
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
            "operator_decision_reference_review_verifier_summary_"
            "bridge_event_template_index_entry_failed:"
            f"{_safe_reason(reason)}"
        ],
        "warnings": [],
    }


def _safe_reason(value: Any) -> str:
    text = str(value)
    if (
        text
        and all(ch.isalnum() or ch in ":._-" for ch in text)
        and len(text) <= 512
    ):
        return text
    return "unsafe_marker_redacted"


if __name__ == "__main__":
    raise SystemExit(main())
