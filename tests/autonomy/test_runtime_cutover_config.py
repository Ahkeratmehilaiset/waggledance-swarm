"""
Tests for runtime cutover configuration.

Covers:
- WaggleSettings runtime_primary and compatibility_mode parsing
- Settings YAML merge (env vars > yaml > defaults)
- _parse_bool helper
- runtime_diagnostics() and mode properties
- Container wiring of runtime mode into AutonomyService
- Startup banner rendering
- Invalid runtime_primary validation
- Cutover readiness with settings-driven config
"""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.adapters.config.settings_loader import (
    WaggleSettings,
    _load_yaml_settings,
    _parse_bool,
)


# ── _parse_bool ─────────────────────────────────────────

class TestParseBool:
    @pytest.mark.parametrize("val,expected", [
        ("true", True), ("True", True), ("TRUE", True),
        ("1", True), ("yes", True), ("on", True),
        ("false", False), ("False", False), ("0", False),
        ("no", False), ("off", False), ("", False),
        ("random", False),
    ])
    def test_parse_bool(self, val, expected):
        assert _parse_bool(val) is expected


# ── YAML loading ────────────────────────────────────────

class TestYamlLoading:
    def test_load_missing_yaml_returns_empty(self):
        result = _load_yaml_settings("/nonexistent/path.yaml")
        assert result == {}

    def test_load_valid_yaml(self, tmp_path):
        yaml_file = tmp_path / "settings.yaml"
        yaml_file.write_text(
            "profile: factory\n"
            "runtime:\n"
            "  primary: hivemind\n"
            "  compatibility_mode: true\n",
            encoding="utf-8",
        )
        result = _load_yaml_settings(yaml_file)
        assert result["profile"] == "factory"
        assert result["runtime"]["primary"] == "hivemind"
        assert result["runtime"]["compatibility_mode"] is True

    def test_load_invalid_yaml_returns_empty(self, tmp_path):
        yaml_file = tmp_path / "bad.yaml"
        yaml_file.write_text(": : invalid yaml [[[", encoding="utf-8")
        result = _load_yaml_settings(yaml_file)
        # yaml.safe_load may parse this as a dict or raise; either way no crash
        assert isinstance(result, dict)


# ── WaggleSettings defaults ─────────────────────────────

class TestSettingsDefaults:
    def test_default_runtime_primary(self):
        s = WaggleSettings()
        assert s.runtime_primary == "waggledance"

    def test_default_compatibility_mode(self):
        s = WaggleSettings()
        assert s.compatibility_mode is False

    def test_is_autonomy_primary_default(self):
        s = WaggleSettings()
        assert s.is_autonomy_primary is True

    def test_is_shadow_mode_default(self):
        s = WaggleSettings()
        assert s.is_shadow_mode is False

    def test_runtime_diagnostics(self):
        s = WaggleSettings(profile="TEST", runtime_primary="waggledance",
                           compatibility_mode=False)
        diag = s.runtime_diagnostics()
        assert diag["runtime_primary"] == "waggledance"
        assert diag["compatibility_mode"] is False
        assert diag["is_autonomy_primary"] is True
        assert diag["profile"] == "TEST"


# ── Settings from env vars ──────────────────────────────

