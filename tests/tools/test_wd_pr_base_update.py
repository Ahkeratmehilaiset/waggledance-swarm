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
    os.chmod(repo / "b.txt", 0o755)  # Codex P1 4245644000: with core.filemode=true the worktree must match
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
    assert receipt["push"]["argv"] == ["push", "--no-follow-tags", "--recurse-submodules=no", "origin",
                                       f"{new_head}:refs/heads/fable-5/topic"]
    assert receipt["push"]["ls_remote_before"] == [f"{head}\trefs/heads/fable-5/topic"]
    assert receipt["push"]["ls_remote_after"] == [f"{new_head}\trefs/heads/fable-5/topic"]
    assert receipt["push"]["push_url_matches_fetch_url"] is True


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
    assert argv == ["push", "--no-follow-tags", "--recurse-submodules=no", "origin", "a" * 40 + ":refs/heads/fable-5/topic"]
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


# --- Grok self-challenge on ee016336 (report 70547a2ed1024a3183da70d0751e5f65) ---

def test_replace_refs_do_not_change_what_is_carried(repo, tmp_path, capsys):
    """Q1: with refs/replace, diff-tree and merge-tree would read a replacement of the reviewed head while
    commit-tree still names the real one. Every git call runs with replacement objects disabled."""
    base = _base(repo)
    head = _pr(repo, base, **{"p.txt": "reviewed\n"})
    _git(repo, "checkout", "-q", "--detach", base)
    _write(repo, "p.txt", "not reviewed\n")
    _git(repo, "add", "p.txt")
    other = _git(repo, "commit-tree", _git(repo, "write-tree").strip(), "-p", base, "-m", "pr change").strip()
    _git(repo, "checkout", "-q", "-f", "main")
    _git(repo, "replace", head, other)
    _commit(repo, "main", **{"c.txt": "c\n"})
    receipt_path = tmp_path / "receipt.json"

    code, _ = _run(capsys, *_args(repo, receipt_path, head, base, "main"))

    receipt = _receipt(receipt_path)
    assert (code, receipt["status"]) == (0, "updated"), receipt
    assert _git(repo, "--no-replace-objects", "show", f"{receipt['new_head']}:p.txt") == "reviewed\n"
    assert receipt["replace_refs"] == 1


def test_grafts_are_refused(repo, tmp_path, capsys):
    base = _base(repo)
    head = _pr(repo, base, **{"p.txt": "pr\n"})
    _commit(repo, "main", **{"c.txt": "c\n"})
    grafts = Path(_git(repo, "rev-parse", "--git-path", "info/grafts").strip())
    if not grafts.is_absolute():
        grafts = repo / grafts
    grafts.parent.mkdir(parents=True, exist_ok=True)
    grafts.write_text(f"{head} {base}\n", encoding="ascii")
    receipt_path = tmp_path / "receipt.json"

    code, _ = _run(capsys, *_args(repo, receipt_path, head, base, "main"))

    receipt = _receipt(receipt_path)
    assert (code, receipt["status"], receipt["reason"]) == (3, "refused", "grafts_present")
    assert receipt["new_head"] is None


@pytest.mark.parametrize("flag,value", [("--verify-interval", "nan"), ("--verify-interval", "inf"),
                                        ("--verify-timeout", "nan"), ("--verify-timeout", "inf")])
def test_non_finite_verify_values_are_usage_errors(repo, remote, tmp_path, capsys, flag, value):
    """Q2: nan passed `<= 0` and time.sleep(nan) raised after the push had already run."""
    base = _base(repo)
    head = _pr(repo, base, **{"p.txt": "pr\n"})
    _git(repo, "push", "-q", "origin", f"{head}:refs/heads/fable-5/topic")
    _commit(repo, "main", **{"c.txt": "c\n"})
    args = _args(repo, tmp_path / "receipt.json", head, base, "main", "--push", "--remote", "origin",
                 "--branch", "fable-5/topic", flag, value)

    assert _tool().main(args) == 2
    assert not (tmp_path / "receipt.json").exists()
    assert _git(remote, "rev-parse", "refs/heads/fable-5/topic").strip() == head


