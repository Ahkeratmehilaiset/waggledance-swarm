# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Schema validation for the runtime agent bridge event stream.

The bridge PowerShell scripts write newline-delimited JSON to
``.agent-bridge/shared/events.jsonl``. This module codifies the current
event shape without changing the writer path: callers can validate events
explicitly, and readers can degrade gracefully by reporting validation issues
instead of failing the bridge loop.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr
from pydantic import ValidationError, field_validator, model_validator


BRIDGE_EVENT_SCHEMA_VERSION = "agent-bridge-event.v1"
KNOWN_AGENTS = frozenset({"codex", "claude", "operator", "system"})
KNOWN_EVENT_TYPES = frozenset({
    "blocked",
    "claim",
    "decision",
    "done",
    "finding",
    "handoff",
    "heartbeat",
    "intent",
    "liveness",
    "message",
    "release",
    "status",
    "test",
    "wake_request",
})
KNOWN_ACK_STATUSES = frozenset({"acknowledged", "received", "seen"})
KNOWN_SEVERITIES = frozenset({"", "low", "medium", "high"})


class BridgeEvent(BaseModel):
    """Canonical bridge event model for events written by Write-AgentEvent."""

    model_config = ConfigDict(extra="allow")

    ts_utc: StrictStr
    agent: StrictStr
    type: StrictStr
    task_id: StrictStr = ""
    status: StrictStr = ""
    severity: StrictStr = ""
    to: StrictStr = ""
    message: StrictStr = ""
    paths: list[StrictStr] = Field(default_factory=list)
    write_scope: list[StrictStr] = Field(default_factory=list)
    run_id: StrictStr = ""
    pid: StrictInt
    cwd: StrictStr
    payload: Any = Field(default_factory=dict)

    @field_validator("ts_utc")
    @classmethod
    def _timestamp_must_be_utc(cls, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("ts_utc must be ISO-8601") from exc
        if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            raise ValueError("ts_utc must carry UTC offset")
        return value

    @field_validator("agent")
    @classmethod
    def _agent_must_be_known(cls, value: str) -> str:
        if value not in KNOWN_AGENTS:
            raise ValueError("agent must be one of the bridge agents")
        return value

    @field_validator("type")
    @classmethod
    def _type_must_be_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("type must be a non-empty string")
        if "\r" in value or "\n" in value:
            raise ValueError("type must be a single-line string")
        return value

    @field_validator("severity")
    @classmethod
    def _severity_must_be_scalar(cls, value: str) -> str:
        if "\r" in value or "\n" in value:
            raise ValueError("severity must be a single-line string")
        return value

    @field_validator("to")
    @classmethod
    def _targets_must_be_known_agents(cls, value: str) -> str:
        if not value:
            return value
        targets = [item.strip() for item in value.split(",") if item.strip()]
        if not targets:
            raise ValueError("to must be empty or comma-separated agents")
        unknown = sorted(set(targets) - KNOWN_AGENTS)
        if unknown:
            raise ValueError(f"to contains unknown bridge agent: {unknown[0]}")
        return value

    @model_validator(mode="after")
    def _event_type_invariants(self) -> "BridgeEvent":
        if self.type == "wake_request" and not self.to.strip():
            raise ValueError("wake_request requires to")
        if self.type in {"claim", "release", "done", "handoff", "blocked"}:
            if not self.task_id.strip():
                raise ValueError(f"{self.type} requires task_id")
        if (
            self.type == "message"
            and self.status in KNOWN_ACK_STATUSES
            and not self.task_id.strip()
        ):
            raise ValueError("ack message requires task_id")
        return self


@dataclass(frozen=True)
class BridgeEventValidationIssue:
    """One JSONL validation issue."""

    line_no: int
    error: str
    raw_excerpt: str = ""
    raw_sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "line_no": self.line_no,
            "error": self.error,
            "raw_excerpt": self.raw_excerpt,
            "raw_sha256": self.raw_sha256,
        }


