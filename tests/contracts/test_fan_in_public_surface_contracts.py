# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import dataclasses
import inspect

from waggledance.adapters.http import deps
from waggledance.core.bridge_llm import types as bridge_types
from waggledance.core.domain import agent, autonomy, events
from waggledance.core.storage import control_plane


def _field_names(cls: type) -> tuple[str, ...]:
    return tuple(field.name for field in dataclasses.fields(cls))


def _assert_slotted(cls: type) -> None:
    assert hasattr(cls, "__slots__"), f"{cls.__name__} must stay slotted"
    instance = cls(**{
        field.name: _sample_value_for_field(field)
        for field in dataclasses.fields(cls)
    })
    assert not hasattr(instance, "__dict__"), (
        f"{cls.__name__} instances must not grow a per-instance __dict__"
    )


def _sample_value_for_field(field: dataclasses.Field) -> object:
    if field.default is not dataclasses.MISSING:
        return field.default
    if field.default_factory is not dataclasses.MISSING:  # type: ignore[attr-defined]
        return field.default_factory()  # type: ignore[misc]
    if field.type in (int, "int"):
        return 1
    if field.type in (float, "float"):
        return 1.0
    if field.type in (bool, "bool"):
        return False
    return "sample"


def test_autonomy_domain_core_dataclass_contracts() -> None:
    expected = {
        autonomy.Goal: (
            "goal_id", "type", "description", "priority", "status", "source", "profile",
            "parent_goal_id", "carry_forward", "promise_to_user", "blocked_reason",
            "resume_after", "active_motive_id", "motive_valence", "created_at", "updated_at",
        ),
        autonomy.WorldSnapshot: (
            "snapshot_id", "timestamp", "entities", "baselines", "residuals", "profile",
            "source_type",
        ),
        autonomy.RiskScore: (
            "severity", "reversibility", "observability", "uncertainty", "blast_radius",
            "approval_required",
        ),
        autonomy.CapabilityContract: (
            "capability_id", "category", "description", "preconditions", "success_criteria",
            "rollback_possible", "max_latency_ms", "trust_score",
        ),
        autonomy.Action: (
            "action_id", "capability_id", "goal_id", "payload", "risk_score", "status",
            "result", "error", "idempotency_key", "created_at", "executed_at",
        ),
        autonomy.CaseTrajectory: (
            "trajectory_id", "goal", "world_snapshot_before", "selected_capabilities",
            "actions", "world_snapshot_after", "verifier_result", "quality_grade",
            "canonical_id", "profile", "counterfactual_alternatives", "trajectory_origin",
            "synthetic", "created_at",
        ),
    }

    for cls, fields in expected.items():
        assert _field_names(cls) == fields


def test_l51_high_frequency_dataclasses_stay_slotted() -> None:
    """PR #288 made the L51 high-frequency records slotted for memory use.

    Field-shape tests alone do not catch a future refactor that drops
    ``slots=True`` while keeping the same public fields.
    """
    slotted_classes = (
        autonomy.Goal,
        autonomy.WorldSnapshot,
        autonomy.RiskScore,
        autonomy.CapabilityContract,
        autonomy.Action,
        autonomy.CaseTrajectory,
        agent.AgentDefinition,
        agent.AgentResult,
        events.DomainEvent,
        bridge_types.CallBudget,
        bridge_types.LLMRequest,
        bridge_types.LLMResponse,
        control_plane.SolverRecord,
        control_plane.CapabilityRecord,
        control_plane.RuntimeGapSignalRecord,
        control_plane.GrowthIntentRecord,
        control_plane.AutogrowthQueueRecord,
        control_plane.AutonomyKPISnapshot,
    )
    for cls in slotted_classes:
        _assert_slotted(cls)


