"""Universal ingestor — Phase 9 §H.

Pure functions that turn input sources into VectorNode objects. The
ingestor:
- reads files / folders / URLs / FAISS DBs / mentor packs
- chunks documents deterministically
- creates content-hashed vector nodes
- runs through the 4-level dedup pipeline
- emits an IngestionManifest record

Ingestion is OFFLINE. URL ingestion takes pre-downloaded content;
this module never makes network calls.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import INGESTION_MODES, INGESTION_SCHEMA_VERSION, SOURCE_KINDS
from ..vector_identity import vector_provenance_graph as vpg
from ..vector_identity.ingestion_dedup import dedup_pipeline


# ── Chunking (deterministic) ─────────────────────────────────────-

def chunk_text(text: str, *, chunk_size: int = 4096) -> list[str]:
    """Split text into chunk_size-byte chunks. Deterministic; no
    overlap; UTF-8 boundary aware (decoded chunks)."""
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")
    out: list[str] = []
    raw = text.encode("utf-8")
    pos = 0
    while pos < len(raw):
        chunk = raw[pos:pos + chunk_size]
        # Step back to a valid UTF-8 boundary if we cut a multi-byte char
        while len(chunk) > 0:
            try:
                out.append(chunk.decode("utf-8"))
                pos += len(chunk)
                break
            except UnicodeDecodeError:
                chunk = chunk[:-1]
                if not chunk:
                    pos += 1
                    break
    return out


# ── Source ingestion ─────────────────────────────────────────────-

def ingest_local_file(path: Path | str,
                            *,
                            capsule_context: str = "neutral_v1",
                            ingested_via: str = "copy_mode",
                            ingested_at_tick: int = 0,
                            chunk_size: int = 4096,
                            tags: Iterable[str] = (),
                            fixture_fallback_used: bool = False,
                            ) -> list[vpg.VectorNode]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    text = p.read_text(encoding="utf-8")
    chunks = chunk_text(text, chunk_size=chunk_size)
    out: list[vpg.VectorNode] = []
    for chunk in chunks:
        node = vpg.make_node(
            content_bytes=chunk.encode("utf-8"),
            kind="document_chunk",
            source=str(p),
            source_kind="local_file",
            ingested_via=ingested_via,
            capsule_context=capsule_context,
            external_path=str(p) if ingested_via == "link_mode" else None,
            ingested_at_tick=ingested_at_tick,
            tags=tuple(tags),
            fixture_fallback_used=fixture_fallback_used,
        )
        out.append(node)
    return out


def ingest_mentor_context_pack(pack: dict,
                                       *,
                                       capsule_context: str = "neutral_v1",
                                       ingested_via: str = "copy_mode",
                                       ingested_at_tick: int = 0,
                                       fixture_fallback_used: bool = False,
                                       ) -> list[vpg.VectorNode]:
    """Convert a mentor_context_pack into vector nodes (one per item)."""
    out: list[vpg.VectorNode] = []
    pack_id = str(pack.get("pack_id", "unknown"))
    produced_by = str(pack.get("produced_by", "unknown_mentor"))
    items = pack.get("items") or []
    for item in items:
        content = str(item.get("content", ""))
        if not content:
            continue
        item_kind = str(item.get("kind", "design_note"))
        node = vpg.make_node(
            content_bytes=content.encode("utf-8"),
            kind="mentor_context",
            source=f"{produced_by}:{pack_id}:{item.get('item_id', '')}",
            source_kind="mentor_context_pack",
            ingested_via=ingested_via,
            capsule_context=capsule_context,
            anchor_status="candidate",   # always enters candidate first
            ingested_at_tick=ingested_at_tick,
            tags=tuple(list(item.get("tags") or []) + [item_kind]),
            fixture_fallback_used=fixture_fallback_used,
        )
        out.append(node)
    return out


def ingest_into_graph(graph: vpg.VectorProvenanceGraph,
                            candidates: Iterable[vpg.VectorNode],
                            ) -> dict:
    """Add candidates to the graph through the dedup pipeline.
    Returns {nodes_added: int, nodes_deduped: {level: count}}."""
    added = 0
    deduped: dict[str, int] = {}
    existing_nodes = list(graph.nodes.values())
    for cand in candidates:
        result = dedup_pipeline(cand, existing_nodes)
        if result.level == "no_match":
            graph.add_node(cand)
            existing_nodes.append(cand)
            added += 1
        else:
            deduped[result.level] = deduped.get(result.level, 0) + 1
    return {"nodes_added": added, "nodes_deduped": deduped}


# ── Manifest ─────────────────────────────────────────────────────-

@dataclass(frozen=True)
class IngestionManifest:
    schema_version: int
    manifest_id: str
    ingested_at_iso: str
    ingested_at_tick: int
    mode: str
    sources: tuple[dict, ...]
    nodes_added: int
    nodes_deduped: dict[str, int]
    anchors_promoted: dict[str, int]
    branch_name: str
    base_commit_hash: str
    pinned_input_manifest_sha256: str

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "manifest_id": self.manifest_id,
            "ingested_at_iso": self.ingested_at_iso,
            "ingested_at_tick": self.ingested_at_tick,
            "mode": self.mode,
            "sources": list(self.sources),
            "nodes_added": self.nodes_added,
            "nodes_deduped": dict(sorted(self.nodes_deduped.items())),
            "anchors_promoted": dict(sorted(self.anchors_promoted.items())),
            "provenance": {
                "branch_name": self.branch_name,
                "base_commit_hash": self.base_commit_hash,
                "pinned_input_manifest_sha256":
                    self.pinned_input_manifest_sha256,
            },
        }


def compute_manifest_id(*, ingested_at_iso: str, mode: str,
                              sources: list[dict]) -> str:
    canonical = json.dumps({
        "ingested_at_iso": ingested_at_iso, "mode": mode,
        "sources": sorted(json.dumps(s, sort_keys=True) for s in sources),
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def make_manifest(*,
                       ingested_at_iso: str,
                       ingested_at_tick: int,
                       mode: str,
                       sources: list[dict],
                       nodes_added: int,
                       nodes_deduped: dict[str, int],
                       anchors_promoted: dict[str, int] | None = None,
                       branch_name: str = "phase9/autonomy-fabric",
                       base_commit_hash: str = "",
                       pinned_input_manifest_sha256: str = "sha256:unknown",
                       ) -> IngestionManifest:
    if mode not in INGESTION_MODES:
        raise ValueError(f"unknown ingestion mode: {mode!r}")
    mid = compute_manifest_id(ingested_at_iso=ingested_at_iso,
                                  mode=mode, sources=sources)
    return IngestionManifest(
        schema_version=INGESTION_SCHEMA_VERSION,
        manifest_id=mid,
        ingested_at_iso=ingested_at_iso,
        ingested_at_tick=ingested_at_tick,
        mode=mode,
        sources=tuple(sources),
        nodes_added=nodes_added,
        nodes_deduped=dict(nodes_deduped),
        anchors_promoted=dict(anchors_promoted or {}),
        branch_name=branch_name,
        base_commit_hash=base_commit_hash,
        pinned_input_manifest_sha256=pinned_input_manifest_sha256,
    )
