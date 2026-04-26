"""Autonomy kernel state — Phase 9 §F.

Persistent in-memory state container for the always-on cognitive
kernel. Stateless tick() in governor.py reads + writes this object;
disk persistence is via tmp + os.replace (atomic on POSIX/Windows).

CRITICAL SCOPE RULE (Prompt_1_Master §F):
- no action execution in this phase
- no runtime mutation
- the kernel is a recommender + scheduler, never an actor

Crown-jewel area waggledance/core/autonomy/*
(BUSL Change Date 2030-03-19).
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

KERNEL_STATE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class TickIdentity:
    """Stable identifier for one tick of the kernel.

    tick_id is a deterministic counter; ts is captured by the caller
    (governor) and excluded from hash inputs to keep tick() outputs
    deterministic given identical state.
    """
    tick_id: int
    ts_iso: str   # caller-supplied; never used for state-hash

    def to_dict(self) -> dict:
        return {"tick_id": self.tick_id, "ts_iso": self.ts_iso}


@dataclass(frozen=True)
class BudgetEntry:
    """One budget reservation slot.

    Hard caps live in the constitution; adaptive narrowing comes from
    policy_core. budget_engine.py owns reservation/reset semantics.
    """
    name: str
    hard_cap: float
    reserved: float = 0.0
    consumed: float = 0.0

    def remaining(self) -> float:
        return max(0.0, self.hard_cap - self.consumed)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "hard_cap": self.hard_cap,
            "reserved": self.reserved,
            "consumed": self.consumed,
        }


@dataclass(frozen=True)
class CircuitBreakerSnapshot:
    """Snapshot of the breaker's state for one resource/lane.

    States: closed | half_open | open | quarantined.
    Append-only history is owned by circuit_breaker.py; this is just
    the view the kernel needs to consult before scheduling work.
    """
    name: str
    state: str
    consecutive_failures: int = 0
    last_transition_tick: int = 0
    quarantined: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "state": self.state,
            "consecutive_failures": self.consecutive_failures,
            "last_transition_tick": self.last_transition_tick,
            "quarantined": self.quarantined,
        }


@dataclass(frozen=True)
class KernelState:
    """Top-level container persisted between ticks.

    Mutating helpers return new instances (frozen dataclass). The
    governor never mutates an existing KernelState in place.
    """
    schema_version: int
    last_tick: TickIdentity | None
    next_tick_id: int
    budgets: tuple[BudgetEntry, ...]
    circuit_breakers: tuple[CircuitBreakerSnapshot, ...]
    constitution_id: str
    constitution_sha256: str
    capsule_context: str
    pinned_input_manifest_sha256: str
    branch_name: str
    base_commit_hash: str
    # Open-counter for action recommendations evaluated this tick.
    actions_recommended_total: int = 0
    # Append-only counter; never decremented, used as monotonic index.
    persisted_revision: int = 0

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "last_tick": self.last_tick.to_dict() if self.last_tick else None,
            "next_tick_id": self.next_tick_id,
            "budgets": [b.to_dict() for b in self.budgets],
            "circuit_breakers": [c.to_dict() for c in self.circuit_breakers],
            "constitution_id": self.constitution_id,
            "constitution_sha256": self.constitution_sha256,
            "capsule_context": self.capsule_context,
            "pinned_input_manifest_sha256": self.pinned_input_manifest_sha256,
            "branch_name": self.branch_name,
            "base_commit_hash": self.base_commit_hash,
            "actions_recommended_total": self.actions_recommended_total,
            "persisted_revision": self.persisted_revision,
        }


# ── Construction ──────────────────────────────────────────────────

def initial_state(*,
                    constitution_id: str,
                    constitution_sha256: str,
                    capsule_context: str = "neutral_v1",
                    pinned_input_manifest_sha256: str = "sha256:unknown",
                    branch_name: str = "phase9/autonomy-fabric",
                    base_commit_hash: str = "",
                    initial_budgets: list[BudgetEntry] | None = None,
                    initial_breakers: list[CircuitBreakerSnapshot] | None = None,
                    ) -> KernelState:
    """Build a fresh state with no ticks executed yet."""
    return KernelState(
        schema_version=KERNEL_STATE_SCHEMA_VERSION,
        last_tick=None,
        next_tick_id=1,
        budgets=tuple(initial_budgets or _default_budgets()),
        circuit_breakers=tuple(initial_breakers or _default_breakers()),
        constitution_id=constitution_id,
        constitution_sha256=constitution_sha256,
        capsule_context=capsule_context,
        pinned_input_manifest_sha256=pinned_input_manifest_sha256,
        branch_name=branch_name,
        base_commit_hash=base_commit_hash,
        actions_recommended_total=0,
        persisted_revision=0,
    )


def _default_budgets() -> list[BudgetEntry]:
    return [
        BudgetEntry(name="provider_calls_per_tick", hard_cap=10.0),
        BudgetEntry(name="builder_invocations_per_tick", hard_cap=2.0),
        BudgetEntry(name="missions_dispatched_per_tick", hard_cap=20.0),
        BudgetEntry(name="actions_recommended_per_tick", hard_cap=50.0),
    ]


def _default_breakers() -> list[CircuitBreakerSnapshot]:
    return [
        CircuitBreakerSnapshot(name="provider_plane", state="closed"),
        CircuitBreakerSnapshot(name="builder_lane", state="closed"),
        CircuitBreakerSnapshot(name="solver_synthesis", state="closed"),
        CircuitBreakerSnapshot(name="action_gate", state="closed"),
    ]


# ── Persistence (atomic) ─────────────────────────────────────────-

def to_canonical_json(state: KernelState) -> str:
    return json.dumps(state.to_dict(), indent=2, sort_keys=True)


def save_state(state: KernelState, path: Path | str) -> Path:
    """Atomic save via tmp + os.replace (R7.5 §G durability rule)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = to_canonical_json(state)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", delete=False,
        dir=target.parent, prefix=".kernel_state.", suffix=".tmp",
    ) as tmp:
        tmp.write(payload)
        tmp_path = Path(tmp.name)
    try:
        os.replace(tmp_path, target)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise
    return target


