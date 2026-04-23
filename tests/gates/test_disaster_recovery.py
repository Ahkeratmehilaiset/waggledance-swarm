"""B.6 — Disaster recovery dry run.

Per v3 §1.16: FAISS live can be rebuilt from:
  configs/axioms/*.yaml
  + data/faiss_delta_ledger/<cell>/*.jsonl (up to manifest high-water mark)
  + embedding model + version
  + backfill config

This test simulates corruption of data/faiss_live/, then rebuilds from
sources alone and verifies:
  - canonical_solver_ids match
  - source text hashes match
  - view counts match
  - manifest source checksum matches

Run (requires Ollama running):
  .venv/Scripts/python.exe tests/gates/test_disaster_recovery.py
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
LIVE_DIR = ROOT / "data" / "faiss_live"
STAGING_DIR = ROOT / "data" / "faiss_staging"
LEDGER_DIR = ROOT / "data" / "faiss_delta_ledger"
OUTPUT = ROOT / "docs" / "plans" / "phase_B6_disaster_recovery_results.json"


def _read_cell_meta(base_dir: Path) -> dict:
    """Return {cell_id: [{doc_id, canonical_solver_id, text_hash, view_type}...]}."""
    result = {}
    for cell_dir in base_dir.iterdir():
        if not cell_dir.is_dir() or cell_dir.name == "__pycache__":
            continue
        meta_file = cell_dir / "meta.json"
        if not meta_file.exists():
            continue
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        result[cell_dir.name] = sorted(
            [
                {
                    "doc_id": m["doc_id"],
                    "canonical_solver_id": m["canonical_solver_id"],
                    "view_type": m["view_type"],
                    "text_hash": hashlib.sha256(m["text"].encode()).hexdigest()[:16],
                }
                for m in meta
            ],
            key=lambda x: x["doc_id"],
        )
    return result


def main() -> int:
    print("=== Phase B.6 Disaster Recovery dry run ===")

    # Precondition: staging must exist (built by tools/hex_manifest.py build)
    if not STAGING_DIR.exists() or not (STAGING_DIR / "manifest.json").exists():
        print("ERROR: no staging manifest. Run tools/hex_manifest.py build first.")
        return 1

    if not LEDGER_DIR.exists():
        print("ERROR: no ledger. Run tools/backfill_axioms_to_hex.py first.")
        return 1

    # Record original staging state (we use staging as reference since live
    # may not yet be committed)
    print("Recording original staging state...")
    original_meta = _read_cell_meta(STAGING_DIR)
    original_manifest = json.loads((STAGING_DIR / "manifest.json").read_text(encoding="utf-8"))
    orig_cell_count = len(original_manifest.get("cells", {}))
    orig_solver_count = sum(
        c["canonical_solver_count"] for c in original_manifest["cells"].values()
    )
    print(f"  original: {orig_cell_count} cells, {orig_solver_count} unique solvers")

    # Simulate "corruption" by moving staging aside
    print()
    print("Simulating staging corruption (moving aside)...")
    backup = STAGING_DIR.with_suffix(".disaster_backup")
    if backup.exists():
        shutil.rmtree(backup)
    shutil.move(str(STAGING_DIR), str(backup))

    try:
        # Rebuild from ledger using hex_manifest.py build
        print("Rebuilding from ledger...")
        py = ROOT / ".venv" / "Scripts" / "python.exe"
        rc = subprocess.call(
            [str(py), str(ROOT / "tools" / "hex_manifest.py"), "build"],
            cwd=str(ROOT),
            timeout=120,
        )
        if rc != 0:
            print(f"ERROR: rebuild returned rc={rc}")
            return 1

        # Compare
        print("Comparing rebuilt vs original...")
        rebuilt_meta = _read_cell_meta(STAGING_DIR)

        diffs = []

        # Cell set equal?
        orig_cells = set(original_meta.keys())
        new_cells = set(rebuilt_meta.keys())
        if orig_cells != new_cells:
            diffs.append(
                f"cell set differs: missing={orig_cells - new_cells}, extra={new_cells - orig_cells}"
            )

        # Per-cell doc set equal?
        for cell in orig_cells & new_cells:
            orig_docs = {(d["doc_id"], d["canonical_solver_id"], d["text_hash"], d["view_type"])
                         for d in original_meta[cell]}
            new_docs = {(d["doc_id"], d["canonical_solver_id"], d["text_hash"], d["view_type"])
                         for d in rebuilt_meta[cell]}
            if orig_docs != new_docs:
                diffs.append(f"cell {cell}: doc set differs")
                missing = orig_docs - new_docs
                extra = new_docs - orig_docs
                for m in list(missing)[:3]:
                    diffs.append(f"    missing: {m}")
                for e in list(extra)[:3]:
                    diffs.append(f"    extra: {e}")

        # Manifest source checksum equal?
        new_manifest = json.loads((STAGING_DIR / "manifest.json").read_text(encoding="utf-8"))
        for cell in orig_cells & new_cells:
            orig_cs = original_manifest["cells"][cell].get("source_checksum")
            new_cs = new_manifest["cells"][cell].get("source_checksum")
            if orig_cs != new_cs:
                diffs.append(f"cell {cell}: source_checksum differs ({orig_cs} → {new_cs})")

        # Result
        passed = not diffs
        result = {
            "gate": "phase_B.6_disaster_recovery",
            "verdict": "pass" if passed else "fail",
            "orig_cells": sorted(orig_cells),
            "rebuilt_cells": sorted(new_cells),
            "orig_solver_count": orig_solver_count,
            "diffs": diffs,
            "invariant": "staging rebuild from configs/axioms + ledger + embedding model must produce identical source checksums",
        }
        OUTPUT.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

        print()
        print(f"=== Verdict: {'PASS' if passed else 'FAIL'} ===")
        if diffs:
            print("Diffs found:")
            for d in diffs:
                print(f"  {d}")
        else:
            print(f"Rebuilt staging is identical to original (source-based checksum match).")
        print(f"Saved: {OUTPUT.relative_to(ROOT)}")

        return 0 if passed else 1

    finally:
        # Cleanup — restore original as the canonical staging
        if STAGING_DIR.exists():
            shutil.rmtree(STAGING_DIR)
        shutil.move(str(backup), str(STAGING_DIR))
        print(f"\nRestored original staging (disaster backup removed).")


if __name__ == "__main__":
    sys.exit(main())
