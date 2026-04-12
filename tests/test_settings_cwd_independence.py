"""Phase 1 release-polish tests: settings loader must be cwd-independent.

Before these changes, ``_load_dotenv(".env")`` and
``_SETTINGS_YAML_PATH = Path("configs/settings.yaml")`` were resolved against
the shell's current working directory. Running ``python start_waggledance.py``
from anywhere other than the project root silently ignored both files.

These tests lock in the new project-root-anchored behavior.
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest


def test_project_root_contains_expected_markers():
    from waggledance.adapters.config.settings_loader import _PROJECT_ROOT

    # A well-formed project root has at least one of these.
    markers = [
        _PROJECT_ROOT / "requirements.txt",
        _PROJECT_ROOT / "pyproject.toml",
        _PROJECT_ROOT / "configs",
    ]
    assert any(m.exists() for m in markers), (
        f"_PROJECT_ROOT={_PROJECT_ROOT} has no recognizable project marker"
    )


def test_default_settings_yaml_is_absolute_and_under_project_root():
    from waggledance.adapters.config.settings_loader import (
        _PROJECT_ROOT,
        _SETTINGS_YAML_PATH,
    )

    assert _SETTINGS_YAML_PATH.is_absolute()
    assert str(_SETTINGS_YAML_PATH).startswith(str(_PROJECT_ROOT))
    assert _SETTINGS_YAML_PATH.name == "settings.yaml"
    assert _SETTINGS_YAML_PATH.parent.name == "configs"


def test_default_dotenv_is_absolute_and_under_project_root():
    from waggledance.adapters.config.settings_loader import (
        _PROJECT_ROOT,
        _DEFAULT_DOTENV_PATH,
    )

    assert _DEFAULT_DOTENV_PATH.is_absolute()
    assert _DEFAULT_DOTENV_PATH.parent == _PROJECT_ROOT
    assert _DEFAULT_DOTENV_PATH.name == ".env"


def test_from_env_loads_yaml_when_invoked_from_unrelated_cwd(tmp_path, monkeypatch):
    """Running the loader from a temp cwd must still pick up configs/settings.yaml."""
    from waggledance.adapters.config.settings_loader import (
        WaggleSettings,
        _SETTINGS_YAML_PATH,
    )

    if not _SETTINGS_YAML_PATH.is_file():
        pytest.skip("real configs/settings.yaml not present in this checkout")

    monkeypatch.chdir(tmp_path)
    # Pass a non-existent .env path so we isolate the yaml-loading behavior.
    s = WaggleSettings.from_env(env_path=tmp_path / ".env-does-not-exist")
    # A real settings.yaml has at least a profile key. Prove _extras is populated.
    assert isinstance(s._extras, dict)
    assert s._extras, "YAML merge was silently empty despite file existing"


def test_runtime_diagnostics_exposes_project_paths():
    from waggledance.adapters.config.settings_loader import WaggleSettings

    with patch.dict(os.environ, {"WAGGLE_API_KEY": "test-sentinel-key"}, clear=False):
        s = WaggleSettings.from_env()

    diag = s.runtime_diagnostics()
    for key in (
        "project_root",
        "dotenv_path",
        "dotenv_present",
        "settings_yaml_path",
        "settings_yaml_present",
        "api_key_set",
    ):
        assert key in diag, f"runtime_diagnostics missing key: {key}"
    assert Path(diag["project_root"]).is_absolute()
    assert diag["api_key_set"] is True
    # Sanity: the returned paths must resolve to actual Path-like strings.
    assert diag["settings_yaml_path"].endswith("settings.yaml")
    assert diag["dotenv_path"].endswith(".env")
