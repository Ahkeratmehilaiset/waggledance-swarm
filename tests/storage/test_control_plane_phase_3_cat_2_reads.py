# SPDX-License-Identifier: BUSL-1.1
"""Phase 3 Category 2 — multi-statement / dynamic-SQL read methods.

These methods build dynamic SQL based on optional filter parameters
before executing a single read. They follow the same Phase 1 / Phase 2
pattern: build sql + params, then `conn = self._read_conn()`, then
`if self._in_transaction: with self._lock: ... else: ...`.

Methods covered:
- list_family_policies
- get_shadow_evaluation
- list_promotion_decisions
- get_solver_capability_features
- list_autogrowth_queue
- list_autogrowth_runs
- count_growth_events
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from waggledance.core.storage.control_plane import ControlPlaneDB


@pytest.fixture()
def db(tmp_path: Path) -> ControlPlaneDB:
    cp = ControlPlaneDB(tmp_path / "phase3cat2.db")
    yield cp
    cp.close()


def test_list_family_policies_phase_3(db: ControlPlaneDB) -> None:
    # method exists and returns a list (may be empty in clean DB)
    out = db.list_family_policies()
    assert isinstance(out, list)
    out2 = db.list_family_policies(low_risk_only=True)
    assert isinstance(out2, list)


def test_get_shadow_evaluation_phase_3(db: ControlPlaneDB) -> None:
    assert db.get_shadow_evaluation(999_999) is None


def test_list_promotion_decisions_phase_3(db: ControlPlaneDB) -> None:
    out = db.list_promotion_decisions()
    assert isinstance(out, list)
    out2 = db.list_promotion_decisions(family_kind="x", decision="approved", limit=5)
    assert isinstance(out2, list)


def test_get_solver_capability_features_phase_3(db: ControlPlaneDB) -> None:
    out = db.get_solver_capability_features(999_999)
    assert out == []


def test_list_autogrowth_queue_phase_3(db: ControlPlaneDB) -> None:
    out = db.list_autogrowth_queue()
    assert isinstance(out, list)
    out2 = db.list_autogrowth_queue(status="ready", limit=10)
    assert isinstance(out2, list)


def test_list_autogrowth_runs_phase_3(db: ControlPlaneDB) -> None:
    out = db.list_autogrowth_runs()
    assert isinstance(out, list)
    out2 = db.list_autogrowth_runs(outcome="success", family_kind="x", limit=5)
    assert isinstance(out2, list)


def test_count_growth_events_phase_3(db: ControlPlaneDB) -> None:
    assert db.count_growth_events() == 0
    assert db.count_growth_events(event_kind="x") == 0


def test_phase_3_cat_2_reads_do_not_wait_for_global_lock(db: ControlPlaneDB) -> None:
    ready = threading.Event()
    go = threading.Event()
    finished = threading.Event()
    errors: list[BaseException] = []

    def reader() -> None:
        try:
            assert isinstance(db.list_family_policies(), list)
            assert isinstance(db.list_promotion_decisions(), list)
            assert isinstance(db.list_autogrowth_queue(), list)
            assert isinstance(db.list_autogrowth_runs(), list)
            assert db.count_growth_events() == 0
            ready.set()
            assert go.wait(1.0)
            assert isinstance(db.list_family_policies(low_risk_only=True), list)
            assert isinstance(db.list_promotion_decisions(decision="approved"), list)
            assert isinstance(db.list_autogrowth_queue(status="ready"), list)
            assert isinstance(db.list_autogrowth_runs(outcome="success"), list)
            assert db.count_growth_events(event_kind="probe") == 0
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            finished.set()

    thread = threading.Thread(target=reader)
    thread.start()
    assert ready.wait(2.0)

    with db._lock:  # noqa: SLF001 - pins regression
        go.set()
        assert finished.wait(2.0)

    thread.join(timeout=1.0)
    assert not errors, f"reader saw errors: {errors}"
