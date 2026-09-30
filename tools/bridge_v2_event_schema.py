# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Schema validation for the runtime agent bridge event stream (Bridge v2 tools-owned kernel).

Bridge v2 contract kernel (dormant; interface bridge-v2-control-interface.v1): a tools-owned
port of the core event schema with no product-package import. Wire shapes, errors, unknown
and refusal handling are unchanged, with two additions:

* F12 (WRITE only): ``validate_event_for_write`` requires every NEW approval-shaped line to
  carry a full lowercase 40-hex ``payload.head`` that the message also names, whatever its
  ``ts_utc`` says. ``approval_shape`` mirrors how the three gates CLASSIFY lines (their
  lowercase, separator and token rules over their eligible types): an RCO pass, a
  build-consensus vote, an approval (including the generic {rco, pass}, approved and
  acknowledged tokens) or a veto-clearing status, as the union of every gate's vocabulary
  (Lead 0465f1d7 option a). It is a FORMAT floor and a shape label, never an authorization.
  Compatibility boundary, deliberately conservative: a new decision/rco_review line, or a
  finding by a non-canonical agent, with approval vocabulary (even acknowledged, approved,
  concur, agree) and a veto-clearing line on those types now needs the head (for example
  the ~11 historical headless decision/acknowledged lines would be refused if written anew).
  The veto channel is never floored (RCO1 e855cb79): a canonical RCO's finding (a veto by type,
  whatever its status), any status the changes gate classifies as a block (its
  _is_blocking_status, ported source-equivalently, so a mixed approval/block status stays
  writable), and the general types the gates never count (message, ack, handoff, ...). The READ path (``validate_event``/``validate_event_line``) stays exactly core
  at all times, so the historical log keeps validating. Read acceptance is shape validity,
  NEVER approval: each gate binds its own semantics (exact head, task, CI, author). Readers can
  use ``commit_head_status`` to label a line's head format.
* F23: ``validate_event_for_write`` accepts a reserved label (``operator``/``system``, as the
  agent or as the role) only with a ``SessionProvenance`` that a trusted entrypoint observed;
  the event's own role, session or environment claims never suffice. Readers get
  ``reserved_label_status`` = ``reserved_label_unverified``: a label in the log is not proof.

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
        "triage_disposition",
        "consumer_tick",
    }
)
KNOWN_ACK_STATUSES = frozenset({"acknowledged", "received", "seen"})
KNOWN_SEVERITIES = frozenset({"", "low", "medium", "high"})
FULL_GIT_SHA_PATTERN = r"^[0-9a-f]{40}$"
GROK_REVIEW_AGENTS = frozenset({"grok-1", "grok-scout-1"})
GROK_REVIEW_STATUSES = frozenset({"grok_response"})
ALLOWED_NON_AGENT_TARGETS = frozenset({"github/main"})
GROK_FRESHNESS_EPOCH_UTC = "2026-05-31T19:24:00Z"
# F12 (write side only, Lead 0465f1d7 option a): every NEW approval-shaped line carries its full
# head. "Approval-shaped" mirrors how the gates CLASSIFY a line, never what they authorize; the
# vocabularies below are copies (no runtime import of gate code), and a fixture proves each gate's
# own sets and classifiers are covered (gate subset of floor):
# * check_rco_pass_present: type.lower() in {decision, rco_review}, status.lower() == rco_pass;
# * idle_consensus_auto_merge: type.lower() in {decision, rco_review, finding} with status.lower()
#   in its RCO_PASS_STATUSES or BUILD_CONSENSUS_STATUSES, and a consensus CLEAR when its
#   separator-normalized type is in DECISION_EVENT_TYPES | {done, test} (or normalizes to empty);
# * check_bridge_changes_requested: a CLEAR (separator-normalized vocabulary) on type.lower() in
#   {decision, rco_review, finding, done, test}; an APPROVAL on decision/rco_review/finding (a
#   canonical RCO's finding is a veto by type) or done + approved_ci_green, by its exact
#   APPROVAL_STATUSES or its generic tokens ({rco, pass}, approved, acknowledged), unless its
#   exact block rules classify the status first.
FLOOR_RCO_PASS_STATUSES = frozenset({"rco_pass"})
FLOOR_BUILD_CONSENSUS_STATUSES = frozenset({"approved", "build_consensus", "build_consensus_pass", "concur",
                                            "concurred", "agree", "agreed"})
