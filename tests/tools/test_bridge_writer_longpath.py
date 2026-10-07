# SPDX-License-Identifier: BUSL-1.1
"""Write-AgentEvent.ps1 native long paths (RCO2 FENCE-2, diagnosis 700676E7).

MSIX pwsh is not longPathAware, so the writer's kernel32 CreateFileW/MoveFileExW calls failed with Win32 error 3 once a
runtime path reached MAX_PATH. The writer now gives those calls a ``\\\\?\\`` name, but only for a long, already canonical
drive path, so the prefixed name denotes exactly the file the unprefixed call would have opened.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[2]
WRITER = ROOT / ".agent-bridge" / "bin" / "Write-AgentEvent.ps1"
SHELLS = ["powershell", "pwsh"]
WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="AppendV1 native calls are Windows-only")
DEEP_RUNTIME_CHARS = 205


def _shell(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        pytest.skip(f"{name} is not installed")
    return executable


def _writer_source() -> str:
    return WRITER.read_text(encoding="utf-8-sig")


def _conversion_function() -> str:
    source = _writer_source()
    start = source.index("function ConvertTo-BridgeNativeLongPath")
    end = source.index("\nfunction ", start + 1)
    return source[start:end]


def _fixture_writer(runtime_root: Path) -> Path:
    # Same isolation as test_bridge_write_agent_event.py: a fixture copy with private mutex names.
    suffix = hashlib.sha256(str(runtime_root).encode()).hexdigest()[:16]
    fixture_root = runtime_root.parent / ("writer-fixture-" + suffix)
    code = fixture_root / ".agent-bridge" / "bin"
    shutil.copytree(ROOT / ".agent-bridge" / "bin", code)
    configs = fixture_root / "configs"
    configs.mkdir()
    shutil.copy2(ROOT / "configs" / "bridge_identity_registry.json", configs)
    prefix = "Local\\WdWriterFixture-" + uuid.uuid4().hex + "-"
    for script in code.glob("*.ps1"):
        source = script.read_text(encoding="utf-8-sig")
        if "Global\\WaggleDanceBridge" in source:
            script.write_text(source.replace("Global\\WaggleDanceBridge", prefix), encoding="utf-8-sig")
    return code / "Write-AgentEvent.ps1"


def _run(shell: str, script: Path, runtime_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("AGENT_BRIDGE_", "WD_BRIDGE_")) or k.startswith("AGENT_BRIDGE_TEST_")}
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(runtime_root)
    return subprocess.run(
        [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), *args],
        cwd=ROOT, env=env, check=False, capture_output=True, text=True,
    )


def _deep_runtime_root(tmp_path: Path) -> Path:
    pad = max(1, DEEP_RUNTIME_CHARS - len(str(tmp_path)) - len("\\rt") - 1)
    runtime_root = tmp_path / ("d" * min(pad, 200)) / "rt"
    assert len(str(runtime_root)) >= DEEP_RUNTIME_CHARS - 5
    return runtime_root


@WINDOWS_ONLY
@pytest.mark.parametrize("shell", SHELLS)
def test_writer_appends_canonically_under_a_deep_runtime_root(tmp_path: Path, shell: str) -> None:
    executable = _shell(shell)
    runtime_root = _deep_runtime_root(tmp_path)
    script = _fixture_writer(runtime_root)

    completed = _run(executable, script, runtime_root, "-Agent", "smoke-1", "-Type", "message", "-TaskId",
                     "longpath-smoke", "-Status", "info", "-Message", "deep runtime root", "-ReceiptJson")

    assert completed.returncode == 0, completed.stderr
    assert "Win32 error 3" not in completed.stderr
    receipt = json.loads(completed.stdout)["_bridge_delivery"]
    assert receipt["accepted"] is True
    assert receipt["delivery_status"] == "canonical"
    assert receipt["canonical_durable"] is True
    events = runtime_root / "shared" / "events.jsonl"
    rows = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [row["task_id"] for row in rows] == ["longpath-smoke"]
    # The writer's temporary names are "<path>.tmp.<pid>.<32 hex guid>", so this root really crosses MAX_PATH.
    assert len(str(events) + ".append-v1-validation.json") + len(".tmp.1.") + 32 > 260


def _convert(executable: str, path: str) -> str:
    # The function text and the path both travel inside -EncodedCommand, so no command-line quoting is involved.
    encoded = base64.b64encode(path.encode("utf-8")).decode("ascii")
    command = (
        _conversion_function()
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
    LONG[3:],
    "\\\\server\\share" + LONG[2:],
    "\\\\?\\" + LONG,
    LONG + "\\",
])
def test_non_canonical_or_already_qualified_paths_pass_unchanged(shell: str, path: str) -> None:
    executable = _shell(shell)

    assert _convert(executable, path) == path


def test_every_native_path_argument_goes_through_the_conversion() -> None:
    source = _writer_source()
    calls = re.findall(r"\[WaggleDance\.BridgeAppendV1Native\]::(CreateFileW|MoveFileExW)\(\s*([^\r\n]*)\r?\n\s*([^\r\n]*)",
                       source)

    assert sorted(name for name, _, _ in calls) == ["CreateFileW"] * 3 + ["MoveFileExW"] * 4
    for name, first, second in calls:
        assert first.startswith("(ConvertTo-BridgeNativeLongPath "), (name, first)
        if name == "MoveFileExW":
            assert second.startswith("(ConvertTo-BridgeNativeLongPath "), (name, second)
