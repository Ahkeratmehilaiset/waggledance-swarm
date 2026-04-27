"""Tests for tools/hex_subdivision_plan.py.

Focus: planner never mutates runtime topology, emits plan markdown
deterministically, surfaces every trigger that crosses its threshold,
and emits no candidate when thresholds are all safely above current
state.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parent.parent


def _load_planner():
    path = ROOT / "tools" / "hex_subdivision_plan.py"
    spec = importlib.util.spec_from_file_location("hex_subdivision_plan", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["hex_subdivision_plan"] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load_planner()


def _fake_manifest(cells_dir: Path, cell: str, payload: dict):
    d = cells_dir / cell
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def test_no_candidates_when_thresholds_unmet(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "CELLS_DIR", tmp_path / "cells")
    monkeypatch.setattr(mod, "_gather_library", lambda: [])
    # manifest that's small and clean
    _fake_manifest(tmp_path / "cells", "thermal", {
        "cell_id": "thermal",
        "solver_count": 2,
        "gap_score": 0.1,
        "llm_fallback_rate": 0.05,
        "recent_rejections": [],
    })
    res = mod.run(["thermal"], plan_path=tmp_path / "plan.md")
    assert res["candidates"] == []


def test_solver_count_trigger_fires(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "CELLS_DIR", tmp_path / "cells")
    monkeypatch.setattr(mod, "_gather_library", lambda: [])
    _fake_manifest(tmp_path / "cells", "thermal", {
        "cell_id": "thermal",
        "solver_count": 100,
        "gap_score": 0,
        "llm_fallback_rate": 0,
        "recent_rejections": [],
    })
    res = mod.run(["thermal"], plan_path=tmp_path / "plan.md",
                  thresholds={"solver_count": 30})
    names = {t["name"] for c in res["candidates"] for t in c["triggers"]}
    assert "solver_count" in names


def test_gap_score_trigger_fires(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "CELLS_DIR", tmp_path / "cells")
    monkeypatch.setattr(mod, "_gather_library", lambda: [])
    _fake_manifest(tmp_path / "cells", "energy", {
        "cell_id": "energy", "solver_count": 5, "gap_score": 0.9,
        "llm_fallback_rate": 0, "recent_rejections": [],
    })
    res = mod.run(["energy"], plan_path=tmp_path / "plan.md",
                  thresholds={"gap_score": 0.7})
    names = {t["name"] for c in res["candidates"] for t in c["triggers"]}
    assert "cell_gap_score" in names


def test_fallback_rate_trigger_fires(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "CELLS_DIR", tmp_path / "cells")
    monkeypatch.setattr(mod, "_gather_library", lambda: [])
    _fake_manifest(tmp_path / "cells", "safety", {
        "cell_id": "safety", "solver_count": 5, "gap_score": 0,
        "llm_fallback_rate": 0.8, "recent_rejections": [],
    })
    res = mod.run(["safety"], plan_path=tmp_path / "plan.md",
                  thresholds={"fallback_rate": 0.5})
    names = {t["name"] for c in res["candidates"] for t in c["triggers"]}
    assert "llm_fallback_rate" in names


def test_rejections_trigger_fires(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "CELLS_DIR", tmp_path / "cells")
    monkeypatch.setattr(mod, "_gather_library", lambda: [])
    _fake_manifest(tmp_path / "cells", "math", {
        "cell_id": "math", "solver_count": 5, "gap_score": 0,
        "llm_fallback_rate": 0,
        "recent_rejections": [{"reason": "scope too broad"}] * 4,
    })
    res = mod.run(["math"], plan_path=tmp_path / "plan.md",
                  thresholds={"rejections": 3})
    names = {t["name"] for c in res["candidates"] for t in c["triggers"]}
    assert "proposal_rejections" in names


def test_entropy_trigger_fires_from_graph(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "CELLS_DIR", tmp_path / "cells")
    _fake_manifest(tmp_path / "cells", "thermal", {
        "cell_id": "thermal", "solver_count": 3, "gap_score": 0,
        "llm_fallback_rate": 0, "recent_rejections": [],
    })
    # Build a library with 7 distinct output units in thermal
    library = []
    for i, u in enumerate(["W", "kW", "ratio", "degC", "kWh", "Pa", "m3"]):
        library.append({
            "id": f"s{i}", "cell": "thermal",
            "inputs": [{"name": "x", "unit": "m"}],
            "outputs": [{"name": "o", "unit": u}],
        })
    monkeypatch.setattr(mod, "_gather_library", lambda: library)
    res = mod.run(["thermal"], plan_path=tmp_path / "plan.md",
                  thresholds={"entropy": 6})
    names = {t["name"] for c in res["candidates"] for t in c["triggers"]}
    assert "output_unit_entropy" in names


def test_planner_never_mutates_topology_file(tmp_path, monkeypatch):
    """The planner may be run any time; it must not touch
    waggledance/core/hex_cell_topology.py or configs/axioms."""
    monkeypatch.setattr(mod, "CELLS_DIR", tmp_path / "cells")
    monkeypatch.setattr(mod, "_gather_library", lambda: [])
    _fake_manifest(tmp_path / "cells", "thermal", {
        "cell_id": "thermal", "solver_count": 999, "gap_score": 0.99,
        "llm_fallback_rate": 0.99, "recent_rejections": [{"r": 1}] * 10,
    })
    topology = ROOT / "waggledance" / "core" / "hex_cell_topology.py"
    before_mtime = topology.stat().st_mtime
    before_content = topology.read_bytes()
    mod.run(["thermal"], plan_path=tmp_path / "plan.md")
    after_mtime = topology.stat().st_mtime
    after_content = topology.read_bytes()
    assert before_mtime == after_mtime
    assert before_content == after_content


def test_plan_markdown_deterministic_modulo_timestamp(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "CELLS_DIR", tmp_path / "cells")
    monkeypatch.setattr(mod, "_gather_library", lambda: [])
    _fake_manifest(tmp_path / "cells", "safety", {
        "cell_id": "safety", "solver_count": 200, "gap_score": 0.9,
        "llm_fallback_rate": 0, "recent_rejections": [],
    })
    r1 = mod.run(["safety"], plan_path=tmp_path / "p1.md")
    r2 = mod.run(["safety"], plan_path=tmp_path / "p2.md")
    # Structural result equal
    assert r1["candidates"] == r2["candidates"]
    # Files equal modulo the "Generated:" header line
    def _strip_ts(text: str) -> str:
        return "\n".join(
            l for l in text.splitlines()
            if not l.lstrip().startswith("- **Generated:**")
        )
    assert _strip_ts((tmp_path / "p1.md").read_text("utf-8")) == \
           _strip_ts((tmp_path / "p2.md").read_text("utf-8"))


def test_candidate_contains_rollback_and_tests(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "CELLS_DIR", tmp_path / "cells")
    monkeypatch.setattr(mod, "_gather_library", lambda: [])
    _fake_manifest(tmp_path / "cells", "thermal", {
        "cell_id": "thermal", "solver_count": 100, "gap_score": 0,
        "llm_fallback_rate": 0, "recent_rejections": [],
    })
    res = mod.run(["thermal"], plan_path=tmp_path / "plan.md")
    assert res["candidates"]
    c = res["candidates"][0]
    assert c["rollback_plan"]
    assert c["tests_needed"]
    assert c["expected_benefit"]
    assert c["risk"]


def test_severity_ordering_deterministic(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "CELLS_DIR", tmp_path / "cells")
    monkeypatch.setattr(mod, "_gather_library", lambda: [])
    _fake_manifest(tmp_path / "cells", "a", {
        "cell_id": "a", "solver_count": 200, "gap_score": 0.95,
        "llm_fallback_rate": 0.9, "recent_rejections": [{"r": 1}] * 8,
    })
    _fake_manifest(tmp_path / "cells", "b", {
        "cell_id": "b", "solver_count": 35, "gap_score": 0,
        "llm_fallback_rate": 0, "recent_rejections": [],
    })
    res = mod.run(["a", "b"], plan_path=tmp_path / "plan.md")
    # a has far worse metrics → should come first
    assert res["candidates"][0]["parent_cell"] == "a"
    assert res["candidates"][0]["severity"] >= res["candidates"][1]["severity"]


def test_plan_has_no_secrets_no_absolute_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "CELLS_DIR", tmp_path / "cells")
    monkeypatch.setattr(mod, "_gather_library", lambda: [])
    _fake_manifest(tmp_path / "cells", "thermal", {
        "cell_id": "thermal", "solver_count": 100, "gap_score": 0,
        "llm_fallback_rate": 0, "recent_rejections": [],
    })
    res = mod.run(["thermal"], plan_path=tmp_path / "plan.md")
    text = (tmp_path / "plan.md").read_text("utf-8")
    assert "WAGGLE_API_KEY" not in text
    assert "gnt_" not in text
    assert "C:\\Users" not in text
    assert "Bearer " not in text