def test_autonomy_domain_enum_contracts() -> None:
    assert tuple(item.value for item in autonomy.GoalType) == (
        "observe", "diagnose", "optimize", "protect", "plan", "act", "verify", "learn",
        "maintain",
    )
    assert tuple(item.value for item in autonomy.GoalStatus) == (
        "proposed", "accepted", "planned", "executing", "verified", "failed",
        "rolled_back", "archived",
    )
    assert tuple(item.value for item in autonomy.SourceType) == (
        "observed", "inferred_by_solver", "inferred_by_stats", "inferred_by_rule",
        "proposed_by_llm", "confirmed_by_verifier", "learned_from_case",
        "self_reflection", "simulated",
    )
    assert tuple(item.value for item in autonomy.CapabilityCategory) == (
        "sense", "normalize", "estimate", "solve", "detect", "predict", "optimize",
        "plan", "verify", "explain", "act", "learn", "retrieve",
    )


def test_agent_domain_dataclass_contracts() -> None:
    assert _field_names(agent.AgentDefinition) == (
        "id", "name", "domain", "tags", "skills", "trust_level", "specialization_score",
        "active", "profile",
    )
    assert _field_names(agent.AgentResult) == (
        "agent_id", "response", "confidence", "latency_ms", "source", "metadata",
    )


def test_control_plane_high_fan_in_record_contracts() -> None:
    expected = {
        control_plane.SolverRecord: (
            "id", "family_id", "name", "version", "status", "spec_hash", "spec_path",
            "created_at", "updated_at",
        ),
        control_plane.CapabilityRecord: (
            "id", "name", "version", "description", "created_at",
        ),
        control_plane.RuntimeGapSignalRecord: (
            "id", "kind", "family_kind", "cell_coord", "signal_payload", "weight",
            "observed_at", "created_at",
        ),
        control_plane.GrowthIntentRecord: (
            "id", "family_kind", "cell_coord", "intent_key", "priority", "status",
            "signal_count", "last_signal_id", "spec_seed_json", "notes", "created_at",
            "updated_at",
        ),
        control_plane.AutogrowthQueueRecord: (
            "id", "intent_id", "priority", "status", "claimed_by", "claimed_at",
            "attempt_count", "last_error", "backoff_until", "created_at", "updated_at",
        ),
        control_plane.AutonomyKPISnapshot: (
            "id", "snapshot_at", "candidates_total", "validations_pass_total",
            "validations_fail_total", "shadows_pass_total", "shadows_fail_total",
            "auto_promotions_total", "rejections_total", "rollbacks_total",
            "dispatcher_hits_total", "dispatcher_misses_total", "per_family_counts_json",
            "created_at",
        ),
    }

    for cls, fields in expected.items():
        assert dataclasses.is_dataclass(cls)
        assert _field_names(cls) == fields


def test_bridge_llm_call_contracts() -> None:
    assert tuple(item.name for item in bridge_types.FallbackLevel) == (
        "CACHE", "LOCAL_LLM", "CLOUD_LLM", "HEURISTIC",
    )
    assert tuple(int(item) for item in bridge_types.FallbackLevel) == (1, 2, 3, 4)
    assert _field_names(bridge_types.CallBudget) == (
        "max_latency_ms", "max_cost_cents", "max_retries", "allow_cloud",
        "allow_pii_to_cloud", "require_json", "fallback_policy",
    )
    assert _field_names(bridge_types.LLMRequest) == (
        "injection_point", "prompt", "intent", "budget", "speculative", "model",
        "temperature", "max_tokens", "metadata",
    )
    assert _field_names(bridge_types.LLMResponse) == (
        "text", "fallback_level", "provider", "success", "latency_ms", "tokens_in",
        "tokens_out", "cost_cents", "error_class", "cached", "redaction_applied",
        "call_id",
    )


def test_http_dependency_provider_contracts() -> None:
    expected = (
        "get_container", "get_chat_service", "get_readiness_service", "get_memory_service",
        "get_autonomy_service", "require_auth",
    )

    for name in expected:
        provider = getattr(deps, name)
        signature = inspect.signature(provider)
        assert tuple(signature.parameters) == ("request",)
