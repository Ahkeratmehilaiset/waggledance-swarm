"""Targeted tests for the final 4 Phase F sub-components:
attention_allocator, background_scheduler, micro_learning_lane,
circuit_breaker."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.autonomy import (
    action_gate as ag,
    attention_allocator as aa,
    background_scheduler as bg,
    circuit_breaker as cb,
    governor as gov,
    kernel_state as ks,
    micro_learning_lane as ml,
    mission_queue as mq,
    policy_core as pc,
)


def _state() -> ks.KernelState:
    return ks.initial_state(
        constitution_id="wd_autonomy_constitution_v1",
        constitution_sha256="sha256:" + "a" * 64,
    )


def _hard_rules():
    return pc.load_hard_rules(
        ROOT / "waggledance" / "core" / "autonomy" / "constitution.yaml"
    )


# ═══════════════════ attention_allocator ═══════════════════════════

def test_attention_default_weights_sum_to_one():
    s = _state()
    weights = aa.allocate(s)
    total = sum(w.weight for w in weights)
    assert abs(total - 1.0) < 1e-5


def test_attention_open_breaker_zeros_lane():
    s = _state()
    new_breakers = tuple(
        ks.CircuitBreakerSnapshot(name=b.name, state="open",
                                       consecutive_failures=5,
                                       last_transition_tick=1)
        if b.name == "provider_plane" else b
        for b in s.circuit_breakers
    )
    s = ks.with_breakers(s, new_breakers)
    weights = aa.allocate(s)
    pp = next(w for w in weights if w.lane == "provider_plane")
    assert pp.weight == 0.0
    # Total still sums to ~1.0 (renormalized over remaining lanes)
    total = sum(w.weight for w in weights)
    assert abs(total - 1.0) < 1e-5


def test_attention_low_budget_dampens():
    s = _state()
    drained = tuple(
        ks.BudgetEntry(name=b.name, hard_cap=b.hard_cap,
                          consumed=b.hard_cap * 0.95)
        if b.name == "provider_calls_per_tick" else b
        for b in s.budgets
    )
    s = ks.with_budgets(s, drained)
    weights = aa.allocate(s)
    pp = next(w for w in weights if w.lane == "provider_plane")
    # Ratio dropped vs base: provider_plane originally 0.25/sum
    # now multiplied by 0.3 then renormalized → smaller than its base share
    base_share = 0.25 / sum(aa._BASE_WEIGHTS.values())
    assert pp.weight < base_share


def test_attention_deterministic():
    s = _state()
    w1 = aa.allocate(s)
    w2 = aa.allocate(s)
    assert [w.lane for w in w1] == [w.lane for w in w2]
    assert [w.weight for w in w1] == [w.weight for w in w2]


def test_attention_ordered_lanes():
    s = _state()
    weights = aa.allocate(s)
    ordered = aa.ordered_lanes(weights)
    assert "provider_plane" in ordered
    # Stable: descending weight, then alphabetical
    pri = {w.lane: w.weight for w in weights}
    for a, b in zip(ordered, ordered[1:]):
        assert pri[a] >= pri[b]


# ═══════════════════ background_scheduler ══════════════════════════

def test_scheduler_dispatches_clean_missions():
    s = _state()
    s = ks.with_tick(s, ts_iso="t1")
    missions = [
        mq.make_mission(kind="consultation_request", lane="provider_plane",
                          priority=0.9, intent="A intent here",
                          rationale="A rationale text",
                          created_tick_id=1),
        mq.make_mission(kind="ingest_request", lane="ingestion",
                          priority=0.8, intent="B intent here",
                          rationale="B rationale text",
                          created_tick_id=1),
    ]
    report = bg.schedule_one_tick(
        state=s, missions=missions, hard_rules=_hard_rules(),
    )
    assert len(report.selected_missions) == 2
    assert report.tick_id == 1


def test_scheduler_blocks_when_breaker_open():
    s = _state()
    s = ks.with_tick(s, ts_iso="t1")
    new_breakers = tuple(
        ks.CircuitBreakerSnapshot(name=b.name, state="open",
                                       consecutive_failures=5,
                                       last_transition_tick=1)
        if b.name == "provider_plane" else b
        for b in s.circuit_breakers
    )
    s = ks.with_breakers(s, new_breakers)
    missions = [
        mq.make_mission(kind="consultation_request", lane="provider_plane",
                          priority=0.9, intent="X intent here",
                          rationale="X rationale text",
                          created_tick_id=1),
    ]
    report = bg.schedule_one_tick(
        state=s, missions=missions, hard_rules=_hard_rules(),
    )
    assert report.selected_missions == ()
    assert any(m.intent == "X intent here" for m in report.blocked_missions)


def test_scheduler_max_dispatched_caps():
    s = _state()
    s = ks.with_tick(s, ts_iso="t1")
    missions = [
        mq.make_mission(kind="ingest_request", lane="ingestion",
                          priority=0.5, intent=f"intent-{i:02d}",
                          rationale=f"rationale {i}",
                          created_tick_id=1)
        for i in range(10)
    ]
    report = bg.schedule_one_tick(
        state=s, missions=missions, hard_rules=_hard_rules(),
        max_dispatched=3,
    )
    assert len(report.selected_missions) == 3


def test_scheduler_excludes_completed():
    s = _state()
    s = ks.with_tick(s, ts_iso="t1")
    a = mq.make_mission(kind="ingest_request", lane="ingestion",
                          priority=0.7, intent="A intent",
                          rationale="A rationale", created_tick_id=1)
    a_done = mq.with_lifecycle(a, status="completed", completed_tick_id=1)
    b = mq.make_mission(kind="ingest_request", lane="ingestion",
                          priority=0.3, intent="B intent",
                          rationale="B rationale", created_tick_id=1)
    report = bg.schedule_one_tick(
        state=s, missions=[a_done, b], hard_rules=_hard_rules(),
    )
    selected_ids = {m.mission_id for m in report.selected_missions}
    assert b.mission_id in selected_ids
    assert a.mission_id not in selected_ids


# ═══════════════════ micro_learning_lane ═══════════════════════════

def test_hints_from_gate_report_emits_deltas():
    s = _state()
    rec_a = gov.make_recommendation(
        tick_id=1, kind="consultation_request", lane="provider_plane",
        intent="A intent here", rationale="A rationale",
    )
    # Force a DEFER by draining budget
    drained = tuple(
        ks.BudgetEntry(name=b.name, hard_cap=b.hard_cap,
                          consumed=b.hard_cap)
        if b.name == "provider_calls_per_tick" else b
        for b in s.budgets
    )
    s = ks.with_budgets(s, drained)
    report = ag.evaluate_batch(
        recommendations=[rec_a], state=s, hard_rules=_hard_rules(),
    )
    hints = ml.hints_from_gate_report(report, missions=[])
    # DEFER → -0.05
    assert any(h.delta < 0 for h in hints)


def test_apply_hints_does_not_mutate_input():
    m = mq.make_mission(
        kind="ingest_request", lane="ingestion", priority=0.5,
        intent="X intent here", rationale="X rationale",
        created_tick_id=1,
    )
    hint = ml.PriorityHint(mission_id=m.mission_id, delta=-0.2,
                              reason="test")
    out = ml.apply_hints([m], [hint])
    assert out[0].priority == pytest.approx(0.3)
    assert m.priority == 0.5   # original untouched


def test_apply_hints_clamps_priority_at_zero():
    m = mq.make_mission(
        kind="ingest_request", lane="ingestion", priority=0.1,
        intent="X intent here", rationale="X rationale",
        created_tick_id=1,
    )
    hint = ml.PriorityHint(mission_id=m.mission_id, delta=-1.0,
                              reason="test")
    out = ml.apply_hints([m], [hint])
    assert out[0].priority == 0.0


def test_apply_hints_recommendation_to_mission_map():
    m = mq.make_mission(
        kind="ingest_request", lane="ingestion", priority=0.5,
        intent="X intent here", rationale="X rationale",
        created_tick_id=1,
    )
    hint = ml.PriorityHint(mission_id="r"*12, delta=-0.2,
                              reason="via rec id")
    out = ml.apply_hints([m], [hint],
                            recommendation_to_mission={"r"*12: m.mission_id})
    assert out[0].priority == pytest.approx(0.3)


# ═══════════════════ circuit_breaker ═══════════════════════════════

def test_breaker_failure_increments_then_opens():
    snap = ks.CircuitBreakerSnapshot(name="provider_plane", state="closed")
    for i in range(cb.DEFAULT_FAILURES_TO_OPEN - 1):
        snap = cb.on_failure(snap, tick_id=i+1)
    assert snap.state == "closed"
    snap = cb.on_failure(snap, tick_id=cb.DEFAULT_FAILURES_TO_OPEN)
    assert snap.state == "open"


def test_breaker_cooldown_moves_to_half_open():
    snap = ks.CircuitBreakerSnapshot(
        name="x", state="open", consecutive_failures=5,
        last_transition_tick=1,
    )
    snap2 = cb.on_cooldown_tick(snap, current_tick=cb.DEFAULT_COOLDOWN_TICKS + 2)
    assert snap2.state == "half_open"


def test_breaker_success_in_half_open_closes():
    snap = ks.CircuitBreakerSnapshot(
        name="x", state="half_open", consecutive_failures=5,
    )
    snap2 = cb.on_success(snap, tick_id=20)
    assert snap2.state == "closed"
    assert snap2.consecutive_failures == 0


def test_breaker_failure_in_half_open_reopens():
    snap = ks.CircuitBreakerSnapshot(
        name="x", state="half_open", consecutive_failures=5,
    )
    snap2 = cb.on_failure(snap, tick_id=21)
    assert snap2.state == "open"


def test_breaker_quarantine_after_repeated_open_cycles():
    snap = ks.CircuitBreakerSnapshot(
        name="x", state="open",
        consecutive_failures=cb.DEFAULT_FAILURES_TO_OPEN
            * cb.DEFAULT_OPEN_CYCLES_TO_QUARANTINE - 1,
    )
    snap2 = cb.on_failure(snap, tick_id=99)
    assert snap2.quarantined is True


def test_breaker_quarantined_is_terminal():
    snap = ks.CircuitBreakerSnapshot(
        name="x", state="open", quarantined=True,
    )
    snap2 = cb.on_success(snap, tick_id=100)
    assert snap2.quarantined is True
    assert snap2 == snap   # unchanged


def test_breaker_event_chain_validates(tmp_path):
    p = tmp_path / "breaker_events.jsonl"
    e1 = cb.make_event(
        lane="provider_plane", from_state="closed", to_state="open",
        tick_id=1, consecutive_failures=5,
        reason="5 consecutive failures",
        prev_entry_sha256=cb.GENESIS_PREV,
        ts="2026-04-26T03:00:00+00:00",
    )
    cb.append_event(p, e1)
    e2 = cb.make_event(
        lane="provider_plane", from_state="open", to_state="half_open",
        tick_id=11, consecutive_failures=5,
        reason="cooldown elapsed",
        prev_entry_sha256=e1.entry_sha256,
        ts="2026-04-26T03:11:00+00:00",
    )
    cb.append_event(p, e2)
    events = cb.read_events(p)
    ok, broken = cb.validate_chain(events)
    assert ok and broken is None


def test_breaker_event_sha_excludes_self():
    e = cb.make_event(
        lane="x", from_state="closed", to_state="open",
        tick_id=1, consecutive_failures=5, reason="r",
        prev_entry_sha256=cb.GENESIS_PREV,
        ts="t",
    )
    d = e.to_dict()
    d.pop("entry_sha256")
    assert cb.compute_entry_sha256(d) == e.entry_sha256
    with pytest.raises(ValueError):
        cb.compute_entry_sha256({"entry_sha256": e.entry_sha256})


def test_breaker_duplicate_append_skipped(tmp_path):
    p = tmp_path / "breaker_events.jsonl"
    e = cb.make_event(
        lane="x", from_state="closed", to_state="open",
        tick_id=1, consecutive_failures=5, reason="r",
        prev_entry_sha256=cb.GENESIS_PREV,
        ts="t",
    )
    cb.append_event(p, e)
    cb.append_event(p, e)
    cb.append_event(p, e)
    assert len(cb.read_events(p)) == 1


def test_breaker_malformed_lines_skipped(tmp_path):
    p = tmp_path / "breaker_events.jsonl"
    e = cb.make_event(
        lane="x", from_state="closed", to_state="open",
        tick_id=1, consecutive_failures=5, reason="r",
        prev_entry_sha256=cb.GENESIS_PREV,
        ts="t",
    )
    cb.append_event(p, e)
    with open(p, "a", encoding="utf-8") as f:
        f.write("{ this is not json\n")
        f.write('{"missing": "fields"}\n')
    events = cb.read_events(p)
    assert len(events) == 1


def test_breaker_chain_break_detected(tmp_path):
    p = tmp_path / "breaker_events.jsonl"
    e1 = cb.make_event(
        lane="x", from_state="closed", to_state="open",
        tick_id=1, consecutive_failures=5, reason="r",
        prev_entry_sha256=cb.GENESIS_PREV, ts="t",
    )
    e2 = cb.make_event(
        lane="x", from_state="open", to_state="half_open",
        tick_id=11, consecutive_failures=5, reason="cooldown",
        prev_entry_sha256="deadbeef" * 8,   # broken
        ts="t2",
    )
    cb.append_event(p, e1)
    cb.append_event(p, e2)
    events = cb.read_events(p)
    ok, broken = cb.validate_chain(events)
    assert not ok
    assert broken == e2.entry_sha256


# ═══════════════════ source safety (all 4 modules) ═════════════════

def test_phase_f_completion_modules_safety():
    pkg = ROOT / "waggledance" / "core" / "autonomy"
    files = ["attention_allocator.py", "background_scheduler.py",
             "micro_learning_lane.py", "circuit_breaker.py"]
    for f in files:
        src = (pkg / f).read_text(encoding="utf-8")
        for pat in ("import faiss", "ollama.generate(", "openai.chat",
                     "anthropic.messages", "requests.post(",
                     "axiom_write(", "promote_to_runtime(",
                     "register_solver_in_runtime("):
            assert pat not in src, f"{f}: forbidden {pat}"
        src_l = src.lower()
        for pat in ("bee ", "hive ", "honeycomb ", "swarm ",
                     "factory ", "pdam", "beverage"):
            assert pat not in src_l, f"{f}: domain metaphor {pat}"
