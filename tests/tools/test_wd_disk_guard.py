# SPDX-License-Identifier: BUSL-1.1
"""tools/wd_disk_guard.py: LLM-free free-space guard for new test runs and new worktrees.

Contract: below 10 GiB free the guard warns; below 3 GiB it refuses a new pytest session
(tests/conftest.py hook) and a new worktree (``worktree-add``) with exit code 28 (ENOSPC).
On Windows C:\\ is always measured, plus the system drive and every target path; a path that
does not exist yet is measured at its nearest existing ancestor; a measurement that fails
counts as a block (fail closed). The module is loaded from its file so a missing module is a
per-test failure, not a collection error.
"""
from __future__ import annotations

import collections
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
GIB = 1024 ** 3
Usage = collections.namedtuple("Usage", "total used free")


def _guard():
    spec = importlib.util.spec_from_file_location("wd_disk_guard_under_test", ROOT / "tools" / "wd_disk_guard.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _usage(free_for):
    """A fake shutil.disk_usage; ``free_for(path)`` returns free bytes or raises."""
    calls: list[str] = []

    def disk_usage(path):
        calls.append(os.fspath(path))
        free = free_for(os.fspath(path))
        return Usage(1000 * GIB, 1000 * GIB - free, free)

    disk_usage.calls = calls
    return disk_usage


def _fixed(free):
    return _usage(lambda _path: free)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=wd-test", "-c", "user.email=wd-test@example.invalid",
                           "-c", "commit.gpgsign=false", *args],
                          cwd=cwd, capture_output=True, text=True, check=True).stdout


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


# --- thresholds ----------------------------------------------------------------------------

def test_thresholds_are_ten_and_three_gib_and_block_exit_is_enospc():
    guard = _guard()
    assert guard.WARN_BELOW_BYTES == 10 * GIB
    assert guard.BLOCK_BELOW_BYTES == 3 * GIB
    assert guard.BLOCK_EXIT_CODE == 28


@pytest.mark.parametrize(("free", "verdict"), [
    (500 * GIB, "ok"),
    (10 * GIB, "ok"),
    (10 * GIB - 1, "warn"),
    (3 * GIB, "warn"),
    (3 * GIB - 1, "block"),
    (0, "block"),
])
def test_classify_boundaries(free, verdict):
    assert _guard().classify(free) == verdict


# --- which paths are measured --------------------------------------------------------------

@pytest.mark.parametrize(("paths", "environ", "expected"), [
    ([], {"SystemDrive": "C:"}, ["C:\\"]),
    ([], {}, ["C:\\"]),
    ([], {"SystemDrive": "c:"}, ["C:\\"]),
    ([], {"SystemDrive": "bogus"}, ["C:\\"]),
    (["D:\\work"], {"SystemDrive": "E:"}, ["C:\\", "E:\\", "D:\\work"]),
    (["D:\\work", "D:\\work"], {"SystemDrive": "C:"}, ["C:\\", "D:\\work"]),
])
def test_windows_always_measures_c_drive_and_the_system_drive(paths, environ, expected):
    assert _guard().guarded_paths(paths, platform="win32", environ=environ) == expected


def test_other_platforms_measure_the_given_paths_or_the_cwd():
    guard = _guard()
    assert guard.guarded_paths([], platform="linux", environ={}) == [os.getcwd()]
    assert guard.guarded_paths(["/data/x"], platform="linux", environ={"SystemDrive": "C:"}) == ["/data/x"]


# --- check ---------------------------------------------------------------------------------

def test_check_reports_every_volume_and_the_worst_verdict_wins(tmp_path):
    guard = _guard()
    low = str(tmp_path)
    usage = _usage(lambda path: 2 * GIB if path.startswith(low) else 50 * GIB)
    result = guard.check("test", [tmp_path], disk_usage=usage)
    assert result["schema"] == "wd.disk-guard.v1"
    assert result["purpose"] == "test"
    assert result["verdict"] == "block"
    assert result["warn_below_bytes"] == 10 * GIB and result["block_below_bytes"] == 3 * GIB
    target = [volume for volume in result["volumes"] if volume["path"] == low]
    assert len(target) == 1
    assert target[0]["free_bytes"] == 2 * GIB and target[0]["verdict"] == "block" and target[0]["error"] is None
    if sys.platform == "win32":
        system = [volume for volume in result["volumes"] if volume["path"] == "C:\\"]
        assert len(system) == 1 and system[0]["verdict"] == "ok"


def test_check_warns_between_the_thresholds(tmp_path):
    result = _guard().check("worktree", [tmp_path], disk_usage=_fixed(5 * GIB))
    assert result["verdict"] == "warn"
    assert {volume["verdict"] for volume in result["volumes"]} == {"warn"}


