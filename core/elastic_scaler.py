"""
WaggleDance Elastic Scaler — Phase 11
Auto-detects hardware and selects optimal tier configuration.

Tiers:
  minimal      — no GPU / <2GB VRAM
  light        — 2-4GB VRAM
  standard     — 4-8GB VRAM  (current dev: RTX A2000 8GB)
  professional — 8-24GB VRAM
  enterprise   — 24GB+ VRAM  (DGX B200 reference)
"""

import logging
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional

log = logging.getLogger("elastic_scaler")

# ═══════════════════════════════════════════════════════════════
# TIER DEFINITIONS
# ═══════════════════════════════════════════════════════════════

TIERS = {
    "minimal": {
        "chat_model": None,
        "bg_model": None,
        "max_agents": 0,
        "vision": False,
        "micro_tier": "V1",
        "min_vram_gb": 0,
        "min_ram_gb": 4,
    },
    "light": {
        "chat_model": "qwen3:0.6b",
        "bg_model": "smollm2:135m",
        "max_agents": 2,
        "vision": False,
        "micro_tier": "V1+V2",
        "min_vram_gb": 2,
        "min_ram_gb": 8,
    },
    "standard": {
        "chat_model": "phi4-mini",
        "bg_model": "llama3.2:1b",
        "max_agents": 6,
        "vision": False,
        "micro_tier": "V1+V2+V3",
        "min_vram_gb": 4,
        "min_ram_gb": 16,
    },
    "professional": {
        "chat_model": "phi4:14b",
        "bg_model": "qwen3:4b",
        "max_agents": 15,
        "vision": True,
        "micro_tier": "V3",
        "min_vram_gb": 16,
        "min_ram_gb": 32,
    },
    "enterprise": {
        "chat_model": "llama3.3:70b",
        "bg_model": "llama3.1:8b",
        "max_agents": 50,
        "vision": True,
        "micro_tier": "V3",
        "min_vram_gb": 48,
        "min_ram_gb": 128,
    },
}


@dataclass
class HardwareProfile:
    """Detected hardware capabilities."""
    cpu_cores: int = 0
    cpu_threads: int = 0
    cpu_name: str = ""
    ram_gb: float = 0.0
    gpu_name: str = ""
    gpu_vram_gb: float = 0.0
    gpu_count: int = 0
    disk_free_gb: float = 0.0
    os_name: str = ""
    platform: str = ""


@dataclass
class TierConfig:
    """Selected tier configuration."""
    tier: str = "minimal"
    chat_model: Optional[str] = None
    bg_model: Optional[str] = None
    max_agents: int = 0
    vision: bool = False
    micro_tier: str = "V1"
    hardware: HardwareProfile = field(default_factory=HardwareProfile)
    reason: str = ""


