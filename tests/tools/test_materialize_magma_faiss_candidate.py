from __future__ import annotations

import errno
import hashlib
import json
import shutil
import struct
from pathlib import Path

import numpy as np
import pytest

import tools.materialize_magma_faiss_candidate as candidate
import tools.vector_indexer as vector_indexer
from waggledance.core.magma import vector_projection


def _topology() -> dict:
    return vector_projection.validate_retrieval_topology_contract(
        {
            "schema_version": vector_projection.RETRIEVAL_TOPOLOGY_VERSION,
            "cells": [
                {
                    "cell_id": "energy",
                    "parent_cell_id": None,
                    "child_cell_ids": [],
                    "neighbor_cell_ids": ["thermal"],
                    "live": True,
                    "subdivision_state": "leaf",
                },
                {
                    "cell_id": "thermal",
                    "parent_cell_id": None,
                    "child_cell_ids": [],
                    "neighbor_cell_ids": ["energy"],
                    "live": True,
                    "subdivision_state": "leaf",
                },
            ],
        }
    )


def _embedding_contract() -> dict:
    return vector_projection.build_embedding_contract(
        model_id="test-embed:latest",
        model_version="ollama-catalog-sha256:" + "a" * 64,
        dimension=4,
        normalization="l2-v1",
        document_prefix="search_document: ",
        query_prefix="search_query: ",
    )


def _stage_projection(
    vector_root: Path,
    *,
    cell_id: str,
    solver_id: str,
    topology: dict,
    embedding: dict,
) -> str:
    topology_digest = vector_projection.retrieval_topology_digest(topology)
    source_digest = "sha256:" + hashlib.sha256(
        f"{cell_id}:{solver_id}".encode("utf-8")
    ).hexdigest()
    document = vector_projection.build_solver_contract_projection(
        {
            "model_id": solver_id,
            "model_name": solver_id.replace("_", " ").title(),
            "description": f"Deterministic capability for {cell_id}.",
            "cell_id": cell_id,
            "variables": {"input": {"unit": "1"}},
            "solver_output_schema": {"primary_value": {"name": "value", "unit": "1"}},
        },
        source_digest=source_digest,
        topology_digest=topology_digest,
    )
    identity = vector_projection.build_projection_source_identity(document)
    dedup_key = vector_indexer._projected_upsert_dedup_key(
        identity, embedding, topology_digest
    )
    fingerprint = vector_indexer._projected_upsert_fingerprint(
        document, embedding, topology_digest
    )
    state = vector_indexer.CellState(
        cell_id=cell_id,
        signatures={solver_id: document["solver_contract_digest"]},
        projection_mode=True,
        projection_documents={solver_id: document},
        projection_source_identities={solver_id: identity},
        projection_embedding_contracts={solver_id: embedding},
        projection_topology_digests={solver_id: topology_digest},
        seen_source_identities={dedup_key: fingerprint},
        embedding_contract=embedding,
        topology_digest=topology_digest,
    )
    return vector_indexer._stage_projection_commit(state, vector_root)["commit_id"]


def _stage_empty_projection(
    vector_root: Path,
    *,
    cell_id: str,
    topology: dict,
    embedding: dict,
) -> str:
    topology_digest = vector_projection.retrieval_topology_digest(topology)
    state = vector_indexer.CellState(
        cell_id=cell_id,
        projection_mode=True,
        embedding_contract=embedding,
        topology_digest=topology_digest,
    )
    return vector_indexer._stage_projection_commit(state, vector_root)["commit_id"]


