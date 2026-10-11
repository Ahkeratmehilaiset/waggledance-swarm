#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Remove a lane's OWN finished scratch worktrees without losing commits or evidence.

Owner-scoped and evidence-preserving. It encodes the checks used in the 2026-10-11 C:
disk-incident cleanups (claude-rco-2 16:03Z: "owner-checked, no commits lost; named-branch
and dirty worktrees kept"; fable-5 16:15Z: detached, empty status, no ignored content except
caches, HEAD contained in a ref, no --force). A worktree is a removal CANDIDATE only when ALL
of these hold, checked in this order (the first failing check is the reported KEEP reason):

* ``main_worktree`` / ``bare``: never the main or a bare worktree;
* ``not_owner``: the directory name starts with ``<owner>-``, where the owner is this lane's
  ``AGENT_BRIDGE_AGENT``; ``--owner`` may only repeat it, never name another lane. A lane name
  must be ``letters(-letters)*-N`` (fable-5, claude-rco-1, codex-tools-1): its only digit
  segment is the last, so no lane name is a hyphen-prefix of another (``scout`` cannot claim
  ``scout-2-task``);
* ``current_worktree``: not the worktree of ``--repo`` and not the one this runs in, compared
  by file identity (``os.path.samefile``), not by spelling: a junction or symlink spelling
  still matches (tested); subst and 8.3 spellings rely on the same identity but are untested;
* ``missing`` / ``locked``: the directory exists and the worktree is not locked;
* ``named_branch``: HEAD is detached (a worktree on a branch is kept);
* ``head_unreadable`` / ``status_unreadable``: HEAD and ``git status`` are readable;
* ``dirty``: no tracked change and no untracked file;
* ``ignored_content``: the only ignored entries are ``__pycache__/`` and ``.pytest_cache/``
  directories. ``git worktree remove`` deletes ignored files silently, so an ignored
  ``.codex-audit/`` evidence folder (RED, GREEN, superseded records) keeps the worktree;
* ``operation_in_progress`` / ``gitdir_unreadable``: no rebase, merge, cherry-pick, revert,
  bisect or sequencer state in the worktree's git dir (a rebase stopped at ``break`` looks
  clean, and its todo is deleted with the worktree);
* ``hidden_index_flags`` / ``index_unreadable``: no index entry is marked assume-unchanged or
  skip-worktree (a change to such a file does not show in ``git status``);
* ``worktree_refs`` / ``worktree_refs_unreadable``: no per-worktree ref exists
  (``refs/worktree``, ``refs/bisect``, ``refs/rewritten`` are deleted with the worktree);
* ``unreachable_head``: HEAD is contained in a branch, remote-tracking ref or tag
  (``refs/stash`` does not count; it is shared and short-lived);
* ``reflog_only_commits`` / ``reflog_unreadable``: every commit reachable from the worktree's
  HEAD reflog (deleted with the worktree) is also reachable from those refs.

Beyond the 2026-10-11 cleanups (which counted any ref, ``refs/stash`` included) this tool
excludes ``refs/stash`` and adds the lane-name, operation, index-flag, per-worktree-ref and
reflog checks; each of those only keeps more, never removes more.

The default is a dry run. ``--apply`` re-reads ``git status`` and the operation state of each
candidate right before removing it (``changed_before_removal`` keeps a worktree written to
since it was classified), runs ``git worktree remove <path>`` WITHOUT --force, and reports
``removed`` only when the directory is gone. Residual: a write in the milliseconds between
that re-check and git's own deletion is not seen, and another process whose current
directory is inside the worktree can make the removal fail part way (``failed``; the
worktree was clean, so its files are all in git). It never prunes, never touches branches, the
bridge or anything outside ``git worktree remove``, and removes only worktrees named for the
lane in ``AGENT_BRIDGE_AGENT``.

Trust model: the owner is whatever ``AGENT_BRIDGE_AGENT`` says, unauthenticated. Owner scoping
stops a lane from removing another lane's worktrees by mistake, not by impersonation: a process
that sets another lane's name can remove that lane's clean, detached, fully reachable worktrees
(no commit or evidence is lost, but the lane may still be using one).

Exit 0 = done, 1 = a removal failed,
2 = refused (missing or mismatched lane identity, or the worktrees cannot be listed).

    python tools/wd_prune_own_worktrees.py --repo .            # dry run
    python tools/wd_prune_own_worktrees.py --repo . --apply --json

``tools/report_prunable_worktrees.py`` is the read-only report for merged named branches; it
keeps every detached worktree, which is what this tool handles.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from typing import Sequence

SCHEMA = "wd.prune-own-worktrees.v1"
OWNER_ENV = "AGENT_BRIDGE_AGENT"
CACHE_DIR_NAMES = frozenset({"__pycache__", ".pytest_cache"})
_OWNER = re.compile(r"[a-z]+(?:-[a-z]+)*-[0-9]+\Z")
_OPERATION_MARKERS = ("rebase-merge", "rebase-apply", "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD",
                      "BISECT_LOG", "sequencer")
