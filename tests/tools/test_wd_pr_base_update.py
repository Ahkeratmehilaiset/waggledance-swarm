# SPDX-License-Identifier: BUSL-1.1
"""tools/wd_pr_base_update.py: update a PR branch onto a newer base without changing its content.

Round 5 B2 (2026-10-12): tree = ``git merge-tree --write-tree --merge-base=<old base> <new base>
<old head>`` (exit 0 required); accept only if the PR's per-file delta (path, status, modes, blob
ids) on the new base equals its delta on the old base, no PR path is in the base delta, and the
stable patch-ids are equal; then ``git commit-tree <tree> -p <old head> -p <new base>``, so the
reviewed head stays the first parent and a push is a fast-forward. ``--push`` pushes without force
and only when the remote branch is still at the reviewed head. Every run writes a receipt, also
when it refuses. The tool is (b)-class and unwired: nothing in the repository calls it.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "wd_pr_base_update.py"


def _tool():
    spec = importlib.util.spec_from_file_location("wd_pr_base_update_under_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          check=True).stdout


def _write(repo: Path, name: str, text: str) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _commit(repo: Path, message: str, **files: str | None) -> str:
    for name, text in files.items():
        name = name.replace("__", "/")
        if text is None:
            _git(repo, "rm", "-q", "--", name)
        else:
            _write(repo, name, text)
            _git(repo, "add", "--", name)
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").strip()


LINES = "".join(f"line {n}\n" for n in range(1, 21))


@pytest.fixture
def git_env(tmp_path, monkeypatch):
    """Isolate git from the caller's global and system configuration (signing, renames, hooks)."""
    empty = tmp_path / "empty.gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "wd-test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "wd-test@example.invalid")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def repo(tmp_path, git_env):
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "core.autocrlf", "false")
    return path


def _base(repo: Path) -> str:
    return _commit(repo, "base", **{"a.txt": LINES, "b.txt": "b\n", "dir__one.txt": "one\n", "dir__two.txt": "two\n"})


def _pr(repo: Path, base: str, **files: str | None) -> str:
    _git(repo, "checkout", "-q", "-b", "pr", base)
    head = _commit(repo, "pr change", **files)
    _git(repo, "checkout", "-q", "main")
    return head


def _run(capsys, *args: str) -> tuple[int, dict | None]:
    code = _tool().main(list(args))
    out = capsys.readouterr().out
    return code, (json.loads(out) if out.strip() else None)


def _args(repo: Path, receipt: Path, old_head: str, old_base: str, new_base: str, *extra: str) -> list[str]:
    return ["--repo", str(repo), "--old-head", old_head, "--old-base", old_base, "--new-base", new_base,
            "--receipt", str(receipt), "--json", *extra]