def test_an_unexpected_error_still_writes_a_refusal_receipt(repo, tmp_path, capsys, monkeypatch):
    """Q2: any exception, not only a refusal, ends in a receipt and exit 3."""
    base = _base(repo)
    head = _pr(repo, base, **{"p.txt": "pr\n"})
    _commit(repo, "main", **{"c.txt": "c\n"})
    tool = _tool()

    def broken(*_args):
        raise RuntimeError("boom")

    monkeypatch.setattr(tool, "_delta", broken)
    receipt_path = tmp_path / "receipt.json"

    code = tool.main(_args(repo, receipt_path, head, base, "main"))

    receipt = _receipt(receipt_path)
    assert (code, receipt["status"], receipt["reason"]) == (3, "refused", "internal_error")
    assert receipt["error_type"] == "RuntimeError"
    assert receipt["new_head"] is None


def test_a_failing_git_command_is_named_in_the_receipt(repo, tmp_path, capsys, monkeypatch):
    """Q5: git_failed names the command and its exit code (no stderr: it can carry a remote URL)."""
    base = _base(repo)
    head = _pr(repo, base, **{"p.txt": "pr\n"})
    _commit(repo, "main", **{"c.txt": "c\n"})
    tool = _tool()
    real = tool._git

    def failing(repo_, args, stdin=None):
        return (128, b"") if args[0] == "diff-tree" else real(repo_, args, stdin)

    monkeypatch.setattr(tool, "_git", failing)
    receipt_path = tmp_path / "receipt.json"

    code = tool.main(_args(repo, receipt_path, head, base, "main"))

    receipt = _receipt(receipt_path)
    assert (code, receipt["reason"]) == (3, "git_failed")
    assert receipt["failed_command"]["argv"][:2] == ["diff-tree", "-r"]
    assert receipt["failed_command"]["exit"] == 128


@pytest.mark.parametrize("how", ["pushurl", "pushinsteadof"])
def test_a_push_url_other_than_the_checked_fetch_url_is_refused(repo, remote, tmp_path, capsys, how):
    """Q3: ls-remote reads the fetch URL; git push would write the push URL."""
    other = tmp_path / "other.git"
    _git(tmp_path, "init", "-q", "--bare", str(other))
    base = _base(repo)
    head = _pr(repo, base, **{"p.txt": "pr\n"})
    for bare in (remote, other):
        _git(repo, "push", "-q", str(bare), f"{head}:refs/heads/fable-5/topic")
    if how == "pushurl":
        _git(repo, "remote", "set-url", "--push", "origin", str(other))
    else:
        _git(repo, "config", f"url.{other.as_posix()}.pushInsteadOf", remote.as_posix())
        _git(repo, "remote", "set-url", "origin", remote.as_posix())
    _commit(repo, "main", **{"c.txt": "c\n"})
    receipt_path = tmp_path / "receipt.json"

    code, _ = _run(capsys, *_args(repo, receipt_path, head, base, "main", "--push", "--remote", "origin",
                                  "--branch", "fable-5/topic", "--verify-interval", "0.05", "--verify-timeout", "0.3"))

    receipt = _receipt(receipt_path)
    assert (code, receipt["status"], receipt["reason"]) == (3, "refused", "push_url_differs")
    assert receipt["push"]["push_url_matches_fetch_url"] is False
    for bare in (remote, other):
        assert _git(bare, "rev-parse", "refs/heads/fable-5/topic").strip() == head
    assert "other.git" not in receipt_path.read_text(encoding="utf-8")


