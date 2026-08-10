# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import pytest

import tools.backfill_axioms_to_hex as backfill
from waggledance.core.magma import vector_projection


def _axiom():
    return {
        "model_id": "heat_loss",
        "model_name": "Heat Loss",
        "description": "Estimate heat loss.",
        "cell_id": "thermal",
        "variables": {"area": {"unit": "m2", "default": 1}},
        "solver_output_schema": {
            "primary_value": {"name": "loss", "unit": "W"},
        },
        "query": "RAW_QUERY_MUST_NOT_APPEAR",
        "response": "RAW_RESPONSE_MUST_NOT_APPEAR",
    }


def _entries(*dimensions):
    return [
        {
            "canonical_solver_id": "heat_loss",
            "embedding_dim": dimension,
            "vector": [0.1] * min(dimension, 3),
            "source_file": "configs/axioms/cottage/heat_loss.yaml",
        }
        for dimension in dimensions
    ]


def test_backfill_projection_event_is_allowlisted_and_explicitly_unreceipted() -> None:
    event = backfill._build_projection_upsert_event(
        axiom=_axiom(),
        entries=_entries(768, 768),
        topology_contract=vector_projection.build_retrieval_topology_contract(),
        source_digest="sha256:" + "3" * 64,
    )

    payload = event.payload
    public = str(payload)
    assert event.source == "axiom_backfill"
    assert payload["embedding_contract"]["dimension"] == 768
    assert payload["source_identity"]["receipt_bound"] is False
    assert payload["source_identity"]["receipt_event_id"] is None
    assert payload["source_identity"]["receipt_digest"] is None
    assert "RAW_QUERY_MUST_NOT_APPEAR" not in public
    assert "RAW_RESPONSE_MUST_NOT_APPEAR" not in public
    assert "vector" not in payload["projection_document"]
    assert "source_file" not in payload["projection_document"]


def test_backfill_projection_rejects_dimension_mismatch_before_event() -> None:
    with pytest.raises(ValueError, match="consistent positive embedding dimension"):
        backfill._build_projection_upsert_event(
            axiom=_axiom(),
            entries=_entries(768, 384),
            topology_contract=vector_projection.build_retrieval_topology_contract(),
            source_digest="sha256:" + "3" * 64,
        )


def test_backfill_projection_rejects_cell_absent_from_topology() -> None:
    axiom = {**_axiom(), "cell_id": "thermal.child"}
    with pytest.raises(ValueError, match="absent from the retrieval topology"):
        backfill._build_projection_upsert_event(
            axiom=axiom,
            entries=_entries(768),
            topology_contract=vector_projection.build_retrieval_topology_contract(),
            source_digest="sha256:" + "3" * 64,
        )