class ElasticScaler:
    """Auto-detects hardware and selects optimal operational tier."""

    def __init__(self):
        self._hardware: Optional[HardwareProfile] = None
        self._tier: Optional[TierConfig] = None

    def detect(self) -> TierConfig:
        """Detect hardware and classify into tier. Main entry point."""
        hw = self._detect_hardware()
        self._hardware = hw
        tier = self._classify_tier(hw)
        self._tier = tier
        log.info(
            f"ElasticScaler: {tier.tier} tier "
            f"(GPU={hw.gpu_name or 'none'}, "
            f"VRAM={hw.gpu_vram_gb:.1f}GB, "
            f"RAM={hw.ram_gb:.0f}GB, "
            f"CPU={hw.cpu_cores}C/{hw.cpu_threads}T)")
        return tier

    @property
    def hardware(self) -> HardwareProfile:
        if not self._hardware:
            self.detect()
        return self._hardware

    @property
    def tier(self) -> TierConfig:
        if not self._tier:
            self.detect()
        return self._tier

    # ── Hardware detection ─────────────────────────────────────

    def _detect_hardware(self) -> HardwareProfile:
        hw = HardwareProfile()
        hw.os_name = platform.system()
        hw.platform = platform.platform()

        # CPU
        hw.cpu_cores = os.cpu_count() or 1
        hw.cpu_threads = hw.cpu_cores  # default
        hw.cpu_name = self._detect_cpu_name()

        # RAM
        hw.ram_gb = self._detect_ram_gb()

        # GPU (NVIDIA via nvidia-smi)
        gpu_info = self._detect_nvidia_gpu()
        if gpu_info:
            hw.gpu_name = gpu_info.get("name", "")
            hw.gpu_vram_gb = gpu_info.get("vram_gb", 0.0)
            hw.gpu_count = gpu_info.get("count", 1)

        # Disk
        hw.disk_free_gb = self._detect_disk_free_gb()

        return hw

    def _detect_cpu_name(self) -> str:
        """Get CPU name string."""
        try:
            if platform.system() == "Windows":
                result = subprocess.run(
                    ["wmic", "cpu", "get", "name"],
                    capture_output=True, text=True, timeout=5)
                lines = [l.strip() for l in result.stdout.splitlines()
                         if l.strip() and l.strip() != "Name"]
                if lines:
                    return lines[0]
            else:
                with open("/proc/cpuinfo") as f:
                    for line in f:
                        if line.startswith("model name"):
                            return line.split(":")[1].strip()
        except Exception:
            pass
        return platform.processor() or "unknown"

    def _detect_ram_gb(self) -> float:
        """Detect total system RAM in GB."""
        try:
            if platform.system() == "Windows":
                result = subprocess.run(
                    ["wmic", "computersystem", "get", "totalphysicalmemory"],
                    capture_output=True, text=True, timeout=5)
                for line in result.stdout.splitlines():
                    line = line.strip()
                    if line.isdigit():
                        return int(line) / (1024 ** 3)
            else:
                with open("/proc/meminfo") as f:
                    for line in f:
                        if line.startswith("MemTotal:"):
                            kb = int(line.split()[1])
                            return kb / (1024 ** 2)
        except Exception:
            pass
        return 0.0

    def _detect_nvidia_gpu(self) -> Optional[Dict]:
        """Detect NVIDIA GPU via nvidia-smi."""
        try:
            result = subprocess.run(
                ["nvidia-smi",
                 "--query-gpu=name,memory.total,count",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                lines = [l.strip() for l in result.stdout.splitlines()
                         if l.strip()]
                if lines:
                    parts = lines[0].split(",")
                    name = parts[0].strip() if len(parts) > 0 else ""
                    vram_mb = float(parts[1].strip()) if len(parts) > 1 else 0
                    count = int(parts[2].strip()) if len(parts) > 2 else 1
                    return {
                        "name": name,
                        "vram_gb": vram_mb / 1024.0,
                        "count": count,
                    }
        except FileNotFoundError:
            pass  # nvidia-smi not found
        except Exception as e:
            log.debug(f"nvidia-smi error: {e}")
        return None

    def _detect_disk_free_gb(self) -> float:
        """Detect free disk space on current drive."""
        try:
            usage = shutil.disk_usage(".")
            return usage.free / (1024 ** 3)
        except Exception:
            return 0.0

    # ── Tier classification ────────────────────────────────────

    def _classify_tier(self, hw: HardwareProfile) -> TierConfig:
        """Classify hardware into the best matching tier."""
        vram = hw.gpu_vram_gb
        ram = hw.ram_gb

        # Walk tiers from highest to lowest
        for tier_name in ["enterprise", "professional", "standard",
                          "light", "minimal"]:
            spec = TIERS[tier_name]
            if vram >= spec["min_vram_gb"] and ram >= spec["min_ram_gb"]:
                config = TierConfig(
                    tier=tier_name,
                    chat_model=spec["chat_model"],
                    bg_model=spec["bg_model"],
                    max_agents=spec["max_agents"],
                    vision=spec["vision"],
                    micro_tier=spec["micro_tier"],
                    hardware=hw,
                    reason=(f"VRAM={vram:.1f}GB>={spec['min_vram_gb']}GB, "
                            f"RAM={ram:.0f}GB>={spec['min_ram_gb']}GB"),
                )
                return config

        # Fallback: minimal
        return TierConfig(
            tier="minimal",
            hardware=hw,
            reason=f"VRAM={vram:.1f}GB, RAM={ram:.0f}GB below all thresholds",
        )

    # ── Runtime scaling ────────────────────────────────────────

    def should_unload_model(self, vram_used_pct: float) -> bool:
        """Returns True if VRAM usage exceeds 90%."""
        return vram_used_pct > 90.0

    def should_spawn_agent(self, queue_depth: int) -> bool:
        """Returns True if task queue > 10 items."""
        return queue_depth > 10

    def get_vram_usage_pct(self) -> float:
        """Query current VRAM usage percentage via nvidia-smi."""
        try:
            result = subprocess.run(
                ["nvidia-smi",
                 "--query-gpu=memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                parts = result.stdout.strip().split(",")
                if len(parts) >= 2:
                    used = float(parts[0].strip())
                    total = float(parts[1].strip())
                    if total > 0:
                        return (used / total) * 100.0
        except Exception:
            pass
        return 0.0

    def summary(self) -> Dict:
        """Return full detection summary as dict."""
        t = self.tier
        h = t.hardware
        return {
            "tier": t.tier,
            "chat_model": t.chat_model,
            "bg_model": t.bg_model,
            "max_agents": t.max_agents,
            "vision": t.vision,
            "micro_tier": t.micro_tier,
            "reason": t.reason,
            "hardware": {
                "cpu": h.cpu_name,
                "cpu_cores": h.cpu_cores,
                "ram_gb": round(h.ram_gb, 1),
                "gpu": h.gpu_name or "none",
                "gpu_vram_gb": round(h.gpu_vram_gb, 1),
                "gpu_count": h.gpu_count,
                "disk_free_gb": round(h.disk_free_gb, 1),
                "os": h.os_name,
            },
        }

    # ── v2.0: ResourceKernel bridge ────────────────────────────

    def get_resource_kernel(self):
        """Return a ResourceKernel wrapping this ElasticScaler's tier.

        Lazily imports to avoid circular dependency. Returns None if
        the autonomy module is not installed.
        """
        try:
            from waggledance.core.autonomy.resource_kernel import ResourceKernel
            tier_name = self.tier.tier
            rk = ResourceKernel(tier=tier_name)
            rk.start()
            return rk
        except ImportError:
            return None
