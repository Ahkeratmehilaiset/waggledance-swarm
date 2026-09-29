#!/usr/bin/env python3
"""Classify an exact git diff for the reviewed Bridge-only CI boundary.

Read-only. This program never invokes pytest or a shell, and does not change
the workflow. Callers must inspect ``scope``; exit zero alone is not approval.
Test-only shared-provider changes use the reviewed transitive consumer set.
The caller must load this script and its sibling map from a trusted base;
this script verifies their bytes against that base but cannot secure a caller
that executes PR-controlled Python before making the routing decision.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Callable, Sequence

if __package__:
    from tools import select_affected_tests as selector
else:
    import select_affected_tests as selector

BRIDGE_EXPLICIT_TESTS = selector.BRIDGE_EXPLICIT_TESTS
bridge_test_closure = selector.bridge_test_closure

SCHEMA = "wd.bridge-ci-scope.v1"
_COMMIT_RE = re.compile(r"[0-9a-fA-F]{40}\Z")
_POLICY_FILES = frozenset({
    "tools/classify_bridge_ci_scope.py",
    "tools/select_affected_tests.py",
    "docs/BRIDGE_TEST_BOUNDARY.md",
    "tests/tools/test_classify_bridge_ci_scope.py",
    "tests/tools/test_select_affected_tests.py",
    "tools/run_release_ci_status_evidence.py",
    "tests/tools/test_release_ci_status_evidence.py",
})


def _result(scope: str, base: str, head: str, changed: list[str],
            tests: list[str], reason: str) -> dict:
    return {"schema": SCHEMA, "scope": scope, "base": base, "head": head,
            "changed_files": changed, "tests": tests, "reason": reason}


def _valid_commit(value: str) -> bool:
    return isinstance(value, str) and bool(_COMMIT_RE.fullmatch(value)) and int(value, 16) != 0


def _safe_relative(path: str) -> bool:
    if not path or "\x00" in path or "\\" in path:
        return False
    parsed = PurePosixPath(path)
    return not parsed.is_absolute() and all(part not in (".", "..") for part in parsed.parts)


def _checked_file(root: Path, relative: str, *, utf8: bool) -> str | None:
    """Return an error for missing, escaping, or unreadable checkout files."""
    if not _safe_relative(relative):
        return f"unsafe path: {relative!r}"
    candidate = root / relative
    try:
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(root) or not resolved.is_file():
            return f"missing or outside checkout: {relative!r}"
        if utf8:
            resolved.read_text(encoding="utf-8")
        else:
            resolved.read_bytes()
    except (OSError, UnicodeError):
        return f"missing or unreadable file: {relative!r}"
    return None


def classify_scope(base: str, head: str, repo_root: str | Path = ".",
                   *, run_git: Callable = subprocess.run) -> dict:
    """Return Bridge only when the exact committed diff is completely mapped."""
    changed: list[str] = []
    full = lambda reason: _result("full", base, head, changed, [], reason)
    if not _valid_commit(base) or not _valid_commit(head) or base.lower() == head.lower():
        return full("base/head must be distinct nonzero exact 40-hex commits")
    try:
        root = Path(repo_root).resolve(strict=True)
        if not root.is_dir():
            return full("checkout root is not a directory")
        if Path(__file__).resolve().parent != Path(selector.__file__).resolve().parent:
            return full("classifier and selector were loaded from different directories")
        for commit in (base, head):
            probe = run_git(["git", "cat-file", "-t", commit], cwd=root,
                            capture_output=True, check=False)
            if probe.returncode != 0 or probe.stdout != b"commit\n":
                return full(f"git commit unavailable or not a commit: {commit}")
        common = run_git(["git", "merge-base", base, head], cwd=root,
                         capture_output=True, check=False)
        if common.returncode != 0 or common.stdout.strip().decode("ascii", "strict").lower() != base.lower():
            return full("base is not the exact merge-base of head")
        for loaded, relative in ((Path(__file__), "tools/classify_bridge_ci_scope.py"),
                                 (Path(selector.__file__), "tools/select_affected_tests.py")):
            original = run_git(["git", "show", f"{base}:{relative}"], cwd=root,
                               capture_output=True, check=False)
            if original.returncode != 0 or not isinstance(original.stdout, bytes):
                return full(f"trusted base policy missing: {relative}")
            if loaded.read_bytes() != original.stdout:
                return full(f"loaded tool differs from trusted base: {relative}")
        checkout = run_git(["git", "rev-parse", "HEAD"], cwd=root,
                           capture_output=True, check=False)
        if checkout.returncode != 0 or checkout.stdout.strip().decode("ascii", "strict").lower() != head.lower():
            return full("checked-out HEAD differs from requested head")
        status = run_git(["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
                         cwd=root, capture_output=True, check=False)
        if status.returncode != 0 or not isinstance(status.stdout, bytes):
            return full("git status failed or returned malformed output")
        if status.stdout:
            return full("dirty checkout (tracked, staged, or untracked paths)")
        diff = run_git(["git", "diff", "--name-only", "-z", "--no-renames", base, head],
                       cwd=root, capture_output=True, check=False)
        if diff.returncode != 0:
            return full(f"git diff failed with exit {diff.returncode}")
        raw = diff.stdout
        if not isinstance(raw, bytes) or (raw and not raw.endswith(b"\x00")):
            return full("malformed NUL-delimited git diff output")
        changed = [part.decode("utf-8", "strict") for part in raw.split(b"\x00")[:-1]] if raw else []
    except (FileNotFoundError, OSError, UnicodeError, ValueError, AttributeError) as exc:
        return full(f"git or strict decode unavailable: {type(exc).__name__}")
    if not changed or len(changed) != len(set(changed)):
        return full("empty or duplicate changed path set")
    if any(path in _POLICY_FILES or path.startswith(".github/workflows/") for path in changed):
        return full("CI routing policy or contract changed; bootstrap requires full scope")
    bridge_tests = set().union(*BRIDGE_EXPLICIT_TESTS.values())
    selected: set[str] = set()
    for path in changed:
        error = _checked_file(root, path, utf8=path.startswith("tests/"))
        if error:
            return full(error)
        if path in BRIDGE_EXPLICIT_TESTS:
            selected.update(BRIDGE_EXPLICIT_TESTS[path])
        elif path in bridge_tests:
            selected.add(path)
        else:
            return full(f"unmapped or mixed product/config path: {path!r}")
    if not selected:
        return full("no Bridge tests selected")
    selected = bridge_test_closure(selected)
    for test in sorted(selected):
        error = _checked_file(root, test, utf8=True)
        if error:
            return full(f"mapped test {error}")
    return _result("bridge", base, head, changed, sorted(selected),
                   "all changed paths belong to the reviewed Bridge map")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args(argv)
    print(json.dumps(classify_scope(args.base, args.head, args.repo_root),
                     ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