def _request_fixture(
    tmp_path: Path,
    *,
    duplicate_solver: bool = False,
) -> tuple[candidate.CandidateRequest, Path]:
    topology = _topology()
    embedding = _embedding_contract()
    vector_root = tmp_path / "data" / "vector"
    cells = []
    for cell_id, solver_id in (
        ("energy", "energy_solver"),
        ("thermal", "energy_solver" if duplicate_solver else "thermal_solver"),
    ):
        commit_id = _stage_projection(
            vector_root,
            cell_id=cell_id,
            solver_id=solver_id,
            topology=topology,
            embedding=embedding,
        )
        cells.append({"cell_id": cell_id, "projection_commit_id": commit_id})
    request_path = tmp_path / "candidate-request.json"
    request_path.write_text(
        json.dumps(
            {
                "schema_version": candidate.REQUEST_SCHEMA,
                "vector_root": "data/vector",
                "embedding_contract": embedding,
                "topology_contract": topology,
                "cells": cells,
            }
        ),
        encoding="utf-8",
    )
    return candidate.load_candidate_request(request_path, repo_root=tmp_path), request_path


class _FakeIndexFlatIP:
    def __init__(self, dimension: int) -> None:
        self.d = dimension
        self.metric_type = _FakeFaiss.METRIC_INNER_PRODUCT
        self.vectors = np.empty((0, dimension), dtype=np.float32)

    @property
    def ntotal(self) -> int:
        return len(self.vectors)

    def add(self, values: np.ndarray) -> None:
        matrix = np.ascontiguousarray(values, dtype=np.float32)
        if matrix.ndim != 2 or matrix.shape[1] != self.d:
            raise ValueError("fake index dimension mismatch")
        self.vectors = np.concatenate([self.vectors, matrix], axis=0)

    def search(self, queries: np.ndarray, count: int) -> tuple[np.ndarray, np.ndarray]:
        scores = np.asarray(queries @ self.vectors.T, dtype=np.float32)
        orders = np.argsort(-scores, axis=1, kind="stable")[:, :count]
        ranked = np.take_along_axis(scores, orders, axis=1)
        return ranked, orders.astype(np.int64)

    def reconstruct_n(self, start: int, count: int) -> np.ndarray:
        return self.vectors[start : start + count].copy()


class _FakeFaiss:
    __version__ = "test-faiss-1"
    _candidate_binary_set_sha256 = "sha256:" + "b" * 64
    METRIC_INNER_PRODUCT = 0
    IndexFlatIP = _FakeIndexFlatIP

    @staticmethod
    def get_compile_options() -> str:
        return "TEST_ONLY"

    @staticmethod
    def write_index(index: _FakeIndexFlatIP, path: str) -> None:
        payload = struct.pack("<II", index.d, index.ntotal) + index.vectors.astype(
            "<f4", copy=False
        ).tobytes(order="C")
        Path(path).write_bytes(payload)

    @staticmethod
    def read_index(path: str) -> _FakeIndexFlatIP:
        payload = Path(path).read_bytes()
        if len(payload) < 8:
            raise ValueError("truncated fake index")
        dimension, count = struct.unpack("<II", payload[:8])
        values = np.frombuffer(payload[8:], dtype="<f4")
        if len(values) != dimension * count:
            raise ValueError("invalid fake index size")
        index = _FakeIndexFlatIP(dimension)
        if count:
            index.add(values.reshape(count, dimension))
        return index


class _ReverseTieIndex(_FakeIndexFlatIP):
    def search(self, queries: np.ndarray, count: int) -> tuple[np.ndarray, np.ndarray]:
        scores, indices = super().search(queries, count)
        return scores[:, ::-1].copy(), indices[:, ::-1].copy()


class _FailingWriteFaiss(_FakeFaiss):
    @staticmethod
    def write_index(_index: _FakeIndexFlatIP, _path: str) -> None:
        raise RuntimeError("native_write_failed")


class _OtherIndex:
    def __init__(self, wrapped: _FakeIndexFlatIP) -> None:
        self._wrapped = wrapped
        self.d = wrapped.d
        self.ntotal = wrapped.ntotal
        self.metric_type = wrapped.metric_type

    def search(self, queries: np.ndarray, count: int) -> tuple[np.ndarray, np.ndarray]:
        return self._wrapped.search(queries, count)

    def reconstruct_n(self, start: int, count: int) -> np.ndarray:
        return self._wrapped.reconstruct_n(start, count)