FLOOR_APPROVAL_STATUSES = frozenset({"rco_pass", "rco_pass_pending_ci", "build_consensus_pass", "approved",
                                     "approved_ci_green", "acknowledged"})
FLOOR_APPROVAL_TOKENS = frozenset({"approved", "acknowledged"})   # and the generic pair {rco, pass}
FLOOR_CLEAR_STATUSES = frozenset({"no_changes_requested", "no_changes_requested_approved",
                                  "approved_waiver_block_cleared", "lead_no_blocker_rco_pending",
                                  "producer_no_block_reemit_required"})
FLOOR_CHANGES_REQUESTED_PREFIXES = ("changes_requested", "rco_changes_requested")
FLOOR_CHANGES_REQUESTED_CLEAR_SUFFIXES = frozenset({
    "concurrence", "payload_corrected", "addressed_exact_head_ci_pending", "resolved", "resolved_ci_green",
    "resolved_ci_pending", "cleared", "cleared_ci_green", "cleared_ci_pending", "block_clear", "block_cleared",
    "block_resolved", "retracted", "withdrawn"})
# check_bridge_changes_requested._is_blocking_status and its helpers, ported SOURCE-EQUIVALENTLY
# (RCO1 e855cb79 S1): a status that gate classifies as a block is never approval-shaped, so a
# mixed approval/block status (rco_pass_blocked, rco_retraction_acknowledged_head_blocked) stays
# writable without a head. A drift fixture compares every constant and the behaviour with the gate.
GATE_BLOCKING_STATUSES = frozenset({"changes_requested", "rco_block", "blocked", "rco_blocked", "block_requested"})
GATE_BLOCKING_EVENT_TYPES = frozenset({"decision", "rco_review", "finding", "blocked", "test"})
GATE_BLOCKING_CLEAR_TOKENS = frozenset({"clear", "cleared"})
GATE_BLOCKING_RESOLUTION_TOKENS = frozenset({"clear", "cleared", "resolved", "retracted", "withdrawn"})
GATE_BLOCKING_RESOLUTION_NEGATION_TOKENS = frozenset({
    "active", "arent", "cannot", "cant", "denied", "failed", "failing", "fails", "incomplete", "isnt", "never",
    "no", "not", "open", "ongoing", "outstanding", "persist", "persistent", "persisting", "persists", "refused",
    "rejected", "still", "uncleared", "unresolved", "unretracted", "unwithdrawn", "wont", "without", "yet"})
GATE_BLOCKING_CLEAR_COORDINATION_TOKENS = frozenset({"needed", "required", "request", "requested", "supersede",
                                                     "superseded"})
GATE_BLOCKING_WORD_TOKENS = frozenset({"block", "blocked", "blocks", "blocking"})
GATE_NON_BLOCKING_BLOCK_PHRASES = frozenset({"not_blocked", "not_blocking", "not_a_blocker"})
GATE_NON_BLOCKING_CONTEXT_STATUS_PREFIXES = ("ack_", "acknowledged_", "answered_", "received_")
GATE_NON_BLOCKING_CONTEXT_STATUS_SEGMENTS = ("_advisory_", "_corrected_", "_correction_", "_forwarded_",
                                             "_no_remaining_issues_", "_open_followup_", "_resolves_",
                                             "_still_monitoring_")
