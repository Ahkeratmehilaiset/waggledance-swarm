#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Render a path-free Memory Palace template-index verifier summary."""

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

from tools.build_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry import (  # noqa: E402
    AUTHORITY_FALSE_FIELDS,
    EXTRA_TEMPLATE_FALSE_FIELDS,
    FORBIDDEN_OUTPUT_MARKERS,
    INDEX_ENTRY_VERSION,
    SUMMARY_ARTIFACT_ID,
    TEMPLATE_ARTIFACT_ID,
)
from tools.verify_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry import (  # noqa: E402
    VERIFICATION_VERSION,
)


SUMMARY_VERSION = (
    "wd.v12.memory_palace_visualization_export."
    "verification_summary_bridge_event_template_index_entry_"
    "verification_summary.v0"
)
PROOF_ID = (
    "memory_palace_visualization_export_verification_summary_"
    "bridge_event_template_index_entry_verification_summary_v0"
)
_REQUIRED_ARTIFACTS = (SUMMARY_ARTIFACT_ID, TEMPLATE_ARTIFACT_ID)
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
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,191}$")
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,511}$")
_VERIFICATION_FALSE_FIELDS = (
    "approval_granted",
    "release_decision_made",
    "automatic_release_decision",
    "runtime_route_changed",
    "storage_write_performed",
    "bridge_append_performed",
    "direct_bridge_write_performed",
    "solver_call_performed",
    "scheduler_enqueue_performed",
    "promotion_performed",
    "gate_skip_performed",
    "network_access_performed",
    "transport_added",
    "external_fetch_performed",
    "runtime_controls_added",
    "controls_present",
    "runtime_authority_granted",
    "external_writes_applied",
    "artifact_payloads_included",
    "local_paths_recorded",
)
_NESTED_AUTHORITY_FALSE_FIELDS = frozenset(
    (*AUTHORITY_FALSE_FIELDS, *EXTRA_TEMPLATE_FALSE_FIELDS, *_VERIFICATION_FALSE_FIELDS)
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
_FORBIDDEN_AUTHORITY_CONTAINER_KEYS = frozenset(
    {
        "operator_boundary",
        "reviewer_ownership",
        "release_authority",
        "approval_authority",
        "runtime_authority",
        "scheduler_authority",
        "gate_authority",
        "bridge_authority",
        "promotion_authority",
        "storage_authority",
        "network_authority",
        "authority_boundary",
    }
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
        help="Optional UTC timestamp override such as 2026-06-08T22:45:00Z.",
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
            summary = build_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry_verification_summary(
                verification_report=verification_report,
                reviewer_agent_id=args.reviewer_agent,
                handoff_ref=args.handoff_ref,
                now_utc=_parse_utc(args.now) if args.now else None,
            )
        except SafeInputError as exc:
            summary = _failure_summary(exc.code)
        except ValueError:
            summary = _failure_summary(
                "memory_palace_visualization_export_verification_summary_"
                "bridge_event_template_index_entry_verification_summary_invalid"
            )

    encoded = json.dumps(summary, indent=2, sort_keys=True, allow_nan=False)
    if args.json:
        print(encoded)
    elif summary["ok"]:
        print(
            render_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry_verification_summary_markdown(
                summary
            )
        )
    else:
        print(
            "Memory Palace visualization template-index verification summary "
            "FAILED: "
            + ", ".join(summary["blockers"]),
            file=sys.stderr,
        )
    return 0 if summary["ok"] else 1


def build_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry_verification_summary(
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
        "memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry_verification": {
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
            "read_side_report_only": verification_report.get("read_side_report_only")
            is True,
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
            **{name: False for name in _VERIFICATION_FALSE_FIELDS},
        },
        "reviewer_next_actions": [
            "review_memory_palace_visualization_template_index_verification_summary",
            "compare_template_index_verification_summary_to_local_artifacts",
            "append_bridge_event_separately_only_after_manual_review",
        ],
        "template_only": True,
        "read_side_report_only": True,
        "manual_review_required": True,
        **{name: False for name in _VERIFICATION_FALSE_FIELDS},
        "blockers": blockers,
        "warnings": report_warnings,
    }
    _assert_no_forbidden_output(json.dumps(summary, allow_nan=False, sort_keys=True))
    return summary


def render_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry_verification_summary_markdown(
    summary: Mapping[str, Any],
) -> str:
    verification = _mapping(
        summary.get(
            "memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry_verification"
        )
    )
    boundary = _mapping(summary.get("operator_boundary"))
    lines = [
        "# Memory Palace Template-Index Verification Summary",
        "",
        f"- Summary version: `{summary.get('summary_version')}`",
        f"- Created at UTC: `{summary.get('created_at_utc')}`",
        "- Manual review required: `true`",
        "- Automatic release decision: `false`",
        "- Approval granted: `false`",
        "- Release decision made: `false`",
        "- Runtime route changed: `false`",
        "- Storage write performed: `false`",
        "- Bridge append performed: `false`",
        "- Direct bridge write performed: `false`",
        "- Solver call performed: `false`",
        "- Scheduler enqueue performed: `false`",
        "- Promotion performed: `false`",
        "- Gate skip performed: `false`",
        "- Network access performed: `false`",
        "- Transport added: `false`",
        "- External fetch performed: `false`",
        "- Runtime controls added: `false`",
        "- Runtime authority granted: `false`",
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
        "- Rebuilt index-entry check: "
        f"`{verification.get('rebuilt_index_entry_check')}`",
        "- Bridge event schema check: "
        f"`{verification.get('bridge_event_schema_check')}`",
        f"- Template only: `{verification.get('template_only')}`",
        f"- Read-side report only: `{verification.get('read_side_report_only')}`",
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
            "include payloads, record local paths, enqueue scheduler work, skip "
            "gates, change runtime routes, write storage, dispatch solvers, or "
            "grant runtime authority.",
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
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_object,
            parse_constant=reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise SafeInputError("verification_report_decode_error") from exc
    except json.JSONDecodeError as exc:
        raise SafeInputError("verification_report_json_error") from exc
    except ValueError as exc:
        raise SafeInputError("verification_report_json_error") from exc
    if not isinstance(parsed, Mapping):
        raise SafeInputError("verification_report_not_mapping")
    return parsed


def _reject_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _failure_summary(reason: str) -> dict[str, Any]:
    return {
        "proof_id": PROOF_ID,
        "ok": False,
        "summary_version": SUMMARY_VERSION,
        "template_only": True,
        "read_side_report_only": True,
        "manual_review_required": True,
        **{name: False for name in _VERIFICATION_FALSE_FIELDS},
        "blockers": [
            "memory_palace_visualization_export_verification_summary_"
            "bridge_event_template_index_entry_verification_summary_failed:"
            f"{_safe_token(reason)}"
        ],
        "warnings": [],
    }


def _boundary_blockers(report: Mapping[str, Any]) -> list[str]:
    blockers = []
    if report.get("manual_review_required") is not True:
        blockers.append("verification_report_manual_review_required_not_true")
    if report.get("read_side_report_only") is not True:
        blockers.append("verification_report_read_side_report_only_not_true")
    for field in _VERIFICATION_FALSE_FIELDS:
        if report.get(field) is not False:
            blockers.append(f"verification_report_{field}_not_false")
    if report.get("template_only") is not True:
        blockers.append("verification_report_template_only_not_true")
    return sorted(blockers)


def _recursive_boundary_blockers(
    value: Any, *, parent_path: tuple[str, ...] = ()
) -> list[str]:
    blockers: list[str] = []
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = _safe_input_key(raw_key)
            blockers.extend(_input_key_boundary_blockers(key, child, parent_path))
            blockers.extend(
                _recursive_boundary_blockers(
                    child,
                    parent_path=parent_path + (key,),
                )
            )
    elif isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        for child in value:
            blockers.extend(
                _recursive_boundary_blockers(child, parent_path=parent_path)
            )
    return sorted(set(blockers))


def _input_key_boundary_blockers(
    key: str,
    value: Any,
    parent_path: tuple[str, ...],
) -> list[str]:
    blockers: list[str] = []
    if key in _FORBIDDEN_PAYLOAD_KEYS or key.endswith("_payload"):
        blockers.append(f"verification_report_forbidden_payload_key:{key}")
    if (
        key in _FORBIDDEN_PATH_KEYS
        or key.endswith("_path")
        or key.endswith("_paths")
    ):
        blockers.append(f"verification_report_forbidden_path_key:{key}")
    if key in _FORBIDDEN_AUTHORITY_CONTAINER_KEYS:
        blockers.append(f"verification_report_forbidden_authority_container:{key}")
    if (
        parent_path
        and key in _NESTED_AUTHORITY_FALSE_FIELDS
        and value is not False
    ):
        blockers.append(f"verification_report_nested_authority_field_not_false:{key}")
    return blockers


def _verification_report_contract_blockers(report: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    if report.get("ok") is not True:
        blockers.append("verification_report_not_ok")
    if report.get("verification_version") != VERIFICATION_VERSION:
        blockers.append("verification_report_verification_version_mismatch")
    if report.get("index_entry_version") != INDEX_ENTRY_VERSION:
        blockers.append("verification_report_index_entry_version_mismatch")
    if report.get("artifact_count_checked") != len(_REQUIRED_ARTIFACTS):
        blockers.append("verification_report_artifact_count_checked_mismatch")
    if report.get("source_contract_check") != "match":
        blockers.append("verification_report_source_contract_check_not_match")
    if report.get("rebuilt_index_entry_check") != "match":
        blockers.append("verification_report_rebuilt_index_entry_check_not_match")
    if report.get("bridge_event_schema_check") != "match":
        blockers.append("verification_report_bridge_event_schema_check_not_match")
    for check_name in ("digest_checks", "size_checks", "schema_version_checks"):
        checks = _check_statuses(_mapping(report.get(check_name)))
        for artifact_id, status in checks.items():
            if status != "match":
                blockers.append(
                    f"verification_check_not_match:{check_name}:{artifact_id}"
                )
    return sorted(set(blockers))


def _token_list_schema_blockers(report: Mapping[str, Any], field: str) -> list[str]:
    value = report.get(field)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return [f"verification_report_{field}_not_list"]
    blockers: list[str] = []
    for item in value:
        if not isinstance(item, str):
            blockers.append(f"verification_report_{field}_item_not_string")
        elif _safe_token(item) == "invalid_token":
            blockers.append(f"verification_report_{field}_item_unsafe")
    return sorted(set(blockers))


def _check_statuses(raw: Mapping[str, Any]) -> dict[str, str]:
    return {
        artifact_id: _check_status(raw.get(artifact_id))
        for artifact_id in _REQUIRED_ARTIFACTS
    }


def _check_status(value: Any) -> str:
    return value if value in _CHECK_STATUS else "unknown"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_nonnegative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value if value >= 0 else 0


def _safe_ref_or_invalid(value: Any) -> str:
    return (
        value
        if isinstance(value, str)
        and _SAFE_REF_RE.fullmatch(value)
        and not _forbidden_output_markers(value)
        else "invalid_ref"
    )


def _validate_safe_ref(label: str, value: Any) -> None:
    if _safe_ref_or_invalid(value) == "invalid_ref":
        raise SafeInputError(f"{label}_unsafe")


def _safe_token(value: Any) -> str:
    return (
        value
        if isinstance(value, str)
        and _SAFE_TOKEN_RE.fullmatch(value)
        and not _forbidden_output_markers(value)
        else "invalid_token"
    )


def _safe_token_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [_safe_token(item) for item in value if isinstance(item, str)]


def _safe_input_key(value: Any) -> str:
    return value if isinstance(value, str) else "invalid_key"


def _markdown_token_list(value: Any) -> list[str]:
    tokens = _safe_token_list(value)
    if not tokens:
        return ["  - none"]
    return [f"  - `{token}`" for token in tokens]


def _assert_no_forbidden_input(label: str, value: Mapping[str, Any]) -> None:
    try:
        serialized = json.dumps(value, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise SafeInputError(f"{label}_non_finite_json_value") from exc
    if _forbidden_output_markers(serialized):
        raise SafeInputError(f"{label}_forbidden_marker")


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
            "Memory Palace template-index verification summary contains "
            "forbidden markers"
        )


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
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00",
        "Z",
    )


if __name__ == "__main__":
    raise SystemExit(main())
