"""LoRA readiness checker — dependency, GPU, data, and model availability checks."""

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

@dataclass
class ReadinessCheck:
    name: str
    passed: bool = False
    message: str = ""

@dataclass
class ReadinessManifest:
    checks: list[ReadinessCheck] = field(default_factory=list)
    ready: bool = False

    def to_dict(self) -> dict:
        return {
            "ready": self.ready,
            "checks": [{"name": c.name, "passed": c.passed, "message": c.message}
                       for c in self.checks],
        }

class LoRAReadinessChecker:
    """Check if the system is ready for LoRA fine-tuning."""

    def __init__(self, training_data_dir: str = "data/training",
                 min_samples: int = 1000,
                 min_vram_gb: float = 4.0):
        self.training_data_dir = training_data_dir
        self.min_samples = min_samples
        self.min_vram_gb = min_vram_gb

    def check_dependencies(self) -> ReadinessCheck:
        """Check if peft and transformers are installed."""
        missing = []
        for pkg in ["peft", "transformers"]:
            try:
                __import__(pkg)
            except ImportError:
                missing.append(pkg)
        if missing:
            return ReadinessCheck("dependencies", False, f"Missing: {', '.join(missing)}")
        return ReadinessCheck("dependencies", True, "peft and transformers available")

    def check_gpu(self) -> ReadinessCheck:
        """Check GPU VRAM availability."""
        try:
            import torch
            if not torch.cuda.is_available():
                return ReadinessCheck("gpu", False, "No CUDA GPU available")
            vram_gb = torch.cuda.get_device_properties(0).total_mem / (1024**3)
            if vram_gb < self.min_vram_gb:
                return ReadinessCheck("gpu", False,
                    f"VRAM {vram_gb:.1f}GB < {self.min_vram_gb}GB minimum")
            return ReadinessCheck("gpu", True, f"VRAM: {vram_gb:.1f}GB")
        except ImportError:
            return ReadinessCheck("gpu", False, "torch not installed")
        except Exception as e:
            return ReadinessCheck("gpu", False, str(e))

    def check_training_data(self) -> ReadinessCheck:
        """Check if sufficient training data exists."""
        data_path = Path(self.training_data_dir)
        if not data_path.exists():
            return ReadinessCheck("training_data", False, f"Directory not found: {data_path}")

        # Count JSONL lines
        total = 0
        for f in data_path.glob("*.jsonl"):
            try:
                with open(f, encoding="utf-8") as fh:
                    total += sum(1 for _ in fh)
            except Exception:
                pass

        if total < self.min_samples:
            return ReadinessCheck("training_data", False,
                f"{total} samples < {self.min_samples} minimum")
        return ReadinessCheck("training_data", True, f"{total} training samples")

    def check_disk_space(self) -> ReadinessCheck:
        """Check if there's enough disk space for model output."""
        try:
            usage = shutil.disk_usage(self.training_data_dir if Path(self.training_data_dir).exists() else ".")
            free_gb = usage.free / (1024**3)
            if free_gb < 2.0:
                return ReadinessCheck("disk_space", False, f"{free_gb:.1f}GB free < 2.0GB minimum")
            return ReadinessCheck("disk_space", True, f"{free_gb:.1f}GB free")
        except Exception as e:
            return ReadinessCheck("disk_space", False, str(e))

    def full_check(self) -> ReadinessManifest:
        """Run all checks and return manifest."""
        checks = [
            self.check_dependencies(),
            self.check_gpu(),
            self.check_training_data(),
            self.check_disk_space(),
        ]
        manifest = ReadinessManifest(
            checks=checks,
            ready=all(c.passed for c in checks),
        )
        return manifest
