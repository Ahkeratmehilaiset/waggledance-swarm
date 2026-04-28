# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Repair forge — control-plane glue around the Phase 9 repair forge."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from waggledance.core.builder_lane.builder_request_pack import BuilderRequest
from waggledance.core.builder_lane.repair_forge import (
    REPAIR_TASK_KINDS,
    RepairContext,
    make_repair_request,
)
from waggledance.core.storage import ControlPlaneDB, ProviderJobRecord


class RepairForge:
    def __init__(
        self,
        *,
        control_plane: Optional[ControlPlaneDB] = None,
        section: Optional[str] = None,
    ) -> None:
        self._cp = control_plane
        self._section = section

    @staticmethod
    def supported_task_kinds() -> tuple[str, ...]:
        return REPAIR_TASK_KINDS

    def make_request(
        self,
        context: RepairContext,
        *,
        task_kind: str,
        isolated_worktree_path: str,
        isolated_branch_name: str,
        capsule_context: str = "neutral_v1",
        max_invocations: int = 1,
        max_wall_seconds: int = 600,
        branch_name: str = "phase10/foundation-truth-builder-lane",
        base_commit_hash: str = "",
        pinned_input_manifest_sha256: str = "sha256:unknown",
    ) -> BuilderRequest:
        return make_repair_request(
            context=context,
            task_kind=task_kind,
            isolated_worktree_path=isolated_worktree_path,
            isolated_branch_name=isolated_branch_name,
            capsule_context=capsule_context,
            max_invocations=max_invocations,
            max_wall_seconds=max_wall_seconds,
            branch_name=branch_name,
            base_commit_hash=base_commit_hash,
            pinned_input_manifest_sha256=pinned_input_manifest_sha256,
        )

    def record_repair_request(
        self,
        context: RepairContext,
        *,
        task_kind: str,
    ) -> Optional[ProviderJobRecord]:
        if self._cp is None:
            return None
        if task_kind not in REPAIR_TASK_KINDS:
            raise ValueError(
                f"task_kind {task_kind!r} not in REPAIR_TASK_KINDS={REPAIR_TASK_KINDS}"
            )
        return self._cp.record_provider_job(
            provider="claude_code_builder_lane",
            request_kind=f"repair:{task_kind}",
            request_hash=None,
            status="queued",
            section=self._section,
            purpose=(
                f"repair {context.defect_kind} in {context.affected_file}; "
                f"failing tests: {','.join(context.failing_test_paths) or '(none)'}"
            )[:512],
        )
