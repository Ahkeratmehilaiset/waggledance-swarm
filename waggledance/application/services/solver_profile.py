# SPDX-License-Identifier: Apache-2.0
"""R20.4 — solver-profile loader + Profile S import discipline.

Loads ``solver-profiles/<name>.json`` and exposes the resolved
profile to the runtime. The same-build-different-config requirement
means downstream code reads the profile object instead of branching
on hardcoded constants.

Profile S (``small``) MUST work without internet and without any LLM
runtime. To enforce that at the import boundary, this module exposes
``assert_profile_s_no_llm_imports()`` which inspects ``sys.modules``
and raises if any cloud-LLM provider library has leaked in. The
companion subprocess test in ``tests/test_solver_profile_small.py``
runs this in a fresh Python process to guarantee the assertion sees
a clean slate.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROFILE_DIR = REPO_ROOT / "solver-profiles"

# Profile S MUST stay clear of these — even a transitive import of a
# cloud LLM SDK is a Profile S regression.
LLM_PROVIDER_MODULES = frozenset({
    "anthropic",
    "openai",
    "azure.ai.openai",
    "google.cloud.aiplatform",
    "vertexai",
    "cohere",
    "groq",
    "together",
    "ollama",
    "llama_cpp",
    "vllm",
    "huggingface_hub",
})


def load_profile(name: str | None = None,
                  profile_dir: Path | None = None) -> dict[str, Any]:
    """Load and return the parsed solver profile.

    Resolution order: explicit ``name`` arg, then ``WAGGLE_PROFILE``
    env var, then ``small``.
    """
    if name is None:
        name = os.environ.get("WAGGLE_PROFILE", "small")
    if name not in {"small", "medium", "large"}:
        raise ValueError(f"unknown profile {name!r}")
    base = profile_dir if profile_dir is not None else DEFAULT_PROFILE_DIR
    path = base / f"{name}.json"
    if not path.is_file():
        raise FileNotFoundError(f"profile config missing: {path}")
    profile = json.loads(path.read_text(encoding="utf-8"))
    if profile.get("name") != name:
        raise ValueError(
            f"profile name mismatch: file says {profile.get('name')!r}, "
            f"requested {name!r}"
        )
    return profile


def is_internet_allowed(profile: dict[str, Any]) -> bool:
    return bool(profile.get("behaviors", {}).get("allow_internet", False))


def is_local_llm_allowed(profile: dict[str, Any]) -> bool:
    return bool(profile.get("behaviors", {}).get("allow_local_llm", False))


def is_cloud_llm_allowed(profile: dict[str, Any]) -> bool:
    return bool(profile.get("behaviors", {}).get("allow_cloud_llm", False))


def fallback_chain(profile: dict[str, Any]) -> list[str]:
    chain = profile.get("behaviors", {}).get("fallback_chain", ["heuristic"])
    return list(chain)


def assert_profile_s_no_llm_imports() -> None:
    """Profile S regression guard: assert no LLM provider library is
    in ``sys.modules``. Call this from a fresh subprocess after any
    Profile-S-relevant import surface so a stray transitive import
    fails the test (not just hides).
    """
    leaked = sorted(name for name in LLM_PROVIDER_MODULES if name in sys.modules)
    if leaked:
        raise AssertionError(
            "Profile S regression: LLM provider modules unexpectedly "
            f"imported into sys.modules: {leaked}"
        )
