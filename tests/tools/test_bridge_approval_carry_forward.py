"""Real Git content identity; no live bridge, merge, or installed runtime."""
from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
import subprocess

import pytest


def _api():
    # Deliberately lazy: each retained RED names an absent behavior, rather than
    # turning the entire file into a collection error before the implementation.
    from tools import bridge_approval_carry_forward
    return bridge_approval_carry_forward


@pytest.fixture
def history(tmp_path):
    root = tmp_path / "git-fixture"
    root.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_AUTHOR_NAME="Synthetic", GIT_AUTHOR_EMAIL="test@example.invalid",
               GIT_COMMITTER_NAME="Synthetic", GIT_COMMITTER_EMAIL="test@example.invalid")
    def git(*args, data=None):
        return subprocess.check_output(["git", *args], cwd=root, env=env, input=data,
                                       stderr=subprocess.PIPE)
    git("init", "--quiet")
    git("config", "core.autocrlf", "false")
    (root / "sample.txt").write_bytes(b"before\n")
    git("add", "sample.txt")
    git("commit", "--quiet", "-m", "base")
    base = git("rev-parse", "HEAD").decode().strip()
    (root / "sample.txt").write_bytes(b"alpha gamma\n")
    git("add", "sample.txt")
    git("commit", "--quiet", "-m", "old reviewed content")
    old = git("rev-parse", "HEAD").decode().strip()
    tree = git("rev-parse", "HEAD^{tree}").decode().strip()
    new = git("commit-tree", tree, "-p", base, data=b"new message only\n").decode().strip()
    return root, git, dict(old_base=base, old_head=old, new_base=base, new_head=new)


def test_real_message_only_change_has_rederivable_identity(history):
    root, git, pair = history
    assert pair["old_head"] != pair["new_head"]
    proof = _api().derive_content_identity(repo_root=root, **pair)
    assert proof["schema"] == "wd.bridge-content-identity.v1"
    assert proof["old_merge_base"] == proof["new_merge_base"] == pair["old_base"]
    assert proof["old_diff_sha256"] == proof["new_diff_sha256"]
    assert proof["old_patch_id"] == proof["new_patch_id"]
    assert proof["old_entries"] == proof["new_entries"]
    assert _api().verify_content_identity(proof=proof, repo_root=root, **pair) == proof


def test_real_rebase_onto_advanced_base_preserves_identity(history):
    root, git, pair = history
    git("checkout", "--quiet", pair["old_base"])
    (root / "unrelated.txt").write_bytes(b"new main content\n")
    git("add", "unrelated.txt")
    git("commit", "--quiet", "-m", "advance base")
    pair["new_base"] = git("rev-parse", "HEAD").decode().strip()
    (root / "sample.txt").write_bytes(b"alpha gamma\n")
    git("add", "sample.txt")
    git("commit", "--quiet", "-m", "same patch rebased")
    pair["new_head"] = git("rev-parse", "HEAD").decode().strip()
    proof = _api().derive_content_identity(repo_root=root, **pair)
    assert proof["old_merge_base"] == pair["old_base"]
    assert proof["new_merge_base"] == pair["new_base"]
    assert proof["old_entries"] == proof["new_entries"]


def test_actual_whitespace_patch_id_collision_refuses(history):
    root, git, pair = history
    git("checkout", "--quiet", pair["old_base"])
    (root / "sample.txt").write_bytes(b"alpha    gamma\n")
    git("add", "sample.txt")
    git("commit", "--quiet", "-m", "content differs despite same patch id")
    pair["new_head"] = git("rev-parse", "HEAD").decode().strip()
    before = git("diff", "--no-ext-diff", "--no-textconv", pair["old_base"], pair["old_head"])
    after = git("diff", "--no-ext-diff", "--no-textconv", pair["new_base"], pair["new_head"])
    assert hashlib.sha256(before).digest() != hashlib.sha256(after).digest()
    assert git("patch-id", "--stable", data=before).split()[0] == git("patch-id", "--stable", data=after).split()[0]
    with pytest.raises(ValueError, match="content|diff|identity"):
        _api().derive_content_identity(repo_root=root, **pair)


@pytest.mark.parametrize("field", ["old_head", "new_head", "old_base", "new_base"])
def test_missing_git_object_refuses_instead_of_accepting_caller_claim(history, field):
    root, _, pair = history
    pair[field] = "f" * 40
    with pytest.raises(ValueError):
        _api().derive_content_identity(repo_root=root, **pair)


@pytest.mark.parametrize("field", ["old_diff_sha256", "new_patch_id", "old_merge_base", "new_entries"])
def test_rederivation_rejects_resealed_or_mutated_evidence(history, field):
    root, _, pair = history
    proof = _api().derive_content_identity(repo_root=root, **pair)
    proof = copy.deepcopy(proof)
    proof[field] = [] if field == "new_entries" else "0" * 64
    with pytest.raises(ValueError):
        _api().verify_content_identity(proof=proof, repo_root=root, **pair)


def test_empty_diff_has_no_approval_carry_identity(history):
    root, _, pair = history
    pair["old_head"] = pair["new_head"] = pair["old_base"]
    with pytest.raises(ValueError, match="empty|patch|identity"):
        _api().derive_content_identity(repo_root=root, **pair)


def test_config_and_diff_driver_cannot_replace_git_content(history):
    root, git, pair = history
    git("config", "diff.noprefix", "true")
    git("config", "diff.mnemonicPrefix", "true")
    git("config", "diff.algorithm", "histogram")
    proof = _api().derive_content_identity(repo_root=root, **pair)
    git("config", "diff.noprefix", "false")
    git("config", "diff.mnemonicPrefix", "false")
    git("config", "diff.algorithm", "myers")
    assert _api().verify_content_identity(proof=proof, repo_root=root, **pair) == proof


def test_ambiguous_criss_cross_merge_bases_refuse(history):
    root, git, pair = history
    tree = git("rev-parse", pair["old_base"] + "^{tree}").decode().strip()
    a = git("commit-tree", tree, "-p", pair["old_base"], data=b"A\n").decode().strip()
    b = git("commit-tree", tree, "-p", pair["old_base"], data=b"B\n").decode().strip()
    left = git("commit-tree", tree, "-p", a, "-p", b, data=b"left\n").decode().strip()
    right = git("commit-tree", tree, "-p", b, "-p", a, data=b"right\n").decode().strip()
    assert len(git("merge-base", "--all", left, right).splitlines()) == 2
    pair.update(old_base=left, old_head=right)
    with pytest.raises(ValueError, match="merge.base|ambiguous"):
        _api().derive_content_identity(repo_root=root, **pair)