class TestSettingsFromEnv:
    def test_env_overrides_runtime_primary(self, tmp_path):
        yaml_file = tmp_path / "settings.yaml"
        yaml_file.write_text(
            "runtime:\n  primary: waggledance\n",
            encoding="utf-8",
        )
        env = {
            "WAGGLE_RUNTIME_PRIMARY": "hivemind",
        }
        with patch.dict(os.environ, env, clear=False):
            s = WaggleSettings.from_env(
                env_path=str(tmp_path / ".env"),  # nonexistent, fine
                yaml_path=yaml_file,
            )
        assert s.runtime_primary == "hivemind"

    def test_env_overrides_compat_mode(self, tmp_path):
        yaml_file = tmp_path / "settings.yaml"
        yaml_file.write_text(
            "runtime:\n  compatibility_mode: false\n",
            encoding="utf-8",
        )
        env = {
            "WAGGLE_COMPAT_MODE": "true",
        }
        with patch.dict(os.environ, env, clear=False):
            s = WaggleSettings.from_env(
                env_path=str(tmp_path / ".env"),
                yaml_path=yaml_file,
            )
        assert s.compatibility_mode is True

    def test_yaml_fallback_for_runtime_primary(self, tmp_path):
        yaml_file = tmp_path / "settings.yaml"
        yaml_file.write_text(
            "runtime:\n  primary: shadow\n",
            encoding="utf-8",
        )
        # Remove env var if set
        env_clean = {k: v for k, v in os.environ.items()
                     if k != "WAGGLE_RUNTIME_PRIMARY"}
        with patch.dict(os.environ, env_clean, clear=True):
            s = WaggleSettings.from_env(
                env_path=str(tmp_path / ".env"),
                yaml_path=yaml_file,
            )
        assert s.runtime_primary == "shadow"

    def test_yaml_fallback_for_compat_mode(self, tmp_path):
        yaml_file = tmp_path / "settings.yaml"
        yaml_file.write_text(
            "runtime:\n  compatibility_mode: true\n",
            encoding="utf-8",
        )
        env_clean = {k: v for k, v in os.environ.items()
                     if k != "WAGGLE_COMPAT_MODE"}
        with patch.dict(os.environ, env_clean, clear=True):
            s = WaggleSettings.from_env(
                env_path=str(tmp_path / ".env"),
                yaml_path=yaml_file,
            )
        assert s.compatibility_mode is True

    def test_default_when_no_yaml_no_env(self, tmp_path):
        """When no YAML and no env var, defaults apply."""
        env_clean = {k: v for k, v in os.environ.items()
                     if not k.startswith("WAGGLE_")}
        with patch.dict(os.environ, env_clean, clear=True):
            s = WaggleSettings.from_env(
                env_path=str(tmp_path / ".env"),
                yaml_path=tmp_path / "nonexistent.yaml",
            )
        assert s.runtime_primary == "waggledance"
        assert s.compatibility_mode is False


# ── Invalid runtime_primary validation ──────────────────

class TestSettingsValidation:
    def test_invalid_runtime_primary_falls_back(self, tmp_path):
        yaml_file = tmp_path / "settings.yaml"
        yaml_file.write_text(
            "runtime:\n  primary: bogus_mode\n",
            encoding="utf-8",
        )
        env_clean = {k: v for k, v in os.environ.items()
                     if k != "WAGGLE_RUNTIME_PRIMARY"}
        with patch.dict(os.environ, env_clean, clear=True):
            s = WaggleSettings.from_env(
                env_path=str(tmp_path / ".env"),
                yaml_path=yaml_file,
            )
        assert s.runtime_primary == "waggledance"  # fallback

    def test_valid_primaries_accepted(self, tmp_path):
        for primary in ("waggledance", "hivemind", "shadow"):
            yaml_file = tmp_path / "settings.yaml"
            yaml_file.write_text(
                f"runtime:\n  primary: {primary}\n",
                encoding="utf-8",
            )
            env_clean = {k: v for k, v in os.environ.items()
                         if k != "WAGGLE_RUNTIME_PRIMARY"}
            with patch.dict(os.environ, env_clean, clear=True):
                s = WaggleSettings.from_env(
                    env_path=str(tmp_path / ".env"),
                    yaml_path=yaml_file,
                )
            assert s.runtime_primary == primary


# ── Mode properties ─────────────────────────────────────

class TestModeProperties:
    def test_waggledance_primary(self):
        s = WaggleSettings(runtime_primary="waggledance", compatibility_mode=False)
        assert s.is_autonomy_primary is True
        assert s.is_shadow_mode is False

    def test_waggledance_compat(self):
        s = WaggleSettings(runtime_primary="waggledance", compatibility_mode=True)
        assert s.is_autonomy_primary is False
        assert s.is_shadow_mode is False

    def test_hivemind_primary(self):
        s = WaggleSettings(runtime_primary="hivemind", compatibility_mode=False)
        assert s.is_autonomy_primary is False
        assert s.is_shadow_mode is False

    def test_shadow_mode(self):
        s = WaggleSettings(runtime_primary="shadow", compatibility_mode=False)
        assert s.is_autonomy_primary is False
        assert s.is_shadow_mode is True


