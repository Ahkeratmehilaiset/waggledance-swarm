"""Targeted tests for waggledance/core/autonomy/action_gate.py."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.autonomy import (
    action_gate as ag,
    governor as gov,
    kernel_state as ks,
    policy_core as pc,
)


def _state(**override) -> ks.KernelState:
    s = ks.initial_state(
        constitution_id="wd_autonomy_constitution_v1",
        constitution_sha256="sha256:" + "a" * 64,
    )
    return s


def _rec(**kw) -> gov.ActionRecommendation:
    defaults = dict(
        tick_id=1, kind="consultation_request", lane="provider_plane",
        intent="x intent", rationale="y rationale",
    )
    defaults.update(kw)
    return gov.make_recommendation(**defaults)


def _hard_rules():
    return pc.load_hard_rules(
        ROOT / "waggledance" / "core" / "autonomy" / "constitution.yaml"
    )


# ── 1. clean recommendation is admitted ─────────────────────────-

def test_clean_recommendation_admitted():
    s = _state()
    r = _rec()
    v = ag.evaluate_one(recommendation=r, state=s, hard_rules=_hard_rules())
    assert v.verdict == "ADMIT_TO_LANE"


# ── 2. open circuit breaker → DEFER ─────────────────────────────-

def test_open_breaker_defers():
    s = _state()
    breakers = list(s.circuit_breakers)
    new_breakers = tuple(
        ks.CircuitBreakerSnapshot(name=b.name, state="open",
                                       consecutive_failures=5,
                                       last_transition_tick=1)
        if b.name == "provider_plane" else b
        for b in breakers
    )
    s = ks.with_breakers(s, new_breakers)
    v = ag.evaluate_one(recommendation=_rec(), state=s,
                            hard_rules=_hard_rules())
    assert v.verdict == "DEFER"
    assert v.breaker_state == "open"


def test_quarantined_breaker_defers():
    s = _state()
    new_breakers = tuple(
        ks.CircuitBreakerSnapshot(name=b.name, state="closed",
                                       quarantined=True)
        if b.name == "provider_plane" else b
        for b in s.circuit_breakers
    )
    s = ks.with_breakers(s, new_breakers)
    v = ag.evaluate_one(recommendation=_rec(), state=s,
                            hard_rules=_hard_rules())
    assert v.verdict == "DEFER"
    assert v.breaker_state == "quarantined"


def test_half_open_breaker_admits():
    """half_open is a probe state — gate allows one through."""
    s = _state()
    new_breakers = tuple(
        ks.CircuitBreakerSnapshot(name=b.name, state="half_open")
        if b.name == "provider_plane" else b
        for b in s.circuit_breakers
    )
    s = ks.with_breakers(s, new_breakers)
    v = ag.evaluate_one(recommendation=_rec(), state=s,
                            hard_rules=_hard_rules())
    assert v.verdict == "ADMIT_TO_LANE"


# ── 3. policy hard-rule violation → REJECT_HARD ─────────────────-

def test_policy_block_rejects_hard():
    """A recommendation with no_runtime_mutation=False (forged) must
    be REJECT_HARD via the action_gate_is_only_exit hard rule."""
    s = _state()
    # Manually construct a forged recommendation; make_recommendation
    # always sets True, so we bypass it for this test.
    forged = gov.ActionRecommendation(
        schema_version=1, recommendation_id="f"*12, tick_id=1,
        kind="consultation_request", lane="provider_plane",
        intent="x intent", rationale="y rationale",
        risk="low", reversibility="advisory_only",
        no_runtime_mutation=False,   # ← forgery
        requires_human_review=False, produced_by="forgery_test",
    )
    v = ag.evaluate_one(recommendation=forged, state=s,
                            hard_rules=_hard_rules())
    assert v.verdict == "REJECT_HARD"
    assert "action_gate_is_only_exit" in v.blocking_rule_ids


# ── 4. budget over-reservation → DEFER ──────────────────────────-

def test_budget_over_reservation_defers():
    s = _state()
    # Fill the provider budget so the next reservation exceeds cap
    new_budgets = tuple(
        ks.BudgetEntry(name=b.name, hard_cap=b.hard_cap,
                          consumed=b.hard_cap)   # already at cap
        if b.name == "provider_calls_per_tick" else b
        for b in s.budgets
    )
    s = ks.with_budgets(s, new_budgets)
    v = ag.evaluate_one(recommendation=_rec(), state=s,
                            hard_rules=_hard_rules())
    assert v.verdict == "DEFER"
    assert v.budget_violation is not None


# ── 5. adaptive tighten can REJECT_HARD a specific lane/kind ────-

def test_adaptive_tighten_rejects_hard():
    s = _state()
    tight = pc.make_policy_rule(
        refines_hard_rule_id="budget_respect",
        verb="tighten",
        statement="FORBID:provider_plane:consultation_request",
        source="proposal:test",
    )
    v = ag.evaluate_one(recommendation=_rec(), state=s,
                            hard_rules=_hard_rules(),
                            adaptive_rules=(tight,))
    assert v.verdict == "REJECT_HARD"
    assert tight.rule_id in v.blocking_rule_ids


# ── 6. adaptive advisory shows in admit verdict ─────────────────-

def test_advisory_does_not_block_but_is_recorded():
    s = _state()
    advisory = pc.make_policy_rule(
        refines_hard_rule_id="domain_neutrality",
        verb="add_advisory_check",
        statement="watch capsule_context drift",
        source="human",
    )
    v = ag.evaluate_one(recommendation=_rec(), state=s,
                            hard_rules=_hard_rules(),
                            adaptive_rules=(advisory,))
    assert v.verdict == "ADMIT_TO_LANE"
    assert advisory.rule_id in v.advisory_rule_ids


# ── 7. evaluate_batch tracks running budgets ────────────────────-

def test_batch_running_budget_defers_subsequent():
    """If a budget hard_cap is e.g. 2.0 and we evaluate 5 calls, the
    first 2 admit, the rest DEFER."""
    s = _state()
    # Lower the provider budget cap to 2 for this test
    tight_budgets = tuple(
        ks.BudgetEntry(name=b.name, hard_cap=2.0)
        if b.name == "provider_calls_per_tick" else b
        for b in s.budgets
    )
    s = ks.with_budgets(s, tight_budgets)
    recs = [_rec(intent=f"call {i}") for i in range(5)]
    report = ag.evaluate_batch(
        recommendations=recs, state=s, hard_rules=_hard_rules(),
    )
    counts = report.counts_by_verdict
    assert counts["ADMIT_TO_LANE"] == 2
    assert counts["DEFER"] == 3


# ── 8. evaluate_batch counts add up ─────────────────────────────-

def test_batch_counts_sum_to_total():
    s = _state()
    recs = [_rec(intent=f"i{j}") for j in range(3)]
    report = ag.evaluate_batch(
        recommendations=recs, state=s, hard_rules=_hard_rules(),
    )
    assert sum(report.counts_by_verdict.values()) == 3


# ── 9. evaluate_batch tick_id from state ────────────────────────-

def test_batch_tick_id_from_state():
    s = _state()
    s = ks.with_tick(s, ts_iso="2026-04-26T03:05:00+00:00")
    report = ag.evaluate_batch(
        recommendations=[_rec()], state=s, hard_rules=_hard_rules(),
    )
    assert report.tick_id == 1


def test_batch_tick_id_zero_when_no_tick_yet():
    s = _state()   # no tick yet
    report = ag.evaluate_batch(
        recommendations=[_rec()], state=s, hard_rules=_hard_rules(),
    )
    assert report.tick_id == 0


# ── 10. unknown kind has no budget cost ─────────────────────────-

def test_noop_kind_has_no_budget_cost():
    s = _state()
    # Drain provider budget so consultation_request would fail
    drained = tuple(
        ks.BudgetEntry(name=b.name, hard_cap=b.hard_cap, consumed=b.hard_cap)
        if b.name == "provider_calls_per_tick" else b
        for b in s.budgets
    )
    s = ks.with_budgets(s, drained)
    # noop kind → no budget consultation → admits
    v = ag.evaluate_one(recommendation=_rec(kind="noop", lane="wait"),
                            state=s, hard_rules=_hard_rules())
    assert v.verdict == "ADMIT_TO_LANE"


# ── 11. action_gate source has no runtime/LLM/domain leakage ────-

def test_action_gate_source_safety():
    src = (ROOT / "waggledance" / "core" / "autonomy"
            / "action_gate.py").read_text(encoding="utf-8")
    for pat in ("import faiss", "ollama.generate(", "openai.chat",
                 "anthropic.messages", "requests.post(",
                 "axiom_write(", "promote_to_runtime(",
                 "register_solver_in_runtime("):
        assert pat not in src
    src_l = src.lower()
    for pat in ("bee ", "hive ", "honeycomb ", "swarm "):
        assert pat not in src_l


# ── 12. verdict to_dict shape ───────────────────────────────────-

def test_verdict_to_dict():
    s = _state()
    v = ag.evaluate_one(recommendation=_rec(), state=s,
                            hard_rules=_hard_rules())
    d = v.to_dict()
    assert d["verdict"] == "ADMIT_TO_LANE"
    assert "recommendation_id" in d
    assert "blocking_rule_ids" in d


# ── 13. batch report to_dict shape ──────────────────────────────-

def test_batch_to_dict_has_counts():
    s = _state()
    report = ag.evaluate_batch(
        recommendations=[_rec()], state=s, hard_rules=_hard_rules(),
    )
    d = report.to_dict()
    assert "counts_by_verdict" in d
    assert "has_fatal_budget" in d


# ── 14. action_gate executes nothing — source enforces ──────────-

def test_action_gate_executes_nothing():
    """Critical contract: the gate authorizes hand-off but never
    executes. Source must not call any 'execute' / 'run' lane API."""
    src = (ROOT / "waggledance" / "core" / "autonomy"
            / "action_gate.py").read_text(encoding="utf-8")
    forbidden = ("execute_action(", "run_recommendation(",
                  "dispatch_to_runtime(", "perform_action(",
                  "live_register_solver(")
    for pat in forbidden:
        assert pat not in src
