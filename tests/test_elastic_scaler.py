#!/usr/bin/env python3
"""
WaggleDance — Phase 11: ElasticScaler Tests
============================================
15 tests across 4 groups:
  1. Syntax (1): elastic_scaler.py parses OK
  2. HardwareProfile (3): dataclass defaults, fields present, os detection
  3. TierConfig (3): dataclass defaults, all tiers defined, classification logic
  4. ElasticScaler (8): init, detect, summary keys, tier names, vram thresholds,
                        should_unload, should_spawn, zbook_tier
"""

import ast
import os
import sys
import unittest

_script_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_script_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


class TestSyntax(unittest.TestCase):
    """Group 1: Syntax check."""

    def test_elastic_scaler_syntax(self):
        fpath = os.path.join(_project_root, "core", "elastic_scaler.py")
        self.assertTrue(os.path.exists(fpath), "Missing: core/elastic_scaler.py")
        with open(fpath, encoding="utf-8") as f:
            source = f.read()
        ast.parse(source, filename="core/elastic_scaler.py")


class TestHardwareProfile(unittest.TestCase):
    """Group 2: HardwareProfile dataclass."""

    def setUp(self):
        from core.elastic_scaler import HardwareProfile
        self.HardwareProfile = HardwareProfile

    def test_defaults(self):
        hw = self.HardwareProfile()
        self.assertEqual(hw.cpu_cores, 0)
        self.assertEqual(hw.ram_gb, 0.0)
        self.assertEqual(hw.gpu_vram_gb, 0.0)
        self.assertEqual(hw.gpu_count, 0)
        self.assertEqual(hw.gpu_name, "")

    def test_fields_present(self):
        hw = self.HardwareProfile()
        for field in ["cpu_cores", "cpu_threads", "cpu_name", "ram_gb",
                      "gpu_name", "gpu_vram_gb", "gpu_count",
                      "disk_free_gb", "os_name", "platform"]:
            self.assertTrue(hasattr(hw, field), f"Missing field: {field}")

    def test_set_values(self):
        hw = self.HardwareProfile(
            cpu_cores=16, ram_gb=128.0, gpu_vram_gb=8.0, gpu_name="RTX A2000"
        )
        self.assertEqual(hw.cpu_cores, 16)
        self.assertEqual(hw.ram_gb, 128.0)
        self.assertEqual(hw.gpu_vram_gb, 8.0)
        self.assertEqual(hw.gpu_name, "RTX A2000")


class TestTierConfig(unittest.TestCase):
    """Group 3: TierConfig and TIERS dict."""

    def setUp(self):
        from core.elastic_scaler import TierConfig, TIERS
        self.TierConfig = TierConfig
        self.TIERS = TIERS

    def test_tier_config_defaults(self):
        tc = self.TierConfig()
        self.assertEqual(tc.tier, "minimal")
        self.assertIsNone(tc.chat_model)
        self.assertIsNone(tc.bg_model)
        self.assertEqual(tc.max_agents, 0)
        self.assertFalse(tc.vision)

    def test_all_tiers_defined(self):
        for name in ["minimal", "light", "standard", "professional", "enterprise"]:
            self.assertIn(name, self.TIERS, f"Missing tier: {name}")

    def test_tier_fields(self):
        for name, spec in self.TIERS.items():
            for field in ["chat_model", "bg_model", "max_agents", "vision",
                          "micro_tier", "min_vram_gb", "min_ram_gb"]:
                self.assertIn(field, spec, f"Tier '{name}' missing field '{field}'")


class TestElasticScaler(unittest.TestCase):
    """Group 4: ElasticScaler class."""

    def setUp(self):
        from core.elastic_scaler import ElasticScaler, HardwareProfile, TierConfig
        self.ElasticScaler = ElasticScaler
        self.HardwareProfile = HardwareProfile
        self.TierConfig = TierConfig
        self.scaler = ElasticScaler()

    def test_init(self):
        self.assertIsNone(self.scaler._hardware)
        self.assertIsNone(self.scaler._tier)

    def test_detect_returns_tier_config(self):
        result = self.scaler.detect()
        self.assertIsInstance(result, self.TierConfig)
        self.assertIn(result.tier, ["minimal", "light", "standard",
                                     "professional", "enterprise"])

    def test_summary_keys(self):
        summary = self.scaler.summary()
        for key in ["tier", "chat_model", "bg_model", "max_agents",
                    "vision", "micro_tier", "reason", "hardware"]:
            self.assertIn(key, summary)

    def test_summary_hardware_keys(self):
        summary = self.scaler.summary()
        hw = summary["hardware"]
        for key in ["cpu", "cpu_cores", "ram_gb", "gpu",
                    "gpu_vram_gb", "gpu_count", "disk_free_gb", "os"]:
            self.assertIn(key, hw)

    def test_should_unload_model(self):
        self.assertTrue(self.scaler.should_unload_model(91.0))
        self.assertFalse(self.scaler.should_unload_model(89.0))
        self.assertFalse(self.scaler.should_unload_model(90.0))

    def test_should_spawn_agent(self):
        self.assertTrue(self.scaler.should_spawn_agent(11))
        self.assertFalse(self.scaler.should_spawn_agent(10))
        self.assertFalse(self.scaler.should_spawn_agent(5))

    def test_tier_property_cached(self):
        """tier property returns same object on repeated access."""
        t1 = self.scaler.tier
        t2 = self.scaler.tier
        self.assertIs(t1, t2)

    def test_zbook_tier(self):
        """ZBook RTX A2000 8GB + 128GB RAM → standard or higher."""
        from core.elastic_scaler import HardwareProfile, ElasticScaler
        s = ElasticScaler()
        hw = HardwareProfile(gpu_vram_gb=8.0, ram_gb=128.0)
        tier = s._classify_tier(hw)
        self.assertIn(tier.tier, ["standard", "professional", "enterprise"])
        self.assertEqual(tier.chat_model, "phi4-mini")


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  Phase 11: ElasticScaler — Test Suite")
    print("=" * 60 + "\n")
    unittest.main(verbosity=2)
