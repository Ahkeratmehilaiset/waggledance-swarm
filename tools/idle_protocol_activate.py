# SPDX-License-Identifier: BUSL-1.1
"""Opt-in bridge emitter for idle-protocol v1 deliberation payloads.

The tool is intentionally manual. It never generates proposals, never runs on a
timer, and never converts consensus into work. It validates an operator/agent
provided payload, checks the bridge idle gate for round-1 proposals, and emits
one bridge event only when ``--emit`` is passed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.idle_check import (
    DEFAULT_CLAIMS_DIR,
    DEFAULT_EVENTS_PATH,
    evaluate_idle_state,
)
from waggledance.core.idle_protocol import (
    detect_idle_convergence,
    validate_idle_proposal,
)
from waggledance.core.bridge_event_schema import validate_event


DEFAULT_BRIDGE_ROOT = Path(".agent-bridge")
DEFAULT_AGENT = "codex"
AGENTS = {"codex", "claude"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and optionally emit one idle-protocol v1 bridge event.",
    )
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS_PATH)
    parser.add_argument("--claims-dir", type=Path, default=DEFAULT_CLAIMS_DIR)
    parser.add_argument("--bridge-root", type=Path, default=DEFAULT_BRIDGE_ROOT)
    parser.add_argument("--from-agent", choices=sorted(AGENTS), default=DEFAULT_AGENT)
    parser.add_argument("--to", choices=sorted(AGENTS), default=None)
    parser.add_argument("--task-id", default=None)
    parser.add_argument("--idle-minutes", type=int, default=60)
    parser.add_argument("--pending-ci-count", type=int, default=0)
    parser.add_argument("--open-request-max-age-hours", type=float, default=12.0)
    parser.add_argument("--now", default=None)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply",
        "--emit",
        dest="emit",
        action="store_true",
        help="Append the bridge event. Default is dry-run validation only.",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate only. This is the default and is accepted for clarity.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = activate_idle_protocol(
            payload_path=args.payload,
            events_path=args.events,
            claims_dir=args.claims_dir,
            bridge_root=args.bridge_root,
            from_agent=args.from_agent,
            to_agent=args.to,
            task_id=args.task_id,
            idle_minutes=args.idle_minutes,
            pending_ci_count=args.pending_ci_count,
            open_request_max_age_hours=args.open_request_max_age_hours,
            now_utc=_parse_utc(args.now) if args.now else datetime.now(timezone.utc),
            emit=args.emit,
        )
    except ActivationError as exc:
        if args.json:
            print(json.dumps(exc.report, sort_keys=True))
        else:
            print(f"idle protocol activation FAILED: {exc}", file=sys.stderr)
            for error in exc.report.get("errors", []):
                print(f"- {error}", file=sys.stderr)
        return int(exc.report.get("exit_code", 2))

    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(report["decision"])
        if report.get("emitted"):
            print(f"event: {report['event_path']}")
        else:
            print("dry-run: pass --emit to append the bridge event")
    return 0


def activate_idle_protocol(
    *,
    payload_path: Path,
    events_path: Path,
    claims_dir: Path,
    bridge_root: Path,
    from_agent: str,
    to_agent: str | None,
    task_id: str | None,
    idle_minutes: int,
    pending_ci_count: int,
    open_request_max_age_hours: float,
    now_utc: datetime,
    emit: bool,
) -> dict[str, Any]:
    payload = _load_payload(payload_path)
    if "_DO_NOT_LEAK" in json.dumps(payload, sort_keys=True):
        raise ActivationError(
            "privacy canary detected in payload",
            {
                "decision": "privacy_canary_detected",
                "errors": ["payload contains _DO_NOT_LEAK marker"],
                "exit_code": 2,
            },
        )
    ok, errors = validate_idle_proposal(payload)
    if not ok:
        raise ActivationError(
            "payload failed idle-protocol validation",
            {"decision": "invalid_payload", "errors": errors, "exit_code": 2},
        )
    control_errors = _execution_control_errors(payload)
    if control_errors:
        raise ActivationError(
            "non-consensus idle payload contains execution-control fields",
            {
                "decision": "invalid_payload",
                "errors": control_errors,
                "exit_code": 2,
            },
        )

    events = _read_bridge_events(events_path)
    event_type = str(payload["event_type"])
    round_number = int(payload["round_number"])
    if event_type == "idle_proposal" or round_number == 1:
        try:
            idle_report = evaluate_idle_state(
                events_path=events_path,
                claims_dir=claims_dir,
                now_utc=now_utc,
                idle_minutes=idle_minutes,
                pending_ci_count=pending_ci_count,
                open_request_max_age_hours=open_request_max_age_hours,
            )
        except ValueError as exc:
            raise ActivationError(
                "idle detector could not prove the bridge is idle",
                {
                    "decision": "unknown",
                    "errors": [str(exc)],
                    "exit_code": 2,
                },
            ) from exc
        if not idle_report["idle"]:
            raise ActivationError(
                "round-1 idle proposal requires an idle bridge",
                {
                    "decision": "active",
                    "blockers": idle_report["blockers"],
                    "idle_report": idle_report,
                    "exit_code": 3,
                },
            )
    else:
        if not _has_prior_idle_payload(events):
            raise ActivationError(
                "round-2+ payload requires an existing idle-protocol event",
                {
                    "decision": "missing_prior_idle_event",
                    "errors": ["no prior idle-protocol.v1 payload found in bridge events"],
                    "exit_code": 4,
                },
            )

    target = to_agent or ("claude" if from_agent == "codex" else "codex")
    bridge_event = _bridge_event(
        payload=payload,
        from_agent=from_agent,
        to_agent=target,
        task_id=task_id or f"idle-protocol-{payload['proposal_id']}",
        now_utc=now_utc,
    )
    try:
        validate_event(bridge_event)
    except Exception as exc:
        raise ActivationError(
            "bridge event failed schema validation",
            {
                "decision": "invalid_bridge_event",
                "errors": [str(exc)],
                "exit_code": 2,
            },
        ) from exc
    convergence = detect_idle_convergence([*events, bridge_event])
    report: dict[str, Any] = {
        "decision": "ready",
        "emitted": False,
        "event_type": event_type,
        "proposal_id": payload["proposal_id"],
        "round_number": round_number,
        "from_agent": from_agent,
        "to": target,
        "task_id": bridge_event["task_id"],
        "convergence": convergence,
        "proposed_bridge_event": bridge_event,
    }
    if emit:
        event_path = _append_bridge_event(bridge_root, bridge_event)
        report["emitted"] = True
        report["event_path"] = str(event_path)
    return report


class ActivationError(ValueError):
    def __init__(self, message: str, report: dict[str, Any]) -> None:
        super().__init__(message)
        self.report = report


def _execution_control_errors(payload: Mapping[str, Any]) -> list[str]:
    if payload.get("event_type") == "idle_consensus_reached":
        return []
    errors: list[str] = []
    for field in ("auto_execute", "operator_gate_required"):
        if field in payload:
            errors.append(
                f"{field}: only idle_consensus_reached may carry execution-control fields"
            )
    return errors


def _load_payload(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ActivationError(
            f"could not read payload: {path}",
            {"decision": "missing_payload", "errors": [str(exc)], "exit_code": 2},
        ) from exc
    except json.JSONDecodeError as exc:
        raise ActivationError(
            f"payload is not valid JSON: {path}",
            {"decision": "invalid_json", "errors": [str(exc)], "exit_code": 2},
        ) from exc
    if not isinstance(payload, dict):
        raise ActivationError(
            "payload must be a JSON object",
            {"decision": "invalid_payload", "errors": ["payload must be an object"], "exit_code": 2},
        )
    return payload


def _read_bridge_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _has_prior_idle_payload(events: Sequence[Mapping[str, Any]]) -> bool:
    for event in events:
        if event.get("protocol_version") == "idle-protocol.v1":
            return True
        payload = event.get("payload")
        if isinstance(payload, Mapping) and payload.get("protocol_version") == "idle-protocol.v1":
            return True
    return False


def _bridge_event(
    *,
    payload: Mapping[str, Any],
    from_agent: str,
    to_agent: str,
    task_id: str,
    now_utc: datetime,
) -> dict[str, Any]:
    proposal_id = str(payload["proposal_id"])
    event_type = str(payload["event_type"])
    round_number = int(payload["round_number"])
    return {
        "ts_utc": _iso(now_utc),
        "agent": from_agent,
        "type": "message",
        "task_id": task_id,
        "status": event_type,
        "severity": "",
        "to": to_agent,
        "message": (
            f"idle-protocol.v1 {event_type} round {round_number} "
            f"proposal {proposal_id}; operator gate remains required."
        ),
        "paths": [],
        "write_scope": [],
        "run_id": "",
        "pid": os.getpid(),
        "cwd": str(Path.cwd()),
        "payload": dict(payload),
    }


def _append_bridge_event(bridge_root: Path, event: Mapping[str, Any]) -> Path:
    shared_dir = bridge_root / "shared"
    outbox_dir = bridge_root / "outbox" / str(event["agent"])
    shared_dir.mkdir(parents=True, exist_ok=True)
    outbox_dir.mkdir(parents=True, exist_ok=True)

    line = json.dumps(event, separators=(",", ":"), sort_keys=False) + "\n"
    events_path = shared_dir / "events.jsonl"
    outbox_path = outbox_dir / (_date_name(str(event["ts_utc"])))
    for path in (events_path, outbox_path):
        _append_line_with_retry(path, line)
    last_path = shared_dir / f"last_{event['agent']}.json"
    _write_json_atomic(last_path, json.dumps(event, indent=2))
    return events_path


def _append_line_with_retry(path: Path, line: str) -> None:
    for attempt in range(40):
        try:
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)
            return
        except OSError:
            if attempt == 39:
                raise
            time.sleep(0.025 + (attempt * 0.01))


def _write_json_atomic(path: Path, payload: str) -> None:
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def _date_name(ts_utc: str) -> str:
    return ts_utc[:10] + ".jsonl"


def _parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
