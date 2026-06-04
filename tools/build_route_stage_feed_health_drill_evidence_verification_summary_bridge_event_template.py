#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build a route-stage feed-health drill verification bridge-event template."""

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

from tools.verify_route_stage_feed_health_drill_evidence import (  # noqa: E402
    PACKAGE_SCHEMA_VERSION,
    REQUIRED_API_OPS_FIELDS,
    REQUIRED_METRICS_FIELDS,
    VERIFICATION_SCHEMA_VERSION,
)
from waggledance.core.bridge_event_schema import validate_event  # noqa: E402


TEMPLATE_VERSION = (
    "waggledance.route_stage_feed_health_drill_evidence_verification_summary_"
    "bridge_event_template.v1"
)
EVENT_STATUS = (
    "route_stage_feed_health_drill_evidence_verification_summary_ready"
)
PROOF_ID = (
    "route_stage_feed_health_drill_evidence_verification_summary_"
    "bridge_event_template_v1"
)


def _joined(*parts: str) -> str:
    return "".join(parts)


def _chars(*codes: int) -> str:
    return "".join(chr(code) for code in codes)


def _slash_wrapped(segment: str) -> str:
    return _joined("/", segment, "/")


AGENT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,32}$")
SAFE_REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,191}$")
SAFE_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,191}$")
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
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
VERIFICATION_CHECK_NAMES = (
    "schema_version_ok",
    "top_level_contract_ok",
    "metrics_scrape_contract_ok",
    "api_ops_contract_ok",
    "feed_health_contract_ok",
    "slo_panels_contract_ok",
    "drill_evidence_contract_ok",
    "operator_log_window_contract_ok",
    "authority_guardrails_ok",
    "no_forbidden_raw_payload",
    "offline_only",
)
AUTHORITY_FALSE_FIELDS = (
    "controls_present",
    "runtime_authority_granted",
    "external_writes_applied",
    "network_access_performed",
)