_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_REACHABLE_REF_ROOTS = ("refs/heads", "refs/remotes", "refs/tags")
_PER_WORKTREE_REF_ROOTS = ("refs/worktree", "refs/bisect", "refs/rewritten")
_MAX_REFLOG_COMMITS = 400  # keeps the rev-list command line far below the Windows limit


def _git(args: Sequence[str], cwd: str | None = None) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(["git", "--no-optional-locks", *args], cwd=cwd, capture_output=True,
                              encoding="utf-8", errors="replace")
    except OSError as exc:
        return 1, "", str(exc)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def parse_worktree_list(porcelain_z: str) -> list[dict]:
    """Parse ``git worktree list --porcelain -z`` (NUL-terminated attributes)."""
    entries: list[dict] = []
    current: dict | None = None
    for field in porcelain_z.split("\0"):
        if not field:
            if current is not None:
                entries.append(current)
                current = None
            continue
        key, _, value = field.partition(" ")
        if key == "worktree":
            if current is not None:
                entries.append(current)
            current = {"path": value, "head": None, "branch": None, "detached": False,
                       "bare": False, "locked": False, "prunable": False}
        elif current is None:
            continue
        elif key == "HEAD":
            current["head"] = value
        elif key == "branch":
            current["branch"] = value
        elif key in ("detached", "bare", "locked", "prunable"):
            current[key] = True
    if current is not None:
        entries.append(current)
    return entries


def _toplevel(path: str) -> str | None:
    code, out, _ = _git(["-C", path, "rev-parse", "--show-toplevel"])
    out = out.strip()
    return _norm(out) if code == 0 and out else None


def _status(path: str) -> list[tuple[str, str]] | None:
    code, out, _ = _git(["-C", path, "status", "--porcelain=v1", "-z", "--ignored=traditional",
                         "--untracked-files=normal"])
    if code != 0:
        return None
    entries: list[tuple[str, str]] = []
    fields = out.split("\0")
    index = 0
    while index < len(fields):
        field = fields[index]
        index += 1
        if not field:
            continue
        xy = field[:2]
        entries.append((xy, field[3:]))
        if xy[0] in "RC":  # a rename or copy carries its source path as the next field
            index += 1
    return entries


def _is_cache_dir(path: str) -> bool:
    return path.endswith("/") and path.rstrip("/").rsplit("/", 1)[-1] in CACHE_DIR_NAMES


def _head_in_ref(repo: str, head: str) -> bool:
    code, out, _ = _git(["-C", repo, "for-each-ref", "--contains", head, "--count=1", "--format=%(refname)",
                         *_REACHABLE_REF_ROOTS])
    return code == 0 and bool(out.strip())


def _hidden_index_flags(path: str) -> bool | None:
    """True when an index entry is assume-unchanged (lowercase tag) or skip-worktree (``S``)."""
    code, out, _ = _git(["-C", path, "ls-files", "-v", "-z"])
    if code != 0:
        return None
    return any(field and (field[0] == "S" or field[0].islower()) for field in out.split("\0"))


def _has_worktree_refs(path: str) -> bool | None:
    code, out, _ = _git(["-C", path, "for-each-ref", "--format=%(refname)", *_PER_WORKTREE_REF_ROOTS])
    if code != 0:
        return None
    return bool(out.strip())


def _reflog_only_commits(path: str, repo: str) -> bool | None:
    """True when a commit reachable from the worktree's HEAD reflog is in no branch, remote ref or tag."""
    code, out, _ = _git(["-C", path, "log", "-g", "--format=%H", "HEAD"])
    if code != 0:
        return None
    shas = sorted({line.strip() for line in out.splitlines() if line.strip()})
    if not shas:
        return False
    if len(shas) > _MAX_REFLOG_COMMITS or not all(_SHA.match(sha) for sha in shas):
        return None
    code, out, _ = _git(["-C", repo, "rev-list", "--count", *shas, "--not", "--branches", "--remotes", "--tags"])
    if code != 0 or not out.strip().isdigit():
        return None
    return int(out.strip()) > 0


def _operation_in_progress(path: str) -> bool | None:
    code, out, _ = _git(["-C", path, "rev-parse", "--absolute-git-dir"])
    gitdir = out.strip()
    if code != 0 or not gitdir:
        return None
    return any(os.path.exists(os.path.join(gitdir, marker)) for marker in _OPERATION_MARKERS)


def _worktree_state(path: str) -> str | None:
    """The first KEEP reason in the worktree's own state (status, ignored files, operation), or None."""
    status = _status(path)
    if status is None:
        return "status_unreadable"
    if any(xy != "!!" for xy, _ in status):
        return "dirty"
    if any(not _is_cache_dir(item) for _, item in status):
        return "ignored_content"
    operation = _operation_in_progress(path)
    if operation is None:
        return "gitdir_unreadable"
    if operation:
        return "operation_in_progress"
    return None


