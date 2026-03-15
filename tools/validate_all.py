#!/usr/bin/env python3
"""One-command validation script for WaggleDance v1.18.0.

Runs: compileall, legacy tests, pytest suites, benchmark.
Exit code 0 = all pass, 1 = any failure.

Usage:
    python tools/validate_all.py [--skip-ollama] [--skip-benchmark]
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run(label: str, cmd: list[str], timeout: int = 600) -> bool:
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    t0 = time.monotonic()
    try:
        result = subprocess.run(
            cmd, cwd=str(ROOT), timeout=timeout,
            capture_output=False, text=True)
        elapsed = time.monotonic() - t0
        status = "PASS" if result.returncode == 0 else "FAIL"
        print(f"  [{status}] {label} ({elapsed:.1f}s)")
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"  [TIMEOUT] {label} (>{timeout}s)")
        return False
    except Exception as e:
        print(f"  [ERROR] {label}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="WaggleDance full validation")
    parser.add_argument("--skip-ollama", action="store_true",
                        help="Skip Ollama-dependent tests")
    parser.add_argument("--skip-benchmark", action="store_true",
                        help="Skip benchmark run")
    args = parser.parse_args()

    results = {}
    py = sys.executable

    # Step 1: Compile check
    results["compileall"] = _run(
        "Compile check",
        [py, "-m", "compileall", "core", "memory", "web", "waggledance",
         "backend", "integrations", "tests", "-q"])

    # Step 2: Legacy tests
    legacy_cmd = [py, "tests/run_all.py"]
    if args.skip_ollama:
        legacy_cmd.append("--skip-ollama")
    results["legacy"] = _run("Legacy tests", legacy_cmd, timeout=300)

    # Step 3: Pytest — unit + core + app + contracts
    results["pytest_unit"] = _run(
        "Pytest unit/core/app/contracts",
        [py, "-m", "pytest",
         "tests/unit/", "tests/unit_core/", "tests/unit_app/", "tests/contracts/",
         "-q", "--tb=short"])

    # Step 4: Pytest — integration
    results["pytest_integration"] = _run(
        "Pytest integration",
        [py, "-m", "pytest", "tests/integration/", "-q", "--tb=short"])

    # Step 5: Benchmark
    if not args.skip_benchmark:
        results["benchmark"] = _run(
            "Benchmark (30 queries)",
            [py, "tools/run_benchmark.py", "--yaml", "configs/benchmarks.yaml"])

    # Summary
    print(f"\n{'='*60}")
    print("  VALIDATION SUMMARY")
    print(f"{'='*60}")
    all_pass = True
    for name, passed in results.items():
        icon = "PASS" if passed else "FAIL"
        print(f"  [{icon}] {name}")
        if not passed:
            all_pass = False

    if all_pass:
        print(f"\n  ALL {len(results)} CHECKS PASSED")
    else:
        failed = sum(1 for v in results.values() if not v)
        print(f"\n  {failed}/{len(results)} CHECKS FAILED")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
