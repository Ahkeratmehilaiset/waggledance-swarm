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
import re
from typing import Any, Iterable, Mapping

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr
from pydantic import ValidationError, field_validator, model_validator

BRIDGE_EVENT_SCHEMA_VERSION = "agent-bridge-event.v1"
AGENT_ID_PATTERN = r"^[a-z][a-z0-9_-]{1,32}$"
AGENT_UUID_PATTERN = (
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-" r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
SESSION_ID_PATTERN = r"^[A-Za-z0-9._:-]{1,128}$"
CAPABILITY_PATTERN = r"^[a-z][a-z0-9_.:-]{1,64}$"
LEGACY_AGENTS = frozenset({"codex", "claude", "operator", "system"})
KNOWN_AGENTS = LEGACY_AGENTS
KNOWN_EVENT_TYPES = frozenset(
    {
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
    }
)
KNOWN_ACK_STATUSES = frozenset({"acknowledged", "received", "seen"})
ACK_STATUS_TOKENS = frozenset({"ack", *KNOWN_ACK_STATUSES})
OPEN_REQUEST_STATUS_TOKENS = frozenset(
    {
        "active",
        "blocked",
        "missing",
        "needed",
        "open",
        "proposal",
        "pushed",
        "ready",
        "request",
        "requested",
        "required",
    }
)
STANDARD_PROTOCOL_EVENT_TYPES = frozenset(
    {
        *KNOWN_EVENT_TYPES,
        "peer_review_request",
        "sandbox_drop",
        "simulation_open",
    }
)
RESPONSE_ONLY_STATUS_TOKENS = frozenset(
    {
        "accepted",
        "ack",
        *KNOWN_ACK_STATUSES,
        "answered",
        "approved",
        "closed",
        "done",
        "merged",
        "observed",
        "pass",
        "reported",
        "resolved",
        "superseded",
        "validated",
        "verified",
    }
)
CLOSED_REQUEST_STATUSES = frozenset(
    {
        "accepted",
        *KNOWN_ACK_STATUSES,
        "answered",
        "approved",
        "autonomous_merge_receipt",
        "changes_requested_retracted",
        "changes_requested_resolved",
        "changes_requested_withdrawn",
        "closed",
        "done",
        "finding_retracted",
        "finding_withdrawn",
        "merged",
        "reported",
        "resolved",
        "retracted",
        "rco_finding_retracted",
        "rco_finding_withdrawn",
        "superseded",
        "validated",
        "verified",
        "withdrawn",
    }
)
KNOWN_SEVERITIES = frozenset({"", "low", "medium", "high"})
FULL_GIT_SHA_PATTERN = r"^[0-9a-f]{40}$"
GROK_REVIEW_AGENTS = frozenset({"grok-1", "grok-scout-1"})
GROK_REVIEW_STATUSES = frozenset({"grok_response"})
ALLOWED_NON_AGENT_TARGETS = frozenset({"github/main"})
GROK_FRESHNESS_EPOCH_UTC = "2026-05-31T19:24:00Z"
GROK_PR_WORKTREE_STRICT_EPOCH_UTC = "2026-06-04T08:32:00Z"
GROK_FRESHNESS_REQUIRED_SHA_FIELDS = (
    "remote_main_sha",
    "local_origin_main_sha",
    "worktree_head",
)
GROK_FRESHNESS_OPTIONAL_SHA_FIELDS = (
    "pr_head_sha",
    "reviewed_head_sha",
    "target_head_sha",
)


def is_ack_status(status: str) -> bool:
    """Return whether a status contains a standalone ACK vocabulary token."""
    tokens = {
        token
        for token in re.split(r"[^a-z0-9]+", status.lower())
        if token
    }
    return bool(tokens.intersection(ACK_STATUS_TOKENS))


def is_response_only_status(status: str) -> bool:
    """Return whether status tokens describe a response rather than new work."""
    tokens = _status_tokens(status)
    if "not" in tokens or tokens.intersection({"required", "needed", "missing"}):
        return False
    if tokens.intersection({"request", "requested"}) and not tokens.intersection(
        RESPONSE_ONLY_STATUS_TOKENS - {"pass"}
    ):
        return False
    return bool(tokens.intersection(RESPONSE_ONLY_STATUS_TOKENS))


def is_open_request_status(status: str) -> bool:
    """Return whether the shared exact-token taxonomy marks status as open."""
    normalized = status.lower()
    if normalized in CLOSED_REQUEST_STATUSES or is_ack_status(status):
        return False
    if is_response_only_status(status):
        return False
    return bool(_status_tokens(status).intersection(OPEN_REQUEST_STATUS_TOKENS))


def _status_tokens(status: str) -> set[str]:
    return {
        token
        for token in re.split(r"[^a-z0-9]+", status.lower())
        if token
    }


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
    role: StrictStr = ""
    agent_uuid: StrictStr = ""
    session_id: StrictStr = ""
    capabilities: list[StrictStr] = Field(default_factory=list)
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
        if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(
            parsed
        ):
            raise ValueError("ts_utc must carry UTC offset")
        return value

    @field_validator("agent")
    @classmethod
    def _agent_must_be_valid_id(cls, value: str) -> str:
        if not _is_valid_agent_id(value):
            raise ValueError("agent must match bridge agent id pattern")
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
        invalid = sorted(
            target
            for target in set(targets)
            if not _is_valid_agent_id(target)
            and target not in ALLOWED_NON_AGENT_TARGETS
        )
        if invalid:
            raise ValueError(f"to contains invalid bridge agent id: {invalid[0]}")
        return value

    @field_validator("role")
    @classmethod
    def _role_must_be_valid_id_or_empty(cls, value: str) -> str:
        if value and not _is_valid_agent_id(value):
            raise ValueError("role must match bridge agent id pattern")
        return value

    @field_validator("agent_uuid")
    @classmethod
    def _agent_uuid_must_be_uuid_or_empty(cls, value: str) -> str:
        if value and not re.fullmatch(AGENT_UUID_PATTERN, value):
            raise ValueError("agent_uuid must be a UUID")
        return value

    @field_validator("session_id")
    @classmethod
    def _session_id_must_be_safe_or_empty(cls, value: str) -> str:
        if value and not re.fullmatch(SESSION_ID_PATTERN, value):
            raise ValueError("session_id must match bridge session id pattern")
        return value

    @field_validator("capabilities")
    @classmethod
    def _capabilities_must_be_safe(cls, value: list[str]) -> list[str]:
        for capability in value:
            if not re.fullmatch(CAPABILITY_PATTERN, capability):
                raise ValueError("capabilities must match bridge capability pattern")
        return value

    @model_validator(mode="after")
    def _event_type_invariants(self) -> "BridgeEvent":
        if self.type == "wake_request" and not self.to.strip():
            raise ValueError("wake_request requires to")
        if self.type in {"claim", "release", "done", "handoff", "blocked"}:
            if not self.task_id.strip():
                raise ValueError(f"{self.type} requires task_id")
        if is_ack_status(self.status) and not self.task_id.strip():
            raise ValueError("ack event requires task_id")
        if (
            self.type.lower() not in STANDARD_PROTOCOL_EVENT_TYPES
            and self.to.strip()
            and is_open_request_status(self.status)
            and not self.task_id.strip()
        ):
            raise ValueError("directed custom request requires task_id")
        self._validate_grok_review_freshness()
        return self

    def _validate_grok_review_freshness(self) -> None:
        if not (
            self.agent in GROK_REVIEW_AGENTS
            and self.type == "message"
            and self.status in GROK_REVIEW_STATUSES
        ):
            return
        if not _is_at_or_after_utc(self.ts_utc, GROK_FRESHNESS_EPOCH_UTC):
            return
        if not isinstance(self.payload, Mapping):
            raise ValueError("grok freshness proof requires payload object")
        freshness = self.payload.get("freshness")
        if not isinstance(freshness, Mapping):
            raise ValueError("grok freshness proof required")
        if freshness.get("freshness_ok") is not True:
            raise ValueError("grok freshness_ok must be true")
        for field_name in GROK_FRESHNESS_REQUIRED_SHA_FIELDS:
            value = freshness.get(field_name)
            if not _is_full_git_sha(value):
                raise ValueError(
                    f"grok freshness {field_name} must be lowercase 40-hex sha"
                )
        remote_main_sha = freshness["remote_main_sha"]
        local_origin_main_sha = freshness["local_origin_main_sha"]
        if remote_main_sha != local_origin_main_sha:
            raise ValueError("grok freshness main sha mismatch")
        worktree_head = freshness["worktree_head"]
        pr_review_worktree_heads = []
        for field_name in GROK_FRESHNESS_OPTIONAL_SHA_FIELDS:
            value = freshness.get(field_name)
            if value is not None and not _is_full_git_sha(value):
                raise ValueError(
                    f"grok freshness {field_name} must be lowercase 40-hex sha"
                )
            if value is not None:
                pr_review_worktree_heads.append(value)
        if _is_at_or_after_utc(
            self.ts_utc,
            GROK_PR_WORKTREE_STRICT_EPOCH_UTC,
        ):
            expected_worktree_heads = [local_origin_main_sha]
        else:
            expected_worktree_heads = [
                local_origin_main_sha,
                *pr_review_worktree_heads,
            ]
        if worktree_head not in expected_worktree_heads:
            raise ValueError("grok freshness worktree sha mismatch")


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


def _is_valid_agent_id(value: str) -> bool:
    return bool(re.fullmatch(AGENT_ID_PATTERN, value))


def _is_full_git_sha(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(FULL_GIT_SHA_PATTERN, value))


def _is_at_or_after_utc(value: str, epoch: str) -> bool:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    parsed_epoch = datetime.fromisoformat(epoch.replace("Z", "+00:00"))
    return parsed >= parsed_epoch


def validate_event_line(
    line: str,
    *,
    line_no: int = 1,
    agent_uuid_by_id: Mapping[str, str] | None = None,
) -> BridgeEvent:
    """Validate one JSONL line from ``events.jsonl``."""
    try:
        decoded = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"line {line_no}: invalid JSON: {exc.msg}") from exc
    if not isinstance(decoded, Mapping):
        raise ValueError(f"line {line_no}: event must be a JSON object")
    try:
        model = validate_event(decoded)
    except ValidationError as exc:
        raise ValueError(f"line {line_no}: {_format_validation_error(exc)}") from exc
    _validate_agent_uuid_binding(
        model,
        agent_uuid_by_id=agent_uuid_by_id,
        line_no=line_no,
    )
    return model


