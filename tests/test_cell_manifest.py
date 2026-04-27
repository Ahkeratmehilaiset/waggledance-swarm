"""Tests for tools/cell_manifest.py.

Scope: structure, determinism, portability, and the "no fabricated data"
rule from x.txt Phase 2. Runs offline — does not touch port 8002.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _load_cell_manifest():
    """Load tools/cell_manifest.py as a module without running main()."""
    path = ROOT / "tools" / "cell_manifest.py"
    spec = importlib.util.spec_from_file_location("cell_manifest", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cell_manifest"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_module_imports():
    mod = _load_cell_manifest()
    assert hasattr(mod, "generate_cell_manifest")
    assert hasattr(mod, "_manifest_payload")
    assert hasattr(mod, "_compute_hash")
    assert mod.MANIFEST_SCHEMA_VERSION >= 1


def test_all_known_cells_produce_a_payload_with_required_fields(tmp_path, monkeypatch):
    mod = _load_cell_manifest()
    # Redirect output dir so we don't touch docs/cells during tests
    monkeypatch.setattr(mod, "CELLS_OUT_DIR", tmp_path)

    all_solvers = mod._collect_all_solvers()

    required = {
        "schema_version", "cell_id", "parent", "level", "siblings",
        "neighbors", "solver_count", "solvers", "top_open_gaps",
        "top_fallback_queries", "recent_rejections",
        "candidate_bridge_edges", "training_pair_count",
        "contradiction_count", "latency_p50_ms", "latency_p95_ms",
        "llm_fallback_rate", "gap_score", "teacher_protocol_reminder",
    }
    for cell in mod.CELLS:
        payload = mod._manifest_payload(cell, all_solvers, hot_rows=[])
        missing = required - set(payload.keys())
        assert not missing, f"cell {cell} missing fields: {missing}"
        assert payload["cell_id"] == cell
        assert payload["level"] == 0
        assert payload["parent"] is None
        assert isinstance(payload["solvers"], list)
        assert payload["solver_count"] == len(payload["solvers"])


def test_manifest_payload_is_deterministic():
    """Same inputs → identical payload (minus timestamp and hash) on repeat runs."""
    mod = _load_cell_manifest()
    all_solvers = mod._collect_all_solvers()
    p1 = mod._manifest_payload("thermal", all_solvers, hot_rows=[])
    p2 = mod._manifest_payload("thermal", all_solvers, hot_rows=[])
    assert p1 == p2

    h1 = mod._compute_hash(p1)
    h2 = mod._compute_hash(p2)
    assert h1 == h2
    assert h1.startswith("sha256:")


def test_manifest_hash_ignores_generated_at_and_hash_itself():
    mod = _load_cell_manifest()
    all_solvers = mod._collect_all_solvers()
    payload = mod._manifest_payload("math", all_solvers, hot_rows=[])

    h0 = mod._compute_hash(payload)
    # Mutating excluded keys must not change the hash
    payload_b = dict(payload)
    payload_b["generated_at"] = "2026-04-24T00:00:00+00:00"
    payload_b["manifest_hash"] = "sha256:deadbeef"
    h1 = mod._compute_hash(payload_b)
    assert h0 == h1

    # Mutating a semantic field must change the hash
    payload_c = dict(payload)
    payload_c["solver_count"] = payload["solver_count"] + 1
    h2 = mod._compute_hash(payload_c)
    assert h0 != h2


def test_production_signals_explicit_null_when_no_data():
    mod = _load_cell_manifest()
    out = mod._production_signals("thermal", hot_rows=[])
    # No fabricated metric values — all must be explicit nulls / empty lists
    assert out["latency_p50_ms"] is None
    assert out["latency_p95_ms"] is None
    assert out["llm_fallback_rate"] is None
    assert out["unresolved_examples"] == []
    assert out["top_fallback_queries"] == []
    assert out["unresolved_count"] == 0
    assert out["training_pair_count"] == 0


def test_siblings_match_adjacency_and_are_sorted():
    mod = _load_cell_manifest()
    all_solvers = mod._collect_all_solvers()
    for cell in mod.CELLS:
        payload = mod._manifest_payload(cell, all_solvers, hot_rows=[])
        assert payload["siblings"] == sorted(mod._ADJACENCY.get(cell, set()))
        # ring-2 must be disjoint from ring-1 and must not contain self
        ring1 = set(payload["siblings"])
        ring2 = set(payload["neighbors"])
        assert ring2.isdisjoint(ring1)
        assert cell not in ring2


def test_solver_signature_is_stable_across_runs():
    mod = _load_cell_manifest()
    solvers_a = mod._collect_all_solvers()
    solvers_b = mod._collect_all_solvers()
    sig_a = {s["id"]: s["signature"] for s in solvers_a}
    sig_b = {s["id"]: s["signature"] for s in solvers_b}
    assert sig_a == sig_b
    for sig in sig_a.values():
        assert re.fullmatch(r"[0-9a-f]{16}", sig), sig


def test_generate_writes_deterministic_json(tmp_path, monkeypatch):
    mod = _load_cell_manifest()
    monkeypatch.setattr(mod, "CELLS_OUT_DIR", tmp_path)

    # Run twice, read JSON, compare hashes (ignoring generated_at)
    _, _ = mod.generate_cell_manifest("energy", hot_rows=[])
    payload1 = json.loads((tmp_path / "energy" / "manifest.json").read_text("utf-8"))
    h1 = payload1["manifest_hash"]

    _, _ = mod.generate_cell_manifest("energy", hot_rows=[])
    payload2 = json.loads((tmp_path / "energy" / "manifest.json").read_text("utf-8"))
    h2 = payload2["manifest_hash"]

    assert h1 == h2


def test_manifest_has_no_secrets_and_no_absolute_local_paths(tmp_path, monkeypatch):
    mod = _load_cell_manifest()
    monkeypatch.setattr(mod, "CELLS_OUT_DIR", tmp_path)

    for cell in mod.CELLS:
        mod.generate_cell_manifest(cell, hot_rows=[])
    blob = ""
    for f in tmp_path.rglob("*"):
        if f.is_file():
            blob += f.read_text("utf-8", errors="ignore")

    # No API-key-style tokens
    assert "WAGGLE_API_KEY" not in blob
    assert "gnt_" not in blob
    # No Windows absolute paths into user profile or drive letters other
    # than posix-style relative paths inside repo
    assert "C:\\" not in blob
    assert "U:\\" not in blob
    assert "C:/Users" not in blob
    # No bearer tokens or common secret prefixes
    assert "Bearer " not in blob


def test_manifest_hash_format():
    mod = _load_cell_manifest()
    all_solvers = mod._collect_all_solvers()
    payload = mod._manifest_payload("general", all_solvers, hot_rows=[])
    payload["manifest_hash"] = mod._compute_hash(payload)
    h = payload["manifest_hash"]
    assert h.startswith("sha256:")
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", h)


def test_canonical_json_is_stable_under_key_reordering():
    mod = _load_cell_manifest()
    a = {"b": 1, "a": 2, "c": {"z": 9, "y": 8}}
    b = {"c": {"y": 8, "z": 9}, "a": 2, "b": 1}
    assert mod._canonical_json(a) == mod._canonical_json(b)
