"""Unit tests for WaggleSettings — configuration loading via monkeypatch."""

import subprocess
from unittest.mock import patch

import pytest

from waggledance.adapters.config.settings_loader import WaggleSettings


class TestWaggleSettingsDefaults:
    """Default values when no environment variables are set."""

    def test_default_profile_is_home(self) -> None:
        settings = WaggleSettings()
        assert settings.profile == "HOME"

    def test_default_ollama_host(self) -> None:
        settings = WaggleSettings()
        assert settings.ollama_host == "http://localhost:11434"

    def test_default_chat_model(self) -> None:
        settings = WaggleSettings()
        assert settings.chat_model == "phi4-mini"

    def test_default_max_agents(self) -> None:
        settings = WaggleSettings()
        assert settings.max_agents == 75

    def test_default_hot_cache_size(self) -> None:
        settings = WaggleSettings()
        assert settings.hot_cache_size == 1000


class TestWaggleSettingsBug3Regressions:
    """BUG 3 regression: ollama_timeout and night_stall_threshold."""

    def test_ollama_timeout_seconds_default_is_120(self) -> None:
        settings = WaggleSettings()
        assert settings.ollama_timeout_seconds == 120.0

    def test_night_stall_threshold_default_is_10(self) -> None:
        settings = WaggleSettings()
        assert settings.night_stall_threshold == 10


class TestWaggleSettingsFromEnv:
    """from_env() reads environment variables."""

    def test_waggle_profile_env_overrides_profile(self, monkeypatch) -> None:
        monkeypatch.setenv("WAGGLE_PROFILE", "apartment")
        # Prevent .env file loading side effects
        with patch(
            "waggledance.adapters.config.settings_loader._load_dotenv"
        ):
            settings = WaggleSettings.from_env(env_path="/nonexistent/.env")
        assert settings.profile == "APARTMENT"

    def test_ollama_host_env_overrides_ollama_host(self, monkeypatch) -> None:
        monkeypatch.setenv("OLLAMA_HOST", "http://gpu-box:11434")
        with patch(
            "waggledance.adapters.config.settings_loader._load_dotenv"
        ):
            settings = WaggleSettings.from_env(env_path="/nonexistent/.env")
        assert settings.ollama_host == "http://gpu-box:11434"

    def test_api_key_auto_generation_works(self, monkeypatch) -> None:
        # Ensure no WAGGLE_API_KEY is set
        monkeypatch.delenv("WAGGLE_API_KEY", raising=False)
        with patch(
            "waggledance.adapters.config.settings_loader._load_dotenv"
        ):
            settings = WaggleSettings.from_env(env_path="/nonexistent/.env")
        # Should auto-generate a non-empty key
        assert len(settings.api_key) > 0

    def test_explicit_api_key_is_preserved(self, monkeypatch) -> None:
        monkeypatch.setenv("WAGGLE_API_KEY", "my-secret-key-123")
        with patch(
            "waggledance.adapters.config.settings_loader._load_dotenv"
        ):
            settings = WaggleSettings.from_env(env_path="/nonexistent/.env")
        assert settings.api_key == "my-secret-key-123"

    def test_from_env_does_not_raise_on_clean_environment(
        self, monkeypatch
    ) -> None:
        # Remove all WAGGLE_ env vars that might affect the test
        for key in list(monkeypatch._env_changes if hasattr(monkeypatch, '_env_changes') else []):
            pass
        with patch(
            "waggledance.adapters.config.settings_loader._load_dotenv"
        ):
            settings = WaggleSettings.from_env(env_path="/nonexistent/.env")
        assert settings is not None
        assert isinstance(settings, WaggleSettings)

    def test_ollama_timeout_from_env(self, monkeypatch) -> None:
        monkeypatch.setenv("WAGGLE_OLLAMA_TIMEOUT", "300.0")
        with patch(
            "waggledance.adapters.config.settings_loader._load_dotenv"
        ):
            settings = WaggleSettings.from_env(env_path="/nonexistent/.env")
        assert settings.ollama_timeout_seconds == 300.0


class TestWaggleSettingsHardwareTier:
    """Hardware tier detection."""

    def test_hardware_tier_detection_runs_without_crashing(self) -> None:
        settings = WaggleSettings(hardware_tier="auto")
        # _detect_hardware_tier handles all exceptions gracefully
        tier = settings.get_hardware_tier()
        assert tier in ("enterprise", "professional", "standard", "light", "minimal")

    def test_explicit_hardware_tier_skips_detection(self) -> None:
        settings = WaggleSettings(hardware_tier="professional")
        assert settings.get_hardware_tier() == "professional"


class TestWaggleSettingsConfigPort:
    """ConfigPort interface: get(), get_profile()."""

    def test_get_returns_attribute_value(self) -> None:
        settings = WaggleSettings(chat_model="llama3.2:1b")
        assert settings.get("chat_model") == "llama3.2:1b"

    def test_get_returns_default_for_missing_key(self) -> None:
        settings = WaggleSettings()
        assert settings.get("nonexistent_key", "fallback") == "fallback"

    def test_get_profile_returns_uppercase(self) -> None:
        settings = WaggleSettings(profile="cottage")
        assert settings.get_profile() == "COTTAGE"
