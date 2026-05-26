#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Record operator-gated authority readiness without activating authority.

This report observes bridge events and the current sprint synthesis, then
records whether an explicit operator approval exists. It never grants runtime
authority, mutates candidate state, routes traffic, creates a release tag, or
moves Docker aliases.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "waggledance.operator_authority_readiness.v0"
SPRINT_DIR = Path("docs/runs/magma_100h_sprint_2026_05_26")
DEFAULT_PHASE_SYNTHESIS_REFRESH = SPRINT_DIR / "phase_synthesis_refresh.json"
DEFAULT_EVENTS = Path(".agent-bridge/shared/events.jsonl")
DEFAULT_OUTPUT = SPRINT_DIR / "operator_authority_readiness.json"

AUTHORITY_TASK_ID = "operator_gated_authority_activation_decision"
RELEASE_SOAK_TASK_ID = "release_soak_evidence_blocker_resolution"
BRIDGE_TASK_ID = "next100h-operator-authority-readiness-hold-2026-05-26"
APPROVAL_EVENT_TYPES = {"approval", "decision"}
APPROVAL_STATUSES = {"approved", "operator_approved", "authority_approved"}
STRICT_BLOCKED_EXIT_CODE = 2

FALSE_RELEASE_BOUNDARY = {
    "stable_release_claim": False,
    "tag_creation": False,
    "docker_latest_move": False,
    "external_effect_authority_change": False,
}


def _parse_timestamp(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(microsecond=0)


def _format_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.UTC).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not path.exists():
        return events
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _normalise_strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _event_targets_authority(event: dict[str, Any]) -> bool:
    task_id = str(event.get("task_id") or "")
    if task_id in {AUTHORITY_TASK_ID, BRIDGE_TASK_ID}:
        return True

    message = str(event.get("message") or "").lower()
    scopes = [scope.lower() for scope in _normalise_strings(event.get("write_scope"))]
    return (
        AUTHORITY_TASK_ID in message
        or "runtime authority" in message
        or "external_effect_authority_change" in scopes
    )


