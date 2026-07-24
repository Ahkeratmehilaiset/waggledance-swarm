# SPDX-License-Identifier: Apache-2.0
"""Targeted tests for waggledance/core/autonomy/action_gate.py."""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.autonomy import (
    action_gate as ag,
    background_scheduler as bg,
    governor as gov,
    kernel_state as ks,
    mission_queue as mq,
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


# ── 2b. lane and kind allowlists fail closed ──────────────────────-

def test_unknown_lane_without_breaker_rejects_hard():
    s = _state()
    assert all(b.name != "bogus_lane" for b in s.circuit_breakers)

    v = ag.evaluate_one(
        recommendation=_rec(kind="noop", lane="bogus_lane"),
        state=s,
        hard_rules=_hard_rules(),
    )

    assert v.verdict == "REJECT_HARD"
    assert v.reason == "unknown recommendation lane: 'bogus_lane'"
    assert v.breaker_state is None


def test_named_breaker_that_is_not_a_lane_rejects_hard():
    s = _state()
    assert any(b.name == "action_gate" for b in s.circuit_breakers)

    v = ag.evaluate_one(
        recommendation=_rec(kind="noop", lane="action_gate"),
        state=s,
        hard_rules=_hard_rules(),
    )

    assert v.verdict == "REJECT_HARD"
    assert v.reason == "unknown recommendation lane: 'action_gate'"
    assert v.breaker_state is None


def test_custom_closed_breaker_does_not_create_an_allowed_lane():
    s = _state()
    s = ks.with_breakers(
        s,
        s.circuit_breakers + (
            ks.CircuitBreakerSnapshot(name="custom_lane", state="closed"),
        ),
    )

    v = ag.evaluate_one(
        recommendation=_rec(kind="noop", lane="custom_lane"),
        state=s,
        hard_rules=_hard_rules(),
    )

    assert v.verdict == "REJECT_HARD"
    assert v.reason == "unknown recommendation lane: 'custom_lane'"
    assert v.breaker_state is None


def test_unknown_kind_rejects_hard():
    v = ag.evaluate_one(
        recommendation=_rec(kind="external_effect", lane="provider_plane"),
        state=_state(),
        hard_rules=_hard_rules(),
    )

    assert v.verdict == "REJECT_HARD"
    assert v.reason == "unknown recommendation kind: 'external_effect'"
    assert v.breaker_state is None


class _EqBomb:
    def __eq__(self, other):
        raise RuntimeError("hostile equality must not run")


class _EqBombStr(str):
    def __eq__(self, other):
        raise RuntimeError("hostile string-subclass equality must not run")


class _ReprBombStr(str):
    def __repr__(self):
        raise RuntimeError("hostile repr must not run")


class _EqualitySpoof:
    def __eq__(self, other):
        return True


class _PassiveStr(str):
    pass


class _RouteFlippingRecommendation(gov.ActionRecommendation):
    def __getattribute__(self, name):
        if name == "lane":
            values = object.__getattribute__(self, "__dict__")
            reads = values.get("_hostile_lane_reads", 0) + 1
            values["_hostile_lane_reads"] = reads
            return (
                "provider_plane"
                if reads <= 2
                else "unknown_after_validation"
            )
        return super().__getattribute__(name)


def test_recommendation_subclass_cannot_flip_route_after_validation():
    base = _rec(kind="noop", lane="provider_plane")
    hostile = _RouteFlippingRecommendation(**base.__dict__)

    verdict = ag.evaluate_one(
        recommendation=hostile,
        state=_state(),
        hard_rules=_hard_rules(),
    )

    assert verdict.verdict == "REJECT_HARD"
    assert verdict.recommendation_id == "invalid-recommendation"
    assert verdict.reason == "recommendation must be an exact ActionRecommendation"
    assert verdict.blocking_rule_ids == ("action_gate_is_only_exit",)
    assert hostile.__dict__.get("_hostile_lane_reads", 0) == 0

    report = ag.evaluate_batch(
        recommendations=(hostile,),
        state=_state(),
        hard_rules=_hard_rules(),
    )
    assert report.verdicts == (verdict,)
    assert hostile.__dict__.get("_hostile_lane_reads", 0) == 0


@pytest.mark.parametrize(
    "hostile_value",
    (
        _EqBomb(),
        _EqBombStr("provider_plane"),
        _ReprBombStr("not_allowlisted"),
        _EqualitySpoof(),
        _PassiveStr("provider_plane"),
    ),
)
@pytest.mark.parametrize(
    ("field", "expected_reason"),
    (
        ("lane", "recommendation lane must be an exact built-in str"),
        ("kind", "recommendation kind must be an exact built-in str"),
    ),
)
def test_route_fields_require_exact_builtin_strings(
    field,
    expected_reason,
    hostile_value,
):
    verdict = ag.evaluate_one(
        recommendation=replace(_rec(), **{field: hostile_value}),
        state=_state(),
        hard_rules=_hard_rules(),
    )

    assert verdict.verdict == "REJECT_HARD"
    assert verdict.reason == expected_reason
    assert verdict.breaker_state is None


_VALID_KIND_LANE_PAIRS = (
    ("ingest_request", "ingestion"),
    ("consultation_request", "provider_plane"),
    ("builder_request", "builder_lane"),
    ("solver_synthesis_request", "solver_synthesis"),
    ("shadow_replay_request", "wait"),
    ("promotion_review_request", "promotion"),
    ("memory_tier_move", "memory_tiers"),
    ("calibration_check", "self_inspection"),
    ("self_inspection", "self_inspection"),
    ("noop", "wait"),
)


def test_valid_kind_lane_matrix_covers_every_allowlisted_value():
    assert {kind for kind, _ in _VALID_KIND_LANE_PAIRS} == set(mq.ALLOWED_KINDS)
    assert {lane for _, lane in _VALID_KIND_LANE_PAIRS} == set(mq.ALLOWED_LANES)


@pytest.mark.parametrize(("kind", "lane"), _VALID_KIND_LANE_PAIRS)
def test_all_allowlisted_kinds_and_lanes_retain_admission(kind, lane):
    v = ag.evaluate_one(
        recommendation=_rec(kind=kind, lane=lane),
        state=_state(),
        hard_rules=_hard_rules(),
    )

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


@pytest.mark.parametrize(
    "forged_value",
    (False, 0, 1, "false", "true", None),
)
def test_no_runtime_mutation_requires_exact_true(forged_value):
    forged = replace(_rec(), no_runtime_mutation=forged_value)

    verdict = ag.evaluate_one(
        recommendation=forged,
        state=_state(),
        hard_rules=_hard_rules(),
    )

    assert verdict.verdict == "REJECT_HARD"
    assert verdict.blocking_rule_ids == ("action_gate_is_only_exit",)
    assert verdict.breaker_state is None


def test_no_runtime_mutation_rejects_before_open_breaker():
    state = _state()
    state = ks.with_breakers(
        state,
        tuple(
            ks.CircuitBreakerSnapshot(
                name=b.name,
                state="open",
                consecutive_failures=1,
                last_transition_tick=1,
            )
            if b.name == "provider_plane"
            else b
            for b in state.circuit_breakers
        ),
    )

    verdict = ag.evaluate_one(
        recommendation=replace(_rec(), no_runtime_mutation=False),
        state=state,
        hard_rules=_hard_rules(),
    )

    assert verdict.verdict == "REJECT_HARD"
    assert verdict.blocking_rule_ids == ("action_gate_is_only_exit",)
    assert verdict.breaker_state is None


@pytest.mark.parametrize(
    ("field", "hostile_value"),
    (
        ("lane", _EqBomb()),
        ("kind", _ReprBombStr("not_allowlisted")),
    ),
)
def test_no_runtime_mutation_rejects_before_hostile_route_fields(
    field,
    hostile_value,
):
    verdict = ag.evaluate_one(
        recommendation=replace(
            _rec(),
            no_runtime_mutation=False,
            **{field: hostile_value},
        ),
        state=_state(),
        hard_rules=_hard_rules(),
    )

    assert verdict.verdict == "REJECT_HARD"
    assert verdict.reason == "no_runtime_mutation must be the boolean literal true"
    assert verdict.blocking_rule_ids == ("action_gate_is_only_exit",)
    assert verdict.breaker_state is None


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


def test_batch_counts_fail_closed_rejections():
    report = ag.evaluate_batch(
        recommendations=[
            _rec(kind="noop", lane="bogus_lane", intent="unknown lane"),
            _rec(kind="external_effect", lane="wait", intent="unknown kind"),
            _rec(kind="noop", lane="wait", intent="valid control"),
        ],
        state=_state(),
        hard_rules=_hard_rules(),
    )

    assert report.counts_by_verdict == {
        "ADMIT_TO_LANE": 1,
        "DEFER": 0,
        "REJECT_HARD": 2,
        "REJECT_SOFT": 0,
    }


def test_scheduler_blocks_forged_mission_loaded_from_disk(tmp_path):
    mission_path = tmp_path / "missions.jsonl"
    forged = {
        "schema_version": 1,
        "mission_id": "forged000001",
        "kind": "external_effect",
        "lane": "provider_plane",
        "priority": 1.0,
        "intent": "perform an unallowlisted effect",
        "rationale": "hostile persisted mission",
        "lifecycle_status": "queued",
        "no_runtime_mutation": True,
        "created_tick_id": 1,
        "capsule_context": "neutral_v1",
    }
    mission_path.write_text(
        json.dumps(forged, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    loaded = mq.load_missions(mission_path)
    assert len(loaded) == 1

    report = bg.schedule_one_tick(
        state=_state(),
        missions=loaded,
        hard_rules=_hard_rules(),
    )

    assert report.selected_missions == ()
    assert [m.mission_id for m in report.blocked_missions] == ["forged000001"]
    assert report.gate_report.counts_by_verdict["REJECT_HARD"] == 1
    assert report.gate_report.verdicts[0].reason == (
        "unknown recommendation kind: 'external_effect'"
    )


@pytest.mark.parametrize(
    "forged_value",
    (False, 0, 1, "false", "true", None),
)
def test_scheduler_preserves_invalid_no_runtime_mutation_for_gate_rejection(
    forged_value,
):
    mission = mq.make_mission(
        kind="consultation_request",
        lane="provider_plane",
        priority=1.0,
        intent="verify scheduler invariant preservation",
        rationale="hostile mission must reach the gate without laundering",
        created_tick_id=1,
    )
    forged = replace(mission, no_runtime_mutation=forged_value)

    report = bg.schedule_one_tick(
        state=_state(),
        missions=[forged],
        hard_rules=_hard_rules(),
    )

    assert report.selected_missions == ()
    assert [m.mission_id for m in report.blocked_missions] == [
        forged.mission_id
    ]
    assert report.gate_report.verdicts[0].verdict == "REJECT_HARD"
    assert report.gate_report.verdicts[0].blocking_rule_ids == (
        "action_gate_is_only_exit",
    )


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
