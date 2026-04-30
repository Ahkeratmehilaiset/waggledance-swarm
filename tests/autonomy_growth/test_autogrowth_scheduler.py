# SPDX-License-Identifier: Apache-2.0
"""AutogrowthScheduler.tick() tests (Phase 12)."""

from __future__ import annotations

import json

import pytest

from waggledance.core.autonomy_growth import (
    AutogrowthScheduler,
    DispatchQuery,
    GapSignal,
    LowRiskGrower,
    LowRiskSolverDispatcher,
    OUTCOME_AUTO_PROMOTED,
    OUTCOME_FAMILY_NOT_LOW_RISK,
    OUTCOME_NO_INTENT,
    OUTCOME_REJECTED,
    OUTCOME_SPEC_INVALID,
    RuntimeGapDetector,
    digest_signals_into_intents,
)
from waggledance.core.storage.control_plane import ControlPlaneDB


@pytest.fixture()
def cp(tmp_path):
    db = ControlPlaneDB(tmp_path / "cp.sqlite")
    db.migrate()
    grower = LowRiskGrower(db)
    grower.ensure_low_risk_policies()
    yield db
    db.close()


def _scalar_seed(seed_name: str = "celsius_to_kelvin") -> dict:
    return {
        "spec": {"from_unit": "C", "to_unit": "K",
                  "factor": 1.0, "offset": 273.15},
        "validation_cases": [
            {"inputs": {"x": 0.0}, "expected": 273.15},
            {"inputs": {"x": 100.0}, "expected": 373.15},
        ],
        "shadow_samples": [{"x": float(i)} for i in range(20)],
        "solver_name_seed": seed_name,
        "cell_id": "thermal",
        "source": "test_self_starting",
        "source_kind": "test_seed",
    }


def _seed_intent(cp: ControlPlaneDB, family: str, key: str, seed: dict,
                 cell: str = "thermal", priority: int = 10) -> int:
    intent = cp.upsert_growth_intent(
        family_kind=family, intent_key=key, cell_coord=cell, priority=priority,
        spec_seed_json=json.dumps(seed, sort_keys=True),
    )
    cp.enqueue_growth_intent(intent.id, priority=priority)
    return intent.id


def test_tick_idle_when_queue_empty(cp: ControlPlaneDB) -> None:
    sched = AutogrowthScheduler(cp)
    result = sched.tick()
    assert result.claimed is False
    assert sched.stats.ticks_idle == 1


def test_tick_promotes_a_queued_low_risk_intent(cp: ControlPlaneDB) -> None:
    intent_id = _seed_intent(
        cp, "scalar_unit_conversion", "suc:thermal:c_to_k",
        _scalar_seed("celsius_to_kelvin"),
    )
    sched = AutogrowthScheduler(cp)
    result = sched.tick()
    assert result.claimed is True
    assert result.outcome == OUTCOME_AUTO_PROMOTED
    assert result.solver_id is not None
    assert result.intent_id == intent_id

    # Solver row + artifact persisted by the engine
    solvers = list(cp.iter_solvers(status="auto_promoted"))
    assert len(solvers) == 1

    # autogrowth_runs row recorded
    runs = cp.list_autogrowth_runs(outcome=OUTCOME_AUTO_PROMOTED)
    assert len(runs) == 1

    # growth_event mirror
    assert cp.count_growth_events(event_kind="solver_auto_promoted") == 1

    # Intent moved to fulfilled
    intent = cp.get_growth_intent(intent_id)
    assert intent.status == "fulfilled"

    # Queue row completed (not 'claimed' anymore)
    queue_rows = cp.list_autogrowth_queue()
    assert all(r.status != "queued" for r in queue_rows)


def test_runtime_dispatcher_serves_self_promoted_solver(
    cp: ControlPlaneDB,
) -> None:
    _seed_intent(
        cp, "scalar_unit_conversion", "suc:thermal:c_to_k",
        _scalar_seed("celsius_to_kelvin"),
    )
    AutogrowthScheduler(cp).tick()

    disp = LowRiskSolverDispatcher(cp)
    res = disp.dispatch(DispatchQuery(
        family_kind="scalar_unit_conversion", inputs={"x": 0.0}
    ))
    assert res.matched is True
    assert res.output == pytest.approx(273.15)


