# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Provider plane execution layer (Phase 10 P3).

Phase 9 shipped the *types* and *routing* scaffold for the provider
plane and builder lane (``waggledance/core/provider_plane/`` and
``waggledance/core/builder_lane/``). Those modules define dataclasses,
priority chains, and routing decisions, but they do not actually call
out to providers.

This package is the **execution layer**:

* :mod:`provider_contracts` validates request/response packs against
  the canonical JSON schemas at ``schemas/provider_*.schema.json``.
* :mod:`provider_registry` bridges the in-memory registry from
  ``core/provider_plane/provider_registry.py`` to the control-plane
  database, persisting ``provider_jobs`` and reading active providers.
* :mod:`provider_plane` is the orchestrator: take a validated request,
  route it through the existing router, dispatch to the chosen
  provider's adapter, persist the job, return a normalized response.
* :mod:`claude_code_builder` is the only authorised subprocess lane
  in this prompt (RULE 17) — isolated worktree only, bounded timeout,
  logged, counts against the provider budget. Falls back to dry-run
  mode if the Claude Code CLI is not available.
* :mod:`builder_lane_router` and :mod:`builder_job_queue` integrate
  the existing builder-lane routing with the control-plane
  ``builder_jobs`` table.
* :mod:`mentor_forge` and :mod:`repair_forge` are thin glue around the
  existing ``builder_lane`` forges, adding control-plane persistence
  and the IR ``learning_suggestion`` advisory boundary.

Mentor-output boundary (RULE per Phase 9 §U2 + Phase 10 P3):
mentor notes become IR objects of type ``learning_suggestion`` with
``lifecycle_status='advisory'``. They cannot directly mutate runtime
or architecture. They can only become proposals through the existing
reviewed pipeline.
"""

from .provider_contracts import (
    ProviderRequest,
    ProviderResponse,
    ProviderContractError,
    validate_request,
    validate_response,
    PROVIDER_TYPES,
    TASK_CLASSES,
    TRUST_LAYERS,
)
from .provider_registry import (
    ProviderConfig,
    ProviderPlaneRegistry,
)
from .provider_plane import (
    ProviderPlane,
    ProviderPlaneError,
    ProviderDispatchResult,
)
from .claude_code_builder import (
    ClaudeCodeBuilder,
    ClaudeCodeBuilderUnavailable,
)
from .builder_job_queue import (
    BuilderJobQueue,
)
from .builder_lane_router import (
    BuilderLaneRouter,
)
from .mentor_forge import (
    MentorForge,
)
from .repair_forge import (
    RepairForge,
)

__all__ = [
    "ProviderRequest",
    "ProviderResponse",
    "ProviderContractError",
    "validate_request",
    "validate_response",
    "PROVIDER_TYPES",
    "TASK_CLASSES",
    "TRUST_LAYERS",
    "ProviderConfig",
    "ProviderPlaneRegistry",
    "ProviderPlane",
    "ProviderPlaneError",
    "ProviderDispatchResult",
    "ClaudeCodeBuilder",
    "ClaudeCodeBuilderUnavailable",
    "BuilderJobQueue",
    "BuilderLaneRouter",
    "MentorForge",
    "RepairForge",
]
