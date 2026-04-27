# SPDX-License-Identifier: BUSL-1.1
"""Session forge — Phase 9 §U2.

Pure orchestrator: given a BuilderRequest + an allocator + a router,
emit the orchestration plan (allocation, routing, expected
worktree/branch/log paths). Does NOT invoke Claude Code; that is the
caller's job per Prompt_1_Master §U2 SUBPROCESS EXCEPTION rule.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import OUTCOME_STATES
from .builder_lane_router import BuilderRoutingDecision, route as route_request
from .builder_request_pack import BuilderRequest
from .worktree_allocator import WorktreeAllocation, allocate as allocate_worktree


@dataclass(frozen=True)
class ForgePlan:
    request: BuilderRequest
    allocation: WorktreeAllocation
    routing: BuilderRoutingDecision
    invocation_log_path: str

    def to_dict(self) -> dict:
        return {
            "request": self.request.to_dict(),
            "allocation": self.allocation.to_dict(),
            "routing": self.routing.to_dict(),
            "invocation_log_path": self.invocation_log_path,
        }


def plan(request: BuilderRequest,
            *,
            worktree_root: Path | str,
            invocation_log_path: Path | str,
            agent_pool=None) -> ForgePlan:
    """Pure: returns a ForgePlan; does NOT touch git or Claude."""
    allocation = allocate_worktree(
        request_id=request.request_id, root=worktree_root,
        base_branch=request.branch_name,
    )
    routing = route_request(request, agent_pool=agent_pool)
    return ForgePlan(
        request=request,
        allocation=allocation,
        routing=routing,
        invocation_log_path=str(invocation_log_path),
    )
