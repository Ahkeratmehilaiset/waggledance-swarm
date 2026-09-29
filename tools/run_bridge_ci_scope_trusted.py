#!/usr/bin/env python3
"""Run the Bridge scope router from an isolated, protected-base policy copy.

Caller contract: the workflow/operator must load THIS file from a trusted
protected revision and launch it with ``python -I -B`` from a trusted cwd,
before executing any proposed-checkout Python, setup hook, or test. This file
cannot authenticate itself when loaded from an untrusted PR. Supply exact
base/head, the independently observed merge-base and protected-main tip,
an absolute trusted Git executable, and an existing ``.codex-audit`` parent
outside the proposed checkout. A stale/missing policy or any uncertainty
returns ``scope=full``. This helper never launches pytest or edits workflows.
Invalid CLI arguments exit nonzero without JSON. Consumers must treat any
nonzero exit, missing/invalid JSON or non-bridge scope as full, never approval.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Sequence

SCHEMA = "wd.bridge-ci-scope.v1"
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_PATH = re.compile(r"[A-Za-z0-9._/-]+\Z")
_POLICY = ("tools/classify_bridge_ci_scope.py", "tools/select_affected_tests.py")


def _full(base: str, head: str, reason: str) -> dict:
    return {"schema": SCHEMA, "scope": "full", "base": base, "head": head,
            "changed_files": [], "tests": [], "reason": reason}


def _sha(value: str) -> bool:
    return type(value) is str and bool(_SHA.fullmatch(value)) and int(value, 16) != 0


def _safe_path(value: str) -> bool:
    if type(value) is not str or not _PATH.fullmatch(value):
        return False
    parsed = PurePosixPath(value)
    return (bool(parsed.parts) and not parsed.is_absolute() and str(parsed) == value
            and all(part not in (".", "..") for part in parsed.parts))


def _clean_env(git_executable: Path, audit_parent: Path) -> dict[str, str]:
    """Avoid PR-supplied PYTHON/GIT/PATH/TMP configuration in child processes."""
    env = {"PATH": str(git_executable.parent), "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_NO_REPLACE_OBJECTS": "1",
           "GIT_OPTIONAL_LOCKS": "0", "GIT_CONFIG_COUNT": "2",
           "GIT_CONFIG_KEY_0": "core.fsmonitor", "GIT_CONFIG_VALUE_0": "false",
           "GIT_CONFIG_KEY_1": "core.hooksPath", "GIT_CONFIG_VALUE_1": str(audit_parent / "no-hooks"),
           "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
           "TMP": str(audit_parent), "TEMP": str(audit_parent), "TMPDIR": str(audit_parent)}
    if os.name == "nt":
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        env.update({"SystemRoot": system_root, "WINDIR": system_root,
                    "PATH": os.pathsep.join((str(git_executable.parent),
                                             str(Path(system_root) / "System32"), system_root)),
                    "PATHEXT": ".COM;.EXE;.BAT;.CMD"})
    else:
        env["PATH"] = os.pathsep.join((str(git_executable.parent), "/usr/bin", "/bin"))
    return env


def _run(argv: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True,
                          check=False, timeout=30)


def _validate_output(raw: bytes, base: str, head: str, checkout: Path) -> dict | None:
    try:
        value = json.loads(raw.decode("utf-8", "strict"))
    except (UnicodeError, ValueError, RecursionError):
        return None
    if type(value) is not dict or set(value) != {
        "schema", "scope", "base", "head", "changed_files", "tests", "reason"
    }:
        return None
    if value["schema"] != SCHEMA or value["base"] != base or value["head"] != head:
        return None
    if value["scope"] not in ("bridge", "full") or type(value["reason"]) is not str or not value["reason"]:
        return None
    changed, tests = value["changed_files"], value["tests"]
    if type(changed) is not list or type(tests) is not list:
        return None
    if any(not _safe_path(path) for path in changed + tests):
        return None
    if len(changed) != len(set(changed)) or len(tests) != len(set(tests)):
        return None
    if value["scope"] == "full":
        return value if not tests else None
    if not changed or not tests or tests != sorted(tests):
        return None
    for test in tests:
        if not test.startswith("tests/tools/") or not test.endswith(".py"):
            return None
        try:
            target = (checkout / test).resolve(strict=True)
            if not target.is_relative_to(checkout) or not target.is_file():
                return None
        except OSError:
            return None
    return value


def run_scope(*, checkout: str | Path, base: str, head: str,
              expected_merge_base: str, protected_base: str,
              audit_parent: str | Path, git_executable: str | Path) -> dict:
    """Return a validated result; expected validation and I/O failures become full."""
    full = lambda reason: _full(base, head, reason)
    if not all(_sha(value) for value in (base, head, expected_merge_base, protected_base)):
        return full("exact nonzero lowercase 40-hex commit inputs required")
    if base == head or base != expected_merge_base or base != protected_base:
        return full("base must equal independently supplied merge-base and current protected tip")
    try:
        root = Path(checkout).resolve(strict=True)
        audit = Path(audit_parent).resolve(strict=True)
        git = Path(git_executable)
        if not git.is_absolute():
            return full("trusted Git executable must be absolute")
        git = git.resolve(strict=True)
        if not root.is_dir() or not audit.is_dir() or audit.name != ".codex-audit":
            return full("checkout or explicit .codex-audit parent missing")
        if audit.is_relative_to(root) or Path(__file__).resolve().is_relative_to(root):
            return full("audit parent and trusted caller must be outside proposed checkout")
        if not git.is_file() or git.is_relative_to(root):
            return full("Git executable must be a trusted file outside proposed checkout")
        env = _clean_env(git, audit)

        def git_read(*args: str) -> bytes | None:
            process = _run([str(git), "-C", str(root), *args], cwd=audit, env=env)
            return process.stdout if process.returncode == 0 else None

        if git_read("rev-parse", "HEAD") != (head + "\n").encode():
            return full("checked-out HEAD differs from requested head")
        if git_read("merge-base", base, head) != (base + "\n").encode():
            return full("base is not exact merge-base of head")
        policy = {}
        for relative in _POLICY:
            entry = git_read("ls-tree", "-z", "--full-tree", base, "--", relative)
            if entry is None or not entry.endswith(b"\x00") or entry.count(b"\x00") != 1:
                return full(f"trusted base policy missing: {relative}")
            try:
                metadata, path = entry[:-1].split(b"\t", 1)
                mode, kind, oid = metadata.split(b" ")
                if mode not in (b"100644", b"100755") or kind != b"blob" or path != relative.encode():
                    return full(f"trusted base policy invalid: {relative}")
                blob_sha = oid.decode("ascii", "strict")
                if not _sha(blob_sha):
                    return full(f"trusted base policy invalid: {relative}")
            except (ValueError, UnicodeError):
                return full(f"trusted base policy invalid: {relative}")
            blob = git_read("cat-file", "blob", blob_sha)
            if blob is None or not blob:
                return full(f"trusted base policy missing: {relative}")
            policy[relative] = blob
        with tempfile.TemporaryDirectory(prefix="bridge-policy-", dir=audit) as directory:
            trusted = Path(directory)
            for relative, blob in policy.items():
                (trusted / Path(relative).name).write_bytes(blob)
            bootstrap = ("import os,runpy,sys; "
                         "script=sys.argv[1]; sys.path.insert(0,os.path.dirname(script)); "
                         "sys.argv=sys.argv[1:]; runpy.run_path(script,run_name='__main__')")
            process = _run([sys.executable, "-I", "-B", "-c", bootstrap,
                            str(trusted / "classify_bridge_ci_scope.py"),
                            "--base", base, "--head", head, "--repo-root", str(root)],
                           cwd=trusted, env=env)
            if process.returncode != 0:
                return full(f"trusted router exited nonzero: {process.returncode}")
            result = _validate_output(process.stdout, base, head, root)
            return result if result is not None else full("trusted router returned invalid output")
    except (OSError, ValueError, UnicodeError, subprocess.TimeoutExpired) as exc:
        return full(f"trusted caller unavailable: {type(exc).__name__}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("repo-root", "base", "head", "expected-merge-base",
                 "protected-base", "audit-parent", "git-executable"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args(argv)
    try:
        result = run_scope(checkout=args.repo_root, base=args.base, head=args.head,
                           expected_merge_base=args.expected_merge_base,
                           protected_base=args.protected_base,
                           audit_parent=args.audit_parent,
                           git_executable=args.git_executable)
    except Exception as exc:
        # Final CLI safety boundary: expose the failure class without arbitrary
        # exception text. A structured full result is not permission to narrow.
        result = _full(args.base, args.head,
                       f"trusted caller unavailable: {type(exc).__name__}")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
