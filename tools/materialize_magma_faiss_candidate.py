#!/usr/bin/env python3
"""Materialize a verified, candidate-only MAGMA FAISS snapshot.

The tool consumes an explicit mapping of immutable ``proj_<sha256>`` solver
projection commits.  It never follows ``current.json``, mutates the source
commits, emits vector events, or writes beneath runtime ``data/faiss*`` paths.
All output is content-addressed beneath ``.codex-audit``.

Physical indices are per live leaf cell, while candidate search remains global:
each query searches every cell and results are merged deterministically.  Cell
routing and runtime authority are deliberately outside this milestone.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import benchmark_magma_solver_retrieval as retrieval_benchmark  # noqa: E402
from tools import vector_indexer  # noqa: E402
from waggledance.core.magma import vector_projection  # noqa: E402
from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest  # noqa: E402


REQUEST_SCHEMA = "magma.faiss.candidate_request.v1"
SNAPSHOT_SCHEMA = "magma.faiss.candidate_snapshot.v1"
CELL_SCHEMA = "magma.faiss.candidate_cell.v1"
ROW_SCHEMA = "magma.faiss.candidate_row.v1"
SNAPSHOT_PREFIX = "faisscand_"
CELL_PREFIX = "faisscell_"
_PROJECTION_COMMIT_ID = re.compile(r"^proj_[0-9a-f]{64}$")
_SNAPSHOT_ID = re.compile(r"^faisscand_[0-9a-f]{64}$")
_CELL_COMMIT_ID = re.compile(r"^faisscell_[0-9a-f]{64}$")
_FULL_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_REQUEST_KEYS = frozenset(
    {"schema_version", "vector_root", "embedding_contract", "topology_contract", "cells"}
)
_REQUEST_CELL_KEYS = frozenset({"cell_id", "projection_commit_id"})
_ROW_KEYS = frozenset(
    {
        "schema_version",
        "row_index",
        "canonical_solver_id",
        "projection_id",
        "projection_digest",
        "source_digest",
        "source_identity_digest",
        "receipt_bound",
    }
)
_CELL_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "cell_commit_id",
        "cell_id",
        "source_projection_commit_id",
        "source_projection_manifest_sha256",
        "projection_digests",
        "source_identity_digests",
        "receipt_bound_count",
        "unreceipted_count",
        "rows_sha256",
        "vectors_sha256",
        "index_sha256",
        "embedding_contract_digest",
        "topology_digest",
        "index_kind",
        "metric",
        "dimension",
        "vector_count",
        "faiss_version",
        "faiss_compile_options",
        "faiss_binary_set_sha256",
    }
)
_SNAPSHOT_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "snapshot_id",
        "scope",
        "runtime_authority_ready",
        "cell_local_routing_evaluated",
        "chromosome_count",
        "chromosome_coverage_evaluated",
        "gene_bank_ready",
        "embedding_contract",
        "embedding_provider_identity",
        "topology_contract",
        "topology_digest",
        "faiss_version",
        "faiss_compile_options",
        "faiss_binary_set_sha256",
        "total_vector_count",
        "receipt_bound_count",
        "unreceipted_count",
        "cells",
        "persisted_parity",
    }
)
_SNAPSHOT_CELL_KEYS = frozenset(
    {
        "cell_id",
        "cell_commit_id",
        "source_projection_commit_id",
        "manifest_sha256",
        "vector_count",
    }
)
_PROVIDER_IDENTITY_KEYS = frozenset(
    {
        "provider",
        "requested_model_tag",
        "catalog_digest",
        "catalog_contract_verified_before_embedding",
        "catalog_contract_verified_after_embedding",
        "response_digest_attested",
    }
)
_PARITY_KEYS = frozenset(
    {
        "document_probe_count",
        "random_probe_count",
        "probe_count",
        "rankings_match_at_k",
        "max_abs_score_error",
        "score_tolerance",
        "global_all_cell_merge",
        "exact_rankings_match",
    }
)


class CandidateContractError(ValueError):
    """An input or persisted candidate violates the fail-closed contract."""


class CandidateUnavailable(RuntimeError):
    """Required candidate machinery is absent; no fallback is permitted."""


@dataclass(frozen=True)
class CandidateRequest:
    repo_root: Path
    audit_root: Path
    vector_root: Path
    embedding_contract: dict[str, Any]
    topology_contract: dict[str, Any]
    topology_digest: str
    cells: tuple[tuple[str, str], ...]


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _canonical_json_line(value: Any) -> bytes:
    return canonical_json_bytes(value) + b"\n"


def _is_link_like(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or (callable(is_junction) and is_junction())


def _require_exact_keys(value: Any, expected: frozenset[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or frozenset(value) != expected:
        raise CandidateContractError(f"{label} has an unknown schema shape")
    return value


def _require_regular_single_link_file(path: Path, label: str) -> None:
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise CandidateContractError(f"{label} metadata is unavailable") from exc
    if _is_link_like(path) or not path.is_file() or metadata.st_nlink != 1:
        raise CandidateContractError(f"{label} must be a regular single-link file")


def _require_contained_existing_components(path: Path, root: Path, label: str) -> None:
    cursor = path
    while True:
        if cursor.exists() and _is_link_like(cursor):
            raise CandidateContractError(f"{label} must not traverse a link")
        if cursor == root:
            return
        if cursor.parent == cursor:
            raise CandidateContractError(f"{label} escapes its root")
        cursor = cursor.parent


def _resolve_repo_file(path: Path | str, repo_root: Path, label: str) -> Path:
    raw = Path(path)
    candidate = raw if raw.is_absolute() else repo_root / raw
    resolved_repo = repo_root.resolve()
    resolved = candidate.resolve()
    try:
        resolved.relative_to(resolved_repo)
    except ValueError as exc:
        raise CandidateContractError(f"{label} escapes repository root") from exc
    _require_contained_existing_components(candidate.absolute(), repo_root.absolute(), label)
    _require_regular_single_link_file(candidate, label)
    return resolved


def _resolve_vector_root(raw: Any, repo_root: Path) -> Path:
    if type(raw) is not str or not raw or Path(raw).is_absolute():
        raise CandidateContractError("vector_root must be a repository-relative path")
    resolved_repo = repo_root.resolve()
    unresolved = repo_root / raw
    resolved = unresolved.resolve()
    try:
        resolved.relative_to(resolved_repo)
    except ValueError as exc:
        raise CandidateContractError("vector_root escapes repository root") from exc
    _require_contained_existing_components(unresolved.absolute(), repo_root.absolute(), "vector_root")
    if not resolved.is_dir():
        raise CandidateContractError("vector_root is not an existing directory")
    return resolved


def resolve_candidate_root(raw: str, repo_root: Path = ROOT) -> Path:
    if not raw or Path(raw).is_absolute():
        raise CandidateContractError("output root must be repository-relative")
    unresolved_audit = repo_root / ".codex-audit"
    if _is_link_like(unresolved_audit):
        raise CandidateContractError(".codex-audit must not be a link")
    audit_root = unresolved_audit.resolve()
    unresolved = repo_root / raw
    resolved = unresolved.resolve()
    try:
        relative = resolved.relative_to(audit_root)
    except ValueError as exc:
        raise CandidateContractError("output root must remain beneath .codex-audit") from exc
    if not relative.parts:
        raise CandidateContractError("output root must name a directory beneath .codex-audit")
    _require_contained_existing_components(unresolved.absolute(), unresolved_audit.absolute(), "output root")
    if unresolved.exists() and not unresolved.is_dir():
        raise CandidateContractError("output root must be a directory")
    return resolved


def _validate_materialization_root(output_root: Path, audit_root: Path) -> None:
    absolute = output_root.absolute()
    expected_audit = audit_root.absolute()
    try:
        relative = absolute.relative_to(expected_audit)
    except ValueError as exc:
        raise CandidateContractError(
            "materialization root must be beneath the repository .codex-audit"
        ) from exc
    if not relative.parts:
        raise CandidateContractError("materialization root must be beneath .codex-audit")
    if _is_link_like(expected_audit):
        raise CandidateContractError(".codex-audit must not be a link")
    _require_contained_existing_components(
        absolute, expected_audit, "materialization root"
    )
    if output_root.exists() and not output_root.is_dir():
        raise CandidateContractError("materialization root must be a directory")


def load_candidate_request(
    request_path: Path | str,
    *,
    repo_root: Path = ROOT,
) -> CandidateRequest:
    path = _resolve_repo_file(request_path, repo_root, "candidate request")
    try:
        value = json.loads(path.read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CandidateContractError("candidate request is not valid JSON") from exc
    request = _require_exact_keys(value, _REQUEST_KEYS, "candidate request")
    if request["schema_version"] != REQUEST_SCHEMA:
        raise CandidateContractError("unsupported candidate request schema")
    embedding = vector_projection.validate_embedding_contract(
        request["embedding_contract"]
    )
    if embedding["normalization"] != "l2-v1":
        raise CandidateContractError("candidate requires l2-v1 normalization")
    version_prefix = "ollama-catalog-sha256:"
    if not embedding["model_version"].startswith(version_prefix):
        raise CandidateContractError("candidate requires a catalog-sha256 model version")
    digest = embedding["model_version"][len(version_prefix) :]
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise CandidateContractError("candidate model catalog digest is invalid")
    topology = vector_projection.validate_retrieval_topology_contract(
        request["topology_contract"]
    )
    topology_digest = vector_projection.retrieval_topology_digest(topology)
    raw_cells = request["cells"]
    if type(raw_cells) is not list or not raw_cells:
        raise CandidateContractError("candidate request cells must be a non-empty list")
    cells: list[tuple[str, str]] = []
    for index, raw_cell in enumerate(raw_cells):
        row = _require_exact_keys(
            raw_cell, _REQUEST_CELL_KEYS, f"candidate request cells[{index}]"
        )
        cell_id = vector_projection.validate_vector_cell_id(row["cell_id"])
        commit_id = row["projection_commit_id"]
        if type(commit_id) is not str or not _PROJECTION_COMMIT_ID.fullmatch(commit_id):
            raise CandidateContractError("projection_commit_id must contain one full sha256")
        cells.append((cell_id, commit_id))
    if len({cell_id for cell_id, _commit_id in cells}) != len(cells):
        raise CandidateContractError("candidate request contains duplicate cells")
    live_leaf_cells = {
        row["cell_id"]
        for row in topology["cells"]
        if row["live"] and row["subdivision_state"] == "leaf"
    }
    if {cell_id for cell_id, _commit_id in cells} != live_leaf_cells:
        raise CandidateContractError("candidate request does not cover every live leaf cell")
    vector_root = _resolve_vector_root(request["vector_root"], repo_root)
    resolved_repo = repo_root.resolve()
    return CandidateRequest(
        repo_root=resolved_repo,
        audit_root=resolved_repo / ".codex-audit",
        vector_root=vector_root,
        embedding_contract=embedding,
        topology_contract=topology,
        topology_digest=topology_digest,
        cells=tuple(sorted(cells)),
    )


def _load_source_cells(request: CandidateRequest) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    seen_solvers: set[str] = set()
    seen_projections: set[str] = set()
    for cell_id, commit_id in request.cells:
        cell_dir = request.vector_root / cell_id
        commits_dir = cell_dir / "commits"
        commit_dir = commits_dir / commit_id
        for path in (cell_dir, commits_dir, commit_dir):
            if _is_link_like(path):
                raise CandidateContractError("projection source path must not be a link")
        result = vector_indexer.load_verified_projection_commit(
            request.vector_root,
            cell_id,
            expected_embedding_contract=request.embedding_contract,
            expected_topology_digest=request.topology_digest,
            commit_id=commit_id,
        )
        rows = sorted(
            result["documents"],
            key=lambda row: row["projection_document"]["canonical_solver_id"],
        )
        for row in rows:
            document = vector_projection.validate_solver_contract_projection(
                row["projection_document"]
            )
            identity = vector_projection.validate_projection_source_identity(
                row["source_identity"]
            )
            solver_id = document["canonical_solver_id"]
            projection_id = document["projection_id"]
            if solver_id in seen_solvers:
                raise CandidateContractError("solver appears in more than one candidate cell")
            if projection_id in seen_projections:
                raise CandidateContractError("projection appears in more than one candidate cell")
            seen_solvers.add(solver_id)
            seen_projections.add(projection_id)
            if document["cell_id"] != cell_id:
                raise CandidateContractError("projection document is assigned to another cell")
            if identity["canonical_solver_id"] != solver_id:
                raise CandidateContractError("source identity does not bind solver")
        sources.append(
            {
                "cell_id": cell_id,
                "commit_id": commit_id,
                "projection_manifest_sha256": result["commit"]["manifest_sha256"],
                "rows": rows,
            }
        )
    if not seen_solvers:
        raise CandidateContractError("candidate source snapshot contains no solvers")
    return sources


def _profile_from_contract(contract: Mapping[str, Any]) -> retrieval_benchmark.EmbeddingProfile:
    prefix = "ollama-catalog-sha256:"
    model_version = str(contract["model_version"])
    if not model_version.startswith(prefix):
        raise CandidateContractError("embedding model version is not catalog-bound")
    return retrieval_benchmark.EmbeddingProfile(
        name="candidate",
        model_id=str(contract["model_id"]),
        model_digest=model_version[len(prefix) :],
        dimension=int(contract["dimension"]),
        document_prefix=str(contract["document_prefix"]),
        query_prefix=str(contract["query_prefix"]),
    )


def _embed_source_cells(
    sources: Sequence[Mapping[str, Any]],
    contract: Mapping[str, Any],
    embedder: Any,
) -> tuple[np.ndarray, dict[str, Any]]:
    profile = _profile_from_contract(contract)
    documents = [
        row["projection_document"]
        for source in sources
        for row in source["rows"]
    ]
    inputs = [profile.document_prefix + row["embedding_text"] for row in documents]
    try:
        identity_before = embedder.verify_profile(profile)
        raw = embedder.embed(inputs, profile, label="candidate_document_embeddings")
    except retrieval_benchmark.BenchmarkUnavailable as exc:
        raise CandidateUnavailable(str(exc)) from exc
    matrix = retrieval_benchmark.normalize_embedding_matrix(
        raw,
        expected_rows=len(inputs),
        expected_dimension=profile.dimension,
        label="candidate_document_embeddings",
    )
    try:
        identity_after = embedder.verify_profile(profile)
    except retrieval_benchmark.BenchmarkUnavailable as exc:
        raise CandidateUnavailable(str(exc)) from exc
    if identity_after != identity_before:
        raise CandidateContractError("embedding model catalog changed during materialization")
    identity_before_verified = retrieval_benchmark.provider_identity_matches_profile(
        identity_before, profile
    )
    identity_after_verified = retrieval_benchmark.provider_identity_matches_profile(
        identity_after, profile
    )
    if not identity_before_verified or not identity_after_verified:
        raise CandidateContractError("embedding provider identity evidence is invalid")
    provider = identity_before.get("provider")
    requested_model = identity_before.get("requested_model_tag")
    catalog_digest = identity_before.get("catalog_digest")
    if (
        not isinstance(provider, str)
        or not provider
        or requested_model != profile.model_id
        or catalog_digest != profile.model_digest
    ):
        raise CandidateContractError("embedding provider identity does not match contract")
    return matrix, {
        "provider": provider,
        "requested_model_tag": requested_model,
        "catalog_digest": catalog_digest,
        "catalog_contract_verified_before_embedding": identity_before_verified,
        "catalog_contract_verified_after_embedding": identity_after_verified,
        "response_digest_attested": False,
    }


def _import_faiss() -> Any:
    try:
        return importlib.import_module("faiss")
    except ModuleNotFoundError as exc:
        if exc.name != "faiss":
            raise
        raise CandidateUnavailable("faiss_not_installed") from exc


def _faiss_identity(faiss_module: Any) -> dict[str, str]:
    index_class = getattr(faiss_module, "IndexFlatIP", None)
    if not isinstance(index_class, type):
        raise CandidateUnavailable("faiss_index_flat_ip_api_unavailable")
    if getattr(faiss_module, "METRIC_INNER_PRODUCT", None) is None:
        raise CandidateUnavailable("faiss_inner_product_metric_unavailable")
    if not callable(getattr(faiss_module, "write_index", None)) or not callable(
        getattr(faiss_module, "read_index", None)
    ):
        raise CandidateUnavailable("faiss_persistence_api_unavailable")
    version = getattr(faiss_module, "__version__", None)
    get_options = getattr(faiss_module, "get_compile_options", None)
    try:
        options = get_options() if callable(get_options) else None
    except Exception as exc:
        raise CandidateUnavailable("faiss_compile_options_unavailable") from exc
    if not isinstance(version, str) or not version.strip():
        raise CandidateUnavailable("faiss_version_unavailable")
    if not isinstance(options, str):
        raise CandidateUnavailable("faiss_compile_options_invalid")
    explicit_fingerprint = getattr(faiss_module, "_candidate_binary_set_sha256", None)
    if explicit_fingerprint is not None:
        if not isinstance(explicit_fingerprint, str) or not _FULL_DIGEST.fullmatch(
            explicit_fingerprint
        ):
            raise CandidateUnavailable("faiss_binary_fingerprint_invalid")
        binary_fingerprint = explicit_fingerprint
    else:
        module_file = getattr(faiss_module, "__file__", None)
        if not isinstance(module_file, str):
            raise CandidateUnavailable("faiss_module_path_unavailable")
        package_dir = Path(module_file).resolve().parent
        binaries = sorted(
            path
            for path in package_dir.iterdir()
            if path.is_file() and path.suffix.lower() in {".dll", ".dylib", ".pyd", ".so"}
        )
        if not binaries:
            raise CandidateUnavailable("faiss_binary_set_unavailable")
        binary_rows = [
            {"name": path.name, "sha256": _sha256_bytes(path.read_bytes())}
            for path in binaries
        ]
        binary_fingerprint = sha256_digest(binary_rows)
    return {
        "faiss_version": version.strip(),
        "faiss_compile_options": options.strip() or "UNREPORTED_BY_FAISS_API",
        "faiss_binary_set_sha256": binary_fingerprint,
    }


def _write_fsynced(path: Path, payload: bytes) -> None:
    with open(path, "xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _write_index_fsynced(faiss_module: Any, index: Any, path: Path) -> None:
    try:
        faiss_module.write_index(index, str(path))
    except Exception as exc:
        raise CandidateContractError("FAISS index write failed") from exc
    _require_regular_single_link_file(path, "candidate FAISS index")
    with open(path, "r+b") as stream:
        stream.flush()
        os.fsync(stream.fileno())


def _read_rows(path: Path, expected_count: int) -> list[dict[str, Any]]:
    _require_regular_single_link_file(path, "candidate rows")
    raw = path.read_bytes()
    if expected_count == 0:
        if raw:
            raise CandidateContractError("empty candidate cell has row data")
        return []
    if not raw or not raw.endswith(b"\n"):
        raise CandidateContractError("candidate rows are not canonical JSONL")
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(raw.splitlines()):
        try:
            value = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CandidateContractError("candidate row is invalid JSON") from exc
        row = _require_exact_keys(value, _ROW_KEYS, "candidate row")
        if line + b"\n" != _canonical_json_line(row):
            raise CandidateContractError("candidate row is not canonically encoded")
        if (
            row["schema_version"] != ROW_SCHEMA
            or type(row["row_index"]) is not int
            or row["row_index"] != index
        ):
            raise CandidateContractError("candidate row index or schema is invalid")
        vector_projection.validate_solver_id(row["canonical_solver_id"])
        for key in (
            "projection_id",
            "projection_digest",
            "source_digest",
            "source_identity_digest",
        ):
            if type(row[key]) is not str or not _FULL_DIGEST.fullmatch(row[key]):
                raise CandidateContractError(f"candidate row {key} is invalid")
        if type(row["receipt_bound"]) is not bool:
            raise CandidateContractError("candidate row receipt_bound is invalid")
        rows.append(row)
    if len(rows) != expected_count:
        raise CandidateContractError("candidate row count mismatch")
    solver_ids = [row["canonical_solver_id"] for row in rows]
    if solver_ids != sorted(solver_ids) or len(solver_ids) != len(set(solver_ids)):
        raise CandidateContractError("candidate rows are not uniquely solver-sorted")
    return rows


def _read_canonical_json(path: Path, expected_keys: frozenset[str], label: str) -> dict[str, Any]:
    _require_regular_single_link_file(path, label)
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CandidateContractError(f"{label} is invalid JSON") from exc
    result = _require_exact_keys(value, expected_keys, label)
    if raw != _canonical_json_line(result):
        raise CandidateContractError(f"{label} is not canonically encoded")
    return result


def _cell_identity(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in manifest.items() if key != "cell_commit_id"}


def _snapshot_identity(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in manifest.items() if key != "snapshot_id"}


def _validate_index_metadata(
    index: Any,
    *,
    dimension: int,
    vector_count: int,
    faiss_module: Any,
) -> None:
    if int(getattr(index, "d", -1)) != dimension:
        raise CandidateContractError("persisted FAISS dimension mismatch")
    if int(getattr(index, "ntotal", -1)) != vector_count:
        raise CandidateContractError("persisted FAISS vector count mismatch")
    index_class = getattr(faiss_module, "IndexFlatIP", None)
    if not isinstance(index_class, type) or not isinstance(index, index_class):
        raise CandidateContractError("persisted FAISS index is not IndexFlatIP")
    metric = getattr(index, "metric_type", None)
    expected_metric = getattr(faiss_module, "METRIC_INNER_PRODUCT", None)
    if expected_metric is None:
        raise CandidateUnavailable("faiss_inner_product_metric_unavailable")
    if metric != expected_metric:
        raise CandidateContractError("persisted FAISS metric mismatch")


def _reconstruct_index(index: Any, count: int, dimension: int) -> np.ndarray:
    if count == 0:
        return np.empty((0, dimension), dtype=np.float32)
    reconstruct = getattr(index, "reconstruct_n", None)
    if not callable(reconstruct):
        raise CandidateContractError("persisted FAISS index cannot be reconstructed")
    try:
        values = reconstruct(0, count)
    except TypeError:
        values = np.empty((count, dimension), dtype=np.float32)
        try:
            reconstruct(0, count, values)
        except Exception as exc:
            raise CandidateContractError("persisted FAISS reconstruction failed") from exc
    except Exception as exc:
        raise CandidateContractError("persisted FAISS reconstruction failed") from exc
    raw = np.ascontiguousarray(np.asarray(values, dtype=np.float32))
    normalized = retrieval_benchmark.normalize_embedding_matrix(
        values,
        expected_rows=count,
        expected_dimension=dimension,
        label="persisted_faiss_vectors",
    )
    if not np.allclose(raw, normalized, rtol=0.0, atol=1.0e-6):
        raise CandidateContractError("persisted FAISS vectors are not L2 normalized")
    return raw


def _load_cell_candidate(
    cell_dir: Path,
    *,
    expected_cell_id: str,
    expected_embedding_digest: str,
    expected_dimension: int,
    expected_topology_digest: str,
    faiss_module: Any,
) -> dict[str, Any]:
    if _is_link_like(cell_dir) or not cell_dir.is_dir():
        raise CandidateContractError("candidate cell directory is missing or linked")
    entries = list(cell_dir.iterdir())
    if {entry.name for entry in entries} != {
        "index.faiss",
        "rows.jsonl",
        "vectors.f32",
        "manifest.json",
    }:
        raise CandidateContractError("candidate cell is incomplete or has extra artifacts")
    manifest = _read_canonical_json(
        cell_dir / "manifest.json", _CELL_MANIFEST_KEYS, "candidate cell manifest"
    )
    if manifest["schema_version"] != CELL_SCHEMA:
        raise CandidateContractError("unsupported candidate cell schema")
    if manifest["cell_id"] != expected_cell_id:
        raise CandidateContractError("candidate cell binding mismatch")
    if not _CELL_COMMIT_ID.fullmatch(str(manifest["cell_commit_id"])):
        raise CandidateContractError("candidate cell commit id is invalid")
    expected_commit = CELL_PREFIX + sha256_digest(_cell_identity(manifest)).split(":", 1)[1]
    if manifest["cell_commit_id"] != expected_commit:
        raise CandidateContractError("candidate cell content address mismatch")
    if manifest["embedding_contract_digest"] != expected_embedding_digest:
        raise CandidateContractError("candidate embedding binding mismatch")
    if manifest["topology_digest"] != expected_topology_digest:
        raise CandidateContractError("candidate topology binding mismatch")
    if manifest["index_kind"] != "faiss.IndexFlatIP" or manifest["metric"] != "cosine_ip":
        raise CandidateContractError("candidate index kind or metric mismatch")
    if (
        type(manifest["source_projection_commit_id"]) is not str
        or not _PROJECTION_COMMIT_ID.fullmatch(manifest["source_projection_commit_id"])
        or type(manifest["source_projection_manifest_sha256"]) is not str
        or not _FULL_DIGEST.fullmatch(manifest["source_projection_manifest_sha256"])
    ):
        raise CandidateContractError("candidate source projection binding is invalid")
    dimension = manifest["dimension"]
    count = manifest["vector_count"]
    if type(dimension) is not int or dimension <= 0 or type(count) is not int or count < 0:
        raise CandidateContractError("candidate dimension or count is invalid")
    if dimension != expected_dimension:
        raise CandidateContractError("candidate cell dimension does not match embedding contract")
    for key in ("projection_digests", "source_identity_digests"):
        values = manifest[key]
        if (
            type(values) is not list
            or len(values) != count
            or any(type(value) is not str or not _FULL_DIGEST.fullmatch(value) for value in values)
        ):
            raise CandidateContractError(f"candidate {key} is invalid")
    if (
        type(manifest["receipt_bound_count"]) is not int
        or type(manifest["unreceipted_count"]) is not int
        or manifest["receipt_bound_count"] < 0
        or manifest["unreceipted_count"] < 0
    ):
        raise CandidateContractError("candidate receipt counts are invalid")
    observed_faiss_identity = _faiss_identity(faiss_module)
    if any(manifest[key] != value for key, value in observed_faiss_identity.items()):
        raise CandidateContractError("candidate cell FAISS build identity mismatch")
    rows_path = cell_dir / "rows.jsonl"
    vectors_path = cell_dir / "vectors.f32"
    index_path = cell_dir / "index.faiss"
    rows = _read_rows(rows_path, count)
    _require_regular_single_link_file(vectors_path, "candidate vectors")
    _require_regular_single_link_file(index_path, "candidate FAISS index")
    vectors_bytes = vectors_path.read_bytes()
    if len(vectors_bytes) != count * dimension * np.dtype("<f4").itemsize:
        raise CandidateContractError("candidate vector byte length mismatch")
    vectors = np.frombuffer(vectors_bytes, dtype="<f4").reshape(count, dimension).copy()
    normalized = retrieval_benchmark.normalize_embedding_matrix(
        vectors,
        expected_rows=count,
        expected_dimension=dimension,
        label="persisted_candidate_vectors",
    )
    if not np.allclose(vectors, normalized, rtol=0.0, atol=1.0e-6):
        raise CandidateContractError("persisted candidate vectors are not L2 normalized")
    if manifest["rows_sha256"] != _sha256_bytes(rows_path.read_bytes()):
        raise CandidateContractError("candidate rows checksum mismatch")
    if manifest["vectors_sha256"] != _sha256_bytes(vectors_bytes):
        raise CandidateContractError("candidate vectors checksum mismatch")
    if manifest["index_sha256"] != _sha256_bytes(index_path.read_bytes()):
        raise CandidateContractError("candidate index checksum mismatch")
    if manifest["projection_digests"] != [row["projection_digest"] for row in rows]:
        raise CandidateContractError("candidate projection digest ordering mismatch")
    if manifest["source_identity_digests"] != [
        row["source_identity_digest"] for row in rows
    ]:
        raise CandidateContractError("candidate source identity ordering mismatch")
    receipt_count = sum(row["receipt_bound"] for row in rows)
    if (
        manifest["receipt_bound_count"] != receipt_count
        or manifest["unreceipted_count"] != count - receipt_count
    ):
        raise CandidateContractError("candidate receipt accounting mismatch")
    try:
        index = faiss_module.read_index(str(index_path))
    except Exception as exc:
        raise CandidateContractError("persisted FAISS index read failed") from exc
    _validate_index_metadata(
        index, dimension=dimension, vector_count=count, faiss_module=faiss_module
    )
    reconstructed = _reconstruct_index(index, count, dimension)
    if not np.array_equal(reconstructed, vectors):
        raise CandidateContractError("persisted FAISS vectors do not match bound vector bytes")
    return {"manifest": manifest, "rows": rows, "vectors": vectors, "index": index}


def _search_all_cells(
    loaded_cells: Sequence[Mapping[str, Any]], query: np.ndarray
) -> tuple[list[str], dict[str, float]]:
    candidates: list[tuple[float, str]] = []
    score_by_solver: dict[str, float] = {}
    for cell in loaded_cells:
        rows = cell["rows"]
        count = len(rows)
        if count == 0:
            continue
        try:
            scores, indices = cell["index"].search(
                np.ascontiguousarray(query.reshape(1, -1), dtype=np.float32), count
            )
        except Exception as exc:
            raise CandidateContractError("persisted FAISS search failed") from exc
        if scores.shape != (1, count) or indices.shape != (1, count):
            raise CandidateContractError("persisted FAISS search shape mismatch")
        observed = [int(index) for index in indices[0]]
        if sorted(observed) != list(range(count)) or not np.isfinite(scores).all():
            raise CandidateContractError("persisted FAISS search returned invalid rows")
        for score, row_index in zip(scores[0], observed):
            solver_id = rows[row_index]["canonical_solver_id"]
            value = float(score)
            if solver_id in score_by_solver:
                raise CandidateContractError("persisted search returned a duplicate solver")
            score_by_solver[solver_id] = value
            candidates.append((value, solver_id))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return [solver_id for _score, solver_id in candidates], score_by_solver


def search_verified_candidate(
    verified: Mapping[str, Any], query_vector: np.ndarray, *, k: int = 5
) -> list[dict[str, Any]]:
    """Search every physical cell and deterministically merge a global top-k.

    The caller must supply the result of :func:`load_verified_candidate_snapshot`.
    Each cell is searched for only its local top-k.  If FAISS reports a tie at a
    local cutoff, that cell is searched fully so solver-id tie breaking cannot
    silently discard an equally scored candidate.
    """
    if type(k) is not int or k <= 0:
        raise CandidateContractError("candidate search k must be a positive integer")
    if type(verified) is not dict or frozenset(verified) not in {
        frozenset({"manifest", "cells", "snapshot_dir"}),
        frozenset({"manifest", "cells", "snapshot_dir", "source_commits_reverified"}),
    }:
        raise CandidateContractError("candidate search input is not a verified snapshot")
    manifest = verified["manifest"]
    loaded_cells = verified["cells"]
    if type(manifest) is not dict or type(loaded_cells) is not list:
        raise CandidateContractError("candidate search input is invalid")
    dimension = manifest.get("embedding_contract", {}).get("dimension")
    if type(dimension) is not int or dimension <= 0:
        raise CandidateContractError("candidate search dimension is invalid")
    raw_query = np.asarray(query_vector, dtype=np.float32)
    if raw_query.shape != (dimension,):
        raise CandidateContractError("candidate query vector dimension mismatch")
    normalized = retrieval_benchmark.normalize_embedding_matrix(
        raw_query.reshape(1, -1),
        expected_rows=1,
        expected_dimension=dimension,
        label="candidate_query_embedding",
    )[0]

    candidates: list[tuple[float, str, str, dict[str, Any]]] = []
    for cell in loaded_cells:
        if type(cell) is not dict or type(cell.get("rows")) is not list:
            raise CandidateContractError("candidate search cell is invalid")
        rows = cell["rows"]
        count = len(rows)
        if count == 0:
            continue
        requested = min(k + 1, count)

        def search(count_to_return: int) -> tuple[np.ndarray, np.ndarray]:
            try:
                scores, indices = cell["index"].search(
                    np.ascontiguousarray(normalized.reshape(1, -1), dtype=np.float32),
                    count_to_return,
                )
            except Exception as exc:
                raise CandidateContractError("persisted FAISS search failed") from exc
            if scores.shape != (1, count_to_return) or indices.shape != (
                1,
                count_to_return,
            ):
                raise CandidateContractError("persisted FAISS search shape mismatch")
            observed = [int(index) for index in indices[0]]
            if (
                len(observed) != len(set(observed))
                or any(index < 0 or index >= count for index in observed)
                or not np.isfinite(scores).all()
                or any(
                    float(scores[0, index]) < float(scores[0, index + 1])
                    for index in range(count_to_return - 1)
                )
            ):
                raise CandidateContractError("persisted FAISS search returned invalid rows")
            return scores, indices

        scores, indices = search(requested)
        local_limit = min(k, requested)
        if requested == k + 1 and scores[0, k - 1] == scores[0, k]:
            scores, indices = search(count)
            cutoff = float(scores[0, k - 1])
            local_limit = sum(float(score) >= cutoff for score in scores[0])
        cell_id = cell.get("manifest", {}).get("cell_id")
        if type(cell_id) is not str:
            raise CandidateContractError("candidate search cell binding is invalid")
        for score, raw_index in zip(
            scores[0, :local_limit], indices[0, :local_limit]
        ):
            row = rows[int(raw_index)]
            solver_id = row["canonical_solver_id"]
            candidates.append((float(score), solver_id, cell_id, row))

    candidates.sort(key=lambda item: (-item[0], item[1]))
    return [
        {
            "canonical_solver_id": solver_id,
            "cell_id": cell_id,
            "projection_id": row["projection_id"],
            "receipt_bound": row["receipt_bound"],
            "score": score,
        }
        for score, solver_id, cell_id, row in candidates[:k]
    ]


def verify_persisted_parity(loaded_cells: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [row for cell in loaded_cells for row in cell["rows"]]
    vectors = np.concatenate([cell["vectors"] for cell in loaded_cells], axis=0)
    solver_ids = [row["canonical_solver_id"] for row in rows]
    if len(solver_ids) != len(set(solver_ids)) or not solver_ids:
        raise CandidateContractError("persisted candidate solvers are empty or duplicated")
    dimension = vectors.shape[1]
    rng = np.random.default_rng(20260810)
    random_values = rng.standard_normal((8, dimension), dtype=np.float32)
    random_probes = retrieval_benchmark.normalize_embedding_matrix(
        random_values,
        expected_rows=8,
        expected_dimension=dimension,
        label="candidate_random_parity_probes",
    )
    probes = np.concatenate([vectors, random_probes], axis=0)
    ks = sorted({1, min(5, len(solver_ids)), len(solver_ids)})
    max_error = 0.0
    for query in probes:
        faiss_order, faiss_scores = _search_all_cells(loaded_cells, query)
        numpy_values = np.asarray(vectors @ query, dtype=np.float32)
        numpy_scores = {
            solver_id: float(numpy_values[index])
            for index, solver_id in enumerate(solver_ids)
        }
        numpy_order = sorted(solver_ids, key=lambda solver_id: (-numpy_scores[solver_id], solver_id))
        if set(faiss_scores) != set(numpy_scores):
            raise CandidateContractError("persisted FAISS/NumPy solver set mismatch")
        error = max(
            abs(faiss_scores[solver_id] - numpy_scores[solver_id])
            for solver_id in solver_ids
        )
        max_error = max(max_error, error)
        if error > 1.0e-6:
            raise CandidateContractError("persisted FAISS/NumPy score mismatch")
        for k in ks:
            if faiss_order[:k] != numpy_order[:k]:
                raise CandidateContractError("persisted FAISS/NumPy ranking mismatch")
    return {
        "document_probe_count": len(vectors),
        "random_probe_count": len(random_probes),
        "probe_count": len(probes),
        "rankings_match_at_k": ks,
        "max_abs_score_error": round(max_error, 9),
        "score_tolerance": 1.0e-6,
        "global_all_cell_merge": True,
        "exact_rankings_match": True,
    }


def _verify_snapshot_sources(
    manifest: Mapping[str, Any],
    loaded_cells: Sequence[Mapping[str, Any]],
    request: CandidateRequest,
) -> None:
    if (
        manifest["embedding_contract"] != request.embedding_contract
        or manifest["topology_contract"] != request.topology_contract
        or manifest["topology_digest"] != request.topology_digest
    ):
        raise CandidateContractError("candidate snapshot does not match source request")
    sources = _load_source_cells(request)
    source_by_cell = {source["cell_id"]: source for source in sources}
    loaded_by_cell = {
        cell["manifest"]["cell_id"]: cell for cell in loaded_cells
    }
    if set(source_by_cell) != set(loaded_by_cell):
        raise CandidateContractError("candidate snapshot source cell set mismatch")
    for cell_id, source in source_by_cell.items():
        loaded = loaded_by_cell[cell_id]
        cell_manifest = loaded["manifest"]
        expected_rows = [
            _build_row(index, row) for index, row in enumerate(source["rows"])
        ]
        if (
            cell_manifest["source_projection_commit_id"] != source["commit_id"]
            or cell_manifest["source_projection_manifest_sha256"]
            != source["projection_manifest_sha256"]
            or loaded["rows"] != expected_rows
        ):
            raise CandidateContractError("candidate snapshot source binding mismatch")


def load_verified_candidate_snapshot(
    snapshot_dir: Path | str,
    *,
    faiss_module: Any | None = None,
    expected_snapshot_id: str | None = None,
    expected_request: CandidateRequest | None = None,
    enforce_directory_name: bool = True,
) -> dict[str, Any]:
    faiss_module = faiss_module or _import_faiss()
    root = Path(snapshot_dir)
    if _is_link_like(root) or not root.is_dir():
        raise CandidateContractError("candidate snapshot directory is missing or linked")
    entries = list(root.iterdir())
    if {entry.name for entry in entries} != {"manifest.json", "cells"}:
        raise CandidateContractError("candidate snapshot is incomplete or has extra artifacts")
    cells_dir = root / "cells"
    if _is_link_like(cells_dir) or not cells_dir.is_dir():
        raise CandidateContractError("candidate cells directory is missing or linked")
    manifest = _read_canonical_json(
        root / "manifest.json", _SNAPSHOT_MANIFEST_KEYS, "candidate snapshot manifest"
    )
    if manifest["schema_version"] != SNAPSHOT_SCHEMA:
        raise CandidateContractError("unsupported candidate snapshot schema")
    snapshot_id = manifest["snapshot_id"]
    if type(snapshot_id) is not str or not _SNAPSHOT_ID.fullmatch(snapshot_id):
        raise CandidateContractError("candidate snapshot id is invalid")
    derived_id = SNAPSHOT_PREFIX + sha256_digest(_snapshot_identity(manifest)).split(":", 1)[1]
    if snapshot_id != derived_id:
        raise CandidateContractError("candidate snapshot content address mismatch")
    if expected_snapshot_id is not None and snapshot_id != expected_snapshot_id:
        raise CandidateContractError("candidate snapshot id does not match expected id")
    if enforce_directory_name and root.name != snapshot_id:
        raise CandidateContractError("candidate snapshot directory name does not match id")
    if (
        manifest["scope"] != "candidate_only_no_runtime_authority"
        or manifest["runtime_authority_ready"] is not False
        or manifest["cell_local_routing_evaluated"] is not False
        or manifest["chromosome_count"] is not None
        or manifest["chromosome_coverage_evaluated"] is not False
        or manifest["gene_bank_ready"] is not False
    ):
        raise CandidateContractError("candidate authority or genome posture is invalid")
    embedding = vector_projection.validate_embedding_contract(manifest["embedding_contract"])
    provider_identity = _require_exact_keys(
        manifest["embedding_provider_identity"],
        _PROVIDER_IDENTITY_KEYS,
        "candidate embedding provider identity",
    )
    version_prefix = "ollama-catalog-sha256:"
    if not embedding["model_version"].startswith(version_prefix):
        raise CandidateContractError("candidate embedding model version is not catalog-bound")
    expected_catalog_digest = embedding["model_version"][len(version_prefix) :]
    if not re.fullmatch(r"[0-9a-f]{64}", expected_catalog_digest):
        raise CandidateContractError("candidate embedding catalog digest is invalid")
    if (
        not isinstance(provider_identity["provider"], str)
        or not provider_identity["provider"]
        or provider_identity["requested_model_tag"] != embedding["model_id"]
        or provider_identity["catalog_digest"] != expected_catalog_digest
        or provider_identity["catalog_contract_verified_before_embedding"] is not True
        or provider_identity["catalog_contract_verified_after_embedding"] is not True
        or provider_identity["response_digest_attested"] is not False
    ):
        raise CandidateContractError("candidate embedding provider identity is invalid")
    topology = vector_projection.validate_retrieval_topology_contract(
        manifest["topology_contract"]
    )
    topology_digest = vector_projection.retrieval_topology_digest(topology)
    if manifest["topology_digest"] != topology_digest:
        raise CandidateContractError("candidate topology digest mismatch")
    observed_faiss_identity = _faiss_identity(faiss_module)
    if any(manifest[key] != value for key, value in observed_faiss_identity.items()):
        raise CandidateContractError("candidate FAISS build identity mismatch")
    raw_cells = manifest["cells"]
    if type(raw_cells) is not list or not raw_cells:
        raise CandidateContractError("candidate snapshot cells are invalid")
    loaded_cells: list[dict[str, Any]] = []
    seen_cells: set[str] = set()
    expected_dirs: set[str] = set()
    for raw_cell in raw_cells:
        summary = _require_exact_keys(raw_cell, _SNAPSHOT_CELL_KEYS, "candidate cell summary")
        cell_id = vector_projection.validate_vector_cell_id(summary["cell_id"])
        if type(summary["vector_count"]) is not int or summary["vector_count"] < 0:
            raise CandidateContractError("candidate cell summary count is invalid")
        if cell_id in seen_cells:
            raise CandidateContractError("candidate snapshot contains duplicate cells")
        seen_cells.add(cell_id)
        expected_dirs.add(cell_id)
        loaded = _load_cell_candidate(
            cells_dir / cell_id,
            expected_cell_id=cell_id,
            expected_embedding_digest=embedding["contract_digest"],
            expected_dimension=embedding["dimension"],
            expected_topology_digest=topology_digest,
            faiss_module=faiss_module,
        )
        cell_manifest = loaded["manifest"]
        if (
            summary["cell_commit_id"] != cell_manifest["cell_commit_id"]
            or summary["source_projection_commit_id"]
            != cell_manifest["source_projection_commit_id"]
            or summary["vector_count"] != cell_manifest["vector_count"]
            or summary["manifest_sha256"]
            != _sha256_bytes((cells_dir / cell_id / "manifest.json").read_bytes())
        ):
            raise CandidateContractError("candidate cell summary binding mismatch")
        loaded_cells.append(loaded)
    actual_dirs = {entry.name for entry in cells_dir.iterdir() if entry.is_dir()}
    if actual_dirs != expected_dirs or any(not entry.is_dir() for entry in cells_dir.iterdir()):
        raise CandidateContractError("candidate cells directory has unexpected entries")
    if [row["cell_id"] for row in raw_cells] != sorted(seen_cells):
        raise CandidateContractError("candidate cell summaries are not sorted")
    live_leaf_cells = {
        row["cell_id"]
        for row in topology["cells"]
        if row["live"] and row["subdivision_state"] == "leaf"
    }
    if seen_cells != live_leaf_cells:
        raise CandidateContractError("candidate snapshot does not cover every live leaf cell")
    projection_ids = [
        row["projection_id"] for loaded in loaded_cells for row in loaded["rows"]
    ]
    if len(projection_ids) != len(set(projection_ids)):
        raise CandidateContractError("candidate snapshot contains duplicate projections")
    total = sum(cell["manifest"]["vector_count"] for cell in loaded_cells)
    receipt_bound = sum(cell["manifest"]["receipt_bound_count"] for cell in loaded_cells)
    for key in ("total_vector_count", "receipt_bound_count", "unreceipted_count"):
        if type(manifest[key]) is not int or manifest[key] < 0:
            raise CandidateContractError("candidate snapshot counts are invalid")
    if (
        manifest["total_vector_count"] != total
        or manifest["receipt_bound_count"] != receipt_bound
        or manifest["unreceipted_count"] != total - receipt_bound
    ):
        raise CandidateContractError("candidate snapshot count accounting mismatch")
    stored_parity = _require_exact_keys(
        manifest["persisted_parity"], _PARITY_KEYS, "candidate persisted parity"
    )
    for key in ("document_probe_count", "random_probe_count", "probe_count"):
        if type(stored_parity[key]) is not int or stored_parity[key] < 0:
            raise CandidateContractError("candidate parity counts are invalid")
    if (
        type(stored_parity["rankings_match_at_k"]) is not list
        or any(type(value) is not int or value <= 0 for value in stored_parity["rankings_match_at_k"])
        or isinstance(stored_parity["max_abs_score_error"], bool)
        or not isinstance(stored_parity["max_abs_score_error"], (int, float))
        or isinstance(stored_parity["score_tolerance"], bool)
        or not isinstance(stored_parity["score_tolerance"], (int, float))
        or stored_parity["global_all_cell_merge"] is not True
        or stored_parity["exact_rankings_match"] is not True
    ):
        raise CandidateContractError("candidate parity evidence is invalid")
    parity = verify_persisted_parity(loaded_cells)
    if stored_parity != parity:
        raise CandidateContractError("candidate persisted parity evidence mismatch")
    if expected_request is not None:
        _verify_snapshot_sources(manifest, loaded_cells, expected_request)
    return {
        "manifest": manifest,
        "cells": loaded_cells,
        "snapshot_dir": root,
        "source_commits_reverified": expected_request is not None,
    }


def _build_row(index: int, source_row: Mapping[str, Any]) -> dict[str, Any]:
    document = source_row["projection_document"]
    identity = source_row["source_identity"]
    return {
        "schema_version": ROW_SCHEMA,
        "row_index": index,
        "canonical_solver_id": document["canonical_solver_id"],
        "projection_id": document["projection_id"],
        "projection_digest": document["projection_digest"],
        "source_digest": document["source_digest"],
        "source_identity_digest": identity["identity_digest"],
        "receipt_bound": identity["receipt_bound"],
    }


def _stage_cell(
    stage_cells: Path,
    source: Mapping[str, Any],
    vectors: np.ndarray,
    *,
    request: CandidateRequest,
    faiss_module: Any,
    faiss_identity: Mapping[str, str],
) -> dict[str, Any]:
    cell_id = source["cell_id"]
    cell_dir = stage_cells / cell_id
    cell_dir.mkdir()
    rows = [_build_row(index, row) for index, row in enumerate(source["rows"])]
    rows_bytes = b"".join(_canonical_json_line(row) for row in rows)
    matrix = np.ascontiguousarray(vectors, dtype="<f4")
    vectors_bytes = matrix.tobytes(order="C")
    _write_fsynced(cell_dir / "rows.jsonl", rows_bytes)
    _write_fsynced(cell_dir / "vectors.f32", vectors_bytes)
    try:
        index = faiss_module.IndexFlatIP(request.embedding_contract["dimension"])
        if len(matrix):
            index.add(matrix)
    except Exception as exc:
        raise CandidateContractError("FAISS IndexFlatIP construction failed") from exc
    _validate_index_metadata(
        index,
        dimension=request.embedding_contract["dimension"],
        vector_count=len(matrix),
        faiss_module=faiss_module,
    )
    _write_index_fsynced(faiss_module, index, cell_dir / "index.faiss")
    receipt_bound = sum(row["receipt_bound"] for row in rows)
    identity = {
        "schema_version": CELL_SCHEMA,
        "cell_id": cell_id,
        "source_projection_commit_id": source["commit_id"],
        "source_projection_manifest_sha256": source["projection_manifest_sha256"],
        "projection_digests": [row["projection_digest"] for row in rows],
        "source_identity_digests": [row["source_identity_digest"] for row in rows],
        "receipt_bound_count": receipt_bound,
        "unreceipted_count": len(rows) - receipt_bound,
        "rows_sha256": _sha256_bytes(rows_bytes),
        "vectors_sha256": _sha256_bytes(vectors_bytes),
        "index_sha256": _sha256_bytes((cell_dir / "index.faiss").read_bytes()),
        "embedding_contract_digest": request.embedding_contract["contract_digest"],
        "topology_digest": request.topology_digest,
        "index_kind": "faiss.IndexFlatIP",
        "metric": "cosine_ip",
        "dimension": request.embedding_contract["dimension"],
        "vector_count": len(rows),
        **faiss_identity,
    }
    cell_commit_id = CELL_PREFIX + sha256_digest(identity).split(":", 1)[1]
    manifest = {**identity, "cell_commit_id": cell_commit_id}
    _write_fsynced(cell_dir / "manifest.json", _canonical_json_line(manifest))
    loaded = _load_cell_candidate(
        cell_dir,
        expected_cell_id=cell_id,
        expected_embedding_digest=request.embedding_contract["contract_digest"],
        expected_dimension=request.embedding_contract["dimension"],
        expected_topology_digest=request.topology_digest,
        faiss_module=faiss_module,
    )
    return loaded


def _directory_digests(path: Path) -> dict[str, str]:
    return {
        item.relative_to(path).as_posix(): _sha256_bytes(item.read_bytes())
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


def _prepare_snapshots_root(output_root: Path, audit_root: Path) -> Path:
    _validate_materialization_root(output_root, audit_root)
    if _is_link_like(output_root):
        raise CandidateContractError("materialization root must not be a link")
    output_root.mkdir(parents=True, exist_ok=True)
    _validate_materialization_root(output_root, audit_root)
    if _is_link_like(output_root) or not output_root.is_dir():
        raise CandidateContractError("materialization root is not a regular directory")
    snapshots_root = output_root / "snapshots"
    if _is_link_like(snapshots_root):
        raise CandidateContractError("candidate snapshots root must not be a link")
    snapshots_root.mkdir(exist_ok=True)
    if (
        _is_link_like(snapshots_root)
        or not snapshots_root.is_dir()
        or snapshots_root.resolve().parent != output_root.resolve()
    ):
        raise CandidateContractError("candidate snapshots root is not contained")
    return snapshots_root


def _remove_stage(stage: Path, snapshots_root: Path) -> None:
    lexical_stage = stage.absolute()
    lexical_root = snapshots_root.absolute()
    if (
        lexical_stage.parent != lexical_root
        or not lexical_stage.name.startswith(".stage-")
    ):
        raise CandidateContractError("refusing to remove a non-candidate staging directory")
    if not os.path.lexists(lexical_stage):
        return
    tombstone = lexical_root / f".discard-{uuid.uuid4().hex}"
    os.replace(lexical_stage, tombstone)
    if _is_link_like(tombstone):
        is_junction = getattr(tombstone, "is_junction", None)
        if callable(is_junction) and is_junction():
            os.rmdir(tombstone)
        else:
            tombstone.unlink()
    elif tombstone.is_dir():
        shutil.rmtree(tombstone)
    else:
        tombstone.unlink()


def materialize_candidate(
    request: CandidateRequest,
    *,
    output_root: Path,
    embedder: Any | None = None,
    faiss_module: Any | None = None,
) -> dict[str, Any]:
    _validate_materialization_root(output_root, request.audit_root)
    sources = _load_source_cells(request)
    faiss_module = faiss_module or _import_faiss()
    faiss_identity = _faiss_identity(faiss_module)
    if embedder is None:
        with retrieval_benchmark.OllamaEmbeddingClient(
            retrieval_benchmark.DEFAULT_OLLAMA_URL
        ) as client:
            matrix, provider_identity = _embed_source_cells(
                sources, request.embedding_contract, client
            )
    else:
        matrix, provider_identity = _embed_source_cells(
            sources, request.embedding_contract, embedder
        )
    snapshots_root = _prepare_snapshots_root(output_root, request.audit_root)
    stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=snapshots_root)).absolute()
    try:
        if (
            _is_link_like(snapshots_root)
            or _is_link_like(stage)
            or stage.parent != snapshots_root.absolute()
            or stage.resolve().parent != snapshots_root.resolve()
            or snapshots_root.resolve().parent != output_root.resolve()
        ):
            raise CandidateContractError("candidate staging directory is not contained")
        stage_cells = stage / "cells"
        stage_cells.mkdir()
        loaded_cells: list[dict[str, Any]] = []
        summaries: list[dict[str, Any]] = []
        offset = 0
        for source in sources:
            count = len(source["rows"])
            loaded = _stage_cell(
                stage_cells,
                source,
                matrix[offset : offset + count],
                request=request,
                faiss_module=faiss_module,
                faiss_identity=faiss_identity,
            )
            offset += count
            loaded_cells.append(loaded)
            manifest = loaded["manifest"]
            summaries.append(
                {
                    "cell_id": source["cell_id"],
                    "cell_commit_id": manifest["cell_commit_id"],
                    "source_projection_commit_id": source["commit_id"],
                    "manifest_sha256": _sha256_bytes(
                        (stage_cells / source["cell_id"] / "manifest.json").read_bytes()
                    ),
                    "vector_count": count,
                }
            )
        if offset != len(matrix):
            raise CandidateContractError("candidate embedding partition is incomplete")
        parity = verify_persisted_parity(loaded_cells)
        receipt_bound = sum(
            loaded["manifest"]["receipt_bound_count"] for loaded in loaded_cells
        )
        identity = {
            "schema_version": SNAPSHOT_SCHEMA,
            "scope": "candidate_only_no_runtime_authority",
            "runtime_authority_ready": False,
            "cell_local_routing_evaluated": False,
            "chromosome_count": None,
            "chromosome_coverage_evaluated": False,
            "gene_bank_ready": False,
            "embedding_contract": request.embedding_contract,
            "embedding_provider_identity": provider_identity,
            "topology_contract": request.topology_contract,
            "topology_digest": request.topology_digest,
            **faiss_identity,
            "total_vector_count": len(matrix),
            "receipt_bound_count": receipt_bound,
            "unreceipted_count": len(matrix) - receipt_bound,
            "cells": sorted(summaries, key=lambda row: row["cell_id"]),
            "persisted_parity": parity,
        }
        snapshot_id = SNAPSHOT_PREFIX + sha256_digest(identity).split(":", 1)[1]
        manifest = {**identity, "snapshot_id": snapshot_id}
        _write_fsynced(stage / "manifest.json", _canonical_json_line(manifest))
        load_verified_candidate_snapshot(
            stage,
            faiss_module=faiss_module,
            expected_snapshot_id=snapshot_id,
            expected_request=request,
            enforce_directory_name=False,
        )
        final = snapshots_root / snapshot_id
        status = "materialized"
        if final.exists():
            load_verified_candidate_snapshot(
                final,
                faiss_module=faiss_module,
                expected_snapshot_id=snapshot_id,
                expected_request=request,
            )
            if _directory_digests(stage) != _directory_digests(final):
                raise CandidateContractError(
                    "existing candidate snapshot conflicts with content address"
                )
            _remove_stage(stage, snapshots_root)
            status = "already_exists"
        else:
            try:
                os.replace(stage, final)
            except OSError:
                # A destination-won directory publication race may surface as
                # WinError 5, EEXIST, or ENOTEMPTY.  Accept any OS error only
                # after proving that a winner published the same immutable bytes.
                if not final.exists():
                    raise
                load_verified_candidate_snapshot(
                    final,
                    faiss_module=faiss_module,
                    expected_snapshot_id=snapshot_id,
                    expected_request=request,
                )
                if _directory_digests(stage) != _directory_digests(final):
                    raise CandidateContractError(
                        "concurrent candidate snapshot conflicts with content address"
                    )
                _remove_stage(stage, snapshots_root)
                status = "already_exists"
        verified = load_verified_candidate_snapshot(
            final,
            faiss_module=faiss_module,
            expected_snapshot_id=snapshot_id,
            expected_request=request,
        )
        return {
            "status": status,
            "snapshot_id": snapshot_id,
            "snapshot_path": final.as_posix(),
            "manifest": verified["manifest"],
        }
    except Exception:
        _remove_stage(stage, snapshots_root)
        raise


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, help="repository-relative candidate request JSON")
    parser.add_argument(
        "--output-root",
        default=".codex-audit/magma-faiss-candidates",
        help="repository-relative directory beneath .codex-audit",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        request = load_candidate_request(args.request)
        output_root = resolve_candidate_root(args.output_root)
        report = materialize_candidate(request, output_root=output_root)
    except CandidateUnavailable as exc:
        print(
            json.dumps(
                {
                    "status": "NOT_AVAILABLE_NOT_RUN",
                    "reason": str(exc),
                    "runtime_authority_ready": False,
                },
                sort_keys=True,
            )
        )
        return 3
    except (CandidateContractError, ValueError, OSError) as exc:
        print(
            json.dumps(
                {"status": "FAILED", "reason": str(exc), "runtime_authority_ready": False},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
