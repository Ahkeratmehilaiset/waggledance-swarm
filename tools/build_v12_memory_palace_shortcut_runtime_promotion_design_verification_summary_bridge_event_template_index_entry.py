# SPDX-License-Identifier: BUSL-1.1
"""Build a local index entry for Memory Palace bridge-event templates.

The index entry records digest, size, schema, and contract checks for a
verification summary plus its inert bridge-event template. It is local and
path-free: it does not append bridge events, enqueue scheduler work, dispatch
solvers, change routes, include artifact payloads, or grant runtime authority.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_v12_memory_palace_shortcut_runtime_promotion_design_verification_summary_bridge_event_template import (  # noqa: E402
    AUTHORITY_FALSE_FIELDS,
    EVENT_STATUS as SOURCE_EVENT_STATUS,
    REQUIRED_TRUE_FIELDS,
    SOURCE_SUMMARY_VERSION,
    SUMMARY_CHECK_NAMES,
    TEMPLATE_VERSION as SOURCE_TEMPLATE_VERSION,
    VERIFICATION_ARTIFACT_ID,
    build_memory_palace_shortcut_runtime_promotion_design_verification_summary_bridge_event_template,
)
from waggledance.core.bridge_event_schema import validate_event  # noqa: E402


INDEX_ENTRY_VERSION = (
    "wd.v12.memory_palace_shortcut_runtime_promotion_design."
    "verification_summary_bridge_event_template_index_entry.v0"
)
PROOF_ID = (
    "memory_palace_runtime_promotion_design_verification_summary_"
    "bridge_event_template_index_entry_v0"
)
SUMMARY_ARTIFACT_ID = (
    "memory_palace_shortcut_runtime_promotion_design_verification_summary"
)
TEMPLATE_ARTIFACT_ID = (
    "memory_palace_shortcut_runtime_promotion_design_verification_summary_"
    "bridge_event_template"
)


def _joined(*parts: str) -> str:
    return "".join(parts)


def _chars(*codes: int) -> str:
    return "".join(chr(code) for code in codes)


def _slash_wrapped(segment: str) -> str:
    return _joined("/", segment, "/")


SAFE_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,191}$")
WINDOWS_DRIVE_PATH_PATTERN = re.compile(r"(?:^|[^A-Za-z0-9])(?:[A-Za-z]:[\\/])")
BACKSLASH = _chars(92)
SENSITIVE_TOKEN_PREFIX = _chars(80, 82, 73, 86, 65, 84, 69, 95)
PATH_MARKERS = (
    _joined("file", ":", "/", "/"),
    _slash_wrapped("home"),
    _slash_wrapped("python"),
    _slash_wrapped("users"),
    _slash_wrapped("workspace"),
    _slash_wrapped("workspaces"),
    _slash_wrapped("tmp"),
    _joined("waggledance", "-", "agent", "-", "worktrees"),
)
FORBIDDEN_OUTPUT_MARKERS = (
    _joined(_chars(104, 116, 116, 112), ":", "/", "/"),
    _joined(_chars(104, 116, 116, 112, 115), ":", "/", "/"),
    _joined("C", ":", "/"),
    _joined("C", ":", BACKSLASH),
    _joined(BACKSLASH, BACKSLASH),
    _slash_wrapped("home"),
    _slash_wrapped("Users"),
    SENSITIVE_TOKEN_PREFIX,
    _joined("Author", "ization"),
    _joined("Bear", "er", " "),
    _joined("sec", "ret"),
    _joined("pass", "word"),
    _joined("generator", "URL"),
)
TOP_LEVEL_TRUE_FIELDS = (
    "template_only",
    "manual_review_required",
    "operator_gate_required_for_runtime_promotion",
)


class IndexEntryError(ValueError):
    """Raised when index-entry inputs violate the local contract."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verification-summary-json",
        "--summary-json",
        dest="verification_summary_json",
        required=True,
        type=Path,
    )
    parser.add_argument(
        "--bridge-event-template-json",
        "--template-json",
        dest="bridge_event_template_json",
        required=True,
        type=Path,
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override such as 2026-06-08T00:00:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary_bytes, summary = _load_json_artifact(
            args.verification_summary_json,
            SUMMARY_ARTIFACT_ID,
        )
        template_bytes, template_report = _load_json_artifact(
            args.bridge_event_template_json,
            TEMPLATE_ARTIFACT_ID,
        )
        report = build_memory_palace_shortcut_runtime_promotion_design_verification_summary_bridge_event_template_index_entry(
            verification_summary=summary,
            bridge_event_template_report=template_report,
            verification_summary_bytes=summary_bytes,
            bridge_event_template_bytes=template_bytes,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except IndexEntryError as exc:
        report = _failure_report(exc.code)
    except ValueError:
        report = _failure_report("bridge_event_template_index_entry_invalid")

    encoded = json.dumps(report, indent=2, sort_keys=True, allow_nan=False)
    if args.json or report["ok"]:
        print(encoded)
    else:
        print(
            "Memory Palace verification-summary bridge-template index entry "
            "FAILED: " + ", ".join(report["blockers"]),
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_memory_palace_shortcut_runtime_promotion_design_verification_summary_bridge_event_template_index_entry(
    *,
    verification_summary: Mapping[str, Any],
    bridge_event_template_report: Mapping[str, Any],
    verification_summary_bytes: bytes,
    bridge_event_template_bytes: bytes,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return a path-free local index entry for a bridge-event template."""

    _assert_mapping(SUMMARY_ARTIFACT_ID, verification_summary)
    _assert_mapping(TEMPLATE_ARTIFACT_ID, bridge_event_template_report)
    _assert_no_forbidden_input(SUMMARY_ARTIFACT_ID, verification_summary)
    _assert_no_forbidden_input(TEMPLATE_ARTIFACT_ID, bridge_event_template_report)
    _assert_bytes_match_artifact(
        SUMMARY_ARTIFACT_ID,
        verification_summary,
        verification_summary_bytes,
    )
    _assert_bytes_match_artifact(
        TEMPLATE_ARTIFACT_ID,
        bridge_event_template_report,
        bridge_event_template_bytes,
    )
    _assert_summary_contract(verification_summary)

    rebuilt = _rebuilt_template(verification_summary, bridge_event_template_report)
    if _deterministic_artifact(rebuilt) != _deterministic_artifact(
        bridge_event_template_report
    ):
        raise IndexEntryError("bridge_event_template_rebuilt_mismatch")

    _assert_template_contract(bridge_event_template_report, verification_summary)

    summary_digest = _sha256_hex(verification_summary_bytes)
    template_digest = _sha256_hex(bridge_event_template_bytes)
    event = _mapping(bridge_event_template_report.get("bridge_event_template"))
    payload = _mapping(event.get("payload"))
    summary_payload = _mapping(
        payload.get("memory_palace_runtime_promotion_design_verification_summary")
    )
    artifacts = (
        _artifact_record(
            artifact_id=SUMMARY_ARTIFACT_ID,
            role="verified_memory_palace_runtime_promotion_design_context",
            artifact=verification_summary,
            raw=verification_summary_bytes,
        ),
        _artifact_record(
            artifact_id=TEMPLATE_ARTIFACT_ID,
            role="template_only_bridge_handoff_context",
            artifact=bridge_event_template_report,
            raw=bridge_event_template_bytes,
        ),
    )
    entry = {
        "proof_id": PROOF_ID,
        "ok": True,
        "index_entry_version": INDEX_ENTRY_VERSION,
        "created_at_utc": _utc_iso(now_utc or datetime.now(timezone.utc)),
        "summary_version": SOURCE_SUMMARY_VERSION,
        "template_version": SOURCE_TEMPLATE_VERSION,
        "verification_artifact_id": VERIFICATION_ARTIFACT_ID,
        "artifact_count": len(artifacts),
        "artifacts": list(artifacts),
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
            "runtime_promotion_design_count_checked": verification_summary.get(
                "runtime_promotion_design_count_checked",
            ),
            "summary_payload_check_count": summary_payload.get("check_count"),
            "manual_review_required": True,
            "operator_gate_required_for_runtime_promotion": True,
            "approval_granted": False,
            "release_decision_made": False,
            "automatic_release_decision": False,
            "promotion_action_allowed": False,
            "promotion_performed": False,
            "runtime_route_changed": False,
            "storage_write_performed": False,
            "bridge_append_performed": False,
            "direct_bridge_write_performed": False,
            "solver_call_performed": False,
            "scheduler_enqueue_performed": False,
            "gate_skip_performed": False,
            "network_access_performed": False,
            "transport_added": False,
            "external_fetch_performed": False,
            "external_writes_applied": False,
            "runtime_authority_granted": False,
            "artifact_payloads_included": False,
            "local_paths_recorded": False,
        },
        "verification_summary": {
            "source_verification_ok": True,
            "runtime_promotion_design_count_checked": verification_summary.get(
                "runtime_promotion_design_count_checked",
            ),
            "blocker_count": 0,
            "warning_count": len(_safe_token_list(verification_summary.get("warnings"))),
            "summary_check_count": len(SUMMARY_CHECK_NAMES),
        },
        "consistency": {
            "required_artifacts_present": [SUMMARY_ARTIFACT_ID, TEMPLATE_ARTIFACT_ID],
            "all_artifact_digests_recorded": True,
            "bridge_event_schema_validated": True,
            "source_contract_check": "match",
            "rebuilt_template_check": "match",
            "template_only": True,
            "summary_ok": True,
            "source_verification_ok": True,
            "artifact_payloads_included": False,
            "local_paths_recorded": False,
        },
        "operator_boundary": {
            "manual_review_required": True,
            "operator_gate_required_for_runtime_promotion": True,
            "approval_granted": False,
            "release_decision_made": False,
            "automatic_release_decision": False,
            "promotion_action_allowed": False,
            "promotion_performed": False,
            "runtime_route_changed": False,
            "storage_write_performed": False,
            "bridge_append_performed": False,
            "direct_bridge_write_performed": False,
            "solver_call_performed": False,
            "scheduler_enqueue_performed": False,
            "gate_skip_performed": False,
            "network_access_performed": False,
            "transport_added": False,
            "external_fetch_performed": False,
            "external_writes_applied": False,
            "runtime_authority_granted": False,
            "artifact_payloads_included": False,
            "local_paths_recorded": False,
        },
        "reviewer_next_actions": [
            "review_memory_palace_verification_summary_bridge_event_template_index_entry",
            "append_bridge_event_separately_only_after_manual_review",
        ],
        "template_only": True,
        "manual_review_required": True,
        "operator_gate_required_for_runtime_promotion": True,
        "approval_granted": False,
        "release_decision_made": False,
        "automatic_release_decision": False,
        "promotion_action_allowed": False,
        "promotion_performed": False,
        "runtime_route_changed": False,
        "storage_write_performed": False,
        "bridge_append_performed": False,
        "direct_bridge_write_performed": False,
        "solver_call_performed": False,
        "scheduler_enqueue_performed": False,
        "gate_skip_performed": False,
        "network_access_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "external_writes_applied": False,
        "controls_present": False,
        "runtime_authority_granted": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": [],
        "warnings": _safe_token_list(bridge_event_template_report.get("warnings")),
    }
    _assert_no_forbidden_output(json.dumps(entry, allow_nan=False, sort_keys=True))
    return entry


def _rebuilt_template(
    summary: Mapping[str, Any],
    template_report: Mapping[str, Any],
) -> Mapping[str, Any]:
    event = _mapping(template_report.get("bridge_event_template"))
    if not event:
        raise IndexEntryError("bridge_event_template_missing")
    try:
        validate_event(event)
    except Exception as exc:
        raise IndexEntryError("bridge_event_template_schema_invalid") from exc
    return build_memory_palace_shortcut_runtime_promotion_design_verification_summary_bridge_event_template(
        summary=summary,
        agent_id=_required_string(event.get("agent"), "bridge_event_template_agent_invalid"),
        task_id=_required_string(
            event.get("task_id"),
            "bridge_event_template_task_id_invalid",
        ),
        to=_required_string(event.get("to"), "bridge_event_template_to_invalid"),
        severity=_required_severity(event.get("severity")),
        role=_required_string(event.get("role"), "bridge_event_template_role_invalid"),
        run_id=_optional_string(event.get("run_id")),
        session_id=_optional_string(event.get("session_id")),
        now_utc=_parse_utc(
            _required_string(event.get("ts_utc"), "bridge_event_template_ts_invalid")
        ),
    )


def _assert_template_contract(
    template_report: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> None:
    if template_report.get("ok") is not True:
        raise IndexEntryError("bridge_event_template_not_ok")
    if template_report.get("template_version") != SOURCE_TEMPLATE_VERSION:
        raise IndexEntryError("bridge_event_template_version_mismatch")
    if template_report.get("template_only") is not True:
        raise IndexEntryError("bridge_event_template_template_only_not_true")
    if template_report.get("manual_review_required") is not True:
        raise IndexEntryError("bridge_event_template_manual_review_not_true")
    if template_report.get("operator_gate_required_for_runtime_promotion") is not True:
        raise IndexEntryError("bridge_event_template_operator_gate_not_true")
    _expect_empty_items(template_report.get("blockers"), "bridge_event_template_blockers_present")
    _expect_authority_false(template_report, "bridge_event_template")

    event = _mapping(template_report.get("bridge_event_template"))
    if event.get("type") != "handoff":
        raise IndexEntryError("bridge_event_template_type_mismatch")
    if event.get("status") != SOURCE_EVENT_STATUS:
        raise IndexEntryError("bridge_event_template_status_mismatch")
    if event.get("paths") != []:
        raise IndexEntryError("bridge_event_template_paths_not_empty")
    if event.get("write_scope") != []:
        raise IndexEntryError("bridge_event_template_write_scope_not_empty")
    if event.get("pid") != 0:
        raise IndexEntryError("bridge_event_template_pid_not_zero")
    if event.get("cwd") != "template_not_emitted":
        raise IndexEntryError("bridge_event_template_cwd_mismatch")

    payload = _mapping(event.get("payload"))
    if payload.get("schema_version") != SOURCE_TEMPLATE_VERSION:
        raise IndexEntryError("bridge_event_template_payload_schema_mismatch")
    if payload.get("source_summary_version") != SOURCE_SUMMARY_VERSION:
        raise IndexEntryError("bridge_event_template_payload_summary_version_mismatch")
    if payload.get("verification_artifact_id") != VERIFICATION_ARTIFACT_ID:
        raise IndexEntryError("bridge_event_template_payload_artifact_id_mismatch")
    for field in TOP_LEVEL_TRUE_FIELDS:
        if payload.get(field) is not True:
            raise IndexEntryError(f"bridge_event_template_payload_{field}_not_true")
    _expect_authority_false(payload, "bridge_event_template_payload")
    _expect_authority_false(
        _mapping(payload.get("operator_boundary")),
        "bridge_event_template_boundary",
    )
    verification = _mapping(
        payload.get("memory_palace_runtime_promotion_design_verification_summary")
    )
    if verification.get("summary_ok") is not True:
        raise IndexEntryError("bridge_event_template_summary_not_ok")
    if verification.get("source_verification_ok") is not True:
        raise IndexEntryError("bridge_event_template_source_verification_not_ok")
    if verification.get("payload_included") is not False:
        raise IndexEntryError("bridge_event_template_summary_payload_included")
    if verification.get("blocker_count") != 0:
        raise IndexEntryError("bridge_event_template_summary_blockers_present")
    if verification.get("runtime_promotion_design_count_checked") != summary.get(
        "runtime_promotion_design_count_checked",
    ):
        raise IndexEntryError("bridge_event_template_summary_design_count_mismatch")
    if verification.get("check_count") != len(SUMMARY_CHECK_NAMES):
        raise IndexEntryError("bridge_event_template_summary_check_count_mismatch")
    _expect_bool_map_all_true(
        verification.get("checks"),
        SUMMARY_CHECK_NAMES,
        "bridge_event_template_summary_checks",
    )
    _expect_bool_map_all_true(
        verification.get("required_true_flags"),
        REQUIRED_TRUE_FIELDS,
        "bridge_event_template_summary_required_true",
    )
    _expect_bool_map_all_true(
        verification.get("authority_boundary"),
        AUTHORITY_FALSE_FIELDS,
        "bridge_event_template_summary_authority_boundary",
    )


def _assert_summary_contract(summary: Mapping[str, Any]) -> None:
    blockers: list[str] = []
    if summary.get("summary_version") != SOURCE_SUMMARY_VERSION:
        blockers.append("verification_summary_version_mismatch")
    if summary.get("ok") is not True:
        blockers.append("verification_summary_not_ok")
    if summary.get("source_verification_ok") is not True:
        blockers.append("verification_summary_source_verification_not_ok")
    _collect_empty_list_blockers(
        summary.get("blockers"),
        "verification_summary_blockers_invalid",
        "verification_summary_blockers_present",
        blockers,
    )
    design_count = summary.get("runtime_promotion_design_count_checked")
    if (
        isinstance(design_count, bool)
        or not isinstance(design_count, int)
        or design_count < 0
    ):
        blockers.append("verification_summary_design_count_invalid")

    checks = _mapping(summary.get("checks"))
    for name in SUMMARY_CHECK_NAMES:
        if checks.get(name) != "match":
            blockers.append(f"verification_summary_check_{name}_not_match")

    required_true = _mapping(summary.get("required_true_flags"))
    for field in REQUIRED_TRUE_FIELDS:
        if required_true.get(field) is not True:
            blockers.append(f"verification_summary_{field}_not_true")
        if summary.get(field) is not True:
            blockers.append(f"verification_summary_top_level_{field}_not_true")

    authority_boundary = _mapping(summary.get("authority_boundary"))
    for field in AUTHORITY_FALSE_FIELDS:
        if authority_boundary.get(field) is not False:
            blockers.append(f"verification_summary_{field}_not_false")
        if summary.get(field) is not False:
            blockers.append(f"verification_summary_top_level_{field}_not_false")

    warnings = summary.get("warnings")
    if not isinstance(warnings, list) or any(
        not isinstance(item, str) for item in warnings
    ):
        blockers.append("verification_summary_warnings_invalid")
    if blockers:
        raise IndexEntryError(sorted(set(blockers))[0])


def _load_json_artifact(path: Path, label: str) -> tuple[bytes, Mapping[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise IndexEntryError(f"{label}_unreadable") from exc

    def reject_constant(value: str) -> None:
        raise ValueError(f"non_finite_json_constant:{value}")

    try:
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_object,
            parse_constant=reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise IndexEntryError(f"{label}_decode_error") from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise IndexEntryError(f"{label}_json_error") from exc
    if not isinstance(parsed, Mapping):
        raise IndexEntryError(f"{label}_not_object")
    if _contains_non_finite(parsed):
        raise IndexEntryError(f"{label}_json_error")
    return raw, parsed


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
    schema_version = (
        artifact.get("summary_version")
        or artifact.get("schema_version")
        or artifact.get("template_version")
        or artifact.get("index_entry_version")
    )
    return {
        "artifact_id": artifact_id,
        "role": role,
        "sha256": _sha256_hex(raw),
        "size_bytes": len(raw),
        "json_schema_version": schema_version,
        "payload_included": False,
        "local_path_recorded": False,
    }


def _assert_mapping(label: str, value: Any) -> None:
    if not isinstance(value, Mapping):
        raise IndexEntryError(f"{label}_not_object")


def _assert_bytes_match_artifact(
    label: str,
    artifact: Mapping[str, Any],
    raw: bytes,
) -> None:
    try:
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise IndexEntryError(f"{label}_bytes_invalid") from exc
    if _contains_non_finite(parsed):
        raise IndexEntryError(f"{label}_bytes_invalid")
    if _deterministic_artifact(parsed) != _deterministic_artifact(artifact):
        raise IndexEntryError(f"{label}_bytes_mismatch")


def _assert_no_forbidden_input(label: str, artifact: Mapping[str, Any]) -> None:
    try:
        text = json.dumps(artifact, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise IndexEntryError(f"{label}_json_error") from exc
    if _contains_non_finite(artifact):
        raise IndexEntryError(f"{label}_json_error")
    if _contains_path_marker(artifact) or _forbidden_output_markers(text):
        raise IndexEntryError(f"{label}_not_path_free")


def _expect_empty_items(value: Any, code: str) -> None:
    if value != []:
        raise IndexEntryError(code)


def _collect_empty_list_blockers(
    value: Any,
    invalid_code: str,
    present_code: str,
    blockers: list[str],
) -> None:
    if not isinstance(value, list):
        blockers.append(invalid_code)
    elif any(not isinstance(item, str) for item in value):
        blockers.append(invalid_code)
    elif value:
        blockers.append(present_code)


def _expect_authority_false(payload: Mapping[str, Any], prefix: str) -> None:
    for field in AUTHORITY_FALSE_FIELDS:
        if field in payload and payload.get(field) is not False:
            raise IndexEntryError(f"{prefix}_{field}_not_false")


def _expect_bool_map_all_true(value: Any, keys: Sequence[str], prefix: str) -> None:
    payload = _mapping(value)
    for key in keys:
        if payload.get(key) is not True:
            raise IndexEntryError(f"{prefix}_{key}_not_true")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _safe_token(value: Any, fallback: str = "invalid_token") -> str:
    if isinstance(value, str) and SAFE_TOKEN_PATTERN.fullmatch(value):
        return value
    return fallback


def _safe_token_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [_safe_token(item) for item in value if isinstance(item, str)]


def _required_string(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value:
        raise IndexEntryError(code)
    return value


def _optional_string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _required_severity(value: Any) -> str:
    if value not in {"", "low", "medium", "high"}:
        raise IndexEntryError("bridge_event_template_severity_invalid")
    return str(value)


def _contains_non_finite(value: Any) -> bool:
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, Mapping):
        return any(_contains_non_finite(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_non_finite(item) for item in value)
    return False


def _contains_path_marker(value: Any) -> bool:
    if isinstance(value, str):
        normalized = value.replace("\\", "/").lower()
        return (
            WINDOWS_DRIVE_PATH_PATTERN.search(value) is not None
            or normalized.startswith("//")
            or any(marker in normalized for marker in PATH_MARKERS)
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


def _assert_no_forbidden_output(text: str) -> None:
    found = _forbidden_output_markers(text)
    if found:
        raise IndexEntryError("bridge_event_template_index_entry_not_path_free")


def _deterministic_artifact(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _parse_utc(raw: str) -> datetime:
    if not raw.endswith("Z"):
        raise IndexEntryError("now_utc_unsafe")
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as exc:
        raise IndexEntryError("now_utc_unsafe") from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != timezone.utc.utcoffset(parsed)
    ):
        raise IndexEntryError("now_utc_unsafe")
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


def _failure_report(reason: str) -> dict[str, Any]:
    return {
        "proof_id": PROOF_ID,
        "ok": False,
        "index_entry_version": INDEX_ENTRY_VERSION,
        "template_only": True,
        "manual_review_required": True,
        "operator_gate_required_for_runtime_promotion": True,
        "approval_granted": False,
        "release_decision_made": False,
        "automatic_release_decision": False,
        "promotion_action_allowed": False,
        "promotion_performed": False,
        "runtime_route_changed": False,
        "storage_write_performed": False,
        "bridge_append_performed": False,
        "direct_bridge_write_performed": False,
        "solver_call_performed": False,
        "scheduler_enqueue_performed": False,
        "gate_skip_performed": False,
        "network_access_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "external_writes_applied": False,
        "controls_present": False,
        "runtime_authority_granted": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": [
            "memory_palace_shortcut_runtime_promotion_design_verification_summary_"
            f"bridge_event_template_index_entry_failed:{_safe_token(reason)}"
        ],
        "warnings": [],
    }


if __name__ == "__main__":
    raise SystemExit(main())
