# SPDX-License-Identifier: BUSL-1.1
"""Quality validation helpers for bridge idle-protocol v1 payloads."""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Any, Mapping

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


def _schema_errors(event: Mapping[str, Any]) -> list[str]:
    validator = _validator()
    return [
        f"schema.{_error_path(error)}: {error.message}"
        for error in sorted(validator.iter_errors(event), key=lambda item: item.path)
    ]


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
