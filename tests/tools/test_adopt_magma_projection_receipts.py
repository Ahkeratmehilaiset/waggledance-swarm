from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools import adopt_magma_projection_receipts as adoption
from tools import materialize_magma_faiss_candidate as candidate
from tools import vector_indexer
from waggledance.core.magma import vector_events, vector_projection


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


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


def _embedding(model_digest_char: str = "a") -> dict:
    return vector_projection.build_embedding_contract(
        model_id="test-embed:latest",
        model_version=(
            "ollama-catalog-sha256:" + model_digest_char * 64
        ),
        dimension=4,
        normalization="l2-v1",
        document_prefix="search_document: ",
        query_prefix="search_query: ",
    )


def _stage_source_projection(
    vector_root: Path,
    *,
    cell_id: str,
    solver_id: str,
    topology: dict,
    embedding: dict,
) -> str:
    topology_digest = vector_projection.retrieval_topology_digest(topology)
    document = vector_projection.build_solver_contract_projection(
        {
            "model_id": solver_id,
            "model_name": solver_id.replace("_", " ").title(),
            "description": f"Deterministic capability for {cell_id}.",
            "cell_id": cell_id,
            "variables": {"input": {"unit": "1"}},
            "solver_output_schema": {
                "primary_value": {"name": "value", "unit": "1"}
            },
        },
        source_digest=_digest(f"{cell_id}:{solver_id}"),
        topology_digest=topology_digest,
    )
    identity = vector_projection.build_projection_source_identity(document)
    dedup_key = vector_indexer._projected_upsert_dedup_key(
        identity, embedding, topology_digest
    )
    state = vector_indexer.CellState(
        cell_id=cell_id,
        signatures={solver_id: document["solver_contract_digest"]},
        projection_mode=True,
        projection_documents={solver_id: document},
        projection_source_identities={solver_id: identity},
        projection_embedding_contracts={solver_id: embedding},
        projection_topology_digests={solver_id: topology_digest},
        seen_source_identities={
            dedup_key: vector_indexer._projected_upsert_fingerprint(
                document, embedding, topology_digest
            )
        },
        embedding_contract=embedding,
        topology_digest=topology_digest,
    )
    return vector_indexer._stage_projection_commit(state, vector_root)[
        "commit_id"
    ]


def _source_request(
    tmp_path: Path,
    *,
    source_name: str = "source",
    embedding: dict | None = None,
) -> tuple[candidate.CandidateRequest, Path]:
    topology = _topology()
    embedding = embedding or _embedding()
    vector_root = tmp_path / ".codex-audit" / source_name / "vector"
    cells = []
    for cell_id, solver_id in (
        ("energy", "energy_solver"),
        ("thermal", "thermal_solver"),
    ):
        commit_id = _stage_source_projection(
            vector_root,
            cell_id=cell_id,
            solver_id=solver_id,
            topology=topology,
            embedding=embedding,
        )
        cells.append(
            {"cell_id": cell_id, "projection_commit_id": commit_id}
        )
    request_path = tmp_path / f"{source_name}-request.json"
    request_path.write_text(
        json.dumps(
            {
                "schema_version": candidate.REQUEST_SCHEMA,
                "vector_root": f".codex-audit/{source_name}/vector",
                "embedding_contract": embedding,
                "topology_contract": topology,
                "cells": cells,
            }
        ),
        encoding="utf-8",
    )
    return (
        candidate.load_candidate_request(request_path, repo_root=tmp_path),
        request_path,
    )


