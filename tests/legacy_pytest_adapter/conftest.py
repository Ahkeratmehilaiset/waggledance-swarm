"""Pytest plugin: discover and run legacy test_*.py files as subprocess items.

Legacy test files use sys.exit() and print-based assertion patterns,
so they cannot be imported directly by pytest. This adapter runs each
as a subprocess and reports pass/fail based on exit code and output.

To use: pytest --legacy tests/legacy_pytest_adapter/
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

# Skip patterns — these are already pytest-native
SKIP_PREFIXES = {
    "tests/unit/",
    "tests/unit_core/",
    "tests/unit_app/",
    "tests/contracts/",
    "tests/integration/",
    "tests/legacy_pytest_adapter/",
}

# Tests requiring Ollama server
OLLAMA_TESTS = {
    "test_corrections.py",
    "test_routing_centroids.py",
    "test_phase4.py",
    "test_phase4ijk.py",
}


def _is_legacy_test(path: Path) -> bool:
    """Check if a test file is a legacy (non-pytest) test."""
    rel = path.as_posix()
    if any(rel.startswith(prefix) for prefix in SKIP_PREFIXES):
        return False
    return path.name.startswith("test_") and path.suffix == ".py"


def _discover_legacy_tests(root: Path, skip_ollama: bool = True) -> list[Path]:
    """Find all legacy test files."""
    tests = []
    search_dir = root / "tests"
    if not search_dir.exists():
        return tests
    for f in sorted(search_dir.glob("test_*.py")):
        if not _is_legacy_test(f.relative_to(root)):
            continue
        if skip_ollama and f.name in OLLAMA_TESTS:
            continue
        tests.append(f)
    return tests


class LegacyTestFailure(Exception):
    """Raised when a legacy test subprocess fails."""
    pass


class LegacyTestItem(pytest.Item):
    """A pytest item that runs a legacy test file as a subprocess."""

    def __init__(self, name, parent, test_path: Path):
        super().__init__(name, parent)
        self.test_path = test_path

    def runtest(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.test_path.parent.parent)

        result = subprocess.run(
            [sys.executable, str(self.test_path)],
            capture_output=True,
            text=True,
            timeout=180,
            cwd=str(self.test_path.parent.parent),
            env=env,
        )

        if result.returncode != 0:
            output = (result.stdout or "") + "\n" + (result.stderr or "")
            raise LegacyTestFailure(
                f"Legacy test {self.test_path.name} exited with code {result.returncode}\n"
                f"Output:\n{output[-2000:]}"
            )

    def repr_failure(self, excinfo, style=None):
        return str(excinfo.value)

    def reportinfo(self):
        return self.test_path, 0, f"legacy::{self.test_path.name}"


class LegacyTestCollector(pytest.File):
    """Collects a single legacy test file as a LegacyTestItem."""

    def collect(self):
        yield LegacyTestItem.from_parent(
            self,
            name=self.path.stem,
            test_path=self.path,
        )
