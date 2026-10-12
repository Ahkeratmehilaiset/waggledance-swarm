#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Update a reviewed PR head onto a newer base without changing its content, and write a receipt.

Round 5 B2 (2026-10-12): the method used by hand for #1854, #1851, #1853, #1847 and #1859 that day.
Given the reviewed head, its base and a newer base, it builds

    tree = git merge-tree --write-tree --merge-base=<old base> <new base> <old head>

and accepts the tree only when ALL of these hold, checked in this order (the first failing check is
the refusal ``reason``):

* the three names resolve to commits (``unresolvable_commit``); the old head and old base must be
  given as full object names, because they bind to what was reviewed;
* ``old_base_not_ancestor_of_head`` / ``new_base_not_descendant_of_old_base``: the old base is an
  ancestor of both the old head and the new base;
* ``already_contains_new_base``: the old head does not already contain the new base;
* ``old_base_not_merge_base``: the old base is the one and only merge base of the old head and the
  new base, so the old base..old head delta is exactly the PR;
* ``empty_pr_delta``: the PR changes at least one path;
* ``merge_conflict`` / ``merge_tree_failed``: merge-tree exits 0;
* ``paths_overlap``: no PR path is in the old base..new base delta. Any overlap means the base
  touched reviewed content, even when the merge is clean (an identical change on both sides or a
  union merge driver), so it needs fresh review instead of this tool;
* ``delta_changed``: the PR's per-file delta on the new base (new base..tree) equals its delta on
  the old base (old base..old head), entry by entry: path, status, both modes and both object ids
  (``git diff-tree -r -z --no-renames``). This catches what overlap cannot, for example merge-ort
  moving a new PR file along a directory renamed on the base;
* ``patch_id_changed``: ``git patch-id --stable`` of both deltas is equal (``git diff`` with fixed
  options, no renames); with equal per-file deltas this is a cross-check.

Then ``git commit-tree <tree> -p <old head> -p <new base>`` makes the new head. The reviewed head is
the first parent, so pushing the new head is a fast-forward of the reviewed head and the reviewed
commit stays reachable. Only objects are written: no branch, tag, index or worktree changes.

``--push`` (with ``--remote`` and ``--branch``) then runs a plain ``git push <remote>
<new head>:refs/heads/<branch>`` with no force flag, and only when ``git ls-remote`` shows that
branch still at the reviewed head (``remote_tip_mismatch`` otherwise; nothing is pushed). It is
verified by polling ``ls-remote`` every ``--verify-interval`` seconds (default 15) for up to
``--verify-timeout`` seconds (default 180, CLAUDE.md rule 7); a push whose result is not seen on
the remote in that time is ``push_unverified`` (exit 4), never retried.

Every run that gets past argument checks writes a receipt (schema ``wd.pr-base-update-receipt.v1``)
to ``--receipt``, also on refusal; an existing file is never overwritten. The receipt binds the old
and new heads and bases, the tree, both patch-ids, the per-file entries, the overlap, the git
version and this tool's sha256, so a consumer can re-derive the result. Exit codes: 0 updated or
pushed, 2 usage, 3 refused, 4 push not verified.

Not carry-forward: equal content does not prove equal behaviour (main can change a callee of
unchanged PR code), so CI must run again at the new head. Whether approvals carry forward is the
merge gate's decision, which is (a)-class. This tool is (b)-class: nothing in the repository calls
it, and wiring it into the gate or a scheduled path makes it (a)-class.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from typing import Sequence

SCHEMA = "wd.pr-base-update-receipt.v1"
EXIT_OK, EXIT_USAGE, EXIT_REFUSED, EXIT_PUSH_UNVERIFIED = 0, 2, 3, 4
_OBJECT_NAME = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_RAW_META = re.compile(r"^:(\d{6}) (\d{6}) ([0-9a-f]+) ([0-9a-f]+) ([A-Z])$")
# git diff for patch-ids: every option that changes the text of a diff is pinned
_PATCH_DIFF = ["-c", "diff.noprefix=false", "-c", "diff.mnemonicPrefix=false", "-c", "core.quotePath=true",
               "diff", "--no-color", "--no-ext-diff", "--no-textconv", "--no-renames", "--binary", "--full-index",
               "--src-prefix=a/", "--dst-prefix=b/"]