def test_adopts_unreceipted_projection_set_without_authority_claims(
    tmp_path: Path,
) -> None:
    request, _request_path = _source_request(tmp_path)
    output_root = candidate.resolve_candidate_root(
        ".codex-audit/receipt-adoption", repo_root=tmp_path
    )
    source_before = candidate._directory_digests(request.vector_root)

    first = adoption.adopt_projection_receipts(
        request,
        output_root=output_root,
        admitted_at_utc="2026-08-10T07:00:00Z",
    )
    second = adoption.adopt_projection_receipts(
        request,
        output_root=output_root,
        admitted_at_utc="2026-08-10T07:00:00Z",
    )
    verified = adoption.load_verified_receipt_adoption(
        first["adoption_path"],
        repo_root=tmp_path,
        expected_source_request=request,
    )

    assert first["status"] == "adopted"
    assert second["status"] == "already_exists"
    assert second["adoption_id"] == first["adoption_id"]
    assert candidate._directory_digests(request.vector_root) == source_before
    manifest = verified["manifest"]
    assert manifest["total_projection_count"] == 2
    assert manifest["source_unreceipted_count"] == 2
    assert manifest["target_receipt_bound_count"] == 2
    assert manifest["upsert_event_count"] == 2
    assert manifest["commit_applied_event_count"] == 2
    assert manifest["receipt_semantics"] == "self_certified_structure_only"
    assert manifest["receipt_authenticity_verified"] is False
    assert manifest["external_authority_artifacts_verified"] is False
    assert manifest["solver_outcome_verified"] is False
    assert manifest["runtime_authority_granted"] is False
    assert manifest["chromosome_coverage_evaluated"] is False
    assert manifest["gene_bank_ready"] is False
    assert verified["source_request_reverified"] is True
    assert verified["event_chain_reverified"] is True
    assert verified["checkpoint_reverified"] is True

    adoption_path = Path(first["adoption_path"])
    events = list(
        vector_events.read_events(
            adoption_path / "events.jsonl", strict=True
        )
    )
    assert [event.event for event in events] == [
        vector_events.EVT_VECTOR_UPSERT_REQUESTED,
        vector_events.EVT_VECTOR_UPSERT_REQUESTED,
        vector_events.EVT_VECTOR_COMMIT_APPLIED,
        vector_events.EVT_VECTOR_COMMIT_APPLIED,
    ]
    assert all(event.ts == "2026-08-10T07:00:00Z" for event in events)
    for summary, commit_event in zip(manifest["cells"], events[2:]):
        assert commit_event.event_id() == summary["commit_applied_event_id"]
        assert (
            commit_event.payload["source_events"]
            == summary["source_upsert_event_ids"]
        )
    checkpoint = vector_indexer.load_checkpoint(
        adoption_path / "checkpoint.json"
    )
    assert checkpoint.last_applied_ts == "2026-08-10T07:00:00Z"
    assert b"\r\n" not in (adoption_path / "checkpoint.json").read_bytes()

    adopted_request = verified["candidate_request"]
    for cell_id, commit_id in adopted_request.cells:
        loaded = vector_indexer.load_verified_projection_commit(
            adopted_request.vector_root,
            cell_id,
            expected_embedding_contract=adopted_request.embedding_contract,
            expected_topology_digest=adopted_request.topology_digest,
            commit_id=commit_id,
        )
        assert loaded["receipt_bound_count"] == 1
        assert loaded["unreceipted_count"] == 0
        [row] = loaded["documents"]
        identity = row["source_identity"]
        assert identity["schema_version"] == (
            vector_projection.PROJECTION_SOURCE_IDENTITY_V2_VERSION
        )
        assert identity["receipt_authenticity_verified"] is False
        context = identity["receipt_proof"]["receipt_context"]
        assert context["rco_decision_artifact"]["review_performed"] is False
        assert context["world_snapshot_artifact"]["external_state_observed"] is False
        receipt = identity["receipt_proof"]["receipt"]
        assert {
            key: receipt[key]
            for key in (
                "policy_digest",
                "charter_digest",
                "rco_decision_digest",
                "world_snapshot_digest",
            )
        } == vector_projection.projection_receipt_context_digests(context)


def test_adoption_rejects_invalid_timestamp_before_writing(tmp_path: Path) -> None:
    request, _request_path = _source_request(tmp_path)
    output_root = tmp_path / ".codex-audit" / "invalid-timestamp"

    with pytest.raises(
        candidate.CandidateContractError,
        match="canonical RFC3339 UTC Z form",
    ):
        adoption.adopt_projection_receipts(
            request,
            output_root=output_root,
            admitted_at_utc="not-a-timestamp",
        )

    assert not output_root.exists()


def test_adoption_refuses_output_outside_audit_root(tmp_path: Path) -> None:
    request, _request_path = _source_request(tmp_path)

    with pytest.raises(candidate.CandidateContractError, match="beneath"):
        adoption.adopt_projection_receipts(
            request,
            output_root=tmp_path / "outside",
            admitted_at_utc="2026-08-10T07:00:00Z",
        )


def test_preexisting_adoptions_link_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_root = tmp_path / ".codex-audit" / "linked-adoptions"
    adoptions_root = output_root / "adoptions"
    adoptions_root.mkdir(parents=True)
    real_is_link_like = candidate._is_link_like

    monkeypatch.setattr(
        candidate,
        "_is_link_like",
        lambda path: path == adoptions_root or real_is_link_like(path),
    )

    with pytest.raises(
        candidate.CandidateContractError, match="must not be a link"
    ):
        adoption._prepare_adoptions_root(
            output_root, tmp_path / ".codex-audit"
        )


