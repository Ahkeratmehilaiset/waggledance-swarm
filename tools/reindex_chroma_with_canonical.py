"""Reindex ChromaDB collections with canonical_id metadata.

Phase 1 migration step 3: Reads existing ChromaDB documents, looks up
canonical_id from the alias registry, and updates document metadata
with canonical_id for the new autonomy core.

Usage:
    python tools/reindex_chroma_with_canonical.py [--alias-db data/alias_registry.db]
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time

COLLECTIONS = ["waggle_memory", "swarm_facts", "corrections", "episodes"]
DEFAULT_ALIAS_DB = "data/alias_registry.db"
CHROMA_DIR = "data/chroma_db"


def load_alias_map(alias_db: str) -> dict:
    """Load legacy_id → canonical mapping."""
    if not os.path.exists(alias_db):
        return {}
    conn = sqlite3.connect(alias_db)
    rows = conn.execute("SELECT legacy_id, canonical FROM alias_registry").fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


def reindex_collection(client, collection_name: str,
                       alias_map: dict) -> dict:
    """Add canonical_id to all documents in a ChromaDB collection."""
    try:
        collection = client.get_or_create_collection(collection_name)
    except Exception as exc:
        return {"collection": collection_name, "status": "not_found", "error": str(exc)}

    count = collection.count()
    if count == 0:
        return {"collection": collection_name, "status": "empty", "documents": 0}

    batch_size = 100
    updated = 0
    skipped = 0

    offset = 0
    while offset < count:
        try:
            results = collection.get(
                limit=batch_size,
                offset=offset,
                include=["metadatas"],
            )
        except Exception:
            break

        ids = results.get("ids", [])
        metadatas = results.get("metadatas", [])

        if not ids:
            break

        update_ids = []
        update_metas = []

        for doc_id, meta in zip(ids, metadatas):
            meta = meta or {}
            if meta.get("canonical_id"):
                skipped += 1
                continue

            # Try to find canonical from agent_id in metadata
            agent_id = meta.get("agent_id", "")
            canonical = alias_map.get(agent_id, "")

            if not canonical and "source" in meta:
                canonical = alias_map.get(meta["source"], "")

            if canonical:
                meta["canonical_id"] = canonical
                update_ids.append(doc_id)
                update_metas.append(meta)
                updated += 1

        if update_ids:
            try:
                collection.update(ids=update_ids, metadatas=update_metas)
            except Exception as exc:
                print(f"  WARNING: batch update failed: {exc}")

        offset += batch_size

    return {
        "collection": collection_name,
        "status": "ok",
        "total_documents": count,
        "updated": updated,
        "skipped_already_set": skipped,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Reindex ChromaDB with canonical_id metadata")
    parser.add_argument("--alias-db", default=DEFAULT_ALIAS_DB)
    parser.add_argument("--chroma-dir", default=CHROMA_DIR)
    args = parser.parse_args()

    print("Loading alias map...")
    alias_map = load_alias_map(args.alias_db)
    print(f"  {len(alias_map)} mappings loaded")

    if not alias_map:
        print("No alias mappings. Run alias_registry_builder.py first.")
        sys.exit(1)

    try:
        import chromadb
        client = chromadb.PersistentClient(path=args.chroma_dir)
    except ImportError:
        print("chromadb not installed. Install with: pip install chromadb")
        sys.exit(1)
    except Exception as exc:
        print(f"Could not connect to ChromaDB at {args.chroma_dir}: {exc}")
        sys.exit(1)

    for coll_name in COLLECTIONS:
        print(f"\nReindexing {coll_name}...")
        result = reindex_collection(client, coll_name, alias_map)
        for k, v in result.items():
            print(f"  {k}: {v}")

    print("\nDone.")


if __name__ == "__main__":
    main()
