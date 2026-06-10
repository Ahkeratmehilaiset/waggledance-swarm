# SPDX-License-Identifier: Apache-2.0
"""Tests for the read-only hex-mesh canary mirror core (storyboard slice 1)."""
from __future__ import annotations

import pytest

from waggledance.core.hex_topology.canary_mirror import (
    CANARY_CLASSIFICATIONS,
    CANARY_DIVERGENT_KEYWORD_OVERRIDE,
    CANARY_DIVERGENT_PRODUCTION_CELL,
    CANARY_MATCH_INTENT_CELL,
    CANARY_MATCH_PRODUCTION_CELL,
    CANARY_MIRROR_REPORT_SCHEMA,
    CANARY_ROUTE_COMPARISON_SCHEMA,
    CanaryMirrorError,
    build_canary_route_comparison,
    classify_canary_route,
    summarize_canary_mirror,
)
from waggledance.core.magma.canonical import sha256_digest


def _comparison(**overrides):
    kwargs = {
        "query": "calculate the heating formula",
        "intent": "math",
        "production_capability_id": "cap.math.formula",
        "quality_path": "silver",
    }
    kwargs.update(overrides)
    return build_canary_route_comparison(**kwargs)


def test_comparison_is_privacy_safe_and_deterministic():
    secret = "operator-secret heating question about pakkanen"
    first = _comparison(query=secret, intent="thermal",
                        production_capability_id="cap.thermal.frost")
    second = _comparison(query=secret, intent="thermal",
                         production_capability_id="cap.thermal.frost")
    assert first == second  # fresh topology per call -> deterministic
    rendered = repr(first)
    assert secret not in rendered
    assert "operator-secret" not in rendered
    assert first["query_digest"] == sha256_digest({"query": secret})
    assert first["query_length"] == len(secret)
    # read-only contract flags are literal constants on every record
    assert first["no_runtime_mutation"] is True
    assert first["runtime_authority_granted"] is False
    assert first["routing_influence_applied"] is False
    assert first["production_decision_unchanged"] is True


def test_match_and_divergence_against_production_cell():
    match = _comparison(intent="math", production_cell_id="math")
    assert match["schema_version"] == CANARY_ROUTE_COMPARISON_SCHEMA
    assert match["mesh_cell_id"] == "math"
    assert match["classification"] == CANARY_MATCH_PRODUCTION_CELL
    assert match["agreement"] is True

    divergent = _comparison(intent="math", production_cell_id="general")
    assert divergent["classification"] == CANARY_DIVERGENT_PRODUCTION_CELL
    assert divergent["agreement"] is False
    # the production decision is recorded, never altered
    assert divergent["production_cell_id"] == "general"


def test_intent_cell_match_without_production_cell():
    record = _comparison(
        query="hello there, how are you", intent="chat",
        production_capability_id="cap.chat.general",
    )
    assert record["mesh_cell_id"] == record["intent_cell_id"] == "general"
    assert record["classification"] == CANARY_MATCH_INTENT_CELL
    assert record["agreement"] is True


def test_keyword_override_divergence_is_flagged():
    # intent classifier said "chat" (-> general) but the query is saturated
    # with thermal keywords -> the mesh keyword fallback routes to thermal.
    record = _comparison(
        query="kova pakkanen ja matala lämpötila, heating tarvitaan",
        intent="chat",
        production_capability_id="cap.chat.general",
    )
    assert record["intent_cell_id"] == "general"
    assert record["mesh_cell_id"] == "thermal"
    assert record["mesh_method"] == "keyword"
    assert record["classification"] == CANARY_DIVERGENT_KEYWORD_OVERRIDE
    assert record["agreement"] is False


def test_unknown_intent_is_handled_consistently():
    record = _comparison(query="something nondescript", intent="zzz_unknown")
    # both the mesh arm and the intent-only arm fall through identically
    assert record["mesh_cell_id"] == record["intent_cell_id"]
    assert record["classification"] == CANARY_MATCH_INTENT_CELL


