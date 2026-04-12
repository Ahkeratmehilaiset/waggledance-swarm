"""Tests for F1-006: hardware-preset plumbing.

``start_waggledance.py --preset=<name>`` used to load the preset YAML
only to print a banner line, then throw the dict away — the runtime's
``WaggleSettings.from_env()`` never saw it. These tests lock in the
post-gate fix (commit after 94e85ae on ``feat/v357-feed-runtime-wiring``):

- ``WAGGLE_PRESET_PATH`` env var is read by ``from_env`` and layered
  above ``settings.yaml`` but below explicit ``WAGGLE_*`` env vars.
- The ``preset_path`` keyword argument works the same way for tests.
- ``profile``, ``chat_model``, ``embed_model`` and ``max_agents`` all
  honour the preset.
- Explicit env vars still win (preset is only a default hint).
- Missing / unreadable preset files degrade gracefully with a
  log warning; the runtime never crashes.
- ``runtime_diagnostics()`` reports which preset (if any) took effect.
- ``start_waggledance.py --preset=X`` sets the env var before calling
  the child runtime.
- No api_key leakage via any of the new diagnostic fields.
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path
from unittest import mock

import pytest

from waggledance.adapters.config.settings_loader import WaggleSettings


_WAGGLE_KEYS_TO_CLEAR = (
    "WAGGLE_PRESET_PATH",
    "WAGGLE_PROFILE",
    "WAGGLE_CHAT_MODEL",
    "WAGGLE_EMBED_MODEL",
    "WAGGLE_MAX_AGENTS",
    "WAGGLE_API_KEY",
    "WAGGLE_RUNTIME_PRIMARY",
    "WAGGLE_COMPAT_MODE",
    "OLLAMA_HOST",
    "CHROMA_DIR",
    "WAGGLE_DB_PATH",
    "WAGGLE_LEARNING_MODEL",
)


@pytest.fixture
def clean_waggle_env(monkeypatch, tmp_path):
    """Strip every WAGGLE_* env var our loader reads so tests get a
    deterministic baseline.  Also redirect the module-level default
    dotenv path to a non-existent file so the real .env on disk cannot
    re-inject values via ``_load_dotenv``."""
    for key in _WAGGLE_KEYS_TO_CLEAR:
        monkeypatch.delenv(key, raising=False)
    # Redirect default .env lookup to a path that doesn't exist so
    # _load_dotenv inside from_env() is a no-op.
    monkeypatch.setattr(
        "waggledance.adapters.config.settings_loader._DEFAULT_DOTENV_PATH",
        tmp_path / ".env",
    )
    # Fix an api_key so the sentinel-leak assertion has something to
    # look for.
    monkeypatch.setenv("WAGGLE_API_KEY", "f1006-preset-sentinel-DO-NOT-LEAK")
    return monkeypatch


def _write_preset(tmp_path: Path, name: str, data: dict) -> Path:
    """Materialise a preset YAML file under a tmp dir."""
    p = tmp_path / f"{name}.yaml"
    import yaml as _yaml
    p.write_text(_yaml.safe_dump(data), encoding="utf-8")
    return p


@pytest.fixture
def empty_settings_yaml(tmp_path: Path) -> Path:
    """Materialise an empty-but-valid settings.yaml so the loader's
    default settings.yaml on disk cannot leak into these tests."""
    p = tmp_path / "settings.yaml"
    p.write_text("{}\n", encoding="utf-8")
    return p



# -------------------------------------------------------------------- #
#  Direct from_env(preset_path=...) plumbing                            #
# -------------------------------------------------------------------- #


def test_preset_profile_is_applied(clean_waggle_env, tmp_path, empty_settings_yaml):
    preset = _write_preset(
        tmp_path,
        "rpi",
        {"profile": "gadget", "ollama_model": "phi4-mini", "agents_max": 5},
    )
    settings = WaggleSettings.from_env(
        yaml_path=empty_settings_yaml, preset_path=preset
    )
    # ``from_env`` uppercases profiles.
    assert settings.profile == "GADGET"


def test_preset_chat_model_is_applied(clean_waggle_env, tmp_path, empty_settings_yaml):
    preset = _write_preset(
        tmp_path,
        "factory",
        {"profile": "factory", "ollama_model": "llama3.2:3b"},
    )
    settings = WaggleSettings.from_env(
        yaml_path=empty_settings_yaml, preset_path=preset
    )
    assert settings.chat_model == "llama3.2:3b"


def test_preset_embed_model_is_applied(clean_waggle_env, tmp_path, empty_settings_yaml):
    preset = _write_preset(
        tmp_path,
        "cottage",
        {"profile": "cottage", "embedding_model": "nomic-embed-text"},
    )
    settings = WaggleSettings.from_env(
        yaml_path=empty_settings_yaml, preset_path=preset
    )
    assert settings.embed_model == "nomic-embed-text"


def test_preset_max_agents_is_applied(clean_waggle_env, tmp_path, empty_settings_yaml):
    preset = _write_preset(
        tmp_path,
        "rpi",
        {"profile": "gadget", "agents_max": 5},
    )
    settings = WaggleSettings.from_env(
        yaml_path=empty_settings_yaml, preset_path=preset
    )
    assert settings.max_agents == 5


# -------------------------------------------------------------------- #
#  WAGGLE_PRESET_PATH env var plumbing                                  #
# -------------------------------------------------------------------- #


def test_preset_env_var_is_read(clean_waggle_env, tmp_path, empty_settings_yaml):
    preset = _write_preset(
        tmp_path,
        "factory",
        {
            "profile": "factory",
            "ollama_model": "llama3.2:3b",
            "embedding_model": "nomic-embed-text",
            "agents_max": 75,
        },
    )
    clean_waggle_env.setenv("WAGGLE_PRESET_PATH", str(preset))
    settings = WaggleSettings.from_env(yaml_path=empty_settings_yaml)
    assert settings.profile == "FACTORY"
    assert settings.chat_model == "llama3.2:3b"
    assert settings.max_agents == 75


def test_preset_keyword_overrides_env_var(
    clean_waggle_env, tmp_path, empty_settings_yaml
):
    """Explicit ``preset_path=`` kwarg beats ``WAGGLE_PRESET_PATH``."""
    factory = _write_preset(
        tmp_path, "factory", {"profile": "factory", "agents_max": 75}
    )
    rpi = _write_preset(tmp_path, "rpi", {"profile": "gadget", "agents_max": 5})
    clean_waggle_env.setenv("WAGGLE_PRESET_PATH", str(factory))

    settings = WaggleSettings.from_env(
        yaml_path=empty_settings_yaml, preset_path=rpi
    )
    assert settings.profile == "GADGET"
    assert settings.max_agents == 5


# -------------------------------------------------------------------- #
#  Precedence: explicit WAGGLE_* env vars still win over preset         #
# -------------------------------------------------------------------- #


def test_env_var_beats_preset_for_profile(
    clean_waggle_env, tmp_path, empty_settings_yaml
):
    preset = _write_preset(
        tmp_path, "rpi", {"profile": "gadget", "agents_max": 5}
    )
    clean_waggle_env.setenv("WAGGLE_PROFILE", "factory")

    settings = WaggleSettings.from_env(
        yaml_path=empty_settings_yaml, preset_path=preset
    )
    assert settings.profile == "FACTORY"


def test_env_var_beats_preset_for_max_agents(
    clean_waggle_env, tmp_path, empty_settings_yaml
):
    preset = _write_preset(
        tmp_path, "rpi", {"profile": "gadget", "agents_max": 5}
    )
    clean_waggle_env.setenv("WAGGLE_MAX_AGENTS", "42")

    settings = WaggleSettings.from_env(
        yaml_path=empty_settings_yaml, preset_path=preset
    )
    assert settings.max_agents == 42


def test_env_var_beats_preset_for_chat_model(
    clean_waggle_env, tmp_path, empty_settings_yaml
):
    preset = _write_preset(
        tmp_path, "factory", {"ollama_model": "llama3.2:3b"}
    )
    clean_waggle_env.setenv("WAGGLE_CHAT_MODEL", "phi4-mini")

    settings = WaggleSettings.from_env(
        yaml_path=empty_settings_yaml, preset_path=preset
    )
    assert settings.chat_model == "phi4-mini"


# -------------------------------------------------------------------- #
#  Extras dotted-key access for non-mapped preset keys                  #
# -------------------------------------------------------------------- #


def test_preset_extras_are_dotted_get_accessible(
    clean_waggle_env, tmp_path, empty_settings_yaml
):
    preset = _write_preset(
        tmp_path,
        "factory",
        {
            "profile": "factory",
            "resource_guard": {
                "max_memory_percent": 80,
                "critical_memory_percent": 90,
            },
            "log_format": "json",
        },
    )
    settings = WaggleSettings.from_env(
        yaml_path=empty_settings_yaml, preset_path=preset
    )
    assert settings.get("resource_guard.max_memory_percent") == 80
    assert settings.get("resource_guard.critical_memory_percent") == 90
    assert settings.get("log_format") == "json"


# -------------------------------------------------------------------- #
#  Graceful degradation                                                 #
# -------------------------------------------------------------------- #


def test_missing_preset_file_does_not_raise(
    clean_waggle_env, tmp_path, empty_settings_yaml
):
    """Operator typo or deleted preset file must not crash startup."""
    nonexistent = tmp_path / "nope.yaml"
    # Must not raise.
    settings = WaggleSettings.from_env(
        yaml_path=empty_settings_yaml, preset_path=nonexistent
    )
    # Falls back to hardcoded defaults.
    assert settings.profile == "HOME"
    assert settings.chat_model == "phi4-mini"
    assert settings.max_agents == 75


def test_malformed_preset_yaml_falls_back(
    clean_waggle_env, tmp_path, empty_settings_yaml
):
    broken = tmp_path / "broken.yaml"
    broken.write_text("this: is: not valid: yaml: [", encoding="utf-8")
    # Must not raise.
    settings = WaggleSettings.from_env(
        yaml_path=empty_settings_yaml, preset_path=broken
    )
    assert settings.profile == "HOME"


def test_preset_non_dict_falls_back(
    clean_waggle_env, tmp_path, empty_settings_yaml
):
    """A YAML file that parses to a non-dict (e.g. a list) is ignored."""
    p = tmp_path / "bad.yaml"
    p.write_text("- just\n- a\n- list\n", encoding="utf-8")
    settings = WaggleSettings.from_env(
        yaml_path=empty_settings_yaml, preset_path=p
    )
    assert settings.profile == "HOME"


def test_no_preset_keeps_existing_behaviour(
    clean_waggle_env, tmp_path, empty_settings_yaml
):
    """No preset kwarg and no env var → baseline defaults, unchanged."""
    settings = WaggleSettings.from_env(yaml_path=empty_settings_yaml)
    assert settings.profile == "HOME"
    assert settings.chat_model == "phi4-mini"
    assert settings.embed_model == "nomic-embed-text"
    assert settings.max_agents == 75


# -------------------------------------------------------------------- #
#  Diagnostics                                                          #
# -------------------------------------------------------------------- #


def test_runtime_diagnostics_reports_preset_metadata(
    clean_waggle_env, tmp_path, empty_settings_yaml
):
    preset = _write_preset(
        tmp_path, "rpi", {"profile": "gadget", "agents_max": 5}
    )
    settings = WaggleSettings.from_env(
        yaml_path=empty_settings_yaml, preset_path=preset
    )
    diag = settings.runtime_diagnostics()
    assert diag["preset_loaded"] is True
    assert diag["preset_path"] == str(preset)
    assert diag["preset_requested"] == str(preset)


def test_runtime_diagnostics_preset_unset_by_default(
    clean_waggle_env, tmp_path, empty_settings_yaml
):
    settings = WaggleSettings.from_env(yaml_path=empty_settings_yaml)
    diag = settings.runtime_diagnostics()
    assert diag["preset_loaded"] is False
    assert diag["preset_path"] == ""
    assert diag["preset_requested"] == ""


def test_runtime_diagnostics_reports_request_even_when_missing(
    clean_waggle_env, tmp_path, empty_settings_yaml
):
    """When the operator asked for a preset but the file is missing, the
    diagnostics must still say so — otherwise the misconfiguration is
    invisible."""
    bogus = tmp_path / "ghost.yaml"
    settings = WaggleSettings.from_env(
        yaml_path=empty_settings_yaml, preset_path=bogus
    )
    diag = settings.runtime_diagnostics()
    assert diag["preset_loaded"] is False
    assert diag["preset_path"] == ""
    assert diag["preset_requested"] == str(bogus)


def test_runtime_diagnostics_never_leaks_api_key(
    clean_waggle_env, tmp_path, empty_settings_yaml
):
    preset = _write_preset(
        tmp_path, "rpi", {"profile": "gadget", "agents_max": 5}
    )
    settings = WaggleSettings.from_env(
        yaml_path=empty_settings_yaml, preset_path=preset
    )
    diag = settings.runtime_diagnostics()
    # The sentinel env var value set in clean_waggle_env MUST NOT appear
    # in any stringified diagnostic value.
    for value in diag.values():
        assert "f1006-preset-sentinel" not in str(value)
    assert diag["api_key_set"] is True


# -------------------------------------------------------------------- #
#  start_waggledance.py end-to-end                                      #
# -------------------------------------------------------------------- #


def test_start_waggledance_sets_preset_env_var(monkeypatch, tmp_path, capsys):
    """Running ``start_waggledance.py --preset=raspberry-pi-iot``
    must set ``WAGGLE_PRESET_PATH`` BEFORE handing off to the child
    runtime, and must print the expected banner lines."""
    # Guard: clear any pre-existing preset path so the assertion is
    # meaningful.
    monkeypatch.delenv("WAGGLE_PRESET_PATH", raising=False)

    # Stub out the child runtime so the test doesn't spin up uvicorn.
    captured = {}

    def _fake_runtime_main(argv=None):
        captured["argv"] = argv
        captured["preset_env"] = os.environ.get("WAGGLE_PRESET_PATH", "")

    monkeypatch.setattr(sys, "argv", ["start_waggledance.py", "--preset=raspberry-pi-iot"])
    monkeypatch.setattr(
        "waggledance.adapters.cli.start_runtime.main", _fake_runtime_main
    )

    runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "start_waggledance.py"),
        run_name="__main__",
    )

    assert "preset_env" in captured, "runtime_main was never called"
    assert captured["preset_env"].endswith("raspberry-pi-iot.yaml")
    assert Path(captured["preset_env"]).is_file()

    out = capsys.readouterr().out
    assert "raspberry-pi-iot" in out
    assert "Profile:" in out
    assert "CLI args:" in out


def test_start_waggledance_without_preset_leaves_env_var_unset(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("WAGGLE_PRESET_PATH", raising=False)

    captured = {}

    def _fake_runtime_main(argv=None):
        captured["preset_env"] = os.environ.get("WAGGLE_PRESET_PATH", "")

    monkeypatch.setattr(sys, "argv", ["start_waggledance.py"])
    monkeypatch.setattr(
        "waggledance.adapters.cli.start_runtime.main", _fake_runtime_main
    )

    runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "start_waggledance.py"),
        run_name="__main__",
    )

    assert captured["preset_env"] == ""


# -------------------------------------------------------------------- #
#  Real shipped presets on disk: parametrised round-trip                #
# -------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("preset_name", "expected_profile", "expected_chat_model", "expected_agents"),
    [
        ("raspberry-pi-iot", "GADGET", "phi4-mini", 5),
        ("cottage-full", "COTTAGE", "llama3.2:3b", 30),
        ("factory-production", "FACTORY", "llama3.2:3b", 75),
    ],
)
def test_all_shipped_presets_load_cleanly(
    clean_waggle_env,
    empty_settings_yaml,
    preset_name,
    expected_profile,
    expected_chat_model,
    expected_agents,
):
    repo_root = Path(__file__).resolve().parents[1]
    preset = repo_root / "configs" / "presets" / f"{preset_name}.yaml"
    assert preset.is_file(), f"shipped preset missing: {preset}"

    settings = WaggleSettings.from_env(
        yaml_path=empty_settings_yaml, preset_path=preset
    )
    assert settings.profile == expected_profile
    assert settings.chat_model == expected_chat_model
    assert settings.max_agents == expected_agents