def test_a_path_that_does_not_exist_yet_is_measured_at_its_nearest_existing_ancestor(tmp_path):
    usage = _fixed(50 * GIB)
    target = tmp_path / "new" / "deeper"
    result = _guard().check("worktree", [target], disk_usage=usage)
    volume = [v for v in result["volumes"] if v["path"] == str(target)]
    assert len(volume) == 1 and volume[0]["measured_path"] == str(tmp_path)
    assert str(tmp_path) in usage.calls
    assert not any("new" in Path(call).parts for call in usage.calls)
    assert result["verdict"] == "ok"


def test_a_measurement_error_fails_closed(tmp_path):
    low = str(tmp_path)

    def free_for(path):
        if path.startswith(low):
            raise OSError("simulated volume error")
        return 50 * GIB

    result = _guard().check("test", [tmp_path], disk_usage=_usage(free_for))
    assert result["verdict"] == "block"
    volume = [v for v in result["volumes"] if v["path"] == low][0]
    assert volume["verdict"] == "block" and volume["free_bytes"] is None
    assert "simulated volume error" in volume["error"]


def test_check_rejects_an_unknown_purpose(tmp_path):
    with pytest.raises(ValueError):
        _guard().check("deploy", [tmp_path], disk_usage=_fixed(50 * GIB))


# --- CLI: check ----------------------------------------------------------------------------

@pytest.mark.parametrize(("free", "code", "word"), [
    (50 * GIB, 0, "OK"),
    (5 * GIB, 0, "WARN"),
    (2 * GIB, 28, "BLOCK"),
])
def test_cli_check_exit_codes(monkeypatch, capsys, tmp_path, free, code, word):
    import shutil
    monkeypatch.setattr(shutil, "disk_usage", _fixed(free))
    assert _guard().main(["check", "--purpose", "status", "--path", str(tmp_path)]) == code
    captured = capsys.readouterr()
    assert f"WD-DISK-GUARD {word}" in captured.out + captured.err


def test_cli_check_json(monkeypatch, capsys, tmp_path):
    import shutil
    monkeypatch.setattr(shutil, "disk_usage", _fixed(2 * GIB))
    assert _guard().main(["check", "--purpose", "test", "--path", str(tmp_path), "--json"]) == 28
    report = json.loads(capsys.readouterr().out)
    assert report["schema"] == "wd.disk-guard.v1" and report["purpose"] == "test" and report["verdict"] == "block"


# --- worktree-add --------------------------------------------------------------------------

@pytest.mark.parametrize(("args", "target"), [
    (["--detach", "p", "HEAD"], "p"),
    (["-b", "topic", "p"], "p"),
    (["-btopic", "p", "main"], "p"),
    (["-B", "topic", "--lock", "--reason", "why", "p"], "p"),
    (["--reason=why", "--lock", "-q", "p", "HEAD"], "p"),
    (["--detach", "--", "-odd-dir", "HEAD"], "-odd-dir"),
])
def test_worktree_add_target_parsing(args, target):
    assert _guard().worktree_add_target(args) == target


@pytest.mark.parametrize("args", [
    [],
    ["--detach"],
    ["-b"],
    ["--frobnicate", "p"],
    ["-fq", "p"],
    ["--detach", "--"],
])
def test_worktree_add_target_refuses_what_it_cannot_parse(args):
    with pytest.raises(ValueError):
        _guard().worktree_add_target(args)


def test_worktree_add_below_three_gib_is_refused_and_git_is_not_run(monkeypatch, capsys, tmp_path):
    import shutil
    repo = _repo(tmp_path)
    target = tmp_path / "wt-blocked"
    monkeypatch.setattr(shutil, "disk_usage", _fixed(3 * GIB - 1))
    code = _guard().main(["worktree-add", "--repo", str(repo), "--", "--detach", str(target), "HEAD"])
    assert code == 28
    assert not target.exists()
    assert str(target).replace("\\", "/") not in _git(repo, "worktree", "list", "--porcelain").replace("\\", "/")
    captured = capsys.readouterr()
    assert "WD-DISK-GUARD BLOCK" in captured.out + captured.err


@pytest.mark.parametrize(("free", "warned"), [(50 * GIB, False), (5 * GIB, True)])
def test_worktree_add_runs_git_when_space_allows(monkeypatch, capfd, tmp_path, free, warned):
    import shutil
    repo = _repo(tmp_path)
    target = tmp_path / "wt-made"
    monkeypatch.setattr(shutil, "disk_usage", _fixed(free))
    assert _guard().main(["worktree-add", "--repo", str(repo), "--", "--detach", str(target), "HEAD"]) == 0
    assert (target / ".git").is_file()
    captured = capfd.readouterr()
    assert ("WD-DISK-GUARD WARN" in captured.out + captured.err) is warned


