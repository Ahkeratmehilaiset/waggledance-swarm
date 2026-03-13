"""
Vectorize everything into FAISS + ChromaDB.

Collections created:
  FAISS  data/faiss/axioms/          — axiom models, formulas, variables
  FAISS  data/faiss/agent_knowledge/ — agent descriptions + keyword profiles
  FAISS  data/faiss/training_pairs/  — high-quality training pairs (score >= 8.0)

  ChromaDB  axioms_knowledge         — axiom texts (mirrored from FAISS)
  ChromaDB  agent_knowledge          — agent texts (mirrored from FAISS)

Embedding: nomic-embed-text (768d) via Ollama — requires Ollama running.
"""

import sys
import os
import json
import hashlib
import time
import yaml
import requests
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.faiss_store import FaissRegistry

# ── Config ─────────────────────────────────────────────────────────────
OLLAMA_URL = "http://localhost:11434/api/embed"
EMBED_MODEL = "nomic-embed-text"
EMBED_DIM = 768
DOC_PREFIX = "search_document: "
BATCH_SIZE = 32

PROJECT_ROOT = Path(__file__).parent.parent
AXIOMS_DIR = PROJECT_ROOT / "configs" / "axioms"
AGENTS_DIR = PROJECT_ROOT / "agents"
TRAINING_JSONL = PROJECT_ROOT / "data" / "finetune_curated.jsonl"
CHROMA_DIR = str(PROJECT_ROOT / "data" / "chroma_db")

MIN_TRAINING_SCORE = 8.0
MAX_TRAINING_TEXTS = 15000   # safety cap


# ── Embedding ───────────────────────────────────────────────────────────

def embed_batch_raw(texts: list, batch_size: int = BATCH_SIZE) -> np.ndarray:
    """Embed texts in batches via Ollama. Returns (N, 768) float32 array."""
    all_vecs: list = []
    total = len(texts)
    for i in range(0, total, batch_size):
        chunk = texts[i:i + batch_size]
        prefixed = [DOC_PREFIX + t for t in chunk]
        try:
            resp = requests.post(
                OLLAMA_URL,
                json={"model": EMBED_MODEL, "input": prefixed},
                timeout=120,
            )
            resp.raise_for_status()
            embeddings = resp.json().get("embeddings", [])
            all_vecs.extend(embeddings)
        except Exception as exc:
            print(f"\n  [!] Embed error at batch {i}: {exc}")
            # Insert zero vectors as fallback
            all_vecs.extend([[0.0] * EMBED_DIM] * len(chunk))
        done = min(i + batch_size, total)
        print(f"  {done}/{total} embedded", end="\r", flush=True)
    print()
    return np.array(all_vecs, dtype=np.float32)


# ── ChromaDB helper ─────────────────────────────────────────────────────

def get_chroma_collection(name: str, chroma_dir: str = CHROMA_DIR):
    """Get or create a ChromaDB collection with cosine metric."""
    try:
        import chromadb
        client = chromadb.PersistentClient(path=chroma_dir)
        return client.get_or_create_collection(
            name, metadata={"hnsw:space": "cosine"}
        )
    except Exception as exc:
        print(f"  [!] ChromaDB unavailable: {exc}")
        return None


# ── 1. Axiom YAMLs ─────────────────────────────────────────────────────

