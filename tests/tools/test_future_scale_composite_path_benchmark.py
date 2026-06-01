from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _load_tool():
    path = ROOT / "tools" / "run_future_scale_composite_path_benchmark.py"
    spec = importlib.util.spec_from_file_location(
        "run_future_scale_composite_path_benchmark",
        path,
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_future_scale_composite_path_benchmark"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_axiom(
    path: Path,
    *,
    model_id: str,
    cell_id: str,
    input_unit: str,
    output_unit: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                f"model_id: {model_id}",
                f"cell_id: {cell_id}",
                "variables:",
                "  input:",
                f"    unit: {input_unit}",
                "solver_output_schema:",
                "  primary_value:",
                "    name: output",
                f"    unit: {output_unit}",
                "formulas:",
                "  - name: output",
                "    formula: input",
                f"    output_unit: {output_unit}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _seed_composite_axioms(root: Path) -> Path:
    axioms = root / "axioms"
    _write_axiom(
        axioms / "thermal" / "heat_power.yaml",
        model_id="heat_power",
        cell_id="thermal",
        input_unit="m",
        output_unit="W",
    )
    _write_axiom(
        axioms / "energy" / "power_to_energy.yaml",
        model_id="power_to_energy",
        cell_id="energy",
        input_unit="W",
        output_unit="kWh",
    )
    _write_axiom(
        axioms / "math" / "energy_ratio.yaml",
        model_id="energy_ratio",
        cell_id="math",
        input_unit="kWh",
        output_unit="ratio",
    )
    return axioms


def test_composite_path_benchmark_measures_local_paths_without_claim_upgrade(
    tmp_path: Path,
) -> None:
    tool = _load_tool()
    benchmark = tool.build_composite_path_benchmark(
        _seed_composite_axioms(tmp_path),
        max_depth=4,
    )

    assert benchmark["schema_version"] == "future_scale.composite_path_benchmark.v1"
    assert benchmark["axis_id"] == "useful_composite_paths"
    assert benchmark["ok"] is True
    assert benchmark["evidence_status"] == "measured_local"
    assert benchmark["measurement_scope"] == "local_offline_axiom_library_composition_graph"
    assert benchmark["summary"]["useful_composite_paths_total"] > 0
    assert benchmark["summary"]["useful_composite_paths_by_depth"]["2"] > 0
    assert benchmark["top_useful_paths"]
    assert benchmark["source"]["axiom_scan_summary"]["files_loaded"] == 3
    assert benchmark["source"]["axiom_scan_summary"]["files_skipped"] == 0

    assert benchmark["claim_gate_satisfied"] is False
    assert benchmark["claim_safe"] is False
    assert benchmark["literal_future_claim_safe"] is False
    assert benchmark["unbounded_claims_rejected"] is True
    assert benchmark["required_runtime_evidence_present"] is False
    assert benchmark["controls_present"] is False
    assert benchmark["runtime_authority_changed"] is False
    assert benchmark["runtime_authority_granted"] is False
    assert benchmark["operator_gate_required"] is False
    assert benchmark["external_writes_applied"] is False

    json.dumps(benchmark, allow_nan=False)


def test_composite_path_benchmark_fails_closed_when_no_paths(tmp_path: Path) -> None:
    tool = _load_tool()
    axioms = tmp_path / "empty_axioms"
    axioms.mkdir()

    benchmark = tool.build_composite_path_benchmark(axioms)

    assert benchmark["ok"] is True
    assert benchmark["evidence_status"] == "blocked_no_useful_composite_paths"
    assert benchmark["measured_value_present"] is False
    assert benchmark["source"]["axiom_scan_summary"]["files_scanned"] == 0
    assert benchmark["summary"]["useful_composite_paths_total"] == 0
    assert benchmark["claim_gate_satisfied"] is False
    assert "needs production corpus binding before runtime scalability claims" in benchmark["blockers"]


def test_composite_path_benchmark_is_deterministic_and_hides_external_paths(
    tmp_path: Path,
) -> None:
    tool = _load_tool()
    axioms = _seed_composite_axioms(tmp_path)
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    tool.write_benchmark(first, tool.build_composite_path_benchmark(axioms))
    tool.write_benchmark(second, tool.build_composite_path_benchmark(axioms))

    assert first.read_text(encoding="utf-8") == second.read_text(encoding="utf-8")
    payload = json.loads(first.read_text(encoding="utf-8"))
    assert payload["source"]["axioms_dir"] == "<external_axioms_dir>"
    assert str(tmp_path) not in first.read_text(encoding="utf-8")


def test_composite_path_benchmark_rejects_non_finite_thresholds() -> None:
    tool = _load_tool()

    with pytest.raises(ValueError, match="min_bridge_score must be finite"):
        tool.build_composite_path_benchmark(
            ROOT / "configs" / "axioms",
            min_bridge_score=float("inf"),
        )


def test_composite_path_benchmark_counts_skipped_axioms_without_path_leak(
    tmp_path: Path,
) -> None:
    tool = _load_tool()
    axioms = _seed_composite_axioms(tmp_path)
    (axioms / "bad" / "broken.yaml").parent.mkdir(parents=True)
    (axioms / "bad" / "broken.yaml").write_text("model_id: [", encoding="utf-8")
    (axioms / "bad" / "missing_model.yaml").write_text(
        "cell_id: thermal\n",
        encoding="utf-8",
    )

    benchmark = tool.build_composite_path_benchmark(axioms)
    scan = benchmark["source"]["axiom_scan_summary"]

    assert scan["files_scanned"] == 5
    assert scan["files_loaded"] == 3
    assert scan["files_skipped"] == 2
    assert scan["skip_reasons"] == {
        "missing_model_id": 1,
        "yaml_parse_error": 1,
    }
    payload = json.dumps(benchmark, sort_keys=True)
    assert "broken.yaml" not in payload
    assert "missing_model.yaml" not in payload
    assert str(tmp_path) not in payload


def test_composite_path_benchmark_rejects_invalid_depth(tmp_path: Path) -> None:
    tool = _load_tool()

    with pytest.raises(ValueError, match="max_depth must be >= 2"):
        tool.build_composite_path_benchmark(tmp_path, max_depth=1)
