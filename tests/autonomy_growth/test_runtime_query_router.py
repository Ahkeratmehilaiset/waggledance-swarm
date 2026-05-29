# SPDX-License-Identifier: Apache-2.0
"""RuntimeQueryRouter tests (Phase 13 P2)."""

from __future__ import annotations

import pytest

from waggledance.core.autonomy_growth import (
    AutogrowthScheduler,
    GapSignal,
    LowRiskGrower,
    RuntimeGapDetector,
    RuntimeQuery,
    RuntimeQueryRouter,
    digest_signals_into_intents,
)
from waggledance.core.storage.control_plane import ControlPlaneDB


@pytest.fixture()
def cp(tmp_path):
    db = ControlPlaneDB(tmp_path / "cp.sqlite")
    db.migrate()
    g = LowRiskGrower(db)
    g.ensure_low_risk_policies()
    yield db
    db.close()


def _seed_one_promoted(cp: ControlPlaneDB, family: str, name: str,
                        spec_seed: dict, cell: str = "thermal") -> None:
    detector = RuntimeGapDetector(cp)
    sig = GapSignal(
        kind="runtime_miss", family_kind=family,
        cell_coord=cell, intent_seed=name, spec_seed=spec_seed,
    )
    detector.record(sig)
    digest_signals_into_intents(
        cp, candidate_signals=[sig],
        min_signals_per_intent=1, autoenqueue=True,
    )
    AutogrowthScheduler(cp).run_until_idle()


def _kelvin_seed(name: str = "celsius_to_kelvin") -> dict:
    return {
        "spec": {"from_unit": "C", "to_unit": "K",
                  "factor": 1.0, "offset": 273.15},
        "validation_cases": [
            {"inputs": {"x": 0.0}, "expected": 273.15},
            {"inputs": {"x": 100.0}, "expected": 373.15},
        ],
        "shadow_samples": [{"x": float(i)} for i in range(10)],
        "solver_name_seed": name,
        "cell_id": "thermal",
    }


def test_router_serves_query_when_solver_already_promoted(
    cp: ControlPlaneDB,
) -> None:
    _seed_one_promoted(cp, "scalar_unit_conversion", "celsius_to_kelvin",
                        _kelvin_seed(), cell="thermal")
    router = RuntimeQueryRouter(cp)
    res = router.route(RuntimeQuery(
        family_kind="scalar_unit_conversion",
        inputs={"x": 0.0},
        cell_coord="thermal",
    ))
    assert res.served is True
    assert res.source == "auto_promoted_solver"
    assert res.output == pytest.approx(273.15)
    assert router.stats.served_total == 1
    assert router.stats.miss_total == 0


def test_router_emits_gap_signal_on_miss(cp: ControlPlaneDB) -> None:
    """No solver promoted yet -> route emits a runtime_gap_signal
    automatically (no human, no caller-side detector call)."""

    router = RuntimeQueryRouter(cp)
    res = router.route(RuntimeQuery(
        family_kind="scalar_unit_conversion",
        inputs={"x": 0.0},
        cell_coord="thermal",
        intent_seed="c_to_k",
        spec_seed=_kelvin_seed(),
    ))
    assert res.served is False
    assert res.source == "gap_emitted"
    assert res.signal_id is not None
    # Signal was persisted automatically
    assert cp.count_runtime_gap_signals(kind="runtime_miss") == 1
    assert cp.count_growth_events(event_kind="signal_recorded") == 1


