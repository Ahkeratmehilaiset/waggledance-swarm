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

from waggledance.core.idle_protocol_session import summarize_idle_session
from waggledance.core.work_queue import resolve_bridge_root


BASE_REQUIRED_FIELDS = (
    "protocol_version",
    "event_type",
    "proposal_id",
    "round_number",
    "proposes_substrate_change",
    "problem_statement",
    "tradeoff_axis",
    "simulation_evidence",
    "charter_alignment",
)
EVENT_REQUIRED_FIELDS = {
    "idle_proposal": ("proposal",),
    "idle_counter_proposal": (
        "responds_to",
        "alternative_proposal",
        "reasoning_points",
    ),
    "idle_adversarial_review": ("responds_to", "counterexamples"),
    "idle_consensus_reached": (
        "consensus_target_proposal_id",
        "operator_gate_required",
        "auto_execute",
    ),
}
EVENT_FORBIDDEN_FIELDS = {
    "idle_proposal": (
        "alternative_proposal",
        "counterexamples",
        "consensus_target_proposal_id",
        "violating_proposal_id",
    ),
    "idle_counter_proposal": (
        "proposal",
        "counterexamples",
        "consensus_target_proposal_id",
        "violating_proposal_id",
    ),
    "idle_adversarial_review": (
        "proposal",
        "alternative_proposal",
        "consensus_target_proposal_id",
    ),
    "idle_consensus_reached": (
        "proposal",
        "alternative_proposal",
        "counterexamples",
    ),
}
DRY_RUN_COMMAND = (
    "python tools/idle_protocol_activate.py "
    "--payload <manual-payload.json> --dry-run --json"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect idle-protocol session state and report the next step.",
    )
    parser.add_argument(
        "--events",
        type=Path,
        default=None,
        help=(
            "Bridge event JSONL path. Defaults to "
            "<runtime bridge root>/shared/events.jsonl."
        ),
    )
    parser.add_argument(
        "--bridge-root",
        type=Path,
        default=None,
        help=(
            "Runtime bridge root used when --events is omitted. Defaults to "
            "AGENT_BRIDGE_RUNTIME_ROOT, AGENT_BRIDGE_ROOT, then repo .agent-bridge."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report only. This is the only mode and is accepted for clarity.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    events_path = args.events
    if events_path is None:
        events_path = resolve_bridge_root(args.bridge_root) / "shared" / "events.jsonl"
    try:
        report = run_idle_protocol_session(
            events_path=events_path,
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
            if report.get("manual_payload_scaffold"):
                print("manual_payload_scaffold: field list only; content required")
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
    scaffold = _manual_payload_scaffold(summary)
    if scaffold is not None:
        report["manual_payload_scaffold"] = scaffold
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


def _manual_payload_scaffold(summary: dict[str, Any]) -> dict[str, Any] | None:
    """Return a field-level scaffold for the next manual payload.

    This intentionally does not generate strategic text or a schema-valid
    payload. It gives the peer enough shape to author the next payload by hand
    and validate it with idle_protocol_activate before any bridge append.
    """
    next_event = summary.get("next_required_event")
    if not isinstance(next_event, dict):
        return None

    event_type = str(next_event.get("event_type") or "")
    if event_type not in EVENT_REQUIRED_FIELDS:
        return None

    reference_hints: dict[str, Any] = {
        "instance_root_proposal_id": summary.get("instance_root_proposal_id"),
        "latest_proposal_id": summary.get("latest_proposal_id"),
    }
    if event_type in {"idle_counter_proposal", "idle_adversarial_review"}:
        reference_hints["responds_to"] = next_event.get("responds_to")

    return {
        "not_a_payload": True,
        "manual_content_required": True,
        "event_type": event_type,
        "round_number": next_event.get("round_number"),
        "required_fields": list(
            BASE_REQUIRED_FIELDS + EVENT_REQUIRED_FIELDS[event_type]
        ),
        "forbidden_fields": list(EVENT_FORBIDDEN_FIELDS[event_type]),
        "reference_hints": reference_hints,
        "dry_run_command": DRY_RUN_COMMAND,
        "safety_notes": [
            "This report is read-only and does not append bridge events.",
            "The scaffold is only a field list; it omits strategic content.",
            "Validate a manually authored payload before using --apply.",
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())
