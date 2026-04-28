# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Builder lane router — Phase 9 router + Phase 10 control-plane glue.

The Phase 9
:func:`waggledance.core.builder_lane.builder_lane_router.route` already
maps a :class:`BuilderRequest` to a routing decision. Phase 10 adds the
control-plane bridge: when a routing decision is made we record the
intent in the control-plane ``provider_jobs`` table so a Reality View
or status endpoint can show what the builder lane is up to.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from waggledance.core.builder_lane.builder_lane_router import (
    BuilderRoutingDecision,
    route as phase9_route,
)
from waggledance.core.builder_lane.builder_request_pack import BuilderRequest
from waggledance.core.storage import ControlPlaneDB, ProviderJobRecord


@dataclass(frozen=True)
class RouteWithJob:
    decision: BuilderRoutingDecision
    job: Optional[ProviderJobRecord]


class BuilderLaneRouter:
    def __init__(
        self,
        *,
        control_plane: Optional[ControlPlaneDB] = None,
        section: Optional[str] = None,
    ) -> None:
        self._cp = control_plane
        self._section = section

    def route(
        self,
        request: BuilderRequest,
        *,
        agent_pool=None,
    ) -> RouteWithJob:
        decision = phase9_route(request, agent_pool=agent_pool)
        job: Optional[ProviderJobRecord] = None
        if self._cp is not None:
            job = self._cp.record_provider_job(
                provider=decision.chosen_provider_type,
                request_kind=f"builder_lane:{request.task_kind}",
                request_hash=None,
                status="queued",
                section=self._section,
                purpose=f"intent={request.intent[:200]}",
            )
        return RouteWithJob(decision=decision, job=job)
