"""Branch-guard regression: Git global options must not hide the command.

Git is a shell-local test double: no branch, index, or live Bridge mutation.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / ".agent-bridge/bin/Invoke-BridgeGit.ps1"
SHELLS = list(dict.fromkeys(filter(None, (shutil.which("pwsh"), shutil.which("powershell.exe")))))


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def run_wrapper(tmp_path, shell, arguments, *, claim=True):
    runtime = tmp_path / "runtime"
    claims = runtime / "work_queue/claims"
    claims.mkdir(parents=True)
    if claim:
        (claims / "foreign.json").write_text(json.dumps({
            "agent": "fable-5", "task_id": "synthetic-claim", "mode": "write",
            "cwd": str(ROOT), "summary": "isolated fixture", "write_scope": ["synthetic"],
        }), encoding="utf-8")
    # rev-parse is the only Git operation the guard itself may invoke.
    script = "\n".join([
        "function global:git {",
        "  if ($args -contains 'rev-parse') {",
        f"    {quote(ROOT)}; $global:LASTEXITCODE = 0; return",
        "  }",
        "  'GIT_EXECUTED'; $global:LASTEXITCODE = 0",
        "}",
        f"Set-Location -LiteralPath {quote(ROOT)}",
        f"& {quote(WRAPPER)} -Agent codex-lead-1 -GitArgs @({','.join(map(quote, arguments))})",
        "exit $LASTEXITCODE",
    ])
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("WD_", "AGENT_BRIDGE_", "GIT_", "CLAUDE_CODE_"))}
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(runtime)
    return subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", script],
                          env=env, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
@pytest.mark.parametrize("prefix", [
    ["--no-pager"], ["--no-optional-locks"], ["--no-pager", "--no-optional-locks"],
    ["-C", "."], ["-c", "core.bare=false"], ["--git-dir=.git"],
    ["--work-tree=."], ["--config-env=core.bare=SYNTHETIC_VALUE"],
])
def test_global_option_cannot_bypass_foreign_write_claim(tmp_path, shell, prefix):
    result = run_wrapper(tmp_path, shell, prefix + ["checkout", "synthetic-missing-branch"])
    assert result.returncode == 2, result.stdout + result.stderr
    assert "GIT_EXECUTED" not in result.stdout


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
@pytest.mark.parametrize("arguments", [["status"], ["--no-pager", "status"], ["--", "status"]])
def test_safe_read_only_passthrough(tmp_path, shell, arguments):
    result = run_wrapper(tmp_path, shell, arguments)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "GIT_EXECUTED" in result.stdout


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
@pytest.mark.parametrize("arguments", [["--"], ["--no-pager"]])
def test_missing_command_is_rejected_without_git(tmp_path, shell, arguments):
    result = run_wrapper(tmp_path, shell, arguments, claim=False)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "GIT_EXECUTED" not in result.stdout
