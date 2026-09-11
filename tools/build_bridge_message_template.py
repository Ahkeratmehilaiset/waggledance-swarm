#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Build a strict, compact bridge message TEMPLATE (never writes the bridge).

Agents post progress, review requests and review verdicts to the shared bridge
in free-form prose with ad-hoc payload keys. This builder renders one bounded,
machine-checkable event template so that a lane can copy the exact writer
arguments instead of improvising them:

* closed ``kind`` vocabulary -> canonical ``type``/``status`` pair
  (``progress``, ``review_requested``, ``build_consensus``, ``rco_pass``,
  ``changes_requested``);
* full lowercase 40-hex ``head_sha`` carried as ``payload.head`` AND
  ``payload.exact_head`` (the two keys the writer and the exact-head gate read)
  and repeated verbatim in the message text; optional bare-integer ``pr``;
* ``payload.decision_status`` on decision kinds (the closed enum the dormant
  bridge event taxonomy reads) - never a substring parse;
* bounded single-line summary and evidence references (Unicode text accepted;
  control, format and line-break code points refused) and an optional
  ``supersedes_event_id``: the compact reader's bare 64-hex canonical-JSON event
  digest (``tools/bridge_compact_view.event_id``), a legacy ``sha256:<raw line
  digest>`` (``validate_bridge_event``) or a legacy prior ``ts_utc``, labelled in
  ``payload.supersedes_event_ref_kind`` and always reference-only (no authority,
  retraction or closure is implied);
* the envelope key order of ``.agent-bridge/bin/Write-AgentEvent.ps1`` with
  ``pid=0`` and ``cwd="template_not_emitted"``, validated through the canonical
  ``waggledance.core.bridge_event_schema`` model.

The output is a TEMPLATE ONLY. It performs no identity verification (an
``agent``/``agent_uuid``/``session_id`` is echoed, never proven), grants no
approval and appends nothing anywhere. ``rco_pass`` templates are refused for
any agent outside the recognized RCO identity set; passing that check still
does not make the template a verified RCO decision - the writer, the identity
registry and the merge gate keep their own checks. Gate, taxonomy, writer and
permission code are untouched by this tool.

Usage:
    python tools/build_bridge_message_template.py --kind review_requested \\
        --task-id fable-5/example-20260911 --agent fable-5 \\
        --head-sha <40-hex> --pr 1234 --to claude-rco-1 \\
        --summary "PR #1234 ready for review at exact head" \\
        --evidence "tests/tools: 41 passed" --evidence "CI: 6 of 6 green"

