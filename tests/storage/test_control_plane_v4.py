# SPDX-License-Identifier: Apache-2.0
"""Schema-v4 tests for solver capability feature lookup (Phase 13)."""

from __future__ import annotations

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


def test_schema_v4_table_present(cp: ControlPlaneDB) -> None:
    assert SCHEMA_VERSION >= 4
    assert "solver_capability_features" in all_table_names()
    assert "solver_capability_features" in cp.stats().table_counts


def _make_promoted_solver(cp: ControlPlaneDB, family: str, name: str,
                          status: str = "auto_promoted") -> int:
    fam = cp.upsert_solver_family(family, "1.0")
    s = cp.upsert_solver(
        family_name=fam.name, name=name, version="1.0",
        status=status, spec_hash=f"hash_{name}",
    )
    return s.id


def test_set_solver_capability_features_round_trip(cp: ControlPlaneDB) -> None:
    sid = _make_promoted_solver(cp, "scalar_unit_conversion", "c_to_k")
    rows = cp.set_solver_capability_features(
        sid, "scalar_unit_conversion",
        {"from_unit": "C", "to_unit": "K"},
    )
    assert len(rows) == 2
    assert {r.feature_name for r in rows} == {"from_unit", "to_unit"}
    fetched = cp.get_solver_capability_features(sid)
    assert {r.feature_name: r.feature_value for r in fetched} == {
        "from_unit": "C", "to_unit": "K",
    }


def test_set_features_replaces_atomically(cp: ControlPlaneDB) -> None:
    sid = _make_promoted_solver(cp, "scalar_unit_conversion", "c_to_k")
    cp.set_solver_capability_features(
        sid, "scalar_unit_conversion",
        {"from_unit": "C", "to_unit": "K"},
    )
    # Replace with a different set
    cp.set_solver_capability_features(
        sid, "scalar_unit_conversion",
        {"from_unit": "F", "to_unit": "C"},
    )
    fetched = cp.get_solver_capability_features(sid)
    assert {r.feature_name: r.feature_value for r in fetched} == {
        "from_unit": "F", "to_unit": "C",
    }


def test_find_auto_promoted_solvers_by_features_exact_match(
    cp: ControlPlaneDB,
) -> None:
    sid_a = _make_promoted_solver(cp, "scalar_unit_conversion", "c_to_k")
    sid_b = _make_promoted_solver(cp, "scalar_unit_conversion", "f_to_c")
    sid_c = _make_promoted_solver(cp, "scalar_unit_conversion", "m_to_km")
    cp.set_solver_capability_features(
        sid_a, "scalar_unit_conversion", {"from_unit": "C", "to_unit": "K"},
    )
    cp.set_solver_capability_features(
        sid_b, "scalar_unit_conversion", {"from_unit": "F", "to_unit": "C"},
    )
    cp.set_solver_capability_features(
        sid_c, "scalar_unit_conversion", {"from_unit": "m", "to_unit": "km"},
    )
    # Looking for C -> K should return sid_a
    matches = cp.find_auto_promoted_solvers_by_features(
        "scalar_unit_conversion", {"from_unit": "C", "to_unit": "K"},
    )
    assert matches == [sid_a]


def test_find_returns_only_auto_promoted_solvers(cp: ControlPlaneDB) -> None:
    sid_draft = _make_promoted_solver(
        cp, "lookup_table", "draft_one", status="draft",
    )
    sid_promoted = _make_promoted_solver(cp, "lookup_table", "promoted_one")
    cp.set_solver_capability_features(
        sid_draft, "lookup_table", {"domain": "color"},
    )
    cp.set_solver_capability_features(
        sid_promoted, "lookup_table", {"domain": "color"},
    )
    matches = cp.find_auto_promoted_solvers_by_features(
        "lookup_table", {"domain": "color"},
    )
    assert sid_promoted in matches
    assert sid_draft not in matches


def test_find_requires_all_features_to_match(cp: ControlPlaneDB) -> None:
    sid_partial = _make_promoted_solver(cp, "threshold_rule", "t_partial")
    sid_full = _make_promoted_solver(cp, "threshold_rule", "t_full")
    cp.set_solver_capability_features(
        sid_partial, "threshold_rule", {"subject": "temperature"},
    )
    cp.set_solver_capability_features(
        sid_full, "threshold_rule",
        {"subject": "temperature", "operator": ">"},
    )
    matches = cp.find_auto_promoted_solvers_by_features(
        "threshold_rule", {"subject": "temperature", "operator": ">"},
    )
    assert matches == [sid_full]


def test_find_empty_features_refuses_unbounded_scan(cp: ControlPlaneDB) -> None:
    """Empty features must NOT scan the whole table."""

    sid = _make_promoted_solver(cp, "scalar_unit_conversion", "c_to_k")
    cp.set_solver_capability_features(
        sid, "scalar_unit_conversion", {"from_unit": "C", "to_unit": "K"},
    )
    matches = cp.find_auto_promoted_solvers_by_features(
        "scalar_unit_conversion", {},
    )
    assert matches == []


def test_v4_indexes_exist(cp: ControlPlaneDB) -> None:
    rows = cp._conn.execute(  # type: ignore[attr-defined]
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
    ).fetchall()
    names = {r["name"] for r in rows}
    must_have = {
        "idx_solver_capability_features_lookup",
        "idx_solver_capability_features_solver",
    }
    missing = must_have - names
    assert not missing, f"missing v4 indexes: {missing}"
