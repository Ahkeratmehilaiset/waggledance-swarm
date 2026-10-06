# SPDX-License-Identifier: BUSL-1.1
"""F28 dry plan: ops/windows/reboot/New-WdReleaseWorktree.ps1 under pwsh 7 and Windows PowerShell 5.1.

A throwaway repository and a bare local remote live in a persistent C: folder outside TEMP
(the source repository must be on persistent C: too) that each test creates and removes; links
point from there. The planned worktree path is a never-created C: path outside TEMP; every case
asserts that nothing was created, branched or pushed.
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


# A persistent C: folder outside TEMP in this checkout's own gitignored .codex-audit (the source repository must be
# on persistent C: too); a checkout anywhere else cannot host those cases and skips them.
PERSISTENT_FIXTURES = ROOT / ".codex-audit" / "f28-release-fixtures"
PERSISTENT_C = str(ROOT).upper().startswith("C:\\") and not any(
    (str(ROOT).upper() + "\\").startswith(str(Path(value).resolve()).upper().rstrip("\\") + "\\")
    for value in (os.environ.get("TEMP"), os.environ.get("TMP")) if value)


def junction(link: Path, target: Path) -> None:
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, check=True)


@pytest.fixture
def cdir():
    """A fresh persistent C: folder outside TEMP; junctions made in it are removed before the folder is."""
    if not PERSISTENT_C:
        pytest.skip("needs a checkout on persistent C: outside TEMP")
    root = PERSISTENT_FIXTURES / uuid.uuid4().hex
    root.mkdir(parents=True)
    links: list[Path] = []
    yield root, links
    for link in links:
        if link.exists() or os.path.lexists(link):
            os.rmdir(link)  # the junction only, never its target
    shutil.rmtree(root, onexc=lambda function, path, _: (os.chmod(path, 0o700), function(path)))


def make_repo(base: Path) -> dict:
    remote = base / "remote.git"
    work = base / "repo"
    git("init", "-q", "--bare", str(remote), cwd=base)
    git("init", "-q", str(work), cwd=base)
    for key, value in (("user.name", "fixture"), ("user.email", "fixture@example.invalid"), ("core.autocrlf", "false")):
        git("config", key, value, cwd=work)
    (work / "a.txt").write_text("a\n", encoding="utf-8")
    git("add", "a.txt", cwd=work)
    git("commit", "-q", "-m", "fixture", cwd=work)
    git("remote", "add", "origin", str(remote), cwd=work)
    git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=work)
    return {"work": work, "remote": remote, "commit": git("rev-parse", "HEAD", cwd=work)}


@pytest.fixture
def repo(cdir) -> dict:
    return make_repo(cdir[0])


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


# --- fable-5 review 04:42:17Z (W-A, W-B): the source repository must be persistent C: as well, and neither the
# repository nor the target may sit under a link (junction, symlink or mount point) that redirects it.

@pytest.mark.parametrize("shell", SHELLS)
def test_a_source_repository_under_temp_is_refused(tmp_path, shell):
    volatile = make_repo(tmp_path)
    path, branch = planned_path(), "claude-rco-2/release-fixture"
    code, plan = run(shell, RepositoryPath=volatile["work"], Branch=branch, Commit=volatile["commit"], WorktreePath=path)
    assert code == 2 and plan["reasons"] == ["repository_volatile"], plan
    assert_untouched(volatile, branch, path)


@pytest.mark.parametrize("shell", SHELLS)
def test_a_source_repository_reached_through_a_link_is_refused(cdir, tmp_path, shell):
    root, links = cdir
    volatile = make_repo(tmp_path)
    link = root / "linked-repo"
    junction(link, volatile["work"])
    links.append(link)
    code, plan = run(shell, RepositoryPath=link, Branch="claude-rco-2/release-fixture", Commit=volatile["commit"],
                     WorktreePath=planned_path())
    assert code == 2 and "repository_reparse" in plan["reasons"], plan


@pytest.mark.parametrize("shell", SHELLS)
def test_a_target_under_a_link_is_refused_even_when_the_string_looks_persistent(repo, cdir, tmp_path, shell):
    root, links = cdir
    link = root / "linked-target"
    junction(link, tmp_path)
    links.append(link)
    path = link / "wt"
    code, plan = run(shell, RepositoryPath=repo["work"], Branch="claude-rco-2/release-fixture", Commit=repo["commit"],
                     WorktreePath=path)
    assert code == 2 and plan["reasons"] == ["path_reparse"], plan
    assert not (tmp_path / "wt").exists()

# --- Grok #1771 c018 (fable-5 triage 73DC8665 MEDIUM 2+3): the Git COMMON directory must be persistent too, and a
# relative -RepositoryPath is resolved where Git runs (the PowerShell location) and emitted only as an absolute path.

def linked_worktree(source: dict, where: Path) -> Path:
    git("worktree", "add", "-q", "--detach", str(where), source["commit"], cwd=source["work"])
    return where


@pytest.mark.parametrize("shell", SHELLS)
def test_a_c_worktree_whose_git_common_dir_is_in_temp_is_refused(cdir, tmp_path, shell):
    root, _ = cdir
    volatile = make_repo(tmp_path)                       # objects and refs live under TEMP
    worktree = linked_worktree(volatile, root / "linked-worktree")   # top level is persistent C:
    path, branch = planned_path(), "claude-rco-2/release-fixture"
    code, plan = run(shell, RepositoryPath=worktree, Branch=branch, Commit=volatile["commit"], WorktreePath=path)
    assert code == 2 and plan["reasons"] == ["repository_volatile"], plan
    assert_untouched(volatile, branch, path)


@pytest.mark.parametrize("shell", SHELLS)
def test_success_twin_a_c_worktree_of_a_c_repository_still_plans(repo, cdir, shell):
    root, _ = cdir
    worktree = linked_worktree(repo, root / "linked-worktree")
    path, branch = planned_path(), "claude-rco-2/release-fixture"
    code, plan = run(shell, RepositoryPath=worktree, Branch=branch, Commit=repo["commit"], WorktreePath=path)
    assert code == 0 and plan["verdict"] == "plan", plan
    assert plan["commands"][0][:3] == ["git", "-C", str(worktree)]
    assert_untouched(repo, branch, path)


def run_in_location(shell: str, location: Path, process_cwd: Path, **params) -> tuple[int, dict]:
    """Run the script in-process after Set-Location, with a DIFFERENT process current directory."""
    quote = lambda value: str(value).replace("'", "''")  # noqa: E731
    arguments = " ".join(f"-{name} '{quote(value)}'" for name, value in params.items())
    command = (f"Set-Location -LiteralPath '{quote(location)}'; "
               f"[Environment]::CurrentDirectory = '{quote(process_cwd)}'; "
               f"& '{quote(SCRIPT)}' {arguments}; exit $LASTEXITCODE")
    result = subprocess.run([shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                             "-Command", command], capture_output=True, text=True, timeout=120)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert lines, result.stderr
    return result.returncode, json.loads(lines[-1])


@pytest.mark.parametrize("shell", SHELLS)
def test_a_relative_repository_path_resolves_where_git_runs_and_is_emitted_absolute(repo, cdir, tmp_path, shell):
    root, _ = cdir
    path, branch = planned_path(), "claude-rco-2/release-fixture"
    # The process cwd names an unrelated TEMP repository; Git runs in the PowerShell location (the persistent one).
    decoy = make_repo(tmp_path)
    code, plan = run_in_location(shell, repo["work"], decoy["work"], RepositoryPath=".", Branch=branch,
                                 Commit=repo["commit"], WorktreePath=path)
    assert code == 0 and plan["verdict"] == "plan", plan
    assert plan["commands"][0] == ["git", "-C", str(repo["work"]), "worktree", "add", "-b", branch, path, repo["commit"]]
    nested = repo["work"] / "sub"
    nested.mkdir()
    code, plan = run_in_location(shell, nested, decoy["work"], RepositoryPath="..", Branch=branch,
                                 Commit=repo["commit"], WorktreePath=path)
    assert code == 0 and plan["commands"][0][2] == str(repo["work"]), plan
    assert_untouched(repo, branch, path)


@pytest.mark.parametrize("shell", SHELLS)
def test_drive_relative_and_root_relative_repository_paths_are_refused(repo, shell):
    for ambiguous in ("C:repo", "\\Python\\repo"):
        code, plan = run(shell, RepositoryPath=ambiguous, Branch="claude-rco-2/release-fixture",
                         Commit=repo["commit"], WorktreePath=planned_path())
        assert code == 2 and "repository_path_ambiguous" in plan["reasons"], (ambiguous, plan)
