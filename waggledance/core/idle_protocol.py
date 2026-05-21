# SPDX-License-Identifier: BUSL-1.1
"""Quality validation helpers for bridge idle-protocol v1 payloads."""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

import jsonschema


ROOT = Path(__file__).resolve().parents[2]
IDLE_PROTOCOL_SCHEMA = ROOT / "schemas" / "v3_13_0" / "idle_protocol.v1.json"
FALSIFIABLE_MARKERS = (
    "if ",
    "when ",
    "unless ",
    "must ",
    "should ",
    "returns ",
    "fails ",
    "blocks ",
    "prevents ",
    "requires ",
    "assert",
    "test",
    "evidence",
)


def validate_idle_proposal(event: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate one idle-protocol payload and return non-throwing errors.

    The JSON schema enforces the structural contract. The additional checks
    reject low-information padding and require counter/review content to be
    empirically testable enough for operator-visible design deliberation.
    """
    errors = _schema_errors(event)
    if isinstance(event, Mapping):
        errors.extend(_quality_errors(event))
    else:
        errors.append("event: must be a mapping")
    return (not errors, errors)


def detect_idle_convergence(
    events: Sequence[Mapping[str, Any]],
    *,
    max_round: int = 10,
    soft_round: int = 5,
) -> dict[str, Any] | None:
    """Detect idle-protocol convergence from recent payloads or bridge events.

    Returns ``None`` while deliberation is still open. Reports are deliberately
    operator-gated: even convergence reports carry ``auto_execute=False``.
    """
    payloads = [_idle_payload(event) for event in events]
    payloads = [payload for payload in payloads if payload is not None]
    if not payloads:
        return None

    valid_payloads: list[Mapping[str, Any]] = []
    invalid_payloads: list[tuple[Mapping[str, Any], list[str]]] = []
    for payload in payloads:
        ok, errors = validate_idle_proposal(dict(payload))
        if ok:
            valid_payloads.append(payload)
        else:
            invalid_payloads.append((payload, errors))

    for payload in valid_payloads:
        if payload.get("event_type") == "idle_charter_violation":
            return {
                "status": "charter_violation",
                "round_number": payload["round_number"],
                "violating_proposal_id": payload["violating_proposal_id"],
                "violation_event_id": payload["proposal_id"],
                "terminate_protocol": True,
                "operator_escalation_required": True,
            }

    soft = _soft_convergence(valid_payloads, soft_round=soft_round)
    if soft is not None:
        return soft

    if invalid_payloads:
        first_invalid, errors = invalid_payloads[0]
        return {
            "status": "invalid_event",
            "proposal_id": first_invalid.get("proposal_id", ""),
            "round_number": first_invalid.get("round_number"),
            "errors": errors,
            "operator_escalation_required": True,
        }

    if not valid_payloads:
        return None

    latest_round = max(int(payload["round_number"]) for payload in valid_payloads)
    if latest_round >= max_round:
        return {
            "status": "hard_convergence",
            "round_number": latest_round,
            "finalist_proposal_ids": _finalists(valid_payloads),
            "operator_gate_required": True,
            "auto_execute": False,
        }
    return None


def _schema_errors(event: Mapping[str, Any]) -> list[str]:
    validator = _validator()
    return [
        f"schema.{_error_path(error)}: {error.message}"
        for error in sorted(validator.iter_errors(event), key=lambda item: item.path)
    ]


def _idle_payload(event: Mapping[str, Any]) -> Mapping[str, Any] | None:
    if event.get("protocol_version") == "idle-protocol.v1":
        return event
    payload = event.get("payload")
    if isinstance(payload, Mapping) and payload.get("protocol_version") == "idle-protocol.v1":
        return payload
    return None


def _soft_convergence(
    payloads: Sequence[Mapping[str, Any]],
    *,
    soft_round: int,
) -> dict[str, Any] | None:
    by_target: dict[str, list[str]] = {}
    round_by_target: dict[str, int] = {}
    for payload in payloads:
        if payload.get("event_type") != "idle_consensus_reached":
            continue
        round_number = int(payload["round_number"])
        if round_number < soft_round:
            continue
        target = str(payload["consensus_target_proposal_id"])
        by_target.setdefault(target, []).append(str(payload["proposal_id"]))
        round_by_target[target] = max(round_by_target.get(target, 0), round_number)
    for target, supporters in by_target.items():
        if len(set(supporters)) >= 2:
            return {
                "status": "soft_convergence",
                "round_number": round_by_target[target],
                "target_proposal_id": target,
                "supporting_consensus_ids": supporters,
                "operator_gate_required": True,
                "auto_execute": False,
            }
    return None


def _finalists(payloads: Sequence[Mapping[str, Any]]) -> list[str]:
    candidates: list[str] = []
    for payload in sorted(payloads, key=lambda item: int(item["round_number"]), reverse=True):
        event_type = str(payload.get("event_type", ""))
        if event_type not in {
            "idle_proposal",
            "idle_counter_proposal",
        }:
            continue
        proposal_id = str(payload.get("proposal_id", ""))
        if proposal_id and proposal_id not in candidates:
            candidates.append(proposal_id)
        if len(candidates) == 3:
            break
    return candidates[:3]


@lru_cache(maxsize=1)
def _validator() -> jsonschema.Draft7Validator:
    schema = json.loads(IDLE_PROTOCOL_SCHEMA.read_text(encoding="utf-8"))
    jsonschema.Draft7Validator.check_schema(schema)
    return jsonschema.Draft7Validator(schema)


def _quality_errors(event: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    _require_substantive_text(
        event.get("problem_statement"),
        "problem_statement",
        errors,
    )
    _require_substantive_text(event.get("tradeoff_axis"), "tradeoff_axis", errors)

    simulation = event.get("simulation_evidence")
    if isinstance(simulation, Mapping):
        _require_substantive_text(
            simulation.get("summary"),
            "simulation_evidence.summary",
            errors,
        )
    alignment = event.get("charter_alignment")
    if isinstance(alignment, Mapping):
        _require_substantive_text(
            alignment.get("reasoning"),
            "charter_alignment.reasoning",
            errors,
        )

    event_type = str(event.get("event_type", ""))
    if event_type == "idle_counter_proposal":
        _require_falsifiable_items(event.get("reasoning_points"), "reasoning_points", errors)
        _require_substantive_text(
            event.get("alternative_proposal"),
            "alternative_proposal",
            errors,
        )
    elif event_type == "idle_adversarial_review":
        _require_falsifiable_items(event.get("counterexamples"), "counterexamples", errors)
    elif event_type == "idle_proposal":
        _require_substantive_text(event.get("proposal"), "proposal", errors)
    return errors


def _require_falsifiable_items(value: Any, field: str, errors: list[str]) -> None:
    if not isinstance(value, list):
        return
    for index, item in enumerate(value):
        if not isinstance(item, str):
            continue
        _require_substantive_text(item, f"{field}[{index}]", errors)
        lowered = f" {item.lower()} "
        if not any(marker in lowered for marker in FALSIFIABLE_MARKERS):
            errors.append(
                f"{field}[{index}]: must include a falsifiable condition, "
                "testable outcome, or concrete evidence marker"
            )


def _require_substantive_text(value: Any, field: str, errors: list[str]) -> None:
    if not isinstance(value, str):
        return
    words = _words(value)
    if len(words) < 5:
        errors.append(f"{field}: must contain at least 5 words")
        return
    unique_ratio = len(set(words)) / len(words)
    if unique_ratio < 0.35:
        errors.append(f"{field}: looks repetitive or padded")


def _words(value: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", value.lower())


def _error_path(error: jsonschema.ValidationError) -> str:
    return ".".join(str(part) for part in error.path) or "<root>"
