#!/usr/bin/env python3
"""Stage-1 migration tool — generate `data/vector/` alongside the
existing `data/faiss_staging/` and `data/faiss_delta_ledger/` trees.

Per docs/architecture/MAGMA_FAISS_SCALING.md §7, Stage 1 ships as an
ADDITIVE copy. Runtime still reads from the legacy paths while a live
campaign is in flight. The physical switchover (delete legacy,
repoint runtime at `data/vector/`) is a separate reviewed commit that
lands after the campaign completes.

Usage:
    python tools/migrate_to_vector_root.py                # dry-run
    python tools/migrate_to_vector_root.py --apply        # copy
    python tools/migrate_to_vector_root.py --verify       # verify
    python tools/migrate_to_vector_root.py --apply --force  # reapply

The `--verify` mode is byte-identical only — if legacy content changes
after migration (new ledger entries during live campaign), the verify
is expected to fail on those cells. That is not a bug, it's the
reason Stage 1 is a snapshot, not a sync.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEGACY_STAGING = ROOT / "data" / "faiss_staging"
LEGACY_LEDGER = ROOT / "data" / "faiss_delta_ledger"
VECTOR_ROOT = ROOT / "data" / "vector"

CELLS = (
    "general", "thermal", "energy", "safety",
    "seasonal", "math", "system", "learning",
)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_staging_manifest() -> dict:
    p = LEGACY_STAGING / "manifest.json"
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _count_ledger_entries(cell: str) -> int:
    cell_dir = LEGACY_LEDGER / cell
    if not cell_dir.exists():
        return 0
    return sum(1 for _ in cell_dir.glob("*.jsonl"))


def _latest_ledger_hwm(cell: str, manifest: dict) -> int | None:
    entries = (manifest.get("cells") or {}).get(cell) or {}
    return entries.get("ledger_hwm")


def _rel_or_abs(path: Path) -> str:
    """Path relative to ROOT if possible, otherwise the POSIX
    absolute form. Lets the same tool run from tests' tmp_path trees."""
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def plan_migration(vector_root: Path = VECTOR_ROOT) -> dict:
    """Describe what the migration would do. Pure function — no writes."""
    manifest = _load_staging_manifest()
    plan = {
        "source_root": _rel_or_abs(LEGACY_STAGING),
        "ledger_root": _rel_or_abs(LEGACY_LEDGER),
        "target_root": _rel_or_abs(vector_root),
        "cells": [],
    }
    # Also copy top-level centroids + manifest
    top_level_files: list[dict] = []
    for name in ("cell_centroids.json", "manifest.json"):
        src = LEGACY_STAGING / name
        if src.exists():
            top_level_files.append({
                "name": name,
                "source": _rel_or_abs(src),
                "bytes": src.stat().st_size,
            })
    plan["top_level"] = top_level_files

    for cell in CELLS:
        src_dir = LEGACY_STAGING / cell
        if not src_dir.exists():
            continue
        cell_entry = (manifest.get("cells") or {}).get(cell) or {}
        files: list[dict] = []
        for item in sorted(src_dir.glob("*")):
            if not item.is_file():
                continue
            files.append({
                "name": item.name,
                "bytes": item.stat().st_size,
            })
        plan["cells"].append({
            "cell": cell,
            "files": files,
            "ledger_entries": _count_ledger_entries(cell),
            "version": cell_entry.get("version"),
            "doc_count": cell_entry.get("doc_count"),
            "dim": cell_entry.get("dim"),
            "source_checksum": cell_entry.get("source_checksum"),
        })
    return plan