@pytest.mark.parametrize("use_parent", [False, True])
def test_adoption_refuses_source_output_overlap_before_writing(
    tmp_path: Path,
    use_parent: bool,
) -> None:
    request, _request_path = _source_request(tmp_path)
    source_before = candidate._directory_digests(request.vector_root)
    output_root = (
        request.vector_root.parent if use_parent else request.vector_root
    )

    with pytest.raises(
        candidate.CandidateContractError,
        match="must not overlap source vector root",
    ):
        adoption.adopt_projection_receipts(
            request,
            output_root=output_root,
            admitted_at_utc="2026-08-10T07:00:00Z",
        )

    assert candidate._directory_digests(request.vector_root) == source_before
    assert not (output_root / "adoptions").exists()


def test_adoption_authenticity_overclaim_fails_after_readdressing(
    tmp_path: Path,
) -> None:
    request, _request_path = _source_request(tmp_path)
    output_root = candidate.resolve_candidate_root(
        ".codex-audit/receipt-claim", repo_root=tmp_path
    )
    report = adoption.adopt_projection_receipts(
        request,
        output_root=output_root,
        admitted_at_utc="2026-08-10T07:00:00Z",
    )
    manifest_path = Path(report["adoption_path"]) / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["receipt_authenticity_verified"] = True
    new_id = adoption.ADOPTION_PREFIX + adoption.sha256_digest(
        adoption._adoption_identity(manifest)
    ).split(":", 1)[1]
    manifest["adoption_id"] = new_id
    manifest_path.write_bytes(adoption._canonical_json_line(manifest))
    rewritten = manifest_path.parent.with_name(new_id)
    manifest_path.parent.rename(rewritten)

    with pytest.raises(
        candidate.CandidateContractError,
        match="authority posture is invalid",
    ):
        adoption.load_verified_receipt_adoption(
            rewritten,
            repo_root=tmp_path,
            expected_source_request=request,
        )


def test_adoption_candidate_request_tamper_fails_closed(tmp_path: Path) -> None:
    request, _request_path = _source_request(tmp_path)
    output_root = candidate.resolve_candidate_root(
        ".codex-audit/request-tamper", repo_root=tmp_path
    )
    report = adoption.adopt_projection_receipts(
        request,
        output_root=output_root,
        admitted_at_utc="2026-08-10T07:00:00Z",
    )
    request_path = Path(report["candidate_request_path"])
    request_path.write_bytes(request_path.read_bytes() + b" ")

    with pytest.raises(
        candidate.CandidateContractError,
        match="candidate request digest mismatch",
    ):
        adoption.load_verified_receipt_adoption(
            report["adoption_path"],
            repo_root=tmp_path,
            expected_source_request=request,
        )


def test_adoption_event_timestamp_tamper_fails_closed(tmp_path: Path) -> None:
    request, _request_path = _source_request(tmp_path)
    output_root = candidate.resolve_candidate_root(
        ".codex-audit/event-time-tamper", repo_root=tmp_path
    )
    report = adoption.adopt_projection_receipts(
        request,
        output_root=output_root,
        admitted_at_utc="2026-08-10T07:00:00Z",
    )
    event_path = Path(report["adoption_path"]) / "events.jsonl"
    rows = [
        json.loads(line) for line in event_path.read_text("utf-8").splitlines()
    ]
    rows[-1]["ts"] = "1900-01-01T00:00:00Z"
    event_path.write_bytes(
        b"".join(adoption._canonical_json_line(row) for row in rows)
    )

    with pytest.raises(
        candidate.CandidateContractError,
        match="event provenance mismatch",
    ):
        adoption.load_verified_receipt_adoption(
            report["adoption_path"],
            repo_root=tmp_path,
            expected_source_request=request,
        )


def test_adoption_checkpoint_timestamp_tamper_fails_closed(
    tmp_path: Path,
) -> None:
    request, _request_path = _source_request(tmp_path)
    output_root = candidate.resolve_candidate_root(
        ".codex-audit/checkpoint-time-tamper", repo_root=tmp_path
    )
    report = adoption.adopt_projection_receipts(
        request,
        output_root=output_root,
        admitted_at_utc="2026-08-10T07:00:00Z",
    )
    checkpoint_path = Path(report["adoption_path"]) / "checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text("utf-8"))
    checkpoint["last_applied_ts"] = "1900-01-01T00:00:00Z"
    checkpoint_path.write_text(
        json.dumps(checkpoint, indent=2, sort_keys=True), encoding="utf-8"
    )

    with pytest.raises(
        candidate.CandidateContractError,
        match="checkpoint provenance mismatch",
    ):
        adoption.load_verified_receipt_adoption(
            report["adoption_path"],
            repo_root=tmp_path,
            expected_source_request=request,
        )


