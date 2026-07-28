# SPDX-License-Identifier: BUSL-1.1
"""Fail-closed session-agent identity checks for bridge command-line tools."""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Mapping, TextIO


SESSION_AGENT_ENV = "AGENT_BRIDGE_AGENT"
SESSION_AGENT_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,32}$")
RESERVED_SESSION_AGENTS = frozenset({"operator", "system"})


def cli_identity_mismatch(
    requested_agent: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object] | None:
    """Return a deterministic mismatch report for an identity-bearing CLI.

    The ambient identity is intentionally checked only by CLI entry points.
    Library callers remain explicit about the ``agent`` argument and do not
    inherit process-environment policy.
    """

    source = os.environ if environ is None else environ
    session_agent = str(source.get(SESSION_AGENT_ENV, ""))
    if not SESSION_AGENT_PATTERN.fullmatch(requested_agent):
        error = f"--agent {requested_agent!r} is malformed"
    elif not session_agent.strip():
        if requested_agent not in RESERVED_SESSION_AGENTS:
            return None
        error = (
            f"reserved --agent {requested_agent!r} requires a verified "
            "bound or internal caller"
        )
    elif not SESSION_AGENT_PATTERN.fullmatch(session_agent):
        error = f"{SESSION_AGENT_ENV} {session_agent!r} is malformed"
    elif requested_agent == "system":
        error = "reserved --agent 'system' has no public Python CLI authority"
    elif requested_agent == session_agent:
        return None
    else:
        error = (
            f"--agent {requested_agent!r} does not match "
            f"{SESSION_AGENT_ENV} {session_agent!r}"
        )
    return {
        "ok": False,
        "decision": "identity_mismatch",
        "safe_mode": "read-only",
        "requested_agent": requested_agent,
        "session_agent": session_agent,
        "errors": [error],
    }


def emit_identity_mismatch(
    report: Mapping[str, object],
    *,
    as_json: bool,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> None:
    """Emit the shared deterministic JSON or human mismatch contract."""

    output = sys.stdout if stdout is None else stdout
    error_output = sys.stderr if stderr is None else stderr
    if as_json:
        print(json.dumps(report, sort_keys=True), file=output)
        return

    requested_agent = str(report["requested_agent"])
    session_agent = str(report["session_agent"])
    requested_display = (
        requested_agent
        if SESSION_AGENT_PATTERN.fullmatch(requested_agent)
        else repr(requested_agent)
    )
    session_display = (
        session_agent
        if not session_agent or SESSION_AGENT_PATTERN.fullmatch(session_agent)
        else repr(session_agent)
    )

    print(report["decision"], file=output)
    print(f"safe_mode: {report['safe_mode']}", file=output)
    print(f"requested_agent: {requested_display}", file=output)
    print(f"session_agent: {session_display}", file=output)
    for error in report.get("errors", []):
        print(f"- {error}", file=error_output)