def _receipt(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _refs(repo: Path) -> str:
    return _git(repo, "for-each-ref", "--format=%(refname) %(objectname)")


def test_clean_update_is_a_fast_forward_merge_with_identical_content(repo, tmp_path, capsys):
    base = _base(repo)
    head = _pr(repo, base, **{"a.txt": LINES.replace("line 3\n", "line three\n"), "p.txt": "pr\n",
                              "b.txt": None})
    new_base = _commit(repo, "main moves", **{"c.txt": "c\n", "dir__one.txt": "ONE\n"})
    refs_before = _refs(repo)
    receipt_path = tmp_path / "receipt.json"

    code, printed = _run(capsys, *_args(repo, receipt_path, head, base, "main"))

    assert code == 0, printed
    receipt = _receipt(receipt_path)
    assert printed == receipt
    assert receipt["schema"] == "wd.pr-base-update-receipt.v1"
    assert receipt["status"] == "updated" and receipt["reason"] == "content_identical"
    assert (receipt["old_head"], receipt["old_base"], receipt["new_base"]) == (head, base, new_base)
    assert receipt["new_base_input"] == "main"
    new_head = receipt["new_head"]
    assert _git(repo, "rev-list", "--parents", "-n", "1", new_head).split() == [new_head, head, new_base]
    assert _git(repo, "rev-parse", f"{new_head}^{{tree}}").strip() == receipt["tree"]
    expected_tree = _git(repo, "merge-tree", "--write-tree", f"--merge-base={base}", new_base, head).split()[0]
    assert receipt["tree"] == expected_tree
    assert sorted(_git(repo, "diff", "--name-only", new_base, new_head).split()) == ["a.txt", "b.txt", "p.txt"]
    assert receipt["pr_paths"] == ["a.txt", "b.txt", "p.txt"]
    assert receipt["overlap_paths"] == []
    assert receipt["base_delta_path_count"] == 2
    for entry in receipt["entries"]:
        assert entry["old"] == entry["new"], entry
        oid = entry["new"]["new_oid"]
        if entry["path"] == "b.txt":
            assert entry["new"]["status"] == "D" and set(oid) == {"0"}
        else:
            assert oid == _git(repo, "rev-parse", f"{head}:{entry['path']}").strip()
    pid = subprocess.run(["git", "patch-id", "--stable"], cwd=repo, capture_output=True, text=True, check=True,
                         input=_git(repo, "diff", base, head)).stdout.split()[0]
    assert receipt["patch_id_old"] == receipt["patch_id_new"] == pid
    assert receipt["push"] is None
    assert receipt["tool_sha256"] == hashlib.sha256(TOOL.read_bytes()).hexdigest()
    # nothing but the new commit object: no branch, tag or remote ref moved
    assert _refs(repo) == refs_before


@pytest.mark.parametrize("case", ["conflict", "overlap_clean_merge", "same_change_both_sides", "union_merge"])
def test_any_pr_path_touched_by_the_base_is_refused(repo, tmp_path, capsys, case):
    base = _base(repo)
    head = _pr(repo, base, **{"a.txt": LINES.replace("line 3\n", "line three\n")})
    if case == "conflict":
        _commit(repo, "main", **{"a.txt": LINES.replace("line 3\n", "line THREE\n")})
    elif case == "overlap_clean_merge":
        _commit(repo, "main", **{"a.txt": LINES.replace("line 18\n", "line eighteen\n")})
    elif case == "same_change_both_sides":
        _commit(repo, "main", **{"a.txt": LINES.replace("line 3\n", "line three\n")})
    else:
        _write(repo, ".gitattributes", "a.txt merge=union\n")
        _git(repo, "add", ".gitattributes")
        _commit(repo, "main", **{"a.txt": LINES.replace("line 3\n", "line 3b\n")})
    refs_before = _refs(repo)
    receipt_path = tmp_path / "receipt.json"

    code, printed = _run(capsys, *_args(repo, receipt_path, head, base, "main"))

    assert code == 3
    receipt = _receipt(receipt_path)
    assert printed == receipt
    assert receipt["status"] == "refused"
    assert receipt["reason"] == ("merge_conflict" if case == "conflict" else "paths_overlap")
    assert receipt["new_head"] is None
    if case != "conflict":
        assert receipt["overlap_paths"] == ["a.txt"]
    assert _refs(repo) == refs_before


def test_a_base_directory_rename_that_moves_a_pr_file_is_refused(repo, tmp_path, capsys):
    """merge-ort follows directory renames: the PR's new dir/new.txt would land at newdir/new.txt.
    The PR paths do not overlap the base delta, so only the per-file delta check can see it."""
    _git(repo, "config", "merge.directoryRenames", "true")
    base = _base(repo)
    head = _pr(repo, base, **{"dir__new.txt": "new\n"})
    _git(repo, "mv", "dir", "newdir")
    _git(repo, "commit", "-q", "-m", "rename dir")
    receipt_path = tmp_path / "receipt.json"

    code, _ = _run(capsys, *_args(repo, receipt_path, head, base, "main"))

    receipt = _receipt(receipt_path)
    assert (code, receipt["status"], receipt["reason"]) == (3, "refused", "delta_changed")
    assert receipt["overlap_paths"] == []
    assert receipt["new_head"] is None


def test_patch_id_mismatch_is_refused(repo, tmp_path, capsys, monkeypatch):
    base = _base(repo)
    head = _pr(repo, base, **{"p.txt": "pr\n"})
    _commit(repo, "main", **{"c.txt": "c\n"})
    tool = _tool()
    real = tool._patch_id
    monkeypatch.setattr(tool, "_patch_id", lambda repo_, a, b: real(repo_, a, b) + ("x" if a == base else ""))
    receipt_path = tmp_path / "receipt.json"

    code = tool.main(_args(repo, receipt_path, head, base, "main"))

    receipt = _receipt(receipt_path)
    assert (code, receipt["status"], receipt["reason"]) == (3, "refused", "patch_id_changed")
    assert receipt["new_head"] is None


@pytest.mark.parametrize("case,reason", [
    ("old_base_too_old", "old_base_not_merge_base"),
    ("old_base_not_ancestor", "old_base_not_ancestor_of_head"),
    ("new_base_unrelated", "new_base_not_descendant_of_old_base"),
    ("already_current", "already_contains_new_base"),
    ("empty_pr", "empty_pr_delta")])
def test_wrong_or_meaningless_bases_are_refused(repo, tmp_path, capsys, case, reason):
    first = _commit(repo, "first", **{"z.txt": "z\n"})
    base = _base(repo)
    if case == "empty_pr":
        _git(repo, "checkout", "-q", "-b", "pr", base)
        _git(repo, "commit", "-q", "--allow-empty", "-m", "empty")
        head = _git(repo, "rev-parse", "HEAD").strip()
        _git(repo, "checkout", "-q", "main")
    else:
        head = _pr(repo, base, **{"p.txt": "pr\n"})
    new_base = _commit(repo, "main", **{"c.txt": "c\n"})
    old_base = base
    if case == "old_base_too_old":
        old_base = first
    elif case == "old_base_not_ancestor":
        old_base = new_base
        new_base = _commit(repo, "main again", **{"d.txt": "d\n"})
    elif case == "new_base_unrelated":
        _git(repo, "checkout", "-q", "--orphan", "other")
        _git(repo, "rm", "-rq", "--cached", ".")
        new_base = _commit(repo, "unrelated", **{"u.txt": "u\n"})
        _git(repo, "checkout", "-q", "-f", "main")
    elif case == "already_current":
        new_base = base
    receipt_path = tmp_path / "receipt.json"

    code, _ = _run(capsys, *_args(repo, receipt_path, head, old_base, new_base))

    receipt = _receipt(receipt_path)
    assert (code, receipt["status"], receipt["reason"]) == (3, "refused", reason)
    assert receipt["new_head"] is None


@pytest.mark.parametrize("which", ["--old-head", "--old-base"])
def test_the_reviewed_commits_must_be_named_by_full_object_name(repo, tmp_path, capsys, which):
    base = _base(repo)
    head = _pr(repo, base, **{"p.txt": "pr\n"})
    _commit(repo, "main", **{"c.txt": "c\n"})
    args = _args(repo, tmp_path / "receipt.json", head, base, "main")
    args[args.index(which) + 1] = args[args.index(which) + 1][:12]

    assert _tool().main(args) == 2
    assert not (tmp_path / "receipt.json").exists()


def test_an_existing_receipt_is_never_overwritten(repo, tmp_path, capsys):
    base = _base(repo)
    head = _pr(repo, base, **{"p.txt": "pr\n"})
    _commit(repo, "main", **{"c.txt": "c\n"})
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_bytes(b"earlier evidence")
    refs_before = _refs(repo)

    assert _tool().main(_args(repo, receipt_path, head, base, "main")) == 2
    assert receipt_path.read_bytes() == b"earlier evidence"
    assert _refs(repo) == refs_before


def test_binary_mode_and_special_path_changes_are_carried_exactly(repo, tmp_path, capsys):
    base = _base(repo)
    _git(repo, "checkout", "-q", "-b", "pr", base)
    (repo / "bin.dat").write_bytes(bytes(range(256)) * 4)
    _write(repo, "space dir/näme.txt", "special\n")
    _git(repo, "add", "bin.dat", "space dir/näme.txt")
    _git(repo, "update-index", "--chmod=+x", "b.txt")
    _git(repo, "commit", "-q", "-m", "binary, mode, special path")
    head = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-q", "main")
    new_base = _commit(repo, "main", **{"c.txt": "c\n"})
    receipt_path = tmp_path / "receipt.json"

    code, _ = _run(capsys, *_args(repo, receipt_path, head, base, new_base))

    receipt = _receipt(receipt_path)
    assert (code, receipt["status"]) == (0, "updated"), receipt
    assert receipt["pr_paths"] == ["b.txt", "bin.dat", "space dir/näme.txt"]
    modes = {entry["path"]: (entry["new"]["old_mode"], entry["new"]["new_mode"]) for entry in receipt["entries"]}
    assert modes["b.txt"] == ("100644", "100755")
    new_head = receipt["new_head"]
    for path in receipt["pr_paths"]:
        assert _git(repo, "rev-parse", f"{new_head}:{path}") == _git(repo, "rev-parse", f"{head}:{path}")
    assert _git(repo, "ls-tree", new_head, "b.txt").split()[0] == "100755"
    # receipt keys stay ASCII even for a non-ASCII path (bridge readers reject non-ASCII keys)
    keys = []
    json.loads(receipt_path.read_text(encoding="utf-8"), object_pairs_hook=lambda pairs: keys.extend(k for k, _ in pairs))
    assert all(key.isascii() for key in keys)


@pytest.fixture
def remote(repo, tmp_path):
    bare = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(bare))
    _git(repo, "remote", "add", "origin", str(bare))
    return bare