# ── Container wiring ────────────────────────────────────

class TestContainerWiring:
    def test_container_passes_runtime_primary(self):
        from waggledance.bootstrap.container import Container
        s = WaggleSettings(runtime_primary="hivemind", compatibility_mode=True,
                           profile="TEST")
        c = Container(settings=s, stub=True)
        svc = c.autonomy_service
        assert svc._lifecycle.primary.value == "hivemind"
        assert svc._lifecycle.compatibility_mode is True
        assert svc._lifecycle.profile == "TEST"

    def test_container_compat_layer_inherits_mode(self):
        from waggledance.bootstrap.container import Container
        s = WaggleSettings(runtime_primary="waggledance", compatibility_mode=True,
                           profile="COTTAGE")
        c = Container(settings=s, stub=True)
        svc = c.autonomy_service
        assert svc._compatibility.compatibility_mode is True

    def test_container_autonomy_mode(self):
        from waggledance.bootstrap.container import Container
        s = WaggleSettings(runtime_primary="waggledance", compatibility_mode=False,
                           profile="FACTORY")
        c = Container(settings=s, stub=True)
        svc = c.autonomy_service
        assert svc._lifecycle.is_autonomy_primary is True
        assert svc._compatibility.compatibility_mode is False

    def test_container_shadow_mode(self):
        from waggledance.bootstrap.container import Container
        s = WaggleSettings(runtime_primary="shadow", compatibility_mode=False,
                           profile="HOME")
        c = Container(settings=s, stub=True)
        svc = c.autonomy_service
        assert svc._lifecycle.primary.value == "shadow"


# ── Cutover with settings-driven config ─────────────────

class TestCutoverWithSettings:
    def test_cutover_passes_when_waggledance_primary(self):
        from waggledance.bootstrap.container import Container
        s = WaggleSettings(runtime_primary="waggledance", compatibility_mode=False,
                           profile="COTTAGE")
        c = Container(settings=s, stub=True)
        svc = c.autonomy_service
        svc.start()
        result = svc.validate_cutover()
        assert result["runtime_primary"] is True
        assert result["compatibility_off"] is True
        svc.stop()

    def test_cutover_fails_when_compat_on(self):
        from waggledance.bootstrap.container import Container
        s = WaggleSettings(runtime_primary="waggledance", compatibility_mode=True,
                           profile="COTTAGE")
        c = Container(settings=s, stub=True)
        svc = c.autonomy_service
        svc.start()
        result = svc.validate_cutover()
        assert result["compatibility_off"] is False
        assert result["all_pass"] is False
        svc.stop()

    def test_cutover_fails_when_hivemind_primary(self):
        from waggledance.bootstrap.container import Container
        s = WaggleSettings(runtime_primary="hivemind", compatibility_mode=False,
                           profile="COTTAGE")
        c = Container(settings=s, stub=True)
        svc = c.autonomy_service
        svc.start()
        result = svc.validate_cutover()
        assert result["runtime_primary"] is False
        assert result["all_pass"] is False
        svc.stop()

    def test_new_runtime_is_primary_when_configured(self):
        """Proves the new runtime becomes the selected primary when configured."""
        from waggledance.bootstrap.container import Container
        s = WaggleSettings(runtime_primary="waggledance", compatibility_mode=False)
        c = Container(settings=s, stub=True)
        svc = c.autonomy_service
        svc.start()

        # Verify lifecycle says autonomy is primary
        assert svc._lifecycle.is_autonomy_primary is True
        # Verify compatibility layer routes to autonomy, not legacy
        assert svc._compatibility.compatibility_mode is False
        # Handle a query — should go through autonomy path
        result = svc.handle_query("test query")
        assert result.get("source") == "autonomy"
        assert svc._compatibility.stats()["runtime_calls"] == 1
        assert svc._compatibility.stats()["legacy_calls"] == 0
        svc.stop()


# ── Startup banner ──────────────────────────────────────

