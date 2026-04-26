# SPDX-License-Identifier: BUSL-1.1
"""Autonomy kernel governor — Phase 9 §F.

Top-level orchestrator for the always-on cognitive kernel. Provides
a stateless tick() that:

1. loads the previous KernelState from disk (or initializes one)
2. consults the immutable constitution
3. produces a deterministic, ordered list of ActionRecommendations
4. writes the post-tick state atomically
5. NEVER executes any side effect — recommendations are handed off
   via action_gate (separate phase F sub-component)

The governor is stateless between calls; persistent state lives in
kernel_state.py. Determinism contract (constitution.deterministic_tick):
given identical input KernelState, tick() must produce identical
recommendations.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

from . import kernel_state as ks


GOVERNOR_SCHEMA_VERSION = 1
ACTION_RECOMMENDATION_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ActionRecommendation:
    """One recommendation produced by the kernel for action_gate.

    The kernel never executes recommendations. This dataclass is the
    only exit shape from the kernel surface.
    """
    schema_version: int
    recommendation_id: str       # 12-char structural sha256
    tick_id: int
    kind: str                    # see schemas/action_recommendation.schema.json
    lane: str
    intent: str
    rationale: str
    risk: str                    # low | medium | high
    reversibility: str           # fully_reversible | shadow_only | advisory_only
    no_runtime_mutation: bool    # const True
    requires_human_review: bool
    produced_by: str
    capsule_context: str = "neutral_v1"
    evidence_refs: tuple[str, ...] = ()
    budget_estimate: dict | None = None

    def to_dict(self) -> dict:
        d = {
            "schema_version": self.schema_version,
            "recommendation_id": self.recommendation_id,
            "tick_id": self.tick_id,
            "kind": self.kind,
            "lane": self.lane,
            "intent": self.intent,
            "rationale": self.rationale,
            "risk": self.risk,
            "reversibility": self.reversibility,
            "no_runtime_mutation": self.no_runtime_mutation,
            "requires_human_review": self.requires_human_review,
            "produced_by": self.produced_by,
            "capsule_context": self.capsule_context,
            "evidence_refs": list(self.evidence_refs),
        }
        if self.budget_estimate is not None:
            d["budget_estimate"] = self.budget_estimate
        return d


@dataclass(frozen=True)
class TickReport:
    """Result of one tick.

    `state_after` is the post-tick KernelState. `recommendations` is
    the ordered list (sorted by recommendation_id ascending for
    deterministic output). `kernel_state_path` records where the new
    state was written, or None if dry_run.
    """
    state_before: ks.KernelState
    state_after: ks.KernelState
    recommendations: tuple[ActionRecommendation, ...]
    kernel_state_path: Path | None
    notes: tuple[str, ...]


# ── Recommendation construction ──────────────────────────────────-

def compute_recommendation_id(tick_id: int, kind: str, lane: str,
                                  intent: str) -> str:
    canonical = json.dumps({
        "tick_id": tick_id, "kind": kind, "lane": lane, "intent": intent,
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def make_recommendation(*, tick_id: int, kind: str, lane: str,
                            intent: str, rationale: str,
                            risk: str = "low",
                            reversibility: str = "advisory_only",
                            requires_human_review: bool = True,
                            produced_by: str = "governor",
                            capsule_context: str = "neutral_v1",
                            evidence_refs: tuple[str, ...] = (),
                            budget_estimate: dict | None = None,
                            ) -> ActionRecommendation:
    rid = compute_recommendation_id(tick_id, kind, lane, intent)
    return ActionRecommendation(
        schema_version=ACTION_RECOMMENDATION_SCHEMA_VERSION,
        recommendation_id=rid,
        tick_id=tick_id, kind=kind, lane=lane,
        intent=intent, rationale=rationale,
        risk=risk, reversibility=reversibility,
        no_runtime_mutation=True,
        requires_human_review=requires_human_review,
        produced_by=produced_by,
        capsule_context=capsule_context,
        evidence_refs=tuple(evidence_refs),
        budget_estimate=budget_estimate,
    )


# ── tick() ────────────────────────────────────────────────────────

def tick(*,
            state: ks.KernelState,
            ts_iso: str,
            constitution_id: str,
            constitution_sha256: str,
            inbound_signals: list[dict] | None = None,
            dry_run: bool = True,
            kernel_state_path: Path | str | None = None,
            ) -> TickReport:
    """Stateless tick.

    Determinism: with identical (state, ts_iso='ignored-by-hash',
    constitution_*, inbound_signals), tick() returns identical
    recommendations and identical post-tick KernelState (modulo
    ts fields).

    The kernel does NOT execute any recommendation. It only:
    - validates constitution identity matches state expectations
    - generates ordered recommendations from inbound signals
    - advances the persisted state via with_tick + with_actions_recommended

    constitution_sha256 mismatch is a fatal error (constitution change
    must be a separate explicit human-approved event).
    """
    notes: list[str] = []
    if state.constitution_sha256 != constitution_sha256:
        raise RuntimeError(
            f"constitution sha256 mismatch: state={state.constitution_sha256} "
            f"vs supplied={constitution_sha256}. Constitution change is a "
            f"fatal-path event and must not happen inside tick()."
        )

    inbound_signals = list(inbound_signals or [])
    new_tick_id = state.next_tick_id

    # Build recommendations deterministically from inbound signals.
    # For Phase F sub-component 3 (governor) this is intentionally
    # minimal — later sub-components (mission_queue, attention_allocator,
    # etc.) will populate the recommendation stream. For now we
    # demonstrate the wire by emitting a recommendation per inbound
    # signal that requests its routing.
    recs: list[ActionRecommendation] = []
    for sig in sorted(inbound_signals, key=lambda s: json.dumps(s, sort_keys=True)):
        kind = str(sig.get("kind") or "noop")
        lane = str(sig.get("lane") or "wait")
        intent = str(sig.get("intent") or f"route signal {kind}")
        rationale = str(sig.get("rationale") or "kernel routing of inbound signal")
        risk = str(sig.get("risk") or "low")
        reversibility = str(sig.get("reversibility") or "advisory_only")
        rec = make_recommendation(
            tick_id=new_tick_id,
            kind=kind, lane=lane,
            intent=intent, rationale=rationale,
            risk=risk, reversibility=reversibility,
            requires_human_review=bool(sig.get("requires_human_review", True)),
            capsule_context=str(sig.get("capsule_context") or state.capsule_context),
            evidence_refs=tuple(sig.get("evidence_refs") or ()),
            budget_estimate=sig.get("budget_estimate"),
        )
        recs.append(rec)

    # Stable order: by recommendation_id ascending
    recs.sort(key=lambda r: r.recommendation_id)
    rec_tuple = tuple(recs)

    # Advance state
    state_after = ks.with_tick(state, ts_iso=ts_iso)
    state_after = ks.with_actions_recommended(state_after, len(rec_tuple))

    # Persist
    saved_path: Path | None = None
    if not dry_run and kernel_state_path is not None:
        saved_path = ks.save_state(state_after, kernel_state_path)
        notes.append(f"kernel_state persisted to {saved_path.as_posix()}")
    elif dry_run:
        notes.append("dry_run=True — state not persisted")

    return TickReport(
        state_before=state,
        state_after=state_after,
        recommendations=rec_tuple,
        kernel_state_path=saved_path,
        notes=tuple(notes),
    )


# ── Constitution loading helpers ─────────────────────────────────-

def load_constitution_sha256(constitution_path: Path | str) -> str:
    """Compute sha256 of the constitution file as bytes."""
    p = Path(constitution_path)
    if not p.exists():
        raise FileNotFoundError(f"constitution not found: {p}")
    return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()


def load_constitution_id(constitution_path: Path | str) -> str:
    """Best-effort: parse constitution_id from a YAML-like file
    without requiring PyYAML at this scope. The constitution file
    format is stable enough to extract via simple line parsing."""
    p = Path(constitution_path)
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("constitution_id:"):
            value = line.split(":", 1)[1].strip().strip('"').strip("'")
            return value
    return "unknown"