def test_push_is_a_verified_fast_forward_of_the_reviewed_head(repo, remote, tmp_path, capsys):
    base = _base(repo)
    head = _pr(repo, base, **{"p.txt": "pr\n"})
    _git(repo, "push", "-q", "origin", f"{head}:refs/heads/fable-5/topic")
    _commit(repo, "main", **{"c.txt": "c\n"})
    receipt_path = tmp_path / "receipt.json"

    code, _ = _run(capsys, *_args(repo, receipt_path, head, base, "main", "--push", "--remote", "origin",
                                  "--branch", "fable-5/topic", "--verify-interval", "0.05", "--verify-timeout", "5"))

    receipt = _receipt(receipt_path)
    assert (code, receipt["status"]) == (0, "pushed"), receipt
    new_head = receipt["new_head"]
    assert _git(remote, "rev-parse", "refs/heads/fable-5/topic").strip() == new_head
    assert receipt["push"]["remote_tip_before"] == head
    assert receipt["push"]["remote_tip_after"] == new_head
    assert receipt["push"]["verified"] is True
    assert receipt["push"]["argv"] == ["push", "origin", f"{new_head}:refs/heads/fable-5/topic"]


@pytest.mark.parametrize("case", ["moved", "missing"])
def test_push_is_refused_unless_the_remote_branch_is_still_the_reviewed_head(repo, remote, tmp_path, capsys, case):
    base = _base(repo)
    head = _pr(repo, base, **{"p.txt": "pr\n"})
    if case == "moved":
        _git(repo, "checkout", "-q", "pr")
        moved = _commit(repo, "someone pushed", **{"q.txt": "q\n"})
        _git(repo, "checkout", "-q", "main")
        _git(repo, "push", "-q", "origin", f"{moved}:refs/heads/fable-5/topic")
    _commit(repo, "main", **{"c.txt": "c\n"})
    before = _git(remote, "for-each-ref", "--format=%(refname) %(objectname)")
    receipt_path = tmp_path / "receipt.json"

    code, _ = _run(capsys, *_args(repo, receipt_path, head, base, "main", "--push", "--remote", "origin",
                                  "--branch", "fable-5/topic", "--verify-interval", "0.05", "--verify-timeout", "1"))

    receipt = _receipt(receipt_path)
    assert (code, receipt["status"], receipt["reason"]) == (3, "refused", "remote_tip_mismatch")
    assert _git(remote, "for-each-ref", "--format=%(refname) %(objectname)") == before
    assert receipt["new_head"] is not None  # the local commit exists; nothing was pushed