def test_classify_canary_route_is_pure_and_rederivable():
    assert classify_canary_route(
        mesh_cell_id="thermal", intent_cell_id="general",
        production_cell_id="thermal",
    ) == CANARY_MATCH_PRODUCTION_CELL
    assert classify_canary_route(
        mesh_cell_id="thermal", intent_cell_id="thermal",
        production_cell_id=None,
    ) == CANARY_MATCH_INTENT_CELL


def test_input_validation_fails_closed():
    with pytest.raises(CanaryMirrorError, match="query"):
        _comparison(query=123)
    with pytest.raises(CanaryMirrorError, match="intent"):
        _comparison(intent="   ")
    with pytest.raises(CanaryMirrorError, match="production_capability_id"):
        _comparison(production_capability_id="")
    with pytest.raises(CanaryMirrorError, match="production_cell_id"):
        _comparison(production_cell_id="  ")


def test_summary_aggregates_and_digest_rederives():
    records = [
        _comparison(intent="math", production_cell_id="math"),
        _comparison(intent="math", production_cell_id="general"),
        _comparison(query="hello", intent="chat",
                    production_capability_id="cap.chat.general"),
        _comparison(
            query="kova pakkanen ja lämpötila", intent="chat",
            production_capability_id="cap.chat.general",
        ),
    ]
    report = summarize_canary_mirror(records)
    assert report["schema_version"] == CANARY_MIRROR_REPORT_SCHEMA
    assert report["measurement_scope"] == "shadow_mirror_read_only"
    assert report["sample_count"] == 4
    assert report["agreement_count"] == 2
    assert report["divergence_count"] == 2
    assert report["agreement_rate"] == 0.5
    # every classification key is always present (closed set)
    assert set(report["by_classification"]) == set(CANARY_CLASSIFICATIONS)
    assert report["by_classification"][CANARY_MATCH_PRODUCTION_CELL] == 1
    assert report["by_classification"][CANARY_DIVERGENT_PRODUCTION_CELL] == 1
    assert report["by_classification"][CANARY_MATCH_INTENT_CELL] == 1
    assert report["by_classification"][CANARY_DIVERGENT_KEYWORD_OVERRIDE] == 1
    assert report["by_mesh_cell"]["math"] == 2
    assert report["by_mesh_cell"]["thermal"] == 1
    # verdict is re-derivable: digest binds every core field
    core = {k: v for k, v in report.items() if k != "canonical_digest"}
    assert report["canonical_digest"] == sha256_digest(core)
    # report-level read-only contract
    assert report["no_runtime_mutation"] is True
    assert report["runtime_authority_granted"] is False
    assert report["routing_influence_applied"] is False


def test_summary_fails_closed_on_malformed_records():
    good = _comparison()
    with pytest.raises(CanaryMirrorError, match="mapping"):
        summarize_canary_mirror([good, "not-a-mapping"])
    with pytest.raises(CanaryMirrorError, match="schema_version"):
        summarize_canary_mirror([{**good, "schema_version": "evil.v9"}])
    with pytest.raises(CanaryMirrorError, match="classification"):
        summarize_canary_mirror([{**good, "classification": "forged"}])
    # authority drift refuses — including the string-"False" type confusion
    with pytest.raises(CanaryMirrorError, match="runtime_authority_granted"):
        summarize_canary_mirror(
            [{**good, "runtime_authority_granted": True}]
        )
    with pytest.raises(CanaryMirrorError, match="no_runtime_mutation"):
        summarize_canary_mirror([{**good, "no_runtime_mutation": "True"}])
    with pytest.raises(CanaryMirrorError, match="mesh_cell_id"):
        summarize_canary_mirror([{**good, "mesh_cell_id": ""}])
    with pytest.raises(CanaryMirrorError, match="sequence"):
        summarize_canary_mirror("not-a-list")


def test_summary_empty_batch_is_explicit_zero():
    report = summarize_canary_mirror([])
    assert report["sample_count"] == 0
    assert report["agreement_count"] == 0
    assert report["divergence_count"] == 0
    assert report["agreement_rate"] == 0.0
    assert set(report["by_classification"]) == set(CANARY_CLASSIFICATIONS)
    assert report["by_mesh_cell"] == {}
    assert report["canonical_digest"]
