# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import copy

import pytest

from waggledance.core.hex_cell_topology import ALL_CELLS
from waggledance.core.magma import vector_projection as projection


SOURCE_DIGEST = "sha256:" + "1" * 64


def _axiom(**overrides):
    value = {
        "model_id": "heat_loss",
        "model_name": "Heat Loss",
        "description": "Estimate heat loss through an insulated hive wall.",
        "cell_id": "thermal",
        "variables": {
            "area": {"unit": "m2", "default": 1, "range": [0, 10]},
            "temperature_delta": {"unit": "K", "default": 20},
        },
        "solver_output_schema": {
            "primary_value": {"name": "heat_loss", "unit": "W"},
            "comparable_fields": [
                {"name": "heat_loss", "unit": "W"},
                {"name": "confidence", "unit": "ratio"},
            ],
        },
        "formulas": [
            {
                "name": "heat_loss",
                "formula": "u_value * area * temperature_delta",
                "output_unit": "W",
            }
        ],
        "capabilities": ["thermal_estimation"],
        "tags": ["cottage", "hive"],
    }
    value.update(overrides)
    return value


def _topology():
    return projection.build_retrieval_topology_contract()


def _document(axiom=None, topology=None):
    topology = topology or _topology()
    return projection.build_solver_contract_projection(
        axiom or _axiom(),
        source_digest=SOURCE_DIGEST,
        topology_digest=projection.retrieval_topology_digest(topology),
    )


def test_projection_is_deterministic_and_only_contains_allowlisted_fields() -> None:
    axiom = _axiom(
        query="RAW_QUERY_PAYLOAD must never be copied",
        response="RAW_RESPONSE_PAYLOAD must never be copied",
        actions=[{"result": "SECRET_RESULT"}],
        metadata={"private": "PRIVATE_METADATA"},
    )
    reordered = dict(reversed(list(axiom.items())))

    first = _document(axiom)
    second = _document(reordered)

    assert first == second
    assert first["projection_digest"].startswith("sha256:")
    assert first["projection_id"].startswith("sha256:")
    assert first["solver_contract_digest"].startswith("sha256:")
    serialized = str(first)
    assert "RAW_QUERY_PAYLOAD" not in serialized
    assert "RAW_RESPONSE_PAYLOAD" not in serialized
    assert "SECRET_RESULT" not in serialized
    assert "PRIVATE_METADATA" not in serialized
    assert "u_value * area" not in serialized
    assert set(first["contract_fields"]) == {
        "model_id",
        "model_name",
        "description",
        "variables",
        "outputs",
        "formulas",
        "capabilities",
        "tags",
    }


def test_allowed_free_text_with_leak_marker_fails_closed_without_redaction() -> None:
    with pytest.raises(ValueError, match="prohibited payload marker"):
        _document(_axiom(description="password=hunter2"))


def test_embedding_affecting_change_changes_contract_and_projection_identity() -> None:
    first = _document()
    changed = _document(_axiom(description="A materially different description."))

    assert first["embedding_text"] != changed["embedding_text"]
    assert first["solver_contract_digest"] != changed["solver_contract_digest"]
    assert first["projection_id"] != changed["projection_id"]
    assert first["projection_digest"] != changed["projection_digest"]


@pytest.mark.parametrize(
    "cell_id",
    ["../thermal", "thermal/child", "thermal\\child", "C:thermal", "thermal..child", ""],
)
def test_vector_cell_id_rejects_path_and_empty_hierarchy_shapes(cell_id: str) -> None:
    with pytest.raises(ValueError, match="cell_id"):
        projection.validate_vector_cell_id(cell_id)


def test_vector_cell_id_accepts_hierarchical_child_identifier() -> None:
    assert projection.validate_vector_cell_id("thermal.heating") == "thermal.heating"