def test_adoption_previous_pointer_forge_fails_closed(tmp_path: Path) -> None:
    request, _request_path = _source_request(tmp_path)
    output_root = candidate.resolve_candidate_root(
        ".codex-audit/pointer-forge", repo_root=tmp_path
    )
    report = adoption.adopt_projection_receipts(
        request,
        output_root=output_root,
        admitted_at_utc="2026-08-10T07:00:00Z",
    )
    [first_cell, *_] = report["manifest"]["cells"]
    pointer_path = (
        Path(report["adoption_path"])
        / "vector"
        / first_cell["cell_id"]
        / "current.json"
    )
    pointer = json.loads(pointer_path.read_text("utf-8"))
    pointer["previous_commit_id"] = "proj_" + "f" * 64
    pointer_path.write_bytes(adoption._canonical_json_line(pointer))

    with pytest.raises(
        candidate.CandidateContractError,
        match="current pointer mismatch",
    ):
        adoption.load_verified_receipt_adoption(
            report["adoption_path"],
            repo_root=tmp_path,
            expected_source_request=request,
        )


def test_adoption_source_contract_forge_fails_closed(tmp_path: Path) -> None:
    source_a, _ = _source_request(
        tmp_path,
        source_name="source-a",
        embedding=_embedding("a"),
    )
    source_b, _ = _source_request(
        tmp_path,
        source_name="source-b",
        embedding=_embedding("b"),
    )
    output_root = candidate.resolve_candidate_root(
        ".codex-audit/source-contract-forge", repo_root=tmp_path
    )
    report = adoption.adopt_projection_receipts(
        source_b,
        output_root=output_root,
        admitted_at_utc="2026-08-10T07:00:00Z",
    )
    old_root = Path(report["adoption_path"])
    manifest_path = old_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["source_request_digest"] = adoption.sha256_digest(
        adoption._source_request_payload(source_a)
    )
    source_a_commits = dict(source_a.cells)
    for cell in manifest["cells"]:
        cell["source_projection_commit_id"] = source_a_commits[
            cell["cell_id"]
        ]
    new_id = adoption.ADOPTION_PREFIX + adoption.sha256_digest(
        adoption._adoption_identity(manifest)
    ).split(":", 1)[1]
    manifest["adoption_id"] = new_id
    request_path = old_root / "candidate-request.json"
    request_payload = json.loads(request_path.read_text("utf-8"))
    request_payload["vector_root"] = (
        f".codex-audit/source-contract-forge/adoptions/{new_id}/vector"
    )
    request_bytes = adoption._canonical_json_line(request_payload)
    manifest["candidate_request_digest"] = adoption._sha256_bytes(
        request_bytes
    )
    request_path.write_bytes(request_bytes)
    manifest_path.write_bytes(adoption._canonical_json_line(manifest))
    rewritten = old_root.with_name(new_id)
    old_root.rename(rewritten)

    with pytest.raises(
        candidate.CandidateContractError,
        match="source request mismatch",
    ):
        adoption.load_verified_receipt_adoption(
            rewritten,
            repo_root=tmp_path,
            expected_source_request=source_a,
        )


def test_adoption_apply_failure_publishes_no_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, _request_path = _source_request(tmp_path)
    output_root = candidate.resolve_candidate_root(
        ".codex-audit/apply-failure", repo_root=tmp_path
    )

    def _fail_apply(*args: object, **kwargs: object) -> object:
        raise RuntimeError("injected indexer failure")

    monkeypatch.setattr(adoption.vector_indexer, "apply", _fail_apply)
    with pytest.raises(RuntimeError, match="injected indexer failure"):
        adoption.adopt_projection_receipts(
            request,
            output_root=output_root,
            admitted_at_utc="2026-08-10T07:00:00Z",
        )

    adoptions_root = output_root / "adoptions"
    assert adoptions_root.is_dir()
    assert list(adoptions_root.iterdir()) == []


def test_adoption_post_publish_verification_failure_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, _request_path = _source_request(tmp_path)
    output_root = candidate.resolve_candidate_root(
        ".codex-audit/post-publish-failure", repo_root=tmp_path
    )

    def _fail_verification(*args: object, **kwargs: object) -> object:
        raise candidate.CandidateContractError(
            "injected post-publish verification failure"
        )

    monkeypatch.setattr(
        adoption, "load_verified_receipt_adoption", _fail_verification
    )
    with pytest.raises(
        candidate.CandidateContractError,
        match="injected post-publish verification failure",
    ):
        adoption.adopt_projection_receipts(
            request,
            output_root=output_root,
            admitted_at_utc="2026-08-10T07:00:00Z",
        )

    adoptions_root = output_root / "adoptions"
    assert adoptions_root.is_dir()
    assert list(adoptions_root.iterdir()) == []
