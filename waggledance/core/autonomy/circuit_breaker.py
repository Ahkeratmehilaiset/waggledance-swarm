"""Autonomy circuit breaker — Phase 9 §F.circuit_breaker.

Append-only, crash-convergent, tamper-evident state machine for
runaway-loop prevention. States:

  closed (normal)
    │ N consecutive failures
    ▼
  open (work suppressed)
    │ cool-down ticks elapsed
    ▼
  half_open (probe — one mission allowed)
    │ probe success
    ▼
  closed
    │ probe failure
    ▼
  open  (or quarantined after M open-cycles)
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from . import kernel_state as ks


CIRCUIT_BREAKER_SCHEMA_VERSION = 1
GENESIS_PREV = "0" * 64

# Default thresholds (constitution may narrow these per capsule)
DEFAULT_FAILURES_TO_OPEN = 5
DEFAULT_COOLDOWN_TICKS = 10
DEFAULT_OPEN_CYCLES_TO_QUARANTINE = 3


@dataclass(frozen=True)
class BreakerEvent:
    schema_version: int
    entry_sha256: str
    ts: str
    lane: str
    from_state: str
    to_state: str
    tick_id: int
    consecutive_failures: int
    reason: str
    prev_entry_sha256: str

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "entry_sha256": self.entry_sha256,
            "ts": self.ts,
            "lane": self.lane,
            "from_state": self.from_state,
            "to_state": self.to_state,
            "tick_id": self.tick_id,
            "consecutive_failures": self.consecutive_failures,
            "reason": self.reason,
            "prev_entry_sha256": self.prev_entry_sha256,
        }


def compute_entry_sha256(entry_dict_without_sha: dict) -> str:
    if "entry_sha256" in entry_dict_without_sha:
        raise ValueError("entry_sha256 must not be in the hash input")
    canonical = json.dumps(entry_dict_without_sha, sort_keys=True,
                              separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def make_event(*, lane: str, from_state: str, to_state: str,
                  tick_id: int, consecutive_failures: int,
                  reason: str, prev_entry_sha256: str,
                  ts: str) -> BreakerEvent:
    base = {
        "schema_version": CIRCUIT_BREAKER_SCHEMA_VERSION,
        "ts": ts, "lane": lane,
        "from_state": from_state, "to_state": to_state,
        "tick_id": tick_id,
        "consecutive_failures": consecutive_failures,
        "reason": reason,
        "prev_entry_sha256": prev_entry_sha256,
    }
    sha = compute_entry_sha256(base)
    return BreakerEvent(
        schema_version=CIRCUIT_BREAKER_SCHEMA_VERSION,
        entry_sha256=sha, ts=ts, lane=lane,
        from_state=from_state, to_state=to_state,
        tick_id=tick_id,
        consecutive_failures=consecutive_failures,
        reason=reason, prev_entry_sha256=prev_entry_sha256,
    )


# ── State transitions (pure) ─────────────────────────────────────-

def on_failure(snap: ks.CircuitBreakerSnapshot, *,
                  tick_id: int,
                  failures_to_open: int = DEFAULT_FAILURES_TO_OPEN,
                  open_cycles_to_quarantine: int = DEFAULT_OPEN_CYCLES_TO_QUARANTINE,
                  ) -> ks.CircuitBreakerSnapshot:
    """Record one failure for this lane. Returns updated snapshot."""
    if snap.quarantined:
        return snap
    new_failures = snap.consecutive_failures + 1
    if snap.state == "half_open":
        # Probe failed → re-open + count this open cycle
        return ks.CircuitBreakerSnapshot(
            name=snap.name, state="open",
            consecutive_failures=new_failures,
            last_transition_tick=tick_id,
            quarantined=False,
        )
    if new_failures >= failures_to_open:
        # Permanent quarantine after M open-cycles
        if snap.state == "open" and \
                new_failures >= failures_to_open * open_cycles_to_quarantine:
            return ks.CircuitBreakerSnapshot(
                name=snap.name, state="open",
                consecutive_failures=new_failures,
                last_transition_tick=tick_id,
                quarantined=True,
            )
        return ks.CircuitBreakerSnapshot(
            name=snap.name, state="open",
            consecutive_failures=new_failures,
            last_transition_tick=tick_id,
            quarantined=False,
        )
    return ks.CircuitBreakerSnapshot(
        name=snap.name, state=snap.state,
        consecutive_failures=new_failures,
        last_transition_tick=snap.last_transition_tick,
        quarantined=False,
    )


def on_success(snap: ks.CircuitBreakerSnapshot, *,
                  tick_id: int) -> ks.CircuitBreakerSnapshot:
    """Record one success. half_open → closed on first success."""
    if snap.quarantined:
        return snap
    if snap.state == "half_open":
        return ks.CircuitBreakerSnapshot(
            name=snap.name, state="closed",
            consecutive_failures=0,
            last_transition_tick=tick_id,
            quarantined=False,
        )
    return ks.CircuitBreakerSnapshot(
        name=snap.name, state=snap.state,
        consecutive_failures=0,
        last_transition_tick=snap.last_transition_tick,
        quarantined=False,
    )


def on_cooldown_tick(snap: ks.CircuitBreakerSnapshot, *,
                          current_tick: int,
                          cooldown_ticks: int = DEFAULT_COOLDOWN_TICKS,
                          ) -> ks.CircuitBreakerSnapshot:
    """If breaker has been open long enough, move to half_open."""
    if snap.quarantined or snap.state != "open":
        return snap
    if current_tick - snap.last_transition_tick >= cooldown_ticks:
        return ks.CircuitBreakerSnapshot(
            name=snap.name, state="half_open",
            consecutive_failures=snap.consecutive_failures,
            last_transition_tick=current_tick,
            quarantined=False,
        )
    return snap


# ── Append-only persistence + chain validation ───────────────────-

def append_event(path: Path | str, event: BreakerEvent) -> Path:
    """Append (skip on duplicate entry_sha256) — same pattern as
    Session D history.py."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = read_events(p)
    if any(e.entry_sha256 == event.entry_sha256 for e in existing):
        return p
    line = json.dumps(event.to_dict(), sort_keys=True,
                         separators=(",", ":"))
    with open(p, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return p


def read_events(path: Path | str) -> list[BreakerEvent]:
    p = Path(path)
    if not p.exists():
        return []
    out: list[BreakerEvent] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            out.append(BreakerEvent(
                schema_version=int(d.get("schema_version") or 1),
                entry_sha256=str(d["entry_sha256"]),
                ts=str(d.get("ts") or ""),
                lane=str(d["lane"]),
                from_state=str(d["from_state"]),
                to_state=str(d["to_state"]),
                tick_id=int(d.get("tick_id") or 0),
                consecutive_failures=int(d.get("consecutive_failures") or 0),
                reason=str(d.get("reason") or ""),
                prev_entry_sha256=str(d.get("prev_entry_sha256") or GENESIS_PREV),
            ))
        except (KeyError, ValueError):
            continue
    return out


def validate_chain(events: list[BreakerEvent]) -> tuple[bool, str | None]:
    prev = GENESIS_PREV
    for e in events:
        if e.prev_entry_sha256 != prev:
            return False, e.entry_sha256
        prev = e.entry_sha256
    return True, None


def latest_prev_entry_sha256(events: list[BreakerEvent]) -> str:
    return events[-1].entry_sha256 if events else GENESIS_PREV