def test_push_does_not_follow_tags(repo, remote, tmp_path, capsys):
    """Q3: push.followTags=true would also push annotated tags reachable from the new head."""
    base = _base(repo)
    head = _pr(repo, base, **{"p.txt": "pr\n"})
    _git(repo, "push", "-q", "origin", f"{head}:refs/heads/fable-5/topic")
    _git(repo, "tag", "-a", "wd-test-tag", "-m", "tag", head)
    _git(repo, "config", "push.followTags", "true")
    _commit(repo, "main", **{"c.txt": "c\n"})
    receipt_path = tmp_path / "receipt.json"

    code, _ = _run(capsys, *_args(repo, receipt_path, head, base, "main", "--push", "--remote", "origin",
                                  "--branch", "fable-5/topic", "--verify-interval", "0.05", "--verify-timeout", "5"))

    assert (code, _receipt(receipt_path)["status"]) == (0, "pushed")
    assert _git(remote, "for-each-ref", "--format=%(refname)", "refs/tags") == ""


@pytest.mark.parametrize("name", ["https://user:secret@example.invalid/repo.git", "remote.git", "nosuchremote"])
def test_remote_must_be_a_configured_remote_name(repo, remote, tmp_path, capsys, name):
    """Q3: a URL as --remote could carry a credential into the receipt; only configured names are taken,
    and the rejected value is not echoed."""
    base = _base(repo)
    head = _pr(repo, base, **{"p.txt": "pr\n"})
    _commit(repo, "main", **{"c.txt": "c\n"})
    value = str(remote) if name == "remote.git" else name

    code = _tool().main(_args(repo, tmp_path / "receipt.json", head, base, "main", "--push", "--remote", value,
                              "--branch", "fable-5/topic"))

    captured = capsys.readouterr()
    assert code == 2
    assert not (tmp_path / "receipt.json").exists()
    assert "secret" not in captured.out + captured.err


def test_receipt_records_what_a_consumer_needs_to_rederive_the_result(repo, tmp_path, capsys):
    """Q5: the observed parent line (not the inputs), the base delta paths, and the config merge-tree reads."""
    base = _base(repo)
    head = _pr(repo, base, **{"p.txt": "pr\n"})
    new_base = _commit(repo, "main", **{"c.txt": "c\n", "dir__one.txt": "ONE\n"})
    receipt_path = tmp_path / "receipt.json"

    code, _ = _run(capsys, *_args(repo, receipt_path, head, base, "main"))

    receipt = _receipt(receipt_path)
    assert code == 0
    new_head = receipt["new_head"]
    assert receipt["observed_parents"] == _git(repo, "rev-list", "--parents", "-n", "1", new_head).split()
    assert receipt["observed_parents"] == [new_head, head, new_base]
    assert receipt["base_delta_paths"] == ["c.txt", "dir/one.txt"]
    assert set(receipt["config"]) == {"core.autocrlf", "core.ignorecase", "core.symlinks", "merge.renormalize",
                                      "extensions.objectformat"}
    assert receipt["config"]["core.autocrlf"] == "false"
    assert (receipt["replace_refs"], receipt["grafts"]) == (0, False)


@pytest.mark.parametrize("pr_path,base_path", [("Foo.txt", "foo.txt"), ("dir__Readme.md", "dir__README.md")])
def test_a_case_variant_of_a_pr_path_in_the_base_delta_is_overlap(repo, tmp_path, capsys, pr_path, base_path):
    """RCO2 R1 on 8e117a84: on a case-insensitive checkout Foo.txt and foo.txt are one file, so a base that adds
    a case variant of a PR path touched reviewed content. The overlap check casefolds paths."""
    base = _base(repo)
    head = _pr(repo, base, **{pr_path: "pr\n"})
    _commit(repo, "main", **{base_path: "base\n"})
    receipt_path = tmp_path / "receipt.json"

    code, _ = _run(capsys, *_args(repo, receipt_path, head, base, "main"))

    receipt = _receipt(receipt_path)
    assert (code, receipt["status"], receipt["reason"]) == (3, "refused", "paths_overlap")
    assert receipt["overlap_paths"] == [pr_path.replace("__", "/")]
    assert receipt["new_head"] is None


