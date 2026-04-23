#!/usr/bin/env python3
"""B.1 — Compute per-cell centroids from the ledger.

Per v3 §1.2 + B.1: embedding-based cell assignment requires a centroid
vector per non-empty cell. Centroids are the L2-normalized mean of all
vectors in that cell's ledger entries.

Centroids are WRITTEN to data/faiss_staging/cell_centroids.json and are
consumed by:
  - tools/backfill_axioms_to_hex.py (audit step — flag triple disagreement)
  - waggledance routing (centroid-top-3 candidate cells per query)

Run:
    python tools/compute_cell_centroids.py
    python tools/compute_cell_centroids.py --source staging   # default
    python tools/compute_cell_centroids.py --source ledger    # recompute from ledger directly
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from collections import defaultdict

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
LEDGER_DIR = ROOT / "data" / "faiss_delta_ledger"
CENTROIDS_FILE = ROOT / "data" / "faiss_staging" / "cell_centroids.json"


def compute_from_ledger() -> dict:
    """Scan all ledger entries, compute L2-normalized centroid per cell."""
    cell_vectors: dict[str, list[list[float]]] = defaultdict(list)
    cell_solvers: dict[str, set[str]] = defaultdict(set)

    for cell_dir in sorted(LEDGER_DIR.iterdir()):
        if not cell_dir.is_dir():
            continue
        for ledger_file in sorted(cell_dir.glob("*.jsonl")):
            with open(ledger_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    cell_vectors[entry["cell_id"]].append(entry["vector"])
                    cell_solvers[entry["cell_id"]].add(entry["canonical_solver_id"])

    centroids = {}
    for cell, vectors in cell_vectors.items():
        if not vectors:
            continue
        arr = np.array(vectors, dtype=np.float64)
        mean = arr.mean(axis=0)
        # L2 normalize
        norm = np.linalg.norm(mean)
        if norm > 0:
            mean = mean / norm
        centroids[cell] = {
            "centroid": mean.tolist(),
            "vector_count": len(vectors),
            "canonical_solver_count": len(cell_solvers[cell]),
            "dim": arr.shape[1],
        }

    return centroids


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["ledger", "staging"], default="ledger")
    args = ap.parse_args()

    if args.source != "ledger":
        print("Only --source=ledger supported in this version (no staging FAISS yet)")
        return 1

    centroids = compute_from_ledger()

    if not centroids:
        print("No vectors found in ledger. Run tools/backfill_axioms_to_hex.py first.")
        return 1

    CENTROIDS_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Simplify for write — only centroid vector as plain list, metadata alongside
    write_data = {
        "generated_at": "2026-04-23",
        "source": "ledger",
        "cells": {
            cell: {
                "centroid": data["centroid"],
                "vector_count": data["vector_count"],
                "canonical_solver_count": data["canonical_solver_count"],
                "dim": data["dim"],
            }
            for cell, data in centroids.items()
        },
    }

    CENTROIDS_FILE.write_text(
        json.dumps(write_data, ensure_ascii=False), encoding="utf-8"
    )

    print(f"Computed centroids for {len(centroids)} non-empty cells:")
    for cell, data in sorted(centroids.items()):
        print(f"  {cell:10} vectors={data['vector_count']:3}  "
              f"solvers={data['canonical_solver_count']:2}  dim={data['dim']}")
    print()
    print(f"Written: {CENTROIDS_FILE.relative_to(ROOT)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