def test_run_until_idle_drains_multiple_queued_intents(
    cp: ControlPlaneDB,
) -> None:
    _seed_intent(
        cp, "scalar_unit_conversion", "suc:thermal:c_to_k",
        _scalar_seed("celsius_to_kelvin"),
    )
    _seed_intent(
        cp, "lookup_table", "lt:general:color",
        {
            "spec": {"table": {"red": "stop", "green": "go"},
                       "default": "wait"},
            "validation_cases": [
                {"inputs": {"key": "red"}, "expected": "stop"},
                {"inputs": {"key": "green"}, "expected": "go"},
                {"inputs": {"key": "x"}, "expected": "wait"},
            ],
            "shadow_samples": [{"key": k} for k in
                                ("red", "green", "blue", "yellow", "red", "x")],
            "solver_name_seed": "color_to_action",
            "cell_id": "general",
        }, cell="general",
    )
    sched = AutogrowthScheduler(cp)
    drained = sched.run_until_idle(max_ticks=10)
    assert drained == 2
    assert sched.stats.auto_promoted == 2
    assert cp.count_solvers(status="auto_promoted") == 2


def test_tick_rejects_outside_allowlist_terminally(cp: ControlPlaneDB) -> None:
    intent = cp.upsert_growth_intent(
        family_kind="temporal_window_rule",  # excluded
        intent_key="twr:thermal:x",
        cell_coord="thermal",
        priority=5,
        spec_seed_json=json.dumps({"spec": {}}),
    )
    cp.enqueue_growth_intent(intent.id, priority=5)

    sched = AutogrowthScheduler(cp)
    result = sched.tick()
    assert result.outcome == OUTCOME_FAMILY_NOT_LOW_RISK
    final_intent = cp.get_growth_intent(intent.id)
    assert final_intent.status == "rejected"


def test_tick_rejects_bad_seed_terminally(cp: ControlPlaneDB) -> None:
    intent = cp.upsert_growth_intent(
        family_kind="lookup_table",
        intent_key="lt:bad",
        cell_coord="general",
        priority=1,
        spec_seed_json=json.dumps({"oops": "no spec"}),
    )
    cp.enqueue_growth_intent(intent.id)
    sched = AutogrowthScheduler(cp)
    result = sched.tick()
    assert result.outcome in (OUTCOME_REJECTED, OUTCOME_SPEC_INVALID, "bad_seed")
    final = cp.get_growth_intent(intent.id)
    assert final.status == "rejected"


def test_self_starting_loop_signal_to_promotion(cp: ControlPlaneDB) -> None:
    """End-to-end: detector records a signal -> digest creates intent +
    enqueues -> scheduler.tick() promotes -> dispatcher serves it."""

    detector = RuntimeGapDetector(cp)
    seed = _scalar_seed("celsius_to_kelvin")
    sig = GapSignal(
        kind="miss", family_kind="scalar_unit_conversion",
        cell_coord="thermal", intent_seed="c_to_k",
        spec_seed=seed,
        weight=2.0,
    )
    detector.record(sig)

    digest_stats = digest_signals_into_intents(
        cp, candidate_signals=[sig],
        min_signals_per_intent=1, autoenqueue=True,
    )
    assert digest_stats.intents_created == 1
    assert digest_stats.intents_enqueued == 1

    sched = AutogrowthScheduler(cp)
    result = sched.tick()
    assert result.outcome == OUTCOME_AUTO_PROMOTED

    # Runtime dispatcher confirms the lane works post-self-start
    disp = LowRiskSolverDispatcher(cp)
    served = disp.dispatch(DispatchQuery(
        family_kind="scalar_unit_conversion", inputs={"x": 25.0}
    ))
    assert served.matched is True
    assert served.output == pytest.approx(25.0 + 273.15)


def test_scheduler_id_distinguishable(cp: ControlPlaneDB) -> None:
    a = AutogrowthScheduler(cp, scheduler_id="scheduler-A")
    b = AutogrowthScheduler(cp, scheduler_id="scheduler-B")
    assert a.scheduler_id != b.scheduler_id


def test_two_schedulers_do_not_double_claim(cp: ControlPlaneDB) -> None:
    _seed_intent(
        cp, "scalar_unit_conversion", "k1",
        _scalar_seed("a"), priority=10,
    )
    _seed_intent(
        cp, "scalar_unit_conversion", "k2",
        _scalar_seed("b"), priority=10,
    )
    a = AutogrowthScheduler(cp, scheduler_id="A")
    b = AutogrowthScheduler(cp, scheduler_id="B")
    r1 = a.tick()
    r2 = b.tick()
    assert r1.claimed and r2.claimed
    assert r1.queue_row_id != r2.queue_row_id
