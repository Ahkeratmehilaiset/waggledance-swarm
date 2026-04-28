# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Concrete declarative family specs (Phase 10 P4).

The Phase 9 ``SolverFamilyRegistry`` defines the ten family *kinds*
and their required spec keys. This subpackage holds *concrete*
declarative spec templates — small, hand-curated examples and
canonical defaults that the bootstrap orchestrator can present to
the LLM as priors and that tests can use as fixtures.

Each entry is a plain dict so the file can be loaded without any
import dependency on Phase 9 dataclasses. The bootstrap orchestrator
turns these into :class:`SolverSpec` instances when needed.
"""

from __future__ import annotations

from typing import Mapping

# Canonical, deterministic example specs. Keys are family kinds.
EXAMPLE_SPECS: Mapping[str, Mapping[str, object]] = {
    "scalar_unit_conversion": {
        "from_unit": "celsius",
        "to_unit": "fahrenheit",
        "factor": 1.8,
        "offset": 32.0,
    },
    "threshold_rule": {
        "threshold": 100.0,
        "operator": ">=",
        "true_label": "high",
        "false_label": "normal",
    },
    "linear_arithmetic": {
        "coefficients": [1.0, -0.5],
        "intercept": 2.0,
    },
    "weighted_aggregation": {
        "weights": [0.4, 0.3, 0.3],
    },
    "interval_bucket_classifier": {
        "intervals": [
            [0.0, 10.0, "low"],
            [10.0, 100.0, "mid"],
            [100.0, 1e9, "high"],
        ],
    },
}


def example_spec(family_kind: str) -> Mapping[str, object]:
    if family_kind not in EXAMPLE_SPECS:
        raise KeyError(f"no example spec for family_kind {family_kind!r}")
    return dict(EXAMPLE_SPECS[family_kind])
