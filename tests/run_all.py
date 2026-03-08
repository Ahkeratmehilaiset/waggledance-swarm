#!/usr/bin/env python3
"""
WaggleDance test runner — runs all test_*.py files as standalone subprocesses.

Most test files use sys.exit() and are NOT pytest-compatible, so this script
runs each one in a separate process and parses the output for PASS/FAIL/WARN.

Usage:
    python tests/run_all.py
    python tests/run_all.py --skip-ollama   # skip tests requiring Ollama

Exit code 0 if all suites pass, 1 if any fail.
"""

import os
import re
import subprocess
import sys
import time
from pathlib import Path

# Tests that require a running Ollama server
OLLAMA_TESTS = {
    "test_corrections.py",
    "test_routing_centroids.py",
    "test_phase4.py",
    "test_phase4ijk.py",
}

# Tests that require specific external services
SKIP_IN_CI = {
    "test_all.py",  # some async tests need pytest-asyncio + Ollama
}


def check_ollama() -> bool:
    """Check if Ollama is running and reachable."""
    try:
        import urllib.request
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
        return True
    except Exception:
        return False


def parse_results(output: str) -> tuple[int, int, int]:
    """Parse PASS/FAIL/WARN counts from test output."""
    pass_count = 0
    fail_count = 0
    warn_count = 0

    # Pattern 1: "PASS: N" / "Pass: N" / "FAIL: N" / "WARN: N"
    for m in re.finditer(r"(?:PASS|Pass|ok)[:\s]+(\d+)", output):
        pass_count = max(pass_count, int(m.group(1)))
    for m in re.finditer(r"(?:FAIL|Fail|fail)[:\s]+(\d+)", output):
        fail_count = max(fail_count, int(m.group(1)))
    for m in re.finditer(r"(?:WARN|Warn|warn)[:\s]+(\d+)", output):
        warn_count = max(warn_count, int(m.group(1)))

    # Pattern 2: "X/Y passed" (pytest style)
    m = re.search(r"(\d+) passed", output)
    if m and int(m.group(1)) > pass_count:
        pass_count = int(m.group(1))
    m = re.search(r"(\d+) failed", output)
    if m and int(m.group(1)) > fail_count:
        fail_count = int(m.group(1))

    # Pattern 3: "RESULTS: X/Y passed, Z failed"
    m = re.search(r"RESULTS:\s*(\d+)/\d+ passed,\s*(\d+) failed", output)
    if m:
        pass_count = max(pass_count, int(m.group(1)))
        fail_count = max(fail_count, int(m.group(2)))

    # Pattern 4: "Score: X/Y"
    m = re.search(r"Score:\s*(\d+)/(\d+)", output)
    if m and pass_count == 0:
        pass_count = int(m.group(1))
        total = int(m.group(2))
        if fail_count == 0:
            fail_count = total - pass_count

    # Pattern 5: "ALL TESTS PASSED"
    if re.search(r"ALL TESTS PASSED", output) and pass_count == 0:
        pass_count = 1  # at least indicate pass

    return pass_count, fail_count, warn_count


def main():
    skip_ollama = "--skip-ollama" in sys.argv or not check_ollama()
    ci_mode = os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"

    tests_dir = Path(__file__).parent
    test_files = sorted(tests_dir.glob("test_*.py"))

    if not test_files:
        print("No test files found!")
        sys.exit(1)

    results = []
    total_pass = 0
    total_fail = 0
    total_warn = 0
    total_skip = 0

    print(f"{'='*60}")
    print(f"  WAGGLEDANCE TEST RUNNER — {len(test_files)} suites")
    print(f"  Ollama: {'available' if not skip_ollama else 'not available (skipping dependent tests)'}")
    print(f"{'='*60}\n")

    for i, test_file in enumerate(test_files, 1):
        name = test_file.name

        # Skip checks
        if skip_ollama and name in OLLAMA_TESTS:
            print(f"[{i:2d}/{len(test_files)}] {name:45s} SKIP (requires Ollama)")
            results.append((name, "SKIP", 0, 0, 0))
            total_skip += 1
            continue
        if ci_mode and name in SKIP_IN_CI:
            print(f"[{i:2d}/{len(test_files)}] {name:45s} SKIP (CI incompatible)")
            results.append((name, "SKIP", 0, 0, 0))
            total_skip += 1
            continue

        t0 = time.time()
        try:
            proc = subprocess.run(
                [sys.executable, str(test_file)],
                capture_output=True, timeout=600,
                cwd=str(tests_dir.parent),
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
                     "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
            )
            # Decode with errors='replace' to handle Windows encoding issues
            proc_stdout = proc.stdout.decode("utf-8", errors="replace") if isinstance(proc.stdout, bytes) else proc.stdout
            proc_stderr = proc.stderr.decode("utf-8", errors="replace") if isinstance(proc.stderr, bytes) else proc.stderr
            output = proc_stdout + proc_stderr
            elapsed = time.time() - t0
            p, f, w = parse_results(output)

            if proc.returncode == 0 and f == 0:
                status = "PASS"
                total_pass += p
                total_warn += w
                print(f"[{i:2d}/{len(test_files)}] {name:45s} PASS ({p} pass, {w} warn) [{elapsed:.1f}s]")
            else:
                status = "FAIL"
                total_pass += p
                total_fail += f if f > 0 else 1
                total_warn += w
                print(f"[{i:2d}/{len(test_files)}] {name:45s} FAIL ({p} pass, {f} fail) [{elapsed:.1f}s]")

            results.append((name, status, p, f, w))

        except subprocess.TimeoutExpired:
            elapsed = time.time() - t0
            print(f"[{i:2d}/{len(test_files)}] {name:45s} TIMEOUT [{elapsed:.0f}s]")
            results.append((name, "TIMEOUT", 0, 1, 0))
            total_fail += 1
        except Exception as e:
            print(f"[{i:2d}/{len(test_files)}] {name:45s} ERROR: {e}")
            results.append((name, "ERROR", 0, 1, 0))
            total_fail += 1

    # Summary
    passed_suites = sum(1 for _, s, _, _, _ in results if s == "PASS")
    failed_suites = sum(1 for _, s, _, _, _ in results if s in ("FAIL", "TIMEOUT", "ERROR"))
    total_suites = len(test_files) - total_skip

    print(f"\n{'='*60}")
    print(f"  SUMMARY")
    print(f"{'='*60}")
    print(f"  Suites: {passed_suites}/{total_suites} GREEN" +
          (f" ({total_skip} skipped)" if total_skip else ""))
    print(f"  Tests:  {total_pass} passed, {total_fail} failed, {total_warn} warnings")

    if failed_suites > 0:
        print(f"\n  FAILED SUITES:")
        for name, status, p, f, w in results:
            if status in ("FAIL", "TIMEOUT", "ERROR"):
                print(f"    - {name} ({status})")

    print(f"{'='*60}")

    sys.exit(0 if failed_suites == 0 else 1)


if __name__ == "__main__":
    main()
