# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Projections — reflective and introspective views (v3.2)."""

from waggledance.core.projections.narrative_projector import project_narrative
from waggledance.core.projections.projection_validator import validate_projection
from waggledance.core.projections.introspection_view import build_introspection
from waggledance.core.projections.autobiographical_index import query_episodes

__all__ = [
    "project_narrative",
    "validate_projection",
    "build_introspection",
    "query_episodes",
]
