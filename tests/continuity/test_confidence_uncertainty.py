# SPDX-License-Identifier: Apache-2.0
"""Tests for capability confidence → epistemic uncertainty integration."""

from __future__ import annotations

import pytest

from waggledance.core.world.epistemic_uncertainty import compute_uncertainty


class TestLowConfidenceIncreasesUncertainty:
    def test_no_confidence_data(self):
        """Without confidence data, low_confidence_capabilities = 0."""
        report = compute_uncertainty(
            entities=[],
            baseline_keys=set(),
        )
        assert report.low_confidence_capabilities == 0

    def test_high_confidence_no_effect(self):
        """All high-confidence capabilities should not increase score."""
        report = compute_uncertainty(
            entities=[],
            baseline_keys=set(),
            capability_confidence={"solve.math": 0.9, "solve.thermal": 0.8},
        )
        assert report.low_confidence_capabilities == 0

    def test_low_confidence_increases_score(self):
        """Low-confidence capabilities should increase uncertainty score."""
        report_without = compute_uncertainty(
            entities=[{"entity_id": "e1", "updated_at": 0}],
            baseline_keys=set(),
        )
        report_with = compute_uncertainty(
            entities=[{"entity_id": "e1", "updated_at": 0}],
            baseline_keys=set(),
            capability_confidence={
                "solve.math": 0.3,
                "solve.thermal": 0.2,
                "solve.stats": 0.1,
            },
        )
        assert report_with.low_confidence_capabilities == 3
        assert report_with.score >= report_without.score

    def test_custom_threshold(self):
        """Custom low_confidence_threshold should be respected."""
        report = compute_uncertainty(
            entities=[],
            baseline_keys=set(),
            capability_confidence={"solve.math": 0.6},
            low_confidence_threshold=0.7,
        )
        assert report.low_confidence_capabilities == 1

    def test_to_dict_includes_field(self):
        report = compute_uncertainty(
            entities=[],
            baseline_keys=set(),
            capability_confidence={"solve.bad": 0.1},
        )
        d = report.to_dict()
        assert "low_confidence_capabilities" in d
        assert d["low_confidence_capabilities"] == 1