def test_a_push_the_remote_does_not_show_is_unverified_and_not_retried(repo, remote, tmp_path, capsys):
    base = _base(repo)
    head = _pr(repo, base, **{"p.txt": "pr\n"})
    _git(repo, "push", "-q", "origin", f"{head}:refs/heads/fable-5/topic")
    attempts = tmp_path / "attempts.log"
    hook = remote / "hooks" / "pre-receive"
    hook.write_text(f"#!/bin/sh\necho attempt >> '{attempts.as_posix()}'\nexit 1\n", encoding="utf-8", newline="\n")
    hook.chmod(0o755)
    _commit(repo, "main", **{"c.txt": "c\n"})
    receipt_path = tmp_path / "receipt.json"

    code, _ = _run(capsys, *_args(repo, receipt_path, head, base, "main", "--push", "--remote", "origin",
                                  "--branch", "fable-5/topic", "--verify-interval", "0.05", "--verify-timeout", "0.3"))

    receipt = _receipt(receipt_path)
    assert (code, receipt["status"], receipt["reason"]) == (4, "push_unverified", "remote_not_at_new_head")
    assert receipt["push"]["push_exit"] != 0
    assert (receipt["push"]["verified"], receipt["push"]["remote_tip_after"]) == (False, head)
    assert receipt["push"]["waited_seconds"] >= 0.3
    assert _git(remote, "rev-parse", "refs/heads/fable-5/topic").strip() == head
    assert attempts.read_text(encoding="utf-8").splitlines() == ["attempt"]