def vectorize_axioms(registry: FaissRegistry) -> int:
    """Vectorize all axiom YAML files → FAISS 'axioms' + ChromaDB 'axioms_knowledge'."""
    col = registry.get_or_create("axioms")
    chroma = get_chroma_collection("axioms_knowledge")

    texts: list = []
    doc_ids: list = []
    metas: list = []

    for yaml_file in sorted(AXIOMS_DIR.rglob("*.yaml")):
        try:
            with open(yaml_file, encoding="utf-8") as f:
                model = yaml.safe_load(f)
        except Exception:
            continue
        if not model or "model_id" not in model:
            continue

        model_id = model["model_id"]
        domain = yaml_file.parent.name
        model_name = model.get("model_name", model_id)
        description = model.get("description", "")

        # 1a. Main concept
        t = f"{model_name}: {description}"
        texts.append(t)
        doc_ids.append(f"axiom:{model_id}:main")
        metas.append({"type": "axiom_main", "model_id": model_id,
                       "domain": domain, "model_name": model_name})

        # 1b. Variables
        for var_name, var_def in model.get("variables", {}).items():
            t = (
                f"{var_name} ({var_def.get('unit', '')}): "
                f"{var_def.get('description', '')}"
            )
            texts.append(t.strip())
            doc_ids.append(f"axiom:{model_id}:var:{var_name}")
            metas.append({"type": "axiom_variable", "model_id": model_id,
                           "domain": domain, "var_name": var_name})

        # 1c. Formulas
        for formula in model.get("formulas", []):
            fname = formula.get("name", "")
            fdesc = formula.get("description", formula.get("formula", ""))
            funit = formula.get("output_unit", "")
            t = f"{fname}: {fdesc} [{funit}]" if funit else f"{fname}: {fdesc}"
            texts.append(t.strip())
            doc_ids.append(f"axiom:{model_id}:formula:{fname}")
            metas.append({"type": "axiom_formula", "model_id": model_id,
                           "domain": domain, "formula_name": fname})

    if not texts:
        print("  No axiom texts found.")
        return 0

    print(f"  Embedding {len(texts)} axiom texts...")
    vecs = embed_batch_raw(texts, batch_size=BATCH_SIZE)
    col.add_batch(doc_ids, texts, vecs, metas)
    col.save()

    if chroma is not None:
        try:
            chroma.upsert(
                ids=doc_ids,
                embeddings=vecs.tolist(),
                documents=texts,
                metadatas=metas,
            )
            print(f"  ChromaDB 'axioms_knowledge': {chroma.count()} docs")
        except Exception as exc:
            print(f"  [!] ChromaDB upsert failed: {exc}")

    return len(texts)


# ── 2. Agent YAMLs ─────────────────────────────────────────────────────

