#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Leaf-transition review-reuse ADVISORY checker (convergence plan S5).

DORMANT and UNWIRED: nothing imports this module; it is not a gate input, not an
approval, not an RCO verdict. Its output always carries ``authority_effect: none``
and ``approval: unknown``. The merge gate stays head-exact (CLAUDE.md 9a).

Question it answers for a composition PLAN (a base commit plus leaves taken from
other commits, before anything is committed): is every changed leaf byte-identical
(blob id AND file mode) to the end of a contiguous chain of transitions in the
caller-supplied reviewed heads, starting from the entry the base holds?

The reviewed-head list is caller-supplied evidence, NOT authenticated reviews.

Per-path verdicts (fail-closed; only REUSE and UNCHANGED can yield REUSE_ALL):
  REUSE              base entry -> target entry through reviewed transitions
  UNCHANGED          the leaf does not change the base entry
  UNREVIEWED         no reviewed head changes this path
  UNREVIEWED_EXTRA   a path the caller says the composition also changes
  BASE_DRIFT         the base entry is the start of no reviewed transition
  TARGET_UNREVIEWED  the target entry is the end of no reviewed transition
  CHAIN_BROKEN       both ends are reviewed, but no contiguous chain links them
  FINAL_LF_ONLY      target bytes differ from a reachable reviewed blob only by one
                     final LF: still a delta review, never reuse
  PATH_NOT_FOUND     the path is absent at both the base and the leaf source
  TARGET_UNRESOLVED  a proposed blob id is missing or not a blob object
  UNSUPPORTED_ENTRY  a tree or gitlink entry where a file is expected

PowerShell dependency check: a bounded regex approximation over function names
(case-insensitive). It can report ``missing`` or ``unknown`` (both block) or
``no_missing_found``; closure is always ``not_proven``.

Object access: only validated 40-hex ids are written to the stdin of one
``git --no-replace-objects -C <repo> cat-file --batch`` process (no revision
syntax, no pathspec, no caller text in argv, inherited GIT_* variables dropped).
Trees and commits are parsed from raw object bytes; nothing is written.

    python tools/bridge_leaf_reuse.py --repo <path> --input-json <plan.json>

plan.json: {"base": oid, "leaves": {path: source_commit_oid | {"blob": oid, "mode": "100644"}},
            "reviewed_heads": [oid, ...], "extra_paths": [path, ...]}
Exit codes: 0 report printed (read ``overall``; the exit code carries no verdict);
2 invalid invocation or plan file.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import deque
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

SCHEMA = "bridge_leaf_reuse_advisory_v1"
ABSENT = "absent"
REUSE_ALL = "REUSE_ALL"
NO_CHANGE = "NO_CHANGE"
DELTA_REVIEW_REQUIRED = "DELTA_REVIEW_REQUIRED"

_OID = re.compile(r"\A[0-9a-f]{40}\Z")
_BLOB_MODES = frozenset({"100644", "100755", "120000"})
_TREE_MODE = "40000"
_PS_SUFFIXES = (".ps1", ".psm1")
_DEP_SCOPE = ".agent-bridge/bin/"
_FUNC_DEF = re.compile(
    r"(?im)^[ \t]*(?:function|filter)[ \t]+(?:(?:global|script|local|private):)?"
    r"([A-Za-z][A-Za-z0-9]*-[A-Za-z0-9]+)\b"
)
_FUNC_REF = re.compile(r"(?i)(?<![\w-])([A-Za-z][A-Za-z0-9]*-[A-Za-z0-9]+)(?![\w-])")
LIMITS = (
    "reviewed heads are caller-supplied evidence, not authenticated reviews",
    "dependency check covers PowerShell function names only (regex approximation); "
    "Python imports, dot-sourcing, modules and dynamic calls are not checked",
    "merge commits are refused as reviewed heads",
    "SHA-1 object ids only",
)

Entry = Union[str, Tuple[str, str]]  # ABSENT or (mode, oid)


class LeafReuseError(Exception):
    """Invalid input or unreadable object: the assessment fails closed."""


class ReaderError(LeafReuseError):
    """The object reader failed (process, protocol or I/O)."""


