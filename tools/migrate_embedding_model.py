#!/usr/bin/env python3
"""B.4 — Embedding model migration scaffold.

Per v3 §1.11: when the embedding model upgrades (e.g. nomic-embed-text v1.5
→ v2, or switch to bge-m3, etc.), the entire corpus must be re-embedded.
This script documents + executes the procedure.

**This is a scaffold — not run during normal backfill.** Use when:
  - Ollama updates to a new nomic-embed-text major version
  - You switch embedding providers
  - You want to compare retrieval quality across embedding models

Procedure per v3 §1.11:
  1. Read current manifest_version (data/faiss_live/manifest.json)
  2. For each cell:
     - Read all docs (text + canonical_solver_id) from current ledger
     - Re-embed text with NEW model via tools/backfill_axioms_to_hex.py
       (with --embedding-model flag) — writes new ledger entries with new model tag
     - A NEW manifest_version is produced when hex_manifest.py build runs
  3. Run Phase C oracle validation on new staging
  4. If pass: atomic swap manifest_version → new (tools/hex_manifest.py commit)
  5. If fail: discard staging, report

Embedding model is recorded in every trace, so old traces remain
interpretable after migration.

Usage:
    python tools/migrate_embedding_model.py \\
        --from nomic-embed-text:v1.5 \\
        --to nomic-embed-text:v2.0 \\
        --dry-run

NOTE: Currently a placeholder. Full implementation needed when first
real migration happens. See v3 §1.11 for rationale.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="from_model", required=True)
    ap.add_argument("--to", dest="to_model", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    print(f"Embedding model migration scaffold")
    print(f"  from: {args.from_model}")
    print(f"  to:   {args.to_model}")
    print()
    print("⚠ This is a placeholder. Full implementation procedure:")
    print()
    print("  1. Verify new model is available via Ollama:")
    print(f"     curl -X POST http://localhost:11434/api/embed \\")
    print(f"         -d '{{\"model\":\"{args.to_model}\",\"input\":[\"test\"]}}'")
    print()
    print("  2. Run backfill with new model (requires --embedding-model flag")
    print("     to be added to tools/backfill_axioms_to_hex.py — TBD):")
    print(f"     python tools/backfill_axioms_to_hex.py --embedding-model {args.to_model}")
    print()
    print("  3. Compute centroids from NEW ledger entries only:")
    print(f"     python tools/compute_cell_centroids.py")
    print()
    print("  4. Build staging with NEW embeddings:")
    print(f"     python tools/hex_manifest.py build")
    print()
    print("  5. Run Phase C oracle validation:")
    print(f"     python tools/shadow_route_three_way.py --source oracle")
    print()
    print("  6. If pass: commit new manifest version:")
    print(f"     python tools/hex_manifest.py commit --version {args.to_model}-migration")
    print()
    print("  7. If fail: discard staging, investigate, stay on old model:")
    print(f"     python tools/hex_manifest.py rollback --to-version <previous>")
    print()
    print("Keep old ledger entries — they document the embedding model version,")
    print("so old traces remain interpretable for audit even after migration.")

    if args.dry_run:
        print("\n(dry run — no changes made)")
        return 0

    print("\nFull implementation pending first real migration.")
    print("No changes made. Re-run with --dry-run for procedure docs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
