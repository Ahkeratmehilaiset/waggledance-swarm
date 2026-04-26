"""Solver candidate store — Phase 9 §U3.

Persistent registry of free-form solver candidates produced by U3.
Each candidate progresses through 10 critical states; transitions
are gated by quotas (cold/shadow throttling) per Prompt_1_Master
§U3 calibration overload rule.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from . import SOLVER_SYNTHESIS_SCHEMA_VERSION


CANDIDATE_STATES = (
    "raw_candidate",
    "schema_valid",
    "static_valid",
    "test_valid",
    "shadow_only",
    "cold_solver",
    "review_ready",
    "approved",
    "archived",
    "rejected",
)


# Quotas per Prompt_1_Master §U3
DEFAULT_QUOTAS = {
    "max_solver_candidates_generated_per_day": 1000,
    "max_new_shadow_solvers_per_day": 200,
    "max_review_ready_solvers_per_day": 50,
    "max_final_approvals_per_day": 20,
}

# Cold solver promotion gates (§U3 CRITICAL COLD SOLVER RULE)
COLD_MIN_USE_COUNT = 50
COLD_MIN_SHADOW_OBS_SECONDS = 3600
COLD_MAX_CRITICAL_REGRESSIONS = 0


@dataclass(frozen=True)
class SolverCandidate:
    schema_version: int
    candidate_id: str
    state: str
    solver_name: str
    cell_id: str
    spec_or_code: dict
    source_gap_ref: str
    no_runtime_mutation: bool
    produced_by: str
    branch_name: str
    base_commit_hash: str
    pinned_input_manifest_sha256: str
    match_confidence: float = 0.0
    use_count: int = 0
    shadow_observation_seconds: int = 0
    critical_regressions: int = 0

    def __post_init__(self) -> None:
        if self.state not in CANDIDATE_STATES:
            raise ValueError(
                f"unknown state: {self.state!r}; "
                f"allowed: {CANDIDATE_STATES}"
            )

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "candidate_id": self.candidate_id,
            "state": self.state,
            "solver_name": self.solver_name,
            "cell_id": self.cell_id,
            "spec_or_code": dict(self.spec_or_code),
            "source_gap_ref": self.source_gap_ref,
            "no_runtime_mutation": self.no_runtime_mutation,
            "match_confidence": self.match_confidence,
            "use_count": self.use_count,
            "shadow_observation_seconds": self.shadow_observation_seconds,
            "critical_regressions": self.critical_regressions,
            "provenance": {
                "produced_by": self.produced_by,
                "branch_name": self.branch_name,
                "base_commit_hash": self.base_commit_hash,
                "pinned_input_manifest_sha256":
                    self.pinned_input_manifest_sha256,
            },
        }


def compute_candidate_id(*, solver_name: str, cell_id: str,
                                 source_gap_ref: str) -> str:
    canonical = json.dumps({
        "solver_name": solver_name, "cell_id": cell_id,
        "source_gap_ref": source_gap_ref,
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def make_candidate(*,
                         solver_name: str,
                         cell_id: str,
                         spec_or_code: dict,
                         source_gap_ref: str,
                         produced_by: str = "U3_synth",
                         match_confidence: float = 0.0,
                         branch_name: str = "phase9/autonomy-fabric",
                         base_commit_hash: str = "",
                         pinned_input_manifest_sha256: str = "sha256:unknown",
                         ) -> SolverCandidate:
    cid = compute_candidate_id(
        solver_name=solver_name, cell_id=cell_id,
        source_gap_ref=source_gap_ref,
    )
    return SolverCandidate(
        schema_version=SOLVER_SYNTHESIS_SCHEMA_VERSION,
        candidate_id=cid, state="raw_candidate",
        solver_name=solver_name, cell_id=cell_id,
        spec_or_code=dict(spec_or_code),
        source_gap_ref=source_gap_ref,
        no_runtime_mutation=True,
        produced_by=produced_by,
        branch_name=branch_name,
        base_commit_hash=base_commit_hash,
        pinned_input_manifest_sha256=pinned_input_manifest_sha256,
        match_confidence=match_confidence,
    )


def with_state(c: SolverCandidate, new_state: str) -> SolverCandidate:
    if new_state not in CANDIDATE_STATES:
        raise ValueError(f"unknown state: {new_state!r}")
    return SolverCandidate(
        schema_version=c.schema_version, candidate_id=c.candidate_id,
        state=new_state, solver_name=c.solver_name,
        cell_id=c.cell_id, spec_or_code=c.spec_or_code,
        source_gap_ref=c.source_gap_ref,
        no_runtime_mutation=c.no_runtime_mutation,
        produced_by=c.produced_by,
        branch_name=c.branch_name,
        base_commit_hash=c.base_commit_hash,
        pinned_input_manifest_sha256=c.pinned_input_manifest_sha256,
        match_confidence=c.match_confidence,
        use_count=c.use_count,
        shadow_observation_seconds=c.shadow_observation_seconds,
        critical_regressions=c.critical_regressions,
    )


def can_exit_cold(c: SolverCandidate) -> tuple[bool, str]:
    """Check Prompt_1_Master §U3 CRITICAL COLD SOLVER RULE."""
    if c.use_count < COLD_MIN_USE_COUNT:
        return False, (
            f"use_count {c.use_count} < min {COLD_MIN_USE_COUNT}"
        )
    if c.shadow_observation_seconds < COLD_MIN_SHADOW_OBS_SECONDS:
        return False, (
            f"shadow_observation_seconds {c.shadow_observation_seconds} "
            f"< min {COLD_MIN_SHADOW_OBS_SECONDS}"
        )
    if c.critical_regressions > COLD_MAX_CRITICAL_REGRESSIONS:
        return False, (
            f"critical_regressions {c.critical_regressions} > max "
            f"{COLD_MAX_CRITICAL_REGRESSIONS}"
        )
    return True, "all cold gates passed"


# ── Persistence ──────────────────────────────────────────────────-

@dataclass
class SolverCandidateStore:
    candidates: dict[str, SolverCandidate] = field(default_factory=dict)

    def add(self, c: SolverCandidate) -> "SolverCandidateStore":
        self.candidates[c.candidate_id] = c
        return self

    def get(self, candidate_id: str) -> SolverCandidate | None:
        return self.candidates.get(candidate_id)

    def by_state(self, state: str) -> list[SolverCandidate]:
        return sorted(
            (c for c in self.candidates.values() if c.state == state),
            key=lambda c: c.candidate_id,
        )

    def to_dict(self) -> dict:
        return {
            "schema_version": SOLVER_SYNTHESIS_SCHEMA_VERSION,
            "candidates": {cid: c.to_dict()
                            for cid, c in sorted(self.candidates.items())},
        }


def save_store(store: SolverCandidateStore,
                  path: Path | str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(store.to_dict(), indent=2, sort_keys=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", delete=False,
        dir=target.parent, prefix=".cands.", suffix=".tmp",
    ) as tmp:
        tmp.write(payload)
        tmp_path = Path(tmp.name)
    try:
        os.replace(tmp_path, target)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise
    return target
