# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Confidence decay — query-time decay for fact confidence values.

v3.2 MAGMA expansion: observed facts decay faster than verifier-confirmed
facts. Original confidence values are never destructively rewritten;
decay is applied at query/use time only.
"""

from __future__ import annotations

import math


# Source types that receive the slower (longer half-life) decay rate
_VERIFIED_SOURCE_TYPES = frozenset({
    "confirmed_by_verifier",
    "inferred_by_solver",
    "inferred_by_rule",
})


def decayed_confidence(
    original_confidence: float,
    age_hours: float,
    half_life_hours: float = 168.0,
    source_type: str = "observed",
) -> float:
    """Compute time-decayed confidence without modifying the original value.

    Args:
        original_confidence: The stored confidence (0.0-1.0), never modified.
        age_hours: Hours since the fact was recorded.
        half_life_hours: Base half-life in hours (default 168 = 7 days).
            Verified facts use this value directly.
            Observed/other facts use half this value (faster decay).
        source_type: Provenance source type. Verified sources decay slower.

    Returns:
        Decayed confidence in [0.0, original_confidence].
    """
    if age_hours <= 0:
        return original_confidence

    # Verified facts decay at the base rate; others decay twice as fast
    effective_half_life = half_life_hours
    if source_type not in _VERIFIED_SOURCE_TYPES:
        effective_half_life = half_life_hours / 2.0

    decay_factor = math.exp(-math.log(2) * age_hours / effective_half_life)
    return original_confidence * decay_factor
