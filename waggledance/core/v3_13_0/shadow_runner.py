# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""ShadowRunner v1 -- run a candidate solver alongside operator baseline.

Phase 1 of Shadow -> Hybrid -> Autonomous migration. Executes a
generated solver candidate against the same input the operator's
existing script consumed, producing two output artifacts that
DivergenceAnalyzer can score.

Sandbox guarantees (refused at construction or at run-time):
* No WRT-003 writes (any attempt aborts with
  shadow.external_effect_blocked).
* No mutation of operator state -- all writes must land in tempfile
  StateHandle scopes.
* Time-boxed via LatencyBudget.shadow_eval_budget_s.
* Cost-bounded via per_shadow_run_cap from ProfileConfig.shadow_overrides.

Design spec:
iterations/anchor_use_case/sprint_1/claude_lane/shadow_runner_scaffold_spec.md
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional


# --------------------------------------------------------------------------
# Run state + abort reasons
# --------------------------------------------------------------------------


class ShadowRunState(str, Enum):
    LOADING = "loading"
    BINDING_INPUTS = "binding_inputs"
    RUNNING_CANDIDATE = "running_candidate"
    RUNNING_BASELINE = "running_baseline"
    COMPARING_OUTPUTS = "comparing_outputs"
    RECORDING = "recording"
    DONE = "done"
    ABORTED = "aborted"


class ShadowAbortReason(str, Enum):
    EXTERNAL_EFFECT_BLOCKED = "shadow.external_effect_blocked"
    OPERATOR_STATE_WRITE_ATTEMPT = "shadow.operator_state_write_attempt"
    TIMEOUT_EXCEEDED = "shadow.timeout_exceeded"
    COST_EXCEEDED = "shadow.cost_exceeded"
    BASELINE_FAILED = "shadow.baseline_failed"
    SHADOW_UNSUPPORTED = "shadow.tool_not_shadow_supported"


# --------------------------------------------------------------------------
# Inputs / outputs / scores
# --------------------------------------------------------------------------


@dataclass
class ShadowRunInput:
    """One shadow run request."""

    candidate_manifest_id: str
    shadow_input_set_ref: str
    profile_config_ref: str
    tool_descriptor_id: str
    state_handles: list[str]
    operator_baseline_command: list[str]    # args=list form, no shell
    expected_output_format: str             # "json" | "csv" | "sql_diff"
                                             # | "filesystem"
    shadow_run_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class CandidateOutput:
    """Output of running the candidate solver."""

    artifact_uri: str
    elapsed_ms: int
    cost_consumed: float = 0.0
    write_risk_observed: str = "internal_memory"   # WRT-001 / 002 / 003


@dataclass
class BaselineOutput:
    """Output of running the operator's existing script."""

    artifact_uri: str
    elapsed_ms: int
    exit_code: int


@dataclass
class DivergenceScore:
    """Output of DivergenceAnalyzer.compare()."""

    score: float                            # 0.0 identical .. 1.0 max
    delta_summary_ref: str                  # artifact URI for full delta
    per_step_scores: list[float] = field(default_factory=list)


@dataclass
class ShadowRunResult:
    """Outcome of one shadow run."""

    shadow_run_id: str
    candidate_output_artifact_uri: Optional[str]
    baseline_output_artifact_uri: Optional[str]
    divergence_score: float
    divergence_summary_ref: Optional[str]
    elapsed_ms: int
    cost_consumed: float
    abort_reason: Optional[str]             # ShadowAbortReason value
    audit_event_ids: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# ToolDescriptor view -- the subset ShadowRunner consults
# --------------------------------------------------------------------------


@dataclass
class ShadowToolView:
    """The subset of SCH-001 ToolDescriptor ShadowRunner reads."""

    tool_descriptor_id: str
    shadow_supported: bool
    write_risk_class: str                   # informational / internal_memory
                                             # / local_artifact / external_effect


@dataclass
class ShadowProfileView:
    """The subset of SCH-009 ProfileConfig ShadowRunner reads."""

    profile_id: str
    shadow_eval_budget_s: int               # LatencyBudget.shadow_eval_budget_s
    per_shadow_run_cap_usd: float
    divergence_thresholds: dict[str, float] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Exceptions
# --------------------------------------------------------------------------


