# SPDX-License-Identifier: BUSL-1.1
"""Validate the v3.13.0 dry-run runbook manifest examples against the
SCH-005 SolverCandidateManifest schema.

If the schema and the published runbook examples drift apart, this
test fails -- catching documentation rot at CI time before operators
copy-paste a broken manifest.

Examples mirror the canonical text in:
* docs/runbooks/v3_13_0/home_dry_run.md
* docs/runbooks/v3_13_0/cottage_dry_run.md
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = (
    ROOT / "schemas" / "v3_13_0" / "solver_candidate_manifest.schema.json"
)
HOME_RUNBOOK = ROOT / "docs" / "runbooks" / "v3_13_0" / "home_dry_run.md"
COTTAGE_RUNBOOK = (
    ROOT / "docs" / "runbooks" / "v3_13_0" / "cottage_dry_run.md"
)


def _validator() -> jsonschema.Draft7Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return jsonschema.Draft7Validator(schema)


def _home_manifest() -> dict:
    """Mirror of docs/runbooks/v3_13_0/home_dry_run.md Step 3 example.

    Keep field-by-field in sync with the runbook. The matching test
    below also asserts every value here appears verbatim in the
    runbook so the two cannot silently diverge."""
    return {
        "schema_version": 1,
        "candidate_id": "electricity_spot_optimizer_home_demo_001",
        "source_docs": [
            "doc:tariff_structure_pdf",
            "doc:consumption_sample_csv",
        ],
        "source_tools": [],
        "training_contracts": [
            "ctr_date", "ctr_search", "ctr_vector",
            "ctr_memory", "ctr_cross_ref",
        ],
        "state_handles": [
            "state:spot_price_store",
            "state:consumption_forecast",
            "state:optimizer_recommendations",
        ],
        "connector_handles": ["conn:spot_price_public_feed"],
        "shadow_inputs": ["synth_24h_winter", "synth_24h_summer"],
        "shadow_expected_outputs": [
            "recommendation_with_savings_estimate_winter",
            "recommendation_with_savings_estimate_summer",
        ],
        "divergence_score": None,
        "accepted_differences": [],
        "rejected_differences": [],
        "promotion_decision": "awaiting_shadow",
        "rollback_plan": "recovery:spot_optimizer_v1",
        "operator_review_id": "op_review_001",
        "provenance_signatures": [],
        "activation_state": "unactivated",
    }


def _cottage_manifest() -> dict:
    """Mirror of docs/runbooks/v3_13_0/cottage_dry_run.md Step 3 example."""
    return {
        "schema_version": 1,
        "candidate_id": "frost_risk_predictor_cottage_demo_001",
        "source_docs": [
            "doc:thermal_model_yaml",
            "doc:sensor_history_csv",
        ],
        "source_tools": [],
        "training_contracts": [
            "ctr_date", "ctr_vector", "ctr_memory",
        ],
        "state_handles": [
            "state:weather_forecast_cache",
            "state:sensor_history",
            "state:frost_risk_predictions",
        ],
        "connector_handles": ["conn:weather_forecast_public"],
        "shadow_inputs": [
            "synth_cold_snap_24h",
            "synth_thaw_24h",
            "synth_steady_freeze_72h",
        ],
        "shadow_expected_outputs": [
            "high_risk_alert_within_6h",
            "no_risk_within_24h",
            "medium_risk_within_72h",
        ],
        "divergence_score": None,
        "accepted_differences": [],
        "rejected_differences": [],
        "promotion_decision": "awaiting_shadow",
        "rollback_plan": "recovery:frost_predictor_v1",
        "operator_review_id": "op_review_002",
        "provenance_signatures": [],
        "activation_state": "unactivated",
    }


# --------------------------------------------------------------------------
# Schema-validation tests
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "factory",
    [_home_manifest, _cottage_manifest],
    ids=["home", "cottage"],
)
def test_runbook_manifest_validates_against_sch_005(factory) -> None:
    """Each runbook's SolverCandidateManifest example must validate
    against the current SCH-005 schema. Catches schema drift."""
    errors = list(_validator().iter_errors(factory()))
    assert errors == [], (
        f"runbook manifest example does not validate: "
        f"{[(e.message, list(e.path)) for e in errors]}"
    )


# --------------------------------------------------------------------------
# Doc-vs-fixture-consistency: every ref_id used in the fixtures must
# appear verbatim in the corresponding runbook markdown. Catches the
# case where the test stays valid but the doc drifts.
# --------------------------------------------------------------------------


def _ref_ids_from_manifest(manifest: dict) -> list[str]:
    """Collect every leaf ref_id from a manifest (candidate_id +
    state_handles + connector_handles + rollback_plan etc.)."""
    ids: list[str] = [
        manifest["candidate_id"],
        manifest["rollback_plan"],
        manifest["operator_review_id"],
    ]
    for key in (
        "source_docs", "training_contracts", "state_handles",
        "connector_handles", "shadow_inputs", "shadow_expected_outputs",
    ):
        ids.extend(manifest.get(key, []))
    return ids


@pytest.mark.parametrize(
    "factory,runbook_path",
    [
        (_home_manifest, HOME_RUNBOOK),
        (_cottage_manifest, COTTAGE_RUNBOOK),
    ],
    ids=["home", "cottage"],
)
def test_runbook_fixture_ids_appear_in_runbook_markdown(
    factory, runbook_path
) -> None:
    """Every ref_id in the manifest fixture must appear in the runbook
    markdown so the doc-test consistency holds."""
    runbook = runbook_path.read_text(encoding="utf-8")
    missing = [rid for rid in _ref_ids_from_manifest(factory())
                if rid not in runbook]
    assert missing == [], (
        f"runbook example fixture references ids not present in "
        f"{runbook_path.name}: {missing}"
    )


def test_runbook_files_exist_in_docs_runbooks() -> None:
    """Sanity: the runbook docs were published to docs/runbooks/v3_13_0/."""
    assert HOME_RUNBOOK.exists(), HOME_RUNBOOK
    assert COTTAGE_RUNBOOK.exists(), COTTAGE_RUNBOOK


def test_runbook_files_are_ascii_only() -> None:
    """Codex coordinator constraint: committed runbook docs are
    ASCII-only to avoid PowerShell 5.1 / cross-shell encoding issues."""
    for path in (HOME_RUNBOOK, COTTAGE_RUNBOOK):
        content = path.read_text(encoding="utf-8")
        non_ascii = [(i + 1, c) for i, c in enumerate(content)
                     if ord(c) > 127]
        assert non_ascii == [], (
            f"{path.name} contains non-ASCII characters "
            f"(first 5): {non_ascii[:5]}"
        )


# --------------------------------------------------------------------------
# Codex RCO round-2: forbid copy-pasteable commands for entry points
# that do not exist as CLIs in v3.13.0. Silent no-op risk if an operator
# literally copies the runbook command line.
# --------------------------------------------------------------------------


_FORBIDDEN_COMMAND_PATTERNS = (
    "python -m waggledance.core.v3_13_0.shadow_runner",
    "python -m waggledance.core.v3_13_0.write_rco_gate",
    "python -m waggledance.core.v3_13_0.divergence_analyzer",
    "python -m waggledance.core.v3_13_0.behavior_capture",
    "python -m waggledance.core.v3_13_0.solver_provenance",
    "python -m waggledance.core.v3_13_0.auto_fix_loop",
    "python -m waggledance.core.v3_13_0.credential_vault",
)


@pytest.mark.parametrize(
    "runbook_path",
    [HOME_RUNBOOK, COTTAGE_RUNBOOK],
    ids=["home", "cottage"],
)
def test_runbook_does_not_publish_non_existent_cli_commands(
    runbook_path,
) -> None:
    """v3.13.0 ships no CLI / __main__ for waggledance.core.v3_13_0.*
    modules. Documentation that tells an operator to invoke them via
    'python -m ...' would silently no-op. Until a real CLI lands, the
    runbooks must use API-level pseudocode instead.
    """
    content = runbook_path.read_text(encoding="utf-8")
    hits = [pat for pat in _FORBIDDEN_COMMAND_PATTERNS if pat in content]
    assert hits == [], (
        f"{runbook_path.name} contains command lines for entry points "
        f"that have no CLI in v3.13.0: {hits}. Use API-level "
        f"pseudocode (ShadowRunner(...).run(...)) until a real CLI "
        f"ships in a separate PR."
    )
