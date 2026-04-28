# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Mentor forge — advisory boundary + IR translation.

Phase 9
:mod:`waggledance.core.builder_lane.mentor_forge` already builds the
:class:`BuilderRequest` for mentor-note tasks and ships
:func:`mentor_note_to_ir_payload` that produces an IR object of type
``learning_suggestion`` with ``lifecycle_status='advisory'``.

Phase 10's :class:`MentorForge` is the orchestration façade: it wraps
the Phase 9 helpers, persists the mentor request as a
``provider_jobs`` row in the control plane, and enforces the
advisory-output contract at the API surface.

A mentor note **cannot** mutate runtime or architecture by itself.
:meth:`MentorForge.compile_advisory_payload` always returns an IR
object with ``is_advisory_only=True``. Callers wishing to promote a
mentor note to a proposal must route it through the existing
reviewed pipeline (Session D / proposal compiler).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from waggledance.core.builder_lane.mentor_forge import (
    MentorPrompt,
    make_mentor_request,
    mentor_note_to_ir_payload,
)
from waggledance.core.builder_lane.builder_request_pack import BuilderRequest
from waggledance.core.storage import ControlPlaneDB, ProviderJobRecord


@dataclass(frozen=True)
class MentorAdvisoryPayload:
    ir_type: str
    lifecycle_status: str
    promotion_state: str
    is_advisory_only: bool
    payload: dict
    evidence_refs: tuple[str, ...]

    def to_ir_payload(self) -> dict:
        return {
            "ir_type": self.ir_type,
            "lifecycle_status": self.lifecycle_status,
            "promotion_state": self.promotion_state,
            "payload": {**self.payload, "is_advisory_only": self.is_advisory_only},
            "evidence_refs": list(self.evidence_refs),
        }


class MentorForge:
    """Façade that enforces the mentor-output advisory boundary."""

    def __init__(
        self,
        *,
        control_plane: Optional[ControlPlaneDB] = None,
        section: Optional[str] = None,
    ) -> None:
        self._cp = control_plane
        self._section = section

    def make_request(
        self,
        prompt: MentorPrompt,
        *,
        isolated_worktree_path: str,
        isolated_branch_name: str,
        capsule_context: str = "neutral_v1",
        max_wall_seconds: int = 600,
        branch_name: str = "phase10/foundation-truth-builder-lane",
        base_commit_hash: str = "",
        pinned_input_manifest_sha256: str = "sha256:unknown",
    ) -> BuilderRequest:
        return make_mentor_request(
            prompt=prompt,
            isolated_worktree_path=isolated_worktree_path,
            isolated_branch_name=isolated_branch_name,
            capsule_context=capsule_context,
            max_wall_seconds=max_wall_seconds,
            branch_name=branch_name,
            base_commit_hash=base_commit_hash,
            pinned_input_manifest_sha256=pinned_input_manifest_sha256,
        )

    def compile_advisory_payload(
        self,
        *,
        topic: str,
        content: str,
        evidence_refs: Sequence[str] = (),
    ) -> MentorAdvisoryPayload:
        raw = mentor_note_to_ir_payload(
            topic=topic,
            content=content,
            evidence_refs=tuple(evidence_refs),
        )
        # Belt-and-braces: the Phase 9 helper already sets these, but
        # we double-enforce so callers using only Phase 10's surface
        # cannot accidentally produce a non-advisory mentor note.
        return MentorAdvisoryPayload(
            ir_type=str(raw["ir_type"]),
            lifecycle_status="advisory",
            promotion_state="supportive",
            is_advisory_only=True,
            payload=dict(raw["payload"]),
            evidence_refs=tuple(raw.get("evidence_refs", ())),
        )

    def record_advisory_request(
        self,
        prompt: MentorPrompt,
    ) -> Optional[ProviderJobRecord]:
        if self._cp is None:
            return None
        return self._cp.record_provider_job(
            provider="claude_code_builder_lane",
            request_kind="mentor_note",
            request_hash=None,
            status="queued",
            section=self._section,
            purpose=f"mentor topic: {prompt.topic[:200]}",
        )
