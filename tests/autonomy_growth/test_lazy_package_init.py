# SPDX-License-Identifier: Apache-2.0
"""Regression tests for autonomy_growth package import discipline."""

from __future__ import annotations

import subprocess
import sys
import textwrap


def test_gap_intake_submodule_import_does_not_eager_load_promotion_engine() -> None:
    code = textwrap.dedent(
        """
        import sys

        import waggledance.core.autonomy_growth.gap_intake  # noqa: F401

        print(
            "waggledance.core.autonomy_growth.auto_promotion_engine"
            in sys.modules
        )
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.stdout.strip() == "False"


def test_package_reexports_still_resolve_lazily() -> None:
    from waggledance.core.autonomy_growth import (
        AutogrowthScheduler,
        FamilyOracleFn,
        GapSignal,
        RuntimeGapDetector,
    )

    assert AutogrowthScheduler.__name__ == "AutogrowthScheduler"
    assert FamilyOracleFn is not None
    assert GapSignal.__name__ == "GapSignal"
    assert RuntimeGapDetector.__name__ == "RuntimeGapDetector"


def test_operator_feedback_scheduler_preview_reexports_resolve_lazily() -> None:
    from waggledance.core.autonomy_growth import (
        OperatorFeedbackSchedulerEnqueuePreview,
        OperatorFeedbackSchedulerPreflight,
        build_operator_feedback_scheduler_enqueue_preview,
        build_operator_feedback_scheduler_preflight_from_bridge_log,
    )

    assert (
        OperatorFeedbackSchedulerEnqueuePreview.__name__
        == "OperatorFeedbackSchedulerEnqueuePreview"
    )
    assert OperatorFeedbackSchedulerPreflight.__name__ == (
        "OperatorFeedbackSchedulerPreflight"
    )
    assert build_operator_feedback_scheduler_enqueue_preview.__name__ == (
        "build_operator_feedback_scheduler_enqueue_preview"
    )
    assert build_operator_feedback_scheduler_preflight_from_bridge_log.__name__ == (
        "build_operator_feedback_scheduler_preflight_from_bridge_log"
    )