def test_router_throttles_repeated_misses(cp: ControlPlaneDB) -> None:
    """Hot loop must not flood runtime_gap_signals."""

    fake_now = [0.0]

    def clock() -> float:
        return fake_now[0]

    router = RuntimeQueryRouter(
        cp, min_signal_interval_seconds=2.0, clock=clock,
    )
    q = RuntimeQuery(
        family_kind="scalar_unit_conversion",
        inputs={"x": 0.0},
        cell_coord="thermal",
        intent_seed="c_to_k",
        spec_seed=_kelvin_seed(),
    )
    res1 = router.route(q)
    res2 = router.route(q)
    res3 = router.route(q)
    assert res1.source == "gap_emitted"
    assert res2.source == "gap_throttled"
    assert res3.source == "gap_throttled"
    assert cp.count_runtime_gap_signals(kind="runtime_miss") == 1

    # Advance the clock past the throttle window
    fake_now[0] = 5.0
    res4 = router.route(q)
    assert res4.source == "gap_emitted"
    assert cp.count_runtime_gap_signals(kind="runtime_miss") == 2


def test_router_refuses_out_of_envelope_family(cp: ControlPlaneDB) -> None:
    router = RuntimeQueryRouter(cp)
    res = router.route(RuntimeQuery(
        family_kind="temporal_window_rule",  # excluded
        inputs={"x": 1},
    ))
    assert res.served is False
    assert res.source == "family_not_low_risk"
    # Out-of-envelope queries do NOT write signals — autonomy is
    # bounded to the allowlist.
    assert cp.count_runtime_gap_signals() == 0


def test_router_end_to_end_runtime_then_harvest_then_serve(
    cp: ControlPlaneDB,
) -> None:
    """Round-trip: query misses -> signal emitted -> harvest cycle
    runs -> next query of same class is served."""

    router = RuntimeQueryRouter(cp, min_signal_interval_seconds=0.0)
    q_first = RuntimeQuery(
        family_kind="scalar_unit_conversion",
        inputs={"x": 25.0},
        cell_coord="thermal",
        intent_seed="celsius_to_kelvin",
        spec_seed=_kelvin_seed(),
    )
    res_before = router.route(q_first)
    assert res_before.served is False
    assert res_before.source == "gap_emitted"

    # Harvest cycle: digest signals into intents and run scheduler
    digest_signals_into_intents(
        cp, candidate_signals=[GapSignal(
            kind="runtime_miss", family_kind="scalar_unit_conversion",
            cell_coord="thermal", intent_seed="celsius_to_kelvin",
            weight=1.0, spec_seed=_kelvin_seed(),
        )], min_signals_per_intent=1, autoenqueue=True,
    )
    drained = AutogrowthScheduler(cp).run_until_idle()
    assert drained == 1

    # Second query — same class — now hits the dispatcher
    res_after = router.route(q_first)
    assert res_after.served is True
    assert res_after.source == "auto_promoted_solver"
    assert res_after.output == pytest.approx(25.0 + 273.15)


def test_router_intent_seed_deduplicates_by_query_shape(
    cp: ControlPlaneDB,
) -> None:
    """Two different runtime values with the same key set produce one
    intent (auto-derived intent_seed = shape signature)."""

    fake_now = [0.0]

    def clock() -> float:
        return fake_now[0]

    router = RuntimeQueryRouter(
        cp, min_signal_interval_seconds=0.0, clock=clock,
    )
    q1 = RuntimeQuery(
        family_kind="scalar_unit_conversion", inputs={"x": 1.0},
        cell_coord="thermal", spec_seed=_kelvin_seed(),
    )
    q2 = RuntimeQuery(
        family_kind="scalar_unit_conversion", inputs={"x": 99.0},
        cell_coord="thermal", spec_seed=_kelvin_seed(),
    )
    router.route(q1)
    router.route(q2)
    # Two signals (different runtime values), but only one intent_key
    # because the auto-derived intent_seed is the shape signature.
    assert cp.count_runtime_gap_signals() == 2
    digest_signals_into_intents(
        cp,
        candidate_signals=[
            GapSignal(kind="runtime_miss",
                       family_kind="scalar_unit_conversion",
                       cell_coord="thermal",
                       intent_seed="x",  # matches auto-derived
                       spec_seed=_kelvin_seed())
            for _ in range(2)
        ],
        min_signals_per_intent=1, autoenqueue=False,
    )
    # One intent regardless of how many shape-equivalent signals fed it
    assert cp.count_growth_intents() == 1
