"""Targeted tests for the Phase 10 path resolver (P2 + P7).

Covers RULE 7 categories:
  3. path_resolver deterministic resolution
  4. MAGMA append-only audit preserved (resolver does not mutate audit DB path)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from waggledance.core.storage import (
    ControlPlaneDB,
    LogicalPathKind,
    PathResolver,
    PathResolverError,
    ResolvedPath,
)


@pytest.fixture()
def repo_root(tmp_path: Path) -> Path:
    # Simulate a repo root by dropping a marker.
    (tmp_path / "LICENSE-CORE.md").write_text("test marker", encoding="utf-8")
    return tmp_path


def test_default_resolution_matches_legacy_layout(repo_root: Path) -> None:
    resolver = PathResolver(repo_root=repo_root)
    snap = resolver.snapshot()
    assert snap[LogicalPathKind.FAISS_ROOT.value].path == (repo_root / "data" / "faiss").resolve()
    assert snap[LogicalPathKind.VECTOR_ROOT.value].path == (repo_root / "data" / "vector").resolve()
    assert snap[LogicalPathKind.AUDIT_LOG_DB.value].path == (repo_root / "data" / "audit_log.db").resolve()
    assert snap[LogicalPathKind.CONTROL_PLANE_DB.value].path == (
        repo_root / "data" / "control_plane.db"
    ).resolve()
    for r in snap.values():
        assert r.source == "default"


def test_override_takes_precedence_over_default(repo_root: Path) -> None:
    resolver = PathResolver(repo_root=repo_root)
    resolver.register_override(LogicalPathKind.FAISS_ROOT, "data/alternate_faiss")
    r = resolver.resolve(LogicalPathKind.FAISS_ROOT)
    assert r.path == (repo_root / "data" / "alternate_faiss").resolve()
    assert r.source == "override"


def test_override_takes_precedence_over_control_plane_binding(
    repo_root: Path, tmp_path: Path
) -> None:
    cp = ControlPlaneDB(db_path=tmp_path / "cp.db")
    cp.bind_runtime_path("default", LogicalPathKind.FAISS_ROOT.value, "data/cp_bound_faiss")
    try:
        resolver = PathResolver(repo_root=repo_root, control_plane=cp)
        # Without override, control plane wins over default.
        r1 = resolver.resolve(LogicalPathKind.FAISS_ROOT)
        assert r1.source == "control_plane"
        assert r1.path == (repo_root / "data" / "cp_bound_faiss").resolve()

        # With override, override wins.
        resolver.register_override(LogicalPathKind.FAISS_ROOT, "data/override_faiss")
        r2 = resolver.resolve(LogicalPathKind.FAISS_ROOT)
        assert r2.source == "override"
        assert r2.path == (repo_root / "data" / "override_faiss").resolve()
    finally:
        cp.close()


def test_control_plane_binding_falls_back_to_default_when_inactive(
    repo_root: Path, tmp_path: Path
) -> None:
    cp = ControlPlaneDB(db_path=tmp_path / "cp.db")
    try:
        # No binding ever recorded → default applies.
        resolver = PathResolver(repo_root=repo_root, control_plane=cp)
        r = resolver.resolve(LogicalPathKind.AUDIT_LOG_DB)
        assert r.source == "default"
        assert r.path == (repo_root / "data" / "audit_log.db").resolve()
    finally:
        cp.close()


def test_derive_appends_path_components(repo_root: Path) -> None:
    resolver = PathResolver(repo_root=repo_root)
    rp = resolver.derive(LogicalPathKind.VECTOR_ROOT, "cell_3", "index.faiss")
    assert rp.path == (repo_root / "data" / "vector" / "cell_3" / "index.faiss").resolve()
    assert rp.kind == LogicalPathKind.VECTOR_ROOT


def test_resolve_unknown_kind_raises(repo_root: Path) -> None:
    resolver = PathResolver(repo_root=repo_root)
    with pytest.raises(PathResolverError):
        resolver.resolve("not_a_kind")  # type: ignore[arg-type]


def test_attach_control_plane_post_construction(repo_root: Path, tmp_path: Path) -> None:
    resolver = PathResolver(repo_root=repo_root)
    cp = ControlPlaneDB(db_path=tmp_path / "cp.db")
    try:
        cp.bind_runtime_path("default", LogicalPathKind.VECTOR_ROOT.value, "data/late_binding")
        resolver.attach_control_plane(cp)
        r = resolver.resolve(LogicalPathKind.VECTOR_ROOT)
        assert r.source == "control_plane"
        assert r.path == (repo_root / "data" / "late_binding").resolve()
    finally:
        cp.close()


def test_clear_overrides(repo_root: Path) -> None:
    resolver = PathResolver(repo_root=repo_root)
    resolver.register_override(LogicalPathKind.FAISS_ROOT, "data/override_a")
    assert resolver.resolve(LogicalPathKind.FAISS_ROOT).source == "override"
    resolver.clear_overrides()
    assert resolver.resolve(LogicalPathKind.FAISS_ROOT).source == "default"


def test_default_resolution_is_drop_in_for_legacy_faiss_dir(repo_root: Path) -> None:
    """Drop-in compatibility test.

    Since core/faiss_store.py:26 hard-codes ``data/faiss`` relative to
    its parent.parent, the resolver's default for FAISS_ROOT must
    yield the exact same effective path when given the repo root.
    """

    resolver = PathResolver(repo_root=repo_root)
    r = resolver.resolve(LogicalPathKind.FAISS_ROOT)
    expected = (repo_root / "data" / "faiss").resolve()
    assert r.path == expected, f"resolver default {r.path} != legacy {expected}"
    assert r.is_default is True


def test_resolver_does_not_create_control_plane_db_implicitly(
    repo_root: Path, tmp_path: Path
) -> None:
    """The resolver, given no control_plane, must not silently open or
    create a control-plane DB file. RULE 14 fail-loud applies — silent
    side effects on import / construction are forbidden."""

    resolver = PathResolver(repo_root=repo_root)
    cp_db_path = repo_root / "data" / "control_plane.db"
    # Resolution should not have materialised the file.
    assert not cp_db_path.exists()
    # And asking for the control-plane path is fine — it just returns
    # the default path string without opening anything.
    r = resolver.resolve(LogicalPathKind.CONTROL_PLANE_DB)
    assert r.path == cp_db_path.resolve()
    assert not cp_db_path.exists()
