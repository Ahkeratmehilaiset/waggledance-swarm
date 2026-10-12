#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""LLM-free free-space guard for new test runs and new worktrees.

On 2026-10-11 C: ran out of space twice (0 MB at about 15:15Z, 1.62 GB at 16:00Z) while
lanes kept starting full test suites and review worktrees. This guard is the mechanical
answer: no model in the path, standard library only, fail closed.

* below 10 GiB free: WARN (printed, the action proceeds);
* below 3 GiB free: BLOCK -- a new pytest session (``tests/conftest.py`` calls
  :func:`pytest_session_guard`) and a new worktree (``worktree-add``) are refused with exit
  code 28 (ENOSPC);
* GiB (2**30 bytes) is the stricter reading of "GB": 10 GiB = 10.74 GB, 3 GiB = 3.22 GB.

Measured volumes: on Windows ``C:\\`` always (the drive the lanes, bundles and the bridge
live on) and the system drive when it differs, plus every target path; elsewhere the target
paths (default: the current directory). A path that does not exist yet is measured at its
nearest existing ancestor. A measurement that fails counts as BLOCK. The worst volume wins.

    python tools/wd_disk_guard.py check --purpose status
    python tools/wd_disk_guard.py check --purpose worktree --path C:/path/new-wt --json
    python tools/wd_disk_guard.py worktree-add --repo . -- --detach C:/path/new-wt origin/main

``worktree-add`` checks the target, the repository and its common git dir (where git writes
the new worktree's index and metadata; when git cannot name it, ``git worktree add`` fails on
its own) and then runs ``git worktree add`` with the arguments unchanged; arguments it cannot
parse are refused (exit 2) rather than guessed. Freeing
space: ``tools/wd_prune_own_worktrees.py`` (owner-scoped, dry run by default).

Not covered here: the bridge worktree scripts under ``.agent-bridge/bin`` do not call this
guard yet (that is a separate, signed change), and nothing polls it periodically.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Callable, Iterable, Sequence

SCHEMA = "wd.disk-guard.v1"
GIB = 1024 ** 3
WARN_BELOW_BYTES = 10 * GIB
BLOCK_BELOW_BYTES = 3 * GIB
BLOCK_EXIT_CODE = 28  # errno ENOSPC
PURPOSES = ("test", "worktree", "status")
_RANK = {"ok": 0, "warn": 1, "block": 2}
_DRIVE = re.compile(r"[A-Za-z]:\Z")

# git worktree add options that take a value; every other accepted option is a plain flag.
_VALUE_OPTIONS = frozenset({"-b", "-B", "--reason"})
_FLAG_OPTIONS = frozenset({
    "-f", "--force", "--detach", "--checkout", "--no-checkout", "--lock", "--orphan",
    "--track", "--no-track", "--guess-remote", "--no-guess-remote", "-q", "--quiet",
    "--relative-paths", "--no-relative-paths",
})


def classify(free_bytes: int) -> str:
    """``block`` below 3 GiB, ``warn`` below 10 GiB, else ``ok``."""
    if free_bytes < BLOCK_BELOW_BYTES:
        return "block"
    if free_bytes < WARN_BELOW_BYTES:
        return "warn"
    return "ok"


def guarded_paths(paths: Iterable, *, platform: str | None = None, environ=None) -> list[str]:
    """The paths to measure, in order, without duplicates."""
    platform = sys.platform if platform is None else platform
    environ = os.environ if environ is None else environ
    windows = platform == "win32"
    ordered: list[str] = []
    if windows:
        ordered.append("C:\\")
        system_drive = str(environ.get("SystemDrive") or "")
        if _DRIVE.match(system_drive):
            ordered.append(system_drive.upper() + "\\")
    ordered.extend(os.fspath(path) for path in paths)
    if not ordered:
        ordered.append(os.getcwd())
    seen: set[str] = set()
    unique: list[str] = []
    for path in ordered:
        key = path.casefold() if windows else path
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _nearest_existing(path: str) -> str | None:
    candidate = os.path.abspath(path)
    while not os.path.exists(candidate):
        parent = os.path.dirname(candidate)
        if parent == candidate:
            return None
        candidate = parent
    return candidate


def _measure(path: str, disk_usage: Callable) -> dict:
    volume = {"path": path, "measured_path": None, "free_bytes": None, "verdict": "block", "error": None}
    measured = _nearest_existing(path)
    if measured is None:
        volume["error"] = "no existing ancestor"
        return volume
    volume["measured_path"] = measured
    try:
        free = int(disk_usage(measured).free)
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        volume["error"] = f"{type(exc).__name__}: {exc}"
        return volume
    volume["free_bytes"] = free
    volume["verdict"] = classify(free)
    return volume


