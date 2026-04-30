# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Per-family capability features for the dispatcher.

Each allowlisted family declares a small, structured feature set that
identifies *what kind* of query that solver answers — independent of
the specific runtime values. A query envelope at runtime carries the
same feature shape, and the dispatcher matches on equality.

Example: a ``scalar_unit_conversion`` solver that converts Celsius to
Kelvin has features ``{"from_unit": "C", "to_unit": "K"}``. A runtime
query stating "I want to convert from C to K" carries the same
features and matches that solver — by capability, not by exact
solver name and not by family-FIFO.
"""
from __future__ import annotations

from typing import Any, Mapping


def _scalar_unit_conversion_features(spec: Mapping[str, Any]) -> dict[str, str]:
    return {
        "from_unit": str(spec.get("from_unit", "_")),
        "to_unit": str(spec.get("to_unit", "_")),
    }


def _lookup_table_features(spec: Mapping[str, Any]) -> dict[str, str]:
    """Lookup tables key on a *domain* label declared by the seed.

    Without a domain hint, every lookup table would feature-match
    every key-based query. We require the seed library to set
    ``domain`` (e.g. "color", "status", "verdict") so dispatch is
    meaningful at scale.
    """

    return {
        "domain": str(spec.get("domain", "_")),
        "default_present": "true" if spec.get("default") is not None else "false",
    }


def _threshold_rule_features(spec: Mapping[str, Any]) -> dict[str, str]:
    return {
        "subject": str(spec.get("subject", "_")),
        "operator": str(spec.get("operator", "_")),
    }


def _interval_bucket_features(spec: Mapping[str, Any]) -> dict[str, str]:
    return {
        "subject": str(spec.get("subject", "_")),
    }


def _linear_arithmetic_features(spec: Mapping[str, Any]) -> dict[str, str]:
    cols = spec.get("input_columns") or []
    cols_sig = "|".join(str(c) for c in cols) or "_positional"
    return {
        "input_columns_signature": cols_sig,
    }


def _bounded_interpolation_features(spec: Mapping[str, Any]) -> dict[str, str]:
    return {
        "x_var": str(spec.get("x_var", "x")),
        "y_var": str(spec.get("y_var", "y")),
    }


_FEATURE_EXTRACTORS = {
    "scalar_unit_conversion": _scalar_unit_conversion_features,
    "lookup_table": _lookup_table_features,
    "threshold_rule": _threshold_rule_features,
    "interval_bucket_classifier": _interval_bucket_features,
    "linear_arithmetic": _linear_arithmetic_features,
    "bounded_interpolation": _bounded_interpolation_features,
}


def extract_features(family_kind: str, spec: Mapping[str, Any]) -> dict[str, str]:
    """Return the structured feature set for a (family, spec) pair.

    Returns an empty dict for unknown families. The dispatcher's
    ``dispatch_by_features`` method refuses to match when the feature
    set is empty — that prevents an unbounded scan and forces the
    caller to fall through to the family-FIFO path or to harvest.
    """

    extractor = _FEATURE_EXTRACTORS.get(family_kind)
    return extractor(spec) if extractor else {}


def feature_dimensions(family_kind: str) -> tuple[str, ...]:
    """Names of the feature dimensions for a family. Used by docs/tests."""

    examples = {
        "scalar_unit_conversion": ("from_unit", "to_unit"),
        "lookup_table": ("domain", "default_present"),
        "threshold_rule": ("subject", "operator"),
        "interval_bucket_classifier": ("subject",),
        "linear_arithmetic": ("input_columns_signature",),
        "bounded_interpolation": ("x_var", "y_var"),
    }
    return examples.get(family_kind, ())


__all__ = [
    "extract_features",
    "feature_dimensions",
]
