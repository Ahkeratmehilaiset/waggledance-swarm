# SPDX-License-Identifier: BUSL-1.1
"""Phase 3 Cat 3 — completion sweep for the last 2 read methods.

After Phase 1-3 covered 24 read methods, only schema_version() and
latest_autonomy_kpi() remained on the old `with self._lock + self._conn`
pattern. This file verifies the same Option B pattern now applies.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from waggledance.core.storage.control_plane import ControlPlaneDB


@pytest.fixture()
def db(tmp_path: Path) -> ControlPlaneDB:
    cp = ControlPlaneDB(tmp_path / "phase3cat3.db")
    yield cp
    cp.close()


def test_schema_version_phase_3_cat_3(db: ControlPlaneDB) -> None:
    # schema_version always returns a positive int after migrate()
    v = db.schema_version()
    assert isinstance(v, int)
    assert v >= 1


def test_latest_autonomy_kpi_phase_3_cat_3(db: ControlPlaneDB) -> None:
    # No KPI snapshots in a fresh DB
    assert db.latest_autonomy_kpi() is None


def test_phase_3_cat_3_reads_do_not_wait_for_global_lock(
    db: ControlPlaneDB,
) -> None:
    ready = threading.Event()
    go = threading.Event()
    finished = threading.Event()
    errors: list[BaseException] = []

    def reader() -> None:
        try:
            assert db.schema_version() >= 1
            assert db.latest_autonomy_kpi() is None
            ready.set()
            assert go.wait(1.0)
            # while main thread holds self._lock, these reads must not block
            assert db.schema_version() >= 1
            assert db.latest_autonomy_kpi() is None
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
