# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Claude Code builder lane — the only authorised subprocess in P3.

Per Phase 10 RULE 17, this lane is the **only** module in this prompt
that may invoke a subprocess. Constraints:

* Isolated worktree only (caller passes ``isolated_worktree_path``).
* Bounded timeout (default 600s; hard ceiling 7200s).
* Logged to a JSONL invocation log via the existing
  ``waggledance/core/builder_lane/worktree_allocator.append_invocation``.
* Counts against the provider budget (RULE 8).
* No direct merge from the subprocess lane.
* If the Claude Code CLI is unavailable the lane degrades to a
  ``dry_run`` mode that returns a synthetic response and writes the
  invocation log entry with ``outcome="dry_run"`` (so the caller can
  see exactly which invocations would have happened).

The lane never reads the autonomy runtime hot path. It is the
"teach-WD" lane; runtime cognition continues to work without it.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence

from waggledance.core.builder_lane.worktree_allocator import (
    InvocationLogEntry,
    append_invocation,
)
from .provider_contracts import ProviderRequest, ProviderResponse, utcnow_iso


_AUTO_DETECT_CLI = object()


class ClaudeCodeBuilderUnavailable(RuntimeError):
    """Raised when Claude Code CLI is required but absent and the caller
    explicitly disabled dry-run fallback."""


@dataclass(frozen=True)
class _LaunchSpec:
    request_id: str
    isolated_worktree_path: Path
    isolated_branch_name: str
    intent: str
    max_wall_seconds: int
    invocation_log_path: Path


class ClaudeCodeBuilder:
    """Claude Code subprocess wrapper with dry-run fallback."""

    DEFAULT_PROVIDER_ID: str = "claude_code_builder_lane_default"
    PROVIDER_TYPE: str = "claude_code_builder_lane"
    HARD_TIMEOUT_CEILING_SECONDS: int = 7200

    def __init__(
        self,
        *,
        cli_path: object = _AUTO_DETECT_CLI,
        invocation_log_path: Optional[Path | str] = None,
        allow_dry_run: bool = True,
    ) -> None:
        # cli_path semantics:
        #   _AUTO_DETECT_CLI (default) → run shutil.which() detection
        #   None  → explicitly mark CLI unavailable (testing / dry-run)
        #   str   → use this path verbatim
        if cli_path is _AUTO_DETECT_CLI:
            self._cli_path: Optional[str] = self._detect_cli()
        else:
            self._cli_path = cli_path  # type: ignore[assignment]
        self._invocation_log_path: Path = (
            Path(invocation_log_path)
            if invocation_log_path is not None
            else Path("data") / "builder_invocation_log.jsonl"
        )
        self._allow_dry_run: bool = allow_dry_run

    @property
    def cli_available(self) -> bool:
        return self._cli_path is not None

    @property
    def invocation_log_path(self) -> Path:
        return self._invocation_log_path

    # -- public API ----------------------------------------------------

    def invoke(
        self,
        request: ProviderRequest,
        *,
        isolated_worktree_path: Path | str,
        isolated_branch_name: str,
        max_wall_seconds: int = 600,
    ) -> ProviderResponse:
        if max_wall_seconds <= 0 or max_wall_seconds > self.HARD_TIMEOUT_CEILING_SECONDS:
            raise ValueError(
                f"max_wall_seconds {max_wall_seconds} outside (0, "
                f"{self.HARD_TIMEOUT_CEILING_SECONDS}]"
            )

        spec = _LaunchSpec(
            request_id=request.request_id,
            isolated_worktree_path=Path(isolated_worktree_path),
            isolated_branch_name=isolated_branch_name,
            intent=request.intent,
            max_wall_seconds=max_wall_seconds,
            invocation_log_path=self._invocation_log_path,
        )

        if not self.cli_available:
            return self._dry_run(request, spec, reason="cli_unavailable")

        return self._real_invoke(request, spec)

    # -- internal -------------------------------------------------------

    def _detect_cli(self) -> Optional[str]:
        candidate = shutil.which("claude") or shutil.which("claude-code")
        return candidate

    def _dry_run(
        self,
        request: ProviderRequest,
        spec: _LaunchSpec,
        *,
        reason: str,
    ) -> ProviderResponse:
        if not self._allow_dry_run:
            raise ClaudeCodeBuilderUnavailable(
                f"Claude Code CLI unavailable and dry-run fallback disabled: {reason}"
            )
        ts = utcnow_iso()
        self._write_log(spec, outcome="dry_run", rationale=reason, ts_iso=ts)
        return ProviderResponse(
            schema_version=1,
            response_id=f"dry-run-{spec.request_id}",
            request_id=spec.request_id,
            provider_used=self.DEFAULT_PROVIDER_ID,
            raw_payload={"dry_run": True, "reason": reason, "intent": spec.intent},
            ts_iso=ts,
            latency_ms=0.0,
            trust_layer_state="raw_quarantine",
            no_direct_mutation=True,
        )

    def _real_invoke(
        self,
        request: ProviderRequest,
        spec: _LaunchSpec,
    ) -> ProviderResponse:
        assert self._cli_path is not None
        if not spec.isolated_worktree_path.exists():
            return self._dry_run(request, spec, reason="worktree_missing")

        cmd: Sequence[str] = (
            self._cli_path,
            "--print",
            "--output-format", "json",
            f"INTENT: {spec.intent}",
        )
        started = time.perf_counter()
        ts_started = utcnow_iso()
        try:
            proc = subprocess.run(  # noqa: S603 — explicit list, no shell
                cmd,
                cwd=str(spec.isolated_worktree_path),
                capture_output=True,
                text=True,
                timeout=spec.max_wall_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            ts = utcnow_iso()
            self._write_log(spec, outcome="timed_out", rationale="subprocess timeout", ts_iso=ts)
            return ProviderResponse(
                schema_version=1,
                response_id=f"timeout-{spec.request_id}",
                request_id=spec.request_id,
                provider_used=self.DEFAULT_PROVIDER_ID,
                raw_payload={"timed_out": True, "intent": spec.intent},
                ts_iso=ts,
                latency_ms=elapsed_ms,
                trust_layer_state="raw_quarantine",
                no_direct_mutation=True,
            )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        ts_completed = utcnow_iso()
        outcome = "completed" if proc.returncode == 0 else "failed"
        rationale = (
            "subprocess returncode=0"
            if proc.returncode == 0
            else f"subprocess returncode={proc.returncode}"
        )
        self._write_log(spec, outcome=outcome, rationale=rationale, ts_iso=ts_completed)
        return ProviderResponse(
            schema_version=1,
            response_id=f"resp-{spec.request_id}",
            request_id=spec.request_id,
            provider_used=self.DEFAULT_PROVIDER_ID,
            raw_payload={
                "stdout": proc.stdout[-4096:] if proc.stdout else "",
                "stderr": proc.stderr[-4096:] if proc.stderr else "",
                "returncode": proc.returncode,
                "ts_started_utc": ts_started,
            },
            ts_iso=ts_completed,
            latency_ms=elapsed_ms,
            trust_layer_state="raw_quarantine",
            no_direct_mutation=True,
        )

    def _write_log(
        self,
        spec: _LaunchSpec,
        *,
        outcome: str,
        rationale: str,
        ts_iso: str,
    ) -> Path:
        entry = InvocationLogEntry(
            request_id=spec.request_id,
            ts_iso=ts_iso,
            isolated_worktree_path=str(spec.isolated_worktree_path),
            isolated_branch_name=spec.isolated_branch_name,
            outcome=outcome,
            rationale=rationale,
        )
        return append_invocation(spec.invocation_log_path, entry)