class ShadowAborted(Exception):
    """Raised internally to short-circuit a run; converted to
    ShadowRunResult with abort_reason set."""

    def __init__(self, reason: ShadowAbortReason, detail: str = ""):
        super().__init__(f"{reason.value}: {detail}")
        self.reason = reason
        self.detail = detail


# --------------------------------------------------------------------------
# ShadowRunner itself
# --------------------------------------------------------------------------


@dataclass
class ShadowRunner:
    """Run candidate alongside operator baseline; score divergence.

    Pluggable hooks; v1 wires mock implementations in tests.
    """

    # --- hooks the caller injects --------------------------------------------

    fetch_tool_descriptor: Callable[[str], Optional[ShadowToolView]]
    fetch_profile_config: Callable[[str], Optional[ShadowProfileView]]

    run_candidate: Callable[[ShadowRunInput], CandidateOutput]
    """Run the candidate solver in a sandbox. May raise ShadowAborted."""

    run_baseline: Callable[[ShadowRunInput], BaselineOutput]
    """Run the operator's existing script. args=list, shell=False
    enforced by caller (spec edit E4)."""

    compare_outputs: Callable[[CandidateOutput, BaselineOutput, str],
                                DivergenceScore]
    """DivergenceAnalyzer.compare() pluggable hook."""

    emit_magma_event: Callable[[dict], str]

    state_handle_is_operator_owned: Callable[[str], bool]
    """Return True if a StateHandle ref points at operator-mutating
    storage (not a tempfile). Used to refuse binding."""

    clock_fn: Callable[[], float] = time.perf_counter
    """Monotonic clock for wall-time budget enforcement. Tests inject
    a deterministic fake so timeout assertions are not flaky on
    fast platforms where perf_counter delta can read 0."""

    # --- gate config ---------------------------------------------------------

    default_baseline_sensitive_class: str = "restricted"

    # =========================================================================
    # PUBLIC API
    # =========================================================================

    def run(self, run_input: ShadowRunInput) -> ShadowRunResult:
        """Execute one shadow run.

        Returns a ShadowRunResult; on abort, abort_reason is populated
        and the divergence_score field is set to 1.0 (max divergence,
        treat as fail).
        """
        audit_ids: list[str] = []
        audit_ids.append(self._audit("shadow.run_started", run_input, {}))
        t_start = self.clock_fn()
        candidate_out: Optional[CandidateOutput] = None
        baseline_out: Optional[BaselineOutput] = None

        try:
            # State: LOADING -- resolve tool + profile views
            tool = self.fetch_tool_descriptor(run_input.tool_descriptor_id)
            if tool is None:
                raise ShadowAborted(
                    ShadowAbortReason.SHADOW_UNSUPPORTED,
                    f"unknown tool {run_input.tool_descriptor_id}",
                )
            if not tool.shadow_supported:
                raise ShadowAborted(
                    ShadowAbortReason.SHADOW_UNSUPPORTED,
                    f"tool {tool.tool_descriptor_id} shadow_supported=False",
                )

            profile = self.fetch_profile_config(run_input.profile_config_ref)
            if profile is None:
                raise ShadowAborted(
                    ShadowAbortReason.SHADOW_UNSUPPORTED,
                    f"unknown profile {run_input.profile_config_ref}",
                )

            # State: BINDING_INPUTS -- refuse operator-owned state handles
            for sref in run_input.state_handles:
                if self.state_handle_is_operator_owned(sref):
                    raise ShadowAborted(
                        ShadowAbortReason.OPERATOR_STATE_WRITE_ATTEMPT,
                        f"state_handle {sref} is operator-owned; "
                        "shadow refuses to bind",
                    )

            # State: RUNNING_CANDIDATE
            candidate_out = self.run_candidate(run_input)

            # Enforce WRT-003 refusal in shadow
            if candidate_out.write_risk_observed == "external_effect":
                raise ShadowAborted(
                    ShadowAbortReason.EXTERNAL_EFFECT_BLOCKED,
                    "candidate attempted WRT-003 external_effect write",
                )

            # Cost enforcement
            if candidate_out.cost_consumed > profile.per_shadow_run_cap_usd:
                raise ShadowAborted(
                    ShadowAbortReason.COST_EXCEEDED,
                    f"cost {candidate_out.cost_consumed} > cap "
                    f"{profile.per_shadow_run_cap_usd}",
                )

            # Time-box check (candidate stage)
            elapsed_so_far_ms = int((self.clock_fn() - t_start) * 1000)
            if elapsed_so_far_ms > profile.shadow_eval_budget_s * 1000:
                raise ShadowAborted(
                    ShadowAbortReason.TIMEOUT_EXCEEDED,
                    f"elapsed {elapsed_so_far_ms} ms > budget "
                    f"{profile.shadow_eval_budget_s * 1000} ms",
                )

            # State: RUNNING_BASELINE
            baseline_out = self.run_baseline(run_input)
            if baseline_out.exit_code != 0:
                raise ShadowAborted(
                    ShadowAbortReason.BASELINE_FAILED,
                    f"baseline command exited with code {baseline_out.exit_code}",
                )

            # Time-box check after baseline
            elapsed_so_far_ms = int((self.clock_fn() - t_start) * 1000)
            if elapsed_so_far_ms > profile.shadow_eval_budget_s * 1000:
                raise ShadowAborted(
                    ShadowAbortReason.TIMEOUT_EXCEEDED,
                    f"elapsed {elapsed_so_far_ms} ms > budget after baseline",
                )

            # State: COMPARING_OUTPUTS
            score = self.compare_outputs(
                candidate_out, baseline_out, run_input.expected_output_format
            )

            # State: RECORDING + DONE
            elapsed_ms = int((self.clock_fn() - t_start) * 1000)
            audit_ids.append(self._audit("shadow.run_completed", run_input, {
                "divergence_score": score.score,
                "elapsed_ms": elapsed_ms,
                "cost_consumed": candidate_out.cost_consumed,
            }))
            return ShadowRunResult(
                shadow_run_id=run_input.shadow_run_id,
                candidate_output_artifact_uri=candidate_out.artifact_uri,
                baseline_output_artifact_uri=baseline_out.artifact_uri,
                divergence_score=score.score,
                divergence_summary_ref=score.delta_summary_ref,
                elapsed_ms=elapsed_ms,
                cost_consumed=candidate_out.cost_consumed,
                abort_reason=None,
                audit_event_ids=audit_ids,
            )

        except ShadowAborted as exc:
            elapsed_ms = int((self.clock_fn() - t_start) * 1000)
            cost_consumed = (
                candidate_out.cost_consumed if candidate_out is not None else 0.0
            )
            audit_ids.append(self._audit(exc.reason.value, run_input, {
                "detail": exc.detail,
                "elapsed_ms": elapsed_ms,
            }))
            return ShadowRunResult(
                shadow_run_id=run_input.shadow_run_id,
                candidate_output_artifact_uri=(
                    candidate_out.artifact_uri if candidate_out is not None else None
                ),
                baseline_output_artifact_uri=(
                    baseline_out.artifact_uri if baseline_out is not None else None
                ),
                divergence_score=1.0,        # max -- treat as fail
                divergence_summary_ref=None,
                elapsed_ms=elapsed_ms,
                cost_consumed=cost_consumed,
                abort_reason=exc.reason.value,
                audit_event_ids=audit_ids,
            )

    # =========================================================================
    # INTERNAL HELPERS
    # =========================================================================

    def _audit(self, event_type: str, run_input: ShadowRunInput,
                extra: dict) -> str:
        envelope = {
            "event_type": event_type,
            "shadow_run_id": run_input.shadow_run_id,
            "candidate_manifest_id": run_input.candidate_manifest_id,
            "tool_descriptor_id": run_input.tool_descriptor_id,
            "profile_config_ref": run_input.profile_config_ref,
            "ts_utc": _utc_iso(),
            **extra,
        }
        return self.emit_magma_event(envelope)


# --------------------------------------------------------------------------
# Utility
# --------------------------------------------------------------------------


def _utc_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


__all__ = [
    "ShadowRunState",
    "ShadowAbortReason",
    "ShadowRunInput",
    "CandidateOutput",
    "BaselineOutput",
    "DivergenceScore",
    "ShadowRunResult",
    "ShadowToolView",
    "ShadowProfileView",
    "ShadowAborted",
    "ShadowRunner",
]
