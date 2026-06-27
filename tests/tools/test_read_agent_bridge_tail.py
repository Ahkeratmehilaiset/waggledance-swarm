from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
READ_AGENT_BRIDGE = ROOT / ".agent-bridge" / "bin" / "Read-AgentBridge.ps1"


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
