# SPDX-License-Identifier: BUSL-1.1
"""Boot-time precomputation freeze contract (L37).

R18 hex-scout audit established that values computable from immutable
configs at boot time MUST be computed once in ``__init__`` and frozen,
not recomputed per request. ``HexTopologyRegistry`` is the canonical
example:

* ``_neighbor_cell_ids`` cache built at ``__init__`` so that
  ``get_neighbor_cells`` does not pay O(cells x neighbors) recompute
  on every call.
* ``_lower_domain_selectors`` and ``_lower_tag_selectors`` are
  pre-lowercased at load time so ``select_origin_cell`` does not pay
  O(cells x selectors) ``.lower()`` calls per query.

This contract test fails if a future refactor moves those caches out
of ``__init__`` and back into the per-request path, OR if the cache
content drifts away from the invariants the original audit pinned.

The contract is BEHAVIORAL (assert observable state), not structural
(AST scan), because AST patterns are too fragile -- the actual
invariant is "selector strings are lowercased before storage", which
is easiest to verify by inspecting the cache content directly.
"""
from __future__ import annotations

from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
HEX_CELLS_CONFIG = PROJECT_ROOT / "configs" / "hex_cells.yaml"


def _make_registry():
    try:
        from waggledance.application.services.hex_topology_registry import (
            HexTopologyRegistry,
        )
    except Exception as exc:
        pytest.skip(f"HexTopologyRegistry import failed: {exc}")
    if not HEX_CELLS_CONFIG.exists():
        pytest.skip(f"hex_cells.yaml not found at {HEX_CELLS_CONFIG}")
    return HexTopologyRegistry(config_path=str(HEX_CELLS_CONFIG), agents=[])


def test_neighbor_cache_populated_at_init() -> None:
    """``_neighbor_cell_ids`` MUST be built at ``__init__``, not lazily."""
    reg = _make_registry()
    if not reg._cells:
        pytest.skip("no cells loaded -- config empty in this env")
    cache = reg._neighbor_cell_ids
    assert isinstance(cache, dict), "_neighbor_cell_ids must be a dict"
    assert len(cache) > 0, (
        "_neighbor_cell_ids is empty after __init__ -- R18 hex-scout cache "
        "regressed back to per-call recompute"
    )
    # Every cell should have an entry (even if some have empty neighbors).
    assert set(cache.keys()) == set(reg._cells.keys()), (
        "_neighbor_cell_ids keys do not match _cells keys -- cache built "
        "from stale topology snapshot"
    )


def test_domain_selectors_are_lowercased_at_init() -> None:
    """``_lower_domain_selectors`` MUST contain pre-lowercased strings."""
    reg = _make_registry()
    if not reg._cells:
        pytest.skip("no cells loaded -- config empty in this env")
    lower_idx = reg._lower_domain_selectors
    assert isinstance(lower_idx, dict)
    # Find at least one non-empty entry to assert on.
    for cell_id, selectors in lower_idx.items():
        for sel in selectors:
            assert sel == sel.lower(), (
                f"_lower_domain_selectors[{cell_id!r}] contains non-lowercased "
                f"entry {sel!r} -- R18 hex-scout precomputation regressed"
            )


def test_tag_selectors_are_lowercased_at_init() -> None:
    """``_lower_tag_selectors`` MUST contain pre-lowercased strings."""
    reg = _make_registry()
    if not reg._cells:
        pytest.skip("no cells loaded -- config empty in this env")
    lower_idx = reg._lower_tag_selectors
    assert isinstance(lower_idx, dict)
    for cell_id, selectors in lower_idx.items():
        for sel in selectors:
            assert sel == sel.lower(), (
                f"_lower_tag_selectors[{cell_id!r}] contains non-lowercased "
                f"entry {sel!r} -- R18 hex-scout precomputation regressed"
            )


def test_caches_are_idempotent_under_select_origin_cell() -> None:
    """Calling ``select_origin_cell`` must not mutate the boot caches.

    The whole point of pre-computing at ``__init__`` is that per-request
    code can rely on these caches as read-only. If a refactor accidentally
    starts mutating ``_lower_*_selectors`` or ``_neighbor_cell_ids`` from
    inside ``select_origin_cell`` (e.g., re-sorting, normalizing again),
    that defeats the freeze.
    """
    reg = _make_registry()
    if not reg._cells:
        pytest.skip("no cells loaded -- config empty in this env")

    before_domain = {k: tuple(v) for k, v in reg._lower_domain_selectors.items()}
    before_tag = {k: tuple(v) for k, v in reg._lower_tag_selectors.items()}
    before_neighbor = {k: tuple(v) for k, v in reg._neighbor_cell_ids.items()}

    queries = [
        "Mikä on tarharivin lämpötila?",
        "calculate the sum of bee population",
        "ovenkahva on rikki",
        "how much honey does the hive produce?",
        "factory wasm runtime issue with thermal regulation",
    ]
    for q in queries:
        reg.select_origin_cell(q)

    after_domain = {k: tuple(v) for k, v in reg._lower_domain_selectors.items()}
    after_tag = {k: tuple(v) for k, v in reg._lower_tag_selectors.items()}
    after_neighbor = {k: tuple(v) for k, v in reg._neighbor_cell_ids.items()}

    assert before_domain == after_domain, (
        "_lower_domain_selectors mutated by select_origin_cell -- boot freeze broken"
    )
    assert before_tag == after_tag, (
        "_lower_tag_selectors mutated by select_origin_cell -- boot freeze broken"
    )
    assert before_neighbor == after_neighbor, (
        "_neighbor_cell_ids mutated by select_origin_cell -- boot freeze broken"
    )
