#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Build a hex xcons verifier-summary bridge-template index entry.

This local index entry binds the path-free verifier summary and its inert
bridge-event template to digest/schema/rebuild checks. It never appends bridge
events, transports artifacts, includes payloads, records local paths, upgrades
claims, skips gates, or grants runtime subdivision authority.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry import (  # noqa: E402
    AUTHORITY_FALSE_FIELDS,
    FORBIDDEN_OUTPUT_MARKERS,
    INDEX_ENTRY_VERSION as SOURCE_INDEX_ENTRY_VERSION,
    TEMPLATE_ARTIFACT_ID as SOURCE_TEMPLATE_ARTIFACT_ID,
)
from tools.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary import (  # noqa: E402
    SUMMARY_VERSION as SOURCE_SUMMARY_VERSION,
)
from tools.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template import (  # noqa: E402
    EVENT_STATUS as SOURCE_EVENT_STATUS,
    HEX_XCONS_VERIFICATION_KEY,
    TEMPLATE_VERSION as SOURCE_TEMPLATE_VERSION,
    build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template,
)
from tools.verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry import (  # noqa: E402
    VERIFICATION_VERSION as SOURCE_VERIFICATION_VERSION,
)
from waggledance.core.bridge_event_schema import validate_event  # noqa: E402


INDEX_ENTRY_VERSION = (
    "waggledance.hex_upgrade_cross_consistency_digest_bridge_event_template_"
    "index_entry_verification_summary_bridge_event_template_index_entry.v1"
)
PROOF_ID = (
    "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_"
    "verification_summary_bridge_event_template_index_entry_v1"
)
SUMMARY_ARTIFACT_ID = (
    "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_"
    "verification_summary"
)
TEMPLATE_ARTIFACT_ID = (
    "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_"
    "verification_summary_bridge_event_template"
)
_ARTIFACT_ORDER = (SUMMARY_ARTIFACT_ID, TEMPLATE_ARTIFACT_ID)
_SOURCE_VERIFICATION_ARTIFACT_IDS = (SOURCE_TEMPLATE_ARTIFACT_ID,)
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,191}$")
_WINDOWS_DRIVE_PATH_RE = re.compile(r"(?:^|[^A-Za-z0-9])(?:[A-Za-z]:[\\/])")
_HEX_EXTRA_FALSE_FIELDS = (
    "automatic_release_decision",
    "controls_present",
    "external_writes_applied",
    "network_access_performed",
)
_OUTPUT_FALSE_FIELDS = tuple(
    dict.fromkeys((*_HEX_EXTRA_FALSE_FIELDS, *AUTHORITY_FALSE_FIELDS))
)
_FALSE_FIELD_KEYS = frozenset(_OUTPUT_FALSE_FIELDS)
_VERIFICATION_FALSE_FIELDS = (
    "claim_safe",
    "literal_future_claim_safe",
    "runtime_subdivision_authority_granted",
    "runtime_authority_granted",
    "direct_bridge_write_performed",
    "transport_added",
    "bridge_event_written",
    "artifact_payloads_included",
    "local_paths_recorded",
)
_FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "payload",
        "payloads",
        "raw_payload",
        "raw_payloads",
        "artifact_payload",
        "artifact_payloads",
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


