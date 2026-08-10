# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import math

import pytest

import tools.backfill_axioms_to_hex as backfill
import tools.benchmark_magma_solver_retrieval as retrieval_benchmark
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


def test_backfill_nomic_profile_matches_retrieval_evidence_profile() -> None:
    profile = retrieval_benchmark.EMBEDDING_PROFILES["nomic"]

    assert backfill.EMBEDDING_MODEL == profile.model_id
    assert backfill.EMBEDDING_MODEL_DIGEST == profile.model_digest
    assert backfill.EMBEDDING_DIMENSION == profile.dimension
    assert backfill.EMBEDDING_DOCUMENT_PREFIX == profile.document_prefix
    assert backfill.EMBEDDING_QUERY_PREFIX == profile.query_prefix


def test_ledger_metadata_carries_complete_embedding_contract() -> None:
    metadata = backfill._embedding_ledger_metadata(
        {
            "catalog_contract_verified_before_embedding": True,
            "catalog_contract_verified_after_embedding": True,
        }
    )
    contract = backfill._build_pinned_embedding_contract()

    assert metadata["embedding_model"] == contract["model_id"]
    assert metadata["embedding_model_version"] == contract["model_version"]
    assert metadata["embedding_normalization"] == contract["normalization"]
    assert metadata["embedding_document_prefix"] == contract["document_prefix"]
    assert metadata["embedding_query_prefix"] == contract["query_prefix"]
    assert metadata["embedding_dim"] == contract["dimension"]
    assert metadata["embedding_contract_digest"] == contract["contract_digest"]
    assert metadata["embedding_catalog_contract_verified_before_embedding"] is True
    assert metadata["embedding_catalog_contract_verified_after_embedding"] is True
    assert metadata["embedding_response_digest_attested"] is False


def test_ledger_metadata_rejects_unverified_catalog_claim() -> None:
    with pytest.raises(backfill.EmbeddingContractError, match="catalog evidence"):
        backfill._embedding_ledger_metadata(
            {
                "catalog_contract_verified_before_embedding": True,
                "catalog_contract_verified_after_embedding": False,
            }
        )


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
    assert payload["embedding_contract"]["model_id"] == "nomic-embed-text:latest"
    assert payload["embedding_contract"]["model_version"] == (
        "ollama-catalog-sha256:" + backfill.EMBEDDING_MODEL_DIGEST
    )
    assert payload["embedding_contract"]["document_prefix"] == "search_document: "
    assert payload["embedding_contract"]["query_prefix"] == "search_query: "
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


def test_backfill_projection_rejects_dimension_from_another_model() -> None:
    with pytest.raises(ValueError, match="does not match pinned model"):
        backfill._build_projection_upsert_event(
            axiom=_axiom(),
            entries=_entries(384),
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


class _Response:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class _EmbeddingClient:
    def __init__(
        self,
        *,
        response_model: str = backfill.EMBEDDING_MODEL,
        catalog_digests: list[str] | None = None,
    ) -> None:
        self.response_model = response_model
        self.catalog_digests = list(
            catalog_digests
            or [backfill.EMBEDDING_MODEL_DIGEST, backfill.EMBEDDING_MODEL_DIGEST]
        )
        self.requests: list[dict] = []

    def __enter__(self) -> "_EmbeddingClient":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def get(self, url: str) -> _Response:
        assert url == backfill.OLLAMA_TAGS_URL
        digest = self.catalog_digests.pop(0)
        return _Response(
            {
                "models": [
                    {
                        "name": backfill.EMBEDDING_MODEL,
                        "model": backfill.EMBEDDING_MODEL,
                        "digest": digest,
                    }
                ]
            }
        )

    def post(self, url: str, *, json: dict) -> _Response:
        assert url == backfill.OLLAMA_URL
        self.requests.append(json)
        vector = [3.0, 4.0] + [0.0] * (backfill.EMBEDDING_DIMENSION - 2)
        return _Response(
            {
                "model": self.response_model,
                "embeddings": [vector for _text in json["input"]],
            }
        )


def test_embed_texts_applies_document_contract_and_normalizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _EmbeddingClient()
    monkeypatch.setattr(backfill.httpx, "Client", lambda **_kwargs: client)

    vectors = backfill.embed_texts(["  solver document  "])

    assert client.requests == [
        {
            "model": backfill.EMBEDDING_MODEL,
            "input": ["search_document: solver document"],
            "keep_alive": "30m",
            "truncate": False,
        }
    ]
    assert len(vectors) == 1
    assert len(vectors[0]) == backfill.EMBEDDING_DIMENSION
    assert vectors[0][:2] == pytest.approx([0.6, 0.8])
    assert math.sqrt(math.fsum(value * value for value in vectors[0])) == pytest.approx(1.0)


def test_embed_texts_rejects_response_model_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _EmbeddingClient(response_model="another-model:latest")
    monkeypatch.setattr(backfill.httpx, "Client", lambda **_kwargs: client)

    with pytest.raises(backfill.EmbeddingContractError, match="response model mismatch"):
        backfill.embed_texts(["solver document"])


def test_embed_texts_rejects_catalog_change_during_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _EmbeddingClient(
        catalog_digests=[backfill.EMBEDDING_MODEL_DIGEST, "f" * 64]
    )
    monkeypatch.setattr(backfill.httpx, "Client", lambda **_kwargs: client)

    with pytest.raises(backfill.EmbeddingContractError, match="digest mismatch"):
        backfill.embed_texts(["solver document"])


def test_catalog_rejects_model_alias_with_conflicting_name() -> None:
    class _AmbiguousCatalogClient:
        def get(self, _url: str) -> _Response:
            return _Response(
                {
                    "models": [
                        {
                            "name": "conflicting-name:latest",
                            "model": backfill.EMBEDDING_MODEL,
                            "digest": backfill.EMBEDDING_MODEL_DIGEST,
                        }
                    ]
                }
            )

    with pytest.raises(backfill.EmbeddingContractError, match="absent from catalog"):
        backfill._verify_embedding_model_catalog(_AmbiguousCatalogClient())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("rows", "reason"),
    [
        ([], "row count"),
        ([[0.0] * (backfill.EMBEDDING_DIMENSION - 1)], "dimension"),
        ([[0.0] * backfill.EMBEDDING_DIMENSION], "zero or invalid norm"),
        (
            [[1.0e-13] + [0.0] * (backfill.EMBEDDING_DIMENSION - 1)],
            "zero or invalid norm",
        ),
        ([[float("nan")] + [0.0] * (backfill.EMBEDDING_DIMENSION - 1)], "non-finite"),
        ([[True] + [0.0] * (backfill.EMBEDDING_DIMENSION - 1)], "non-numeric"),
    ],
)
def test_embedding_rows_fail_closed(rows: object, reason: str) -> None:
    with pytest.raises(backfill.EmbeddingContractError, match=reason):
        backfill._normalize_embedding_rows(rows, expected_rows=1)
