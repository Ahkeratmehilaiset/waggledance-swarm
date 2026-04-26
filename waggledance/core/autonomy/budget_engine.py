"""Autonomy budget engine — Phase 9 §F.budget_engine.

Owns reservation/consumption/reset semantics for kernel budgets. Hard
caps are immutable per-tick (set in constitution + KernelState.budgets);
adaptive policy may narrow them but never widen.

CRITICAL FAIL-LOUD RULE (Prompt_1_Master §GLOBAL FAIL-LOUD):
- over-reserved → recoverable warning + violation record
- over-consumed → fatal (constitution-listed fatal_path)
- negative amount → fatal

The engine is pure: every transition returns a new tuple of
BudgetEntry instead of mutating the input.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from . import kernel_state as ks


BUDGET_STATE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class BudgetViolation:
    budget_name: str
    kind: str        # over_reserved | over_consumed | negative_amount
    amount: float
    severity: str    # warning | recoverable | fatal

    def to_dict(self) -> dict:
        return {
            "budget_name": self.budget_name,
            "kind": self.kind,
            "amount": self.amount,
            "severity": self.severity,
        }


@dataclass(frozen=True)
class BudgetReport:
    schema_version: int
    tick_id: int
    budgets: tuple[ks.BudgetEntry, ...]
    violations: tuple[BudgetViolation, ...]

    def has_fatal(self) -> bool:
        return any(v.severity == "fatal" for v in self.violations)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "tick_id": self.tick_id,
            "budgets": [
                {**b.to_dict(), "remaining": b.remaining(),
                 "reset_at_tick_boundary": True}
                for b in self.budgets
            ],
            "violations": [v.to_dict() for v in self.violations],
        }


# ── Lookup ───────────────────────────────────────────────────────-

def _find(budgets: tuple[ks.BudgetEntry, ...], name: str) -> ks.BudgetEntry | None:
    for b in budgets:
        if b.name == name:
            return b
    return None


def _replace_one(budgets: tuple[ks.BudgetEntry, ...],
                    new_entry: ks.BudgetEntry) -> tuple[ks.BudgetEntry, ...]:
    out = []
    for b in budgets:
        if b.name == new_entry.name:
            out.append(new_entry)
        else:
            out.append(b)
    return tuple(out)


# ── Reservation / consumption / reset ────────────────────────────-

def reserve(budgets: tuple[ks.BudgetEntry, ...],
              *, name: str, amount: float
              ) -> tuple[tuple[ks.BudgetEntry, ...], BudgetViolation | None]:
    """Add `amount` to reserved. Returns new tuple + optional
    violation. Negative amount is fatal."""
    if amount < 0:
        return budgets, BudgetViolation(
            budget_name=name, kind="negative_amount",
            amount=amount, severity="fatal",
        )
    b = _find(budgets, name)
    if b is None:
        # Unknown budget is a recoverable violation — caller must
        # add it explicitly via narrow_cap before reserving.
        return budgets, BudgetViolation(
            budget_name=name, kind="over_reserved",
            amount=amount, severity="recoverable",
        )
    new_reserved = b.reserved + amount
    if new_reserved + b.consumed > b.hard_cap:
        # Reservation would exceed hard cap → recoverable warning,
        # reservation NOT applied (caller must back off).
        violation = BudgetViolation(
            budget_name=name, kind="over_reserved",
            amount=amount, severity="recoverable",
        )
        return budgets, violation
    new_entry = ks.BudgetEntry(
        name=b.name, hard_cap=b.hard_cap,
        reserved=new_reserved, consumed=b.consumed,
    )
    return _replace_one(budgets, new_entry), None


def consume(budgets: tuple[ks.BudgetEntry, ...],
              *, name: str, amount: float
              ) -> tuple[tuple[ks.BudgetEntry, ...], BudgetViolation | None]:
    """Move `amount` from reserved → consumed. If reserved is
    insufficient, the call is allowed but generates a fatal violation
    (over_consumed). Negative amount is fatal."""
    if amount < 0:
        return budgets, BudgetViolation(
            budget_name=name, kind="negative_amount",
            amount=amount, severity="fatal",
        )
    b = _find(budgets, name)
    if b is None:
        return budgets, BudgetViolation(
            budget_name=name, kind="over_consumed",
            amount=amount, severity="fatal",
        )
    if b.consumed + amount > b.hard_cap:
        return budgets, BudgetViolation(
            budget_name=name, kind="over_consumed",
            amount=amount, severity="fatal",
        )
    new_consumed = b.consumed + amount
    new_reserved = max(0.0, b.reserved - amount)
    new_entry = ks.BudgetEntry(
        name=b.name, hard_cap=b.hard_cap,
        reserved=new_reserved, consumed=new_consumed,
    )
    return _replace_one(budgets, new_entry), None


def reset_for_new_tick(budgets: tuple[ks.BudgetEntry, ...]
                            ) -> tuple[ks.BudgetEntry, ...]:
    """Reset reserved + consumed to 0 at tick boundary; hard_cap is
    preserved. Returns a new tuple."""
    return tuple(
        ks.BudgetEntry(name=b.name, hard_cap=b.hard_cap,
                          reserved=0.0, consumed=0.0)
        for b in budgets
    )


def narrow_cap(budgets: tuple[ks.BudgetEntry, ...],
                  *, name: str, new_cap: float
                  ) -> tuple[tuple[ks.BudgetEntry, ...], BudgetViolation | None]:
    """Adaptive policy may narrow a hard_cap but never widen it.
    Widening attempts are recoverable violations (operation refused).
    Negative caps are fatal."""
    if new_cap < 0:
        return budgets, BudgetViolation(
            budget_name=name, kind="negative_amount",
            amount=new_cap, severity="fatal",
        )
    b = _find(budgets, name)
    if b is None:
        # Unknown budget — caller wants to add a new one; allow
        # creation iff name is non-empty.
        if not name:
            return budgets, BudgetViolation(
                budget_name=name, kind="negative_amount",
                amount=new_cap, severity="fatal",
            )
        new_entry = ks.BudgetEntry(name=name, hard_cap=new_cap)
        return tuple(list(budgets) + [new_entry]), None
    if new_cap > b.hard_cap:
        return budgets, BudgetViolation(
            budget_name=name, kind="over_reserved",
            amount=new_cap, severity="recoverable",
        )
    new_entry = ks.BudgetEntry(
        name=b.name, hard_cap=new_cap,
        reserved=min(b.reserved, new_cap),
        consumed=min(b.consumed, new_cap),
    )
    return _replace_one(budgets, new_entry), None


# ── Reporting ────────────────────────────────────────────────────-

def build_report(budgets: tuple[ks.BudgetEntry, ...],
                    *, tick_id: int,
                    violations: Iterable[BudgetViolation] = (),
                    ) -> BudgetReport:
    return BudgetReport(
        schema_version=BUDGET_STATE_SCHEMA_VERSION,
        tick_id=tick_id,
        budgets=tuple(budgets),
        violations=tuple(violations),
    )
