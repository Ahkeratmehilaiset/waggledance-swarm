"""WaggleSettings — the ONLY place that reads os.environ and .env files.

Also merges runtime.primary and compatibility_mode from configs/settings.yaml
when env vars are not set, providing a single source of truth for cutover config.
"""
# implements ConfigPort

import logging
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_VALID_RUNTIME_PRIMARIES = frozenset({"waggledance", "hivemind", "shadow"})

_SETTINGS_YAML_PATH = Path("configs/settings.yaml")


@dataclass
class WaggleSettings:
    """Centralised application configuration.

    Implements ConfigPort (get, get_profile, get_hardware_tier).
    All environment variable reads happen exclusively in from_env().
    """

    # Core
    profile: str = "HOME"
    ollama_host: str = "http://localhost:11434"
    chroma_dir: str = "./chroma_data"
    db_path: str = "./shared_memory.db"

    # Models
    chat_model: str = "phi4-mini"
    learning_model: str = "llama3.2:1b"
    embed_model: str = "nomic-embed-text"

    # Gemma 4 dual-tier (optional, OFF by default)
    gemma_enabled: bool = False
    gemma_fast_model: str = "gemma4:e4b"
    gemma_heavy_model: str = "gemma4:26b"
    gemma_active_profile: str = "disabled"  # disabled|fast_only|heavy_only|dual_tier
    gemma_heavy_reasoning_only: bool = True
    gemma_degrade_to_default: bool = True

    # Parallel LLM dispatch (optional, OFF by default)
    llm_parallel_enabled: bool = False
    llm_parallel_max_concurrent: int = 4
    llm_parallel_max_inflight_per_model: int = 2
    llm_parallel_request_timeout_s: int = 120
    llm_parallel_round_table_first_pass: bool = False
    llm_parallel_dream_batch: int = 1
    llm_parallel_candidate_lab: int = 1
    llm_parallel_verifier_advisory: int = 1
    llm_parallel_dedupe: bool = True

    # Hardware tier (auto-detected if not set)
    hardware_tier: str = "auto"

    # Auth
    api_key: str = ""

    # Limits
    max_agents: int = 75
    round_table_size: int = 6
    hot_cache_size: int = 1000
    night_mode_idle_minutes: int = 30

    # Timeouts -- BUG 3 fix: was 30s, caused embed failures under load
    ollama_timeout_seconds: float = 120.0

    # Night learning -- BUG 3 fix: convergence stall threshold
    night_stall_threshold: int = 10

    # Runtime cutover
    runtime_primary: str = "waggledance"
    compatibility_mode: bool = False

    # Extra settings loaded from YAML, kept in a flat dict for dotted-key access
    _extras: dict = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------------ #
    #  Factory                                                            #
    # ------------------------------------------------------------------ #

    @classmethod
    def from_env(cls, env_path: str = ".env",
                 yaml_path: str | Path | None = None) -> "WaggleSettings":
        """Load from environment variables and .env file.

        This is the ONLY place in the entire application that reads
        os.environ or .env files.  It is also the ONLY place that
        generates a default API key when none is provided.

        Resolution order (highest wins):
          1. Environment variables (WAGGLE_*)
          2. configs/settings.yaml (for runtime section + profile)
          3. Dataclass defaults
        """
        # Try loading .env file into os.environ (best-effort)
        _load_dotenv(env_path)

        # Merge settings.yaml as base layer
        yaml_base = _load_yaml_settings(yaml_path)
        yaml_runtime = yaml_base.get("runtime", {})

        # Gemma profiles from YAML
        gemma_cfg = yaml_base.get("gemma_profiles", {})

        # Parallel LLM dispatch from YAML
        par_cfg = yaml_base.get("llm_parallel", {})

        settings = cls(
            profile=os.environ.get(
                "WAGGLE_PROFILE",
                yaml_base.get("profile", "home")).upper(),
            ollama_host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
            chroma_dir=os.environ.get("CHROMA_DIR", "./chroma_data"),
            db_path=os.environ.get("WAGGLE_DB_PATH", "./shared_memory.db"),
            chat_model=os.environ.get("WAGGLE_CHAT_MODEL", "phi4-mini"),
            learning_model=os.environ.get("WAGGLE_LEARNING_MODEL", "llama3.2:1b"),
            embed_model=os.environ.get("WAGGLE_EMBED_MODEL", "nomic-embed-text"),
            gemma_enabled=_parse_bool(os.environ.get(
                "WAGGLE_GEMMA_ENABLED",
                str(gemma_cfg.get("enabled", False)))),
            gemma_fast_model=os.environ.get(
                "WAGGLE_GEMMA_FAST_MODEL",
                gemma_cfg.get("fast_model", "gemma4:e4b")),
            gemma_heavy_model=os.environ.get(
                "WAGGLE_GEMMA_HEAVY_MODEL",
                gemma_cfg.get("heavy_model", "gemma4:26b")),
            gemma_active_profile=os.environ.get(
                "WAGGLE_GEMMA_PROFILE",
                gemma_cfg.get("active_profile", "disabled")),
            gemma_heavy_reasoning_only=_parse_bool(os.environ.get(
                "WAGGLE_GEMMA_HEAVY_REASONING_ONLY",
                str(gemma_cfg.get("heavy_reasoning_only", True)))),
            gemma_degrade_to_default=_parse_bool(os.environ.get(
                "WAGGLE_GEMMA_DEGRADE_TO_DEFAULT",
                str(gemma_cfg.get("degrade_to_default", True)))),
            llm_parallel_enabled=_parse_bool(os.environ.get(
                "WAGGLE_LLM_PARALLEL_ENABLED",
                str(par_cfg.get("enabled", False)))),
            llm_parallel_max_concurrent=int(os.environ.get(
                "WAGGLE_LLM_PARALLEL_MAX_CONCURRENT",
                par_cfg.get("max_concurrent", 4))),
            llm_parallel_max_inflight_per_model=int(os.environ.get(
                "WAGGLE_LLM_PARALLEL_MAX_INFLIGHT",
                par_cfg.get("max_inflight_per_model", 2))),
            llm_parallel_request_timeout_s=int(os.environ.get(
                "WAGGLE_LLM_PARALLEL_TIMEOUT",
                par_cfg.get("request_timeout_s", 120))),
            llm_parallel_round_table_first_pass=_parse_bool(os.environ.get(
                "WAGGLE_LLM_PARALLEL_RT_FIRST",
                str(par_cfg.get("round_table_parallel_first_pass", False)))),
            llm_parallel_dream_batch=int(os.environ.get(
                "WAGGLE_LLM_PARALLEL_DREAM",
                par_cfg.get("dream_batch_parallelism", 1))),
            llm_parallel_candidate_lab=int(os.environ.get(
                "WAGGLE_LLM_PARALLEL_CANDIDATE",
                par_cfg.get("candidate_lab_parallelism", 1))),
            llm_parallel_verifier_advisory=int(os.environ.get(
                "WAGGLE_LLM_PARALLEL_VERIFIER",
                par_cfg.get("verifier_advisory_parallelism", 1))),
            llm_parallel_dedupe=_parse_bool(os.environ.get(
                "WAGGLE_LLM_PARALLEL_DEDUPE",
                str(par_cfg.get("dedupe_identical_prompts", True)))),
            hardware_tier=os.environ.get("WAGGLE_HW_TIER", "auto"),
            api_key=os.environ.get("WAGGLE_API_KEY", "").strip(),
            max_agents=int(os.environ.get("WAGGLE_MAX_AGENTS", "75")),
            round_table_size=int(os.environ.get("WAGGLE_RT_SIZE", "6")),
            hot_cache_size=int(os.environ.get("WAGGLE_CACHE_SIZE", "1000")),
            night_mode_idle_minutes=int(
                os.environ.get("WAGGLE_NIGHT_IDLE_MIN", "30")
            ),
            ollama_timeout_seconds=float(
                os.environ.get("WAGGLE_OLLAMA_TIMEOUT", "120.0")
            ),
            night_stall_threshold=int(
                os.environ.get("WAGGLE_STALL_THRESHOLD", "10")
            ),
            runtime_primary=os.environ.get(
                "WAGGLE_RUNTIME_PRIMARY",
                yaml_runtime.get("primary", "waggledance")),
            compatibility_mode=_parse_bool(os.environ.get(
                "WAGGLE_COMPAT_MODE",
                str(yaml_runtime.get("compatibility_mode", False)))),
            _extras=yaml_base,
        )

        # Validate runtime_primary
        if settings.runtime_primary not in _VALID_RUNTIME_PRIMARIES:
            logger.error(
                "Invalid runtime_primary '%s' — must be one of %s. "
                "Falling back to 'waggledance'.",
                settings.runtime_primary, sorted(_VALID_RUNTIME_PRIMARIES))
            settings.runtime_primary = "waggledance"

        # Auto-generate API key if none provided
        if not settings.api_key:
            settings.api_key = secrets.token_urlsafe(32)
            logger.warning(
                "No API key configured -- auto-generated (set WAGGLE_API_KEY to override)"
            )

        return settings

    # ------------------------------------------------------------------ #
    #  Runtime mode helpers                                               #
    # ------------------------------------------------------------------ #

    @property
    def is_autonomy_primary(self) -> bool:
        """True when waggledance runtime is the primary path."""
        return (self.runtime_primary == "waggledance"
                and not self.compatibility_mode)

    @property
    def is_shadow_mode(self) -> bool:
        """True when running in shadow mode (both runtimes, new shadows)."""
        return self.runtime_primary == "shadow"

    def runtime_diagnostics(self) -> dict:
        """Return a diagnostic summary for startup logging."""
        return {
            "runtime_primary": self.runtime_primary,
            "compatibility_mode": self.compatibility_mode,
            "is_autonomy_primary": self.is_autonomy_primary,
            "profile": self.profile,
            "hardware_tier": self.hardware_tier,
        }

    # ------------------------------------------------------------------ #
    #  ConfigPort interface                                               #
    # ------------------------------------------------------------------ #

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value by key.

        Supports dotted keys (e.g. 'llm.model' maps to chat_model,
        'swarm.top_k' looks in _extras).  Falls back to dataclass
        attributes first, then _extras, then default.
        """
        # Direct attribute lookup
        if hasattr(self, key) and not key.startswith("_"):
            return getattr(self, key)

        # Dotted-key lookup in extras
        if key in self._extras:
            return self._extras[key]

        # Walk dotted path in extras
        parts = key.split(".")
        node: Any = self._extras
        for part in parts:
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node

    def get_profile(self) -> str:
        """Return the active profile in uppercase."""
        return self.profile.upper()

    def get_hardware_tier(self) -> str:
        """Return hardware tier setting.

        Returns "auto" when auto-detection is requested — the Container
        resolves the actual tier via ElasticScaler (single source of truth).
        Returns the explicit tier string otherwise.
        """
        return self.hardware_tier


# ------------------------------------------------------------------ #
#  Helpers                                                            #
# ------------------------------------------------------------------ #


def _load_dotenv(env_path: str) -> None:
    """Best-effort .env loader.  No external dependency (no python-dotenv)."""
    path = Path(env_path)
    if not path.is_file():
        return
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception as exc:
        logger.warning("Could not read %s: %s", env_path, exc)


def _load_yaml_settings(yaml_path: str | Path | None = None) -> dict:
    """Load settings.yaml as a base config layer. Returns empty dict on failure."""
    path = Path(yaml_path) if yaml_path else _SETTINGS_YAML_PATH
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("Could not read settings YAML %s: %s", path, exc)
        return {}


def _parse_bool(value: str) -> bool:
    """Parse a boolean from a string (env var or YAML value)."""
    return str(value).lower() in ("true", "1", "yes", "on")
