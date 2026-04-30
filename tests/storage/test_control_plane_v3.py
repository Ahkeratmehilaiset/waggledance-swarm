# SPDX-License-Identifier: Apache-2.0
"""Schema-v3 tests for the control plane (Phase 12 self-starting autogrowth)."""

from __future__ import annotations

import json

import pytest

from waggledance.core.storage.control_plane import ControlPlaneDB
from waggledance.core.storage.control_plane_schema import (
    SCHEMA_VERSION,
    all_table_names,
)


@pytest.fixture()
def cp(tmp_path):
    db = ControlPlaneDB(tmp_path / "cp.sqlite")
    db.migrate()
    yield db
    db.close()


def test_schema_v3_tables_present(cp: ControlPlaneDB) -> None:
    assert SCHEMA_VERSION >= 3
    expected = {
        "runtime_gap_signals",
        "growth_intents",
        "autogrowth_queue",
        "autogrowth_runs",
        "growth_events",
    }
    assert expected.issubset(set(all_table_names()))
    counts = cp.stats().table_counts
    for t in expected:
        assert t in counts, f"missing {t} in stats"


def test_record_runtime_gap_signal_round_trip(cp: ControlPlaneDB) -> None:
    sig = cp.record_runtime_gap_signal(
        kind="miss",
        family_kind="lookup_table",
        cell_coord="general",
        signal_payload=json.dumps({"key": "blue", "occurrences": 4}),
        weight=1.5,
    )
    assert sig.id > 0
    assert sig.kind == "miss"
    assert sig.weight == pytest.approx(1.5)
    assert cp.count_runtime_gap_signals() == 1
    assert cp.count_runtime_gap_signals(kind="miss") == 1
    assert cp.count_runtime_gap_signals(kind="fallback") == 0
    assert cp.count_runtime_gap_signals(family_kind="lookup_table") == 1
    assert cp.count_runtime_gap_signals(cell_coord="thermal") == 0


def test_growth_intent_upsert_increments_signal_count(
    cp: ControlPlaneDB,
) -> None:
    sig1 = cp.record_runtime_gap_signal(kind="miss", family_kind="threshold_rule")
    intent_a = cp.upsert_growth_intent(
        family_kind="threshold_rule",
        intent_key="threshold_rule:thermal:hot_threshold",
        cell_coord="thermal",
        priority=5,
        last_signal_id=sig1.id,
    )
    assert intent_a.signal_count == 0  # initial create

    sig2 = cp.record_runtime_gap_signal(kind="miss", family_kind="threshold_rule")
    intent_b = cp.upsert_growth_intent(
        family_kind="threshold_rule",
        intent_key="threshold_rule:thermal:hot_threshold",
        cell_coord="thermal",
        priority=3,  # priority should rise to MAX(5, 3) = 5
        last_signal_id=sig2.id,
    )
    assert intent_b.id == intent_a.id
    assert intent_b.signal_count == 1
    assert intent_b.priority == 5
    assert intent_b.last_signal_id == sig2.id


def test_enqueue_growth_intent_is_idempotent_for_open_rows(
    cp: ControlPlaneDB,
) -> None:
    intent = cp.upsert_growth_intent(
        family_kind="scalar_unit_conversion",
        intent_key="suc:thermal:c_to_k",
        cell_coord="thermal",
    )
    q1 = cp.enqueue_growth_intent(intent.id, priority=10)
    q2 = cp.enqueue_growth_intent(intent.id, priority=10)
    assert q1.id == q2.id  # same row reused

    refreshed = cp.get_growth_intent(intent.id)
    assert refreshed is not None and refreshed.status == "enqueued"


def test_claim_next_queue_row_is_atomic_and_serial(
    cp: ControlPlaneDB,
) -> None:
    """Multiple claimers must not double-claim a single queued row."""

    intent_a = cp.upsert_growth_intent(
        family_kind="lookup_table", intent_key="lt:a"
    )
    intent_b = cp.upsert_growth_intent(
        family_kind="lookup_table", intent_key="lt:b"
    )
    cp.enqueue_growth_intent(intent_a.id, priority=10)
    cp.enqueue_growth_intent(intent_b.id, priority=5)

    first = cp.claim_next_queue_row("scheduler-x")
    assert first is not None and first.priority == 10
    assert first.status == "claimed"

    second = cp.claim_next_queue_row("scheduler-y")
    assert second is not None and second.id != first.id
    assert second.priority == 5

    third = cp.claim_next_queue_row("scheduler-z")
    assert third is None


