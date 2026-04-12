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


def _find_project_root() -> Path:
    """Walk up from this file to find the project root.

    A directory counts as the project root when it contains any of these
    markers: ``requirements.txt``, ``pyproject.toml``, ``configs``. This makes
    settings-loading deterministic regardless of the current working directory.
    """
    here = Path(__file__).resolve()
    for candidate in (here, *here.parents):
        if candidate.is_file():
            continue
        for marker in ("requirements.txt", "pyproject.toml", "configs"):
            if (candidate / marker).exists():
                return candidate
    # Fallback: repo layout is waggledance/adapters/config/settings_loader.py,
    # so parents[3] is the project root on a normal checkout.
    return here.parents[3] if len(here.parents) >= 4 else Path.cwd()


_PROJECT_ROOT = _find_project_root()
_SETTINGS_YAML_PATH = _PROJECT_ROOT / "configs" / "settings.yaml"
_DEFAULT_DOTENV_PATH = _PROJECT_ROOT / ".env"


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

    # Auth — repr=False so the key never leaks via logging/repr/str(settings).
    # The only legitimate reader is the auth middleware (direct attribute
    # access). `runtime_diagnostics()` only exposes a bool.
    api_key: str = field(default="", repr=False)

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
    def from_env(cls, env_path: str | Path | None = None,
                 yaml_path: str | Path | None = None,
                 preset_path: str | Path | None = None) -> "WaggleSettings":
        """Load from environment variables and .env file.

        This is the ONLY place in the entire application that reads
        os.environ or .env files.  It is also the ONLY place that
        generates a default API key when none is provided.

        Resolution order (highest wins):
          1. Environment variables (WAGGLE_*)
          2. Hardware preset YAML (``configs/presets/*.yaml``, selected
             via ``--preset`` on the CLI or ``WAGGLE_PRESET_PATH`` env)
          3. configs/settings.yaml (for runtime section + profile)
          4. Dataclass defaults

        F1-006 (Release Polish Run 20260409_054702): the preset layer
        was previously ignored — ``start_waggledance.py --preset=X``
        loaded the YAML for the banner and threw it away. It now lands
        here and sources four directly-mapped fields (``profile``,
        ``chat_model``, ``embed_model``, ``max_agents``) so ``--preset``
        does what the help text promises.
        """
        # Try loading .env file into os.environ (best-effort).
        # When no explicit path is given, default to the project-root-anchored
        # path so the loader is deterministic regardless of cwd.
        _load_dotenv(env_path if env_path is not None else _DEFAULT_DOTENV_PATH)

        # Merge settings.yaml as base layer
        yaml_base = _load_yaml_settings(yaml_path)
        yaml_runtime = yaml_base.get("runtime", {})

        # Merge preset YAML as a layer ABOVE settings.yaml but BELOW env
        # vars. The preset is an explicit operator choice (they typed
        # ``--preset=factory-production``) so it should override the
        # defaults in settings.yaml, but any explicit ``WAGGLE_*`` env
        # var they also set is even more explicit and still wins.
        # ``WAGGLE_PRESET_PATH`` is set by ``start_waggledance.py`` when
        # ``--preset`` is used; the yaml_path keyword is still honoured
        # so tests can drive the logic directly.
        resolved_preset = (
            preset_path
            if preset_path is not None
            else os.environ.get("WAGGLE_PRESET_PATH", "")
        )
        preset_base: dict = {}
        preset_loaded_from: Path | None = None
        if resolved_preset:
            preset_file = Path(resolved_preset)
            if preset_file.is_file():
                try:
                    loaded = yaml.safe_load(preset_file.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        preset_base = loaded
                        preset_loaded_from = preset_file
                    else:
                        logger.warning(
                            "Preset YAML %s did not parse to a dict "
                            "(got %s); ignoring.",
                            preset_file,
                            type(loaded).__name__,
                        )
                except Exception as exc:
                    logger.warning(
                        "Could not read preset YAML %s: %s (%s)",
                        preset_file,
                        exc,
                        type(exc).__name__,
                    )
            else:
                logger.warning(
                    "WAGGLE_PRESET_PATH points to missing file: %s", preset_file
                )

        # Gemma profiles from YAML
        gemma_cfg = yaml_base.get("gemma_profiles", {})

        # Parallel LLM dispatch from YAML
        par_cfg = yaml_base.get("llm_parallel", {})

        # Preset-aware defaults for the four fields that every preset
        # YAML is expected to (optionally) set. Precedence is already
        # enforced by os.environ.get() — env var wins, then preset, then
        # the hardcoded baseline.
        _preset_profile = str(preset_base.get("profile", yaml_base.get("profile", "home")))
        _preset_chat_model = str(preset_base.get("ollama_model", "phi4-mini"))
        _preset_embed_model = str(preset_base.get("embedding_model", "nomic-embed-text"))
        _preset_max_agents = str(preset_base.get("agents_max", "75"))

        settings = cls(
            profile=os.environ.get(
                "WAGGLE_PROFILE",
                _preset_profile).upper(),
            ollama_host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
            chroma_dir=os.environ.get("CHROMA_DIR", "./chroma_data"),
            db_path=os.environ.get("WAGGLE_DB_PATH", "./shared_memory.db"),
            chat_model=os.environ.get("WAGGLE_CHAT_MODEL", _preset_chat_model),
            learning_model=os.environ.get("WAGGLE_LEARNING_MODEL", "llama3.2:1b"),
            embed_model=os.environ.get("WAGGLE_EMBED_MODEL", _preset_embed_model),
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
            max_agents=int(os.environ.get("WAGGLE_MAX_AGENTS", _preset_max_agents)),
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
            _extras={
                **yaml_base,
                # Preset keys layer ON TOP of settings.yaml so
                # ``settings.get("resource_guard.max_memory_percent")``
                # surfaces the preset value when the user picked one.
                **preset_base,
                # Metadata the startup banner + runtime_diagnostics()
                # read to show which preset (if any) actually took
                # effect. Never leaks into the dotted .get() namespace
                # because the key starts with an underscore.
                "_preset_path": (
                    str(preset_loaded_from) if preset_loaded_from else ""
                ),
                "_preset_requested": str(resolved_preset or ""),
            },
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
            "project_root": str(_PROJECT_ROOT),
            "dotenv_path": str(_DEFAULT_DOTENV_PATH),
            "dotenv_present": _DEFAULT_DOTENV_PATH.is_file(),
            "settings_yaml_path": str(_SETTINGS_YAML_PATH),
            "settings_yaml_present": _SETTINGS_YAML_PATH.is_file(),
            "preset_requested": self._extras.get("_preset_requested", ""),
            "preset_path": self._extras.get("_preset_path", ""),
            "preset_loaded": bool(self._extras.get("_preset_path", "")),
            "api_key_set": bool(self.api_key),
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


def _load_dotenv(env_path: str | Path) -> None:
    """Best-effort .env loader (no python-dotenv dependency).

    Handles the common real-world dotenv subset:

    - Lines beginning with ``#`` are comments and skipped.
    - Blank lines are skipped.
    - Optional ``export KEY=val`` shell prefix is recognised and dropped.
    - UTF-8 BOM at the start of the file is stripped (operators who
      edit ``.env`` in Windows Notepad hit this otherwise, and the
      first line's key becomes invisible because it silently acquires
      a leading ``\\ufeff``).
    - Fully-matched surrounding single- OR double-quote pairs are
      removed from values. Mismatched quotes are left intact, so
      ``KEY="half`` stores the literal ``"half`` instead of silently
      losing the quote character.
    - Unquoted values support inline ``# comment`` trailers, but only
      when the ``#`` is preceded by whitespace. This lets operators
      annotate ``WAGGLE_API_KEY=abc123  # dev key`` without the
      comment landing in the actual key.
    - Values containing ``=`` are preserved (partition-on-first-``=``).
    - Shell env still wins over ``.env`` — we only set a variable if
      it is not already in ``os.environ``.

    NOT handled (deliberately — see F3-005 follow-ups): escape
    sequences (``\\n``, ``\\t``), shell variable expansion
    (``${OTHER}``), multi-line values.
    """
    path = Path(env_path)
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
        if text.startswith("\ufeff"):
            # Strip UTF-8 BOM so the first line's key does not silently
            # acquire a ``\ufeff`` prefix that makes the env var
            # invisible to downstream readers.
            text = text[1:]
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].lstrip()
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            # Reject obviously malformed keys (empty, whitespace, or
            # shell-special chars). A valid POSIX env var name is
            # ``[A-Za-z_][A-Za-z0-9_]*`` — we accept a slightly looser
            # version since WaggleSettings only reads keys we own.
            if not key or not all(c.isalnum() or c == "_" for c in key):
                continue
            value = value.strip()
            if (
                len(value) >= 2
                and value[0] == value[-1]
                and value[0] in ("'", '"')
            ):
                # Fully-matched quoted value — strip the pair, do NOT
                # touch inline ``#`` (it is part of the literal).
                value = value[1:-1]
            else:
                # Unquoted value — strip trailing ``# comment`` when
                # the ``#`` is preceded by whitespace (or is the very
                # first char, meaning the value is effectively empty).
                for i in range(len(value)):
                    if value[i] == "#" and (i == 0 or value[i - 1].isspace()):
                        value = value[:i].rstrip()
                        break
            if key not in os.environ:
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
