# SPDX-License-Identifier: BUSL-1.1
"""Shared read-only status for deferred idle-protocol lift items."""
from __future__ import annotations

import copy
from typing import Any


DEFERRED_LIFT_STATE: dict[str, Any] = {
    "source": "docs/architecture/IDLE_PROTOCOL_V1.md#deferred",
    "authority": {
        "read_only_report": True,
        "emits_bridge_events": False,
        "claims_work": False,
        "creates_tasks": False,
        "creates_branches": False,
        "creates_pull_requests": False,
        "merges": False,
        "skips_gates": False,
    },
    "items": {
        "production_two_agent_activation_loop": {
            "state": "partial_read_only_ready",
            "implemented_by": [
                "tools/idle_loop_once.py",
                "tools/agent_next_task.py",
                "docs/architecture/IDLE_LOOP_RUNBOOK.md",
            ],
            "safe_next": (
                "Expose readiness and scheduler recommendations only; any "
                "bridge write stays in an explicit caller-owned claim/release "
                "step."
            ),
        },
        "automatic_payload_generation": {
            "state": "deferred",
            "implemented_by": [],
            "safe_next": (
                "Keep idle-protocol payload composition outside tooling until "
                "payload templates and quality gates are peer reviewed."
            ),
        },
        "auto_conversion_consensus_to_implementation_work": {
            "state": "report_only_partial",
            "implemented_by": [
                "tools/idle_consensus_artifact.py",
                "tools/idle_consensus_to_pr.py",
                "tools/idle_consensus_draft_pr.py",
            ],
            "safe_next": (
                "Report candidate-diff readiness only; implementer agents "
                "still create scoped diffs and PRs separately."
            ),
        },
    },
}


def deferred_lift_state() -> dict[str, Any]:
    """Return a mutable copy of the deferred idle-protocol lift status."""
    return copy.deepcopy(DEFERRED_LIFT_STATE)
