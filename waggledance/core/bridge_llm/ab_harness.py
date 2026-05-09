# SPDX-License-Identifier: Apache-2.0
"""R20.3 — A/B harness for runtime LLM augmentation injection points.

Runs a heuristic-vs-treatment A/B for a single decision and records
both arms' latency + quality so a deployment-gate (≥20% quality gain
threshold per R20 master prompt §2.4) can be evaluated post-hoc.

Usage pattern at an injection point:

    from waggledance.core.bridge_llm.ab_harness import ABHarness

    harness = ABHarness(
        client=BridgeLLMClient.default(),
        injection_point="event_log_adapter.case_trajectory_quality_grade",
        treatment_share=float(os.environ.get("WAGGLE_R20_3_AB_SHARE", "0.0")),
    )

    def control_fn():
        return rule_based_quality_grade(...)

    treatment_request = LLMRequest(
        injection_point="event_log_adapter.case_trajectory_quality_grade",
        prompt=...,
        intent="grade_case",
    )

    result = harness.run(control_fn, treatment_request)
    use_grade = result.chosen

The harness ALWAYS computes the control. When treatment_share > 0 it
also computes the treatment so both arms can be compared. The
production decision (`chosen`) is selected by the configured share —
e.g. share=0.5 routes 50% to treatment, 50% to control.

Profile S compatibility: when client.is_enabled() is False (Profile S
disables BridgeLLMClient via solver-profiles/small.json), the treatment
arm short-circuits to the same heuristic the control already uses.
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .client import BridgeLLMClient
from .types import LLMRequest, LLMResponse


log = logging.getLogger(__name__)


@dataclass
class ABResult:
    """One A/B decision result."""

    control_value: Any
    treatment_value: Any | None
    chosen_value: Any
    chosen_arm: str  # "control" | "treatment"
    latency_control_ms: float
    latency_treatment_ms: float | None
    treatment_response: LLMResponse | None
    # Filled in by the caller post-hoc when an oracle is available.
    quality_control: float | None = None
    quality_treatment: float | None = None


class ABHarness:
    """Heuristic-vs-treatment A/B harness for one injection point.

    treatment_share=0.0 (default) means treatment is never run; the
    harness always returns control. This is the **safe default** for
    a doping point that does not yet have a quality oracle: the LLM
    code path is wired but unused, so a follow-up can flip the share
    once the oracle exists.
    """

    def __init__(
        self,
        client: BridgeLLMClient,
        injection_point: str,
        treatment_share: float = 0.0,
        rng_seed: int | None = None,
    ):
        if not (0.0 <= treatment_share <= 1.0):
            raise ValueError(
                f"treatment_share must be in [0,1], got {treatment_share}"
            )
        self._client = client
        self._injection_point = injection_point
        self._treatment_share = treatment_share
        self._rng = random.Random(rng_seed)

    @property
    def treatment_share(self) -> float:
        return self._treatment_share

    @property
    def injection_point(self) -> str:
        return self._injection_point

    def run(
        self,
        control_fn: Callable[[], Any],
        treatment_request: LLMRequest | None = None,
    ) -> ABResult:
        """Compute control (and treatment if share > 0) and pick.

        - control_fn is always called; its result is the heuristic baseline.
        - If self._treatment_share > 0 AND treatment_request is provided,
          the treatment arm is also computed via BridgeLLMClient.run().
        - The production "chosen" arm is selected uniformly by share.
        """
        # Control arm
        c_start = time.perf_counter()
        control_value = control_fn()
        c_latency = (time.perf_counter() - c_start) * 1000

        # Treatment arm (only when configured AND request supplied)
        treatment_value: Any | None = None
        treatment_response: LLMResponse | None = None
        t_latency: float | None = None
        if self._treatment_share > 0.0 and treatment_request is not None:
            t_start = time.perf_counter()
            try:
                response = self._client.run(treatment_request)
                treatment_response = response
                # The treatment value IS the response text — callers who
                # need typed extraction can post-process.
                treatment_value = response.text
                t_latency = (time.perf_counter() - t_start) * 1000
            except Exception as exc:
                # Hard requirement: the harness MUST NOT crash the call
                # site. A treatment failure falls through to control.
                log.debug(
                    "ABHarness treatment failed at %s: %s",
                    self._injection_point, exc,
                )
                t_latency = (time.perf_counter() - t_start) * 1000

        # Pick the production decision
        if (
            treatment_value is not None
            and self._rng.random() < self._treatment_share
        ):
            chosen_value = treatment_value
            chosen_arm = "treatment"
        else:
            chosen_value = control_value
            chosen_arm = "control"

        return ABResult(
            control_value=control_value,
            treatment_value=treatment_value,
            chosen_value=chosen_value,
            chosen_arm=chosen_arm,
            latency_control_ms=c_latency,
            latency_treatment_ms=t_latency,
            treatment_response=treatment_response,
        )
