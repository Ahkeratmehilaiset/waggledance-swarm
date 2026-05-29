#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Build a code-similarity index over the repo's review/verification tooling.

For each Python module under the configured globs, this extracts a structural
*skeleton* (module docstring + import names + every def/class signature, nested
ones included) and embeds it with the **same Ollama embedder WD already uses**
(`/api/embed`, `nomic-embed-text` by default — see
`waggledance/adapters/memory/chroma_vector_store.py`). The vectors are stored in
a dedicated ChromaDB collection (cosine space). Nothing here touches WD's runtime
vector collections.

WHY THIS EXISTS — it is a *review-coverage aid, not a bug detector*. The MAGMA
verification-tool family has shown the same fail-open class recurring across
consumer layers (a tool trusts an upstream artifact's top-level `ok` flag
without re-deriving the verdict from nested fields). When a reviewer finds such
a bug in one module, the expensive part is *remembering to audit every sibling
that shares the same shape*. `find_similar_tools.py` answers exactly that, using
the index this script builds. The judgement (forge every nested field, fuzz
type-confusion, run inputs empirically) still belongs to the reviewer.

GPU: embedding runs on whatever device the Ollama server is bound to. To use
GPU1 (GPU0 is the display in this environment), the Ollama server must have been
started with CUDA_VISIBLE_DEVICES=1. This script does not start or reconfigure
Ollama; it only calls the local embed endpoint.

Invocation:
    python tools/build_tool_similarity_index.py
    python tools/build_tool_similarity_index.py --glob "tools/*.py" "waggledance/core/magma/*.py"
    python tools/build_tool_similarity_index.py --model all-minilm
    python tools/build_tool_similarity_index.py --force --json
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_GLOBS = ["tools/*.py"]
DEFAULT_PERSIST_SUBDIR = Path("data") / "tool_similarity_index"
COLLECTION_NAME = "tool_code_similarity"
DEFAULT_MODEL = "nomic-embed-text"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_EMBED_TIMEOUT = 60.0
DOC_PREFIX = "search_document: "
MAX_IMPORTS_META = 4000  # Chroma metadata is a flat string; keep it bounded.
LOCAL_OLLAMA_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def file_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def require_repo_relative_file(root: Path, raw_path: str) -> Path:
    """Resolve a reviewer-supplied path while keeping reads under the repo root."""

    repo_root = root.resolve()
    candidate = Path(raw_path)
    resolved = (candidate if candidate.is_absolute() else repo_root / candidate).resolve()
    try:
        resolved.relative_to(repo_root)
    except ValueError as exc:
        raise ValueError("file_outside_repo_root") from exc
    return resolved