class TemplateIndexEntryError(ValueError):
    """Raised when local JSON artifacts violate the index-entry contract."""

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
        help="Optional UTC timestamp override such as 2026-06-20T09:40:00Z.",
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
        report = build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
            index_entry_verification_summary=summary,
            summary_bridge_event_template_report=template_report,
            index_entry_verification_summary_bytes=summary_bytes,
            summary_bridge_event_template_bytes=template_bytes,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except TemplateIndexEntryError as exc:
        report = _failure_report(exc.code)
    except ValueError:
        report = _failure_report(
            "hex_upgrade_cross_consistency_digest_bridge_event_template_"
            "index_entry_verification_summary_bridge_event_template_"
            "index_entry_invalid"
        )

    encoded = json.dumps(report, indent=2, sort_keys=True, allow_nan=False)
    if args.json or report["ok"]:
        print(encoded)
    else:
        print(
            "hex-upgrade xcons verifier-summary bridge-template index entry "
            "FAILED: "
            + ", ".join(report["blockers"]),
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
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
        raise TemplateIndexEntryError("summary_bridge_event_template_rebuilt_mismatch")

    _assert_template_report_contract(
        summary_bridge_event_template_report,
        index_entry_verification_summary=index_entry_verification_summary,
    )

    summary_digest = _sha256_hex(index_entry_verification_summary_bytes)
    template_digest = _sha256_hex(summary_bridge_event_template_bytes)
    verification = _mapping(
        index_entry_verification_summary.get(HEX_XCONS_VERIFICATION_KEY)
    )
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
        "proof_id": PROOF_ID,
        "ok": True,
        "index_entry_version": INDEX_ENTRY_VERSION,
        "created_at_utc": _utc_iso(now_utc or datetime.now(timezone.utc)),
        "summary_version": SOURCE_SUMMARY_VERSION,
        "template_version": SOURCE_TEMPLATE_VERSION,
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
            "event_status": SOURCE_EVENT_STATUS,
            "manual_review_required": True,
            **{field: False for field in _OUTPUT_FALSE_FIELDS},
        },
        "verification_summary": {
            "summary_proof_id": _safe_ref_or_invalid(
                index_entry_verification_summary.get("proof_id")
            ),
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
            "review_hex_xcons_verifier_summary_bridge_event_template_index_entry",
            "compare_bridge_event_template_index_entry_to_local_artifacts",
            "append_bridge_event_separately_only_after_manual_review",
        ],
        "template_only": True,
        "manual_review_required": True,
        **{field: False for field in _OUTPUT_FALSE_FIELDS},
        "blockers": [],
        "warnings": _safe_token_list(
            index_entry_verification_summary.get("warnings")
        )
        + _safe_token_list(summary_bridge_event_template_report.get("warnings")),
    }
    entry["path_free_verified"] = not _contains_path_marker(entry)
    _assert_no_forbidden_output(json.dumps(entry, allow_nan=False, sort_keys=True))
    return entry


