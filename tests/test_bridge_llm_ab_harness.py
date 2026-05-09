"""R20.3 — ABHarness tests for runtime LLM augmentation A/B."""
from __future__ import annotations

import pytest


def test_harness_share_zero_skips_treatment():
    """treatment_share=0 means treatment never runs; chosen is always control."""
    from waggledance.core.bridge_llm import (
        ABHarness, BridgeLLMClient, LLMRequest,
    )
    client = BridgeLLMClient.disabled(reason="test")
    harness = ABHarness(
        client=client,
        injection_point="ab.test.zero",
        treatment_share=0.0,
    )
    result = harness.run(
        control_fn=lambda: "heuristic-answer",
        treatment_request=LLMRequest(
            injection_point="ab.test.zero", prompt="hi"
        ),
    )
    assert result.control_value == "heuristic-answer"
    assert result.treatment_value is None
    assert result.chosen_value == "heuristic-answer"
    assert result.chosen_arm == "control"
    assert result.latency_treatment_ms is None


def test_harness_share_full_runs_both_arms():
    """treatment_share=1.0 means treatment runs and is always chosen."""
    from waggledance.core.bridge_llm import (
        ABHarness, BridgeLLMClient, LLMRequest,
    )
    client = BridgeLLMClient.disabled(reason="test")
    harness = ABHarness(
        client=client,
        injection_point="ab.test.full",
        treatment_share=1.0,
        rng_seed=42,
    )
    result = harness.run(
        control_fn=lambda: "heuristic-answer",
        treatment_request=LLMRequest(
            injection_point="ab.test.full", prompt="hi"
        ),
    )
    assert result.control_value == "heuristic-answer"
    assert result.treatment_value is not None
    assert result.chosen_arm == "treatment"
    assert result.latency_treatment_ms is not None


def test_harness_treatment_failure_falls_through_to_control():
    """A treatment-arm exception MUST NOT crash the call. The harness
    records the treatment latency even on failure and falls through to
    using the control as `chosen`."""
    from waggledance.core.bridge_llm import (
        ABHarness, BridgeLLMClient, LLMRequest,
    )

    class CrashingClient(BridgeLLMClient):
        def run(self, request):
            raise RuntimeError("simulated treatment crash")

    # We can't easily subclass BridgeLLMClient with the convenience
    # constructors; build it directly.
    from waggledance.core.bridge_llm.providers.heuristic import HeuristicProvider
    crashing = CrashingClient(
        providers=[HeuristicProvider()],
        fallback_chain=("heuristic",),
        config={"enabled": True},
    )

    harness = ABHarness(
        client=crashing,
        injection_point="ab.test.crash",
        treatment_share=1.0,
        rng_seed=42,
    )
    result = harness.run(
        control_fn=lambda: "heuristic-answer",
        treatment_request=LLMRequest(
            injection_point="ab.test.crash", prompt="hi"
        ),
    )
    # Treatment crashed -> no treatment_value
    assert result.treatment_value is None
    # Chosen falls back to control regardless of share
    assert result.chosen_value == "heuristic-answer"
    assert result.chosen_arm == "control"
    # Latency for the failed treatment arm IS recorded so operators
    # can see crash cost in telemetry
    assert result.latency_treatment_ms is not None


def test_harness_records_both_latencies_when_share_full():
    from waggledance.core.bridge_llm import (
        ABHarness, BridgeLLMClient, LLMRequest,
    )
    client = BridgeLLMClient.disabled(reason="test")
    harness = ABHarness(
        client=client,
        injection_point="ab.test.latency",
        treatment_share=1.0,
        rng_seed=42,
    )
    result = harness.run(
        control_fn=lambda: "heuristic",
        treatment_request=LLMRequest(
            injection_point="ab.test.latency", prompt="hi"
        ),
    )
    assert result.latency_control_ms >= 0.0
    assert result.latency_treatment_ms is not None
    assert result.latency_treatment_ms >= 0.0


def test_harness_invalid_share_raises():
    from waggledance.core.bridge_llm import ABHarness, BridgeLLMClient
    with pytest.raises(ValueError, match="treatment_share"):
        ABHarness(
            client=BridgeLLMClient.disabled(reason="test"),
            injection_point="x",
            treatment_share=1.5,
        )


def test_harness_treatment_request_none_skips_treatment():
    """Even with share=1.0, if no treatment_request is provided the
    treatment arm is skipped (no LLMRequest = no LLM call)."""
    from waggledance.core.bridge_llm import ABHarness, BridgeLLMClient
    client = BridgeLLMClient.disabled(reason="test")
    harness = ABHarness(
        client=client,
        injection_point="ab.test.norequest",
        treatment_share=1.0,
    )
    result = harness.run(
        control_fn=lambda: "ctl",
        treatment_request=None,
    )
    assert result.treatment_value is None
    assert result.chosen_value == "ctl"
    assert result.chosen_arm == "control"


def test_harness_share_split_routes_proportionally():
    """With treatment_share=0.5 and 100 calls, roughly half should
    be 'treatment' and half 'control' under deterministic seeding.
    We allow a wide band (40/60) since 100 samples can drift."""
    from waggledance.core.bridge_llm import (
        ABHarness, BridgeLLMClient, LLMRequest,
    )
    client = BridgeLLMClient.disabled(reason="test")
    harness = ABHarness(
        client=client,
        injection_point="ab.test.split",
        treatment_share=0.5,
        rng_seed=12345,
    )
    arm_counts = {"control": 0, "treatment": 0}
    for _ in range(100):
        result = harness.run(
            control_fn=lambda: "ctl",
            treatment_request=LLMRequest(
                injection_point="ab.test.split", prompt="x"
            ),
        )
        arm_counts[result.chosen_arm] += 1
    # Allow generous tolerance for 100-sample variance
    assert 30 <= arm_counts["control"] <= 70
    assert 30 <= arm_counts["treatment"] <= 70
    assert arm_counts["control"] + arm_counts["treatment"] == 100
