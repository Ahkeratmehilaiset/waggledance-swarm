# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tools.report_prunable_worktrees import (
    _parse_worktree_list,
    classify_worktree,
    gather_worktrees,
    report,
)


BASE = "main"
PROT: frozenset[str] = frozenset()


def _wt(**kw) -> dict:
    base = {"path": "C:/wt/x", "branch": "feat", "head": "abc", "is_main": False,
            "detached": False, "dirty": False, "merged": True}
    base.update(kw)
    return base


# --- pure classification: fail-safe to KEEP ----------------------------------

def test_prunable_when_merged_and_clean():
    r = classify_worktree(_wt(), base=BASE, protected=PROT)
    assert r["prunable"] is True
    assert "merged" in r["reason"]


def test_main_worktree_kept():
    assert classify_worktree(_wt(is_main=True), base=BASE, protected=PROT)["prunable"] is False


def test_protected_path_kept():
    r = classify_worktree(_wt(path="C:/wt/here"), base=BASE, protected=frozenset({"C:/wt/here"}))
    assert r["prunable"] is False
    assert "protected" in r["reason"]


def test_detached_head_kept():
    assert classify_worktree(_wt(detached=True), base=BASE, protected=PROT)["prunable"] is False


def test_no_branch_kept():
    assert classify_worktree(_wt(branch=None), base=BASE, protected=PROT)["prunable"] is False


def test_dirty_unknown_kept():
    r = classify_worktree(_wt(dirty=None), base=BASE, protected=PROT)
    assert r["prunable"] is False
    assert "unreadable" in r["reason"]


def test_dirty_kept():
    r = classify_worktree(_wt(dirty=True), base=BASE, protected=PROT)
    assert r["prunable"] is False
    assert "uncommitted" in r["reason"]


def test_merged_unknown_kept():
    r = classify_worktree(_wt(merged=None), base=BASE, protected=PROT)
    assert r["prunable"] is False
    assert "unknown" in r["reason"]


def test_not_merged_kept():
    r = classify_worktree(_wt(merged=False), base=BASE, protected=PROT)
    assert r["prunable"] is False
    assert "not merged" in r["reason"]


def test_path_backslash_normalized():
    r = classify_worktree(_wt(path="C:\\wt\\x"), base=BASE, protected=PROT)
    assert r["path"] == "C:/wt/x"


# --- report aggregation ------------------------------------------------------

def test_report_counts():
    wts = [_wt(path="C:/a"), _wt(path="C:/b", merged=False), _wt(path="C:/c", is_main=True)]
    r = report(wts, base=BASE, protected=PROT)
    assert r["total"] == 3
    assert r["prunable_count"] == 1
    assert r["keep_count"] == 2
    assert r["prunable"][0]["path"] == "C:/a"


# --- porcelain parsing -------------------------------------------------------

def test_parse_worktree_list():
    text = (
        "worktree /repo/main\nHEAD aaa\nbranch refs/heads/main\n\n"
        "worktree /repo/wt1\nHEAD bbb\nbranch refs/heads/feat\n\n"
        "worktree /repo/det\nHEAD ccc\ndetached\n"
    )
    entries = _parse_worktree_list(text)
    assert len(entries) == 3
    assert entries[0]["branch"] == "main"
    assert entries[1]["branch"] == "feat"
    assert entries[2]["detached"] is True


# --- integration: real temp git repo + worktrees -----------------------------

@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_integration_merged_clean_is_prunable(tmp_path):
    def g(*a, cwd=None):
        subprocess.run(["git", *a], cwd=str(cwd or repo), check=True,
                       capture_output=True, text=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    g("init", "-b", "main")
    g("config", "user.email", "t@t.invalid")
    g("config", "user.name", "t")
    (repo / "f.txt").write_text("1\n", encoding="utf-8")
    g("add", "."); g("commit", "-m", "init")
    # merged branch -> worktree should be prunable
    g("branch", "feat-merged")
    g("worktree", "add", str(tmp_path / "wt-merged"), "feat-merged")
    # unmerged branch with a new commit -> worktree should be kept
    g("worktree", "add", "-b", "feat-open", str(tmp_path / "wt-open"))
    (tmp_path / "wt-open" / "g.txt").write_text("x\n", encoding="utf-8")
    g("add", ".", cwd=tmp_path / "wt-open")
    g("commit", "-m", "open", cwd=tmp_path / "wt-open")

    result = report(gather_worktrees(str(repo), "main"), base="main",
                    protected=frozenset())
    prunable_paths = {Path(c["path"]).name for c in result["prunable"]}
    keep_paths = {Path(c["path"]).name for c in result["keep"]}
    assert "wt-merged" in prunable_paths
    assert "wt-open" in keep_paths      # unmerged -> kept
    assert "repo" in keep_paths         # main worktree -> kept
