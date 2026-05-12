# SPDX-License-Identifier: BUSL-1.1
"""Regex pattern cache regression contract (L35).

H22 + H58 audit established that ``re.compile()`` inside a function body
rebuilds the compiled pattern on every call. The fix was to either:

1. Move ``re.compile`` to module scope (cheapest), or
2. Use a module-level cache (``_SIGNAL_PATTERN_CACHE`` in
   ``solver_router._has_signal`` is the canonical example): the
   ``re.compile`` is wrapped behind a cache-miss check so the per-call
   cost is amortized.

This contract test fails if ANY new function body contains a plain
``re.compile(...)`` call outside the explicit allowlist of cache-pattern
sites. The allowlist is small (one site today) and is intentionally
strict -- adding to it requires a code review acknowledgement that the
new site is a real cache-miss pattern, not an accidental hot-path
regression.

Why this matters: ``solver_router.classify_intent`` shows up in every
chat turn. A regression that moves ``re.compile`` back into the per-call
path is invisible in unit tests (functionally correct) but doubles
hot-path latency. The L34 perf-budget test catches it AFTER it lands;
this L35 test catches it AT THE PR.
"""
from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WAGGLEDANCE_ROOT = PROJECT_ROOT / "waggledance"


# Allowlist: (file_path_relative, function_name) pairs where a
# ``re.compile(...)`` inside the function body is the canonical
# module-level cache-miss pattern. Each entry must store the result
# into a module-level dict / cache structure.
#
# Adding to this set requires a code review confirming the new site
# is a real cache-miss path (compile-once, reuse-forever) and NOT a
# per-call rebuild.
KNOWN_CACHE_SITES: set[tuple[str, str]] = {
    ("waggledance/core/reasoning/solver_router.py", "_has_signal"),
}


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _is_re_compile_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute):
        return (
            func.attr == "compile"
            and isinstance(func.value, ast.Name)
            and func.value.id == "re"
        )
    return False


def _find_violations() -> list[tuple[str, int, str]]:
    """Return list of (rel_path, lineno, function_name) for un-allowlisted
    re.compile() calls inside function bodies."""
    violations: list[tuple[str, int, str]] = []
    for path in sorted(WAGGLEDANCE_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = _parse(path)
        except SyntaxError:
            continue
        rel_path = path.relative_to(PROJECT_ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            fn_name = node.name
            for sub in ast.walk(node):
                if _is_re_compile_call(sub):
                    if (rel_path, fn_name) in KNOWN_CACHE_SITES:
                        continue
                    violations.append((rel_path, sub.lineno, fn_name))
    return violations


def _find_known_cache_sites_still_active() -> set[tuple[str, str]]:
    """Return the set of allowlist entries that are STILL present in the
    code. Used to detect when an entry should be pruned because the
    cached re.compile has moved elsewhere."""
    active: set[tuple[str, str]] = set()
    for path in sorted(WAGGLEDANCE_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = _parse(path)
        except SyntaxError:
            continue
        rel_path = path.relative_to(PROJECT_ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            key = (rel_path, node.name)
            if key in KNOWN_CACHE_SITES:
                if any(_is_re_compile_call(sub) for sub in ast.walk(node)):
                    active.add(key)
    return active


def test_no_unallowlisted_re_compile_in_function_bodies() -> None:
    violations = _find_violations()
    assert violations == [], (
        "re.compile() inside function bodies rebuilds patterns per call. "
        "Move to module scope or use a module-level cache (see "
        "solver_router._has_signal for the canonical pattern). "
        f"Violations: {violations}"
    )


def test_known_cache_sites_are_still_in_code() -> None:
    """Stale-allowlist guard: if a known cache site no longer contains a
    re.compile (refactor moved it elsewhere), prune the allowlist entry
    so the test stays tight."""
    active = _find_known_cache_sites_still_active()
    stale = KNOWN_CACHE_SITES - active
    assert stale == set(), (
        "KNOWN_CACHE_SITES contains entries that are no longer present in "
        f"the code -- prune these from the allowlist: {stale}"
    )


def test_scanner_catches_synthetic_violation() -> None:
    """Negative test: prove the AST scanner actually catches a real
    re.compile-in-function pattern. Without this, a silent bug in the
    visitor that always returned empty would make both production tests
    pass while letting violations through."""
    synthetic_src = """
import re
def example():
    pat = re.compile(r"\\bfoo\\b")
    return pat.search("foo bar")
"""
    tree = ast.parse(synthetic_src)
    found = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(node):
                if _is_re_compile_call(sub):
                    found = True
                    break
    assert found, "scanner failed to flag synthetic re.compile() inside function body"
