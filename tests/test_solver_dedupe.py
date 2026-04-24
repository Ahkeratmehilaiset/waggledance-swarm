"""Tests for tools/solver_dedupe.py.

Covers the x.txt Phase 3 requirement that the duplicate report is stable
(same axioms dir → identical summary_hash across runs).
"""
from __future__ import annotations

import importlib.util
import sys
import yaml
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _load_dedupe():
    path = ROOT / "tools" / "solver_dedupe.py"
    spec = importlib.util.spec_from_file_location("solver_dedupe", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["solver_dedupe"] = mod
    spec.loader.exec_module(mod)
    return mod


def _axiom(model_id, formula="a + b", out_unit="m",
           output_name="out", tags=None, check="a > 0"):
    return {
        "model_id": model_id,
        "model_name": f"Model {model_id}",
        "description": "something",
        "formulas": [
            {"name": "f", "formula": formula, "output_unit": out_unit},
        ],
        "variables": {"a": {"unit": "m"}, "b": {"unit": "m"}},
        "solver_output_schema": {"primary_value": {"name": output_name, "unit": out_unit}},
        "validation": [{"check": check, "message": "m"}],
        "tags": tags or ["thermal"],
    }


def test_report_is_stable_across_runs(tmp_path):
    mod = _load_dedupe()
    axioms_dir = tmp_path / "axioms" / "x"
    axioms_dir.mkdir(parents=True)
    (axioms_dir / "a.yaml").write_text(yaml.safe_dump(_axiom("a")), encoding="utf-8")
    (axioms_dir / "b.yaml").write_text(yaml.safe_dump(_axiom("b", formula="x * y")), encoding="utf-8")

    r1 = mod.run_dedupe(tmp_path / "axioms", tmp_path / "report1.md")
    r2 = mod.run_dedupe(tmp_path / "axioms", tmp_path / "report2.md")

    # Summary hash must be identical across runs (x.txt Phase 3 requirement)
    assert r1["summary_hash"] == r2["summary_hash"]
    # And JSON summary must be equal
    assert r1["summary"] == r2["summary"]


def test_detects_strict_duplicate_across_files(tmp_path):
    mod = _load_dedupe()
    axioms_dir = tmp_path / "axioms" / "x"
    axioms_dir.mkdir(parents=True)
    # Two files with identical semantic shape but different filenames
    dup = _axiom("m1")
    (axioms_dir / "a.yaml").write_text(yaml.safe_dump(dup), encoding="utf-8")
    # Distinct model_id, same formulas/variables/outputs/conditions/tags
    dup2 = _axiom("m2")
    (axioms_dir / "b.yaml").write_text(yaml.safe_dump(dup2), encoding="utf-8")

    r = mod.run_dedupe(tmp_path / "axioms", tmp_path / "report.md")
    assert r["summary"]["strict_duplicate_groups"] == 1
    # The one group must contain both files
    groups = r["summary"]["strict_duplicates"]
    assert len(groups) == 1
    group_paths = next(iter(groups.values()))
    assert len(group_paths) == 2


def test_distinct_axioms_are_not_duplicates(tmp_path):
    mod = _load_dedupe()
    axioms_dir = tmp_path / "axioms" / "x"
    axioms_dir.mkdir(parents=True)
    (axioms_dir / "a.yaml").write_text(
        yaml.safe_dump(_axiom("honey", formula="colony * nectar")),
        encoding="utf-8",
    )
    (axioms_dir / "b.yaml").write_text(
        yaml.safe_dump(_axiom("heat", formula="area * dT / R")),
        encoding="utf-8",
    )

    r = mod.run_dedupe(tmp_path / "axioms", tmp_path / "report.md")
    assert r["summary"]["strict_duplicate_groups"] == 0
    assert r["summary"]["legacy_collision_groups"] == 0


def test_legacy_collision_surfaces_when_only_output_unit_differs(tmp_path):
    """Two axioms with identical formula/variable/condition shape but
    different output units should NOT be strict dupes, but SHOULD
    surface as a legacy collision to review."""
    mod = _load_dedupe()
    axioms_dir = tmp_path / "axioms" / "x"
    axioms_dir.mkdir(parents=True)
    (axioms_dir / "watts.yaml").write_text(
        yaml.safe_dump(_axiom("m1", out_unit="W", output_name="p")),
        encoding="utf-8",
    )
    (axioms_dir / "kw.yaml").write_text(
        yaml.safe_dump(_axiom("m2", out_unit="kW", output_name="p")),
        encoding="utf-8",
    )

    r = mod.run_dedupe(tmp_path / "axioms", tmp_path / "report.md")
    # Legacy hash matches (formulas/variables identical) but strict hashes
    # differ (output unit differs)
    assert r["summary"]["strict_duplicate_groups"] == 0
    assert r["summary"]["legacy_collision_groups"] == 1


def test_report_markdown_has_no_secrets_no_absolute_paths(tmp_path):
    mod = _load_dedupe()
    axioms_dir = tmp_path / "axioms" / "x"
    axioms_dir.mkdir(parents=True)
    (axioms_dir / "a.yaml").write_text(yaml.safe_dump(_axiom("a")), encoding="utf-8")

    report_path = tmp_path / "report.md"
    mod.run_dedupe(tmp_path / "axioms", report_path)
    md = report_path.read_text("utf-8")
    assert "WAGGLE_API_KEY" not in md
    assert "Bearer " not in md
    assert "gnt_" not in md


def test_skips_unparseable_yaml(tmp_path):
    mod = _load_dedupe()
    axioms_dir = tmp_path / "axioms" / "x"
    axioms_dir.mkdir(parents=True)
    (axioms_dir / "good.yaml").write_text(
        yaml.safe_dump(_axiom("m1")), encoding="utf-8"
    )
    (axioms_dir / "broken.yaml").write_text(": : garbage\n:\n:::", encoding="utf-8")

    r = mod.run_dedupe(tmp_path / "axioms", tmp_path / "report.md")
    # 2 files scanned, 1 parseable
    assert r["summary"]["scanned"] == 2
    assert r["summary"]["parseable"] == 1
