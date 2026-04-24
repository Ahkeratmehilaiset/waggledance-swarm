"""Tests for tools/migrate_to_vector_root.py (Stage 1 copy).

Runtime safety is the headline requirement: migration MUST NOT touch
or mutate the legacy data/faiss_staging/ or data/faiss_delta_ledger/
trees, and it MUST be idempotent."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent


def _load_mod():
    path = ROOT / "tools" / "migrate_to_vector_root.py"
    spec = importlib.util.spec_from_file_location("migrate_to_vector_root", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["migrate_to_vector_root"] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load_mod()


def _seed_legacy(tmp_path: Path, cells=("thermal", "energy")) -> dict:
    """Build a minimal fake legacy tree under tmp_path."""
    staging = tmp_path / "data" / "faiss_staging"
    ledger = tmp_path / "data" / "faiss_delta_ledger"
    staging.mkdir(parents=True)
    ledger.mkdir(parents=True)

    manifest_cells = {}
    for cell in cells:
        cdir = staging / cell
        cdir.mkdir()
        (cdir / "index.faiss").write_bytes(b"FAISS-INDEX-BYTES-" + cell.encode())
        (cdir / "meta.json").write_text(json.dumps({"cell": cell}),
                                          encoding="utf-8")

        ldir = ledger / cell
        ldir.mkdir()
        for i in range(3):
            (ldir / f"0000{i}_seed.jsonl").write_text("{}\n", encoding="utf-8")

        manifest_cells[cell] = {
            "version": f"20260424T000000Z-{cell[:4]}",
            "doc_count": 5,
            "canonical_solver_count": 2,
            "dim": 768,
            "source_checksum": "deadbeef12345678",
            "ledger_hwm": 3,
        }

    (staging / "manifest.json").write_text(
        json.dumps({"kind": "staging", "cells": manifest_cells}),
        encoding="utf-8",
    )
    (staging / "cell_centroids.json").write_text(
        json.dumps({"thermal": [0.1, 0.2]}), encoding="utf-8"
    )
    return {"staging": staging, "ledger": ledger,
            "target": tmp_path / "data" / "vector",
            "cells": cells}


def _repoint_mod(monkeypatch, seed):
    monkeypatch.setattr(mod, "LEGACY_STAGING", seed["staging"])
    monkeypatch.setattr(mod, "LEGACY_LEDGER", seed["ledger"])
    monkeypatch.setattr(mod, "VECTOR_ROOT", seed["target"])
    monkeypatch.setattr(mod, "CELLS", tuple(seed["cells"]))


def test_dry_run_does_not_write(tmp_path, monkeypatch):
    seed = _seed_legacy(tmp_path)
    _repoint_mod(monkeypatch, seed)
    assert not seed["target"].exists()
    plan = mod.plan_migration(seed["target"])
    assert not seed["target"].exists()
    assert len(plan["cells"]) == len(seed["cells"])


def test_apply_creates_target_tree(tmp_path, monkeypatch):
    seed = _seed_legacy(tmp_path)
    _repoint_mod(monkeypatch, seed)
    out = mod.apply_migration(seed["target"])
    assert seed["target"].exists()
    # Top-level files
    assert (seed["target"] / "cell_centroids.json").exists()
    assert (seed["target"] / "manifest.json").exists()
    # Per-cell layout
    for cell in seed["cells"]:
        cell_dir = seed["target"] / cell
        assert (cell_dir / "index.faiss").exists()
        assert (cell_dir / "meta.json").exists()
        assert (cell_dir / "manifest.json").exists()
        assert (cell_dir / "commit.json").exists()


def test_apply_preserves_index_bytes_exactly(tmp_path, monkeypatch):
    seed = _seed_legacy(tmp_path, cells=("thermal",))
    _repoint_mod(monkeypatch, seed)
    mod.apply_migration(seed["target"])
    src_bytes = (seed["staging"] / "thermal" / "index.faiss").read_bytes()
    dst_bytes = (seed["target"] / "thermal" / "index.faiss").read_bytes()
    assert src_bytes == dst_bytes


def test_apply_does_not_touch_legacy_tree(tmp_path, monkeypatch):
    seed = _seed_legacy(tmp_path)
    _repoint_mod(monkeypatch, seed)

    # Snapshot the legacy tree before migration
    def _fs_state(root: Path) -> dict:
        state: dict = {}
        for p in sorted(root.rglob("*")):
            if p.is_file():
                state[p.relative_to(root).as_posix()] = hashlib.sha256(
                    p.read_bytes()
                ).hexdigest()
        return state
    before_staging = _fs_state(seed["staging"])
    before_ledger = _fs_state(seed["ledger"])

    mod.apply_migration(seed["target"])

    # Legacy tree must be byte-identical after migration
    assert _fs_state(seed["staging"]) == before_staging
    assert _fs_state(seed["ledger"]) == before_ledger


def test_apply_is_idempotent_without_force(tmp_path, monkeypatch):
    seed = _seed_legacy(tmp_path)
    _repoint_mod(monkeypatch, seed)

    first = mod.apply_migration(seed["target"])
    # Capture mtime of a specific file
    t1 = (seed["target"] / "thermal" / "index.faiss").stat().st_mtime_ns

    # Second call must be a no-op (no re-copy)
    second = mod.apply_migration(seed["target"])
    t2 = (seed["target"] / "thermal" / "index.faiss").stat().st_mtime_ns
    assert t1 == t2
    # Cells report skipped-exists on the second pass
    assert any(c["status"] == "skipped-exists" for c in second["cells"])


def test_apply_force_overwrites(tmp_path, monkeypatch):
    seed = _seed_legacy(tmp_path)
    _repoint_mod(monkeypatch, seed)
    mod.apply_migration(seed["target"])

    # Modify target in place
    (seed["target"] / "thermal" / "index.faiss").write_bytes(b"changed")
    mod.apply_migration(seed["target"], force=True)

    # File is restored to match source
    assert (seed["target"] / "thermal" / "index.faiss").read_bytes().startswith(
        b"FAISS-INDEX-BYTES-"
    )


def test_per_cell_manifest_contains_required_fields(tmp_path, monkeypatch):
    seed = _seed_legacy(tmp_path)
    _repoint_mod(monkeypatch, seed)
    mod.apply_migration(seed["target"])

    m = json.loads(
        (seed["target"] / "thermal" / "manifest.json").read_text("utf-8")
    )
    required = {
        "schema_version", "cell_id", "dim", "doc_count",
        "canonical_solver_count", "source_version",
        "source_checksum", "ledger_hwm", "ledger_entries",
        "index_checksum",
    }
    assert required.issubset(m.keys())
    assert m["cell_id"] == "thermal"
    assert m["index_checksum"].startswith("sha256:")


def test_per_cell_commit_contains_faiss_commit_id(tmp_path, monkeypatch):
    seed = _seed_legacy(tmp_path)
    _repoint_mod(monkeypatch, seed)
    mod.apply_migration(seed["target"])
    c = json.loads(
        (seed["target"] / "thermal" / "commit.json").read_text("utf-8")
    )
    assert c["faiss_commit_id"].startswith("faiss_")
    assert c["source"]["stage"] == 0
    assert c["source"]["staging_root"].endswith("faiss_staging")


def test_commit_id_is_deterministic(tmp_path, monkeypatch):
    """Same source tree → same faiss_commit_id across runs."""
    seed = _seed_legacy(tmp_path)
    _repoint_mod(monkeypatch, seed)
    mod.apply_migration(seed["target"])
    c1 = json.loads(
        (seed["target"] / "thermal" / "commit.json").read_text("utf-8")
    )

    seed2 = _seed_legacy(tmp_path / "run2")
    monkeypatch.setattr(mod, "LEGACY_STAGING", seed2["staging"])
    monkeypatch.setattr(mod, "LEGACY_LEDGER", seed2["ledger"])
    monkeypatch.setattr(mod, "VECTOR_ROOT", seed2["target"])
    monkeypatch.setattr(mod, "CELLS", tuple(seed2["cells"]))
    mod.apply_migration(seed2["target"])
    c2 = json.loads(
        (seed2["target"] / "thermal" / "commit.json").read_text("utf-8")
    )
    assert c1["faiss_commit_id"] == c2["faiss_commit_id"]


def test_verify_clean_after_apply(tmp_path, monkeypatch):
    seed = _seed_legacy(tmp_path)
    _repoint_mod(monkeypatch, seed)
    mod.apply_migration(seed["target"])
    report = mod.verify_migration(seed["target"])
    assert report["drift"] == []
    assert len(report["ok"]) >= len(seed["cells"]) * 2  # index + meta per cell


def test_verify_detects_drift_when_source_changes(tmp_path, monkeypatch):
    seed = _seed_legacy(tmp_path)
    _repoint_mod(monkeypatch, seed)
    mod.apply_migration(seed["target"])

    # Change the source index AFTER migration → verify should complain
    (seed["staging"] / "thermal" / "index.faiss").write_bytes(b"DRIFTED")
    report = mod.verify_migration(seed["target"])
    assert any(d["cell"] == "thermal" for d in report["drift"])


def test_migration_does_not_require_ledger_dir(tmp_path, monkeypatch):
    """Apply must succeed even if the ledger dir is empty for a cell."""
    seed = _seed_legacy(tmp_path, cells=("thermal",))
    # Remove the ledger for thermal
    import shutil as _sh
    _sh.rmtree(seed["ledger"] / "thermal")
    _repoint_mod(monkeypatch, seed)
    out = mod.apply_migration(seed["target"])
    assert out["cells"][0]["ledger_entries"] == 0