def require_local_ollama_url(base_url: str) -> str:
    """Return a normalized local Ollama base URL or raise ValueError."""

    parsed = urlparse(base_url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("ollama_url_port_invalid") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in LOCAL_OLLAMA_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("ollama_url_must_be_local")
    host = parsed.hostname
    if host == "::1":
        host = "[::1]"
    normalized = f"http://{host}"
    if port is not None:
        normalized += f":{port}"
    return normalized


def extract_skeleton(text: str) -> tuple[str, int, list[str]]:
    """Return (skeleton_text, n_defs, sorted_imports).

    Uses ast.parse (which never executes the module). Falls back to a line-based
    skeleton when the source does not parse, so the indexer is robust to syntax
    it does not understand.
    """
    imports: set[str] = set()
    defs: list[str] = []
    doc = ""
    try:
        tree = ast.parse(text)
        doc = ast.get_docstring(tree) or ""
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    imports.add(f"{module}.{alias.name}" if module else alias.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                arg_names = [a.arg for a in node.args.args]
                prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
                defs.append(f"{prefix} {node.name}({', '.join(arg_names)})")
            elif isinstance(node, ast.ClassDef):
                defs.append(f"class {node.name}")
    except SyntaxError:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")):
                imports.add(stripped)
            elif stripped.startswith(("def ", "async def ", "class ")):
                defs.append(stripped.rstrip(":"))

    sorted_imports = sorted(imports)
    parts: list[str] = []
    if doc:
        parts.append("DOC: " + " ".join(doc.split())[:600])
    parts.append("IMPORTS: " + ", ".join(sorted_imports))
    parts.append("DEFS:")
    parts.extend(defs)
    return "\n".join(parts), len(defs), sorted_imports


def embed(text: str, *, model: str, base_url: str, timeout: float, prefix: str) -> list[float]:
    """Call Ollama /api/embed. Raises RuntimeError with a clear hint on failure.

    On a context-length overflow (HTTP 400) the input is truncated and retried,
    so the indexer is robust to the embedder's context window regardless of model.
    """
    try:
        local_base_url = require_local_ollama_url(base_url)
    except ValueError as exc:
        raise RuntimeError(
            "Ollama URL must be local http localhost/127.0.0.1/[::1] "
            "with no userinfo, path, query, or fragment."
        ) from exc

    import requests

    url = f"{local_base_url}/api/embed"
    payload_text = prefix + text
    for _attempt in range(6):
        try:
            resp = requests.post(
                url, json={"model": model, "input": payload_text}, timeout=timeout,
            )
        except requests.exceptions.ConnectionError as exc:
            raise RuntimeError(
                f"Could not reach Ollama at {base_url}. Is the server running? ({exc})"
            ) from exc
        if resp.status_code == 404:
            raise RuntimeError(
                f"Ollama has no model '{model}'. Pull it first: `ollama pull {model}` "
                f"(or pass --model all-minilm to use an already-present embedder)."
            )
        if resp.status_code == 400 and "context length" in resp.text.lower() and len(payload_text) > 256:
            payload_text = prefix + payload_text[len(prefix):int(len(payload_text) * 0.6)]
            continue
        if resp.status_code != 200:
            raise RuntimeError(f"Ollama /api/embed returned HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        embeddings = data.get("embeddings")
        if not embeddings or not isinstance(embeddings, list):
            raise RuntimeError(f"Ollama /api/embed returned no embeddings: {str(data)[:200]}")
        return embeddings[0]
    raise RuntimeError("Ollama /api/embed kept overflowing context after truncation retries.")


def gather_files(root: Path, globs: list[str]) -> list[Path]:
    seen: dict[str, Path] = {}
    for pattern in globs:
        for path in sorted(root.glob(pattern)):
            if path.is_file() and path.suffix == ".py":
                seen[str(path.resolve())] = path
    return list(seen.values())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the tool code-similarity index.")
    parser.add_argument("--root", default=str(ROOT), help="Repo root (default: parent of tools/).")
    parser.add_argument("--glob", nargs="+", default=DEFAULT_GLOBS, help="Glob(s) relative to --root.")
    parser.add_argument("--persist-dir", default=None, help="Chroma persist dir (default: <root>/data/tool_similarity_index).")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Ollama embedding model (default: {DEFAULT_MODEL}).")
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--timeout", type=float, default=DEFAULT_EMBED_TIMEOUT)
    parser.add_argument("--max-chars", type=int, default=8000,
                        help="Pre-cap the skeleton length before embedding (avoids context-overflow retries).")
    parser.add_argument("--force", action="store_true", help="Re-embed even if the file's sha is unchanged.")
    parser.add_argument("--json", action="store_true", help="Emit a machine-readable summary.")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    persist_dir = Path(args.persist_dir).resolve() if args.persist_dir else (root / DEFAULT_PERSIST_SUBDIR)
    persist_dir.mkdir(parents=True, exist_ok=True)

    files = gather_files(root, args.glob)
    if not files:
        print(json.dumps({"error": "no_files_matched", "globs": args.glob, "root": str(root)}))
        return 1

    import chromadb

    client = chromadb.PersistentClient(path=str(persist_dir))
    # embedding_function=None: we always supply our own Ollama vectors, so Chroma
    # must not instantiate (or download) its default ONNX embedder.
    coll = client.get_or_create_collection(
        name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"}, embedding_function=None
    )

    existing = coll.get(include=["metadatas"])
    existing_sha = {
        _id: (meta or {}).get("sha256")
        for _id, meta in zip(existing.get("ids", []), existing.get("metadatas", []))
    }

    indexed, skipped, failed = [], [], []
    for path in files:
        rel = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            failed.append({"file": rel, "error": f"read_error: {exc}"})
            continue
        if not text.strip():
            skipped.append({"file": rel, "reason": "empty"})
            continue

        sha = file_sha(text)
        if not args.force and existing_sha.get(rel) == sha:
            skipped.append({"file": rel, "reason": "unchanged"})
            continue

        skeleton, n_defs, imports = extract_skeleton(text)
        skeleton = skeleton[:args.max_chars]
        try:
            vector = embed(
                skeleton, model=args.model, base_url=args.ollama_url,
                timeout=args.timeout, prefix=DOC_PREFIX,
            )
        except RuntimeError as exc:
            # A model/connection error is fatal for the whole run — fail loudly.
            print(json.dumps({"error": "embed_failed", "file": rel, "detail": str(exc)}))
            return 2

        imports_meta = ", ".join(imports)
        if len(imports_meta) > MAX_IMPORTS_META:
            imports_meta = imports_meta[:MAX_IMPORTS_META]
        coll.upsert(
            ids=[rel],
            embeddings=[vector],
            documents=[skeleton],
            metadatas=[{
                "path": rel,
                "sha256": sha,
                "n_defs": n_defs,
                "imports": imports_meta,
                "bytes": len(text.encode("utf-8", errors="replace")),
                "indexed_at": _utc_now_iso(),
            }],
        )
        indexed.append({"file": rel, "n_defs": n_defs})

    summary = {
        "collection": COLLECTION_NAME,
        "persist_dir": str(persist_dir),
        "model": args.model,
        "total_matched": len(files),
        "indexed": len(indexed),
        "skipped": len(skipped),
        "failed": len(failed),
        "collection_count": coll.count(),
    }
    if args.json:
        print(json.dumps({**summary, "indexed_files": indexed, "failed_files": failed}, indent=2))
    else:
        print(f"Index '{COLLECTION_NAME}' @ {persist_dir}")
        print(f"  model={args.model}  matched={len(files)}  "
              f"indexed={len(indexed)}  skipped={len(skipped)}  failed={len(failed)}")
        print(f"  collection now holds {coll.count()} documents")
        if failed:
            for f in failed:
                print(f"  FAILED {f['file']}: {f['error']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
