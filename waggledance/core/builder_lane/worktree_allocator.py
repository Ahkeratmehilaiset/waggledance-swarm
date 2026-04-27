# SPDX-License-Identifier: BUSL-1.1
"""Worktree allocator — Phase 9 §U2.

Manages deterministic isolated paths for builder lane invocations.
Per Prompt_1_Master §U2 SUBPROCESS EXCEPTION, builder lane outputs
must be confined to allocated worktrees and tracked in
builder_invocation_log.jsonl. This module reserves the path; actual
git worktree add is performed by the caller (and tested via mocks).
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorktreeAllocation:
    request_id: str
    base_path: Path
    branch_name: str
    base_branch: str

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "base_path": self.base_path.as_posix(),
            "branch_name": self.branch_name,
            "base_branch": self.base_branch,
        }


def derive_worktree_path(*, request_id: str,
                              root: Path | str) -> Path:
    """Deterministic path: <root>/builder_lane/<request_id>/."""
    return Path(root) / "builder_lane" / request_id


def derive_branch_name(*, request_id: str,
                            base_branch: str = "phase9/autonomy-fabric"
                            ) -> str:
    """Deterministic branch name. Suffix with first 8 chars of
    request_id so the branch is stable + collision-resistant."""
    return f"phase9-builder/{request_id[:8]}"


def allocate(*, request_id: str,
                root: Path | str,
                base_branch: str = "phase9/autonomy-fabric"
                ) -> WorktreeAllocation:
    """Reserve an allocation. Does NOT call git; caller does."""
    return WorktreeAllocation(
        request_id=request_id,
        base_path=derive_worktree_path(request_id=request_id, root=root),
        branch_name=derive_branch_name(request_id=request_id,
                                            base_branch=base_branch),
        base_branch=base_branch,
    )


# ── Invocation log (append-only, atomic per-line) ────────────────-

@dataclass(frozen=True)
class InvocationLogEntry:
    request_id: str
    ts_iso: str
    isolated_worktree_path: str
    isolated_branch_name: str
    outcome: str
    rationale: str

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "ts_iso": self.ts_iso,
            "isolated_worktree_path": self.isolated_worktree_path,
            "isolated_branch_name": self.isolated_branch_name,
            "outcome": self.outcome,
            "rationale": self.rationale,
        }


def append_invocation(path: Path | str,
                            entry: InvocationLogEntry) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry.to_dict(), sort_keys=True,
                         separators=(",", ":"))
    with open(p, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return p


def read_invocations(path: Path | str) -> list[InvocationLogEntry]:
    p = Path(path)
    if not p.exists():
        return []
    out: list[InvocationLogEntry] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            out.append(InvocationLogEntry(
                request_id=str(d["request_id"]),
                ts_iso=str(d.get("ts_iso") or ""),
                isolated_worktree_path=str(d["isolated_worktree_path"]),
                isolated_branch_name=str(d["isolated_branch_name"]),
                outcome=str(d.get("outcome") or "unknown"),
                rationale=str(d.get("rationale") or ""),
            ))
        except (KeyError, ValueError):
            continue
    return out