class _WrongReaderFaiss(_FakeFaiss):
    @staticmethod
    def read_index(path: str) -> _OtherIndex:
        return _OtherIndex(_FakeFaiss.read_index(path))


class _FakeEmbedder:
    def __init__(self) -> None:
        self.inputs: list[str] = []
        self.verify_calls = 0

    def verify_profile(self, profile: object) -> dict[str, str]:
        self.verify_calls += 1
        return {
            "provider": "fake",
            "requested_model_tag": getattr(profile, "model_id"),
            "catalog_digest": getattr(profile, "model_digest"),
        }

    def embed(self, texts: list[str], profile: object, *, label: str) -> np.ndarray:
        assert label == "candidate_document_embeddings"
        self.inputs.extend(texts)
        dimension = getattr(profile, "dimension")
        rows = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            rows.append([float(digest[index] + 1) for index in range(dimension)])
        return np.asarray(rows, dtype=np.float32)


def test_default_embedder_uses_benchmark_ollama_base_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request, _request_path = _request_fixture(tmp_path)
    output_root = candidate.resolve_candidate_root(
        ".codex-audit/default-embedder", repo_root=tmp_path
    )
    observed_urls: list[str] = []

    class _ContextEmbedder(_FakeEmbedder):
        def __init__(self, base_url: str) -> None:
            super().__init__()
            observed_urls.append(base_url)

        def __enter__(self) -> "_ContextEmbedder":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(
        candidate.retrieval_benchmark,
        "OllamaEmbeddingClient",
        _ContextEmbedder,
    )

    report = candidate.materialize_candidate(
        request,
        output_root=output_root,
        faiss_module=_FakeFaiss,
    )

    assert report["status"] == "materialized"
    assert observed_urls == [candidate.retrieval_benchmark.DEFAULT_OLLAMA_URL]


def test_materializes_verified_global_candidate_and_is_idempotent(tmp_path: Path) -> None:
    request, _request_path = _request_fixture(tmp_path)
    output_root = candidate.resolve_candidate_root(
        ".codex-audit/magma-faiss-candidates", repo_root=tmp_path
    )
    embedder = _FakeEmbedder()

    first = candidate.materialize_candidate(
        request,
        output_root=output_root,
        embedder=embedder,
        faiss_module=_FakeFaiss,
    )
    second = candidate.materialize_candidate(
        request,
        output_root=output_root,
        embedder=_FakeEmbedder(),
        faiss_module=_FakeFaiss,
    )

    assert first["status"] == "materialized"
    assert second["status"] == "already_exists"
    assert second["snapshot_id"] == first["snapshot_id"]
    assert embedder.verify_calls == 2
    assert len(embedder.inputs) == 2
    assert all(text.startswith("search_document: ") for text in embedder.inputs)
    manifest = first["manifest"]
    assert manifest["total_vector_count"] == 2
    assert manifest["unreceipted_count"] == 2
    assert manifest["runtime_authority_ready"] is False
    assert manifest["cell_local_routing_evaluated"] is False
    assert manifest["chromosome_count"] is None
    assert manifest["gene_bank_ready"] is False
    assert (
        manifest["embedding_provider_identity"][
            "catalog_contract_verified_before_embedding"
        ]
        is True
    )
    assert (
        manifest["embedding_provider_identity"][
            "catalog_contract_verified_after_embedding"
        ]
        is True
    )
    assert manifest["persisted_parity"]["exact_rankings_match"] is True
    assert not (tmp_path / "data" / "faiss").exists()
    assert not (tmp_path / "data" / "faiss_staging").exists()


