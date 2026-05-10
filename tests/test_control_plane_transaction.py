"""R21.2 — ControlPlaneDB.transaction() context manager tests."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from waggledance.core.storage.control_plane import ControlPlaneDB


@pytest.fixture
def db(tmp_path: Path) -> ControlPlaneDB:
    db = ControlPlaneDB(db_path=tmp_path / "test.db")
    db.migrate()
    return db


# ─── Happy path: commit on success ───────────────────────────────

def test_transaction_commits_on_normal_exit(db: ControlPlaneDB):
    with db.transaction():
        db.upsert_solver_family(
            name="scalar_unit_conversion", version="v1", status="active",
        )
        db.upsert_solver(
            name="solver-A", version="v1",
            family_name="scalar_unit_conversion", status="auto_promoted",
        )
    # After commit the solver is durable
    rec = db.get_solver("solver-A")
    assert rec is not None
    assert rec.name == "solver-A"


def test_transaction_count_is_atomic(db: ControlPlaneDB):
    """Mid-transaction the row should be visible via db.get_solver()
    inside the same connection (sqlite default), but other queries
    that use DIFFERENT connections shouldn't see it until commit.
    Test the simpler invariant: after transaction(), count_solvers()
    matches the number of upserts."""
    with db.transaction():
        db.upsert_solver_family(
            name="scalar_unit_conversion", version="v1", status="active",
        )
        for i in range(10):
            db.upsert_solver(
                name=f"solver-{i:02d}", version="v1",
                family_name="scalar_unit_conversion", status="auto_promoted",
            )
    assert db.count_solvers() == 10


# ─── Rollback path ───────────────────────────────────────────────

def test_transaction_rolls_back_on_exception(db: ControlPlaneDB):
    """An exception inside transaction() rolls back ALL writes —
    even the ones that succeeded before the exception."""
    db.upsert_solver_family(
        name="scalar_unit_conversion", version="v1", status="active",
    )
    # Pre-existing solver count is 0
    assert db.count_solvers() == 0

    with pytest.raises(RuntimeError, match="boom"):
        with db.transaction():
            db.upsert_solver(
                name="should-rollback-1", version="v1",
                family_name="scalar_unit_conversion",
                status="auto_promoted",
            )
            db.upsert_solver(
                name="should-rollback-2", version="v1",
                family_name="scalar_unit_conversion",
                status="auto_promoted",
            )
            raise RuntimeError("boom")

    # Both writes must be rolled back
    assert db.count_solvers() == 0
    assert db.get_solver("should-rollback-1") is None
    assert db.get_solver("should-rollback-2") is None


def test_transaction_rolls_back_unknown_family_error(db: ControlPlaneDB):
    """A real ControlPlaneError mid-transaction (e.g. unknown family)
    must roll back the partial writes from earlier in the block."""
    from waggledance.core.storage.control_plane import ControlPlaneError

    db.upsert_solver_family(
        name="scalar_unit_conversion", version="v1", status="active",
    )

    with pytest.raises(ControlPlaneError, match="unknown solver family"):
        with db.transaction():
            db.upsert_solver(
                name="ok-1", version="v1",
                family_name="scalar_unit_conversion",
                status="auto_promoted",
            )
            # This raises because the family doesn't exist
            db.upsert_solver(
                name="bad", version="v1",
                family_name="not_a_family",
                status="auto_promoted",
            )

    # The successful first write must also be rolled back
    assert db.count_solvers() == 0


# ─── Re-entrant nesting ─────────────────────────────────────────

def test_nested_transaction_is_no_op(db: ControlPlaneDB):
    """A nested transaction() call from the same thread shares the
    outer transaction. Inner exit does NOT commit; only outer does."""
    db.upsert_solver_family(
        name="scalar_unit_conversion", version="v1", status="active",
    )
    with db.transaction():
        with db.transaction():
            # Inner exit returns normally
            db.upsert_solver(
                name="inner", version="v1",
                family_name="scalar_unit_conversion",
                status="auto_promoted",
            )
        # Still inside outer
        db.upsert_solver(
            name="outer", version="v1",
            family_name="scalar_unit_conversion",
            status="auto_promoted",
        )
    # Both visible after outer commit
    assert db.get_solver("inner") is not None
    assert db.get_solver("outer") is not None


def test_nested_exception_rolls_back_outer(db: ControlPlaneDB):
    """If inner block raises, outer transaction's COMMIT must NOT fire.
    The whole transaction rolls back."""
    db.upsert_solver_family(
        name="scalar_unit_conversion", version="v1", status="active",
    )
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.upsert_solver(
                name="outer-then-inner", version="v1",
                family_name="scalar_unit_conversion",
                status="auto_promoted",
            )
            with db.transaction():
                raise RuntimeError("inner boom")
    assert db.count_solvers() == 0


# ─── Bulk-load speedup (R21.2 headline) ─────────────────────────

def test_bulk_load_under_transaction_completes(db: ControlPlaneDB):
    """Smoke: 200 upserts under a single transaction complete and
    end up durable. The headline speedup vs autocommit is measured by
    the scale_proof tool, not pinned here as a wall-clock assertion
    (would be too noisy at 200 rows on a CI runner)."""
    db.upsert_solver_family(
        name="scalar_unit_conversion", version="v1", status="active",
    )
    n = 200
    with db.transaction():
        for i in range(n):
            db.upsert_solver(
                name=f"bulk-{i:04d}", version="v1",
                family_name="scalar_unit_conversion",
                status="auto_promoted",
            )
    assert db.count_solvers() == n


def test_in_transaction_flag_resets_after_block(db: ControlPlaneDB):
    """The internal _in_transaction flag must reset to False on both
    success and exception paths, otherwise subsequent transactions
    silently no-op and never commit."""
    assert db._in_transaction is False
    with db.transaction():
        assert db._in_transaction is True
    assert db._in_transaction is False

    with pytest.raises(RuntimeError):
        with db.transaction():
            assert db._in_transaction is True
            raise RuntimeError("boom")
    assert db._in_transaction is False

    # Subsequent transaction still works
    db.upsert_solver_family(
        name="scalar_unit_conversion", version="v1", status="active",
    )
    with db.transaction():
        db.upsert_solver(
            name="post-rollback", version="v1",
            family_name="scalar_unit_conversion",
            status="auto_promoted",
        )
    assert db.get_solver("post-rollback") is not None
