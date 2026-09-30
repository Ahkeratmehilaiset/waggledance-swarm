# SPDX-License-Identifier: BUSL-1.1
"""F28 dry plan: ops/windows/reboot/New-WdReleaseWorktree.ps1 under pwsh 7 and Windows PowerShell 5.1.

A throwaway repository and a bare local remote live under tmp_path. The planned worktree
path is a never-created C: path outside TEMP; every case asserts that nothing was created,
branched or pushed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ops" / "windows" / "reboot" / "New-WdReleaseWorktree.ps1"
WINDOWS_POWERSHELL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
SHELLS = [shell for shell in (shutil.which("pwsh"), str(WINDOWS_POWERSHELL) if WINDOWS_POWERSHELL.is_file() else None) if shell]
pytestmark = [pytest.mark.skipif(os.name != "nt" or not SHELLS or shutil.which("git") is None,
                                 reason="Windows PowerShell hosts and git are required")]


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> dict:
    remote = tmp_path / "remote.git"
    work = tmp_path / "repo"
    git("init", "-q", "--bare", str(remote), cwd=tmp_path)
    git("init", "-q", str(work), cwd=tmp_path)
    for key, value in (("user.name", "fixture"), ("user.email", "fixture@example.invalid"), ("core.autocrlf", "false")):
        git("config", key, value, cwd=work)
    (work / "a.txt").write_text("a\n", encoding="utf-8")
    git("add", "a.txt", cwd=work)
    git("commit", "-q", "-m", "fixture", cwd=work)
    git("remote", "add", "origin", str(remote), cwd=work)
    git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=work)
    return {"work": work, "remote": remote, "commit": git("rev-parse", "HEAD", cwd=work)}


def planned_path() -> str:
    return r"C:\Python\wd-release-plan-fixture-" + uuid.uuid4().hex  # never created


def run(shell: str, **params) -> tuple[int, dict]:
    argv = [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT)]
    for name, value in params.items():
        argv += ["-" + name] if value is True else ["-" + name, str(value)]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert lines, result.stderr
    return result.returncode, json.loads(lines[-1])


def assert_untouched(repo: dict, branch: str, path: str) -> None:
    assert not Path(path).exists()
    assert git("branch", "--list", branch, cwd=repo["work"]) == ""
    assert git("ls-remote", "--heads", "origin", "refs/heads/" + branch, cwd=repo["work"]) == ""


@pytest.mark.parametrize("shell", SHELLS)
def test_a_valid_request_plans_exact_commands_and_changes_nothing(repo, shell):
    path, branch = planned_path(), "claude-rco-2/release-fixture"
    code, plan = run(shell, RepositoryPath=repo["work"], Branch=branch, Commit=repo["commit"], WorktreePath=path)
    assert code == 0 and plan["verdict"] == "plan" and plan["reasons"] == []
    assert plan["schema"] == "wd.release-worktree-plan.v1" and plan["applied"] is False
    assert plan["commands"] == [
        ["git", "-C", str(repo["work"]), "worktree", "add", "-b", branch, path, repo["commit"]],
        ["git", "-C", path, "push", "-u", "origin", branch]]
    assert any("@{u}" in line for line in plan["verification"])
    assert_untouched(repo, branch, path)


@pytest.mark.parametrize("shell", SHELLS)
def test_apply_is_refused_before_any_check(repo, shell):
    path, branch = planned_path(), "claude-rco-2/release-fixture"
    code, plan = run(shell, RepositoryPath=repo["work"], Branch=branch, Commit=repo["commit"], WorktreePath=path,
                     Apply=True)
    assert code == 3 and plan["reasons"] == ["apply_requires_signed_activation"] and plan["verdict"] == "refuse"
    assert_untouched(repo, branch, path)


@pytest.mark.parametrize("shell", SHELLS)
def test_an_existing_branch_locally_or_on_the_remote_is_refused(repo, shell):
    git("branch", "taken-local", cwd=repo["work"])
    git("push", "-q", "origin", "HEAD:refs/heads/taken-remote", cwd=repo["work"])
    for branch, reason in (("taken-local", "branch_exists_locally"), ("taken-remote", "branch_exists_on_remote")):
        code, plan = run(shell, RepositoryPath=repo["work"], Branch=branch, Commit=repo["commit"],
                         WorktreePath=planned_path())
        assert code == 2 and reason in plan["reasons"], plan


@pytest.mark.parametrize("shell", SHELLS)
def test_invalid_inputs_are_refused_with_their_reasons(repo, shell, tmp_path):
    good = {"RepositoryPath": repo["work"], "Branch": "claude-rco-2/ok-branch", "Commit": repo["commit"],
            "WorktreePath": planned_path()}
    cases = [
        ({"Commit": "abc"}, "commit_invalid"),
        ({"Commit": "0" * 40}, "commit_absent"),
        ({"Branch": "Bad Branch"}, "branch_invalid"),
        ({"Branch": "x/../y"}, "branch_invalid"),
        ({"WorktreePath": r"relative\path"}, "path_not_persistent_c_drive"),
        ({"WorktreePath": str(tmp_path / "wt")}, "path_volatile"),
        ({"RepositoryPath": tmp_path}, "repository_invalid"),
        ({"Remote": "nowhere"}, "remote_unreachable"),
    ]
    for change, reason in cases:
        code, plan = run(shell, **{**good, **change})
        assert code == 2 and plan["verdict"] == "refuse" and reason in plan["reasons"], (change, plan)
