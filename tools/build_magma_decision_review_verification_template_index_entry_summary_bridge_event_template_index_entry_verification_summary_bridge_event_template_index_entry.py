# SPDX-License-Identifier: BUSL-1.1
"""Build a MAGMA verifier-summary bridge-template index entry."""
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

from tools.build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry import (  # noqa: E402
    INDEX_ENTRY_VERSION as SOURCE_INDEX_ENTRY_VERSION,
)
from tools.build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary import (  # noqa: E402
    SUMMARY_VERSION as SOURCE_SUMMARY_VERSION,
)
from tools.build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template import (  # noqa: E402
    TEMPLATE_VERSION as SOURCE_TEMPLATE_VERSION,
    ContractError as SourceBridgeTemplateContractError,
    SafeInputError as SourceBridgeTemplateSafeInputError,
    build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template,
)
from tools.package_magma_alert_feed_release_evidence import (  # noqa: E402
    FORBIDDEN_OUTPUT_MARKERS,
)
from tools.verify_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry import (  # noqa: E402
    VERIFICATION_VERSION as SOURCE_VERIFICATION_VERSION,
)
from waggledance.core.bridge_event_schema import validate_event  # noqa: E402


INDEX_ENTRY_VERSION = "magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry.v1"

SUMMARY_ARTIFACT_ID = "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary"
TEMPLATE_ARTIFACT_ID = "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template"
_ARTIFACT_ORDER = (SUMMARY_ARTIFACT_ID, TEMPLATE_ARTIFACT_ID)
_SOURCE_VERIFICATION_ARTIFACT_IDS = (
    "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary",
    "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template",
)
_VERIFICATION_KEY = (
    "operator_decision_reference_review_bundle_verification_bridge_event_template_"
    "index_entry_verification_summary_bridge_event_template_index_entry_verification"
)
_SOURCE_EVENT_STATUS = "decision_reference_review_verifier_summary_bridge_event_template_ready"
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
_FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "payload",
        "payloads",
        "raw_payload",
        "raw_payloads",
        "artifact_payload",
        "artifact_payloads",
        "artifact_payload_bytes",
        "artifact_payload_content",
        "payload_bytes",
        "payload_content",
        "raw_bytes",
        "raw_content",
    }
)
_FORBIDDEN_PATH_KEYS = frozenset(
    {
        "path",
        "paths",
        "local_path",
        "local_paths",
        "file_path",
        "file_paths",
        "filesystem_path",
        "filesystem_paths",
        "filename",
        "filenames",
        "source_path",
        "source_paths",
        "target_path",
        "target_paths",
        "url",
        "urls",
        "uri",
        "uris",
        "endpoint",
        "endpoints",
    }
)
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,191}$")
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,511}$")
_AGENT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,32}$")


