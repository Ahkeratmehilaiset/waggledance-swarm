from __future__ import annotations

import threading
from pathlib import Path

import pytest

from waggledance.core.storage.control_plane import ControlPlaneDB, ControlPlaneError


@pytest.fixture()
def db(tmp_path: Path) -> ControlPlaneDB:
    cp = ControlPlaneDB(tmp_path / "cp.db")
    yield cp
    cp.close()


def _seed_hot_path_rows(db: ControlPlaneDB) -> None:
    db.upsert_solver_family("fam", "1.0")
    db.upsert_solver("solver", "1.0", family_name="fam")
    db.bind_runtime_path("default", "faiss_root", "data/faiss")
    db.record_runtime_gap_signal(kind="runtime_miss", cell_coord="hub")


def test_hot_read_methods_do_not_wait_for_global_lock(db: ControlPlaneDB) -> None:
    _seed_hot_path_rows(db)
    ready = threading.Event()
    go = threading.Event()
    finished = threading.Event()
    errors: list[BaseException] = []

    def reader() -> None:
        try:
            # Warm this thread's read-only connection before the writer lock
            # is held. The production hot path pays this once per thread.
            assert db.get_solver("solver") is not None
            assert db.get_active_runtime_path("default", "faiss_root") is not None
            assert db.count_runtime_gap_signals() == 1
            assert db.list_runtime_gap_signals(limit=1)
            ready.set()
            assert go.wait(1.0)

            solver = db.get_solver("solver")
            path = db.get_active_runtime_path("default", "faiss_root")
            count = db.count_runtime_gap_signals()
            signals = db.list_runtime_gap_signals(limit=1)

            assert solver is not None and solver.name == "solver"
            assert path is not None and path.physical_path == "data/faiss"
            assert count == 1
            assert len(signals) == 1
        except BaseException as exc:  # pragma: no cover - re-raised in main thread
            errors.append(exc)
        finally:
            finished.set()

    thread = threading.Thread(target=reader)
    thread.start()
    assert ready.wait(1.0)

    with db._lock:  # noqa: SLF001 - this pins the old global-lock regression.
        go.set()
        assert finished.wait(1.0)

    thread.join(1.0)
    if errors:
        raise errors[0]


def test_transaction_reads_stay_on_writer_connection(db: ControlPlaneDB) -> None:
    with db.transaction():
        db.upsert_solver_family("fam", "1.0")
        db.upsert_solver("pending", "1.0", family_name="fam")
        db.record_runtime_gap_signal(kind="runtime_miss", cell_coord="hub")

        solver = db.get_solver("pending")
        count = db.count_runtime_gap_signals(kind="runtime_miss")
        signals = db.list_runtime_gap_signals(kind="runtime_miss")

        assert solver is not None and solver.name == "pending"
        assert count == 1
        assert len(signals) == 1


def test_close_closes_thread_local_read_connections(db: ControlPlaneDB) -> None:
    _seed_hot_path_rows(db)
    assert db.get_solver("solver") is not None
    assert len(db._read_conns) == 1  # noqa: SLF001

    db.close()

    assert db._read_conns == []  # noqa: SLF001
    with pytest.raises(ControlPlaneError, match="closed"):
        db.get_solver("solver")
