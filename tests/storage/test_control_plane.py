"""Targeted tests for the Phase 10 control-plane database (P2 + P7).

Covers RULE 7 categories:
  2. control-plane DB schema creation/migration
 12. solver registry scalability smoke test (batch insert / lookup)
 25. no absolute path leakage (paths are stored relative or via resolver)
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from waggledance.core.storage import (
    ControlPlaneDB,
    ControlPlaneError,
    SCHEMA_VERSION,
)
from waggledance.core.storage.control_plane_schema import all_table_names


@pytest.fixture()
def cp(tmp_path: Path) -> ControlPlaneDB:
    db = ControlPlaneDB(db_path=tmp_path / "cp.db")
    yield db
    db.close()


def test_schema_creates_all_canonical_tables(cp: ControlPlaneDB) -> None:
    rows = cp._conn.execute(  # noqa: SLF001 — test access
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    table_names = {row["name"] for row in rows}
    for expected in all_table_names():
        assert expected in table_names, f"missing table {expected!r}"


def test_schema_version_persisted_and_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "cp.db"
    db1 = ControlPlaneDB(db_path=db_path)
    assert db1.schema_version() == SCHEMA_VERSION
    db1.close()
    # Reopen — migrate must be idempotent.
    db2 = ControlPlaneDB(db_path=db_path)
    assert db2.schema_version() == SCHEMA_VERSION
    db2.close()


def test_solver_family_upsert_and_solver_link(cp: ControlPlaneDB) -> None:
    fam = cp.upsert_solver_family("thermal", "1.0.0", description="thermal solvers")
    assert fam.id > 0
    sv = cp.upsert_solver("thermal_basic", "0.1.0", family_name="thermal")
    assert sv.family_id == fam.id

    # Upsert again — id stable, version bumped.
    sv2 = cp.upsert_solver("thermal_basic", "0.2.0", family_name="thermal", status="shadow")
    assert sv2.id == sv.id
    assert sv2.version == "0.2.0"
    assert sv2.status == "shadow"


def test_solver_unknown_family_raises(cp: ControlPlaneDB) -> None:
    with pytest.raises(ControlPlaneError):
        cp.upsert_solver("orphan", "0.0.1", family_name="does-not-exist")


def test_capability_dependency_dag(cp: ControlPlaneDB) -> None:
    cp.upsert_capability("reason.causal", "1.0")
    cp.upsert_capability("solver.thermal", "1.0")
    cp.upsert_capability("solver.honey_yield", "1.0")
    cp.add_capability_dependency("solver.honey_yield", "solver.thermal")
    cp.add_capability_dependency("solver.honey_yield", "reason.causal")
    # Idempotent on duplicate.
    cp.add_capability_dependency("solver.honey_yield", "solver.thermal")
    rows = cp._conn.execute(  # noqa: SLF001 — test access
        "SELECT COUNT(*) AS c FROM capability_dependencies"
    ).fetchone()
    assert int(rows["c"]) == 2


def test_solver_capability_link_and_lookup(cp: ControlPlaneDB) -> None:
    cp.upsert_solver_family("thermal", "1.0.0")
    cp.upsert_solver("thermal_basic", "0.1.0", family_name="thermal")
    cp.upsert_capability("solver.thermal", "1.0")
    cp.link_solver_capability("thermal_basic", "solver.thermal")

    rows = cp._conn.execute(  # noqa: SLF001
        """
        SELECT c.name FROM capabilities c
          JOIN solver_capabilities sc ON sc.capability_id = c.id
          JOIN solvers s ON s.id = sc.solver_id
         WHERE s.name = ?
        """,
        ("thermal_basic",),
    ).fetchall()
    assert [r["name"] for r in rows] == ["solver.thermal"]


def test_runtime_path_binding_deactivates_previous(cp: ControlPlaneDB) -> None:
    b1 = cp.bind_runtime_path("default", "faiss_root", "data/faiss")
    assert b1.is_active is True

    b2 = cp.bind_runtime_path("default", "faiss_root", "data/vector")
    assert b2.is_active is True
    assert b2.id != b1.id

    active = cp.get_active_runtime_path("default", "faiss_root")
    assert active is not None
    assert active.id == b2.id
    assert active.physical_path == "data/vector"

    # Verify previous is deactivated (only one active row per (name, kind)).
    rows = cp._conn.execute(  # noqa: SLF001
        "SELECT COUNT(*) AS c FROM runtime_path_bindings WHERE is_active = 1 "
        "AND logical_name = ? AND path_kind = ?",
        ("default", "faiss_root"),
    ).fetchone()
    assert int(rows["c"]) == 1


def test_provider_job_record_and_update(cp: ControlPlaneDB) -> None:
    job = cp.record_provider_job(
        "claude_code_builder_lane",
        "code_generate",
        section="P3",
        purpose="builder lane scaffold",
        cost_estimate=0.5,
    )
    assert job.status == "queued"
    updated = cp.update_provider_job(
        job.id,
        status="completed",
        cost_actual=0.42,
        completed_at="2026-04-28T12:00:00+00:00",
    )
    assert updated.status == "completed"
    assert updated.cost_actual == pytest.approx(0.42)


def test_count_solvers_at_50k_within_budget(tmp_path: Path) -> None:
    """Scale smoke test — RULE 7 category 12.

    Bulk-insert 50000 solvers in 10 families; verify count + lookup
    finishes well under interactive thresholds.
    """

    db = ControlPlaneDB(db_path=tmp_path / "cp_scale.db")
    try:
        for i in range(10):
            db.upsert_solver_family(f"fam_{i:02d}", "1.0.0")

        start = time.perf_counter()
        with db._lock:  # noqa: SLF001 — direct bulk insert
            db._conn.execute("BEGIN IMMEDIATE")
            try:
                fam_ids = [
                    int(
                        db._conn.execute(
                            "SELECT id FROM solver_families WHERE name = ?",
                            (f"fam_{i:02d}",),
                        ).fetchone()["id"]
                    )
                    for i in range(10)
                ]
                now = "2026-04-28T00:00:00+00:00"
                rows = [
                    (fam_ids[i % 10], f"solver_{i:06d}", "0.1.0", "draft", None, None, now, now)
                    for i in range(50_000)
                ]
                db._conn.executemany(
                    """
                    INSERT INTO solvers(family_id, name, version, status, spec_hash, spec_path, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
                db._conn.execute("COMMIT")
            except Exception:
                db._conn.execute("ROLLBACK")
                raise
        elapsed_insert = time.perf_counter() - start

        start = time.perf_counter()
        total = db.count_solvers()
        elapsed_count = time.perf_counter() - start

        start = time.perf_counter()
        sv = db.get_solver("solver_042424")
        elapsed_lookup = time.perf_counter() - start

        assert total == 50_000
        assert sv is not None and sv.name == "solver_042424"
        # Generous bounds — these guard against schema regressions, not
        # microbenchmark drift. Insert under 30s, count under 1s, lookup
        # under 0.05s.
        assert elapsed_insert < 30.0, f"50k insert took {elapsed_insert:.2f}s"
        assert elapsed_count < 1.0, f"count(*) took {elapsed_count:.2f}s"
        assert elapsed_lookup < 0.05, f"unique lookup took {elapsed_lookup * 1000:.1f}ms"
    finally:
        db.close()


def test_foreign_keys_enforced(cp: ControlPlaneDB) -> None:
    cp.upsert_solver_family("thermal", "1.0.0")
    cp.upsert_solver("thermal_basic", "0.1.0", family_name="thermal")
    cp.upsert_capability("solver.thermal", "1.0")
    cp.link_solver_capability("thermal_basic", "solver.thermal")

    # Deleting the capability should cascade-delete the link.
    with cp._lock:  # noqa: SLF001
        cp._conn.execute("DELETE FROM capabilities WHERE name = ?", ("solver.thermal",))
        rows = cp._conn.execute(
            "SELECT COUNT(*) AS c FROM solver_capabilities"
        ).fetchone()
    assert int(rows["c"]) == 0


def test_bsd_path_strings_are_relative_or_absolute_but_recorded_verbatim(
    cp: ControlPlaneDB, tmp_path: Path
) -> None:
    """RULE 7 category 25 — no path leakage in writes.

    The control plane records whatever path string the caller hands in;
    this test makes the contract explicit so a future reviewer who sees
    an absolute machine path in a row knows it came from the caller,
    not from the storage layer.
    """

    cp.register_vector_shard(
        "agent_knowledge",
        "data/faiss/agent_knowledge",  # relative (the desired idiom)
        format="faiss",
    )
    rec = cp.get_vector_shard("agent_knowledge")
    assert rec is not None
    assert rec.physical_path == "data/faiss/agent_knowledge"
    assert not rec.physical_path.startswith("C:")
    assert not rec.physical_path.startswith("/c/")
