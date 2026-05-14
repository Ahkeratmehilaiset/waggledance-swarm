# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""BehaviorCapture v1 -- operator workflow capture for shadow replay.

Wraps an existing operator tool invocation, records what it did
(args, env-var NAMES not values, input/output state refs, stdout
artifact, exit code), runs a PII / credential scan over output, and
emits a behavior.captured MAGMA event. The captured record feeds
ShadowRunner and DivergenceAnalyzer; payload content stays out of
the analyzer surface (refs + hashes only).

Capture is OPT-IN per ToolDescriptor (capture_supported=True). Stdin
payload capture requires an additional per-invocation consent token
+ ToolDescriptor.capture_stdin=True; otherwise only stdin hash is
recorded.

Design spec:
iterations/anchor_use_case/sprint_1/claude_lane/behavior_capture_spec.md
"""
from __future__ import annotations

import hashlib
import subprocess
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Callable, Optional


# --------------------------------------------------------------------------
# Sensitivity classes -- mirror StateHandle.sensitive_class
# --------------------------------------------------------------------------


class SensitiveClass(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    RESTRICTED = "restricted"
    SECRET = "secret"
    OPAQUE = "opaque"


# --------------------------------------------------------------------------
# Retention windows (sensitive-class aware, per spec edit E3)
# --------------------------------------------------------------------------


RETENTION_DAYS_BY_CLASS: dict[str, Optional[int]] = {
    "public": 30,
    "internal": 30,
    "restricted": 7,
    # secret + opaque: hash-only -> no payload retained;
    # represented as None to signal "no retention window applies"
    "secret": None,
    "opaque": None,
}


def is_payload_retained(sensitive_class: str) -> bool:
    """Return True if any payload is retained at all for this class."""
    return sensitive_class in ("public", "internal", "restricted")


def retention_window_days(sensitive_class: str) -> Optional[int]:
    """Return the retention floor in days, or None for hash-only classes."""
    return RETENTION_DAYS_BY_CLASS.get(sensitive_class)


# --------------------------------------------------------------------------
# Tool invocation + record shapes
# --------------------------------------------------------------------------


@dataclass
class ToolInvocation:
    """A request to capture one tool invocation."""

    tool_descriptor_id: str
    invocation_args: list[str]                    # CLI args, args=list form
    invocation_env_keys: list[str]                # env var NAMES only
    cwd: str
    input_state_refs: list[str] = field(default_factory=list)
    output_state_refs: list[str] = field(default_factory=list)
    md_injection_files: list[str] = field(default_factory=list)
    stdin_payload: Optional[bytes] = None         # raw stdin if any
    pipeline_id: Optional[str] = None             # set by caller for
                                                   # multi-stage workflows
    parent_capture_id: Optional[str] = None       # set by caller for chaining


@dataclass
class CapturedBehaviorRecord:
    """Persisted record of one captured invocation."""

    capture_id: str
    tool_descriptor_id: str
    invocation_args: list[str]
    invocation_env_keys: list[str]
    invocation_ts_utc: str
    input_state_refs: list[str]
    output_state_refs: list[str]
    stdout_artifact_uri: str                      # restricted StateHandle ref
    stderr_artifact_uri: str
    exit_code: int
    elapsed_ms: int
    md_injection_files: list[str]
    classification_summary: str                   # one-line, redacted
    sensitive_class: str
    operator_review_status: str                   # pending / approved /
                                                   # excluded
    stdin_hash_sha256: Optional[str]              # always recorded
    stdin_artifact_uri: Optional[str]             # only if consent granted
    pipeline_id: Optional[str]
    parent_capture_id: Optional[str]
    auth_mode: Optional[str]                      # from MfaPolicy if set
    pii_scan_hits: list[str]                      # categories only, not
                                                   # raw matches


# --------------------------------------------------------------------------
# ToolDescriptor view -- the subset BehaviorCapture reads
# --------------------------------------------------------------------------


@dataclass
class ToolDescriptorCaptureView:
    """The subset of SCH-001 ToolDescriptor BehaviorCapture consults."""

    tool_descriptor_id: str
    capture_supported: bool
    capture_stdin: bool
    capture_payloads: bool                        # row-level DB capture
                                                   # opt-in
    sensitive_class: str                          # mirrored from primary
                                                   # output_state_ref
    auth_mode: Optional[str]


# --------------------------------------------------------------------------
# Stop / refusal exception
# --------------------------------------------------------------------------


class CaptureRefused(Exception):
    """Raised when capture is not permitted (opaque, no consent, etc.)."""

    def __init__(self, tool_descriptor_id: str, reason: str):
        super().__init__(f"capture refused for {tool_descriptor_id}: {reason}")
        self.tool_descriptor_id = tool_descriptor_id
        self.reason = reason


# --------------------------------------------------------------------------
# BehaviorCapture itself
# --------------------------------------------------------------------------


@dataclass
class BehaviorCapture:
    """Capture a baseline tool invocation for shadow replay.

    Pluggable hooks; v1 wires mock implementations in tests. Real
    integrations come in the next PRs.
    """

    # --- hooks the caller injects --------------------------------------------

    fetch_tool_descriptor: Callable[[str], Optional[ToolDescriptorCaptureView]]
    """Resolve tool_descriptor_id -> SCH-001 capture view or None."""

    operator_scope_policy_check: Callable[[str], bool]
    """Return True if operator pre-approved capture for this tool."""

    pii_scan: Callable[[bytes], list[str]]
    """Run PII / credential scan over output; return categories hit."""

    persist_artifact: Callable[[str, bytes], str]
    """Persist artifact (kind, content) -> restricted StateHandle URI."""

    emit_magma_event: Callable[[dict], str]
    """Emit a behavior.captured event; return event_id."""

    consent_token_validator: Callable[[str, str], bool]
    """(tool_descriptor_id, token) -> True if token is valid + single-use."""

    subprocess_runner: Optional[Callable[..., "subprocess.CompletedProcess"]] = None
    """Override subprocess.run; tests inject a fake."""

    # --- gate config ---------------------------------------------------------

    classification_summary_max_chars: int = 200

    # =========================================================================
    # PUBLIC API
    # =========================================================================

    def capture(self, invocation: ToolInvocation,
                 stdin_consent_token: Optional[str] = None) -> CapturedBehaviorRecord:
        """Capture one invocation.

        Refuses with CaptureRefused if:
        * tool descriptor unknown
        * capture_supported=False
        * sensitive_class=opaque (no capture allowed even with consent)
        * operator scope policy denies
        """
        tool = self.fetch_tool_descriptor(invocation.tool_descriptor_id)
        if tool is None:
            self._emit_refused(invocation.tool_descriptor_id,
                                  "unknown tool descriptor")
            raise CaptureRefused(invocation.tool_descriptor_id,
                                  "unknown tool descriptor")
        if not tool.capture_supported:
            self._emit_refused(invocation.tool_descriptor_id,
                                  "ToolDescriptor.capture_supported is False")
            raise CaptureRefused(invocation.tool_descriptor_id,
                                  "ToolDescriptor.capture_supported is False")
        if tool.sensitive_class == SensitiveClass.OPAQUE.value:
            self._emit_refused(invocation.tool_descriptor_id,
                                  "sensitive_class=opaque refuses capture")
            raise CaptureRefused(invocation.tool_descriptor_id,
                                  "sensitive_class=opaque refuses capture")
        if not self.operator_scope_policy_check(invocation.tool_descriptor_id):
            self._emit_refused(invocation.tool_descriptor_id,
                                  "operator scope policy denied")
            raise CaptureRefused(invocation.tool_descriptor_id,
                                  "operator scope policy denied")

        # Per Codex RCO round-2 fix: secret sensitive_class is hash-only;
        # raw payloads (stdin/stdout/stderr) must NEVER be passed to
        # persist_artifact. Opaque is already refused above. The
        # `is_payload_retained()` helper centralises the policy.
        retain_payload = is_payload_retained(tool.sensitive_class)

        # Stdin handling: hash always; payload only if consent + opt-in
        # AND sensitive_class permits payload retention.
        #
        # BC1 (claude-iter-review-behavior-capture-2026-05-13): when stdin
        # is retained, scan for PII BEFORE persist_artifact so the
        # categories are surfaced in pii_scan_hits and operator_review_status
        # routes the artifact through pending review -- consistent with the
        # stdout/stderr ordering at line 267-269. Without the scan, an
        # operator-consented stdin payload could carry PII straight into
        # restricted-class storage with no review flag.
        pii_hits: list[str] = []
        stdin_hash = None
        stdin_payload = invocation.stdin_payload
        stdin_artifact_uri = None
        if stdin_payload is not None:
            stdin_hash = hashlib.sha256(stdin_payload).hexdigest()
            if (retain_payload
                    and tool.capture_stdin
                    and stdin_consent_token):
                if self.consent_token_validator(invocation.tool_descriptor_id,
                                                   stdin_consent_token):
                    # BC1: scan stdin BEFORE persisting so PII categories
                    # contribute to the aggregate pii_scan_hits + review
                    # status. Per Codex's coordinator decision (option B),
                    # we persist regardless and let the review flag route
                    # operator attention; we do NOT introduce a new event
                    # type for stdin-PII-detected.
                    stdin_pii_hits = list(self.pii_scan(stdin_payload))
                    pii_hits.extend(stdin_pii_hits)
                    stdin_artifact_uri = self.persist_artifact(
                        "stdin", stdin_payload
                    )

        # Run the subprocess (args=list, shell=False per ANTI-006).
        # Tests inject subprocess_runner. Real runner wraps subprocess.run
        # with shell=False enforced.
        t_start = time.perf_counter()
        runner = self.subprocess_runner or _default_subprocess_runner
        completed = runner(
            args=invocation.invocation_args,
            cwd=invocation.cwd,
            input=stdin_payload,
        )
        elapsed_ms = int((time.perf_counter() - t_start) * 1000)

        # PII / credential scan -- run on stdout + stderr; hits route to
        # operator review queue (sets operator_review_status=pending).
        # Stdin contributions, if any, were already appended to pii_hits
        # above (BC1).
        pii_hits.extend(self.pii_scan(completed.stdout or b""))
        pii_hits.extend(self.pii_scan(completed.stderr or b""))
        review_status = "pending" if pii_hits else "approved"

        # Persist stdout / stderr per sensitive_class retention policy.
        # For public / internal / restricted: persist raw payload bytes.
        # For secret: persist ONLY a deterministic hash marker; raw bytes
        #   never reach persist_artifact (Codex RCO round-2 fix).
        # Opaque: already refused upstream.
        stdout_bytes = completed.stdout or b""
        stderr_bytes = completed.stderr or b""
        if retain_payload:
            stdout_uri = self.persist_artifact("stdout", stdout_bytes)
            stderr_uri = self.persist_artifact("stderr", stderr_bytes)
        else:
            stdout_hash = hashlib.sha256(stdout_bytes).hexdigest()
            stderr_hash = hashlib.sha256(stderr_bytes).hexdigest()
            # Persist only the hash hex (well-known non-payload) -- the
            # raw payload never crosses this boundary.
            stdout_uri = self.persist_artifact(
                "stdout_hash", stdout_hash.encode("ascii")
            )
            stderr_uri = self.persist_artifact(
                "stderr_hash", stderr_hash.encode("ascii")
            )

        # Classification summary: redacted, one-line.
        # We do NOT include raw stdout content here; only the operator-
        # facing structural summary.
        summary = self._build_classification_summary(
            tool_descriptor_id=invocation.tool_descriptor_id,
            exit_code=completed.returncode,
            elapsed_ms=elapsed_ms,
            pii_hits=pii_hits,
        )

        record = CapturedBehaviorRecord(
            capture_id=str(uuid.uuid4()),
            tool_descriptor_id=invocation.tool_descriptor_id,
            invocation_args=list(invocation.invocation_args),
            invocation_env_keys=list(invocation.invocation_env_keys),
            invocation_ts_utc=_utc_iso(),
            input_state_refs=list(invocation.input_state_refs),
            output_state_refs=list(invocation.output_state_refs),
            stdout_artifact_uri=stdout_uri,
            stderr_artifact_uri=stderr_uri,
            exit_code=completed.returncode,
            elapsed_ms=elapsed_ms,
            md_injection_files=list(invocation.md_injection_files),
            classification_summary=summary,
            sensitive_class=tool.sensitive_class,
            operator_review_status=review_status,
            stdin_hash_sha256=stdin_hash,
            stdin_artifact_uri=stdin_artifact_uri,
            pipeline_id=invocation.pipeline_id,
            parent_capture_id=invocation.parent_capture_id,
            auth_mode=tool.auth_mode,
            pii_scan_hits=pii_hits,
        )

        # Emit behavior.captured MAGMA event
        self.emit_magma_event({
            "event_type": "behavior.captured",
            "capture_id": record.capture_id,
            "tool_descriptor_id": record.tool_descriptor_id,
            "exit_code": record.exit_code,
            "sensitive_class": record.sensitive_class,
            "operator_review_status": record.operator_review_status,
            "pii_scan_hits_count": len(pii_hits),
            "pipeline_id": record.pipeline_id,
            "parent_capture_id": record.parent_capture_id,
            "stdout_artifact_uri": record.stdout_artifact_uri,
            "stderr_artifact_uri": record.stderr_artifact_uri,
            "stdin_hash_sha256": record.stdin_hash_sha256,
            "ts_utc": record.invocation_ts_utc,
        })

        return record

    # =========================================================================
    # INTERNAL HELPERS
    # =========================================================================

    def _emit_refused(self, tool_descriptor_id: str, reason: str) -> None:
        """Emit a behavior.capture_refused MAGMA event before raising.

        Per BC2 finding (claude-iter-review-behavior-capture-2026-05-13):
        silent CaptureRefused exceptions deny operator-facing observability
        of recurring refusal patterns (e.g., tool descriptor went missing,
        scope policy regressed). Emitting an explicit MAGMA event keeps
        the refusal auditable without changing the caller-visible
        exception contract.
        """
        try:
            self.emit_magma_event({
                "event_type": "behavior.capture_refused",
                "tool_descriptor_id": tool_descriptor_id,
                "reason": reason,
                "ts_utc": _utc_iso(),
            })
        except Exception:
            # Defensive: MAGMA emit failure must not mask the original
            # refusal. The CaptureRefused exception will still propagate.
            pass

    def _build_classification_summary(self, *, tool_descriptor_id: str,
                                       exit_code: int, elapsed_ms: int,
                                       pii_hits: list[str]) -> str:
        # Operator-facing, single line, no payload content.
        pii_marker = (
            f" pii_hits={pii_hits}" if pii_hits else ""
        )
        summary = (
            f"tool={tool_descriptor_id} exit={exit_code} "
            f"elapsed_ms={elapsed_ms}{pii_marker}"
        )
        if len(summary) > self.classification_summary_max_chars:
            return summary[: self.classification_summary_max_chars - 3] + "..."
        return summary


# --------------------------------------------------------------------------
# Default subprocess runner -- shell=False enforced (ANTI-006 stance)
# --------------------------------------------------------------------------


def _default_subprocess_runner(*, args: list[str], cwd: str,
                                 input: Optional[bytes]) -> "subprocess.CompletedProcess":
    if not isinstance(args, list):
        raise ValueError("BehaviorCapture requires args=list; refusing string args")
    return subprocess.run(
        args,
        cwd=cwd,
        input=input,
        capture_output=True,
        check=False,
        shell=False,
    )


# --------------------------------------------------------------------------
# Utility
# --------------------------------------------------------------------------


def _utc_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


__all__ = [
    "SensitiveClass",
    "RETENTION_DAYS_BY_CLASS",
    "is_payload_retained",
    "retention_window_days",
    "ToolInvocation",
    "CapturedBehaviorRecord",
    "ToolDescriptorCaptureView",
    "CaptureRefused",
    "BehaviorCapture",
]
