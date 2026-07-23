from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from waggledance.core import bridge_log_reader

ROOT = Path(__file__).resolve().parents[2]
READ_AGENT_BRIDGE = ROOT / ".agent-bridge" / "bin" / "Read-AgentBridge.ps1"
BRIDGE_LOG_READER = ROOT / ".agent-bridge" / "bin" / "BridgeLogReader.ps1"
BRIDGE_INCREMENTAL_SMOKE = (
    ROOT
    / ".agent-bridge"
    / "bin"
    / "Test-BridgeIncrementalReaderSmoke.ps1"
)


def _powershell() -> str:
    executable = (
        shutil.which("pwsh")
        or shutil.which("powershell")
        or shutil.which("powershell.exe")
    )
    assert executable is not None
    return executable


def _append_event(events_path: Path, event: dict[str, object]) -> None:
    events_path.parent.mkdir(parents=True, exist_ok=True)
    with events_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, separators=(",", ":")) + "\n")


def _event(
    *,
    ts_utc: str,
    agent: str,
    event_type: str,
    task_id: str,
    status: str,
    to: str = "",
    message: str = "",
) -> dict[str, object]:
    return {
        "ts_utc": ts_utc,
        "agent": agent,
        "type": event_type,
        "task_id": task_id,
        "status": status,
        "severity": "",
        "to": to,
        "message": message,
        "paths": [],
        "write_scope": [],
        "run_id": "",
        "pid": 0,
        "cwd": "",
        "payload": {},
    }


def _run_reader(
    bridge_root: Path,
    *extra_args: str,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(bridge_root)
    return subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(READ_AGENT_BRIDGE),
            "-Agent",
            "codex",
            "-NoAckReceived",
            "-Tail",
            "3",
            *extra_args,
        ],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=True,
    )


def _powershell_file_identity(path: Path) -> str:
    env = os.environ.copy()
    env["BRIDGE_IDENTITY_TEST_PATH"] = str(path)
    env["BRIDGE_LOG_READER_SCRIPT"] = str(BRIDGE_LOG_READER)
    command = (
        "$ErrorActionPreference = 'Stop'; "
        ". $env:BRIDGE_LOG_READER_SCRIPT; "
        "$stream = [System.IO.File]::Open("
        "$env:BRIDGE_IDENTITY_TEST_PATH, "
        "[System.IO.FileMode]::Open, "
        "[System.IO.FileAccess]::Read, "
        "([System.IO.FileShare]::ReadWrite -bor "
        "[System.IO.FileShare]::Delete)); "
        "try { Get-BridgeLogFileIdentity -Stream $stream } "
        "finally { $stream.Dispose() }"
    )
    result = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=True,
    )
    return result.stdout.strip()


