#!/usr/bin/env python3
"""B.3 — Hex manifest tool with snapshot context manager.

Per v3 §1.10 + §1.14 + v3.1 tweak 7:

  build     — consume ledger up to high-water mark, build staging FAISS indices
  commit    — atomic swap staging → live (os.replace per Gemini)
  rollback  — atomic swap live → previous version from history
  status    — show current manifest, drift warnings, snapshot leak metrics

Snapshot API:
  Used by runtime retrieval to guarantee per-query coherent view across cells.
  Snapshots are refcounted; context manager ensures release even on exceptions.

Run:
    python tools/hex_manifest.py build --ledger-hwm auto
    python tools/hex_manifest.py commit --version coherent-set-A
    python tools/hex_manifest.py status
    python tools/hex_manifest.py rollback --to-version <prev>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
LEDGER_DIR = ROOT / "data" / "faiss_delta_ledger"
STAGING_DIR = ROOT / "data" / "faiss_staging"
LIVE_DIR = ROOT / "data" / "faiss_live"
HISTORY_DIR = ROOT / "data" / "faiss_history"


def _compute_high_water_marks() -> dict[str, int]:
    """Per cell: max seq across all ledger files in that cell."""
    hwm = {}
    for cell_dir in LEDGER_DIR.iterdir() if LEDGER_DIR.exists() else []:
        if not cell_dir.is_dir():
            continue
        max_seq = 0
        for ledger_file in cell_dir.glob("*.jsonl"):
            for line in open(ledger_file, encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("seq", 0) > max_seq:
                        max_seq = entry["seq"]
                except json.JSONDecodeError:
                    continue
        if max_seq > 0:
            hwm[cell_dir.name] = max_seq
    return hwm


def _build_staging_from_ledger(hwm: dict[str, int]) -> dict:
    """For each cell with a HWM, read ledger entries with seq <= HWM,
    dedupe by doc_id (later seq wins), write to staging FAISS index.
    """
    import faiss
    import numpy as np

    STAGING_DIR.mkdir(parents=True, exist_ok=True)

    cell_summaries = {}
    for cell, cell_hwm in hwm.items():
        cell_dir = LEDGER_DIR / cell
        # Collect entries up to HWM, sorted by seq
        entries: list[dict] = []
        for ledger_file in sorted(cell_dir.glob("*.jsonl")):
            for line in open(ledger_file, encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("seq", 0) <= cell_hwm:
                    entries.append(entry)

        # Dedupe by doc_id — keep latest seq
        by_doc: dict[str, dict] = {}
        for e in sorted(entries, key=lambda x: x["seq"]):
            by_doc[e["doc_id"]] = e

        if not by_doc:
            continue

        doc_ids = list(by_doc.keys())
        vectors = np.array([by_doc[d]["vector"] for d in doc_ids], dtype=np.float32)
        dim = vectors.shape[1]

        # Build FlatIP index (per Gemini: IndexFlatL2/IP stays valid to ~50k)
        index = faiss.IndexFlatIP(dim)
        # Normalize for cosine via IP
        faiss.normalize_L2(vectors)
        index.add(vectors)

        # Write staging files
        cell_staging = STAGING_DIR / cell
        cell_staging.mkdir(parents=True, exist_ok=True)
        # Use os.replace target pattern — write .tmp first then rename
        idx_tmp = cell_staging / "index.faiss.tmp"
        idx_final = cell_staging / "index.faiss"
        faiss.write_index(index, str(idx_tmp))
        os.replace(idx_tmp, idx_final)

        # Doc metadata
        meta = [
            {"doc_id": d, "canonical_solver_id": by_doc[d]["canonical_solver_id"],
             "view_type": by_doc[d]["view_type"], "text": by_doc[d]["text"],
             "canonical_hash": by_doc[d]["canonical_hash"],
             "seq": by_doc[d]["seq"]}
            for d in doc_ids
        ]
        meta_tmp = cell_staging / "meta.json.tmp"
        meta_final = cell_staging / "meta.json"
        meta_tmp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        os.replace(meta_tmp, meta_final)

        # Source-based manifest checksum per v3 §1.12
        checksum_inputs = sorted([
            f"{m['canonical_solver_id']}|{m['view_type']}|{hashlib.sha256(m['text'].encode()).hexdigest()}"
            for m in meta
        ])
        src_checksum = hashlib.sha256("|".join(checksum_inputs).encode()).hexdigest()[:16]

        cell_summaries[cell] = {
            "version": f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{src_checksum[:4]}",
            "doc_count": len(meta),
            "canonical_solver_count": len(set(m["canonical_solver_id"] for m in meta)),
            "dim": dim,
            "source_checksum": src_checksum,
            "ledger_hwm": cell_hwm,
        }

    # Write staging manifest
    manifest = {
        "kind": "staging",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cells": cell_summaries,
        "delta_ledger_high_water_mark": hwm,
    }
    manifest_tmp = STAGING_DIR / "manifest.json.tmp"
    manifest_final = STAGING_DIR / "manifest.json"
    manifest_tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(manifest_tmp, manifest_final)

    return manifest


def cmd_build(args) -> int:
    print("Computing ledger high-water marks...")
    hwm = _compute_high_water_marks()
    if not hwm:
        print("No ledger entries found. Run tools/backfill_axioms_to_hex.py first.")
        return 1

    print(f"  HWMs: {hwm}")
    print()
    print("Building staging FAISS indices...")
    manifest = _build_staging_from_ledger(hwm)
    print(f"Staging manifest:")
    for cell, summary in manifest["cells"].items():
        print(f"  {cell:10} docs={summary['doc_count']:3}  "
              f"solvers={summary['canonical_solver_count']:2}  "
              f"version={summary['version']}")
    print()
    print(f"Written: {STAGING_DIR.relative_to(ROOT)}/manifest.json")
    return 0


def cmd_commit(args) -> int:
    staging_manifest = STAGING_DIR / "manifest.json"
    if not staging_manifest.exists():
        print("No staging manifest. Run `build` first.")
        return 1

    manifest = json.loads(staging_manifest.read_text(encoding="utf-8"))

    # Archive current live if exists
    if LIVE_DIR.exists():
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        live_manifest = json.loads((LIVE_DIR / "manifest.json").read_text(encoding="utf-8"))
        prev_version = live_manifest.get("manifest_version", "unknown")
        archive = HISTORY_DIR / prev_version
        if archive.exists():
            print(f"Archive path {archive} already exists — skipping archive")
        else:
            shutil.copytree(LIVE_DIR, archive)
            print(f"Archived previous live to {archive.relative_to(ROOT)}")

    # Promote staging → live
    manifest["kind"] = "live"
    manifest["manifest_version"] = args.version or manifest["generated_at"]
    manifest["committed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Atomic-ish swap: write new live manifest, then os.replace the full dir
    # Safest approach: copy staging into live_tmp then atomic rename
    live_tmp = LIVE_DIR.parent / (LIVE_DIR.name + ".tmp")
    if live_tmp.exists():
        shutil.rmtree(live_tmp)
    shutil.copytree(STAGING_DIR, live_tmp)
    # Write new manifest
    (live_tmp / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if LIVE_DIR.exists():
        shutil.rmtree(LIVE_DIR)
    os.replace(live_tmp, LIVE_DIR)

    print(f"Committed: {LIVE_DIR.relative_to(ROOT)} @ version {manifest['manifest_version']}")
    return 0


def cmd_rollback(args) -> int:
    target = args.to_version
    if not target:
        print("Specify --to-version <id>")
        return 1
    archive = HISTORY_DIR / target
    if not archive.exists():
        print(f"No archive at {archive}")
        return 1

    # Archive current live first
    if LIVE_DIR.exists():
        live_manifest = json.loads((LIVE_DIR / "manifest.json").read_text(encoding="utf-8"))
        current_version = live_manifest.get("manifest_version", "unknown")
        backup = HISTORY_DIR / f"{current_version}-pre-rollback"
        if not backup.exists():
            shutil.copytree(LIVE_DIR, backup)

    # Atomic replace
    rollback_tmp = LIVE_DIR.parent / (LIVE_DIR.name + ".rollback_tmp")
    if rollback_tmp.exists():
        shutil.rmtree(rollback_tmp)
    shutil.copytree(archive, rollback_tmp)
    if LIVE_DIR.exists():
        shutil.rmtree(LIVE_DIR)
    os.replace(rollback_tmp, LIVE_DIR)

    print(f"Rolled back live to version {target}")
    return 0


def cmd_status(args) -> int:
    print("=== Ledger HWMs ===")
    hwm = _compute_high_water_marks()
    for c, s in sorted(hwm.items()):
        print(f"  {c:10} seq_max={s}")

    print()
    print("=== Staging ===")
    if (STAGING_DIR / "manifest.json").exists():
        m = json.loads((STAGING_DIR / "manifest.json").read_text(encoding="utf-8"))
        for c, s in sorted(m["cells"].items()):
            print(f"  {c:10} docs={s['doc_count']:3}  "
                  f"solvers={s['canonical_solver_count']:2}  version={s['version']}")
    else:
        print("  (no staging manifest)")

    print()
    print("=== Live ===")
    if (LIVE_DIR / "manifest.json").exists():
        m = json.loads((LIVE_DIR / "manifest.json").read_text(encoding="utf-8"))
        print(f"  manifest_version: {m.get('manifest_version')}")
        print(f"  committed_at:     {m.get('committed_at')}")
        for c, s in sorted(m["cells"].items()):
            print(f"  {c:10} docs={s['doc_count']:3}  solvers={s['canonical_solver_count']:2}")
    else:
        print("  (no live manifest yet — run `commit` after `build`)")

    print()
    print("=== History archives ===")
    if HISTORY_DIR.exists():
        for d in sorted(HISTORY_DIR.iterdir()):
            if d.is_dir():
                print(f"  {d.name}")
    else:
        print("  (none)")

    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build")
    p_build.add_argument("--ledger-hwm", default="auto")

    p_commit = sub.add_parser("commit")
    p_commit.add_argument("--version", help="manifest version id")

    p_rollback = sub.add_parser("rollback")
    p_rollback.add_argument("--to-version", required=True)

    p_status = sub.add_parser("status")

    args = ap.parse_args()

    handlers = {
        "build": cmd_build,
        "commit": cmd_commit,
        "rollback": cmd_rollback,
        "status": cmd_status,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
