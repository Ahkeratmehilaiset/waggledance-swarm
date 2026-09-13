"""Regression tests for ``tools/savepoint.ps1 -MessageFile``.

``-Message`` passes the commit message through command-line argument parsing,
which mangles multi-line messages when the script is invoked through a nested
``powershell -File`` call.  ``-MessageFile`` hands the exact bytes to
``git commit -F`` instead.  These tests drive the real script against a
throwaway repository with a local bare ``origin`` (no network, no project
repo), under every PowerShell host available on the machine.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "savepoint.ps1"

def _powershell_hosts() -> tuple[str, ...]:
    """Every distinct PowerShell host on PATH (pwsh 7 and/or Windows 5.1)."""

    hosts: dict[str, str] = {}
    for candidate in (
        shutil.which("pwsh"),
        shutil.which("powershell"),
        shutil.which("powershell.exe"),
    ):
        if candidate:
            key = str(Path(candidate).resolve()).lower()
            hosts.setdefault(key, candidate)
    return tuple(hosts.values())


POWERSHELL_HOSTS: tuple[str, ...] = _powershell_hosts()

pytestmark = [
    pytest.mark.skipif(
        os.name != "nt", reason="savepoint.ps1 is a Windows C:-drive tool"
    ),
    pytest.mark.skipif(not POWERSHELL_HOSTS, reason="PowerShell is unavailable"),
]

# Multi-line, non-ASCII, quotes, backticks and a ``$variable`` -- exactly the
# characters that break when a message passes through argument parsing.
MESSAGE = (
    "fix(savepoint): accept -MessageFile\n"
    "\n"
    "Body line with \"double quotes\", 'single quotes', a `backtick`, "
    "and $notAVariable.\n"
    "Non-ASCII survives: \u00e4\u00f6 \u2013 \u20ac\n"
    "\n"
    "Co-Authored-By: Example <example@example.invalid>\n"
)


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0, (
        f"git {' '.join(args)} failed ({result.returncode}): "
        f"{result.stdout}{result.stderr}"
    )
    return result.stdout


def _run_savepoint(
    host: str,
    repo: Path,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    # The script refuses to run from a cwd under $env:TEMP / $env:TMP (volatile
    # paths are not a source of truth).  The throwaway repo lives under pytest's
    # tmp_path, which usually IS the system temp dir, so the subprocess gets a
    # non-matching TEMP/TMP.  This exercises the message path, not that guard.
    env = dict(os.environ)
    env["TEMP"] = r"C:\savepoint-test-not-the-temp-dir"
    env["TMP"] = r"C:\savepoint-test-not-the-temp-dir"
    return subprocess.run(
        [
            host,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            *args,
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


@pytest.fixture
def repo_with_origin(tmp_path: Path) -> tuple[Path, Path]:
    """A C:-drive repo on a real branch with one commit and a local bare origin."""

    assert str(tmp_path).upper().startswith("C:"), tmp_path
    origin = tmp_path / "origin.git"
    repo = tmp_path / "repo"
    origin.mkdir()
    repo.mkdir()
    _git(origin, "init", "--bare", "--quiet")
    _git(repo, "init", "--quiet", "--initial-branch", "savepoint-test")
    # Deep pytest basetemps exceed Windows MAX_PATH inside .git/objects.
    _git(origin, "config", "core.longpaths", "true")
    _git(repo, "config", "core.longpaths", "true")
    _git(repo, "config", "user.email", "savepoint-test@example.invalid")
    _git(repo, "config", "user.name", "Savepoint Test")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "config", "core.autocrlf", "false")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "commit", "--allow-empty", "--quiet", "-m", "init")
    return repo, origin


def _stage_change(repo: Path, name: str = "change.txt") -> None:
    (repo / name).write_text("staged change\n", encoding="utf-8")
    _git(repo, "add", name)


def _head_message(repo: Path, ref: str = "HEAD") -> str:
    return _git(repo, "log", "-1", "--format=%B", ref)


def _commit_count(repo: Path) -> int:
    return int(_git(repo, "rev-list", "--count", "HEAD").strip())


@pytest.mark.parametrize("host", POWERSHELL_HOSTS)
@pytest.mark.parametrize("bom", (False, True), ids=("utf8", "utf8-bom"))
def test_message_file_round_trips_exact_bytes_and_pushes(
    host: str, bom: bool, repo_with_origin: tuple[Path, Path], tmp_path: Path
) -> None:
    repo, origin = repo_with_origin
    _stage_change(repo)
    message_file = tmp_path / "commit-msg.txt"
    message_file.write_bytes(MESSAGE.encode("utf-8-sig" if bom else "utf-8"))

    result = _run_savepoint(
        host, repo, "-MessageFile", str(message_file), "-SkipTests"
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _commit_count(repo) == 2
    # git's own message cleanup strips trailing blank lines; everything else,
    # including a BOM-free first line, must be exactly what was written.
    assert _head_message(repo).rstrip("\n") == MESSAGE.rstrip("\n")
    assert not _head_message(repo).startswith("\ufeff")
    # Pushed to origin on the same branch.
    assert (
        _head_message(origin, "savepoint-test").rstrip("\n") == MESSAGE.rstrip("\n")
    )
    # The scratch copy handed to ``git commit -F`` is removed again.
    git_dir = Path(_git(repo, "rev-parse", "--absolute-git-dir").strip())
    assert not list(git_dir.glob("SAVEPOINT_COMMIT_MSG*"))


@pytest.mark.parametrize("host", POWERSHELL_HOSTS)
def test_plain_message_path_is_unchanged(
    host: str, repo_with_origin: tuple[Path, Path]
) -> None:
    repo, origin = repo_with_origin
    _stage_change(repo)

    result = _run_savepoint(
        host, repo, "-Message", "chore: single line", "-SkipTests"
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _head_message(repo).rstrip("\n") == "chore: single line"
    assert (
        _head_message(origin, "savepoint-test").rstrip("\n") == "chore: single line"
    )


@pytest.mark.parametrize("host", POWERSHELL_HOSTS)
@pytest.mark.parametrize(
    ("extra_args", "expected_text"),
    (
        pytest.param((), "exactly one of -Message or -MessageFile", id="neither"),
        pytest.param(
            ("-Message", "x", "-MessageFile", "__FILE__"),
            "exactly one of -Message or -MessageFile",
            id="both",
        ),
        pytest.param(
            ("-MessageFile", "__MISSING__"),
            "not an existing file",
            id="missing-file",
        ),
        pytest.param(("-MessageFile", "__EMPTY__"), "is empty", id="empty-file"),
        pytest.param(
            ("-MessageFile", "__BOM_ONLY__"), "is empty", id="bom-only-file"
        ),
    ),
)
def test_invalid_message_source_fails_before_any_git_work(
    host: str,
    extra_args: tuple[str, ...],
    expected_text: str,
    repo_with_origin: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    repo, origin = repo_with_origin
    _stage_change(repo)
    real = tmp_path / "real.txt"
    real.write_text("real message\n", encoding="utf-8")
    empty = tmp_path / "empty.txt"
    empty.write_bytes(b"   \n")
    bom_only = tmp_path / "bom-only.txt"
    bom_only.write_bytes(b"\xef\xbb\xbf")
    substitutions = {
        "__FILE__": str(real),
        "__MISSING__": str(tmp_path / "does-not-exist.txt"),
        "__EMPTY__": str(empty),
        "__BOM_ONLY__": str(bom_only),
    }
    args = tuple(substitutions.get(arg, arg) for arg in extra_args)

    result = _run_savepoint(host, repo, *args, "-SkipTests")

    assert result.returncode != 0
    # Windows PowerShell 5.1 word-wraps formatted Write-Error output at an
    # environment-dependent column when stdout/stderr are redirected, which can
    # split the phrase across lines; compare on whitespace-normalized text.
    combined = " ".join((result.stdout + result.stderr).split())
    assert expected_text in combined, combined
    # Validation happens before the drive/repo/staging/test/commit/push steps:
    # nothing was committed and nothing reached origin.
    assert _commit_count(repo) == 1
    assert _git(origin, "for-each-ref").strip() == ""
