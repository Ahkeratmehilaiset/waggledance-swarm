#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Bridge v2 F22: tools-owned write-scope resolution with an explanation, parity-first.

Ports the resolution rules of ``waggledance/core/bridge_resource_scope.py`` and
``.agent-bridge/bin/BridgeResourceScope.ps1`` into a Bridge-owned module (no
``waggledance`` import), and removes the two places where the runtimes can disagree:

* case: the legacy Python uses ``str.casefold`` and PowerShell ``ToLowerInvariant``,
  which differ for some non-ASCII letters. Here a scope must be ASCII and only ASCII
  letters are lowered, so both runtimes produce byte-identical results;
* rootedness: PowerShell ``IsPathRooted`` and Python ``is_absolute`` differ for ``\\x``
  and ``C:x``. Here a rooted-but-not-fully-qualified scope is refused outright.

The tools-owned resolver also refuses equal or nested lexical worktree/shared-root
layouts for every entry except ``*``, which overlaps every claim. This fail-closed
policy intentionally differs from the legacy Python and PowerShell resolvers; it
does not prove physical-alias detection or runtime parity. Disjoint and cwd-less
legacy layouts retain their previous handling.

Every entry is split on commas (as PowerShell does). ``explain_scope`` returns what an
entry resolves to or why it is refused, with examples (F22 ``-Explain``). Nothing is
written; the filesystem access is ``lstat`` on the worktree/shared paths to refuse links and
reparse points, through an injectable ``lstat``, plus a bounded read of a ``.git`` or
``commondir`` pointer file when the cwd is a linked worktree (RS7).
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import re
import stat
from typing import Callable, Sequence

KINDS = ("repo", "worktree", "shared")
AUDIT_DIR = ".codex-audit"
CHECKPOINT = ".codex-audit/wd-current-state.json"
_ALIAS_SEGMENT = re.compile(r"(?:[. ]$|~[0-9])")
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:/")
_EXPLICIT_KIND = re.compile(r"^([A-Za-z_-]+):")
EXAMPLES = (
    "tools/bridge_v2_work_queue.py (repo path, repository-logical)",
    "tests/tools (repo directory: every path under it)",
    "worktree:.codex-audit/wd-current-state.json (this worktree's checkpoint only)",
    "shared:work_queue/claims (the shared runtime root, physical)",
    "* (the whole repository)",
)


class ScopeError(ValueError):
    """A scope entry was refused; the message names the rule, never guesses."""


@dataclass(frozen=True)
class ResourceScope:
    kind: str
    path: str
    root: str = ""


def _ascii_lower(value: str) -> str:
    if not value.isascii():
        raise ScopeError("scope must be ASCII: both runtimes must normalize it identically")
    return value.lower()


def _normalize_absolute(path: str, lstat: Callable[[str], os.stat_result]) -> str:
    """A fully qualified local path: forward slashes, ASCII-lowered, no link or reparse point.

    Same result as the legacy rule (absolute path, '/' separators, trailing '/' removed,
    lowered) but refuses UNC, device, drive-relative and root-relative forms outright."""
    text = path.replace("\\", "/")
    if any(part and part != "." and _ALIAS_SEGMENT.search(part) for part in text.split("/")):
        raise ScopeError("ambiguous Windows root alias")
    if os.name == "nt":
        if not _WINDOWS_ABSOLUTE.match(text):
            raise ScopeError("a root must be a local drive-letter path")
        drive, rest = text[:2], text[2:]
        # After the drive, ':' only ever names an NTFS stream (a.py:s; a.py::$DATA IS a.py), which every
        # relative and POSIX-absolute entry already refuses in resolve_entry's colon guard.
        if ":" in rest:
            raise ScopeError("resource traversal or alternate stream is forbidden")
    else:
        if not text.startswith("/") or text.startswith("//"):
            raise ScopeError("a root must be a local absolute path")
        drive, rest = "", text
    parts = [part for part in rest.split("/") if part and part != "."]
    if ".." in parts:
        raise ScopeError("resource traversal or alternate stream is forbidden")
    walked = drive
    for part in parts:
        walked = walked + "/" + part
        try:
            info = lstat(walked)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ScopeError("resource path contains a link/reparse point")
    return _ascii_lower((drive + "/" + "/".join(parts)).rstrip("/"))

def _entries(scopes: Sequence[str] | str) -> list[str]:
    source = (scopes,) if isinstance(scopes, str) else scopes
    result = []
    for scope in source:
        if not isinstance(scope, str):
            raise ScopeError("scope entries must be strings")
        result.extend(entry.strip() for entry in scope.split(",") if entry.strip())
    return result


