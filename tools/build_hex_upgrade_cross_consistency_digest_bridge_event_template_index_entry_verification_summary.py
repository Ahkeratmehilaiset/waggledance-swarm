#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Render a path-free hex-upgrade index-entry verifier summary.

This reviewer-context tool summarizes the offline verifier for the WD Image #1
hexagonal-upgrades cross-consistency bridge-template index entry. It never
includes artifact payloads or local paths, never appends bridge events, and never
grants runtime subdivision authority.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry import (  # noqa: E402
    FORBIDDEN_OUTPUT_MARKERS,
    INDEX_ENTRY_VERSION,
)
from tools.verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry import (  # noqa: E402
    VERIFICATION_VERSION,
)


SUMMARY_VERSION = (
    "waggledance.hex_upgrade_cross_consistency_digest_bridge_event_template_"
    "index_entry_verification_summary.v1"
)
PROOF_ID = (
    "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_"
    "verification_summary_v1"
)
_CHECK_STATUS = frozenset(
    {
        "match",
        "mismatch",
        "failed",
        "missing",
        "missing_index_record",
        "not_checked",
        "unknown",
    }
)
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,191}$")
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,511}$")
_VERIFICATION_FALSE_FIELDS = (
    "approval_granted",
    "release_decision_made",
    "merge_decision_made",
    "promotion_granted",
    "direct_bridge_write_performed",
    "transport_added",
    "external_fetch_performed",
    "network_access_performed",
    "runtime_controls_added",
    "runtime_authority_granted",
    "runtime_subdivision_authority_granted",
    "bridge_event_written",
    "gate_skip_allowed",
    "fast_track_priority",
    "artifact_payloads_included",
    "local_paths_recorded",
    "claim_safe",
)
_SUMMARY_FALSE_FIELDS = (
    *_VERIFICATION_FALSE_FIELDS,
    "automatic_release_decision",
    "external_writes_applied",
    "controls_present",
    "scheduler_enqueue_allowed",
    "scheduler_tick_allowed",
)
_NESTED_AUTHORITY_FALSE_FIELDS = frozenset(_SUMMARY_FALSE_FIELDS)
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
_FORBIDDEN_AUTHORITY_CONTAINER_KEYS = frozenset(
    {
        "operator_boundary",
        "reviewer_ownership",
        "release_authority",
        "approval_authority",
        "runtime_authority",
        "scheduler_authority",
    }
)
_PATH_MARKERS = tuple(FORBIDDEN_OUTPUT_MARKERS) + (
    "file://",
    "http://",
    "https://",
    "waggledance-agent-worktrees",
    "/tmp/",
    "\\tmp\\",
)


class SafeInputError(ValueError):
    """Raised when local verifier inputs are unsafe to summarize."""

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
        help="Optional UTC timestamp override such as 2026-06-25T22:00:00Z.",
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
            summary = build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary(
                verification_report=verification_report,
                reviewer_agent_id=args.reviewer_agent,
                handoff_ref=args.handoff_ref,
                now_utc=_parse_utc(args.now) if args.now else None,
            )
        except SafeInputError as exc:
            summary = _failure_summary(exc.code)
        except ValueError:
            summary = _failure_summary(
                "hex_upgrade_index_entry_verification_summary_invalid"
            )

    encoded = json.dumps(summary, indent=2, sort_keys=True, allow_nan=False)
    if args.json:
        print(encoded)
    elif summary["ok"]:
        print(
            render_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_markdown(
                summary
            )
        )
    else:
        print(
            "hex-upgrade index-entry verification summary FAILED: "
            + ", ".join(summary["blockers"]),
            file=sys.stderr,
        )
    return 0 if summary["ok"] else 1