def _same_dir(left: str, right: str) -> bool:
    try:
        return os.path.samefile(left, right)
    except OSError:
        return _norm(left) == _norm(right)


def classify(entry: dict, *, index: int, owner: str, protected: Sequence[str], repo: str) -> tuple[str, str]:
    """(decision, reason): ``("candidate", "eligible")`` or ``("keep", <first failing check>)``."""
    path = entry["path"]
    name = os.path.basename(path.rstrip("/\\"))
    if index == 0:
        return "keep", "main_worktree"
    if entry["bare"]:
        return "keep", "bare"
    if not name.startswith(owner + "-"):
        return "keep", "not_owner"
    if any(_same_dir(path, other) for other in protected):
        return "keep", "current_worktree"
    if entry["prunable"] or not os.path.isdir(path):
        return "keep", "missing"
    if entry["locked"]:
        return "keep", "locked"
    if not entry["detached"]:
        return "keep", "named_branch"
    head = entry["head"] or ""
    if not _SHA.match(head):
        return "keep", "head_unreadable"
    state = _worktree_state(path)
    if state is not None:
        return "keep", state
    hidden = _hidden_index_flags(path)
    if hidden is None:
        return "keep", "index_unreadable"
    if hidden:
        return "keep", "hidden_index_flags"
    worktree_refs = _has_worktree_refs(path)
    if worktree_refs is None:
        return "keep", "worktree_refs_unreadable"
    if worktree_refs:
        return "keep", "worktree_refs"
    if not _head_in_ref(repo, head):
        return "keep", "unreachable_head"
    reflog_only = _reflog_only_commits(path, repo)
    if reflog_only is None:
        return "keep", "reflog_unreadable"
    if reflog_only:
        return "keep", "reflog_only_commits"
    return "candidate", "eligible"


def _remove(repo: str, path: str) -> tuple[str, str | None]:
    code, _, err = _git(["-C", repo, "worktree", "remove", path])
    if code == 0 and not os.path.exists(path):
        return "removed", None
    return "failed", (err.strip() or f"git worktree remove exit {code}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=".", help="a path inside the repository (default: .)")
    parser.add_argument("--owner", help=f"must equal {OWNER_ENV}; another lane's name is refused")
    parser.add_argument("--apply", action="store_true", help="remove the candidates (default: dry run)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    lane = os.environ.get(OWNER_ENV, "")
    if not _OWNER.match(lane):
        print(f"refused: {OWNER_ENV} is missing or not a lane name like fable-5 or codex-tools-1 ({lane!r}); "
              "the owner comes from the lane identity", file=sys.stderr)
        return 2
    if args.owner is not None and args.owner != lane:
        print(f"refused: --owner {args.owner!r} is not this lane ({OWNER_ENV}={lane!r}); only your own worktrees",
              file=sys.stderr)
        return 2
    repo_top = _toplevel(args.repo)
    code, listing, err = _git(["-C", args.repo, "worktree", "list", "--porcelain", "-z"])
    if repo_top is None or code != 0:
        print(f"refused: cannot list the worktrees of {args.repo!r}: {err.strip()}", file=sys.stderr)
        return 2
    protected = [repo_top]
    here = _toplevel(os.getcwd())
    if here is not None:
        protected.append(here)

    rows = []
    for index, entry in enumerate(parse_worktree_list(listing)):
        decision, reason = classify(entry, index=index, owner=lane, protected=tuple(protected), repo=args.repo)
        row = {"path": entry["path"], "head": entry["head"], "decision": decision, "reason": reason, "error": None}
        if decision == "candidate" and args.apply:
            if _worktree_state(entry["path"]) is not None:  # written to since it was classified
                row["decision"], row["reason"] = "keep", "changed_before_removal"
            else:
                row["decision"], row["error"] = _remove(args.repo, entry["path"])
        rows.append(row)

    counts = {key: sum(1 for row in rows if row["decision"] == key) for key in ("candidate", "removed", "failed", "keep")}
    report = {"schema": SCHEMA, "owner": lane, "mode": "apply" if args.apply else "dry_run",
              "repo": args.repo, "counts": counts, "worktrees": rows}
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for row in rows:
            name = os.path.basename(row["path"].rstrip("/\\"))
            suffix = f" ({row['error']})" if row["error"] else ""
            print(f"{row['decision'].upper():9} {row['reason']:22} {name}{suffix}")
        if args.apply:
            print(f"apply: removed {counts['removed']}, failed {counts['failed']}, kept {counts['keep']}")
        else:
            print(f"dry run: {counts['candidate']} candidate(s), kept {counts['keep']}; nothing removed (use --apply)")
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
