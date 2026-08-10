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


def _embedding():
    return projection.build_embedding_contract(
        model_id="nomic-embed-text:latest",
        model_version="ollama-catalog-sha256:" + "a" * 64,
        dimension=768,
        normalization="l2-v1",
        document_prefix="search_document: ",
        query_prefix="search_query: ",
    )


def _receipt_proof(document=None, embedding=None):
    document = document or _document()
    embedding = embedding or _embedding()
    return projection.build_self_certified_projection_receipt_proof(
        document,
        embedding,
        ts_utc="2026-08-10T06:00:00Z",
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


def test_receipt_bound_source_identity_v2_is_fully_reverifiable() -> None:
    document = _document()
    embedding = _embedding()
    proof = _receipt_proof(document, embedding)

    identity = projection.build_receipt_bound_projection_source_identity(
        document, embedding, proof
    )
    validated = projection.validate_projection_source_identity(identity)

    assert validated["schema_version"] == projection.PROJECTION_SOURCE_IDENTITY_V2_VERSION
    assert validated["receipt_bound"] is True
    assert validated["receipt_event_id"] == proof["receipt"]["event_id"]
    assert validated["receipt_digest"] == projection.sha256_digest(proof["receipt"])
    assert validated["receipt_proof_digest"] == proof["proof_digest"]
    assert validated["projection_digest"] == document["projection_digest"]
    assert validated["embedding_contract_digest"] == embedding["contract_digest"]
    assert validated["receipt_authenticity_verified"] is False
    assert validated["external_authority_artifacts_verified"] is False
    assert validated["solver_outcome_verified"] is False
    assert validated["runtime_authority_granted"] is False
    assert (
        validated["receipt_proof"]["payload"]["runtime_authority_granted"]
        is False
    )
    assert (
        validated["receipt_proof"]["payload"][
            "admission_evaluator_contract_digest"
        ]
        == projection.PROJECTION_ADMISSION_EVALUATOR_DIGEST
    )
    assert (
        validated["receipt_proof"]["payload"][
            "external_authority_artifacts_verified"
        ]
        is False
    )
    assert validated["receipt_proof"]["payload"]["solver_outcome_verified"] is False
    context = validated["receipt_proof"]["receipt_context"]
    assert context["rco_decision_artifact"] == {
        "schema_version": "magma.faiss.local_rco_status.v1",
        "review_performed": False,
        "decision": "not_evaluated",
        "authority_granted": False,
    }
    assert validated["receipt_digest"] == projection.sha256_digest(
        validated["receipt_proof"]["receipt"]
    )
    assert {
        key: validated["receipt_proof"]["receipt"][key]
        for key in (
            "policy_digest",
            "charter_digest",
            "rco_decision_digest",
            "world_snapshot_digest",
        )
    } == projection.projection_receipt_context_digests(context)
    assert projection.validate_projection_source_binding(
        document, validated, embedding
    ) == validated


def test_projection_receipt_proof_rejects_payload_and_receipt_tampering() -> None:
    document = _document()
    embedding = _embedding()
    proof = _receipt_proof(document, embedding)

    poisoned_payload = copy.deepcopy(proof)
    poisoned_payload["payload"]["cell_id"] = "energy"
    poisoned_payload["proof_digest"] = projection.sha256_digest(
        {
            key: value
            for key, value in poisoned_payload.items()
            if key != "proof_digest"
        }
    )
    with pytest.raises(ValueError, match="does not match projection contracts"):
        projection.validate_projection_receipt_proof(
            poisoned_payload, document, embedding
        )

    poisoned_receipt = copy.deepcopy(proof)
    poisoned_receipt["receipt"]["evaluation_result_digest"] = "sha256:" + "9" * 64
    poisoned_receipt["proof_digest"] = projection.sha256_digest(
        {
            key: value
            for key, value in poisoned_receipt.items()
            if key != "proof_digest"
        }
    )
    with pytest.raises(ValueError, match="evaluation digest mismatch"):
        projection.validate_projection_receipt_proof(
            poisoned_receipt, document, embedding
        )

    fake_signature = copy.deepcopy(proof)
    fake_signature["receipt"].update(
        {
            "signature_algorithm": "Ed25519",
            "signature": "base64url:aaaaaaaaaaaaaaaa",
            "key_id": "key:unverified-test-key",
        }
    )
    fake_signature["proof_digest"] = projection.sha256_digest(
        {key: value for key, value in fake_signature.items() if key != "proof_digest"}
    )
    with pytest.raises(ValueError, match="cannot claim an unverified"):
        projection.validate_projection_receipt_proof(
            fake_signature, document, embedding
        )

    invented_rco_approval = copy.deepcopy(proof)
    invented_rco_approval["receipt_context"]["rco_decision_artifact"].update(
        {
            "review_performed": True,
            "decision": "allow",
            "authority_granted": True,
        }
    )
    invented_rco_approval["proof_digest"] = projection.sha256_digest(
        {
            key: value
            for key, value in invented_rco_approval.items()
            if key != "proof_digest"
        }
    )
    with pytest.raises(ValueError, match="explicitly non-authoritative"):
        projection.validate_projection_receipt_proof(
            invented_rco_approval, document, embedding
        )


def test_source_identity_v2_rejects_forged_summaries_and_cross_contracts() -> None:
    document = _document()
    embedding = _embedding()
    identity = projection.build_receipt_bound_projection_source_identity(
        document, embedding, _receipt_proof(document, embedding)
    )
    forged = copy.deepcopy(identity)
    forged["receipt_digest"] = "sha256:" + "0" * 64
    forged["identity_digest"] = projection.sha256_digest(
        {key: value for key, value in forged.items() if key != "identity_digest"}
    )
    with pytest.raises(ValueError, match="receipt digest mismatch"):
        projection.validate_projection_source_identity(forged)

    false_authenticity = copy.deepcopy(identity)
    false_authenticity["receipt_authenticity_verified"] = True
    false_authenticity["identity_digest"] = projection.sha256_digest(
        {
            key: value
            for key, value in false_authenticity.items()
            if key != "identity_digest"
        }
    )
    with pytest.raises(ValueError, match="cannot grant authority or authenticity"):
        projection.validate_projection_source_identity(false_authenticity)

    other_embedding = projection.build_embedding_contract(
        model_id=embedding["model_id"],
        model_version=embedding["model_version"],
        dimension=embedding["dimension"],
        normalization=embedding["normalization"],
        document_prefix=embedding["document_prefix"],
        query_prefix="different-query-prefix: ",
    )
    assert projection.projection_admission_event_id(
        document, embedding
    ) != projection.projection_admission_event_id(document, other_embedding)
    with pytest.raises(ValueError, match="does not match projection contracts"):
        projection.validate_projection_source_binding(
            document, identity, other_embedding
        )


def test_source_identity_versions_are_strict_and_receipts_cannot_replay() -> None:
    document = _document()
    embedding = _embedding()
    proof = _receipt_proof(document, embedding)
    identity_v2 = projection.build_receipt_bound_projection_source_identity(
        document, embedding, proof
    )

    mislabeled_v2 = copy.deepcopy(identity_v2)
    mislabeled_v2["schema_version"] = projection.PROJECTION_SOURCE_IDENTITY_V1_VERSION
    with pytest.raises(ValueError, match="keys mismatch"):
        projection.validate_projection_source_identity(mislabeled_v2)

    identity_v1 = projection.build_projection_source_identity(document)
    identity_v1["schema_version"] = projection.PROJECTION_SOURCE_IDENTITY_V2_VERSION
    with pytest.raises(ValueError, match="keys mismatch"):
        projection.validate_projection_source_identity(identity_v1)

    other_document = _document(
        _axiom(
            model_id="heat_loss_revision",
            description="A different solver contract and projection.",
        )
    )
    with pytest.raises(ValueError, match="does not match projection contracts"):
        projection.validate_projection_receipt_proof(
            proof, other_document, embedding
        )


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