@pytest.mark.parametrize("pr_files,base_files", [
    ({"Foo": "pr file\n"}, {"foo__x.txt": "base dir\n"}),
    ({"foo__x.txt": "pr dir\n"}, {"Foo": "base file\n"}),
])
def test_a_pr_path_colliding_by_case_with_a_file_or_directory_is_refused(repo, tmp_path, capsys, pr_files, base_files):
    """RCO2 R1 (optional part): a PR file and a base directory (or the reverse) that differ only by case cannot both
    exist in a case-insensitive checkout, although the paths do not overlap."""
    base = _base(repo)
    head = _pr(repo, base, **pr_files)
    _commit(repo, "main", **base_files)
    receipt_path = tmp_path / "receipt.json"

    code, _ = _run(capsys, *_args(repo, receipt_path, head, base, "main"))

    receipt = _receipt(receipt_path)
    assert (code, receipt["status"], receipt["reason"]) == (3, "refused", "case_collision")
    assert receipt["case_collisions"] == sorted(name.replace("__", "/") for name in pr_files)
    assert receipt["new_head"] is None


def test_a_clean_update_records_no_case_collision(repo, tmp_path, capsys):
    base = _base(repo)
    head = _pr(repo, base, **{"p.txt": "pr\n"})
    _commit(repo, "main", **{"c.txt": "c\n"})
    receipt_path = tmp_path / "receipt.json"

    code, _ = _run(capsys, *_args(repo, receipt_path, head, base, "main"))

    receipt = _receipt(receipt_path)
    assert (code, receipt["status"], receipt["case_collisions"]) == (0, "updated", [])


def test_an_unwritable_receipt_still_prints_the_receipt(repo, tmp_path, capsys):
    """RCO2 N1: when the receipt file cannot be written (here after an update; also after a push), the receipt goes
    to stdout so the result is not lost; the exit code stays 2."""
    base = _base(repo)
    head = _pr(repo, base, **{"p.txt": "pr\n"})
    _commit(repo, "main", **{"c.txt": "c\n"})
    receipt_path = tmp_path / "missing-dir" / "receipt.json"

    code = _tool().main(["--repo", str(repo), "--old-head", head, "--old-base", base, "--new-base", "main",
                         "--receipt", str(receipt_path)])

    captured = capsys.readouterr()
    assert code == 2
    assert not receipt_path.exists()
    assert "receipt not written" in captured.err
    printed = json.loads(captured.out)
    assert (printed["status"], printed["reason"]) == ("updated", "content_identical")


def test_git_never_prompts_and_network_calls_have_a_timeout(repo, monkeypatch):
    """RCO2 N2: an unattended run must not wait on a credential prompt or a hung remote."""
    tool = _tool()
    seen = []
    real_run = subprocess.run

    def recording_run(argv, **kwargs):
        seen.append((list(argv), kwargs.get("env"), kwargs.get("timeout")))
        return real_run(argv, **kwargs)

    monkeypatch.setattr(tool.subprocess, "run", recording_run)
    tool._git(str(repo), ["version"])
    tool._remote_tip(str(repo), "origin", "topic")
    tool._git(str(repo), tool._push_argv("origin", "a" * 40, "topic"))

    assert len(seen) == 3
    assert all(env is not None and env.get("GIT_TERMINAL_PROMPT") == "0" for _, env, _ in seen)
    network = [timeout for argv, _, timeout in seen if "ls-remote" in argv or "push" in argv]
    assert len(network) == 2 and all(isinstance(t, (int, float)) and 0 < t < 3600 for t in network)


