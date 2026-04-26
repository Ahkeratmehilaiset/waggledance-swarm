# SPDX-License-Identifier: BUSL-1.1
"""Builder result pack construction — Phase 9 §U2.

Pure function. Builder result records what was produced (not what
was applied). All artifacts live in the isolated worktree until a
human reviewer takes them further.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from . import ARTIFACT_KINDS, BUILDER_LANE_SCHEMA_VERSION, OUTCOME_STATES


@dataclass(frozen=True)
class BuilderArtifact:
    artifact_kind: str
    relative_path: str
    sha256_full: str

    def __post_init__(self) -> None:
        if self.artifact_kind not in ARTIFACT_KINDS:
            raise ValueError(
                f"unknown artifact_kind: {self.artifact_kind!r}; "
                f"allowed: {ARTIFACT_KINDS}"
            )

    def to_dict(self) -> dict:
        return {
            "artifact_kind": self.artifact_kind,
            "relative_path": self.relative_path,
            "sha256_full": self.sha256_full,
        }


@dataclass(frozen=True)
class BuilderResult:
    schema_version: int
    result_id: str
    request_id: str
    outcome: str
    artifacts: tuple[BuilderArtifact, ...]
    isolated_branch_name: str
    isolated_worktree_path: str
    tests_passed: int
    tests_failed: int
    tests_skipped: int
    no_main_branch_auto_merge: bool
    human_review_required: bool
    ts_iso: str

    def __post_init__(self) -> None:
        if self.outcome not in OUTCOME_STATES:
            raise ValueError(
                f"unknown outcome: {self.outcome!r}; "
                f"allowed: {OUTCOME_STATES}"
            )

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "result_id": self.result_id,
            "request_id": self.request_id,
            "outcome": self.outcome,
            "artifacts": [a.to_dict() for a in self.artifacts],
            "isolated_branch_name": self.isolated_branch_name,
            "isolated_worktree_path": self.isolated_worktree_path,
            "tests_run": {
                "passed": self.tests_passed,
                "failed": self.tests_failed,
                "skipped": self.tests_skipped,
            },
            "no_main_branch_auto_merge": self.no_main_branch_auto_merge,
            "human_review_required": self.human_review_required,
            "ts_iso": self.ts_iso,
        }


def compute_result_id(request_id: str, ts_iso: str,
                            outcome: str) -> str:
    canonical = json.dumps({
        "request_id": request_id, "ts_iso": ts_iso, "outcome": outcome,
    }, sort_keys=True, separators=(",", ":"))
    return "br_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:10]


def make_result(*,
                     request_id: str,
                     outcome: str,
                     artifacts: tuple[BuilderArtifact, ...] = (),
                     isolated_branch_name: str = "",
                     isolated_worktree_path: str = "",
                     tests_passed: int = 0,
                     tests_failed: int = 0,
                     tests_skipped: int = 0,
                     human_review_required: bool = True,
                     ts_iso: str = "",
                     ) -> BuilderResult:
    return BuilderResult(
        schema_version=BUILDER_LANE_SCHEMA_VERSION,
        result_id=compute_result_id(request_id, ts_iso, outcome),
        request_id=request_id, outcome=outcome,
        artifacts=tuple(artifacts),
        isolated_branch_name=isolated_branch_name,
        isolated_worktree_path=isolated_worktree_path,
        tests_passed=tests_passed,
        tests_failed=tests_failed,
        tests_skipped=tests_skipped,
        no_main_branch_auto_merge=True,
        human_review_required=human_review_required,
        ts_iso=ts_iso,
    )
