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
runtime.

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
import sys
import tempfile
import traceback
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.magma import vector_events  # noqa: E402


# Default paths. Tests + CLI can override everything.
DEFAULT_VECTOR_ROOT = ROOT / "data" / "vector"
DEFAULT_EVENT_LOG = DEFAULT_VECTOR_ROOT / "events.jsonl"
DEFAULT_CHECKPOINT = DEFAULT_VECTOR_ROOT / "checkpoints" / "vector_indexer.json"

CHECKPOINT_SCHEMA_VERSION = 1


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
            cell.signatures[mid] = sig
            cell.source_event_ids.append(event_id)
        return
    if event.event == vector_events.EVT_VECTOR_DELETE_REQUESTED:
        cell.delete_requests += 1
        mid = event.payload.get("model_id")
        if mid and mid in cell.signatures:
            del cell.signatures[mid]
            cell.source_event_ids.append(event_id)
        return
    if event.event == vector_events.EVT_VECTOR_COMMIT_APPLIED:
        cell.committed_count = int(event.payload.get("vector_count", 0))
        cell.last_commit_id = event.payload.get("faiss_commit_id")
        return


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
    cp = Checkpoint(
        schema_version=data.get("schema_version", 1),
        global_last_applied_event_id=data.get("global_last_applied_event_id"),
        last_applied_ts=data.get("last_applied_ts"),
    )
    for cell_id, entry in (data.get("per_cell") or {}).items():
        cp.per_cell[cell_id] = PerCellCheckpoint(
            last_applied_event_id=entry.get("last_applied_event_id"),
            commit_id=entry.get("commit_id"),
            applied_ts=entry.get("applied_ts"),
            vector_count=int(entry.get("vector_count") or 0),
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


# ── Writer: staging → atomic swap ─────────────────────────────────

def _stage_commit(cell: CellState, commit_id: str,
                    vector_root: Path) -> dict:
    """Write `<vector_root>/<cell>/commits/<commit_id>/{manifest, commit, vectors}.

    Idempotent: if the directory already exists with matching content,
    we still rewrite the manifest (cheap) but keep the existing
    payload. Returns a dict with artifact_path, vector_count, checksum.
    """
    cell_dir = vector_root / cell.cell_id
    commits_dir = cell_dir / "commits"
    commit_dir = commits_dir / commit_id
    commit_dir.mkdir(parents=True, exist_ok=True)

    # Manifest — projection shape.
    manifest = {
        "schema_version": 1,
        "cell_id": cell.cell_id,
        "commit_id": commit_id,
        "vector_count": len(cell.signatures),
        "signatures": dict(sorted(cell.signatures.items())),
        "produced_at": _utc_now_iso(),
    }
    (commit_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8",
    )

    # Placeholder vectors payload — Stage-2 skeleton emits a JSONL
    # file of {solver_id, signature}. A later commit replaces this
    # with a real FAISS index.
    lines = [
        json.dumps({"solver_id": mid, "signature": sig}, sort_keys=True)
        for mid, sig in sorted(cell.signatures.items())
    ]
    (commit_dir / "vectors.jsonl").write_text(
        ("\n".join(lines) + "\n") if lines else "", encoding="utf-8",
    )

    # Commit record — projection pointer used by the runtime repoint.
    checksum = _checksum_dir(commit_dir)
    commit = {
        "schema_version": 1,
        "cell_id": cell.cell_id,
        "faiss_commit_id": commit_id,
        "produced_at": manifest["produced_at"],
        "vector_count": manifest["vector_count"],
        "checksum": checksum,
        "input_event_range": (
            [cell.first_event_id, cell.last_event_id]
            if cell.first_event_id else None
        ),
        "source": "indexer",
        "stage": 2,
    }
    (commit_dir / "commit.json").write_text(
        json.dumps(commit, indent=2, sort_keys=True), encoding="utf-8",
    )
    return {
        "commit_dir": commit_dir,
        "artifact_path": commit_dir.relative_to(vector_root.parent).as_posix()
            if str(commit_dir).startswith(str(vector_root.parent))
            else commit_dir.as_posix(),
        "vector_count": len(cell.signatures),
        "checksum": checksum,
    }


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
    commit_dir = vector_root / cell_id / "commits" / commit_id
    commit_json = commit_dir / "commit.json"
    if not commit_json.exists():
        return False
    try:
        data = json.loads(commit_json.read_text(encoding="utf-8"))
    except Exception:
        return False
    recorded = data.get("checksum")
    if not recorded:
        return False
    actual = _checksum_dir(commit_dir)
    return actual == recorded


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


def _cell_projection_since(cell_id: str,
                             since_event_id: str | None,
                             event_log: Path | str | None) -> CellState:
    """Build the cell's desired state from events strictly AFTER
    `since_event_id`. Used by apply(); separate from the global
    replay() for clarity."""
    cell = CellState(cell_id=cell_id)
    active = since_event_id is None
    for event in vector_events.read_events(event_log):
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
    return cell


def _cell_projection_from_prior_state(
    cell_id: str, prior_signatures: dict[str, str],
    since_event_id: str | None,
    event_log: Path | str | None,
) -> CellState:
    """Compute the cell state by starting from prior_signatures and
    folding in only events since the checkpointed id. This gives the
    complete current picture, not just the delta."""
    cell = CellState(cell_id=cell_id, signatures=dict(prior_signatures))
    active = since_event_id is None
    for event in vector_events.read_events(event_log):
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
    return cell


def _cells_with_events(
    since_event_id: str | None,
    event_log: Path | str | None,
) -> tuple[set[str], str | None, str | None]:
    """Scan the log once and return (set of cells seen, first_event_id,
    last_event_id) strictly after `since_event_id`."""
    cells: set[str] = set()
    first, last = None, None
    active = since_event_id is None
    for event in vector_events.read_events(event_log):
        eid = event.event_id()
        if not active:
            if eid == since_event_id:
                active = True
            continue
        cells.add(event.cell_id)
        if first is None:
            first = eid
        last = eid
    return cells, first, last


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
    cells_seen, first_eid_global, last_eid_global = _cells_with_events(
        since_event_id, elog,
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
        prior_signatures: dict[str, str] = {}
        # If the prior commit exists, read its manifest to rebuild
        # prior_signatures — this gives us the baseline to fold new
        # events onto.
        if per_cell.commit_id:
            prior_manifest = (
                vroot / cell_id / "commits" / per_cell.commit_id / "manifest.json"
            )
            if prior_manifest.exists():
                try:
                    pm = json.loads(prior_manifest.read_text("utf-8"))
                    prior_signatures = dict(pm.get("signatures") or {})
                except Exception:
                    prior_signatures = {}

        # Project the cell's current desired state by folding events
        # since the cell's last applied event.
        since_cell = per_cell.last_applied_event_id or since_event_id
        cell_state = _cell_projection_from_prior_state(
            cell_id, prior_signatures, since_cell, elog,
        )
        new_commit_id = compute_commit_id(cell_state)

        prior_commit = per_cell.commit_id
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
            staged = _stage_commit(cell_state, new_commit_id, vroot)
            if _fail_before_swap_for_cells and cell_id in _fail_before_swap_for_cells:
                raise RuntimeError("simulated crash before pointer swap")
            _swap_current_pointer(vroot / cell_id, new_commit_id)

            # Emit commit_applied
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

    report.events_processed = sum(
        1 for _ in vector_events.read_events(elog)
    ) if first_eid_global else 0
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
