# SPDX-License-Identifier: BUSL-1.1
"""Mentor forge — Phase 9 §U2.

Specialization wrapper: builds BuilderRequests for mentor_note tasks.
Per Prompt_1_Master §U2 MENTOR FORGE OUTPUT BOUNDARY, mentor notes
are advisory-only IR objects (lifecycle_status: advisory). They
CANNOT trigger architectural or runtime changes by themselves; they
become meta-proposals only via Session D evaluation with full
multi-plane evidence.
"""
from __future__ import annotations

from dataclasses import dataclass

from .builder_request_pack import BuilderRequest, make_request


@dataclass(frozen=True)
class MentorPrompt:
    topic: str
    why_relevant: str
    related_evidence_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "why_relevant": self.why_relevant,
            "related_evidence_refs": list(self.related_evidence_refs),
        }


def make_mentor_request(*,
                              prompt: MentorPrompt,
                              isolated_worktree_path: str,
                              isolated_branch_name: str,
                              capsule_context: str = "neutral_v1",
                              max_wall_seconds: int = 600,
                              branch_name: str = "phase9/autonomy-fabric",
                              base_commit_hash: str = "",
                              pinned_input_manifest_sha256: str = "sha256:unknown",
                              ) -> BuilderRequest:
    intent = (
        f"Mentor note (advisory only) on: {prompt.topic}. "
        f"Why relevant: {prompt.why_relevant}"
    )
    return make_request(
        task_kind="mentor_note",
        intent=intent,
        isolated_worktree_path=isolated_worktree_path,
        isolated_branch_name=isolated_branch_name,
        capsule_context=capsule_context,
        input_payload=prompt.to_dict(),
        max_invocations=1,
        max_wall_seconds=max_wall_seconds,
        branch_name=branch_name,
        base_commit_hash=base_commit_hash,
        pinned_input_manifest_sha256=pinned_input_manifest_sha256,
    )


# ── Mentor → IR helper ────────────────────────────────────────────

def mentor_note_to_ir_payload(*,
                                       topic: str,
                                       content: str,
                                       evidence_refs: tuple[str, ...] = (),
                                       ) -> dict:
    """Build the payload for an IR object of type
    `learning_suggestion` with `lifecycle_status='advisory'`. Per
    Prompt_1_Master §U2 these CANNOT trigger architectural change
    on their own — Session D may consume them as one evidence plane
    among several."""
    return {
        "ir_type": "learning_suggestion",
        "lifecycle_status": "advisory",
        "promotion_state": "supportive",
        "payload": {
            "about_topic": topic,
            "content": content,
            "is_advisory_only": True,
        },
        "evidence_refs": list(evidence_refs),
    }
