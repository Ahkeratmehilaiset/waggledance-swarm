# Hybrid FAISS + Hex-Cell Retrieval

> Added in v3.4.0. Feature-flagged — default OFF.
>
> **Dependency note (post-2026-04-20):** `faiss-cpu` is an **optional**
> dependency. Full-install via `requirements.lock.txt` (and the Docker
> image) installs it. CI (`requirements-ci.txt`) does not. Since
> 2026-04-20 (`b21548d`), `waggledance/bootstrap/container.py` returns
> `None` from `faiss_registry` on `ImportError`, and
> `HybridRetrievalService` short-circuits when `enabled=False`. Prior
> to that fix, any `/api/chat` request without `faiss-cpu` installed
> returned HTTP 500 — this was the single cause of the 19-day red CI
> window. Enable the feature via `hybrid_retrieval.enabled=true` in
> `configs/settings.yaml` only on hosts where `faiss-cpu` is installed.

## Overview

WaggleDance v3.4 introduces a **hybrid retrieval architecture** that adds cell-local FAISS indices as a fast retrieval layer before the existing global ChromaDB path. This is a **logical hex-cell overlay** — not a visual rewrite.

## Architecture

```
Query → Intent Classification → Cell Assignment
                                    │
              ┌─────────────────────┼──────────────────────┐
              ▼                     ▼                      ▼
        Local FAISS Cell    Ring-1 Neighbor Cells    Ring-2 (optional)
              │                     │                      │
              └─────────────────────┼──────────────────────┘
                                    ▼
                         Global ChromaDB (existing)
                                    │
                                    ▼
                           LLM Fallback (existing)
```

## Hex-Cell Topology

Eight logical cells based on knowledge domains:

| Cell | Domain | Ring-1 Neighbors |
|------|--------|-----------------|
| `general` | General knowledge, chat | safety, seasonal, math, learning |
| `thermal` | Temperature, heating, cooling | energy, seasonal, safety |
| `energy` | Energy, optimization, grid | thermal, safety, math |
| `safety` | Safety rules, constraints | thermal, energy, system, general |
| `seasonal` | Seasonal knowledge | thermal, general, learning |
| `math` | Mathematics, calculations | energy, general, system |
| `system` | System stats, health | safety, math, learning |
| `learning` | Learning, training data | seasonal, general, system |

## Cell Assignment

Deterministic, auditable — no ML clustering:

1. **Intent mapping**: SolverRouter intent → cell (e.g., `math` → `math` cell)
2. **Keyword scan**: Domain keywords checked for `chat` intent
3. **Default**: `general` cell for unclassified queries

## Retrieval Order (when hybrid enabled)

1. Existing deterministic solver/rule path (unchanged)
2. Local FAISS cell retrieval (~5-10ms)
3. Ring-1 neighbor FAISS retrieval (~10-20ms)
4. (Optional) Ring-2 neighbor retrieval
5. Global ChromaDB retrieval (~50ms, existing path)
6. LLM fallback (existing path)

## Feature Flag

In `configs/settings.yaml`:

```yaml
hybrid_retrieval:
  enabled: false        # Set true to activate
  ring2_enabled: false  # Search neighbor-of-neighbor cells
  min_score: 0.35       # Minimum similarity threshold
  sufficient_score: 0.70  # Score to stop searching further
```

Or via environment variable:
```
WAGGLE_HYBRID_RETRIEVAL=true
```

## Telemetry Trace Fields

Every request exposes (when hybrid enabled):

| Field | Type | Description |
|-------|------|-------------|
| `retrieval_mode` | str | "hybrid", "global_only", or "disabled" |
| `answered_by_layer` | str | solver, local_faiss, neighbor_faiss, global_chroma, llm |
| `cell_id` | str | Assigned hex cell |
| `neighbor_hops_used` | int | 0=local only, 1=ring-1, 2=ring-2 |
| `local_hit` | bool | Found results in local cell |
| `neighbor_hit` | bool | Found results in neighbor cells |
| `global_hit` | bool | Found results in global ChromaDB |
| `llm_fallback` | bool | Fell through to LLM |
| `local_faiss_ms` | float | Local FAISS search time |
| `neighbor_faiss_ms` | float | Neighbor FAISS search time |
| `global_chroma_ms` | float | Global ChromaDB search time |

## API Endpoints

- `GET /api/status` — includes `hybrid_retrieval.enabled` and hit counters
- `GET /api/ops` — includes full `hybrid_retrieval` stats section

## What This Does NOT Change

- SQLite stores remain authoritative for their domains
- ChromaDB/MAGMA remain global memory/audit truth
- HotCache behavior is preserved
- Solver-first routing is preserved
- Learning funnel / case capture continues to work
- No visual Hologram changes

## Ingest Path

When hybrid is enabled, new cases are mirrored to the correct cell-local FAISS index:
- Global ChromaDB ingest continues unchanged
- Cell-local FAISS ingest is additive (failure does not block global path)
- Cell assignment uses the same deterministic rules as retrieval

## Files

| File | Role |
|------|------|
| `waggledance/core/hex_cell_topology.py` | Cell assignment + neighbor mapping |
| `waggledance/application/services/hybrid_retrieval_service.py` | Retrieval orchestration + telemetry |
| `waggledance/bootstrap/container.py` | DI wiring |
| `waggledance/application/services/chat_service.py` | Pipeline integration |
| `configs/settings.yaml` | Feature flag |
| `core/faiss_store.py` | Underlying FAISS index (existing) |
