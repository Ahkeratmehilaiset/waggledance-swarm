# SPDX-License-Identifier: BUSL-1.1
"""tools/wd_prune_own_worktrees.py: owner-scoped, evidence-preserving worktree cleanup.

A worktree is removed only when every check of the 2026-10-11 disk-incident cleanups holds:
the basename starts with ``<AGENT_BRIDGE_AGENT>-`` (a lane name is ``letters(-letters)*-N``, so
no lane name is a hyphen-prefix of another); it is not the main or the current worktree (by
file identity, so a junction or symlink spelling of the path still matches), not
missing and not locked; no rebase, merge, cherry-pick, revert or bisect is in progress; HEAD is detached (named-branch worktrees are kept); ``git status``
shows no tracked change and no untracked file; the only ignored content is ``__pycache__/`` or
``.pytest_cache/`` (an ignored ``.codex-audit/`` evidence folder keeps the worktree, because
``git worktree remove`` deletes ignored files silently); no index entry hides a change
(assume-unchanged or skip-worktree); the worktree has no per-worktree refs (``refs/worktree``,
``refs/bisect``, ``refs/rewritten`` are deleted with it); and HEAD and every commit in the
worktree's HEAD reflog are contained in a branch, remote-tracking ref or tag (a commit
reachable only from ``refs/stash`` or from that reflog is not enough); so is every commit that
ORIG_HEAD, MERGE_AUTOSTASH or a FETCH_HEAD line names (per-worktree files, deleted with the
worktree; an unparseable one keeps it, and a long FETCH_HEAD is no reason to keep). Removal
is ``git worktree remove`` without ``--force``, after a final status re-check; the default is a
dry run. The owner comes from the lane identity; another lane's name is refused.
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
    "fable-5-reflog-only": ("keep", "reflog_only_commits"),
    "fable-5-assume-unchanged": ("keep", "hidden_index_flags"),
    "fable-5-skip-worktree": ("keep", "hidden_index_flags"),
    "fable-5-worktree-ref": ("keep", "worktree_refs"),
    "fable-5-rebase-in-progress": ("keep", "operation_in_progress"),
    "fable-5-merge-in-progress": ("keep", "operation_in_progress"),
    "fable-5-orig-head-only": ("keep", "pseudo_ref_only_commits"),
    "fable-5-fetch-head-only": ("keep", "pseudo_ref_only_commits"),
    "fable-5-pseudo-refs-in-ref": ("candidate", "eligible"),
    "fable-5-fetch-head-unreadable": ("keep", "pseudo_ref_unreadable"),
    "fable-5-merge-autostash": ("keep", "pseudo_ref_only_commits"),
    "fable-5-fetch-head-many": ("candidate", "eligible"),
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


def _gitdir(cwd: Path) -> Path:
    return Path(_git(cwd, "rev-parse", "--absolute-git-dir").strip())


def _loose_commit(cwd: Path, message: str) -> str:
    """A commit on top of HEAD that no ref and no reflog holds (HEAD does not move)."""
    return _git(cwd, "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", message).strip()


def _many_commits(repo: Path, branch: str, count: int) -> list[str]:
    """COUNT commits on a new BRANCH from main, made by one git fast-import process; newest first."""
    stream = "".join(f"commit refs/heads/{branch}\n"
                     f"committer wd-test <wd-test@example.invalid> {1700000000 + n} +0000\n"
                     f"data <<END\nmany {n}\nEND\n" + ("from main\n" if n == 0 else "") + "\n"
                     for n in range(count))
    subprocess.run(["git", "fast-import", "--quiet"], cwd=repo, input=stream.encode("ascii"), capture_output=True,
                   check=True)
    return _git(repo, "rev-list", f"--max-count={count}", branch).split()


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
    reflog_only = detached("fable-5-reflog-only")
    _commit(reflog_only, "d.txt", "d\n")
    _git(reflog_only, "checkout", "-q", "--detach", "HEAD~1")
    assume = detached("fable-5-assume-unchanged")
    _git(assume, "update-index", "--assume-unchanged", "a.txt")
    (assume / "a.txt").write_text("hidden change\n", encoding="utf-8")
    skip = detached("fable-5-skip-worktree")
    _git(skip, "update-index", "--skip-worktree", "a.txt")
    (skip / "a.txt").write_text("hidden change\n", encoding="utf-8")
    _git(detached("fable-5-worktree-ref"), "update-ref", "refs/worktree/keep", "HEAD")
    rebase = detached("fable-5-rebase-in-progress")
    rebase_dir = Path(_git(rebase, "rev-parse", "--absolute-git-dir").strip()) / "rebase-merge"
    rebase_dir.mkdir()
    (rebase_dir / "git-rebase-todo").write_text("break\n", encoding="utf-8")
    merge = detached("fable-5-merge-in-progress")
    merge_head = _git(merge, "rev-parse", "HEAD").strip()
    (Path(_git(merge, "rev-parse", "--absolute-git-dir").strip()) / "MERGE_HEAD").write_text(merge_head + "\n",
                                                                                            encoding="utf-8")
    # ORIG_HEAD and FETCH_HEAD are per-worktree files, deleted with the worktree. Each names a commit made
    # with commit-tree, so it is in no ref and not in the HEAD reflog either.
    orig_only = detached("fable-5-orig-head-only")
    _gitdir(orig_only).joinpath("ORIG_HEAD").write_text(_loose_commit(orig_only, "orig only") + "\n", encoding="utf-8")
    fetch_only = detached("fable-5-fetch-head-only")
    head = _git(fetch_only, "rev-parse", "HEAD").strip()
    _gitdir(fetch_only).joinpath("FETCH_HEAD").write_text(
        f"{head}\tnot-for-merge\tbranch 'main' of ../repo\n"
        f"{_loose_commit(fetch_only, 'fetched only')}\t\tbranch 'topic' of ../repo\n", encoding="utf-8")
    in_ref = detached("fable-5-pseudo-refs-in-ref")
    head = _git(in_ref, "rev-parse", "HEAD").strip()
    _gitdir(in_ref).joinpath("ORIG_HEAD").write_text(head + "\n", encoding="utf-8")
    _gitdir(in_ref).joinpath("FETCH_HEAD").write_text(f"{head}\t\tbranch 'main' of ../repo\n", encoding="utf-8")
    _gitdir(detached("fable-5-fetch-head-unreadable")).joinpath("FETCH_HEAD").write_text("not a fetch head\n",
                                                                                          encoding="utf-8")
    # git merge --autostash records its stash commit (in no ref) in the per-worktree MERGE_AUTOSTASH file.
    autostash = detached("fable-5-merge-autostash")
    _gitdir(autostash).joinpath("MERGE_AUTOSTASH").write_text(_loose_commit(autostash, "autostash") + "\n",
                                                               encoding="utf-8")
    # A plain fetch from a remote with many branches lists every tip (34 of 140 real FETCH_HEADs had over 400
    # lines on 2026-10-12). All of them are in a branch here, so the worktree stays eligible.
    many = detached("fable-5-fetch-head-many")
    tips = _many_commits(repo, "wd-test-many", 401)
    assert len(set(tips)) == 401
    _gitdir(many).joinpath("FETCH_HEAD").write_text(
        "".join(f"{sha}\tnot-for-merge\tbranch 'b{n}' of ../repo\n" for n, sha in enumerate(tips)), encoding="utf-8")
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
    reflog_commit = _git(lane.paths["fable-5-reflog-only"], "rev-parse", "HEAD@{1}").strip()
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
    for sha in (unreachable_head, stash_head, reflog_commit):
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


@pytest.mark.parametrize("value", [None, "", "fable 5", "../fable-5", "Fable-5", "scout", "fable-5-x", "fable-5x"])
def test_a_missing_or_invalid_lane_identity_is_refused(lane, capsys, monkeypatch, value):
    if value is None:
        monkeypatch.delenv("AGENT_BRIDGE_AGENT", raising=False)
    else:
        monkeypatch.setenv("AGENT_BRIDGE_AGENT", value)
    # Without --owner the lane identity alone decides; with it, a mismatch must not be what refuses.
    assert _prune().main(["--repo", str(lane.repo), "--apply"]) == 2
    assert _prune().main(["--repo", str(lane.repo), "--owner", OWNER, "--apply"]) == 2
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


def _dir_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, check=True)
    else:
        os.symlink(target, link, target_is_directory=True)


def _remove_link(link: Path) -> None:
    if os.name == "nt":
        os.rmdir(link)  # the junction only, never its target
    else:
        link.unlink()


def test_current_worktree_protection_compares_file_identity_not_spelling(lane, tmp_path):
    prune = _prune()
    target = lane.paths["fable-5-clean"]
    link = tmp_path / "link-to-clean"
    _dir_link(link, target)
    try:
        entry = {"path": str(target).replace("\\", "/"), "head": _git(target, "rev-parse", "HEAD").strip(),
                 "branch": None, "detached": True, "bare": False, "locked": False, "prunable": False}
        decision = prune.classify(entry, index=1, owner=OWNER, protected=(str(link),), repo=str(lane.repo))
        assert decision == ("keep", "current_worktree")
        assert prune.classify(entry, index=1, owner=OWNER, protected=(), repo=str(lane.repo)) == ("candidate", "eligible")
    finally:
        _remove_link(link)


def _late_change(path: Path, change: str) -> None:
    if change == "evidence":  # ignored evidence written after the checks
        (path / ".codex-audit").mkdir()
        (path / ".codex-audit" / "late.json").write_bytes(b'{"native_exit": 0}')
    elif change == "commit":  # a new commit on the detached HEAD: status stays clean and HEAD is in no ref
        _commit(path, "late.txt", "late\n")
    elif change == "moved":  # HEAD moves to a new commit a branch holds: nothing would be lost, but it changed
        _commit(path, "late.txt", "late\n")
        _git(path, "branch", "wd-test-late", "HEAD")
    elif change == "orig-head":  # ORIG_HEAD names a commit in no ref: HEAD and status do not change
        _gitdir(path).joinpath("ORIG_HEAD").write_text(_loose_commit(path, "late orig") + "\n", encoding="utf-8")
    else:  # a change hidden by an assume-unchanged index flag: status stays clean
        _git(path, "update-index", "--assume-unchanged", "a.txt")
        (path / "a.txt").write_text("hidden change\n", encoding="utf-8")


@pytest.mark.parametrize("change", ["evidence", "commit", "moved", "index-flag", "orig-head"])
def test_a_change_after_classification_is_caught_before_removal(lane, capsys, monkeypatch, change):
    """RCO1 R2 on PR1859: --apply must re-run every check right before removal, not only the status."""
    prune = _prune()
    target = lane.paths["fable-5-clean"]
    real_classify = prune.classify
    changed = []

    def classify_then_change(entry, **kwargs):
        result = real_classify(entry, **kwargs)
        if Path(entry["path"]).name == "fable-5-clean" and not changed:
            assert result == ("candidate", "eligible")
            _late_change(target, change)  # the worktree changes after its checks passed
            changed.append(_git(target, "rev-parse", "HEAD").strip())
        return result

    monkeypatch.setattr(prune, "classify", classify_then_change)
    code = prune.main(["--repo", str(lane.repo), "--apply", "--json"])
    report = json.loads(capsys.readouterr().out)
    entry = _by_name(report)["fable-5-clean"]
    assert changed and (entry["decision"], entry["reason"]) == ("keep", "changed_before_removal")
    assert target.is_dir() and _git(target, "rev-parse", "HEAD").strip() == changed[0]
    assert code == 0


def test_human_output_names_each_decision(lane, capsys):
    assert _prune().main(["--repo", str(lane.repo)]) == 0
    out = capsys.readouterr().out
    assert "CANDIDATE" in out and "fable-5-clean" in out
    assert "KEEP" in out and "ignored_content" in out and "fable-5-evidence" in out
    assert "dry run" in out.lower()


# --- e30166d0 mutant survivors: an unreadable or odd git answer must keep the worktree -----------


@pytest.mark.parametrize("answer", [(128, "", ""), (0, "", ""), (0, "two\n", ""), (0, "-1\n", "")])
def test_an_unreadable_outside_refs_count_is_unknown_not_clean(monkeypatch, answer):
    """Survivor MUT_PR_OUTSIDE_REFS_FAILS_OPEN: a failed or non-numeric rev-list count is unknown (keep),
    never "every commit is in a ref" (which would let the worktree be removed)."""
    prune = _prune()
    monkeypatch.setattr(prune, "_git", lambda args, cwd=None, stdin=None: answer)

    assert prune._outside_refs("repo", {"a" * 40}) is None


@pytest.mark.parametrize("line", ["--all", "not-a-sha", "a" * 39, "A" * 40])
def test_a_reflog_line_that_is_not_an_object_name_is_unknown_and_never_reaches_rev_list(monkeypatch, line):
    """Survivor MUT_PR_REFLOG_SHA_UNCHECKED: rev-list --stdin also reads pseudo-options such as --all, so an
    unvalidated reflog line could change the count; it must make the answer unknown before rev-list runs."""
    prune = _prune()
    calls = []

    def fake_git(args, cwd=None, stdin=None):
        calls.append(list(args))
        if "log" in args:
            return 0, f"{'b' * 40}\n{line}\n", ""
        return 0, "0\n", ""  # rev-list would say "no commit outside the refs"

    monkeypatch.setattr(prune, "_git", fake_git)

    assert prune._reflog_only_commits("worktree", "repo") is None
    assert not any("rev-list" in call for call in calls)
