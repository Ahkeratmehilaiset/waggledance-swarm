# SPDX-License-Identifier: BUSL-1.1
"""Report the next idle-protocol v1 step.

This is a manual bridge coordination primitive. It does not generate strategic
content, run on a timer, create implementation tasks, or execute consensus.
It is read-only and never appends bridge events.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.idle_check import DEFAULT_EVENTS_PATH
from waggledance.core.idle_protocol_session import summarize_idle_session


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect idle-protocol session state and report the next step.",
    )
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS_PATH)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report only. This is the only mode and is accepted for clarity.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = run_idle_protocol_session(
            events_path=args.events,
        )
    except SessionError as exc:
        if args.json:
            print(json.dumps(exc.report, sort_keys=True))
        else:
            print(f"idle session FAILED: {exc}", file=sys.stderr)
            for error in exc.report.get("errors", []):
                print(f"- {error}", file=sys.stderr)
        return int(exc.report.get("exit_code", 2))

    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(report["decision"])
        next_event = report["summary"].get("next_required_event")
        if next_event:
            print(
                "next: "
                f"{next_event['event_type']} round {next_event['round_number']}"
            )
        else:
            print("next: operator review or no session action")
    return 0


def run_idle_protocol_session(
    *,
    events_path: Path,
) -> dict[str, Any]:
    events, invalid_lines = _read_events(events_path)
    if invalid_lines:
        raise SessionError(
            "bridge events contain invalid JSON lines",
            {
                "decision": "unknown",
                "errors": [f"invalid bridge event lines: {invalid_lines}"],
                "exit_code": 2,
            },
        )
    summary = summarize_idle_session(events)
    report: dict[str, Any] = {
        "decision": _decision_for_summary(summary),
        "read_only": True,
        "summary": summary,
    }
    return report


class SessionError(ValueError):
    def __init__(self, message: str, report: dict[str, Any]) -> None:
        super().__init__(message)
        self.report = report


def _read_events(path: Path) -> tuple[list[dict[str, Any]], int]:
    if not path.exists():
        raise SessionError(
            f"missing bridge events file: {path}",
            {
                "decision": "unknown",
                "errors": [f"missing bridge events file: {path}"],
                "exit_code": 2,
            },
        )
    events: list[dict[str, Any]] = []
    invalid_lines = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            invalid_lines += 1
            continue
        if isinstance(event, dict):
            events.append(event)
        else:
            invalid_lines += 1
    return events, invalid_lines


def _decision_for_summary(summary: dict[str, Any]) -> str:
    if summary.get("terminal"):
        return "operator_review_required"
    if summary.get("next_required_event"):
        return "next_required"
    return "no_action"


if __name__ == "__main__":
    raise SystemExit(main())