FLOOR_APPROVAL_TYPES = frozenset({"decision", "rco_review", "finding"})
FLOOR_CLEAR_TYPES = frozenset({"decision", "rco_review", "finding", "done", "test"})
FLOOR_DONE_APPROVAL_STATUSES = frozenset({"approved_ci_green"})  # a generic done is never an approval
FLOOR_RCO_AGENTS = frozenset({"claude-rco-1", "claude-rco-2"})   # canonical RCOs: their finding is a veto BY TYPE
# Compatibility names (7f070e36): every exact approval status, the approval types, the done approval.
COMMIT_HEAD_STATUSES = FLOOR_APPROVAL_STATUSES | FLOOR_BUILD_CONSENSUS_STATUSES
COMMIT_HEAD_TYPES = FLOOR_APPROVAL_TYPES
DONE_COMMIT_HEAD_STATUSES = FLOOR_DONE_APPROVAL_STATUSES
# F23: labels that no lane may self-assert; they need provenance from a trusted entrypoint.
RESERVED_AGENT_LABELS = frozenset({"operator", "system"})
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
    request_id: StrictStr | None = None
    in_reply_to_request_id: StrictStr | None = None
    request_digest: StrictStr | None = None
    in_reply_to_request_digest: StrictStr | None = None
    in_reply_to_requester: dict[str, StrictStr] | None = None
    expected_responders: dict[str, dict[str, StrictStr]] | None = None

    @model_validator(mode="before")
    @classmethod
    def _payload_cannot_override_envelope(cls, value: Any) -> Any:
        # Check raw presence before model defaults can manufacture an envelope.
        if not isinstance(value, Mapping):
            return value
        contract_keys = (
            "agent", "agent_uuid", "session_id", "run_id", "task_id",
            "request_id", "request_digest", "expected_responders",
            "in_reply_to_request_id", "in_reply_to_request_digest",
            "in_reply_to_requester",
        )
        canonical_keys = {key.casefold(): key for key in cls.model_fields}
        payload = value.get("payload")
        # PowerShell's property lookup ignores case. Refuse alternate spellings
        # and case-duplicates at the two parser-visible levels. Nested result
        # objects remain application data, not bridge envelope fields.
        for fields in (value, payload):
            if not isinstance(fields, Mapping):
                continue
            seen: set[str] = set()
            for key in fields:
                if not isinstance(key, str):
                    continue
                folded = key.casefold()
                if folded in seen:
                    raise ValueError(f"duplicate case-insensitive bridge field {key}")
                seen.add(folded)
                if folded in canonical_keys and key != canonical_keys[folded]:
                    raise ValueError(f"bridge field {key} requires canonical lower-case spelling")
        if not isinstance(payload, Mapping):
            return value
        for key in contract_keys:
            if key not in value["payload"]:
                continue
            if key not in value:
                raise ValueError(f"payload contract field {key} requires top-level field")
            try:
                top = json.dumps(value[key], sort_keys=True, separators=(",", ":"), allow_nan=False)
                nested = json.dumps(value["payload"][key], sort_keys=True, separators=(",", ":"), allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"payload contract field {key} is not canonical JSON") from exc
            if top != nested:
                raise ValueError(f"payload contract field {key} conflicts with top-level field")
        return value

    @field_validator("request_id", "in_reply_to_request_id")
    @classmethod
    def _request_identifier(cls, value: str | None) -> str | None:
        if value and not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value):
            raise ValueError("invalid request identifier")
        return value

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
        if (
            self.type == "message"
            and self.status in KNOWN_ACK_STATUSES
            and not self.task_id.strip()
        ):
            raise ValueError("ack message requires task_id")
        self._validate_triage_disposition()
        self._validate_grok_review_freshness()
        return self

    def _validate_triage_disposition(self) -> None:
        if self.type != "triage_disposition":
            return
        if not self.task_id.strip():
            raise ValueError("triage_disposition requires task_id")
        if self.status != "recorded":
            raise ValueError("triage_disposition status must be recorded")
        if not isinstance(self.payload, Mapping):
            raise ValueError("triage_disposition payload must be an object")
        disposition = self.payload.get("disposition")
        if not isinstance(disposition, str) or disposition not in {"ack_dispatch", "defer"}:
            raise ValueError(
                "triage_disposition payload.disposition must be "
                "ack_dispatch or defer"
            )
        target_event_id = self.payload.get("target_event_id")
        if not _is_nonempty_single_line(target_event_id):
            raise ValueError(
                "triage_disposition payload.target_event_id must be "
                "non-empty single-line text"
            )
        if disposition == "defer":
            for field_name in ("reason", "next_condition"):
                if not _is_nonempty_single_line(self.payload.get(field_name)):
                    raise ValueError(
                        f"triage_disposition payload.{field_name} must be "
                        "non-empty single-line text for defer"
                    )

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
    """Validate one decoded bridge event mapping.

    Read path, exactly core: the result is shape validity only and NEVER an approval. A new
    write goes through ``validate_event_for_write``."""
    return BridgeEvent.model_validate(event)


