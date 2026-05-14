# SPDX-License-Identifier: BUSL-1.1
"""Tests for SolverSynthesizer v1 release pipeline glue."""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from waggledance.core.v3_13_0.doc_ingest import build_doc_ingest_proposal
from waggledance.core.v3_13_0.solver_synthesizer import (
    OperatorCaseSeed,
    SolverSynthesizerError,
    SolverTarget,
    build_capability_graph,
    rank_solver_backlog,
    synthesize_from_doc_ingest_proposal,
)
from waggledance.core.v3_13_0.write_rco_gate import WriteRiskClass


ROOT = Path(__file__).resolve().parents[2]
SCH_005 = ROOT / "schemas" / "v3_13_0" / "solver_candidate_manifest.schema.json"
OPERATOR_CASE_BUNDLE = (
    ROOT / "tests" / "fixtures" / "v3_13_0" /
    "operator_case_seed_bundle.json"
)


def _validate_manifest(manifest: dict) -> None:
    schema = json.loads(SCH_005.read_text(encoding="utf-8"))
    errors = list(jsonschema.Draft7Validator(schema).iter_errors(manifest))
    assert errors == [], [(error.message, list(error.path)) for error in errors]


def _write_home_input(root: Path) -> None:
    (root / "profile_config.yaml").write_text(
        "\n".join([
            "schema_version: 1",
            "profile_id: home_release",
            "profile_kind: home",
            "country: FI",
        ]),
        encoding="utf-8",
    )
    (root / "tariff_structure.md").write_text(
        "night tariff window 22:00-06:00\n",
        encoding="utf-8",
    )
    (root / "consumption_sample.csv").write_text(
        "ts,kwh\n2026-05-13T00:00:00Z,1.25\n",
        encoding="utf-8",
    )


def test_doc_ingest_proposal_synthesizes_schema_valid_candidate(
    tmp_path: Path,
) -> None:
    _write_home_input(tmp_path)
    proposal = build_doc_ingest_proposal(
        tmp_path,
        profile_kind="home",
        candidate_id="electricity_spot_optimizer_home_release_001",
    )

    target = SolverTarget(
        target_domain="DOM-007",
        target_write_risk=WriteRiskClass.LOCAL_ARTIFACT.value,
        target_state_ref="state:spot_price_store_release",
        tool_descriptor_id="tool:electricity_optimizer_release",
        required_capabilities=(
            "cap:bulk_sync",
            "cap:price_window_optimization",
            "cap:operator_explanation",
        ),
        failure_modes=(
            "stale_price_feed",
            "missing_consumption_sample",
        ),
        shadow_expected_outputs=(
            "shadow:charge_window_recommendation",
            "shadow:stale_price_refusal",
        ),
    )

    draft = synthesize_from_doc_ingest_proposal(proposal, target)

    _validate_manifest(draft.manifest)
    assert len(draft.manifest_sha256) == 64
    assert draft.candidate_record.candidate_id == \
        "electricity_spot_optimizer_home_release_001"
    assert draft.candidate_record.target_domain == "DOM-007"
    assert draft.candidate_record.target_write_risk == "local_artifact"
    assert "state:spot_price_store_release" in draft.manifest["state_handles"]
    assert draft.manifest["activation_state"] == "unactivated"
    assert draft.manifest["promotion_decision"] == "awaiting_shadow"

    payload = draft.to_bridge_payload()
    assert payload["kind"] == "solver"
    assert payload["event_type"] == "solver_candidate_manifest_synthesized"
    assert payload["required_capabilities"] == [
        "cap:bulk_sync",
        "cap:price_window_optimization",
        "cap:operator_explanation",
    ]

    intent = draft.construct_intent(
        agent_id="codex",
        session_id="sess_release_pipeline",
        payload={"recommendation": "charge_at_02:00"},
    )
    assert intent.target_state_ref == "state:spot_price_store_release"
    assert intent.provenance_chain == draft.manifest_sha256
    assert intent.payload["candidate_id"] == draft.candidate_record.candidate_id


def test_external_effect_target_requires_explicit_connector_ref() -> None:
    with pytest.raises(SolverSynthesizerError, match="connector_ref"):
        SolverTarget(
            target_domain="DOM-021",
            target_write_risk=WriteRiskClass.EXTERNAL_EFFECT.value,
            target_state_ref="state:factory_logbook",
            tool_descriptor_id="tool:pdam_logbook_reconciler",
            action="post",
        )


