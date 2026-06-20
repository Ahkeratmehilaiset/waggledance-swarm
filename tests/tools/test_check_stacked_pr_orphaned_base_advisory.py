# SPDX-License-Identifier: BUSL-1.1
"""Tests for tools/check_stacked_pr_orphaned_base_advisory.py.

Forge vectors: an ORPHANED-base stacked PR (base not an ancestor of main, e.g.
base branch squash-merged like #1307's ca81aff0) is flagged; a BEHIND-but-mergeable
stacked PR (base still an ancestor of main) is NOT flagged; a base-on-main PR is
not orphaned-flagged and is conflict-flagged only when merge-tree conflicts.
Read-only/advisory: the CLI never blocks (exits 0). Offline/deterministic (git
predicates injected).
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.check_stacked_pr_orphaned_base_advisory import (  # noqa: E402
    classify_open_pr_base_hazards,
    main,
)

MAIN = "f" * 40
BASE_IN_HISTORY = "a" * 40  # an ancestor of main (behind-but-mergeable)
BASE_ORPHANED = "c" * 40  # NOT an ancestor of main (squash-merged base)
HEAD = "b" * 40


def _pr(number, head_ref, base_ref, base_oid, head_oid=HEAD):
    return {
        "number": number,
        "headRefName": head_ref,
        "headRefOid": head_oid,
        "baseRefName": base_ref,
        "baseRefOid": base_oid,
    }


def _ancestor_set(*ancestors):
    anc = set(ancestors)
    return lambda a, d: a in anc  # is_ancestor(a, d): a in the known-ancestor set


def test_orphaned_base_stacked_pr_flagged() -> None:
    # The #1307 pattern: stacked on a squash-merged base SHA not in main history.
    prs = [_pr(1307, "lead/route-stage", "lead/parent-branch", BASE_ORPHANED)]
    out = classify_open_pr_base_hazards(prs, MAIN, _ancestor_set(BASE_IN_HISTORY))
    assert len(out) == 1
    assert out[0]["number"] == 1307
    assert "orphaned_base" in out[0]["hazards"]
    assert "retarget" in out[0]["advice"]


def test_behind_but_mergeable_stacked_pr_not_flagged() -> None:
    # Non-main base, but base IS an ancestor of main -> behind, not orphaned.
    prs = [_pr(42, "feat/x", "feat/parent", BASE_IN_HISTORY)]
    out = classify_open_pr_base_hazards(prs, MAIN, _ancestor_set(BASE_IN_HISTORY))
    assert out == []


def test_base_on_main_by_name_not_orphaned() -> None:
    prs = [_pr(50, "feat/y", "main", BASE_IN_HISTORY)]
    # No merge_tree_conflict provided -> base-on-main PRs produce no advisory.
    out = classify_open_pr_base_hazards(prs, MAIN, _ancestor_set())
    assert out == []


def test_base_on_main_by_oid_not_orphaned() -> None:
    prs = [_pr(51, "feat/z", "main", MAIN)]
    out = classify_open_pr_base_hazards(prs, MAIN, _ancestor_set())
    assert out == []


def test_base_on_main_merge_tree_conflict_flagged() -> None:
    prs = [_pr(60, "feat/c", "main", MAIN, head_oid=HEAD)]
    conflict = lambda base, head: head == HEAD  # this head conflicts vs main
    out = classify_open_pr_base_hazards(prs, MAIN, _ancestor_set(), conflict)
    assert len(out) == 1
    assert "merge_tree_conflict_vs_main" in out[0]["hazards"]
    assert "conflict" in out[0]["advice"]


def test_base_on_main_no_conflict_not_flagged() -> None:
    prs = [_pr(61, "feat/d", "main", MAIN)]
    no_conflict = lambda base, head: False
    out = classify_open_pr_base_hazards(prs, MAIN, _ancestor_set(), no_conflict)
    assert out == []


def test_orphaned_takes_precedence_no_conflict_check_on_stacked() -> None:
    # A stacked orphaned PR is flagged orphaned; merge-tree conflict is not the
    # primary signal (retarget first). Conflict fn must not be consulted for it.
    calls = []

    def conflict(base, head):
        calls.append((base, head))
        return True

    prs = [_pr(1308, "lead/stack", "lead/parent", BASE_ORPHANED)]
    out = classify_open_pr_base_hazards(prs, MAIN, _ancestor_set(), conflict)
    assert out[0]["hazards"] == ["orphaned_base"]
    assert calls == []  # conflict check not run for non-main-base PRs


def test_main_cli_is_advisory_exit_zero(tmp_path, monkeypatch, capsys) -> None:
    # CLI is WARN/advisory-only: exits 0 even when advisories exist. Patch the
    # git/gh helpers so it stays offline.
    import tools.check_stacked_pr_orphaned_base_advisory as mod

    monkeypatch.setattr(mod, "_open_prs", lambda repo: [
        _pr(1307, "lead/route-stage", "lead/parent", BASE_ORPHANED)
    ])
    monkeypatch.setattr(mod, "_current_main_sha", lambda repo: MAIN)
    monkeypatch.setattr(mod, "_git_is_ancestor", lambda a, d: False)  # orphaned
    rc = main(["--json", "--no-merge-tree"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["advisory_count"] == 1
    assert out["advisories"][0]["number"] == 1307
    assert "orphaned_base" in out["advisories"][0]["hazards"]
