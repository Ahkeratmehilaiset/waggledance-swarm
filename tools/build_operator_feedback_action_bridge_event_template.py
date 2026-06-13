#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Build an operator-feedback action bridge-event template.

The template is intentionally no-authority: it reads durable bridge-log
``ops_feedback`` evidence, derives the bounded ``feedback_action_taken`` plan
through the core scheduler preflight, and renders a reviewable bridge event
template without appending it, enqueuing scheduler work, ticking the scheduler,
or skipping any gate.
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

from waggledance.core.autonomy_growth.operator_feedback_amplifier import (  # noqa: E402
    FEEDBACK_ACTION_TAKEN_EVENT_TYPE,
    OperatorFeedbackSchedulerPreflight,
    OperatorFeedbackValidationError,
    build_operator_feedback_scheduler_preflight_from_bridge_log,
)
from waggledance.core.bridge_event_schema import validate_event  # noqa: E402
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402


TEMPLATE_VERSION = "wd.operator_feedback_action_bridge_event_template.v1"
EVENT_STATUS = "operator_feedback_action_bridge_event_template_ready"
PROOF_ID = "operator_feedback_action_bridge_event_template_v1"
RATE_LIMIT_SOURCE = "durable_bridge_log"

AGENT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,32}$")
SAFE_REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,191}$")
SAFE_TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._/-]{0,191}$")
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
WINDOWS_DRIVE_PATH_PATTERN = re.compile(r"(?:^|[^A-Za-z0-9])(?:[A-Za-z]:[\\/])")
PATH_MARKERS = (
    "file://",
    "/home/",
    "/python/",
    "/users/",
    "/workspace/",
    "/workspaces/",
    "/tmp/",
    "waggledance-agent-worktrees",
)
FORBIDDEN_OUTPUT_MARKERS = (
    "http://",
    "https://",
    "C:/",
    "C:\\",
    "\\\\",
    "/home/",
    "/Users/",
    "PRIVATE_",
    "Authorization",
    "Bearer ",
    "secret",
    "password",
)
REPORT_FALSE_FIELDS = (
    "direct_bridge_write_performed",
    "bridge_event_written",
    "scheduler_enqueue_allowed",
    "scheduler_tick_allowed",
    "scheduler_tick_executed",
    "runtime_authority_granted",
    "canary_activation_applied",
    "gate_skip_allowed",
    "promotion_gate_skip_allowed",
    "adversarial_gate_skip_allowed",
    "canary_gate_skip_allowed",
    "queue_writes_applied",
    "control_plane_writes_applied",
    "external_writes_applied",
    "approval_granted",
    "release_decision_made",
    "raw_query_exported",
    "local_paths_recorded",
)
ACTION_FALSE_FIELDS = (
    "runtime_authority_granted",
    "canary_activation_applied",
    "bridge_event_written",
)
CANDIDATE_FALSE_FIELDS = (
    "scheduler_enqueue_allowed",
    "scheduler_tick_allowed",
    "bridge_event_written",
    "runtime_authority_granted",
    "gate_skip_allowed",
    "promotion_gate_skip_allowed",
    "adversarial_gate_skip_allowed",
    "canary_gate_skip_allowed",
    "raw_query_exported",
)
GAP_SIGNAL_FALSE_FIELDS = (
    "gate_skip_allowed",
    "promotion_gate_skip_allowed",
    "adversarial_gate_skip_allowed",
    "canary_gate_skip_allowed",
    "raw_query_exported",
    "runtime_authority_granted",
)