def test_complete_queue_row_records_backoff(cp: ControlPlaneDB) -> None:
    intent = cp.upsert_growth_intent(
        family_kind="threshold_rule", intent_key="thr:fail"
    )
    q = cp.enqueue_growth_intent(intent.id)
    claimed = cp.claim_next_queue_row("scheduler")
    assert claimed is not None
    finished = cp.complete_queue_row(
        claimed.id,
        status="failed",
        last_error="oracle disagreed",
        backoff_seconds=120,
    )
    assert finished.status == "failed"
    assert finished.last_error == "oracle disagreed"
    assert finished.backoff_until is not None


def test_record_autogrowth_run_persists_outcome(cp: ControlPlaneDB) -> None:
    intent = cp.upsert_growth_intent(
        family_kind="lookup_table", intent_key="lt:done"
    )
    q = cp.enqueue_growth_intent(intent.id)
    run = cp.record_autogrowth_run(
        family_kind="lookup_table",
        outcome="auto_promoted",
        intent_id=intent.id,
        queue_row_id=q.id,
        cell_coord="general",
    )
    assert run.outcome == "auto_promoted"
    listed = cp.list_autogrowth_runs(outcome="auto_promoted")
    assert len(listed) == 1
    assert listed[0].id == run.id


def test_emit_growth_event_is_append_only_history(cp: ControlPlaneDB) -> None:
    """Growth events accumulate; nothing here issues UPDATE/DELETE."""

    cp.emit_growth_event(
        "intent_created",
        entity_kind="growth_intent",
        entity_id=1,
        family_kind="threshold_rule",
        cell_coord="thermal",
        payload=json.dumps({"intent_key": "thr:hot"}),
    )
    cp.emit_growth_event(
        "attempt_completed",
        entity_kind="autogrowth_run",
        entity_id=42,
        family_kind="threshold_rule",
        cell_coord="thermal",
        payload=json.dumps({"outcome": "auto_promoted"}),
    )
    assert cp.count_growth_events() == 2
    assert cp.count_growth_events(event_kind="intent_created") == 1
    assert cp.count_growth_events(family_kind="threshold_rule") == 2
    assert cp.count_growth_events(cell_coord="thermal") == 2


def test_v3_indexes_exist(cp: ControlPlaneDB) -> None:
    rows = cp._conn.execute(  # type: ignore[attr-defined]
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
    ).fetchall()
    names = {r["name"] for r in rows}
    must_have = {
        "idx_runtime_gap_signals_kind",
        "idx_runtime_gap_signals_family_cell",
        "idx_growth_intents_status",
        "idx_growth_intents_family_cell",
        "idx_autogrowth_queue_pending",
        "idx_autogrowth_queue_intent",
        "idx_autogrowth_queue_backoff",
        "idx_autogrowth_runs_intent",
        "idx_autogrowth_runs_outcome",
        "idx_autogrowth_runs_family_cell",
        "idx_growth_events_entity",
        "idx_growth_events_kind",
        "idx_growth_events_family_cell",
    }
    missing = must_have - names
    assert not missing, f"missing v3 indexes: {missing}"


def test_set_growth_intent_status_round_trip(cp: ControlPlaneDB) -> None:
    intent = cp.upsert_growth_intent(
        family_kind="linear_arithmetic", intent_key="lin:a"
    )
    updated = cp.set_growth_intent_status(intent.id, "fulfilled")
    assert updated is not None and updated.status == "fulfilled"


def test_count_growth_intents_by_filter(cp: ControlPlaneDB) -> None:
    cp.upsert_growth_intent(
        family_kind="lookup_table", intent_key="lt:1", cell_coord="general",
    )
    cp.upsert_growth_intent(
        family_kind="lookup_table", intent_key="lt:2", cell_coord="thermal",
    )
    cp.upsert_growth_intent(
        family_kind="threshold_rule", intent_key="thr:1", cell_coord="thermal",
    )
    assert cp.count_growth_intents() == 3
    assert cp.count_growth_intents(family_kind="lookup_table") == 2
    assert cp.count_growth_intents(cell_coord="thermal") == 2
    assert cp.count_growth_intents(
        family_kind="lookup_table", cell_coord="general",
    ) == 1
