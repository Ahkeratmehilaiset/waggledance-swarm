#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Build no-authority operator-feedback scheduler preflight reports.

This CLI reads an already-durable bridge JSONL window, selects one
``ops_feedback`` event by feedback id, and delegates all identity/rate-limit
checks to the core operator-feedback amplifier. It does not enqueue scheduler
work, tick the scheduler, write bridge events, or grant runtime authority.
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
    build_operator_feedback_scheduler_preflight_from_bridge_log,
)


REPORT_VERSION = "wd.operator_feedback_scheduler_preflight_cli.v1"
SCHEMA_VERSION = "operator_feedback_scheduler_preflight_cli.v1"
MEASUREMENT_SCOPE = "local_read_only_operator_feedback_scheduler_preflight"
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
    "external_writes_applied",
    "queue_writes_applied",
    "control_plane_writes_applied",
    "raw_query_exported",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a path-free, no-authority scheduler preflight report from "
            "durable operator-feedback bridge events."
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
    try:
        durable_events = read_durable_bridge_events(args.events, tail=args.tail)
        report = build_operator_feedback_scheduler_preflight_cli_report(
            feedback_id=args.feedback_id,
            durable_bridge_events=durable_events,
        )
        exit_code = 0
    except (OperatorFeedbackValidationError, ValueError) as exc:
        report = build_failure_report(
            feedback_id=str(args.feedback_id or ""),
            code=_error_code_for(exc),
            message=_safe_error_message(exc),
        )
        exit_code = 1

    indent = 2 if args.pretty else None
    print(json.dumps(report, indent=indent, sort_keys=True, allow_nan=False))
    return exit_code


def read_durable_bridge_events(
    events_path: Path,
    *,
    tail: int | None = None,
) -> list[Mapping[str, Any]]:
    """Read a durable bridge JSONL window without returning path metadata."""

    if tail is not None and tail < 1:
        raise ValueError("tail must be a positive integer")
    path = Path(events_path)
    if not path.exists():
        raise ValueError("events file not found")
    if not path.is_file():
        raise ValueError("events source must be a file")
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError("events file read failed") from exc

    numbered_lines = list(enumerate(raw_lines, start=1))
    selected_lines = numbered_lines[-tail:] if tail is not None else numbered_lines
    events: list[Mapping[str, Any]] = []
    for line_no, line in selected_lines:
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON at bridge log line {line_no}") from exc
        if not isinstance(raw, Mapping):
            raise ValueError(f"bridge log line {line_no} must be a JSON object")
        events.append(raw)
    return events


def build_operator_feedback_scheduler_preflight_cli_report(
    *,
    feedback_id: str,
    durable_bridge_events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    preflight = build_operator_feedback_scheduler_preflight_from_bridge_log(
        feedback_id=feedback_id,
        durable_bridge_events=durable_bridge_events,
    )
    report: dict[str, Any] = _base_report(
        feedback_id=preflight.action_plan.feedback_id,
        events_checked=len(durable_bridge_events),
        ok=True,
    )
    report.update({
        "verified_operator_id": preflight.verified_operator_id,
        "rate_limit_source": preflight.rate_limit_source,
        "operator_fast_track_count": preflight.operator_fast_track_count,
        "global_fast_track_count": preflight.global_fast_track_count,
        "global_fast_track_per_hour_max": (
            preflight.global_fast_track_per_hour_max
        ),
        "preflight": preflight.to_dict(),
    })
    errors = validate_operator_feedback_scheduler_preflight_cli_report(report)
    if errors:
        raise ValueError("; ".join(errors))
    return report


def build_failure_report(
    *,
    feedback_id: str,
    code: str,
    message: str,
) -> dict[str, Any]:
    report = _base_report(feedback_id=feedback_id, events_checked=0, ok=False)
    report.update({
        "verified_operator_id": "",
        "rate_limit_source": "durable_bridge_log",
        "operator_fast_track_count": 0,
        "global_fast_track_count": 0,
        "global_fast_track_per_hour_max": 0,
        "preflight": None,
        "blockers": [{"code": code, "message": message}],
    })
    return report


def validate_operator_feedback_scheduler_preflight_cli_report(
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
        if preflight is None:
            errors.append("preflight must be a JSON object when ok is true")
        else:
            for field in (
                "scheduler_enqueue_allowed",
                "scheduler_tick_allowed",
                "gate_skip_allowed",
                "bridge_event_written",
            ):
                if preflight.get(field) is not False:
                    errors.append(f"preflight.{field} must be exact false bool")
            candidate = _plain_json_object_or_none(
                preflight.get("scheduler_candidate_artifact")
            )
            if candidate is None:
                errors.append(
                    "preflight.scheduler_candidate_artifact must be a JSON object"
                )
            else:
                for field in (
                    "scheduler_enqueue_allowed",
                    "scheduler_tick_allowed",
                    "bridge_event_written",
                    "runtime_authority_granted",
                    "gate_skip_allowed",
                    "promotion_gate_skip_allowed",
                    "adversarial_gate_skip_allowed",
                    "canary_gate_skip_allowed",
                    "raw_query_exported",
                ):
                    if candidate.get(field) is not False:
                        errors.append(
                            "preflight.scheduler_candidate_artifact."
                            f"{field} must be exact false bool"
                        )
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
        return "preflight input validation failed"
    return message or "preflight input validation failed"


def _error_code_for(exc: BaseException) -> str:
    if isinstance(exc, OperatorFeedbackValidationError):
        return "operator_feedback_validation_failed"
    return "bridge_log_input_invalid"


if __name__ == "__main__":
    raise SystemExit(main())