class VerifierSummaryBridgeTemplateIndexEntryError(ValueError):
    """Raised when verifier-summary template index-entry inputs are unsafe."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
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
    except VerifierSummaryBridgeTemplateIndexEntryError as exc:
        report = _failure_report(exc.code)
    except ValueError:
        report = _failure_report(
            "operator_decision_reference_review_verifier_summary_bridge_event_template_index_entry_invalid"
        )

    if args.json or report["ok"]:
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
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
    _assert_no_forbidden_input(SUMMARY_ARTIFACT_ID, index_entry_verification_summary)
    _assert_no_forbidden_input(TEMPLATE_ARTIFACT_ID, summary_bridge_event_template_report)
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
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_rebuilt_mismatch"
        )
    _assert_template_report_contract(
        summary_bridge_event_template_report,
        index_entry_verification_summary=index_entry_verification_summary,
    )

    identity = _identity(index_entry_verification_summary)
    reference = _assert_reference_contract(
        _mapping(index_entry_verification_summary.get("operator_decision_reference_review"))
    )
    verification = _mapping(index_entry_verification_summary.get(_VERIFICATION_KEY))
    reviewer = _mapping(index_entry_verification_summary.get("reviewer_ownership"))
    summary_digest = _sha256_hex(index_entry_verification_summary_bytes)
    template_digest = _sha256_hex(summary_bridge_event_template_bytes)
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
        "summary_version": SOURCE_SUMMARY_VERSION,
        "template_version": SOURCE_TEMPLATE_VERSION,
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
            "event_status": _SOURCE_EVENT_STATUS,
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
            "verification_ok": True,
            "verification_version": _safe_ref_or_invalid(
                verification.get("verification_version")
            ),
            "index_entry_version": _safe_ref_or_invalid(
                verification.get("index_entry_version")
            ),
            "artifact_count_checked": _as_nonnegative_int(
                verification.get("artifact_count_checked")
            ),
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
        "template_only": True,
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
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_missing"
        )
    try:
        validate_event(event)
    except ValueError as exc:
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_schema_invalid"
        ) from exc

    try:
        return build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template(
            summary=summary,
            agent_id=_required_agent_id(
                event.get("agent"),
                "summary_bridge_event_template_agent_invalid",
            ),
            task_id=_required_safe_ref(
                event.get("task_id"),
                "summary_bridge_event_template_task_id_invalid",
            ),
            to=_required_targets(event.get("to")),
            severity=_required_severity(event.get("severity")),
            role=_required_agent_id(
                event.get("role"),
                "summary_bridge_event_template_role_invalid",
            ),
            run_id=_optional_safe_ref(event.get("run_id")),
            session_id=_optional_session_id(event.get("session_id")),
            now_utc=_parse_utc(
                _required_safe_ref(
                    event.get("ts_utc"),
                    "summary_bridge_event_template_ts_utc_invalid",
                )
            ),
        )
    except (
        SourceBridgeTemplateContractError,
        SourceBridgeTemplateSafeInputError,
    ) as exc:
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            f"summary_bridge_event_template_source_contract_failed:{exc.code}"
        ) from exc


def _assert_template_report_contract(
    template_report: Mapping[str, Any],
    *,
    index_entry_verification_summary: Mapping[str, Any],
) -> None:
    if template_report.get("ok") is not True:
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_not_ok"
        )
    if template_report.get("template_version") != SOURCE_TEMPLATE_VERSION:
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_version_mismatch"
        )
    if template_report.get("template_only") is not True:
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_template_only_not_true"
        )
    _expect_empty_items(
        template_report.get("blockers"),
        "summary_bridge_event_template_blockers_present",
    )
    _expect_authority_false(template_report, "summary_bridge_event_template")

    event = _mapping(template_report.get("bridge_event_template"))
    if event.get("type") != "handoff":
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_type_mismatch"
        )
    if event.get("status") != _SOURCE_EVENT_STATUS:
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_status_mismatch"
        )
    if event.get("paths") != []:
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_paths_not_empty"
        )
    if event.get("write_scope") != []:
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_write_scope_not_empty"
        )
    if event.get("pid") != 0:
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_pid_not_zero"
        )
    if event.get("cwd") != "template_not_emitted":
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_cwd_mismatch"
        )

    payload = _mapping(event.get("payload"))
    if payload.get("schema_version") != SOURCE_TEMPLATE_VERSION:
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_payload_schema_mismatch"
        )
    if payload.get("summary_version") != SOURCE_SUMMARY_VERSION:
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_payload_summary_version_mismatch"
        )
    if payload.get("template_only") is not True:
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_payload_template_only_not_true"
        )
    _expect_authority_false(payload, "summary_bridge_event_template_payload")

    identity = _identity(index_entry_verification_summary)
    for field in ("release_ref", "commit_sha", "ci_run_ref"):
        if payload.get(field) != identity[field]:
            raise VerifierSummaryBridgeTemplateIndexEntryError(
                f"summary_bridge_event_template_{field}_mismatch"
            )

    summary_reviewer = _mapping(
        index_entry_verification_summary.get("reviewer_ownership")
    )
    payload_reviewer = _mapping(payload.get("reviewer_ownership"))
    for field in (
        "reviewer_agent_id",
        "handoff_ref",
        "manual_review_required",
        "approval_granted",
        "release_decision_made",
        "automatic_release_decision",
    ):
        if payload_reviewer.get(field) != summary_reviewer.get(field):
            raise VerifierSummaryBridgeTemplateIndexEntryError(
                f"summary_bridge_event_template_reviewer_{field}_mismatch"
            )

    payload_reference = _mapping(payload.get("operator_decision_reference_review"))
    summary_reference = _assert_reference_contract(
        _mapping(index_entry_verification_summary.get("operator_decision_reference_review"))
    )
    if payload_reference.get("decision_reference") != summary_reference[
        "decision_reference"
    ]:
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_decision_reference_mismatch"
        )
    if payload_reference.get("expected_decision_reference") != summary_reference[
        "expected_decision_reference"
    ]:
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_expected_decision_reference_mismatch"
        )
    _assert_reference_contract(payload_reference)

    payload_verification = _mapping(payload.get(_VERIFICATION_KEY))
    summary_verification = _mapping(
        index_entry_verification_summary.get(_VERIFICATION_KEY)
    )
    for field in ("verification_version", "index_entry_version"):
        if payload_verification.get(field) != _safe_ref_or_invalid(
            summary_verification.get(field)
        ):
            raise VerifierSummaryBridgeTemplateIndexEntryError(
                f"summary_bridge_event_template_verification_{field}_mismatch"
            )
    for field in (
        "artifact_count_checked",
        "digest_checks",
        "size_checks",
        "schema_version_checks",
        "source_contract_check",
        "rebuilt_index_entry_check",
        "bridge_event_schema_check",
        "template_only",
        "blocker_count",
        "warning_count",
    ):
        if payload_verification.get(field) != summary_verification.get(field):
            raise VerifierSummaryBridgeTemplateIndexEntryError(
                f"summary_bridge_event_template_verification_{field}_mismatch"
            )
    if payload_verification.get("verification_ok") is not True:
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_verification_not_ok"
        )

    boundary = _mapping(payload.get("operator_boundary"))
    if boundary.get("verification_report_boundary_ok") is not True:
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_boundary_verification_report_not_ok"
        )
    if boundary.get("manual_review_required") is not True:
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_boundary_manual_review_required_not_true"
        )
    _expect_authority_false(boundary, "summary_bridge_event_template_boundary")


def _assert_summary_contract(summary: Mapping[str, Any]) -> None:
    blockers = _summary_contract_blockers(summary)
    if blockers:
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            f"summary_bridge_event_template_source_contract_failed:{blockers[0]}"
        )


def _summary_contract_blockers(summary: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    blockers.extend(_recursive_contract_blockers(summary))
    if summary.get("ok") is not True:
        blockers.append("index_entry_verification_summary_not_ok")
    if summary.get("summary_version") != SOURCE_SUMMARY_VERSION:
        blockers.append("index_entry_verification_summary_version_mismatch")
    if _safe_ref_or_invalid(summary.get("release_ref")) == "invalid_ref":
        blockers.append("index_entry_verification_summary_release_ref_invalid")
    if _commit_or_invalid(summary.get("commit_sha")) == "invalid_commit":
        blockers.append("index_entry_verification_summary_commit_sha_invalid")
    if _safe_ref_or_invalid(summary.get("ci_run_ref")) == "invalid_ref":
        blockers.append("index_entry_verification_summary_ci_run_ref_invalid")
    if _safe_token_list(summary.get("blockers")):
        blockers.append("index_entry_verification_summary_blockers_present")
    for field in _AUTHORITY_FALSE_FIELDS:
        if summary.get(field) is not False:
            blockers.append(f"index_entry_verification_summary_{field}_not_false")

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
        blockers.append("index_entry_verification_not_ok")
    if verification.get("verification_version") != SOURCE_VERIFICATION_VERSION:
        blockers.append("index_entry_verification_version_mismatch")
    if verification.get("index_entry_version") != SOURCE_INDEX_ENTRY_VERSION:
        blockers.append("index_entry_verification_index_entry_version_mismatch")
    if verification.get("artifact_count_checked") != len(
        _SOURCE_VERIFICATION_ARTIFACT_IDS
    ):
        blockers.append("index_entry_verification_artifact_count_mismatch")
    if verification.get("source_contract_check") != "match":
        blockers.append("index_entry_verification_source_contract_not_match")
    if verification.get("rebuilt_index_entry_check") != "match":
        blockers.append("index_entry_verification_rebuilt_index_entry_not_match")
    if verification.get("bridge_event_schema_check") != "match":
        blockers.append("index_entry_verification_bridge_event_schema_not_match")
    if verification.get("template_only") is not True:
        blockers.append("index_entry_verification_template_only_not_true")
    blocker_count = verification.get("blocker_count")
    if (
        isinstance(blocker_count, bool)
        or not isinstance(blocker_count, int)
        or blocker_count != 0
    ):
        blockers.append("index_entry_verification_blocker_count_nonzero")
    if _safe_token_list(verification.get("blockers")):
        blockers.append("index_entry_verification_blockers_present")
    for check_name in ("digest_checks", "size_checks", "schema_version_checks"):
        checks = _mapping(verification.get(check_name))
        for artifact_id in _SOURCE_VERIFICATION_ARTIFACT_IDS:
            if checks.get(artifact_id) != "match":
                blockers.append(
                    f"index_entry_verification_{check_name}_{artifact_id}_not_match"
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


def _recursive_contract_blockers(value: Any) -> list[str]:
    blockers: list[str] = []
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = _safe_input_key(raw_key)
            if key in _FORBIDDEN_PAYLOAD_KEYS or key.endswith("_payload"):
                blockers.append(f"input_payload_key:{key}")
            if (
                key in _FORBIDDEN_PATH_KEYS
                or key.endswith("_path")
                or key.endswith("_paths")
            ):
                blockers.append(f"input_path_key:{key}")
            if key in _AUTHORITY_FALSE_FIELDS and child is not False:
                blockers.append(f"input_{key}_not_false")
            blockers.extend(_recursive_contract_blockers(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            blockers.extend(_recursive_contract_blockers(child))
    return sorted(set(blockers))


def _artifact_record(
    *,
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
    for field in ("summary_version", "template_version", "index_entry_version"):
        value = artifact.get(field)
        if isinstance(value, str):
            return value
    return "invalid_schema"


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


def _identity(artifact: Mapping[str, Any]) -> dict[str, str]:
    return {
        "release_ref": _required_safe_ref(
            artifact.get("release_ref"),
            "index_entry_verification_summary_release_ref_invalid",
        ),
        "commit_sha": _required_commit(
            artifact.get("commit_sha"),
            "index_entry_verification_summary_commit_sha_invalid",
        ),
        "ci_run_ref": _required_safe_ref(
            artifact.get("ci_run_ref"),
            "index_entry_verification_summary_ci_run_ref_invalid",
        ),
    }


def _assert_reference_contract(reference: Mapping[str, Any]) -> dict[str, str]:
    decision_reference = _required_safe_ref(
        reference.get("decision_reference"),
        "operator_decision_reference_invalid",
    )
    expected_decision_reference = _required_safe_ref(
        reference.get("expected_decision_reference"),
        "operator_decision_reference_expected_invalid",
    )
    if decision_reference != expected_decision_reference:
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            "operator_decision_reference_mismatch"
        )
    if reference.get("decision_reference_verified") is not True:
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            "operator_decision_reference_not_verified"
        )
    for field in _REFERENCE_FALSE_FIELDS:
        if reference.get(field) is not False:
            raise VerifierSummaryBridgeTemplateIndexEntryError(
                f"operator_decision_reference_{field}_not_false"
            )
    if reference.get("decision_must_be_recorded_separately") is not True:
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            "operator_decision_reference_decision_must_be_recorded_separately_not_true"
        )
    if reference.get("review_context_only") is not True:
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            "operator_decision_reference_review_context_only_not_true"
        )
    if reference.get("manual_review_required") is not True:
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            "operator_decision_reference_manual_review_required_not_true"
        )
    return {
        "decision_reference": decision_reference,
        "expected_decision_reference": expected_decision_reference,
    }


def _load_json_artifact(path: Path, artifact_id: str) -> tuple[bytes, Mapping[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            f"{artifact_id}_unreadable"
        ) from exc
    parsed = _parse_json_bytes(raw, artifact_id)
    if not isinstance(parsed, Mapping):
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            f"{artifact_id}_not_mapping"
        )
    return raw, parsed


def _parse_json_bytes(raw: bytes, artifact_id: str) -> Any:
    def reject_constant(_value: str) -> None:
        raise ValueError("non_finite_json_constant")

    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_object,
            parse_constant=reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            f"{artifact_id}_decode_error"
        ) from exc
    except json.JSONDecodeError as exc:
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            f"{artifact_id}_json_error"
        ) from exc
    except ValueError as exc:
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            f"{artifact_id}_json_error"
        ) from exc


def _reject_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _failure_report(reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "index_entry_version": INDEX_ENTRY_VERSION,
        "template_only": True,
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
            "operator_decision_reference_review_verifier_summary_bridge_event_template_index_entry_failed:"
            f"{_safe_reason(reason)}"
        ],
        "warnings": [],
    }


def _assert_mapping(artifact_id: str, value: Any) -> None:
    if not isinstance(value, Mapping):
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            f"{artifact_id}_not_mapping"
        )


def _assert_no_forbidden_input(artifact_id: str, artifact: Mapping[str, Any]) -> None:
    try:
        serialized = json.dumps(artifact, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            f"{artifact_id}_non_finite_json_value"
        ) from exc
    if _forbidden_output_markers(serialized):
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            f"{artifact_id}_forbidden_marker"
        )


def _assert_bytes_match_artifact(
    artifact_id: str,
    artifact: Mapping[str, Any],
    raw: bytes,
) -> None:
    parsed = _parse_json_bytes(raw, artifact_id)
    if _deterministic_artifact(parsed) != _deterministic_artifact(artifact):
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            f"{artifact_id}_bytes_mismatch"
        )


def _expect_authority_false(value: Mapping[str, Any], prefix: str) -> None:
    for field in _AUTHORITY_FALSE_FIELDS:
        if value.get(field) is not False:
            raise VerifierSummaryBridgeTemplateIndexEntryError(
                f"{prefix}_{field}_not_false"
            )


def _expect_empty_items(value: Any, reason: str) -> None:
    if value in (None, [], (), set()):
        return
    raise VerifierSummaryBridgeTemplateIndexEntryError(reason)


def _required_agent_id(value: Any, reason: str) -> str:
    if not isinstance(value, str) or not _AGENT_ID_RE.match(value):
        raise VerifierSummaryBridgeTemplateIndexEntryError(reason)
    return value


def _required_safe_ref(value: Any, reason: str) -> str:
    if (
        not isinstance(value, str)
        or not _SAFE_REF_RE.match(value)
        or _forbidden_output_markers(value)
    ):
        raise VerifierSummaryBridgeTemplateIndexEntryError(reason)
    return value


def _required_commit(value: Any, reason: str) -> str:
    if not isinstance(value, str) or not _COMMIT_RE.match(value):
        raise VerifierSummaryBridgeTemplateIndexEntryError(reason)
    return value


def _required_targets(value: Any) -> str:
    if not isinstance(value, str):
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_to_invalid"
        )
    targets = [item.strip() for item in value.split(",") if item.strip()]
    if not targets:
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            "summary_bridge_event_template_to_invalid"
        )
    for target in targets:
        _required_agent_id(target, "summary_bridge_event_template_to_invalid")
    return ",".join(targets)


def _required_severity(value: Any) -> str:
    if value in {"", "low", "medium", "high"}:
        return str(value)
    raise VerifierSummaryBridgeTemplateIndexEntryError(
        "summary_bridge_event_template_severity_invalid"
    )


def _optional_safe_ref(value: Any) -> str:
    if value in (None, ""):
        return ""
    return _required_safe_ref(value, "summary_bridge_event_template_ref_invalid")


def _optional_session_id(value: Any) -> str:
    if value in (None, ""):
        return ""
    return _required_safe_ref(value, "summary_bridge_event_template_session_id_invalid")


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


def _safe_token(value: Any, fallback: str = "unsafe_marker_redacted") -> str:
    if (
        isinstance(value, str)
        and _SAFE_TOKEN_RE.match(value)
        and not _forbidden_output_markers(value)
    ):
        return value
    return fallback


def _safe_reason(value: Any) -> str:
    return _safe_token(value, "unsafe_reason_redacted")


def _safe_token_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw_values = value
    elif isinstance(value, (set, tuple)):
        raw_values = list(value)
    else:
        raw_values = [value]
    return sorted({_safe_token(item) for item in raw_values})


def _safe_input_key(value: Any) -> str:
    if not isinstance(value, str):
        return "non_string_key"
    key = value.strip().replace("-", "_").lower()
    key = re.sub(r"[^a-z0-9_]", "_", key)
    return key[:128] or "empty_key"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_nonnegative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int) and value >= 0:
        return value
    return 0


def _deterministic_artifact(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise VerifierSummaryBridgeTemplateIndexEntryError(
            "artifact_non_finite_json_value"
        ) from exc


def _sha256_hex(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _parse_utc(raw: str) -> datetime:
    if not isinstance(raw, str) or not raw.endswith("Z"):
        raise VerifierSummaryBridgeTemplateIndexEntryError("timestamp_invalid")
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as exc:
        raise VerifierSummaryBridgeTemplateIndexEntryError("timestamp_invalid") from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != timezone.utc.utcoffset(parsed)
    ):
        raise VerifierSummaryBridgeTemplateIndexEntryError("timestamp_invalid")
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


def _forbidden_output_markers(text: str) -> list[str]:
    lower_text = text.lower()
    return sorted(
        marker
        for marker in FORBIDDEN_OUTPUT_MARKERS
        if marker.lower() in lower_text
    )


def _assert_no_forbidden_output(text: str) -> None:
    if _forbidden_output_markers(text):
        raise VerifierSummaryBridgeTemplateIndexEntryError("forbidden_output_marker")


if __name__ == "__main__":
    raise SystemExit(main())
