# SPDX-License-Identifier: Apache-2.0
"""Schema-v5 tests for immutable scoped activation snapshot pointers."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from waggledance.core.storage.control_plane import ControlPlaneDB
from waggledance.core.storage.control_plane_schema import (
    MIGRATIONS,
    SCHEMA_VERSION,
    all_table_names,
)


ZERO_DIGEST = "sha256:" + "0" * 64
CREATED_AT = "2026-08-05T00:00:00+00:00"


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


@pytest.fixture()
def cp(tmp_path: Path):
    db = ControlPlaneDB(tmp_path / "cp.sqlite")
    yield db
    db.close()


def _insert_scope(
    conn: sqlite3.Connection,
    label: str,
    *,
    deployment_digest: str | None = None,
    cell_id: str | None = None,
) -> str:
    scope_digest = _digest(f"scope:{label}")
    conn.execute(
        """
        INSERT INTO activation_scopes(
            activation_scope_digest,
            deployment_scope_digest,
            cell_id,
            created_at
        ) VALUES (?, ?, ?, ?)
        """,
        (
            scope_digest,
            deployment_digest or _digest(f"deployment:{label}"),
            cell_id or _digest(f"cell:{label}"),
            CREATED_AT,
        ),
    )
    return scope_digest


def _pointer_values(
    scope_digest: str,
    label: str,
    *,
    revision: int | float = 0,
    previous_bundle_digest: str = ZERO_DIGEST,
    activation_head_digest: str | None = None,
    previous_activation_head_digest: str = ZERO_DIGEST,
) -> dict[str, object]:
    return {
        "activation_scope_digest": scope_digest,
        "bundle_digest": _digest(f"bundle:{label}"),
        "store_revision": revision,
        "previous_bundle_digest": previous_bundle_digest,
        "activation_head_digest": activation_head_digest
        or _digest(f"activation-head:{label}"),
        "previous_activation_head_digest": previous_activation_head_digest,
        "expression_context_digest": _digest(f"expression-context:{label}"),
        "expected_profile_head_digest": _digest(f"profile:{label}"),
        "expected_policy_head_digest": _digest(f"policy:{label}"),
        "expected_resource_head_digest": _digest(f"resource:{label}"),
        "expected_domain_head_digest": _digest(f"domain:{label}"),
        "expected_environment_head_digest": _digest(f"environment:{label}"),
        "charter_ceiling_digest": _digest(f"charter:{label}"),
        "expressed_ceiling_digest": _digest(f"expressed:{label}"),
        "created_at": CREATED_AT,
    }


def _insert_pointer(
    conn: sqlite3.Connection,
    scope_digest: str,
    label: str,
    **overrides: object,
) -> sqlite3.Cursor:
    values = _pointer_values(scope_digest, label)
    values.update(overrides)
    columns = tuple(values)
    return conn.execute(
        f"INSERT INTO activation_snapshot_pointers({', '.join(columns)}) "
        f"VALUES ({', '.join('?' for _ in columns)})",
        tuple(values[column] for column in columns),
    )


def _create_v4_database(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        for version in range(1, 5):
            for statement in MIGRATIONS[version]:
                conn.execute(statement)
        conn.execute(
            """
            INSERT OR REPLACE INTO schema_meta(key, value, updated_at)
            VALUES ('schema_version', '4', ?)
            """,
            (CREATED_AT,),
        )
        cursor = conn.execute(
            """
            INSERT INTO solver_families(
                name, version, description, status,
                spec_path, created_at, updated_at
            ) VALUES ('preserved-family', '1.0', NULL, 'active', NULL, ?, ?)
            """,
            (CREATED_AT, CREATED_AT),
        )
        conn.execute(
            """
            INSERT INTO solvers(
                family_id, name, version, status,
                spec_hash, spec_path, created_at, updated_at
            ) VALUES (?, 'preserved-solver', '1.0', 'active', NULL, NULL, ?, ?)
            """,
            (cursor.lastrowid, CREATED_AT, CREATED_AT),
        )
        conn.commit()
    finally:
        conn.close()


def test_v4_to_current_migration_preserves_v5_rows_and_starts_empty(
    tmp_path: Path,
) -> None:
    path = tmp_path / "v4.sqlite"
    _create_v4_database(path)

    db = ControlPlaneDB(path)
    try:
        assert SCHEMA_VERSION >= 5
        assert db.schema_version() == SCHEMA_VERSION
        assert db.get_solver_family("preserved-family") is not None
        assert db.get_solver("preserved-solver") is not None
        for table in (
            "activation_scopes",
            "activation_scope_tombstones",
            "activation_snapshot_pointers",
        ):
            assert table in all_table_names()
            count = db._conn.execute(  # noqa: SLF001 - schema assertion
                f"SELECT COUNT(*) AS count FROM {table}"
            ).fetchone()["count"]
            assert count == 0
    finally:
        db.close()


def test_v5_tables_columns_checks_and_triggers(cp: ControlPlaneDB) -> None:
    expected_columns = {
        "activation_scopes": [
            "activation_scope_digest",
            "deployment_scope_digest",
            "cell_id",
            "created_at",
        ],
        "activation_scope_tombstones": [
            "activation_scope_digest",
            "reason_digest",
            "retired_at",
        ],
        "activation_snapshot_pointers": [
            "id",
            "activation_scope_digest",
            "bundle_digest",
            "store_revision",
            "previous_bundle_digest",
            "activation_head_digest",
            "previous_activation_head_digest",
            "expression_context_digest",
            "expected_profile_head_digest",
            "expected_policy_head_digest",
            "expected_resource_head_digest",
            "expected_domain_head_digest",
            "expected_environment_head_digest",
            "charter_ceiling_digest",
            "expressed_ceiling_digest",
            "created_at",
        ],
    }
    for table, expected in expected_columns.items():
        columns = [
            row["name"]
            for row in cp._conn.execute(  # noqa: SLF001 - schema assertion
                f"PRAGMA table_info({table})"
            ).fetchall()
        ]
        assert columns == expected

    trigger_names = {
        row["name"]
        for row in cp._conn.execute(  # noqa: SLF001 - schema assertion
            "SELECT name FROM sqlite_master WHERE type = 'trigger'"
        ).fetchall()
    }
    assert {
        "trg_activation_scopes_refuse_collision",
        "trg_activation_scope_tombstones_refuse_collision",
        "trg_activation_snapshot_pointers_refuse_collision",
        "trg_activation_scopes_refuse_update",
        "trg_activation_scopes_refuse_delete",
        "trg_activation_scope_tombstones_refuse_update",
        "trg_activation_scope_tombstones_refuse_delete",
        "trg_activation_snapshot_pointers_refuse_update",
        "trg_activation_snapshot_pointers_refuse_delete",
        "trg_activation_snapshot_pointers_validate_insert",
    }.issubset(trigger_names)

    digest_columns_by_table = {
        "activation_scopes": (
            "activation_scope_digest",
            "deployment_scope_digest",
            "cell_id",
        ),
        "activation_scope_tombstones": (
            "activation_scope_digest",
            "reason_digest",
        ),
        "activation_snapshot_pointers": (
            "activation_scope_digest",
            "bundle_digest",
            "previous_bundle_digest",
            "activation_head_digest",
            "previous_activation_head_digest",
            "expression_context_digest",
            "expected_profile_head_digest",
            "expected_policy_head_digest",
            "expected_resource_head_digest",
            "expected_domain_head_digest",
            "expected_environment_head_digest",
            "charter_ceiling_digest",
            "expressed_ceiling_digest",
        ),
    }
    for table, digest_columns in digest_columns_by_table.items():
        table_sql = cp._conn.execute(  # noqa: SLF001 - schema assertion
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()["sql"]
        for column in digest_columns:
            assert f"typeof({column}) = 'text'" in table_sql
            assert f"length({column}) = 71" in table_sql
            assert f"substr({column}, 1, 7) = 'sha256:'" in table_sql
            assert f"substr({column}, 8) NOT GLOB '*[^0-9a-f]*'" in table_sql
    pointer_sql = cp._conn.execute(  # noqa: SLF001 - schema assertion
        """
        SELECT sql FROM sqlite_master
        WHERE type = 'table' AND name = 'activation_snapshot_pointers'
        """
    ).fetchone()["sql"]
    assert "typeof(store_revision) = 'integer'" in pointer_sql
    assert "store_revision BETWEEN 0 AND 9223372036854775807" in pointer_sql

    invalid_uppercase = "sha256:" + "A" * 64
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        cp._conn.execute(  # noqa: SLF001 - constraint assertion
            "INSERT INTO activation_scopes VALUES (?, ?, ?, ?)",
            (invalid_uppercase, _digest("deployment"), _digest("cell"), CREATED_AT),
        )

    scope = _insert_scope(cp._conn, "strict")  # noqa: SLF001
    with pytest.raises(sqlite3.IntegrityError):
        cp._conn.execute(  # noqa: SLF001 - constraint assertion
            """
            INSERT INTO activation_scope_tombstones(
                activation_scope_digest, reason_digest, retired_at
            ) VALUES (?, ?, ?)
            """,
            (scope, invalid_uppercase, CREATED_AT),
        )
    digest_columns = (
        "bundle_digest",
        "previous_bundle_digest",
        "activation_head_digest",
        "previous_activation_head_digest",
        "expression_context_digest",
        "expected_profile_head_digest",
        "expected_policy_head_digest",
        "expected_resource_head_digest",
        "expected_domain_head_digest",
        "expected_environment_head_digest",
        "charter_ceiling_digest",
        "expressed_ceiling_digest",
    )
    for column in digest_columns:
        # BEFORE INSERT chain validation may reject malformed predecessor
        # digests before the table CHECK runs; either path must fail closed.
        with pytest.raises(sqlite3.IntegrityError):
            _insert_pointer(
                cp._conn,  # noqa: SLF001
                scope,
                f"invalid-{column}",
                **{column: invalid_uppercase},
            )
    assert cp._conn.execute(  # noqa: SLF001
        "SELECT COUNT(*) AS count FROM activation_snapshot_pointers"
    ).fetchone()["count"] == 0

    with pytest.raises(sqlite3.IntegrityError):
        _insert_pointer(cp._conn, scope, "negative", store_revision=-1)  # noqa: SLF001
    with pytest.raises(sqlite3.IntegrityError):
        _insert_pointer(cp._conn, scope, "fractional", store_revision=0.5)  # noqa: SLF001


def test_independent_scopes_may_share_activation_head(cp: ControlPlaneDB) -> None:
    deployment = _digest("shared-deployment")
    scope_a = _insert_scope(
        cp._conn, "a", deployment_digest=deployment, cell_id=_digest("cell-a")  # noqa: SLF001
    )
    scope_b = _insert_scope(
        cp._conn, "b", deployment_digest=deployment, cell_id=_digest("cell-b")  # noqa: SLF001
    )
    shared_head = _digest("shared-activation-head")
    first_a = _pointer_values(
        scope_a, "a0", activation_head_digest=shared_head
    )
    first_b = _pointer_values(
        scope_b, "b0", activation_head_digest=shared_head
    )
    _insert_pointer(cp._conn, scope_a, "a0", **first_a)  # noqa: SLF001
    _insert_pointer(cp._conn, scope_b, "b0", **first_b)  # noqa: SLF001

    next_head = _digest("a-next-head")
    _insert_pointer(
        cp._conn,  # noqa: SLF001
        scope_a,
        "a1",
        store_revision=1,
        previous_bundle_digest=first_a["bundle_digest"],
        activation_head_digest=next_head,
        previous_activation_head_digest=shared_head,
    )

    current = {
        row["activation_scope_digest"]: row["revision"]
        for row in cp._conn.execute(  # noqa: SLF001
            """
            SELECT activation_scope_digest, MAX(store_revision) AS revision
            FROM activation_snapshot_pointers
            GROUP BY activation_scope_digest
            """
        ).fetchall()
    }
    assert current == {scope_a: 1, scope_b: 0}

    scope_c = _insert_scope(cp._conn, "c")  # noqa: SLF001
    with pytest.raises(sqlite3.IntegrityError, match="immutable key collision"):
        _insert_pointer(
            cp._conn,  # noqa: SLF001
            scope_c,
            "c0",
            bundle_digest=first_a["bundle_digest"],
        )


def test_pointer_trigger_refuses_invalid_genesis_and_chain(cp: ControlPlaneDB) -> None:
    bad_revision = _insert_scope(cp._conn, "bad-revision")  # noqa: SLF001
    with pytest.raises(sqlite3.IntegrityError, match="genesis mismatch"):
        _insert_pointer(
            cp._conn, bad_revision, "bad-revision", store_revision=1  # noqa: SLF001
        )

    bad_bundle = _insert_scope(cp._conn, "bad-bundle")  # noqa: SLF001
    with pytest.raises(sqlite3.IntegrityError, match="genesis mismatch"):
        _insert_pointer(
            cp._conn,  # noqa: SLF001
            bad_bundle,
            "bad-bundle",
            previous_bundle_digest=_digest("not-genesis"),
        )

    bad_head = _insert_scope(cp._conn, "bad-head")  # noqa: SLF001
    with pytest.raises(sqlite3.IntegrityError, match="genesis mismatch"):
        _insert_pointer(
            cp._conn,  # noqa: SLF001
            bad_head,
            "bad-head",
            previous_activation_head_digest=_digest("not-genesis"),
        )

    scope = _insert_scope(cp._conn, "chain")  # noqa: SLF001
    first = _pointer_values(scope, "chain-0")
    _insert_pointer(cp._conn, scope, "chain-0", **first)  # noqa: SLF001

    invalid_successors = (
        {
            "store_revision": 2,
            "previous_bundle_digest": first["bundle_digest"],
            "previous_activation_head_digest": first["activation_head_digest"],
        },
        {
            "store_revision": 1,
            "previous_bundle_digest": _digest("wrong-bundle"),
            "previous_activation_head_digest": first["activation_head_digest"],
        },
        {
            "store_revision": 1,
            "previous_bundle_digest": first["bundle_digest"],
            "previous_activation_head_digest": _digest("wrong-head"),
        },
    )
    for index, overrides in enumerate(invalid_successors):
        with pytest.raises(sqlite3.IntegrityError, match="chain mismatch"):
            _insert_pointer(
                cp._conn, scope, f"invalid-successor-{index}", **overrides  # noqa: SLF001
            )

    _insert_pointer(
        cp._conn,  # noqa: SLF001
        scope,
        "chain-1",
        store_revision=1,
        previous_bundle_digest=first["bundle_digest"],
        previous_activation_head_digest=first["activation_head_digest"],
    )
    assert cp._conn.execute(  # noqa: SLF001
        """
        SELECT COUNT(*) AS count
        FROM activation_snapshot_pointers
        WHERE activation_scope_digest = ?
        """,
        (scope,),
    ).fetchone()["count"] == 2


def test_tombstones_and_all_v5_rows_are_immutable(cp: ControlPlaneDB) -> None:
    scope = _insert_scope(cp._conn, "retired")  # noqa: SLF001
    first = _pointer_values(scope, "retired-0")
    pointer_id = _insert_pointer(  # noqa: SLF001
        cp._conn, scope, "retired-0", **first
    ).lastrowid
    cp._conn.execute(  # noqa: SLF001
        """
        INSERT INTO activation_scope_tombstones(
            activation_scope_digest, reason_digest, retired_at
        ) VALUES (?, ?, ?)
        """,
        (scope, _digest("retirement-reason"), CREATED_AT),
    )

    with pytest.raises(sqlite3.IntegrityError, match="scope is retired"):
        _insert_pointer(
            cp._conn,  # noqa: SLF001
            scope,
            "retired-1",
            store_revision=1,
            previous_bundle_digest=first["bundle_digest"],
            previous_activation_head_digest=first["activation_head_digest"],
        )

    mutations = (
        (
            "UPDATE activation_scopes SET created_at = ? "
            "WHERE activation_scope_digest = ?",
            ("later", scope),
        ),
        ("DELETE FROM activation_scopes WHERE activation_scope_digest = ?", (scope,)),
        (
            "UPDATE activation_scope_tombstones SET retired_at = ? "
            "WHERE activation_scope_digest = ?",
            ("later", scope),
        ),
        ("DELETE FROM activation_scope_tombstones WHERE activation_scope_digest = ?", (scope,)),
        (
            "UPDATE activation_snapshot_pointers SET created_at = ? WHERE id = ?",
            ("later", pointer_id),
        ),
        ("DELETE FROM activation_snapshot_pointers WHERE id = ?", (pointer_id,)),
    )
    for sql, params in mutations:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            cp._conn.execute(sql, params)  # noqa: SLF001

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        cp._conn.execute(  # noqa: SLF001
            """
            INSERT INTO activation_scope_tombstones(
                activation_scope_digest, reason_digest, retired_at
            ) VALUES (?, ?, ?)
            """,
            (_digest("unknown-scope"), _digest("reason"), CREATED_AT),
        )


def test_insert_or_replace_cannot_erase_history_or_rewrite_tombstone(
    cp: ControlPlaneDB,
) -> None:
    active_scope = _insert_scope(cp._conn, "replace-active")  # noqa: SLF001
    first = _pointer_values(active_scope, "replace-active-0")
    pointer_id = _insert_pointer(  # noqa: SLF001
        cp._conn, active_scope, "replace-active-0", **first
    ).lastrowid

    retired_scope = _insert_scope(cp._conn, "replace-retired")  # noqa: SLF001
    _insert_pointer(cp._conn, retired_scope, "replace-retired-0")  # noqa: SLF001
    original_reason = _digest("original-retirement")
    cp._conn.execute(  # noqa: SLF001
        """
        INSERT INTO activation_scope_tombstones(
            activation_scope_digest, reason_digest, retired_at
        ) VALUES (?, ?, ?)
        """,
        (retired_scope, original_reason, CREATED_AT),
    )

    # Prove the schema itself is safe on an ordinary external SQLite writer;
    # it must not depend solely on ControlPlaneDB's connection pragma.
    external = sqlite3.connect(cp.db_path, isolation_level=None)
    try:
        assert external.execute("PRAGMA recursive_triggers").fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            external.execute(
                """
                INSERT OR REPLACE INTO activation_scopes(
                    activation_scope_digest,
                    deployment_scope_digest,
                    cell_id,
                    created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    active_scope,
                    _digest("deployment:replace-active"),
                    _digest("cell:replace-active"),
                    "rewritten",
                ),
            )

        successor = _pointer_values(
            active_scope,
            "replace-successor",
            revision=1,
            previous_bundle_digest=first["bundle_digest"],
            activation_head_digest=first["activation_head_digest"],
            previous_activation_head_digest=first["activation_head_digest"],
        )
        successor["bundle_digest"] = first["bundle_digest"]
        columns = tuple(successor)
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            external.execute(
                "INSERT OR REPLACE INTO activation_snapshot_pointers"
                f"({', '.join(columns)}) VALUES "
                f"({', '.join('?' for _ in columns)})",
                tuple(successor[column] for column in columns),
            )

        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            external.execute(
                """
                INSERT OR REPLACE INTO activation_scope_tombstones(
                    activation_scope_digest, reason_digest, retired_at
                ) VALUES (?, ?, ?)
                """,
                (retired_scope, _digest("forged-reason"), "rewritten"),
            )
    finally:
        external.close()

    pointer = cp._conn.execute(  # noqa: SLF001
        """
        SELECT id, store_revision, bundle_digest, activation_head_digest
        FROM activation_snapshot_pointers
        WHERE activation_scope_digest = ?
        """,
        (active_scope,),
    ).fetchall()
    assert [tuple(row) for row in pointer] == [
        (
            pointer_id,
            0,
            first["bundle_digest"],
            first["activation_head_digest"],
        )
    ]
    tombstone = cp._conn.execute(  # noqa: SLF001
        """
        SELECT reason_digest, retired_at
        FROM activation_scope_tombstones
        WHERE activation_scope_digest = ?
        """,
        (retired_scope,),
    ).fetchone()
    assert tuple(tombstone) == (original_reason, CREATED_AT)
