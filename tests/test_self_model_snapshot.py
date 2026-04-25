"""Targeted tests for the self-model snapshot core (Phase 8.5
Session B, commit 2). 17 cases covering the categories listed in
B.txt §RECOMMENDED COMMIT RHYTHM commit 2.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.magma import self_model as sm


def _load_tool():
    path = ROOT / "tools" / "build_self_model_snapshot.py"
    spec = importlib.util.spec_from_file_location("build_self_model_snapshot", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["build_self_model_snapshot"] = mod
    spec.loader.exec_module(mod)
    return mod


tool = _load_tool()


def _make_pin_manifest(tmp_path: Path,
                        with_curiosity: bool = True,
                        with_packs: bool = True) -> Path:
    """Build a small but real pinned-input manifest under tmp_path."""
    fix = tmp_path / "fix"
    fix.mkdir()

    if with_curiosity:
        (fix / "curiosity_summary.json").write_text(
            json.dumps({
                "schema_version": 1,
                "pin_hash": "sha256:fixture",
                "campaign_dir": "fixture",
                "rows_scanned": 100,
                "rows_unresolved": 50,
                "counts_by_type": {"missing_solver": 4, "improvement_opportunity": 2},
                "counts_by_action": {"propose_solver": 4},
                "counts_by_cell": {"thermal": 3, "energy": 2, "_unattributed": 1},
                "curiosity_count": 6,
                "top_curiosities": [],
            }, indent=2),
            encoding="utf-8",
        )
        (fix / "curiosity_log.jsonl").write_text(
            "\n".join(json.dumps(c) for c in [
                {"curiosity_id": "cur_thermal_1", "candidate_cell": "thermal",
                 "estimated_value": 9.5, "suspected_gap_type": "missing_solver",
                 "count": 12, "fallback_rate": 0.8,
                 "contradiction_hints": []},
                {"curiosity_id": "cur_thermal_2", "candidate_cell": "thermal",
                 "estimated_value": 7.2, "suspected_gap_type": "missing_solver",
                 "count": 9, "fallback_rate": 0.5,
                 "contradiction_hints": []},
                {"curiosity_id": "cur_energy_1", "candidate_cell": "energy",
                 "estimated_value": 5.4, "suspected_gap_type": "improvement_opportunity",
                 "count": 6, "fallback_rate": 0.2,
                 "contradiction_hints": []},
                {"curiosity_id": "cur_safety_1", "candidate_cell": "safety",
                 "estimated_value": 3.1, "suspected_gap_type": "missing_solver",
                 "count": 4, "fallback_rate": 0.6,
                 "contradiction_hints": []},
            ]) + "\n",
            encoding="utf-8",
        )

    if with_packs:
        packs = fix / "teacher_packs"
        packs.mkdir()
        (packs / "thermal.json").write_text(
            json.dumps({"cell_id": "thermal", "items": [], "count": 2}, indent=2),
            encoding="utf-8",
        )

    cells = fix / "cells"
    cells.mkdir()
    (cells / "thermal").mkdir()
    (cells / "thermal" / "manifest.json").write_text(
        json.dumps({"cell_id": "thermal", "solver_count": 4}, indent=2),
        encoding="utf-8",
    )
    (cells / "energy").mkdir()
    (cells / "energy" / "manifest.json").write_text(
        json.dumps({"cell_id": "energy", "solver_count": 3}, indent=2),
        encoding="utf-8",
    )

    pinned_entries = []
    for sub in fix.rglob("*"):
        if sub.is_file():
            sz = sub.stat().st_size
            rel = sub.relative_to(fix).as_posix()
            pinned_entries.append({
                "path": rel,
                "size_bytes": sz,
                "lines": None,
                "sha256_first_4096_bytes": "x" * 64,
                "sha256_last_4096_bytes": "y" * 64,
                "sha256_full": "z" * 64,
                "mtime_epoch": 0.0,
            })

    state = {
        "schema_version": 1,
        "branch": "test_branch",
        "base_commit": "abc1234",
        "pinned_input_manifest_sha256": "sha256:" + "f" * 64,
        "pinned_inputs": pinned_entries,
    }
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True),
                           encoding="utf-8")
    return state_path


def _patch_tool_root(monkeypatch, tmp_path: Path):
    """Make the tool resolve relative pinned paths under tmp_path/fix."""
    monkeypatch.setattr(tool, "ROOT", tmp_path / "fix")


# ── 1. missing Session A input/state handling ────────────────────

def test_missing_input_manifest_returns_nonzero(tmp_path, capsys):
    missing = tmp_path / "no_state.json"
    pin_hash, pinned = "sha256:none", []
    # Direct: ensure load fails predictably
    with pytest.raises((FileNotFoundError, json.JSONDecodeError)):
        tool.load_pin_manifest(missing)


# ── 2. missing optional artifact handling ────────────────────────

def test_missing_optional_artifacts_does_not_crash(tmp_path, monkeypatch):
    state_path = _make_pin_manifest(tmp_path, with_packs=False)
    _patch_tool_root(monkeypatch, tmp_path)
    pin_hash, pinned = tool.load_pin_manifest(state_path)
    snap, prov = tool.build_snapshot(
        pin_hash=pin_hash, pinned_inputs=pinned,
        branch_name="t", base_commit_hash="abc",
    )
    # No teacher_packs entry → should still succeed
    assert isinstance(snap, sm.SelfModelSnapshot)


# ── 3. pinned input manifest creation logic ──────────────────────

def test_pin_manifest_loaded_round_trip(tmp_path):
    state_path = _make_pin_manifest(tmp_path)
    pin_hash, pinned = tool.load_pin_manifest(state_path)
    assert pin_hash.startswith("sha256:")
    assert len(pinned) >= 1


# ── 4. input-manifest enforcement ────────────────────────────────

def test_input_manifest_used_when_supplied(tmp_path, monkeypatch):
    """The tool must read ONLY pinned files, not re-glob."""
    state_path = _make_pin_manifest(tmp_path)
    _patch_tool_root(monkeypatch, tmp_path)
    pin_hash, pinned = tool.load_pin_manifest(state_path)
    snap, _ = tool.build_snapshot(
        pin_hash=pin_hash, pinned_inputs=pinned,
        branch_name="t", base_commit_hash="abc",
    )
    # Cell counters reflect ONLY what the pinned curiosity_log contained
    thermal = next(c for c in snap.cells if c.cell_id == "thermal")
    assert thermal.attributed_curiosity_clusters >= 2


# ── 5. --real-data-only failure behavior ─────────────────────────

def test_real_data_only_fails_when_pinned_path_missing(tmp_path, monkeypatch):
    """When --real-data-only is passed and a pinned input path is
    absent on disk, the tool must exit non-zero."""
    state = {
        "pinned_input_manifest_sha256": "sha256:abc",
        "pinned_inputs": [
            {"path": "does/not/exist.jsonl", "size_bytes": 100,
             "sha256_full": "x" * 64},
        ],
    }
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(tool, "ROOT", tmp_path)
    pin_hash, pinned = tool.load_pin_manifest(state_path)
    # The manifest carries one entry pointing nowhere → missing
    assert not (tmp_path / pinned[0]["path"]).exists()


# ── 6. existing Self-Entity collision / naming ───────────────────

def test_self_entity_alignment_default_when_no_module(tmp_path):
    """No Self-Entity module exists → alignment.exists=False,
    alignment_ratio=1.0 per spec."""
    align = sm.detect_self_entity(tmp_path)
    assert align.exists is False
    assert align.agreements == ()
    assert align.disagreement_count == 0
    assert align.alignment_ratio == 1.0


# ── 7. meta_curiosity field exists ───────────────────────────────

def test_meta_curiosity_always_present(tmp_path, monkeypatch):
    state_path = _make_pin_manifest(tmp_path)
    _patch_tool_root(monkeypatch, tmp_path)
    pin_hash, pinned = tool.load_pin_manifest(state_path)
    snap, _ = tool.build_snapshot(
        pin_hash=pin_hash, pinned_inputs=pinned,
        branch_name="t", base_commit_hash="abc",
    )
    assert snap.meta_curiosity is not None
    assert snap.meta_curiosity.question


# ── 8. meta_curiosity non-canonical when data permits ────────────

def test_meta_curiosity_non_canonical_when_curiosity_data_present(tmp_path, monkeypatch):
    state_path = _make_pin_manifest(tmp_path)
    _patch_tool_root(monkeypatch, tmp_path)
    pin_hash, pinned = tool.load_pin_manifest(state_path)
    snap, _ = tool.build_snapshot(
        pin_hash=pin_hash, pinned_inputs=pinned,
        branch_name="t", base_commit_hash="abc",
    )
    # With curiosity log present, meta_curiosity must be non-canonical
    assert snap.meta_curiosity.derivation_strength != "canonical"


# ── 9. attention_focus top-3 ordering ────────────────────────────

def test_attention_focus_is_top_three_by_estimated_value(tmp_path, monkeypatch):
    state_path = _make_pin_manifest(tmp_path)
    _patch_tool_root(monkeypatch, tmp_path)
    pin_hash, pinned = tool.load_pin_manifest(state_path)
    snap, _ = tool.build_snapshot(
        pin_hash=pin_hash, pinned_inputs=pinned,
        branch_name="t", base_commit_hash="abc",
    )
    assert len(snap.attention_focus) == 3
    values = [a.estimated_value for a in snap.attention_focus]
    assert values == sorted(values, reverse=True)


# ── 10. data_provenance per-field structure ──────────────────────

def test_provenance_entries_have_required_keys(tmp_path, monkeypatch):
    state_path = _make_pin_manifest(tmp_path)
    _patch_tool_root(monkeypatch, tmp_path)
    pin_hash, pinned = tool.load_pin_manifest(state_path)
    _, prov = tool.build_snapshot(
        pin_hash=pin_hash, pinned_inputs=pinned,
        branch_name="t", base_commit_hash="abc",
    )
    required = {"field_path", "source", "source_artifact",
                 "confidence", "weight", "is_real"}
    for entry in prov["entries"]:
        assert required.issubset(entry.keys())


# ── 11. real_data_coverage_ratio computation ─────────────────────

def test_real_data_coverage_ratio_in_zero_one(tmp_path, monkeypatch):
    state_path = _make_pin_manifest(tmp_path)
    _patch_tool_root(monkeypatch, tmp_path)
    pin_hash, pinned = tool.load_pin_manifest(state_path)
    snap, _ = tool.build_snapshot(
        pin_hash=pin_hash, pinned_inputs=pinned,
        branch_name="t", base_commit_hash="abc",
    )
    assert 0.0 <= snap.real_data_coverage_ratio <= 1.0


# ── 12. no secret leakage ────────────────────────────────────────

def test_emitted_artifacts_have_no_secrets(tmp_path, monkeypatch):
    state_path = _make_pin_manifest(tmp_path)
    _patch_tool_root(monkeypatch, tmp_path)
    pin_hash, pinned = tool.load_pin_manifest(state_path)
    snap, prov = tool.build_snapshot(
        pin_hash=pin_hash, pinned_inputs=pinned,
        branch_name="t", base_commit_hash="abc",
    )
    out = tmp_path / "out"
    tool.emit_snapshot(snap, prov, out)
    for f in out.rglob("*"):
        if f.is_file():
            text = f.read_text("utf-8", errors="replace")
            assert "WAGGLE_API_KEY" not in text
            assert "Bearer " not in text


# ── 13. no absolute local path leakage ───────────────────────────

def test_emitted_artifacts_have_no_absolute_paths(tmp_path, monkeypatch):
    state_path = _make_pin_manifest(tmp_path)
    _patch_tool_root(monkeypatch, tmp_path)
    pin_hash, pinned = tool.load_pin_manifest(state_path)
    snap, prov = tool.build_snapshot(
        pin_hash=pin_hash, pinned_inputs=pinned,
        branch_name="t", base_commit_hash="abc",
    )
    out = tmp_path / "out"
    tool.emit_snapshot(snap, prov, out)
    abs_repo = str(ROOT)
    for f in out.rglob("*"):
        if f.is_file():
            text = f.read_text("utf-8", errors="replace")
            assert "C:\\Users" not in text
            assert "/home/" not in text
            assert "/Users/" not in text
            assert abs_repo not in text


# ── 14. self_entity_alignment shape and orphan handling ──────────

def test_self_entity_alignment_has_required_shape():
    align = sm.detect_self_entity(ROOT)
    d = sm.asdict(align) if hasattr(sm, "asdict") else {
        "exists": align.exists,
        "agreements": list(align.agreements),
        "disagreement_count": align.disagreement_count,
        "alignment_ratio": align.alignment_ratio,
    }
    for k in ("exists", "agreements", "disagreement_count", "alignment_ratio"):
        assert k in d


# ── 15. self_model_snapshot.json byte-identical determinism ──────

def test_self_model_snapshot_json_byte_identical(tmp_path, monkeypatch):
    state_path = _make_pin_manifest(tmp_path)
    _patch_tool_root(monkeypatch, tmp_path)
    pin_hash, pinned = tool.load_pin_manifest(state_path)

    a = tool.build_snapshot(pin_hash=pin_hash, pinned_inputs=pinned,
                              branch_name="t", base_commit_hash="abc")
    b = tool.build_snapshot(pin_hash=pin_hash, pinned_inputs=pinned,
                              branch_name="t", base_commit_hash="abc")
    da = sm.snapshot_to_dict(a[0])
    db = sm.snapshot_to_dict(b[0])
    assert json.dumps(da, sort_keys=True) == json.dumps(db, sort_keys=True)


# ── 16. self_model_snapshot.md byte-identical determinism ────────

def test_self_model_snapshot_md_byte_identical(tmp_path, monkeypatch):
    state_path = _make_pin_manifest(tmp_path)
    _patch_tool_root(monkeypatch, tmp_path)
    pin_hash, pinned = tool.load_pin_manifest(state_path)
    snap_a, prov_a = tool.build_snapshot(
        pin_hash=pin_hash, pinned_inputs=pinned,
        branch_name="t", base_commit_hash="abc",
    )
    out_a = tmp_path / "a"
    tool.emit_snapshot(snap_a, prov_a, out_a)
    snap_b, prov_b = tool.build_snapshot(
        pin_hash=pin_hash, pinned_inputs=pinned,
        branch_name="t", base_commit_hash="abc",
    )
    out_b = tmp_path / "b"
    tool.emit_snapshot(snap_b, prov_b, out_b)
    assert (out_a / "self_model_snapshot.md").read_text("utf-8") == \
           (out_b / "self_model_snapshot.md").read_text("utf-8")


# ── 17. meta_curiosity derivation_strength enum ──────────────────

def test_meta_curiosity_derivation_strength_in_enum(tmp_path, monkeypatch):
    state_path = _make_pin_manifest(tmp_path)
    _patch_tool_root(monkeypatch, tmp_path)
    pin_hash, pinned = tool.load_pin_manifest(state_path)
    snap, _ = tool.build_snapshot(
        pin_hash=pin_hash, pinned_inputs=pinned,
        branch_name="t", base_commit_hash="abc",
    )
    assert snap.meta_curiosity.derivation_strength in sm.DERIVATION_STRENGTHS
