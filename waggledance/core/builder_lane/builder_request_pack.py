# SPDX-License-Identifier: BUSL-1.1
"""Builder request pack construction — Phase 9 §U2.

Pure function: builds a deterministic BuilderRequest. No subprocess
calls; no live LLM invocation; no runtime mutation.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from . import BUILDER_LANE_SCHEMA_VERSION, TASK_KINDS


@dataclass(frozen=True)
class BuilderRequest:
    schema_version: int
    request_id: str
    task_kind: str
    intent: str
    isolated_worktree_path: str
    isolated_branch_name: str
    capsule_context: str
    input_payload: dict
    no_runtime_mutation: bool
    no_main_branch_auto_merge: bool
    branch_name: str
    base_commit_hash: str
    pinned_input_manifest_sha256: str
    agent_id_hint: str | None = None
    max_invocations: int = 1
    max_wall_seconds: int = 300

    def to_dict(self) -> dict:
        d = {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "task_kind": self.task_kind,
            "intent": self.intent,
            "isolated_worktree_path": self.isolated_worktree_path,
            "isolated_branch_name": self.isolated_branch_name,
            "agent_id_hint": self.agent_id_hint,
            "capsule_context": self.capsule_context,
            "input_payload": dict(self.input_payload),
            "no_runtime_mutation": self.no_runtime_mutation,
            "no_main_branch_auto_merge": self.no_main_branch_auto_merge,
            "budget": {
                "max_invocations": self.max_invocations,
                "max_wall_seconds": self.max_wall_seconds,
            },
            "provenance": {
                "branch_name": self.branch_name,
                "base_commit_hash": self.base_commit_hash,
                "pinned_input_manifest_sha256":
                    self.pinned_input_manifest_sha256,
            },
        }
        return d


def compute_request_id(*, task_kind: str, intent: str,
                              capsule_context: str,
                              input_payload: dict | None = None) -> str:
    canonical = json.dumps({
        "task_kind": task_kind, "intent": intent,
        "capsule_context": capsule_context,
        "input_payload": input_payload or {},
    }, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def make_request(*,
                      task_kind: str,
                      intent: str,
                      isolated_worktree_path: str,
                      isolated_branch_name: str,
                      capsule_context: str = "neutral_v1",
                      input_payload: dict | None = None,
                      agent_id_hint: str | None = None,
                      max_invocations: int = 1,
                      max_wall_seconds: int = 300,
                      branch_name: str = "phase9/autonomy-fabric",
                      base_commit_hash: str = "",
                      pinned_input_manifest_sha256: str = "sha256:unknown",
                      ) -> BuilderRequest:
    if task_kind not in TASK_KINDS:
        raise ValueError(
            f"unknown task_kind: {task_kind!r}; allowed: {TASK_KINDS}"
        )
    if max_invocations < 1:
        raise ValueError("max_invocations must be >= 1")
    if max_wall_seconds < 1:
        raise ValueError("max_wall_seconds must be >= 1")
    rid = compute_request_id(
        task_kind=task_kind, intent=intent,
        capsule_context=capsule_context,
        input_payload=input_payload,
    )
    return BuilderRequest(
        schema_version=BUILDER_LANE_SCHEMA_VERSION,
        request_id=rid, task_kind=task_kind, intent=intent,
        isolated_worktree_path=isolated_worktree_path,
        isolated_branch_name=isolated_branch_name,
        capsule_context=capsule_context,
        input_payload=dict(input_payload or {}),
        no_runtime_mutation=True,
        no_main_branch_auto_merge=True,
        branch_name=branch_name,
        base_commit_hash=base_commit_hash,
        pinned_input_manifest_sha256=pinned_input_manifest_sha256,
        agent_id_hint=agent_id_hint,
        max_invocations=max_invocations,
        max_wall_seconds=max_wall_seconds,
    )
