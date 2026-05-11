"""R20.4 — solver-profile loader + Profile S import-discipline tests."""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE_DIR = REPO_ROOT / "solver-profiles"


# ─── Config presence + shape ─────────────────────────────────────

@pytest.mark.parametrize("name", ["small", "medium", "large"])
def test_profile_config_exists_and_self_consistent(name: str):
    """Every profile JSON must exist, parse, and have a `name` field
    that matches the file basename."""
    path = PROFILE_DIR / f"{name}.json"
    assert path.is_file(), f"missing profile config: {path}"
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert parsed["name"] == name
    assert "behaviors" in parsed
    assert "bridge_llm_client" in parsed
    assert "telemetry" in parsed


def test_profile_small_disallows_internet_and_llm():
    """Profile S contract: no internet, no local LLM, no cloud LLM."""
    parsed = json.loads((PROFILE_DIR / "small.json").read_text(encoding="utf-8"))
    behaviors = parsed["behaviors"]
    assert behaviors["allow_internet"] is False
    assert behaviors["allow_local_llm"] is False
    assert behaviors["allow_cloud_llm"] is False
    assert behaviors["fallback_chain"] == ["heuristic"]
    assert parsed["bridge_llm_client"]["enabled"] is False


def test_profile_medium_allows_local_no_cloud():
    """Profile M contract: local LLM yes, cloud LLM no."""
    parsed = json.loads((PROFILE_DIR / "medium.json").read_text(encoding="utf-8"))
    behaviors = parsed["behaviors"]
    assert behaviors["allow_local_llm"] is True
    assert behaviors["allow_cloud_llm"] is False
    assert "local-ollama" in behaviors["fallback_chain"]
    assert "heuristic" in behaviors["fallback_chain"]


def test_profile_large_full_chain_with_redaction_required():
    """Profile L contract: full four-tier chain; cloud allowed only
    with redaction ON BY DEFAULT."""
    parsed = json.loads((PROFILE_DIR / "large.json").read_text(encoding="utf-8"))
    behaviors = parsed["behaviors"]
    assert behaviors["allow_cloud_llm"] is True
    assert "cache" in behaviors["fallback_chain"]
    assert "anthropic-api" in behaviors["fallback_chain"]
    assert "cloud" not in behaviors["fallback_chain"]
    assert "heuristic" in behaviors["fallback_chain"]
    bridge = parsed["bridge_llm_client"]
    assert bridge["redaction_required"] is True, (
        "Profile L MUST require redaction by default for cloud LLM calls"
    )


# ─── Loader semantics ────────────────────────────────────────────

def test_load_profile_returns_parsed_dict():
    from waggledance.application.services.solver_profile import load_profile
    profile = load_profile("small")
    assert profile["name"] == "small"


def test_load_profile_unknown_name_raises():
    from waggledance.application.services.solver_profile import load_profile
    with pytest.raises(ValueError, match="unknown profile"):
        load_profile("xlarge")


def test_load_profile_env_var_default(monkeypatch):
    monkeypatch.setenv("WAGGLE_PROFILE", "medium")
    from waggledance.application.services.solver_profile import load_profile
    profile = load_profile()
    assert profile["name"] == "medium"


def test_helpers_match_profile_contract():
    from waggledance.application.services.solver_profile import (
        load_profile, is_internet_allowed, is_local_llm_allowed,
        is_cloud_llm_allowed, fallback_chain,
    )
    s = load_profile("small")
    m = load_profile("medium")
    L = load_profile("large")
    assert is_internet_allowed(s) is False
    assert is_local_llm_allowed(s) is False
    assert is_cloud_llm_allowed(s) is False
    assert is_local_llm_allowed(m) is True
    assert is_cloud_llm_allowed(m) is False
    assert is_cloud_llm_allowed(L) is True
    assert fallback_chain(s) == ["heuristic"]
    assert "heuristic" in fallback_chain(m)
    assert "heuristic" in fallback_chain(L)


# ─── Profile S import discipline (subprocess-isolated) ──────────

def test_profile_s_loads_with_no_llm_imports():
    """Headline R20.4 contract: in a fresh Python subprocess, loading
    Profile S and calling assert_profile_s_no_llm_imports() must NOT
    have any LLM provider library in sys.modules. Subprocess
    isolation is required because the parent test process may have
    imported LLM modules transitively."""
    script = textwrap.dedent('''
        import os, sys
        os.environ["WAGGLE_PROFILE"] = "small"
        # Path manipulation so the subprocess finds the package
        sys.path.insert(0, %r)
        from waggledance.application.services.solver_profile import (
            load_profile, assert_profile_s_no_llm_imports,
        )
        profile = load_profile()
        assert profile["name"] == "small"
        assert profile["behaviors"]["allow_internet"] is False
        assert profile["behaviors"]["allow_local_llm"] is False
        # The headline assertion: no LLM libs leaked in.
        assert_profile_s_no_llm_imports()
        print("PROFILE_S_OK")
    ''') % (str(REPO_ROOT),)
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, (
        f"Profile S subprocess failed: stdout={proc.stdout!r} "
        f"stderr={proc.stderr!r}"
    )
    assert "PROFILE_S_OK" in proc.stdout


def test_profile_s_assertion_fires_when_llm_imported():
    """Sanity check the regression guard is functional: artificially
    pre-import an LLM provider name into sys.modules and confirm the
    assertion catches it."""
    script = textwrap.dedent('''
        import os, sys, types
        os.environ["WAGGLE_PROFILE"] = "small"
        sys.path.insert(0, %r)
        # Inject a fake `anthropic` module to simulate a leaked import
        sys.modules["anthropic"] = types.ModuleType("anthropic")
        from waggledance.application.services.solver_profile import (
            assert_profile_s_no_llm_imports,
        )
        try:
            assert_profile_s_no_llm_imports()
        except AssertionError as e:
            if "anthropic" in str(e):
                print("PROFILE_S_GUARD_FIRED")
            else:
                print(f"PROFILE_S_GUARD_WRONG_MSG: {e}")
        else:
            print("PROFILE_S_GUARD_FAILED")
    ''') % (str(REPO_ROOT),)
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0
    assert "PROFILE_S_GUARD_FIRED" in proc.stdout, (
        f"guard didn't fire: stdout={proc.stdout!r}"
    )