def _no_lstat(path: str):
    raise FileNotFoundError(path)   # the layout check is lexical: it never reads the disk


def _roots_overlap(worktree: str, bridge_root: str) -> bool:
    """True when the worktree and the shared runtime root are the same directory or one contains the other,
    compared lexically with the same normalization as every scope (the disk is never read). In such a layout one
    physical file can be named both as a repo path and as a shared path, and resources_overlap compares those two
    namespaces by path only, so a real conflict could be missed. An unusable root keeps its previous handling."""
    if not worktree:
        return False
    try:
        base, shared = _normalize_absolute(worktree, _no_lstat), _normalize_absolute(bridge_root, _no_lstat)
    except ScopeError:
        return False
    return base == shared or base.startswith(shared + "/") or shared.startswith(base + "/")


_ABSOLUTE_POINTER = re.compile(r"^(?:[A-Za-z]:)?[/\\]")


def _pointer(path: str) -> str:
    """The first line of a small git pointer file (.git or commondir), surrounding whitespace removed."""
    with open(path, "rb") as handle:
        data = handle.read(4097)
    if len(data) > 4096:
        raise ScopeError("git pointer file is oversized")
    lines = data.decode("utf-8").splitlines()
    return lines[0].strip() if lines else ""


def _require_top_level(worktree: str, lstat: Callable[[str], os.stat_result]) -> None:
    """RS7 (Grok 44ce90e5): scopes are repository-relative and an absolute entry is made relative to the cwd, so a cwd
    below the top level (or outside any repository) names one file two ways and resources_overlap misses the conflict.
    The cwd must be a git top level by git's own discovery test, read from the file system only: "<cwd>/.git" is a
    directory, or a file "gitdir: <dir>" (a linked worktree); that git dir holds HEAD, and its common dir ("commondir",
    else itself) holds objects/ and refs/. An empty marker or a dangling pointer is refused; nothing is guessed."""
    top = worktree.rstrip("/\\")
    try:
        marker = lstat(top + "/.git")
        if stat.S_ISDIR(marker.st_mode):
            git_dir = top + "/.git"
        elif stat.S_ISREG(marker.st_mode):
            line = _pointer(top + "/.git")
            if not line.startswith("gitdir:") or not line[7:].strip():
                raise ScopeError("the .git file is not a gitdir pointer")
            target = line[7:].strip()
            git_dir = target if _ABSOLUTE_POINTER.match(target) else top + "/" + target
            # RS7-L2 (RCO1): git would also follow "gitdir: ../.git" from a subdirectory, so a .git FILE counts only
            # when git's worktree bookkeeping agrees: the admin dir has commondir and its gitdir back-link names
            # this cwd's .git. A pointer at a main .git or at another worktree's admin dir is refused.
            lstat(git_dir + "/commondir")
            back = _pointer(git_dir + "/gitdir")
            back = back if _ABSOLUTE_POINTER.match(back) else git_dir + "/" + back
            if os.path.normcase(os.path.normpath(back)) != os.path.normcase(os.path.normpath(top + "/.git")):
                raise ScopeError("the worktree admin dir's gitdir back-link names another .git")
        else:
            raise ScopeError(".git is neither a directory nor a file")
        common = git_dir
        try:
            lstat(git_dir + "/commondir")
        except FileNotFoundError:
            pass
        else:
            target = _pointer(git_dir + "/commondir")
            common = target if _ABSOLUTE_POINTER.match(target) else git_dir + "/" + target
        if not (stat.S_ISREG(lstat(git_dir + "/HEAD").st_mode) and stat.S_ISDIR(lstat(common + "/objects").st_mode)
                and stat.S_ISDIR(lstat(common + "/refs").st_mode)):
            raise ScopeError("the git dir lacks HEAD, objects or refs")
    except (OSError, ValueError) as error:   # ScopeError is a ValueError; a decode error is one too
        raise ScopeError("the claim cwd is not a repository top level (no valid .git there): every scope except * "
                         "is refused, fail-closed (" + str(error)[:120] + ")") from None