Exit codes: 0 template rendered (JSON on stdout; ``--writer-args-only`` prints
just the writer argument values, ``--compact`` one line); 2 invalid input (JSON
``{"error": <reason>, ...}`` on stderr, nothing on stdout).
"""
from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any
import unicodedata

from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.bridge_event_schema import (  # noqa: E402
    AGENT_ID_PATTERN,
    AGENT_UUID_PATTERN,
    ALLOWED_NON_AGENT_TARGETS,
    FULL_GIT_SHA_PATTERN,
    SESSION_ID_PATTERN,
    validate_event,
)

TEMPLATE_VERSION = "wd.bridge_message_template.v1"
TEMPLATE_CWD = "template_not_emitted"

# Closed kind vocabulary -> canonical (type, status). Order is the public enum order.
KIND_EVENT_SHAPES: dict[str, tuple[str, str]] = {
    "progress": ("status", "progress"),
    "review_requested": ("wake_request", "review_requested"),
    "build_consensus": ("decision", "build_consensus_pass"),
    "rco_pass": ("decision", "rco_pass"),
    "changes_requested": ("decision", "changes_requested"),
}
TEMPLATE_KINDS: tuple[str, ...] = tuple(KIND_EVENT_SHAPES)
DECISION_KINDS = frozenset(
    kind for kind, (event_type, _status) in KIND_EVENT_SHAPES.items() if event_type == "decision"
)
HEAD_REQUIRED_KINDS = frozenset(TEMPLATE_KINDS) - {"progress"}
TO_REQUIRED_KINDS = frozenset({"review_requested"})

# Mirrors ``$rcoReviewAgents`` in Write-AgentEvent.ps1 and ``_RECOGNIZED_RCOS`` in
# tools/check_bridge_changes_requested.py (lock-step asserted by the tests).
RECOGNIZED_RCO_AGENTS = frozenset({"claude-rco-1", "claude-rco-2"})
# Payload keys the writer treats as a canonical task binding on rco_pass; the
# template never emits them so the -TaskId binding stays the only binding.
RCO_PASS_FORBIDDEN_PAYLOAD_KEYS = (
    "canonical_task_id",
    "branch",
    "headRefName",
    "head_ref_name",
    "branch_name",
    "accepted_task_ids",
)

MAX_SUMMARY_CHARS = 400
MAX_EVIDENCE_ITEMS = 12
MAX_EVIDENCE_CHARS = 200
MAX_TASK_ID_CHARS = 180
# ``|`` separates message sections and ``;`` separates evidence references.
RESERVED_TEXT_CHARS = ("|", ";")
# Same markers Write-AgentEvent.ps1 refuses (case-insensitive) before any write.
PRIVATE_MARKERS = ("PRIVATE_MARKER", "_DO_NOT_LEAK")
# supersedes_event_id reference kinds (payload.supersedes_event_ref_kind). The
# reference never carries authority, retraction or closure (payload.supersedes_effect).
SUPERSEDES_REF_CANONICAL_JSON = "canonical_json_sha256"  # bare 64-hex compact reader event id
SUPERSEDES_REF_RAW_LINE = "raw_line_sha256"  # legacy sha256:<raw jsonl line digest>
SUPERSEDES_REF_TS_UTC = "ts_utc"  # legacy prior event timestamp
SUPERSEDES_EFFECT = "reference_only"

ENVELOPE_KEY_ORDER: tuple[str, ...] = (
    "ts_utc",
    "agent",
    "type",
    "task_id",
    "status",
    "severity",
    "to",
    "message",
    "paths",
    "write_scope",
    "run_id",
    "pid",
    "cwd",
    "payload",
)

_AGENT_ID_RE = re.compile(AGENT_ID_PATTERN)
_AGENT_UUID_RE = re.compile(AGENT_UUID_PATTERN)
_SESSION_ID_RE = re.compile(SESSION_ID_PATTERN)
_FULL_GIT_SHA_RE = re.compile(FULL_GIT_SHA_PATTERN)
# Writer ``$bridgeTaskBindingPattern`` plus its unsafe-substring rules.
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9._/-]{1,180}$")
_EVENT_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?Z$")
_LINE_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_CANONICAL_JSON_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_GENERATED_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
# Every code point str.splitlines() treats as a line break.
_LINE_BREAK_CHARS = frozenset("\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029")


class BridgeMessageTemplateError(ValueError):
    """Invalid template input; ``reason`` is a stable machine-readable code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def build_bridge_message_template(
    *,
    kind: str,
    task_id: str,
    agent: str,
    summary: str,
    head_sha: str | None = "",
    evidence: Sequence[str] = (),
    to: str | None = "",
    pr: int | None = None,
    agent_uuid: str | None = "",
    session_id: str | None = "",
    role: str | None = "",
    run_id: str | None = "",
    supersedes_event_id: str | None = "",
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Return a validated bridge event template report without writing anything.

    Raises :class:`BridgeMessageTemplateError` on any invalid input. The report
    carries the canonical event envelope under ``event`` and the matching
    ``Write-AgentEvent.ps1`` argument values under ``writer_args``.
    """
    if type(kind) is not str or kind not in KIND_EVENT_SHAPES:
        raise BridgeMessageTemplateError("kind_unknown")
    event_type, status = KIND_EVENT_SHAPES[kind]

    agent_id = _validate_agent_id(agent)
    if kind == "rco_pass" and agent_id not in RECOGNIZED_RCO_AGENTS:
        raise BridgeMessageTemplateError("rco_pass_agent_not_recognized")
    safe_task_id = _validate_task_id(task_id)
    head = _validate_head_sha(head_sha, required=kind in HEAD_REQUIRED_KINDS)
    targets = _normalize_targets(to, required=kind in TO_REQUIRED_KINDS)
    pr_number = _validate_pr(pr)
    safe_summary = _validate_text(
        summary,
        label="summary",
        max_chars=MAX_SUMMARY_CHARS,
        empty_reason="summary_empty",
        invalid_reason="summary_invalid",
    )
    evidence_refs = _validate_evidence(evidence)
    safe_agent_uuid = _validate_optional(agent_uuid, _AGENT_UUID_RE, "agent_uuid_invalid")
    safe_session_id = _validate_optional(session_id, _SESSION_ID_RE, "session_id_invalid")
    safe_role = _validate_optional(role, _AGENT_ID_RE, "role_invalid")
    safe_run_id = _validate_optional(run_id, _SESSION_ID_RE, "run_id_invalid")
    supersedes, supersedes_kind = _validate_supersedes_event_id(supersedes_event_id)
    ts_utc = _format_generated_at(generated_at)

    message = render_message(
        kind=kind,
        task_id=safe_task_id,
        head_sha=head,
        pr=pr_number,
        supersedes_event_id=supersedes,
        summary=safe_summary,
        evidence=evidence_refs,
    )

    payload: dict[str, Any] = {}
    if head:
        payload["head"] = head
        payload["exact_head"] = head
    if pr_number is not None:
        payload["pr"] = pr_number
    if kind in DECISION_KINDS:
        payload["decision_status"] = status
    payload["summary"] = safe_summary
    payload["evidence"] = list(evidence_refs)
    if supersedes:
        payload["supersedes_event_id"] = supersedes
        payload["supersedes_event_ref_kind"] = supersedes_kind
        payload["supersedes_effect"] = SUPERSEDES_EFFECT
    payload["template"] = {
        "version": TEMPLATE_VERSION,
        "kind": kind,
        "template_only": True,
        "identity_verified": False,
        "bridge_event_written": False,
        "approval_granted": False,
    }

    event: dict[str, Any] = {
        "ts_utc": ts_utc,
        "agent": agent_id,
        "type": event_type,
        "task_id": safe_task_id,
        "status": status,
        "severity": "",
        "to": targets,
        "message": message,
        "paths": [],
        "write_scope": [],
        "run_id": safe_run_id,
        "pid": 0,
        "cwd": TEMPLATE_CWD,
        "payload": payload,
    }
    if safe_role:
        event["role"] = safe_role
    if safe_agent_uuid:
        event["agent_uuid"] = safe_agent_uuid
    if safe_session_id:
        event["session_id"] = safe_session_id
    try:
        validate_event(event)
    except ValidationError as exc:
        raise BridgeMessageTemplateError("event_schema_invalid") from exc

    writer_args = {
        "Agent": agent_id,
        "Type": event_type,
        "Status": status,
        "TaskId": safe_task_id,
        "To": targets,
        "Message": message,
        "Role": safe_role,
        "AgentUuid": safe_agent_uuid,
        "SessionId": safe_session_id,
        "RunId": safe_run_id,
        "PayloadJson": json.dumps(
            payload, separators=(",", ":"), allow_nan=False, ensure_ascii=False
        ),
    }
    return {
        "template_version": TEMPLATE_VERSION,
        "kind": kind,
        "template_only": True,
        "identity_verified": False,
        "bridge_event_written": False,
        "approval_granted": False,
        "authority": "none",
        "event": event,
        "writer_args": writer_args,
    }


def render_message(
    *,
    kind: str,
    task_id: str,
    head_sha: str,
    pr: int | None,
    supersedes_event_id: str,
    summary: str,
    evidence: Sequence[str],
) -> str:
    """Render the compact single-line message body.

    ``<kind> task=<id> [head=<sha>] [pr=#<n>] [supersedes=<id>] | <summary> |
    evidence: <ref>; <ref> | template_only=true identity_verified=false``
    """
    header = [f"{kind} task={task_id}"]
    if head_sha:
        header.append(f"head={head_sha}")
    if pr is not None:
        header.append(f"pr=#{pr}")
    if supersedes_event_id:
        header.append(f"supersedes={supersedes_event_id}")
    evidence_text = "; ".join(evidence) if evidence else "none"
    return (
        f"{' '.join(header)} | {summary} | evidence: {evidence_text} | "
        "template_only=true identity_verified=false"
    )


def _validate_agent_id(value: Any) -> str:
    if type(value) is not str or not _AGENT_ID_RE.fullmatch(value):
        raise BridgeMessageTemplateError("agent_invalid")
    return value


def _validate_task_id(value: Any) -> str:
    if type(value) is not str or not _TASK_ID_RE.fullmatch(value):
        raise BridgeMessageTemplateError("task_id_invalid")
    if (
        len(value) > MAX_TASK_ID_CHARS
        or "\\" in value
        or ":" in value
        or ".." in value
        or "//" in value
        or value.startswith("/")
        or value.endswith("/")
    ):
        raise BridgeMessageTemplateError("task_id_invalid")
    return value


def _validate_head_sha(value: Any, *, required: bool) -> str:
    if value is None or (type(value) is str and value == ""):
        if required:
            raise BridgeMessageTemplateError("head_sha_required")
        return ""
    if type(value) is not str or not _FULL_GIT_SHA_RE.fullmatch(value):
        raise BridgeMessageTemplateError("head_sha_invalid")
    return value


def _normalize_targets(value: Any, *, required: bool) -> str:
    if value is None:
        value = ""
    if type(value) is not str:
        raise BridgeMessageTemplateError("to_invalid")
    items = [item.strip() for item in value.split(",")]
    if all(not item for item in items):
        if required:
            raise BridgeMessageTemplateError("to_required")
        return ""
    if any(not item for item in items):
        raise BridgeMessageTemplateError("to_invalid")
    for item in items:
        if not _AGENT_ID_RE.fullmatch(item) and item not in ALLOWED_NON_AGENT_TARGETS:
            raise BridgeMessageTemplateError("to_invalid")
    return ",".join(items)


def _validate_pr(value: Any) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 1:
        raise BridgeMessageTemplateError("pr_invalid")
    return value


def _validate_text(
    value: Any,
    *,
    label: str,
    max_chars: int,
    empty_reason: str,
    invalid_reason: str,
) -> str:
    if type(value) is not str:
        raise BridgeMessageTemplateError(invalid_reason)
    text = value.strip()
    if not text:
        raise BridgeMessageTemplateError(empty_reason)
    if any(char in _LINE_BREAK_CHARS for char in text):
        raise BridgeMessageTemplateError(f"{label}_multiline")
    # Unicode text is accepted; control (Cc), format (Cf), surrogate, private-use
    # and unassigned code points are not.
    if any(unicodedata.category(char).startswith("C") for char in text):
        raise BridgeMessageTemplateError(f"{label}_control_char")
    if any(char in text for char in RESERVED_TEXT_CHARS):
        raise BridgeMessageTemplateError(f"{label}_reserved_char")
    if len(text) > max_chars:
        raise BridgeMessageTemplateError(f"{label}_too_long")
    _assert_no_private_marker(text)
    return text


def _validate_evidence(value: Any) -> list[str]:
    if (
        value is None
        or isinstance(value, (str, bytes, bytearray))
        or not isinstance(value, Sequence)
    ):
        raise BridgeMessageTemplateError("evidence_invalid")
    if len(value) > MAX_EVIDENCE_ITEMS:
        raise BridgeMessageTemplateError("evidence_too_many")
    return [
        _validate_text(
            item,
            label="evidence_item",
            max_chars=MAX_EVIDENCE_CHARS,
            empty_reason="evidence_item_empty",
            invalid_reason="evidence_item_invalid",
        )
        for item in value
    ]


def _validate_optional(value: Any, pattern: re.Pattern[str], reason: str) -> str:
    if value is None:
        return ""
    if type(value) is not str:
        raise BridgeMessageTemplateError(reason)
    if value and not pattern.fullmatch(value):
        raise BridgeMessageTemplateError(reason)
    return value


def _validate_supersedes_event_id(value: Any) -> tuple[str, str]:
    """Return ``(reference, kind)``; ``("", "")`` when no reference was given."""
    if value is None:
        return "", ""
    if type(value) is not str:
        raise BridgeMessageTemplateError("supersedes_event_id_invalid")
    if not value:
        return "", ""
    if _CANONICAL_JSON_DIGEST_RE.fullmatch(value):
        return value, SUPERSEDES_REF_CANONICAL_JSON
    if _LINE_DIGEST_RE.fullmatch(value):
        return value, SUPERSEDES_REF_RAW_LINE
    if _EVENT_TS_RE.fullmatch(value):
        return value, SUPERSEDES_REF_TS_UTC
    raise BridgeMessageTemplateError("supersedes_event_id_invalid")


def _format_generated_at(value: Any) -> str:
    if value is None:
        value = datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise BridgeMessageTemplateError("generated_at_invalid")
    if value.utcoffset() != timedelta(0):
        raise BridgeMessageTemplateError("generated_at_invalid")
    return value.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _assert_no_private_marker(text: str) -> None:
    upper = text.upper()
    for marker in PRIVATE_MARKERS:
        if marker in upper:
            raise BridgeMessageTemplateError("private_marker")


def parse_generated_at(raw: str) -> datetime | None:
    """Parse the CLI ``--generated-at`` value (UTC ISO-8601 ending in ``Z``)."""
    if raw == "":
        return None
    if type(raw) is not str or not _GENERATED_AT_RE.fullmatch(raw):
        raise BridgeMessageTemplateError("generated_at_invalid")
    layout = "%Y-%m-%dT%H:%M:%S.%fZ" if "." in raw else "%Y-%m-%dT%H:%M:%SZ"
    try:
        parsed = datetime.strptime(raw, layout)
    except ValueError as exc:
        raise BridgeMessageTemplateError("generated_at_invalid") from exc
    return parsed.replace(tzinfo=timezone.utc)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Render a strict compact bridge event template (template only: "
            "no bridge write, no identity verification, no approval)."
        ),
    )
    parser.add_argument("--kind", required=True, choices=TEMPLATE_KINDS)
    parser.add_argument("--task-id", required=True, help="canonical task id (branch name)")
    parser.add_argument("--agent", required=True, help="posting bridge agent id (echoed, not verified)")
    parser.add_argument("--summary", required=True, help=f"single-line ASCII summary, max {MAX_SUMMARY_CHARS} chars")
    parser.add_argument("--head-sha", default="", help="full lowercase 40-hex head (required except for progress)")
    parser.add_argument(
        "--evidence",
        action="append",
        default=[],
        help=f"evidence reference (repeatable, max {MAX_EVIDENCE_ITEMS} x {MAX_EVIDENCE_CHARS} chars)",
    )
    parser.add_argument("--to", default="", help="comma-separated recipients (required for review_requested)")
    parser.add_argument("--pr", type=int, default=None, help="bare PR number")
    parser.add_argument("--agent-uuid", default="")
    parser.add_argument("--session-id", default="")
    parser.add_argument("--role", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument(
        "--supersedes-event-id",
        default="",
        help="prior event ts_utc or sha256:<line digest> this template supersedes",
    )
    parser.add_argument("--generated-at", default="", help="UTC ISO-8601 ending in Z (default: now)")
    parser.add_argument("--compact", action="store_true", help="single-line JSON output")
    parser.add_argument(
        "--writer-args-only",
        action="store_true",
        help="print only the Write-AgentEvent.ps1 argument values (no event/report wrapper)",
    )
    return parser


def _configure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_utf8_stdio()
    try:
        report = build_bridge_message_template(
            kind=args.kind,
            task_id=args.task_id,
            agent=args.agent,
            summary=args.summary,
            head_sha=args.head_sha,
            evidence=list(args.evidence),
            to=args.to,
            pr=args.pr,
            agent_uuid=args.agent_uuid,
            session_id=args.session_id,
            role=args.role,
            run_id=args.run_id,
            supersedes_event_id=args.supersedes_event_id,
            generated_at=parse_generated_at(args.generated_at),
        )
    except BridgeMessageTemplateError as exc:
        print(
            json.dumps(
                {"error": exc.reason, "template_only": True, "bridge_event_written": False}
            ),
            file=sys.stderr,
        )
        return 2
    output: dict[str, Any] = report["writer_args"] if args.writer_args_only else report
    if args.compact:
        print(json.dumps(output, separators=(",", ":"), allow_nan=False, ensure_ascii=False))
    else:
        print(json.dumps(output, indent=2, allow_nan=False, ensure_ascii=False))
    return 0


__all__ = [
    "TEMPLATE_VERSION",
    "TEMPLATE_KINDS",
    "KIND_EVENT_SHAPES",
    "DECISION_KINDS",
    "HEAD_REQUIRED_KINDS",
    "TO_REQUIRED_KINDS",
    "RECOGNIZED_RCO_AGENTS",
    "RCO_PASS_FORBIDDEN_PAYLOAD_KEYS",
    "ENVELOPE_KEY_ORDER",
    "SUPERSEDES_REF_CANONICAL_JSON",
    "SUPERSEDES_REF_RAW_LINE",
    "SUPERSEDES_REF_TS_UTC",
    "SUPERSEDES_EFFECT",
    "BridgeMessageTemplateError",
    "build_bridge_message_template",
    "render_message",
    "parse_generated_at",
    "build_parser",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
