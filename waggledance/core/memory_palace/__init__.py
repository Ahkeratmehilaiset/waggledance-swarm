# SPDX-License-Identifier: BUSL-1.1
"""Memory Palace projection contracts.

This package is a read-side projection layer only. MAGMA, MemoryService,
FAISS, and Chroma remain the sources of truth for memory content and history.
"""

from waggledance.core.memory_palace.projection import (
    MEMORY_PALACE_NAVIGATION_INDEX_SCHEMA_VERSION,
    MEMORY_PALACE_PROJECTION_SCHEMA_VERSION,
    MemoryPalaceProjectionError,
    MemoryPlacement,
    PalaceShortcutHint,
    PalaceShortcutCandidate,
    PalaceNode,
    build_memory_palace_navigation_index,
    build_memory_palace_projection,
    derive_candidate_placements,
    derive_shortcut_hints,
    rank_shortcut_candidates_for_memory,
    validate_palace_hierarchy,
)

__all__ = [
    "MEMORY_PALACE_NAVIGATION_INDEX_SCHEMA_VERSION",
    "MEMORY_PALACE_PROJECTION_SCHEMA_VERSION",
    "MemoryPalaceProjectionError",
    "MemoryPlacement",
    "PalaceShortcutHint",
    "PalaceShortcutCandidate",
    "PalaceNode",
    "build_memory_palace_navigation_index",
    "build_memory_palace_projection",
    "derive_candidate_placements",
    "derive_shortcut_hints",
    "rank_shortcut_candidates_for_memory",
    "validate_palace_hierarchy",
]
