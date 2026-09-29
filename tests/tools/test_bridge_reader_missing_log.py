"""A failed canonical read must not leave cache artifacts in a code package."""

from pathlib import Path
import json
import os
import shutil
import subprocess

import pytest


BIN = Path(__file__).resolve().parents[2] / ".agent-bridge/bin"
SHELLS = list(dict.fromkeys(filter(None, (shutil.which("pwsh"), shutil.which("powershell.exe")))))


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize("explicit_root", (False, True))
@pytest.mark.parametrize("script,args", [
    ("Get-BridgeRequestInventory.ps1", ["-Agent", "codex-lead-1"]),
    ("Get-BridgeReplySnapshot.ps1", ["-RequestId", "missing-fixture-id"]),
])
def test_missing_runtime_log_has_no_cache_side_effects(tmp_path, shell, script, args, explicit_root):
    package = tmp_path / "bundle/tools-bootstrap/.agent-bridge"
    shutil.copytree(BIN, package / "bin")
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("AGENT_BRIDGE_", "WD_", "CLAUDE_CODE_", "GIT_"))}
    before = {p.relative_to(package) for p in package.rglob("*")}
    if explicit_root:
        env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(package)
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-File",
                             str(package / "bin" / script), *args],
                            env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode != 0
    # Snapshot may report a structured error, never a valid answer/inventory.
    if result.stdout.strip():
        assert json.loads(result.stdout).get("error")
    assert {p.relative_to(package) for p in package.rglob("*")} == before