def _write_per_cell_manifest(cell_dir: Path, cell: str,
                              cell_entry: dict,
                              index_sha256: str,
                              ledger_count: int) -> None:
    manifest = {
        "schema_version": 1,
        "cell_id": cell,
        "dim": cell_entry.get("dim"),
        "doc_count": cell_entry.get("doc_count"),
        "canonical_solver_count": cell_entry.get("canonical_solver_count"),
        "source_version": cell_entry.get("version"),
        "source_checksum": cell_entry.get("source_checksum"),
        "ledger_hwm": cell_entry.get("ledger_hwm"),
        "ledger_entries": ledger_count,
        "index_checksum": f"sha256:{index_sha256}",
    }
    (cell_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _write_per_cell_commit(cell_dir: Path, cell: str,
                            cell_entry: dict,
                            index_sha256: str,
                            ledger_count: int) -> None:
    """Commit.json is the MAGMA-facing projection pointer.

    Stage 1 has no live event stream yet, so `faiss_commit_id` is
    derived from the source checksum + ledger hwm so that a later
    stage can replay events and verify the commit they produced
    matches this recorded id.
    """
    commit_id_blob = json.dumps({
        "cell": cell,
        "source_version": cell_entry.get("version"),
        "source_checksum": cell_entry.get("source_checksum"),
        "ledger_hwm": cell_entry.get("ledger_hwm"),
        "index_checksum": index_sha256,
    }, sort_keys=True)
    commit_id = "faiss_" + hashlib.sha256(
        commit_id_blob.encode("utf-8")
    ).hexdigest()[:16]

    commit = {
        "schema_version": 1,
        "cell_id": cell,
        "faiss_commit_id": commit_id,
        "produced_at": _utc_now_iso(),
        "source": {
            "stage": 0,   # stage 0 = legacy staging tree
            "staging_root": _rel_or_abs(LEGACY_STAGING),
            "ledger_root": _rel_or_abs(LEGACY_LEDGER),
            "ledger_hwm": cell_entry.get("ledger_hwm"),
            "ledger_entries": ledger_count,
        },
        "index_checksum": f"sha256:{index_sha256}",
        "vector_count": cell_entry.get("doc_count"),
    }
    (cell_dir / "commit.json").write_text(
        json.dumps(commit, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def apply_migration(vector_root: Path = VECTOR_ROOT,
                    force: bool = False) -> dict:
    """Materialize data/vector/ from the current legacy tree. Idempotent
    when `force=False` — if the target directory already exists for a
    cell, that cell is skipped. With `force=True`, existing target is
    overwritten."""
    if not LEGACY_STAGING.exists():
        raise FileNotFoundError(
            f"{LEGACY_STAGING} missing — cannot migrate"
        )
    manifest = _load_staging_manifest()
    vector_root.mkdir(parents=True, exist_ok=True)

    # Top-level files (cell_centroids + global manifest). These are
    # snapshots; Stage 2 replaces them with a streaming projection.
    top_level_copied: list[str] = []
    for name in ("cell_centroids.json", "manifest.json"):
        src = LEGACY_STAGING / name
        dst = vector_root / name
        if not src.exists():
            continue
        if dst.exists() and not force:
            continue
        shutil.copy2(src, dst)
        top_level_copied.append(name)

    cells_done: list[dict] = []
    for cell in CELLS:
        src_dir = LEGACY_STAGING / cell
        if not src_dir.exists():
            continue
        cell_dir = vector_root / cell
        if cell_dir.exists() and not force:
            cells_done.append({"cell": cell, "status": "skipped-exists"})
            continue
        cell_dir.mkdir(parents=True, exist_ok=True)

        index_sha = ""
        for item in sorted(src_dir.glob("*")):
            if not item.is_file():
                continue
            dst = cell_dir / item.name
            shutil.copy2(item, dst)
            if item.name == "index.faiss":
                index_sha = _sha256(dst)

        cell_entry = (manifest.get("cells") or {}).get(cell) or {}
        ledger_count = _count_ledger_entries(cell)
        _write_per_cell_manifest(cell_dir, cell, cell_entry,
                                   index_sha, ledger_count)
        _write_per_cell_commit(cell_dir, cell, cell_entry,
                                 index_sha, ledger_count)
        cells_done.append({
            "cell": cell,
            "status": "copied",
            "index_sha256": index_sha,
            "ledger_entries": ledger_count,
        })

    return {
        "vector_root": vector_root.as_posix(),
        "top_level_copied": top_level_copied,
        "cells": cells_done,
    }


def verify_migration(vector_root: Path = VECTOR_ROOT) -> dict:
    """Compare each cell's index.faiss + meta.json between legacy
    staging and vector_root. Snapshot-strict: any legacy change after
    migration will surface as drift."""
    results: dict = {"vector_root": vector_root.as_posix(),
                     "drift": [], "ok": []}
    for cell in CELLS:
        src_dir = LEGACY_STAGING / cell
        dst_dir = vector_root / cell
        if not src_dir.exists():
            continue
        if not dst_dir.exists():
            results["drift"].append({
                "cell": cell, "reason": "target missing",
            })
            continue
        for name in ("index.faiss", "meta.json"):
            sp, dp = src_dir / name, dst_dir / name
            if not sp.exists() and not dp.exists():
                continue
            if not sp.exists():
                results["drift"].append({
                    "cell": cell, "file": name,
                    "reason": "source missing",
                })
                continue
            if not dp.exists():
                results["drift"].append({
                    "cell": cell, "file": name,
                    "reason": "target missing",
                })
                continue
            if _sha256(sp) != _sha256(dp):
                results["drift"].append({
                    "cell": cell, "file": name,
                    "reason": "checksum mismatch",
                })
            else:
                results["ok"].append(f"{cell}/{name}")
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vector-root", type=Path, default=VECTOR_ROOT)
    ap.add_argument("--apply", action="store_true",
                    help="Write the target tree (default is dry-run plan)")
    ap.add_argument("--verify", action="store_true",
                    help="Compare legacy and target byte-for-byte")
    ap.add_argument("--force", action="store_true",
                    help="Overwrite existing target files when --apply")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.apply and args.verify:
        print("choose one of --apply or --verify, not both", file=sys.stderr)
        return 2

    if args.verify:
        out = verify_migration(args.vector_root)
    elif args.apply:
        out = apply_migration(args.vector_root, force=args.force)
    else:
        out = {"mode": "dry-run", "plan": plan_migration(args.vector_root)}

    if args.json:
        print(json.dumps(out, indent=2, default=str))
    else:
        mode = ("verify" if args.verify
                else "apply" if args.apply
                else "dry-run")
        print(f"mode: {mode}")
        if args.verify:
            print(f"ok: {len(out['ok'])}")
            if out["drift"]:
                print(f"drift: {len(out['drift'])}")
                for d in out["drift"][:10]:
                    print(f"  - {d}")
        elif args.apply:
            print(f"vector_root: {out['vector_root']}")
            print(f"top_level: {out['top_level_copied']}")
            for c in out["cells"]:
                print(f"  {c['cell']:10} {c['status']}")
        else:
            plan = out["plan"]
            print(f"source: {plan['source_root']}")
            print(f"target: {plan['target_root']}")
            print(f"cells:  {len(plan['cells'])}")
            for c in plan["cells"]:
                files = ", ".join(f["name"] for f in c["files"])
                print(f"  {c['cell']:10} files=({files}) "
                      f"ledger={c['ledger_entries']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