def test_valid_empty_live_cell_is_preserved(tmp_path: Path) -> None:
    topology = _topology()
    embedding = _embedding_contract()
    vector_root = tmp_path / "data" / "vector"
    energy_commit = _stage_projection(
        vector_root,
        cell_id="energy",
        solver_id="energy_solver",
        topology=topology,
        embedding=embedding,
    )
    thermal_commit = _stage_empty_projection(
        vector_root,
        cell_id="thermal",
        topology=topology,
        embedding=embedding,
    )
    request_path = tmp_path / "empty-cell-request.json"
    request_path.write_text(
        json.dumps(
            {
                "schema_version": candidate.REQUEST_SCHEMA,
                "vector_root": "data/vector",
                "embedding_contract": embedding,
                "topology_contract": topology,
                "cells": [
                    {"cell_id": "energy", "projection_commit_id": energy_commit},
                    {"cell_id": "thermal", "projection_commit_id": thermal_commit},
                ],
            }
        ),
        encoding="utf-8",
    )
    request = candidate.load_candidate_request(request_path, repo_root=tmp_path)
    output_root = candidate.resolve_candidate_root(
        ".codex-audit/empty-cell", repo_root=tmp_path
    )

    report = candidate.materialize_candidate(
        request,
        output_root=output_root,
        embedder=_FakeEmbedder(),
        faiss_module=_FakeFaiss,
    )

    assert report["manifest"]["total_vector_count"] == 1
    assert [(row["cell_id"], row["vector_count"]) for row in report["manifest"]["cells"]] == [
        ("energy", 1),
        ("thermal", 0),
    ]


def test_unavailable_faiss_reports_no_candidate_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request, _request_path = _request_fixture(tmp_path)
    output_root = tmp_path / ".codex-audit" / "unavailable"

    def unavailable() -> object:
        raise candidate.CandidateUnavailable("faiss_not_installed")

    monkeypatch.setattr(candidate, "_import_faiss", unavailable)

    with pytest.raises(candidate.CandidateUnavailable, match="faiss_not_installed"):
        candidate.materialize_candidate(
            request, output_root=output_root, embedder=_FakeEmbedder()
        )

    assert not output_root.exists()


def test_broken_faiss_import_is_not_misreported_as_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_import(_name: str) -> object:
        raise ModuleNotFoundError("missing transitive module", name="faiss.loader")

    monkeypatch.setattr(candidate.importlib, "import_module", broken_import)

    with pytest.raises(ModuleNotFoundError) as caught:
        candidate._import_faiss()

    assert caught.value.name == "faiss.loader"


def test_unavailable_embedding_backend_reports_no_candidate_write(tmp_path: Path) -> None:
    request, _request_path = _request_fixture(tmp_path)
    output_root = tmp_path / ".codex-audit" / "embedding-unavailable"

    class _UnavailableEmbedder:
        def verify_profile(self, _profile: object) -> dict:
            raise candidate.retrieval_benchmark.BenchmarkUnavailable(
                "embedding_backend_unavailable"
            )

    with pytest.raises(
        candidate.CandidateUnavailable, match="embedding_backend_unavailable"
    ):
        candidate.materialize_candidate(
            request,
            output_root=output_root,
            embedder=_UnavailableEmbedder(),
            faiss_module=_FakeFaiss,
        )

    assert not output_root.exists()


def test_preexisting_snapshots_link_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_root = tmp_path / ".codex-audit" / "linked-snapshots"
    snapshots_root = output_root / "snapshots"
    snapshots_root.mkdir(parents=True)
    real_is_link_like = candidate._is_link_like

    monkeypatch.setattr(
        candidate,
        "_is_link_like",
        lambda path: path == snapshots_root or real_is_link_like(path),
    )

    with pytest.raises(candidate.CandidateContractError, match="must not be a link"):
        candidate._prepare_snapshots_root(output_root, tmp_path / ".codex-audit")


def test_programmatic_api_rejects_nested_runtime_audit_name(tmp_path: Path) -> None:
    request, _request_path = _request_fixture(tmp_path)
    disguised_runtime_root = tmp_path / "data" / "faiss" / ".codex-audit" / "candidate"

    with pytest.raises(
        candidate.CandidateContractError, match="repository .codex-audit"
    ):
        candidate.materialize_candidate(
            request,
            output_root=disguised_runtime_root,
            embedder=_FakeEmbedder(),
            faiss_module=_FakeFaiss,
        )

    assert not disguised_runtime_root.exists()


