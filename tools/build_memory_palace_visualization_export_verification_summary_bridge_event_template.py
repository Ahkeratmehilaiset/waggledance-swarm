# SPDX-License-Identifier: BUSL-1.1
"""Build a bridge-event template for visualization-export verification summaries.

The template is deliberately inert: it validates a path-free Memory Palace
visualization-export verification summary and returns a schema-valid bridge
event that another operator or agent can review. It does not append bridge
events, enqueue schedulers, change routes, include artifact payloads, or grant
runtime, storage, bridge, promotion, network, or gate-skip authority.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_memory_palace_visualization_export_verification_summary import (  # noqa: E402
    SUMMARY_VERSION as SOURCE_SUMMARY_VERSION,
    VERIFICATION_ARTIFACT_ID,
)
from waggledance.core.bridge_event_schema import validate_event  # noqa: E402


TEMPLATE_VERSION = (
    "wd.v12.memory_palace_visualization_export."
    "verification_summary_bridge_event_template.v0"
)
EVENT_STATUS = "memory_palace_visualization_export_verification_summary_ready"
PROOF_ID = (
    "memory_palace_visualization_export_verification_summary_"
    "bridge_event_template_v0"
)


def _joined(*parts: str) -> str:
    return "".join(parts)


def _chars(*codes: int) -> str:
    return "".join(chr(code) for code in codes)


def _slash_wrapped(segment: str) -> str:
    return _joined("/", segment, "/")


AGENT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,32}$")
SAFE_REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._/-]{0,191}$")
SAFE_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,191}$")
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
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
SUMMARY_CHECK_NAMES = (
    "source_export_version_check",
    "source_claim_label_check",
    "source_of_truth_check",
    "source_projection_schema_version_check",
    "layout_check",
    "graph_check",
    "aggregate_check",
    "authority_boundary_check",
    "guardrail_check",
    "path_free_check",
)
SUMMARY_COUNT_NAMES = (
    "node_count_checked",
    "edge_count_checked",
    "shortcut_edge_count_checked",
)
REQUIRED_TRUE_FIELDS = (
    "read_side_report_only",
    "manual_review_required",
)
AUTHORITY_FALSE_FIELDS = (
    "runtime_route_changed",
    "storage_write_performed",
    "bridge_append_performed",
    "solver_call_performed",
    "scheduler_enqueue_performed",
    "promotion_performed",
    "gate_skip_performed",
    "network_access_performed",
    "runtime_authority_granted",
    "artifact_payloads_included",
    "local_paths_recorded",
)
EXTRA_TEMPLATE_FALSE_FIELDS = (
    "approval_granted",
    "release_decision_made",
    "automatic_release_decision",
    "direct_bridge_write_performed",
    "transport_added",
    "external_fetch_performed",
    "external_writes_applied",
)
_ALLOWED_SUMMARY_FIELDS = frozenset(
    {
        "summary_version",
        "ok",
        "source_verification_version",
        "source_verification_ok",
        "checks",
        "counts",
        "required_true_flags",
        "authority_boundary",
        "blocker_count",
        "warning_count",
        "blockers",
        "warnings",
        "operator_interpretation",
        *REQUIRED_TRUE_FIELDS,
        *AUTHORITY_FALSE_FIELDS,
    }
)


class SafeInputError(ValueError):
    """Raised when a local summary or template input cannot be trusted."""

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
    parser.add_argument("--agent", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument(
        "--to",
        default="operator,codex-lead-1,claude-rco-1,claude-rco-2",
        help="Comma-separated bridge targets for the template.",
    )
    parser.add_argument(
        "--severity",
        default="medium",
        choices=("", "low", "medium", "high"),
    )
    parser.add_argument("--role", default="tools-tests")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--session-id", default="")
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
        summary = _load_json_report(args.verification_summary_json)
        report = build_memory_palace_visualization_export_verification_summary_bridge_event_template(
            summary=summary,
            agent_id=args.agent,
            task_id=args.task_id,
            to=args.to,
            severity=args.severity,
            role=args.role,
            run_id=args.run_id,
            session_id=args.session_id,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except SafeInputError as exc:
        report = _bridge_template_error_report(exc.code)
    except ValueError:
        report = _bridge_template_error_report("bridge_event_template_invalid")

    encoded = json.dumps(report, indent=2, sort_keys=True, allow_nan=False)
    if args.json:
        print(encoded)
    elif report["ok"]:
        print(
            json.dumps(
                report["bridge_event_template"],
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
        )
    else:
        print(
            "Memory Palace visualization export verification summary "
            "bridge-event template FAILED: "
            + ", ".join(report["blockers"]),
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_memory_palace_visualization_export_verification_summary_bridge_event_template(
    *,
    summary: Mapping[str, Any],
    agent_id: str,
    task_id: str,
    to: str = "operator,codex-lead-1,claude-rco-1,claude-rco-2",
    severity: str = "medium",
    role: str = "tools-tests",
    run_id: str = "",
    session_id: str = "",
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return a schema-valid bridge-event template without appending it."""

    if not isinstance(summary, Mapping):
        return _bridge_template_error_report("verification_summary_not_object")
    try:
        summary_text = json.dumps(summary, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        return _bridge_template_error_report("verification_summary_not_json_safe")
    if _contains_non_finite(summary):
        return _bridge_template_error_report("verification_summary_contains_non_finite")
    if _contains_path_marker(summary) or _forbidden_output_markers(summary_text):
        return _bridge_template_error_report("verification_summary_not_path_free")

    input_error = _bridge_template_input_error(
        agent_id=agent_id,
        task_id=task_id,
        to=to,
        severity=severity,
        role=role,
        run_id=run_id,
        session_id=session_id,
    )
    if input_error is not None:
        return _bridge_template_error_report(input_error)
    targets, _ = _validate_bridge_targets(to)

    contract_blockers = _verification_summary_contract_blockers(summary)
    if contract_blockers:
        return _bridge_template_error_report(contract_blockers[0])

    checks = _mapping(summary.get("checks"))
    counts = _mapping(summary.get("counts"))
    required_true = _mapping(summary.get("required_true_flags"))
    authority_boundary = _mapping(summary.get("authority_boundary"))
    warnings = _safe_token_list(summary.get("warnings"))
    payload = {
        "schema_version": TEMPLATE_VERSION,
        "source_summary_version": SOURCE_SUMMARY_VERSION,
        "verification_artifact_id": VERIFICATION_ARTIFACT_ID,
        "memory_palace_visualization_export_verification_summary": {
            "summary_ok": True,
            "source_verification_ok": True,
            "check_count": len(SUMMARY_CHECK_NAMES),
            "checks": {
                name: checks.get(name) == "match" for name in SUMMARY_CHECK_NAMES
            },
            "counts": {
                name: _as_nonnegative_int(counts.get(name))
                for name in SUMMARY_COUNT_NAMES
            },
            "required_true_flags": {
                name: required_true.get(name) is True
                for name in REQUIRED_TRUE_FIELDS
            },
            "authority_boundary": {
                name: authority_boundary.get(name) is False
                for name in AUTHORITY_FALSE_FIELDS
            },
            "blocker_count": 0,
            "warning_count": len(warnings),
            "warnings": warnings,
            "payload_included": False,
        },
        "operator_boundary": {
            "manual_review_required": True,
            "approval_granted": False,
            "release_decision_made": False,
            "automatic_release_decision": False,
            "runtime_authority_granted": False,
            "runtime_route_changed": False,
            "scheduler_enqueue_performed": False,
            "gate_skip_performed": False,
        },
        "template_only": True,
        "manual_review_required": True,
        **{name: False for name in AUTHORITY_FALSE_FIELDS},
        **{name: False for name in EXTRA_TEMPLATE_FALSE_FIELDS},
    }
    event = {
        "ts_utc": _utc_iso(now_utc or datetime.now(timezone.utc)),
        "agent": agent_id,
        "type": "handoff",
        "task_id": task_id,
        "status": EVENT_STATUS,
        "severity": severity,
        "to": targets,
        "message": (
            "Memory Palace visualization export verification summary "
            "bridge-event template ready; manual_review_required=true; "
            "approval_granted=false; release_decision_made=false; "
            "template_only=true; no bridge write, scheduler enqueue, route "
            "change, gate skip, payload inclusion, local path recording, "
            "network access, storage write, or runtime authority grant."
        ),
        "paths": [],
        "write_scope": [],
        "run_id": run_id,
        "role": role,
        "session_id": session_id,
        "capabilities": [
            "tools",
            "memory_palace",
            "bridge_event",
        ],
        "pid": 0,
        "cwd": "template_not_emitted",
        "payload": payload,
    }
    if _contains_path_marker(event):
        return _bridge_template_error_report("bridge_event_template_path_marker")
    try:
        _assert_no_forbidden_output(json.dumps(event, allow_nan=False, sort_keys=True))
        validate_event(event)
    except Exception:
        return _bridge_template_error_report("bridge_event_schema_invalid")
    return {
        "proof_id": PROOF_ID,
        "ok": True,
        "template_version": TEMPLATE_VERSION,
        "bridge_event_template": event,
        "template_only": True,
        "manual_review_required": True,
        **{name: False for name in AUTHORITY_FALSE_FIELDS},
        **{name: False for name in EXTRA_TEMPLATE_FALSE_FIELDS},
        "blockers": [],
        "warnings": warnings,
    }


def _verification_summary_contract_blockers(summary: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    if set(summary) - _ALLOWED_SUMMARY_FIELDS:
        blockers.append("verification_summary_unexpected_field_present")
    if summary.get("summary_version") != SOURCE_SUMMARY_VERSION:
        blockers.append("verification_summary_version_mismatch")
    if summary.get("ok") is not True:
        blockers.append("verification_summary_not_ok")
    if summary.get("source_verification_ok") is not True:
        blockers.append("verification_summary_source_verification_not_ok")

    raw_blockers = summary.get("blockers")
    if not isinstance(raw_blockers, list):
        blockers.append("verification_summary_blockers_invalid")
    elif any(not isinstance(item, str) for item in raw_blockers):
        blockers.append("verification_summary_blockers_invalid")
    elif raw_blockers:
        blockers.append("verification_summary_blockers_present")

    raw_warnings = summary.get("warnings")
    if not isinstance(raw_warnings, list):
        blockers.append("verification_summary_warnings_invalid")
    elif any(not isinstance(item, str) for item in raw_warnings):
        blockers.append("verification_summary_warnings_invalid")

    counts = _mapping(summary.get("counts"))
    for name in SUMMARY_COUNT_NAMES:
        if _as_nonnegative_int(counts.get(name)) < 0:
            blockers.append(f"verification_summary_{name}_invalid")

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

    return sorted(set(blockers))


def _load_json_report(path: Path) -> Mapping[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SafeInputError("verification_summary_unreadable") from exc

    def reject_constant(value: str) -> None:
        raise ValueError(f"non_finite_json_constant:{value}")

    try:
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_object,
            parse_constant=reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise SafeInputError("verification_summary_decode_error") from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise SafeInputError("verification_summary_json_error") from exc
    if not isinstance(parsed, Mapping):
        raise SafeInputError("verification_summary_not_object")
    if _contains_non_finite(parsed):
        raise SafeInputError("verification_summary_json_error")
    return parsed


def _reject_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _bridge_template_error_report(reason: str) -> dict[str, Any]:
    return {
        "proof_id": PROOF_ID,
        "ok": False,
        "template_version": TEMPLATE_VERSION,
        "template_only": True,
        "manual_review_required": True,
        **{name: False for name in AUTHORITY_FALSE_FIELDS},
        **{name: False for name in EXTRA_TEMPLATE_FALSE_FIELDS},
        "blockers": [
            "memory_palace_visualization_export_verification_summary_"
            f"bridge_event_template_failed:{_safe_token(reason)}"
        ],
        "warnings": [],
    }


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


def _as_nonnegative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return -1
    return value if value >= 0 else -1


def _validate_bridge_agent_id(label: str, value: Any) -> str | None:
    if isinstance(value, str) and AGENT_ID_PATTERN.fullmatch(value):
        return None
    return f"{label}_unsafe"


def _validate_bridge_targets(raw_targets: Any) -> tuple[str, str | None]:
    if not isinstance(raw_targets, str):
        return "", "to_unsafe"
    targets = [item.strip() for item in raw_targets.split(",") if item.strip()]
    if not targets:
        return "", "to_unsafe"
    for target in targets:
        error = _validate_bridge_agent_id("to", target)
        if error is not None:
            return "", error
    return ",".join(targets), None


def _bridge_template_input_error(
    *,
    agent_id: str,
    task_id: str,
    to: str,
    severity: str,
    role: str,
    run_id: str,
    session_id: str,
) -> str | None:
    error = _validate_bridge_agent_id("agent", agent_id)
    if error is not None:
        return error
    if not isinstance(task_id, str) or not SAFE_REF_PATTERN.fullmatch(task_id):
        return "task_id_unsafe"
    _, target_error = _validate_bridge_targets(to)
    if target_error is not None:
        return target_error
    if severity not in {"", "low", "medium", "high"}:
        return "severity_unsafe"
    if role:
        error = _validate_bridge_agent_id("role", role)
        if error is not None:
            return error
    if run_id and not SAFE_REF_PATTERN.fullmatch(run_id):
        return "run_id_unsafe"
    if session_id and not SESSION_ID_PATTERN.fullmatch(session_id):
        return "session_id_unsafe"
    return None


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
        raise ValueError(
            "sanitized bridge-event template contains forbidden markers: "
            + ", ".join(found)
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
    normalized = value.astimezone(timezone.utc).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
