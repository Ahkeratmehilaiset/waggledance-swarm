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
  ``AGENT_BRIDGE_AGENT``; ``--owner`` may only repeat it, never name another lane;
* ``current_worktree``: not the worktree of ``--repo`` and not the one this runs in;
* ``missing`` / ``locked``: the directory exists and the worktree is not locked;
* ``named_branch``: HEAD is detached (a worktree on a branch is kept);
* ``head_unreadable`` / ``status_unreadable``: HEAD and ``git status`` are readable;
* ``dirty``: no tracked change and no untracked file;
* ``ignored_content``: the only ignored entries are ``__pycache__/`` and ``.pytest_cache/``
  directories. ``git worktree remove`` deletes ignored files silently, so an ignored
  ``.codex-audit/`` evidence folder (RED, GREEN, superseded records) keeps the worktree;
* ``unreachable_head``: HEAD is contained in a branch, remote-tracking ref or tag
  (``refs/stash`` does not count; it is shared and short-lived).

The default is a dry run. ``--apply`` runs ``git worktree remove <path>`` WITHOUT --force for
each candidate right after classifying it, and reports ``removed`` only when the directory is
gone. It never prunes, never touches another lane's worktree, branches, the bridge or
anything outside ``git worktree remove``. Exit 0 = done, 1 = a removal failed,
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
_OWNER = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_REACHABLE_REF_ROOTS = ("refs/heads", "refs/remotes", "refs/tags")


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


def classify(entry: dict, *, index: int, owner: str, protected: frozenset[str], repo: str) -> tuple[str, str]:
    """(decision, reason): ``("candidate", "eligible")`` or ``("keep", <first failing check>)``."""
    path = entry["path"]
    name = os.path.basename(path.rstrip("/\\"))
    if index == 0:
        return "keep", "main_worktree"
    if entry["bare"]:
        return "keep", "bare"
    if not name.startswith(owner + "-"):
        return "keep", "not_owner"
    if _norm(path) in protected:
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
    status = _status(path)
    if status is None:
        return "keep", "status_unreadable"
    if any(xy != "!!" for xy, _ in status):
        return "keep", "dirty"
    if any(not _is_cache_dir(item) for _, item in status):
        return "keep", "ignored_content"
    if not _head_in_ref(repo, head):
        return "keep", "unreachable_head"
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
        print(f"refused: {OWNER_ENV} is missing or not a lane name ({lane!r}); the owner comes from the lane identity",
              file=sys.stderr)
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
    protected = {repo_top}
    here = _toplevel(os.getcwd())
    if here is not None:
        protected.add(here)

    rows = []
    for index, entry in enumerate(parse_worktree_list(listing)):
        decision, reason = classify(entry, index=index, owner=lane, protected=frozenset(protected), repo=args.repo)
        row = {"path": entry["path"], "head": entry["head"], "decision": decision, "reason": reason, "error": None}
        if decision == "candidate" and args.apply:
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
            print(f"{row['decision'].upper():9} {row['reason']:17} {name}{suffix}")
        if args.apply:
            print(f"apply: removed {counts['removed']}, failed {counts['failed']}, kept {counts['keep']}")
        else:
            print(f"dry run: {counts['candidate']} candidate(s), kept {counts['keep']}; nothing removed (use --apply)")
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
