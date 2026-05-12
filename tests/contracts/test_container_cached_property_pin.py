# SPDX-License-Identifier: BUSL-1.1
"""Container @cached_property regression contract (L36).

PR #281 (lazy autonomy_growth re-exports) established the lazy-init
pattern at package level. The companion pattern at instance level is
``@cached_property`` on ``waggledance.bootstrap.container.Container``:
each service accessor (``container.llm``, ``container.control_plane_db``,
``container.orchestrator``, etc.) is computed once on first access and
cached on the instance, then subsequent accesses are O(1) dict lookups.

If a future refactor accidentally swaps ``@cached_property`` for plain
``@property``, that service gets re-constructed on every access -- the
Container loses its singleton-per-instance property and the cold-boot
savings from PR #281 leak back via the per-request path.

This contract test pins the exact set of Container methods that MUST
remain ``@cached_property``. Adding a new service accessor requires
updating the set in this test (forces deliberation). Removing one
requires the same (forces the commit message to explain why a
previously-cached service is no longer cached).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONTAINER_PATH = (
    PROJECT_ROOT / "waggledance" / "bootstrap" / "container.py"
)


# Pinned set of method names on Container that must use @cached_property.
# Adding to this set: PR that adds a new service accessor MUST update
# this list and document why the new accessor is cache-safe (no
# side-effecting init, no per-call argument variance).
# Removing from this set: PR that removes a service accessor must update
# this list and explain the removal in the commit message.
PINNED_CACHED_PROPERTIES: frozenset[str] = frozenset(
    {
        # LLM / model adapters
        "llm",
        "gemma_router",
        # Storage / persistence
        "vector_store",
        "memory_repository",
        "trust_store",
        "shared_memory",
        "hot_cache",
        "control_plane_db",
        "faiss_registry",
        # Configuration / events
        "config",
        "event_bus",
        # Autonomy + autogrowth substrate
        "autogrowth_scheduler",
        "autogrowth_background_ticker",
        "scheduler",
        "orchestrator",
        "autonomy_service",
        # Application services
        "memory_service",
        "chat_service",
        "learning_service",
        "readiness_service",
        "night_pipeline",
        # Resource + lifecycle management
        "elastic_scaler",
        "adaptive_throttle",
        "resource_guard",
        "priority_lock",
        "storage_health",
        # Hex topology + retrieval
        "hex_cell_topology",
        "hex_topology_registry",
        "hex_health_monitor",
        "hex_neighbor_assist",
        "hybrid_retrieval",
        "hybrid_observer",
        "hybrid_backfill",
        "parallel_dispatcher",
        # Solver lab / verifier / accelerator
        "solver_candidate_lab",
        "gemma_verifier_advisor",
        "synthetic_accelerator",
        # Feed ingest
        "feed_ingest_sink",
        "data_feed_scheduler",
    }
)


def _find_container_class(tree: ast.Module) -> ast.ClassDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Container":
            return node
    raise AssertionError("Container class not found in container.py")


def _decorator_names(node: ast.FunctionDef) -> set[str]:
    names: set[str] = set()
    for d in node.decorator_list:
        if isinstance(d, ast.Name):
            names.add(d.id)
        elif isinstance(d, ast.Attribute):
            names.add(d.attr)
        elif isinstance(d, ast.Call):
            if isinstance(d.func, ast.Name):
                names.add(d.func.id)
            elif isinstance(d.func, ast.Attribute):
                names.add(d.func.attr)
    return names


def _cached_properties_in_container() -> set[str]:
    if not CONTAINER_PATH.exists():
        pytest.skip(f"container.py not found at {CONTAINER_PATH}")
    tree = ast.parse(CONTAINER_PATH.read_text(encoding="utf-8"))
    container = _find_container_class(tree)
    found: set[str] = set()
    for item in container.body:
        if isinstance(item, ast.FunctionDef):
            if "cached_property" in _decorator_names(item):
                found.add(item.name)
    return found


def _plain_properties_in_container() -> set[str]:
    if not CONTAINER_PATH.exists():
        pytest.skip(f"container.py not found at {CONTAINER_PATH}")
    tree = ast.parse(CONTAINER_PATH.read_text(encoding="utf-8"))
    container = _find_container_class(tree)
    found: set[str] = set()
    for item in container.body:
        if isinstance(item, ast.FunctionDef):
            decorators = _decorator_names(item)
            if "property" in decorators and "cached_property" not in decorators:
                found.add(item.name)
    return found


def test_container_cached_properties_match_pinned_set() -> None:
    found = _cached_properties_in_container()
    missing = PINNED_CACHED_PROPERTIES - found
    extra = found - PINNED_CACHED_PROPERTIES
    assert not missing, (
        "Pinned cached_property entries are NO LONGER decorated with "
        f"@cached_property in container.py: {sorted(missing)}. "
        "Either restore the decorator (PR #281 / R18 lazy-init pattern) "
        "OR update PINNED_CACHED_PROPERTIES in this test if the removal "
        "is intentional (and explain why in the commit message)."
    )
    assert not extra, (
        f"New @cached_property entries found in container.py: {sorted(extra)}. "
        "Update PINNED_CACHED_PROPERTIES in this test to include them. "
        "Each new entry should be cache-safe (no per-call argument variance, "
        "no side-effects beyond construction)."
    )


def test_no_plain_property_decorators_in_container() -> None:
    """Plain ``@property`` is a regression risk for service accessors --
    the body re-runs on every access. If a Container method genuinely
    needs ``@property`` (rare: e.g., a read-only view of internal state
    that MUST recompute), document why in the test as an explicit
    exemption.
    """
    plain = _plain_properties_in_container()
    assert not plain, (
        f"Container has plain @property decorators (not @cached_property): {sorted(plain)}. "
        "Service accessors must use @cached_property so the body runs once per Container "
        "instance, not per access. If a recomputing @property is genuinely needed, add the "
        "method name to an explicit exemption set in this test with rationale."
    )


def test_scanner_catches_synthetic_violation() -> None:
    """Negative test: confirm the AST scanner reports a synthetic
    cached_property removal."""
    synthetic_src = """
class Container:
    @cached_property
    def real_cached(self):
        return 1

    @property
    def fake_property(self):
        return 2

    def normal_method(self):
        return 3
"""
    tree = ast.parse(synthetic_src)
    container = _find_container_class(tree)
    cached_names = {
        item.name
        for item in container.body
        if isinstance(item, ast.FunctionDef)
        and "cached_property" in _decorator_names(item)
    }
    plain_names = {
        item.name
        for item in container.body
        if isinstance(item, ast.FunctionDef)
        and "property" in _decorator_names(item)
        and "cached_property" not in _decorator_names(item)
    }
    assert cached_names == {"real_cached"}
    assert plain_names == {"fake_property"}
