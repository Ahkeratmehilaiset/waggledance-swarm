# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Scale-safe control-plane / data-plane storage foundation.

This package is the substrate referenced by RULE 4 of the Phase 10 master
prompt:

* MAGMA remains the append-only audit / provenance / history plane (in
  ``waggledance/core/magma/``).
* FAISS / vector stores remain the retrieval / data plane (today still
  rooted at ``data/faiss/`` via ``core/faiss_store.py``).
* This package is the *control / metadata* plane: registries, query
  helpers, path resolution.

The control-plane database is SQLite by default. The schema is designed
to support tens of thousands of solvers, capabilities, vector shards,
provider/builder jobs, and promotion-ladder transitions without a
schema rewrite. Existing subsystems can keep their own JSON/YAML
manifests; this package is additive and does not migrate them.

Phase 10 P2 introduces the bones (schema, control-plane, path resolver,
registry queries). Population/migration of existing data is explicitly
follow-up work; nothing in this package mutates runtime behavior on
import.
"""

from .control_plane_schema import (
    SCHEMA_VERSION,
    INITIAL_SCHEMA_SQL,
    MIGRATIONS,
)
from .control_plane import (
    ControlPlaneDB,
    ControlPlaneError,
    SolverFamilyRecord,
    SolverRecord,
    CapabilityRecord,
    VectorShardRecord,
    VectorIndexRecord,
    ProviderJobRecord,
    BuilderJobRecord,
    PromotionStateRecord,
    CutoverStateRecord,
    RuntimePathBinding,
)
from .path_resolver import (
    PathResolver,
    PathResolverError,
    ResolvedPath,
    LogicalPathKind,
)
from .registry_queries import RegistryQueries

__all__ = [
    "SCHEMA_VERSION",
    "INITIAL_SCHEMA_SQL",
    "MIGRATIONS",
    "ControlPlaneDB",
    "ControlPlaneError",
    "SolverFamilyRecord",
    "SolverRecord",
    "CapabilityRecord",
    "VectorShardRecord",
    "VectorIndexRecord",
    "ProviderJobRecord",
    "BuilderJobRecord",
    "PromotionStateRecord",
    "CutoverStateRecord",
    "RuntimePathBinding",
    "PathResolver",
    "PathResolverError",
    "ResolvedPath",
    "LogicalPathKind",
    "RegistryQueries",
]