def validate_event_file(
    events_path: str | Path,
    *,
    tail: int | None = None,
    max_errors: int = 20,
    waived_line_sha256: Mapping[int, str] | None = None,
    waived_line_errors: Mapping[int, str] | None = None,
    agent_uuid_by_id: Mapping[str, str] | None = None,
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
            validate_event_line(
                line,
                line_no=line_no,
                agent_uuid_by_id=agent_uuid_by_id,
            )
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


def _validate_agent_uuid_binding(
    event: BridgeEvent,
    *,
    agent_uuid_by_id: Mapping[str, str] | None,
    line_no: int,
) -> None:
    if not agent_uuid_by_id:
        return
    expected_uuid = agent_uuid_by_id.get(event.agent)
    if not expected_uuid:
        return
    if not event.agent_uuid:
        raise ValueError(f"line {line_no}: agent_uuid required by bridge agent profile")
    if event.agent_uuid != expected_uuid:
        raise ValueError(
            f"line {line_no}: agent_uuid does not match bridge agent profile"
        )


__all__ = [
    "BRIDGE_EVENT_SCHEMA_VERSION",
    "AGENT_ID_PATTERN",
    "ACK_STATUS_TOKENS",
    "CLOSED_REQUEST_STATUSES",
    "FULL_GIT_SHA_PATTERN",
    "GROK_FRESHNESS_EPOCH_UTC",
    "GROK_REVIEW_AGENTS",
    "GROK_REVIEW_STATUSES",
    "BridgeEvent",
    "BridgeEventValidationIssue",
    "BridgeEventValidationResult",
    "KNOWN_AGENTS",
    "LEGACY_AGENTS",
    "KNOWN_EVENT_TYPES",
    "KNOWN_ACK_STATUSES",
    "KNOWN_SEVERITIES",
    "OPEN_REQUEST_STATUS_TOKENS",
    "RESPONSE_ONLY_STATUS_TOKENS",
    "STANDARD_PROTOCOL_EVENT_TYPES",
    "is_ack_status",
    "is_open_request_status",
    "is_response_only_status",
    "validate_event",
    "validate_event_file",
    "validate_event_line",
]