def _extract_agent_texts(agent_dir: Path) -> list:
    """Extract text chunks from an agent's core.yaml."""
    core_yaml = agent_dir / "core.yaml"
    if not core_yaml.exists():
        return []
    try:
        with open(core_yaml, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception:
        return []
    if not data:
        return []

    agent_id = agent_dir.name
    header = data.get("header", {})
    name_en = header.get("agent_name", header.get("name", agent_id)).strip()
    name_fi = header.get("name_fi", "").strip()
    profiles = data.get("profiles", [])
    profiles_str = ", ".join(str(p) for p in profiles) if profiles else ""

    chunks = []

    # Main description
    main = f"{name_en}"
    if name_fi and name_fi != name_en:
        main += f" ({name_fi})"
    if profiles_str:
        main += f" — profiles: {profiles_str}"
    assumptions = data.get("ASSUMPTIONS", [])
    if assumptions and isinstance(assumptions, list):
        main += ". " + "; ".join(str(a) for a in assumptions[:3])
    chunks.append((f"agent:{agent_id}:main", main[:500],
                   {"type": "agent_main", "agent_id": agent_id,
                    "name_en": name_en, "name_fi": name_fi}))

    # Decision metrics as keyword text
    metrics = data.get("DECISION_METRICS_AND_THRESHOLDS", {})
    if metrics and isinstance(metrics, dict):
        metric_keys = list(metrics.keys())[:20]
        kw_text = f"{name_en} monitors: {', '.join(metric_keys)}"
        chunks.append((f"agent:{agent_id}:metrics", kw_text[:400],
                       {"type": "agent_metrics", "agent_id": agent_id}))

    # Keyword triggers if present
    kw = data.get("KEYWORD_TRIGGERS", data.get("keyword_triggers", []))
    if kw and isinstance(kw, list):
        kw_text = f"{name_en} keywords: {', '.join(str(k) for k in kw[:25])}"
        chunks.append((f"agent:{agent_id}:keywords", kw_text[:400],
                       {"type": "agent_keywords", "agent_id": agent_id}))

    return chunks


def vectorize_agents(registry: FaissRegistry) -> int:
    """Vectorize all agent core.yaml files → FAISS 'agent_knowledge' + ChromaDB."""
    col = registry.get_or_create("agent_knowledge")
    chroma = get_chroma_collection("agent_knowledge")

    all_chunks: list = []
    for agent_dir in sorted(AGENTS_DIR.iterdir()):
        if not agent_dir.is_dir() or agent_dir.name.startswith("_"):
            continue
        chunks = _extract_agent_texts(agent_dir)
        all_chunks.extend(chunks)

    if not all_chunks:
        print("  No agent texts found.")
        return 0

    doc_ids = [c[0] for c in all_chunks]
    texts = [c[1] for c in all_chunks]
    metas = [c[2] for c in all_chunks]

    print(f"  Embedding {len(texts)} agent texts ({len([c for c in all_chunks if ':main' in c[0]])} agents)...")
    vecs = embed_batch_raw(texts, batch_size=BATCH_SIZE)
    col.add_batch(doc_ids, texts, vecs, metas)
    col.save()

    if chroma is not None:
        try:
            chroma.upsert(
                ids=doc_ids,
                embeddings=vecs.tolist(),
                documents=texts,
                metadatas=metas,
            )
            print(f"  ChromaDB 'agent_knowledge': {chroma.count()} docs")
        except Exception as exc:
            print(f"  [!] ChromaDB upsert failed: {exc}")

    return len(texts)


# ── 3. Training pairs ───────────────────────────────────────────────────

def vectorize_training(registry: FaissRegistry) -> int:
    """Vectorize high-quality training pairs (score >= 8.0) → FAISS 'training_pairs'."""
    if not TRAINING_JSONL.exists():
        print(f"  Training file not found: {TRAINING_JSONL}")
        return 0

    col = registry.get_or_create("training_pairs")

    texts: list = []
    doc_ids: list = []
    metas: list = []
    seen_hashes: set = set()

    with open(TRAINING_JSONL, encoding="utf-8", errors="replace") as f:
        for line in f:
            if len(texts) >= MAX_TRAINING_TEXTS:
                break
            try:
                entry = json.loads(line)
            except Exception:
                continue

            score = float(entry.get("quality_score", 0))
            if score < MIN_TRAINING_SCORE:
                continue

            msgs = entry.get("messages", [])
            asst = next(
                (m["content"] for m in msgs if m.get("role") == "assistant"), ""
            )
            if len(asst) < 80:
                continue
            if "OLETUKSET JA KONTEKSTI" in asst or "ASSUMPTIONS AND CONTEXT" in asst:
                continue

            # Use first 600 chars for embedding
            text = asst[:600].strip()
            h = hashlib.md5(text.lower().encode()).hexdigest()[:16]
            if h in seen_hashes:
                continue
            seen_hashes.add(h)

            doc_id = f"train:{h}"
            texts.append(text)
            doc_ids.append(doc_id)
            metas.append({
                "type": "training_pair",
                "score": score,
                "agent": entry.get("agent_type", ""),
            })

    if not texts:
        print("  No training pairs found.")
        return 0

    print(f"  Embedding {len(texts)} training pairs (score>={MIN_TRAINING_SCORE}, deduped)...")
    # Use smaller batch for longer texts
    vecs = embed_batch_raw(texts, batch_size=16)
    col.add_batch(doc_ids, texts, vecs, metas)
    col.save()

    return len(texts)


# ── Main ────────────────────────────────────────────────────────────────

def main():
    print("\n=== WaggleDance Vectorize All ===")
    print(f"  Embedding model: {EMBED_MODEL} ({EMBED_DIM}d)")
    print(f"  FAISS dir: data/faiss/")
    print(f"  ChromaDB: data/chroma_db/")

    # Verify Ollama is reachable
    try:
        r = requests.get("http://localhost:11434/api/version", timeout=5)
        print(f"  Ollama: OK (v{r.json().get('version', '?')})")
    except Exception as exc:
        print(f"  [!] Ollama not reachable: {exc}")
        print("  Start Ollama first: ollama serve")
        sys.exit(1)

    registry = FaissRegistry()
    t0 = time.monotonic()
    results = {}

    # 1. Axiom YAMLs
    print("\n[1/3] Axiom YAMLs...")
    n = vectorize_axioms(registry)
    results["axioms"] = n
    print(f"  -> {n} vectors stored")

    # 2. Agent YAMLs
    print("\n[2/3] Agent YAMLs...")
    n = vectorize_agents(registry)
    results["agent_knowledge"] = n
    print(f"  -> {n} vectors stored")

    # 3. Training pairs
    print("\n[3/3] Training pairs...")
    n = vectorize_training(registry)
    results["training_pairs"] = n
    print(f"  -> {n} vectors stored")

    elapsed = time.monotonic() - t0

    print(f"\n=== Done in {elapsed:.1f}s ===")
    stats = registry.stats()
    total = sum(stats.values())
    for name, count in stats.items():
        print(f"  FAISS '{name}': {count:,} vectors")
    print(f"  Total: {total:,} vectors")

    # Verify by a test search
    print("\n[Test] Searching axioms for 'battery life'...")
    try:
        qvec = embed_batch_raw(["battery life estimation IoT"], batch_size=1)[0]
        col = registry.get_or_create("axioms")
        hits = col.search(qvec, k=3)
        for h in hits:
            print(f"  {h.score:.3f} [{h.metadata.get('model_id','?')}] {h.text[:60]}")
    except Exception as exc:
        print(f"  Search test failed: {exc}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