def test_duplicate_solver_across_cells_fails_before_output(tmp_path: Path) -> None:
    request, _request_path = _request_fixture(tmp_path, duplicate_solver=True)
    output_root = tmp_path / ".codex-audit" / "duplicate"

    with pytest.raises(candidate.CandidateContractError, match="more than one candidate cell"):
        candidate.materialize_candidate(
            request,
            output_root=output_root,
            embedder=_FakeEmbedder(),
            faiss_module=_FakeFaiss,
        )

    assert not output_root.exists()


def test_persisted_vector_tamper_fails_closed(tmp_path: Path) -> None:
    request, _request_path = _request_fixture(tmp_path)
    output_root = candidate.resolve_candidate_root(
        ".codex-audit/tamper", repo_root=tmp_path
    )
    report = candidate.materialize_candidate(
        request,
        output_root=output_root,
        embedder=_FakeEmbedder(),
        faiss_module=_FakeFaiss,
    )
    snapshot = Path(report["snapshot_path"])
    vector_path = snapshot / "cells" / "energy" / "vectors.f32"
    payload = bytearray(vector_path.read_bytes())
    payload[0] ^= 1
    vector_path.write_bytes(payload)

    with pytest.raises(candidate.CandidateContractError, match="checksum mismatch"):
        candidate.load_verified_candidate_snapshot(snapshot, faiss_module=_FakeFaiss)


def test_persisted_catalog_verification_claim_fails_closed(tmp_path: Path) -> None:
    request, _request_path = _request_fixture(tmp_path)
    output_root = candidate.resolve_candidate_root(
        ".codex-audit/catalog-claim", repo_root=tmp_path
    )
    report = candidate.materialize_candidate(
        request,
        output_root=output_root,
        embedder=_FakeEmbedder(),
        faiss_module=_FakeFaiss,
    )
    manifest_path = Path(report["snapshot_path"]) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["embedding_provider_identity"][
        "catalog_contract_verified_after_embedding"
    ] = False
    new_snapshot_id = candidate.SNAPSHOT_PREFIX + candidate.sha256_digest(
        candidate._snapshot_identity(manifest)
    ).split(":", 1)[1]
    manifest["snapshot_id"] = new_snapshot_id
    manifest_path.write_bytes(candidate._canonical_json_line(manifest))
    rewritten_snapshot = manifest_path.parent.with_name(new_snapshot_id)
    manifest_path.parent.rename(rewritten_snapshot)

    with pytest.raises(
        candidate.CandidateContractError,
        match="embedding provider identity is invalid",
    ):
        candidate.load_verified_candidate_snapshot(
            rewritten_snapshot, faiss_module=_FakeFaiss
        )


def test_deserialized_index_must_actually_be_index_flat_ip(tmp_path: Path) -> None:
    request, _request_path = _request_fixture(tmp_path)
    output_root = candidate.resolve_candidate_root(
        ".codex-audit/index-type", repo_root=tmp_path
    )
    report = candidate.materialize_candidate(
        request,
        output_root=output_root,
        embedder=_FakeEmbedder(),
        faiss_module=_FakeFaiss,
    )

    with pytest.raises(candidate.CandidateContractError, match="not IndexFlatIP"):
        candidate.load_verified_candidate_snapshot(
            report["snapshot_path"], faiss_module=_WrongReaderFaiss
        )


def test_native_faiss_write_failure_is_structured_and_leaves_no_stage(
    tmp_path: Path,
) -> None:
    request, _request_path = _request_fixture(tmp_path)
    output_root = candidate.resolve_candidate_root(
        ".codex-audit/native-write-failure", repo_root=tmp_path
    )

    with pytest.raises(candidate.CandidateContractError, match="index write failed"):
        candidate.materialize_candidate(
            request,
            output_root=output_root,
            embedder=_FakeEmbedder(),
            faiss_module=_FailingWriteFaiss,
        )

    snapshots_root = output_root / "snapshots"
    assert snapshots_root.is_dir()
    assert list(snapshots_root.iterdir()) == []