class SafeInputError(ValueError):
    """Raised when local JSON or bridge template inputs are unsafe."""

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
        default="operator,claude-rco-1,codex-tools-1",
        help="Comma-separated bridge targets for the template.",
    )
    parser.add_argument(
        "--severity",
        default="medium",
        choices=("", "low", "medium", "high"),
    )
    parser.add_argument("--role", default="lead-impl")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--session-id", default="")
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override such as 2026-06-04T00:00:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = _load_json_report(args.verification_summary_json)
        report = build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template(
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
            "route-stage feed-health drill evidence verification summary "
            "bridge-event template FAILED: "
            + ", ".join(report["blockers"]),
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template(
    *,
    summary: Mapping[str, Any],
    agent_id: str,
    task_id: str,
    to: str = "operator,claude-rco-1,codex-tools-1",
    severity: str = "medium",
    role: str = "lead-impl",
    run_id: str = "",
    session_id: str = "",
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return a bridge-event template without appending it."""

    if not isinstance(summary, Mapping):
        return _bridge_template_error_report("verification_summary_not_object")
    if _contains_path_marker(summary) or _forbidden_output_markers(
        json.dumps(summary, sort_keys=True, default=str)
    ):
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
    required_artifacts = _mapping(summary.get("required_artifacts"))
    warnings = _safe_token_list(summary.get("warnings"))
    evidence_size = _as_nonnegative_int(summary.get("evidence_size_bytes"))
    evidence_sha256 = str(summary.get("evidence_sha256"))
    payload = {
        "schema_version": TEMPLATE_VERSION,
        "verification_schema_version": VERIFICATION_SCHEMA_VERSION,
        "package_schema_version": PACKAGE_SCHEMA_VERSION,
        "evidence_sha256": evidence_sha256,
        "evidence_size_bytes": evidence_size,
        "route_stage_feed_health_drill_evidence_verification": {
            "verification_ok": True,
            "verified": True,
            "check_count": len(VERIFICATION_CHECK_NAMES),
            "checks": {name: checks.get(name) is True for name in VERIFICATION_CHECK_NAMES},
            "required_artifact_counts": {
                "metrics_scrape": len(_string_sequence(required_artifacts.get("metrics_scrape"))),
                "api_ops": len(_string_sequence(required_artifacts.get("api_ops"))),
                "operator_log_window": len(
                    _string_sequence(required_artifacts.get("operator_log_window"))
                ),
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
            "Route-stage feed-health drill evidence verification summary "
            "bridge-event template ready; manual_review_required=true; "
            "approval_granted=false; release_decision_made=false; "
            "template_only=true; no bridge write, transport, external fetch, "
            "payload inclusion, local path recording, runtime controls, or "
            "authority grant."
        ),
        "paths": [],
        "write_scope": [],
        "run_id": run_id,
        "role": role,
        "session_id": session_id,
        "capabilities": [
            "wd_image1",
            "route_stage_feed",
            "bridge_event",
        ],
        "pid": 0,
        "cwd": "template_not_emitted",
        "payload": payload,
    }
    if _contains_path_marker(event):
        return _bridge_template_error_report("bridge_event_template_path_marker")
    _assert_no_forbidden_output(json.dumps(event, allow_nan=False, sort_keys=True))
    try:
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
        "direct_bridge_write_performed": False,
        "automatic_release_decision": False,
        "approval_granted": False,
        "release_decision_made": False,
        "runtime_controls_added": False,
        "controls_present": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
        "network_access_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": [],
        "warnings": warnings,
    }


def _verification_summary_contract_blockers(summary: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    if summary.get("schema_version") != VERIFICATION_SCHEMA_VERSION:
        blockers.append("verification_summary_schema_version_mismatch")
    if summary.get("package_schema_version") != PACKAGE_SCHEMA_VERSION:
        blockers.append("verification_summary_package_schema_version_mismatch")
    if summary.get("ok") is not True:
        blockers.append("verification_summary_not_ok")
    if summary.get("verified") is not True:
        blockers.append("verification_summary_not_verified")
    raw_blockers = summary.get("blockers")
    if not isinstance(raw_blockers, list):
        blockers.append("verification_summary_blockers_invalid")
    elif any(not isinstance(item, str) for item in raw_blockers):
        blockers.append("verification_summary_blockers_invalid")
    elif raw_blockers:
        blockers.append("verification_summary_blockers_present")
    if summary.get("evidence_package") != "<redacted>":
        blockers.append("verification_summary_evidence_package_not_redacted")
    if not _is_sha256(summary.get("evidence_sha256")):
        blockers.append("verification_summary_evidence_sha256_invalid")
    if _as_nonnegative_int(summary.get("evidence_size_bytes")) <= 0:
        blockers.append("verification_summary_evidence_size_invalid")
    for field in AUTHORITY_FALSE_FIELDS:
        if summary.get(field) is not False:
            blockers.append(f"verification_summary_{field}_not_false")

    checks = _mapping(summary.get("checks"))
    for name in VERIFICATION_CHECK_NAMES:
        if checks.get(name) is not True:
            blockers.append(f"verification_summary_check_{name}_not_true")

    required = _mapping(summary.get("required_artifacts"))
    if tuple(_string_sequence(required.get("metrics_scrape"))) != tuple(
        REQUIRED_METRICS_FIELDS
    ):
        blockers.append("verification_summary_metrics_scrape_fields_mismatch")
    if tuple(_string_sequence(required.get("api_ops"))) != tuple(
        REQUIRED_API_OPS_FIELDS
    ):
        blockers.append("verification_summary_api_ops_fields_mismatch")
    if tuple(_string_sequence(required.get("operator_log_window"))) != (
        "timestamp",
        "commit",
        "sanitized_reason",
    ):
        blockers.append("verification_summary_operator_log_window_fields_mismatch")
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
    except json.JSONDecodeError as exc:
        raise SafeInputError("verification_summary_json_error") from exc
    except ValueError as exc:
        raise SafeInputError("verification_summary_json_error") from exc
    if not isinstance(parsed, Mapping):
        raise SafeInputError("verification_summary_not_object")
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
        "direct_bridge_write_performed": False,
        "automatic_release_decision": False,
        "approval_granted": False,
        "release_decision_made": False,
        "runtime_controls_added": False,
        "controls_present": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
        "network_access_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": [
            "route_stage_feed_health_drill_evidence_verification_summary_"
            f"bridge_event_template_failed:{_safe_token(reason)}"
        ],
        "warnings": [],
    }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _string_sequence(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [item for item in value if isinstance(item, str)]


def _safe_token(value: Any, fallback: str = "invalid_token") -> str:
    if isinstance(value, str) and SAFE_TOKEN_PATTERN.fullmatch(value):
        return value
    return fallback


def _safe_token_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [_safe_token(item) for item in value if isinstance(item, str)]


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256_PATTERN.fullmatch(value) is not None


def _as_nonnegative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value if value >= 0 else 0


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
