#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Select the AFFECTED test files for a set of changed files (fast local iteration).

RFC: WD bridge throughput & resilience, item P2/D6 (stop re-running the full
~795-test suite locally on every head). This maps changed source/test files to
the test files that exercise them, so an agent can run ONLY the affected tests
locally during iteration instead of the whole suite.

CRITICAL CONTRACT — CI remains the authoritative full-suite gate. This selector
is best-effort and **FAIL-SAFE**: whenever the affected set is uncertain (a
broad-impact file changed, a changed source file maps to no test, an unknown
file type, or empty input) it returns ``full_suite=True`` so nothing is ever
silently under-run. It only ever NARROWS when the mapping is unambiguous.

LIMITATION — import-grep detects only DIRECT imports (and the ``test_<stem>`` name
convention); it does NOT detect transitive/indirect imports (``importlib`` /
``__import__`` / re-exports / conftest-fixture-mediated coverage), so a
transitively-affected test may not be selected for a LOCAL run. This is safe ONLY
because CI stays the authoritative full suite.

GUARDRAIL — this selector must NEVER replace the full suite in CI. If it is ever
wired into CI it must be ADDITIVE (the full suite still runs); it may gate only
LOCAL iteration.

Read-only: it inspects the repo tree and runs no tests and mutates nothing.

Usage:
    python tools/select_affected_tests.py --files waggledance/x/y.py tests/test_z.py --json
    python tools/select_affected_tests.py --changed-from-git origin/main --json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Sequence


# Changing any of these can affect arbitrary tests → fail-safe to the full suite.
BROAD_IMPACT_BASENAMES = frozenset(
    {
        "conftest.py",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "tox.ini",
        "pytest.ini",
        "__init__.py",
    }
)
BROAD_IMPACT_SUBSTRINGS = ("requirements",)
# Gate-critical / charter source: a change here can alter merge behavior broadly.
BROAD_IMPACT_PATHS = frozenset(
    {"waggledance/core/idle_consensus_charter.py"}
)
SOURCE_PREFIXES = ("waggledance/", "tools/")
TEST_PREFIXES = ("tests/",)
_IMPORT_RE_CACHE: dict[str, re.Pattern[str]] = {}


def _norm(path: str) -> str:
    return path.replace("\\", "/").strip().lstrip("./")


def _is_test_file(path: str) -> bool:
    p = _norm(path)
    return (
        p.startswith(TEST_PREFIXES)
        and p.endswith(".py")
        and Path(p).name.startswith("test_")
    )


def _is_source_file(path: str) -> bool:
    p = _norm(path)
    return p.startswith(SOURCE_PREFIXES) and p.endswith(".py") and not _is_test_file(p)


def _is_broad_impact(path: str) -> bool:
    p = _norm(path)
    if p in BROAD_IMPACT_PATHS:
        return True
    name = Path(p).name
    if name in BROAD_IMPACT_BASENAMES:
        return True
    return any(sub in name for sub in BROAD_IMPACT_SUBSTRINGS)


def _module_dotted(path: str) -> str:
    p = _norm(path)
    return p[:-3].replace("/", ".") if p.endswith(".py") else p.replace("/", ".")


def _tests_importing(
    source_path: str, repo_root: Path
) -> tuple[set[str], bool]:
    """Return (affected tests, read_error).

    ``read_error`` is True if any candidate test file could not be read — the
    caller must then fail-safe to the full suite, since it cannot rule out that
    the unreadable candidate imports the changed module.
    """
    dotted = _module_dotted(source_path)
    stem = Path(_norm(source_path)).stem
    tests_dir = repo_root / "tests"
    if not tests_dir.is_dir():
        return set(), False
    import_pat = _IMPORT_RE_CACHE.get(dotted)
    if import_pat is None:
        # Match `import a.b.c` / `from a.b.c import ...` / `from a.b import c`.
        esc = re.escape(dotted)
        parent = re.escape(dotted.rsplit(".", 1)[0]) if "." in dotted else esc
        import_pat = re.compile(
            rf"(?:^|\b)(?:import\s+{esc}\b"
            rf"|from\s+{esc}\s+import\b"
            rf"|from\s+{parent}\s+import\s+[^\n]*\b{re.escape(stem)}\b)"
        )
        _IMPORT_RE_CACHE[dotted] = import_pat
    affected: set[str] = set()
    read_error = False
    for test_file in tests_dir.rglob("test_*.py"):
        rel = test_file.relative_to(repo_root).as_posix()
        # Name convention: tests/.../test_<stem>.py is affected by <stem>.py.
        if test_file.stem == f"test_{stem}":
            affected.add(rel)
            continue
        try:
            text = test_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            # An unreadable candidate could import the changed module → signal
            # uncertainty so the caller fail-safes to the full suite (contract).
            read_error = True
            continue
        if import_pat.search(text):
            affected.add(rel)
    return affected, read_error


def _full(reason: str) -> dict:
    return {"full_suite": True, "tests": [], "reason": reason}


def select_affected_tests(
    changed_files: Iterable[str], repo_root: str | Path = "."
) -> dict:
    """Return {full_suite, tests, reason}. Fail-safe to full_suite when uncertain."""
    root = Path(repo_root)
    files = [_norm(f) for f in changed_files if _norm(f)]
    if not files:
        return _full("no changed files provided")
    tests: set[str] = set()
    for f in files:
        if _is_broad_impact(f):
            return _full(f"broad-impact file changed: {f}")
        if _is_test_file(f):
            tests.add(f)
            continue
        if _is_source_file(f):
            mapped, read_error = _tests_importing(f, root)
            if read_error:
                return _full(f"unreadable test candidate while mapping: {f}")
            if not mapped:
                return _full(f"no affected test found for source: {f}")
            tests |= mapped
            continue
        # Unknown file type (docs, config, data, etc.) → cannot reason → fail-safe.
        return _full(f"unmapped changed file: {f}")
    if not tests:
        return _full("no tests selected")
    return {"full_suite": False, "tests": sorted(tests), "reason": "affected-only"}


def _git_changed(base: str, repo_root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...HEAD"],
        cwd=str(repo_root),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    ).stdout
    return [line for line in (out or "").splitlines() if line.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--files", nargs="+", help="changed file paths (repo-relative)")
    src.add_argument(
        "--changed-from-git",
        metavar="BASE",
        help="compute changed files from `git diff --name-only BASE...HEAD`",
    )
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.repo_root)
    if args.changed_from_git is not None:
        changed = _git_changed(args.changed_from_git, root)
    else:
        changed = list(args.files)
    result = select_affected_tests(changed, root)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    elif result["full_suite"]:
        print(f"FULL SUITE ({result['reason']})")
    else:
        print("\n".join(result["tests"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