def test_cell_dimension_must_match_top_level_embedding_contract(tmp_path: Path) -> None:
    request, _request_path = _request_fixture(tmp_path)
    output_root = candidate.resolve_candidate_root(
        ".codex-audit/dimension-binding", repo_root=tmp_path
    )
    report = candidate.materialize_candidate(
        request,
        output_root=output_root,
        embedder=_FakeEmbedder(),
        faiss_module=_FakeFaiss,
    )
    cell_dir = Path(report["snapshot_path"]) / "cells" / "energy"

    with pytest.raises(
        candidate.CandidateContractError, match="does not match embedding contract"
    ):
        candidate._load_cell_candidate(
            cell_dir,
            expected_cell_id="energy",
            expected_embedding_digest=request.embedding_contract["contract_digest"],
            expected_dimension=request.embedding_contract["dimension"] - 1,
            expected_topology_digest=request.topology_digest,
            faiss_module=_FakeFaiss,
        )


def test_output_root_must_remain_beneath_audit_directory(tmp_path: Path) -> None:
    with pytest.raises(candidate.CandidateContractError, match="beneath .codex-audit"):
        candidate.resolve_candidate_root("candidate-output", repo_root=tmp_path)


def test_equal_scores_merge_by_solver_id() -> None:
    first = _FakeIndexFlatIP(2)
    first.add(np.asarray([[1.0, 0.0]], dtype=np.float32))
    second = _FakeIndexFlatIP(2)
    second.add(np.asarray([[1.0, 0.0]], dtype=np.float32))
    loaded = [
        {
            "rows": [{"canonical_solver_id": "zeta"}],
            "index": first,
        },
        {
            "rows": [{"canonical_solver_id": "alpha"}],
            "index": second,
        },
    ]

    order, scores = candidate._search_all_cells(
        loaded, np.asarray([1.0, 0.0], dtype=np.float32)
    )

    assert order == ["alpha", "zeta"]
    assert scores == {"zeta": 1.0, "alpha": 1.0}


def test_verified_top_k_search_matches_global_numpy_and_reverifies_sources(
    tmp_path: Path,
) -> None:
    request, _request_path = _request_fixture(tmp_path)
    output_root = candidate.resolve_candidate_root(
        ".codex-audit/search", repo_root=tmp_path
    )
    report = candidate.materialize_candidate(
        request,
        output_root=output_root,
        embedder=_FakeEmbedder(),
        faiss_module=_FakeFaiss,
    )
    verified = candidate.load_verified_candidate_snapshot(
        report["snapshot_path"],
        faiss_module=_FakeFaiss,
        expected_request=request,
    )
    query = np.asarray([1.0, 2.0, 3.0, 4.0], dtype=np.float32)

    results = candidate.search_verified_candidate(verified, query, k=2)
    normalized_query = query / np.linalg.norm(query)
    expected = []
    for cell in verified["cells"]:
        for row, vector in zip(cell["rows"], cell["vectors"]):
            expected.append(
                (float(vector @ normalized_query), row["canonical_solver_id"])
            )
    expected.sort(key=lambda item: (-item[0], item[1]))

    assert verified["source_commits_reverified"] is True
    assert [row["canonical_solver_id"] for row in results] == [
        solver_id for _score, solver_id in expected
    ]
    assert [row["score"] for row in results] == pytest.approx(
        [score for score, _solver_id in expected], abs=1.0e-6
    )


