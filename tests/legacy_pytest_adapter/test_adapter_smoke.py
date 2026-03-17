"""Smoke tests for the legacy pytest adapter (v1.17.0)."""

import importlib.util
import unittest
from pathlib import Path

# Import from conftest in same directory (use importlib to avoid shadowing
# by tests/conftest.py which only has collect_ignore)
_conftest_path = Path(__file__).parent / "conftest.py"
_spec = importlib.util.spec_from_file_location("legacy_conftest", _conftest_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
_is_legacy_test = _mod._is_legacy_test
_discover_legacy_tests = _mod._discover_legacy_tests
LegacyTestFailure = _mod.LegacyTestFailure
SKIP_PREFIXES = _mod.SKIP_PREFIXES
OLLAMA_TESTS = _mod.OLLAMA_TESTS


class TestLegacyDiscovery(unittest.TestCase):
    def test_is_legacy_test_excludes_pytest_dirs(self):
        for prefix in SKIP_PREFIXES:
            path = Path(prefix + "test_foo.py")
            self.assertFalse(_is_legacy_test(path), f"Should exclude {path}")

    def test_is_legacy_test_includes_root_tests(self):
        path = Path("tests/test_pipeline.py")
        self.assertTrue(_is_legacy_test(path))

    def test_is_legacy_test_rejects_non_test(self):
        self.assertFalse(_is_legacy_test(Path("tests/conftest.py")))
        self.assertFalse(_is_legacy_test(Path("tests/helper.py")))

    def test_discover_finds_legacy_files(self):
        root = Path(__file__).parent.parent.parent  # project root
        files = _discover_legacy_tests(root, skip_ollama=True)
        names = {f.name for f in files}
        self.assertIn("test_pipeline.py", names)
        for skip in OLLAMA_TESTS:
            self.assertNotIn(skip, names)


class TestLegacyTestFailure(unittest.TestCase):
    def test_exception_message(self):
        exc = LegacyTestFailure("test_foo.py failed with code 1")
        self.assertIn("test_foo.py", str(exc))

    def test_is_exception(self):
        self.assertTrue(issubclass(LegacyTestFailure, Exception))


if __name__ == "__main__":
    unittest.main()
