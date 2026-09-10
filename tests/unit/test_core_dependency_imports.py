# SPDX-License-Identifier: BUSL-1.1
"""Clean core-install import contract.

Dependency remediation 2026-08-26: chromadb was de-scoped from the stable
default install, which revealed that ``jsonschema`` had been satisfied
only as a chromadb transitive while being a REAL production dependency —
six ``waggledance.core`` modules import it at module top level. This test
pins the contract that the declared core dependency set covers those
production imports, so a clean install of the default dependencies can
import the MAGMA core without any optional extra.

No chromadb, network, Ollama, or filesystem state is required.
"""
from __future__ import annotations

import importlib

import pytest

# Production modules with a top-level `import jsonschema` (grep-verified
# 2026-08-26). If one of these gains/loses the dependency, update the
# declaration in pyproject [project] dependencies and requirements.txt
# together with this list.
JSONSCHEMA_CORE_CONSUMERS = [
    "waggledance.core.idle_protocol",
    "waggledance.core.magma.evaluation_result",
    "waggledance.core.magma.rco_decision_artifact",
    "waggledance.core.magma.receipt",
    "waggledance.core.magma.schema_validation",
    "waggledance.core.magma.share_manifest",
]


def test_jsonschema_importable() -> None:
    import jsonschema  # noqa: F401


@pytest.mark.parametrize("module_name", JSONSCHEMA_CORE_CONSUMERS)
def test_magma_core_module_imports_cleanly(module_name: str) -> None:
    importlib.import_module(module_name)


def test_jsonschema_declared_as_core_dependency() -> None:
    """The declaration must live in [project] dependencies, not only in
    extras/CI files — production imports require the default install."""
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    cfg = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    deps = cfg["project"]["dependencies"]
    assert any(
        d.replace(" ", "").startswith("jsonschema>=") for d in deps
    ), f"jsonschema missing from core dependencies: {deps}"