def _rebuilt_summary_bridge_template(
    summary: Mapping[str, Any],
    template_report: Mapping[str, Any],
) -> Mapping[str, Any]:
    event = _mapping(template_report.get("bridge_event_template"))
    if not event:
        raise TemplateIndexEntryError("summary_bridge_event_template_missing")
    try:
        validate_event(event)
    except ValueError as exc:
        raise TemplateIndexEntryError(
            "summary_bridge_event_template_schema_invalid"
        ) from exc

    rebuilt = build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template(
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
    if rebuilt.get("ok") is not True:
        blockers = _safe_token_list(rebuilt.get("blockers"))
        reason = blockers[0] if blockers else "summary_bridge_event_template_not_ok"
        raise TemplateIndexEntryError(
            f"summary_bridge_event_template_source_contract_failed:{reason}"
        )
    return rebuilt


def _assert_template_report_contract(
    template_report: Mapping[str, Any],
    *,
    index_entry_verification_summary: Mapping[str, Any],
) -> None:
    if template_report.get("ok") is not True:
        raise TemplateIndexEntryError("summary_bridge_event_template_not_ok")
    if template_report.get("template_version") != SOURCE_TEMPLATE_VERSION:
        raise TemplateIndexEntryError("summary_bridge_event_template_version_mismatch")
    if template_report.get("template_only") is not True:
        raise TemplateIndexEntryError(
            "summary_bridge_event_template_template_only_not_true"
        )
    if template_report.get("manual_review_required") is not True:
        raise TemplateIndexEntryError(
            "summary_bridge_event_template_manual_review_not_true"
        )
    if template_report.get("path_free_verified") is not True:
        raise TemplateIndexEntryError("summary_bridge_event_template_path_free_not_true")
    _expect_empty_items(
        template_report.get("blockers"),
        "summary_bridge_event_template_blockers_present",
    )
    _expect_authority_false(template_report, "summary_bridge_event_template")

    event = _mapping(template_report.get("bridge_event_template"))
    if event.get("type") != "handoff":
        raise TemplateIndexEntryError("summary_bridge_event_template_type_mismatch")
    if event.get("status") != SOURCE_EVENT_STATUS:
        raise TemplateIndexEntryError("summary_bridge_event_template_status_mismatch")
    if event.get("paths") != []:
        raise TemplateIndexEntryError("summary_bridge_event_template_paths_not_empty")
    if event.get("write_scope") != []:
        raise TemplateIndexEntryError(
            "summary_bridge_event_template_write_scope_not_empty"
        )
    if event.get("pid") != 0:
        raise TemplateIndexEntryError("summary_bridge_event_template_pid_not_zero")
    if event.get("cwd") != "template_not_emitted":
        raise TemplateIndexEntryError("summary_bridge_event_template_cwd_mismatch")

    payload = _mapping(event.get("payload"))
    if payload.get("schema_version") != SOURCE_TEMPLATE_VERSION:
        raise TemplateIndexEntryError(
            "summary_bridge_event_template_payload_schema_mismatch"
        )
    if payload.get("summary_version") != SOURCE_SUMMARY_VERSION:
        raise TemplateIndexEntryError(
            "summary_bridge_event_template_payload_summary_version_mismatch"
        )
    if payload.get("summary_proof_id") != index_entry_verification_summary.get(
        "proof_id"
    ):
        raise TemplateIndexEntryError(
            "summary_bridge_event_template_summary_proof_mismatch"
        )
    if payload.get("template_only") is not True:
        raise TemplateIndexEntryError(
            "summary_bridge_event_template_payload_template_only_not_true"
        )
    _expect_authority_false(payload, "summary_bridge_event_template_payload")
    _expect_authority_false(
        _mapping(payload.get("operator_boundary")),
        "summary_bridge_event_template_boundary",
    )

    payload_reviewer = _mapping(payload.get("reviewer_ownership"))
    summary_reviewer = _mapping(index_entry_verification_summary.get("reviewer_ownership"))
    for field in ("reviewer_agent_id", "handoff_ref"):
        if payload_reviewer.get(field) != summary_reviewer.get(field):
            raise TemplateIndexEntryError(
                f"summary_bridge_event_template_reviewer_{field}_mismatch"
            )
    for field in (
        "manual_review_required",
        "approval_granted",
        "release_decision_made",
        "automatic_release_decision",
    ):
        if payload_reviewer.get(field) != summary_reviewer.get(field):
            raise TemplateIndexEntryError(
                f"summary_bridge_event_template_reviewer_{field}_mismatch"
            )

    payload_verification = _mapping(payload.get(HEX_XCONS_VERIFICATION_KEY))
    summary_verification = _mapping(
        index_entry_verification_summary.get(HEX_XCONS_VERIFICATION_KEY)
    )
    for field in (
        "verification_version",
        "index_entry_version",
        "source_template_version",
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
        "warnings",
    ):
        if payload_verification.get(field) != summary_verification.get(field):
            raise TemplateIndexEntryError(
                f"summary_bridge_event_template_verification_{field}_mismatch"
            )
    if payload_verification.get("verification_ok") is not True:
        raise TemplateIndexEntryError(
            "summary_bridge_event_template_verification_not_ok"
        )
    for field in _VERIFICATION_FALSE_FIELDS:
        if payload_verification.get(field) is not False:
            raise TemplateIndexEntryError(
                f"summary_bridge_event_template_verification_{field}_not_false"
            )


def _assert_summary_contract(summary: Mapping[str, Any]) -> None:
    blockers = _summary_contract_blockers(summary)
    if blockers:
        raise TemplateIndexEntryError(
            f"summary_bridge_event_template_source_contract_failed:{blockers[0]}"
        )


def _summary_contract_blockers(summary: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    blockers.extend(_recursive_contract_blockers(summary))
    if summary.get("ok") is not True:
        blockers.append("index_entry_verification_summary_not_ok")
    if summary.get("summary_version") != SOURCE_SUMMARY_VERSION:
        blockers.append("index_entry_verification_summary_version_mismatch")
    if summary.get("template_only") is not True:
        blockers.append("index_entry_verification_summary_template_only_not_true")
    if summary.get("manual_review_required") is not True:
        blockers.append("index_entry_verification_summary_manual_review_not_true")
    if summary.get("path_free_verified") is not True:
        blockers.append("index_entry_verification_summary_path_free_not_true")
    for field in _OUTPUT_FALSE_FIELDS:
        if summary.get(field) is not False:
            blockers.append(f"index_entry_verification_summary_{field}_not_false")
    _expect_empty_items_or_collect(
        summary.get("blockers"),
        "index_entry_verification_summary_blockers_present",
        blockers,
    )

    reviewer = _mapping(summary.get("reviewer_ownership"))
    if reviewer.get("manual_review_required") is not True:
        blockers.append("reviewer_ownership_manual_review_required_not_true")
    if _safe_ref_or_invalid(reviewer.get("reviewer_agent_id")) == "invalid_ref":
        blockers.append("reviewer_ownership_reviewer_agent_id_invalid")
    if _safe_ref_or_invalid(reviewer.get("handoff_ref")) == "invalid_ref":
        blockers.append("reviewer_ownership_handoff_ref_invalid")
    for field in (
        "approval_granted",
        "release_decision_made",
        "automatic_release_decision",
    ):
        if reviewer.get(field) is not False:
            blockers.append(f"reviewer_ownership_{field}_not_false")

    verification = _mapping(summary.get(HEX_XCONS_VERIFICATION_KEY))
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
    if verification.get("blocker_count") != 0:
        blockers.append("index_entry_verification_blocker_count_nonzero")
    _expect_empty_items_or_collect(
        verification.get("blockers"),
        "index_entry_verification_blockers_present",
        blockers,
    )
    for field in _VERIFICATION_FALSE_FIELDS:
        if verification.get(field) is not False:
            blockers.append(f"index_entry_verification_{field}_not_false")
    for check_name in ("digest_checks", "size_checks", "schema_version_checks"):
        checks = _mapping(verification.get(check_name))
        for artifact_id in _SOURCE_VERIFICATION_ARTIFACT_IDS:
            if checks.get(artifact_id) != "match":
                blockers.append(
                    f"index_entry_verification_{check_name}_{_safe_reason(artifact_id)}_not_match"
                )

    boundary = _mapping(summary.get("operator_boundary"))
    if boundary.get("verification_report_boundary_ok") is not True:
        blockers.append("operator_boundary_verification_report_not_ok")
    _expect_empty_items_or_collect(
        boundary.get("boundary_blockers"),
        "operator_boundary_blockers_present",
        blockers,
    )
    if boundary.get("manual_review_required") is not True:
        blockers.append("operator_boundary_manual_review_required_not_true")
    for field in _OUTPUT_FALSE_FIELDS:
        if boundary.get(field) is not False:
            blockers.append(f"operator_boundary_{field}_not_false")
    return sorted(set(blockers))


def _recursive_contract_blockers(value: Any) -> list[str]:
    blockers: list[str] = []
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = raw_key if isinstance(raw_key, str) else "invalid_key"
            if key in _FORBIDDEN_PAYLOAD_KEYS or key.endswith("_payload"):
                blockers.append(f"input_payload_key:{key}")
            if (
                key in _FORBIDDEN_PATH_KEYS
                or key.endswith("_path")
                or key.endswith("_paths")
            ):
                blockers.append(f"input_path_key:{key}")
            if key in _FALSE_FIELD_KEYS and child is not False:
                blockers.append(f"input_{key}_not_false")
            blockers.extend(_recursive_contract_blockers(child))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            blockers.extend(_recursive_contract_blockers(child))
    return sorted(set(blockers))


def _load_json_artifact(path: Path, artifact_id: str) -> tuple[bytes, Mapping[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise TemplateIndexEntryError(f"{artifact_id}_unreadable") from exc

    parsed = _parse_json_bytes(raw, artifact_id)
    if not isinstance(parsed, Mapping):
        raise TemplateIndexEntryError(f"{artifact_id}_not_mapping")
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
        raise TemplateIndexEntryError(f"{artifact_id}_decode_error") from exc
    except json.JSONDecodeError as exc:
        raise TemplateIndexEntryError(f"{artifact_id}_json_error") from exc
    except ValueError as exc:
        raise TemplateIndexEntryError(f"{artifact_id}_json_error") from exc


def _reject_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


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
        **{field: False for field in _OUTPUT_FALSE_FIELDS},
    }


def _failure_report(reason: str) -> dict[str, Any]:
    return {
        "proof_id": PROOF_ID,
        "ok": False,
        "index_entry_version": INDEX_ENTRY_VERSION,
        "template_only": True,
        "manual_review_required": True,
        **{field: False for field in _OUTPUT_FALSE_FIELDS},
        "path_free_verified": True,
        "blockers": [
            "hex_upgrade_cross_consistency_digest_bridge_event_template_"
            "index_entry_verification_summary_bridge_event_template_"
            f"index_entry_failed:{_safe_reason(reason)}"
        ],
        "warnings": [],
    }


def _assert_mapping(artifact_id: str, value: Any) -> None:
    if not isinstance(value, Mapping):
        raise TemplateIndexEntryError(f"{artifact_id}_not_mapping")


def _assert_bytes_match_artifact(
    artifact_id: str,
    artifact: Mapping[str, Any],
    raw: bytes,
) -> None:
    parsed = _parse_json_bytes(raw, artifact_id)
    if _deterministic_artifact(parsed) != _deterministic_artifact(artifact):
        raise TemplateIndexEntryError(f"{artifact_id}_bytes_mismatch")


def _assert_no_forbidden_input(artifact_id: str, artifact: Mapping[str, Any]) -> None:
    try:
        serialized = json.dumps(artifact, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise TemplateIndexEntryError(f"{artifact_id}_non_finite_json_value") from exc
    if _contains_path_marker(artifact) or _forbidden_output_markers(serialized):
        raise TemplateIndexEntryError(f"{artifact_id}_not_path_free")


def _expect_authority_false(value: Mapping[str, Any], prefix: str) -> None:
    for field in _OUTPUT_FALSE_FIELDS:
        if field in value and value.get(field) is not False:
            raise TemplateIndexEntryError(f"{prefix}_{field}_not_false")


def _expect_empty_items(value: Any, reason: str) -> None:
    if value == []:
        return
    raise TemplateIndexEntryError(reason)


def _expect_empty_items_or_collect(value: Any, reason: str, blockers: list[str]) -> None:
    if value == []:
        return
    blockers.append(reason)


def _required_safe_ref(value: Any, reason: str) -> str:
    if (
        not isinstance(value, str)
        or not _SAFE_REF_RE.fullmatch(value)
        or _forbidden_output_markers(value)
        or _contains_path_marker(value)
    ):
        raise TemplateIndexEntryError(reason)
    return value


def _optional_safe_ref(value: Any) -> str:
    if value in (None, ""):
        return ""
    return _required_safe_ref(value, "summary_bridge_event_template_ref_invalid")


def _required_targets(value: Any) -> str:
    if not isinstance(value, str):
        raise TemplateIndexEntryError("summary_bridge_event_template_to_invalid")
    targets = [item.strip() for item in value.split(",") if item.strip()]
    if not targets:
        raise TemplateIndexEntryError("summary_bridge_event_template_to_invalid")
    for target in targets:
        _required_safe_ref(target, "summary_bridge_event_template_to_invalid")
    return ",".join(targets)


def _required_severity(value: Any) -> str:
    if value in {"", "low", "medium", "high"}:
        return str(value)
    raise TemplateIndexEntryError("summary_bridge_event_template_severity_invalid")


def _safe_ref_or_invalid(value: Any) -> str:
    return (
        value
        if isinstance(value, str)
        and _SAFE_REF_RE.fullmatch(value)
        and not _forbidden_output_markers(value)
        and not _contains_path_marker(value)
        else "invalid_ref"
    )


def _safe_token(value: Any, fallback: str = "invalid_token") -> str:
    return (
        value
        if isinstance(value, str)
        and _SAFE_REF_RE.fullmatch(value)
        and not _forbidden_output_markers(value)
        and not _contains_path_marker(value)
        else fallback
    )


def _safe_reason(value: Any) -> str:
    return _safe_token(value, "unsafe_reason_redacted")


def _safe_token_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [_safe_token(item) for item in value if isinstance(item, str)]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_nonnegative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value if value >= 0 else 0


def _contains_path_marker(value: Any) -> bool:
    if isinstance(value, str):
        normalized = value.replace("\\", "/").lower()
        return (
            _WINDOWS_DRIVE_PATH_RE.search(value) is not None
            or normalized.startswith("//")
            or "/home/" in normalized
            or "/users/" in normalized
            or "/tmp/" in normalized
            or "waggledance-agent-worktrees" in normalized
        )
    if isinstance(value, Mapping):
        return any(
            _contains_path_marker(key) or _contains_path_marker(item)
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_path_marker(item) for item in value)
    return False


def _forbidden_output_markers(text: str) -> list[str]:
    lower_text = text.lower()
    return sorted(
        marker
        for marker in FORBIDDEN_OUTPUT_MARKERS
        if marker.lower() in lower_text
    )


def _assert_no_forbidden_output(serialized: str) -> None:
    if _contains_path_marker(serialized) or _forbidden_output_markers(serialized):
        raise TemplateIndexEntryError("forbidden_output_marker")


def _deterministic_artifact(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TemplateIndexEntryError("artifact_non_finite_json_value") from exc


def _sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _parse_utc(raw: str) -> datetime:
    if not isinstance(raw, str) or not raw.endswith("Z"):
        raise TemplateIndexEntryError("timestamp_invalid")
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as exc:
        raise TemplateIndexEntryError("timestamp_invalid") from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != timezone.utc.utcoffset(parsed)
    ):
        raise TemplateIndexEntryError("timestamp_invalid")
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00",
        "Z",
    )


if __name__ == "__main__":
    raise SystemExit(main())
