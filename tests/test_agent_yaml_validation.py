"""
Suite #44: Agent YAML Validation
=================================
Validates all 75 agent YAML definitions for required structure,
valid fields, and consistency.

Run: python -m pytest tests/test_agent_yaml_validation.py -v
"""

import os
import sys
import unittest
from pathlib import Path

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import yaml
except ImportError:
    import importlib
    yaml = importlib.import_module("yaml")

AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"
VALID_PROFILES = {"gadget", "cottage", "home", "factory"}
VALID_PRIORITIES = {"critical", "high", "medium", "low"}

# All 75 agent directories (excluding __pycache__)
AGENT_NAMES = sorted([
    d.name for d in AGENTS_DIR.iterdir()
    if d.is_dir() and d.name != "__pycache__"
])


def load_core_yaml(agent_name: str) -> dict:
    """Load core.yaml for an agent, return parsed dict."""
    path = AGENTS_DIR / agent_name / "core.yaml"
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class TestAgentYAMLValidation(unittest.TestCase):
    """Validate structural integrity of all agent YAML definitions."""

    def test_00_agents_dir_exists(self):
        """agents/ directory exists and contains agents."""
        self.assertTrue(AGENTS_DIR.exists(), "agents/ directory missing")
        self.assertGreaterEqual(len(AGENT_NAMES), 72, f"Expected 72+ agents, found {len(AGENT_NAMES)}")

    def test_01_agent_count(self):
        """Exactly 75 agent directories exist."""
        self.assertEqual(len(AGENT_NAMES), 75, f"Expected 75 agents, found {len(AGENT_NAMES)}: {AGENT_NAMES}")


# ── Per-agent tests (dynamically generated) ──────────────────────

def _make_core_yaml_exists_test(agent_name):
    def test(self):
        path = AGENTS_DIR / agent_name / "core.yaml"
        self.assertTrue(path.exists(), f"{agent_name}/core.yaml missing")
    test.__doc__ = f"{agent_name}: core.yaml exists"
    return test


def _make_yaml_parseable_test(agent_name):
    def test(self):
        path = AGENTS_DIR / agent_name / "core.yaml"
        try:
            data = load_core_yaml(agent_name)
        except Exception as e:
            self.fail(f"{agent_name}/core.yaml parse error: {e}")
        self.assertIsInstance(data, dict, f"{agent_name}/core.yaml did not parse to dict")
    test.__doc__ = f"{agent_name}: core.yaml is valid YAML"
    return test


def _make_header_test(agent_name):
    def test(self):
        data = load_core_yaml(agent_name)
        self.assertIn("header", data, f"{agent_name}: missing 'header' section")
        header = data["header"]
        self.assertIsInstance(header, dict, f"{agent_name}: header is not a dict")
        self.assertIn("agent_id", header, f"{agent_name}: header missing 'agent_id'")
        self.assertEqual(header["agent_id"], agent_name,
                         f"{agent_name}: agent_id '{header['agent_id']}' != directory name '{agent_name}'")
    test.__doc__ = f"{agent_name}: header.agent_id matches directory"
    return test


def _make_profiles_test(agent_name):
    def test(self):
        data = load_core_yaml(agent_name)
        self.assertIn("profiles", data, f"{agent_name}: missing 'profiles'")
        profiles = data["profiles"]
        self.assertIsInstance(profiles, list, f"{agent_name}: profiles is not a list")
        self.assertGreater(len(profiles), 0, f"{agent_name}: profiles list is empty")
        for p in profiles:
            self.assertIn(p, VALID_PROFILES,
                          f"{agent_name}: invalid profile '{p}', expected one of {VALID_PROFILES}")
    test.__doc__ = f"{agent_name}: profiles are valid"
    return test


def _make_no_empty_strings_test(agent_name):
    def test(self):
        data = load_core_yaml(agent_name)
        header = data.get("header", {})
        agent_id = header.get("agent_id", "")
        self.assertTrue(len(agent_id.strip()) > 0, f"{agent_name}: agent_id is empty")
    test.__doc__ = f"{agent_name}: agent_id is non-empty"
    return test


# Register per-agent tests dynamically
for _agent in AGENT_NAMES:
    setattr(TestAgentYAMLValidation, f"test_10_{_agent}_core_exists",
            _make_core_yaml_exists_test(_agent))
    setattr(TestAgentYAMLValidation, f"test_20_{_agent}_parseable",
            _make_yaml_parseable_test(_agent))
    setattr(TestAgentYAMLValidation, f"test_30_{_agent}_header",
            _make_header_test(_agent))
    setattr(TestAgentYAMLValidation, f"test_40_{_agent}_profiles",
            _make_profiles_test(_agent))
    setattr(TestAgentYAMLValidation, f"test_50_{_agent}_nonempty",
            _make_no_empty_strings_test(_agent))


# ── Cross-agent consistency tests ─────────────────────────────────

class TestAgentCrossValidation(unittest.TestCase):
    """Cross-agent consistency checks."""

    def test_all_agents_have_unique_ids(self):
        """All agent_id values are unique across agents."""
        ids = []
        for agent_name in AGENT_NAMES:
            try:
                data = load_core_yaml(agent_name)
                ids.append(data.get("header", {}).get("agent_id", "MISSING"))
            except Exception:
                ids.append(f"ERROR_{agent_name}")
        self.assertEqual(len(ids), len(set(ids)),
                         f"Duplicate agent_ids found: {[x for x in ids if ids.count(x) > 1]}")

    def test_all_yaml_files_utf8(self):
        """All YAML files are valid UTF-8."""
        errors = []
        for agent_name in AGENT_NAMES:
            for yaml_file in (AGENTS_DIR / agent_name).glob("*.yaml"):
                try:
                    yaml_file.read_text(encoding="utf-8")
                except UnicodeDecodeError as e:
                    errors.append(f"{agent_name}/{yaml_file.name}: {e}")
        self.assertEqual(len(errors), 0, f"UTF-8 errors: {errors}")

    def test_no_duplicate_directories(self):
        """No duplicate agent directory names (case-insensitive on Windows)."""
        lower_names = [n.lower() for n in AGENT_NAMES]
        self.assertEqual(len(lower_names), len(set(lower_names)),
                         "Duplicate agent directories (case-insensitive)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
