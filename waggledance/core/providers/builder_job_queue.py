# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Builder job queue — thin queue over control-plane ``builder_jobs``.

The queue lets the orchestrator submit builder requests asynchronously
and observe their lifecycle. The actual subprocess work is performed
by :class:`waggledance.core.providers.claude_code_builder.ClaudeCodeBuilder`;
the queue only persists state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from waggledance.core.storage import (
    BuilderJobRecord,
    ControlPlaneDB,
    ControlPlaneError,
)


@dataclass(frozen=True)
class BuilderJobSubmission:
    worktree_path: str
    branch: str
    invocation_log_path: Optional[str] = None
    parent_provider_job_id: Optional[int] = None


class BuilderJobQueue:
    """Persistent queue over control-plane ``builder_jobs``."""

    def __init__(self, control_plane: ControlPlaneDB) -> None:
        self._cp = control_plane

    def submit(self, submission: BuilderJobSubmission) -> BuilderJobRecord:
        return self._cp.record_builder_job(
            worktree_path=submission.worktree_path,
            branch=submission.branch,
            invocation_log_path=submission.invocation_log_path,
            parent_provider_job_id=submission.parent_provider_job_id,
            status="queued",
        )

    def list_queued(self, limit: int = 50) -> List[BuilderJobRecord]:
        with self._cp._lock:  # noqa: SLF001 — internal compose
            rows = self._cp._conn.execute(
                "SELECT * FROM builder_jobs WHERE status = 'queued' ORDER BY id LIMIT ?",
                (int(limit),),
            ).fetchall()
            return [self._cp._row_to_builder_job(r) for r in rows]

    def get(self, job_id: int) -> Optional[BuilderJobRecord]:
        with self._cp._lock:  # noqa: SLF001
            row = self._cp._conn.execute(
                "SELECT * FROM builder_jobs WHERE id = ?", (int(job_id),)
            ).fetchone()
            return self._cp._row_to_builder_job(row) if row else None

    def update_status(
        self,
        job_id: int,
        *,
        status: str,
        started_at: Optional[str] = None,
        completed_at: Optional[str] = None,
        error: Optional[str] = None,
    ) -> BuilderJobRecord:
        valid = {"queued", "running", "completed", "failed", "timed_out", "dry_run"}
        if status not in valid:
            raise ControlPlaneError(f"unknown builder_job status {status!r}; allowed: {sorted(valid)}")
        sets: List[str] = ["status = ?"]
        params: List[object] = [status]
        if started_at is not None:
            sets.append("started_at = ?")
            params.append(started_at)
        if completed_at is not None:
            sets.append("completed_at = ?")
            params.append(completed_at)
        if error is not None:
            sets.append("error = ?")
            params.append(error)
        params.append(int(job_id))
        with self._cp._lock:  # noqa: SLF001
            self._cp._conn.execute(
                f"UPDATE builder_jobs SET {', '.join(sets)} WHERE id = ?",
                params,
            )
            row = self._cp._conn.execute(
                "SELECT * FROM builder_jobs WHERE id = ?", (int(job_id),)
            ).fetchone()
            if row is None:
                raise ControlPlaneError(f"unknown builder_job {job_id}")
            return self._cp._row_to_builder_job(row)

    def stats(self) -> dict[str, int]:
        with self._cp._lock:  # noqa: SLF001
            rows = self._cp._conn.execute(
                "SELECT status, COUNT(*) AS c FROM builder_jobs GROUP BY status"
            ).fetchall()
            return {str(r["status"]): int(r["c"]) for r in rows}
