# SPDX-License-Identifier: BUSL-1.1
"""Manual one-shot starter for idle-protocol v1 deliberation.

This tool is intentionally opt-in. It checks the bridge idle predicates first
and delegates final validation/emission to ``idle_protocol_activate``. It does
not run on a timer, generate implementation work, or convert consensus into
tasks.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.idle_check import (
    evaluate_idle_state,
)
from tools.idle_protocol_activate import (
    DEFAULT_AGENT,
    ActivationError,
    activate_idle_protocol,
    parse_agent_id,
)
from waggledance.core.work_queue import resolve_bridge_root


DEFAULT_SCRATCH_DIR = Path(".codex-audit") / "idle-runner"
SAFE_SCRATCH_PROPOSAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check idle state and optionally start one idle-protocol v1 round.",
    )
    parser.add_argument(
        "--events",
        type=Path,
        default=None,
        help="Bridge events JSONL path. Defaults to <bridge-root>/shared/events.jsonl.",
    )
    parser.add_argument(
        "--claims-dir",
        type=Path,
        default=None,
        help="Bridge claims directory. Defaults to <bridge-root>/work_queue/claims.",
    )
    parser.add_argument(
        "--bridge-root",
        type=Path,
        default=None,
        help=(
            "Path to .agent-bridge directory (default: "
            "AGENT_BRIDGE_RUNTIME_ROOT/AGENT_BRIDGE_ROOT or repo-local)."
        ),
    )
    parser.add_argument("--from-agent", type=parse_agent_id, default=DEFAULT_AGENT)
    parser.add_argument("--to", type=parse_agent_id, default=None)
    parser.add_argument("--task-id", default=None)
    parser.add_argument("--proposal-id", default=None)
    parser.add_argument("--idle-minutes", type=int, default=60)
    parser.add_argument("--pending-ci-count", type=int, default=0)
    parser.add_argument("--open-request-max-age-hours", type=float, default=12.0)
    parser.add_argument("--operator-last-activity-utc", default=None)
    parser.add_argument("--now", default=None)
    parser.add_argument(
        "--receipt-out-dir",
        type=Path,
        default=None,
        help="Optional non-existing output directory for the local MAGMA receipt bundle.",
    )
    parser.add_argument(
        "--scratch-dir",
        type=Path,
        default=DEFAULT_SCRATCH_DIR,
        help="Directory for the temporary payload file passed to the activator.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply",
        "--emit",
        dest="emit",
        action="store_true",
        help="Append the bridge event when the bridge is idle. Default is dry-run.",
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
    bridge_root = resolve_bridge_root(args.bridge_root)
    events_path = args.events or bridge_root / "shared" / "events.jsonl"
    claims_dir = args.claims_dir or bridge_root / "work_queue" / "claims"
    try:
        report = run_idle_protocol_once(
            events_path=events_path,
            claims_dir=claims_dir,
            bridge_root=bridge_root,
            from_agent=args.from_agent,
            to_agent=args.to,
            task_id=args.task_id,
            proposal_id=args.proposal_id,
            idle_minutes=args.idle_minutes,
            pending_ci_count=args.pending_ci_count,
            open_request_max_age_hours=args.open_request_max_age_hours,
            operator_last_activity_utc=(
                _parse_utc(args.operator_last_activity_utc)
                if args.operator_last_activity_utc
                else None
            ),
            now_utc=_parse_utc(args.now) if args.now else datetime.now(timezone.utc),
            emit=args.emit,
            receipt_out_dir=args.receipt_out_dir,
            scratch_dir=args.scratch_dir,
        )
    except IdleRunnerError as exc:
        if args.json:
            print(json.dumps(exc.report, sort_keys=True))
        else:
            print(f"idle runner FAILED: {exc}", file=sys.stderr)
            for error in exc.report.get("errors", []):
                print(f"- {error}", file=sys.stderr)
        return int(exc.report.get("exit_code", 2))

    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(report["decision"])
        if report.get("emitted"):
            print(f"event: {report['activation']['event_path']}")
        elif report["decision"] == "idle_ready":
            print("dry-run: pass --emit to append the bridge event")
        else:
            print("no-op: bridge is active")
    return 0


def run_idle_protocol_once(
    *,
    events_path: Path,
    claims_dir: Path,
    bridge_root: Path,
    from_agent: str,
    to_agent: str | None,
    task_id: str | None,
    proposal_id: str | None,
    idle_minutes: int,
    pending_ci_count: int,
    open_request_max_age_hours: float,
    operator_last_activity_utc: datetime | None,
    now_utc: datetime,
    emit: bool,
    receipt_out_dir: Path | None,
    scratch_dir: Path,
) -> dict[str, Any]:
    try:
        idle_report = evaluate_idle_state(
            events_path=events_path,
            claims_dir=claims_dir,
            now_utc=now_utc,
            idle_minutes=idle_minutes,
            pending_ci_count=pending_ci_count,
            open_request_max_age_hours=open_request_max_age_hours,
            operator_last_activity_utc=operator_last_activity_utc,
        )
    except ValueError as exc:
        raise IdleRunnerError(
            "idle detector could not prove the bridge state",
            {
                "decision": "unknown",
                "emitted": False,
                "errors": [str(exc)],
                "exit_code": 2,
            },
        ) from exc

    if not idle_report["idle"]:
        return {
            "decision": "active",
            "emitted": False,
            "blockers": idle_report["blockers"],
            "idle_report": idle_report,
        }

    payload = build_round_one_payload(
        proposal_id=proposal_id or _default_proposal_id(now_utc, from_agent),
    )
    payload_path = _write_scratch_payload(payload, scratch_dir)
    try:
        activation = activate_idle_protocol(
            payload_path=payload_path,
            events_path=events_path,
            claims_dir=claims_dir,
            bridge_root=bridge_root,
            from_agent=from_agent,
            to_agent=to_agent,
            task_id=task_id,
            idle_minutes=idle_minutes,
            pending_ci_count=pending_ci_count,
            open_request_max_age_hours=open_request_max_age_hours,
            now_utc=now_utc,
            emit=emit,
            receipt_out_dir=receipt_out_dir,
        )
    except ActivationError as exc:
        report = dict(exc.report)
        report.setdefault("emitted", False)
        report["idle_report"] = idle_report
        raise IdleRunnerError("idle activation failed", report) from exc
    finally:
        _remove_scratch_payload(payload_path)

    return {
        "decision": "idle_ready",
        "emitted": activation["emitted"],
        "idle_report": idle_report,
        "activation": activation,
    }


def build_round_one_payload(*, proposal_id: str) -> dict[str, Any]:
    return {
        "protocol_version": "idle-protocol.v1",
        "event_type": "idle_proposal",
        "proposal_id": proposal_id,
        "round_number": 1,
        "proposes_substrate_change": True,
        "problem_statement": (
            "Idle dream rounds can drift into generic bridge meta-work and skip "
            "the operator-authorized competitor tracking cadence from "
            "docs/architecture/DREAM_MODE_AGENDA.md."
        ),
        "proposal": (
            "Start one operator-gated idle-protocol round that asks the peer agent "
            "to choose a WD-lens competitor evidence refresh: inspect "
            "docs/benchmarks/COMPETITIVE_EVIDENCE_MATRIX_2026.md and "
            "docs/benchmarks/rival_local_checks, identify one stale or "
            "public-doc-only axis, and propose the smallest read-only evidence "
            "or local-check step. The round must not make superiority claims, "
            "advance consensus_grade, or create implementation work."
        ),
        "tradeoff_axis": (
            "Competitor-awareness cadence versus keeping idle activation "
            "read-only, evidence-bound, and operator-gated."
        ),
        "simulation_evidence": {
            "kind": "scenario_simulation",
            "summary": (
                "When evaluate_idle_state reports no pending CI, open claims, "
                "fresh scout or RCO requests, recent merges, recent agent messages, "
                "or recent operator activity, this runner prepares one round-one "
                "competitor-tracking proposal; when any blocker exists it emits "
                "nothing and performs no repository or external writes."
            ),
        },
        "charter_alignment": {
            "compatible": True,
            "reasoning": (
                "The runner emits only local bridge deliberation, keeps convergence "
                "operator-gated, never executes external effects, never overclaims "
                "rival results, and never converts agreement into work."
            ),
        },
    }


class IdleRunnerError(ValueError):
    def __init__(self, message: str, report: dict[str, Any]) -> None:
        super().__init__(message)
        self.report = report


def _write_scratch_payload(payload: dict[str, Any], scratch_dir: Path) -> Path:
    proposal_id = str(payload.get("proposal_id", ""))
    if not SAFE_SCRATCH_PROPOSAL_ID.fullmatch(proposal_id):
        raise IdleRunnerError(
            "proposal_id is not safe for a scratch filename",
            {
                "decision": "invalid_proposal_id",
                "emitted": False,
                "errors": [
                    (
                        "proposal_id may contain only ASCII letters, digits, "
                        "'.', '_', and '-', must start with a letter or digit, "
                        "and must be at most 128 characters"
                    )
                ],
                "exit_code": 2,
            },
        )
    scratch_dir.mkdir(parents=True, exist_ok=True)
    path = scratch_dir / f"{proposal_id}.json"
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def _remove_scratch_payload(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _default_proposal_id(now_utc: datetime, from_agent: str) -> str:
    stamp = now_utc.astimezone(timezone.utc).strftime("%Y%m%dt%H%M%S%fz")
    return f"idle-prop-{stamp}-{from_agent}"


def _parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


if __name__ == "__main__":
    raise SystemExit(main())