def test_verified_top_k_expands_cutoff_ties_before_solver_id_merge() -> None:
    index = _ReverseTieIndex(2)
    index.add(np.asarray([[1.0, 0.0], [1.0, 0.0]], dtype=np.float32))
    verified = {
        "manifest": {"embedding_contract": {"dimension": 2}},
        "cells": [
            {
                "manifest": {"cell_id": "energy"},
                "rows": [
                    {
                        "canonical_solver_id": solver_id,
                        "projection_id": "sha256:" + digest * 64,
                        "receipt_bound": False,
                    }
                    for solver_id, digest in (("alpha", "a"), ("zeta", "b"))
                ],
                "index": index,
            }
        ],
        "snapshot_dir": Path("unused"),
        "source_commits_reverified": False,
    }

    results = candidate.search_verified_candidate(
        verified, np.asarray([1.0, 0.0], dtype=np.float32), k=1
    )

    assert [row["canonical_solver_id"] for row in results] == ["alpha"]


def test_expected_request_rejects_changed_projection_source(tmp_path: Path) -> None:
    request, _request_path = _request_fixture(tmp_path)
    output_root = candidate.resolve_candidate_root(
        ".codex-audit/source-reverify", repo_root=tmp_path
    )
    report = candidate.materialize_candidate(
        request,
        output_root=output_root,
        embedder=_FakeEmbedder(),
        faiss_module=_FakeFaiss,
    )
    cell_id, commit_id = request.cells[0]
    source_manifest = (
        request.vector_root / cell_id / "commits" / commit_id / "manifest.json"
    )
    payload = bytearray(source_manifest.read_bytes())
    payload[-2] ^= 1
    source_manifest.write_bytes(payload)

    with pytest.raises((candidate.CandidateContractError, ValueError)):
        candidate.load_verified_candidate_snapshot(
            report["snapshot_path"],
            faiss_module=_FakeFaiss,
            expected_request=request,
        )


@pytest.mark.parametrize(
    "publication_error",
    [
        PermissionError("simulated Windows destination-won race"),
        OSError(errno.ENOTEMPTY, "simulated POSIX destination-won race"),
    ],
)
def test_concurrent_destination_winner_is_verified_and_reused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    publication_error: OSError,
) -> None:
    request, _request_path = _request_fixture(tmp_path)
    output_root = candidate.resolve_candidate_root(
        ".codex-audit/concurrent-winner", repo_root=tmp_path
    )
    real_replace = candidate.os.replace
    publication_attempts = 0

    def destination_wins(source: Path | str, destination: Path | str) -> None:
        nonlocal publication_attempts
        source_path = Path(source)
        destination_path = Path(destination)
        if (
            source_path.name.startswith(".stage-")
            and destination_path.name.startswith(candidate.SNAPSHOT_PREFIX)
        ):
            publication_attempts += 1
            shutil.copytree(source_path, destination_path)
            raise publication_error
        real_replace(source, destination)

    monkeypatch.setattr(candidate.os, "replace", destination_wins)

    report = candidate.materialize_candidate(
        request,
        output_root=output_root,
        embedder=_FakeEmbedder(),
        faiss_module=_FakeFaiss,
    )

    assert publication_attempts == 1
    assert report["status"] == "already_exists"
    assert [path.name for path in (output_root / "snapshots").iterdir()] == [
        report["snapshot_id"]
    ]


def test_real_faiss_round_trip_when_installed(tmp_path: Path) -> None:
    faiss = pytest.importorskip("faiss")
    request, _request_path = _request_fixture(tmp_path)
    output_root = candidate.resolve_candidate_root(
        ".codex-audit/real-faiss", repo_root=tmp_path
    )

    report = candidate.materialize_candidate(
        request,
        output_root=output_root,
        embedder=_FakeEmbedder(),
        faiss_module=faiss,
    )
    repeated = candidate.materialize_candidate(
        request,
        output_root=output_root,
        embedder=_FakeEmbedder(),
        faiss_module=faiss,
    )

    assert report["status"] == "materialized"
    assert repeated["status"] == "already_exists"
    assert repeated["snapshot_id"] == report["snapshot_id"]
    assert report["manifest"]["faiss_version"] == faiss.__version__
    assert report["manifest"]["faiss_binary_set_sha256"].startswith("sha256:")
    assert report["manifest"]["persisted_parity"]["exact_rankings_match"] is True