class TestStartupBanner:
    def test_banner_shows_runtime_mode(self, capsys):
        from waggledance.adapters.cli.start_runtime import _print_banner
        s = WaggleSettings(runtime_primary="waggledance",
                           compatibility_mode=False, profile="FACTORY")
        _print_banner(stub=False, host="0.0.0.0", port=8000,
                      log_level="warning", settings=s)
        out = capsys.readouterr().out
        assert "waggledance" in out
        assert "FACTORY" in out
        assert "OFF" in out  # compat OFF

    def test_banner_shows_compat_on(self, capsys):
        from waggledance.adapters.cli.start_runtime import _print_banner
        s = WaggleSettings(runtime_primary="hivemind",
                           compatibility_mode=True, profile="COTTAGE")
        _print_banner(stub=False, host="0.0.0.0", port=8000,
                      log_level="info", settings=s)
        out = capsys.readouterr().out
        assert "hivemind" in out
        assert "ON" in out  # compat ON

    def test_banner_stub_mode(self, capsys):
        from waggledance.adapters.cli.start_runtime import _print_banner
        _print_banner(stub=True, host="0.0.0.0", port=8000,
                      log_level="debug", settings=None)
        out = capsys.readouterr().out
        assert "STUB" in out

    def test_banner_without_settings(self, capsys):
        from waggledance.adapters.cli.start_runtime import _print_banner
        _print_banner(stub=False, host="0.0.0.0", port=8000,
                      log_level="warning", settings=None)
        out = capsys.readouterr().out
        assert "waggledance" in out  # default

    def test_banner_shows_localhost_not_0000(self, capsys):
        """Banner must show http://localhost:PORT, not http://0.0.0.0:PORT."""
        from waggledance.adapters.cli.start_runtime import _print_banner
        _print_banner(stub=False, host="0.0.0.0", port=8000,
                      log_level="warning", settings=None)
        out = capsys.readouterr().out
        assert "http://localhost:8000" in out
        assert "http://0.0.0.0" not in out

    def test_banner_shows_bind_address(self, capsys):
        """Banner should show bind address separately from browser URL."""
        from waggledance.adapters.cli.start_runtime import _print_banner
        _print_banner(stub=False, host="0.0.0.0", port=9000,
                      log_level="warning", settings=None)
        out = capsys.readouterr().out
        assert "0.0.0.0:9000" in out        # bind address shown
        assert "http://localhost:9000" in out  # local URL shown

    def test_banner_custom_host_uses_localhost(self, capsys):
        """When host is 127.0.0.1, local URL still shows localhost."""
        from waggledance.adapters.cli.start_runtime import _print_banner
        _print_banner(stub=False, host="127.0.0.1", port=8000,
                      log_level="warning", settings=None)
        out = capsys.readouterr().out
        assert "http://localhost:8000" in out
        # No LAN line for loopback-only bind
        assert "LAN URL" not in out

    def test_banner_custom_port(self, capsys):
        """Custom port appears in both bind and local URL."""
        from waggledance.adapters.cli.start_runtime import _print_banner
        _print_banner(stub=False, host="0.0.0.0", port=3000,
                      log_level="warning", settings=None)
        out = capsys.readouterr().out
        assert "http://localhost:3000" in out
        assert "0.0.0.0:3000" in out


# ── dotted-key extras access ────────────────────────────

class TestSettingsExtras:
    def test_get_dotted_key_from_yaml(self, tmp_path):
        yaml_file = tmp_path / "settings.yaml"
        yaml_file.write_text(
            "profile: cottage\n"
            "runtime:\n  primary: waggledance\n"
            "swarm:\n  top_k: 5\n",
            encoding="utf-8",
        )
        env_clean = {k: v for k, v in os.environ.items()
                     if not k.startswith("WAGGLE_")}
        with patch.dict(os.environ, env_clean, clear=True):
            s = WaggleSettings.from_env(
                env_path=str(tmp_path / ".env"),
                yaml_path=yaml_file,
            )
        assert s.get("swarm.top_k") == 5

    def test_get_direct_attribute(self):
        s = WaggleSettings(runtime_primary="shadow")
        assert s.get("runtime_primary") == "shadow"

    def test_get_default(self):
        s = WaggleSettings()
        assert s.get("nonexistent_key", 42) == 42