class SafeInputError(ValueError):
    """Raised when local JSONL or bridge template inputs are unsafe."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--feedback-id", required=True)
    parser.add_argument("--tail", type=int, default=None)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument(
        "--to",
        default="operator,codex-lead-1,claude-rco-1,claude-rco-2",
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
        help="Optional UTC timestamp override such as 2026-06-06T19:00:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    events_checked = 0
    try:
        durable_events = read_durable_bridge_events(args.events, tail=args.tail)
        events_checked = len(durable_events)
        report = build_operator_feedback_action_bridge_event_template(
            feedback_id=args.feedback_id,
            durable_bridge_events=durable_events,
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
        report = _bridge_template_error_report(
            exc.code,
            feedback_id=str(args.feedback_id or ""),
            events_checked=events_checked,
        )

    indent = 2 if args.pretty else None
    if args.json or not report["ok"]:
        print(json.dumps(report, indent=indent, sort_keys=True, allow_nan=False))
    else:
        print(json.dumps(
            report["bridge_event_template"],
            indent=indent,
            sort_keys=True,
            allow_nan=False,
        ))
    return 0 if report["ok"] else 1


def read_durable_bridge_events(
    events_path: Path,
    *,
    tail: int | None = None,
) -> list[Mapping[str, Any]]:
    """Read a durable bridge JSONL window without returning path metadata."""

    if tail is not None and tail < 1:
        raise SafeInputError("tail_must_be_positive")
    path = Path(events_path)
    if not path.exists():
        raise SafeInputError("events_file_not_found")
    if not path.is_file():
        raise SafeInputError("events_source_not_file")
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SafeInputError("events_file_unreadable") from exc

    numbered_lines = list(enumerate(raw_lines, start=1))
    selected_lines = numbered_lines[-tail:] if tail is not None else numbered_lines
    events: list[Mapping[str, Any]] = []
    for line_no, line in selected_lines:
        if not line.strip():
            continue

        def reject_constant(value: str) -> None:
            raise ValueError(f"non_finite_json_constant:{value}")

        try:
            raw = json.loads(line, parse_constant=reject_constant)
        except json.JSONDecodeError as exc:
            raise SafeInputError(f"events_json_error_line_{line_no}") from exc
        except ValueError as exc:
            raise SafeInputError(f"events_json_error_line_{line_no}") from exc
        if not isinstance(raw, Mapping):
            raise SafeInputError(f"events_line_{line_no}_not_object")
        events.append(raw)
    return events


def build_operator_feedback_action_bridge_event_template(
    *,
    feedback_id: str,
    durable_bridge_events: Sequence[Mapping[str, Any]],
    agent_id: str,
    task_id: str,
    to: str = "operator,codex-lead-1,claude-rco-1,claude-rco-2",
    severity: str = "medium",
    role: str = "tools-tests",
    run_id: str = "",
    session_id: str = "",
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return a valid bridge-event template without appending it."""

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
        return _bridge_template_error_report(
            input_error,
            feedback_id=feedback_id,
            events_checked=len(durable_bridge_events),
        )
    targets, _ = _validate_bridge_targets(to)
    try:
        preflight = build_operator_feedback_scheduler_preflight_from_bridge_log(
            feedback_id=feedback_id,
            durable_bridge_events=durable_bridge_events,
        )
    except OperatorFeedbackValidationError as exc:
        return _bridge_template_error_report(
            "operator_feedback_validation_failed:" + _safe_reason(str(exc)),
            feedback_id=feedback_id,
            events_checked=len(durable_bridge_events),
        )

    safety_errors = _preflight_safety_errors(preflight)
    if safety_errors:
        return _bridge_template_error_report(
            safety_errors[0],
            feedback_id=feedback_id,
            events_checked=len(durable_bridge_events),
        )

    preflight_dict = preflight.to_dict()
    action_plan = preflight.action_plan.to_dict()
    candidate = dict(preflight.scheduler_candidate_artifact)
    fast_track_priority = candidate.get("fast_track_priority") is True
    payload = {
        "schema_version": TEMPLATE_VERSION,
        "event_type": FEEDBACK_ACTION_TAKEN_EVENT_TYPE,
        "feedback_id": preflight.action_plan.feedback_id,
        "action_id": preflight.action_plan.action_id,
        "verified_operator_id": preflight.verified_operator_id,
        "rate_limit_source": preflight.rate_limit_source,
        "operator_fast_track_count": preflight.operator_fast_track_count,
        "global_fast_track_count": preflight.global_fast_track_count,
        "global_fast_track_per_hour_max": (
            preflight.global_fast_track_per_hour_max
        ),
        "source_bridge_event_digest": preflight.source_bridge_event_digest,
        "preflight_digest": sha256_digest(preflight_dict),
        "action_plan_digest": sha256_digest(action_plan),
        "feedback_action_taken": action_plan,
        "scheduler_candidate_artifact": candidate,
        "fast_track_queue_priority_requested": fast_track_priority,
        "fast_track_is_queue_priority_only": True,
        "authority_boundary": _authority_boundary(fast_track_priority),
        "template_only": True,
        "manual_review_required": True,
        "direct_bridge_write_performed": False,
        "bridge_event_written": False,
        "scheduler_enqueue_allowed": False,
        "scheduler_tick_allowed": False,
        "scheduler_tick_executed": False,
        "runtime_authority_granted": False,
        "canary_activation_applied": False,
        "gate_skip_allowed": False,
        "promotion_gate_skip_allowed": False,
        "adversarial_gate_skip_allowed": False,
        "canary_gate_skip_allowed": False,
        "queue_writes_applied": False,
        "control_plane_writes_applied": False,
        "external_writes_applied": False,
        "approval_granted": False,
        "release_decision_made": False,
        "raw_query_exported": False,
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
            "Operator-feedback action bridge-event template ready; "
            f"feedback_id={preflight.action_plan.feedback_id}; "
            f"queue_priority={candidate['queue_priority']}; "
            "template_only=true; bridge_event_written=false; "
            "scheduler_enqueue_allowed=false; scheduler_tick_allowed=false; "
            "gate_skip_allowed=false."
        ),
        "paths": [],
        "write_scope": [],
        "run_id": run_id,
        "role": role,
        "session_id": session_id,
        "capabilities": [
            "operator_feedback",
            "autonomy_growth",
            "bridge_event",
            "tools",
        ],
        "pid": 0,
        "cwd": "template_not_emitted",
        "payload": payload,
    }
    validate_event(event)
    encoded_event = json.dumps(event, allow_nan=False, sort_keys=True)
    _assert_no_forbidden_output(encoded_event)
    report = {
        "proof_id": PROOF_ID,
        "ok": True,
        "template_version": TEMPLATE_VERSION,
        "feedback_id": preflight.action_plan.feedback_id,
        "events_checked": len(durable_bridge_events),
        "bridge_event_template": event,
        "template_only": True,
        "manual_review_required": True,
        "fast_track_queue_priority_requested": fast_track_priority,
        "fast_track_is_queue_priority_only": True,
        "blockers": [],
        "warnings": [],
    }
    for field in REPORT_FALSE_FIELDS:
        report[field] = False
    errors = validate_operator_feedback_action_bridge_event_template_report(report)
    if errors:
        return _bridge_template_error_report(
            errors[0],
            feedback_id=feedback_id,
            events_checked=len(durable_bridge_events),
        )
    return report


