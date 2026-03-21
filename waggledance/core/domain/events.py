"""Domain event types and event model."""

from dataclasses import dataclass
from enum import Enum


class EventType(Enum):
    """All domain event types in the system."""

    TASK_RECEIVED = "task_received"
    ROUTE_SELECTED = "route_selected"
    AGENT_STARTED = "agent_started"
    AGENT_COMPLETED = "agent_completed"
    ROUND_TABLE_STARTED = "round_table_started"
    ROUND_TABLE_COMPLETED = "round_table_completed"
    MEMORY_STORED = "memory_stored"
    MEMORY_RETRIEVED = "memory_retrieved"
    NIGHT_MODE_STARTED = "night_mode_started"
    NIGHT_MODE_TICK = "night_mode_tick"
    NIGHT_STALL_DETECTED = "night_stall_detected"
    CORRECTION_RECORDED = "correction_recorded"
    CIRCUIT_OPEN = "circuit_open"
    CIRCUIT_CLOSED = "circuit_closed"
    TASK_ERROR = "task_error"
    APP_SHUTDOWN = "app_shutdown"

    # Autonomy lifecycle events (Phase 2)
    GOAL_PROPOSED = "goal_proposed"
    GOAL_ACCEPTED = "goal_accepted"
    GOAL_FAILED = "goal_failed"
    PLAN_CREATED = "plan_created"
    PLAN_STEP_COMPLETED = "plan_step_completed"
    ACTION_REQUESTED = "action_requested"
    ACTION_EXECUTED = "action_executed"
    ACTION_DENIED = "action_denied"
    ACTION_ROLLED_BACK = "action_rolled_back"
    POLICY_CHECK = "policy_check"
    VERIFICATION_PASSED = "verification_passed"
    VERIFICATION_FAILED = "verification_failed"
    CASE_RECORDED = "case_recorded"
    CAPABILITY_SELECTED = "capability_selected"
    WORLD_SNAPSHOT = "world_snapshot"
    SPECIALIST_CANARY_START = "specialist_canary_start"
    SPECIALIST_PROMOTED = "specialist_promoted"
    SPECIALIST_ROLLED_BACK = "specialist_rolled_back"


@dataclass
class DomainEvent:
    """Immutable domain event."""

    type: EventType
    payload: dict
    timestamp: float
    source: str
