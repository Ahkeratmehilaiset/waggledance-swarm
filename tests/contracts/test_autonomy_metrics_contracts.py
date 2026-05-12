# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from waggledance.core.autonomy.metrics import AutonomyMetrics


def test_record_case_grade_count_batches_counter_and_gold_rate() -> None:
    metrics = AutonomyMetrics()

    metrics.record_case_grade("gold", count=3)
    metrics.record_case_grade("silver", count=2)

    assert metrics.get_counter("case_grade_gold") == 3
    assert metrics.get_counter("case_grade_silver") == 2
    assert metrics.get_metric("case_gold") == 3 / 5


def test_record_case_grade_ignores_non_positive_count() -> None:
    metrics = AutonomyMetrics()

    metrics.record_case_grade("gold", count=0)
    metrics.record_case_grade("silver", count=-2)

    assert metrics.get_counter("case_grade_gold") == 0
    assert metrics.get_counter("case_grade_silver") == 0
    assert metrics.get_metric("case_gold") == 0.0
