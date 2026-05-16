# SPDX-License-Identifier: BUSL-1.1
"""Tests for the v3.13.0 first-slice solver registry."""
from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path

import pytest

from waggledance.core.v3_13_0.solver_registry import (
    DEFAULT_REGISTRY_PATH,
    SCHEMA_VERSION,
    SolverManifest,
    SolverRegistryError,
    get_solver_manifest,
    load_solver_registry,
    resolve_solver_entrypoint,
)


EXPECTED_CASE_IDS = {
    "ACCT-01__unpaid_bill_reconciler__home",
    "AIR-01__indoor_air_quality_advisor__cottage",
    "EMAIL-01__inbox_priority_classifier__home",
    "EMAIL-02__vendor_email_indexer__home",
    "ENG-01__spot_electricity_monitor__home",
    "ENG-06__cottage_fireplace_advisor__cottage",
    "FIN-10__cottage_bookkeeping_separator__cottage",
    "PDF-01__invoice_field_extractor__home",
}


def test_default_solver_registry_loads_current_first_slice_solvers() -> None:
    solvers = load_solver_registry()

    assert len(solvers) == 8
    assert {solver.case_id for solver in solvers} == EXPECTED_CASE_IDS
    assert all(isinstance(solver, SolverManifest) for solver in solvers)
    assert all(solver.risk_class == "informational" for solver in solvers)
    assert all(solver.write_intent == "none" for solver in solvers)


def test_get_solver_manifest_returns_one_immutable_entry() -> None:
    solver = get_solver_manifest("AIR-01__indoor_air_quality_advisor__cottage")

    assert solver.name == "AIR-01"
    assert solver.marker_class == "informational_with_severity"
    assert "AIR_QUALITY_EMERGENCY" in solver.result_markers
    assert solver.knowledge_refs == (
        "knowledge/air_quality/core.yaml#DECISION_METRICS_AND_THRESHOLDS.pm25_ug",
        "knowledge/air_quality/core.yaml#DECISION_METRICS_AND_THRESHOLDS.co_ppm_indoor",
        "knowledge/air_quality/core.yaml#DECISION_METRICS_AND_THRESHOLDS.radon_bq",
    )
    assert solver.to_mapping()["knowledge_refs"] == list(solver.knowledge_refs)


def test_declared_solver_entrypoints_and_optional_modules_are_importable() -> None:
    for solver in load_solver_registry():
        assert callable(resolve_solver_entrypoint(solver))
        assert hasattr(importlib.import_module(solver.module), solver.result_class)
        for module_name in (
            solver.case_id_source_module,
            solver.advisory_card_module,
            solver.cli_module,
            *solver.adapter_modules,
            *solver.transport_modules,
        ):
            if module_name is not None:
                importlib.import_module(module_name)


def test_registry_case_ids_match_declared_source_modules() -> None:
    for solver in load_solver_registry():
        source_module = solver.case_id_source_module or solver.module
        module = importlib.import_module(source_module)
        if hasattr(module, "CASE_ID"):
            assert module.CASE_ID == solver.case_id


def test_registry_entries_have_unique_case_ids_and_entrypoints() -> None:
    solvers = load_solver_registry()

    assert len({solver.case_id for solver in solvers}) == len(solvers)
    assert len({
        (solver.module, solver.entry_function)
        for solver in solvers
    }) == len(solvers)


def test_default_manifest_has_no_absolute_paths_or_secret_markers() -> None:
    raw = DEFAULT_REGISTRY_PATH.read_text(encoding="utf-8")

    assert "C:\\" not in raw
    assert "U:\\" not in raw
    assert "password" not in raw.casefold()
    assert "token" not in raw.casefold()


def test_registry_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    manifest = _default_registry_mapping()
    manifest["solvers"][1]["case_id"] = manifest["solvers"][0]["case_id"]
    path = _write_registry(tmp_path, manifest)

    with pytest.raises(SolverRegistryError, match="duplicate case_id"):
        load_solver_registry(path)


def test_registry_rejects_duplicate_entrypoints(tmp_path: Path) -> None:
    manifest = _default_registry_mapping()
    manifest["solvers"][1]["module"] = manifest["solvers"][0]["module"]
    manifest["solvers"][1]["entry_function"] = (
        manifest["solvers"][0]["entry_function"]
    )
    path = _write_registry(tmp_path, manifest)

    with pytest.raises(SolverRegistryError, match="duplicate solver entrypoint"):
        load_solver_registry(path)


def test_registry_rejects_unsafe_manifest_strings(tmp_path: Path) -> None:
    manifest = _default_registry_mapping()
    manifest["solvers"][0]["knowledge_refs"] = ["C:\\operator\\secrets.json"]
    path = _write_registry(tmp_path, manifest)

    with pytest.raises(SolverRegistryError, match="unsafe path|secret-like"):
        load_solver_registry(path)


def test_registry_rejects_unknown_schema_version(tmp_path: Path) -> None:
    manifest = _default_registry_mapping()
    manifest["schema_version"] = SCHEMA_VERSION + 1
    path = _write_registry(tmp_path, manifest)

    with pytest.raises(SolverRegistryError, match="schema_version"):
        load_solver_registry(path)


def test_unknown_case_id_fails_closed() -> None:
    with pytest.raises(SolverRegistryError, match="unknown solver case_id"):
        get_solver_manifest("case:unknown_solver")


def _default_registry_mapping() -> dict:
    return copy.deepcopy(json.loads(DEFAULT_REGISTRY_PATH.read_text(
        encoding="utf-8",
    )))


def _write_registry(tmp_path: Path, manifest: dict) -> Path:
    path = tmp_path / "solver_registry.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path
