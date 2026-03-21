"""Tests for core.lora_readiness — LoRAReadinessChecker, ReadinessManifest."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.lora_readiness import (
    LoRAReadinessChecker,
    ReadinessCheck,
    ReadinessManifest,
)


class TestReadinessCheckDataclass(unittest.TestCase):
    def test_defaults(self):
        c = ReadinessCheck(name="test")
        self.assertEqual(c.name, "test")
        self.assertFalse(c.passed)
        self.assertEqual(c.message, "")

    def test_passed(self):
        c = ReadinessCheck(name="gpu", passed=True, message="8GB VRAM")
        self.assertTrue(c.passed)
        self.assertEqual(c.message, "8GB VRAM")


class TestReadinessManifest(unittest.TestCase):
    def test_to_dict_empty(self):
        m = ReadinessManifest()
        d = m.to_dict()
        self.assertFalse(d["ready"])
        self.assertEqual(d["checks"], [])

    def test_to_dict_with_checks(self):
        m = ReadinessManifest(
            checks=[
                ReadinessCheck("deps", True, "ok"),
                ReadinessCheck("gpu", False, "no GPU"),
            ],
            ready=False,
        )
        d = m.to_dict()
        self.assertFalse(d["ready"])
        self.assertEqual(len(d["checks"]), 2)
        self.assertEqual(d["checks"][0]["name"], "deps")
        self.assertTrue(d["checks"][0]["passed"])
        self.assertFalse(d["checks"][1]["passed"])


class TestCheckDependencies(unittest.TestCase):
    @patch("builtins.__import__")
    def test_both_available(self, mock_import):
        """When peft and transformers are importable, check passes."""
        original_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def side_effect(name, *args, **kwargs):
            if name in ("peft", "transformers"):
                return MagicMock()
            return original_import(name, *args, **kwargs)

        mock_import.side_effect = side_effect
        checker = LoRAReadinessChecker()
        result = checker.check_dependencies()
        self.assertTrue(result.passed)
        self.assertIn("available", result.message)

    def test_missing_packages(self):
        """When peft/transformers are not installed, check fails."""
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name in ("peft", "transformers"):
                raise ImportError(f"No module named '{name}'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            checker = LoRAReadinessChecker()
            result = checker.check_dependencies()
            self.assertFalse(result.passed)
            self.assertIn("Missing", result.message)


class TestCheckGPU(unittest.TestCase):
    def test_no_torch(self):
        """Without torch, GPU check fails."""
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "torch":
                raise ImportError("No module named 'torch'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            checker = LoRAReadinessChecker()
            result = checker.check_gpu()
            self.assertFalse(result.passed)
            self.assertIn("torch not installed", result.message)

    def test_no_cuda(self):
        """torch available but no CUDA."""
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        with patch.dict("sys.modules", {"torch": mock_torch}):
            checker = LoRAReadinessChecker()
            result = checker.check_gpu()
            self.assertFalse(result.passed)
            self.assertIn("No CUDA GPU", result.message)

    def test_insufficient_vram(self):
        """CUDA available but VRAM below threshold."""
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        props = MagicMock()
        props.total_mem = 2 * (1024**3)  # 2 GB
        mock_torch.cuda.get_device_properties.return_value = props
        with patch.dict("sys.modules", {"torch": mock_torch}):
            checker = LoRAReadinessChecker(min_vram_gb=4.0)
            result = checker.check_gpu()
            self.assertFalse(result.passed)
            self.assertIn("2.0GB", result.message)

    def test_sufficient_vram(self):
        """CUDA available with enough VRAM."""
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        props = MagicMock()
        props.total_mem = 8 * (1024**3)  # 8 GB
        mock_torch.cuda.get_device_properties.return_value = props
        with patch.dict("sys.modules", {"torch": mock_torch}):
            checker = LoRAReadinessChecker(min_vram_gb=4.0)
            result = checker.check_gpu()
            self.assertTrue(result.passed)
            self.assertIn("8.0GB", result.message)


class TestCheckTrainingData(unittest.TestCase):
    def test_directory_not_found(self):
        checker = LoRAReadinessChecker(training_data_dir="/nonexistent/path/xyz123")
        result = checker.check_training_data()
        self.assertFalse(result.passed)
        self.assertIn("not found", result.message)

    def test_insufficient_samples(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            jsonl = Path(tmpdir) / "train.jsonl"
            jsonl.write_text("\n".join(['{"text":"a"}'] * 10), encoding="utf-8")
            checker = LoRAReadinessChecker(training_data_dir=tmpdir, min_samples=100)
            result = checker.check_training_data()
            self.assertFalse(result.passed)
            self.assertIn("10 samples", result.message)

    def test_sufficient_samples(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            jsonl = Path(tmpdir) / "train.jsonl"
            jsonl.write_text(
                "\n".join(['{"text":"sample"}'] * 1500), encoding="utf-8",
            )
            checker = LoRAReadinessChecker(training_data_dir=tmpdir, min_samples=1000)
            result = checker.check_training_data()
            self.assertTrue(result.passed)
            self.assertIn("1500", result.message)

    def test_multiple_jsonl_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            for i in range(3):
                f = Path(tmpdir) / f"data_{i}.jsonl"
                f.write_text(
                    "\n".join(['{"text":"x"}'] * 400), encoding="utf-8",
                )
            checker = LoRAReadinessChecker(training_data_dir=tmpdir, min_samples=1000)
            result = checker.check_training_data()
            self.assertTrue(result.passed)
            self.assertIn("1200", result.message)

    def test_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            checker = LoRAReadinessChecker(training_data_dir=tmpdir, min_samples=100)
            result = checker.check_training_data()
            self.assertFalse(result.passed)
            self.assertIn("0 samples", result.message)


class TestCheckDiskSpace(unittest.TestCase):
    def test_disk_space_check_runs(self):
        """Disk space check on current directory should return a result."""
        checker = LoRAReadinessChecker(training_data_dir=".")
        result = checker.check_disk_space()
        # Should pass on any machine with >2GB free
        self.assertIsInstance(result, ReadinessCheck)
        self.assertEqual(result.name, "disk_space")
        # Message should contain GB info
        self.assertIn("GB", result.message)

    def test_disk_space_nonexistent_dir_falls_back(self):
        """Non-existent dir falls back to '.' for disk check."""
        checker = LoRAReadinessChecker(training_data_dir="/nonexistent/xyz123")
        result = checker.check_disk_space()
        self.assertIsInstance(result, ReadinessCheck)
        self.assertEqual(result.name, "disk_space")


class TestFullCheck(unittest.TestCase):
    def test_full_check_returns_manifest(self):
        """full_check returns a ReadinessManifest with 4 checks."""
        with tempfile.TemporaryDirectory() as tmpdir:
            checker = LoRAReadinessChecker(training_data_dir=tmpdir)
            manifest = checker.full_check()
            self.assertIsInstance(manifest, ReadinessManifest)
            self.assertEqual(len(manifest.checks), 4)
            check_names = [c.name for c in manifest.checks]
            self.assertIn("dependencies", check_names)
            self.assertIn("gpu", check_names)
            self.assertIn("training_data", check_names)
            self.assertIn("disk_space", check_names)

    def test_full_check_ready_false_when_any_fails(self):
        """If any check fails, ready should be False."""
        with tempfile.TemporaryDirectory() as tmpdir:
            checker = LoRAReadinessChecker(
                training_data_dir=tmpdir, min_samples=99999,
            )
            manifest = checker.full_check()
            self.assertFalse(manifest.ready)

    def test_full_check_to_dict(self):
        """Manifest to_dict returns proper JSON-serializable structure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            checker = LoRAReadinessChecker(training_data_dir=tmpdir)
            manifest = checker.full_check()
            d = manifest.to_dict()
            self.assertIn("ready", d)
            self.assertIn("checks", d)
            self.assertIsInstance(d["checks"], list)
            # Should be JSON-serializable
            json_str = json.dumps(d)
            self.assertIsInstance(json_str, str)


if __name__ == "__main__":
    unittest.main()
