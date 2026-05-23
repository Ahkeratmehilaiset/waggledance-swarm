#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fail-closed pre-merge resolver gate for ``requirements.lock.txt``.

The static lock-floor regression tests
(``tests/tools/test_requirements_lock_release_floors.py``) catch
*per-package* drift but cannot catch *cross-package* conflicts like
``moviepy<12.0`` vs ``pillow==12.2.0`` -- those only surface during a
full pip resolve. PR #584 had to retroactively remove ``moviepy`` after
exactly that conflict landed on main. This gate runs the equivalent of
``pip install --dry-run -r requirements.lock.txt --extra-index-url
https://download.pytorch.org/whl/cu126`` (the cu126 index is needed for
the safetensors/torch family per the documented lock exception in
``docs/release/RELEASE_READINESS.md``), captures pip's exit code and
stderr, and exits non-zero on a real resolver conflict.

Usage:
    python tools/check_lock_resolves.py
    python tools/check_lock_resolves.py --output report.json
    python tools/check_lock_resolves.py --lock-file path/to/lock.txt
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_LOCK = Path("requirements.lock.txt")
DEFAULT_EXTRA_INDEX = "https://download.pytorch.org/whl/cu126"
DEFAULT_TIMEOUT_SECONDS = 480  # 8 minutes; pip resolve can be slow.

CONFLICT_MARKER_PATTERNS = (
    re.compile(r"Cannot install ", re.IGNORECASE),
    re.compile(r"The conflict is caused by:", re.IGNORECASE),
    re.compile(r"ResolutionImpossible", re.IGNORECASE),
    re.compile(r"ERROR: ResolutionImpossible", re.IGNORECASE),
)


def _run_pip_dry_install(
    lock_file: Path,
    *,
    extra_index_url: str,
    cache_dir: Path,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--dry-run",
        "--ignore-installed",
        "--no-input",
        "--disable-pip-version-check",
        "--no-color",
        "--cache-dir",
        str(cache_dir),
        "-r",
        str(lock_file),
    ]
    if extra_index_url:
        cmd.extend(["--extra-index-url", extra_index_url])
    return subprocess.run(  # noqa: S603
        cmd,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _extract_conflicts(stderr: str) -> list[str]:
    """Return distinct lines that look like resolver-conflict markers."""

    seen: list[str] = []
    for line in stderr.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(pat.search(stripped) for pat in CONFLICT_MARKER_PATTERNS):
            if stripped not in seen:
                seen.append(stripped)
    return seen


def check_lock_resolves(
    lock_file: Path = DEFAULT_LOCK,
    *,
    extra_index_url: str = DEFAULT_EXTRA_INDEX,
    cache_dir: Path | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    runner=None,
) -> dict[str, object]:
    """Run a dry-run pip resolve and return a structured result.

    ``runner`` is injectable so unit tests can mock the pip call without
    spawning a real network process. When ``None`` (the default), the
    module-level ``_run_pip_dry_install`` is resolved at call time so
    ``monkeypatch.setattr`` on that attribute works as expected.
    """

    if runner is None:
        runner = _run_pip_dry_install

    if not lock_file.exists():
        return {
            "ok": False,
            "lock_file": str(lock_file),
            "returncode": None,
            "conflicts": [],
            "error": f"lock file not found: {lock_file}",
        }

    if cache_dir is None:
        cache_dir = Path(tempfile.gettempdir()) / "wd-pip-resolve-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    try:
        completed = runner(
            lock_file,
            extra_index_url=extra_index_url,
            cache_dir=cache_dir,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "lock_file": str(lock_file),
            "returncode": None,
            "conflicts": [],
            "error": f"pip dry-run timed out after {exc.timeout}s",
        }

    stderr = completed.stderr or ""
    conflicts = _extract_conflicts(stderr)
    ok = completed.returncode == 0 and not conflicts
    return {
        "ok": ok,
        "lock_file": str(lock_file),
        "returncode": completed.returncode,
        "conflicts": conflicts,
        "stderr_tail": "\n".join(stderr.splitlines()[-40:]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock-file", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--extra-index-url", default=DEFAULT_EXTRA_INDEX)
    parser.add_argument("--cache-dir", type=Path, default=None)
    parser.add_argument(
        "--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write JSON summary to this path (in addition to stdout).",
    )
    args = parser.parse_args(argv)

    result = check_lock_resolves(
        args.lock_file,
        extra_index_url=args.extra_index_url,
        cache_dir=args.cache_dir,
        timeout=args.timeout_seconds,
    )

    payload = json.dumps(result, indent=2, sort_keys=True)
    print(payload)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")

    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
