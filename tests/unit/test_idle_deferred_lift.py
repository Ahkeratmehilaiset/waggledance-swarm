# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from tools import agent_next_task, idle_loop_once
from waggledance.core.idle_deferred_lift import deferred_lift_state


def test_deferred_lift_state_shape_and_authority_are_read_only() -> None:
    state = deferred_lift_state()

    assert state["source"] == "docs/architecture/IDLE_PROTOCOL_V1.md#deferred"
    assert state["authority"] == {
        "read_only_report": True,
        "emits_bridge_events": False,
        "claims_work": False,
        "creates_tasks": False,
        "creates_branches": False,
        "creates_pull_requests": False,
        "merges": False,
        "skips_gates": False,
    }
    assert state["items"]["production_two_agent_activation_loop"]["state"] == (
        "partial_read_only_ready"
    )
    assert state["items"]["automatic_payload_generation"]["state"] == "deferred"
    assert state["items"]["auto_conversion_consensus_to_implementation_work"][
        "state"
    ] == "report_only_partial"


def test_deferred_lift_state_returns_deep_copy() -> None:
    state = deferred_lift_state()
    state["items"]["automatic_payload_generation"]["state"] = "mutated"

    fresh = deferred_lift_state()

    assert fresh["items"]["automatic_payload_generation"]["state"] == "deferred"


def test_tools_reexport_shared_deferred_lift_state() -> None:
    assert idle_loop_once.deferred_lift_state is deferred_lift_state
    assert agent_next_task.deferred_lift_state is deferred_lift_state
