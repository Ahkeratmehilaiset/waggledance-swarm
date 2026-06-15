#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Build no-write operator-feedback scheduler enqueue preview reports.

This CLI is the scheduler-facing preview layer for ADR-053. It reads an
already-durable bridge JSONL window, derives the scheduler preflight for one
``ops_feedback`` event, and renders the queue-priority preview. It never writes
bridge events, enqueues scheduler work, ticks the scheduler, or grants runtime
authority.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.autonomy_growth.operator_feedback_amplifier import (  # noqa: E402
    OperatorFeedbackValidationError,
    build_operator_feedback_scheduler_enqueue_preview,
    build_operator_feedback_scheduler_preflight_from_bridge_log,
)

import build_operator_feedback_scheduler_preflight as preflight_cli  # noqa: E402


REPORT_VERSION = "wd.operator_feedback_scheduler_enqueue_preview_cli.v1"
SCHEMA_VERSION = "operator_feedback_scheduler_enqueue_preview_cli.v1"
MEASUREMENT_SCOPE = "local_read_only_operator_feedback_scheduler_enqueue_preview"
TOP_LEVEL_FALSE_FIELDS = (
    "scheduler_enqueue_allowed",
    "scheduler_tick_allowed",
    "scheduler_tick_executed",
    "bridge_event_written",
    "runtime_authority_granted",
    "gate_skip_allowed",
    "promotion_gate_skip_allowed",
    "adversarial_gate_skip_allowed",
    "canary_gate_skip_allowed",
    "canary_activation_applied",
    "queue_write_applied",
    "growth_intent_created",
    "external_writes_applied",
    "queue_writes_applied",
    "control_plane_writes_applied",
    "raw_query_exported",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a path-free, no-write scheduler enqueue preview report "
            "from durable operator-feedback bridge events."
        ),
    )
    parser.add_argument(
        "--events",
        type=Path,
        required=True,
        help="Durable bridge events JSONL file to read.",
    )
    parser.add_argument(
        "--feedback-id",
        required=True,
        help="Feedback id to select from the durable bridge log.",
    )
    parser.add_argument(
        "--tail",
        type=int,
        default=None,
        help="Only inspect the last N JSONL lines.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the report as JSON. JSON output is the only output format.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    events_checked = 0
    try:
        durable_events = preflight_cli.read_durable_bridge_events(
            args.events,
            tail=args.tail,
        )
        events_checked = len(durable_events)
        report = build_operator_feedback_scheduler_enqueue_preview_cli_report(
            feedback_id=args.feedback_id,
            durable_bridge_events=durable_events,
        )
        exit_code = 0
    except (OperatorFeedbackValidationError, ValueError) as exc:
        report = build_failure_report(
            feedback_id=str(args.feedback_id or ""),
            events_checked=events_checked,
            code=_error_code_for(exc),
            message=_safe_error_message(exc),
        )
        exit_code = 1

    indent = 2 if args.pretty else None
    print(json.dumps(report, indent=indent, sort_keys=True, allow_nan=False))
    return exit_code


def build_operator_feedback_scheduler_enqueue_preview_cli_report(
    *,
    feedback_id: str,
    durable_bridge_events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    preflight = build_operator_feedback_scheduler_preflight_from_bridge_log(
        feedback_id=feedback_id,
        durable_bridge_events=durable_bridge_events,
    )
    preview = build_operator_feedback_scheduler_enqueue_preview(preflight)
    report: dict[str, Any] = _base_report(
        feedback_id=preview.feedback_id,
        events_checked=len(durable_bridge_events),
        ok=True,
    )
    report.update({
        "verified_operator_id": preview.verified_operator_id,
        "rate_limit_source": preview.rate_limit_source,
        "operator_fast_track_count": preview.operator_fast_track_count,
        "global_fast_track_count": preview.global_fast_track_count,
        "global_fast_track_per_hour_max": preview.global_fast_track_per_hour_max,
        "queue_priority": preview.queue_priority,
        "priority_weight": preview.priority_weight,
        "fast_track_priority": preview.fast_track_priority,
        "rate_limited": preview.rate_limited,
        "source_bridge_event_digest": preview.source_bridge_event_digest,
        "scheduler_candidate_digest": preview.scheduler_candidate_digest,
        "preflight": preflight.to_dict(),
        "preview": preview.to_dict(),
    })
    errors = validate_operator_feedback_scheduler_enqueue_preview_cli_report(report)
    if errors:
        raise ValueError("; ".join(errors))
    return report


def build_failure_report(
    *,
    feedback_id: str,
    events_checked: int = 0,
    code: str,
    message: str,
) -> dict[str, Any]:
    report = _base_report(
        feedback_id=feedback_id,
        events_checked=events_checked,
        ok=False,
    )
    report.update({
        "verified_operator_id": "",
        "rate_limit_source": "durable_bridge_log",
        "operator_fast_track_count": 0,
        "global_fast_track_count": 0,
        "global_fast_track_per_hour_max": 0,
        "queue_priority": "normal",
        "priority_weight": 0,
        "fast_track_priority": False,
        "rate_limited": False,
        "source_bridge_event_digest": "",
        "scheduler_candidate_digest": "",
        "preflight": None,
        "preview": None,
        "blockers": [{"code": code, "message": message}],
    })
    return report


def validate_operator_feedback_scheduler_enqueue_preview_cli_report(
    report: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    report_dict = _plain_json_object_or_none(report)
    if report_dict is None:
        return ["report must be a JSON object"]
    for field in TOP_LEVEL_FALSE_FIELDS:
        if report_dict.get(field) is not False:
            errors.append(f"{field} must be exact false bool")
    if report_dict.get("durable_bridge_log_source") is not True:
        errors.append("durable_bridge_log_source must be exact true bool")
    if report_dict.get("input_path_recorded") is not False:
        errors.append("input_path_recorded must be exact false bool")
    if report_dict.get("ok") is True:
        preflight = _plain_json_object_or_none(report_dict.get("preflight"))
        preview = _plain_json_object_or_none(report_dict.get("preview"))
        if preflight is None:
            errors.append("preflight must be a JSON object when ok is true")
        if preview is None:
            errors.append("preview must be a JSON object when ok is true")
        if preview is not None:
            for field in (
                "scheduler_enqueue_allowed",
                "scheduler_tick_allowed",
                "queue_write_applied",
                "growth_intent_created",
                "bridge_event_written",
                "runtime_authority_granted",
                "gate_skip_allowed",
                "promotion_gate_skip_allowed",
                "adversarial_gate_skip_allowed",
                "canary_gate_skip_allowed",
            ):
                if preview.get(field) is not False:
                    errors.append(f"preview.{field} must be exact false bool")
            if preview.get("rate_limit_source") != "durable_bridge_log":
                errors.append("preview.rate_limit_source must be durable_bridge_log")
            if preview.get("queue_priority") not in {"fast_track", "normal"}:
                errors.append("preview.queue_priority is unsupported")
            if preview.get("priority_weight") not in {0, 100}:
                errors.append("preview.priority_weight is unsupported")
            if not isinstance(preview.get("fast_track_priority"), bool):
                errors.append("preview.fast_track_priority must be boolean")
            if preview.get("next_required_integration") != (
                "scheduler_enqueue_adapter_separate_pr"
            ):
                errors.append("preview.next_required_integration mismatch")
        if preflight is not None:
            preflight_errors = (
                preflight_cli
                .validate_operator_feedback_scheduler_preflight_cli_report({
                    **_base_preflight_validation_wrapper(report_dict),
                    "preflight": preflight,
                })
            )
            errors.extend(f"preflight.{error}" for error in preflight_errors)
    return errors


def _base_report(
    *,
    feedback_id: str,
    events_checked: int,
    ok: bool,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "ok": ok,
        "report_version": REPORT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "measurement_scope": MEASUREMENT_SCOPE,
        "feedback_id": feedback_id,
        "events_checked": events_checked,
        "durable_bridge_log_source": True,
        "input_path_recorded": False,
        "blockers": [],
    }
    for field in TOP_LEVEL_FALSE_FIELDS:
        report[field] = False
    return report


def _base_preflight_validation_wrapper(
    report: Mapping[str, Any],
) -> dict[str, Any]:
    wrapper: dict[str, Any] = {
        "ok": report.get("ok"),
        "report_version": preflight_cli.REPORT_VERSION,
        "schema_version": preflight_cli.SCHEMA_VERSION,
        "measurement_scope": preflight_cli.MEASUREMENT_SCOPE,
        "feedback_id": report.get("feedback_id", ""),
        "events_checked": report.get("events_checked", 0),
        "durable_bridge_log_source": True,
        "input_path_recorded": False,
        "blockers": [],
    }
    for field in preflight_cli.TOP_LEVEL_FALSE_FIELDS:
        wrapper[field] = False
    return wrapper


def _plain_json_object_or_none(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    try:
        return json.loads(json.dumps(value, sort_keys=True, allow_nan=False))
    except (TypeError, ValueError):
        return None


def _safe_error_message(exc: BaseException) -> str:
    if isinstance(exc, OperatorFeedbackValidationError):
        return str(exc)
    message = str(exc)
    if "\\" in message or "/" in message:
        return "enqueue preview input validation failed"
    return message or "enqueue preview input validation failed"


def _error_code_for(exc: BaseException) -> str:
    if isinstance(exc, OperatorFeedbackValidationError):
        return "operator_feedback_validation_failed"
    return "bridge_log_input_invalid"


if __name__ == "__main__":
    raise SystemExit(main())
