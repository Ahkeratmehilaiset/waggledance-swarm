# SPDX-License-Identifier: Apache-2.0
"""Self-starting gap intake tests (Phase 12)."""

from __future__ import annotations

import json

import pytest

from waggledance.core.autonomy_growth.gap_intake import (
    GapSignal,
    RuntimeGapDetector,
    digest_signals_into_intents,
)
from waggledance.core.storage.control_plane import ControlPlaneDB


@pytest.fixture()
def cp(tmp_path):
    db = ControlPlaneDB(tmp_path / "cp.sqlite")
    db.migrate()
    yield db
    db.close()


def test_runtime_gap_detector_records_signals_and_events(cp: ControlPlaneDB) -> None:
    det = RuntimeGapDetector(cp)
    rec = det.record(GapSignal(
        kind="miss",
        family_kind="lookup_table",
        cell_coord="general",
        intent_seed="color_to_action",
        weight=1.0,
        payload={"key": "blue", "occurrences": 3},
    ))
    assert rec.id > 0
    # signal persisted
    assert cp.count_runtime_gap_signals(kind="miss") == 1
    # growth event mirrored
    assert cp.count_growth_events(event_kind="signal_recorded") == 1
    # stats reflect the call
    assert det.stats.signals_recorded == 1
    assert det.stats.by_family["lookup_table"] == 1


def test_digest_skips_non_low_risk_families(cp: ControlPlaneDB) -> None:
    det = RuntimeGapDetector(cp)
    s_in = det.record(GapSignal(
        kind="miss", family_kind="scalar_unit_conversion",
        cell_coord="thermal", intent_seed="c_to_k",
        spec_seed={"spec": {"factor": 1.0}},
    ))
    s_out = det.record(GapSignal(
        kind="miss", family_kind="temporal_window_rule",  # excluded
        cell_coord="thermal", intent_seed="window",
    ))
    stats = digest_signals_into_intents(
        cp,
        candidate_signals=[
            GapSignal(kind="miss", family_kind="scalar_unit_conversion",
                       cell_coord="thermal", intent_seed="c_to_k",
                       spec_seed={"spec": {"factor": 1.0}}),
            GapSignal(kind="miss", family_kind="temporal_window_rule",
                       cell_coord="thermal", intent_seed="window"),
        ],
    )
    # one in (low-risk), one out (excluded)
    assert stats.intents_created == 1
    intents = cp.count_growth_intents()
    assert intents == 1


def test_digest_creates_and_enqueues_low_risk_intent(cp: ControlPlaneDB) -> None:
    sigs = [
        GapSignal(
            kind="miss", family_kind="threshold_rule",
            cell_coord="thermal", intent_seed="hot_threshold",
            weight=1.0,
            spec_seed={
                "spec": {"threshold": 30.0, "operator": ">",
                          "true_label": "hot", "false_label": "cool"},
                "validation_cases": [
                    {"inputs": {"x": 50}, "expected": "hot"},
                    {"inputs": {"x": 10}, "expected": "cool"},
                ],
                "shadow_samples": [{"x": float(i)} for i in range(-5, 50)],
                "solver_name_seed": "hot_threshold",
            },
        )
        for _ in range(3)
    ]
    stats = digest_signals_into_intents(
        cp, candidate_signals=sigs, min_signals_per_intent=1, autoenqueue=True,
    )
    assert stats.intents_created == 1
    assert stats.intents_enqueued == 1
    assert cp.count_queue_rows(status="queued") == 1
    # event mirror
    assert cp.count_growth_events(event_kind="intent_created") == 1
    assert cp.count_growth_events(event_kind="intent_enqueued") == 1


def test_digest_min_signals_threshold(cp: ControlPlaneDB) -> None:
    """Below-threshold signal counts must NOT auto-enqueue."""

    sig = GapSignal(
        kind="miss", family_kind="lookup_table",
        cell_coord="general", intent_seed="x",
        spec_seed={"spec": {"table": {"a": 1}, "default": 0}},
    )
    stats = digest_signals_into_intents(
        cp, candidate_signals=[sig],
        min_signals_per_intent=3, autoenqueue=True,
    )
    assert stats.intents_created == 0
    assert cp.count_queue_rows() == 0


def test_intent_keys_collide_into_one_row(cp: ControlPlaneDB) -> None:
    """Same (family, cell, seed) triple → one intent, signal_count grows."""

    sigs = [
        GapSignal(
            kind="miss", family_kind="lookup_table",
            cell_coord="general", intent_seed="color",
            spec_seed={"spec": {"table": {"a": 1}, "default": 0}},
        ),
        GapSignal(
            kind="miss", family_kind="lookup_table",
            cell_coord="general", intent_seed="color",
            spec_seed={"spec": {"table": {"a": 1, "b": 2}, "default": 0}},
        ),
    ]
    digest_signals_into_intents(cp, candidate_signals=sigs, autoenqueue=False)
    intents = cp.count_growth_intents()
    assert intents == 1
    # The second seed wins (latest spec_seed)
    intent = next(iter(cp.list_family_policies()), None)  # noqa
    rows = cp._conn.execute(  # type: ignore[attr-defined]
        "SELECT * FROM growth_intents"
    ).fetchall()
    assert len(rows) == 1
    seed = json.loads(rows[0]["spec_seed_json"])
    assert "b" in seed["spec"]["table"]