class Refused(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _git(repo: str, args: Sequence[str], stdin: bytes | None = None) -> tuple[int, bytes]:
    try:
        proc = subprocess.run(["git", "--no-optional-locks", "-C", repo, *args], input=stdin,
                              capture_output=True)
    except OSError:
        return 127, b""
    return proc.returncode, proc.stdout


def _text(out: bytes) -> str:
    return out.decode("utf-8", errors="replace").strip()


def _resolve(repo: str, name: str) -> str | None:
    if not name or name.startswith("-"):
        return None
    code, out = _git(repo, ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{name}^{{commit}}"])
    value = _text(out)
    return value if code == 0 and _OBJECT_NAME.match(value) else None


def _is_ancestor(repo: str, ancestor: str, descendant: str) -> bool:
    code, _ = _git(repo, ["merge-base", "--is-ancestor", ancestor, descendant])
    if code not in (0, 1):
        raise Refused("git_failed")
    return code == 0


def _delta(repo: str, old: str, new: str) -> dict[str, dict]:
    """Per-file delta between two tree-ishes: path -> status, modes and object ids, no renames."""
    code, out = _git(repo, ["diff-tree", "-r", "-z", "--no-renames", "--no-commit-id", old, new])
    if code != 0:
        raise Refused("git_failed")
    fields = out.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 2:
        raise Refused("git_failed")
    delta = {}
    for meta, path in zip(fields[::2], fields[1::2]):
        match = _RAW_META.match(meta.decode("ascii", errors="replace"))
        if not match:
            raise Refused("git_failed")
        old_mode, new_mode, old_oid, new_oid, status = match.groups()
        delta[path.decode("utf-8", errors="surrogateescape")] = {
            "status": status, "old_mode": old_mode, "new_mode": new_mode, "old_oid": old_oid, "new_oid": new_oid}
    return delta


def _patch_id(repo: str, old: str, new: str) -> str | None:
    code, diff = _git(repo, [*_PATCH_DIFF, old, new])
    if code != 0:
        raise Refused("git_failed")
    code, out = _git(repo, ["patch-id", "--stable"], stdin=diff)
    if code != 0:
        raise Refused("git_failed")
    value = _text(out).split()
    return value[0] if value else None


def _push_argv(remote: str, new_head: str, branch: str) -> list[str]:
    return ["push", remote, f"{new_head}:refs/heads/{branch}"]


def _remote_tip(repo: str, remote: str, branch: str) -> str | None:
    ref = f"refs/heads/{branch}"
    code, out = _git(repo, ["ls-remote", remote, ref])
    if code != 0:
        return None
    tips = [line.split("\t") for line in _text(out).splitlines() if "\t" in line]
    exact = [oid for oid, name in tips if name == ref]
    return exact[0] if len(exact) == 1 else None


def _tool_sha256() -> str:
    with open(__file__, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def update(repo: str, old_head: str, old_base: str, new_base: str, receipt: dict,
           message: str | None = None) -> None:
    """Fill RECEIPT; raise Refused on the first failing check. Writes only objects."""
    resolved = {key: _resolve(repo, value) for key, value in
                (("old_head", old_head), ("old_base", old_base), ("new_base", new_base))}
    receipt.update(resolved)
    if None in resolved.values() or resolved["old_head"] != old_head or resolved["old_base"] != old_base:
        raise Refused("unresolvable_commit")
    head, base, onto = resolved["old_head"], resolved["old_base"], resolved["new_base"]
    if not _is_ancestor(repo, base, head):
        raise Refused("old_base_not_ancestor_of_head")
    if not _is_ancestor(repo, base, onto):
        raise Refused("new_base_not_descendant_of_old_base")
    if _is_ancestor(repo, onto, head):
        raise Refused("already_contains_new_base")
    code, out = _git(repo, ["merge-base", "--all", head, onto])
    if code != 0 or _text(out).split() != [base]:
        raise Refused("old_base_not_merge_base")
    pr = _delta(repo, base, head)
    receipt["pr_paths"] = sorted(pr)
    if not pr:
        raise Refused("empty_pr_delta")
    base_delta = _delta(repo, base, onto)
    receipt["base_delta_path_count"] = len(base_delta)
    receipt["overlap_paths"] = sorted(set(pr) & set(base_delta))
    code, out = _git(repo, ["merge-tree", "--write-tree", "--no-messages", f"--merge-base={base}", onto, head])
    receipt["merge_tree_exit"] = code
    if code == 1:
        raise Refused("merge_conflict")
    tree = _text(out).split()[0] if _text(out) else ""
    if code != 0 or not _OBJECT_NAME.match(tree):
        raise Refused("merge_tree_failed")
    receipt["tree"] = tree
    if receipt["overlap_paths"]:
        raise Refused("paths_overlap")
    carried = _delta(repo, onto, tree)
    receipt["entries"] = [{"path": path, "old": pr.get(path), "new": carried.get(path)}
                          for path in sorted(set(pr) | set(carried))]
    if carried != pr:
        raise Refused("delta_changed")
    receipt["patch_id_old"] = _patch_id(repo, base, head)
    receipt["patch_id_new"] = _patch_id(repo, onto, tree)
    if receipt["patch_id_old"] != receipt["patch_id_new"]:
        raise Refused("patch_id_changed")
    text = message or (
        f"Merge new base {onto[:12]} into reviewed head {head[:12]}\n\n"
        f"Content-identical base update (tools/wd_pr_base_update.py): tree {tree},\n"
        f"stable patch-id {receipt['patch_id_new']} on both bases; the reviewed head is the first parent.\n")
    code, out = _git(repo, ["commit-tree", tree, "-p", head, "-p", onto, "-F", "-"], stdin=text.encode("utf-8"))
    new_head = _text(out)
    if code != 0 or not _OBJECT_NAME.match(new_head):
        raise Refused("commit_failed")
    code, out = _git(repo, ["rev-list", "--parents", "-n", "1", new_head])
    _, tree_out = _git(repo, ["rev-parse", f"{new_head}^{{tree}}"])
    if code != 0 or _text(out).split() != [new_head, head, onto] or _text(tree_out) != tree:
        raise Refused("commit_verification_failed")
    receipt["new_head"] = new_head
    receipt["parents"] = [head, onto]
    receipt["status"], receipt["reason"] = "updated", "content_identical"


def push(repo: str, remote: str, branch: str, receipt: dict, interval: float, timeout: float) -> None:
    """Fast-forward REMOTE's BRANCH from the reviewed head to the new head; never forced."""
    head, new_head = receipt["old_head"], receipt["new_head"]
    before = _remote_tip(repo, remote, branch)
    argv = _push_argv(remote, new_head, branch)
    receipt["push"] = {"remote": remote, "branch": branch, "remote_tip_before": before, "argv": argv}
    if before != head:
        raise Refused("remote_tip_mismatch")
    code, _ = _git(repo, argv)
    receipt["push"]["push_exit"] = code
    started = time.monotonic()
    after = _remote_tip(repo, remote, branch)
    while after != new_head and time.monotonic() - started < timeout:
        time.sleep(interval)
        after = _remote_tip(repo, remote, branch)
    receipt["push"].update(remote_tip_after=after, verified=after == new_head,
                           waited_seconds=round(time.monotonic() - started, 3))
    if after == new_head:
        receipt["status"], receipt["reason"] = "pushed", "fast_forward_verified"
    else:
        receipt["status"], receipt["reason"] = "push_unverified", "remote_not_at_new_head"


def _usage(parser: argparse.ArgumentParser, args: argparse.Namespace) -> str | None:
    for flag, value in (("--old-head", args.old_head), ("--old-base", args.old_base)):
        if not _OBJECT_NAME.match(value or ""):
            return f"{flag} must be a full lower-case object name"
    if not args.new_base or args.new_base.startswith("-"):
        return "--new-base is required"
    if os.path.lexists(args.receipt):
        return f"receipt exists, not overwritten: {args.receipt}"
    if args.push:
        if not args.remote or args.remote.startswith("-") or not args.branch:
            return "--push needs --remote and --branch"
        if args.branch[0] in "+-" or ":" in args.branch:
            return f"unsafe branch name: {args.branch}"
        code, _ = _git(args.repo, ["check-ref-format", f"refs/heads/{args.branch}"])
        if code != 0:
            return f"invalid branch name: {args.branch}"
        if args.verify_interval <= 0 or args.verify_timeout < 0:
            return "--verify-interval must be > 0 and --verify-timeout >= 0"
    elif args.remote or args.branch:
        return "--remote and --branch need --push"
    return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", default=".", help="a path inside the repository (default: .)")
    parser.add_argument("--old-head", required=True, help="the reviewed PR head (full object name)")
    parser.add_argument("--old-base", required=True, help="the base it was reviewed on (full object name)")
    parser.add_argument("--new-base", required=True, help="the newer base, for example origin/main")
    parser.add_argument("--receipt", required=True, help="receipt path; must not exist")
    parser.add_argument("--message", help="commit message (default: generated)")
    parser.add_argument("--push", action="store_true", help="fast-forward --remote --branch to the new head")
    parser.add_argument("--remote")
    parser.add_argument("--branch")
    parser.add_argument("--verify-interval", type=float, default=15.0)
    parser.add_argument("--verify-timeout", type=float, default=180.0)
    parser.add_argument("--json", action="store_true", help="print the receipt")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_USAGE if exc.code else EXIT_OK
    problem = _usage(parser, args)
    if problem:
        print(f"wd_pr_base_update: {problem}", file=sys.stderr)
        return EXIT_USAGE
    _, version = _git(args.repo, ["version"])
    receipt: dict = {
        "schema": SCHEMA, "status": "refused", "reason": None, "created_utc": _now(),
        "repo": os.path.abspath(args.repo), "git_version": _text(version),
        "tool": "tools/wd_pr_base_update.py", "tool_sha256": _tool_sha256(),
        "old_head": args.old_head, "old_base": args.old_base, "new_base": None, "new_base_input": args.new_base,
        "merge_tree_exit": None, "tree": None, "new_head": None, "parents": None,
        "pr_paths": None, "base_delta_path_count": None, "overlap_paths": None, "entries": None,
        "patch_id_old": None, "patch_id_new": None, "push": None}
    try:
        update(args.repo, args.old_head, args.old_base, args.new_base, receipt, args.message)
        if args.push:
            push(args.repo, args.remote, args.branch, receipt, args.verify_interval, args.verify_timeout)
    except Refused as refusal:
        receipt["status"], receipt["reason"] = "refused", refusal.reason
    receipt["finished_utc"] = _now()
    body = json.dumps(receipt, indent=1, ensure_ascii=True) + "\n"
    try:
        with open(args.receipt, "x", encoding="ascii", newline="\n") as handle:
            handle.write(body)
    except OSError as exc:
        print(f"wd_pr_base_update: receipt not written: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if args.json:
        sys.stdout.write(body)
    else:
        digest = hashlib.sha256(body.encode("ascii")).hexdigest()
        print(f"{receipt['status']} {receipt['reason']} new_head={receipt['new_head']} "
              f"receipt={args.receipt} sha256={digest}")
    if receipt["status"] in ("updated", "pushed"):
        return EXIT_OK
    return EXIT_PUSH_UNVERIFIED if receipt["status"] == "push_unverified" else EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