def test_worktree_add_relative_target_is_measured_relative_to_the_repo(monkeypatch, tmp_path):
    import shutil
    repo = _repo(tmp_path)
    usage = _fixed(50 * GIB)
    monkeypatch.setattr(shutil, "disk_usage", usage)
    assert _guard().main(["worktree-add", "--repo", str(repo), "--", "--detach", "rel-wt", "HEAD"]) == 0
    assert (repo / "rel-wt" / ".git").is_file()
    assert str(repo) in usage.calls


def _blocked_under(root: Path):
    """A fake disk_usage: 1 GiB free under ROOT (a BLOCK), 50 GiB everywhere else."""
    prefix = os.path.normcase(str(root.resolve()))
    return _usage(lambda path: GIB if os.path.normcase(str(Path(path).resolve())).startswith(prefix) else 50 * GIB)


def test_worktree_add_also_measures_the_repository(monkeypatch, capsys, tmp_path):
    """Git writes the new worktree's index and metadata into the repository's git directory, so a
    blocked repository volume refuses even when the target's volume has room (Tools' Grok on PR1859)."""
    import shutil
    repo = _repo(tmp_path)
    target = tmp_path / "elsewhere" / "wt"
    monkeypatch.setattr(shutil, "disk_usage", _blocked_under(repo))
    code = _guard().main(["worktree-add", "--repo", str(repo), "--", "--detach", str(target), "HEAD"])
    assert code == 28
    assert not target.exists()
    assert "WD-DISK-GUARD BLOCK" in capsys.readouterr().err


def test_worktree_add_from_a_linked_worktree_measures_the_common_git_dir(monkeypatch, capsys, tmp_path):
    import shutil
    repo = _repo(tmp_path)
    linked = tmp_path / "linked"
    _git(repo, "worktree", "add", "-q", "--detach", str(linked), "HEAD")
    target = tmp_path / "elsewhere" / "wt"
    monkeypatch.setattr(shutil, "disk_usage", _blocked_under(repo / ".git"))  # only the common git dir is full
    code = _guard().main(["worktree-add", "--repo", str(linked), "--", "--detach", str(target), "HEAD"])
    assert code == 28
    assert not target.exists()
    assert "WD-DISK-GUARD BLOCK" in capsys.readouterr().err


def test_worktree_add_measures_the_common_git_dir_without_rev_parse_path_format(monkeypatch, capsys, tmp_path):
    """git before 2.31 has no --path-format, yet its git worktree add works; the common git dir is
    still measured (Grok on the PR1859 follow-up)."""
    import shutil
    real_run = subprocess.run

    def git_without_path_format(args, *rest, **kwargs):
        if any(str(arg).startswith("--path-format") for arg in args):
            return subprocess.CompletedProcess(args, 129, "", "error: unknown option `path-format=absolute'\n")
        return real_run(args, *rest, **kwargs)

    repo = _repo(tmp_path)
    target = tmp_path / "elsewhere" / "wt"
    monkeypatch.setattr(subprocess, "run", git_without_path_format)
    monkeypatch.setattr(shutil, "disk_usage", _blocked_under(repo / ".git"))
    code = _guard().main(["worktree-add", "--repo", str(repo), "--", "--detach", str(target), "HEAD"])
    assert code == 28
    assert not target.exists()
    assert "WD-DISK-GUARD BLOCK" in capsys.readouterr().err


def test_worktree_add_passes_a_dash_dash_path_through(monkeypatch, tmp_path):
    import shutil
    repo = _repo(tmp_path)
    monkeypatch.setattr(shutil, "disk_usage", _fixed(50 * GIB))
    assert _guard().main(["worktree-add", "--repo", str(repo), "--", "--detach", "--", "-odd-dir", "HEAD"]) == 0
    assert (repo / "-odd-dir" / ".git").is_file()


@pytest.mark.parametrize("with_repo", [True, False])
def test_worktree_add_requires_the_separator(monkeypatch, capsys, tmp_path, with_repo):
    import shutil
    repo = _repo(tmp_path)
    monkeypatch.setattr(shutil, "disk_usage", _fixed(50 * GIB))
    monkeypatch.chdir(repo)  # without --repo the default "." is a valid repo, so only the missing -- refuses
    options = ["--repo", str(repo)] if with_repo else []
    assert _guard().main(["worktree-add", *options, "--detach", str(tmp_path / "x"), "HEAD"]) == 2
    assert not (tmp_path / "x").exists()
    assert "usage: worktree-add" in capsys.readouterr().err