@dataclass(frozen=True)
class SessionProvenance:
    """What a TRUSTED entrypoint observed about the writing session; never read from the event."""

    agent: str
    session_id: str
    observed_by: str


def validate_event_for_write(event: Mapping[str, Any], *,
                             provenance: SessionProvenance | None = None) -> BridgeEvent:
    """Writer-side validation (F23 and F12) for a NEW event.

    F23: ``operator`` or ``system`` as the agent is accepted only when ``provenance`` is a
    ``SessionProvenance`` (not a look-alike) for that agent, with the event's non-empty
    session_id and a named observer. A reserved role on any other agent is refused. Ordinary
    agents are unaffected (their identity binding stays with the registry checks).

    F12: every new approval-shaped line (see ``approval_shape``; never a canonical RCO's finding) must
    carry the exact full head, with no time-based exemption; the event's own ``ts_utc`` never
    relaxes it. This is a FORMAT floor, not an approval: the gates still bind the head to the
    PR, the task, CI and the author. F23 is checked first."""
    model = validate_event(event)
    if model.role in RESERVED_AGENT_LABELS and model.role != model.agent:
        raise ValueError(f"reserved role {model.role} requires the matching reserved agent")
    if model.agent in RESERVED_AGENT_LABELS:
        if (type(provenance) is not SessionProvenance or provenance.agent != model.agent
                or not model.session_id or provenance.session_id != model.session_id
                or not isinstance(provenance.observed_by, str) or not provenance.observed_by.strip()):
            raise ValueError(f"reserved agent label {model.agent} requires verified session provenance")
    _require_commit_head(model)
    return model


