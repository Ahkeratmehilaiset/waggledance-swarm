#!/usr/bin/env python3
"""B.0 — Multi-view axiom backfill tool (ledger-only pattern).

Per v3 §1.13 + v3.1 tweak 2: ledger-only architecture.
  - configs/axioms/*.yaml is READ-ONLY source material
  - This tool emits deterministic ledger entries to data/faiss_delta_ledger/
  - Manifest builder (tools/hex_manifest.py) consumes ledger entries
  - Never writes FAISS staging directly

Multi-view embeddings per v3 §1.7:
  - canonical_en   (model_name + description — the formal English description)
  - canonical_fi   (same content translated to Finnish if Finnish terms available)
  - synonym_mixed  (model_id + keywords + units — lexical synonym bag)
  - unit_variable  (variable names + their units)
  - example_queries (auto-generated question-style texts)

Dedup rules per v3.1 tweak 2:
  - same canonical_hash → no-op
  - same canonical_solver_id + new hash → new revision (requires validation)
  - source-side placement check per v3.1 tweak 1: BLOCK if declared cell
    disagrees with BOTH keyword AND centroid

Source-side cell honesty audit:
  - Reads axiom.cell_id (declared — MUST be present)
  - Computes keyword cell (via tools/cell_manifest heuristic)
  - Computes centroid cell (cosine to cell centroids if centroids exist)
  - If declared != keyword AND declared != centroid AND no approved
    placement_review → BLOCK with explanation

Run:
    python tools/backfill_axioms_to_hex.py --dry-run
    python tools/backfill_axioms_to_hex.py
    python tools/backfill_axioms_to_hex.py --filter-domain cottage
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.learning.solver_hash import canonical_hash  # noqa: E402


AXIOMS_DIR = ROOT / "configs" / "axioms"
LEDGER_DIR = ROOT / "data" / "faiss_delta_ledger"
CENTROIDS_FILE = ROOT / "data" / "faiss_staging" / "cell_centroids.json"
OLLAMA_URL = "http://localhost:11434/api/embed"
EMBEDDING_MODEL = "nomic-embed-text"
EMBEDDING_MODEL_VERSION = "v1.5"   # if Ollama exposes more precise tag, update
DEFAULT_BATCH_SIZE = 32

# Same classification as cell_manifest.py (for keyword-cell heuristic)
_CELL_KEYWORDS = {
    "thermal":  ["heating", "cooling", "thermal", "hvac", "heat_pump", "frost",
                 "temperature", "lämpö", "pakkanen", "freezing", "pipe"],
    "energy":   ["energy", "solar", "battery", "power", "kwh", "grid",
                 "watt", "sähkö", "electricity"],
    "safety":   ["safety", "alarm", "risk", "hazard", "violation",
                 "turvallisuus", "varroa", "mite", "swarm"],
    "seasonal": ["season", "month", "winter", "summer", "spring", "autumn",
                 "vuodenaika", "kevät", "kesä", "talvi", "harvest", "sato"],
    "math":     ["formula", "calculate", "yield", "honey", "colony",
                 "optimize"],
    "system":   ["system", "status", "health", "uptime", "process", "mtbf",
                 "oee", "diagnose", "signal", "propagation"],
    "learning": ["learn", "train", "dream", "insight", "adapt"],
    "general":  [],
}

# FI aliases for common axiom terms — used to generate canonical_fi view
_FI_ALIASES = {
    "heating": "lämmitys",
    "heating_cost": "lämmityskustannus",
    "honey": "hunaja",
    "yield": "sato",
    "colony": "pesä",
    "bee": "mehiläinen",
    "pipe": "putki",
    "freezing": "jäätyminen",
    "thermal": "lämpö",
    "temperature": "lämpötila",
    "varroa": "varroa",
    "treatment": "käsittely",
    "swarm": "parvi",
    "risk": "riski",
    "solar": "aurinkopaneeli",
    "power": "teho",
    "energy": "energia",
    "electricity": "sähkö",
    "battery": "akku",
    "estimate": "arvio",
    "cost": "kustannus",
}


# ── Helpers ───────────────────────────────────────────────────────


def nfc_strip(text: str) -> str:
    """v3.1 tweak 3 normalization — match actual embedding input."""
    return unicodedata.normalize("NFC", text).strip()


def canonicalize_for_embedding(text: str) -> str:
    """Wrapper for the above, used consistently across backfill + query time."""
    return nfc_strip(text)


def classify_keyword_cell(axiom: dict) -> str:
    text = " ".join([
        axiom.get("model_id", ""),
        axiom.get("model_name", ""),
        axiom.get("description", ""),
    ]).lower()
    scores = {}
    for cell, kws in _CELL_KEYWORDS.items():
        score = sum(1 for kw in kws if kw in text)
        if score:
            scores[cell] = score
    return max(scores, key=scores.get) if scores else "general"


def compute_centroid_cell_top1(query_vec, centroids: dict) -> Optional[str]:
    """Return top-1 cell by cosine similarity. None if no centroids available."""
    if not centroids:
        return None
    import numpy as np
    # Support both flat {cell: [vec]} and nested {cell: {"centroid": [vec], ...}}
    cells_data = centroids.get("cells", centroids) if isinstance(centroids, dict) else {}
    best_cell, best_score = None, -1.0
    for cell, entry in cells_data.items():
        if isinstance(entry, dict):
            vec = entry.get("centroid")
        else:
            vec = entry
        if not vec or not isinstance(vec, list):
            continue
        try:
            c = np.array(vec, dtype=np.float64)
        except (ValueError, TypeError):
            continue
        denom = (np.linalg.norm(query_vec) * np.linalg.norm(c)) + 1e-12
        sim = float(np.dot(query_vec, c) / denom)
        if sim > best_score:
            best_cell, best_score = cell, sim
    return best_cell


def load_centroids() -> dict:
    if CENTROIDS_FILE.exists():
        return json.loads(CENTROIDS_FILE.read_text(encoding="utf-8"))
    return {}


# ── Multi-view generation ─────────────────────────────────────────


def build_views(axiom: dict) -> list[dict]:
    """Generate 4-5 views per v3 §1.7."""
    mid = axiom["model_id"]
    name = axiom.get("model_name", "")
    desc = axiom.get("description", "")
    formulas = axiom.get("formulas", [])
    variables = axiom.get("variables", {})

    views = []

    # canonical_en
    en_text = nfc_strip(f"{name}. {desc}")
    views.append({"view_type": "canonical_en", "lang": "en", "text": en_text})

    # canonical_fi (auto-translated via _FI_ALIASES)
    fi_text = en_text
    for en_word, fi_word in _FI_ALIASES.items():
        fi_text = fi_text.replace(en_word, f"{en_word} / {fi_word}")
    if fi_text != en_text:
        views.append({"view_type": "canonical_fi", "lang": "fi", "text": nfc_strip(fi_text)})

    # synonym_mixed — model_id tokens + keywords + units
    units = " ".join(
        str(v.get("unit", "")) for v in variables.values() if isinstance(v, dict)
    )
    mixed = f"{mid.replace('_', ' ')} {name} {units}"
    views.append({"view_type": "synonym_mixed", "lang": "mixed", "text": nfc_strip(mixed)})

    # unit_variable — variable names + units
    var_text = " ".join(
        f"{vname} {v.get('unit','')}" for vname, v in variables.items() if isinstance(v, dict)
    )
    if var_text.strip():
        views.append({"view_type": "unit_variable", "lang": "generic", "text": nfc_strip(var_text)})

    # example_queries — auto-generated by combining model_name with Finnish question words
    examples = []
    if name:
        examples.append(f"paljonko {name.lower()}")
        examples.append(f"what is {name.lower()}")
        examples.append(f"laske {name.lower()}")
    examples_text = "; ".join(examples)
    if examples_text:
        views.append({"view_type": "example_queries", "lang": "mixed", "text": nfc_strip(examples_text)})

    return views


# ── Embedding client ──────────────────────────────────────────────


def embed_texts(texts: list[str], batch_size: int = DEFAULT_BATCH_SIZE) -> list[list[float]]:
    """Batch-embed via Ollama /api/embed. Returns vectors in same order as texts."""
    all_vectors = []
    with httpx.Client(timeout=120.0) as client:
        for i in range(0, len(texts), batch_size):
            batch = [canonicalize_for_embedding(t) for t in texts[i:i + batch_size]]
            r = client.post(
                OLLAMA_URL,
                json={"model": EMBEDDING_MODEL, "input": batch, "keep_alive": "30m"},
            )
            r.raise_for_status()
            all_vectors.extend(r.json()["embeddings"])
    return all_vectors


# ── Cell honesty audit ─────────────────────────────────────────────


def audit_placement(axiom: dict, first_view_vec, centroids: dict) -> tuple[bool, dict]:
    """Return (ok_to_proceed, audit_record).

    v3.1 tweak 1: BLOCK if declared != keyword AND declared != centroid,
    unless placement_review.status == "approved".
    """
    declared = axiom.get("cell_id")
    if not declared:
        return False, {"error": "missing cell_id (v3 requirement)"}

    keyword = classify_keyword_cell(axiom)
    centroid = compute_centroid_cell_top1(first_view_vec, centroids) if first_view_vec is not None else None

    review = axiom.get("placement_review", {})
    review_status = review.get("status", "auto_heuristic")

    record = {
        "declared_cell": declared,
        "keyword_cell": keyword,
        "centroid_cell": centroid,
        "placement_review_status": review_status,
    }

    # If no centroids exist yet (first backfill), skip centroid check
    if centroid is None:
        record["note"] = "centroids unavailable (first backfill) — placement trusted"
        return True, record

    # Agreement cases: declared matches either heuristic → OK
    if declared == keyword or declared == centroid:
        return True, record

    # Triple disagreement: require human-approved placement
    if review_status == "approved":
        record["note"] = "triple disagreement but placement_review.status=approved → allowed"
        return True, record

    # Triple disagreement without approval: BLOCK
    record["error"] = (
        f"Triple disagreement: declared={declared} vs keyword={keyword} vs centroid={centroid}. "
        f"Set placement_review.status=approved after human review (v3.1 tweak 1)."
    )
    return False, record


# ── Ledger writer ──────────────────────────────────────────────────


def next_seq_for_cell(cell: str) -> int:
    """Monotonic seq number per cell. Scan existing ledger files."""
    cell_dir = LEDGER_DIR / cell
    if not cell_dir.exists():
        return 1
    max_seq = 0
    for f in cell_dir.glob("*.jsonl"):
        try:
            first_seq = int(f.name.split("_")[0])
            if first_seq > max_seq:
                # Scan the file to find max seq in it
                for line in open(f, encoding="utf-8"):
                    if line.strip():
                        entry = json.loads(line)
                        if entry.get("seq", 0) > max_seq:
                            max_seq = entry["seq"]
        except (ValueError, json.JSONDecodeError):
            continue
    return max_seq + 1


def write_ledger_entries(cell: str, entries: list[dict], source: str, timestamp: str):
    """Append entries to a fresh per-cell ledger file."""
    cell_dir = LEDGER_DIR / cell
    cell_dir.mkdir(parents=True, exist_ok=True)

    # Filename: {first_seq}_{source}_{timestamp}.jsonl
    first_seq = entries[0]["seq"] if entries else next_seq_for_cell(cell)
    ts_slug = timestamp.replace(":", "").replace("-", "").replace(".", "")[:15]
    fname = f"{first_seq:06d}_{source}_{ts_slug}.jsonl"
    fpath = cell_dir / fname

    with open(fpath, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    return fpath


# ── Main backfill ──────────────────────────────────────────────────


def backfill(dry_run: bool = False, filter_domain: Optional[str] = None) -> dict:
    centroids = load_centroids()
    axioms_to_process = []

    for axiom_path in sorted(AXIOMS_DIR.rglob("*.yaml")):
        if filter_domain and axiom_path.parent.name != filter_domain:
            continue
        with open(axiom_path, encoding="utf-8") as f:
            axiom = yaml.safe_load(f)
        if not axiom or not axiom.get("model_id"):
            continue
        if "cell_id" not in axiom:
            print(f"  BLOCK: {axiom_path.relative_to(ROOT)} missing cell_id", file=sys.stderr)
            continue
        if "solver_output_schema" not in axiom:
            print(f"  BLOCK: {axiom_path.relative_to(ROOT)} missing solver_output_schema",
                  file=sys.stderr)
            continue
        axioms_to_process.append((axiom_path, axiom))

    print(f"Backfilling {len(axioms_to_process)} axioms...")
    if dry_run:
        print("  (DRY RUN — no ledger writes)")

    per_cell_entries = {}
    errors = []
    processed = 0

    # Embed all views in one batch pass to amortize Ollama fixed cost
    all_views = []   # [(axiom_idx, view, text), ...]
    for idx, (_, axiom) in enumerate(axioms_to_process):
        views = build_views(axiom)
        for view in views:
            all_views.append((idx, view))

    print(f"Embedding {len(all_views)} views in batches of {DEFAULT_BATCH_SIZE} ...")
    texts = [v["text"] for _, v in all_views]
    t0 = time.perf_counter()
    vectors = embed_texts(texts, batch_size=DEFAULT_BATCH_SIZE)
    elapsed = time.perf_counter() - t0
    print(f"  embedded in {elapsed:.1f}s ({elapsed*1000/len(texts):.1f}ms/view avg)")

    # Group per axiom
    views_by_axiom = {}
    vectors_by_axiom = {}
    for (idx, view), vec in zip(all_views, vectors):
        views_by_axiom.setdefault(idx, []).append(view)
        vectors_by_axiom.setdefault(idx, []).append(vec)

    # Process each axiom
    for idx, (axiom_path, axiom) in enumerate(axioms_to_process):
        first_vec = vectors_by_axiom[idx][0]
        ok, audit = audit_placement(axiom, first_vec, centroids)
        if not ok:
            errors.append({
                "axiom": axiom["model_id"],
                "path": str(axiom_path.relative_to(ROOT)),
                "audit": audit,
            })
            print(f"  BLOCK {axiom['model_id']}: {audit.get('error')}")
            continue

        mid = axiom["model_id"]
        cid = canonical_hash(axiom)
        cell = axiom["cell_id"]
        domain = axiom_path.parent.name

        # One entry per view
        seq = per_cell_entries.get(cell, {}).get("next_seq") or next_seq_for_cell(cell)

        entries = []
        for view, vec in zip(views_by_axiom[idx], vectors_by_axiom[idx]):
            # Per v3 §1.7 dedup — canonical_solver_id is constant across views
            doc_id = f"{mid}#{view['view_type']}"
            entry = {
                "seq": seq,
                "cell_id": cell,
                "source": "axiom_backfill",
                "canonical_solver_id": mid,
                "canonical_hash": cid,
                "doc_id": doc_id,
                "view_type": view["view_type"],
                "view_lang": view["lang"],
                "text": view["text"],
                "text_hash": hashlib.sha256(view["text"].encode("utf-8")).hexdigest(),
                "vector": vec,
                "embedding_model": f"{EMBEDDING_MODEL}:{EMBEDDING_MODEL_VERSION}",
                "embedding_dim": len(vec),
                "source_file": str(axiom_path.relative_to(ROOT)),
                "domain": domain,
                "placement_audit": audit,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            entries.append(entry)
            seq += 1

        per_cell_entries.setdefault(cell, {"entries": [], "next_seq": 1})
        per_cell_entries[cell]["entries"].extend(entries)
        per_cell_entries[cell]["next_seq"] = seq
        processed += 1

    # Write ledger files per cell
    ledger_paths = []
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not dry_run:
        for cell, bundle in per_cell_entries.items():
            if bundle["entries"]:
                path = write_ledger_entries(cell, bundle["entries"], "axiom_backfill", ts)
                ledger_paths.append(path)
                print(f"  wrote {len(bundle['entries'])} entries to {path.relative_to(ROOT)}")
    else:
        for cell, bundle in per_cell_entries.items():
            print(f"  WOULD write {len(bundle['entries'])} entries for cell={cell}")

    return {
        "axioms_processed": processed,
        "axioms_blocked": len(errors),
        "ledger_files_written": len(ledger_paths),
        "errors": errors,
        "per_cell_counts": {c: len(b["entries"]) for c, b in per_cell_entries.items()},
        "timestamp": ts,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--filter-domain", help="only process a specific domain dir")
    args = ap.parse_args()

    result = backfill(dry_run=args.dry_run, filter_domain=args.filter_domain)

    print()
    print("=== Summary ===")
    print(f"  Processed: {result['axioms_processed']}")
    print(f"  Blocked:   {result['axioms_blocked']}")
    print(f"  Ledger files: {result['ledger_files_written']}")
    print(f"  Per-cell counts:")
    for c, n in sorted(result["per_cell_counts"].items()):
        print(f"    {c}: {n}")

    if result["errors"]:
        print()
        print("=== Blocked axioms ===")
        for e in result["errors"]:
            print(f"  {e['axiom']}: {e['audit'].get('error')}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