def check(purpose: str, paths: Iterable = (), *, platform: str | None = None, environ=None,
          disk_usage: Callable | None = None) -> dict:
    """Measure every guarded path; the worst volume verdict is the result."""
    if purpose not in PURPOSES:
        raise ValueError(f"unknown purpose {purpose!r}; expected one of {', '.join(PURPOSES)}")
    usage = shutil.disk_usage if disk_usage is None else disk_usage
    volumes = [_measure(path, usage) for path in guarded_paths(paths, platform=platform, environ=environ)]
    verdict = max((volume["verdict"] for volume in volumes), key=_RANK.__getitem__)
    return {
        "schema": SCHEMA,
        "purpose": purpose,
        "verdict": verdict,
        "warn_below_bytes": WARN_BELOW_BYTES,
        "block_below_bytes": BLOCK_BELOW_BYTES,
        "volumes": volumes,
    }


def format_line(result: dict) -> str:
    parts = []
    for volume in result["volumes"]:
        if volume["free_bytes"] is None:
            parts.append(f"{volume['path']} unmeasurable ({volume['error']})")
        else:
            parts.append(f"{volume['path']} {volume['free_bytes'] / GIB:.2f} GiB free")
    line = (f"WD-DISK-GUARD {result['verdict'].upper()} purpose={result['purpose']}: " + "; ".join(parts)
            + f" (warn below {WARN_BELOW_BYTES // GIB} GiB, block below {BLOCK_BELOW_BYTES // GIB} GiB)")
    if result["verdict"] == "block":
        line += (". New test runs and new worktrees are refused; free space first"
                 " (python tools/wd_prune_own_worktrees.py lists your own removable worktrees).")
    return line


def worktree_add_target(git_args: Sequence[str]) -> str:
    """The <path> operand of ``git worktree add`` arguments; ValueError when unclear."""
    args = list(git_args)
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            if index + 1 < len(args):
                return args[index + 1]
            raise ValueError("no worktree path after --")
        if arg in _VALUE_OPTIONS:
            if index + 1 >= len(args):
                raise ValueError(f"{arg} needs a value")
            index += 2
            continue
        if arg.startswith("--reason=") or (arg[:2] in ("-b", "-B") and len(arg) > 2):
            index += 1
            continue
        if arg in _FLAG_OPTIONS:
            index += 1
            continue
        if arg.startswith("-"):
            raise ValueError(f"unsupported git worktree add option {arg!r}")
        return arg
    raise ValueError("no worktree path in the git worktree add arguments")


def pytest_session_guard(session) -> None:
    """Called from ``tests/conftest.py``: refuse a new session below 3 GiB, warn below 10 GiB."""
    config = session.config
    if hasattr(config, "workerinput"):  # a pytest-xdist worker; the controller already checked
        return
    basetemp = getattr(config.option, "basetemp", None) or tempfile.gettempdir()
    result = check("test", [str(config.rootpath), str(basetemp)])
    if result["verdict"] == "ok":
        return
    line = format_line(result)
    if result["verdict"] == "block":
        import pytest

        pytest.exit(line, returncode=BLOCK_EXIT_CODE)
    sys.stderr.write(line + "\n")
    sys.stderr.flush()


def _worktree_add(options: list[str]) -> int:
    """``worktree-add [--repo R] -- <git worktree add arguments>``, split on the first ``--`` here,
    not by argparse (whose REMAINDER handling of ``--`` differs between Python versions)."""
    if "--" not in options:
        print("WD-DISK-GUARD refused: usage: worktree-add [--repo R] -- <git worktree add arguments>", file=sys.stderr)
        return 2
    split = options.index("--")
    git_args = options[split + 1:]
    parser = argparse.ArgumentParser(prog="wd_disk_guard.py worktree-add")
    parser.add_argument("--repo", default=".")
    try:
        repo = parser.parse_args(options[:split]).repo
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    try:
        target = worktree_add_target(git_args)
    except ValueError as exc:
        print(f"WD-DISK-GUARD refused: {exc}", file=sys.stderr)
        return 2
    # git writes the new worktree's index and metadata into the common git dir, so its volume counts too
    paths = [os.path.join(repo, target), repo]
    common = subprocess.run(["git", "-C", repo, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            capture_output=True, text=True)
    if common.returncode == 0 and common.stdout.strip():
        paths.append(common.stdout.strip())
    result = check("worktree", paths)
    if result["verdict"] != "ok":
        print(format_line(result), file=sys.stderr)
    if result["verdict"] == "block":
        return BLOCK_EXIT_CODE
    return subprocess.run(["git", "-C", repo, "worktree", "add", *git_args]).returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    check_parser = sub.add_parser("check", help="measure free space and print the verdict")
    check_parser.add_argument("--purpose", choices=PURPOSES, required=True)
    check_parser.add_argument("--path", action="append", default=[], help="a target path (repeatable)")
    check_parser.add_argument("--json", action="store_true")
    sub.add_parser("worktree-add", help="[--repo R] -- <git worktree add arguments>: check the target, then run "
                                        "git worktree add with those arguments unchanged")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["worktree-add"]:
        return _worktree_add(argv[1:])
    args = build_parser().parse_args(argv)
    result = check(args.purpose, args.path)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(format_line(result))
    return BLOCK_EXIT_CODE if result["verdict"] == "block" else 0


if __name__ == "__main__":
    raise SystemExit(main())
