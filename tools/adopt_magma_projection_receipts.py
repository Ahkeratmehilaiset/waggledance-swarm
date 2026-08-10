#!/usr/bin/env python3
"""Adopt unreceipted MAGMA projections into isolated structural receipts.

The source projection commits are immutable and never modified. New projection
commits and a replacement candidate request are published content-addressed
beneath ``.codex-audit``. The receipts are explicitly
``self_certified_structure_only``: no external authenticity, solver outcome,
governance approval, runtime authority, chromosome claim, or gene-bank claim is
created by this tool.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import uuid
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import materialize_magma_faiss_candidate as candidate  # noqa: E402
from tools import vector_indexer  # noqa: E402
from waggledance.core.magma import vector_events, vector_projection  # noqa: E402
from waggledance.core.magma.canonical import (  # noqa: E402
    canonical_json_bytes,
    sha256_digest,
)


ADOPTION_SCHEMA = "magma.faiss.projection_receipt_adoption.v1"
ADOPTION_PREFIX = "receiptadopt_"
_ADOPTION_ID = re.compile(r"^receiptadopt_[0-9a-f]{64}$")
_PROJECTION_COMMIT_ID = re.compile(r"^proj_[0-9a-f]{64}$")
_FULL_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_EVENT_ID = re.compile(r"^evt_[0-9a-f]{16}$")
_CANONICAL_UTC = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)
_ADOPTION_CELL_KEYS = frozenset(
    {
        "cell_id",
        "source_projection_commit_id",
        "target_projection_commit_id",
        "document_count",
        "source_upsert_event_ids",
        "commit_applied_event_id",
    }
)
_ADOPTION_CORE_KEYS = frozenset(
    {
        "schema_version",
        "source_request_digest",
        "admitted_at_utc",
        "receipt_semantics",
        "receipt_context",
        "receipt_context_digest",
        "receipt_authenticity_verified",
        "external_authority_artifacts_verified",
        "solver_outcome_verified",
        "runtime_authority_granted",
        "chromosome_coverage_evaluated",
        "gene_bank_ready",
        "total_projection_count",
        "source_unreceipted_count",
        "target_receipt_bound_count",
        "upsert_event_count",
        "commit_applied_event_count",
        "event_chain_digest",
        "event_log_digest",
        "checkpoint_semantic_digest",
        "checkpoint_digest",
        "cells",
    }
)
_ADOPTION_KEYS = _ADOPTION_CORE_KEYS | frozenset(
    {"adoption_id", "candidate_request_digest"}
)
_CHECKPOINT_KEYS = frozenset(
    {
        "schema_version",
        "global_last_applied_event_id",
        "last_applied_ts",
        "per_cell",
    }
)
_CHECKPOINT_CELL_KEYS = frozenset(
    {"last_applied_event_id", "commit_id", "applied_ts", "vector_count"}
)


def _canonical_json_line(value: Any) -> bytes:
    return canonical_json_bytes(value) + b"\n"


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _write_fsynced(path: Path, payload: bytes) -> None:
    with open(path, "xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _fsync_existing(path: Path) -> None:
    with open(path, "r+b") as stream:
        stream.flush()
        os.fsync(stream.fileno())


def _require_utc_timestamp(
    value: Any,
    *,
    label: str,
    canonical_z: bool,
) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise candidate.CandidateContractError(
            f"{label} must be an ISO-8601 UTC timestamp"
        )
    if canonical_z and _CANONICAL_UTC.fullmatch(value) is None:
        raise candidate.CandidateContractError(
            f"{label} must use canonical RFC3339 UTC Z form"
        )
    try:
        parsed = datetime.fromisoformat(
            value[:-1] + "+00:00" if value.endswith("Z") else value
        )
    except ValueError as exc:
        raise candidate.CandidateContractError(
            f"{label} must be an ISO-8601 UTC timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise candidate.CandidateContractError(
            f"{label} must be an ISO-8601 UTC timestamp"
        )
    return value


def _require_exact_keys(
    value: Any, expected: frozenset[str], label: str
) -> dict[str, Any]:
    if type(value) is not dict or frozenset(value) != expected:
        raise candidate.CandidateContractError(
            f"{label} has an unknown schema shape"
        )
    return value


def _request_payload(
    request: candidate.CandidateRequest,
    *,
    vector_root: Path,
    cells: Sequence[tuple[str, str]],
) -> dict[str, Any]:
    try:
        relative_vector_root = vector_root.resolve().relative_to(
            request.repo_root.resolve()
        )
    except ValueError as exc:
        raise candidate.CandidateContractError(
            "adopted vector root escapes repository root"
        ) from exc
    return {
        "schema_version": candidate.REQUEST_SCHEMA,
        "vector_root": relative_vector_root.as_posix(),
        "embedding_contract": request.embedding_contract,
        "topology_contract": request.topology_contract,
        "cells": [
            {"cell_id": cell_id, "projection_commit_id": commit_id}
            for cell_id, commit_id in cells
        ],
    }


def _source_request_payload(
    request: candidate.CandidateRequest,
) -> dict[str, Any]:
    return _request_payload(
        request,
        vector_root=request.vector_root,
        cells=request.cells,
    )


def _adoption_identity(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value[key] for key in sorted(_ADOPTION_CORE_KEYS)}


def _collect_adoption_events(
    request: candidate.CandidateRequest,
    *,
    admitted_at_utc: str,
) -> tuple[list[vector_events.VectorEvent], list[dict[str, Any]]]:
    events: list[vector_events.VectorEvent] = []
    sources: list[dict[str, Any]] = []
    seen_solvers: set[str] = set()
    seen_projections: set[str] = set()
    for cell_id, source_commit_id in sorted(request.cells):
        source = vector_indexer.load_verified_projection_commit(
            request.vector_root,
            cell_id,
            expected_embedding_contract=request.embedding_contract,
            expected_topology_digest=request.topology_digest,
            commit_id=source_commit_id,
        )
        rows = sorted(
            source["documents"],
            key=lambda row: row["projection_document"]["canonical_solver_id"],
        )
        if (
            not rows
            or source.get("receipt_structure_reverified") is not True
            or source.get("receipt_authenticity_verified") is not False
            or source.get("solver_outcome_verified") is not False
            or source.get("runtime_authority_granted") is not False
            or source.get("receipt_bound_count") != 0
            or source.get("unreceipted_count") != len(rows)
        ):
            raise candidate.CandidateContractError(
                "source projection is not a non-empty unreceipted set"
            )
        cell_event_ids: list[str] = []
        documents: list[dict[str, Any]] = []
        for row in rows:
            document = vector_projection.validate_solver_contract_projection(
                row["projection_document"]
            )
            old_identity = vector_projection.validate_projection_source_binding(
                document,
                row["source_identity"],
                request.embedding_contract,
            )
            if (
                old_identity["schema_version"]
                != vector_projection.PROJECTION_SOURCE_IDENTITY_V1_VERSION
                or old_identity["receipt_bound"] is not False
            ):
                raise candidate.CandidateContractError(
                    "receipt adoption accepts explicitly unreceipted v1 sources only"
                )
            solver_id = document["canonical_solver_id"]
            projection_id = document["projection_id"]
            if solver_id in seen_solvers:
                raise candidate.CandidateContractError(
                    "solver appears in more than one adoption cell"
                )
            if projection_id in seen_projections:
                raise candidate.CandidateContractError(
                    "projection appears in more than one adoption cell"
                )
            seen_solvers.add(solver_id)
            seen_projections.add(projection_id)
            proof = (
                vector_projection.build_self_certified_projection_receipt_proof(
                    document,
                    request.embedding_contract,
                    ts_utc=admitted_at_utc,
                )
            )
            identity = (
                vector_projection.build_receipt_bound_projection_source_identity(
                    document,
                    request.embedding_contract,
                    proof,
                )
            )
            base_event = vector_events.vector_upsert_requested(
                cell_id=cell_id,
                model_id=solver_id,
                signature=document["solver_contract_digest"],
                reason="self_certified_receipt_adoption",
                projection_document=document,
                source_identity=identity,
                embedding_contract=request.embedding_contract,
                topology_digest=request.topology_digest,
                source="receipt_adoption",
            )
            event = replace(base_event, ts=admitted_at_utc)
            events.append(event)
            cell_event_ids.append(event.event_id())
            documents.append(document)
        sources.append(
            {
                "cell_id": cell_id,
                "source_projection_commit_id": source_commit_id,
                "documents": documents,
                "source_upsert_event_ids": cell_event_ids,
            }
        )
    if not events:
        raise candidate.CandidateContractError(
            "receipt adoption source contains no projections"
        )
    return events, sources


def _checkpoint_semantic_payload(
    checkpoint: vector_indexer.Checkpoint,
) -> dict[str, Any]:
    return {
        "schema_version": checkpoint.schema_version,
        "global_last_applied_event_id": (
            checkpoint.global_last_applied_event_id
        ),
        "last_applied_ts": checkpoint.last_applied_ts,
        "per_cell": {
            cell_id: {
                "last_applied_event_id": entry.last_applied_event_id,
                "commit_id": entry.commit_id,
                "applied_ts": entry.applied_ts,
                "vector_count": entry.vector_count,
            }
            for cell_id, entry in sorted(checkpoint.per_cell.items())
        },
    }


def _prepare_adoptions_root(
    output_root: Path, audit_root: Path
) -> Path:
    candidate._validate_materialization_root(output_root, audit_root)
    if candidate._is_link_like(output_root):
        raise candidate.CandidateContractError(
            "receipt adoption root must not be a link"
        )
    output_root.mkdir(parents=True, exist_ok=True)
    candidate._validate_materialization_root(output_root, audit_root)
    adoptions_root = output_root / "adoptions"
    if candidate._is_link_like(adoptions_root):
        raise candidate.CandidateContractError(
            "receipt adoptions directory must not be a link"
        )
    adoptions_root.mkdir(exist_ok=True)
    if (
        candidate._is_link_like(adoptions_root)
        or not adoptions_root.is_dir()
        or adoptions_root.resolve().parent != output_root.resolve()
    ):
        raise candidate.CandidateContractError(
            "receipt adoptions directory is not contained"
        )
    return adoptions_root


def _require_disjoint_roots(output_root: Path, source_vector_root: Path) -> None:
    output = output_root.resolve()
    source = source_vector_root.resolve()
    overlaps = output == source
    if not overlaps:
        try:
            output.relative_to(source)
            overlaps = True
        except ValueError:
            pass
    if not overlaps:
        try:
            source.relative_to(output)
            overlaps = True
        except ValueError:
            pass
    if overlaps:
        raise candidate.CandidateContractError(
            "receipt adoption output must not overlap source vector root"
        )


def _remove_published_adoption(
    final: Path,
    adoptions_root: Path,
    adoption_id: str,
) -> None:
    lexical_final = final.absolute()
    lexical_root = adoptions_root.absolute()
    if (
        lexical_final.parent != lexical_root
        or lexical_final.name != adoption_id
        or _ADOPTION_ID.fullmatch(adoption_id) is None
    ):
        raise candidate.CandidateContractError(
            "refusing to roll back an unrelated adoption"
        )
    if not os.path.lexists(lexical_final):
        return
    tombstone = lexical_root / f".stage-rollback-{uuid.uuid4().hex}"
    os.replace(lexical_final, tombstone)
    candidate._remove_stage(tombstone, lexical_root)


def adopt_projection_receipts(
    request: candidate.CandidateRequest,
    *,
    output_root: Path,
    admitted_at_utc: str,
) -> dict[str, Any]:
    """Publish an event-replayed replacement request with structural receipts."""
    candidate._validate_materialization_root(output_root, request.audit_root)
    _require_disjoint_roots(output_root, request.vector_root)
    admitted_at_utc = _require_utc_timestamp(
        admitted_at_utc,
        label="receipt adoption admitted_at_utc",
        canonical_z=True,
    )
    context = vector_projection.build_projection_receipt_context()
    context_digest = sha256_digest(context)
    input_events, sources = _collect_adoption_events(
        request,
        admitted_at_utc=admitted_at_utc,
    )
    total = len(input_events)
    adoptions_root = _prepare_adoptions_root(output_root, request.audit_root)
    stage = Path(
        tempfile.mkdtemp(prefix=".stage-", dir=adoptions_root)
    ).absolute()
    published_final: Path | None = None
    published_adoption_id: str | None = None
    try:
        if (
            candidate._is_link_like(stage)
            or stage.parent != adoptions_root.absolute()
            or stage.resolve().parent != adoptions_root.resolve()
        ):
            raise candidate.CandidateContractError(
                "receipt adoption staging directory is not contained"
            )
        stage_vector_root = stage / "vector"
        event_log = stage / "events.jsonl"
        checkpoint_path = stage / "checkpoint.json"
        vector_events.emit_many(input_events, event_log)
        _fsync_existing(event_log)
        apply_report = vector_indexer.apply(
            event_log=event_log,
            vector_root=stage_vector_root,
            checkpoint_path=checkpoint_path,
            dry_run=False,
            applied_at_utc=admitted_at_utc,
        )
        if (
            apply_report.events_processed != total
            or apply_report.cells_with_changes != len(sources)
            or apply_report.cells_applied != len(sources)
            or apply_report.cells_failed != 0
            or apply_report.cells_skipped_no_change != 0
            or set(apply_report.cell_results)
            != {source["cell_id"] for source in sources}
        ):
            raise candidate.CandidateContractError(
                "receipt adoption indexer apply did not complete exactly"
            )
        _fsync_existing(event_log)
        _fsync_existing(checkpoint_path)

        all_events = list(vector_events.read_events(event_log, strict=True))
        if (
            len(all_events) != total + len(sources)
            or all_events[:total] != input_events
            or any(
                event.event != vector_events.EVT_VECTOR_COMMIT_APPLIED
                for event in all_events[total:]
            )
        ):
            raise candidate.CandidateContractError(
                "receipt adoption event replay evidence is incomplete"
            )
        checkpoint = vector_indexer.load_checkpoint(checkpoint_path)
        summaries: list[dict[str, Any]] = []
        for source in sources:
            cell_id = source["cell_id"]
            result = apply_report.cell_results[cell_id]
            if (
                result.status != "applied"
                or not isinstance(result.commit_id, str)
                or not _PROJECTION_COMMIT_ID.fullmatch(result.commit_id)
                or not isinstance(result.commit_applied_event_id, str)
                or not _EVENT_ID.fullmatch(result.commit_applied_event_id)
                or result.vector_count != len(source["documents"])
            ):
                raise candidate.CandidateContractError(
                    "receipt adoption indexer result is invalid"
                )
            summaries.append(
                {
                    "cell_id": cell_id,
                    "source_projection_commit_id": source[
                        "source_projection_commit_id"
                    ],
                    "target_projection_commit_id": result.commit_id,
                    "document_count": len(source["documents"]),
                    "source_upsert_event_ids": source[
                        "source_upsert_event_ids"
                    ],
                    "commit_applied_event_id": (
                        result.commit_applied_event_id
                    ),
                }
            )
        summaries.sort(key=lambda row: row["cell_id"])
        core = {
            "schema_version": ADOPTION_SCHEMA,
            "source_request_digest": sha256_digest(
                _source_request_payload(request)
            ),
            "admitted_at_utc": admitted_at_utc,
            "receipt_semantics": "self_certified_structure_only",
            "receipt_context": context,
            "receipt_context_digest": context_digest,
            "receipt_authenticity_verified": False,
            "external_authority_artifacts_verified": False,
            "solver_outcome_verified": False,
            "runtime_authority_granted": False,
            "chromosome_coverage_evaluated": False,
            "gene_bank_ready": False,
            "total_projection_count": total,
            "source_unreceipted_count": total,
            "target_receipt_bound_count": total,
            "upsert_event_count": total,
            "commit_applied_event_count": len(sources),
            "event_chain_digest": sha256_digest(
                [event.event_digest() for event in all_events]
            ),
            "event_log_digest": _sha256_bytes(event_log.read_bytes()),
            "checkpoint_semantic_digest": sha256_digest(
                _checkpoint_semantic_payload(checkpoint)
            ),
            "checkpoint_digest": _sha256_bytes(
                checkpoint_path.read_bytes()
            ),
            "cells": summaries,
        }
        adoption_id = ADOPTION_PREFIX + sha256_digest(core).split(":", 1)[1]
        final = adoptions_root / adoption_id
        target_cells = tuple(
            (row["cell_id"], row["target_projection_commit_id"])
            for row in summaries
        )
        request_payload = _request_payload(
            request,
            vector_root=final / "vector",
            cells=target_cells,
        )
        request_bytes = _canonical_json_line(request_payload)
        manifest = {
            **core,
            "adoption_id": adoption_id,
            "candidate_request_digest": _sha256_bytes(request_bytes),
        }
        _write_fsynced(stage / "candidate-request.json", request_bytes)
        _write_fsynced(
            stage / "manifest.json",
            _canonical_json_line(manifest),
        )

        status = "adopted"
        if final.exists():
            existing = load_verified_receipt_adoption(
                final,
                repo_root=request.repo_root,
                expected_source_request=request,
            )
            if existing["manifest"] != manifest:
                raise candidate.CandidateContractError(
                    "existing receipt adoption conflicts with content address"
                )
            candidate._remove_stage(stage, adoptions_root)
            status = "already_exists"
        else:
            try:
                os.replace(stage, final)
                published_final = final
                published_adoption_id = adoption_id
            except OSError:
                if not final.exists():
                    raise
                existing = load_verified_receipt_adoption(
                    final,
                    repo_root=request.repo_root,
                    expected_source_request=request,
                )
                if existing["manifest"] != manifest:
                    raise candidate.CandidateContractError(
                        "concurrent receipt adoption conflicts with content address"
                    )
                candidate._remove_stage(stage, adoptions_root)
                status = "already_exists"
        verified = load_verified_receipt_adoption(
            final,
            repo_root=request.repo_root,
            expected_source_request=request,
        )
        published_final = None
        published_adoption_id = None
        return {
            "status": status,
            "adoption_id": adoption_id,
            "adoption_path": final.as_posix(),
            "candidate_request_path": (
                final / "candidate-request.json"
            ).as_posix(),
            "manifest": verified["manifest"],
        }
    except Exception as exc:
        if published_final is not None and published_adoption_id is not None:
            try:
                _remove_published_adoption(
                    published_final,
                    adoptions_root,
                    published_adoption_id,
                )
            except Exception as rollback_exc:
                raise candidate.CandidateContractError(
                    "receipt adoption verification failed and rollback failed: "
                    f"{type(rollback_exc).__name__}: {rollback_exc}"
                ) from exc
        if stage.exists():
            candidate._remove_stage(stage, adoptions_root)
        raise


def _read_canonical_object(
    path: Path, expected_keys: frozenset[str], label: str
) -> dict[str, Any]:
    candidate._require_regular_single_link_file(path, label)
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise candidate.CandidateContractError(f"invalid {label}") from exc
    value = _require_exact_keys(value, expected_keys, label)
    if raw != _canonical_json_line(value):
        raise candidate.CandidateContractError(
            f"{label} is not canonically encoded"
        )
    return value


def _read_verified_events(path: Path) -> list[vector_events.VectorEvent]:
    candidate._require_regular_single_link_file(
        path, "receipt adoption event log"
    )
    try:
        events = list(vector_events.read_events(path, strict=True))
    except Exception as exc:
        raise candidate.CandidateContractError(
            "receipt adoption event log is invalid"
        ) from exc
    expected = b"".join(
        event.to_json().encode("utf-8") + b"\n" for event in events
    )
    if path.read_bytes() != expected:
        raise candidate.CandidateContractError(
            "receipt adoption event log is not canonical"
        )
    return events


def _read_verified_checkpoint(
    path: Path,
) -> vector_indexer.Checkpoint:
    candidate._require_regular_single_link_file(
        path, "receipt adoption checkpoint"
    )
    try:
        raw = json.loads(path.read_text("utf-8"))
    except Exception as exc:
        raise candidate.CandidateContractError(
            "receipt adoption checkpoint is invalid"
        ) from exc
    raw = _require_exact_keys(
        raw, _CHECKPOINT_KEYS, "receipt adoption checkpoint"
    )
    per_cell = raw["per_cell"]
    if type(per_cell) is not dict:
        raise candidate.CandidateContractError(
            "receipt adoption checkpoint cells are invalid"
        )
    for cell_id, entry in per_cell.items():
        vector_projection.validate_vector_cell_id(cell_id)
        _require_exact_keys(
            entry,
            _CHECKPOINT_CELL_KEYS,
            f"receipt adoption checkpoint cell {cell_id}",
        )
    try:
        return vector_indexer.load_checkpoint(path)
    except Exception as exc:
        raise candidate.CandidateContractError(
            "receipt adoption checkpoint contract is invalid"
        ) from exc


def load_verified_receipt_adoption(
    adoption_dir: Path | str,
    *,
    repo_root: Path = ROOT,
    expected_source_request: candidate.CandidateRequest | None = None,
) -> dict[str, Any]:
    root = Path(adoption_dir)
    if candidate._is_link_like(root) or not root.is_dir():
        raise candidate.CandidateContractError(
            "receipt adoption directory is missing or linked"
        )
    try:
        root.resolve().relative_to((repo_root / ".codex-audit").resolve())
    except ValueError as exc:
        raise candidate.CandidateContractError(
            "receipt adoption escapes .codex-audit"
        ) from exc
    entries = list(root.iterdir())
    if {entry.name for entry in entries} != {
        "manifest.json",
        "candidate-request.json",
        "events.jsonl",
        "checkpoint.json",
        "vector",
    }:
        raise candidate.CandidateContractError(
            "receipt adoption is incomplete or has extra artifacts"
        )
    vector_root = root / "vector"
    if candidate._is_link_like(vector_root) or not vector_root.is_dir():
        raise candidate.CandidateContractError(
            "receipt adoption vector root is missing or linked"
        )
    manifest = _read_canonical_object(
        root / "manifest.json", _ADOPTION_KEYS, "receipt adoption manifest"
    )
    if manifest["schema_version"] != ADOPTION_SCHEMA:
        raise candidate.CandidateContractError(
            "unsupported receipt adoption schema"
        )
    adoption_id = manifest["adoption_id"]
    if (
        type(adoption_id) is not str
        or not _ADOPTION_ID.fullmatch(adoption_id)
        or root.name != adoption_id
        or adoption_id
        != ADOPTION_PREFIX
        + sha256_digest(_adoption_identity(manifest)).split(":", 1)[1]
    ):
        raise candidate.CandidateContractError(
            "receipt adoption content address mismatch"
        )
    admitted_at_utc = _require_utc_timestamp(
        manifest["admitted_at_utc"],
        label="receipt adoption admitted_at_utc",
        canonical_z=True,
    )
    context = vector_projection.validate_projection_receipt_context(
        manifest["receipt_context"]
    )
    if (
        manifest["receipt_context_digest"] != sha256_digest(context)
        or manifest["receipt_semantics"]
        != "self_certified_structure_only"
        or manifest["receipt_authenticity_verified"] is not False
        or manifest["external_authority_artifacts_verified"] is not False
        or manifest["solver_outcome_verified"] is not False
        or manifest["runtime_authority_granted"] is not False
        or manifest["chromosome_coverage_evaluated"] is not False
        or manifest["gene_bank_ready"] is not False
    ):
        raise candidate.CandidateContractError(
            "receipt adoption authority posture is invalid"
        )
    if (
        type(manifest["source_request_digest"]) is not str
        or not _FULL_DIGEST.fullmatch(manifest["source_request_digest"])
        or type(manifest["event_chain_digest"]) is not str
        or not _FULL_DIGEST.fullmatch(manifest["event_chain_digest"])
        or type(manifest["event_log_digest"]) is not str
        or not _FULL_DIGEST.fullmatch(manifest["event_log_digest"])
        or type(manifest["checkpoint_semantic_digest"]) is not str
        or not _FULL_DIGEST.fullmatch(
            manifest["checkpoint_semantic_digest"]
        )
        or type(manifest["checkpoint_digest"]) is not str
        or not _FULL_DIGEST.fullmatch(manifest["checkpoint_digest"])
    ):
        raise candidate.CandidateContractError(
            "receipt adoption evidence digest is invalid"
        )

    request_path = root / "candidate-request.json"
    candidate._require_regular_single_link_file(
        request_path, "receipt adoption candidate request"
    )
    request_bytes = request_path.read_bytes()
    if manifest["candidate_request_digest"] != _sha256_bytes(request_bytes):
        raise candidate.CandidateContractError(
            "receipt adoption candidate request digest mismatch"
        )
    try:
        request_value = json.loads(request_bytes.decode("utf-8"))
    except Exception as exc:
        raise candidate.CandidateContractError(
            "receipt adoption candidate request is invalid"
        ) from exc
    if request_bytes != _canonical_json_line(request_value):
        raise candidate.CandidateContractError(
            "receipt adoption candidate request is not canonically encoded"
        )
    adopted_request = candidate.load_candidate_request(
        request_path,
        repo_root=repo_root,
    )
    if adopted_request.vector_root != vector_root.resolve():
        raise candidate.CandidateContractError(
            "receipt adoption candidate request points to another vector root"
        )

    raw_cells = manifest["cells"]
    if type(raw_cells) is not list or not raw_cells:
        raise candidate.CandidateContractError(
            "receipt adoption cells are invalid"
        )
    summaries: list[dict[str, Any]] = []
    total = 0
    target_pairs: list[tuple[str, str]] = []
    all_source_event_ids: list[str] = []
    for index, raw_cell in enumerate(raw_cells):
        summary = _require_exact_keys(
            raw_cell,
            _ADOPTION_CELL_KEYS,
            f"receipt adoption cells[{index}]",
        )
        cell_id = vector_projection.validate_vector_cell_id(summary["cell_id"])
        source_commit = summary["source_projection_commit_id"]
        target_commit = summary["target_projection_commit_id"]
        count = summary["document_count"]
        source_event_ids = summary["source_upsert_event_ids"]
        commit_event_id = summary["commit_applied_event_id"]
        if (
            type(source_commit) is not str
            or not _PROJECTION_COMMIT_ID.fullmatch(source_commit)
            or type(target_commit) is not str
            or not _PROJECTION_COMMIT_ID.fullmatch(target_commit)
            or type(count) is not int
            or count <= 0
            or type(source_event_ids) is not list
            or len(source_event_ids) != count
            or any(
                type(event_id) is not str
                or not _EVENT_ID.fullmatch(event_id)
                for event_id in source_event_ids
            )
            or type(commit_event_id) is not str
            or not _EVENT_ID.fullmatch(commit_event_id)
        ):
            raise candidate.CandidateContractError(
                "receipt adoption cell summary is invalid"
            )
        summaries.append(dict(summary))
        target_pairs.append((cell_id, target_commit))
        all_source_event_ids.extend(source_event_ids)
        total += count
    if summaries != sorted(summaries, key=lambda row: row["cell_id"]):
        raise candidate.CandidateContractError(
            "receipt adoption cells are not sorted"
        )
    if (
        len({row["cell_id"] for row in summaries}) != len(summaries)
        or len(set(all_source_event_ids)) != len(all_source_event_ids)
        or len(
            {row["commit_applied_event_id"] for row in summaries}
        )
        != len(summaries)
    ):
        raise candidate.CandidateContractError(
            "receipt adoption event or cell identity is duplicated"
        )
    if tuple(target_pairs) != adopted_request.cells:
        raise candidate.CandidateContractError(
            "receipt adoption candidate request cell mapping mismatch"
        )
    numeric_claims = (
        manifest["total_projection_count"],
        manifest["source_unreceipted_count"],
        manifest["target_receipt_bound_count"],
        manifest["upsert_event_count"],
        manifest["commit_applied_event_count"],
    )
    if (
        any(type(value) is not int or value < 0 for value in numeric_claims)
        or manifest["total_projection_count"] != total
        or manifest["source_unreceipted_count"] != total
        or manifest["target_receipt_bound_count"] != total
        or manifest["upsert_event_count"] != total
        or manifest["commit_applied_event_count"] != len(summaries)
    ):
        raise candidate.CandidateContractError(
            "receipt adoption count accounting mismatch"
        )

    events = _read_verified_events(root / "events.jsonl")
    if len(events) != total + len(summaries):
        raise candidate.CandidateContractError(
            "receipt adoption event count mismatch"
        )
    upsert_events = events[:total]
    commit_events = events[total:]
    if (
        [event.event_id() for event in upsert_events]
        != all_source_event_ids
        or any(
            event.event != vector_events.EVT_VECTOR_UPSERT_REQUESTED
            or event.source != "receipt_adoption"
            or event.ts != admitted_at_utc
            or event.payload.get("reason")
            != "self_certified_receipt_adoption"
            for event in upsert_events
        )
        or any(
            event.event != vector_events.EVT_VECTOR_COMMIT_APPLIED
            or event.source != "indexer"
            for event in commit_events
        )
        or [event.cell_id for event in commit_events]
        != [row["cell_id"] for row in summaries]
        or [event.event_id() for event in commit_events]
        != [row["commit_applied_event_id"] for row in summaries]
        or manifest["event_chain_digest"]
        != sha256_digest([event.event_digest() for event in events])
        or manifest["event_log_digest"]
        != _sha256_bytes((root / "events.jsonl").read_bytes())
    ):
        raise candidate.CandidateContractError(
            "receipt adoption event provenance mismatch"
        )
    if any(event.ts != admitted_at_utc for event in commit_events):
        raise candidate.CandidateContractError(
            "receipt adoption commit event timestamp mismatch"
        )

    checkpoint = _read_verified_checkpoint(root / "checkpoint.json")
    if (
        checkpoint.global_last_applied_event_id
        != all_source_event_ids[-1]
        or set(checkpoint.per_cell)
        != {row["cell_id"] for row in summaries}
        or manifest["checkpoint_semantic_digest"]
        != sha256_digest(_checkpoint_semantic_payload(checkpoint))
        or manifest["checkpoint_digest"]
        != _sha256_bytes((root / "checkpoint.json").read_bytes())
        or checkpoint.last_applied_ts != admitted_at_utc
    ):
        raise candidate.CandidateContractError(
            "receipt adoption checkpoint provenance mismatch"
        )
    vector_entries = list(vector_root.iterdir())
    if (
        {entry.name for entry in vector_entries}
        != {row["cell_id"] for row in summaries}
        or any(candidate._is_link_like(entry) or not entry.is_dir() for entry in vector_entries)
    ):
        raise candidate.CandidateContractError(
            "receipt adoption vector cells do not match manifest"
        )
    loaded_targets: dict[str, dict[str, Any]] = {}
    seen_solvers: set[str] = set()
    seen_projections: set[str] = set()
    for summary, commit_event in zip(summaries, commit_events):
        cell_id = summary["cell_id"]
        target_commit = summary["target_projection_commit_id"]
        count = summary["document_count"]
        cell_dir = vector_root / cell_id
        commits_dir = cell_dir / "commits"
        if (
            {entry.name for entry in cell_dir.iterdir()}
            != {"current.json", "commits"}
            or candidate._is_link_like(commits_dir)
            or not commits_dir.is_dir()
            or {entry.name for entry in commits_dir.iterdir()}
            != {target_commit}
        ):
            raise candidate.CandidateContractError(
                "receipt adoption vector cell contains extra artifacts"
            )
        candidate._require_regular_single_link_file(
            cell_dir / "current.json", "receipt adoption vector pointer"
        )
        loaded = vector_indexer.load_verified_projection_commit(
            vector_root,
            cell_id,
            expected_embedding_contract=adopted_request.embedding_contract,
            expected_topology_digest=adopted_request.topology_digest,
        )
        if (
            loaded["commit_id"] != target_commit
            or loaded["pointer"]["commit_id"] != target_commit
            or loaded["pointer"]["previous_commit_id"] is not None
        ):
            raise candidate.CandidateContractError(
                "receipt adoption current pointer mismatch"
            )
        if (
            loaded["manifest"]["document_count"] != count
            or loaded["receipt_bound_count"] != count
            or loaded["unreceipted_count"] != 0
            or loaded["receipt_structure_reverified"] is not True
            or loaded["receipt_authenticity_verified"] is not False
            or loaded["solver_outcome_verified"] is not False
            or loaded["runtime_authority_granted"] is not False
        ):
            raise candidate.CandidateContractError(
                "receipt adoption target proof accounting mismatch"
            )
        observed_versions = sorted(
            {
                row["source_identity"]["schema_version"]
                for row in loaded["documents"]
            }
        )
        if observed_versions != [
            vector_projection.PROJECTION_SOURCE_IDENTITY_V2_VERSION
        ]:
            raise candidate.CandidateContractError(
                "receipt adoption target contains a non-v2 identity"
            )
        cell_upserts = [
            event for event in upsert_events if event.cell_id == cell_id
        ]
        rows = sorted(
            loaded["documents"],
            key=lambda row: row["projection_document"]["canonical_solver_id"],
        )
        if len(cell_upserts) != count or len(rows) != count:
            raise candidate.CandidateContractError(
                "receipt adoption cell event accounting mismatch"
            )
        for row, event in zip(rows, cell_upserts):
            document = row["projection_document"]
            identity = row["source_identity"]
            solver_id = document["canonical_solver_id"]
            projection_id = document["projection_id"]
            if solver_id in seen_solvers or projection_id in seen_projections:
                raise candidate.CandidateContractError(
                    "receipt adoption target duplicates a solver or projection"
                )
            seen_solvers.add(solver_id)
            seen_projections.add(projection_id)
            expected_proof = (
                vector_projection.build_self_certified_projection_receipt_proof(
                    document,
                    adopted_request.embedding_contract,
                    ts_utc=admitted_at_utc,
                )
            )
            expected_identity = (
                vector_projection.build_receipt_bound_projection_source_identity(
                    document,
                    adopted_request.embedding_contract,
                    expected_proof,
                )
            )
            if (
                event.solver_id != solver_id
                or event.payload["projection_document"] != document
                or event.payload["source_identity"] != identity
                or event.payload["embedding_contract"]
                != adopted_request.embedding_contract
                or event.payload["topology_digest"]
                != adopted_request.topology_digest
                or identity["receipt_proof"]["receipt"]["ts_utc"]
                != admitted_at_utc
                or identity["receipt_proof"]["receipt_context"] != context
                or identity != expected_identity
            ):
                raise candidate.CandidateContractError(
                    "receipt adoption event, proof, and target are not cross-bound"
                )
        expected_source_ids = summary["source_upsert_event_ids"]
        commit_dir = commits_dir / target_commit
        payload = commit_event.payload
        if (
            commit_event.solver_id is not None
            or payload.get("faiss_commit_id") != target_commit
            or payload.get("artifact_path")
            != f"vector/{cell_id}/commits/{target_commit}"
            or payload.get("vector_count") != count
            or payload.get("checksum")
            != vector_indexer._checksum_dir(commit_dir)
            or payload.get("source_events") != expected_source_ids
            or payload.get("input_event_range")
            != [expected_source_ids[0], expected_source_ids[-1]]
            or payload.get("materialization_state") != "projection_only"
            or payload.get("index_kind") != "none"
        ):
            raise candidate.CandidateContractError(
                "receipt adoption commit event does not bind its artifact"
            )
        checkpoint_entry = checkpoint.per_cell[cell_id]
        if (
            checkpoint_entry.applied_ts != admitted_at_utc
            or
            checkpoint_entry.last_applied_event_id
            != expected_source_ids[-1]
            or checkpoint_entry.commit_id != target_commit
            or checkpoint_entry.vector_count != count
        ):
            raise candidate.CandidateContractError(
                "receipt adoption checkpoint cell binding mismatch"
            )
        loaded_targets[cell_id] = loaded

    source_reverified = False
    if expected_source_request is not None:
        expected_digest = sha256_digest(
            _source_request_payload(expected_source_request)
        )
        expected_pairs = tuple(
            (row["cell_id"], row["source_projection_commit_id"])
            for row in summaries
        )
        if (
            manifest["source_request_digest"] != expected_digest
            or dict(expected_pairs) != dict(expected_source_request.cells)
            or adopted_request.embedding_contract
            != expected_source_request.embedding_contract
            or adopted_request.topology_contract
            != expected_source_request.topology_contract
            or adopted_request.topology_digest
            != expected_source_request.topology_digest
        ):
            raise candidate.CandidateContractError(
                "receipt adoption source request mismatch"
            )
        for summary in summaries:
            source = vector_indexer.load_verified_projection_commit(
                expected_source_request.vector_root,
                summary["cell_id"],
                expected_embedding_contract=(
                    expected_source_request.embedding_contract
                ),
                expected_topology_digest=(
                    expected_source_request.topology_digest
                ),
                commit_id=summary["source_projection_commit_id"],
            )
            target = loaded_targets[summary["cell_id"]]
            source_rows = source["documents"]
            if (
                source["receipt_bound_count"] != 0
                or source["unreceipted_count"] != len(source_rows)
                or any(
                    row["source_identity"]["schema_version"]
                    != vector_projection.PROJECTION_SOURCE_IDENTITY_V1_VERSION
                    or row["source_identity"]["receipt_bound"] is not False
                    for row in source_rows
                )
            ):
                raise candidate.CandidateContractError(
                    "receipt adoption source is not unreceipted v1"
                )
            source_documents = sorted(
                (row["projection_document"] for row in source_rows),
                key=lambda row: row["canonical_solver_id"],
            )
            target_documents = sorted(
                (
                    row["projection_document"]
                    for row in target["documents"]
                ),
                key=lambda row: row["canonical_solver_id"],
            )
            if source_documents != target_documents:
                raise candidate.CandidateContractError(
                    "receipt adoption changed a projection document"
                )
        source_reverified = True
    return {
        "manifest": manifest,
        "candidate_request": adopted_request,
        "adoption_dir": root.resolve(),
        "source_request_reverified": source_reverified,
        "event_chain_reverified": True,
        "checkpoint_reverified": True,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--request",
        required=True,
        help="repository-relative source candidate request JSON",
    )
    parser.add_argument(
        "--output-root",
        default=".codex-audit/magma-faiss-receipt-adoptions",
        help="repository-relative directory beneath .codex-audit",
    )
    parser.add_argument(
        "--admitted-at-utc",
        required=True,
        help="explicit RFC3339 receipt timestamp for deterministic reruns",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        request = candidate.load_candidate_request(args.request, repo_root=ROOT)
        output_root = candidate.resolve_candidate_root(
            args.output_root,
            repo_root=ROOT,
        )
        report = adopt_projection_receipts(
            request,
            output_root=output_root,
            admitted_at_utc=args.admitted_at_utc,
        )
    except Exception as exc:
        if args.json:
            print(
                json.dumps(
                    {
                        "status": "failed",
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    sort_keys=True,
                )
            )
        else:
            print(f"receipt adoption failed: {type(exc).__name__}: {exc}")
        return 1
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(
            f"{report['status']}: {report['adoption_id']} -> "
            f"{report['candidate_request_path']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