def test_source_identity_is_explicitly_unreceipted_and_content_addressed() -> None:
    document = _document()
    identity = projection.build_projection_source_identity(document)

    assert identity["receipt_event_id"] is None
    assert identity["receipt_digest"] is None
    assert identity["receipt_bound"] is False
    assert identity["identity_digest"].startswith("sha256:")

    with pytest.raises(ValueError, match="all-or-none"):
        projection.build_projection_source_identity(
            document,
            receipt_event_id="receipt-1",
        )


def test_source_identity_v1_rejects_unverified_receipt_claims() -> None:
    document = _document()
    fake_digest = "sha256:" + "0" * 64

    with pytest.raises(ValueError, match="verified receipt evidence"):
        projection.build_projection_source_identity(
            document,
            receipt_event_id="entirely-invented",
            receipt_digest=fake_digest,
        )

    forged = projection.build_projection_source_identity(document)
    forged.update(
        {
            "receipt_event_id": "entirely-invented",
            "receipt_digest": fake_digest,
            "receipt_bound": True,
        }
    )
    forged["identity_digest"] = projection.sha256_digest(
        {key: value for key, value in forged.items() if key != "identity_digest"}
    )
    with pytest.raises(ValueError, match="verified receipt evidence"):
        projection.validate_projection_source_identity(forged)


def test_embedding_contract_binds_exact_model_dimension_normalization_and_prefixes() -> None:
    contract = projection.build_embedding_contract(
        model_id="nomic-embed-text",
        model_version="v1.5",
        dimension=768,
        normalization="l2-v1",
        document_prefix="",
        query_prefix="search_query: ",
    )

    assert contract["dimension"] == 768
    assert contract["query_prefix"] == "search_query: "
    assert contract["contract_digest"].startswith("sha256:")

    tampered = {**contract, "dimension": 384}
    with pytest.raises(ValueError, match="digest mismatch"):
        projection.validate_embedding_contract(tampered)
    with pytest.raises(ValueError, match="positive integer"):
        projection.build_embedding_contract(
            model_id="m", model_version="v", dimension=True,
        )


def test_current_retrieval_topology_is_deterministic_and_binds_solver_cells() -> None:
    first = _topology()
    second = _topology()

    assert first == second
    assert {row["cell_id"] for row in first["cells"]} == set(ALL_CELLS)
    assert projection.retrieval_topology_digest(first) == projection.retrieval_topology_digest(second)


def test_topology_rejects_dangling_and_nonreciprocal_neighbors() -> None:
    dangling = copy.deepcopy(_topology())
    dangling["cells"][0]["neighbor_cell_ids"].append("missing")
    with pytest.raises(ValueError, match="dangling"):
        projection.validate_retrieval_topology_contract(dangling)

    nonreciprocal = copy.deepcopy(_topology())
    first = nonreciprocal["cells"][0]
    neighbor = first["neighbor_cell_ids"][0]
    neighbor_row = next(row for row in nonreciprocal["cells"] if row["cell_id"] == neighbor)
    neighbor_row["neighbor_cell_ids"].remove(first["cell_id"])
    with pytest.raises(ValueError, match="not reciprocal"):
        projection.validate_retrieval_topology_contract(nonreciprocal)


def test_rebalanced_partition_requires_exactly_once_and_current_topology() -> None:
    topology = _topology()
    document = _document(topology=topology)

    report = projection.validate_rebalanced_projection_partition(
        [document["projection_id"]],
        {"thermal": [document]},
        topology,
    )
    assert report["before_count"] == report["after_count"] == 1
    assert report["duplicates"] == report["missing"] == report["orphaned"] == 0

    with pytest.raises(ValueError, match="duplicates"):
        projection.validate_rebalanced_projection_partition(
            [document["projection_id"]],
            {"thermal": [document, document]},
            topology,
        )

    stale_topology = copy.deepcopy(topology)
    stale_topology["cells"][0]["live"] = False
    stale_document = _document(topology=stale_topology)
    with pytest.raises(ValueError, match="stale topology"):
        projection.validate_rebalanced_projection_partition(
            [stale_document["projection_id"]],
            {"thermal": [stale_document]},
            topology,
        )