def build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary(
    *,
    verification_report: Mapping[str, Any],
    reviewer_agent_id: str,
    handoff_ref: str,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return path-free reviewer context for a local hex index verification."""

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

    verification_summary = {
        "verification_ok": verification_ok,
        "verification_version": _safe_ref_or_invalid(
            verification_report.get("verification_version")
        ),
        "index_entry_version": _safe_ref_or_invalid(
            verification_report.get("index_entry_version")
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
        "digest_checks": _check_statuses(
            _mapping(verification_report.get("digest_checks"))
        ),
        "size_checks": _check_statuses(
            _mapping(verification_report.get("size_checks"))
        ),
        "template_only": verification_report.get("template_only") is True,
        "manual_review_required": verification_report.get("manual_review_required") is True,
    }
    for field in _SUMMARY_FALSE_FIELDS:
        verification_summary[field] = verification_report.get(field) is True

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
        "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification": verification_summary,
        "operator_boundary": {
            "verification_report_boundary_ok": not boundary_blockers,
            "manual_review_required": True,
            "approval_granted": False,
            "release_decision_made": False,
            "automatic_release_decision": False,
            "runtime_authority_granted": False,
            "runtime_subdivision_authority_granted": False,
            "bridge_event_written": False,
            "gate_skip_allowed": False,
            "fast_track_priority": False,
            "claim_safe": False,
        },
        "template_only": True,
        "manual_review_required": True,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "direct_bridge_write_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "network_access_performed": False,
        "runtime_controls_added": False,
        "runtime_authority_granted": False,
        "runtime_subdivision_authority_granted": False,
        "approval_granted": False,
        "release_decision_made": False,
        "automatic_release_decision": False,
        "merge_decision_made": False,
        "promotion_granted": False,
        "bridge_event_written": False,
        "gate_skip_allowed": False,
        "fast_track_priority": False,
        "claim_safe": False,
        "external_writes_applied": False,
        "controls_present": False,
        "scheduler_enqueue_allowed": False,
        "scheduler_tick_allowed": False,
        "blockers": blockers,
        "warnings": report_warnings,
    }
    _assert_no_forbidden_output(json.dumps(summary, allow_nan=False, sort_keys=True))
    return summary


def render_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_markdown(
    summary: Mapping[str, Any],
) -> str:
    verification = _mapping(
        summary.get(
            "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification"
        )
    )
    lines = [
        "# Hex Upgrade Index Verification Summary",
        "",
        f"- OK: `{_bool_text(summary.get('ok'))}`",
        f"- Verification OK: `{_bool_text(verification.get('verification_ok'))}`",
        f"- Source contract: `{verification.get('source_contract_check', 'unknown')}`",
        f"- Rebuilt index entry: `{verification.get('rebuilt_index_entry_check', 'unknown')}`",
        f"- Bridge event schema: `{verification.get('bridge_event_schema_check', 'unknown')}`",
        f"- Runtime subdivision authority granted: `{_bool_text(summary.get('runtime_subdivision_authority_granted'))}`",
        f"- Runtime authority granted: `{_bool_text(summary.get('runtime_authority_granted'))}`",
        f"- Bridge event written: `{_bool_text(summary.get('bridge_event_written'))}`",
        f"- Gate skip allowed: `{_bool_text(summary.get('gate_skip_allowed'))}`",
        f"- Artifact payloads included: `{_bool_text(summary.get('artifact_payloads_included'))}`",
        f"- Local paths recorded: `{_bool_text(summary.get('local_paths_recorded'))}`",
        "",
        "This summary is reviewer context only. It does not append bridge events, "
        "transport artifacts, grant runtime subdivision authority, skip gates, or "
        "make a release decision.",
    ]
    return "\n".join(lines)


def _load_json_report(path: Path) -> Mapping[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SafeInputError("verification_report_unreadable") from exc
    try:
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except ValueError as exc:
        raise SafeInputError("verification_report_json_invalid") from exc
    if not isinstance(value, Mapping):
        raise SafeInputError("verification_report_not_object")
    return value


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key: {key}")
        result[key] = value
    return result


def _failure_summary(blocker: str) -> dict[str, Any]:
    summary = {
        "proof_id": PROOF_ID,
        "ok": False,
        "summary_version": SUMMARY_VERSION,
        "created_at_utc": _utc_iso(datetime.now(timezone.utc)),
        "reviewer_ownership": {
            "reviewer_agent_id": "invalid_ref",
            "handoff_ref": "invalid_ref",
            "manual_review_required": True,
            "approval_granted": False,
            "release_decision_made": False,
            "automatic_release_decision": False,
        },
        "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification": {
            "verification_ok": False,
            "verification_version": "invalid_ref",
            "index_entry_version": "invalid_ref",
            "source_contract_check": "failed",
            "rebuilt_index_entry_check": "failed",
            "bridge_event_schema_check": "failed",
            "digest_checks": {},
            "size_checks": {},
            "template_only": False,
            "manual_review_required": True,
        },
        "operator_boundary": {
            "verification_report_boundary_ok": False,
            "manual_review_required": True,
            "approval_granted": False,
            "release_decision_made": False,
            "automatic_release_decision": False,
            "runtime_authority_granted": False,
            "runtime_subdivision_authority_granted": False,
            "bridge_event_written": False,
            "gate_skip_allowed": False,
            "fast_track_priority": False,
            "claim_safe": False,
        },
        "template_only": True,
        "manual_review_required": True,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "direct_bridge_write_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "network_access_performed": False,
        "runtime_controls_added": False,
        "runtime_authority_granted": False,
        "runtime_subdivision_authority_granted": False,
        "approval_granted": False,
        "release_decision_made": False,
        "automatic_release_decision": False,
        "merge_decision_made": False,
        "promotion_granted": False,
        "bridge_event_written": False,
        "gate_skip_allowed": False,
        "fast_track_priority": False,
        "claim_safe": False,
        "external_writes_applied": False,
        "controls_present": False,
        "scheduler_enqueue_allowed": False,
        "scheduler_tick_allowed": False,
        "blockers": [_safe_token(blocker)],
        "warnings": [],
    }
    _assert_no_forbidden_output(json.dumps(summary, allow_nan=False, sort_keys=True))
    return summary


def _verification_report_contract_blockers(report: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    if report.get("verification_version") != VERIFICATION_VERSION:
        blockers.append("verification_report_version_mismatch")
    if report.get("index_entry_version") != INDEX_ENTRY_VERSION:
        blockers.append("verification_report_index_entry_version_mismatch")
    if report.get("template_only") is not True:
        blockers.append("verification_report_template_only_not_true")
    if report.get("manual_review_required") is not True:
        blockers.append("verification_report_manual_review_required_not_true")
    for name in (
        "source_contract_check",
        "rebuilt_index_entry_check",
        "bridge_event_schema_check",
    ):
        if _check_status(report.get(name)) == "unknown":
            blockers.append(f"verification_report_{name}_unknown")
    blockers.extend(_check_status_schema_blockers(report, "digest_checks"))
    blockers.extend(_check_status_schema_blockers(report, "size_checks"))
    return blockers


def _boundary_blockers(report: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    for field in _VERIFICATION_FALSE_FIELDS:
        if report.get(field) is not False:
            blockers.append(f"verification_report_{field}_not_false")
    return blockers


def _recursive_boundary_blockers(value: Any, prefix: str = "verification_report") -> list[str]:
    blockers: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            normalized = key_text.lower()
            if normalized in _FORBIDDEN_AUTHORITY_CONTAINER_KEYS:
                blockers.append(f"{prefix}_forbidden_authority_container:{key_text}")
            if normalized in _NESTED_AUTHORITY_FALSE_FIELDS and item is not False:
                blockers.append(f"{prefix}_nested_authority_field_not_false:{key_text}")
            if normalized in _FORBIDDEN_PAYLOAD_KEYS:
                blockers.append(f"{prefix}_forbidden_payload_key:{key_text}")
            if normalized in _FORBIDDEN_PATH_KEYS:
                blockers.append(f"{prefix}_forbidden_path_key:{key_text}")
            blockers.extend(_recursive_boundary_blockers(item, prefix=prefix))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            blockers.extend(_recursive_boundary_blockers(item, prefix=prefix))
    return blockers


def _check_status_schema_blockers(
    report: Mapping[str, Any],
    field: str,
) -> list[str]:
    value = report.get(field)
    if not isinstance(value, Mapping):
        return [f"verification_report_{field}_not_mapping"]
    return [
        f"verification_report_{field}_bad_status:{_safe_token(str(key))}"
        for key, item in value.items()
        if _check_status(item) == "unknown"
    ]


def _token_list_schema_blockers(
    report: Mapping[str, Any],
    field: str,
) -> list[str]:
    value = report.get(field, [])
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return [f"verification_report_{field}_not_list"]
    blockers: list[str] = []
    for item in value:
        if not isinstance(item, str) or not _SAFE_TOKEN_RE.fullmatch(item):
            blockers.append(f"verification_report_{field}_unsafe_token")
    return blockers


def _assert_no_forbidden_input(label: str, value: Any) -> None:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=True, default=str)
    normalized = encoded.replace("\\", "/").lower()
    for marker in _PATH_MARKERS:
        marker_normalized = marker.replace("\\", "/").lower()
        if marker_normalized and marker_normalized in normalized:
            raise SafeInputError(f"{label}_contains_forbidden_marker")


def _assert_no_forbidden_output(encoded: str) -> None:
    normalized = encoded.replace("\\", "/").lower()
    for marker in _PATH_MARKERS:
        marker_normalized = marker.replace("\\", "/").lower()
        if marker_normalized and marker_normalized in normalized:
            raise AssertionError(f"forbidden output marker: {marker}")


def _check_statuses(value: Mapping[str, Any]) -> dict[str, str]:
    return {
        _safe_token(str(key)): _check_status(item)
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
    }


def _check_status(value: Any) -> str:
    if isinstance(value, str) and value in _CHECK_STATUS:
        return value
    return "unknown"


def _safe_token_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return sorted({_safe_token(str(item)) for item in value if isinstance(item, str)})


def _safe_token(value: str, fallback: str = "invalid_token") -> str:
    if _SAFE_TOKEN_RE.fullmatch(value):
        return value
    return fallback


def _safe_ref_or_invalid(value: Any) -> str:
    if isinstance(value, str) and _SAFE_REF_RE.fullmatch(value):
        return value
    return "invalid_ref"


def _validate_safe_ref(label: str, value: str) -> None:
    if not isinstance(value, str) or not _SAFE_REF_RE.fullmatch(value):
        raise SafeInputError(f"{label}_invalid")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _bool_text(value: Any) -> str:
    return "true" if value is True else "false"


def _parse_utc(text: str) -> datetime:
    normalized = text.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00",
        "Z",
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