def explicit_operator_approval_events(
    events: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    approvals: list[dict[str, Any]] = []
    for event in events:
        if str(event.get("agent") or "") != "operator":
            continue
        if str(event.get("type") or "").lower() not in APPROVAL_EVENT_TYPES:
            continue
        if str(event.get("status") or "").lower() not in APPROVAL_STATUSES:
            continue
        if not _event_targets_authority(event):
            continue
        approvals.append(event)
    return approvals


def _remaining_authority_package(
    phase_synthesis_refresh: dict[str, Any],
) -> dict[str, Any]:
    return _remaining_package(phase_synthesis_refresh, AUTHORITY_TASK_ID)


def _remaining_package(
    phase_synthesis_refresh: dict[str, Any],
    package_id: str,
) -> dict[str, Any]:
    for package in phase_synthesis_refresh.get("remaining_work_packages") or []:
        if isinstance(package, dict) and package.get("id") == package_id:
            return dict(package)
    return {}


def _source_phase_synthesis_summary(
    phase_synthesis_refresh: dict[str, Any],
) -> dict[str, Any]:
    release_soak_package = _remaining_package(
        phase_synthesis_refresh,
        RELEASE_SOAK_TASK_ID,
    )
    return {
        "schema_version": phase_synthesis_refresh.get("schema_version"),
        "sprint_id": phase_synthesis_refresh.get("sprint_id"),
        "generated_at_utc": phase_synthesis_refresh.get("generated_at_utc"),
        "ok": phase_synthesis_refresh.get("ok") is True,
        "release_boundary_all_false": (
            phase_synthesis_refresh.get("release_boundary")
            == FALSE_RELEASE_BOUNDARY
        ),
        "remaining_release_soak_package": {
            "id": RELEASE_SOAK_TASK_ID,
            "status": release_soak_package.get("status"),
            "owner": release_soak_package.get("owner"),
        },
    }


def _collect_blockers(
    *,
    phase_synthesis_refresh: dict[str, Any],
    approval_events: list[dict[str, Any]],
) -> list[str]:
    blockers: list[str] = []
    if phase_synthesis_refresh.get("ok") is not True:
        blockers.append("phase_synthesis_refresh_not_ok")
    if phase_synthesis_refresh.get("release_boundary") != FALSE_RELEASE_BOUNDARY:
        blockers.append("phase_synthesis_release_boundary_mutated")

    package = _remaining_authority_package(phase_synthesis_refresh)
    if package.get("status") != "operator_decision_required":
        blockers.append("operator_decision_package_not_waiting")
    if not approval_events:
        blockers.append("explicit_operator_approval_event_missing")
    return blockers


def build_report(
    *,
    phase_synthesis_refresh: dict[str, Any],
    events: Iterable[dict[str, Any]],
    checked_at_utc: dt.datetime | None = None,
) -> dict[str, Any]:
    checked_at_utc = checked_at_utc or _utc_now()
    approval_events = explicit_operator_approval_events(events)
    blockers = _collect_blockers(
        phase_synthesis_refresh=phase_synthesis_refresh,
        approval_events=approval_events,
    )
    approval_seen = bool(approval_events)

    return {
        "schema_version": SCHEMA_VERSION,
        "checked_at_utc": _format_utc(checked_at_utc),
        "ok": True,
        "authority_activation_status": (
            "operator_approved_activation_still_not_granted"
            if approval_seen
            else "hold_operator_approval_required"
        ),
        "activation_blockers": blockers,
        "explicit_operator_approval_found": approval_seen,
        "approval_event_count": len(approval_events),
        "approval_events": [
            {
                "ts_utc": event.get("ts_utc"),
                "agent": event.get("agent"),
                "type": event.get("type"),
                "task_id": event.get("task_id"),
                "status": event.get("status"),
                "message": event.get("message"),
            }
            for event in approval_events
        ],
        "source_phase_synthesis_refresh": _source_phase_synthesis_summary(
            phase_synthesis_refresh
        ),
        "required_operator_task": {
            "id": AUTHORITY_TASK_ID,
            "source_status": _remaining_authority_package(
                phase_synthesis_refresh
            ).get("status"),
            "source_acceptance": _remaining_authority_package(
                phase_synthesis_refresh
            ).get("acceptance"),
        },
        "authority_guardrails": {
            "operator_gate_required": True,
            "runtime_authority_granted": False,
            "runtime_traffic_mutation_applied": False,
            "candidate_state_mutation_applied": False,
            "activation_effect": "none",
            "requires_separate_receipt_bound_activation": True,
        },
        "release_boundary": dict(FALSE_RELEASE_BOUNDARY),
        "read_only_invariants": {
            "no_runtime_authority_granted": True,
            "no_runtime_traffic_mutated": True,
            "no_candidate_state_mutated": True,
            "no_release_boundary_mutated": True,
        },
    }


def build_report_from_paths(
    *,
    phase_synthesis_refresh_path: Path,
    events_path: Path,
    checked_at_utc: dt.datetime | None = None,
) -> dict[str, Any]:
    return build_report(
        phase_synthesis_refresh=_read_json(phase_synthesis_refresh_path),
        events=_read_events(events_path),
        checked_at_utc=checked_at_utc,
    )


def strict_exit_code(report: dict[str, Any]) -> int:
    return STRICT_BLOCKED_EXIT_CODE if report.get("activation_blockers") else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase-synthesis-refresh",
        type=Path,
        default=DEFAULT_PHASE_SYNTHESIS_REFRESH,
    )
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument(
        "--checked-at-utc",
        type=_parse_timestamp,
        help="Override report timestamp, ISO-8601 UTC.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Return a non-zero exit code when authority activation blockers "
            "are present. The report is still written before returning."
        ),
    )
    args = parser.parse_args(argv)

    report = build_report_from_paths(
        phase_synthesis_refresh_path=args.phase_synthesis_refresh,
        events_path=args.events,
        checked_at_utc=args.checked_at_utc,
    )
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    if args.json:
        print(encoded, end="")
    return strict_exit_code(report) if args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
