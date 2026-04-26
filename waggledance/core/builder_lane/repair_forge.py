"""Repair forge — Phase 9 §U2.

Specialization wrapper: builds BuilderRequests for repair-class tasks
(repair_adapter, patch_schema, fix_replay_defect). Pure constructor;
no subprocess calls.
"""
from __future__ import annotations

from dataclasses import dataclass

from .builder_request_pack import BuilderRequest, make_request


REPAIR_TASK_KINDS = (
    "repair_adapter",
    "patch_schema",
    "fix_replay_defect",
)


@dataclass(frozen=True)
class RepairContext:
    affected_file: str
    defect_kind: str
    failing_test_paths: tuple[str, ...] = ()
    rationale: str = ""

    def to_dict(self) -> dict:
        return {
            "affected_file": self.affected_file,
            "defect_kind": self.defect_kind,
            "failing_test_paths": list(self.failing_test_paths),
            "rationale": self.rationale,
        }


def make_repair_request(*,
                              context: RepairContext,
                              task_kind: str,
                              isolated_worktree_path: str,
                              isolated_branch_name: str,
                              capsule_context: str = "neutral_v1",
                              max_invocations: int = 1,
                              max_wall_seconds: int = 600,
                              branch_name: str = "phase9/autonomy-fabric",
                              base_commit_hash: str = "",
                              pinned_input_manifest_sha256: str = "sha256:unknown",
                              ) -> BuilderRequest:
    if task_kind not in REPAIR_TASK_KINDS:
        raise ValueError(
            f"task_kind {task_kind!r} is not a repair task; "
            f"allowed: {REPAIR_TASK_KINDS}"
        )
    intent = (
        f"Repair {context.defect_kind} in {context.affected_file}; "
        f"failing tests: {','.join(context.failing_test_paths) or '(none specified)'}"
    )
    return make_request(
        task_kind=task_kind,
        intent=intent,
        isolated_worktree_path=isolated_worktree_path,
        isolated_branch_name=isolated_branch_name,
        capsule_context=capsule_context,
        input_payload=context.to_dict(),
        max_invocations=max_invocations,
        max_wall_seconds=max_wall_seconds,
        branch_name=branch_name,
        base_commit_hash=base_commit_hash,
        pinned_input_manifest_sha256=pinned_input_manifest_sha256,
    )
