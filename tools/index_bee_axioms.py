"""Incremental FAISS indexer for bee-domain axiom YAMLs.

Adds only new doc_ids (checks _doc_ids list, skips already-indexed).
Mirrors the same format as vectorize_all.py so searches work identically.

Requirements: Ollama running with nomic-embed-text loaded.
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
EMBED_DIM = 768
DOC_PREFIX = "search_document: "
BATCH_SIZE = 16

PROJECT_ROOT = Path(__file__).parent.parent
AXIOMS_DIR = PROJECT_ROOT / "configs" / "axioms"


def embed_batch(texts: list) -> np.ndarray:
    all_vecs = []
    for i in range(0, len(texts), BATCH_SIZE):
        chunk = texts[i:i + BATCH_SIZE]
        prefixed = [DOC_PREFIX + t for t in chunk]
        resp = requests.post(
            OLLAMA_URL,
            json={"model": EMBED_MODEL, "input": prefixed},
            timeout=60,
        )
        resp.raise_for_status()
        all_vecs.extend(resp.json()["embeddings"])
        done = min(i + BATCH_SIZE, len(texts))
        print(f"  {done}/{len(texts)} embedded", end="\r", flush=True)
    print()
    return np.array(all_vecs, dtype=np.float32)


def _extract_axiom_chunks(yaml_file: Path) -> list:
    """Return list of (doc_id, text, meta) tuples from an axiom YAML."""
    try:
        with open(yaml_file, encoding="utf-8") as f:
            model = yaml.safe_load(f)
    except Exception:
        return []
    if not model or "model_id" not in model:
        return []

    model_id = model["model_id"]
    domain = yaml_file.parent.name
    model_name = model.get("model_name", model_id)
    description = model.get("description", "")
    chunks = []

    # Main concept
    chunks.append((
        f"axiom:{model_id}:main",
        f"{model_name}: {description}",
        {"type": "axiom_main", "model_id": model_id, "domain": domain,
         "model_name": model_name},
    ))

    # Variables
    for var_name, var_def in model.get("variables", {}).items():
        t = (f"{var_name} ({var_def.get('unit', '')}): "
             f"{var_def.get('description', '')}")
        chunks.append((
            f"axiom:{model_id}:var:{var_name}",
            t.strip(),
            {"type": "axiom_variable", "model_id": model_id,
             "domain": domain, "var_name": var_name},
        ))

    # Formulas
    for formula in model.get("formulas", []):
        fname = formula.get("name", "")
        fdesc = formula.get("description", formula.get("formula", ""))
        funit = formula.get("output_unit", "")
        t = f"{fname}: {fdesc} [{funit}]" if funit else f"{fname}: {fdesc}"
        chunks.append((
            f"axiom:{model_id}:formula:{fname}",
            t.strip(),
            {"type": "axiom_formula", "model_id": model_id,
             "domain": domain, "formula_name": fname},
        ))

    return chunks


def main():
    print("\n=== index_bee_axioms: Incremental FAISS Indexer ===")

    # Check Ollama
    try:
        r = requests.get("http://localhost:11434/api/version", timeout=5)
        print(f"  Ollama: OK (v{r.json().get('version', '?')})")
    except Exception as exc:
        print(f"  [!] Ollama not reachable: {exc}")
        print("  Start Ollama first: ollama serve")
        return 1

    registry = FaissRegistry()
    col = registry.get_or_create("axioms")
    existing = set(col._doc_ids)
    print(f"  Existing axioms vectors: {col.count} ({len(existing)} unique doc_ids)")

    # Collect all chunks from all axiom YAMLs
    all_chunks = []
    yaml_count = 0
    for yaml_file in sorted(AXIOMS_DIR.rglob("*.yaml")):
        chunks = _extract_axiom_chunks(yaml_file)
        if chunks:
            yaml_count += 1
            all_chunks.extend(chunks)

    print(f"  Found {yaml_count} axiom YAML files, {len(all_chunks)} total chunks")

    # Filter to only new doc_ids
    new = [(did, txt, meta) for did, txt, meta in all_chunks if did not in existing]
    print(f"  New chunks to index: {len(new)}")

    if not new:
        print("  Nothing to add — FAISS already up to date.")
        return 0

    new_doc_ids = [c[0] for c in new]
    new_texts = [c[1] for c in new]
    new_metas = [c[2] for c in new]

    # Show which models are new
    new_models = sorted({m.get("model_id", "?") for m in new_metas})
    print(f"  New models: {new_models}")

    print(f"  Embedding {len(new_texts)} texts via {EMBED_MODEL}...")
    vecs = embed_batch(new_texts)

    col.add_batch(new_doc_ids, new_texts, vecs, new_metas)
    col.save()

    print(f"\n  Done! Axioms collection: {col.count} vectors (+{len(new)} added)")

    # Verify with a search
    print("\n  [Verify] Searching for 'honey yield colony strength'...")
    try:
        q_resp = requests.post(
            OLLAMA_URL,
            json={"model": EMBED_MODEL, "input": [
                "query: honey yield colony strength seasonal harvest"]},
            timeout=30,
        )
        q_resp.raise_for_status()
        q_vec = np.array(q_resp.json()["embeddings"][0], dtype=np.float32)
        hits = col.search(q_vec, k=3)
        for h in hits:
            print(f"  {h.score:.3f} [{h.metadata.get('model_id', '?')}] {h.text[:70]}")
    except Exception as exc:
        print(f"  Search verify failed: {exc}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