def _read_with_legacy_identity_type_preloaded(
    path: Path,
    tmp_path: Path,
) -> dict[str, object]:
    legacy_type_source = tmp_path / "legacy_bridge_file_identity.cs"
    legacy_type_source.write_text(
        """
using Microsoft.Win32.SafeHandles;

namespace WaggleDance.BridgeSnapshotDeltaV1
{
    public struct ByHandleFileInformation
    {
        public uint Marker;
    }

    public static class NativeMethods
    {
        public static ByHandleFileInformation Identity(SafeFileHandle file)
        {
            return new ByHandleFileInformation { Marker = 1 };
        }
    }
}
""".strip(),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["BRIDGE_IDENTITY_TEST_PATH"] = str(path)
    env["BRIDGE_LEGACY_TYPE_SOURCE"] = str(legacy_type_source)
    env["BRIDGE_LOG_READER_SCRIPT"] = str(BRIDGE_LOG_READER)
    command = (
        "$ErrorActionPreference = 'Stop'; "
        "Add-Type -Path $env:BRIDGE_LEGACY_TYPE_SOURCE; "
        ". $env:BRIDGE_LOG_READER_SCRIPT; "
        "$result = Read-BridgeLogSnapshotDelta "
        "-Path $env:BRIDGE_IDENTITY_TEST_PATH; "
        "[pscustomobject]@{ "
        "legacy_return_type = "
        "[WaggleDance.BridgeSnapshotDeltaV1.NativeMethods]"
        ".GetMethod('Identity').ReturnType.FullName; "
        "status = [string]$result.status; "
        "reason = [string]$result.reason; "
        "row_count = @($result.rows).Count; "
        "file_identity = [string]$result.candidate_cursor.file_identity "
        "} | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=True,
    )
    return json.loads(result.stdout)


def _disposed_stream_identity_failure(path: Path) -> dict[str, object]:
    env = os.environ.copy()
    env["BRIDGE_IDENTITY_TEST_PATH"] = str(path)
    env["BRIDGE_LOG_READER_SCRIPT"] = str(BRIDGE_LOG_READER)
    command = (
        "$ErrorActionPreference = 'Stop'; "
        ". $env:BRIDGE_LOG_READER_SCRIPT; "
        "$stream = [System.IO.File]::OpenRead("
        "$env:BRIDGE_IDENTITY_TEST_PATH); "
        "$stream.Dispose(); "
        "$result = try { "
        "Get-BridgeLogFileIdentity -Stream $stream | Out-Null; "
        "[pscustomobject]@{ threw = $false; message = '' } "
        "} catch { "
        "[pscustomobject]@{ "
        "threw = $true; message = [string]$_.Exception.Message "
        "} "
        "}; "
        "$result | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=True,
    )
    return json.loads(result.stdout)


def _run_incremental_smoke(cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(BRIDGE_INCREMENTAL_SMOKE),
        ],
        cwd=cwd,
        env=os.environ.copy(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        check=True,
    )


def test_bridge_file_identity_matches_python_contract(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_bytes(b"{}\n")

    with events_path.open("rb") as stream:
        expected = bridge_log_reader._file_identity(stream)

    assert _powershell_file_identity(events_path) == expected


def test_bridge_reader_ignores_preloaded_legacy_identity_type(
    tmp_path: Path,
) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_bytes(b'{"row":1}\n')

    with events_path.open("rb") as stream:
        expected_identity = bridge_log_reader._file_identity(stream)

    result = _read_with_legacy_identity_type_preloaded(events_path, tmp_path)

    assert result == {
        "legacy_return_type": (
            "WaggleDance.BridgeSnapshotDeltaV1.ByHandleFileInformation"
        ),
        "status": "OK",
        "reason": "rows_available",
        "row_count": 1,
        "file_identity": expected_identity,
    }


def test_bridge_file_identity_fails_closed_for_disposed_handle(
    tmp_path: Path,
) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_bytes(b"{}\n")

    result = _disposed_stream_identity_failure(events_path)

    assert result["threw"] is True
    assert "bridge file identity unavailable" in str(result["message"])


def test_bridge_incremental_smoke_covers_path_hardening(
    tmp_path: Path,
) -> None:
    result = _run_incremental_smoke(tmp_path)

    assert "[FAIL]" not in result.stdout
    assert (
        "[PASS] relative event and state paths bind PowerShell location"
        in result.stdout
    )
    assert (
        "[PASS] invalid path APIs fail closed before unintended I/O"
        in result.stdout
    )
    assert (
        "[PASS] native path policy rejects aliases without overreach"
        in result.stdout
    )


def test_read_agent_bridge_tail_does_not_hide_open_incoming(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / ".agent-bridge"
    events_path = bridge_root / "shared" / "events.jsonl"
    _append_event(
        events_path,
        _event(
            ts_utc="2026-05-11T16:00:00.0000000Z",
            agent="claude",
            event_type="wake_request",
            task_id="old-tail-noise-2026-05-11",
            status="open",
            to="codex",
            message="old request outside the tail window",
        ),
    )
    for index in range(3):
        _append_event(
            events_path,
            _event(
                ts_utc=f"2026-05-11T17:0{index}:00.0000000Z",
                agent="codex",
                event_type="heartbeat",
                task_id=f"recent-tail-{index}",
                status="active",
                message=f"recent filler {index}",
            ),
        )

    result = _run_reader(bridge_root)

    assert "OPEN old-tail-noise-2026-05-11" in result.stdout


def test_read_agent_bridge_tail_hides_resolved_items_outside_tail(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / ".agent-bridge"
    events_path = bridge_root / "shared" / "events.jsonl"
    _append_event(
        events_path,
        _event(
            ts_utc="2026-05-11T16:00:00.0000000Z",
            agent="claude",
            event_type="wake_request",
            task_id="old-resolved-tail-noise-2026-05-11",
            status="open",
            to="codex",
            message="old request outside the tail window",
        ),
    )
    _append_event(
        events_path,
        _event(
            ts_utc="2026-05-11T16:01:00.0000000Z",
            agent="claude",
            event_type="done",
            task_id="old-resolved-tail-noise-2026-05-11",
            status="done",
            message="closed outside the tail window",
        ),
    )
    for index in range(3):
        _append_event(
            events_path,
            _event(
                ts_utc=f"2026-05-11T17:0{index}:00.0000000Z",
                agent="codex",
                event_type="heartbeat",
                task_id=f"recent-tail-{index}",
                status="active",
                message=f"recent filler {index}",
            ),
        )

    result = _run_reader(bridge_root)

    assert "OPEN old-resolved-tail-noise-2026-05-11" not in result.stdout
    assert "old-resolved-tail-noise-2026-05-11" not in result.stdout
    assert "answered/closed item(s) outside -Tail hidden" in result.stdout


def test_read_agent_bridge_continuity_tail_can_be_bounded_or_full(
    tmp_path: Path,
) -> None:
    bridge_root = tmp_path / ".agent-bridge"
    events_path = bridge_root / "shared" / "events.jsonl"
    _append_event(
        events_path,
        _event(
            ts_utc="2026-05-11T16:00:00.0000000Z",
            agent="claude",
            event_type="wake_request",
            task_id="old-continuity-tail-request",
            status="open",
            to="codex",
            message="old request outside continuity tail",
        ),
    )
    for index in range(5):
        _append_event(
            events_path,
            _event(
                ts_utc=f"2026-05-11T17:0{index}:00.0000000Z",
                agent="codex",
                event_type="heartbeat",
                task_id=f"recent-continuity-filler-{index}",
                status="active",
                message=f"recent filler {index}",
            ),
        )

    bounded = _run_reader(bridge_root, "-ContinuityTail", "3")
    full = _run_reader(bridge_root, "-ContinuityTail", "0")

    assert "OPEN old-continuity-tail-request" not in bounded.stdout
    assert "OPEN old-continuity-tail-request" in full.stdout
