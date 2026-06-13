#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Render a MAGMA share import admission-status bridge-event template.

Input is the path-free JSON emitted by
``import_magma_share_manifest.py --admission-status-json``. The output is a
template only: it never appends to the bridge, enables transport, imports
payload files, or grants runtime authority.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.bridge_event_schema import validate_event  # noqa: E402
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402
from waggledance.core.magma.share_manifest import (  # noqa: E402
    IMPORT_ADMISSION_STATUS_VERSION,
)


TEMPLATE_VERSION = "magma_share_import_admission_status_bridge_event_template.v0"
AGENT_RE = re.compile(r"^[a-z][a-z0-9_-]{1,32}$")
SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._/-]{0,191}$")
SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,191}$")
SHA_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SESSION_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
FORBIDDEN_MARKERS = (
    "PRIVATE" + "_MARKER",
    "_DO" + "_NOT" + "_LEAK",
    "C:" + "\\",
    "C:/",
    "\\\\",
    "/home/",
    "/Users/",
    "/tmp/",
    "file" + "://",
    "http" + "://",
    "https" + "://",
    "waggledance-agent-worktrees",
    "Bearer ",
    "Author" + "ization",
)
TRUE_FLAGS = ("replay_metadata_only", "no_authority_import")
FALSE_FLAGS = (
    "transport_enabled",
    "runtime_export_enabled",
    "runtime_authority_granted",
    "runtime_authority_changed",
    "payload_digest_imported",
    "raw_material_imported",
    "replacement_map_imported",
    "local_paths_recorded",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admission-status-json", required=True, type=Path)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--to", default="codex-lead-1,claude-rco-1,operator")
    parser.add_argument("--severity", default="medium", choices=("", "low", "medium", "high"))
    parser.add_argument("--role", default="tools-tests")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--session-id", default="")
    parser.add_argument("--now", default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        status = json.loads(args.admission_status_json.read_text(encoding="utf-8"))
        report = build_magma_share_import_admission_status_bridge_event_template(
            admission_status=status,
            agent_id=args.agent,
            task_id=args.task_id,
            to=args.to,
            severity=args.severity,
            role=args.role,
            run_id=args.run_id,
            session_id=args.session_id,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except (OSError, json.JSONDecodeError, ValueError):
        report = _failure("admission_status_template_invalid")
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    elif report["ok"]:
        print(json.dumps(report["bridge_event_template"], indent=2, sort_keys=True))
    else:
        print(", ".join(report["blockers"]), file=sys.stderr)
    return 0 if report["ok"] else 1


def build_magma_share_import_admission_status_bridge_event_template(
    *,
    admission_status: Mapping[str, Any],
    agent_id: str,
    task_id: str,
    to: str,
    severity: str = "medium",
    role: str = "tools-tests",
    run_id: str = "",
    session_id: str = "",
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    error = _input_error(agent_id, task_id, to, severity, role, run_id, session_id)
    if error:
        return _failure(error)
    status, error = _safe_status(admission_status)
    if error:
        return _failure(error)
    assert status is not None

    payload = _payload(status)
    ok = payload["ok"] is True
    event = {
        "ts_utc": _utc_iso(now_utc or datetime.now(timezone.utc)),
        "agent": agent_id,
        "type": "handoff" if ok else "finding",
        "task_id": task_id,
        "status": "magma_share_import_admission_ready"
        if ok
        else f"magma_share_import_admission_{payload['status']}",
        "severity": severity,
        "to": _targets(to),
        "message": _message(payload),
        "paths": [],
        "write_scope": [],
        "run_id": run_id,
        "role": role,
        "session_id": session_id,
        "capabilities": ["magma", "bridge_event", "reviewer_handoff"],
        "pid": 0,
        "cwd": "template_not_emitted",
        "payload": payload,
    }
    try:
        validate_event(event)
        _assert_no_forbidden(json.dumps(event, sort_keys=True, allow_nan=False))
    except Exception:
        return _failure("bridge_event_template_invalid")

    return {
        "ok": True,
        "template_version": TEMPLATE_VERSION,
        "bridge_event_template": event,
        "admission_status_digest": sha256_digest(status),
        "template_only": True,
        "direct_bridge_write_performed": False,
        "transport_enabled": False,
        "runtime_export_enabled": False,
        "runtime_authority_granted": False,
        "runtime_authority_changed": False,
        "payload_files_imported": 0,
        "payload_digest_imported": False,
        "raw_material_imported": False,
        "replacement_map_imported": False,
        "blockers": [],
        "warnings": [],
    }


def _safe_status(value: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(value, Mapping):
        return None, "admission_status_not_object"
    try:
        status = json.loads(json.dumps(value, sort_keys=True, allow_nan=False))
    except (TypeError, ValueError):
        return None, "admission_status_not_json"
    try:
        _assert_no_forbidden(json.dumps(status, sort_keys=True, allow_nan=False))
    except ValueError:
        return None, "path_or_private_marker_present"
    if status.get("summary_version") != IMPORT_ADMISSION_STATUS_VERSION:
        return None, "summary_version_mismatch"
    if not isinstance(status.get("ok"), bool):
        return None, "ok_not_bool"
    for key in ("status", "blocker_class"):
        if not _safe_token(status.get(key)):
            return None, f"{key}_unsafe"
    blockers = status.get("blockers")
    if not isinstance(blockers, list) or not all(_safe_token(item) for item in blockers):
        return None, "blockers_unsafe"
    for key in TRUE_FLAGS:
        if status.get(key) is not True:
            return None, f"{key}_not_true"
    for key in FALSE_FLAGS:
        if status.get(key) is not False:
            return None, f"{key}_not_false"
    if status.get("payload_files_imported") != 0:
        return None, "payload_files_imported_not_zero"
    return status, ""


def _payload(status: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": TEMPLATE_VERSION,
        "summary_version": str(status["summary_version"]),
        "source": _safe_string(status.get("source")),
        "status": str(status["status"]),
        "severity": _safe_string(status.get("severity")),
        "ok": status.get("ok") is True,
        "blocker_class": str(status["blocker_class"]),
        "blocker_count": len(status.get("blockers") or []),
        "admission_contract_digest": _digest(status.get("admission_contract_digest")),
        "report_digest": _digest(status.get("report_digest")),
        "share_manifest_digest": _digest(status.get("share_manifest_digest")),
        "source_manifest_digest": _digest(status.get("source_manifest_digest")),
        "share_id": _safe_string(status.get("share_id"), default=""),
        "purpose": _safe_string(status.get("purpose"), default=""),
        "entry_count": _int(status.get("entry_count")),
        "age_seconds": _int(status.get("age_seconds")),
        "max_age_hours": _int(status.get("max_age_hours")),
        "context_verified": status.get("context_verified") is True,
        "context_drift_detected": status.get("context_drift_detected") is True,
        "replay_metadata_only": status.get("replay_metadata_only") is True,
        "no_authority_import": status.get("no_authority_import") is True,
        "operator_handoff_required_for_peer_review": (
            status.get("operator_handoff_required_for_peer_review") is True
        ),
        "controls_present": status.get("controls_present") is True,
        "transport_enabled": False,
        "runtime_export_enabled": False,
        "runtime_authority_granted": False,
        "runtime_authority_changed": False,
        "payload_files_imported": 0,
        "payload_digest_imported": False,
        "raw_material_imported": False,
        "replacement_map_imported": False,
        "local_paths_recorded": False,
        "template_only": True,
        "direct_bridge_write_performed": False,
    }


def _message(payload: Mapping[str, Any]) -> str:
    if payload["ok"]:
        return (
            "MAGMA share import admission ready; "
            f"entry_count={payload['entry_count']}; "
            f"admission_contract_digest={payload['admission_contract_digest']}; "
            "template_only=true; transport_enabled=false; "
            "runtime_authority_granted=false; payload_files_imported=0."
        )
    return (
        "MAGMA share import admission not ready; "
        f"status={payload['status']}; blocker_class={payload['blocker_class']}; "
        "template_only=true; transport_enabled=false; "
        "runtime_authority_granted=false; payload_files_imported=0."
    )


def _input_error(
    agent_id: str,
    task_id: str,
    to: str,
    severity: str,
    role: str,
    run_id: str,
    session_id: str,
) -> str:
    if not isinstance(agent_id, str) or not AGENT_RE.fullmatch(agent_id):
        return "agent_unsafe"
    if not isinstance(task_id, str) or not SAFE_REF_RE.fullmatch(task_id):
        return "task_id_unsafe"
    try:
        _targets(to)
    except ValueError:
        return "to_unsafe"
    if severity not in {"", "low", "medium", "high"}:
        return "severity_unsafe"
    if role and (not isinstance(role, str) or not AGENT_RE.fullmatch(role)):
        return "role_unsafe"
    if run_id and (not isinstance(run_id, str) or not SAFE_REF_RE.fullmatch(run_id)):
        return "run_id_unsafe"
    if session_id and not SESSION_RE.fullmatch(session_id):
        return "session_id_unsafe"
    return ""


def _targets(raw: str) -> str:
    targets = [part.strip() for part in str(raw or "").split(",") if part.strip()]
    if not targets or any(not AGENT_RE.fullmatch(target) for target in targets):
        raise ValueError("unsafe targets")
    return ",".join(targets)


def _safe_token(value: Any) -> bool:
    return isinstance(value, str) and bool(SAFE_TOKEN_RE.fullmatch(value))


def _safe_string(value: Any, *, default: str = "unknown") -> str:
    return value if _safe_token(value) else default


def _digest(value: Any) -> str:
    return value if isinstance(value, str) and SHA_RE.fullmatch(value) else ""


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _parse_utc(raw: str) -> datetime:
    if not isinstance(raw, str) or not raw.endswith("Z"):
        raise ValueError("unsafe now")
    parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("unsafe now")
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _assert_no_forbidden(text: str) -> None:
    lowered = text.lower()
    if any(marker.lower() in lowered for marker in FORBIDDEN_MARKERS):
        raise ValueError("forbidden output marker")


def _failure(reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "template_version": TEMPLATE_VERSION,
        "template_only": True,
        "direct_bridge_write_performed": False,
        "transport_enabled": False,
        "runtime_export_enabled": False,
        "runtime_authority_granted": False,
        "runtime_authority_changed": False,
        "payload_files_imported": 0,
        "payload_digest_imported": False,
        "raw_material_imported": False,
        "replacement_map_imported": False,
        "blockers": [f"admission_status_bridge_event_template_failed:{reason}"],
        "warnings": [],
    }


if __name__ == "__main__":
    raise SystemExit(main())
