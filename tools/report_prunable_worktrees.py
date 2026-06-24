#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Report git worktrees that are SAFE to prune (RFC P3 / EF4: worktree sprawl).

The swarm accumulates one worktree per task (observed: ~1485 worktree entries /
~584 dirs), most for long-merged tasks. This tool ENUMERATES git worktrees and
classifies each as ``prunable`` (its branch is merged into the base AND the
worktree is clean AND it is not protected) or ``keep`` (with a reason), so an
operator/ops step can reclaim the merged ones.

**READ-ONLY and FAIL-SAFE.** It runs ``git worktree list`` / status / merge
checks and **never deletes anything**. Classification fails *toward KEEP*:
anything uncertain (detached HEAD, unreadable status, missing branch, the main
or current worktree) is reported as keep, never prunable. Deletion is
intentionally out of scope for v1 — the report is the artifact; a guarded
``--apply`` is a separate, operator-gated future increment.

    python tools/report_prunable_worktrees.py --json
    python tools/report_prunable_worktrees.py --base origin/main --worktree-root C:/path
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Sequence


def _norm(path: str) -> str:
    return path.replace("\\", "/").rstrip("/")


def classify_worktree(
    wt: dict,
    *,
    base: str,
    protected: frozenset[str],
) -> dict:
    """Pure classification: decide if a parsed worktree entry is prunable.

    ``wt`` keys: path, branch, head, is_main(bool), detached(bool),
    merged(bool|None), dirty(bool|None). ``merged``/``dirty`` may be None when
    unknown -> treated as fail-safe KEEP. Returns {path, prunable, reason}.
    """
    path = _norm(str(wt.get("path", "")))
    result = {"path": path, "branch": wt.get("branch"), "prunable": False}

    if not path:
        result["reason"] = "missing path"
        return result
    if wt.get("is_main"):
        result["reason"] = "main worktree (protected)"
        return result
    if path in protected:
        result["reason"] = "protected/current worktree"
        return result
    if wt.get("detached"):
        result["reason"] = "detached HEAD (uncertain)"
        return result
    if not wt.get("branch"):
        result["reason"] = "no branch (uncertain)"
        return result
    if wt.get("dirty") is None:
        result["reason"] = "worktree status unreadable (uncertain)"
        return result
    if wt.get("dirty"):
        result["reason"] = "uncommitted changes"
        return result
    if wt.get("merged") is None:
        result["reason"] = f"merge status vs {base} unknown (uncertain)"
        return result
    if not wt.get("merged"):
        result["reason"] = f"branch not merged into {base}"
        return result

    result["prunable"] = True
    result["reason"] = f"merged into {base} and clean"
    return result


def report(worktrees: Sequence[dict], *, base: str, protected: frozenset[str]) -> dict:
    """Classify all worktrees and summarize. Pure over parsed input."""
    classified = [classify_worktree(wt, base=base, protected=protected) for wt in worktrees]
    prunable = [c for c in classified if c["prunable"]]
    keep = [c for c in classified if not c["prunable"]]
    return {
        "base": base,
        "total": len(classified),
        "prunable_count": len(prunable),
        "keep_count": len(keep),
        "prunable": sorted(prunable, key=lambda c: c["path"]),
        "keep": sorted(keep, key=lambda c: c["path"]),
    }


# --- git layer (thin; fail-safe to "unknown") --------------------------------

def _run_git(args: list[str], cwd: str | None = None) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        return proc.returncode, proc.stdout or ""
    except OSError:
        return 1, ""


def _parse_worktree_list(porcelain: str) -> list[dict]:
    """Parse ``git worktree list --porcelain`` into entries."""
    entries: list[dict] = []
    cur: dict = {}
    for line in porcelain.splitlines():
        if not line.strip():
            if cur:
                entries.append(cur)
                cur = {}
            continue
        if line.startswith("worktree "):
            if cur:
                entries.append(cur)
            cur = {"path": line[len("worktree "):].strip(), "detached": False}
        elif line.startswith("branch "):
            ref = line[len("branch "):].strip()
            cur["branch"] = ref.replace("refs/heads/", "", 1)
        elif line.startswith("HEAD "):
            cur["head"] = line[len("HEAD "):].strip()
        elif line.strip() == "detached":
            cur["detached"] = True
        elif line.strip() == "bare":
            cur["bare"] = True
    if cur:
        entries.append(cur)
    return entries


def _is_dirty(path: str) -> bool | None:
    code, out = _run_git(["status", "--porcelain"], cwd=path)
    if code != 0:
        return None  # unreadable -> fail-safe keep
    return bool(out.strip())


def _is_merged(head: str, base: str, repo_cwd: str) -> bool | None:
    if not head:
        return None
    code, _ = _run_git(["merge-base", "--is-ancestor", head, base], cwd=repo_cwd)
    if code == 0:
        return True
    if code == 1:
        return False
    return None  # any other code (e.g. bad ref) -> uncertain -> keep


def gather_worktrees(repo_cwd: str, base: str) -> list[dict]:
    code, out = _run_git(["worktree", "list", "--porcelain"], cwd=repo_cwd)
    if code != 0:
        return []
    entries = _parse_worktree_list(out)
    for i, wt in enumerate(entries):
        wt["is_main"] = i == 0  # git lists the main worktree first
        if wt.get("bare"):
            wt["is_main"] = True
        if not wt.get("detached") and wt.get("branch"):
            wt["dirty"] = _is_dirty(wt["path"])
            wt["merged"] = _is_merged(wt.get("head", ""), base, repo_cwd)
    return entries


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main", help="merge base ref (default origin/main)")
    parser.add_argument("--repo-root", default=".", help="a path inside the git repo")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_cwd = str(Path(args.repo_root))
    worktrees = gather_worktrees(repo_cwd, args.base)
    # Protect the current worktree (where this runs) as a belt-and-suspenders.
    here = _norm(str(Path(repo_cwd).resolve()))
    result = report(worktrees, base=args.base, protected=frozenset({here}))
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"worktrees: {result['total']}  prunable: {result['prunable_count']}  keep: {result['keep_count']}")
        print(f"(read-only report; base={result['base']}; no deletion performed)")
        for c in result["prunable"]:
            print(f"  PRUNABLE  {c['path']}  [{c.get('branch')}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