def test_operator_case_seeds_build_shared_capability_graph_and_backlog() -> None:
    seeds = [
        OperatorCaseSeed.from_mapping({
            "case_id": "case:factory_logbook_reconcile",
            "profiles": ["factory"],
            "source_refs": ["doc:pdam_logbook_handoff"],
            "connector_handles": ["conn:pdam_logbook", "conn:pdam_mes"],
            "required_capabilities": [
                "cap:bulk_sync",
                "cap:state_reconciliation",
                "cap:operator_explanation",
            ],
            "failure_modes": ["stale_cache", "conflicting_state"],
            "decision_kind": "close_or_explain",
            "expected_output": "candidate close comment or refusal",
            "risk_class": "external_effect",
            "first_solver_slice": "read_only_close_recommendation",
            "shadow_expected_output": "shadow:pdam_reconcile_recommendation",
        }),
        OperatorCaseSeed.from_mapping({
            "case_id": "case:home_charge_window",
            "profiles": ["home", "cottage"],
            "source_refs": ["doc:tariff_structure", "doc:consumption_sample"],
            "connector_handles": ["conn:spot_price_public_feed"],
            "required_capabilities": [
                "cap:bulk_sync",
                "cap:price_window_optimization",
                "cap:operator_explanation",
            ],
            "failure_modes": ["stale_price_feed", "missing_consumption_sample"],
            "decision_kind": "recommendation",
            "expected_output": "cheapest safe charge window",
            "risk_class": "local_artifact",
            "first_solver_slice": "read_only_recommendation",
            "shadow_expected_output": "shadow:charge_window_recommendation",
        }),
        OperatorCaseSeed.from_mapping({
            "case_id": "case:cottage_frost_risk",
            "profiles": ["cottage"],
            "source_refs": ["doc:thermal_model", "doc:sensor_history"],
            "connector_handles": ["conn:weather_forecast_public"],
            "required_capabilities": [
                "cap:bulk_sync",
                "cap:risk_forecast",
                "cap:operator_explanation",
            ],
            "failure_modes": ["stale_weather", "sensor_gap"],
            "decision_kind": "alert",
            "expected_output": "frost risk alert or no-risk explanation",
            "risk_class": "local_artifact",
            "first_solver_slice": "read_only_alert",
            "shadow_expected_output": "shadow:frost_risk_alert",
        }),
    ]

    graph = build_capability_graph(seeds)

    explanation = graph["cap:operator_explanation"]
    assert explanation.case_ids == (
        "case:cottage_frost_risk",
        "case:factory_logbook_reconcile",
        "case:home_charge_window",
    )
    assert explanation.profiles == ("cottage", "factory", "home")
    assert "conflicting_state" in explanation.failure_modes
    assert "stale_price_feed" in explanation.failure_modes

    backlog = rank_solver_backlog(seeds)
    assert backlog[0].case_id == "case:home_charge_window"
    assert backlog[0].first_solver_slice == "read_only_recommendation"
    assert backlog[-1].risk_class == "external_effect"


def test_operator_case_seed_bundle_fixture_builds_release_backlog() -> None:
    bundle = json.loads(OPERATOR_CASE_BUNDLE.read_text(encoding="utf-8"))
    seeds = [OperatorCaseSeed.from_mapping(case) for case in bundle["cases"]]

    assert len(seeds) == 18
    assert {profile for seed in seeds for profile in seed.profiles} == {
        "cottage",
        "factory",
        "home",
    }
    assert {seed.risk_class for seed in seeds} == {
        WriteRiskClass.EXTERNAL_EFFECT.value,
        WriteRiskClass.INFORMATIONAL.value,
        WriteRiskClass.LOCAL_ARTIFACT.value,
    }

    eng_01 = next(
        seed for seed in seeds
        if seed.case_id == "ENG-01__spot_electricity_monitor__home"
    )
    assert eng_01.risk_class == WriteRiskClass.INFORMATIONAL.value
    assert "doc:operator__helen_password_reset" in eng_01.source_refs
    assert eng_01.first_solver_slice == (
        "fetch_next_24h_spot_prices_and_return_top_3_cheapest_hours"
    )

    graph = build_capability_graph(seeds)
    assert "browser_session_persistent" in graph
    assert "ENG-01__spot_electricity_monitor__home" in \
        graph["browser_session_persistent"].case_ids

    backlog = rank_solver_backlog(seeds)
    assert len(backlog) == 18
    assert backlog[0].priority_score >= backlog[-1].priority_score


def test_operator_case_seed_refuses_secret_like_source_refs() -> None:
    with pytest.raises(SolverSynthesizerError, match="secret-like source_ref"):
        OperatorCaseSeed.from_mapping({
            "case_id": "case:bad_secret_source",
            "profiles": ["home"],
            "source_refs": ["file:credentials.json"],
            "connector_handles": ["conn:spot_price_public_feed"],
            "required_capabilities": ["cap:bulk_sync"],
            "failure_modes": ["stale_price_feed"],
            "decision_kind": "recommendation",
            "expected_output": "refused",
            "risk_class": "local_artifact",
            "first_solver_slice": "read_only_recommendation",
            "shadow_expected_output": "shadow:refusal",
        })


def test_operator_case_seed_refuses_scalar_list_fields() -> None:
    with pytest.raises(SolverSynthesizerError, match="profiles"):
        OperatorCaseSeed.from_mapping({
            "case_id": "case:bad_profiles_scalar",
            "profiles": "home",
            "source_refs": ["doc:tariff_structure"],
            "connector_handles": ["conn:spot_price_public_feed"],
            "required_capabilities": ["cap:bulk_sync"],
            "failure_modes": ["stale_price_feed"],
            "decision_kind": "recommendation",
            "expected_output": "refused",
            "risk_class": "local_artifact",
            "first_solver_slice": "read_only_recommendation",
            "shadow_expected_output": "shadow:refusal",
        })
