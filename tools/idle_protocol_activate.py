# SPDX-License-Identifier: BUSL-1.1
"""Opt-in bridge emitter for idle-protocol v1 deliberation payloads.

The tool is intentionally manual. It never generates proposals, never runs on a
timer, and never converts consensus into work. It validates an operator/agent
provided payload, checks the bridge idle gate for round-1 proposals, and emits
one bridge event only when ``--emit`` is passed.
"""
from __future__ import annotations

import argparse
from contextlib import suppress
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
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
from tools.verify_magma_receipt import verify_manifest
from waggledance.core.idle_protocol import (
    detect_idle_convergence,
    validate_idle_proposal,
)
from waggledance.core.bridge_event_schema import AGENT_ID_PATTERN, validate_event
from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.magma.evaluation_result import build_evaluation_result
from waggledance.core.magma.receipt import build_magma_receipt
from waggledance.core.magma.receipt_bundle import (
    ReceiptBundleEntry,
    write_receipt_bundle,
)
from waggledance.core.work_queue import resolve_bridge_root


DEFAULT_BRIDGE_ROOT = Path(".agent-bridge")
DEFAULT_AGENT = "codex"
DEFAULT_MAX_INSTANCES_PER_DAY = 5
REFERENCE_FIELDS = (
    "responds_to",
    "consensus_target_proposal_id",
    "rejected_event_id",
    "violating_proposal_id",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and optionally emit one idle-protocol v1 bridge event.",
    )
    parser.add_argument("--payload", type=Path, required=True)
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
    parser.add_argument("--idle-minutes", type=int, default=60)
    parser.add_argument("--pending-ci-count", type=int, default=0)
    parser.add_argument("--open-request-max-age-hours", type=float, default=12.0)
    parser.add_argument("--now", default=None)
    parser.add_argument(
        "--receipt-out-dir",
        type=Path,
        default=None,
        help="Optional non-existing output directory for a local MAGMA receipt bundle.",
    )
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
    bridge_root = resolve_bridge_root(args.bridge_root)
    events_path = args.events or bridge_root / "shared" / "events.jsonl"
    claims_dir = args.claims_dir or bridge_root / "work_queue" / "claims"
    try:
        report = activate_idle_protocol(
            payload_path=args.payload,
            events_path=events_path,
            claims_dir=claims_dir,
            bridge_root=bridge_root,
            from_agent=args.from_agent,
            to_agent=args.to,
            task_id=args.task_id,
            idle_minutes=args.idle_minutes,
            pending_ci_count=args.pending_ci_count,
            open_request_max_age_hours=args.open_request_max_age_hours,
            now_utc=_parse_utc(args.now) if args.now else datetime.now(timezone.utc),
            emit=args.emit,
            receipt_out_dir=args.receipt_out_dir,
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
    receipt_out_dir: Path | None = None,
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
    sequence_errors = _sequence_errors(payload, events)
    if sequence_errors:
        raise ActivationError(
            "payload failed idle-protocol sequence validation",
            {
                "decision": "invalid_sequence",
                "errors": sequence_errors,
                "exit_code": 4,
            },
        )
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
        instances_today = _idle_instances_for_utc_day(events, now_utc)
        if len(instances_today) >= DEFAULT_MAX_INSTANCES_PER_DAY:
            raise ActivationError(
                "idle protocol daily instance limit exhausted",
                {
                    "decision": "rate_limited",
                    "errors": [
                        (
                            "daily idle-protocol instance limit exhausted: "
                            f"{len(instances_today)}/{DEFAULT_MAX_INSTANCES_PER_DAY}"
                        )
                    ],
                    "rate_limit": {
                        "max_instances_per_day": DEFAULT_MAX_INSTANCES_PER_DAY,
                        "instances_today": len(instances_today),
                        "utc_date": _utc_date(now_utc),
                    },
                    "exit_code": 5,
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

    target = to_agent or _default_peer(from_agent)
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
        "rate_limit": {
            "max_instances_per_day": DEFAULT_MAX_INSTANCES_PER_DAY,
            "utc_date": _utc_date(now_utc),
        },
        "proposed_bridge_event": bridge_event,
    }
    if receipt_out_dir is not None:
        try:
            report["receipt_bundle"] = _emit_receipt_bundle(
                bridge_event=bridge_event,
                convergence=convergence,
                out_dir=receipt_out_dir,
                now_utc=now_utc,
            )
        except ValueError as exc:
            raise ActivationError(
                "idle MAGMA receipt bundle failed",
                {
                    "decision": "invalid_receipt_bundle",
                    "errors": [str(exc)],
                    "exit_code": 2,
                },
            ) from exc
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
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ActivationError(
                "bridge events file is not valid JSONL",
                {
                    "decision": "invalid_events_file",
                    "errors": [
                        f"invalid JSON in bridge events at line {line_no}: {exc.msg}"
                    ],
                    "exit_code": 2,
                },
            ) from exc
        if not isinstance(event, dict):
            raise ActivationError(
                "bridge events file contains a non-object event",
                {
                    "decision": "invalid_events_file",
                    "errors": [
                        (
                            f"invalid bridge event at line {line_no}: "
                            "event must be a JSON object"
                        )
                    ],
                    "exit_code": 2,
                },
            )
        events.append(event)
    return events


def _has_prior_idle_payload(events: Sequence[Mapping[str, Any]]) -> bool:
    for event in events:
        if _idle_payload(event) is not None:
            return True
    return False


def _sequence_errors(
    payload: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> list[str]:
    prior_payloads = [
        prior for event in events if (prior := _idle_payload(event)) is not None
    ]
    by_id = {
        str(prior.get("proposal_id", "")): prior
        for prior in prior_payloads
        if prior.get("proposal_id")
    }
    prior_ids = set(by_id)
    event_type = str(payload.get("event_type", ""))
    proposal_id = str(payload.get("proposal_id", ""))
    round_number = int(payload.get("round_number", 0))
    instance_root = _instance_root(payload, by_id)
    instance_payloads = [
        prior
        for prior in prior_payloads
        if instance_root is not None and _instance_root(prior, by_id) == instance_root
    ]
    errors: list[str] = []

    if proposal_id in prior_ids:
        errors.append("proposal_id: already present in bridge events")
    if any(prior.get("event_type") == "idle_charter_violation" for prior in instance_payloads):
        errors.append("instance: previous idle_charter_violation terminated this instance")
    if round_number == 1 and event_type != "idle_proposal":
        errors.append("round_number: round 1 must be an idle_proposal")

    if prior_payloads:
        for field in REFERENCE_FIELDS:
            _require_prior_reference(payload, field, prior_ids, errors)

    if event_type == "idle_consensus_reached" and round_number < 5:
        errors.append("round_number: idle_consensus_reached requires round 5 or later")
    if (
        round_number > 3
        and event_type in {"idle_counter_proposal", "idle_consensus_reached"}
        and not any(
            prior.get("event_type") == "idle_adversarial_review"
            and int(prior.get("round_number", 0)) == 3
            for prior in instance_payloads
        )
    ):
        errors.append(
            "round_number: round 4+ continuation requires a prior round-3 "
            "idle_adversarial_review in the same instance"
        )

    # 2026-05-18 bridge-consensus substrate-invariant #2: late agent joins
    # cannot inject round 6+ unless the instance they reference has a
    # round-1 root AND every payload in that instance is validator-passing.
    # Without this, an agent could inject a round-6 idle_consensus_reached
    # payload referencing an instance whose prior rounds were never emitted
    # or were schema-invalid, bypassing peer review and adversarial scrutiny.
    if round_number >= 6:
        errors.extend(_late_round_predecessor_errors(
            round_number, instance_root, instance_payloads
        ))
    return errors


def _late_round_predecessor_errors(
    round_number: int,
    instance_root: str | None,
    instance_payloads: Sequence[Mapping[str, Any]],
) -> list[str]:
    """Return error strings if the late-round event lacks a complete instance.

    Honors the 2026-05-18 bridge-consensus substrate-invariant #2: a round-6
    or later event must reference an instance whose payloads are
    bridge-resident (i.e., the responds_to chain reaches round 1) AND
    validator-passing (each present payload conforms to idle_protocol.v1).
    """
    errors: list[str] = []
    if instance_root is None:
        errors.append(
            f"round_number: late round {round_number} requires a "
            "round-1 idle_proposal root that is bridge-resident in "
            "the same instance"
        )
        return errors

    instance_rounds: set[int] = set()
    for prior in instance_payloads:
        try:
            instance_rounds.add(int(prior.get("round_number", 0)))
        except (TypeError, ValueError):
            continue

    has_round_one = any(
        int(prior.get("round_number", 0)) == 1
        and str(prior.get("event_type", "")) == "idle_proposal"
        for prior in instance_payloads
    )
    if not has_round_one:
        errors.append(
            f"round_number: late round {round_number} requires a "
            "round-1 idle_proposal to be bridge-resident in the same instance"
        )
    required_rounds = set(range(1, 6))
    missing_rounds = sorted(required_rounds - instance_rounds)
    if missing_rounds:
        errors.append(
            f"round_number: late round {round_number} requires rounds 1..5 "
            "to be bridge-resident in the same instance; missing rounds "
            f"{missing_rounds}"
        )

    invalid_rounds: list[int] = []
    for prior in instance_payloads:
        ok, _validation_errors = validate_idle_proposal(dict(prior))
        if not ok:
            try:
                invalid_rounds.append(int(prior.get("round_number", 0)))
            except (TypeError, ValueError):
                continue
    if invalid_rounds:
        errors.append(
            f"round_number: late round {round_number} requires every prior "
            f"round in the same instance to be validator-passing; rounds "
            f"{sorted(set(invalid_rounds))} fail the idle_protocol.v1 schema"
        )
    return errors


def _instance_root(
    payload: Mapping[str, Any],
    by_id: Mapping[str, Mapping[str, Any]],
) -> str | None:
    proposal_id = str(payload.get("proposal_id", ""))
    try:
        if int(payload.get("round_number", 0)) == 1:
            return proposal_id or None
    except (TypeError, ValueError):
        return None

    target = _first_reference(payload)
    if not target:
        return None
    seen: set[str] = set()
    while target and target not in seen:
        seen.add(target)
        prior = by_id.get(target)
        if prior is None:
            return None
        try:
            if int(prior.get("round_number", 0)) == 1:
                return str(prior.get("proposal_id", "")) or None
        except (TypeError, ValueError):
            return None
        target = _first_reference(prior)
    return None


def _first_reference(payload: Mapping[str, Any]) -> str | None:
    for field in REFERENCE_FIELDS:
        target = payload.get(field)
        if target:
            return str(target)
    return None


def _require_prior_reference(
    payload: Mapping[str, Any],
    field: str,
    prior_ids: set[str],
    errors: list[str],
) -> None:
    if field not in payload:
        return
    target = str(payload.get(field, ""))
    if target not in prior_ids:
        errors.append(f"{field}: target not found in bridge events")


def _idle_payload(event: Mapping[str, Any]) -> Mapping[str, Any] | None:
    if event.get("protocol_version") == "idle-protocol.v1":
        return event
    payload = event.get("payload")
    if isinstance(payload, Mapping) and payload.get("protocol_version") == "idle-protocol.v1":
        return payload
    return None


def _idle_instances_for_utc_day(
    events: Sequence[Mapping[str, Any]],
    now_utc: datetime,
) -> list[str]:
    target_date = _utc_date(now_utc)
    instances: list[str] = []
    for event in events:
        payload = _quota_payload(event)
        if payload is None or payload.get("event_type") != "idle_proposal":
            continue
        round_number = payload.get("round_number")
        if round_number is not None and round_number != 1:
            continue
        try:
            event_date = _utc_date(_parse_utc(str(event["ts_utc"])))
        except (KeyError, TypeError, ValueError):
            continue
        if event_date == target_date:
            instances.append(str(payload.get("proposal_id", "")))
    return instances


def _quota_payload(event: Mapping[str, Any]) -> Mapping[str, Any] | None:
    payload = event.get("payload")
    if isinstance(payload, Mapping) and payload.get("protocol_version") == "idle-protocol.v1":
        return payload
    return None


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


def parse_agent_id(value: str) -> str:
    if not re.fullmatch(AGENT_ID_PATTERN, value):
        raise argparse.ArgumentTypeError(
            "agent id must match ^[a-z][a-z0-9_-]{1,32}$"
        )
    return value


def _default_peer(from_agent: str) -> str:
    if from_agent == "codex":
        return "claude"
    if from_agent == "claude":
        return "codex"
    return "codex"


def _emit_receipt_bundle(
    *,
    bridge_event: Mapping[str, Any],
    convergence: Mapping[str, Any] | None,
    out_dir: Path,
    now_utc: datetime,
) -> dict[str, Any]:
    idle_payload = dict(bridge_event.get("payload", {}))
    evaluation = _evaluation_for_idle_payload(idle_payload, convergence)
    operator_required = _operator_required_for_idle_event(
        str(idle_payload.get("event_type", bridge_event.get("status", ""))),
        convergence,
    )
    if operator_required:
        evaluation["operator_required"] = True
    proposal_id = str(idle_payload.get("proposal_id", "unknown"))
    receipt = build_magma_receipt(
        event_id=f"magma:idle_protocol:{proposal_id}",
        ts_utc=_iso(now_utc + timedelta(seconds=1)),
        risk_class=evaluation["risk_class"],
        payload=idle_payload,
        evaluation_result=evaluation,
        previous_receipt=None,
        policy_digest=sha256_digest({"policy_version": evaluation["policy_version"]}),
        charter_digest=sha256_digest({
            "charter_version": evaluation["charter_version"],
            "payload_charter_alignment": idle_payload.get(
                "charter_alignment",
                {},
            ),
        }),
        rco_decision_digest=sha256_digest({
            "actual_gate": evaluation["actual_gate"],
            "convergence": convergence,
            "event_status": bridge_event["status"],
        }),
        world_snapshot_digest=sha256_digest({
            "agent": bridge_event["agent"],
            "task_id": bridge_event["task_id"],
            "to": bridge_event["to"],
            "ts_utc": bridge_event["ts_utc"],
        }),
        solver_contract_digest=sha256_digest({
            "solver_selection": evaluation["solver_selection"],
            "policy_version": evaluation["policy_version"],
        }),
    )
    if operator_required:
        receipt["operator_gate_required"] = True
    return write_receipt_bundle(
        out_dir=out_dir,
        chain_id=f"magma:idle_protocol:{proposal_id}:v0",
        entries=[
            ReceiptBundleEntry(
                label="idle",
                payload=idle_payload,
                evaluation_result=evaluation,
                receipt=receipt,
            )
        ],
        verify_manifest=verify_manifest,
    )


def _evaluation_for_idle_payload(
    payload: Mapping[str, Any],
    convergence: Mapping[str, Any] | None,
) -> dict[str, Any]:
    event_type = str(payload.get("event_type", ""))
    actual_gate = _gate_for_idle_event(event_type, convergence)
    return build_evaluation_result(
        case_id=f"case:idle_protocol:{payload.get('proposal_id', 'unknown')}",
        subject_type="peer_review",
        target_payload=dict(payload),
        risk_class="local_artifact",
        expected_gate=actual_gate,
        actual_gate=actual_gate,
        verifier_path=[
            "idle_protocol_schema_v1",
            "idle_protocol_quality_validator",
            "idle_protocol_sequence_guard",
            "bridge_event_schema_v1",
            "magma_receipt_verifier_v1",
        ],
        solver_selection=[
            "idle_protocol_validator",
            "idle_protocol_convergence_detector",
        ],
        policy_version="policy:idle_protocol:v1",
        charter_version="charter:v1",
        domain_threshold_version="threshold:idle_protocol:v1",
        verdict=_verdict_for_idle_event(event_type, convergence),
        reason_codes=_reason_codes_for_idle_event(event_type, convergence),
        confidence_score=1.0,
        uncertainty_sources=[],
    )


def _gate_for_idle_event(
    event_type: str,
    convergence: Mapping[str, Any] | None,
) -> str:
    if event_type == "idle_consensus_reached":
        return "require_approval"
    if event_type == "idle_charter_violation":
        return "refuse"
    if convergence and convergence.get("status") in {
        "soft_convergence",
        "hard_convergence",
    }:
        return "require_approval"
    return "review"


def _verdict_for_idle_event(
    event_type: str,
    convergence: Mapping[str, Any] | None,
) -> str:
    if event_type == "idle_charter_violation":
        return "refuse"
    if convergence and convergence.get("status") == "invalid_event":
        return "fail"
    if event_type == "idle_consensus_reached":
        return "review"
    return "pass"


def _operator_required_for_idle_event(
    event_type: str,
    convergence: Mapping[str, Any] | None,
) -> bool:
    return _gate_for_idle_event(event_type, convergence) == "require_approval"


def _reason_codes_for_idle_event(
    event_type: str,
    convergence: Mapping[str, Any] | None,
) -> list[str]:
    reason_codes = [
        f"idle_protocol:{event_type}",
        f"gate:{_gate_for_idle_event(event_type, convergence)}",
        "receipt_bundle:opt_in",
    ]
    if convergence:
        reason_codes.append(f"convergence:{convergence.get('status', 'none')}")
    return reason_codes


def _append_bridge_event(bridge_root: Path, event: Mapping[str, Any]) -> Path:
    shared_dir = bridge_root / "shared"
    outbox_dir = bridge_root / "outbox" / str(event["agent"])
    shared_dir.mkdir(parents=True, exist_ok=True)
    outbox_dir.mkdir(parents=True, exist_ok=True)

    line = json.dumps(event, separators=(",", ":"), sort_keys=False) + "\n"
    events_path = shared_dir / "events.jsonl"
    outbox_path = outbox_dir / (_date_name(str(event["ts_utc"])))
    last_path = shared_dir / f"last_{event['agent']}.json"
    old_last = last_path.read_text(encoding="utf-8") if last_path.exists() else None
    outbox_written = False
    last_written = False
    try:
        _append_line_with_retry(outbox_path, line)
        outbox_written = True
        _write_json_atomic(last_path, json.dumps(event, indent=2))
        last_written = True
        _append_line_with_retry(events_path, line)
    except Exception:
        if last_written:
            _restore_last_file(last_path, old_last)
        if outbox_written:
            _remove_trailing_line_if_exact(outbox_path, line)
        raise
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


def _restore_last_file(path: Path, previous: str | None) -> None:
    with suppress(OSError):
        if previous is None:
            if path.exists():
                path.unlink()
            return
        _write_json_atomic(path, previous)


def _remove_trailing_line_if_exact(path: Path, line: str) -> None:
    with suppress(OSError):
        text = path.read_text(encoding="utf-8")
        if not text.endswith(line):
            return
        remaining = text[: -len(line)]
        if remaining:
            path.write_text(remaining, encoding="utf-8")
        else:
            path.unlink()


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


def _utc_date(value: datetime) -> str:
    return value.astimezone(timezone.utc).date().isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