def _separated(text: str) -> str:
    """The gates' separator normalization: lowercase, every non-alphanumeric run -> '_', trimmed."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _clear_shaped(status: str) -> bool:
    """check_bridge_changes_requested._is_clear_status (also used by idle_consensus_auto_merge)."""
    normalized = _separated(status)
    if normalized in FLOOR_CLEAR_STATUSES:
        return True
    for prefix in FLOOR_CHANGES_REQUESTED_PREFIXES:
        if normalized.startswith(prefix + "_"):
            return normalized[len(prefix) + 1:] in FLOOR_CHANGES_REQUESTED_CLEAR_SUFFIXES
    return False


def _gate_status_tokens(status: str) -> set:
    """check_bridge_changes_requested._status_tokens."""
    return {token for token in re.split(r"[^a-z0-9]+", status.lower()) if token}


def _gate_non_blocking_context(status: str) -> bool:
    """check_bridge_changes_requested._has_non_blocking_context_status."""
    normalized = _separated(status)
    if normalized.startswith(GATE_NON_BLOCKING_CONTEXT_STATUS_PREFIXES):
        return True
    bounded = f"_{normalized}_"
    return any(segment in bounded for segment in GATE_NON_BLOCKING_CONTEXT_STATUS_SEGMENTS)


def _gate_non_blocking_block_phrase(status: str) -> bool:
    """check_bridge_changes_requested._has_non_blocking_block_phrase."""
    normalized = _separated(status)
    if (normalized.startswith(("block_", "blocked_", "rco_block")) or "block_requested" in normalized
            or "changes_requested" in normalized):
        return False
    bounded = f"_{normalized}_"
    return any(f"_{phrase}_" in bounded for phrase in GATE_NON_BLOCKING_BLOCK_PHRASES)


def _gate_blocking(status: str, event_type: str = "") -> bool:
    """check_bridge_changes_requested._is_blocking_status, ported SOURCE-EQUIVALENTLY (the drift
    fixture compares it with the gate's own function over a corpus). A block there is never an
    approval there, so the floor must never refuse it: refusing to record a veto fails open."""
    normalized = _separated(status)
    normalized_event_type = _separated(event_type)
    if normalized_event_type and normalized_event_type not in GATE_BLOCKING_EVENT_TYPES:
        return False
    if normalized in GATE_BLOCKING_STATUSES:
        return True
    for prefix in FLOOR_CHANGES_REQUESTED_PREFIXES:
        if normalized == prefix:
            return True
        if not normalized.startswith(prefix + "_"):
            continue
        suffix = normalized[len(prefix) + 1:]
        if not suffix:
            return True
        if suffix in FLOOR_CHANGES_REQUESTED_CLEAR_SUFFIXES:
            return False
        return True
    if _clear_shaped(status):
        return False
    if _gate_non_blocking_context(status):
        return False
    if _gate_non_blocking_block_phrase(status):
        return False
    tokens = _gate_status_tokens(status)
    if {"changes", "requested"}.issubset(tokens):
        return True
    if not tokens.intersection(GATE_BLOCKING_WORD_TOKENS):
        return False
    if "rco" in tokens:
        return True
    if tokens.intersection(GATE_BLOCKING_RESOLUTION_TOKENS):
        if tokens.intersection(GATE_BLOCKING_RESOLUTION_NEGATION_TOKENS):
            return True
        return False
    if "preflight" in tokens and tokens.intersection(GATE_BLOCKING_CLEAR_TOKENS):
        return False
    if tokens.intersection(GATE_BLOCKING_CLEAR_TOKENS) and tokens.intersection(GATE_BLOCKING_CLEAR_COORDINATION_TOKENS):
        return False
    if {"classifier", "artifact"}.issubset(tokens) and "veto" in tokens and tokens.intersection({"no", "false"}):
        return False
    return True


def _approval_tokens(status: str) -> bool:
    """check_bridge_changes_requested._is_approval_status's generic token rule."""
    tokens = _gate_status_tokens(status)
    return {"rco", "pass"} <= tokens or bool(tokens & FLOOR_APPROVAL_TOKENS)


def approval_shape(event: BridgeEvent) -> str | None:
    """How the gates would CLASSIFY this line: ``rco_pass``, ``build_consensus``, ``approval``,
    ``veto_clear``, or None for a general line the gates never count. A shape for the F12 format
    floor, NEVER an authorization: whether a line counts is still each gate's decision (agent,
    task, exact head, CI, author).

    The veto channel comes first (RCO1 e855cb79 B1): a canonical RCO's ``finding`` is a veto BY TYPE
    in check_bridge_changes_requested, whatever its status (even clear- or pass-looking), so it is
    NEVER floored; refusing to record a veto would fail open. idle_consensus_auto_merge's counting
    of such a finding as a pass or a clear is that gate's own ordering issue, for a separate
    operator-explicit gate change. A status the changes gate classifies as a block (the ported
    ``_gate_blocking``) is never approval-shaped by the changes-gate token rule either."""
    kind, status = event.type.lower(), event.status.lower()
    if kind == "finding" and event.agent in FLOOR_RCO_AGENTS:
        return None
    kind_separated = _separated(event.type)
    if _clear_shaped(event.status) and (kind in FLOOR_CLEAR_TYPES or kind_separated in FLOOR_CLEAR_TYPES
                                        or not kind_separated):
        return "veto_clear"
    if kind in FLOOR_APPROVAL_TYPES:
        if status in FLOOR_RCO_PASS_STATUSES:
            return "rco_pass"
        if status in FLOOR_BUILD_CONSENSUS_STATUSES:
            return "build_consensus"
        if (status in FLOOR_APPROVAL_STATUSES or _approval_tokens(status)) and not _gate_blocking(event.status, kind):
            return "approval"
    if kind == "done" and status in FLOOR_DONE_APPROVAL_STATUSES:
        return "approval"
    return None


def _is_commit_approval(event: BridgeEvent) -> bool:
    return approval_shape(event) is not None


def _require_commit_head(event: BridgeEvent) -> None:
    """F12 head FORMAT floor for an approval-shaped line; independent of the event's own timestamp."""
    if not _is_commit_approval(event):
        return
    head = event.payload.get("head") if isinstance(event.payload, Mapping) else None
    if not _is_full_git_sha(head):
        raise ValueError(f"{event.status} head must be lowercase 40-hex sha")
    if head not in event.message:
        raise ValueError(f"{event.status} message must contain exact head")


def commit_head_status(event: BridgeEvent) -> str:
    """For readers: the F12 head FORMAT of a line. A label, NEVER an authorization.

    ``not_approval_shaped`` (a general line the gates never count), or for an approval-shaped
    line ``approval_shaped_head_format_valid`` / ``approval_shaped_head_format_invalid`` (for
    example a historical line, or one from a writer without the floor). No label says
    "authorized": a gate must still bind the head to the PR, the task, CI and the author."""
    if not _is_commit_approval(event):
        return "not_approval_shaped"
    try:
        _require_commit_head(event)
    except ValueError:
        return "approval_shaped_head_format_invalid"
    return "approval_shaped_head_format_valid"


def reserved_label_status(event: BridgeEvent) -> str:
    """For readers: a reserved label in the log is never proof of its origin."""
    if event.agent in RESERVED_AGENT_LABELS or event.role in RESERVED_AGENT_LABELS:
        return "reserved_label_unverified"
    return "not_reserved"


def _is_valid_agent_id(value: str) -> bool:
    return bool(re.fullmatch(AGENT_ID_PATTERN, value))


def _is_full_git_sha(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(FULL_GIT_SHA_PATTERN, value))


def _is_nonempty_single_line(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and "\r" not in value
        and "\n" not in value
    )


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
        decoded = _decode_event_json_pairs(json.loads(line, object_pairs_hook=_JsonObjectPairs))
    except json.JSONDecodeError as exc:
        raise ValueError(f"line {line_no}: invalid JSON: {exc.msg}") from exc
    except ValueError as exc:
        raise ValueError(f"line {line_no}: {exc}") from exc
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


class _JsonObjectPairs(list):
    """Keep raw JSON object keys until event/payload duplicates are checked."""


def _decode_event_json_pairs(
    value: Any, *, check_keys: bool = True, event_root: bool = True
) -> Any:
    if isinstance(value, _JsonObjectPairs):
        decoded: dict[str, Any] = {}
        seen: set[str] = set()
        for key, item in value:
            folded = key.casefold()
            if check_keys and folded in seen:
                raise ValueError(f"duplicate case-insensitive bridge field {key}")
            seen.add(folded)
            decoded[key] = _decode_event_json_pairs(
                item, check_keys=event_root and folded == "payload", event_root=False
            )
        return decoded
    if isinstance(value, list):
        return [
            _decode_event_json_pairs(item, check_keys=False, event_root=False)
            for item in value
        ]
    return value


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
    "COMMIT_HEAD_STATUSES",
    "COMMIT_HEAD_TYPES",
    "DONE_COMMIT_HEAD_STATUSES",
    "FLOOR_RCO_PASS_STATUSES",
    "FLOOR_BUILD_CONSENSUS_STATUSES",
    "FLOOR_APPROVAL_STATUSES",
    "FLOOR_APPROVAL_TOKENS",
    "FLOOR_CLEAR_STATUSES",
    "FLOOR_CHANGES_REQUESTED_PREFIXES",
    "FLOOR_CHANGES_REQUESTED_CLEAR_SUFFIXES",
    "GATE_BLOCKING_STATUSES",
    "GATE_BLOCKING_EVENT_TYPES",
    "FLOOR_APPROVAL_TYPES",
    "FLOOR_CLEAR_TYPES",
    "FLOOR_DONE_APPROVAL_STATUSES",
    "FLOOR_RCO_AGENTS",
    "RESERVED_AGENT_LABELS",
    "SessionProvenance",
    "validate_event_for_write",
    "approval_shape",
    "commit_head_status",
    "reserved_label_status",
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
    "KNOWN_SEVERITIES",
    "validate_event",
    "validate_event_file",
    "validate_event_line",
]
