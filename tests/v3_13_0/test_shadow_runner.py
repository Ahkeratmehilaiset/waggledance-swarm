# SPDX-License-Identifier: BUSL-1.1
"""Tests for ShadowRunner v1.

Covers acceptance criteria from shadow_runner_scaffold_spec.md:
* Synthetic baseline shadow run (no real operator data)
* All fail-closed abort paths
* MAGMA audit events emitted
"""
from __future__ import annotations

from typing import Optional

import pytest

from waggledance.core.v3_13_0.shadow_runner import (
    BaselineOutput,
    CandidateOutput,
    DivergenceScore,
    ShadowAbortReason,
    ShadowRunner,
    ShadowRunInput,
    ShadowRunResult,
    ShadowProfileView,
    ShadowToolView,
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _emit_collector(collector: list):
    def emit(envelope: dict) -> str:
        envelope_id = f"evt_{len(collector):04d}"
        envelope["__id"] = envelope_id
        collector.append(envelope)
        return envelope_id
    return emit


def _candidate_runner(*, cost: float = 0.0,
                       write_risk: str = "internal_memory",
                       elapsed_ms: int = 10,
                       artifact_uri: str = "artifact://shadow/cand_0001"):
    def run(_input: ShadowRunInput) -> CandidateOutput:
        return CandidateOutput(
            artifact_uri=artifact_uri,
            elapsed_ms=elapsed_ms,
            cost_consumed=cost,
            write_risk_observed=write_risk,
        )
    return run


def _baseline_runner(*, exit_code: int = 0, elapsed_ms: int = 10,
                      artifact_uri: str = "artifact://shadow/base_0001"):
    def run(_input: ShadowRunInput) -> BaselineOutput:
        return BaselineOutput(
            artifact_uri=artifact_uri,
            elapsed_ms=elapsed_ms,
            exit_code=exit_code,
        )
    return run


def _compare_returning(score: float = 0.0,
                        delta_ref: str = "artifact://shadow/delta_0001"):
    def compare(c, b, fmt) -> DivergenceScore:
        return DivergenceScore(score=score, delta_summary_ref=delta_ref,
                                per_step_scores=[score])
    return compare


def _make_runner(*, tool: ShadowToolView,
                  profile: ShadowProfileView,
                  candidate_run=None,
                  baseline_run=None,
                  compare=None,
                  operator_owned_states: tuple = (),
                  events: list = None):
    events = events if events is not None else []
    return ShadowRunner(
        fetch_tool_descriptor=lambda _tid: tool,
        fetch_profile_config=lambda _pid: profile,
        run_candidate=candidate_run or _candidate_runner(),
        run_baseline=baseline_run or _baseline_runner(),
        compare_outputs=compare or _compare_returning(),
        emit_magma_event=_emit_collector(events),
        state_handle_is_operator_owned=lambda sref: sref in operator_owned_states,
    )


def _shadow_input(**overrides) -> ShadowRunInput:
    base = dict(
        candidate_manifest_id="cand:m_001",
        shadow_input_set_ref="capture:c_001",
        profile_config_ref="profile:home",
        tool_descriptor_id="tool_test",
        state_handles=["state:shadow_tempfile"],
        operator_baseline_command=["python", "demo.py"],
        expected_output_format="json",
        shadow_run_id="run_001",
    )
    base.update(overrides)
    return ShadowRunInput(**base)


def _supported_tool(write_risk: str = "internal_memory") -> ShadowToolView:
    return ShadowToolView(
        tool_descriptor_id="tool_test",
        shadow_supported=True,
        write_risk_class=write_risk,
    )


def _budget_profile(*, budget_s: int = 30, cap_usd: float = 0.05) -> ShadowProfileView:
    return ShadowProfileView(
        profile_id="profile:home",
        shadow_eval_budget_s=budget_s,
        per_shadow_run_cap_usd=cap_usd,
    )


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------


class TestShadowHappyPath:

    def test_synthetic_shadow_run_emits_started_and_completed(self):
        events = []
        runner = _make_runner(
            tool=_supported_tool(),
            profile=_budget_profile(),
            events=events,
        )
        result = runner.run(_shadow_input())
        assert isinstance(result, ShadowRunResult)
        assert result.abort_reason is None
        assert result.divergence_score == 0.0
        assert result.candidate_output_artifact_uri == "artifact://shadow/cand_0001"
        assert result.baseline_output_artifact_uri == "artifact://shadow/base_0001"
        event_types = [e["event_type"] for e in events]
        assert "shadow.run_started" in event_types
        assert "shadow.run_completed" in event_types


# --------------------------------------------------------------------------
# Abort paths
# --------------------------------------------------------------------------


class TestShadowAborts:

    def test_unsupported_tool_aborts(self):
        runner = _make_runner(
            tool=ShadowToolView(tool_descriptor_id="tool_test",
                                  shadow_supported=False,
                                  write_risk_class="internal_memory"),
            profile=_budget_profile(),
        )
        result = runner.run(_shadow_input())
        assert result.abort_reason == ShadowAbortReason.SHADOW_UNSUPPORTED.value

    def test_operator_owned_state_handle_aborts(self):
        runner = _make_runner(
            tool=_supported_tool(),
            profile=_budget_profile(),
            operator_owned_states=("state:operator_db",),
        )
        result = runner.run(_shadow_input(
            state_handles=["state:operator_db"]
        ))
        assert result.abort_reason == \
            ShadowAbortReason.OPERATOR_STATE_WRITE_ATTEMPT.value

    def test_candidate_wrt_003_aborts(self):
        runner = _make_runner(
            tool=_supported_tool(),
            profile=_budget_profile(),
            candidate_run=_candidate_runner(write_risk="external_effect"),
        )
        result = runner.run(_shadow_input())
        assert result.abort_reason == \
            ShadowAbortReason.EXTERNAL_EFFECT_BLOCKED.value

    def test_cost_exceeded_aborts(self):
        runner = _make_runner(
            tool=_supported_tool(),
            profile=_budget_profile(cap_usd=0.01),
            candidate_run=_candidate_runner(
                cost=0.50,
                artifact_uri="artifact://shadow/cand_expensive",
            ),
        )
        result = runner.run(_shadow_input())
        assert result.abort_reason == ShadowAbortReason.COST_EXCEEDED.value
        assert result.cost_consumed == 0.50
        assert result.candidate_output_artifact_uri == \
            "artifact://shadow/cand_expensive"
        assert result.baseline_output_artifact_uri is None

    def test_timeout_exceeded_aborts(self):
        """Inject a fake clock that simulates 60s elapsed after the
        candidate runs against a 30s shadow budget."""
        ticks = iter([0.0, 60.0, 60.0])  # start, post-candidate, post-baseline
        events = []
        runner = ShadowRunner(
            fetch_tool_descriptor=lambda _tid: _supported_tool(),
            fetch_profile_config=lambda _pid: _budget_profile(budget_s=30),
            run_candidate=_candidate_runner(
                cost=0.02,
                artifact_uri="artifact://shadow/cand_timeout",
            ),
            run_baseline=_baseline_runner(),
            compare_outputs=_compare_returning(),
            emit_magma_event=_emit_collector(events),
            state_handle_is_operator_owned=lambda sref: False,
            clock_fn=lambda: next(ticks),
        )
        result = runner.run(_shadow_input())
        assert result.abort_reason == ShadowAbortReason.TIMEOUT_EXCEEDED.value
        assert result.cost_consumed == 0.02
        assert result.candidate_output_artifact_uri == \
            "artifact://shadow/cand_timeout"
        assert result.baseline_output_artifact_uri is None

    def test_baseline_nonzero_exit_aborts(self):
        events = []
        runner = _make_runner(
            tool=_supported_tool(),
            profile=_budget_profile(),
            candidate_run=_candidate_runner(
                cost=0.03,
                artifact_uri="artifact://shadow/cand_before_baseline_fail",
            ),
            baseline_run=_baseline_runner(
                exit_code=2,
                artifact_uri="artifact://shadow/base_failed",
            ),
            compare=_compare_returning(score=0.0),
            events=events,
        )
        result = runner.run(_shadow_input())
        assert result.abort_reason == ShadowAbortReason.BASELINE_FAILED.value
        assert result.divergence_score == 1.0
        assert result.cost_consumed == 0.03
        assert result.candidate_output_artifact_uri == \
            "artifact://shadow/cand_before_baseline_fail"
        assert result.baseline_output_artifact_uri == \
            "artifact://shadow/base_failed"
        event_types = [e["event_type"] for e in events]
        assert ShadowAbortReason.BASELINE_FAILED.value in event_types
        assert "shadow.run_completed" not in event_types

    def test_abort_records_audit_event(self):
        events = []
        runner = _make_runner(
            tool=_supported_tool(),
            profile=_budget_profile(),
            candidate_run=_candidate_runner(write_risk="external_effect"),
            events=events,
        )
        runner.run(_shadow_input())
        event_types = [e["event_type"] for e in events]
        assert "shadow.run_started" in event_types
        assert ShadowAbortReason.EXTERNAL_EFFECT_BLOCKED.value in event_types
        # shadow.run_completed must NOT be emitted on abort
        assert "shadow.run_completed" not in event_types


# --------------------------------------------------------------------------
# Divergence integration
# --------------------------------------------------------------------------


class TestDivergenceIntegration:

    def test_divergence_score_propagated_to_result(self):
        runner = _make_runner(
            tool=_supported_tool(),
            profile=_budget_profile(),
            compare=_compare_returning(score=0.42,
                                         delta_ref="artifact://delta/x"),
        )
        result = runner.run(_shadow_input())
        assert result.divergence_score == 0.42
        assert result.divergence_summary_ref == "artifact://delta/x"
