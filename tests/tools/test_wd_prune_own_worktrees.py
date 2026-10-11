# SPDX-License-Identifier: BUSL-1.1
"""tools/wd_prune_own_worktrees.py: owner-scoped, evidence-preserving worktree cleanup.

A worktree is removed only when every check of the 2026-10-11 disk-incident cleanups holds:
the basename starts with ``<AGENT_BRIDGE_AGENT>-``; it is not the main or the current worktree,
not missing and not locked; HEAD is detached (named-branch worktrees are kept); ``git status``
shows no tracked change and no untracked file; the only ignored content is ``__pycache__/`` or
``.pytest_cache/`` (an ignored ``.codex-audit/`` evidence folder keeps the worktree, because
``git worktree remove`` deletes ignored files silently); and HEAD is contained in a branch,
remote-tracking ref or tag (a commit reachable only from ``refs/stash`` is not enough). Removal
is ``git worktree remove`` without ``--force``; the default is a dry run. The owner comes from
the lane identity; another lane's name is refused.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
OWNER = "fable-5"

EXPECTED = {
    "repo": ("keep", "main_worktree"),
    "fable-5-clean": ("candidate", "eligible"),
    "fable-5-cache-only": ("candidate", "eligible"),
    "fable-5-evidence": ("keep", "ignored_content"),
    "fable-5-ignored-log": ("keep", "ignored_content"),
    "fable-5-dirty": ("keep", "dirty"),
    "fable-5-untracked": ("keep", "dirty"),
    "fable-5-branch": ("keep", "named_branch"),
    "fable-5-unreachable": ("keep", "unreachable_head"),
    "fable-5-stash-only": ("keep", "unreachable_head"),
    "fable-5-locked": ("keep", "locked"),
    "fable-5-missing": ("keep", "missing"),
    "fable-5-broken": ("keep", "status_unreadable"),
    "other-lane-clean": ("keep", "not_owner"),
    "fable-55-clean": ("keep", "not_owner"),
}
ELIGIBLE = {name for name, (decision, _) in EXPECTED.items() if decision == "candidate"}


def _prune():
    spec = importlib.util.spec_from_file_location("wd_prune_own_worktrees_under_test",
                                                  ROOT / "tools" / "wd_prune_own_worktrees.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=wd-test", "-c", "user.email=wd-test@example.invalid",
                           "-c", "commit.gpgsign=false", *args],
                          cwd=cwd, capture_output=True, text=True, check=True).stdout


def _commit(cwd: Path, name: str, text: str) -> str:
    (cwd / name).write_text(text, encoding="utf-8")
    _git(cwd, "add", name)
    _git(cwd, "commit", "-q", "-m", f"add {name}")
    return _git(cwd, "rev-parse", "HEAD").strip()


@pytest.fixture
def lane(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", OWNER)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / ".gitignore").write_text(".codex-audit/\n__pycache__/\n.pytest_cache/\n*.log\n", encoding="utf-8")
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "a.txt")
    _git(repo, "commit", "-q", "-m", "init")
    wts = tmp_path / "wt"
    wts.mkdir()
    paths = {"repo": repo}

    def detached(name: str) -> Path:
        path = wts / name
        _git(repo, "worktree", "add", "-q", "--detach", str(path), "HEAD")
        paths[name] = path
        return path

    detached("fable-5-clean")
    cache = detached("fable-5-cache-only")
    (cache / "__pycache__").mkdir()
    (cache / "__pycache__" / "m.cpython-313.pyc").write_bytes(b"\0pyc")
    (cache / ".pytest_cache" / "v" / "cache").mkdir(parents=True)
    (cache / ".pytest_cache" / "v" / "cache" / "nodeids").write_text("[]", encoding="utf-8")
    evidence = detached("fable-5-evidence")
    (evidence / ".codex-audit").mkdir()
    (evidence / ".codex-audit" / "evidence-red.json").write_bytes(b'{"native_exit": 1}')
    (detached("fable-5-ignored-log") / "run.log").write_text("log\n", encoding="utf-8")
    (detached("fable-5-dirty") / "a.txt").write_text("changed\n", encoding="utf-8")
    (detached("fable-5-untracked") / "new.txt").write_text("new\n", encoding="utf-8")
    branch = wts / "fable-5-branch"
    _git(repo, "worktree", "add", "-q", "-b", "fable-5/topic", str(branch), "HEAD")
    paths["fable-5-branch"] = branch
    _commit(detached("fable-5-unreachable"), "b.txt", "b\n")
    stash_only = detached("fable-5-stash-only")
    _commit(stash_only, "c.txt", "c\n")
    (stash_only / "a.txt").write_text("stashed\n", encoding="utf-8")
    _git(stash_only, "stash", "push", "-q", "-m", "wd-test-stash")
    locked = detached("fable-5-locked")
    _git(repo, "worktree", "lock", str(locked))
    shutil.rmtree(detached("fable-5-missing"))
    broken = detached("fable-5-broken")
    (broken / ".git").unlink()  # git for Windows marks the gitdir file hidden; a hidden file cannot be opened with "w"
    (broken / ".git").write_text("gitdir: " + str(tmp_path / "no-such-gitdir") + "\n", encoding="utf-8")
    detached("other-lane-clean")
    detached("fable-55-clean")
    return Lane(repo=repo, paths=paths)


class Lane:
    def __init__(self, repo: Path, paths: dict):
        self.repo = repo
        self.paths = paths


def _run(capsys, *args: str) -> tuple[int, dict]:
    code = _prune().main([*args, "--json"])
    out = capsys.readouterr().out
    return code, (json.loads(out) if out.strip() else {})


def _by_name(report: dict) -> dict:
    return {Path(entry["path"]).name: entry for entry in report["worktrees"]}


def _snapshot(paths: dict) -> dict:
    files = {}
    for name, path in paths.items():
        if name == "repo" or not path.is_dir():
            continue
        for file in path.rglob("*"):
            if file.is_file() and ".git" not in file.relative_to(path).parts[:1]:
                files[str(file)] = file.read_bytes()
    return files


def test_dry_run_classifies_every_worktree_and_removes_nothing(lane, capsys):
    before = _snapshot(lane.paths)
    code, report = _run(capsys, "--repo", str(lane.repo))
    assert code == 0
    assert report["schema"] == "wd.prune-own-worktrees.v1"
    assert report["owner"] == OWNER and report["mode"] == "dry_run"
    found = {name: (entry["decision"], entry["reason"]) for name, entry in _by_name(report).items()}
    assert found == EXPECTED
    assert _snapshot(lane.paths) == before
    for name, path in lane.paths.items():
        assert path.exists() or name == "fable-5-missing"


def test_apply_removes_only_eligible_worktrees_and_keeps_every_evidence_byte(lane, capsys):
    before = _snapshot({name: path for name, path in lane.paths.items() if name not in ELIGIBLE})
    unreachable_head = _git(lane.paths["fable-5-unreachable"], "rev-parse", "HEAD").strip()
    stash_head = _git(lane.paths["fable-5-stash-only"], "rev-parse", "HEAD").strip()
    code, report = _run(capsys, "--repo", str(lane.repo), "--apply")
    assert code == 0 and report["mode"] == "apply"
    entries = _by_name(report)
    for name in ELIGIBLE:
        assert entries[name]["decision"] == "removed"
        assert not lane.paths[name].exists()
    listed = _git(lane.repo, "worktree", "list", "--porcelain").replace("\\", "/")
    for name in ELIGIBLE:
        assert str(lane.paths[name]).replace("\\", "/") not in listed
    for name, (decision, reason) in EXPECTED.items():
        if name not in ELIGIBLE:
            assert (entries[name]["decision"], entries[name]["reason"]) == (decision, reason)
    assert _snapshot({name: path for name, path in lane.paths.items() if name not in ELIGIBLE}) == before
    for sha in (unreachable_head, stash_head):
        _git(lane.repo, "cat-file", "-e", f"{sha}^{{commit}}")


def test_apply_never_forces_a_removal(lane, capsys, monkeypatch):
    calls = []
    real_run = subprocess.run

    def recording_run(argv, *args, **kwargs):
        calls.append([str(part) for part in argv])
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", recording_run)
    code, _ = _run(capsys, "--repo", str(lane.repo), "--apply")
    assert code == 0
    removes = [argv for argv in calls if "worktree" in argv and "remove" in argv]
    assert len(removes) == len(ELIGIBLE)
    for argv in removes:
        assert "--force" not in argv and "-f" not in argv


def test_another_lanes_owner_name_is_refused(lane, capsys):
    code = _prune().main(["--repo", str(lane.repo), "--owner", "other-lane", "--apply"])
    assert code == 2
    assert lane.paths["other-lane-clean"].exists() and lane.paths["fable-5-clean"].exists()
    assert "AGENT_BRIDGE_AGENT" in capsys.readouterr().err


@pytest.mark.parametrize("value", [None, "", "fable 5", "../fable-5", "Fable-5"])
def test_a_missing_or_invalid_lane_identity_is_refused(lane, capsys, monkeypatch, value):
    if value is None:
        monkeypatch.delenv("AGENT_BRIDGE_AGENT", raising=False)
    else:
        monkeypatch.setenv("AGENT_BRIDGE_AGENT", value)
    code = _prune().main(["--repo", str(lane.repo), "--owner", OWNER, "--apply"])
    assert code == 2
    assert lane.paths["fable-5-clean"].exists() and lane.paths["fable-5-cache-only"].exists()


def test_the_matching_owner_flag_is_accepted(lane, capsys):
    code, report = _run(capsys, "--repo", str(lane.repo), "--owner", OWNER)
    assert code == 0 and report["owner"] == OWNER


def test_the_current_worktree_is_kept(lane, capsys):
    code, report = _run(capsys, "--repo", str(lane.paths["fable-5-clean"]))
    assert code == 0
    entries = _by_name(report)
    assert (entries["fable-5-clean"]["decision"], entries["fable-5-clean"]["reason"]) == ("keep", "current_worktree")
    assert entries["fable-5-cache-only"]["decision"] == "candidate"


def test_a_detached_main_worktree_with_the_owner_prefix_is_kept(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", OWNER)
    repo = tmp_path / "fable-5-main"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, "a.txt", "a\n")
    _git(repo, "checkout", "-q", "--detach")
    other = tmp_path / "other-lane-view"
    _git(repo, "worktree", "add", "-q", "--detach", str(other), "HEAD")
    code, report = _run(capsys, "--repo", str(other), "--apply")
    assert code == 0
    entry = _by_name(report)["fable-5-main"]
    assert (entry["decision"], entry["reason"]) == ("keep", "main_worktree")
    assert (repo / "a.txt").exists()


def test_human_output_names_each_decision(lane, capsys):
    assert _prune().main(["--repo", str(lane.repo)]) == 0
    out = capsys.readouterr().out
    assert "CANDIDATE" in out and "fable-5-clean" in out
    assert "KEEP" in out and "ignored_content" in out and "fable-5-evidence" in out
    assert "dry run" in out.lower()
