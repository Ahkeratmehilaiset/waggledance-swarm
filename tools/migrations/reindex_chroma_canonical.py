#!/usr/bin/env python3
"""
Phase 1 Migration: Add canonical_id metadata to ChromaDB documents.

For each document in waggle_memory, swarm_facts, corrections, episodes:
if it has agent_id in metadata, resolve to canonical and add canonical_id.

Usage:
    python tools/migrations/reindex_chroma_canonical.py [--dry-run]

Idempotent: skips documents that already have canonical_id set.
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.capabilities.aliasing import AliasRegistry

log = logging.getLogger("migration.chroma_canonical")

COLLECTIONS = ["waggle_memory", "swarm_facts", "corrections", "episodes"]
CHROMA_PATH = "data/chroma_db"
BATCH_SIZE = 100


def run(dry_run: bool = False) -> dict:
    """Add canonical_id metadata to all ChromaDB collections."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        import chromadb
    except ImportError:
        log.error("chromadb not installed. Run: pip install chromadb")
        return {}

    if not Path(CHROMA_PATH).exists():
        log.info("ChromaDB directory not found: %s (skipping)", CHROMA_PATH)
        return {}

    registry = AliasRegistry.from_yaml("configs/alias_registry.yaml")
    lookup = registry.build_legacy_to_canonical_map()
    log.info("Loaded alias registry: %d agents, %d lookup entries", len(registry), len(lookup))

    client = chromadb.PersistentClient(path=CHROMA_PATH)
    stats = {}
    mode = "[DRY RUN] " if dry_run else ""

    for coll_name in COLLECTIONS:
        try:
            coll = client.get_collection(coll_name)
        except Exception:
            log.info("%sCollection %s not found, skipping", mode, coll_name)
            continue

        count = coll.count()
        log.info("%sProcessing %s (%d documents)...", mode, coll_name, count)

        updated = 0
        offset = 0

        while offset < count:
            result = coll.get(
                limit=BATCH_SIZE,
                offset=offset,
                include=["metadatas"],
            )

            ids_to_update = []
            metas_to_update = []

            for doc_id, meta in zip(result["ids"], result["metadatas"]):
                if meta is None:
                    continue

                # Skip if canonical_id already set
                if meta.get("canonical_id"):
                    continue

                agent_id = meta.get("agent_id", "")
                if not agent_id:
                    continue

                canonical = lookup.get(agent_id.lower())
                if canonical:
                    new_meta = dict(meta)
                    new_meta["canonical_id"] = canonical
                    ids_to_update.append(doc_id)
                    metas_to_update.append(new_meta)

            if ids_to_update and not dry_run:
                coll.update(
                    ids=ids_to_update,
                    metadatas=metas_to_update,
                )

            updated += len(ids_to_update)
            offset += BATCH_SIZE

        stats[coll_name] = updated
        log.info("  %s: %d documents %s", coll_name, updated,
                 "(would be updated)" if dry_run else "updated")

    log.info("\n=== Summary ===")
    total = sum(stats.values())
    for coll, count in stats.items():
        log.info("  %s: %d", coll, count)
    log.info("  Total: %d documents %s", total,
             "(would be updated)" if dry_run else "updated")
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Add canonical_id to ChromaDB metadata")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be updated")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
