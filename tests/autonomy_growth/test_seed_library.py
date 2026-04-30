# SPDX-License-Identifier: Apache-2.0
"""Canonical seed library tests (Phase 13 P4)."""

from __future__ import annotations

import pytest

from waggledance.core.autonomy_growth import (
    LOW_RISK_FAMILY_KINDS,
    all_canonical_seeds,
    expected_per_family_counts,
    seeds_for_family,
)


def test_seed_library_total_meets_session_minimum() -> None:
    """At least 48 canonical seeds across all six families."""

    seeds = all_canonical_seeds()
    assert len(seeds) >= 48, (
        f"only {len(seeds)} canonical seeds; the session minimum is 48"
    )


def test_seed_library_covers_every_allowlisted_family() -> None:
    counts = expected_per_family_counts()
    for fam in LOW_RISK_FAMILY_KINDS:
        assert counts.get(fam, 0) > 0, f"family {fam} has no seeds"
        assert seeds_for_family(fam), f"family {fam} returned 0 from seeds_for_family"


def test_seed_keys_are_unique_within_family() -> None:
    """Solver_name_seed must be unique inside each family kind."""

    seeds = all_canonical_seeds()
    by_family: dict[str, set[str]] = {}
    for s in seeds:
        f = s["_family_kind"]
        by_family.setdefault(f, set())
        name = s["solver_name_seed"]
        assert name not in by_family[f], f"duplicate seed name {name} in {f}"
        by_family[f].add(name)


def test_seed_specs_have_required_phase11_keys() -> None:
    """Every seed's spec must satisfy the Phase 9/10 spec contract for
    its family. We do not call make_spec here (that requires registry
    access); we just check the structural contract."""

    required_by_family = {
        "scalar_unit_conversion": ("from_unit", "to_unit", "factor"),
        "lookup_table": ("table",),
        "threshold_rule": ("threshold", "operator",
                            "true_label", "false_label"),
        "interval_bucket_classifier": ("intervals",),
        "linear_arithmetic": ("coefficients", "intercept"),
        "bounded_interpolation": ("knots", "method", "min_x", "max_x"),
    }
    for s in all_canonical_seeds():
        spec = s["spec"]
        for k in required_by_family[s["_family_kind"]]:
            assert k in spec, (
                f"seed {s['solver_name_seed']} ({s['_family_kind']}) "
                f"missing required spec key {k}"
            )


def test_seeds_carry_validation_and_shadow_samples() -> None:
    for s in all_canonical_seeds():
        assert s["validation_cases"], (
            f"seed {s['solver_name_seed']} has no validation cases"
        )
        assert s["shadow_samples"], (
            f"seed {s['solver_name_seed']} has no shadow samples"
        )


def test_seeds_for_family_disjoint_subset() -> None:
    total = len(all_canonical_seeds())
    sum_subsets = sum(
        len(seeds_for_family(f)) for f in LOW_RISK_FAMILY_KINDS
    )
    assert total == sum_subsets


def test_capability_feature_seeds_carry_lookup_hints() -> None:
    """Phase 13 features need domain/subject hints. Verify the
    seed library populates them."""

    for s in all_canonical_seeds():
        family = s["_family_kind"]
        spec = s["spec"]
        if family == "lookup_table":
            assert "domain" in spec, "lookup_table seed must declare domain"
        elif family == "threshold_rule":
            assert "subject" in spec, "threshold_rule seed must declare subject"
        elif family == "interval_bucket_classifier":
            assert "subject" in spec, "interval seed must declare subject"
        elif family == "bounded_interpolation":
            assert "x_var" in spec and "y_var" in spec, (
                "bounded_interpolation seed must declare x_var and y_var"
            )