def test_push_argv_never_forces():
    tool = _tool()
    argv = tool._push_argv("origin", "a" * 40, "fable-5/topic")
    assert argv == ["push", "origin", "a" * 40 + ":refs/heads/fable-5/topic"]
    source = TOOL.read_text(encoding="utf-8")
    for forbidden in ("--force", "force-with-lease", "--mirror", "+refs/", '"-f"', "'-f'"):
        assert forbidden not in source, forbidden


@pytest.mark.parametrize("branch", ["+fable-5/topic", "fable-5/topic:main", "-f", "fable-5/../x", ""])
def test_unsafe_branch_names_are_refused_before_anything_runs(repo, remote, tmp_path, capsys, branch):
    base = _base(repo)
    head = _pr(repo, base, **{"p.txt": "pr\n"})
    _commit(repo, "main", **{"c.txt": "c\n"})

    code = _tool().main(_args(repo, tmp_path / "receipt.json", head, base, "main", "--push", "--remote", "origin",
                              "--branch", branch))

    assert code == 2
    assert not (tmp_path / "receipt.json").exists()


def test_push_options_require_push(repo, tmp_path, capsys):
    base = _base(repo)
    head = _pr(repo, base, **{"p.txt": "pr\n"})
    _commit(repo, "main", **{"c.txt": "c\n"})
    assert _tool().main(_args(repo, tmp_path / "r.json", head, base, "main", "--branch", "fable-5/topic")) == 2
    assert _tool().main(_args(repo, tmp_path / "r.json", head, base, "main", "--push")) == 2


def test_the_tool_is_not_wired_into_any_runtime_path():
    """(b)-class while unwired: wiring it into the gate or a scheduled path makes it (a)-class."""
    listed = subprocess.run(["git", "grep", "-l", "-I", "wd_pr_base_update", "--", "*.py", "*.ps1", "*.psm1",
                             "*.yml", "*.yaml", "*.json", "*.cmd", "*.bat", "*.sh"],
                            cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert listed.returncode in (0, 1), listed.stderr
    users = sorted(set(listed.stdout.split()) - {"tools/wd_pr_base_update.py", "tests/tools/test_wd_pr_base_update.py"})
    assert users == []