def _validate_oid(value: Any, what: str) -> str:
    if not isinstance(value, str) or not _OID.match(value):
        raise LeafReuseError("%s must be a full lower-case 40-hex object id" % what)
    return value


def _validate_path(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise LeafReuseError("path must be a non-empty string")
    if value.startswith("/") or value.endswith("/") or "\\" in value:
        raise LeafReuseError("path must be repo-relative with forward slashes: %r" % value)
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise LeafReuseError("path contains control characters: %r" % value)
    for part in value.split("/"):
        if part in ("", ".", ".."):
            raise LeafReuseError("path has an empty, '.' or '..' component: %r" % value)
    try:
        value.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise LeafReuseError("path is not encodable as UTF-8: %r" % value) from exc
    return value


class GitObjectReader:
    """Read-only object store backed by one ``git cat-file --batch`` process."""

    def __init__(self, repo: Union[str, os.PathLike], git: str = "git") -> None:
        self.argv = [git, "--no-replace-objects", "-C", os.fspath(repo), "cat-file", "--batch"]
        self._proc: Optional[subprocess.Popen] = None
        self._cache: Dict[str, Optional[Tuple[str, bytes]]] = {}

    def __enter__(self) -> "GitObjectReader":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.stdin.close()
            except OSError:
                pass
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            proc.stdout.close()

    def _start(self) -> subprocess.Popen:
        if self._proc is None:
            env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
            try:
                self._proc = subprocess.Popen(
                    self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, env=env,
                )
            except OSError as exc:
                raise ReaderError("cannot start git cat-file: %s" % exc) from exc
        return self._proc

    def read(self, oid: str) -> Optional[Tuple[str, bytes]]:
        """(type, raw bytes) for an existing object, None for a missing one."""
        _validate_oid(oid, "object id")
        if oid in self._cache:
            return self._cache[oid]
        proc = self._start()
        try:
            proc.stdin.write(oid.encode("ascii") + b"\n")
            proc.stdin.flush()
            header = proc.stdout.readline()
        except OSError as exc:
            raise ReaderError("git cat-file I/O failed: %s" % exc) from exc
        if not header.endswith(b"\n"):
            raise ReaderError("git cat-file ended without a response for %s" % oid)
        parts = header[:-1].split(b" ")
        if parts == [oid.encode("ascii"), b"missing"]:
            self._cache[oid] = None
            return None
        if len(parts) != 3 or parts[0] != oid.encode("ascii") or not parts[2].isdigit():
            raise ReaderError("unexpected git cat-file header for %s" % oid)
        size = int(parts[2])
        data = proc.stdout.read(size)
        trailer = proc.stdout.read(1)
        if len(data) != size or trailer != b"\n":
            raise ReaderError("short git cat-file read for %s" % oid)
        result = (parts[1].decode("ascii"), data)
        self._cache[oid] = result
        return result


class _Objects:
    """Typed views over a store with ``read(oid) -> (type, bytes) | None``."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self._trees: Dict[str, Dict[bytes, Tuple[str, str]]] = {}

    def get(self, oid: str, want: str) -> bytes:
        found = self.store.read(_validate_oid(oid, want + " id"))
        if found is None:
            raise LeafReuseError("%s %s not found" % (want, oid))
        kind, data = found
        if kind != want:
            raise LeafReuseError("object %s is a %s, not a %s" % (oid, kind, want))
        return data

    def commit(self, oid: str) -> Tuple[str, List[str]]:
        tree, parents = None, []
        for line in self.get(oid, "commit").split(b"\n"):
            if tree is None:
                if not line.startswith(b"tree "):
                    raise LeafReuseError("commit %s has no leading tree line" % oid)
                tree = _validate_oid(line[5:].decode("ascii", "replace"), "tree id")
            elif line.startswith(b"parent "):
                parents.append(_validate_oid(line[7:].decode("ascii", "replace"), "parent id"))
            else:
                break
        if tree is None:
            raise LeafReuseError("commit %s has no tree" % oid)
        return tree, parents

    def tree(self, oid: str) -> Dict[bytes, Tuple[str, str]]:
        if oid not in self._trees:
            data, pos, entries = self.get(oid, "tree"), 0, {}
            while pos < len(data):
                space = data.find(b" ", pos)
                nul = data.find(b"\0", space + 1)
                if space < 0 or nul < 0 or nul + 21 > len(data):
                    raise LeafReuseError("malformed tree object %s" % oid)
                mode = data[pos:space].decode("ascii", "replace")
                entries[data[space + 1:nul]] = (mode, data[nul + 1:nul + 21].hex())
                pos = nul + 21
            self._trees[oid] = entries
        return self._trees[oid]

    def entry(self, commit_oid: str, path: str) -> Entry:
        tree_oid, _ = self.commit(commit_oid)
        parts = path.encode("utf-8").split(b"/")
        for i, name in enumerate(parts):
            found = self.tree(tree_oid).get(name)
            if found is None:
                return ABSENT
            if i == len(parts) - 1:
                return found
            if found[0] != _TREE_MODE:
                return ABSENT
            tree_oid = found[1]
        return ABSENT  # pragma: no cover

    def diff(self, a: Optional[str], b: Optional[str], prefix: bytes = b"") -> Dict[bytes, Tuple[Entry, Entry]]:
        """{path: (before, after)} for every non-tree entry that differs between trees a and b (None = empty)."""
        out: Dict[bytes, Tuple[Entry, Entry]] = {}
        if a == b:
            return out
        ea = self.tree(a) if a else {}
        eb = self.tree(b) if b else {}
        for name in sorted(set(ea) | set(eb)):
            x, y = ea.get(name), eb.get(name)
            if x == y:
                continue
            path = prefix + name
            x_tree = x is not None and x[0] == _TREE_MODE
            y_tree = y is not None and y[0] == _TREE_MODE
            if x_tree or y_tree:
                out.update(self.diff(x[1] if x_tree else None, y[1] if y_tree else None, path + b"/"))
                if x is not None and not x_tree:
                    out[path] = (x, ABSENT)
                if y is not None and not y_tree:
                    out[path] = (ABSENT, y)
            else:
                out[path] = (x if x is not None else ABSENT, y if y is not None else ABSENT)
        return out

    def flatten(self, tree_oid: Optional[str], prefix: bytes = b"") -> Dict[bytes, Tuple[str, str]]:
        out: Dict[bytes, Tuple[str, str]] = {}
        for path, (_, after) in self.diff(None, tree_oid, prefix).items():
            out[path] = after  # type: ignore[assignment]
        return out

    def subtree(self, commit_oid: str, path: str) -> Optional[str]:
        found = self.entry(commit_oid, path)
        return found[1] if found != ABSENT and found[0] == _TREE_MODE else None


def _render(entry: Entry) -> Any:
    return ABSENT if entry == ABSENT else {"mode": entry[0], "oid": entry[1]}


def _transitions(objs: _Objects, heads: Sequence[str]) -> Dict[bytes, List[Dict[str, Any]]]:
    trans: Dict[bytes, List[Dict[str, Any]]] = {}
    for head in heads:
        tree, parents = objs.commit(head)
        if len(parents) > 1:
            raise LeafReuseError("reviewed head %s is a merge commit (unsupported)" % head)
        parent_tree = objs.commit(parents[0])[0] if parents else None
        for path, (before, after) in objs.diff(parent_tree, tree).items():
            trans.setdefault(path, []).append(
                {"from": before, "to": after, "head": head, "root": not parents})
    return trans


def _chain(trans: Sequence[Mapping[str, Any]], start: Entry, target: Entry) -> Optional[List[str]]:
    prev: Dict[Entry, Optional[Tuple[Entry, str]]] = {start: None}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        if node == target:
            heads: List[str] = []
            while prev[node] is not None:
                node, head = prev[node]  # type: ignore[misc]
                heads.append(head)
            return heads[::-1]
        for t in trans:
            if t["from"] == node and t["to"] not in prev:
                prev[t["to"]] = (node, t["head"])
                queue.append(t["to"])
    return None


def _final_lf_only(objs: _Objects, target: Entry, candidates: Iterable[Entry]) -> bool:
    if target == ABSENT:
        return False
    tdata = objs.get(target[1], "blob")
    for cand in candidates:
        if cand == ABSENT or cand[0] != target[0] or cand == target:
            continue
        cdata = objs.get(cand[1], "blob")
        if tdata == cdata + b"\n" or cdata == tdata + b"\n":
            return True
    return False


def _ps_text(data: bytes) -> Optional[str]:
    try:
        return data.decode("utf-8-sig", "strict")
    except UnicodeDecodeError:
        return None


def _is_ps(path: bytes) -> bool:
    return path.lower().endswith(tuple(s.encode() for s in _PS_SUFFIXES))


def _ps_files(objs: _Objects, commit_oid: str) -> Dict[bytes, Tuple[str, str]]:
    sub = objs.subtree(commit_oid, _DEP_SCOPE.rstrip("/"))
    files = objs.flatten(sub, _DEP_SCOPE.encode()) if sub else {}
    return {p: e for p, e in files.items() if _is_ps(p) and e[0] in _BLOB_MODES}


def _dependency_check(objs: _Objects, base: str, heads: Sequence[str],
                      composed: Mapping[bytes, Entry]) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "method": "regex_approximation_powershell_function_names",
        "closure": "not_proven", "status": "not_applicable", "missing": [], "unknown": [],
    }
    leaves = {p: e for p, e in composed.items() if _is_ps(p) and e != ABSENT}
    if not leaves:
        return result

    def defs_of(files: Mapping[bytes, Tuple[str, str]], where: str) -> Dict[str, str]:
        names: Dict[str, str] = {}
        for path, (_, oid) in sorted(files.items()):
            text = _ps_text(objs.get(oid, "blob"))
            if text is None:
                result["unknown"].append({"path": path.decode("utf-8", "replace"), "where": where,
                                          "reason": "not strict UTF-8"})
                continue
            for name in _FUNC_DEF.findall(text):
                names.setdefault(name.casefold(), name)
        return names

    reviewed: Dict[str, str] = {}
    for head in heads:
        for key, name in defs_of(_ps_files(objs, head), head).items():
            reviewed.setdefault(key, name)
    tree_files = dict(_ps_files(objs, base))
    for path, entry in composed.items():
        if entry == ABSENT:
            tree_files.pop(path, None)
        elif _is_ps(path):
            tree_files[path] = entry  # type: ignore[assignment]
    present = defs_of(tree_files, "composed")
    for path, entry in sorted(leaves.items()):
        text = _ps_text(objs.get(entry[1], "blob"))
        if text is None:
            continue  # already listed as unknown via the composed tree
        refs = {r.casefold() for r in _FUNC_REF.findall(text)}
        for key in sorted(refs & set(reviewed) - set(present)):
            result["missing"].append({"leaf": path.decode("utf-8"), "function": reviewed[key]})
    result["status"] = "unknown" if result["unknown"] else ("missing" if result["missing"] else "no_missing_found")
    return result


def assess(store: Any, base: str, leaves: Mapping[str, Any], reviewed_heads: Sequence[str],
           extra_paths: Sequence[str] = ()) -> Dict[str, Any]:
    """Advisory assessment of a composition plan. Never raises; errors fail closed."""
    report: Dict[str, Any] = {
        "schema": SCHEMA, "advisory_only": True, "authority_effect": "none", "approval": "unknown",
        "review_evidence": "caller_supplied_unauthenticated", "base": None, "reviewed_heads": [],
        "paths": {}, "dependency_check": None, "overall": DELTA_REVIEW_REQUIRED, "limits": list(LIMITS),
    }
    try:
        objs = _Objects(store)
        base = _validate_oid(base, "base")
        heads = [_validate_oid(h, "reviewed head") for h in reviewed_heads]
        heads = list(dict.fromkeys(heads))
        if not isinstance(leaves, Mapping) or not leaves:
            raise LeafReuseError("leaves must be a non-empty mapping")
        specs = {_validate_path(p): s for p, s in leaves.items()}
        extras = [_validate_path(p) for p in extra_paths]
        if set(extras) & set(specs) or len(set(extras)) != len(extras):
            raise LeafReuseError("extra_paths overlap the leaves or repeat")
        for spec in specs.values():
            if isinstance(spec, Mapping):
                _validate_oid(spec.get("blob"), "proposed blob")
            else:
                _validate_oid(spec, "leaf source commit")
        objs.commit(base)
        report["base"], report["reviewed_heads"] = base, heads
        trans = _transitions(objs, heads)
        composed: Dict[bytes, Entry] = {}
        for path, spec in specs.items():
            base_entry = objs.entry(base, path)
            row: Dict[str, Any] = {"base_entry": _render(base_entry)}
            report["paths"][path] = row
            if isinstance(spec, Mapping):
                found = store.read(spec["blob"])
                if found is None or found[0] != "blob" or spec.get("mode") not in _BLOB_MODES:
                    row.update(verdict="TARGET_UNRESOLVED", target_entry=None)
                    continue
                target: Entry = (spec["mode"], spec["blob"])
            else:
                target = objs.entry(spec, path)
            row["target_entry"] = _render(target)
            if (target != ABSENT and target[0] not in _BLOB_MODES) or (
                    base_entry != ABSENT and base_entry[0] not in _BLOB_MODES):
                row["verdict"] = "UNSUPPORTED_ENTRY"
                continue
            composed[path.encode("utf-8")] = target
            ts = trans.get(path.encode("utf-8"), [])
            if base_entry == ABSENT and target == ABSENT:
                row["verdict"] = "PATH_NOT_FOUND"
            elif base_entry == target:
                row["verdict"] = "UNCHANGED"
            elif not ts:
                row["verdict"] = "UNREVIEWED"
            else:
                chain = _chain(ts, base_entry, target)
                if chain is not None:
                    row.update(verdict="REUSE", chain=chain)
                elif _final_lf_only(objs, target, [t["to"] for t in ts if _chain(ts, base_entry, t["to"]) is not None]):
                    row["verdict"] = "FINAL_LF_ONLY"
                elif all(t["from"] != base_entry for t in ts):
                    row["verdict"] = "BASE_DRIFT"
                elif all(t["to"] != target for t in ts):
                    row["verdict"] = "TARGET_UNREVIEWED"
                else:
                    row["verdict"] = "CHAIN_BROKEN"
        for path in extras:
            report["paths"][path] = {"verdict": "UNREVIEWED_EXTRA",
                                     "base_entry": _render(objs.entry(base, path)), "target_entry": None}
        deps = _dependency_check(objs, base, heads, composed)
        report["dependency_check"] = deps
        verdicts = [row["verdict"] for row in report["paths"].values()]
        if deps["status"] in ("missing", "unknown") or any(v not in ("REUSE", "UNCHANGED") for v in verdicts):
            report["overall"] = DELTA_REVIEW_REQUIRED
        elif all(v == "UNCHANGED" for v in verdicts):
            report["overall"] = NO_CHANGE
        else:
            report["overall"] = REUSE_ALL
    except Exception as exc:  # fail closed: any error requires a delta review
        report["error"] = "%s: %s" % (type(exc).__name__, exc)
        report["overall"] = DELTA_REVIEW_REQUIRED
    return report


def assess_repo(repo: Union[str, os.PathLike], base: str, leaves: Mapping[str, Any],
                reviewed_heads: Sequence[str], extra_paths: Sequence[str] = ()) -> Dict[str, Any]:
    with GitObjectReader(repo) as reader:
        return assess(reader, base, leaves, reviewed_heads, extra_paths)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Advisory leaf-transition review-reuse report (no authority).")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--input-json", required=True)
    args = parser.parse_args(argv)
    try:
        with open(args.input_json, "r", encoding="utf-8") as handle:
            plan = json.load(handle)
        if not isinstance(plan, dict):
            raise ValueError("plan must be a JSON object")
        unknown = set(plan) - {"base", "leaves", "reviewed_heads", "extra_paths"}
        if unknown:
            raise ValueError("unknown plan keys: %s" % sorted(unknown))
        heads, extras = plan.get("reviewed_heads", []), plan.get("extra_paths", [])
        if not isinstance(heads, list) or not isinstance(extras, list):
            raise ValueError("reviewed_heads and extra_paths must be lists")
    except (OSError, ValueError) as exc:
        print("bridge_leaf_reuse: %s" % exc, file=sys.stderr)
        return 2
    report = assess_repo(args.repo, plan.get("base"), plan.get("leaves") or {}, heads, extras)
    print(json.dumps(report, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
