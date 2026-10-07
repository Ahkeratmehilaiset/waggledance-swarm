# SPDX-License-Identifier: BUSL-1.1
"""Accepted-queue replay (Drain-AcceptedBridgeQueue.ps1 -> Restore-BridgeSpool.ps1) under a deep runtime root.

MSIX pwsh is not longPathAware: a kernel32 call with a plain name fails with Win32 error 3 once the path reaches
MAX_PATH (RCO2 FENCE-2). The drain and the targeted replay open and move spool files through their own P/Invoke types.
A queued accepted WAL is produced by the real writer under Windows PowerShell 5.1 (longPathAware), then drained.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[2]
SHELLS = ["powershell", "pwsh"]
WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="the accepted-queue drain is Windows-only")
DEEP_RUNTIME_CHARS = 205


def _shell(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        pytest.skip(f"{name} is not installed")
    return executable


def _fixture_bin(base: Path) -> Path:
    # Private mutex names in a fixture copy only, as in test_bridge_write_agent_event.py; never a runtime bypass.
    code = base / "fx" / ".agent-bridge" / "bin"
    shutil.copytree(ROOT / ".agent-bridge" / "bin", code)
    configs = base / "fx" / "configs"
    configs.mkdir()
    shutil.copy2(ROOT / "configs" / "bridge_identity_registry.json", configs)
    prefix = "Local\\WdReplayFixture-" + uuid.uuid4().hex + "-"
    for script in code.glob("*.ps1"):
        source = script.read_text(encoding="utf-8-sig")
        if "Global\\WaggleDanceBridge" in source:
            script.write_text(source.replace("Global\\WaggleDanceBridge", prefix), encoding="utf-8-sig")
    return code


def _env(runtime_root: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("AGENT_BRIDGE_", "WD_BRIDGE_")) or k.startswith("AGENT_BRIDGE_TEST_")}
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(runtime_root)
    env.update(extra)
    return env


def _run(shell: str, script: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script), *args],
        cwd=ROOT, env=env, check=False, capture_output=True, text=True,
    )


def _deep_runtime_root(tmp_path: Path) -> Path:
    pad = max(1, DEEP_RUNTIME_CHARS - len(str(tmp_path)) - len("\\rt") - 1)
    return tmp_path / ("d" * min(pad, 200)) / "rt"


def _queue_one_accepted_wal(code: Path, runtime_root: Path) -> list[Path]:
    queued = _run(_shell("powershell"), code / "Write-AgentEvent.ps1",
                  _env(runtime_root, AGENT_BRIDGE_TEST_MUTEX_CONSTRUCTION_FAILURE="Append"),
                  "-Agent", "smoke-1", "-Type", "message", "-TaskId", "replay-longpath", "-Status", "info",
                  "-Message", "queued under a deep runtime root", "-ReceiptJson")
    assert queued.returncode == 0, queued.stderr
    assert json.loads(queued.stdout)["_bridge_delivery"]["delivery_status"] == "queued"
    ready = sorted((runtime_root / "spool" / "accepted-v1" / "ready").glob("*.jsonl"))
    assert len(ready) == 1, sorted(p.relative_to(runtime_root).as_posix() for p in runtime_root.rglob("*") if p.is_file())
    assert not (runtime_root / "shared" / "events.jsonl").exists()
    return ready


@WINDOWS_ONLY
@pytest.mark.parametrize("shell", SHELLS)
def test_drain_replays_a_queued_wal_under_a_deep_runtime_root(tmp_path: Path, shell: str) -> None:
    executable = _shell(shell)
    runtime_root = _deep_runtime_root(tmp_path)
    code = _fixture_bin(tmp_path)
    ready = _queue_one_accepted_wal(code, runtime_root)
    # The replay's temporary and marker names run past MAX_PATH from this root.
    assert len(str(ready[0])) + len(".tmp.1.") + 32 > 260

    drained = _run(executable, code / "Drain-AcceptedBridgeQueue.ps1", _env(runtime_root),
                   "-BridgeRoot", str(runtime_root), "-PendingMinAgeSeconds", "0", "-ReceiptJson")

    assert drained.returncode == 0, drained.stdout + drained.stderr
    assert "Win32 error 3" not in drained.stdout + drained.stderr
    receipt = json.loads(drained.stdout)
    assert receipt["drained"] == 1, receipt
    assert receipt["failed"] == 0, receipt
    rows = [json.loads(line) for line in
            (runtime_root / "shared" / "events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [row["task_id"] for row in rows] == ["replay-longpath"]
    assert not ready[0].exists()


SCRIPTS = {"Restore-BridgeSpool.ps1": (6, 1), "Drain-AcceptedBridgeQueue.ps1": (2, 1)}


def _source(name: str) -> str:
    return (ROOT / ".agent-bridge" / "bin" / name).read_text(encoding="utf-8-sig")


def _conversion_function(name: str) -> str:
    source = _source(name)
    start = source.index("function ConvertTo-BridgeNativeLongPath")
    return source[start:source.index("\nfunction ", start + 1)]


def test_both_replay_scripts_carry_the_same_conversion() -> None:
    assert _conversion_function("Restore-BridgeSpool.ps1") == _conversion_function("Drain-AcceptedBridgeQueue.ps1")


@pytest.mark.parametrize("name", sorted(SCRIPTS))
def test_every_native_path_argument_goes_through_the_conversion(name: str) -> None:
    calls = re.findall(r"::(CreateFileW|MoveFileExW)\(\s*([^\r\n]*)\r?\n\s*([^\r\n]*)", _source(name))
    creates, moves = SCRIPTS[name]

    assert sorted(kind for kind, _, _ in calls) == ["CreateFileW"] * creates + ["MoveFileExW"] * moves
    for kind, first, second in calls:
        assert first.startswith("(ConvertTo-BridgeNativeLongPath "), (kind, first)
        if kind == "MoveFileExW":
            assert second.startswith("(ConvertTo-BridgeNativeLongPath "), (kind, second)


def _convert(executable: str, path: str) -> str:
    # The function text and the path both travel inside -EncodedCommand, so no command-line quoting is involved.
    encoded = base64.b64encode(path.encode("utf-8")).decode("ascii")
    command = (
        _conversion_function("Restore-BridgeSpool.ps1")
        + "\n$value = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + encoded + "'))"
        + "\n[Console]::Out.Write([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes((ConvertTo-BridgeNativeLongPath -Path $value))))"
    )
    completed = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-EncodedCommand",
         base64.b64encode(command.encode("utf-16-le")).decode("ascii")],
        check=False, capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return base64.b64decode(completed.stdout.strip()).decode("utf-8")


LONG = "C:\\" + "\\".join(["segment" + str(i).zfill(3) for i in range(40)]) + "\\events.jsonl"


@pytest.mark.parametrize("shell", SHELLS)
def test_only_long_canonical_drive_paths_gain_the_prefix(shell: str) -> None:
    executable = _shell(shell)
    assert len(LONG) > 260

    assert _convert(executable, LONG) == "\\\\?\\" + LONG
    assert _convert(executable, "C:\\short\\events.jsonl") == "C:\\short\\events.jsonl"


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("path", [
    LONG.replace("\\segment005\\", "\\segment005\\..\\"),
    LONG.replace("\\segment005\\", "\\.\\"),
    LONG.replace("\\segment005\\", "/segment005/"),
    LONG.replace("\\segment005\\", "\\segment005.\\"),
    LONG.replace("\\segment005\\", "\\segment005 \\"),
    LONG.replace("\\segment005\\", "\\\\"),
    LONG.replace("\\segment005\\", "\\NUL\\"),
    LONG.replace("\\segment005\\", "\\com1.txt\\"),
    LONG.replace("\\events.jsonl", "\\foo.\x00x"),
    LONG.replace("\\events.jsonl", "\\foo \x00x"),
    LONG[3:],
    "\\\\server\\share" + LONG[2:],
    "\\\\?\\" + LONG,
    LONG + "\\",
])
def test_non_canonical_or_already_qualified_paths_pass_unchanged(shell: str, path: str) -> None:
    executable = _shell(shell)

    assert _convert(executable, path) == path