def load_state(path: Path | str) -> KernelState | None:
    """Read + reconstruct a KernelState from disk. Missing file → None."""
    target = Path(path)
    if not target.exists():
        return None
    data = json.loads(target.read_text(encoding="utf-8"))
    last_tick = None
    if data.get("last_tick"):
        last_tick = TickIdentity(
            tick_id=int(data["last_tick"]["tick_id"]),
            ts_iso=str(data["last_tick"]["ts_iso"]),
        )
    return KernelState(
        schema_version=int(data.get("schema_version") or 1),
        last_tick=last_tick,
        next_tick_id=int(data.get("next_tick_id") or 1),
        budgets=tuple(
            BudgetEntry(
                name=str(b["name"]),
                hard_cap=float(b["hard_cap"]),
                reserved=float(b.get("reserved") or 0.0),
                consumed=float(b.get("consumed") or 0.0),
            )
            for b in (data.get("budgets") or [])
        ),
        circuit_breakers=tuple(
            CircuitBreakerSnapshot(
                name=str(c["name"]),
                state=str(c.get("state") or "closed"),
                consecutive_failures=int(c.get("consecutive_failures") or 0),
                last_transition_tick=int(c.get("last_transition_tick") or 0),
                quarantined=bool(c.get("quarantined") or False),
            )
            for c in (data.get("circuit_breakers") or [])
        ),
        constitution_id=str(data.get("constitution_id") or ""),
        constitution_sha256=str(data.get("constitution_sha256") or ""),
        capsule_context=str(data.get("capsule_context") or "neutral_v1"),
        pinned_input_manifest_sha256=str(
            data.get("pinned_input_manifest_sha256") or "sha256:unknown"
        ),
        branch_name=str(data.get("branch_name") or ""),
        base_commit_hash=str(data.get("base_commit_hash") or ""),
        actions_recommended_total=int(data.get("actions_recommended_total") or 0),
        persisted_revision=int(data.get("persisted_revision") or 0),
    )


# ── State transitions (pure functions; never mutate input) ───────-

def with_tick(state: KernelState, ts_iso: str) -> KernelState:
    """Produce the post-tick state with last_tick + next_tick_id
    advanced. ts_iso is opaque to deterministic-hash semantics."""
    new_tick = TickIdentity(tick_id=state.next_tick_id, ts_iso=ts_iso)
    return _replace(state,
                      last_tick=new_tick,
                      next_tick_id=state.next_tick_id + 1,
                      persisted_revision=state.persisted_revision + 1)


def with_budgets(state: KernelState,
                    budgets: tuple[BudgetEntry, ...]) -> KernelState:
    return _replace(state, budgets=budgets)


def with_breakers(state: KernelState,
                     breakers: tuple[CircuitBreakerSnapshot, ...]) -> KernelState:
    return _replace(state, circuit_breakers=breakers)


def with_actions_recommended(state: KernelState,
                                 added: int) -> KernelState:
    return _replace(state,
                      actions_recommended_total=state.actions_recommended_total + max(0, added))


def _replace(state: KernelState, **kw: Any) -> KernelState:
    """Local replacement helper; dataclasses.replace doesn't accept
    tuples for our frozen types nicely on every Python version."""
    base = state.to_dict()
    # Cannot use to_dict()/load_state() round-trip because budgets
    # need full BudgetEntry tuples, etc. Build directly.
    return KernelState(
        schema_version=kw.get("schema_version", state.schema_version),
        last_tick=kw.get("last_tick", state.last_tick),
        next_tick_id=kw.get("next_tick_id", state.next_tick_id),
        budgets=kw.get("budgets", state.budgets),
        circuit_breakers=kw.get("circuit_breakers", state.circuit_breakers),
        constitution_id=kw.get("constitution_id", state.constitution_id),
        constitution_sha256=kw.get("constitution_sha256", state.constitution_sha256),
        capsule_context=kw.get("capsule_context", state.capsule_context),
        pinned_input_manifest_sha256=kw.get(
            "pinned_input_manifest_sha256", state.pinned_input_manifest_sha256
        ),
        branch_name=kw.get("branch_name", state.branch_name),
        base_commit_hash=kw.get("base_commit_hash", state.base_commit_hash),
        actions_recommended_total=kw.get(
            "actions_recommended_total", state.actions_recommended_total
        ),
        persisted_revision=kw.get("persisted_revision", state.persisted_revision),
    )
