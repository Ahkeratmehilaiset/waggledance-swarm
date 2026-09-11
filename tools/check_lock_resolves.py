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
stderr, and exits non-zero on a resolver conflict or an incomplete lock.
The native pip installation report must match every active exact pin, with
no additional unpinned distributions. Target-wheel selection on a different
host is not evidence of native environment-marker evaluation.

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

# pip is already required to run this tool; its vendored parser keeps the CI
# resolver job dependency-free before application packages are installed.
from pip._vendor.packaging.markers import default_environment
from pip._vendor.packaging.requirements import Requirement
from pip._vendor.packaging.utils import canonicalize_name
from pip._vendor.packaging.version import Version

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
    report_path: Path,
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
        "--report",
        str(report_path),
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


def _check_report_completeness(lock_text: str, report: object) -> dict[str, object]:
    """Compare a native, ignore-installed resolution with active exact pins."""
    if not isinstance(report, dict) or report.get("version") != "1":
        raise ValueError("missing or unsupported pip installation report")
    environment = default_environment()
    reported_environment = report.get("environment")
    if not isinstance(reported_environment, dict) or any(
        reported_environment.get(key) != value for key, value in environment.items()
    ):
        raise ValueError("pip report environment does not match the native host")
    installs = report.get("install")
    if not isinstance(installs, list):
        raise ValueError("pip report install must be an array")

    requirements: dict[str, list[Requirement]] = {}
    pins: dict[str, Version] = {}
    for raw in lock_text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("--extra-index-url ") and line.split(maxsplit=1)[1].strip():
            continue
        requirement = Requirement(line)
        if requirement.url is not None or requirement.extras:
            raise ValueError("lock entries must be version pins, not URLs or extras")
        if requirement.marker is not None and not requirement.marker.evaluate(environment):
            continue
        name = canonicalize_name(requirement.name)
        requirements.setdefault(name, []).append(requirement)
        for specifier in requirement.specifier:
            if specifier.operator == "==" and "*" not in specifier.version:
                version = Version(specifier.version)
                if name in pins and pins[name] != version:
                    raise ValueError(f"conflicting active exact pins: {name}")
                pins[name] = version
    if not pins:
        raise ValueError("lock has no active exact pins on this host")
    missing_pins = sorted(requirements.keys() - pins.keys())
    if missing_pins:
        raise ValueError(f"active requirements without exact pins: {missing_pins}")

    resolved: dict[str, Version] = {}
    for item in installs:
        metadata = item.get("metadata") if isinstance(item, dict) else None
        if not isinstance(metadata, dict):
            raise ValueError("pip report item has no package metadata")
        name, version = metadata.get("name"), metadata.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            raise ValueError("pip report package name/version must be strings")
        name = canonicalize_name(name, validate=True)
        if name in resolved:
            raise ValueError(f"duplicate resolved package: {name}")
        resolved[name] = Version(version)

    unpinned = sorted(resolved.keys() - pins.keys())
    missing = sorted(pins.keys() - resolved.keys())
    if unpinned or missing:
        raise ValueError(f"incomplete lock: unpinned={unpinned}, absent_from_report={missing}")
    for name, version in resolved.items():
        # Version equality retains local build labels, unlike == specifier
        # matching which also permits e.g. 2.13.0+cu126 against ==2.13.0.
        if version != pins[name]:
            raise ValueError(f"resolved version differs from exact pin: {name}=={version}")
        if any(not req.specifier.contains(version, prereleases=True) for req in requirements[name]):
            raise ValueError(f"resolved version violates retained floor: {name}=={version}")
    return {
        "ok": True,
        "native_environment": environment,
        "active_exact_pin_count": len(pins),
        "resolved_count": len(resolved),
        "resolved_packages": [
            {"name": name, "version": str(version)} for name, version in sorted(resolved.items())
        ],
    }


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

    completeness = None
    report_error = None
    try:
        lock_text = lock_file.read_text(encoding="utf-8-sig")
        # A fresh private report path prevents a previous successful run from
        # standing in for a missing report on this invocation.
        with tempfile.TemporaryDirectory(prefix="wd-lock-report-", dir=cache_dir) as directory:
            report_path = Path(directory) / "install.json"
            completed = runner(
                lock_file,
                extra_index_url=extra_index_url,
                cache_dir=cache_dir,
                timeout=timeout,
                report_path=report_path,
            )
            if completed.returncode == 0 and not _extract_conflicts(completed.stderr or ""):
                try:
                    if lock_file.read_text(encoding="utf-8-sig") != lock_text:
                        raise ValueError("lock changed during dependency resolution")
                    report = json.loads(report_path.read_text(encoding="utf-8"))
                    completeness = _check_report_completeness(lock_text, report)
                except (OSError, ValueError) as exc:
                    report_error = str(exc)
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "lock_file": str(lock_file),
            "returncode": None,
            "conflicts": [],
            "error": f"pip dry-run timed out after {exc.timeout}s",
        }
    except OSError as exc:
        return {
            "ok": False, "lock_file": str(lock_file), "returncode": None,
            "conflicts": [], "error": str(exc),
        }

    stderr = completed.stderr or ""
    conflicts = _extract_conflicts(stderr)
    ok = completed.returncode == 0 and not conflicts and completeness is not None
    return {
        "ok": ok,
        "lock_file": str(lock_file),
        "returncode": completed.returncode,
        "conflicts": conflicts,
        "completeness": completeness,
        "error": report_error,
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