def resolve_entry(entry: str, *, worktree: str, bridge_root: str,
                  lstat: Callable[[str], os.stat_result] = os.lstat) -> ResourceScope:
    raw = entry.replace("\\", "/").strip()
    if not raw:
        raise ScopeError("scope must name a path or the whole repository (*)")
    if raw != "*" and _roots_overlap(worktree, bridge_root):
        # Fail-closed (RCO2 a7169ffe): a nested or equal worktree/shared layout refuses EVERY entry form; only *,
        # which overlaps every claim by resources_overlap's first rule, can never miss a conflict.
        raise ScopeError("the worktree and the shared runtime root overlap (the same directory, or one inside the "
                         "other): every scope except * is refused, fail-closed")
    if raw != "*" and worktree:
        _require_top_level(worktree, lstat)
    kind = "repo"
    explicit = _EXPLICIT_KIND.match(raw)
    if explicit and not _WINDOWS_ABSOLUTE.match(raw):
        kind, raw = raw.split(":", 1)
        kind = _ascii_lower(kind)
        if kind not in KINDS:
            raise ScopeError("unknown resource kind (use repo:, worktree: or shared:)")
    elif _ascii_lower(raw).strip("/") == CHECKPOINT and worktree:
        kind = "worktree"
    parts = raw.split("/")
    if ".." in parts or (":" in raw and not _WINDOWS_ABSOLUTE.match(raw)):
        raise ScopeError("resource traversal or alternate stream is forbidden")
    if any(part and part != "." and _ALIAS_SEGMENT.search(part) for part in parts):
        raise ScopeError("ambiguous Windows path alias")
    rooted = raw.startswith("/") or bool(re.match(r"^[A-Za-z]:", raw))
    if rooted:
        full = _normalize_absolute(raw, lstat)
        base = _normalize_absolute(worktree, lstat) if worktree else ""
        shared = _normalize_absolute(bridge_root, lstat)
        if kind == "shared":   # an explicit shared: absolute path is rooted at the shared root, or refused
            if not full.startswith(shared + "/"):
                raise ScopeError("a shared: absolute scope must be under the shared runtime root")
            raw = full[len(shared) + 1:]
        elif base and full.startswith(base + "/"):
            raw = full[len(base) + 1:]
            if raw == CHECKPOINT:
                kind = "worktree"
        elif full.startswith(shared + "/"):
            kind, raw = "shared", full[len(shared) + 1:]
        else:
            raise ScopeError("absolute scope is outside the worktree/shared root")
    raw = _ascii_lower("/".join(part for part in raw.split("/") if part and part != "."))
    if not raw or ("*" in raw and raw != "*") or "?" in raw:
        raise ScopeError("scope must name a path or the whole repository (*)")
    root = ""
    if kind == "worktree":
        if not worktree or not (raw == AUDIT_DIR or raw.startswith(AUDIT_DIR + "/")):
            raise ScopeError("worktree resources require a cwd and must be under .codex-audit")
        root = _normalize_absolute(worktree, lstat)
        _normalize_absolute(worktree.rstrip("/\\") + "/" + raw, lstat)  # no link inside the target either
    elif kind == "shared":
        root = _normalize_absolute(bridge_root, lstat)
        _normalize_absolute(bridge_root.rstrip("/\\") + "/" + raw, lstat)
    elif worktree and raw != "*":
        _normalize_absolute(worktree.rstrip("/\\") + "/" + raw, lstat)  # as the legacy resolver does
    return ResourceScope(kind, raw, root)


def resolve_scopes(scopes: Sequence[str] | str, *, worktree: str, bridge_root: str,
                   lstat: Callable[[str], os.stat_result] = os.lstat) -> tuple[ResourceScope, ...]:
    return tuple(resolve_entry(entry, worktree=worktree, bridge_root=bridge_root, lstat=lstat)
                 for entry in _entries(scopes))


def explain_scope(entry: str, *, worktree: str, bridge_root: str,
                  lstat: Callable[[str], os.stat_result] = os.lstat) -> dict:
    """F22 -Explain: what one entry resolves to, or why it is refused, with examples."""
    try:
        scope = resolve_entry(entry, worktree=worktree, bridge_root=bridge_root, lstat=lstat)
    except ScopeError as error:
        return {"entry": entry[:256], "accepted": False, "reason": str(error), "examples": list(EXAMPLES)}
    meaning = {"repo": "a repository-logical path: overlaps any claim on the same path in any worktree",
               "worktree": "a physical path inside this worktree's .codex-audit only",
               "shared": "a physical path under the shared runtime root"}[scope.kind]
    return {"entry": entry[:256], "accepted": True, "kind": scope.kind, "path": scope.path,
            "root": scope.root, "meaning": meaning, "examples": list(EXAMPLES)}


def resources_overlap(left: ResourceScope, right: ResourceScope) -> bool:
    """The legacy overlap rule, identical in both runtimes."""
    if left.path == "*" or right.path == "*":
        return True
    if left.kind == "repo" or right.kind == "repo":
        a, b = left.path, right.path  # an old claim without cwd stays conservative
    else:
        a, b = left.root + "/" + left.path, right.root + "/" + right.path
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")
