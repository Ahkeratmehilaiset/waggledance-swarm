# SPDX-License-Identifier: BUSL-1.1
"""Phase 3 Category 1 — helper-routed reads (Option B coverage).

The public `get_solver_family` and `get_family_policy` route through
private helpers `_fetch_one_solver_family` and `_fetch_family_policy`.
Phase 3 Cat 1 redesigns each helper to accept an optional `conn`
parameter so callers can pass the thread-local read connection
(bypassing self._lock) while writer-path callers continue to use
the default self._conn under their own self._lock.

This file verifies:
- Backwards compatibility — writer-path upsert() still works (helper
  default behavior unchanged).
- Reader-path get_solver_family / get_family_policy use the read
  connection and do not block when self._lock is held.
- In-transaction reads still see uncommitted writes.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from waggledance.core.storage.control_plane import ControlPlaneDB


@pytest.fixture()
def db(tmp_path: Path) -> ControlPlaneDB:
    cp = ControlPlaneDB(tmp_path / "phase3cat1.db")
    yield cp
    cp.close()


def test_get_solver_family_phase_3_cat_1(db: ControlPlaneDB) -> None:
    assert db.get_solver_family("nonexistent") is None
    db.upsert_solver_family("fam_x", "1.0", description="X", status="active")
    fam = db.get_solver_family("fam_x")
    assert fam is not None
    assert fam.name == "fam_x"


def test_get_family_policy_phase_3_cat_1(db: ControlPlaneDB) -> None:
    assert db.get_family_policy("nonexistent_kind") is None
    db.upsert_family_policy(
        "kind_x",
        is_low_risk=True,
        max_auto_promote=10,
        min_validation_pass_rate=0.8,
        min_shadow_samples=5,
        min_shadow_agreement_rate=0.7,
    )
    pol = db.get_family_policy("kind_x")
    assert pol is not None
    assert pol.family_kind == "kind_x"


def test_phase_3_cat_1_helper_readers_do_not_block_under_lock(
    db: ControlPlaneDB,
) -> None:
    db.upsert_solver_family("fam_a", "1.0", description="A", status="active")
    db.upsert_family_policy(
        "kind_a",
        is_low_risk=True,
        max_auto_promote=5,
        min_validation_pass_rate=0.9,
        min_shadow_samples=3,
        min_shadow_agreement_rate=0.85,
    )

    ready = threading.Event()
    go = threading.Event()
    finished = threading.Event()
    errors: list[BaseException] = []

    def reader() -> None:
        try:
            assert db.get_solver_family("fam_a") is not None
            assert db.get_family_policy("kind_a") is not None
            ready.set()
            assert go.wait(1.0)
            # main thread is now holding self._lock — these must not block
            assert db.get_solver_family("fam_a").name == "fam_a"
            assert db.get_family_policy("kind_a").family_kind == "kind_a"
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


def test_phase_3_cat_1_in_transaction_sees_uncommitted_writes(
    db: ControlPlaneDB,
) -> None:
    db.upsert_solver_family("base_fam", "1.0", status="active")
    with db.transaction():
        db.upsert_solver_family("new_fam", "1.0", status="active")
        fam = db.get_solver_family("new_fam")
        assert fam is not None, (
            "get_solver_family inside transaction did not see "
            "uncommitted write — transaction visibility broken"
        )
        assert fam.name == "new_fam"


def test_phase_3_cat_1_helper_default_conn_still_works(db: ControlPlaneDB) -> None:
    """The helper's conn=None fallback to self._conn must still work
    (writer-side callers depend on this)."""
    db.upsert_solver_family("writer_fam", "1.0", status="active")
    # Direct helper call WITHOUT explicit conn arg — should use self._conn
    fam = db._fetch_one_solver_family("writer_fam", raise_if_missing=False)
    assert fam is not None
    assert fam.name == "writer_fam"
    # raise_if_missing path
    with pytest.raises(Exception):
        db._fetch_one_solver_family("missing_fam", raise_if_missing=True)
