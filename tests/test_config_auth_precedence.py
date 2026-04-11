"""Phase 3 release-polish tests: config/auth precedence and secret safety.

These tests lock in three invariants that together make up the "secrets
are never leaked and precedence is deterministic" contract:

1. Precedence order for ``api_key``: ``WAGGLE_API_KEY`` env var > whatever
   ``from_env()`` would otherwise auto-generate. (YAML has no api_key.)
2. Precedence order for ``profile``/``runtime_primary``: env var > yaml >
   dataclass default.
3. The api key NEVER appears in ``repr(settings)``, ``str(settings)``, or
   ``runtime_diagnostics()``. Only a boolean indicator (``api_key_set``)
   is allowed in diagnostics output.
"""

import os
from unittest.mock import patch

from waggledance.adapters.config.settings_loader import WaggleSettings


_SENTINEL = "test-sentinel-api-key-do-not-leak-12345"


def _isolated_env(**overrides):
    """Build a clean env dict with WAGGLE_* vars stripped, plus overrides.

    We copy the existing os.environ so that PATH / PYTHONPATH / etc. survive
    on Windows, but force a deterministic WaggleDance slice.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("WAGGLE_")}
    env.update(overrides)
    return env


def test_api_key_from_env_var_wins_over_auto_generate():
    with patch.dict(os.environ, _isolated_env(WAGGLE_API_KEY=_SENTINEL), clear=True):
        s = WaggleSettings.from_env(env_path="/does/not/exist/.env")
    assert s.api_key == _SENTINEL


def test_api_key_auto_generated_when_env_empty():
    with patch.dict(os.environ, _isolated_env(), clear=True):
        s = WaggleSettings.from_env(env_path="/does/not/exist/.env")
    # Auto-generated keys from secrets.token_urlsafe(32) are >= 32 chars.
    assert s.api_key
    assert len(s.api_key) >= 32


def test_api_key_stripped_of_whitespace():
    with patch.dict(
        os.environ,
        _isolated_env(WAGGLE_API_KEY=f"  {_SENTINEL}  \n"),
        clear=True,
    ):
        s = WaggleSettings.from_env(env_path="/does/not/exist/.env")
    assert s.api_key == _SENTINEL


def test_profile_env_beats_default():
    with patch.dict(
        os.environ,
        _isolated_env(WAGGLE_API_KEY="k", WAGGLE_PROFILE="FACTORY"),
        clear=True,
    ):
        s = WaggleSettings.from_env(env_path="/does/not/exist/.env")
    assert s.profile == "FACTORY"


def test_api_key_not_in_repr():
    """repr(settings) is what @dataclass auto-generates and is the most
    common accidental-leak surface when operators log settings objects."""
    with patch.dict(
        os.environ,
        _isolated_env(WAGGLE_API_KEY=_SENTINEL),
        clear=True,
    ):
        s = WaggleSettings.from_env(env_path="/does/not/exist/.env")
    r = repr(s)
    assert _SENTINEL not in r, (
        f"api_key leaked in repr! first 200 chars: {r[:200]}"
    )


def test_api_key_not_in_str():
    with patch.dict(
        os.environ,
        _isolated_env(WAGGLE_API_KEY=_SENTINEL),
        clear=True,
    ):
        s = WaggleSettings.from_env(env_path="/does/not/exist/.env")
    assert _SENTINEL not in str(s)


def test_runtime_diagnostics_exposes_bool_not_value():
    with patch.dict(
        os.environ,
        _isolated_env(WAGGLE_API_KEY=_SENTINEL),
        clear=True,
    ):
        s = WaggleSettings.from_env(env_path="/does/not/exist/.env")
    diag = s.runtime_diagnostics()
    # Bool presence is allowed.
    assert diag["api_key_set"] is True
    # Raw value must not leak.
    assert _SENTINEL not in str(diag)
    for key, value in diag.items():
        assert value != _SENTINEL, f"api_key leaked via diagnostics[{key}]"


def test_runtime_diagnostics_false_when_no_key_configured():
    """When auto-generated we still report api_key_set=True — because the
    middleware is protected — but when the dataclass is constructed
    directly without a key, the bool flips to False."""
    s = WaggleSettings()  # defaults, api_key=""
    diag = s.runtime_diagnostics()
    assert diag["api_key_set"] is False
