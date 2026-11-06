#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Default-off, fixed-content public status via the inherited bridge writer.

This declares only that a lane issued an informational event. It does not
assert that Mind is running, measured, installed, or in use. The bridge remains
the authority; metadata projection is a separate consumer. This code does not
start a publisher, load a model, or read a bridge log.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from sandbox.mind.step2 import shared_capture_adapter as adapter

ROOT = Path(__file__).resolve().parents[1]
TASK = "lead-mind-production-20261031"
_NON_REVIEW_SHA = frozenset({"0a9b1f67bc7015924ef595a17f7b092274c0fd568691f9d1ef07bf6a7b9decd2",
                             "95e21913155ebc941ab031761343381b407954dd97f65b9482237961f7a312c7"})
_PUBLIC_SHA = frozenset({"a5c5f0d91fc27c799678d1d97a28f48c540997ac91385ae097921388e2a4cdb5",
                         "29382d02a3adcf8dc5c3f3a078f9a1dc5565fdcf1a0f14f9c8c49fc1bb793268"})
_ROSTER_SHA = frozenset({"b60bbfd5cfa28bb93053c9b7492378d19c0abb184bd2569398109c50ef24adcb",
                         "bded859e8ae0ffc42c5a56007883d30e0eb4fafd028da7ff8261ae3bbbf9aaf7"})
_ALLOWED_LANES = frozenset({"codex-lead-1", "codex-tools-1", "fable-5"})
_FRAME = re.compile(r"wd-reboot-[A-Za-z0-9._:-]{1,110}\Z")
_HEX = re.compile(r"[a-fA-F0-9]{64}\Z")


class PublicStatusRefused(RuntimeError):
    """A fixed error: never include bridge paths, raw input, or secrets."""


class PublicStatusWriteUncertain(RuntimeError):
    """The writer was invoked; a failed receipt cannot prove no append occurred."""


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pinned_helpers() -> tuple[Path, Path]:
    """Verify inherited manifest anchor and both helper bytes before execution."""
    raw_bin = os.environ.get("WD_BRIDGE_BIN", "")
    anchor = os.environ.get("WD_REBOOT_EXPECTED_MANIFEST_HASH", "")
    if not raw_bin or not _HEX.fullmatch(anchor):
        raise ValueError()
    bin_dir = Path(raw_bin).resolve(strict=True)
    bundle = bin_dir.parent.parent.parent
    if bin_dir != (bundle / "tools-bootstrap" / ".agent-bridge" / "bin").resolve(strict=True):
        raise ValueError()
    raw = (bundle / "deployment-manifest.json").read_bytes()
    if len(raw) > 16 * 1024 * 1024 or _digest(raw).lower() != anchor.lower():
        raise ValueError()
    files = json.loads(raw)["files"]
    result = []
    for name in ("Get-BridgeExecutionEvidence.ps1", "Write-AgentEvent.ps1"):
        path = bin_dir / name
        expected = files["tools-bootstrap/.agent-bridge/bin/" + name]
        if not isinstance(expected, str) or not _HEX.fullmatch(expected) or _digest(path.read_bytes()).lower() != expected.lower():
            raise ValueError()
        result.append(path)
    return result[0], result[1]


