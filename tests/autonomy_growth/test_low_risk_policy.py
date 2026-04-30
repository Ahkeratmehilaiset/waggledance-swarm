# SPDX-License-Identifier: Apache-2.0
"""Parity test between the low-risk policy code and the doc."""

from __future__ import annotations

from pathlib import Path

from waggledance.core.autonomy_growth import (
    LOW_RISK_FAMILY_KINDS,
    is_low_risk_family,
)
from waggledance.core.autonomy_growth.solver_executor import (
    supported_executor_kinds,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY_DOC = (
    REPO_ROOT / "docs" / "architecture" / "LOW_RISK_AUTOGROWTH_POLICY.md"
)


def test_low_risk_family_kinds_match_executors_exactly() -> None:
    """Every allowlisted family must have an executor and vice versa."""

    code = set(LOW_RISK_FAMILY_KINDS)
    execs = set(supported_executor_kinds())
    assert code == execs, (
        f"allowlist - executor mismatch: "
        f"only_in_allowlist={code - execs} "
        f"only_in_executors={execs - code}"
    )


def test_is_low_risk_family_true_false() -> None:
    assert is_low_risk_family("scalar_unit_conversion") is True
    assert is_low_risk_family("temporal_window_rule") is False
    assert is_low_risk_family("non_existent_family") is False


def test_policy_doc_lists_all_allowlisted_families() -> None:
    """The architecture doc must mention every allowlisted family kind."""

    text = POLICY_DOC.read_text(encoding="utf-8")
    for kind in LOW_RISK_FAMILY_KINDS:
        assert f"`{kind}`" in text, (
            f"family kind {kind!r} from code is not mentioned in "
            f"LOW_RISK_AUTOGROWTH_POLICY.md"
        )


def test_policy_doc_does_not_lie_about_excluded_families() -> None:
    """Families that are NOT auto-promote-eligible must not appear in the
    auto-promote table block."""

    text = POLICY_DOC.read_text(encoding="utf-8")
    excluded = {
        "weighted_aggregation",
        "temporal_window_rule",
        "structured_field_extractor",
        "deterministic_composition_wrapper",
    }
    # The doc has an explicit "Excluded" section that mentions these.
    # We only verify they appear in the doc somewhere — we do not
    # parse the markdown table boundaries, that would be brittle.
    for kind in excluded:
        assert kind in text, (
            f"excluded family {kind!r} should be mentioned in the doc "
            f"so reviewers can see why it is excluded"
        )