@dataclass(frozen=True)
class BridgeEventValidationResult:
    """Summary for a bridge event file validation run."""

    schema_version: str
    checked: int
    valid: int
    invalid: int
    issues: tuple[BridgeEventValidationIssue, ...]
    waived_invalid: int = 0
    waived_issues: tuple[BridgeEventValidationIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return self.invalid == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "checked": self.checked,
            "valid": self.valid,
            "invalid": self.invalid,
            "waived_invalid": self.waived_invalid,
            "ok": self.ok,
            "issues": [issue.to_dict() for issue in self.issues],
            "waived_issues": [issue.to_dict() for issue in self.waived_issues],
        }


def validate_event(event: Mapping[str, Any]) -> BridgeEvent:
    """Validate one decoded bridge event mapping."""
    return BridgeEvent.model_validate(event)


def validate_event_line(line: str, *, line_no: int = 1) -> BridgeEvent:
    """Validate one JSONL line from ``events.jsonl``."""
    try:
        decoded = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"line {line_no}: invalid JSON: {exc.msg}") from exc
    if not isinstance(decoded, Mapping):
        raise ValueError(f"line {line_no}: event must be a JSON object")
    try:
        return validate_event(decoded)
    except ValidationError as exc:
        raise ValueError(f"line {line_no}: {_format_validation_error(exc)}") from exc


def validate_event_file(
    events_path: str | Path,
    *,
    tail: int | None = None,
    max_errors: int = 20,
    waived_line_sha256: Mapping[int, str] | None = None,
    waived_line_errors: Mapping[int, str] | None = None,
) -> BridgeEventValidationResult:
    """Validate a bridge JSONL file and return a non-throwing summary."""
    path = Path(events_path)
    waivers = dict(waived_line_sha256 or {})
    waived_errors = dict(waived_line_errors or {})
    lines = _select_lines(path.read_text(encoding="utf-8").splitlines(), tail=tail)
    checked = 0
    valid = 0
    waived_invalid = 0
    issues: list[BridgeEventValidationIssue] = []
    waived_issues: list[BridgeEventValidationIssue] = []
    for line_no, line in lines:
        if not line.strip():
            continue
        checked += 1
        try:
            validate_event_line(line, line_no=line_no)
        except ValueError as exc:
            issue = BridgeEventValidationIssue(
                line_no=line_no,
                error=str(exc),
                raw_excerpt=line[:200],
                raw_sha256=_line_sha256(line),
            )
            if (
                waivers.get(line_no) == issue.raw_sha256
                and waived_errors.get(line_no) == issue.error
            ):
                waived_invalid += 1
                if len(waived_issues) < max_errors:
                    waived_issues.append(issue)
                continue
            if len(issues) < max_errors:
                issues.append(issue)
            continue
        valid += 1
    return BridgeEventValidationResult(
        schema_version=BRIDGE_EVENT_SCHEMA_VERSION,
        checked=checked,
        valid=valid,
        invalid=checked - valid - waived_invalid,
        issues=tuple(issues),
        waived_invalid=waived_invalid,
        waived_issues=tuple(waived_issues),
    )


def _select_lines(
    lines: Iterable[str],
    *,
    tail: int | None,
) -> list[tuple[int, str]]:
    numbered = list(enumerate(lines, start=1))
    if tail is None:
        return numbered
    if tail <= 0:
        return []
    return numbered[-tail:]


def _format_validation_error(error: ValidationError) -> str:
    first = error.errors()[0]
    loc = ".".join(str(item) for item in first.get("loc", ())) or "<event>"
    return f"{loc}: {first.get('msg', 'validation failed')}"


def _line_sha256(line: str) -> str:
    return "sha256:" + hashlib.sha256(line.encode("utf-8")).hexdigest()


__all__ = [
    "BRIDGE_EVENT_SCHEMA_VERSION",
    "BridgeEvent",
    "BridgeEventValidationIssue",
    "BridgeEventValidationResult",
    "KNOWN_AGENTS",
    "KNOWN_EVENT_TYPES",
    "KNOWN_SEVERITIES",
    "validate_event",
    "validate_event_file",
    "validate_event_line",
]