def _powershell() -> str:
    root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    return str(root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")


def _attest() -> dict:
    evidence, _ = _pinned_helpers()
    result = subprocess.run([_powershell(), "-NoProfile", "-NonInteractive", "-File", str(evidence)],
                            capture_output=True, text=True, timeout=30, check=True)
    return json.loads(result.stdout)


def _write(event: dict) -> dict:
    _, writer = _pinned_helpers()
    # Remove uncontrolled free-text metadata; the writer still derives the
    # actor UUID, session and run from the inherited launch frame.
    env = dict(os.environ)
    env.pop("AGENT_BRIDGE_ROLE", None)
    env.pop("AGENT_BRIDGE_CAPABILITIES", None)
    command = [_powershell(), "-NoProfile", "-NonInteractive", "-File", str(writer),
               "-Agent", event["agent"], "-Type", "status", "-TaskId", TASK,
               "-Status", "info", "-PayloadJson", '{"public_status":true}', "-ReceiptJson"]
    completed = subprocess.run(command, cwd=str(ROOT), env=env, capture_output=True,
                               text=True, timeout=60, check=True)
    written = json.loads(completed.stdout)
    delivery = written.pop("_bridge_delivery")
    for key, value in event.items():
        if written.get(key) != value:
            raise ValueError()
    if written.get("paths") != [] or written.get("write_scope") != []:
        raise ValueError()
    if not adapter._public_status_event(written, frozenset({TASK}), frozenset({"status"})):
        raise ValueError()
    if (delivery.get("schema") != "waggledance.bridge.delivery-receipt.v1"
            or delivery.get("accepted") is not True
            or delivery.get("delivery_status") not in {"canonical", "queued"}
            or type(delivery.get("canonical_durable")) is not bool
            or (delivery.get("delivery_status") == "canonical") != (delivery.get("canonical_durable") is True)):
        raise ValueError()
    result = {"delivery_status": delivery["delivery_status"], "canonical_durable": delivery["canonical_durable"]}
    if delivery["delivery_status"] == "queued":
        wal_path, wal_sha = delivery.get("retained_wal_path"), delivery.get("retained_wal_sha256")
        if not isinstance(wal_path, str) or not wal_path or not isinstance(wal_sha, str) or not re.fullmatch(r"[a-f0-9]{64}", wal_sha):
            raise ValueError()
        result["retained_wal_sha256"] = wal_sha
    return result


def _validated_event() -> dict:
    policy = (ROOT / "configs/mind/pilot_non_review_policy.json").read_bytes()
    public = (ROOT / "configs/mind/pilot_public_status_policy.json").read_bytes()
    roster = (ROOT / "ops/windows/reboot/wd-fleet.json").read_bytes()
    policy_sha, public_sha, roster_sha = _digest(policy), _digest(public), _digest(roster)
    # The two digests per file pin the exact Git LF blob and its known CRLF
    # checkout. No byte normalization or caller-supplied trust anchor occurs.
    if policy_sha not in _NON_REVIEW_SHA or public_sha not in _PUBLIC_SHA or roster_sha not in _ROSTER_SHA:
        raise ValueError()
    tasks, lanes = adapter.strict_pins(policy, policy_sha, roster, roster_sha)
    public_tasks, public_types = adapter.public_status_policy(public, public_sha, tasks)
    if public_tasks != frozenset({TASK}) or public_types != frozenset({"status"}):
        raise ValueError()
    agent = os.environ.get("AGENT_BRIDGE_AGENT", "")
    if agent not in _ALLOWED_LANES or os.environ.get("AGENT_BRIDGE_AGENT_UUID") != lanes[agent]:
        raise ValueError()
    for key in ("AGENT_BRIDGE_SESSION_ID", "AGENT_BRIDGE_RUN_ID"):
        if not _FRAME.fullmatch(os.environ.get(key, "")):
            raise ValueError()
    evidence = _attest()
    if (evidence.get("pin_status") != "manifest_and_launcher_verified"
            or evidence.get("observed_agent") != agent
            or not evidence.get("native_conversation_id")):
        raise ValueError()
    return {"agent": agent, "agent_uuid": lanes[agent], "type": "status", "task_id": TASK, "status": "info",
            "to": "", "message": "", "payload": {"public_status": True}}


def publish(*, enabled: bool = False) -> dict:
    """Explicit opt-in; emit only the fixed policy-approved empty broadcast."""
    if enabled is False:
        return {"status": "disabled"}
    event = None
    try:
        if enabled is not True:
            raise ValueError()
        event = _validated_event()
    except Exception:
        pass
    # Raise outside the handler: __context__ cannot retain raw policy bytes.
    if event is None:
        raise PublicStatusRefused("public_status_refused")
    result = None
    try:
        result = _write(event)
    except Exception:
        pass
    # Never auto-retry: the writer may have appended before the receipt was lost.
    # Raise outside the handler so an invalid receipt cannot leak via __context__.
    if result is None:
        raise PublicStatusWriteUncertain("public_status_write_uncertain")
    return result
