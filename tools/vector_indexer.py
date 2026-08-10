#!/usr/bin/env python3
"""Vector indexer — Stage 2 writer skeleton with atomic apply.

Reads `data/vector/events.jsonl` (or overridden path), builds per-cell
projections from the event stream, writes staged commit artifacts,
atomically swaps the `current.json` pointer, emits
`vector.commit_applied`, and advances a durable checkpoint.

**Still OFF the runtime read path.** `core/faiss_store` reads the
legacy `data/faiss_staging/` tree. This writer populates
`data/vector/<cell>/commits/<commit_id>/` and a pointer file only; a
separate reviewed commit after the live campaign will repoint
runtime. Strict projected commits are deliberately labeled
`materialization_state=projection_only` and `index_kind=none`: this
module does not create a searchable FAISS index yet.

See `docs/architecture/MAGMA_VECTOR_STAGE2.md` for the full contract.

Invocation:

    python tools/vector_indexer.py                      # dry-run report
    python tools/vector_indexer.py --apply              # perform writes
    python tools/vector_indexer.py --cell thermal --apply
    python tools/vector_indexer.py --since evt_abc --apply
    python tools/vector_indexer.py --json               # machine output

Dry-run is the default. `--apply` is required for any write.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import traceback
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.magma import vector_events  # noqa: E402
from waggledance.core.magma import vector_projection  # noqa: E402
from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest  # noqa: E402


# Default paths. Tests + CLI can override everything.
DEFAULT_VECTOR_ROOT = ROOT / "data" / "vector"
DEFAULT_EVENT_LOG = DEFAULT_VECTOR_ROOT / "events.jsonl"
DEFAULT_CHECKPOINT = DEFAULT_VECTOR_ROOT / "checkpoints" / "vector_indexer.json"

CHECKPOINT_SCHEMA_VERSION = 1

PROJECTION_MANIFEST_V1_SCHEMA = "magma.faiss.projection_manifest.v1"
PROJECTION_MANIFEST_V2_SCHEMA = "magma.faiss.projection_manifest.v2"
# New commits use v2. The reader retains exact v1 support for persisted commits.
PROJECTION_MANIFEST_SCHEMA = PROJECTION_MANIFEST_V2_SCHEMA
PROJECTION_COMMIT_SCHEMA = "magma.faiss.projection_commit.v1"
PROJECTION_POINTER_SCHEMA = "magma.faiss.current_pointer.v1"
_PROJECTION_COMMIT_ID = re.compile(r"^proj_[0-9a-f]{64}$")
_LEGACY_COMMIT_ID = re.compile(r"^faiss_[0-9a-f]{16}$")
_FULL_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_EVENT_ID = re.compile(r"^evt_[0-9a-f]{16}$")
_LEGACY_MANIFEST_KEYS = frozenset(
    {"schema_version", "cell_id", "commit_id", "vector_count", "signatures", "produced_at"}
)
_LEGACY_COMMIT_KEYS = frozenset(
    {
        "schema_version",
        "cell_id",
        "faiss_commit_id",
        "produced_at",
        "vector_count",
        "checksum",
        "input_event_range",
        "source",
        "stage",
    }
)
_PROJECTION_ROW_KEYS = frozenset({"projection_document", "source_identity"})
_PROJECTION_MANIFEST_COMMON_KEYS = frozenset(
    {
        "schema_version",
        "materialization_state",
        "index_kind",
        "cell_id",
        "commit_id",
        "document_count",
        "documents_sha256",
        "documents_bytes",
        "source_identity_digests",
        "processed_source_identities",
        "embedding_contract",
        "topology_digest",
        "projection_schema_version",
    }
)
_PROJECTION_MANIFEST_V1_KEYS = _PROJECTION_MANIFEST_COMMON_KEYS | frozenset(
    {"source_identity_schema_version"}
)
_PROJECTION_MANIFEST_V2_KEYS = _PROJECTION_MANIFEST_COMMON_KEYS | frozenset(
    {"source_identity_schema_versions"}
)
_PROJECTION_COMMIT_KEYS = frozenset(
    {
        "schema_version",
        "materialization_state",
        "cell_id",
        "commit_id",
        "manifest_sha256",
        "manifest_bytes",
        "documents_sha256",
        "documents_bytes",
    }
)
_PROJECTION_POINTER_KEYS = frozenset(
    {"schema_version", "commit_id", "previous_commit_id"}
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate_commit_id(value: Any, *, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or not (
        _LEGACY_COMMIT_ID.fullmatch(value) or _PROJECTION_COMMIT_ID.fullmatch(value)
    ):
        raise ValueError("checkpoint contains an invalid commit_id")
    return value


# ── Per-cell projection (read-only; matches Stage-1 ReplayReport) ──

@dataclass
class CellState:
    cell_id: str
    upsert_requests: int = 0
    delete_requests: int = 0
    committed_count: int = 0
    last_commit_id: str | None = None
    # solver_id → signature (authoritative at the last vector.upsert)
    signatures: dict[str, str] = field(default_factory=dict)
    # Ordered list of event_ids that contributed to this cell's current
    # desired state (used by commit_applied for audit).
    source_event_ids: list[str] = field(default_factory=list)
    first_event_id: str | None = None
    last_event_id: str | None = None
    # Projected mode is additive. Legacy-only cells continue to use the
    # Stage-2 placeholder writer until every active solver has a validated
    # projection document.
    projection_mode: bool = False
    projection_documents: dict[str, dict[str, Any]] = field(default_factory=dict)
    projection_source_identities: dict[str, dict[str, Any]] = field(default_factory=dict)
    projection_embedding_contracts: dict[str, dict[str, Any]] = field(default_factory=dict)
    projection_topology_digests: dict[str, str] = field(default_factory=dict)
    seen_source_identities: dict[str, str] = field(default_factory=dict)
    embedding_contract: dict[str, Any] | None = None
    topology_digest: str | None = None


@dataclass
class ReplayReport:
    """Projection produced by a replay pass. Shape preserved from the
    Stage-1 stub so existing tests keep parsing."""
    events_seen: int = 0
    events_skipped: int = 0
    cells: dict[str, CellState] = field(default_factory=dict)
    unknown_event_types: dict[str, int] = field(default_factory=dict)
    first_event_id: str | None = None
    last_event_id: str | None = None


def _state_for(cells: dict[str, CellState], cell_id: str) -> CellState:
    if cell_id not in cells:
        cells[cell_id] = CellState(cell_id=cell_id)
    return cells[cell_id]


def _apply_event_to_state(cell: CellState, event: vector_events.VectorEvent,
                            event_id: str) -> None:
    """Fold one event into the cell projection. Handles only the
    event types that actually mutate state; informational events
    (solver.upserted) are a no-op for the projection."""
    if event.event == vector_events.EVT_SOLVER_UPSERTED:
        # Informational — ledger write already happened. The
        # corresponding vector.upsert_requested drives state.
        return
    if event.event == vector_events.EVT_VECTOR_UPSERT_REQUESTED:
        cell.upsert_requests += 1
        sig = event.payload.get("signature", "")
        mid = event.payload.get("model_id")
        if mid:
            projected = all(
                key in event.payload
                for key in (
                    "projection_document",
                    "source_identity",
                    "embedding_contract",
                    "topology_digest",
                )
            )
            if projected:
                _apply_projected_upsert(cell, event, event_id)
                return
            if cell.projection_mode:
                raise ValueError(
                    f"projection-less upsert cannot downgrade projected cell {cell.cell_id!r}"
                )
            cell.signatures[mid] = sig
            cell.source_event_ids.append(event_id)
        return
    if event.event == vector_events.EVT_VECTOR_DELETE_REQUESTED:
        cell.delete_requests += 1
        mid = event.payload.get("model_id")
        if mid and mid in cell.signatures:
            del cell.signatures[mid]
            cell.projection_documents.pop(mid, None)
            cell.projection_source_identities.pop(mid, None)
            cell.projection_embedding_contracts.pop(mid, None)
            cell.projection_topology_digests.pop(mid, None)
            cell.source_event_ids.append(event_id)
        return
    if event.event == vector_events.EVT_VECTOR_COMMIT_APPLIED:
        cell.committed_count = int(event.payload.get("vector_count", 0))
        cell.last_commit_id = event.payload.get("faiss_commit_id")
        return


def _apply_projected_upsert(
    cell: CellState,
    event: vector_events.VectorEvent,
    event_id: str,
) -> None:
    payload = event.payload
    document = vector_projection.validate_solver_contract_projection(
        payload["projection_document"]
    )
    embedding = vector_projection.validate_embedding_contract(
        payload["embedding_contract"]
    )
    identity = vector_projection.validate_projection_source_binding(
        document,
        payload["source_identity"],
        embedding,
    )
    topology_digest = payload["topology_digest"]
    model_id = document["canonical_solver_id"]
    dedup_key = _projected_upsert_dedup_key(
        identity,
        embedding,
        topology_digest,
    )
    fingerprint = _projected_upsert_fingerprint(
        document,
        embedding,
        topology_digest,
    )

    previous_fingerprint = cell.seen_source_identities.get(dedup_key)
    if previous_fingerprint is not None:
        if previous_fingerprint != fingerprint:
            raise ValueError(
                "source identity generation conflicts with projected content"
            )
        # A later identical event is normally a replay no-op. After an
        # intervening delete, however, the solver is inactive and the new log
        # position is an explicit reactivation request. Restore its projection
        # without discarding the durable identity history.
        if model_id in cell.signatures:
            return

    cell.projection_mode = True
    cell.signatures[model_id] = document["solver_contract_digest"]
    cell.projection_documents[model_id] = document
    cell.projection_source_identities[model_id] = identity
    cell.projection_embedding_contracts[model_id] = embedding
    cell.projection_topology_digests[model_id] = topology_digest
    cell.seen_source_identities[dedup_key] = fingerprint
    cell.source_event_ids.append(event_id)


def _projected_upsert_dedup_key(
    identity: dict[str, Any],
    embedding_contract: dict[str, Any],
    topology_digest: str,
) -> str:
    """Scope source-identity dedup to one embedding/topology generation."""
    return sha256_digest(
        {
            "source_identity_digest": identity["identity_digest"],
            "embedding_contract_digest": embedding_contract["contract_digest"],
            "topology_digest": topology_digest,
        }
    )


def _projected_upsert_fingerprint(
    document: dict[str, Any],
    embedding_contract: dict[str, Any],
    topology_digest: str,
) -> str:
    """Bind dedup identity to every materialization-affecting contract."""
    return sha256_digest(
        {
            "projection_digest": document["projection_digest"],
            "embedding_contract_digest": embedding_contract["contract_digest"],
            "topology_digest": topology_digest,
        }
    )


def replay(path: Path | str | None = None,
           since_event_id: str | None = None) -> ReplayReport:
    """Walk the event log and build a per-cell projection. If
    `since_event_id` is given, start from the event AFTER that id.

    Unknown event names are counted but do NOT abort replay.
    """
    report = ReplayReport()
    active = since_event_id is None

    for event in vector_events.read_events(path):
        eid = event.event_id()
        if not active:
            if eid == since_event_id:
                active = True
            continue
        if report.first_event_id is None:
            report.first_event_id = eid
        report.last_event_id = eid
        report.events_seen += 1

        cell = _state_for(report.cells, event.cell_id)
        if cell.first_event_id is None:
            cell.first_event_id = eid
        cell.last_event_id = eid

        if event.event in vector_events.ALL_VECTOR_EVENT_NAMES:
            _apply_event_to_state(cell, event, eid)
        else:
            report.unknown_event_types[event.event] = (
                report.unknown_event_types.get(event.event, 0) + 1
            )
            report.events_skipped += 1
    return report


# ── Checkpoint ─────────────────────────────────────────────────────

@dataclass
class PerCellCheckpoint:
    last_applied_event_id: str | None = None
    commit_id: str | None = None
    applied_ts: str | None = None
    vector_count: int = 0


@dataclass
class Checkpoint:
    schema_version: int = CHECKPOINT_SCHEMA_VERSION
    global_last_applied_event_id: str | None = None
    last_applied_ts: str | None = None
    per_cell: dict[str, PerCellCheckpoint] = field(default_factory=dict)

    def cell_entry(self, cell_id: str) -> PerCellCheckpoint:
        if cell_id not in self.per_cell:
            self.per_cell[cell_id] = PerCellCheckpoint()
        return self.per_cell[cell_id]

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "global_last_applied_event_id": self.global_last_applied_event_id,
            "last_applied_ts": self.last_applied_ts,
            "per_cell": {
                k: asdict(v) for k, v in sorted(self.per_cell.items())
            },
        }


def _resolve_checkpoint_path(path: Path | str | None) -> Path:
    if path is not None:
        return Path(path)
    env = os.environ.get("WAGGLE_VECTOR_CHECKPOINT")
    if env:
        return Path(env)
    return DEFAULT_CHECKPOINT


def load_checkpoint(path: Path | str | None = None) -> Checkpoint:
    """Read the checkpoint file. Missing file returns a fresh empty
    checkpoint. Malformed JSON raises — operator must fix."""
    target = _resolve_checkpoint_path(path)
    if not target.exists():
        return Checkpoint()
    with open(target, encoding="utf-8") as f:
        data = json.load(f)
    if type(data) is not dict or data.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
        raise ValueError("unsupported vector indexer checkpoint schema")
    per_cell_data = data.get("per_cell") or {}
    if type(per_cell_data) is not dict:
        raise ValueError("checkpoint per_cell must be an object")
    cp = Checkpoint(
        schema_version=data["schema_version"],
        global_last_applied_event_id=data.get("global_last_applied_event_id"),
        last_applied_ts=data.get("last_applied_ts"),
    )
    for cell_id, entry in per_cell_data.items():
        safe_cell_id = vector_projection.validate_vector_cell_id(cell_id)
        if type(entry) is not dict:
            raise ValueError("checkpoint cell entry must be an object")
        commit_id = _validate_commit_id(entry.get("commit_id"), allow_none=True)
        vector_count = entry.get("vector_count") or 0
        if type(vector_count) is not int or vector_count < 0:
            raise ValueError("checkpoint vector_count must be a non-negative integer")
        cp.per_cell[safe_cell_id] = PerCellCheckpoint(
            last_applied_event_id=entry.get("last_applied_event_id"),
            commit_id=commit_id,
            applied_ts=entry.get("applied_ts"),
            vector_count=vector_count,
        )
    return cp


def save_checkpoint(cp: Checkpoint,
                     path: Path | str | None = None) -> Path:
    """Atomic checkpoint save — write to a temp file in the same dir
    then `os.replace()`. The rename is atomic on both POSIX and
    Windows."""
    target = _resolve_checkpoint_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(cp.to_dict(), indent=2, sort_keys=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", delete=False,
        dir=target.parent, prefix=".checkpoint.", suffix=".tmp",
    ) as tmp:
        tmp.write(payload)
        tmp_path = Path(tmp.name)
    try:
        os.replace(tmp_path, target)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise
    return target


# ── Commit id + content addressing ────────────────────────────────

def compute_commit_id(cell: CellState) -> str:
    """Deterministic sha256 over the cell's canonical projection.

    Same signatures + vector_count → same commit id. This drives the
    idempotency guarantee: a rerun of the same event window produces
    the same commit_id, so the staging path is predictable and
    rewriting it is harmless.
    """
    canonical = {
        "cell_id": cell.cell_id,
        "signatures": dict(sorted(cell.signatures.items())),
        "vector_count": len(cell.signatures),
    }
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return "faiss_" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _checksum_dir(commit_dir: Path) -> str:
    """sha256 over the sorted list of (relative_path, content_sha256)
    pairs. Stable across runs that produce byte-identical content.

    `commit.json` is excluded from the checksum because it CARRIES the
    checksum — including it would create a chicken-and-egg problem
    where the recorded checksum can never match a recomputed one."""
    parts: list[str] = []
    for p in sorted(commit_dir.rglob("*")):
        if not p.is_file():
            continue
        if p.name == "commit.json":
            continue
        rel = p.relative_to(commit_dir).as_posix()
        with open(p, "rb") as f:
            content_sha = hashlib.sha256(f.read()).hexdigest()
        parts.append(f"{rel}:{content_sha}")
    blob = "\n".join(parts).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def _require_regular_single_link_files(
    entries: list[Path],
    label: str,
) -> None:
    for entry in entries:
        try:
            stat_result = entry.stat(follow_symlinks=False)
        except OSError as exc:
            raise ValueError(f"{label} artifact metadata is unavailable") from exc
        if entry.is_symlink() or not entry.is_file() or stat_result.st_nlink != 1:
            raise ValueError(f"{label} artifacts must be regular single-link files")


def _legacy_vectors_bytes(signatures: dict[str, str]) -> bytes:
    lines = [
        json.dumps({"solver_id": model_id, "signature": signature}, sort_keys=True)
        for model_id, signature in sorted(signatures.items())
    ]
    return (("\n".join(lines) + "\n") if lines else "").encode("utf-8")


def _load_verified_legacy_directory(
    commit_dir: Path,
    *,
    expected_cell_id: str,
    expected_commit_id: str,
    expected_signatures: dict[str, str] | None = None,
) -> dict[str, Any]:
    if not commit_dir.is_dir():
        raise ValueError("legacy commit directory is missing")
    entries = list(commit_dir.iterdir())
    expected_names = {"manifest.json", "vectors.jsonl", "commit.json"}
    if {entry.name for entry in entries} != expected_names:
        raise ValueError("legacy commit is incomplete or contains extra artifacts")
    _require_regular_single_link_files(entries, "legacy commit")

    try:
        manifest = json.loads((commit_dir / "manifest.json").read_text("utf-8"))
        commit = json.loads((commit_dir / "commit.json").read_text("utf-8"))
    except Exception as exc:
        raise ValueError("legacy commit metadata is invalid") from exc
    if type(manifest) is not dict or frozenset(manifest) != _LEGACY_MANIFEST_KEYS:
        raise ValueError("legacy manifest has an unknown schema shape")
    if type(commit) is not dict or frozenset(commit) != _LEGACY_COMMIT_KEYS:
        raise ValueError("legacy commit record has an unknown schema shape")
    if manifest["schema_version"] != 1 or commit["schema_version"] != 1:
        raise ValueError("unsupported legacy commit schema")
    if (
        manifest["cell_id"] != expected_cell_id
        or commit["cell_id"] != expected_cell_id
        or manifest["commit_id"] != expected_commit_id
        or commit["faiss_commit_id"] != expected_commit_id
    ):
        raise ValueError("legacy commit binding mismatch")
    if (
        not isinstance(manifest["produced_at"], str)
        or commit["produced_at"] != manifest["produced_at"]
        or commit["source"] != "indexer"
        or commit["stage"] != 2
    ):
        raise ValueError("legacy commit provenance is invalid")

    vector_count = manifest["vector_count"]
    if type(vector_count) is not int or vector_count < 0:
        raise ValueError("legacy vector_count is invalid")
    if type(commit["vector_count"]) is not int or commit["vector_count"] != vector_count:
        raise ValueError("legacy commit vector_count mismatch")
    signatures = manifest["signatures"]
    if type(signatures) is not dict or len(signatures) != vector_count:
        raise ValueError("legacy commit signatures are invalid")
    canonical_signatures: dict[str, str] = {}
    for solver_id, signature in signatures.items():
        safe_solver_id = vector_projection.validate_solver_id(solver_id)
        if not isinstance(signature, str):
            raise ValueError("legacy solver signature must be text")
        canonical_signatures[safe_solver_id] = signature
    if expected_signatures is not None and canonical_signatures != expected_signatures:
        raise ValueError("existing legacy commit conflicts with expected signatures")
    if compute_commit_id(
        CellState(cell_id=expected_cell_id, signatures=canonical_signatures)
    ) != expected_commit_id:
        raise ValueError("legacy commit content address mismatch")
    if (commit_dir / "vectors.jsonl").read_bytes() != _legacy_vectors_bytes(
        canonical_signatures
    ):
        raise ValueError("legacy vectors payload does not match manifest")

    event_range = commit["input_event_range"]
    if event_range is not None and (
        type(event_range) is not list
        or len(event_range) != 2
        or any(
            not isinstance(event_id, str) or _EVENT_ID.fullmatch(event_id) is None
            for event_id in event_range
        )
    ):
        raise ValueError("legacy commit input event range is invalid")
    checksum = commit["checksum"]
    if not isinstance(checksum, str) or _FULL_DIGEST.fullmatch(checksum) is None:
        raise ValueError("legacy commit checksum is invalid")
    if _checksum_dir(commit_dir) != checksum:
        raise ValueError("legacy commit integrity check failed")
    return {
        "commit_dir": commit_dir,
        "manifest": manifest,
        "commit": commit,
        "signatures": canonical_signatures,
        "vector_count": vector_count,
        "checksum": checksum,
    }


def _load_verified_legacy_commit(
    vector_root: Path,
    cell_id: str,
    commit_id: str,
    *,
    expected_signatures: dict[str, str] | None = None,
) -> dict[str, Any]:
    safe_cell = vector_projection.validate_vector_cell_id(cell_id)
    safe_commit = _validate_commit_id(commit_id)
    if not _LEGACY_COMMIT_ID.fullmatch(safe_commit):
        raise ValueError("legacy commit_id is invalid")
    commit_dir = _safe_legacy_commit_dir(vector_root, safe_cell, safe_commit)
    return _load_verified_legacy_directory(
        commit_dir,
        expected_cell_id=safe_cell,
        expected_commit_id=safe_commit,
        expected_signatures=expected_signatures,
    )


# ── Writer: staging → atomic swap ─────────────────────────────────

def _stage_commit(cell: CellState, commit_id: str,
                    vector_root: Path) -> dict:
    """Write `<vector_root>/<cell>/commits/<commit_id>/{manifest, commit, vectors}.

    Idempotent: an existing directory is verified and never rewritten.
    New content is built in an unpredictable same-volume staging directory
    and published only after it passes the complete legacy contract.
    """
    commits_dir = _safe_projection_commits_dir(vector_root, cell.cell_id)
    commits_dir.mkdir(parents=True, exist_ok=True)
    final_dir = _safe_legacy_commit_dir(vector_root, cell.cell_id, commit_id)
    expected_signatures = dict(sorted(cell.signatures.items()))
    if final_dir.exists():
        existing = _load_verified_legacy_commit(
            vector_root,
            cell.cell_id,
            commit_id,
            expected_signatures=expected_signatures,
        )
        return {
            "commit_dir": final_dir,
            "artifact_path": final_dir.relative_to(vector_root.resolve().parent).as_posix(),
            "vector_count": existing["vector_count"],
            "checksum": existing["checksum"],
        }

    stage_dir = Path(tempfile.mkdtemp(prefix=".stage-", dir=commits_dir)).resolve()
    produced_at = _utc_now_iso()
    manifest = {
        "schema_version": 1,
        "cell_id": cell.cell_id,
        "commit_id": commit_id,
        "vector_count": len(cell.signatures),
        "signatures": expected_signatures,
        "produced_at": produced_at,
    }
    try:
        _write_fsynced(
            stage_dir / "manifest.json",
            json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"),
        )
        _write_fsynced(stage_dir / "vectors.jsonl", _legacy_vectors_bytes(cell.signatures))
        checksum = _checksum_dir(stage_dir)
        commit = {
            "schema_version": 1,
            "cell_id": cell.cell_id,
            "faiss_commit_id": commit_id,
            "produced_at": produced_at,
            "vector_count": manifest["vector_count"],
            "checksum": checksum,
            "input_event_range": (
                [cell.first_event_id, cell.last_event_id]
                if cell.first_event_id else None
            ),
            "source": "indexer",
            "stage": 2,
        }
        _write_fsynced(
            stage_dir / "commit.json",
            json.dumps(commit, indent=2, sort_keys=True).encode("utf-8"),
        )
        _load_verified_legacy_directory(
            stage_dir,
            expected_cell_id=cell.cell_id,
            expected_commit_id=commit_id,
            expected_signatures=expected_signatures,
        )
        os.replace(stage_dir, final_dir)
        published = _load_verified_legacy_commit(
            vector_root,
            cell.cell_id,
            commit_id,
            expected_signatures=expected_signatures,
        )
        return {
            "commit_dir": final_dir,
            "artifact_path": final_dir.relative_to(vector_root.resolve().parent).as_posix(),
            "vector_count": published["vector_count"],
            "checksum": published["checksum"],
        }
    except Exception:
        if stage_dir.exists():
            _remove_projection_stage(stage_dir, commits_dir)
        raise


def _swap_current_pointer(cell_dir: Path, commit_id: str) -> None:
    """Atomically point `<cell>/current.json` at the new commit."""
    cell_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"commit_id": commit_id, "applied_at": _utc_now_iso()},
                          indent=2, sort_keys=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", delete=False,
        dir=cell_dir, prefix=".current.", suffix=".tmp",
    ) as tmp:
        tmp.write(payload)
        tmp_path = Path(tmp.name)
    try:
        os.replace(tmp_path, cell_dir / "current.json")
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise


def read_current_pointer(cell_dir: Path) -> str | None:
    p = cell_dir / "current.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("commit_id")
    except Exception:
        return None


def verify_commit_integrity(vector_root: Path, cell_id: str,
                              commit_id: str) -> bool:
    """Re-hash the committed directory and compare with commit.json's
    checksum. Returns False if mismatched or missing."""
    try:
        _load_verified_legacy_commit(vector_root, cell_id, commit_id)
    except Exception:
        return False
    return True


# ── Validated projection-only materialization ─────────────────────

def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _canonical_json_line(value: Any) -> bytes:
    return canonical_json_bytes(value) + b"\n"


def _safe_cell_dir(vector_root: Path, cell_id: str) -> Path:
    safe_cell = vector_projection.validate_vector_cell_id(cell_id)
    root = vector_root.resolve()
    candidate = (root / safe_cell).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("cell directory escapes vector root") from exc
    return candidate


def _safe_projection_commits_dir(vector_root: Path, cell_id: str) -> Path:
    cell_dir = _safe_cell_dir(vector_root, cell_id)
    commits_dir = (cell_dir / "commits").resolve()
    try:
        commits_dir.relative_to(cell_dir)
    except ValueError as exc:
        raise ValueError("commits directory escapes cell root") from exc
    return commits_dir


def _safe_legacy_commit_dir(
    vector_root: Path,
    cell_id: str,
    commit_id: str,
) -> Path:
    if not isinstance(commit_id, str) or not _LEGACY_COMMIT_ID.fullmatch(commit_id):
        raise ValueError("legacy commit_id is invalid")
    commits_dir = _safe_projection_commits_dir(vector_root, cell_id)
    candidate = (commits_dir / commit_id).resolve()
    try:
        candidate.relative_to(commits_dir)
    except ValueError as exc:
        raise ValueError("legacy commit directory escapes cell root") from exc
    return candidate


def _safe_projection_commit_dir(
    vector_root: Path,
    cell_id: str,
    commit_id: str,
) -> Path:
    if not isinstance(commit_id, str) or not _PROJECTION_COMMIT_ID.fullmatch(commit_id):
        raise ValueError("projection commit_id must contain one full sha256")
    commits_dir = _safe_projection_commits_dir(vector_root, cell_id)
    candidate = (commits_dir / commit_id).resolve()
    try:
        candidate.relative_to(commits_dir)
    except ValueError as exc:
        raise ValueError("projection commit directory escapes cell root") from exc
    return candidate


def _validate_projection_cell_complete(cell: CellState) -> None:
    if not cell.projection_mode:
        raise ValueError("cell is not in projected mode")
    active = set(cell.signatures)
    document_ids = set(cell.projection_documents)
    identity_ids = set(cell.projection_source_identities)
    embedding_ids = set(cell.projection_embedding_contracts)
    topology_ids = set(cell.projection_topology_digests)
    if not (
        active == document_ids == identity_ids == embedding_ids == topology_ids
    ):
        missing_documents = sorted(active - document_ids)
        missing_identities = sorted(active - identity_ids)
        raise ValueError(
            "projected cell is incomplete: "
            f"missing_documents={missing_documents}, "
            f"missing_source_identities={missing_identities}"
        )
    if active:
        embeddings_by_digest: dict[str, dict[str, Any]] = {}
        for model_id in sorted(active):
            embedding = vector_projection.validate_embedding_contract(
                cell.projection_embedding_contracts[model_id]
            )
            embeddings_by_digest[embedding["contract_digest"]] = embedding
        if len(embeddings_by_digest) != 1:
            raise ValueError(
                "projected cell migration is incomplete: mixed embedding contracts"
            )
        topology_digests = set(cell.projection_topology_digests.values())
        if len(topology_digests) != 1:
            raise ValueError(
                "projected cell migration is incomplete: mixed topology digests"
            )
        cell.embedding_contract = next(iter(embeddings_by_digest.values()))
        cell.topology_digest = next(iter(topology_digests))
    if cell.embedding_contract is None or cell.topology_digest is None:
        raise ValueError("projected cell is missing model or topology binding")
    vector_projection.validate_embedding_contract(cell.embedding_contract)
    if not isinstance(cell.topology_digest, str) or not _FULL_DIGEST.fullmatch(
        cell.topology_digest
    ):
        raise ValueError("projected cell topology_digest is invalid")
    for identity_digest, fingerprint in cell.seen_source_identities.items():
        if not _FULL_DIGEST.fullmatch(identity_digest):
            raise ValueError("processed source identity digest is invalid")
        if not isinstance(fingerprint, str) or not _FULL_DIGEST.fullmatch(fingerprint):
            raise ValueError("processed source identity fingerprint is invalid")


def _projection_rows(cell: CellState) -> list[dict[str, Any]]:
    _validate_projection_cell_complete(cell)
    rows: list[dict[str, Any]] = []
    seen_identities: set[str] = set()
    for model_id in sorted(cell.signatures):
        document = vector_projection.validate_solver_contract_projection(
            cell.projection_documents[model_id]
        )
        embedding = vector_projection.validate_embedding_contract(
            cell.projection_embedding_contracts[model_id]
        )
        identity = vector_projection.validate_projection_source_binding(
            document,
            cell.projection_source_identities[model_id],
            embedding,
        )
        if document["canonical_solver_id"] != model_id:
            raise ValueError("projection document key does not match solver state")
        if identity["canonical_solver_id"] != model_id:
            raise ValueError("source identity key does not match solver state")
        if document["cell_id"] != cell.cell_id:
            raise ValueError("projection document is assigned to another cell")
        if document["topology_digest"] != cell.topology_digest:
            raise ValueError("projection document topology binding is stale")
        if embedding != cell.embedding_contract:
            raise ValueError("projection row embedding binding is stale")
        if cell.projection_topology_digests[model_id] != cell.topology_digest:
            raise ValueError("projection row topology generation is stale")
        identity_digest = identity["identity_digest"]
        if identity_digest in seen_identities:
            raise ValueError("active projections contain duplicate source identity")
        seen_identities.add(identity_digest)
        dedup_key = _projected_upsert_dedup_key(
            identity,
            cell.embedding_contract,
            cell.topology_digest,
        )
        expected_fingerprint = _projected_upsert_fingerprint(
            document,
            cell.embedding_contract,
            cell.topology_digest,
        )
        if cell.seen_source_identities.get(dedup_key) != expected_fingerprint:
            raise ValueError("active projection is missing durable dedup identity")
        rows.append(
            {
                "projection_document": document,
                "source_identity": identity,
            }
        )
    return rows


def _projection_artifact_payload(
    cell: CellState,
    *,
    manifest_schema: str = PROJECTION_MANIFEST_SCHEMA,
) -> tuple[str, bytes, dict[str, Any], bytes, dict[str, Any], bytes]:
    rows = _projection_rows(cell)
    documents_bytes = b"".join(_canonical_json_line(row) for row in rows)
    documents_sha256 = _sha256_bytes(documents_bytes)
    identity_versions = sorted(
        {row["source_identity"]["schema_version"] for row in rows}
    )
    identity_payload = {
        "schema_version": manifest_schema,
        "materialization_state": "projection_only",
        "index_kind": "none",
        "cell_id": cell.cell_id,
        "document_count": len(rows),
        "documents_sha256": documents_sha256,
        "documents_bytes": len(documents_bytes),
        "source_identity_digests": sorted(
            row["source_identity"]["identity_digest"] for row in rows
        ),
        "processed_source_identities": dict(
            sorted(cell.seen_source_identities.items())
        ),
        "embedding_contract": vector_projection.validate_embedding_contract(
            cell.embedding_contract
        ),
        "topology_digest": cell.topology_digest,
        "projection_schema_version": vector_projection.SOLVER_PROJECTION_VERSION,
    }
    if manifest_schema == PROJECTION_MANIFEST_V1_SCHEMA:
        if identity_versions not in (
            [],
            [vector_projection.PROJECTION_SOURCE_IDENTITY_V1_VERSION],
        ):
            raise ValueError(
                "projection manifest v1 cannot encode receipt-bound identities"
            )
        identity_payload["source_identity_schema_version"] = (
            vector_projection.PROJECTION_SOURCE_IDENTITY_V1_VERSION
        )
    elif manifest_schema == PROJECTION_MANIFEST_V2_SCHEMA:
        identity_payload["source_identity_schema_versions"] = identity_versions
    else:
        raise ValueError("unsupported projection manifest schema")
    commit_id = "proj_" + sha256_digest(identity_payload).split(":", 1)[1]
    manifest = {**identity_payload, "commit_id": commit_id}
    manifest_bytes = _canonical_json_line(manifest)
    commit = {
        "schema_version": PROJECTION_COMMIT_SCHEMA,
        "materialization_state": "projection_only",
        "cell_id": cell.cell_id,
        "commit_id": commit_id,
        "manifest_sha256": _sha256_bytes(manifest_bytes),
        "manifest_bytes": len(manifest_bytes),
        "documents_sha256": documents_sha256,
        "documents_bytes": len(documents_bytes),
    }
    commit_bytes = _canonical_json_line(commit)
    return (
        commit_id,
        documents_bytes,
        manifest,
        manifest_bytes,
        commit,
        commit_bytes,
    )


def compute_projection_commit_id(cell: CellState) -> str:
    return _projection_artifact_payload(cell)[0]


def _write_fsynced(path: Path, payload: bytes) -> None:
    with open(path, "xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _directory_file_digests(path: Path) -> dict[str, str]:
    return {
        candidate.relative_to(path).as_posix(): _sha256_bytes(candidate.read_bytes())
        for candidate in sorted(path.rglob("*"))
        if candidate.is_file()
    }


def _remove_projection_stage(stage_dir: Path, commits_dir: Path) -> None:
    resolved_stage = stage_dir.resolve()
    resolved_commits = commits_dir.resolve()
    if (
        resolved_stage.parent != resolved_commits
        or not resolved_stage.name.startswith(".stage-")
    ):
        raise ValueError("refusing to remove a non-staging projection directory")
    if resolved_stage.exists():
        shutil.rmtree(resolved_stage)


def _stage_projection_commit(cell: CellState, vector_root: Path) -> dict[str, Any]:
    (
        commit_id,
        documents_bytes,
        _manifest,
        manifest_bytes,
        _commit,
        commit_bytes,
    ) = _projection_artifact_payload(cell)
    cell_dir = _safe_cell_dir(vector_root, cell.cell_id)
    commits_dir = _safe_projection_commits_dir(vector_root, cell.cell_id)
    commits_dir.mkdir(parents=True, exist_ok=True)
    final_dir = _safe_projection_commit_dir(vector_root, cell.cell_id, commit_id)
    stage_dir = Path(
        tempfile.mkdtemp(prefix=".stage-", dir=commits_dir)
    ).resolve()
    try:
        _write_fsynced(stage_dir / "documents.jsonl", documents_bytes)
        _write_fsynced(stage_dir / "manifest.json", manifest_bytes)
        _write_fsynced(stage_dir / "commit.json", commit_bytes)
        _load_verified_projection_directory(
            stage_dir,
            expected_cell_id=cell.cell_id,
            expected_commit_id=commit_id,
            expected_embedding_contract=cell.embedding_contract,
            expected_topology_digest=cell.topology_digest,
        )
        if final_dir.exists():
            _load_verified_projection_directory(
                final_dir,
                expected_cell_id=cell.cell_id,
                expected_commit_id=commit_id,
                expected_embedding_contract=cell.embedding_contract,
                expected_topology_digest=cell.topology_digest,
            )
            if _directory_file_digests(stage_dir) != _directory_file_digests(final_dir):
                raise ValueError("existing projection commit conflicts with content address")
            _remove_projection_stage(stage_dir, commits_dir)
        else:
            os.replace(stage_dir, final_dir)
        checksum = _checksum_dir(final_dir)
        return {
            "commit_dir": final_dir,
            "commit_id": commit_id,
            "artifact_path": final_dir.relative_to(vector_root.resolve().parent).as_posix(),
            "vector_count": len(cell.projection_documents),
            "checksum": checksum,
        }
    except Exception:
        if stage_dir.exists():
            _remove_projection_stage(stage_dir, commits_dir)
        raise


def _write_atomic_json(target: Path, value: dict[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = _canonical_json_line(value)
    temp_path = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        _write_fsynced(temp_path, payload)
        os.replace(temp_path, target)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _swap_projection_pointer(
    cell_dir: Path,
    commit_id: str,
    previous_commit_id: str | None,
) -> None:
    if not _PROJECTION_COMMIT_ID.fullmatch(commit_id):
        raise ValueError("invalid projection pointer commit_id")
    previous_commit_id = _validate_commit_id(
        previous_commit_id,
        allow_none=True,
    )
    pointer = {
        "schema_version": PROJECTION_POINTER_SCHEMA,
        "commit_id": commit_id,
        "previous_commit_id": previous_commit_id,
    }
    _write_atomic_json(cell_dir / "current.json", pointer)


def _read_exact_json(path: Path, expected_keys: frozenset[str], label: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ValueError(f"invalid {label}") from exc
    if type(value) is not dict or frozenset(value) != expected_keys:
        raise ValueError(f"{label} has an unknown schema shape")
    if raw != _canonical_json_line(value):
        raise ValueError(f"{label} is not canonically encoded")
    return value


def _read_projection_manifest(path: Path) -> dict[str, Any]:
    """Read one canonical manifest and dispatch its exact versioned shape."""
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ValueError("invalid projection manifest") from exc
    if type(value) is not dict:
        raise ValueError("projection manifest has an unknown schema shape")
    expected_keys = {
        PROJECTION_MANIFEST_V1_SCHEMA: _PROJECTION_MANIFEST_V1_KEYS,
        PROJECTION_MANIFEST_V2_SCHEMA: _PROJECTION_MANIFEST_V2_KEYS,
    }.get(value.get("schema_version"))
    if expected_keys is None:
        raise ValueError("unsupported projection manifest schema")
    if frozenset(value) != expected_keys:
        raise ValueError("projection manifest has an unknown schema shape")
    if raw != _canonical_json_line(value):
        raise ValueError("projection manifest is not canonically encoded")
    return value


def _load_verified_projection_directory(
    commit_dir: Path,
    *,
    expected_cell_id: str,
    expected_commit_id: str,
    expected_embedding_contract: dict[str, Any],
    expected_topology_digest: str,
) -> dict[str, Any]:
    if not commit_dir.is_dir():
        raise ValueError("projection commit directory is missing")
    expected_names = {"documents.jsonl", "manifest.json", "commit.json"}
    entries = list(commit_dir.iterdir())
    if {entry.name for entry in entries} != expected_names:
        raise ValueError("projection commit contains unexpected artifacts")
    _require_regular_single_link_files(entries, "projection commit")
    documents_path = commit_dir / "documents.jsonl"
    manifest_path = commit_dir / "manifest.json"
    commit_path = commit_dir / "commit.json"
    if not all(path.is_file() for path in (documents_path, manifest_path, commit_path)):
        raise ValueError("projection commit is incomplete")
    manifest = _read_projection_manifest(manifest_path)
    commit = _read_exact_json(
        commit_path, _PROJECTION_COMMIT_KEYS, "projection commit record"
    )
    manifest_schema = manifest["schema_version"]
    if commit["schema_version"] != PROJECTION_COMMIT_SCHEMA:
        raise ValueError("unsupported projection commit schema")
    if manifest["materialization_state"] != "projection_only" or manifest["index_kind"] != "none":
        raise ValueError("projection artifact overstates materialization readiness")
    if commit["materialization_state"] != "projection_only":
        raise ValueError("projection commit state mismatch")
    if manifest["cell_id"] != expected_cell_id or commit["cell_id"] != expected_cell_id:
        raise ValueError("projection commit cell mismatch")
    if manifest["commit_id"] != expected_commit_id or commit["commit_id"] != expected_commit_id:
        raise ValueError("projection commit id mismatch")
    validated_embedding = vector_projection.validate_embedding_contract(
        expected_embedding_contract
    )
    if manifest["embedding_contract"] != validated_embedding:
        raise ValueError("projection embedding contract mismatch")
    if manifest["topology_digest"] != expected_topology_digest:
        raise ValueError("projection topology digest mismatch")
    if not isinstance(expected_topology_digest, str) or not _FULL_DIGEST.fullmatch(
        expected_topology_digest
    ):
        raise ValueError("expected projection topology digest is invalid")
    if manifest["projection_schema_version"] != vector_projection.SOLVER_PROJECTION_VERSION:
        raise ValueError("projection document schema mismatch")
    if manifest_schema == PROJECTION_MANIFEST_V1_SCHEMA:
        if (
            manifest["source_identity_schema_version"]
            != vector_projection.PROJECTION_SOURCE_IDENTITY_V1_VERSION
        ):
            raise ValueError("projection source identity schema mismatch")
        declared_identity_versions = [
            vector_projection.PROJECTION_SOURCE_IDENTITY_V1_VERSION
        ]
    else:
        declared_identity_versions = manifest["source_identity_schema_versions"]
        if (
            type(declared_identity_versions) is not list
            or any(
                type(version) is not str
                or version not in vector_projection.PROJECTION_SOURCE_IDENTITY_VERSIONS
                for version in declared_identity_versions
            )
            or declared_identity_versions
            != sorted(set(declared_identity_versions))
        ):
            raise ValueError("projection source identity schemas are invalid")

    documents_bytes = documents_path.read_bytes()
    rows: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(documents_bytes.splitlines(), start=1):
        if not raw_line:
            raise ValueError("projection documents contain an empty row")
        try:
            row = json.loads(raw_line.decode("utf-8"))
        except Exception as exc:
            raise ValueError(f"invalid projection document row {line_number}") from exc
        if type(row) is not dict or frozenset(row) != _PROJECTION_ROW_KEYS:
            raise ValueError("projection document row has an unknown shape")
        document = vector_projection.validate_solver_contract_projection(
            row["projection_document"]
        )
        identity = vector_projection.validate_projection_source_binding(
            document,
            row["source_identity"],
            validated_embedding,
        )
        if document["cell_id"] != expected_cell_id:
            raise ValueError("projection row cell mismatch")
        if document["topology_digest"] != expected_topology_digest:
            raise ValueError("projection row topology mismatch")
        rows.append({"projection_document": document, "source_identity": identity})
    if documents_bytes != b"".join(_canonical_json_line(row) for row in rows):
        raise ValueError("projection documents are not canonically encoded")
    if manifest["document_count"] != len(rows):
        raise ValueError("projection document count mismatch")
    if type(manifest["document_count"]) is not int or manifest["document_count"] < 0:
        raise ValueError("projection document count is invalid")
    if manifest["documents_sha256"] != _sha256_bytes(documents_bytes):
        raise ValueError("projection documents checksum mismatch")
    if manifest["documents_bytes"] != len(documents_bytes):
        raise ValueError("projection documents size mismatch")
    identity_digests = sorted(row["source_identity"]["identity_digest"] for row in rows)
    if len(identity_digests) != len(set(identity_digests)):
        raise ValueError("projection commit contains duplicate source identities")
    if manifest["source_identity_digests"] != identity_digests:
        raise ValueError("projection source identity manifest mismatch")
    actual_identity_versions = sorted(
        {row["source_identity"]["schema_version"] for row in rows}
    )
    expected_identity_versions = (
        []
        if manifest_schema == PROJECTION_MANIFEST_V1_SCHEMA and not rows
        else declared_identity_versions
    )
    if actual_identity_versions != expected_identity_versions:
        raise ValueError("projection source identity schema manifest mismatch")
    solver_ids = [row["projection_document"]["canonical_solver_id"] for row in rows]
    if len(solver_ids) != len(set(solver_ids)):
        raise ValueError("projection commit contains duplicate solver documents")
    processed_identities = manifest["processed_source_identities"]
    if type(processed_identities) is not dict:
        raise ValueError("processed source identities must be an object")
    for identity_digest, fingerprint in processed_identities.items():
        if not isinstance(identity_digest, str) or not _FULL_DIGEST.fullmatch(
            identity_digest
        ):
            raise ValueError("processed source identity digest is invalid")
        if not isinstance(fingerprint, str) or not _FULL_DIGEST.fullmatch(fingerprint):
            raise ValueError("processed source identity fingerprint is invalid")

    reconstructed = CellState(
        cell_id=expected_cell_id,
        signatures={
            row["projection_document"]["canonical_solver_id"]: row["projection_document"][
                "solver_contract_digest"
            ]
            for row in rows
        },
        projection_mode=True,
        projection_documents={
            row["projection_document"]["canonical_solver_id"]: row["projection_document"]
            for row in rows
        },
        projection_source_identities={
            row["projection_document"]["canonical_solver_id"]: row["source_identity"]
            for row in rows
        },
        projection_embedding_contracts={
            row["projection_document"]["canonical_solver_id"]: validated_embedding
            for row in rows
        },
        projection_topology_digests={
            row["projection_document"]["canonical_solver_id"]: expected_topology_digest
            for row in rows
        },
        seen_source_identities=dict(processed_identities),
        embedding_contract=validated_embedding,
        topology_digest=expected_topology_digest,
    )
    (
        derived_commit_id,
        _derived_documents,
        derived_manifest,
        derived_manifest_bytes,
        derived_commit,
        derived_commit_bytes,
    ) = _projection_artifact_payload(
        reconstructed,
        manifest_schema=manifest_schema,
    )
    if derived_commit_id != expected_commit_id:
        raise ValueError("projection content address mismatch")
    if manifest != derived_manifest or manifest_path.read_bytes() != derived_manifest_bytes:
        raise ValueError("projection manifest mismatch")
    if commit != derived_commit or commit_path.read_bytes() != derived_commit_bytes:
        raise ValueError("projection commit record mismatch")
    receipt_bound_count = sum(
        row["source_identity"]["receipt_bound"] is True for row in rows
    )
    return {
        "commit_id": expected_commit_id,
        "manifest": manifest,
        "commit": commit,
        "documents": rows,
        "embedding_contract": validated_embedding,
        "topology_digest": expected_topology_digest,
        "receipt_bound_count": receipt_bound_count,
        "unreceipted_count": len(rows) - receipt_bound_count,
        # Every v2 proof above was replayed through the deterministic admission
        # contract and cross-bound to this exact document/embedding generation.
        "receipt_structure_reverified": True,
        "receipt_authenticity_verified": False,
        "solver_outcome_verified": False,
        "runtime_authority_granted": False,
    }


def load_verified_projection_commit(
    vector_root: Path | str,
    cell_id: str,
    *,
    expected_embedding_contract: dict[str, Any],
    expected_topology_digest: str,
    commit_id: str | None = None,
) -> dict[str, Any]:
    """Load a projection commit only when every caller binding matches."""
    root = Path(vector_root)
    cell_dir = _safe_cell_dir(root, cell_id)
    pointer = None
    if commit_id is None:
        pointer = _read_exact_json(
            cell_dir / "current.json", _PROJECTION_POINTER_KEYS, "projection pointer"
        )
        if pointer["schema_version"] != PROJECTION_POINTER_SCHEMA:
            raise ValueError("unsupported projection pointer schema")
        commit_id = pointer["commit_id"]
        previous = pointer["previous_commit_id"]
        _validate_commit_id(previous, allow_none=True)
    commit_dir = _safe_projection_commit_dir(root, cell_id, commit_id)
    result = _load_verified_projection_directory(
        commit_dir,
        expected_cell_id=cell_id,
        expected_commit_id=commit_id,
        expected_embedding_contract=expected_embedding_contract,
        expected_topology_digest=expected_topology_digest,
    )
    result["pointer"] = pointer
    return result


# ── Apply pass ─────────────────────────────────────────────────────

@dataclass
class CellApplyResult:
    cell_id: str
    status: str                # "applied" | "no-change" | "failed"
    commit_id: str | None = None
    prior_commit_id: str | None = None
    vector_count: int = 0
    error: str | None = None
    commit_applied_event_id: str | None = None


@dataclass
class ApplyReport:
    dry_run: bool
    events_processed: int
    cells_with_changes: int
    cells_applied: int
    cells_skipped_no_change: int
    cells_failed: int
    cell_results: dict[str, CellApplyResult] = field(default_factory=dict)


def _state_from_projection_result(
    cell_id: str,
    result: dict[str, Any],
) -> CellState:
    rows = result["documents"]
    embedding = result["embedding_contract"]
    topology_digest = result["topology_digest"]
    processed = result["manifest"]["processed_source_identities"]
    return CellState(
        cell_id=cell_id,
        signatures={
            row["projection_document"]["canonical_solver_id"]: row[
                "projection_document"
            ]["solver_contract_digest"]
            for row in rows
        },
        projection_mode=True,
        projection_documents={
            row["projection_document"]["canonical_solver_id"]: row[
                "projection_document"
            ]
            for row in rows
        },
        projection_source_identities={
            row["projection_document"]["canonical_solver_id"]: row[
                "source_identity"
            ]
            for row in rows
        },
        projection_embedding_contracts={
            row["projection_document"]["canonical_solver_id"]: embedding
            for row in rows
        },
        projection_topology_digests={
            row["projection_document"]["canonical_solver_id"]: topology_digest
            for row in rows
        },
        seen_source_identities=dict(processed),
        embedding_contract=embedding,
        topology_digest=topology_digest,
    )


def _load_prior_cell_state(
    vector_root: Path,
    cell_id: str,
    commit_id: str | None,
) -> CellState:
    """Rehydrate only from an integrity-checked, contained artifact."""
    safe_cell = vector_projection.validate_vector_cell_id(cell_id)
    if commit_id is None:
        return CellState(cell_id=safe_cell)
    commit_id = _validate_commit_id(commit_id)
    if _PROJECTION_COMMIT_ID.fullmatch(commit_id):
        commit_dir = _safe_projection_commit_dir(vector_root, safe_cell, commit_id)
        manifest = _read_projection_manifest(commit_dir / "manifest.json")
        result = load_verified_projection_commit(
            vector_root,
            safe_cell,
            expected_embedding_contract=manifest["embedding_contract"],
            expected_topology_digest=manifest["topology_digest"],
            commit_id=commit_id,
        )
        return _state_from_projection_result(safe_cell, result)

    result = _load_verified_legacy_commit(vector_root, safe_cell, commit_id)
    return CellState(cell_id=safe_cell, signatures=result["signatures"])


def _cell_projection_since(cell_id: str,
                             since_event_id: str | None,
                             event_log: Path | str | None) -> CellState:
    """Build the cell's desired state from events strictly AFTER
    `since_event_id`. Used by apply(); separate from the global
    replay() for clarity."""
    cell = CellState(cell_id=cell_id)
    active = since_event_id is None
    for event in vector_events.read_events(event_log, strict=True):
        eid = event.event_id()
        if not active:
            if eid == since_event_id:
                active = True
            continue
        if event.cell_id != cell_id:
            continue
        if cell.first_event_id is None:
            cell.first_event_id = eid
        cell.last_event_id = eid
        if event.event in vector_events.ALL_VECTOR_EVENT_NAMES:
            _apply_event_to_state(cell, event, eid)
    if since_event_id is not None and not active:
        raise ValueError("checkpoint event anchor was not found in the event log")
    return cell


def _cell_projection_from_prior_state(
    prior_state: CellState,
    since_event_id: str | None,
    event_log: Path | str | None,
    *,
    events: list[vector_events.VectorEvent] | None = None,
) -> CellState:
    """Compute the cell state by starting from a verified prior artifact and
    folding in only events since the checkpointed id. This gives the
    complete current picture, not just the delta."""
    cell_id = prior_state.cell_id
    cell = CellState(
        cell_id=cell_id,
        signatures=dict(prior_state.signatures),
        projection_mode=prior_state.projection_mode,
        projection_documents=dict(prior_state.projection_documents),
        projection_source_identities=dict(
            prior_state.projection_source_identities
        ),
        projection_embedding_contracts=dict(
            prior_state.projection_embedding_contracts
        ),
        projection_topology_digests=dict(
            prior_state.projection_topology_digests
        ),
        seen_source_identities=dict(prior_state.seen_source_identities),
        embedding_contract=(
            dict(prior_state.embedding_contract)
            if prior_state.embedding_contract is not None
            else None
        ),
        topology_digest=prior_state.topology_digest,
    )
    active = since_event_id is None
    event_stream = (
        events
        if events is not None
        else list(vector_events.read_events(event_log, strict=True))
    )
    for event in event_stream:
        eid = event.event_id()
        if not active:
            if eid == since_event_id:
                active = True
            continue
        if event.cell_id != cell_id:
            continue
        if cell.first_event_id is None:
            cell.first_event_id = eid
        cell.last_event_id = eid
        if event.event in vector_events.ALL_VECTOR_EVENT_NAMES:
            _apply_event_to_state(cell, event, eid)
    if since_event_id is not None and not active:
        raise ValueError("checkpoint event anchor was not found in the event log")
    return cell


def _cells_with_events(
    since_event_id: str | None,
    event_log: Path | str | None,
    *,
    events: list[vector_events.VectorEvent] | None = None,
) -> tuple[set[str], str | None, str | None, int]:
    """Return cells, event range, and count from one immutable log prefix."""
    cells: set[str] = set()
    first, last = None, None
    count = 0
    active = since_event_id is None
    event_stream = (
        events
        if events is not None
        else list(vector_events.read_events(event_log, strict=True))
    )
    for event in event_stream:
        eid = event.event_id()
        if not active:
            if eid == since_event_id:
                active = True
            continue
        cells.add(event.cell_id)
        count += 1
        if first is None:
            first = eid
        last = eid
    if since_event_id is not None and not active:
        raise ValueError("requested event anchor was not found in the event log")
    return cells, first, last, count


def apply(
    event_log: Path | str | None = None,
    vector_root: Path | str | None = None,
    checkpoint_path: Path | str | None = None,
    since_event_id: str | None = None,
    cell_filter: str | None = None,
    dry_run: bool = True,
    force: bool = False,
    _fail_before_swap_for_cells: set[str] | None = None,
) -> ApplyReport:
    """Run an apply pass.

    Returns an `ApplyReport` even in dry-run mode — only
    `dry_run=False` actually writes anything. `_fail_before_swap_for_cells`
    is a test-only knob that simulates a crash after staging but before
    the pointer swap.
    """
    vroot = Path(vector_root) if vector_root else DEFAULT_VECTOR_ROOT
    elog = event_log if event_log is not None else DEFAULT_EVENT_LOG

    checkpoint = load_checkpoint(checkpoint_path)

    # Figure out which cells have pending events globally (we'll filter
    # by cell_filter when iterating).
    event_snapshot = list(vector_events.read_events(elog, strict=True))
    cells_seen, first_eid_global, last_eid_global, events_processed = _cells_with_events(
        since_event_id,
        elog,
        events=event_snapshot,
    )
    if cell_filter is not None:
        cells_seen = {c for c in cells_seen if c == cell_filter}

    report = ApplyReport(
        dry_run=dry_run,
        events_processed=0,
        cells_with_changes=0,
        cells_applied=0,
        cells_skipped_no_change=0,
        cells_failed=0,
    )

    # Also consider cells in the checkpoint that might need re-verify
    # even if no new events arrived — only if --force.
    if force:
        for cell_id in checkpoint.per_cell:
            if cell_filter is None or cell_id == cell_filter:
                cells_seen.add(cell_id)

    # Build per-cell apply plan.
    for cell_id in sorted(cells_seen):
        per_cell = checkpoint.cell_entry(cell_id)
        prior_commit = per_cell.commit_id
        try:
            prior_state = _load_prior_cell_state(vroot, cell_id, prior_commit)
            # Project the complete desired state by folding only events not
            # already checkpointed for this cell.
            since_cell = per_cell.last_applied_event_id or since_event_id
            cell_state = _cell_projection_from_prior_state(
                prior_state,
                since_cell,
                elog,
                events=event_snapshot,
            )
            new_commit_id = (
                compute_projection_commit_id(cell_state)
                if cell_state.projection_mode
                else compute_commit_id(cell_state)
            )
        except Exception as exc:  # noqa: BLE001
            report.cells_with_changes += 1
            report.cells_failed += 1
            report.cell_results[cell_id] = CellApplyResult(
                cell_id=cell_id,
                status="failed",
                prior_commit_id=prior_commit,
                error=f"{type(exc).__name__}: {exc}",
            )
            continue

        if new_commit_id == prior_commit and not force:
            report.cells_skipped_no_change += 1
            report.cell_results[cell_id] = CellApplyResult(
                cell_id=cell_id, status="no-change",
                commit_id=new_commit_id, prior_commit_id=prior_commit,
                vector_count=len(cell_state.signatures),
            )
            continue

        report.cells_with_changes += 1

        if dry_run:
            report.cell_results[cell_id] = CellApplyResult(
                cell_id=cell_id, status="would-apply",
                commit_id=new_commit_id, prior_commit_id=prior_commit,
                vector_count=len(cell_state.signatures),
            )
            continue

        # Apply: stage → (simulated failure hook) → swap → emit →
        # checkpoint-update
        try:
            if cell_state.projection_mode:
                staged = _stage_projection_commit(cell_state, vroot)
            else:
                staged = _stage_commit(cell_state, new_commit_id, vroot)
            if _fail_before_swap_for_cells and cell_id in _fail_before_swap_for_cells:
                raise RuntimeError("simulated crash before pointer swap")
            if cell_state.projection_mode:
                previous_for_pointer = prior_commit
                if new_commit_id == prior_commit:
                    existing_pointer = _read_exact_json(
                        _safe_cell_dir(vroot, cell_id) / "current.json",
                        _PROJECTION_POINTER_KEYS,
                        "projection pointer",
                    )
                    previous_for_pointer = existing_pointer["previous_commit_id"]
                _swap_projection_pointer(
                    _safe_cell_dir(vroot, cell_id),
                    new_commit_id,
                    previous_for_pointer,
                )
            else:
                _swap_current_pointer(
                    _safe_cell_dir(vroot, cell_id),
                    new_commit_id,
                )

            # Emit commit_applied
            commit_event_kwargs: dict[str, Any] = {}
            if cell_state.projection_mode:
                commit_event_kwargs = {
                    "materialization_state": "projection_only",
                    "index_kind": "none",
                }
            event = vector_events.vector_commit_applied(
                cell_id=cell_id,
                faiss_commit_id=new_commit_id,
                artifact_path=staged["artifact_path"],
                vector_count=staged["vector_count"],
                checksum=staged["checksum"],
                source_events=list(cell_state.source_event_ids),
                input_event_range=(
                    (cell_state.first_event_id, cell_state.last_event_id)
                    if cell_state.first_event_id else None
                ),
                source="indexer",
                **commit_event_kwargs,
            )
            vector_events.emit(event, elog)

            # Advance cell checkpoint entry
            per_cell.last_applied_event_id = cell_state.last_event_id or per_cell.last_applied_event_id
            per_cell.commit_id = new_commit_id
            per_cell.applied_ts = _utc_now_iso()
            per_cell.vector_count = len(cell_state.signatures)

            report.cells_applied += 1
            report.cell_results[cell_id] = CellApplyResult(
                cell_id=cell_id, status="applied",
                commit_id=new_commit_id, prior_commit_id=prior_commit,
                vector_count=len(cell_state.signatures),
                commit_applied_event_id=event.event_id(),
            )
        except Exception as exc:  # noqa: BLE001
            report.cells_failed += 1
            report.cell_results[cell_id] = CellApplyResult(
                cell_id=cell_id, status="failed",
                commit_id=new_commit_id, prior_commit_id=prior_commit,
                vector_count=len(cell_state.signatures),
                error=f"{type(exc).__name__}: {exc}",
            )
            # Crucial: per_cell checkpoint NOT advanced on failure.
            continue

    # Advance the global checkpoint ts + id only if at least one cell
    # actually applied. Failed cells keep their prior state.
    if not dry_run and report.cells_applied > 0:
        checkpoint.last_applied_ts = _utc_now_iso()
        checkpoint.global_last_applied_event_id = last_eid_global
        save_checkpoint(checkpoint, checkpoint_path)

    report.events_processed = events_processed
    return report


# ── CLI ────────────────────────────────────────────────────────────

def _format_replay_report(report: ReplayReport) -> str:
    lines = [
        "vector-indexer replay report",
        "",
        f"events seen:    {report.events_seen}",
        f"events skipped: {report.events_skipped}",
        f"first event:    {report.first_event_id or '—'}",
        f"last event:     {report.last_event_id or '—'}",
        "",
        f"{'cell':12} {'upserts':>8} {'deletes':>8} "
        f"{'committed':>10} {'signatures':>10}  last_commit",
        "-" * 80,
    ]
    for name in sorted(report.cells):
        c = report.cells[name]
        lines.append(
            f"{c.cell_id:12} {c.upsert_requests:>8} {c.delete_requests:>8} "
            f"{c.committed_count:>10} {len(c.signatures):>10}  "
            f"{c.last_commit_id or '—'}"
        )
    if report.unknown_event_types:
        lines.append("")
        lines.append("unknown event types:")
        for name, n in sorted(report.unknown_event_types.items()):
            lines.append(f"  {name}: {n}")
    lines.append("")
    return "\n".join(lines)


def _format_apply_report(report: ApplyReport) -> str:
    lines = [
        f"vector-indexer apply report ({'DRY-RUN' if report.dry_run else 'APPLY'})",
        "",
        f"events processed:     {report.events_processed}",
        f"cells with changes:   {report.cells_with_changes}",
        f"cells applied:        {report.cells_applied}",
        f"cells no-change:      {report.cells_skipped_no_change}",
        f"cells failed:         {report.cells_failed}",
        "",
        f"{'cell':12} {'status':16} {'commit_id':22} vectors",
        "-" * 70,
    ]
    for cell_id, r in sorted(report.cell_results.items()):
        lines.append(
            f"{r.cell_id:12} {r.status:16} "
            f"{(r.commit_id or '—'):22} {r.vector_count}"
        )
        if r.error:
            lines.append(f"    ERROR: {r.error}")
    lines.append("")
    return "\n".join(lines)


def _replay_to_json(report: ReplayReport) -> dict:
    return {
        "events_seen": report.events_seen,
        "events_skipped": report.events_skipped,
        "first_event_id": report.first_event_id,
        "last_event_id": report.last_event_id,
        "unknown_event_types": dict(report.unknown_event_types),
        "cells": {
            name: {
                "cell_id": c.cell_id,
                "upsert_requests": c.upsert_requests,
                "delete_requests": c.delete_requests,
                "committed_count": c.committed_count,
                "last_commit_id": c.last_commit_id,
                "signatures": dict(c.signatures),
            }
            for name, c in report.cells.items()
        },
    }


def _apply_to_json(report: ApplyReport) -> dict:
    return {
        "dry_run": report.dry_run,
        "events_processed": report.events_processed,
        "cells_with_changes": report.cells_with_changes,
        "cells_applied": report.cells_applied,
        "cells_skipped_no_change": report.cells_skipped_no_change,
        "cells_failed": report.cells_failed,
        "cell_results": {
            cid: asdict(r) for cid, r in report.cell_results.items()
        },
    }


# Legacy aliases used by Stage-1 tests
_to_json = _replay_to_json
_format_report = _format_replay_report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event-log", type=Path, default=None)
    ap.add_argument("--vector-root", type=Path, default=None)
    ap.add_argument("--checkpoint-path", type=Path, default=None)
    ap.add_argument("--since", type=str, default=None)
    ap.add_argument("--cell", type=str, default=None,
                    help="restrict apply to a single cell")
    ap.add_argument("--apply", action="store_true",
                    help="perform writes (default: dry-run)")
    ap.add_argument("--force", action="store_true",
                    help="re-apply even if commit_id matches current")
    ap.add_argument("--replay-only", action="store_true",
                    help="run Stage-1-style projection report only")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.replay_only:
        report = replay(args.event_log, since_event_id=args.since)
        if args.json:
            print(json.dumps(_replay_to_json(report), indent=2, default=str))
        else:
            print(_format_replay_report(report))
        return 0

    ap_report = apply(
        event_log=args.event_log,
        vector_root=args.vector_root,
        checkpoint_path=args.checkpoint_path,
        since_event_id=args.since,
        cell_filter=args.cell,
        dry_run=not args.apply,
        force=args.force,
    )
    if args.json:
        print(json.dumps(_apply_to_json(ap_report), indent=2, default=str))
    else:
        print(_format_apply_report(ap_report))
    return 0 if ap_report.cells_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
