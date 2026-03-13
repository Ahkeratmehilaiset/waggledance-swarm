"""Index bee-domain knowledge YAMLs into FAISS 'bee_knowledge' collection.

Processes configs/knowledge/cottage/*.yaml files.
Each month becomes multiple searchable chunks:
  - Month overview (month name + season + tasks summary)
  - Individual task chunks (one per task)
  - Keyword chunk (for routing)

Incremental: skips already-indexed doc_ids.
Requires Ollama with nomic-embed-text.
"""

import sys
import yaml
import requests
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.faiss_store import FaissRegistry

OLLAMA_URL = "http://localhost:11434/api/embed"
EMBED_MODEL = "nomic-embed-text"
DOC_PREFIX = "search_document: "
BATCH_SIZE = 16

PROJECT_ROOT = Path(__file__).parent.parent
KNOWLEDGE_DIR = PROJECT_ROOT / "configs" / "knowledge"


def embed_batch(texts: list) -> np.ndarray:
    all_vecs = []
    for i in range(0, len(texts), BATCH_SIZE):
        chunk = texts[i:i + BATCH_SIZE]
        resp = requests.post(
            OLLAMA_URL,
            json={"model": EMBED_MODEL,
                  "input": [DOC_PREFIX + t for t in chunk]},
            timeout=60,
        )
        resp.raise_for_status()
        all_vecs.extend(resp.json()["embeddings"])
        done = min(i + BATCH_SIZE, len(texts))
        print(f"  {done}/{len(texts)} embedded", end="\r", flush=True)
    print()
    return np.array(all_vecs, dtype=np.float32)


def _extract_knowledge_chunks(yaml_file: Path) -> list:
    """Return (doc_id, text, meta) tuples from a knowledge YAML."""
    try:
        with open(yaml_file, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
    except Exception as exc:
        print(f"  [!] Failed to load {yaml_file.name}: {exc}")
        return []
    if not doc or "knowledge_id" not in doc:
        return []

    kid = doc["knowledge_id"]
    domain = doc.get("domain", yaml_file.parent.name)
    k_name = doc.get("knowledge_name", kid)
    chunks = []

    base_meta = {"knowledge_id": kid, "domain": domain, "type": "bee_knowledge"}

    # Main description chunk
    chunks.append((
        f"know:{kid}:main",
        f"{k_name}: {doc.get('description', '')}",
        {**base_meta, "chunk_type": "main"},
    ))

    # Monthly task chunks
    for month_data in doc.get("months", []):
        month_num = month_data.get("month", 0)
        name_fi = month_data.get("month_name_fi", "")
        name_en = month_data.get("month_name_en", "")
        season_fi = month_data.get("season_fi", "")
        season_en = month_data.get("season_en", "")

        # Combined FI+EN task text for the month
        fi_tasks = month_data.get("tasks_fi", [])
        en_tasks = month_data.get("tasks_en", [])
        risks = month_data.get("risk_factors_fi", [])

        # Overview chunk (month name + season + first 2 tasks)
        overview = (
            f"{name_fi}/{name_en} ({season_fi}/{season_en}): "
            + "; ".join(fi_tasks[:2])
        )
        chunks.append((
            f"know:{kid}:month:{month_num:02d}:overview",
            overview[:500],
            {**base_meta, "chunk_type": "month_overview",
             "month": month_num, "month_name_fi": name_fi, "month_name_en": name_en},
        ))

        # Individual FI task chunks
        for i, task in enumerate(fi_tasks):
            chunks.append((
                f"know:{kid}:month:{month_num:02d}:fi:{i}",
                f"{name_fi} - {task}",
                {**base_meta, "chunk_type": "task_fi", "month": month_num,
                 "month_name_fi": name_fi},
            ))

        # Individual EN task chunks
        for i, task in enumerate(en_tasks):
            chunks.append((
                f"know:{kid}:month:{month_num:02d}:en:{i}",
                f"{name_en} - {task}",
                {**base_meta, "chunk_type": "task_en", "month": month_num,
                 "month_name_en": name_en},
            ))

        # Risk/keyword summary chunk
        if risks or month_data.get("keywords"):
            kws = month_data.get("keywords", [])
            risk_text = (
                f"{name_fi} risks: {', '.join(risks)}. "
                f"Keywords: {', '.join(kws[:10])}"
            )
            chunks.append((
                f"know:{kid}:month:{month_num:02d}:keywords",
                risk_text[:400],
                {**base_meta, "chunk_type": "month_keywords", "month": month_num},
            ))

    # General facts
    for i, fact in enumerate(doc.get("general_facts_fi", [])):
        chunks.append((
            f"know:{kid}:fact:fi:{i}",
            fact,
            {**base_meta, "chunk_type": "general_fact_fi"},
        ))
    for i, fact in enumerate(doc.get("general_facts_en", [])):
        chunks.append((
            f"know:{kid}:fact:en:{i}",
            fact,
            {**base_meta, "chunk_type": "general_fact_en"},
        ))

    return chunks


def main():
    print("\n=== index_bee_knowledge: Bee Knowledge FAISS Indexer ===")

    # Check Ollama
    try:
        r = requests.get("http://localhost:11434/api/version", timeout=5)
        print(f"  Ollama: OK (v{r.json().get('version', '?')})")
    except Exception as exc:
        print(f"  [!] Ollama not reachable: {exc}")
        return 1

    registry = FaissRegistry()
    col = registry.get_or_create("bee_knowledge")
    existing = set(col._doc_ids)
    print(f"  Existing bee_knowledge vectors: {col.count}")

    # Collect chunks from all knowledge YAMLs
    all_chunks = []
    yaml_count = 0
    for yaml_file in sorted(KNOWLEDGE_DIR.rglob("*.yaml")):
        chunks = _extract_knowledge_chunks(yaml_file)
        if chunks:
            yaml_count += 1
            all_chunks.extend(chunks)
            print(f"  Loaded {yaml_file.name}: {len(chunks)} chunks")

    print(f"  Total: {yaml_count} YAML files, {len(all_chunks)} chunks")

    # Filter to new only
    new = [(d, t, m) for d, t, m in all_chunks if d not in existing]
    print(f"  New chunks to index: {len(new)}")

    if not new:
        print("  Nothing to add — already up to date.")
        return 0

    new_doc_ids = [c[0] for c in new]
    new_texts = [c[1] for c in new]
    new_metas = [c[2] for c in new]

    print(f"  Embedding {len(new_texts)} texts via {EMBED_MODEL}...")
    vecs = embed_batch(new_texts)

    col.add_batch(new_doc_ids, new_texts, vecs, new_metas)
    col.save()

    print(f"\n  Done! bee_knowledge: {col.count} vectors (+{len(new)} added)")

    # Verify with sample searches
    print("\n  [Verify] Searching for 'mitä tehdä maaliskuussa keväällä'...")
    try:
        q_resp = requests.post(
            OLLAMA_URL,
            json={"model": EMBED_MODEL,
                  "input": ["query: mitä pitää tehdä maaliskuussa mehiläisille keväällä"]},
            timeout=30,
        )
        q_resp.raise_for_status()
        q_vec = np.array(q_resp.json()["embeddings"][0], dtype=np.float32)
        hits = col.search(q_vec, k=3)
        for h in hits:
            print(f"  {h.score:.3f} [{h.metadata.get('month_name_fi', h.metadata.get('chunk_type', '?'))}] "
                  f"{h.text[:80]}")
    except Exception as exc:
        print(f"  Search verify failed: {exc}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