def test_worktree_add_with_unparseable_arguments_is_refused(monkeypatch, tmp_path):
    import shutil
    repo = _repo(tmp_path)
    monkeypatch.setattr(shutil, "disk_usage", _fixed(50 * GIB))
    assert _guard().main(["worktree-add", "--repo", str(repo), "--", "--frobnicate", str(tmp_path / "x")]) == 2
    assert not (tmp_path / "x").exists()


def test_worktree_add_returns_the_git_exit_code(monkeypatch, tmp_path):
    import shutil
    repo = _repo(tmp_path)
    monkeypatch.setattr(shutil, "disk_usage", _fixed(50 * GIB))
    code = _guard().main(["worktree-add", "--repo", str(repo), "--", "--detach", str(tmp_path / "x"), "no-such-ref"])
    assert code not in (0, 2, 28)
    assert not (tmp_path / "x" / ".git").exists()


# --- pytest session guard ------------------------------------------------------------------

def _session(tmp_path, *, worker=False):
    config = SimpleNamespace(option=SimpleNamespace(basetemp=str(tmp_path / "basetemp")), rootpath=tmp_path)
    if worker:
        config.workerinput = {}
    return SimpleNamespace(config=config)


def _guard_session(session):
    """Run the session guard; an unexpected pytest.exit becomes a value, not the end of this session."""
    try:
        _guard().pytest_session_guard(session)
    except pytest.exit.Exception as exc:
        return exc
    return None


def test_session_guard_blocks_below_three_gib(monkeypatch, tmp_path):
    import shutil
    monkeypatch.setattr(shutil, "disk_usage", _fixed(2 * GIB))
    with pytest.raises(pytest.exit.Exception) as excinfo:
        _guard().pytest_session_guard(_session(tmp_path))
    assert excinfo.value.returncode == 28
    assert "WD-DISK-GUARD BLOCK" in str(excinfo.value.msg)


def test_session_guard_warns_between_thresholds(monkeypatch, capsys, tmp_path):
    import shutil
    monkeypatch.setattr(shutil, "disk_usage", _fixed(5 * GIB))
    assert _guard_session(_session(tmp_path)) is None
    assert "WD-DISK-GUARD WARN" in capsys.readouterr().err


def test_session_guard_is_silent_when_space_is_ample(monkeypatch, capsys, tmp_path):
    import shutil
    monkeypatch.setattr(shutil, "disk_usage", _fixed(50 * GIB))
    assert _guard_session(_session(tmp_path)) is None
    captured = capsys.readouterr()
    assert "WD-DISK-GUARD" not in captured.out + captured.err


def test_session_guard_measures_the_rootdir_and_the_basetemp_ancestor(monkeypatch, tmp_path):
    import shutil
    usage = _fixed(50 * GIB)
    monkeypatch.setattr(shutil, "disk_usage", usage)
    assert _guard_session(_session(tmp_path)) is None
    assert str(tmp_path) in usage.calls


def test_session_guard_skips_xdist_workers(monkeypatch, tmp_path):
    import shutil
    usage = _fixed(2 * GIB)
    monkeypatch.setattr(shutil, "disk_usage", usage)
    assert _guard_session(_session(tmp_path, worker=True)) is None
    assert usage.calls == []


def test_session_guard_child_target():
    """The node the subprocess cases below run; it passes whenever the session is allowed to start."""


_PLUGIN = '''
import collections
import os
import shutil

_Usage = collections.namedtuple("_Usage", "total used free")
_FREE = int(os.environ["WD_TEST_FAKE_FREE_BYTES"])


def _fake_disk_usage(path):
    return _Usage(_FREE * 2 + 1, _FREE + 1, _FREE)


shutil.disk_usage = _fake_disk_usage
'''


@pytest.mark.parametrize(("free", "code", "ran", "word"), [
    (50 * GIB, 0, True, None),
    (5 * GIB, 0, True, "WD-DISK-GUARD WARN"),
    (2 * GIB, 28, False, "WD-DISK-GUARD BLOCK"),
])
def test_tests_conftest_guards_a_new_pytest_session(tmp_path, free, code, ran, word):
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    (plugin_dir / "wd_fake_disk_plugin.py").write_text(_PLUGIN, encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if key not in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS")}
    env["PYTHONPATH"] = str(plugin_dir) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["WD_TEST_FAKE_FREE_BYTES"] = str(free)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "wd_fake_disk_plugin",
         f"--basetemp={tmp_path / 'child-basetemp'}",
         "tests/tools/test_wd_disk_guard.py::test_session_guard_child_target"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    output = proc.stdout + proc.stderr
    assert proc.returncode == code, output
    assert ("1 passed" in proc.stdout) is ran, output
    if word is None:
        assert "WD-DISK-GUARD" not in output
    else:
        assert word in output
