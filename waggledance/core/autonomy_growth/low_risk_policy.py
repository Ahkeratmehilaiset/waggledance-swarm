# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Low-risk autogrowth allowlist (Phase 11).

The single source of truth is this file. The matching architecture
document lives at
``docs/architecture/LOW_RISK_AUTOGROWTH_POLICY.md``; a regression
test in ``tests/autonomy_growth/test_low_risk_policy.py`` enforces
parity between the doc and this constant.

Rules:

* Adding a family to ``LOW_RISK_FAMILY_KINDS`` REQUIRES that the family
  has a deterministic compiler in
  ``waggledance/core/solver_synthesis/deterministic_solver_compiler.py``
  AND a pure executor in
  ``waggledance/core/autonomy_growth/solver_executor.py``.
* The auto-promotion engine refuses to promote a family that is not in
  this list, regardless of any human override. The human-gated
  promotion path remains available through the existing Phase 9
  promotion ladder.
"""
from __future__ import annotations

LOW_RISK_POLICY_VERSION = 1

LOW_RISK_FAMILY_KINDS: tuple[str, ...] = (
    "scalar_unit_conversion",
    "lookup_table",
    "threshold_rule",
    "interval_bucket_classifier",
    "linear_arithmetic",
    "bounded_interpolation",
)


def is_low_risk_family(family_kind: str) -> bool:
    """Return True iff the family is auto-promote-eligible."""

    return family_kind in LOW_RISK_FAMILY_KINDS