def test_a_network_timeout_is_a_failed_call_not_an_exception(repo, monkeypatch):
    tool = _tool()

    def timing_out(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout") or 1)

    monkeypatch.setattr(tool.subprocess, "run", timing_out)
    assert tool._remote_tip(str(repo), "origin", "topic") == (None, [])
    assert tool._git(str(repo), tool._push_argv("origin", "a" * 40, "topic"))[0] == 124


def test_an_inherited_git_dir_never_redirects_the_tool(repo, tmp_path, capsys, monkeypatch):
    """RCO1 R2 / Codex P1 4245644006: with GIT_DIR naming another repository, -C repo is not enough; the tool
    reported repo A but wrote the new head into B. Repository-shaping GIT_* variables are stripped."""
    base = _base(repo)
    head = _pr(repo, base, **{"p.txt": "pr\n"})
    _commit(repo, "main", **{"c.txt": "c\n"})
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", "--no-local", str(repo), str(other))
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    receipt_path = tmp_path / "receipt.json"

    code, _ = _run(capsys, *_args(repo, receipt_path, head, base, "main"))

    monkeypatch.delenv("GIT_DIR")
    monkeypatch.delenv("GIT_WORK_TREE")
    receipt = _receipt(receipt_path)
    assert (code, receipt["status"]) == (0, "updated"), receipt
    new_head = receipt["new_head"]
    assert subprocess.run(["git", "-C", str(repo), "cat-file", "-e", new_head]).returncode == 0
    assert subprocess.run(["git", "-C", str(other), "cat-file", "-e", new_head]).returncode != 0


def test_an_inherited_graft_file_is_ignored(repo, tmp_path, capsys, monkeypatch):
    """GIT_GRAFT_FILE would rewrite parents outside info/grafts; it is stripped like the other variables."""
    base = _base(repo)
    head = _pr(repo, base, **{"p.txt": "pr\n"})
    _commit(repo, "main", **{"c.txt": "c\n"})
    grafts = tmp_path / "grafts"
    grafts.write_text(f"{head}\n", encoding="ascii")  # would make the reviewed head a root commit
    monkeypatch.setenv("GIT_GRAFT_FILE", str(grafts))
    receipt_path = tmp_path / "receipt.json"

    code, _ = _run(capsys, *_args(repo, receipt_path, head, base, "main"))

    receipt = _receipt(receipt_path)
    assert (code, receipt["status"], receipt["grafts"]) == (0, "updated", False), receipt


def test_a_shallow_repository_is_refused(repo, tmp_path, capsys):
    """A shallow boundary hides parents, so ancestry and the merge base cannot be trusted."""
    base = _base(repo)
    head = _pr(repo, base, **{"p.txt": "pr\n"})
    _commit(repo, "main", **{"c.txt": "c\n"})
    shallow = tmp_path / "shallow"
    _git(tmp_path, "clone", "-q", "--depth", "1", "--no-single-branch", Path(repo).as_uri(), str(shallow))
    _git(shallow, "fetch", "-q", "--depth", "1", "origin", head)
    receipt_path = tmp_path / "receipt.json"

    code, _ = _run(capsys, *_args(shallow, receipt_path, head, base, "origin/main"))

    receipt = _receipt(receipt_path)
    assert (code, receipt["status"], receipt["reason"]) == (3, "refused", "shallow_repository")


def test_the_tool_is_not_wired_into_any_runtime_path():
    """(b)-class while unwired: wiring it into the gate or a scheduled path makes it (a)-class."""
    listed = subprocess.run(["git", "grep", "-l", "-I", "wd_pr_base_update", "--", "*.py", "*.ps1", "*.psm1",
                             "*.yml", "*.yaml", "*.json", "*.cmd", "*.bat", "*.sh"],
                            cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert listed.returncode in (0, 1), listed.stderr
    users = sorted(set(listed.stdout.split()) - {"tools/wd_pr_base_update.py", "tests/tools/test_wd_pr_base_update.py"})
    assert users == []
