# SPDX-License-Identifier: Apache-2.0
"""Targeted tests for waggledance/core/autonomy/budget_engine.py
(Phase 9 §F.budget_engine).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.autonomy import (
    budget_engine as be,
    kernel_state as ks,
)


def _budgets() -> tuple[ks.BudgetEntry, ...]:
    return (
        ks.BudgetEntry(name="provider_calls_per_tick", hard_cap=10.0),
        ks.BudgetEntry(name="builder_invocations_per_tick", hard_cap=2.0),
    )


# ── 1. reserve happy path ────────────────────────────────────────-

def test_reserve_within_cap():
    b = _budgets()
    new_b, v = be.reserve(b, name="provider_calls_per_tick", amount=5.0)
    assert v is None
    entry = next(e for e in new_b if e.name == "provider_calls_per_tick")
    assert entry.reserved == 5.0
    assert entry.consumed == 0.0
    assert entry.remaining() == 10.0
    # Original tuple unchanged
    orig = next(e for e in b if e.name == "provider_calls_per_tick")
    assert orig.reserved == 0.0


# ── 2. reserve over hard cap → recoverable + no-op ──────────────-

def test_reserve_over_cap_returns_recoverable_warning_and_noop():
    b = _budgets()
    new_b, v = be.reserve(b, name="provider_calls_per_tick", amount=20.0)
    assert v is not None
    assert v.severity == "recoverable"
    assert v.kind == "over_reserved"
    # Reservation NOT applied
    entry = next(e for e in new_b if e.name == "provider_calls_per_tick")
    assert entry.reserved == 0.0


# ── 3. reserve negative → fatal ──────────────────────────────────-

def test_reserve_negative_amount_is_fatal():
    b = _budgets()
    _, v = be.reserve(b, name="provider_calls_per_tick", amount=-1.0)
    assert v is not None
    assert v.severity == "fatal"
    assert v.kind == "negative_amount"


# ── 4. reserve unknown budget → recoverable ─────────────────────-

def test_reserve_unknown_budget_is_recoverable():
    b = _budgets()
    _, v = be.reserve(b, name="bogus_budget", amount=1.0)
    assert v is not None
    assert v.severity == "recoverable"


# ── 5. consume happy path ────────────────────────────────────────-

def test_consume_moves_reserved_to_consumed():
    b = _budgets()
    b, _ = be.reserve(b, name="provider_calls_per_tick", amount=5.0)
    b, v = be.consume(b, name="provider_calls_per_tick", amount=3.0)
    assert v is None
    entry = next(e for e in b if e.name == "provider_calls_per_tick")
    assert entry.consumed == 3.0
    assert entry.reserved == 2.0   # 5 reserved → 2 left after 3 consumed


# ── 6. consume over hard cap → fatal ──────────────────────────────

def test_consume_over_hard_cap_is_fatal():
    b = _budgets()
    _, v = be.consume(b, name="provider_calls_per_tick", amount=11.0)
    assert v is not None
    assert v.severity == "fatal"
    assert v.kind == "over_consumed"


# ── 7. consume without prior reserve still allowed within cap ───-

def test_consume_without_reserve_within_cap():
    b = _budgets()
    b, v = be.consume(b, name="provider_calls_per_tick", amount=3.0)
    assert v is None
    entry = next(e for e in b if e.name == "provider_calls_per_tick")
    assert entry.consumed == 3.0
    assert entry.reserved == 0.0


# ── 8. consume negative → fatal ──────────────────────────────────-

def test_consume_negative_amount_is_fatal():
    b = _budgets()
    _, v = be.consume(b, name="provider_calls_per_tick", amount=-1.0)
    assert v is not None
    assert v.severity == "fatal"


# ── 9. consume unknown budget → fatal ───────────────────────────-

def test_consume_unknown_budget_is_fatal():
    b = _budgets()
    _, v = be.consume(b, name="bogus", amount=1.0)
    assert v is not None
    assert v.severity == "fatal"


# ── 10. reset_for_new_tick zeros reserved + consumed ────────────-

def test_reset_for_new_tick():
    b = _budgets()
    b, _ = be.reserve(b, name="provider_calls_per_tick", amount=5.0)
    b, _ = be.consume(b, name="provider_calls_per_tick", amount=3.0)
    b2 = be.reset_for_new_tick(b)
    for e in b2:
        assert e.reserved == 0.0
        assert e.consumed == 0.0
    # hard_cap preserved
    entry = next(e for e in b2 if e.name == "provider_calls_per_tick")
    assert entry.hard_cap == 10.0


# ── 11. narrow_cap can tighten ───────────────────────────────────-

def test_narrow_cap_tightens():
    b = _budgets()
    new_b, v = be.narrow_cap(b, name="provider_calls_per_tick", new_cap=5.0)
    assert v is None
    entry = next(e for e in new_b if e.name == "provider_calls_per_tick")
    assert entry.hard_cap == 5.0


# ── 12. narrow_cap refuses widening (recoverable) ───────────────-

def test_narrow_cap_refuses_widening():
    b = _budgets()
    _, v = be.narrow_cap(b, name="provider_calls_per_tick", new_cap=999.0)
    assert v is not None
    assert v.severity == "recoverable"


# ── 13. narrow_cap negative → fatal ─────────────────────────────-

def test_narrow_cap_negative_is_fatal():
    b = _budgets()
    _, v = be.narrow_cap(b, name="provider_calls_per_tick", new_cap=-1.0)
    assert v is not None
    assert v.severity == "fatal"


# ── 14. narrow_cap can add a new budget ─────────────────────────-

def test_narrow_cap_adds_new_budget():
    b = _budgets()
    new_b, v = be.narrow_cap(b, name="new_budget", new_cap=3.0)
    assert v is None
    entry = next(e for e in new_b if e.name == "new_budget")
    assert entry.hard_cap == 3.0


# ── 15. narrow_cap reduces reserved/consumed if over new cap ────-

def test_narrow_cap_reduces_reserved_consumed_to_new_cap():
    b = _budgets()
    b, _ = be.reserve(b, name="provider_calls_per_tick", amount=8.0)
    b, _ = be.consume(b, name="provider_calls_per_tick", amount=8.0)
    new_b, _ = be.narrow_cap(b, name="provider_calls_per_tick", new_cap=3.0)
    entry = next(e for e in new_b if e.name == "provider_calls_per_tick")
    assert entry.consumed == 3.0   # clamped to new cap
    assert entry.reserved == 0.0   # was 0 because all consumed


# ── 16. build_report shape + has_fatal ──────────────────────────-

def test_build_report_shape():
    b = _budgets()
    r = be.build_report(b, tick_id=42)
    d = r.to_dict()
    assert d["schema_version"] == be.BUDGET_STATE_SCHEMA_VERSION
    assert d["tick_id"] == 42
    assert "budgets" in d and len(d["budgets"]) == 2
    for entry in d["budgets"]:
        assert "remaining" in entry
        assert "reset_at_tick_boundary" in entry
    assert d["violations"] == []


def test_has_fatal_detects_fatal_violation():
    b = _budgets()
    v = be.BudgetViolation(budget_name="x", kind="over_consumed",
                              amount=1.0, severity="fatal")
    r = be.build_report(b, tick_id=1, violations=[v])
    assert r.has_fatal() is True


def test_has_fatal_false_for_recoverable():
    b = _budgets()
    v = be.BudgetViolation(budget_name="x", kind="over_reserved",
                              amount=1.0, severity="recoverable")
    r = be.build_report(b, tick_id=1, violations=[v])
    assert r.has_fatal() is False


# ── 17. report determinism ───────────────────────────────────────

def test_report_to_dict_is_byte_stable():
    import json
    b = _budgets()
    r = be.build_report(b, tick_id=7)
    d1 = json.dumps(r.to_dict(), indent=2, sort_keys=True)
    d2 = json.dumps(r.to_dict(), indent=2, sort_keys=True)
    assert d1 == d2


# ── 18. budget_engine source has no runtime/LLM/domain leakage ──-

def test_budget_engine_source_safety():
    src = (ROOT / "waggledance" / "core" / "autonomy"
            / "budget_engine.py").read_text(encoding="utf-8")
    forbidden_runtime = ["import faiss", "from waggledance.runtime",
                          "ollama.generate(", "openai.chat",
                          "anthropic.messages", "requests.post(",
                          "axiom_write("]
    for pat in forbidden_runtime:
        assert pat not in src
    src_l = src.lower()
    forbidden_metaphors = ["bee ", "hive ", "honeycomb ", "swarm ",
                            "factory ", "pdam", "beverage"]
    for pat in forbidden_metaphors:
        assert pat not in src_l


# ── 19. multi-step happy-path scenario ──────────────────────────-

def test_full_tick_lifecycle():
    """Simulate one tick: reserve some calls, consume some, build
    report, check no fatal."""
    b = _budgets()
    violations = []
    b, v = be.reserve(b, name="provider_calls_per_tick", amount=4.0)
    if v: violations.append(v)
    b, v = be.consume(b, name="provider_calls_per_tick", amount=3.0)
    if v: violations.append(v)
    b, v = be.reserve(b, name="builder_invocations_per_tick", amount=1.0)
    if v: violations.append(v)
    r = be.build_report(b, tick_id=1, violations=violations)
    assert not r.has_fatal()
    assert r.tick_id == 1
    # Reset for next tick
    b2 = be.reset_for_new_tick(b)
    for e in b2:
        assert e.reserved == 0.0
        assert e.consumed == 0.0