def validate_operator_feedback_action_bridge_event_template_report(
    report: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    report_dict = _plain_json_object_or_none(report)
    if report_dict is None:
        return ["report_not_json_object"]
    for field in REPORT_FALSE_FIELDS:
        if report_dict.get(field) is not False:
            errors.append(f"{field}_not_exact_false")
    if report_dict.get("template_only") is not True:
        errors.append("template_only_not_true")
    if report_dict.get("manual_review_required") is not True:
        errors.append("manual_review_required_not_true")
    if report_dict.get("fast_track_is_queue_priority_only") is not True:
        errors.append("fast_track_is_queue_priority_only_not_true")
    event = _plain_json_object_or_none(report_dict.get("bridge_event_template"))
    if event is None:
        errors.append("bridge_event_template_not_object")
        return errors
    payload = _plain_json_object_or_none(event.get("payload"))
    if payload is None:
        errors.append("payload_not_object")
        return errors
    if payload.get("template_only") is not True:
        errors.append("payload_template_only_not_true")
    for field in REPORT_FALSE_FIELDS:
        if payload.get(field) is not False:
            errors.append(f"payload_{field}_not_exact_false")
    if payload.get("event_type") != FEEDBACK_ACTION_TAKEN_EVENT_TYPE:
        errors.append("payload_event_type_mismatch")
    action = _plain_json_object_or_none(payload.get("feedback_action_taken"))
    if action is None:
        errors.append("feedback_action_taken_not_object")
    else:
        for field in ACTION_FALSE_FIELDS:
            if action.get(field) is not False:
                errors.append(f"action_{field}_not_exact_false")
        gap_signal = action.get("gap_signal")
        if isinstance(gap_signal, Mapping):
            for field in GAP_SIGNAL_FALSE_FIELDS:
                if gap_signal.get(field) is not False:
                    errors.append(f"gap_signal_{field}_not_exact_false")
    candidate = _plain_json_object_or_none(
        payload.get("scheduler_candidate_artifact")
    )
    if candidate is None:
        errors.append("scheduler_candidate_artifact_not_object")
    else:
        for field in CANDIDATE_FALSE_FIELDS:
            if candidate.get(field) is not False:
                errors.append(f"candidate_{field}_not_exact_false")
    return errors


def _preflight_safety_errors(
    preflight: OperatorFeedbackSchedulerPreflight,
) -> list[str]:
    errors: list[str] = []
    if preflight.rate_limit_source != RATE_LIMIT_SOURCE:
        errors.append("rate_limit_source_not_durable_bridge_log")
    for field in (
        "scheduler_enqueue_allowed",
        "scheduler_tick_allowed",
        "gate_skip_allowed",
        "bridge_event_written",
    ):
        if getattr(preflight, field) is not False:
            errors.append(f"preflight_{field}_not_exact_false")
    action = preflight.action_plan.to_dict()
    for field in ACTION_FALSE_FIELDS:
        if action.get(field) is not False:
            errors.append(f"action_{field}_not_exact_false")
    candidate = dict(preflight.scheduler_candidate_artifact)
    for field in CANDIDATE_FALSE_FIELDS:
        if candidate.get(field) is not False:
            errors.append(f"candidate_{field}_not_exact_false")
    if candidate.get("queue_priority") not in {"fast_track", "normal"}:
        errors.append("candidate_queue_priority_invalid")
    if candidate.get("fast_track_priority") is True and (
        candidate.get("gate_skip_allowed") is not False
        or candidate.get("scheduler_enqueue_allowed") is not False
    ):
        errors.append("fast_track_priority_not_queue_only")
    gap_signal = action.get("gap_signal")
    if isinstance(gap_signal, Mapping):
        if gap_signal.get("queue_priority_only") is not True:
            errors.append("gap_signal_queue_priority_only_not_true")
        for field in GAP_SIGNAL_FALSE_FIELDS:
            if gap_signal.get(field) is not False:
                errors.append(f"gap_signal_{field}_not_exact_false")
    return errors


def _authority_boundary(fast_track_priority: bool) -> dict[str, Any]:
    return {
        "manual_review_required": True,
        "verified_operator_identity_required": True,
        "durable_rate_limit_source_required": True,
        "global_fast_track_cap_required": True,
        "fast_track_queue_priority_requested": fast_track_priority,
        "fast_track_is_queue_priority_only": True,
        "approval_granted": False,
        "release_decision_made": False,
        "direct_bridge_write_performed": False,
        "bridge_event_written": False,
        "scheduler_enqueue_allowed": False,
        "scheduler_tick_allowed": False,
        "scheduler_tick_executed": False,
        "runtime_authority_granted": False,
        "canary_activation_applied": False,
        "gate_skip_allowed": False,
        "promotion_gate_skip_allowed": False,
        "adversarial_gate_skip_allowed": False,
        "canary_gate_skip_allowed": False,
        "queue_writes_applied": False,
        "control_plane_writes_applied": False,
        "external_writes_applied": False,
        "raw_query_exported": False,
        "local_paths_recorded": False,
    }


def _bridge_template_error_report(
    reason: str,
    *,
    feedback_id: str,
    events_checked: int,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "proof_id": PROOF_ID,
        "ok": False,
        "template_version": TEMPLATE_VERSION,
        "feedback_id": feedback_id,
        "events_checked": events_checked,
        "template_only": True,
        "manual_review_required": True,
        "fast_track_queue_priority_requested": False,
        "fast_track_is_queue_priority_only": True,
        "blockers": [
            "operator_feedback_action_bridge_event_template_failed:"
            f"{_safe_reason(reason)}"
        ],
        "warnings": [],
    }
    for field in REPORT_FALSE_FIELDS:
        report[field] = False
    return report


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
    if _validate_task_id(task_id) is not None:
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


def _validate_bridge_targets(raw_targets: str) -> tuple[str, str | None]:
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


def _validate_task_id(value: Any) -> str | None:
    if not isinstance(value, str) or not SAFE_TASK_ID_PATTERN.fullmatch(value):
        return "task_id_unsafe"
    if _contains_path_marker(value):
        return "task_id_unsafe"
    if "/" not in value:
        return None
    if value.endswith("/") or "//" in value:
        return "task_id_unsafe"
    segments = value.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        return "task_id_unsafe"
    return None


def _validate_bridge_agent_id(label: str, value: Any) -> str | None:
    if not isinstance(value, str) or not AGENT_ID_PATTERN.fullmatch(value):
        return f"{label}_unsafe"
    return None


def _plain_json_object_or_none(value: Any) -> dict[str, Any] | None:
    try:
        plain = json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError):
        return None
    if not isinstance(plain, dict):
        return None
    return plain


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
    if _contains_path_marker(json.loads(text)):
        raise ValueError("bridge-event template contains path marker")
    found = _forbidden_output_markers(text)
    if found:
        raise ValueError(
            "bridge-event template contains forbidden marker: " + found[0]
        )


def _safe_reason(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9:._-]+", "_", str(value)).strip("_")
    if not text:
        return "invalid_reason"
    if not text[0].isalnum():
        text = "reason_" + text
    return text[:192]


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
