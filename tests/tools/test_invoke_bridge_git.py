"""F9 Invoke-BridgeGit guard fixtures (authored per operator directive; NOT executed yet).

Git is a shell-local test double: `git -C <dir> rev-parse --show-toplevel` answers
<dir> (every directory is its own worktree root) and any other call prints
GIT_EXECUTED with its argv. No branch, index, live Bridge or real repository is
touched. Every refusal has a same-fixture success twin. Complements the existing
tests/tools/test_bridge_git_leading_options.py (commit 43ea4d81), which stays valid.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / ".agent-bridge" / "bin" / "Invoke-BridgeGit.ps1"
SHELLS = list(dict.fromkeys(filter(None, (shutil.which("pwsh"), shutil.which("powershell.exe")))))
pytestmark = pytest.mark.skipif(not SHELLS, reason="PowerShell is required")
_SCRUB = ("WD_", "AGENT_BRIDGE_", "GIT_", "CLAUDE_CODE_")


def _q(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _worktrees(tmp_path: Path) -> tuple[Path, Path]:
    a, b = tmp_path / "wt-a", tmp_path / "wt-b"
    (a / "sub").mkdir(parents=True)
    b.mkdir()
    return a, b


def _run(tmp_path: Path, shell: str, arguments: list[str], *, cwd: Path, agent: str = "claude-rco-2",
         claims: list[dict] | None = None, env_extra: dict | None = None, force: bool = False,
         bound_agent: str | None = None) -> subprocess.CompletedProcess:
    runtime = tmp_path / "runtime"
    claim_dir = runtime / "work_queue" / "claims"
    claim_dir.mkdir(parents=True, exist_ok=True)
    for old in claim_dir.glob("*.json"):
        old.unlink()
    for index, claim in enumerate(claims or []):
        (claim_dir / f"c{index}.json").write_text(json.dumps(claim), encoding="utf-8")
    script = "\n".join([
        "function global:git {",
        "  $a = @($args)",
        "  if ($a -contains 'rev-parse') {",
        "    $i = [array]::IndexOf($a, '-C'); if ($i -ge 0) { $a[$i + 1] } else { (Get-Location).Path }",
        "    $global:LASTEXITCODE = 0; return",
        "  }",
        "  'GIT_EXECUTED ' + ($a -join ' '); $global:LASTEXITCODE = 0",
        "}",
        f"Set-Location -LiteralPath {_q(cwd)}",
        f"& {_q(WRAPPER)} -Agent {_q(agent)}" + (" -Force" if force else "")
        + f" -GitArgs @({','.join(map(_q, arguments))})",
        "exit $LASTEXITCODE",
    ])
    env = {k: v for k, v in os.environ.items() if not k.startswith(_SCRUB)}
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(runtime)
    if bound_agent is not None:
        env["AGENT_BRIDGE_AGENT"] = bound_agent
    env.update(env_extra or {})
    return subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", script],
                          env=env, capture_output=True, text=True, timeout=60)


def _claim(agent: str, cwd: Path) -> dict:
    return {"agent": agent, "task_id": "synthetic/" + agent, "mode": "write", "cwd": str(cwd),
            "summary": "fixture", "write_scope": ["synthetic"]}


# -- -C binds the EFFECTIVE directory --------------------------------------------

@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
def test_dash_c_into_a_foreign_claimed_worktree_is_blocked(tmp_path, shell):
    a, b = _worktrees(tmp_path)
    result = _run(tmp_path, shell, ["-C", str(a), "checkout", "x"], cwd=b, claims=[_claim("fable-5", a)])
    assert result.returncode == 2 and "GIT_EXECUTED" not in result.stdout


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
def test_dash_c_out_of_a_claimed_worktree_to_a_free_one_runs(tmp_path, shell):
    # Success twin: caller sits in the claimed worktree but targets another one.
    a, b = _worktrees(tmp_path)
    result = _run(tmp_path, shell, ["-C", str(b), "checkout", "x"], cwd=a, claims=[_claim("fable-5", a)])
    assert result.returncode == 0, result.stderr
    assert "GIT_EXECUTED -C " + str(b) + " checkout x" in result.stdout


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
def test_relative_dash_c_chain_and_empty_value_resolve_like_git(tmp_path, shell):
    a, b = _worktrees(tmp_path)
    # -C wt-a -C sub -C "" => <tmp>/wt-a/sub, which carries the foreign claim.
    blocked = _run(tmp_path, shell, ["-C", "wt-a", "-C", "sub", "-C", "", "switch", "x"], cwd=tmp_path,
                   claims=[_claim("fable-5", a / "sub")])
    assert blocked.returncode == 2
    free = _run(tmp_path, shell, ["-C", "wt-b", "switch", "x"], cwd=tmp_path, claims=[_claim("fable-5", a / "sub")])
    assert free.returncode == 0, free.stderr


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
@pytest.mark.parametrize("arguments", [
    ["-C"],
    pytest.param(["-C", "C:relative", "status"], marks=pytest.mark.skipif(os.name != "nt", reason="Windows drive-relative form")),
    pytest.param(["-C", "\\rooted", "status"], marks=pytest.mark.skipif(os.name != "nt", reason="Windows root-relative form")),
    ["-Cwt-a", "status"], ["-ccolor.ui=false", "status"]])
def test_malformed_or_unbindable_directory_options_refuse_for_every_verb(tmp_path, shell, arguments):
    _, b = _worktrees(tmp_path)
    result = _run(tmp_path, shell, arguments, cwd=b)
    assert result.returncode == 2 and "GIT_EXECUTED" not in result.stdout


# -- -c strict allowlist ------------------------------------------------------------

@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
@pytest.mark.parametrize("setting", ["color.ui=false", "COLOR.UI=always", "core.quotepath=off",
                                     "advice.detachedHead=false", "color.ui"])
def test_display_only_config_is_allowed(tmp_path, shell, setting):
    _, b = _worktrees(tmp_path)
    result = _run(tmp_path, shell, ["-c", setting, "status"], cwd=b)
    assert result.returncode == 0, result.stderr
    assert "GIT_EXECUTED -c " + setting + " status" in result.stdout


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
@pytest.mark.parametrize("setting", ["core.worktree=/elsewhere", "core.hooksPath=hooks", "core.fsmonitor=x",
                                     "alias.st=!calc", "include.path=x", "core.sshCommand=x",
                                     "color.ui=a b", "color.ui=x;y", "color.ui.extra=1"])
def test_non_allowlisted_or_unsafe_config_is_refused(tmp_path, shell, setting):
    _, b = _worktrees(tmp_path)
    result = _run(tmp_path, shell, ["-c", setting, "status"], cwd=b)
    assert result.returncode == 2 and "GIT_EXECUTED" not in result.stdout


# -- inherited redirection variables on mutation -----------------------------------------

@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
@pytest.mark.parametrize("name", ["GIT_NAMESPACE", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
                                  "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_3", "GIT_CONFIG_GLOBAL",
                                  "GIT_CONFIG_SYSTEM", "GIT_CONFIG", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                                  "git_config_global"])
def test_branch_moving_refuses_redirection_env_but_passthrough_is_unchanged(tmp_path, shell, name):
    _, b = _worktrees(tmp_path)
    env = {name: "synthetic"}
    moving = _run(tmp_path, shell, ["switch", "x"], cwd=b, env_extra=env)
    assert moving.returncode == 2 and "GIT_EXECUTED" not in moving.stdout
    passthrough = _run(tmp_path, shell, ["status"], cwd=b, env_extra=env)
    assert passthrough.returncode == 0 and "GIT_EXECUTED status" in passthrough.stdout


# -- -Agent bound to the pinned session identity ------------------------------------------

@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
def test_bound_lane_cannot_claim_another_agents_identity(tmp_path, shell):
    a, _ = _worktrees(tmp_path)
    claims = [_claim("fable-5", a)]
    spoof = _run(tmp_path, shell, ["switch", "x"], cwd=a, agent="fable-5", claims=claims, bound_agent="claude-rco-2")
    assert spoof.returncode == 2 and "identity_mismatch" in spoof.stderr
    own = _run(tmp_path, shell, ["switch", "x"], cwd=a, agent="fable-5", claims=claims, bound_agent="fable-5")
    assert own.returncode == 0, own.stderr  # success twin: the real owner in its exact cwd


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
@pytest.mark.parametrize("bound", [None, "claude-rco-2"])
def test_force_cannot_self_grant_operator_or_system(tmp_path, shell, bound):
    a, _ = _worktrees(tmp_path)
    claims = [_claim("fable-5", a)]
    for agent in ("operator", "system"):
        result = _run(tmp_path, shell, ["switch", "x"], cwd=a, agent=agent, claims=claims, force=True,
                      bound_agent=bound)
        assert result.returncode == 2 and "GIT_EXECUTED" not in result.stdout
        assert "identity_mismatch" in result.stderr


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
def test_force_by_ordinary_lane_is_still_rejected(tmp_path, shell):
    a, _ = _worktrees(tmp_path)
    result = _run(tmp_path, shell, ["switch", "x"], cwd=a, agent="claude-rco-2", force=True,
                  claims=[_claim("fable-5", a)], bound_agent="claude-rco-2")
    assert result.returncode == 2 and "REJECTED" in result.stderr


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: Path(s).stem)
def test_passthrough_with_generic_agent_label_keeps_working(tmp_path, shell):
    # Identity binding applies only where -Agent carries authority (branch moves).
    _, b = _worktrees(tmp_path)
    result = _run(tmp_path, shell, ["--no-pager", "log", "-1"], cwd=b, agent="claude", bound_agent="claude-rco-2")
    assert result.returncode == 0 and "GIT_EXECUTED --no-pager log -1" in result.stdout
