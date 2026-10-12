# SPDX-License-Identifier: BUSL-1.1
"""tools/bridge_carry_forward_proof.py: when may approvals given at a reviewed head count at its base update?

Operator 2026-10-12 (round 5, plan doc "Carry-forward-todiste: kaikki ehdot vaaditaan"); every condition
is required:

1. the old head has a full, direct consensus verdict: approvals, every eligible RCO, no veto, charter
   and green CI at that SHA;
2. the new tree is ``git merge-tree --write-tree`` of current main and the old head, exit 0, no edits;
3. the PR delta's stable patch-id and every PR file's blob are unchanged;
4. the new head's first parent is the old head (no force-push) and its other parent is current main;
5. no PR path is in the base delta (RCO1: overlap only where the blob is the same in both bases, which
   is exactly "not in the base delta");
6. gate and governance PRs never carry approvals;
7. CI and the veto check run again at the new head; the receipt stores both SHAs, both patch-ids, the
   merge-tree oid and the path sets; any deviation or extra commit needs new votes.

The verifier re-derives everything from git objects; it never reads a receipt or a status flag.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "tools" / "bridge_carry_forward_proof.py"


def _module():
    spec = importlib.util.spec_from_file_location("bridge_carry_forward_proof_under_test", MODULE)
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


REPOSITORY_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                  "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_NAMESPACE", "GIT_GRAFT_FILE", "GIT_SHALLOW_FILE",
                  "GIT_REPLACE_REF_BASE")
NL = chr(10)
LINES = "".join(f"line {n}\n" for n in range(1, 21))


@pytest.fixture
def repo(tmp_path, monkeypatch):
    empty = tmp_path / "empty.gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "wd-test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "wd-test@example.invalid")
    for name in REPOSITORY_ENV:
        monkeypatch.delenv(name, raising=False)
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


def _tree(repo: Path, main: str, head: str, merge_base: str | None = None) -> str:
    args = ["merge-tree", "--write-tree", "--no-messages"]
    if merge_base:
        args.append(f"--merge-base={merge_base}")
    result = subprocess.run(["git", *args, main, head], cwd=repo, capture_output=True, text=True)
    return result.stdout.split()[0]


def _merge(repo: Path, tree: str, *parents: str) -> str:
    args = ["commit-tree", tree]
    for parent in parents:
        args += ["-p", parent]
    return _git(repo, *args, "-m", "update").strip()


def _update(repo: Path, head: str, main: str) -> str:
    """The content-identical update the plan describes: merge-tree tree, reviewed head first."""
    base = _git(repo, "merge-base", head, main).strip()
    return _merge(repo, _tree(repo, main, head, base), head, main)


def _verify(repo: Path, old_head: str, new_head: str, main: str) -> dict:
    return _module().verify_carry_forward(repo=str(repo), old_head=old_head, new_head=new_head, main_head=main)


def _scenario(repo: Path) -> tuple[str, str, str, str]:
    base = _base(repo)
    head = _pr(repo, base, **{"a.txt": LINES.replace("line 3\n", "line three\n"), "p.txt": "pr\n", "b.txt": None})
    main = _commit(repo, "main moves", **{"c.txt": "c\n", "dir__one.txt": "ONE\n"})
    return base, head, main, _update(repo, head, main)


def _rev(repo: Path, name: str) -> str:
    return _git(repo, "rev-parse", name).strip()


# --- conditions 2-5: the proof itself -------------------------------------------------------------


def test_a_content_identical_base_update_is_proven(repo):
    base, head, main, new_head = _scenario(repo)

    proof = _verify(repo, head, new_head, main)

    assert (proof["ok"], proof["reason"]) == (True, "content_identical_base_update"), proof
    assert proof["schema"] == "wd.carry-forward-proof.v1"
    assert (proof["old_head"], proof["new_head"], proof["main_head"], proof["old_base"]) == (head, new_head, main, base)
    assert proof["new_head_parents"] == [head, main]
    assert proof["merge_tree"] == _rev(repo, f"{new_head}^{{tree}}")
    assert proof["merge_tree_exit"] == 0
    assert proof["pr_paths"] == ["a.txt", "b.txt", "p.txt"]
    assert proof["base_delta_paths"] == ["c.txt", "dir/one.txt"]
    assert proof["overlap_paths"] == []
    assert proof["gate_governance_paths"] == []
    assert proof["patch_id_old"] and proof["patch_id_old"] == proof["patch_id_new"]
    assert [entry["path"] for entry in proof["entries"]] == ["a.txt", "b.txt", "p.txt"]
    assert all(entry["old"] == entry["new"] for entry in proof["entries"])
    assert proof["replace_refs"] == 0 and proof["grafts"] is False
    assert (proof["shallow"], proof["case_collisions"], proof["stripped_git_env"]) == (False, [], [])


def test_a_rebased_head_is_not_a_carry(repo):
    base, head, main, _ = _scenario(repo)
    rebased = _merge(repo, _tree(repo, main, head, base), main)

    proof = _verify(repo, head, rebased, main)

    assert (proof["ok"], proof["reason"]) == (False, "new_head_not_a_two_parent_merge")


def test_the_reviewed_head_must_be_the_first_parent(repo):
    base, head, main, _ = _scenario(repo)
    swapped = _merge(repo, _tree(repo, main, head, base), main, head)

    proof = _verify(repo, head, swapped, main)

    assert (proof["ok"], proof["reason"]) == (False, "first_parent_not_old_head")


def test_the_other_parent_must_be_current_main(repo):
    _, head, main, new_head = _scenario(repo)
    newer_main = _commit(repo, "main moves again", **{"d.txt": "d\n"})

    proof = _verify(repo, head, new_head, newer_main)

    assert (proof["ok"], proof["reason"]) == (False, "second_parent_not_main")


def test_an_extra_commit_on_top_needs_new_votes(repo):
    _, head, main, new_head = _scenario(repo)
    _git(repo, "checkout", "-q", "--detach", new_head)
    extra = _commit(repo, "extra", **{"p.txt": "changed after review\n"})
    _git(repo, "checkout", "-q", "-f", "main")

    proof = _verify(repo, head, extra, main)

    assert (proof["ok"], proof["reason"]) == (False, "new_head_not_a_two_parent_merge")


def test_an_extra_commit_below_the_merge_needs_new_votes(repo):
    base = _base(repo)
    head = _pr(repo, base, **{"p.txt": "pr\n"})
    _git(repo, "checkout", "-q", "pr")
    extra = _commit(repo, "extra", **{"q.txt": "unreviewed\n"})
    _git(repo, "checkout", "-q", "main")
    main = _commit(repo, "main", **{"c.txt": "c\n"})

    proof = _verify(repo, head, _update(repo, extra, main), main)

    assert (proof["ok"], proof["reason"]) == (False, "first_parent_not_old_head")


def test_a_hand_edited_merge_is_refused(repo):
    base, head, main, _ = _scenario(repo)
    _git(repo, "checkout", "-q", "--detach", main)
    _git(repo, "read-tree", _tree(repo, main, head, base))
    _git(repo, "checkout-index", "-a", "-f")
    _write(repo, "p.txt", "edited in the merge\n")
    _git(repo, "add", "p.txt")
    edited = _merge(repo, _git(repo, "write-tree").strip(), head, main)
    _git(repo, "checkout", "-q", "-f", "main")

    proof = _verify(repo, head, edited, main)

    assert (proof["ok"], proof["reason"]) == (False, "tree_mismatch")


def test_a_conflicting_update_is_refused(repo):
    base = _base(repo)
    head = _pr(repo, base, **{"a.txt": LINES.replace("line 3\n", "pr three\n")})
    main = _commit(repo, "main", **{"a.txt": LINES.replace("line 3\n", "main three\n")})
    resolved = _merge(repo, _rev(repo, f"{head}^{{tree}}"), head, main)

    proof = _verify(repo, head, resolved, main)

    assert (proof["ok"], proof["reason"]) == (False, "merge_conflict")
    assert proof["merge_tree_exit"] == 1


@pytest.mark.parametrize("case", ["other_hunk", "same_change"])
def test_a_pr_path_the_base_also_changed_is_refused_even_when_the_merge_is_clean(repo, case):
    base = _base(repo)
    if case == "other_hunk":
        head = _pr(repo, base, **{"a.txt": LINES.replace("line 3\n", "line three\n")})
        main = _commit(repo, "main", **{"a.txt": LINES.replace("line 18\n", "line eighteen\n")})
    else:
        head = _pr(repo, base, **{"p.txt": "same\n"})
        main = _commit(repo, "main", **{"p.txt": "same\n"})

    proof = _verify(repo, head, _update(repo, head, main), main)

    assert (proof["ok"], proof["reason"]) == (False, "paths_overlap")
    assert proof["overlap_paths"] == (["a.txt"] if case == "other_hunk" else ["p.txt"])


def test_a_base_directory_rename_that_moves_a_pr_file_is_refused(repo):
    _git(repo, "config", "merge.directoryRenames", "true")
    base = _base(repo)
    head = _pr(repo, base, **{"dir__new.txt": "new\n"})
    _git(repo, "mv", "dir", "newdir")
    _git(repo, "commit", "-q", "-m", "rename dir")
    main = _rev(repo, "HEAD")

    proof = _verify(repo, head, _update(repo, head, main), main)

    assert (proof["ok"], proof["reason"]) == (False, "delta_changed")
    assert proof["overlap_paths"] == []


def test_an_empty_pr_is_not_a_carry(repo):
    base = _base(repo)
    main = _commit(repo, "main", **{"c.txt": "c\n"})

    proof = _verify(repo, base, _merge(repo, _rev(repo, f"{main}^{{tree}}"), base, main), main)

    assert (proof["ok"], proof["reason"]) == (False, "already_contains_main_or_empty")


def test_an_empty_commit_pr_is_not_a_carry(repo):
    """Mutant MUT_CF_EMPTY_ACCEPTED survived without this: the head is not an ancestor of main, the delta is empty."""
    base = _base(repo)
    _git(repo, "checkout", "-q", "-b", "pr", base)
    _git(repo, "commit", "-q", "--allow-empty", "-m", "empty")
    head = _rev(repo, "HEAD")
    _git(repo, "checkout", "-q", "main")
    main = _commit(repo, "main", **{"c.txt": "c\n"})

    proof = _verify(repo, head, _update(repo, head, main), main)

    assert (proof["ok"], proof["reason"]) == (False, "empty_pr_delta")


def test_a_patch_id_mismatch_never_carries(repo, monkeypatch):
    """Equal per-file entries already imply equal diffs, so this check is the independent second witness; it is
    exercised through the _patch_id seam (mutant MUT_CF_NO_PATCH_ID_CHECK survived without this)."""
    base, head, main, new_head = _scenario(repo)
    module = _module()
    real = module._patch_id
    monkeypatch.setattr(module, "_patch_id", lambda repo_, old, new: real(repo_, old, new) + ("x" if old == base else ""))

    proof = module.verify_carry_forward(repo=str(repo), old_head=head, new_head=new_head, main_head=main)

    assert (proof["ok"], proof["reason"]) == (False, "patch_id_changed")
    assert proof["patch_id_old"] != proof["patch_id_new"]


def test_a_head_that_already_contains_main_is_not_a_carry(repo):
    base = _base(repo)
    head = _pr(repo, base, **{"p.txt": "pr\n"})
    new_head = _merge(repo, _rev(repo, f"{head}^{{tree}}"), head, base)

    proof = _verify(repo, head, new_head, base)

    assert (proof["ok"], proof["reason"]) == (False, "already_contains_main_or_empty")


def test_a_criss_cross_history_is_refused(repo):
    base = _base(repo)
    _git(repo, "checkout", "-q", "-b", "pr", base)
    side = _commit(repo, "side", **{"s.txt": "s\n"})
    _git(repo, "checkout", "-q", "main")
    m1 = _commit(repo, "m1", **{"c.txt": "c\n"})
    _git(repo, "merge", "-q", "--no-ff", "-m", "m2", side)
    main = _rev(repo, "HEAD")
    _git(repo, "checkout", "-q", "pr")
    _git(repo, "merge", "-q", "--no-ff", "-m", "x2", m1)
    head = _commit(repo, "pr change", **{"p.txt": "pr\n"})
    _git(repo, "checkout", "-q", "main")
    new_head = _merge(repo, _tree(repo, main, head), head, main)

    proof = _verify(repo, head, new_head, main)

    assert (proof["ok"], proof["reason"]) == (False, "old_base_not_unique")


@pytest.mark.parametrize("pr_path,base_path", [("Foo.txt", "foo.txt"), ("dir__Readme.md", "dir__README.md")])
def test_a_case_variant_of_a_pr_path_in_the_base_delta_is_overlap(repo, pr_path, base_path):
    """RCO2 R1 on PR1861 (same check here): on a case-insensitive checkout Foo.txt and foo.txt are one file."""
    base = _base(repo)
    head = _pr(repo, base, **{pr_path: "pr\n"})
    main = _commit(repo, "main", **{base_path: "base\n"})

    proof = _verify(repo, head, _update(repo, head, main), main)

    assert (proof["ok"], proof["reason"]) == (False, "paths_overlap")
    assert proof["overlap_paths"] == [pr_path.replace("__", "/")]


@pytest.mark.parametrize("pr_files,base_files", [
    ({"Foo": "pr file\n"}, {"foo__x.txt": "base dir\n"}),
    ({"foo__x.txt": "pr dir\n"}, {"Foo": "base file\n"}),
])
def test_a_pr_path_colliding_by_case_with_a_file_or_directory_never_carries(repo, pr_files, base_files):
    base = _base(repo)
    head = _pr(repo, base, **pr_files)
    main = _commit(repo, "main", **base_files)

    proof = _verify(repo, head, _update(repo, head, main), main)

    assert (proof["ok"], proof["reason"]) == (False, "case_collision")
    assert proof["case_collisions"] == sorted(name.replace("__", "/") for name in pr_files)


DOTLESS_I = "\u0131"  # LATIN SMALL LETTER DOTLESS I: upper() is "I", casefold() is itself


def test_a_dotless_i_variant_of_a_pr_path_in_the_base_delta_is_overlap(repo):
    """RCO1 shared Grok on PR1861 (de7113e9, confirmed): NTFS upper-cases each name, so it holds one of the two."""
    base = _base(repo)
    head = _pr(repo, base, **{DOTLESS_I + ".txt": "pr" + NL})
    main = _commit(repo, "main", **{"i.txt": "base" + NL})

    proof = _verify(repo, head, _update(repo, head, main), main)

    assert (proof["ok"], proof["reason"]) == (False, "paths_overlap")
    assert proof["overlap_paths"] == [DOTLESS_I + ".txt"]


def test_a_dotless_i_pr_file_colliding_with_a_base_directory_never_carries(repo):
    base = _base(repo)
    head = _pr(repo, base, **{DOTLESS_I: "pr file" + NL})
    main = _commit(repo, "main", **{"i__x.txt": "base dir" + NL})

    proof = _verify(repo, head, _update(repo, head, main), main)

    assert (proof["ok"], proof["reason"]) == (False, "case_collision")
    assert proof["case_collisions"] == [DOTLESS_I]

def test_an_inherited_git_dir_never_redirects_the_proof(repo, tmp_path, monkeypatch):
    """RCO1 R2 / Codex P1 on PR1861 (same here): -C does not pin the repository while GIT_DIR is set."""
    base = _base(repo)
    head = _pr(repo, base, **{"p.txt": "pr\n"})
    main = _commit(repo, "main", **{"c.txt": "c\n"})
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", "--no-local", str(repo), str(other))  # has no new head
    new_head = _update(repo, head, main)
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))

    proof = _verify(repo, head, new_head, main)

    assert proof["ok"] is True, proof
    assert proof["stripped_git_env"] == ["GIT_DIR"]
    assert str(other) not in json.dumps(proof)


def test_a_shallow_repository_never_carries(repo, tmp_path):
    _, head, main, new_head = _scenario(repo)
    shallow = tmp_path / "shallow"
    _git(tmp_path, "clone", "-q", "--depth", "1", Path(repo).as_uri(), str(shallow))
    _git(shallow, "fetch", "-q", "--depth", "1", "origin", head, new_head)

    proof = _verify(shallow, head, new_head, main)

    assert (proof["ok"], proof["reason"]) == (False, "shallow_repository")


# --- condition 6: gate and governance PRs never carry -------------------------------------------


@pytest.mark.parametrize("path", ["tools/check_rco_pass_present.py", "tools/idle_consensus_auto_merge.py",
                                  "CLAUDE.md", "tools/bridge_carry_forward_proof.py"])
def test_gate_and_governance_prs_never_carry(repo, path):
    base = _base(repo)
    head = _pr(repo, base, **{"p.txt": "pr\n", path.replace("/", "__"): "x = 1\n"})
    main = _commit(repo, "main", **{"c.txt": "c\n"})

    proof = _verify(repo, head, _update(repo, head, main), main)

    assert (proof["ok"], proof["reason"]) == (False, "gate_or_governance_path")
    assert proof["gate_governance_paths"] == [path.lower()]


# --- inputs and object integrity -----------------------------------------------------------------


@pytest.mark.parametrize("which", ["old_head", "new_head", "main_head"])
@pytest.mark.parametrize("value", ["HEAD", "--all", "abc123", "main", ""])
def test_heads_must_be_full_object_names(repo, which, value):
    _, head, main, new_head = _scenario(repo)
    names = {"old_head": head, "new_head": new_head, "main_head": main, which: value}

    proof = _module().verify_carry_forward(repo=str(repo), **names)

    assert (proof["ok"], proof["reason"]) == (False, "invalid_input")


def test_an_unknown_object_is_refused(repo):
    _, head, main, _ = _scenario(repo)

    proof = _verify(repo, head, "0" * 40, main)

    assert (proof["ok"], proof["reason"]) == (False, "unresolvable_commit")


def test_a_tag_object_is_not_a_head(repo):
    _, head, main, new_head = _scenario(repo)
    _git(repo, "tag", "-a", "-m", "reviewed", "reviewed", head)
    tag_object = _rev(repo, "refs/tags/reviewed")
    assert tag_object != head

    proof = _verify(repo, tag_object, new_head, main)

    assert (proof["ok"], proof["reason"]) == (False, "unresolvable_commit")


def test_the_same_head_is_not_a_carry(repo):
    _, head, main, _ = _scenario(repo)

    proof = _verify(repo, head, head, main)

    assert (proof["ok"], proof["reason"]) == (False, "same_head")


def test_grafts_are_refused(repo):
    base, head, main, new_head = _scenario(repo)
    grafts = Path(_git(repo, "rev-parse", "--git-path", "info/grafts").strip())
    if not grafts.is_absolute():
        grafts = repo / grafts
    grafts.parent.mkdir(parents=True, exist_ok=True)
    grafts.write_text(f"{head} {base}\n", encoding="ascii")

    proof = _verify(repo, head, new_head, main)

    assert (proof["ok"], proof["reason"]) == (False, "grafts_present")


def _replaced(repo: Path) -> tuple[str, str, str]:
    """A reviewed head whose refs/replace stand-in carries other content."""
    base = _base(repo)
    head = _pr(repo, base, **{"p.txt": "reviewed\n"})
    _git(repo, "checkout", "-q", "--detach", base)
    _write(repo, "p.txt", "not reviewed\n")
    _git(repo, "add", "p.txt")
    other = _git(repo, "commit-tree", _git(repo, "write-tree").strip(), "-p", base, "-m", "pr change").strip()
    _git(repo, "checkout", "-q", "-f", "main")
    _git(repo, "replace", head, other)
    main = _commit(repo, "main", **{"c.txt": "c\n"})
    return base, head, main


def test_replace_refs_do_not_change_what_is_proven(repo):
    base, head, main = _replaced(repo)
    real_tree = _git(repo, "--no-replace-objects", "merge-tree", "--write-tree", "--no-messages",
                     f"--merge-base={base}", main, head).split()[0]

    proof = _verify(repo, head, _merge(repo, real_tree, head, main), main)

    assert proof["ok"] is True, proof
    assert proof["replace_refs"] == 1


def test_unreviewed_content_from_a_replacement_is_refused(repo):
    base, head, main = _replaced(repo)
    replaced_tree = _tree(repo, main, head, base)  # honours refs/replace: carries "not reviewed"

    proof = _verify(repo, head, _merge(repo, replaced_tree, head, main), main)

    assert (proof["ok"], proof["reason"]) == (False, "tree_mismatch")


def test_a_failing_git_command_fails_closed(repo, monkeypatch):
    _, head, main, new_head = _scenario(repo)
    module = _module()
    real = module._git

    def failing(repo_, args, stdin=None):
        if args[0] == "diff-tree":
            return 128, b""
        return real(repo_, args, stdin)

    monkeypatch.setattr(module, "_git", failing)
    proof = module.verify_carry_forward(repo=str(repo), old_head=head, new_head=new_head, main_head=main)

    assert (proof["ok"], proof["reason"]) == (False, "git_failed")
    assert proof["failed_command"]["argv"][:2] == ["diff-tree", "-r"]


def test_an_unexpected_error_fails_closed(repo, monkeypatch):
    _, head, main, new_head = _scenario(repo)
    module = _module()

    def broken(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(module, "_delta", broken)
    proof = module.verify_carry_forward(repo=str(repo), old_head=head, new_head=new_head, main_head=main)

    assert (proof["ok"], proof["reason"]) == (False, "internal_error")
    assert proof["error_type"] == "RuntimeError"


# --- conditions 1 and 7: the carry decision -------------------------------------------------------


GOOD_VERDICT = {"consensus_ok": True, "ci_all_green": True, "charter_ok": True, "veto_clear": True,
                "approvals_carried": False}


def _evaluate(repo: Path, head: str, new_head: str, main: str, verdict: dict | None = None,
              ci: object = True, veto: object = True) -> dict:
    old = dict(GOOD_VERDICT, head=head) if verdict is None else verdict
    return _module().evaluate_carry_forward(repo=str(repo), old_head=head, new_head=new_head, main_head=main,
                                            old_head_verdict=old, new_head_ci_all_green=ci,
                                            new_head_veto_clear=veto)


def test_a_full_direct_verdict_carries_and_the_receipt_rederives_it(repo):
    base, head, main, new_head = _scenario(repo)

    decision = _evaluate(repo, head, new_head, main)

    assert (decision["carried"], decision["reasons"]) == (True, []), decision
    receipt = decision["receipt"]
    assert receipt["schema"] == "wd.carry-forward-receipt.v1"
    assert (receipt["old_head"], receipt["new_head"], receipt["main_head"], receipt["old_base"]) == (
        head, new_head, main, base)
    assert receipt["patch_id_old"] == receipt["patch_id_new"] == decision["proof"]["patch_id_old"]
    assert receipt["merge_tree"] == _rev(repo, f"{new_head}^{{tree}}")
    assert receipt["pr_paths"] == ["a.txt", "b.txt", "p.txt"]
    assert receipt["base_delta_paths"] == ["c.txt", "dir/one.txt"]
    assert receipt["old_head_verdict"] == dict(GOOD_VERDICT, head=head)
    assert (receipt["new_head_ci_all_green"], receipt["new_head_veto_clear"]) == (True, True)
    assert (receipt["shallow"], receipt["case_collisions"], receipt["stripped_git_env"]) == (False, [], [])
    assert (receipt["replace_refs"], receipt["grafts"]) == (0, False)
    assert json.loads(json.dumps(receipt, ensure_ascii=True)) == receipt


@pytest.mark.parametrize("field", ["consensus_ok", "ci_all_green", "charter_ok", "veto_clear"])
@pytest.mark.parametrize("value", [False, None, 1, "true"])
def test_the_old_head_needs_every_element_of_a_full_verdict(repo, field, value):
    _, head, main, new_head = _scenario(repo)
    verdict = dict(GOOD_VERDICT, head=head, **{field: value})

    decision = _evaluate(repo, head, new_head, main, verdict=verdict)

    assert decision["carried"] is False
    assert any(field in reason for reason in decision["reasons"])


@pytest.mark.parametrize("verdict", [None, [], "ok", {}])
def test_a_missing_old_head_verdict_never_carries(repo, verdict):
    _, head, main, new_head = _scenario(repo)

    decision = _module().evaluate_carry_forward(repo=str(repo), old_head=head, new_head=new_head, main_head=main,
                                                old_head_verdict=verdict, new_head_ci_all_green=True,
                                                new_head_veto_clear=True)

    assert decision["carried"] is False


def test_the_old_verdict_must_be_for_the_old_head(repo):
    _, head, main, new_head = _scenario(repo)

    decision = _evaluate(repo, head, new_head, main, verdict=dict(GOOD_VERDICT, head=main))

    assert decision["carried"] is False
    assert any("head" in reason for reason in decision["reasons"])


@pytest.mark.parametrize("carried", [True, None, "false", 0])
def test_carried_approvals_never_carry_again(repo, carried):
    _, head, main, new_head = _scenario(repo)

    decision = _evaluate(repo, head, new_head, main, verdict=dict(GOOD_VERDICT, head=head, approvals_carried=carried))

    assert decision["carried"] is False
    assert any("approvals_carried" in reason for reason in decision["reasons"])


@pytest.mark.parametrize("which", ["ci", "veto"])
@pytest.mark.parametrize("value", [False, None, 1, "success"])
def test_ci_and_the_veto_check_must_pass_again_at_the_new_head(repo, which, value):
    _, head, main, new_head = _scenario(repo)

    decision = _evaluate(repo, head, new_head, main, **{which: value})

    assert decision["carried"] is False
    assert any(("ci" if which == "ci" else "veto") in reason for reason in decision["reasons"])


def test_a_failed_proof_never_carries_even_with_a_perfect_verdict(repo):
    base = _base(repo)
    head = _pr(repo, base, **{"p.txt": "pr\n", "CLAUDE.md": "rules\n"})
    main = _commit(repo, "main", **{"c.txt": "c\n"})
    new_head = _update(repo, head, main)

    decision = _evaluate(repo, head, new_head, main)

    assert decision["carried"] is False
    assert decision["proof"]["reason"] == "gate_or_governance_path"
    assert decision["receipt"]["new_head"] == new_head


READ_ONLY_SUBCOMMANDS = {"for-each-ref", "rev-parse", "rev-list", "merge-base", "diff-tree", "diff", "patch-id",
                         "ls-tree", "merge-tree"}  # merge-tree --write-tree writes unreachable tree objects only


def test_the_module_runs_only_read_only_git_subcommands(repo, monkeypatch):
    _, head, main, new_head = _scenario(repo)
    module = _module()
    real = module._git
    seen = []

    def recording(repo_, args, stdin=None):
        words = list(args)
        while words and words[0] == "-c":
            words = words[2:]
        seen.append(words[0])
        return real(repo_, args, stdin)

    monkeypatch.setattr(module, "_git", recording)
    decision = module.evaluate_carry_forward(repo=str(repo), old_head=head, new_head=new_head, main_head=main,
                                             old_head_verdict=dict(GOOD_VERDICT, head=head),
                                             new_head_ci_all_green=True, new_head_veto_clear=True)

    assert decision["carried"] is True
    assert seen and set(seen) <= READ_ONLY_SUBCOMMANDS, sorted(set(seen) - READ_ONLY_SUBCOMMANDS)
    assert seen.count("merge-tree") == 1


def test_git_runs_without_inherited_repository_variables_and_never_prompts(repo, monkeypatch):
    _, head, main, new_head = _scenario(repo)
    module = _module()
    real = subprocess.run
    envs = []

    def recording(argv, **kwargs):
        envs.append(kwargs.get("env"))
        return real(argv, **kwargs)

    monkeypatch.setenv("GIT_INDEX_FILE", str(repo / "elsewhere.index"))
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "1")
    monkeypatch.setattr(module.subprocess, "run", recording)
    proof = module.verify_carry_forward(repo=str(repo), old_head=head, new_head=new_head, main_head=main)

    assert proof["ok"] is True, proof
    assert proof["stripped_git_env"] == ["GIT_INDEX_FILE"]
    assert envs and all(env is not None and env.get("GIT_TERMINAL_PROMPT") == "0" for env in envs)
    assert not [key for env in envs for key in env if key.upper() in REPOSITORY_ENV]


def test_every_git_call_has_a_timeout(repo, monkeypatch):
    """RCO1 follow-up on PR1861 (same here): a hung git call (a lock, a filter, a slow disk) must end."""
    _, head, main, new_head = _scenario(repo)
    module = _module()
    real = subprocess.run
    timeouts = []

    def recording(argv, **kwargs):
        timeouts.append(kwargs.get("timeout"))
        return real(argv, **kwargs)

    monkeypatch.setattr(module.subprocess, "run", recording)
    proof = module.verify_carry_forward(repo=str(repo), old_head=head, new_head=new_head, main_head=main)

    assert proof["ok"] is True, proof
    assert timeouts and all(isinstance(t, (int, float)) and 0 < t <= 3600 for t in timeouts), timeouts


def test_a_git_timeout_fails_closed(repo, monkeypatch):
    _, head, main, new_head = _scenario(repo)
    module = _module()

    def timing_out(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout") or 1)

    monkeypatch.setattr(module.subprocess, "run", timing_out)
    proof = module.verify_carry_forward(repo=str(repo), old_head=head, new_head=new_head, main_head=main)

    assert (proof["ok"], proof["reason"]) == (False, "git_failed")
    assert proof["failed_command"]["exit"] == 124


def test_inherited_git_variables_are_named_but_never_their_values(repo, monkeypatch):
    """RCO1 follow-up on PR1861 (same here): injected config or transport variables are visible by name."""
    _, head, main, new_head = _scenario(repo)
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'wd.probe=value-one-7f3a'")
    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -o wd=value-two-7f3a")

    decision = _evaluate(repo, head, new_head, main)

    assert decision["carried"] is True, decision
    for record in (decision["proof"], decision["receipt"]):
        assert {"GIT_CONFIG_PARAMETERS", "GIT_SSH_COMMAND"} <= set(record["inherited_git_env"])
        assert record["inherited_git_env"] == sorted(record["inherited_git_env"])
        assert not set(record["inherited_git_env"]) & set(REPOSITORY_ENV)
    dumped = json.dumps(decision)
    assert "value-one-7f3a" not in dumped and "value-two-7f3a" not in dumped
