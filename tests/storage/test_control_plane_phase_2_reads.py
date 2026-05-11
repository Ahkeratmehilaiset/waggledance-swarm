# SPDX-License-Identifier: BUSL-1.1
"""Tests for Phase 2 Option B coverage: the additional read methods that
were extended from PR #223's get_solver / get_active_runtime_path /
list_runtime_gap_signals / count_runtime_gap_signals patches.

Verifies (a) each method works under normal use, (b) reads inside a
transaction still see uncommitted writes (transaction safety preserved),
and (c) reads outside a transaction do not require self._lock (the
read-conn fast path is used).
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from waggledance.core.storage.control_plane import ControlPlaneDB


@pytest.fixture()
def db(tmp_path: Path) -> ControlPlaneDB:
    cp = ControlPlaneDB(tmp_path / "phase2.db")
    yield cp
    cp.close()


def _seed_basic_rows(db: ControlPlaneDB) -> None:
    db.upsert_solver_family("fam_a", "1.0", description="A", status="active")
    db.upsert_solver_family("fam_b", "1.0", description="B", status="active")
    db.upsert_solver("solver_x", "1.0", family_name="fam_a", status="active")
    db.upsert_solver("solver_y", "1.0", family_name="fam_a", status="auto_promoted")
    db.bind_runtime_path("logical_x", "kind_x", "/path/x")
    db.bind_runtime_path("logical_y", "kind_y", "/path/y")
    db.set_meta("test_key", "test_value")
    db.record_runtime_gap_signal(
        "kind_gap", family_kind="fam_a", cell_coord="hub",
        signal_payload="{}", weight=1.0,
    )


# ---- baseline correctness ---------------------------------------------------


def test_list_solver_families_phase_2(db: ControlPlaneDB) -> None:
    _seed_basic_rows(db)
    families = db.list_solver_families()
    assert {f.name for f in families} == {"fam_a", "fam_b"}


def test_get_solver_name_phase_2(db: ControlPlaneDB) -> None:
    _seed_basic_rows(db)
    sx = db.get_solver("solver_x")
    assert sx is not None
    assert db.get_solver_name(sx.id) == "solver_x"
    assert db.get_solver_name(999_999) is None


def test_count_solvers_phase_2(db: ControlPlaneDB) -> None:
    _seed_basic_rows(db)
    assert db.count_solvers() == 2
    assert db.count_solvers(status="auto_promoted") == 1


def test_list_active_runtime_paths_phase_2(db: ControlPlaneDB) -> None:
    _seed_basic_rows(db)
    paths = db.list_active_runtime_paths()
    assert {(p.logical_name, p.path_kind) for p in paths} == {
        ("logical_x", "kind_x"),
        ("logical_y", "kind_y"),
    }


def test_count_auto_promoted_for_family_phase_2(db: ControlPlaneDB) -> None:
    _seed_basic_rows(db)
    assert db.count_auto_promoted_for_family("fam_a") == 1
    assert db.count_auto_promoted_for_family("fam_b") == 0


def test_get_meta_phase_2(db: ControlPlaneDB) -> None:
    _seed_basic_rows(db)
    assert db.get_meta("test_key") == "test_value"
    assert db.get_meta("missing_key") is None


# ---- concurrency: read does not block while writer holds self._lock ---------


def test_phase_2_reads_do_not_wait_for_global_lock(db: ControlPlaneDB) -> None:
    _seed_basic_rows(db)
    ready = threading.Event()
    go = threading.Event()
    finished = threading.Event()
    errors: list[BaseException] = []

    def reader() -> None:
        try:
            assert db.list_solver_families()
            assert db.list_active_runtime_paths()
            assert db.get_meta("test_key") == "test_value"
            assert db.count_solvers() == 2
            ready.set()
            assert go.wait(1.0)
            # While main thread holds self._lock, these must NOT block.
            assert len(db.list_solver_families()) == 2
            assert len(db.list_active_runtime_paths()) == 2
            assert db.get_meta("test_key") == "test_value"
            assert db.count_solvers() == 2
            assert db.count_auto_promoted_for_family("fam_a") == 1
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


# ---- transaction safety: in-tx reads still see uncommitted writes ----------


def test_phase_2_reads_inside_transaction_see_uncommitted_writes(
    db: ControlPlaneDB,
) -> None:
    db.upsert_solver_family("base_fam", "1.0", status="active")

    with db.transaction():
        db.upsert_solver_family("new_fam", "1.0", status="active")
        # Inside the writer's transaction, list_solver_families must include
        # the just-inserted but not-yet-committed row.
        names = {f.name for f in db.list_solver_families()}
        assert "new_fam" in names, (
            "list_solver_families inside transaction did not see "
            "uncommitted write — read path lost transaction visibility"
        )
        assert db.count_solvers() == 0  # solvers table still empty
        db.set_meta("tx_key", "tx_value")
        assert db.get_meta("tx_key") == "tx_value", (
            "get_meta inside transaction did not see uncommitted write"
        )
