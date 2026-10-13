# SPDX-License-Identifier: BUSL-1.1
"""Normalize captured provider-native token records without estimating usage.

This module is deliberately narrower than a generic provider adapter.  It
accepts only schemas captured from launchers used by this fleet and raises
``TelemetryUnknownError`` for every unknown or ambiguous shape.  It does not
launch a model, infer tokens from text/events, or grant runtime authority.

Supported in this first shadow-only slice:

* Codex CLI 0.149.0 session JSONL (``event_msg/token_count``), including the
  native cumulative total and native last-turn total.
* Claude interactive session JSONL (``assistant.message.usage``).  The current
  builder ``--print --output-format json`` terminal envelope and Grok CLI stay
  unsupported until their own captured fixtures are checked in; callers must
  treat those launchers as ``telemetry_unknown`` meanwhile.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Mapping, Sequence


class TelemetryUnknownError(ValueError):
    """The source cannot be accounted for exactly and must fail closed."""


MODEL_TASK_CLASSES = frozenset(
    {
        "cheap_conformance",
        "production_code_tests",
        "lead_triage/design",
        "plan_adversarial_advisory",
        "recognized_rco_review",
        "build_consensus",
    }
)
SUPPORTED_CODEX_CLI_VERSIONS = frozenset({"0.149.0"})
SUPPORTED_CLAUDE_INTERACTIVE_VERSIONS = frozenset({"2.1.246"})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class UsageRecord:
    """One provider-native usage event with immutable provenance bindings."""

    lane: str
    provider: str
    model: str
    task_class: str
    session_id: str
    source_event_id: str
    occurred_at_utc: str
    invocation_argv_sha256: str
    source_log_sha256: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cache_write_input_tokens: int
    reasoning_output_tokens: int
    total_tokens: int
    accounting_mode: str
    cumulative_total_tokens: int | None
    session_complete: bool

    def to_mapping(self) -> dict[str, Any]:
        result = asdict(self)
        result["schema"] = "wd.provider-usage.v1"
        return result

    def canonical_sha256(self) -> str:
        encoded = json.dumps(
            self.to_mapping(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def extract_codex_session_usage(
    rows: Sequence[Mapping[str, Any]],
    *,
    lane: str,
    task_class: str,
    invocation_argv_sha256: str,
    source_log_sha256: str,
    session_complete: bool,
) -> list[UsageRecord]:
    """Extract Codex 0.149.0 native last-turn totals plus cumulative proof."""

    _validate_bindings(
        lane=lane,
        task_class=task_class,
        invocation_argv_sha256=invocation_argv_sha256,
        source_log_sha256=source_log_sha256,
    )
    metas = [row for row in rows if row.get("type") == "session_meta"]
    if len(metas) != 1:
        raise TelemetryUnknownError("exactly one Codex session_meta is required")
    meta = _mapping(metas[0].get("payload"), "Codex session_meta payload")
    session_id = _nonempty(meta.get("id"), "Codex session id")
    version = _nonempty(meta.get("cli_version"), "Codex CLI version")
    if version not in SUPPORTED_CODEX_CLI_VERSIONS:
        raise TelemetryUnknownError(f"unsupported Codex CLI version: {version}")
    if meta.get("originator") != "codex_exec" or meta.get("source") != "exec":
        raise TelemetryUnknownError("unsupported Codex session origin/source")

    model = ""
    records: list[UsageRecord] = []
    seen_event_ids: dict[str, UsageRecord] = {}
    for row in rows:
        row_type = row.get("type")
        payload = row.get("payload")
        if row_type == "turn_context":
            context = _mapping(payload, "Codex turn_context payload")
            model = _nonempty(context.get("model"), "Codex model")
            continue
        if row_type != "event_msg":
            continue
        if not isinstance(payload, Mapping):
            raise TelemetryUnknownError("Codex event_msg payload is not an object")
        if payload.get("type") != "token_count":
            continue
        if not model:
            raise TelemetryUnknownError("Codex token_count precedes turn_context")
        timestamp = _utc_text(row.get("timestamp"), "Codex token timestamp")
        info = _mapping(payload.get("info"), "Codex token_count info")
        last = _mapping(info.get("last_token_usage"), "Codex last_token_usage")
        cumulative = _mapping(
            info.get("total_token_usage"), "Codex total_token_usage"
        )
        _validate_codex_usage(last, "last_token_usage")
        _validate_codex_usage(cumulative, "total_token_usage")
        source_event_id = f"token_count:{timestamp}"
        record = UsageRecord(
            lane=lane,
            provider="openai_codex",
            model=model,
            task_class=task_class,
            session_id=session_id,
            source_event_id=source_event_id,
            occurred_at_utc=timestamp,
            invocation_argv_sha256=invocation_argv_sha256,
            source_log_sha256=source_log_sha256,
            input_tokens=_integer(last, "input_tokens"),
            output_tokens=_integer(last, "output_tokens"),
            cached_input_tokens=_integer(last, "cached_input_tokens"),
            cache_write_input_tokens=_integer(last, "cache_write_input_tokens"),
            reasoning_output_tokens=_integer(last, "reasoning_output_tokens"),
            total_tokens=_integer(last, "total_tokens"),
            accounting_mode="session_cumulative_delta",
            cumulative_total_tokens=_integer(cumulative, "total_tokens"),
            session_complete=bool(session_complete),
        )
        previous = seen_event_ids.get(source_event_id)
        if previous is not None and previous != record:
            raise TelemetryUnknownError("conflicting Codex token event id")
        if previous is None:
            seen_event_ids[source_event_id] = record
            records.append(record)
    if not records:
        raise TelemetryUnknownError("Codex session has no native token_count events")
    return records


def extract_claude_interactive_usage(
    rows: Sequence[Mapping[str, Any]],
    *,
    lane: str,
    task_class: str,
    invocation_argv_sha256: str,
    source_log_sha256: str,
    session_complete: bool,
) -> list[UsageRecord]:
    """Extract captured Claude interactive assistant-message usage records."""

    _validate_bindings(
        lane=lane,
        task_class=task_class,
        invocation_argv_sha256=invocation_argv_sha256,
        source_log_sha256=source_log_sha256,
    )
    records: list[UsageRecord] = []
    by_message: dict[str, tuple[dict[str, Any], UsageRecord]] = {}
    for row in rows:
        if row.get("type") != "assistant":
            continue
        message = row.get("message")
        if not isinstance(message, Mapping):
            raise TelemetryUnknownError("Claude assistant message is not an object")
        if "usage" not in message:
            continue
        version = _nonempty(row.get("version"), "Claude CLI version")
        if version not in SUPPORTED_CLAUDE_INTERACTIVE_VERSIONS:
            raise TelemetryUnknownError(
                f"unsupported Claude interactive CLI version: {version}"
            )
        if row.get("entrypoint") != "sdk-cli":
            raise TelemetryUnknownError("unsupported Claude interactive entrypoint")
        session_id = _nonempty(row.get("sessionId"), "Claude session id")
        message_id = _nonempty(message.get("id"), "Claude message id")
        model = _nonempty(message.get("model"), "Claude model")
        timestamp = _utc_text(row.get("timestamp"), "Claude message timestamp")
        usage = _mapping(message.get("usage"), "Claude message usage")
        _validate_claude_usage(usage)
        input_tokens = _integer(usage, "input_tokens")
        cache_write = _integer(usage, "cache_creation_input_tokens")
        cache_read = _integer(usage, "cache_read_input_tokens")
        output_tokens = _integer(usage, "output_tokens")
        details = usage.get("output_tokens_details", {})
        details = _mapping(details, "Claude output_tokens_details")
        if set(details) - {"thinking_tokens"}:
            raise TelemetryUnknownError("unknown Claude output token detail")
        reasoning = _integer(details, "thinking_tokens", default=0)
        if reasoning > output_tokens:
            raise TelemetryUnknownError("Claude thinking tokens exceed output tokens")
        total = input_tokens + cache_write + cache_read + output_tokens
        record = UsageRecord(
            lane=lane,
            provider="anthropic_claude",
            model=model,
            task_class=task_class,
            session_id=session_id,
            source_event_id=f"message:{message_id}",
            occurred_at_utc=timestamp,
            invocation_argv_sha256=invocation_argv_sha256,
            source_log_sha256=source_log_sha256,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cache_read,
            cache_write_input_tokens=cache_write,
            reasoning_output_tokens=reasoning,
            total_tokens=total,
            accounting_mode="per_event",
            cumulative_total_tokens=None,
            session_complete=bool(session_complete),
        )
        # Claude can repeat one native assistant message in the session JSONL
        # with a different envelope uuid/timestamp.  Compare the immutable
        # message content, not the transport envelope, then count it once.
        fingerprint = {
            "session_id": session_id,
            "message_id": message_id,
            "model": model,
            "usage": usage,
        }
        previous = by_message.get(message_id)
        if previous is not None:
            if previous[0] != fingerprint:
                raise TelemetryUnknownError("conflicting Claude message id reuse")
            continue
        by_message[message_id] = (fingerprint, record)
        records.append(record)
    if not records:
        raise TelemetryUnknownError("Claude session has no native usage messages")
    return records


def _validate_bindings(
    *,
    lane: str,
    task_class: str,
    invocation_argv_sha256: str,
    source_log_sha256: str,
) -> None:
    _nonempty(lane, "lane")
    if task_class not in MODEL_TASK_CLASSES:
        raise TelemetryUnknownError(f"unknown model task class: {task_class!r}")
    for label, digest in (
        ("invocation argv", invocation_argv_sha256),
        ("source log", source_log_sha256),
    ):
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise TelemetryUnknownError(f"{label} SHA-256 is invalid")


def _validate_codex_usage(usage: Mapping[str, Any], label: str) -> None:
    allowed = {
        "input_tokens",
        "cached_input_tokens",
        "cache_write_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "total_tokens",
    }
    if set(usage) != allowed:
        raise TelemetryUnknownError(f"unknown or missing Codex {label} fields")
    input_tokens = _integer(usage, "input_tokens")
    cached = _integer(usage, "cached_input_tokens")
    cache_write = _integer(usage, "cache_write_input_tokens")
    output = _integer(usage, "output_tokens")
    reasoning = _integer(usage, "reasoning_output_tokens")
    total = _integer(usage, "total_tokens")
    if cached > input_tokens or cache_write > input_tokens:
        raise TelemetryUnknownError("Codex cache detail exceeds input tokens")
    if reasoning > output:
        raise TelemetryUnknownError("Codex reasoning detail exceeds output tokens")
    if total != input_tokens + output:
        raise TelemetryUnknownError("Codex native total does not equal input + output")


def _validate_claude_usage(usage: Mapping[str, Any]) -> None:
    required = {
        "input_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "output_tokens",
    }
    allowed = required | {
        "output_tokens_details",
        "server_tool_use",
        "service_tier",
        "cache_creation",
        "inference_geo",
        "iterations",
        "speed",
    }
    if required - set(usage):
        raise TelemetryUnknownError("Claude usage is missing required token fields")
    if set(usage) - allowed:
        raise TelemetryUnknownError("unknown Claude usage fields")
    for field in required:
        _integer(usage, field)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TelemetryUnknownError(f"{label} is not an object")
    return value


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TelemetryUnknownError(f"{label} is missing")
    return value.strip()


def _integer(
    mapping: Mapping[str, Any], field: str, *, default: int | None = None
) -> int:
    if field not in mapping:
        if default is not None:
            return default
        raise TelemetryUnknownError(f"token field is missing: {field}")
    value = mapping[field]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TelemetryUnknownError(f"token field must be a non-negative int: {field}")
    return value


def _utc_text(value: Any, label: str) -> str:
    text = _nonempty(value, label)
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise TelemetryUnknownError(f"{label} is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise TelemetryUnknownError(f"{label} has no timezone")
    parsed = parsed.astimezone(timezone.utc)
    return parsed.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
